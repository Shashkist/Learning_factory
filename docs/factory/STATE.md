# Factory state

**This is the resume point.** Any session that runs the factory reads this first and rewrites it as it works. Keep it short: it is a dashboard, not a log (the log is git history and the issues).

_Last updated: 2026-11-15 ~09:15 UTC, laptop pass lf-a7d42 (started 08:56 UTC)._

## Now

- **Pass lf-a7d42 running.** Claims: #8, #72.
- **#8 terminal proxy: PR #73** (branch `f/8-term-proxy-3`, replaces #70). Cause of the docker failure found (D33): under runsc, input typed before the student's shell is up is dropped. The exam fixture now waits for the prompt (its own exam commit before the code, D9). Waiting for CI, then reviewer round 2 (last), merge.
- **#72 manager image + compose file** (slice 1 of #13, D32): exam on `f/72-manager-image` (dbaf1a7); implementer working. CI build/push of the image is a second PR (D19).

## Next (in order)

1. **#70 (#8)**: diagnose the docker `echo hi` failure (see #8), fix, round-2 review, merge.
2. **#13** deploy. #3 is done: isolation is proven on the server, and lf-lab-term (tini) is not affected by the busybox finding. See the #13 comments for: `install-block.sh factory` for `/live.json`, the D31 migration watch item, D30 container name `lf-manager` + concurrency group `server-lab-host`, pre-pull `lf-lab-term`, `LF_TRUSTED_PROXY`, create-admin. Then #9, #11, #10 (#12 content can run in parallel).
3. p2: #67 (remove liveness.sh), #69 (WS Origin check), #64 (live view leftovers), #54, #55, #56, #47, #44, #21, #15, #14. Keep at least 5 issues `status:ready`.

## Waiting for the owner

- O1–O3 done (DNS `labs` and `factory`, deploy secrets, database role). O4 (OpenRouter) skipped by choice.
- O6 closed: no token; the owner signs in again when the runner alerts (sign-in ends 2026-11-20..23). O8 done (deploy secrets only in the `production` environment).
- O9 decided by the manager: D31 (#61). **O10 open:** move `LF_DATABASE_URL` into the `production` environment.

## Recently done

- 2026-11-15 · #61 decided (D31): the read-only `DB check` (#63, run 43693809513, as `lf_app`) found **no SECURITY DEFINER function** it can execute; 2 invoker functions in `public`; Supabase event triggers fire on our DDL (watch item on #13). Nothing revoked. O10 for the owner.
- 2026-11-15 · #3 closed: the liveness diagnostic (run 43692619852) traced the dying smoke labs to busybox `timeout` + `sh` as PID 1 (also under runc); the isolation smoke with `--init` (run 43693581363) passed every check. Shipped via #62 (reached main early by accident) and #65.
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
