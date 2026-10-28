"""Minimal fixture-envelope helpers adapted from compiler/tests/test_retrieval_service.py.
Source: private DeepShovel repository, owner-authorized local paper artifact reuse.
No LICENSE file was found by the repository license-file scan; this attribution does
not assert an open-source license or authorize external redistribution of source.
Algorithms remain imported from production; these helpers construct test envelopes.
"""
from __future__ import annotations
import hashlib
from typing import Any
from collections.abc import Sequence
from pathlib import Path
from compiler_service.retrieval_contract import IndexSnapshotRequest, relation_candidate_key
from compiler_service.retrieval_service import RetrievalService
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
def _service(tmp_path: Path, *, vector):
    return RetrievalService(data_dir=tmp_path, dense_encoder=FakeDense(),
        vector_index=vector, remote_query_planner_enabled=False,
        query_scoped_concept_pattern_scan_enabled=False)
