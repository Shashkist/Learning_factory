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

**Decision.** `nginx-proxy3` mounts a single `/root/nginx/conf.d/default.conf` for all sites. The labs server block lives in this repo (`labs/deploy/nginx/labs.conf`) and is installed between `# BEGIN learning-factory` and `# END learning-factory` markers by a script that backs the file up, runs `nginx -t` inside the container, reloads on success and restores the backup on failure. It follows the existing pattern: `set $upstream ...` with `resolver 127.0.0.11`, and WebSocket upgrade headers on `/s/`.
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
