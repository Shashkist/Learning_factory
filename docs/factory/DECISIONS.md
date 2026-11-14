# Decisions and assumptions

**When two documents disagree, this file settles it.** Every entry is revertible: say "revert D7" (in an issue or a session) and the factory reverses it, updates the spec and the affected issues, and records the reversal here **in place** (never delete an entry).

Format: **D<n> · title** · status · who decided · date, then what was decided, why, alternatives, and how to revert.

Owner decisions (from the brainstorm, 2026-11-14) are in the spec, §3. This file holds the factory's own decisions and assumptions.

---

## D1 · Factory runtime: hourly cloud routine plus "continue" in any session
in force · factory (initial setup) · 2026-11-14

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
