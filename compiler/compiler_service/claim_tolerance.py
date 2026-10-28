"""Deterministic Claim-level isolation for local failures.

One rule covers every local failure (GRF-9):

* a node on no Claim's Evidence Path is pruned and reported, whether or not
  the rest of the response was valid;
* a malformed record with a stable ID is dropped, and every Claim that depends
  on it is removed (a malformed edge breaks what it feeds, its target);
* a located violation removes the Claims that depend on its location -- for an
  edge, on its target, so a bad edge from a shared source cannot taint Claims
  that never use it.

Removed Claims take only their exclusive evidence closure with them, within the
cumulative local budget. Anything global, unlocated, or over budget raises the
strict error so the retry receives its feedback.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from .errors import ContractIssue, ContractValidationError, ContractViolation
from .graph_contract import (
    Assessment,
    Assumption,
    Claim,
    Edge,
    EvidencePaths,
    Observation,
    PaperEvidenceGraph,
    ReasoningStep,
    Scope,
    SourceAnchor,
    StructuredVisualEvidence,
    VerificationReport,
)

LOCAL_CLAIM_ERROR_RATIO = 0.20

_RECORD_MODELS: tuple[tuple[str, type[Any]], ...] = (
    ("claims", Claim),
    ("observations", Observation),
    ("reasoning", ReasoningStep),
    ("assumptions", Assumption),
    ("anchors", SourceAnchor),
    ("visual_evidence", StructuredVisualEvidence),
    ("scopes", Scope),
    ("edges", Edge),
)
_GRAPH_COLLECTIONS = frozenset(name for name, _ in _RECORD_MODELS)
# Removed records the audit lists by ID; edges follow their endpoints.
_AUDITED_NODE_COLLECTIONS = (
    "observations",
    "reasoning",
    "assumptions",
    "anchors",
    "visual_evidence",
    "scopes",
)
# The normalized shape of ``models.Identifier``.
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9._:-]{0,127}")


@dataclass(frozen=True, slots=True)
class ParsedGraph:
    graph: PaperEvidenceGraph
    original_claim_count: int
    # (Claim ID, value-free reason) in detection order; the ID is None for a
    # malformed Claim record whose own ID is unusable.
    removed_claims: tuple[tuple[str | None, str], ...] = ()
    invalid_node_ids: tuple[str, ...] = ()
    unreachable_node_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ParsedVerification:
    report: VerificationReport
    schema_invalid_claim_ids: frozenset[str]
    unattributable_records: int = 0


def max_local_claim_errors(total_claims: int) -> int:
    """Return the cumulative local-loss budget: at most 20% of the Claims.

    Small extractions get no tolerance (one of two Claims would be a 50%
    loss); their local failures take the normal fresh-retry path instead.
    """

    if total_claims <= 1:
        return 0
    return (total_claims * 20) // 100  # LOCAL_CLAIM_ERROR_RATIO, exact


def parse_graph_with_local_claim_tolerance(payload: dict[str, Any]) -> ParsedGraph:
    """Parse one extraction into exactly the Evidence Paths of its usable Claims.

    The authoritative JSON Schema remains ``PaperEvidenceGraph``. A strictly
    valid response loses only its unreachable nodes; otherwise every record is
    validated on its own and local failures are isolated as described in the
    module docstring. The response envelope itself is never repaired.
    """

    raw_claims = payload.get("claims")
    if not isinstance(raw_claims, list):
        return ParsedGraph(PaperEvidenceGraph.model_validate(payload, strict=True), 0)
    try:
        graph = PaperEvidenceGraph.model_validate(payload, strict=True)
    except (ValidationError, ContractValidationError) as strict_error:
        return _isolate_local_failures(payload, len(raw_claims), strict_error)
    if not graph.claims:
        return ParsedGraph(graph, 0)
    unreachable = _unreachable_node_ids(graph)
    if unreachable:
        graph = prune_graph_to_claims(graph, {claim.id for claim in graph.claims})
    return ParsedGraph(graph, len(raw_claims), unreachable_node_ids=unreachable)


def _isolate_local_failures(
    payload: dict[str, Any],
    original_count: int,
    strict_error: ValidationError | ContractValidationError,
) -> ParsedGraph:
    if set(payload) != set(PaperEvidenceGraph.model_fields):
        raise strict_error from None
    records: dict[str, list[Any]] = {}
    broken: dict[str, str] = {}
    anonymous_claims = 0
    for collection, model in _RECORD_MODELS:
        raw_items = payload[collection]
        if not isinstance(raw_items, list):
            raise strict_error from None
        records[collection] = []
        for raw in raw_items:
            try:
                records[collection].append(model.model_validate(raw, strict=True))
            except ValidationError:
                dependency = _local_record_id(
                    raw, "target_id" if collection == "edges" else "id"
                )
                if dependency is None:
                    if collection == "claims" and isinstance(raw, dict):
                        # Nothing can validly reference an unusable Claim ID.
                        anonymous_claims += 1
                        continue
                    raise strict_error from None
                broken.setdefault(dependency, collection)
    temporary = PaperEvidenceGraph.model_construct(
        schema_version=payload["schema_version"],
        paper_id=payload["paper_id"],
        **records,
    )
    dependents = _DependentClaims(temporary)
    removed: dict[str, str] = {
        record_id: "extraction:schema_invalid"
        for record_id, collection in broken.items()
        if collection == "claims"
    }
    for claim_id in sorted(dependents.of(broken)):
        removed.setdefault(claim_id, "extraction:dependency_invalid")
    unreachable = _unreachable_node_ids(temporary)

    budget = max_local_claim_errors(original_count)
    retained = {claim.id for claim in temporary.claims} - set(removed)
    while True:
        kept = sum(1 for claim in temporary.claims if claim.id in retained)
        if not kept or original_count - kept > budget:
            raise strict_error from None
        pruned = _pruned_payload(temporary, retained)
        try:
            graph = PaperEvidenceGraph.model_validate(pruned, strict=True)
        except ContractValidationError as error:
            affected = _affected_claims(dependents, pruned, error.violations, retained)
            if affected is None:
                raise strict_error from None
            for claim_id, issue in affected.items():
                removed.setdefault(claim_id, f"extraction:{issue.value}")
            retained -= set(affected)
        except ValidationError:
            raise strict_error from None
        else:
            break
    return ParsedGraph(
        graph,
        original_count,
        removed_claims=(
            *removed.items(),
            *((None, "extraction:schema_invalid"),) * anonymous_claims,
        ),
        invalid_node_ids=tuple(
            record_id
            for record_id, collection in broken.items()
            if collection in _AUDITED_NODE_COLLECTIONS
        ),
        unreachable_node_ids=unreachable,
    )


def parse_verification_with_local_claim_tolerance(
    payload: dict[str, Any],
    *,
    valid_claim_ids: Collection[str],
) -> ParsedVerification:
    """Drop malformed assessment records without dropping their Claims.

    A malformed record says nothing about its Claim; the Claim simply remains
    unassessed and is requested again. Records naming no known Claim are
    counted and reported. The report envelope itself must be valid, and at
    least one assessment must survive, otherwise the strict error is raised so
    the retry receives schema feedback.
    """

    try:
        return ParsedVerification(
            VerificationReport.model_validate(payload, strict=True),
            frozenset(),
        )
    except ValidationError as strict_error:
        raw_assessments = payload.get("assessments")
        if not isinstance(raw_assessments, list):
            raise strict_error
        known = set(valid_claim_ids)
        valid: list[Assessment] = []
        invalid_claim_ids: set[str] = set()
        unattributable = 0
        for raw_assessment in raw_assessments:
            try:
                valid.append(Assessment.model_validate(raw_assessment, strict=True))
            except ValidationError:
                claim_id = _local_record_id(raw_assessment, "claim_id")
                if claim_id is not None and claim_id in known:
                    invalid_claim_ids.add(claim_id)
                else:
                    unattributable += 1
        if (not invalid_claim_ids and not unattributable) or not valid:
            raise strict_error
        try:
            report = VerificationReport.model_validate(
                {
                    **payload,
                    "assessments": [
                        item.model_dump(mode="json") for item in valid
                    ],
                },
                strict=True,
            )
        except ValidationError:
            raise strict_error from None
        return ParsedVerification(
            report, frozenset(invalid_claim_ids), unattributable
        )


def prune_graph_to_claims(
    graph: PaperEvidenceGraph,
    retained_claim_ids: Collection[str],
) -> PaperEvidenceGraph:
    """Return the exact dependency closure of retained Claims and no orphans."""

    return PaperEvidenceGraph.model_validate(
        _pruned_payload(graph, retained_claim_ids), strict=True
    )


def _pruned_payload(
    graph: PaperEvidenceGraph,
    retained_claim_ids: Collection[str],
) -> dict[str, Any]:
    retained = set(retained_claim_ids)
    reachable = EvidencePaths(graph).closure(retained)
    retained.update(item.id for item in graph.claims if item.id in reachable)

    scope_ids = {item.scope_id for item in graph.claims if item.id in retained}
    scope_ids.update(
        item.scope_id
        for item in graph.observations
        if item.id in reachable and item.scope_id is not None
    )
    payload = graph.model_dump(mode="json")
    for key in (
        "observations",
        "reasoning",
        "assumptions",
        "anchors",
        "visual_evidence",
    ):
        payload[key] = [item for item in payload[key] if item["id"] in reachable]
    payload["claims"] = [item for item in payload["claims"] if item["id"] in retained]
    node_ids = reachable | retained
    payload["edges"] = [
        item
        for item in payload["edges"]
        if item["source_id"] in node_ids and item["target_id"] in node_ids
    ]
    payload["scopes"] = [item for item in payload["scopes"] if item["id"] in scope_ids]
    return payload


def _unreachable_node_ids(graph: PaperEvidenceGraph) -> tuple[str, ...]:
    """Records on no Claim's Evidence Path, in collection order."""

    reachable = EvidencePaths(graph).closure(claim.id for claim in graph.claims)
    used_scopes = {item.scope_id for item in graph.claims}
    used_scopes.update(
        item.scope_id
        for item in graph.observations
        if item.id in reachable and item.scope_id is not None
    )
    return tuple(
        item.id
        for collection in _AUDITED_NODE_COLLECTIONS
        for item in getattr(graph, collection)
        if item.id not in (used_scopes if collection == "scopes" else reachable)
    )


def claims_affected_by_violations(
    graph: PaperEvidenceGraph,
    violations: Sequence[ContractViolation],
) -> dict[str, ContractIssue] | None:
    """Map located violations to every Claim that depends on them.

    ``None`` means some violation is global or cannot be isolated safely.
    """

    return _affected_claims(
        _DependentClaims(graph),
        graph,
        violations,
        {claim.id for claim in graph.claims},
    )


def _affected_claims(
    dependents: _DependentClaims,
    container: Any,
    violations: Sequence[ContractViolation],
    retained: Collection[str],
) -> dict[str, ContractIssue] | None:
    affected: dict[str, ContractIssue] = {}
    for violation in violations:
        node_id = _located_node_id(container, violation.loc)
        claims = set() if node_id is None else dependents.of((node_id,))
        claims.intersection_update(retained)
        if not claims:
            return None
        for claim_id in sorted(claims):
            affected.setdefault(claim_id, violation.issue)
    return affected


def _located_node_id(container: Any, loc: tuple[str | int, ...]) -> str | None:
    """The node a located violation removes: an edge's target, else the item."""

    if len(loc) < 2 or loc[0] not in _GRAPH_COLLECTIONS or type(loc[1]) is not int:
        return None
    collection, index = loc[0], loc[1]
    items = (
        container[collection]
        if isinstance(container, dict)
        else getattr(container, collection)
    )
    if not 0 <= index < len(items):
        return None
    key = "target_id" if collection == "edges" else "id"
    item = items[index]
    value = item.get(key) if isinstance(item, dict) else getattr(item, key, None)
    return value if isinstance(value, str) else None


class _DependentClaims:
    """Forward evidence dependencies of one graph, built once.

    A Claim depends on a node when the node is on its Evidence Path or is the
    Scope of the Claim or of an Observation on that path.
    """

    def __init__(self, graph: PaperEvidenceGraph) -> None:
        self._claim_ids = {claim.id for claim in graph.claims}
        children: dict[str, list[str]] = {}
        for collection in (
            graph.claims,
            graph.observations,
            graph.reasoning,
            graph.assumptions,
        ):
            for item in collection:
                for anchor_id in item.anchor_ids:
                    children.setdefault(anchor_id, []).append(item.id)
        for item in graph.visual_evidence:
            children.setdefault(item.anchor_id, []).append(item.id)
        for item in (*graph.claims, *graph.observations):
            if item.scope_id is not None:
                children.setdefault(item.scope_id, []).append(item.id)
        for edge in graph.edges:
            if edge.source_id in self._claim_ids and edge.target_id in self._claim_ids:
                continue
            children.setdefault(edge.source_id, []).append(edge.target_id)
        self._children = children

    def of(self, node_ids: Iterable[str]) -> set[str]:
        """Claims that depend on any of ``node_ids``."""

        seen = set(node_ids)
        pending = list(seen)
        while pending:
            for child in self._children.get(pending.pop(), ()):
                if child not in seen:
                    seen.add(child)
                    pending.append(child)
        return seen & self._claim_ids


def _local_record_id(value: Any, key: str) -> str | None:
    """A malformed record's reference, normalized and checked like Identifier.

    Without one the failure cannot be attributed, so it stays global; a raw
    string would also be unusable as an audit target.
    """

    if not isinstance(value, dict):
        return None
    candidate = value.get(key)
    if not isinstance(candidate, str):
        return None
    candidate = candidate.strip()
    return candidate if _IDENTIFIER.fullmatch(candidate) else None


__all__ = [
    "LOCAL_CLAIM_ERROR_RATIO",
    "ParsedGraph",
    "ParsedVerification",
    "claims_affected_by_violations",
    "max_local_claim_errors",
    "parse_graph_with_local_claim_tolerance",
    "parse_verification_with_local_claim_tolerance",
    "prune_graph_to_claims",
]
