from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Callable, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from compiler_service.errors import (
    ContractIssue,
    InputError,
    ModelAttemptDiagnostic,
    ModelOutputError,
    ModelRequestErrorKind,
    QueryPlanHandoffError,
)
from compiler_service.retrieval_contract import (
    ClaimSearchDocument,
    HybridSearchRequest,
    IndexSnapshotRequest,
    ManifestCheckRequest,
    QueryPlan,
    QueryPlanPayload,
    RelationDecision,
    RetrievalConfidence,
    RetrievalManifest,
    SearchScopeFilter,
    relation_candidate_key,
)
from compiler_service.retrieval_providers import (
    DenseBatchIneligibleError,
    FastEmbedDenseEncoder,
)
from compiler_service.retrieval_service import (
    RetrievalService,
    _additional_dense_query_texts,
    _aggregate_dense_view_hits,
    _apply_guarded_cross_language_dense_reserve,
    _apply_selective_safe_top12_reserve,
    _apply_soft_family_diversity,
    _Candidate,
    _cluster_candidates,
    _discount_sparse_cjk_lexical_channel,
    _documents_cluster_compatible,
    _explicit_relation,
    _fuse_concept_anchors,
    _fuse_exact_anchors,
    _fuse_scientific_mentions,
    _retrieval_confidence_observation,
    _safe_relation_decision,
    _scope_filter,
    _scope_hard_match,
    _scope_relation,
    _time_disjoint,
)
from compiler_service.retrieval_store import ChannelHit, QdrantVectorIndex
from compiler_service.retrieval_text import fts_query


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _scope(
    *,
    location: list[str] | None = None,
    time: dict[str, Any] | None = None,
    entities: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "location": location or [],
        "time": time,
        "formation": [],
        "sample_type": [],
        "method": [],
        "condition": [],
        "measurement": [],
        "uncertainty": None,
        "entities": entities or [],
    }


def _document(
    claim_id: str,
    canonical: str,
    *,
    snapshot_id: str = "snapshot-1",
    paper_id: str = "paper-1",
    family_id: str = "family-1",
    claim_type: str = "descriptive",
    polarity: str = "affirmed",
    location: list[str] | None = None,
    time: dict[str, Any] | None = None,
    entities: list[str] | None = None,
    explicit_relations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    proposition = _digest(canonical)
    return {
        "index_version": "evidence-retrieval/2.0",
        "snapshot_id": snapshot_id,
        "paper_id": paper_id,
        "paper_family_id": family_id,
        "root_artifact_version_id": f"artifact-{snapshot_id}",
        "claim_id": claim_id,
        "source_claim_id": None,
        "original_claim": canonical,
        "canonical_claim": canonical,
        "proposition_key": f"proposition:sha256:{proposition}",
        "scoped_claim_id": f"claim:sha256:{_digest(canonical + str(location))}",
        "paper_title": f"Title {paper_id}",
        "evidence_context": canonical,
        "entities": entities or [],
        "key_terms": canonical.split()[:256],
        "abbreviations": [],
        "qualifiers": [],
        "claim_type": claim_type,
        "polarity": polarity,
        "modality": "asserted",
        "attribution": "authors_conclusion",
        "rhetorical_role": "terminal_conclusion",
        "scope": _scope(location=location, time=time, entities=entities),
        "evidence_anchors": [],
        "shortest_evidence_path": {
            "root_claim_id": claim_id,
            "nodes": [],
            "links": [],
            "depth": 0,
            "mean_edge_confidence": None,
        },
        "path_quality": 1.0,
        "extractor_prompt_version": "extractor-v1",
        "explicit_relations": explicit_relations or [],
    }


def _manifest(
    snapshot_id: str = "snapshot-1",
    *,
    paper_id: str = "paper-1",
    family_id: str = "family-1",
    document_count: int = 1,
    created_at: str = "2026-08-18T00:00:00Z",
) -> dict[str, Any]:
    return {
        "index_version": "evidence-retrieval/2.0",
        "snapshot_id": snapshot_id,
        "paper_id": paper_id,
        "paper_family_id": family_id,
        "root_artifact_version_id": f"artifact-{snapshot_id}",
        "root_artifact_sha256": "a" * 64,
        "graph_sha256": _digest(snapshot_id),
        "graph_schema_version": "scientific-evidence-graph/1.0",
        "extractor_prompt_version": "extractor-v1",
        "document_count": document_count,
        "created_at": created_at,
    }


def _index_request(
    manifest: dict[str, Any], documents: list[dict[str, Any]]
) -> IndexSnapshotRequest:
    return IndexSnapshotRequest.model_validate(
        {
            "schema_version": "evidence-retrieval/2.0",
            "project_id": "project-1",
            "manifest": manifest,
            "documents": documents,
        },
        strict=True,
    )


def _search_request(
    query: str,
    *,
    schema_version: str = "evidence-retrieval/2.0",
    query_claim: dict[str, str] | None = None,
    intent: str = "claim_search",
    must_terms: list[str] | None = None,
    exclude_terms: list[str] | None = None,
    scope: dict[str, Any] | None = None,
    claim_types: list[str] | None = None,
    polarity: str | None = None,
    active_snapshot_ids: list[str] | None = None,
    limit: int = 20,
    stage: str = "ai_rerank",
    candidate_refs: list[dict[str, str]] | None = None,
    query_plan_handoff: str | None = None,
) -> HybridSearchRequest:
    return HybridSearchRequest.model_validate(
        {
            "schema_version": schema_version,
            "project_id": "project-1",
            "stage": stage,
            "query": query,
            "query_claim": query_claim,
            "candidate_refs": candidate_refs or [],
            "intent": intent,
            "must_terms": must_terms or [],
            "exclude_terms": exclude_terms or [],
            "scope": scope,
            "claim_types": claim_types or [],
            "polarity": polarity,
            "filters": {
                "corpus": "cross_paper",
                "paper_ids": [],
                "paper_family_ids": [],
                "active_snapshot_ids": active_snapshot_ids,
                "limit": limit,
            },
            "query_plan_handoff": query_plan_handoff,
        },
        strict=True,
    )


class FakeDense:
    name = "fake-multilingual"
    dimension = 2

    def __init__(self, *, revision: str = "fake-rev-1") -> None:
        self.revision = revision
        self.fail_documents = False
        self.fail_query = False
        self.ready = False

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if self.fail_documents:
            raise RuntimeError("dense unavailable")
        self.ready = True
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        if self.fail_query:
            raise RuntimeError("dense unavailable")
        self.ready = True
        return [1.0, 0.0]


class FakeQueryPlanner:
    name = "fake-query-planner"

    def __init__(self, payload: QueryPlanPayload, *, fail: bool = False) -> None:
        self.payload = payload
        self.fail = fail
        self.seen: list[str] = []
        self.last_warnings: tuple[str, ...] = ()

    def plan(self, request: HybridSearchRequest) -> QueryPlanPayload:
        self.seen.append(request.query)
        if self.fail:
            raise RuntimeError("planner unavailable")
        return self.payload


class FakeVectorIndex:
    model_name = "fake-multilingual"
    model_revision = "fake-rev-1"
    dimension = 2

    def __init__(self, revision: str = "fake-rev-1") -> None:
        self.model_revision = revision
        self.collection_name = f"fake-{revision}"
        self.points: dict[tuple[str, str, str], tuple[str, str, Any, list[float]]] = {}
        self.fail_search = False
        self.fail_delete = False
        self.last_search: dict[str, Any] | None = None

    def upsert(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        claim_ids: Sequence[str],
        claim_types: Sequence[str],
        polarities: Sequence[str],
        scopes: Sequence[Any],
        vectors: Sequence[Sequence[float]],
    ) -> list[str]:
        point_ids: list[str] = []
        for claim_id, claim_type, polarity, scope, vector in zip(
            claim_ids, claim_types, polarities, scopes, vectors, strict=True
        ):
            point_id = f"{snapshot_id}:{claim_id}"
            self.points[(project_id, snapshot_id, claim_id)] = (
                claim_type,
                polarity,
                scope,
                list(vector),
            )
            point_ids.append(point_id)
        return point_ids

    def search(
        self,
        *,
        project_id: str,
        snapshot_ids: Sequence[str],
        vector: Sequence[float],
        limit: int,
        claim_types: Sequence[str],
        polarity: str | None,
        scope: Any | None,
    ) -> list[ChannelHit]:
        if self.fail_search:
            raise RuntimeError("vector unavailable")
        self.last_search = {
            "limit": limit,
            "claim_types": list(claim_types),
            "polarity": polarity,
        }
        allowed = set(snapshot_ids)
        hits = []
        for (project, snapshot, claim_id), (
            claim_type,
            item_polarity,
            item_scope,
            item,
        ) in self.points.items():
            if project != project_id or snapshot not in allowed:
                continue
            if claim_types and claim_type not in claim_types:
                continue
            if polarity is not None and item_polarity != polarity:
                continue
            if scope is not None and not _scope_hard_match(scope, item_scope):
                continue
            score = sum(left * right for left, right in zip(vector, item, strict=True))
            hits.append(ChannelHit(snapshot, claim_id, score))
        return sorted(hits, key=lambda item: (-item.score, item.snapshot_id))[:limit]

    def delete_snapshots(
        self,
        *,
        project_id: str,
        snapshot_ids: Sequence[str],
        collections: Sequence[str],
    ) -> None:
        del collections
        if self.fail_delete:
            raise RuntimeError("delete unavailable")
        allowed = set(snapshot_ids)
        self.points = {
            key: value
            for key, value in self.points.items()
            if key[0] != project_id or key[1] not in allowed
        }


class FakeRelationClassifier:
    name = "fake-relation"

    def __init__(
        self,
        relations: dict[str, str] | None = None,
        directions: dict[str, str] | None = None,
    ) -> None:
        self.relations = relations or {}
        self.directions = directions or {}
        self.seen: list[tuple[str, str]] = []

    def classify(
        self, plan: QueryPlan, candidates: Sequence[ClaimSearchDocument]
    ) -> list[RelationDecision]:
        del plan
        self.seen = [(item.snapshot_id, item.claim_id) for item in candidates]
        return [
            RelationDecision(
                candidate_key=relation_candidate_key(item.snapshot_id, item.claim_id),
                relation=self.relations.get(item.canonical_claim, "related"),
                relation_direction=self.directions.get(
                    item.canonical_claim, "result_to_query"
                ),
                relation_confidence=0.8,
                scope_relation="equivalent",
            )
            for item in candidates
        ]


def _service(
    tmp_path: Path,
    *,
    dense: FakeDense | None = None,
    vector: FakeVectorIndex | None = None,
    classifier: FakeRelationClassifier | None = None,
    planner: FakeQueryPlanner | None = None,
    remote_query_planner_enabled: bool = True,
    selective_safe_reserve_enabled: bool = True,
    selective_safe_dense_batch_enabled: bool = False,
    query_scoped_concept_pattern_scan_enabled: bool = False,
    query_plan_handoff_secret: str | None = None,
    query_plan_handoff_ttl_seconds: int = 120,
    query_plan_handoff_clock: Callable[[], float] | None = None,
    rrf_k: int = 60,
) -> RetrievalService:
    active_dense = dense or FakeDense()
    return RetrievalService(
        data_dir=tmp_path,
        dense_encoder=active_dense,
        query_planner=planner,
        vector_index=vector or FakeVectorIndex(active_dense.revision),
        relation_classifier=classifier or FakeRelationClassifier(),
        remote_query_planner_enabled=remote_query_planner_enabled,
        selective_safe_reserve_enabled=selective_safe_reserve_enabled,
        selective_safe_dense_batch_enabled=selective_safe_dense_batch_enabled,
        query_scoped_concept_pattern_scan_enabled=(
            query_scoped_concept_pattern_scan_enabled
        ),
        query_plan_handoff_secret=query_plan_handoff_secret,
        query_plan_handoff_ttl_seconds=query_plan_handoff_ttl_seconds,
        query_plan_handoff_clock=(
            query_plan_handoff_clock
            if query_plan_handoff_clock is not None
            else time.time
        ),
        rrf_k=rrf_k,
    )


def _translated_plan(translation: str) -> QueryPlanPayload:
    return QueryPlanPayload(
        query_type="claim_search",
        canonical_claim=translation,
        must_terms=[],
        optional_terms=[translation],
        exclude_terms=[],
        entities=[translation],
        polarity=None,
        claim_types=[],
        scope_constraints=None,
    )


_HANDOFF_SECRET_A = "query-plan-handoff-test-secret-a-0001"
_HANDOFF_SECRET_B = "query-plan-handoff-test-secret-b-0002"


def _handoff_preview(
    tmp_path: Path,
    *,
    clock: Callable[[], float] | None = None,
) -> tuple[
    RetrievalService,
    FakeQueryPlanner,
    FakeRelationClassifier,
    Any,
    list[dict[str, str]],
]:
    planner = FakeQueryPlanner(
        _translated_plan("The Ordos Basin preserves multiple tectonic stages.")
    )
    classifier = FakeRelationClassifier()
    service = _service(
        tmp_path,
        planner=planner,
        classifier=classifier,
        query_plan_handoff_secret=_HANDOFF_SECRET_A,
        query_plan_handoff_clock=clock,
    )
    documents = [
        _document(
            "claim-ordos",
            "The Ordos Basin preserves multiple tectonic stages.",
        ),
        _document(
            "claim-ordos-comparison",
            "Ordos Basin tectonic stages differ across structural domains.",
        ),
        _document(
            "claim-distractor",
            "Pacific sediment records a separate depositional history.",
        ),
    ]
    service.index_snapshot(
        _index_request(_manifest(document_count=len(documents)), documents)
    )
    preview = service.search(
        _search_request(
            "鄂尔多斯盆地保存了多期构造演化证据。",
            schema_version="evidence-retrieval/2.1",
            intent="supporting_evidence",
            stage="preview",
            limit=12,
        )
    )
    assert preview.query_plan_handoff is not None
    candidate_refs = [
        {"snapshot_id": item.snapshot_id, "claim_id": item.claim_id}
        for item in preview.preview
    ]
    return service, planner, classifier, preview, candidate_refs


def test_21_preview_without_query_claim_seals_remote_plan_and_final_scores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, planner, classifier, preview, candidate_refs = _handoff_preview(tmp_path)

    assert preview.query_plan.planner == "gpt"
    assert preview.query_plan.canonical_claim == (
        "The Ordos Basin preserves multiple tectonic stages."
    )
    assert preview.query_plan_handoff is not None
    assert preview.query_plan_handoff.startswith("v1.derived-auth-v1.")

    forbidden_calls = {
        "lexical": 0,
        "embed": 0,
        "vector": 0,
        "rrf": 0,
        "sparse_discount": 0,
        "exact_anchor": 0,
        "concept_anchor": 0,
        "scientific_anchor": 0,
        "family_diversity": 0,
        "cross_language_reserve": 0,
        "selective_reserve": 0,
    }
    load_calls: list[dict[str, Any]] = []
    original_load_documents = service.store.load_documents

    def forbidden(name: str) -> Callable[..., Any]:
        def invoke(*args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            forbidden_calls[name] += 1
            raise AssertionError(f"verified final unexpectedly called {name}")

        return invoke

    def load_documents(**kwargs: Any) -> dict[tuple[str, str], ClaimSearchDocument]:
        load_calls.append(kwargs)
        return original_load_documents(**kwargs)

    monkeypatch.setattr(service.store, "lexical_search", forbidden("lexical"))
    monkeypatch.setattr(service.dense_encoder, "embed_query", forbidden("embed"))
    monkeypatch.setattr(service.vector_index, "search", forbidden("vector"))
    monkeypatch.setattr(service.store, "load_documents", load_documents)
    for function_name, call_name in (
        ("_rrf", "rrf"),
        ("_discount_sparse_cjk_lexical_channel", "sparse_discount"),
        ("_fuse_exact_anchors", "exact_anchor"),
        ("_fuse_concept_anchors", "concept_anchor"),
        ("_fuse_scientific_mentions", "scientific_anchor"),
        ("_apply_soft_family_diversity", "family_diversity"),
        ("_apply_guarded_cross_language_dense_reserve", "cross_language_reserve"),
        ("_apply_selective_safe_top12_reserve", "selective_reserve"),
    ):
        monkeypatch.setattr(
            f"compiler_service.retrieval_service.{function_name}",
            forbidden(call_name),
        )

    final = service.search(
        _search_request(
            "鄂尔多斯盆地保存了多期构造演化证据。",
            schema_version="evidence-retrieval/2.1",
            intent="supporting_evidence",
            stage="ai_rerank",
            limit=12,
            candidate_refs=candidate_refs,
            query_plan_handoff=preview.query_plan_handoff,
        )
    )

    assert planner.seen == ["鄂尔多斯盆地保存了多期构造演化证据。"]
    assert classifier.seen == [
        (item["snapshot_id"], item["claim_id"]) for item in candidate_refs
    ]
    assert final.query_plan == preview.query_plan
    preview_scores = {
        (item.snapshot_id, item.claim_id): item.scores for item in preview.preview
    }
    final_scores = {
        (item.snapshot_id, item.claim_id): item.scores
        for cluster in final.clusters
        for item in cluster.member_refs
    }
    final_order = [
        (item.snapshot_id, item.claim_id)
        for cluster in final.clusters
        for item in cluster.member_refs
    ]
    assert final_order == [
        (item["snapshot_id"], item["claim_id"]) for item in candidate_refs
    ]
    assert final_scores == preview_scores
    assert all(value == 0 for value in forbidden_calls.values())
    assert len(load_calls) == 1
    assert load_calls[0]["keys"] == [
        (item["snapshot_id"], item["claim_id"]) for item in candidate_refs
    ]
    assert "ranking_feature_sink" not in load_calls[0]
    assert final.channels.lexical.attempted is False
    assert final.channels.lexical.status == "skipped"
    assert final.channels.lexical.returned == 0
    assert final.channels.dense.attempted is False
    assert final.channels.dense.status == "skipped"
    assert final.channels.dense.returned == 0
    assert {
        item.relation
        for cluster in final.clusters
        for item in cluster.member_refs
    } == {"related"}
    assert "query_plan_handoff" not in final.model_dump(mode="json")


def test_21_verified_handoff_missing_candidate_fails_before_relation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, planner, classifier, preview, candidate_refs = _handoff_preview(tmp_path)
    missing_key = (
        candidate_refs[0]["snapshot_id"],
        candidate_refs[0]["claim_id"],
    )
    load_calls = 0
    original_load_documents = service.store.load_documents

    def load_documents(**kwargs: Any) -> dict[tuple[str, str], ClaimSearchDocument]:
        nonlocal load_calls
        load_calls += 1
        documents = original_load_documents(**kwargs)
        documents.pop(missing_key, None)
        return documents

    monkeypatch.setattr(service.store, "load_documents", load_documents)

    with pytest.raises(QueryPlanHandoffError, match="run preview again"):
        service.search(
            _search_request(
                "鄂尔多斯盆地保存了多期构造演化证据。",
                schema_version="evidence-retrieval/2.1",
                intent="supporting_evidence",
                stage="ai_rerank",
                limit=12,
                candidate_refs=candidate_refs,
                query_plan_handoff=preview.query_plan_handoff,
            )
        )

    assert load_calls == 1
    assert planner.seen == ["鄂尔多斯盆地保存了多期构造演化证据。"]
    assert classifier.seen == []


def test_21_verified_handoff_keeps_query_claim_same_paper_closure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planner = FakeQueryPlanner(
        _translated_plan("Mercury exposure and neurological harm")
    )
    classifier = FakeRelationClassifier()
    service = _service(
        tmp_path,
        planner=planner,
        classifier=classifier,
        query_plan_handoff_secret=_HANDOFF_SECRET_A,
    )
    seed = _document(
        "claim-seed",
        "Mercury exposure increases neurological risk",
    )
    same_paper = _document(
        "claim-same-paper",
        "Mercury exposure is associated with neurological harm",
        claim_type="causal",
        location=["China"],
    )
    same_paper_qualifier = _document(
        "claim-same-paper-qualifier",
        "Mercury exposure duration qualifies the observed neurological harm",
        claim_type="causal",
        location=["China"],
    )
    same_paper_wrong_term = _document(
        "claim-same-paper-wrong-term",
        "Lead exposure is associated with neurological harm",
        claim_type="causal",
        location=["China"],
    )
    same_paper_wrong_type = _document(
        "claim-same-paper-wrong-type",
        "Mercury exposure has a descriptive neurological profile",
        location=["China"],
    )
    same_paper_wrong_polarity = _document(
        "claim-same-paper-wrong-polarity",
        "Mercury exposure does not increase neurological harm",
        claim_type="causal",
        polarity="negated",
        location=["China"],
    )
    same_paper_wrong_scope = _document(
        "claim-same-paper-wrong-scope",
        "Mercury exposure is associated with neurological harm in Chile",
        claim_type="causal",
        location=["Chile"],
    )
    cross_paper = _document(
        "claim-cross-paper",
        "Independent cohorts link mercury exposure to neurological harm",
        snapshot_id="snapshot-2",
        paper_id="paper-2",
        family_id="family-2",
        claim_type="causal",
        location=["China"],
    )
    service.index_snapshot(
        _index_request(
            _manifest(document_count=7),
            [
                seed,
                same_paper,
                same_paper_qualifier,
                same_paper_wrong_term,
                same_paper_wrong_type,
                same_paper_wrong_polarity,
                same_paper_wrong_scope,
            ],
        )
    )
    service.index_snapshot(
        _index_request(
            _manifest(
                "snapshot-2",
                paper_id="paper-2",
                family_id="family-2",
            ),
            [cross_paper],
        )
    )
    query_claim = {"snapshot_id": "snapshot-1", "claim_id": "claim-seed"}
    preview = service.search(
        _search_request(
            "find related evidence",
            schema_version="evidence-retrieval/2.1",
            query_claim=query_claim,
            must_terms=["mercury"],
            exclude_terms=["duration"],
            scope={"location": ["China"]},
            claim_types=["causal"],
            polarity="affirmed",
            stage="preview",
        )
    )
    candidate_refs = [
        {"snapshot_id": item.snapshot_id, "claim_id": item.claim_id}
        for item in preview.preview
    ]

    final_request = _search_request(
        "find related evidence",
        schema_version="evidence-retrieval/2.1",
        query_claim=query_claim,
        must_terms=["mercury"],
        exclude_terms=["duration"],
        scope={"location": ["China"]},
        claim_types=["causal"],
        polarity="affirmed",
        stage="ai_rerank",
        candidate_refs=candidate_refs,
        query_plan_handoff=preview.query_plan_handoff,
    )
    final = service.search(final_request)

    assert len(planner.seen) == 1
    assert seed["canonical_claim"] in planner.seen[0]
    assert set(classifier.seen) == {
        ("snapshot-1", "claim-same-paper"),
        ("snapshot-2", "claim-cross-paper"),
    }
    assert {
        (item.snapshot_id, item.claim_id)
        for cluster in final.clusters
        for item in cluster.member_refs
    } == set(classifier.seen)
    preview_scores = {
        (item.snapshot_id, item.claim_id): item.scores for item in preview.preview
    }
    final_cross_scores = {
        (item.snapshot_id, item.claim_id): item.scores
        for cluster in final.clusters
        for item in cluster.member_refs
        if item.snapshot_id == "snapshot-2"
    }
    assert final_cross_scores == preview_scores

    original_load_documents = service.store.load_documents
    classifier_seen = list(classifier.seen)

    def load_without_query_claim(
        **kwargs: Any,
    ) -> dict[tuple[str, str], ClaimSearchDocument]:
        documents = original_load_documents(**kwargs)
        documents.pop(("snapshot-1", "claim-seed"), None)
        return documents

    monkeypatch.setattr(service.store, "load_documents", load_without_query_claim)
    with pytest.raises(QueryPlanHandoffError, match="run preview again"):
        service.search(final_request)

    assert len(planner.seen) == 1
    assert classifier.seen == classifier_seen


@pytest.mark.parametrize("mutation", ["add", "delete", "reorder"])
def test_21_handoff_rejects_candidate_set_or_order_changes_before_dispatch(
    tmp_path: Path,
    mutation: str,
) -> None:
    service, planner, classifier, preview, candidate_refs = _handoff_preview(tmp_path)
    mutated = deepcopy(candidate_refs)
    if mutation == "add":
        mutated.append({"snapshot_id": "snapshot-1", "claim_id": "claim-added"})
    elif mutation == "delete":
        mutated.pop()
    else:
        mutated.reverse()

    with pytest.raises(QueryPlanHandoffError, match="run preview again"):
        service.search(
            _search_request(
                "鄂尔多斯盆地保存了多期构造演化证据。",
                schema_version="evidence-retrieval/2.1",
                intent="supporting_evidence",
                stage="ai_rerank",
                limit=12,
                candidate_refs=mutated,
                query_plan_handoff=preview.query_plan_handoff,
            )
        )

    assert planner.seen == ["鄂尔多斯盆地保存了多期构造演化证据。"]
    assert classifier.seen == []


@pytest.mark.parametrize(
    ("query", "intent"),
    [
        ("鄂尔多斯盆地的构造演化是什么？", "supporting_evidence"),
        ("鄂尔多斯盆地保存了多期构造演化证据。", "contradiction_search"),
    ],
)
def test_21_handoff_rejects_query_or_intent_changes_before_dispatch(
    tmp_path: Path,
    query: str,
    intent: str,
) -> None:
    service, planner, classifier, preview, candidate_refs = _handoff_preview(tmp_path)

    with pytest.raises(QueryPlanHandoffError, match="run preview again"):
        service.search(
            _search_request(
                query,
                schema_version="evidence-retrieval/2.1",
                intent=intent,
                stage="ai_rerank",
                limit=12,
                candidate_refs=candidate_refs,
                query_plan_handoff=preview.query_plan_handoff,
            )
        )

    assert planner.seen == ["鄂尔多斯盆地保存了多期构造演化证据。"]
    assert classifier.seen == []


def test_21_handoff_rejects_corpus_change_before_dispatch(tmp_path: Path) -> None:
    service, planner, classifier, preview, candidate_refs = _handoff_preview(tmp_path)
    service.index_snapshot(
        _index_request(
            _manifest(
                "snapshot-2",
                paper_id="paper-2",
                family_id="family-2",
            ),
            [
                _document(
                    "claim-new-corpus",
                    "A newly active paper changes the searchable corpus.",
                    snapshot_id="snapshot-2",
                    paper_id="paper-2",
                    family_id="family-2",
                )
            ],
        )
    )

    with pytest.raises(QueryPlanHandoffError, match="run preview again"):
        service.search(
            _search_request(
                "鄂尔多斯盆地保存了多期构造演化证据。",
                schema_version="evidence-retrieval/2.1",
                intent="supporting_evidence",
                stage="ai_rerank",
                limit=12,
                candidate_refs=candidate_refs,
                query_plan_handoff=preview.query_plan_handoff,
            )
        )

    assert planner.seen == ["鄂尔多斯盆地保存了多期构造演化证据。"]
    assert classifier.seen == []


def test_21_handoff_rejects_signature_bitflip_before_state_or_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, planner, classifier, preview, candidate_refs = _handoff_preview(tmp_path)
    assert preview.query_plan_handoff is not None
    version, key_id, payload, signature = preview.query_plan_handoff.split(".")
    replacement = "A" if signature[0] != "A" else "B"
    corrupted = ".".join((version, key_id, payload, replacement + signature[1:]))
    state_calls = 0

    def forbidden_state_access(*args: Any, **kwargs: Any) -> Any:
        nonlocal state_calls
        del args, kwargs
        state_calls += 1
        raise AssertionError("invalid handoff unexpectedly accessed retrieval state")

    monkeypatch.setattr(service.store, "load_documents", forbidden_state_access)
    monkeypatch.setattr(service.store, "resolve_snapshots", forbidden_state_access)

    with pytest.raises(QueryPlanHandoffError, match="run preview again"):
        service.search(
            _search_request(
                "鄂尔多斯盆地保存了多期构造演化证据。",
                schema_version="evidence-retrieval/2.1",
                query_claim={
                    "snapshot_id": "missing-snapshot",
                    "claim_id": "missing-claim",
                },
                intent="supporting_evidence",
                stage="ai_rerank",
                limit=12,
                candidate_refs=candidate_refs,
                query_plan_handoff=corrupted,
            )
        )

    assert planner.seen == ["鄂尔多斯盆地保存了多期构造演化证据。"]
    assert classifier.seen == []
    assert state_calls == 0


def test_21_handoff_rejects_limit_change_before_state_or_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, planner, classifier, preview, candidate_refs = _handoff_preview(tmp_path)
    state_calls = 0

    def forbidden_state_access(*args: Any, **kwargs: Any) -> Any:
        nonlocal state_calls
        del args, kwargs
        state_calls += 1
        raise AssertionError("mismatched handoff unexpectedly accessed retrieval state")

    monkeypatch.setattr(service.store, "load_documents", forbidden_state_access)
    monkeypatch.setattr(service.store, "resolve_snapshots", forbidden_state_access)

    with pytest.raises(QueryPlanHandoffError, match="run preview again"):
        service.search(
            _search_request(
                "鄂尔多斯盆地保存了多期构造演化证据。",
                schema_version="evidence-retrieval/2.1",
                intent="supporting_evidence",
                stage="ai_rerank",
                limit=13,
                candidate_refs=candidate_refs,
                query_plan_handoff=preview.query_plan_handoff,
            )
        )

    assert planner.seen == ["鄂尔多斯盆地保存了多期构造演化证据。"]
    assert classifier.seen == []
    assert state_calls == 0


def test_21_handoff_expires_at_default_ttl_boundary_before_dispatch(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    now = [1_000_000.0]
    service, planner, classifier, preview, candidate_refs = _handoff_preview(
        tmp_path,
        clock=lambda: now[0],
    )
    now[0] += 120

    with (
        caplog.at_level("WARNING", logger="compiler_service.retrieval_service"),
        pytest.raises(QueryPlanHandoffError, match="run preview again"),
    ):
        service.search(
            _search_request(
                "鄂尔多斯盆地保存了多期构造演化证据。",
                schema_version="evidence-retrieval/2.1",
                intent="supporting_evidence",
                stage="ai_rerank",
                limit=12,
                candidate_refs=candidate_refs,
                query_plan_handoff=preview.query_plan_handoff,
            )
        )

    assert planner.seen == ["鄂尔多斯盆地保存了多期构造演化证据。"]
    assert classifier.seen == []
    # Only the private log names the failed check, never the response.
    assert "Query plan handoff rejected: expired age_s=120" in caplog.text


@pytest.mark.parametrize("mismatch", ["secret", "configuration", "pipeline"])
def test_21_handoff_rejects_service_identity_mismatch_before_dispatch(
    tmp_path: Path,
    mismatch: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, _, preview, candidate_refs = _handoff_preview(tmp_path)
    if mismatch == "pipeline":
        monkeypatch.setattr(
            "compiler_service.retrieval_service._RETRIEVAL_PIPELINE_VERSION",
            "test-changed-retrieval-pipeline",
        )
    planner = FakeQueryPlanner(
        _translated_plan("The Ordos Basin preserves multiple tectonic stages.")
    )
    classifier = FakeRelationClassifier()
    service = _service(
        tmp_path,
        planner=planner,
        classifier=classifier,
        query_plan_handoff_secret=(
            _HANDOFF_SECRET_B if mismatch == "secret" else _HANDOFF_SECRET_A
        ),
        rrf_k=61 if mismatch == "configuration" else 60,
    )

    with pytest.raises(QueryPlanHandoffError, match="run preview again"):
        service.search(
            _search_request(
                "鄂尔多斯盆地保存了多期构造演化证据。",
                schema_version="evidence-retrieval/2.1",
                intent="supporting_evidence",
                stage="ai_rerank",
                limit=12,
                candidate_refs=candidate_refs,
                query_plan_handoff=preview.query_plan_handoff,
            )
        )

    assert planner.seen == []
    assert classifier.seen == []


def test_20_preview_dump_omits_handoff_field(tmp_path: Path) -> None:
    service, _, _, modern_preview, _ = _handoff_preview(tmp_path)

    assert "query_plan_handoff" in modern_preview.model_dump(mode="json")
    legacy_preview = service.search(
        _search_request(
            "鄂尔多斯盆地",
            schema_version="evidence-retrieval/2.0",
            stage="preview",
        )
    )

    assert legacy_preview.schema_version == "evidence-retrieval/2.0"
    assert "query_plan_handoff" not in legacy_preview.model_dump(mode="json")


def test_preview_translation_planner_recalls_cross_language_entity_and_final_reuses_it(
    tmp_path: Path,
) -> None:
    planner = FakeQueryPlanner(_translated_plan("Ordos Basin"))
    classifier = FakeRelationClassifier()
    service = _service(tmp_path, planner=planner, classifier=classifier)
    documents = [
        _document(
            f"claim-distractor-{index:02d}",
            f"Unrelated Pacific sediment observation {index}",
        )
        for index in range(13)
    ]
    documents.append(
        _document(
            "claim-ordos",
            "The Ordos Basin preserves multiple tectonic stages.",
        )
    )
    service.index_snapshot(
        _index_request(_manifest(document_count=len(documents)), documents)
    )

    preview = service.search(_search_request("鄂尔多斯盆地", stage="preview", limit=12))

    assert planner.seen == ["鄂尔多斯盆地"]
    assert preview.query_plan.original_query == "鄂尔多斯盆地"
    assert preview.query_plan.canonical_claim == "Ordos Basin"
    assert preview.query_plan.planner == "gpt"
    assert preview.query_plan.query_type == "topic_search"
    assert preview.result_status == "matches"
    assert preview.preview[0].claim_id == "claim-ordos"

    final = service.search(
        _search_request(
            "鄂尔多斯盆地",
            stage="ai_rerank",
            candidate_refs=[
                {
                    "snapshot_id": preview.preview[0].snapshot_id,
                    "claim_id": preview.preview[0].claim_id,
                }
            ],
        )
    )

    assert planner.seen == ["鄂尔多斯盆地"]
    assert classifier.seen == []
    assert final.channels.relation.provider == "deterministic-topic-routing"
    assert final.result_status == "matches"
    assert final.clusters[0].relation == "related"
    assert final.clusters[0].representative.relation_confidence is None
    assert final.clusters[0].representative.claim_id == "claim-ordos"


def test_deterministic_spine_keeps_top12_filters_and_telemetry(
    tmp_path: Path,
) -> None:
    planner = FakeQueryPlanner(_translated_plan("Pacific Ocean sediment"))
    service = _service(
        tmp_path,
        planner=planner,
        remote_query_planner_enabled=False,
        selective_safe_reserve_enabled=False,
    )
    documents = [
        _document(
            f"claim-ordos-{index:02d}",
            f"Ordos Basin tectonic evidence observation {index}",
            claim_type="causal",
            location=["Ordos Basin"],
        )
        for index in range(1, 15)
    ]
    documents.extend(
        [
            _document(
                "claim-excluded",
                "Ordos Basin forbidden tectonic evidence",
                claim_type="causal",
                location=["Ordos Basin"],
            ),
            _document(
                "claim-wrong-type",
                "Ordos Basin tectonic evidence from the wrong claim type",
                claim_type="descriptive",
                location=["Ordos Basin"],
            ),
        ]
    )
    service.index_snapshot(
        _index_request(_manifest(document_count=len(documents)), documents)
    )
    request = _search_request(
        "鄂尔多斯盆地 tectonic evidence",
        must_terms=["evidence"],
        exclude_terms=["forbidden"],
        scope={"location": ["Ordos Basin"]},
        claim_types=["causal"],
        polarity="affirmed",
        stage="preview",
        limit=12,
    )

    successful_planner_is_ignored = service.search(request)
    planner.fail = True
    failing_planner_is_ignored = service.search(request)

    assert planner.seen == []
    assert successful_planner_is_ignored == failing_planner_is_ignored
    assert successful_planner_is_ignored.warnings == []
    assert successful_planner_is_ignored.query_plan.planner == "deterministic"
    assert successful_planner_is_ignored.query_plan.canonical_claim == request.query
    assert successful_planner_is_ignored.query_plan.must_terms == ["evidence"]
    assert successful_planner_is_ignored.query_plan.exclude_terms == ["forbidden"]
    assert successful_planner_is_ignored.query_plan.claim_types == ["causal"]
    assert successful_planner_is_ignored.query_plan.polarity == "affirmed"
    assert successful_planner_is_ignored.query_plan.scope_constraints is not None
    assert successful_planner_is_ignored.query_plan.scope_constraints.location == [
        "Ordos Basin"
    ]
    assert len(successful_planner_is_ignored.preview) == 12
    assert all(
        item.claim_id.startswith("claim-ordos-")
        for item in successful_planner_is_ignored.preview
    )
    observation = successful_planner_is_ignored.retrieval_confidence
    assert observation is not None
    assert observation.applicability == "policy_inapplicable"
    assert observation.policy_inapplicable_reasons == ["explicit_constraints"]
    assert observation.decision == "return"

    # The explicit deterministic mode is identical to the existing fallback
    # ranking; only the fallback's diagnostic warning differs.
    service.remote_query_planner_enabled = True
    fallback = service.search(request)
    assert planner.seen == [request.query]
    assert fallback.query_plan == successful_planner_is_ignored.query_plan
    assert fallback.preview == successful_planner_is_ignored.preview
    assert fallback.channels == successful_planner_is_ignored.channels
    assert fallback.retrieval_confidence == observation
    assert fallback.warnings == [
        "query planner unavailable; deterministic plan retained"
    ]


def test_deterministic_spine_preserves_exact_candidate_refs_path(
    tmp_path: Path,
) -> None:
    planner = FakeQueryPlanner(_translated_plan("unrelated planner rewrite"))
    classifier = FakeRelationClassifier()
    service = _service(
        tmp_path,
        planner=planner,
        classifier=classifier,
        remote_query_planner_enabled=False,
    )
    documents = [
        _document(
            f"claim-candidate-{index}",
            f"Ordos Basin source comparison evidence {index}",
        )
        for index in range(1, 4)
    ]
    service.index_snapshot(
        _index_request(_manifest(document_count=len(documents)), documents)
    )
    query = "鄂尔多斯盆地显示多阶段构造演化"
    preview = service.search(_search_request(query, stage="preview", limit=12))
    candidate_refs = [
        {"snapshot_id": item.snapshot_id, "claim_id": item.claim_id}
        for item in reversed(preview.preview)
    ]

    final = service.search(
        _search_request(
            query,
            stage="ai_rerank",
            candidate_refs=candidate_refs,
        )
    )

    expected_order = [
        (item["snapshot_id"], item["claim_id"]) for item in candidate_refs
    ]
    assert planner.seen == []
    assert classifier.seen == expected_order
    assert final.query_plan.planner == "deterministic"
    assert final.retrieval_confidence is None
    assert {
        (item.snapshot_id, item.claim_id)
        for cluster in final.clusters
        for item in cluster.member_refs
    } == set(expected_order)


def test_selective_safe_optional_calls_are_guarded_and_keep_channel_contracts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dense = FakeDense()
    vector = FakeVectorIndex()
    service = _service(tmp_path, dense=dense, vector=vector)
    documents = [
        _document(
            f"claim-alpha-{index:02d}",
            f"alpha evidence question observation {index}",
        )
        for index in range(14)
    ]
    service.index_snapshot(
        _index_request(_manifest(document_count=len(documents)), documents)
    )

    lexical_calls: list[dict[str, Any]] = []
    dense_texts: list[str] = []
    vector_calls: list[dict[str, Any]] = []
    original_lexical_search = service.store.lexical_search
    original_embed_query = dense.embed_query
    original_vector_search = vector.search

    def lexical_search(**kwargs: Any) -> list[ChannelHit]:
        lexical_calls.append(kwargs)
        return original_lexical_search(**kwargs)

    def embed_query(text: str) -> list[float]:
        dense_texts.append(text)
        return original_embed_query(text)

    def vector_search(**kwargs: Any) -> list[ChannelHit]:
        vector_calls.append(kwargs)
        return original_vector_search(**kwargs)

    monkeypatch.setattr(service.store, "lexical_search", lexical_search)
    monkeypatch.setattr(dense, "embed_query", embed_query)
    monkeypatch.setattr(vector, "search", vector_search)

    request = _search_request(
        "alpha evidence question",
        must_terms=["alpha"],
        exclude_terms=["forbidden"],
        claim_types=["descriptive"],
        polarity="affirmed",
        stage="preview",
        limit=12,
    )
    preview = service.search(request)

    assert len(preview.preview) == 12
    assert len(lexical_calls) == 2
    assert lexical_calls[0]["limit"] == 500
    assert lexical_calls[1]["limit"] == 1
    assert lexical_calls[1]["query_values"] == [request.query]
    for name in (
        "project_id",
        "snapshot_ids",
        "dense_model",
        "dense_model_revision",
        "dense_dimension",
        "must_values",
        "exclude_values",
        "claim_types",
        "polarity",
        "scope",
    ):
        assert lexical_calls[1][name] == lexical_calls[0][name]
    assert len(dense_texts) == 2
    assert dense_texts[0] != dense_texts[1]
    assert [item["limit"] for item in vector_calls] == [500, 250]
    for name in (
        "project_id",
        "snapshot_ids",
        "claim_types",
        "polarity",
        "scope",
    ):
        assert vector_calls[1][name] == vector_calls[0][name]

    lexical_calls.clear()
    dense_texts.clear()
    vector_calls.clear()
    full_label = service.search(
        _search_request("鄂尔多斯盆地", stage="preview", limit=12)
    )
    assert len(full_label.preview) == 12
    assert len(lexical_calls) == len(dense_texts) == len(vector_calls) == 1

    lexical_calls.clear()
    dense_texts.clear()
    vector_calls.clear()
    candidate_refs = [
        {"snapshot_id": item.snapshot_id, "claim_id": item.claim_id}
        for item in preview.preview
    ]
    final = service.search(
        _search_request(
            request.query,
            must_terms=["alpha"],
            exclude_terms=["forbidden"],
            claim_types=["descriptive"],
            polarity="affirmed",
            stage="ai_rerank",
            candidate_refs=candidate_refs,
        )
    )
    assert final.stage == "ai_rerank"
    assert len(lexical_calls) == len(dense_texts) == len(vector_calls) == 1

    lexical_calls.clear()
    dense_texts.clear()
    vector_calls.clear()
    direct_final = service.search(
        _search_request(
            request.query,
            must_terms=["alpha"],
            exclude_terms=["forbidden"],
            claim_types=["descriptive"],
            polarity="affirmed",
            stage="ai_rerank",
        )
    )
    assert direct_final.stage == "ai_rerank"
    assert len(lexical_calls) == len(dense_texts) == len(vector_calls) == 2


def test_selective_safe_small_pool_makes_no_optional_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dense = FakeDense()
    vector = FakeVectorIndex()
    service = _service(tmp_path, dense=dense, vector=vector)
    documents = [
        _document(
            f"claim-small-{index:02d}",
            f"small pool evidence question observation {index}",
        )
        for index in range(12)
    ]
    service.index_snapshot(
        _index_request(_manifest(document_count=len(documents)), documents)
    )

    lexical_calls = 0
    dense_calls = 0
    vector_calls = 0
    original_lexical_search = service.store.lexical_search
    original_embed_query = dense.embed_query
    original_vector_search = vector.search

    def lexical_search(**kwargs: Any) -> list[ChannelHit]:
        nonlocal lexical_calls
        lexical_calls += 1
        return original_lexical_search(**kwargs)

    def embed_query(text: str) -> list[float]:
        nonlocal dense_calls
        dense_calls += 1
        return original_embed_query(text)

    def vector_search(**kwargs: Any) -> list[ChannelHit]:
        nonlocal vector_calls
        vector_calls += 1
        return original_vector_search(**kwargs)

    monkeypatch.setattr(service.store, "lexical_search", lexical_search)
    monkeypatch.setattr(dense, "embed_query", embed_query)
    monkeypatch.setattr(vector, "search", vector_search)

    response = service.search(
        _search_request("small pool evidence question", stage="preview")
    )

    assert len(response.preview) == 12
    assert lexical_calls == dense_calls == vector_calls == 1


def test_selective_safe_internal_disable_switch_returns_exact_old_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dense = FakeDense()
    vector = FakeVectorIndex()
    service = _service(
        tmp_path,
        dense=dense,
        vector=vector,
        selective_safe_reserve_enabled=False,
    )
    documents = [
        _document(
            f"claim-disabled-{index:02d}",
            f"internal ablation control evidence {index}",
        )
        for index in range(1, 14)
    ]
    service.index_snapshot(
        _index_request(_manifest(document_count=len(documents)), documents)
    )
    baseline_hits = [
        ChannelHit(
            "snapshot-1",
            f"claim-disabled-{index:02d}",
            1.0 - index / 100.0,
        )
        for index in range(1, 14)
    ]
    lexical_calls = 0
    dense_calls = 0
    vector_calls = 0
    original_embed_query = dense.embed_query

    def lexical_search(**kwargs: Any) -> list[ChannelHit]:
        nonlocal lexical_calls
        lexical_calls += 1
        if kwargs["limit"] == 1:
            return [baseline_hits[-1]]
        return baseline_hits

    def embed_query(text: str) -> list[float]:
        nonlocal dense_calls
        dense_calls += 1
        return original_embed_query(text)

    def vector_search(**kwargs: Any) -> list[ChannelHit]:
        del kwargs
        nonlocal vector_calls
        vector_calls += 1
        return baseline_hits

    monkeypatch.setattr(service.store, "lexical_search", lexical_search)
    monkeypatch.setattr(dense, "embed_query", embed_query)
    monkeypatch.setattr(vector, "search", vector_search)

    response = service.search(
        _search_request("internal ablation control", stage="preview", limit=12)
    )

    assert [item.claim_id for item in response.preview] == [
        f"claim-disabled-{index:02d}" for index in range(1, 13)
    ]
    assert [item.scores.rrf for item in response.preview] == pytest.approx(
        [2.0 / (60 + index) for index in range(1, 13)]
    )
    assert lexical_calls == dense_calls == vector_calls == 1


class _RecordingBatchDense(FakeDense):
    def __init__(self, *, batch_mode: str = "success") -> None:
        super().__init__()
        self.batch_mode = batch_mode
        self.query_calls: list[str] = []
        self.batch_calls: list[list[str]] = []

    def embed_query(self, text: str) -> list[float]:
        self.query_calls.append(text)
        return super().embed_query(text)

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        values = list(texts)
        self.batch_calls.append(values)
        if self.batch_mode == "exception":
            raise RuntimeError("synthetic batch failure")
        if self.batch_mode == "count":
            return [[1.0, 0.0]] * max(0, len(values) - 1)
        if self.batch_mode == "shape":
            return [[1.0] for _ in values]
        if self.batch_mode == "nan":
            return [[float("nan"), 0.0] for _ in values]
        return [[1.0, 0.0] for _ in values]


class _RecordingVectorIndex(FakeVectorIndex):
    def __init__(self) -> None:
        super().__init__()
        self.search_calls: list[dict[str, Any]] = []

    def search(self, **kwargs: Any) -> list[ChannelHit]:
        self.search_calls.append(deepcopy(kwargs))
        return super().search(**kwargs)


def _run_dense_batch_equivalence_case(
    tmp_path: Path,
    *,
    enabled: bool,
    batch_mode: str = "success",
) -> tuple[Any, _RecordingBatchDense, _RecordingVectorIndex, RetrievalService]:
    dense = _RecordingBatchDense(batch_mode=batch_mode)
    vector = _RecordingVectorIndex()
    service = _service(
        tmp_path,
        dense=dense,
        vector=vector,
        planner=FakeQueryPlanner(
            _translated_plan("Canonical translated multiview evidence claim")
        ),
        selective_safe_dense_batch_enabled=enabled,
    )
    documents = [
        _document(
            f"claim-dense-batch-{index:02d}",
            f"Multiview evidence question observation {index}",
        )
        for index in range(14)
    ]
    service.index_snapshot(
        _index_request(_manifest(document_count=len(documents)), documents)
    )
    vector.search_calls.clear()
    response = service.search(
        _search_request("raw multiview evidence question", stage="preview")
    )
    return response, dense, vector, service


def test_selective_safe_dense_batch_is_opt_in_and_result_exact(
    tmp_path: Path,
) -> None:
    baseline, baseline_dense, baseline_vector, baseline_service = (
        _run_dense_batch_equivalence_case(tmp_path / "baseline", enabled=False)
    )
    candidate, batch_dense, batch_vector, batch_service = (
        _run_dense_batch_equivalence_case(tmp_path / "candidate", enabled=True)
    )

    assert candidate.model_dump(mode="json") == baseline.model_dump(mode="json")
    assert baseline_dense.batch_calls == []
    assert len(baseline_dense.query_calls) == 3
    assert len(batch_dense.query_calls) == 1
    assert batch_dense.batch_calls == [
        [
            "raw multiview evidence question",
            "Canonical translated multiview evidence claim",
        ]
    ]
    assert batch_vector.search_calls == baseline_vector.search_calls
    assert baseline_service.dense_batch_observations == {}
    assert batch_service.dense_batch_observations == {"success": 1}


@pytest.mark.parametrize(
    ("batch_mode", "outcome"),
    [
        ("exception", "exception_fallback"),
        ("count", "invalid_count_fallback"),
        ("shape", "invalid_shape_fallback"),
        ("nan", "invalid_value_fallback"),
    ],
)
def test_selective_safe_invalid_dense_batch_exactly_falls_back(
    tmp_path: Path,
    batch_mode: str,
    outcome: str,
) -> None:
    baseline, baseline_dense, baseline_vector, _ = _run_dense_batch_equivalence_case(
        tmp_path / f"baseline-{batch_mode}", enabled=False
    )
    candidate, batch_dense, batch_vector, service = _run_dense_batch_equivalence_case(
        tmp_path / f"candidate-{batch_mode}",
        enabled=True,
        batch_mode=batch_mode,
    )

    assert candidate.model_dump(mode="json") == baseline.model_dump(mode="json")
    assert batch_dense.query_calls == baseline_dense.query_calls
    assert batch_vector.search_calls == baseline_vector.search_calls
    assert service.dense_batch_observations == {outcome: 1}


def test_fastembed_dense_query_batch_uses_one_bounded_native_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], int]] = []

    class Model:
        def token_count(self, text: str) -> int:
            del text
            return 2

        def query_embed(
            self, texts: Sequence[str], *, batch_size: int
        ) -> Sequence[np.ndarray[Any, Any]]:
            values = list(texts)
            calls.append((values, batch_size))
            return [
                np.array([1.0, index], dtype="float32")
                for index, _ in enumerate(values)
            ]

    encoder = object.__new__(FastEmbedDenseEncoder)
    encoder.dimension = 2
    encoder._ready = False
    encoder._query_slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(encoder, "_active_model", lambda: Model())

    assert encoder.embed_queries(["raw", "canonical"]) == [
        [1.0, 0.0],
        [1.0, 1.0],
    ]
    assert calls == [(["raw", "canonical"], 2)]
    with pytest.raises(RuntimeError, match="exact-equivalence limit"):
        encoder.embed_queries(["one", "two", "three"])


def test_fastembed_dense_query_batch_rejects_unequal_token_lengths_before_inference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query_calls = 0

    class Model:
        def token_count(self, text: str) -> int:
            return {"short": 2, "long": 3}[text]

        def query_embed(self, texts: Sequence[str], *, batch_size: int) -> list[Any]:
            del texts, batch_size
            nonlocal query_calls
            query_calls += 1
            return []

    encoder = object.__new__(FastEmbedDenseEncoder)
    encoder.dimension = 2
    encoder._ready = False
    encoder._query_slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(encoder, "_active_model", lambda: Model())

    with pytest.raises(DenseBatchIneligibleError, match="unequal token lengths"):
        encoder.embed_queries(["short", "long"])
    assert query_calls == 0


def test_promoted_rank13_candidate_ref_reuses_full_pool_scores_when_degraded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dense = FakeDense()
    vector = FakeVectorIndex()
    classifier = FakeRelationClassifier()
    service = _service(tmp_path, dense=dense, vector=vector, classifier=classifier)
    documents = [
        _document(
            f"claim-promoted-{index:02d}",
            f"promoted candidate evidence observation {index}",
        )
        for index in range(1, 14)
    ]
    service.index_snapshot(
        _index_request(_manifest(document_count=len(documents)), documents)
    )
    baseline_hits = [
        ChannelHit(
            "snapshot-1",
            f"claim-promoted-{index:02d}",
            1.0 - index / 100.0,
        )
        for index in range(1, 14)
    ]

    def lexical_search(**kwargs: Any) -> list[ChannelHit]:
        if kwargs["limit"] == 1:
            return [baseline_hits[-1]]
        return baseline_hits

    def vector_search(**kwargs: Any) -> list[ChannelHit]:
        del kwargs
        return baseline_hits

    monkeypatch.setattr(service.store, "lexical_search", lexical_search)
    monkeypatch.setattr(vector, "search", vector_search)
    preview = service.search(
        _search_request(
            "Promoted candidate evidence indicates an observation",
            stage="preview",
            limit=12,
        )
    )
    promoted = next(
        item for item in preview.preview if item.claim_id == "claim-promoted-13"
    )
    assert preview.preview[-1].claim_id == "claim-promoted-13"

    load_calls: list[list[tuple[str, str]]] = []
    original_load_documents = service.store.load_documents

    def load_documents(**kwargs: Any) -> dict[tuple[str, str], ClaimSearchDocument]:
        load_calls.append(list(kwargs["keys"]))
        return original_load_documents(**kwargs)

    monkeypatch.setattr(service.store, "load_documents", load_documents)
    candidate_refs = [
        {
            "snapshot_id": promoted.snapshot_id,
            "claim_id": promoted.claim_id,
        }
    ]
    related = service.search(
        _search_request(
            "Promoted candidate evidence indicates an observation",
            stage="ai_rerank",
            candidate_refs=candidate_refs,
        )
    )
    assert len(load_calls) == 1
    assert len(load_calls[0]) == 13
    related_scores = related.clusters[0].representative.scores
    assert related_scores.lexical == promoted.scores.lexical
    assert related_scores.dense == promoted.scores.dense
    assert related_scores.rrf == promoted.scores.rrf

    load_calls.clear()
    service.relation_classifier = None
    degraded = service.search(
        _search_request(
            "Promoted candidate evidence indicates an observation",
            stage="ai_rerank",
            candidate_refs=candidate_refs,
        )
    )
    assert len(load_calls) == 1
    assert len(load_calls[0]) == 13
    assert degraded.channels.relation.status == "degraded"
    assert degraded.result_status == "degraded"
    assert degraded.clusters == []
    assert degraded.warnings == [
        "AI relation reranking is unavailable; preview retained"
    ]


def test_selective_safe_optional_failures_leave_baseline_healthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dense = FakeDense()
    vector = FakeVectorIndex()
    service = _service(tmp_path, dense=dense, vector=vector)
    documents = [
        _document(
            f"claim-fallback-{index:02d}",
            f"fallback evidence question observation {index}",
        )
        for index in range(14)
    ]
    service.index_snapshot(
        _index_request(_manifest(document_count=len(documents)), documents)
    )

    lexical_calls = 0
    vector_calls = 0
    original_lexical_search = service.store.lexical_search
    original_vector_search = vector.search

    def lexical_search(**kwargs: Any) -> list[ChannelHit]:
        nonlocal lexical_calls
        lexical_calls += 1
        if lexical_calls > 1:
            raise RuntimeError("optional raw lexical failure")
        return original_lexical_search(**kwargs)

    def vector_search(**kwargs: Any) -> list[ChannelHit]:
        nonlocal vector_calls
        vector_calls += 1
        if vector_calls > 1:
            raise RuntimeError("optional dense view failure")
        return original_vector_search(**kwargs)

    monkeypatch.setattr(service.store, "lexical_search", lexical_search)
    monkeypatch.setattr(vector, "search", vector_search)

    response = service.search(
        _search_request("fallback evidence question", stage="preview", limit=12)
    )

    assert len(response.preview) == 12
    assert lexical_calls == vector_calls == 2
    assert response.channels.lexical.status == "ok"
    assert response.channels.dense.status == "ok"
    assert response.warnings == []


def test_selective_safe_roles_require_their_main_channel_to_be_healthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    documents = [
        _document(
            f"claim-channel-status-{index:02d}",
            f"channel status evidence observation {index}",
        )
        for index in range(14)
    ]

    lexical_dense = FakeDense()
    lexical_vector = FakeVectorIndex()
    lexical_service = _service(
        tmp_path / "lexical-degraded",
        dense=lexical_dense,
        vector=lexical_vector,
    )
    lexical_service.index_snapshot(
        _index_request(_manifest(document_count=len(documents)), documents)
    )
    lexical_calls = 0

    def failed_lexical_search(**kwargs: Any) -> list[ChannelHit]:
        del kwargs
        nonlocal lexical_calls
        lexical_calls += 1
        raise RuntimeError("main lexical unavailable")

    monkeypatch.setattr(lexical_service.store, "lexical_search", failed_lexical_search)
    lexical_response = lexical_service.search(
        _search_request("channel status evidence", stage="preview")
    )
    assert lexical_calls == 1
    assert lexical_response.channels.lexical.status == "degraded"
    assert lexical_response.channels.dense.status == "ok"

    dense_encoder = FakeDense()
    dense_vector = FakeVectorIndex()
    dense_service = _service(
        tmp_path / "dense-degraded",
        dense=dense_encoder,
        vector=dense_vector,
    )
    dense_service.index_snapshot(
        _index_request(_manifest(document_count=len(documents)), documents)
    )
    dense_vector.fail_search = True
    raw_lexical_calls = 0
    dense_embed_calls = 0
    dense_vector_calls = 0
    original_lexical_search = dense_service.store.lexical_search
    original_embed_query = dense_encoder.embed_query
    original_vector_search = dense_vector.search

    def lexical_search(**kwargs: Any) -> list[ChannelHit]:
        nonlocal raw_lexical_calls
        raw_lexical_calls += 1
        return original_lexical_search(**kwargs)

    def embed_query(text: str) -> list[float]:
        nonlocal dense_embed_calls
        dense_embed_calls += 1
        return original_embed_query(text)

    def vector_search(**kwargs: Any) -> list[ChannelHit]:
        nonlocal dense_vector_calls
        dense_vector_calls += 1
        return original_vector_search(**kwargs)

    monkeypatch.setattr(dense_service.store, "lexical_search", lexical_search)
    monkeypatch.setattr(dense_encoder, "embed_query", embed_query)
    monkeypatch.setattr(dense_vector, "search", vector_search)
    dense_response = dense_service.search(
        _search_request("channel status evidence", stage="preview")
    )
    assert raw_lexical_calls == 2
    assert dense_embed_calls == dense_vector_calls == 1
    assert dense_response.channels.lexical.status == "ok"
    assert dense_response.channels.dense.status == "degraded"


def test_preview_translation_planner_failure_keeps_deterministic_retrieval(
    tmp_path: Path,
) -> None:
    planner = FakeQueryPlanner(_translated_plan("Ordos Basin"), fail=True)
    service = _service(tmp_path, planner=planner)
    service.index_snapshot(
        _index_request(
            _manifest(),
            [_document("claim-ordos", "The Ordos Basin is tectonically stable.")],
        )
    )

    response = service.search(_search_request("鄂尔多斯盆地", stage="preview"))

    assert response.query_plan.planner == "deterministic"
    assert response.query_plan.optional_terms[:2] == [
        "Ordos Basin",
        "鄂爾多斯盆地",
    ]
    assert response.preview[0].claim_id == "claim-ordos"
    assert response.warnings == [
        "query planner unavailable; deterministic plan retained"
    ]


def test_preview_observes_cross_language_concept_without_deciding_to_abstain(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    documents = [
        _document(
            "claim-ordos",
            "The Ordos Basin preserves multiple tectonic stages.",
        ),
        _document(
            "claim-pacific",
            "Pacific abyssal sediment records a distinct depositional process.",
        ),
    ]
    service.index_snapshot(
        _index_request(_manifest(document_count=len(documents)), documents)
    )

    response = service.search(_search_request("鄂尔多斯盆地", stage="preview"))

    assert response.preview[0].claim_id == "claim-ordos"
    observation = response.retrieval_confidence
    assert observation is not None
    assert observation.mode == "observe"
    assert observation.decision == "return"
    assert observation.would_abstain is None
    assert observation.applicability == "applicable"
    assert observation.query_profile == "topic"
    assert observation.concept_anchor_hits == 1
    assert observation.strong_signal_types == ["concept_anchor"]


def test_query_scoped_concept_scan_is_opt_in_and_response_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documents = [
        _document(
            "claim-ordos",
            "The Ordos Basin preserves multiple tectonic stages.",
        ),
        _document(
            "claim-pacific",
            "Pacific abyssal sediment records a distinct depositional process.",
        ),
    ]
    control = _service(tmp_path / "control")
    control.index_snapshot(
        _index_request(_manifest(document_count=len(documents)), documents)
    )
    assert control.query_scoped_concept_pattern_scan_enabled is False
    with monkeypatch.context() as scoped:
        scoped.setattr(
            control.ontology,
            "query_scoped_concept_ids",
            lambda *_args, **_kwargs: pytest.fail(
                "default-old path called the opt-in scoped matcher"
            ),
        )
        control_response = control.search(
            _search_request("鄂尔多斯盆地", stage="preview")
        ).model_dump(mode="json")

    candidate = _service(
        tmp_path / "candidate",
        query_scoped_concept_pattern_scan_enabled=True,
    )
    candidate.index_snapshot(
        _index_request(_manifest(document_count=len(documents)), documents)
    )
    assert candidate.query_scoped_concept_pattern_scan_enabled is True
    candidate_response = candidate.search(
        _search_request("鄂尔多斯盆地", stage="preview")
    ).model_dump(mode="json")

    assert control_response == candidate_response


def test_preview_observes_no_strong_signal_for_out_of_domain_quantum_query(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    documents = [
        _document("claim-ordos", "The Ordos Basin contains oil shale."),
        _document("claim-ree", "Deep-sea sediment contains rare earth elements."),
    ]
    service.index_snapshot(
        _index_request(_manifest(document_count=len(documents)), documents)
    )

    response = service.search(_search_request("量子纠缠", stage="preview"))

    observation = response.retrieval_confidence
    assert observation is not None
    assert observation.applicability == "applicable"
    assert observation.strong_signal_types == []
    assert observation.exact_anchor_hits == 0
    assert observation.concept_anchor_hits == 0
    assert observation.scientific_anchor_hits == 0
    assert observation.direct_lexical_overlap == 0
    # Observe-only telemetry must not suppress the existing preview.
    assert observation.decision == "return"
    assert observation.would_abstain is None
    assert len(response.preview) == len(documents)


def test_confidence_observation_is_read_only_over_ranked_candidates() -> None:
    candidates = [
        _candidate(_document("claim-first", "alpha basin"), score=0.7),
        _candidate(_document("claim-second", "beta sediment"), score=0.6),
    ]
    before = [
        (
            item.document.claim_id,
            item.lexical,
            item.dense,
            item.rrf,
            item.rerank,
        )
        for item in candidates
    ]

    observation = _retrieval_confidence_observation(
        request=_search_request("alpha basin", stage="preview"),
        preview_candidates=candidates,
        dense_hits=[
            ChannelHit("snapshot-1", "claim-first", 0.9),
            ChannelHit("snapshot-1", "claim-second", 0.8),
        ],
        snapshot_ids=["snapshot-1"],
        dense_snapshot_ids=["snapshot-1"],
        lexical_status="ok",
        dense_status="ok",
        exact_anchor_hits=0,
        concept_anchor_hits=0,
        scientific_anchor_hits=0,
    )

    after = [
        (
            item.document.claim_id,
            item.lexical,
            item.dense,
            item.rrf,
            item.rerank,
        )
        for item in candidates
    ]
    assert after == before
    assert observation.dense_top1 == pytest.approx(0.9)
    assert observation.dense_top2 == pytest.approx(0.8)
    assert observation.dense_margin == pytest.approx(0.1)
    assert observation.dense_top3_mean == pytest.approx(0.85)


def test_retrieval_confidence_contract_rejects_non_finite_or_unbounded_fields() -> None:
    payload = {
        "applicability": "applicable",
        "policy_inapplicable_reasons": [],
        "query_profile": "topic",
        "long_query_truncated": False,
        "direct_lexical_query_tokens": 1,
        "direct_lexical_overlap": 1,
        "direct_lexical_coverage": 1.0,
        "exact_anchor_hits": 0,
        "concept_anchor_hits": 0,
        "scientific_anchor_hits": 0,
        "strong_signal_types": [],
        "dense_observed_hits": 1,
        "dense_top1": 0.8,
        "dense_top2": None,
        "dense_margin": None,
        "dense_top3_mean": 0.8,
    }
    assert RetrievalConfidence.model_validate(payload, strict=True).mode == "observe"

    non_finite = payload | {"dense_top1": float("nan")}
    with pytest.raises(ValidationError):
        RetrievalConfidence.model_validate(non_finite, strict=True)
    unbounded = payload | {"direct_lexical_query_tokens": 257}
    with pytest.raises(ValidationError):
        RetrievalConfidence.model_validate(unbounded, strict=True)


def test_serving_process_warmup_materializes_dense_model(tmp_path: Path) -> None:
    dense = FakeDense()
    service = _service(tmp_path, dense=dense)

    service.warm_models()

    assert service.model_readiness == {
        "dense": True,
        "vector_store": True,
    }


def _candidate(
    payload: dict[str, Any],
    *,
    relation: str = "related",
    score: float = 1.0,
) -> _Candidate:
    document = ClaimSearchDocument.model_validate(payload, strict=True)
    return _Candidate(
        document=document,
        lexical=score,
        dense=score,
        rrf=score,
        decision=RelationDecision(
            candidate_key=relation_candidate_key(
                document.snapshot_id, document.claim_id
            ),
            relation=relation,
            relation_direction="result_to_query",
            relation_confidence=0.8,
            scope_relation="unknown",
        ),
    )


def test_contract_keeps_provenance_and_bounds_transport() -> None:
    document = _document("claim-1", "a claim")
    document["original_claim"] = "x" * 128_000
    document["evidence_anchors"] = [
        {
            "anchor_id": "anchor-1",
            "artifact_version_id": "artifact-snapshot-1",
            "anchor_type": "text",
            "page": 1,
            "section": None,
            "paragraph": 1,
            "quote": "q" * 128_000,
            "figure": None,
            "table": None,
            "row_start": None,
            "row_end": None,
            "row_range_label": None,
            "column_names": [],
            "series": None,
            "char_start": 0,
            "char_end": 128_000,
            "region": None,
            "region_label": None,
            "dataset_path": None,
        }
    ]
    parsed = ClaimSearchDocument.model_validate(document, strict=True)
    assert len(parsed.original_claim or "") == 128_000
    too_long = deepcopy(document)
    too_long["original_claim"] += "x"
    with pytest.raises(ValidationError):
        ClaimSearchDocument.model_validate(too_long, strict=True)
    no_path = deepcopy(document)
    no_path["shortest_evidence_path"] = None
    with pytest.raises(ValidationError):
        ClaimSearchDocument.model_validate(no_path, strict=True)
    request = _index_request(
        _manifest(document_count=50_000),
        [_document("claim-1", "a claim")],
    )
    assert request.manifest.document_count == 50_000
    payload = request.model_dump(mode="json")
    payload["assessments"] = []
    with pytest.raises(ValidationError):
        IndexSnapshotRequest.model_validate(payload, strict=True)


def test_dense_failure_is_repairable_by_idempotent_replay(tmp_path: Path) -> None:
    dense = FakeDense()
    vector = FakeVectorIndex()
    service = _service(tmp_path, dense=dense, vector=vector)
    manifest = _manifest(document_count=2)
    first = _document("claim-1", "mercury increases risk")
    second = _document("claim-2", "lead increases risk")
    dense.fail_documents = True
    assert (
        service.index_snapshot(_index_request(manifest, [first])).manifests[0].status
        == "degraded"
    )
    service.index_snapshot(_index_request(manifest, [second]))
    check_request = ManifestCheckRequest(
        schema_version="evidence-retrieval/2.0",
        project_id="project-1",
        manifests=[RetrievalManifest.model_validate(manifest, strict=True)],
    )
    check = service.check_manifests(check_request)
    assert check.manifests[0].status == "degraded"
    assert check.manifests[0].indexed_document_count == 2
    assert not check.manifests[0].dense_complete

    dense.fail_documents = False
    repair = service.repair_dense_projections()
    assert repair == {
        "attempted_projections": 1,
        "repaired_projections": 1,
        "warnings": [],
    }
    assert service.check_manifests(check_request).manifests[0].status == "indexed"
    replay = service.index_snapshot(_index_request(manifest, [first]))
    assert replay.manifests[0].status == "unchanged"

    changed = deepcopy(first)
    changed["canonical_claim"] = "silently changed projection"
    with pytest.raises(InputError, match="immutable retrieval document conflict"):
        service.index_snapshot(_index_request(manifest, [changed]))

    other = _service(
        tmp_path,
        dense=FakeDense(revision="fake-rev-2"),
        vector=FakeVectorIndex("fake-rev-2"),
    )
    other_check = other.check_manifests(
        ManifestCheckRequest(
            schema_version="evidence-retrieval/2.0",
            project_id="project-1",
            manifests=[RetrievalManifest.model_validate(manifest, strict=True)],
        )
    )
    assert other_check.manifests[0].status == "missing"


def test_explicit_constraints_are_pushed_down_and_channel_failures_degrade(
    tmp_path: Path,
) -> None:
    vector = FakeVectorIndex()
    service = _service(tmp_path, vector=vector)
    documents = [
        _document(
            "claim-good",
            "汞 mercury exposure causes neurological harm",
            claim_type="causal",
        ),
        _document(
            "claim-wrong-type",
            "汞 mercury exposure is measured",
            claim_type="methodological",
        ),
        _document(
            "claim-excluded",
            "汞 mercury exposure causes excluded harm",
            claim_type="causal",
        ),
    ]
    service.index_snapshot(_index_request(_manifest(document_count=3), documents))
    request = _search_request(
        "mercury neurological harm",
        must_terms=["汞"],
        exclude_terms=["excluded"],
        claim_types=["causal"],
        polarity="affirmed",
    )
    response = service.search(request)
    refs = [item for cluster in response.clusters for item in cluster.member_refs]
    assert {(item.snapshot_id, item.claim_id) for item in refs} == {
        ("snapshot-1", "claim-good")
    }
    assert vector.last_search == {
        "limit": 500,
        "claim_types": ["causal"],
        "polarity": "affirmed",
    }
    assert response.retrieval_confidence is None

    vector.fail_search = True
    degraded = service.search(request)
    assert degraded.channels.dense.status == "degraded"
    assert degraded.channels.lexical.status == "ok"
    assert degraded.channels.rerank.status == "skipped"
    assert degraded.clusters
    assert len(degraded.warnings) == 1
    assert degraded.retrieval_confidence is None

    preview = service.search(request.model_copy(update={"stage": "preview"}))
    observation = preview.retrieval_confidence
    assert observation is not None
    assert observation.applicability == "policy_inapplicable"
    assert "explicit_constraints" in observation.policy_inapplicable_reasons
    assert "dense_channel_unavailable" in observation.policy_inapplicable_reasons


def test_query_claim_is_fail_closed_and_drives_seeded_search(tmp_path: Path) -> None:
    service = _service(tmp_path)
    seed = _document("claim-seed", "Mercury exposure increases neurological risk")
    service.index_snapshot(_index_request(_manifest(), [seed]))
    response = service.search(
        _search_request(
            "是否有独立证据？",
            query_claim={"snapshot_id": "snapshot-1", "claim_id": "claim-seed"},
        )
    )
    assert response.query_plan.canonical_claim == seed["canonical_claim"]
    with pytest.raises(InputError, match="not indexed"):
        service.search(
            _search_request(
                "find evidence",
                query_claim={"snapshot_id": "snapshot-1", "claim_id": "missing"},
            )
        )


def test_query_claim_bypasses_same_paper_retrieval_but_includes_it_in_final(
    tmp_path: Path,
) -> None:
    classifier = FakeRelationClassifier()
    service = _service(tmp_path, classifier=classifier)
    seed = _document(
        "claim-seed",
        "Mercury exposure increases neurological risk",
    )
    same_paper = _document(
        "claim-same-paper",
        "Mercury exposure is associated with neurological harm",
    )
    same_paper_duplicate = _document(
        "claim-same-paper-2",
        "Mercury exposure is associated with neurological harm",
    )
    cross_paper = _document(
        "claim-cross-paper",
        "Independent cohorts link mercury exposure to neurological harm",
        snapshot_id="snapshot-2",
        paper_id="paper-2",
        family_id="family-2",
    )
    service.index_snapshot(
        _index_request(
            _manifest(document_count=3),
            [seed, same_paper, same_paper_duplicate],
        )
    )
    service.index_snapshot(
        _index_request(
            _manifest(
                "snapshot-2",
                paper_id="paper-2",
                family_id="family-2",
            ),
            [cross_paper],
        )
    )
    query_claim = {"snapshot_id": "snapshot-1", "claim_id": "claim-seed"}

    preview = service.search(
        _search_request(
            "find related evidence",
            query_claim=query_claim,
            stage="preview",
        )
    )
    assert [(item.snapshot_id, item.claim_id) for item in preview.preview] == [
        ("snapshot-2", "claim-cross-paper")
    ]
    assert classifier.seen == []

    final = service.search(
        _search_request(
            "find related evidence",
            query_claim=query_claim,
            stage="ai_rerank",
            candidate_refs=[
                {
                    "snapshot_id": item.snapshot_id,
                    "claim_id": item.claim_id,
                }
                for item in preview.preview
            ],
        )
    )
    assert set(classifier.seen) == {
        ("snapshot-1", "claim-same-paper"),
        ("snapshot-1", "claim-same-paper-2"),
        ("snapshot-2", "claim-cross-paper"),
    }
    returned = {
        (item.snapshot_id, item.claim_id)
        for cluster in final.clusters
        for item in cluster.member_refs
    }
    assert returned == set(classifier.seen)


def test_query_claim_preserves_closed_label_only_time_scope(tmp_path: Path) -> None:
    service = _service(tmp_path)
    seed = _document(
        "claim-seed",
        "The formation is Middle Jurassic in age",
        time={"label": "Middle Jurassic", "start": None, "end": None, "unit": None},
    )
    service.index_snapshot(_index_request(_manifest(), [seed]))

    response = service.search(
        _search_request(
            "find related evidence",
            query_claim={"snapshot_id": "snapshot-1", "claim_id": "claim-seed"},
            stage="preview",
        )
    )

    assert response.query_plan.scope_constraints is not None
    assert response.query_plan.scope_constraints.time is not None
    assert response.query_plan.scope_constraints.time.label == "Middle Jurassic"
    assert response.query_plan.scope_constraints.time.start is None
    assert response.query_plan.scope_constraints.time.end is None
    assert response.query_plan.scope_constraints.time.unit is None


def test_mixed_language_technical_anchors_beat_same_language_distractors(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    documents = [
        _document(
            "claim-optics",
            "该方法能提高多像光学实验的观测效果。",
        ),
        _document(
            "claim-topoinf",
            "Across ten GNN backbones, TopoInf refinement usually improves "
            "node-classification accuracy.",
        ),
        _document(
            "claim-topoinf-method",
            "TopoInf defines an edge-level topology compatibility objective.",
        ),
    ]
    service.index_snapshot(_index_request(_manifest(document_count=3), documents))

    response = service.search(
        _search_request(
            "topoinf 能提高很多gnn方法的效果",
            stage="preview",
        )
    )

    assert response.query_plan.entities == ["topoinf", "gnn"]
    assert [item.claim_id for item in response.preview][:2] == [
        "claim-topoinf",
        "claim-topoinf-method",
    ]
    assert response.preview[0].scores.rerank is None
    assert response.channels.rerank.status == "skipped"


def test_hyphenated_technical_anchor_and_component_share_one_rank_channel() -> None:
    def candidate(claim_id: str, statement: str, rank_score: float) -> _Candidate:
        return _Candidate(
            document=ClaimSearchDocument.model_validate(_document(claim_id, statement)),
            lexical=None,
            dense=None,
            rrf=rank_score,
        )

    compound = candidate("claim-compound", "REY-rich REY sediment", 0.5)
    component = candidate("claim-component", "REY sediment", 0.4)
    rare = candidate("claim-rare", "DSDP sediment", 0.3)
    independent = candidate("claim-independent", "TopoInf GNN method", 0.2)

    _fuse_exact_anchors(
        [compound, component, rare, independent],
        ["rey-rich", "rey", "dsdp", "topoinf", "gnn"],
        60,
    )

    assert compound.rrf == pytest.approx(0.5 + 1 / 61)
    assert component.rrf == pytest.approx(0.4 + 1 / 62)
    assert rare.rrf == pytest.approx(0.3 + 1 / 61)
    assert independent.rrf == pytest.approx(0.2 + 2 / 61)


def test_exact_number_comparator_and_unit_receive_a_distinct_rank_channel() -> None:
    commercial = _candidate(
        _document(
            "claim-commercial",
            "The commercial air flow rate is 36 kg/s and power is 9.26 MW.",
        ),
        score=0.2,
    )
    pilot = _candidate(
        _document(
            "claim-pilot",
            "The pilot air flow rate is 9 kg/s and power is 2.5 MW.",
        ),
        score=0.3,
    )

    _fuse_scientific_mentions(
        [commercial, pilot],
        "What is produced at an air flow rate of 36 kg/s?",
        60,
    )

    assert commercial.rrf == pytest.approx(0.2 + 1 / 61)
    assert pilot.rrf == 0.3


def test_anchor_fusion_sinks_record_only_candidates_actually_boosted(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    topoinf = _candidate(
        _document("claim-topoinf-sink", "TopoInf improves topology refinement"),
        score=0.4,
    )
    ordos = _candidate(
        _document("claim-ordos-sink", "The Ordos Basin records several stages"),
        score=0.3,
    )
    numeric = _candidate(
        _document("claim-number-sink", "The air flow rate is 36 kg/s"),
        score=0.2,
    )
    unrelated = _candidate(
        _document("claim-unrelated-sink", "An unrelated observation"), score=0.1
    )
    candidates = [topoinf, ordos, numeric, unrelated]
    boosted: set[tuple[str, str]] = set()

    _fuse_exact_anchors(candidates, ["topoinf"], 60, boosted_keys=boosted)
    _fuse_concept_anchors(
        candidates,
        "鄂尔多斯盆地",
        service.ontology,
        60,
        boosted_keys=boosted,
    )
    _fuse_scientific_mentions(
        candidates,
        "What happens at 36 kg/s?",
        60,
        boosted_keys=boosted,
    )

    assert boosted == {
        ("snapshot-1", "claim-topoinf-sink"),
        ("snapshot-1", "claim-ordos-sink"),
        ("snapshot-1", "claim-number-sink"),
    }


def test_selective_safe_dense_views_are_distinct_and_max_cosine_stable() -> None:
    plan = QueryPlan(
        query_type="claim_search",
        canonical_claim="Ordos Basin tectonic evidence",
        must_terms=[],
        optional_terms=[],
        exclude_terms=[],
        entities=[],
        polarity=None,
        claim_types=[],
        scope_constraints=None,
        original_query="鄂尔多斯盆地构造证据",
        planner="gpt",
    )

    assert _additional_dense_query_texts(plan) == [
        "鄂尔多斯盆地构造证据",
        "Ordos Basin tectonic evidence",
    ]
    aggregated = _aggregate_dense_view_hits(
        [
            [
                ChannelHit("snapshot-1", "claim-invalid-finite", 0.99),
                ChannelHit("snapshot-1", "claim-invalid-nan", float("nan")),
            ],
            [
                ChannelHit("snapshot-1", "claim-a", 0.7),
                ChannelHit("snapshot-1", "claim-b", 0.6),
            ],
            [
                ChannelHit("snapshot-1", "claim-b", 0.9),
                ChannelHit("snapshot-1", "claim-c", 0.8),
                ChannelHit("snapshot-1", "claim-a", 0.8),
            ],
            [ChannelHit("snapshot-1", "claim-invalid-inf", float("inf"))],
        ],
        limit=250,
    )
    assert [(item.snapshot_id, item.claim_id, item.score) for item in aggregated] == [
        ("snapshot-1", "claim-b", 0.9),
        ("snapshot-1", "claim-a", 0.8),
        ("snapshot-1", "claim-c", 0.8),
    ]


def test_selective_safe_reserve_deduplicates_roles_and_protects_boundary() -> None:
    candidates = [
        _candidate(
            _document(f"claim-reserve-{index:02d}", f"reserve evidence {index}"),
            score=1.0 - index / 100.0,
        )
        for index in range(1, 15)
    ]
    scores_before = {item.document.claim_id: item.rrf for item in candidates}
    shared_top1 = ChannelHit("snapshot-1", "claim-reserve-14", 0.9)

    _apply_selective_safe_top12_reserve(
        candidates,
        [shared_top1],
        [shared_top1, ChannelHit("snapshot-1", "claim-reserve-13", 0.8)],
        protected_keys={("snapshot-1", "claim-reserve-12")},
    )

    assert [item.document.claim_id for item in candidates[:12]] == [
        *[f"claim-reserve-{index:02d}" for index in range(1, 11)],
        "claim-reserve-12",
        "claim-reserve-14",
    ]
    assert [item.document.claim_id for item in candidates[12:]] == [
        "claim-reserve-11",
        "claim-reserve-13",
    ]
    assert {item.document.claim_id: item.rrf for item in candidates} == scores_before


def test_selective_safe_reserve_uses_at_most_two_seats_and_never_falls_to_top2() -> (
    None
):
    def baseline() -> list[_Candidate]:
        return [
            _candidate(
                _document(f"claim-two-seat-{index:02d}", f"two seat evidence {index}"),
                score=1.0 - index / 100.0,
            )
            for index in range(1, 15)
        ]

    candidates = baseline()
    top10_before = [
        (item.document.claim_id, item.rrf, id(item)) for item in candidates[:10]
    ]
    _apply_selective_safe_top12_reserve(
        candidates,
        [ChannelHit("snapshot-1", "claim-two-seat-13", 0.9)],
        [ChannelHit("snapshot-1", "claim-two-seat-14", 0.9)],
        protected_keys=set(),
    )
    assert [item.document.claim_id for item in candidates[:12]] == [
        *[f"claim-two-seat-{index:02d}" for index in range(1, 11)],
        "claim-two-seat-13",
        "claim-two-seat-14",
    ]
    assert [
        (item.document.claim_id, item.rrf, id(item)) for item in candidates[:10]
    ] == top10_before

    unavailable = baseline()
    before = [item.document.claim_id for item in unavailable]
    _apply_selective_safe_top12_reserve(
        unavailable,
        [
            ChannelHit("snapshot-outside", "claim-outside", 1.0),
            ChannelHit("snapshot-1", "claim-two-seat-13", 0.9),
        ],
        [],
        protected_keys=set(),
    )
    assert [item.document.claim_id for item in unavailable] == before


def test_selective_safe_reserve_never_uses_an_unprotected_top10_candidate() -> None:
    candidates = [
        _candidate(
            _document(
                f"claim-top10-guard-{index:02d}", f"top ten guard evidence {index}"
            ),
            score=1.0 - index / 100.0,
        )
        for index in range(1, 14)
    ]
    before = [(item.document.claim_id, item.rrf, id(item)) for item in candidates]
    protected = {
        ("snapshot-1", f"claim-top10-guard-{index:02d}") for index in range(2, 13)
    }

    _apply_selective_safe_top12_reserve(
        candidates,
        [ChannelHit("snapshot-1", "claim-top10-guard-13", 0.9)],
        [],
        protected_keys=protected,
    )

    assert [
        (item.document.claim_id, item.rrf, id(item)) for item in candidates
    ] == before


def test_hyphenated_anchor_matches_one_unambiguous_spaced_phrase_family() -> None:
    first = _candidate(
        _document(
            "claim-sulfuric-first",
            "Sulfuric acid leaching releases the extractable fraction.",
            family_id="family-leaching",
        ),
        score=0.4,
    )
    second = _candidate(
        _document(
            "claim-sulfuric-second",
            "The sulfuric acid extraction confirms the leaching result.",
            family_id="family-leaching",
        ),
        score=0.3,
    )
    acid_elsewhere = _candidate(
        _document(
            "claim-acid-elsewhere",
            "Acid digestion was used for an unrelated measurement.",
            snapshot_id="snapshot-acid-elsewhere",
            paper_id="paper-acid-elsewhere",
            family_id="family-acid-elsewhere",
        ),
        score=0.2,
    )

    _fuse_exact_anchors([first, second, acid_elsewhere], ["sulfuric-acid"], 60)

    assert first.rrf == pytest.approx(0.4 + 1 / 61)
    assert second.rrf == pytest.approx(0.3 + 1 / 62)
    assert acid_elsewhere.rrf == 0.2


def test_hyphenated_anchor_phrase_bridge_fails_closed_when_ambiguous() -> None:
    late_eocene = [
        _candidate(
            _document(
                f"claim-late-eocene-{index}",
                f"A late Eocene event was observed at site {index}.",
                family_id="family-late-eocene",
            ),
            score=score,
        )
        for index, score in enumerate((0.5, 0.4), start=1)
    ]
    late_elsewhere = _candidate(
        _document("claim-late-only", "A late interval was identified."),
        score=0.3,
    )
    eocene_elsewhere = _candidate(
        _document("claim-eocene-only", "An Eocene interval was identified."),
        score=0.2,
    )
    late_candidates = [*late_eocene, late_elsewhere, eocene_elsewhere]

    _fuse_exact_anchors(late_candidates, ["late-eocene"], 60)

    assert [item.rrf for item in late_candidates] == [0.5, 0.4, 0.3, 0.2]

    fish_left = _candidate(
        _document(
            "claim-fish-left",
            "Fish debris accumulated at the first site.",
            family_id="family-fish-left",
        ),
        score=0.4,
    )
    fish_right = _candidate(
        _document(
            "claim-fish-right",
            "Fish debris accumulated at the second site.",
            snapshot_id="snapshot-fish-right",
            paper_id="paper-fish-right",
            family_id="family-fish-right",
        ),
        score=0.3,
    )

    _fuse_exact_anchors([fish_left, fish_right], ["fish-debris"], 60)

    assert fish_left.rrf == 0.4
    assert fish_right.rrf == 0.3


def test_duplicate_compound_anchor_cannot_evict_rare_evidence_from_top12(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    distractors = [
        _document(
            f"claim-rey-{index:02}",
            f"REY-rich REY sediment includes common deposit observation {index}.",
        )
        for index in range(12)
    ]
    target = _document(
        "claim-dsdp-target",
        "DSDP sediment provenance fingerprint independently confirms the "
        "selective deep-sea observation.",
    )
    service.index_snapshot(
        _index_request(_manifest(document_count=13), [*distractors, target])
    )

    response = service.search(
        _search_request(
            "DSDP sediment provenance fingerprint independently confirms "
            "the selective deep-sea observation in REY-rich REY deposits",
            stage="preview",
            limit=12,
        )
    )

    assert len(response.preview) == 12
    assert any(item.claim_id == "claim-dsdp-target" for item in response.preview)
    assert response.channels.lexical.status == "ok"
    assert response.channels.dense.status == "ok"
    assert response.channels.relation.status == "skipped"


def test_soft_family_diversity_preserves_three_claims_without_hard_cap() -> None:
    candidates = [
        _candidate(
            _document(
                f"claim-family-{index}",
                f"family evidence {index}",
                family_id="family-repeated",
            ),
            score=score,
        )
        for index, score in enumerate((0.50, 0.40, 0.30, 0.2005, 0.19), start=1)
    ]
    distinct = _candidate(
        _document(
            "claim-distinct",
            "independent family evidence",
            snapshot_id="snapshot-distinct",
            paper_id="paper-distinct",
            family_id="family-distinct",
        ),
        score=0.20,
    )
    candidates.insert(4, distinct)

    _apply_soft_family_diversity(candidates)
    candidates.sort(key=lambda item: -item.rrf)

    assert [item.document.claim_id for item in candidates[:3]] == [
        "claim-family-1",
        "claim-family-2",
        "claim-family-3",
    ]
    assert candidates[3] is distinct
    assert candidates[4].document.claim_id == "claim-family-4"
    assert candidates[4].rrf == pytest.approx(0.2005 * 0.99)
    assert candidates[-1].rrf == pytest.approx(0.19 * 0.99**2)


def test_guarded_cross_language_dense_reserve_is_strict_and_score_sorted() -> None:
    candidates = [
        _candidate(
            _document(
                f"claim-zh-{index}",
                f"深海采矿候选证据 {index}",
                snapshot_id=f"snapshot-zh-{index}",
                paper_id=f"paper-zh-{index}",
                family_id=f"family-zh-{index}",
            ),
            score=score,
        )
        for index, score in enumerate((0.50, 0.40, 0.30), start=1)
    ]
    target = _candidate(
        _document(
            "claim-en-target",
            "Independent toxicity evidence supports precautionary approval.",
            snapshot_id="snapshot-en",
            paper_id="paper-en",
            family_id="family-en",
        ),
        score=0.10,
    )
    candidates.append(target)
    dense_hits = [
        ChannelHit("snapshot-en", "claim-en-target", 0.70),
        ChannelHit("snapshot-zh-1", "claim-zh-1", 0.65),
    ]

    _apply_guarded_cross_language_dense_reserve(
        candidates,
        [ChannelHit("snapshot-zh-1", "claim-zh-1", 9.0)],
        dense_hits,
        "深海采矿审批如何保护“海绵栖息地”与微生物连通性？",
    )
    assert target.rrf == 0.10

    _apply_guarded_cross_language_dense_reserve(
        candidates,
        [ChannelHit("snapshot-zh-1", "claim-zh-1", 9.0)],
        dense_hits,
        "深海采矿审批如何保护海绵栖息地与微生物连通性？",
    )
    candidates.sort(key=lambda item: -item.rrf)

    assert candidates[2] is target
    assert candidates[1].rrf > target.rrf > candidates[3].rrf

    original = target.rrf
    _apply_guarded_cross_language_dense_reserve(
        candidates,
        [ChannelHit("snapshot-zh-1", "claim-zh-1", 9.0)],
        [
            ChannelHit("snapshot-en", "claim-en-target", 0.70),
            ChannelHit("snapshot-zh-1", "claim-zh-1", 0.67),
        ],
        "深海采矿审批如何保护海绵栖息地与微生物连通性？",
    )
    assert target.rrf == original


def test_common_single_cjk_bigram_cannot_outrank_cross_language_dense_evidence() -> (
    None
):
    wrong = [
        _candidate(
            _document(
                f"claim-wrong-{index}",
                f"皮加费塔盆地深海稀土过程 {index}",
                snapshot_id=f"snapshot-wrong-{index}",
                paper_id="paper-wrong",
                family_id="family-wrong",
            ),
            score=1 / (60 + index + 1) + 1 / (60 + 67 + index),
        )
        for index in range(3)
    ]
    correct = _candidate(
        _document(
            "claim-ordos",
            "The Chang 7 oil shale is the main oil-shale seam of the Ordos Basin.",
            snapshot_id="snapshot-ordos",
            paper_id="paper-ordos",
            family_id="family-ordos",
        ),
        score=1 / 61,
    )
    lexical_hits = [
        ChannelHit(item.document.snapshot_id, item.document.claim_id, 9.0 - index)
        for index, item in enumerate(wrong)
    ]

    candidates = [*wrong, correct]
    _discount_sparse_cjk_lexical_channel(
        candidates,
        lexical_hits,
        "鄂尔多斯盆地 有没有石油",
        60,
    )

    assert sorted(candidates, key=lambda item: -item.rrf)[0] is correct
    assert wrong[0].rrf == pytest.approx(0.25 / 61 + 1 / 127)


def test_sparse_cjk_discount_preserves_rare_and_multi_bigram_matches() -> None:
    rare = _candidate(
        _document("claim-rare-place", "黄岩海山矿物组成"),
        score=1 / 61,
    )
    multi = _candidate(
        _document("claim-multi", "南海东部海山结核矿物组成"),
        score=1 / 62,
    )

    _discount_sparse_cjk_lexical_channel(
        [rare],
        [ChannelHit("snapshot-1", "claim-rare-place", 5.0)],
        "黄岩海山的矿物是什么",
        60,
    )
    _discount_sparse_cjk_lexical_channel(
        [rare, multi],
        [
            ChannelHit("snapshot-1", "claim-rare-place", 5.0),
            ChannelHit("snapshot-1", "claim-multi", 4.0),
        ],
        "南海东部海山结核的矿物组成",
        60,
    )

    assert rare.rrf == pytest.approx(1 / 61)
    assert multi.rrf == pytest.approx(1 / 62)


def test_multicharacter_cjk_fts_omits_noisy_unigrams() -> None:
    query = fts_query(["topoinf 能提高很多gnn方法的效果"])

    assert '"topoinf"' in query
    assert '"gnn"' in query
    assert '"提高"' in query
    assert '"高"' not in query
    assert '"的"' not in query


def test_preview_thirty_signed_candidates_reach_final(tmp_path: Path) -> None:
    classifier = FakeRelationClassifier()
    service = _service(
        tmp_path, classifier=classifier,
        query_plan_handoff_secret=_HANDOFF_SECRET_A,
    )
    documents = [
        _document(f"claim-limit-{i}", f"alpha evidence observation {i}")
        for i in range(35)
    ]
    service.index_snapshot(
        _index_request(_manifest(document_count=len(documents)), documents)
    )
    request = _search_request(
        "alpha evidence", stage="preview", limit=50, intent="supporting_evidence",
        schema_version="evidence-retrieval/2.1",
    )
    preview = service.search(request)
    assert len(preview.preview) == 30
    refs = [
        {"snapshot_id": item.snapshot_id, "claim_id": item.claim_id}
        for item in preview.preview
    ]
    final = HybridSearchRequest.model_validate({
        **request.model_dump(), "stage": "ai_rerank",
        "candidate_refs": refs, "query_plan_handoff": preview.query_plan_handoff,
    })
    service.search(final)
    assert classifier.seen == [(r["snapshot_id"], r["claim_id"]) for r in refs]
    with pytest.raises(ValidationError):
        HybridSearchRequest.model_validate({
            **final.model_dump(), "candidate_refs": [*refs, refs[0]],
        })


def test_preview_exposes_cross_language_top12_before_ai_orders_same_candidates(
    tmp_path: Path,
) -> None:
    class ReverseClassifier(FakeRelationClassifier):
        def classify(
            self, plan: QueryPlan, candidates: Sequence[ClaimSearchDocument]
        ) -> list[RelationDecision]:
            decisions = super().classify(plan, candidates)
            return list(reversed(decisions))

    classifier = ReverseClassifier()
    service = _service(tmp_path, classifier=classifier)
    documents = [
        _document(
            "claim-topoinf",
            "Across ten GNN backbones, TopoInf refinement usually improves "
            "test accuracy.",
        ),
        _document("claim-other", "TopoInf defines an edge compatibility objective."),
    ]
    service.index_snapshot(_index_request(_manifest(document_count=2), documents))

    preview = service.search(
        _search_request(
            "topoinf 能提高很多gnn方法的效果",
            stage="preview",
        )
    )
    assert preview.stage == "preview"
    assert preview.clusters == []
    assert [item.claim_id for item in preview.preview] == [
        "claim-topoinf",
        "claim-other",
    ]
    assert preview.channels.relation.status == "skipped"
    assert classifier.seen == []

    candidate_refs = [
        {"snapshot_id": item.snapshot_id, "claim_id": item.claim_id}
        for item in preview.preview
    ]
    final = service.search(
        _search_request(
            "topoinf 能提高很多gnn方法的效果",
            stage="ai_rerank",
            candidate_refs=candidate_refs,
        )
    )
    assert final.stage == "ai_rerank"
    assert final.preview == []
    assert final.channels.relation.provider == "fake-relation"
    assert [cluster.representative.claim_id for cluster in final.clusters] == [
        "claim-other",
        "claim-topoinf",
    ]


def test_model_relation_direction_is_projected_to_search_members(
    tmp_path: Path,
) -> None:
    canonical = "A broad claim directly implies this narrower result"
    classifier = FakeRelationClassifier(
        {canonical: "supports"},
        {canonical: "query_to_result"},
    )
    service = _service(tmp_path, classifier=classifier)
    service.index_snapshot(
        _index_request(_manifest(), [_document("claim-directed", canonical)])
    )

    response = service.search(_search_request(canonical))

    assert response.clusters[0].representative.relation == "supports"
    assert response.clusters[0].representative.relation_direction == "query_to_result"


@pytest.mark.parametrize("coverage_error", ["missing", "duplicate", "extra"])
def test_relation_classifier_requires_exact_candidate_key_coverage(
    tmp_path: Path,
    coverage_error: str,
) -> None:
    class InvalidCoverageClassifier(FakeRelationClassifier):
        def classify(
            self, plan: QueryPlan, candidates: Sequence[ClaimSearchDocument]
        ) -> list[RelationDecision]:
            decisions = super().classify(plan, candidates)
            assert len(decisions) >= 2
            if coverage_error == "missing":
                return decisions[:-1]
            if coverage_error == "duplicate":
                return [*decisions[:-1], decisions[0]]
            return [
                *decisions,
                RelationDecision(
                    candidate_key=relation_candidate_key(
                        "snapshot-extra", "claim-extra"
                    ),
                    relation="related",
                    relation_direction="result_to_query",
                    relation_confidence=0.8,
                    scope_relation="equivalent",
                ),
            ]

    service = _service(tmp_path, classifier=InvalidCoverageClassifier())
    documents = [
        _document("claim-1", "Mercury exposure increases neurological risk"),
        _document("claim-2", "Mercury exposure is associated with health risk"),
    ]
    service.index_snapshot(
        _index_request(_manifest(document_count=len(documents)), documents)
    )

    response = service.search(
        _search_request("Mercury exposure increases health risk")
    )

    assert response.channels.relation.status == "degraded"
    assert response.clusters == []
    assert response.warnings == ["AI relation reranking failed; preview retained"]


def test_ai_failure_never_substitutes_deterministic_relation_rules(
    tmp_path: Path,
) -> None:
    dense = FakeDense()
    service = RetrievalService(
        data_dir=tmp_path,
        dense_encoder=dense,
        vector_index=FakeVectorIndex(dense.revision),
        relation_classifier=None,
    )
    document = _document("claim-1", "Mercury exposure increases risk")
    service.index_snapshot(_index_request(_manifest(), [document]))
    preview = service.search(
        _search_request("Mercury exposure increases risk", stage="preview")
    )
    final = service.search(
        _search_request(
            "Mercury exposure increases risk",
            stage="ai_rerank",
            candidate_refs=[
                {
                    "snapshot_id": preview.preview[0].snapshot_id,
                    "claim_id": preview.preview[0].claim_id,
                }
            ],
        )
    )
    assert final.clusters == []
    assert final.channels.relation.status == "degraded"
    assert final.channels.relation.provider == "gpt"
    assert final.warnings == ["AI relation reranking is unavailable; preview retained"]


def test_ai_relation_failure_logs_safe_diagnostics_without_sensitive_text(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "sk-private-fixture-never-log"
    private_candidate_key = "candidate:sha256:" + "a" * 64
    failure = ModelOutputError(
        f"provider content {private_candidate_key} and secret {secret} "
        "must never be logged",
        diagnostics=(
            ModelAttemptDiagnostic(
                attempt=1,
                classification="request_failed",
                request_error_kind=ModelRequestErrorKind.AUTHENTICATION,
                request_http_status=401,
            ),
            ModelAttemptDiagnostic(
                attempt=2,
                classification="schema_validation_failed",
                finish_reason="stop",
            ),
            ModelAttemptDiagnostic(
                attempt=3,
                classification="contract_validation_failed",
                finish_reason="stop",
                contract_issue=ContractIssue.RETRIEVAL_RELATION_CONFIDENCE_MISSING,
                contract_loc=("decisions", 0, "relation_confidence"),
            ),
        ),
    )

    class FailingRelationClassifier(FakeRelationClassifier):
        def classify(
            self,
            plan: QueryPlan,
            candidates: Sequence[ClaimSearchDocument],
        ) -> list[RelationDecision]:
            del plan, candidates
            raise failure

    service = _service(tmp_path, classifier=FailingRelationClassifier())
    service.index_snapshot(
        _index_request(
            _manifest(), [_document("claim-1", "Mercury exposure increases risk")]
        )
    )

    with caplog.at_level(logging.WARNING, logger="compiler_service.retrieval_service"):
        response = service.search(_search_request("Mercury exposure increases risk"))

    assert response.channels.relation.status == "degraded"
    assert response.warnings == ["AI relation reranking failed; preview retained"]
    record = next(
        item
        for item in caplog.records
        if item.name == "compiler_service.retrieval_service"
    )
    message = record.getMessage()
    assert "ModelOutputError" in message
    assert "MODEL_OUTPUT_INVALID" in message
    assert "request_failed" in message
    assert "authentication_failed" in message
    assert "401" in message
    assert "schema_validation_failed" in message
    assert "stop" in message
    assert "retrieval_relation_confidence_missing" in message
    assert "('decisions', 0, 'relation_confidence')" in message
    assert secret not in caplog.text
    assert private_candidate_key not in caplog.text
    assert "provider content" not in caplog.text


def test_relation_identity_intent_order_and_explicit_edge_snapshot_safety(
    tmp_path: Path,
) -> None:
    class ContradictionFirst(FakeRelationClassifier):
        def classify(
            self, plan: QueryPlan, candidates: Sequence[ClaimSearchDocument]
        ) -> list[RelationDecision]:
            decisions = super().classify(plan, candidates)
            return sorted(
                decisions,
                key=lambda item: 0 if item.relation == "contradicts" else 1,
            )

    classifier = ContradictionFirst(
        {
            "same proposition": "equivalent",
            "same proposition but opposite": "contradicts",
        }
    )
    service = _service(tmp_path, classifier=classifier)
    first_manifest = _manifest("snapshot-a", paper_id="paper-a", family_id="family-a")
    second_manifest = _manifest("snapshot-b", paper_id="paper-b", family_id="family-b")
    first = _document(
        "shared-claim-id",
        "same proposition",
        snapshot_id="snapshot-a",
        paper_id="paper-a",
        family_id="family-a",
        location=["global"],
    )
    second = _document(
        "shared-claim-id",
        "same proposition but opposite",
        snapshot_id="snapshot-b",
        paper_id="paper-b",
        family_id="family-b",
        location=["global"],
    )
    service.index_snapshot(_index_request(first_manifest, [first]))
    service.index_snapshot(_index_request(second_manifest, [second]))
    response = service.search(
        _search_request(
            "same proposition",
            intent="contradiction_search",
            scope={"location": ["global"]},
        )
    )
    assert set(classifier.seen) == {
        ("snapshot-a", "shared-claim-id"),
        ("snapshot-b", "shared-claim-id"),
    }
    assert response.clusters[0].relation == "contradicts"

    relation = {
        "edge_id": "edge-1",
        "source_claim_id": "query-claim",
        "target_claim_id": "candidate-claim",
        "other_claim_id": "candidate-claim",
        "relation": "supports",
        "direction": "outgoing",
        "confidence": 0.0,
        "scope_overlap": "equivalent",
    }
    query = ClaimSearchDocument.model_validate(
        _document(
            "query-claim",
            "query",
            explicit_relations=[relation],
        ),
        strict=True,
    )
    same_snapshot = ClaimSearchDocument.model_validate(
        _document("candidate-claim", "candidate"), strict=True
    )
    other_snapshot = ClaimSearchDocument.model_validate(
        _document(
            "candidate-claim",
            "candidate",
            snapshot_id="snapshot-other",
            paper_id="paper-other",
            family_id="family-other",
        ),
        strict=True,
    )
    explicit = _explicit_relation(query, same_snapshot)
    assert explicit is not None and explicit[0].relation_confidence == 0.0
    assert _explicit_relation(query, other_snapshot) is None

    relation["confidence"] = None
    unknown_confidence_query = ClaimSearchDocument.model_validate(
        _document(
            "query-claim",
            "query",
            explicit_relations=[relation],
        ),
        strict=True,
    )
    unknown_confidence = _explicit_relation(unknown_confidence_query, same_snapshot)
    assert unknown_confidence is not None
    assert unknown_confidence[0].relation_confidence is None


def test_relation_blockers_and_geologic_time_units_are_conservative() -> None:
    document = ClaimSearchDocument.model_validate(
        _document(
            "claim-1",
            "mercury raises risk",
            polarity="negated",
            location=["South China"],
            entities=["mercury"],
        ),
        strict=True,
    )
    plan = QueryPlan(
        query_type="claim_search",
        original_query="mercury raises risk",
        canonical_claim="mercury raises risk",
        must_terms=[],
        optional_terms=[],
        exclude_terms=[],
        entities=["汞"],
        polarity="affirmed",
        claim_types=[],
        scope_constraints=SearchScopeFilter(location=["华南"]),
        planner="deterministic",
    )
    proposed = RelationDecision(
        candidate_key=relation_candidate_key("snapshot-1", "claim-1"),
        relation="contradicts",
        relation_direction="query_to_result",
        relation_confidence=0.9,
        scope_relation="overlap",
    )
    safe = _safe_relation_decision(plan, document, proposed)
    assert safe.scope_relation == "unknown"
    assert safe.relation != "contradicts"
    assert safe.relation_direction == "result_to_query"

    no_scope = plan.model_copy(update={"scope_constraints": None, "polarity": None})
    direct = proposed.model_copy(
        update={"relation": "supports", "scope_relation": "unknown"}
    )
    assert (
        _safe_relation_decision(no_scope, document, direct).relation_direction
        == "query_to_result"
    )

    equivalent = proposed.model_copy(
        update={"relation": "equivalent", "scope_relation": "unknown"}
    )
    assert (
        _safe_relation_decision(no_scope, document, equivalent).relation == "equivalent"
    )
    assert _safe_relation_decision(plan, document, equivalent).relation != "equivalent"

    left = type("Time", (), {"start": 0.25, "end": 0.25, "unit": "Ga"})()
    right = type("Time", (), {"start": 250.0, "end": 250.0, "unit": "Ma"})()
    assert _time_disjoint(left, right) is False
    containing = SearchScopeFilter.model_validate(
        {"time": {"label": None, "start": 1.0, "end": 2.0, "unit": "Ma"}},
        strict=True,
    )
    contained = ClaimSearchDocument.model_validate(
        _document(
            "claim-time",
            "dated result",
            time={
                "label": None,
                "start": 1_500.0,
                "end": 1_800.0,
                "unit": "ka",
            },
        ),
        strict=True,
    )
    assert _scope_relation(containing, contained.scope) == "candidate_within_query"
    open_ended = SearchScopeFilter.model_validate(
        {"time": {"label": None, "start": 1.5, "end": None, "unit": "Ma"}},
        strict=True,
    )
    assert _scope_relation(open_ended, contained.scope) == "candidate_within_query"
    unknown_unit = SearchScopeFilter.model_validate(
        {"time": {"label": None, "start": 1.0, "end": 2.0, "unit": "epoch"}},
        strict=True,
    )
    assert _scope_relation(unknown_unit, contained.scope) == "unknown"


def test_scientific_signatures_and_qualifiers_block_false_equivalence() -> None:
    numeric_left = _candidate(
        _document(
            "claim-numeric-left",
            "Mercury exposure increased neurological risk by 10% in adults",
        ),
        relation="equivalent",
    )
    numeric_right = _candidate(
        _document(
            "claim-numeric-right",
            "Mercury exposure increased neurological risk by 20% in adults",
        ),
        relation="equivalent",
    )
    direction_left = _candidate(
        _document(
            "claim-direction-left",
            "Mercury exposure increased neurological risk in adults",
        ),
        relation="equivalent",
    )
    direction_right = _candidate(
        _document(
            "claim-direction-right",
            "Mercury exposure decreased neurological risk in adults",
        ),
        relation="equivalent",
    )
    assert not _documents_cluster_compatible(numeric_left, numeric_right)
    assert not _documents_cluster_compatible(direction_left, direction_right)

    qualifier_left_payload = _document(
        "claim-qualifier-left",
        "Mercury exposure increased neurological risk in adults",
        snapshot_id="snapshot-left",
        paper_id="paper-left",
        family_id="family-left",
    )
    qualifier_left_payload["qualifiers"] = ["under high-dose exposure"]
    qualifier_right_payload = _document(
        "claim-qualifier-right",
        "Mercury exposure increased neurological risk in adults",
        snapshot_id="snapshot-right",
        paper_id="paper-right",
        family_id="family-right",
    )
    qualifier_right_payload["qualifiers"] = ["under low-dose exposure"]
    qualifier_left = _candidate(qualifier_left_payload, relation="equivalent")
    qualifier_right = _candidate(qualifier_right_payload, relation="equivalent")
    assert not _documents_cluster_compatible(qualifier_left, qualifier_right)
    split_clusters = _cluster_candidates([qualifier_left, qualifier_right], limit=10)
    assert len(split_clusters) == 2
    assert len({item.cluster_id for item in split_clusters}) == 2

    for query_claim, candidate in (
        (
            "Mercury exposure increased neurological risk by 10% in adults",
            numeric_right.document,
        ),
        (
            "Mercury exposure increased neurological risk in adults",
            direction_right.document,
        ),
    ):
        plan = QueryPlan(
            query_type="claim_search",
            original_query=query_claim,
            canonical_claim=query_claim,
            must_terms=[],
            optional_terms=[],
            exclude_terms=[],
            entities=[],
            polarity=None,
            claim_types=[],
            scope_constraints=None,
            planner="deterministic",
        )
        proposed = RelationDecision(
            candidate_key=relation_candidate_key(
                candidate.snapshot_id, candidate.claim_id
            ),
            relation="equivalent",
            relation_direction="result_to_query",
            relation_confidence=0.95,
            scope_relation="unknown",
        )
        assert _safe_relation_decision(plan, candidate, proposed).relation in {
            "qualifies",
            "related",
        }


def test_stored_claims_outside_the_query_filter_contract_still_relate() -> None:
    # Live data that stopped the relation backfill: a Claim without original
    # wording, and Scope values the filter's NFKC+lower key merges.
    raw = _document(
        "claim-1",
        "NdCl2+ dominates neodymium transport in brines",
        entities=["NdCl²⁺", "NdCl₂⁺", "Nd³⁺"],
    )
    raw["original_claim"] = None
    document = ClaimSearchDocument.model_validate(raw, strict=True)
    scope = _scope_filter(document.scope)
    assert scope is not None and scope.entities == ["NdCl²⁺", "Nd³⁺"]
    plan = QueryPlan(
        query_type="claim_search",
        original_query="NdCl2+ dominates neodymium transport",
        canonical_claim="NdCl2+ dominates neodymium transport",
        must_terms=[],
        optional_terms=[],
        exclude_terms=[],
        entities=[],
        polarity=None,
        claim_types=[],
        scope_constraints=scope,
        planner="deterministic",
    )
    proposed = RelationDecision(
        candidate_key=relation_candidate_key("snapshot-1", "claim-1"),
        relation="supports",
        relation_direction="result_to_query",
        relation_confidence=0.8,
        scope_relation="unknown",
    )
    assert _safe_relation_decision(plan, document, proposed).relation != "unrelated"


def test_stored_scope_lists_are_bounded_after_deduplication_without_mutation():
    values = ["NdCl²⁺", "NdCl₂⁺"] + [f"species-{index}" for index in range(70)]
    document = ClaimSearchDocument.model_validate(
        _document("claim-large-scope", "Species measured in brines", entities=values),
        strict=True,
    )
    before = document.scope.model_dump()
    scope = _scope_filter(document.scope)
    assert scope is not None
    assert scope.entities == ["NdCl²⁺"] + values[2:65]
    assert len(scope.entities) == 64
    assert document.scope.model_dump() == before


def test_empty_scope_boundary_internal_conversion_and_seeded_search(
    tmp_path: Path,
) -> None:
    empty_document = ClaimSearchDocument.model_validate(
        _document("claim-empty", "Mercury exposure increases risk"), strict=True
    )
    assert _scope_filter(empty_document.scope) is None
    assert _documents_cluster_compatible(
        _candidate(
            _document("claim-empty-left", "Mercury exposure increases risk"),
            relation="equivalent",
        ),
        _candidate(
            _document("claim-empty-right", "Mercury exposure increases risk"),
            relation="equivalent",
        ),
    )

    invalid = _search_request("Mercury exposure increases risk").model_dump(mode="json")
    invalid["scope"] = {"location": []}
    with pytest.raises(ValidationError, match="effective constraint"):
        HybridSearchRequest.model_validate(invalid, strict=True)
    with pytest.raises(ValidationError, match="meaningful"):
        QueryPlan(
            query_type="claim_search",
            original_query="Mercury exposure increases risk",
            canonical_claim="Mercury exposure increases risk",
            must_terms=[],
            optional_terms=[],
            exclude_terms=[],
            entities=[],
            polarity=None,
            claim_types=[],
            scope_constraints=SearchScopeFilter(location=[]),
            planner="deterministic",
        )

    service = _service(tmp_path)
    service.index_snapshot(
        _index_request(_manifest(), [_document("claim-seed", "Mercury risk")])
    )
    response = service.search(
        _search_request(
            "find independent evidence",
            query_claim={"snapshot_id": "snapshot-1", "claim_id": "claim-seed"},
        )
    )
    assert response.query_plan.scope_constraints is None


def test_relation_fallback_uses_original_cross_language_query() -> None:
    claim = "实验中改变 NaCl 浓度测得虹霓红光视角与理论值吻合"
    document = ClaimSearchDocument.model_validate(
        _document("claim-rainbow", claim), strict=True
    )
    plan = QueryPlan(
        query_type="claim_search",
        original_query=claim,
        canonical_claim=(
            "Changing NaCl concentration produced rainbow viewing angles "
            "that matched theory"
        ),
        must_terms=[],
        optional_terms=[],
        exclude_terms=[],
        entities=["NaCl"],
        polarity="affirmed",
        claim_types=[],
        scope_constraints=None,
        planner="gpt",
    )

    assert _safe_relation_decision(plan, document, None).relation != "unrelated"


def test_zero_claim_latest_manifest_suppresses_old_family_snapshot(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    old = _manifest(
        "snapshot-old",
        family_id="family-shared",
        created_at="2026-08-18T10:00:00+02:00",
    )
    old_document = _document(
        "claim-old",
        "obsolete mercury result",
        snapshot_id="snapshot-old",
        family_id="family-shared",
    )
    service.index_snapshot(_index_request(old, [old_document]))
    empty = _manifest(
        "snapshot-empty",
        family_id="family-shared",
        document_count=0,
        created_at="2026-08-18T09:30:00Z",
    )
    result = service.index_snapshot(_index_request(empty, []))
    assert result.manifests[0].status == "indexed"
    response = service.search(_search_request("obsolete mercury result"))
    assert response.clusters == []
    assert response.result_status == "no_direct_answer"


def test_unrelated_candidates_are_removed_from_final_clusters(
    tmp_path: Path,
) -> None:
    canonical = "The retrieval result is an unrelated noise candidate"
    classifier = FakeRelationClassifier({canonical: "unrelated"})
    service = _service(tmp_path, classifier=classifier)
    service.index_snapshot(
        _index_request(_manifest(), [_document("claim-noise", canonical)])
    )
    response = service.search(_search_request(canonical))
    assert response.channels.relation.returned == 1
    assert response.clusters == []
    assert response.result_status == "no_direct_answer"


def test_cluster_ranking_round_robins_representative_paper_families() -> None:
    candidates = [
        _candidate(
            _document(
                "claim-a1",
                "Mercury exposure finding alpha",
                snapshot_id="snapshot-a1",
                paper_id="paper-a",
                family_id="family-a",
            ),
            score=5.0,
        ),
        _candidate(
            _document(
                "claim-a2",
                "Lead exposure finding beta",
                snapshot_id="snapshot-a2",
                paper_id="paper-a",
                family_id="family-a",
            ),
            score=4.0,
        ),
        _candidate(
            _document(
                "claim-a3",
                "Cadmium exposure finding gamma",
                snapshot_id="snapshot-a3",
                paper_id="paper-a",
                family_id="family-a",
            ),
            score=3.0,
        ),
        _candidate(
            _document(
                "claim-b1",
                "Arsenic exposure finding delta",
                snapshot_id="snapshot-b1",
                paper_id="paper-b",
                family_id="family-b",
            ),
            score=2.0,
        ),
        _candidate(
            _document(
                "claim-c1",
                "Selenium exposure finding epsilon",
                snapshot_id="snapshot-c1",
                paper_id="paper-c",
                family_id="family-c",
            ),
            score=1.0,
        ),
    ]
    clusters = _cluster_candidates(candidates, limit=3)
    assert [item.representative.paper_family_id for item in clusters] == [
        "family-a",
        "family-b",
        "family-c",
    ]


def test_manifest_check_uses_one_batched_store_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path)
    original_connect = service.store._connect
    connections = 0

    def tracked_connect():
        nonlocal connections
        connections += 1
        return original_connect()

    def forbidden_single_lookup(**kwargs):
        del kwargs
        raise AssertionError("check_manifests must not issue per-manifest lookups")

    monkeypatch.setattr(service.store, "_connect", tracked_connect)
    monkeypatch.setattr(service.store, "get_manifest", forbidden_single_lookup)
    request = ManifestCheckRequest(
        schema_version="evidence-retrieval/2.0",
        project_id="project-1",
        manifests=[
            RetrievalManifest.model_validate(
                _manifest(f"snapshot-{index}"), strict=True
            )
            for index in range(1_000)
        ],
    )
    response = service.check_manifests(request)
    assert connections == 1
    assert len(response.manifests) == 1_000
    assert {item.status for item in response.manifests} == {"missing"}


def test_delete_failure_leaves_projection_retryable(tmp_path: Path) -> None:
    vector = FakeVectorIndex()
    service = _service(tmp_path, vector=vector)
    manifest = _manifest()
    service.index_snapshot(
        _index_request(manifest, [_document("claim-1", "mercury result")])
    )
    from compiler_service.retrieval_contract import DeleteSnapshotsRequest

    request = DeleteSnapshotsRequest(
        schema_version="evidence-retrieval/2.0",
        index_version="evidence-retrieval/2.0",
        project_id="project-1",
        snapshot_ids=["snapshot-1"],
    )
    vector.fail_delete = True
    with pytest.raises(RuntimeError, match="delete unavailable"):
        service.delete_snapshots(request)
    check_request = ManifestCheckRequest(
        schema_version="evidence-retrieval/2.0",
        project_id="project-1",
        manifests=[RetrievalManifest.model_validate(manifest, strict=True)],
    )
    assert service.check_manifests(check_request).manifests[0].status == "indexed"
    vector.fail_delete = False
    deleted = service.delete_snapshots(request)
    assert deleted.deleted_snapshots == 1
    assert deleted.deleted_documents == 1
    assert service.check_manifests(check_request).manifests[0].status == "missing"


def test_scope_filter_is_applied_before_bounded_channel_limit(tmp_path: Path) -> None:
    service = _service(tmp_path)
    distractors = [
        _document(
            f"claim-wrong-{index}",
            "common common common common common common common common",
            location=["wrong-region"],
        )
        for index in range(501)
    ]
    target = _document(
        "claim-target",
        "common target",
        location=["target-region"],
    )
    service.index_snapshot(
        _index_request(_manifest(document_count=502), [*distractors, target])
    )
    response = service.search(
        _search_request(
            "common",
            scope={"location": ["target-region"]},
            limit=5,
        )
    )
    refs = [item for cluster in response.clusters for item in cluster.member_refs]
    assert [(item.snapshot_id, item.claim_id) for item in refs] == [
        ("snapshot-1", "claim-target")
    ]


def test_same_paper_family_selector_and_shared_lowercase_contract(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    manifest = _manifest(family_id="family-only", created_at="2026-08-18T10:00Z")
    document = _document(
        "claim-1",
        "Straße differs from STRASSE",
        family_id="family-only",
    )
    service.index_snapshot(_index_request(manifest, [document]))
    payload = _search_request("Straße").model_dump(mode="json")
    # Required terms are whole tokens under the shared NFKC + casefold
    # normalization: both spellings name the same token.
    payload["must_terms"] = ["STRASSE", "Straße"]
    payload["filters"] = {
        "corpus": "same_paper",
        "paper_ids": [],
        "paper_family_ids": ["family-only"],
        "active_snapshot_ids": None,
        "limit": 5,
    }
    request = HybridSearchRequest.model_validate(payload, strict=True)
    assert request.must_terms == ["STRASSE", "Straße"]
    response = service.search(request)
    assert response.clusters


@pytest.mark.parametrize(
    ("must", "exclude", "expected"),
    (
        (["Ca"], [], []),
        (["ratio"], [], ["claim-ratio"]),
        ([], ["rat"], ["claim-ratio"]),
        ([], ["paper"], ["claim-ratio"]),
        (["87Sr/86Sr"], [], ["claim-ratio"]),
    ),
)
def test_must_and_exclude_terms_match_whole_content_tokens(
    tmp_path: Path, must: list[str], exclude: list[str], expected: list[str]
) -> None:
    service = _service(tmp_path)
    manifest = _manifest()
    document = _document(
        "claim-ratio",
        "The 87Sr/86Sr ratio can rise after weathering",
    )
    service.index_snapshot(_index_request(manifest, [document]))
    payload = _search_request("87Sr/86Sr ratio weathering").model_dump(mode="json")
    payload["must_terms"] = must
    payload["exclude_terms"] = exclude
    response = service.search(HybridSearchRequest.model_validate(payload, strict=True))
    refs = [
        item.claim_id for cluster in response.clusters for item in cluster.member_refs
    ]
    # "Ca" is not "can", excluding "rat" keeps "ratio", and the "[paper]"
    # section header is never content.
    assert refs == expected


def test_concrete_qdrant_channel_filters_project_and_scope(tmp_path: Path) -> None:
    index = QdrantVectorIndex(
        data_dir=tmp_path / "qdrant-index",
        model_name="fake-multilingual",
        model_revision="revision-1",
        dimension=2,
    )
    target = ClaimSearchDocument.model_validate(
        _document("claim-target", "target", location=["target-region"]),
        strict=True,
    )
    wrong = ClaimSearchDocument.model_validate(
        _document("claim-wrong", "wrong", location=["wrong-region"]),
        strict=True,
    )
    try:
        index.upsert(
            project_id="project-1",
            snapshot_id="snapshot-1",
            claim_ids=[target.claim_id, wrong.claim_id],
            claim_types=[target.claim_type, wrong.claim_type],
            polarities=[target.polarity, wrong.polarity],
            scopes=[target.scope, wrong.scope],
            vectors=[[1.0, 0.0], [1.0, 0.0]],
        )
        hits = index.search(
            project_id="project-1",
            snapshot_ids=["snapshot-1"],
            vector=[1.0, 0.0],
            limit=10,
            claim_types=[],
            polarity=None,
            scope=SearchScopeFilter(location=["target-region"]),
        )
        assert [(item.snapshot_id, item.claim_id) for item in hits] == [
            ("snapshot-1", "claim-target")
        ]
        assert (
            index.search(
                project_id="project-other",
                snapshot_ids=["snapshot-1"],
                vector=[1.0, 0.0],
                limit=10,
                claim_types=[],
                polarity=None,
                scope=None,
            )
            == []
        )
    finally:
        index._client.close()


def test_concrete_qdrant_channel_breaks_canonical_score_ties_by_identity(
    tmp_path: Path,
) -> None:
    index = QdrantVectorIndex(
        data_dir=tmp_path / "qdrant-tie-index",
        model_name="fake-multilingual",
        model_revision="revision-1",
        dimension=2,
    )
    try:
        index.upsert(
            project_id="project-1",
            snapshot_id="snapshot-1",
            claim_ids=["claim-z", "claim-a"],
            claim_types=["descriptive", "descriptive"],
            polarities=["affirmed", "affirmed"],
            scopes=[SearchScopeFilter(), SearchScopeFilter()],
            vectors=[[1.0, 0.0], [1.0, 0.0]],
        )
        hits = index.search(
            project_id="project-1",
            snapshot_ids=["snapshot-1"],
            vector=[1.0, 0.0],
            limit=2,
            claim_types=[],
            polarity=None,
            scope=None,
        )
        assert [(item.claim_id, item.score) for item in hits] == [
            ("claim-a", 1.0),
            ("claim-z", 1.0),
        ]
    finally:
        index._client.close()


def test_concrete_qdrant_channel_stabilizes_ties_crossing_limit(
    tmp_path: Path,
) -> None:
    selected: list[list[str]] = []
    for run, claim_ids in enumerate(
        (
            ["claim-z", "claim-y", "claim-b", "claim-a"],
            ["claim-a", "claim-z", "claim-y", "claim-b"],
            ["claim-y", "claim-b", "claim-a", "claim-z"],
        )
    ):
        index = QdrantVectorIndex(
            data_dir=tmp_path / f"qdrant-boundary-tie-index-{run}",
            model_name="fake-multilingual",
            model_revision="revision-1",
            dimension=2,
        )
        try:
            index.upsert(
                project_id="project-1",
                snapshot_id="snapshot-1",
                claim_ids=claim_ids,
                claim_types=["descriptive"] * 4,
                polarities=["affirmed"] * 4,
                scopes=[SearchScopeFilter()] * 4,
                vectors=[[1.0, 0.0]] * 4,
            )
            hits = index.search(
                project_id="project-1",
                snapshot_ids=["snapshot-1"],
                vector=[1.0, 0.0],
                limit=2,
                claim_types=[],
                polarity=None,
                scope=None,
            )
            selected.append([item.claim_id for item in hits])
        finally:
            index._client.close()

    assert selected == [["claim-a", "claim-b"]] * 3


def test_concrete_qdrant_cosine_search_does_not_mutate_local_vectors(
    tmp_path: Path,
) -> None:
    dimension = 384
    index = QdrantVectorIndex(
        data_dir=tmp_path / "qdrant-read-only-index",
        model_name="fake-multilingual",
        model_revision="revision-1",
        dimension=dimension,
    )
    vectors = [
        [
            ((offset * factor) % modulus - midpoint) / divisor
            for offset in range(dimension)
        ]
        for factor, modulus, midpoint, divisor in (
            (17, 101, 50, 37),
            (29, 97, 48, 41),
            (31, 89, 44, 43),
        )
    ]
    query = [((offset * 13) % 103 - 51) / 39 for offset in range(dimension)]
    try:
        index.upsert(
            project_id="project-1",
            snapshot_id="snapshot-1",
            claim_ids=["claim-c", "claim-b", "claim-a"],
            claim_types=["descriptive"] * 3,
            polarities=["affirmed"] * 3,
            scopes=[SearchScopeFilter()] * 3,
            vectors=vectors,
        )
        local_collection = index._client._client.collections[index.collection_name]
        before = local_collection.vectors[""].tobytes()
        first = index.search(
            project_id="project-1",
            snapshot_ids=["snapshot-1"],
            vector=query,
            limit=3,
            claim_types=[],
            polarity=None,
            scope=None,
        )
        assert local_collection.vectors[""].tobytes() == before
        for _ in range(7):
            assert (
                index.search(
                    project_id="project-1",
                    snapshot_ids=["snapshot-1"],
                    vector=query,
                    limit=3,
                    claim_types=[],
                    polarity=None,
                    scope=None,
                )
                == first
            )
            assert local_collection.vectors[""].tobytes() == before
    finally:
        index._client.close()


def test_time_filters_overlap_ordered_and_legacy_reversed_intervals(
    tmp_path: Path,
) -> None:
    import json
    import sqlite3

    service = _service(tmp_path)
    manifest = _manifest()
    document = _document(
        "claim-deccan",
        "Deccan volcanism released mercury",
        time={"label": None, "start": 66.0, "end": 56.0, "unit": "Ma"},
    )
    service.index_snapshot(_index_request(manifest, [document]))
    stored = service.store._path
    with sqlite3.connect(stored) as connection:
        (payload,) = connection.execute(
            "SELECT scope_filter_json FROM retrieval_documents"
        ).fetchone()
        scope = json.loads(payload)
        # New rows are ordered at index time.
        assert scope["time"]["start"] < scope["time"]["end"]
        # Rows indexed before ordering stay matchable.
        scope["time"]["start"], scope["time"]["end"] = (
            scope["time"]["end"],
            scope["time"]["start"],
        )
        connection.execute(
            "UPDATE retrieval_documents SET scope_filter_json = ?",
            (json.dumps(scope),),
        )
    windows = (("Ma", 60, 61), ("百万年", 60, 61), ("亿年", 0.6, 0.61))
    for unit, start, end in windows:
        time = {"label": None, "start": start, "end": end, "unit": unit}
        response = service.search(
            _search_request("Deccan volcanism mercury", scope={"time": time})
        )
        refs = [
            item.claim_id
            for cluster in response.clusters
            for item in cluster.member_refs
        ]
        assert refs == ["claim-deccan"], unit

    unknown = service.search(
        _search_request(
            "Deccan volcanism mercury",
            scope={"time": {"label": None, "start": 1, "end": 2, "unit": "epochs"}},
        )
    )
    assert any(
        "time filter unit is not recognized" in item for item in unknown.warnings
    )


def test_anchor_channels_share_one_base_order_and_a_capped_total() -> None:
    from compiler_service import retrieval_service as ranking
    from compiler_service.retrieval_ontology import default_retrieval_ontology

    k = 60
    many_concepts = ranking._Candidate(
        document=ClaimSearchDocument.model_validate(
            _document(
                "claim-concepts",
                "REE TOC ICP-MS XRD XRF PAAS concentrations in seawater",
            )
        ),
        lexical=None,
        dense=None,
        rrf=0.0,
    )
    leader = ranking._Candidate(
        document=ClaimSearchDocument.model_validate(
            _document("claim-leader", "unrelated wording only")
        ),
        lexical=1.0,
        dense=1.0,
        rrf=2.0 / (k + 1),
    )
    candidates = [leader, many_concepts]
    budget = ranking._AnchorBoostBudget(
        base_order=sorted(candidates, key=ranking._candidate_sort_key),
        cap=ranking._ANCHOR_BOOST_CAP_CHANNELS / (k + 1),
    )
    hits = ranking._fuse_concept_anchors(
        candidates,
        "REE TOC ICP-MS XRD XRF PAAS seawater",
        default_retrieval_ontology(),
        k,
        budget=budget,
    )
    assert hits >= 5
    assert many_concepts.rrf == 0.0  # deferred until every channel has voted
    budget.apply()
    # Seven concept channels would add 7/(k+1); the cap keeps the total at the
    # weight of leading both base channels, so the base leader is not passed.
    assert many_concepts.rrf == pytest.approx(2.0 / (k + 1))
    assert many_concepts.rrf <= leader.rrf


def test_family_diversity_demotes_repeats_without_dropping_them() -> None:
    from compiler_service import retrieval_service as ranking

    def group(claim: str, family: str) -> Any:
        candidate = ranking._Candidate(
            document=ClaimSearchDocument.model_validate(
                _document(claim, f"claim {claim}", family_id=family)
            ),
            lexical=None,
            dense=None,
            rrf=0.0,
        )
        return ranking._ClusterGroup(
            representative=candidate, preview=[candidate], family_ids={family}
        )

    ranked = [
        group("a1", "A"),
        group("a2", "A"),
        group("b1", "B"),
        group("c1", "C"),
        group("d1", "D"),
        group("e1", "E"),
    ]
    order = [
        item.representative.document.claim_id
        for item in ranking._diverse_cluster_groups(ranked, limit=4)
    ]
    # a2 is demoted behind other families but still beats e1's weaker result.
    assert order == ["a1", "b1", "c1", "d1"]
    order = [
        item.representative.document.claim_id
        for item in ranking._diverse_cluster_groups(ranked, limit=6)
    ]
    assert order == ["a1", "b1", "c1", "d1", "a2", "e1"]


def test_service_indexes_and_searches_with_the_numpy_vector_backend(
    tmp_path: Path,
) -> None:
    from compiler_service.retrieval_vectors import NumpyVectorIndex

    dense = FakeDense()
    service = RetrievalService(
        data_dir=tmp_path,
        dense_encoder=dense,
        relation_classifier=FakeRelationClassifier(),
        vector_backend="numpy",
    )
    assert isinstance(service.vector_index, NumpyVectorIndex)
    manifest = _manifest(document_count=2)
    service.index_snapshot(
        _index_request(
            manifest,
            [
                _document("claim-hg", "Mercury anomalies track volcanism"),
                _document("claim-other", "Rainfall increased in spring"),
            ],
        )
    )
    response = service.search(_search_request("Mercury anomalies volcanism"))
    assert response.channels.dense.status == "ok"
    assert response.channels.dense.returned >= 1
    refs = [
        item.claim_id for cluster in response.clusters for item in cluster.member_refs
    ]
    assert "claim-hg" in refs


class _FailingPlanner:
    name = "failing-planner"
    last_warnings: tuple[str, ...] = ()

    def plan(self, request: HybridSearchRequest):
        raise RuntimeError("model race failed")


def test_interactive_planning_failure_never_seals_a_local_plan(
    tmp_path: Path,
) -> None:
    from compiler_service.retrieval_service import RetrievalPlanningError

    service = _service(
        tmp_path,
        planner=_FailingPlanner(),
        query_plan_handoff_secret="s" * 40,
    )
    manifest = _manifest()
    service.index_snapshot(
        _index_request(manifest, [_document("claim-1", "Mercury tracks volcanism")])
    )
    request = _search_request(
        "Mercury volcanism",
        schema_version="evidence-retrieval/2.1",
        stage="preview",
    )
    # Batch keeps its documented deterministic plan with a warning.
    batch = service.search(request)
    assert any("deterministic plan retained" in item for item in batch.warnings)
    # Interactive answers come only from a validated model plan (RTS-1).
    with pytest.raises(RetrievalPlanningError):
        service.search(request, interactive=True)


def test_interactive_final_requires_a_preview_handoff(tmp_path: Path) -> None:
    service = _service(tmp_path)
    manifest = _manifest()
    service.index_snapshot(
        _index_request(manifest, [_document("claim-1", "Mercury tracks volcanism")])
    )
    final = _search_request(
        "Mercury volcanism",
        stage="ai_rerank",
        candidate_refs=[{"snapshot_id": "snapshot-1", "claim_id": "claim-1"}],
    )
    assert service.search(final).stage == "ai_rerank"
    with pytest.raises(InputError, match="preview handoff"):
        service.search(final, interactive=True)
