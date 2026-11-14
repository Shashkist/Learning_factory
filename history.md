# History: Learning Factory

Short entries with insights and fun facts about building this with Claude Code, for the POC presentation.

## 2026-11-14 · From an idea to a running factory in one evening

- **Brainstorm first, then build.** The lab platform was designed in conversation: Ilia described the KodeKloud experience he wanted, Claude looked at a live KodeKloud lab in Chrome and reverse-engineered it from the network traffic (per-session host, `/check_config`, `/heart_beat`, ttyd for terminals, code-server for VS Code labs).
- **A reviewer read the design before Ilia did.** A fresh agent tried to plan from the document alone and returned 8 questions; 7 were settled by the factory itself and recorded as revertible decisions; only one (the server size for VS Code weeks) went to Ilia.
- **The factory is the talk made real.** The roles and loop follow Ilia's "Software Factory" deck: a manager on a strong model, workers on cheaper ones, a hidden exam written before the code, a second opinion from another AI company, triggers (hourly, failed build, nightly), and guardrails ("never touch the owner's other services").
- **Memory lives in the repo, not in a session.** `docs/factory/STATE.md` plus claim comments on issues mean "continue the factory" works from any session, on the laptop or in the cloud.
- **Reuse:** the issue claim lock came from the e-sign project unchanged except one label; its 33 tests passed on the first run.

## 2026-11-14 · First issue through the factory (#1, skeleton + CI)

- **The hidden exam needed a rule nobody had written.** "A PR may not change tests and code together" collided with "one issue = one PR". The manager settled it on its own (D9): exam and code share a PR but never a commit, and the exam must come first. CI walks the commits to prove it.
- **The reviewer attacked the exam guard like a red team.** In two rounds it built throwaway git repos and showed four ways around the check: a merge commit, a `conftest.py` that marks every test as skipped, a pyproject `--deselect`, and a PR that edits the check that judges it. Each finding came with a reproduction.
- **Knowing when to stop is a decision too.** After two rounds the manager merged and moved the remaining hardening to a follow-up issue (#21, D10), because everything else in the queue waits on #1.
- **Numbers:** about 45 minutes from claim to green `main`, 10 commits squashed into one, 3 CI rounds (one failure: `uvx` was missing on the runner), and a real gVisor container started in CI in 6 s.
- **Windows quirk:** `uv run mypy` fails to start its `.exe` shim on the laptop (Access denied), so the factory uses `uv run python -m mypy` locally. Linux CI is unaffected.

## 2026-11-14 · Second issue (#18, dashboard): the reviewer read the bill

- **A code review that found a money bug.** The reviewer checked the account's plan with `gh api` and worked out that the requested 15-minute dashboard cron alone (~2,880 billed minutes a month) is more than the free private-repo allowance (2,000). CI would have stopped around day 20 of every month. The manager cut the cadence to push-to-main plus every 3 hours (D12) and left "make the repo public" to the owner (O7).
- **Live data beat the fixtures.** The reviewer ran the snapshot builder against the real repo and found two bugs the 30 green exam tests missed: the CI light showed a feature branch's run, and the current issue vanished from the page as soon as its PR opened (status `in-review`).
- **Changing an exam honestly.** The exam pinned the old cron, and the build is not allowed to edit the exam. So the exam writer revised it, and the manager rebuilt the branch with both exam commits ahead of the code. Force-push was blocked by the permission guard, so the rebuilt branch went out as a new PR (#23) and #22 was closed as superseded.
- **Guardrails worked both ways.** The auto-mode classifier also refused a local web server + headless screenshot step. The page was checked by file-level tests and review instead, and a real screenshot is still owed.

## 2026-11-14 · Fourth pass (#2, #5): two issues in parallel, and the guardrail that shaped the PRs

- **Two issues at once, one manager.** The pass claimed #2 (lab image) and #5 (content loader) together. Each got its own git worktree, exam writer and implementer, working side by side. Three PRs merged in about 40 minutes: #30, #31, #33.
- **The factory's own guardrail forced a split, and it chose not to weaken it.** The exam-integrity gate refuses a PR that changes the exam and the CI workflow together. Issue #2 needed both (its acceptance criteria include the CI job that publishes the image). The manager split it into "code + CI" (#30) and "exam only" (#33) and wrote the pattern down as D19, instead of relaxing the gate.
- **Proof before merge, with a throwaway PR.** To show the exam passes against #30 before #30 merged, the manager stacked the exam commits on #30's branch in a draft PR (#32). Its only job was a CI run, and it showed `6 passed` under gVisor.
- **The exam had a bug too.** The first docker run failed because the test read the container IP from a field Docker 28 no longer fills. The image itself was fine (5 of 6 checks passed). The fix went into a separate exam-only commit, with the assertions unchanged.
- **What the reviewer caught that green CI did not:**
  - `su` dies under `--cap-drop ALL`, so every production terminal would have closed at once.
  - Ubuntu's minimized image replaces `man` with a stub, which matters in a Linux course.
  - A reconnect truncated the command log that checks rely on.
  - In the loader, a `FAIL` line followed by `PASS` for the same item counted as passed.
  - One broken copy of a lab would have hidden the live one.

  Each finding came with a reproduction, and CI was green for all of them.
- **Windows vs Linux in one lint rule.** ruff grouped `app` imports differently on the laptop than in CI, so local lint was green while CI was red. The fix was to state `known-first-party = ["app"]` explicitly. The check was to run ruff on the old file and see it now fail locally too.
- **Numbers:** 2 issues closed, 3 PRs merged, 4 decisions recorded (D18–D21), 2 review rounds per PR. The image is in GHCR with both tags at one digest.

## 2026-11-14 · Fifth pass (#4, #6): the database and login, with no database on the laptop

- **CI as the only Postgres.** The laptop has no Docker and no Postgres, so every database test skipped locally and ran for real only in GitHub Actions (a `postgres:16` service container). An exam-integrity gate that fails on *skipped* tests turned "it skipped" into "it must run there": 21 schema tests and 20 login tests ran against a real database as the restricted role, on every PR.
- **Prove the exam before the code merges.** #4 needed a CI change, so it shipped as two PRs (D19). A draft "evidence" PR stacked the exam on the code branch, so the exam ran green against the code before the code reached `main`. Then the exam shipped on its own.
- **The reviewer kept finding what the tests could not.** For #4: a `revoke` that looked like it closed access to `public` did nothing, because the role's access came through `PUBLIC` (the exam passed anyway, because Postgres 15 changed the default). The factory did not "fix" it: the database is shared with another app, so it became an owner check (O9) with the exact query to run. For #6: the reviewer fired 100 parallel wrong passwords at the login and all 100 got through a "10 per 15 minutes" limit. The fix records each attempt before any await, and caps argon2 so a flood cannot eat the CPU the owner's bots need.
- **A test harness bug, not a code bug.** The first CI run of #6 failed with "Future attached to a different loop": the exam's second test client ran on its own event loop while the app's database pool belonged to the first one. The manager fixed the harness (one line, no assertion changed). The per-commit gate then rejected an exam change after code, so the branch was re-sequenced (exam, harness fix, code) into a new PR rather than force-pushed.
- **Agents in parallel, one conductor.** While the reviewer read #4's round 2, the exam for #6 was being written in another worktree from the decision D23; the #6 implementer started the minute #4 merged.
- **Numbers:** 2 issues, 3 merged PRs (#34, #38, #40), 2 decisions (D22, D23) amended once each after review, 4 review rounds, 1 owner item (O9), 0 secrets touched.

## 2026-11-14 · Third pass (#18, #19, #25): the dashboard went live, and the owner worked alongside the factory

- **Three PRs in about 55 minutes, hands-off.** #23 (dashboard), #24 (publish to https://factory.shashkist.com) and #27 (deploy key limited to `main`, pinned host key) were merged by one laptop pass. Each had an exam written first, CI and a fresh-context review. The pass stopped on its own at the "3 merged PRs" budget.
- **The reviewer broke the nginx installer twice, and that was the point.** The script edits the `default.conf` that every site on the shared server uses. Round 1 reproduced an `END`-before-`BEGIN` marker silently deleting the n8n and OpenClaw blocks. Round 2 found that a half-written backup could be "restored" over a conf nobody had touched yet, and that pruning would delete the owner's own hand-made `.bak` files. Each finding came with a reproduction, and each became a unit test. Nothing ever ran against the real server.
- **Owner and factory in parallel, without collisions.** While the factory built #25, Ilia's own session moved the deploy secrets into the new `production` environment with a fresh key (O8 step 1) and pushed to `main`. The factory's reviewer noticed the new script on `main` and adjusted its verdict. The manager saw the resulting incident and fixed its root cause in the same PR.
- **A self-inflicted incident, caught by the line.** The owner's SSH calls plus the publish step's three new connections made the server refuse port 22 for a moment. The incident workflow opened #26 (p0). The manager confirmed it was transient with a re-run, then fixed the cause (one multiplexed SSH connection plus a retry). #26 closed with the live run as evidence.
- **Evidence over assertions.** "Done" for the dashboard meant `curl` against the live site: 200, `no-cache`, `noindex`, a `generated_at` from the run that had just finished, the CSP present and no Google Fonts. It also meant the deployment record showing that `main` deployed to `production`.
- **Windows quirk:** in Git Bash on Windows, a `\n` inside a Python heredoc can reach the file as a real newline. Multi-line edits to YAML and Python files are safer with the editor tool than with shell heredocs.

## 2026-11-14 · Course design session (syllabus)

- **Spell-check as a side effect.** Asking "is the folder name spelled right?" got `multi-engeneering` fixed to `multi-engineering` in one turn.
- **Claude reads Hebrew PDFs directly.** The original the partner college syllabus (right-to-left Hebrew mixed with English terms) was read straight from the PDF, with no conversion step.
- **Sessions talking to each other.** The course session used `ListAgents` + `SendMessage` to ask other running sessions what they knew, instead of Ilia re-explaining. Two sessions had the same `openclaw-*` name. Claude found which one wrote the lab-platform spec by searching the local transcripts (`~/.claude/projects/*.jsonl`, plain JSONL that grep can read), then sent it the course decisions that affect the labs. That session answered with one real conflict: "agent mode" can't run inside a v1 lab machine. The syllabus was corrected the same minute.
- **Bringing in an outside chat.** A long design discussion from a chat app was pasted into the CLI. Claude checked it against the original syllabus and found the gap (300h in the original vs 250h in the draft). Answer: 250h with the teacher plus 50h of self-practice labs.
- **A PDF from scratch, no new installs.** A hand-written RTL HTML page was printed to PDF with headless Edge, and PyMuPDF stamped the original logo at the same coordinates as in the source PDF. Same look as the original, 7 pages.
- **Lesson learned: shared checkouts.** Two sessions working in one folder means one session's `git add` can pick up the other's files. Keep separate areas (`courses/` for course work, `docs/factory/` for the factory) or use worktrees.
- **AI proposes, the human edits.** Ilia's final syllabus kept the AI-first spine (AI from day one, a checkpoint without AI in every lesson, Loop Engineering → Software Factory), but cut about a third of Claude's additions (ITSM lesson, Observability, Game Day, AI-mode badges) to stay close to what the partner college expects. Claude then saved the final version to memory with an explicit "these were dropped, don't bring them back as decided" note, and sent the new lesson numbering to the lab-platform session, so the labs follow the final course and not the draft.

## 2026-11-14 · Factory setup notes (owner session)

- **2026-11-14 · Owner pre-approved a hands-off factory.** Ilia approved in advance: (1) the hourly cloud routine that runs the manager on Opus, (2) a laptop scheduled task that starts Claude Code headless when the cloud marks work `needs:laptop`, and (3) a one-time script that installs the repo's GitHub secrets **without anyone seeing a value**: a brand-new deploy key just for this repo (not his personal SSH key), a database user with a freshly generated password, and the DNS record created through the DigitalOcean API. Lesson: approvals for secrets are given once, explicitly and in writing, and the script prints only names and "✓ set".
- **2026-11-14 · The factory started itself.** No cloud: the laptop runs it. A Windows scheduled task (at logon + every 30 min) checks GitHub for work and starts `claude -p --model claude-opus-5-5 --permission-mode auto` in the factory's own worktree, with a daily budget of 10 passes and a PAUSE file. Two minutes after the first start, the manager had claimed issue #1, updated STATE.md and recorded its own first decision (D9). Nobody typed anything.
- **Windows PowerShell 5.1 traps, found and fixed in one evening:** (1) double quotes inside a `jq` argument to a native exe are silently stripped, so the "is there work?" check said 0; (2) `@()` around an empty JSON array counts 1; (3) with `$ErrorActionPreference = 'Stop'`, git's progress on stderr kills the script without a log line; (4) a second worktree cannot check out `main`, so the factory gets its own branch `factory` that follows `origin/main`.
- **Why not the cloud routine:** the only Claude account in the browser was the company organisation, so the 24/7 cloud manager for a personal project was stopped before connecting GitHub. The laptop path keeps every file the same, so the cloud can be added later from a personal account.
