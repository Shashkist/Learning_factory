"""Unit tests for scripts/dashboard_server_stats.py (#631)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import dashboard_server_stats as m

FREE = "total used free shared buff/cache available\nMem: 4294967296 1 2 3 4 3221225472\n"
DF = "Filesystem 1-blocks Used Available Capacity Mounted on\n/dev/a 21474836480 5368709120 1 26% /\n"


def test_parse_good():
    assert m.parse(FREE, DF) == {
        "memory": "1.0 GiB of 4.0 GiB (25%)",
        "disk": "5.0 GiB of 20.0 GiB (25%)",
    }


def test_parse_bad_is_none():
    assert m.parse("", DF) is None
    assert m.parse(FREE, "x /\n") is None


def test_main_merges_and_tolerates_failure(tmp_path, capsys):
    snap = tmp_path / "s.json"
    snap.write_text(json.dumps({"server": None, "a": 1}))
    stats = tmp_path / "t.txt"
    stats.write_text(FREE + "---\n" + DF)
    assert m.main(["x", str(snap), str(stats)]) == 0
    assert json.loads(snap.read_text())["server"]["disk"].startswith("5.0")
    snap.write_text(json.dumps({"server": None}))
    assert m.main(["x", str(snap), str(tmp_path / "missing")]) == 0
    assert json.loads(snap.read_text()) == {"server": None}
    assert len(capsys.readouterr().out.strip().splitlines()) == 2
