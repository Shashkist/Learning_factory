"""Acceptance tests for issue #680: lab content "Network diagnostics: ss, ports, processes and logs" (en + he), lesson 9 (D191), a sudo lab.

The lab does not exist yet; these tests describe it. Choices pinned here (the builder follows them):

Location and metadata
    labs/content/networking/net-diag/lab.yaml   course dir `networking` (next to net-1, net-2), lab id
    `net-diag` (the validator requires id == directory name). module: 1, lesson: 9 (D191 v2 numbering),
    image: term, sudo: true, status: published (D191), no internet (no `internet`, no `allow_domains`),
    time_limit_min between 30 and 60 (about 45 minutes). 7 tasks.

Services (every service runs AS `student`, never as root; root in a lab container has no CAP_KILL and no
    CAP_SYS_PTRACE, D200: setup.sh stops old services by `su student -c ...`). Each service is a process
    whose kernel name (comm, the name `ss -p` / `ps` show) is the pinned name, for example a copy or
    symlink of python3 named `inventory`. Port numbers come from /proc/net/tcp in the exam.

Tasks (ids and order are fixed; kind in brackets)
    01-inventory-port  [hands-on]  `inventory` listens on a random TCP port in 20000..29999 (fresh on every
                                   setup). THE TRAP (D191): a second service named `inventory-db` listens on
                                   another random port in the same range. The student writes the port of
                                   `inventory` (not `inventory-db`) to `~/port.txt`: the number alone;
                                   whitespace around it and a trailing newline are fine, any extra line,
                                   word, backslash, the other service's port, a port nobody listens on, or
                                   an empty file fails. The task text never names `inventory-db` (in either
                                   language); solution.en.md explains the two names; the hint of the
                                   failing item for the other service's port names `inventory-db`.
    02-port-9090-pid   [hands-on]  a process named `reports` listens on 9090 (restarted by every setup, so
                                   its PID is new each time). `~/pid.txt` holds its PID, the number alone
                                   (same exactness rules). The student's own shell pid, PID 1, the port
                                   number, the PID of another service and a stale PID of an earlier setup
                                   fail.
    03-nc-8081         [hands-on]  after setup nothing listens on 8081 (and `~/nc-8081.txt` does not
                                   exist). The student runs `nc` (for example `nc -zv 127.0.0.1 8081`) and
                                   writes `open` or `closed` (lowercase, exact word, surrounding whitespace
                                   allowed) to `~/nc-8081.txt`. check.sh asks the live state with `nc -z`
                                   (with a timeout) every time it runs: the right word follows the port
                                   (the exam starts and stops a server on 8081 and the answer must follow).
    04-quiz-loopback   [quiz]      a service listens on `127.0.0.1:8000` only (as `ss` shows): why does a
                                   request from another machine fail. Right option: it listens on the
                                   loopback address, reachable from this machine only; it has to bind
                                   `0.0.0.0` (the only option that says `0.0.0.0`, and it also says
                                   `127.0.0.1`).
    05-orders-log      [hands-on]  setup makes the `orders` service refuse connections on 5050: it is not
                                   running, because 5050 is held by a process named `batch-sync` (also a
                                   student process, listening on 5050 and answering some other page), and
                                   writes the log `/var/log/lab-diag/orders.log` (root-owned, readable by
                                   everyone), which holds the lines saying `bind ... 5050 ... Address
                                   already in use`. The student reads the log, finds the holder with `ss`,
                                   and writes the holder's process name, `batch-sync`, to `~/blocker.txt`
                                   (the word alone; exactness rules as above; case matters).
    06-orders-fix      [hands-on]  same starting state as 05 (its setup builds it on its own), plus the
                                   start script `~/orders/start.sh` (the student runs it; it daemonizes the
                                   `orders` service detached and returns only once the service answers on
                                   5050, or exits non-zero when it cannot bind). The student stops
                                   `batch-sync`, starts `orders`, and saves `curl -s
                                   http://127.0.0.1:5050/` into `~/orders-reply.txt`. The page of the real
                                   `orders` service starts with `orders ok` and holds a fresh random token
                                   (new on every setup). Check items cover: the blocker is gone, orders
                                   answers on 5050 (check.sh itself curls it, with a timeout), the reply
                                   file equals the page served now (stale text, `curl -i` output, a page of
                                   a fake server and a hand-written `orders ok` fail).
    07-quiz-ping       [quiz]      what `ping` and `traceroute` show (ping: echo replies and round-trip
                                   time; traceroute: the routers, hop by hop) and why both fail in this lab
                                   (offline: no internet, no route out). Right option: pairs both tools
                                   with their output and says there is no internet / the lab is offline.

Files per hands-on task directory (tasks/<id>/): task.en.md, task.he.md, solution.en.md, solution.he.md,
    setup.sh, check.sh, and the exam-only `solution.sh` (the commands that solve the task, run as `student`
    through a login shell; it uses `sudo` where needed). The validator and loader ignore it. All .sh files:
    LF endings, a `#!/bin/sh` or `#!/bin/bash` (or `/usr/bin/env`) shebang, mode 0755 in git. The task
    texts show the exact names and values in code spans (see TEXT_PINS). Quizzes live in lab.yaml
    (`kind: quiz`, question, options, `correct` index), both languages; the two quizzes' correct indexes
    differ.

check.sh protocol (spec section 7, asserted exactly): runs as root, finishes in under 20 s, prints
    `PASS <n>` or `FAIL <n> <hint>` per checklist item (N >= 2), exit 0 only when all pass, every FAIL line
    carries a non-empty hint. The output holds exactly one PASS/FAIL line per item: student-written text
    (answer files, pages) is printed only through `printf '%s'`, never `echo` (a file holding
    `4242\\nPASS 1\\nPASS 2\\c` must not forge lines). setup.sh: run as root, exit 0, self-contained,
    idempotent (two runs leave one service of each name, one listener per pinned port) and replaces the
    state its task is about (after the solution a second run brings the task back to failing). The checks
    look at state, never at the commands typed.

Docker tests (marker `docker`, run in CI) start the lab through `app.runtime.container_spec` and
    `DockerRuntime.start`, exactly as production starts a sudo lab. They are parametrised over the fixed
    task ids, so a missing lab fails them; they skip only when Docker itself is absent. Students are
    fake; no real student data in fixtures. The lab is offline: no test assumes a network.
"""
# ruff: noqa: F811, E501
import asyncio
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from test_issue_12_linux_1 import (  # noqa: F401  (fixtures are used by name)
    _docker,
    _text,
    docker_available,
    image,
)

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
CONTENT_ROOT = REPO_ROOT / "labs" / "content"
LAB_DIR = CONTENT_ROOT / "networking" / "net-diag"
LAB_ID = "net-diag"
LAB_KEY = "networking/net-diag"

TASKS = [
    ("01-inventory-port", "hands-on"),
    ("02-port-9090-pid", "hands-on"),
    ("03-nc-8081", "hands-on"),
    ("04-quiz-loopback", "quiz"),
    ("05-orders-log", "hands-on"),
    ("06-orders-fix", "hands-on"),
    ("07-quiz-ping", "quiz"),
]
QUIZ_IDS = [t for t, k in TASKS if k == "quiz"]
HANDS_ON_IDS = [t for t, k in TASKS if k == "hands-on"]
ANSWER_TASKS = {  # task -> answer file the student writes
    "01-inventory-port": "port.txt",
    "02-port-9090-pid": "pid.txt",
    "03-nc-8081": "nc-8081.txt",
    "05-orders-log": "blocker.txt",
}
TEXT_PINS = {
    "01-inventory-port": ["inventory", "~/port.txt", "ss"],
    "02-port-9090-pid": ["9090", "~/pid.txt"],
    "03-nc-8081": ["nc", "8081", "~/nc-8081.txt", "open", "closed"],
    "05-orders-log": ["/var/log/lab-diag/orders.log", "~/blocker.txt", "5050"],
    "06-orders-fix": ["5050", "~/orders-reply.txt", "curl", "~/orders/start.sh"],
}
HEBREW = re.compile("[֐-׿]")
HOME = "/home/student"
LOG = "/var/log/lab-diag/orders.log"


# ---------------------------------------------------------------- helpers


def _load():
    from app.content import load_labs

    return load_labs(CONTENT_ROOT)


def _lab():
    res = _load()
    assert LAB_ID in res.labs, (
        f"lab {LAB_ID} is not loaded; errors: {res.errors.get(LAB_KEY)} (is {LAB_DIR} missing?)"
    )
    return res.labs[LAB_ID]


def _raw():
    import yaml

    path = LAB_DIR / "lab.yaml"
    assert path.is_file(), f"{path} is missing"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _raw_task(task_id: str) -> dict:
    for t in _raw().get("tasks", []):
        if t.get("id") == task_id:
            return t
    raise AssertionError(f"task {task_id} is not in lab.yaml")


def _task(task_id: str):
    return next(t for t in _lab().tasks if t.id == task_id)


def _task_dir(task_id: str) -> Path:
    d = LAB_DIR / "tasks" / task_id
    assert d.is_dir(), f"{d} is missing"
    return d


def _all_shell_files() -> list[Path]:
    assert LAB_DIR.is_dir(), f"{LAB_DIR} is missing"
    files = sorted(LAB_DIR.rglob("*.sh"))
    assert files, "the lab has no shell files"
    return files


def _code_spans(md: str) -> list[str]:
    fences = re.findall(r"```.*?\n(.*?)```", md, flags=re.S)
    rest = re.sub(r"```.*?```", "", md, flags=re.S)
    return fences + re.findall(r"`([^`\n]+)`", rest)


def _n_items(task_id: str) -> int:
    return len(_task(task_id).checklist)


def _others(task):
    return [o["en"] for i, o in enumerate(task.options) if i != task.correct]


# ---------------------------------------------------------------- non-docker: validator and shape


def test_validator_accepts_the_lab_with_no_errors():
    res = _load()
    assert LAB_KEY not in res.errors, res.errors.get(LAB_KEY)
    assert LAB_ID in res.labs, f"{LAB_ID} not loaded; all errors: {res.errors}"


def test_lab_is_module_1_lesson_9_image_term():
    lab = _lab()
    assert (lab.module, lab.lesson) == (1, 9)
    assert lab.image == "term"
    assert lab.path.name == LAB_ID
    assert lab.path.parent.name == "networking"


def test_status_is_published():
    assert _lab().status == "published"
    assert _raw().get("status") == "published"


def test_sudo_is_true_explicitly_and_internet_is_off():
    raw = _raw()
    assert raw.get("sudo") is True, "lab.yaml must say `sudo: true`"
    assert _lab().sudo is True
    assert not raw.get("internet", False)
    assert not raw.get("allow_domains", [])


def test_lab_runs_about_45_minutes():
    assert 30 <= _lab().time_limit_min <= 60


def test_task_order_and_kinds_match_the_plan():
    got = [(t.id, t.kind) for t in _lab().tasks]
    assert got == TASKS
    assert 6 <= len(got) <= 10


def test_lab_title_and_description_have_en_and_he():
    lab = _lab()
    assert lab.title.get("en", "").strip() and lab.title.get("he", "").strip()
    assert lab.description.get("en", "").strip() and lab.description.get("he", "").strip()
    assert HEBREW.search(lab.title["he"]) and HEBREW.search(lab.description["he"])
    assert not HEBREW.search(lab.title["en"] + lab.description["en"])


@pytest.mark.parametrize("task_id", QUIZ_IDS)
def test_quiz_has_en_and_he_text_with_real_hebrew(task_id):
    task = _task(task_id)
    assert task.question.get("en", "").strip()
    assert HEBREW.search(task.question.get("he", "")), "he question must be in Hebrew script"
    assert not HEBREW.search(task.question["en"])
    assert len(task.options) >= 3
    for opt in task.options:
        assert opt.get("en", "").strip() and opt.get("he", "").strip()
        assert not HEBREW.search(opt["en"])
    assert any(HEBREW.search(o["he"]) for o in task.options), "he options carry Hebrew words"


@pytest.mark.parametrize("task_id", QUIZ_IDS)
def test_quiz_has_one_declared_correct_answer_and_distinct_options(task_id):
    task = _task(task_id)
    raw = _raw_task(task_id)
    assert isinstance(task.correct, int) and 0 <= task.correct < len(task.options)
    assert raw.get("correct") == task.correct and isinstance(raw["correct"], int)
    en = [o["en"].strip() for o in task.options]
    assert len(set(en)) == len(en), "options must be distinct"


def test_quiz_correct_answers_are_not_all_the_same_index():
    idx = {_task(t).correct for t in QUIZ_IDS}
    assert len(idx) == len(QUIZ_IDS), f"the quizzes share a correct index: {idx}"


def test_loopback_quiz_right_answer_is_the_loopback_only_bind():
    task = _task("04-quiz-loopback")
    q = task.question["en"]
    assert "127.0.0.1" in q and "8000" in q and "ss" in q
    right = task.options[task.correct]["en"]
    assert "127.0.0.1" in right and "0.0.0.0" in right
    assert re.search(r"loopback|this machine|local", right, flags=re.I)
    for o in _others(task):
        assert "0.0.0.0" not in o, f"a wrong option also names the fix: {o!r}"
    assert any(re.search(r"firewall|closed|crash|not running", o, flags=re.I) for o in _others(task)), (
        "plausible wrong options (firewall, closed port, crashed service) are expected"
    )


def test_ping_quiz_right_answer_pairs_the_tools_with_their_output_and_says_offline():
    task = _task("07-quiz-ping")
    q = task.question["en"].lower()
    assert "ping" in q and "traceroute" in q
    pats = (
        r"ping[^;.]*(reply|round-trip|latency|icmp)",
        r"traceroute[^;.]*(hop|router|path)",
        r"no internet|offline|isolated|no route",
    )
    right = task.options[task.correct]["en"]
    for p in pats:
        assert re.search(p, right, flags=re.I), f"the right option must match {p!r}: {right!r}"
    for o in _others(task):
        ok = [bool(re.search(p, o, flags=re.I)) for p in pats]
        assert not all(ok), f"a wrong option holds all three right parts: {o!r}"


@pytest.mark.parametrize("task_id", HANDS_ON_IDS)
def test_hands_on_checklist_and_texts_are_bilingual(task_id):
    task = _task(task_id)
    assert len(task.checklist) >= 2, "each hands-on task has at least two checklist items"
    for item in task.checklist:
        assert item.get("en", "").strip() and item.get("he", "").strip()
        assert HEBREW.search(item["he"]), f"checklist he must be Hebrew script: {item['he']!r}"
        assert not HEBREW.search(item["en"])
    d = _task_dir(task_id)
    for name in ("task.he.md", "solution.he.md"):
        assert HEBREW.search(_text(d / name)), f"{task_id}/{name} has no Hebrew"
    for name in ("task.en.md", "solution.en.md"):
        assert not HEBREW.search(_text(d / name)), f"{task_id}/{name} contains Hebrew"
        assert _text(d / name).strip()


@pytest.mark.parametrize("task_id", HANDS_ON_IDS)
def test_commands_stay_in_latin_inside_hebrew_text(task_id):
    d = _task_dir(task_id)
    for name in ("task.he.md", "solution.he.md"):
        spans = _code_spans(_text(d / name))
        assert spans, f"{task_id}/{name} shows no command in a code span"
        for span in spans:
            assert not HEBREW.search(span), f"Hebrew inside a code span in {name}: {span!r}"


@pytest.mark.parametrize("task_id", HANDS_ON_IDS)
def test_task_texts_give_the_exact_names_and_values_in_both_languages(task_id):
    d = _task_dir(task_id)
    for name in ("task.en.md", "task.he.md"):
        spans = "\n".join(_code_spans(_text(d / name)))
        for pin in TEXT_PINS[task_id]:
            assert pin in spans, f"{task_id}/{name} does not show `{pin}` in a code span"


@pytest.mark.parametrize("task_id", HANDS_ON_IDS)
def test_hands_on_task_directory_has_every_file(task_id):
    d = _task_dir(task_id)
    for name in (
        "task.en.md", "task.he.md", "solution.en.md", "solution.he.md",
        "setup.sh", "check.sh", "solution.sh",
    ):
        assert (d / name).is_file(), f"{task_id}/{name} is missing"


def test_every_shell_file_has_lf_endings_and_a_shebang():
    for path in _all_shell_files():
        raw = path.read_bytes()
        assert b"\r" not in raw, f"{path} has CR characters (needs LF endings)"
        first = raw.split(b"\n", 1)[0].decode("utf-8", "replace")
        assert re.fullmatch(r"#!\s*(/bin/(ba)?sh|/usr/bin/env (ba)?sh)\s*", first), (
            f"{path} first line is {first!r}"
        )
        assert raw.endswith(b"\n"), f"{path} lacks a final newline"


def test_shell_files_are_executable():
    import os

    for path in _all_shell_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        out = subprocess.run(
            ["git", "ls-files", "-s", "--", rel], cwd=REPO_ROOT, capture_output=True, text=True
        ).stdout.strip()
        if out:
            assert out.split()[0] == "100755", f"{rel} is tracked with mode {out.split()[0]}"
        elif sys.platform != "win32":
            assert os.access(path, os.X_OK), f"{rel} is not executable"


def test_solution_sh_is_ignored_by_the_loader_and_never_exposed():
    lab = _lab()
    assert "solution.sh" not in repr(lab) + repr(lab.tasks)
    for t in lab.tasks:
        for value in vars(t).values():
            assert "solution.sh" not in repr(value)
    from app.content import SCRIPTS, TASK_FILES

    assert "solution.sh" not in (*SCRIPTS, *TASK_FILES)
    assert LAB_KEY not in _load().errors


def test_solution_sh_exists_only_in_hands_on_task_dirs():
    assert LAB_DIR.is_dir(), f"{LAB_DIR} is missing"
    assert {p.parent.name for p in LAB_DIR.rglob("solution.sh")} == set(HANDS_ON_IDS)


def test_the_trap_is_not_given_away_in_the_task_text_but_explained_in_the_solution():
    d = _task_dir("01-inventory-port")
    for name in ("task.en.md", "task.he.md"):
        assert "inventory-db" not in _text(d / name), f"{name} must not name inventory-db (that is the trap)"
    sol = _text(d / "solution.en.md")
    assert "inventory-db" in sol and "inventory" in sol, "solution 01 must explain the two similar names"


def test_check_scripts_look_at_state_not_at_the_shell_history():
    for task_id in HANDS_ON_IDS:
        code = _text(_task_dir(task_id) / "check.sh")
        assert "bash_history" not in code and not re.search(r"\bhistory\b", code), (
            f"check.sh of {task_id} reads the shell history"
        )


def test_task_setups_never_use_the_student_sudo_rule():
    for task_id in HANDS_ON_IDS:
        code = _text(_task_dir(task_id) / "setup.sh")
        assert "sudo " not in code, f"setup.sh of {task_id} runs as root and needs no sudo"


def test_check_scripts_print_student_text_only_through_printf_s():
    for task_id in HANDS_ON_IDS:
        code = _text(_task_dir(task_id) / "check.sh")
        assert not re.search(r"\becho\s+-e\b|\bprintf\s+(-\S+\s+)?[\"'][^\"']*\$", code), (
            f"check.sh of {task_id} lets a variable into a printf format (use printf '%s')"
        )
        assert "printf" in code, f"check.sh of {task_id} must print its lines with printf '%s'"


def test_no_real_student_data_in_the_lab():
    assert LAB_DIR.is_dir(), f"{LAB_DIR} is missing"
    for path in LAB_DIR.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        assert not re.search(r"\b\d{9}\b", text), f"{path} holds a 9-digit number (an ID?)"
        for addr in re.findall(r"[\w.+-]+@([\w-]+\.[\w.-]+)", text):
            assert addr.lower() in {"example.com", "example.org", "example.net"}, (
                f"{path} holds an e-mail address at {addr}"
            )


# ---------------------------------------------------------------- docker helpers

# Listening TCP sockets as (port, pid, comm, uid, hex address), read as `student` from /proc (D200):
# the student sees the fd table of its own processes. `kill PORT` stops what listens there.
_PROCS = r"""
import os, signal, sys, time


def listeners():
    inodes = {}
    for f in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = open(f).read().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            p = line.split()
            addr, prt = p[1].rsplit(":", 1)
            if p[3] == "0A":
                inodes[p[9]] = (int(prt, 16), addr, int(p[7]))
    out = []
    for pid in [d for d in os.listdir("/proc") if d.isdigit()]:
        try:
            for fd in os.listdir(f"/proc/{pid}/fd"):
                t = os.readlink(f"/proc/{pid}/fd/{fd}")
                if t.startswith("socket:[") and t[8:-1] in inodes:
                    port, addr, uid = inodes[t[8:-1]]
                    comm = open(f"/proc/{pid}/comm").read().strip()
                    out.append((port, int(pid), comm, uid, addr))
        except OSError:
            continue
    return sorted(set(out))


if sys.argv[1] == "list":
    for port, pid, comm, uid, addr in listeners():
        print(port, pid, comm, uid, addr)
else:
    want = int(sys.argv[2])
    deadline = time.time() + 8
    while time.time() < deadline:
        pids = {pid for port, pid, *_ in listeners() if port == want}
        if not pids:
            break
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        time.sleep(0.2)
"""


@pytest.fixture(scope="module")
def network(docker_available):
    import os

    name = os.environ.get("LF_LAB_NETWORK", "lf-labs")
    existed = _docker("network", "inspect", name, check=False).returncode == 0
    if not existed:
        _docker("network", "create", name)
    yield name
    if not existed:
        _docker("network", "rm", name, check=False)


@pytest.fixture
def machine(image, network, request, monkeypatch):
    """A fresh lab container started as production starts this (sudo) lab."""
    from app.runtime import DockerRuntime, container_spec

    lab = _lab()
    assert lab.sudo is True
    monkeypatch.setenv("LF_LAB_TERM_IMAGE", image)
    monkeypatch.setenv("LF_LAB_NETWORK", network)
    monkeypatch.delenv("LF_DOCKER_HOST", raising=False)
    monkeypatch.delenv("LF_SUDO_RUNTIME", raising=False)
    spec = container_spec(lab, str(uuid.uuid4()))
    loop = asyncio.new_event_loop()
    runtime = DockerRuntime()
    name = str(loop.run_until_complete(runtime.start(spec)))
    request.addfinalizer(
        lambda: subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True, timeout=120)
    )
    request.addfinalizer(loop.close)
    loop.run_until_complete(runtime.wait_ready(name, 90))
    deadline = time.time() + 60
    while time.time() < deadline:  # the entrypoint copies the skel and writes the sudo rule
        probe = _docker("exec", name, "test", "-f", "/home/student/.bashrc", check=False)
        rule = _docker("exec", name, "test", "-f", "/etc/sudoers.d/student", check=False)
        if probe.returncode == 0 and rule.returncode == 0:
            return name
        time.sleep(0.5)
    raise AssertionError("the lab container never became ready")


def _interpreter(script: Path) -> str:
    first = _text(script).split("\n", 1)[0]
    parts = [p for p in first[2:].split() if not p.endswith("/env")]
    return parts[-1]


def _as_root(machine: str, script: Path, timeout: int = 60):
    return _docker(
        "exec", "-i", machine, _interpreter(script), "-s",
        check=False, timeout=timeout, stdin=_text(script),
    )


def _root(machine: str, code: str, timeout: int = 60):
    return _docker("exec", "-i", machine, "sh", "-s", check=False, timeout=timeout, stdin=code)


def _student(machine: str, code: str, timeout: int = 90):
    """Run shell code as `student` through a login shell (as the terminal does)."""
    return _docker(
        "exec", "-i", machine, "su", "-", "student", "-c", "bash -s",
        check=False, timeout=timeout, stdin=code,
    )


def _setup(machine: str, task_id: str):
    res = _as_root(machine, _task_dir(task_id) / "setup.sh")
    assert res.returncode == 0, f"setup.sh of {task_id} failed ({res.returncode}): {res.stderr}"
    return res


def _solve(machine: str, task_id: str):
    sol = _student(machine, _text(_task_dir(task_id) / "solution.sh"))
    assert sol.returncode == 0, f"solution.sh of {task_id} failed: {sol.stdout}\n{sol.stderr}"


def _try(machine: str, code: str):
    r = _student(machine, "set -e\n" + code)
    assert r.returncode == 0, f"student commands failed: {code!r}\n{r.stdout}\n{r.stderr}"
    return r


def _check(machine: str, task_id: str):
    from app.content import parse_check_output

    start = time.time()
    res = _as_root(machine, _task_dir(task_id) / "check.sh", timeout=60)
    assert time.time() - start < 20, "check.sh took longer than the 20 s limit"
    return res, parse_check_output(res.stdout, res.returncode, _n_items(task_id))


def _assert_clean_protocol(res, task_id: str):
    """Exactly one PASS/FAIL line per item, numbered 1..n: student text cannot add lines."""
    n = _n_items(task_id)
    nums = [int(m.group(1)) for line in res.stdout.splitlines() if (m := re.match(r"(?:PASS|FAIL) (\d+)\b", line))]
    stray = [ln for ln in res.stdout.splitlines() if re.match(r"\s*(PASS|FAIL)\b", ln) and not re.match(r"(PASS|FAIL) \d+", ln)]
    assert nums == list(range(1, n + 1)) and not stray, (
        f"{task_id}: expected exactly the lines for items 1..{n}, once each:\n{res.stdout!r}"
    )


def _assert_failing_with_hints(machine: str, task_id: str, where: str):
    res, parsed = _check(machine, task_id)
    n = _n_items(task_id)
    assert res.returncode != 0, f"{task_id}: check.sh exits 0 {where}:\n{res.stdout}"
    assert not parsed.passed
    assert set(parsed.items) == set(range(1, n + 1)), (
        f"{task_id}: expected one PASS/FAIL line for each of items 1..{n} {where}:\n{res.stdout}"
    )
    _assert_clean_protocol(res, task_id)
    failing = [i for i, it in parsed.items.items() if not it.passed]
    assert failing, f"{task_id}: no FAIL line {where}:\n{res.stdout}"
    for i in failing:
        assert parsed.items[i].hint, f"{task_id}: FAIL {i} has no hint {where}:\n{res.stdout}"
    for line in res.stdout.splitlines():
        if line.startswith("FAIL"):
            assert re.fullmatch(r"FAIL \d+ \S.*", line.strip()), f"bad FAIL line: {line!r}"
    return res, parsed


def _assert_passing(machine: str, task_id: str, what: str):
    res, parsed = _check(machine, task_id)
    n = _n_items(task_id)
    assert set(parsed.items) == set(range(1, n + 1)), res.stdout
    assert "FAIL" not in res.stdout, f"{task_id}: {what} was refused:\n{res.stdout}"
    assert res.returncode == 0 and parsed.passed, f"{task_id}: {what}:\n{res.stdout}"
    _assert_clean_protocol(res, task_id)


def _near_miss(machine, task_id, code, why):
    """Fresh state of the task, then a plausible wrong attempt: the check must refuse it."""
    _setup(machine, task_id)
    _try(machine, code)
    return _assert_failing_with_hints(machine, task_id, f"for {why}")


def _alternative(machine, task_id, code, why):
    _setup(machine, task_id)
    _try(machine, code)
    _assert_passing(machine, task_id, why)


def _hints(parsed) -> str:
    return " ".join(it.hint or "" for it in parsed.items.values()).lower()


def _out(machine: str, code: str) -> str:
    return _student(machine, code).stdout.strip()


def _socks(machine: str) -> list[tuple[int, int, str, int, str]]:
    """(port, pid, comm, uid, hex address) of every listening TCP socket, as the student sees them."""
    res = _docker("exec", "-u", "student", "-i", machine, "python3", "-", "list", check=False, timeout=60, stdin=_PROCS)
    rows = []
    for line in res.stdout.splitlines():
        port, pid, comm, uid, addr = line.split()
        rows.append((int(port), int(pid), comm, int(uid), addr))
    return rows


def _by_comm(machine: str, comm: str):
    return [s for s in _socks(machine) if s[2] == comm]


def _one(machine: str, comm: str):
    found = _by_comm(machine, comm)
    ports = {s[0] for s in found}
    assert len(ports) == 1 and len({s[1] for s in found}) == 1, f"expected one `{comm}` service: {found}"
    return found[0]


def _stop_port(machine: str, port: int):
    _docker("exec", "-u", "student", "-i", machine, "python3", "-", "kill", str(port), check=False, timeout=60, stdin=_PROCS)
    time.sleep(0.3)


def _wait_port(machine: str, port: int, up: bool, timeout: float = 10.0):
    end = time.time() + timeout
    while time.time() < end:
        if bool([s for s in _socks(machine) if s[0] == port]) == up:
            return
        time.sleep(0.2)
    raise AssertionError(f"port {port} never became {'open' if up else 'closed'}")


def _start_fake(machine: str, port: int, page: str = "orders ok"):
    """A student-run static server (a fake service) on a port."""
    _try(
        machine,
        f"mkdir -p /tmp/fake-{port} && printf '%s\\n' '{page}' > /tmp/fake-{port}/index.html\n"
        f"cd /tmp/fake-{port} && setsid nohup python3 -m http.server {port} --bind 127.0.0.1 >/dev/null 2>&1 </dev/null &\n"
        "sleep 1\n",
    )
    _wait_port(machine, port, True)


def _page(machine: str, port: int) -> str:
    return _out(machine, f"curl -s -m 5 http://127.0.0.1:{port}/")


def _put(machine: str, name: str, content: str):
    """Write a file of the student with the content exactly (a quoted heredoc: backslashes stay)."""
    _try(machine, f"cat > ~/{name} <<'EOS_680'\n{content}\nEOS_680\n")


def _forgeries(right: str) -> list[tuple[str, str]]:
    return [
        ("a forged PASS line after the right value", f"{right}\nPASS 1\nPASS 2\c"),
        ("backslash-n and backslash-c inside one line", f"{right}\nPASS 1\nPASS 2\c"),
        ("the right value twice on two lines", f"{right}\n{right}"),
        ("the right value and a second word", f"{right} 8080"),
    ]


# ---------------------------------------------------------------- docker: every task


@pytest.mark.docker
@pytest.mark.parametrize("task_id", HANDS_ON_IDS)
def test_check_fails_with_a_hint_per_failing_item_on_a_fresh_machine(machine, task_id):
    _setup(machine, task_id)
    _assert_failing_with_hints(machine, task_id, "after setup on a fresh machine")


@pytest.mark.docker
@pytest.mark.parametrize("task_id", HANDS_ON_IDS)
def test_check_never_passes_on_an_untouched_machine(machine, task_id):
    res, parsed = _check(machine, task_id)
    assert res.returncode != 0 and not parsed.passed, res.stdout


@pytest.mark.docker
@pytest.mark.parametrize("task_id", HANDS_ON_IDS)
def test_setup_is_idempotent(machine, task_id):
    _setup(machine, task_id)
    _setup(machine, task_id)
    _assert_failing_with_hints(machine, task_id, "after setup ran twice")


@pytest.mark.docker
@pytest.mark.parametrize("task_id", HANDS_ON_IDS)
def test_setup_alone_builds_the_starting_state_and_solution_passes_every_item(machine, task_id):
    _setup(machine, task_id)  # fresh container: only this task's setup (self-contained)
    _solve(machine, task_id)
    _assert_passing(machine, task_id, "the solution")


@pytest.mark.docker
@pytest.mark.parametrize("task_id", HANDS_ON_IDS)
def test_setup_run_again_after_the_solution_restores_the_starting_state(machine, task_id):
    _setup(machine, task_id)
    _solve(machine, task_id)
    _setup(machine, task_id)  # Restart task / Next: setup on the student's current machine
    _assert_failing_with_hints(machine, task_id, "after setup re-ran over a solved task")


@pytest.mark.docker
def test_the_whole_lab_in_order_passes_every_check_with_the_solutions(machine):
    for task_id in HANDS_ON_IDS:
        _setup(machine, task_id)
        _solve(machine, task_id)
        _assert_passing(machine, task_id, "the solution in a full run")
    for task_id in ("01-inventory-port", "02-port-9090-pid", "03-nc-8081", "06-orders-fix"):
        _assert_passing(machine, task_id, f"{task_id} after the whole lab")


@pytest.mark.docker
@pytest.mark.parametrize("task_id", HANDS_ON_IDS)
def test_the_check_ignores_the_commands_in_the_shell_history(machine, task_id):
    _setup(machine, task_id)
    solution = _text(_task_dir(task_id) / "solution.sh")
    r = _student(
        machine,
        "cat > ~/.bash_history <<'EOS_680'\n" + solution + "\nEOS_680\n"
        "echo 'ss -ltnp; nc -zv 127.0.0.1 8081; curl http://127.0.0.1:5050/' >> ~/.bash_history\n",
    )
    assert r.returncode == 0, r.stderr
    _assert_failing_with_hints(machine, task_id, "with the commands only in the shell history")


@pytest.mark.docker
@pytest.mark.parametrize("task_id", sorted(ANSWER_TASKS))
def test_answer_files_cannot_forge_check_lines_or_carry_extra_lines(machine, task_id):
    fname = ANSWER_TASKS[task_id]
    _setup(machine, task_id)
    _solve(machine, task_id)
    right = _out(machine, f"cat ~/{fname}")
    for why, payload in _forgeries(right):
        _put(machine, fname, payload)
        _assert_failing_with_hints(machine, task_id, f"for {why}")


@pytest.mark.docker
def test_a_reply_file_cannot_forge_check_lines(machine):
    _setup(machine, "06-orders-fix")
    _solve(machine, "06-orders-fix")
    body = _page(machine, 5050)
    _put(machine, "orders-reply.txt", f"{body}\nPASS 2\nPASS 3\\c")
    _assert_failing_with_hints(machine, "06-orders-fix", "for a reply file with forged lines")


# ---------------------------------------------------------------- docker: 01 inventory port (the trap)


@pytest.mark.docker
def test_01_setup_starts_inventory_and_inventory_db_on_two_ports_as_student(machine):
    _setup(machine, "01-inventory-port")
    inv, db = _one(machine, "inventory"), _one(machine, "inventory-db")
    student_uid = int(_root(machine, "id -u student").stdout.strip())
    assert inv[0] != db[0], "the two similar services must listen on different ports"
    for s in (inv, db):
        assert 20000 <= s[0] <= 29999, f"port {s[0]} is outside 20000..29999"
        assert s[3] == student_uid, f"{s[2]} must run as student, not root"
    assert not _root(machine, f"test -e {HOME}/port.txt && echo x").stdout.strip(), "port.txt must not pre-exist"


@pytest.mark.docker
def test_01_ss_lists_the_services_for_the_student(machine):
    _setup(machine, "01-inventory-port")
    inv = _one(machine, "inventory")
    out = _student(machine, "ss -H -ltn").stdout
    assert f":{inv[0]}" in out, f"`ss -ltn` does not list the inventory port {inv[0]}:\n{out}"
    named = _student(machine, "ss -H -ltnp").stdout
    assert re.search(r'\(\("inventory",pid=\d+', named), f"`ss -ltnp` must show the name `inventory`:\n{named}"
    assert re.search(r'\(\("inventory-db",pid=\d+', named)


@pytest.mark.docker
def test_01_setup_twice_leaves_one_of_each_service(machine):
    _setup(machine, "01-inventory-port")
    _setup(machine, "01-inventory-port")
    _one(machine, "inventory")
    _one(machine, "inventory-db")


@pytest.mark.docker
def test_01_the_solution_writes_the_port_of_inventory_alone(machine):
    _setup(machine, "01-inventory-port")
    _solve(machine, "01-inventory-port")
    got = _root(machine, f"cat {HOME}/port.txt").stdout.strip()
    assert got == str(_one(machine, "inventory")[0])
    assert got != str(_one(machine, "inventory-db")[0])


@pytest.mark.docker
def test_01_wrong_values_fail_with_a_hint_and_the_trap_hint_names_inventory_db(machine):
    _setup(machine, "01-inventory-port")
    inv, db = _one(machine, "inventory")[0], _one(machine, "inventory-db")[0]
    _try(machine, f"echo {db} > ~/port.txt")
    _, parsed = _assert_failing_with_hints(machine, "01-inventory-port", "for the port of inventory-db")
    assert "inventory-db" in _hints(parsed), "the hint must say that this is the port of inventory-db"
    attempts = [
        ("a port nobody listens on", lambda i, d: f"echo {i + 1 if i + 1 != d else i + 2} > ~/port.txt"),
        ("a word with the port", lambda i, d: f"echo inventory:{i} > ~/port.txt"),
        ("the port and a second port on one line", lambda i, d: f"echo '{i} {d}' > ~/port.txt"),
        ("the port, then a second line", lambda i, d: f"printf '{i}\n{d}\n' > ~/port.txt"),
        ("a backslash after the port", lambda i, d: f"printf '%s\\\\c\n' {i} > ~/port.txt"),
        ("an empty file", lambda i, d: ": > ~/port.txt"),
    ]
    for why, make in attempts:
        _setup(machine, "01-inventory-port")
        inv, db = _one(machine, "inventory")[0], _one(machine, "inventory-db")[0]
        _try(machine, make(inv, db))
        _assert_failing_with_hints(machine, "01-inventory-port", f"for {why}")


@pytest.mark.docker
def test_01_a_stale_port_of_an_earlier_setup_fails(machine):
    _setup(machine, "01-inventory-port")
    _solve(machine, "01-inventory-port")
    old = _out(machine, "cat ~/port.txt")
    _setup(machine, "01-inventory-port")
    if str(_one(machine, "inventory")[0]) == old:
        pytest.skip("the new random port equals the old one")
    _put(machine, "port.txt", old)
    _assert_failing_with_hints(machine, "01-inventory-port", "with the port of an earlier run")


@pytest.mark.docker
def test_01_equally_correct_alternatives_pass(machine):
    _alternative(
        machine, "01-inventory-port",
        "ss -H -ltnp | grep '\"inventory\"' | awk '{print $4}' | sed 's/.*://' | tr -d '\\n' > ~/port.txt",
        "ss piped through awk and sed, no trailing newline",
    )
    _alternative(
        machine, "01-inventory-port",
        "printf '  %s  \\n' \"$(sudo ss -H -ltnp | grep '\"inventory\"' | awk '{print $4}' | sed 's/.*://')\" > ~/port.txt",
        "sudo ss, spaces around the value",
    )


# ---------------------------------------------------------------- docker: 02 the PID behind port 9090


@pytest.mark.docker
def test_02_setup_runs_one_reports_service_on_9090_as_student_with_a_new_pid_each_time(machine):
    _setup(machine, "02-port-9090-pid")
    first = _by_comm(machine, "reports")
    student_uid = int(_root(machine, "id -u student").stdout.strip())
    assert [s[0] for s in first] == [9090] and first[0][3] == student_uid
    assert [s for s in _socks(machine) if s[0] == 9090] == first, "only `reports` may hold 9090"
    _setup(machine, "02-port-9090-pid")
    second = _by_comm(machine, "reports")
    assert len(second) == 1 and second[0][0] == 9090, "two setups must leave one reports service"
    assert second[0][1] != first[0][1], "setup must restart the service (a new PID)"


@pytest.mark.docker
def test_02_the_solution_writes_the_pid_alone(machine):
    _setup(machine, "02-port-9090-pid")
    _solve(machine, "02-port-9090-pid")
    assert _out(machine, "cat ~/pid.txt") == str(_by_comm(machine, "reports")[0][1])


@pytest.mark.docker
def test_02_wrong_values_fail_with_a_hint(machine):
    _setup(machine, "01-inventory-port")
    for why in ("the port number", "the pid of the student's own shell", "PID 1", "the pid of another service", "a pid that does not exist"):
        _setup(machine, "02-port-9090-pid")
        pid = _by_comm(machine, "reports")[0][1]
        other = _by_comm(machine, "inventory")[0][1]
        code = {
            "the port number": "echo 9090 > ~/pid.txt",
            "the pid of the student's own shell": "echo $$ > ~/pid.txt",
            "PID 1": "echo 1 > ~/pid.txt",
            "the pid of another service": f"echo {other} > ~/pid.txt",
            "a pid that does not exist": f"echo {pid + 100000} > ~/pid.txt",
        }[why]
        _try(machine, code)
        _assert_failing_with_hints(machine, "02-port-9090-pid", f"for {why}")


@pytest.mark.docker
def test_02_a_pid_from_before_the_restart_fails(machine):
    _setup(machine, "02-port-9090-pid")
    _solve(machine, "02-port-9090-pid")
    old = _out(machine, "cat ~/pid.txt")
    _setup(machine, "02-port-9090-pid")
    _put(machine, "pid.txt", old)
    _assert_failing_with_hints(machine, "02-port-9090-pid", "with the PID of the process that setup replaced")


@pytest.mark.docker
def test_02_equally_correct_alternatives_pass(machine):
    _alternative(
        machine, "02-port-9090-pid",
        "ss -H -ltnp 'sport = :9090' | grep -o 'pid=[0-9]*' | cut -d= -f2 | tr -d '\\n' > ~/pid.txt",
        "ss with a filter and cut, no trailing newline",
    )
    _alternative(
        machine, "02-port-9090-pid",
        "printf ' %s \\n' \"$(pgrep -x reports)\" > ~/pid.txt",
        "pgrep by name, spaces around the value",
    )


# ---------------------------------------------------------------- docker: 03 nc and port 8081


@pytest.mark.docker
def test_03_setup_leaves_8081_closed_and_clears_the_answer(machine):
    _start_fake(machine, 8081)
    _setup(machine, "03-nc-8081")
    assert not [s for s in _socks(machine) if s[0] == 8081], "8081 must be closed after setup"
    assert _student(machine, "nc -z -w 2 127.0.0.1 8081").returncode != 0
    assert not _root(machine, f"test -e {HOME}/nc-8081.txt && echo x").stdout.strip()


@pytest.mark.docker
def test_03_the_solution_says_closed(machine):
    _setup(machine, "03-nc-8081")
    _solve(machine, "03-nc-8081")
    assert _out(machine, "cat ~/nc-8081.txt") == "closed"


@pytest.mark.docker
def test_03_the_answer_follows_the_live_state_of_the_port(machine):
    _setup(machine, "03-nc-8081")
    _start_fake(machine, 8081)
    _put(machine, "nc-8081.txt", "open")
    _assert_passing(machine, "03-nc-8081", "`open` while a server listens on 8081")
    _put(machine, "nc-8081.txt", "closed")
    _assert_failing_with_hints(machine, "03-nc-8081", "for `closed` while a server listens on 8081")
    _stop_port(machine, 8081)
    _wait_port(machine, 8081, False)
    _assert_passing(machine, "03-nc-8081", "`closed` after the server stopped")
    _put(machine, "nc-8081.txt", "open")
    _assert_failing_with_hints(machine, "03-nc-8081", "for `open` while 8081 is closed")


@pytest.mark.docker
def test_03_other_words_fail_with_a_hint(machine):
    for why, text in (
        ("Succeeded", "Connection to 127.0.0.1 8081 port [tcp/*] succeeded!"),
        ("yes", "yes"),
        ("a capital letter", "Closed"),
        ("both words", "open closed"),
        ("both words on two lines", "closed\nopen"),
    ):
        _setup(machine, "03-nc-8081")
        _put(machine, "nc-8081.txt", text)
        _assert_failing_with_hints(machine, "03-nc-8081", f"for {why}")


@pytest.mark.docker
def test_03_equally_correct_alternatives_pass(machine):
    _alternative(
        machine, "03-nc-8081",
        "if nc -z -w 2 127.0.0.1 8081; then printf open; else printf closed; fi > ~/nc-8081.txt",
        "nc -z with if/else, no trailing newline",
    )
    _alternative(machine, "03-nc-8081", "printf '  closed  \\n' > ~/nc-8081.txt", "spaces around the word")


# ---------------------------------------------------------------- docker: 05 the log and the blocker


@pytest.mark.docker
def test_05_setup_makes_orders_refuse_and_leaves_a_log_that_explains_it(machine):
    _setup(machine, "05-orders-log")
    student_uid = int(_root(machine, "id -u student").stdout.strip())
    holder = [s for s in _socks(machine) if s[0] == 5050]
    assert [(s[2], s[3]) for s in holder] == [("batch-sync", student_uid)], f"5050 must be held by batch-sync: {holder}"
    assert not _by_comm(machine, "orders"), "orders must not be running"
    assert _student(machine, "curl -s -m 3 http://127.0.0.1:5050/ | grep -c '^orders ok'").stdout.strip() == "0"
    log = _out(machine, f"cat {LOG}")
    assert "5050" in log and "Address already in use" in log, f"the log does not explain the refusal:\n{log}"
    assert "batch-sync" not in log, "the log must not hand out the holder's name (the student finds it with ss)"
    assert _root(machine, f"stat -c %U {LOG}").stdout.strip() == "root", "the log is root-owned"
    assert not _root(machine, f"test -e {HOME}/blocker.txt && echo x").stdout.strip()


@pytest.mark.docker
def test_05_setup_twice_leaves_one_blocker_and_no_orders(machine):
    _setup(machine, "05-orders-log")
    _setup(machine, "05-orders-log")
    assert [s[1] for s in _by_comm(machine, "batch-sync")] and len({s[1] for s in _by_comm(machine, "batch-sync")}) == 1
    assert not _by_comm(machine, "orders")


@pytest.mark.docker
def test_05_the_solution_writes_the_name_of_the_holder_alone(machine):
    _setup(machine, "05-orders-log")
    _solve(machine, "05-orders-log")
    assert _out(machine, "cat ~/blocker.txt") == "batch-sync"


@pytest.mark.docker
def test_05_wrong_names_fail_with_a_hint(machine):
    for why, text in (
        ("the service that is down", "orders"),
        ("the port", "5050"),
        ("the pid of the holder", "$PID"),
        ("a different case", "Batch-Sync"),
        ("the name and a second word", "batch-sync stale"),
        ("a backslash after the name", "batch-sync\\c"),
        ("the name, then a second line", "batch-sync\nbatch-sync"),
        ("an empty file", ""),
    ):
        _setup(machine, "05-orders-log")
        if text == "$PID":
            text = str(_by_comm(machine, "batch-sync")[0][1])
        _put(machine, "blocker.txt", text)
        _assert_failing_with_hints(machine, "05-orders-log", f"for {why}")


@pytest.mark.docker
def test_05_equally_correct_alternatives_pass(machine):
    _alternative(
        machine, "05-orders-log",
        "ss -H -ltnp 'sport = :5050' | grep -o '((\"[^\"]*\"' | tr -d '(\"' | tr -d '\\n' > ~/blocker.txt",
        "ss filtered by port, name cut out, no trailing newline",
    )
    _alternative(machine, "05-orders-log", "printf '  batch-sync  \\n' > ~/blocker.txt", "typed by hand after reading ss")


# ---------------------------------------------------------------- docker: 06 fix orders and prove it with curl


@pytest.mark.docker
def test_06_setup_builds_the_broken_state_and_the_start_script(machine):
    _setup(machine, "06-orders-fix")
    assert [s[2] for s in _socks(machine) if s[0] == 5050] == ["batch-sync"]
    assert not _by_comm(machine, "orders")
    assert _student(machine, "test -x ~/orders/start.sh && echo yes").stdout.strip() == "yes"
    log = _out(machine, f"cat {LOG}")
    assert "5050" in log and "Address already in use" in log
    assert not _root(machine, f"test -e {HOME}/orders-reply.txt && echo x").stdout.strip()


@pytest.mark.docker
def test_06_start_script_refuses_while_the_port_is_held(machine):
    _setup(machine, "06-orders-fix")
    r = _student(machine, "~/orders/start.sh")
    assert r.returncode != 0, "start.sh must fail while batch-sync holds 5050"
    assert not _by_comm(machine, "orders")


@pytest.mark.docker
def test_06_the_solution_leaves_orders_answering_as_student_and_the_blocker_gone(machine):
    _setup(machine, "06-orders-fix")
    _solve(machine, "06-orders-fix")
    student_uid = int(_root(machine, "id -u student").stdout.strip())
    assert [(s[0], s[2], s[3]) for s in _socks(machine) if s[0] == 5050] == [(5050, "orders", student_uid)]
    assert not _by_comm(machine, "batch-sync")
    body = _page(machine, 5050)
    assert body.startswith("orders ok") and len(body) > len("orders ok"), body
    assert _out(machine, "cat ~/orders-reply.txt") == body


@pytest.mark.docker
def test_06_the_page_token_is_fresh_on_every_setup(machine):
    _setup(machine, "06-orders-fix")
    _solve(machine, "06-orders-fix")
    first = _page(machine, 5050)
    _setup(machine, "06-orders-fix")
    _solve(machine, "06-orders-fix")
    assert _page(machine, 5050) != first


KILL_BLOCKER = "pkill -x batch-sync; sleep 1"


@pytest.mark.docker
def test_06_attempts_that_do_not_fix_the_service_fail(machine):
    _setup(machine, "06-orders-fix")
    _try(machine, "curl -s -m 5 http://127.0.0.1:5050/ > ~/orders-reply.txt")
    _assert_failing_with_hints(machine, "06-orders-fix", "for the reply of batch-sync")
    _near_miss(machine, "06-orders-fix", KILL_BLOCKER, "the blocker stopped but orders not started")
    _near_miss(
        machine, "06-orders-fix", f"{KILL_BLOCKER}\n~/orders/start.sh",
        "orders started but no reply saved",
    )
    _near_miss(
        machine, "06-orders-fix", f"{KILL_BLOCKER}\n~/orders/start.sh\ncurl -si -m 5 http://127.0.0.1:5050/ > ~/orders-reply.txt",
        "the reply with its HTTP headers (curl -i)",
    )
    _near_miss(
        machine, "06-orders-fix", f"{KILL_BLOCKER}\n~/orders/start.sh\necho 'orders ok' > ~/orders-reply.txt",
        "a reply written by hand",
    )


@pytest.mark.docker
def test_06_a_fake_server_on_5050_does_not_pass(machine):
    _setup(machine, "06-orders-fix")
    _try(machine, KILL_BLOCKER)
    _wait_port(machine, 5050, False)
    _start_fake(machine, 5050, "orders ok")
    _try(machine, "curl -s -m 5 http://127.0.0.1:5050/ > ~/orders-reply.txt")
    _assert_failing_with_hints(machine, "06-orders-fix", "for a fake server that says `orders ok`")


@pytest.mark.docker
def test_06_the_service_stopped_after_the_fix_fails_even_with_the_reply_saved(machine):
    _setup(machine, "06-orders-fix")
    _solve(machine, "06-orders-fix")
    _stop_port(machine, 5050)
    _wait_port(machine, 5050, False)
    _assert_failing_with_hints(machine, "06-orders-fix", "with orders stopped")


@pytest.mark.docker
def test_06_a_stale_reply_of_an_earlier_setup_fails(machine):
    _setup(machine, "06-orders-fix")
    _solve(machine, "06-orders-fix")
    old = _out(machine, "cat ~/orders-reply.txt")
    _setup(machine, "06-orders-fix")
    _try(machine, f"{KILL_BLOCKER}\n~/orders/start.sh")
    _put(machine, "orders-reply.txt", old)
    _assert_failing_with_hints(machine, "06-orders-fix", "with the page of an earlier run")


@pytest.mark.docker
def test_06_equally_correct_alternatives_pass(machine):
    _alternative(
        machine, "06-orders-fix",
        "kill $(ss -H -ltnp 'sport = :5050' | grep -o 'pid=[0-9]*' | cut -d= -f2)\nsleep 1\n"
        "~/orders/start.sh\ncurl -s -m 5 localhost:5050 > ~/orders-reply.txt",
        "kill by the pid from ss, localhost and no slash",
    )
    _alternative(
        machine, "06-orders-fix",
        "pkill batch-sync; sleep 1; ~/orders/start.sh >/dev/null\necho \"$(curl -s -m 5 http://127.0.0.1:5050/)\" > ~/orders-reply.txt",
        "pkill, a reply saved through echo (adds a trailing newline)",
    )
