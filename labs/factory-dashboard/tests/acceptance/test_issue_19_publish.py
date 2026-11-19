"""Exam for issue #19: publish the dashboard at factory.shashkist.com (see D14).

CONTRACT (the builder works from this)

labs/deploy/nginx/factory.conf
    The complete marked block, INCLUDING its marker lines:
        # BEGIN learning-factory:factory
        ...server { ... }
        # END learning-factory:factory
    (the server block currently live, plus optional additions).

labs/deploy/nginx/install-block.sh <name> <file>      (run as `bash install-block.sh ...`)
    <file> holds one block that starts with `# BEGIN learning-factory:<name>` and ends with
    `# END learning-factory:<name>`. The script writes it into the nginx conf: if the markers
    for <name> exist, the lines from BEGIN to END (inclusive) are replaced in place; if not,
    the block is appended. Everything outside the markers stays byte-identical. Then:
        1. backup the conf next to it, named `<conf basename>.<anything>` (left behind),
        2. `<docker> exec nginx-proxy3 nginx -t`
        3. on success `<docker> exec nginx-proxy3 nginx -s reload`;
           on failure restore the original bytes, do not reload, exit non-zero.
    It never calls `restart`. Environment overrides:
        LF_NGINX_CONF   path of the conf to edit (default: the server's default.conf)
        LF_DOCKER       docker binary (default: docker)

build_snapshot.py --public
    Same CLI as #18 plus the flag. Output has no `state` key; all other keys as in #18.

index.html: no third-party URLs, CSP meta with default-src 'self', no inline script bodies.

.github/workflows/factory-dashboard.yml: builds with --public, publish step on main only.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

COMPONENT = Path(__file__).resolve().parents[2]
REPO = COMPONENT.parents[1]
SCRIPT = COMPONENT / "build_snapshot.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "issue18"
NOW = "2026-11-14T12:00:00Z"
INSTALLER = REPO / "labs" / "deploy" / "nginx" / "install-block.sh"
FACTORY_CONF = REPO / "labs" / "deploy" / "nginx" / "factory.conf"
WORKFLOW = REPO / ".github" / "workflows" / "factory-dashboard.yml"
INDEX = COMPONENT / "index.html"
BASH = shutil.which("bash")

# ---- installer harness -----------------------------------------------------

OTHER_BEFORE = (
    "# some other site\n"
    "server {\n    listen 443 ssl;\n    server_name teams.shashkist.com;\n"
    "    location / { proxy_pass http://app:8080; }\n}\n"
    "# BEGIN learning-factory:http\nserver { listen 80; server_name factory.shashkist.com; }\n"
    "# END learning-factory:http\n"
)
OTHER_AFTER = "server {\n    listen 443 ssl;\n    server_name n8n.example.org;\n}\n\n"


def block(name: str, body: str) -> str:
    return f"# BEGIN learning-factory:{name}\n{body}\n# END learning-factory:{name}\n"


FAKE_DOCKER = """#!/usr/bin/env bash
echo "$*" >> "$FAKE_LOG"
if [ -n "$FAKE_FAIL_T" ] && [[ "$*" == *"nginx -t"* ]]; then
  echo "nginx: configuration file test failed" >&2
  exit 1
fi
exit 0
"""


@pytest.fixture
def env(tmp_path):
    assert BASH, "bash is required"
    docker = tmp_path / "fake-docker"
    docker.write_text(FAKE_DOCKER, encoding="utf-8", newline="\n")
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    conf_dir = tmp_path / "conf.d"
    conf_dir.mkdir()
    return {
        "tmp": tmp_path,
        "conf": conf_dir / "default.conf",
        "log": tmp_path / "docker.log",
        "docker": docker,
    }


def install(env, name: str, blockfile: Path, fail_test: bool = False):
    e = dict(os.environ)
    e.update(
        LF_NGINX_CONF=str(env["conf"]),
        LF_DOCKER=str(env["docker"]),
        FAKE_LOG=str(env["log"]),
    )
    e.pop("FAKE_FAIL_T", None)
    if fail_test:
        e["FAKE_FAIL_T"] = "1"
    return subprocess.run(
        [BASH, str(INSTALLER), name, str(blockfile)],
        capture_output=True, text=True, env=e, cwd=env["tmp"],
    )


def calls(env) -> list[str]:
    return env["log"].read_text(encoding="utf-8").splitlines() if env["log"].exists() else []


def setup_conf(env, text: str, blk: str) -> Path:
    env["conf"].write_bytes(text.encode())
    f = env["tmp"] / "block.conf"
    f.write_bytes(blk.encode())
    return f


def test_installer_script_exists():
    assert INSTALLER.is_file(), "labs/deploy/nginx/install-block.sh is missing"


def test_installer_appends_block_when_absent(env):
    original = OTHER_BEFORE + OTHER_AFTER
    blk = block("factory", "server { server_name factory.shashkist.com; }")
    f = setup_conf(env, original, blk)
    p = install(env, "factory", f)
    assert p.returncode == 0, p.stdout + p.stderr
    out = env["conf"].read_text(encoding="utf-8")
    assert out.startswith(original)
    assert out.count("# BEGIN learning-factory:factory") == 1
    assert out.count("# END learning-factory:factory") == 1
    assert "server { server_name factory.shashkist.com; }" in out


def test_installer_replaces_block_in_place(env):
    old = block("factory", "server { server_name OLD; }")
    original = OTHER_BEFORE + old + OTHER_AFTER
    new = block("factory", "server { server_name NEW; }")
    f = setup_conf(env, original, new)
    p = install(env, "factory", f)
    assert p.returncode == 0, p.stdout + p.stderr
    assert env["conf"].read_text(encoding="utf-8") == OTHER_BEFORE + new + OTHER_AFTER


def test_installer_is_idempotent(env):
    blk = block("factory", "server { server_name factory.shashkist.com; }")
    f = setup_conf(env, OTHER_BEFORE + OTHER_AFTER, blk)
    assert install(env, "factory", f).returncode == 0
    first = env["conf"].read_bytes()
    assert install(env, "factory", f).returncode == 0
    assert env["conf"].read_bytes() == first
    assert first.decode().count("BEGIN learning-factory:factory") == 1


def test_installer_leaves_other_marked_blocks_alone(env):
    old = block("factory", "server { server_name OLD; }")
    original = OTHER_BEFORE + old + OTHER_AFTER
    new = block("http", "server { listen 80; server_name changed; }")
    f = setup_conf(env, original, new)
    assert install(env, "http", f).returncode == 0
    out = env["conf"].read_text(encoding="utf-8")
    assert old in out and "server_name changed" in out
    assert out.count("BEGIN learning-factory:http") == 1
    assert out.count("END learning-factory:http") == 1
    assert "server_name factory.shashkist.com; }\n# END learning-factory:http" not in out


def test_installer_validates_then_reloads_in_order(env):
    f = setup_conf(env, OTHER_BEFORE, block("factory", "server {}"))
    assert install(env, "factory", f).returncode == 0
    c = calls(env)
    t = [i for i, x in enumerate(c) if "exec nginx-proxy3 nginx -t" in x]
    r = [i for i, x in enumerate(c) if "exec nginx-proxy3 nginx -s reload" in x]
    assert t and r and t[0] < r[0]


def test_installer_never_restarts(env):
    f = setup_conf(env, OTHER_BEFORE, block("factory", "server {}"))
    install(env, "factory", f)
    install(env, "factory", f, fail_test=True)
    assert any("nginx -t" in x for x in calls(env))
    assert not any("restart" in x for x in calls(env))


def test_installer_restores_original_when_nginx_test_fails(env):
    original = OTHER_BEFORE + block("factory", "server { OLD }") + OTHER_AFTER
    f = setup_conf(env, original, block("factory", "server { BROKEN }"))
    p = install(env, "factory", f, fail_test=True)
    assert p.returncode != 0
    assert any("nginx -t" in x for x in calls(env)), "nginx -t was never run"
    assert env["conf"].read_bytes() == original.encode()


def test_installer_restores_original_on_failed_append(env):
    original = OTHER_BEFORE + OTHER_AFTER
    f = setup_conf(env, original, block("factory", "server { BROKEN }"))
    assert install(env, "factory", f, fail_test=True).returncode != 0
    assert any("nginx -t" in x for x in calls(env)), "nginx -t was never run"
    assert env["conf"].read_bytes() == original.encode()


def test_installer_does_not_reload_when_nginx_test_fails(env):
    f = setup_conf(env, OTHER_BEFORE, block("factory", "server { BROKEN }"))
    install(env, "factory", f, fail_test=True)
    c = calls(env)
    assert any("nginx -t" in x for x in c)
    assert not any("reload" in x for x in c)


def test_installer_leaves_a_backup_of_the_original(env):
    original = OTHER_BEFORE + OTHER_AFTER
    f = setup_conf(env, original, block("factory", "server { NEW }"))
    assert install(env, "factory", f).returncode == 0
    backups = [
        p for p in env["conf"].parent.iterdir()
        if p != env["conf"] and p.name.startswith("default.conf.")
    ]
    assert backups, "no backup `default.conf.<suffix>` left next to the conf"
    assert any(b.read_bytes() == original.encode() for b in backups)


# ---- factory.conf ----------------------------------------------------------

def conf_text() -> str:
    assert FACTORY_CONF.is_file(), "labs/deploy/nginx/factory.conf is missing"
    return FACTORY_CONF.read_text(encoding="utf-8")


def test_factory_conf_is_a_marked_block():
    lines = [x.strip() for x in conf_text().strip().splitlines()]
    assert lines[0] == "# BEGIN learning-factory:factory"
    assert lines[-1] == "# END learning-factory:factory"


def test_factory_conf_serves_the_factory_host_from_the_static_root():
    t = conf_text()
    assert re.search(r"server_name\s+factory\.shashkist\.com\s*;", t)
    assert re.search(r"root\s+/var/www/static/factory\s*;", t)
    assert re.search(r"listen\s+443\s+ssl\s*;", t)


def test_factory_conf_uses_the_shared_certificate():
    t = conf_text()
    assert "/etc/letsencrypt/live/teams.shashkist.com/fullchain.pem" in t
    assert "/etc/letsencrypt/live/teams.shashkist.com/privkey.pem" in t


def test_factory_conf_is_noindex():
    assert re.search(r'add_header\s+X-Robots-Tag\s+"noindex"\s+always\s*;', conf_text())


def test_factory_conf_snapshot_is_not_cached():
    m = re.search(r"location\s*=\s*/snapshot\.json\s*\{(.*?)\}", conf_text(), re.S)
    assert m, "no `location = /snapshot.json` block"
    assert re.search(r'add_header\s+Cache-Control\s+"no-cache"\s+always\s*;', m.group(1))


# ---- build_snapshot.py --public -------------------------------------------

def snapshot(tmp_path: Path, *extra: str) -> dict:
    out = tmp_path / "snapshot.json"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--out", str(out), "--fixtures", str(FIXTURES),
         "--repo-root", str(FIXTURES / "repo"), "--now", NOW, *extra],
        capture_output=True, text=True, encoding="utf-8", cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(out.read_text(encoding="utf-8"))


def test_public_snapshot_has_no_state_key(tmp_path):
    assert "state" not in snapshot(tmp_path, "--public")


def test_public_snapshot_contains_no_state_free_text(tmp_path):
    raw = json.dumps(snapshot(tmp_path, "--public"))
    for text in ("exam, then build", "Dashboard work", "CI green", "7.8 GB"):
        assert text not in raw


def test_public_snapshot_keeps_the_fields_the_page_renders(tmp_path):
    pub = snapshot(tmp_path, "--public")
    full = snapshot(tmp_path)
    assert pub == {k: v for k, v in full.items() if k != "state"}
    for key in ("generated_at", "issues", "claim", "prs", "runs", "decisions",
                "owner_open", "throughput"):
        assert key in pub


def test_snapshot_without_public_keeps_the_18_contract(tmp_path):
    state = snapshot(tmp_path)["state"]
    assert set(state) == {"now", "next", "health"}
    assert "exam, then build" in state["now"]


# ---- page ------------------------------------------------------------------

def test_page_has_no_third_party_urls():
    html = INDEX.read_text(encoding="utf-8")
    assert "fonts.googleapis" not in html and "fonts.gstatic" not in html
    assert not re.findall(r"""(?:src|href)\s*=\s*["'](?:https?:)?//""", html, re.I)


def test_page_stylesheet_has_no_third_party_urls():
    css = (COMPONENT / "styles.css").read_text(encoding="utf-8")
    assert not re.search(r"""(?:url\(|@import)\s*["']?(?:https?:)?//""", css, re.I)


def test_page_has_csp_default_src_self():
    html = INDEX.read_text(encoding="utf-8")
    m = re.search(r"""<meta[^>]+http-equiv=["']Content-Security-Policy["'][^>]*>""", html, re.I)
    assert m, "no CSP meta"
    assert re.search(r"default-src\s+'self'", m.group(0))


def test_page_has_no_inline_script_body():
    html = INDEX.read_text(encoding="utf-8")
    scripts = list(re.finditer(r"<script\b([^>]*)>(.*?)</script>", html, re.S | re.I))
    assert len(scripts) >= 2, "expected the theme script and app.js as files"
    for m in scripts:
        assert "src=" in m.group(1), "script without src"
        assert m.group(2).strip() == "", "inline script body"


# ---- workflow --------------------------------------------------------------

def wf() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def all_steps() -> list[dict]:
    return [s for j in wf()["jobs"].values() for s in j.get("steps", [])]


def publish_steps() -> list[dict]:
    steps = [s for s in all_steps() if "/root/static/factory" in str(s.get("run", ""))]
    assert steps, "no step that publishes to /root/static/factory"
    return steps


def test_workflow_builds_the_snapshot_with_public():
    runs = " ".join(str(s.get("run", "")) for s in all_steps())
    assert re.search(r"build_snapshot\.py[^\n]*--public", runs)


def test_workflow_keeps_the_d71_triggers():
    # D71 (#155) replaces D12's 3-hour schedule with every 15 minutes; push and dispatch stay.
    on = wf().get("on", wf().get(True))
    assert set(on) == {"schedule", "push", "workflow_dispatch"}
    assert on["push"]["branches"] == ["main"]
    assert on["schedule"] == [{"cron": "7,22,37,52 * * * *"}]


def test_workflow_publish_is_guarded_to_main():
    for s in publish_steps():
        job_guard = any(
            "refs/heads/main" in str(j.get("if", "")) and s in j.get("steps", [])
            for j in wf()["jobs"].values()
        )
        assert job_guard or "refs/heads/main" in str(s.get("if", "")), (
            "publish step is not guarded by `github.ref == 'refs/heads/main'`"
        )


def test_workflow_secrets_only_via_env_never_inline_in_run():
    for s in all_steps():
        assert "secrets." not in str(s.get("run", "")), "secret inline in run:"
    env_all: dict = {}
    for s in publish_steps():
        env_all.update(s.get("env", {}) or {})
    for name in ("SSH_PRIVATE_KEY", "SERVER_HOST", "SERVER_USER"):
        assert any(
            str(v).strip() == "${{ secrets." + name + " }}" for v in env_all.values()
        ), f"{name} not passed through env:"


def test_workflow_publishes_to_root_static_factory_via_staging_then_mv():
    run = "\n".join(str(s["run"]) for s in publish_steps())
    copy = re.search(r"\b(scp|rsync)\b", run)
    assert copy, "no scp/rsync upload"
    mv = re.search(r"\bmv\b", run)
    assert mv, "no mv into place"
    assert copy.start() < mv.start()


def test_workflow_publishes_the_four_files():
    text = WORKFLOW.read_text(encoding="utf-8")
    for f in ("index.html", "app.js", "styles.css", "snapshot.json"):
        assert f in text


def test_workflow_skips_with_a_log_line_when_secrets_are_empty():
    run = "\n".join(str(s["run"]) for s in publish_steps())
    assert re.search(r"-z\b|\[\s*-n\b", run), "no empty-secret check"
    assert re.search(r"echo[^\n]*(skip|not set|missing|no secrets)", run, re.I)


def test_workflow_never_touches_nginx_or_docker_services():
    text = WORKFLOW.read_text(encoding="utf-8")
    for bad in ("docker restart", "systemctl restart", "nginx -s", "install-block"):
        assert bad not in text, bad
