# Decisions and assumptions

**When two documents disagree, this file settles it.** Every entry is revertible: say "revert D7" (in an issue or a session) and the factory reverses it, updates the spec and the affected issues, and records the reversal here **in place** (never delete an entry).

Format: **D<n> · title** · status · who decided · date, then what was decided, why, alternatives, and how to revert.

Owner decisions (from the brainstorm, 2026-11-14) are in the spec, §3. This file holds the factory's own decisions and assumptions.

---

## D1 · Factory runtime: hourly cloud routine plus "continue" in any session
**amended 2026-11-14 by the owner: laptop-only for now** · factory (initial setup) · 2026-11-14

> **Amendment (owner, 2026-11-14):** the only Claude account available in the cloud is the owner's company organisation, so the cloud routine is **not** used for this personal project. The factory runs on the laptop instead: the Windows task `LearningFactoryPoll` (at logon + every 30 min) starts one headless manager pass (`claude -p --model claude-opus-5-5 --permission-mode auto`) in the worktree `C:\projects\Learning_factory-factory` when there is work, with a daily budget of 10 passes and a PAUSE file. The cloud routine can be added later from a personal Claude account; nothing else changes. Original text kept below.


**Decision.** The manager runs as a Claude Code cloud routine every hour on Opus 5.5. The same loop runs when Ilia says "continue the factory" in a local session. All memory lives in the repo (`STATE.md`, `DECISIONS.md`, `OWNER.md`) and in GitHub issues, so either path resumes the other.
**Why.** It runs 24/7 without the laptop. The cloud has no SSH or root on the production server, so unattended runs can only change the server through reviewed GitHub Actions workflows.
**Alternatives.** Claude Code headless on the VPS (rejected: an autonomous agent with root on the server that runs OpenClaw); laptop only (rejected: stops when the lid closes).
**Revert.** Disable the routine at claude.ai/code/routines; the "continue" path keeps working.

## D2 · Every server change goes through GitHub Actions over SSH
in force · factory · 2026-11-14

**Decision.** Deploys, bootstrap (gVisor, swap, the `lf-labs` network and its firewall rules, the nginx block, the certificate) and smoke tests are workflows in `.github/workflows/`, using the same secret names as e-sign (`SERVER_HOST`, `SERVER_USER`, `SSH_PRIVATE_KEY`). Scripts are idempotent and validated (`nginx -t`, backup and restore, `systemctl reload docker` only).
**Why.** It is auditable and repeatable, and the same path works for cloud and local runs.
**Revert.** Run the scripts by hand over SSH instead.

## D3 · nginx: a marked block inside the shared `default.conf`
in force · factory · 2026-11-14

**Decision.** `nginx-proxy3` mounts a single `/root/nginx/conf.d/default.conf` for all sites. The labs server block lives in this repo (`labs/deploy/nginx/labs.conf`) and is installed between `# BEGIN learning-factory:<name>` and `# END learning-factory:<name>` markers (amended 2026-11-14: one marked block per site; `http`, `factory` and `labs` already exist on the server) by a script that backs the file up, runs `nginx -t` inside the container, reloads on success and restores the backup on failure. It follows the existing pattern: `set $upstream ...` with `resolver 127.0.0.11`, and WebSocket upgrade headers on `/s/`.
**Why.** Adding a separate include file would mean recreating the nginx container (downtime for every site).
**Note.** The copy of `default.conf` in `EMEW-AI/Digital Ocean` will not contain this block until someone copies it (listed in `OWNER.md`).
**Revert.** Delete the marked block and reload nginx.

## D4 · Certificate: expand the shared cert by webroot only
in force · factory · 2026-11-14

**Decision.** `labs.shashkist.com` is added to the shared `teams.shashkist.com` certificate with certbot `--expand` in **webroot mode only**. Unattended runs never use the standalone fallback of `add_new_domain.sh`, because it stops `nginx-proxy3`. If webroot fails, the workflow fails and opens an incident.
**Revert.** Re-run `add_new_domain.sh` without the domain.

## D5 · Images: built in CI, pushed to GHCR, pulled by the server
in force · factory · 2026-11-14

**Decision.** CI builds `lf-manager`, `lf-lab-term` and `lf-lab-code` and pushes them to `ghcr.io/shashkist/` with the workflow's own `GITHUB_TOKEN`. The deploy step logs the server in to GHCR with that same short-lived token for the pull, then logs out.
**Why.** No registry secret for the owner to install, and no build load on the production server.
**Alternatives.** Docker Hub as in e-sign (needs `DOCKER_*` secrets); building on the server (uses the server's CPU and disk).
**Revert.** Switch the workflow to Docker Hub.

## D6 · The manager joins the network that nginx uses
assumption · factory · 2026-11-14

**Assumption.** `lf-manager` attaches to the same Docker network as `nginx-proxy3` (to be read from `docker inspect` during bootstrap), plus its own `lf-labs` network for lab containers. The nginx upstream is `http://lf-manager:8000`.
**Revert.** Publish the manager on `127.0.0.1:<port>` and point nginx at the host.

## D7 · Owner pre-approval for a hands-off factory
in force · **owner (Ilia)** · 2026-11-14

**Approved in advance by the owner:** (1) the hourly cloud routine running the manager; (2) a Windows scheduled task on the owner's laptop that starts Claude Code headless only when issues carry `needs:laptop`; (3) a one-time, re-runnable script `scripts/owner/setup-secrets.ps1` that installs this repo's GitHub secrets by piping values straight from their sources (never printed or logged): a new repo-only deploy key (public half added to the server), `SERVER_HOST`/`SERVER_USER`, a new database role `lf_app` with a generated password (`LF_DATABASE_URL`), and the `labs` DNS A record through the DigitalOcean API. OpenRouter is **not** installed (the owner is unsure it is needed, so the second-opinion job stays optional and skipped).
**Revert.** Delete the repo secrets, remove the deploy key line from the server's `authorized_keys` (comment `lf-deploy`), disable the routine and the scheduled task.

## D8 · Factory observability at factory.shashkist.com (static, built in CI)
in force · factory, on the owner's request · 2026-11-14

**Decision.** A read-only dashboard of the factory's progress at `https://factory.shashkist.com`, kept light:
- **Data:** a workflow `factory-dashboard.yml` runs every 15 minutes, on pushes to `main`, and on issue and PR events. It builds `snapshot.json` from GitHub: issues by status, phase and priority; the current claim and its step; open and merged PRs; CI, deploy and smoke runs; `STATE.md` (Now / Next / Health); the `DECISIONS.md` titles; open `OWNER.md` items; and throughput (closed per day, lead time, incidents).
- **Page:** one static HTML + JS file reads `snapshot.json`. It looks like the "live factory page" slide in Ilia's deck: stations Plan → Exam → Build → Check → Ship with the current issue on the line, the queue, recently shipped, decisions (revertible), waiting for owner, health, and the server's memory and disk from the latest smoke run.
- **Hosting:** static files copied over SSH to `/root/static/factory/` (already mounted in `nginx-proxy3` as `/var/www/static`). A marked nginx block serves `factory.shashkist.com` (D3 mechanism), and the shared certificate is expanded by webroot (D4). No new container, no server process.
- **Style:** the structure of the guild platform's design system (three token layers palette → semantic → light/dark with `data-theme`, a theme toggle, Poppins, cards), with **its own palette, not a company brand palette** (personal project).
- **Privacy (assumption):** public and `noindex`. It shows only issue titles, statuses and pipeline health, never student data or secrets. **Revert:** add basic auth to the marked block.

## D9 · Exam integrity is checked per commit, not per PR
in force · factory · 2026-11-14

**Decision.** The exam and the code ship in the same PR (one issue = one PR), but never in the same commit. The `exam-integrity` CI job walks the PR's commits and fails when (a) one commit changes both `labs/**/tests/acceptance/**` and non-test code under `labs/`, or (b) a commit changes an acceptance test after the first commit that changed non-test code under `labs/`. So the exam is fixed before the build starts and the build cannot edit it.
**Why.** A PR-level "tests and code together" ban would force a separate exam PR whose tests fail on `main` (red line) until the code lands. Ordering per commit keeps `main` green and still makes the exam tamper-evident. Squash-merge is fine: the check runs on the PR's commits before the merge.
**Alternatives.** Separate exam PR with `xfail` markers (noisy, two PRs per issue); a CODEOWNERS lock on `tests/acceptance/` (needs a second GitHub user).
**Revert.** Change the job to compare the whole PR diff (both paths changed → fail) and land exams in their own PRs.

## D10 · Review rounds on #20 closed by the manager; exam tamper-proofing is good enough for now
in force · factory · 2026-11-14

**Decision.** After two review rounds, #20 merges with: per-commit exam integrity (D9 + amendment), pytest config files under `labs/` counted as exam surface, the acceptance gate run with `--confcutdir=tests -o addopts=""` (zero skips; docker-marked exams gated in the docker job). Still open and moved to #21: pinning the exam SHA, running the check from a `pull_request_target` workflow on `main` instead of the PR's own `ci.yml`, removing the bootstrap fallback, and pinning actions by SHA.
**Why.** The party that could tamper with the exam is our own build step, not an outside attacker; every change is still visible in the PR diff and read by the reviewer. Each extra round blocks the whole line (everything depends on #1).
**Revert.** Reopen #20's findings as blockers and do #21 before any other issue.

## D11 · Owner: the manager may push to any branch; infrastructure for both subdomains is in place
in force · **owner (Ilia)** + factory · 2026-11-14 (renumbered from a duplicate D10)

**Owner:** the factory manager may push to any branch (including `main`) and decide on merges without asking.
**Done outside the queue (owner request, so the subdomain stopped showing n8n):** DNS records for `labs` and `factory` are in the EMEW-AI terraform (imported, TTL 300). The shared certificate covers both. Placeholder HTTPS blocks `learning-factory:factory` and `learning-factory:labs` and the port 80 block `learning-factory:http` are installed, and the EMEW-AI repo copy of `default.conf` matches the server (commit `1894c50` in AI-Brainstorms/EMEW-AI). Issues #13, #14 and #19 were updated.
**Revert:** delete the three marked blocks and reload nginx; `terraform destroy -target` the two records.

> **D9 amendment (factory, 2026-11-14, review of #20).** The exam surface is every file under `labs/*/tests/` except `labs/*/tests/unit/`. Merge commits on PR branches are rejected (rebase instead). The check runs the base branch's copy of `scripts/ci/exam_integrity.py`, and a PR may not change the exam together with `scripts/ci/**` or `ci.yml`. The acceptance suite must run with zero skips. **Known gap:** history rewriting on the branch (fixup + autosquash) can still reshape the exam; closing it by pinning the exam SHA is #21.


## D12 · Dashboard snapshot cadence fits the free Actions minutes; exam for #18 revised
in force (assumption on budget) · factory · 2026-11-14

**Decision.** The repo is private on the free plan (2,000 Actions minutes a month; every job bills at least one minute). A 15-minute cron alone is ~2,880 minutes a month and would stop CI around day 20. So `factory-dashboard.yml` runs on **push to `main`** (the factory commits `STATE.md` at every step, so the page stays current), a **cron every 3 hours** (~240 min/month) and `workflow_dispatch`; no `issues` / `pull_request` triggers (they also built from unmerged branches, review of #22). The snapshot's run lights use only runs on `main`, queried per workflow. The claim is read from `status:in-progress` issues, then `status:in-review`, with the same rule as `scripts/issue_lock.py` (first live claim wins). `server` is `null` until the smoke workflow (#15) provides it.
**Exam change.** D8's cadence and the claim rule were pinned by the exam. The exam writer revised it in a new commit; the manager re-sequenced the branch so both exam commits come before any code (the builder never touched the exam).
**Alternatives.** Make the repo public (unlimited minutes for public repos) and go back to 15 minutes: owner decision, `OWNER.md` O7. Publish from the laptop runner instead of Actions.
**Revert.** Restore the `*/15` cron and the event triggers in `factory-dashboard.yml` and its exam.

## D13 · Review of #18 closed by the manager after round 2
in force · factory · 2026-11-14

**Decision.** PR #23 merged after two review rounds with the round-2 should-fixes done in the PR (Dashboard CI path filter covers `factory-dashboard.yml` and `junit_gate.py`; run-lookup failures are logged instead of shown silently as "no run"), plus small nits (throughput counts only issues closed as completed, Ship matched before Check, incident watch on the dashboard workflows, 7-day artifact retention). Moved out: workflows other than `ci.yml` that run an exam are not CI surface for `exam_integrity.py`, and `pyproject.toml` pytest config is not exam surface → #21. The public `snapshot.json` carries STATE.md text, and the page has no CSP and loads Google Fonts → #19, before publishing.
**Why.** None of the findings was a blocker; the CI-surface fix cannot ship in a PR that also carries an exam (the base-copy check rejects exam + `scripts/ci` together).
**Revert.** Reopen #18 and treat the moved findings as blockers.

## D14 · Publishing the dashboard: static copy only, trimmed snapshot, no third-party requests
in force · factory · 2026-11-14 (issue #19)

**Decision.**
- **Publish = copy files.** `factory-dashboard.yml` copies `index.html`, `app.js`, `styles.css` and `snapshot.json` to `/root/static/factory/` over SSH (secrets `SSH_PRIVATE_KEY`, `SERVER_HOST`, `SERVER_USER`), only for runs on `refs/heads/main`. It uploads into a staging directory and moves the files into place with `mv` (no half-written `snapshot.json`). Without the secrets it logs one line and skips (rule 2). A publish run never touches nginx.
- **nginx block in the repo.** The `factory` block already on the server (D11) is stored as `labs/deploy/nginx/factory.conf`. `labs/deploy/nginx/install-block.sh <name> <file>` replaces or appends the marked block idempotently (backup → `docker exec nginx-proxy3 nginx -t` → reload, restore on failure; D3). It runs on the server when a block changes (by #13's deploy, or by hand), not on every dashboard run.
- **Public snapshot is trimmed.** `build_snapshot.py --public` omits the STATE.md free text (`state`), which the page does not render; the published file holds only what the page shows (issue titles and statuses, decision and owner item titles, run lights, throughput). D8's privacy assumption (public, `noindex`) stands for those fields.
- **No third-party requests.** Poppins is replaced by the system font stack, and the page carries a CSP `<meta>` (`default-src 'self'`); the inline theme script moves into a file.
**Why.** The lightest path that keeps the owner's other sites untouched on every run, and a public page that leaks nothing beyond D8.
**Alternatives.** Self-host Poppins (adds font binaries); render STATE Now/Health on the page; install the nginx block on every run.
**Revert.** Drop `--public` in the workflow; restore the Google Fonts links; remove the publish step (the placeholder page stays).

## D15 · Review of #19: installer hardened in the PR; deploy-key scope and host-key pinning split out
in force · factory · 2026-11-14

**Decision.** PR #24 merges after both review rounds on `install-block.sh` were fixed in the PR (marker order and loose or indented markers refused, a complete `lf-` prefixed backup before the live conf is touched and pruning of our own backups only, text outside the block verified unchanged, restore on any exit before `nginx -t` passes, `flock`, no-op when unchanged, explicit reload failure, backup pruning; unit tests for each). Split out to #25: the root SSH key is a repo-level secret any branch's workflow could read, and the publish step trusts the host key on first sight (`ssh-keyscan`). #25 moves the key into a `production` environment limited to `main` (owner action O8) and pins the host key in a repo variable. D14 is amended: the public snapshot omits the STATE.md text; the other fields (all decision titles, phase and priority counts, merged PRs) stay, as they are what D8 already publishes. Exam gaps (restart check limited to docker calls, whole-file text checks) go to #21.
**Why.** The key exposure predates this PR (D2/D7) and needs an owner step; the installer is the part that can hurt the owner's other sites, so it is fixed now.
**Revert.** Reopen #19 and hold publishing until #25 lands.

## D16 · Labs follow the final lesson plan; no AI-mode badges
in force · **owner (course approved)** · 2026-11-14

**Decision.** The course is final: `courses/multi-engineering/lesson-plan.md` (the PDFs in that folder are authoritative). 63 lessons in modules 0–6 (M0 1–4, M1 5–12, M2 13–22, M3 23–38, M4 39–48, M5 49–56, M6 57–63). Every lab carries `module` and `lesson` in `lab.yaml` (issue #5); Linux 1 is lesson 5 (#12). The course method is "understand with AI, then do with AI, then a 10–15 minute checkpoint without AI, plus a short no-AI practical exam at the end of each module". The 🟢🟡🔴 AI-mode badges are **not** a course rule and are not built. Self-practice is 50 h, split per module 2/6/10/14/8/6/4. That is a guide for how many labs each module needs (the planner uses it when slicing content issues).
**Revert.** Remove the fields from the lab format.

## D17 · #25 shipped; incident #26 fixed in the same PR
in force · factory · 2026-11-14

**Decision.** The factory created the `production` environment (deployment branches: `main` only) and the repo variable `SERVER_HOST_KEY` (ed25519, `SHA256:rLeH22crVQGqJdAe53Y2npx4qFPOO6ZT3qnEfwmPg00`, the same key the owner's machine already trusted). #27 moved the publish into a `production` job with the pinned host key. The owner then ran O8 step 2: the repo-level deploy secrets are gone, so only `main` (through `production`) can read the key, and #25 closed. Incident #26 (the server refused port 22 during a burst of SSH connections) was fixed in #27: one multiplexed connection (ControlMaster) and a retry on the first connect. The review's exam gaps for #25 went to #21.
**Why.** The environment and variable are not secrets, so the factory can create them; deleting the old key is the owner's step (D7, rule 2).
**Revert.** Remove `environment: production` from the publish job and restore the step in the snapshot job; delete the environment and the variable.

## D18 · Interfaces for the content loader (#5) and the lab image (#2)
in force · factory · 2026-11-14

**Decision.** (a) The content loader is the manager module `app.content`: `load_labs(root) -> LoadResult` with `.labs` (valid labs by id) and `.errors` (lab dir relative to root -> list of messages); a broken lab is reported, never served, and never stops other labs from loading. `parse_check_output(stdout, exit_code) -> CheckResult`: a task passes only when the exit code is 0 **and** no `FAIL` line was printed; other lines are ignored. (b) The lab image `lf-lab-term` takes its ttyd base path from env `LF_BASE_PATH` and listens on port 7681. The docker-marked exam uses the image named in `LF_LAB_TERM_IMAGE` (CI builds it once and passes it), otherwise it builds `labs/images/term` itself. (c) The exam for "scripts are executable" is skipped on Windows only; CI (Linux) always runs it.
**Why.** The exam writer and the implementer need one agreed interface; these are the smallest that cover the issue text. Exit code and lines must agree so a crashed `check.sh` that printed some `PASS` lines never counts as passed.
**Alternatives.** Loader as a CLI with JSON output (harder to use from the API); port 8080 for ttyd (7681 is ttyd's default).
**Revert.** Rename in the module and the exam together, in one issue with a new exam.

## D19 · An issue that needs the exam and a CI workflow change ships in two PRs
in force · factory · 2026-11-14

**Decision.** The exam-integrity gate (D9) fails a PR that changes the exam together with `ci.yml` or `scripts/ci/**`. When an issue needs both (as #2 does: its acceptance criteria include the CI job that builds and pushes the image), it ships as **PR A** (code and CI change, no exam; "Part of #n") and then **PR B** (the exam alone, branched from `main` after A merges; it closes the issue). The exam is still written before the code (its commit is older than A's code commits), and B's CI runs it against `main`. The gate itself is not changed.
**Why.** The gate keeps a PR from weakening the CI that grades its own exam; relaxing it would cost more than a second PR. A red exam never lands on `main`, because B is merged only when it is green.
**Also.** The #2 exam read the container IP from `.NetworkSettings.IPAddress`, which Docker 28+ leaves empty; the manager fixed the lookup in a separate exam-only commit (per-network address). The assertions did not change.
**Alternatives.** Allow exam + CI changes when the CI diff only adds a job (needs a YAML-aware gate; later, if it happens often); write the CI part of the exam as a follow-up issue.
**Revert.** Ship such issues in one PR again after changing the gate.

## D20 · How lab containers run (from the review of #2)
in force · factory · 2026-11-14

**Decision.**
- Lab containers run with `--cap-drop ALL --cap-add SETUID --cap-add SETGID --security-opt no-new-privileges` under `runsc`. `SETUID`/`SETGID` are needed for ttyd (root) to start `su - student`; `no-new-privileges` still blocks any setuid binary from raising the student's rights.
- `sudo` is installed in `lf-lab-term` with no rule for `student`. A lab with `sudo: true` gets a file in `/etc/sudoers.d/` from the manager, and that lab's container starts **without** `no-new-privileges` (sudo is setuid). Only such labs drop it (#7).
- `tini` is PID 1 in lab images, so orphaned processes are reaped and do not eat the `--pids-limit`.
- The command log is appended to (`script -a`), so a reconnect keeps the history. Its size is capped by #7: `/var/log/lf` is a size-capped tmpfs (`mode=0700`), counted against the container's memory limit.
- ttyd has no login of its own. Lab containers must not reach each other: #3 creates `lf-labs` with inter-container traffic off (`com.docker.network.bridge.enable_icc=false`) in addition to the DOCKER-USER rules. Because the manager is on that network too (D6), ICC off would also cut the manager off, so #3 adds a DOCKER-USER rule, ahead of the ICC drop, that accepts the manager's address to lab containers on tcp/7681 (plus established return traffic). #3's test checks both: the manager reaches 7681, one lab container does not reach another. A per-session ttyd credential injected by the #8 proxy is an option if that ever proves insufficient.
- The CI job that pushes `lf-lab-term` waits for the docker exam job (`needs: docker`). The docker job builds its own copy from the same commit; D18(b)'s "build once" is relaxed to that.
**Why.** The review of #30 found that `su` fails under `--cap-drop ALL`, that the image had no `sudo` to grant, that `man` is stubbed out in minimized Ubuntu, and that a reconnect truncated the log.
**Alternatives.** Start ttyd as `student` and log with a root helper (more moving parts); `--init` instead of tini (a run flag every caller must remember).
**Revert.** Drop the capability adds and the `sudo` package in the image and in #7.

## D21 · Review of #5 closed by the manager after round 2
in force · factory · 2026-11-14

**Decision.** Round 2 of the review of #31 left one finding (a served lab was also listed in `errors` when a broken copy shared its id). The manager had it fixed (72b1b4f), checked the diff, and merged on green CI without a round 3. The two non-blocking findings went to the issues that own them: the student API must not send the quiz answer (`Task.correct`) (#9); the allowlist proxy must refuse hostnames that resolve to private or link-local addresses (#3).
**Why.** The fix was four lines with a unit test; a third round would cost more than it finds (FACTORY: two rounds, then the manager decides).
**Revert.** Reopen #5 with a new review.

## D22 · Database layer for #4: asyncpg, plain SQL migrations, a separate health endpoint
in force · factory · 2026-11-14

**Decision.**
- **Driver:** `asyncpg` directly (no ORM). Every connection uses `statement_cache_size=0`, because Supabase's transaction pooler (port 6543) does not keep prepared statements.
- **Migrations:** plain SQL files `labs/manager/migrations/NNN_<slug>.sql`, applied in name order by `app.db` (`migrate(dsn) -> list[str]` of newly applied versions), each in its own transaction under `pg_advisory_xact_lock`, recorded in `learning_factory.schema_migrations(version, applied_at)`. A second run applies nothing. The manager runs them on startup when `LF_DATABASE_URL` is set.
- **Schema and role:** migrations never create the schema (the `lf_app` role has no CREATE on the database, and `create schema if not exists` checks that privilege first). `labs/deploy/db/001-create-schema-and-role.sql` is the owner's script (already applied, O3): idempotent, creates role `lf_app` (login, password set separately by the owner), schema `learning_factory` with usage+create for `lf_app`, revokes `public`, sets `search_path`. CI runs that file as the Postgres superuser and then everything else as `lf_app`, so CI proves the role works with only that schema.
- **Degraded mode:** `LF_DATABASE_URL` absent → the manager starts, logs one line, and DB-backed endpoints answer 503 `database not configured`. `/healthz` stays the plain liveness probe (`{"ok": true}`, exam of #1). The new `GET /api/health` reports `{"status": "ok"|"degraded", "database": "ok"|"not_configured"|"unavailable"}` with HTTP 200.
- **Tests:** tests that need Postgres read `LF_TEST_DATABASE_URL` (a superuser DSN to a throwaway database) and skip when it is absent (local runs). CI's `manager` job gets a `postgres:16` service and sets it, so the no-skip exam gate makes them run there. #4 needs a `ci.yml` change, so it ships in two PRs (D19).
**Why.** The spec allows SQLAlchemy/asyncpg; seven tables and hand-written queries do not need an ORM or Alembic, and plain SQL is what the owner can read. A separate health endpoint keeps #1's exam intact.
**Alternatives.** SQLAlchemy Core + Alembic (more moving parts, pooler quirks in two layers); putting the database state into `/healthz` (would change #1's exam).
**Revert.** Add SQLAlchemy/Alembic later behind `app.db`; the SQL files become the first Alembic revision.
**Amended after the review of #34 (round 1).**
- *What "only schema `learning_factory`" means.* The owner script gives `lf_app` tables only in `learning_factory` and revokes its direct rights on `public`, but `lf_app` keeps what every role gets through `PUBLIC`: USAGE on schema `public`, EXECUTE on functions in `public` (the Postgres default), CONNECT/TEMP, and catalog reads. Changing `PUBLIC` affects the other app in the same database, so the factory does not do it; the owner checks the exposure (O9). The manager never calls anything outside `learning_factory`.
- *Robustness.* Any startup error (including a malformed URL, whose `ValueError` can echo part of the password) leaves the manager degraded and logs only the error class (plus SQLSTATE and message for server errors); a failed start is retried in the background with backoff; connect, health ping and the migration lock all have timeouts; `migrate()` fails when it finds no migration files.
- *Data integrity in 001.* One `task_results` row per (session, task); one `lab_runs` row per session; at most one `first_completion` per (student, lab).
- *Tests.* Database tests (unit and exam) refuse any `LF_TEST_DATABASE_URL` that is not localhost: they drop the schema.
- *Limits.* Migrations run in a transaction through the transaction pooler, so a migration can never use `CREATE INDEX CONCURRENTLY`.

## D23 · Login for #6: server-side sessions, in-process rate limits, API shape
in force · factory · 2026-11-14

**Decision.**
- **ID hash:** `app.auth.id_hash(raw_id, secret) -> str` = lowercase hex of HMAC-SHA256(`LF_ID_SECRET`, normalized id); normalization removes all whitespace, then leading zeros. An id that is empty after normalization never matches. `LF_ID_SECRET` absent → login endpoints answer 503 `login not configured` (degraded, like the database).
- **Passwords:** argon2id via `argon2-cffi` (`PasswordHasher` defaults) for cohort and admin passwords.
- **Sessions:** server-side. Migration `002_auth_sessions.sql` adds `learning_factory.auth_sessions` (token_hash = SHA-256 of a 32-byte random token, kind `student`|`admin`, subject = student `id_hash` or admin username, created_at, expires_at). Cookie `lf_session` = the token; `HttpOnly; Secure; SameSite=Lax; Path=/`; lifetime 12 h. Logout deletes the row. A disabled or expired student is refused at login and on every authenticated request.
- **API:** `POST /api/auth/login` `{"id", "password"}` (student) and `POST /api/auth/admin/login` `{"username", "password"}` → 200 `{"kind", "name"}` + cookie. Unknown id or wrong password → 401 `{"detail": "invalid_credentials"}` (same answer for both). Right password but disabled → 403 `{"detail": "account_disabled"}`, access ended → 403 `{"detail": "access_expired"}`. `GET /api/auth/me` → 200 `{"kind", "name"}` or 401. `POST /api/auth/logout` → 204.
- **Access date:** valid while today (server date, UTC) ≤ `coalesce(student.access_until, cohort.access_until)`.
- **Rate limit:** in-process sliding window: 10 failed logins per 15 min per client IP and, separately, per id hash (admin: per username). Over the limit → 429 `{"detail": "too_many_attempts"}` before the password is checked, also for a correct password. Client IP = the socket peer; behind nginx, the manager trusts `X-Forwarded-For` only from the proxy it is configured with (`LF_TRUSTED_PROXY`, unset = trust none). One manager process, so memory is enough; a restart resets the counters (accepted).
- **First admin:** `python -m app.cli create-admin <username>` reads the password from stdin (no echo, never an argument), idempotent per username (`--reset` to change a password). OWNER.md gets the exact command once #13 deploys the manager.
- **Logs:** the raw id is never logged; log lines name the id hash's first 8 hex characters at most.
**Why.** Server-side sessions can be revoked (disable a student, end access) without a signing key to rotate; the spec's single manager container makes in-memory rate limits sufficient. KodeKloud-style labs need nothing more.
**Alternatives.** Signed stateless cookies (no revocation without a deny list); rate limits in Postgres (more writes on every failure).
**Revert.** Replace `app.auth` internals; the API shape stays.
**Amended after the review of #40 (round 1).** Each attempt is recorded in the limiter before any database or argon2 work, so a burst of parallel requests cannot get past the limit; argon2 runs behind a small semaphore and answers 503 `busy` when it is full (the CPU is shared with the owner's other services). Limits come from `LF_LOGIN_ID_LIMIT` and `LF_LOGIN_IP_LIMIT` (default 10 each, 15 min). Behind nginx the per-IP limit is only meaningful with `LF_TRUSTED_PROXY` set, and a class behind one school NAT shares one IP, so production sets the IP limit to 100 (both are now acceptance criteria of #13, together with the owner's `create-admin` command). Database errors after startup answer 503, never 500. `create-admin --reset` ends that admin's sessions.
**Amended after the review of #40 (round 2), closed by the manager without a round 3.** A student id is valid when, after normalization, it is 1–32 ASCII digits (`app.auth.valid_id`); anything else is refused before the database. The cohort import (#11) must use the same function, or a student with letters in the id could never log in. The limiter's key cap evicts per-IP keys first and never a live per-id key at its limit; IPv6 clients are grouped by /64; the expired-key sweep runs at most every 5 s.

## D24 · Session lifecycle for #7: a Docker runtime layer, API shape, janitor, admission
in force · factory · 2026-11-15

**Decision.**
- **Docker access:** the manager talks to the Docker Engine API directly with `httpx` over the unix socket (`LF_DOCKER_HOST`, default `unix:///var/run/docker.sock`; `httpx` becomes a runtime dependency). No docker CLI in the image, no docker SDK. All of it sits behind `app.runtime`: a `LabRuntime` protocol (`start(spec) -> container name`, `remove(name)` = force + volumes, `list_lab_containers()` -> name, running, session id), the `DockerRuntime` implementation, and a pure function `container_spec(lab, session_id) -> dict` that builds the Engine API create body. The app holds the runtime in `app.state.runtime`; tests replace it with a fake after startup.
- **Run flags (spec §6, D20):** image from the lab (`lf-lab-term` → `LF_LAB_TERM_IMAGE`, default `ghcr.io/shashkist/lf-lab-term:latest`), `Runtime=runsc`, network `LF_LAB_NETWORK` (default `lf-labs`), memory 256 MB (768 MB for `lf-lab-code`) with swap equal to memory (no extra swap), `NanoCpus` 0.5 CPU, `PidsLimit` 256, `CapDrop ALL`, `CapAdd SETUID SETGID`, `no-new-privileges` unless the lab has `sudo: true`, `LogConfig none`, no mounts or volumes, tmpfs `/var/log/lf` (`size=16m,mode=0700`) and tmpfs `/home/student` (`size=128m`) as the home size cap. Name `lab-<session uuid>`, labels `lf=1` and `lf.session=<uuid>`, env `LF_BASE_PATH=/s/<uuid>/term/`. Because a tmpfs home hides the image's home, the `lf-lab-term` entrypoint copies `/etc/skel` into an empty home and gives it to `student`.
- **API (student cookie from D23):** `POST /api/sessions` `{"lab_id", "force": false}` → 201 `{"id", "lab_id", "status", "expires_at"}` for a new session, 200 with the same shape when the same lab is already open; another lab open → 409 `{"detail": {"code": "another_lab_open", "session_id", "lab_id", "title"}}` (`title` is the bilingual dict); `force=true` ends the other one first; unknown lab → 404 `{"detail": "unknown_lab"}`; admission refused → 503 `{"detail": "capacity_full"}`. `GET /api/sessions/current` → 200 (same shape) or 404 `{"detail": "no_session"}`. `POST /api/sessions/{id}/heartbeat` → 204, 404 when it is not the caller's live session. `POST /api/sessions/{id}/end` → 204, also for the caller's already ended session. Any loaded lab can be started; `status` filtering belongs to the student UI (#10).
- **Data:** migration `003_session_lifecycle.sql` adds `lab_sessions.expires_at` (start + the lab's `time_limit_min`) and `end_reason` (`student`, `forced`, `heartbeat`, `time_limit`, `access_expired`, `orphan`). The existing partial unique index is the final guard for "one live session per student": a unique violation answers 409.
- **Janitor:** `app.janitor.run_once(...)` ends sessions without a heartbeat for `LF_HEARTBEAT_TIMEOUT_S` (300; the start time counts as the first heartbeat), past `expires_at`, or whose student's access has ended (same date rule as D23); removes `lab-*` containers that are stopped or have no live session; marks live sessions whose container is gone as ended (`orphan`). It runs on startup and every `LF_JANITOR_INTERVAL_S` (60) seconds, and logs a warning when free memory or disk is under its reserve.
- **Admission:** free memory = `MemAvailable` from `/proc/meminfo` (`LF_MEMINFO_PATH` for tests); refuse when it minus the new container's memory limit would fall below `LF_MEM_RESERVE_MB` (1500), when free disk on `LF_DISK_PATH` (`/`) is below `LF_DISK_RESERVE_GB` (10), or when live sessions of that image kind reach `LF_MAX_TERM` (20) / `LF_MAX_CODE` (8). Settings are read per request.
- **Content:** labs load once at startup from `LF_CONTENT_DIR` (default: the repo's `labs/content`) into `app.state.labs`.
- **Tests:** API and janitor tests use Postgres (the `manager` job) with a fake runtime; `docker`-marked tests drive `DockerRuntime` against real Docker under runsc in the existing `docker` job (start, limits as seen by `docker inspect`, end, orphan sweep), creating the `lf-labs` network if it is missing. No `ci.yml` change, so #7 ships in one PR.
**Why.** The Engine API over the socket is one small dependency the manager already uses for tests (httpx) and works for the later "lab host" over TLS (spec §5). A runtime protocol lets the session logic be tested without Docker, where the database lives, and the Docker part where Docker lives.
**Alternatives.** `aiodocker` (pulls in aiohttp), the `docker` SDK (sync, needs a thread pool), the docker CLI in the image (+50 MB). A writable-layer quota for the home cap (needs xfs with pquota; the server's disk is ext4).
**Revert.** Swap `DockerRuntime` for another implementation behind the same protocol.
**Amended during the build.** Root in a lab has no `CAP_CHOWN` (`CapDrop ALL` + `SETUID`/`SETGID` only), so the entrypoint cannot `chown` the home. Instead `student` has a fixed uid/gid 1001 in `lf-lab-term`, the home tmpfs is mounted with `uid=1001,gid=1001,mode=0755`, and the entrypoint copies `/etc/skel` into an empty home as `student`. A failed container start answers 502 `start_failed` and leaves no session row. Admission runs before a forced end, so a refusal never kills the student's open lab. Known gap, accepted: two students starting at the same instant can exceed a per-kind cap by one (the memory reserve still applies to each).
**Amended after the review of #41 (round 1).**
- *Disk.* Every place a student can write is a size-capped tmpfs that counts against the container's memory: `/home/student` (128m), `/tmp` (64m), `/var/tmp` (32m), `/run/lock` (1m), `/var/log/lf` (16m). Home and `/tmp` are mounted `exec` (Docker adds `noexec` to tmpfs by default, which would stop students from running their own scripts). For root in `sudo` labs, the production runtime runs with `--overlay2=root:memory`, so writes anywhere in the root filesystem count against memory, not host disk (#3 registers `runsc` with that argument).
- *Sudo labs.* The container gets `LF_SUDO=1` and the entrypoint writes `/etc/sudoers.d/student` (mode 0440, `student ALL=(ALL) NOPASSWD:ALL`); they also get `CHOWN`, `DAC_OVERRIDE` and `FOWNER`, which the "change ownership" tasks need. Non-sudo labs keep exactly `SETUID`+`SETGID`.
- *Janitor safety.* The sweep never removes the container of a session that is `starting`; ending a session while it starts leaves no container (the start path checks that its row is still live after the start and removes the container otherwise); each janitor step has its own error handling and the loop never dies; the sweep lists only containers labelled `lf=1` with an `lf.session` label; each container carries `lf.expires=<epoch>`, and the sweep removes containers past it even when the database is unreachable.
- *Later issues.* `setup.sh`/`check.sh` (#9) need root rights the lab's root does not have (no `DAC_OVERRIDE`/`CHOWN` in non-sudo labs): #9 runs them through `docker exec` with `Privileged: false` but adds the capabilities it needs for the exec only, or runs them as `student` where that is enough; #9 decides. A per-student start rate limit and admin-only `draft` labs are follow-ups (#10/#11), and #13 must pre-pull `lf-lab-term`.

## D25 · Server bootstrap for #3: one script with steps, tested on the CI runner
in force · factory · 2026-11-15

**Decision.**
- `labs/deploy/bootstrap/bootstrap.sh` runs the steps in the order the owner set on #3 (daemon.json runtime → swap → network → iptables), each idempotent, each followed by the baseline check and its own rollback. `DRY_RUN=1` prints every command and planned diff and changes nothing. The baseline is configurable: `LF_BASELINE_URLS` (space-separated, expected 2xx/3xx), `LF_BASELINE_TCP` (`host:port` list), plus all non-`lf-`/`lab-` containers with their start times. On the server, the workflow passes the owner's list (n8n, pulsesign, jsoul, OpenClaw 18789).
- `daemon.json` is merged with `jq` (only `runtimes.runsc` is added; everything else is compared with `jq -S` before and after). `systemctl reload docker` only. `lf-labs`: bridge `br-lf-labs`, `enable_icc=false`, internal is **not** set (labs with `internet: true` later need egress through the allowlist proxy; the DOCKER-USER rules do the blocking). DOCKER-USER rules all match `-i br-lf-labs` and carry `-m comment --comment lf-labs`; the manager→lab tcp/7681 accept comes first, then established/related, then drops to the host IPs, RFC 1918, link-local, CGNAT and other bridges. Rules are re-applied by a systemd oneshot unit `lf-labs-iptables.service` after `docker.service` (no `iptables-persistent`, which would save the owner's rules too). The manager's address on `lf-labs` is fixed: the network has subnet `172.31.240.0/24` and the manager gets `172.31.240.2` (#13 sets it in compose).
- **Tests:** the exam lives in `labs/manager/tests/acceptance/test_issue_3_bootstrap.py`, `docker`-marked, and runs the real script with `sudo` against the runner in the existing `docker` job: twice (the second run reports no changes), with a foreign sentinel container whose start time must not change, a pre-seeded daemon.json key that must survive byte-identical, both network checks (the manager address reaches a lab's 7681, one lab cannot reach another), and a dry run that changes nothing. No `ci.yml` change.
- `bootstrap.yml`: `workflow_dispatch` (input `apply`, default false) and pushes to `main` that change `labs/deploy/bootstrap/**` (dry run unless the commit message contains `[bootstrap apply]`). Never scheduled. `environment: production`, one multiplexed SSH connection. The first server run is a dry run; its log is reviewed and recorded on #3 before the apply run.
**Why.** The owner's safety rules on #3 need a rollback per step and proof that foreign containers were not restarted; a runner is a real Docker host where all of it can be exercised without touching the server.
**Alternatives.** Ansible (a new tool for one host); `iptables-persistent` (saves every rule on the host, including the owner's).
**Revert.** Delete the rules tagged `lf-labs`, `docker network rm lf-labs`, remove the runsc key from daemon.json (backup kept), `swapoff /swapfile-lf`.
**Amended while writing the exam.** (1) Traffic from a lab to the host's own addresses (the `lf-labs` gateway, eth0, docker0) is delivered through `INPUT`, never through `FORWARD`/`DOCKER-USER`, so DOCKER-USER alone cannot keep labs off the host's services (spec §6). The script also adds one jump in `INPUT`, `-i br-lf-labs -m comment --comment lf-labs -j LF-LABS-INPUT`, and that chain drops everything except established/related. Like every other rule it matches only traffic from `br-lf-labs`, which keeps the intent of the owner's rule 4 on #3; the deviation from "only in DOCKER-USER" is noted on #3. (2) The report is the last stdout line `BOOTSTRAP RESULT: dry run|changed|no changes`; exit 0 on success. (3) CI's `docker` job runs the docker-marked tests twice (all tests, then the exam alone), so the exam's fixture first resets the runner to the pre-bootstrap state (rules tagged `lf-labs`, the `LF-LABS-INPUT` chain, the `lf-labs` network, the unit). That reset is test harness only, never part of the script. (4) Internet is off for labs (spec §6): the last `lf-labs` rule in DOCKER-USER is `-i br-lf-labs ! -s 172.31.240.2 -j DROP`. The manager's address is excluded so its own egress works whichever of its networks is its default route; a lab cannot take that address without `NET_ADMIN`/`NET_RAW`, which it does not have. The allowlist proxy for `internet: true` labs will insert its own accept ahead of it. (5) A failing bootstrap opens an incident (`Server bootstrap` added to `incident.yml`). (6) `lf-labs` hands out dynamic addresses only from `172.31.240.128/25` (`--ip-range`), so a lab started while the manager restarts can never take the manager's `172.31.240.2` (CI found exactly that). An existing `lf-labs` with another config fails the run; it is never recreated. (7) `runsc` is registered with `runtimeArgs: ["--overlay2=root:memory"]`, so writes to a lab's root filesystem count against its memory limit, not the host disk (D24, review of #41).
**Amended after the review of #42 (round 1).**
- *No apt package for runsc.* The `runsc` Debian package's postinst runs `runsc install` and `systemctl reload docker` itself, outside the script's backup, diff and rollback. If no `runsc` binary exists, the script installs the release binary from `storage.googleapis.com/gvisor/releases/release/latest/x86_64/` to `/usr/local/bin/runsc`, checked against its published `.sha512`; an existing binary is reused. `daemon.json` is backed up before anything else, written atomically (temp file in `/etc/docker`, `dockerd --validate --config-file`, `mv`), and only by the script.
- *apt never restarts host services:* every `apt-get` runs with `DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l NEEDRESTART_SUSPEND=1`.
- *Rollback touches only this run's work:* the script records each rule it inserted and deletes only those, so a later run that fails its baseline never strips the isolation of running labs.
- *Simpler rules:* no host-IP drops in DOCKER-USER (traffic to the host goes through `INPUT`, which `LF-LABS-INPUT` covers); the private-range, link-local, CGNAT, other-bridge and catch-all drops stay.
- *The network is checked, not trusted:* `--ipv6=false`; an existing `lf-labs` must have the bridge name `br-lf-labs`, ICC off, IPv6 off, and the right subnet and ip-range, or the run fails. Full IPv6 isolation (ip6tables) is a follow-up.
- *Swap* needs 4 GB plus the 10 GB disk reserve free (`LF_SWAP_MIN_FREE_GB`, default 14). The runsc check container is named `lf-runsc-check`. `HUP`/`INT`/`TERM` roll back like an error. The workflow applies only when a commit message line is exactly `[bootstrap apply]`.
- *The exam exercises the risky steps on the runner:* it removes the runner's `runsc` registration so the runtime step really runs, turns swap off around the run, and fails the baseline after the first step to prove the rollback restores `daemon.json` byte for byte. It also checks that a lab cannot reach the internet.
- *Reload quirk:* dockerd reloads runtimes only when the `runtimes` key is present in `daemon.json`. Restoring a backup without that key would leave `runsc` registered in memory, so the rollback first writes the backup plus `"runtimes": {}`, reloads, then restores the exact bytes and reloads again. The exam seeds `"runtimes": {}` for the same reason (harness fix, before the code).

## D26 · Sudo labs cannot use setuid `sudo` under gVisor; the sudo grant is a follow-up
in force · factory · 2026-11-15

**Decision.** #7 ships without a working `sudo` in `sudo: true` labs. It still writes `/etc/sudoers.d/student` (0440) for those labs, adds `CHOWN`/`DAC_OVERRIDE`/`FOWNER` and leaves out `no-new-privileges`, and the exam checks exactly that. The two exam tests that ran `sudo` as `student` were replaced (exam-only commit, re-sequenced before the code). Making sudo labs work is issue #47.
**Evidence (CI runs 43651139210, 43651444902, 43651681853, runsc 4.19.0-gvisor).** A setuid-root copy of perl run by uid 1001 stays at euid 1001 under runsc and gets euid 0 under runc. This holds on the rootfs overlay and on `/var`, with `NoNewPrivs: 0`, an empty `SecurityOpt`, a root mount without `nosuid`, and every capability variant tried (including Docker's default set). The same lab spec under runc runs `sudo -n id -u` → `0`.
**Why not runc for sudo labs.** Spec §6 makes gVisor the condition for running labs on this server; weakening it for some labs is the owner's call, not the factory's.
**Alternatives for #47.** A root helper inside the container that runs commands for `student` over a socket (a `sudo` shim); starting the sudo lab's terminal as root; a runsc flag or version that honours setuid, if one exists; runc for sudo labs with the owner's consent.
**Revert.** Restore the two sudo tests once #47 provides a mechanism.

## D27 · #3 merged after review round 2 with a blocking finding; no apply run until #50
in force · factory · 2026-11-15

**Decision.** PR #46 (#3) was merged after two review rounds although round 2 left one blocking finding: a rollback of the iptables step deletes the previous boot copy of the rules script, so a later failed apply would leave labs unisolated after the next reboot. Merging only runs a **dry run** on the server (verified by both reviews; run 43653076930 printed `BOOTSTRAP RESULT: dry run` and changed nothing). The finding matters only for an apply run, so every apply waits for #50, which also takes the round-2 findings that could hurt the first apply (runsc registered by a dockerd flag making daemon.json conflict at the next restart; a cancelled SSH session killing the script without rollback; unpinned runsc; daemon.json mode) and the exam scenarios the runner did not cover (seed without a `runtimes` key, the download path, rollback after the iptables step). #3 stays open (`status:blocked`) until the dry-run log is reviewed and recorded, #50 is closed, and the apply run plus an isolation smoke on the server pass.
**Why.** FACTORY: two review rounds, then the manager decides and splits leftovers. The dry run is the first server step the owner asked for on #3, and its output is what the next review needs.
**Revert.** Revert #46 (the workflow then disappears; nothing was applied on the server).
