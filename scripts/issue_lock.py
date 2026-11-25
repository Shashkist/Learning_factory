#!/usr/bin/env python3
"""Who is working on which issue, and what to do when they stop.

WHY THIS EXISTS. Several Claude sessions work this repo at once, and on
2026-09-15/16 they collided three times in one day: two suites testing each
other's dev server, a commit made in another session's working tree, and a
session telling the owner production lacked a fix that had been live for an
hour. Isolated worktrees fix file collisions. They do nothing about two
sessions picking up the same GitHub issue, and nothing about a session dying
with a half-finished branch nobody can find.

THE LOCK IS ADVISORY, AND SAYING SO IS THE POINT. Nothing here can stop a
determined process from editing an issue -- GitHub has no compare-and-swap on
labels, and a lock that claims to be airtight when it is not is worse than one
that admits it. What this buys is that a collision becomes VISIBLE in one
command instead of being discovered from a merge conflict three hours later.

WHY NOT `assignees`. Every session in this project authenticates as the same
GitHub user, so an assignee cannot tell `lf-manager-1` from `lf-manager-2`. The claim
therefore lives in a comment, which is the only per-issue store that can hold a
string GitHub has no opinion about.

WHY NOT A FILE IN THE REPO. A lock file is a merge conflict waiting to happen
and is stale the moment a session forgets to push it. The issue tracker is
already the shared mutable state every session can reach.

THE RECOVERY CASE IS THE REAL FEATURE. A claim records the BRANCH. When a
session dies, `stale` prints what it was doing and where the work is, so the
next session continues the branch instead of starting the issue again -- which
is the outcome this file exists to prevent.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

NL = chr(10)

# The marker that identifies OUR comment among a conversation's worth of
# others. Deliberately ugly and deliberately unique: a claim we cannot find
# again is a lock that can never be released.
MARKER = "<!-- pulsesign-issue-claim"
END = "-->"

CLAIM_LABEL = "status:in-progress"
OPEN_LABEL = "status:ready"

#: A claim older than this is presumed abandoned. Generous on purpose -- a
#: session can legitimately spend an hour on one failing test, and stealing a
#: live claim is worse than waiting for a dead one.
DEFAULT_STALE_AFTER = timedelta(minutes=90)


def _run(args: list[str]) -> str:
    """Run `gh` and return stdout. Injected in tests; never mocked in src."""
    proc = subprocess.run(
        ["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        raise RuntimeError("gh " + " ".join(args) + " failed: " + proc.stderr.strip())
    return proc.stdout


#: The repository a bare `gh` call reaches from this checkout. It never gets `-R`.
HERE_REPO = "Shashkist/Learning_factory"

#: Which repositories the factory works on; override with LF_REPOS_FILE.
REPOS_FILE = Path(__file__).resolve().parent.parent / "docs" / "factory" / "repos.yaml"


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def read_repos(path: Path | None = None) -> tuple[list[str], str, dict[str, str]]:
    """(repositories, default, clone path per repository) from repos.yaml.

    This repository alone, with one warning line on stderr, when the file is
    missing, empty or not readable. The default is always this repository
    (HERE_REPO): a bare `gh` call reaches whatever the checkout points at, so
    no file can move the default elsewhere. If the file marks another entry
    default, or lists no entry for this repository, one warning says so and
    this repository is used as the default (and read).

    A tiny parser for the flat list format (`- repo: x` then `  key: value`):
    the tooling job runs with no dependencies, so no PyYAML here.
    """
    path = path or Path(os.environ.get("LF_REPOS_FILE") or REPOS_FILE)
    entries: list[dict] = []
    try:
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.split(" #", 1)[0].rstrip()
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if line.startswith("- "):
                entries.append({})
                line = "  " + line[2:]
            key, sep, value = line.strip().partition(":")
            if not entries or not sep or not key.isidentifier():
                raise ValueError(line)
            entries[-1][key] = _unquote(value)
        unique: dict[str, dict] = {}
        for e in entries:
            unique.setdefault(e["repo"].lower(), e)
        entries = list(unique.values())
        if not entries or any(e["repo"].count("/") != 1 for e in entries):
            raise ValueError("no repositories")
    except (OSError, ValueError, KeyError):
        print(
            "warning: " + str(path) + " (repos.yaml) is missing or unreadable;"
            " using " + HERE_REPO + " only",
            file=sys.stderr,
        )
        return [HERE_REPO], HERE_REPO, {}
    repos = [HERE_REPO if e["repo"].lower() == HERE_REPO.lower() else e["repo"] for e in entries]
    clones = {r: e["clone"] for r, e in zip(repos, entries) if e.get("clone")}
    marked = [r for r, e in zip(repos, entries) if e.get("default", "").lower() in ("true", "yes")]
    listed = HERE_REPO in repos
    if not listed:
        repos.insert(0, HERE_REPO)
    if not listed or (marked and HERE_REPO not in marked):
        print(
            "warning: " + str(path) + " (repos.yaml) does not mark " + HERE_REPO
            + " as the default repository; using it as the default",
            file=sys.stderr,
        )
    return repos, HERE_REPO, clones


def scoped(run, repo: str | None):
    """`run` aimed at a named repository: `-R repo`, or the repo in an api path.

    Only the api path argument (`repos/{owner}/{repo}/...`) is rewritten; body
    text is never touched.
    """
    if not repo:
        return run

    def run_in(args: list[str]) -> str:
        if args[0] == "api":
            return run(
                [a.replace("{owner}/{repo}", repo) if a.startswith("repos/") else a
                 for a in args]
            )
        return run([*args, "-R", repo])

    return run_in


def now() -> str:
    """This machine's clock, to the second.

    TWO PROPERTIES OF THIS THAT THE TIEBREAK DEPENDS ON, both raised by a peer
    reading the race path rather than running it. Neither is a bug today and
    both are cheap to be wrong about later.

    IT IS THE CLIENT'S CLOCK. Every session in this project runs on one
    machine, so there is one clock and "earliest claim wins" means what it
    says. The day a session runs somewhere else -- a cloud runner, a second
    laptop -- that comparison quietly becomes "whoever's clock is behind
    wins", and a skewed clock would take an issue from an earlier claimant
    deterministically and invisibly. The fix, if that day comes, is to sort on
    GitHub's own `createdAt` for the comment, which is server-side and
    monotonic across machines. Not worth the extra request while there is one
    clock.

    IT IS TRUNCATED TO THE SECOND, so two claims in the same second carry an
    identical `claimed` and the tuple comparison falls through to the session
    NAME. That is what makes the race resolvable at all -- both sides compute
    the same answer with no negotiation -- but it means that inside any one
    second the rule is not "first claim wins", it is "alphabetically-first
    session name wins". Since a real race resolves inside a second by
    definition, that is the common case rather than an edge one. The property
    the lock actually owes you is that both sides reach the SAME verdict
    without talking, and that holds either way.
    """
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_claim(body: str) -> dict | None:
    """The JSON inside a claim comment, or None if this is not one.

    Returns None rather than raising for a malformed claim: a comment somebody
    hand-edited must not take the whole tool down, and an unreadable claim is
    treated as no claim -- which errs towards letting work proceed.
    """
    if MARKER not in body:
        return None
    try:
        raw = body.split(MARKER, 1)[1].split(END, 1)[0]
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except (ValueError, IndexError):
        return None


def render(claim: dict) -> str:
    """The comment body: machine-readable first, human-readable after.

    Both, always. The JSON is what `status` reads; the sentence underneath is
    what a person scrolling the issue sees, and an issue whose lock is
    invisible to humans just moves the collision somewhere else.
    """
    step = claim.get("step") or "no step recorded"
    head = MARKER + NL + json.dumps(claim, indent=2, sort_keys=True) + NL + END
    said = (
        "**Claimed by `" + claim["session"] + "`** - branch `" + claim["branch"] + "`"
        + NL + NL
        + "Last heartbeat `" + claim["heartbeat"] + "` - _" + step + "_"
        + NL + NL
        + "<sub>Advisory lock. If this session is gone, "
        "`python scripts/issue_lock.py stale` will say so and name the branch "
        "to continue.</sub>"
    )
    return head + NL + said


def retraction(session: str, reason: str) -> str:
    """A claim withdrawn. Same marker, so the next reader finds one comment."""
    payload = json.dumps({"released": now(), "by": session, "reason": reason})
    return (
        MARKER + NL + payload + NL + END + NL
        + "Released by `" + session + "`" + (" - " + reason if reason else "")
    )


def find_all_claims(number: int, run=_run) -> list[tuple[dict, int | None]]:
    """Every claim comment on an issue, in the order GitHub returns them.

    Plural because two sessions CAN both write one. The label index that
    `status` reads lags a write by a second or two, so two sessions claiming in
    the same breath each see an unclaimed issue and each post. Comments, unlike
    the label search, are consistent immediately -- which is what lets
    `cmd_claim` resolve a race the label index cannot even see.
    """
    raw = run(["issue", "view", str(number), "--json", "comments"])
    out: list[tuple[dict, int | None]] = []
    for comment in json.loads(raw).get("comments", []):
        claim = parse_claim(comment.get("body", ""))
        if claim is not None:
            # The comment id: `gh` gives a url ending in #issuecomment-<id>.
            match = re.search(r"issuecomment-(\d+)", comment.get("url", ""))
            out.append((claim, int(match.group(1)) if match else None))
    return out


def find_claim(number: int, run=_run) -> tuple[dict | None, int | None]:
    """The live claim on an issue, and the comment id to write through.

    A released claim still yields its comment id: reusing it keeps one claim
    comment per issue instead of a stack of them, which is what makes a
    heartbeat cheap enough to run on every step.
    """
    claims = find_all_claims(number, run)
    for claim, cid in claims:
        if "released" not in claim:
            return claim, cid
    return (None, claims[0][1]) if claims else (None, None)


def age_minutes(iso: str) -> float:
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        # An unparseable heartbeat is treated as ancient, not as fresh: the
        # failure mode of "assume alive" is a permanently locked issue.
        return float("inf")
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds() / 60


def write_comment(number: int, body: str, comment_id: int | None, run=_run) -> None:
    if comment_id is None:
        run(["issue", "comment", str(number), "--body", body])
    else:
        # Edited in place rather than appended: a heartbeat every few minutes
        # would otherwise bury the issue's actual conversation.
        run(
            [
                "api",
                "-X",
                "PATCH",
                "repos/{owner}/{repo}/issues/comments/" + str(comment_id),
                "-f",
                "body=" + body,
            ]
        )


def live_rivals(number: int, session: str, stale_after: float, run=_run):
    """Other sessions' claims that are neither released nor yet abandoned."""
    return [
        (c, cid)
        for c, cid in find_all_claims(number, run)
        if "released" not in c
        and c.get("session") != session
        and age_minutes(c.get("heartbeat", "")) < stale_after
    ]


def unparked_branch(number: int, run=_run) -> str | None:
    """The branch an un-park told the next session to continue, if the comment
    a claim would reuse is that release (#595)."""
    claims = find_all_claims(number, run)
    if not claims:
        return None
    found = re.search(r"continue branch ([^\s(]\S*)", str(claims[0][0].get("reason") or ""))
    return found.group(1) if found else None


def cmd_claim(args, run=_run) -> int:
    run = scoped(run, getattr(args, "repo", None))
    existing, comment_id = find_claim(args.number, run)

    if existing is None and comment_id is not None and not (args.steal and args.reason):
        old = find_all_claims(args.number, run)[0][0]
        named = unparked_branch(args.number, run)
        if named and named != args.branch:
            print(
                "#" + str(args.number) + " was un-parked: " + str(old.get("reason"))
                + NL + "Claim it with --branch " + named
                + ", or --steal with --reason to start another branch.",
                file=sys.stderr,
            )
            return 1

    if existing and existing.get("session") != args.session:
        age = age_minutes(existing.get("heartbeat", ""))
        if age < args.stale_after and not args.steal:
            print(
                "#" + str(args.number) + " is claimed by " + str(existing.get("session"))
                + " (branch " + str(existing.get("branch"))
                + ", heartbeat " + format(age, ".0f") + "m ago)." + NL
                + "Ask them, or --steal with --reason if you know they are gone.",
                file=sys.stderr,
            )
            return 1
        if args.steal and not args.reason:
            print("--steal requires --reason", file=sys.stderr)
            return 1

    keep_claimed_at = (
        existing.get("claimed")
        if existing and existing.get("session") == args.session
        else now()
    )
    claim = {
        "session": args.session,
        "branch": args.branch,
        "worktree": args.worktree or "",
        "claimed": keep_claimed_at,
        "heartbeat": now(),
        "step": args.step or "claimed",
    }
    if args.steal and existing:
        claim["stolen_from"] = existing.get("session")
        claim["steal_reason"] = args.reason

    write_comment(args.number, render(claim), comment_id, run)

    # READ AFTER WRITE, because the write is not a lock.
    #
    # GitHub has no compare-and-swap on an issue, and the label index `status`
    # reads lags a write by a second or two -- so two sessions starting
    # together both see "unclaimed" and both post. Comments are consistent
    # immediately, so reading them back is the only way to learn the race
    # happened at all.
    #
    # The tie-break is the earliest `claimed`, with the session name settling
    # an identical instant. Both sides run the same comparison over the same
    # data, so both reach the same answer without talking to each other -- and
    # the loser stands down rather than both charging ahead, which is the
    # outcome this whole file exists to prevent.
    if not args.steal:
        rivals = live_rivals(args.number, args.session, args.stale_after, run)
        if rivals:
            mine = (claim["claimed"], args.session)
            winner = min((c.get("claimed", ""), c.get("session", "")) for c, _ in rivals)
            if winner < mine:
                # Retract MY comment, by id -- not "the claim on this issue".
                # `find_claim` returns the first live claim it sees, which in a
                # race is as likely to be the winner's as mine, and releasing
                # the winner's claim while standing down would unlock the issue
                # for the session that just won it.
                mine_id = next(
                    (
                        cid
                        for c, cid in find_all_claims(args.number, run)
                        if c.get("session") == args.session and "released" not in c
                    ),
                    None,
                )
                if mine_id is not None:
                    write_comment(
                        args.number,
                        retraction(
                            args.session, "stood down: " + winner[1] + " claimed first"
                        ),
                        mine_id,
                        run,
                    )
                print(
                    "#" + str(args.number) + " was claimed by " + winner[1]
                    + " at the same moment and they were first. Stood down.",
                    file=sys.stderr,
                )
                return 1

    run(
        [
            "issue",
            "edit",
            str(args.number),
            "--add-label",
            CLAIM_LABEL,
            "--remove-label",
            OPEN_LABEL,
        ]
    )
    print("#" + str(args.number) + " claimed by " + args.session + " on " + args.branch)
    return 0


def cmd_heartbeat(args, run=_run) -> int:
    run = scoped(run, getattr(args, "repo", None))
    existing, comment_id = find_claim(args.number, run)
    if not existing:
        print(
            "#" + str(args.number) + " has no claim to beat. Run `claim` first.",
            file=sys.stderr,
        )
        return 1
    if existing.get("session") != args.session:
        print(
            "#" + str(args.number) + " is " + str(existing.get("session")) + "'s, not yours.",
            file=sys.stderr,
        )
        return 1
    existing["heartbeat"] = now()
    if args.step:
        existing["step"] = args.step
    # Tri-state on purpose: None leaves the flag as it was, so an ordinary
    # heartbeat neither sets nor clears it. Only an explicit --blocked or
    # --unblocked moves it, because "I am still here" and "I am waiting on a
    # human" are different statements and a session says the first far more
    # often than it changes the second.
    if args.blocked is not None:
        if args.blocked:
            existing["blocked"] = True
        else:
            existing.pop("blocked", None)
    write_comment(args.number, render(existing), comment_id, run)
    print(
        "#" + str(args.number) + " heartbeat " + existing["heartbeat"]
        + (" [held]" if existing.get("blocked") else "")
        + " - " + existing["step"]
    )
    return 0


def cmd_release(args, run=_run) -> int:
    run = scoped(run, getattr(args, "repo", None))
    existing, comment_id = find_claim(args.number, run)
    if not existing:
        print("#" + str(args.number) + " has no claim.", file=sys.stderr)
        return 0
    if existing.get("session") != args.session and not args.force:
        print(
            "#" + str(args.number) + " is " + str(existing.get("session"))
            + "'s. --force to release anyway.",
            file=sys.stderr,
        )
        return 1
    write_comment(args.number, retraction(args.session, args.reason or ""), comment_id, run)
    print("#" + str(args.number) + " released")
    return 0


PARKED_LABELS = ("status:needs-owner", "status:blocked")


def cmd_unpark(args, run=_run) -> int:
    """Hand a parked issue back to the queue: release its claim, naming the branch."""
    run = scoped(run, getattr(args, "repo", None))
    number = str(args.number)
    raw = run(["issue", "view", number, "--json", "labels"])
    names = {x["name"] for x in json.loads(raw).get("labels", [])}
    if not names & set(PARKED_LABELS):
        print("#" + number + " is not parked (no status:needs-owner or status:blocked).",
              file=sys.stderr)
        return 1
    live = [(c, cid) for c, cid in find_all_claims(args.number, run) if "released" not in c]
    for c, _ in live:
        age = age_minutes(c.get("heartbeat", ""))
        if (c.get("session") != args.session and age < DEFAULT_STALE_AFTER.total_seconds() / 60
                and not args.force):
            print(
                "#" + number + " is held by " + str(c.get("session"))
                + " (heartbeat " + format(age, ".0f") + "m ago). --force to un-park anyway.",
                file=sys.stderr,
            )
            return 1
    for c, cid in live:
        branch = str(c.get("branch") or "") or "(no branch recorded)"
        write_comment(
            args.number, retraction(args.session, "un-parked; continue branch " + branch),
            cid, run,
        )
        print("#" + number + " claim released; continue branch " + branch)
    print("#" + number + (" un-parked" if live else " un-parked; no claim to release"))
    run(["issue", "edit", number,
         "--remove-label", ",".join(PARKED_LABELS), "--add-label", "status:ready"])
    return 0


#: How far back the recently-updated sweep looks. Generous: its only job is to
#: cover the window in which the label index has not caught up.
RECENT_MINUTES = 15


def _candidates(run=_run) -> list[dict]:
    """Issues that might carry a claim, from FIVE queries unioned: one per claim
    label (in-progress, in-review, needs-owner, blocked; gh ANDs repeated --label flags,
    so each label is its own call) plus the recently-updated sweep.

    An issue relabelled in-review, needs-owner or blocked keeps its claim comment but
    drops out of the recent window, so those labels must be read too (#592).

    NEITHER QUERY IS TRUSTWORTHY ALONE, and finding that out cost a peer a
    confusing minute: they claimed #153, `claim` confirmed it, and `status`
    immediately answered "No issues are claimed."

    `claim` already reads back through the comments API because the label
    index lags a write by a second or two. `status` did not -- it went
    through the lagging path alone, so the first thing a session does after
    claiming could tell it the claim had not happened. That is the same
    hazard the lock exists to prevent, one layer out: a `status` that
    under-reports invites exactly the double-claim it is meant to stop.

    So: the label queries are the cheap wide net, and a recently-updated sweep
    covers the window they have not indexed yet -- posting a claim comment bumps
    `updatedAt` immediately. Five list calls per repository, and the comment
    reads stay bounded to what the sets return (deduped by number) rather than
    every open issue.
    """
    labelled = [
        i
        for label in (CLAIM_LABEL, "status:in-review", "status:needs-owner",
                       "status:blocked")
        for i in json.loads(
            run(["issue", "list", "--label", label, "--state", "open",
                 "--json", "number,title", "-L", "100"])
        )
    ]
    recent_raw = json.loads(
        run(["issue", "list", "--state", "open", "--json", "number,title,updatedAt",
             "-L", "100"])
    )
    by_number = {i["number"]: {"number": i["number"], "title": i["title"]} for i in labelled}
    for i in recent_raw:
        if age_minutes(i.get("updatedAt", "")) < RECENT_MINUTES:
            by_number.setdefault(i["number"], {"number": i["number"], "title": i["title"]})
    return list(by_number.values())


def _claimed_issues(run=_run, args=None, failed: list | None = None) -> list[dict]:
    """Claim rows of every repository read. `failed` collects the repositories
    that could not be read (each also printed once, as skipped)."""
    out = []
    default = getattr(args, "default_repo", HERE_REPO)
    only = getattr(args, "repo", None)
    for repo in [only] if only else getattr(args, "repos", None) or [default]:
        # The default repository is reached with bare calls, as always.
        scope = scoped(run, None if repo == default else repo)
        rows = []
        try:
            for issue in _candidates(scope):
                claim, _ = find_claim(issue["number"], scope)
                if claim and "released" not in claim:
                    rows.append(
                        {**issue, "repo": repo, "claim": claim,
                         "age": age_minutes(claim.get("heartbeat", ""))}
                    )
        except (RuntimeError, ValueError):
            print("cannot read " + repo + " - skipped", file=sys.stderr)
            if failed is not None:
                failed.append(repo)
            continue
        out.extend(rows)
    return out


def claimed(repos: list[str], run=_run, failed: list | None = None) -> list[dict]:
    """Claim rows (with `repo` and `number`) of the given repositories; `failed`
    collects the ones that could not be read."""
    args = argparse.Namespace(repos=repos, default_repo=HERE_REPO, repo=None)
    return _claimed_issues(run, args, failed)


def _default_unreadable(args, failed: list) -> bool:
    """The repository the caller depends on could not be read: this repository,
    or the one named with --repo."""
    return (getattr(args, "repo", None) or getattr(args, "default_repo", HERE_REPO)) in failed


def cmd_status(args, run=_run) -> int:
    failed: list = []
    rows = _claimed_issues(run, args, failed)
    if _default_unreadable(args, failed):
        # Silence here would read as "nothing is claimed" and invite a double claim.
        return 1
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    if not rows:
        print("No issues are claimed.")
        return 0
    print("    #  repo                         session          age  branch                                 step")
    for r in sorted(rows, key=lambda r: -r["age"]):
        c = r["claim"]
        print(
            format(r["number"], ">5") + "  "
            + format(r["repo"], "<28") + " "
            + format(c.get("session", ""), "<14") + " "
            + format(r["age"], ">5.0f") + "m  "
            + format(c.get("branch", ""), "<38") + " "
            # A held claim reads as abandoned at a glance otherwise -- the age
            # is the same number either way, and the age is what a reader
            # scans. Saying it here means nobody has to run `stale` to find out
            # that a five-hour-old claim is deliberate.
            + ("[held] " if c.get("blocked") else "")
            + str(c.get("step", ""))
        )
    return 0


def cmd_stale(args, run=_run) -> int:
    """Claims nobody has touched recently -- the recovery path.

    Prints the BRANCH, because the point is not to free the issue, it is to let
    the next session continue work that already exists. An issue released
    without its branch sends someone back to the beginning, which is the
    duplicated effort this file is here to stop.

    SILENCE HAS TWO CAUSES AND THEY WANT OPPOSITE RESPONSES. This command
    measures silence, and for a long time offered `--steal` under everything it
    found. But a session that DIED waiting is safe to take over, and one that
    is WAITING ON A HUMAN is not: stealing a parked claim duplicates work that
    is already finished, which is the precise collision this file exists to
    prevent.

    The asymmetry decides the default. Failing to steal a dead claim costs
    delay; stealing a live one costs two sessions writing the same code. So a
    claim whose holder said it was blocked is still LISTED -- visibility is the
    whole point and hiding it would be worse -- but the take-over command is
    withheld, because a suggestion printed under a claim that is merely patient
    is what makes the wrong thing the easy thing.

    Found for real, not reasoned: two sessions sat five days awaiting an
    owner's decision on a push. Both claims read as stale and stealable, and
    both were holding finished work.
    """
    failed: list = []
    claimed = _claimed_issues(run, args, failed)
    if _default_unreadable(args, failed):
        return 1
    rows = [r for r in claimed if r["age"] >= args.older_than]
    if not rows:
        print("No claim is older than " + format(args.older_than, ".0f") + "m.")
        return 0
    for r in rows:
        c = r["claim"]
        other = r["repo"] != getattr(args, "default_repo", HERE_REPO)
        clone = getattr(args, "clones", {}).get(r["repo"])
        git = "git -C '" + clone.replace("\\", "/") + "' " if other and clone else "git "
        common = (
            "#" + str(r["number"]) + " " + r["title"] + NL
            + "    repo       " + r["repo"] + NL
            + "    claimed by " + str(c.get("session"))
            + " - silent " + format(r["age"], ".0f") + "m" + NL
            + "    branch     " + str(c.get("branch")) + NL
            + "    worktree   " + (str(c.get("worktree")) or "(not recorded)") + NL
            + "    last step  " + str(c.get("step")) + NL
            + "    continue   " + git + "fetch origin && " + git + "log --oneline origin/main.."
            + str(c.get("branch")) + NL
        )
        if c.get("blocked"):
            # Absence of the key means not blocked, so every claim written
            # before this flag existed stays stealable. The alternative --
            # treating unknown as held -- would freeze them all on one deploy.
            print(
                common
                + "    HELD       deliberately, blocked on something off this"
                + " machine. Do NOT steal:" + NL
                + "               the work is probably finished and parked."
                + " Ask " + str(c.get("session")) + " first." + NL
            )
            continue
        print(
            common
            + "    take over  python scripts/issue_lock.py claim " + str(r["number"])
            + " --session <you> --branch " + str(c.get("branch"))
            + (" --repo " + r["repo"] if other else "")
            + " --steal --reason 'session gone'" + NL
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Advisory per-issue locking for parallel sessions."
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("number", type=int)
        sp.add_argument(
            "--session", required=True, help="this session's name, from ListAgents"
        )
        sp.add_argument("--repo", help="owner/name, when not this repository")

    c = sub.add_parser("claim", help="take an issue, or fail if someone live holds it")
    common(c)
    c.add_argument("--branch", required=True)
    c.add_argument("--worktree")
    c.add_argument("--step")
    c.add_argument("--steal", action="store_true")
    c.add_argument("--reason")
    c.add_argument(
        "--stale-after", type=float, default=DEFAULT_STALE_AFTER.total_seconds() / 60
    )
    c.set_defaults(func=cmd_claim)

    h = sub.add_parser("heartbeat", help="say you are still here, and what you are doing")
    common(h)
    h.add_argument("--step")
    # Tri-state: absent leaves the flag alone, so an ordinary heartbeat does
    # not silently clear a block. Only an explicit flag moves it.
    h.add_argument(
        "--blocked", dest="blocked", action="store_true", default=None,
        help="waiting on someone off this machine; `stale` will not offer a steal",
    )
    h.add_argument(
        "--unblocked", dest="blocked", action="store_false",
        help="the wait is over; the claim becomes stealable again if it goes quiet",
    )
    h.set_defaults(func=cmd_heartbeat)

    r = sub.add_parser("release", help="hand the issue back")
    common(r)
    r.add_argument("--reason")
    r.add_argument("--force", action="store_true")
    r.set_defaults(func=cmd_release)

    u = sub.add_parser("unpark", help="parked issue back to ready; release its claim")
    common(u)
    u.add_argument("--force", action="store_true",
                   help="release a live claim of another session")
    u.set_defaults(func=cmd_unpark)

    s = sub.add_parser("status", help="who holds what")
    s.add_argument("--json", action="store_true")
    s.add_argument("--repo", help="owner/name: show only this repository")
    s.set_defaults(func=cmd_status)

    t = sub.add_parser(
        "stale", help="claims whose session has gone quiet, and how to continue them"
    )
    t.add_argument(
        "--older-than", type=float, default=DEFAULT_STALE_AFTER.total_seconds() / 60
    )
    t.add_argument("--repo", help="owner/name: show only this repository")
    t.set_defaults(func=cmd_stale)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.repos, args.default_repo, args.clones = read_repos()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
