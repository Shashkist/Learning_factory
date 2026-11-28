import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from docker_shard import parse, partition
from docker_shards_gate import _file_of


def test_parse_accepts_k_of_n_only():
    assert parse("2/4") == (2, 4)
    for bad in ("", "0/4", "5/4", "1/0", "1/4/4", "a/b", "4"):
        with pytest.raises(pytest.UsageError, match="LF_DOCKER_SHARD"):
            parse(bad)


def test_partition_packs_largest_first_ties_to_lowest_shard():
    counts = {"a.py": 5, "b.py": 3, "c.py": 3, "d.py": 2, "e.py": 1}
    assert partition(counts, 4) == {"a.py": 1, "b.py": 2, "c.py": 3, "d.py": 4, "e.py": 4}


def test_file_of_strips_class_names_and_prefers_known_files():
    case = {"classname": "tests.acceptance.test_a.TestX"}
    assert _file_of(case, set()) == "tests/acceptance/test_a.py"
    assert _file_of({"classname": "t.test_b"}, {"t/test_b.py"}) == "t/test_b.py"
    assert _file_of({"classname": "x", "file": "p/q.py"}, set()) == "p/q.py"
