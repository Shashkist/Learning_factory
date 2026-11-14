# Factory state

**This is the resume point.** Any session that runs the factory reads this first and rewrites it as it works. Keep it short: it is a dashboard, not a log (the log is git history and the issues).

_Last updated: 2026-11-14, factory setup (initial session)._

## Now

- **Issue:** none in progress.
- **Step:** factory set up; the issue queue for phase 1 is seeded; the first run will pick the top `status:ready` issue.
- **Branch:** –

## Next (in order)

1. The highest-priority `status:ready` issue in phase 1 (see `gh issue list --label status:ready`).
2. Keep at least 5 issues `status:ready` (planner).

## Waiting for the owner

See `OWNER.md`: O1 DNS, O2 deploy secrets, O3 database, O4 second opinion (optional). Issues that need these carry `status:needs-owner`. Everything else proceeds.

## Recently done

- 2026-11-14 · Design approved and committed (`docs/superpowers/specs/2026-11-14-lab-platform-design.md`).
- 2026-11-14 · Factory set up: rules, roles, memory files, labels, phase 1 issues, hourly routine.

## Health

- `main`: no CI yet (issue: CI pipeline).
- Deploy: not set up yet.
- Server: 4 vCPU / 7.8 GB / ~3.6 GB free / 92 GB disk free (2026-11-14).
