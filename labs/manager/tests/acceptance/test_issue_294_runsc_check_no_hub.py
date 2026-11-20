"""Acceptance tests for issue #294 (D94): the bootstrap runsc check needs no Docker Hub.

Interface under test: `labs/deploy/bootstrap/bootstrap.sh`, step `runtime`. After the reload the
script starts a test container with runsc (`hello-world:latest`, container name `lf-runsc-check`).
Docker Hub answered an anonymous pull with `429 Too Many Requests` and failed every #3 case in CI.
D94: if `hello-world:latest` is present locally, run it with `--pull=never` (no pull at all); if it
is missing, pull `mirror.gcr.io/library/hello-world:latest` and tag it `hello-world:latest`; only
if that fails try Docker Hub; if every pull is refused, fail the step with one log line that names
the image and the registries tried. The retry-once rule for the first runsc start (#180) stays.

Harness: the same as the #180 exam (the real script under `bash`, server paths redirected into a
temp directory, stubs on PATH), with a `docker` stub that models an image store:
  * `$STUB_DIR/images` lists the images present locally (normalised: `hello-world` is
    `docker.io/library/hello-world:latest`);
  * `docker image inspect|inspect <ref>` exits 0 only for a present image; `docker images|image ls`
    prints an id only for a present image;
  * `docker [image] pull <ref>` (the last argument) is refused with a `toomanyrequests` message when
    the registry of the ref is set to `refuse` (STUB_MIRROR for mirror.gcr.io, STUB_HUB for
    anything else), else the image becomes present;
  * `docker [image] tag <src> <dst>` makes dst present when src is (fails when STUB_TAG is `fail`);
  * `docker run ... <image>` (the image is the last argument): with `--pull=never` and the image
    missing it fails (exit 125); without `--pull=never` (or with `--pull=always`) a missing image is
    pulled implicitly from Docker Hub and recorded in `implicit-pulls` (refused when STUB_HUB is
    `refuse`); then with `--runtime=runsc` it fails the first STUB_RUNSC_FAILS runs.
Every call is recorded in `docker.calls`. Nothing real is touched, no Docker is needed.

Needs Linux with `jq` and `bash`: skipped elsewhere, runs in the CI `manager` job.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
SCRIPT = REPO_ROOT / "labs" / "deploy" / "bootstrap" / "bootstrap.sh"
RESULT = "BOOTSTRAP RESULT:"
MIRROR_REF = "mirror.gcr.io/library/hello-world:latest"
HUB_REF = "docker.io/library/hello-world:latest"
OOM_MESSAGE = (
    "docker: Error response from daemon: failed to create task for container: failed to create "
    "shim task: OCI runtime start failed: write /proc/self/oom_score_adj: no such process: unknown"
)
HUB_NAMES = re.compile(r"docker\.io|docker hub|dockerhub", re.I)

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or not all(shutil.which(t) for t in ("bash", "jq")),
    reason="needs Linux with bash and jq",
)

DOCKER_STUB = r"""#!/bin/bash
echo "$*" >> "$STUB_DIR/docker.calls"
IMG="$STUB_DIR/images"; touch "$IMG"
norm() {
  local r="$1"
  case "$r" in */*) ;; *) r="docker.io/library/$r" ;; esac
  case "${r##*/}" in *:*) ;; *) r="$r:latest" ;; esac
  echo "$r"
}
have() { grep -qxF "$(norm "$1")" "$IMG"; }
refused() {
  case "$(norm "$1")" in
    mirror.gcr.io/*) [ "$STUB_MIRROR" = refuse ] ;;
    *) [ "$STUB_HUB" = refuse ] ;;
  esac
}
pull_ref() {
  if refused "$1"; then
    echo "Error response from daemon: toomanyrequests: rate limit reached" >&2
    return 1
  fi
  norm "$1" >> "$IMG"
}
last() { eval "echo \"\${$#}\""; }
listed() {
  for a in "$@"; do
    case "$a" in *hello-world*) have "${a#reference=}" && echo abc123def456 ;; esac
  done
}
case "$1" in
  info) cat "$STUB_DIR/live-runtimes" 2>/dev/null || echo '{}' ;;
  image)
    shift
    case "$1" in
      inspect) shift
        if have "$(last "$@")"; then echo '[{}]'; else echo "No such image" >&2; exit 1; fi ;;
      pull) shift; pull_ref "$(last "$@")" || exit 1 ;;
      tag) [ "$STUB_TAG" = fail ] && { echo "Error: tag failed" >&2; exit 1; }
        if have "$2"; then norm "$3" >> "$IMG"; fi ;;
      ls) listed "$@" ;;
    esac ;;
  inspect)
    if have "$(last "$@")"; then echo '[{}]'; else echo "No such object" >&2; exit 1; fi ;;
  images) listed "$@" ;;
  pull) shift; pull_ref "$(last "$@")" || exit 1 ;;
  tag) [ "$STUB_TAG" = fail ] && { echo "Error: tag failed" >&2; exit 1; }
    if have "$2"; then norm "$3" >> "$IMG"; fi ;;
  run)
    image="$(last "$@")"
    if ! have "$image" || [[ " $* " == *" --pull=always "* ]]; then
      if [[ " $* " == *" --pull=never "* ]]; then
        echo "docker: Error response from daemon: No such image: $image" >&2
        exit 125
      fi
      norm "$image" >> "$STUB_DIR/implicit-pulls"
      pull_ref "$image" || exit 125
    fi
    case "$*" in
      *--runtime=runsc*)
        n="$(grep -c -E '^run .*--runtime=runsc' "$STUB_DIR/docker.calls" || true)"
        if [ "$n" -le "$STUB_RUNSC_FAILS" ]; then
          echo "$STUB_OOM_MESSAGE" >&2
          exit 125
        fi
        echo "Hello from Docker!" ;;
    esac ;;
  network)
    case "$2" in
      inspect)
        case "$*" in
          *EnableIPv6*) echo "br-lf-labs false false 172.31.240.0/24 172.31.240.128/25" ;;
          *.Subnet*) echo "172.31.240.0/24 172.31.240.128/25" ;;
        esac ;;
    esac ;;
esac
exit 0
"""

SYSTEMCTL_STUB = r"""#!/bin/bash
echo "$*" >> "$STUB_DIR/systemctl.calls"
case "$1 $2" in
  "reload docker")
    jq -c '.runtimes // {}' "$STUB_DAEMON_JSON" > "$STUB_DIR/live-runtimes" 2>/dev/null \
      || echo '{}' > "$STUB_DIR/live-runtimes" ;;
esac
case "$1" in is-enabled) exit 1 ;; esac
exit 0
"""

STUBS = {
    "docker": DOCKER_STUB,
    "systemctl": SYSTEMCTL_STUB,
    "iptables": "#!/bin/bash\nexit 0\n",
    "runsc": "#!/bin/bash\n[ \"$1\" = --version ] && echo 'runsc version exam'\nexit 0\n",
    "dockerd": "#!/bin/bash\nexit 0\n",
    "id": '#!/bin/bash\n[ "$1" = -u ] && echo 0\nexit 0\n',
    "flock": "#!/bin/bash\nexit 0\n",
    "swapon": "#!/bin/bash\necho '/swapfile-exam file 4G 0B -2'\nexit 0\n",
    "pgrep": "#!/bin/bash\nexit 1\n",
}

SEED = {"max-concurrent-uploads": 4}


def _norm(ref: str) -> str:
    if "/" not in ref:
        ref = "docker.io/library/" + ref
    if ":" not in ref.rsplit("/", 1)[-1]:
        ref += ":latest"
    return ref


def _is_pull(call: str) -> bool:
    words = call.split()
    return words[:1] == ["pull"] or words[:2] == ["image", "pull"]


def _is_tag(call: str) -> bool:
    words = call.split()
    return words[:1] == ["tag"] or words[:2] == ["image", "tag"]


def _is_hello_latest(run_call: str) -> bool:
    return _norm(run_call.split()[-1]) == HUB_REF


class _Run:
    def __init__(
        self,
        tmp_path: Path,
        *,
        local: bool,
        mirror: str = "ok",
        hub: str = "ok",
        tag: str = "ok",
        runsc_fails: int = 0,
        dry: bool = False,
    ) -> None:
        self.dir = tmp_path
        bindir = tmp_path / "bin"
        stubdir = tmp_path / "stub"
        etc_docker = tmp_path / "etc" / "docker"
        for d in (
            bindir,
            stubdir,
            etc_docker,
            tmp_path / "sbin",
            tmp_path / "systemd",
            tmp_path / "usrbin",
        ):
            d.mkdir(parents=True)
        for name, text in STUBS.items():
            (bindir / name).write_text(text)
            (bindir / name).chmod(0o755)
        self.daemon_json = etc_docker / "daemon.json"
        self.seed = (json.dumps(SEED, indent=2) + "\n").encode()
        self.daemon_json.write_bytes(self.seed)
        (stubdir / "docker.calls").write_text("")
        (stubdir / "images").write_text(HUB_REF + "\n" if local else "")

        text = SCRIPT.read_text(encoding="utf-8")
        for server_path, local_path in (
            ("/etc/docker", etc_docker),
            ("/run/lf-bootstrap.lock", tmp_path / "bootstrap.lock"),
            ("/usr/local/sbin", tmp_path / "sbin"),
            ("/etc/systemd/system", tmp_path / "systemd"),
            ("/usr/local/bin", tmp_path / "usrbin"),
        ):
            text = text.replace(server_path, str(local_path))
        script = tmp_path / "bootstrap.sh"
        script.write_text(text)

        env = {
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "STUB_DIR": str(stubdir),
            "STUB_DAEMON_JSON": str(self.daemon_json),
            "STUB_RUNSC_FAILS": str(runsc_fails),
            "STUB_OOM_MESSAGE": OOM_MESSAGE,
            "STUB_MIRROR": mirror,
            "STUB_HUB": hub,
            "STUB_TAG": tag,
            "LF_BASELINE_URLS": "",
            "LF_BASELINE_TCP": "",
            "LF_SWAP_MIN_FREE_GB": "1",
        }
        if dry:
            env["DRY_RUN"] = "1"
        self.res = subprocess.run(
            ["bash", str(script)],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        self.out = self.res.stdout + self.res.stderr
        self.calls = (stubdir / "docker.calls").read_text().splitlines()
        ip = stubdir / "implicit-pulls"
        self.implicit_pulls = ip.read_text().splitlines() if ip.exists() else []
        self.images_after = (stubdir / "images").read_text().splitlines()
        self.last_line = next(
            (ln.strip() for ln in reversed(self.res.stdout.splitlines()) if ln.strip()), ""
        )

    @property
    def runsc_runs(self) -> list[str]:
        return [c for c in self.calls if c.startswith("run ") and "--runtime=runsc" in c]

    @property
    def pulls(self) -> list[str]:
        """Explicit pulls, as the normalised ref (the last argument), in order."""
        return [_norm(c.split()[-1]) for c in self.calls if _is_pull(c)]

    @property
    def tags(self) -> list[list[str]]:
        return [[_norm(w) for w in c.split()[-2:]] for c in self.calls if _is_tag(c)]

    def index(self, pred) -> int | None:
        return next((i for i, c in enumerate(self.calls) if pred(c)), None)

    def registry_lines(self) -> list[str]:
        """Log lines (not the echoed commands) naming the image and both registries."""
        return [
            ln
            for ln in self.out.splitlines()
            if not ln.startswith("+ ")
            and "hello-world" in ln
            and "mirror.gcr.io" in ln
            and HUB_NAMES.search(ln)
        ]

    def tail(self) -> str:
        return (
            "---- docker calls ----\n"
            + "\n".join(self.calls)
            + "\n---- script output (tail) ----\n"
            + self.out[-2500:]
        )


# One script run per scenario, shared by the cases of that scenario.
_SCENARIOS = {
    "local_registries_refuse": dict(local=True, mirror="refuse", hub="refuse"),
    "local_first_start_fails": dict(local=True, mirror="refuse", hub="refuse", runsc_fails=1),
    "local_runsc_broken": dict(local=True, mirror="refuse", hub="refuse", runsc_fails=2),
    "missing_mirror_ok": dict(local=False, mirror="ok", hub="refuse"),
    "missing_mirror_ok_first_start_fails": dict(
        local=False, mirror="ok", hub="refuse", runsc_fails=1
    ),
    "missing_mirror_refuses_hub_ok": dict(local=False, mirror="refuse", hub="ok"),
    "missing_all_refuse": dict(local=False, mirror="refuse", hub="refuse"),
    "missing_runsc_broken": dict(local=False, mirror="ok", hub="ok", runsc_fails=2),
    "missing_mirror_ok_tag_fails": dict(local=False, mirror="ok", hub="refuse", tag="fail"),
    "missing_dry_run": dict(local=False, mirror="ok", hub="ok", dry=True),
}


@pytest.fixture(scope="module")
def scenario(tmp_path_factory):
    cache: dict[str, _Run] = {}

    def get(name: str) -> _Run:
        if name not in cache:
            cache[name] = _Run(tmp_path_factory.mktemp(name), **_SCENARIOS[name])
        return cache[name]

    return get


def _precondition(r: _Run) -> None:
    assert "== step runtime: applying" in r.out, "precondition: runtime step ran\n" + r.tail()


# ---------------------------------------------------------------- image present locally


def test_local_image_registries_refuse_everything_the_step_succeeds(scenario):
    r = scenario("local_registries_refuse")
    _precondition(r)
    assert r.res.returncode == 0, "a refused registry failed a local-image check\n" + r.tail()
    assert r.last_line == f"{RESULT} changed", r.tail()
    assert len(r.runsc_runs) == 1, r.tail()


def test_local_image_makes_no_pull_at_all(scenario):
    r = scenario("local_registries_refuse")
    _precondition(r)
    assert r.pulls == [], f"the check pulled although the image is local: {r.pulls}\n" + r.tail()
    assert r.implicit_pulls == [], (
        f"the check started a container that would pull: {r.implicit_pulls}\n" + r.tail()
    )
    assert r.tags == [], f"nothing to tag when the image is local: {r.tags}"


def test_local_image_the_runsc_container_runs_hello_world_with_pull_never(scenario):
    r = scenario("local_registries_refuse")
    _precondition(r)
    assert len(r.runsc_runs) == 1, r.tail()
    run = r.runsc_runs[0]
    assert "lf-runsc-check" in run, run
    assert "--pull=never" in run.split(), f"the run may pull a missing image: {run}"
    assert _is_hello_latest(run), f"the check runs another image: {run}"


def test_local_image_first_start_fails_second_succeeds_both_starts_use_pull_never(scenario):
    r = scenario("local_first_start_fails")
    _precondition(r)
    assert r.res.returncode == 0, r.tail()
    assert len(r.runsc_runs) == 2, "expected the failed start and one retry\n" + r.tail()
    assert all("--pull=never" in c.split() for c in r.runsc_runs), r.runsc_runs
    assert r.pulls == [] and r.implicit_pulls == [], r.tail()


# ---------------------------------------------------------------- broken runsc still fails


def test_local_image_broken_runsc_still_fails_the_step_and_rolls_back(scenario):
    r = scenario("local_runsc_broken")
    _precondition(r)
    assert r.res.returncode != 0, "the step passed although runsc never started\n" + r.tail()
    assert r.last_line == f"{RESULT} failed", r.tail()
    assert len(r.runsc_runs) == 2, "one start and exactly one retry\n" + r.tail()
    assert "step 'runtime' failed; rolling it back" in r.out, r.tail()
    assert r.daemon_json.read_bytes() == r.seed, "daemon.json was not restored\n" + r.tail()
    assert "rm -f lf-runsc-check" in r.calls, r.tail()
    assert r.pulls == [] and r.implicit_pulls == [], r.tail()


def test_missing_image_broken_runsc_fails_the_step_without_blaming_the_registries(scenario):
    r = scenario("missing_runsc_broken")
    _precondition(r)
    assert r.res.returncode != 0, r.tail()
    assert r.last_line == f"{RESULT} failed", r.tail()
    assert len(r.runsc_runs) == 2, r.tail()
    assert r.registry_lines() == [], "a broken runtime was reported as a refused pull: " + repr(
        r.registry_lines()
    )
    assert "step 'runtime' failed; rolling it back" in r.out, r.tail()


# ---------------------------------------------------------------- image missing locally


def test_missing_image_is_pulled_from_the_mirror_first(scenario):
    r = scenario("missing_mirror_ok")
    _precondition(r)
    assert r.pulls, "no pull although the image is missing\n" + r.tail()
    assert r.pulls[0] == MIRROR_REF, f"the first pull is not the mirror: {r.pulls}"


def test_missing_image_mirror_works_docker_hub_is_never_contacted(scenario):
    r = scenario("missing_mirror_ok")
    _precondition(r)
    assert r.pulls == [MIRROR_REF], f"unexpected pulls: {r.pulls}\n" + r.tail()
    assert r.implicit_pulls == [], f"a run pulled from Docker Hub: {r.implicit_pulls}\n" + r.tail()


def test_missing_image_mirror_copy_is_tagged_hello_world_latest(scenario):
    r = scenario("missing_mirror_ok")
    _precondition(r)
    assert [MIRROR_REF, HUB_REF] in r.tags, f"no tag of the mirror image as hello-world: {r.tags}"
    assert HUB_REF in r.images_after, r.images_after


def test_missing_image_mirror_works_the_step_succeeds_and_runs_hello_world_after_the_pull(
    scenario,
):
    r = scenario("missing_mirror_ok")
    _precondition(r)
    assert r.res.returncode == 0, r.tail()
    assert r.last_line == f"{RESULT} changed", r.tail()
    assert len(r.runsc_runs) == 1, r.tail()
    assert _is_hello_latest(r.runsc_runs[0]), r.runsc_runs
    pull_i = r.index(lambda c: _is_pull(c) and _norm(c.split()[-1]) == MIRROR_REF)
    tag_i = r.index(_is_tag)
    run_i = r.index(lambda c: c.startswith("run ") and "--runtime=runsc" in c)
    assert pull_i is not None and tag_i is not None and run_i is not None, r.tail()
    assert pull_i < tag_i < run_i, f"order must be pull, tag, run: {pull_i} {tag_i} {run_i}"


def test_missing_image_first_start_fails_second_succeeds_the_pull_is_not_repeated(scenario):
    r = scenario("missing_mirror_ok_first_start_fails")
    _precondition(r)
    assert r.res.returncode == 0, r.tail()
    assert len(r.runsc_runs) == 2, "expected the failed start and one retry\n" + r.tail()
    assert r.pulls == [MIRROR_REF], f"the retry pulled again: {r.pulls}\n" + r.tail()
    assert r.implicit_pulls == [], r.tail()


def test_missing_image_mirror_refuses_docker_hub_is_tried_next(scenario):
    r = scenario("missing_mirror_refuses_hub_ok")
    _precondition(r)
    assert r.pulls[:1] == [MIRROR_REF], f"the mirror must be tried first: {r.pulls}\n" + r.tail()
    assert HUB_REF in r.pulls[1:], (
        f"Docker Hub was not tried by an explicit pull after the mirror refused: {r.pulls}\n"
        + r.tail()
    )


def test_missing_image_mirror_refuses_docker_hub_works_the_step_succeeds(scenario):
    r = scenario("missing_mirror_refuses_hub_ok")
    _precondition(r)
    assert r.res.returncode == 0, "the Docker Hub fallback did not save the step\n" + r.tail()
    assert r.last_line == f"{RESULT} changed", r.tail()
    assert len(r.runsc_runs) == 1 and _is_hello_latest(r.runsc_runs[0]), r.tail()
    assert HUB_REF in r.images_after, r.images_after


# ---------------------------------------------------------------- every pull refused


def test_missing_image_every_pull_refused_the_step_fails_and_rolls_back(scenario):
    r = scenario("missing_all_refuse")
    _precondition(r)
    assert r.res.returncode != 0, "the step passed with no image at all\n" + r.tail()
    assert r.last_line == f"{RESULT} failed", r.tail()
    assert f"{RESULT} changed" not in r.res.stdout, r.tail()
    assert "step 'runtime' failed; rolling it back" in r.out, r.tail()
    assert r.daemon_json.read_bytes() == r.seed, "daemon.json was not restored\n" + r.tail()
    assert "== step swap" not in r.out and "== step iptables" not in r.out, r.tail()
    assert r.runsc_runs == [], (
        "runsc was started although there was no image: a registry problem is blamed on runsc\n"
        + r.tail()
    )


def test_missing_image_every_pull_refused_both_registries_were_tried_mirror_first(scenario):
    r = scenario("missing_all_refuse")
    _precondition(r)
    assert r.pulls[:1] == [MIRROR_REF], f"{r.pulls}\n" + r.tail()
    assert HUB_REF in r.pulls[1:], f"Docker Hub was not tried: {r.pulls}\n" + r.tail()
    assert r.implicit_pulls == [], "a run without --pull=never was tried as a last resort: " + repr(
        r.implicit_pulls
    )


def test_missing_image_every_pull_refused_one_log_line_names_the_image_and_the_registries(
    scenario,
):
    r = scenario("missing_all_refuse")
    _precondition(r)
    lines = r.registry_lines()
    assert len(lines) == 1, (
        "expected exactly one log line naming hello-world, mirror.gcr.io and Docker Hub, "
        f"got {lines}\n" + r.tail()
    )


# ---------------------------------------------------------------- dry run


def test_dry_run_pulls_nothing_and_starts_nothing(scenario):
    r = scenario("missing_dry_run")
    assert r.res.returncode == 0, r.tail()
    assert r.pulls == [] and r.tags == [] and r.implicit_pulls == [], r.tail()
    assert r.runsc_runs == [], r.tail()
    assert r.images_after == [], r.images_after
    assert "== step runtime: applying" in r.out, (
        "the dry run never reached the runtime step\n" + r.tail()
    )
    assert r.last_line == f"{RESULT} dry run", r.tail()


# ---------------------------------------------------------------- #298: tag failure, dry-run plan


def test_missing_image_mirror_pull_works_but_tag_fails_the_step_fails_without_starting_runsc(
    scenario,
):
    r = scenario("missing_mirror_ok_tag_fails")
    _precondition(r)
    assert r.res.returncode != 0, (
        "the step passed although the image could not be tagged\n" + r.tail()
    )
    assert r.last_line == f"{RESULT} failed", r.tail()
    assert r.pulls[:1] == [MIRROR_REF], f"the mirror pull must have been made: {r.pulls}"
    assert r.tags, "the tag must have been attempted\n" + r.tail()
    assert r.runsc_runs == [], "runsc was started although the check image is missing\n" + r.tail()
    assert "step 'runtime' failed; rolling it back" in r.out, r.tail()


def test_missing_image_tag_fails_the_log_does_not_say_the_mirror_refused_the_pull(scenario):
    r = scenario("missing_mirror_ok_tag_fails")
    _precondition(r)
    log_lines = [ln for ln in r.out.splitlines() if not ln.startswith("+ ")]
    refused = [ln for ln in log_lines if re.search(r"refus", ln, re.I)]
    assert refused == [], f"the log blames a refused pull: {refused}\n" + r.tail()
    assert r.registry_lines() == [], r.registry_lines()


def test_missing_image_tag_fails_the_log_says_it_could_not_pull_or_tag_the_image(scenario):
    r = scenario("missing_mirror_ok_tag_fails")
    _precondition(r)
    lines = [
        ln
        for ln in r.out.splitlines()
        if not ln.startswith("+ ")
        and "hello-world" in ln
        and re.search(r"could not pull or tag", ln, re.I)
    ]
    assert lines, 'no log line with "could not pull or tag" naming hello-world\n' + r.tail()


def test_dry_run_on_a_host_without_the_image_shows_the_planned_pull_and_tag(scenario):
    r = scenario("missing_dry_run")
    pull = re.compile(r"^\+ docker (image )?pull mirror\.gcr\.io/library/hello-world\S*$", re.M)
    tag = re.compile(
        r"^\+ docker (image )?tag mirror\.gcr\.io/library/hello-world\S* hello-world\S*$", re.M
    )
    assert pull.search(r.out), "the dry run does not show the planned mirror pull\n" + r.tail()
    assert tag.search(r.out), "the dry run does not show the planned tag\n" + r.tail()
    assert pull.search(r.out).start() < tag.search(r.out).start(), (
        "the pull is planned after the tag"
    )


def test_dry_run_shows_the_plan_but_executes_no_pull_tag_or_run(scenario):
    r = scenario("missing_dry_run")
    assert any(c.startswith(("image inspect", "inspect ")) for c in r.calls), (
        "the dry run must still read the image store (read-only inspect)" + r.tail()
    )
    assert r.pulls == [] and r.tags == [], r.tail()
    assert not any(c.startswith("run ") for c in r.calls), r.tail()
    assert r.implicit_pulls == [] and r.images_after == [], r.tail()
