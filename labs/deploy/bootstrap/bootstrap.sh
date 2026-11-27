#!/usr/bin/env bash
# Server bootstrap for the lab platform (issue #3, DECISIONS D25).
#
# Runs as root on the lab host. One change at a time, in this order:
#   runtime (runsc in daemon.json) -> swap -> network (lf-labs) -> iptables (+ boot unit)
# Before the first change it records a baseline of everything that is not ours (containers
# that are not lf-/lab-, HTTP and TCP checks). After every change it re-checks the baseline;
# on any difference that change is rolled back and the run fails.
#
# Environment:
#   DRY_RUN=1            print every command and the planned daemon.json diff, change nothing
#   LF_BASELINE_URLS     space separated URLs, each must answer 2xx/3xx
#   LF_BASELINE_TCP      space separated host:port, each must accept a TCP connection
# Argument:
#   --rules-only         (used by lf-labs-iptables.service at boot) only re-apply the iptables rules
#
# The last stdout line is "BOOTSTRAP RESULT: dry run|changed|no changes".
set -Eeuo pipefail

# One bootstrap at a time on the server, dry runs included, taken before anything else. The kernel
# drops the lock when this process dies; commands started through run() do not inherit it.
if [ "${1:-}" != "--rules-only" ]; then
  exec 9>/run/lf-bootstrap.lock
  flock -n 9 || { echo 'another bootstrap is running'; echo 'BOOTSTRAP RESULT: failed'; exit 1; }
fi

DRY_RUN="${DRY_RUN:-0}"
LF_BASELINE_URLS="${LF_BASELINE_URLS:-}"
LF_BASELINE_TCP="${LF_BASELINE_TCP:-}"

# Pinned runsc release (bump both lines together; the hash is the published runsc.sha512).
RUNSC_RELEASE=20260817.0
RUNSC_SHA512=84936438d583ec976800f464e75a83e1515f0890b451b9b4db219c4472b54ca9b106a6772ee683f1e64cce2128871d7637b14d800591f8451b8137f6c39fb2ef

DAEMON_JSON=/etc/docker/daemon.json
NET=lf-labs
BRIDGE=br-lf-labs
SUBNET=172.31.240.0/24
# Dynamic addresses only from the upper half: the manager's fixed .2 is never handed out.
IP_RANGE=172.31.240.128/25
GATEWAY=172.31.240.1
MANAGER_IP=172.31.240.2
LAB_PORT=7681
CHAIN=LF-LABS-INPUT
TAG=lf-labs
SWAPFILE=/swapfile-lf
UNIT=lf-labs-iptables.service
UNIT_PATH="/etc/systemd/system/$UNIT"
SBIN_COPY=/usr/local/sbin/lf-labs-iptables
SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
STAMP="$(date +%Y%m%d%H%M%S)"

CHANGED=0
CURRENT=""
BASE_STATE=""
DAEMON_BACKUP=""
DAEMON_WRITTEN=0
DOCKER_DIR_CREATED=0

log() { printf '%s\n' "$*"; }
err() { printf '%s\n' "$*" >&2; }
dry() { [ "$DRY_RUN" = 1 ]; }

# Wait for the xtables lock instead of failing when another tool holds it.
iptables() { command iptables -w 10 "$@"; }
ip6tables() { command ip6tables -w 10 "$@"; }

# Print a command; run it unless this is a dry run.
run() {
  printf '+ %s\n' "$*"
  if dry; then return 0; fi
  "$@" 9>&-
}
# Same for a shell snippet (pipes, redirects).
runsh() {
  printf '+ %s\n' "$1"
  if dry; then return 0; fi
  bash -c "$1" 9>&-
}

# ------------------------------------------------------------------ baseline

# Containers that are not ours, with their start time: a restart changes this text.
container_state() {
  local id
  { for id in $(docker ps -aq 2>/dev/null); do
      docker inspect -f '{{.Name}} {{.State.Running}} {{.State.StartedAt}}' "$id" 2>/dev/null || true
    done; } | { grep -v -E '^/(lf|lab)-' || true; } | sort
}

check_url() {
  local url=$1 code="" attempt
  for attempt in 1 2; do
    code="$(curl -s -o /dev/null -m 20 -w '%{http_code}' "$url" 2>/dev/null || true)"
    case "$code" in 2??|3??) log "baseline ok: $url answered $code"; return 0 ;; esac
    sleep 3
  done
  err "baseline check failed: $url answered '${code:-none}'"
  return 1
}

check_tcp() {
  local hp=$1
  if timeout 5 bash -c 'exec 3<>"/dev/tcp/$0/$1"' "${hp%:*}" "${hp##*:}" 2>/dev/null; then
    log "baseline ok: tcp $hp accepts connections"
    return 0
  fi
  err "baseline check failed: tcp $hp does not accept connections"
  return 1
}

# Returns non-zero when the baseline is not as recorded.
baseline_ok() {
  local ok=0 u t now
  for u in $LF_BASELINE_URLS; do check_url "$u" || ok=1; done
  for t in $LF_BASELINE_TCP; do check_tcp "$t" || ok=1; done
  now="$(container_state)"
  if [ "$now" != "$BASE_STATE" ]; then
    err "baseline check failed: containers differ from the baseline"
    err "--- before"; err "$BASE_STATE"; err "--- now"; err "$now"
    ok=1
  else
    log "baseline ok: $(printf '%s' "$now" | grep -c . || true) foreign containers, names, states and start times unchanged"
  fi
  return "$ok"
}

# ------------------------------------------------------------------ report

report() {
  log "== $1"
  log "-- free memory"; free -m || true
  log "-- free disk"; df -h / || true
  log "-- $DAEMON_JSON (key names only)"
  if [ ! -f "$DAEMON_JSON" ]; then log "(no file)"
  else jq -r 'keys | join(" ")' "$DAEMON_JSON" || log "(not valid JSON)"; fi
  if [ -f "$DAEMON_JSON" ] && jq -e '.runtimes' "$DAEMON_JSON" >/dev/null 2>&1; then
    log "runtimes: $(jq -c '.runtimes | with_entries(.value |= (.path // ""))' "$DAEMON_JSON" 2>/dev/null)"
  fi
  log "-- reloadable keys a docker reload would apply from the file (besides runsc)"; report_reload_extras
  log "-- runsc version"; { runsc --version 2>/dev/null | head -1; } || log "runsc not installed"
  log "-- live docker runtimes (names and paths)"; log "$(runtimes_brief)"
  log "-- live dockerd command line"; dockerd_cmdline || log "(dockerd process not found)"
  if runsc_in_file && ! runsc_overlay_ok; then
    log "WARNING: runtimes.runsc in $DAEMON_JSON has no runtimeArgs $RUNSC_OVERLAY: lab root filesystem writes are not capped by the memory limit (existing entry left untouched)"
  fi
  if suid_in_file && ! suid_entry_ok; then
    log "WARNING: runtimes.$SUID_RT in $DAEMON_JSON is not runsc's path with $SUID_ARG: existing entry left untouched"
  fi
  log "-- network $NET (subnet ip-range)"; network_config || log "(not created)"
  log "-- iptables -S DOCKER-USER"; iptables -S DOCKER-USER || true
  log "-- iptables -S INPUT (lf-labs rules)"
  { iptables -S INPUT | grep -E -- "--comment \"?$TAG\"?( |$)"; } || log "(none)"
}

# ------------------------------------------------------------------ step: runtime

runsc_in_file() {
  [ -f "$DAEMON_JSON" ] || return 1
  jq -e '.runtimes.runsc' "$DAEMON_JSON" >/dev/null 2>&1
}

RUNSC_OVERLAY="--overlay2=root:memory"

runsc_overlay_ok() {
  jq -e --arg a "$RUNSC_OVERLAY" '(.runtimes.runsc.runtimeArgs // []) | any(. == $a)' "$DAEMON_JSON" >/dev/null 2>&1
}

# The second runtime for labs that need setuid binaries (sudo, D179): runsc's path and args plus --allow-suid.
SUID_RT=runsc-suid
SUID_ARG="--allow-suid"

suid_in_file() {
  [ -f "$DAEMON_JSON" ] || return 1
  jq -e --arg n "$SUID_RT" '.runtimes[$n]' "$DAEMON_JSON" >/dev/null 2>&1
}

# The file entry is the one this script would write: the whole runsc entry plus the --allow-suid arg.
suid_entry_ok() {
  jq -e --arg n "$SUID_RT" --arg a "$SUID_ARG" '.runtimes.runsc as $r | $r != null and .runtimes[$n] == ($r | .runtimeArgs = ((.runtimeArgs // []) + [$a]))' "$DAEMON_JSON" >/dev/null 2>&1
}

runtimes_json() { docker info --format '{{json .Runtimes}}' 2>/dev/null || true; }

# Runtime names and paths only: the full `docker info` entries carry feature JSON (public log).
runtimes_brief() { runtimes_json | jq -c 'with_entries(.value |= (.path // ""))' 2>/dev/null || echo "(could not read the live runtimes)"; }

# Exact runtime name in the live runtimes (a key, not a substring of a path or another name).
runtime_has() {
  local info
  info="$(runtimes_json)"
  jq -e --arg n "$1" 'has($n)' <<<"$info" >/dev/null 2>&1
}

runsc_loaded() { runtime_has runsc; }
suid_loaded() { runtime_has "$SUID_RT"; }

# The runsc docker has loaded carries the overlay arg (what `docker info` shows, not the file).
runsc_loaded_overlay() {
  local info
  info="$(runtimes_json)"
  jq -e --arg a "$RUNSC_OVERLAY" '(.runsc.runtimeArgs // []) | any(. == $a)' <<<"$info" >/dev/null 2>&1
}

dockerd_pid() { pgrep -xo dockerd 2>/dev/null || true; }

# jq is needed for every daemon.json and docker info check below. A real run installs it first.
# A dry run changes nothing on the host (D133): without jq it shows the planned install and stops.
ensure_jq() {
  command -v jq >/dev/null 2>&1 && return 0
  if dry; then
    log "+ planned: install the jq package with apt"
    err "jq is not on this host and a dry run installs nothing, so the daemon.json and runtime checks cannot run. A real run installs jq first."
    log "BOOTSTRAP RESULT: failed"
    exit 1
  fi
  log "jq missing: installing it with apt"
  apt_get install -y jq
  JQ_INSTALLED=1
}

# The live dockerd command line, one argument per element, in DOCKERD_ARGS. Fails when there is none.
DOCKERD_ARGS=()
dockerd_args() {
  local pid
  DOCKERD_ARGS=()
  pid="$(dockerd_pid)"
  [ -n "$pid" ] && [ -r "/proc/$pid/cmdline" ] || return 1
  mapfile -d '' -t DOCKERD_ARGS < "/proc/$pid/cmdline" || true
}

# Lab isolation lives in DOCKER-USER rules: dockerd with iptables disabled (flag or daemon.json)
# never evaluates them. Fails the run, dry runs included, before anything is applied.
iptables_fail() {
  err "dockerd runs with iptables disabled ($1): Docker never evaluates the DOCKER-USER chain, so the lab isolation rules would do nothing."
  if [ "${JQ_INSTALLED:-0}" = 1 ]; then err "Nothing was changed except installing the jq package."; else err "Nothing was changed."; fi
  err "The owner has to enable iptables for dockerd (that needs a docker restart, which this script never does), then re-run."
  log "BOOTSTRAP RESULT: failed"
  exit 1
}

# The flag form needs no jq. dockerd parses the value with Go's ParseBool.
iptables_flag_check() {
  local a
  dockerd_args || return 0
  for a in "${DOCKERD_ARGS[@]}"; do
    case "$a" in
      --iptables=0|--iptables=f|--iptables=F|--iptables=false|--iptables=False|--iptables=FALSE)
        iptables_fail "the dockerd flag $a" ;;
    esac
  done
}

# The config-file form (the live --config-file value, else the default file) needs jq.
iptables_file_check() {
  local i n file=$DAEMON_JSON
  dockerd_args || true
  n=${#DOCKERD_ARGS[@]}
  for ((i = 0; i < n; i++)); do
    case "${DOCKERD_ARGS[i]}" in
      --config-file=*) file=${DOCKERD_ARGS[i]#*=} ;;
      --config-file) [ $((i + 1)) -lt $n ] && file=${DOCKERD_ARGS[i+1]} ;;
    esac
  done
  if [ -f "$file" ] && jq -e '.iptables == false' "$file" >/dev/null 2>&1; then
    iptables_fail "\"iptables\": false in $file"
  fi
}

# Flags whose values are harmless to print; every other flag value is redacted (the output goes
# to a public Actions log). Host flags print only fd:// and unix:// values (a tcp host is an address).
SAFE_DOCKERD_FLAGS=" --containerd --add-runtime --config-file --data-root --iptables --ip-forward --live-restore "

# The live dockerd command line with values redacted. The program name prints as is. Any other bare
# argument (no leading -) is a value: printed only right after a safe flag given without =, else
# <redacted>. A flag not on the safe list prints as <name>=<redacted>; a single-dash argument's
# name is its first two characters, so a short flag group (-DH, -Dfoo) shows no value either.
# -H/--host print fd:// and unix:// values only.
dockerd_cmdline() {
  local a name v i n prev_safe=0 out=()
  dockerd_args || return 1
  n=${#DOCKERD_ARGS[@]}
  for ((i = 0; i < n; i++)); do
    a=${DOCKERD_ARGS[i]}
    if [[ "$a" != -* ]]; then
      if [ "$i" = 0 ] || [ "$prev_safe" = 1 ]; then out+=("$a"); else out+=("<redacted>"); fi
      prev_safe=0
      continue
    fi
    prev_safe=0
    if [[ "$a" == --* ]]; then name=${a%%=*}; else name=${a:0:2}; fi
    if [ "$name" = -H ] || [ "$name" = --host ]; then
      v=""
      if [ "$name" = -H ] && [ ${#a} -gt 2 ]; then v=${a:2}; v=${v#=}
      elif [ "$name" = --host ] && [[ "$a" == *=* ]]; then v=${a#*=}
      elif [ $((i + 1)) -lt $n ] && [[ "${DOCKERD_ARGS[i+1]}" != -* ]]; then i=$((i + 1)); v=${DOCKERD_ARGS[i]}; fi
      case "$v" in
        fd://*|unix://*) out+=("$name=$v") ;;
        *) out+=("$name=<redacted>") ;;
      esac
    elif [[ "$a" == --* && "$SAFE_DOCKERD_FLAGS" == *" $name "* ]]; then
      out+=("$a")
      [[ "$a" == *=* ]] || prev_safe=1
    else
      out+=("$name=<redacted>")
    fi
  done
  echo "${out[*]}"
}

# runsc is loaded but not from daemon.json (a dockerd flag). daemon.json stays untouched: the same
# runtime name there would stop dockerd at its next restart, and a flag cannot carry runtime args.
# Satisfied when the loaded runsc already has the overlay arg, otherwise the owner has to act.
runsc_flag_registered_check() {
  if runsc_loaded_overlay; then
    log "runsc is registered outside $DAEMON_JSON (dockerd flag) and already has $RUNSC_OVERLAY: nothing to do"
    return 0
  fi
  err "runsc is loaded but not from $DAEMON_JSON (a dockerd flag such as --add-runtime), and it has no $RUNSC_OVERLAY."
  err "Not editing $DAEMON_JSON: the same runtime name there would stop dockerd at its next restart."
  err "The owner has to remove the flag or move runsc into $DAEMON_JSON; that needs a docker restart, which this script never does."
  err "live docker runtimes: $(runtimes_brief)"
  err "live dockerd command line: $(dockerd_cmdline || echo unknown)"
  log "BOOTSTRAP RESULT: failed"
  exit 1
}

# runsc-suid is wanted when it is not in the file, or is the entry we would write and not loaded.
# A differing entry stays as it is (the report warns); with runsc outside the file there is
# nothing to copy it from.
need_suid() {
  # A loaded runsc without the overlay: no sudo runtime whose root writes bypass the memory cap.
  runsc_loaded_overlay || return 1
  if suid_in_file; then
    suid_entry_ok || return 1
    suid_loaded && return 1
    return 0
  fi
  runsc_in_file
}

need_runtime() {
  if runsc_in_file; then
    runsc_loaded || return 0
    need_suid
    return
  fi
  if runsc_loaded; then
    runsc_flag_registered_check
    return 1
  fi
  return 0
}

# Validate a daemon.json candidate the way dockerd will read it: with the live dockerd flags
# (a flag and the same key in the file make dockerd refuse to start). No pipeline: under pipefail
# a probe that exits early would kill the run.
validate_daemon_json() {
  local file=$1 a skip=0 help out rc=0 keep=()
  help="$(dockerd --help 2>&1 || true)"
  if [[ "$help" != *--validate* ]]; then
    log "dockerd does not support --validate: skipping the config validation"
    return 0
  fi
  if dockerd_args; then
    for a in "${DOCKERD_ARGS[@]:1}"; do
      if [ "$skip" = 1 ]; then skip=0; continue; fi
      case "$a" in
        --config-file) skip=1 ;;
        --config-file=*) ;;
        *) keep+=("$a") ;;
      esac
    done
  fi
  printf '+ dockerd <the live flags, %s of them> --validate --config-file %s\n' "${#keep[@]}" "$file"
  # The error text quotes values from the file and the flags, anywhere in the message, and no
  # pattern list can be complete: print one generic line with the exit code, nothing of the text.
  out="$(dockerd ${keep[@]+"${keep[@]}"} --validate --config-file "$file" 2>&1)" || rc=$?
  if [ "$rc" != 0 ]; then
    err "dockerd --validate rejected the candidate daemon.json (exit $rc); details are not printed because they can contain values"
  fi
  return "$rc"
}

# Keys of daemon.json that dockerd applies on a reload. A reload applies the whole file, so
# runsc in the file but not loaded is only reloaded when none of these (and no other runtime that
# is not loaded) would change with it. Source: moby v28.0.0 daemon/reload.go, reload_unix.go and
# cmd/dockerd/daemon.go (D197). The running values are not observable: any is a "differs".
RELOADABLE_KEYS='["debug","labels","live-restore","max-concurrent-downloads","max-concurrent-uploads","max-download-attempts","shutdown-timeout","default-runtime","allow-nondistributable-artifacts","insecure-registries","registry-mirrors","authorization-plugins","features","node-generic-resources","network-diagnostic-port","metrics-addr","cdi-spec-dirs","default-network-opts","proxies","default-shm-size","default-ipc-mode","default-cgroupns-mode"]'

# Prints what a reload would change besides runsc, one item per line.
reload_extras() {
  local k name
  for k in $(jq -r --argjson r "$RELOADABLE_KEYS" 'keys[] | select(. as $k | $r | index($k))' "$DAEMON_JSON"); do
    echo "key $k"
  done
  for name in $(jq -r '(.runtimes // {}) | del(.runsc) | keys[]' "$DAEMON_JSON"); do
    if [ "$name" = "$SUID_RT" ]; then
      if suid_entry_ok; then continue; fi
      echo "runtime $name (differs from the expected entry)"
      continue
    fi
    if ! runtime_has "$name"; then echo "runtime $name (not loaded)"; continue; fi
    runtime_matches_live "$name" || echo "runtime $name (path or runtimeArgs differ from the loaded one)"
  done
  # Loaded, but gone from the file: a reload would drop it (docker's built-in runtimes are not in the file).
  for name in $(runtimes_json | jq -r --slurpfile f "$DAEMON_JSON" '(keys - ["runc","io.containerd.runc.v2","runsc"])[] as $n | select(($f[0].runtimes // {}) | has($n) | not) | $n' 2>/dev/null); do
    [ "$name" = "$SUID_RT" ] && continue
    echo "runtime $name (loaded, removed from the file)"
  done
}

# The file entry of runtime $1 is the loaded one (path and runtimeArgs, as `docker info` shows them).
# runsc-suid also needs an entry without any key besides path and runtimeArgs.
runtime_matches_live() {
  local live
  live="$(runtimes_json)"
  jq -e --arg n "$1" --argjson l "$live" '.runtimes[$n] as $f | ($f | type) == "object" and ($l | type) == "object" and ($l | has($n)) and ((($f | keys) - ["path", "runtimeArgs"]) == [] or $n != "runsc-suid") and ($f.path == $l[$n].path) and (($f.runtimeArgs // []) == ($l[$n].runtimeArgs // []))' "$DAEMON_JSON" >/dev/null 2>&1
}

# For the report: what a docker reload would apply from the file besides runsc.
report_reload_extras() {
  local extras
  if [ ! -f "$DAEMON_JSON" ]; then log "(no file)"; return 0; fi
  extras="$(reload_extras)" || extras="(could not read the file)"
  log "${extras:-(none)}"
}

strip_runsc='del(.runtimes.runsc, .runtimes["runsc-suid"]) | if .runtimes == {} then del(.runtimes) else . end'

RUNSC_URL="https://storage.googleapis.com/gvisor/releases/release/$RUNSC_RELEASE/x86_64"
RUNSC_DEST=/usr/local/bin/runsc
RUNSC_INSTALLED=0
RELOAD_EXISTING=0
RUNSC_LOADED_BEFORE=0
SUID_LIVE_BEFORE=0
DAEMON_TMP=""

# Used only for the jq install (ensure_jq), which never runs in a dry run.
apt_get() {
  printf '+ env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l NEEDRESTART_SUSPEND=1 apt-get -o DPkg::Lock::Timeout=120 %s\n' "$*"
  env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l NEEDRESTART_SUSPEND=1 apt-get -o DPkg::Lock::Timeout=120 "$@" 9>&-
}

# Never through apt: the runsc package's postinst edits daemon.json and reloads docker by itself.
# The pinned release is checked against RUNSC_SHA512, installed under a temp name next to its
# destination and moved into place (atomic).
install_runsc() {
  local dir actual
  log "runsc binary not found: installing release $RUNSC_RELEASE to $RUNSC_DEST (sha512 checked)"
  if dry; then
    printf '+ download %s/runsc, check its sha512 against the pinned value, write %s.lf-new, mv it to %s\n' "$RUNSC_URL" "$RUNSC_DEST" "$RUNSC_DEST"
    return 0
  fi
  dir="$(mktemp -d)"
  curl -fsSL --retry 3 -o "$dir/runsc" "$RUNSC_URL/runsc" || { rm -rf "$dir"; err "runsc download failed"; return 1; }
  actual="$(sha512sum "$dir/runsc")"
  if [ "${actual%% *}" != "$RUNSC_SHA512" ]; then
    rm -rf "$dir"
    err "runsc sha512 does not match the pinned release $RUNSC_RELEASE"
    return 1
  fi
  if ! { install -m 0755 "$dir/runsc" "$RUNSC_DEST.lf-new" && mv -f "$RUNSC_DEST.lf-new" "$RUNSC_DEST"; }; then
    rm -rf "$dir"; rm -f "$RUNSC_DEST.lf-new"
    err "installing runsc failed"
    return 1
  fi
  RUNSC_INSTALLED=1
  rm -rf "$dir"
}

# runsc is in daemon.json but docker has not loaded it. A reload applies the whole file, so refuse
# (changing nothing) unless runsc is the only difference and the file already carries the overlay arg.
check_existing_runsc() {
  local extras
  if ! runsc_overlay_ok; then
    err "runsc is in $DAEMON_JSON without runtimeArgs $RUNSC_OVERLAY: not editing the file, nothing was changed. Add the arg by hand, then re-run."
    return 1
  fi
  extras="$(reload_extras)"
  if [ -n "$extras" ]; then
    err "runsc is in $DAEMON_JSON but not loaded, and a reload would apply more than runsc (the running values are not known):"
    err "$extras"
    err "Nothing was changed. Check the file, run 'systemctl reload docker' yourself if it is right, then re-run."
    return 1
  fi
}

apply_runtime() {
  local runsc_bin new orig i extras existing=0
  if runsc_in_file && ! runsc_loaded; then
    check_existing_runsc
  elif runsc_in_file; then
    # runsc is loaded and a reload (or the write below) applies the whole file: refuse, changing
    # nothing, when other pending keys or runtimes, an edited runsc or a differing runsc-suid come with it.
    extras="$(reload_extras)"
    if ! runtime_matches_live runsc; then extras="${extras:+$extras$'
'}runtime runsc (path or runtimeArgs differ from the loaded one)"; fi
    if [ -n "$extras" ]; then
      err "a reload of $DAEMON_JSON would apply more than the runsc runtimes (the running values are not known):"
      err "$extras"
      err "Nothing was changed. Check the file, run 'systemctl reload docker' yourself if it is right, then re-run."
      return 1
    fi
  elif suid_in_file && ! runtime_matches_live "$SUID_RT"; then
    # Fresh path: a runsc-suid in the file that is not live as written would go live with the reload.
    err "$DAEMON_JSON has a $SUID_RT entry but no runsc: the reload would make that entry live. Nothing was changed. Remove it or add runsc by hand, then re-run."
    return 1
  elif [ -f "$DAEMON_JSON" ] && [ -n "$(runtimes_json | jq -r "keys[]" 2>/dev/null)" ] && extras="$(reload_extras | grep "^runtime " | grep -v "^runtime $SUID_RT " || true)" && [ -n "$extras" ]; then
    # Fresh path, docker answering: another runtime the reload would load or drop (other reloadable keys: D197, not refused here).
    err "a reload of $DAEMON_JSON would also apply other runtimes (the running values are not known):"
    err "$extras"
    err "Nothing was changed. Check the file, run 'systemctl reload docker' yourself if it is right, then re-run."
    return 1
  fi
  if [ -f "$DAEMON_JSON" ]; then
    DAEMON_BACKUP="$DAEMON_JSON.lf-bak.$STAMP"
    run cp -p "$DAEMON_JSON" "$DAEMON_BACKUP"
  fi
  if command -v runsc >/dev/null 2>&1; then runsc_bin="$(command -v runsc)"; else install_runsc; runsc_bin=$RUNSC_DEST; fi

  if [ -f "$DAEMON_JSON" ]; then orig="$(cat "$DAEMON_JSON")"; else orig='{}'; fi
  new="$orig"
  if ! runsc_in_file; then
    new="$(printf '%s' "$new" | jq --arg p "$runsc_bin" --arg a "$RUNSC_OVERLAY" '.runtimes = ((.runtimes // {}) + {runsc: {path: $p, runtimeArgs: [$a]}})')"
  fi
  # runsc-suid: the same entry plus --allow-suid; an entry already there is never rewritten.
  if ! suid_in_file; then
    new="$(printf '%s' "$new" | jq --arg n "$SUID_RT" --arg a "$SUID_ARG" '.runtimes[$n] = (.runtimes.runsc | .runtimeArgs = ((.runtimeArgs // []) + [$a]))')"
  fi
  RUNSC_LOADED_BEFORE=0
  if runsc_loaded; then RUNSC_LOADED_BEFORE=1; fi
  SUID_LIVE_BEFORE=0
  if runtime_has "$SUID_RT"; then SUID_LIVE_BEFORE=1; fi
  if [ "$(printf '%s' "$new" | jq -S .)" = "$(printf '%s' "$orig" | jq -S .)" ]; then
    log "the runtimes are already in $DAEMON_JSON and nothing else would change: validating, then only reloading docker"
    validate_daemon_json "$DAEMON_JSON"
    RELOAD_EXISTING=1
    run systemctl reload docker
  else
    if [ "$(printf '%s' "$new" | jq -S "$strip_runsc")" != "$(printf '%s' "$orig" | jq -S "$strip_runsc")" ]; then
      err "refusing to write $DAEMON_JSON: the merge would change more than the runsc runtimes"
      return 1
    fi
    # runsc in the file but not loaded: a rollback has to drop it again (as when only reloading).
    if runsc_in_file && ! runsc_loaded; then existing=1; fi
    # Only the runtimes key is shown: the neighbouring keys may hold credentials (public log).
    log "planned $DAEMON_JSON diff (jq -S '{runtimes}', before -> after):"
    diff -u <(printf '%s' "$orig" | jq -S '{runtimes: (.runtimes // {})}') <(printf '%s' "$new" | jq -S '{runtimes: (.runtimes // {})}') || true
    if dry; then
      [ -d /etc/docker ] || printf '+ mkdir -p -m 0755 /etc/docker\n'
      printf '+ write temp file in /etc/docker, dockerd --validate, mv to %s\n' "$DAEMON_JSON"
    else
      # A host that never had a daemon.json may not have /etc/docker at all.
      if [ ! -d /etc/docker ]; then DOCKER_DIR_CREATED=1; fi
      mkdir -p -m 0755 /etc/docker
      DAEMON_TMP="$(mktemp /etc/docker/daemon.json.lf-new.XXXXXX)"
      # mktemp makes a 0600 file: give it the mode and owner of the file it replaces.
      if [ -f "$DAEMON_JSON" ]; then
        chmod --reference="$DAEMON_JSON" "$DAEMON_TMP"
        chown --reference="$DAEMON_JSON" "$DAEMON_TMP"
      else
        chmod 0644 "$DAEMON_TMP"
      fi
      printf '%s\n' "$new" > "$DAEMON_TMP"
      validate_daemon_json "$DAEMON_TMP"
      DAEMON_WRITTEN=1
      mv "$DAEMON_TMP" "$DAEMON_JSON"
      DAEMON_TMP=""
      RELOAD_EXISTING=$existing
    fi
    run systemctl reload docker
  fi
  if ! dry; then
    for i in $(seq 30); do
      if runsc_loaded; then break; fi
      sleep 1
    done
    if ! runsc_loaded; then err "docker does not list runsc after the reload"; return 1; fi
    if ! runsc_loaded_overlay; then err "docker lists runsc without $RUNSC_OVERLAY after the reload: $(runtimes_brief)"; return 1; fi
    if suid_entry_ok; then
      for i in $(seq 30); do
        if suid_loaded; then break; fi
        sleep 1
      done
      if ! suid_loaded; then err "docker does not list $SUID_RT after the reload"; return 1; fi
    fi
  fi
  # The check image comes from a local copy; a missing one is pulled from a mirror first, because
  # Docker Hub answers anonymous pulls with 429 (#294). The runsc runs below never pull.
  # (The read-only inspect also runs in a dry run, so the plan shows the pull only when it is needed.)
  if ! docker image inspect hello-world >/dev/null 2>&1; then
    if run docker pull mirror.gcr.io/library/hello-world; then
      if ! run docker tag mirror.gcr.io/library/hello-world hello-world; then
        err "could not pull or tag the hello-world image for the runsc check (the pull worked, docker tag failed)"
        return 1
      fi
    elif ! run docker pull hello-world; then
      err "cannot get the hello-world image for the runsc check: mirror.gcr.io and Docker Hub both refused the pull"
      return 1
    fi
  fi
  # The first runsc start right after a reload can fail once ("OCI runtime start failed ...
  # oom_score_adj: no such process", #153). Retry that one start; a second failure fails the step.
  if ! run docker run --rm --name lf-runsc-check --pull=never --runtime=runsc hello-world; then
    err "the runsc test container did not start; retrying once"
    docker rm -f lf-runsc-check >/dev/null 2>&1 || true
    sleep 2
    run docker run --rm --name lf-runsc-check --pull=never --runtime=runsc hello-world
  fi
}

# Replace daemon.json atomically with the bytes of file $1 (a temp file in /etc/docker, then mv).
# cp -p keeps the mode and owner of the source.
put_daemon_json() {
  local tmp
  tmp="$(mktemp /etc/docker/daemon.json.lf-restore.XXXXXX)" && cp -p "$1" "$tmp" && mv "$tmp" "$DAEMON_JSON"
}

# Waits until docker no longer lists the runtime(s) named in the arguments.
wait_runtimes_gone() {
  local i n gone
  for i in $(seq 30); do
    gone=1
    for n in "$@"; do if runtime_has "$n"; then gone=0; fi; done
    if [ "$gone" = 1 ]; then return 0; fi
    sleep 1
  done
  err "ROLLBACK: docker still lists $* after the reload; continuing with the restore"
  return 1
}

rollback_runtime() {
  local stage drop gone
  if [ -n "$DAEMON_TMP" ]; then rm -f "$DAEMON_TMP" || true; fi
  docker rm -f lf-runsc-check >/dev/null 2>&1 || true
  if [ "$DAEMON_WRITTEN" = 1 ] || [ "$RELOAD_EXISTING" = 1 ]; then
    err "rolling back daemon.json"
    # Docker drops a runtime on reload only when the file still has a "runtimes" key. When the
    # original has none, or when runsc was in the original file, first reload a copy without
    # runsc (keeping an empty "runtimes"), then restore the exact bytes.
    if [ "$RELOAD_EXISTING" = 1 ] || [ -z "$DAEMON_BACKUP" ] || ! jq -e 'has("runtimes")' "$DAEMON_BACKUP" >/dev/null 2>&1; then
      stage="$(mktemp /etc/docker/daemon.json.lf-stage.XXXXXX)" || stage=""
      if [ -n "$stage" ]; then
        # runsc that was loaded before this run stays loaded: only runsc-suid is dropped then.
        drop='del(.runsc, .["runsc-suid"])'; gone=(runsc "$SUID_RT")
        if [ "$RUNSC_LOADED_BEFORE" = 1 ]; then drop='del(.["runsc-suid"])'; gone=("$SUID_RT"); fi
        if [ -n "$DAEMON_BACKUP" ]; then jq ".runtimes = ((.runtimes // {}) | $drop)" "$DAEMON_BACKUP" > "$stage"; else echo '{"runtimes": {}}' > "$stage"; fi
        if put_daemon_json "$stage"; then
          systemctl reload docker || true
          wait_runtimes_gone "${gone[@]}" || true
        fi
        rm -f "$stage" || true
      fi
    fi
    if [ -n "$DAEMON_BACKUP" ]; then
      put_daemon_json "$DAEMON_BACKUP" || true
    else
      rm -f "$DAEMON_JSON" || true
    fi
    # The original file lists runsc when this run only reloaded it: a reload now would load it again.
    if [ "$RELOAD_EXISTING" = 0 ]; then systemctl reload docker || true; fi
    # runsc stays loaded when the original file had it loaded; runsc-suid goes unless it was live before.
    gone_after=()
    if [ "$RUNSC_LOADED_BEFORE" = 0 ]; then gone_after+=(runsc); fi
    if [ "$SUID_LIVE_BEFORE" = 0 ]; then gone_after+=("$SUID_RT"); fi
    if [ "${#gone_after[@]}" -gt 0 ]; then wait_runtimes_gone "${gone_after[@]}" || true; fi
  fi
  # An /etc/docker this run created goes away only when empty (rmdir fails on anything else).
  if [ "$DOCKER_DIR_CREATED" = 1 ]; then rmdir /etc/docker 2>/dev/null || true; fi
  if [ "$RUNSC_INSTALLED" = 1 ]; then rm -f "$RUNSC_DEST" || true; fi
  rm -f "$RUNSC_DEST.lf-new" || true
}

# ------------------------------------------------------------------ step: swap

need_swap() { [ -z "$(swapon --show --noheadings 2>/dev/null)" ]; }

apply_swap() {
  local avail min="${LF_SWAP_MIN_FREE_GB:-14}"
  avail="$(df --output=avail -BG / | tail -1 | tr -dc '0-9')"
  if [ "${avail:-0}" -lt "$min" ]; then
    err "not enough free disk (${avail:-0} GB, need $min GB: 4 GB swap plus the disk reserve)"
    return 1
  fi
  run fallocate -l 4G "$SWAPFILE"
  run chmod 600 "$SWAPFILE"
  run mkswap "$SWAPFILE"
  run swapon "$SWAPFILE"
  if ! grep -q "^$SWAPFILE " /etc/fstab 2>/dev/null; then
    runsh "echo '$SWAPFILE none swap sw 0 0' >> /etc/fstab"
  fi
}

rollback_swap() {
  err "rolling back swap"
  swapoff "$SWAPFILE" 2>/dev/null || true
  rm -f "$SWAPFILE" || true
  sed -i "\\|^$SWAPFILE |d" /etc/fstab || true
}

# ------------------------------------------------------------------ step: network

network_config() {
  docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}} {{.IPRange}}{{end}}' "$NET" 2>/dev/null
}

# bridge name, icc, ipv6, subnet, ip-range of the existing network, one line
network_full() {
  docker network inspect -f '{{index .Options "com.docker.network.bridge.name"}} {{index .Options "com.docker.network.bridge.enable_icc"}} {{.EnableIPv6}} {{range .IPAM.Config}}{{.Subnet}} {{.IPRange}}{{end}}' "$NET" 2>/dev/null
}

# Needed when the network is missing. An existing one with another subnet or ip-range is never
# recreated (containers may be attached): the run fails with a clear message instead.
need_network() {
  local cfg
  docker network inspect "$NET" >/dev/null 2>&1 || return 0
  cfg="$(network_full || true)"
  if [ "$cfg" != "$BRIDGE false false $SUBNET $IP_RANGE" ]; then
    err "network $NET exists as '$cfg' (bridge icc ipv6 subnet ip-range), expected '$BRIDGE false false $SUBNET $IP_RANGE'."
    err "Not recreating it (containers may be attached). Fix it by hand, then re-run."
    log "BOOTSTRAP RESULT: failed"
    exit 1
  fi
  return 1
}

ip2int() { local a b c d; IFS=. read -r a b c d <<<"$1"; echo $(( (a << 24) | (b << 16) | (c << 8) | d )); }

# 0 when CIDR $1 and CIDR $2 overlap (a bare address counts as /32).
cidr_overlap() {
  local n1=${1%/*} l1=${1#*/} n2=${2%/*} l2=${2#*/} s1 e1 s2 e2
  [ "$l1" = "$1" ] && l1=32
  [ "$l2" = "$2" ] && l2=32
  s1=$(( $(ip2int "$n1") & ((0xFFFFFFFF << (32 - l1)) & 0xFFFFFFFF) )); e1=$(( s1 + (1 << (32 - l1)) - 1 ))
  s2=$(( $(ip2int "$n2") & ((0xFFFFFFFF << (32 - l2)) & 0xFFFFFFFF) )); e2=$(( s2 + (1 << (32 - l2)) - 1 ))
  [ "$s1" -le "$e2" ] && [ "$s2" -le "$e1" ]
}

# Before any step: when lf-labs still has to be created, its subnet must not overlap another
# Docker network or a host route. Otherwise the run would stop at the network step with the
# earlier steps already applied.
preflight_subnet() {
  local id sub dst ids routes clash=0
  # An existing lf-labs is checked here too: a wrong one stops the run before any change
  # (need_network exits), a correct one needs no subnet check.
  need_network || return 0
  # Read both lists first: a failing command must stop the run, not compare nothing.
  ids="$(docker network ls -q)" || { err "preflight: docker network ls failed; nothing was changed"; log "BOOTSTRAP RESULT: failed"; exit 1; }
  routes="$(ip -4 route show)" || { err "preflight: ip route failed; nothing was changed"; log "BOOTSTRAP RESULT: failed"; exit 1; }
  for id in $ids; do
    for sub in $(docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}} {{end}}' "$id" 2>/dev/null); do
      case "$sub" in *:*) continue ;; esac
      if cidr_overlap "$SUBNET" "$sub"; then
        err "subnet $SUBNET overlaps docker network $(docker network inspect -f '{{.Name}}' "$id") ($sub)"
        clash=1
      fi
    done
  done
  for dst in $(printf '%s\n' "$routes" | awk '{print $1}'); do
    case "$dst" in [0-9]*) ;; *) continue ;; esac
    if cidr_overlap "$SUBNET" "$dst"; then err "subnet $SUBNET overlaps host route $dst"; clash=1; fi
  done
  if [ "$clash" = 1 ]; then
    err "nothing was changed"
    log "BOOTSTRAP RESULT: failed"
    exit 1
  fi
  log "preflight ok: $SUBNET overlaps no docker network and no host route"
}

apply_network() {
  run docker network create \
    --opt "com.docker.network.bridge.name=$BRIDGE" \
    --opt com.docker.network.bridge.enable_icc=false \
    --ipv6=false --subnet "$SUBNET" --ip-range "$IP_RANGE" "$NET"
}

rollback_network() {
  err "rolling back network"
  docker network rm "$NET" || true
}

# ------------------------------------------------------------------ step: iptables

# One rule per line, in evaluation order. Every rule matches only traffic from the bridge.
docker_user_rules() {
  local base="-i $BRIDGE" tag="-m comment --comment $TAG" net
  echo "$base -o $BRIDGE -s $MANAGER_IP -p tcp --dport $LAB_PORT $tag -j ACCEPT"
  echo "$base -m conntrack --ctstate ESTABLISHED,RELATED $tag -j ACCEPT"
  for net in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16 100.64.0.0/10; do
    echo "$base -d $net $tag -j DROP"
  done
  echo "$base -o docker0 $tag -j DROP"
  echo "$base -o br-+ $tag -j DROP"
  # Labs have no direct internet (spec 6): everything else from the bridge is dropped. The
  # manager's own egress is excluded; the allowlist proxy inserts its accept ahead of this.
  echo "$base ! -s $MANAGER_IP $tag -j DROP"
}

# Rules of the INPUT helper chain (first word is a placeholder for the chain name).
chain_rules() {
  echo "x -i $BRIDGE -m conntrack --ctstate ESTABLISHED,RELATED -m comment --comment $TAG -j ACCEPT"
  echo "x -i $BRIDGE -m comment --comment $TAG -j DROP"
}

# IPv6 guard (D167, D168): lf-labs has no IPv6, but a lab may still get a link-local address under
# gVisor. Drop everything IPv6 from the bridge, tagged like the IPv4 rules. One rule per line,
# first word is the chain. Forwarding goes in DOCKER-USER when ip6tables has it, else FORWARD.
# Both are decided once per run (IP6_STATE, FWD6), so a run says it once and uses one chain.
PROC_ROOT="${PROC_ROOT:-/proc}"
IP6_STATE=""
FWD6=""

# The boot unit can start before dockerd has made the ip6tables DOCKER-USER chain: wait for it
# briefly, so --rules-only and a normal run choose the same chain.
ip6_fwd_chain() {
  local i
  [ -n "$FWD6" ] && return 0
  FWD6=FORWARD
  for i in 1 2 3 4 5 6; do
    if ip6tables -n -L DOCKER-USER >/dev/null 2>&1; then FWD6=DOCKER-USER; return 0; fi
    [ "$i" = 6 ] || sleep 1
  done
}

ip6_rules() {
  echo "$FWD6 -i $BRIDGE -m comment --comment $TAG -j DROP"
  echo "INPUT -i $BRIDGE -m comment --comment $TAG -j DROP"
}

# none: the kernel has no IPv6, nothing to guard. ok: ip6tables works. unusable: the kernel has
# IPv6 but ip6tables is missing or fails; the guard fails closed (no `command -v`: ip6tables is a
# function here, so that would always succeed).
ip6_state() {
  [ -n "$IP6_STATE" ] && return 0
  if [ ! -e "$PROC_ROOT/net/if_inet6" ]; then
    IP6_STATE=none
    log "no IPv6 in the kernel (net/if_inet6 absent): no IPv6 guard added"
  elif ip6tables -n -L INPUT >/dev/null 2>&1; then
    IP6_STATE=ok
  else
    IP6_STATE=unusable
  fi
}

ip6_missing() {
  local rule a rules
  ip6_state
  case "$IP6_STATE" in none) return 1 ;; unusable) return 0 ;; esac
  ip6_fwd_chain
  mapfile -t rules < <(ip6_rules)
  for rule in "${rules[@]}"; do
    read -ra a <<<"$rule"
    ip6tables -C "${a[@]}" 2>/dev/null || return 0
  done
  # The rules only guard lab-to-lab IPv6 when bridged frames go through ip6tables.
  [ "$(cat "$PROC_ROOT/sys/net/bridge/bridge-nf-call-ip6tables" 2>/dev/null || true)" = 1 ] || return 0
  return 1
}

ip6_usable_check() {
  if [ "$IP6_STATE" = unusable ]; then
    err "the kernel has IPv6 but ip6tables is missing or not usable: the IPv6 guard cannot be put in place"
    return 1
  fi
}

# Read-only: lab-to-lab IPv6 only meets the guard when bridged frames go through ip6tables. At boot
# this runs after the drops are inserted, so a failed check never leaves the host without them.
ip6_bridge_nf_check() {
  local v
  v="$(cat "$PROC_ROOT/sys/net/bridge/bridge-nf-call-ip6tables" 2>/dev/null || true)"
  if [ "$v" != 1 ]; then
    err "bridge-nf-call-ip6tables is '${v:-absent}', not 1: lab-to-lab IPv6 would bypass the ip6tables guard (load br_netfilter and set it to 1; this script never writes it)"
    return 1
  fi
}

apply_ip6() {
  local rule a rules
  ip6_state
  [ "$IP6_STATE" = none ] && return 0
  ip6_usable_check
  ip6_fwd_chain
  mapfile -t rules < <(ip6_rules)
  for rule in "${rules[@]}"; do
    read -ra a <<<"$rule"
    if ! ip6tables -C "${a[@]}" 2>/dev/null; then
      run ip6tables -I "${a[0]}" 1 "${a[@]:1}"
      INS_V6+=("$rule")
    fi
  done
  ip6_bridge_nf_check
}

# What this run inserted, so a rollback removes only that.
INS_V6=()
INS_DU=()
INS_CHAIN=()
CHAIN_CREATED=0
JUMP_ADDED=0
SBIN_INSTALLED=0
SBIN_PREV=0
UNIT_WRITTEN=0
UNIT_PREV=0
UNIT_ENABLED=0

INPUT_JUMP=(-i "$BRIDGE" -m comment --comment "$TAG" -j "$CHAIN")

rules_missing() {
  local rule a rules
  mapfile -t rules < <(docker_user_rules)
  for rule in "${rules[@]}"; do
    read -ra a <<<"$rule"
    iptables -C DOCKER-USER "${a[@]}" 2>/dev/null || return 0
  done
  iptables -n -L "$CHAIN" >/dev/null 2>&1 || return 0
  mapfile -t rules < <(chain_rules)
  for rule in "${rules[@]}"; do
    read -ra a <<<"$rule"
    iptables -C "$CHAIN" "${a[@]:1}" 2>/dev/null || return 0
  done
  iptables -C INPUT "${INPUT_JUMP[@]}" 2>/dev/null || return 0
  ip6_missing && return 0
  return 1
}

unit_text() {
  cat <<UNITEOF
[Unit]
Description=Re-apply the Learning Factory lab network rules
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$SBIN_COPY --rules-only

[Install]
WantedBy=multi-user.target
UNITEOF
}

sbin_current() { [ -f "$SBIN_COPY" ] && cmp -s "$SCRIPT_PATH" "$SBIN_COPY"; }
unit_current() { [ -f "$UNIT_PATH" ] && cmp -s <(unit_text) "$UNIT_PATH"; }

need_iptables() {
  rules_missing && return 0
  sbin_current || return 0
  unit_current || return 0
  systemctl is-enabled "$UNIT" >/dev/null 2>&1 || return 0
  return 1
}

apply_rules() {
  local rule pos=0 a rules
  mapfile -t rules < <(docker_user_rules)
  for rule in "${rules[@]}"; do
    pos=$((pos + 1))
    read -ra a <<<"$rule"
    if ! iptables -C DOCKER-USER "${a[@]}" 2>/dev/null; then
      run iptables -I DOCKER-USER "$pos" "${a[@]}"
      INS_DU+=("$rule")
    fi
  done
  if ! iptables -n -L "$CHAIN" >/dev/null 2>&1; then
    run iptables -N "$CHAIN"
    CHAIN_CREATED=1
  fi
  mapfile -t rules < <(chain_rules)
  for rule in "${rules[@]}"; do
    read -ra a <<<"$rule"
    if ! iptables -C "$CHAIN" "${a[@]:1}" 2>/dev/null; then
      run iptables -A "$CHAIN" "${a[@]:1}"
      INS_CHAIN+=("$rule")
    fi
  done
  # The jump comes last, so the chain is complete before any traffic reaches it.
  if ! iptables -C INPUT "${INPUT_JUMP[@]}" 2>/dev/null; then
    run iptables -I INPUT 1 "${INPUT_JUMP[@]}"
    JUMP_ADDED=1
  fi
  apply_ip6
}

# An earlier boot copy or unit is kept as <file>.lf-prev while this step runs: a rollback puts it
# back, a success deletes it. A leftover .lf-prev means an earlier run died half way: stop.
apply_iptables() {
  local f
  for f in "$SBIN_COPY.lf-prev" "$UNIT_PATH.lf-prev"; do
    if [ -e "$f" ]; then err "$f exists: an earlier run did not finish. Move it back over the original (or delete it if the original is right), then re-run."; return 1; fi
  done
  if ! sbin_current; then
    if [ -f "$SBIN_COPY" ]; then run cp -p "$SBIN_COPY" "$SBIN_COPY.lf-prev"; SBIN_PREV=1; fi
    SBIN_INSTALLED=1
    run install -m 0755 "$SCRIPT_PATH" "$SBIN_COPY.lf-new"
    run mv -f "$SBIN_COPY.lf-new" "$SBIN_COPY"
  fi
  apply_rules
  if ! unit_current; then
    if [ -f "$UNIT_PATH" ]; then run cp -p "$UNIT_PATH" "$UNIT_PATH.lf-prev"; UNIT_PREV=1; fi
    printf '+ write %s\n' "$UNIT_PATH"
    UNIT_WRITTEN=1
    if ! dry; then
      unit_text > "$UNIT_PATH.lf-new" || return 1
      mv -f "$UNIT_PATH.lf-new" "$UNIT_PATH" || return 1
    fi
    run systemctl daemon-reload
  fi
  if ! systemctl is-enabled "$UNIT" >/dev/null 2>&1; then UNIT_ENABLED=1; fi
  run systemctl enable "$UNIT"
}

cleanup_iptables() { rm -f "$SBIN_COPY.lf-prev" "$UNIT_PATH.lf-prev"; }

rollback_iptables() {
  local rule a
  err "rolling back iptables (only what this run added)"
  if [ "$UNIT_ENABLED" = 1 ]; then systemctl disable "$UNIT" 2>/dev/null || true; fi
  rm -f "$SBIN_COPY.lf-new" "$UNIT_PATH.lf-new" || true
  if [ "$UNIT_PREV" = 1 ]; then
    mv -f "$UNIT_PATH.lf-prev" "$UNIT_PATH" || true
  elif [ "$UNIT_WRITTEN" = 1 ]; then
    rm -f "$UNIT_PATH" || true
  fi
  if [ "$SBIN_PREV" = 1 ]; then
    mv -f "$SBIN_COPY.lf-prev" "$SBIN_COPY" || true
  elif [ "$SBIN_INSTALLED" = 1 ]; then
    rm -f "$SBIN_COPY" || true
  fi
  if [ "$UNIT_WRITTEN" = 1 ]; then systemctl daemon-reload || true; fi
  for rule in "${INS_V6[@]}"; do
    read -ra a <<<"$rule"
    ip6tables -D "${a[@]}" || true
  done
  if [ "$JUMP_ADDED" = 1 ]; then iptables -D INPUT "${INPUT_JUMP[@]}" || true; fi
  for rule in "${INS_CHAIN[@]}"; do
    read -ra a <<<"$rule"
    iptables -D "$CHAIN" "${a[@]:1}" || true
  done
  if [ "$CHAIN_CREATED" = 1 ]; then iptables -X "$CHAIN" || true; fi
  for rule in "${INS_DU[@]}"; do
    read -ra a <<<"$rule"
    iptables -D DOCKER-USER "${a[@]}" || true
  done
}

# ------------------------------------------------------------------ driver

# A rollback must finish: ignore HUP/INT/TERM/PIPE (a dropped SSH session, a cancelled workflow,
# a closed log pipe) for the rest of the run, and do not stop on a failing restore command.
begin_rollback() {
  trap '' HUP INT TERM PIPE
  trap - ERR
  set +e
}

on_err() {
  local rc=$1 cmd=$2
  begin_rollback
  err "failed (exit $rc): $cmd"
  if [ -n "$CURRENT" ] && ! dry; then
    err "step '$CURRENT' failed; rolling it back"
    "rollback_$CURRENT" || true
    if baseline_ok; then err "baseline is as before after the rollback"; else err "baseline still differs after the rollback"; fi
  fi
  log "BOOTSTRAP RESULT: failed"
  exit 1
}
trap 'on_err "$?" "$BASH_COMMAND"' ERR
trap 'on_err 143 "interrupted by a signal"' HUP INT TERM

do_step() {
  local n=$1
  if ! "need_$n"; then
    log "== step $n: already in place"
    return 0
  fi
  log "== step $n: applying"
  CURRENT=$n
  "apply_$n"
  if ! dry && ! baseline_ok; then
    begin_rollback
    err "baseline differs after step '$n'; rolling it back"
    "rollback_$n" || true
    if baseline_ok; then err "baseline restored"; fi
    log "BOOTSTRAP RESULT: failed"
    exit 1
  fi
  if ! dry && declare -F "cleanup_$n" >/dev/null; then "cleanup_$n"; fi
  CURRENT=""
  CHANGED=1
}

if [ "${1:-}" = "--rules-only" ]; then
  for _ in $(seq 60); do
    if iptables -n -L DOCKER-USER >/dev/null 2>&1; then break; fi
    sleep 1
  done
  apply_rules
  exit 0
fi

if [ "$(id -u)" != 0 ]; then err "run as root"; exit 1; fi

iptables_flag_check
ensure_jq
iptables_file_check

log "== baseline"
BASE_STATE="$(container_state)"
log "containers that are not lf-/lab-:"; log "${BASE_STATE:-(none)}"
if ! baseline_ok; then
  err "baseline failed before any change; nothing was changed"
  log "BOOTSTRAP RESULT: failed"
  exit 1
fi
if dry; then log "DRY RUN: nothing below is executed"; fi
preflight_subnet

report "before"
do_step runtime
do_step swap
do_step network
do_step iptables
if ! dry; then report "after"; fi

if dry; then
  log "BOOTSTRAP RESULT: dry run"
elif [ "$CHANGED" = 1 ]; then
  log "BOOTSTRAP RESULT: changed"
else
  log "BOOTSTRAP RESULT: no changes"
fi
