#!/usr/bin/env bash
# Isolation smoke for the lab network on the server (issue #3, spec 6, D6, D25).
#
# Starts a few throw-away containers named lf-smoke-* and checks, from inside the lab network:
#   1. the manager address (172.31.240.2) reaches a lab's tcp/7681;
#   2. one lab cannot open tcp/7681 on another lab;
#   3. a lab cannot open a TCP connection to the host (lf-labs gateway, docker0, the public IP);
#   4. a lab cannot reach a container on another bridge (docker0);
#   5. a lab cannot reach the internet;
#   6. lab containers run under runsc.
# Every "cannot" check has a control: the same target is first shown reachable from the host, so
# a dead target can never make a check pass.
#
# It changes nothing outside its own lf-smoke-* containers, which it always removes (trap). It
# never restarts Docker and never touches another container. The foreign containers' names,
# states and start times are compared before and after.
#
# Env: LF_SMOKE_IMAGE (default busybox:1.36.1-musl; needs nc -z, wget, httpd), LF_SMOKE_HOST_PORTS (default "22 80 443 18789").
# Output ends with "SMOKE RESULT: ok" (exit 0) or "SMOKE RESULT: failed" (exit 1).

set -uo pipefail

IMAGE="${LF_SMOKE_IMAGE:-busybox:1.36.1-musl}"
HOST_PORTS="${LF_SMOKE_HOST_PORTS:-22 80 443 18789}"
NET=lf-labs
GATEWAY=172.31.240.1
MANAGER_IP=172.31.240.2
PORT=7681
P=lf-smoke
# --rm plus a bounded life: even a smoke killed without its trap leaves nothing behind for long.
RUN_OPTS=(--rm --runtime runsc --memory 64m --pids-limit 64)
LIFE_S=600

FAILED=0
pass() { printf 'PASS  %s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*"; FAILED=1; }
skip() { printf 'SKIP  %s\n' "$*"; }
log() { printf '%s\n' "$*"; }

container_state() {
  local id
  { for id in $(docker ps -aq 2>/dev/null); do
      docker inspect -f '{{.Name}} {{.State.Running}} {{.State.StartedAt}}' "$id" 2>/dev/null || true
    done; } | { grep -v -E '^/(lf|lab)-' || true; } | sort
}

cleanup() {
  local n
  for n in mgr a b other; do docker rm -f "$P-$n" >/dev/null 2>&1 || true; done
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM PIPE

# TCP connect from inside a container (busybox nc -z: connect only, no I/O): 0 = connected.
# Returns 1 only for a clean "no connection"; anything else (a timeout of the probe itself, a
# missing tool) returns 2, and the caller treats that as a failed check, never as isolation.
c_tcp() {
  local rc=0
  # nc's own exit code is printed inside the container, so a failing docker exec (container
  # gone, daemon error) can never be read as "no connection".
  local out
  out="$(docker exec "$1" sh -c 'timeout 8 nc -z -w 3 "$0" "$1" </dev/null >/dev/null 2>&1; echo "nc=$?"' "$2" "$3" 2>/dev/null)" || true
  case "$out" in nc=*) rc=${out#nc=} ;; *) return 2 ;; esac
  case "$rc" in 0) return 0 ;; 1) return 1 ;; *) return 2 ;; esac
}
# Expect no connection: PASS only on a clean refusal/timeout of nc itself.
expect_blocked() { # container ip port label
  local rc=0
  c_tcp "$1" "$2" "$3" || rc=$?
  case "$rc" in
    0) fail "$4: connected" ;;
    1) pass "$4: blocked" ;;
    *) fail "$4: probe error (exit $rc), cannot tell" ;;
  esac
}
# HTTP GET from inside a container: 0 = got an answer.
c_http() { docker exec "$1" timeout 6 wget -q -T 4 -O /dev/null "$2" >/dev/null 2>&1; }
# TCP connect from the host: 0 = connected.
h_tcp() { timeout 5 bash -c 'exec 3<>"/dev/tcp/$0/$1"' "$1" "$2" 2>/dev/null; }

ip_of() { docker inspect -f "{{(index .NetworkSettings.Networks \"$2\").IPAddress}}" "$1"; }

serve() { # name, extra docker run args...: a busybox httpd on $PORT
  local name=$1; shift
  docker run -d --name "$name" "${RUN_OPTS[@]}" "$@" "$IMAGE" \
    sh -c "mkdir -p /www && echo ok > /www/index.html && httpd -p $PORT -h /www && sleep $LIFE_S" >/dev/null
}

log "== isolation smoke ($(date -u +%FT%TZ))"
if ! docker network inspect "$NET" >/dev/null 2>&1; then
  log "network $NET does not exist: run the bootstrap (apply) first"
  log "SMOKE RESULT: failed"; exit 1
fi
if ! docker info --format '{{json .Runtimes}}' | grep -q '"runsc"'; then
  log "runtime runsc is not registered with Docker"
  log "SMOKE RESULT: failed"; exit 1
fi
cleanup
BASE="$(container_state)"
docker pull -q "$IMAGE" >/dev/null || { log "cannot pull $IMAGE"; log "SMOKE RESULT: failed"; exit 1; }

# The manager's address is used only while no manager container exists in any state (a stopped
# lf-manager must be able to start again at any time).
MGR_FREE=1
if [ -n "$(docker ps -aq --filter 'name=^lf-manager$')" ] \
  || docker network inspect -f '{{range .Containers}}{{.IPv4Address}} {{end}}' "$NET" | grep -q "$MANAGER_IP/"; then
  MGR_FREE=0
fi

serve "$P-a" --network "$NET" || { log "cannot start $P-a"; log "SMOKE RESULT: failed"; exit 1; }
serve "$P-b" --network "$NET" || { log "cannot start $P-b"; log "SMOKE RESULT: failed"; exit 1; }
serve "$P-other" || { log "cannot start $P-other"; log "SMOKE RESULT: failed"; exit 1; }
if [ "$MGR_FREE" = 1 ]; then
  docker run -d --name "$P-mgr" "${RUN_OPTS[@]}" --network "$NET" --ip "$MANAGER_IP" "$IMAGE" \
    sleep "$LIFE_S" >/dev/null || { log "cannot start $P-mgr"; log "SMOKE RESULT: failed"; exit 1; }
fi
IP_A="$(ip_of "$P-a" "$NET")"; IP_B="$(ip_of "$P-b" "$NET")"; IP_OTHER="$(ip_of "$P-other" bridge)"
log "lab a $IP_A, lab b $IP_B, other bridge $IP_OTHER"

# 6. runtime
RT_NAMES="a b other"
[ "$MGR_FREE" = 1 ] && RT_NAMES="$RT_NAMES mgr"
for n in $RT_NAMES; do
  rt="$(docker inspect -f '{{.HostConfig.Runtime}}' "$P-$n")"
  if [ "$rt" = runsc ]; then pass "$P-$n runs under runsc"; else fail "$P-$n runs under '$rt'"; fi
done

# Wait until both labs answer (from the host: the bridge is local).
for ip in "$IP_A" "$IP_B"; do
  for _ in $(seq 1 20); do h_tcp "$ip" "$PORT" && break; sleep 1; done
done

# Tool control: the in-container TCP probe must be able to succeed at all, or every "cannot"
# below would pass for the wrong reason.
if c_tcp "$P-a" 127.0.0.1 "$PORT" && ! c_tcp "$P-a" 127.0.0.1 1; then
  pass "control: the in-container TCP probe tells an open port from a closed one"
else
  fail "control: the in-container TCP probe does not tell open (127.0.0.1:$PORT) from closed (127.0.0.1:1)"
  log "SMOKE RESULT: failed"; exit 1
fi

# 1. manager address -> lab 7681
if [ "$MGR_FREE" = 1 ]; then
  ok=1
  for _ in 1 2 3 4 5; do if c_http "$P-mgr" "http://$IP_A:$PORT/"; then ok=0; break; fi; sleep 2; done
  if [ "$ok" = 0 ]; then pass "manager address reaches lab a on $PORT"; else fail "manager address cannot reach lab a on $PORT"; fi
else
  skip "manager address $MANAGER_IP is in use (the manager is deployed); not testing from it"
fi

# 2. lab -> lab 7681
if h_tcp "$IP_A" "$PORT" && h_tcp "$IP_B" "$PORT"; then
  expect_blocked "$P-b" "$IP_A" "$PORT" "lab b -> lab a:$PORT"
  expect_blocked "$P-a" "$IP_B" "$PORT" "lab a -> lab b:$PORT"
else
  fail "control: the labs do not answer on $PORT from the host"
fi

# 3. lab -> host (only ports the host itself answers on count)
PUBLIC_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p')"
DOCKER0_IP="$(ip -4 -o addr show docker0 2>/dev/null | awk '{print $4}' | cut -d/ -f1)"
tested=0
for ip in "$GATEWAY" "$DOCKER0_IP" "$PUBLIC_IP"; do
  [ -n "$ip" ] || continue
  for port in $HOST_PORTS; do
    h_tcp "$ip" "$port" || continue
    tested=$((tested + 1))
    expect_blocked "$P-a" "$ip" "$port" "lab -> host $ip:$port"
  done
done
if [ "$tested" = 0 ]; then fail "control: no host port answered from the host; nothing tested"; fi

# 4. lab -> container on another bridge
if h_tcp "$IP_OTHER" "$PORT"; then
  expect_blocked "$P-a" "$IP_OTHER" "$PORT" "lab -> docker0 container $IP_OTHER:$PORT"
else
  fail "control: the docker0 container does not answer from the host"
fi

# 5. lab -> internet (IP literals, so DNS plays no part; each one only counts if the host reaches it)
tested=0
for hp in 1.1.1.1:443 8.8.8.8:443 1.1.1.1:80; do
  h_tcp "${hp%:*}" "${hp##*:}" || continue
  tested=$((tested + 1))
  expect_blocked "$P-a" "${hp%:*}" "${hp##*:}" "lab -> internet $hp"
done
if [ "$tested" = 0 ]; then fail "control: the host reaches none of the internet targets; nothing tested"; fi

cleanup
NOW="$(container_state)"
if [ "$NOW" = "$BASE" ]; then
  pass "foreign containers unchanged (names, states, start times)"
else
  fail "foreign containers changed during the smoke (a restart loop elsewhere also shows here)"
  log "--- changed lines"; diff <(printf '%s\n' "$BASE") <(printf '%s\n' "$NOW") || true
fi

if [ "$FAILED" = 0 ]; then log "SMOKE RESULT: ok"; exit 0; fi
log "SMOKE RESULT: failed"; exit 1
