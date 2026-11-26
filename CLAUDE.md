# Learning Factory: working in this repository

This repo is built by a **software factory**: an autonomous manager agent that picks GitHub issues, builds them with worker agents, ships them and closes them with evidence. Humans steer it. They don't do the work.

**If you were asked to "continue the factory" (or "continue", "resume"), load the `factory` skill and follow it.** It starts from `docs/factory/STATE.md`.

Read first:
- `docs/superpowers/specs/2026-11-14-lab-platform-design.md`: what we are building (the spec).
- `docs/factory/FACTORY.md`: how the factory works (roles, loop, labels, guardrails).
- `docs/factory/STATE.md`: where the factory stopped and what is next.
- `docs/factory/DECISIONS.md`: every decision and assumption the factory made. **When two documents disagree, this one settles it.**
- `docs/factory/OWNER.md`: what is waiting for the owner (Ilia).

## Layout

```
labs/manager/   FastAPI lab manager (API, auth, sessions, proxy, janitor)
labs/web/       React + Vite UI (student app, teacher dashboard), en/he
labs/images/    lab container images (lf-lab-term, lf-lab-code)
labs/content/   lab definitions (lab.yaml + tasks), one folder per lab
labs/deploy/    compose file, nginx include, server bootstrap scripts
courses/        NOT in git (ignored, D43): course material lives outside the public repo. On the laptop: C:\projects\lf-private\courses (lesson plan: multi-engineering/lesson-plan.md)
docs/           spec, factory docs, decisions
```

## Rules that are never broken

1. **The owner's other services come first.** The production server (`207.154.193.238`) also runs OpenClaw, n8n and client apps. Never restart Docker, never edit another site's nginx block, never touch containers whose name does not start with `lf-` or `lab-`. Shared-config changes (nginx, `/etc/docker/daemon.json`, iptables) happen only through the idempotent, validated scripts in `labs/deploy/` (`nginx -t` before reload; backup, then restore on failure; `systemctl reload docker`, never restart).
2. **Secrets are installed by the owner, never by an agent.** Agents wire the reference (`${{ secrets.NAME }}`, env names), make absence a supported state (degrade and log a line), and write the exact command for the owner into `docs/factory/OWNER.md`. Never print, commit, test with, or "verify" a secret value. Verify by behaviour.
3. **No attribution.** No `Co-Authored-By`, no "Generated with Claude Code", no mention of Claude, AI or agents in commits, PR titles and bodies, or issue comments. Write as a normal engineering team.
4. **Never blocked.** If a decision is needed, decide it (research first: KodeKloud, Killercoda, iximiuz Labs, Instruqt), record it in `DECISIONS.md` as revertible, and continue. If only the owner can unblock (a secret, DNS, money), add it to `OWNER.md`, comment on the issue, label it `status:needs-owner`, and **move to another issue**.
5. **Evidence before assertions.** A green unit test is not a working lab. Done means: CI green, deployed, and a smoke test against the live server says what you expected. Name what you expected to see, then check that it is there.
6. **Finishing means closing the issue in the same breath:** close with a comment that says how you know (PR, deploy run, smoke output), flip the label to `status:done`, update `STATE.md`.
7. **Student data is minimal:** name + keyed ID hash. Never log a raw ID, never put real student data in tests or fixtures.
8. **Lab isolation.** Lab containers never reach the host, other containers, the internet or a secret.

## Conventions for exams and reviews

- Exam files live in `labs/<component>/tests/acceptance/`, one per issue: `test_issue_<n>_<slug>.py` (pytest) or `issue-<n>-<slug>.spec.ts` (Playwright).
- Tests that need Docker or gVisor are marked `@pytest.mark.docker`; they run in CI, not in cloud sessions.
- Manager unit tests go in `labs/manager/tests/unit/`; web unit tests stay next to the code as `labs/web/src/**/*.test.ts(x)`.
- Any other path under `labs/*/tests/`, and any `conftest.py`, `pytest.ini`, `tox.ini` or `setup.cfg` under `labs/` outside `tests/unit/`, counts as exam, and exam integrity refuses a commit that mixes exam and code.
- Lab containers never reach the host, other containers or the internet.
- The reviewer checks that memory and disk come back after a session ends.
- Researchers check the docs of the libraries involved (ttyd, code-server, gVisor, FastAPI, xterm.js); KodeKloud's observed behaviour is in spec §2.
- CI does not format-check, so `ruff format` is not run on older files.

## Git

- Commits, pushes, PR merges and branch cleanup in this repo are **pre-approved by the owner**. Do them without asking.
- One issue = one branch `f/<issue>-<slug>` = one PR into `main`. Squash-merge when CI is green and the reviewer approved. Delete the branch.
- Never push straight to `main`, except `docs/factory/*` state updates (journal commits), which go directly.
- Before merging: `git log --oneline origin/main..HEAD`. Know exactly what you are shipping.
- **The local checkout `C:\projects\Learning_factory` is shared with other sessions** (for example course work under the ignored `courses/` folder). Commit **by explicit path list only**, never `git add -A` or `git add .`. Read a file before overwriting it. A local factory run works in its own worktree `../Learning_factory-factory` on the local branch `factory` (following `origin/main`).
- Commit messages: conventional (`feat(manager): ...`, `fix(web): ...`), with the issue number (`#12`).

## Verification commands

From `labs/manager/`: `uv run pytest -q`, `uv run ruff check .`, `uv run mypy app`.
From `labs/web/`: `npm test -- --run`, `npx tsc --noEmit`, `npm run lint`, `npm run build`.
From the repository root (factory tooling): `uvx pytest scripts -q`.
From `labs/factory-dashboard/` (dashboard and the factory tooling exams): `uv run ruff check .`, `uv run pytest -q`.
Integration tests that need Docker and gVisor run in GitHub Actions (`.github/workflows/ci.yml`). Cloud sessions have no Docker; do not fake it.

## Factory facts

The project profile is `.factory/factory.yaml` (limits, verify commands, exam, required checks, smoke, areas); CI checks it in the `tooling` job.

- **State commits to main.** The factory worktree is created with `git worktree add -B factory ../Learning_factory-factory origin/main` (local branch `factory`, following `origin/main`). State updates there are committed with the message `chore(factory): state, ...` and pushed with `git pull --rebase origin main && git push origin HEAD:main`.
- **Per-issue worktrees.** An issue of this repository is built in `C:\projects\lf-<n>`; an issue of Shashkist/factory in a worktree of its clone, `C:\projects\factory-<n>`.
- **Independent areas.** Issues that change different areas can run side by side, for example `labs/content`, `labs/manager` and `.github`; two issues in the same file cannot.
- **Merge gate exam check.** The merge gate's exam check in this repository is `manager-exam / gate`; with the factory tools run `factory-tools merge-gate <pr> --session <you> --exam-gate "manager-exam / gate"` (the tool's default `acceptance / gate` is the factory repository's).
- **Deploy watch.** After a merge, watch the Deploy workflow run (`deploy.yml`) to the end before the smoke check.
- **Smoke target.** `https://labs.shashkist.com`, or the server address with a `Host: labs.shashkist.com` header until DNS exists.
