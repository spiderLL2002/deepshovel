"""Versioned, hash-bound materialized inputs for deterministic ranking rules.

The projection is a rebuildable performance aid.  It never changes document
identity: source ``document_json`` remains immutable and every invalid or
missing feature resolves to the legacy request-time computation.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from .graph_contract import canonical_json
from .retrieval_contract import ClaimSearchDocument
from .retrieval_ontology import RetrievalOntology, concept_document_text
from .retrieval_text import (
    dense_document_text,
    lexical_fields,
    lexical_tokens,
    normalized_text,
)
from .scientific_mentions import scientific_mention_keys

RANKING_FEATURE_SCHEMA_VERSION = "deepshovel-ranking-document-features/1.0"
MAX_SERIALIZED_FEATURE_BYTES = 512 * 1024


# Explicit semantic versions of the modules whose output a feature sidecar
# freezes. Bump an entry only when that module's feature output changes; an
# unrelated edit (a docstring, a new contract model) no longer invalidates
# every sidecar and forces a full rebuild (RTX-2).
FEATURE_SOURCE_VERSIONS: dict[str, str] = {
    "retrieval_ranking_features.py": "2026-10-19.1",
    "retrieval_contract.py": "2026-10-19.1",
    "retrieval_text.py": "2026-10-19.1",
    "retrieval_ontology.py": "2026-10-19.1",
    "scientific_mentions.py": "2026-10-19.1",
    "number_normalization.py": "2026-10-19.1",
}


def _source_sha256(name: str) -> str:
    """Stable identity of one feature-producing module's declared version."""

    version = FEATURE_SOURCE_VERSIONS[name]
    return hashlib.sha256(
        f"deepshovel-ranking-feature-source\0{name}\0{version}".encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class RankingFeatureBindings:
    ranking_feature_producer_source_sha256: str
    retrieval_contract_source_sha256: str
    retrieval_text_source_sha256: str
    retrieval_ontology_source_sha256: str
    scientific_mentions_source_sha256: str
    number_normalization_source_sha256: str
    ontology_content_sha256: str


@dataclass(frozen=True, slots=True)
class LoadedRankingFeatureProjection:
    """Lazy sidecar bytes returned by the same document-load SQL statement."""

    projection_json: str
    projection_payload: object
    observed_source_document_json_sha256: str


def ontology_object_sha256(ontology: RetrievalOntology) -> str:
    """Stable fallback identity for injected ontologies without a source path."""

    payload = {
        "revision": ontology.revision,
        "concepts": [asdict(item) for item in ontology.concepts],
        "relations": list(ontology.relations),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def current_ranking_feature_bindings(
    ontology: RetrievalOntology,
    *,
    ontology_content_sha256: str | None = None,
) -> RankingFeatureBindings:
    return RankingFeatureBindings(
        ranking_feature_producer_source_sha256=_source_sha256(
            "retrieval_ranking_features.py"
        ),
        retrieval_contract_source_sha256=_source_sha256("retrieval_contract.py"),
        retrieval_text_source_sha256=_source_sha256("retrieval_text.py"),
        retrieval_ontology_source_sha256=_source_sha256("retrieval_ontology.py"),
        scientific_mentions_source_sha256=_source_sha256("scientific_mentions.py"),
        number_normalization_source_sha256=_source_sha256("number_normalization.py"),
        ontology_content_sha256=(
            ontology_content_sha256 or ontology_object_sha256(ontology)
        ),
    )


def _feature_entry(
    values: Sequence[str], *, bindings: Mapping[str, str]
) -> dict[str, Any]:
    canonical_values = sorted(set(values))
    encoded = canonical_json(canonical_values).encode("utf-8")
    if len(encoded) > MAX_SERIALIZED_FEATURE_BYTES:
        return {
            "status": "legacy_fallback",
            "reason": "serialized_feature_budget_exceeded",
            "bindings": dict(sorted(bindings.items())),
        }
    return {
        "status": "ready",
        "bindings": dict(sorted(bindings.items())),
        "values": canonical_values,
    }


def build_ranking_feature_projection(
    document: ClaimSearchDocument,
    *,
    source_document_json: str,
    ontology: RetrievalOntology,
    bindings: RankingFeatureBindings,
) -> str:
    """Build all frozen features from the exact validated stored document."""

    dense_text = dense_document_text(document)
    all_concept_ids = {item.concept_id for item in ontology.concepts}
    producer_contract_binding = {
        "ranking_feature_producer_source_sha256": (
            bindings.ranking_feature_producer_source_sha256
        ),
        "retrieval_contract_source_sha256": bindings.retrieval_contract_source_sha256,
    }
    retrieval_text_binding = {
        **producer_contract_binding,
        "retrieval_text_source_sha256": bindings.retrieval_text_source_sha256,
    }
    payload = {
        "schema_version": RANKING_FEATURE_SCHEMA_VERSION,
        "source_document_json_sha256": hashlib.sha256(
            source_document_json.encode("utf-8")
        ).hexdigest(),
        "features": {
            "exact_anchor_tokens": _feature_entry(
                lexical_tokens(normalized_text(dense_text), maximum=4_096),
                bindings=retrieval_text_binding,
            ),
            "sparse_cjk_tokens": _feature_entry(
                lexical_tokens("\n".join(lexical_fields(document)), maximum=4_096),
                bindings=retrieval_text_binding,
            ),
            "scientific_mention_keys": _feature_entry(
                sorted(scientific_mention_keys(dense_text)),
                bindings={
                    **retrieval_text_binding,
                    "scientific_mentions_source_sha256": (
                        bindings.scientific_mentions_source_sha256
                    ),
                    "number_normalization_source_sha256": (
                        bindings.number_normalization_source_sha256
                    ),
                },
            ),
            "ontology_concept_ids": _feature_entry(
                sorted(
                    ontology.query_scoped_concept_ids(
                        concept_document_text(document), all_concept_ids
                    )
                ),
                bindings={
                    **producer_contract_binding,
                    "retrieval_ontology_source_sha256": (
                        bindings.retrieval_ontology_source_sha256
                    ),
                    "ontology_content_sha256": bindings.ontology_content_sha256,
                },
            ),
        },
    }
    return canonical_json(payload)


def validated_feature_values(
    loaded: LoadedRankingFeatureProjection | None,
    feature_name: str,
    *,
    bindings: RankingFeatureBindings,
) -> tuple[str, ...] | None:
    """Return a valid canonical feature or signal exact legacy fallback."""

    if loaded is None:
        return None
    payload = loaded.projection_payload
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != RANKING_FEATURE_SCHEMA_VERSION:
        return None
    source_sha256 = payload.get("source_document_json_sha256")
    if (
        not isinstance(source_sha256, str)
        or source_sha256 != loaded.observed_source_document_json_sha256
    ):
        return None
    features = payload.get("features")
    if not isinstance(features, dict):
        return None
    feature = features.get(feature_name)
    if not isinstance(feature, dict) or feature.get("status") != "ready":
        return None
    expected_by_feature: dict[str, dict[str, str]] = {
        "exact_anchor_tokens": {
            "ranking_feature_producer_source_sha256": (
                bindings.ranking_feature_producer_source_sha256
            ),
            "retrieval_contract_source_sha256": (
                bindings.retrieval_contract_source_sha256
            ),
            "retrieval_text_source_sha256": bindings.retrieval_text_source_sha256,
        },
        "sparse_cjk_tokens": {
            "ranking_feature_producer_source_sha256": (
                bindings.ranking_feature_producer_source_sha256
            ),
            "retrieval_contract_source_sha256": (
                bindings.retrieval_contract_source_sha256
            ),
            "retrieval_text_source_sha256": bindings.retrieval_text_source_sha256,
        },
        "scientific_mention_keys": {
            "ranking_feature_producer_source_sha256": (
                bindings.ranking_feature_producer_source_sha256
            ),
            "retrieval_contract_source_sha256": (
                bindings.retrieval_contract_source_sha256
            ),
            "retrieval_text_source_sha256": bindings.retrieval_text_source_sha256,
            "scientific_mentions_source_sha256": (
                bindings.scientific_mentions_source_sha256
            ),
            "number_normalization_source_sha256": (
                bindings.number_normalization_source_sha256
            ),
        },
        "ontology_concept_ids": {
            "ranking_feature_producer_source_sha256": (
                bindings.ranking_feature_producer_source_sha256
            ),
            "retrieval_contract_source_sha256": (
                bindings.retrieval_contract_source_sha256
            ),
            "retrieval_ontology_source_sha256": (
                bindings.retrieval_ontology_source_sha256
            ),
            "ontology_content_sha256": bindings.ontology_content_sha256,
        },
    }
    expected = expected_by_feature.get(feature_name)
    if expected is None or feature.get("bindings") != expected:
        return None
    values = feature.get("values")
    if not isinstance(values, list) or any(
        not isinstance(item, str) for item in values
    ):
        return None
    if values != sorted(set(values)):
        return None
    return tuple(values)
