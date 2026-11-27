"""Acceptance tests for issue #676: lab content "Linux 2" (en + he), lesson 6 (D191), a sudo lab.

The lab does not exist yet; these tests describe it. Choices pinned here (the builder follows them):

Location and metadata
    labs/content/linux/linux-2/lab.yaml   course dir `linux` (next to linux-1), lab id `linux-2`
    (the validator requires id == directory name). module: 1, lesson: 6 (D191 v2 numbering),
    image: term, sudo: true (the lab runs on the setuid-honouring runtime, D199, with the
    student's sudo rule from the entrypoint), status: published (D191), no internet.

Tasks (ids and order are fixed; kind in brackets)
    01-create-user    [hands-on]  user `dev1`, home folder /home/dev1, shell /bin/bash
    02-group-ops      [hands-on]  group `ops`, `dev1` is a member as a SECONDARY group. The
                                  deliberate trap (D191): `usermod -G ops dev1` without `-a`
                                  replaces the groups. Setup makes `dev1` (if absent) a member
                                  of at least one other secondary group; the check fails when
                                  that membership is lost. The task text does not say `-a`
                                  or "append"; solution.en.md explains `-a`.
    03-service-user   [hands-on]  user `svc-app`, shell /usr/sbin/nologin, no home folder
    04-lock-olduser   [hands-on]  setup makes `olduser` with a usable password; the task locks the
                                  account (it must still exist)
    05-sleep-pid      [hands-on]  a background `sleep 600` is running and ~/sleep.pid holds its PID
    06-runaway-worker [hands-on]  setup starts a process `runaway-worker` and a decoy
                                  `runaway-worker-keep`; stop the first, leave the decoy running
    07-sudoers-dev1   [hands-on]  /etc/sudoers.d/dev1: dev1 may run only
                                  `/usr/bin/systemctl status *`; passes `visudo -cf`; mode 0440
    08-cron-backup    [hands-on]  crontab of `student` runs ~/backup.sh every 5 minutes; setup
                                  leaves ~/backup.sh there without the x bit; make it executable
    09-quiz-kill      [quiz]      what `kill -9` does differently from `kill`
    10-quiz-cron      [quiz]      the five crontab time fields

Files per hands-on task directory (tasks/<id>/): task.en.md, task.he.md, solution.en.md,
    solution.he.md (the validator's files), setup.sh, check.sh, and the exam-only `solution.sh`
    (the commands that solve the task, run as `student` through a login shell; it uses `sudo`
    where root is needed). The validator and loader ignore it. All .sh files: LF endings, a
    `#!/bin/sh` or `#!/bin/bash` (or `/usr/bin/env`) shebang, mode 0755 in git. The task texts
    show the exact names and values in code spans (see TEXT_PINS).

check.sh protocol (spec section 7, asserted exactly): runs as root, finishes in under 20 s, prints
    `PASS <n>` or `FAIL <n> <hint>` per checklist item (N >= 2), exit 0 only when all pass, every
    FAIL line carries a non-empty hint. setup.sh: run as root, exit 0, self-contained, idempotent,
    and replaces the state its task is about (after the solution a second run brings the task back
    to failing). The checks look at state (accounts, groups, files, processes, the crontab), never
    at the commands typed (a bash history that holds the commands but no state gets FAIL), and
    equally correct alternative commands get PASS.

Docker tests (marker `docker`, run in CI) start the lab through `app.runtime.container_spec` and
    `DockerRuntime.start`, exactly as production starts a sudo lab (runtime `runsc-suid`, the
    spec's capabilities and tmpfs mounts, LF_SUDO=1), so `sudo` works for `student` as in the
    real lab. They are parametrised over the fixed task ids, so a missing lab fails them; they
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
LAB_DIR = CONTENT_ROOT / "linux" / "linux-2"
LAB_ID = "linux-2"
LAB_KEY = "linux/linux-2"

TASKS = [
    ("01-create-user", "hands-on"),
    ("02-group-ops", "hands-on"),
    ("03-service-user", "hands-on"),
    ("04-lock-olduser", "hands-on"),
    ("05-sleep-pid", "hands-on"),
    ("06-runaway-worker", "hands-on"),
    ("07-sudoers-dev1", "hands-on"),
    ("08-cron-backup", "hands-on"),
    ("09-quiz-kill", "quiz"),
    ("10-quiz-cron", "quiz"),
]
QUIZ_IDS = [t for t, k in TASKS if k == "quiz"]
HANDS_ON_IDS = [t for t, k in TASKS if k == "hands-on"]
# exact names and values that the en and he task texts show in code spans
TEXT_PINS = {
    "01-create-user": ["dev1", "/bin/bash"],
    "02-group-ops": ["ops", "dev1"],
    "03-service-user": ["svc-app", "/usr/sbin/nologin"],
    "04-lock-olduser": ["olduser"],
    "05-sleep-pid": ["sleep 600", "sleep.pid"],
    "06-runaway-worker": ["runaway-worker", "runaway-worker-keep"],
    "07-sudoers-dev1": ["/etc/sudoers.d/dev1", "/usr/bin/systemctl status *", "0440"],
    "08-cron-backup": ["backup.sh", "student"],
}
HEBREW = re.compile("[֐-׿]")


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
    assert lab.path.parent.name == "linux"


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
    task = next(t for t in _lab().tasks if t.id == task_id)
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
    task = next(t for t in _lab().tasks if t.id == task_id)
    raw = _raw_task(task_id)
    assert isinstance(task.correct, int) and 0 <= task.correct < len(task.options)
    assert raw.get("correct") == task.correct and isinstance(raw["correct"], int)
    en = [o["en"].strip() for o in task.options]
    assert len(set(en)) == len(en), "options must be distinct"


def test_kill_quiz_right_answer_says_sigkill_cannot_be_caught():
    task = next(t for t in _lab().tasks if t.id == "09-quiz-kill")
    q = task.question["en"]
    assert "kill -9" in q or all("kill -9" in o["en"] for o in task.options)
    right = task.options[task.correct]["en"].lower()
    assert "sigkill" in right or "signal 9" in right or " 9" in right
    assert re.search(r"(cannot|can't|can not|may not)\s+be\s+(caught|ignored|blocked|handled)", right), (
        f"the right option must say the process cannot catch or ignore the signal: {right!r}"
    )
    for i, o in enumerate(task.options):
        if i != task.correct:
            assert not re.search(
                r"(cannot|can't|can not)\s+be\s+(caught|ignored|blocked)", o["en"].lower()
            ), f"a wrong option must not hold the right claim: {o['en']!r}"


def test_cron_quiz_right_answer_lists_the_five_fields_in_order():
    task = next(t for t in _lab().tasks if t.id == "10-quiz-cron")
    right = re.sub(r"[-_]", " ", task.options[task.correct]["en"].lower())
    assert re.search(r"minute.*hour.*day.*month.*(day.*week|weekday)", right), (
        f"the right option must list minute, hour, day of month, month, day of week in order: {right!r}"
    )
    for i, o in enumerate(task.options):
        if i != task.correct:
            wrong = re.sub(r"[-_]", " ", o["en"].lower())
            assert not re.search(
                r"minute.*hour.*day.*month.*(day.*week|weekday)", wrong
            ), f"a wrong option lists the fields in the right order: {o['en']!r}"


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


def test_the_trap_is_not_given_away_in_the_task_text_but_explained_in_the_solution():
    d = _task_dir("02-group-ops")
    task_text = _text(d / "task.en.md")
    assert "append" not in task_text.lower(), "task 02 must not hint at appending"
    assert not re.search(r"usermod\s+-\w*a", task_text), "task 02 must not show `usermod -a`"
    sol = _text(d / "solution.en.md")
    assert re.search(r"-\w*a\b", sol) and "usermod" in sol, "solution 02 must explain usermod -a"


def test_check_scripts_look_at_state_not_at_the_shell_history():
    for task_id in HANDS_ON_IDS:
        code = _text(_task_dir(task_id) / "check.sh")
        assert "bash_history" not in code and not re.search(r"\bhistory\b", code), (
            f"check.sh of {task_id} reads the shell history"
        )


def test_task_setups_never_use_the_student_sudo_rule_and_hold_no_secrets():
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
    """Run shell code as root (a probe of the state; it changes nothing the checks look at)."""
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


def _groups(machine: str, user: str) -> set[str]:
    out = _root(machine, f"id -nG {user}").stdout.split()
    return set(out)


def _runaway(machine: str) -> dict[str, int]:
    """Count the processes named runaway-worker and runaway-worker-keep (by argv, not by comm)."""
    code = r"""
for d in /proc/[0-9]*; do
  a=$(tr '\0' '\n' < "$d/cmdline" 2>/dev/null | head -n 2)
  for w in $a; do
    case "${w##*/}" in
      runaway-worker) echo main ;;
      runaway-worker-keep) echo keep ;;
    esac
  done
done | sort | uniq -c
"""
    out = _root(machine, code).stdout.split()
    counts = {"main": 0, "keep": 0}
    for n, name in zip(out[0::2], out[1::2], strict=False):
        counts[name] = int(n)
    return counts


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
    # setting up a later task must not undo an earlier one that the student solved
    _assert_passing(machine, "01-create-user", "task 01 after the whole lab")


@pytest.mark.docker
@pytest.mark.parametrize("task_id", HANDS_ON_IDS)
def test_the_check_ignores_the_commands_in_the_shell_history(machine, task_id):
    _setup(machine, task_id)
    solution = _text(_task_dir(task_id) / "solution.sh")
    r = _student(
        machine,
        "cat > ~/.bash_history <<'EOS_676'\n" + solution + "\nEOS_676\n"
        "echo 'useradd dev1; usermod -aG ops dev1; crontab -l' >> ~/.bash_history\n",
    )
    assert r.returncode == 0, r.stderr
    _assert_failing_with_hints(machine, task_id, "with the commands only in the shell history")


# ---------------------------------------------------------------- docker: 01 create user


@pytest.mark.docker
def test_01_user_without_a_home_or_with_the_default_shell_fails(machine):
    res, _ = _near_miss(machine, "01-create-user", "sudo useradd dev1", "useradd without -m and -s")
    assert "FAIL" in res.stdout
    _near_miss(machine, "01-create-user", "sudo useradd -m dev1", "a user with the default shell")


@pytest.mark.docker
def test_01_equally_correct_alternative_commands_pass(machine):
    _alternative(
        machine, "01-create-user",
        "sudo useradd -m dev1 && sudo chsh -s /bin/bash dev1",
        "useradd -m then chsh",
    )


@pytest.mark.docker
def test_01_the_user_has_a_real_home_folder_owned_by_it(machine):
    _setup(machine, "01-create-user")
    _solve(machine, "01-create-user")
    out = _root(machine, "stat -c '%U' /home/dev1; getent passwd dev1").stdout
    assert out.splitlines()[0] == "dev1"
    assert out.splitlines()[1].endswith(":/home/dev1:/bin/bash")


# ---------------------------------------------------------------- docker: 02 group ops (the trap)


@pytest.mark.docker
def test_02_setup_gives_dev1_a_secondary_group_that_the_task_must_keep(machine):
    _setup(machine, "02-group-ops")
    groups = _groups(machine, "dev1")
    assert len(groups - {"dev1"}) >= 1, f"dev1 needs a pre-existing secondary group: {groups}"
    assert "ops" not in groups


@pytest.mark.docker
def test_02_usermod_G_without_a_loses_the_old_group_and_fails_with_a_hint(machine):
    _setup(machine, "02-group-ops")
    before = _groups(machine, "dev1") - {"dev1"}
    assert before
    _try(machine, "sudo groupadd ops\nsudo usermod -G ops dev1")
    groups = _groups(machine, "dev1")
    assert "ops" in groups and not (before & groups), "the trap did not drop the old group"
    res, parsed = _assert_failing_with_hints(machine, "02-group-ops", "for usermod -G without -a")
    hints = " ".join(it.hint or "" for it in parsed.items.values()).lower()
    assert "-a" in hints or "append" in hints, (
        f"a hint must point to appending (usermod -a) after the trap:\n{res.stdout}"
    )


@pytest.mark.docker
def test_02_the_solution_adds_ops_and_keeps_the_old_group(machine):
    _setup(machine, "02-group-ops")
    before = _groups(machine, "dev1")
    _solve(machine, "02-group-ops")
    after = _groups(machine, "dev1")
    assert "ops" in after
    assert before <= after, f"the solution dropped groups: {before} -> {after}"
    _assert_passing(machine, "02-group-ops", "the solution")


@pytest.mark.docker
def test_02_the_group_alone_or_a_second_user_in_it_is_not_enough(machine):
    _near_miss(machine, "02-group-ops", "sudo groupadd ops", "a group nobody is in")
    _near_miss(
        machine, "02-group-ops",
        "sudo groupadd -f ops\nsudo useradd -m other1 || true\nsudo usermod -aG ops other1",
        "another user in ops",
    )


@pytest.mark.docker
def test_02_alternative_gpasswd_passes(machine):
    _alternative(
        machine, "02-group-ops", "sudo groupadd ops\nsudo gpasswd -a dev1 ops", "gpasswd -a"
    )


@pytest.mark.docker
def test_02_ops_as_the_primary_group_of_dev1_is_not_the_task(machine):
    _near_miss(
        machine, "02-group-ops", "sudo groupadd ops\nsudo usermod -g ops dev1",
        "ops as the primary group (usermod -g)",
    )


# ---------------------------------------------------------------- docker: 03 service user


@pytest.mark.docker
def test_03_a_service_user_with_a_home_or_a_login_shell_fails(machine):
    _near_miss(
        machine, "03-service-user", "sudo useradd -m -s /usr/sbin/nologin svc-app",
        "a service user with a home folder",
    )
    _near_miss(machine, "03-service-user", "sudo useradd -M svc-app", "a service user with a shell")
    _near_miss(
        machine, "03-service-user", "sudo useradd -M -s /bin/bash svc-app",
        "a service user with a login shell",
    )


@pytest.mark.docker
def test_03_the_service_user_keeps_no_home_folder(machine):
    _setup(machine, "03-service-user")
    _solve(machine, "03-service-user")
    out = _root(machine, "test -e /home/svc-app && echo home; getent passwd svc-app").stdout
    assert "home" not in out.split()
    assert out.strip().splitlines()[-1].endswith("/usr/sbin/nologin")


@pytest.mark.docker
def test_03_alternative_system_account_flags_pass(machine):
    _alternative(
        machine, "03-service-user", "sudo useradd -r -s /usr/sbin/nologin svc-app",
        "useradd -r (system account, no home)",
    )


@pytest.mark.docker
def test_03_a_home_folder_left_behind_by_an_earlier_user_fails(machine):
    _setup(machine, "03-service-user")
    _try(machine, "sudo useradd -M -s /usr/sbin/nologin svc-app\nsudo mkdir -p /home/svc-app")
    _assert_failing_with_hints(machine, "03-service-user", "with a /home/svc-app folder")


# ---------------------------------------------------------------- docker: 04 lock olduser


@pytest.mark.docker
def test_04_setup_creates_an_unlocked_olduser(machine):
    _setup(machine, "04-lock-olduser")
    status = _root(machine, "passwd -S olduser").stdout.split()
    assert status[:2] == ["olduser", "P"], f"olduser starts with a usable password: {status}"


@pytest.mark.docker
def test_04_deleting_the_account_or_changing_the_shell_is_not_locking_it(machine):
    _near_miss(machine, "04-lock-olduser", "sudo userdel olduser", "deleting olduser")
    _near_miss(
        machine, "04-lock-olduser", "sudo usermod -s /usr/sbin/nologin olduser",
        "only changing the shell",
    )


@pytest.mark.docker
def test_04_alternative_passwd_l_passes(machine):
    _alternative(machine, "04-lock-olduser", "sudo passwd -l olduser", "passwd -l")


# ---------------------------------------------------------------- docker: 05 sleep.pid


@pytest.mark.docker
def test_05_a_pid_file_that_does_not_name_a_running_sleep_600_fails(machine):
    _near_miss(
        machine, "05-sleep-pid",
        "nohup sleep 600 >/dev/null 2>&1 &\necho $$ > ~/sleep.pid",
        "the shell's own PID instead of the sleep",
    )
    _near_miss(
        machine, "05-sleep-pid",
        "nohup sleep 700 >/dev/null 2>&1 &\necho $! > ~/sleep.pid",
        "a sleep with another duration",
    )
    _near_miss(
        machine, "05-sleep-pid",
        "nohup sleep 600 >/dev/null 2>&1 &\necho $! > ~/sleep.pid\nkill $!\nsleep 1",
        "a PID of a process that is gone",
    )
    _near_miss(
        machine, "05-sleep-pid",
        "nohup sleep 600 >/dev/null 2>&1 &",
        "a running sleep without the pid file",
    )


@pytest.mark.docker
def test_05_the_sleep_keeps_running_after_the_solution_ends(machine):
    _setup(machine, "05-sleep-pid")
    _solve(machine, "05-sleep-pid")
    pid = _root(machine, "cat /home/student/sleep.pid").stdout.strip()
    assert pid.isdigit()
    args = _root(machine, f"tr '\\0' ' ' < /proc/{pid}/cmdline").stdout.strip()
    assert args.endswith("sleep 600"), f"PID {pid} runs {args!r}"


@pytest.mark.docker
def test_05_alternative_ways_to_write_the_pid_pass(machine):
    _alternative(
        machine, "05-sleep-pid",
        "nohup sleep 600 >/dev/null 2>&1 &\nprintf '%s' \"$!\" > ~/sleep.pid",
        "a PID file without a newline",
    )


# ---------------------------------------------------------------- docker: 06 runaway worker


@pytest.mark.docker
def test_06_setup_starts_the_worker_and_the_decoy(machine):
    _setup(machine, "06-runaway-worker")
    counts = _runaway(machine)
    assert counts["main"] >= 1 and counts["keep"] >= 1, counts


@pytest.mark.docker
def test_06_setup_run_twice_does_not_pile_up_processes(machine):
    _setup(machine, "06-runaway-worker")
    _setup(machine, "06-runaway-worker")
    counts = _runaway(machine)
    assert 1 <= counts["main"] <= 2 and 1 <= counts["keep"] <= 2, counts


@pytest.mark.docker
def test_06_killing_both_by_a_pattern_fails_because_the_decoy_must_live(machine):
    _near_miss(
        machine, "06-runaway-worker", "sudo pkill -f runaway-worker || true\nsleep 1",
        "killing the decoy too (pkill -f)",
    )
    assert _runaway(machine) == {"main": 0, "keep": 0}


@pytest.mark.docker
def test_06_the_solution_stops_the_worker_and_leaves_the_decoy(machine):
    _setup(machine, "06-runaway-worker")
    _solve(machine, "06-runaway-worker")
    counts = _runaway(machine)
    assert counts["main"] == 0 and counts["keep"] >= 1, counts
    _assert_passing(machine, "06-runaway-worker", "the solution")


@pytest.mark.docker
def test_06_alternative_exact_name_kill_with_sudo_passes(machine):
    _alternative(
        machine, "06-runaway-worker", "sudo pkill -x runaway-worker\nsleep 1",
        "pkill -x with sudo",
    )


# ---------------------------------------------------------------- docker: 07 sudoers


def _sudoers_attempt(content: str, mode: str | None = "0440") -> str:
    code = f"sudo tee /etc/sudoers.d/dev1 >/dev/null <<'EOS_676'\n{content}\nEOS_676\n"
    if mode:
        code += f"sudo chmod {mode} /etc/sudoers.d/dev1\n"
    return code


@pytest.mark.docker
def test_07_the_file_is_checked_with_visudo_and_mode_0440(machine):
    _setup(machine, "07-sudoers-dev1")
    _solve(machine, "07-sudoers-dev1")
    out = _root(
        machine, "visudo -cf /etc/sudoers.d/dev1 >/dev/null && echo valid; stat -c %a /etc/sudoers.d/dev1"
    ).stdout.split()
    assert out == ["valid", "440"], out
    _assert_passing(machine, "07-sudoers-dev1", "the solution")


@pytest.mark.docker
def test_07_a_wrong_mode_fails_with_a_hint(machine):
    _near_miss(
        machine, "07-sudoers-dev1",
        _sudoers_attempt("dev1 ALL=(root) /usr/bin/systemctl status *", mode="0644"),
        "mode 0644",
    )


@pytest.mark.docker
def test_07_too_wide_a_rule_fails(machine):
    _near_miss(
        machine, "07-sudoers-dev1", _sudoers_attempt("dev1 ALL=(ALL) NOPASSWD: ALL"),
        "ALL commands",
    )
    _near_miss(
        machine, "07-sudoers-dev1", _sudoers_attempt("dev1 ALL=(ALL) /usr/bin/systemctl *"),
        "every systemctl subcommand",
    )
    _near_miss(
        machine, "07-sudoers-dev1",
        _sudoers_attempt(
            "dev1 ALL=(root) /usr/bin/systemctl status *, /usr/bin/systemctl restart *"
        ),
        "a rule that also allows restart",
    )


@pytest.mark.docker
def test_07_a_file_that_visudo_refuses_fails(machine):
    _near_miss(
        machine, "07-sudoers-dev1",
        _sudoers_attempt("dev1 ALL=(root) /usr/bin/systemctl status *\nthis line is not valid"),
        "a syntax error",
    )


@pytest.mark.docker
def test_07_a_rule_for_another_user_or_file_name_fails(machine):
    _near_miss(
        machine, "07-sudoers-dev1",
        "echo 'dev1 ALL=(root) /usr/bin/systemctl status *' | sudo tee /etc/sudoers.d/dev1-rule"
        " >/dev/null\nsudo chmod 0440 /etc/sudoers.d/dev1-rule",
        "a file with another name",
    )


@pytest.mark.docker
def test_07_alternative_equally_narrow_rule_with_nopasswd_passes(machine):
    _alternative(
        machine, "07-sudoers-dev1",
        _sudoers_attempt("dev1 ALL=(ALL) NOPASSWD: /usr/bin/systemctl status *"),
        "an equally narrow rule with NOPASSWD",
    )


# ---------------------------------------------------------------- docker: 08 cron


CRON_OK = "printf '*/5 * * * * ~/backup.sh\\n' | crontab -\nchmod +x ~/backup.sh"


@pytest.mark.docker
def test_08_setup_leaves_a_script_without_the_x_bit_and_no_crontab(machine):
    _setup(machine, "08-cron-backup")
    out = _root(
        machine, "test -f /home/student/backup.sh && echo file; test -x /home/student/backup.sh && echo exec"
    ).stdout.split()
    assert out == ["file"], f"setup must leave ~/backup.sh without the x bit: {out}"
    listing = _student(machine, "crontab -l 2>&1").stdout
    assert "backup.sh" not in listing


@pytest.mark.docker
def test_08_the_crontab_runs_as_student_with_the_every_5_minutes_line(machine):
    _setup(machine, "08-cron-backup")
    _solve(machine, "08-cron-backup")
    listing = _student(machine, "crontab -l").stdout
    lines = [ln for ln in listing.splitlines() if "backup.sh" in ln and not ln.startswith("#")]
    assert len(lines) == 1, listing
    assert lines[0].split()[:5] == ["*/5", "*", "*", "*", "*"] or lines[0].startswith("0-59/5 "), lines
    _assert_passing(machine, "08-cron-backup", "the solution")


@pytest.mark.docker
def test_08_a_script_that_is_not_executable_fails_with_an_executable_hint(machine):
    res, parsed = _near_miss(
        machine, "08-cron-backup", "printf '*/5 * * * * ~/backup.sh\\n' | crontab -",
        "a crontab line with a script that is not executable",
    )
    hints = " ".join(it.hint or "" for it in parsed.items.values()).lower()
    assert "executable" in hints or "chmod" in hints, res.stdout


@pytest.mark.docker
def test_08_an_executable_script_without_a_crontab_line_fails(machine):
    _near_miss(machine, "08-cron-backup", "chmod +x ~/backup.sh", "no crontab line")


@pytest.mark.docker
def test_08_a_line_with_another_schedule_fails(machine):
    for sched, why in (
        ("0 * * * *", "hourly"),
        ("* * * * *", "every minute"),
        ("*/15 * * * *", "every 15 minutes"),
        ("5 * * * *", "minute 5 of every hour"),
    ):
        _near_miss(
            machine, "08-cron-backup",
            f"printf '{sched} ~/backup.sh\\n' | crontab -\nchmod +x ~/backup.sh",
            f"the schedule {why}",
        )


@pytest.mark.docker
def test_08_a_line_for_another_script_fails(machine):
    _near_miss(
        machine, "08-cron-backup",
        "printf '*/5 * * * * ~/other.sh\\n' | crontab -\nchmod +x ~/backup.sh",
        "a line for another script",
    )


@pytest.mark.docker
def test_08_alternative_absolute_path_passes(machine):
    _alternative(
        machine, "08-cron-backup",
        "printf '*/5 * * * * /home/student/backup.sh\\n' | crontab -\nchmod 755 ~/backup.sh",
        "the absolute path of the script",
    )


@pytest.mark.docker
def test_08_alternative_existing_lines_kept_and_added_to_pass(machine):
    _alternative(
        machine, "08-cron-backup",
        "( crontab -l 2>/dev/null; echo '# nightly' ; echo '*/5 * * * * ~/backup.sh' ) | crontab -\n"
        "chmod u+x ~/backup.sh",
        "the line added to an existing crontab",
    )
