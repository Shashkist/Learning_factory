from pathlib import Path

import pytest

from app.classtable import cell_status, csv_text, published
from app.content import Lab, Task


def lab(lab_id="x", status="published", module=1, lesson=1, tasks=("a", "b")):
    return Lab(
        id=lab_id,
        title={"en": "T", "he": "כ"},
        status=status,
        image="term",
        module=module,
        lesson=lesson,
        path=Path("."),
        tasks=[Task(id=t, kind="hands-on") for t in tasks],
    )


def test_cell_status():
    lb = lab()
    assert cell_status(lb, set(), False) == "none"
    assert cell_status(lb, {"a"}, False) == "1/2"
    assert cell_status(lb, {"a", "gone"}, False) == "1/2"
    assert cell_status(lb, {"gone"}, False) == "none"
    assert cell_status(lb, set(), True) == "done"


def test_published_order():
    labs = {
        "d": lab("d", status="draft"),
        "b": lab("b", module=2, lesson=1),
        "a": lab("a", module=1, lesson=9),
        "c": lab("c", module=1, lesson=2),
    }
    assert [x.id for x in published(labs)] == ["c", "a", "b"]


def test_csv_text_quotes_and_flags():
    table = {
        "labs": [{"id": "l1"}, {"id": "l2"}],
        "rows": [
            {
                "name": 'Fake, "Q"',
                "cohort": "C",
                "last_activity": None,
                "cells": {
                    "l1": {"status": "2/3", "flag": True},
                    "l2": {"status": "none", "flag": False},
                },
            }
        ],
    }
    lines = csv_text(table).splitlines()
    assert lines[0] == "name,cohort,last_activity,l1,l2"
    assert lines[1] == '"Fake, ""Q""",C,,2/3 ⚡,none'


def test_csv_text_defuses_formulas():
    row = {"name": '=HYPERLINK("x")', "cohort": "-c", "last_activity": None, "cells": {}}
    lines = csv_text({"labs": [], "rows": [row]}).splitlines()
    assert lines[1] == "\"'=HYPERLINK(\"\"x\"\")\",'-c,"
    plain = {"name": "Fake Dana", "cohort": "C", "last_activity": None, "cells": {}}
    assert csv_text({"labs": [], "rows": [plain]}).splitlines()[1] == "Fake Dana,C,"


@pytest.mark.parametrize("start", ["=", "+", "-", "@", "\t", "\r"])
def test_csv_text_defuses_every_formula_start(start: str):
    row = {"name": f"{start}x", "cohort": "C", "last_activity": None, "cells": {}}
    out = csv_text({"labs": [], "rows": [row]})
    assert f"'{start}x" in out


@pytest.mark.parametrize("cohort", [0, -1, 2**31])
def test_out_of_range_cohort_is_unknown_not_a_db_error(cohort: int):
    import asyncio
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.classtable import build

    class NoPool:
        async def acquire(self, timeout=None):
            raise AssertionError("the database must not be asked")

        def __getattr__(self, name):
            raise AssertionError("the database must not be asked")

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(pool=NoPool(), labs={})))
    with pytest.raises(HTTPException) as e:
        asyncio.run(build(request, cohort, None))  # type: ignore[arg-type]
    assert (e.value.status_code, e.value.detail) == (404, "unknown_cohort")
