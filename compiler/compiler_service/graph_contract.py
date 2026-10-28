"""Independent strict LLM contract for one paper's scientific evidence graph.

The Compiler owns this extraction-facing JSON shape. Its canonicalized output is
validated by the TypeScript evidence-graph kernel, and cross-language tests guard
drift between those independently deployable boundaries.
"""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator
from pydantic_core import PydanticCustomError

from .errors import ContractIssue, ContractValidationError, ContractViolation
from .models import Identifier, MediaType, Sha256, ShortText, StrictModel
from .shared_contract import (
    NORMALIZED_REGION_EXTENT_MAX,
    ClaimPolarity,
    ClaimType,
)

Statement = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8_000)
]
RawValue = Annotated[str, StringConstraints(min_length=1, max_length=8_000)]
Probability = Annotated[float | int, Field(ge=0, le=1)]
ObservationType = Literal[
    "measurement",
    "trend",
    "pattern",
    "comparison",
    "correlation",
    "source_assertion",
]
ReasoningType = Literal[
    "deductive",
    "inductive",
    "abductive",
    "causal_inference",
    "statistical_inference",
    "analogy",
    "synthesis",
]
AssumptionType = Literal[
    "background_knowledge",
    "methodological",
    "measurement",
    "statistical",
    "causal",
    "scope",
    "generalization",
]
AnchorType = Literal["text", "figure", "table", "dataset", "metadata", "citation"]
EdgeRelation = Literal[
    "grounds",
    "premise_for",
    "assumption_for",
    "supports",
    "contradicts",
    "qualifies",
    "refines",
    "equivalent_to",
]

# Nested visual collections need their own hard ceilings: top-level maxItems does
# not otherwise constrain a figure with many series/points or a large table.
MAX_VISUAL_AXES = 4
MAX_VISUAL_SERIES = 4
MAX_VISUAL_POINTS_PER_SERIES = 16
MAX_TABLE_CELLS = 64
MAX_VISUAL_FEATURES = 8


def normalized_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


def canonical_json(value: Any) -> str:
    return json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_hash_json(value: Any) -> str:
    """Serialize identity inputs with the Web's JSON.stringify number rules.

    This stays separate from ``canonical_json`` so prompt, Schema, job, and model
    payload bytes do not change when hash compatibility is tightened.
    """

    return _hash_json_value(_canonical_value(value))


def _hash_json_value(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float)):
        return _ecmascript_number(value)
    if isinstance(value, list):
        return "[" + ",".join(_hash_json_value(item) for item in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("canonical hash JSON requires string object keys")
        keys = sorted(value, key=lambda key: key.encode("utf-16-be"))
        return (
            "{"
            + ",".join(
                f"{json.dumps(key, ensure_ascii=False)}:{_hash_json_value(value[key])}"
                for key in keys
            )
            + "}"
        )
    raise TypeError(f"canonical hash JSON rejects {type(value).__name__}")


def _ecmascript_number(value: int | float) -> str:
    # repr() supplies shortest round-tripping digits; these placement thresholds
    # are ECMAScript NumberToString / RFC 8785 rather than Python json.dumps.
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(
            "canonical hash JSON rejects numbers outside IEEE-754"
        ) from exc
    if not math.isfinite(number):
        raise ValueError("canonical hash JSON rejects non-finite numbers")
    if number == 0:
        return "0"

    sign = "-" if number < 0 else ""
    shortest = repr(abs(number)).lower()
    if "e" in shortest:
        mantissa, exponent = shortest.split("e")
        integer, _, fraction = mantissa.partition(".")
        digits = (integer + fraction).lstrip("0").rstrip("0")
        decimal_point = int(exponent) + len(integer)
    else:
        integer, _, fraction = shortest.partition(".")
        combined = integer + fraction
        leading_zeroes = len(combined) - len(combined.lstrip("0"))
        digits = combined.lstrip("0").rstrip("0")
        decimal_point = len(integer) - leading_zeroes

    digit_count = len(digits)
    if digit_count <= decimal_point <= 21:
        body = digits + "0" * (decimal_point - digit_count)
    elif 0 < decimal_point <= 21:
        body = digits[:decimal_point] + "." + digits[decimal_point:]
    elif -6 < decimal_point <= 0:
        body = "0." + "0" * (-decimal_point) + digits
    else:
        exponent = decimal_point - 1
        fraction = f".{digits[1:]}" if digit_count > 1 else ""
        body = f"{digits[0]}{fraction}e{'+' if exponent >= 0 else ''}{exponent}"
    return sign + body


def _canonical_value(value: Any) -> Any:
    if isinstance(value, StrictModel):
        return _canonical_value(value.model_dump(mode="json"))
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical JSON rejects non-finite numbers")
        return 0 if value == 0 else value
    if isinstance(value, Mapping):
        return {key: _canonical_value(value[key]) for key in sorted(value)}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical_value(item) for item in value]
    raise TypeError(f"canonical JSON rejects {type(value).__name__}")


def proposition_key(statement: str) -> str:
    identity = normalized_text(statement).lower()
    return "proposition:sha256:" + hashlib.sha256(identity.encode()).hexdigest()


def validate_unique_scope_entities(values: list[str]) -> list[str]:
    """Keep Scope duplicate diagnostics stable."""

    if len(values) != len(set(values)):
        raise PydanticCustomError(
            "scope_entity_duplicate",
            "scope entities must be unique",
        )
    return values


class Scope(StrictModel):
    id: Identifier
    location: ShortText | None
    time: ShortText | None
    formation: ShortText | None
    sample_type: ShortText | None
    method: ShortText | None
    condition: ShortText | None
    measurement: ShortText | None
    uncertainty: ShortText | None
    entities: list[ShortText]

    @field_validator("entities")
    @classmethod
    def unique_entities(cls, values: list[str]) -> list[str]:
        return validate_unique_scope_entities(values)

    def canonical(self) -> dict[str, Any]:
        def one(value: str | None) -> list[str]:
            return [] if value is None else [normalized_text(value)]

        time = None
        if self.time is not None:
            time = {
                "label": normalized_text(self.time),
                "start": None,
                "end": None,
                "unit": None,
            }
        return {
            "location": one(self.location),
            "time": time,
            "formation": one(self.formation),
            "sample_type": one(self.sample_type),
            "method": one(self.method),
            "condition": one(self.condition),
            "measurement": one(self.measurement),
            "uncertainty": None
            if self.uncertainty is None
            else normalized_text(self.uncertainty),
            "entities": sorted({normalized_text(item) for item in self.entities}),
        }


def scoped_claim_id(key: str, scope: Scope) -> str:
    payload = {"proposition_key": key, "scope": scope.canonical()}
    digest = hashlib.sha256(canonical_hash_json(payload).encode()).hexdigest()
    return "claim:sha256:" + digest


class Region(StrictModel):
    x: Annotated[float | int, Field(ge=0)]
    y: Annotated[float | int, Field(ge=0)]
    width: Annotated[float | int, Field(gt=0)]
    height: Annotated[float | int, Field(gt=0)]
    unit: Literal["normalized"]

    @model_validator(mode="after")
    def normalized_bounds(self) -> Region:
        if (
            self.x > 1
            or self.y > 1
            or self.x + self.width > NORMALIZED_REGION_EXTENT_MAX
            or self.y + self.height > NORMALIZED_REGION_EXTENT_MAX
        ):
            raise ValueError("normalized region must fit inside [0,1]")
        return self


class SourceLocator(StrictModel):
    # These coordinates are independently optional. Model JSON commonly omits
    # an unavailable nullable member instead of spelling it as ``null``; both
    # representations are semantically identical and must not invalidate an
    # otherwise complete graph. ``has_coordinate`` below still rejects a truly
    # empty locator, so this is normalization rather than provenance relaxation.
    page: Annotated[int, Field(ge=1)] | None = None
    # The Compiler makes exact source identity caller-owned. The model selects one
    # immutable catalog entry; Compiler hydrates page, quote, and geometry from
    # DocumentIR before any graph contract is evaluated.
    source_span_id: Identifier | None = None
    section: ShortText | None = None
    paragraph: Annotated[int, Field(ge=1)] | None = None
    figure: ShortText | None = None
    table: ShortText | None = None
    row_range: ShortText | None = None
    column_names: list[ShortText] = Field(default_factory=list)
    series: ShortText | None = None
    region: Region | None = None
    quote: (
        Annotated[
            str,
            StringConstraints(min_length=1, max_length=16_000),
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def has_coordinate(self) -> SourceLocator:
        values = (
            self.page,
            self.source_span_id,
            self.section,
            self.paragraph,
            self.figure,
            self.table,
            self.row_range,
            self.series,
            self.region,
            self.quote,
        )
        if all(value is None for value in values) and not self.column_names:
            raise ValueError("locator must contain a source coordinate")
        return self


class SourceAnchor(StrictModel):
    id: Identifier
    artifact_id: Identifier
    version_id: Identifier
    artifact_sha256: Sha256
    media_type: MediaType
    anchor_type: AnchorType
    locator: SourceLocator


class Claim(StrictModel):
    id: Identifier
    statement: Statement
    claim_type: ClaimType
    polarity: ClaimPolarity
    scope_id: Identifier
    anchor_ids: list[Identifier]
    qualifiers: list[ShortText]
    proposition_key: (
        Annotated[str, StringConstraints(pattern=r"^proposition:sha256:[a-f0-9]{64}$")]
        | None
    ) = None
    scoped_claim_id: (
        Annotated[str, StringConstraints(pattern=r"^claim:sha256:[a-f0-9]{64}$")] | None
    ) = None


class Observation(StrictModel):
    id: Identifier
    statement: Statement
    observation_type: ObservationType
    anchor_ids: list[Identifier]
    scope_id: Identifier | None
    raw_value: RawValue | None
    unit: ShortText | None

    @field_validator("raw_value")
    @classmethod
    def raw_value_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("raw_value must not be whitespace only")
        return value


class ReasoningStep(StrictModel):
    id: Identifier
    statement: Statement
    reasoning_type: ReasoningType
    anchor_ids: list[Identifier]


class Assumption(StrictModel):
    id: Identifier
    statement: Statement
    assumption_type: AssumptionType
    anchor_ids: list[Identifier]


class VisualAxis(StrictModel):
    id: Identifier
    direction: Literal["x", "y"]
    label: ShortText
    unit: ShortText | None
    scale: Literal["linear", "log", "categorical"]


class VisualPoint(StrictModel):
    x: float | int | ShortText
    y: float | int | ShortText
    uncertainty: Annotated[float | int, Field(ge=0)] | None


class VisualSeries(StrictModel):
    id: Identifier
    label: ShortText
    x_axis_id: Identifier
    y_axis_id: Identifier
    points: list[VisualPoint] = Field(max_length=MAX_VISUAL_POINTS_PER_SERIES)


class TableCell(StrictModel):
    row: Annotated[int, Field(ge=1)]
    column: ShortText
    raw_value: RawValue
    numeric_value: float | int | None
    unit: ShortText | None

    @field_validator("raw_value")
    @classmethod
    def raw_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("table raw_value must not be whitespace only")
        return value


class VisualFeature(StrictModel):
    kind: Literal["trend", "peak", "trough", "plateau", "change_point", "uncertainty"]
    description: Statement
    series_id: Identifier | None
    value: float | int | None
    unit: ShortText | None


class StructuredVisualEvidence(StrictModel):
    id: Identifier
    anchor_id: Identifier
    visual_kind: Literal["figure", "table"]
    axes: list[VisualAxis] = Field(max_length=MAX_VISUAL_AXES)
    series: list[VisualSeries] = Field(max_length=MAX_VISUAL_SERIES)
    cells: list[TableCell] = Field(max_length=MAX_TABLE_CELLS)
    features: list[VisualFeature] = Field(max_length=MAX_VISUAL_FEATURES)

    @model_validator(mode="after")
    def valid_shape(self) -> StructuredVisualEvidence:
        axis_ids = {item.id for item in self.axes}
        if len(axis_ids) != len(self.axes):
            raise ValueError("visual axis IDs must be unique")
        if self.visual_kind == "figure":
            if not self.series or self.cells:
                raise ValueError("figure requires series and forbids table cells")
        elif not self.cells or self.axes or self.series:
            raise ValueError("table requires cells and forbids axes/series")
        series_ids = {item.id for item in self.series}
        if len(series_ids) != len(self.series):
            raise ValueError("visual series IDs must be unique")
        for series in self.series:
            if series.x_axis_id not in axis_ids or series.y_axis_id not in axis_ids:
                raise ValueError("visual series references an unknown axis")
            x = next(item for item in self.axes if item.id == series.x_axis_id)
            y = next(item for item in self.axes if item.id == series.y_axis_id)
            if x.direction != "x" or y.direction != "y":
                raise ValueError("series axis directions are invalid")
        for feature in self.features:
            if feature.series_id is not None and feature.series_id not in series_ids:
                raise ValueError("visual feature references unknown series")
        coordinates = {(cell.row, cell.column) for cell in self.cells}
        if len(coordinates) != len(self.cells):
            raise ValueError("table cell coordinates must be unique")
        return self


class Edge(StrictModel):
    id: Identifier
    source_id: Identifier
    target_id: Identifier
    relation: EdgeRelation
    rationale: ShortText | None = None
    confidence: Probability | None = None


class PaperEvidenceGraph(StrictModel):
    schema_version: Literal["1.0"]
    paper_id: Identifier
    claims: list[Claim]
    observations: list[Observation]
    reasoning: list[ReasoningStep]
    assumptions: list[Assumption]
    anchors: list[SourceAnchor]
    visual_evidence: list[StructuredVisualEvidence]
    scopes: list[Scope]
    edges: list[Edge]

    @model_validator(mode="after")
    def validate_graph(self) -> PaperEvidenceGraph:
        """Enforce graph structure with located, allowlisted contract issues.

        ``ContractValidationError`` is not a ``ValueError``, so Pydantic passes
        it through unchanged and a retry names the failing collection, index
        and field instead of a bare ``value_error`` at the root (GRF-8). Each
        phase reports all of its violations; later phases rely on unique IDs
        and resolved references, so they run only after earlier ones pass.
        """

        if self.claims and not self.anchors:
            raise ContractValidationError(
                ContractIssue.GRAPH_ANCHOR_REQUIRED, loc=("anchors",)
            )
        if not self.claims and any(
            (
                self.observations,
                self.reasoning,
                self.assumptions,
                self.visual_evidence,
                self.scopes,
                self.edges,
            )
        ):
            raise ContractValidationError(
                ContractIssue.GRAPH_ORPHAN_NODES, loc=("claims",)
            )
        _raise_violations(self._id_violations())
        _raise_violations(self._reference_violations())
        self._validate_edges()
        return self

    def _id_violations(self) -> list[ContractViolation]:
        violations: list[ContractViolation] = []
        seen: set[str] = set()
        for collection in _ID_COLLECTIONS:
            for index, item in enumerate(getattr(self, collection)):
                if item.id in seen:
                    violations.append(
                        ContractViolation(
                            ContractIssue.GRAPH_ID_DUPLICATE, (collection, index, "id")
                        )
                    )
                seen.add(item.id)
        return violations

    def _reference_violations(self) -> list[ContractViolation]:
        violations: list[ContractViolation] = []

        def add(issue: ContractIssue, *loc: str | int) -> None:
            violations.append(ContractViolation(issue, loc))

        scope_map = {item.id: item for item in self.scopes}
        anchors = {item.id for item in self.anchors}
        for index, claim in enumerate(self.claims):
            if (issue := _reference_issue(claim.anchor_ids, anchors)) is not None:
                add(issue, "claims", index, "anchor_ids")
            scope = scope_map.get(claim.scope_id)
            if scope is None:
                add(ContractIssue.GRAPH_REFERENCE_UNKNOWN, "claims", index, "scope_id")
                continue
            key = proposition_key(claim.statement)
            scoped = scoped_claim_id(key, scope)
            if claim.proposition_key not in {None, key}:
                add(
                    ContractIssue.GRAPH_IDENTITY_MISMATCH,
                    "claims",
                    index,
                    "proposition_key",
                )
            elif claim.scoped_claim_id not in {None, scoped}:
                add(
                    ContractIssue.GRAPH_IDENTITY_MISMATCH,
                    "claims",
                    index,
                    "scoped_claim_id",
                )
            object.__setattr__(claim, "proposition_key", key)
            object.__setattr__(claim, "scoped_claim_id", scoped)
        for index, item in enumerate(self.observations):
            if not item.anchor_ids:
                add(
                    ContractIssue.GRAPH_ANCHOR_REQUIRED,
                    "observations",
                    index,
                    "anchor_ids",
                )
            elif (issue := _reference_issue(item.anchor_ids, anchors)) is not None:
                add(issue, "observations", index, "anchor_ids")
            if item.scope_id is not None and item.scope_id not in scope_map:
                add(
                    ContractIssue.GRAPH_REFERENCE_UNKNOWN,
                    "observations",
                    index,
                    "scope_id",
                )
        for collection in ("reasoning", "assumptions"):
            for index, item in enumerate(getattr(self, collection)):
                if (issue := _reference_issue(item.anchor_ids, anchors)) is not None:
                    add(issue, collection, index, "anchor_ids")
        for index, item in enumerate(self.visual_evidence):
            if item.anchor_id not in anchors:
                add(
                    ContractIssue.GRAPH_REFERENCE_UNKNOWN,
                    "visual_evidence",
                    index,
                    "anchor_id",
                )
        return violations

    def validate_observation_path_ownership(self) -> None:
        """Require each observation to feed exactly one reasoning path."""

        premise_counts = {item.id: 0 for item in self.observations}
        for edge in self.edges:
            if edge.relation == "premise_for" and edge.source_id in premise_counts:
                premise_counts[edge.source_id] += 1
        invalid_index = next(
            (
                index
                for index, observation in enumerate(self.observations)
                if premise_counts[observation.id] != 1
            ),
            None,
        )
        if invalid_index is not None:
            raise ContractValidationError(
                ContractIssue.OBSERVATION_PATH_OWNERSHIP,
                loc=("observations", invalid_index),
            )

    def _validate_edges(self) -> None:
        node_types = {
            **{item.id: "anchor" for item in self.anchors},
            **{item.id: "visual" for item in self.visual_evidence},
            **{item.id: "observation" for item in self.observations},
            **{item.id: "reasoning" for item in self.reasoning},
            **{item.id: "assumption" for item in self.assumptions},
            **{item.id: "claim" for item in self.claims},
        }
        allowed = {
            "grounds": {("anchor", "observation"), ("visual", "observation")},
            "premise_for": {
                ("observation", "reasoning"),
                ("reasoning", "reasoning"),
            },
            "assumption_for": {("assumption", "reasoning")},
            "supports": {("reasoning", "claim"), ("claim", "claim")},
            "contradicts": {("reasoning", "claim"), ("claim", "claim")},
            "qualifies": {("reasoning", "claim"), ("claim", "claim")},
            "refines": {("claim", "claim")},
            "equivalent_to": {("claim", "claim")},
        }
        violations: list[ContractViolation] = []
        signatures: set[tuple[str, str, str]] = set()
        for index, edge in enumerate(self.edges):
            source = node_types.get(edge.source_id)
            target = node_types.get(edge.target_id)
            if source is None or target is None:
                field = "source_id" if source is None else "target_id"
                violations.append(
                    ContractViolation(
                        ContractIssue.GRAPH_REFERENCE_UNKNOWN, ("edges", index, field)
                    )
                )
            elif (source, target) not in allowed[edge.relation]:
                violations.append(
                    ContractViolation(
                        ContractIssue.GRAPH_EDGE_ENDPOINT_INVALID,
                        ("edges", index, "relation"),
                    )
                )
            else:
                signature = (edge.source_id, edge.target_id, edge.relation)
                if signature in signatures:
                    violations.append(
                        ContractViolation(
                            ContractIssue.GRAPH_EDGE_DUPLICATE, ("edges", index)
                        )
                    )
                signatures.add(signature)
        _raise_violations(violations)

        cycle_edge = _derivation_cycle_edge(
            [
                (index, edge.source_id, edge.target_id)
                for index, edge in enumerate(self.edges)
                if not (
                    node_types[edge.source_id] == "claim"
                    and node_types[edge.target_id] == "claim"
                )
            ]
        )
        if cycle_edge is not None:
            raise ContractValidationError(
                ContractIssue.GRAPH_DERIVATION_CYCLE, loc=("edges", cycle_edge)
            )

        # A reasoning step reaches a Claim when it concludes one or feeds a step
        # that does: one backward pass over reasoning -> reasoning premises.
        has_premise: set[str] = set()
        concluded_claims: set[str] = set()
        reaches_claim: set[str] = set()
        reasoning_predecessors: dict[str, list[str]] = {}
        for edge in self.edges:
            source = node_types[edge.source_id]
            target = node_types[edge.target_id]
            if edge.relation == "premise_for" and target == "reasoning":
                has_premise.add(edge.target_id)
                if source == "reasoning":
                    reasoning_predecessors.setdefault(edge.target_id, []).append(
                        edge.source_id
                    )
            if source == "reasoning" and target == "claim":
                concluded_claims.add(edge.target_id)
                reaches_claim.add(edge.source_id)
        pending = list(reaches_claim)
        while pending:
            for predecessor in reasoning_predecessors.get(pending.pop(), ()):
                if predecessor not in reaches_claim:
                    reaches_claim.add(predecessor)
                    pending.append(predecessor)
        for index, step in enumerate(self.reasoning):
            if step.id not in has_premise:
                violations.append(
                    ContractViolation(
                        ContractIssue.GRAPH_REASONING_PREMISE_MISSING,
                        ("reasoning", index),
                    )
                )
            elif step.id not in reaches_claim:
                violations.append(
                    ContractViolation(
                        ContractIssue.GRAPH_REASONING_UNCONCLUDED, ("reasoning", index)
                    )
                )
        for index, claim in enumerate(self.claims):
            if claim.id not in concluded_claims:
                violations.append(
                    ContractViolation(
                        ContractIssue.GRAPH_CLAIM_CONCLUSION_MISSING, ("claims", index)
                    )
                )
        _raise_violations(violations)


class EvidencePaths:
    """Raw-graph Evidence Paths with the backward adjacency built once.

    Mirrors the default TypeScript Evidence Path: a node's parents are its
    anchors and the sources of its incoming edges, except Claim -> Claim
    comparisons. Works on unvalidated (``model_construct``) graphs too.
    """

    def __init__(self, graph: PaperEvidenceGraph) -> None:
        claim_ids = {claim.id for claim in graph.claims}
        parents: dict[str, list[str]] = {}
        for collection in (
            graph.claims,
            graph.observations,
            graph.reasoning,
            graph.assumptions,
        ):
            for item in collection:
                parents.setdefault(item.id, []).extend(item.anchor_ids)
        for item in graph.visual_evidence:
            parents.setdefault(item.id, []).append(item.anchor_id)
        for edge in graph.edges:
            if edge.source_id in claim_ids and edge.target_id in claim_ids:
                continue
            parents.setdefault(edge.target_id, []).append(edge.source_id)
        self._parents = parents

    def closure(self, roots: Iterable[str]) -> set[str]:
        """Every node on the Evidence Path of any root, roots included."""

        reachable: set[str] = set()
        pending = list(roots)
        while pending:
            current = pending.pop()
            if current in reachable:
                continue
            reachable.add(current)
            pending.extend(self._parents.get(current, ()))
        return reachable


_ID_COLLECTIONS = (
    "claims",
    "observations",
    "reasoning",
    "assumptions",
    "anchors",
    "visual_evidence",
    "scopes",
    "edges",
)


def _raise_violations(violations: list[ContractViolation]) -> None:
    if violations:
        raise ContractValidationError.from_violations(tuple(violations))


def _reference_issue(values: list[str], allowed: set[str]) -> ContractIssue | None:
    if len(values) != len(set(values)):
        return ContractIssue.GRAPH_REFERENCE_DUPLICATE
    if not allowed.issuperset(values):
        return ContractIssue.GRAPH_REFERENCE_UNKNOWN
    return None


def _derivation_cycle_edge(edges: list[tuple[int, str, str]]) -> int | None:
    """Return the lowest index of an edge on a derivation cycle, if any.

    Iterative (Kahn) so arbitrarily deep model output cannot exhaust the
    interpreter stack. Nodes left with incoming edges after the topological
    pass all have a remaining predecessor, so walking predecessors from one of
    them must close a cycle.
    """

    indegree: dict[str, int] = {}
    outgoing: dict[str, list[str]] = {}
    for _, source, target in edges:
        indegree.setdefault(source, 0)
        indegree[target] = indegree.get(target, 0) + 1
        outgoing.setdefault(source, []).append(target)
    ready = [node for node, degree in indegree.items() if degree == 0]
    while ready:
        for target in outgoing.get(ready.pop(), ()):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    remaining = {node for node, degree in indegree.items() if degree}
    if not remaining:
        return None
    incoming: dict[str, list[tuple[int, str]]] = {}
    for index, source, target in edges:
        if source in remaining and target in remaining:
            incoming.setdefault(target, []).append((index, source))
    node = min(
        (index, target)
        for index, source, target in edges
        if source in remaining and target in remaining
    )[1]
    first_visit: dict[str, int] = {}
    taken: list[int] = []
    while node not in first_visit:
        first_visit[node] = len(taken)
        index, node_before = min(incoming[node])
        taken.append(index)
        node = node_before
    return min(taken[first_visit[node] :])


Verdict = Literal[
    "supported", "contradicted", "qualified", "uncertain", "insufficient_evidence"
]


class Assessment(StrictModel):
    id: Identifier
    claim_id: Identifier
    scoped_claim_id: Annotated[
        str, StringConstraints(pattern=r"^claim:sha256:[a-f0-9]{64}$")
    ]
    verdict: Verdict
    confidence: Probability
    evidence_ids: list[Identifier] = Field(
        description=(
            "Claims are forbidden. Only insufficient_evidence may use an empty "
            "list; every other verdict must cite an on-path SourceAnchor, "
            "StructuredVisualEvidence, or Observation."
        )
    )
    rationale: Statement
    limitations: list[ShortText]

    @model_validator(mode="after")
    def substantive_verdict_requires_evidence(self) -> Assessment:
        if self.verdict != "insufficient_evidence" and not self.evidence_ids:
            raise ValueError(
                "a verdict other than insufficient_evidence requires evidence_ids"
            )
        return self


class VerificationReport(StrictModel):
    schema_version: Literal["1.0"]
    paper_id: Identifier
    graph_sha256: Sha256
    verifier_model: Literal["gpt-6-luna", "gpt-5.6-luna", "deepseek-v4-flash"]
    assessments: list[Assessment]
    warnings: list[ShortText]

    def validate_against(self, graph: PaperEvidenceGraph) -> None:
        if self.paper_id != graph.paper_id:
            raise ContractValidationError(
                ContractIssue.VERIFICATION_PAPER_ID_MISMATCH,
                loc=("paper_id",),
            )
        claims = {claim.id: claim for claim in graph.claims}
        nodes = {
            item.id: item
            for collection in (
                graph.claims,
                graph.observations,
                graph.reasoning,
                graph.assumptions,
                graph.anchors,
                graph.visual_evidence,
            )
            for item in collection
        }
        seen: set[str] = set()
        assessed_claims: set[str] = set()
        evidence_paths = EvidencePaths(graph)
        for assessment_index, assessment in enumerate(self.assessments):
            if assessment.id in seen:
                raise ContractValidationError(
                    ContractIssue.VERIFICATION_ASSESSMENT_ID_DUPLICATE,
                    loc=("assessments", assessment_index, "id"),
                )
            seen.add(assessment.id)
            claim = claims.get(assessment.claim_id)
            if claim is None:
                raise ContractValidationError(
                    ContractIssue.VERIFICATION_CLAIM_UNKNOWN,
                    loc=("assessments", assessment_index, "claim_id"),
                )
            if assessment.claim_id in assessed_claims:
                raise ContractValidationError(
                    ContractIssue.VERIFICATION_CLAIM_ASSESSED_MULTIPLE_TIMES,
                    loc=("assessments", assessment_index, "claim_id"),
                )
            assessed_claims.add(assessment.claim_id)
            if assessment.scoped_claim_id != claim.scoped_claim_id:
                raise ContractValidationError(
                    ContractIssue.VERIFICATION_SCOPED_CLAIM_ID_MISMATCH,
                    loc=("assessments", assessment_index, "scoped_claim_id"),
                )
            if len(assessment.evidence_ids) != len(set(assessment.evidence_ids)):
                raise ContractValidationError(
                    ContractIssue.VERIFICATION_EVIDENCE_ID_DUPLICATE,
                    loc=("assessments", assessment_index, "evidence_ids"),
                )
            if set(assessment.evidence_ids) - set(nodes):
                raise ContractValidationError(
                    ContractIssue.VERIFICATION_EVIDENCE_ID_UNKNOWN,
                    loc=("assessments", assessment_index, "evidence_ids"),
                )
            evidence_nodes = [nodes[item] for item in assessment.evidence_ids]
            if any(isinstance(item, Claim) for item in evidence_nodes):
                # Claims are conclusions under review, never evidence for any
                # assessment. This also prevents the assessed Claim from donating
                # its own numbers to the verifier rationale.
                raise ContractValidationError(
                    ContractIssue.VERIFICATION_EVIDENCE_OUTSIDE_PATH,
                    loc=("assessments", assessment_index, "evidence_ids"),
                )
            path_nodes = evidence_paths.closure((assessment.claim_id,))
            off_path = set(assessment.evidence_ids) - path_nodes
            if off_path:
                raise ContractValidationError(
                    ContractIssue.VERIFICATION_EVIDENCE_OUTSIDE_PATH,
                    loc=("assessments", assessment_index, "evidence_ids"),
                )
            if assessment.verdict != "insufficient_evidence" and not any(
                isinstance(
                    item,
                    SourceAnchor | StructuredVisualEvidence | Observation,
                )
                for item in evidence_nodes
            ):
                # Reasoning and Assumptions can explain a path but cannot by
                # themselves establish a substantive verification verdict.
                raise ContractValidationError(
                    ContractIssue.VERIFICATION_EVIDENCE_OUTSIDE_PATH,
                    loc=("assessments", assessment_index, "evidence_ids"),
                )
            # Rationale is verifier commentary, not a scientific graph node.
            # Its wording must not invalidate an otherwise complete assessment:
            # retained Claims remain bound by their frozen Evidence Paths, while
            # rationale discrepancies are advisory text for review.
        missing = set(claims) - assessed_claims
        if missing:
            missing_index = next(
                index for index, claim in enumerate(graph.claims) if claim.id in missing
            )
            raise ContractValidationError(
                ContractIssue.VERIFICATION_CLAIM_UNASSESSED,
                loc=("assessments", missing_index, "claim_id"),
            )


__all__ = [
    "EvidencePaths",
    "PaperEvidenceGraph",
    "VerificationReport",
    "canonical_hash_json",
    "canonical_json",
    "normalized_text",
    "proposition_key",
    "scoped_claim_id",
    "validate_unique_scope_entities",
]
