# Factory state

**This is the resume point.** Any session that runs the factory reads this first and rewrites it as it works. Keep it short: it is a dashboard, not a log (the log is git history and the issues).

_Last updated: 2026-11-14 ~21:30 UTC, laptop pass lf-k7m2 (stopped at 3 merged PRs: #30, #31, #33)._

## Now

- **Issue:** #4 database schema, claimed by lf-q4d9. Branches: `f/4-exam` (exam), `f/4-db-schema` (code + CI, PR A per D19). Design in D22.
- **Next session starts at:** if the claim is stale, continue #4 from its branches; otherwise Next 1 below.

## Next (in order)

1. p1 foundations: #4 database schema (needs a Postgres service in `ci.yml`, so it ships as code+CI PR then exam PR, D19), #3 server bootstrap (read the D20 notes on the issue: `enable_icc=false` plus a manager→lab 7681 accept rule), then #6, #7 (D20 run flags and tmpfs log on the issue), #13.
2. #21 exam-integrity hardening (p2): leftovers of the reviews of #20, #23 and #27 (D10, D13, D17) are collected as comments on the issue.
3. Keep at least 5 issues `status:ready` (planner).

## Waiting for the owner

- O1–O3 done (DNS `labs` and `factory`, deploy secrets, database role). O4 (OpenRouter) skipped by choice.
- O6 (long-lived token before 2026-11-20). O8 done (deploy secrets only in the `production` environment).

## Recently done

- 2026-11-14 · #2 Lab image `lf-lab-term` shipped in #30 (image + CI) and #33 (exam), split per D19. `ghcr.io/shashkist/lf-lab-term:latest` pushed from main (run 43641907273); docker exam 6 passed under runsc; smoke under the production run flags (D20) passes.
- 2026-11-14 · #5 Content loader and validator shipped in #31 (sample lab `labs/content/sample/hello`, `module`/`lesson` per D16). Review closed after round 2 (D21).
- 2026-11-14 · #25 code shipped in #27: publish runs in the `production` environment (main only) with a pinned host key; run 43639502324 deployed with the new key. Incident #26 (SSH refused in a burst) fixed in the same PR and closed. Owner finished O8; #25 closed (D17).
- 2026-11-14 · #19 Dashboard live at **https://factory.shashkist.com** (PR #24, D14, D15). Publish runs on every push to main and every 3 h; `snapshot.json` verified fresh from outside. `install-block.sh` hardened over two review rounds.
- 2026-11-14 · #18 Factory dashboard shipped in #23 (review closed after round 2, D13). The `Factory dashboard` workflow on main uploads the `factory-dashboard` artifact (snapshot verified). Leftovers in #21 and #19.
- 2026-11-14 · #1 Repo skeleton and CI shipped in #20: manager/web skeletons, CI (manager, web, docker with gVisor, exam-integrity per commit D9), incident workflow. Main CI green.
- 2026-11-14 · Design approved and committed (`docs/superpowers/specs/2026-11-14-lab-platform-design.md`).
- 2026-11-14 · Factory set up: rules, roles, memory files, labels, phase 1 issues #1–#16 (epic #17).
- 2026-11-14 · Cloud routine dropped for now (company Claude account only); the laptop runs the factory (D1 amended).

## Health

- **Runtime: laptop only** (D1 amended). `LearningFactoryPoll` runs a manager pass at logon and every 30 min when there is work; max 10 passes/day; pause with `%LOCALAPPDATA%\learning-factory\PAUSE`. Logs: `poll.log` and `runs\*.log` in that folder.

- `main`: CI green (run 43642221188 on 9d0c367). Incident workflow active.
- **Pattern:** an issue whose acceptance needs a `ci.yml` change ships as two PRs (D19). To prove the exam before the code PR merges, stack the exam commits in a draft PR based on the code branch (as #32 did).
- **Actions minutes:** private repo on the free plan (2,000 min/month). Dashboard cadence cut to push + every 3 h (D12); owner option O7.
- Local runs: `uv run mypy` / `uv run pytest` `.exe` shims fail on this laptop (Access denied); use `uv run python -m ...`. Force-push is blocked by the permission classifier: re-sequence onto a new branch + new PR instead.
- Deploy: not set up yet (#13). Dashboard publish works from the `production` environment (run 43639502324); the server refuses bursts of new SSH connections, so reuse one connection (ControlMaster) in deploy scripts.
- Server: 4 vCPU / 7.8 GB / ~3.6 GB free / 92 GB disk free (2026-11-14).
