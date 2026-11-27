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
       -> {"jobs": [{"name", "started_at", "runner_id", "steps": [...]}, ...]}   (real shape:
          started_at is filled even for a job that never ran; it had 0 steps and no runner_id)
  3. gh api "repos/<GH_REPO>/actions/workflows/ci.yml/runs?branch=main&event=push..."
       -> {"workflow_runs": [{"head_sha", "head_branch", "event", "status", "conclusion",
            "created_at"}, ...]}  in no promised order; decoys for other branches and events
  4. gh api "repos/<GH_REPO>/actions/workflows/deploy.yml/runs..."
       -> {"workflow_runs": [{"id", "head_sha", "display_title", "event", "status",
          "conclusion"}, ...]}   (display_title is `Deploy <sha deployed>`; head_sha is main's head
          when the run was triggered and may be a docs-only commit: never match on it)
  5. gh api "repos/<GH_REPO>/actions/workflows/bootstrap.yml/runs..."   (same shape)
  6. gh api "repos/<GH_REPO>/actions/workflows/server-smoke.yml/runs..." (same shape)
  6b. gh api repos/<GH_REPO>/actions/runs/<smoke run id>/jobs   (for a smoke run not completed)
       -> {"jobs": [{"name", "status", "runner_id", "steps": [...]}, ...]}
  7. gh workflow run deploy.yml --ref main -f sha=<target sha>    (the only write; at most once)
The fake also answers `gh api repos/<GH_REPO>/commits/main` (a docs-only head that has no CI run);
the script may read it but must not deploy it.

Behaviour the exam fixes (D189 and its amendments 1 and 2)
----------------------------------------------------
  * The target sha is the head_sha of the newest `push` CI run on `main` by created_at (a
    docs-only main head has no CI run, so the newest code commit is deployed). No such CI run at
    all: non-zero exit, no dispatch.
  * Amendment 2: guard (d) matches the target sha against the Deploy run's `display_title`
    (`Deploy <sha>`, from `run-name:` in deploy.yml), never its `head_sha`. While any other Deploy
    run is queued, pending, waiting or requested: no dispatch, exactly one line
        a pending Deploy holds the slot, nothing to do                           exit 0
    Smoke: only a `smoke` job that is queued, pending or waiting with no runner_id and no steps
    holds the slot (the script reads the jobs of each smoke run that is not completed); the run
    status does not matter. Bootstrap: only pending, waiting, requested hold the slot.
  * Guards, in this reading: (b) the cancelled run's `deploy` job never started, i.e. no job named
    `deploy` has a step or a runner_id (started_at proves nothing); (c) that CI run's conclusion is
    `success`; (d) no Deploy run for the target sha is queued, in progress, waiting, pending,
    requested or completed `success` already; (a) the cancelled run's `event` is `workflow_run`.
  * (b) false -> stdout is exactly one line
        deploy job had started: deliberate cancel or timeout, nothing to do      exit 0
    (d) false (a Deploy covers the target) -> exactly one line
        a Deploy for <sha> already covers main                                   exit 0
    (c) false -> exactly one line
        CI for main's head is <conclusion>: nothing to deploy                    exit 0
    No `gh workflow run` in any of these.
  * A held pending slot (bootstrap run pending/waiting/requested; smoke job as above) holds
    the one pending slot of the shared concurrency group: no dispatch (it would cancel that run),
    exit 1, so the incident step names the undeployed commit.
  * (a) false (the cancelled run was a `workflow_dispatch`: a second cancel) with (b), (c) true and
    nothing covering the target: no dispatch, exit 1 (the `if: failure()` step opens an incident),
    so a Deploy cancelled again never loops.
  * All hold and the event is `workflow_run`: one `gh workflow run deploy.yml --ref main -f
    sha=<target sha>` and stdout exactly `re-dispatched Deploy for <sha>`, exit 0.
  * A failed read (gh exits 1) or an empty read (gh exits 0 with no output) of any of the six
    endpoints is an error: non-zero exit, never a dispatch. A well-formed empty list
    (`{"jobs": []}`, `{"workflow_runs": []}`) is a real answer and is read as zero (for the CI
    list that means no push CI run: an error too).
  * Check order when a guard fails is not fixed, except that an exit 1 and a dispatch happen only
    when (b), (c) and (d) all say "go on", and the event decides which of the two.
  * incident.yml's failure step creates the issue with labels type:incident, priority:p0,
    status:ready and looks for an open one by the type:incident label and its title.
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
DEPLOY_WF = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
INCIDENT = REPO_ROOT / ".github" / "workflows" / "incident.yml"
SCRIPT = REPO_ROOT / "labs" / "deploy" / "watch" / "deploy-cancelled.sh"
SCRIPT_REL = "labs/deploy/watch/deploy-cancelled.sh"

REPO = "Shashkist/Learning_factory"
RUN_ID = "4242"
HEAD = "a" * 40  # the head_sha of the newest push CI run on main: the deploy target
OLD = "b" * 40   # an older sha (the cancelled run's own head_sha)
OTHER = "c" * 40
DOCS = "d" * 40  # main's head: a docs-only commit with no CI run

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
      "repos/$GH_REPO/actions/runs/"*"/jobs") n="${ep#repos/$GH_REPO/actions/runs/}"; key="jobs_${n%/jobs}" ;;
      "repos/$GH_REPO/commits/main") key=commit ;;
      "repos/$GH_REPO/actions/workflows/ci.yml/runs") key=ci ;;
      "repos/$GH_REPO/actions/workflows/deploy.yml/runs") key=deploy ;;
      "repos/$GH_REPO/actions/workflows/bootstrap.yml/runs") key=boot ;;
      "repos/$GH_REPO/actions/workflows/server-smoke.yml/runs") key=smoke ;;
      *) echo "fake gh: unexpected endpoint $ep" >&2; exit 99 ;;
    esac
    case ",${FAKE_GH_FAIL:-}," in *",$key,"*) echo "gh: HTTP 500" >&2; exit 1 ;; esac
    case ",${FAKE_GH_EMPTY:-}," in *",$key,"*) exit 0 ;; esac
    [ -f "$FAKE_GH_DIR/$key.json" ] || { echo "fake gh: no fixture for $key" >&2; exit 99; }
    cat "$FAKE_GH_DIR/$key.json"
    ;;
  workflow)
    [ "${2:-}" = run ] || { echo "fake gh: unexpected: $*" >&2; exit 99; }
    exit "${FAKE_GH_DISPATCH_EXIT:-0}"
    ;;
  *) echo "fake gh: unexpected: $*" >&2; exit 99 ;;
esac
"""


def _job(started_at="2026-11-27T10:00:00Z", steps=None, runner_id=None, name="deploy"):
    """A job in the real shape: GitHub fills started_at even for a job that never ran."""
    return {"name": name, "started_at": started_at, "conclusion": "cancelled",
            "runner_id": runner_id, "steps": steps or []}


STEP = {"name": "Set up job", "status": "completed", "conclusion": "success", "number": 1}


def _dep(rid, status, conclusion, title_sha, head_sha=None, event="workflow_run"):
    """A Deploy run: display_title names the sha it deploys; head_sha is main's head at trigger time."""
    return {"id": rid, "head_sha": head_sha or title_sha, "display_title": f"Deploy {title_sha}",
            "event": event, "status": status, "conclusion": conclusion}


def _sjob(name, status, runner_id=None, steps=None):
    return {"name": name, "status": status, "conclusion": None, "runner_id": runner_id,
            "steps": steps or []}


def _ci(sha=HEAD, conclusion="success", created="2026-11-27T10:03:00Z", **kw):
    return {"head_sha": sha, "head_branch": "main", "event": "push", "status": "completed",
            "conclusion": conclusion, "created_at": created, **kw}


def _scenario(**over) -> dict:
    """Default: every guard holds, the cancelled run was a workflow_run Deploy, main's head is a
    docs-only commit and the newest push CI run on main is HEAD's (green)."""
    s = {
        "run": {"id": int(RUN_ID), "event": "workflow_run", "head_sha": OLD, "conclusion": "cancelled"},
        "jobs": {"jobs": [_job()]},
        "commit": {"sha": DOCS},
        "ci": {"workflow_runs": [
            # decoys: another branch, another event (both newer than the real one), an older push
            {"head_sha": OTHER, "head_branch": "f/y", "event": "push", "status": "completed", "conclusion": "failure", "created_at": "2026-11-27T10:07:00Z"},
            {"head_sha": OTHER, "head_branch": "main", "event": "pull_request", "status": "completed", "conclusion": "failure", "created_at": "2026-11-27T10:06:00Z"},
            _ci(HEAD, "success", "2026-11-27T10:03:00Z"),
            _ci(OLD, "failure", "2026-11-27T10:01:00Z"),
        ]},
        "deploy": {"workflow_runs": [
            _dep(int(RUN_ID), "completed", "cancelled", HEAD, DOCS),
            _dep(4100, "completed", "success", OTHER),
            _dep(4101, "in_progress", None, OTHER, DOCS),
            _dep(4102, "completed", "failure", HEAD),
            _dep(4103, "completed", "success", DOCS),
        ]},
        "boot": {"workflow_runs": [
            {"id": 5100, "head_sha": HEAD, "event": "push", "status": "completed", "conclusion": "success"},
        ]},
        "smoke": {"workflow_runs": [
            {"id": 5200, "head_sha": HEAD, "event": "workflow_run", "status": "completed", "conclusion": "success"},
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

DISPATCH = f"workflow run deploy.yml --ref main -f sha={HEAD}"


@needs_tools
def test_all_guards_hold_redispatches_deploy_for_the_newest_push_ci_sha_exactly_once(tmp_path):
    r, calls = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert _dispatches(calls) == [DISPATCH]
    assert _lines(r) == [f"re-dispatched Deploy for {HEAD}"]


@needs_tools
def test_a_docs_only_main_head_deploys_the_newest_code_commit_not_the_head(tmp_path):
    r, calls = _run(tmp_path)  # commits/main says DOCS, which has no CI run
    assert DOCS not in " ".join(_dispatches(calls))
    assert _dispatches(calls) == [DISPATCH]


@needs_tools
def test_the_newest_push_ci_run_is_found_by_created_at_not_by_list_order(tmp_path):
    ci = {"workflow_runs": [
        _ci(OLD, "failure", "2026-11-27T09:00:00Z"),
        _ci(HEAD, "success", "2026-11-27T11:00:00Z"),
        _ci(OTHER, "failure", "2026-11-27T10:00:00Z"),
    ]}
    r, calls = _run(tmp_path, _scenario(ci=ci))
    assert r.returncode == 0, r.stderr
    assert _dispatches(calls) == [DISPATCH]


@needs_tools
def test_a_deploy_run_with_no_jobs_listed_yet_counts_as_never_started(tmp_path):
    r, calls = _run(tmp_path, _scenario(jobs={"jobs": []}))
    assert r.returncode == 0, r.stderr
    assert _dispatches(calls) == [DISPATCH]


@needs_tools
def test_a_started_job_with_another_name_does_not_stop_the_redispatch(tmp_path):
    jobs = {"jobs": [_job(), _job(name="notify", steps=[STEP], runner_id=7)]}
    r, calls = _run(tmp_path, _scenario(jobs=jobs))
    assert r.returncode == 0, r.stderr
    assert len(_dispatches(calls)) == 1


@needs_tools
def test_the_latest_ci_run_decides_not_an_older_success(tmp_path):
    ci = {"workflow_runs": [
        _ci(HEAD, "failure", "2026-11-27T10:09:00Z"),
        _ci(OLD, "success", "2026-11-27T10:01:00Z"),
    ]}
    r, calls = _run(tmp_path, _scenario(ci=ci))
    _assert_refused(r, calls, "CI for main's head is failure: nothing to deploy")


@needs_tools
def test_a_newer_failed_push_ci_run_for_another_sha_decides_not_an_older_green_one(tmp_path):
    ci = {"workflow_runs": [
        _ci(OTHER, "failure", "2026-11-27T10:09:00Z"),
        _ci(HEAD, "success", "2026-11-27T10:01:00Z"),
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


@needs_tools
def test_a_second_cancel_raises_an_incident_instead_of_looping(tmp_path):
    # first cancel (workflow_run): one dispatch. The dispatched run is cancelled in turn (event
    # workflow_dispatch): no dispatch, exit 1, so the failure step opens the incident.
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    first, calls1 = _run(tmp_path / "one")
    assert first.returncode == 0 and len(_dispatches(calls1)) == 1
    run = {"id": int(RUN_ID), "event": "workflow_dispatch", "head_sha": HEAD, "conclusion": "cancelled"}
    second, calls2 = _run(tmp_path / "two", _scenario(run=run))
    assert second.returncode == 1, f"{second.stdout!r} {second.stderr!r}"
    assert _dispatches(calls2) == []


# ------------------------------------------------------------------ guard (b): the deploy job never started

@needs_tools
def test_guard_b_a_deploy_job_that_ran_a_step_means_deliberate_cancel_so_no_redispatch(tmp_path):
    jobs = {"jobs": [_job(steps=[STEP])]}
    r, calls = _run(tmp_path, _scenario(jobs=jobs))
    _assert_refused(r, calls, "deploy job had started: deliberate cancel or timeout, nothing to do")


@needs_tools
def test_guard_b_a_deploy_job_with_a_runner_id_had_started_even_with_no_steps(tmp_path):
    jobs = {"jobs": [_job(runner_id=12)]}
    r, calls = _run(tmp_path, _scenario(jobs=jobs))
    _assert_refused(r, calls, "deploy job had started: deliberate cancel or timeout, nothing to do")


@needs_tools
def test_guard_b_started_at_alone_does_not_mean_the_job_started(tmp_path):
    # real shape of a job that never ran: started_at filled, zero steps, no runner
    jobs = {"jobs": [_job(started_at="2026-11-27T10:00:00Z", steps=[], runner_id=None)]}
    r, calls = _run(tmp_path, _scenario(jobs=jobs))
    assert r.returncode == 0, r.stderr
    assert _dispatches(calls) == [DISPATCH]


@needs_tools
def test_guard_b_applies_to_a_workflow_dispatch_run_too_and_is_not_an_incident(tmp_path):
    run = {"id": int(RUN_ID), "event": "workflow_dispatch", "head_sha": OLD, "conclusion": "cancelled"}
    jobs = {"jobs": [_job(steps=[STEP], runner_id=3)]}
    r, calls = _run(tmp_path, _scenario(run=run, jobs=jobs))
    _assert_refused(r, calls, "deploy job had started: deliberate cancel or timeout, nothing to do")


# ------------------------------------------------------------------ guard (c): CI for the target is green

@needs_tools
@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out"])
def test_guard_c_ci_for_the_target_not_success_means_nothing_to_deploy(tmp_path, conclusion):
    ci = {"workflow_runs": [_ci(HEAD, conclusion)]}
    r, calls = _run(tmp_path, _scenario(ci=ci))
    _assert_refused(r, calls, f"CI for main's head is {conclusion}: nothing to deploy")


@needs_tools
def test_guard_c_a_green_ci_on_another_branch_or_a_pull_request_is_not_proof(tmp_path):
    ci = {"workflow_runs": [
        {"head_sha": OTHER, "head_branch": "f/y", "event": "push", "status": "completed", "conclusion": "success", "created_at": "2026-11-27T10:05:00Z"},
        {"head_sha": HEAD, "head_branch": "main", "event": "pull_request", "status": "completed", "conclusion": "success", "created_at": "2026-11-27T10:04:00Z"},
        _ci(HEAD, "failure", "2026-11-27T10:03:00Z"),
    ]}
    r, calls = _run(tmp_path, _scenario(ci=ci))
    _assert_refused(r, calls, "CI for main's head is failure: nothing to deploy")


@needs_tools
def test_guard_c_a_ci_run_still_in_progress_is_not_green_and_never_redispatches(tmp_path):
    ci = {"workflow_runs": [
        {"head_sha": HEAD, "head_branch": "main", "event": "push", "status": "in_progress", "conclusion": None, "created_at": "2026-11-27T10:09:00Z"},
        _ci(OLD, "success", "2026-11-27T10:01:00Z"),
    ]}
    r, calls = _run(tmp_path, _scenario(ci=ci))
    assert _dispatches(calls) == []


NO_PUSH_CI = [
    {"workflow_runs": []},
    {"workflow_runs": [
        {"head_sha": OTHER, "head_branch": "f/y", "event": "push", "status": "completed", "conclusion": "success", "created_at": "2026-11-27T10:05:00Z"},
        {"head_sha": HEAD, "head_branch": "main", "event": "pull_request", "status": "completed", "conclusion": "success", "created_at": "2026-11-27T10:04:00Z"},
    ]},
]


@needs_tools
@pytest.mark.parametrize("ci", NO_PUSH_CI, ids=["empty list", "only other branches and events"])
def test_guard_c_no_push_ci_run_on_main_at_all_is_an_error_and_never_redispatches(tmp_path, ci):
    r, calls = _run(tmp_path, _scenario(ci=ci))
    _assert_error(r, calls)


# ------------------------------------------------------------------ guard (d): a Deploy covers the target

PENDING_LINE = "a pending Deploy holds the slot, nothing to do"
COVER_LINE = f"a Deploy for {HEAD} already covers main"


@needs_tools
@pytest.mark.parametrize("head", [HEAD, DOCS], ids=["same head_sha", "head_sha is a docs-only commit"])
@pytest.mark.parametrize(("status", "conclusion"), [("in_progress", None), ("completed", "success")])
def test_guard_d_a_running_or_green_deploy_whose_display_title_names_the_target_covers_it(tmp_path, status, conclusion, head):
    dep = _scenario()["deploy"]
    dep["workflow_runs"].append(_dep(4200, status, conclusion, HEAD, head))
    r, calls = _run(tmp_path, _scenario(deploy=dep))
    _assert_refused(r, calls, COVER_LINE)


@needs_tools
@pytest.mark.parametrize("head", [HEAD, DOCS], ids=["same head_sha", "head_sha is a docs-only commit"])
@pytest.mark.parametrize("status", ["queued", "pending", "waiting", "requested"])
def test_guard_d_a_pending_deploy_whose_display_title_names_the_target_covers_it(tmp_path, status, head):
    # D189 amendment 3 (c): the cover line for every pending status, `requested` included
    dep = _scenario()["deploy"]
    dep["workflow_runs"].append(_dep(4200, status, None, HEAD, head))
    r, calls = _run(tmp_path, _scenario(deploy=dep))
    _assert_refused(r, calls, COVER_LINE)


@needs_tools
@pytest.mark.parametrize(("status", "conclusion"), [("in_progress", None), ("completed", "success")])
def test_guard_d_a_deploy_with_head_sha_of_the_target_but_a_display_title_of_another_sha_does_not_cover(tmp_path, status, conclusion):
    dep = {"workflow_runs": [_dep(4200, status, conclusion, OTHER, HEAD)]}
    r, calls = _run(tmp_path, _scenario(deploy=dep))
    assert r.returncode == 0, r.stderr
    assert _dispatches(calls) == [DISPATCH]


@needs_tools
def test_guard_d_a_deploy_for_the_docs_only_main_head_does_not_cover_the_target(tmp_path):
    # (was queued before amendment 2; a queued Deploy now holds the slot, so it runs here)
    dep = {"workflow_runs": [_dep(4200, "in_progress", None, DOCS)]}
    r, calls = _run(tmp_path, _scenario(deploy=dep))
    assert r.returncode == 0, r.stderr
    assert _dispatches(calls) == [DISPATCH]


@needs_tools
def test_guard_d_coverage_stops_an_exit_1_for_a_cancelled_workflow_dispatch_run(tmp_path):
    run = {"id": int(RUN_ID), "event": "workflow_dispatch", "head_sha": OLD, "conclusion": "cancelled"}
    dep = {"workflow_runs": [_dep(4200, "in_progress", None, HEAD, DOCS)]}
    r, calls = _run(tmp_path, _scenario(run=run, deploy=dep))
    _assert_refused(r, calls, COVER_LINE)


@needs_tools
def test_guard_d_failed_cancelled_or_other_sha_deploys_do_not_cover_the_target(tmp_path):
    # (the old queued OTHER run is now in_progress: a queued Deploy holds the slot, see below)
    dep = {"workflow_runs": [
        _dep(4300, "completed", "failure", HEAD),
        _dep(4301, "completed", "cancelled", HEAD),
        _dep(4302, "completed", "success", OTHER),
        _dep(4303, "in_progress", None, OTHER),
    ]}
    r, calls = _run(tmp_path, _scenario(deploy=dep))
    assert r.returncode == 0, r.stderr
    assert _dispatches(calls) == [DISPATCH]


# ------------------------------------------------------------------ amendment 2: a pending Deploy holds the slot

PENDING_STATUSES = ["queued", "pending", "waiting", "requested"]
WILL_NOT_DEPLOY = f"a pending Deploy will not deploy {HEAD}: not re-dispatching, {HEAD} is not deployed"


def _assert_will_not_deploy(r, calls):
    """D189 amendment 3 (f): no dispatch, exit 1, one stderr line naming the target."""
    assert r.returncode == 1, f"exit {r.returncode}: {r.stdout!r} {r.stderr!r}"
    assert _dispatches(calls) == [], f"re-dispatched: {calls}"
    assert WILL_NOT_DEPLOY in r.stderr.splitlines(), f"stderr was {r.stderr!r}"


@needs_tools
@pytest.mark.parametrize("status", PENDING_STATUSES)
@pytest.mark.parametrize("sha", [OTHER, DOCS, OLD])
def test_any_other_pending_workflow_dispatch_deploy_run_means_no_dispatch_and_one_line_exit_0(tmp_path, status, sha):
    dep = _scenario()["deploy"]
    dep["workflow_runs"].append(_dep(4400, status, None, sha, event="workflow_dispatch"))
    r, calls = _run(tmp_path, _scenario(deploy=dep))
    _assert_refused(r, calls, PENDING_LINE)


@needs_tools
@pytest.mark.parametrize("status", PENDING_STATUSES)
@pytest.mark.parametrize("sha", [OTHER, DOCS, OLD])
def test_any_other_pending_workflow_run_deploy_will_not_deploy_the_target_so_no_dispatch_and_exit_1_naming_it(tmp_path, status, sha):
    # a CI re-run of an older sha that finished later, or a run whose deploy job its `if` skips
    dep = _scenario()["deploy"]
    dep["workflow_runs"].append(_dep(4400, status, None, sha, event="workflow_run"))
    r, calls = _run(tmp_path, _scenario(deploy=dep))
    _assert_will_not_deploy(r, calls)


@needs_tools
@pytest.mark.parametrize("status", PENDING_STATUSES)
def test_a_pending_workflow_run_deploy_with_a_foreign_title_also_stops_a_cancelled_workflow_dispatch_run(tmp_path, status):
    run = {"id": int(RUN_ID), "event": "workflow_dispatch", "head_sha": OLD, "conclusion": "cancelled"}
    dep = _scenario()["deploy"]
    dep["workflow_runs"].append(_dep(4400, status, None, OTHER, event="workflow_run"))
    r, calls = _run(tmp_path, _scenario(run=run, deploy=dep))
    _assert_will_not_deploy(r, calls)


@needs_tools
def test_a_pending_workflow_dispatch_and_a_pending_workflow_run_deploy_together_give_the_pending_line_exit_0(tmp_path):
    dep = _scenario()["deploy"]
    dep["workflow_runs"].append(_dep(4400, "queued", None, OTHER, event="workflow_run"))
    dep["workflow_runs"].append(_dep(4401, "pending", None, OLD, event="workflow_dispatch"))
    r, calls = _run(tmp_path, _scenario(deploy=dep))
    _assert_refused(r, calls, PENDING_LINE)


@needs_tools
@pytest.mark.parametrize("status", PENDING_STATUSES)
def test_a_pending_deploy_for_the_target_covers_it_even_beside_a_pending_workflow_run_deploy(tmp_path, status):
    dep = _scenario()["deploy"]
    dep["workflow_runs"].append(_dep(4200, status, None, HEAD, event="workflow_dispatch"))
    dep["workflow_runs"].append(_dep(4400, "queued", None, OTHER, event="workflow_run"))
    r, calls = _run(tmp_path, _scenario(deploy=dep))
    _assert_refused(r, calls, COVER_LINE)


@needs_tools
@pytest.mark.parametrize("status", PENDING_STATUSES)
def test_a_pending_workflow_dispatch_deploy_run_holds_the_slot_for_a_cancelled_workflow_dispatch_run_too(tmp_path, status):
    run = {"id": int(RUN_ID), "event": "workflow_dispatch", "head_sha": OLD, "conclusion": "cancelled"}
    dep = _scenario()["deploy"]
    dep["workflow_runs"].append(_dep(4400, status, None, OTHER, event="workflow_dispatch"))
    r, calls = _run(tmp_path, _scenario(run=run, deploy=dep))
    _assert_refused(r, calls, PENDING_LINE)


# D189 amendment 3 (d): a pending bootstrap run or smoke job is checked before any Deploy-slot check,
# so a queued Deploy that holds the running slot never hides it.

@needs_tools
@pytest.mark.parametrize("event", ["workflow_run", "workflow_dispatch", "push"])
@pytest.mark.parametrize("sha", [OTHER, OLD, DOCS])
def test_a_queued_deploy_does_not_hide_a_smoke_job_that_holds_the_pending_slot(tmp_path, event, sha):
    dep = _scenario()["deploy"]
    dep["workflow_runs"].append(_dep(4400, "queued", None, sha, event=event))
    jobs = [_sjob("gate", "completed"), _sjob("smoke", "queued")]
    r, calls = _run(tmp_path, _scenario(deploy=dep, **_smoke("in_progress", jobs)))
    assert r.returncode == 1, f"exit {r.returncode}: {r.stdout!r} {r.stderr!r}"
    assert _dispatches(calls) == [], f"re-dispatched: {calls}"


@needs_tools
@pytest.mark.parametrize("event", ["workflow_run", "workflow_dispatch"])
@pytest.mark.parametrize("boot_status", ["pending", "waiting", "requested"])
def test_a_queued_deploy_does_not_hide_a_pending_bootstrap_run(tmp_path, event, boot_status):
    dep = _scenario()["deploy"]
    dep["workflow_runs"].append(_dep(4400, "queued", None, OTHER, event=event))
    boot = {"workflow_runs": [{"id": 5301, "head_sha": OTHER, "event": "push", "status": boot_status, "conclusion": None}]}
    r, calls = _run(tmp_path, _scenario(deploy=dep, boot=boot))
    assert r.returncode == 1, f"exit {r.returncode}: {r.stdout!r} {r.stderr!r}"
    assert _dispatches(calls) == [], f"re-dispatched: {calls}"


@needs_tools
def test_a_queued_deploy_for_the_target_still_covers_it_beside_a_pending_smoke_job(tmp_path):
    # (c) comes before (d): the target is covered, so the cover line and exit 0
    dep = _scenario()["deploy"]
    dep["workflow_runs"].append(_dep(4200, "queued", None, HEAD))
    jobs = [_sjob("gate", "completed"), _sjob("smoke", "queued")]
    r, calls = _run(tmp_path, _scenario(deploy=dep, **_smoke("in_progress", jobs)))
    _assert_refused(r, calls, COVER_LINE)


@needs_tools
def test_a_running_or_finished_other_deploy_run_does_not_hold_the_slot(tmp_path):
    dep = {"workflow_runs": [
        _dep(4400, "in_progress", None, OTHER), _dep(4401, "completed", "cancelled", OTHER),
        _dep(4402, "completed", "success", OTHER),
    ]}
    r, calls = _run(tmp_path, _scenario(deploy=dep))
    assert r.returncode == 0, r.stderr
    assert _dispatches(calls) == [DISPATCH]


@needs_tools
def test_guard_b_and_c_still_come_first_over_a_pending_deploy(tmp_path):
    dep = _scenario()["deploy"]
    dep["workflow_runs"].append(_dep(4400, "queued", None, OTHER))
    r, calls = _run(tmp_path, _scenario(deploy=dep, jobs={"jobs": [_job(steps=[STEP])]}))
    assert r.returncode == 0 and _dispatches(calls) == []
    assert _lines(r) in (
        ["deploy job had started: deliberate cancel or timeout, nothing to do"], [PENDING_LINE]
    ), r.stdout


@needs_tools
def test_guard_d_an_empty_deploy_run_list_is_zero_runs_not_an_error(tmp_path):
    r, calls = _run(tmp_path, _scenario(deploy={"workflow_runs": []}))
    assert r.returncode == 0, r.stderr
    assert len(_dispatches(calls)) == 1


# ------------------------------------------------------------------ the pending slot of server-lab-host

BOOT_HOLDS = ["pending", "waiting", "requested"]
SMOKE_RUN_STATES = ["queued", "pending", "waiting", "requested", "in_progress"]
HOLDING_JOB_STATES = ["queued", "pending", "waiting"]


def _smoke(run_status, jobs, rid=5301):
    """Scenario overrides for one not-completed smoke run with the given jobs."""
    runs = [{"id": 5300, "head_sha": HEAD, "event": "push", "status": "completed", "conclusion": "success"},
            {"id": rid, "head_sha": HEAD, "event": "workflow_run", "status": run_status, "conclusion": None}]
    return {"smoke": {"workflow_runs": runs}, f"jobs_{rid}": {"jobs": jobs}}


@needs_tools
@pytest.mark.parametrize("status", BOOT_HOLDS)
def test_a_pending_waiting_or_requested_bootstrap_run_holds_the_slot_so_no_dispatch_and_exit_1(tmp_path, status):
    runs = {"workflow_runs": [
        {"id": 5300, "head_sha": HEAD, "event": "push", "status": "completed", "conclusion": "success"},
        {"id": 5301, "head_sha": HEAD, "event": "push", "status": status, "conclusion": None},
    ]}
    r, calls = _run(tmp_path, _scenario(boot=runs))
    assert r.returncode == 1, f"exit {r.returncode}: {r.stdout!r} {r.stderr!r}"
    assert _dispatches(calls) == [], f"dispatch would cancel the pending run: {calls}"


@needs_tools
def test_a_pending_bootstrap_run_blocks_a_workflow_dispatch_cancel_too(tmp_path):
    run = {"id": int(RUN_ID), "event": "workflow_dispatch", "head_sha": OLD, "conclusion": "cancelled"}
    runs = {"workflow_runs": [{"id": 5301, "head_sha": OTHER, "event": "push", "status": "pending", "conclusion": None}]}
    r, calls = _run(tmp_path, _scenario(run=run, boot=runs))
    assert r.returncode == 1
    assert _dispatches(calls) == []


@needs_tools
@pytest.mark.parametrize("status", ["queued", "in_progress"])
def test_a_queued_or_running_bootstrap_run_does_not_stop_the_redispatch(tmp_path, status):
    # a queued bootstrap run holds the running slot and waits for a runner: no longer counts (amendment 2)
    runs = {"workflow_runs": [{"id": 5301, "head_sha": HEAD, "event": "push", "status": status, "conclusion": None}]}
    r, calls = _run(tmp_path, _scenario(boot=runs))
    assert r.returncode == 0, r.stderr
    assert _dispatches(calls) == [DISPATCH]


@needs_tools
@pytest.mark.parametrize("job_status", HOLDING_JOB_STATES)
@pytest.mark.parametrize("run_status", SMOKE_RUN_STATES)
def test_a_smoke_job_waiting_for_the_lock_holds_the_slot_so_no_dispatch_and_exit_1(tmp_path, run_status, job_status):
    jobs = [_sjob("gate", "completed"), _sjob("smoke", job_status)]
    r, calls = _run(tmp_path, _scenario(**_smoke(run_status, jobs)))
    assert r.returncode == 1, f"exit {r.returncode}: {r.stdout!r} {r.stderr!r}"
    assert _dispatches(calls) == [], f"dispatch would cancel the pending run: {calls}"


@needs_tools
def test_a_smoke_run_in_progress_whose_smoke_job_is_pending_is_spared(tmp_path):
    jobs = [_sjob("gate", "completed"), _sjob("smoke", "pending")]
    r, calls = _run(tmp_path, _scenario(**_smoke("in_progress", jobs)))
    assert r.returncode == 1, f"exit {r.returncode}: {r.stdout!r} {r.stderr!r}"
    assert _dispatches(calls) == []


@needs_tools
def test_a_smoke_job_holding_the_slot_blocks_a_workflow_dispatch_cancel_too(tmp_path):
    run = {"id": int(RUN_ID), "event": "workflow_dispatch", "head_sha": OLD, "conclusion": "cancelled"}
    jobs = [_sjob("gate", "completed"), _sjob("smoke", "queued")]
    r, calls = _run(tmp_path, _scenario(run=run, **_smoke("in_progress", jobs)))
    assert r.returncode == 1
    assert _dispatches(calls) == []


@needs_tools
@pytest.mark.parametrize("run_status", ["queued", "in_progress"])
@pytest.mark.parametrize("gate_status", ["queued", "in_progress"])
def test_a_smoke_run_whose_gate_job_is_queued_or_running_and_has_no_smoke_job_yet_does_not_block(tmp_path, run_status, gate_status):
    r, calls = _run(tmp_path, _scenario(**_smoke(run_status, [_sjob("gate", gate_status)])))
    assert r.returncode == 0, r.stderr
    assert _dispatches(calls) == [DISPATCH]


@needs_tools
def test_a_smoke_run_with_no_jobs_listed_yet_does_not_block(tmp_path):
    r, calls = _run(tmp_path, _scenario(**_smoke("queued", [])))
    assert r.returncode == 0, r.stderr
    assert _dispatches(calls) == [DISPATCH]


@needs_tools
def test_a_smoke_run_with_a_running_smoke_job_does_not_block(tmp_path):
    jobs = [_sjob("gate", "completed"), _sjob("smoke", "in_progress", runner_id=9, steps=[STEP])]
    r, calls = _run(tmp_path, _scenario(**_smoke("in_progress", jobs)))
    assert r.returncode == 0, r.stderr
    assert _dispatches(calls) == [DISPATCH]


@needs_tools
@pytest.mark.parametrize(
    ("runner_id", "steps"), [(9, []), (None, [STEP])], ids=["has a runner_id", "has a step"]
)
def test_a_queued_smoke_job_that_has_a_runner_or_a_step_has_started_and_does_not_block(tmp_path, runner_id, steps):
    jobs = [_sjob("gate", "completed"), _sjob("smoke", "queued", runner_id=runner_id, steps=steps)]
    r, calls = _run(tmp_path, _scenario(**_smoke("in_progress", jobs)))
    assert r.returncode == 0, r.stderr
    assert _dispatches(calls) == [DISPATCH]


@needs_tools
def test_the_pending_smoke_job_of_a_second_smoke_run_is_found_among_several_runs(tmp_path):
    over = _smoke("in_progress", [_sjob("gate", "in_progress")], rid=5301)
    over["smoke"]["workflow_runs"].append(
        {"id": 5302, "head_sha": OTHER, "event": "workflow_run", "status": "in_progress", "conclusion": None})
    over["jobs_5302"] = {"jobs": [_sjob("gate", "completed"), _sjob("smoke", "waiting")]}
    r, calls = _run(tmp_path, _scenario(**over))
    assert r.returncode == 1, f"exit {r.returncode}: {r.stdout!r} {r.stderr!r}"
    assert _dispatches(calls) == []


@needs_tools
@pytest.mark.parametrize("fail_mode", ["fail", "empty"])
def test_a_failing_or_empty_read_of_a_smoke_runs_jobs_is_an_error_and_never_a_dispatch(tmp_path, fail_mode):
    over = _smoke("in_progress", [_sjob("gate", "completed")])
    r, calls = _run(tmp_path, _scenario(**over), **{fail_mode: "jobs_5301"})
    _assert_error(r, calls)


@needs_tools
@pytest.mark.parametrize("key", ["boot", "smoke"])
@pytest.mark.parametrize("conclusion", ["success", "cancelled", "failure"])
def test_a_finished_bootstrap_or_smoke_run_does_not_stop_the_redispatch(tmp_path, key, conclusion):
    runs = {"workflow_runs": [{"id": 5301, "head_sha": HEAD, "event": "push", "status": "completed", "conclusion": conclusion}]}
    r, calls = _run(tmp_path, _scenario(**{key: runs}))
    assert r.returncode == 0, r.stderr
    assert _dispatches(calls) == [DISPATCH]


@needs_tools
@pytest.mark.parametrize("key", ["boot", "smoke"])
def test_an_empty_bootstrap_or_smoke_run_list_is_zero_runs_not_an_error(tmp_path, key):
    r, calls = _run(tmp_path, _scenario(**{key: {"workflow_runs": []}}))
    assert r.returncode == 0, r.stderr
    assert _dispatches(calls) == [DISPATCH]


# ------------------------------------------------------------------ positive checks: a bad read is never "no"

READS = ["run", "jobs", "ci", "deploy", "boot", "smoke"]


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
    jobs = {"jobs": [_job(steps=[STEP], runner_id=3)]}
    r, calls = _run(tmp_path, _scenario(jobs=jobs), empty="jobs")
    _assert_error(r, calls)


@needs_tools
def test_an_empty_deploy_list_read_is_not_taken_as_nothing_covers_main(tmp_path):
    dep = {"workflow_runs": [_dep(4200, "in_progress", None, HEAD, DOCS)]}
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


def _wf_job() -> dict:
    jobs = _wf()["jobs"]
    assert "deploy-cancelled" in jobs, "incident.yml has no job `deploy-cancelled`"
    return jobs["deploy-cancelled"]


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s)).replace('"', "'")


def test_the_job_runs_only_for_a_cancelled_deploy_workflow_run():
    cond = _norm(_wf_job().get("if", ""))
    assert "github.event.workflow_run.name == 'Deploy'" in cond, cond
    assert "github.event.workflow_run.conclusion == 'cancelled'" in cond, cond


def test_the_job_permissions_are_contents_read_actions_write_and_nothing_broader():
    perms = _wf_job().get("permissions")
    assert isinstance(perms, dict), "the job needs its own permissions block"
    assert perms.get("contents") == "read"
    assert perms.get("actions") == "write"
    assert set(perms) <= {"contents", "actions", "issues"}, perms
    assert perms.get("issues", "write") == "write"


def test_the_job_checks_out_and_runs_the_script_with_run_id_repo_and_token():
    job = _wf_job()
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
    steps = _wf_job()["steps"]
    fail = [s for s in steps if "failure()" in str(s.get("if", ""))]
    assert fail, "needs a step with `if: failure()`"
    text = "\n".join(f"{s.get('run', '')}\n{s.get('uses', '')}" for s in fail).lower()
    assert "incident" in text, "the failure step must open or update the incident"
    assert "gh issue" in text or "factory" in text, text
    idx_run = next(i for i, s in enumerate(steps) if SCRIPT_REL in str(s.get("run", "")))
    assert steps.index(fail[0]) > idx_run, "the failure step comes after the script step"


def _failure_text() -> str:
    steps = _wf_job()["steps"]
    fail = [s for s in steps if "failure()" in str(s.get("if", ""))]
    assert fail, "needs a step with `if: failure()`"
    return "\n".join(str(s.get("run", "")) for s in fail)


def test_the_failure_step_creates_the_incident_with_the_three_factory_labels():
    text = _failure_text()
    assert "gh issue create" in text, text
    created = text[text.index("gh issue create"):]
    for label in ("type:incident", "priority:p0", "status:ready"):
        assert label in created, f"the created issue lacks the label {label}: {created}"
    assert "--label" in created, created


def test_the_failure_step_finds_an_open_incident_by_label_and_title():
    text = _failure_text()
    assert "gh issue list" in text, text
    listed = text[text.index("gh issue list"):]
    listed = listed.split("gh issue create")[0].split("gh issue comment")[0]
    assert re.search(r"--label[ =]['\"]?type:incident", listed), f"search by the type:incident label: {listed}"
    assert "--state open" in listed or "-s open" in listed, listed
    assert "title" in listed.lower(), f"search by the title too: {listed}"


def test_the_failure_step_names_the_run_it_reports():
    steps = _wf_job()["steps"]
    fail = next(s for s in steps if "failure()" in str(s.get("if", "")))
    blob = f"{fail.get('run', '')}\n{fail.get('env', '')}\n{fail.get('with', '')}"
    assert "html_url" in blob or "workflow_run.id" in blob or "RUN_ID" in blob


def test_deploy_yml_names_each_run_after_the_sha_it_deploys():
    yaml = pytest.importorskip("yaml")
    assert DEPLOY_WF.is_file()
    data = yaml.safe_load(DEPLOY_WF.read_text(encoding="utf-8"))
    name = re.sub(r"\s+", "", str(data.get("run-name", "")).strip().strip("'\""))
    assert name == "Deploy${{github.event.workflow_run.head_sha||inputs.sha||github.sha}}", data.get("run-name")


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
