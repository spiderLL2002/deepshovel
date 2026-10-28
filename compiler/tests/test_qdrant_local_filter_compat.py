"""Exact local-filter equivalence without opening a Qdrant index."""

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from qdrant_client.http import models
from qdrant_client.local import payload_filters

from compiler_service import qdrant_local_compat as compat


@pytest.fixture(autouse=True)
def installed():
    compat.install_read_only_local_cosine_patch()


def assert_mask_matches(payloads, query):
    arguments = (payloads, query, list(range(len(payloads))), {})
    expected = compat._ORIGINAL_PAYLOAD_MASK(*arguments)
    actual = payload_filters.calculate_payload_mask(*arguments)
    np.testing.assert_array_equal(actual, expected)
    assert compat._MATCH_ANY_CACHE.get() is None
    return actual


@pytest.mark.parametrize(
    "operands",
    [list(range(64)), [str(i) for i in range(64)], [1, 2], []],
)
def test_match_any_preserves_scalar_and_array_types(operands):
    values = [None, False, True, 0, 1, 1.0, "1", "99", 63, 64, [1, "1"], {}, []]
    payloads = [{"value": value} for value in values] + [{}]
    query = models.Filter(
        must=[models.FieldCondition(key="value", match=models.MatchAny(any=operands))]
    )
    assert_mask_matches(payloads, query)


def test_nested_boolean_range_except_and_deleted_vector_semantics():
    query = models.Filter(
        must=[
            models.NestedCondition(
                nested=models.Nested(
                    key="items",
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="snapshot",
                                match=models.MatchAny(any=list(range(64))),
                            )
                        ],
                        must_not=[
                            models.FieldCondition(
                                key="kind",
                                match=models.MatchExcept.model_validate(
                                    {"except": ["keep"]}
                                ),
                            )
                        ],
                    ),
                )
            ),
            models.FieldCondition(key="score", range=models.Range(gte=2, lt=8)),
        ],
        min_should=models.MinShould(
            conditions=[
                models.FieldCondition(
                    key="tag", match=models.MatchAny(any=[str(i) for i in range(64)])
                )
            ],
            min_count=1,
        ),
    )
    payloads = [
        {
            "items": [{"snapshot": i % 67, "kind": "keep" if i % 2 else "drop"}],
            "score": i % 10,
            "tag": str(i % 67),
        }
        for i in range(100)
    ]
    assert_mask_matches(payloads, query)
    vector_query = models.Filter(must=[query, models.HasVectorCondition(has_vector="")])
    arguments = (
        payloads,
        vector_query,
        list(range(100)),
        {"": np.arange(100) % 3 == 0},
    )
    np.testing.assert_array_equal(
        payload_filters.calculate_payload_mask(*arguments),
        compat._ORIGINAL_PAYLOAD_MASK(*arguments),
    )


def test_unsupported_operands_use_official_function():
    # Guard future SDK/constructed conditions outside audited pure-str/int sets.
    conditions = [
        models.MatchAny.model_construct(any=[True] * 64),
        models.MatchAny.model_construct(any=[1, "1"] * 32),
        models.MatchExcept.model_validate({"except": list(range(64))}),
    ]
    token = compat._MATCH_ANY_CACHE.set({})
    try:
        for condition in conditions:
            for value in [None, False, True, 1, 1.0, "1", 80]:
                assert compat._compiled_check_match(condition, value) == (
                    compat._ORIGINAL_CHECK_MATCH(condition, value)
                )
    finally:
        compat._MATCH_ANY_CACHE.reset(token)


def test_large_operand_list_is_compiled_once_per_query():
    class CountedList(list):
        iterations = 0

        def __iter__(self):
            self.iterations += 1
            return super().__iter__()

    operands = CountedList(range(1500))
    condition = models.MatchAny.model_construct(any=operands)
    query = models.Filter(must=[models.FieldCondition(key="value", match=condition)])
    payloads = [{"value": i} for i in range(100)]
    for expected_iterations in (2, 4):
        result = payload_filters.calculate_payload_mask(
            payloads, query, list(range(100)), {}
        )
        assert result.all()
        assert operands.iterations == expected_iterations
        assert compat._MATCH_ANY_CACHE.get() is None


def test_query_cache_isolated_between_threads_and_cleared_after_error(monkeypatch):
    def evaluate(offset):
        query = models.Filter(
            must=[
                models.FieldCondition(
                    key="value",
                    match=models.MatchAny(any=list(range(offset, offset + 64))),
                )
            ]
        )
        return assert_mask_matches([{"value": i} for i in range(128)], query).sum()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(evaluate, [0, 64, 0, 64])) == [64] * 4

    def fail(*args, **kwargs):
        raise RuntimeError("fixture failure")

    monkeypatch.setattr(compat, "_ORIGINAL_PAYLOAD_MASK", fail)
    with pytest.raises(RuntimeError, match="fixture failure"):
        payload_filters.calculate_payload_mask([], None, [], {})
    assert compat._MATCH_ANY_CACHE.get() is None


def test_payload_source_version_gate_and_idempotent_install(monkeypatch):
    first = compat.install_read_only_local_cosine_patch()
    assert compat.install_read_only_local_cosine_patch() == first
    monkeypatch.setattr(compat, "_EXPECTED_PAYLOAD_FILTERS_SHA256", "invalid")
    with pytest.raises(RuntimeError, match="payload filters differ"):
        compat.install_read_only_local_cosine_patch()
