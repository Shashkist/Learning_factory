"""Exam for #611 (D163): a workflow change needs a docker check that concluded SUCCESS.

NO NETWORK. Same fake gh as the #608 exam (loaded from its file), extended with the paginated
`repos/{owner}/{repo}/pulls/9/commits` API (a JSON array of `{"sha": ...}` per page, 100 per
page; `--paginate` concatenates the pages, `--slurp` wraps them in one array, `page=N` in the
path or a `-f page=N` field picks a page) and `repos/{owner}/{repo}/pulls/9` (`commits` is the
PR's commit count). `pr view --json commits` is capped at 100 entries, like the real one.
Per-commit file lists are served exactly as in the #608 exam.
"""

import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def _import(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


base = _import(HERE / "test_issue_608_gate_skipped_exam.py", "exam608_for_611")
merge_gate = base.merge_gate
check, green, renamed, assert_held_naming = (base.check, base.green, base.renamed,
                                             base.assert_held_naming)
CODE, CI_YML = base.CODE, base.CI_YML
DEPLOY_YML = ".github/workflows/deploy.yml"
PER_PAGE = 100


class PrGh(base.Gh):
    def __init__(self, rollup, commits, listed=None, count=None, fail_listing=False):
        super().__init__(rollup, commits=commits)
        self.pr["commits"] = self.pr["commits"][:100]
        self.listed = self.oids if listed is None else self.oids[:listed]
        self.count = len(self.oids) if count is None else count
        self.fail_listing = fail_listing

    def _page(self, argv, path):
        for a in [path, *argv]:
            m = re.search(r"\bpage=(\d+)", a)
            if m:
                return int(m.group(1))
        return 1

    def __call__(self, argv):
        if argv[0] == "api":
            path = next(a for a in argv[1:] if a.startswith("repos/"))
            bare = path.split("?")[0]
            if re.fullmatch(r"repos/.+/pulls/9", bare):
                return json.dumps({"commits": self.count})
            if re.fullmatch(r"repos/.+/pulls/9/commits", bare):
                if self.fail_listing:
                    raise RuntimeError("gh api failed: HTTP 502")
                pages = [[{"sha": o} for o in self.listed[i:i + PER_PAGE]]
                         for i in range(0, len(self.listed), PER_PAGE)] or [[]]
                if "--slurp" in argv:
                    return json.dumps(pages)
                if "--paginate" in argv:
                    return "".join(json.dumps(p) for p in pages)
                n = self._page(argv, path)
                return json.dumps(pages[n - 1] if n <= len(pages) else [])
        return super().__call__(argv)


def gate(capsys, rollup, *, commits, **kw):
    argv = ["9", "--session", base.SESSION, "--exam-gate", base.EXAM]  # explicit (D166)
    code = merge_gate.main(argv, run=PrGh(rollup, commits, **kw))
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    return code, lines


# criterion 1: with a workflow change, docker must be SUCCESS

WORKFLOW_CHANGES = {
    "ci_yml_modified": [[CODE, CI_YML]],
    "another_workflow_file": [[CODE, DEPLOY_YML]],
    "previous_name_only_under_workflows": [[renamed("x/ci.yml", CI_YML)]],
    "new_name_only_under_workflows": [[renamed(".github/workflows/ci2.yml", "x/ci.yml")]],
    "only_in_an_early_commit": [[CI_YML], [CODE], ["scripts/a.py"]],
}
BAD_DOCKER = {
    "missing": green(),
    "renamed_and_skipped": green(check("docker (heavy)", "SKIPPED")),
    "skipped": green(check("docker", "SKIPPED")),
    "neutral": green(check("docker", "NEUTRAL")),
    "cancelled": green(check("docker", "CANCELLED")),
}


@pytest.mark.parametrize("docker", BAD_DOCKER)
@pytest.mark.parametrize("change", WORKFLOW_CHANGES)
def test_workflow_change_without_a_successful_docker_holds_naming_docker(capsys, change, docker):
    code, lines = gate(capsys, BAD_DOCKER[docker], commits=WORKFLOW_CHANGES[change])
    assert_held_naming(code, lines, "docker")


@pytest.mark.parametrize("change", WORKFLOW_CHANGES)
def test_workflow_change_with_docker_success_is_not_held_for_docker(capsys, change):
    rollup = green(check("docker"), check("docker (heavy)", "SKIPPED"))
    assert gate(capsys, rollup, commits=WORKFLOW_CHANGES[change]) == (0, ["MERGE"])


def test_no_workflow_change_keeps_the_608_rules_skipped_docker_merges(capsys):
    rollup = green(check("docker", "SKIPPED"))
    assert gate(capsys, rollup, commits=[[CODE], ["scripts/a.py"]]) == (0, ["MERGE"])


def test_no_workflow_change_and_no_docker_check_merges(capsys):
    assert gate(capsys, green(), commits=[[CODE]]) == (0, ["MERGE"])


# criterion 2: every commit of the PR is read, or the gate holds

def test_workflow_change_beyond_the_first_100_commits_holds_naming_docker(capsys):
    commits = [[CODE] for _ in range(105)]
    commits[102] = [CI_YML]
    code, lines = gate(capsys, green(check("docker", "SKIPPED")), commits=commits)
    assert_held_naming(code, lines, "docker")


def test_workflow_change_beyond_the_first_100_commits_without_docker_holds(capsys):
    commits = [[CODE] for _ in range(105)]
    commits[102] = [CI_YML]
    code, lines = gate(capsys, green(), commits=commits)
    assert_held_naming(code, lines, "docker")


def test_over_100_commits_without_a_workflow_change_do_not_hold_for_docker(capsys):
    commits = [[CODE] for _ in range(105)]
    code, lines = gate(capsys, green(check("docker", "SKIPPED")), commits=commits)
    assert not [ln for ln in lines if ln.startswith("HOLD: ") and "docker" in ln], lines


def _held_saying_commits(code, lines):
    assert code == 1 and "MERGE" not in lines, lines
    assert [ln for ln in lines if ln.startswith("HOLD: ") and "commit" in ln.lower()], lines


def test_commit_listing_failure_holds_with_a_line_about_commits(capsys):
    code, lines = gate(capsys, green(check("docker")), commits=[[CODE]], fail_listing=True)
    _held_saying_commits(code, lines)


def test_fewer_commits_listed_than_the_pr_has_holds_with_a_line_about_commits(capsys):
    code, lines = gate(capsys, green(check("docker")), commits=[[CODE], [CI_YML]], listed=1)
    _held_saying_commits(code, lines)


def test_full_listing_with_docker_success_merges(capsys):
    code, lines = gate(capsys, green(check("docker")), commits=[[CODE], ["scripts/a.py"]])
    assert (code, lines) == (0, ["MERGE"])


# criterion 3: ci_paths counts both sides of a rename

def _git(cwd, *args):
    cmd = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
           "-c", "commit.gpgsign=false", *args]
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _moved(tmp_path, monkeypatch, src, dst):
    _git(tmp_path, "init", "-q")
    (tmp_path / src).parent.mkdir(parents=True)
    (tmp_path / src).write_text("".join(f"line {i}\n" for i in range(40)), encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "one")
    first = _git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / dst).parent.mkdir(parents=True, exist_ok=True)
    _git(tmp_path, "mv", src, dst)
    _git(tmp_path, "commit", "-q", "-m", "two")
    monkeypatch.chdir(tmp_path)
    return first, _git(tmp_path, "rev-parse", "HEAD")


def _ci_paths():
    return _import(ROOT / "scripts" / "ci_paths.py", "ci_paths_for_611")


@pytest.mark.parametrize("src", ["labs/manager/app/thing.py", "labs/web/src/app.tsx"])
def test_moving_a_file_out_of_a_heavy_folder_is_heavy(tmp_path, monkeypatch, src):
    first, head = _moved(tmp_path, monkeypatch, src, "docs/moved.txt")
    assert _ci_paths().is_heavy(first, head) is True


def test_moving_a_file_between_light_folders_is_not_heavy(tmp_path, monkeypatch):
    first, head = _moved(tmp_path, monkeypatch, "docs/a.txt", "scripts/a.txt")
    assert _ci_paths().is_heavy(first, head) is False
