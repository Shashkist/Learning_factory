"""Exam for #110: a byte-order mark in the Google sign-in settings must not reach Google.

A value saved by Windows PowerShell 5.1 often starts with U+FEFF. Python's `str.strip()` keeps it,
so the live redirect carried `client_id=%EF%BB%BF...` and a BOM-prefixed first allow-list entry
failed `isascii()` and was dropped without a word.

Contract: `app.auth.google_config()` reads LF_GOOGLE_CLIENT_ID, LF_GOOGLE_CLIENT_SECRET,
LF_GOOGLE_REDIRECT_URI and every LF_ADMIN_EMAILS entry with surrounding whitespace and a leading
U+FEFF removed. All values here are fake.
"""

import itertools
from urllib.parse import parse_qs, urlsplit

import pytest

BOM = "\ufeff"  # U+FEFF, kept as an escape so it stays visible
CLIENT_ID = "fake-client-110.example.test"
CLIENT_SECRET = "fake-client-" + "secret-110"  # throwaway, never a real secret
REDIRECT = "https://labs.example.test/auth/google/callback"
TEST_SECRET = "exam-110-" + "id-secret"
_ip = itertools.count(1)


def _configure(monkeypatch, *, cid=CLIENT_ID, secret=CLIENT_SECRET, emails, redirect=None):
    monkeypatch.setenv("LF_GOOGLE_CLIENT_ID", cid)
    monkeypatch.setenv("LF_GOOGLE_CLIENT_SECRET", secret)
    monkeypatch.setenv("LF_ADMIN_EMAILS", emails)
    if redirect is None:
        monkeypatch.delenv("LF_GOOGLE_REDIRECT_URI", raising=False)
    else:
        monkeypatch.setenv("LF_GOOGLE_REDIRECT_URI", redirect)
    monkeypatch.delenv("LF_ADMIN_PASSWORD_LOGIN", raising=False)


@pytest.mark.parametrize("prefix", [BOM, BOM + " ", " " + BOM, BOM + "\r\n"])
def test_client_id_and_secret_lose_a_leading_bom(monkeypatch, prefix):
    from app.auth import google_config

    _configure(
        monkeypatch,
        cid=prefix + CLIENT_ID + "\r\n",
        secret=prefix + CLIENT_SECRET,
        emails="admin@example.test",
    )
    cfg = google_config()
    assert cfg is not None
    assert cfg.client_id == CLIENT_ID
    assert cfg.client_secret == CLIENT_SECRET


def test_a_bom_on_the_first_allow_list_entry_keeps_that_address(monkeypatch):
    from app.auth import google_config

    _configure(monkeypatch, emails=BOM + "Owner@Example.TEST, second@example.test")
    cfg = google_config()
    assert cfg is not None
    assert cfg.emails == frozenset({"owner@example.test", "second@example.test"})


def test_a_bom_only_allow_list_means_not_configured(monkeypatch):
    from app.auth import google_config

    _configure(monkeypatch, emails=BOM + " , ")
    assert google_config() is None


def test_a_bom_on_the_redirect_uri_is_removed(monkeypatch):
    from app.auth import google_config

    _configure(monkeypatch, emails="admin@example.test", redirect=BOM + REDIRECT + "\n")
    cfg = google_config()
    assert cfg is not None
    assert cfg.redirect_uri == REDIRECT


def test_start_sends_the_clean_client_id_to_google(monkeypatch):
    from fastapi.testclient import TestClient

    _configure(monkeypatch, cid=BOM + CLIENT_ID, emails=BOM + "admin@example.test")
    monkeypatch.delenv("LF_DATABASE_URL", raising=False)
    monkeypatch.delenv("LF_TRUSTED_PROXY", raising=False)
    monkeypatch.setenv("LF_ID_SECRET", TEST_SECRET)
    from app.main import app

    with TestClient(
        app, base_url="https://testserver", client=(f"203.0.113.{next(_ip)}", 50000)
    ) as c:
        assert c.get("/api/auth/admin/methods").json()["google"] is True
        resp = c.get("/auth/google/start", follow_redirects=False)
    assert resp.status_code in (302, 303, 307), resp.status_code
    location = resp.headers["location"]
    assert "%EF%BB%BF" not in location.upper()
    assert parse_qs(urlsplit(location).query)["client_id"] == [CLIENT_ID]
