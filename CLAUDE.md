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
courses/        course material (not built by the factory unless an issue says so)
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

## Git

- Commits, pushes, PR merges and branch cleanup in this repo are **pre-approved by the owner**. Do them without asking.
- One issue = one branch `f/<issue>-<slug>` = one PR into `main`. Squash-merge when CI is green and the reviewer approved. Delete the branch.
- Never push straight to `main`, except `docs/factory/*` state updates (journal commits), which go directly.
- Before merging: `git log --oneline origin/main..HEAD`. Know exactly what you are shipping.
- Commit messages: conventional (`feat(manager): ...`, `fix(web): ...`), with the issue number (`#12`).

## Verification commands

From `labs/manager/`: `uv run pytest -q`, `uv run ruff check .`, `uv run mypy app`.
From `labs/web/`: `npm test -- --run`, `npx tsc --noEmit`, `npm run lint`, `npm run build`.
Integration tests that need Docker and gVisor run in GitHub Actions (`.github/workflows/ci.yml`). Cloud sessions have no Docker; do not fake it.
