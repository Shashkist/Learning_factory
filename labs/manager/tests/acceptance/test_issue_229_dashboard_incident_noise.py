# ruff: noqa: E501
"""Acceptance tests for issue #229 (D71): a single failed *scheduled* Factory dashboard run is
not a p0. No network, Docker or secrets: the decision script is run with subprocess and the
workflow is parsed.

Contract the exam fixes
-----------------------
Script `scripts/incident_gate.py` (stdlib only), run as

    python scripts/incident_gate.py --workflow NAME --event EVENT --previous CONCLUSION

  * NAME: the failed workflow's `name:` (for example `Factory dashboard`, `CI`, `Deploy`).
  * EVENT: the event that started the failed run (`schedule`, `push`, `workflow_dispatch`, ...).
  * CONCLUSION: the conclusion of the previous *scheduled* run of the same workflow, or the
    empty string / `none` when there is no previous scheduled run. Ignored for every case that
    is not (`Factory dashboard`, `schedule`).
  * Always exits 0 for valid arguments and prints exactly one word on stdout: `raise` (open or
    comment on the incident) or `skip` (do not).
  * Decision: `Factory dashboard` + `schedule` -> `raise` only if the previous conclusion is
    one of `failure`, `timed_out`, `startup_failure`; anything else (`success`, `cancelled`,
    `skipped`, empty, `none`) -> `skip`. Every other combination -> `raise`.
  * Missing --workflow or --event exits non-zero (argparse style); --previous is optional
    (defaults to none).

Workflow `.github/workflows/incident.yml`:
  * still triggers on the same six workflows (completed) and keeps the three labels;
  * the incident job checks the repo out (actions/checkout) and runs `incident_gate.py`;
  * it looks the previous run up with `gh run list --workflow ... --event schedule`, excluding
    the current run id (`github.event.workflow_run.id`), and has `actions: read` permission;
  * the step that creates or comments on the issue only runs when the gate said `raise`
    (the string `raise` appears in an `if:` / shell test that guards it).
"""
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
INCIDENT = REPO_ROOT / ".github" / "workflows" / "incident.yml"
GATE = REPO_ROOT / "scripts" / "incident_gate.py"

DASH = "Factory dashboard"
FAILING = ("failure", "timed_out", "startup_failure")
SIX = ["CI", "Dashboard CI", "Factory dashboard", "Deploy", "Server smoke", "Server bootstrap"]


def _gate(workflow: str, event: str, previous: str | None = None) -> subprocess.CompletedProcess:
    assert GATE.is_file(), "scripts/incident_gate.py is missing"
    cmd = [sys.executable, "-I", str(GATE), "--workflow", workflow, "--event", event]
    if previous is not None:
        cmd += ["--previous", previous]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=REPO_ROOT)


def _decision(workflow: str, event: str, previous: str | None = None) -> str:
    r = _gate(workflow, event, previous)
    assert r.returncode == 0, f"gate exited {r.returncode}: {r.stderr}"
    return r.stdout.strip()


def _wf() -> dict:
    yaml = pytest.importorskip("yaml")
    assert INCIDENT.is_file()
    data = yaml.safe_load(INCIDENT.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _steps_text() -> str:
    job = next(iter(_wf()["jobs"].values()))
    return "\n".join(f"{s.get('if', '')}\n{s.get('run', '')}\n{s.get('uses', '')}" for s in job["steps"])


# ------------------------------------------------------------ the decision script


@pytest.mark.parametrize("previous", FAILING)
def test_a_failed_scheduled_dashboard_run_after_a_failed_scheduled_run_raises_an_incident(previous):
    assert _decision(DASH, "schedule", previous) == "raise"


@pytest.mark.parametrize("previous", ["success", "cancelled", "skipped"])
def test_a_failed_scheduled_dashboard_run_after_a_good_scheduled_run_is_not_an_incident(previous):
    assert _decision(DASH, "schedule", previous) == "skip"


@pytest.mark.parametrize("previous", ["", "none", None])
def test_a_failed_scheduled_dashboard_run_with_no_previous_scheduled_run_is_not_an_incident(previous):
    assert _decision(DASH, "schedule", previous) == "skip"


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
@pytest.mark.parametrize("previous", ["success", "none", "failure"])
def test_a_failed_non_scheduled_dashboard_run_keeps_raising_an_incident(event, previous):
    assert _decision(DASH, event, previous) == "raise"


@pytest.mark.parametrize("workflow", ["CI", "Dashboard CI", "Deploy", "Server smoke", "Server bootstrap"])
@pytest.mark.parametrize("event", ["push", "schedule", "workflow_dispatch"])
@pytest.mark.parametrize("previous", ["success", "none"])
def test_other_workflows_raise_an_incident_regardless_of_the_previous_scheduled_run(workflow, event, previous):
    assert _decision(workflow, event, previous) == "raise"


def test_the_gate_requires_workflow_and_event():
    assert GATE.is_file(), "scripts/incident_gate.py is missing"
    assert _gate(DASH, "schedule", "failure").returncode == 0
    r = subprocess.run([sys.executable, "-I", str(GATE), "--event", "push"], capture_output=True, text=True, timeout=30)
    assert r.returncode != 0
    r = subprocess.run([sys.executable, "-I", str(GATE), "--workflow", "CI"], capture_output=True, text=True, timeout=30)
    assert r.returncode != 0


# ------------------------------------------------------------ incident.yml


def test_incident_workflow_still_watches_the_same_six_workflows():
    on = _wf().get("on", _wf().get(True))
    run = on["workflow_run"]
    assert sorted(run["workflows"]) == sorted(SIX)
    assert run["types"] == ["completed"]


def test_incident_workflow_keeps_the_labels_and_title():
    text = INCIDENT.read_text(encoding="utf-8")
    for needle in ("type:incident", "priority:p0", "status:ready", "Incident: $WF_NAME failed", "Failed again:"):
        assert needle in text


def test_incident_workflow_runs_the_gate_script_after_checking_out_the_repo():
    text = _steps_text()
    assert "actions/checkout" in text, "the script must exist on the runner"
    assert "scripts/incident_gate.py" in text


def test_incident_workflow_looks_up_the_previous_scheduled_run_excluding_the_current_one():
    text = _steps_text()
    assert "gh run list" in text
    assert "--event schedule" in text
    assert "Factory dashboard" in text or "$WF_NAME" in text
    assert "github.event.workflow_run.id" in INCIDENT.read_text(encoding="utf-8") or "RUN_ID" in text
    assert "select(.databaseId" in text or "databaseId" in text, "the current run id must be filtered out of the list"
    assert "--json" in text


def test_incident_workflow_can_read_runs():
    perms = _wf().get("permissions") or next(iter(_wf()["jobs"].values())).get("permissions") or {}
    assert perms.get("actions") == "read"
    assert perms.get("issues") == "write"


def test_the_issue_is_only_opened_or_commented_when_the_gate_says_raise():
    job = next(iter(_wf()["jobs"].values()))
    steps = job["steps"]
    idx = next(i for i, s in enumerate(steps) if "gh issue create" in s.get("run", ""))
    gated_by_if = "raise" in str(steps[idx].get("if", ""))
    gated_in_shell = "raise" in steps[idx].get("run", "")
    assert gated_by_if or gated_in_shell, "the issue step must test the gate's `raise` decision"
    assert any("incident_gate.py" in s.get("run", "") for s in steps[: idx + 1])


def test_the_job_if_still_filters_on_failed_conclusions_and_ci_on_main_push():
    cond = str(next(iter(_wf()["jobs"].values()))["if"])
    for needle in ("failure", "timed_out", "startup_failure", "head_branch == 'main'", "event == 'push'"):
        assert needle in cond
