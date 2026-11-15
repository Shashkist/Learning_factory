#!/usr/bin/env bash
# Liveness diagnostic for lab containers on the server (issue #3, step d).
#
# The isolation smoke saw its busybox labs on lf-labs exit (exit 0, not OOM) right after a
# `docker exec` into them, while a container with `sleep` as PID 1 survived its execs. This script
# tells the possible causes apart. Each variant starts one throw-away container (D20 lab limits),
# runs a few `docker exec` probes into it, waits, and reports whether it is still running:
#
#   sh-pid1-labs        sh -c 'httpd daemon && sleep' on lf-labs, runsc, execs use busybox timeout
#   sh-pid1-labs-plain  same, but the execs do not use `timeout` (no orphaned timer process)
#   sh-pid1-bridge      same as the first, on the default bridge (is lf-labs a factor?)
#   init-labs           same as the first with --init (tini is PID 1, as in lf-lab-term)
#   sleep-pid1-labs     sleep is PID 1, httpd started with `docker exec -d`
#   sh-pid1-bridge-runc same as sh-pid1-bridge under runc (control: runsc vs the busybox workload)
#
# Before the probes it prints PID 1's command line and the process table of each variant, so the
# log shows what really is PID 1 (ash may exec the last command of a list) and where orphans end
# up. All runsc variants share the server's runsc build, so this cannot compare runsc builds.
#
# For a container that died it prints the exit code, OOM flag, .State.Error, start and finish
# times and the last log lines; then the docker/containerd journal lines that mention it.
#
# It changes nothing outside its own lf-smoke-live-* containers, which it always removes. It never
# restarts Docker and never touches another container; foreign containers are compared before and
# after. Output ends with "LIVENESS RESULT: all alive", "LIVENESS RESULT: some died" (both exit 0:
# this is a diagnostic) or "LIVENESS RESULT: failed" (exit 1: it could not run, or a variant did
# not start). A one-off for issue #3 step (d): removed when #3 closes (D30).

set -uo pipefail

IMAGE="${LF_SMOKE_IMAGE:-busybox:1.36.1-musl}"
NET=lf-labs
PORT=7681
P=lf-smoke-live
# The lab limits of D20: memory with no extra swap (the host has 4 GB of swap), 256 pids, 0.5 CPU.
LIMITS=(--memory 256m --memory-swap 256m --pids-limit 256 --cpus 0.5)
RUN_OPTS=(--runtime runsc "${LIMITS[@]}")
LIFE_S=300
VARIANTS="sh-pid1-labs sh-pid1-labs-plain sh-pid1-bridge init-labs sleep-pid1-labs sh-pid1-bridge-runc"

log() { printf '%s %s\n' "$(date -u +%T)" "$*"; }

container_state() {
  local id
  { for id in $(docker ps -aq 2>/dev/null); do
      docker inspect -f '{{.Name}} {{.State.Running}} {{.State.StartedAt}}' "$id" 2>/dev/null || true
    done; } | { grep -v -E '^/(lf|lab)-' || true; } | sort
}

cleanup() {
  local v
  for v in $VARIANTS; do docker rm -f "$P-$v" >/dev/null 2>&1 || true; done
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM PIPE

running() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = true ]; }
details() {
  docker inspect -f 'status={{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} error="{{.State.Error}}" started={{.State.StartedAt}} finished={{.State.FinishedAt}}' "$1" 2>/dev/null \
    || echo 'no such container'
}

SERVE="mkdir -p /www && echo ok > /www/index.html && httpd -p $PORT -h /www && sleep $LIFE_S"

start_variant() { # variant
  local name="$P-$1"
  case "$1" in
    sh-pid1-labs|sh-pid1-labs-plain)
      docker run -d --name "$name" "${RUN_OPTS[@]}" --network "$NET" "$IMAGE" sh -c "$SERVE" ;;
    sh-pid1-bridge)
      docker run -d --name "$name" "${RUN_OPTS[@]}" "$IMAGE" sh -c "$SERVE" ;;
    sh-pid1-bridge-runc)
      docker run -d --name "$name" --runtime runc "${LIMITS[@]}" "$IMAGE" sh -c "$SERVE" ;;
    init-labs)
      docker run -d --name "$name" "${RUN_OPTS[@]}" --init --network "$NET" "$IMAGE" sh -c "$SERVE" ;;
    sleep-pid1-labs)
      docker run -d --name "$name" "${RUN_OPTS[@]}" --network "$NET" "$IMAGE" sleep "$LIFE_S" \
        && docker exec -d "$name" sh -c "mkdir -p /www && echo ok > /www/index.html && httpd -f -p $PORT -h /www" ;;
  esac
}

oneline() { tr '\n' ' ' | cut -c1-"$1"; }

probe() { # variant: one exec, prints the in-container result
  local name="$P-$1" out
  if [ "$1" = sh-pid1-labs-plain ]; then
    out="$(docker exec "$name" sh -c 'nc -z -w 3 127.0.0.1 '"$PORT"' </dev/null >/dev/null 2>&1; echo "nc=$?"' 2>&1)" || true
  else
    out="$(docker exec "$name" sh -c 'timeout 8 nc -z -w 3 127.0.0.1 '"$PORT"' </dev/null >/dev/null 2>&1; echo "nc=$?"' 2>&1)" || true
  fi
  printf '%s' "$out" | oneline 160
}

procs() { # variant: PID 1's command line and the process table, on one line
  docker exec "$P-$1" sh -c 'echo "pid1=[$(tr "\000" " " </proc/1/cmdline)]"; ps -o pid,ppid,stat,comm' 2>&1 | oneline 400
}

log "== liveness diagnostic"
log "kernel $(uname -r); $(/usr/local/bin/runsc --version 2>/dev/null | head -1 || echo 'runsc --version failed')"
if ! docker network inspect "$NET" >/dev/null 2>&1; then
  log "network $NET does not exist: run the bootstrap (apply) first"; log "LIVENESS RESULT: failed"; exit 1
fi
if ! docker info --format '{{json .Runtimes}}' | grep -q '"runsc"'; then
  log "runtime runsc is not registered with Docker"; log "LIVENESS RESULT: failed"; exit 1
fi
cleanup
BASE="$(container_state)"
SINCE="$(date -u '+%Y-%m-%d %H:%M:%S') UTC"
docker pull -q "$IMAGE" >/dev/null || { log "cannot pull $IMAGE"; log "LIVENESS RESULT: failed"; exit 1; }

NOSTART=""
for v in $VARIANTS; do
  if start_variant "$v" >/dev/null; then log "started $v"
  else NOSTART="$NOSTART $v"; log "NOSTART $v: $(details "$P-$v")"; fi
done
sleep 3
for v in $VARIANTS; do
  if running "$P-$v"; then log "procs $v: $(procs "$v")"; fi
done

for round in 1 2 3 4 5; do
  for v in $VARIANTS; do
    if running "$P-$v"; then log "round $round $v exec: $(probe "$v")"; else log "round $round $v: not running"; fi
  done
  sleep 2
  if [ "$round" = 1 ]; then # where the probes' orphans (busybox timeout timers) ended up
    for v in $VARIANTS; do
      if running "$P-$v"; then log "procs after round 1 $v: $(procs "$v")"; fi
    done
  fi
done
log "waiting 20 s after the last exec"
sleep 20

DIED=0
log "-- result per variant"
for v in $VARIANTS; do
  case " $NOSTART " in *" $v "*) log "NOSTART $v"; continue ;; esac
  if running "$P-$v"; then
    log "ALIVE  $v"
  else
    DIED=1
    log "DIED   $v: $(details "$P-$v")"
    docker logs --tail 30 "$P-$v" 2>&1 | sed 's/^/         log: /'
  fi
done

if [ "$DIED" = 1 ]; then
  log "-- docker and containerd journal since $SINCE (lines mentioning the smoke containers, last 40)"
  ids="$(for v in $VARIANTS; do docker inspect -f '{{.Id}}' "$P-$v" 2>/dev/null | cut -c1-12; done | paste -sd'|' -)"
  journalctl -u docker -u containerd --since "$SINCE" --no-pager -o short-iso 2>/dev/null \
    | grep -E "${ids:-$P}|$P" | tail -40 | cut -c1-300 || true
fi

cleanup
NOW="$(container_state)"
if [ "$NOW" = "$BASE" ]; then
  log "foreign containers unchanged (names, states, start times)"
else
  log "foreign containers changed during the run (a restart loop elsewhere also shows here)"
  diff <(printf '%s\n' "$BASE") <(printf '%s\n' "$NOW") || true
fi

if [ -n "$NOSTART" ]; then log "LIVENESS RESULT: failed (did not start:$NOSTART)"; exit 1; fi
if [ "$DIED" = 0 ]; then log "LIVENESS RESULT: all alive"; else log "LIVENESS RESULT: some died"; fi
exit 0
