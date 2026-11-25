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
| **Reviewer** | Opus 5.5, fresh context, read-only | Tries to refute the PR against the spec, `CLAUDE.md` rules and security. Approves or returns findings. Two rounds max, then the manager decides (D145); a fix the manager makes after round 2 is checked by a fresh reviewer whose verdict is posted, never a self-written approval (D148 amendment 3). |
| **Second opinion** | a model from another company (OpenRouter, GPT class) in CI | Reviews every PR diff and posts findings as a PR comment. Optional: runs only when the `OPENROUTER_API_KEY` secret exists. |
| **Scribe** | Haiku 5.5 | After a merge: updates `STATE.md`, the spec where reality differs, and `history.md`. |

## The loop (one manager run)

1. **Load memory.** Read `STATE.md`, `gh issue list --state open`, the open PRs and the latest CI and deploy runs, for every repository in `repos.yaml` (`gh ... -R <repo>`). Release stale claims (`scripts/issue_lock.py stale`). An open Dependabot PR is handled before picking an issue (see "Dependabot PRs" below).
2. **Fix the line first.** A red `main`, a failed deploy or a failed smoke test goes to the top: open or reopen an issue `type:incident` and work it now.
3. **Plan.** If fewer than 5 issues are `status:ready`, slice more from the spec or the next phase (the planner hat) and label them.
4. **Pick** the highest-priority `status:ready` issue whose dependencies (`Depends on #n`) are closed, in any repository of `repos.yaml`: `python scripts/factory_pick.py` prints the order: incident p0, p0, p1, p2, p3, oldest first (`--repo <owner/name>` limits it). **Claim** it (`issue_lock.py claim`, with `--repo` for another repository), label it `status:in-progress`, and write the step to `STATE.md`.
5. **Decide.** Any open design question gets researched (researcher), decided, and recorded in `DECISIONS.md` (D-number, revertible). Only owner-only blockers go to `OWNER.md` (then label `status:needs-owner` and return to step 4).
6. **Exam.** The exam writer adds acceptance tests on the branch. Commit. **A contradiction in the exams is the factory's to settle, not the owner's (O23 lesson).** When an issue's criteria contradict each other or an older exam (for example a new name check against older exact-name checks), the manager picks the option that keeps every older exam as strict as it is, records it as a D-entry, and dispatches the exam writer to correct only the new exam: an exam-only commit before any code, on a fresh `f/<n>-<slug>-2` branch if code was already committed (D103). The reviewer checks that no exam got weaker. Loosening an older exam is allowed only when the issue's spec section requires it, and the D-entry names each loosened case. The manager never edits an exam itself, and such an issue is never parked on the owner.
7. **Build.** The implementer works in a worktree until unit tests, lint, types and the exam pass locally. Commit and push, then open the PR (body: what, why, how verified, `Closes #n`).
8. **Check.** Wait for CI (integration tests with Docker + gVisor, exam integrity, second opinion). Then the reviewer. Fix loop: at most 5 rounds; after that, record what is left and split it into a follow-up issue.
9. **Ship.** `python scripts/merge_gate.py <pr> --session <you>` must print `MERGE` (D148); then squash-merge with `--match-head-commit <the head the gate checked>`. The deploy workflow ships `main`. The smoke workflow runs against the live server.
10. **Close** with evidence (PR, deploy run, smoke output), set `status:done`, release the claim, update `STATE.md`, and append a line to `history.md` if something was learned.
11. **Repeat** from step 2 until the run's budget is used (see Cadence), then write the resume point in `STATE.md` and stop cleanly.

**Two repositories (D143).** Up to 3 issues in flight, counted across both. Work on `Shashkist/factory` happens in a worktree of its clone (`C:\projects\factory-<n>`); `gh` calls pass `-R`, `issue_lock.py` calls pass `--repo`, and that repository's own `CLAUDE.md`, `DECISIONS.md` and `OWNER.md` apply. Shipped there means what the `ship:` field of `repos.yaml` says (for `main-ci`: green CI on its `main` after the merge). `STATE.md` → Now names the repository of each in-flight issue.

**Heartbeat:** at every step, update the claim's step (`issue_lock.py heartbeat`) and the `Now` section of `STATE.md`. A session that dies leaves its last step behind for the next one.

## Triggers

| Trigger | What happens |
|---|---|
| **Every hour** (cloud routine "learning-factory-manager") | one manager run; it resumes from `STATE.md` |
| **Ilia says "continue the factory"** in any Claude Code session | the same run, locally, with SSH available |
| **CI red on `main`, a failed deploy, or a failed smoke test** | a workflow opens an issue `type:incident` with `priority:p0`; the next run takes it first |
| **Nightly at 02:00 Israel time** | a smoke workflow starts and ends 30 lab sessions and checks that memory and disk returned to baseline (the cleanup guarantee) |

## Minute budget

A full CI run (on a PR or on a push to main) costs about **29 billable minutes**: `manager` ~12, `docker` ~10, `web` ~3, every other job ~1 (three green PR runs, 2026-11-20, each job rounded up the way GitHub bills). Since the repository went public (D43, D46) hosted minutes are free, so these rules now save queue and wall-clock time (public repositories still have a concurrent-job limit). For scale, from the private-repository period (#98, 737 runs over about two days of factory work): CI on push to main 886 minutes, CI on pull requests 681, dashboard publish on push 215; the account hit its 2,000-minute limit (#96).

- Batch state commits: one per step, not one per sub-step.
- A push to main that touches only `docs/**`, `courses/**`, `.claude/**` or the root file `history.md` runs no CI and no Deploy (#271); a `docs/factory` push still runs the dashboard workflow. Other root `*.md` and everything under `labs/` count as code. A green CI deploys when its commit is on main and main differs from it only in docs (`scripts/ci_paths.py deploys`, the same start-to-end comparison GitHub's `paths-ignore` makes), so a docs-only commit landing meanwhile does not stale it, and older code never rolls production back (D85).
- On a PR, the `docker` and `image-*` jobs run only when the change touches `labs/images/`, `labs/manager/`, `labs/deploy/`, `labs/content/` or `.github/workflows/`; pushes to main always run them.
- PR runs are cancelled when a newer commit lands on the same PR. Main runs are never cancelled.
- The dashboard publishes on push only when `docs/factory/**` or the dashboard code changed.

## Labels

- Un-park a `status:needs-owner` or `status:blocked` issue with `python scripts/issue_lock.py unpark <n> --session <you>`: it releases the held claim, names the branch to continue, and relabels to `status:ready`.
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

## Dependabot PRs (#485, D138)

`.github/dependabot.yml` keeps the SHA-pinned actions current: one grouped PR a week for minor and patch bumps, one PR per major bump, and only releases at least 7 days old. Dependabot moves each pin and its `# vX.Y.Z` comment together. The factory handles an open Dependabot PR before picking an issue, without an issue, a claim or an exam:
- **CI green** (the pin check in `scripts/test_ci_workflows.py` runs on it).
- **Quick review:** for each bumped action, the new SHA is the commit behind the named tag in the upstream repository, and the release notes show no changed inputs or defaults that our workflows use. A major bump also gets a look at every job that uses the action.
- **Squash-merge** when both hold. A merged bump counts toward the 3-PR cost guard. `.github/workflows/` is heavy (`scripts/ci_paths.py`), so the merge runs the full CI and a Deploy that replaces `lf-manager`, like any heavy PR. The manager's quick review replaces the reviewer agent for these PRs.
- **A bump that cannot pass** (red pin check because the upstream tag is not `vX.Y.Z`, or a breaking major): pin that action by hand on a normal `f/` branch if needed, then tell Dependabot to stop proposing it. On a single-action PR comment `@dependabot ignore this major version` (or `@dependabot ignore this dependency`); on the grouped PR name the action: `@dependabot ignore <owner/action>` or `@dependabot ignore <owner/action> minor version`. Record it in `DECISIONS.md` under D138.

## Decisions without the owner

The manager decides. Order of evidence: the spec → `DECISIONS.md` → how KodeKloud does it (observed behaviour is in spec §2) → other lab platforms → the simplest thing that keeps the guardrails. Every decision gets a D-number, the options considered, why, and **how to revert it**. Ilia reads `DECISIONS.md` when he wants and reverts by writing "revert D12" in an issue or a session.
