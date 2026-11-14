#!/usr/bin/env bash
# Install or replace one marked nginx block in the shared default.conf, idempotently (D3).
#   install-block.sh <name> <file>
# <file> holds one block: "# BEGIN learning-factory:<name>" ... "# END learning-factory:<name>".
# Text outside the markers is left byte-identical. Flow: lock -> build the new file -> no change? exit
# -> backup -> write in place (same inode: the conf is bind-mounted) -> `nginx -t` -> reload.
# Until `nginx -t` passes, any exit (error, signal) restores the backup. Never restarts anything.
# Env: LF_NGINX_CONF (default /root/nginx/conf.d/default.conf), LF_DOCKER (default docker).
set -euo pipefail

name=${1:?usage: install-block.sh <name> <file>}
file=${2:?usage: install-block.sh <name> <file>}
conf=${LF_NGINX_CONF:-/root/nginx/conf.d/default.conf}
docker=${LF_DOCKER:-docker}
begin="# BEGIN learning-factory:${name}"
end="# END learning-factory:${name}"
keep_backups=5

die() { echo "install-block: $*" >&2; exit 2; }

[[ "$name" =~ ^[a-z0-9-]+$ ]] || die "bad block name: $name"
[ -f "$file" ] || die "block file not found: $file"
[ -f "$conf" ] || die "nginx conf not found: $conf"

# One installer at a time (flock exists on the server; it is optional for local test runs).
if command -v flock >/dev/null 2>&1; then
  exec 9>"${conf}.lock"
  flock -w 60 9 || die "another install-block.sh holds ${conf}.lock"
fi

count() { grep -cxF -- "$2" "$1" || true; }
# Marker lines that match only after stripping CR and leading/trailing blanks: refuse instead of guessing.
loose() { tr -d '\r' < "$1" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | grep -cxF -- "$2" || true; }

[ "$(count "$file" "$begin")" = 1 ] && [ "$(count "$file" "$end")" = 1 ] \
  || die "block file must contain exactly one '$begin' and one '$end' line"
[ "$(head -n1 "$file")" = "$begin" ] || die "block file must start with '$begin'"
[ "$(tail -n1 "$file" | tr -d '\n')" = "$end" ] || die "block file must end with '$end'"
grep -q $'\r' "$file" && die "block file has CRLF line endings"

nb=$(count "$conf" "$begin")
ne=$(count "$conf" "$end")
[ "$(loose "$conf" "$begin")" = "$nb" ] && [ "$(loose "$conf" "$end")" = "$ne" ] \
  || die "conf has '$name' marker lines with CR or trailing blanks; fix them by hand first"
[ "$nb" = "$ne" ] && [ "$nb" -le 1 ] || die "conf has unbalanced or duplicate markers for '$name'"
if [ "$nb" = 1 ]; then
  lb=$(grep -nxF -- "$begin" "$conf" | cut -d: -f1)
  le=$(grep -nxF -- "$end" "$conf" | cut -d: -f1)
  [ "$lb" -lt "$le" ] || die "conf has '$end' before '$begin'"
fi

tmp=$(mktemp "${conf}.new.XXXXXX")
backup=""
written=0
validated=0
cleanup() {
  rc=$?
  rm -f "$tmp" "${tmp}.a" "${tmp}.b" "${conf}.lf-partial.$$"
  # Restore only if the live conf was (possibly partly) overwritten, and only from a complete backup.
  if [ "$written" = 1 ] && [ "$validated" = 0 ]; then
    if cat "$backup" > "$conf"; then
      echo "install-block: original restored from $backup" >&2
    else
      echo "install-block: RESTORE FAILED: copy $backup back to $conf by hand" >&2
    fi
    [ "$rc" = 0 ] && rc=1
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 1' INT HUP TERM

# Values go through the environment: awk -v would interpret backslashes in paths.
outside() { B="$begin" E="$end" awk '$0 == ENVIRON["B"] { s = 1; next } s && $0 == ENVIRON["E"] { s = 0; next } !s' "$1"; }

if [ "$nb" = 1 ]; then
  B="$begin" E="$end" F="$file" awk '
    $0 == ENVIRON["B"] && !s { while ((getline l < ENVIRON["F"]) > 0) print l; close(ENVIRON["F"]); s = 1; next }
    s { if ($0 == ENVIRON["E"]) s = 0; next }
    { print }
  ' "$conf" > "$tmp"
  # Everything outside this block must be unchanged.
  outside "$conf" > "${tmp}.a"
  outside "$tmp" > "${tmp}.b"
  cmp -s "${tmp}.a" "${tmp}.b" || die "refusing: the edit would change text outside the '$name' block"
else
  cat "$conf" > "$tmp"
  [ -z "$(tail -c1 "$conf")" ] || printf '\n' >> "$tmp"
  cat "$file" >> "$tmp"
  [ -z "$(tail -c1 "$file")" ] || printf '\n' >> "$tmp"
fi

if cmp -s "$tmp" "$conf"; then
  echo "block '$name' unchanged; nothing to do"
  exit 0
fi

# Our backups carry an lf- prefix, so pruning never touches anyone else's. The backup is complete
# (copied, then renamed) before the live conf is touched.
cp -p "$conf" "${conf}.lf-partial.$$"
backup="${conf}.lf-$(date +%Y%m%d%H%M%S).$$.bak"
mv "${conf}.lf-partial.$$" "$backup"
written=1
cat "$tmp" > "$conf"

if ! "$docker" exec nginx-proxy3 nginx -t; then
  echo "install-block: nginx -t failed" >&2
  exit 1   # the EXIT trap restores the backup
fi
validated=1

# Keep the newest backups only (names sort by timestamp). Pruning never fails the install.
{ find "$(dirname "$conf")" -maxdepth 1 -type f -name "$(basename "$conf").lf-*.bak" | sort -r \
    | tail -n +$((keep_backups + 1)) | while IFS= read -r old; do rm -f -- "$old"; done; } || true

if ! "$docker" exec nginx-proxy3 nginx -s reload; then
  echo "install-block: config is valid and in place, but the reload failed; nginx still serves the old config (backup: $backup)" >&2
  exit 1
fi
echo "installed block '$name' (backup: $backup)"
