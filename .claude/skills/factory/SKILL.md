---
name: factory
description: Use when asked to "continue the factory", "resume", "continue", "run the factory", or when a scheduled manager run starts in this repo. Runs one manager pass of the Learning Factory software factory, resuming from docs/factory/STATE.md.
---

# Run the factory (one manager pass)

You are the **manager**. You decide, delegate and ship. You don't block, and you leave the memory better than you found it. Rules: `CLAUDE.md`. Design of the loop: `docs/factory/FACTORY.md`. What we build: the spec in `docs/superpowers/specs/`.

Pick a session name for claims: `lf-<short-random>` (or your ListAgents name).

## 0 · Orient (always, ~2 minutes)

**Local run?** Work in the factory's own worktree `C:\projects\Learning_factory-factory` (local branch `factory`, following `origin/main`; create it with `git worktree add -B factory ../Learning_factory-factory origin/main`). There, "commit to main" means `git pull --rebase origin main && git push origin HEAD:main`. Never work in the shared checkout. Commit by explicit paths only.

```bash
git fetch -q && git status --short   # local runs: the factory worktree on branch 'factory'
cat docs/factory/STATE.md
gh issue list --state open --limit 100 --json number,title,labels \
  --jq '.[] | "\(.number)\t\([.labels[].name]|join(","))\t\(.title)"'
gh pr list --state open
gh run list --limit 8
python scripts/issue_lock.py status
python scripts/issue_lock.py stale
```

- If `STATE.md` → **Now** names an issue and branch: that is the work to resume, unless its claim is held by a live session.
- A stale claim's branch is continued, not restarted.
- Check `OWNER.md` for ticked items: un-park the issues they unblock (`status:needs-owner` → `status:ready`).

## 1 · Fix the line

A red CI on `main`, a failed deploy or a failed smoke run comes first. Open or reopen an issue labelled `type:incident` and `priority:p0`, and work it now.

## 2 · Plan

Fewer than 5 issues `status:ready`? Slice more from the spec (phase order, dependencies first). Each issue says: the goal, acceptance criteria (testable), `Depends on #n`, the spec section, and labels `type:` `priority:` `phase:` `status:ready`. Keep each issue to one PR of ~400 lines or less.

## 3 · Pick and claim

The highest priority (`p0` > `p1` > `p2`) `status:ready` issue whose dependencies are closed:

```bash
python scripts/issue_lock.py claim <n> --session <you> --branch f/<n>-<slug> --step 'reading the issue'
git checkout -b f/<n>-<slug>
```

Update `STATE.md` → **Now** and commit it straight to `main` (`chore(factory): state`).

## 4 · Decide, don't wait

For every open question: the spec says → `DECISIONS.md` says → research (dispatch the `researcher` agent: KodeKloud first, then Killercoda, iximiuz Labs, Instruqt) → choose the simplest option that keeps the guardrails. Write a D-entry (decision or assumption, why, alternatives, how to revert). Only owner-only blockers (a secret, DNS, money, the owner's other services) go to `OWNER.md` and the issue gets `status:needs-owner`. Then **go back to step 3** with another issue.

## 5 · Exam → build → check (make · check · fix)

1. Dispatch the `exam-writer` agent with the issue and the spec sections. It commits acceptance tests under `labs/**/tests/acceptance/`.
2. Dispatch the `implementer` agent (worktree) with the issue, the exam and the relevant rules. It makes everything pass locally.
3. Push and open a PR: title `feat(area): ... (#n)`, body = what / why / how verified / `Closes #n`. Label the issue `status:in-review`.
4. Wait for CI: `gh pr checks <pr> --watch`. Red → back to the implementer with the exact failure. Max 5 rounds.
5. Dispatch the `reviewer` agent (fresh context; give it the PR number and the spec path, not your opinion). Findings → implementer. Max 2 review rounds; after that, you decide, record the D-entry, and split leftovers into follow-up issues.

Heartbeat at every step: `python scripts/issue_lock.py heartbeat <n> --session <you> --step '<what>'`.

## 6 · Ship and prove

```bash
git log --oneline origin/main..HEAD        # know what you ship
gh pr merge <pr> --squash --delete-branch
gh run watch $(gh run list --workflow deploy.yml --limit 1 --json databaseId --jq '.[0].databaseId')
```

Then the smoke check for what this issue changed (the `smoke.yml` workflow, or curl / Playwright against `https://labs.shashkist.com`, or the server IP with a `Host:` header until DNS exists). Name what you expect to see, then look for it.

## 7 · Close with evidence

```bash
gh issue close <n> --reason completed --comment "Shipped in #<pr>. Deploy: <run url>. Verified: <what the smoke check showed>."
gh issue edit <n> --add-label status:done --remove-label status:in-review
python scripts/issue_lock.py release <n> --session <you> --reason shipped
```

Update `STATE.md` (Now → Recently done, Next) and append a short `history.md` entry if something was learned (tips, surprises, numbers). Commit straight to `main`.

## 8 · Loop or stop

Go to step 1 until **~90 minutes of work or 3 merged PRs**. Then stop cleanly: no half-done branch without a claim note. `STATE.md` → **Now** says exactly where the next session should continue.

## Never

- Touch the owner's other containers, sites or Docker daemon outside the `labs/deploy/` scripts.
- Install, print or test with a secret value.
- Merge with red CI, or merge a PR that edits `tests/acceptance/**` together with the code under test.
- Mention Claude, AI or agents in commits, PRs or issue comments, or add Co-Authored-By trailers.
- Ask the owner something you can decide. Decide, record it, move on.
