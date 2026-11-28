"""Acceptance tests for issue #679: lab content "Networking 2: IP addresses, ports, /etc/hosts and DNS" (en + he), lesson 8 (D191), a sudo lab.

The lab does not exist yet; these tests describe it. Choices pinned here (the builder follows them):

Location and metadata
    labs/content/networking/net-2/lab.yaml   course dir `networking` (next to net-1), lab id `net-2`
    (the validator requires id == directory name). module: 1, lesson: 8 (D191 v2 numbering),
    image: term, sudo: true (the lab runs on the setuid-honouring runtime, D199, with the
    student's sudo rule from the entrypoint; /etc/hosts needs root), status: published (D191),
    no internet (no `internet`, no `allow_domains`), time_limit_min between 30 and 60.

Tasks (ids and order are fixed; kind in brackets)
    01-my-ip          [hands-on]  `~/myip.txt` (/home/student/myip.txt) holds the container's own IPv4
                                  address WITH the prefix length, exactly as `ip addr` prints it on
                                  its `inet` line (for example `172.20.0.5/16`; one of the global
                                  addresses of the machine, not 127.0.0.1/8). check.sh computes the
                                  true value inside the container (`ip -4 -o addr show scope
                                  global`), it never holds a constant. Whitespace around the value
                                  and a trailing newline are fine; the address alone (no prefix), a
                                  wrong prefix, another address, `127.0.0.1/8`, a whole `inet ...`
                                  line and an empty file fail.
    02-hosts-web      [hands-on]  `web.lab` must resolve to `127.0.0.1` through `/etc/hosts`, and the
                                  student reaches the local web service by that name. Setup
                                  (root; idempotent) removes every `web.lab` line from /etc/hosts
                                  (in place: /etc/hosts is a bind mount, never rename over it),
                                  removes ~/web-reply.txt, and (re)starts exactly ONE
                                  `python3 -m http.server 8081` run AS `student`, serving
                                  ~/webroot (its index.html holds a fresh random token on every
                                  setup run). The student saves the page fetched BY NAME,
                                  `curl -s http://web.lab:8081/`, into `~/web-reply.txt`.
                                  Check items: (1) `getent hosts web.lab` answers 127.0.0.1 (the
                                  first match counts: an earlier `web.lab` line to another
                                  address, a commented line, `127.0.0.2`, or `web` without `.lab`
                                  fail), (2) the service answers on port 8081 (check.sh uses
                                  curl with a timeout), (3) ~/web-reply.txt equals the page the
                                  service serves now (stale or `curl -i` output fails).
                                  THE TRAP (D191): the student writes the line without `sudo`
                                  (`echo ... >> /etc/hosts` is `Permission denied`). The task text
                                  does not say `sudo` (in either language); the hint of the failing
                                  hosts item says `sudo`; solution.en.md explains `sudo tee -a` and
                                  the permission error.
    03-nameserver     [hands-on]  `~/nameserver.txt` holds the address of the FIRST `nameserver` line
                                  of /etc/resolv.conf, the address alone. check.sh reads
                                  /etc/resolv.conf when it runs (the exam rewrites the file and the
                                  answer follows). The whole line, a hostname, the search domain,
                                  another nameserver line and an empty file fail.
    04-quiz-hosts-26  [quiz]      how many usable hosts a /26 holds: right answer 62 (other options
                                  64, 63, 30, and so on)
    05-quiz-subnet    [quiz]      are 192.168.10.200 and 192.168.11.5 in the same /24: right answer
                                  "No" (the third octet differs); the wrong options say "Yes"
    06-quiz-records   [quiz]      A vs CNAME vs MX: right option pairs A with an IPv4 address, CNAME
                                  with an alias (another name) and MX with the mail server; the
                                  other options swap the pairs
    07-quiz-ports     [quiz]      22 = SSH, 53 = DNS, 80 = HTTP, 443 = HTTPS (the wrong options
                                  shuffle them)

Files per hands-on task directory (tasks/<id>/): task.en.md, task.he.md, solution.en.md,
    solution.he.md (the validator's files), setup.sh, check.sh, and the exam-only `solution.sh`
    (the commands that solve the task, run as `student` through a login shell; it uses `sudo`
    where root is needed). The validator and loader ignore it. All .sh files: LF endings, a
    `#!/bin/sh` or `#!/bin/bash` (or `/usr/bin/env`) shebang, mode 0755 in git. The task texts
    show the exact names and values in code spans (see TEXT_PINS). Quizzes live in lab.yaml
    (`kind: quiz`, question, options, `correct` index), both languages.

check.sh protocol (spec section 7, asserted exactly): runs as root, finishes in under 20 s, prints
    `PASS <n>` or `FAIL <n> <hint>` per checklist item (N >= 2), exit 0 only when all pass, every
    FAIL line carries a non-empty hint. setup.sh: run as root, exit 0, self-contained, idempotent
    (two runs leave one web service and no `web.lab` line), and replaces the state its task is
    about (after the solution a second run brings the task back to failing). The checks look at
    state, never at the commands typed (a bash history that holds the commands but no state gets
    FAIL), and equally correct alternative commands get PASS.

Docker tests (marker `docker`, run in CI) start the lab through `app.runtime.container_spec` and
    `DockerRuntime.start`, exactly as production starts a sudo lab (runtime `runsc-suid`, the
    spec's capabilities and tmpfs mounts, LF_SUDO=1). Root in a lab container keeps only SETUID
    and SETGID (D200): listeners are read from /proc/net/tcp and servers are stopped as
    `student`. They are parametrised over the fixed task ids, so a missing lab fails them; they
    skip only when Docker itself is absent. Students are fake; no real student data in fixtures.
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
LAB_DIR = CONTENT_ROOT / "networking" / "net-2"
LAB_ID = "net-2"
LAB_KEY = "networking/net-2"

TASKS = [
    ("01-my-ip", "hands-on"),
    ("02-hosts-web", "hands-on"),
    ("03-nameserver", "hands-on"),
    ("04-quiz-hosts-26", "quiz"),
    ("05-quiz-subnet", "quiz"),
    ("06-quiz-records", "quiz"),
    ("07-quiz-ports", "quiz"),
]
QUIZ_IDS = [t for t, k in TASKS if k == "quiz"]
HANDS_ON_IDS = [t for t, k in TASKS if k == "hands-on"]
# exact names and values that the en and he task texts show in code spans
TEXT_PINS = {
    "01-my-ip": ["~/myip.txt", "ip addr"],
    "02-hosts-web": ["web.lab", "127.0.0.1", "/etc/hosts", "8081", "~/web-reply.txt"],
    "03-nameserver": ["/etc/resolv.conf", "~/nameserver.txt"],
}
HEBREW = re.compile("[֐-׿]")
HOME = "/home/student"
PORT = 8081


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


# ---------------------------------------------------------------- non-docker: validator and shape


def test_validator_accepts_the_lab_with_no_errors():
    res = _load()
    assert LAB_KEY not in res.errors, res.errors.get(LAB_KEY)
    assert LAB_ID in res.labs, f"{LAB_ID} not loaded; all errors: {res.errors}"


def test_lab_is_module_1_lesson_8_image_term():
    lab = _lab()
    assert (lab.module, lab.lesson) == (1, 8)
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


def _others(task):
    return [o["en"] for i, o in enumerate(task.options) if i != task.correct]


def test_hosts_quiz_right_answer_is_62_and_only_that_option_says_it():
    task = _task("04-quiz-hosts-26")
    assert "/26" in task.question["en"]
    assert re.search(r"\b62\b", task.options[task.correct]["en"])
    assert not re.search(r"\b62\b", " ".join(_others(task))), "a wrong option also says 62"
    assert any(re.search(r"\b64\b", o) for o in _others(task)), "64 (all addresses) is a plausible wrong option"


def test_subnet_quiz_names_both_addresses_and_the_right_answer_is_no():
    task = _task("05-quiz-subnet")
    q = task.question["en"]
    assert "192.168.10.200" in q and "192.168.11.5" in q and "/24" in q
    assert task.options[task.correct]["en"].strip().lower().startswith("no")
    for o in _others(task):
        assert not o.strip().lower().startswith("no"), f"a wrong option says no: {o!r}"
    assert any(o.strip().lower().startswith("yes") for o in _others(task))


_RECORD_PAIRS = (r"\bA\b[^,;]*IPv4", r"CNAME[^,;]*alias", r"MX[^,;]*mail")


def test_records_quiz_right_option_pairs_a_cname_and_mx_correctly():
    task = _task("06-quiz-records")
    q = task.question["en"]
    assert all(w in q for w in ("A", "CNAME", "MX"))
    right = task.options[task.correct]["en"]
    for pat in _RECORD_PAIRS:
        assert re.search(pat, right, flags=re.I if pat != _RECORD_PAIRS[0] else 0), (
            f"the right option must match {pat!r}: {right!r}"
        )
    for o in _others(task):
        ok = [bool(re.search(p, o, flags=re.I if p != _RECORD_PAIRS[0] else 0)) for p in _RECORD_PAIRS]
        assert not all(ok), f"a wrong option holds all three right pairs: {o!r}"


def test_ports_quiz_right_option_maps_22_53_80_443_in_order():
    task = _task("07-quiz-ports")
    pat = r"22\D{1,12}ssh.*53\D{1,12}dns.*80\D{1,12}http\b.*443\D{1,12}https"
    assert re.search(pat, task.options[task.correct]["en"], flags=re.I | re.S)
    for o in _others(task):
        assert not re.search(pat, o, flags=re.I | re.S), f"a wrong option is also right: {o!r}"


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
    d = _task_dir("02-hosts-web")
    for name in ("task.en.md", "task.he.md"):
        assert "sudo" not in _text(d / name).lower(), f"{name} must not say sudo (that is the trap)"
    sol = _text(d / "solution.en.md")
    assert "sudo" in sol and "Permission denied" in sol, "solution 02 must explain sudo and the error"
    assert "tee" in sol or "sudo sh -c" in sol or "sudo bash -c" in sol, "solution 02 shows how to append with sudo"


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

_LISTENERS = r"""
import sys
port = int(sys.argv[1])
out = []
for f in ("/proc/net/tcp", "/proc/net/tcp6"):
    try:
        lines = open(f).read().splitlines()[1:]
    except OSError:
        continue
    for line in lines:
        p = line.split()
        addr, prt = p[1].rsplit(":", 1)
        if p[3] == "0A" and int(prt, 16) == port:
            out.append((addr, int(p[7])))
for a, u in sorted(out):
    print(a, u)
"""

# Runs as `student` (root in a lab container has no CAP_KILL and cannot signal it, D200).
_WEB_SERVERS = r"""
import os, signal, sys, time
me = os.getpid()
kill = sys.argv[1] == "kill"


def servers():
    found = []
    for pid in [d for d in os.listdir("/proc") if d.isdigit()]:
        try:
            cmd = open(f"/proc/{pid}/cmdline").read().replace("\0", " ")
            state = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[0]
        except OSError:
            continue
        if int(pid) != me and "http.server" in cmd and "python" in cmd and "8081" in cmd and state != "Z":
            found.append(int(pid))
    return found


if not kill:
    print(len(servers()))
else:
    deadline = time.time() + 8
    while servers() and time.time() < deadline:
        for pid in servers():
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
    """Run shell code as root (a probe, or the exam rewriting /etc/resolv.conf in place)."""
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
    """Student commands that the test itself needs to succeed."""
    r = _student(machine, "set -e\n" + code)
    assert r.returncode == 0, f"student commands failed: {code!r}\n{r.stdout}\n{r.stderr}"
    return r


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
    return res, parsed


def _assert_passing(machine: str, task_id: str, what: str):
    res, parsed = _check(machine, task_id)
    n = _n_items(task_id)
    assert set(parsed.items) == set(range(1, n + 1)), res.stdout
    assert "FAIL" not in res.stdout, f"{task_id}: {what} was refused:\n{res.stdout}"
    assert res.returncode == 0 and parsed.passed, f"{task_id}: {what}:\n{res.stdout}"


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


def _listeners(machine: str, port: int) -> list[tuple[str, int]]:
    """(hex address, uid) of the listening sockets on a TCP port, read from /proc/net (D200)."""
    res = _docker("exec", "-i", machine, "python3", "-", str(port), check=False, timeout=60, stdin=_LISTENERS)
    return [(a, int(u)) for a, u in (line.split() for line in res.stdout.splitlines())]


def _web_servers(machine: str) -> int:
    res = _docker(
        "exec", "-u", "student", "-i", machine, "python3", "-", "count",
        check=False, timeout=60, stdin=_WEB_SERVERS,
    )
    return int(res.stdout.strip() or "0")


def _stop_web(machine: str):
    _docker(
        "exec", "-u", "student", "-i", machine, "python3", "-", "kill",
        check=False, timeout=60, stdin=_WEB_SERVERS,
    )
    time.sleep(0.3)


def _page(machine: str) -> str:
    return _out(machine, f"curl -s -m 5 http://127.0.0.1:{PORT}/")


def _real_addresses(machine: str) -> list[str]:
    out = _root(machine, "ip -4 -o addr show scope global | awk '{print $4}'").stdout.split()
    assert out, "the lab container has no global IPv4 address to answer with"
    return out


def _first_nameserver(machine: str) -> str:
    out = _root(machine, "awk '/^nameserver/ {print $2; exit}' /etc/resolv.conf").stdout.strip()
    assert out, "the lab container has no nameserver line in /etc/resolv.conf"
    return out


def _tamper(machine: str, content: str):
    """Rewrite /etc/resolv.conf in place (a bind mount: never rename over it), as the exam only."""
    r = _root(machine, f"cat > /etc/resolv.conf <<'EOS_679'\n{content}\nEOS_679\n")
    assert r.returncode == 0, f"cannot rewrite /etc/resolv.conf: {r.stderr}"


HOSTS_OK = "echo '127.0.0.1 web.lab' | sudo tee -a /etc/hosts >/dev/null"
REPLY_OK = f"curl -s -m 5 http://web.lab:{PORT}/ > ~/web-reply.txt"


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
    _assert_passing(machine, "01-my-ip", "task 01 after the whole lab")
    _assert_passing(machine, "02-hosts-web", "task 02 after the whole lab")


@pytest.mark.docker
@pytest.mark.parametrize("task_id", HANDS_ON_IDS)
def test_the_check_ignores_the_commands_in_the_shell_history(machine, task_id):
    _setup(machine, task_id)
    solution = _text(_task_dir(task_id) / "solution.sh")
    r = _student(
        machine,
        "cat > ~/.bash_history <<'EOS_679'\n" + solution + "\nEOS_679\n"
        "echo 'ip addr; sudo tee -a /etc/hosts; cat /etc/resolv.conf' >> ~/.bash_history\n",
    )
    assert r.returncode == 0, r.stderr
    _assert_failing_with_hints(machine, task_id, "with the commands only in the shell history")


# ---------------------------------------------------------------- docker: 01 my ip


@pytest.mark.docker
def test_01_the_solution_leaves_the_address_with_its_prefix(machine):
    _setup(machine, "01-my-ip")
    _solve(machine, "01-my-ip")
    got = _root(machine, f"cat {HOME}/myip.txt").stdout.strip()
    assert got in _real_addresses(machine), f"{got!r} is not an address of the machine"
    assert re.fullmatch(r"\d+\.\d+\.\d+\.\d+/\d+", got)


@pytest.mark.docker
def test_01_the_check_computes_the_value_it_never_holds_the_address(machine):
    code = _text(_task_dir("01-my-ip") / "check.sh")
    assert re.search(r"\bip\b", code), "check.sh must ask `ip` for the address"
    for addr in _real_addresses(machine):
        assert addr.split("/")[0] not in code, "check.sh holds the machine's address as a constant"
    assert not re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2}\b", code.replace("127.0.0.1/8", "")), (
        "check.sh holds a literal address with a prefix"
    )


@pytest.mark.docker
def test_01_wrong_values_fail_with_a_hint(machine):
    real = _real_addresses(machine)[0]
    ip, prefix = real.split("/")
    octets = ip.split(".")
    other = ".".join(octets[:3] + [str(int(octets[3]) + 1 if int(octets[3]) < 250 else int(octets[3]) - 1)])
    other_prefix = int(prefix) + 1 if int(prefix) < 30 else int(prefix) - 1
    attempts = [
        ("the loopback address", "echo 127.0.0.1/8 > ~/myip.txt"),
        ("the address without its prefix", f"echo {ip} > ~/myip.txt"),
        ("the right address with another prefix", f"echo {ip}/{other_prefix} > ~/myip.txt"),
        ("another address of the same network", f"echo {other}/{prefix} > ~/myip.txt"),
        ("the whole inet line", "ip -4 -o addr show scope global > ~/myip.txt"),
        ("an empty file", ": > ~/myip.txt"),
    ]
    for why, code in attempts:
        _near_miss(machine, "01-my-ip", code, why)


@pytest.mark.docker
def test_01_equally_correct_alternative_ways_to_write_it_pass(machine):
    _alternative(
        machine, "01-my-ip",
        "ip -4 addr show scope global | awk '/inet /{print $2}' | head -n1 | tr -d '\\n' > ~/myip.txt",
        "ip addr piped through awk, no trailing newline",
    )
    _alternative(
        machine, "01-my-ip",
        "printf '  %s  \\n' \"$(ip -4 -o addr show scope global | awk '{print $4}' | head -n1)\" > ~/myip.txt",
        "spaces around the value",
    )


# ---------------------------------------------------------------- docker: 02 hosts and the web service


@pytest.mark.docker
def test_02_setup_starts_one_web_service_as_student_and_clears_the_name(machine):
    _setup(machine, "02-hosts-web")
    student_uid = int(_root(machine, "id -u student").stdout.strip())
    socks = _listeners(machine, PORT)
    assert socks, "nothing listens on port 8081 after setup"
    assert all(uid == student_uid for _, uid in socks), f"the service must run as student, not root: {socks}"
    assert _page(machine), "the service serves an empty page"
    assert _root(machine, "getent hosts web.lab").stdout.strip() == "", "web.lab resolves before the task"
    assert _out(machine, f"test -e {HOME}/web-reply.txt && echo reply") == ""


@pytest.mark.docker
def test_02_setup_twice_leaves_one_service_and_one_hosts_state(machine):
    _setup(machine, "02-hosts-web")
    first = _page(machine)
    _setup(machine, "02-hosts-web")
    assert _web_servers(machine) == 1, "two setups must leave exactly one web service"
    assert _page(machine), "the service must answer after the second setup"
    assert _page(machine) != first, "the page holds a fresh token on every setup"
    _solve(machine, "02-hosts-web")
    _setup(machine, "02-hosts-web")
    assert _root(machine, "grep -c 'web\\.lab' /etc/hosts; true").stdout.split()[0] == "0"
    assert _web_servers(machine) == 1


@pytest.mark.docker
def test_02_the_solution_gives_the_page_by_name_and_keeps_the_service_unprivileged(machine):
    _setup(machine, "02-hosts-web")
    _solve(machine, "02-hosts-web")
    assert _root(machine, "getent hosts web.lab").stdout.split()[:2] == ["127.0.0.1", "web.lab"]
    reply = _root(machine, f"cat {HOME}/web-reply.txt").stdout.strip()
    assert reply and reply == _page(machine)
    assert all(uid != 0 for _, uid in _listeners(machine, PORT))


@pytest.mark.docker
def test_02_the_trap_a_hosts_edit_without_sudo_is_refused_and_the_hint_names_sudo(machine):
    _setup(machine, "02-hosts-web")
    r = _student(machine, "echo '127.0.0.1 web.lab' >> /etc/hosts")
    assert r.returncode != 0 and "ermission denied" in (r.stderr + r.stdout), "the student can write /etc/hosts without sudo"
    _try(machine, REPLY_OK.replace("web.lab", "127.0.0.1"))  # a reply by address, not by name
    res, parsed = _assert_failing_with_hints(machine, "02-hosts-web", "after a hosts edit that failed")
    assert "sudo" in _hints(parsed), f"a hint must point to sudo:\n{res.stdout}"
    assert parsed.items[1].passed is False, "the hosts item (1) must be the failing one"


@pytest.mark.docker
def test_02_wrong_hosts_entries_fail(machine):
    attempts = [
        ("the name without .lab", "echo '127.0.0.1 web' | sudo tee -a /etc/hosts >/dev/null"),
        ("another loopback address", "echo '127.0.0.2 web.lab' | sudo tee -a /etc/hosts >/dev/null"),
        ("the machine's own address", "echo \"$(hostname -i | awk '{print $1}') web.lab\" | sudo tee -a /etc/hosts >/dev/null"),
        ("a commented line", "echo '# 127.0.0.1 web.lab' | sudo tee -a /etc/hosts >/dev/null"),
        ("the name before the address", "echo 'web.lab 127.0.0.1' | sudo tee -a /etc/hosts >/dev/null"),
        (
            "an earlier line to another address (the first match wins)",
            "printf '10.9.9.9 web.lab\\n127.0.0.1 web.lab\\n' | sudo tee -a /etc/hosts >/dev/null",
        ),
    ]
    for why, code in attempts:
        _setup(machine, "02-hosts-web")
        _try(machine, code)
        _try(machine, f"curl -s -m 5 http://127.0.0.1:{PORT}/ > ~/web-reply.txt")
        _assert_failing_with_hints(machine, "02-hosts-web", f"for {why}")


@pytest.mark.docker
def test_02_a_correct_entry_without_the_page_saved_fails_and_a_wrong_page_fails(machine):
    _near_miss(machine, "02-hosts-web", HOSTS_OK, "the entry without ~/web-reply.txt")
    _near_miss(
        machine, "02-hosts-web",
        HOSTS_OK + f"\ncurl -si -m 5 http://web.lab:{PORT}/ > ~/web-reply.txt",
        "the reply with its HTTP headers (curl -i)",
    )
    _near_miss(
        machine, "02-hosts-web", HOSTS_OK + "\necho 'it works' > ~/web-reply.txt",
        "a reply written by hand",
    )


@pytest.mark.docker
def test_02_a_stopped_service_fails_even_with_the_entry_and_the_page(machine):
    _setup(machine, "02-hosts-web")
    _solve(machine, "02-hosts-web")
    _stop_web(machine)
    _assert_failing_with_hints(machine, "02-hosts-web", "with the web service stopped")


@pytest.mark.docker
def test_02_a_stale_page_from_an_earlier_token_fails(machine):
    _setup(machine, "02-hosts-web")
    _solve(machine, "02-hosts-web")
    old = _out(machine, "cat ~/web-reply.txt")
    _setup(machine, "02-hosts-web")  # new token, empty hosts state
    _try(machine, HOSTS_OK)
    _try(machine, "cat > ~/web-reply.txt <<'EOS_679'\n" + old + "\nEOS_679\n")
    _assert_failing_with_hints(machine, "02-hosts-web", "with the page of an earlier run")


@pytest.mark.docker
def test_02_alternative_ways_to_add_the_line_pass(machine):
    _alternative(
        machine, "02-hosts-web",
        "sudo sh -c \"echo '127.0.0.1 web.lab' >> /etc/hosts\"\n" + REPLY_OK,
        "sudo sh -c with a redirect",
    )
    _alternative(
        machine, "02-hosts-web",
        "printf '127.0.0.1\\tlocalhost-web web.lab\\n' | sudo tee -a /etc/hosts >/dev/null\n"
        + REPLY_OK.replace(f"{PORT}/", f"{PORT}"),
        "a tab separator, an extra alias and a URL without the final slash",
    )


# ---------------------------------------------------------------- docker: 03 nameserver


@pytest.mark.docker
def test_03_the_solution_holds_the_address_alone(machine):
    _setup(machine, "03-nameserver")
    _solve(machine, "03-nameserver")
    got = _root(machine, f"cat {HOME}/nameserver.txt").stdout.strip()
    assert got == _first_nameserver(machine)
    assert " " not in got


@pytest.mark.docker
def test_03_the_check_follows_resolv_conf_it_is_not_a_constant(machine):
    _tamper(machine, "search lab.test\nnameserver 192.0.2.53\nnameserver 192.0.2.54")
    _setup(machine, "03-nameserver")
    _try(machine, "printf '192.0.2.53\\n' > ~/nameserver.txt")
    _assert_passing(machine, "03-nameserver", "the first nameserver of a rewritten resolv.conf")
    _tamper(machine, "search lab.test\nnameserver 198.51.100.7")
    _assert_failing_with_hints(machine, "03-nameserver", "after resolv.conf names another server")


@pytest.mark.docker
def test_03_wrong_answers_fail_with_a_hint(machine):
    _tamper(machine, "search lab.test\nnameserver 192.0.2.53\nnameserver 192.0.2.54")
    attempts = [
        ("the whole line", "grep '^nameserver' /etc/resolv.conf | head -n1 > ~/nameserver.txt"),
        ("the search domain", "echo lab.test > ~/nameserver.txt"),
        ("a hostname", "echo localhost > ~/nameserver.txt"),
        ("the second nameserver", "echo 192.0.2.54 > ~/nameserver.txt"),
        ("both addresses", "awk '/^nameserver/ {print $2}' /etc/resolv.conf > ~/nameserver.txt"),
        ("an empty file", ": > ~/nameserver.txt"),
    ]
    for why, code in attempts:
        _near_miss(machine, "03-nameserver", code, why)


@pytest.mark.docker
def test_03_equally_correct_alternatives_pass(machine):
    _tamper(machine, "nameserver 192.0.2.53")
    _alternative(
        machine, "03-nameserver", "grep nameserver /etc/resolv.conf | cut -d' ' -f2 | tr -d '\\n' > ~/nameserver.txt",
        "grep and cut, no trailing newline",
    )
    _alternative(
        machine, "03-nameserver", "echo '192.0.2.53' > ~/nameserver.txt", "typed by hand after reading the file"
    )
