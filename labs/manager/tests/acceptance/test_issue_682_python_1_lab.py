"""Acceptance tests for issue #682: lab content "Python 1: variables, types and conditions" (en + he), lesson 15 (D191).

The lab does not exist yet; these tests describe it. Choices pinned here (the builder follows them):

Location and metadata
    labs/content/python/python-1/lab.yaml   course dir `python`, lab id `python-1` (the validator
    requires id == directory name). module: 2, lesson: 15 (D191 v2 numbering), image: term,
    sudo: false (explicit key), status: published (D191: ships published once the review approves
    and the docker solve test passes), no internet, time_limit_min 45.

Tasks (ids and order are fixed; kind in brackets; the file the student writes in brackets)
    01-type-quiz   [quiz]       `type("5")` versus `type(5)`: str and int
    02-grade       [hands-on]   ~/grade.py N     prints `pass` (N >= 60) or `fail`
    03-even        [hands-on]   ~/even.py N      prints `even` or `odd`
    04-temp        [hands-on]   ~/temp.py C      prints C in Fahrenheit, F = C * 9 / 5 + 32, rounded to one
                                decimal place and always shown with one decimal (`212.0`, `98.6`).
                                C may be whole or decimal (`36.6`).
    05-leap        [hands-on]   ~/leap.py YEAR   prints `leap` or `not leap` (1900 is not, 2000 is)
    06-plus-quiz   [quiz]       what `"5" + 5` does: it raises a TypeError
    07-input-trap  [hands-on]   ~/add.py A B     prints the sum of two whole numbers (`3 4` prints `7`).
                                The deliberate trap (D191): the arguments arrive as strings, so
                                `sys.argv[1] + sys.argv[2]` prints `34`. The task text does not say
                                "int" or "convert"; solution.en.md explains `int(` and the string.

Script contract (all five scripts)
    Run as `python3 ~/<name>.py <args>` (the x bit and a shebang are NOT needed), print exactly one
    line on stdout and exit 0. The scripts take input only from the command line (the check closes
    stdin). N, YEAR, A, B are whole numbers (may be negative or 0); C may be decimal.

Files per hands-on task directory (tasks/<id>/): task.en.md, task.he.md, solution.en.md,
    solution.he.md (the validator's files), setup.sh, check.sh, and the exam-only `solution.sh`
    (the commands that solve the task, run as `student` through a login shell; it writes the
    script into the home folder with a heredoc). The validator and loader ignore it. All .sh files:
    LF endings, a `#!/bin/sh` or `#!/bin/bash` (or `/usr/bin/env`) shebang, mode 0755 in git. The
    task texts show the exact names and values in code spans (the script names above, `pass`,
    `fail`, `60`, `even`, `odd`, `1900`, `2000`, `add.py`).

check.sh protocol (spec section 7, asserted exactly): as in the #12 and #677 exams. Runs as root
    (no capabilities beyond SETUID/SETGID), finishes in under 20 s, prints `PASS <n>` or
    `FAIL <n> <hint>` per checklist item (N >= 2), exit 0 only when all pass, every FAIL line
    carries a non-empty hint. setup.sh: run as root, exit 0, self-contained, idempotent, and
    replaces the state its task is about (after the solution a second run brings the task back to
    failing: it removes the student's script).

Hidden inputs (D191). The check runs the student's script on inputs that are not in the task text
    and compares stdout and the exit code. The exam proves it by behaviour: a script that hard-codes
    the example output gets FAIL, plausible near-misses (`>` for `>=`, no 100/400 rule, no rounding,
    comparing the string argument, ...) get FAIL with a hint, and equally correct alternative
    implementations get PASS (the check looks at behaviour, not at the code typed). A script is
    never run as root, and a script that hangs or reads stdin must not hang the check (it ends
    within 20 s with FAIL).

Quizzes: `question` and `options` in en and he (Latin technical terms inside Hebrew text), `correct`
    an int index. The correct option of 01 is the only one where `str` comes before `int` (both
    named); the correct option of 06 is the only one that names `TypeError`, and wrong options
    include the tempting `10` and `55`.

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
LAB_DIR = CONTENT_ROOT / "python" / "python-1"
LAB_ID = "python-1"
LAB_KEY = "python/python-1"

TASKS = [
    ("01-type-quiz", "quiz"),
    ("02-grade", "hands-on"),
    ("03-even", "hands-on"),
    ("04-temp", "hands-on"),
    ("05-leap", "hands-on"),
    ("06-plus-quiz", "quiz"),
    ("07-input-trap", "hands-on"),
]
QUIZ_IDS = [t for t, k in TASKS if k == "quiz"]
HANDS_ON_IDS = [t for t, k in TASKS if k == "hands-on"]
SCRIPT_TASKS = {
    "02-grade": "grade.py",
    "03-even": "even.py",
    "04-temp": "temp.py",
    "05-leap": "leap.py",
    "07-input-trap": "add.py",
}
# exact names and values that the en and he task texts show in code spans
TEXT_PINS = {
    "02-grade": ["grade.py", "pass", "fail", "60"],
    "03-even": ["even.py", "even", "odd"],
    "04-temp": ["temp.py"],
    "05-leap": ["leap.py", "1900", "2000"],
    "07-input-trap": ["add.py"],
}
HEBREW = re.compile("[֐-׿]")

# (command-line arguments, expected stdout) on values that are in no task text
GRADE = [
    ("0", "fail"), ("9", "fail"), ("59", "fail"), ("60", "pass"), ("61", "pass"),
    ("75", "pass"), ("100", "pass"), ("5", "fail"),
]
EVEN = [
    ("0", "even"), ("7", "odd"), ("10", "even"), ("-3", "odd"), ("-8", "even"),
    ("1001", "odd"), ("12345678", "even"),
]
TEMP = [
    ("0", "32.0"), ("100", "212.0"), ("37", "98.6"), ("-40", "-40.0"), ("36.6", "97.9"),
    ("21.5", "70.7"), ("25", "77.0"), ("18.3", "64.9"),
]
LEAP = [
    ("1900", "not leap"), ("2000", "leap"), ("2024", "leap"), ("2023", "not leap"),
    ("2100", "not leap"), ("2400", "leap"), ("1996", "leap"), ("1800", "not leap"),
    ("1600", "leap"), ("2019", "not leap"),
]
ADD = [("3 4", "7"), ("10 25", "35"), ("-2 9", "7"), ("0 0", "0"), ("100 200", "300")]
CASES = {
    "grade.py": GRADE, "even.py": EVEN, "temp.py": TEMP, "leap.py": LEAP, "add.py": ADD,
}


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


def _task(task_id: str):
    return next(t for t in _lab().tasks if t.id == task_id)


# ---------------------------------------------------------------- non-docker: validator and shape


def test_validator_accepts_the_lab_with_no_errors():
    res = _load()
    assert LAB_KEY not in res.errors, res.errors.get(LAB_KEY)
    assert LAB_ID in res.labs, f"{LAB_ID} not loaded; all errors: {res.errors}"


def test_lab_is_module_2_lesson_15_image_term():
    lab = _lab()
    assert (lab.module, lab.lesson) == (2, 15)
    assert lab.image == "term"
    assert lab.path.name == LAB_ID
    assert lab.path.parent.name == "python"


def test_status_is_published():
    assert _lab().status == "published"
    assert _raw().get("status") == "published"


def test_sudo_is_false_explicitly_and_internet_is_off():
    raw = _raw()
    assert raw.get("sudo") is False, "lab.yaml must say `sudo: false`"
    assert _lab().sudo is False
    assert not raw.get("internet", False)
    assert not raw.get("allow_domains", [])


def test_time_limit_is_about_45_minutes():
    assert _raw().get("time_limit_min") == 45


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
    en = [o["en"].strip() for o in task.options]
    assert len(set(en)) == len(en), "options must be distinct"
    raw = _raw_task(task_id)
    assert isinstance(raw.get("correct"), int) and raw["correct"] == task.correct
    assert 0 <= task.correct < len(task.options)


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


def test_solution_sh_writes_the_named_script_and_uses_no_sudo():
    for task_id, script in SCRIPT_TASKS.items():
        code = _text(_task_dir(task_id) / "solution.sh")
        assert script in code, f"{task_id}/solution.sh never mentions {script}"
        assert "sudo" not in code


def test_the_trap_is_not_given_away_in_the_task_text_but_explained_in_the_solution():
    d = _task_dir("07-input-trap")
    task_text = _text(d / "task.en.md").lower()
    assert "int(" not in task_text and "convert" not in task_text, (
        "task 07 must not hint at converting the arguments"
    )
    solution = _text(d / "solution.en.md")
    assert "int(" in solution, "solution 07 must show int(...)"
    assert "string" in solution.lower(), "solution 07 must explain that input arrives as a string"
    assert "34" in solution, "solution 07 must show what the string sum prints (`34`)"


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


def test_check_scripts_run_python_as_python3_not_as_root():
    """The check runs the student's code as `student` (a drop of privileges is visible in the file)."""
    for task_id in SCRIPT_TASKS:
        code = _text(_task_dir(task_id) / "check.sh")
        assert "python3" in code, f"check.sh of {task_id} never runs python3"
        assert re.search(r"\bsu\b|\bsetpriv\b|\bsetuidgid\b|\brunuser\b", code), (
            f"check.sh of {task_id} must run the student's script as student"
        )
        assert "timeout" in code, f"check.sh of {task_id} must bound the student's script"


# ---------------------------------------------------------------- non-docker: the quizzes


def test_type_quiz_is_about_str_and_int_and_one_option_is_right():
    task = _task("01-type-quiz")
    q = task.question["en"]
    assert 'type("5")' in q and "type(5)" in q, 'the quiz must show type("5") and type(5)'
    for opt in task.options:
        assert "str" in opt["en"] or "int" in opt["en"] or "float" in opt["en"], opt
    right = []
    for i, o in enumerate(task.options):
        s = re.search(r"\bstr\b", o["en"])
        n = re.search(r"\bint\b", o["en"])
        if s and n and s.start() < n.start():
            right.append(i)
    assert right == [task.correct], (
        f"exactly the correct option names str first and then int; got {right}, correct={task.correct}"
    )
    he_correct = task.options[task.correct]["he"]
    assert "str" in he_correct and "int" in he_correct, "the he option keeps str and int in English"
    wrong = [o["en"] for i, o in enumerate(task.options) if i != task.correct]
    assert any(
        (m := re.search(r"\bint\b", o)) and (n := re.search(r"\bstr\b", o)) and m.start() < n.start()
        for o in wrong
    ), "a distractor should swap the two types"


def test_plus_quiz_is_about_string_plus_int_and_one_option_is_right():
    task = _task("06-plus-quiz")
    assert '"5" + 5' in task.question["en"], 'the quiz must show "5" + 5'
    names = [i for i, o in enumerate(task.options) if re.search(r"\bTypeError\b", o["en"])]
    assert names == [task.correct], f"only the correct option names TypeError; got {names}"
    assert re.search(r"\bTypeError\b", task.options[task.correct]["he"]), "he keeps TypeError in English"
    joined = [o["en"] for i, o in enumerate(task.options) if i != task.correct]
    assert any(re.search(r"\b10\b", o) for o in joined), "a distractor says the result is 10"
    assert any(re.search(r"\b55\b", o) for o in joined), "a distractor says the result is 55"


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


def _write_script(name: str, body: str) -> str:
    assert "EOS_682" not in body
    return f"cat > ~/{name} <<'EOS_682'\n{body}\nEOS_682\n"


def _run(machine: str, command: str):
    """Run a command line as student in its home; returns (stdout, exit code)."""
    res = _student(machine, f"cd ~ && {command}")
    return res.stdout, res.returncode


def _task_of(script: str) -> str:
    return next(t for t, s in SCRIPT_TASKS.items() if s == script)


def _near_miss(machine, script, body, why):
    """Fresh state of the task, then a plausible wrong script: the check must refuse it."""
    task_id = _task_of(script)
    _setup(machine, task_id)
    r = _student(machine, _write_script(script, body))
    assert r.returncode == 0, r.stderr
    _assert_failing_with_hints(machine, task_id, f"for {why}")


def _alternative(machine, script, body, why):
    task_id = _task_of(script)
    _setup(machine, task_id)
    r = _student(machine, _write_script(script, body))
    assert r.returncode == 0, r.stderr
    _assert_passing(machine, task_id, why)


def _expected_script(script: str) -> str:
    """A table-driven reference of the contract, for the cheat tests."""
    return {
        "grade.py": "import sys\nn = int(sys.argv[1])\nprint('pass' if n >= 60 else 'fail')",
        "even.py": "import sys\nn = int(sys.argv[1])\nprint('even' if n % 2 == 0 else 'odd')",
        "temp.py": "import sys\nc = float(sys.argv[1])\nprint(f'{round(c * 9 / 5 + 32, 1):.1f}')",
        "leap.py": (
            "import sys\ny = int(sys.argv[1])\n"
            "print('leap' if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 'not leap')"
        ),
        "add.py": "import sys\nprint(int(sys.argv[1]) + int(sys.argv[2]))",
    }[script]


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
    _, rc = _run(machine, f"test -e ~/{SCRIPT_TASKS[task_id]}")
    assert rc != 0, f"setup of {task_id} left the student's ~/{SCRIPT_TASKS[task_id]} in place"


@pytest.mark.docker
@pytest.mark.parametrize(("task_id", "script"), sorted(SCRIPT_TASKS.items()))
def test_a_python_script_needs_no_executable_bit(machine, task_id, script):
    """Python files run as `python3 ~/name.py`: mode 644 and no shebang must still pass."""
    _setup(machine, task_id)
    _solve(machine, task_id)
    r = _student(machine, f"chmod 644 ~/{script}")
    assert r.returncode == 0
    _assert_passing(machine, task_id, "the solution without an x bit")


@pytest.mark.docker
@pytest.mark.parametrize(("task_id", "script"), sorted(SCRIPT_TASKS.items()))
def test_a_missing_script_and_a_broken_script_fail_with_hints(machine, task_id, script):
    _setup(machine, task_id)
    _assert_failing_with_hints(machine, task_id, "with no script")
    for why, body in {
        "a syntax error": "print('oops'",
        "a traceback": "raise SystemExit(3)",
        "an empty file": "",
    }.items():
        _student(machine, _write_script(script, body))
        _assert_failing_with_hints(machine, task_id, f"for {why}")


@pytest.mark.docker
@pytest.mark.parametrize(("task_id", "script"), sorted(SCRIPT_TASKS.items()))
def test_the_check_runs_the_script_and_never_as_root(machine, task_id, script):
    _setup(machine, task_id)
    _solve(machine, task_id)
    probe = "import os; open('/tmp/who-ran-682', 'a').write(str(os.getuid()) + chr(10))"
    r = _student(machine, f"rm -f /tmp/who-ran-682; sed -i '1i {probe}' ~/{script}")
    assert r.returncode == 0
    _assert_passing(machine, task_id, "the solution with an id probe")
    who = _student(machine, "cat /tmp/who-ran-682").stdout.split()
    assert who, f"the check of {task_id} never ran ~/{script}"
    assert "0" not in who, f"the check of {task_id} ran the student's script as root: {who}"


@pytest.mark.docker
@pytest.mark.parametrize(("task_id", "script"), sorted(SCRIPT_TASKS.items()))
def test_a_script_that_hangs_does_not_hang_the_check(machine, task_id, script):
    _setup(machine, task_id)
    _solve(machine, task_id)
    r = _student(machine, f"sed -i '1i import time; time.sleep(600)' ~/{script}")
    assert r.returncode == 0
    _assert_failing_with_hints(machine, task_id, "for a script that sleeps 600 s")  # < 20 s inside


@pytest.mark.docker
@pytest.mark.parametrize(("task_id", "script"), sorted(SCRIPT_TASKS.items()))
def test_a_script_that_waits_for_input_does_not_hang_the_check(machine, task_id, script):
    _setup(machine, task_id)
    _solve(machine, task_id)
    r = _student(machine, f"sed -i '1i import sys; sys.stdin.read()' ~/{script}")
    assert r.returncode == 0
    res, _ = _check(machine, task_id)  # returns inside the 20 s bound (asserted in _check)
    assert res.stdout.strip(), "the check printed nothing"


@pytest.mark.docker
def test_the_check_leaves_the_student_home_and_the_script_unchanged(machine):
    """Hidden inputs must not litter the student's home or rewrite the student's work."""
    for task_id in SCRIPT_TASKS:
        _setup(machine, task_id)
        _solve(machine, task_id)
        listing = "cd ~ && find . -xdev -type f -not -name '*.pyc' | sort | xargs -d '\\n' sha256sum"
        before = _student(machine, listing).stdout
        _assert_passing(machine, task_id, "the solution")
        after = _student(machine, listing).stdout
        assert before == after, f"the check of {task_id} changed the student's home folder"


# ---------------------------------------------------------------- docker: the solutions behave


@pytest.mark.docker
@pytest.mark.parametrize(("task_id", "script"), sorted(SCRIPT_TASKS.items()))
def test_solution_scripts_do_what_the_issue_says_on_inputs_of_the_exam(machine, task_id, script):
    """Pins the meaning of every script on values that are in no task text (D191)."""
    _setup(machine, task_id)
    _solve(machine, task_id)
    for args, want in CASES[script]:
        assert _run(machine, f"python3 {script} {args}") == (want + "\n", 0), (script, args)


# ---------------------------------------------------------------- docker: the reference scripts pass


@pytest.mark.docker
@pytest.mark.parametrize(("task_id", "script"), sorted(SCRIPT_TASKS.items()))
def test_a_plain_reference_implementation_passes(machine, task_id, script):
    _alternative(machine, script, _expected_script(script), "a plain implementation")


# ---------------------------------------------------------------- docker: hard-coded answers fail


@pytest.mark.docker
def test_grade_hard_coded_answers_fail(machine):
    _setup(machine, "02-grade")
    task_text = _text(_task_dir("02-grade") / "task.en.md")
    examples = set(re.findall(r"grade\.py\s+(\d+)", task_text)) | {"75", "59", "60", "40"}
    cheats = {"always pass": "print('pass')", "always fail": "print('fail')"}
    for n in sorted(examples):
        cheats[f"only {n} is known"] = (
            f"import sys\nprint({'pass' if int(n) >= 60 else 'fail'!r} if sys.argv[1] == {n!r} else 'x')"
        )
    for why, body in cheats.items():
        _student(machine, _write_script("grade.py", body))
        _assert_failing_with_hints(machine, "02-grade", f"for a script that {why}")


@pytest.mark.docker
def test_grade_near_misses_fail(machine):
    for why, body in {
        "uses > instead of >=": "import sys\nprint('pass' if int(sys.argv[1]) > 60 else 'fail')",
        "passes from 50": "import sys\nprint('pass' if int(sys.argv[1]) >= 50 else 'fail')",
        "passes from 61 by <=": "import sys\nprint('fail' if int(sys.argv[1]) <= 60 else 'pass')",
        "compares the string to a string": "import sys\nprint('pass' if sys.argv[1] >= '60' else 'fail')",
        "compares the string to a number": "import sys\nprint('pass' if sys.argv[1] >= 60 else 'fail')",
        "capital letters": "import sys\nprint('Pass' if int(sys.argv[1]) >= 60 else 'Fail')",
        "prints a bool": "import sys\nprint(int(sys.argv[1]) >= 60)",
        "wrong exit code": "import sys\nprint('pass' if int(sys.argv[1]) >= 60 else 'fail')\nsys.exit(1)",
        "prints to stderr": (
            "import sys\nprint('pass' if int(sys.argv[1]) >= 60 else 'fail', file=sys.stderr)"
        ),
    }.items():
        _near_miss(machine, "grade.py", body, why)


@pytest.mark.docker
def test_grade_alternative_implementations_pass(machine):
    body = "import sys\nif int(sys.argv[1]) < 60:\n    print('fail')\nelse:\n    print('pass')"
    _alternative(machine, "grade.py", body, "an if/else with <")
    body = "import sys\nn = int(sys.argv[1])\nresult = 'fail'\nif n >= 60:\n    result = 'pass'\nprint(result)"
    _alternative(machine, "grade.py", body, "a result variable")
    body = "import sys\nprint('pass' if float(sys.argv[1]) >= 60 else 'fail')"
    _alternative(machine, "grade.py", body, "float instead of int")


@pytest.mark.docker
def test_even_hard_coded_and_near_miss_scripts_fail(machine):
    for why, body in {
        "always even": "print('even')",
        "always odd": "print('odd')",
        "inverted": "import sys\nprint('odd' if int(sys.argv[1]) % 2 == 0 else 'even')",
        "tests == 1 and fails on negatives": (
            "import sys\nn = int(sys.argv[1])\nprint('odd' if n % 2 == 1 else 'even')"
            "\nif n < 0:\n    print('odd')"
        ),
        "crashes on the string": "import sys\nprint('even' if sys.argv[1] % 2 == 0 else 'odd')",
        "looks at the length of the text": (
            "import sys\nprint('even' if len(sys.argv[1]) % 2 == 0 else 'odd')"
        ),
        "capital letters": "import sys\nprint('Even' if int(sys.argv[1]) % 2 == 0 else 'Odd')",
        "zero counts as odd": (
            "import sys\nn = int(sys.argv[1])\nprint('even' if n != 0 and n % 2 == 0 else 'odd')"
        ),
        "a prompt on stdout": "import sys\nprint('Result: ' + ('even' if int(sys.argv[1]) % 2 == 0 else 'odd'))",
    }.items():
        _near_miss(machine, "even.py", body, why)


@pytest.mark.docker
def test_even_alternative_implementations_pass(machine):
    _alternative(
        machine, "even.py",
        "import sys\nprint('odd' if int(sys.argv[1]) & 1 else 'even')", "a bit test",
    )
    _alternative(
        machine, "even.py",
        "import sys\nn = int(sys.argv[1])\nif n % 2:\n    print('odd')\nelse:\n    print('even')",
        "a truthy remainder",
    )


@pytest.mark.docker
def test_temp_hard_coded_and_near_miss_scripts_fail(machine):
    for why, body in {
        "a fixed answer": "print('212.0')",
        "no rounding": "import sys\nprint(float(sys.argv[1]) * 9 / 5 + 32)",
        "rounds to two decimals": "import sys\nprint(f'{float(sys.argv[1]) * 9 / 5 + 32:.2f}')",
        "rounds to a whole number": "import sys\nprint(round(float(sys.argv[1]) * 9 / 5 + 32))",
        "truncates to an int": "import sys\nprint(int(float(sys.argv[1]) * 9 / 5 + 32))",
        "integer division": "import sys\nprint(round(float(sys.argv[1]) * (9 // 5) + 32, 1))",
        "forgets the + 32": "import sys\nprint(f'{round(float(sys.argv[1]) * 9 / 5, 1):.1f}')",
        "the Kelvin formula": "import sys\nprint(f'{round(float(sys.argv[1]) + 273.15, 1):.1f}')",
        "accepts whole numbers only": (
            "import sys\nprint(f'{round(int(sys.argv[1]) * 9 / 5 + 32, 1):.1f}')"
        ),
        "a unit in the output": (
            "import sys\nprint(f'{round(float(sys.argv[1]) * 9 / 5 + 32, 1):.1f}F')"
        ),
        "concatenates the string": (
            "import sys\nprint(sys.argv[1] * 9 / 5 + 32)"
        ),
    }.items():
        _near_miss(machine, "temp.py", body, why)


@pytest.mark.docker
def test_temp_alternative_implementations_pass(machine):
    _alternative(
        machine, "temp.py",
        "import sys\nc = float(sys.argv[1])\nf = c * 1.8 + 32\nprint('%.1f' % round(f, 1))",
        "a 1.8 factor and a % format",
    )
    _alternative(
        machine, "temp.py",
        "import sys\nc = float(sys.argv[1])\nf = c * 9 / 5 + 32\nprint(round(f, 1))",
        "round(f, 1) printed as is",
    )


@pytest.mark.docker
def test_leap_hard_coded_and_near_miss_scripts_fail(machine):
    for why, body in {
        "only 1900 and 2000 are known": (
            "import sys\nprint({'1900': 'not leap', '2000': 'leap'}.get(sys.argv[1], 'leap'))"
        ),
        "every fourth year (1900 is wrong)": (
            "import sys\nprint('leap' if int(sys.argv[1]) % 4 == 0 else 'not leap')"
        ),
        "no 400 rule (2000 is wrong)": (
            "import sys\ny = int(sys.argv[1])\nprint('leap' if y % 4 == 0 and y % 100 != 0 else 'not leap')"
        ),
        "only the 400 rule": (
            "import sys\nprint('leap' if int(sys.argv[1]) % 400 == 0 else 'not leap')"
        ),
        "the 100 rule without the 4 rule": (
            "import sys\ny = int(sys.argv[1])\nprint('leap' if y % 100 != 0 or y % 400 == 0 else 'not leap')"
        ),
        "or instead of and": (
            "import sys\ny = int(sys.argv[1])\nprint('leap' if y % 4 == 0 or (y % 100 != 0 and y % 400 == 0) else 'not leap')"
        ),
        "prints yes and no": (
            "import sys\ny = int(sys.argv[1])\nprint('yes' if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 'no')"
        ),
        "prints a bool": (
            "import sys\ny = int(sys.argv[1])\nprint(y % 4 == 0 and (y % 100 != 0 or y % 400 == 0))"
        ),
        "compares the string argument": (
            "import sys\ny = sys.argv[1]\nprint('leap' if y[-2:] in ('00', '04', '08') else 'not leap')"
        ),
    }.items():
        _near_miss(machine, "leap.py", body, why)


@pytest.mark.docker
def test_leap_alternative_implementations_pass(machine):
    _alternative(
        machine, "leap.py",
        "import calendar\nimport sys\nprint('leap' if calendar.isleap(int(sys.argv[1])) else 'not leap')",
        "calendar.isleap",
    )
    body = (
        "import sys\ny = int(sys.argv[1])\n"
        "if y % 400 == 0:\n    print('leap')\nelif y % 100 == 0:\n    print('not leap')\n"
        "elif y % 4 == 0:\n    print('leap')\nelse:\n    print('not leap')"
    )
    _alternative(machine, "leap.py", body, "an if/elif ladder")


@pytest.mark.docker
def test_the_trap_string_arguments_fail_and_the_converted_sum_passes(machine):
    for why, body in {
        "concatenates the two strings": "import sys\nprint(sys.argv[1] + sys.argv[2])",
        "repeats one string": "import sys\nprint(sys.argv[1] * 2)",
        "converts only the first argument": "import sys\nprint(int(sys.argv[1]) + sys.argv[2])",
        "a fixed answer for the example": "print(7)",
        "prints a float": "import sys\nprint(float(sys.argv[1]) + float(sys.argv[2]))",
        "multiplies": "import sys\nprint(int(sys.argv[1]) * int(sys.argv[2]))",
        "ignores the second argument": "import sys\nprint(int(sys.argv[1]) + int(sys.argv[1]))",
    }.items():
        _near_miss(machine, "add.py", body, why)
    res, parsed = _check(machine, "07-input-trap")
    hints = " ".join(it.hint or "" for it in parsed.items.values()).lower()
    assert "string" in hints or "int" in hints or "number" in hints, (
        f"the hint for the trap must lead the student to the type of the arguments:\n{res.stdout}"
    )
    _alternative(
        machine, "add.py",
        "import sys\na = int(sys.argv[1])\nb = int(sys.argv[2])\nprint(a + b)", "two int() calls",
    )
    _alternative(
        machine, "add.py",
        "import sys\nprint(sum(int(x) for x in sys.argv[1:3]))", "sum() over converted arguments",
    )
