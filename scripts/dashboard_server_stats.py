"""Merge the server's memory and disk into the dashboard snapshot (#631, D171).

Usage: python3 scripts/dashboard_server_stats.py <snapshot.json> <stats-file>

The stats file holds `free -b`, a line `---`, then `df -B1 -P /`. On any failure the
snapshot is left as it is, the exit code is 0 and one log line is printed.
"""
from __future__ import annotations

import json
import sys

GIB = 2**30


def _fmt(used: int, total: int) -> str:
    return f"{used / GIB:.1f} GiB of {total / GIB:.1f} GiB ({round(used / total * 100)}%)"


def _memory(free_output: str) -> str:
    for line in free_output.splitlines():
        parts = line.split()
        if parts and parts[0] == "Mem:":
            total, available = int(parts[1]), int(parts[6])
            return _fmt(total - available, total)
    raise ValueError("no Mem row")


def _disk(df_output: str) -> str:
    for line in df_output.splitlines():
        parts = line.split()
        if len(parts) >= 6 and parts[-1] == "/":
            return _fmt(int(parts[2]), int(parts[1]))
    raise ValueError("no root row")


def parse(free_output: str, df_output: str) -> dict | None:
    try:
        return {"memory": _memory(free_output), "disk": _disk(df_output)}
    except (ValueError, IndexError, ZeroDivisionError, AttributeError, TypeError):
        return None


def main(argv: list[str]) -> int:
    try:
        snap_path, stats_path = argv[1], argv[2]
        with open(stats_path, encoding="utf-8") as f:
            text = f.read().replace("\r\n", "\n")
        free, sep, df = text.partition("\n---\n")
        server = parse(free, df) if sep else None
        if server is None:
            print("server stats: no usable stats, server stays null")
            return 0
        with open(snap_path, encoding="utf-8") as f:
            snap = json.load(f)
        snap["server"] = server
        with open(snap_path, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False)
        print("server stats: merged")
    except Exception as exc:  # noqa: BLE001 - never fail the build
        print(f"server stats: skipped ({type(exc).__name__})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
