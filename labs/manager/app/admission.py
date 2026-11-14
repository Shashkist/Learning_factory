"""Admission control (D24): refuse a start when memory, disk or the per-kind cap is short.

Every setting is read from the environment per call."""
import logging
import os
import shutil
from typing import Any

from app import db
from app.content import Lab

GB = 1024**3
log = logging.getLogger("lab-manager")


def env_num(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def parse_meminfo(text: str) -> int | None:
    """MemAvailable in MB, or None when the line is missing or malformed."""
    for line in text.splitlines():
        if line.startswith("MemAvailable:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1]) // 1024
    return None


def mem_available_mb() -> int | None:
    path = os.environ.get("LF_MEMINFO_PATH", "/proc/meminfo")
    try:
        with open(path, encoding="ascii") as f:
            return parse_meminfo(f.read())
    except OSError:
        return None


def disk_free_gb() -> float | None:
    """Free GB, or None (logged) when the path cannot be read."""
    try:
        return shutil.disk_usage(os.environ.get("LF_DISK_PATH", "/")).free / GB
    except OSError as exc:
        log.error("cannot read disk usage (%s)", type(exc).__name__)
        return None


def mem_ok(available_mb: int | None, new_memory_mb: int, reserve_mb: float) -> bool:
    """Unknown free memory refuses: the owner's other services come first."""
    return available_mb is not None and available_mb - new_memory_mb >= reserve_mb


def low_resources() -> list[str]:
    """Reasons (for logs) why free memory or disk is under its reserve right now."""
    out = []
    avail = mem_available_mb()
    if not mem_ok(avail, 0, env_num("LF_MEM_RESERVE_MB", 1500)):
        out.append(f"memory available {avail} MB")
    free = disk_free_gb()
    if free is None:
        out.append("disk free unknown")
    elif free < env_num("LF_DISK_RESERVE_GB", 10):
        out.append(f"disk free {free:.1f} GB")
    return out


def resources_ok(new_memory_mb: int) -> bool:
    if not mem_ok(mem_available_mb(), new_memory_mb, env_num("LF_MEM_RESERVE_MB", 1500)):
        return False
    free = disk_free_gb()
    return free is not None and free >= env_num("LF_DISK_RESERVE_GB", 10)


async def cap_ok(pool: Any, lab: Lab, labs: dict[str, Lab]) -> bool:
    """True while live sessions of the lab's image kind are under that kind's cap."""
    cap = int(env_num("LF_MAX_CODE", 8) if lab.image == "code" else env_num("LF_MAX_TERM", 20))
    ids = [i for i, other in labs.items() if other.image == lab.image]
    live = await pool.fetchval(
        f"select count(*) from {db.SCHEMA}.lab_sessions "
        "where status in ('starting', 'running') and lab_id = any($1::text[])",
        ids,
    )
    return bool(live < cap)
