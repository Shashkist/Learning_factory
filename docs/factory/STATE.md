# Factory state

**This is the resume point.** Any session that runs the factory reads this first and rewrites it as it works. Keep it short: it is a dashboard, not a log (the log is git history and the issues).

_Last updated: 2026-11-14 ~19:55 UTC, laptop pass lf-k7m2 (stopped at the 90-minute budget)._

## Now

- **Issue:** #18 Factory dashboard (claim held by lf-k7m2; the claim comment's branch field still says the old `f/18-factory-dashboard`, the real branch is below).
- **Step:** PR #23 open, **CI green** (run 43634103197 + Dashboard CI 43634103302). Review round 1 done on #22 (closed, superseded): fixes and the revised exam (D12) are in #23. **Next session: dispatch the reviewer for round 2 on #23** (fresh context: PR number + D8/D12), fix or decide, squash-merge, check that the `Factory dashboard` workflow on `main` uploads the `factory-dashboard` artifact with a sane `snapshot.json`, close #18 with evidence.
- **Branch:** `f/18-dashboard-v2` (exam 0eaa72d + b8e6b97 precede the code; PR #23).

## Next (in order)

1. #19 Publish the dashboard (after #18).
2. p1 foundations now unblocked by #1: #2 lab image, #3 server bootstrap, #4 database schema, #5 content loader; then #6, #7, #13 in dependency order.
3. #21 exam-integrity hardening (p2, review leftovers of #20, D10).
4. Keep at least 5 issues `status:ready` (planner).

## Waiting for the owner

- O1–O3 done (DNS `labs` and `factory`, deploy secrets, database role). O4 (OpenRouter) skipped by choice.

## Recently done

- 2026-11-14 · #1 Repo skeleton and CI shipped in #20: manager/web skeletons, CI (manager, web, docker with gVisor, exam-integrity per commit D9), incident workflow. Main CI green.
- 2026-11-14 · Design approved and committed (`docs/superpowers/specs/2026-11-14-lab-platform-design.md`).
- 2026-11-14 · Factory set up: rules, roles, memory files, labels, phase 1 issues #1–#16 (epic #17).
- 2026-11-14 · Cloud routine dropped for now (company Claude account only); the laptop runs the factory (D1 amended).

## Health

- **Runtime: laptop only** (D1 amended). `LearningFactoryPoll` runs a manager pass at logon and every 30 min when there is work; max 10 passes/day; pause with `%LOCALAPPDATA%\learning-factory\PAUSE`. Logs: `poll.log` and `runs\*.log` in that folder.

- `main`: CI green (run 43632254920). Incident workflow active.
- **Actions minutes:** private repo on the free plan (2,000 min/month). Dashboard cadence cut to push + every 3 h (D12); owner option O7.
- Local runs: `uv run mypy` / `uv run pytest` `.exe` shims fail on this laptop (Access denied); use `uv run python -m ...`. Force-push is blocked by the permission classifier: re-sequence onto a new branch + new PR instead.
- Deploy: not set up yet.
- Server: 4 vCPU / 7.8 GB / ~3.6 GB free / 92 GB disk free (2026-11-14).
