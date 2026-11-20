import shutil
from pathlib import Path

import pytest
import yaml

from app.content import load_labs, parse_check_output


def test_non_mapping_yaml_is_reported(tmp_path: Path) -> None:
    lab = tmp_path / "c" / "bad"
    lab.mkdir(parents=True)
    (lab / "lab.yaml").write_text("- just\n- a list\n", encoding="utf-8")
    result = load_labs(tmp_path)
    assert result.labs == {}
    assert result.errors["c/bad"]


def test_directory_without_lab_yaml_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "c" / "notalab").mkdir(parents=True)
    result = load_labs(tmp_path)
    assert result.labs == {} and result.errors == {}


def test_missing_root_loads_nothing(tmp_path: Path) -> None:
    result = load_labs(tmp_path / "absent")
    assert result.labs == {} and result.errors == {}


def test_pass_line_hint_is_ignored() -> None:
    r = parse_check_output("PASS 1 extra\n", 0)
    assert r.items[1].hint is None and r.passed


def test_empty_output_with_exit_zero_passes() -> None:
    assert parse_check_output("", 0).passed is True


def test_fail_is_never_overwritten_by_a_later_pass() -> None:
    r = parse_check_output("FAIL 1 nope\nPASS 1\n", 0)
    assert r.passed is False
    assert r.items[1].passed is False and r.items[1].hint == "nope"


def test_tabs_and_leading_spaces_are_accepted() -> None:
    r = parse_check_output("FAIL 1\tnope\n  FAIL 2\n\tPASS 3\n", 0)
    assert r.passed is False
    assert r.items[1].hint == "nope"
    assert r.items[2].passed is False and r.items[2].hint is None
    assert r.items[3].passed is True


def test_malformed_fail_line_is_noise_when_the_exit_code_is_zero() -> None:
    assert parse_check_output("PASS 1\nFAIL oops\n", 0).passed is True
    assert parse_check_output("PASS 1\nFAIL oops\n", 1).passed is False


def test_item_numbers_are_ascii_digits_only() -> None:
    assert parse_check_output("PASS ١\n", 0).items == {}
    assert set(parse_check_output("PASS 1\nFAIL ١ x\n", 0).items) == {1}


def test_failed_word_is_not_a_fail_line() -> None:
    assert parse_check_output("FAILED to find x\nPASS 1\n", 0).passed is True


# ---- fixture labs ----------------------------------------------------------

SAMPLE = Path(__file__).resolve().parents[4] / "labs" / "content" / "sample" / "hello"


def _copy(root: Path, rel: str, **over: object) -> Path:
    dest = root / rel
    shutil.copytree(SAMPLE, dest)
    data = yaml.safe_load((dest / "lab.yaml").read_text(encoding="utf-8"))
    data.update(over)
    (dest / "lab.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return dest


def test_broken_duplicate_does_not_take_the_valid_lab_offline(tmp_path: Path) -> None:
    _copy(tmp_path, "c/hello")
    _copy(tmp_path, "c/hello-copy", status="live")
    result = load_labs(tmp_path)
    assert "hello" in result.labs
    assert "c/hello" not in result.errors
    assert any("c/hello" in e and "duplicate" in e for e in result.errors["c/hello-copy"])


def test_two_valid_duplicates_are_both_withheld(tmp_path: Path) -> None:
    _copy(tmp_path, "c/hello")
    _copy(tmp_path, "d/hello")
    result = load_labs(tmp_path)
    assert result.labs == {}
    assert set(result.errors) == {"c/hello", "d/hello"}


@pytest.mark.parametrize("bad_id", ["a/b", "..", " hello", "hello ", "a b", ""])
def test_bad_lab_id_is_rejected(tmp_path: Path, bad_id: str) -> None:
    _copy(tmp_path, "c/hello", id=bad_id)
    result = load_labs(tmp_path)
    assert result.labs == {} and result.errors["c/hello"]


def test_lab_id_must_equal_directory_name(tmp_path: Path) -> None:
    _copy(tmp_path, "c/other", id="hello")
    result = load_labs(tmp_path)
    assert result.labs == {}
    assert any("directory" in e for e in result.errors["c/other"])


@pytest.mark.parametrize(
    "domain",
    ["*.example.com", "*", "example", "1.2.3.4", "10.0.0.0/8", "https://example.com",
     "example.com:443", "example.com/path", "Example.com", "-a.example.com",
     "a-.example.com", "a..example.com", "example.com.", "a" * 250 + ".com"],
)
def test_bad_allow_domain_is_rejected(tmp_path: Path, domain: str) -> None:
    _copy(tmp_path, "c/hello", allow_domains=[domain])
    result = load_labs(tmp_path)
    assert result.labs == {}
    assert any("allow_domains" in e for e in result.errors["c/hello"])


def test_good_allow_domains_are_accepted_and_kept(tmp_path: Path) -> None:
    _copy(tmp_path, "c/hello", allow_domains=["pypi.org", "files.pythonhosted.org", "a-b.c1.io"])
    lab = load_labs(tmp_path).labs["hello"]
    assert lab.allow_domains == ["pypi.org", "files.pythonhosted.org", "a-b.c1.io"]


def test_fast_settings_default_and_are_read(tmp_path: Path) -> None:
    _copy(tmp_path, "c/hello", fast_lab_min=12)
    lab = load_labs(tmp_path).labs["hello"]
    assert lab.fast_lab_min == 12
    assert [t.min_task_seconds for t in lab.tasks] == [20, 20]


@pytest.mark.parametrize("value", [-1, "ten", 1.5, True])
def test_bad_fast_lab_min_is_reported(tmp_path: Path, value: object) -> None:
    _copy(tmp_path, "c/hello", fast_lab_min=value)
    result = load_labs(tmp_path)
    assert result.labs == {}
    assert any("fast_lab_min" in e for e in result.errors["c/hello"])


def test_validated_content_is_kept(tmp_path: Path) -> None:
    _copy(tmp_path, "c/hello")
    lab = load_labs(tmp_path).labs["hello"]
    assert lab.path == tmp_path / "c" / "hello"
    assert lab.time_limit_min == 15 and lab.internet is False and lab.sudo is False
    assert lab.description["he"]
    quiz, hands_on = lab.tasks
    assert quiz.correct == 1 and quiz.options[1]["en"] == "echo" and quiz.question["he"]
    assert len(hands_on.checklist) == 2 and hands_on.checklist[0]["en"]
