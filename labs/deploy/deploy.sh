#!/usr/bin/env bash
# Deploy the lf-manager to this server (issue #13, D34). Idempotent: run it again with the same tag
# and nothing changes. Copied by .github/workflows/deploy.yml with compose.yml and nginx/ to
# /root/learning-factory/ and run there.
#
# Env:   LF_MANAGER_TAG     commit sha to deploy (required; `latest` is refused)
#        LF_DEPLOY_ROOT     default /root/learning-factory
#        LF_DOCKER          docker binary, default `docker`
#        LF_HEALTH_TIMEOUT  seconds to wait for the manager and the live checks, default 90
# Stdin: lines NAME=value for LF_DATABASE_URL, GHCR_USER, GHCR_TOKEN and the Google admin sign-in
#        values LF_GOOGLE_CLIENT_ID, LF_GOOGLE_CLIENT_SECRET, LF_ADMIN_EMAILS (any may be absent or empty).
#        Secrets travel here only: never in argv, never printed, never `set -x`, and
#        the stack is never rendered with the full-config subcommand (it prints env_file values).
# Never restarts Docker or touches containers that are not lf-*; shared nginx config changes go
# only through nginx/install-block.sh.
set -euo pipefail
umask 077

root=${LF_DEPLOY_ROOT:-/root/learning-factory}
docker=${LF_DOCKER:-docker}
tag=${LF_MANAGER_TAG:-}
timeout=${LF_HEALTH_TIMEOUT:-90}
envf="$root/.env"
registry=ghcr.io
image_prefix=ghcr.io/shashkist
labs_host=labs.shashkist.com
factory_host=factory.shashkist.com

say() { printf 'deploy: %s\n' "$*"; }
die() { printf 'deploy: %s\n' "$*" >&2; exit 1; }

[ -n "$tag" ] || die "LF_MANAGER_TAG is not set"
[ "$tag" != latest ] || die "refusing to deploy the moving tag 'latest'; pass a commit sha"
[[ "$tag" =~ ^[A-Za-z0-9._-]+$ ]] || die "LF_MANAGER_TAG has unexpected characters"

# ---- stdin: NAME=value lines
db_url=""; ghcr_user=""; ghcr_token=""
g_id=""; g_secret=""; g_emails=""
if [ ! -t 0 ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%$'\r'}
    case "$line" in
      LF_DATABASE_URL=*) db_url=${line#*=} ;;
      GHCR_USER=*) ghcr_user=${line#*=} ;;
      GHCR_TOKEN=*) ghcr_token=${line#*=} ;;
      LF_GOOGLE_CLIENT_ID=*) g_id=${line#*=} ;;
      LF_GOOGLE_CLIENT_SECRET=*) g_secret=${line#*=} ;;
      LF_ADMIN_EMAILS=*) g_emails=${line#*=} ;;
    esac
  done
fi

# ---- preflight (nothing is changed before all of it passes)
version_ge() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]; }

engine=$("$docker" version --format '{{.Server.Version}}' 2>/dev/null) || die "cannot talk to the Docker Engine"
engine=${engine#v}
version_ge "$engine" 28.0.0 || die "Docker Engine $engine is older than 28.0.0"
compose_v=$("$docker" compose version --short 2>/dev/null) || die "docker compose is not available"
compose_v=${compose_v#v}
version_ge "$compose_v" 2.33.1 || die "Docker Compose $compose_v is older than 2.33.1"
say "docker engine $engine, docker compose $compose_v"
"$docker" network inspect lf-labs >/dev/null 2>&1 || die "docker network lf-labs does not exist (run the bootstrap first)"
running=$("$docker" ps --format '{{.Names}}') || die "cannot list containers"
printf '%s\n' "$running" | grep -qx nginx-proxy3 || die "container nginx-proxy3 is not running"

# ---- .env (mode 600). Values are written, never printed.
mkdir -p "$root"
touch "$envf"
chmod 600 "$envf"

set_env() {
  local tmp
  tmp=$(mktemp "$root/.env.XXXXXX")
  { grep -v "^$1=" "$envf" || true; printf "%s='%s'\n" "$1" "$2"; } > "$tmp"
  mv "$tmp" "$envf"
}

if ! grep -q '^LF_ID_SECRET=.' "$envf"; then
  set_env LF_ID_SECRET "$(openssl rand -hex 32)"
  say "generated LF_ID_SECRET"
fi
if [ -n "$db_url" ]; then
  case "$db_url" in *"'"*) die "LF_DATABASE_URL contains a single quote" ;; esac
  set_env LF_DATABASE_URL "$db_url"
fi
# Google admin sign-in (D39): present values are written (a quote would break .env); a missing one
# only logs a line, and the manager then starts with Google sign-in "not configured".
for pair in "LF_GOOGLE_CLIENT_ID:$g_id" "LF_GOOGLE_CLIENT_SECRET:$g_secret" "LF_ADMIN_EMAILS:$g_emails"; do
  gname=${pair%%:*}; gval=${pair#*:}
  case "$gval" in *"'"*) die "$gname contains a single quote" ;; esac
  [ -z "$gval" ] || set_env "$gname" "$gval"
  grep -q "^$gname=." "$envf" || say "skip: $gname is missing (Google admin sign-in stays off)"
done
if ! grep -q '^LF_DATABASE_URL=.' "$envf"; then
  # Absence is a supported state: say what is missing and change nothing (the owner installs it).
  say "deploy skipped: LF_DATABASE_URL is missing (production environment secret, OWNER.md O3)"
  exit 0
fi
chmod 600 "$envf"
# A bare key (no value) counts too: compose hands it on from the shell. An optional UTF-8 BOM may precede it.
# grep reads the file itself (no pipe), so a large .env cannot lose the match to SIGPIPE under pipefail.
if grep -Eq $'^(\xEF\xBB\xBF)?[[:space:]]*(export[[:space:]]+)?LF_TERM_UPSTREAM[[:space:]]*([=:]|$)' "$envf"; then
  die "LF_TERM_UPSTREAM is set in .env (a test seam); remove that line, it must never run in production"
fi

# ---- the previous tag (for rollback)
prev=""
[ -f "$root/deployed-tag" ] && prev=$(tr -d '[:space:]' < "$root/deployed-tag")

# ---- images: GHCR login on stdin, pull, logout. The login lives in a private, throw-away
# DOCKER_CONFIG so root's shared ~/.docker/config.json (other services' registry logins) is never
# changed, and an interrupted run leaves no token behind.
docker_config=$(mktemp -d)
trap 'rm -rf "$docker_config"' EXIT
export DOCKER_CONFIG="$docker_config"
if [ -n "$ghcr_token" ]; then
  printf '%s' "$ghcr_token" | "$docker" login "$registry" -u "${ghcr_user:-token}" --password-stdin >/dev/null \
    || die "docker login $registry failed"
else
  say "no GHCR_TOKEN received; pulling without a login"
fi
pull_ok=1
"$docker" pull "$image_prefix/lf-manager:$tag" >/dev/null || pull_ok=0
"$docker" pull "$image_prefix/lf-lab-term:latest" >/dev/null || pull_ok=0
[ -z "$ghcr_token" ] || "$docker" logout "$registry" >/dev/null 2>&1 || true
unset DOCKER_CONFIG
rm -rf "$docker_config"
[ "$pull_ok" = 1 ] || die "image pull failed"

# ---- the nginx network and nginx's address on it (the manager trusts that one proxy)
net_line=$("$docker" inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{$v.IPAddress}}{{"\n"}}{{end}}' nginx-proxy3 \
  | grep -v '^bridge ' | grep -v '^$') || true
[ "$(printf '%s
' "$net_line" | grep -c .)" -le 1 ]   || die "nginx-proxy3 is on more than one user network; refusing to guess which one the manager joins"
nginx_net=${net_line%% *}
[ -n "$nginx_net" ] && [ "$nginx_net" != "$net_line" ] \
  || die "cannot read the network of nginx-proxy3"
export LF_MANAGER_TAG="$tag" LF_NGINX_NETWORK="$nginx_net" LF_TRUSTED_PROXY=nginx-proxy3
export LF_DOCKER="$docker"
say "nginx network $nginx_net, trusted proxy nginx-proxy3 (the manager re-resolves it)"

compose_up() {
  LF_MANAGER_TAG="$1" "$docker" compose -p learning-factory -f "$root/compose.yml" up -d
}

# wait_for <description> <command...>: retry for LF_HEALTH_TIMEOUT seconds
wait_for() {
  local what=$1; shift
  local deadline=$((SECONDS + timeout))
  until "$@" >/dev/null 2>&1; do
    [ "$SECONDS" -lt "$deadline" ] || { say "timed out waiting for: $what"; return 1; }
    sleep 1
  done
}

manager_healthy() {
  [ "$("$docker" inspect -f '{{.State.Running}}' lf-manager)" = true ] \
    && "$docker" exec lf-manager python -c \
      "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).read()"
}

default_gateway() {
  local out
  out=$("$docker" exec lf-manager ip route show default 2>/dev/null) || out=""
  if [ -z "$out" ]; then
    # python:slim has no `ip`: read the kernel table instead
    out=$("$docker" exec lf-manager python -c "
import socket, struct
for row in open('/proc/net/route').read().splitlines()[1:]:
    f = row.split()
    if f[1] == '00000000':
        print('default via ' + socket.inet_ntoa(struct.pack('<L', int(f[2], 16))))
        break
" 2>/dev/null) || out=""
  fi
  printf '%s\n' "$out" | awk '{ for (i = 1; i <= NF; i++) if ($i == "via") { print $(i + 1); exit } }'
}

# Container-level checks; a failure here rolls back to the previous tag.
verify_container() {
  wait_for "lf-manager to answer /healthz" manager_healthy || return 1
  local gw want labs_gw
  want=$("$docker" network inspect "$nginx_net" --format '{{range .IPAM.Config}}{{.Gateway}} {{end}}' | awk '{print $1}')
  labs_gw=$("$docker" network inspect lf-labs --format '{{range .IPAM.Config}}{{.Gateway}} {{end}}' | awk '{print $1}')
  gw=$(default_gateway)
  if [ -z "$gw" ] || [ "$gw" = "$labs_gw" ] || [ "$gw" != "$want" ]; then
    say "the manager's default route is via '${gw:-none}', expected the nginx network gateway $want"
    return 1
  fi
  local mig
  # Only this container start's logs (a same-tag re-run must not depend on rotated old logs).
  local started
  started=$("$docker" inspect -f '{{.State.StartedAt}}' lf-manager 2>/dev/null) || started=""
  mig=$("$docker" logs ${started:+--since "$started"} lf-manager 2>&1 | grep 'migrations applied' | tail -n1) || mig=""
  if [ -z "$mig" ]; then
    say "no 'migrations applied' line in the manager logs"
    return 1
  fi
  say "$mig"
  # best effort: the address the manager resolved for the proxy name
  local plines
  plines=$("$docker" logs ${started:+--since "$started"} lf-manager 2>&1 | grep "trusted proxy") || plines=""
  case "$plines" in
    *"trusted proxy: startup lookup raised unexpectedly"*)
      say "WARNING: trusted proxy: the manager's startup lookup raised unexpectedly; see its logs"
      ;;
  esac
  printf '%s\n' "$plines" | grep . | tail -n1 \
    | while read -r l; do
        say "$l"
        case "$l" in
          *"(none)"*|*"lookup failed"*)
            pname=${l#*trusted proxy }; pname=${pname%%[ :]*}
            say "WARNING: trusted proxy $pname did not resolve; logins are rate-limited per proxy, not per client"
            ;;
        esac
      done || true
}

say "starting lf-manager:$tag"
compose_up "$tag"
if ! verify_container; then
  if [ -n "$prev" ] && [ "$prev" != "$tag" ]; then
    say "rolling back to $prev"
    compose_up "$prev" || true
    wait_for "the previous lf-manager to answer /healthz" manager_healthy || say "the previous tag is not healthy either"
  else
    say "no previous tag recorded; nothing to roll back to"
  fi
  die "deploy of $tag failed its health check"
fi

# ---- nginx blocks (validated and reloaded by the installer, never restarted)
bash "$root/nginx/install-block.sh" labs "$root/nginx/labs.conf"
bash "$root/nginx/install-block.sh" factory "$root/nginx/factory.conf"

# ---- live checks through nginx on this host
labs_get() { curl -fsS --max-time 10 --resolve "$labs_host:443:127.0.0.1" "https://$labs_host$1"; }
check_healthz() { labs_get /healthz | grep -Eq '"ok": *true'; }
check_database() { labs_get /api/health | grep -Eq '"database": *"ok"'; }
check_live_json() {
  curl -sI --max-time 10 --resolve "$factory_host:443:127.0.0.1" "https://$factory_host/live.json" \
    | tr -d '\r' | grep -iq '^cache-control:.*no-cache'
}
wait_for "https://$labs_host/healthz" check_healthz || die "live check failed: /healthz"
wait_for "database ok in https://$labs_host/api/health" check_database || die "live check failed: /api/health does not report database ok"
wait_for "no-cache on https://$factory_host/live.json" check_live_json || die "live check failed: /live.json has no Cache-Control: no-cache"

printf '%s\n' "$tag" > "$root/deployed-tag"
say "deployed lf-manager:$tag; all checks passed"

# ---- prune our old images (only after every check above passed; never fatal)
# Removes lf-manager tags other than the deployed one and the previous one (rollback target), and
# dangling images whose repository is under $image_prefix/. Nothing else is ever named: no
# `system prune`, no unfiltered `image prune`, no --force (an image in use simply stays).
pruned=0
prune_one() { # prune_one <ref to remove> <what to show in the log>
  if "$docker" rmi "$1" >/dev/null 2>&1; then
    pruned=$((pruned + 1))
  else
    say "skip: could not remove $2 (in use or already gone)"
  fi
}
prune_images() {
  local repo itag iid
  while read -r repo itag iid; do
    [ "$repo" = "$image_prefix/lf-manager" ] || continue
    [ -n "$itag" ] && [ "$itag" != "<none>" ] || continue
    [ "$itag" != "$tag" ] && [ "$itag" != "$prev" ] || continue
    prune_one "$repo:$itag" "$repo:$itag"
  done < <("$docker" images --filter "reference=$image_prefix/lf-manager" --format '{{.Repository}} {{.Tag}} {{.ID}}' 2>/dev/null || true)
  while read -r repo itag iid; do
    case "$repo" in "$image_prefix"/*) ;; *) continue ;; esac
    [ -n "$iid" ] || continue
    prune_one "$iid" "dangling $repo $iid"
  done < <("$docker" images --filter dangling=true --format '{{.Repository}} {{.Tag}} {{.ID}}' 2>/dev/null || true)
}
prune_images || true
say "pruned $pruned old image(s)"
# Machine-readable proof of a real apply (the workflow runs the smoke only when it sees this line).
printf 'deployed %s\n' "$tag"
