"""Scientific-channel invariance with fixed plan and fake retrieval providers.

This deliberately makes no invariance claim for real planners or embeddings.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from compiler_service import retrieval_service as ranking
from compiler_service.retrieval_contract import (
    ClaimSearchDocument,
    HybridSearchRequest,
    IndexSnapshotRequest,
    QueryPlan,
)
from compiler_service.retrieval_ontology import RetrievalOntology
from compiler_service.retrieval_store import ChannelHit

PLAIN = "Which samples contain 10 mg/kg?\nHow does the concentration vary?"
NUMBERED = "(1) Which samples contain 10 mg/kg?\n(2) How does the concentration vary?"
TEXTS = {
    "claim-a": "The measured dimensionless value is 1.",
    "claim-b": "The measured dimensionless value is 2.",
    "claim-c": "The concentration is 10 mg/kg.",
}


class FakeDense:
    """Same injected-provider seam as test_retrieval_service.FakeDense."""

    name = "list-marker-fake"
    revision = "fixture-revision"
    dimension = 2
    ready = True

    def embed_documents(self, texts):
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, text):
        return [1.0, 0.0]


class FakeVectorIndex:
    model_name = FakeDense.name
    model_revision = FakeDense.revision
    dimension = FakeDense.dimension
    collection_name = "fixture"

    def upsert(self, **kwargs):
        return ["point-" + claim for claim in kwargs["claim_ids"]]

    def search(self, **kwargs):
        return [ChannelHit("snapshot-1", claim, 0.8) for claim in TEXTS]


def document(claim, text):
    digest = hashlib.sha256(text.encode()).hexdigest()
    return ClaimSearchDocument.model_validate(
        {
            "index_version": "evidence-retrieval/2.0",
            "snapshot_id": "snapshot-1",
            "paper_id": "paper-1",
            "paper_family_id": "family-1",
            "root_artifact_version_id": "artifact-1",
            "claim_id": claim,
            "source_claim_id": None,
            "original_claim": text,
            "canonical_claim": text,
            "proposition_key": "proposition:sha256:" + digest,
            "scoped_claim_id": "claim:sha256:" + digest,
            "paper_title": "Fixture",
            "evidence_context": text,
            "entities": [],
            "key_terms": [],
            "abbreviations": [],
            "qualifiers": [],
            "claim_type": "descriptive",
            "polarity": "affirmed",
            "modality": "asserted",
            "attribution": "authors_conclusion",
            "rhetorical_role": "terminal_conclusion",
            "scope": {
                "location": [],
                "time": None,
                "formation": [],
                "sample_type": [],
                "method": [],
                "condition": [],
                "measurement": [],
                "uncertainty": None,
                "entities": [],
            },
            "evidence_anchors": [],
            "shortest_evidence_path": {
                "root_claim_id": claim,
                "nodes": [],
                "links": [],
                "depth": 0,
                "mean_edge_confidence": None,
            },
            "path_quality": 1.0,
            "extractor_prompt_version": "fixture-v1",
            "explicit_relations": [],
        },
        strict=True,
    )


def service(tmp_path, monkeypatch, *, materialized):
    instance = ranking.RetrievalService(
        data_dir=tmp_path,
        dense_encoder=FakeDense(),
        vector_index=FakeVectorIndex(),
        remote_query_planner_enabled=False,
        selective_safe_reserve_enabled=False,
        materialized_ranking_features_enabled=materialized,
        ontology=RetrievalOntology(revision="empty-fixture", concepts=()),
    )
    docs = [document(claim, text) for claim, text in TEXTS.items()]
    instance.index_snapshot(
        IndexSnapshotRequest.model_validate(
            {
                "schema_version": "evidence-retrieval/2.0",
                "project_id": "project-1",
                "manifest": {
                    "index_version": "evidence-retrieval/2.0",
                    "snapshot_id": "snapshot-1",
                    "paper_id": "paper-1",
                    "paper_family_id": "family-1",
                    "root_artifact_version_id": "artifact-1",
                    "root_artifact_sha256": "a" * 64,
                    "graph_sha256": "b" * 64,
                    "graph_schema_version": "scientific-evidence-graph/1.0",
                    "extractor_prompt_version": "fixture-v1",
                    "document_count": len(docs),
                    "created_at": "2026-09-05T00:00:00Z",
                },
                "documents": [doc.model_dump(mode="json") for doc in docs],
            },
            strict=True,
        )
    )
    fixed_plan = QueryPlan(
        query_type="claim_search",
        canonical_claim=PLAIN,
        original_query=PLAIN,
        planner="deterministic",
        must_terms=[],
        optional_terms=[],
        exclude_terms=[],
        entities=[],
        claim_types=[],
        polarity=None,
        scope_constraints=None,
    )
    monkeypatch.setattr(instance, "_plan", lambda *args, **kwargs: fixed_plan)
    monkeypatch.setattr(
        instance.store,
        "lexical_search",
        lambda **kwargs: [ChannelHit("snapshot-1", claim, 1.0) for claim in TEXTS],
    )
    return instance


@pytest.fixture
def scientific_trace(monkeypatch):
    """Observe the capped anchor-boost phase around the scientific channel."""

    captured = []
    counts = []
    original = ranking._fuse_scientific_mentions
    original_apply = ranking._AnchorBoostBudget.apply

    def rows(candidates):
        return tuple(
            (candidate.document.claim_id, candidate.rrf.hex())
            for candidate in sorted(candidates, key=ranking._candidate_sort_key)
        )

    def observe(candidates, *args, **kwargs):
        count = original(candidates, *args, **kwargs)
        counts.append(count)
        return count

    def apply(budget):
        before = rows(budget.base_order)
        original_apply(budget)
        captured.append((before, rows(budget.base_order), counts[-1]))

    monkeypatch.setattr(ranking, "_fuse_scientific_mentions", observe)
    monkeypatch.setattr(ranking._AnchorBoostBudget, "apply", apply)
    return captured


def preview(instance, query):
    response = instance.search(
        HybridSearchRequest.model_validate(
            {
                "schema_version": "evidence-retrieval/2.0",
                "project_id": "project-1",
                "stage": "preview",
                "query": query,
                "query_claim": None,
                "candidate_refs": [],
                "intent": "claim_search",
                "must_terms": [],
                "exclude_terms": [],
                "scope": None,
                "claim_types": [],
                "polarity": None,
                "filters": {
                    "corpus": "cross_paper",
                    "limit": 12,
                    "paper_ids": [],
                    "paper_family_ids": [],
                    "active_snapshot_ids": ["snapshot-1"],
                },
            }
        )
    )
    assert response.channels.lexical.status == response.channels.dense.status == "ok"
    return (
        response.query_plan.model_dump(mode="json"),
        tuple((row.claim_id, row.scores.rrf.hex()) for row in response.preview),
    )


@pytest.mark.parametrize("materialized", [False, True])
def test_numbered_questions_preserve_scientific_scores_and_order(
    tmp_path,
    monkeypatch,
    scientific_trace,
    materialized,
):
    instance = service(tmp_path, monkeypatch, materialized=materialized)
    plain = preview(instance, PLAIN)
    numbered = preview(instance, NUMBERED)
    assert plain == numbered
    assert scientific_trace[0] == scientific_trace[1]
    before, after, matched_mentions = scientific_trace[0]
    assert matched_mentions == 1
    before_scores, after_scores = dict(before), dict(after)
    assert after_scores["claim-a"] == before_scores["claim-a"]
    assert after_scores["claim-b"] == before_scores["claim-b"]
    assert float.fromhex(after_scores["claim-c"]) > float.fromhex(
        before_scores["claim-c"]
    )
    assert after[0][0] == "claim-c"


def test_materialized_and_legacy_scientific_paths_are_exactly_equal(
    tmp_path,
    monkeypatch,
    scientific_trace,
):
    legacy = service(tmp_path / "legacy", monkeypatch, materialized=False)
    materialized = service(tmp_path / "materialized", monkeypatch, materialized=True)
    assert preview(legacy, NUMBERED) == preview(materialized, NUMBERED)
    assert scientific_trace[0] == scientific_trace[1]


def test_stale_scientific_binding_uses_legacy_fallback_not_poisoned_values(
    tmp_path,
    monkeypatch,
    scientific_trace,
):
    instance = service(tmp_path, monkeypatch, materialized=True)
    expected = preview(instance, NUMBERED)
    connection = sqlite3.connect(instance.store._path)
    try:
        rows = connection.execute(
            "SELECT rowid,projection_json FROM retrieval_ranking_features"
        ).fetchall()
        for rowid, raw in rows:
            payload = json.loads(raw)
            scientific = payload["features"]["scientific_mention_keys"]
            scientific["bindings"]["scientific_mentions_source_sha256"] = "0" * 64
            scientific["values"] = ["mention:dec:999e+0|cmp:bare|unit:bare"]
            connection.execute(
                "UPDATE retrieval_ranking_features SET projection_json=? WHERE rowid=?",
                (json.dumps(payload), rowid),
            )
        connection.commit()
    finally:
        connection.close()
    calls = []
    validate = ranking.validated_feature_values

    def observed_validation(loaded, feature_name, *, bindings):
        result = validate(loaded, feature_name, bindings=bindings)
        if feature_name == "scientific_mention_keys":
            calls.append((loaded is not None, result))
        return result

    monkeypatch.setattr(ranking, "validated_feature_values", observed_validation)
    assert preview(instance, NUMBERED) == expected
    assert calls == [(True, None)] * len(TEXTS)
    assert scientific_trace[0] == scientific_trace[1]


def test_final_signature_gate_preserves_body_quantity_and_comparison_conflicts():
    plain = "The concentration is >10 mg/kg.\nThe sample was collected offshore."
    numbered = (
        "(1) The concentration is >10 mg/kg.\n"
        "(2) The sample was collected offshore."
    )
    assert not ranking._claim_signature_conflict(plain, numbered)
    assert ranking._claim_signature_conflict(
        plain, numbered.replace(">10 mg/kg", ">20 mg/kg")
    )
    assert ranking._claim_signature_conflict(
        plain, numbered.replace(">10 mg/kg", "<10 mg/kg")
    )
