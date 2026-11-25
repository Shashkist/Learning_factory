"""Exam for #605: the merge gate accepts the reusable exam-integrity check name (D25).

NO NETWORK. `_check_reasons` is tested directly, plus end-to-end cases through
`merge_gate.main(argv, run=fake_gh)` with a fake gh whose every other fact is green.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

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
REUSABLE = "exam-integrity / exam-integrity"
NOT_RUN = "exam-integrity has not run"


def check(name, conclusion="SUCCESS", status="COMPLETED"):
    return {"__typename": "CheckRun", "name": name, "status": status,
            "conclusion": conclusion}


def with_gate_defaults(rollup):
    """D160: a non-empty world lacking `manager-exam` / `changes` gets a SUCCESS one, so
    these cases keep testing the exam-integrity rule only."""
    if not rollup:
        return rollup
    names = {c["name"] for c in rollup}
    return rollup + [check(n) for n in ("manager-exam", "changes") if n not in names]


def reasons(*checks):
    return merge_gate._check_reasons(with_gate_defaults(list(checks)))


# criterion 1: the exact name behaves as today

def test_exact_name_success_is_accepted():
    assert reasons(check("unit"), check("exam-integrity")) == []


@pytest.mark.parametrize("state", ["SKIPPED", "NEUTRAL", "FAILURE"])
def test_exact_name_not_success_holds(state):
    out = reasons(check("unit"), check("exam-integrity", state))
    assert out == ["exam-integrity ended in " + state.lower()]


def test_exact_name_pending_holds():
    out = reasons(check("exam-integrity", "", "IN_PROGRESS"))
    assert out == ["exam-integrity is pending"]


def test_missing_exact_name_still_says_not_run():
    assert reasons(check("unit")) == [NOT_RUN]


# criterion 2: a name ending in " / exam-integrity" counts

def test_reusable_name_success_is_accepted():
    assert reasons(check("unit"), check(REUSABLE)) == []


def test_other_caller_job_name_success_is_accepted():
    assert reasons(check("acceptance / exam"), check("ci / exam-integrity")) == []


@pytest.mark.parametrize("state", ["SKIPPED", "NEUTRAL", "FAILURE"])
def test_reusable_name_not_success_holds_naming_the_check(state):
    out = reasons(check("unit"), check(REUSABLE, state))
    assert out == [REUSABLE + " ended in " + state.lower()]


def test_reusable_name_pending_holds_naming_the_check():
    out = reasons(check("unit"), check(REUSABLE, "", "IN_PROGRESS"))
    assert out == [REUSABLE + " is pending"]


# criterion 3: names that only contain the word do not count

@pytest.mark.parametrize("name", [
    "exam-integrity-lint",
    "exam-integrity / other",
    "x / exam-integrity-2",
    "xexam-integrity",
    "foo/exam-integrity",
])
def test_lookalike_name_does_not_count(name):
    assert reasons(check("unit"), check(name)) == [NOT_RUN]


def test_lookalike_next_to_a_real_check_does_not_add_a_hold():
    assert reasons(check(REUSABLE), check("exam-integrity-lint")) == []


# end to end through main()

class Gh:
    def __init__(self, rollup):
        now = issue_lock.now()
        claim = issue_lock.render({"session": SESSION, "branch": "f/5-x", "worktree": "C:/wt",
                                   "claimed": now, "heartbeat": now, "step": "ship"})
        self.claim = {"body": claim, "url": "https://github.com/o/r/issues/5#issuecomment-77"}
        self.pr = {
            "author": {"login": "Shashkist"},
            "body": "What.\n\nCloses #5\n",
            "headRefOid": HEAD,
            "commits": [{"oid": HEAD}],
            "statusCheckRollup": rollup,
            "comments": [{"author": {"login": "Shashkist"},
                          "body": "Review round 1: APPROVE at " + HEAD + "\n\nOk.",
                          "createdAt": "2026-11-24T10:00:00Z"}],
        }

    def __call__(self, argv):
        assert argv[0] not in {"merge", "comment", "review", "edit", "close"}
        if argv[:2] == ["pr", "view"]:
            pr = dict(self.pr)
            pr["statusCheckRollup"] = with_gate_defaults(pr["statusCheckRollup"])
            return json.dumps(pr)
        if argv[0] == "api":
            return json.dumps({"files": [{"filename": "labs/manager/app/thing.py"}]})
        if argv[:2] == ["issue", "view"]:
            return json.dumps({"comments": [self.claim]})
        return ""


def gate(capsys, rollup):
    code = merge_gate.main(["9", "--session", SESSION], run=Gh(rollup))
    return code, capsys.readouterr().out


def test_main_merges_with_reusable_exam_integrity_success(capsys):
    code, out = gate(capsys, [check("acceptance / exam"), check(REUSABLE)])
    assert (code, out.strip()) == (0, "MERGE")


def test_main_holds_on_skipped_reusable_exam_integrity(capsys):
    code, out = gate(capsys, [check("unit"), check(REUSABLE, "SKIPPED")])
    assert code == 1
    assert "HOLD: " + REUSABLE + " ended in skipped" in out
    assert NOT_RUN not in out
    assert "MERGE" not in out


def test_main_holds_with_lookalike_only(capsys):
    code, out = gate(capsys, [check("unit"), check("exam-integrity-lint")])
    assert code == 1
    assert "HOLD: " + NOT_RUN in out
