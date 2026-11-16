"""The reserved smoke cohort (D36/D54, D61): one module for the Python checks and the SQL filter."""

SMOKE_COHORT = "lf-smoke"

# Left out of every teacher query that joins `cohorts c`.
NOT_SMOKE_SQL = f"lower(trim(c.name)) <> '{SMOKE_COHORT}'"


def is_smoke_cohort(name: object) -> bool:
    """The reserved name, in any case and with any padding: refused for teacher cohorts."""
    return isinstance(name, str) and name.strip().lower() == SMOKE_COHORT


def is_smoke_member(name: object) -> bool:
    """Access to the internal smoke lab: the exact cohort only, never a look-alike (#128)."""
    return isinstance(name, str) and name == SMOKE_COHORT
