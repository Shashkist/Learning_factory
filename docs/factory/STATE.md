# Factory state

**This is the resume point.** Any session that runs the factory reads this first and rewrites it as it works. Keep it short: it is a dashboard, not a log (the log is git history and the issues).

_Last updated: 2026-11-14, laptop pass lf-k7m2._

## Now

- **Issue:** #18 Factory dashboard: snapshot.json and the static page (claimed by lf-k7m2).
- **Step:** exam, then build.
- **Branch:** `f/18-factory-dashboard`

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
- Deploy: not set up yet.
- Server: 4 vCPU / 7.8 GB / ~3.6 GB free / 92 GB disk free (2026-11-14).
