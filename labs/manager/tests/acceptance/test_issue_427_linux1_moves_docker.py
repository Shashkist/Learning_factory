"""Acceptance test for issue #427, Docker part (CI `docker` job, runsc): the moves of the real
Linux 1 lab in a browser against the REAL manager.

Reported on the live site: Next on task 1 (a quiz) of Linux 1 shows an error instead of task 2, and
the whole lab screen seems to reload. The cause is not known, so this exam is also the diagnosis:
when it is red, the failure message carries what the manager answered for the failing move (HTTP
status and body, as the browser captured it for
`POST /api/sessions/<id>/tasks/current/<action>`),
the task id that failed, the output of the browser run and the tail of the manager log.

Setup is the #167 harness (real manager process, real Postgres, TCP forwarder for the terminal,
smoke student from the CLI): its fixtures are imported, only the content folder is this exam's.
The content folder holds
  - `linux/linux-1`: the real Linux 1 lab, copied unchanged (it is already `status: published`);
  - `linux/linux-1-broken`: a copy whose task 02-directory-tree has a `setup.sh` that exits 1, so
    the move from task 1 to task 2 fails deterministically (criterion 4).
Real content is never touched.

Browser part: labs/web/tests/acceptance/playwright.docker.427.config.ts and
issue-427-linux1-moves.pw.ts, two browser tests, one lab session each:
  criteria 1-3  all nine moves of Linux 1, task 1 to task 10; every move shows the next task's
                title with no `role="alert"`, and the page, the terminal iframe element and the
                iframe's own document keep a JS tag set before the first move (no reload, no
                navigation). Quizzes are answered, then Next; hands-on tasks are left with Skip
                (Next only appears on a passed task).
  criterion 4   in the broken copy, Next from the answered quiz gets 502 setup_failed, the screen
                shows the en `lab.error.setup_failed` text, keeps task 1, keeps the tags, and a
                second Next is enabled and answers the same error.
The pytest exam then checks that both lab containers are gone.

Fails, never skips, when the built UI, Node or Chromium is missing (as the #167 exam).
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest
import yaml
from test_issue_167_browser_e2e_docker import (  # noqa: F401, F811
    SMOKE_ID,
    SMOKE_PASSWORD,
    WEB_DIR,
    _annotations,
    _log_tail,
    _rm,
    _watch,
    admin_dsn,
    docker,
    image,
    manager,
    network,
)

pytestmark = pytest.mark.docker

REPO_ROOT = Path(__file__).resolve().parents[4]
LINUX_1 = REPO_ROOT / "labs" / "content" / "linux" / "linux-1"
EN_JSON = WEB_DIR / "src" / "i18n" / "en.json"
PW_CONFIG = "tests/acceptance/playwright.docker.427.config.ts"
BROKEN_ID = "linux-1-broken"
BROKEN_TASK = "02-directory-tree"
LAB_TITLE = "Linux 1: navigation, files, permissions"  # lab.yaml title.en
BROKEN_TITLE = "Linux 1 (broken setup copy, exam only)"


def _heading(md: str, fallback: str) -> str:
    """The title the manager shows for a hands-on task: its first Markdown heading."""
    m = re.search(r"^#+[ \t]*(.+?)[ \t]*$", md, re.M)
    return m.group(1) if m else fallback


def _tasks() -> list[dict]:
    lab = yaml.safe_load((LINUX_1 / "lab.yaml").read_text(encoding="utf-8"))
    out = []
    for t in lab["tasks"]:
        if t["kind"] == "quiz":
            out.append(
                {
                    "id": t["id"],
                    "kind": "quiz",
                    "title": t["question"]["en"],
                    "correct_text": t["options"][t["correct"]]["en"],
                }
            )
        else:
            md = (LINUX_1 / "tasks" / t["id"] / "task.en.md").read_text(encoding="utf-8")
            out.append(
                {
                    "id": t["id"],
                    "kind": "hands-on",
                    "title": _heading(md, t["id"]),
                    "correct_text": None,
                }
            )
    return out


@pytest.fixture(scope="module")
def content_dir(tmp_path_factory) -> Path:
    """Overrides the #167 fixture of the same name: real Linux 1 plus the broken-setup copy."""
    root = tmp_path_factory.mktemp("content427")
    shutil.copytree(LINUX_1, root / "linux" / "linux-1")
    broken = root / "linux" / BROKEN_ID
    shutil.copytree(LINUX_1, broken)
    lab_yaml = broken / "lab.yaml"
    text = lab_yaml.read_text(encoding="utf-8")
    assert text.startswith("id: linux-1\n"), "lab.yaml of Linux 1 no longer starts with its id"
    assert f'en: "{LAB_TITLE}"' in text, "the en title of Linux 1 changed: update LAB_TITLE"
    assert "status: published" in text, "Linux 1 is no longer published"
    lab_yaml.write_text(
        text.replace("id: linux-1\n", f"id: {BROKEN_ID}\n", 1).replace(
            f'en: "{LAB_TITLE}"', f'en: "{BROKEN_TITLE}"', 1
        ),
        encoding="utf-8",
    )
    setup = broken / "tasks" / BROKEN_TASK / "setup.sh"
    setup.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    return root


def test_linux_1_has_ten_tasks_as_the_exam_expects():
    tasks = _tasks()
    assert [t["id"] for t in tasks][0] == "01-pwd-ls" and len(tasks) == 10, tasks
    assert tasks[1]["id"] == BROKEN_TASK and tasks[1]["kind"] == "hands-on"


def test_the_linux_1_moves_in_a_browser_keep_the_screen_and_a_setup_failure_shows_its_error(
    manager, tmp_path  # noqa: F811
):
    """Criteria 1-4 of #427 (see the module docstring)."""
    assert shutil.which("node"), "node is not installed (the docker job must set up Node)"
    assert shutil.which("npx"), "npx is not installed (the docker job must set up Node)"
    assert (WEB_DIR / "node_modules" / "@playwright" / "test").is_dir(), (
        "labs/web/node_modules has no @playwright/test: run `npm ci` in labs/web first"
    )
    assert (WEB_DIR / PW_CONFIG).is_file(), f"labs/web/{PW_CONFIG} is missing"
    setup_error = json.loads(EN_JSON.read_text(encoding="utf-8"))["lab.error.setup_failed"]
    report = tmp_path / "report.json"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LF_", "SMOKE_"))}
    env.update(
        CI="1",
        LF_E2E_BASE_URL=manager,
        LF_E2E_ID=SMOKE_ID,
        LF_E2E_PASSWORD=SMOKE_PASSWORD,
        LF_E2E_LAB_TITLE=LAB_TITLE,
        LF_E2E_BROKEN_LAB_ID=BROKEN_ID,
        LF_E2E_BROKEN_LAB_TITLE=BROKEN_TITLE,
        LF_E2E_SETUP_ERROR=setup_error,
        LF_E2E_TASKS=json.dumps(_tasks()),
        LF_E2E_REPORT=str(report),
        LF_E2E_OUTPUT_DIR=str(tmp_path / "pw-output"),
    )
    before = set(
        docker("ps", "-a", "--filter", "name=^lab-", "--format", "{{.Names}}", check=False)
        .stdout.split()
    )
    stop = threading.Event()
    seen: set[str] = set()
    watcher = threading.Thread(target=_watch, args=(stop, seen), daemon=True)
    watcher.start()
    try:
        run = subprocess.run(
            ["npx", "--no-install", "playwright", "test", "-c", PW_CONFIG],
            cwd=WEB_DIR, env=env, capture_output=True, text=True, timeout=900,
        )
        stop.set()
        watcher.join(timeout=10)
        out = run.stdout + run.stderr
        moves = [ln for ln in out.splitlines() if "LF427 " in ln]
        diagnosis = (
            "\n--- manager answers seen by the browser (LF427 lines) ---\n"
            + "\n".join(moves)
            + "\n--- manager log tail ---\n"
            + _log_tail(6000)
        )
        assert run.returncode == 0, (
            "the Linux 1 browser flow failed (is Chromium installed? "
            "`npx playwright install chromium`):\n" + out[-8000:] + diagnosis
        )
        data = json.loads(report.read_text(encoding="utf-8"))
        stats = data["stats"]
        assert stats["expected"] == 2, f"want two browser tests passed: {stats}" + diagnosis
        assert not (stats["unexpected"] or stats["skipped"] or stats["flaky"]), stats
        assert SMOKE_PASSWORD not in out
        sessions = _annotations(data, "lf-session")
        assert len(sessions) == 2, f"want two lf-session annotations: {sessions}"
        for session in sessions:
            name = f"lab-{session}"
            assert name in seen, f"{name} was never seen running; saw {sorted(seen)}"
            deadline = time.time() + 30
            while docker("inspect", name, check=False).returncode == 0:
                assert time.time() < deadline, f"{name} still exists after End lab"
                time.sleep(1)
    finally:
        stop.set()
        watcher.join(timeout=10)
        try:
            mine = _annotations(json.loads(report.read_text(encoding="utf-8")), "lf-session")
        except (OSError, ValueError):
            mine = []
        for name in (seen - before) | {f"lab-{s}" for s in mine}:
            _rm(name)
