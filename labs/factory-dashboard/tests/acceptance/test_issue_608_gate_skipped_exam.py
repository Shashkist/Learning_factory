"""Exam for #608 (D160): the merge gate holds unless the exam gate check concluded SUCCESS.

NO NETWORK. The gate is driven through `merge_gate.main(argv, run=fake_gh)`. The fake gh
serves one PR (#9) whose every other fact is green, and serves the PR's changed files three
ways, all consistent, so any reasonable implementation can read them: `files[].path` in
`pr view --json`, the per-commit `api repos/.../commits/<oid>` and `pr diff --name-only`.

Rules under test: the exam gate check is `manager-exam` (`--exam-gate "<name>"`, exact match)
and must be SUCCESS; `docker` SKIPPED passes only when `changes` is SUCCESS and the PR does
not change `.github/workflows/ci.yml`; every other check keeps today's rule.
"""

import importlib.util
import json
import re
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
EXAM = "manager-exam"
CI_YML = ".github/workflows/ci.yml"
CODE = "labs/manager/app/thing.py"
WRITES = {"merge", "comment", "review", "edit", "close", "reopen", "ready"}


def check(name, conclusion="SUCCESS", status="COMPLETED"):
    return {"__typename": "CheckRun", "name": name, "status": status, "conclusion": conclusion}


def green(*extra, exam=True, changes=True):
    base = [check("manager"), check("web"), check("tooling"), check("exam-integrity")]
    if exam:
        base.append(check(EXAM))
    if changes:
        base.append(check("changes"))
    return base + list(extra)


def entry(f):
    """A changed file: a path, or a ready-made GitHub file object."""
    return f if isinstance(f, dict) else {"filename": f, "status": "modified"}


def renamed(new, old):
    return {"filename": new, "previous_filename": old, "status": "renamed"}


PAGE = 2  # files per page the fake serves with --paginate


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
    """Fake gh. `commits` is a list of file lists, oldest first, one per commit oid; the
    commits API serves each commit its own files. Without --paginate only page 1 comes back;
    with --paginate every page is concatenated as JSON objects, one per page, like gh does."""

    def __init__(self, rollup, files=(CODE,), commits=None):
        now = issue_lock.now()
        claim = issue_lock.render({"session": SESSION, "branch": "f/5-x", "worktree": "C:/wt",
                                   "claimed": now, "heartbeat": now, "step": "ship"})
        self.claim = {"body": claim, "url": "https://github.com/o/r/issues/5#issuecomment-77"}
        per_commit = [list(files)] if commits is None else [list(c) for c in commits]
        self.oids = [format(i + 1, "040x") for i in range(len(per_commit) - 1)] + [HEAD]
        pairs = zip(self.oids, per_commit, strict=True)
        self.by_oid = {o: [entry(f) for f in fs] for o, fs in pairs}
        net = [e["filename"] for es in self.by_oid.values() for e in es]
        self.pr = {
            "author": {"login": "Shashkist"},
            "body": "What.\n\nCloses #5\n",
            "headRefOid": HEAD,
            "commits": [{"oid": o} for o in self.oids],
            "statusCheckRollup": rollup,
            "files": [{"path": f} for f in net],
            "comments": [{"author": {"login": "Shashkist"},
                          "body": "Review round 1: APPROVE at " + HEAD + "\n\nOk.",
                          "createdAt": "2026-11-24T10:00:00Z"}],
        }
        self.net = net

    def __call__(self, argv):
        assert argv[0] not in WRITES and argv[:2] != ["pr", "merge"], "gate must be read-only"
        assert "-X" not in argv, "gate must be read-only"
        if argv[:2] == ["pr", "view"]:
            return json.dumps(self.pr)
        if argv[:2] == ["pr", "diff"]:
            return "\n".join(self.net) + "\n"
        if argv[0] == "api":
            listing = pr_api(argv, self.oids)
            if listing is not None:
                return listing
            path = next(a for a in argv[1:] if a.startswith("repos/"))
            es = self.by_oid[path.rsplit("/commits/", 1)[1].split("?")[0]]
            pages = [es[i:i + PAGE] for i in range(0, len(es), PAGE)] or [[]]
            if "--paginate" not in argv:
                pages = pages[:1]
            return "".join(json.dumps({"files": pg}) for pg in pages)
        if argv[:2] == ["issue", "view"]:
            return json.dumps({"comments": [self.claim]})
        return ""


def gate(capsys, rollup, *extra, files=(CODE,), commits=None):
    # the exam gate is named explicitly (D166); a later --exam-gate in `extra` wins
    argv = ["9", "--session", SESSION, "--exam-gate", EXAM, *extra]
    code = merge_gate.main(argv, run=Gh(rollup, files, commits))
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    return code, lines


def hold_lines(lines):
    return [ln for ln in lines if ln.startswith("HOLD: ")]


def assert_held_naming(code, lines, name):
    assert code == 1 and "MERGE" not in lines, lines
    named = [ln for ln in hold_lines(lines) if name in ln]
    assert named, lines
    return named


# criterion 1: manager-exam must be SUCCESS

@pytest.mark.parametrize("state", ["SKIPPED", "NEUTRAL", "FAILURE"])
def test_manager_exam_not_success_holds_with_a_line_naming_it(capsys, state):
    code, lines = gate(capsys, green(check(EXAM, state), exam=False))
    assert_held_naming(code, lines, EXAM)
    assert len(hold_lines(lines)) == 1, lines


def test_missing_manager_exam_holds_with_a_line_naming_it(capsys):
    code, lines = gate(capsys, green(exam=False))
    assert_held_naming(code, lines, EXAM)
    assert len(hold_lines(lines)) == 1, lines


def test_manager_exam_success_merges(capsys):
    assert gate(capsys, green()) == (0, ["MERGE"])


# criterion 1: --exam-gate renames the required check, exact match

def test_exam_gate_option_renames_the_required_check(capsys):
    rollup = green(check("ci / exam gate", "SKIPPED"), exam=False)
    code, lines = gate(capsys, rollup, "--exam-gate", "ci / exam gate")
    assert_held_naming(code, lines, "ci / exam gate")


def test_exam_gate_option_success_merges_without_manager_exam(capsys):
    rollup = green(check("ci / exam gate"), exam=False)
    assert gate(capsys, rollup, "--exam-gate", "ci / exam gate") == (0, ["MERGE"])


def test_configured_name_replaces_the_default_one(capsys):
    rollup = green(check(EXAM, "SKIPPED"), check("ci / exam gate"), exam=False)
    assert gate(capsys, rollup, "--exam-gate", "ci / exam gate") == (0, ["MERGE"])


def test_configured_name_missing_holds(capsys):
    code, lines = gate(capsys, green(), "--exam-gate", "ci / exam gate")
    assert_held_naming(code, lines, "ci / exam gate")


@pytest.mark.parametrize("lookalike", ["manager-exam-2", "x / manager-exam", "manager-examx"])
def test_lookalike_name_does_not_satisfy_the_exam_gate(capsys, lookalike):
    code, lines = gate(capsys, green(check(lookalike), exam=False))
    assert_held_naming(code, lines, EXAM)


def test_lookalike_does_not_satisfy_a_configured_name(capsys):
    rollup = green(check("x / ci / exam gate"), check("ci / exam gate-2"))
    code, lines = gate(capsys, rollup, "--exam-gate", "ci / exam gate")
    assert_held_naming(code, lines, "ci / exam gate")


# criterion 2: a skipped docker

def test_docker_skipped_with_changes_success_and_no_ci_yml_merges(capsys):
    assert gate(capsys, green(check("docker", "SKIPPED"))) == (0, ["MERGE"])


def test_pr_606_check_set_still_merges(capsys):
    rollup = [check("manager"), check("dashboard"), check(EXAM), check("web"),
              check("changes"), check("exam-integrity"), check("tooling"),
              check("docker", "SKIPPED"), check("image-term", "SKIPPED"),
              check("image-manager", "SKIPPED")]
    files = ["scripts/merge_gate.py", "docs/factory/DECISIONS.md"]
    assert gate(capsys, rollup, files=files) == (0, ["MERGE"])


def test_docker_skipped_with_a_ci_yml_change_holds_naming_docker(capsys):
    code, lines = gate(capsys, green(check("docker", "SKIPPED")), files=[CODE, CI_YML])
    assert_held_naming(code, lines, "docker")


@pytest.mark.parametrize("changes", [None, "SKIPPED", "FAILURE"])
def test_docker_skipped_without_changes_success_holds(capsys, changes):
    rollup = green(check("docker", "SKIPPED"), changes=False)
    if changes:
        rollup.append(check("changes", changes))
    code, lines = gate(capsys, rollup)
    assert_held_naming(code, lines, "docker")


def test_docker_success_with_a_ci_yml_change_is_not_held_for_docker(capsys):
    code, lines = gate(capsys, green(check("docker")), files=[CODE, CI_YML])
    assert (code, lines) == (0, ["MERGE"])


def test_ci_yml_change_without_a_docker_check_holds_naming_docker(capsys):
    # corrected by D163 (#611): this case expected MERGE; with a workflow change only a
    # docker check that concluded SUCCESS proves the heavy exam ran
    code, lines = gate(capsys, green(), files=[CI_YML])
    assert_held_naming(code, lines, "docker")


def test_ci_yml_change_with_docker_success_merges(capsys):
    assert gate(capsys, green(check("docker")), files=[CI_YML]) == (0, ["MERGE"])


# criterion 3: every other check keeps today's rule; exam integrity keeps its own

@pytest.mark.parametrize("state", ["SKIPPED", "NEUTRAL"])
def test_other_checks_skipped_or_neutral_still_pass(capsys, state):
    rollup = green(check("image-term", state), check("lint", state))
    assert gate(capsys, rollup) == (0, ["MERGE"])


@pytest.mark.parametrize("state", ["FAILURE", "CANCELLED"])
def test_other_check_failing_still_holds(capsys, state):
    code, lines = gate(capsys, green(check("lint", state)))
    assert_held_naming(code, lines, "lint")


def test_other_check_pending_still_holds(capsys):
    code, lines = gate(capsys, green(check("lint", "", "IN_PROGRESS")))
    assert_held_naming(code, lines, "lint")


@pytest.mark.parametrize("state", ["SKIPPED", "NEUTRAL", "FAILURE"])
def test_exam_integrity_not_success_still_holds(capsys, state):
    rollup = [c for c in green() if c["name"] != "exam-integrity"]
    code, lines = gate(capsys, rollup + [check("exam-integrity", state)])
    assert_held_naming(code, lines, "exam-integrity")


def test_exam_integrity_missing_still_says_not_run(capsys):
    rollup = [c for c in green() if c["name"] != "exam-integrity"]
    code, lines = gate(capsys, rollup)
    assert "HOLD: exam-integrity has not run" in lines


def test_no_checks_at_all_still_holds(capsys):
    code, lines = gate(capsys, [])
    assert code == 1 and "HOLD: no checks have run on this PR" in lines


# D160 amendment 1: every commit, renames, any workflow file, paginated file lists

SKIPPED_DOCKER = green(check("docker", "SKIPPED"))


def test_ci_yml_renamed_to_another_workflow_name_holds_naming_docker(capsys):
    files = [renamed(".github/workflows/ci2.yml", CI_YML)]
    code, lines = gate(capsys, SKIPPED_DOCKER, commits=[files])
    assert_held_naming(code, lines, "docker")


def test_ci_yml_moved_out_of_workflows_holds_naming_docker(capsys):
    code, lines = gate(capsys, SKIPPED_DOCKER, commits=[[renamed("x/ci.yml", CI_YML)]])
    assert_held_naming(code, lines, "docker")


def test_another_workflow_file_changed_holds_naming_docker(capsys):
    files = [CODE, ".github/workflows/deploy.yml"]
    code, lines = gate(capsys, SKIPPED_DOCKER, commits=[files])
    assert_held_naming(code, lines, "docker")


def test_ci_yml_changed_only_in_the_first_commit_holds_naming_docker(capsys):
    code, lines = gate(capsys, SKIPPED_DOCKER, commits=[[CI_YML], [CODE]])
    assert_held_naming(code, lines, "docker")


def test_two_commits_without_a_workflow_change_merge(capsys):
    commits = [[CODE], ["docs/factory/STATE.md", "scripts/merge_gate.py"]]
    assert gate(capsys, SKIPPED_DOCKER, commits=commits) == (0, ["MERGE"])


def test_ci_yml_only_on_the_second_page_of_a_commit_holds_naming_docker(capsys):
    files = [CODE, "scripts/a.py", "scripts/b.py", "scripts/c.py", CI_YML]
    assert len(files) > 2 * PAGE
    code, lines = gate(capsys, SKIPPED_DOCKER, commits=[files])
    assert_held_naming(code, lines, "docker")


def test_many_files_over_several_pages_without_workflow_changes_merge(capsys):
    files = [CODE] + [f"scripts/f{i}.py" for i in range(5)]
    assert gate(capsys, SKIPPED_DOCKER, commits=[files]) == (0, ["MERGE"])


def test_docker_success_with_a_renamed_workflow_is_not_held_for_docker(capsys):
    rollup = green(check("docker"))
    files = [renamed(".github/workflows/ci2.yml", CI_YML)]
    assert gate(capsys, rollup, commits=[files]) == (0, ["MERGE"])


# D166: the default exam gate (`manager-exam` on main, `manager-exam / gate` after #613) is
# still what main() requires when no --exam-gate is given. Both names are served, so the
# cases hold under either default.

DEFAULT_GATES = ("manager-exam", "manager-exam / gate")


def default_gate(capsys, rollup):
    code = merge_gate.main(["9", "--session", SESSION], run=Gh(rollup))
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    return code, lines


def test_default_gate_success_merges(capsys):
    rollup = green(*[check(n) for n in DEFAULT_GATES], exam=False)
    assert default_gate(capsys, rollup) == (0, ["MERGE"])


def test_default_gate_missing_holds_naming_manager_exam(capsys):
    code, lines = default_gate(capsys, green(exam=False))
    assert_held_naming(code, lines, "manager-exam")


@pytest.mark.parametrize("state", ["SKIPPED", "FAILURE", "NEUTRAL"])
def test_default_gate_not_success_holds_naming_manager_exam(capsys, state):
    rollup = green(*[check(n, state) for n in DEFAULT_GATES], exam=False)
    code, lines = default_gate(capsys, rollup)
    assert_held_naming(code, lines, "manager-exam")
