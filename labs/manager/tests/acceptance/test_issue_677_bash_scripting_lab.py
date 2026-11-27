"""Acceptance tests for issue #677: lab content "Bash scripting" (en + he), lesson 6 (D191).

The lab does not exist yet; these tests describe it. Choices pinned here (the builder follows them):

Location and metadata
    labs/content/bash/bash-1/lab.yaml   course dir `bash`, lab id `bash-1` (the validator requires
    id == directory name). module: 1, lesson: 6 (D191 v2 numbering), image: term, sudo: false
    (explicit key), status: published (D191: ships published once the review approves and the
    docker solve test passes), no internet.

Tasks (ids and order are fixed; kind in brackets; the script the student writes in brackets)
    01-special-vars [quiz]       what `$?`, `$#` and `$1` mean
    02-greet        [hands-on]   ~/greet.sh NAME        prints `Hello, NAME`, exit 0
    03-for-loop     [hands-on]   state: ~/files holds exactly the regular files file1 .. file5
    04-exists       [hands-on]   ~/exists.sh PATH       prints `found` (a file or a directory
                                 exists) or `missing`, exit 0 both ways
    05-usage        [hands-on]   ~/usage.sh             no argument: prints `Usage: usage.sh NAME`
                                 on stdout and exits 1; with one argument: exits 0
    06-count-lines  [hands-on]   ~/count_lines.sh FILE  prints the number of lines (only the
                                 number), a last line without a newline counts; the task text
                                 asks for a `while read` loop
    07-backup       [hands-on]   ~/backup.sh            makes ~/backup.tar.gz of ~/notes; entries
                                 start with `notes/` (tar -C ~ notes), a re-run replaces the archive
    08-space-trap   [hands-on]   ~/firstline.sh FILE    prints the first line of FILE; setup makes
                                 `~/docs/my notes.txt` (a space in the name, two or more lines).
                                 The deliberate trap (D191): `head -n 1 $1` fails on it. The task
                                 text does not say "quote"; solution.en.md explains the quotes.

Files per hands-on task directory (tasks/<id>/): task.en.md, task.he.md, solution.en.md,
    solution.he.md (the validator's files), setup.sh, check.sh, and the exam-only `solution.sh`
    (the commands that solve the task, run as `student` through a login shell; it writes the
    script into the home folder and makes it executable). The validator and loader ignore it.
    All .sh files: LF endings, a `#!/bin/sh` or `#!/bin/bash` (or `/usr/bin/env`) shebang,
    mode 0755 in git. The task texts show the exact names and values in code spans (the script
    names above, `Hello, `, `found`, `missing`, `Usage: usage.sh NAME`, `file1`, `file5`,
    `while read`, `backup.tar.gz`, `my notes.txt`).

check.sh protocol (spec section 7, asserted exactly): as in the #12 exam. Runs as root (no
    capabilities beyond SETUID/SETGID), finishes in under 20 s, prints `PASS <n>` or
    `FAIL <n> <hint>` per checklist item (N >= 2), exit 0 only when all pass, every FAIL line
    carries a non-empty hint. setup.sh: run as root, exit 0, self-contained, idempotent, and
    replaces the state its task is about (after the solution a second run brings the task back to
    failing: it removes the student's script).

Hidden inputs (D191). The check runs the student's script on inputs that are not in the task
    text and compares stdout and the exit code. The exam proves it by behaviour: for each script
    task a script that hard-codes the example output (a "cheat") gets FAIL, plausible near-misses
    get FAIL with a hint, and equally correct alternative implementations (printf instead of echo,
    `grep -c ''` instead of `while read`) get PASS (the check looks at behaviour, not at the
    commands typed). The unquoted `$1` in task 08 gets FAIL. A script is never run as root, and
    a script that hangs must not hang the check (it ends within 20 s with FAIL).

Docker tests (marker `docker`, run in CI) start `lf-lab-term` under the production flags of
app.runtime.container_spec (the #12 exam's `machine` fixture). They are parametrised over the
fixed task ids, so a missing lab fails them; they skip only when Docker itself is absent.
Students are fake; no real student data in fixtures.
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
LAB_DIR = CONTENT_ROOT / "bash" / "bash-1"
LAB_ID = "bash-1"
LAB_KEY = "bash/bash-1"

TASKS = [
    ("01-special-vars", "quiz"),
    ("02-greet", "hands-on"),
    ("03-for-loop", "hands-on"),
    ("04-exists", "hands-on"),
    ("05-usage", "hands-on"),
    ("06-count-lines", "hands-on"),
    ("07-backup", "hands-on"),
    ("08-space-trap", "hands-on"),
]
QUIZ_IDS = [t for t, k in TASKS if k == "quiz"]
HANDS_ON_IDS = [t for t, k in TASKS if k == "hands-on"]
SCRIPT_TASKS = {
    "02-greet": "greet.sh",
    "04-exists": "exists.sh",
    "05-usage": "usage.sh",
    "06-count-lines": "count_lines.sh",
    "07-backup": "backup.sh",
    "08-space-trap": "firstline.sh",
}
# exact names and values that the en and he task texts show in code spans
TEXT_PINS = {
    "02-greet": ["greet.sh", "Hello, "],
    "03-for-loop": ["file1", "file5", "files"],
    "04-exists": ["exists.sh", "found", "missing"],
    "05-usage": ["usage.sh", "Usage: usage.sh NAME"],
    "06-count-lines": ["count_lines.sh", "while read"],
    "07-backup": ["backup.sh", "backup.tar.gz", "notes"],
    "08-space-trap": ["firstline.sh", "my notes.txt"],
}
HEBREW = re.compile("[֐-׿]")
USAGE_LINE = "Usage: usage.sh NAME"


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


def test_lab_is_module_1_lesson_6_image_term():
    lab = _lab()
    assert (lab.module, lab.lesson) == (1, 6)
    assert lab.image == "term"
    assert lab.path.name == LAB_ID
    assert lab.path.parent.name == "bash"


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
    for opt in task.options:
        assert opt.get("en", "").strip() and opt.get("he", "").strip()
        assert not HEBREW.search(opt["en"])
    assert any(HEBREW.search(o["he"]) for o in task.options), "he options carry Hebrew words"


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


def test_solution_sh_writes_the_named_script_and_makes_it_executable():
    for task_id, script in SCRIPT_TASKS.items():
        code = _text(_task_dir(task_id) / "solution.sh")
        assert script in code, f"{task_id}/solution.sh never mentions {script}"
        assert "chmod" in code, f"{task_id}/solution.sh does not make the script executable"
        assert "sudo" not in code


def test_the_trap_is_not_given_away_in_the_task_text_but_explained_in_the_solution():
    d = _task_dir("08-space-trap")
    assert "quot" not in _text(d / "task.en.md").lower(), "task 08 must not hint at quoting"
    assert "quot" in _text(d / "solution.en.md").lower(), "solution 08 must explain the quotes"
    assert "my notes.txt" in _text(d / "setup.sh"), "setup 08 must create `my notes.txt`"


def test_check_scripts_do_not_hold_the_example_name_of_the_greet_task():
    """The checks run on hidden inputs: a name that the task text shows is not one of them."""
    task_text = _text(_task_dir("02-greet") / "task.en.md")
    examples = set()
    for span in _code_spans(task_text):
        for m in re.finditer(r"greet\.sh\s+\"?([A-Za-z][A-Za-z]+)\"?", span):
            if not m.group(1).isupper():
                examples.add(m.group(1))
        examples.update(re.findall(r"Hello, ([A-Za-z]+)", span))
    check = _text(_task_dir("02-greet") / "check.sh")
    for name in examples:
        assert not re.search(rf"\b{re.escape(name)}\b", check), (
            f"check.sh of 02-greet uses `{name}`, which is the example in the task text"
        )


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


# ---------------------------------------------------------------- non-docker: the quiz


def test_quiz_options_all_name_the_three_variables_and_one_is_right():
    task = next(t for t in _lab().tasks if t.id == "01-special-vars")
    q = task.question["en"]
    for var in ("$?", "$#", "$1"):
        assert var in q or all(var in o["en"] for o in task.options), (
            f"the quiz must be about {var}"
        )
    assert len(task.options) >= 3
    assert isinstance(task.correct, int) and 0 <= task.correct < len(task.options)
    raw = _raw_task("01-special-vars")
    assert raw.get("correct") == task.correct and isinstance(raw["correct"], int)
    en = [o["en"].strip() for o in task.options]
    assert len(set(en)) == len(en), "options must be distinct"
    full = [
        i for i, e in enumerate(en)
        if all(v in e for v in ("$?", "$#", "$1"))
        and re.search(r"exit (status|code)", e, re.I)
        and re.search(r"number of arguments", e, re.I)
        and re.search(r"first argument", e, re.I)
    ]
    assert full == [task.correct], (
        f"exactly the correct option names exit status, number of arguments and first argument; got {full}"
    )
    for i, o in enumerate(task.options):
        for var in ("$?", "$#", "$1"):
            assert var in o["en"] and var in o["he"], f"option {i} must show {var} in en and he"


# ---------------------------------------------------------------- docker helpers

HOME = "/home/student"


def _student(machine: str, code: str, timeout: int = 60):
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


def _write_script(name: str, body: str, mode: str = "755") -> str:
    assert "EOS_677" not in body
    return f"cat > ~/{name} <<'EOS_677'\n{body}\nEOS_677\nchmod {mode} ~/{name}\n"


def _run(machine: str, command: str):
    """Run a command line as student in its home; returns (stdout, exit code)."""
    res = _student(machine, f"cd ~ && {command}")
    return res.stdout, res.returncode


def _near_miss(machine, task_id, name, body, why, mode="755"):
    """Fresh state of the task, then a plausible wrong script: the check must refuse it."""
    _setup(machine, task_id)
    r = _student(machine, _write_script(name, body, mode))
    assert r.returncode == 0, r.stderr
    _assert_failing_with_hints(machine, task_id, f"for {why}")


def _alternative(machine, task_id, name, body, why):
    _setup(machine, task_id)
    r = _student(machine, _write_script(name, body))
    assert r.returncode == 0, r.stderr
    _assert_passing(machine, task_id, why)


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
    for name in SCRIPT_TASKS.get(task_id, "").split():
        out, rc = _run(machine, f"test -e ~/{name}")
        assert rc != 0, f"setup of {task_id} left the student's ~/{name} in place"


@pytest.mark.docker
@pytest.mark.parametrize(("task_id", "script"), sorted(SCRIPT_TASKS.items()))
def test_a_script_that_is_not_executable_fails_with_an_executable_hint(machine, task_id, script):
    _setup(machine, task_id)
    _solve(machine, task_id)
    r = _student(machine, f"chmod 644 ~/{script}")
    assert r.returncode == 0
    _assert_failing_with_hints(machine, task_id, "for a script without the x bit")
    res, parsed = _check(machine, task_id)
    hints = " ".join(it.hint or "" for it in parsed.items.values()).lower()
    assert "executable" in hints or "chmod" in hints, (
        f"a FAIL hint must tell the student about the executable bit:\n{res.stdout}"
    )


@pytest.mark.docker
@pytest.mark.parametrize(("task_id", "script"), sorted(SCRIPT_TASKS.items()))
def test_the_check_runs_the_script_and_never_as_root(machine, task_id, script):
    _setup(machine, task_id)
    _solve(machine, task_id)
    r = _student(machine, f"sed -i '1a id -u >> /tmp/who-ran-677' ~/{script}; rm -f /tmp/who-ran-677")
    assert r.returncode == 0
    _assert_passing(machine, task_id, "the solution with an id probe")
    who = _student(machine, "cat /tmp/who-ran-677").stdout.split()
    assert who, f"the check of {task_id} never ran ~/{script}"
    assert "0" not in who, f"the check of {task_id} ran the student's script as root: {who}"


@pytest.mark.docker
@pytest.mark.parametrize(("task_id", "script"), sorted(SCRIPT_TASKS.items()))
def test_a_script_that_hangs_does_not_hang_the_check(machine, task_id, script):
    _setup(machine, task_id)
    _solve(machine, task_id)
    r = _student(machine, f"sed -i '1a sleep 600' ~/{script}")
    assert r.returncode == 0
    _assert_failing_with_hints(machine, task_id, "for a script that sleeps 600 s")  # < 20 s inside


@pytest.mark.docker
@pytest.mark.parametrize(("task_id", "script"), sorted(SCRIPT_TASKS.items()))
def test_a_script_that_waits_for_input_does_not_hang_the_check(machine, task_id, script):
    _setup(machine, task_id)
    _solve(machine, task_id)
    r = _student(machine, f"sed -i '1a cat > /dev/null' ~/{script}")
    assert r.returncode == 0
    res, _ = _check(machine, task_id)  # returns inside the 20 s bound (asserted in _check)
    assert res.stdout.strip(), "the check printed nothing"


# ---------------------------------------------------------------- docker: the solutions behave


@pytest.mark.docker
def test_solution_scripts_do_what_the_issue_says_on_inputs_of_the_exam(machine):
    """Pins the meaning of every script on values that are in no task text (D191)."""
    for task_id in SCRIPT_TASKS:
        _setup(machine, task_id)
        _solve(machine, task_id)
    out, rc = _run(machine, "./greet.sh Noam")
    assert (out, rc) == ("Hello, Noam\n", 0)
    out, rc = _run(machine, "./greet.sh 'Mary Ann'")
    assert (out, rc) == ("Hello, Mary Ann\n", 0)
    # exists.sh: a file, a directory and a missing path
    _student(machine, "mkdir -p /tmp/ex677 && : > /tmp/ex677/f.txt")
    assert _run(machine, "./exists.sh /tmp/ex677/f.txt") == ("found\n", 0)
    assert _run(machine, "./exists.sh /tmp/ex677") == ("found\n", 0)
    assert _run(machine, "./exists.sh /tmp/ex677/nope") == ("missing\n", 0)
    # usage.sh
    assert _run(machine, "./usage.sh") == (USAGE_LINE + "\n", 1)
    assert _run(machine, "./usage.sh Dana")[1] == 0
    # count_lines.sh: the last line without a newline counts, an empty file is 0
    cases = {
        "a\\nb\\nc": "3", "a\\nb\\nc\\n": "3", "": "0", "only": "1", "\\n\\n": "2",
        "x y\\n\\nz": "3",
    }
    for i, (content, want) in enumerate(cases.items()):
        _student(machine, f"printf '{content}' > /tmp/cl677-{i}")
        assert _run(machine, f"./count_lines.sh /tmp/cl677-{i}") == (want + "\n", 0), content
    # firstline.sh on a name with a space
    _student(machine, "printf 'alpha line\\nsecond\\n' > '/tmp/two words.txt'")
    assert _run(machine, "./firstline.sh '/tmp/two words.txt'") == ("alpha line\n", 0)


@pytest.mark.docker
def test_backup_archive_holds_notes_with_relative_names_and_is_replaced_on_rerun(machine):
    _setup(machine, "07-backup")
    _solve(machine, "07-backup")
    _student(machine, "echo hidden-677 > ~/notes/new-hidden-677.txt; rm -f ~/backup.tar.gz")
    out, rc = _run(machine, "./backup.sh && tar tzf ~/backup.tar.gz")
    assert rc == 0, out
    names = [n.rstrip("/") for n in out.split("\n") if n]
    assert "notes" in names and "notes/new-hidden-677.txt" in names
    assert all(n == "notes" or n.startswith("notes/") for n in names), names
    _student(machine, "rm -f ~/notes/new-hidden-677.txt")
    out, rc = _run(machine, "./backup.sh && tar tzf ~/backup.tar.gz")
    assert rc == 0 and "new-hidden-677" not in out, "a re-run must replace the archive"


@pytest.mark.docker
def test_trap_setup_creates_a_file_with_a_space_in_its_name_with_two_lines(machine):
    _setup(machine, "08-space-trap")
    out, rc = _run(machine, "test -f ~/docs/'my notes.txt' && wc -l < ~/docs/'my notes.txt'")
    assert rc == 0, "setup 08 must create ~/docs/my notes.txt as a regular file"
    assert int(out.strip()) >= 2


# ---------------------------------------------------------------- docker: hard-coded answers fail


@pytest.mark.docker
def test_greet_hard_coded_example_output_fails(machine):
    task_text = _text(_task_dir("02-greet") / "task.en.md")
    names = set(re.findall(r"Hello, ([A-Za-z]+)", task_text)) | {"World", "NAME", "Alice"}
    _setup(machine, "02-greet")
    for name in sorted(names):
        body = f'#!/bin/bash\necho "Hello, {name}"'
        _student(machine, _write_script("greet.sh", body))
        _assert_failing_with_hints(machine, "02-greet", f"for a greet.sh that always says {name}")


@pytest.mark.docker
def test_greet_near_misses_fail(machine):
    for why, body in {
        "no comma": '#!/bin/bash\necho "Hello $1"',
        "exclamation mark": '#!/bin/bash\necho "Hello, $1!"',
        "wrong exit code": '#!/bin/bash\necho "Hello, $1"\nexit 3',
        "uses the second argument": '#!/bin/bash\necho "Hello, $2"',
        "prints to stderr": '#!/bin/bash\necho "Hello, $1" >&2',
    }.items():
        _near_miss(machine, "02-greet", "greet.sh", body, why)


@pytest.mark.docker
def test_greet_alternative_implementations_pass(machine):
    _alternative(machine, "02-greet", "greet.sh", '#!/bin/bash\nprintf "Hello, %s\\n" "$1"', "printf")
    _alternative(machine, "02-greet", "greet.sh", '#!/bin/bash\nname=$1\necho "Hello, ${name}"', "var")


@pytest.mark.docker
def test_for_loop_near_misses_fail(machine):
    cases = {
        "file0 to file4": "mkdir -p ~/files; for i in 0 1 2 3 4; do touch ~/files/file$i; done",
        "only file1 to file4": "mkdir -p ~/files; for i in 1 2 3 4; do touch ~/files/file$i; done",
        "a sixth file": "mkdir -p ~/files; for i in 1 2 3 4 5 6; do touch ~/files/file$i; done",
        "folders, not files": "mkdir -p ~/files/file1 ~/files/file2 ~/files/file3 ~/files/file4 ~/files/file5",
        "dashes in the names": "mkdir -p ~/files; for i in 1 2 3 4 5; do touch ~/files/file-$i; done",
        "files in the home folder": "for i in 1 2 3 4 5; do touch ~/file$i; done",
    }
    for why, code in cases.items():
        _setup(machine, "03-for-loop")
        assert _student(machine, code).returncode == 0
        _assert_failing_with_hints(machine, "03-for-loop", f"for {why}")


@pytest.mark.docker
def test_for_loop_alternative_ways_pass(machine):
    _setup(machine, "03-for-loop")
    _student(machine, "mkdir -p ~/files; for i in $(seq 1 5); do echo x > ~/files/file$i; done")
    _assert_passing(machine, "03-for-loop", "a seq loop that writes text into the files")


@pytest.mark.docker
def test_exists_hard_coded_answers_fail(machine):
    _setup(machine, "04-exists")
    for why, body in {
        "always found": "#!/bin/bash\necho found",
        "always missing": "#!/bin/bash\necho missing",
        "found for /etc/hostname only": '#!/bin/bash\n[ "$1" = /etc/hostname ] && echo found || echo missing',
    }.items():
        _student(machine, _write_script("exists.sh", body))
        _assert_failing_with_hints(machine, "04-exists", f"for {why}")


@pytest.mark.docker
def test_exists_near_misses_fail(machine):
    for why, body in {
        "directories are missing": '#!/bin/bash\nif [ -f "$1" ]; then echo found; else echo missing; fi',
        "files are missing (only directories count)": '#!/bin/bash\nif [ -d "$1" ]; then echo found; else echo missing; fi',
        "exit code follows the test": '#!/bin/bash\nif [ -e "$1" ]; then echo found; else echo missing; exit 1; fi',
        "capital letters": '#!/bin/bash\nif [ -e "$1" ]; then echo Found; else echo Missing; fi',
    }.items():
        _near_miss(machine, "04-exists", "exists.sh", body, why)


@pytest.mark.docker
def test_exists_alternative_implementation_passes(machine):
    body = '#!/bin/bash\ntest -e "$1" && echo found && exit 0\necho missing\nexit 0'
    _alternative(machine, "04-exists", "exists.sh", body, "a `test -e` script")


@pytest.mark.docker
def test_usage_hard_coded_and_near_miss_scripts_fail(machine):
    for why, body in {
        "always prints usage and exits 1": f'#!/bin/bash\necho "{USAGE_LINE}"\nexit 1',
        "always exits 0": f'#!/bin/bash\n[ $# -eq 0 ] && echo "{USAGE_LINE}"\nexit 0',
        "usage on stderr only": f'#!/bin/bash\n[ $# -eq 0 ] && {{ echo "{USAGE_LINE}" >&2; exit 1; }}\nexit 0',
        "wrong usage text": '#!/bin/bash\n[ $# -eq 0 ] && { echo "usage: usage.sh"; exit 1; }\nexit 0',
        "exit 2 for no argument": f'#!/bin/bash\n[ $# -eq 0 ] && {{ echo "{USAGE_LINE}"; exit 2; }}\nexit 0',
        "exit 1 with an argument": f'#!/bin/bash\n[ $# -eq 0 ] && echo "{USAGE_LINE}"\nexit 1',
    }.items():
        _near_miss(machine, "05-usage", "usage.sh", body, why)


@pytest.mark.docker
def test_usage_alternative_implementation_passes(machine):
    body = (
        f'#!/bin/bash\nif [ "$#" -lt 1 ]; then\n  echo "{USAGE_LINE}"\n  exit 1\nfi\nexit 0'
    )
    _alternative(machine, "05-usage", "usage.sh", body, "a `$#` test with -lt")


@pytest.mark.docker
def test_count_lines_hard_coded_numbers_fail(machine):
    _setup(machine, "06-count-lines")
    for n in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12):
        _student(machine, _write_script("count_lines.sh", f"#!/bin/bash\necho {n}"))
        _assert_failing_with_hints(machine, "06-count-lines", f"for a script that always prints {n}")


@pytest.mark.docker
def test_count_lines_near_misses_fail(machine):
    for why, body in {
        "wc -l misses the last line without a newline": '#!/bin/bash\nwc -l < "$1"',
        "wc -l prints the file name too": '#!/bin/bash\nwc -l "$1"',
        "a read loop that drops the last line": (
            '#!/bin/bash\nn=0\nwhile read -r line; do n=$((n+1)); done < "$1"\necho "$n"'
        ),
        "counts non-empty lines only": '#!/bin/bash\ngrep -c . "$1"',
        "off by one": (
            '#!/bin/bash\nn=0\nwhile read -r line || [ -n "$line" ]; do n=$((n+1)); done < "$1"\n'
            'echo $((n+1))'
        ),
    }.items():
        _near_miss(machine, "06-count-lines", "count_lines.sh", body, why)


@pytest.mark.docker
def test_count_lines_alternative_implementation_passes(machine):
    _alternative(machine, "06-count-lines", "count_lines.sh", "#!/bin/bash\ngrep -c '' \"$1\"", "grep -c ''")
    body = '#!/bin/bash\nawk \'END { print NR }\' "$1"'
    _alternative(machine, "06-count-lines", "count_lines.sh", body, "awk")


@pytest.mark.docker
def test_backup_hard_coded_or_wrong_archives_fail(machine):
    _setup(machine, "07-backup")
    snapshot = (
        "files=$(cd ~ && find notes -type f -printf \"'%p' \"); "
        "printf '#!/bin/bash\\ntar czf ~/backup.tar.gz -C ~ %s\\n' \"$files\" > ~/backup.sh; "
        "chmod +x ~/backup.sh"
    )
    assert _student(machine, snapshot).returncode == 0
    _assert_failing_with_hints(machine, "07-backup", "for a backup.sh with a fixed list of files")
    # the archive made once by hand, the script does nothing
    _student(machine, "cd ~ && tar czf ~/backup.tar.gz notes")
    _student(machine, _write_script("backup.sh", "#!/bin/bash\ntrue"))
    _assert_failing_with_hints(machine, "07-backup", "for an archive made once and a no-op script")


@pytest.mark.docker
def test_backup_near_misses_fail(machine):
    for why, body in {
        "absolute paths in the archive": "#!/bin/bash\ntar czf ~/backup.tar.gz ~/notes",
        "the home folder instead of notes": "#!/bin/bash\ntar czf ~/backup.tar.gz -C ~ .",
        "an uncompressed tar named .tar.gz": "#!/bin/bash\ntar cf ~/backup.tar.gz -C ~ notes",
        "a different file name": "#!/bin/bash\ntar czf ~/notes.tar.gz -C ~ notes",
        "the archive inside notes": "#!/bin/bash\ntar czf ~/notes/backup.tar.gz -C ~ notes",
        "only the top-level files, no folders": "#!/bin/bash\ntar czf ~/backup.tar.gz -C ~ --no-recursion notes",
    }.items():
        _near_miss(machine, "07-backup", "backup.sh", body, why)


@pytest.mark.docker
def test_backup_alternative_implementation_passes(machine):
    body = "#!/bin/bash\ncd ~ || exit 1\nrm -f backup.tar.gz\ntar -czf backup.tar.gz notes"
    _alternative(machine, "07-backup", "backup.sh", body, "cd and tar -czf")


@pytest.mark.docker
def test_trap_unquoted_variable_fails_and_the_quoted_one_passes(machine):
    for why, body in {
        "head -n 1 $1 (unquoted)": "#!/bin/bash\nhead -n 1 $1",
        "cat $1 | head (unquoted)": "#!/bin/bash\ncat $1 | head -n 1",
        "file=$1 then head $file (unquoted)": "#!/bin/bash\nfile=$1\nhead -n 1 $file",
    }.items():
        _near_miss(machine, "08-space-trap", "firstline.sh", body, why)
    res, parsed = _check(machine, "08-space-trap")
    hints = " ".join(it.hint or "" for it in parsed.items.values()).lower()
    assert "space" in hints or "quot" in hints, (
        f"the hint for the trap must lead the student to the quotes or the space:\n{res.stdout}"
    )
    _alternative(machine, "08-space-trap", "firstline.sh", '#!/bin/bash\nhead -n 1 "$1"', "quoted head")
    _alternative(
        machine, "08-space-trap", "firstline.sh", '#!/bin/bash\nread -r line < "$1"\necho "$line"',
        "read -r line < \"$1\"",
    )


@pytest.mark.docker
def test_trap_hard_coded_file_fails(machine):
    _setup(machine, "08-space-trap")
    body = '#!/bin/bash\nhead -n 1 "$HOME/docs/my notes.txt"'
    _student(machine, _write_script("firstline.sh", body))
    _assert_failing_with_hints(machine, "08-space-trap", "for a script that reads only the example file")
    body = '#!/bin/bash\necho "$(head -n 1 "$HOME/docs/my notes.txt")"'
    _student(machine, _write_script("firstline.sh", body))
    _assert_failing_with_hints(machine, "08-space-trap", "for a script that echoes the example line")


@pytest.mark.docker
def test_check_leaves_no_files_in_the_student_home_and_does_not_change_the_script(machine):
    """Hidden inputs must not litter the student's home or rewrite the student's work."""
    for task_id in ("02-greet", "06-count-lines", "08-space-trap"):
        _setup(machine, task_id)
        _solve(machine, task_id)
        before = _student(machine, "cd ~ && find . -xdev -type f | sort | xargs -d '\\n' sha256sum").stdout
        _assert_passing(machine, task_id, "the solution")
        after = _student(machine, "cd ~ && find . -xdev -type f | sort | xargs -d '\\n' sha256sum").stdout
        assert before == after, f"the check of {task_id} changed the student's home folder"
