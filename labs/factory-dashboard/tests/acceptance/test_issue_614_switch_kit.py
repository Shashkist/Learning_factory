"""Exam for #614 (D175): the client #1 switch kit.

OFFLINE and static. Reads docs/factory/OWNER.md, the two runner files, .factory/factory.yaml,
.github/workflows/ci.yml and CLAUDE.md. The owner applies the diff in OWNER.md by hand (the
runner is owner infrastructure, D35), so the exam proves the diff applies to the runner files
as they are, and checks the result of applying it in a scratch copy. Once the owner has
applied it, the files are already switched: the reverse apply then succeeds instead, and the
exam stays green.
"""

import json
import re
import shutil
import subprocess
from fnmatch import fnmatchcase
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[4]
OWNER_MD = ROOT / "docs" / "factory" / "OWNER.md"
CLAUDE_MD = ROOT / "CLAUDE.md"
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"
PROFILE = ROOT / ".factory" / "factory.yaml"
POLL = "scripts/laptop/factory-poll.ps1"
HOOKS = "scripts/laptop/factory-hooks.json"
RUNNER_FILES = [POLL, HOOKS]

TAG = "v0.2.0"
SHA = "29a613e65389cbee2dc646cec25e71863d00064c"
GUARD_COMMAND = 'python "C:/projects/Learning_factory/scripts/factory_guard.py"'
PROGRESS_SUBCOMMANDS = ("pass-start", "pass-end", "runner-state", "publish")

ITEM_START = re.compile(r"^- \[[ xX]\] \*\*O\d+")


# helpers

def _read(path):
    assert path.is_file(), f"{path} does not exist"
    return path.read_text(encoding="utf-8")


def owner_item(text):
    """The OWNER.md list item (first line to the next top-level item or heading) that names #614."""
    lines = text.splitlines()
    found = []
    i = 0
    while i < len(lines):
        if ITEM_START.match(lines[i]):
            j = i + 1
            while (j < len(lines) and not ITEM_START.match(lines[j])
               and not lines[j].startswith("#")):
                j += 1
            if re.search(r"#614(?!\d)", lines[i]):
                found.append("\n".join(lines[i:j]))
            i = j
        else:
            i += 1
    return found


def extract_diff(item):
    """The body of the first fenced ```diff block of `item`, dedented by the fence's indent."""
    lines = item.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*)```diff\s*$", line)
        if not m:
            continue
        indent = m.group(1)
        body = []
        for later in lines[i + 1:]:
            if re.match(r"^\s*```\s*$", later):
                break
            body.append(later[len(indent):] if later.startswith(indent) else later.lstrip())
        else:
            return None
        return "\n".join(body) + "\n"
    return None


def diff_files(diff):
    return re.findall(r"^diff --git a/(\S+) b/(\S+)$", diff, re.M)


def _git(cwd, *args, check=True):
    return subprocess.run(
        ["git", "-c", "core.autocrlf=false", *args],
        cwd=cwd, capture_output=True, text=True, check=check,
    )


def make_sandbox(base, files, source_root=ROOT):
    """Copy `files` (repo-relative) from source_root into base/ with the same layout, git init."""
    base.mkdir(parents=True, exist_ok=True)
    for rel in files:
        dst = base / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_root / rel, dst)
    _git(base, "init", "-q")
    return base


def classify(base, diff):
    """('forward'|'applied'|'neither'|'both', patch path). forward: applies cleanly to the files
    as they are; applied: reverses cleanly, the diff is already in."""
    patch = base.parent / (base.name + ".patch")
    patch.write_bytes(diff.encode("utf-8"))
    fwd = _git(base, "apply", "--check", str(patch), check=False).returncode == 0
    rev = _git(base, "apply", "--check", "-R", str(patch), check=False).returncode == 0
    state = {(True, False): "forward", (False, True): "applied",
             (False, False): "neither", (True, True): "both"}[(fwd, rev)]
    return state, patch


def applied_result(base, diff):
    """Return the sandbox with the diff applied (applied here if not yet)."""
    state, patch = classify(base, diff)
    assert state in ("forward", "applied"), (
        f"the diff must apply cleanly to the runner files, or already be applied; got {state}")
    if state == "forward":
        _git(base, "apply", str(patch))
    return base


@pytest.fixture(scope="module")
def item():
    items = owner_item(_read(OWNER_MD))
    assert len(items) == 1, f"OWNER.md needs exactly one item that names #614, found {len(items)}"
    return items[0]


@pytest.fixture(scope="module")
def diff(item):
    d = extract_diff(item)
    assert d, "the #614 item has no closed fenced ```diff block"
    return d


@pytest.fixture(scope="module")
def result(tmp_path_factory, diff):
    base = make_sandbox(tmp_path_factory.mktemp("switch") / "repo", RUNNER_FILES)
    return applied_result(base, diff)


# A. the OWNER.md item

def test_owner_md_has_one_item_for_614(item):
    assert ITEM_START.match(item.splitlines()[0])
    assert "#614" in item.splitlines()[0]


def test_owner_item_holds_a_unified_diff_of_exactly_the_two_runner_files(diff):
    pairs = diff_files(diff)
    assert pairs, "the diff block has no `diff --git a/... b/...` header"
    assert all(a == b for a, b in pairs)
    assert sorted(a for a, _ in pairs) == sorted(RUNNER_FILES)
    assert len(pairs) == 2
    assert re.search(r"^@@ .* @@", diff, re.M)


def test_owner_item_names_the_pinned_tag_and_full_sha(item):
    assert TAG in item
    assert SHA in item


def test_owner_item_says_how_to_check_it_worked(item):
    low = item.lower()
    assert "one pass" in low, "say what to wait for: one pass"
    assert "factory.shashkist.com" in low
    assert "lane" in low


def test_owner_item_gives_the_switch_back_as_a_git_revert(item):
    assert "git revert" in item


# B. the diff applies

def test_diff_applies_cleanly_or_is_already_applied(tmp_path, diff):
    base = make_sandbox(tmp_path / "repo", RUNNER_FILES)
    state, _ = classify(base, diff)
    assert state in ("forward", "applied"), (
        f"`git apply --check` and `git apply --check -R` must succeed exactly one; got {state}")


# C. the applied result

def _poll_text(result):
    return (result / POLL).read_text(encoding="utf-8")


def test_runner_names_the_tag_and_the_pinned_sha(result):
    text = _poll_text(result)
    assert TAG in text
    assert SHA in text


def test_runner_refuses_a_clone_whose_head_is_not_the_pinned_sha(result):
    text = _poll_text(result)
    assert "rev-parse" in text
    sha_vars = re.findall(r"\$(\w+)\s*=\s*['\"]" + SHA + r"['\"]", text)
    assert sha_vars or SHA in text
    compares = [
        ln for ln in text.splitlines()
        if "rev-parse" in ln and (SHA in ln or any("$" + v in ln for v in sha_vars))
        or re.search(r"-(ne|eq|ceq|cne|notmatch)\b", ln) and any("$" + v in ln for v in sha_vars)
    ]
    assert compares, "HEAD from rev-parse must be compared with the pinned SHA variable"


def test_runner_installs_factory_tools_from_the_clone_and_loads_the_plugin(result):
    text = _poll_text(result)
    assert "uv tool install" in text
    assert "--plugin-dir" in text


def test_runner_prompt_names_the_plugin_skill(result):
    assert "factory:factory" in _poll_text(result)


def test_runner_progress_calls_all_go_through_factory_tools(result):
    text = _poll_text(result)
    assert "factory-tools" in text
    sub = "|".join(re.escape(s) for s in PROGRESS_SUBCOMMANDS)
    invocations = [
        ln for ln in text.splitlines()
        if re.search(r"(?<![\w-])(pass-start|pass-end|runner-state)(?![\w-])", ln)
        or re.search(r"(?<![\w-])publish\s+(--|@)", ln)
    ]
    seen = {s for s in PROGRESS_SUBCOMMANDS
            if any(re.search(r"(?<![\w-])" + s + r"(?![\w-])", ln) for ln in invocations)}
    missing = set(PROGRESS_SUBCOMMANDS) - seen
    assert not missing, f"runner no longer calls {missing}"
    for ln in invocations:
        assert "factory_progress.py" not in ln, f"still the ported script: {ln.strip()}"
        assert re.search(r"progress\s+(" + sub + r")\b", ln), (
            f"not `factory-tools progress <cmd>`: {ln.strip()}")


def test_runner_no_longer_runs_the_ported_progress_script(result):
    for ln in _poll_text(result).splitlines():
        if "factory_progress.py" in ln:
            # only the orphan-hook match may still name it
            assert "-match" in ln, f"factory_progress.py outside the orphan regex: {ln.strip()}"
            assert not re.search(r"(pass-start|pass-end|runner-state|publish)", ln)


def test_hooks_file_is_valid_json_and_progress_hooks_use_factory_tools(result):
    data = json.loads((result / HOOKS).read_text(encoding="utf-8"))
    commands = [
        h["command"]
        for groups in data["hooks"].values()
        for group in groups
        for h in group["hooks"]
        if h.get("type") == "command"
    ]
    progress = [c for c in commands if "factory_guard.py" not in c]
    assert len(progress) >= 6, "the progress hooks must all stay"
    for c in progress:
        assert "factory-tools progress hook" in c, c
        assert "factory_progress.py" not in c, c
    assert sorted(data["hooks"]) == sorted(
        ["PreToolUse", "PostToolUse", "Notification", "SubagentStop", "Stop", "SessionStart"])


def test_guard_hook_is_byte_identical_to_today(result):
    data = json.loads((result / HOOKS).read_text(encoding="utf-8"))
    guards = [
        (group.get("matcher"), h)
        for group in data["hooks"]["PreToolUse"]
        for h in group["hooks"]
        if "factory_guard.py" in h["command"]
    ]
    assert len(guards) == 1
    matcher, hook = guards[0]
    assert hook["command"] == GUARD_COMMAND
    assert hook["timeout"] == 5
    assert matcher == "Bash|PowerShell|Edit|Write|MultiEdit|NotebookEdit"
    raw = (result / HOOKS).read_text(encoding="utf-8")
    assert json.dumps(GUARD_COMMAND)[1:-1] in raw


# D. the profile

def _ci_exam_paths():
    ci = yaml.safe_load(_read(CI_YML))
    block = ci["jobs"]["exam-integrity"]["with"]["exam-paths"]
    globs = [ln.strip() for ln in block.splitlines() if ln.strip()]
    assert globs
    return globs


def _profile():
    return yaml.safe_load(_read(PROFILE))


def test_profile_exam_holds_every_exam_paths_glob_of_ci():
    exam = _profile()["exam"]
    missing = [g for g in _ci_exam_paths() if g not in exam]
    assert not missing, f"profile exam lacks the ci.yml exam-paths globs: {missing}"


def test_profile_exam_keeps_the_acceptance_glob():
    assert "labs/**/tests/acceptance/**" in _profile()["exam"]


def test_profile_exam_never_counts_manager_unit_tests():
    path = "labs/manager/tests/unit/test_x.py"
    hits = [g for g in _profile()["exam"] if fnmatchcase(path, g)]
    assert not hits, f"these globs make tests/unit exam: {hits}"


def test_dashboard_verify_runs_the_strict_acceptance_step():
    run = _profile()["verify"]["dashboard"]["run"]
    strict = [c for c in run if "pytest tests/acceptance" in c]
    assert strict, "no `pytest tests/acceptance` command in verify.dashboard"
    assert any("--confcutdir=tests" in c and '-o addopts=""' in c
               and "--junitxml=acceptance.xml" in c for c in strict), strict
    assert any("junit_gate.py" in c and "acceptance.xml" in c and "pytest" not in c for c in run)


def test_profile_has_the_p3_label():
    assert _profile()["labels"]["p3"] == "priority:p3"


# E. CLAUDE.md

def test_claude_md_names_the_merge_gate_exam_check():
    lines = [ln for ln in _read(CLAUDE_MD).splitlines()
             if "factory-tools merge-gate" in ln and '--exam-gate "manager-exam / gate"' in ln]
    assert lines, 'CLAUDE.md needs `factory-tools merge-gate ... --exam-gate "manager-exam / gate"`'


# sanity of the helpers (hand-made diff, no repository files)

def test_helpers_extract_and_apply_a_small_diff(tmp_path):
    src = tmp_path / "src"
    (src / "scripts" / "laptop").mkdir(parents=True)
    (src / POLL).write_bytes(b"one\ntwo\n")
    (src / HOOKS).write_bytes(b"{}\n")
    md = (
        "- [ ] **O99 · thing (#614).** text\n"
        "  ```diff\n"
        f"  diff --git a/{POLL} b/{POLL}\n"
        f"  --- a/{POLL}\n"
        f"  +++ b/{POLL}\n"
        "  @@ -1,2 +1,2 @@\n"
        "   one\n"
        "  -two\n"
        "  +three\n"
        "  ```\n"
        "- [ ] **O100 · other.**\n"
    )
    items = owner_item(md)
    assert len(items) == 1
    d = extract_diff(items[0])
    assert diff_files(d) == [(POLL, POLL)]
    base = make_sandbox(tmp_path / "sb", RUNNER_FILES, source_root=src)
    assert classify(base, d)[0] == "forward"
    applied_result(base, d)
    assert (base / POLL).read_bytes() == b"one\nthree\n"
    assert classify(base, d)[0] == "applied"
