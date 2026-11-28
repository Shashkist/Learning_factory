"""Pytest plugin: run one shard of the docker-marked exam (#727, D202).

Load with `-p docker_shard` and PYTHONPATH holding scripts/ci. With LF_DOCKER_SHARD=k/n, after
`-m` selection only the items of whole test files assigned to shard k stay; the rest are reported
as deselected. Files are packed by selected item count (largest first, then path) onto the shard
with the fewest items so far (ties to the lowest shard), so every shard computes the same
partition. LF_DOCKER_SHARD_MANIFEST=<file> gets {"shard", "shards", "files": {path: shard}}.
Unset LF_DOCKER_SHARD: no change. Any other malformed value stops pytest with a usage error.
"""
import json
import os
import re
from collections import Counter

import pytest

ENV = "LF_DOCKER_SHARD"
MANIFEST_ENV = "LF_DOCKER_SHARD_MANIFEST"


def parse(value: str) -> tuple[int, int]:
    m = re.fullmatch(r"([0-9]+)/([0-9]+)", value)
    if m and 1 <= int(m.group(1)) <= int(m.group(2)):
        return int(m.group(1)), int(m.group(2))
    raise pytest.UsageError(f"{ENV} must be <k>/<n> with 1 <= k <= n, got {value!r}")


def partition(counts: dict[str, int], shards: int) -> dict[str, int]:
    load = [0] * shards
    owner = {}
    for path in sorted(counts, key=lambda p: (-counts[p], p)):
        k = load.index(min(load))
        load[k] += counts[path]
        owner[path] = k + 1
    return owner


def pytest_configure(config):
    value = os.environ.get(ENV)
    if value is not None:
        parse(value)


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    value = os.environ.get(ENV)
    if value is None:
        return
    shard, shards = parse(value)

    def rel(item) -> str:
        return item.path.relative_to(config.rootpath).as_posix()

    owner = partition(dict(Counter(rel(i) for i in items)), shards)
    kept = [i for i in items if owner[rel(i)] == shard]
    dropped = [i for i in items if owner[rel(i)] != shard]
    if dropped:
        config.hook.pytest_deselected(items=dropped)
    items[:] = kept
    target = os.environ.get(MANIFEST_ENV)
    if target:
        with open(target, "w", encoding="utf-8") as f:
            json.dump({"shard": shard, "shards": shards, "files": owner}, f, sort_keys=True)
