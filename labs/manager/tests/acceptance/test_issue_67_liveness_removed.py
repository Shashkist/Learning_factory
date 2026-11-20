# ruff: noqa: E501
"""Acceptance tests for issue #67: the one-off liveness diagnostic of D30 is removed.

  * `labs/deploy/smoke/liveness.sh` is deleted, and nothing in `server-smoke.yml` (header,
    choices, `case`) mentions it.
  * `labs/deploy/smoke/isolation.sh` keeps `--init` in its run options and says why, next to it:
    with `sh` as PID 1, a `docker exec` that runs busybox `timeout` made the container exit
    (code 0) about a second later, so the smoke's labs need an init, as `lf-lab-term` has (tini).
  * D30 notes that the diagnostic was removed.
"""
import re
from pathlib import Path

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
SMOKE_DIR = REPO_ROOT / "labs" / "deploy" / "smoke"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "server-smoke.yml"
DECISIONS = REPO_ROOT / "docs" / "factory" / "DECISIONS.md"


def test_the_liveness_script_is_deleted():
    assert not (SMOKE_DIR / "liveness.sh").exists()
    assert (SMOKE_DIR / "isolation.sh").is_file(), "the isolation smoke must stay"


def test_the_workflow_does_not_mention_liveness_anywhere():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "liveness" not in text.lower()


def _run_opts_block(text: str) -> tuple[int, list[str]]:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.match(r"\s*RUN_OPTS=\(", line):
            return i, lines
    raise AssertionError("isolation.sh no longer defines RUN_OPTS")


def test_isolation_keeps_init_and_says_why_next_to_it():
    text = (SMOKE_DIR / "isolation.sh").read_text(encoding="utf-8")
    i, lines = _run_opts_block(text)
    assert "--init" in lines[i].split(), "RUN_OPTS must keep --init"
    # the comment block right above RUN_OPTS carries the cause
    j = i - 1
    comment: list[str] = []
    while j >= 0 and lines[j].lstrip().startswith("#"):
        comment.insert(0, lines[j])
        j -= 1
    why = " ".join(comment).lower()
    assert "pid 1" in why, "say that the cause is `sh` as PID 1"
    assert "timeout" in why, "name busybox `timeout` run through docker exec"
    assert "exec" in why
    assert "tini" in why, "say that lf-lab-term has the same init (tini)"


def test_d30_records_the_removal():
    text = DECISIONS.read_text(encoding="utf-8")
    start = text.index("## D30 ")
    end = text.index("\n## D31 ", start)
    d30 = text[start:end]
    assert re.search(r"#67", d30), "D30 needs a note naming #67"
    assert re.search(r"removed", d30.split("#67", 1)[1][:400], re.IGNORECASE)
