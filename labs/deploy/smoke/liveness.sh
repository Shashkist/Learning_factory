#!/usr/bin/env bash
# Liveness diagnostic for lab containers on the server (issue #3, step d).
#
# The isolation smoke saw its busybox labs on lf-labs exit (exit 0, not OOM) right after a
# `docker exec` into them, while a container with `sleep` as PID 1 survived its execs. This script
# tells the possible causes apart. Each variant starts one throw-away container (production limits,
# runsc), runs a few `docker exec` probes into it, waits, and reports whether it is still running:
#
#   sh-pid1-labs        sh is PID 1 (httpd daemon && sleep), on lf-labs, execs use busybox timeout
#   sh-pid1-labs-plain  same, but the execs do not use `timeout` (no orphaned timer process)
#   sh-pid1-bridge      same as the first, on the default bridge (is lf-labs a factor?)
#   init-labs           same as the first with --init (tini is PID 1, as in lf-lab-term)
#   sleep-pid1-labs     sleep is PID 1, httpd started with `docker exec -d`
#
# For a container that died it prints the exit code, OOM flag, .State.Error, start and finish
# times, and the last log lines; then the dockerd journal lines that mention it.
#
# It changes nothing outside its own lf-smoke-live-* containers, which it always removes. It never
# restarts Docker and never touches another container; foreign containers are compared before and
# after. Output ends with "LIVENESS RESULT: all alive", "LIVENESS RESULT: some died" (both exit 0:
# this is a diagnostic) or "LIVENESS RESULT: failed" (exit 1: it could not run).

set -uo pipefail

IMAGE="${LF_SMOKE_IMAGE:-busybox:1.36.1-musl}"
NET=lf-labs
PORT=7681
P=lf-smoke-live
RUN_OPTS=(--runtime runsc --memory 256m --pids-limit 256)
LIFE_S=300
VARIANTS="sh-pid1-labs sh-pid1-labs-plain sh-pid1-bridge init-labs sleep-pid1-labs"

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
    init-labs)
      docker run -d --name "$name" "${RUN_OPTS[@]}" --init --network "$NET" "$IMAGE" sh -c "$SERVE" ;;
    sleep-pid1-labs)
      docker run -d --name "$name" "${RUN_OPTS[@]}" --network "$NET" "$IMAGE" sleep "$LIFE_S" \
        && docker exec -d "$name" sh -c "mkdir -p /www && echo ok > /www/index.html && httpd -f -p $PORT -h /www" ;;
  esac
}

probe() { # variant: one exec, prints the in-container result
  local name="$P-$1" out
  if [ "$1" = sh-pid1-labs-plain ]; then
    out="$(docker exec "$name" sh -c 'nc -z -w 3 127.0.0.1 '"$PORT"' </dev/null >/dev/null 2>&1; echo "nc=$?"' 2>&1)" || true
  else
    out="$(docker exec "$name" sh -c 'timeout 8 nc -z -w 3 127.0.0.1 '"$PORT"' </dev/null >/dev/null 2>&1; echo "nc=$?"' 2>&1)" || true
  fi
  printf '%s' "$out" | tr '\n' ' ' | cut -c1-160
}

log "== liveness diagnostic"
log "kernel $(uname -r); $(/usr/local/bin/runsc --version 2>/dev/null | head -1 || echo 'runsc --version failed')"
if ! docker network inspect "$NET" >/dev/null 2>&1; then
  log "network $NET does not exist: run the bootstrap (apply) first"; log "LIVENESS RESULT: failed"; exit 1
fi
cleanup
BASE="$(container_state)"
SINCE="$(date -u '+%Y-%m-%d %H:%M:%S')"
docker pull -q "$IMAGE" >/dev/null || { log "cannot pull $IMAGE"; log "LIVENESS RESULT: failed"; exit 1; }

for v in $VARIANTS; do
  if start_variant "$v" >/dev/null; then log "started $v"; else log "cannot start $v: $(details "$P-$v")"; fi
done
sleep 3

for round in 1 2 3 4 5; do
  for v in $VARIANTS; do
    if running "$P-$v"; then log "round $round $v exec: $(probe "$v")"; else log "round $round $v: not running"; fi
  done
  sleep 2
done
log "waiting 20 s after the last exec"
sleep 20

DIED=0
log "-- result per variant"
for v in $VARIANTS; do
  if running "$P-$v"; then
    log "ALIVE  $v"
  else
    DIED=1
    log "DIED   $v: $(details "$P-$v")"
    docker logs --tail 30 "$P-$v" 2>&1 | sed 's/^/         log: /'
  fi
done

if [ "$DIED" = 1 ]; then
  log "-- dockerd journal since $SINCE (lines mentioning the smoke containers or runsc, last 40)"
  ids="$(for v in $VARIANTS; do docker inspect -f '{{.Id}}' "$P-$v" 2>/dev/null | cut -c1-12; done | paste -sd'|' -)"
  journalctl -u docker --since "$SINCE" --no-pager -o short-iso 2>/dev/null \
    | grep -E "${ids:-lf-smoke-live}|runsc|$P" | tail -40 | cut -c1-300 || true
fi

cleanup
NOW="$(container_state)"
if [ "$NOW" = "$BASE" ]; then
  log "foreign containers unchanged (names, states, start times)"
else
  log "foreign containers changed during the run (a restart loop elsewhere also shows here)"
  diff <(printf '%s\n' "$BASE") <(printf '%s\n' "$NOW") || true
fi

if [ "$DIED" = 0 ]; then log "LIVENESS RESULT: all alive"; else log "LIVENESS RESULT: some died"; fi
exit 0
