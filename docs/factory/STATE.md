# Factory state

**This is the resume point.** Any session that runs the factory reads this first and rewrites it as it works. Keep it short: it is a dashboard, not a log (the log is git history and the issues).

_Last updated: 2026-11-14, factory setup (initial session)._

## Now

- **Issue:** none in progress.
- **Step:** factory set up; the issue queue for phase 1 is seeded; the first run will pick the top `status:ready` issue.
- **Branch:** –

## Next (in order)

1. #1 Repo skeleton and CI (p0): everything depends on it.
2. Then the p1 issues in dependency order; the dashboard (#18 → #19) early, so the owner can watch progress.
2. Keep at least 5 issues `status:ready` (planner).

## Waiting for the owner

- **Connect GitHub to Claude in the cloud** (claude.ai → Code → connect GitHub with access to `Shashkist/Learning_factory`). The hourly routine can't be saved without it (HTTP 401 `github_token_missing`, 2026-11-14).
- O1–O3 done (DNS `labs` and `factory`, deploy secrets, database role). O4 (OpenRouter) skipped by choice.

## Recently done

- 2026-11-14 · Design approved and committed (`docs/superpowers/specs/2026-11-14-lab-platform-design.md`).
- 2026-11-14 · Factory set up: rules, roles, memory files, labels, phase 1 issues #1–#16 (epic #17).
- 2026-11-14 · Hourly cloud routine NOT created yet: it needs the owner's explicit go-ahead (D1). Until then the factory runs when a session is told "continue the factory".

## Health

- Laptop poller `LearningFactoryPoll` (Windows Task Scheduler) is active: at logon and every 30 min; idle unless issues carry `needs:laptop`. Log: `%LOCALAPPDATA%\learning-factory\poll.log`.

- `main`: no CI yet (issue: CI pipeline).
- Deploy: not set up yet.
- Server: 4 vCPU / 7.8 GB / ~3.6 GB free / 92 GB disk free (2026-11-14).
