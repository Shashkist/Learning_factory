"""Exam for #645 (D160 amendment, D176): the docker rule only for repositories with a docker job.

NO NETWORK. Same fake gh and helpers as the #608/#611 exams (loaded from their files). The
gate is pointed at a fixture repos file through LF_REPOS_FILE and told the repository with
`--repo`. The form (manager's choice): a per-repository key `docker_check: true|false` in
`docs/factory/repos.yaml`. The rule is on for `true`, and on (fail closed) for a repository
that is not listed or has no key.
"""

import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def _import(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


exam611 = _import(HERE / "test_issue_611_gate_docker_rename.py", "exam611_for_645")
base = exam611.base
merge_gate = base.merge_gate
check, green, assert_held_naming = base.check, base.green, base.assert_held_naming
CODE, CI_YML = base.CODE, base.CI_YML

LF = "Shashkist/Learning_factory"
FACTORY = "Shashkist/factory"
OTHER = "Shashkist/other"


def write_repos(tmp_path, monkeypatch, entries):
    """entries: list of (repo, extra lines). Learning_factory is the default entry."""
    lines = []
    for repo, extra in entries:
        lines += [f"- repo: {repo}", f"  default: {str(repo == LF).lower()}",
                  f"  clone: C:\\projects\\{repo.split('/')[1]}", "  ship: main-ci", *extra]
    f = tmp_path / "repos.yaml"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setenv("LF_REPOS_FILE", str(f))


def standard(tmp_path, monkeypatch):
    write_repos(tmp_path, monkeypatch, [(LF, ["  docker_check: true"]),
                                        (FACTORY, ["  docker_check: false"])])


def gate(capsys, rollup, repo, commits=((CODE, CI_YML),)):
    argv = ["9", "--session", base.SESSION, "--exam-gate", base.EXAM]
    if repo:
        argv += ["--repo", repo]
    code = merge_gate.main(argv, run=exam611.PrGh(rollup, [list(c) for c in commits]))
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    return code, lines


def docker_holds(lines):
    return [ln for ln in lines if ln.startswith("HOLD: ") and "docker" in ln]


# criteria 1 and 2: off where the entry says there is no docker job

def test_factory_workflow_change_without_docker_check_is_not_held_for_docker(
        capsys, tmp_path, monkeypatch):
    standard(tmp_path, monkeypatch)
    code, lines = gate(capsys, green(), FACTORY)
    assert not docker_holds(lines), lines
    assert (code, lines) == (0, ["MERGE"])


@pytest.mark.parametrize("path", [CI_YML, ".github/workflows/deploy.yml"])
def test_factory_any_workflow_file_without_docker_check_merges(
        capsys, tmp_path, monkeypatch, path):
    standard(tmp_path, monkeypatch)
    assert gate(capsys, green(), FACTORY, commits=[(path,)]) == (0, ["MERGE"])


def test_factory_workflow_rule_does_not_apply_to_a_skipped_docker_named_check(
        capsys, tmp_path, monkeypatch):
    standard(tmp_path, monkeypatch)
    code, lines = gate(capsys, green(check("docker", "SKIPPED")), FACTORY)
    assert not [ln for ln in lines if "docker must succeed" in ln], lines


def test_factory_other_checks_still_hold(capsys, tmp_path, monkeypatch):
    standard(tmp_path, monkeypatch)
    code, lines = gate(capsys, green(check("tooling", "FAILURE")), FACTORY)
    assert_held_naming(code, lines, "tooling")
    assert not docker_holds(lines), lines


def test_factory_missing_exam_gate_still_holds(capsys, tmp_path, monkeypatch):
    standard(tmp_path, monkeypatch)
    code, lines = gate(capsys, green(exam=False), FACTORY)
    assert_held_naming(code, lines, base.EXAM)


def test_repo_name_match_ignores_case(capsys, tmp_path, monkeypatch):
    standard(tmp_path, monkeypatch)
    assert gate(capsys, green(), "shashkist/FACTORY") == (0, ["MERGE"])


# criterion 2: still on for Learning_factory

def test_learning_factory_workflow_change_without_docker_holds_naming_docker(
        capsys, tmp_path, monkeypatch):
    standard(tmp_path, monkeypatch)
    code, lines = gate(capsys, green(), LF)
    assert_held_naming(code, lines, "docker")


def test_learning_factory_workflow_change_with_skipped_docker_holds(
        capsys, tmp_path, monkeypatch):
    standard(tmp_path, monkeypatch)
    code, lines = gate(capsys, green(check("docker", "SKIPPED")), LF)
    assert_held_naming(code, lines, "docker")


def test_learning_factory_workflow_change_with_docker_success_merges(
        capsys, tmp_path, monkeypatch):
    standard(tmp_path, monkeypatch)
    assert gate(capsys, green(check("docker")), LF) == (0, ["MERGE"])


def test_learning_factory_without_repo_option_keeps_the_rule(capsys, tmp_path, monkeypatch):
    standard(tmp_path, monkeypatch)
    code, lines = gate(capsys, green(), None)
    assert_held_naming(code, lines, "docker")


# fail closed: no key, not listed, unreadable file, unknown value

def test_listed_without_docker_check_key_keeps_the_rule(capsys, tmp_path, monkeypatch):
    write_repos(tmp_path, monkeypatch, [(LF, ["  docker_check: true"]), (OTHER, [])])
    code, lines = gate(capsys, green(), OTHER)
    assert_held_naming(code, lines, "docker")


def test_not_listed_keeps_the_rule(capsys, tmp_path, monkeypatch):
    standard(tmp_path, monkeypatch)
    code, lines = gate(capsys, green(), OTHER)
    assert_held_naming(code, lines, "docker")


def test_explicit_true_keeps_the_rule(capsys, tmp_path, monkeypatch):
    write_repos(tmp_path, monkeypatch, [(LF, ["  docker_check: true"]),
                                        (OTHER, ["  docker_check: true"])])
    code, lines = gate(capsys, green(), OTHER)
    assert_held_naming(code, lines, "docker")


def test_missing_repos_file_keeps_the_rule(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("LF_REPOS_FILE", str(tmp_path / "nope.yaml"))
    code, lines = gate(capsys, green(), FACTORY)
    assert_held_naming(code, lines, "docker")


def test_unrecognised_value_keeps_the_rule(capsys, tmp_path, monkeypatch):
    write_repos(tmp_path, monkeypatch, [(LF, ["  docker_check: true"]),
                                        (OTHER, ["  docker_check: maybe"])])
    code, lines = gate(capsys, green(), OTHER)
    assert_held_naming(code, lines, "docker")


# no workflow change: nothing changes for either repository

@pytest.mark.parametrize("repo", [LF, FACTORY])
def test_no_workflow_change_merges_without_docker_in_both(capsys, tmp_path, monkeypatch, repo):
    standard(tmp_path, monkeypatch)
    assert gate(capsys, green(), repo, commits=[(CODE,)]) == (0, ["MERGE"])


# the shipped repos.yaml

def _entries():
    entries, cur = {}, None
    text = (ROOT / "docs" / "factory" / "repos.yaml").read_text(encoding="utf-8-sig")
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("- "):
            cur = {}
            line = "  " + line[2:]
        key, _, value = line.strip().partition(":")
        cur[key] = value.strip().strip("'\"")
        if key == "repo":
            entries[cur["repo"]] = cur
    return entries


def test_shipped_repos_yaml_says_factory_has_no_docker_job():
    assert _entries()[FACTORY].get("docker_check", "").lower() == "false"


def test_shipped_repos_yaml_says_learning_factory_has_the_docker_job():
    assert _entries()[LF].get("docker_check", "").lower() == "true"


def test_shipped_repos_yaml_is_still_read_by_issue_lock():
    repos, default, _clones = base.issue_lock.read_repos(ROOT / "docs" / "factory" / "repos.yaml")
    assert {LF, FACTORY} <= set(repos) and default == LF


def test_shipped_file_drives_the_gate_end_to_end(capsys, monkeypatch):
    monkeypatch.setenv("LF_REPOS_FILE", str(ROOT / "docs" / "factory" / "repos.yaml"))
    assert gate(capsys, green(), FACTORY) == (0, ["MERGE"])
    code, lines = gate(capsys, green(), LF)
    assert_held_naming(code, lines, "docker")
