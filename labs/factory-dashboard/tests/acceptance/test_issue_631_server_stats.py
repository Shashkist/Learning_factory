"""Exam for issue #631 (D171 and its amendment 1): server memory and disk in the dashboard snapshot.

CONTRACT (the builder works from this)

scripts/dashboard_server_stats.py   (repository root `scripts/`, plain Python 3 standard library)

    parse(free_output: str, df_output: str) -> dict | None
        free_output  raw text of `free -b`: the `Mem:` row, columns after the label are
                     total used free shared buff/cache available (bytes).
        df_output    raw text of `df -B1 -P /`: the row whose last column (Mounted on) is `/`;
                     column 2 is Size (1-byte blocks), column 3 is Used.
        Returns {"memory": "<used> of <total> (<pct>%)", "disk": "<used> of <total> (<pct>%)"}
        or None when anything is missing or malformed (never raises). Memory used is
        total - available (not the `used` column). Sizes are GiB (bytes / 2**30) with one
        decimal and the unit: `1.2 GiB`. pct = round(used / total * 100), an integer, no decimal.
        A total of 0 (memory or disk) gives None.
        Example: Mem total 4 GiB, available 3 GiB, disk Size 20 GiB, Used 5 GiB gives
        {"memory": "1.0 GiB of 4.0 GiB (25%)", "disk": "5.0 GiB of 20.0 GiB (25%)"}

    CLI: python scripts/dashboard_server_stats.py <snapshot.json> <stats-file>
        The stats file holds the raw `free -b` output, then a line that is exactly `---`, then the
        raw `df -B1 -P /` output. On good input the snapshot is rewritten in place with
        "server" set to the dict above; every other key is unchanged. On any failure (missing
        file, empty file, no separator, malformed free or df, total 0) the snapshot keeps its
        content (`"server": null` stays null), the process exits 0, prints exactly one
        non-empty line (log line, stdout and stderr together) and the snapshot is still valid JSON.

.github/workflows/factory-dashboard.yml
    publish job (production environment; stays download-and-copy only, the #568 exam is unchanged
    and keeps judging it): its final remote command (the one that moves the files into $dest)
    also runs, inside the quoted remote string and after the mv's,
        { free -b; echo ---; df -B1 -P /; } > $dest/server-stats.txt 2>/dev/null || true
    (any equivalent that writes `free -b`, a `---` line, `df -B1 -P /` into $dest/server-stats.txt
    and cannot fail the command). No new connection.
    snapshot job (no secrets): after the factory build and before the artifact upload it fetches
    https://factory.shashkist.com/server-stats.txt (curl, fail on HTTP error, with a timeout,
    saved to a file, a failure never fails the step) and runs
        python3 scripts/dashboard_server_stats.py site/snapshot.json <that file>
    The repository checkout (full-SHA pinned, no path; sparse-checkout allowed if it lists scripts)
    comes first in the job. Nothing of Shashkist/factory runs for this.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[4]
SCRIPT = REPO / "scripts" / "dashboard_server_stats.py"
WORKFLOW = REPO / ".github" / "workflows" / "factory-dashboard.yml"
GIB = 2**30
URL = "https://factory.shashkist.com/server-stats.txt"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
CONN = re.compile(r"\bssh\s+\$\{?ssh_opts\}?")


# ---- sample outputs ---------------------------------------------------------

def free_out(total: int, available: int, used: int | None = None) -> str:
    used = total - available if used is None else used
    return (
        "               total        used        free      shared  buff/cache   available\n"
        f"Mem:     {total:>12} {used:>11} {1000:>11} {2000:>11} {3000:>11} {available:>11}\n"
        "Swap:               0           0           0\n"
    )


def df_out(size: int, used: int, mount: str = "/") -> str:
    return (
        "Filesystem        1-blocks        Used   Available Capacity Mounted on\n"
        "tmpfs            412316672     1048576   411268096       1% /run\n"
        f"/dev/vda1     {size:>14} {used:>11} {size - used:>11}      26% {mount}\n"
        "/dev/vda15       109395456     6186496   103209  6% /boot/efi\n"
    )


GOOD_FREE = free_out(4 * GIB, 3 * GIB)
GOOD_DF = df_out(20 * GIB, 5 * GIB)
GOOD = {"memory": "1.0 GiB of 4.0 GiB (25%)", "disk": "5.0 GiB of 20.0 GiB (25%)"}


def load_module():
    assert SCRIPT.is_file(), "scripts/dashboard_server_stats.py does not exist"
    spec = importlib.util.spec_from_file_location("dashboard_server_stats", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse(free: str, df: str):
    return load_module().parse(free, df)


# ---- parse() ----------------------------------------------------------------

def test_parse_formats_memory_and_disk_in_gib_with_one_decimal_and_a_percentage():
    assert parse(GOOD_FREE, GOOD_DF) == GOOD


def test_parse_memory_used_is_total_minus_available_not_the_used_column():
    free = free_out(4 * GIB, 3 * GIB, used=4 * GIB - 5)
    assert parse(free, GOOD_DF)["memory"] == "1.0 GiB of 4.0 GiB (25%)"


def test_parse_rounds_sizes_to_one_decimal_and_the_percentage_to_an_integer():
    total = 4080218931  # 3.8 GiB
    used = 1288490188  # 1.2 GiB
    got = parse(free_out(total, total - used), df_out(21474836480, 6442450944))
    assert got == {"memory": "1.2 GiB of 3.8 GiB (32%)", "disk": "6.0 GiB of 20.0 GiB (30%)"}


def test_parse_takes_the_disk_from_the_root_line_not_another_mount():
    got = parse(GOOD_FREE, df_out(20 * GIB, 5 * GIB))
    assert got["disk"] == "5.0 GiB of 20.0 GiB (25%)"


def test_parse_returns_a_dict_with_exactly_memory_and_disk():
    assert set(parse(GOOD_FREE, GOOD_DF)) == {"memory", "disk"}


MALFORMED = {
    "free empty": ("", GOOD_DF),
    "df empty": (GOOD_FREE, ""),
    "both empty": ("", ""),
    "free garbage": ("command not found\n", GOOD_DF),
    "df garbage": (GOOD_FREE, "df: cannot read table of mounted file systems\n"),
    "free without a Mem row": ("total used free\nSwap: 0 0 0\n", GOOD_DF),
    "free non numeric": (
        "              total  used  free  shared  buff/cache  available\nMem: lots x y z w v\n",
        GOOD_DF,
    ),
    "free truncated row": ("              total  used\nMem: 100 50\n", GOOD_DF),
    "df without a root line": (GOOD_FREE, df_out(20 * GIB, 5 * GIB, mount="/data")),
    "df non numeric": (
        GOOD_FREE,
        "Filesystem 1-blocks Used Available Capacity Mounted on\n/dev/vda1 big some 1 26% /\n",
    ),
    "df truncated row": (GOOD_FREE, "Filesystem 1-blocks Used\n/dev/vda1 100 /\n"),
    "memory total 0": (free_out(0, 0), GOOD_DF),
    "disk total 0": (GOOD_FREE, df_out(0, 0)),
}


@pytest.mark.parametrize("free,df", list(MALFORMED.values()), ids=list(MALFORMED))
def test_parse_returns_none_for_malformed_or_empty_output_and_never_raises(free, df):
    assert parse(free, df) is None


# ---- CLI --------------------------------------------------------------------

SNAPSHOT = {
    "generated_at": "2026-11-26T10:00:00Z",
    "server": None,
    "health": {"ci": "green", "open": 3, "nested": {"a": [1, 2, {"b": None}]}},
    "rows": [{"repository": "Shashkist/factory", "n": 1}, {"repository": "x/y", "n": 2}],
    "label": "Ünïcode ok",
}


def run_cli(tmp_path: Path, stats: str | None, snapshot: dict = SNAPSHOT):
    snap = tmp_path / "snapshot.json"
    snap.write_text(json.dumps(snapshot), encoding="utf-8")
    stats_file = tmp_path / "server-stats.txt"
    if stats is not None:
        stats_file.write_text(stats, encoding="utf-8", newline="\n")
    assert SCRIPT.is_file(), "scripts/dashboard_server_stats.py does not exist"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(snap), str(stats_file)],
        capture_output=True, text=True, timeout=60, cwd=tmp_path,
    )
    return proc, snap


def stats_text(free: str = GOOD_FREE, df: str = GOOD_DF) -> str:
    return f"{free}---\n{df}"


def log_lines(proc) -> list[str]:
    return [ln for ln in (proc.stdout + proc.stderr).splitlines() if ln.strip()]


def test_cli_with_good_stats_fills_server_and_leaves_every_other_key_unchanged(tmp_path):
    proc, snap = run_cli(tmp_path, stats_text())
    assert proc.returncode == 0, proc.stderr
    got = json.loads(snap.read_text(encoding="utf-8"))
    assert got["server"] == GOOD
    assert {k: v for k, v in got.items() if k != "server"} == {
        k: v for k, v in SNAPSHOT.items() if k != "server"
    }


def test_cli_with_good_stats_replaces_an_older_server_value(tmp_path):
    old = {**SNAPSHOT, "server": {"memory": "old", "disk": "old"}}
    proc, snap = run_cli(tmp_path, stats_text(), old)
    assert proc.returncode == 0
    assert json.loads(snap.read_text(encoding="utf-8"))["server"] == GOOD


def test_cli_accepts_windows_line_endings_in_the_stats_file(tmp_path):
    proc, snap = run_cli(tmp_path, stats_text().replace("\n", "\r\n"))
    assert proc.returncode == 0
    assert json.loads(snap.read_text(encoding="utf-8"))["server"] == GOOD


ABSENT = {
    "empty stats file": "",
    "missing stats file": None,
    "whitespace only": "\n  \n",
    "no separator": GOOD_FREE + GOOD_DF,
    "free part empty": stats_text(free=""),
    "df part empty": stats_text(df=""),
    "malformed free": stats_text(free="Mem: nope\n"),
    "malformed df": stats_text(df="df: /: No such file or directory\n"),
    "html error page": "<html><body>404 Not Found</body></html>\n",
    "memory total 0": stats_text(free=free_out(0, 0)),
    "disk total 0": stats_text(df=df_out(0, 0)),
}


@pytest.mark.parametrize("stats", list(ABSENT.values()), ids=list(ABSENT))
def test_cli_without_usable_stats_keeps_server_null_exits_0_and_logs_one_line(tmp_path, stats):
    proc, snap = run_cli(tmp_path, stats)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(snap.read_text(encoding="utf-8")) == SNAPSHOT
    assert len(log_lines(proc)) == 1, f"expected exactly one log line, got {log_lines(proc)!r}"


# ---- workflow ---------------------------------------------------------------

def wf() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def snapshot_job() -> dict:
    found = [
        j for j in wf()["jobs"].values()
        if "factory_tools.dashboard build" in "\n".join(str(s.get("run", "")) for s in j["steps"])
    ]
    assert len(found) == 1
    return found[0]


def publish_job() -> dict:
    found = [
        j for j in wf()["jobs"].values()
        if str(j.get("environment", "")).strip().lower() == "production"
    ]
    assert len(found) == 1
    return found[0]


def runs(job: dict) -> list[str]:
    return [str(s.get("run", "")).replace("\\\n", " ") for s in job["steps"]]


def remote_command() -> str:
    """The quoted remote string of the last remote-shell call in the publish job."""
    text = "\n".join(runs(publish_job()))
    starts = [m.start() for m in CONN.finditer(text)]
    assert starts, "no remote call with $ssh_opts in the publish job"
    tail = text[starts[-1]:]
    m = re.search(r'"([^"]*)"\s+"([^"]*)"', tail)
    assert m, "the last remote call has no quoted remote command"
    return m.group(2)


def test_publish_last_remote_command_still_moves_the_files_into_place():
    remote = remote_command()
    assert re.search(r"\bmv\b[^;&|]*index\.html", remote)
    assert re.search(r"\bmv\b[^;&|]*snapshot\.json", remote)


def test_publish_last_remote_command_reads_free_then_df_into_server_stats_after_the_mvs():
    remote = remote_command()
    last_mv = max(m.start() for m in re.finditer(r"\bmv\b", remote))
    free = re.search(r"\bfree\s+-b\b", remote)
    sep = re.search(r"echo\s+['\"]?---['\"]?", remote)
    df = re.search(r"\bdf\s+-B1\s+-P\s+/(?![\w/.])", remote)
    assert free and sep and df, "free -b, echo ---, df -B1 -P / must all be in the remote command"
    assert last_mv < free.start() < sep.start() < df.start()
    assert re.search(r">\s*['\"]?(\$\{?dest\}?|/root/static/factory)/server-stats\.txt", remote)


def test_publish_server_stats_read_cannot_fail_the_remote_command_or_the_step():
    remote = remote_command()
    m = re.search(r"\bfree\s+-b\b.*", remote, re.S)
    assert m
    assert re.search(r"\|\|\s*(true|:)(\s|;|$)", m.group(0)), (
        "the free/df read must end in `|| true` (or `|| :`) so it cannot abort the publish"
    )
    assert not re.search(r"\bset\s+-[a-z]*e", remote)


def test_publish_adds_no_new_connection_and_no_local_read():
    text = "\n".join(runs(publish_job()))
    assert len(CONN.findall(text)) == 2, "no new remote invocation"
    assert len(re.findall(r"\bscp\b", text)) == 1
    outside = text.replace(remote_command(), "")
    assert not re.search(r"\b(free\s+-b|df\s+-B1)\b", outside)
    assert "ControlMaster" in text


def test_publish_read_comes_after_the_first_mkdir_call_and_the_scp():
    text = "\n".join(runs(publish_job()))
    assert text.index("mkdir") < text.index("scp") < text.index("free -b")


def test_publish_job_stays_download_and_copy_only_no_python_no_checkout():
    job = publish_job()
    text = "\n".join(runs(job))
    assert "python" not in text and "dashboard_server_stats" not in text
    assert not [s for s in job["steps"] if "checkout" in str(s.get("uses", ""))]


def test_snapshot_job_still_has_no_secrets_reference():
    assert not re.search(r"\bsecrets\b", yaml.safe_dump(snapshot_job()), re.I)
    assert str(snapshot_job().get("environment", "")).strip().lower() != "production"


def step_index(job: dict, pattern: str) -> list[int]:
    return [i for i, r in enumerate(runs(job)) if re.search(pattern, r)]


def build_index(job: dict) -> int:
    return step_index(job, r"factory_tools\.dashboard build")[0]


def upload_index(job: dict) -> int:
    idx = [i for i, s in enumerate(job["steps"]) if "upload-artifact" in str(s.get("uses", ""))]
    assert idx
    return idx[0]


def test_snapshot_job_fetches_the_server_stats_after_the_build_and_before_the_upload():
    job = snapshot_job()
    fetch = step_index(job, re.escape(URL))
    assert fetch, f"no step fetches {URL}"
    assert build_index(job) < fetch[0] < upload_index(job)


def fetch_step() -> dict:
    job = snapshot_job()
    idx = step_index(job, re.escape(URL))
    assert idx, f"no step fetches {URL}"
    return job["steps"][idx[0]]


def fetch_line() -> str:
    run = str(fetch_step()["run"]).replace("\\\n", " ")
    return next(ln for ln in run.splitlines() if URL in ln)


def test_snapshot_job_fetch_uses_curl_fail_timeout_and_a_local_file():
    line = fetch_line()
    assert re.search(r"\bcurl\b", line)
    assert re.search(r"(^|\s)(-[a-zA-Z]*f[a-zA-Z]*|--fail)(\s|$)", line), "fail on HTTP errors"
    assert re.search(r"(^|\s)(-m|--max-time|--connect-timeout)[\s=]\d+", line), "needs a timeout"
    assert re.search(r"(^|\s)(-o|--output)[\s=]\S+|>\s*\S+", line), "save to a file"


def test_snapshot_job_fetch_failure_does_not_fail_the_job():
    step = fetch_step()
    line = fetch_line()
    tolerated = (
        step.get("continue-on-error") is True
        or re.search(r"\|\|\s*(true|:|\{|echo|printf)", line)
        or re.search(r"\bif\s+!?\s*curl\b", line)
    )
    assert tolerated, "a failed fetch must not fail the snapshot job"


def script_call() -> tuple[int, str]:
    job = snapshot_job()
    idx = step_index(job, r"dashboard_server_stats\.py")
    assert idx, "no step runs scripts/dashboard_server_stats.py"
    return idx[0], runs(job)[idx[0]]


def test_snapshot_job_runs_the_merge_script_on_site_snapshot_json_after_the_build():
    job = snapshot_job()
    i, run = script_call()
    assert re.search(
        r"\bpython3?\s+(\./)?scripts/dashboard_server_stats\.py\s+site/snapshot\.json\s+\S+", run
    )
    assert build_index(job) < i < upload_index(job)
    assert i >= step_index(job, re.escape(URL))[0]


def test_snapshot_job_script_reads_the_file_the_fetch_wrote():
    m = re.search(r"(?:^|\s)(?:-o|--output)[\s=](\S+)|>\s*(\S+)", fetch_line())
    assert m
    path = (m.group(1) or m.group(2)).strip("'\"")
    assert path in script_call()[1], f"the script must be given {path}"


def test_snapshot_job_merge_does_not_use_the_factory_checkout_or_uv():
    job = snapshot_job()
    path = next(
        str((s.get("with") or {}).get("path", ""))
        for s in job["steps"]
        if (s.get("with") or {}).get("repository") == "Shashkist/factory"
    ).strip("/ ")
    for pattern in (r"dashboard_server_stats\.py", re.escape(URL)):
        for i in step_index(job, pattern):
            run = runs(job)[i]
            assert path not in run and "factory_tools" not in run
            assert not re.search(r"(^|[\s;&|=])uv\s", run)
            assert "PYTHONPATH" not in run


def test_snapshot_job_checks_out_this_repository_first_pinned_by_full_sha():
    job = snapshot_job()
    own = [
        (i, s) for i, s in enumerate(job["steps"])
        if str(s.get("uses", "")).startswith("actions/checkout")
        and not (s.get("with") or {}).get("repository")
        and str((s.get("with") or {}).get("path", ".")).strip("/ ") in ("", ".")
    ]
    assert own, "the snapshot job must check out this repository so scripts/ exists"
    i, step = own[0]
    assert FULL_SHA.match(str(step["uses"]).split("@", 1)[1].split()[0])
    sparse = (step.get("with") or {}).get("sparse-checkout")
    if sparse:
        assert re.search(r"(^|\s)scripts/?(\s|$)", str(sparse))
    assert i < script_call()[0]


def test_every_checkout_in_the_workflow_is_pinned_by_full_sha():
    for job in wf()["jobs"].values():
        for s in job["steps"]:
            if str(s.get("uses", "")).startswith("actions/checkout"):
                assert FULL_SHA.match(str(s["uses"]).split("@", 1)[1].split()[0])
