"""Gate over the sharded docker exam (#727, D202).

Usage: python docker_shards_gate.py <dir>

Below <dir>, each shard has a folder holding docker-acceptance.xml (JUnit) and
docker-shard-manifest.json (written by docker_shard.py). Exit 0 only when exactly the announced
number of shards is present, the manifests agree, every report passes junit_gate.py, the files
seen in each report are the files its manifest gives that shard, and no file is in two shards.
"""
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import junit_gate  # noqa: E402

REPORT = "docker-acceptance.xml"
MANIFEST = "docker-shard-manifest.json"


def _file_of(case, known: set[str]) -> str:
    if case.get("file"):
        return case.get("file").replace("\\", "/")
    parts = case.get("classname", "").split(".")
    for i in range(len(parts), 0, -1):  # longest prefix that is a known file
        if "/".join(parts[:i]) + ".py" in known:
            return "/".join(parts[:i]) + ".py"
    while len(parts) > 1 and parts[-1][:1].isupper():  # trailing class names
        parts.pop()
    return "/".join(parts) + ".py"


def check(root: Path) -> list[str]:
    errors: list[str] = []
    dirs = {p.parent for p in root.rglob(REPORT)} | {p.parent for p in root.rglob(MANIFEST)}
    shards: dict[int, Path] = {}
    manifests: dict[Path, dict] = {}
    for d in sorted(dirs):
        name = d.name
        if not (d / REPORT).is_file():
            errors.append(f"{name}: {REPORT} is missing")
        if not (d / MANIFEST).is_file():
            errors.append(f"{name}: {MANIFEST} is missing")
            continue
        try:
            data = json.loads((d / MANIFEST).read_text(encoding="utf-8"))
            k = data["shard"]
            manifests[d] = data
            if k in shards:
                errors.append(f"shard {k} is present twice ({shards[k].name}, {name})")
            shards[k] = d
        except (ValueError, KeyError, TypeError) as e:
            errors.append(f"{name}: unreadable manifest ({e!r})")
    counts = {m.get("shards") for m in manifests.values()}
    files = {json.dumps(m.get("files"), sort_keys=True) for m in manifests.values()}
    if len(counts) > 1:
        errors.append(f"manifests disagree on the shard count: {sorted(map(str, counts))}")
    if len(files) > 1:
        errors.append("manifests disagree on the files of the shards")
    n = next(iter(counts)) if len(counts) == 1 else None
    if n is not None and set(shards) != set(range(1, n + 1)):
        errors.append(f"want shards 1..{n}, found {sorted(shards)}")
    if not manifests:
        errors.append("no shard found: no manifest")
    seen_in: dict[str, int] = {}
    for k, d in sorted(shards.items()):
        if not (d / REPORT).is_file():
            continue
        errors += [f"shard {k}: {e}" for e in junit_gate.check(str(d / REPORT))]
        assigned = manifests[d].get("files", {})
        known = set(assigned)
        seen = {_file_of(c, known) for c in ET.parse(d / REPORT).getroot().iter("testcase")}
        mine = {f for f, s in assigned.items() if s == k}
        for f in sorted(seen - mine):
            errors.append(f"shard {k} ran {f}, which its manifest does not assign to it")
        for f in sorted(mine - seen):
            errors.append(f"shard {k} did not run {f}, which its manifest assigns to it")
        for f in sorted(seen):
            if f in seen_in and seen_in[f] != k:
                errors.append(f"{f} ran in shards {seen_in[f]} and {k}")
            seen_in.setdefault(f, k)
    return errors


if __name__ == "__main__":
    problems = check(Path(sys.argv[1]))
    for p in problems:
        print(f"FAIL: {p}")
    sys.exit(1 if problems else 0)
