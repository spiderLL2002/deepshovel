from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any

import pytest

from compiler_service.retrieval_contract import ClaimSearchDocument
from compiler_service.retrieval_ontology import default_retrieval_ontology
from compiler_service.retrieval_service import (
    _Candidate,
    _document_query_concept_ids,
    _fuse_concept_anchors,
)


def _document(claim_id: str, canonical: str) -> ClaimSearchDocument:
    proposition = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    value: dict[str, Any] = {
        "index_version": "evidence-retrieval/2.0",
        "snapshot_id": f"snapshot-{claim_id}",
        "paper_id": f"paper-{claim_id}",
        "paper_family_id": f"family-{claim_id}",
        "root_artifact_version_id": f"artifact-{claim_id}",
        "claim_id": claim_id,
        "source_claim_id": None,
        "original_claim": canonical,
        "canonical_claim": canonical,
        "proposition_key": f"proposition:sha256:{proposition}",
        "scoped_claim_id": f"claim:sha256:{proposition}",
        "paper_title": f"Title {claim_id}",
        "evidence_context": canonical,
        "entities": [],
        "key_terms": canonical.split(),
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
            "root_claim_id": claim_id,
            "nodes": [],
            "links": [],
            "depth": 0,
            "mean_edge_confidence": None,
        },
        "path_quality": 1.0,
        "extractor_prompt_version": "extractor-v1",
        "explicit_relations": [],
    }
    return ClaimSearchDocument.model_validate(value, strict=True)


def test_scoped_membership_equals_full_unsuppressed_membership() -> None:
    ontology = default_retrieval_ontology()
    all_ids = {concept.concept_id for concept in ontology.concepts}
    labels = [label for concept in ontology.concepts for label in concept.labels]
    values = labels + [
        "western Ordos Basin and rare earth elements",
        "鄂尔多斯盆地、稀土元素与ICP-MS",
        "unreviewed text with no ontology label",
    ]

    for index, value in enumerate(values):
        full = {
            concept.concept_id
            for concept in ontology.concepts_in(value, suppress_nested=False)
        }
        requested_sets = (
            set(),
            all_ids,
            full,
            full | {"unknown:fail-closed"},
            {sorted(all_ids)[index % len(all_ids)]},
            {"unknown:fail-closed"},
        )
        for requested in requested_sets:
            assert ontology.query_scoped_concept_ids(value, requested) == (
                full & requested
            )


def test_scoped_matcher_exception_falls_back_to_exact_legacy_fusion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ontology = default_retrieval_ontology()
    control = [
        _Candidate(
            _document("ordos", "The Ordos Basin preserves shale."), 1.0, 1.0, 0.5
        ),
        _Candidate(
            _document("pacific", "Pacific abyssal sediment is described."),
            0.9,
            0.9,
            0.4,
        ),
    ]
    candidate = deepcopy(control)
    control_boosted: set[tuple[str, str]] = set()
    candidate_boosted: set[tuple[str, str]] = set()

    control_hits = _fuse_concept_anchors(
        control,
        "鄂尔多斯盆地",
        ontology,
        60,
        boosted_keys=control_boosted,
        query_scoped_pattern_scan=False,
    )

    def fail_scoped(*_args: object, **_kwargs: object) -> set[str]:
        raise RuntimeError("injected scoped matcher failure")

    monkeypatch.setattr(ontology, "query_scoped_concept_ids", fail_scoped)
    candidate_hits = _fuse_concept_anchors(
        candidate,
        "鄂尔多斯盆地",
        ontology,
        60,
        boosted_keys=candidate_boosted,
        query_scoped_pattern_scan=True,
    )

    assert candidate_hits == control_hits
    assert candidate_boosted == control_boosted
    assert [item.rrf for item in candidate] == [item.rrf for item in control]
    assert [item.document.claim_id for item in candidate] == [
        item.document.claim_id for item in control
    ]


def test_empty_and_unknown_sets_fail_closed_without_inventing_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ontology = default_retrieval_ontology()
    document = _document("ordos", "The Ordos Basin preserves shale.")

    assert ontology.query_scoped_concept_ids("Ordos Basin", set()) == set()
    assert (
        ontology.query_scoped_concept_ids("Ordos Basin", {"unknown:fail-closed"})
        == set()
    )

    monkeypatch.setattr(
        ontology,
        "query_scoped_concept_ids",
        lambda *_args, **_kwargs: pytest.fail("empty query must not scan documents"),
    )
    candidate = _Candidate(document, 1.0, 1.0, 0.5)
    assert (
        _fuse_concept_anchors(
            [candidate],
            "unreviewed text",
            ontology,
            60,
            query_scoped_pattern_scan=True,
        )
        == 0
    )
    assert candidate.rrf == 0.5


def test_document_helper_scoped_and_legacy_paths_are_exact() -> None:
    ontology = default_retrieval_ontology()
    document = _document(
        "combined", "Western Ordos Basin rare earth elements were measured by ICP-MS."
    )
    query_ids = {
        concept.concept_id
        for concept in ontology.concepts_in(
            "Ordos Basin rare earth elements ICP-MS", suppress_nested=False
        )
    }

    control = _document_query_concept_ids(
        document,
        ontology,
        query_ids,
        query_scoped_pattern_scan=False,
    )
    candidate = _document_query_concept_ids(
        document,
        ontology,
        query_ids,
        query_scoped_pattern_scan=True,
    )

    assert candidate == (control & query_ids)


