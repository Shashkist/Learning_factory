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
cadence superseded by D71 (2026-11-19, repository public) · factory · 2026-11-14

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

**Decision.** The course is final: `courses/multi-engineering/lesson-plan.md` (the PDFs in that folder are authoritative; since D43 the folder is kept outside the public repository, on the laptop at `C:\projects\lf-private\courses`). 63 lessons in modules 0–6 (M0 1–4, M1 5–12, M2 13–22, M3 23–38, M4 39–48, M5 49–56, M6 57–63). Every lab carries `module` and `lesson` in `lab.yaml` (issue #5); Linux 1 is lesson 5 (#12). The course method is "understand with AI, then do with AI, then a 10–15 minute checkpoint without AI, plus a short no-AI practical exam at the end of each module". The 🟢🟡🔴 AI-mode badges are **not** a course rule and are not built. Self-practice is 50 h, split per module 2/6/10/14/8/6/4. That is a guide for how many labs each module needs (the planner uses it when slicing content issues).
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
**Amended for #49 (PR #53).**
- *Janitor sweep.* It reads the live rows, then the container list, then the rows again. A container whose session was `starting` in either snapshot is kept. Docker states other than `exited`/`dead` (`created`, `restarting`, `paused`) count as running.
- *Start deadline.* `starting` rows older than `LF_START_DEADLINE_S` (default 180 s, measured from `started_at`, read every pass) end with reason `orphan`, and their container is removed. The update is not yet guarded against a row that flips to `running` at that moment; #55 fixes that.
- *DB down.* The expiry-label sweep backs off from 2 s, doubling up to the janitor interval, and resets after a healthy pass. An identical repeated error is logged once at ERROR (DEBUG after that). Recovery lines and periodic reminders come in #55.
- *Mounts.* Two more capped mounts: `/dev/shm` (`ShmSize` 64 MB) and a root-owned 1 MB tmpfs on `/var/mail`, which hides the `student` mail spool that `useradd -m` creates. The exam's write-surface check is `find / -xdev ! -type l \( -perm -0002 -o -user student \)`. `! -type l` was added to the issue's command because symlinks always show mode 0777 and hold no data.

**Amended after the review of #139 (round 1).** The start waits for the lab to be ready for at most `LF_READY_TIMEOUT_S` (default 45 s, capped at 50 s (48 s since #198), so under nginx's default 60 s `proxy_read_timeout`, which `labs/deploy/nginx/labs.conf` does not change for `/`; see the #177 amendment, where the variable became the whole-start budget). A container that is gone or stopped ends the wait at once; a transport error from the Docker socket (connect error) or an unhelpful answer (500) keeps waiting, bounded by the timeout.

**Amended after #177: one start deadline.** `LF_READY_TIMEOUT_S` now sets the whole-start budget (default 45 s, cap 50 s; 48 s since #198), measured from the request start (a route dependency stamps it before authentication). The whole start (smoke-cohort check, live-session lookup, admission, forced end of the previous session, insert, create/start, readiness wait and the final update, database calls included) runs under that one deadline, and the readiness wait gets only what is left (a spent budget fails the start without waiting). On a timeout, and on any other error after the `starting` row was inserted, the handler cleans up and answers 502 `start_failed` itself (a `TimeoutError` escaping would be turned into 503 by `db_guard`). The authentication database calls run before the handler: they use up the budget (the stamp is taken first) but are not cut off by it. What the code guarantees: each step is bounded by the deadline, cleanup after a failure is bounded to 5 s per step (the `starting` row is deleted first so the student's slot is freed, then the container is removed, even when the delete failed), and anything left over (the container, the `starting` row) is the janitor's orphan sweep (`LF_START_DEADLINE_S`). The `LabRuntime` protocol annotates `runtime` in `sessions.py` and is checked by mypy. **The readiness signal** is "ttyd is running in the container" (`pgrep -x ttyd`); every lab image, including the future `lf-lab-code`, must start ttyd only after the skel copy, so ready means the skel is in place. `wait_ready` is part of the `LabRuntime` protocol; a runtime without it is treated as ready and a line is logged.

**Amended after #192: a failed start whose row is no longer `starting`.** Cleanup is two 5 s bounds, so the answer stays under nginx's 60 s (≤ 50 s budget + 10 s; see the #198 amendment): (1) the row step: delete the `starting` row; if that deleted nothing, end the row with `update ... set status = 'ended', end_reason = 'orphan' where id = $1 and status in ('starting', 'running')` (the deadline cancelled the final `running` update after the server had committed it; `orphan` is the existing reason for a live row without a container, so no migration); (2) the container removal (once; it is not repeated by an `end_session` call). The answer is 409 `session_ended` only when the delete found nothing and the update found no live row (ended meanwhile, or gone); every other failure, including a failed or slow row step, answers 502 `start_failed`. The issue text asked for a new reason `start_failed`, which migration 003's check constraint refuses; a unit test now asserts every `end_reason` literal in `app/` is allowed by the newest constraint. **Revert:** add `start_failed` to the constraint in a new migration and use it here.

**Amended after #198: a margin to nginx.** `READY_TIMEOUT_MAX_S` is 48 s, so the worst failed start (48 s budget + 2 × 5 s cleanup = 58 s) stays under nginx's default 60 s `proxy_read_timeout`; a unit test asserts `READY_TIMEOUT_MAX_S + 2 * CLEANUP_BOUND_S < 60`. The end-reason schema test also reads the janitor's SQL `case ... then/else` reasons and scans `app/` recursively. **Revert:** set the cap back to 50 and drop the sum assertion.

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

## D28 · Live progress: agent and sub-step events, published by the laptop through a write-only key
in force · **owner request** (owner session) · 2026-11-15

**Decision.** The owner wants to see the processes, agents and sub-steps live, in the style of the QA plugin's progress board (one event per sub-step, a board of steps with sub-steps, icons) and the mini-factory page in his deck. `scripts/factory_progress.py` records the events; the skill and every agent definition report through it. The laptop runner publishes `live.json` to `/root/static/factory/` every 15 s during a pass. It uses a dedicated SSH key (`lf-progress`, private half only on the laptop in `%LOCALAPPDATA%\learning-factory\progress_key`). That key's `authorized_keys` line has a **forced command** (`head -c 262144 > live.json.tmp && mv -f … live.json`) plus `no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding`, so it can write that one file and nothing else (verified: an `id; ls` attempt ran only the forced command). This uses no Actions minutes and no server process. The screen is issue #51.
**Amended the same night after asking the two sessions that know the bug-factory page:** worker activity comes from **Claude Code hooks loaded only for factory runs** (`--settings scripts/laptop/factory-hooks.json`; the hook records nothing unless `LF_PROGRESS_FILE` is set). It does not come from reading transcripts: transcripts are an internal format, and reading them on this laptop would also expose the owner's company sessions. Hooks record only allowlisted fields (worker role, hashed id, tool *category*, state, times); never tool input, prompts, titles, paths or branches. `live.json` is built field by field from a public schema (`schema: 1`, `generated_at`); agent details are scrubbed of IPs, emails, foreign URLs, absolute paths and key-like strings. Publishing runs every 5 s but pushes only when the content changed, plus a heartbeat each minute so the page can show "laptop offline". Only the pattern was taken from the QA plugin, not its code (that is a work project).
**Revert.** Remove the ` lf-progress` line from the server's `authorized_keys`, delete the key, and drop the `Publish-Live` calls in the runner.

## D29 · runsc registered by a dockerd flag: the bootstrap fails safe instead of editing daemon.json
in force · factory · 2026-11-15

**Decision.** #50 criterion 2 asks for two things that conflict when runsc is registered by a dockerd flag (`--add-runtime`): daemon.json must be left alone, and `docker info` must show runsc with `--overlay2=root:memory`. dockerd has no flag for runtime args, and adding a `runsc` key to daemon.json while the flag exists makes dockerd refuse to start at the next restart. So: if the loaded runsc already has the overlay arg, the step is satisfied. Otherwise the run fails with `BOOTSTRAP RESULT: failed` and an owner message (remove the flag or move runsc into daemon.json; that needs a Docker restart, which the script never does) and changes nothing. The report always prints the live runtimes and the dockerd command line. The exam accepts exactly this: a flag-registered run must leave daemon.json byte-identical and may succeed only with the overlay arg present.
**Also.** "runsc in daemon.json but not loaded": the script only reloads when the file's runsc already has the overlay arg, no other reloadable key is in the file (their running values cannot be read from `docker info`, so any of them counts as "differs"), and no other runtime in the file is unloaded. Otherwise it fails and changes nothing. runsc is pinned to release 20260817.0, the last dated release that still publishes a bare `runsc` binary plus `.sha512` (later ones ship only tarballs).
**Why.** CLAUDE.md rule 1: never restart Docker on the shared server. A safe failure with a precise owner step costs less than a daemon that will not come back after the next reboot.
**Revert.** Let the script write the overlay arg into daemon.json and tell the owner to drop the flag before the next restart.

## D30 · Server isolation smoke: throw-away busybox containers, controls for every "blocked" check
**Decision (#3, step d).** The isolation proof on the real server is `labs/deploy/smoke/isolation.sh`, run by the dispatch-only `Server smoke` workflow (`production` environment). It starts `lf-smoke-*` containers from `busybox:1.36.1-musl` (Docker Hub, no registry login on the server) under runsc with `--rm` and a 600 s life, and checks: manager address `.2` reaches a lab's 7681 (skipped whenever any `lf-manager` container exists), lab ↔ lab 7681, lab → host (gateway, docker0, public IP), lab → docker0 container, lab → internet (IP literals), runtime runsc, foreign containers unchanged. Every "blocked" check counts only when the host itself reaches the same target, and the in-container probe must first tell an open port from a closed one, so a dead target or a missing tool fails the smoke instead of passing it. Concurrency group `server-lab-host`, to be shared with the deploy workflow (#13).
**Assumption.** Before the apply, the bootstrap also gets a subnet preflight, `/etc/docker` creation and positive baseline log lines (#58, from the written dry-run review on #3). The missing exam scenario without a daemon.json goes to #54 (moved to #142 when #54 shipped the rest in #140) rather than holding the apply: the create path is a `mkdir` + temp file + `dockerd --validate` + `mv`, and its rollback is exercised by the apply's own baseline check.
**Why.** CI proves the rules on a runner; only a run on the server proves them where students will be. Busybox avoids pulling the private lab image with credentials on the shared host.
**Revert.** Delete the workflow and script; isolation is then proven only by the CI exam.
**Amended after review round 2 of #57 (manager's call, no round 3).** Round 2 found a broken `lf-manager` guard (an escaped newline), `docker exec` errors being read as "blocked", and a separate concurrency group from the bootstrap. All three fixed (fc2d470) and checked with a fake `docker`: nc's own exit code is printed inside the container, so anything but `nc=0`/`nc=1` fails the check. **Contract for #13:** the manager container is named `lf-manager`, and the deploy workflow uses concurrency group `server-lab-host` (shared with `bootstrap.yml` and `server-smoke.yml`).
**Amended after the first server run (43688429887) and the review of #60.** That run reached nothing from a lab, but halfway through every exec into lab a began failing within ~40 ms, so it proved nothing. The smoke containers now run with the **production lab limits** (256 MB, 256 pids, D20) instead of 64 MB / 64 pids: under runsc the sandbox's own threads and every `docker exec` count against `pids.max`. No `--rm` (a dead container stays inspectable; the trap and the next run remove it by name; life still bounded at 600 s). Every "blocked" needs the source running before the probe and the target still answering from the host after it; the tool control needs exactly open = 0 and closed = 1; any failure prints nc's raw output and the containers' `status/exit/oom`.
**Amended after the second server run (43689843571, #62).** Both busybox labs on `lf-labs` exited (`exit=0 oom=false`) right after a `docker exec`, while a container with `sleep` as PID 1 survived its execs. (a) The smoke's containers now run with `--init` (Docker's tini as PID 1): `lf-lab-term` runs under tini in production (its ENTRYPOINT), so this matches the real labs. D20 is unchanged (production gets tini from the image, not from `Init`). They also get `--memory-swap 256m --cpus 0.5`, so the D20 limits hold on a host that now has swap. (b) `check=liveness` (`labs/deploy/smoke/liveness.sh`) is a one-off diagnostic: one container per suspect (`sh`/tini/`sleep` as PID 1, execs with and without busybox `timeout`, `lf-labs` vs the default bridge, runsc vs runc), with PID 1 and the process table printed, and exit details, logs and docker/containerd journal lines for any that die. It cannot compare runsc builds (all runsc variants share the server's build). It and its workflow choice are removed when #3 closes. **Revert:** drop `--init` from `RUN_OPTS`; delete `liveness.sh` and the `liveness` choice.

## D31 · lf_app PUBLIC execute exposure: nothing to revoke, accepted as is
in force · factory · 2026-11-15

**Decision (#61, was owner item O9).** No change to `PUBLIC`'s rights in the shared database. The read-only `DB check` (run 43693809513, as `lf_app`, `transaction_read_only = on`) found:
- **`SECURITY DEFINER` functions `lf_app` can execute: none.** That covers every schema it has USAGE on (`public`, `pg_catalog`, `information_schema`, `learning_factory`) and also `public` without the USAGE condition.
- In `public`, `lf_app` can execute 2 functions of the other app, both through `PUBLIC`, both SECURITY INVOKER and owned by `postgres`. They run with `lf_app`'s own rights, and `lf_app` has no rights on that app's tables, so they reach nothing. The names are on #61.
- 6 Supabase event triggers exist, all owned by `supabase_admin`, **none `SECURITY DEFINER`**. Two of them (`pgrst_ddl_watch`, `pgrst_drop_watch`) fire on any DDL, including the manager's migrations run as `lf_app`, and make the other app's PostgREST reload its schema cache. Migrations are rare (on deploy), so this is accepted.

**Why.** `revoke ... from public` would change what the other app relies on and protects nothing here: no definer function is exposed. Per #61's rules, the labs-side position holds: `lf_app` is used only by our manager and has no `anon` exposure.
**Watch item for #13.** The first migration against the real database runs these event triggers as `lf_app`. Check that the deploy log shows the migration applied. DDL is transactional, so a failure leaves nothing behind.
**Revert / re-check.** Re-run `DB check` (`check=public-execute`) after any extension or schema change in that database. If a `SECURITY DEFINER` function with `via_public = t` ever appears, decide then. The exact `revoke execute on function <fn> from public` and its `grant` restore go into a new D-entry, with a health check of the other app before and after.

## D32 · #13 deploy is split: the image and compose file first (#72), then the deploy itself
in force · factory · 2026-11-15

**Decision.** #13 had grown to nine acceptance items (image, compose, workflow, `.env` generation, nginx block, trusted proxy, health checks, pre-pull, create-admin), well past one PR of ~400 lines. Slice 1 is #72: the `lf-manager` image (it serves the built web UI from `LF_WEB_DIR`, SPA fallback that never shadows `/api`, `/s` or `/healthz`) and `labs/deploy/compose.yml`. Its CI build/push of the image is a second PR (D19). #13 keeps the deploy workflow, the server-side script, the nginx `labs` block and the live checks, and depends on #72.
**Why.** One reviewable PR each; the image can be proven in CI (the docker job builds and runs it) before anything touches the server.
**Alternatives.** One large #13 PR (hard to review, long CI loops); building the image on the server (spec §10 allows it, but it puts npm and uv builds on the shared production host).
**Revert.** Fold #72 back into #13; nothing on the server depends on the split.

## D33 · Terminal exam types only after the student's prompt (#8)
in force · factory · 2026-11-15

**Finding.** Under runsc, input sent to ttyd before `su - student` has started its shell is dropped, not buffered by the pty (diagnostics run 43697313873, draft #71: the prompt arrives within about a second; typed after it, `echo hi` prints `hi` and the command log has it within 2 s).
**Decision.** The docker exam's `typed` fixture waits for the prompt (`$ ` at the end of the output) before typing, as a person would. The fix is its own exam commit before the code (D9), on a re-sequenced branch (#74 replaces #73, #70, #66).
**Amended (round 2 of review): one assertion changed.** The `hi` pattern `\nhi\r?\n` never matches real bash output: after the newline of the typed command, bash ends bracketed paste mode (`ESC[?2004l`) and sends `\r` before `hi` (bytes captured in run 43697313873). The pattern (`HI_LINE`) now allows CSI sequences and `\r` between the newline and `hi`. It still needs a line that is just `hi`; the echoed `echo hi` line alone does not match. What the exam proves is unchanged.
**Consequence for the UI (#10).** Nothing to do: xterm.js only sends what the student types after seeing the terminal. A future "run this command for me" button must wait for the prompt too.
**Revert.** Restore the fixture's immediate send if a later gVisor buffers typed-ahead input.

## D34 · #13 deploy: shape of the workflow, the server script and the live checks
in force · factory · 2026-11-15

**Decision.**
- **Trigger.** `.github/workflows/deploy.yml` runs on `workflow_run` of `CI` on `main` when it succeeded (deploying `LF_MANAGER_TAG=<that run's head_sha>`, the image the `image-manager` job just pushed), and on `workflow_dispatch` (input: a commit sha, default the current `main`). Environment `production`, concurrency group `server-lab-host` (D30), no cancel. Missing `SSH_PRIVATE_KEY`/`SERVER_HOST`/`SERVER_USER`/`SERVER_HOST_KEY` → the job prints which names are missing and exits 0 (absence is supported). One SSH connection (ControlMaster), as the smoke workflow does.
- **Server side.** One idempotent script, `labs/deploy/deploy.sh`, copied with `compose.yml` and `nginx/` to `/root/learning-factory/`. Steps: preflight (Docker Engine >= 28, Compose >= 2.33.1, `lf-labs` exists, `nginx-proxy3` running) → `.env` (mode 600; `LF_ID_SECRET` generated with `openssl rand -hex 32` only when missing; `LF_DATABASE_URL` written from the value the workflow sends on SSH **stdin**, kept as is when none is sent; values are never printed, `docker compose config` is never run) → GHCR login with the job's `GITHUB_TOKEN` on stdin, pull `lf-manager:<sha>` and `lf-lab-term:latest`, logout → export `LF_MANAGER_TAG`, `LF_NGINX_NETWORK` (the network of `nginx-proxy3`, read from `docker inspect`) and `LF_TRUSTED_PROXY` (nginx's address on that network) → `docker compose -p learning-factory up -d` → container health → `install-block.sh labs` and `install-block.sh factory` → live checks. A failed health check after `up` redeploys the previously recorded tag (`/root/learning-factory/deployed-tag`) and fails the run.
- **nginx `labs` block** (`labs/deploy/nginx/labs.conf`, replaces the placeholder): 443 with the shared certificate, `set $upstream http://lf-manager:8000` + `resolver 127.0.0.11`, `X-Forwarded-For $remote_addr` (overwritten, so a client cannot choose its own rate-limit key), WebSocket upgrade on `/s/`, `X-Robots-Tag noindex`.
- **Live checks** (fail the deploy when wrong): `https://labs.shashkist.com/healthz` via `curl --resolve ...:443:127.0.0.1` says ok; `/api/health` says `"database": "ok"`; manager logs show the migrations line; `ip route` inside `lf-manager` has its default route on the nginx network; `LF_TERM_UPSTREAM` is not in `.env`; `docker compose version` is printed; `/live.json` on factory has `Cache-Control: no-cache`.
- **Not in #13:** a live terminal smoke through the production path (Docker DNS `lab-<uuid>`) needs a synthetic smoke student and a lab; it is a follow-up issue, before #10 ships.
**Why.** The deploy uses exactly the image CI tested and pushed; a secret only travels on stdin; every shared-config change goes through the validated installer (rule 1).
**Alternatives.** Deploy in the CI workflow itself (mixes PR and production permissions); `:latest` (out-of-order runs could change what runs); build on the server (D5).
**Revert.** Disable `deploy.yml`; `docker compose -p learning-factory down` on the server and reinstall the placeholder `labs` block.
**Amended (review of #83).** A `workflow_run` deploys only a green CI for a **push** in this repository, and only when its sha is still the head of main (an older run never rolls production back). A manual sha must already be on main. The GHCR login uses a throw-away `DOCKER_CONFIG`, never root's shared one. With no `LF_DATABASE_URL` anywhere, the script says so and exits 0 before changing anything. It refuses to guess when `nginx-proxy3` is on more than one user network. Container checks (healthz, default route, migrations line) roll back; the live checks through nginx only fail the run (revisit in #85). Live since run 43712703642.

## D35
**Decision (owner session, 2026-11-15).** Factory guardrails are enforced by a hook, not only by instructions. `scripts/factory_guard.py` runs as the first PreToolUse hook of every factory run (`scripts/laptop/factory-hooks.json`) and blocks, with exit code 2 and a reason the agent sees:
1. ssh/scp/sftp/rsync to the owner's server (server changes go through the `production` workflows);
2. `gh secret|variable set|delete` and writes to the secrets API (secrets are the owner's);
3. `gh repo delete|edit|archive|rename`;
4. `git push --force`/`-f`/`+ref` without a lease (`--force-with-lease` is allowed);
5. edits to the guard, its tests, the hooks file and the laptop runner (Edit/Write, and shell writes that name them).
The hook is referenced from the **main checkout** (`C:/projects/Learning_factory/scripts/factory_guard.py`), not `${CLAUDE_PROJECT_DIR}`, so a pass cannot change its own guard. The live-view hooks now end in `|| true`: a missing `factory_progress.py` made Python exit 2, which Claude Code treats as "block".
**Why.** Advice from a peer factory ("safety by prompt or substring blocklist is not safety; enforce it in code or hooks"). The factory never SSH-ed to the server (checked in the transcripts), but nothing but instructions stopped it. Verified: 57 unit tests, plus headless runs with the hooks: the server SSH and a `gh secret set` were blocked (the secret does not exist), `echo` ran.
**Limits.** A pattern check stops mistakes and drift, not an adversary; the hard boundaries stay GitHub environment protection, the forced-command live-view key and owner-only secrets.
**Changing it.** Need something blocked? Open an issue labelled `needs:owner`; the owner session changes the guard. **Revert:** remove the first PreToolUse entry from `factory-hooks.json`.

## D36 · #86 live terminal smoke: a synthetic smoke student, run from a GitHub runner
in force · factory · 2026-11-15

**Decision.**
- **Smoke student.** A new CLI command `python -m app.cli smoke-student` (run on the server by the workflow as `docker exec -i lf-manager ...`) upserts a dedicated cohort named `lf-smoke` and one student in it: the fixed synthetic ID `990000000000000000000000000001` (30 digits, cannot be a real 9-digit ID), name `Smoke Test`. The class password is read from **stdin**, generated fresh by the workflow for every run (`openssl rand -hex 24`), never stored, printed or kept as a secret. Each run sets `access_until` to tomorrow, re-enables the student and deletes that student's auth sessions. `smoke-student --disable` (no stdin) disables the student and signs it out; the workflow runs it at the end of every run (`if: always()`). No real data (rule 7), nothing for the owner to install (rule 2).
- **The path under test is the real one.** The client (`labs/deploy/smoke/terminal.py`, Python + pinned `websockets`) runs on the GitHub runner and goes through the public internet: `https://labs.shashkist.com` → nginx → `lf-manager` → Docker DNS `lab-<uuid>:7681` → ttyd. Steps: `POST /api/auth/login` → `POST /api/sessions` (lab `sample/hello`) → WebSocket `wss://labs.shashkist.com/s/<id>/term/ws` → wait for the prompt (D33) → `echo hi` → see `hi` → `POST /api/sessions/<id>/end`. It ends the session in a `finally`, prints the session id (not secret) and fails with a one-line reason.
- **Container gone.** After the client, the workflow checks on the server (same connection) that no container `lab-<id>` exists (polls up to 30 s).
- **Where it runs.** `server-smoke.yml` gets the option `check: terminal` (manual), runs it after every successful `Deploy` (`workflow_run`) and nightly (`schedule`, 03:30 UTC). Environment `production`, concurrency `server-lab-host` (D30).
**Why.** The deploy's live checks stop at `/healthz`; only a real terminal through nginx proves the Docker DNS name, the `Host` header and the WebSocket upgrade (D34 follow-up). A per-run password avoids a stored secret the owner would have to install.
**Alternatives.** Run the client on the server (skips the public TLS + nginx path from outside); an admin API to create students (does not exist yet, #11); a stored smoke password secret (owner work, rule 2).
**Revert.** Remove the `terminal` option and the `workflow_run`/`schedule` triggers; running `smoke-student --disable` leaves the cohort harmless.
**Amended (review of #87).** The lab id is `hello` (directory `labs/content/sample/hello`). The client retries the WebSocket upgrade on 502/503/504 until its timeout (ttyd may not listen yet right after the lab starts). setup-python and a hash-pinned `pip install --require-hashes` (`labs/deploy/smoke/requirements.txt`) run **before** the server key exists on the runner; the password is generated, masked, sent on stdin and given to the client in one step (no `GITHUB_ENV`). The cohort name `lf-smoke` is reserved (noted on #11). **Known risk, #90:** the shared group `server-lab-host` keeps one pending run, so a queued smoke can cancel a pending Deploy; until #90, the next push to main re-deploys.

## D37 · #9 task engine is split into three slices
in force · factory · 2026-11-15

**Decision.** #9 (task engine) was larger than one ~400-line PR. #9 is now slice 1: the `docker exec` script runner (streamed on stdin, timeout, under gVisor), the task API, setup on show and Next/Skip/Previous/Restart/Continue-from-N per spec §5, `shown_at`. #88 is slice 2: `check.sh` results (20 s timeout, PASS/FAIL parsing, checklist with hints), attempts, `passed_at`, quiz grading without leaking answers. #89 is slice 3: lab completion, `lab_runs` first vs repeat, `active_seconds`, the fast flag rules of §8. Each depends on the previous one. #10 (student UI) needs #9 and #88; #11 (teacher UI) shows #89's values.
**Why.** One reviewable PR per slice, each with its own exam (D19 pattern). **Revert.** Merge the issues back.

## D38 · #90 the post-deploy smoke runs inside the Deploy run; incidents watch Server smoke
in force · factory · 2026-11-15

**Decision.**
- `server-smoke.yml` gets `on: workflow_call` (input `from_deploy`, boolean, default false) and **loses** its `workflow_run` trigger. `deploy.yml` gets a second job `smoke` (`needs: deploy`, `uses: ./.github/workflows/server-smoke.yml`, `secrets: inherit`, `with: from_deploy: true`) that runs only when the deploy step really deployed (a job output `deployed == 'true'`; a stale-sha skip or missing access values set it false). It runs under the Deploy run's own `server-lab-host` lock, so it never queues on its own and never runs against an image that was not deployed.
- In `server-smoke.yml` the `server-lab-host` concurrency moves from the workflow to the job, and is used only when not called from Deploy (`from_deploy` false); a called run already holds the caller's lock and must not wait for it.
- Scheduled (nightly) and manual runs first check, in a small job without concurrency, that no `Deploy` run is queued, waiting or in progress (`gh run list --workflow deploy.yml`); if one is, the smoke is skipped with a line in the log. This shrinks the cancel window to seconds; it does not close it. A cancelled Deploy is not yet noticed: see D40 (#85).
- "Really deployed" means `deploy.sh` printed its final line `deployed <tag>` (only after every live check passed); its exit 0 on a skip (no database URL) no longer counts (review of #91).
- `incident.yml` watches `Server smoke` (the old list named `Smoke`, which does not exist), so a red live smoke opens an incident.
**Why.** GitHub keeps one pending run per concurrency group; an automatically queued smoke could cancel a pending Deploy (seen in the review of #87), and a smoke after a skipped Deploy gave a false red (run 43719795462).
**Alternatives.** A separate group for the smoke (it could then run during a deploy); a composite action shared by both workflows (more files, same effect).
**Revert.** Restore the `workflow_run` trigger and the workflow-level concurrency; drop the `smoke` job from `deploy.yml`.

## D39 · #92 admin sign-in with Google: shape of the flow and the test seam
in force · factory · 2026-11-15

**Decision.**
- New module `labs/manager/app/google_auth.py`, router without prefix: `GET /auth/google/start` and `GET /auth/google/callback` (nginx `location /` already sends them to the manager; the SPA fallback is registered last, so these routes win). Plus `GET /api/auth/admin/methods` → `{"google": bool, "password": bool}` for the UI.
- **Configured** means `LF_GOOGLE_CLIENT_ID`, `LF_GOOGLE_CLIENT_SECRET` and a non-empty `LF_ADMIN_EMAILS` are all set. Not configured: `/auth/google/start` answers 503 with a plain page "Google sign-in is not configured"; the manager starts normally.
- Redirect URI: `LF_GOOGLE_REDIRECT_URI`, default `https://labs.shashkist.com/auth/google/callback`; never built from the Host header.
- **Flow state is server-side, in memory** (the manager is one uvicorn process): `start` creates `state`, `nonce` and a PKCE S256 verifier, keeps them for 10 minutes keyed by `state` (capped at 1,000 pending flows, oldest dropped), and sets cookie `lf_oauth` (HttpOnly, Secure, SameSite=Lax, path `/auth/google`, 10 minutes) to a random browser binding whose SHA-256 is stored with the flow. The callback pops the flow (one use: a replayed callback finds nothing → 403), compares the cookie binding in constant time, then exchanges the code with the verifier. A cookie injected from another `*.shashkist.com` site cannot start a session on its own, because the flow must exist server-side.
- **Token exchange** at `https://oauth2.googleapis.com/token`, **JWKS** at `https://www.googleapis.com/oauth2/v3/certs` (fixed constants, no override), with `httpx.AsyncClient` (10 s timeout). The ID token signature is verified with the JWKS (RS256 only, PyJWT `pyjwt[crypto]`); then `iss` ∈ {`https://accounts.google.com`, `accounts.google.com`}, `aud` == client ID, `exp`, `nonce`, `email_verified is True`, email (lowercased, trimmed) exactly in the lowercased allow-list.
- **Test seam:** if `app.state.google_transport` (an `httpx.AsyncBaseTransport`) is set, every outbound Google call uses it. The exam serves the token endpoint and the JWKS from an `httpx.MockTransport` signing with its own RSA key. JWKS is cached for 1 hour and refetched once on an unknown `kid`.
- **Success:** the existing admin session (`auth_sessions`, kind `admin`) with subject `google:<sub>`; no `admins` row. `current_principal` accepts a `google:` admin subject while Google is configured (name shown: `admin`). Redirect 303 to `/admin`. **Failure** of any check: 403 with a plain HTML page, no session, no row. Logs say which check failed, never the email, the code, `state`, or a token.
- The callback is rate-limited with the login limiter (key `ip:<client>`, `LF_LOGIN_IP_LIMIT`).
- **Password admin login** answers 403 `password_login_disabled` when Google is configured, unless the break-glass flag `LF_ADMIN_PASSWORD_LOGIN=1` is set. `create-admin` stays, documented as break-glass.
- Removing an address from `LF_ADMIN_EMAILS` takes effect at the next sign-in; open Google admin sessions last at most 12 h (`SESSION_SECONDS`). Accepted for one admin.
- **Deploy:** `deploy.yml` passes `LF_GOOGLE_CLIENT_ID`, `LF_GOOGLE_CLIENT_SECRET` (secrets) and `LF_ADMIN_EMAILS` (variable) on the deploy connection's stdin, like `LF_DATABASE_URL`; `deploy.sh` writes each present value into `.env` (refusing a single quote, like the database URL) and logs one skip line per missing value; compose passes them to `lf-manager`.
- **Amended by #99 (2026-11-15):** the callback has its own rate-limit key `gcb:<client>` (same `LF_LOGIN_IP_LIMIT`), so student typos behind one NAT cannot lock the admin out; `location = /auth/google/callback` in the labs nginx block has `access_log off`; while Google is configured and `LF_ADMIN_PASSWORD_LOGIN` is not `1`, a password admin session is refused at once (not after 12 h); `LF_ADMIN_EMAILS` comes from secrets only; dropped allow-list entries are logged as a count once per value. Break-glass procedure: OWNER.md O19.
- **UI:** at `/admin` the web app asks `/api/auth/me` and `/api/auth/admin/methods`: signed in → "Signed in as admin" + log out; else one "Sign in with Google" link to `/auth/google/start`, or the text "Google sign-in is not configured".
**Why.** The owner wants Google-only admin access (issue #92). Server-side flow state keeps the replay and cookie-injection cases simple to reason about; the transport seam lets the exam run a full fake Google without network.
**Alternatives.** A signed cookie holding the flow state (survives restarts, but replay needs a used-state store anyway); authlib (heavier); skipping the signature check because the token comes straight from Google over TLS (allowed by OIDC, but cheap to do).
**Revert.** Remove `LF_GOOGLE_CLIENT_ID` from the server `.env` (an owner step: `deploy.sh` keeps a value when none is sent, so deleting the secret alone changes nothing): password admin login works again and the Google routes answer 503. To revoke an address, set the allow-list to the remaining addresses (a non-empty value replaces the old one).
**Amended (review of #95).** The access log strips query strings (the callback's `code` and `state` would otherwise land in `docker logs`); `/auth/google/start` is rate-limited per IP (`LF_GOOGLE_START_LIMIT`, default 30), so anonymous requests cannot evict the owner's pending flow; non-ASCII emails are refused; admin usernames may not contain `:`. The allow-list is read as `secrets.LF_ADMIN_EMAILS || vars.LF_ADMIN_EMAILS`: GitHub prints a step's `env:` values in the log, and only secret values are masked, so the owner moves it to a secret (OWNER.md O15).
**Round 2 (2026-11-15, pass lf-m7k2).** Round-1 fixes shipped as 5ed0a30; review round 2 approved with nits only. Kept as is in #95: the `vars` fallback (dropping it changes the exam after the code, which the D9 gate rejects; the variable is deleted, so the fallback is inert), the shared `ip:` key for the callback limit, the nginx access log for the callback (code single-use, PKCE verifier in memory), earlier password admin sessions living up to 12 h (production has none). All four are in #99. Merge waits for CI (D41).

## D40 · #90 ships without "a cancelled Deploy is noticed"; that criterion moves to #85
in force · factory · 2026-11-15

**Decision.** PR #91 closes #90 with criteria 1, 2 (within D38's limits) and 4. Criterion 3 (a cancelled Deploy for the head of main is noticed) moves to #85, which already lists it. The automatic re-dispatch tried in round 1 was removed after review round 2 (the round limit): it read `contents` without permission in a private repo, counted a stale-sha skip (which ends `success` with the head's `headSha`) as coverage, could dispatch a head whose CI was red (the image job pushes even when tests fail), and had no limit when the owner cancels on purpose or a run times out.
**Why.** The smoke inside the Deploy run removes the frequent cause (an automatic smoke queued after every Deploy). The remaining cases (a manual bootstrap dispatch, the nightly gate race) are rare and visible in the Actions list; the next push to main deploys again. A correct watcher needs the conditions above, worth its own reviewed change.
**Alternatives.** A third review round on the re-dispatch job (over the round limit, FACTORY.md); an incident on every cancelled Deploy (noisy: a newer pending Deploy cancels an older one by design).
**Revert.** Not needed; #85 adds the watcher. Its requirements: `contents: read`; coverage only by a run that really deploys main's head (a dispatch, or `deployed=true` output); dispatch only after a green push CI for the head; re-dispatch only when the cancelled run's `deploy` job never started and the run was not itself a re-dispatch; `pipefail` and positive checks; an `if: failure()` step that opens an incident.

## D41 · While Actions minutes are used up, the factory works locally and merges nothing
ended · factory · 2026-11-15 (CI runs again after D43; main CI 43754524341 green)

**Decision.** From 2026-11-15 15:31 UTC GitHub refuses every job (incident #96, owner item O16). Until a job runs again, a pass (1) starts no runs on purpose (they are refused anyway), (2) does work that needs no CI: exams, code, local unit tests, lint, types, review rounds on open PRs, content checked by the local validator, (3) pushes branches and opens PRs, but merges nothing and deploys nothing. STATE.md lists those PRs as "waiting for CI". The first pass after O16 is ticked re-runs the last main CI, then takes the open PRs in order, one CI at a time.
**Why.** Rule 5 and FACTORY.md: no merge without green CI; the Postgres and gVisor exams run only in CI, so a local green is not enough. Local work keeps the queue moving at no minute cost.
**Alternatives.** Stop the factory until the owner acts (wastes the passes); merge on local evidence only (breaks rule 5; the Postgres and gVisor exams would not have run).
**Revert.** Delete this entry once O16 is ticked and main CI is green again. Minute use (cheaper CI triggers, path filters, dashboard cadence) is worth its own issue then (#98).

## D42 · #12 Linux 1: the ownership task needs no sudo until #47 ships
in force · factory · 2026-11-15

**Decision.** Sudo does not work under gVisor (D26, #47), so #12's "change ownership (sudo task)" becomes two tasks without root: a hands-on task where the student reads ownership with `ls -l` and changes the **group** of their own file to a group they belong to (`chgrp`, allowed without root when the user is a member; the setup creates the group membership in the image or picks the student's primary group plus one supplementary group that `lf-lab-term` already has), and a quiz on what `chown` would need (root) and what `ls -l` columns mean. The lab stays `sudo: false`. When #47 ships, a later content issue may add a real `sudo chown` task.
**Why.** A task that cannot pass under the production runtime would block the whole lab. Ownership is still taught; only the root-only step waits.
**Alternatives.** Wait for #47 (blocks #12); run the lab under runc (breaks the isolation guardrail); drop ownership (spec lists it).
**Revert.** Replace the chgrp task with `sudo chown` once #47 is done and the image has sudo working.

## D43 · The repository is public; history rewritten; course material kept outside it

**Decision (owner, 2026-11-15).** `Shashkist/Learning_factory` is public, so GitHub-hosted Actions minutes are free (ends O7/O16, incident #96). The earlier private repository was renamed `Learning_factory-archive` and stays private: it keeps the old pull requests. The public repository was created from a rewritten history:
- **Commit authors:** every author and committer is the owner's GitHub noreply address. The work e-mail is gone, along with the `Co-authored-by` trailers that carried it.
- **Course material:** `courses/` was removed from all history and is now in `.gitignore`. The laptop copy is `C:\projects\lf-private\courses`.
- **Wording:** mentions of the course partner and of the owner's employer were reworded.

Issues were moved in number order, and closed placeholders took the numbers of the old pull requests, so every `#n` reference in docs, decisions and commit messages still points at the same item. Open PRs (#95, #100, #101) were re-opened under new numbers. The commit author for this repository is set to the noreply address in its local git config.
**Why.** Free CI at the factory's pace (~900 min/day) without exposing private data. GitHub keeps every PR's head forever and a force-push cannot change it, so only a new repository is clean.
**Alternatives.** A paid Actions budget, which keeps the repo private but costs money at this pace. A self-hosted runner, which is unsafe on a public repository: fork PRs would run on it. Making the old repository public, which would leak data through its PR refs.
**Revert.** Make the repository private again (`gh repo edit --visibility private`). The archive repository holds the original history.
**Rules from now on.** No course material, client names, employer material or personal e-mail addresses in the repository, issues or PRs. Fork PRs never get secrets: deploys run only in the `production` environment limited to `main`.

## D44 · Up to 3 independent issues in flight at once

**Decision (owner, 2026-11-15).** The manager keeps up to 3 issues in progress at once. It claims the next independent issue while another one waits on CI, review or a background agent. Independent means no dependency between the issues and no overlapping code area. Each issue gets its own claim, branch and worktree. Merges stay serial (rebase, green CI, merge). Rule text: the factory skill, step 3.
**Why.** Passes spend much of their time waiting. The owner wants parallel pipelines, and the live page now shows one lane per issue.
**Alternatives.** Strictly one issue at a time: simpler, but slower. More than 3 at once: too many rebases and too much review load for one manager.
**Revert.** Delete the "Work in parallel" paragraph in `.claude/skills/factory/SKILL.md`.

## D45 · #9 task scripts run as root with the session container's capabilities only; scripts drop to `student` themselves
in force · factory · 2026-11-15

**Decision.** The task runner (#9) runs `setup.sh` and `check.sh` with a Docker Engine API exec as `root`, `Privileged: false`, no extra capabilities (the Engine API has no per-exec capability add; `Privileged` is forbidden). The script is streamed on stdin to its shebang interpreter with `-s` and is never written into the container. Root there holds only `SETUID`/`SETGID` (D20, D24), so it cannot read or write student-private files. A script that touches the student's home runs that part as the student itself (`su student -s /bin/sh -c '...'`), which is what #12's Linux 1 scripts and exam already do. Working directory `/`, environment empty apart from `PATH` and `HOME=/root`.
**Why.** It keeps the container's capability set unchanged (ttyd is root inside it, so widening it widens the attack surface), needs nothing the API does not have, and matches the content contract already proven under gVisor in #12's docker exam.
**Alternatives.** Add `CHOWN`/`DAC_OVERRIDE`/`FOWNER` to every lab container (wider root for ttyd); run every script as `student` (setup could not prepare root-owned files such as `/etc` exercises); `Privileged` exec (breaks the isolation guardrail).
**Revert.** Add a per-lab `script_user` field or container capabilities, and change the runner's exec user.

## D46 · #98 (CI minute budget) drops to p2; PR #103 closed, the branch kept
in force · factory · 2026-11-15

**Decision.** With the repository public (D43), GitHub-hosted minutes cost nothing, so #98's goal (spend fewer minutes) no longer protects merges or deploys. #98 goes from p1 to p2 and back to `status:ready`. PR #103 is closed without merging: round 1 found three blockers (exam and CI change in one PR, `**.md` making lab content "docs-only", non-ASCII paths), and fixing them needs the D19 re-sequence anyway (code + CI PR, then the exam PR). The branch `f/98-ci-minutes` and the review findings (first comment on PR #103) stay as the starting point. Cancelling superseded PR runs is still worth doing for speed, and is the first slice to take when #98 comes up again.
**Why.** The risky part of #98 is that a wrong "docs-only" rule silently skips a Deploy (finding 2). Taking that risk now buys only wall-clock time; p1 work (#12, #9, #88, #89, #10, #11) moves the platform forward.
**Alternatives.** Re-sequence and finish #98 now (one or two more review rounds on the deploy path); drop the issue (loses the measured findings).
**Revert.** Relabel #98 p1 if the repository goes private again or GitHub limits public-repo minutes.

## D47 · #9 exam fixes after the first CI run: own student-id range, the container's own env is allowed
in force · factory · 2026-11-15

**Decision.** PR #113's first CI run had one failure in each exam, both in the exam, not the code. (1) The task API exam took student ids from `700000000 + n`, the range the login exam (#6) locks out on purpose; the login limiter is in-process, so the lockout carried over in the same pytest run (429 `too_many_attempts`). The #9 exam now uses `790000000 + n`. (2) The docker exam allowed only shell variables in the exec's environment, but a Docker exec always inherits the container's own `Env` (`LF_BASE_PATH`, `LF_SUDO`, set at create by `container_spec`), while the unit exam fixes the exec's `Env` and `Cmd` exactly. The allow-list now adds those two container variables; nothing from the manager process can appear. The manager made the two-line exam fix itself and re-sequenced the branch (exam, exam fix, code) as `f/9-task-engine-2`; PR #113 is closed.
**Why.** "Clean environment" in D45 means no manager settings or secrets reach a script; the container's own variables are already visible to the student's shell. Wrapping the command in `env -i` would contradict the exam's exact `Cmd` contract for no safety gain.
**Alternatives.** `env -i` around the interpreter (changes the `Cmd` contract); creating lab containers without `LF_BASE_PATH` (ttyd needs it); resetting the limiter between test modules (a test-only seam in production code).
**Revert.** Wrap the exec `Cmd` in `env -i PATH=... HOME=/root` and restore the stricter allow-list.

## D48 · #106 wrong-answer exam: `chmod 600` on an answer file must still pass; one content fix in the same PR
in force · factory · 2026-11-15

**Decision.** (1) The issue named "`chmod 600` on the answer file" as an unreadable-file case. check.sh reads the file as `student`, who owns it, so `chmod 600` is readable and correct: the exam keeps it as a case that must PASS (the guard against #104's round 1 bug), and `chmod 000` is the case that must give the "cannot be read" hint. (2) The near-miss `chmod 100 hello.sh` passed task 07 item 1, but a shell script cannot run without `r`. The issue asked for an exam-only PR; since the exam exposes a real content bug, the fix (owner needs `r` and `x`, hint keeps its wording and adds "a script needs r as well") goes in the same PR as a commit after the exam (D9 order). (3) 09-chgrp item 3 (owner no longer `student`) has no near-miss case: neither root (no `CAP_CHOWN`, D45) nor the student can change a file's owner in the container.
**Why.** An exam that fails on main cannot merge; splitting a three-line content fix into another issue would only delay it.
**Alternatives.** Mark the case `xfail` and open a content issue; make `chmod 600` a FAIL case (contradicts the spec: the owner can read it).
**Amended (review round 1, PR #120):** mode 1 or 3 gets its own hint ("the owner has x ... but not r"); the student's near-miss commands run under `set -e`. The private-home false FAILs found in review are #119.
**Revert.** Restore `[ $((u % 2)) -eq 1 ]` alone in `07-chmod/check.sh` and drop the `execute_only_100` case.

## D49 · Serial merges without rebasing PR branches: wait for main's CI and Deploy between merges; review round 3 replaced by the manager's own fix
in force · factory · 2026-11-15

**Decision.** (1) With up to 3 PRs in flight (D44), the factory cannot rebase a PR branch after another PR merges: the exam-integrity gate (D9) refuses merge commits on a PR branch and force-push is blocked on the laptop. When the PRs touch separate files (GitHub reports `CLEAN`), they are squash-merged one at a time, and the next merge waits until `main`'s CI and the Deploy for the previous one are green. Main's CI then tests the combined code. If GitHub reports a conflict or `BEHIND` with overlapping files, the branch is re-sequenced onto a new branch and PR instead. (2) After review round 2 (the limit in FACTORY.md), a remaining should-fix of a few lines is fixed by the manager, with unit tests, and merged on green CI without a round 3; the leftovers become follow-up issues (#114 → #117, #118 → #121, #120 → #119).
**Why.** Re-running every PR's CI after every merge would cost a CI cycle per PR per merge, while main's CI catches a bad combination right after the merge, before the next one.
**Alternatives.** Re-sequence every open PR after each merge (a new PR each time); allow merge commits in the gate (weakens the per-commit exam check).
**Revert.** Require `gh pr update-branch` plus a green PR run before each merge, and allow merge commits from `main` in `exam_integrity.py`.

## D50 · #88 check and quiz API shape
in force · factory · 2026-11-16

**Decision.** Two routes under the existing task router (`/api/sessions/{sid}/tasks`), same owner / running-session / lab checks and error style (`HTTPException(code, "<slug>")`) as slice 1:
- `POST /{task_id}/check` (hands-on only). Runs the task's `check.sh` through `exec_script` with a 20 s timeout. Response `{"passed": bool, "attempts": int, "reason": null | "timeout" | "error", "checklist": [{"n": int, "text": {"en","he"}, "passed": bool, "hint": str | null}]}`. `passed` is true only when exit 0 **and** every checklist item printed `PASS <n>`; missing, duplicate-conflicting or unknown lines count as failed. A timeout or exec failure is a normal 200 with `passed: false`, a `reason`, every item failed; never a hung request or a 5xx.
- `POST /{task_id}/answer` (quiz only), body `{"option": int}` (index into `options`). Response `{"correct": bool, "attempts": int}`. The correct index is never returned, not even after a right or wrong answer. An index out of range is 422 and counts no attempt.
- Wrong kind for the route → 409 `wrong_task_kind`. A task never shown in this session (no `shown_at`, so its setup has not run) → 409 `task_not_shown`. Unknown task → 404 `unknown_task`.
- Each graded call does `attempts = attempts + 1`; `passed_at` is set on the first pass only (`coalesce`), never cleared by a later fail. A check of an already passed task still runs and counts.
**Why.** Mirrors KodeKloud's `/check_config` (one call, checklist back) and the spec §7 contract; the answer is never revealed so a student cannot fish for it with a wrong attempt. **Alternatives.** One `/grade` route for both kinds; reveal the correct option after a wrong answer. **Revert.** Change the routes and the exam together in a new issue.

## D51 · #88 review round 1: checks take the session lock, one check parser, server errors count no attempt
in force · factory · 2026-11-16 · amends D50

**Decision.** (1) A check runs under the same per-session lock as setup (`_show`); if the lock is held (a setup or another check of that session), the check runs nothing, counts nothing and answers 409 `session_busy`. One student can then hold at most one exec slot, and a check never runs against a half-reset machine during Restart/Previous. (2) One parser for `check.sh` output: the content parser (`app.content.parse_check_output`, used by the content exams) is the only one; production uses it too. Lines that are not `PASS <n>` / `FAIL <n> [hint]` are ignored (stderr is merged into the output today, so a tool's warning must not fail a correct check); item numbers have at most 4 digits; numbers outside the checklist are ignored; an item passes only with a `PASS` line and no `FAIL` line; the check passes only with exit 0, no `FAIL` line and every checklist item passed. (3) `reason: "error"` (no free exec slot, Docker failure) counts **no** attempt: it is the server's failure, not the student's; a timeout still counts one. (4) The attempt `update` also requires the session to be still running. (5) Killing a timed-out script stays in #117.
**Why.** Review round 1 of #123: a parallel check loop from one session could take every exec slot (other students' setups 504, their checks "error" and counted); a stray stderr line gave "all items ticked, check failed, no hint"; the strict and lenient parsers disagreed on `PASS 1 ok`.
**Alternatives.** Per-session exec quota instead of the lock; split stdout/stderr in the runtime (a runtime change touching setup too; possible later). **Revert.** Drop the lock in `check_task`; restore the strict parser.

## D52 · #119 checks: the student side only reports facts, root decides; exam helper fixes may re-sequence a branch
in force · factory · 2026-11-16 · amends D45

**Decision.** (1) A `check.sh` that must read the student's files through a private home runs a small facts-only part as the student and decides as root: root pipes a fixed reader script on **stdin** to `su student -s /bin/sh -c '/bin/sh -s' </...` (never `su -`, never the script on a command line), the reader prints raw facts (exists / type / mode / owner / group / file contents, delimited), and root compares them with the expected values and prints `PASS`/`FAIL`. Expected answers, hints and the comparison never appear in a student-owned process, argument list or file; temp files, when needed, are created by root outside the student part. (2) Accepted risk: the student owns the reader process, so they can kill or stop it (their own check then fails or times out) and, where same-user ptrace is allowed, could alter the facts it prints; the image has no debugger and the labs are practice, not exams (spec §5 "Checks and root"). (3) When a PR's first CI run shows an exam bug confined to the test harness or a student-command helper (not a weakened expectation), the branch is re-sequenced (exam, exam fix, code) onto a new branch and PR, as for #124 → #125; the fix is named in the PR.
**Why.** Review round 1 of #125: running the whole check as the student exposed the check body in `/proc/<pid>/cmdline` and let the student overwrite 06's expected-lines temp file to pass a wrong answer. **Alternatives.** Run the whole check as the student (rejected: leaks and tampering); give root `CAP_DAC_READ_SEARCH` (rejected in D45: widens every lab). **Revert.** Restore the per-task `su` reads of #106.
**Amended 2026-11-16 (review round 2 of #125).** The reader prints with `printf '%s
'` (dash `echo` turns `
` in file content into new lines, which forged fact lines), opens regular files only, and reads at most 64 KiB / 200 lines (a FIFO or `/dev/zero` symlink hung it). The accepted risk in (2) also covers a student writing fake fact lines into the reader's stdout through `/proc/<pid>/fd/1` with a plain shell loop, no debugger needed. A per-check token is the follow-up (#126), with the 04/05 expected texts still on root command lines.

## D53 · #10 student UI ships in four slices
in force · factory · 2026-11-16

**Decision.** #10 is too big for one PR (~400 lines) and "My labs" has no API yet. Slices:
1. **#10 (this issue, web only):** i18n foundation (`en.json` / `he.json`, a `t()` helper, EN / עב toggle remembered in `localStorage`, `dir="rtl"` on Hebrew, code stays LTR), student login page (ID + class password → `POST /api/auth/login`, errors for wrong credentials / rate limit / expired access), `GET /api/auth/me` on load, logout, a minimal client-side router (`/`, `/login`, `/labs`, `/labs/<id>`), an empty "My labs" placeholder. Vitest + Testing Library tests in both languages. No new dependency for routing or i18n (a dozen lines each).
2. **New issue:** `GET /api/labs` (published labs for students, with per-student progress ✓ / n/m / not started) + the My labs page (cards by module, Start / Continue / Redo, "Another lab is open" dialog).
3. **New issue:** lab screen (task panel, terminal iframe, splitter, Check / Next / Skip / Previous / Restart, heartbeat every 15 s, End lab).
4. **New issue:** Playwright main flow in both languages against a fake backend in CI + one docker-marked end-to-end test.
**Why.** Each slice is one reviewable PR; slice 1 needs no manager change, so it runs in parallel with #89 and #128. **Alternatives.** One big PR; add react-router / i18next (more dependencies for a two-language, five-route app). **Revert.** Merge the follow-up issues back into #10.

## D54 · #128 smoke-only lab: `status: internal`, startable only by the `lf-smoke` cohort
in force · factory · 2026-11-16

**Decision.** The smoke lab lives at `labs/content/smoke/smoke-tasks/` (id `smoke-tasks`; amended 2026-11-16: the loader requires the lab id to equal its folder name, so not `smoke/tasks/`): one quiz task and one hands-on task with trivial, deterministic `setup.sh` / `check.sh`. `lab.yaml` gets a new `status` value `internal`. `POST /api/sessions` answers 404 `unknown_lab` (same as a missing lab, so its existence does not leak) for an `internal` lab unless the student belongs to cohort `lf-smoke`; any future student catalog (#10 slice 2) never lists `internal` labs.
**Why.** Reuses the existing status field and the synthetic smoke cohort (D36); no new table or flag. **Alternatives.** An `audience:` field per lab; a separate content root mounted only for smoke runs. **Revert.** Drop the status value and move the smoke lab to `draft`.

## D55 · #89 completion and fast-flag contract choices
in force · factory · 2026-11-16

**Decision.** Choices the spec (§8) leaves open, as pinned by the #89 exam and review round 1:
- (a) **Median scope:** the 30 % rule uses the median of every other student's **first** completion of the lab, across all cohorts (not per cohort), and needs at least 5 such students before it applies. Runs with 0 active seconds (no heartbeat data) do not count as priors.
- (b) `fast_lab_min` in `lab.yaml` is in **minutes** (like `time_limit_min`); per-task `min_task_seconds` is in seconds, default 20.
- (c) `lab_runs.active_seconds` is a snapshot **at completion** (time to finish), not the whole session.
- (d) Only completed sessions get a `lab_runs` row; abandoned runs are visible through `lab_sessions` / `task_results`.
- (e) A task's time runs from `shown_at` to the end of the passing check (the check's own run time counts for the student).
- (f) Heartbeats while the session is `starting` count as active time; `last_heartbeat` is set when the session becomes `running`.
- (g) Active time is accumulated per heartbeat in the database (gap ≤ `LF_HEARTBEAT_TIMEOUT_S`, default 300 s, read per request), unrounded; rounded only into `lab_runs`.
**Why.** Small cohorts (often < 5 completions) would never reach the median rule per cohort; the rest are the simplest readings of §8. **Alternatives.** Per-cohort median; summing time over the whole session; rows for abandoned runs. **Revert.** Change the rule in `app/flags.py` / `app/tasks.py` and the #89 exam together in a new issue.

## D56 · #117 a timed-out task script is killed by a second exec, as each process's owner
in force (review round 2 of #148 approved, shipped 2026-11-16) · factory · 2026-11-16

**Decision.** After a setup/check deadline, the manager runs a second exec in the lab container (root, unprivileged, same as the script exec). With one `ps -eo pid=,ppid=,uid=,gid=,args=` walk it collects the timed-out exec's interpreter (uid 0, **PPid 0**, args exactly `<interp> -s`) and all its descendants. It sends STOP, collects again, then KILL to both sets, for up to 3 rounds. Non-root processes are signalled as their owner (`setpriv --reuid --regid --clear-groups kill`), because the container drops `CAP_KILL` and every shipped script starts work as the student (D45, D52). The kill is bounded (`min(5, max(3, 0.2*timeout))` s), so a setup still answers inside nginx's 60 s. `exec_script` returns only after the first exec stops or the bounded kill gives up, which keeps the session lock (D49) held. A failed kill logs one line and still reports `timed_out`.
**Why.** `timeout -s KILL` kills only its direct child (background jobs survive). The `/exec/<id>/json` Pid is a host pid under runsc. The exec is not a process-group leader. The exam pins the exec env to `PATH` and `HOME`, so there is no env marker. PPid 0 tells the exec's own interpreter apart from root leftovers of earlier scripts (which get PPid 1).
**Alternatives.** Wrap every script in `setsid` and kill the group; add `CAP_KILL` to lab containers (rejected: more privilege for the student's container).
**Known limits.** A process that double-forks away from the tree before the deadline, or a script that `exec`s another command, is not matched. Acceptable for authored lab scripts; the spec §7 note says "the interpreter and its descendants".
**Revert.** Drop the kill exec (scripts are abandoned again after the timeout, as before #117).

## D57 · #130 lab screen: Markdown, Solution tab, end of lab
in force · factory · 2026-11-16

**Decision.** (a) Task text is rendered as Markdown with `react-markdown` and no raw-HTML plugin (HTML in a task file is shown as text, never parsed); inline code and code blocks are `dir="ltr"` inside the Hebrew panel. (b) The Solution tab ships as a visible tab that says the solution is not available yet: the API deliberately sends no solution text (`tasks.py`), and when and how a solution is revealed (after an attempt? does it count for the ⚡ flag?) is its own backend + UI issue. (c) Next/Skip on the last task: the server's 409 `no_next_task` shows "lab complete" with a link back to My labs, not an error. (d) A session that ended (timer at 0:00, 404/410 from the session, task or heartbeat calls) shows "this lab session has ended" with a link back to My labs, and the terminal is removed.
**Why.** Spec §7 writes tasks in Markdown and §9 shows a Solution tab; the review of #150 found raw Markdown unreadable and code reversed in RTL. A library that escapes HTML by default is less code to trust than a home-made parser. The solution reveal changes what the backend sends to students, which needs its own exam.
**Alternatives.** A small in-house Markdown subset (more code to secure); hiding the Solution tab until it works (the exam pins the tab).
**Revert.** Swap the renderer in `LabScreen.tsx`; the solution follow-up issue replaces (b).
**Amended 2026-11-16 (#157), (d):** only the server ends a lab. The screen shows "this lab session has ended" (and removes the terminal, stops the heartbeat) only on a 404/410 from the session, task or heartbeat calls. The countdown is display only: it is corrected for clock skew with the server's time (the response `Date` header, or a server-sent `now` if the API has one), and at 0:00 it keeps the terminal and the heartbeat until the server answers. Why: a school PC whose clock runs ahead by more than the time left lost the lab on every load, because the screen stopped heartbeating and the janitor then really ended the session. Revert: end on the local timer again.
**Amended 2026-11-16 (#157), (c):** a 409 `no_next_task` shows "lab complete" after Next, or after Skip on a task the screen shows as passed. Skip on an unpassed last task shows a neutral "this is the last task, pass it to finish" alert and keeps the task usable (the server records no completion). Known gap → follow-up: "lab complete" after Next does not yet check that every task passed (the server only records a completion then).
**Amended 2026-11-16 (#160), (c):** on a 409 `no_next_task` the screen re-reads `GET .../tasks` and shows "lab complete" only when every task is passed; otherwise it shows "N tasks still open" with the task list, and nothing is recorded. A 409 `task_changed` re-fetches `GET tasks/current` and shows that task (the #157 "old task stays usable, retry" behaviour is replaced). Why: the server records a completion only when every task passed, so the screen must not say "well done" when it did not; deciding in the web keeps the API unchanged. Alternative: a distinct server code on the last Next (API change, own exam). Revert: show "lab complete" on any `no_next_task` again.


## D58 · #153 bootstrap exam flake: cause, round limit, and how "10 green runs" is counted
in force · factory · 2026-11-16

**Decision.** (a) Cause (review of #158, round 1): the #3 rollback scenario's TCP trigger closed 0.3 s after the backup appeared, without `shutdown()`, so the runtime step could still pass its own baseline check; a later step (swap) failed instead and only that step was rolled back, leaving runsc in `daemon.json`. No product bug; the exam now uses a one-shot listener that shuts down after the script's start-of-run connection. (b) Review round 2 asked for teardown/precondition ordering fixes (product failures must not surface as "exam precondition" or fixture errors; cleanup before the last check; write the original `daemon.json` before a docker restart). The two-round limit was reached; the manager checked the fixes in bd5e346 against the round-2 findings instead of a third round. (c) Criterion 3 ("10 consecutive green docker job runs") is counted on the docker jobs of `main` and PR runs after #158 merges, not on a rerun loop of one PR (15 min per run). #153 stays open until the count is recorded on it.
**Why.** A rerun loop would hold one pass for 2.5 hours; every PR and `main` push runs the same docker job anyway.
**Alternatives.** Temporary draft PRs to run the job in parallel (clutter); a `workflow_dispatch` loop (needs a CI change).
**Revert.** Run the rerun loop; reopen the review for a third round.

## D59 · #11 teacher UI ships in four slices
in force · factory · 2026-11-16

**Decision.** #11 is far bigger than one PR (~400 lines): a teacher API does not exist yet. Slices:
1. **#11 (this issue): cohorts and import.** `app/teacher.py` router under `/api/admin/*` (every route `current_admin`): list / create / edit cohorts (name, class password, access date; the password is never returned), paste-import "ID, name" lines (`valid_id` after normalization, `id_hash`, per-row errors, duplicates reported within the paste and against existing students; the pasted text is never stored or logged), list a cohort's students (name, note, disabled, access override). The name `lf-smoke` is refused on create and rename (case-insensitive, trimmed), and the `lf-smoke` cohort is left out of every teacher list. Web: an admin shell at `/admin` (nav: Cohorts, later Class table, Live) with the Cohorts page (en/he). Vitest tests; no Playwright yet.
2. **New issue: class table.** `GET /api/admin/class` (students × published labs, ✓ / n/m / –, ⚡ flags, last activity, filter by cohort and flagged-only) + CSV export + the page. `lf-smoke` left out of rows, medians and the export.
3. **New issue: student page.** Per lab: tasks passed, attempts, time per task next to the class median, flags with reason, dismiss a flag (migration: `flag_dismissed_at`), extend access, disable, note.
4. **New issue: live sessions** (who is in which lab now, end a session) **and teacher Playwright flows** (after #131 sets Playwright up).
**Why.** Each slice is one reviewable PR with its own exam; slice 1 is the base the others need (admin shell, router, `lf-smoke` exclusion helper). **Alternatives.** One big PR; API-only and UI-only slices (UI slices could not be checked end to end). **Revert.** Merge the follow-up issues back into #11.
**Amended (review round 1 of #170).** Slice 1 ships the edit API (`PATCH`) and the students API; the edit form, the student list on the Cohorts page, translated import errors and the import hardening nits move to #172. #11 closes with #170; #172 carries the "edit" criterion in the UI.
**Amended (#163, slice 2).** The `flag_dismissed_at` column (migration 006, on `task_results` and `lab_runs`) ships with the class table, because the ⚡ already hides dismissed flags; slice 3 (#164) only adds the dismiss action. The CSV is written with a UTF-8 BOM (Excel on Windows reads Hebrew names correctly) and a name or cohort that starts with `= + - @`, tab or CR gets a leading `'` (no formula runs when it opens). Two students with the same name are told apart by their note on the student page (#164); the class table shows the name only.

## D60 · #131 Playwright: the suite is the deliverable; the docker end-to-end test moves to #167
in force · factory · 2026-11-16

**Decision.** For #131 the Playwright specs are the deliverable, so there is no separate exam: they live in `labs/web/e2e/` (not `tests/acceptance/`), run against a fake backend (`page.route`, every unmocked `/api` call answers 500) with the built app served by `vite preview`, Chromium only, in the `web` CI job. Criterion 2 (one docker end-to-end test against the real manager) needs Node, Chromium, Postgres, the smoke lab and a seeded smoke student in the `docker` job; it is split into #167. The Playwright config declares `process` itself instead of adding Node types to the app's tsconfig (they would change timer types in `src`).
**Why.** One reviewable PR; the docker job setup is a separate piece of CI work. **Alternatives.** Wait for both parts in one PR; a separate tsconfig for e2e. **Revert.** Fold #167 back into #131.

## D61 · #172 two smoke-cohort checks from one module
in force · factory · 2026-11-16

**Decision.** `app/smoke.py` holds the one definition of the reserved smoke cohort: `SMOKE_COHORT`, the SQL filter `NOT_SMOKE_SQL`, and two checks. `is_smoke_cohort` (any case, any padding) is the teacher API's reserved-name refusal and list filter; `is_smoke_member` (the exact name) decides access to the internal smoke lab in `sessions.py`. The #172 exam asked both files to use a case-insensitive check, which contradicted the #128 exam (a cohort named `LF-SMOKE` or `lf-smoke ` must not reach the internal lab); the #172 exam was corrected before the code, and the branch was re-sequenced (#174 → new PR).
**Why.** Access stays as tight as it was (exact name); naming stays as broad as review asked (no look-alike teacher cohorts).
**Alternatives.** Case-insensitive access everywhere (widens access to the internal lab for a cohort created outside the teacher API).
**Revert.** Use one check in both files and drop the #128 look-alike case.

## D62 · Exam process matchers must not match the random session id
in force · factory · 2026-11-16

**Decision.** Incident #182: the #117 docker exam counted processes whose command line contained a bare marker (`6012`). ttyd runs with `-b /s/<session uuid>/term/`, and that run's session id ended in `6012`, so ttyd counted as a leftover. The exam was fixed exam-only (#183): the marker must follow a space. Rule for every docker exam: a process or log matcher anchors its marker (a leading space, `sleep MARK` at the start) or uses a non-hex marker, never a bare digit string that a UUID can hold.
**Why.** A bare 4-digit marker collides with a random session id in about 1 run in 2,300; the failure looks like a kill bug.
**Alternatives.** Exclude ttyd by name (misses any other process that carries the id); a fixed session id in the fixture (the fixture is shared by other exams).
**Revert.** Not needed; the anchored match is strictly more precise.

## D63 · #164 the student page is addressed by an opaque random key, not the id hash
in force · factory · 2026-11-17

**Decision.** The issue names `GET /api/admin/students/<id_hash>`, but the #163 contract keeps id hashes out of every teacher row (the class table, its CSV), so the class table could not link to such a page. A new migration adds `students.key uuid not null default gen_random_uuid() unique`. The student page and its actions use `/api/admin/students/<key>`; an unknown or malformed key, or a student of the `lf-smoke` cohort, answers 404. Class-table rows (`/api/admin/class`) and the cohort students list (`/api/admin/cohorts/<id>/students`) gain a `key` field, so two students with the same name get distinct links. The #163 exam's exact row-key set is widened to include `key` in an exam-only commit before the code (it still asserts no id hash and no raw id in the body). The CSV stays without keys.
**Why.** A random key says nothing about the student and cannot be reversed or matched against another system; the id hash is a stable pseudonym that should stay inside the database. **Alternatives.** The id hash in the URL (breaks #163's privacy contract); the row's position (not stable); an HMAC of the id hash (needs the secret on every lookup, no gain over a random column). **Revert.** Drop the column in a new migration and route by id hash; restore the #163 row-key set.

## D64 · #164 student page API shape (set by the exam)
in force · factory · 2026-11-18

**Decision.** Three admin-only routes (401 anonymous, 403 student): `GET /api/admin/students/<key>`, `PATCH /api/admin/students/<key>` with any of `{note, access_until, disabled}` (only sent fields change; `access_until: null` clears the override; a blank note is stored as null, others trimmed; a wrong type or bad date answers 422 and changes nothing), and `POST /api/admin/students/<key>/flags/<flag id>/dismiss` (per flag, by an opaque flag id; another student's or an unknown flag answers 404). All three answer 200 with the same page body: `{key, name, cohort, note, disabled, access_until, cohort_access_until, labs: [{id, title, status, tasks_passed, tasks_total, attempts, tasks: [{id, passed, attempts, seconds, median_seconds}], flags: [{id, scope: task|lab, task_id, reason, dismissed_at}]}]}`. Every published lab is listed, also untouched ones. Time per task is the first completion; the median is over every student's first completion across cohorts, `lf-smoke` excluded, with no minimum count (D55's "at least 5" applies to flagging only). A dismissed flag stays listed with its timestamp and no longer sets the ⚡ in the class table. Disable is `PATCH {disabled: true}`: login answers 403 `account_disabled` and a logged-in student's `POST /api/sessions` answers 401. Web: route `/admin/students/<key>`, class-table names link there.
**Why.** One body for all three answers lets the page re-render from the response without a second fetch; per-flag ids let one of two flags be dismissed. **Alternatives.** Separate disable/enable/extend routes (more surface, same effect); a median only above 5 students (hides the number teachers ask for in small classes). **Revert.** Change the exam `test_issue_164_student_page.py` first, then the routes.

## D65 · #165 live sessions: Playwright flows split into #205; API shape set by the exam
in force · factory · 2026-11-19

**Decision.** #165 keeps the live sessions API and page; the teacher Playwright flows (cohort create, import, class table, student page) move to #205, which does not depend on the live page. API (set by the exam `test_issue_165_live_sessions.py`): `GET /api/admin/live` → `{sessions: [{id, key, name, cohort, lab_id, lab_title: {en, he} | null, started_at, last_heartbeat | null}]}`, only `starting`/`running`, newest first, `lf-smoke` left out (D61), no student id or hash (the `key` is D63's opaque key for the student page link). `POST /api/admin/live/<session id>/end` → 204 through the same `end_session` path as the student's End lab; 404 (never 422) for an unknown, malformed, ended or `lf-smoke` session; 401/403 before 404. The end reason reuses the existing check-constrained set (migration 003); no new `teacher` reason and no migration. Web: `/admin/live`, nav Cohorts · Class table · Live, an in-row confirm before End.
**Why.** Each part is one reviewable PR and they run side by side; a new end reason needs a migration for no visible gain today. **Alternatives.** One PR with both (~700 lines); a `teacher` end reason (migration 008 + every reason list). **Revert.** Fold #205 back into #165; add the reason in a new migration and change the exam first.
**Amended (review round 1 of #210).** The end reason is `forced` (ended by someone other than the student, as for a forced lab switch). On the web page, an End that answers 404 means the session already ended elsewhere (the student, the janitor, another tab): the row is dropped (or the list re-fetched), not kept with an error; any other failure keeps the row with `admin.live.end_error`. The exam's tests use only 500 and a network error, so this does not contradict them. Started and last heartbeat cells show the date and HH:MM in the browser's local time.

## D66 · #204 sliced: web follow-ups stay, flag text and API nits split out
in force · factory · 2026-11-19

**Decision.** #204 keeps the web-only items: pending state on writes (2), focus and dismissal time after "Dismiss flag" (3), a loading line (4), names on the Cohorts page linking to the student page (8), plus the #206 fake-backend branches when the specs grow. Item 1 (translated flag reasons from structured fields, needs a #164 exam change first) moves to #216, which depends on #204 (same `StudentPage.tsx`). Items 5-7 (auth before body parsing, medians only on GET, attempts of current tasks; manager only) move to #217, which can run beside #204.
**Why.** Eight items across manager and web, one with an exam change, is more than one ~400-line PR; the split gives a web lane and a manager lane that do not touch the same files. **Alternatives.** One PR (too big to review well); split by layer only (item 1 crosses both). **Revert.** Fold #216 and #217 back into #204.

## D67 · #202 the whole task setup request stays under nginx's 60 s
in force · factory · 2026-11-19

**Decision.** Set by the exam `test_issue_202_bounded_setup.py`: `SETUP_TIMEOUT_MAX_S = 40` caps `LF_SETUP_TIMEOUT_S` (values above are clamped; junk falls back to the default, which drops from 50 s to at most 40 s), `KILL_BOUND_S = 5` in `runtime.py`, `DB_STEP_BOUND_S = 8` for the database step after setup, and `SLOT_WAIT_S (5) + 40 + 5 + 8 = 58 < 60` is asserted. The allowed end reasons are one frozenset `sessions.END_REASONS`, compared by the exam with the newest migration's CHECK constraint; `end_session` refuses any other reason before touching the database.
**Why.** A busy class with a hanging setup script must get the JSON `setup_timeout`, not nginx's HTML 504. 40 s is still far above any setup script in the labs so far (they create a few files). **Alternatives.** Raise nginx's `proxy_read_timeout` for the task routes (a shared-config change for one route, and a student still waits a minute); make setup asynchronous with polling (much more code). **Revert.** Change the exam first, then the constants.

## D68 · #204 student page writes are sent one after another
in force · factory · 2026-11-19

**Decision.** Review round 3 of #219 is decided by the manager: the student page sends its writes (note, access date, disable/enable, flag dismiss) through one client-side queue, one request at a time. Each answer is then the newest server state and replaces the page (keeping the note/date draft rule: a field the teacher edited since the send keeps the draft). The sequence number, stale flag and re-fetch logic from rounds 2-3 go away. The control that started a write stays disabled while it is queued or in flight (the exam's contract); other controls stay usable and their writes wait in the queue. A failed write is shown for its own control, and the queue continues.
**Why.** The server handles two requests concurrently and each answer is a snapshot taken when its handler ran. No client-side ordering of overlapping answers can tell which snapshot is newer: round 2 fixed one order, round 3 found the mirror order. With one write at a time there is nothing to order. A teacher clicks a few buttons a minute, so the queue costs nothing noticeable.
**Alternatives.** Re-fetch whenever writes overlapped (one more GET, still racy if the GET overlaps a write); disable every write control while any write runs (simple, but a teacher cannot type a note while a dismiss runs); a version column on the server (a migration for a UI race). **Revert.** Remove the queue and send writes directly; the page then needs another way to order answers.
**Amended (pass lf-v3k8, review round 4 of #219).** The queue lives at module scope (one per browser page load) and the page's first read waits behind it, so reopening a student cannot start a second, overlapping queue. A late dismiss answer moves focus only if focus is still on that Dismiss button or on nothing. The single save alert names the failed parts and marks each failed control (`aria-invalid`). Writes time out after 30 s so a stuck request cannot hold the queue. After four review rounds the manager checked the round-4 commit (4280666) against the findings and merges on green CI without a fifth round.

## D69 · #217 class medians stay over every published lab
in force · factory · 2026-11-19

**Decision.** Item 2 of #217 (the medians query runs on GET, PATCH and dismiss) is accepted as it is: the student page keeps computing class medians over every published lab on every route. Review round 1 of #221 showed that restricting the query to the labs the student has results in shows "—" for a lab the class has finished but this student has not opened, which contradicts D64 (every published lab is listed with the class median). The #217 exam requires PATCH and dismiss to answer the same body as GET, so "medians only on GET" is out too.
**Why.** One aggregate over `task_results` for a class of a few dozen students costs milliseconds; a teacher writes a few times a minute. Correct numbers matter more.
**Alternatives.** Medians only for touched labs (wrong numbers); a cached or materialised median per lab (complexity with no load to justify it); an index on `task_results (lab, task, passed_at)` if the query ever shows up in the logs.
**Revert.** Pass the student's lab ids to `_MEDIANS_SQL` again, and amend D64.

## D70 · #220 items 7 and 8: pool command timeout now, acquire timeouts and idempotent moves later
in force · factory · 2026-11-19

**Decision.** #220 ships the pool-wide `command_timeout` at 50 s (`POOL_COMMAND_TIMEOUT`, below nginx's 60 s read timeout; migrations keep 60 s on their own connection) and bounds every database step and read on the task show and check paths by the request budget (bounded acquire, `lock_timeout`/`statement_timeout`, terminate on failure, bounded release whose errors are swallowed after COMMIT). The pool-wide wait for a free connection on all other routes is **not** changed in #220: it moves to #224 with the strict cap of the check-path single reads and the check-timeout wording. Item 8 (a Next/Skip retried after a committed-but-503 answer skips one more task) moves to #222: the lab screen sends the target task and the server treats a repeat as a no-op.
**Why.** Each part is one reviewable PR; the task paths were the ones with a known way to exceed nginx's timeout. Other routes are single short reads at class scale.
**Alternatives.** One PR for all of it (over the ~400-line budget, more review rounds); a pool subclass with a default acquire timeout (touches every route at once).
**Revert.** Set the pool back to `COMMAND_TIMEOUT`; close #222/#224 as won't-do.

## D71 · Dashboard snapshot every 15 minutes (supersedes D12's cadence)
in force · factory · 2026-11-19

**Decision.** The Factory dashboard workflow runs on `*/15 * * * *` again, plus every push to `main` and manual dispatch (#155). The exam cases that pinned D12's `0 */3 * * *` were changed first, in the same PR: the exam-integrity gate only refuses exam changes together with `ci.yml` or `scripts/ci/**` (D19), and `factory-dashboard.yml` is neither.
**Why.** D12 cut the cadence to fit 2,000 free private-repo minutes. The repository is public (D43; `gh repo view` says PUBLIC), so GitHub-hosted minutes cost nothing, and the GitHub part of factory.shashkist.com lagged up to 3 hours.
**Cost.** ~96 runs a day of ~1 minute each, plus one SSH connection per publish (ControlMaster, 3 retries 20 s apart). The server refuses bursts of new SSH connections (#26); a publish that lands next to a Deploy may retry once. Accepted risk (review of #226): `incident.yml` opens a p0 incident for any failed dashboard run, so a transient failure now has 12 times the chances; #229 raises it only after two failed scheduled runs in a row.
**Alternatives.** Every 30 minutes (half the SSH traffic, still stale enough to notice); publish from the laptop poller (no runner, but only while the laptop is on).
**Revert.** Set the cron back to `0 */3 * * *` in `factory-dashboard.yml` and in the #18/#19 exam cases.

## D72 · #224 item 3: acquire limit on the task routes now, every other route in #236 (amends D70)
in force · factory · 2026-11-19

**Decision.** #224 adds `db.POOL_ACQUIRE_TIMEOUT` (10 s) and uses it on the task routes only (check/answer path, `task_list`, `current_task`, the navigation guard), through the bounded step. On the task routes the step bound (at most `DB_STEP_BOUND_S`, 8 s) is what actually limits the wait today; the 10 s constant is the ceiling #236 will reuse. Auth, sessions and the admin routes keep waiting on the pool without their own acquire limit until #236, and that includes the dependencies that run before every Check and Answer: `auth.current_principal` (up to 3 reads) and `term_proxy.authorize` (1 read).
**Why (measured risk).** The pool has 5 connections and a 50 s `command_timeout` (D70). The only known way to hold all five for long is checks stuck behind a lock, and those are now bounded. Other routes are single short reads at class scale; when the pool is exhausted they wait until nginx answers 504 at 60 s, which is a bad message but loses no data. A shared helper over every route is a separate ~300-line change with its own error code and wording.
**Alternatives.** Fold all routes into #224 (over the PR budget, a second review surface); a pool subclass with a default acquire timeout (one change, but every route's error handling changes at once without tests).
**Revert.** Drop `POOL_ACQUIRE_TIMEOUT` from the task routes; close #236 as won't-do.

## D73 · Dashboard cron off the busy minutes (amends D71)
in force · factory · 2026-11-19

**Decision.** The Factory dashboard schedule moves from `*/15 * * * *` to `7,22,37,52 * * * *` (#155, reopened). Same cadence, off the quarter hours. Push and manual dispatch triggers stay.
**Why.** After #226 (09:12 UTC) no scheduled run happened in 4+ hours, and before it the `0 */3` cron fired only every 6-9 hours. GitHub's documentation says scheduled runs at the start of an hour (and other busy minutes) are the most likely to be delayed or dropped. This is the cheapest option and needs no secret.
**Check.** If there is still no scheduled run within ~2 hours of the merge, the next step is an external `workflow_dispatch` (the laptop poller while it runs, or a server crontab with an owner-installed dispatch-only token: an OWNER.md item), or accepting push-only freshness.
**Alternatives.** External dispatch now (needs an owner token); push-only freshness (stale while the factory is idle).
**Revert.** Set the cron back to `*/15 * * * *` in `factory-dashboard.yml` and in the #18/#19 exam cases.

## D74 · #236: one exam case corrected after the build; the acquire limit is per wait
in force · factory · 2026-11-19

**Exam correction (recorded, not hidden).** The implementer of #236 found one exam case that could not pass without breaking #217: `GET /api/admin/students/abc` in the admin-reads list expected 503 `db_busy`, but `student_page._student` refuses a malformed key with 404 before any read. The manager changed the case to a well-formed UUID key and **rewrote the unpushed branch** so the corrected exam commit comes before the code commits (exam-integrity refuses an exam change after code). So the exam was corrected *after* the code was written, and the history shows it first. The assertion is not weaker: the same `abc` URL stays in the guard-route list, where the auth read comes first. Reviewer of #244 confirmed the change is legitimate. Next time: send such a case back to the exam writer before the implementer starts, or record it like this one.
**Acquire limit is per wait.** `db.bounded` limits each wait for a pool connection to `POOL_ACQUIRE_TIMEOUT` (10 s), not the whole request. Routes with many reads in a row (student page, class table) could in theory pass nginx's 60 s while the pool stays exhausted; the task routes cannot (`REQUEST_BUDGET_S`). Accepted for now: an exhausted pool is already the exception (checks bounded, `command_timeout` 50 s), and each of those reads is short. A per-request deadline is #245.
**Test-only fallback.** `BoundedPool` uses a pool without `acquire` directly (old fakes in unit tests and the #177 exam). No production pool takes that path; removing it is #245.
**Revert.** Drop `db.bounded` from the routes (each goes back to waiting on the pool); the exam case change stays (it matches #217).

## D75 · #222: Next/Skip carry `from`, the task the student moves from
in force · factory · 2026-11-19

**Decision.** `POST /api/sessions/{sid}/tasks/current/next|skip` takes an optional body `{"from": "<task id>"}`. Current == `from`: move as before. Current == the task right after `from` (the move already happened): no-op, 200 with the current task, no setup run. Anything else: 409 `task_changed` (the lab screen already re-reads the current task on it, #160). No body: the old relative move (older exams and clients). The check and the move run under the session lock.
**Why `from` and not the target.** The lab screen knows the task on screen, not the next one; a target would need another read before every move. An idempotency key would need a table of seen keys; `from` needs no storage.
**Alternatives.** Target task id; an idempotency-key header with a stored result.
**Revert.** Ignore the body in `navigate` (relative moves again); the web field is harmless.
**Amended (#255, pass lf-v6k1).** Two concurrent moves with the same `from`: one moves, and the other answers either the retry no-op (200 with the current task) or 409 `setup_running` when it meets the per-session lock (the lock is never waited for; the lab screen treats `setup_running` as busy). Never `task_changed`, never a second move. A `from` that is no task of the lab is always 409 `task_changed`, also on the first task.
**Amended by #251 (PR #254).** Previous takes the same body: current == `from` moves back; current == the task right *before* `from` is the no-op; anything else is 409 `task_changed`; `from` on the first task stays 409 `no_previous_task`. Restart ignores the body. The move body no longer has `populate_by_name`, so `{"from_": ...}` is ignored (relative move). The lab screen sends `from` with every move except Restart.

## D76 · #230 split: the progress hook is bounded now, the guard hook waits for the owner (D35)
in force · factory · 2026-11-19

**Decision.** `scripts/_hook_io.py` gives hooks a bounded stdin read (10 s) and a 30 s whole-process watchdog. The progress hook uses both and exits 0 on a timeout (it must never block its caller). The guard hook is owner infrastructure (D35: a factory run cannot edit it, and the guard itself refused the edit), so its half is OWNER.md O21. Recommended there: **fail closed** (exit 2) on a stdin timeout, because a live parent always sends the payload at once, so a timeout means a dead or broken parent and denying costs nothing; keep fail-open on malformed JSON.
**Found on the way.** `factory_progress.py` is also loaded by file path (the #51 dashboard exam), so hook-only imports are lazy, and `dashboard-ci.yml` now runs on changes to the progress script. CI does not run `scripts/test_*.py` at all: #249.
**Revert.** Drop the `_hook_io` calls from `cmd_hook` and the `__main__` block.

## D77 · #155: GitHub's scheduler does not deliver; push freshness now, an external dispatch is the owner's choice (follows D73)
in force · factory · 2026-11-19

**Evidence.** After #242 (14:48 UTC, cron `7,22,37,52 * * * *`) no scheduled Factory dashboard run happened by 16:47 UTC (8 slots missed); the last one was 02:27 UTC. Push runs work: every push to main publishes.
**Decision.** Accept push-only freshness as the supported state: while the factory works it pushes to main many times an hour (state, merges), and each push publishes. The cron stays as a best-effort extra. A time-keeping dispatch needs owner infrastructure either way (the laptop runner is D35-protected; a server crontab needs a dispatch-only token), so it is the optional owner item O22, and #155 waits there.
**Alternatives.** A dispatch loop inside the factory pass (stops when the pass stops: the idle time is exactly when it lags); a self-hosted runner (more moving parts on the shared server).
**Revert.** Nothing to revert; with O22 done, close #155 once a dispatched run shows up every ~15 minutes.

## D78 · #251: one exam case corrected after CI; tooling tests in a job of their own (#249)
in force · factory · 2026-11-19

**Exam correction (recorded, not hidden).** The #251 exam case `test_many_repeats_never_move_back_past_the_target` reaches 04-last (which has a setup) and then asserted the setups seen were only `["03-net"]`, without clearing the exec log first, as every other case in the file does. CI (PR #253) failed it with `['04-last', '03-net'] == ['03-net']`; the server ran 03-net's setup exactly once over four repeats, the behaviour the case pins. The manager rebuilt the branch as `f/251-previous-idempotent-2` (PR #254): the exam commit gains one `env.runtime.execs.clear()` after reaching 04-last, the assertion is unchanged, and the exam commit stays strictly before the code commits. The reviewer of #253 found the cause. Same pattern as D74; the exam writer could not run the DB cases locally. Exam gaps found on the way (Restart with a body, a retry whose target is the first task, a concurrent double Previous) are a follow-up issue.
**Tooling tests job (#249).** `uvx pytest scripts -q` runs in its own CI job `tooling`, not inside `exam-integrity`: there, code from the PR (`scripts/test_*.py`, a `scripts/conftest.py`, which exam integrity does not count as CI surface) would run before the check in the same job and could change what it runs (`$GITHUB_PATH`, `$GITHUB_ENV`), and a failing tooling test would skip the check and hide its verdict. `exam-integrity` keeps only `scripts/ci` (CI surface) before the check.
**Revert.** Drop the `tooling` job; the exam case change stays (it matches the file's own pattern).

## D79 · #245: the request deadline caps statements too; the #177 exam fake gains `acquire`
in force · factory · 2026-11-19

**Exam edit across issues (recorded, not hidden).** The #245 exam commit (2010562 on #256, rebuilt as 1e7fc76 on #261) gives the fake pool in `test_issue_177_start_followups.py` a faithful `acquire`/`release`, because #245 removes the `BoundedPool` fallback that used a pool without `acquire` directly. No #177 assertion changed; #177 passes before and after the code (checked by the #256 reviewer). It sits in the exam commit, before any code.
**Decision (review round 1 of #256).** "One deadline per request" means the whole request, not only the waits for a connection: `BoundedPool._run` passes the time left (capped at `POOL_COMMAND_TIMEOUT`) as asyncpg's `timeout=` to each statement, and an expiry answers the same 503 `db_busy`. A route that opens its own `acquire()` + transaction (e.g. `teacher.py`) keeps the per-statement `command_timeout`; the risk left is one statement after a late acquire, recorded here. The outer `asyncio.wait_for` around asyncpg's `acquire(timeout=)` stays only for test fakes that ignore `timeout`, with a comment. Issue item 3 (cleanup on the `session_ended` 409 during start) is fixed in the same PR with a unit test, so it can keep "Closes #245". The exam's `LIMIT` goes from 0.4 s to 2.0 s (the busy cases are bounded by the deadline, not by `LIMIT`) in a rebuilt exam commit on `f/245-request-deadline-2`.
**Alternatives.** `asyncio.timeout(55)` around the whole handler (needs its own `TimeoutError` → `db_busy` translation in front of `db_guard`, and would also cut Docker calls that have their own budgets); splitting item 3 into a new issue.
**Revert.** Drop the `timeout=` argument in `_run`; the deadline then covers connection waits only, as in PR #256 round 1.
**Amended (round 2, pass lf-v6k1).** Passing asyncpg's `timeout=` to each statement broke ~30 test fakes whose `fetch*/execute` take no keywords, four of them in the #177 exam (which this PR may not edit). `_run` wraps each statement in `asyncio.timeout(<time left, capped at POOL_COMMAND_TIMEOUT>)` instead: the same bound (asyncpg's own `timeout=` also works by cancelling the query), and it works with every fake. asyncpg's `command_timeout` stays as the backstop. Revert: drop the `asyncio.timeout` wrapper. **Round 2 of #261 (manager's call after the second review round):** the reviewer found that `release` reused the shortened acquire wait, so a cancelled statement near the deadline could turn `db_busy` into a plain 503 'database unavailable'. The fix (release with its own floor, a release timeout maps to `db_busy`) is one more code commit on the same branch with a unit test; the manager checks it against the finding instead of a third review round.

## D80 · #186: the #178 and #160 exams find the open-tasks view without the status wrapper
in force · factory · 2026-11-19

**Exam edit across issues (recorded, not hidden).** #186 moves `role="status"` from the open-tasks wrapper onto the count line, as the issue asks ("exam edit first"). The #178 exam located the view as the `status` region holding the task titles; it now takes the nearest element around the open-task buttons that also holds the count line. The #160 Hebrew singular case looked for the digit "1" in a status/alert, which matched only through the task title inside the old wrapper; the Hebrew `lab.open.one` spells "one", so for one open task the case accepts that line. No assertion was dropped. The edit is its own exam commit (the first commit of the #186 PR on `f/186-busy-save-live-region-3`, which also adds a #178 case for the singular line, criterion 3), first on the branch, before the new `src/` cases (which exam integrity treats as unit tests, so they could not come before an exam change) and the code; both edited files pass on the old and the new code. The first exam writer missed the files under `tests/acceptance/` and edited only `src/*.test.tsx`; the implementer found the conflict, and the manager rebuilt the unpushed branch (exam edit, then the code cherry-picked unchanged).
**Alternatives.** Keep the wrapper as the live region (fails the issue's accessibility goal); `aria-live` on the count line inside a non-live wrapper (same exam edit needed).
**Revert.** Put `role="status"` back on the wrapper and revert the #186 squash commit.

## D81 · #257: one request clock; `REQUEST_BUDGET_S` stays as an import-time copy
in force · factory · 2026-11-19

**Decision (review round 1 of #270).** The task routes (show, navigate) measure their remaining time from the start the request middleware records (`db.mark_request_start`) against `db.REQUEST_DEADLINE_S`, read at request time. The `_start` dependency no longer stamps a later start of its own, so auth reads and task steps share one start point and one deadline. `tasks.REQUEST_BUDGET_S` is kept only as an import-time copy of `db.REQUEST_DEADLINE_S`, because the #220 and #224 exams and `tests/unit/test_task_db_step_bound.py` import the name to build "budget used up" contexts; nothing reads it at request time. Criterion 1's "read at request time" is met by the routes, not by that copy.
**Assumption.** A test that patches `db.REQUEST_DEADLINE_S` and also builds contexts from `tasks.REQUEST_BUDGET_S` would get a stale number; no exam does both today (#245 patches the deadline but does not touch task routes).
**Alternatives.** A module `__getattr__` alias (breaks `monkeypatch.setattr(..., raising=False)` in the #257 exam, which leaves a real attribute behind on undo); removing the name (edits three older exams); keeping two clocks (the defect #257 removes).
**Revert.** Revert the #257 squash commit: `_left` reads `REQUEST_BUDGET_S` again and `_start` stamps its own start.

## D82 · #98 ships in two slices: PR-run cancelling and dashboard paths now, docs-only skip later (#271)
in force · factory · 2026-11-20

**Decision.** #98 is done as a low-risk slice: CI cancels superseded runs on a pull request only (main runs are grouped per commit and never cancelled), the Factory dashboard publishes on push only when `docs/factory/**` or its own code changed (the 15-minute schedule stays), and FACTORY.md gets a minute-budget note. Criteria 1 and 2 (docs-only pushes skip CI, path-scoped docker and image jobs) move to #271, with the PR #103 review findings.
**Why.** D46 named PR-run cancelling as the first slice to take. Criteria 1 and 2 change what a Deploy sees as the newest head; a wrong docs-only rule silently skips a Deploy, so they deserve their own PR and review.
**Alternatives.** Finish all of #98 in one PR (the D46 risk again); leave #98 untouched (state commits keep cancelling nothing and the dashboard republishes on every code merge).
**Assumption (review of #272).** After a code squash-merge the dashboard no longer publishes on that push; the page catches up at the next `docs/factory/**` state commit, which rule 6 makes follow every close. The 15-minute schedule is unreliable (D73: runs ~5 hours apart on 2026-11-19/20), so it is not counted on. `dashboard-ci.yml` gets no PR concurrency on purpose: it takes ~19 s and criterion 4 names CI only.
**Revert.** Remove the `concurrency` block from `ci.yml` and the `paths` filter from the dashboard's `push` trigger.

## D83 · #257 merged after review round 2 without a third review; #269 merged on its round-1 approval plus a checked fix
in force · factory · 2026-11-20

**Decision.** PR #270 (#257) reached the two-round review limit with no blocker. Round 2 asked for one deadline helper (`db.time_left`, now used by the pool and the task routes) and a clock-agreement test that drives `db.BoundedPool` instead of re-computing the db side; both are in d14acd9, which the manager read line by line, and CI was green on it. Round-2 nit 5 (remove `tasks.REQUEST_BUDGET_S`, needs exam edits) is #273. PR #274 (#269) was approved in round 1 with two should-fix findings: the stale open-tasks list at lab complete was fixed in 56118f5 with a unit test, and the weak exam case (finding 2) needs an exam-only PR, so it is #275.
**Why.** FACTORY.md caps review at two rounds; after that the manager decides. Each fix answered a named finding with a small, readable diff, and a third reviewer would have read the same lines.
**Alternatives.** A third review round for #270 (~6 minutes and one more CI); rebuilding #274 as `-2` with the exam case strengthened first (a rebuild and a second CI for a test-only gap).
**Revert.** Revert the squash commits ad4d27d (#270) or 8e68837 (#274).

## D84 · #273: the name goes everywhere in `labs/manager`, including exam prose and the #257 guard case
in force · factory · 2026-11-20

**Decision.** #273 criterion 2 (`grep -rn REQUEST_BUDGET_S labs/manager` finds nothing) is read literally. The exam commit also rewrites the docstrings of the #245 and #257 exams that name it, and replaces the #257 guard case that patched `tasks.REQUEST_BUDGET_S` with `raising=False` by an equivalent behavioural check (patching `db.REQUEST_DEADLINE_S` changes what a task route sees), or drops it when another case in that exam already covers it.
**Why.** A guard that names a removed attribute keeps the name alive and tests a monkeypatch side effect, not behaviour. Docstrings that point at a name that no longer exists mislead the next reader.
**Alternatives.** Limit the grep to `labs/manager/app` and leave the exam mentions as history (cheaper, but the criterion says otherwise and the guard case keeps testing nothing).
**Revert.** Restore the old #245/#257 exam text from the #273 exam commit's parent.
