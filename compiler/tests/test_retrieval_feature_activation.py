"""Focused serving equivalence using real SQLite and existing fake providers."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import replace

import pytest
import test_retrieval_service as fixtures

from compiler_service import retrieval_ranking_features as features
from compiler_service import retrieval_service as ranking
from compiler_service.errors import QueryPlanHandoffError
from compiler_service.retrieval_contract import QueryClaimRef
from compiler_service.retrieval_service import RetrievalService

QUERY = "甲乙丙丁戊己 monazite rare earth elements 10 mg/kg"
FEATURES = (
    "exact_anchor_tokens",
    "sparse_cjk_tokens",
    "scientific_mention_keys",
    "ontology_concept_ids",
)
CONSUMERS = (
    "_discount_sparse_cjk_lexical_channel",
    "_fuse_exact_anchors",
    "_fuse_concept_anchors",
    "_fuse_scientific_mentions",
)


def service_at(path, *, enabled):
    dense = fixtures.FakeDense()
    return RetrievalService(
        data_dir=path,
        dense_encoder=dense,
        vector_index=fixtures.FakeVectorIndex(dense.revision),
        relation_classifier=fixtures.FakeRelationClassifier(),
        remote_query_planner_enabled=False,
        query_scoped_concept_pattern_scan_enabled=True,
        materialized_ranking_features_enabled=enabled,
        query_plan_handoff_secret=fixtures._HANDOFF_SECRET_A,
        query_plan_handoff_clock=lambda: 1000.0,
    )


def observe_projection_builds(monkeypatch):
    rebuilt = []
    original = ranking.build_ranking_feature_projection

    def tracked(document, **kwargs):
        rebuilt.append(document.claim_id)
        return original(document, **kwargs)

    monkeypatch.setattr(ranking, "build_ranking_feature_projection", tracked)
    return rebuilt


def test_startup_preflight_reuses_valid_features_without_rebuilding(
    tmp_path, monkeypatch
):
    _, service = pair(tmp_path)
    path = tmp_path / "on/retrieval/retrieval.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM retrieval_ranking_features")
    rebuilt = observe_projection_builds(monkeypatch)
    first = service.prepare_materialized_ranking_features()
    assert first["inserted"] == 14
    assert rebuilt == [f"claim-{index:02}" for index in range(14)]
    rebuilt.clear()
    second = service.prepare_materialized_ranking_features()
    assert second["unchanged"] == 14
    assert second["inserted"] == second["updated"] == 0
    assert rebuilt == []


@pytest.mark.parametrize("damage", ["missing", "json", "source", "feature", "binding"])
def test_startup_preflight_rebuilds_only_invalid_sidecar(tmp_path, monkeypatch, damage):
    _, service = pair(tmp_path)
    path = tmp_path / "on/retrieval/retrieval.sqlite3"
    with sqlite3.connect(path) as connection:
        if damage == "missing":
            connection.execute(
                "DELETE FROM retrieval_ranking_features WHERE claim_id='claim-00'"
            )
        elif damage == "source":
            # Valid source bytes changed after the prior sidecar was generated.
            connection.execute(
                "UPDATE retrieval_documents SET document_json=document_json || ' ' "
                "WHERE claim_id='claim-00'"
            )
        else:
            raw = connection.execute(
                "SELECT projection_json FROM retrieval_ranking_features "
                "WHERE claim_id='claim-00'"
            ).fetchone()[0]
            payload = json.loads(raw)
            if damage == "json":
                raw = "{bad json"
            elif damage == "feature":
                del payload["features"]["scientific_mention_keys"]
                raw = json.dumps(payload)
            else:
                payload["features"]["ontology_concept_ids"]["bindings"][
                    "ontology_content_sha256"
                ] = "stale"
                raw = json.dumps(payload)
            connection.execute(
                "UPDATE retrieval_ranking_features SET projection_json=? "
                "WHERE claim_id='claim-00'",
                (raw,),
            )
    rebuilt = observe_projection_builds(monkeypatch)
    receipt = service.prepare_materialized_ranking_features()
    assert rebuilt == ["claim-00"]
    assert receipt["unchanged"] == 13
    assert receipt["inserted"] + receipt["updated"] == 1
    rebuilt.clear()
    assert service.prepare_materialized_ranking_features()["unchanged"] == 14
    assert rebuilt == []


@pytest.mark.parametrize(
    "binding", ["ranking_feature_producer_source_sha256", "ontology_content_sha256"]
)
def test_startup_preflight_rebuilds_after_current_binding_changes(
    tmp_path, monkeypatch, binding
):
    _, service = pair(tmp_path)
    service.ranking_feature_bindings = replace(
        service.ranking_feature_bindings, **{binding: "new-version"}
    )
    rebuilt = observe_projection_builds(monkeypatch)
    assert service.prepare_materialized_ranking_features()["updated"] == 14
    assert len(rebuilt) == 14
    rebuilt.clear()
    assert service.prepare_materialized_ranking_features()["unchanged"] == 14
    assert rebuilt == []


@pytest.mark.parametrize("change", ["source", "coverage"])
def test_startup_reused_features_recheck_sources_atomically(
    tmp_path, monkeypatch, change
):
    _, service = pair(tmp_path)
    path = tmp_path / "on/retrieval/retrieval.sqlite3"
    with sqlite3.connect(path) as connection:
        before = connection.execute(
            "SELECT * FROM retrieval_ranking_features ORDER BY claim_id"
        ).fetchall()
    original = ranking.validated_feature_values
    attempted = []

    def check_with_concurrent_writer(*args, **kwargs):
        if not attempted:
            attempted.append(True)
            with (
                sqlite3.connect(path, timeout=0) as connection,
                pytest.raises(sqlite3.OperationalError, match="locked"),
            ):
                if change == "source":
                    connection.execute(
                        "UPDATE retrieval_documents "
                        "SET document_json=document_json || ' ' "
                        "WHERE claim_id='claim-00'"
                    )
                else:
                    connection.execute(
                        "UPDATE retrieval_manifests SET indexed_document_count=0"
                    )
        return original(*args, **kwargs)

    monkeypatch.setattr(
        ranking, "validated_feature_values", check_with_concurrent_writer
    )
    rebuilt = observe_projection_builds(monkeypatch)
    assert service.prepare_materialized_ranking_features()["unchanged"] == 14
    assert attempted == [True]
    assert rebuilt == []
    with sqlite3.connect(path) as connection:
        after = connection.execute(
            "SELECT * FROM retrieval_ranking_features ORDER BY claim_id"
        ).fetchall()
    assert after == before


def pair(tmp_path):
    services = [
        service_at(tmp_path / name, enabled=enabled)
        for name, enabled in (("off", False), ("on", True))
    ]
    documents = [
        fixtures._document(
            f"claim-{index:02}", f"甲乙 monazite rare earth elements 10 mg/kg {index}"
        )
        for index in range(14)
    ]
    for service in services:
        service.index_snapshot(
            fixtures._index_request(
                fixtures._manifest(document_count=len(documents)), documents
            )
        )
    return services


def request(*, schema="evidence-retrieval/2.1", **kwargs):
    return fixtures._search_request(
        QUERY, schema_version=schema, stage="preview", limit=12, **kwargs
    )


def semantic(response):
    # Handoff is signed over the switch itself; all other response fields agree.
    result = response.model_dump(mode="json")
    result.pop("query_plan_handoff", None)
    return result


def final_request(preview_request, preview):
    return preview_request.model_copy(
        update={
            "stage": "ai_rerank",
            "candidate_refs": [
                QueryClaimRef(snapshot_id=item.snapshot_id, claim_id=item.claim_id)
                for item in preview.preview
            ],
            "query_plan_handoff": preview.query_plan_handoff,
        }
    )


def observe_consumption(monkeypatch):
    calls = Counter()
    original = ranking.validated_feature_values

    def observed(loaded, name, *, bindings):
        value = original(loaded, name, bindings=bindings)
        calls[(name, "fallback" if value is None else "ready")] += 1
        return value

    monkeypatch.setattr(ranking, "validated_feature_values", observed)
    return calls


def test_21_switch_preserves_own_preview_final_and_rejects_old_token(
    tmp_path, monkeypatch
):
    off, on = pair(tmp_path)
    calls = observe_consumption(monkeypatch)
    initial = request()
    previews = [service.search(initial) for service in (off, on)]
    assert len(previews[0].preview) == 12
    assert semantic(previews[0]) == semantic(previews[1])
    assert all(calls[(name, "ready")] == 14 for name in FEATURES)
    assert not any(calls[(name, "fallback")] for name in FEATURES)

    def forbidden(*args, **kwargs):
        raise AssertionError("verified final revisited retrieval or sidecars")

    for name in CONSUMERS:
        monkeypatch.setattr(ranking, name, forbidden)
    monkeypatch.setattr(ranking, "validated_feature_values", forbidden)
    for service in (off, on):
        original = service.store.load_documents

        def without_sidecars(*, _original=original, **kwargs):
            assert kwargs.get("ranking_feature_sink") is None
            return _original(**kwargs)

        monkeypatch.setattr(service.store, "load_documents", without_sidecars)
        monkeypatch.setattr(service.store, "lexical_search", forbidden)
        monkeypatch.setattr(service.dense_encoder, "embed_query", forbidden)
        monkeypatch.setattr(service.vector_index, "search", forbidden)
    finals = [
        service.search(final_request(initial, preview))
        for service, preview in zip((off, on), previews, strict=True)
    ]
    assert semantic(finals[0]) == semantic(finals[1])
    for preview, final in zip(previews, finals, strict=True):
        assert {
            (item.snapshot_id, item.claim_id): item.scores for item in preview.preview
        } == {
            (item.snapshot_id, item.claim_id): item.scores
            for cluster in final.clusters
            for item in cluster.member_refs
        }
    on.relation_classifier.seen.clear()
    with pytest.raises(QueryPlanHandoffError):
        on.search(final_request(initial, previews[0]))
    assert on.relation_classifier.seen == []


def test_query_claim_20_keeps_sidecar_ranking_and_same_paper_closure(
    tmp_path, monkeypatch
):
    off, on = pair(tmp_path)
    cross = fixtures._document(
        "claim-cross",
        "甲乙 monazite rare earth elements 10 mg/kg",
        snapshot_id="snapshot-2",
        paper_id="paper-2",
        family_id="family-2",
    )
    for service in (off, on):
        service.index_snapshot(
            fixtures._index_request(
                fixtures._manifest(
                    "snapshot-2", paper_id="paper-2", family_id="family-2"
                ),
                [cross],
            )
        )
    calls = observe_consumption(monkeypatch)
    initial = request(
        schema="evidence-retrieval/2.0",
        query_claim={
            "snapshot_id": "snapshot-1",
            "claim_id": "claim-00",
        },
    )
    previews = [service.search(initial) for service in (off, on)]
    assert semantic(previews[0]) == semantic(previews[1])
    assert [(item.snapshot_id, item.claim_id) for item in previews[1].preview] == [
        ("snapshot-2", "claim-cross")
    ]
    before = calls.copy()
    finals = [
        service.search(final_request(initial, preview))
        for service, preview in zip((off, on), previews, strict=True)
    ]
    assert semantic(finals[0]) == semantic(finals[1])
    assert all(calls[(name, "ready")] > before[(name, "ready")] for name in FEATURES)
    assert ("snapshot-1", "claim-01") in on.relation_classifier.seen
    assert ("snapshot-2", "claim-cross") in on.relation_classifier.seen
    assert ("snapshot-1", "claim-00") not in on.relation_classifier.seen


@pytest.mark.parametrize(
    "case", ["missing", "source_stale", "binding_stale", "oversize"]
)
def test_mixed_invalid_sidecar_falls_back_through_real_consumers(
    tmp_path, monkeypatch, case
):
    off, on = pair(tmp_path)
    calls = observe_consumption(monkeypatch)
    initial = request()
    baseline = off.search(initial)
    assert semantic(on.search(initial)) == semantic(baseline)
    assert all(calls[(name, "ready")] == 14 for name in FEATURES)
    with sqlite3.connect(on.store._path) as connection:
        raw, source = connection.execute(
            "SELECT f.projection_json,d.document_json "
            "FROM retrieval_ranking_features f "
            "JOIN retrieval_documents d USING(project_id,snapshot_id,index_version,"
            "dense_model,dense_model_revision,dense_dimension,claim_id) "
            "WHERE f.claim_id='claim-00'"
        ).fetchone()
        payload = json.loads(raw)
        if case == "missing":
            connection.execute(
                "DELETE FROM retrieval_ranking_features WHERE claim_id='claim-00'"
            )
        else:
            if case == "source_stale":
                payload["source_document_json_sha256"] = "0" * 64
            elif case == "binding_stale":
                for entry in payload["features"].values():
                    entry["bindings"]["ranking_feature_producer_source_sha256"] = (
                        "0" * 64
                    )
            else:
                document = fixtures.ClaimSearchDocument.model_validate_json(source)
                # Exercise the real producer's budget branch with a small limit,
                # without inventing a huge source document or changing source bytes.
                with monkeypatch.context() as budget:
                    budget.setattr(features, "MAX_SERIALIZED_FEATURE_BYTES", 1)
                    payload = json.loads(
                        features.build_ranking_feature_projection(
                            document,
                            source_document_json=source,
                            ontology=on.ontology,
                            bindings=on.ranking_feature_bindings,
                        )
                    )
                assert all(
                    entry["status"] == "legacy_fallback"
                    and entry["reason"] == "serialized_feature_budget_exceeded"
                    for entry in payload["features"].values()
                )
            connection.execute(
                "UPDATE retrieval_ranking_features SET projection_json=? "
                "WHERE claim_id='claim-00'",
                (json.dumps(payload),),
            )
    calls.clear()
    assert semantic(on.search(initial)) == semantic(baseline)
    for name in FEATURES:
        assert calls[(name, "fallback")] == 1
        assert calls[(name, "ready")] == 13
