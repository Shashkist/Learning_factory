"""Acceptance tests for issue #11, slice 1 (D59): teacher API for cohorts and paste import.

Spec sections 8 (data model) and 9 (cohorts). Database tests need a superuser DSN to a
throwaway Postgres in LF_TEST_DATABASE_URL and skip locally when it is absent (CI sets it and
fails on any skip). Every id and name below is fake.

CONTRACT the implementation must honour (names chosen by the exam). Every route needs an admin
session: no session -> 401, a student session -> 403. Validation failures are 422.

  GET   /api/admin/cohorts
        200 JSON array of {id:int, name:str, access_until:"YYYY-MM-DD", students:int}.
        Never any password or hash key or value. The reserved `lf-smoke` cohort is never listed.
  POST  /api/admin/cohorts   {name, password, access_until:"YYYY-MM-DD"}
        201 {id, name, access_until, students:0}. name is trimmed. 422 for an empty or blank
        name, an empty password, a missing or malformed or impossible date, and for the name
        `lf-smoke` (case-insensitive, after trimming). The password is stored hashed.
  PATCH /api/admin/cohorts/{id}   any of {name, password, access_until}
        200 the updated cohort (same shape as in the list). 404 unknown id (and the lf-smoke
        cohort's id). 422 for the same invalid values as create, and for renaming to `lf-smoke`.
        A changed password works for student login at once; the old one stops working.
  POST  /api/admin/cohorts/{id}/import   {text}
        text is the pasted lines "ID, name": split at the first comma, the name is the rest,
        trimmed; blank lines are skipped. 404 unknown cohort. 422 when `text` is missing.
        Otherwise 200
          {added:int, rows:[{line:int, status:"added"|"error", code?:str, message?:str}]}
        with one row per non-blank line, `line` being the 1-based line number in the pasted
        text. Good rows are saved even when other rows fail. Error codes: "invalid_id"
        (app.auth.valid_id false after normalization), "missing_name", "duplicate_in_paste"
        (the second and later rows with the same normalized id), "already_exists" (the id is
        already a student, in any cohort). Every error row has a non-empty `message`.
        Raw ids are stored only as app.auth.id_hash; the pasted text is never stored or logged.
  GET   /api/admin/cohorts/{id}/students
        200 JSON array of {name, note:str|null, disabled:bool, access_until:str|null}, ordered
        by name. No raw id anywhere in the body. 404 unknown cohort.
"""
import asyncio
import datetime as dt
import hashlib
import hmac
import itertools
import logging
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

SCHEMA = "learning_factory"
APP_ROLE = "lf_app"
APP_PASSWORD = "test-only-" + "pw"  # throwaway test credential, not a secret
TEST_SECRET = "test-only-id-" + "secret"  # fixed, throwaway
COHORT_PASSWORD = "class-" + "password-1"  # throwaway
NEW_PASSWORD = "class-" + "password-2"  # throwaway
ADMIN_PASSWORD = "admin-" + "password-1"  # throwaway
TODAY = dt.datetime.now(dt.UTC).date()
DAY = dt.timedelta(days=1)
FUTURE = (TODAY + 30 * DAY).isoformat()
SMOKE = "lf-smoke"

_n = itertools.count(1)

REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "labs").is_dir() or (p / ".git").exists()
)
OWNER_SQL = REPO_ROOT / "labs" / "deploy" / "db" / "001-create-schema-and-role.sql"


# ---------------------------------------------------------------- helpers


def _admin_dsn() -> str:
    dsn = os.environ.get("LF_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("LF_TEST_DATABASE_URL not set (no Postgres available)")
    host = urlsplit(dsn).hostname
    if host not in ("localhost", "127.0.0.1", "::1"):
        pytest.fail(f"LF_TEST_DATABASE_URL must point at a local throwaway Postgres, not {host!r}")
    return dsn


def _app_dsn(admin_dsn: str) -> str:
    parts = urlsplit(admin_dsn)
    host = parts.hostname or "localhost"
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{APP_ROLE}:{APP_PASSWORD}@{host}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _run_as(dsn: str, fn):
    async def go():
        import asyncpg  # a missing asyncpg must fail, not skip

        conn = await asyncpg.connect(dsn, statement_cache_size=0)
        try:
            return await fn(conn)
        finally:
            await conn.close()

    return asyncio.run(go())


def _norm(raw: str) -> str:
    return "".join(raw.split()).lstrip("0")


def id_hash(raw: str) -> str:
    return hmac.new(TEST_SECRET.encode(), _norm(raw).encode(), hashlib.sha256).hexdigest()


def fresh_id() -> str:
    """A fake normalized id, unique per call (own range, 9 digits)."""
    return str(110000000 + next(_n))


def fresh_ip() -> str:
    n = next(_n)
    return f"10.11.{n // 250}.{n % 250 + 1}"


def _hash_pw(pw: str) -> str:
    from argon2 import PasswordHasher  # a missing argon2-cffi must fail, not skip

    return PasswordHasher().hash(pw)


class Env:
    def __init__(self, admin_dsn: str, client):
        self.admin_dsn = admin_dsn
        self.client = client  # lifespan client, no session
        self.app = client.app

    def sql(self, fn):
        return _run_as(self.admin_dsn, fn)

    def new_client(self):
        from fastapi.testclient import TestClient

        c = TestClient(self.app, base_url="https://testserver", client=(fresh_ip(), 50000))
        c.portal = self.client.portal  # the app's pool belongs to the lifespan client's loop
        return c

    def seed_cohort(self, name: str | None = None, until: dt.date | None = None) -> int:
        pw = _hash_pw(COHORT_PASSWORD)
        name = name or f"Fake cohort {next(_n)}"

        async def go(conn):
            return await conn.fetchval(
                f"insert into {SCHEMA}.cohorts (name, password_hash, access_until) "
                "values ($1, $2, $3) returning id",
                name, pw, until or TODAY + 30 * DAY,
            )

        return self.sql(go)

    def seed_student(self, cohort_id: int, raw: str | None = None, name: str | None = None,
                     note: str | None = None, disabled: bool = False,
                     access_until: dt.date | None = None) -> tuple[str, str]:
        raw = raw or fresh_id()
        name = name or f"Fake Student {next(_n)}"

        async def go(conn):
            await conn.execute(
                f"insert into {SCHEMA}.students "
                "(id_hash, name, note, cohort_id, access_until, disabled) "
                "values ($1,$2,$3,$4,$5,$6)",
                id_hash(raw), name, note, cohort_id, access_until, disabled,
            )

        self.sql(go)
        return raw, name

    def admin(self):
        name = f"fake-admin-{next(_n)}"
        pw = _hash_pw(ADMIN_PASSWORD)

        async def go(conn):
            await conn.execute(
                f"insert into {SCHEMA}.admins (username, password_hash) values ($1, $2)", name, pw
            )

        self.sql(go)
        client = self.new_client()
        body = {"username": name, "password": ADMIN_PASSWORD}
        resp = client.post("/api/auth/admin/login", json=body)
        assert resp.status_code == 200, resp.text
        return client

    def student_client(self):
        cid = self.seed_cohort()
        raw, _ = self.seed_student(cid)
        client = self.new_client()
        resp = client.post("/api/auth/login", json={"id": raw, "password": COHORT_PASSWORD})
        assert resp.status_code == 200, resp.text
        return client

    def smoke_cohort(self) -> int:
        """The reserved cohort, as the smoke student's setup creates it (seeded directly)."""

        async def go(conn):
            found = await conn.fetchval(
                f"select id from {SCHEMA}.cohorts where lower(name) = $1", SMOKE
            )
            if found:
                return found
            return await conn.fetchval(
                f"insert into {SCHEMA}.cohorts (name, password_hash, access_until) "
                "values ($1, $2, $3) returning id",
                SMOKE, _hash_pw(COHORT_PASSWORD), TODAY + 30 * DAY,
            )

        return self.sql(go)

    def cohort_row(self, cid: int):
        async def go(conn):
            return await conn.fetchrow(f"select * from {SCHEMA}.cohorts where id = $1", cid)

        return self.sql(go)

    def student_rows(self, cid: int):
        async def go(conn):
            return await conn.fetch(f"select * from {SCHEMA}.students where cohort_id = $1", cid)

        return self.sql(go)

    def dump_everything(self) -> str:
        """Every row of every table in the schema, as text."""

        async def go(conn):
            tables = await conn.fetch(
                "select table_name from information_schema.tables "
                "where table_schema = $1 and table_type = 'BASE TABLE'", SCHEMA
            )
            out = []
            for t in tables:
                rows = await conn.fetch(
                    f'select row_to_json(x)::text as j from {SCHEMA}."{t["table_name"]}" x'
                )
                out += [r["j"] for r in rows]
            return "\n".join(out)

        return self.sql(go)


@pytest.fixture(scope="module")
def _db():
    admin_dsn = _admin_dsn()

    async def reset(conn):
        await conn.execute(f"drop schema if exists {SCHEMA} cascade")
        assert OWNER_SQL.is_file(), f"{OWNER_SQL} is missing"
        await conn.execute(OWNER_SQL.read_text(encoding="utf-8"))
        await conn.execute(f"alter role {APP_ROLE} password '{APP_PASSWORD}'")

    _run_as(admin_dsn, reset)
    return admin_dsn


@pytest.fixture
def env(_db, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("LF_DATABASE_URL", _app_dsn(_db))
    monkeypatch.setenv("LF_ID_SECRET", TEST_SECRET)
    monkeypatch.delenv("LF_TRUSTED_PROXY", raising=False)
    from app.main import app

    with TestClient(app, base_url="https://testserver", client=(fresh_ip(), 50000)) as c:
        yield Env(_db, c)


@pytest.fixture
def admin(env):
    return env.admin()


def new_cohort(client, name=None, password=COHORT_PASSWORD, until=FUTURE):
    body = {"name": name or f"Fake cohort {next(_n)}", "password": password, "access_until": until}
    resp = client.post("/api/admin/cohorts", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def do_import(client, cid, text):
    return client.post(f"/api/admin/cohorts/{cid}/import", json={"text": text})


def import_ok(client, cid, text):
    resp = do_import(client, cid, text)
    assert resp.status_code == 200, resp.text
    return resp.json()


def students(client, cid):
    resp = client.get(f"/api/admin/cohorts/{cid}/students")
    assert resp.status_code == 200, resp.text
    return resp.json()


def listed(client):
    resp = client.get("/api/admin/cohorts")
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------- access control

ROUTES = [
    ("get", "/api/admin/cohorts", None),
    ("post", "/api/admin/cohorts", {"name": "X", "password": "pw-x", "access_until": FUTURE}),
    ("patch", "/api/admin/cohorts/1", {"name": "Y"}),
    ("post", "/api/admin/cohorts/1/import", {"text": "123456789, Fake Name"}),
    ("get", "/api/admin/cohorts/1/students", None),
]


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_anonymous_gets_401_on_every_admin_route(env, method, path, body):
    resp = getattr(env.client, method)(path, **({"json": body} if body is not None else {}))
    assert resp.status_code == 401, resp.text


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_student_session_gets_403_on_every_admin_route(env, method, path, body):
    student = env.student_client()
    resp = getattr(student, method)(path, **({"json": body} if body is not None else {}))
    assert resp.status_code == 403, resp.text


def test_a_refused_student_cannot_create_a_cohort(env):
    student = env.student_client()
    name = f"Sneaky {next(_n)}"
    body = {"name": name, "password": "pw-x", "access_until": FUTURE}
    student.post("/api/admin/cohorts", json=body)
    assert name not in [c["name"] for c in listed(env.admin())]


# ---------------------------------------------------------------- no database needed


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_admin_routes_exist_and_refuse_a_request_without_a_session(method, path, body):
    """Runs without Postgres: no cookie means 401 before any database access."""
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app, base_url="https://testserver")  # no lifespan: no pool needed
    resp = getattr(client, method)(path, **({"json": body} if body is not None else {}))
    assert resp.status_code == 401, f"{method.upper()} {path} -> {resp.status_code}"


# ---------------------------------------------------------------- list


def test_list_shows_cohorts_with_name_date_and_student_count(env, admin):
    cid = env.seed_cohort("List me", TODAY + 12 * DAY)
    env.seed_student(cid)
    env.seed_student(cid)
    mine = [c for c in listed(admin) if c["id"] == cid]
    assert len(mine) == 1
    assert mine[0]["name"] == "List me"
    assert mine[0]["access_until"] == (TODAY + 12 * DAY).isoformat()
    assert mine[0]["students"] == 2


def test_list_never_contains_a_password_or_hash(env, admin):
    env.seed_cohort()
    text = admin.get("/api/admin/cohorts").text
    assert "argon2" not in text and "$" not in text
    assert COHORT_PASSWORD not in text
    for c in listed(admin):
        assert not [k for k in c if "pass" in k.lower() or "hash" in k.lower()], c


def test_smoke_cohort_is_never_listed(env, admin):
    cid = env.smoke_cohort()
    shown = listed(admin)
    assert cid not in [c["id"] for c in shown]
    assert SMOKE not in [c["name"].strip().lower() for c in shown]


def test_smoke_cohort_is_unreachable_by_id(env, admin):
    cid = env.smoke_cohort()
    assert admin.patch(f"/api/admin/cohorts/{cid}", json={"name": "Renamed"}).status_code == 404
    assert do_import(admin, cid, "123456789, Fake Name").status_code == 404
    assert admin.get(f"/api/admin/cohorts/{cid}/students").status_code == 404


# ---------------------------------------------------------------- create


def test_create_returns_201_and_the_cohort_appears_in_the_list(env, admin):
    created = new_cohort(admin, name="  Spring group  ", until=FUTURE)
    assert created["name"] == "Spring group"
    assert created["access_until"] == FUTURE
    assert created["students"] == 0
    assert "password" not in created and "password_hash" not in created
    assert [c for c in listed(admin) if c["id"] == created["id"]] == [created]


def test_create_stores_only_a_hash_of_the_password(env, admin):
    created = new_cohort(admin)
    row = env.cohort_row(created["id"])
    assert row["password_hash"] != COHORT_PASSWORD
    assert COHORT_PASSWORD not in row["password_hash"]
    assert row["password_hash"].startswith("$argon2")


def test_a_student_in_a_created_cohort_logs_in_with_its_password(env, admin):
    created = new_cohort(admin, password=COHORT_PASSWORD)
    raw = fresh_id()
    assert import_ok(admin, created["id"], f"{raw}, Fake Learner")["added"] == 1
    resp = env.new_client().post("/api/auth/login", json={"id": raw, "password": COHORT_PASSWORD})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"kind": "student", "name": "Fake Learner"}


@pytest.mark.parametrize("name", ["lf-smoke", "LF-SMOKE", "  Lf-Smoke  ", "lf-smoke\t"])
def test_create_refuses_the_reserved_smoke_name(env, admin, name):
    async def count(conn):
        return await conn.fetchval(
            f"select count(*) from {SCHEMA}.cohorts where lower(trim(name)) = 'lf-smoke'"
        )

    # The smoke exams share this database and may already hold the real lf-smoke cohort,
    # so compare before and after instead of expecting none.
    before = env.sql(count)
    body = {"name": name, "password": "pw-x", "access_until": FUTURE}
    resp = admin.post("/api/admin/cohorts", json=body)
    assert resp.status_code == 422, resp.text
    assert env.sql(count) == before


@pytest.mark.parametrize(
    "body",
    [
        {"name": "", "password": "pw-x", "access_until": FUTURE},
        {"name": "   ", "password": "pw-x", "access_until": FUTURE},
        {"password": "pw-x", "access_until": FUTURE},
        {"name": "Ok name", "password": "", "access_until": FUTURE},
        {"name": "Ok name", "access_until": FUTURE},
        {"name": "Ok name", "password": "pw-x", "access_until": ""},
        {"name": "Ok name", "password": "pw-x"},
        {"name": "Ok name", "password": "pw-x", "access_until": "next friday"},
        {"name": "Ok name", "password": "pw-x", "access_until": "2026-13-45"},
        {"name": "Ok name", "password": "pw-x", "access_until": "2026-02-30"},
    ],
)
def test_create_validates_name_password_and_date(env, admin, body):
    before = len(listed(admin))
    resp = admin.post("/api/admin/cohorts", json=body)
    assert resp.status_code == 422, resp.text
    assert len(listed(admin)) == before


# ---------------------------------------------------------------- edit


def test_edit_changes_name_and_access_date(env, admin):
    cid = new_cohort(admin)["id"]
    new_date = (TODAY + 90 * DAY).isoformat()
    resp = admin.patch(
        f"/api/admin/cohorts/{cid}", json={"name": " Autumn group ", "access_until": new_date}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Autumn group"
    assert resp.json()["access_until"] == new_date
    again = [c for c in listed(admin) if c["id"] == cid][0]
    assert again["name"] == "Autumn group" and again["access_until"] == new_date


def test_edit_one_field_leaves_the_others_alone(env, admin):
    created = new_cohort(admin, name="Keep me", until=FUTURE)
    resp = admin.patch(f"/api/admin/cohorts/{created['id']}", json={"name": "Renamed"})
    assert resp.status_code == 200
    assert resp.json()["access_until"] == FUTURE


def test_edit_response_has_no_password_or_hash(env, admin):
    cid = new_cohort(admin)["id"]
    resp = admin.patch(f"/api/admin/cohorts/{cid}", json={"password": NEW_PASSWORD})
    assert resp.status_code == 200
    assert NEW_PASSWORD not in resp.text and "$" not in resp.text
    assert not [k for k in resp.json() if "pass" in k.lower() or "hash" in k.lower()]


def test_changed_password_works_and_the_old_one_stops_working(env, admin):
    cid = new_cohort(admin, password=COHORT_PASSWORD)["id"]
    raw = fresh_id()
    import_ok(admin, cid, f"{raw}, Fake Learner")
    first = env.new_client().post("/api/auth/login", json={"id": raw, "password": COHORT_PASSWORD})
    assert first.status_code == 200
    changed = admin.patch(f"/api/admin/cohorts/{cid}", json={"password": NEW_PASSWORD})
    assert changed.status_code == 200
    old = env.new_client().post("/api/auth/login", json={"id": raw, "password": COHORT_PASSWORD})
    assert old.status_code == 401
    new = env.new_client().post("/api/auth/login", json={"id": raw, "password": NEW_PASSWORD})
    assert new.status_code == 200, new.text


def test_edit_access_date_in_the_past_expires_logins(env, admin):
    cid = new_cohort(admin)["id"]
    raw = fresh_id()
    import_ok(admin, cid, f"{raw}, Fake Learner")
    past = (TODAY - 2 * DAY).isoformat()
    assert admin.patch(f"/api/admin/cohorts/{cid}", json={"access_until": past}).status_code == 200
    resp = env.new_client().post("/api/auth/login", json={"id": raw, "password": COHORT_PASSWORD})
    assert resp.status_code == 403
    assert resp.json() == {"detail": "access_expired"}


@pytest.mark.parametrize("name", ["lf-smoke", "LF-Smoke", "  lf-smoke "])
def test_edit_refuses_a_rename_to_the_smoke_name(env, admin, name):
    created = new_cohort(admin, name="Normal name")
    resp = admin.patch(f"/api/admin/cohorts/{created['id']}", json={"name": name})
    assert resp.status_code == 422, resp.text
    assert [c for c in listed(admin) if c["id"] == created["id"]][0]["name"] == "Normal name"


@pytest.mark.parametrize(
    "body",
    [{"name": ""}, {"name": "  "}, {"password": ""}, {"access_until": "tomorrow"},
     {"access_until": "2026-02-30"}, {"access_until": ""}],
)
def test_edit_validates_values(env, admin, body):
    created = new_cohort(admin, name="Stable")
    resp = admin.patch(f"/api/admin/cohorts/{created['id']}", json=body)
    assert resp.status_code == 422, resp.text
    assert [c for c in listed(admin) if c["id"] == created["id"]] == [created]


def test_edit_unknown_cohort_is_404(env, admin):
    assert admin.patch("/api/admin/cohorts/99999999", json={"name": "Nope"}).status_code == 404


# ---------------------------------------------------------------- import


def test_import_adds_students_and_reports_one_row_per_line(env, admin):
    cid = new_cohort(admin)["id"]
    a, b = fresh_id(), fresh_id()
    out = import_ok(admin, cid, f"{a}, Fake Alpha\n{b},Fake Beta\n")
    assert out["added"] == 2
    assert [(r["line"], r["status"]) for r in out["rows"]] == [(1, "added"), (2, "added")]
    assert [s["name"] for s in students(admin, cid)] == ["Fake Alpha", "Fake Beta"]
    assert [c for c in listed(admin) if c["id"] == cid][0]["students"] == 2


def test_import_keeps_the_rest_of_the_name_after_the_first_comma(env, admin):
    cid = new_cohort(admin)["id"]
    import_ok(admin, cid, f"{fresh_id()},  Fake, Surname  ")
    assert [s["name"] for s in students(admin, cid)] == ["Fake, Surname"]


def test_import_normalizes_ids_spaces_and_leading_zeros(env, admin):
    cid = new_cohort(admin)["id"]
    norm = fresh_id()
    messy = f" 00{norm[:4]} {norm[4:]} "
    assert import_ok(admin, cid, f"{messy}, Fake Messy")["added"] == 1
    resp = env.new_client().post("/api/auth/login", json={"id": norm, "password": COHORT_PASSWORD})
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Fake Messy"


def test_import_stores_the_hmac_of_the_normalized_id(env, admin):
    cid = new_cohort(admin)["id"]
    raw = fresh_id()
    import_ok(admin, cid, f"{raw}, Fake Hashed")
    rows = env.student_rows(cid)
    assert [r["id_hash"] for r in rows] == [id_hash(raw)]
    assert [r["name"] for r in rows] == ["Fake Hashed"]
    assert rows[0]["disabled"] is False and rows[0]["access_until"] is None


def test_import_skips_blank_lines_and_numbers_lines_as_pasted(env, admin):
    cid = new_cohort(admin)["id"]
    text = f"\n{fresh_id()}, Fake One\n\n   \n{fresh_id()}, Fake Two\r\n"
    out = import_ok(admin, cid, text)
    assert [(r["line"], r["status"]) for r in out["rows"]] == [(2, "added"), (5, "added")]
    assert out["added"] == 2


@pytest.mark.parametrize(
    "line",
    ["abc123456, Fake Name", "12 34x, Fake Name", "000000, Fake Name",
     "٣٤٥٦٧٨٩, Fake Name", "1" * 33 + ", Fake Name", ", Fake Name"],
)
def test_import_reports_an_invalid_id_per_row(env, admin, line):
    cid = new_cohort(admin)["id"]
    good = fresh_id()
    out = import_ok(admin, cid, f"{good}, Fake Good\n{line}\n")
    assert out["added"] == 1
    assert out["rows"][0]["status"] == "added"
    bad = out["rows"][1]
    assert bad["line"] == 2 and bad["status"] == "error" and bad["code"] == "invalid_id"
    assert bad["message"].strip()
    assert [s["name"] for s in students(admin, cid)] == ["Fake Good"]


def test_import_accepts_the_longest_valid_id(env, admin):
    cid = new_cohort(admin)["id"]
    assert import_ok(admin, cid, "7" * 32 + ", Fake Long")["added"] == 1


def test_import_reports_a_missing_name_per_row(env, admin):
    cid = new_cohort(admin)["id"]
    out = import_ok(admin, cid, f"{fresh_id()}\n{fresh_id()},   \n{fresh_id()}, Fake Ok")
    assert out["added"] == 1
    assert [(r["line"], r["status"]) for r in out["rows"]] == [
        (1, "error"), (2, "error"), (3, "added")]
    assert [r["code"] for r in out["rows"][:2]] == ["missing_name", "missing_name"]
    assert all(r["message"].strip() for r in out["rows"][:2])


def test_import_reports_duplicates_within_the_paste(env, admin):
    cid = new_cohort(admin)["id"]
    dup = fresh_id()
    text = f"{dup}, Fake First\n{fresh_id()}, Fake Other\n0{dup[:3]} {dup[3:]}, Fake Second"
    out = import_ok(admin, cid, text)
    assert out["added"] == 2
    assert [r["status"] for r in out["rows"]] == ["added", "added", "error"]
    assert out["rows"][2]["code"] == "duplicate_in_paste" and out["rows"][2]["message"].strip()
    names = [s["name"] for s in students(admin, cid)]
    assert "Fake Second" not in names and "Fake First" in names


def test_import_reports_ids_that_already_exist_in_the_same_cohort(env, admin):
    cid = new_cohort(admin)["id"]
    existing, _ = env.seed_student(cid, name="Fake Existing")
    out = import_ok(admin, cid, f"{existing}, Fake Changed\n{fresh_id()}, Fake New")
    assert out["added"] == 1
    assert out["rows"][0]["status"] == "error" and out["rows"][0]["code"] == "already_exists"
    assert out["rows"][0]["message"].strip()
    assert sorted(s["name"] for s in students(admin, cid)) == ["Fake Existing", "Fake New"]


def test_import_reports_ids_that_exist_in_another_cohort(env, admin):
    other = new_cohort(admin)["id"]
    mine = new_cohort(admin)["id"]
    existing, _ = env.seed_student(other, name="Fake Elsewhere")
    out = import_ok(admin, mine, f"{existing}, Fake Moved")
    assert out["added"] == 0
    assert out["rows"][0]["code"] == "already_exists"
    assert students(admin, mine) == []
    assert [s["name"] for s in students(admin, other)] == ["Fake Elsewhere"]


def test_importing_the_same_paste_twice_adds_nothing_the_second_time(env, admin):
    cid = new_cohort(admin)["id"]
    text = f"{fresh_id()}, Fake A\n{fresh_id()}, Fake B"
    assert import_ok(admin, cid, text)["added"] == 2
    again = import_ok(admin, cid, text)
    assert again["added"] == 0
    assert [r["code"] for r in again["rows"]] == ["already_exists", "already_exists"]
    assert len(students(admin, cid)) == 2


def test_import_of_an_empty_paste_adds_nothing(env, admin):
    cid = new_cohort(admin)["id"]
    assert import_ok(admin, cid, " \n\n") == {"added": 0, "rows": []}


def test_import_into_an_unknown_cohort_is_404(env, admin):
    assert do_import(admin, 99999999, f"{fresh_id()}, Fake Name").status_code == 404


def test_import_without_text_is_422(env, admin):
    cid = new_cohort(admin)["id"]
    assert admin.post(f"/api/admin/cohorts/{cid}/import", json={}).status_code == 422


def test_raw_ids_and_the_pasted_text_never_reach_the_logs(env, admin, caplog):
    cid = new_cohort(admin)["id"]
    ok1, ok2, present = fresh_id(), fresh_id(), fresh_id()
    env.seed_student(cid, raw=present, name="Fake Present")
    text = (
        f"{ok1}, Fake Logged One\n{ok2}, Fake Logged Two\n{present}, Fake Dup\n"
        f"{ok1}, Fake Dup Two\nnot-an-id-77, Fake Broken\n"
    )
    with caplog.at_level(logging.DEBUG):
        out = import_ok(admin, cid, text)
        students(admin, cid)
    assert out["added"] == 2
    logged = "\n".join(
        r.getMessage() + " " + str(r.args) + " " + (str(r.exc_info) if r.exc_info else "")
        for r in caplog.records
    )
    for secret in (ok1, ok2, present, "not-an-id-77", "Fake Logged One", text.strip()):
        assert secret not in logged, f"{secret!r} was logged"


def test_raw_ids_and_the_pasted_text_are_in_no_database_column(env, admin):
    cid = new_cohort(admin)["id"]
    ids = [fresh_id() for _ in range(3)]
    lines = [f"{i}, Fake Stored {n}" for n, i in enumerate(ids)]
    text = "\n".join(lines) + "\nbroken-id-55, Fake Bad"
    import_ok(admin, cid, text)
    dump = env.dump_everything()
    for i in ids:
        assert i not in dump, "a raw id is stored"
    assert "broken-id-55" not in dump
    assert text not in dump
    assert id_hash(ids[0]) in dump  # the dump does cover the students table


# ---------------------------------------------------------------- students list


def test_students_list_shows_name_note_disabled_and_access_override(env, admin):
    cid = new_cohort(admin)["id"]
    until = TODAY + 5 * DAY
    env.seed_student(cid, name="Fake Zed", note="needs a seat near the door", disabled=True,
                     access_until=until)
    env.seed_student(cid, name="Fake Amy")
    shown = students(admin, cid)
    assert [s["name"] for s in shown] == ["Fake Amy", "Fake Zed"]
    amy, zed = shown
    assert amy["note"] is None and amy["disabled"] is False and amy["access_until"] is None
    assert zed["note"] == "needs a seat near the door"
    assert zed["disabled"] is True
    assert zed["access_until"] == until.isoformat()


def test_students_list_has_no_raw_ids_and_only_this_cohorts_students(env, admin):
    cid = new_cohort(admin)["id"]
    other = new_cohort(admin)["id"]
    raw, _ = env.seed_student(cid, name="Fake Mine")
    other_raw, _ = env.seed_student(other, name="Fake Theirs")
    resp = admin.get(f"/api/admin/cohorts/{cid}/students")
    assert resp.status_code == 200
    assert raw not in resp.text and other_raw not in resp.text
    assert [s["name"] for s in resp.json()] == ["Fake Mine"]


def test_students_list_of_an_empty_cohort_is_an_empty_array(env, admin):
    assert students(admin, new_cohort(admin)["id"]) == []


def test_students_list_of_an_unknown_cohort_is_404(env, admin):
    assert admin.get("/api/admin/cohorts/99999999/students").status_code == 404
