"""Acceptance tests for issue #670 (review follow-up of #668, #651): what counts as a pending
daemon change when the bootstrap reloads docker.

A `systemctl reload docker` applies the whole of daemon.json. The bootstrap refuses to reload when
the reload would also apply something nobody reviewed (#50, D187): the file is left unchanged,
docker is not reloaded, and the pending change is named. #670 widens "pending":

1. With `runsc` loaded, a foreign runtime that is loaded but whose file entry is edited (path or
   runtimeArgs) or removed is pending (write path and the #50 reload-only path).
2. The fresh path (no `runsc` in the file, `runsc` not loaded) also refuses a pending foreign
   runtime. Reloadable keys on the fresh path are NOT tested here: the #3, #56, #180 and #294
   exams start a fresh host from a file that holds `max-concurrent-uploads`,
   `max-concurrent-downloads` or `registry-mirrors` and expect the bootstrap to go ahead, so
   refusing them there needs a decision, not an exam (see the report of this exam).
3. RELOADABLE_KEYS covers every key that Docker's reload applies, for the pinned engine.
4. `runsc_matches_live` and `suid_matches_live` are one function that takes the runtime name.

Review round 1 of #711 and D198 (with D197 amendment 2) add:

5. `docker info` shows only the path and runtimeArgs of a loaded runtime. For any runtime other than
   `runsc`, a file entry key besides `path` and `runtimeArgs` (`options`, `runtimeType`) cannot be
   compared with the live side and counts as a difference (D198). So a foreign entry that carries
   `options` or `runtimeType` is refused, even when nothing in it was edited.
6. With no daemon.json on the host, a runtime that `docker info` lists (added by a dockerd flag,
   for example `nvidia`) would be dropped by the reload that follows the bootstrap's write: a
   missing file reads as `{}`, so the bootstrap refuses, creates no file and does not reload.
7. On the fresh path a loaded foreign runtime that is edited or removed in the file is refused too
   (D197 amendment 2), not only an unloaded one.

Pinned refusal form: the script reports on stdout (`log`) and refuses on stderr (`err`). The report
lines print every runtime and key name before any refusal, so the check looks at stderr only: the
refusal must say `Nothing was changed` and name the pending change there as a whole word: a runtime
as `runtime <name>`, a reloadable key by its name. The rest of the wording is free.

Pinned Docker version: the repository requires Engine 28.0.0 or newer (`labs/deploy/deploy.sh`,
`version_ge "$engine" 28.0.0`), so the key list is derived from moby tag v28.0.0:
- https://github.com/moby/moby/blob/v28.0.0/daemon/reload.go (`Daemon.Reload`: debug,
  max-concurrent-downloads, max-concurrent-uploads, max-download-attempts, shutdown-timeout,
  features, labels, insecure-registries, registry-mirrors, live-restore, network-diagnostic-port)
- https://github.com/moby/moby/blob/v28.0.0/daemon/reload_unix.go (`reloadPlatform`:
  default-runtime, runtimes, default-shm-size, default-ipc-mode, default-cgroupns-mode)
- https://github.com/moby/moby/blob/v28.0.0/cmd/dockerd/daemon.go (`reloadConfig`:
  authorization-plugins, applied after `Daemon.Reload`)
`runtimes` is the key the bootstrap itself manages and is not in the list. Keys the script lists
beyond these (allow-nondistributable-artifacts, metrics-addr, node-generic-resources, ...) may stay;
the exam asserts only that the sixteen verified keys are refused and that a key docker does not
reload (`log-opts`, `dns`) is not.

The harness is the one of the #651 exam (the real script under `bash`, server paths redirected into
a temp directory, stubs on PATH). Linux with bash and jq; no Docker is needed.
"""

import re

import pytest
from test_issue_651_runsc_suid import (
    LOADED,
    QUIET_KEYS,
    RESULT,
    SCRIPT,
    SERVER_RUNSC,
    SUID,
    SUID_ARG,
    _Harness,
    needs_linux,
)

# What `docker info` always lists besides the file's runtimes: they are not in daemon.json.
BUILTINS = {"runc": {"path": "runc"}, "io.containerd.runc.v2": {"path": "runc"}}
FOREIGN_LIVE = {"path": "/opt/exam/foreign", "runtimeArgs": ["--a"]}
SUID_ENTRY = {"path": SERVER_RUNSC["path"], "runtimeArgs": [*SERVER_RUNSC["runtimeArgs"], SUID_ARG]}


def _refusal_names(h: _Harness, named: str) -> None:
    """The refusal's own lines (stderr; the stdout report prints every name anyway) say that
    nothing was changed and name the pending change `named` as a whole word."""
    assert h.res is not None
    err = h.res.stderr
    assert "Nothing was changed" in err, "the refusal does not say so\n" + h.tail()
    assert re.search(rf"(?<![\w-]){re.escape(named)}(?![\w-])", err), \
        f"the refusal does not name {named!r}\n" + h.tail()


def _refused(h: _Harness, named: str) -> None:
    """Failed run, file byte-equal, docker not reloaded, nothing stray, the change named."""
    assert h.res.returncode != 0, "the bootstrap went ahead\n" + h.tail()
    assert h.last_line == f"{RESULT} failed", h.tail()
    assert h.daemon_json.read_bytes() == h.seed, "daemon.json was modified\n" + h.tail()
    assert h.reloads == [] and h.restarts == [], \
        f"docker was reloaded although a pending change would be applied: {h.systemctl_calls}"
    assert h.etc_files == ["daemon.json"], h.etc_files
    _refusal_names(h, named)


def _seed(start: str, foreign: "dict | None") -> dict:
    """`write`: runsc in the file and loaded, runsc-suid missing (the production start).
    `reload-only`: runsc-suid is in the file too, but not loaded (the #50 path)."""
    runtimes = {"runsc": SERVER_RUNSC}
    if start == "reload-only":
        runtimes[SUID] = SUID_ENTRY
    if foreign is not None:
        runtimes["foreign"] = foreign
    return {**QUIET_KEYS, "runtimes": runtimes}


LIVE_WITH_FOREIGN = {"runsc": SERVER_RUNSC, "foreign": FOREIGN_LIVE, **BUILTINS}


# ------------------------------------------------ 1: a loaded foreign runtime edited or removed


@needs_linux
@pytest.mark.parametrize("start", ["write", "reload-only"])
@pytest.mark.parametrize(
    "foreign",
    [
        {**FOREIGN_LIVE, "path": "/opt/exam/foreign-edited"},
        {**FOREIGN_LIVE, "runtimeArgs": ["--b"]},
        None,
    ],
    ids=["path-edited", "runtimeArgs-edited", "removed"],
)
def test_loaded_foreign_runtime_edited_or_removed_in_the_file_is_a_pending_change(
    tmp_path, start, foreign
):
    h = _Harness(tmp_path, _seed(start, foreign), live=LIVE_WITH_FOREIGN).run()
    _refused(h, "runtime foreign")
    assert h.live == LIVE_WITH_FOREIGN, f"the live runtimes changed: {h.live}"


@needs_linux
@pytest.mark.parametrize("start", ["write", "reload-only"])
@pytest.mark.parametrize(
    "extra",
    [
        {"options": {"TypeUrl": "io.containerd.exam.v1.Options", "ConfigPath": "/etc/exam.toml"}},
        {"runtimeType": "io.containerd.kata.v2"},
    ],
    ids=["options", "runtimeType"],
)
def test_loaded_foreign_runtime_with_options_or_runtimetype_is_a_pending_change(
    tmp_path, start, extra
):
    """`docker info` shows only path and runtimeArgs, so an `options` or `runtimeType` key of a
    foreign entry cannot be compared with the live side and counts as a difference (D198)."""
    h = _Harness(tmp_path, _seed(start, {**FOREIGN_LIVE, **extra}), live=LIVE_WITH_FOREIGN).run()
    _refused(h, "runtime foreign")
    assert h.live == LIVE_WITH_FOREIGN, f"the live runtimes changed: {h.live}"


# ------------------------------------------------ no daemon.json, a runtime from a dockerd flag


def _no_file_host(tmp_path, live: dict) -> _Harness:
    """A host without /etc/docker/daemon.json: the harness always seeds one, so remove it."""
    h = _Harness(tmp_path, {}, live=live)
    h.daemon_json.unlink()
    return h


@needs_linux
@pytest.mark.parametrize(
    "live",
    [
        {"nvidia": {"path": "nvidia-container-runtime"}, **BUILTINS},
        {"nvidia": {"path": "nvidia-container-runtime"}, "other": {"path": "/opt/o"}, **BUILTINS},
    ],
    ids=["nvidia", "two-flag-runtimes"],
)
def test_no_daemon_json_with_a_runtime_added_by_a_dockerd_flag_is_refused(tmp_path, live):
    """The reload after the write would drop the flag-added runtime (a file with `runtimes` wins
    over the flag). Refuse: no file is created, docker is not reloaded, the runtime is named."""
    h = _no_file_host(tmp_path, live).run()
    assert h.res.returncode != 0, "the bootstrap went ahead\n" + h.tail()
    assert h.last_line == f"{RESULT} failed", h.tail()
    assert not h.daemon_json.exists(), "a daemon.json was created\n" + h.tail()
    assert h.reloads == [] and h.restarts == [], h.systemctl_calls
    assert h.etc_files == [], h.etc_files
    assert h.live == live, f"the live runtimes changed: {h.live}"
    _refusal_names(h, "runtime nvidia")


@needs_linux
def test_no_daemon_json_with_only_docker_builtin_runtimes_still_goes_ahead(tmp_path):
    """Control: runc and io.containerd.runc.v2 are not flag-added runtimes."""
    h = _no_file_host(tmp_path, dict(BUILTINS)).run()
    assert h.res.returncode == 0, h.tail()
    assert h.last_line == f"{RESULT} changed", h.tail()
    assert len(h.reloads) == 1 and h.restarts == [], h.systemctl_calls
    assert {"runsc", SUID} <= set(h.daemon["runtimes"]), h.daemon


@needs_linux
def test_loaded_foreign_runtime_unchanged_in_the_file_is_not_pending(tmp_path):
    """Control: the file entry equals the loaded one, so the reload only adds runsc-suid."""
    h = _Harness(tmp_path, _seed("write", FOREIGN_LIVE), live=LIVE_WITH_FOREIGN).run()
    assert h.res.returncode == 0, h.tail()
    assert h.last_line == f"{RESULT} changed", h.tail()
    assert len(h.reloads) == 1 and h.restarts == [], h.systemctl_calls
    assert h.daemon["runtimes"]["foreign"] == FOREIGN_LIVE
    assert h.daemon["runtimes"][SUID] == SUID_ENTRY


@needs_linux
def test_docker_builtin_runtimes_missing_from_the_file_are_not_a_removed_runtime(tmp_path):
    """Control, the production shape: docker lists runc and io.containerd.runc.v2 although the
    file holds only runsc. They are not "removed" runtimes."""
    live = {"runsc": SERVER_RUNSC, **BUILTINS}
    h = _Harness(tmp_path, LOADED, live=live).run()
    assert h.res.returncode == 0, h.tail()
    assert h.last_line == f"{RESULT} changed", h.tail()
    assert len(h.reloads) == 1 and h.restarts == [], h.systemctl_calls
    assert h.daemon["runtimes"][SUID] == SUID_ENTRY


# ------------------------------------------------ 2: the fresh path


@needs_linux
def test_fresh_path_with_a_foreign_runtime_in_the_file_not_loaded_is_refused(tmp_path):
    """No runsc in the file, nothing live: the reload that registers runsc would also load the
    foreign runtime. Refuse, name it, leave file and docker alone."""
    seed = {**QUIET_KEYS, "runtimes": {"foreign": FOREIGN_LIVE}}
    h = _Harness(tmp_path, seed, live=dict(BUILTINS)).run()
    _refused(h, "runtime foreign")
    assert "runsc" not in h.live and SUID not in h.live, h.live


@needs_linux
@pytest.mark.parametrize(
    "foreign",
    [
        {**FOREIGN_LIVE, "path": "/opt/exam/foreign-edited"},
        {**FOREIGN_LIVE, "runtimeArgs": ["--b"]},
        {**FOREIGN_LIVE, "options": {"ConfigPath": "/etc/exam.toml"}},
        {**FOREIGN_LIVE, "runtimeType": "io.containerd.kata.v2"},
        None,
    ],
    ids=["path-edited", "runtimeArgs-edited", "options", "runtimeType", "removed"],
)
def test_fresh_path_with_a_loaded_foreign_runtime_edited_or_removed_in_the_file_is_refused(
    tmp_path, foreign
):
    """D197 amendment 2: no runsc in the file, not loaded; the foreign runtime IS loaded but the
    file edits or drops it, and the reload that registers runsc would apply that."""
    seed = {**QUIET_KEYS, "runtimes": {"foreign": foreign} if foreign else {}}
    live = {"foreign": FOREIGN_LIVE, **BUILTINS}
    h = _Harness(tmp_path, seed, live=live).run()
    _refused(h, "runtime foreign")
    assert h.live == live, f"the live runtimes changed: {h.live}"


@needs_linux
def test_fresh_path_with_a_loaded_runtime_and_no_runtimes_key_in_the_file_is_refused(tmp_path):
    """The file has no `runtimes` key but docker has `foreign` loaded (a dockerd flag): the
    bootstrap's write adds `runtimes`, so the reload would drop `foreign`."""
    live = {"foreign": FOREIGN_LIVE, **BUILTINS}
    h = _Harness(tmp_path, dict(QUIET_KEYS), live=live).run()
    _refused(h, "runtime foreign")
    assert h.live == live, f"the live runtimes changed: {h.live}"


@needs_linux
def test_fresh_path_with_a_loaded_foreign_runtime_unchanged_in_the_file_goes_ahead(tmp_path):
    """Control: the file entry equals the loaded one, so the reload only adds runsc."""
    seed = {**QUIET_KEYS, "runtimes": {"foreign": FOREIGN_LIVE}}
    h = _Harness(tmp_path, seed, live={"foreign": FOREIGN_LIVE, **BUILTINS}).run()
    assert h.res.returncode == 0, h.tail()
    assert h.last_line == f"{RESULT} changed", h.tail()
    assert len(h.reloads) == 1 and h.restarts == [], h.systemctl_calls
    assert h.daemon["runtimes"]["foreign"] == FOREIGN_LIVE


# ------------------------------------------------ 3: RELOADABLE_KEYS against moby v28.0.0

# key -> a plausible value; every key is applied by a reload in moby v28.0.0 (see the docstring).
DOCKER_RELOADED = {
    "debug": True,
    "labels": ["exam=1"],
    "live-restore": True,
    "max-concurrent-downloads": 7,
    "max-concurrent-uploads": 4,
    "max-download-attempts": 7,
    "shutdown-timeout": 30,
    "default-runtime": "runc",
    "insecure-registries": ["10.9.9.9:5000"],
    "registry-mirrors": ["https://mirror.invalid"],
    "authorization-plugins": ["exam-authz"],
    "features": {"buildkit": True},
    "network-diagnostic-port": 2000,
    "default-shm-size": "128M",
    "default-ipc-mode": "private",
    "default-cgroupns-mode": "private",
}


@needs_linux
@pytest.mark.parametrize("key", sorted(DOCKER_RELOADED))
def test_every_key_docker_reloads_counts_as_pending_on_the_write_path(tmp_path, key):
    seed = {**LOADED, key: DOCKER_RELOADED[key]}
    h = _Harness(tmp_path, seed, live={"runsc": SERVER_RUNSC, **BUILTINS}).run()
    _refused(h, key)


@needs_linux
@pytest.mark.parametrize("key", ["default-shm-size", "default-ipc-mode", "default-cgroupns-mode"])
def test_reloadable_platform_keys_are_pending_on_the_reload_only_path(tmp_path, key):
    seed = {**_seed("reload-only", None), key: DOCKER_RELOADED[key]}
    h = _Harness(tmp_path, seed, live={"runsc": SERVER_RUNSC, **BUILTINS}).run()
    _refused(h, key)


@needs_linux
@pytest.mark.parametrize("key,value", [("dns", ["10.0.0.2"]), ("log-opts", {"max-size": "10m"})])
def test_a_key_docker_does_not_reload_is_not_pending(tmp_path, key, value):
    """Control: refusing everything would be safe and useless."""
    seed = {**LOADED, key: value}
    h = _Harness(tmp_path, seed, live={"runsc": SERVER_RUNSC, **BUILTINS}).run()
    assert h.res.returncode == 0, h.tail()
    assert h.last_line == f"{RESULT} changed", h.tail()
    assert len(h.reloads) == 1, h.systemctl_calls


# ------------------------------------------------ 4: one live-match function


def test_runsc_and_runsc_suid_share_one_live_match_function():
    text = SCRIPT.read_text(encoding="utf-8")
    for old in ("runsc_matches_live", "suid_matches_live"):
        assert old not in text, f"{old} is still in bootstrap.sh (definition or call)"
    # One function, defined once, taking the runtime name as its argument.
    defs = re.findall(r"^(\w*matches_live\w*)\(\)", text, flags=re.M)
    assert len(defs) == 1, f"expected one live-match function, found: {defs}"
    name = defs[0]
    body = re.search(rf"^{name}\(\) \{{.*?^\}}", text, flags=re.M | re.S)
    assert body and "$1" in body.group(0), f"{name} does not take the runtime name ($1)"
    uses = re.findall(rf"\b{name}\s+(\S+)", text)
    assert len(uses) >= 2, f"{name} is not called for both runtimes: {uses}"
