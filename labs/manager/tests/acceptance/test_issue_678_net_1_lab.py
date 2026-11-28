"""Acceptance tests for issue #678: lab content "Networking 1: HTTP and curl" (en + he), lesson 7 (D191).

The lab does not exist yet; these tests describe it. Choices pinned here (the builder follows them):

Location and metadata
    labs/content/networking/net-1/lab.yaml   course dir `networking`, lab id `net-1` (the validator
    requires id == directory name). module: 1, lesson: 7 (D191 v2 numbering), image: term,
    sudo: false (explicit key), status: published (D191: ships published once the review approves
    and the docker solve test passes), no internet.

Tasks (ids and order are fixed; kind in brackets; what the student leaves behind in brackets)
    01-server        [hands-on]  `python3 -m http.server 8080` running in the background and serving
                                 ~/site. State: a process listens on TCP 8080 AND a page that only
                                 ~/site holds is served from it. Setup makes ~/site (index.html,
                                 data.json, ...) and leaves 8080 free (it stops a server that runs).
    02-status-codes  [hands-on]  ~/status-ok.txt holds `200` (status of /index.html) and
                                 ~/status-missing.txt holds `404` (a page that does not exist). Only the
                                 number, surrounding whitespace and a CRLF are ignored. The check asks the
                                 live server too. Setup brings the server up (self-contained).
    03-headers       [hands-on]  ~/headers.txt: the response headers of `/` and nothing else (the status
                                 line `HTTP/.. 200 ..`, the Content-Type `text/html` of index.html, the
                                 Content-Length of index.html, no body). `curl -sI` and `curl -D` pass,
                                 `curl -i` (headers plus body) and a body only fail.
    04-content-type  [hands-on]  ~/content-type.txt holds the Content-Type of /data.json, the value only:
                                 `application/json` (whitespace/CR around it ignored). The whole header line
                                 `Content-Type: application/json` is NOT accepted (the task text asks for
                                 the value).
    05-post-echo     [hands-on]  POST the JSON body {"lab":"net-1","msg":"hello"} (shown in the task text) with
                                 the header Content-Type: application/json to the echo service that setup
                                 provides, and save the reply as ~/reply.json.
                                 THE ECHO SERVICE (choice of this exam): setup starts it, detached,
                                 not as root, listening on 127.0.0.1 port 9090 only (IPv4). POST /echo
                                 with Content-Type application/json answers 200 and a JSON object
                                 {"id": "<string unique to this service instance>", "echo": <the body sent>};
                                 any other Content-Type answers 415 with a JSON object that has an "error"
                                 key. The check accepts ~/reply.json only if it is valid JSON, "echo" equals
                                 the body of the task text and "id" equals the live service's id. So a
                                 reply written by hand, a 415 reply and a reply to another body all FAIL.
    06-status-quiz   [quiz]      what 200, 301, 404 and 500 mean
    07-https-quiz    [quiz]      HTTP vs HTTPS (HTTPS = HTTP over TLS: encrypted, authenticated server)
    08-localhost-trap [hands-on] ~/pong.txt holds the reply of the service on 127.0.0.1 port 9091.
                                 THE TRAP (D191, how this exam exercises it): setup starts two listeners on
                                 port 9091: the real service on 127.0.0.1 (IPv4 only) answering GET / with
                                 the single line `pong:<token>` (a token unique to this instance), and a
                                 decoy on [::1] answering GET / with another body. `localhost` may resolve to
                                 ::1 first, so `curl localhost:9091` lands on the decoy; the task text names
                                 `127.0.0.1` and the port explicitly, and never says IPv6, `::1` or the trap.
                                 The check takes the real reply live from 127.0.0.1:9091 and requires
                                 ~/pong.txt to hold exactly it (trimmed). The decoy's reply and a
                                 hard-coded `pong` FAIL; the FAIL hint leads to `localhost`, `127.0.0.1` or
                                 IPv6. solution.en.md explains the IPv6 / `::1` / `localhost` story.
                                 Observable contract (mechanism free): after setup,
                                 `curl -s http://localhost:9091/` does NOT print the real reply and
                                 `curl -s http://127.0.0.1:9091/` does.

Files per hands-on task directory (tasks/<id>/): task.en.md, task.he.md, solution.en.md,
    solution.he.md (the validator's files), setup.sh, check.sh, and the exam-only `solution.sh`
    (the commands that solve the task, run as `student` through a login shell). The validator and
    loader ignore it. All .sh files: LF endings, `#!/bin/sh` or `#!/bin/bash` (or `/usr/bin/env`)
    shebang, mode 0755 in git. Every background process that setup.sh or solution.sh starts must
    detach fully (setsid, stdin from /dev/null, stdout and stderr redirected) or `docker exec`
    never returns. The task texts show the exact names and values in code spans (TEXT_PINS below).

check.sh protocol (spec section 7, asserted exactly): runs as root (no capabilities beyond
    SETUID/SETGID), finishes in under 20 s (a server that never answers must not hang it: curl
    needs a time limit), prints `PASS <n>` or `FAIL <n> <hint>` per checklist item (N >= 2), exit 0
    only when all pass, every FAIL line carries a non-empty hint. setup.sh: run as root, exit 0,
    self-contained, idempotent, and replaces the state its task is about (after the solution a
    second run brings the task back to failing: it removes the student's files and, for 01, the
    server). A check looks at state, not at the commands typed. A check never changes the student's
    home folder.

Docker tests (marker `docker`, run in CI) start `lf-lab-term` under the production flags (the #12
    exam's `machine` fixture: --network none, loopback only). They are parametrised over the fixed
    task ids, so a missing lab fails them; they skip only when Docker itself is absent.
Students and data are fake; no real student data in fixtures.
"""
# ruff: noqa: F811, E501
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_issue_12_linux_1 import (  # noqa: F401  (fixtures are used by name)
    _as_root,
    _docker,
    _text,
    docker_available,
    image,
    machine,
    runtime_flag,
)

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
CONTENT_ROOT = REPO_ROOT / "labs" / "content"
LAB_DIR = CONTENT_ROOT / "networking" / "net-1"
LAB_ID = "net-1"
LAB_KEY = "networking/net-1"

TASKS = [
    ("01-server", "hands-on"),
    ("02-status-codes", "hands-on"),
    ("03-headers", "hands-on"),
    ("04-content-type", "hands-on"),
    ("05-post-echo", "hands-on"),
    ("06-status-quiz", "quiz"),
    ("07-https-quiz", "quiz"),
    ("08-localhost-trap", "hands-on"),
]
QUIZ_IDS = [t for t, k in TASKS if k == "quiz"]
HANDS_ON_IDS = [t for t, k in TASKS if k == "hands-on"]
# the file(s) in the student's home that each task is about (setup must remove them)
STUDENT_FILES = {
    "02-status-codes": ["status-ok.txt", "status-missing.txt"],
    "03-headers": ["headers.txt"],
    "04-content-type": ["content-type.txt"],
    "05-post-echo": ["reply.json"],
    "08-localhost-trap": ["pong.txt"],
}
# exact names and values that the en and he task texts show in code spans
TEXT_PINS = {
    "01-server": ["python3 -m http.server 8080", "~/site"],
    "02-status-codes": ["status-ok.txt", "status-missing.txt", "/index.html", "8080"],
    "03-headers": ["headers.txt", "8080"],
    "04-content-type": ["content-type.txt", "/data.json"],
    "05-post-echo": ["reply.json", "127.0.0.1:9090/echo", '{"lab":"net-1","msg":"hello"}'],
    "08-localhost-trap": ["127.0.0.1", "9091", "pong.txt"],
}
HEBREW = re.compile("[֐-׿]")
POST_BODY = '{"lab":"net-1","msg":"hello"}'


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
    return len(next(t for t in _lab().tasks if t.id == task_id).checklist)


# ---------------------------------------------------------------- non-docker: validator and shape


def test_validator_accepts_the_lab_with_no_errors():
    res = _load()
    assert LAB_KEY not in res.errors, res.errors.get(LAB_KEY)
    assert LAB_ID in res.labs, f"{LAB_ID} not loaded; all errors: {res.errors}"


def test_lab_is_module_1_lesson_7_image_term():
    lab = _lab()
    assert (lab.module, lab.lesson) == (1, 7)
    assert lab.image == "term"
    assert lab.path.name == LAB_ID
    assert lab.path.parent.name == "networking"


def test_status_is_published():
    assert _lab().status == "published"
    assert _raw().get("status") == "published"


def test_sudo_is_false_explicitly_and_internet_is_off():
    raw = _raw()
    assert raw.get("sudo") is False, "lab.yaml must say `sudo: false`"
    assert _lab().sudo is False
    assert not raw.get("internet", False)
    assert not raw.get("allow_domains", [])


def test_task_order_and_kinds_match_the_plan():
    got = [(t.id, t.kind) for t in _lab().tasks]
    assert got == TASKS
    assert 6 <= len(got) <= 10


def test_lab_is_about_45_minutes():
    assert _raw().get("time_limit_min") == 45


def test_lab_title_and_description_have_en_and_he():
    lab = _lab()
    assert lab.title.get("en", "").strip() and lab.title.get("he", "").strip()
    assert lab.description.get("en", "").strip() and lab.description.get("he", "").strip()
    assert HEBREW.search(lab.title["he"]) and HEBREW.search(lab.description["he"])
    assert not HEBREW.search(lab.title["en"] + lab.description["en"])


@pytest.mark.parametrize("task_id", QUIZ_IDS)
def test_quiz_has_en_and_he_text_with_real_hebrew(task_id):
    task = next(t for t in _lab().tasks if t.id == task_id)
    assert task.question.get("en", "").strip()
    assert HEBREW.search(task.question.get("he", "")), "he question must be in Hebrew script"
    assert not HEBREW.search(task.question["en"])
    assert len(task.options) >= 3
    for opt in task.options:
        assert opt.get("en", "").strip() and opt.get("he", "").strip()
        assert not HEBREW.search(opt["en"])
    assert any(HEBREW.search(o["he"]) for o in task.options), "he options carry Hebrew words"
    en = [o["en"].strip() for o in task.options]
    assert len(set(en)) == len(en), "options must be distinct"
    raw = _raw_task(task_id)
    assert isinstance(raw.get("correct"), int) and raw["correct"] == task.correct
    assert 0 <= task.correct < len(task.options)


@pytest.mark.parametrize("task_id", HANDS_ON_IDS)
def test_hands_on_checklist_and_texts_are_bilingual(task_id):
    task = next(t for t in _lab().tasks if t.id == task_id)
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


@pytest.mark.parametrize("task_id", sorted(TEXT_PINS))
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


def test_solution_scripts_use_no_sudo_and_name_the_port_or_the_file():
    for task_id in HANDS_ON_IDS:
        code = _text(_task_dir(task_id) / "solution.sh")
        assert "sudo" not in code
        assert "curl" in code or "http.server" in code, f"{task_id}/solution.sh does not use curl or http.server"


def test_the_trap_is_not_given_away_in_the_task_text_but_explained_in_the_solution():
    d = _task_dir("08-localhost-trap")
    for name in ("task.en.md", "task.he.md"):
        text = _text(d / name).lower()
        assert "ipv6" not in text and "::1" not in text, f"task 08 {name} must not hint at IPv6"
    sol = _text(d / "solution.en.md").lower()
    assert "ipv6" in sol and "::1" in sol and "localhost" in sol, (
        "solution 08 must explain localhost resolving to ::1 (IPv6) first"
    )
    setup = _text(d / "setup.sh")
    assert "9091" in setup and "127.0.0.1" in setup, "setup 08 must start the IPv4-only service on 9091"


def test_echo_service_setup_names_the_ipv4_loopback_and_port():
    setup = _text(_task_dir("05-post-echo") / "setup.sh")
    assert "9090" in setup and "127.0.0.1" in setup


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


# ---------------------------------------------------------------- non-docker: the quizzes


def test_status_quiz_covers_200_301_404_500_and_one_option_is_right():
    task = next(t for t in _lab().tasks if t.id == "06-status-quiz")
    q = task.question["en"]
    for code in ("200", "301", "404", "500"):
        assert code in q or all(code in o["en"] for o in task.options), f"the quiz must be about {code}"
    for i, o in enumerate(task.options):
        for code in ("200", "301", "404", "500"):
            assert code in o["en"] and code in o["he"], f"option {i} must show {code} in en and he"
    right = [
        i for i, o in enumerate(task.options)
        if re.search(r"200\D{0,12}(ok|success)", o["en"], re.I)
        and re.search(r"301\D{0,25}(redirect|moved)", o["en"], re.I)
        and re.search(r"404\D{0,25}(not found|missing|does not exist|no such)", o["en"], re.I)
        and re.search(r"500\D{0,25}(server error|server .{0,12}(failed|broke|fault)|error on the server|internal)", o["en"], re.I)
    ]
    assert right == [task.correct], f"exactly the correct option explains all four codes; got {right}"


def test_https_quiz_is_about_http_vs_https_and_one_option_is_right():
    task = next(t for t in _lab().tasks if t.id == "07-https-quiz")
    for i, o in enumerate(task.options):
        assert "HTTPS" in o["en"] and "HTTP" in o["en"], f"option {i} must name HTTP and HTTPS"
        assert "HTTPS" in o["he"] and "HTTP" in o["he"], f"he option {i} must name HTTP and HTTPS"
    right = [
        i for i, o in enumerate(task.options)
        if re.search(r"encrypt", o["en"], re.I)
        and re.search(r"HTTPS[^.;]{0,60}encrypt|encrypt[^.;]{0,60}HTTPS", o["en"], re.I)
        and re.search(r"HTTP[^S][^.;]{0,60}(plain|not encrypted|unencrypted|readable)", o["en"], re.I)
    ]
    assert right == [task.correct], (
        f"exactly the correct option says HTTPS is encrypted and plain HTTP is not; got {right}"
    )


# ---------------------------------------------------------------- docker helpers

HOME = "/home/student"
SITE = f"{HOME}/site"

_LISTENERS = r"""
import os, sys
port = int(sys.argv[1])
inodes = {}
for f in ("/proc/net/tcp", "/proc/net/tcp6"):
    try:
        lines = open(f).read().splitlines()[1:]
    except OSError:
        continue
    for line in lines:
        p = line.split()
        addr, prt = p[1].rsplit(":", 1)
        if p[3] == "0A" and int(prt, 16) == port:
            inodes[p[9]] = addr
out = set()
for pid in [d for d in os.listdir("/proc") if d.isdigit()]:
    try:
        for fd in os.listdir(f"/proc/{pid}/fd"):
            t = os.readlink(f"/proc/{pid}/fd/{fd}")
            if t.startswith("socket:[") and t[8:-1] in inodes:
                out.add((inodes[t[8:-1]], os.stat(f"/proc/{pid}").st_uid))
    except OSError:
        pass
for a, u in sorted(out):
    print(a, u)
"""

_KILL_SERVERS = r"""
import os, signal
me = os.getpid()
for pid in [d for d in os.listdir("/proc") if d.isdigit()]:
    try:
        cmd = open(f"/proc/{pid}/cmdline").read()
    except OSError:
        continue
    if int(pid) != me and "http.server" in cmd and "python" in cmd:
        try:
            os.kill(int(pid), signal.SIGKILL)
        except OSError:
            pass
"""


def _student(machine: str, code: str, timeout: int = 60):
    """Run shell code as `student` through a login shell (as the terminal does)."""
    return _docker(
        "exec", "-i", machine, "su", "-", "student", "-c", "bash -s",
        check=False, timeout=timeout, stdin=code,
    )


def _root_py(machine: str, code: str, *args: str):
    return _docker("exec", "-i", machine, "python3", "-", *args, check=False, timeout=60, stdin=code)


def _listeners(machine: str, port: int) -> list[tuple[str, int]]:
    """(hex address, uid) of the listening sockets on a TCP port."""
    res = _root_py(machine, _LISTENERS, str(port))
    return [(a, int(u)) for a, u in (line.split() for line in res.stdout.splitlines())]


def _stop_http_servers(machine: str):
    _root_py(machine, _KILL_SERVERS)
    time.sleep(0.5)


def _setup(machine: str, task_id: str):
    res = _as_root(machine, _task_dir(task_id) / "setup.sh")
    assert res.returncode == 0, f"setup.sh of {task_id} failed ({res.returncode}): {res.stderr}"
    return res


def _solve(machine: str, task_id: str):
    sol = _student(machine, _text(_task_dir(task_id) / "solution.sh"))
    assert sol.returncode == 0, f"solution.sh of {task_id} failed: {sol.stdout}\n{sol.stderr}"


def _check(machine: str, task_id: str):
    from app.content import parse_check_output

    start = time.time()
    res = _as_root(machine, _task_dir(task_id) / "check.sh", timeout=60)
    assert time.time() - start < 20, "check.sh took longer than the 20 s limit"
    return res, parse_check_output(res.stdout, res.returncode, _n_items(task_id))


def _assert_failing_with_hints(machine: str, task_id: str, where: str):
    res, parsed = _check(machine, task_id)
    n = _n_items(task_id)
    assert res.returncode != 0, f"{task_id}: check.sh exits 0 {where}:\n{res.stdout}"
    assert not parsed.passed
    assert set(parsed.items) == set(range(1, n + 1)), (
        f"{task_id}: expected one PASS/FAIL line for each of items 1..{n} {where}:\n{res.stdout}"
    )
    failing = [i for i, it in parsed.items.items() if not it.passed]
    assert failing, f"{task_id}: no FAIL line {where}:\n{res.stdout}"
    for i in failing:
        assert parsed.items[i].hint, f"{task_id}: FAIL {i} has no hint {where}:\n{res.stdout}"
    for line in res.stdout.splitlines():
        if line.startswith("FAIL"):
            assert re.fullmatch(r"FAIL \d+ \S.*", line.strip()), f"bad FAIL line: {line!r}"


def _assert_passing(machine: str, task_id: str, what: str):
    res, parsed = _check(machine, task_id)
    n = _n_items(task_id)
    assert set(parsed.items) == set(range(1, n + 1)), res.stdout
    assert "FAIL" not in res.stdout, f"{task_id}: {what} was refused:\n{res.stdout}"
    assert res.returncode == 0 and parsed.passed, f"{task_id}: {what}:\n{res.stdout}"


def _hints(machine: str, task_id: str) -> str:
    res, parsed = _check(machine, task_id)
    return " ".join(it.hint or "" for it in parsed.items.values()).lower()


def _out(machine: str, code: str) -> tuple[str, int]:
    res = _student(machine, f"cd ~ && {code}")
    return res.stdout, res.returncode


def _put(machine: str, name: str, content: str):
    """Write ~/name with exactly `content` (shell printf escapes allowed: \\n, \\r)."""
    assert "'" not in content
    r = _student(machine, f"printf '{content}' > ~/{name}")
    assert r.returncode == 0, r.stderr


def _detached(code: str) -> str:
    return f"setsid nohup {code} </dev/null >/dev/null 2>&1 &"


def _wait_port(machine: str, port: int, seconds: float = 8.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        r = _student(machine, f"curl -s -m 2 -o /dev/null http://127.0.0.1:{port}/")
        if r.returncode == 0:
            return
        time.sleep(0.3)
    raise AssertionError(f"nothing answered on port {port}")


def _own_server(machine: str, directory: str, port: int, extra: str = ""):
    """Start the student's http.server as the student, detached, and wait for it."""
    r = _student(
        machine, f"cd {directory} && {_detached(f'python3 -m http.server {port} {extra}')}\nsleep 0.2"
    )
    assert r.returncode == 0, r.stderr
    _wait_port(machine, port)


def _fresh_after_solution(machine: str, task_id: str):
    _setup(machine, task_id)
    _solve(machine, task_id)


def _echo_reply(machine: str, body: str = POST_BODY) -> str:
    out, rc = _out(
        machine,
        f"curl -s -m 5 -X POST -H 'Content-Type: application/json' -d '{body}' http://127.0.0.1:9090/echo",
    )
    assert rc == 0, "the echo service does not answer on 127.0.0.1:9090"
    return out


def _real_pong(machine: str) -> str:
    out, rc = _out(machine, "curl -s -m 5 http://127.0.0.1:9091/")
    assert rc == 0 and out.strip().startswith("pong:"), f"the real service must say pong:<token>, got {out!r}"
    return out


# ---------------------------------------------------------------- docker: every hands-on task


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
    before = _student(machine, f"cd {SITE} && find . -type f | sort | xargs sha256sum").stdout
    _setup(machine, task_id)
    after = _student(machine, f"cd {SITE} && find . -type f | sort | xargs sha256sum").stdout
    assert before and before == after, "a second setup must not change the files under ~/site"
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
    for name in STUDENT_FILES.get(task_id, []):
        _, rc = _out(machine, f"test -e ~/{name}")
        assert rc != 0, f"setup of {task_id} left the student's ~/{name} in place"


@pytest.mark.docker
@pytest.mark.parametrize("task_id", HANDS_ON_IDS)
def test_setup_makes_the_site_folder_owned_by_student(machine, task_id):
    _setup(machine, task_id)
    out, rc = _out(machine, "test -O ~/site/index.html && test -O ~/site/data.json && echo ok")
    assert out.strip() == "ok" and rc == 0, "setup must create ~/site/index.html and ~/site/data.json for student"


@pytest.mark.docker
@pytest.mark.parametrize("task_id", ["02-status-codes", "03-headers", "04-content-type", "05-post-echo", "08-localhost-trap"])
def test_a_check_never_changes_the_student_home(machine, task_id):
    _fresh_after_solution(machine, task_id)
    cmd = "cd ~ && find . -xdev -type f | sort | xargs -d '\\n' sha256sum"
    before = _student(machine, cmd).stdout
    _assert_passing(machine, task_id, "the solution")
    assert before == _student(machine, cmd).stdout, f"the check of {task_id} changed the home folder"


# ---------------------------------------------------------------- docker: 01 the server


@pytest.mark.docker
def test_server_setup_leaves_port_8080_free_and_site_in_place(machine):
    _setup(machine, "01-server")
    assert _listeners(machine, 8080) == [], "after setup 01 nothing may listen on 8080"
    out, _ = _out(machine, "ls ~/site")
    assert "index.html" in out and "data.json" in out


@pytest.mark.docker
def test_server_setup_stops_a_server_that_runs_already(machine):
    _setup(machine, "01-server")
    _solve(machine, "01-server")
    _assert_passing(machine, "01-server", "the solution")
    _setup(machine, "01-server")
    assert _listeners(machine, 8080) == [], "setup 01 must stop the student's server"


@pytest.mark.docker
def test_server_solution_serves_site_and_runs_as_student_not_root(machine):
    _setup(machine, "01-server")
    _solve(machine, "01-server")
    uids = _listeners(machine, 8080)
    assert uids and all(uid != 0 for _, uid in uids), f"the server must not run as root: {uids}"
    out, rc = _out(machine, "curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/index.html")
    assert (out, rc) == ("200", 0)


@pytest.mark.docker
def test_server_serving_the_home_folder_instead_of_site_fails(machine):
    _setup(machine, "01-server")
    _own_server(machine, "~", 8080)
    _assert_failing_with_hints(machine, "01-server", "for a server that serves ~ and not ~/site")
    assert "site" in _hints(machine, "01-server") or "directory" in _hints(machine, "01-server") or "folder" in _hints(machine, "01-server")


@pytest.mark.docker
def test_server_serving_another_directory_fails(machine):
    _setup(machine, "01-server")
    _student(machine, "mkdir -p /tmp/elsewhere && echo hi > /tmp/elsewhere/index.html")
    _own_server(machine, "/tmp/elsewhere", 8080)
    _assert_failing_with_hints(machine, "01-server", "for a server that serves /tmp/elsewhere")


@pytest.mark.docker
def test_server_on_the_wrong_port_fails(machine):
    _setup(machine, "01-server")
    _own_server(machine, "~/site", 8000)
    _assert_failing_with_hints(machine, "01-server", "for a server on port 8000 instead of 8080")
    _stop_http_servers(machine)
    _own_server(machine, "~/site", 8888)
    _assert_failing_with_hints(machine, "01-server", "for a server on port 8888 instead of 8080")


@pytest.mark.docker
def test_server_that_was_stopped_again_fails(machine):
    _setup(machine, "01-server")
    _solve(machine, "01-server")
    _assert_passing(machine, "01-server", "the solution")
    _stop_http_servers(machine)
    _assert_failing_with_hints(machine, "01-server", "after the server was stopped")


@pytest.mark.docker
def test_server_equally_correct_ways_pass(machine):
    for code, why in (
        ("cd ~/site && " + _detached("python3 -m http.server 8080"), "cd then server"),
        (_detached("python3 -m http.server 8080 --directory ~/site"), "--directory"),
        ("cd ~ && " + _detached("python3 -m http.server --directory site 8080"), "--directory, port last"),
        ("cd ~/site && " + _detached("python3 -m http.server 8080 --bind 127.0.0.1"), "bound to 127.0.0.1"),
    ):
        _setup(machine, "01-server")
        assert _student(machine, code + "\nsleep 0.2").returncode == 0
        _wait_port(machine, 8080)
        _assert_passing(machine, "01-server", why)


@pytest.mark.docker
def test_server_check_does_not_hang_on_a_listener_that_never_answers(machine):
    _setup(machine, "01-server")
    r = _student(machine, _detached("nc -lk 127.0.0.1 8080") + "\nsleep 0.3")
    assert r.returncode == 0
    _assert_failing_with_hints(machine, "01-server", "for a listener that never answers (ends inside 20 s)")


# ---------------------------------------------------------------- docker: 02 status codes


@pytest.mark.docker
def test_setup_of_the_curl_tasks_brings_the_server_up_on_8080_serving_site(machine):
    for task_id in ("02-status-codes", "03-headers", "04-content-type"):
        _setup(machine, task_id)
        uids = _listeners(machine, 8080)
        assert uids and all(uid != 0 for _, uid in uids), f"{task_id}: a server (not root) must run on 8080"
        out, _ = _out(machine, "curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/index.html")
        assert out == "200", task_id
        out, _ = _out(machine, "curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/no-such-page")
        assert out == "404", task_id


@pytest.mark.docker
def test_status_wrong_values_fail(machine):
    _setup(machine, "02-status-codes")
    for why, ok, missing in (
        ("swapped", "404", "200"),
        ("both 200", "200", "200"),
        ("words, not numbers", "OK", "Not Found"),
        ("301 for the ok page", "301", "404"),
        ("403 for the missing page", "200", "403"),
        ("empty files", "", ""),
        ("number with text", "HTTP 200", "HTTP 404"),
        ("two numbers", "200 200", "404 404"),
    ):
        _put(machine, "status-ok.txt", ok)
        _put(machine, "status-missing.txt", missing)
        _assert_failing_with_hints(machine, "02-status-codes", f"for {why}")


@pytest.mark.docker
def test_status_one_file_missing_fails_and_names_the_file_in_the_hint(machine):
    _setup(machine, "02-status-codes")
    _put(machine, "status-ok.txt", "200\\n")
    _assert_failing_with_hints(machine, "02-status-codes", "with only status-ok.txt")
    assert "status-missing" in _hints(machine, "02-status-codes")
    _student(machine, "rm -f ~/status-ok.txt")
    _put(machine, "status-missing.txt", "404\\n")
    _assert_failing_with_hints(machine, "02-status-codes", "with only status-missing.txt")
    assert "status-ok" in _hints(machine, "02-status-codes")


@pytest.mark.docker
def test_status_equally_correct_ways_pass(machine):
    _setup(machine, "02-status-codes")
    for why, a, b in (
        ("no newline", "200", "404"),
        ("newline", "200\\n", "404\\n"),
        ("CRLF", "200\\r\\n", "404\\r\\n"),
    ):
        _put(machine, "status-ok.txt", a)
        _put(machine, "status-missing.txt", b)
        _assert_passing(machine, "02-status-codes", why)
    _student(machine, "rm -f ~/status-*.txt")
    code = (
        "curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/index.html > ~/status-ok.txt; "
        "curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/nothing-here.html > ~/status-missing.txt"
    )
    assert _student(machine, code).returncode == 0
    _assert_passing(machine, "02-status-codes", "curl -w on localhost, another missing page")


# ---------------------------------------------------------------- docker: 03 headers


@pytest.mark.docker
def test_headers_wrong_files_fail(machine):
    _setup(machine, "03-headers")
    for why, code in (
        ("the body only", "curl -s http://127.0.0.1:8080/ > ~/headers.txt"),
        ("headers plus body (curl -i)", "curl -si http://127.0.0.1:8080/ > ~/headers.txt"),
        ("headers of /data.json", "curl -sI http://127.0.0.1:8080/data.json > ~/headers.txt"),
        ("headers of a missing page", "curl -sI http://127.0.0.1:8080/no-such-page > ~/headers.txt"),
        ("an empty file", ": > ~/headers.txt"),
        ("the status code only", "printf '200\\n' > ~/headers.txt"),
        ("a made-up header block", "printf 'HTTP/1.0 200 OK\\r\\nContent-Type: text/plain\\r\\n\\r\\n' > ~/headers.txt"),
    ):
        _student(machine, "rm -f ~/headers.txt")
        assert _student(machine, code).returncode == 0
        _assert_failing_with_hints(machine, "03-headers", f"for {why}")


@pytest.mark.docker
def test_headers_equally_correct_ways_pass(machine):
    _setup(machine, "03-headers")
    for why, code in (
        ("HEAD request", "curl -sI http://127.0.0.1:8080/ > ~/headers.txt"),
        ("dump-header of a GET", "curl -s -D ~/headers.txt -o /dev/null http://127.0.0.1:8080/"),
        ("localhost, trailing slash omitted by the host form", "curl -sI http://localhost:8080 > ~/headers.txt"),
        ("headers of /index.html", "curl -sI http://127.0.0.1:8080/index.html > ~/headers.txt"),
    ):
        _student(machine, "rm -f ~/headers.txt")
        assert _student(machine, code).returncode == 0
        _assert_passing(machine, "03-headers", why)


# ---------------------------------------------------------------- docker: 04 content type


@pytest.mark.docker
def test_content_type_wrong_values_fail(machine):
    _setup(machine, "04-content-type")
    for why, content in (
        ("the content type of the html page", "text/html"),
        ("the whole header line", "Content-Type: application/json"),
        ("a bare word", "json"),
        ("octet-stream", "application/octet-stream"),
        ("an empty file", ""),
        ("two values", "application/json text/html"),
    ):
        _put(machine, "content-type.txt", content)
        _assert_failing_with_hints(machine, "04-content-type", f"for {why}")
    _student(machine, "rm -f ~/content-type.txt")
    _assert_failing_with_hints(machine, "04-content-type", "without the file")


@pytest.mark.docker
def test_content_type_equally_correct_ways_pass(machine):
    _setup(machine, "04-content-type")
    for why, content in (
        ("no newline", "application/json"),
        ("newline", "application/json\\n"),
        ("CRLF from the raw header", "application/json\\r\\n"),
    ):
        _put(machine, "content-type.txt", content)
        _assert_passing(machine, "04-content-type", why)
    code = (
        "curl -sI http://127.0.0.1:8080/data.json | grep -i '^content-type' | cut -d' ' -f2 > ~/content-type.txt"
    )
    assert _student(machine, code).returncode == 0  # keeps the CR of the raw header
    _assert_passing(machine, "04-content-type", "grep and cut from the raw header")
    code = "curl -s -o /dev/null -w '%{content_type}' http://127.0.0.1:8080/data.json > ~/content-type.txt"
    assert _student(machine, code).returncode == 0
    _assert_passing(machine, "04-content-type", "curl -w %{content_type}")


# ---------------------------------------------------------------- docker: 05 echo service


@pytest.mark.docker
def test_echo_setup_starts_an_ipv4_only_service_that_is_not_root(machine):
    _setup(machine, "05-post-echo")
    got = _listeners(machine, 9090)
    assert got, "nothing listens on port 9090 after setup 05"
    assert {a for a, _ in got} == {"0100007F"}, f"the echo service must listen on 127.0.0.1 only: {got}"
    assert all(uid != 0 for _, uid in got), f"the echo service must not run as root: {got}"
    out, rc = _out(machine, "curl -s -m 3 -g http://[::1]:9090/echo -o /dev/null -w '%{http_code}'")
    assert out in ("000", "") or rc != 0, "the echo service must not answer on ::1"


@pytest.mark.docker
def test_echo_service_contract(machine):
    import json

    _setup(machine, "05-post-echo")
    reply = json.loads(_echo_reply(machine))
    assert reply["echo"] == json.loads(POST_BODY) and isinstance(reply["id"], str) and reply["id"]
    assert json.loads(_echo_reply(machine))["id"] == reply["id"], "the id is per service instance"
    other = json.loads(_echo_reply(machine, '{"x":[1,2]}'))
    assert other["echo"] == {"x": [1, 2]} and other["id"] == reply["id"]
    out, _ = _out(
        machine,
        "curl -s -m 5 -X POST -d 'a=b' -o /tmp/e415 -w '%{http_code}' http://127.0.0.1:9090/echo; echo; cat /tmp/e415",
    )
    code, _, body = out.partition("\n")
    assert code == "415", f"a POST without Content-Type: application/json must give 415, got {code}"
    assert "error" in json.loads(body.strip())


@pytest.mark.docker
def test_echo_setup_twice_keeps_one_service(machine):
    _setup(machine, "05-post-echo")
    _setup(machine, "05-post-echo")
    got = _listeners(machine, 9090)
    assert got and len({a for a, _ in got}) == 1
    _assert_failing_with_hints(machine, "05-post-echo", "after setup ran twice")


@pytest.mark.docker
def test_echo_wrong_replies_fail(machine):
    import json

    _setup(machine, "05-post-echo")
    real = json.loads(_echo_reply(machine))
    other = _echo_reply(machine, '{"lab":"net-2","msg":"bye"}')
    for why, code in (
        ("a reply to another body", f"cat > ~/reply.json <<'E678'\n{other}\nE678"),
        ("a 415 reply (no JSON header)", "curl -s -m 5 -X POST -d '" + POST_BODY + "' http://127.0.0.1:9090/echo > ~/reply.json"),
        ("a hand-made reply with a made-up id", f"echo '{{\"id\":\"made-up\",\"echo\":{POST_BODY}}}' > ~/reply.json"),
        ("only the body that was sent", f"echo '{POST_BODY}' > ~/reply.json"),
        ("the right echo without the id", f"echo '{{\"echo\":{POST_BODY}}}' > ~/reply.json"),
        ("an id but another echo", f"echo '{{\"id\":\"{real['id']}\",\"echo\":{{\"lab\":\"net-1\"}}}}' > ~/reply.json"),
        ("not JSON", "echo 'hello' > ~/reply.json"),
        ("an empty file", ": > ~/reply.json"),
    ):
        _student(machine, "rm -f ~/reply.json")
        assert _student(machine, code).returncode == 0
        _assert_failing_with_hints(machine, "05-post-echo", f"for {why}")


@pytest.mark.docker
def test_echo_equally_correct_ways_pass(machine):
    _setup(machine, "05-post-echo")
    for why, code in (
        (
            "-d with the header, -o",
            f"curl -s -X POST -H 'Content-Type: application/json' -d '{POST_BODY}' -o ~/reply.json http://127.0.0.1:9090/echo",
        ),
        (
            "body from a file, redirect",
            f"echo '{POST_BODY}' > /tmp/body.json && curl -s -H 'Content-Type: application/json' --data @/tmp/body.json http://127.0.0.1:9090/echo > ~/reply.json",
        ),
        (
            "pretty-printed by jq",
            f"curl -s -H 'Content-Type: application/json' -d '{POST_BODY}' http://127.0.0.1:9090/echo | jq . > ~/reply.json",
        ),
        (
            "body with spaces",
            "curl -s -H 'Content-Type: application/json' -d '{ \"lab\": \"net-1\", \"msg\": \"hello\" }' http://127.0.0.1:9090/echo > ~/reply.json",
        ),
    ):
        _student(machine, "rm -f ~/reply.json")
        assert _student(machine, code).returncode == 0
        _assert_passing(machine, "05-post-echo", why)


# ---------------------------------------------------------------- docker: 08 the localhost trap


@pytest.mark.docker
def test_trap_localhost_answers_wrong_and_127_answers_right(machine):
    _setup(machine, "08-localhost-trap")
    real = _real_pong(machine)
    via_name, _ = _out(machine, "curl -s -m 5 http://localhost:9091/")
    assert via_name.strip() != real.strip(), "after setup 08, `curl localhost:9091` must not give the real reply"
    assert "pong:" not in via_name, "the decoy must not look like the service"


@pytest.mark.docker
def test_trap_service_is_ipv4_loopback_only_and_decoy_is_ipv6(machine):
    _setup(machine, "08-localhost-trap")
    got = _listeners(machine, 9091)
    addrs = {a for a, _ in got}
    assert "0100007F" in addrs, f"the real service must listen on 127.0.0.1: {got}"
    assert "00000000000000000000000001000000" in addrs, f"a decoy must listen on [::1]: {got}"
    assert all(uid != 0 for _, uid in got), f"no listener may run as root: {got}"
    assert not addrs & {"00000000", "0" * 32}, f"nothing may listen on all addresses: {got}"


@pytest.mark.docker
def test_trap_real_token_is_stable_across_a_second_setup_run(machine):
    _setup(machine, "08-localhost-trap")
    first = _real_pong(machine)
    _setup(machine, "08-localhost-trap")
    assert _real_pong(machine) == first, "setup must be idempotent: one real service, same reply"


@pytest.mark.docker
def test_trap_wrong_answers_fail_and_the_hint_leads_to_the_cause(machine):
    _setup(machine, "08-localhost-trap")
    real = _real_pong(machine).strip()
    for why, code in (
        ("the reply of localhost (the decoy)", "curl -s -m 5 http://localhost:9091/ > ~/pong.txt"),
        ("a hard-coded pong", "echo pong > ~/pong.txt"),
        ("pong with a made-up token", "echo pong:0000 > ~/pong.txt"),
        ("the token without pong:", f"echo '{real.split(':', 1)[1]}' > ~/pong.txt"),
        ("an empty file", ": > ~/pong.txt"),
        ("the reply plus extra text", f"echo '{real} (from 127.0.0.1)' > ~/pong.txt"),
    ):
        _student(machine, "rm -f ~/pong.txt")
        assert _student(machine, code).returncode == 0
        _assert_failing_with_hints(machine, "08-localhost-trap", f"for {why}")
    _student(machine, "curl -s -m 5 http://localhost:9091/ > ~/pong.txt")
    hint = _hints(machine, "08-localhost-trap")
    assert "localhost" in hint or "127.0.0.1" in hint or "ipv6" in hint, (
        f"the hint for the decoy reply must lead the student to localhost / 127.0.0.1 / IPv6: {hint!r}"
    )


@pytest.mark.docker
def test_trap_equally_correct_ways_pass(machine):
    _setup(machine, "08-localhost-trap")
    for why, code in (
        ("curl 127.0.0.1", "curl -s http://127.0.0.1:9091/ > ~/pong.txt"),
        ("curl -o", "curl -s -o ~/pong.txt http://127.0.0.1:9091/"),
        ("curl with -4 and localhost", "curl -4 -s http://localhost:9091/ > ~/pong.txt"),
        ("curl with a path-less url", "curl -s http://127.0.0.1:9091 > ~/pong.txt"),
        ("netcat raw HTTP", "printf 'GET / HTTP/1.0\\r\\n\\r\\n' | nc -N 127.0.0.1 9091 | tail -n 1 > ~/pong.txt"),
    ):
        _student(machine, "rm -f ~/pong.txt")
        if _student(machine, code).returncode != 0:
            continue  # nc flavours differ; the curl ways above always run
        if "nc " in code:
            out, _ = _out(machine, "cat ~/pong.txt")
            if not out.startswith("pong:"):
                continue
        _assert_passing(machine, "08-localhost-trap", why)
    _student(machine, "rm -f ~/pong.txt")
    _student(machine, "curl -s http://127.0.0.1:9091/ > ~/pong.txt")
    _assert_passing(machine, "08-localhost-trap", "curl 127.0.0.1 (final)")
