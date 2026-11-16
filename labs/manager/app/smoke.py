"""The reserved smoke cohort (D36/D54): one definition for the Python check and the SQL filter."""

SMOKE_COHORT = "lf-smoke"

# Left out of every teacher query that joins `cohorts c`.
NOT_SMOKE_SQL = f"lower(trim(c.name)) <> '{SMOKE_COHORT}'"


def is_smoke_cohort(name: object) -> bool:
    """True for the reserved name, in any case and with any padding."""
    return isinstance(name, str) and name.strip().lower() == SMOKE_COHORT
