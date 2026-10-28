"""Normalize validated model output to DeepShovel's canonical graph contract."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from . import shared_contract
from .errors import ContractError, ContractIssue, ContractValidationError
from .graph_contract import (
    PaperEvidenceGraph,
    canonical_hash_json,
    normalized_text,
    proposition_key,
    scoped_claim_id,
)
from .models import ArtifactContext, ParserMetadata
from .number_normalization import parse_plain_number

SCHEMA_VERSION = shared_contract.EVIDENCE_GRAPH_SCHEMA_VERSION
EXTRACTION_PROMPT_VERSION = shared_contract.EXTRACTION_PROMPT_VERSION
VERIFICATION_PROMPT_VERSION = shared_contract.VERIFICATION_PROMPT_VERSION
COMPILER_CONTRACT_VERSION = "1.0"
# Canonical Reasoning -> Claim conclusions. ``infers`` is a supporting
# conclusion; ``contradicts`` and ``qualifies`` keep the paper's stance.
REASONING_CONCLUSION_RELATIONS = frozenset(
    shared_contract.REASONING_CONCLUSION_RELATION_NAMES
)
_COMPARATOR = re.compile(r"^\s*(?P<comparator><=|>=|≤|≥|<|>)\s*")
_COMPARATORS = {"<": "<", ">": ">", "<=": "<=", ">=": ">=", "≤": "<=", "≥": ">="}
_ROW = re.compile(r"^\s*(\d+)\s*(?:(?::|-|\.\.)\s*(\d+)\s*)?$")


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def graph_sha256(graph: dict[str, Any]) -> str:
    ordered = dict(graph)
    for collection in (
        "artifacts",
        "artifact_versions",
        "anchors",
        "visual_evidence",
        "observations",
        "assumptions",
        "reasoning",
        "claims",
        "edges",
        "assessments",
        "snapshots",
    ):
        ordered[collection] = sorted(graph[collection], key=lambda item: item["id"])
    return hashlib.sha256(canonical_hash_json(ordered).encode()).hexdigest()


def to_canonical_graph(
    raw: PaperEvidenceGraph,
    artifact: ArtifactContext,
    *,
    parser: ParserMetadata,
    created_at: str | None = None,
) -> dict[str, Any]:
    raw.validate_observation_path_ownership()
    # Canonicalization freezes representation and structural provenance.  In
    # Scientific value/comparator/unit and formula-semantic
    # disagreements are advisory verifier findings rather than whole-graph
    # admission failures.
    created = created_at or utc_now()
    scopes = {scope.id: scope for scope in raw.scopes}
    canonical_scopes = {key: value.canonical() for key, value in scopes.items()}
    empty_scope = _empty_scope()

    claim_ids: dict[str, str] = {}
    claims: list[dict[str, Any]] = []
    claim_index: dict[str, int] = {}
    for index, item in enumerate(raw.claims):
        scope = scopes[item.scope_id]
        key = proposition_key(item.statement)
        scoped = scoped_claim_id(key, scope)
        if scoped in claim_index:
            # Polarity and qualifiers are deliberately not part of Claim
            # identity (recall and cross-paper alignment depend on it), so two
            # local Claims with one statement and Scope are one Claim. Locate
            # the later duplicate so it can be isolated or corrected.
            raise ContractValidationError(
                ContractIssue.CANONICAL_ID_COLLISION, loc=("claims", index)
            )
        claim_index[scoped] = index
        claim_ids[item.id] = scoped
        claims.append(
            {
                "kind": "atomic_claim",
                "id": scoped,
                "source_id": item.id,
                "proposition": normalized_text(item.statement),
                "claim_type": item.claim_type,
                "polarity": item.polarity,
                "qualifiers": sorted(
                    {normalized_text(value) for value in item.qualifiers}
                ),
                "anchor_ids": sorted(item.anchor_ids),
                "proposition_key": key,
                "scoped_claim_id": scoped,
                "scope": canonical_scopes[item.scope_id],
                "modality": _modality(item.statement, item.polarity),
            }
        )
    canonical_claims = {item["id"]: item for item in claims}

    anchors: list[dict[str, Any]] = []
    for index, item in enumerate(raw.anchors):
        try:
            selector, quote, quote_hash = _selector(item.locator)
        except ContractError as exc:
            raise ContractValidationError(
                ContractIssue.CANONICALIZATION_INVALID,
                loc=("anchors", index, "locator", "row_range"),
            ) from exc
        anchors.append(
            {
                "kind": "source_anchor",
                "id": item.id,
                "artifact_version_id": artifact.version_id,
                "anchor_type": item.anchor_type,
                "selector": selector,
                "quote": quote,
                "quote_sha256": quote_hash,
            }
        )

    observations = [
        {
            "kind": "observation",
            "id": item.id,
            "statement": normalized_text(item.statement),
            "observation_type": item.observation_type,
            "anchor_ids": sorted(item.anchor_ids),
            "scope": empty_scope
            if item.scope_id is None
            else canonical_scopes[item.scope_id],
            "value": _observation_value(item.raw_value, item.unit),
        }
        for item in raw.observations
    ]

    raw_claims = {item.id: item for item in raw.claims}
    reasoning_ids = {item.id for item in raw.reasoning}
    outgoing_scopes: dict[str, list[dict[str, Any]]] = {}
    for edge in raw.edges:
        if edge.source_id in reasoning_ids and edge.target_id in claim_ids:
            target = raw_claims[edge.target_id]
            outgoing_scopes.setdefault(edge.source_id, []).append(
                canonical_scopes[target.scope_id]
            )
    reasoning: list[dict[str, Any]] = []
    for item in raw.reasoning:
        candidates = outgoing_scopes.get(item.id, [])
        scope = (
            candidates[0]
            if candidates and all(value == candidates[0] for value in candidates)
            else empty_scope
        )
        reasoning.append(
            {
                "kind": "reasoning",
                "id": item.id,
                "statement": normalized_text(item.statement),
                "reasoning_type": item.reasoning_type,
                "anchor_ids": sorted(item.anchor_ids),
                "scope": scope,
                "limitations": [],
            }
        )
    assumptions = [
        {
            "kind": "assumption",
            "id": item.id,
            "statement": normalized_text(item.statement),
            "assumption_type": item.assumption_type,
            "explicit": bool(item.anchor_ids),
            "anchor_ids": sorted(item.anchor_ids),
            "scope": empty_scope,
        }
        for item in raw.assumptions
    ]
    visuals = [_visual(item) for item in raw.visual_evidence]

    node_types = {
        **{item.id: "anchor" for item in raw.anchors},
        **{item.id: "visual" for item in raw.visual_evidence},
        **{item.id: "observation" for item in raw.observations},
        **{item.id: "reasoning" for item in raw.reasoning},
        **{item.id: "assumption" for item in raw.assumptions},
        **{item.id: "claim" for item in raw.claims},
    }
    edges: list[dict[str, Any]] = []
    signatures: set[tuple[str, str, str]] = set()
    edge_ids: set[str] = set()
    for edge_index, item in enumerate(raw.edges):
        source_type = node_types[item.source_id]
        target_type = node_types[item.target_id]
        source = claim_ids.get(item.source_id, item.source_id)
        target = claim_ids.get(item.target_id, item.target_id)
        relation = item.relation
        comparison = source_type == "claim" and target_type == "claim"
        if relation == "supports" and source_type == "reasoning":
            # A supporting conclusion is the kernel's ``infers`` derivation.
            # Contradicting and qualifying conclusions keep their relation so a
            # refutation or limitation is never stored as an inference.
            relation = "infers"
        overlap = None
        if comparison:
            try:
                overlap = _scope_overlap(
                    canonical_claims[source]["scope"],
                    canonical_claims[target]["scope"],
                    equivalent=relation == "equivalent_to",
                )
            except ContractValidationError as exc:
                raise ContractValidationError(
                    exc.contract_issue, loc=("edges", edge_index)
                ) from None
        signature = (source, target, relation)
        if signature in signatures:
            raise ContractValidationError(
                ContractIssue.CANONICAL_EDGE_COLLAPSE, loc=("edges", edge_index)
            )
        if item.id in edge_ids:
            raise ContractValidationError(
                ContractIssue.CANONICAL_ID_COLLISION, loc=("edges", edge_index)
            )
        signatures.add(signature)
        edge_ids.add(item.id)
        edges.append(
            {
                "kind": "edge",
                "id": item.id,
                "relation": relation,
                "source_id": source,
                "target_id": target,
                "rationale": None
                if item.rationale is None
                else normalized_text(item.rationale),
                "confidence": _number(item.confidence),
                "scope_overlap": overlap,
            }
        )
    for observation in raw.observations:
        for anchor_id in observation.anchor_ids:
            signature = (anchor_id, observation.id, "grounds")
            if signature in signatures:
                continue
            digest = hashlib.sha256(
                f"{anchor_id}\0{observation.id}\0grounds".encode()
            ).hexdigest()
            edge_id = f"edge:compiler:{digest}"
            if edge_id in edge_ids:
                raise ContractValidationError(ContractIssue.CANONICAL_ID_COLLISION)
            signatures.add(signature)
            edge_ids.add(edge_id)
            edges.append(
                {
                    "kind": "edge",
                    "id": edge_id,
                    "relation": "grounds",
                    "source_id": anchor_id,
                    "target_id": observation.id,
                    "rationale": None,
                    "confidence": None,
                    "scope_overlap": None,
                }
            )
    _require_paths(
        observations,
        reasoning,
        claims,
        edges,
        raw_index={
            **{item.id: ("observations", i) for i, item in enumerate(raw.observations)},
            **{item.id: ("reasoning", i) for i, item in enumerate(raw.reasoning)},
            **{scoped: ("claims", i) for scoped, i in claim_index.items()},
        },
    )

    graph: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "paper_id": raw.paper_id,
        "artifacts": [
            {
                "kind": "artifact",
                "id": artifact.artifact_id,
                "canonical_name": unicodedata.normalize("NFC", artifact.file_name),
                "artifact_type": _artifact_type(artifact.media_type),
            }
        ],
        "artifact_versions": [
            {
                "kind": "artifact_version",
                "id": artifact.version_id,
                "artifact_id": artifact.artifact_id,
                "version": artifact.version_id,
                "locator": unicodedata.normalize("NFC", artifact.locator),
                "sha256": artifact.artifact_sha256,
                "media_type": artifact.media_type,
            }
        ],
        "anchors": sorted(anchors, key=lambda value: value["id"]),
        "visual_evidence": sorted(visuals, key=lambda value: value["id"]),
        "observations": sorted(observations, key=lambda value: value["id"]),
        "assumptions": sorted(assumptions, key=lambda value: value["id"]),
        "reasoning": sorted(reasoning, key=lambda value: value["id"]),
        "claims": sorted(claims, key=lambda value: value["id"]),
        "edges": sorted(edges, key=lambda value: value["id"]),
        "assessments": [],
        "snapshots": [],
    }
    entity_ids = [
        item["id"]
        for collection in (
            "artifacts",
            "artifact_versions",
            "anchors",
            "visual_evidence",
            "observations",
            "assumptions",
            "reasoning",
            "claims",
        )
        for item in graph[collection]
    ]
    duplicate = next(
        (key for key, count in Counter(entity_ids).items() if count > 1), None
    )
    if duplicate:
        raise ContractValidationError(ContractIssue.CANONICAL_ID_COLLISION)
    entity_ids.sort()
    edge_ids_sorted = sorted(item["id"] for item in graph["edges"])
    payload = _snapshot_payload(graph, entity_ids, edge_ids_sorted)
    content_hash = hashlib.sha256(canonical_hash_json(payload).encode()).hexdigest()
    graph["snapshots"] = [
        {
            "kind": "snapshot",
            "id": f"snapshot:sha256:{content_hash}",
            "parent_snapshot_id": None,
            "entity_ids": entity_ids,
            "edge_ids": edge_ids_sorted,
            "assessment_ids": [],
            "content_sha256": content_hash,
            "created_at": created,
            "generator": {
                "source": "extractor",
                "name": EXTRACTION_PROMPT_VERSION,
                "version": (
                    f"compiler/{COMPILER_CONTRACT_VERSION}+{parser.name}/"
                    f"{parser.version}-{parser.transport}-{parser.backend}"
                ),
            },
        }
    ]
    return graph


def _empty_scope() -> dict[str, Any]:
    return {
        "location": [],
        "time": None,
        "formation": [],
        "sample_type": [],
        "method": [],
        "condition": [],
        "measurement": [],
        "uncertainty": None,
        "entities": [],
    }


def _selector(locator: Any) -> tuple[dict[str, Any], str | None, str | None]:
    row_start = row_end = None
    if locator.row_range is not None:
        match = _ROW.fullmatch(locator.row_range)
        if match is None:
            raise ContractError("row_range must be an integer or start:end")
        row_start = int(match.group(1))
        row_end = int(match.group(2) or match.group(1))
        if row_start > row_end:
            raise ContractError("row_range start exceeds end")
    quote = None
    quote_hash = None
    if locator.quote is not None:
        quote = unicodedata.normalize("NFC", locator.quote)
        quote_hash = hashlib.sha256(quote.encode()).hexdigest()
    region = None
    if locator.region is not None:
        region = {
            "x": _number(locator.region.x),
            "y": _number(locator.region.y),
            "width": _number(locator.region.width),
            "height": _number(locator.region.height),
            "unit": locator.region.unit,
        }
    return (
        {
            "page": locator.page,
            "section": locator.section,
            "paragraph": locator.paragraph,
            "table": locator.table,
            "row_start": row_start,
            "row_end": row_end,
            "row_range_label": locator.row_range,
            "column_names": locator.column_names,
            "figure": locator.figure,
            "series": locator.series,
            "char_start": None,
            "char_end": None,
            "region": region,
            "region_label": None,
            "dataset_path": None,
        },
        quote,
        quote_hash,
    )


def _observation_value(raw: str | None, unit: str | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    comparator, numeric = observation_number(raw)
    return {
        "raw": unicodedata.normalize("NFC", raw),
        "numeric": numeric,
        "comparator": comparator,
        "unit": unit,
        "uncertainty": None,
    }


def observation_number(raw: str) -> tuple[str | None, float | int | None]:
    """Comparator and value of one unambiguous number, else ``(None, None)``.

    ``value.numeric`` is hashed into immutable snapshots, so it is set only
    when the whole raw value is one number (``1,234 mg`` -> 1234,
    ``2.5×10⁻³`` -> 0.0025, ``−5`` -> -5). Ranges, lists and non-finite
    values keep their raw text with a null numeric value.
    """

    comparator = None
    rest = raw
    match = _COMPARATOR.match(raw)
    if match is not None:
        comparator = _COMPARATORS[match.group("comparator")]
        rest = raw[match.end() :]
    numeric = parse_plain_number(rest)
    if numeric is None:
        return None, None
    return comparator, _number(numeric)


def _visual(item: Any) -> dict[str, Any]:
    series = [entry.model_dump(mode="json") for entry in item.series]
    for entry in series:
        for point in entry["points"]:
            for coordinate in ("x", "y"):
                if not isinstance(point[coordinate], str):
                    point[coordinate] = _number(point[coordinate])
            point["uncertainty"] = _number(point["uncertainty"])
    cells = [cell.model_dump(mode="json") for cell in item.cells]
    for cell in cells:
        # JSON.stringify emits an integral model float such as 42.0 as 42.
        cell["numeric_value"] = _number(cell["numeric_value"])
    features = [feature.model_dump(mode="json") for feature in item.features]
    for feature in features:
        feature["value"] = _number(feature["value"])
    return {
        "kind": "structured_visual_evidence",
        "id": item.id,
        "anchor_id": item.anchor_id,
        "visual_kind": item.visual_kind,
        "axes": [axis.model_dump(mode="json") for axis in item.axes],
        "series": series,
        "cells": cells,
        "features": features,
    }


def _scope_overlap(
    source: dict[str, Any], target: dict[str, Any], *, equivalent: bool
) -> str:
    if source == target:
        return "equivalent"
    if equivalent:
        raise ContractValidationError(
            ContractIssue.CANONICAL_CLAIM_COMPARISON_SCOPE_INVALID
        )
    compared = 0
    for key in (
        "location",
        "formation",
        "sample_type",
        "method",
        "condition",
        "measurement",
        "entities",
    ):
        left, right = source[key], target[key]
        if left and right:
            compared += 1
            if set(left).isdisjoint(right):
                raise ContractValidationError(
                    ContractIssue.CANONICAL_CLAIM_COMPARISON_SCOPE_INVALID
                )
    if source["time"] is not None and target["time"] is not None:
        compared += 1
        if source["time"]["label"] != target["time"]["label"]:
            raise ContractValidationError(
                ContractIssue.CANONICAL_CLAIM_COMPARISON_SCOPE_INVALID
            )
    if compared == 0:
        raise ContractValidationError(
            ContractIssue.CANONICAL_CLAIM_COMPARISON_SCOPE_INVALID
        )
    return "partial"


def _require_paths(
    observations: list[dict[str, Any]],
    reasoning: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    raw_index: dict[str, tuple[str, int]] | None = None,
) -> None:
    def invalid(node_id: str) -> ContractValidationError:
        loc = () if raw_index is None else raw_index.get(node_id, ())
        return ContractValidationError(ContractIssue.CANONICAL_PATH_INVALID, loc=loc)

    incoming: dict[str, set[str]] = {}
    outgoing: dict[str, set[str]] = {}
    for edge in edges:
        incoming.setdefault(edge["target_id"], set()).add(edge["relation"])
        outgoing.setdefault(edge["source_id"], set()).add(edge["relation"])
    for item in observations:
        if "grounds" not in incoming.get(item["id"], set()):
            raise invalid(item["id"])
    claim_ids = {item["id"] for item in claims}
    reasoning_ids = {item["id"] for item in reasoning}
    concluded_claims: set[str] = set()
    # A reasoning step reaches a Claim when it concludes one or is a premise of
    # a step that does: one backward pass instead of a search per step.
    reaches_claim: set[str] = set()
    premise_sources: dict[str, list[str]] = {}
    for edge in edges:
        if edge["source_id"] not in reasoning_ids:
            continue
        if (
            edge["relation"] in REASONING_CONCLUSION_RELATIONS
            and edge["target_id"] in claim_ids
        ):
            concluded_claims.add(edge["target_id"])
            reaches_claim.add(edge["source_id"])
        elif edge["relation"] == "premise_for":
            premise_sources.setdefault(edge["target_id"], []).append(
                edge["source_id"]
            )
    pending = list(reaches_claim)
    while pending:
        for source_id in premise_sources.get(pending.pop(), ()):
            if source_id not in reaches_claim:
                reaches_claim.add(source_id)
                pending.append(source_id)
    for item in reasoning:
        if "premise_for" not in incoming.get(item["id"], set()):
            raise invalid(item["id"])
        if not ({"premise_for"} | REASONING_CONCLUSION_RELATIONS).intersection(
            outgoing.get(item["id"], set())
        ):
            raise invalid(item["id"])
        if item["id"] not in reaches_claim:
            raise invalid(item["id"])
    for item in claims:
        if item["id"] not in concluded_claims:
            raise invalid(item["id"])


def _snapshot_payload(
    graph: dict[str, Any], entity_ids: list[str], edge_ids: list[str]
) -> dict[str, Any]:
    entities = {
        item["id"]: item
        for collection in (
            "artifacts",
            "artifact_versions",
            "anchors",
            "visual_evidence",
            "observations",
            "assumptions",
            "reasoning",
            "claims",
        )
        for item in graph[collection]
    }
    edges = {item["id"]: item for item in graph["edges"]}
    return {
        "schema_version": graph["schema_version"],
        "paper_id": graph["paper_id"],
        "parent_snapshot_id": None,
        "entities": [entities[key] for key in entity_ids],
        "edges": [edges[key] for key in edge_ids],
        "assessments": [],
    }


def _artifact_type(media_type: str) -> str:
    if media_type.startswith("image/"):
        return "figure"
    if media_type in {"text/csv", "text/tab-separated-values"}:
        return "table"
    if "spreadsheet" in media_type:
        return "dataset"
    return "paper"


def _modality(statement: str, polarity: str) -> str:
    if polarity == "uncertain":
        return "hypothesis"
    conditional = normalized_text(statement).lower().startswith(("if ", "若"))
    return "conditional" if conditional else "asserted"


def _number(value: float | int | None) -> float | int | None:
    if value is None or isinstance(value, int):
        return value
    return int(value) if value.is_integer() else value


__all__ = [
    "EXTRACTION_PROMPT_VERSION",
    "VERIFICATION_PROMPT_VERSION",
    "graph_sha256",
    "to_canonical_graph",
    "utc_now",
]
