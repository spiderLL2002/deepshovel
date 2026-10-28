"""Bounded local-mask reuse, invalidation, and serialized SDK mutation."""

import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from qdrant_client.http import models
from qdrant_client.local import payload_filters
from test_retrieval_service import _scope

from compiler_service import qdrant_local_compat as compat
from compiler_service.retrieval_contract import SearchScope
from compiler_service.retrieval_store import QdrantVectorIndex


@pytest.fixture
def index(tmp_path):
    result = QdrantVectorIndex(
        data_dir=tmp_path, model_name="mask-fixture", model_revision="1", dimension=2
    )
    yield result
    result._client.close()


def put(index, claim="one", *, kind="measurement", vector=(1.0, 0.0)):
    index.upsert(
        project_id="p",
        snapshot_id="s",
        claim_ids=[claim],
        claim_types=[kind],
        polarities=["positive"],
        scopes=[SearchScope.model_validate(_scope())],
        vectors=[vector],
    )


def query(index, *, types=(), vector=(1.0, 0.0)):
    return index.search(
        project_id="p",
        snapshot_ids=["s"],
        vector=vector,
        limit=10,
        claim_types=types,
        polarity=None,
        scope=None,
    )


def count_masks(monkeypatch):
    calls = []
    original = compat._ORIGINAL_PAYLOAD_MASK

    def counted(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(compat, "_ORIGINAL_PAYLOAD_MASK", counted)
    return calls


def test_repeated_views_filter_changes_and_all_mutations(index, monkeypatch):
    put(index)
    calls = count_masks(monkeypatch)
    first = query(index)
    assert query(index) == first
    query(index, vector=(0.0, 1.0))
    assert len(calls) == 1
    assert not query(index, types=["other"])
    assert len(calls) == 2
    put(index, "two", vector=(0.0, 1.0))
    assert len(query(index)) == 2
    assert len(calls) == 3
    # Same point ID replaces its payload without changing payload-list length.
    put(index, kind="other")
    assert len(query(index, types=["other"])) == 1
    assert len(calls) == 4
    index.delete_snapshots(
        project_id="p", snapshot_ids=["s"], collections=[index.collection_name]
    )
    # The delete SDK call itself also evaluates its uncached selection mask.
    after_delete = len(calls)
    assert not query(index)
    assert len(calls) == after_delete + 1
    assert compat._MASK_CACHE.get() is None


def test_partial_sdk_write_failure_cannot_leave_stale_mask(index, monkeypatch):
    put(index)
    assert not query(index, types=["other"])
    original = index._client.upsert

    def partial_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected after write")

    monkeypatch.setattr(index._client, "upsert", partial_then_fail)
    with pytest.raises(RuntimeError, match="injected after write"):
        put(index, kind="other")
    assert len(query(index, types=["other"])) == 1
    assert compat._MASK_CACHE.get() is None


def test_query_and_mutation_cannot_interleave(index, monkeypatch):
    put(index)
    entered, release, mutation_started, mutated = (threading.Event() for _ in range(4))
    original = compat._ORIGINAL_PAYLOAD_MASK

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(compat, "_ORIGINAL_PAYLOAD_MASK", blocked)

    def mutate():
        mutation_started.set()
        put(index, "two")
        mutated.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        reading = pool.submit(query, index)
        assert entered.wait(5)
        writing = pool.submit(mutate)
        assert mutation_started.wait(5)
        try:
            assert not mutated.wait(0.05)
        finally:
            release.set()
        assert len(reading.result(timeout=5)) == 1
        writing.result(timeout=5)
    assert len(query(index)) == 2


def test_lru_byte_limit_full_filter_and_collection_identity(monkeypatch):
    compat.install_read_only_local_cosine_patch()
    calls = count_masks(monkeypatch)
    cache = compat.PayloadMaskCache(max_entries=2, max_bytes=8)
    payloads, ids, deleted = (
        [{"n": 1}] * 4,
        list(range(4)),
        {"": np.zeros(4, dtype=bool)},
    )
    filters = [
        models.Filter(
            must=[models.FieldCondition(key="n", match=models.MatchValue(value=v))]
        )
        for v in (1, 2, 3)
    ]
    with compat.payload_mask_cache_scope(cache):

        def evaluate(f, p=payloads):
            actual = payload_filters.calculate_payload_mask(p, f, ids, deleted)
            expected = compat._ORIGINAL_PAYLOAD_MASK(p, f, ids, deleted)
            np.testing.assert_array_equal(actual, expected)
            assert not actual.flags.writeable
            return actual

        first = evaluate(filters[0])
        assert evaluate(filters[0]) is first
        evaluate(filters[1])
        evaluate(filters[2])
        assert len(cache.entries) == 2 and cache.nbytes == 8
        assert evaluate(filters[0]) is not first
        assert evaluate(filters[0], list(payloads)) is not first
    # Counts include explicit official comparison calls, but the repeated mask
    # above must have skipped one calculation.
    assert len(calls) == 11
    oversized = compat.PayloadMaskCache(max_bytes=1)
    with compat.payload_mask_cache_scope(oversized):
        payload_filters.calculate_payload_mask(payloads, None, ids, deleted)
    assert not oversized.entries and oversized.nbytes == 0
    with pytest.raises(RuntimeError), compat.payload_mask_cache_scope(cache):
        raise RuntimeError("scope failure")
    assert compat._MASK_CACHE.get() is None
