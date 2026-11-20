"""Lab content loader and validator (layout: <root>/<course>/<lab>/lab.yaml)."""

from __future__ import annotations

import re
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.flags import DEFAULT_MIN_TASK_SECONDS

LANGS = ("en", "he")
STATUSES = ("draft", "published", "internal")
IMAGES = ("term", "code")
KINDS = ("quiz", "hands-on")
# module -> (first lesson, last lesson), per the final lesson plan (D16)
MODULE_LESSONS = {
    0: (1, 4),
    1: (5, 12),
    2: (13, 22),
    3: (23, 38),
    4: (39, 48),
    5: (49, 56),
    6: (57, 63),
}
TASK_FILES = ("task.en.md", "task.he.md", "solution.en.md", "solution.he.md")
SCRIPTS = ("setup.sh", "check.sh")
_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")


def _valid_hostname(value: Any) -> bool:
    """A plain lowercase DNS name with at least one dot; no IPs, wildcards, ports or paths."""
    if not isinstance(value, str) or len(value) > 253:
        return False
    labels = value.split(".")
    if len(labels) < 2 or not all(_LABEL.fullmatch(x) for x in labels):
        return False
    return any(c.isalpha() for c in labels[-1])  # an all-numeric last label is an IP


@dataclass
class Task:
    id: str
    kind: str
    question: dict[str, str] = field(default_factory=dict)
    options: list[dict[str, str]] = field(default_factory=list)
    correct: int | None = None
    checklist: list[dict[str, str]] = field(default_factory=list)
    min_task_seconds: int = DEFAULT_MIN_TASK_SECONDS


@dataclass
class Lab:
    id: str
    title: dict[str, str]
    status: str
    image: str
    module: int
    lesson: int
    path: Path
    tasks: list[Task] = field(default_factory=list)
    description: dict[str, str] = field(default_factory=dict)
    time_limit_min: int = 60
    internet: bool = False
    allow_domains: list[str] = field(default_factory=list)
    sudo: bool = False
    fast_lab_min: int | None = None


@dataclass
class LoadResult:
    labs: dict[str, Lab] = field(default_factory=dict)
    errors: dict[str, list[str]] = field(default_factory=dict)
    warnings: dict[str, list[str]] = field(default_factory=dict)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _bilingual(value: Any, name: str, errs: list[str]) -> dict[str, str]:
    if not isinstance(value, dict):
        errs.append(f"{name}: must be a mapping with en and he")
        return {}
    out: dict[str, str] = {}
    for lang in LANGS:
        text = value.get(lang)
        if isinstance(text, str) and text.strip():
            out[lang] = text
        else:
            errs.append(f"{name}: missing {lang} text")
    return out


def _bilingual_list(value: Any, name: str, errs: list[str]) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        errs.append(f"{name}: must be a non-empty list")
        return []
    return [_bilingual(item, f"{name}[{i}]", errs) for i, item in enumerate(value)]


def _check_hands_on(lab_dir: Path, task: dict[str, Any], tid: str, errs: list[str]) -> Task:
    checklist = _bilingual_list(task.get("checklist"), f"task {tid} checklist", errs)
    min_seconds: Any = task.get("min_task_seconds", DEFAULT_MIN_TASK_SECONDS)
    if not _is_int(min_seconds) or min_seconds < 0:
        errs.append(f"task {tid}: min_task_seconds must be a non-negative integer (seconds)")
        min_seconds = DEFAULT_MIN_TASK_SECONDS
    result = Task(id=tid, kind="hands-on", checklist=checklist, min_task_seconds=min_seconds)
    tdir = lab_dir / "tasks" / tid
    if not tdir.is_dir():
        errs.append(f"task {tid}: directory tasks/{tid}/ is missing")
        return result
    for name in (*TASK_FILES, *SCRIPTS):
        path = tdir / name
        if not path.is_file():
            errs.append(f"task {tid}: {name} is missing")
        elif name in SCRIPTS and sys.platform != "win32":
            if not path.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
                errs.append(f"task {tid}: {name} is not executable")
    return result


def _check_quiz(task: dict[str, Any], tid: str, errs: list[str]) -> Task:
    question = _bilingual(task.get("question"), f"task {tid} question", errs)
    options = _bilingual_list(task.get("options"), f"task {tid} options", errs)
    correct: Any = task.get("correct")
    if not _is_int(correct) or not 0 <= correct < len(options):
        errs.append(f"task {tid}: correct must be exactly one index into options")
        correct = None
    return Task(id=tid, kind="quiz", question=question, options=options, correct=correct)


def _check_tasks(lab_dir: Path, raw: Any, errs: list[str]) -> list[Task]:
    if not isinstance(raw, list) or not raw:
        errs.append("tasks: must be a non-empty list")
        return []
    tasks: list[Task] = []
    seen: set[str] = set()
    for i, task in enumerate(raw):
        if not isinstance(task, dict):
            errs.append(f"tasks[{i}]: must be a mapping")
            continue
        tid, kind = task.get("id"), task.get("kind")
        if not isinstance(tid, str) or not _TASK_ID.fullmatch(tid):
            errs.append(f"tasks[{i}]: id must be a simple name")
            continue
        if tid in seen:
            errs.append(f"task {tid}: duplicate task id")
        seen.add(tid)
        if kind == "quiz":
            tasks.append(_check_quiz(task, tid, errs))
        elif kind == "hands-on":
            tasks.append(_check_hands_on(lab_dir, task, tid, errs))
        else:
            errs.append(f"task {tid}: kind must be one of {', '.join(KINDS)}")
    return tasks


def _check_course_fields(data: dict[str, Any], errs: list[str]) -> None:
    module: Any = data.get("module")
    lesson: Any = data.get("lesson")
    module_ok = _is_int(module) and module in MODULE_LESSONS
    if not module_ok:
        errs.append("module: required, an integer 0..6")
    if not _is_int(lesson):
        errs.append("lesson: required, an integer 1..63")
    elif module_ok:
        lo, hi = MODULE_LESSONS[module]
        if not lo <= lesson <= hi:
            errs.append(f"lesson: {lesson} is outside module {module} range {lo}-{hi}")


def _validate(lab_dir: Path, data: Any) -> tuple[Lab | None, list[str]]:
    errs: list[str] = []
    if not isinstance(data, dict):
        return None, ["lab.yaml: must be a mapping"]
    lab_id: Any = data.get("id")
    if not isinstance(lab_id, str) or not _TASK_ID.fullmatch(lab_id):
        errs.append("id: required, letters, digits, '.', '_' or '-' only")
    elif lab_id != lab_dir.name:
        errs.append(f"id: {lab_id!r} must equal the lab directory name {lab_dir.name!r}")
    title = _bilingual(data.get("title"), "title", errs)
    description = _bilingual(data.get("description"), "description", errs)
    image: Any = data.get("image")
    status: Any = data.get("status")
    if image not in IMAGES:
        errs.append(f"image: must be one of {', '.join(IMAGES)}")
    if status not in STATUSES:
        errs.append(f"status: must be one of {', '.join(STATUSES)}")
    limit = data.get("time_limit_min", 60)
    if not _is_int(limit) or limit <= 0:
        errs.append("time_limit_min: must be a positive integer")
    fast_min: Any = data.get("fast_lab_min")
    if fast_min is not None and (not _is_int(fast_min) or fast_min < 0):
        errs.append("fast_lab_min: must be a non-negative integer (minutes)")
    for flag in ("internet", "sudo"):
        if not isinstance(data.get(flag, False), bool):
            errs.append(f"{flag}: must be true or false")
    domains = data.get("allow_domains", [])
    if not isinstance(domains, list):
        errs.append("allow_domains: must be a list of hostnames")
        domains = []
    for d in domains:
        if not _valid_hostname(d):
            errs.append(f"allow_domains: {d!r} is not a plain lowercase hostname")
    _check_course_fields(data, errs)
    tasks = _check_tasks(lab_dir, data.get("tasks"), errs)
    if errs:
        return None, errs
    return (
        Lab(
            id=lab_id,
            title=title,
            status=status,
            image=image,
            module=data["module"],
            lesson=data["lesson"],
            path=lab_dir,
            tasks=tasks,
            description=description,
            time_limit_min=limit,
            internet=data.get("internet", False),
            allow_domains=list(domains),
            sudo=data.get("sudo", False),
            fast_lab_min=fast_min,
        ),
        errs,
    )


_SEPARATORS = re.compile(r"(\|\||&&|;|\|)")
_REDIRECTS = re.compile(r"\d*>&\d+|\d*>>?\s*\S+")
_COMMENT = re.compile(r"\s+#.*$")
_STDOUT_TO_FILE = re.compile(r"(?<![02-9&])>>?\s*[^\s&>]|&>>?\s*\S")
_STDERR_TO_FILE = re.compile(r"2>>?\s*[^\s&>]|2>&1|&>>?\s*\S")
_WHILE_READ = re.compile(r"while\s+(?:\w+=\S*\s+)*read\b")
_CAT_SUBSHELL = re.compile(r"\$\(\s*cat(?:\s+-\w*)*\s*\)|`\s*cat(?:\s+-\w*)*\s*`")
_SU_READER = re.compile(r"su\b.*\s-c\s+(['\"])\s*(?:read\b[^'\"]*|cat(?:\s+-\w*)*\s*)\1")


def _reads_stdin(segment: str) -> bool:
    if "<" in segment:
        return False
    words = _REDIRECTS.sub(" ", segment).split()
    if not words:
        return False
    if words[0] == "read":
        return True
    if words[0] == "cat" and all(w.startswith("-") for w in words[1:]):
        return True
    return bool(_SU_READER.match(segment.strip()))


def _loop_depth_change(segment: str) -> int:
    words = re.sub(r"^(?:do|then|else)\s+", "", segment.strip()).split()
    if not words:
        return 0
    return 1 if words[0] in ("while", "until", "for") else -1 if words[0] == "done" else 0


def _script_warnings(text: str, label: str) -> list[str]:
    """Authoring rules (spec 7): the script arrives on stdin, and a timeout kills it."""
    found: list[str] = []
    stdin_msg = "reads stdin, which is the script itself; add </dev/null"
    segments: list[tuple[int, str, bool]] = []  # (line, text, follows a pipe)
    for n, raw in enumerate(text.splitlines(), 1):
        line = _COMMENT.sub("", raw.strip())
        if not line or line.startswith("#"):
            continue
        parts = _SEPARATORS.split(line)
        for i in range(0, len(parts), 2):
            segments.append((n, parts[i], i > 0 and parts[i - 1] == "|"))
        if _CAT_SUBSHELL.search(line) and "<" not in line:
            found.append(f"{label} line {n}: {stdin_msg}")
        if line.endswith("&") and not line.endswith("&&"):
            if not (_STDOUT_TO_FILE.search(line) and _STDERR_TO_FILE.search(line)):
                found.append(
                    f"{label} line {n}: a background job keeps the exec open; "
                    "redirect it with >/dev/null 2>&1"
                )
    for k, (n, seg, piped) in enumerate(segments):
        if piped:
            continue
        if _WHILE_READ.match(seg.strip()):
            redirected, depth = "<" in seg, 0
            for _n, later, _p in segments[k:]:
                depth += _loop_depth_change(later)
                if depth <= 0:
                    redirected = redirected or "<" in later
                    break
            if not redirected:
                found.append(f"{label} line {n}: {stdin_msg}")
        elif _reads_stdin(seg):
            found.append(f"{label} line {n}: {stdin_msg}")
    return found


def _lab_warnings(lab: Lab) -> list[str]:
    found: list[str] = []
    for task in lab.tasks:
        for name in SCRIPTS if task.kind == "hands-on" else ():
            path = lab.path / "tasks" / task.id / name
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            found += _script_warnings(text, f"task {task.id}: {name}")
    return found


def load_labs(root: Path) -> LoadResult:
    result = LoadResult()
    found: dict[str, list[str]] = {}  # lab id -> lab dirs
    loaded: dict[str, Lab] = {}  # lab dir -> valid lab
    for yaml_path in sorted(root.glob("*/*/lab.yaml")):
        lab_dir = yaml_path.parent
        rel = lab_dir.relative_to(root).as_posix()
        try:
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, UnicodeDecodeError, OSError) as exc:
            result.errors[rel] = [f"lab.yaml: cannot be parsed: {exc}"]
            continue
        lab, errs = _validate(lab_dir, data)
        if isinstance(data, dict) and isinstance(data.get("id"), str):
            found.setdefault(data["id"], []).append(rel)
        if lab is None:
            result.errors[rel] = errs
        else:
            loaded[rel] = lab
            if warns := _lab_warnings(lab):
                result.warnings[rel] = warns
    for lab_id, dirs in found.items():
        if len(dirs) > 1:
            valid = [rel for rel in dirs if rel in loaded]
            served = valid if len(valid) == 1 else []  # a lone valid lab stays served
            for rel in dirs:
                if rel in served:
                    continue
                others = ", ".join(d for d in dirs if d != rel)
                result.errors.setdefault(rel, []).append(
                    f"id: duplicate lab id {lab_id!r} (also in {others})"
                )
            if len(valid) > 1:  # a broken copy never takes the valid lab offline
                for rel in valid:
                    del loaded[rel]
    for lab in loaded.values():
        result.labs[lab.id] = lab
    return result


MAX_HINT = 300
_ITEM = re.compile(r"\s*(PASS|FAIL)\s+([0-9]{1,4})(?:\s+(.*))?$")


@dataclass
class ItemResult:
    passed: bool
    hint: str | None = None


@dataclass
class CheckResult:
    passed: bool
    items: dict[int, ItemResult] = field(default_factory=dict)


def parse_check_output(stdout: str, exit_code: int, n_items: int | None = None) -> CheckResult:
    """Parse `PASS <n>` / `FAIL <n> [hint]` lines.

    With `n_items` (the checklist length), `passed` is True exactly when the exit code is 0
    and every item 1..n_items has a PASS and no FAIL; lines for an n outside that range are
    noise for `passed` (they stay in `items`). Without it, `passed` is exit 0 and no FAIL.
    """
    items: dict[int, ItemResult] = {}
    failed = False
    for line in stdout.splitlines():
        m = _ITEM.match(line)
        if not m:  # a stray FAIL line (not `FAIL <n>`) is noise, not an item failure
            continue
        n = int(m.group(2))
        if m.group(1) == "PASS":
            items.setdefault(n, ItemResult(True))  # never overwrites a FAIL
        else:
            failed = True
            items[n] = ItemResult(False, (m.group(3) or "").strip()[:MAX_HINT] or None)
    if n_items is None:
        return CheckResult(exit_code == 0 and not failed, items)
    ok = all(n in items and items[n].passed for n in range(1, n_items + 1))
    return CheckResult(exit_code == 0 and ok, items)
