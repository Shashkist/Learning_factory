# ruff: noqa: E501
"""Acceptance tests for issue #658 (D189): a cancelled Deploy run is noticed.

Incident.yml gets a job `deploy-cancelled`. Its logic is the script
`labs/deploy/watch/deploy-cancelled.sh` (bash, `set -euo pipefail`, `gh` and `jq`). The exam
runs the script with bash against a fake `gh` on PATH (and the real `jq`); no network, no secrets.

Environment the job passes to the script (the exam sets the same)
-----------------------------------------------------------------
  RUN_ID    id of the cancelled Deploy run (`github.event.workflow_run.id`)
  GH_REPO   `owner/name`
  GH_TOKEN  `secrets.GITHUB_TOKEN` (the fake ignores it)

The `gh` calls the script may make (anything else makes the fake fail the call)
------------------------------------------------------------------------------
Reads are `gh api <endpoint>` with NO `--jq`, `-q`, `--template`, `--paginate` or `--slurp`: the
fake prints the raw JSON of the fixture and the script filters it with `jq`. The fake ignores any
query string, so the script must filter by sha, event and branch itself, from the JSON fields.

  1. gh api repos/<GH_REPO>/actions/runs/<RUN_ID>
       -> {"id", "event", "head_sha", "conclusion", ...}        (the cancelled run)
  2. gh api repos/<GH_REPO>/actions/runs/<RUN_ID>/jobs
       -> {"jobs": [{"name", "started_at"}, ...]}                (started_at may be null)
  3. gh api repos/<GH_REPO>/commits/main
       -> {"sha": "<main head>"}
  4. gh api "repos/<GH_REPO>/actions/workflows/ci.yml/runs?branch=main&event=push&head_sha=<sha>..."
       -> {"workflow_runs": [{"head_sha", "head_branch", "event", "status", "conclusion",
            "created_at"}, ...]}  newest first, like GitHub; decoys for other shas/branches/events
  5. gh api "repos/<GH_REPO>/actions/workflows/deploy.yml/runs?head_sha=<sha>..."
       -> {"workflow_runs": [{"id", "head_sha", "event", "status", "conclusion"}, ...]}
  6. gh workflow run deploy.yml --ref main -f sha=<main head>    (the only write; at most once)

Behaviour the exam fixes (D189 (2), (3))
-----------------------------------------
  * Guards, in this reading: (b) no job named `deploy` of the cancelled run has a `started_at`;
    (c) the latest push CI run on main for main's head has conclusion `success`; (d) no Deploy run
    for main's head is queued, in progress, waiting, pending or completed `success` already;
    (a) the cancelled run's `event` is `workflow_run`.
  * (b) false -> stdout is exactly one line
        deploy job had started: deliberate cancel or timeout, nothing to do      exit 0
    (d) false (a Deploy covers main) -> exactly one line
        a Deploy for <sha> already covers main                                   exit 0
    (c) false -> exactly one line
        CI for main's head is <conclusion>: nothing to deploy                    exit 0
    No `gh workflow run` in any of these.
  * (a) false (the cancelled run was a `workflow_dispatch`) with (b) and (c) true and nothing
    covering main's head: no dispatch, exit 1 (the job's `if: failure()` step opens an incident).
  * All hold and the event is `workflow_run`: one `gh workflow run deploy.yml --ref main -f
    sha=<main head>` and stdout exactly `re-dispatched Deploy for <sha>`, exit 0. The sha is main's
    head (read from commits/main), not the cancelled run's head_sha.
  * A failed read (gh exits 1) or an empty read (gh exits 0 with no output) of any of the five
    endpoints is an error: non-zero exit, never a dispatch. A well-formed empty list
    (`{"jobs": []}`, `{"workflow_runs": []}`) is a real answer and is read as zero.
  * Check order when a guard fails is not fixed, except that an exit 1 and a dispatch happen only
    when (b), (c) and (d) all say "go on", and the event decides which of the two.
"""
import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
INCIDENT = REPO_ROOT / ".github" / "workflows" / "incident.yml"
SCRIPT = REPO_ROOT / "labs" / "deploy" / "watch" / "deploy-cancelled.sh"
SCRIPT_REL = "labs/deploy/watch/deploy-cancelled.sh"

REPO = "Shashkist/Learning_factory"
RUN_ID = "4242"
HEAD = "a" * 40  # main's head
OLD = "b" * 40   # an older sha (the cancelled run's own head_sha)
OTHER = "c" * 40

SIX = ["CI", "Dashboard CI", "Factory dashboard", "Deploy", "Server smoke", "Server bootstrap"]

needs_tools = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None, reason="needs bash and jq"
)

# ------------------------------------------------------------------ the fake gh

FAKE_GH = r"""#!/usr/bin/env bash
# fake gh: logs every call, answers from $FAKE_GH_DIR/<key>.json
echo "$*" >> "$FAKE_GH_LOG"
case "${1:-}" in
  api)
    shift
    ep=""
    for a in "$@"; do
      case "$a" in
        --jq|-q|--template|-t|--paginate|--slurp|--jq=*|--template=*)
          echo "fake gh: $a is not allowed, read raw JSON" >&2; exit 98 ;;
        -*) ;;
        *) [ -z "$ep" ] && ep="$a" ;;
      esac
    done
    ep="${ep%%\?*}"; ep="${ep#/}"
    case "$ep" in
      "repos/$GH_REPO/actions/runs/$RUN_ID") key=run ;;
      "repos/$GH_REPO/actions/runs/$RUN_ID/jobs") key=jobs ;;
      "repos/$GH_REPO/commits/main") key=commit ;;
      "repos/$GH_REPO/actions/workflows/ci.yml/runs") key=ci ;;
      "repos/$GH_REPO/actions/workflows/deploy.yml/runs") key=deploy ;;
      *) echo "fake gh: unexpected endpoint $ep" >&2; exit 99 ;;
    esac
    case ",${FAKE_GH_FAIL:-}," in *",$key,"*) echo "gh: HTTP 500" >&2; exit 1 ;; esac
    case ",${FAKE_GH_EMPTY:-}," in *",$key,"*) exit 0 ;; esac
    cat "$FAKE_GH_DIR/$key.json"
    ;;
  workflow)
    [ "${2:-}" = run ] || { echo "fake gh: unexpected: $*" >&2; exit 99; }
    exit "${FAKE_GH_DISPATCH_EXIT:-0}"
    ;;
  *) echo "fake gh: unexpected: $*" >&2; exit 99 ;;
esac
"""


def _scenario(**over) -> dict:
    """Default: every guard holds and the cancelled run was a workflow_run Deploy."""
    s = {
        "run": {"id": int(RUN_ID), "event": "workflow_run", "head_sha": OLD, "conclusion": "cancelled"},
        "jobs": {"jobs": [{"name": "deploy", "started_at": None, "conclusion": "cancelled"}]},
        "commit": {"sha": HEAD},
        "ci": {"workflow_runs": [
            # decoys: another sha, another branch, another event; the real one is the first for HEAD
            {"head_sha": OTHER, "head_branch": "main", "event": "push", "status": "completed", "conclusion": "failure", "created_at": "2026-11-27T10:05:00Z"},
            {"head_sha": HEAD, "head_branch": "f/x", "event": "pull_request", "status": "completed", "conclusion": "failure", "created_at": "2026-11-27T10:04:00Z"},
            {"head_sha": HEAD, "head_branch": "main", "event": "push", "status": "completed", "conclusion": "success", "created_at": "2026-11-27T10:03:00Z"},
            {"head_sha": HEAD, "head_branch": "main", "event": "push", "status": "completed", "conclusion": "failure", "created_at": "2026-11-27T10:01:00Z"},
        ]},
        "deploy": {"workflow_runs": [
            {"id": int(RUN_ID), "head_sha": HEAD, "event": "workflow_run", "status": "completed", "conclusion": "cancelled"},
            {"id": 4100, "head_sha": OTHER, "event": "workflow_run", "status": "completed", "conclusion": "success"},
            {"id": 4101, "head_sha": OTHER, "event": "workflow_run", "status": "in_progress", "conclusion": None},
            {"id": 4102, "head_sha": HEAD, "event": "workflow_run", "status": "completed", "conclusion": "failure"},
        ]},
    }
    s.update(over)
    return s


def _run(tmp_path, scenario=None, fail="", empty="", dispatch_exit="0"):
    """Run the script against the fake gh; returns (CompletedProcess, [gh call lines])."""
    assert SCRIPT.is_file(), f"{SCRIPT_REL} is missing"
    scenario = scenario or _scenario()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(FAKE_GH, encoding="utf-8", newline="\n")
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    data = tmp_path / "data"
    data.mkdir()
    for key, body in scenario.items():
        (data / f"{key}.json").write_text(json.dumps(body), encoding="utf-8")
    log = tmp_path / "calls.log"
    log.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "RUN_ID": RUN_ID,
        "GH_REPO": REPO,
        "GH_TOKEN": "fake-token-not-a-secret",
        "FAKE_GH_DIR": str(data),
        "FAKE_GH_LOG": str(log),
        "FAKE_GH_FAIL": fail,
        "FAKE_GH_EMPTY": empty,
        "FAKE_GH_DISPATCH_EXIT": dispatch_exit,
    }
    r = subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, timeout=60, env=env, cwd=tmp_path
    )
    calls = [c for c in log.read_text(encoding="utf-8").splitlines() if c.strip()]
    return r, calls


def _dispatches(calls):
    return [c for c in calls if c.startswith("workflow run")]


def _lines(r):
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def _assert_refused(r, calls, line):
    assert r.returncode == 0, f"exit {r.returncode}: {r.stderr}"
    assert _dispatches(calls) == [], f"re-dispatched: {calls}"
    assert _lines(r) == [line], f"stdout was {r.stdout!r}"


def _assert_error(r, calls):
    assert r.returncode != 0, f"must fail, stdout={r.stdout!r} stderr={r.stderr!r}"
    assert _dispatches(calls) == [], f"re-dispatched on a failed read: {calls}"


# ------------------------------------------------------------------ the script: the happy path

@needs_tools
def test_all_guards_hold_redispatches_deploy_for_mains_head_exactly_once(tmp_path):
    r, calls = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert _dispatches(calls) == [f"workflow run deploy.yml --ref main -f sha={HEAD}"]
    assert _lines(r) == [f"re-dispatched Deploy for {HEAD}"]


@needs_tools
def test_a_deploy_run_with_no_jobs_listed_yet_counts_as_never_started(tmp_path):
    r, calls = _run(tmp_path, _scenario(jobs={"jobs": []}))
    assert r.returncode == 0, r.stderr
    assert _dispatches(calls) == [f"workflow run deploy.yml --ref main -f sha={HEAD}"]


@needs_tools
def test_a_started_job_with_another_name_does_not_stop_the_redispatch(tmp_path):
    jobs = {"jobs": [{"name": "deploy", "started_at": None}, {"name": "notify", "started_at": "2026-11-27T10:00:00Z"}]}
    r, calls = _run(tmp_path, _scenario(jobs=jobs))
    assert r.returncode == 0, r.stderr
    assert len(_dispatches(calls)) == 1


@needs_tools
def test_the_latest_ci_run_decides_not_an_older_success(tmp_path):
    ci = {"workflow_runs": [
        {"head_sha": HEAD, "head_branch": "main", "event": "push", "status": "completed", "conclusion": "failure", "created_at": "2026-11-27T10:09:00Z"},
        {"head_sha": HEAD, "head_branch": "main", "event": "push", "status": "completed", "conclusion": "success", "created_at": "2026-11-27T10:01:00Z"},
    ]}
    r, calls = _run(tmp_path, _scenario(ci=ci))
    _assert_refused(r, calls, "CI for main's head is failure: nothing to deploy")


# ------------------------------------------------------------------ guard (a): the event

@needs_tools
def test_guard_a_a_cancelled_workflow_dispatch_deploy_is_never_redispatched_and_exits_1(tmp_path):
    run = {"id": int(RUN_ID), "event": "workflow_dispatch", "head_sha": OLD, "conclusion": "cancelled"}
    r, calls = _run(tmp_path, _scenario(run=run))
    assert r.returncode == 1, f"exit {r.returncode}: {r.stdout!r} {r.stderr!r}"
    assert _dispatches(calls) == []


@needs_tools
def test_guard_a_the_loop_stops_after_one_redispatch(tmp_path):
    # the re-dispatch is a workflow_dispatch run: cancelled again, it must not dispatch again
    run = {"id": int(RUN_ID), "event": "workflow_dispatch", "head_sha": HEAD, "conclusion": "cancelled"}
    r, calls = _run(tmp_path, _scenario(run=run))
    assert _dispatches(calls) == []
    assert r.returncode != 0


# ------------------------------------------------------------------ guard (b): the deploy job never started

@needs_tools
def test_guard_b_a_started_deploy_job_means_deliberate_cancel_or_timeout_so_no_redispatch(tmp_path):
    jobs = {"jobs": [{"name": "deploy", "started_at": "2026-11-27T10:00:00Z", "conclusion": "cancelled"}]}
    r, calls = _run(tmp_path, _scenario(jobs=jobs))
    _assert_refused(r, calls, "deploy job had started: deliberate cancel or timeout, nothing to do")


@needs_tools
def test_guard_b_applies_to_a_workflow_dispatch_run_too_and_is_not_an_incident(tmp_path):
    run = {"id": int(RUN_ID), "event": "workflow_dispatch", "head_sha": OLD, "conclusion": "cancelled"}
    jobs = {"jobs": [{"name": "deploy", "started_at": "2026-11-27T10:00:00Z"}]}
    r, calls = _run(tmp_path, _scenario(run=run, jobs=jobs))
    _assert_refused(r, calls, "deploy job had started: deliberate cancel or timeout, nothing to do")


# ------------------------------------------------------------------ guard (c): CI for main's head is green

@needs_tools
@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out"])
def test_guard_c_ci_for_main_head_not_success_means_nothing_to_deploy(tmp_path, conclusion):
    ci = {"workflow_runs": [
        {"head_sha": HEAD, "head_branch": "main", "event": "push", "status": "completed", "conclusion": conclusion, "created_at": "2026-11-27T10:03:00Z"},
    ]}
    r, calls = _run(tmp_path, _scenario(ci=ci))
    _assert_refused(r, calls, f"CI for main's head is {conclusion}: nothing to deploy")


@needs_tools
def test_guard_c_a_green_ci_for_another_sha_or_a_pull_request_is_not_proof(tmp_path):
    ci = {"workflow_runs": [
        {"head_sha": OTHER, "head_branch": "main", "event": "push", "status": "completed", "conclusion": "success", "created_at": "2026-11-27T10:05:00Z"},
        {"head_sha": HEAD, "head_branch": "f/x", "event": "pull_request", "status": "completed", "conclusion": "success", "created_at": "2026-11-27T10:04:00Z"},
        {"head_sha": HEAD, "head_branch": "main", "event": "push", "status": "completed", "conclusion": "failure", "created_at": "2026-11-27T10:03:00Z"},
    ]}
    r, calls = _run(tmp_path, _scenario(ci=ci))
    _assert_refused(r, calls, "CI for main's head is failure: nothing to deploy")


@needs_tools
def test_guard_c_no_ci_run_at_all_for_main_head_never_redispatches(tmp_path):
    r, calls = _run(tmp_path, _scenario(ci={"workflow_runs": []}))
    assert _dispatches(calls) == []


# ------------------------------------------------------------------ guard (d): a newer Deploy covers main's head

@needs_tools
@pytest.mark.parametrize(
    ("status", "conclusion"),
    [("queued", None), ("in_progress", None), ("waiting", None), ("pending", None), ("completed", "success")],
)
def test_guard_d_a_deploy_for_main_head_that_is_queued_running_or_green_already_covers_it(tmp_path, status, conclusion):
    dep = _scenario()["deploy"]
    dep["workflow_runs"].append(
        {"id": 4200, "head_sha": HEAD, "event": "workflow_run", "status": status, "conclusion": conclusion}
    )
    r, calls = _run(tmp_path, _scenario(deploy=dep))
    _assert_refused(r, calls, f"a Deploy for {HEAD} already covers main")


@needs_tools
def test_guard_d_coverage_stops_an_exit_1_for_a_cancelled_workflow_dispatch_run(tmp_path):
    run = {"id": int(RUN_ID), "event": "workflow_dispatch", "head_sha": OLD, "conclusion": "cancelled"}
    dep = {"workflow_runs": [{"id": 4200, "head_sha": HEAD, "event": "workflow_run", "status": "queued", "conclusion": None}]}
    r, calls = _run(tmp_path, _scenario(run=run, deploy=dep))
    _assert_refused(r, calls, f"a Deploy for {HEAD} already covers main")


@needs_tools
def test_guard_d_failed_cancelled_or_other_sha_deploys_do_not_cover_main(tmp_path):
    dep = {"workflow_runs": [
        {"id": 4300, "head_sha": HEAD, "event": "workflow_run", "status": "completed", "conclusion": "failure"},
        {"id": 4301, "head_sha": HEAD, "event": "workflow_run", "status": "completed", "conclusion": "cancelled"},
        {"id": 4302, "head_sha": OTHER, "event": "workflow_run", "status": "completed", "conclusion": "success"},
        {"id": 4303, "head_sha": OTHER, "event": "workflow_run", "status": "queued", "conclusion": None},
    ]}
    r, calls = _run(tmp_path, _scenario(deploy=dep))
    assert r.returncode == 0, r.stderr
    assert _dispatches(calls) == [f"workflow run deploy.yml --ref main -f sha={HEAD}"]


@needs_tools
def test_guard_d_an_empty_deploy_run_list_is_zero_runs_not_an_error(tmp_path):
    r, calls = _run(tmp_path, _scenario(deploy={"workflow_runs": []}))
    assert r.returncode == 0, r.stderr
    assert len(_dispatches(calls)) == 1


# ------------------------------------------------------------------ positive checks: a bad read is never "no"

READS = ["run", "jobs", "commit", "ci", "deploy"]


@needs_tools
@pytest.mark.parametrize("key", READS)
def test_a_failing_gh_read_is_an_error_and_never_a_redispatch(tmp_path, key):
    r, calls = _run(tmp_path, fail=key)
    _assert_error(r, calls)


@needs_tools
@pytest.mark.parametrize("key", READS)
def test_an_empty_gh_read_is_an_error_and_never_a_redispatch(tmp_path, key):
    r, calls = _run(tmp_path, empty=key)
    _assert_error(r, calls)


@needs_tools
def test_an_empty_jobs_read_is_not_taken_as_no_deploy_job_started(tmp_path):
    # a deliberate cancel of a started job, and the jobs read is broken: must not re-dispatch
    jobs = {"jobs": [{"name": "deploy", "started_at": "2026-11-27T10:00:00Z"}]}
    r, calls = _run(tmp_path, _scenario(jobs=jobs), empty="jobs")
    _assert_error(r, calls)


@needs_tools
def test_an_empty_deploy_list_read_is_not_taken_as_nothing_covers_main(tmp_path):
    dep = {"workflow_runs": [{"id": 4200, "head_sha": HEAD, "event": "workflow_run", "status": "in_progress", "conclusion": None}]}
    r, calls = _run(tmp_path, _scenario(deploy=dep), empty="deploy")
    _assert_error(r, calls)


@needs_tools
def test_a_rejected_dispatch_makes_the_script_fail_and_is_not_reported_as_done(tmp_path):
    r, calls = _run(tmp_path, dispatch_exit="1")
    assert r.returncode != 0
    assert "re-dispatched Deploy" not in r.stdout


# ------------------------------------------------------------------ the script text

def test_the_script_exists_and_sets_errexit_and_pipefail():
    assert SCRIPT.is_file(), f"{SCRIPT_REL} is missing"
    text = SCRIPT.read_text(encoding="utf-8")
    assert text.startswith("#!"), "needs a shebang"
    assert "bash" in text.splitlines()[0]
    assert re.search(r"^\s*set\b[^\n#]*pipefail", text, re.M), "needs `set -o pipefail` (set -euo pipefail)"
    assert re.search(r"^\s*set\s+-[a-z]*e", text, re.M), "needs set -e"


def test_the_script_does_not_trace_or_print_the_token():
    assert SCRIPT.is_file(), f"{SCRIPT_REL} is missing"
    text = SCRIPT.read_text(encoding="utf-8")
    assert not re.search(r"(echo|printf)[^\n]*GH_TOKEN", text)
    assert not re.search(r"^\s*set\s+-[a-z]*x", text, re.M)


# ------------------------------------------------------------------ incident.yml

def _wf() -> dict:
    yaml = pytest.importorskip("yaml")
    assert INCIDENT.is_file()
    data = yaml.safe_load(INCIDENT.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _job() -> dict:
    jobs = _wf()["jobs"]
    assert "deploy-cancelled" in jobs, "incident.yml has no job `deploy-cancelled`"
    return jobs["deploy-cancelled"]


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s)).replace('"', "'")


def test_the_job_runs_only_for_a_cancelled_deploy_workflow_run():
    cond = _norm(_job().get("if", ""))
    assert "github.event.workflow_run.name == 'Deploy'" in cond, cond
    assert "github.event.workflow_run.conclusion == 'cancelled'" in cond, cond


def test_the_job_permissions_are_contents_read_actions_write_and_nothing_broader():
    perms = _job().get("permissions")
    assert isinstance(perms, dict), "the job needs its own permissions block"
    assert perms.get("contents") == "read"
    assert perms.get("actions") == "write"
    assert set(perms) <= {"contents", "actions", "issues"}, perms
    assert perms.get("issues", "write") == "write"


def test_the_job_checks_out_and_runs_the_script_with_run_id_repo_and_token():
    job = _job()
    steps = job["steps"]
    assert any("actions/checkout" in str(s.get("uses", "")) for s in steps), "the script comes from a checkout"
    runner = [s for s in steps if SCRIPT_REL in str(s.get("run", ""))]
    assert runner, f"no step runs {SCRIPT_REL}"
    env = {**(job.get("env") or {}), **(runner[0].get("env") or {})}
    assert "github.event.workflow_run.id" in str(env.get("RUN_ID"))
    assert "github.repository" in str(env.get("GH_REPO"))
    assert "secrets.GITHUB_TOKEN" in str(env.get("GH_TOKEN"))
    assert "failure()" not in str(runner[0].get("if", "")), "the script step itself is not the failure step"


def test_a_failure_step_after_the_script_opens_or_updates_an_incident():
    steps = _job()["steps"]
    fail = [s for s in steps if "failure()" in str(s.get("if", ""))]
    assert fail, "needs a step with `if: failure()`"
    text = "\n".join(f"{s.get('run', '')}\n{s.get('uses', '')}" for s in fail).lower()
    assert "incident" in text, "the failure step must open or update the incident"
    assert "gh issue" in text or "factory" in text, text
    idx_run = next(i for i, s in enumerate(steps) if SCRIPT_REL in str(s.get("run", "")))
    assert steps.index(fail[0]) > idx_run, "the failure step comes after the script step"


def test_the_failure_step_names_the_run_it_reports():
    steps = _job()["steps"]
    fail = next(s for s in steps if "failure()" in str(s.get("if", "")))
    blob = f"{fail.get('run', '')}\n{fail.get('env', '')}\n{fail.get('with', '')}"
    assert "html_url" in blob or "workflow_run.id" in blob or "RUN_ID" in blob


def test_the_six_watched_workflows_and_the_existing_jobs_are_unchanged():
    wf = _wf()
    trig = wf.get("on", wf.get(True))
    assert trig["workflow_run"]["workflows"] == SIX
    assert trig["workflow_run"]["types"] == ["completed"]
    jobs = wf["jobs"]
    assert {"decide", "incident", "deploy-cancelled"} <= set(jobs)
    decide = _norm(jobs["decide"]["if"]).replace(" ", "")
    assert "'failure','timed_out','startup_failure'" in decide
    assert "cancelled" not in decide, "decide stays failure-only; cancelled has its own job"
    assert str(jobs["incident"].get("uses", "")).startswith("Shashkist/factory/.github/workflows/incident.yml@")
    assert wf["permissions"] == {"actions": "read", "contents": "read", "issues": "write"}
