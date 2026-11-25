"""Exam for #64 (criterion 7 only, D164): X-Content-Type-Options nosniff on the dashboard site.

nginx does not inherit add_header into a location that has its own add_header, so every location
with any add_header must repeat the header, with `always` like the existing ones.
"""
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
DEPLOY = REPO / "labs" / "deploy"
NGINX_CONF = DEPLOY / "nginx" / "factory.conf"
INSTALLER = DEPLOY / "nginx" / "install-block.sh"

NOSNIFF = re.compile(r'add_header\s+X-Content-Type-Options\s+"?nosniff"?\s+always\s*;', re.I)
NOINDEX = re.compile(r'add_header\s+X-Robots-Tag\s+"noindex"\s+always\s*;', re.I)
NOCACHE = re.compile(r'add_header\s+Cache-Control\s+"no-cache"\s+always\s*;', re.I)
ANY_HEADER = re.compile(r"\badd_header\b")


def _strip_comments(text: str) -> str:
    return re.sub(r"#[^\n]*", "", text)


def _close(text: str, open_idx: int) -> int:
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    raise AssertionError("unbalanced braces in nginx conf")


def _blocks(text: str, head: re.Pattern):
    """Yield (header, body) for each block whose header matches."""
    for m in head.finditer(text):
        o = text.index("{", m.start())
        yield text[m.start():o].strip(), text[o + 1:_close(text, o)]


def _without_blocks(body: str) -> str:
    """Directives that sit directly in `body`, nested blocks removed."""
    out, i = [], 0
    while i < len(body):
        if body[i] == "{":
            i = _close(body, i) + 1
        else:
            out.append(body[i])
            i += 1
    return "".join(out)


def _server() -> str:
    text = _strip_comments(NGINX_CONF.read_text(encoding="utf-8"))
    servers = [b for _, b in _blocks(text, re.compile(r"\bserver\s*\{"))
               if "factory.shashkist.com" in b]
    assert len(servers) == 1, "expected one server block for factory.shashkist.com"
    return servers[0]


def _locations(server: str):
    return list(_blocks(server, re.compile(r"\blocation\b[^{;]*\{")))


def _location(path: str) -> str:
    for head, body in _locations(_server()):
        if re.fullmatch(r"location\s*=\s*" + re.escape(path), head):
            return body
    raise AssertionError(f"no `location = {path}` block")


def test_server_level_sends_nosniff_always():
    direct = _without_blocks(_server())
    assert NOSNIFF.search(direct), "server level lacks nosniff always"
    assert NOINDEX.search(direct), "server level lost X-Robots-Tag noindex"


@pytest.mark.parametrize("path", ["/snapshot.json", "/live.json"])
def test_json_location_sends_nosniff_always(path):
    body = _location(path)
    assert NOSNIFF.search(body), f"{path} lacks add_header X-Content-Type-Options nosniff always"
    assert NOCACHE.search(body), f"{path} lost Cache-Control no-cache"
    assert NOINDEX.search(body), f"{path} lost X-Robots-Tag noindex"


def test_every_location_with_add_header_repeats_nosniff():
    locs = _locations(_server())
    assert locs, "no location blocks found"
    for head, body in locs:
        if ANY_HEADER.search(body):
            assert NOSNIFF.search(body), f"`{head}` has add_header but drops nosniff"


def test_nosniff_is_declared_only_in_factory_conf_under_deploy():
    """No second hand-written copy of the factory site's headers that could diverge."""
    copies = []
    for p in DEPLOY.rglob("*"):
        if not p.is_file() or p == NGINX_CONF or p.suffix in {".png", ".gz"}:
            continue
        try:
            t = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if "factory.shashkist.com" in t and ANY_HEADER.search(t):
            copies.append(p.name)
    assert not copies, f"factory site headers duplicated in {copies}"


def test_installer_puts_nosniff_into_the_shared_conf(tmp_path):
    bash = shutil.which("bash")
    assert bash
    docker = tmp_path / "fake-docker"
    docker.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8", newline="\n")
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    other = "server {\n    server_name other.example.org;\n}\n"
    old = ("# BEGIN learning-factory:factory\nserver { server_name factory.shashkist.com; }\n"
           "# END learning-factory:factory\n")
    conf = tmp_path / "default.conf"
    conf.write_text(other + old, encoding="utf-8", newline="\n")
    env = dict(os.environ)
    env.update(LF_NGINX_CONF=str(conf), LF_DOCKER=str(docker))
    p = subprocess.run([bash, str(INSTALLER), "factory", str(NGINX_CONF)],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    out = _strip_comments(conf.read_text(encoding="utf-8"))
    assert len(NOSNIFF.findall(out)) >= 3, "installed conf lacks nosniff (server + 2 JSON)"
