#!/usr/bin/env bash
# O8 (issue #25, D15): deploy secrets live in the `production` environment, readable only by runs on main.
# Owner pre-approval: D7. Values are piped, never printed.
#
#   bash scripts/owner/setup-production-env.sh            # step 1: new key + env secrets (old key keeps working)
#   bash scripts/owner/setup-production-env.sh --finish   # step 2 (after #25 merged): remove the repo-level key
set -euo pipefail
REPO="Shashkist/Learning_factory"; SERVER="root@207.154.193.238"; SERVER_IP="207.154.193.238"
ok() { printf '  \xe2\x9c\x93 %s\n' "$*"; }

if [ "${1:-}" = "--finish" ]; then
  gh secret delete SSH_PRIVATE_KEY -R "$REPO" >/dev/null 2>&1 && ok "repo-level SSH_PRIVATE_KEY deleted" || ok "repo-level SSH_PRIVATE_KEY already gone"
  for s in SERVER_HOST SERVER_USER; do gh secret delete "$s" -R "$REPO" >/dev/null 2>&1 && ok "repo-level $s deleted" || true; done
  ssh -o BatchMode=yes "$SERVER" 'f=/root/.ssh/authorized_keys; cp "$f" "$f.bak-lf" && { grep -v " lf-deploy$" "$f.bak-lf" || true; } > "$f.tmp" && [ "$(grep -c " lf-deploy-prod$" "$f.tmp")" -eq 1 ] && [ "$(grep -vc " lf-deploy" "$f.tmp")" -eq "$(grep -vc " lf-deploy" "$f.bak-lf")" ] && chmod 600 "$f.tmp" && mv "$f.tmp" "$f" || { echo "authorized_keys left unchanged" >&2; exit 1; }'
  ok "old lf-deploy key removed from the server (lf-deploy-prod and owner keys kept)"
  exit 0
fi

# The environment must exist and be limited to main (created by the factory for #25).
gh api "repos/$REPO/environments/production/deployment-branch-policies" --jq '[.branch_policies[].name] | index("main")' | grep -q . \
  || { echo "production environment is not limited to main yet; stopping" >&2; exit 1; }
ok "production environment exists and is limited to main"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
ssh-keygen -q -t ed25519 -N "" -C "lf-deploy-prod" -f "$tmp/k" >/dev/null
ssh -o BatchMode=yes "$SERVER" 'f=/root/.ssh/authorized_keys; cp "$f" "$f.bak-lf" && { grep -v " lf-deploy-prod$" "$f.bak-lf" || true; cat; } > "$f.tmp" && [ "$(grep -vc " lf-deploy-prod$" "$f.tmp")" -eq "$(grep -vc " lf-deploy-prod$" "$f.bak-lf")" ] && [ "$(grep -c " lf-deploy-prod$" "$f.tmp")" -eq 1 ] && chmod 600 "$f.tmp" && mv "$f.tmp" "$f" || { echo "authorized_keys left unchanged" >&2; exit 1; }' < "$tmp/k.pub"
gh secret set SSH_PRIVATE_KEY --env production -R "$REPO" < "$tmp/k" >/dev/null
rm -f "$tmp/k" "$tmp/k.pub"
printf '%s' "$SERVER_IP" | gh secret set SERVER_HOST --env production -R "$REPO" >/dev/null
printf '%s' "root"       | gh secret set SERVER_USER --env production -R "$REPO" >/dev/null
ok "production: SSH_PRIVATE_KEY (new key lf-deploy-prod), SERVER_HOST, SERVER_USER set"
echo "  The repo-level key still works until --finish runs (after #25 merges)."
gh secret list --env production -R "$REPO" | awk '{print "  env: " $1}'
