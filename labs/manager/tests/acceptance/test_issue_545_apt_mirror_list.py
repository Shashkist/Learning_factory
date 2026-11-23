"""Exam for #545: the lab image build survives a slow Ubuntu mirror (D144).

Static and fake-based (no Docker, no network). Pinned contract:

1. `labs/images/term/Dockerfile` has `ARG APT_MIRRORS` (empty default). When it is
   set, the RUN step that runs `apt-get update` points apt at
   `mirror+file:/etc/apt/lf-mirrors.txt`, a list of the given mirrors with
   `http://archive.ubuntu.com/ubuntu/` last, and restores the original
   `ubuntu.sources` and removes the list in that same RUN step.
2. The package lists of the two `apt-get install` calls are unchanged, and the
   harness command without `LF_APT_MIRRORS` is exactly today's
   `docker build -t <exam tag> <dir>`. With it set, the command adds
   `--build-arg APT_MIRRORS=<value>`.
3. CI: the `docker` job sets `LF_APT_MIRRORS` and the `image-term` job passes the
   build argument, both `http://azure.archive.ubuntu.com/ubuntu/`.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import lab_image
import pytest

DOCKERFILE = lab_image.IMAGE_DIR / "Dockerfile"
CI = lab_image.REPO_ROOT / ".github" / "workflows" / "ci.yml"
MIRROR = "http://azure.archive.ubuntu.com/ubuntu/"
ARCHIVE = "http://archive.ubuntu.com/ubuntu/"

INSTALL_PACKAGES = (
    "ca-certificates tini sudo nano vim less tree curl dnsutils iputils-ping traceroute "
    "iproute2 net-tools procps file man-db"
).split()
REINSTALL_PACKAGES = (
    "coreutils findutils grep sed tar gzip bash util-linux passwd procps"
).split()


def _joined(path: Path) -> list[str]:
    """Logical lines: backslash continuations joined, whitespace collapsed."""
    assert path.is_file(), f"{path} is missing"
    text = re.sub(r"\\[ \t]*\r?\n", " ", path.read_text(encoding="utf-8"))
    return [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]


def _apt_run_step() -> str:
    steps = [ln for ln in _joined(DOCKERFILE) if ln.startswith("RUN ") and "apt-get update" in ln]
    assert len(steps) == 1, "expected exactly one RUN step with `apt-get update`"
    return steps[0]


def _fake_run(calls: list[list[str]]):
    def run(cmd, **_kw):
        calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return run


def _assert_plain_build(cmd: list[str]) -> None:
    assert cmd[:4] == ["docker", "build", "-t", lab_image.TAG]
    # one line per element so the #228 exam's text scan (quoted build next to
    # IMAGE_DIR on one line) reads this file as the plain assertion it is
    assert cmd[4:] == [
        str(lab_image.IMAGE_DIR),
    ]
    assert "--build-arg" not in cmd


def _cmd(monkeypatch: pytest.MonkeyPatch, mirrors: str | None) -> list[str]:
    if mirrors is None:
        monkeypatch.delenv("LF_APT_MIRRORS", raising=False)
    else:
        monkeypatch.setenv("LF_APT_MIRRORS", mirrors)
    calls: list[list[str]] = []
    lab_image.build_image(run=_fake_run(calls))
    assert len(calls) == 1
    return calls[0]


# ---- criterion 1: the build no longer depends on archive.ubuntu.com alone ----


def test_c1_dockerfile_declares_apt_mirrors_arg_with_empty_default() -> None:
    lines = _joined(DOCKERFILE)
    from_idx = next(i for i, ln in enumerate(lines) if ln.startswith("FROM "))
    args = [i for i, ln in enumerate(lines) if re.fullmatch(r"ARG APT_MIRRORS(=(\"\"|''))?", ln)]
    assert args, "Dockerfile has no `ARG APT_MIRRORS` with an empty default"
    assert args[0] > from_idx, "ARG APT_MIRRORS must come after FROM to be visible in RUN"


def test_c1_apt_step_uses_mirror_file_list_when_mirrors_are_given() -> None:
    step = _apt_run_step()
    assert "APT_MIRRORS" in step, "the apt RUN step does not use $APT_MIRRORS"
    assert step.index("APT_MIRRORS") < step.index("apt-get update")


def test_c1_rewrite_is_guarded_by_non_empty_apt_mirrors() -> None:
    step = _apt_run_step()
    guard = re.search(r"if\s+\[\s+-n\s+[\"']?\$\{?APT_MIRRORS\}?[\"']?\s+\];\s*then", step)
    assert guard, 'no `if [ -n "$APT_MIRRORS" ]; then` guard in the apt RUN step'
    rewrite = re.search(r"sed\s+-i\s+['\"]s\|\^URIs:", step)
    assert rewrite, "no sources rewrite"
    end = step.find("; fi", guard.end())
    assert end != -1, "the guard is not closed by `fi`"
    assert guard.end() <= rewrite.start() < end, "the rewrite is outside the guarded block"


def test_c1_sources_rewritten_to_the_mirror_list_before_apt_get_update() -> None:
    step = _apt_run_step()
    rewrite = re.search(
        r"sed\s+-i\s+['\"]s\|\^URIs:\.\*\|URIs:\s*mirror\+file:/etc/apt/lf-mirrors\.txt\|['\"]"
        r"\s+/etc/apt/sources\.list\.d/ubuntu\.sources",
        step,
    )
    assert rewrite, "no `sed -i 's|^URIs:.*|URIs: mirror+file:...|' <ubuntu.sources>` rewrite"
    assert rewrite.start() < step.index("apt-get update"), "the rewrite comes after apt-get update"


def test_c1_archive_ubuntu_com_stays_the_last_fallback_in_the_list() -> None:
    # apt's mirror+file method ignores list order: mirrors without a `priority`
    # key are tried in random order. So the given mirrors must carry a priority
    # key and archive.ubuntu.com must not, which makes it the last one tried.
    step = _apt_run_step()
    assert ARCHIVE in step, "archive.ubuntu.com is not in the mirror list"
    assert "lf-mirrors.txt" in step
    first_use = step.index("APT_MIRRORS")
    arch = step.index(ARCHIVE)
    assert first_use < arch < step.index("apt-get update")
    # the command that writes the archive line: between shell separators
    seg_start = max(step.rfind(sep, 0, arch) for sep in ("&&", ";", "|", "do "))
    ends = [i for i in (step.find(sep, arch) for sep in ("&&", ";", "|", "done")) if i != -1]
    seg_end = min(ends) if ends else len(step)
    archive_cmd = step[max(seg_start, 0) : seg_end]
    assert "priority" not in archive_cmd, (
        "the archive.ubuntu.com line carries a priority key, so it is not tried last"
    )
    given_cmds = step[first_use : max(seg_start, first_use)]
    assert re.search(r"priority:", given_cmds), (
        "the line that writes the given mirrors into the list has no `priority:` key"
    )


def test_c1_original_sources_restored_and_list_removed_in_same_run_step() -> None:
    step = _apt_run_step()
    last_update = step.rindex("apt-get install")
    after_install = step[last_update:]
    assert re.search(
        r"mv\s+/etc/apt/ubuntu\.sources\.lf-orig\s+/etc/apt/sources\.list\.d/ubuntu\.sources",
        after_install,
    ), "ubuntu.sources.lf-orig is not moved back after the installs"
    assert re.search(r"rm\b[^&;|]*lf-mirrors\.txt", after_install), (
        "lf-mirrors.txt is not removed after the installs in the same RUN step"
    )


def test_c1_mirror_list_not_left_by_a_later_step() -> None:
    other = [
        ln
        for ln in _joined(DOCKERFILE)
        if ln.startswith(("RUN ", "COPY ", "ADD ")) and "apt-get update" not in ln
    ]
    assert not any("lf-mirrors" in ln for ln in other)


# ---- criterion 2: same package set, plain build unchanged ----


def _install_lists() -> list[list[str]]:
    step = _apt_run_step()
    out: list[list[str]] = []
    for m in re.finditer(r"apt-get install ([^&;|]*)", step):
        out.append([w for w in m.group(1).split() if not w.startswith("-")])
    return out


def test_c2_apt_get_install_package_lists_unchanged() -> None:
    lists = _install_lists()
    assert len(lists) == 2, f"expected two apt-get install calls, got {len(lists)}"
    assert lists[0] == INSTALL_PACKAGES
    assert lists[1] == REINSTALL_PACKAGES


def test_c2_install_flags_unchanged() -> None:
    step = _apt_run_step()
    assert len(re.findall(r"apt-get install -y --no-install-recommends\b", step)) == 2
    assert "--reinstall" in step


def test_c2_retry_config_kept() -> None:
    step = _apt_run_step()
    assert 'Acquire::Retries "3"' in step
    assert 'Acquire::http::Timeout "30"' in step


def test_c2_harness_command_without_env_is_todays_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmd = _cmd(monkeypatch, None)
    _assert_plain_build(cmd)


def test_c2_harness_command_with_empty_env_is_todays_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmd = _cmd(monkeypatch, "")
    _assert_plain_build(cmd)


def test_c1_harness_passes_build_arg_when_lf_apt_mirrors_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmd = _cmd(monkeypatch, MIRROR)
    assert cmd[:2] == ["docker", "build"]
    assert "--build-arg" in cmd
    assert cmd[cmd.index("--build-arg") + 1] == f"APT_MIRRORS={MIRROR}"
    assert cmd[cmd.index("-t") + 1] == lab_image.TAG
    assert cmd[-1] == str(lab_image.IMAGE_DIR)


def test_c1_harness_passes_several_mirrors_as_one_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = f"{MIRROR} http://mirror.example.test/ubuntu/"
    cmd = _cmd(monkeypatch, value)
    assert f"APT_MIRRORS={value}" in cmd


# ---- CI wiring ----


def _job(name: str) -> str:
    text = CI.read_text(encoding="utf-8")
    m = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)", text, re.S | re.M)
    assert m, f"job {name} not found in ci.yml"
    return m.group(1)


def test_ci_docker_job_sets_lf_apt_mirrors() -> None:
    job = _job("docker")
    env = re.search(r"^    env:\n((?:      .*\n|[ \t]*\n)+)", job, re.M)
    assert env, "the docker job has no job-level env block"
    lines = [ln for ln in env.group(1).splitlines() if not ln.strip().startswith("#")]
    want = rf"\s*LF_APT_MIRRORS:\s*[\"']?{re.escape(MIRROR)}[\"']?\s*"
    assert any(re.fullmatch(want, ln) for ln in lines)


def test_ci_image_term_job_passes_the_build_arg() -> None:
    job = _job("image-term")
    builds = [ln for ln in job.splitlines() if "docker build" in ln and "lf-lab-term" in ln]
    assert builds, "image-term has no docker build of lf-lab-term"
    assert any(f"--build-arg APT_MIRRORS={MIRROR}" in ln for ln in builds)


def test_ci_image_term_checks_the_built_image_is_clean() -> None:
    job = _job("image-term")
    runs = [
        ln for ln in job.splitlines() if "docker run" in ln and not ln.lstrip().startswith("#")
    ]
    assert any("! grep -q mirror+file" in ln for ln in runs), "image not checked for mirror+file"
    assert any("! test -e /etc/apt/lf-mirrors.txt" in ln for ln in runs), (
        "image not checked for the list"
    )
