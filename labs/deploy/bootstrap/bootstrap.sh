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

DRY_RUN="${DRY_RUN:-0}"
LF_BASELINE_URLS="${LF_BASELINE_URLS:-}"
LF_BASELINE_TCP="${LF_BASELINE_TCP:-}"

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

log() { printf '%s\n' "$*"; }
err() { printf '%s\n' "$*" >&2; }
dry() { [ "$DRY_RUN" = 1 ]; }

# Wait for the xtables lock instead of failing when another tool holds it.
iptables() { command iptables -w 10 "$@"; }

# Print a command; run it unless this is a dry run.
run() {
  printf '+ %s\n' "$*"
  if dry; then return 0; fi
  "$@"
}
# Same for a shell snippet (pipes, redirects).
runsh() {
  printf '+ %s\n' "$1"
  if dry; then return 0; fi
  bash -c "$1"
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
    case "$code" in 2??|3??) return 0 ;; esac
    sleep 3
  done
  err "baseline check failed: $url answered '${code:-none}'"
  return 1
}

check_tcp() {
  local hp=$1
  if timeout 5 bash -c 'exec 3<>"/dev/tcp/$0/$1"' "${hp%:*}" "${hp##*:}" 2>/dev/null; then return 0; fi
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
  fi
  return "$ok"
}

# ------------------------------------------------------------------ report

report() {
  log "== $1"
  log "-- free memory"; free -m || true
  log "-- free disk"; df -h / || true
  log "-- $DAEMON_JSON"; if [ -f "$DAEMON_JSON" ]; then cat "$DAEMON_JSON"; else log "(no file)"; fi
  log "-- runsc version"; { runsc --version 2>/dev/null | head -1; } || log "runsc not installed"
  if runsc_in_file && ! runsc_overlay_ok; then
    log "WARNING: runtimes.runsc in $DAEMON_JSON has no runtimeArgs $RUNSC_OVERLAY: lab root filesystem writes are not capped by the memory limit (existing entry left untouched)"
  fi
  log "-- network $NET (subnet ip-range)"; network_config || log "(not created)"
  log "-- iptables -S DOCKER-USER"; iptables -S DOCKER-USER || true
  log "-- iptables -S INPUT (lf-labs rules)"
  { iptables -S INPUT | grep -E -- "--comment \"?$TAG\"?( |$)"; } || log "(none)"
}

# ------------------------------------------------------------------ step: runtime

runsc_in_file() {
  [ -f "$DAEMON_JSON" ] || return 1
  if command -v jq >/dev/null 2>&1; then
    jq -e '.runtimes.runsc' "$DAEMON_JSON" >/dev/null 2>&1
  else
    grep -q '"runsc"' "$DAEMON_JSON"
  fi
}

RUNSC_OVERLAY="--overlay2=root:memory"

runsc_overlay_ok() {
  if command -v jq >/dev/null 2>&1; then
    jq -e --arg a "$RUNSC_OVERLAY" '(.runtimes.runsc.runtimeArgs // []) | any(. == $a)' "$DAEMON_JSON" >/dev/null 2>&1
  else
    grep -q -- "$RUNSC_OVERLAY" "$DAEMON_JSON"
  fi
}

runsc_loaded() {
  local info
  info="$(docker info --format '{{json .Runtimes}}' 2>/dev/null || true)"
  [[ "$info" == *runsc* ]]
}

need_runtime() {
  runsc_in_file || return 0
  runsc_loaded || return 0
  return 1
}

strip_runsc='del(.runtimes.runsc) | if .runtimes == {} then del(.runtimes) else . end'

RUNSC_URL=https://storage.googleapis.com/gvisor/releases/release/latest/x86_64
RUNSC_DEST=/usr/local/bin/runsc
RUNSC_INSTALLED=0
RUNSC_WAS_LOADED=0
DAEMON_TMP=""

apt_get() {
  run env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l NEEDRESTART_SUSPEND=1 apt-get "$@"
}

# Never through apt: the runsc package's postinst edits daemon.json and reloads docker by itself.
install_runsc() {
  local dir
  log "runsc binary not found: installing the release binary to $RUNSC_DEST (sha512 checked)"
  if dry; then
    printf '+ download %s/runsc and runsc.sha512, sha512sum -c, install -m 0755 to %s\n' "$RUNSC_URL" "$RUNSC_DEST"
    return 0
  fi
  dir="$(mktemp -d)"
  ( cd "$dir" \
    && curl -fsSL -o runsc "$RUNSC_URL/runsc" \
    && curl -fsSL -o runsc.sha512 "$RUNSC_URL/runsc.sha512" \
    && sha512sum -c runsc.sha512 ) || { rm -rf "$dir"; err "runsc download or checksum failed"; return 1; }
  install -m 0755 "$dir/runsc" "$RUNSC_DEST"
  RUNSC_INSTALLED=1
  rm -rf "$dir"
}

apply_runtime() {
  local runsc_bin new orig i
  if runsc_loaded; then RUNSC_WAS_LOADED=1; fi
  if [ -f "$DAEMON_JSON" ]; then
    DAEMON_BACKUP="$DAEMON_JSON.lf-bak.$STAMP"
    run cp -p "$DAEMON_JSON" "$DAEMON_BACKUP"
  fi
  if command -v runsc >/dev/null 2>&1; then runsc_bin="$(command -v runsc)"; else install_runsc; runsc_bin=$RUNSC_DEST; fi

  if runsc_in_file; then
    log "runsc is already in $DAEMON_JSON; only reloading docker"
    run systemctl reload docker
  elif dry && ! command -v jq >/dev/null 2>&1; then
    log "jq is NOT installed on this host: a real run installs it with apt first; the daemon.json diff cannot be shown in this dry run"
    apt_get install -y jq
    log "planned: add runtimes.runsc={path:$runsc_bin,runtimeArgs:[$RUNSC_OVERLAY]} to $DAEMON_JSON (only that key; everything else compared with jq -S), validate with dockerd --validate, mv into place"
    run systemctl reload docker
  else
    command -v jq >/dev/null 2>&1 || apt_get install -y jq
    if [ -f "$DAEMON_JSON" ]; then orig="$(cat "$DAEMON_JSON")"; else orig='{}'; fi
    new="$(printf '%s' "$orig" | jq --arg p "$runsc_bin" --arg a "$RUNSC_OVERLAY" '.runtimes = ((.runtimes // {}) + {runsc: {path: $p, runtimeArgs: [$a]}})')"
    if [ "$(printf '%s' "$new" | jq -S "$strip_runsc")" != "$(printf '%s' "$orig" | jq -S "$strip_runsc")" ]; then
      err "refusing to write $DAEMON_JSON: the merge would change more than runtimes.runsc"
      return 1
    fi
    log "planned $DAEMON_JSON diff (jq -S, before -> after):"
    diff -u <(printf '%s' "$orig" | jq -S .) <(printf '%s' "$new" | jq -S .) || true
    if dry; then
      printf '+ write temp file in /etc/docker, dockerd --validate, mv to %s\n' "$DAEMON_JSON"
    else
      DAEMON_TMP="$(mktemp /etc/docker/daemon.json.lf-new.XXXXXX)"
      printf '%s\n' "$new" > "$DAEMON_TMP"
      if dockerd --help 2>&1 | grep -q -- '--validate'; then
        dockerd --validate --config-file "$DAEMON_TMP"
      else
        log "dockerd does not support --validate: skipping the config validation"
      fi
      DAEMON_WRITTEN=1
      mv "$DAEMON_TMP" "$DAEMON_JSON"
      DAEMON_TMP=""
    fi
    run systemctl reload docker
  fi
  if ! dry; then
    for i in $(seq 30); do
      if runsc_loaded; then break; fi
      sleep 1
    done
    runsc_loaded
  fi
  run docker run --rm --name lf-runsc-check --runtime=runsc hello-world
}

# Replace daemon.json atomically with the bytes of file $1 (a temp file in /etc/docker, then mv).
put_daemon_json() {
  local tmp
  tmp="$(mktemp /etc/docker/daemon.json.lf-restore.XXXXXX)" && cp -p "$1" "$tmp" && mv "$tmp" "$DAEMON_JSON"
}

wait_runsc_gone() {
  local i
  for i in $(seq 30); do
    if ! runsc_loaded; then return 0; fi
    sleep 1
  done
  err "ROLLBACK: docker still lists runsc after the reload; continuing with the restore"
  return 1
}

rollback_runtime() {
  local stage
  if [ -n "$DAEMON_TMP" ]; then rm -f "$DAEMON_TMP" || true; fi
  docker rm -f lf-runsc-check >/dev/null 2>&1 || true
  if [ "$DAEMON_WRITTEN" = 1 ]; then
    err "rolling back daemon.json"
    # Docker drops a runtime on reload only when the file still has a "runtimes" key. When the
    # original has none, first reload a copy with "runtimes": {}, then restore the exact bytes.
    if [ "$RUNSC_WAS_LOADED" = 0 ] && command -v jq >/dev/null 2>&1         && { [ -z "$DAEMON_BACKUP" ] || ! jq -e 'has("runtimes")' "$DAEMON_BACKUP" >/dev/null 2>&1; }; then
      stage="$(mktemp /etc/docker/daemon.json.lf-stage.XXXXXX)" || stage=""
      if [ -n "$stage" ]; then
        if [ -n "$DAEMON_BACKUP" ]; then jq '.runtimes = {}' "$DAEMON_BACKUP" > "$stage"; else echo '{"runtimes": {}}' > "$stage"; fi
        if put_daemon_json "$stage"; then
          systemctl reload docker || true
          wait_runsc_gone || true
        fi
        rm -f "$stage" || true
      fi
    fi
    if [ -n "$DAEMON_BACKUP" ]; then
      put_daemon_json "$DAEMON_BACKUP" || true
    else
      rm -f "$DAEMON_JSON" || true
    fi
    systemctl reload docker || true
    if [ "$RUNSC_WAS_LOADED" = 0 ]; then wait_runsc_gone || true; fi
  fi
  if [ "$RUNSC_INSTALLED" = 1 ]; then rm -f "$RUNSC_DEST" || true; fi
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

# What this run inserted, so a rollback removes only that.
INS_DU=()
INS_CHAIN=()
CHAIN_CREATED=0
JUMP_ADDED=0
SBIN_INSTALLED=0
UNIT_WRITTEN=0
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
}

apply_iptables() {
  apply_rules
  if ! sbin_current; then run install -m 0755 "$SCRIPT_PATH" "$SBIN_COPY"; SBIN_INSTALLED=1; fi
  if ! unit_current; then
    printf '+ write %s\n' "$UNIT_PATH"
    if ! dry; then unit_text > "$UNIT_PATH"; fi
    UNIT_WRITTEN=1
    run systemctl daemon-reload
  fi
  if ! systemctl is-enabled "$UNIT" >/dev/null 2>&1; then UNIT_ENABLED=1; fi
  run systemctl enable "$UNIT"
}

rollback_iptables() {
  local rule a
  err "rolling back iptables (only what this run added)"
  if [ "$UNIT_ENABLED" = 1 ]; then systemctl disable "$UNIT" 2>/dev/null || true; fi
  if [ "$UNIT_WRITTEN" = 1 ]; then rm -f "$UNIT_PATH" || true; fi
  if [ "$SBIN_INSTALLED" = 1 ]; then rm -f "$SBIN_COPY" || true; fi
  if [ "$UNIT_WRITTEN" = 1 ]; then systemctl daemon-reload || true; fi
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

on_err() {
  local rc=$1 cmd=$2
  trap - ERR HUP INT TERM
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
    err "baseline differs after step '$n'; rolling it back"
    trap - ERR HUP INT TERM
    "rollback_$n" || true
    if baseline_ok; then err "baseline restored"; fi
    log "BOOTSTRAP RESULT: failed"
    exit 1
  fi
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

log "== baseline"
BASE_STATE="$(container_state)"
log "containers that are not lf-/lab-:"; log "${BASE_STATE:-(none)}"
if ! baseline_ok; then
  err "baseline failed before any change; nothing was changed"
  log "BOOTSTRAP RESULT: failed"
  exit 1
fi
if dry; then log "DRY RUN: nothing below is executed"; fi

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
