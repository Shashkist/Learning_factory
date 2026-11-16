# Factory state

**This is the resume point.** Any session that runs the factory reads this first and rewrites it as it works. Keep it short: it is a dashboard, not a log (the log is git history and the issues).

_Last updated: 2026-11-16 ~05:40 UTC, laptop pass lf-p9x4 (started ~05:15 UTC)._

## Now

- **Pass lf-p9x4 in flight.** #144 (CI incident) closed: a one-off ghcr.io login denial, later main runs green.
- **#117 (claim lf-p9x4):** PR **#148** CI was red: every docker exam case timed out in the kill. Cause: the kill exec matched `<interp> -s ` with a trailing space, `ps args=` prints none, so nothing was killed. Fixed in 748f946 (CI running). Next: green → review round 2 → merge → Deploy → close.
- **#130 (claim lf-p9x4):** lab screen, worktree `C:\projects\lf-130`, branch `f/130-lab-screen`; exam being written. #147 stays a separate issue after #130 (keeps the PR under ~400 lines).
- **Production:** Deploy 43805998335 shipped `lf-manager:014d73b` (catalog `GET /api/labs` + My labs page), deploy and post-deploy smoke green. Live: `/api/labs` signed out → 401 `not_authenticated`; the bundle has the My labs dialog.
- **#10 is sliced (D53):** slices 1 (#133) and 2 (#146, #129) shipped; next #130 (lab screen, fold in #147), #131 (Playwright).
- **Lesson (D49):** a state commit pushed to `main` while a merge's Deploy is pending makes that Deploy skip; push state after the Deploy.
- **Repository is public (D43).** Always `git pull --rebase` before a state commit, never `--autostash`.
- **Known gap (D40):** a cancelled Deploy is not noticed; fix in #85.
- **Next session starts at:** Next 1.

## Next (in order)

1. **#117** finish (see Now).
2. **#130** (p1) lab screen (map 409 `session_busy` / `setup_running` to "busy, try again"), folding in **#147** (p1, My labs follow-ups from the review of #146: double clicks, 401 while open, error vs empty, toggle label; exam changes first). Then **#11** teacher UI (leave out the `lf-smoke` cohort, refuse that cohort name; see comments on #11; flags/reasons in `task_results` / `lab_runs`, D55; also: students can still start a `draft` lab by id).
3. **#139** (session ready after the skel copy; one ownership check), **#137** completion nits, **#138** task smoke nits, **#126** Linux 1 exam, **#127** check parser nits, **#142** bootstrap exam scenarios.
4. **#98** (p2, D46): cancel superseded PR runs and skip full CI for docs-only pushes.
5. **#131** Playwright; **#85** deploy follow-ups, **#94**, **#93**; p2 list: #81, #76, #67, #69, #64, #68, #55, #56, #47, #44, #21, #15, #14. Keep at least 5 issues `status:ready`.

## Waiting for the owner

- **O19 (new, read only):** the break-glass admin login steps, needed only if Google sign-in breaks: since #99 a password admin session is refused at once while Google is configured.
- **O18 (optional since #110):** re-save `LF_GOOGLE_CLIENT_ID` / `LF_GOOGLE_CLIENT_SECRET` without the byte-order mark, then do the **first real Google sign-in**.
- **O17:** read Linux 1 (en + he) and publish it (`status: published`). #106 added 75 wrong-answer cases and fixed the chmod check (D48).
- O16/O7 resolved by D43 (public repository). O12 (back up `.env`), O13 (re-deploy after recreating nginx-proxy3), O5 reopened.

## Recently done

- 2026-11-16 · Pass lf-w8c1: **2 PRs merged, #117 in review round 2.** Incident #132 closed as a one-off flake (1 of 72 CI runs; the bootstrap exam now prints the script output on failure). #54 bootstrap exam hardening (#140, exam-only; review approved, should-fix fixed in the PR: the workflow-shell stub refuses unknown remote commands; leftovers #142; D30 points there). #129 catalog API + My labs, with #136 folded in (#146; re-sequenced twice, from #141 (unit test outside `tests/unit/`, exam lint) and #145 (exam's fake labs had lessons outside their module ranges, so CI's DB tests saw an empty catalog); review approved; should-fix → #147; live 401 + bundle verified). #117 round 1 (#143): blocker, root cannot signal student-owned processes (no `CAP_KILL`) → setpriv as owner, PPid 0 root match, bounded kill (D56); re-sequenced into #148. New: #139, #142, #147.

- 2026-11-16 · Pass lf-t5w2: **3 PRs merged, three lanes in parallel.** #10 sliced into #10/#129/#130/#131 (D53); slice 1 shipped in #133 (i18n en/he + RTL, student login, router; review approved, should-fix fixed by the manager: failed logout kept the session, malformed `%` id crashed the page; leftovers #136; live bundle verified). #89 completion, active time, fast flag (#134, D55; round 1 blocker: two tasks passing at once lost the completion forever → session row lock + race test; per-beat rounding could double or erase active time → `double precision`; round 2 approved; migration 005 live; leftovers #137). #128 smoke drives the task API (#135, D54 internal smoke lab for cohort `lf-smoke`; round 1: stale prompt, nested quiz leak, unchecked second container; round 2 approved; live smoke all 10 steps ok; leftovers #138).

- 2026-11-16 · Pass lf-k3v9: **3 PRs merged.** #121 Google test nits (#122; review approved, a test-order should-fix fixed in the PR). #88 task engine slice 2, check + answer (#123; D50 API shape; exam pushed alone first so CI proved the harness, one seed-order fix; round 1 blocker: checks bypassed the session lock so one student could hold every exec slot, D51; round 2 approved, nits → #127; live routes 401). #119 Linux 1 checks with a private home (#125, re-sequenced from #124 after a `find /` exit-code exam bug; round 1: check body visible in `ps` and a student-owned temp file let a wrong answer pass → facts-only reader on stdin, root decides, D52; round 2: dash `echo` let file content forge fact lines → `printf`, regular files only, bounded reads, fixed by the manager; leftovers → #126). New: #126, #127, #128.

- 2026-11-15 · Pass lf-r7p3: **3 PRs merged.** #9 task engine slice 1 (#114, re-sequenced from #113 after two exam-side CI failures, D47; round 1 blocker: per-session setup lock; round 2 should-fix fixed by the manager, D49; leftovers #117). #99 Google sign-in follow-ups (#118; round 1 blocker: undecodable allow-list bytes crashed every admin path incl. break-glass; live: callback via the no-access-log location; O19; leftovers #121). #106 Linux 1 wrong-answer exam (#120, third branch; 75 near-miss cases; chmod content fix, D48; leftovers #119). #110 and #12 closed with live evidence.

- 2026-11-15 · Pass lf-q4n8: **4 PRs merged.** #92 Google admin sign-in (#102; live: `google: true`, 303 to Google with the right callback); #105 harness race in the proxy exam (#108: the janitor's first DB pass ~2 s after startup ended every live session because the fake runtime listed no containers; incident #107 closed); #12 Linux 1 (#104; round 1: 5 should-fix, round 2 approved; leftovers #106); #110 byte-order mark in the Google settings, found by the live check of #92 (#112). #98 dropped to p2 and PR #103 closed (D46). D45 (task scripts as root, no extra capabilities). #9 exam + code, PR #113 opened.

- 2026-11-15 · Pass lf-m7k2 (no merges, CI down #96): found the cause of the red line (Actions minutes, O16, D41); measured where the minutes go and built #98 (PR #100: exam + code, local 21/21); finished #95 round-1 fixes (5ed0a30), round 2 approved, nits → #99; #12 exam + content (PR #101, D42); PR #100 round 1 requested changes.

- 2026-11-15 · #90 shipped in #91 (2 review rounds, D38 amended, D40): the terminal smoke runs as a job inside the Deploy run, only after `deploy.sh` printed `deployed <tag>`; nightly/manual runs check for a pending Deploy first. Live: Deploy run 43727774792 (smoke job green), stale-sha Deploy 43727169484 ran no smoke. An automatic re-dispatch of cancelled Deploys was removed in round 2 and moved to #85. Follow-ups #94.

- 2026-11-15 · #86 Live terminal smoke shipped in #87 (exam first, 2 review rounds, D36): `app.cli smoke-student` (synthetic `lf-smoke` cohort, per-run password on stdin), `labs/deploy/smoke/terminal.py` (runner → https://labs.shashkist.com → nginx → Docker DNS `lab-<uuid>` → ttyd), `server-smoke.yml` `terminal` check after Deploy and nightly. Live run 43719981589 green on `lf-manager:d3e69a2`. Review leftovers → #90 (PR #91), note on #11. #9 sliced into #9/#88/#89 (D37).

- 2026-11-15 · #13 Deploy shipped in #83 (exam, code, review round 1 fixes: main-only shas, private registry login, skip without DB URL; round 2 approved). Deploy run 43712703642: `lf-manager:2b33cf3` live, migrations 001-003 applied against Supabase, `labs` + `factory` nginx blocks installed (`nginx -t` ok). Verified from outside: healthz, `/api/health` database ok, UI with noindex, `/live.json` no-cache. Follow-ups #85, #86. #72 closed after #80 (main CI 43706527875).

- 2026-11-15 · #72 (slice 1 of #13, D32) shipped in #75 (image + compose, 2 review rounds) and #78 (CI `image-manager` job: build, smoke, `compose config --quiet`, push on main; serving fixes). The first push of the new GHCR package was denied at the manifest (incident #82, closed): a re-run of the failed job pushed it. Exam follow-up #80 waits for merge. Leftovers → #81.

- 2026-11-15 · #8 Terminal proxy shipped in #74 (re-sequenced from #66/#70/#73; review round 2 approved). Main CI 43699419037 green, docker exam under runsc with no skips. The docker failure was the exam typing before the student's shell was up (dropped under runsc) and a `hi` pattern that missed bash's bracketed-paste reset (D33). Live terminal smoke → #13. Leftovers → #76.

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
- Deploy: `.github/workflows/deploy.yml` after every green main CI (D34); server files in `/root/learning-factory/` (`.env` mode 600, `deployed-tag`). Server: Docker 28.2.1, Compose 2.36.2, nginx network `my_network`. Dashboard publish works from the `production` environment (run 43639502324); the server refuses bursts of new SSH connections, so reuse one connection (ControlMaster) in deploy scripts.
- Server: 4 vCPU / 7.8 GB / ~3.6 GB free / 92 GB disk free (2026-11-14).
