# Factory state

**This is the resume point.** Any session that runs the factory reads this first and rewrites it as it works. Keep it short: it is a dashboard, not a log (the log is git history and the issues).

_Last updated: 2026-11-15 ~08:00 UTC, laptop pass lf-ce139 (running, started 07:32 UTC). Previous pass lf-m7k2 stopped at 3 merged PRs (#58, #57, #60)._

## Now

- **Pass lf-ce139 (started 07:32 UTC, running).** Claims: #3 (`f/3-smoke-diag`), #8 (`f/8-term-proxy`, worktree `../lf-wt-8`), #61 (`f/61-db-check`, worktree `../lf-wt-61`).
- **#3 step (d):** #62's first commit (038ee8e: `liveness.sh`, `--init` in the isolation smoke) reached `main` by accident (a state commit made in the shared worktree while that branch was checked out, then `push HEAD:main`); GitHub marked #62 merged. Review fixes in **PR #65** (round 2 approved). Next: merge #65, dispatch `Server smoke` `check=liveness`, then `check=isolation`; record on #3. If labs die even under tini, that blocks #13.
- **#8 terminal proxy:** exam 2069923 pushed (35 tests + 8 docker; seam `LF_TERM_UPSTREAM`; dev dep `websockets`). Implementer building in `../lf-wt-8`.
- **#61:** PR #63 (dispatch-only `DB check` workflow, catalog queries as `lf_app`). Review round 1: request changes (read-only via `begin transaction read only` instead of PGOPTIONS through the pooler, `via_public`, event triggers, sslmode). Then run it and write the D-entry (generic title, no function names in titles: the dashboard publishes titles).
- **Next session starts at:** whatever of the above is still open (check claims), then Next 1.

## Next (in order)

1. **#3 step (d)** as above (Shared server: only through `labs/deploy/` scripts; never restart Docker). Then close #3.
3. **#13** deploy (needs #3 isolation proven; contract D30: container `lf-manager`, concurrency group `server-lab-host`; see #13 comments), then #8, #9, #11, #10. #61 (O9, owner delegated) is a manager decision.
4. p2: #54 (now also: no-daemon.json scenario, subnet-overlap scenario, `/etc/docker` rollback), #55, #56, #47, #44, #21, #15, #14. Keep at least 5 issues `status:ready`.

## Waiting for the owner

- O1–O3 done (DNS `labs` and `factory`, deploy secrets, database role). O4 (OpenRouter) skipped by choice.
- O6 closed: no token; the owner signs in again when the runner alerts (sign-in ends 2026-11-20..23). O8 done (deploy secrets only in the `production` environment).
- O9 handed to the manager: issue #61 (decide, owner delegated 2026-11-15).

## Recently done

- 2026-11-15 · #51 Live dashboard view shipped in #59 (round 2 approved; publish run 43691834038; page and `/live.json` schema 1 verified live). The `/live.json` nginx block (`Cache-Control: no-cache`) needs `install-block.sh factory` (noted on #13). Leftovers → #64.
- 2026-11-15 · #3 apply: dry-run review recorded (approve), pre-apply fixes in #58 (`/etc/docker`, subnet preflight, positive baseline lines), isolation smoke in #57 (2 review rounds) and #60 (liveness + diagnostics). Apply run 43688303191 changed the server with every baseline check green. D30.
- 2026-11-15 · #49 Session lifecycle hardening shipped in #53 (review approved in round 1): the janitor reads rows, then containers, then rows again; `created` counts as running; `LF_START_DEADLINE_S` (180 s); DB-down backoff and log dedupe; capped `/dev/shm` and a `/var/mail` tmpfs. In the PR run, 22 DB and 6 docker exam tests ran, none skipped. Main CI 43659088406 green. D24 amended. Leftovers → #55.
- 2026-11-15 · #50 Bootstrap safety shipped in #52 (2 review rounds): `.lf-prev` boot copies, flag-registered runsc fails safe (D29), pinned runsc + sha512, signal-proof rollback, a detached workflow run with a per-run directory and a server-side `flock`. Server dry run 43659490082 ok. Leftovers → #54, #56.
- 2026-11-15 · #7 Session lifecycle shipped in #48 (after #41 and #43 were re-sequenced): Docker Engine API over the socket, capped tmpfs for every student-writable path, admission, janitor (heartbeat, time limit, access expiry, orphans, `lf.expires` sweep while the DB is down). Main CI 43652825241: 207 + 31 exam tests, no skips. Sudo under gVisor does not work (setuid not honoured, D26) → #47. Leftovers → #49.
- 2026-11-15 · #3 Server bootstrap code shipped in #46 (after #42 and #45); CI ran the real script on a runner incl. the runtime step, swap and a rollback after a step. Server dry run passed. Apply blocked on #50 (D27). Follow-ups #44 (IPv6), #50.

- 2026-11-14 · #6 Login shipped in #40: student (ID + class password) and admin login, server-side sessions (`auth_sessions`, migration 002), rate limits that hold under parallel bursts, argon2 behind a semaphore, `python -m app.cli create-admin`. Two review rounds + round-2 fixes (D23 amended twice). Main CI 43647224796: 142 acceptance tests against Postgres, no skips.
- 2026-11-14 · #4 Database shipped in #34 (code + Postgres service in CI) and #38 (exam), per D19, with draft evidence PRs (#35–#37) running the exam on the code branch first. asyncpg + plain SQL migrations, `/api/health` degraded mode (D22, amended). Owner item O9: what `lf_app` still reaches through `PUBLIC`.

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

- `main`: CI green (run 43647224796 on dc7a068). Incident workflow active.
- **Postgres exists only in CI** (the `manager` job's `postgres:16` service; no Docker/Postgres on the laptop). DB tests skip locally and must run in CI (the no-skip gate enforces it). They refuse any `LF_TEST_DATABASE_URL` that is not localhost.
- **Exam harness fixes go before the code.** The per-commit gate (D9) rejects an exam change after a code commit; re-sequence onto a new branch (exam, harness fix, code) and open a new PR (#39 → #40). TestClient: a second client must reuse `other.portal = client.portal` (the asyncpg pool belongs to one loop).
- **Pattern:** an issue whose acceptance needs a `ci.yml` change ships as two PRs (D19). To prove the exam before the code PR merges, stack the exam commits in a draft PR based on the code branch (as #32 did).
- **Actions minutes:** private repo on the free plan (2,000 min/month). Dashboard cadence cut to push + every 3 h (D12); owner option O7.
- Local runs: `uv run mypy` / `uv run pytest` `.exe` shims fail on this laptop (Access denied); use `uv run python -m ...`. Force-push is blocked by the permission classifier: re-sequence onto a new branch + new PR instead.
- Deploy: not set up yet (#13). Dashboard publish works from the `production` environment (run 43639502324); the server refuses bursts of new SSH connections, so reuse one connection (ControlMaster) in deploy scripts.
- Server: 4 vCPU / 7.8 GB / ~3.6 GB free / 92 GB disk free (2026-11-14).
