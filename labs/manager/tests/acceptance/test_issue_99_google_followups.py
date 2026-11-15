# ruff: noqa: E501, F401, F811
"""Acceptance tests for issue #99: Google admin sign-in follow-ups (decisions D39, D23).

Contract (beyond the issue text; the exam relies on it):

1. nginx. `labs/deploy/nginx/labs.conf`, inside the labs block, has a
   `location = /auth/google/callback { ... }` with `access_log off;`, `proxy_pass $upstream;` and
   exactly the `proxy_set_header` lines of `location /`. `install-block.sh` still runs `nginx -t`
   before it reloads (the only way the block reaches the server).
2. Rate-limit keys. The Google callback counts attempts under its own key `gcb:<client>`, not
   `ip:<client>`; the limit value is still `LF_LOGIN_IP_LIMIT`. Failures of one flow never use up
   the budget of the other, from the same client address.
3. Admin sessions. While Google sign-in is configured and `LF_ADMIN_PASSWORD_LOGIN` is not "1",
   `current_principal` answers 401 for an admin session whose subject is not `google:...`
   (checked on every request, so a password session made earlier stops working at once). With the
   flag set, or with Google not configured, the password session keeps working; a `google:`
   session works while Google is configured. `/api/auth/me` is the admin-only probe.
4. deploy.yml. `LF_ADMIN_EMAILS` comes from `secrets.*` only: no `vars.*` anywhere near it.
5. A BOM (U+FEFF) in front of the first `LF_ADMIN_EMAILS` entry does not stop that address from
   completing the callback (303 to /admin).
6. When `LF_ADMIN_EMAILS` entries are dropped (non-ASCII), the manager logs ONE line that names
   the setting `LF_ADMIN_EMAILS` and carries the COUNT of dropped entries as a standalone number,
   never an address nor a part of one. The line is logged once per distinct allow-list value, not
   once per call or per request (`google_config()` runs on every request). Nothing is logged
   when nothing is dropped.

Database tests need LF_TEST_DATABASE_URL (skipped locally, CI fails on any skip); they reuse the
#92 harness and its fake Google. All values are synthetic. Fake client addresses come from
203.0.113.0/24 and fake student ids from 799000000+n, ranges no other module uses (the login
limiter is in-process and shared by all modules).
"""
import itertools
import logging
import re

import pytest
import test_issue_92_google_admin as g92
from test_issue_92_google_admin import (  # noqa: F401  (fixtures re-used by name)
    ADMIN_EMAIL,
    ALLOW_LIST,
    _client,
    _configure,
    _db,
    env,
    env_breakglass,
    google,
    sign_in,
)

BOM = "﻿"  # U+FEFF, kept as an escape so it stays visible
REPO_ROOT = g92.REPO_ROOT
LABS_CONF = REPO_ROOT / "labs" / "deploy" / "nginx" / "labs.conf"
INSTALL = REPO_ROOT / "labs" / "deploy" / "nginx" / "install-block.sh"
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
PW = "break-glass-" + "pw-99"  # throwaway
STUDENT_PW = "wrong-" + "class-pw-99"  # never a real password

_ip = itertools.count(10)
_sid = itertools.count(1)


def nat_ip() -> str:
    return f"203.0.113.{next(_ip)}"


def fake_student_id() -> str:
    return str(799000000 + next(_sid))


# ---------------------------------------------------------------- 1. nginx (static)


def _block(text: str, header: str) -> str:
    """The body of the first `<header> {` ... matching `}`, or fail."""
    m = re.search(re.escape(header) + r"\s*\{", text)
    assert m, f"{header!r} not found"
    depth, i = 1, m.end()
    while depth and i < len(text):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    assert depth == 0, f"unbalanced braces after {header!r}"
    return text[m.end() : i - 1]


def _labs_block() -> str:
    text = LABS_CONF.read_text(encoding="utf-8")
    m = re.search(r"# BEGIN learning-factory:labs\n(.*?)# END learning-factory:labs", text, re.S)
    assert m, "the labs block markers are missing"
    return m.group(1)


def _headers(body: str) -> list[str]:
    return sorted(
        re.sub(r"\s+", " ", ln.strip())
        for ln in body.splitlines()
        if ln.strip().startswith("proxy_set_header")
    )


def test_nginx_labs_block_has_an_exact_location_for_the_google_callback():
    labs = _labs_block()
    assert re.search(r"location\s*=\s*/auth/google/callback\s*\{", labs), (
        "no `location = /auth/google/callback` in the labs block"
    )


def test_nginx_google_callback_location_writes_no_access_log():
    body = _block(_labs_block(), "location = /auth/google/callback")
    assert re.search(r"^\s*access_log\s+off\s*;", body, re.M), body


def test_nginx_google_callback_location_proxies_like_the_root_location():
    labs = _labs_block()
    cb = _block(labs, "location = /auth/google/callback")
    root = _block(labs, "location /")
    assert re.search(r"proxy_pass\s+\$upstream\s*;", cb), cb
    assert _headers(cb), "the callback location sets no proxy headers"
    assert _headers(cb) == _headers(root), (_headers(cb), _headers(root))


def test_nginx_the_access_log_is_off_for_the_callback_only():
    labs = _labs_block()
    assert len(re.findall(r"access_log\s+off", labs)) == 1
    for header in ("location /", "location /s/"):
        assert "access_log" not in _block(labs, header), header


def test_nginx_block_still_installs_only_through_a_validated_reload():
    text = INSTALL.read_text(encoding="utf-8")
    test_at = text.find("nginx -t")
    reload_at = text.find("nginx -s reload")
    assert 0 < test_at < reload_at, "install-block.sh must run `nginx -t` before the reload"


# ---------------------------------------------------------------- 4. deploy.yml (static)


def test_deploy_workflow_reads_the_allow_list_from_secrets_only():
    text = DEPLOY_YML.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if "LF_ADMIN_EMAILS" in ln and "${{" in ln]
    assert lines, "deploy.yml does not pass LF_ADMIN_EMAILS from an expression"
    for ln in lines:
        assert "secrets.LF_ADMIN_EMAILS" in ln, ln
        assert "vars." not in ln, ln
    assert "vars.LF_ADMIN_EMAILS" not in text


# ---------------------------------------------------------------- 2. separate rate-limit keys (database)


def _student_failure(client):
    return client.post("/api/auth/login", json={"id": fake_student_id(), "password": STUDENT_PW})


def _forged_callback(client):
    return client.get(
        f"/auth/google/callback?code=fake-code&state=forged-99-{next(_sid)}",
        headers={"cookie": "lf_oauth=forged-99"},
        follow_redirects=False,
    )


@pytest.fixture
def limited(env, monkeypatch):
    """The #92 env with a small shared limit; clients come from the classroom NAT range."""
    monkeypatch.setenv("LF_LOGIN_IP_LIMIT", "3")
    from app.main import app

    return env, app


def test_student_login_failures_do_not_block_the_google_callback_from_the_same_address(limited):
    _, app = limited
    with _client(app, nat_ip()) as c:
        for i in range(3):
            assert _student_failure(c).status_code == 401, i
        assert _student_failure(c).status_code == 429  # the student budget is used up
        for i in range(3):
            r = _forged_callback(c)
            assert r.status_code == 403, (i, r.status_code, r.text[:200])


def test_google_callback_failures_do_not_block_the_student_login_from_the_same_address(limited):
    _, app = limited
    with _client(app, nat_ip()) as c:
        for i in range(3):
            assert _forged_callback(c).status_code == 403, i
        assert _forged_callback(c).status_code == 429  # the callback budget is used up
        r = _student_failure(c)
        assert r.status_code == 401, (r.status_code, r.text[:200])
        assert r.json() == {"detail": "invalid_credentials"}


def test_google_callback_limit_value_is_still_login_ip_limit(limited):
    _, app = limited
    with _client(app, nat_ip()) as c:
        codes = [_forged_callback(c).status_code for _ in range(5)]
    assert codes == [403, 403, 403, 429, 429], codes


def test_google_callback_budget_is_per_client_address(limited):
    _, app = limited
    with _client(app, nat_ip()) as a, _client(app, nat_ip()) as b:
        for _ in range(4):
            _forged_callback(a)
        assert _forged_callback(a).status_code == 429
        assert _forged_callback(b).status_code == 403


def test_a_429_from_the_callback_keeps_the_too_many_attempts_body(limited):
    _, app = limited
    with _client(app, nat_ip()) as c:
        for _ in range(3):
            _forged_callback(c)
        r = _forged_callback(c)
    assert r.status_code == 429
    assert r.json() == {"detail": "too_many_attempts"}


# ---------------------------------------------------------------- 3. stale password sessions (database)


def _password_session(env) -> str:
    """Sign in as a password admin while that is allowed (Google off), return the cookie token."""
    username = f"fake-admin-99-{next(_sid)}"
    env.add_password_admin(username, PW)
    resp = env.client.post("/api/auth/admin/login", json={"username": username, "password": PW})
    assert resp.status_code == 200, (resp.status_code, resp.text[:200])
    token = env.client.cookies.get("lf_session")
    assert token
    return token


def _me(token: str):
    from app.main import app

    with _client(app, nat_ip()) as c:
        return c.get("/api/auth/me", headers={"cookie": f"lf_session={token}"})


def test_password_admin_session_works_while_google_is_not_configured(env, monkeypatch):
    monkeypatch.delenv("LF_GOOGLE_CLIENT_ID")
    token = _password_session(env)
    assert _me(token).status_code == 200


def test_password_admin_session_stops_working_at_once_when_google_gets_configured(env, monkeypatch):
    monkeypatch.delenv("LF_GOOGLE_CLIENT_ID")
    token = _password_session(env)
    assert _me(token).status_code == 200
    monkeypatch.setenv("LF_GOOGLE_CLIENT_ID", g92.CLIENT_ID)  # Google is configured now
    r = _me(token)
    assert r.status_code == 401, (r.status_code, r.text[:200])
    assert r.json() == {"detail": "not_authenticated"}


def test_password_admin_session_keeps_working_with_the_break_glass_flag(env, monkeypatch):
    monkeypatch.delenv("LF_GOOGLE_CLIENT_ID")
    token = _password_session(env)
    monkeypatch.setenv("LF_GOOGLE_CLIENT_ID", g92.CLIENT_ID)
    monkeypatch.setenv("LF_ADMIN_PASSWORD_LOGIN", "1")
    assert _me(token).status_code == 200


def test_a_session_made_with_the_break_glass_flag_stops_when_the_flag_goes(env_breakglass, monkeypatch):
    token = _password_session(env_breakglass)
    assert _me(token).status_code == 200
    monkeypatch.delenv("LF_ADMIN_PASSWORD_LOGIN")
    assert _me(token).status_code == 401


def test_a_google_admin_session_still_works_while_google_is_configured(env):
    _, _, resp = sign_in(env.client, env.google)
    assert resp.status_code == 303
    token = env.client.cookies.get("lf_session")
    r = _me(token)
    assert r.status_code == 200
    assert r.json() == {"kind": "admin", "name": "admin"}


# ---------------------------------------------------------------- 5. BOM in the allow-list: full callback (database)


@pytest.mark.parametrize("prefix", [BOM, BOM + " "])
def test_bom_prefixed_allow_list_still_lets_the_owner_complete_the_callback(env, monkeypatch, prefix):
    monkeypatch.setenv("LF_ADMIN_EMAILS", prefix + ALLOW_LIST)
    env.google.email = ADMIN_EMAIL  # the first entry of the list
    _, _, resp = sign_in(env.client, env.google)
    assert resp.status_code == 303, (resp.status_code, resp.text[:300])
    assert resp.headers["location"] == "/admin"
    assert "lf_session=" in resp.headers.get("set-cookie", "")
    assert env.client.get("/api/auth/me").json() == {"kind": "admin", "name": "admin"}


# ---------------------------------------------------------------- 6. dropped entries are counted, never named


def _config_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "LF_ADMIN_EMAILS" in r.getMessage()]


def test_dropped_allow_list_entries_are_logged_once_as_a_count(monkeypatch, caplog):
    from app.auth import google_config

    _configure(monkeypatch)
    monkeypatch.setenv(
        "LF_ADMIN_EMAILS",
        "keep.one@example.test, dröpped-alpha@example.test, dropped-beta-א@example.test,"
        " über-gamma@example.test",
    )
    caplog.set_level(logging.DEBUG)
    for _ in range(3):  # google_config() runs on every request: still one line
        cfg = google_config()
        assert cfg is not None and cfg.emails == frozenset({"keep.one@example.test"})
    lines = _config_lines(caplog)
    assert len(lines) == 1, lines
    assert re.search(r"(?<![\w.])3(?![\w.])", lines[0]), lines[0]
    text = "\n".join(r.getMessage() for r in caplog.records).lower()
    for piece in ("alpha", "beta", "gamma", "pped", "dropped-", "ber-", "example.test", "keep.one"):
        assert piece not in text, f"{piece!r} leaked into the logs"


def test_a_single_dropped_entry_is_counted_as_one(monkeypatch, caplog):
    from app.auth import google_config

    _configure(monkeypatch)
    monkeypatch.setenv("LF_ADMIN_EMAILS", "kept@example.test, solé-single@example.test")
    caplog.set_level(logging.DEBUG)
    assert google_config() is not None
    lines = _config_lines(caplog)
    assert len(lines) == 1, lines
    assert re.search(r"(?<![\w.])1(?![\w.])", lines[0]), lines[0]
    assert "single" not in lines[0].lower() and "example.test" not in lines[0].lower()


def test_nothing_is_logged_when_no_allow_list_entry_is_dropped(monkeypatch, caplog):
    from app.auth import google_config

    _configure(monkeypatch)
    monkeypatch.setenv("LF_ADMIN_EMAILS", BOM + "clean.one@example.test, clean.two@example.test, ")
    caplog.set_level(logging.DEBUG)
    cfg = google_config()
    assert cfg is not None and len(cfg.emails) == 2
    assert _config_lines(caplog) == []
