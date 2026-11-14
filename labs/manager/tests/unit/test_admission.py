from pathlib import Path

import pytest

from app import admission


def test_parse_meminfo_reads_memavailable_in_mb() -> None:
    text = "MemTotal: 8000000 kB\nMemFree: 100 kB\nMemAvailable:   2048000 kB\n"
    assert admission.parse_meminfo(text) == 2000


@pytest.mark.parametrize(
    "text", ["", "MemFree: 5 kB\n", "MemAvailable: lots kB\n", "MemAvailable:\n"]
)
def test_parse_meminfo_returns_none_when_unusable(text: str) -> None:
    assert admission.parse_meminfo(text) is None


def test_mem_ok_counts_the_new_limit_against_the_reserve() -> None:
    assert admission.mem_ok(1800, 256, 1500)
    assert admission.mem_ok(1756, 256, 1500)  # exactly at the reserve is allowed
    assert not admission.mem_ok(1755, 256, 1500)


def test_mem_ok_refuses_when_free_memory_is_unknown() -> None:
    assert not admission.mem_ok(None, 0, 0)


def test_resources_ok_reads_settings_per_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemAvailable: 1024000 kB\n", encoding="ascii")  # 1000 MB
    monkeypatch.setenv("LF_MEMINFO_PATH", str(meminfo))
    monkeypatch.setenv("LF_DISK_RESERVE_GB", "0")
    monkeypatch.delenv("LF_MEM_RESERVE_MB", raising=False)
    assert not admission.resources_ok(256)
    monkeypatch.setenv("LF_MEM_RESERVE_MB", "100")
    assert admission.resources_ok(256)
    monkeypatch.setenv("LF_MEM_RESERVE_MB", "not-a-number")  # falls back to the default
    assert not admission.resources_ok(256)


def test_missing_meminfo_file_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LF_MEMINFO_PATH", str(tmp_path / "nope"))
    monkeypatch.setenv("LF_DISK_RESERVE_GB", "0")
    assert not admission.resources_ok(1)
    assert any("memory" in r for r in admission.low_resources())


def test_disk_reserve_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemAvailable: 99999999 kB\n", encoding="ascii")
    monkeypatch.setenv("LF_MEMINFO_PATH", str(meminfo))
    monkeypatch.setenv("LF_DISK_PATH", str(tmp_path))
    monkeypatch.setenv("LF_DISK_RESERVE_GB", "100000000")
    assert not admission.resources_ok(256)
    assert any("disk" in r for r in admission.low_resources())


def test_bad_disk_path_is_unknown_not_an_error(monkeypatch, tmp_path):
    from app import admission

    monkeypatch.setenv("LF_DISK_PATH", str(tmp_path / "missing"))
    assert admission.disk_free_gb() is None
    assert "disk free unknown" in admission.low_resources()
    assert admission.resources_ok(0) is False
