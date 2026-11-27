# ruff: noqa: E501
"""Acceptance tests for issue #656 (D182, D186, D188): the nightly soak, slice (b) of #15.

INTERFACE this exam fixes for the implementer
=============================================

1. `labs/deploy/smoke/soak.py` (a sibling of lab_api.py, run as `python labs/deploy/smoke/soak.py`;
   it may import lab_api). It defines

     run_soak(api, host, *, sessions=30, clock=time.monotonic, sleep=time.sleep,
              wait_s=420.0, poll_s=10.0, tolerance=50_000_000, out=print, err=None) -> int

   and returns the exit code (0 ok, 1 failed). `out(line)` receives the one summary line, `err(line)`
   (default: stderr) receives one line per failure. Every wait is bounded by `wait_s` of `clock()`
   time and polls with `sleep(poll_s)`; nothing else sleeps. `main()` builds the real `api` and `host`
   from SMOKE_ID, SMOKE_PASSWORD, the public base URL and the smoke ssh config, and calls `run_soak`.

   `api` (the public API as the smoke student; ids are opaque strings):
     api.start(lab_id) -> sid            start a session; one live session per student, so they run
                                         one after the other (a second start while one is live is a bug)
     api.heartbeat(sid) -> None
     api.end(sid) -> None                the student's own end (end reason `student`)
     api.end_reason(sid) -> str | None   None while the session is live, else its end reason
                                         (`student`, `heartbeat`, `time_limit`, ...)
   `host` (read-only reads over the smoke ssh config):
     host.memory_available() -> int      bytes, the `available` column of `free -b`
     host.disk_available() -> int        bytes, the available column of `df -B1 -P /`
     host.lab_containers() -> list[str]  names of `lab-*` containers
     host.lab_volumes() -> list[str]     orphan lab volumes (`lf-` lab volumes of no live session)
   Memory and disk are read before the first session and after the last one (and after the waits).

   Plan of the run: 30 sessions in a row. One on `smoke-tasks` is left without any heartbeat until
   `end_reason` says `heartbeat`; one on the internal lab `smoke-soak` (1 minute limit) runs into its
   limit until `end_reason` says `time_limit`; the other 28 are started on `smoke-tasks` and ended
   with `api.end`, each checked to report `student`.

   The summary is exactly one `out` line, printed on success and on failure:
     soak summary: sessions=30 student=28 heartbeat=1 time_limit=1 memory_delta=<int> disk_delta=<int>
   (deltas in bytes, after minus before, signed; an end reason that did not occur is left out).

   Also, in the same module:
     parse_free_available(text) -> int   the `available` column of the `Mem:` row of `free -b`
     parse_df_available(text) -> int     the 4th column of the data row of `df -B1 -P /`

   Fails (returns 1, one `err` line each): a session that does not end with the expected reason (also
   when the bounded wait runs out), a `lab-*` container or an orphan lab volume left at the end,
   memory or disk more than `tolerance` bytes below the starting value.

2. `.github/workflows/server-smoke.yml` (D188): no new cron and no new `check` option (the #86 and
   #90 exams pin both). The soak runs in job `smoke` after the terminal check, sharing its
   smoke-student enable and its always-run disable, when the job env `SOAK` is true. `SOAK` is true
   on a scheduled run (`github.event_name == 'schedule'`, still only the 03:30 cron) and on a manual
   dispatch with the new boolean input `soak` (default false); a called run (Deploy,
   `workflow_call`, no `soak` input) never soaks. Exactly one step runs soak.py, bounded by
   `timeout`, gated by SOAK, after terminal.py and before the disable; the job timeout is at least
   30 minutes (terminal + soak). The isolation runner never runs the soak. Failed runs reach the
   Incident workflow, which already lists `Server smoke`.

3. `labs/content/soak/smoke-soak/lab.yaml` (D188 amendment 1; course folder `soak`; outside `labs/content/smoke/`, which #128 pins to one
   lab): `status: internal`, `time_limit_min: 1`, id `smoke-soak`, image `term`. The content loader
   (app.content.load_labs, unchanged layout `<root>/<course>/<lab>/lab.yaml`) serves it and
   refuses `time_limit_min` below 5 unless the status is `internal`.

Nothing here touches a server; the fake API and fake host below stand in for it.
"""
import importlib.util
import inspect
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
SMOKE_DIR = REPO_ROOT / "labs" / "deploy" / "smoke"
SOAK = SMOKE_DIR / "soak.py"
SMOKE_WF = REPO_ROOT / ".github" / "workflows" / "server-smoke.yml"
INCIDENT_WF = REPO_ROOT / ".github" / "workflows" / "incident.yml"
CONTENT = REPO_ROOT / "labs" / "content"
MB = 1_000_000

HB_TIMEOUT = 300.0  # the janitor's heartbeat timeout on the server
SWEEP = 30.0  # the janitor looks this much later (fake)
LIMITS = {"smoke-soak": 60.0, "smoke-tasks": 600.0}


# ---------------------------------------------------------------- the soak module and the fakes


def _soak():
    assert SOAK.is_file(), "labs/deploy/smoke/soak.py is missing"
    sys.path.insert(0, str(SMOKE_DIR))
    try:
        spec = importlib.util.spec_from_file_location("soak_under_test", SOAK)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(SMOKE_DIR))
    return mod


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.t0 = self.now

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        assert seconds >= 0
        self.now += seconds
        if self.now - self.t0 > 20_000:
            raise RuntimeError("unbounded wait: the soak slept past 20000 fake seconds")

    @property
    def elapsed(self):
        return self.now - self.t0


class FakeApi:
    def __init__(self, clock, *, hb_timeout=HB_TIMEOUT, limits=None, student_end_reason="student"):
        self.clock = clock
        self.hb_timeout = hb_timeout
        self.limits = dict(LIMITS if limits is None else limits)
        self.student_end_reason = student_end_reason
        self.sessions = {}
        self.order = []
        self.beats = {}
        self.overlap = False

    def _live_ids(self):
        return [s for s in self.sessions if self.end_reason(s) is None]

    @property
    def started(self):
        return len(self.order)

    def start(self, lab_id):
        if self._live_ids():
            self.overlap = True
            raise RuntimeError("a second session was started while one is live")
        self.clock.now += 2
        sid = f"sid-{len(self.order) + 1:02d}"
        self.sessions[sid] = {"lab": lab_id, "start": self.clock.now, "last": self.clock.now, "ended": None}
        self.order.append(sid)
        self.beats[sid] = 0
        return sid

    def heartbeat(self, sid):
        self.beats[sid] += 1
        if self.end_reason(sid) is None:
            self.sessions[sid]["last"] = self.clock.now

    def end(self, sid):
        self.clock.now += 1
        if self.end_reason(sid) is None:
            self.sessions[sid]["ended"] = self.student_end_reason

    def end_reason(self, sid):
        s = self.sessions[sid]
        if s["ended"] is None:
            if self.clock.now - s["last"] >= self.hb_timeout + SWEEP:
                s["ended"] = "heartbeat"
            elif self.clock.now - s["start"] >= self.limits[s["lab"]] + SWEEP:
                s["ended"] = "time_limit"
        return s["ended"]


class FakeHost:
    """Start values before the first session, end values after it."""

    def __init__(self, api, *, mem=(8_000 * MB, 8_000 * MB), disk=(50_000 * MB, 50_000 * MB),
                 containers=(), volumes=()):
        self.api, self.mem, self.disk = api, mem, disk
        self.containers, self.volumes = list(containers), list(volumes)

    def _phase(self):
        return 1 if self.api.started else 0

    def memory_available(self):
        return self.mem[self._phase()]

    def disk_available(self):
        return self.disk[self._phase()]

    def lab_containers(self):
        return self.containers if self._phase() else []

    def lab_volumes(self):
        return self.volumes if self._phase() else []


def _run(api=None, host=None, clock=None, **kw):
    mod = _soak()
    clock = clock or Clock()
    api = api or FakeApi(clock)
    host = host or FakeHost(api)
    out, err = [], []
    kw.setdefault("sessions", 30)
    kw.setdefault("wait_s", 400.0)
    kw.setdefault("poll_s", 5.0)
    rc = mod.run_soak(api, host, clock=clock, sleep=clock.sleep, out=out.append, err=err.append, **kw)
    return rc, out, err, api, clock


def _reasons(api):
    return {sid: api.end_reason(sid) for sid in api.order}


# ---------------------------------------------------------------- (a) the soak client, happy run


def test_ac1_default_run_is_30_sessions():
    mod = _soak()
    assert inspect.signature(mod.run_soak).parameters["sessions"].default == 30


def test_ac1_thirty_sessions_in_a_row_exit_zero():
    rc, out, err, api, _ = _run()
    assert rc == 0, f"exit {rc}, errors {err}"
    assert api.started == 30
    assert not api.overlap


def test_ac1_most_sessions_end_normally():
    _, _, _, api, _ = _run()
    reasons = list(_reasons(api).values())
    assert reasons.count("student") == 28


def test_ac1_one_session_is_left_without_heartbeats_until_the_janitor_ends_it():
    _, _, _, api, _ = _run()
    silent = [s for s, r in _reasons(api).items() if r == "heartbeat"]
    assert len(silent) >= 1
    assert all(api.beats[s] == 0 for s in silent), "a session meant to miss heartbeats got one"
    assert all(api.sessions[s]["lab"] == "smoke-tasks" for s in silent)


def test_ac1_one_session_runs_into_the_time_limit_of_the_internal_soak_lab():
    _, _, _, api, _ = _run()
    limited = [s for s, r in _reasons(api).items() if r == "time_limit"]
    assert len(limited) >= 1
    assert all(api.sessions[s]["lab"] == "smoke-soak" for s in limited)


def test_ac1_the_short_limit_lab_is_used_only_for_the_time_limit_session():
    _, _, _, api, _ = _run()
    on_soak = [s for s in api.order if api.sessions[s]["lab"] == "smoke-soak"]
    assert 1 <= len(on_soak) <= 2
    assert {api.sessions[s]["lab"] for s in api.order} == {"smoke-tasks", "smoke-soak"}


def test_ac1_sessions_never_overlap_because_a_student_has_one_live_session():
    _, _, _, api, _ = _run()
    assert not api.overlap
    assert not api._live_ids(), "a session is still live at the end"


def test_ac1_the_waits_are_bounded_in_clock_time():
    _, _, _, api, clock = _run()
    # one wait for the janitor (about 330 s), one for the time limit (about 90 s), 28 quick sessions
    assert clock.elapsed < 1200


def test_ac3_one_summary_line_with_sessions_end_reasons_and_deltas():
    api_clock = Clock()
    api = FakeApi(api_clock)
    host = FakeHost(api, mem=(8_000 * MB, 7_990 * MB), disk=(50_000 * MB, 50_010 * MB))
    rc, out, err, _, _ = _run(api=api, host=host, clock=api_clock)
    assert rc == 0, err
    assert len(out) == 1, f"exactly one summary line expected, got {out}"
    line = out[0]
    assert line.startswith("soak summary:")
    assert "\n" not in line
    assert re.search(r"\bsessions=30\b", line)
    assert re.search(r"\bstudent=28\b", line)
    assert re.search(r"\bheartbeat=1\b", line)
    assert re.search(r"\btime_limit=1\b", line)
    assert re.search(r"\bmemory_delta=-10000000\b", line)
    assert re.search(r"\bdisk_delta=10000000\b", line)


def test_ac2_memory_and_disk_are_read_before_and_after():
    mod = _soak()
    clock = Clock()
    api = FakeApi(clock)
    calls = []

    class Spy(FakeHost):
        def memory_available(self):
            calls.append(("mem", api.started))
            return super().memory_available()

        def disk_available(self):
            calls.append(("disk", api.started))
            return super().disk_available()

    host = Spy(api)
    mod.run_soak(api, host, sessions=30, clock=clock, sleep=clock.sleep, wait_s=400.0, poll_s=5.0,
                 out=lambda _: None, err=lambda _: None)
    for kind in ("mem", "disk"):
        before = [c for c in calls if c[0] == kind and c[1] == 0]
        after = [c for c in calls if c[0] == kind and c[1] == 30]
        assert before, f"{kind} is not read before the first session"
        assert after, f"{kind} is not read after the last session"


# ---------------------------------------------------------------- (a) the soak client, failures


def test_ac3_a_silent_session_that_the_janitor_never_ends_fails_within_the_wait():
    clock = Clock()
    api = FakeApi(clock, hb_timeout=10**9)
    rc, out, err, _, _ = _run(api=api, clock=clock)
    assert rc == 1
    assert err and any("heartbeat" in e for e in err)
    assert clock.elapsed < 3 * 400 + 600, "the wait for the heartbeat end is not bounded"
    assert len(out) == 1, "the summary line is printed on failure too"


def test_ac3_a_session_that_never_reaches_the_time_limit_fails_within_the_wait():
    clock = Clock()
    api = FakeApi(clock, limits={"smoke-soak": 10**9, "smoke-tasks": 600.0})
    rc, out, err, _, _ = _run(api=api, clock=clock)
    assert rc == 1
    assert any("time_limit" in e for e in err)
    assert clock.elapsed < 3 * 400 + 600
    assert len(out) == 1


def test_ac3_a_normal_session_that_ends_with_another_reason_fails():
    clock = Clock()
    api = FakeApi(clock, student_end_reason="forced")
    rc, _, err, _, _ = _run(api=api, clock=clock)
    assert rc == 1
    assert err


def test_ac3_a_leftover_lab_container_fails_and_is_named():
    clock = Clock()
    api = FakeApi(clock)
    host = FakeHost(api, containers=["lab-1234abcd"])
    rc, _, err, _, _ = _run(api=api, host=host, clock=clock)
    assert rc == 1
    assert any("lab-1234abcd" in e for e in err)


def test_ac3_an_orphan_lab_volume_fails_and_is_named():
    clock = Clock()
    api = FakeApi(clock)
    host = FakeHost(api, volumes=["lf-lab-vol-9"])
    rc, _, err, _, _ = _run(api=api, host=host, clock=clock)
    assert rc == 1
    assert any("lf-lab-vol-9" in e for e in err)


def test_ac2_memory_more_than_50_mb_below_the_start_fails():
    clock = Clock()
    api = FakeApi(clock)
    host = FakeHost(api, mem=(8_000 * MB, 8_000 * MB - 51 * MB))
    rc, out, err, _, _ = _run(api=api, host=host, clock=clock)
    assert rc == 1
    assert any("memory" in e.lower() for e in err)
    assert len(out) == 1 and re.search(r"memory_delta=-51000000\b", out[0])


def test_ac2_disk_more_than_50_mb_below_the_start_fails():
    clock = Clock()
    api = FakeApi(clock)
    host = FakeHost(api, disk=(50_000 * MB, 50_000 * MB - 51 * MB))
    rc, _, err, _, _ = _run(api=api, host=host, clock=clock)
    assert rc == 1
    assert any("disk" in e.lower() for e in err)


def test_ac2_a_drop_within_50_mb_passes():
    clock = Clock()
    api = FakeApi(clock)
    host = FakeHost(api, mem=(8_000 * MB, 8_000 * MB - 40 * MB), disk=(50_000 * MB, 50_000 * MB - 40 * MB))
    rc, _, err, _, _ = _run(api=api, host=host, clock=clock)
    assert rc == 0, err


def test_ac2_more_free_memory_and_disk_than_at_the_start_passes():
    clock = Clock()
    api = FakeApi(clock)
    host = FakeHost(api, mem=(8_000 * MB, 8_500 * MB), disk=(50_000 * MB, 52_000 * MB))
    rc, _, err, _, _ = _run(api=api, host=host, clock=clock)
    assert rc == 0, err


def test_ac2_the_tolerance_is_a_parameter():
    clock = Clock()
    api = FakeApi(clock)
    host = FakeHost(api, mem=(8_000 * MB, 8_000 * MB - 20 * MB))
    rc, _, _, _, _ = _run(api=api, host=host, clock=clock, tolerance=10 * MB)
    assert rc == 1


def test_ac2_free_output_is_parsed_to_the_available_bytes():
    mod = _soak()
    text = (
        "               total        used        free      shared  buff/cache   available\n"
        "Mem:      8344428544  2100000000  3000000000     1000000  3244428544  5900000000\n"
        "Swap:              0           0           0\n"
    )
    assert mod.parse_free_available(text) == 5_900_000_000


def test_ac2_df_output_is_parsed_to_the_available_bytes():
    mod = _soak()
    text = (
        "Filesystem      1-blocks        Used   Available Capacity Mounted on\n"
        "/dev/vda1    81000000000 30000000000 51000000000      38% /\n"
    )
    assert mod.parse_df_available(text) == 51_000_000_000


def test_ac3_the_module_has_a_main_for_the_workflow():
    assert callable(_soak().main)


# ---------------------------------------------------------------- (b) the workflow wiring


def _wf() -> dict:
    yaml = pytest.importorskip("yaml")
    assert SMOKE_WF.is_file()
    data = yaml.safe_load(SMOKE_WF.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _triggers(wf: dict) -> dict:
    return wf.get("on") or wf.get(True) or {}


def _job() -> dict:
    return _wf()["jobs"]["smoke"]


def _steps() -> list:
    return _job()["steps"]


def _runner_steps() -> list:
    return [s for s in _steps() if "soak.py" in str(s.get("run", ""))]


def _runner_index() -> int:
    steps = _runner_steps()
    assert steps, "no step runs soak.py"
    return _steps().index(steps[0])


def _run_all() -> str:
    return "\n".join(str(s.get("run", "")) for s in _steps())


def _step_with(pattern: str, *, skip: str | None = None) -> int:
    for i, s in enumerate(_steps()):
        run = str(s.get("run", ""))
        if re.search(pattern, run) and not (skip and re.search(skip, run)):
            return i
    raise AssertionError(f"no step runs something matching {pattern!r}")


def _soak_condition() -> str:
    """The step-level `if` of the soak step plus the shell text before soak.py runs.

    The soak may be its own step or the tail of the terminal step (the class password lives only
    in the shell of the step that enables the smoke student); either way SOAK must gate it.
    """
    step = _runner_steps()[0]
    run = str(step.get("run", ""))
    return str(step.get("if", "")) + "\n" + run[: run.index("soak.py")]


class _B(int):
    """A boolean input value that compares equal to both true and 'true'."""

    def __eq__(self, other):
        if isinstance(other, str):
            return other.lower() == ("true" if int(self) else "false")
        return int(self) == int(bool(other))

    def __ne__(self, other):
        return not self.__eq__(other)

    __hash__ = int.__hash__


def _eval_soak(expr: str, event: str, *, soak=None, from_deploy=None):
    from types import SimpleNamespace as NS

    src = expr.strip()
    if src.startswith("${{") and src.endswith("}}"):
        src = src[3:-2].strip()
    src = src.replace("!=", "\x00").replace("!", " not ").replace("\x00", "!=")
    src = src.replace("||", " or ").replace("&&", " and ")
    src = re.sub(r"\btrue\b", "True", src)
    src = re.sub(r"\bfalse\b", "False", src)
    src = re.sub(r"\bnull\b", "None", src)
    ns = {
        "github": NS(event_name=event),
        "inputs": NS(
            soak=None if soak is None else _B(soak),
            from_deploy=None if from_deploy is None else _B(from_deploy),
            check=None,
        ),
    }
    try:
        value = eval(src, {"__builtins__": {}}, ns)  # noqa: S307
    except Exception:  # an expression shape this mini evaluator cannot read
        return NotImplemented
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


def test_ac1_dispatch_has_a_boolean_soak_input_defaulting_to_false():
    inputs = _triggers(_wf())["workflow_dispatch"]["inputs"]
    assert "soak" in inputs, "workflow_dispatch needs the input `soak`"
    assert inputs["soak"]["type"] == "boolean"
    assert inputs["soak"]["default"] is False


def test_ac1_the_soak_is_not_a_check_option_and_the_schedule_is_still_only_0330():
    # D188: no new `check` option and no new cron (the older #86/#90 exams pin both)
    trig = _triggers(_wf())
    assert "soak" not in trig["workflow_dispatch"]["inputs"]["check"]["options"]
    assert [c["cron"] for c in trig["schedule"]] == ["30 3 * * *"]


def test_ac1_a_called_run_cannot_turn_the_soak_on():
    call = _triggers(_wf())["workflow_call"]
    assert "soak" not in (call.get("inputs") or {}), "Deploy must not be able to ask for a soak"


def test_ac1_the_job_env_has_a_soak_switch_that_names_the_schedule_and_the_input():
    expr = str(_job()["env"]["SOAK"])
    assert "github.event_name == 'schedule'" in expr
    assert "inputs.soak" in expr


@pytest.mark.parametrize(
    "event,soak,from_deploy,expected",
    [
        ("schedule", None, None, True),
        ("workflow_dispatch", True, None, True),
        ("workflow_dispatch", False, None, False),
        ("workflow_dispatch", None, None, False),
        ("workflow_run", None, True, False),
        ("workflow_run", None, None, False),
    ],
)
def test_ac1_soak_switch_values(event, soak, from_deploy, expected):
    value = _eval_soak(str(_job()["env"]["SOAK"]), event, soak=soak, from_deploy=from_deploy)
    if value is NotImplemented:
        pytest.skip("SOAK expression shape not readable by the mini evaluator (structure cases cover it)")
    assert value is expected, f"SOAK for {event} soak={soak} from_deploy={from_deploy}"


def test_ac1_the_check_selector_still_comes_from_inputs_check_with_a_terminal_fallback():
    expr = str(_job()["env"]["CHECK"])
    assert "inputs.check" in expr and "'terminal'" in expr


def test_ac1_the_soak_runs_in_the_same_group_with_the_same_gate():
    job = _job()
    assert "server-lab-host" in str(job["concurrency"]["group"])
    assert job["concurrency"]["cancel-in-progress"] is False
    assert job["needs"] == "gate"
    assert "deploy_busy" in str(job["if"])


def test_ac1_the_job_timeout_covers_the_terminal_check_and_the_soak():
    assert _job()["timeout-minutes"] >= 30


def test_ac1_the_client_is_started_and_bounded_by_timeout():
    steps = _runner_steps()
    assert len(steps) == 1, "exactly one step runs soak.py"
    run = str(steps[0]["run"])
    assert re.search(r"\btimeout\b[^\n]*soak\.py", run)


def test_ac1_the_soak_client_only_runs_when_soak_is_true():
    assert re.search(r"\bSOAK\b", _soak_condition()), "the soak client must be gated by SOAK"


def test_ac1_the_soak_client_runs_only_under_the_terminal_check_that_enables_the_student():
    step = _runner_steps()[0]
    enable = _step_with(r"app\.cli smoke-student", skip=r"--disable")
    same_step = _steps().index(step) == enable
    assert same_step or "env.CHECK == 'terminal'" in str(step.get("if", "")), (
        "a separate soak step must be gated on the terminal check, whose enable it shares"
    )


def test_ac1_the_soak_comes_after_the_terminal_client_and_before_the_disable():
    steps = _steps()
    idx = _runner_index()
    terminal = _step_with(r"terminal\.py")
    disable = _step_with(r"smoke-student\s+--disable")
    assert terminal <= idx < disable
    if terminal == idx:
        run = str(steps[idx]["run"])
        assert run.index("terminal.py") < run.index("soak.py")


def test_ac1_the_soak_shares_the_one_smoke_student_enable_and_the_one_disable():
    enable = [
        ln for ln in _run_all().splitlines() if "smoke-student" in ln and "--disable" not in ln
    ]
    assert len(enable) == 1, f"the soak shares the terminal check's enable, found {enable}"
    disable = [s for s in _steps() if "smoke-student --disable" in str(s.get("run", ""))]
    assert len(disable) == 1


def test_ac1_the_smoke_student_is_enabled_whenever_the_soak_runs():
    # the soak runs only under the terminal check; the enable step must run for that check
    idx = _step_with(r"app\.cli smoke-student", skip=r"--disable")
    cond = str(_steps()[idx].get("if", ""))
    assert "env.CHECK == 'terminal'" in cond or "SOAK" in cond


def test_ac1_the_soak_runs_as_the_smoke_student_and_the_password_is_masked():
    run_all = _run_all()
    assert "app.cli smoke-student" in run_all
    assert "::add-mask::" in run_all
    assert "SMOKE_ID" in run_all and "SMOKE_PASSWORD" in run_all
    assert 'echo "$class_pw"' not in run_all and "echo $class_pw" not in run_all
    line = next(ln for ln in str(_runner_steps()[0]["run"]).splitlines() if "soak.py" in ln)
    after = line.split("soak.py", 1)[1]
    assert "SMOKE_PASSWORD" not in after and "SMOKE_ID" not in after, "credentials are never arguments"


def test_ac1_the_smoke_student_is_disabled_again_after_a_soak():
    disable = [s for s in _steps() if "smoke-student --disable" in str(s.get("run", ""))]
    assert disable
    cond = " ".join(str(s.get("if", "")) for s in disable)
    assert "always()" in cond
    assert "env.CHECK == 'terminal'" in cond or "SOAK" in cond


def test_ac1_the_isolation_runner_never_runs_the_soak():
    step = next(s for s in _steps() if "isolation.sh" in str(s.get("run", "")))
    assert "soak" not in str(step.get("run", "")).lower()
    assert "env.CHECK != 'terminal'" in str(step.get("if", "")), "the isolation step must stay off the terminal check"


def test_ac2_free_and_df_are_read_with_the_exact_commands():
    text = _run_all()
    if SOAK.is_file():
        text += SOAK.read_text(encoding="utf-8")
    assert "free -b" in text
    assert "df -B1 -P /" in text


def test_ac2_the_soak_reads_over_the_smoke_ssh_config_alias():
    text = "\n".join(str(s.get("run", "")) + str(s.get("env", "")) for s in _runner_steps())
    assert "ssh_config" in text


_WRITE = re.compile(
    r"\bdocker\s+(rm|kill|stop|restart|pause|prune|rmi|run|start|create|update|cp)\b"
    r"|\bdocker\s+(container|system|network|volume|image)\s+(rm|kill|stop|prune|remove|create|run|start)\b"
    r"|\bip6?tables\b[^\n]*\s(-[AIDFXNPRZ]|--append|--insert|--delete|--flush)\b"
    r"|\bsystemctl\b|\bservice\s+\w+\s+(restart|stop)|\breboot\b|\bshutdown\b|\bkill(all)?\b|\bpkill\b"
    r"|\bdrop_caches\b"
)


def test_ac3_the_soak_sends_no_write_command_to_the_server():
    texts = [str(s.get("run", "")) for s in _runner_steps()]
    if SOAK.is_file():
        texts.append(SOAK.read_text(encoding="utf-8"))
    assert _runner_steps(), "no soak client step found"
    for t in texts:
        m = _WRITE.search(t)
        assert m is None, f"write command for the server: {m.group(0) if m else ''}"


def test_ac3_the_soak_step_runs_inside_the_ssh_and_key_lifecycle():
    steps = _steps()
    names = [str(s.get("name", "")) for s in steps]
    cfg = next(i for i, n in enumerate(names) if n.startswith("Configure SSH"))
    key = next(i for i, n in enumerate(names) if n.startswith("Remove the SSH key"))
    assert cfg < _runner_index() < key


def test_ac3_a_failed_scheduled_run_opens_an_incident_through_the_existing_workflow():
    yaml = pytest.importorskip("yaml")
    inc = yaml.safe_load(INCIDENT_WF.read_text(encoding="utf-8"))
    trig = inc.get("on") or inc.get(True)
    assert "Server smoke" in trig["workflow_run"]["workflows"]
    assert _wf()["name"] == "Server smoke"
    spec = importlib.util.spec_from_file_location("incident_gate", REPO_ROOT / "scripts" / "incident_gate.py")
    assert spec and spec.loader
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    assert gate.decide("Server smoke", "schedule", "failure") == "raise"


def test_ac3_a_failing_client_fails_the_job():
    steps = _runner_steps()
    assert steps, "no soak client step found"
    assert "continue-on-error" not in steps[0]
    line = next(ln for ln in str(steps[0]["run"]).splitlines() if "soak.py" in ln)
    assert "|| true" not in line and "||true" not in line


# ---------------------------------------------------------------- (c) AC4: the short limit is internal only


def _lab_yaml(lab_id: str, status: str, limit: int) -> str:
    return (
        f"id: {lab_id}\ntitle:\n  en: T\n  he: ט\ndescription:\n  en: D\n  he: ד\n"
        f"image: term\ntime_limit_min: {limit}\ninternet: false\nallow_domains: []\nsudo: false\n"
        f"status: {status}\nmodule: 1\nlesson: 5\ntasks:\n  - id: q1\n    kind: quiz\n"
        "    question:\n      en: Q\n      he: ש\n    options:\n      - en: A\n        he: א\n"
        "      - en: B\n        he: ב\n    correct: 0\n"
    )


def _load(tmp_path: Path, status: str, limit: int):
    from app.content import load_labs

    d = tmp_path / "grp" / "short-lab"
    d.mkdir(parents=True)
    (d / "lab.yaml").write_text(_lab_yaml("short-lab", status, limit), encoding="utf-8")
    return load_labs(tmp_path)


@pytest.mark.parametrize("status", ["published", "draft"])
@pytest.mark.parametrize("limit", [1, 4])
def test_ac4_the_loader_refuses_a_short_limit_for_a_student_lab(tmp_path, status, limit):
    res = _load(tmp_path, status, limit)
    assert "short-lab" not in res.labs
    errs = res.errors.get("grp/short-lab", [])
    assert any("time_limit_min" in e for e in errs), errs


@pytest.mark.parametrize("status", ["published", "draft"])
def test_ac4_the_loader_accepts_5_minutes_for_a_student_lab(tmp_path, status):
    res = _load(tmp_path, status, 5)
    assert not res.errors, res.errors
    assert res.labs["short-lab"].time_limit_min == 5


@pytest.mark.parametrize("limit", [1, 2, 4])
def test_ac4_the_loader_accepts_a_short_limit_for_an_internal_lab(tmp_path, limit):
    res = _load(tmp_path, "internal", limit)
    assert not res.errors, res.errors
    assert res.labs["short-lab"].time_limit_min == limit


def test_ac4_the_smoke_soak_lab_exists_internal_with_a_one_minute_limit():
    from app.content import load_labs

    res = load_labs(CONTENT)
    assert "smoke-soak" in res.labs, f"smoke-soak is not loaded: {res.errors}"
    lab = res.labs["smoke-soak"]
    assert lab.status == "internal"
    assert lab.time_limit_min == 1
    assert lab.image == "term"
    assert (CONTENT / "soak" / "smoke-soak" / "lab.yaml").is_file()
    assert not (CONTENT / "smoke" / "smoke-soak").exists(), "the lab stays out of labs/content/smoke/ (#128)"


def test_ac4_the_regular_smoke_lab_keeps_its_ten_minutes():
    from app.content import load_labs

    assert load_labs(CONTENT).labs["smoke-tasks"].time_limit_min == 10


def test_ac4_every_lab_that_is_not_internal_keeps_at_least_five_minutes():
    from app.content import load_labs

    res = load_labs(CONTENT)
    assert not res.errors, res.errors
    short = [lab.id for lab in res.labs.values() if lab.status != "internal" and lab.time_limit_min < 5]
    assert not short, short
