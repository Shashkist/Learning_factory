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

- [x] **O6 · Claude Code sign-in for the laptop runner** — **owner decision 2026-11-15: no long-lived token; the owner signs in again (`claude auth login`) when asked.** The current sign-in ends between 2026-11-20 and 2026-11-23. When a pass fails because the sign-in has expired, the runner shows a Windows alert "Learning Factory: sign in again" and the factory waits until then.
- [ ] **O7 · (optional) GitHub Actions minutes.** The repo is private on the free plan: 2,000 Actions minutes a month for CI, deploys, smoke tests and the dashboard. The factory keeps the dashboard to push-to-main plus every 3 hours (D12), but a busy month of CI may still reach the limit, and then jobs stop until the month ends. Options: make the repo public (`gh repo edit Shashkist/Learning_factory --visibility public --accept-visibility-change-consequences`; check that nothing private is in it first, e.g. `courses/`), or set a spending limit in GitHub → Settings → Billing. Tell any session "O7: public" and the dashboard goes back to every 15 minutes.

- [x] **O8 · Move the server deploy secrets into the `production` environment** — **done 2026-11-14 (both steps; publish verified with the new key)**. Step 1 done 2026-11-14 by `scripts/owner/setup-production-env.sh` (owner pre-approval D7): env secrets `SSH_PRIVATE_KEY` (new key `lf-deploy-prod`), `SERVER_HOST`, `SERVER_USER` are set; the old repo-level key still works. **Step 2, after #25 merges:** `bash scripts/owner/setup-production-env.sh --finish` (deletes the repo-level secrets and the old `lf-deploy` key on the server). Original note: (issue #25, D15). Only workflow runs on `main` can then read the root SSH key. After the factory has created the environment (it will note that on #25), run:
  ```bash
  gh secret set SSH_PRIVATE_KEY --env production -R Shashkist/Learning_factory < path/to/deploy_key
  gh secret set SERVER_HOST     --env production -R Shashkist/Learning_factory
  gh secret set SERVER_USER     --env production -R Shashkist/Learning_factory
  gh secret delete SSH_PRIVATE_KEY -R Shashkist/Learning_factory
  gh secret delete SERVER_HOST     -R Shashkist/Learning_factory
  gh secret delete SERVER_USER     -R Shashkist/Learning_factory
  ```
  Do the deletes only after #25 is merged (the workflows then use `environment: production`); until then nothing changes.

- [ ] **O10 · Move `LF_DATABASE_URL` into the `production` environment** (from the review of #63, same reason as O8: a repo-level secret is readable by a workflow on any branch). The only workflow that uses it (`db-check.yml`, #61) already runs in `production`; #13's deploy will too. Set it from the same place you set it for O3, then delete the repo-level copy:
  ```bash
  gh secret set LF_DATABASE_URL --env production -R Shashkist/Learning_factory   # paste the value when prompted
  gh secret delete LF_DATABASE_URL -R Shashkist/Learning_factory
  ```
  Until then nothing breaks. The factory verifies by behaviour (the next `DB check` run still connects), never by reading the value.

- [x] **O9 · What `lf_app` can reach through `PUBLIC`** — **handed to the manager by the owner (2026-11-15): issue #61.** The original note and query are kept below for the manager.
  The role sees tables only in `learning_factory`, but like every role it inherits `PUBLIC`'s rights: it can call functions in schema `public` and read the catalog. A `SECURITY DEFINER` function in `public` would run with its owner's rights. Check:
  ```sql
  select p.oid::regprocedure as fn, p.prosecdef as security_definer
  from pg_proc p join pg_namespace n on n.oid = p.pronamespace
  where n.nspname = 'public' and has_function_privilege('lf_app', p.oid, 'EXECUTE')
  order by 2 desc, 1;
  ```

## Questions

_None yet._ When the factory meets something only Ilia can answer, it lands here with the issue link and the assumption it made meanwhile.
