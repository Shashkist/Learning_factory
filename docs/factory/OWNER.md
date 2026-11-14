# Waiting for the owner

The factory never waits on these. It works around them and comes back when they are ticked. Tick a box (`[x]`) and push, or tell any session "done: O2".

## Actions

> 2026-11-14: O1–O3 done by `scripts/owner/setup-secrets.sh` (owner pre-approval D7): `labs` and `factory` A records, a new repo-only deploy key, and role `lf_app` with schema `learning_factory` (login verified). O4 skipped by the owner's choice.

- [x] **O1 · DNS record for `labs.shashkist.com`.** DigitalOcean → Networking → Domains → `shashkist.com` → add an **A record**: hostname `labs`, value `207.154.193.238`, TTL 3600. (Or add a `digitalocean_record` in `EMEW-AI/Digital Ocean/terraform/main.tf` and `terraform apply`.) Until then the factory tests through the server IP with a `Host:` header; the certificate step (D4) waits for this.
- [x] **O2 · Deploy secrets for this repo** (the same values e-sign uses). Run from a shell that has them:
  ```bash
  gh secret set SERVER_HOST     -R Shashkist/Learning_factory   # paste the value when prompted
  gh secret set SERVER_USER     -R Shashkist/Learning_factory
  gh secret set SSH_PRIVATE_KEY -R Shashkist/Learning_factory < path/to/deploy_key
  ```
- [x] **O3 · Database access for the new schema.** In Supabase (the JSoul project) → SQL editor, run the script the factory writes to `labs/deploy/db/001-create-schema-and-role.sql` **once it exists** (it creates schema `learning_factory` and a role that can use only that schema). Then store the role's connection string (pooler, port 6543):
  ```bash
  gh secret set LF_DATABASE_URL -R Shashkist/Learning_factory
  ```
- [ ] **O4 · (optional, skipped for now) Second-opinion reviewer.** `gh secret set OPENROUTER_API_KEY -R Shashkist/Learning_factory`. Without it, the cross-company PR review is skipped and the PRs say so.
- [x] **O5 · (optional) Copy the labs nginx block** (done 2026-11-14 for the placeholder blocks; repeat when the factory changes them) from `labs/deploy/nginx/labs.conf` into `EMEW-AI/Digital Ocean/default.conf`, so that repo's copy matches the server (see D3).

- [ ] **O6 · Before 2026-11-20: a long-lived Claude Code token for the laptop runner.** Claude Code warned: "this sign-in ends between 2026-11-20 and 2026-11-23 … use claude setup-token for unattended jobs". In a terminal:
  ```powershell
  claude setup-token        # sign in in the browser; it prints a token
  [Environment]::SetEnvironmentVariable('CLAUDE_CODE_OAUTH_TOKEN', '<paste token>', 'User')
  ```
  The scheduled task inherits user environment variables. Without this, passes fail after that date (the watchdog alerts "no progress").
- [ ] **O7 · (optional) GitHub Actions minutes.** The repo is private on the free plan: 2,000 Actions minutes a month for CI, deploys, smoke tests and the dashboard. The factory keeps the dashboard to push-to-main plus every 3 hours (D12), but a busy month of CI may still reach the limit, and then jobs stop until the month ends. Options: make the repo public (`gh repo edit Shashkist/Learning_factory --visibility public --accept-visibility-change-consequences`; check that nothing private is in it first, e.g. `courses/`), or set a spending limit in GitHub → Settings → Billing. Tell any session "O7: public" and the dashboard goes back to every 15 minutes.

## Questions

_None yet._ When the factory meets something only Ilia can answer, it lands here with the issue link and the assumption it made meanwhile.
