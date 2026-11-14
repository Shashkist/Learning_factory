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
