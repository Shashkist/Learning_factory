"""Exam for #674 (D190): the merge gate judges the latest run of each check, not stale runs.

NO NETWORK. Same fake gh and helpers as the #608/#611 exams (loaded from their files). The
rollup entries carry the fields `gh pr view --json statusCheckRollup` gives: CheckRun has
`name`, `workflowName`, `status`, `conclusion`, `startedAt`, `completedAt`; StatusContext has
`context`, `state`, `startedAt`. Runs are grouped by (workflow, name); the latest is the one
with the newest `startedAt` (`completedAt` when there is no `startedAt`). Equal or missing
timestamps cannot prove which run is later, so they fail closed.
"""

import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _import(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


exam611 = _import(HERE / "test_issue_611_gate_docker_rename.py", "exam611_for_674")
base = exam611.base
merge_gate = base.merge_gate
check, green, assert_held_naming = base.check, base.green, base.assert_held_naming
CODE, CI_YML = base.CODE, base.CI_YML

T1, T2, T3 = "2026-11-27T10:00:00Z", "2026-11-27T10:10:00Z", "2026-11-27T10:20:00Z"
ZERO = "0001-01-01T00:00:00Z"  # completedAt gh prints for a run that is still going


def run(name, conclusion="SUCCESS", start=None, end=None, workflow=None, status="COMPLETED"):
    c = check(name, conclusion, status)
    if workflow:
        c["workflowName"] = workflow
    if start:
        c["startedAt"] = start
    if end:
        c["completedAt"] = end
    return c


def status_context(name, state, start=None):
    c = {"__typename": "StatusContext", "context": name, "state": state}
    if start:
        c["startedAt"] = start
    return c


def gate(capsys, rollup, commits=((CODE,),)):
    argv = ["9", "--session", base.SESSION, "--exam-gate", base.EXAM]
    code = merge_gate.main(argv, run=exam611.PrGh(rollup, [list(c) for c in commits]))
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    return code, lines


def without(name):
    return [c for c in green() if c["name"] != name]


# criterion 1: a stale run of a check whose latest run passed does not hold

@pytest.mark.parametrize("stale", ["CANCELLED", "FAILURE", "TIMED_OUT"])
def test_old_stale_run_plus_new_success_merges(capsys, stale):
    rollup = green(run("image-term", stale, T1, T1), run("image-term", "SUCCESS", T2, T2))
    assert gate(capsys, rollup) == (0, ["MERGE"])


@pytest.mark.parametrize("reverse", [False, True])
def test_the_order_of_the_rollup_does_not_matter(capsys, reverse):
    pair = [run("dashboard", "FAILURE", T1, T1), run("dashboard", "SUCCESS", T2, T2)]
    rollup = green(*(pair[::-1] if reverse else pair))
    assert gate(capsys, rollup) == (0, ["MERGE"])


def test_three_runs_judge_only_the_newest(capsys):
    rollup = green(run("web2", "SUCCESS", T1, T1), run("web2", "FAILURE", T2, T2),
                   run("web2", "SUCCESS", T3, T3))
    assert gate(capsys, rollup) == (0, ["MERGE"])


def test_latest_is_found_by_completed_at_when_there_is_no_start(capsys):
    rollup = green(run("dashboard", "CANCELLED", None, T1), run("dashboard", "SUCCESS", None, T2))
    assert gate(capsys, rollup) == (0, ["MERGE"])


@pytest.mark.parametrize("reverse", [False, True])
def test_old_success_plus_new_failure_holds_naming_the_check(capsys, reverse):
    pair = [run("dashboard", "SUCCESS", T1, T1), run("dashboard", "FAILURE", T2, T2)]
    code, lines = gate(capsys, green(*(pair[::-1] if reverse else pair)))
    assert_held_naming(code, lines, "dashboard")


def test_old_success_plus_new_cancelled_holds(capsys):
    rollup = green(run("image-term", "SUCCESS", T1, T1), run("image-term", "CANCELLED", T2, T2))
    assert_held_naming(*gate(capsys, rollup), "image-term")


def test_old_success_plus_new_pending_holds(capsys):
    rollup = green(run("dashboard", "SUCCESS", T1, T1),
                   run("dashboard", "", T2, ZERO, status="IN_PROGRESS"))
    assert_held_naming(*gate(capsys, rollup), "dashboard")


def test_old_failure_plus_new_pending_holds(capsys):
    rollup = green(run("dashboard", "FAILURE", T1, T1),
                   run("dashboard", "", T2, ZERO, status="QUEUED"))
    assert_held_naming(*gate(capsys, rollup), "dashboard")


# criterion 1 and 3: no timestamps, equal timestamps and half-stamped runs fail closed

@pytest.mark.parametrize("reverse", [False, True])
def test_two_runs_without_timestamps_hold(capsys, reverse):
    pair = [run("dashboard", "CANCELLED"), run("dashboard", "SUCCESS")]
    code, lines = gate(capsys, green(*(pair[::-1] if reverse else pair)))
    assert_held_naming(code, lines, "dashboard")


def test_two_runs_without_timestamps_failure_and_success_hold(capsys):
    rollup = green(run("dashboard", "FAILURE"), run("dashboard", "SUCCESS"))
    assert_held_naming(*gate(capsys, rollup), "dashboard")


@pytest.mark.parametrize("reverse", [False, True])
def test_equal_timestamps_hold(capsys, reverse):
    pair = [run("dashboard", "CANCELLED", T1, T2), run("dashboard", "SUCCESS", T1, T2)]
    code, lines = gate(capsys, green(*(pair[::-1] if reverse else pair)))
    assert_held_naming(code, lines, "dashboard")


def test_a_run_without_timestamps_next_to_a_stamped_success_holds(capsys):
    rollup = green(run("dashboard", "CANCELLED"), run("dashboard", "SUCCESS", T2, T2))
    assert_held_naming(*gate(capsys, rollup), "dashboard")


# criterion 1: per check name and workflow

def test_same_name_in_two_workflows_is_judged_separately_latest_failure_holds(capsys):
    rollup = green(run("build", "SUCCESS", T1, T1, "ci"),
                   run("build", "FAILURE", T2, T2, "other"))
    assert_held_naming(*gate(capsys, rollup), "build")


def test_same_name_in_two_workflows_each_with_its_own_latest_merges(capsys):
    rollup = green(run("build", "CANCELLED", T1, T1, "ci"), run("build", "SUCCESS", T2, T2, "ci"),
                   run("build", "FAILURE", T1, T1, "other"),
                   run("build", "SUCCESS", T3, T3, "other"))
    assert gate(capsys, rollup) == (0, ["MERGE"])


def test_a_newer_success_in_another_workflow_does_not_hide_a_failing_one(capsys):
    rollup = green(run("build", "FAILURE", T1, T1, "ci"), run("build", "SUCCESS", T3, T3, "other"))
    assert_held_naming(*gate(capsys, rollup), "build")


def test_different_names_are_not_merged_into_one(capsys):
    rollup = green(run("image-term", "FAILURE", T1, T1), run("image-code", "SUCCESS", T3, T3))
    assert_held_naming(*gate(capsys, rollup), "image-term")


# criterion 1: StatusContext entries

def test_status_context_old_failure_plus_new_success_merges(capsys):
    rollup = green(status_context("ci/legacy", "FAILURE", T1),
                   status_context("ci/legacy", "SUCCESS", T2))
    assert gate(capsys, rollup) == (0, ["MERGE"])


def test_status_context_old_success_plus_new_failure_holds(capsys):
    rollup = green(status_context("ci/legacy", "SUCCESS", T1),
                   status_context("ci/legacy", "FAILURE", T2))
    assert_held_naming(*gate(capsys, rollup), "ci/legacy")


def test_status_context_without_timestamps_holds(capsys):
    rollup = green(status_context("ci/legacy", "FAILURE"), status_context("ci/legacy", "SUCCESS"))
    assert_held_naming(*gate(capsys, rollup), "ci/legacy")


# criterion 2: the latest run still holds as today

@pytest.mark.parametrize("state", ["CANCELLED", "FAILURE", "TIMED_OUT", "ACTION_REQUIRED"])
def test_a_single_run_not_passing_still_holds(capsys, state):
    assert_held_naming(*gate(capsys, green(run("dashboard", state, T1, T1))), "dashboard")


def test_a_single_pending_run_still_holds(capsys):
    rollup = green(run("dashboard", "", T1, ZERO, status="IN_PROGRESS"))
    assert_held_naming(*gate(capsys, rollup), "dashboard")


def test_no_checks_at_all_still_holds(capsys):
    code, lines = gate(capsys, [])
    assert code == 1 and "MERGE" not in lines


# criterion 2: the exam gate, exam-integrity and docker rules are unchanged

def test_exam_gate_old_failure_new_success_merges(capsys):
    rollup = without(base.EXAM) + [run(base.EXAM, "FAILURE", T1, T1),
                                   run(base.EXAM, "SUCCESS", T2, T2)]
    assert gate(capsys, rollup) == (0, ["MERGE"])


def test_exam_gate_old_success_new_failure_holds(capsys):
    rollup = without(base.EXAM) + [run(base.EXAM, "SUCCESS", T1, T1),
                                   run(base.EXAM, "FAILURE", T2, T2)]
    assert_held_naming(*gate(capsys, rollup), base.EXAM)


def test_exam_gate_old_success_new_skipped_holds(capsys):
    rollup = without(base.EXAM) + [run(base.EXAM, "SUCCESS", T1, T1),
                                   run(base.EXAM, "SKIPPED", T2, T2)]
    assert_held_naming(*gate(capsys, rollup), base.EXAM)


def test_exam_gate_still_missing_holds(capsys):
    rollup = green(run("dashboard", "SUCCESS", T1, T1), exam=False)
    assert_held_naming(*gate(capsys, rollup), base.EXAM)


def test_exam_integrity_old_failure_new_success_merges(capsys):
    rollup = without("exam-integrity") + [run("exam-integrity", "FAILURE", T1, T1),
                                          run("exam-integrity", "SUCCESS", T2, T2)]
    assert gate(capsys, rollup) == (0, ["MERGE"])


def test_exam_integrity_old_success_new_failure_holds(capsys):
    rollup = without("exam-integrity") + [run("exam-integrity", "SUCCESS", T1, T1),
                                          run("exam-integrity", "FAILURE", T2, T2)]
    assert_held_naming(*gate(capsys, rollup), "exam-integrity")


def test_exam_integrity_still_missing_holds(capsys):
    assert_held_naming(*gate(capsys, without("exam-integrity")), "exam-integrity")


def test_docker_old_cancelled_new_success_with_workflow_change_merges(capsys):
    rollup = green(run("docker", "CANCELLED", T1, T1), run("docker", "SUCCESS", T2, T2))
    assert gate(capsys, rollup, commits=[(CODE, CI_YML)]) == (0, ["MERGE"])


def test_docker_old_success_new_cancelled_with_workflow_change_holds(capsys):
    rollup = green(run("docker", "SUCCESS", T1, T1), run("docker", "CANCELLED", T2, T2))
    assert_held_naming(*gate(capsys, rollup, commits=[(CODE, CI_YML)]), "docker")


def test_docker_old_success_new_skipped_with_workflow_change_holds(capsys):
    rollup = green(run("docker", "SUCCESS", T1, T1), run("docker", "SKIPPED", T2, T2))
    assert_held_naming(*gate(capsys, rollup, commits=[(CODE, CI_YML)]), "docker")


def test_docker_old_skipped_new_success_with_workflow_change_merges(capsys):
    rollup = green(run("docker", "SKIPPED", T1, T1), run("docker", "SUCCESS", T2, T2))
    assert gate(capsys, rollup, commits=[(CODE, CI_YML)]) == (0, ["MERGE"])


def test_docker_still_missing_with_workflow_change_holds(capsys):
    assert_held_naming(*gate(capsys, green(), commits=[(CODE, CI_YML)]), "docker")


def test_docker_skipped_needs_the_latest_changes_run_to_succeed(capsys):
    rollup = without("changes") + [run("changes", "SUCCESS", T1, T1),
                                   run("changes", "FAILURE", T2, T2),
                                   run("docker", "SKIPPED", T2, T2)]
    code, lines = gate(capsys, rollup)
    assert code == 1 and "MERGE" not in lines, lines


def test_docker_skipped_with_a_stale_failed_changes_run_merges(capsys):
    rollup = without("changes") + [run("changes", "FAILURE", T1, T1),
                                   run("changes", "SUCCESS", T2, T2),
                                   run("docker", "SKIPPED", T2, T2)]
    assert gate(capsys, rollup) == (0, ["MERGE"])
