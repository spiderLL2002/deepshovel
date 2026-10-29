"""Strict, bounded HTTP contracts for the claim retrieval projection.

Retrieval is a rebuildable projection over immutable evidence snapshots.  These
models deliberately carry references and ranking diagnostics, not evidence
truth or synthesized answers.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
import unicodedata
from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BeforeValidator,
    Field,
    JsonValue,
    RootModel,
    SerializerFunctionWrapHandler,
    StringConstraints,
    field_validator,
    model_serializer,
    model_validator,
)

from . import shared_contract
from .graph_contract import canonical_json
from .models import Identifier, Sha256, StrictModel
from .shared_contract import (
    MAX_CHECK_MANIFESTS,
    MAX_CONTEXT_SUPPLEMENTS,
    MAX_CONTEXT_TEXT_CHARACTERS,
    MAX_CONTEXT_TOTAL_BYTES,
    MAX_CONVERSATION_TURNS,
    MAX_INDEX_DOCUMENTS,
    MAX_QUERY_PLAN_HANDOFF_CHARACTERS,
    MAX_SEARCH_LIMIT,
    QUERY_PLAN_HANDOFF_SCHEMA_VERSION,
    ClaimModality,
    ClaimPolarity,
    ClaimType,
)

# Shared with the Web through contracts/shared-contract.json.
RETRIEVAL_SCHEMA_VERSION = shared_contract.RETRIEVAL_INDEX_VERSION
RETRIEVAL_SEARCH_SCHEMA_VERSION = shared_contract.RETRIEVAL_SEARCH_PROTOCOL_VERSION
RETRIEVAL_CONTEXT_SCHEMA_VERSION = shared_contract.RETRIEVAL_CONTEXT_PROTOCOL_VERSION
MAX_RETRIEVAL_BODY_BYTES = 8 * 1024 * 1024
MAX_ACTIVE_SNAPSHOTS = 4_096
MAX_CORPUS_SNAPSHOTS = 131_072
MAX_CLUSTER_MEMBERS = 20
MAX_WARNINGS = 32
MAX_CLAIM_RELATION_BATCH = 4_096

IndexVersion = Literal["evidence-retrieval/2.0"]
# The clarification-only protocol 2.2 (c1 tokens) was retired on 2026-10-19;
# the browser uses 2.3 and synthesis uses 2.0/2.1. A parity test keeps these
# Literals equal to the shared contract.
SearchSchemaVersion = Literal[
    "evidence-retrieval/2.0",
    "evidence-retrieval/2.1",
    "evidence-retrieval/2.3",
]
QueryType = Literal[
    "paper_lookup",
    "topic_search",
    "claim_search",
    "supporting_evidence",
    "contradiction_search",
]
RetrievalResultStatus = Literal[
    "matches", "no_direct_answer", "degraded", "clarification_required", "needs_context"
]
QueryRelation = Literal[
    "equivalent",
    "supports",
    "contradicts",
    "qualifies",
    "refines",
    "supplements",
    "related",
    "unrelated",
]
ScopeRelation = Literal[
    "equivalent",
    "overlap",
    "query_within_candidate",
    "candidate_within_query",
    "disjoint",
    "unknown",
]
RelationDirection = Literal["query_to_result", "result_to_query"]
GraphClaimRelation = Literal[
    "equivalent_to",
    "supports",
    "contradicts",
    "refines",
    "qualifies",
    "supplements",
]
GraphScopeOverlap = Literal[
    "partial", "source_within_target", "target_within_source", "equivalent"
]

BoundedText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8_000)
]
SearchText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8_000)
]
TermText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1_000)
]
QueryTermText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1_000)
]
WarningText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]
QueryPlanHandoffToken = Annotated[
    str,
    StringConstraints(
        strip_whitespace=False,
        min_length=64,
        max_length=MAX_QUERY_PLAN_HANDOFF_CHARACTERS,
        pattern=r"^v1\.[A-Za-z0-9_-]{1,32}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}$",
    ),
]
TimestampText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)
]
ExactClaimText = Annotated[str, StringConstraints(min_length=1, max_length=128_000)]
PathNodeLabel = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=280)
]
PathRelationText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
]


def _validate_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be ISO 8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a UTC offset")
    return value


class ScopeTime(StrictModel):
    label: TermText | None
    start: float | int | None
    end: float | int | None
    unit: TermText | None


class SearchScope(StrictModel):
    location: list[TermText]
    time: ScopeTime | None
    formation: list[TermText]
    sample_type: list[TermText]
    method: list[TermText]
    condition: list[TermText]
    measurement: list[TermText]
    uncertainty: TermText | None
    entities: list[TermText]


class SearchScopeFilterTime(StrictModel):
    label: QueryTermText | None
    start: float | int | None
    end: float | int | None
    unit: QueryTermText | None

    @model_validator(mode="after")
    def bounded_time(self) -> SearchScopeFilterTime:
        if self.label is None and self.start is None and self.end is None:
            raise ValueError("scope time requires a label or numeric bound")
        if (self.start is not None or self.end is not None) and self.unit is None:
            raise ValueError("numeric scope time requires a unit")
        if self.start is not None and self.end is not None and self.start > self.end:
            # "66–56 Ma" names the same interval old-to-young; order it instead
            # of failing every planner attempt on an unambiguous value.
            start, end = self.end, self.start
            object.__setattr__(self, "start", start)
            object.__setattr__(self, "end", end)
        return self


class SearchScopeFilter(StrictModel):
    location: Annotated[list[QueryTermText], Field(max_length=64)] | None = None
    time: SearchScopeFilterTime | None = None
    formation: Annotated[list[QueryTermText], Field(max_length=64)] | None = None
    sample_type: Annotated[list[QueryTermText], Field(max_length=64)] | None = None
    method: Annotated[list[QueryTermText], Field(max_length=64)] | None = None
    condition: Annotated[list[QueryTermText], Field(max_length=64)] | None = None
    measurement: Annotated[list[QueryTermText], Field(max_length=64)] | None = None
    uncertainty: QueryTermText | None = None
    entities: Annotated[list[QueryTermText], Field(max_length=64)] | None = None

    @field_validator(
        "location",
        "formation",
        "sample_type",
        "method",
        "condition",
        "measurement",
        "entities",
    )
    @classmethod
    def unique_values(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        keys = [unicodedata.normalize("NFKC", item).lower() for item in values]
        if len(keys) != len(set(keys)):
            raise ValueError("scope filter values must be unique")
        return values

    @model_serializer(mode="wrap")
    def serialize_scope_filter(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, object]:
        data = handler(self)
        for name in (
            "location",
            "formation",
            "sample_type",
            "method",
            "condition",
            "measurement",
            "entities",
        ):
            if data.get(name) is None:
                data.pop(name, None)
        for name in ("time", "uncertainty"):
            if name not in self.model_fields_set and data.get(name) is None:
                data.pop(name, None)
        return data


def _scope_filter_is_meaningful(scope: SearchScopeFilter) -> bool:
    arrays = (
        scope.location,
        scope.formation,
        scope.sample_type,
        scope.method,
        scope.condition,
        scope.measurement,
        scope.entities,
    )
    return bool(
        any(value for value in arrays)
        or scope.time is not None
        or scope.uncertainty is not None
    )


class SearchRegion(StrictModel):
    x: Annotated[float | int, Field(ge=0)]
    y: Annotated[float | int, Field(ge=0)]
    width: Annotated[float | int, Field(gt=0)]
    height: Annotated[float | int, Field(gt=0)]
    unit: Literal["normalized", "pixel"]


class SearchEvidenceAnchor(StrictModel):
    anchor_id: Identifier
    artifact_version_id: Identifier
    anchor_type: Literal["text", "figure", "table", "dataset", "metadata", "citation"]
    page: Annotated[int, Field(ge=1)] | None
    section: TermText | None
    paragraph: Annotated[int, Field(ge=1)] | None
    quote: ExactClaimText | None
    figure: TermText | None
    table: TermText | None
    row_start: Annotated[int, Field(ge=1)] | None
    row_end: Annotated[int, Field(ge=1)] | None
    row_range_label: TermText | None
    column_names: list[TermText]
    series: TermText | None
    char_start: Annotated[int, Field(ge=0)] | None
    char_end: Annotated[int, Field(ge=0)] | None
    region: SearchRegion | None
    region_label: TermText | None
    dataset_path: (
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000),
        ]
        | None
    )


class EvidencePathNode(StrictModel):
    id: Identifier
    kind: TermText
    label: PathNodeLabel


class EvidencePathLink(StrictModel):
    from_id: Identifier
    to_id: Identifier
    relation: PathRelationText
    link_type: Literal["edge", "provenance"]
    confidence: Annotated[float | int, Field(ge=0, le=1)] | None


class ShortestEvidencePath(StrictModel):
    root_claim_id: Identifier
    nodes: list[EvidencePathNode]
    links: list[EvidencePathLink]
    depth: Annotated[int, Field(ge=0)]
    mean_edge_confidence: Annotated[float | int, Field(ge=0, le=1)] | None


class SearchAbbreviation(StrictModel):
    short_form: TermText
    long_form: TermText | None


class ExplicitClaimRelation(StrictModel):
    edge_id: Identifier
    source_claim_id: Identifier
    target_claim_id: Identifier
    other_claim_id: Identifier
    relation: GraphClaimRelation
    direction: Literal["outgoing", "incoming"]
    confidence: Annotated[float | int, Field(ge=0, le=1)] | None
    scope_overlap: GraphScopeOverlap


class ClaimSearchDocument(StrictModel):
    index_version: IndexVersion
    snapshot_id: Identifier
    paper_id: Identifier
    paper_family_id: Identifier
    root_artifact_version_id: Identifier
    claim_id: Identifier
    source_claim_id: Identifier | None
    original_claim: ExactClaimText | None
    canonical_claim: BoundedText
    proposition_key: Annotated[
        str, StringConstraints(pattern=r"^proposition:sha256:[a-f0-9]{64}$")
    ]
    scoped_claim_id: Annotated[
        str, StringConstraints(pattern=r"^claim:sha256:[a-f0-9]{64}$")
    ]
    paper_title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1_000)
    ]
    evidence_context: Annotated[str, StringConstraints(max_length=16_000)]
    entities: list[TermText]
    key_terms: Annotated[list[TermText], Field(max_length=256)]
    abbreviations: Annotated[list[SearchAbbreviation], Field(max_length=32)]
    qualifiers: list[TermText]
    claim_type: ClaimType
    polarity: ClaimPolarity
    modality: ClaimModality
    attribution: Literal["authors_conclusion"]
    rhetorical_role: Literal["terminal_conclusion"]
    scope: SearchScope
    evidence_anchors: list[SearchEvidenceAnchor]
    shortest_evidence_path: ShortestEvidencePath
    path_quality: Annotated[float | int, Field(ge=0, le=1)]
    extractor_prompt_version: TermText
    explicit_relations: list[ExplicitClaimRelation] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_explicit_relations(self) -> ClaimSearchDocument:
        edge_ids: set[str] = set()
        for item in self.explicit_relations:
            if item.edge_id in edge_ids:
                raise ValueError("explicit relation edge_id values must be unique")
            edge_ids.add(item.edge_id)
            if item.direction == "outgoing":
                valid = (
                    item.source_claim_id == self.claim_id
                    and item.target_claim_id == item.other_claim_id
                )
            else:
                valid = (
                    item.target_claim_id == self.claim_id
                    and item.source_claim_id == item.other_claim_id
                )
            if not valid:
                raise ValueError("explicit relation direction does not match its claim")
        return self


class RetrievalManifest(StrictModel):
    index_version: IndexVersion
    snapshot_id: Identifier
    paper_id: Identifier
    paper_family_id: Identifier
    root_artifact_version_id: Identifier
    root_artifact_sha256: Sha256
    graph_sha256: Sha256
    graph_schema_version: Literal["scientific-evidence-graph/1.0"]
    extractor_prompt_version: TermText
    # This is the immutable snapshot total, not a per-request transport bound.
    # IndexSnapshotRequest.documents is chunked independently below.
    document_count: Annotated[int, Field(ge=0)]
    created_at: TimestampText

    _timestamp = field_validator("created_at")(_validate_timestamp)


class ManifestCheckRequest(StrictModel):
    schema_version: IndexVersion
    project_id: Identifier
    manifests: Annotated[
        list[RetrievalManifest], Field(min_length=1, max_length=MAX_CHECK_MANIFESTS)
    ]

    @field_validator("manifests")
    @classmethod
    def unique_snapshots(
        cls, values: list[RetrievalManifest]
    ) -> list[RetrievalManifest]:
        ids = [item.snapshot_id for item in values]
        if len(ids) != len(set(ids)):
            raise ValueError("manifest snapshot_id values must be unique")
        return values


class IndexSnapshotRequest(StrictModel):
    schema_version: IndexVersion
    project_id: Identifier
    manifest: RetrievalManifest
    documents: Annotated[
        list[ClaimSearchDocument], Field(max_length=MAX_INDEX_DOCUMENTS)
    ]

    @model_validator(mode="after")
    def consistent_projection(self) -> IndexSnapshotRequest:
        manifest = self.manifest
        if len(self.documents) > manifest.document_count:
            raise ValueError("index chunk exceeds the immutable manifest total")
        claim_ids: set[str] = set()
        for item in self.documents:
            if item.claim_id in claim_ids:
                raise ValueError("document claim_id values must be unique")
            claim_ids.add(item.claim_id)
            expected = (
                item.index_version == manifest.index_version
                and item.snapshot_id == manifest.snapshot_id
                and item.paper_id == manifest.paper_id
                and item.paper_family_id == manifest.paper_family_id
                and item.root_artifact_version_id == manifest.root_artifact_version_id
                and item.extractor_prompt_version == manifest.extractor_prompt_version
            )
            if not expected:
                raise ValueError("document identity does not match its manifest")
        return self


class DeleteSnapshotsRequest(StrictModel):
    schema_version: IndexVersion
    index_version: IndexVersion
    project_id: Identifier
    snapshot_ids: Annotated[
        list[Identifier], Field(min_length=1, max_length=MAX_ACTIVE_SNAPSHOTS)
    ]

    @field_validator("snapshot_ids")
    @classmethod
    def unique_snapshot_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("snapshot_ids must be unique")
        return values


class RetrievalOpenAlexScope(StrictModel):
    level: Literal["domain", "field", "subfield", "topic"]
    id: Annotated[str, StringConstraints(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def matching_taxonomy_identity(self) -> RetrievalOpenAlexScope:
        suffix = {
            "domain": r"domains/[0-9]+",
            "field": r"fields/[0-9]+",
            "subfield": r"subfields/[0-9]+",
            "topic": r"T[0-9]+",
        }[self.level]
        if re.fullmatch(r"https://openalex\.org/" + suffix, self.id) is None:
            raise ValueError("OpenAlex scope must identify a node at its stated level")
        return self


def _decode_active_snapshot_ids(value: object) -> object:
    """Decode a bounded wire-only SHA-256 corpus without changing internal lists."""
    if not isinstance(value, str):
        return value
    prefix = "sha256-v1:"
    maximum = (MAX_CORPUS_SNAPSHOTS * 32 * 4 + 2) // 3
    if not value.startswith(prefix) or len(value) > len(prefix) + maximum:
        raise ValueError("invalid or oversized snapshot corpus codec")
    encoded = value[len(prefix) :]
    if not encoded or re.fullmatch(r"[A-Za-z0-9_-]+", encoded) is None:
        raise ValueError("snapshot corpus must use unpadded base64url")
    try:
        decoded = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
        )
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid snapshot corpus encoding") from exc
    if (
        not decoded
        or len(decoded) % 32
        or len(decoded) > MAX_CORPUS_SNAPSHOTS * 32
        or base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != encoded
    ):
        raise ValueError("snapshot corpus is not a canonical SHA-256 sequence")
    return [
        "snapshot:sha256:" + decoded[offset : offset + 32].hex()
        for offset in range(0, len(decoded), 32)
    ]


class SearchFilters(StrictModel):
    corpus: Literal["same_paper", "cross_paper"]
    paper_ids: Annotated[list[Identifier], Field(max_length=32)]
    paper_family_ids: Annotated[list[Identifier], Field(max_length=32)]
    active_snapshot_ids: Annotated[
        Annotated[list[Identifier], Field(max_length=MAX_CORPUS_SNAPSHOTS)] | None,
        BeforeValidator(_decode_active_snapshot_ids),
    ]
    limit: Annotated[int, Field(ge=1, le=MAX_SEARCH_LIMIT)]
    # Gateway-resolved taxonomy constraint, retained for exact request binding.
    # active_snapshot_ids remains the Compiler's concrete corpus filter.
    openalex_scope: RetrievalOpenAlexScope | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @field_validator("paper_ids", "paper_family_ids", "active_snapshot_ids")
    @classmethod
    def unique_snapshot_ids(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        if len(values) != len(set(values)):
            raise ValueError("search filter IDs must be unique")
        return values


class QueryClaimRef(StrictModel):
    snapshot_id: Identifier
    claim_id: Identifier


ClarificationToken = Annotated[str, StringConstraints(min_length=1, max_length=700_000)]
ClarificationOptionId = Annotated[
    str, StringConstraints(min_length=1, max_length=32, pattern=r"^[a-zA-Z0-9_-]+$")
]


class ClarificationOption(StrictModel):
    option_id: ClarificationOptionId
    label: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]
    resolved_query: SearchText

    @field_validator("resolved_query")
    @classmethod
    def nfc_resolved_query(cls, value: str) -> str:
        if value != unicodedata.normalize("NFC", value):
            raise ValueError("resolved_query must be NFC normalized")
        return value


class ClarificationPrompt(StrictModel):
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1_000)
    ]
    options: Annotated[list[ClarificationOption], Field(min_length=2, max_length=4)]

    @model_validator(mode="after")
    def unique_options(self) -> ClarificationPrompt:
        if len({item.option_id for item in self.options}) != len(self.options):
            raise ValueError("clarification option IDs must be unique")
        if len({item.resolved_query for item in self.options}) != len(self.options):
            raise ValueError("clarification queries must be unique")
        return self


class ClarificationSelection(StrictModel):
    clarification_token: ClarificationToken
    option_id: ClarificationOptionId


class ClarificationRequired(ClarificationPrompt):
    clarification_token: ClarificationToken


def _exact_context_text(value: str) -> str:
    if not value.strip():
        raise ValueError("context text must contain non-whitespace characters")
    if value != unicodedata.normalize("NFC", value):
        raise ValueError("context text must be NFC normalized")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("context text must be valid Unicode") from exc
    return value


ContextText = Annotated[
    str,
    StringConstraints(min_length=1, max_length=MAX_CONTEXT_TEXT_CHARACTERS),
    AfterValidator(_exact_context_text),
]
ContextToken = Annotated[
    str,
    StringConstraints(
        min_length=64,
        max_length=700_000,
        pattern=r"^n1\.derived-auth-v1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}$",
    ),
]


class ConversationTurn(StrictModel):
    role: Literal["user", "assistant"]
    content: ContextText


class ContextSubmission(StrictModel):
    context_token: ContextToken
    content: ContextText


class RetrievalContextState(StrictModel):
    supplements: Annotated[
        list[ContextText], Field(max_length=MAX_CONTEXT_SUPPLEMENTS)
    ] = Field(default_factory=list)
    selected_query: ContextText | None = None

    @model_validator(mode="after")
    def bounded_supplements(self) -> RetrievalContextState:
        if (
            sum(len(item.encode("utf-8")) for item in self.supplements)
            > MAX_CONTEXT_TOTAL_BYTES
        ):
            raise ValueError("context supplements exceed the UTF-8 byte budget")
        return self


class NeedsContextPrompt(StrictModel):
    question: Annotated[
        str,
        StringConstraints(min_length=1, max_length=1_000),
        AfterValidator(_exact_context_text),
    ]


class NeedsContextRequired(NeedsContextPrompt):
    context_token: ContextToken


class RetrievalArgumentNode(StrictModel):
    id: Identifier
    kind: TermText
    statement: Annotated[str, Field(max_length=8000)]
    scope: dict[str, JsonValue] | None
    anchor_ids: list[Identifier]


class RetrievalArgumentEdge(StrictModel):
    source_id: Identifier
    target_id: Identifier
    relation: TermText


class RetrievalArgumentContext(StrictModel):
    snapshot_id: Identifier
    claim_id: Identifier
    nodes: Annotated[list[RetrievalArgumentNode], Field(max_length=256)]
    edges: Annotated[list[RetrievalArgumentEdge], Field(max_length=512)]
    truncated: bool

    @model_validator(mode="after")
    def valid_dependencies(self):
        ids = {node.id for node in self.nodes}
        if len(ids) != len(self.nodes):
            raise ValueError("argument nodes must have unique ids")
        if any(edge.source_id not in ids or edge.target_id not in ids for edge in self.edges):
            raise ValueError("argument edge endpoints must be retained")
        if not self.truncated and self.claim_id not in ids:
            raise ValueError("complete argument must contain its claim")
        return self


class HybridSearchRequest(StrictModel):
    argument_contexts: Annotated[list[RetrievalArgumentContext], Field(max_length=1024)] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )
    schema_version: SearchSchemaVersion
    project_id: Identifier
    stage: Literal["preview", "ai_rerank"]
    query: SearchText
    query_claim: QueryClaimRef | None
    candidate_refs: Annotated[list[QueryClaimRef], Field(max_length=30)]
    intent: Literal["claim_search", "supporting_evidence", "contradiction_search"]
    must_terms: Annotated[list[QueryTermText], Field(max_length=64)]
    exclude_terms: Annotated[list[QueryTermText], Field(max_length=64)]
    scope: SearchScopeFilter | None
    claim_types: Annotated[list[ClaimType], Field(max_length=7)]
    polarity: ClaimPolarity | None
    filters: SearchFilters
    query_plan_handoff: QueryPlanHandoffToken | None = None
    clarification_selection: ClarificationSelection | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    conversation_history: Annotated[
        list[ConversationTurn], Field(max_length=MAX_CONVERSATION_TURNS)
    ] = Field(default_factory=list, exclude_if=lambda value: not value)
    context_submission: ContextSubmission | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @field_validator("query")
    @classmethod
    def nfc_query(cls, value: str) -> str:
        if value != unicodedata.normalize("NFC", value):
            raise ValueError("query must be NFC normalized")
        return value

    @model_validator(mode="after")
    def explicit_constraints_are_consistent(self) -> HybridSearchRequest:
        if self.argument_contexts:
            if self.stage != "ai_rerank":
                raise ValueError("argument context is only accepted for final comparison")
            permitted = {(ref.snapshot_id, ref.claim_id) for ref in self.candidate_refs}
            if self.query_claim is not None:
                permitted.add((self.query_claim.snapshot_id, self.query_claim.claim_id))
            keys = [(item.snapshot_id, item.claim_id) for item in self.argument_contexts]
            if len(set(keys)) != len(keys) or any(
                key not in permitted
                and (self.query_claim is None or key[0] != self.query_claim.snapshot_id)
                for key in keys
            ):
                raise ValueError("argument contexts must match distinct requested claims")
        if (
            self.schema_version != RETRIEVAL_CONTEXT_SCHEMA_VERSION
            and self.clarification_selection is not None
        ):
            raise ValueError("clarification selection requires search protocol 2.3")
        if self.schema_version != RETRIEVAL_CONTEXT_SCHEMA_VERSION:
            if {"conversation_history", "context_submission"} & self.model_fields_set:
                raise ValueError("conversation context requires search protocol 2.3")
            if "openalex_scope" in self.filters.model_fields_set:
                raise ValueError("OpenAlex scope binding requires search protocol 2.3")
        else:
            _exact_context_text(self.query)
            if (
                self.context_submission is not None
                and self.clarification_selection is not None
            ):
                raise ValueError(
                    "context submission and clarification selection are exclusive"
                )
        if (
            sum(len(item.content.encode("utf-8")) for item in self.conversation_history)
            > MAX_CONTEXT_TOTAL_BYTES
        ):
            raise ValueError("conversation history exceeds the UTF-8 byte budget")
        must = [unicodedata.normalize("NFKC", item).lower() for item in self.must_terms]
        excluded = [
            unicodedata.normalize("NFKC", item).lower() for item in self.exclude_terms
        ]
        if len(must) != len(set(must)) or len(excluded) != len(set(excluded)):
            raise ValueError("query terms must be unique")
        if set(must).intersection(excluded):
            raise ValueError("must_terms and exclude_terms must not overlap")
        if len(self.claim_types) != len(set(self.claim_types)):
            raise ValueError("claim_types must be unique")
        candidate_keys = [
            (item.snapshot_id, item.claim_id) for item in self.candidate_refs
        ]
        if len(candidate_keys) != len(set(candidate_keys)):
            raise ValueError("candidate_refs must be unique")
        if self.stage == "preview" and self.candidate_refs:
            raise ValueError("preview requests cannot provide AI candidates")
        if self.stage == "preview" and self.query_plan_handoff is not None:
            raise ValueError("preview requests cannot provide a query plan handoff")
        if self.schema_version == RETRIEVAL_SCHEMA_VERSION:
            if self.query_plan_handoff is not None:
                raise ValueError(
                    "legacy search requests cannot provide a query plan handoff"
                )
        elif self.stage == "ai_rerank" and self.query_plan_handoff is None:
            raise ValueError(
                "search protocol 2.1 final requests require a query plan handoff"
            )
        if self.scope is not None and not _scope_filter_is_meaningful(self.scope):
            raise ValueError("search scope must contain an effective constraint")
        if (
            self.filters.corpus == "same_paper"
            and self.query_claim is None
            and len(self.filters.paper_ids) + len(self.filters.paper_family_ids) != 1
        ):
            raise ValueError(
                "same_paper text search requires exactly one paper or family selector"
            )
        return self


class ManifestProjectionStatus(StrictModel):
    snapshot_id: Identifier
    document_count: Annotated[int, Field(ge=0)]
    indexed_document_count: Annotated[int, Field(ge=0)]
    manifest_sha256: Sha256 | None
    dense_complete: bool

    @model_validator(mode="after")
    def valid_counts(self) -> ManifestProjectionStatus:
        if self.indexed_document_count > self.document_count:
            raise ValueError("indexed count must not exceed manifest document_count")
        if self.dense_complete and self.indexed_document_count != self.document_count:
            raise ValueError(
                "dense completeness requires a complete document projection"
            )
        return self


class ManifestCheckStatus(ManifestProjectionStatus):
    status: Literal["indexed", "missing", "stale", "degraded"]

    @model_validator(mode="after")
    def status_semantics(self) -> ManifestCheckStatus:
        if self.status == "missing":
            if (
                self.manifest_sha256 is not None
                or self.indexed_document_count != 0
                or self.dense_complete
            ):
                raise ValueError(
                    "missing manifests cannot report stored projection state"
                )
        elif self.manifest_sha256 is None:
            raise ValueError("stored manifest status requires manifest_sha256")
        if self.status == "indexed" and (
            self.indexed_document_count != self.document_count
            or not self.dense_complete
        ):
            raise ValueError("indexed status requires a complete dense projection")
        if self.status == "degraded" and self.dense_complete:
            raise ValueError("degraded status cannot report dense completeness")
        return self


class IndexManifestStatus(ManifestProjectionStatus):
    status: Literal["indexed", "unchanged", "degraded"]

    @model_validator(mode="after")
    def status_semantics(self) -> IndexManifestStatus:
        if self.manifest_sha256 is None:
            raise ValueError("index response status requires manifest_sha256")
        if self.status in {"indexed", "unchanged"} and (
            self.indexed_document_count != self.document_count
            or not self.dense_complete
        ):
            raise ValueError(
                "indexed and unchanged require a complete dense projection"
            )
        if self.status == "degraded" and self.dense_complete:
            raise ValueError("degraded status cannot report dense completeness")
        return self


class ManifestCheckResponse(StrictModel):
    schema_version: IndexVersion = RETRIEVAL_SCHEMA_VERSION
    manifests: Annotated[
        list[ManifestCheckStatus], Field(max_length=MAX_CHECK_MANIFESTS)
    ]


class IndexSnapshotResponse(StrictModel):
    schema_version: IndexVersion = RETRIEVAL_SCHEMA_VERSION
    manifests: Annotated[list[IndexManifestStatus], Field(min_length=1, max_length=1)]
    indexed_documents: Annotated[int, Field(ge=0, le=MAX_INDEX_DOCUMENTS)]
    warnings: Annotated[list[WarningText], Field(max_length=MAX_WARNINGS)]


class DeleteSnapshotsResponse(StrictModel):
    schema_version: IndexVersion = RETRIEVAL_SCHEMA_VERSION
    deleted_snapshots: Annotated[int, Field(ge=0)]
    deleted_documents: Annotated[int, Field(ge=0)]
    warnings: Annotated[list[WarningText], Field(max_length=MAX_WARNINGS)]


class QueryPlanPayload(StrictModel):
    query_type: QueryType
    canonical_claim: SearchText
    must_terms: Annotated[list[QueryTermText], Field(max_length=64)]
    optional_terms: Annotated[list[QueryTermText], Field(max_length=64)]
    exclude_terms: Annotated[list[QueryTermText], Field(max_length=64)]
    entities: Annotated[list[QueryTermText], Field(max_length=64)]
    polarity: ClaimPolarity | None
    claim_types: Annotated[list[ClaimType], Field(max_length=7)]
    scope_constraints: SearchScopeFilter | None


class QueryPlan(QueryPlanPayload):
    original_query: SearchText
    planner: Literal["gpt", "deterministic"]
    resolved_query: SearchText | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @field_validator("resolved_query")
    @classmethod
    def nfc_resolved_query(cls, value: str | None) -> str | None:
        if value is not None and value != unicodedata.normalize("NFC", value):
            raise ValueError("resolved_query must be NFC normalized")
        return value

    @model_validator(mode="after")
    def meaningful_scope(self) -> QueryPlan:
        scope = self.scope_constraints
        if scope is not None and not _scope_filter_is_meaningful(scope):
            raise ValueError("query plan scope_constraints must be meaningful")
        return self


class ClarifyPlanningOutput(StrictModel):
    action: Literal["clarify"]
    clarification: ClarificationPrompt


class ContextRetrievePlanningOutput(StrictModel):
    action: Literal["retrieve"]
    # This is a model-derived retrieval task, so use the same canonical text
    # boundary as QueryPlan and signed handoff decoding. Exact caller history
    # and supplements continue to use ContextText without whitespace repair.
    resolved_query: Annotated[SearchText, AfterValidator(_exact_context_text)]
    query_plan: QueryPlanPayload


class NeedsContextPlanningOutput(StrictModel):
    action: Literal["needs_context"]
    needs_context: NeedsContextPrompt


class ContextQueryPlanningOutput(
    RootModel[
        Annotated[
            ContextRetrievePlanningOutput
            | ClarifyPlanningOutput
            | NeedsContextPlanningOutput,
            Field(discriminator="action"),
        ]
    ]
):
    """Strict three-branch wire output; only the active payload may occur."""


class ContextTokenPayload(StrictModel):
    schema_version: Literal["deepshovel-context/1.0"] = "deepshovel-context/1.0"
    audience: Literal["/v1/retrieval/search:context"] = "/v1/retrieval/search:context"
    issued_at: Annotated[int, Field(ge=0)]
    expires_at: Annotated[int, Field(ge=0)]
    request_binding_sha256: Sha256
    configuration_binding_sha256: Sha256
    context: RetrievalContextState

    @model_validator(mode="after")
    def bounded_window(self) -> ContextTokenPayload:
        if not 0 < self.expires_at - self.issued_at <= 30 * 60:
            raise ValueError(
                "context token requires a positive window of at most 30 minutes"
            )
        return self


class ContextClarificationTokenPayload(StrictModel):
    schema_version: Literal["deepshovel-context-clarification/1.0"] = (
        "deepshovel-context-clarification/1.0"
    )
    audience: Literal["/v1/retrieval/search:context-clarification"] = (
        "/v1/retrieval/search:context-clarification"
    )
    issued_at: Annotated[int, Field(ge=0)]
    expires_at: Annotated[int, Field(ge=0)]
    request_binding_sha256: Sha256
    configuration_binding_sha256: Sha256
    context: RetrievalContextState
    clarification: ClarificationPrompt

    @model_validator(mode="after")
    def bounded_window_and_selection(self) -> ContextClarificationTokenPayload:
        if not 0 < self.expires_at - self.issued_at <= 30 * 60:
            raise ValueError(
                "context choice token requires a positive window of at most 30 minutes"
            )
        if self.context.selected_query is not None:
            raise ValueError("a selected context cannot ask for another choice")
        return self


class StageDiagnostic(StrictModel):
    attempted: bool
    status: Literal["ok", "degraded", "skipped"]
    provider: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]
    returned: Annotated[int, Field(ge=0, le=500)]


class ChannelDiagnostics(StrictModel):
    lexical: StageDiagnostic
    dense: StageDiagnostic
    rerank: StageDiagnostic
    relation: StageDiagnostic


StrongRetrievalSignal = Literal[
    "exact_anchor",
    "concept_anchor",
    "scientific_anchor",
]
RetrievalPolicyInapplicableReason = Literal[
    "explicit_constraints",
    "query_claim",
    "restricted_corpus",
    "no_active_snapshots",
    "lexical_channel_unavailable",
    "dense_channel_unavailable",
    "invalid_dense_scores",
]


class RetrievalConfidence(StrictModel):
    """Bounded observations for later abstention-policy calibration.

    This is deliberately not a probability or a live policy decision.  The
    serving path always returns its existing preview while calibration data is
    collected against judged retrieval cases.
    """

    mode: Literal["observe"] = "observe"
    decision: Literal["return"] = "return"
    would_abstain: None = None
    applicability: Literal["applicable", "policy_inapplicable"]
    policy_inapplicable_reasons: Annotated[
        list[RetrievalPolicyInapplicableReason], Field(max_length=8)
    ]
    query_profile: Literal["topic", "claim"]
    long_query_truncated: bool
    direct_lexical_query_tokens: Annotated[int, Field(ge=0, le=256)]
    direct_lexical_overlap: Annotated[int, Field(ge=0, le=256)]
    direct_lexical_coverage: Annotated[float, Field(ge=0, le=1)]
    exact_anchor_hits: Annotated[int, Field(ge=0, le=8)]
    concept_anchor_hits: Annotated[int, Field(ge=0, le=8_192)]
    scientific_anchor_hits: Annotated[int, Field(ge=0, le=8_192)]
    strong_signal_types: Annotated[list[StrongRetrievalSignal], Field(max_length=3)]
    dense_observed_hits: Annotated[int, Field(ge=0, le=3)]
    dense_top1: Annotated[float, Field(ge=-2, le=2)] | None
    dense_top2: Annotated[float, Field(ge=-2, le=2)] | None
    dense_margin: Annotated[float, Field(ge=0, le=4)] | None
    dense_top3_mean: Annotated[float, Field(ge=-2, le=2)] | None

    @model_validator(mode="after")
    def consistent_observation(self) -> RetrievalConfidence:
        reasons = self.policy_inapplicable_reasons
        if len(reasons) != len(set(reasons)):
            raise ValueError("policy_inapplicable_reasons must be unique")
        if self.applicability == "applicable" and reasons:
            raise ValueError("applicable observations cannot have policy reasons")
        if self.applicability == "policy_inapplicable" and not reasons:
            raise ValueError("policy_inapplicable observations require a reason")
        if self.direct_lexical_overlap > self.direct_lexical_query_tokens:
            raise ValueError("direct lexical overlap cannot exceed query tokens")
        expected_coverage = (
            0.0
            if self.direct_lexical_query_tokens == 0
            else self.direct_lexical_overlap / self.direct_lexical_query_tokens
        )
        if not math.isclose(
            self.direct_lexical_coverage,
            expected_coverage,
            rel_tol=1e-9,
            abs_tol=1e-12,
        ):
            raise ValueError("direct lexical coverage does not match token counts")
        if len(self.strong_signal_types) != len(set(self.strong_signal_types)):
            raise ValueError("strong_signal_types must be unique")
        expected_signals = {
            name
            for name, count in (
                ("exact_anchor", self.exact_anchor_hits),
                ("concept_anchor", self.concept_anchor_hits),
                ("scientific_anchor", self.scientific_anchor_hits),
            )
            if count > 0
        }
        if set(self.strong_signal_types) != expected_signals:
            raise ValueError("strong_signal_types do not match anchor hit counts")

        dense_values = (self.dense_top1, self.dense_top2, self.dense_margin)
        if self.dense_observed_hits == 0:
            if any(value is not None for value in dense_values) or (
                self.dense_top3_mean is not None
            ):
                raise ValueError("empty dense observations cannot report scores")
        else:
            if self.dense_top1 is None or self.dense_top3_mean is None:
                raise ValueError("dense observations require top1 and top3 mean")
            if self.dense_observed_hits == 1:
                if self.dense_top2 is not None or self.dense_margin is not None:
                    raise ValueError("one dense observation cannot report a margin")
            elif self.dense_top2 is None or self.dense_margin is None:
                raise ValueError("two dense observations require top2 and margin")
        return self


class RetrievalScores(StrictModel):
    lexical: float | None
    dense: float | None
    rrf: Annotated[float, Field(ge=0)]
    rerank: float | None


class PreviewMemberRef(StrictModel):
    claim_id: Identifier
    snapshot_id: Identifier
    paper_id: Identifier
    paper_family_id: Identifier
    scores: RetrievalScores


class QueryPlanHandoffPayload(StrictModel):
    schema_version: Literal["deepshovel-query-plan-handoff/1.0"] = (
        QUERY_PLAN_HANDOFF_SCHEMA_VERSION
    )
    issuer: Literal["deepshovel-compiler"] = "deepshovel-compiler"
    audience: Literal["/v1/retrieval/search:ai_rerank"] = (
        "/v1/retrieval/search:ai_rerank"
    )
    key_id: Literal["derived-auth-v1"] = "derived-auth-v1"
    issued_at: Annotated[int, Field(ge=0)]
    expires_at: Annotated[int, Field(ge=0)]
    retrieval_schema_version: Literal[
        "evidence-retrieval/2.1", "evidence-retrieval/2.3"
    ] = RETRIEVAL_SEARCH_SCHEMA_VERSION
    request_binding_sha256: Sha256
    corpus_binding_sha256: Sha256
    configuration_binding_sha256: Sha256
    query_plan: QueryPlan
    ordered_candidates: Annotated[list[PreviewMemberRef], Field(max_length=30)]
    context_state: RetrievalContextState | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    task_context_sha256: Sha256 | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    frozen_task_reference: FrozenCompiledContextTaskReference | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def protocol_context_binding(self) -> QueryPlanHandoffPayload:
        if self.retrieval_schema_version == RETRIEVAL_CONTEXT_SCHEMA_VERSION:
            if (
                self.context_state is None
                or self.task_context_sha256 is None
                or self.frozen_task_reference is None
            ):
                raise ValueError("2.3 handoff requires context state and task binding")
        elif {
            "context_state",
            "task_context_sha256",
            "frozen_task_reference",
        } & self.model_fields_set:
            raise ValueError("legacy handoff forbids context binding fields")
        return self

    @model_validator(mode="after")
    def valid_window_and_candidates(self) -> QueryPlanHandoffPayload:
        if self.expires_at <= self.issued_at:
            raise ValueError("query plan handoff must expire after issuance")
        identities = [
            (item.snapshot_id, item.claim_id) for item in self.ordered_candidates
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("query plan handoff candidates must be unique")
        return self


class SearchMemberRef(StrictModel):
    claim_id: Identifier
    snapshot_id: Identifier
    paper_id: Identifier
    paper_family_id: Identifier
    relation: QueryRelation
    relation_direction: RelationDirection
    relation_confidence: Annotated[float | int, Field(ge=0, le=1)] | None
    scope_relation: ScopeRelation
    scores: RetrievalScores


class SearchCluster(StrictModel):
    cluster_id: Identifier
    relation: QueryRelation
    representative: SearchMemberRef
    member_refs: Annotated[
        list[SearchMemberRef], Field(min_length=1, max_length=MAX_CLUSTER_MEMBERS)
    ]
    distinct_paper_families: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def consistent_preview(self) -> SearchCluster:
        if self.representative != self.member_refs[0]:
            raise ValueError("cluster representative must be its first member_ref")
        if any(item.relation != self.relation for item in self.member_refs):
            raise ValueError("cluster members must share the cluster relation")
        identities = [(item.snapshot_id, item.claim_id) for item in self.member_refs]
        if len(identities) != len(set(identities)):
            raise ValueError(
                "cluster member refs must have unique composite identities"
            )
        preview_families = {item.paper_family_id for item in self.member_refs}
        if self.distinct_paper_families < len(preview_families):
            raise ValueError("cluster family count must cover its member preview")
        return self


class HybridSearchResponse(StrictModel):
    schema_version: SearchSchemaVersion = RETRIEVAL_SCHEMA_VERSION
    stage: Literal["preview", "ai_rerank"]
    result_status: RetrievalResultStatus
    query_plan: QueryPlan | None
    clarification: ClarificationRequired | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    needs_context: NeedsContextRequired | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    channels: ChannelDiagnostics
    preview: Annotated[list[PreviewMemberRef], Field(max_length=30)]
    clusters: Annotated[list[SearchCluster], Field(max_length=MAX_SEARCH_LIMIT)]
    warnings: Annotated[list[WarningText], Field(max_length=MAX_WARNINGS)]
    retrieval_confidence: RetrievalConfidence | None = None
    query_plan_handoff: QueryPlanHandoffToken | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def unique_cluster_ids(self) -> HybridSearchResponse:
        if self.result_status in ("clarification_required", "needs_context"):
            context_pending = self.result_status == "needs_context"
            if (
                self.schema_version != RETRIEVAL_CONTEXT_SCHEMA_VERSION
                or self.stage != "preview"
                or self.query_plan is not None
                or (
                    context_pending
                    and (self.needs_context is None or self.clarification is not None)
                )
                or (
                    not context_pending
                    and (self.clarification is None or self.needs_context is not None)
                )
                or self.preview
                or self.clusters
                or self.query_plan_handoff is not None
                or self.retrieval_confidence is not None
                or any(
                    item.attempted or item.status != "skipped" or item.returned != 0
                    for item in (
                        self.channels.lexical,
                        self.channels.dense,
                        self.channels.rerank,
                        self.channels.relation,
                    )
                )
            ):
                raise ValueError(
                    "pending context or clarification must precede all retrieval"
                )
            return self
        if (
            self.query_plan is None
            or self.clarification is not None
            or self.needs_context is not None
        ):
            raise ValueError("retrieval responses require a plan and no clarification")
        if (
            self.schema_version != RETRIEVAL_CONTEXT_SCHEMA_VERSION
            and self.query_plan.resolved_query is not None
        ):
            raise ValueError("resolved query requires search protocol 2.3")
        if (
            self.schema_version == RETRIEVAL_CONTEXT_SCHEMA_VERSION
            and self.query_plan.resolved_query is None
        ):
            raise ValueError("search protocol 2.3 requires an explicit resolved query")
        cluster_ids = [item.cluster_id for item in self.clusters]
        if len(cluster_ids) != len(set(cluster_ids)):
            raise ValueError("search cluster IDs must be unique")
        if self.retrieval_confidence is not None and self.stage != "preview":
            raise ValueError("retrieval_confidence is preview-only telemetry")
        if self.schema_version == RETRIEVAL_SCHEMA_VERSION:
            if self.query_plan_handoff is not None:
                raise ValueError(
                    "legacy search responses cannot carry a query plan handoff"
                )
        elif self.stage == "preview":
            if self.query_plan_handoff is None:
                raise ValueError(
                    "search protocol 2.1 previews require a query plan handoff"
                )
        elif self.query_plan_handoff is not None:
            raise ValueError("final responses cannot carry a query plan handoff")
        if self.stage == "ai_rerank" and self.preview:
            raise ValueError("final responses cannot carry unassessed previews")
        visible_results = self.preview if self.stage == "preview" else self.clusters
        degraded = any(
            diagnostic.status == "degraded"
            for diagnostic in (
                self.channels.lexical,
                self.channels.dense,
                self.channels.rerank,
                self.channels.relation,
            )
        )
        if degraded != (self.result_status == "degraded"):
            raise ValueError("result_status must expose degraded retrieval channels")
        if self.result_status == "matches" and not visible_results:
            raise ValueError("matches requires at least one visible result")
        if self.result_status == "no_direct_answer" and visible_results:
            raise ValueError("no_direct_answer cannot contain visible results")
        return self


class ClaimRelationBatchRequest(StrictModel):
    schema_version: IndexVersion
    project_id: Identifier
    client_request_id: Identifier
    # Explicit lists keep the historical frozen comparison set. Null authorizes
    # the current complete project corpus, matching ordinary search semantics.
    active_snapshot_ids: (
        Annotated[
            list[Identifier], Field(min_length=1, max_length=MAX_ACTIVE_SNAPSHOTS)
        ]
        | None
    )
    claim_refs: Annotated[
        list[QueryClaimRef], Field(min_length=1, max_length=MAX_CLAIM_RELATION_BATCH)
    ]
    reprocess: bool = False

    @model_validator(mode="after")
    def unique_active_claims(self) -> ClaimRelationBatchRequest:
        if self.active_snapshot_ids is not None and len(
            self.active_snapshot_ids
        ) != len(set(self.active_snapshot_ids)):
            raise ValueError("active_snapshot_ids must be unique")
        keys = [(item.snapshot_id, item.claim_id) for item in self.claim_refs]
        if len(keys) != len(set(keys)):
            raise ValueError("claim_refs must be unique")
        active = (
            set(self.active_snapshot_ids)
            if self.active_snapshot_ids is not None
            else None
        )
        if active is not None and any(
            item.snapshot_id not in active for item in self.claim_refs
        ):
            raise ValueError("claim_refs must belong to active_snapshot_ids")
        return self


class ClaimRelationBatchItem(StrictModel):
    query_claim: QueryClaimRef
    status: Literal["succeeded", "failed"]
    response: HybridSearchResponse | None
    error_code: TermText | None

    @model_validator(mode="after")
    def consistent_result(self) -> ClaimRelationBatchItem:
        if self.status == "succeeded":
            if self.response is None or self.response.stage != "ai_rerank":
                raise ValueError("succeeded batch items require an AI result")
            if self.error_code is not None:
                raise ValueError("succeeded batch items cannot contain an error")
        elif self.response is not None or self.error_code is None:
            raise ValueError("failed batch items require only an error code")
        return self


class ClaimRelationBatchResponse(StrictModel):
    schema_version: IndexVersion = RETRIEVAL_SCHEMA_VERSION
    batch_id: Identifier
    status: Literal["running", "completed", "cancelled"]
    reprocess: bool
    total: Annotated[int, Field(ge=1, le=MAX_CLAIM_RELATION_BATCH)]
    completed: Annotated[int, Field(ge=0, le=MAX_CLAIM_RELATION_BATCH)]
    succeeded: Annotated[int, Field(ge=0, le=MAX_CLAIM_RELATION_BATCH)]
    failed: Annotated[int, Field(ge=0, le=MAX_CLAIM_RELATION_BATCH)]
    items: Annotated[
        list[ClaimRelationBatchItem], Field(max_length=MAX_CLAIM_RELATION_BATCH)
    ]

    # Omitted for legacy cumulative responses; cursor pages preserve total counters.
    item_offset: Annotated[int, Field(ge=0)] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    next_cursor: Annotated[int, Field(ge=0)] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    retired: bool | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def consistent_progress(self) -> ClaimRelationBatchResponse:
        if self.completed != self.succeeded + self.failed:
            raise ValueError("completed must equal succeeded plus failed")
        if self.completed > self.total:
            raise ValueError("batch completion exceeds total")
        if self.item_offset is None:
            if (
                self.next_cursor is not None
                or self.retired is not None
                or self.completed != len(self.items)
            ):
                raise ValueError("batch item count must match completed")
        elif (
            self.next_cursor is None
            or self.retired is None
            or not self.item_offset <= self.next_cursor <= self.completed
        ):
            raise ValueError("invalid batch cursor range")
        elif self.retired:
            if (
                self.status != "cancelled"
                or self.items
                or self.next_cursor != self.completed
            ):
                raise ValueError("retired batches cannot expose results")
        elif (
            self.next_cursor - self.item_offset != len(self.items)
            or len(self.items) > 16
        ):
            raise ValueError("batch cursor page does not match items")
        if self.status in {"completed", "cancelled"} and self.completed != self.total:
            raise ValueError("terminal batches must contain every result")
        if self.status == "running" and self.completed >= self.total:
            raise ValueError("running batches must have pending work")
        return self


class RelationDecision(StrictModel):
    candidate_key: Identifier
    relation: QueryRelation
    relation_direction: RelationDirection
    relation_confidence: Annotated[float | int, Field(ge=0, le=1)] | None
    scope_relation: ScopeRelation


class RelationDecisionBatch(StrictModel):
    decisions: Annotated[list[RelationDecision], Field(max_length=MAX_SEARCH_LIMIT)]


class ContextRelationInterpretation(StrictModel):
    """Model interpretation of task data, never a scientific truth assertion."""

    kind: Literal["single_proposition", "non_propositional", "missing_context"]
    proposition: SearchText | None
    polarity: ClaimPolarity | None
    entities: Annotated[list[QueryTermText], Field(max_length=64)]
    scope: SearchScopeFilter | None

    @field_validator("proposition")
    @classmethod
    def proposition_is_nfc(cls, value: str | None) -> str | None:
        if value is not None and value != unicodedata.normalize("NFC", value):
            raise ValueError("relation proposition must be NFC")
        return value

    @model_validator(mode="after")
    def consistent_interpretation(self) -> ContextRelationInterpretation:
        if self.kind == "single_proposition":
            if self.proposition is None:
                raise ValueError("single proposition requires proposition text")
            if self.scope is not None and not _scope_filter_is_meaningful(self.scope):
                raise ValueError("relation interpretation scope must be meaningful")
        elif (
            self.proposition is not None
            or self.polarity is not None
            or self.entities
            or self.scope is not None
        ):
            raise ValueError(
                "non-proposition interpretations cannot carry a proposition"
            )
        return self


class FrozenCompiledContextTaskReference(StrictModel):
    """Frozen compiled execution protocol.

    The proposal is canonical JSON to avoid a circular type dependency. Each
    execution consumer must strictly decode and recompile it against real task
    inputs, then compare compiled_task_sha256 and the interpretation projection.
    A matching hash alone is not a semantic validation.
    """

    version: Literal["deepshovel-context-task-reference/3.0"] = (
        "deepshovel-context-task-reference/3.0"
    )
    interpreter_identity_sha256: Sha256
    task_input_sha256: Sha256
    interpretation_sha256: Sha256
    interpretation: ContextRelationInterpretation
    proposal_json: Annotated[str, StringConstraints(min_length=2, max_length=196608)]
    compiled_task_sha256: Sha256

    @model_validator(mode="after")
    def validate_sealed_projection(self) -> FrozenCompiledContextTaskReference:
        if self.interpretation.kind == "missing_context":
            raise ValueError("missing context cannot be frozen for execution")
        digest = hashlib.sha256(
            canonical_json(self.interpretation.model_dump(mode="json")).encode()
        ).hexdigest()
        if digest != self.interpretation_sha256:
            raise ValueError("frozen interpretation hash mismatch")
        if len(self.proposal_json.encode("utf-8")) > 196608:
            raise ValueError("proposal exceeds byte limit")
        parsed = json.loads(self.proposal_json)
        if not isinstance(parsed, dict) or canonical_json(parsed) != self.proposal_json:
            raise ValueError("proposal must be a canonical JSON object")
        return self

    @property
    def reference_sha256(self) -> str:
        return hashlib.sha256(
            canonical_json(self.model_dump(mode="json")).encode()
        ).hexdigest()


class InteractiveRelationRankingDecision(StrictModel):
    id: Annotated[int, Field(ge=1, le=MAX_SEARCH_LIMIT)]
    relation: QueryRelation
    direction: RelationDirection
    scope: ScopeRelation
    score: Annotated[float | int, Field(ge=0, le=1)]


class InteractiveRelationRankingOutput(StrictModel):
    decisions: Annotated[
        list[InteractiveRelationRankingDecision], Field(max_length=MAX_SEARCH_LIMIT)
    ]


class ContextTaskRelationDecisionsWire(StrictModel):
    """Model-visible v3 response; caller owns identity, not a model SHA echo."""

    decisions: Annotated[list[RelationDecision], Field(max_length=MAX_SEARCH_LIMIT)]


class CallerBoundContextRelationBatch(ContextTaskRelationDecisionsWire):
    reference_sha256: Sha256
    configuration_sha256: Sha256
    batch_index: Annotated[int, Field(ge=0)]
    ordered_manifest_sha256: Sha256
    model_input_projection_sha256: Sha256


def relation_candidate_key(snapshot_id: str, claim_id: str) -> str:
    digest = hashlib.sha256(f"{snapshot_id}\0{claim_id}".encode()).hexdigest()
    return "candidate:sha256:" + digest


QueryPlanHandoffPayload.model_rebuild()
