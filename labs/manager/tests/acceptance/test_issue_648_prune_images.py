# ruff: noqa: E501
"""Acceptance tests for issue #648 (part of #85, D34): after a successful deploy, deploy.sh removes
our old images and nothing else. Runs labs/deploy/deploy.sh against a fake `docker` (no Docker).

The harness (fake docker for compose/inspect/exec, fake curl, temp LF_DEPLOY_ROOT) is the one of the
#13 exam; this file only adds a fake image store in front of it.

Listing contract of the fake (an implementation may use any of these shapes):
  * `docker images` / `docker image ls` / `docker image list`, with optional
    `-a`, `-q`/`--quiet`, `--no-trunc`, `--digests`,
    `--filter dangling=true|false`, `--filter reference=<glob>` (matched against the repository, or
    `repository:tag`; several filters are ANDed),
    `--format '<Go template>'` using {{.Repository}}, {{.Tag}}, {{.ID}}, {{.Digest}}, or `{{json .}}`.
    With no --format a plain table with a header line is printed.
  * A dangling image is listed with Tag `<none>`; our dangling images keep their Repository
    (for example ghcr.io/shashkist/lf-lab-term) and have a Digest. A dangling foreign image has its
    own repository, and one more has Repository `<none>` (owner unknown: it must stay).
Removal contract:
  * `docker rmi [opts] <ref>...` / `docker image rm|remove`: a ref is `repository:tag`,
    `repository@digest`, or an image ID (any prefix of 6+ hex characters).
  * `docker image prune [--filter reference=<glob>]` removes dangling images (only those matching
    the reference filter when one is given); `docker system prune` removes every dangling image.
  * Every attempt is recorded; the tests assert on the images that really went away, on the images
    that were never named, and on the command lines (no global prune, no force).
The fake fails a removal when the image's `repo:tag` or ID is listed in STUB_RMI_FAIL (as a running
lab container would make it fail).
"""
import importlib.util
import re
from pathlib import Path

import pytest

_BASE_PATH = Path(__file__).with_name("test_issue_13_deploy.py")
_spec = importlib.util.spec_from_file_location("lf_exam_issue_13_deploy_harness", _BASE_PATH)
assert _spec and _spec.loader and _BASE_PATH.is_file(), "the #13 deploy exam (harness) is missing"
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)

NEW_TAG = _base.NEW_TAG
OLD_TAG = _base.OLD_TAG
OLDER_1 = "c" * 40
OLDER_2 = "d" * 40
MANAGER = "ghcr.io/shashkist/lf-manager"
LAB_TERM = "ghcr.io/shashkist/lf-lab-term"
_stdin = _base._stdin

IMAGES_STUB = r"""#!/usr/bin/env bash
# fake docker: an image store in front of the #13 stub (docker-base)
S="${STUB_STATE:?}"
here="$(cd "$(dirname "$0")" && pwd)"
IMG="$S/images.tsv"
w1=""; w2=""
for a in "$@"; do
  case "$a" in -*) continue ;; esac
  if [ -z "$w1" ]; then w1="$a"; elif [ -z "$w2" ]; then w2="$a"; break; fi
done
verb=""
case "$w1" in
  images) verb=list ;;
  rmi) verb=rm ;;
  image) case "$w2" in ls|list) verb=list ;; rm|remove) verb=rm ;; prune) verb=prune ;; esac ;;
  system) [ "$w2" = prune ] && verb=sysprune ;;
esac
if [ -z "$verb" ]; then exec "$here/docker-base" "$@"; fi
printf 'docker %s\n' "$*" >> "$S/calls.log"
[ -f "$IMG" ] || : > "$IMG"
TAB=$'\t'

matches() { # matches <repo> <tag> <filter...>
  local repo=$1 tag=$2 f; shift 2
  for f in "$@"; do
    case "$f" in
      dangling=true) [ "$tag" = "<none>" ] || return 1 ;;
      dangling=false) [ "$tag" != "<none>" ] || return 1 ;;
      reference=*) local p=${f#reference=}
        # shellcheck disable=SC2053
        [[ "$repo" == $p || "$repo:$tag" == $p ]] || return 1 ;;
    esac
  done
  return 0
}

filters=(); fmt=""; quiet=0; force=0
args=("$@")
for ((i=0; i<${#args[@]}; i++)); do
  a="${args[$i]}"
  case "$a" in
    --filter|-f) j=$((i+1)); v="${args[$j]}"
      # `-f` is --force for rmi / prune, a filter only for the listing
      if [ "$verb" = list ] || [ "$a" = --filter ]; then filters+=("$v"); i=$j; else force=1; fi ;;
    --filter=*) filters+=("${a#--filter=}") ;;
    --format) i=$((i+1)); fmt="${args[$i]}" ;;
    --format=*) fmt="${a#--format=}" ;;
    -q|--quiet) quiet=1 ;;
    --force|-f) force=1 ;;
  esac
done

case "$verb" in
  list)
    [ -n "$fmt" ] || [ "$quiet" = 1 ] || printf 'REPOSITORY\tTAG\tIMAGE ID\n'
    while IFS=$'\t' read -r repo tag id digest; do
      [ -n "$repo" ] || continue
      matches "$repo" "$tag" "${filters[@]}" || continue
      if [ "$quiet" = 1 ]; then echo "$id"
      elif [ -z "$fmt" ]; then printf '%s\t%s\t%s\n' "$repo" "$tag" "$id"
      elif [ "$fmt" = '{{json .}}' ]; then
        printf '{"Repository":"%s","Tag":"%s","ID":"%s","Digest":"%s"}\n' "$repo" "$tag" "$id" "$digest"
      else
        out=${fmt#table }
        out=${out//'{{.Repository}}'/$repo}; out=${out//'{{.Tag}}'/$tag}
        out=${out//'{{.ID}}'/$id}; out=${out//'{{.Digest}}'/$digest}
        printf '%s\n' "$out"
      fi
    done < "$IMG"
    exit 0 ;;
  prune|sysprune)
    printf 'PRUNE %s\n' "$*" >> "$S/removed.log"
    tmp="$S/images.new"; : > "$tmp"
    while IFS=$'\t' read -r repo tag id digest; do
      [ -n "$repo" ] || continue
      if [ "$tag" = "<none>" ] && { [ "$verb" = sysprune ] || matches "$repo" "$tag" "${filters[@]}"; }; then
        printf 'REMOVED\t%s:%s\t%s\n' "$repo" "$tag" "$id" >> "$S/removed.log"
      else
        printf '%s\t%s\t%s\t%s\n' "$repo" "$tag" "$id" "$digest" >> "$tmp"
      fi
    done < "$IMG"
    mv "$tmp" "$IMG"; exit 0 ;;
  rm)
    rc=0
    for ref in "$@"; do
      case "$ref" in -*|rmi|image|rm|remove) continue ;; esac
      found=""
      while IFS=$'\t' read -r repo tag id digest; do
        [ -n "$repo" ] || continue
        if [ "$repo:$tag" = "$ref" ] || [ "$id" = "$ref" ] || [ "sha256:$id" = "$ref" ] \
           || { [ -n "$digest" ] && [ "$repo@$digest" = "$ref" ]; } \
           || { [ "${#ref}" -ge 6 ] && [[ "$ref" =~ ^[0-9a-f]+$ ]] && [ "${id:0:${#ref}}" = "$ref" ]; }; then
          found="$repo:$tag$TAB$id"; break
        fi
      done < "$IMG"
      if [ -z "$found" ]; then
        echo "Error response from daemon: No such image: $ref" >&2; rc=1
        printf 'MISSING\t%s\n' "$ref" >> "$S/removed.log"; continue
      fi
      rt=${found%%$TAB*}; rid=${found#*$TAB}
      if [ "$force" = 0 ] && { [[ " ${STUB_RMI_FAIL:-} " == *" $rt "* ]] || [[ " ${STUB_RMI_FAIL:-} " == *" $rid "* ]]; }; then
        echo "Error response from daemon: conflict: unable to delete $rid (cannot be forced) - image is being used by running container abc123" >&2
        printf 'FAILED\t%s\t%s\n' "$rt" "$rid" >> "$S/removed.log"; rc=1; continue
      fi
      grep -vF "$rid" "$IMG" > "$S/images.new" || true
      # keep the other entries that do not share the id
      mv "$S/images.new" "$IMG"
      printf 'REMOVED\t%s\t%s\n' "$rt" "$rid" >> "$S/removed.log"
      echo "Untagged: $rt"
    done
    exit $rc ;;
esac
"""


class PruneServer(_base.Server):
    def __init__(self, tmp: Path):
        super().__init__(tmp)
        (self.bin / "docker").replace(self.bin / "docker-base")
        _base._write_exec(self.bin / "docker", IMAGES_STUB)

    def set_images(self, rows: list[tuple[str, str, str]]) -> None:
        lines = []
        for repo, tag, iid in rows:
            digest = f"sha256:{iid * 5}" if tag == "<none>" and repo != "<none>" else ""
            lines.append("\t".join((repo, tag, iid, digest)))
        (self.state / "images.tsv").write_text("\n".join(lines) + "\n", newline="\n")

    def image_refs(self) -> set[str]:
        f = self.state / "images.tsv"
        out = set()
        for line in f.read_text().splitlines() if f.exists() else []:
            repo, tag, _iid, *_ = line.split("\t")
            out.add(f"{repo}:{tag}")
        return out

    def _log(self) -> list[list[str]]:
        f = self.state / "removed.log"
        return [ln.split("\t") for ln in f.read_text().splitlines()] if f.exists() else []

    def removed(self) -> list[str]:
        """repo:tag of every image that really went away"""
        return [r[1] for r in self._log() if r[0] == "REMOVED"]

    def attempted(self) -> list[str]:
        """repo:tag of every image a removal was attempted for (also the failed ones)"""
        return [r[1] for r in self._log() if r[0] in ("REMOVED", "FAILED")]

    def removal_calls(self) -> list[str]:
        return [c for c in self.calls_of("docker") if re.search(r"\b(rmi|rm|remove|prune)\b", c)
                and not c.startswith("docker compose")]


@pytest.fixture
def srv(tmp_path) -> PruneServer:
    return PruneServer(tmp_path)


def _image_set(*, manager_tags=(NEW_TAG, OLD_TAG, OLDER_1, OLDER_2)) -> list[tuple[str, str, str]]:
    rows = [(MANAGER, t, f"{i + 1:02x}{i + 1:02x}a1b2c3d4"[:12]) for i, t in enumerate(manager_tags)]
    rows += [
        (LAB_TERM, "latest", "aa11bb22cc33"),
        (LAB_TERM, "<none>", "dd44ee55ff66"),                 # ours, dangling
        ("ghcr.io/shashkist/lf-lab-code", "latest", "ab12cd34ef56"),
        ("n8nio/n8n", "latest", "f00d00000001"),               # foreign
        ("docker.io/library/foreign-app", "<none>", "f00d00000002"),  # foreign, dangling
        ("<none>", "<none>", "f00d00000003"),                  # owner unknown, dangling
        ("ghcr.io/shashkistx/lf-manager", "old", "f00d00000004"),   # look-alike prefix
        ("registry.example.invalid/lf-manager", "old", "f00d00000005"),
    ]
    return rows


FOREIGN = {
    "n8nio/n8n:latest",
    "docker.io/library/foreign-app:<none>",
    "<none>:<none>",
    "ghcr.io/shashkistx/lf-manager:old",
    "registry.example.invalid/lf-manager:old",
}


def _deploy(srv: PruneServer, *, prev: str | None = OLD_TAG, images=None, **extra):
    if prev is not None:
        (srv.root / "deployed-tag").write_text(prev + "\n")
    srv.set_images(images if images is not None else _image_set())
    return srv.run(_stdin(), **extra)


def _ok(r) -> None:
    assert r.returncode == 0, f"exit {r.returncode}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}"


def _last_line(r) -> str:
    return [ln for ln in r.stdout.splitlines() if ln.strip()][-1].strip()


# ---------------------------------------------------------------- criterion 1: old manager tags


def test_current_previous_and_two_older_manager_tags_remove_only_the_two_older(srv):
    r = _deploy(srv)
    _ok(r)
    assert {f"{MANAGER}:{OLDER_1}", f"{MANAGER}:{OLDER_2}"} <= set(srv.removed()), srv.removed()
    left = srv.image_refs()
    assert f"{MANAGER}:{NEW_TAG}" in left, "the deployed tag must stay"
    assert f"{MANAGER}:{OLD_TAG}" in left, "the previous tag (rollback target) must stay"
    assert f"{MANAGER}:{OLDER_1}" not in left and f"{MANAGER}:{OLDER_2}" not in left


def test_no_manager_tag_other_than_the_older_ones_is_removed(srv):
    _ok(_deploy(srv))
    manager_removed = [x for x in srv.removed() if x.startswith(MANAGER + ":")]
    assert sorted(manager_removed) == sorted([f"{MANAGER}:{OLDER_1}", f"{MANAGER}:{OLDER_2}"])


def test_the_previous_tag_is_read_from_deployed_tag_before_the_deploy(srv):
    # the rollback target is whatever deployed-tag said, not a fixed sha
    other_prev = "e" * 40
    images = _image_set(manager_tags=(NEW_TAG, other_prev, OLD_TAG, OLDER_1))
    _ok(_deploy(srv, prev=other_prev, images=images))
    left = srv.image_refs()
    assert f"{MANAGER}:{other_prev}" in left and f"{MANAGER}:{NEW_TAG}" in left
    assert f"{MANAGER}:{OLD_TAG}" not in left and f"{MANAGER}:{OLDER_1}" not in left


def test_first_deploy_without_a_previous_tag_keeps_only_the_current_tag(srv):
    r = _deploy(srv, prev=None)
    _ok(r)
    left = srv.image_refs()
    assert f"{MANAGER}:{NEW_TAG}" in left
    for t in (OLD_TAG, OLDER_1, OLDER_2):
        assert f"{MANAGER}:{t}" not in left, f"{t[:1]}... should be pruned on a first deploy"
    assert (srv.root / "deployed-tag").read_text().strip() == NEW_TAG


def test_redeploying_the_same_tag_keeps_it_and_removes_the_older_ones(srv):
    images = _image_set(manager_tags=(NEW_TAG, OLDER_1, OLDER_2))
    r = _deploy(srv, prev=NEW_TAG, images=images)
    _ok(r)
    left = srv.image_refs()
    assert f"{MANAGER}:{NEW_TAG}" in left
    assert f"{MANAGER}:{OLDER_1}" not in left and f"{MANAGER}:{OLDER_2}" not in left


# ---------------------------------------------------------------- criterion 2: dangling lab images


def test_a_dangling_lab_image_of_ours_is_removed(srv):
    _ok(_deploy(srv))
    assert f"{LAB_TERM}:<none>" in srv.removed(), srv.removed()
    assert f"{LAB_TERM}:<none>" not in srv.image_refs()


def test_the_current_lab_image_and_other_tagged_images_of_ours_stay(srv):
    _ok(_deploy(srv))
    left = srv.image_refs()
    assert f"{LAB_TERM}:latest" in left, "lf-lab-term:latest is what labs start from"
    assert "ghcr.io/shashkist/lf-lab-code:latest" in left


# ---------------------------------------------------------------- criterion 3: nothing foreign, no global prune


def test_foreign_images_are_never_removed_even_when_dangling(srv):
    _ok(_deploy(srv))
    left = srv.image_refs()
    for ref in FOREIGN:
        assert ref in left, f"{ref} is not ours and must still be there"
    assert not FOREIGN & set(srv.attempted()), srv.attempted()


def test_every_removed_image_is_under_ghcr_shashkist(srv):
    _ok(_deploy(srv))
    assert srv.removed(), "nothing was pruned at all"
    assert all(x.startswith("ghcr.io/shashkist/") for x in srv.attempted()), srv.attempted()


def test_foreign_images_are_not_even_named_on_a_removal_command_line(srv):
    _ok(_deploy(srv))
    for c in srv.removal_calls():
        for needle in ("n8n", "foreign-app", "f00d0000", "shashkistx", "example.invalid"):
            assert needle not in c, c


def test_no_system_prune_and_no_unfiltered_image_prune(srv):
    _ok(_deploy(srv))
    for c in srv.calls_of("docker"):
        assert not re.search(r"\bsystem\s+prune\b", c), c
        assert not re.search(r"\b(container|volume|network|builder)\s+prune\b", c), c
        if re.search(r"\bimage\s+prune\b", c):
            assert "reference=ghcr.io/shashkist/" in c, f"image prune must be limited to our images: {c}"


def test_no_force_removal(srv):
    _ok(_deploy(srv))
    for c in srv.removal_calls():
        if re.search(r"\b(rmi|rm|remove)\b", c):
            assert not re.search(r"(^|\s)(-f|--force|-[a-zA-Z]*f[a-zA-Z]*)(\s|$)", c.split("rmi", 1)[-1]), c


def test_foreign_images_survive_whichever_way_the_listing_is_done(srv):
    # a deploy with nothing of ours to prune must not touch the foreign images either
    images = [r for r in _image_set(manager_tags=(NEW_TAG, OLD_TAG)) if r[1] != "<none>" or r[0] != LAB_TERM]
    _ok(_deploy(srv, images=images))
    assert FOREIGN <= srv.image_refs()
    assert srv.removed() == []


# ---------------------------------------------------------------- criterion 4: failures are skipped


def test_a_removal_that_fails_is_skipped_with_a_log_line_and_the_deploy_still_succeeds(srv):
    r = _deploy(srv, STUB_RMI_FAIL=f"{MANAGER}:{OLDER_1}")
    _ok(r)
    assert f"{MANAGER}:{OLDER_1}" in srv.image_refs(), "the in-use image cannot go"
    assert f"{MANAGER}:{OLDER_2}" not in srv.image_refs(), "the others are still pruned"
    assert f"{LAB_TERM}:<none>" not in srv.image_refs()
    out = r.stdout + r.stderr
    skip = [ln for ln in out.splitlines() if re.search(r"skip", ln, re.I) and (OLDER_1 in ln or "0303a1b2c3d4" in ln)]
    assert len(skip) == 1, f"one log line for the image that could not be removed: {out}"


def test_a_failed_removal_does_not_change_the_final_line_the_tag_file_or_the_exit_code(srv):
    r = _deploy(srv, STUB_RMI_FAIL=f"{MANAGER}:{OLDER_1} {MANAGER}:{OLDER_2} {LAB_TERM}:<none>")
    _ok(r)
    assert _last_line(r) == f"deployed {NEW_TAG}", r.stdout
    assert (srv.root / "deployed-tag").read_text().strip() == NEW_TAG
    assert "deploy: pruned 0 old image(s)" in r.stdout


def test_the_final_line_is_deployed_tag_after_a_normal_prune(srv):
    r = _deploy(srv)
    _ok(r)
    assert _last_line(r) == f"deployed {NEW_TAG}", r.stdout


# ---------------------------------------------------------------- criterion 5: a failed deploy prunes nothing


def test_a_failed_health_check_with_rollback_prunes_nothing(srv):
    r = _deploy(srv, STUB_BAD_TAG=NEW_TAG)
    assert r.returncode != 0
    assert srv.removal_calls() == [], srv.removal_calls()
    assert srv.removed() == []
    assert f"{MANAGER}:{OLDER_1}" in srv.image_refs()


def test_a_failed_live_check_prunes_nothing(srv):
    r = _deploy(srv, STUB_DB_DOWN="1")
    assert r.returncode != 0
    assert srv.removal_calls() == [], srv.removal_calls()
    assert srv.removed() == []
    assert f"{LAB_TERM}:<none>" in srv.image_refs()
    assert "pruned" not in r.stdout + r.stderr


def test_a_failed_first_deploy_prunes_nothing(srv):
    r = _deploy(srv, prev=None, STUB_BAD_TAG=NEW_TAG)
    assert r.returncode != 0
    assert srv.removed() == []


# ---------------------------------------------------------------- criterion 6: the log line


def test_the_log_says_how_many_images_were_removed(srv):
    r = _deploy(srv)
    _ok(r)
    n = len(srv.removed())
    assert n == 3, srv.removed()  # two old manager tags + one dangling lab image
    assert f"deploy: pruned {n} old image(s)" in r.stdout, r.stdout


def test_the_count_only_includes_images_that_really_went(srv):
    r = _deploy(srv, STUB_RMI_FAIL=f"{MANAGER}:{OLDER_2}")
    _ok(r)
    assert len(srv.removed()) == 2
    assert "deploy: pruned 2 old image(s)" in r.stdout, r.stdout


def test_the_first_deploy_counts_three_manager_tags_and_the_dangling_image(srv):
    r = _deploy(srv, prev=None)
    _ok(r)
    assert len(srv.removed()) == 4
    assert "deploy: pruned 4 old image(s)" in r.stdout, r.stdout


# ---------------------------------------------------------------- order: only after all live checks


def test_pruning_starts_only_after_the_live_checks_and_the_nginx_blocks(srv):
    _ok(_deploy(srv))
    calls = srv.calls()
    removals = [i for i, c in enumerate(calls) if c in srv.removal_calls()]
    assert removals, "no removal at all"
    last_check = max(i for i, c in enumerate(calls) if c.startswith("curl") or c.startswith("install-block"))
    assert min(removals) > last_check, "images may only be pruned once every live check has passed"
