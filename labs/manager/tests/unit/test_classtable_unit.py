from pathlib import Path

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
