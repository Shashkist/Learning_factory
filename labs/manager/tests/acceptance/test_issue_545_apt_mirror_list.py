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


def _todays_command() -> list[str]:
    head = ["docker", "build", "-t", lab_image.TAG]
    return [*head, str(lab_image.IMAGE_DIR)]


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
    assert "mirror+file:/etc/apt/lf-mirrors.txt" in step
    assert "/etc/apt/sources.list.d/ubuntu.sources" in step
    assert step.index("APT_MIRRORS") < step.index("apt-get update")


def test_c1_archive_ubuntu_com_stays_the_last_fallback_in_the_list() -> None:
    step = _apt_run_step()
    assert ARCHIVE in step, "archive.ubuntu.com is not in the mirror list"
    assert "lf-mirrors.txt" in step
    # the given mirrors are written before the archive fallback
    assert step.index("APT_MIRRORS") < step.index(ARCHIVE) < step.index("apt-get update")


def test_c1_original_sources_restored_and_list_removed_in_same_run_step() -> None:
    step = _apt_run_step()
    last_update = step.rindex("apt-get install")
    after_install = step[last_update:]
    assert "ubuntu.sources" in after_install, "ubuntu.sources is not restored after the installs"
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
    assert cmd == _todays_command()


def test_c2_harness_command_with_empty_env_is_todays_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmd = _cmd(monkeypatch, "")
    assert cmd == _todays_command()


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
    assert re.search(rf"LF_APT_MIRRORS:\s*[\"']?{re.escape(MIRROR)}", _job("docker"))


def test_ci_image_term_job_passes_the_build_arg() -> None:
    job = _job("image-term")
    builds = [ln for ln in job.splitlines() if "docker build" in ln and "lf-lab-term" in ln]
    assert builds, "image-term has no docker build of lf-lab-term"
    assert any(f"--build-arg APT_MIRRORS={MIRROR}" in ln for ln in builds)
