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
