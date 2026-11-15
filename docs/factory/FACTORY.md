# How the factory works

The factory turns the spec into a running lab platform at `labs.shashkist.com` with as little human time as possible. It follows the pattern from Ilia's "Software Factory" talk: **WHEN a trigger fires, DO small loops (make · check · fix) UNTIL a clear definition of done, THEN hand off to the next station, NEVER cross the guardrails.**

## Roles

| Role | Model | Job |
|---|---|---|
| **Manager** | Opus 5.5 (`claude-opus-5-5`) or Fable 5.2 | Runs the loop. Picks the issue, makes decisions, splits work, dispatches workers, merges, closes. Owns `STATE.md` and `DECISIONS.md`. Never writes product code itself when a worker can. |
| **Planner** (manager hat) | same | Keeps 5–10 `status:ready` issues in the queue, sliced from the spec, each small enough for one PR (≤ ~400 changed lines). |
| **Researcher** | Sonnet 5.5 | Answers design questions with evidence: how KodeKloud / Killercoda / iximiuz Labs / Instruqt do it, library docs. Returns a recommendation plus sources. |
| **Exam writer** | Sonnet 5.5 | Writes the acceptance tests for an issue **from the spec and the issue only**, before implementation, in `tests/acceptance/`. The implementer cannot change them (the hidden exam). |
| **Implementer** | Sonnet 5.5, own worktree | Makes the exam and its own unit tests pass. Minimum code. Reuses before writing. |
| **Reviewer** | Opus 5.5, fresh context, read-only | Tries to refute the PR against the spec, `CLAUDE.md` rules and security. Approves or returns findings. Two rounds max, then the manager decides. |
| **Second opinion** | a model from another company (OpenRouter, GPT class) in CI | Reviews every PR diff and posts findings as a PR comment. Optional: runs only when the `OPENROUTER_API_KEY` secret exists. |
| **Scribe** | Haiku 5.5 | After a merge: updates `STATE.md`, the spec where reality differs, and `history.md`. |

## The loop (one manager run)

1. **Load memory.** Read `STATE.md`, `gh issue list --state open`, the open PRs and the latest CI and deploy runs. Release stale claims (`scripts/issue_lock.py stale`).
2. **Fix the line first.** A red `main`, a failed deploy or a failed smoke test goes to the top: open or reopen an issue `type:incident` and work it now.
3. **Plan.** If fewer than 5 issues are `status:ready`, slice more from the spec or the next phase (the planner hat) and label them.
4. **Pick** the highest-priority `status:ready` issue whose dependencies (`Depends on #n`) are closed. **Claim** it (`issue_lock.py claim`), label it `status:in-progress`, and write the step to `STATE.md`.
5. **Decide.** Any open design question gets researched (researcher), decided, and recorded in `DECISIONS.md` (D-number, revertible). Only owner-only blockers go to `OWNER.md` (then label `status:needs-owner` and return to step 4).
6. **Exam.** The exam writer adds acceptance tests on the branch. Commit.
7. **Build.** The implementer works in a worktree until unit tests, lint, types and the exam pass locally. Commit and push, then open the PR (body: what, why, how verified, `Closes #n`).
8. **Check.** Wait for CI (integration tests with Docker + gVisor, exam integrity, second opinion). Then the reviewer. Fix loop: at most 5 rounds; after that, record what is left and split it into a follow-up issue.
9. **Ship.** Squash-merge. The deploy workflow ships `main`. The smoke workflow runs against the live server.
10. **Close** with evidence (PR, deploy run, smoke output), set `status:done`, release the claim, update `STATE.md`, and append a line to `history.md` if something was learned.
11. **Repeat** from step 2 until the run's budget is used (see Cadence), then write the resume point in `STATE.md` and stop cleanly.

**Heartbeat:** at every step, update the claim's step (`issue_lock.py heartbeat`) and the `Now` section of `STATE.md`. A session that dies leaves its last step behind for the next one.

## Triggers

| Trigger | What happens |
|---|---|
| **Every hour** (cloud routine "learning-factory-manager") | one manager run; it resumes from `STATE.md` |
| **Ilia says "continue the factory"** in any Claude Code session | the same run, locally, with SSH available |
| **CI red on `main`, a failed deploy, or a failed smoke test** | a workflow opens an issue `type:incident` with `priority:p0`; the next run takes it first |
| **Nightly at 02:00 Israel time** | a smoke workflow starts and ends 30 lab sessions and checks that memory and disk returned to baseline (the cleanup guarantee) |

## Labels

- `status:ready` · `status:in-progress` · `status:in-review` · `status:needs-owner` · `status:blocked` · `status:done` · `status:superseded`
- `type:feature` · `type:infra` · `type:bug` · `type:incident` · `type:content` (lab content) · `type:research` · `type:epic`
- `priority:p0` (line down) · `priority:p1` · `priority:p2` · `priority:p3`
- `phase:1` · `phase:2` · `phase:later`

## Memory: what lets any session continue

| File / place | Holds | Written |
|---|---|---|
| `docs/factory/STATE.md` | **Now** (issue, step, branch), **Next** (ordered), **Recently done**, **Health** | at every step, committed straight to `main` |
| issue claim comments | who holds the issue, branch, last step | by `issue_lock.py` |
| `docs/factory/DECISIONS.md` | decisions and assumptions, each revertible | when decided |
| `docs/factory/OWNER.md` | owner actions and questions | when needed; the owner ticks them |
| `history.md` | insights for Ilia's POC presentation | after notable events |

## Guardrails (the NEVER part)

- **NEVER** touch Ilia's other services. The rules are in `CLAUDE.md` §1.
- **NEVER** install or read secrets. The owner installs them.
- **NEVER** merge with red CI, or merge a PR that changes `tests/acceptance/**` together with the code it tests (the exam-integrity check fails it).
- **NEVER** spend more than one run on a single issue without splitting it.
- **NEVER** deploy anything that makes a lab container reach the host, other containers, or the open internet (spec §6).
- **Cost guard:** a run stops starting new issues after ~90 minutes of work or 3 merged PRs, whichever comes first.

## Actions minute budget

The free private plan gives 2,000 Actions minutes a month. In November about 1,860 were used in two days (#98, incident #96). Where they went: CI on push to main 886, CI on pull requests 681, dashboard on push 215, everything else about 80. The `docker` job alone cost 681.

What a PR costs now: `manager` plus `web` plus `exam-integrity`, roughly 8 to 10 minutes. A PR that touches `labs/images`, `labs/manager`, `labs/deploy`, `labs/content` or `.github/workflows` also runs `docker` and the image jobs (about 15 more). A docs-only push to main costs nothing; a code push costs the full run.

Rules:
- **Batch state commits:** one `docs/factory/*` state commit per loop step, not one per sub-step. Each one that reaches the dashboard filter still costs a run.
- Docs-only paths are listed in `scripts/ci/docs-only-paths.txt`; `ci.yml` `paths-ignore` must match it.
- Do not re-run CI to see if it passes. Read the failure first.

## Decisions without the owner

The manager decides. Order of evidence: the spec → `DECISIONS.md` → how KodeKloud does it (observed behaviour is in spec §2) → other lab platforms → the simplest thing that keeps the guardrails. Every decision gets a D-number, the options considered, why, and **how to revert it**. Ilia reads `DECISIONS.md` when he wants and reverts by writing "revert D12" in an issue or a session.
