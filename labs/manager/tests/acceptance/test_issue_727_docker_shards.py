"""Acceptance tests for issue #727 (D202): the docker exam job runs as 4 shards, and a gate job
keeps the single decisive check named `docker`. Non-docker: parses ci.yml, runs pytest in a
subprocess on small fake test trees, and runs the gate script on fabricated report trees. No
Docker, no network, only fake data.

Interface pinned here (the implementer builds to it)

ci.yml, job key `docker` (stays the first job whose key holds "docker", #1):
  - `strategy.matrix.shard` is exactly [1, 2, 3, 4], `fail-fast: false`; the leg `name` contains
    `${{ matrix.shard }}` and is never exactly `docker`.
  - its one docker acceptance pytest line keeps `tests/acceptance`, `-m docker`,
    `--junitxml=docker-acceptance.xml`, loads `-p docker_shard`, has no narrowing flag; the same
    step sets `LF_DOCKER_SHARD` to `${{ matrix.shard }}/4`, `LF_DOCKER_SHARD_MANIFEST` to a path
    ending in `docker-shard-manifest.json`, and a PYTHONPATH holding scripts/ci. A `timeout`
    stays in front of pytest and ends well inside the job limit. `junit_gate.py
    docker-acceptance.xml` still runs.
  - an `actions/upload-artifact` step with `if: always()` uploads `docker-acceptance.xml` and
    `docker-shard-manifest.json` under the artifact name `docker-shard-${{ matrix.shard }}`.
  - the docker-marked unit tests (`-m docker tests/unit`) run in shard 1 only.
ci.yml, job key `docker-gate`, `name: docker`: needs `changes` and `docker`; `if` has `always()`
  and `needs.changes.outputs.heavy == 'true'`; checks out the repo, downloads the artifacts with
  `pattern: docker-shard-*` (no merge-multiple, so each shard keeps its own folder), runs
  `python3 scripts/ci/docker_shards_gate.py <dir>`, fails unless `needs.docker.result` is
  success; no Node, no npm, no pytest, nothing soft. Image jobs keep `needs: docker` and do not
  need `docker-gate`.

scripts/ci/docker_shard.py (pytest plugin, `-p docker_shard`, PYTHONPATH=scripts/ci):
  - `LF_DOCKER_SHARD` unset: no change. `k/n` with integers and 1 <= k <= n: after `-m`
    selection keep the items of whole test files assigned to shard k; the rest are deselected
    (pytest reports them as deselected). Partition: files by selected item count descending,
    then path ascending; each goes to the shard with the fewest items so far, ties to the lowest
    shard number. Any other value, the empty string included, makes pytest exit non-zero with a
    message naming LF_DOCKER_SHARD and runs nothing.
  - `LF_DOCKER_SHARD_MANIFEST=<file>`: writes {"shard": k, "shards": n, "files": {<path relative
    to the pytest rootdir, forward slashes>: <shard>}} for every file that had a selected item,
    all shards' files.

scripts/ci/docker_shards_gate.py <dir> reads, below <dir>, one folder per shard:
    <dir>/docker-shard-<k>/docker-acceptance.xml        (pytest JUnit report)
    <dir>/docker-shard-<k>/docker-shard-manifest.json   (the plugin's manifest)
  (found recursively: any folder depth is fine, a report and a manifest sit side by side).
  A testcase belongs to the file named by its `file` attribute, else by its `classname`: the
  dotted module path, optionally followed by class names (`tests.acceptance.test_a.TestX` is
  tests/acceptance/test_a.py). Exit 0 only when exactly `shards` shards are present, all
  manifests agree, every report passes the junit_gate.py rules (no tests, skipped, failed,
  errored are failures), the files seen in each report equal that shard's files in the manifest,
  and no file shows in two shards. Else `FAIL: ...` lines and exit 1.

Older exams that constrain the change (they stay unchanged and must still pass)
  - #167 (ci wiring): the gate step of job `docker` keeps `tests/acceptance`, `-m docker`,
    `--junitxml=docker-acceptance.xml`, `junit_gate.py docker-acceptance.xml`, none of -k,
    --deselect, --ignore, --lf, --collect-only; node, npm ci, build and chromium stay before it;
    nothing soft; no other web work. So the shard is chosen by a plugin and an env value, never
    by a flag, and `docker-gate` runs no Node or npm.
  - #243: job `docker` keeps an explicit timeout-minutes of 20..69; no exam timeout above it.
  - #271: `docker` needs exactly {changes} and keeps the heavy `if` verbatim; image jobs have
    no `if` and need only changes/docker, so they stay on `docker`, never on `docker-gate`.
  - #364: exactly one pytest line with `tests/acceptance` and `-m docker` in job `docker`, with
    `--junitxml=docker-acceptance.xml`; the non-docker exam stays in one place (the gate job
    runs no pytest).
  - #651: job `docker` keeps its gVisor install step with runsc-suid and `systemctl reload`.
  - #545: job `docker` keeps its job-level `LF_APT_MIRRORS` env line.
  - #1: the first job whose key holds "docker" is still the real one (`docker` before
    `docker-gate`) and keeps runsc and `-m` in its steps.
  - #72 and #81: the manager image job still needs `docker`.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
SCRIPTS_CI = REPO_ROOT / "scripts" / "ci"
PLUGIN = SCRIPTS_CI / "docker_shard.py"
GATE = SCRIPTS_CI / "docker_shards_gate.py"
SHARDS = 4

# ---------------------------------------------------------------------------- ci.yml helpers


def _jobs() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]


def _steps(job: str) -> list[dict]:
    assert job in _jobs(), f"ci.yml has no {job} job"
    return _jobs()[job]["steps"]


def _needs(job: dict) -> list[str]:
    n = job.get("needs") or []
    return [n] if isinstance(n, str) else list(n)


def _flat(value) -> str:
    """Any yaml value as text with the spaces inside ${{ }} removed."""
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    return re.sub(r"\$\{\{\s*(.*?)\s*\}\}", lambda m: "${{" + m.group(1) + "}}", text)


def _pytest_step() -> dict:
    hits = [s for s in _steps("docker")
            if "tests/acceptance" in (s.get("run") or "") and "-m docker" in (s.get("run") or "")]
    assert len(hits) == 1, f"want one docker acceptance step in job docker, got {len(hits)}"
    return hits[0]


def _pytest_line() -> str:
    run = _pytest_step()["run"].replace("\\\n", " ")
    lines = [ln.strip() for ln in run.splitlines()
             if "pytest" in ln and not ln.strip().startswith("#")]
    assert len(lines) == 1, lines
    return lines[0]


def _step_env_text(step: dict) -> str:
    """Everything that can set an env value for the step: its run text, its env, the job's env."""
    return _flat([step.get("run", ""), step.get("env") or {}, _jobs()["docker"].get("env") or {}])


# ---------------------------------------------------------------------------- ci.yml: docker


def test_docker_job_is_a_matrix_of_exactly_four_shards_that_does_not_fail_fast():
    job = _jobs()["docker"]
    strategy = job.get("strategy") or {}
    assert strategy.get("fail-fast") is False, "fail-fast must be false so every shard reports"
    shards = (strategy.get("matrix") or {}).get("shard")
    assert [str(s) for s in shards or []] == ["1", "2", "3", "4"], shards
    extra = set(strategy.get("matrix") or {}) - {"shard"}
    assert not extra, f"the matrix has other axes or entries: {extra}"


def test_no_docker_leg_is_named_exactly_docker_and_each_leg_names_its_shard():
    name = str(_jobs()["docker"].get("name", ""))
    assert "${{matrix.shard}}" in _flat(name), f"leg name {name!r} does not name its shard"
    assert name.strip() != "docker", "a leg named exactly `docker` would be mistaken for the gate"


def test_docker_job_keeps_its_needs_condition_timeout_and_first_place():
    jobs = _jobs()
    job = jobs["docker"]
    assert _needs(job) == ["changes"]
    assert str(job.get("if", "")).strip() == "needs.changes.outputs.heavy == 'true'"
    assert 20 <= int(job["timeout-minutes"]) <= 69
    keys = list(jobs)
    assert keys.index("docker") < keys.index("docker-gate"), "docker must come before docker-gate"


def test_shard_run_is_one_pytest_line_without_a_narrowing_flag_and_loads_the_plugin():
    line = _pytest_line()
    line = line[line.index("pytest"):] + " "  # not the `timeout -k 60` before it
    for want in ("tests/acceptance", "-m docker", "--junitxml=docker-acceptance.xml",
                 "-p docker_shard"):
        assert want in line, f"{want!r} missing from: {line}"
    for flag in (" -k ", " -k=", "--deselect", "--ignore", "--lf ", "--collect-only"):
        assert flag not in line, f"the shard line narrows the run with {flag.strip()!r}"
    assert "junit_gate.py docker-acceptance.xml" in _pytest_step()["run"]


def test_shard_step_sets_the_shard_the_manifest_and_a_python_path_to_scripts_ci():
    text = _step_env_text(_pytest_step())
    assert re.search(r"LF_DOCKER_SHARD\W+\$\{\{matrix\.shard\}\}/4", text), \
        "LF_DOCKER_SHARD must be `${{ matrix.shard }}/4`"
    assert re.search(r"LF_DOCKER_SHARD_MANIFEST[^,]{0,80}?docker-shard-manifest\.json", text), \
        "LF_DOCKER_SHARD_MANIFEST must point at docker-shard-manifest.json"
    assert re.search(r"PYTHONPATH[^,]{0,80}?scripts/ci", text), "scripts/ci must be on PYTHONPATH"


def test_shard_run_keeps_a_timeout_that_ends_well_inside_the_job_limit():
    line = _pytest_line()
    before = line[:line.index("pytest")]
    m = re.search(r"\btimeout\b.*?\s(\d+)m\b", before)
    assert m, f"no `timeout ... <n>m` in front of pytest: {line}"
    limit = int(_jobs()["docker"]["timeout-minutes"])
    assert int(m.group(1)) + 5 <= limit, f"budget {m.group(1)}m is not well inside {limit}m"


def test_each_leg_uploads_report_and_manifest_even_when_the_step_fails():
    ups = [s for s in _steps("docker")
           if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
    hits = [s for s in ups
            if "docker-acceptance.xml" in _flat((s.get("with") or {}).get("path", ""))]
    assert len(hits) == 1, "want one upload step for docker-acceptance.xml"
    step = hits[0]
    path = _flat(step["with"]["path"])
    assert "docker-shard-manifest.json" in path, "the manifest is not uploaded"
    assert "always()" in _flat(step.get("if", "")), "the upload must run when the exam step fails"
    assert _flat(step["with"]["name"]) == "docker-shard-${{matrix.shard}}", step["with"]["name"]
    order = _steps("docker").index(step), _steps("docker").index(_pytest_step())
    assert order[0] > order[1], "the upload must come after the exam step"


def test_docker_marked_unit_tests_run_only_in_shard_one():
    runs = [s for s in _steps("docker")
            if re.search(r"-m docker\b.*tests/unit", s.get("run") or "")]
    assert len(runs) == 1, f"want exactly one docker unit step, got {len(runs)}"
    cond = _flat(runs[0].get("if", ""))
    assert re.search(r"matrix\.shard\s*==\s*'?1'?", cond), f"unit step condition: {cond!r}"


def test_docker_job_keeps_runsc_apt_mirror_and_browser_steps():
    job = _jobs()["docker"]
    text = json.dumps(job)
    assert "runsc-suid" in text and "systemctl reload docker" in text
    assert job["env"]["LF_APT_MIRRORS"].startswith("http")
    for needle in ("actions/setup-node@", "npm ci", "npm run build", "playwright install"):
        assert needle in text, f"the docker job lost {needle!r}"


# ---------------------------------------------------------------------------- ci.yml: gate


def test_docker_gate_job_carries_the_check_name_docker_and_needs_changes_and_docker():
    jobs = _jobs()
    assert "docker-gate" in jobs, "ci.yml has no docker-gate job"
    gate = jobs["docker-gate"]
    assert str(gate.get("name")) == "docker", "the gate's check name must be exactly `docker`"
    assert {"changes", "docker"} <= set(_needs(gate))
    others = [k for k, v in jobs.items() if k != "docker-gate" and str(v.get("name")) == "docker"]
    assert not others, f"more than one job named docker: {others}"
    assert str(gate.get("runs-on", "")).startswith("ubuntu")


def test_docker_gate_runs_even_after_failed_legs_but_only_for_heavy_changes():
    cond = _flat(_jobs()["docker-gate"].get("if", ""))
    assert "always()" in cond, "without always() a failed shard would skip the gate"
    assert "needs.changes.outputs.heavy=='true'" in cond.replace(" ", ""), cond
    assert "||" not in cond, f"an `or` could bypass the heavy test: {cond}"
    assert "failure()" not in cond and "cancelled()" not in cond, cond


def test_docker_gate_fails_unless_every_leg_succeeded_and_has_nothing_soft():
    gate = _jobs()["docker-gate"]
    assert gate.get("continue-on-error") in (None, False)
    for step in gate["steps"]:
        assert step.get("continue-on-error") in (None, False), step
        assert "|| true" not in (step.get("run") or ""), step
    text = _flat(gate["steps"])
    assert "needs.docker.result" in text, "the gate never looks at the result of the legs"
    assert "success" in text


def test_docker_gate_downloads_the_shard_artifacts_and_runs_the_gate_script():
    steps = _steps("docker-gate")
    assert any(str(s.get("uses", "")).startswith("actions/checkout@") for s in steps)
    down = [s for s in steps if str(s.get("uses", "")).startswith("actions/download-artifact@")]
    assert len(down) == 1, "want one download-artifact step"
    w = down[0].get("with") or {}
    assert str(w.get("pattern", "")).startswith("docker-shard-"), w
    assert str(w.get("merge-multiple", "false")).lower() == "false", \
        "merge-multiple would put every shard's report into one folder"
    runs = [s["run"] for s in steps if "docker_shards_gate.py" in (s.get("run") or "")]
    assert len(runs) == 1, "want one step running docker_shards_gate.py"
    m = re.search(r"python3?\s+(\S*scripts/ci/docker_shards_gate\.py)\s+\S+", runs[0])
    assert m, runs[0]
    run_step = next(s for s in steps if "docker_shards_gate.py" in (s.get("run") or ""))
    assert steps.index(down[0]) < steps.index(run_step), "download before the gate script"
    assert (REPO_ROOT / m.group(1).lstrip("./")).name == "docker_shards_gate.py"


def test_docker_gate_sets_up_no_node_runs_no_npm_and_no_pytest():
    for step in _steps("docker-gate"):
        assert not str(step.get("uses", "")).startswith("actions/setup-node@"), step
        run = step.get("run") or ""
        assert not re.search(r"\bnpm\b|\bnpx\b|\bpytest\b", run), run


def test_image_jobs_still_wait_for_the_legs_through_docker_not_the_gate():
    jobs = _jobs()
    for name in ("image-term", "image-manager"):
        assert name in jobs, f"{name} is gone"
        needs = _needs(jobs[name])
        assert "docker" in needs and set(needs) <= {"changes", "docker"}, needs
        assert "if" not in jobs[name]


# ---------------------------------------------------------------------------- plugin helpers

FAKE_FILES = {  # path under the fake rootdir -> (docker cases, non-docker cases)
    "tests/acceptance/test_a.py": (5, 0),
    "tests/acceptance/test_b.py": (3, 2),
    "tests/acceptance/test_c.py": (3, 0),
    "tests/acceptance/test_d.py": (2, 0),
    "tests/acceptance/sub/test_e.py": (1, 1),
    "tests/acceptance/test_f.py": (0, 4),  # never docker: must not be in any manifest
}
# Known packing, counts a5 b3 c3 d2 e1 (b before c on the tie, by path)
PACKED_4 = {"tests/acceptance/test_a.py": 1, "tests/acceptance/test_b.py": 2,
            "tests/acceptance/test_c.py": 3, "tests/acceptance/test_d.py": 4,
            "tests/acceptance/sub/test_e.py": 4}
PACKED_2 = {"tests/acceptance/test_a.py": 1, "tests/acceptance/test_b.py": 2,
            "tests/acceptance/test_c.py": 2, "tests/acceptance/test_d.py": 1,
            "tests/acceptance/sub/test_e.py": 2}


def _module(docker: int, plain: int) -> str:
    parts = ["import pytest\n"]
    if docker:
        parts.append(f"@pytest.mark.docker\n@pytest.mark.parametrize('i', range({docker}))\n"
                     "def test_d(i):\n    assert True\n")
    if plain:
        parts.append(f"@pytest.mark.parametrize('i', range({plain}))\n"
                     "def test_p(i):\n    assert True\n")
    return "\n".join(parts)


@pytest.fixture(scope="module")
def fake_root(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("fake_exam")
    (root / "pytest.ini").write_text("[pytest]\nmarkers =\n    docker: needs docker\n")
    for rel, (d, p) in FAKE_FILES.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(_module(d, p))
    return root


def _run_pytest(root: Path, *args: str, shard: str | None = None, manifest: Path | None = None,
                plugin: bool = True, hashseed: str = "0"):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("LF_DOCKER_") and k != "PYTEST_ADDOPTS"}
    env["PYTHONPATH"] = str(SCRIPTS_CI)
    env["PYTHONHASHSEED"] = hashseed
    if shard is not None:
        env["LF_DOCKER_SHARD"] = shard
    if manifest is not None:
        env["LF_DOCKER_SHARD_MANIFEST"] = str(manifest)
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(root),
           "-o", "addopts=", *(["-p", "docker_shard"] if plugin else []), *args]
    return subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True, timeout=120)


def _collected(res) -> list[str]:
    return sorted(ln.strip() for ln in res.stdout.splitlines() if "::" in ln)


def _collect(root: Path, k: int | None, n: int = SHARDS, **kw) -> list[str]:
    shard = None if k is None else f"{k}/{n}"
    res = _run_pytest(root, "--collect-only", "-m", "docker", "tests", shard=shard, **kw)
    assert res.returncode in (0, 5), res.stdout + res.stderr
    return _collected(res)


def _expected_docker_ids() -> list[str]:
    return sorted(f"{rel}::test_d[{i}]" for rel, (d, _) in FAKE_FILES.items() for i in range(d))


def _files_of(ids: list[str]) -> set[str]:
    return {i.split("::")[0] for i in ids}


# ---------------------------------------------------------------------------- plugin


def test_the_plugin_file_exists():
    assert PLUGIN.is_file(), "scripts/ci/docker_shard.py is missing"


def test_without_the_variable_the_plugin_changes_nothing(fake_root):
    with_plugin = _run_pytest(fake_root, "--collect-only", "-m", "docker", "tests")
    without = _run_pytest(fake_root, "--collect-only", "-m", "docker", "tests", plugin=False)
    assert with_plugin.returncode == 0, with_plugin.stdout + with_plugin.stderr
    assert _collected(with_plugin) == _collected(without) == _expected_docker_ids()
    every = _run_pytest(fake_root, "--collect-only", "tests")
    assert len(_collected(every)) == sum(d + p for d, p in FAKE_FILES.values())


def test_the_union_of_the_shards_is_every_docker_case_exactly_once(fake_root):
    seen: list[str] = []
    for k in range(1, SHARDS + 1):
        seen += _collect(fake_root, k)
    assert sorted(seen) == _expected_docker_ids()


def test_shards_hold_docker_marked_cases_only(fake_root):
    for k in range(1, SHARDS + 1):
        ids = _collect(fake_root, k)
        assert not any("test_p[" in i or "test_f.py" in i for i in ids), ids


def test_whole_files_stay_together_and_no_file_is_in_two_shards(fake_root):
    owner: dict[str, int] = {}
    for k in range(1, SHARDS + 1):
        ids = _collect(fake_root, k)
        for f in _files_of(ids):
            assert f not in owner, f"{f} is in shards {owner[f]} and {k}"
            owner[f] = k
            want = FAKE_FILES[f][0]
            assert len([i for i in ids if i.startswith(f + "::")]) == want, f"{f} was split"
    assert set(owner) == set(PACKED_4)


def test_packing_is_largest_first_onto_the_least_loaded_shard_ties_to_the_lowest(fake_root):
    for n, want in ((4, PACKED_4), (2, PACKED_2)):
        got: dict[str, int] = {}
        for k in range(1, n + 1):
            for f in _files_of(_collect(fake_root, k, n)):
                got[f] = k
        assert got == want, f"{n} shards: {got}"


def test_the_partition_is_deterministic(fake_root, tmp_path):
    runs = []
    for seed in ("0", "1", "2"):
        run = {k: _collect(fake_root, k, hashseed=seed) for k in range(1, SHARDS + 1)}
        runs.append(run)
    assert runs[0] == runs[1] == runs[2]
    assert any(runs[0].values()), "nothing was selected at all"


def test_unselected_cases_are_reported_deselected_not_skipped(fake_root):
    res = _run_pytest(fake_root, "-m", "docker", "tests", "-rs", shard="1/4")
    out = res.stdout + res.stderr
    assert res.returncode == 0, out
    assert "deselected" in out, out
    assert "skipped" not in out, out
    assert re.search(r"\b5 passed, \d+ deselected\b", out), out  # shard 1 is test_a.py: 5 cases


def test_more_shards_than_files_leaves_the_extra_shards_empty_and_still_covers_all(fake_root):
    seen: list[str] = []
    for k in range(1, 9):
        seen += _collect(fake_root, k, 8)
    assert sorted(seen) == _expected_docker_ids()


@pytest.mark.parametrize("value", [
    "0/4", "5/4", "a/4", "4", "1/0", "", "1/4/4", "-1/4", "1.5/4", "2/-4", "/4", "1/", " ",
    "1-4", "x",
])
def test_a_malformed_value_fails_pytest_and_names_the_variable(fake_root, value):
    res = _run_pytest(fake_root, "-m", "docker", "tests", shard=value)
    out = res.stdout + res.stderr
    assert res.returncode != 0, f"{value!r} was accepted:\n{out}"
    assert "LF_DOCKER_SHARD" in out, out
    assert "passed" not in out, f"tests ran with {value!r}:\n{out}"


def test_a_valid_value_with_k_equal_n_is_accepted(fake_root):
    res = _run_pytest(fake_root, "--collect-only", "-m", "docker", "tests", shard="1/1")
    assert res.returncode == 0, res.stdout + res.stderr
    assert _collected(res) == _expected_docker_ids()


def test_the_manifest_lists_every_selected_file_with_its_shard(fake_root, tmp_path):
    manifests = {}
    for k in (2, 4):
        target = tmp_path / f"m{k}.json"
        _collect(fake_root, k, manifest=target)
        manifests[k] = json.loads(target.read_text(encoding="utf-8"))
    assert manifests[2]["shard"] == 2 and manifests[4]["shard"] == 4
    for data in manifests.values():
        assert data["shards"] == SHARDS
        assert data["files"] == PACKED_4, data["files"]  # all shards' files, forward slashes
        assert set(data) == {"shard", "shards", "files"}
        assert all(isinstance(v, int) for v in data["files"].values())
    assert "tests/acceptance/test_f.py" not in manifests[2]["files"]


def test_the_manifest_is_written_even_when_pytest_runs_the_tests(fake_root, tmp_path):
    target = tmp_path / "run.json"
    res = _run_pytest(fake_root, "-m", "docker", "tests", shard="3/4", manifest=target)
    assert res.returncode == 0, res.stdout + res.stderr
    assert json.loads(target.read_text(encoding="utf-8"))["files"] == PACKED_4


def test_no_manifest_is_written_when_the_variable_is_not_set(fake_root):
    _collect(fake_root, 1)
    assert not list(fake_root.glob("*.json"))


# ---------------------------------------------------------------------------- gate script

# the tree the gate reads: <dir>/docker-shard-<k>/ holding docker-acceptance.xml and
# docker-shard-manifest.json
G_FILES = {"tests/acceptance/test_a.py": 1, "tests/acceptance/test_b.py": 2,
           "tests/acceptance/test_c.py": 3, "tests/acceptance/sub/test_e.py": 4}


def _classname(path: str) -> str:
    return path[:-3].replace("/", ".")


def _case(path: str, name: str, outcome: str = "pass", style: str = "classname") -> str:
    cls = _classname(path) + (".TestX" if style == "class" else "")
    attrs = f'classname="{cls}" name="{name}" time="0.01"'
    if style == "file":
        attrs += f' file="{path}"'
    inner = {"pass": "", "skip": '<skipped type="pytest.skip" message="s"/>',
             "fail": '<failure message="boom">boom</failure>',
             "error": '<error message="boom">boom</error>'}[outcome]
    return f"<testcase {attrs}>{inner}</testcase>"


def _report(cases: list[str]) -> str:
    def n(tag: str) -> int:
        return sum(tag in c for c in cases)
    return (f'<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest" '
            f'errors="{n("<error ")}" failures="{n("<failure ")}" skipped="{n("<skipped ")}" '
            f'tests="{len(cases)}" time="1.0">{"".join(cases)}</testsuite></testsuites>')


def _write_tree(root: Path, files: dict[str, int], reports: dict[int, list[str]],
                manifests: dict[int, dict] | None = None, shards: int = 4) -> Path:
    out = root / "artifacts"
    for k in sorted(set(reports) | set(manifests or {})):
        d = out / f"docker-shard-{k}"
        d.mkdir(parents=True)
        if k in reports:
            (d / "docker-acceptance.xml").write_text(_report(reports[k]), encoding="utf-8")
        man = (manifests or {}).get(k, {"shard": k, "shards": shards, "files": files})
        (d / "docker-shard-manifest.json").write_text(json.dumps(man), encoding="utf-8")
    return out


def _good_reports(style: str = "classname") -> dict[int, list[str]]:
    return {k: [_case(f, f"test_x[{i}]", style=style) for i in range(2)
                for f, s in G_FILES.items() if s == k] for k in range(1, 5)}


def _gate(tree: Path):
    return subprocess.run([sys.executable, "-I", str(GATE), str(tree)], capture_output=True,
                          text=True, timeout=60)


def _expect_fail(res, *words: str) -> None:
    out = res.stdout + res.stderr
    assert res.returncode == 1, f"want exit 1, got {res.returncode}:\n{out}"
    assert re.search(r"^FAIL: ", res.stdout, re.M), out
    for w in words:
        assert w.lower() in out.lower(), f"{w!r} not in:\n{out}"


def test_the_gate_script_exists():
    assert GATE.is_file(), "scripts/ci/docker_shards_gate.py is missing"


@pytest.mark.parametrize("style", ["classname", "class", "file"])
def test_gate_passes_four_clean_shards_that_cover_their_files(tmp_path, style):
    tree = _write_tree(tmp_path, G_FILES, _good_reports(style))
    res = _gate(tree)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "FAIL" not in res.stdout


def test_gate_finds_the_shard_folders_at_any_depth(tmp_path):
    tree = _write_tree(tmp_path, G_FILES, _good_reports())
    nested = tmp_path / "wrap" / "deeper"
    nested.parent.mkdir()
    tree.rename(nested)
    assert _gate(nested).returncode == 0


def test_gate_fails_when_a_shard_is_missing(tmp_path):
    reports = _good_reports()
    del reports[3]
    _expect_fail(_gate(_write_tree(tmp_path, G_FILES, reports)), "shard")


def test_gate_fails_when_a_shard_report_is_missing_but_its_manifest_is_there(tmp_path):
    reports = _good_reports()
    del reports[2]
    manifests = {k: {"shard": k, "shards": 4, "files": G_FILES} for k in range(1, 5)}
    tree = _write_tree(tmp_path, G_FILES, reports, manifests=manifests)
    _expect_fail(_gate(tree))


def test_gate_fails_when_a_shard_ran_no_tests(tmp_path):
    reports = _good_reports()
    reports[4] = []
    _expect_fail(_gate(_write_tree(tmp_path, G_FILES, reports)))


def test_gate_fails_on_a_skipped_case(tmp_path):
    reports = _good_reports()
    reports[1][0] = _case("tests/acceptance/test_a.py", "test_x[0]", "skip")
    _expect_fail(_gate(_write_tree(tmp_path, G_FILES, reports)), "skipped")


def test_gate_fails_on_a_failed_case(tmp_path):
    reports = _good_reports()
    reports[2][1] = _case("tests/acceptance/test_b.py", "test_x[1]", "fail")
    _expect_fail(_gate(_write_tree(tmp_path, G_FILES, reports)), "failed")


def test_gate_fails_on_an_errored_case(tmp_path):
    reports = _good_reports()
    reports[3][0] = _case("tests/acceptance/test_c.py", "test_x[0]", "error")
    _expect_fail(_gate(_write_tree(tmp_path, G_FILES, reports)), "errored")


def test_gate_fails_when_a_file_shows_up_in_two_shards(tmp_path):
    reports = _good_reports()
    reports[2].append(_case("tests/acceptance/test_a.py", "test_x[9]"))  # a is shard 1's file
    _expect_fail(_gate(_write_tree(tmp_path, G_FILES, reports)), "test_a.py")


def test_gate_fails_when_a_manifest_file_is_in_no_report(tmp_path):
    files = dict(G_FILES, **{"tests/acceptance/test_g.py": 2})  # shard 2 owns b and g
    _expect_fail(_gate(_write_tree(tmp_path, files, _good_reports())), "test_g.py")


def test_gate_fails_on_a_report_file_that_its_manifest_does_not_assign(tmp_path):
    reports = _good_reports()
    reports[1].append(_case("tests/acceptance/test_z_new.py", "t"))
    _expect_fail(_gate(_write_tree(tmp_path, G_FILES, reports)), "test_z_new")


def test_gate_fails_when_the_manifests_disagree_on_the_files(tmp_path):
    other = dict(G_FILES, **{"tests/acceptance/test_c.py": 4})
    manifests = {k: {"shard": k, "shards": 4, "files": G_FILES} for k in range(1, 5)}
    manifests[3] = {"shard": 3, "shards": 4, "files": other}
    _expect_fail(_gate(_write_tree(tmp_path, G_FILES, _good_reports(), manifests)))


def test_gate_fails_when_the_manifests_disagree_on_the_shard_count(tmp_path):
    manifests = {k: {"shard": k, "shards": 4, "files": G_FILES} for k in range(1, 5)}
    manifests[2]["shards"] = 3
    _expect_fail(_gate(_write_tree(tmp_path, G_FILES, _good_reports(), manifests)))


def test_gate_fails_when_the_manifests_claim_more_shards_than_are_present(tmp_path):
    manifests = {k: {"shard": k, "shards": 5, "files": G_FILES} for k in range(1, 5)}
    _expect_fail(_gate(_write_tree(tmp_path, G_FILES, _good_reports(), manifests)), "shard")


def test_gate_fails_when_the_directory_holds_no_shards(tmp_path):
    empty = tmp_path / "none"
    empty.mkdir()
    _expect_fail(_gate(empty))
