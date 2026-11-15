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
- [x] **O5 · Copy the labs nginx block** into `EMEW-AI/Digital Ocean/default.conf` — **done 2026-11-15 through that repo (commit 0f4dbc3): the file is now byte-identical to the server's live `default.conf` with the real `labs` and `factory` blocks; its pipeline redeployed it (nginx restarted, address unchanged).** Repeat when the factory changes a block: copy the server's file, never hand-merge (D3).

- [x] **O6 · Claude Code sign-in for the laptop runner** — **owner decision 2026-11-15: no long-lived token; the owner signs in again (`claude auth login`) when the runner's Windows alert asks. Do not remind the owner about the expiry date in reports.** When a pass fails because the sign-in has expired, the runner shows a Windows alert "Learning Factory: sign in again" and the factory waits until then.
- [x] **O7 · GitHub Actions minutes** — **resolved 2026-11-15: the repository is public (D43), so GitHub-hosted Actions minutes are free.**

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

- [x] **O10 · Move `LF_DATABASE_URL` into the `production` environment** — **done 2026-11-15: copied unchanged from the server's `.env` into the `production` environment (no rotation, the running manager untouched), repo-level copy deleted; `DB check` run 43722854463 connected with the environment copy only.** Original note (from the review of #63, same reason as O8): a repo-level secret is readable by a workflow on any branch. For reference, the manual way:
  ```bash
  gh secret set LF_DATABASE_URL --env production -R Shashkist/Learning_factory   # paste the value when prompted
  gh secret delete LF_DATABASE_URL -R Shashkist/Learning_factory
  ```
  Until then nothing breaks. The factory verifies by behaviour (the next `DB check` run still connects), never by reading the value.

- [x] **O11 · First admin** — **replaced 2026-11-15 by owner decision: admins sign in with Google, allow-list only (#92, see O14). No password admin is created; `create-admin` stays as break-glass.** The old instructions, for break-glass only:
  ```bash
  docker exec -i lf-manager python -m app.cli create-admin <username>
  ```
  Type the password when prompted (or pipe it: `printf '%s\n' "$PW" | docker exec -i lf-manager python -m app.cli create-admin <username>`). To change it later add `--reset`. The deploy needs the production environment to hold `SSH_PRIVATE_KEY`, `SERVER_HOST`, `SERVER_USER`, `LF_DATABASE_URL` (O10) and the variable `SERVER_HOST_KEY`; without them it prints which are missing and does nothing.
- [x] **O12 · Back up `/root/learning-factory/.env`** — **owner decision 2026-11-15: skipped. If the file is ever lost, students re-register ("we will remake it").** Do not ask again; a factory change that would make the loss worse (e.g. more secrets in that file) must say so in its PR. Original note: the first deploy generates `LF_ID_SECRET` there; every student ID hash depends on it.
- [x] **O13 · nginx-proxy3 recreated → re-run Deploy** — **owner: "you decide"; decided 2026-11-15: removed by making it self-healing (#93). Until #93 ships:** after recreating `nginx-proxy3`, re-run the Deploy workflow (a plain restart keeps the address, checked 2026-11-15).

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
- [x] **O14 · Own Google sign-in client for labs (for #92)** — **done 2026-11-15** (owner + a session in Chrome). Google Cloud project `learning-factory-514815` ("Learning Factory"); consent screen app name **Learning Factory Labs**, authorized domain `shashkist.com`, audience External but **kept in Testing on purpose**: the only test user is the owner, so Google itself refuses every other account before labs sees it (a second allow-list next to `LF_ADMIN_EMAILS`). Publishing would need a privacy-policy page; do that only if non-admins ever sign in with Google. Web client **labs**: origin `https://labs.shashkist.com`, redirect `https://labs.shashkist.com/auth/google/callback`. `LF_GOOGLE_CLIENT_ID`/`LF_GOOGLE_CLIENT_SECRET` set in the `production` environment from the downloaded JSON (never printed; the file was deleted). Scopes: `openid email profile` only. Google notes that a new client can take from five minutes to a few hours to work.
- [x] **O15 · Admin allow-list is a secret** — **done 2026-11-15 15:36 UTC: `LF_ADMIN_EMAILS` set as a `production` secret (piped, not echoed), the variable deleted.** No Deploy had read the variable yet (#95 not merged), so the address never reached a log.
- [x] **O16 · GitHub Actions minutes were used up (incident #96)** — **resolved 2026-11-15 by making the repository public (D43).** Kept for the record: 
  1. **Spending limit** (keeps the repo private): GitHub → Settings → Billing and licensing → Budgets and alerts → Actions budget, e.g. $10 for the month (Linux runners cost $0.006/min, so $10 ≈ 1,600 min). **Size it knowing the current rate:** a busy factory day used ~900 min (~$5); #98 cuts that (docs-only pushes skip CI, heavy jobs only when their paths change). If the cause is really a failed payment, fix the card under Payment information first.
  2. **Make the repo public** (Actions become free; check `courses/` and the docs for anything private first). This is O7's option; the command is in O7.
  3. **Wait** for the reset on 2026-12-01 (16 days without merges or deploys).
  Then tick this box (or tell any session "done: O16"); the next pass re-runs main CI first.
- [ ] **O17 · Read Linux 1 (en + he) and publish it** — #12. The lab is in `labs/content/linux/linux-1/` with `status: draft` (students do not see it). Please read the ten tasks in English and Hebrew (wording, difficulty, whether the Hebrew sounds natural). When you are happy, set `status: published` in its `lab.yaml` (or tell any session "publish linux-1") and tick this box. The ownership part uses `chgrp` and a quiz instead of `sudo chown` (D42).
