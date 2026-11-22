"""Acceptance tests for issue #505 (review of #503, D137): the deploy seam check, more spellings.

Contract this exam fixes, on top of the #490 exam (which stays as it is):

* A bare `LF_TERM_UPSTREAM` line (no `=`, no `:`) is flagged. Checked against compose: the
  env_file reference of the Docker docs says a line with only `VAR` and no value takes its value
  from the environment compose runs in, and compose-go's dotenv parser (`parseLine`, the branch
  for a key without a separator) hands the bare key to the lookup function for its value. So a bare
  seam line can switch the seam on from the shell. Trailing blanks, leading blanks and an `export `
  prefix are accepted the same way as for the `=` form.
* A UTF-8 byte-order mark in front of the first line does not hide the seam: `\xEF\xBB\xBF`
  + `LF_TERM_UPSTREAM=x` is flagged. A BOM in front of an unrelated key is not flagged.
* Comments and longer names stay unflagged, bare or not.

Values are fake sentinels.
"""
# ruff: noqa: F401, F811  (imported fixtures are used by name)
import pytest
from test_issue_13_deploy import _ok, _stdin, server

SEAM = "LF_TERM_UPSTREAM"
SENTINEL = "http://sentinel-term-upstream.invalid:7681"
BOM = b"\xef\xbb\xbf"

BARE_FLAGGED = [
    pytest.param(f"{SEAM}", id="bare"),
    pytest.param(f"{SEAM}   ", id="bare-trailing-spaces"),
    pytest.param(f"{SEAM}\t", id="bare-trailing-tab"),
    pytest.param(f"  {SEAM}", id="bare-leading-spaces"),
    pytest.param(f"export {SEAM}", id="bare-export"),
    pytest.param(f"export {SEAM}  ", id="bare-export-trailing-spaces"),
]

BARE_NOT_FLAGGED = [
    pytest.param(f"{SEAM}_X=1", id="longer-name-suffix-with-value"),
    pytest.param(f"{SEAM}_X", id="longer-name-suffix-bare"),
    pytest.param(f"X_{SEAM}", id="longer-name-prefix-bare"),
    pytest.param(f"# {SEAM}", id="comment-bare"),
    pytest.param(f"#{SEAM}", id="comment-bare-tight"),
]


def _refused(server, content: bytes) -> None:
    server.env_file.write_bytes(content)
    r = server.run(_stdin())
    assert r.returncode != 0, f"{content!r} got past the seam check"
    assert SEAM in r.stdout + r.stderr
    assert "sentinel-term-upstream" not in r.stdout + r.stderr
    assert not server.compose_up(), "the deploy went on to start containers"


@pytest.mark.parametrize("line", BARE_FLAGGED)
def test_deploy_seam_check_catches_a_bare_key(server, line):
    _refused(server, (line + "\n").encode())


def test_deploy_seam_check_catches_a_bare_key_on_the_last_line_without_newline(server):
    _refused(server, f"OTHER_KEY=1\n{SEAM}".encode())


@pytest.mark.parametrize("line", BARE_NOT_FLAGGED)
def test_deploy_seam_check_ignores_similar_bare_lines(server, line):
    server.env_file.write_bytes((line + "\n").encode())
    _ok(server.run(_stdin()))


def test_deploy_seam_check_catches_the_seam_behind_a_byte_order_mark(server):
    _refused(server, BOM + f"{SEAM}={SENTINEL}\n".encode())


def test_deploy_seam_check_catches_a_bare_seam_behind_a_byte_order_mark(server):
    _refused(server, BOM + f"{SEAM}\n".encode())


def test_deploy_seam_check_ignores_an_unrelated_key_behind_a_byte_order_mark(server):
    server.env_file.write_bytes(BOM + b"LF_OTHER_KEY=1\n")
    _ok(server.run(_stdin()))


def test_deploy_seam_check_ignores_a_longer_name_behind_a_byte_order_mark(server):
    server.env_file.write_bytes(BOM + f"{SEAM}_X=1\n".encode())
    _ok(server.run(_stdin()))
