"""Acceptance tests for issue #126: students cannot see or influence the Linux 1 checks (CI, runsc).

D52 moved the Linux 1 checks to "the student side only reports facts, root decides". This exam
pins the four properties that design is meant to give, from the student's side of the container.
Same harness as the #12, #106, #119 exams (a fresh production-spec container per case, `machine`)
and, for the timeout case, that of #117 (`DockerRuntime.exec_script` on a real lab container).

  1. No process command line shows the decision logic of a check (expected answers, hint texts).
     A sampler runs as `student`, reads every /proc/<pid>/cmdline in a tight loop while the
     checks of one task run (right answer and untouched, several rounds), and keeps every needle
     it sees. The needles are the hint fragments parsed from the check scripts plus the expected
     answers named below. A canary (a student process with a known argument) proves the sampler
     can see short-lived processes, so a blind sampler fails the case instead of passing it.
     Mutations that turn it red: a check that runs `grep -qF "<expected text>" file` as root (the
     04 and 05 checks do today, see the note at the end of the 04/05 cases), `printf` or `echo`
     replaced by `/usr/bin/printf "<hint>"`, an expected line passed as an argument of any
     command.
  2. A planted ~/.profile, ~/.bash_profile, ~/.bash_login, ~/.bashrc and ~/.shrc (with ENV set)
     that prints `PASS 1`..`PASS 3` and exits 0 changes nothing: the check prints byte for byte
     what it printed before. A second variant plants forged fact lines, which is what a `su -`
     reader would let through. Mutation: `su - student -c ...` (login shell) in the reader.
  3. Task 06: a student loop that overwrites every student-owned file in /tmp and /var/tmp with
     the wrong answer (a copy of the whole log, which as the "expected lines" would make that
     answer pass) cannot turn a wrong ~/errors.txt into a PASS. Student-owned world-writable
     decoys sit at the usual names (`exp`, `expected`, ...). Mutation: the expected lines (or the
     comparison) written to a temp file by a process that runs as student, or a root write to a
     fixed name in /tmp that the student pre-created.
  4. A student who SIGSTOPs their own processes in a loop during a check gets a failed check
     within the 20 s limit (plus the bounded kill of D56), not a hung request; the check's
     student reader does not pile up afterwards. Mutations: an `exec_script` without the
     deadline (hangs), a runner that does not kill the stopped reader (the leftover case).

No content or app code is touched by this exam. Students are fake. Docker tests skip only when
Docker is absent.
"""
# ruff: noqa: F811
import re
import subprocess
import time
import uuid

import pytest
from test_issue_9_runner_docker import (  # noqa: F401  (fixtures are used by name)
    LAB_YAML,
    box,
    call,
    docker,
    loop,
    network,
    run,
    runtime,
)
from test_issue_12_linux_1 import (  # noqa: F401
    HANDS_ON_IDS,
    _as_student,
    _check,
    _docker,
    _n_items,
    _setup,
    _task_dir,
    _text,
    docker_available,
    image,
    machine,
    runtime_flag,
)

pytestmark = pytest.mark.docker

CHECK_TIMEOUT_S = 20.0  # spec section 7
KILL_BOUND_S = 5.0  # D56: longest kill of a timed-out script
CHECKS = 6  # rounds of a check per state in the sampling case


# ---------------------------------------------------------------- helpers


def _nologin_student(machine: str, script: str, check: bool = True):
    """Run `script` as student without a login shell (the planted dotfiles must not run)."""
    res = _docker(
        "exec", "-i", machine, "su", "student", "-s", "/bin/sh", "-c", "/bin/sh -s",
        check=False, timeout=120, stdin="set -e\n" + script + "\n",
    )
    if check:
        assert res.returncode == 0, f"student commands failed: {res.stdout}\n{res.stderr}"
    return res


def _detached_as_student(machine: str, script: str):
    _docker("exec", "-d", "-u", "student", machine, "sh", "-c", script)


def _alive(machine: str, token: str) -> int:
    """Processes whose command line carries `token` (bracket trick: the counter hides itself)."""
    pat = f"[{token[0]}]{token[1:]}"
    out = _docker(
        "exec", "-u", "root", machine, "sh", "-c", f"ps -eo args= | grep -c '{pat}'; true",
        check=False,
    ).stdout.strip()
    return int(out or 0)


def _stop_marker(machine: str, stop: str):
    """root creates the stop file in the sticky /tmp (the student loops end when they see it)."""
    _docker("exec", "-u", "root", machine, "touch", stop)


def _wait_loop_gone(machine: str, token: str, seconds: float = 8.0):
    deadline = time.monotonic() + seconds
    while _alive(machine, token) and time.monotonic() < deadline:
        time.sleep(0.25)


def _hint_fragments() -> set[str]:
    """Every hint text of the hands-on checks, as fragments of at least 20 characters.

    Parsed from the `FAIL n <hint>` lines. A hint is cut at `; ` and at shell variables, so a
    fragment is literal text a check could hand to a command (the echo builtin does not).
    """
    out: set[str] = set()
    for tid in HANDS_ON_IDS:
        for m in re.finditer(r'FAIL\s+\d+\s+([^"\n]+)"?', _text(_task_dir(tid) / "check.sh")):
            for part in re.split(r"; |\$\{?\w+\}?", m.group(1)):
                part = part.strip().strip('"')
                if len(part) >= 20:
                    out.add(part)
    assert len(out) >= 10, f"found too few hint texts in the check scripts: {sorted(out)}"
    return out


# The expected answers of the checks, as the content states them (setup.sh and check.sh):
# what a check compares the student's work with and the student is asked to find or produce.
ANSWERS = {
    "04-copy-move": ["Quarterly report draft", "Photo of the team"],
    "05-find-file": [
        "/home/student/archive/2023/spring/misc/secret-recipe.txt",
        "archive/2023/spring/misc",
    ],
    "06-grep-log": [
        "2025-11-14 09:01:12 ERROR cannot open file /data/report.csv",
        "2025-11-14 09:03:15 ERROR connection to database lost",
        "2025-11-14 09:04:09 ERROR retry failed",
        "2025-11-14 09:06:11 ERROR timeout while saving profile",
        "connection to database lost",
        "retry failed",
        "timeout while saving profile",
    ],
}
CANARY = "canary-126-" + uuid.uuid4().hex[:8]

SAMPLER = r"""
rm -f /tmp/hits126
while [ ! -e /tmp/stop-sampler126 ]; do
  cat /proc/[0-9]*/cmdline 2>/dev/null | tr '\0' ' ' | grep -aoF -f /tmp/needles126 >> /tmp/hits126
done
"""


def _read_hits(machine: str) -> list[str]:
    out = _docker("exec", "-u", "root", machine, "sh", "-c", "cat /tmp/hits126 2>/dev/null; true")
    return [ln for ln in out.stdout.splitlines() if ln.strip()]


def _solve(machine: str, task_id: str):
    res = _as_student(machine, _task_dir(task_id) / "solution.sh")
    assert res.returncode == 0, f"solution.sh of {task_id} failed: {res.stdout}\n{res.stderr}"


# ------------------------------------------------------- 1. no decision logic on a command line


@pytest.mark.parametrize("task_id", HANDS_ON_IDS)
def test_no_process_command_line_shows_the_decision_logic_of_the_check(machine, task_id):
    needles = sorted(_hint_fragments() | {a for lst in ANSWERS.values() for a in lst} | {CANARY})
    assert all(n.strip() for n in needles), "an empty needle would match every command line"
    _docker(
        "exec", "-i", "-u", "student", machine, "sh", "-c", "cat > /tmp/needles126",
        stdin="\n".join(needles) + "\n",
    )
    _setup(machine, task_id)
    _detached_as_student(machine, SAMPLER)
    time.sleep(1.0)

    # The canary: short-lived student processes with a known argument. If the sampler cannot see
    # these, a clean result below would mean nothing.
    _docker(
        "exec", "-u", "student", machine, "sh", "-c",
        f"i=0; while [ $i -lt 300 ]; do grep -qF {CANARY} /dev/null; i=$((i+1)); done",
        timeout=120,
    )
    time.sleep(0.5)
    assert any(CANARY in h for h in _read_hits(machine)), (
        "the /proc sampler did not see a short-lived student process; it cannot vouch for a check"
    )

    # Untouched task, then the right answer, several rounds each: every branch of the check runs.
    for _ in range(CHECKS):
        _check(machine, task_id)
    _solve(machine, task_id)
    for _ in range(CHECKS):
        _check(machine, task_id)

    _stop_marker(machine, "/tmp/stop-sampler126")
    time.sleep(1.5)
    leaked = sorted({h for h in _read_hits(machine) if h != CANARY})
    assert not leaked, (
        f"{task_id}: the check's decision logic was visible in /proc/<pid>/cmdline: {leaked}"
    )


# ---------------------------------------------------------------- 2. planted dotfiles


DOTFILES = [".profile", ".bash_profile", ".bash_login", ".bashrc", ".shrc"]


def _reader_keys(task_id: str) -> list[str]:
    text = _text(_task_dir(task_id) / "check.sh")
    return re.findall(r"^[fc] (\w+) /home/student", text, flags=re.M)


def _plant(machine: str, lines: list[str]):
    body = "\n".join(lines) + "\n"
    cmds = "".join(f"cat > \"$HOME/{f}\" <<'PLANT126'\n{body}PLANT126\n" for f in DOTFILES)
    _nologin_student(machine, cmds)


def _unplant(machine: str):
    _nologin_student(machine, "rm -f " + " ".join(f'"$HOME/{f}"' for f in DOTFILES))


@pytest.mark.parametrize("task_id", HANDS_ON_IDS)
def test_planted_shell_startup_files_do_not_change_the_verdict_of_an_untouched_task(
    machine, task_id
):
    _setup(machine, task_id)
    base, parsed = _check(machine, task_id)
    assert base.returncode != 0 and not parsed.passed, base.stdout  # the baseline is a failure
    keys = _reader_keys(task_id)
    assert keys, "could not read the keys of the check's fact reader"

    pass_lines = ['export ENV="$HOME/.shrc"'] + [
        f'echo "PASS {i}"' for i in range(1, _n_items(task_id) + 1)
    ] + ["exit 0"]
    forged_d = [f"echo 'F {k} d 755 student team'; echo 'R {k} 1'" for k in keys]
    forged_f = [f"echo 'F {k} f 755 student team'; echo 'R {k} 1'" for k in keys]
    variants = {
        "prints PASS and exits 0": pass_lines,
        "forges fact lines for folders": forged_d,
        "forges fact lines for files": forged_f,
    }
    for name, lines in variants.items():
        _setup(machine, task_id)  # the same untouched starting state
        _plant(machine, lines)
        res, parsed = _check(machine, task_id)
        assert res.returncode != 0 and not parsed.passed, (
            f"{task_id}: a planted startup file that {name} made the check pass:\n{res.stdout}"
        )
        assert res.stdout == base.stdout, (
            f"{task_id}: a planted startup file that {name} changed the verdict.\n"
            f"before:\n{base.stdout}\nafter:\n{res.stdout}"
        )
        _unplant(machine)


# ------------------------------------------------------- 3. task 06 and the /tmp overwriter


ATTACK = r"""
while [ ! -e /tmp/stop-attack126 ]; do
  for f in /tmp/* /tmp/*/* /var/tmp/* /var/tmp/*/*; do
    if [ -f "$f" ] && [ -O "$f" ]; then
      cat /home/student/logs/app.log > "$f" 2>/dev/null
    fi
  done
done
"""
DECOYS = ["exp", "expected", "want", "wanted", "answer", "errors", "lines", "ref", "good", "out"]


def test_06_a_loop_overwriting_student_owned_files_in_tmp_cannot_make_a_wrong_answer_pass(machine):
    task_id = "06-grep-log"
    _setup(machine, task_id)
    # A wrong answer: the whole log. Items 1 and 2 hold, item 3 (only ERROR lines) must fail.
    _nologin_student(machine, "cp ~/logs/app.log ~/errors.txt")
    base, parsed = _check(machine, task_id)
    assert base.returncode != 0 and not parsed.passed, base.stdout
    assert parsed.items[1].passed and parsed.items[2].passed and not parsed.items[3].passed, (
        base.stdout
    )
    _nologin_student(
        machine,
        "for n in " + " ".join(DECOYS)
        + "; do cat ~/logs/app.log > /tmp/$n; chmod 666 /tmp/$n; done",
    )
    _detached_as_student(machine, ATTACK)
    time.sleep(1.0)
    assert _alive(machine, "stop-attack126") >= 1, "the attack loop did not start"
    try:
        for i in range(10):
            res, parsed = _check(machine, task_id)
            assert res.returncode != 0 and not parsed.passed, (
                f"round {i}: a /tmp overwriting loop turned the wrong errors.txt into a pass:\n"
                f"{res.stdout}"
            )
            assert res.stdout == base.stdout, (
                f"round {i}: the verdict moved under the loop.\nbefore:\n{base.stdout}\n"
                f"after:\n{res.stdout}"
            )
    finally:
        _stop_marker(machine, "/tmp/stop-attack126")
        _wait_loop_gone(machine, "stop-attack126")


# ------------------------------------------------------- 4. a student who stops their processes


STOPPER = r"""
while [ ! -e /tmp/stop-stopper126 ]; do
  for p in /proc/[0-9]*; do
    p=${p#/proc/}
    [ "$p" = "$$" ] || kill -STOP "$p" 2>/dev/null
  done
done
"""


def _release_students_processes(box: str):
    docker("exec", "-u", "root", box, "touch", "/tmp/stop-stopper126", check=False)
    _wait_loop_gone(box, "stop-stopper126")
    docker("exec", "-u", "student", box, "sh", "-c", "kill -CONT -1; kill -KILL -1", check=False)
    docker("exec", "-u", "root", box, "rm", "-f", "/tmp/stop-stopper126", check=False)


def _run_check_under_stopper(runtime, call, box, task_id):
    task = _task_dir(task_id)
    setup = call(runtime.exec_script, box, _text(task / "setup.sh"), 60)
    assert setup.exit_code == 0 and not setup.timed_out, setup.output
    docker("exec", "-d", "-u", "student", box, "sh", "-c", STOPPER)
    time.sleep(1.0)
    assert _alive(box, "stop-stopper126") >= 1, "the stopping loop did not start"
    started = time.monotonic()
    res = call(runtime.exec_script, box, _text(task / "check.sh"), CHECK_TIMEOUT_S)
    return res, time.monotonic() - started


@pytest.fixture
def fresh_box(runtime, call, tmp_path_factory, request) -> str:
    """A lab container of its own per case (copy of the module-scoped `box` of the #9 exam)."""
    from app.content import load_labs
    from app.runtime import container_spec

    root = tmp_path_factory.mktemp("content")
    d = root / "test" / "runner"
    d.mkdir(parents=True)
    (d / "lab.yaml").write_text(LAB_YAML, encoding="utf-8")
    res = load_labs(root)
    assert "runner" in res.labs, res.errors
    session = str(uuid.uuid4())
    spec = container_spec(res.labs["runner"], session)
    name = f"lab-{session}"
    request.addfinalizer(
        lambda: subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True, timeout=120)
    )
    assert str(call(runtime.start, spec)) == name
    deadline = time.time() + 60
    while time.time() < deadline:  # the entrypoint copies /etc/skel into the empty tmpfs home
        if docker("exec", name, "test", "-f", "/home/student/.bashrc", check=False).returncode == 0:
            return name
        time.sleep(0.5)
    raise AssertionError("/home/student/.bashrc never appeared (skel was not copied)")


@pytest.mark.parametrize("task_id", ["02-directory-tree", "06-grep-log"])
def test_a_student_stopping_their_own_processes_gets_a_failed_check_not_a_hang(
    docker_available, runtime, call, fresh_box, task_id
):
    try:
        res, elapsed = _run_check_under_stopper(runtime, call, fresh_box, task_id)
    finally:
        _release_students_processes(fresh_box)
    assert elapsed < CHECK_TIMEOUT_S + KILL_BOUND_S + 5, (
        f"the check request hung for {elapsed:.0f} s under a student SIGSTOP loop"
    )
    assert res.timed_out or res.exit_code != 0, (
        f"a stopped student reader did not fail the check: exit {res.exit_code}\n{res.output}"
    )
    assert "PASS" not in res.output.replace("PASS 0", ""), res.output


# Stale-killer cases (stopped reader pile-up, next check after a stop loop): see #306.
