"""Acceptance tests for issue #151: follow-ups of the bounded kill of a timed-out script (#117).

No Docker here (the real thing under runsc is test_issue_151_kill_followups_docker.py). The
runner part drives `DockerRuntime.exec_script` against the fake Docker Engine of
test_issue_9_runner.py, which got four things for this issue: the kill exec is recognised by
its command (`is_kill_exec`), the plan key `"stream"` (`"ends_early"`: the output stream ends
while the script lives on; `"reset"`: the connection is reset), `kill_effective = False` (a kill
that reaches nothing) and `json_fails_until_kill` (exec inspect answers HTTP 500, not JSON,
until a kill exec has started).

Criteria covered (the numbers are the issue's):

1. Kill on early failures too. Once the exec id is known, ANY failure of the exec (stream ends
   early, connection reset, inspect fails) runs the same bounded kill (a second exec of the
   same kind as after a timeout) before `exec_script` returns or raises. So the script has
   stopped when the session lock (held around `exec_script`) is released. The call still ends
   as a failure (it raises, or reports `timed_out`): never as a success. No exec id (the create
   was refused) means no kill.
2. The worst case of one setup request stays strictly under nginx's `proxy_read_timeout` for
   the manager API location, read from `labs/deploy/nginx/labs.conf` (the location that serves
   `/api/...`, falling back to the server level and to nginx's default of 60 s).
   `SLOT_WAIT_S + <largest setup budget> + KILL_BOUND_S + DB_STEP_BOUND_S` and the request
   deadline are checked against it. The kill really is bounded by `runtime.KILL_BOUND_S`.
3. "Kill left it alive" and "kill hung" are different ERROR lines of the logger `lab-manager`
   and both are produced inside the kill bound (the still-running poll fits in it):
       ALIVE_LOG = "kill left the script running"
       HUNG_LOG = "kill of the script hung"
   (substrings of the record message). A kill that fails otherwise (the engine refuses the
   kill exec) logs neither text, a successful kill logs no error at all. Each case: exactly
   one ERROR record that mentions "script".
4. The kill program reads and signals real ids: `ruid=`/`rgid=` in the `ps -o` list, no bare
   effective `uid=`/`gid=`.
5. The image requirement is written down in the spec (sections 6 and 7 name `procps`,
   `setpriv` and `awk`); the lab image test is in the docker file.
6. Validator rules (in this file, the 117 exam keeps its older cases): warn on `while read`,
   `x=$(cat)` and `su student -c 'read x'`; no warning for a background job whose stdout and
   stderr go to a log file, nor for a trailing comment that ends in `&`.
"""
# ruff: noqa: F811
import asyncio
import inspect
import logging
import re
import time
from pathlib import Path

import pytest
from test_issue_9_runner import (  # noqa: F401  (engine is a fixture, used by name)
    FakeEngine,
    engine,
    runtime,
)
from test_issue_117_kill_timeouts import (
    _killed_by_second_exec,
    _KillFailsEngine,
    background_warnings,
    lab_with_script,
    stdin_warnings,
)

ALIVE_LOG = "kill left the script running"
HUNG_LOG = "kill of the script hung"
REPO_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "labs").is_dir())
LABS_CONF = REPO_ROOT / "labs" / "deploy" / "nginx" / "labs.conf"
SPEC = REPO_ROOT / "docs" / "superpowers" / "specs" / "2026-11-14-lab-platform-design.md"
DECISIONS = REPO_ROOT / "docs" / "factory" / "DECISIONS.md"
API_PATH = "/api/sessions/00000000-0000-0000-0000-000000000000/tasks/01-x/show"
SLEEPER = "#!/bin/sh\nexec >/dev/null 2>&1\nsleep 600\n"


def _outcome(engine, script=SLEEPER, timeout=8.0):
    """(result or None, exception or None, seconds, exec ids finished when it returned)."""
    rt = runtime()
    started = time.monotonic()
    res = exc = None
    try:
        res = rt.exec_script("lab-exam-1", script, timeout)
        if inspect.isawaitable(res):
            res = asyncio.run(_await(res))
    except Exception as err:  # noqa: BLE001 - a failure is one of the accepted outcomes
        exc = err
    return res, exc, time.monotonic() - started, set(engine.finished)


async def _await(aw):
    return await aw


def _ended_as_a_failure(res, exc) -> bool:
    if exc is not None:
        return True
    return res is not None and (res.timed_out or res.exit_code != 0)


def _kill_ran_and_script_stopped(engine, res, exc, finished) -> None:
    assert _ended_as_a_failure(res, exc), f"a script that never ended was reported as {res}"
    assert len(engine.kill_runs) == 1, f"kill execs: {len(engine.kill_runs)}"
    assert len(engine.creates) == 2, "the kill is one more exec in the same container"
    assert "exec1" in finished, "exec_script gave up (lock released) with the script alive"
    body, _ = engine.kill_runs[0]
    assert body["User"] in ("root", "0", "0:0") and not body.get("Privileged")


def _error_lines(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == "lab-manager" and r.levelno >= logging.ERROR
    ]


# ============================================================ 1. kill on early failures


def test_kill_runs_when_the_output_stream_ends_before_the_script_does(engine):
    """`exec >/dev/null 2>&1` then a blocking command: Docker's stream ends, the script lives."""
    engine.behaviour = lambda b, s: {"stream": "ends_early"}
    res, exc, took, finished = _outcome(engine, timeout=8.0)
    _kill_ran_and_script_stopped(engine, res, exc, finished)
    assert took < 25


def test_kill_runs_when_the_exec_connection_is_reset(engine):
    engine.behaviour = lambda b, s: {"stream": "reset"}
    res, exc, took, finished = _outcome(engine, timeout=8.0)
    _kill_ran_and_script_stopped(engine, res, exc, finished)
    assert took < 25


def test_kill_runs_when_the_exec_inspect_fails_after_the_stream_ended(engine):
    engine.json_fails_until_kill = True
    engine.behaviour = lambda b, s: {"stream": "ends_early"}
    res, exc, took, finished = _outcome(engine, timeout=30.0)
    _kill_ran_and_script_stopped(engine, res, exc, finished)
    assert took < 10, "the failure came at once, long before the 30 s deadline"


def test_an_early_failure_with_a_hanging_kill_still_returns_within_the_kill_bound(engine):
    from app import runtime as rt

    engine.json_fails_until_kill = True
    engine.kill_delay = 600.0
    engine.behaviour = lambda b, s: {"stream": "ends_early"}
    res, exc, took, finished = _outcome(engine, timeout=30.0)
    assert _ended_as_a_failure(res, exc)
    assert len(engine.kill_runs) == 1, "the kill must be tried on this failure path too"
    assert took < rt.KILL_BOUND_S + 1.5, f"an unbounded kill held the session lock: {took:.1f}s"


def test_the_kill_of_an_early_failure_is_the_same_kill_as_after_a_timeout(engine):
    engine.json_fails_until_kill = True
    engine.behaviour = lambda b, s: {"stream": "ends_early"}
    _outcome(engine, timeout=30.0)
    assert _killed_by_second_exec(engine)  # SIGKILL, root, unprivileged, no capability field


def test_no_exec_id_means_no_kill(engine):
    engine.create_status = 500
    res, exc, _took, _finished = _outcome(engine, timeout=5.0)
    assert exc is not None, "a refused exec create must raise"
    assert engine.kill_runs == [] and engine.creates == []
    assert engine.paths().count("POST /containers/lab-exam-1/exec") == 1


def test_a_script_that_ends_normally_is_not_killed(engine):
    engine.behaviour = lambda b, s: {"exit": 3, "frames": []}
    res, exc, _took, _finished = _outcome(engine, "#!/bin/sh\nexit 3\n", timeout=5.0)
    assert exc is None and res.exit_code == 3 and res.timed_out is False
    assert engine.kill_runs == []


# ============================================================ 2. nginx bound


def _blocks(text: str) -> list[tuple[str, str, str]]:
    """(header, body, whole) of every `location ... {}` in an nginx config, braces matched."""
    found = []
    for m in re.finditer(r"location\s+([^{;]+?)\s*\{", text):
        depth, i = 1, m.end()
        while depth and i < len(text):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        found.append((m.group(1).strip(), text[m.end() : i - 1], text[m.start() : i]))
    return found


def _seconds(value: str) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m|h|d)?", value.strip())
    assert m, f"unreadable nginx time {value!r}"
    unit = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2) or "s"]
    return float(m.group(1)) * unit


def nginx_read_timeout_s(conf: str, path: str) -> float:
    """proxy_read_timeout that applies to `path`: the best-matching location, else the server
    level, else nginx's default of 60 s. Regex locations are not used by labs.conf (not read)."""
    conf = re.sub(r"(?m)#.*$", "", conf)
    best, best_len = None, -1
    for header, body, _whole in _blocks(conf):
        parts = header.split()
        modifier = parts[0] if len(parts) == 2 else ""
        uri = parts[-1]
        if modifier == "=":
            matches, rank = path == uri, 10_000
        elif modifier in ("", "^~"):
            matches, rank = path.startswith(uri), len(uri)
        else:
            continue
        if matches and rank > best_len:
            best, best_len = body, rank
    for scope in (best, _strip_locations(conf)):
        if scope is None:
            continue
        found = re.search(r"(?<![\w-])proxy_read_timeout\s+([^;\s]+)\s*;", scope)
        if found:
            return _seconds(found.group(1))
    return 60.0


def _strip_locations(conf: str) -> str:
    for _h, _b, whole in _blocks(conf):
        conf = conf.replace(whole, "")
    return conf


def test_the_nginx_reader_picks_the_location_that_serves_the_path():
    conf = """
    server {
        proxy_read_timeout 45s;
        location / { proxy_pass http://x; }
        location /api/ { proxy_pass http://x; proxy_read_timeout 2m; }
        location /s/ { proxy_pass http://x; proxy_read_timeout 3600s; }
        location = /api/exact { proxy_read_timeout 7s; }
    }"""
    assert nginx_read_timeout_s(conf, "/api/sessions/a") == 120
    assert nginx_read_timeout_s(conf, "/s/a/term/") == 3600
    assert nginx_read_timeout_s(conf, "/login") == 45
    assert nginx_read_timeout_s(conf, "/api/exact") == 7
    assert nginx_read_timeout_s("server { location / { proxy_pass http://x; } }", "/api/x") == 60


def test_labs_conf_gives_the_api_the_default_or_a_set_read_timeout():
    seconds = nginx_read_timeout_s(LABS_CONF.read_text(encoding="utf-8"), API_PATH)
    assert 0 < seconds <= 3600, seconds
    assert seconds != 3600, "the 3600 s of the terminal location must not be the API's"


def test_worst_case_slot_wait_setup_kill_and_database_stays_under_the_nginx_read_timeout(
    monkeypatch,
):
    from app import runtime as rt
    from app import tasks

    limit = nginx_read_timeout_s(LABS_CONF.read_text(encoding="utf-8"), API_PATH)
    monkeypatch.setenv("LF_SETUP_TIMEOUT_S", "1e9")  # the largest setup an operator can ask for
    worst = tasks.SLOT_WAIT_S + tasks.setup_timeout() + rt.KILL_BOUND_S + tasks.DB_STEP_BOUND_S
    assert worst < limit, f"{worst} s of work behind a {limit} s nginx read timeout"
    monkeypatch.delenv("LF_SETUP_TIMEOUT_S")
    default = tasks.SLOT_WAIT_S + tasks.setup_timeout() + rt.KILL_BOUND_S + tasks.DB_STEP_BOUND_S
    assert default < limit, default


def test_the_request_deadline_and_the_pool_wait_are_under_the_nginx_read_timeout():
    from app import db

    limit = nginx_read_timeout_s(LABS_CONF.read_text(encoding="utf-8"), API_PATH)
    assert db.REQUEST_DEADLINE_S < limit
    assert db.POOL_ACQUIRE_TIMEOUT < limit


def test_the_check_path_worst_case_is_under_the_nginx_read_timeout():
    from app import runtime as rt
    from app import tasks

    limit = nginx_read_timeout_s(LABS_CONF.read_text(encoding="utf-8"), API_PATH)
    worst = tasks.SLOT_WAIT_S + tasks.CHECK_TIMEOUT_S + rt.KILL_BOUND_S + tasks.DB_STEP_BOUND_S
    assert worst < limit, worst


def test_the_kill_bound_is_the_real_bound_of_a_kill_that_never_answers(engine):
    """A big timeout (30 s) has the largest kill budget; it is still `KILL_BOUND_S`."""
    from app import runtime as rt

    engine.kill_delay = 600.0
    engine.json_fails_until_kill = True
    engine.behaviour = lambda b, s: {"stream": "ends_early"}
    _res, _exc, took, _finished = _outcome(engine, timeout=30.0)
    assert len(engine.kill_runs) == 1
    assert rt.KILL_BOUND_S - 0.5 <= took < rt.KILL_BOUND_S + 1.5, f"{took:.2f}s"


def test_d56_states_the_real_owner_ids():
    text = DECISIONS.read_text(encoding="utf-8")
    assert "ruid=" in text and "rgid=" in text, "the decision record still describes `uid=`"


# ============================================================ 3. the error paths of the kill


def _timed_out(engine, timeout=1.0):
    engine.behaviour = lambda b, s: {"exit": 0, "frames": [], "delay": 600.0}
    return _outcome(engine, "#!/bin/sh\nsleep 600\n", timeout)


def test_a_kill_that_left_the_script_alive_logs_its_own_line_inside_the_kill_bound(
    engine, caplog
):
    """timeout 1 s gives a 3 s kill bound (D56: max(3, ...)); the 10 s poll cannot fit that."""
    engine.kill_effective = False
    with caplog.at_level(logging.ERROR, logger="lab-manager"):
        res, exc, took, _finished = _timed_out(engine)
    assert exc is None and res.timed_out is True
    assert took < 1.0 + 3.0 + 1.5, f"{took:.1f}s: the still-running poll outlasts the kill bound"
    lines = _error_lines(caplog)
    assert [ln for ln in lines if ALIVE_LOG in ln], lines
    assert not [ln for ln in lines if HUNG_LOG in ln], lines
    assert len([ln for ln in lines if "script" in ln]) == 1, lines


def test_a_kill_that_hung_logs_its_own_line_inside_the_kill_bound(engine, caplog):
    engine.kill_delay = 600.0
    with caplog.at_level(logging.ERROR, logger="lab-manager"):
        res, exc, took, _finished = _timed_out(engine)
    assert exc is None and res.timed_out is True
    assert took < 1.0 + 3.0 + 1.5, f"{took:.1f}s"
    lines = _error_lines(caplog)
    assert [ln for ln in lines if HUNG_LOG in ln], lines
    assert not [ln for ln in lines if ALIVE_LOG in ln], lines
    assert len([ln for ln in lines if "script" in ln]) == 1, lines


def test_a_kill_the_engine_refuses_logs_neither_the_alive_nor_the_hung_line(
    monkeypatch, caplog
):
    eng = _KillFailsEngine()
    eng.start()
    monkeypatch.setenv("LF_DOCKER_HOST", f"tcp://127.0.0.1:{eng.port}")
    try:
        with caplog.at_level(logging.ERROR, logger="lab-manager"):
            res, exc, _took, _finished = _timed_out(eng)
    finally:
        eng.stop()
    assert exc is None and res.timed_out is True
    lines = _error_lines(caplog)
    assert not [ln for ln in lines if ALIVE_LOG in ln or HUNG_LOG in ln], lines
    assert len([ln for ln in lines if "script" in ln]) == 1, lines


def test_a_kill_that_works_logs_no_error(engine, caplog):
    with caplog.at_level(logging.ERROR, logger="lab-manager"):
        res, exc, _took, finished = _timed_out(engine)
    assert exc is None and res.timed_out is True and "exec1" in finished
    assert _error_lines(caplog) == []


def test_the_failure_path_kill_that_left_the_script_alive_is_logged_as_alive(engine, caplog):
    engine.json_fails_until_kill = True
    engine.kill_effective = False
    engine.behaviour = lambda b, s: {"stream": "ends_early"}
    with caplog.at_level(logging.ERROR, logger="lab-manager"):
        res, exc, took, _finished = _outcome(engine, timeout=2.0)
    assert _ended_as_a_failure(res, exc)
    assert took < 3.0 + 1.5, f"{took:.1f}s"
    assert [ln for ln in _error_lines(caplog) if ALIVE_LOG in ln], _error_lines(caplog)


# ============================================================ 4. real owner


def test_the_kill_program_reads_and_signals_the_real_ids(engine):
    _timed_out(engine)
    body, _stdin = engine.kill_runs[0]
    program = " ".join(body["Cmd"])
    assert "ruid=" in program and "rgid=" in program, program
    assert not re.search(r"(?<![re])uid=", program), "effective `uid=` in the kill program"
    assert not re.search(r"(?<![re])gid=", program), "effective `gid=` in the kill program"
    assert "setpriv" in program and "kill" in program


# ============================================================ 5. written down


def _spec_sections_6_and_7() -> str:
    text = SPEC.read_text(encoding="utf-8")
    start = text.index("## 6. Isolation and limits")
    end = text.index("## 8. Data model")
    return text[start:end]


@pytest.mark.parametrize("tool", ["procps", "setpriv", "awk"])
def test_the_spec_names_the_tools_the_kill_needs_in_the_lab_image(tool):
    section = _spec_sections_6_and_7()
    assert re.search(rf"(?<![\w-]){tool}(?![\w-])", section), f"spec 6/7 never names {tool}"


def test_the_spec_ties_those_tools_to_the_kill_of_a_timed_out_script():
    section = _spec_sections_6_and_7()
    found = re.search(r"[^\n]*setpriv[^\n]*", section)
    assert found and re.search(r"kill|timeout", found.group(0), re.I), found


def test_the_lab_dockerfile_installs_procps_and_util_linux():
    text = (REPO_ROOT / "labs" / "images" / "term" / "Dockerfile").read_text(encoding="utf-8")
    for pkg in ("procps", "util-linux"):
        assert re.search(rf"(?<![\w-]){pkg}(?![\w-])", text), pkg


# ============================================================ 6. validator

WHILE_READ = [
    "while read line; do echo $line; done",
    "while read -r a b; do echo $a; done",
    "while IFS= read -r line; do echo $line; done",
    "while read line\ndo\n  echo $line\ndone",
    "true && while read line; do echo $line; done",
]
SUBSHELL_CAT = ["x=$(cat)", 'x="$(cat)"', "x=$(cat -)", "x=`cat`"]
SU_READ = [
    "su student -c 'read x'",
    'su student -c "read x"',
    "su student -s /bin/sh -c 'read -r x'",
    "su - student -c 'cat'",
]


@pytest.mark.parametrize("name", ["setup.sh", "check.sh"])
@pytest.mark.parametrize("line", WHILE_READ + SUBSHELL_CAT + SU_READ)
def test_the_new_stdin_readers_are_a_warning(tmp_path, name, line):
    result = lab_with_script(tmp_path, f"#!/bin/sh\n{line}\nexit 0\n", name)
    found = stdin_warnings(result)
    assert found, f"no </dev/null warning for {line!r}: {result.warnings}"
    assert any(name in w and "02-make-file" in w for w in found), found


STDIN_SAFE_NEW = [
    "cat /etc/hostname | while read l; do echo $l; done",
    "ls | while IFS= read -r l; do echo $l; done",
    "while read l; do echo $l; done < /etc/hostname",
    "while read l; do echo $l; done </dev/null",
    "while read l\ndo\n  echo $l\ndone < /etc/hostname",
    "while read l\ndo\n  echo $l\ndone </dev/null",
    "x=$(cat /etc/hostname)",
    "x=$(cat </dev/null)",
    "x=$(cat < /etc/hostname)",
    "x=$(echo hi | cat)",
    "x=$(ls | while read l; do echo $l; done)",
    "su student -c 'echo hi'",
    "su student -c 'cat /etc/hostname'",
    "su student -c 'read x' </dev/null",
    "su student -c 'read x </dev/null'",
    "su student -s /bin/sh -c 'rm -f /home/student/ok' </dev/null",
    "su student -s /bin/sh -c '/bin/sh -s' <<'READER'\necho hi\nREADER",
    "echo while read",
    "# while read line; do :; done",
    "# x=$(cat)",
    "# su student -c 'read x'",
]


@pytest.mark.parametrize("name", ["setup.sh", "check.sh"])
@pytest.mark.parametrize("line", STDIN_SAFE_NEW)
def test_commands_that_do_not_read_the_script_get_no_new_stdin_warning(tmp_path, name, line):
    result = lab_with_script(tmp_path, f"#!/bin/sh\n{line}\nexit 0\n", name)
    assert stdin_warnings(result) == [], f"false positive for {line!r}: {result.warnings}"


BACKGROUND_LOGGED = [
    "sleep 600 >/tmp/log 2>&1 &",
    "sleep 600 > /var/log/lf/app.log 2>&1 &",
    "sleep 600 >>/tmp/log 2>&1 &",
    "sleep 600 >/tmp/out 2>/tmp/err &",
    "python3 -m http.server 8080 </dev/null >/tmp/http.log 2>&1 &",
]
BACKGROUND_COMMENTED = [
    "echo done  # wait for it &",
    "true # not a job &",
    "echo hi   # nohup foo &",
    "echo hi # &",
    "sleep 600 >/dev/null 2>&1 &  # keep it quiet &",
]


@pytest.mark.parametrize("name", ["setup.sh", "check.sh"])
@pytest.mark.parametrize("line", BACKGROUND_LOGGED + BACKGROUND_COMMENTED)
def test_a_logged_background_job_or_a_comment_ending_in_ampersand_gets_no_warning(
    tmp_path, name, line
):
    result = lab_with_script(tmp_path, f"#!/bin/sh\n{line}\nexit 0\n", name)
    assert background_warnings(result) == [], f"false positive for {line!r}: {result.warnings}"
    assert result.warnings.get("test/good", []) == [], result.warnings


BACKGROUND_STILL_BAD = [
    "sleep 600 2>/tmp/err &",
    "sleep 600 >/tmp/log &",
    "sleep 600 2>&1 &",
    "sleep 600 &",
]


@pytest.mark.parametrize("name", ["setup.sh", "check.sh"])
@pytest.mark.parametrize("line", BACKGROUND_STILL_BAD)
def test_a_background_job_with_an_output_still_on_the_exec_stream_is_still_a_warning(
    tmp_path, name, line
):
    result = lab_with_script(tmp_path, f"#!/bin/sh\n{line}\nexit 0\n", name)
    found = background_warnings(result)
    assert found, f"no warning for {line!r}: {result.warnings}"
    assert any(name in w and "02-make-file" in w for w in found), found


def test_the_shipped_sample_labs_still_have_no_warnings_with_the_new_rules():
    from app.content import load_labs

    result = load_labs(REPO_ROOT / "labs" / "content")
    assert result.labs, result.errors
    assert not result.warnings, dict(result.warnings)
