from datetime import UTC, datetime
from pathlib import Path

from build_snapshot import Source, build

FIXTURES = Path(__file__).resolve().parents[1] / "acceptance" / "fixtures" / "issue18"
NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)


def test_public_omits_only_state():
    src = Source(FIXTURES)
    root = FIXTURES / "repo"
    full = build(src, root, NOW)
    pub = build(src, root, NOW, public=True)
    assert "state" in full and "state" not in pub
    assert pub == {k: v for k, v in full.items() if k != "state"}
