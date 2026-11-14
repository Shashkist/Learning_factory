#!/usr/bin/env bash
# One-time, re-runnable setup of the Learning Factory's GitHub secrets and DNS records.
# Approved by the owner in advance (docs/factory/DECISIONS.md, D7).
#
# Every value goes from its source straight into `gh secret set` (stdin) or an API call.
# Nothing is echoed, logged or written to the repo. Output shows only names and "set".
#
# Run from Git Bash on the owner's laptop:  bash scripts/owner/setup-secrets.sh
set -euo pipefail

REPO="Shashkist/Learning_factory"
SERVER="root@207.154.193.238"
SERVER_IP="207.154.193.238"
DO_ENV="/c/projects/EMEW-AI/Digital Ocean/terraform/.env"
DNS_NAMES=(labs factory)          # labs.shashkist.com, factory.shashkist.com (observability)

ok()   { printf '  \xe2\x9c\x93 %s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }

step "Server address"
printf '%s' "$SERVER_IP" | gh secret set SERVER_HOST -R "$REPO" >/dev/null && ok "SERVER_HOST set"
printf '%s' "root"       | gh secret set SERVER_USER -R "$REPO" >/dev/null && ok "SERVER_USER set"

step "Deploy key (new, only for this repo; comment lf-deploy)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
ssh-keygen -q -t ed25519 -N "" -C "lf-deploy" -f "$tmp/lf_deploy" >/dev/null
# Replace any previous lf-deploy key on the server, then add the new public key.
# Safety: the swap happens only if every non-lf-deploy key survives, so owner access can never be lost.
ssh -o BatchMode=yes "$SERVER" 'f=/root/.ssh/authorized_keys; cp "$f" "$f.bak-lf" && { grep -v " lf-deploy$" "$f.bak-lf" || true; cat; } > "$f.tmp" && [ "$(grep -vc " lf-deploy$" "$f.tmp")" -eq "$(grep -vc " lf-deploy$" "$f.bak-lf")" ] && [ "$(grep -c " lf-deploy$" "$f.tmp")" -eq 1 ] && chmod 600 "$f.tmp" && mv "$f.tmp" "$f" || { echo "authorized_keys left unchanged" >&2; exit 1; }' < "$tmp/lf_deploy.pub"
gh secret set SSH_PRIVATE_KEY -R "$REPO" < "$tmp/lf_deploy" >/dev/null
rm -f "$tmp/lf_deploy" "$tmp/lf_deploy.pub"
ok "SSH_PRIVATE_KEY set, public half installed on the server (old lf-deploy key replaced)"

step "DNS A records in DigitalOcean (shashkist.com)"
DO_TOKEN="$(grep '^TF_VAR_do_token=' "$DO_ENV" | cut -d= -f2- | tr -d '"\r')"
for name in "${DNS_NAMES[@]}"; do
  exists="$(curl -sf -H "Authorization: Bearer $DO_TOKEN" \
    "https://api.digitalocean.com/v2/domains/shashkist.com/records?type=A&name=$name.shashkist.com" \
    | python -c 'import sys,json; print(len(json.load(sys.stdin)["domain_records"]))')"
  if [ "$exists" = "0" ]; then
    curl -sf -X POST -H "Authorization: Bearer $DO_TOKEN" -H "Content-Type: application/json" \
      -d "{\"type\":\"A\",\"name\":\"$name\",\"data\":\"$SERVER_IP\",\"ttl\":3600}" \
      "https://api.digitalocean.com/v2/domains/shashkist.com/records" >/dev/null
    ok "$name.shashkist.com -> $SERVER_IP created"
  else
    ok "$name.shashkist.com already exists (left as is)"
  fi
done
unset DO_TOKEN

step "Database user lf_app + schema learning_factory (Supabase management API, run on the server)"
# The server holds the management token for the project. It creates or rotates the role with a
# fresh password and prints ONLY the connection URL on stdout, which goes straight into gh.
ssh -o BatchMode=yes "$SERVER" 'bash -s' <<'REMOTE' | gh secret set LF_DATABASE_URL -R "$REPO" >/dev/null
set -euo pipefail
TOKEN="$(grep "^SUPABASE_ACCESS_TOKEN_SPB=" /root/.env | cut -d= -f2- | tr -d "\"\r")"
REF="$(grep "^SUPABASE_PROJECT_REF=" /root/.env | cut -d= -f2- | tr -d "\"\r")"
PW="$(openssl rand -hex 24)"
SQL="do \$\$ begin
  if not exists (select 1 from pg_roles where rolname = 'lf_app') then
    execute format('create role lf_app login password %L', '$PW');
  else
    execute format('alter role lf_app login password %L', '$PW');
  end if;
end \$\$;
create schema if not exists learning_factory;
grant usage, create on schema learning_factory to lf_app;
revoke all on schema public from lf_app;
alter role lf_app set search_path = learning_factory;"
python3 - "$SQL" > /tmp/lf_q.json <<'PY'
import json, sys; print(json.dumps({"query": sys.argv[1]}))
PY
curl -sf -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  --data @/tmp/lf_q.json "https://api.supabase.com/v1/projects/$REF/database/query" >/dev/null
rm -f /tmp/lf_q.json
HOST="$(curl -sf -H "Authorization: Bearer $TOKEN" "https://api.supabase.com/v1/projects/$REF/config/database/pooler" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); d=d[0] if isinstance(d,list) else d; print(d["db_host"])')"
URL="postgresql://lf_app.$REF:$PW@$HOST:6543/postgres"
# Verify by behaviour: log in as lf_app and report who and where we are (to stderr, never the URL).
sleep 5
RESULT="$(docker run --rm postgres:16-alpine psql "$URL" -tAc "select current_user || ' / ' || current_schema()" 2>/dev/null || echo "LOGIN FAILED")"
echo "  login check: $RESULT" >&2
printf '%s' "$URL"
REMOTE
ok "LF_DATABASE_URL set (role lf_app, schema learning_factory, transaction pooler)"

step "Secrets now in $REPO"
gh secret list -R "$REPO" | awk '{print "  " $1}'
echo; echo "OpenRouter is intentionally not installed (owner: not needed for now)."
