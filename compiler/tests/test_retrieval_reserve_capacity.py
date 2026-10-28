"""Focused fake-provider checks for the selective reserve capacity guard."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from test_retrieval_service import (
    FakeDense,
    FakeVectorIndex,
    _candidate,
    _document,
    _index_request,
    _manifest,
    _search_request,
    _service,
)

from compiler_service import retrieval_service as retrieval
from compiler_service.retrieval_store import ChannelHit


@pytest.mark.parametrize("protected_seats", [(11, 12), (11,), (12,), ()])
def test_capacity_guard_keeps_exact_preview_and_available_promotions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    protected_seats: tuple[int, ...],
) -> None:
    dense, vector = FakeDense(), FakeVectorIndex()
    service = _service(
        tmp_path,
        dense=dense,
        vector=vector,
        query_plan_handoff_secret="capacity-test-only-not-a-real-credential",
        query_plan_handoff_clock=lambda: 1000.0,
    )
    documents = [
        _document(f"claim-capacity-{index:02d}", f"capacity evidence {index}")
        for index in range(1, 15)
    ]
    service.index_snapshot(_index_request(_manifest(document_count=14), documents))
    baseline = [
        ChannelHit("snapshot-1", document["claim_id"], 1.0 - index / 100.0)
        for index, document in enumerate(documents, 1)
    ]
    calls: Counter[str] = Counter()

    def lexical_search(**kwargs: Any) -> list[ChannelHit]:
        optional = kwargs["limit"] == 1
        calls["optional_lexical" if optional else "main_lexical"] += 1
        return [baseline[12]] if optional else list(baseline)

    def embed_query(text: str) -> list[float]:
        optional = text == "optional capacity wording"
        calls["optional_dense" if optional else "main_dense"] += 1
        return [0.0, 1.0] if optional else [1.0, 0.0]

    def vector_search(**kwargs: Any) -> list[ChannelHit]:
        optional = kwargs["vector"] == [0.0, 1.0]
        calls["optional_vector" if optional else "main_vector"] += 1
        return (
            [ChannelHit("snapshot-1", baseline[13].claim_id, 2.0)]
            if optional
            else list(baseline)
        )

    def protect_seats(
        candidates: list[Any],
        query: str,
        rrf_k: int,
        *,
        boosted_keys: set,
        **kwargs: Any,
    ) -> int:
        del candidates, query, rrf_k, kwargs
        boosted_keys.update(
            ("snapshot-1", f"claim-capacity-{seat:02d}") for seat in protected_seats
        )
        return len(protected_seats)

    monkeypatch.setattr(service.store, "lexical_search", lexical_search)
    monkeypatch.setattr(dense, "embed_query", embed_query)
    monkeypatch.setattr(vector, "search", vector_search)
    monkeypatch.setattr(retrieval, "_fuse_scientific_mentions", protect_seats)
    monkeypatch.setattr(
        retrieval,
        "_additional_dense_query_texts",
        lambda plan: ["optional capacity wording"],
    )
    request = _search_request(
        "capacity probe wording",
        schema_version="evidence-retrieval/2.1",
        stage="preview",
    )

    # A true capacity predicate reproduces the prior enabled-reserve guard.
    with monkeypatch.context() as old_guard:
        old_guard.setattr(
            retrieval,
            "_selective_safe_reserve_has_capacity",
            lambda *args, **kwargs: True,
        )
        old = service.search(request)
    assert calls == Counter(
        main_lexical=1,
        main_dense=1,
        main_vector=1,
        optional_lexical=1,
        optional_dense=1,
        optional_vector=1,
    )
    calls.clear()
    current = service.search(request)

    assert current.model_dump(mode="json") == old.model_dump(mode="json")
    assert current.query_plan_handoff is not None
    assert [
        (item.snapshot_id, item.claim_id, item.scores.rrf.hex())
        for item in current.preview
    ] == [
        (item.snapshot_id, item.claim_id, item.scores.rrf.hex()) for item in old.preview
    ]
    expected_ids = [f"claim-capacity-{index:02d}" for index in range(1, 11)]
    if len(protected_seats) == 2:
        expected_ids += ["claim-capacity-11", "claim-capacity-12"]
        assert calls == Counter(main_lexical=1, main_dense=1, main_vector=1)
    else:
        expected_ids += (
            [f"claim-capacity-{protected_seats[0]:02d}", "claim-capacity-13"]
            if protected_seats
            else ["claim-capacity-13", "claim-capacity-14"]
        )
        assert calls == Counter(
            main_lexical=1,
            main_dense=1,
            main_vector=1,
            optional_lexical=1,
            optional_dense=1,
            optional_vector=1,
        )
    # Reserve promotion reorders the first twelve seats; a larger current
    # preview still retains the remaining candidates after that boundary.
    assert [item.claim_id for item in current.preview[:12]] == expected_ids
    assert {item.claim_id for item in current.preview} == {
        document["claim_id"] for document in documents
    }


def test_capacity_uses_ordered_boundary_and_full_identity_without_mutation() -> None:
    candidates = [
        _candidate(_document(f"claim-{index}", "capacity evidence"), score=float(index))
        for index in range(14)
    ]
    original = [(id(item), item.rrf.hex()) for item in candidates]
    has_capacity = retrieval._selective_safe_reserve_has_capacity
    protected = {("snapshot-1", "claim-10"), ("snapshot-1", "claim-11")}
    assert not has_capacity(candidates, protected_keys=protected)
    assert has_capacity(
        candidates,
        protected_keys={("other-snapshot", "claim-10"), ("snapshot-1", "claim-11")},
    )
    assert has_capacity(candidates, protected_keys={("snapshot-1", "claim-0")})
    for size in (0, 11, 12):
        assert not has_capacity(candidates[:size], protected_keys=set())
    assert [(id(item), item.rrf.hex()) for item in candidates] == original
