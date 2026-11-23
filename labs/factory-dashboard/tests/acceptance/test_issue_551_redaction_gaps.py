"""Exam for issue #551 (review leftovers of #543, D143): redaction gaps and crew titles.

Observable contract, from the issue only:
  * The published live.json (the real renderer's output) contains no foreign URL: backslash dot segments under
    an allowed github.com/Shashkist/factory/ or .../Learning_factory/ prefix, an allowed URL containing a
    backslash, "/..", "%2e" or "%5c" (any case), an uppercase scheme, and a scheme-less github.com/someone/else/...
    are all redacted, in the title, the detail, the sub-step and the agent.
  * Plain allowed URLs (pull requests of this repository and of Shashkist/factory) are still published unchanged.
  * A crew node titled "PR#547" or "Build issue#1" attaches to this repository's lane #547 / #1 (the page, run
    in node through the #68 fake browser); "factory#1" still attaches to the other repository's lane.
No network, no machine environment (LF_CREW_DIR is unset); events are built in memory.
"""
# ruff: noqa: E501
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("exam551_lanes", HERE / "test_issue_68_live_view_lanes.py")
assert _spec and _spec.loader
lanes_exam = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lanes_exam)
base = lanes_exam.base

FOREIGN = "Shashkist/factory"
OK_URLS = ["https://github.com/Shashkist/Learning_factory/pull/552", "https://github.com/Shashkist/factory/pull/1"]

# (published text, a marker that must not survive redaction)
BAD_FORMS = [
    pytest.param(r"https://github.com/Shashkist/factory/x\..\..\..\someone-mk\else\pull\1", "someone-mk", id="backslash-dots-factory"),
    pytest.param(r"https://github.com/Shashkist/Learning_factory/x\..\..\..\someone-mk\else\pull\1", "someone-mk", id="backslash-dots-learning-factory"),
    pytest.param(r"https://github.com/Shashkist/factory/pull\1-evil-mk", "evil-mk", id="allowed-with-backslash"),
    pytest.param("https://github.com/Shashkist/factory/pull/1/../../../evil-mk/x", "evil-mk", id="allowed-with-slash-dotdot"),
    pytest.param("https://github.com/Shashkist/Learning_factory/pull/1/%2e%2e/evil-mk", "evil-mk", id="allowed-with-pct2e-lower"),
    pytest.param("https://github.com/Shashkist/Learning_factory/pull/1/%2E%2E/evil-mk", "evil-mk", id="allowed-with-pct2e-upper"),
    pytest.param("https://github.com/Shashkist/factory/pull/1%5cevil-mk", "evil-mk", id="allowed-with-pct5c-lower"),
    pytest.param("https://github.com/Shashkist/factory/pull/1%5Cevil-mk", "evil-mk", id="allowed-with-pct5c-upper"),
    pytest.param("HTTPS://example.com/x", "example.com", id="uppercase-scheme"),
    pytest.param("Https://example.com/x", "example.com", id="mixed-case-scheme"),
    pytest.param("github.com/someone-mk/else/pull/1", "someone-mk", id="schemeless-github"),
]
FIELDS = ["title", "detail", "substep", "agent"]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("LF_CREW_DIR", raising=False)
    monkeypatch.delenv("LF_PROGRESS_FILE", raising=False)


def live_with(field: str, value: str) -> dict:
    """Render one step event whose `field` carries `value`; the other fields are harmless."""
    ev = {"kind": "step", "at": base.ago(1), "issue": 1, "repo": "", "title": "task", "agent": "implementer-1",
          "role": "implementer", "station": "build", "substep": "tests", "status": "start", "detail": "ok"}
    ev[field] = f"see {value}" if field in ("title", "detail") else value
    return base.render([base.start_pass(45), ev])


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("form,marker", BAD_FORMS)
def test_every_url_form_is_redacted_in_the_published_output(form, marker, field):
    blob = json.dumps(live_with(field, form), ensure_ascii=False)
    assert marker not in blob, f"{form!r} reached live.json through {field}"


@pytest.mark.parametrize("field", ["title", "detail"])
@pytest.mark.parametrize("url", OK_URLS)
def test_plain_allowed_urls_are_published_unchanged(url, field):
    assert url in json.dumps(live_with(field, url)), "over-redaction: an allowed link was removed"


def test_allowed_url_is_kept_while_a_bad_one_in_the_same_text_is_removed():
    live = live_with("detail", f"{OK_URLS[0]} HTTPS://example.com/x github.com/someone-mk/else")
    blob = json.dumps(live)
    assert OK_URLS[0] in blob and "example.com" not in blob and "someone-mk" not in blob


# ---- crew titles ---------------------------------------------------------------------------------

def _steps(issue: int, repo: str, title: str, agent: str) -> dict:
    return {"kind": "step", "at": base.ago(1), "issue": issue, "repo": repo, "title": title, "agent": agent,
            "role": "implementer", "station": "build", "substep": "tests", "status": "done", "detail": ""}


def crew_page(tmp_path: Path, crew_title: str) -> dict:
    live = base.render([base.start_pass(45), _steps(1, "", "Local one", "impl-l1"),
                        _steps(1, FOREIGN, "Foreign one", "impl-f1"), _steps(547, "", "Local 547", "impl-l547")])
    live = copy.deepcopy(live)
    live["crew"] = [{"id": "root", "type": "manager", "state": "busy", "tool": "Bash", "label": "pass", "title": "pass",
                     "last": base.ago(0.1), "children": [
                         {"id": "kid", "type": "implementer", "state": "busy", "tool": "CrewTool", "label": "x",
                          "title": crew_title, "last": base.ago(0.1), "children": []}]}]
    return lanes_exam.run_page(tmp_path, live)


def attached(root, lane_key: str) -> bool:
    got = [n for n in lanes_exam.by_class(root, "lane") if n["a"].get("data-lane") == lane_key]
    assert got, f"no lane {lane_key!r}"
    return "CrewTool" in base.text(got[0])


KEYS = ["1", "547", FOREIGN + "#1"]


@pytest.mark.parametrize("title,lane", [("PR#547", "547"), ("Build issue#1", "1"), ("#547", "547"), ("Learning_factory#1", "1")])
def test_crew_title_attaches_to_this_repositorys_lane(tmp_path, title, lane):
    root = crew_page(tmp_path, title)
    assert attached(root, lane), f"crew {title!r} did not attach to lane {lane}"
    assert [k for k in KEYS if k != lane and attached(root, k)] == [], "attached to more than one lane"


def test_crew_title_factory_hash_1_still_attaches_to_the_other_repositorys_lane(tmp_path):
    root = crew_page(tmp_path, "factory#1")
    assert attached(root, FOREIGN + "#1")
    assert not attached(root, "1") and not attached(root, "547")
