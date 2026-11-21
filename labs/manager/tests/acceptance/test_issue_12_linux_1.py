"""Acceptance tests for issue #12: lab content "Linux 1: navigation, files, permissions" (en + he).

The lab does not exist yet; these tests describe it. Choices pinned here (the builder follows them):

Location and metadata
    labs/content/linux/linux-1/lab.yaml   course dir `linux`, lab id `linux-1` (the validator
    requires id == directory name). module: 1, lesson: 5 (issue comment: lesson 5 of module 1,
    D16). status: draft, sudo: false (explicit key), image: term.

Tasks (ids and order are fixed; kind in brackets)
    01-pwd-ls [quiz]          02-directory-tree [hands-on]   03-create-file [hands-on]
    04-copy-move [hands-on]   05-find-file [hands-on]        06-grep-log [hands-on]
    07-chmod [hands-on]       08-rwx-quiz [quiz]             09-chgrp [hands-on]  (D42)
    10-chown-quiz [quiz]      (D42: what `chown` needs, what the `ls -l` columns mean)

Files per hands-on task directory (tasks/<id>/): task.en.md, task.he.md, solution.en.md,
    solution.he.md (the validator's files), setup.sh, check.sh, and the exam-only
    `solution.sh` (the commands that solve the task; run as `student` through a login shell).
    The validator and loader ignore `solution.sh` (they only look at the named files); it is
    never part of Lab/Task data and the manager serves task text only from the named files.
    All three .sh files: LF endings, a `#!/bin/sh` or `#!/bin/bash` (or `/usr/bin/env`) shebang,
    mode 0755 in git.

check.sh protocol (spec section 7, asserted exactly)
    Runs as root in the container (no capabilities beyond SETUID/SETGID, as in production, so
    root cannot read or write student-private files), finishes in under 20 s, prints one line per
    checklist item in lab.yaml: `PASS <n>` or `FAIL <n> <hint>` (n = 1..N, N = len(checklist) >= 2),
    exit code 0 only when every item passes. Every FAIL line carries a non-empty hint.
setup.sh: run as root by `docker exec` (spec section 5), exit 0, self-contained on a fresh
    container (builds the whole starting state of its task), idempotent, and replaces the state
    its task is about (a second run, also after the solution, brings the task back to failing).
chgrp task (D42): after setup, `su - student` has at least one supplementary group besides its
    primary one (the image or setup supplies it; the group name is not pinned); the solution is a
    plain `chgrp` as student, no sudo.

Docker tests (marker `docker`, run in CI) start `lf-lab-term` ($LF_LAB_TERM_IMAGE, else built from
labs/images/term) under the production flags of app.runtime.container_spec: runsc when Docker
has it, cap-drop ALL + SETUID/SETGID, no-new-privileges, the same tmpfs mounts. They are
parametrised over the fixed task ids above, so a missing lab fails them; it never skips or
passes them silently. They skip only when Docker itself is absent.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import lab_image
import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
CONTENT_ROOT = REPO_ROOT / "labs" / "content"
LAB_DIR = CONTENT_ROOT / "linux" / "linux-1"
LAB_ID = "linux-1"
LAB_KEY = "linux/linux-1"
OWNER_MD = REPO_ROOT / "docs" / "factory" / "OWNER.md"

TASKS = [
    ("01-pwd-ls", "quiz"),
    ("02-directory-tree", "hands-on"),
    ("03-create-file", "hands-on"),
    ("04-copy-move", "hands-on"),
    ("05-find-file", "hands-on"),
    ("06-grep-log", "hands-on"),
    ("07-chmod", "hands-on"),
    ("08-rwx-quiz", "quiz"),
    ("09-chgrp", "hands-on"),
    ("10-chown-quiz", "quiz"),
]
QUIZ_IDS = [t for t, k in TASKS if k == "quiz"]
HANDS_ON_IDS = [t for t, k in TASKS if k == "hands-on"]
HEBREW = re.compile("[֐-׿]")


# ---------------------------------------------------------------- helpers


def _load():
    from app.content import load_labs

    return load_labs(CONTENT_ROOT)


def _lab():
    res = _load()
    assert LAB_ID in res.labs, (
        f"lab {LAB_ID} is not loaded; errors: {res.errors.get(LAB_KEY)} "
        f"(is {LAB_DIR} missing?)"
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


def _text(path: Path) -> str:
    assert path.is_file(), f"{path} is missing"
    return path.read_text(encoding="utf-8")


def _all_shell_files() -> list[Path]:
    assert LAB_DIR.is_dir(), f"{LAB_DIR} is missing"
    files = sorted(LAB_DIR.rglob("*.sh"))
    assert files, "the lab has no shell files"
    return files


def _code_spans(md: str) -> list[str]:
    fences = re.findall(r"```.*?\n(.*?)```", md, flags=re.S)
    rest = re.sub(r"```.*?```", "", md, flags=re.S)
    return fences + re.findall(r"`([^`\n]+)`", rest)


# ---------------------------------------------------------------- non-docker: validator and shape


def test_validator_accepts_the_lab_with_no_errors():
    res = _load()
    assert LAB_KEY not in res.errors, res.errors.get(LAB_KEY)
    assert LAB_ID in res.labs, f"{LAB_ID} not loaded; all errors: {res.errors}"


def test_lab_is_module_1_lesson_5_image_term():
    lab = _lab()
    assert (lab.module, lab.lesson) == (1, 5)
    assert lab.image == "term"
    assert lab.path.name == LAB_ID


def test_status_is_published():
    assert _lab().status == "published"
    assert _raw().get("status") == "published"


def test_sudo_is_false_explicitly_and_internet_is_off():
    raw = _raw()
    assert raw.get("sudo") is False, "lab.yaml must say `sudo: false` (D42)"
    assert _lab().sudo is False
    assert not raw.get("internet", False)
    assert not raw.get("allow_domains", [])


def test_task_order_and_kinds_match_the_plan():
    got = [(t.id, t.kind) for t in _lab().tasks]
    assert got == TASKS


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
    # he options may be bare commands (Latin), but most options carry Hebrew words
    assert any(HEBREW.search(o["he"]) for o in task.options) or all(
        o["he"].strip() == o["en"].strip() for o in task.options
    )


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
        md = _text(d / name)
        spans = _code_spans(md)
        assert spans, f"{task_id}/{name} shows no command in a code span"
        for span in spans:
            assert not HEBREW.search(span), f"Hebrew inside a code span in {name}: {span!r}"


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
    found = {p.parent.name for p in LAB_DIR.rglob("solution.sh")}
    assert found == set(HANDS_ON_IDS)


def test_owner_md_asks_owner_to_read_and_publish_linux_1():
    text = _text(OWNER_MD)
    lines = [
        ln for ln in text.splitlines()
        if ln.lstrip().startswith("- [ ]") and re.search(r"Linux 1", ln)
        and re.search(r"publish", ln, re.I) and re.search(r"\bread\b", ln, re.I)
    ]
    assert lines, "OWNER.md needs an open `- [ ]` line asking to read and publish Linux 1"


# ---------------------------------------------------------------- non-docker: quizzes


@pytest.mark.parametrize("task_id", QUIZ_IDS)
def test_quiz_has_one_correct_answer_the_validator_accepts_and_wrong_options(task_id):
    task = next(t for t in _lab().tasks if t.id == task_id)
    assert len(task.options) >= 3, "a quiz offers the right answer and at least two wrong ones"
    assert isinstance(task.correct, int) and 0 <= task.correct < len(task.options)
    en = [o["en"].strip() for o in task.options]
    assert len(set(en)) == len(en), "options must be distinct"
    wrong = [o for i, o in enumerate(task.options) if i != task.correct]
    assert len(wrong) >= 2
    raw = _raw_task(task_id)
    assert raw.get("correct") == task.correct and isinstance(raw["correct"], int)


def test_quiz_answers_are_not_all_in_the_same_position():
    idx = {t.id: t.correct for t in _lab().tasks if t.kind == "quiz"}
    assert len(set(idx.values())) > 1, f"every quiz has the correct option at {idx}"


# ---------------------------------------------------------------- docker helpers


def _docker(*args, timeout=600, check=True, stdin=None):
    res = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout, input=stdin
    )
    if check:
        assert res.returncode == 0, f"docker {' '.join(args)} failed: {res.stderr}"
    return res


@pytest.fixture(scope="module")
def docker_available():
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    if _docker("info", check=False, timeout=60).returncode != 0:
        pytest.skip("the Docker daemon is not reachable")


@pytest.fixture(scope="module")
def runtime_flag(docker_available):
    out = _docker("info", "--format", "{{json .Runtimes}}", check=False, timeout=60).stdout
    try:
        runtimes = json.loads(out or "{}")
    except ValueError:
        runtimes = {}
    return ["--runtime=runsc"] if "runsc" in runtimes else []


@pytest.fixture(scope="module")
def image(docker_available):
    return lab_image.ensure_lab_image()


@pytest.fixture
def machine(image, runtime_flag, request):
    """A fresh lab container under the production flags (see app.runtime.container_spec)."""
    name = f"lf-exam-{uuid.uuid4().hex[:10]}"
    request.addfinalizer(
        lambda: subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True, timeout=120)
    )
    _docker(
        "run", "-d", "--name", name, *runtime_flag, "--log-driver", "none", "--network", "none",
        "--memory", "256m", "--pids-limit", "256",
        "--cap-drop", "ALL", "--cap-add", "SETUID", "--cap-add", "SETGID",
        "--security-opt", "no-new-privileges",
        "--tmpfs", "/var/log/lf:size=16m,mode=0700",
        "--tmpfs", "/home/student:size=128m,mode=0755,uid=1001,gid=1001,exec",
        "--tmpfs", "/tmp:size=64m,mode=1777,exec",
        "--tmpfs", "/var/tmp:size=32m,mode=1777",
        "--tmpfs", "/run/lock:size=1m,mode=1777",
        "--tmpfs", "/var/mail:size=1m,mode=0755",
        "-e", "LF_BASE_PATH=/s/exam/term/",
        image,
    )
    deadline = time.time() + 40
    while time.time() < deadline:  # the entrypoint fills the empty home from /etc/skel
        r = _docker("exec", name, "test", "-f", "/home/student/.bashrc", check=False)
        if r.returncode == 0:
            break
        time.sleep(0.5)
    else:
        raise AssertionError("the lab container never became ready")
    return name


def _interpreter(script: Path) -> str:
    first = _text(script).split("\n", 1)[0]
    parts = [p for p in first[2:].split() if not p.endswith("/env")]
    return parts[-1]


def _as_root(machine: str, script: Path, timeout: int = 60):
    return _docker(
        "exec", "-i", machine, _interpreter(script), "-s",
        check=False, timeout=timeout, stdin=_text(script),
    )


def _as_student(machine: str, script: Path):
    return _docker(
        "exec", "-i", machine, "su", "-", "student", "-c", f"{_interpreter(script)} -s",
        check=False, timeout=60, stdin=_text(script),
    )


def _check(machine: str, task_id: str):
    from app.content import parse_check_output

    start = time.time()
    res = _as_root(machine, _task_dir(task_id) / "check.sh", timeout=60)
    assert time.time() - start < 20, "check.sh took longer than the 20 s limit"
    return res, parse_check_output(res.stdout, res.returncode, _n_items(task_id))


def _n_items(task_id: str) -> int:
    return len(next(t for t in _lab().tasks if t.id == task_id).checklist)


def _setup(machine: str, task_id: str):
    res = _as_root(machine, _task_dir(task_id) / "setup.sh")
    assert res.returncode == 0, f"setup.sh of {task_id} failed ({res.returncode}): {res.stderr}"
    return res


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


# ---------------------------------------------------------------- docker tests


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
    sol = _as_student(machine, _task_dir(task_id) / "solution.sh")
    assert sol.returncode == 0, f"solution.sh of {task_id} failed: {sol.stdout}\n{sol.stderr}"
    res, parsed = _check(machine, task_id)
    n = _n_items(task_id)
    assert set(parsed.items) == set(range(1, n + 1)), res.stdout
    assert all(it.passed for it in parsed.items.values()), res.stdout
    assert "FAIL" not in res.stdout
    assert res.returncode == 0 and parsed.passed, res.stdout


@pytest.mark.docker
@pytest.mark.parametrize("task_id", HANDS_ON_IDS)
def test_setup_run_again_after_the_solution_restores_the_starting_state(machine, task_id):
    _setup(machine, task_id)
    sol = _as_student(machine, _task_dir(task_id) / "solution.sh")
    assert sol.returncode == 0, sol.stdout + sol.stderr
    _setup(machine, task_id)  # Restart task / Next: setup on the student's current machine
    _assert_failing_with_hints(machine, task_id, "after setup re-ran over a solved task")


@pytest.mark.docker
def test_chgrp_task_gives_the_student_a_supplementary_group_without_sudo(machine):
    task_id = "09-chgrp"
    _setup(machine, task_id)
    out = _docker("exec", machine, "su", "-", "student", "-c", "id -gn; id -Gn", check=False)
    assert out.returncode == 0, out.stderr
    primary, groups = out.stdout.strip().splitlines()[:2]
    others = [g for g in groups.split() if g != primary]
    assert others, f"student has only its primary group ({groups}) in a fresh login shell"
    assert _raw().get("sudo") is False
    sol = _text(_task_dir(task_id) / "solution.sh")
    code = "\n".join(ln for ln in sol.splitlines() if not ln.lstrip().startswith("#"))
    assert "chgrp" in code and "sudo" not in code, "the solution is a plain chgrp, no sudo"
    assert "chown" not in code, "chown needs root; it is a quiz only (D42)"
