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
