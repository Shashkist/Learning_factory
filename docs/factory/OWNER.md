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
- [ ] **O5 · (optional) Copy the labs nginx block** from `labs/deploy/nginx/labs.conf` into `EMEW-AI/Digital Ocean/default.conf`, so that repo's copy matches the server (see D3).

## Questions

_None yet._ When the factory meets something only Ilia can answer, it lands here with the issue link and the assumption it made meanwhile.
