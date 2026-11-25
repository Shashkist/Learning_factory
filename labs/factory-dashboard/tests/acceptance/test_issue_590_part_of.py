"""Exam for #590: the merge gate reads a line-anchored `Part of #n` (a D19 PR A).

NO NETWORK. The gate is driven through `merge_gate.main(argv, run=fake_gh)`.
The fake serves one PR whose every other fact is green, and per-issue claim
comments (`issue view <n> --json comments`), so the claim check is the only
variable. The gate is read-only: any writing gh command fails the test.
"""

import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "ci"))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


issue_lock = _load("issue_lock")
merge_gate = _load("merge_gate")

SESSION = "lf-pass-1"
HEAD = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
NO_CLOSES = "PR body has no `Closes #n` for the issue it ships"
WRITES = {"merge", "comment", "review", "edit", "close", "reopen", "ready"}


def check(name):
    return {"__typename": "CheckRun", "name": name, "status": "COMPLETED",
            "conclusion": "SUCCESS"}


def claim_comment(session=SESSION, released=False):
    if released:
        body = issue_lock.retraction(session, "done")
    else:
        now = issue_lock.now()
        body = issue_lock.render({"session": session, "branch": "f/5-x", "worktree": "C:/wt",
                                  "claimed": now, "heartbeat": now, "step": "ship"})
    return {"body": body, "url": "https://github.com/o/r/issues/5#issuecomment-77"}


def pr_api(argv, oids):
    """Answer the PR commit listing (`pulls/9/commits`, 100 per page; --paginate concatenates,
    --slurp wraps the pages; page=N picks one) and `pulls/9` (commit count); else None."""
    path = next((a for a in argv[1:] if a.startswith("repos/")), "")
    bare = path.split("?")[0]
    if bare.endswith("/pulls/9"):
        return json.dumps({"commits": len(oids)})
    if not bare.endswith("/pulls/9/commits"):
        return None
    pages = [[{"sha": o} for o in oids[i:i + 100]] for i in range(0, len(oids), 100)] or [[]]
    if "--slurp" in argv:
        return json.dumps(pages)
    if "--paginate" in argv:
        return "".join(json.dumps(p) for p in pages)
    pat = re.compile(r"page=(\d+)")
    m = next(filter(None, (pat.search(a) for a in [path, *argv])), None)
    n = int(m.group(1)) if m else 1
    return json.dumps(pages[n - 1] if n <= len(pages) else [])


class Gh:
    """Fake gh serving PR 9, green, approved for its head. claims: issue -> comments."""

    def __init__(self, body, claims):
        self.claims = claims
        self.pr = {
            "author": {"login": "Shashkist"},
            "body": body,
            "headRefOid": HEAD,
            "commits": [{"oid": HEAD}],
            "statusCheckRollup": [check("unit"), check("exam-integrity")],
            "comments": [{"author": {"login": "Shashkist"},
                          "body": "Review round 1: APPROVE at " + HEAD + "\n\nOk.",
                          "createdAt": "2026-11-24T10:00:00Z"}],
        }

    def __call__(self, argv):
        assert argv[0] not in WRITES and argv[:2] != ["pr", "merge"], "gate must be read-only"
        assert "-X" not in argv, "gate must be read-only"
        if argv[:2] == ["pr", "view"]:
            # D160: the exam gate and `changes` checks are green in this world
            pr = dict(self.pr)
            pr["statusCheckRollup"] = pr["statusCheckRollup"] + [
                check("manager-exam"), check("changes")]
            return json.dumps(pr)
        if argv[0] == "api":
            listing = pr_api(argv, [HEAD])
            if listing is not None:
                return listing
            return json.dumps({"files": [{"filename": "labs/manager/app/thing.py"}]})
        if argv[:2] == ["issue", "view"]:
            return json.dumps({"comments": self.claims.get(int(argv[2]), [])})
        return ""


def gate(capsys, body, claims):
    code = merge_gate.main(["9", "--session", SESSION], run=Gh(body, claims))
    return code, capsys.readouterr().out


def test_part_of_with_own_claim_merges(capsys):
    code, out = gate(capsys, "What.\n\nPart of #5\n", {5: [claim_comment()]})
    assert (code, out.strip()) == (0, "MERGE")


def test_part_of_is_case_insensitive_and_tolerates_whitespace(capsys):
    code, out = gate(capsys, "What.\n\n  PART OF #5  \n", {5: [claim_comment()]})
    assert (code, out.strip()) == (0, "MERGE")


def test_part_of_claimed_by_another_session_holds(capsys):
    code, out = gate(capsys, "Part of #5\n", {5: [claim_comment("lf-other")]})
    assert code == 1
    assert "HOLD: claim for #5 is not held by session " + SESSION in out
    assert "MERGE" not in out


def test_part_of_with_released_claim_holds(capsys):
    code, out = gate(capsys, "Part of #5\n", {5: [claim_comment(released=True)]})
    assert code == 1
    assert "HOLD: claim for #5 is not held by session " + SESSION in out


def test_part_of_with_no_claim_holds(capsys):
    code, out = gate(capsys, "Part of #5\n", {})
    assert code == 1
    assert "HOLD: claim for #5 is not held by session " + SESSION in out


def test_body_with_neither_closes_nor_part_of_holds_with_todays_reason(capsys):
    code, out = gate(capsys, "Just a change.\n", {5: [claim_comment()]})
    assert code == 1
    assert out.strip() == "HOLD: " + NO_CLOSES


def test_part_of_mid_sentence_does_not_count(capsys):
    code, out = gate(capsys, "This is part of #5 work.\n", {5: [claim_comment()]})
    assert code == 1
    assert out.strip() == "HOLD: " + NO_CLOSES


def test_closes_and_part_of_need_both_claims(capsys):
    body = "Closes #5\nPart of #6\n"
    code, out = gate(capsys, body, {5: [claim_comment()], 6: [claim_comment()]})
    assert (code, out.strip()) == (0, "MERGE")


def test_closes_and_part_of_holds_when_the_part_of_claim_is_missing(capsys):
    code, out = gate(capsys, "Closes #5\nPart of #6\n", {5: [claim_comment()]})
    assert code == 1
    assert "HOLD: claim for #6 is not held by session " + SESSION in out
    assert "claim for #5" not in out


def test_closes_and_part_of_holds_when_the_closes_claim_is_missing(capsys):
    code, out = gate(capsys, "Closes #5\nPart of #6\n", {6: [claim_comment()]})
    assert code == 1
    assert "HOLD: claim for #5 is not held by session " + SESSION in out
    assert "claim for #6" not in out
