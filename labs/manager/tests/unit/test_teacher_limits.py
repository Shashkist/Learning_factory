import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import quiet_validation_error
from app.smoke import NOT_SMOKE_SQL, is_smoke_cohort
from app.teacher import CohortEdit, ImportBody, NewCohort, split_lines


def test_tab_split_when_no_comma():
    (ln,) = split_lines("123456789\t  Fake Name  ")
    assert (ln.id, ln.name) == ("123456789", "Fake Name")


def test_comma_wins_over_tab():
    (ln,) = split_lines("123456789, Fake\tName")
    assert (ln.id, ln.name) == ("123456789", "Fake\tName")


def test_line_cap():
    ImportBody(text="\n".join("1, A" for _ in range(2000)) + "\n\n  \n")
    with pytest.raises(ValidationError):
        ImportBody(text="\n".join("1, A" for _ in range(2001)))


@pytest.mark.parametrize("pw", ["   ", "\t", " \t \n "])
def test_blank_password_refused(pw):
    with pytest.raises(ValidationError):
        CohortEdit(password=pw)
    with pytest.raises(ValidationError):
        NewCohort(name="Fake", password=pw, access_until="2027-01-01")


def test_padded_password_ok():
    assert CohortEdit(password="  pw ").password == "  pw "


@pytest.mark.parametrize("day", ["2019-12-31", "0001-01-01"])
def test_date_floor(day):
    with pytest.raises(ValidationError):
        CohortEdit(access_until=day)


def test_date_floor_itself_ok():
    assert CohortEdit(access_until="2020-01-01").access_until.isoformat() == "2020-01-01"


def test_smoke_check():
    assert is_smoke_cohort("  LF-Smoke ")
    assert not is_smoke_cohort("Fake") and not is_smoke_cohort(None)
    assert "lf-smoke" in NOT_SMOKE_SQL


def test_422_has_no_input_or_ctx():
    small = FastAPI()
    small.add_exception_handler(RequestValidationError, quiet_validation_error)

    @small.post("/c")
    def create(body: NewCohort) -> None:
        return None

    @small.post("/i")
    def imp(body: ImportBody) -> None:
        return None

    client = TestClient(small)
    secret = "SENTINEL-172-xyz"
    bad = {"name": "Z" * 300, "password": secret, "access_until": "x" + secret}
    for path, body in [("/i", {"text": 172987654321}), ("/c", bad)]:
        resp = client.post(path, json=body)
        assert resp.status_code == 422
        assert "input" not in resp.text and "ctx" not in resp.text
        assert secret not in resp.text and "ZZZZ" not in resp.text
