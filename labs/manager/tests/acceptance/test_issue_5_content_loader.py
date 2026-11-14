"""Acceptance tests for issue #5: lab content format, loader and validator.

Public interface under test (build against this)
------------------------------------------------
    from app.content import load_labs, parse_check_output

    load_labs(root: pathlib.Path) -> LoadResult
        root is a directory like labs/content, layout <course>/<lab>/lab.yaml.
        LoadResult.labs   : dict[str, Lab]        lab id -> lab (valid labs only)
        LoadResult.errors : dict[str, list[str]]  lab directory, relative to root,
                            POSIX style (e.g. "sample/broken") -> human-readable errors
        A broken lab appears in errors and never in labs. One broken lab never
        prevents the others from loading. A lab id used by two lab directories is
        an error for BOTH directories; neither is served.
        A directory with a lab.yaml is a lab; everything else is ignored.
        Errors are collected per lab (all problems, not just the first).

    Lab: id, title (dict with "en"/"he"), status ("draft"|"published"), image
         ("term"|"code"), module (int), lesson (int), tasks (ordered list).
    Task: id (str), kind ("quiz"|"hands-on").

    parse_check_output(stdout: str, exit_code: int) -> CheckResult
        CheckResult.passed: bool
        CheckResult.items : dict[int, ItemResult]
        ItemResult.passed: bool, ItemResult.hint: str | None
        Lines "PASS <n>" and "FAIL <n> [hint]" (at line start) are items; every
        other line is ignored (blank lines, noise, CRLF endings). The hint is the
        text after "FAIL <n> " stripped, or None when absent. passed is True only
        when the exit code is 0 AND no item failed.

lab.yaml schema (spec section 7 plus the course fields from the issue comment)
------------------------------------------------------------------------------
    id: str                      unique across all labs under root
    title: {en: str, he: str}    both required
    description: {en: str, he: str}
    image: term | code
    time_limit_min: int          default 60
    internet: bool
    allow_domains: [str]
    sudo: bool
    status: draft | published
    module: int 0..6             required
    lesson: int 1..63            required, inside its module's range:
                                 M0 1-4, M1 5-12, M2 13-22, M3 23-38,
                                 M4 39-48, M5 49-56, M6 57-63.
                                 Not unique: several labs may share a lesson.
    tasks:                       ordered list, order is the task order
      - id: 01-intro             unique within the lab
        kind: quiz
        question: {en, he}
        options: [{en, he}, ...]
        correct: <0-based index into options>   exactly one correct option
                 (missing, a list, or out of range is an error)
      - id: 02-make-file         hands-on; directory tasks/<id>/ must exist
        kind: hands-on
        checklist: [{en, he}, ...]
    There is no ai_mode field.

A hands-on task directory tasks/<id>/ must contain task.en.md, task.he.md,
setup.sh, check.sh (both scripts executable), solution.en.md, solution.he.md.
"""
import copy
import os
import stat
import sys
from pathlib import Path

import pytest
import yaml

from app.content import load_labs, parse_check_output

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
SAMPLE_ROOT = REPO_ROOT / "labs" / "content"

BILINGUAL = {"en": "text", "he": "טקסט"}

QUIZ_TASK = {
    "id": "01-quiz",
    "kind": "quiz",
    "question": {"en": "Which?", "he": "איזה?"},
    "options": [{"en": "A", "he": "א"}, {"en": "B", "he": "ב"}],
    "correct": 1,
}
HANDS_ON_TASK = {
    "id": "02-make-file",
    "kind": "hands-on",
    "checklist": [{"en": "File exists", "he": "הקובץ קיים"}],
}


def _lab_yaml(lab_id="good", **over):
    data = {
        "id": lab_id,
        "title": {"en": "Good lab", "he": "מעבדה טובה"},
        "description": dict(BILINGUAL),
        "image": "term",
        "time_limit_min": 30,
        "internet": False,
        "allow_domains": [],
        "sudo": False,
        "status": "draft",
        "module": 0,
        "lesson": 1,
        "tasks": [copy.deepcopy(QUIZ_TASK), copy.deepcopy(HANDS_ON_TASK)],
    }
    data.update(over)
    return data


def make_lab(root: Path, rel: str, data=None, **over) -> Path:
    """Write a valid lab at root/rel; `data` replaces lab.yaml wholesale."""
    lab_dir = root / rel
    data = data if data is not None else _lab_yaml(Path(rel).name, **over)
    lab_dir.mkdir(parents=True)
    (lab_dir / "lab.yaml").write_text(
        yaml.safe_dump(data, allow_unicode=True), encoding="utf-8"
    )
    for task in data.get("tasks", []):
        if task.get("kind") != "hands-on":
            continue
        tdir = lab_dir / "tasks" / task["id"]
        tdir.mkdir(parents=True, exist_ok=True)
        for name in ("task.en.md", "task.he.md", "solution.en.md", "solution.he.md"):
            (tdir / name).write_text("# text\n", encoding="utf-8")
        for name in ("setup.sh", "check.sh"):
            script = tdir / name
            script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            script.chmod(0o755)
    return lab_dir


def errors_text(result, rel: str) -> str:
    assert rel in result.errors, f"{rel} not reported; errors={dict(result.errors)}"
    errs = result.errors[rel]
    assert errs and all(isinstance(e, str) and e for e in errs)
    return "\n".join(errs).lower()


def assert_broken(result, rel: str, lab_id: str) -> str:
    assert lab_id not in result.labs, "a broken lab must not be served"
    return errors_text(result, rel)


@pytest.fixture
def root(tmp_path):
    return tmp_path / "content"


# ---- the real sample lab -------------------------------------------------


def test_sample_lab_loads_without_errors():
    result = load_labs(SAMPLE_ROOT)
    assert "sample/hello" not in result.errors, result.errors.get("sample/hello")
    assert "hello" in result.labs


def test_sample_lab_has_one_quiz_and_one_hands_on_task():
    lab = load_labs(SAMPLE_ROOT).labs["hello"]
    assert len(lab.tasks) == 2
    assert sorted(t.kind for t in lab.tasks) == ["hands-on", "quiz"]
    assert len({t.id for t in lab.tasks}) == 2


def test_sample_lab_has_both_languages_and_valid_metadata():
    lab = load_labs(SAMPLE_ROOT).labs["hello"]
    assert lab.title["en"] and lab.title["he"]
    assert lab.status in ("draft", "published")
    assert lab.image in ("term", "code")


def test_sample_lab_hands_on_task_has_all_files_and_executable_scripts():
    lab = load_labs(SAMPLE_ROOT).labs["hello"]
    task = next(t for t in lab.tasks if t.kind == "hands-on")
    tdir = SAMPLE_ROOT / "sample" / "hello" / "tasks" / task.id
    for name in ("task.en.md", "task.he.md", "setup.sh", "check.sh",
                 "solution.en.md", "solution.he.md"):
        assert (tdir / name).is_file(), f"{tdir / name} is missing"
    if sys.platform != "win32":
        for name in ("setup.sh", "check.sh"):
            assert os.access(tdir / name, os.X_OK)


def test_sample_lab_yaml_has_course_fields_and_no_ai_mode():
    raw = yaml.safe_load((SAMPLE_ROOT / "sample" / "hello" / "lab.yaml").read_text("utf-8"))
    assert "ai_mode" not in raw
    assert isinstance(raw["module"], int) and isinstance(raw["lesson"], int)


# ---- loading from a fixture tree -----------------------------------------


def test_valid_lab_loads_with_ordered_tasks(root):
    make_lab(root, "course/good")
    result = load_labs(root)
    assert result.errors == {}
    lab = result.labs["good"]
    assert lab.title == {"en": "Good lab", "he": "מעבדה טובה"}
    assert lab.status == "draft"
    assert lab.image == "term"
    assert [(t.id, t.kind) for t in lab.tasks] == [
        ("01-quiz", "quiz"),
        ("02-make-file", "hands-on"),
    ]


def test_labs_in_several_courses_load_together(root):
    make_lab(root, "c1/one")
    make_lab(root, "c2/two", lesson=2)
    assert set(load_labs(root).labs) == {"one", "two"}


def test_empty_root_loads_nothing(root):
    root.mkdir()
    result = load_labs(root)
    assert result.labs == {} and result.errors == {}


def test_lesson_is_not_unique_across_labs(root):
    make_lab(root, "c/a", lesson=5, module=1)
    make_lab(root, "c/b", lesson=5, module=1)
    result = load_labs(root)
    assert result.errors == {}
    assert set(result.labs) == {"a", "b"}


# ---- validation: languages ----------------------------------------------


def test_missing_he_title_is_reported_and_lab_not_served(root):
    make_lab(root, "c/bad", title={"en": "Only English"})
    text = assert_broken(load_labs(root), "c/bad", "bad")
    assert "title" in text and "he" in text


def test_missing_he_task_file_is_reported_and_lab_not_served(root):
    lab_dir = make_lab(root, "c/bad")
    (lab_dir / "tasks" / "02-make-file" / "task.he.md").unlink()
    text = assert_broken(load_labs(root), "c/bad", "bad")
    assert "task.he.md" in text


def test_missing_en_task_file_is_reported(root):
    lab_dir = make_lab(root, "c/bad")
    (lab_dir / "tasks" / "02-make-file" / "task.en.md").unlink()
    text = assert_broken(load_labs(root), "c/bad", "bad")
    assert "task.en.md" in text


def test_missing_he_quiz_question_is_reported(root):
    quiz = copy.deepcopy(QUIZ_TASK)
    quiz["question"] = {"en": "Which?"}
    make_lab(root, "c/bad", tasks=[quiz, copy.deepcopy(HANDS_ON_TASK)])
    assert_broken(load_labs(root), "c/bad", "bad")


# ---- validation: scripts -------------------------------------------------


def test_missing_setup_sh_is_reported(root):
    lab_dir = make_lab(root, "c/bad")
    (lab_dir / "tasks" / "02-make-file" / "setup.sh").unlink()
    text = assert_broken(load_labs(root), "c/bad", "bad")
    assert "setup.sh" in text


def test_missing_check_sh_is_reported(root):
    lab_dir = make_lab(root, "c/bad")
    (lab_dir / "tasks" / "02-make-file" / "check.sh").unlink()
    text = assert_broken(load_labs(root), "c/bad", "bad")
    assert "check.sh" in text


@pytest.mark.skipif(sys.platform == "win32", reason="cannot clear the x bit on Windows")
@pytest.mark.parametrize("script", ["setup.sh", "check.sh"])
def test_non_executable_script_is_reported(root, script):
    lab_dir = make_lab(root, "c/bad")
    path = lab_dir / "tasks" / "02-make-file" / script
    path.chmod(path.stat().st_mode & ~(stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    text = assert_broken(load_labs(root), "c/bad", "bad")
    assert script in text and "executable" in text


# ---- validation: quiz ----------------------------------------------------


def _quiz_lab(root, quiz):
    make_lab(root, "c/bad", tasks=[quiz, copy.deepcopy(HANDS_ON_TASK)])
    return load_labs(root)


def test_quiz_with_zero_correct_options_is_reported(root):
    quiz = copy.deepcopy(QUIZ_TASK)
    del quiz["correct"]
    assert_broken(_quiz_lab(root, quiz), "c/bad", "bad")


def test_quiz_with_two_correct_options_is_reported(root):
    quiz = copy.deepcopy(QUIZ_TASK)
    quiz["correct"] = [0, 1]
    assert_broken(_quiz_lab(root, quiz), "c/bad", "bad")


def test_quiz_correct_index_out_of_range_is_reported(root):
    quiz = copy.deepcopy(QUIZ_TASK)
    quiz["correct"] = 7
    assert_broken(_quiz_lab(root, quiz), "c/bad", "bad")


def test_quiz_with_exactly_one_correct_option_is_valid(root):
    quiz = copy.deepcopy(QUIZ_TASK)
    quiz["correct"] = 0
    result = _quiz_lab(root, quiz)
    assert "c/bad" not in result.errors
    assert "bad" in result.labs


# ---- validation: status, ids, course fields ------------------------------


def test_invalid_status_is_reported(root):
    make_lab(root, "c/bad", status="live")
    text = assert_broken(load_labs(root), "c/bad", "bad")
    assert "status" in text


@pytest.mark.parametrize("status", ["draft", "published"])
def test_valid_statuses_are_accepted(root, status):
    make_lab(root, "c/ok", status=status)
    assert load_labs(root).labs["ok"].status == status


def test_duplicate_lab_ids_are_reported_for_both_directories(root):
    make_lab(root, "c1/first", data=_lab_yaml("same"))
    make_lab(root, "c2/second", data=_lab_yaml("same"))
    result = load_labs(root)
    assert "same" not in result.labs
    assert "duplicate" in errors_text(result, "c1/first")
    assert "duplicate" in errors_text(result, "c2/second")


def test_duplicate_task_ids_are_reported(root):
    t1 = copy.deepcopy(HANDS_ON_TASK)
    t2 = copy.deepcopy(HANDS_ON_TASK)
    make_lab(root, "c/bad", tasks=[t1, t2])
    text = assert_broken(load_labs(root), "c/bad", "bad")
    assert "duplicate" in text


@pytest.mark.parametrize(
    "module,lesson",
    [(0, 4), (1, 5), (1, 12), (2, 13), (2, 22), (3, 23), (3, 38),
     (4, 39), (4, 48), (5, 49), (5, 56), (6, 57), (6, 63)],
)
def test_lesson_inside_module_range_is_valid(root, module, lesson):
    make_lab(root, "c/ok", module=module, lesson=lesson)
    result = load_labs(root)
    assert "c/ok" not in result.errors
    assert "ok" in result.labs


@pytest.mark.parametrize(
    "module,lesson",
    [(0, 5), (1, 4), (1, 13), (2, 12), (2, 23), (3, 39), (4, 49),
     (5, 57), (6, 56), (6, 64), (0, 0), (7, 1), (-1, 1)],
)
def test_lesson_outside_module_range_is_reported(root, module, lesson):
    make_lab(root, "c/bad", module=module, lesson=lesson)
    text = assert_broken(load_labs(root), "c/bad", "bad")
    assert "lesson" in text or "module" in text


@pytest.mark.parametrize("field", ["module", "lesson"])
def test_missing_module_or_lesson_is_reported(root, field):
    data = _lab_yaml("bad")
    del data[field]
    make_lab(root, "c/bad", data=data)
    text = assert_broken(load_labs(root), "c/bad", "bad")
    assert field in text


def test_malformed_yaml_is_reported_not_raised(root):
    lab_dir = make_lab(root, "c/bad")
    (lab_dir / "lab.yaml").write_text("id: [unclosed\n  : :\n", encoding="utf-8")
    result = load_labs(root)
    assert result.errors.get("c/bad")


def test_errors_are_collected_per_lab_not_just_the_first(root):
    lab_dir = make_lab(root, "c/bad", status="live", title={"en": "x"})
    (lab_dir / "tasks" / "02-make-file" / "check.sh").unlink()
    result = load_labs(root)
    text = assert_broken(result, "c/bad", "bad")
    assert "status" in text and "title" in text and "check.sh" in text
    assert len(result.errors["c/bad"]) >= 3


# ---- broken lab reported, good lab still served --------------------------


def test_broken_lab_is_reported_while_good_lab_is_still_served(root):
    make_lab(root, "sample/good")
    bad = make_lab(root, "sample/broken")
    (bad / "tasks" / "02-make-file" / "check.sh").unlink()
    result = load_labs(root)
    assert "good" in result.labs
    assert "sample/good" not in result.errors
    assert "broken" not in result.labs
    assert "check.sh" in errors_text(result, "sample/broken")


# ---- check.sh output protocol --------------------------------------------


def test_all_pass_with_exit_zero_passes():
    r = parse_check_output("PASS 1\nPASS 2\n", 0)
    assert r.passed is True
    assert set(r.items) == {1, 2}
    assert all(i.passed and i.hint is None for i in r.items.values())


def test_fail_line_with_hint_is_parsed():
    r = parse_check_output("PASS 1\nFAIL 2 file /tmp/x is missing\n", 1)
    assert r.passed is False
    assert r.items[1].passed is True
    assert r.items[2].passed is False
    assert r.items[2].hint == "file /tmp/x is missing"


def test_fail_line_without_hint_has_none_hint():
    r = parse_check_output("FAIL 3\n", 1)
    assert r.items[3].passed is False
    assert r.items[3].hint is None


def test_exit_zero_with_a_fail_line_is_not_passed():
    r = parse_check_output("PASS 1\nFAIL 2 nope\n", 0)
    assert r.passed is False
    assert r.items[2].passed is False


def test_nonzero_exit_with_only_pass_lines_is_not_passed():
    r = parse_check_output("PASS 1\nPASS 2\n", 1)
    assert r.passed is False
    assert all(i.passed for i in r.items.values())


def test_timeout_style_exit_code_is_not_passed():
    assert parse_check_output("PASS 1\n", 124).passed is False


def test_other_lines_are_ignored():
    out = "starting checks...\n\nPASS 1\nsome debug: FAIL 9 not at line start\nDONE\nFAIL 2 bad\n"
    r = parse_check_output(out, 1)
    assert set(r.items) == {1, 2}
    assert r.items[2].hint == "bad"


def test_crlf_line_endings_are_handled():
    r = parse_check_output("PASS 1\r\nFAIL 2 why\r\n", 1)
    assert set(r.items) == {1, 2}
    assert r.items[2].hint == "why"


def test_item_numbers_are_ints():
    r = parse_check_output("PASS 10\nPASS 2\n", 0)
    assert set(r.items) == {2, 10}
    assert r.passed is True
