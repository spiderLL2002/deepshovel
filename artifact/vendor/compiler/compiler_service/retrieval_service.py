"""Production claim-level hybrid retrieval orchestration."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import math
import re
import threading
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext, suppress
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from .errors import (
    CompilerError,
    InputError,
    ModelAttemptDiagnostic,
    ModelJobBudgetExceeded,
    ModelJobDeadlineExceeded,
    ModelOutputError,
    ModelRequestError,
    ModelRequestErrorKind,
    ModelUnavailable,
    QueryPlanHandoffError,
    QueryPlanHandoffUnavailable,
)
from .graph_contract import canonical_json
from .job_control import ModelJobControl
from .retrieval_contract import (
    MAX_QUERY_PLAN_HANDOFF_CHARACTERS,
    RETRIEVAL_CONTEXT_SCHEMA_VERSION,
    RETRIEVAL_SCHEMA_VERSION,
    RETRIEVAL_SEARCH_SCHEMA_VERSION,
    CallerBoundContextRelationBatch,
    ChannelDiagnostics,
    ClaimSearchDocument,
    ClarificationPrompt,
    ClarificationRequired,
    ContextClarificationTokenPayload,
    ContextQueryPlanningOutput,
    ContextRelationInterpretation,
    ContextTokenPayload,
    DeleteSnapshotsRequest,
    DeleteSnapshotsResponse,
    FrozenCompiledContextTaskReference,
    HybridSearchRequest,
    HybridSearchResponse,
    IndexManifestStatus,
    IndexSnapshotRequest,
    IndexSnapshotResponse,
    ManifestCheckRequest,
    ManifestCheckResponse,
    ManifestCheckStatus,
    NeedsContextPrompt,
    NeedsContextRequired,
    PreviewMemberRef,
    QueryClaimRef,
    QueryPlan,
    QueryPlanHandoffPayload,
    QueryPlanPayload,
    RelationDecision,
    RetrievalConfidence,
    RetrievalContextState,
    RetrievalScores,
    SearchCluster,
    SearchFilters,
    SearchMemberRef,
    SearchScope,
    SearchScopeFilter,
    SearchScopeFilterTime,
    StageDiagnostic,
    relation_candidate_key,
)
from .retrieval_ontology import (
    RetrievalOntology,
    concept_document_text,
    default_retrieval_ontology,
    ontology_normalized_text,
)
from .retrieval_providers import (
    DenseBatchIneligibleError,
    DenseEncoder,
    QueryPlanner,
    QueryPlanningInputTooLarge,
    RelationClassifier,
    compiled_context_relation_input,
    context_planning_identity,
    context_task_projection,
    freeze_compiled_context_task_reference,
    interactive_search_enabled,
    ordered_context_candidate_manifest_sha256,
    retrieval_search_budget,
    retrieval_search_policy,
    validate_compiled_context_task_reference,
)
from .retrieval_ranking_features import (
    LoadedRankingFeatureProjection,
    RankingFeatureBindings,
    build_ranking_feature_projection,
    current_ranking_feature_bindings,
    validated_feature_values,
)
from .retrieval_store import (
    ChannelHit,
    QdrantVectorIndex,
    RankingFeaturePreflightReceipt,
    ResolvedSnapshot,
    RetrievalStore,
    VectorIndex,
)
from .retrieval_task_contract import (
    CompiledTaskContract,
    InteractiveContextPlanningOutput,
    TaskOperationProposal,
    compile_task_contract,
)
from .retrieval_text import (
    constraint_text,
    content_token_set,
    dense_document_text,
    has_negation,
    lexical_fields,
    lexical_tokens,
    normalized_text,
    scope_values,
    term_present,
    time_interval_years,
    time_unit_factor,
    token_overlap,
)
from .scientific_mentions import scientific_mention_keys
from .shared_contract import RETRIEVAL_CONTINUATION_KEY_ID

logger = logging.getLogger(__name__)

MappingRankingFeatures = Mapping[tuple[str, str], LoadedRankingFeatureProjection]

# Lexical and dense retrieval recall the broad pool.  Exact technical anchors
# are fused before the narrow pool is handed to Model for final reranking.
_MODEL_RELATION_LIMIT = 12
# Preserve the existing Top-12 reserve policy; only widen the output window.
_PREVIEW_LIMIT = 30
_RELATION_CLASSIFIER_BATCH_LIMIT = 50
_CHANNEL_LIMIT = 250
_FILTERED_CHANNEL_LIMIT = 500
_SPARSE_CJK_LEXICAL_WEIGHT = 0.25
_SPARSE_CJK_MIN_QUERY_BIGRAMS = 4
_SPARSE_CJK_COMMON_DOCUMENTS = 3
_SOFT_FAMILY_DIVERSITY_START = 3
_SOFT_FAMILY_DIVERSITY_FACTOR = 0.99
_CROSS_LANGUAGE_DENSE_MIN_SCORE = 0.65
_CROSS_LANGUAGE_DENSE_MIN_MARGIN = 0.04
_UP_DIRECTION_RE = re.compile(
    r"\b(?:increase(?:d|s|ing)?|higher|greater|rise|rises|rose|rising|"
    r"upregulat(?:e|ed|es|ing|ion))\b|(?:增加|升高|提高|上升|促进|正相关)"
)
_DOWN_DIRECTION_RE = re.compile(
    r"\b(?:decrease(?:d|s|ing)?|lower|lowered|less|decline|declined|"
    r"reduc(?:e|ed|es|ing|tion)|downregulat(?:e|ed|es|ing|ion))\b|"
    r"(?:减少|降低|下降|抑制|负相关)"
)
_CLAIM_PROFILE_RE = re.compile(
    r"\b(?:is|are|was|were|cause(?:s|d)?|lead(?:s)?\s+to|result(?:s)?\s+in|"
    r"show(?:s|ed)?|indicat(?:e|es|ed)|suggest(?:s|ed)?|support(?:s|ed)?|"
    r"contradict(?:s|ed)?|correlat(?:e|es|ed))\b|"
    r"(?:是|为|导致|造成|表明|显示|说明|发现|支持|反驳|影响|相关)"
)
_QUERY_PLAN_HANDOFF_KEY_ID = RETRIEVAL_CONTINUATION_KEY_ID
_QUERY_PLAN_HANDOFF_MAC_DOMAIN = b"deepshovel-query-plan-handoff/v1\0"
_QUERY_PLAN_HANDOFF_KDF_DOMAIN = b"deepshovel-query-plan-handoff/key/v1\0"
_QUERY_PLAN_HANDOFF_MAX_PAYLOAD_BYTES = 512 * 1024
_QUERY_PLAN_HANDOFF_DEFAULT_TTL_SECONDS = 120
_QUERY_PLAN_HANDOFF_CLOCK_SKEW_SECONDS = 5
_RETRIEVAL_PIPELINE_VERSION = "deepshovel-retrieval-pipeline/2.1-handoff-3"
_CONTEXT_MAC_DOMAIN = b"deepshovel:context:1.0\0"
_CONTEXT_CLARIFICATION_MAC_DOMAIN = b"deepshovel:context-clarification:1.0\0"


class ClarificationSelectionError(InputError):
    code = "CLARIFICATION_REPREVIEW_REQUIRED"
    status_code = 409


class ContextSubmissionError(InputError):
    code = "CONTEXT_REPREVIEW_REQUIRED"
    status_code = 409


_PLANNING_FAILURES = {
    "OUTPUT_INVALID": (502, "retrieval planner returned an invalid output contract"),
    "TIMEOUT": (504, "retrieval planning timed out"),
    "UPSTREAM_FAILED": (502, "retrieval planning upstream request failed"),
    "UNAVAILABLE": (503, "clarification-capable retrieval planning is unavailable"),
    "PROVIDER_INVALID": (
        502,
        "retrieval planning provider returned an invalid decision",
    ),
    "INPUT_TOO_LARGE": (413, "retrieval planning input exceeds its bounded size"),
    "BUDGET_EXCEEDED": (503, "retrieval planning exhausted its bounded call budget"),
    "CAPABILITY_UNAVAILABLE": (
        422,
        "the supplied source representation cannot support the requested operation",
    ),
    "INTERNAL_ERROR": (500, "retrieval planning failed for an unclassified reason"),
}


class RetrievalPlanningError(InputError):
    """Fixed public categories: never include upstream messages or model data."""

    retryable = False

    def __init__(self, kind: str) -> None:
        self.status_code, message = _PLANNING_FAILURES[kind]
        self.code = "RETRIEVAL_PLANNING_" + kind
        super().__init__(message)


def _planning_failure(error: Exception) -> RetrievalPlanningError:
    """Keep the existing public classification independent of observability."""
    if isinstance(error, RetrievalPlanningError):
        return error
    if isinstance(error, ModelOutputError):
        kind = "OUTPUT_INVALID"
    elif isinstance(error, (ModelJobDeadlineExceeded, TimeoutError)):
        kind = "TIMEOUT"
    elif isinstance(error, ModelRequestError):
        if error.kind == ModelRequestErrorKind.TIMEOUT:
            kind = "TIMEOUT"
        elif error.kind == ModelRequestErrorKind.UNEXPECTED:
            kind = "INTERNAL_ERROR"
        else:
            kind = "UPSTREAM_FAILED"
    elif isinstance(error, ModelUnavailable):
        kind = "UPSTREAM_FAILED"
    elif isinstance(error, QueryPlanningInputTooLarge):
        kind = "INPUT_TOO_LARGE"
    elif isinstance(error, ModelJobBudgetExceeded):
        kind = "BUDGET_EXCEEDED"
    elif isinstance(error, ValidationError):
        kind = "PROVIDER_INVALID"
    else:
        kind = "INTERNAL_ERROR"
    return RetrievalPlanningError(kind)


def _log_context_planning_failure(
    failure: RetrievalPlanningError, error: Exception
) -> None:
    """Emit only fixed categories and already sanitized schema locations."""
    kind = failure.code.removeprefix("RETRIEVAL_PLANNING_")
    observation: dict[str, object] = {
        "failure_kind": kind if kind in _PLANNING_FAILURES else "INTERNAL_ERROR"
    }
    if isinstance(error, ModelOutputError) and error.diagnostics:
        diagnostic = error.diagnostics[-1]
        if isinstance(diagnostic, ModelAttemptDiagnostic):
            observation.update(
                classification=diagnostic.classification,
                finish_reason=diagnostic.finish_reason,
                schema_errors=[
                    {"loc": item.loc, "type": item.type}
                    for item in diagnostic.schema_errors
                ],
            )
    elif isinstance(error, ModelRequestError):
        observation.update(
            request_error_kind=error.kind.value,
            request_http_status=error.http_status,
        )
    # Do not log exceptions, their arguments, tracebacks, or the diagnostic
    # object: its other fields can contain content fingerprints or usage.
    logger.warning("Context retrieval planning failed: %s", canonical_json(observation))


@dataclass(slots=True)
class _Candidate:
    document: ClaimSearchDocument
    lexical: float | None
    dense: float | None
    rrf: float
    rerank: float | None = None
    decision: RelationDecision | None = None
    direction: str = "result_to_query"
    ai_rank: int | None = None
    same_paper_default: bool = False

    def rank_value(self) -> float:
        return self.rrf if self.rerank is None else self.rerank


@dataclass(slots=True)
class _ClusterGroup:
    representative: _Candidate
    preview: list[_Candidate]
    family_ids: set[str]


@dataclass(slots=True)
class _ContextTask:
    """A compiled 2.3 task from the single preview planning call."""

    context: RetrievalContextState
    query_document: ClaimSearchDocument | None
    compiled_task: CompiledTaskContract
    frozen_reference: FrozenCompiledContextTaskReference


@dataclass(slots=True)
class _SearchTask:
    """The verified, effective task of one search request, fixed before recall."""

    # As received (and signed); ``request`` carries the resolved query instead.
    protocol_request: HybridSearchRequest
    request: HybridSearchRequest
    is_context_protocol: bool
    verified_handoff: QueryPlanHandoffPayload | None
    context: RetrievalContextState | None
    frozen_reference: FrozenCompiledContextTaskReference | None
    compiled_task: CompiledTaskContract | None
    query_document: ClaimSearchDocument | None
    snapshots: list[ResolvedSnapshot]
    plan: QueryPlan


@dataclass(slots=True)
class _RecallResult:
    """Ranked candidates and the channel observations that produced them."""

    candidates: list[_Candidate]
    snapshot_ids: list[str]
    dense_snapshot_ids: list[str]
    lexical_hits: list[ChannelHit]
    dense_hits: list[ChannelHit]
    lexical_status: str
    dense_status: str
    anchor_boosted_keys: set[tuple[str, str]] = field(default_factory=set)
    exact_anchor_hits: int = 0
    concept_anchor_hits: int = 0
    scientific_anchor_hits: int = 0


@dataclass(slots=True, frozen=True)
class _RelationOutcome:
    status: Literal["ok", "degraded", "skipped"]
    attempted: bool
    provider: str


def _log_relation_failure(exc: BaseException) -> None:
    """Log a relation failure by type, code and value-free diagnostics only."""
    safe_diagnostics = tuple(
        {
            "classification": item.classification,
            "request_error_kind": (
                item.request_error_kind.value
                if item.request_error_kind is not None
                else None
            ),
            "request_http_status": item.request_http_status,
            "finish_reason": item.finish_reason,
            "provider_code": item.request_provider_code,
            "content_bytes": item.content_bytes,
            "content_sha256": item.content_sha256,
            "usage": item.usage.model_dump(exclude_none=True) if item.usage else None,
            "contract_issue": (
                item.contract_issue.value if item.contract_issue is not None else None
            ),
            "contract_loc": item.contract_loc,
        }
        for item in getattr(exc, "diagnostics", ())
        if isinstance(item, ModelAttemptDiagnostic)
    )
    logger.warning(
        "AI relation reranking failed: exception_type=%s code=%s diagnostics=%s",
        type(exc).__name__,
        exc.code if isinstance(exc, CompilerError) else None,
        safe_diagnostics,
    )


class RetrievalService:
    """Own the rebuildable local search projection and staged search pipeline."""

    def __init__(
        self,
        *,
        data_dir: Path,
        dense_encoder: DenseEncoder,
        query_planner: QueryPlanner | None = None,
        relation_classifier: RelationClassifier | None = None,
        vector_index: VectorIndex | None = None,
        ontology: RetrievalOntology | None = None,
        remote_query_planner_enabled: bool = True,
        selective_safe_reserve_enabled: bool = True,
        selective_safe_dense_batch_enabled: bool = False,
        query_scoped_concept_pattern_scan_enabled: bool = True,
        materialized_ranking_features_enabled: bool = False,
        ranking_feature_ontology_content_sha256: str | None = None,
        query_plan_handoff_secret: str | None = None,
        query_plan_handoff_ttl_seconds: int = (_QUERY_PLAN_HANDOFF_DEFAULT_TTL_SECONDS),
        query_plan_handoff_clock: Callable[[], float] = time.time,
        rrf_k: int = 60,
        qdrant_url: str | None = None,
        qdrant_api_key: str | None = None,
        vector_backend: str = "qdrant",
    ) -> None:
        if rrf_k < 1:
            raise ValueError("RRF k must be positive")
        if type(materialized_ranking_features_enabled) is not bool:
            raise ValueError("materialized_ranking_features_enabled must be Boolean")
        if (
            type(query_plan_handoff_ttl_seconds) is not int
            or not 30 <= query_plan_handoff_ttl_seconds <= 600
        ):
            raise ValueError("query plan handoff TTL must be an integer from 30 to 600")
        if query_plan_handoff_secret is not None and (
            not 32 <= len(query_plan_handoff_secret) <= 512
            or any(
                not 33 <= ord(character) <= 126
                for character in query_plan_handoff_secret
            )
        ):
            raise ValueError(
                "query plan handoff secret must be 32-512 non-whitespace "
                "ASCII characters"
            )
        if not callable(query_plan_handoff_clock):
            raise ValueError("query plan handoff clock must be callable")
        root = data_dir / "retrieval"
        self.store = RetrievalStore(
            root, ranking_features_enabled=materialized_ranking_features_enabled
        )
        self.dense_encoder = dense_encoder
        self.query_planner = query_planner
        self.relation_classifier = relation_classifier
        using_default_ontology = ontology is None
        self.ontology = ontology or default_retrieval_ontology()
        self.remote_query_planner_enabled = remote_query_planner_enabled
        self.selective_safe_reserve_enabled = selective_safe_reserve_enabled
        self.selective_safe_dense_batch_enabled = selective_safe_dense_batch_enabled
        self.query_scoped_concept_pattern_scan_enabled = (
            query_scoped_concept_pattern_scan_enabled
        )
        self.materialized_ranking_features_enabled = (
            materialized_ranking_features_enabled
        )
        if using_default_ontology and ranking_feature_ontology_content_sha256 is None:
            ranking_feature_ontology_content_sha256 = hashlib.sha256(
                Path(__file__).with_name("retrieval_ontology.json").read_bytes()
            ).hexdigest()
        self.ranking_feature_bindings = current_ranking_feature_bindings(
            self.ontology,
            ontology_content_sha256=ranking_feature_ontology_content_sha256,
        )
        self._query_plan_handoff_key = (
            None
            if query_plan_handoff_secret is None
            else hmac.new(
                query_plan_handoff_secret.encode("ascii"),
                _QUERY_PLAN_HANDOFF_KDF_DOMAIN,
                hashlib.sha256,
            ).digest()
        )
        self.query_plan_handoff_ttl_seconds = query_plan_handoff_ttl_seconds
        self._query_plan_handoff_clock = query_plan_handoff_clock
        self._dense_batch_observation_lock = threading.Lock()
        self._dense_batch_observations: dict[str, int] = {}
        self.dense_revision = getattr(dense_encoder, "revision", "injected-v1")
        self.rrf_k = rrf_k
        if vector_index is None and vector_backend == "numpy":
            from .retrieval_vectors import NumpyVectorIndex

            vector_index = NumpyVectorIndex(
                data_dir=root,
                model_name=dense_encoder.name,
                model_revision=self.dense_revision,
                dimension=dense_encoder.dimension,
            )
        self.vector_index = vector_index or QdrantVectorIndex(
            data_dir=root,
            model_name=dense_encoder.name,
            model_revision=self.dense_revision,
            dimension=dense_encoder.dimension,
            url=qdrant_url,
            api_key=qdrant_api_key,
        )
        self._index_lock = threading.Lock()
        self._materialized_ranking_feature_preflight: dict[str, Any] | None = None

    @property
    def dense_model(self) -> str:
        return self.dense_encoder.name

    @property
    def dense_dimension(self) -> int:
        return self.dense_encoder.dimension

    @property
    def model_readiness(self) -> dict[str, bool]:
        return {
            "dense": bool(getattr(self.dense_encoder, "ready", True)),
            "vector_store": self.vector_index is not None,
        }

    @property
    def dense_batch_observations(self) -> dict[str, int]:
        """Return internal opt-in batch outcomes without changing API diagnostics."""

        with self._dense_batch_observation_lock:
            return dict(sorted(self._dense_batch_observations.items()))

    @property
    def materialized_ranking_feature_preflight(self) -> dict[str, Any] | None:
        receipt = self._materialized_ranking_feature_preflight
        return None if receipt is None else dict(receipt)

    def _query_plan_handoff_configuration_binding(self) -> str:
        return _canonical_sha256(
            {
                "pipeline_version": _RETRIEVAL_PIPELINE_VERSION,
                "dense_model": self.dense_model,
                "dense_model_revision": self.dense_revision,
                "dense_dimension": self.dense_dimension,
                "rrf_k": self.rrf_k,
                "ontology_revision": self.ontology.revision,
                "ranking_feature_bindings": asdict(self.ranking_feature_bindings),
                "remote_query_planner_enabled": self.remote_query_planner_enabled,
                "selective_safe_reserve_enabled": self.selective_safe_reserve_enabled,
                "selective_safe_dense_batch_enabled": (
                    self.selective_safe_dense_batch_enabled
                ),
                "query_scoped_concept_pattern_scan_enabled": (
                    self.query_scoped_concept_pattern_scan_enabled
                ),
                "materialized_ranking_features_enabled": (
                    self.materialized_ranking_features_enabled
                ),
                "query_planner": getattr(self.query_planner, "name", None),
                "relation_classifier": getattr(self.relation_classifier, "name", None),
                "vector_index": type(self.vector_index).__name__,
            }
        )

    def _query_plan_handoff_corpus_binding(
        self,
        request: HybridSearchRequest,
        snapshots: Sequence[ResolvedSnapshot],
    ) -> str:
        snapshot_ids = [item.snapshot_id for item in snapshots]
        bound_snapshot_ids = list(snapshot_ids)
        if (
            request.query_claim is not None
            and request.query_claim.snapshot_id not in bound_snapshot_ids
        ):
            bound_snapshot_ids.append(request.query_claim.snapshot_id)
        manifests = self.store.get_manifests(
            project_id=request.project_id,
            snapshot_ids=bound_snapshot_ids,
            index_version="evidence-retrieval/2.0",
            dense_model=self.dense_model,
            dense_model_revision=self.dense_revision,
            dense_dimension=self.dense_dimension,
        )
        if set(manifests) != set(bound_snapshot_ids):
            raise _query_plan_handoff_error("corpus_manifest_missing")
        descriptors = []
        for snapshot in snapshots:
            manifest = manifests[snapshot.snapshot_id]
            descriptors.append(
                {
                    "snapshot_id": snapshot.snapshot_id,
                    "paper_id": snapshot.paper_id,
                    "paper_family_id": snapshot.paper_family_id,
                    "manifest_sha256": manifest.manifest_sha256,
                    "document_count": manifest.document_count,
                    "indexed_document_count": manifest.indexed_document_count,
                    "dense_complete": manifest.dense_complete,
                }
            )
        descriptors.sort(key=lambda item: item["snapshot_id"])
        requested = request.filters.active_snapshot_ids
        query_claim_manifest = None
        if request.query_claim is not None:
            stored = manifests[request.query_claim.snapshot_id]
            query_claim_manifest = {
                "snapshot_id": request.query_claim.snapshot_id,
                "manifest_sha256": stored.manifest_sha256,
                "document_count": stored.document_count,
                "indexed_document_count": stored.indexed_document_count,
                "dense_complete": stored.dense_complete,
            }
        return _canonical_sha256(
            {
                "requested_active_snapshot_ids": (
                    None if requested is None else sorted(requested)
                ),
                "query_claim_manifest": query_claim_manifest,
                "resolved_snapshots": descriptors,
            }
        )

    def _protocol_configuration_binding(self, request: HybridSearchRequest) -> str:
        legacy = self._query_plan_handoff_configuration_binding()
        if request.schema_version == RETRIEVAL_CONTEXT_SCHEMA_VERSION:
            return _canonical_sha256(
                {
                    "legacy": legacy,
                    "pipeline_version": "deepshovel-retrieval/2.3-task-contract-1",
                    "continuation_ttl_seconds": 1800,
                    "context": context_planning_identity(),
                }
            )
        return legacy

    def _issue_query_plan_handoff(
        self,
        request: HybridSearchRequest,
        plan: QueryPlan,
        snapshots: Sequence[ResolvedSnapshot],
        candidates: Sequence[_Candidate],
        *,
        context: RetrievalContextState | None = None,
        query_document: ClaimSearchDocument | None = None,
        frozen_reference: FrozenCompiledContextTaskReference | None = None,
    ) -> str:
        key = self._query_plan_handoff_key
        if key is None:
            raise QueryPlanHandoffUnavailable(
                "query plan handoff signing is unavailable"
            )
        now = int(self._query_plan_handoff_clock())
        context_fields: dict[str, Any] = {}
        if request.schema_version == RETRIEVAL_CONTEXT_SCHEMA_VERSION:
            if context is None or frozen_reference is None:
                raise InputError("context handoff requires verified task context")
            compiled = validate_compiled_context_task_reference(
                frozen_reference,
                request,
                context=context,
                query_document=query_document,
            )
            _validate_compiled_task_plan(plan, request, compiled)
            context_fields = {
                "context_state": context,
                "frozen_task_reference": frozen_reference.model_copy(deep=True),
                "task_context_sha256": _canonical_sha256(
                    context_task_projection(
                        request, context=context, query_document=query_document
                    )
                ),
            }
        payload = QueryPlanHandoffPayload(
            retrieval_schema_version=request.schema_version,
            issued_at=now,
            expires_at=now + self.query_plan_handoff_ttl_seconds,
            request_binding_sha256=_query_plan_handoff_request_binding(request),
            corpus_binding_sha256=self._query_plan_handoff_corpus_binding(
                request, snapshots
            ),
            configuration_binding_sha256=(
                self._protocol_configuration_binding(request)
            ),
            query_plan=plan,
            ordered_candidates=[_preview_ref(item) for item in candidates],
            **context_fields,
        )
        payload_bytes = canonical_json(payload.model_dump(mode="json")).encode("utf-8")
        if len(payload_bytes) > _QUERY_PLAN_HANDOFF_MAX_PAYLOAD_BYTES:
            raise InputError("query plan handoff exceeds the bounded protocol size")
        payload_segment = _base64url_encode(payload_bytes)
        signature = hmac.new(
            key,
            _query_plan_handoff_mac_input(payload_bytes),
            hashlib.sha256,
        ).digest()
        token = ".".join(
            (
                "v1",
                _QUERY_PLAN_HANDOFF_KEY_ID,
                payload_segment,
                _base64url_encode(signature),
            )
        )
        if len(token) > MAX_QUERY_PLAN_HANDOFF_CHARACTERS:
            raise InputError("query plan handoff exceeds the bounded protocol size")
        return token

    def _issue_context_token(
        self,
        request: HybridSearchRequest,
        context: RetrievalContextState,
        clarification: ClarificationPrompt | None = None,
    ) -> str:
        key = self._query_plan_handoff_key
        if key is None:
            raise InputError("context continuation signing is unavailable")
        now = int(self._query_plan_handoff_clock())
        fields = {
            "issued_at": now,
            "expires_at": now + 1800,
            "request_binding_sha256": _context_request_binding(request),
            "configuration_binding_sha256": self._protocol_configuration_binding(
                request
            ),
            "context": context,
        }
        if clarification is None:
            payload = ContextTokenPayload(**fields)
            version, domain = "n1", _CONTEXT_MAC_DOMAIN
        else:
            payload = ContextClarificationTokenPayload(
                **fields, clarification=clarification
            )
            version, domain = "c2", _CONTEXT_CLARIFICATION_MAC_DOMAIN
        raw = canonical_json(payload.model_dump(mode="json")).encode("utf-8")
        if len(raw) > _QUERY_PLAN_HANDOFF_MAX_PAYLOAD_BYTES:
            raise RetrievalPlanningError("INPUT_TOO_LARGE")
        signature = hmac.new(key, domain + raw, hashlib.sha256).digest()
        token = ".".join(
            (
                version,
                _QUERY_PLAN_HANDOFF_KEY_ID,
                _base64url_encode(raw),
                _base64url_encode(signature),
            )
        )
        if len(token) > MAX_QUERY_PLAN_HANDOFF_CHARACTERS:
            raise RetrievalPlanningError("INPUT_TOO_LARGE")
        return token

    def _verify_context_continuation(
        self, request: HybridSearchRequest
    ) -> RetrievalContextState:
        selection = request.clarification_selection
        submission = request.context_submission
        if selection is None and submission is None:
            return RetrievalContextState(supplements=[], selected_query=None)
        error_type = (
            ContextSubmissionError
            if submission is not None
            else ClarificationSelectionError
        )
        try:
            if selection is not None and submission is not None:
                raise ValueError("exclusive continuation")
            key = self._query_plan_handoff_key
            if key is None:
                raise ValueError("unavailable")
            token = (
                submission.context_token
                if submission is not None
                else selection.clarification_token
            )
            expected_version, domain, model = (
                ("n1", _CONTEXT_MAC_DOMAIN, ContextTokenPayload)
                if submission is not None
                else (
                    "c2",
                    _CONTEXT_CLARIFICATION_MAC_DOMAIN,
                    ContextClarificationTokenPayload,
                )
            )
            version, key_id, body, signature = token.split(".")
            if version != expected_version or key_id != _QUERY_PLAN_HANDOFF_KEY_ID:
                raise ValueError("envelope")
            raw = _base64url_decode(body)
            if len(raw) > _QUERY_PLAN_HANDOFF_MAX_PAYLOAD_BYTES:
                raise ValueError("bounded payload")
            expected = hmac.new(key, domain + raw, hashlib.sha256).digest()
            if not hmac.compare_digest(expected, _base64url_decode(signature)):
                raise ValueError("signature")
            value = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            )
            payload = model.model_validate(value, strict=True)
            if canonical_json(payload.model_dump(mode="json")).encode("utf-8") != raw:
                raise ValueError("canonical")
            now = int(self._query_plan_handoff_clock())
            if (
                payload.issued_at > now + _QUERY_PLAN_HANDOFF_CLOCK_SKEW_SECONDS
                or payload.expires_at <= now
                or not 0 < payload.expires_at - payload.issued_at <= 1800
                or not hmac.compare_digest(
                    payload.request_binding_sha256, _context_request_binding(request)
                )
                or not hmac.compare_digest(
                    payload.configuration_binding_sha256,
                    self._protocol_configuration_binding(request),
                )
            ):
                raise ValueError("binding")
            context = payload.context
            if selection is not None:
                if context.selected_query is not None:
                    raise ValueError("already selected")
                options = [
                    item
                    for item in payload.clarification.options
                    if item.option_id == selection.option_id
                ]
                if len(options) != 1:
                    raise ValueError("option")
                return RetrievalContextState(
                    supplements=context.supplements,
                    selected_query=options[0].resolved_query,
                )
        except (ValueError, TypeError, UnicodeDecodeError, binascii.Error):
            raise error_type(
                "context continuation is invalid or expired; request a new preview"
            ) from None
        # The token is valid; exceeding its cumulative input budget is a 413,
        # not a misleading bad-signature error or an implicit context reset.
        try:
            return RetrievalContextState(
                supplements=[*context.supplements, submission.content],
                selected_query=context.selected_query,
            )
        except ValidationError:
            raise RetrievalPlanningError("INPUT_TOO_LARGE") from None

    def _context_decision(
        self,
        request: HybridSearchRequest,
        *,
        context: RetrievalContextState,
        query_document: ClaimSearchDocument | None = None,
    ) -> ContextQueryPlanningOutput | InteractiveContextPlanningOutput:
        """The one 2.3 planning call: retrieve, clarify or ask for context."""
        try:
            interactive = interactive_search_enabled()
            method = getattr(
                self.query_planner,
                "plan_interactive_context" if interactive else "plan_context",
                None,
            )
            if not self.remote_query_planner_enabled or not callable(method):
                raise RetrievalPlanningError("UNAVAILABLE")
            call_request = request.model_copy(deep=True)
            call_context = context.model_copy(deep=True)
            output = (
                method(
                    call_request,
                    context=call_context,
                    query_document=query_document.model_copy(deep=True),
                )
                if query_document is not None
                else method(call_request, context=call_context)
            )
            output_type = (
                InteractiveContextPlanningOutput
                if interactive
                else ContextQueryPlanningOutput
            )
            if not isinstance(output, output_type):
                raise RetrievalPlanningError("PROVIDER_INVALID")
            output = output_type.model_validate(
                output.model_dump(mode="json"), strict=True
            )
            selected = output.decision if interactive else output.root
            if context.selected_query is not None and selected.action == "clarify":
                raise RetrievalPlanningError("PROVIDER_INVALID")
            return output
        except Exception as exc:
            failure = _planning_failure(exc)
            _log_context_planning_failure(failure, exc)
            if failure is exc:
                raise
            raise failure from None

    def _interpret_context_task(
        self,
        request: HybridSearchRequest,
        context: RetrievalContextState,
        query_document: ClaimSearchDocument | None,
        *,
        proposal: TaskOperationProposal | None = None,
    ) -> CompiledTaskContract:
        """Interpret original task material before recall, never derived hints."""
        try:
            if proposal is not None:
                return compile_task_contract(
                    proposal, request, context=context, query_document=query_document
                )
            method = getattr(self.query_planner, "interpret_compiled_task", None)
            if not self.remote_query_planner_enabled or not callable(method):
                raise RetrievalPlanningError("UNAVAILABLE")
            output = method(
                request.model_copy(deep=True),
                context=context.model_copy(deep=True),
                query_document=(
                    query_document.model_copy(deep=True) if query_document else None
                ),
            )
            if not isinstance(output, CompiledTaskContract):
                raise RetrievalPlanningError("PROVIDER_INVALID")
            validated = CompiledTaskContract.model_validate(
                output.model_dump(mode="json"), strict=True
            )
            # Custom providers cannot invent source inventory or derived fields.
            expected = compile_task_contract(
                validated.proposal,
                request,
                context=context,
                query_document=query_document,
            )
            if expected.model_dump(mode="json") != validated.model_dump(mode="json"):
                raise RetrievalPlanningError("PROVIDER_INVALID")
            return expected
        except Exception as exc:
            failure = _planning_failure(exc)
            if failure is exc:
                raise
            raise failure from None

    def _classify_frozen_context_batches(
        self,
        plan: QueryPlan,
        candidates: Sequence[ClaimSearchDocument],
        *,
        request: HybridSearchRequest,
        context: RetrievalContextState,
        query_document: ClaimSearchDocument | None,
        frozen_reference: FrozenCompiledContextTaskReference,
        search_started_at: float,
        warnings: list[str],
    ) -> list[RelationDecision]:
        """All batches share the frozen task, counters and caller cancellation."""
        compiled = validate_compiled_context_task_reference(
            frozen_reference, request, context=context, query_document=query_document
        )
        _validate_compiled_task_plan(plan, request, compiled)
        reference = FrozenCompiledContextTaskReference.model_validate(
            frozen_reference.model_dump(mode="json"), strict=True
        )
        configuration_sha256 = self._protocol_configuration_binding(request)
        classify = getattr(self.relation_classifier, "classify_compiled_context", None)
        if not callable(classify):
            raise ValueError("context-aware relation classification is unavailable")
        decisions: list[RelationDecision] = []
        batch_count = math.ceil(len(candidates) / _RELATION_CLASSIFIER_BATCH_LIMIT)
        with retrieval_search_budget(
            max_completion_calls=max(1, batch_count), started_at=search_started_at
        ) as control:
            for offset in range(0, len(candidates), _RELATION_CLASSIFIER_BATCH_LIMIT):
                control.checkpoint()
                batch = candidates[offset : offset + _RELATION_CLASSIFIER_BATCH_LIMIT]
                manifest_sha256 = ordered_context_candidate_manifest_sha256(batch)
                batch_index = offset // _RELATION_CLASSIFIER_BATCH_LIMIT
                input_sha256 = _canonical_sha256(
                    compiled_context_relation_input(
                        request,
                        context=context,
                        query_document=query_document,
                        frozen_reference=reference,
                        candidates=batch,
                    )
                )
                call_plan = plan.model_copy(deep=True)
                call_candidates = [item.model_copy(deep=True) for item in batch]
                call_arguments = {
                    "request": request.model_copy(deep=True),
                    "context": context.model_copy(deep=True),
                    "query_document": (
                        query_document.model_copy(deep=True) if query_document else None
                    ),
                    "frozen_reference": reference.model_copy(deep=True),
                    "configuration_sha256": configuration_sha256,
                    "batch_index": batch_index,
                }

                def call_state_sha256(
                    bound_plan, bound_candidates, bound_arguments
                ) -> str:
                    return _canonical_sha256(
                        {
                            "plan": bound_plan.model_dump(mode="json"),
                            "candidates": [
                                item.model_dump(mode="json")
                                for item in bound_candidates
                            ],
                            **{
                                key: value.model_dump(mode="json")
                                if hasattr(value, "model_dump")
                                else value
                                for key, value in bound_arguments.items()
                            },
                        }
                    )

                call_state = call_state_sha256(
                    call_plan, call_candidates, call_arguments
                )
                classified = classify(call_plan, call_candidates, **call_arguments)
                control.checkpoint()
                if (
                    call_state_sha256(call_plan, call_candidates, call_arguments)
                    != call_state
                ):
                    raise ValueError("context classifier mutated its call inputs")
                if not isinstance(classified, CallerBoundContextRelationBatch):
                    raise ValueError("frozen context relation reference is missing")
                classified = CallerBoundContextRelationBatch.model_validate(
                    classified.model_dump(mode="python"), strict=True
                )
                if (
                    classified.reference_sha256 != reference.reference_sha256
                    or classified.configuration_sha256 != configuration_sha256
                    or classified.batch_index != batch_index
                    or classified.ordered_manifest_sha256 != manifest_sha256
                    or classified.model_input_projection_sha256 != input_sha256
                ):
                    raise ValueError("frozen context relation reference mismatch")
                expected = {
                    relation_candidate_key(item.snapshot_id, item.claim_id)
                    for item in batch
                }
                keys = [item.candidate_key for item in classified.decisions]
                if len(keys) != len(expected) or set(keys) != expected:
                    raise ValueError("context relation batch coverage mismatch")
                if any(
                    item.relation_confidence is None for item in classified.decisions
                ):
                    raise ValueError("context relation confidence is missing")
                if (
                    not interactive_search_enabled()
                    and reference.interpretation.kind == "non_propositional"
                    and any(
                        item.relation not in {"related", "unrelated"}
                        for item in classified.decisions
                    )
                ):
                    raise ValueError("non-proposition relation is too strong")
                decisions.extend(classified.decisions)
                for warning in getattr(self.relation_classifier, "last_warnings", ()):
                    _warn(warnings, warning)
            control.checkpoint()
        return decisions

    def _clarification_response(
        self,
        request: HybridSearchRequest,
        prompt: ClarificationPrompt,
        *,
        context: RetrievalContextState,
    ) -> HybridSearchResponse:
        skipped = StageDiagnostic(
            attempted=False, status="skipped", provider="not-run", returned=0
        )
        return HybridSearchResponse(
            schema_version=request.schema_version,
            stage="preview",
            result_status="clarification_required",
            query_plan=None,
            clarification=ClarificationRequired(
                **prompt.model_dump(mode="json"),
                clarification_token=self._issue_context_token(
                    request, context, prompt
                ),
            ),
            channels=ChannelDiagnostics(
                lexical=skipped, dense=skipped, rerank=skipped, relation=skipped
            ),
            preview=[],
            clusters=[],
            warnings=[],
        )

    def _needs_context_response(
        self,
        request: HybridSearchRequest,
        prompt: NeedsContextPrompt,
        context: RetrievalContextState,
    ) -> HybridSearchResponse:
        if len(context.supplements) >= 8:
            raise RetrievalPlanningError("INPUT_TOO_LARGE")
        skipped = StageDiagnostic(
            attempted=False, status="skipped", provider="not-run", returned=0
        )
        return HybridSearchResponse(
            schema_version=request.schema_version,
            stage="preview",
            result_status="needs_context",
            query_plan=None,
            needs_context=NeedsContextRequired(
                **prompt.model_dump(mode="json"),
                context_token=self._issue_context_token(request, context),
            ),
            channels=ChannelDiagnostics(
                lexical=skipped, dense=skipped, rerank=skipped, relation=skipped
            ),
            preview=[],
            clusters=[],
            warnings=[],
        )

    def _verify_query_plan_handoff_envelope(
        self,
        request: HybridSearchRequest,
    ) -> QueryPlanHandoffPayload:
        key = self._query_plan_handoff_key
        if key is None:
            raise QueryPlanHandoffUnavailable(
                "query plan handoff verification is unavailable"
            )
        token = request.query_plan_handoff
        if token is None:
            raise _query_plan_handoff_error("token_missing")
        try:
            version, key_id, payload_segment, signature_segment = token.split(".")
            if version != "v1" or key_id != _QUERY_PLAN_HANDOFF_KEY_ID:
                raise ValueError("unsupported handoff envelope")
            payload_bytes = _base64url_decode(payload_segment)
            if len(payload_bytes) > _QUERY_PLAN_HANDOFF_MAX_PAYLOAD_BYTES:
                raise ValueError("handoff payload is too large")
            signature = _base64url_decode(signature_segment)
            if len(signature) != hashlib.sha256().digest_size:
                raise ValueError("invalid handoff signature size")
            expected = hmac.new(
                key,
                _query_plan_handoff_mac_input(payload_bytes),
                hashlib.sha256,
            ).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("invalid handoff signature")
            raw_payload = json.loads(
                payload_bytes.decode("utf-8"),
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            )
            if not isinstance(raw_payload, dict):
                raise ValueError("handoff payload must be an object")
            if canonical_json(raw_payload).encode("utf-8") != payload_bytes:
                raise ValueError("handoff payload is not canonical")
            payload = QueryPlanHandoffPayload.model_validate(raw_payload, strict=True)
            if (
                canonical_json(payload.model_dump(mode="json")).encode("utf-8")
                != payload_bytes
            ):
                raise ValueError("handoff model is not canonical")
            if payload.key_id != key_id:
                raise ValueError("handoff key identity mismatch")
            if payload.retrieval_schema_version != request.schema_version:
                raise ValueError("handoff protocol mismatch")
        except (ValueError, TypeError, UnicodeDecodeError, binascii.Error) as exc:
            raise _query_plan_handoff_error(_handoff_envelope_reason(exc)) from None

        now = int(self._query_plan_handoff_clock())
        if payload.issued_at > now + _QUERY_PLAN_HANDOFF_CLOCK_SKEW_SECONDS:
            raise _query_plan_handoff_error("issued_in_future")
        if payload.expires_at <= now:
            raise _query_plan_handoff_error(
                f"expired age_s={now - payload.issued_at}"
            )
        if payload.expires_at - payload.issued_at > self.query_plan_handoff_ttl_seconds:
            raise _query_plan_handoff_error("lifetime_exceeds_ttl")
        expected_candidates = [
            {"snapshot_id": item.snapshot_id, "claim_id": item.claim_id}
            for item in payload.ordered_candidates
        ]
        requested_candidates = [
            {"snapshot_id": item.snapshot_id, "claim_id": item.claim_id}
            for item in request.candidate_refs
        ]
        bindings = (
            (
                "request_binding",
                payload.request_binding_sha256,
                _query_plan_handoff_request_binding(request),
            ),
            (
                "configuration_binding",
                payload.configuration_binding_sha256,
                self._protocol_configuration_binding(request),
            ),
            (
                "candidate_binding",
                _canonical_sha256(expected_candidates),
                _canonical_sha256(requested_candidates),
            ),
        )
        for reason, sealed, observed in bindings:
            if not hmac.compare_digest(sealed, observed):
                raise _query_plan_handoff_error(reason)
        return payload

    def _verify_query_plan_handoff_corpus(
        self,
        request: HybridSearchRequest,
        snapshots: Sequence[ResolvedSnapshot],
        handoff: QueryPlanHandoffPayload,
    ) -> None:
        observed = self._query_plan_handoff_corpus_binding(request, snapshots)
        if not hmac.compare_digest(handoff.corpus_binding_sha256, observed):
            raise _query_plan_handoff_error("corpus_binding")

    def _verify_handoff_task_context(
        self,
        request: HybridSearchRequest,
        handoff: QueryPlanHandoffPayload,
        query_document: ClaimSearchDocument | None,
    ) -> RetrievalContextState:
        """Restore only outer-sealed context after request/config/corpus checks."""
        try:
            if (
                request.schema_version != RETRIEVAL_CONTEXT_SCHEMA_VERSION
                or handoff.retrieval_schema_version != request.schema_version
                or handoff.context_state is None
                or handoff.task_context_sha256 is None
                or handoff.frozen_task_reference is None
            ):
                raise ValueError("missing context binding")
            context = handoff.context_state.model_copy(deep=True)
            observed = _canonical_sha256(
                context_task_projection(
                    request, context=context, query_document=query_document
                )
            )
            if not hmac.compare_digest(handoff.task_context_sha256, observed):
                raise ValueError("task context mismatch")
            compiled = validate_compiled_context_task_reference(
                handoff.frozen_task_reference,
                request,
                context=context,
                query_document=query_document,
            )
            _validate_compiled_task_plan(handoff.query_plan, request, compiled)
        except (ValueError, TypeError):
            raise _query_plan_handoff_error("task_context") from None
        # Do not reopen n1/c2 or require their current TTL. The valid final
        # handoff seals the exact context accepted by its preceding preview.
        return context

    def _load_verified_handoff_candidates(
        self,
        request: HybridSearchRequest,
        handoff: QueryPlanHandoffPayload,
        snapshot_ids: Sequence[str],
    ) -> list[_Candidate]:
        """Hydrate the signed preview without repeating recall or ranking."""

        ordered_keys = [
            (item.snapshot_id, item.claim_id) for item in handoff.ordered_candidates
        ]
        documents = self.store.load_documents(
            project_id=request.project_id,
            dense_model=self.dense_model,
            dense_model_revision=self.dense_revision,
            dense_dimension=self.dense_dimension,
            keys=ordered_keys,
        )
        active_snapshots = set(snapshot_ids)
        candidates: list[_Candidate] = []
        for sealed, key in zip(handoff.ordered_candidates, ordered_keys, strict=True):
            document = documents.get(key)
            if (
                document is None
                or document.snapshot_id not in active_snapshots
                or not _matches_explicit_constraints(request, document)
                or sealed.paper_id != document.paper_id
                or sealed.paper_family_id != document.paper_family_id
            ):
                raise _query_plan_handoff_error("candidate_document")
            candidates.append(
                _Candidate(
                    document=document,
                    lexical=sealed.scores.lexical,
                    dense=sealed.scores.dense,
                    rrf=sealed.scores.rrf,
                    rerank=sealed.scores.rerank,
                )
            )
        return candidates

    def _observe_dense_batch(self, outcome: str) -> None:
        with self._dense_batch_observation_lock:
            self._dense_batch_observations[outcome] = (
                self._dense_batch_observations.get(outcome, 0) + 1
            )
        logger.info(
            "selective-safe dense query batch outcome",
            extra={"dense_batch_outcome": outcome},
        )

    def _batch_additional_dense_vectors(
        self, query_texts: list[str]
    ) -> list[list[float]] | None:
        """Return a validated opt-in batch or request exact sequential fallback."""

        if len(query_texts) < 2:
            self._observe_dense_batch("insufficient_views_fallback")
            return None
        embed_queries = getattr(self.dense_encoder, "embed_queries", None)
        if not callable(embed_queries):
            self._observe_dense_batch("unavailable_fallback")
            return None
        try:
            vectors = embed_queries(query_texts)
        except DenseBatchIneligibleError:
            self._observe_dense_batch("ineligible_fallback")
            return None
        except Exception:
            self._observe_dense_batch("exception_fallback")
            return None
        if not isinstance(vectors, list) or len(vectors) != len(query_texts):
            self._observe_dense_batch("invalid_count_fallback")
            return None
        for vector in vectors:
            if not isinstance(vector, list) or len(vector) != self.dense_dimension:
                self._observe_dense_batch("invalid_shape_fallback")
                return None
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in vector
            ):
                self._observe_dense_batch("invalid_value_fallback")
                return None
        self._observe_dense_batch("success")
        return vectors

    def warm_models(self) -> None:
        """Materialize the multilingual dense ONNX session."""

        query = "multilingual scientific evidence"
        vector = self.dense_encoder.embed_query(query)
        if len(vector) != self.dense_dimension:
            raise RuntimeError(
                "retrieval serving-process warmup returned an invalid shape"
            )

    def prepare_materialized_ranking_features(
        self, *, project_ids: Sequence[str] | None = None
    ) -> dict[str, Any]:
        """Atomically validate and backfill all selected complete local indexes."""

        if not self.materialized_ranking_features_enabled:
            raise RuntimeError("materialized ranking features are disabled")
        from .retrieval_ranking_features import RANKING_FEATURE_SCHEMA_VERSION

        contract_sha256 = _canonical_sha256(
            {
                "receipt_protocol": "ranking-feature-snapshot-receipt/1.0",
                "schema_version": RANKING_FEATURE_SCHEMA_VERSION,
                "bindings": asdict(self.ranking_feature_bindings),
            }
        )

        def prepare(
            document: ClaimSearchDocument,
            source: str,
            loaded: LoadedRankingFeatureProjection | None,
        ) -> str:
            if loaded is not None and all(
                validated_feature_values(
                    loaded, name, bindings=self.ranking_feature_bindings
                )
                is not None
                for name in (
                    "exact_anchor_tokens",
                    "sparse_cjk_tokens",
                    "scientific_mention_keys",
                    "ontology_concept_ids",
                )
            ):
                return loaded.projection_json
            return build_ranking_feature_projection(
                document,
                source_document_json=source,
                ontology=self.ontology,
                bindings=self.ranking_feature_bindings,
            )

        with self._index_lock:
            store_receipt: RankingFeaturePreflightReceipt = (
                self.store.prepare_ranking_feature_receipts(
                    project_ids=project_ids,
                    dense_model=self.dense_model,
                    dense_model_revision=self.dense_revision,
                    dense_dimension=self.dense_dimension,
                    contract_sha256=contract_sha256,
                    prepare=prepare,
                )
            )
        receipt = {
            "status": "ready",
            "project_count": len(store_receipt.project_ids),
            "source_documents": store_receipt.source_documents,
            "inserted": store_receipt.inserted,
            "updated": store_receipt.updated,
            "unchanged": store_receipt.unchanged,
            "deleted_orphans": store_receipt.deleted_orphans,
            "schema_created": store_receipt.schema_created,
            "delete_trigger_created": store_receipt.delete_trigger_created,
        }
        self._materialized_ranking_feature_preflight = receipt
        return dict(receipt)

    def rebuild_materialized_ranking_features(self, *, project_id: str) -> int:
        """Explicitly converge one already-complete project's sidecars."""

        receipt = self.prepare_materialized_ranking_features(project_ids=[project_id])
        return int(receipt["source_documents"])

    def check_manifests(self, request: ManifestCheckRequest) -> ManifestCheckResponse:
        stored_by_snapshot = self.store.get_manifests(
            project_id=request.project_id,
            snapshot_ids=[item.snapshot_id for item in request.manifests],
            index_version=request.manifests[0].index_version,
            dense_model=self.dense_model,
            dense_model_revision=self.dense_revision,
            dense_dimension=self.dense_dimension,
        )
        statuses: list[ManifestCheckStatus] = []
        for manifest in request.manifests:
            expected = self.store.manifest_sha256(manifest)
            stored = stored_by_snapshot.get(manifest.snapshot_id)
            if stored is None:
                statuses.append(
                    ManifestCheckStatus(
                        snapshot_id=manifest.snapshot_id,
                        status="missing",
                        document_count=manifest.document_count,
                        indexed_document_count=0,
                        manifest_sha256=None,
                        dense_complete=False,
                    )
                )
                continue
            if stored.manifest_sha256 != expected:
                status = "stale"
            elif (
                stored.indexed_document_count != stored.document_count
                or not stored.dense_complete
            ):
                status = "degraded"
            else:
                status = "indexed"
            statuses.append(
                ManifestCheckStatus(
                    snapshot_id=manifest.snapshot_id,
                    status=status,
                    document_count=stored.document_count,
                    indexed_document_count=stored.indexed_document_count,
                    manifest_sha256=stored.manifest_sha256,
                    dense_complete=stored.dense_complete,
                )
            )
        return ManifestCheckResponse(manifests=statuses)

    def index_snapshot(self, request: IndexSnapshotRequest) -> IndexSnapshotResponse:
        warnings: list[str] = []
        with self._index_lock:
            manifest = request.manifest
            expected = self.store.manifest_sha256(manifest)
            before = self.store.get_manifest(
                project_id=request.project_id,
                snapshot_id=manifest.snapshot_id,
                index_version=manifest.index_version,
                dense_model=self.dense_model,
                dense_model_revision=self.dense_revision,
                dense_dimension=self.dense_dimension,
            )
            if before is not None and before.manifest_sha256 != expected:
                raise InputError(
                    "immutable snapshot has a conflicting retrieval manifest"
                )
            try:
                document_jsons = self.store.validate_projection_chunk(
                    request,
                    dense_model=self.dense_model,
                    dense_model_revision=self.dense_revision,
                    dense_dimension=self.dense_dimension,
                )
            except ValueError as exc:
                raise InputError(str(exc)) from exc
            if (
                before is not None
                and before.indexed_document_count == before.document_count
                and before.dense_complete
            ):
                return IndexSnapshotResponse(
                    manifests=[
                        IndexManifestStatus(
                            snapshot_id=manifest.snapshot_id,
                            status="unchanged",
                            document_count=before.document_count,
                            indexed_document_count=before.indexed_document_count,
                            manifest_sha256=before.manifest_sha256,
                            dense_complete=True,
                        )
                    ],
                    indexed_documents=0,
                    warnings=[],
                )

            chunk_dense_complete = True
            point_ids: list[str | None] = [None] * len(request.documents)
            if request.documents:
                try:
                    vectors = self.dense_encoder.embed_documents(
                        [dense_document_text(item) for item in request.documents]
                    )
                    if len(vectors) != len(request.documents):
                        raise RuntimeError(
                            "dense encoder returned an invalid vector count"
                        )
                    created = self.vector_index.upsert(
                        project_id=request.project_id,
                        snapshot_id=manifest.snapshot_id,
                        claim_ids=[item.claim_id for item in request.documents],
                        claim_types=[item.claim_type for item in request.documents],
                        polarities=[item.polarity for item in request.documents],
                        scopes=[item.scope for item in request.documents],
                        vectors=vectors,
                    )
                    point_ids = list(created)
                except Exception:
                    chunk_dense_complete = False
                    _warn(
                        warnings,
                        "dense indexing unavailable; lexical projection retained",
                    )

            try:
                ranking_feature_jsons = (
                    [
                        build_ranking_feature_projection(
                            document,
                            source_document_json=document_json,
                            ontology=self.ontology,
                            bindings=self.ranking_feature_bindings,
                        )
                        for document, document_json in zip(
                            request.documents, document_jsons, strict=True
                        )
                    ]
                    if self.materialized_ranking_features_enabled
                    else None
                )
                self.store.upsert_projection(
                    request,
                    dense_model=self.dense_model,
                    dense_model_revision=self.dense_revision,
                    dense_dimension=self.dense_dimension,
                    collection_name=self.vector_index.collection_name,
                    dense_complete=chunk_dense_complete,
                    point_ids=point_ids,
                    document_jsons=document_jsons,
                    ranking_feature_jsons=ranking_feature_jsons,
                )
            except ValueError as exc:
                raise InputError(str(exc)) from exc
            stored = self.store.get_manifest(
                project_id=request.project_id,
                snapshot_id=manifest.snapshot_id,
                index_version=manifest.index_version,
                dense_model=self.dense_model,
                dense_model_revision=self.dense_revision,
                dense_dimension=self.dense_dimension,
            )
            if stored is None:
                raise RuntimeError("retrieval manifest commit did not complete")
            complete = (
                stored.indexed_document_count == stored.document_count
                and stored.dense_complete
            )
            return IndexSnapshotResponse(
                manifests=[
                    IndexManifestStatus(
                        snapshot_id=manifest.snapshot_id,
                        status="indexed" if complete else "degraded",
                        document_count=stored.document_count,
                        indexed_document_count=stored.indexed_document_count,
                        manifest_sha256=stored.manifest_sha256,
                        dense_complete=stored.dense_complete,
                    )
                ],
                indexed_documents=len(request.documents),
                warnings=warnings,
            )

    def delete_snapshots(
        self, request: DeleteSnapshotsRequest
    ) -> DeleteSnapshotsResponse:
        warnings: list[str] = []
        with self._index_lock:
            deleted = self.store.plan_delete_snapshots(
                project_id=request.project_id,
                snapshot_ids=request.snapshot_ids,
            )
            if deleted.collections:
                self.vector_index.delete_snapshots(
                    project_id=request.project_id,
                    snapshot_ids=request.snapshot_ids,
                    collections=deleted.collections,
                )
            self.store.commit_delete_snapshots(
                project_id=request.project_id,
                snapshot_ids=request.snapshot_ids,
            )
        return DeleteSnapshotsResponse(
            deleted_snapshots=deleted.deleted_snapshots,
            deleted_documents=deleted.deleted_documents,
            warnings=warnings,
        )

    def repair_dense_projections(self) -> dict[str, Any]:
        """Explicitly re-embed complete lexical projections for this model identity."""

        projections = self.store.list_dense_repairs(
            dense_model=self.dense_model,
            dense_model_revision=self.dense_revision,
            dense_dimension=self.dense_dimension,
        )
        repaired = 0
        warnings: list[str] = []
        for projection in projections:
            for documents in self.store.iter_repair_chunks(
                project_id=projection.project_id,
                snapshot_id=projection.manifest.snapshot_id,
                dense_model=self.dense_model,
                dense_model_revision=self.dense_revision,
                dense_dimension=self.dense_dimension,
            ):
                response = self.index_snapshot(
                    IndexSnapshotRequest(
                        schema_version="evidence-retrieval/2.0",
                        project_id=projection.project_id,
                        manifest=projection.manifest,
                        documents=documents,
                    )
                )
                for warning in response.warnings:
                    _warn(warnings, warning)
            stored = self.store.get_manifest(
                project_id=projection.project_id,
                snapshot_id=projection.manifest.snapshot_id,
                index_version=projection.manifest.index_version,
                dense_model=self.dense_model,
                dense_model_revision=self.dense_revision,
                dense_dimension=self.dense_dimension,
            )
            if stored is not None and stored.dense_complete:
                repaired += 1
            else:
                _warn(
                    warnings,
                    "one complete lexical projection remains dense-degraded",
                )
        return {
            "attempted_projections": len(projections),
            "repaired_projections": repaired,
            "warnings": warnings,
        }

    def search(
        self, request: HybridSearchRequest, *, interactive: bool = False
    ) -> HybridSearchResponse:
        # Only authenticated real-time routes opt into the paired model race.
        # Batch callers retain their existing provider policy.
        with retrieval_search_policy(interactive=interactive):
            return self._search_with_policy(request)

    def _search_with_policy(self, request: HybridSearchRequest) -> HybridSearchResponse:
        started_at = time.monotonic()
        if request.stage == "preview" and (
            request.schema_version == RETRIEVAL_CONTEXT_SCHEMA_VERSION
            or interactive_search_enabled()
        ):
            try:
                with retrieval_search_budget(
                    max_completion_calls=(
                        2
                        if request.schema_version == RETRIEVAL_CONTEXT_SCHEMA_VERSION
                        and not interactive_search_enabled()
                        else 1
                    ),
                    started_at=started_at,
                ) as control:
                    result = self._search(
                        request, search_started_at=started_at, search_control=control
                    )
                    control.checkpoint()
                    return result
            except (ModelJobBudgetExceeded, ModelJobDeadlineExceeded) as exc:
                raise _planning_failure(exc) from None
        return self._search(request, search_started_at=started_at)

    def _search(
        self,
        request: HybridSearchRequest,
        *,
        search_started_at: float,
        search_control: ModelJobControl | None = None,
        preview_limit: int = _PREVIEW_LIMIT,
    ) -> HybridSearchResponse:
        """One search: fix the verified task, recall and rank, then seal or label.

        Stages: ``_resolve_search_task`` (protocol verification, 2.3 planning,
        corpus, QueryPlan) -> ``_recall_candidates`` (channels, fusion, anchors,
        reserves) -> either ``_preview_response`` (sealed handoff) or
        ``_final_relation_candidates`` -> ``_label_relation_candidates`` ->
        ``_final_response`` (clusters).
        """
        warnings: list[str] = []
        task = self._resolve_search_task(request, warnings, search_control)
        if isinstance(task, HybridSearchResponse):
            # A pending clarification or missing context precedes all retrieval.
            return task
        recall = self._recall_candidates(task, warnings, search_control)
        preview_candidates = recall.candidates[
            : min(_PREVIEW_LIMIT, preview_limit, task.request.filters.limit)
        ]
        if task.request.stage == "preview":
            return self._preview_response(task, recall, preview_candidates, warnings)
        relation_candidates = self._final_relation_candidates(
            task, recall, preview_candidates
        )
        outcome = self._label_relation_candidates(
            task, relation_candidates, warnings, search_started_at=search_started_at
        )
        return self._final_response(
            task, recall, relation_candidates, outcome, warnings
        )

    # -- stage 1: the verified task -------------------------------------------

    def _resolve_search_task(
        self,
        request: HybridSearchRequest,
        warnings: list[str],
        search_control: ModelJobControl | None,
    ) -> _SearchTask | HybridSearchResponse:
        """Verify protocol state and fix the effective task and plan before recall."""
        protocol_request = request
        if (
            interactive_search_enabled()
            and request.schema_version == RETRIEVAL_SCHEMA_VERSION
            and request.stage == "ai_rerank"
        ):
            # A handoff-free legacy final would rank under a fresh local plan
            # instead of the model plan that produced the preview (RTS-1).
            raise InputError("interactive final requests require a preview handoff")
        is_context_protocol = request.schema_version == RETRIEVAL_CONTEXT_SCHEMA_VERSION
        resolved_query: str | None = None
        verified_handoff = (
            self._verify_query_plan_handoff_envelope(request)
            if request.schema_version
            in (RETRIEVAL_SEARCH_SCHEMA_VERSION, RETRIEVAL_CONTEXT_SCHEMA_VERSION)
            and request.stage == "ai_rerank"
            else None
        )
        context: RetrievalContextState | None = None
        context_query_document: ClaimSearchDocument | None = None
        frozen_reference: FrozenCompiledContextTaskReference | None = None
        compiled_task: CompiledTaskContract | None = None
        if is_context_protocol:
            if verified_handoff is not None:
                resolved_query = verified_handoff.query_plan.resolved_query
                if resolved_query is None:
                    raise _query_plan_handoff_error("resolved_query_missing")
            else:
                planned = self._plan_context_task(request, search_control)
                if isinstance(planned, HybridSearchResponse):
                    return planned
                context = planned.context
                context_query_document = planned.query_document
                compiled_task = planned.compiled_task
                frozen_reference = planned.frozen_reference
                # Interactive planning resolves the task once. Compilation here
                # checks the returned source references without another model call.
                resolved_query = compiled_task.retrieval_text
        if resolved_query is not None:
            # Keep the signed/original request separate from the effective task.
            request = request.model_copy(update={"query": resolved_query})
        query_document = context_query_document or self._query_document(request)
        if request.query_claim is not None and query_document is None:
            if verified_handoff is not None:
                raise _query_plan_handoff_error("query_document_missing")
            raise InputError("query Claim is not indexed for the active dense model")
        planning_request = _planning_request(request, query_document)
        snapshots = self._resolve_snapshots(request, query_document, warnings)
        if verified_handoff is not None:
            self._verify_query_plan_handoff_corpus(
                protocol_request, snapshots, verified_handoff
            )
            if is_context_protocol:
                context = self._verify_handoff_task_context(
                    protocol_request, verified_handoff, query_document
                )
                frozen_reference = verified_handoff.frozen_task_reference.model_copy(
                    deep=True
                )
                compiled_task = validate_compiled_context_task_reference(
                    frozen_reference,
                    protocol_request,
                    context=context,
                    query_document=query_document,
                )
        # Plan the visible pass before recall so faithful translation aliases
        # can retrieve evidence written in another language.  The planner is
        # bounded and failure-safe; deterministic planning remains the fallback.
        if verified_handoff is None:
            if compiled_task is not None:
                plan = _compiled_task_plan(protocol_request, compiled_task)
            else:
                plan = self._plan(
                    request,
                    planning_request,
                    query_document,
                    warnings,
                    allow_remote=(
                        self.remote_query_planner_enabled
                        and request.stage == "preview"
                        and not request.candidate_refs
                        and bool(snapshots or query_document is not None)
                    ),
                )
            if not is_context_protocol:
                plan = _augment_plan_with_ontology(plan, self.ontology)
        else:
            # The signed preview plan is the sole 2.1 final authority.  Never
            # re-plan or ontology-augment it after verification.
            plan = verified_handoff.query_plan
        return _SearchTask(
            protocol_request=protocol_request,
            request=request,
            is_context_protocol=is_context_protocol,
            verified_handoff=verified_handoff,
            context=context,
            frozen_reference=frozen_reference,
            compiled_task=compiled_task,
            query_document=query_document,
            snapshots=snapshots,
            plan=plan,
        )

    def _plan_context_task(
        self,
        request: HybridSearchRequest,
        search_control: ModelJobControl | None,
    ) -> _ContextTask | HybridSearchResponse:
        """The 2.3 preview prelude: one planning call, then task compilation."""
        context = self._verify_context_continuation(request)
        query_document: ClaimSearchDocument | None = None
        if request.query_claim is not None:
            query_document = self._query_document(request)
            if query_document is None:
                raise InputError(
                    "query Claim is not indexed for the active dense model"
                )
        output = self._context_decision(
            request,
            context=context,
            query_document=query_document,
        )
        if search_control is not None:
            search_control.checkpoint()
        selected = (
            output.decision
            if isinstance(output, InteractiveContextPlanningOutput)
            else output.root
        )
        if selected.action == "needs_context":
            return self._needs_context_response(
                request, selected.needs_context, context
            )
        if selected.action == "clarify":
            return self._clarification_response(
                request, selected.clarification, context=context
            )
        compiled_task = self._interpret_context_task(
            request,
            context,
            query_document,
            proposal=(
                selected.proposal
                if isinstance(output, InteractiveContextPlanningOutput)
                else None
            ),
        )
        if search_control is not None:
            search_control.checkpoint()
        if compiled_task.status == "needs_context":
            return self._needs_context_response(
                request, compiled_task.needs_context, context
            )
        if compiled_task.status == "capability_unavailable":
            raise RetrievalPlanningError("CAPABILITY_UNAVAILABLE")
        frozen_reference = freeze_compiled_context_task_reference(
            request,
            context=context,
            query_document=query_document,
            compiled_task=compiled_task,
        )
        return _ContextTask(
            context=context,
            query_document=query_document,
            compiled_task=compiled_task,
            frozen_reference=frozen_reference,
        )

    # -- stage 2: recall and ranking ------------------------------------------

    def _recall_candidates(
        self,
        task: _SearchTask,
        warnings: list[str],
        search_control: ModelJobControl | None,
    ) -> _RecallResult:
        """Recall, fuse and rank candidates; a verified final reloads its seal."""
        request = task.request
        plan = task.plan
        verified_handoff = task.verified_handoff
        compiled_task = task.compiled_task
        snapshot_ids = [item.snapshot_id for item in task.snapshots]
        dense_snapshot_ids = [
            item.snapshot_id for item in task.snapshots if item.dense_complete
        ]

        query_values = [
            *(compiled_task.query_units if compiled_task is not None else []),
            *plan.must_terms,
            *plan.entities,
            *plan.optional_terms,
            plan.canonical_claim,
            _effective_plan_query(plan),
        ]
        if request.scope is not None and _time_unit_unrecognized(request.scope):
            _warn(
                warnings,
                "time filter unit is not recognized; no Claim can satisfy "
                "its numeric bounds",
            )
        lexical_hits: list[ChannelHit] = []
        lexical_status = "skipped" if verified_handoff is not None else "ok"
        channel_limit = (
            _FILTERED_CHANNEL_LIMIT
            if _has_explicit_constraints(request)
            else _CHANNEL_LIMIT
        )
        if verified_handoff is None:
            if search_control is not None:
                search_control.checkpoint()
            try:
                lexical_hits = self.store.lexical_search(
                    project_id=request.project_id,
                    snapshot_ids=snapshot_ids,
                    dense_model=self.dense_model,
                    dense_model_revision=self.dense_revision,
                    dense_dimension=self.dense_dimension,
                    query_values=query_values,
                    must_values=request.must_terms,
                    exclude_values=request.exclude_terms,
                    claim_types=request.claim_types,
                    polarity=request.polarity,
                    scope=request.scope,
                    limit=channel_limit,
                )
            except Exception:
                lexical_status = "degraded"
                _warn(warnings, "lexical retrieval unavailable; dense channel retained")

        dense_hits: list[ChannelHit] = []
        dense_status = "skipped" if verified_handoff is not None else "ok"
        if verified_handoff is None:
            try:
                if dense_snapshot_ids:
                    query_vector = self.dense_encoder.embed_query(
                        _dense_query_text(plan)
                    )
                    dense_hits = self.vector_index.search(
                        project_id=request.project_id,
                        snapshot_ids=dense_snapshot_ids,
                        vector=query_vector,
                        limit=channel_limit,
                        claim_types=request.claim_types,
                        polarity=request.polarity,
                        scope=request.scope,
                    )
            except Exception:
                dense_status = "degraded"
                _warn(warnings, "dense retrieval unavailable; lexical channel retained")

        ranking_feature_projections: (
            dict[tuple[str, str], LoadedRankingFeatureProjection] | None
        ) = (
            {}
            if verified_handoff is None and self.materialized_ranking_features_enabled
            else None
        )
        if verified_handoff is not None:
            candidates = self._load_verified_handoff_candidates(
                request, verified_handoff, snapshot_ids
            )
        else:
            fused = _rrf(lexical_hits, dense_hits, self.rrf_k)
            documents = self.store.load_documents(
                project_id=request.project_id,
                dense_model=self.dense_model,
                dense_model_revision=self.dense_revision,
                dense_dimension=self.dense_dimension,
                keys=list(fused),
                ranking_feature_sink=ranking_feature_projections,
                ranking_feature_predicate=(
                    lambda document: _matches_explicit_constraints(request, document)
                ),
            )
            lexical_scores = {
                (item.snapshot_id, item.claim_id): item.score for item in lexical_hits
            }
            dense_scores = {
                (item.snapshot_id, item.claim_id): item.score for item in dense_hits
            }
            candidates = [
                _Candidate(
                    document=documents[key],
                    lexical=lexical_scores.get(key),
                    dense=dense_scores.get(key),
                    rrf=score,
                )
                for key, score in fused.items()
                if key in documents
                and _matches_explicit_constraints(request, documents[key])
            ]
        if (
            verified_handoff is None
            and dense_hits
            and request.filters.corpus == "cross_paper"
            and not _has_explicit_constraints(request)
        ):
            _discount_sparse_cjk_lexical_channel(
                candidates,
                lexical_hits,
                request.query,
                self.rrf_k,
                feature_projections=ranking_feature_projections,
                feature_bindings=self.ranking_feature_bindings,
            )
        recall = _RecallResult(
            candidates=candidates,
            snapshot_ids=snapshot_ids,
            dense_snapshot_ids=dense_snapshot_ids,
            lexical_hits=lexical_hits,
            dense_hits=dense_hits,
            lexical_status=lexical_status,
            dense_status=dense_status,
        )
        if verified_handoff is None:
            self._rank_with_anchors(task, recall, ranking_feature_projections)

        # The selective reserve is deliberately downstream of every production
        # ranking rule.  It only seats raw-query lexical Top-1 and max-cosine
        # multi-view dense Top-1 candidates which are already in the baseline
        # pool.  Topic labels reviewed in the ontology, small pools, and final
        # requests carrying the caller's exact preview do no additional work.
        # Protected boundary seats cannot be evicted for any channel input, so
        # avoid optional retrieval when neither original seat can be used.
        if (
            verified_handoff is None
            and self.selective_safe_reserve_enabled
            and not request.candidate_refs
            and len(candidates) > _MODEL_RELATION_LIMIT
            and not _query_is_full_span_reviewed_ontology_label(
                request.query, self.ontology
            )
            and _selective_safe_reserve_has_capacity(
                candidates, protected_keys=recall.anchor_boosted_keys
            )
        ):
            self._apply_selective_safe_reserve(task, recall)
        return recall

    def _rank_with_anchors(
        self,
        task: _SearchTask,
        recall: _RecallResult,
        ranking_feature_projections: (
            dict[tuple[str, str], LoadedRankingFeatureProjection] | None
        ),
    ) -> None:
        """Anchor boosts, family diversity and the cross-language dense reserve."""
        request = task.request
        candidates = recall.candidates
        # Every anchor channel ranks against the same pre-boost order, and
        # their combined bonus per candidate is capped (RTS-12): many
        # matched concepts cannot outweigh leading both base channels.
        anchor_budget = _AnchorBoostBudget(
            base_order=sorted(candidates, key=_candidate_sort_key),
            cap=_ANCHOR_BOOST_CAP_CHANNELS / (self.rrf_k + 1),
        )
        recall.exact_anchor_hits = _fuse_exact_anchors(
            candidates,
            _query_anchor_tokens(request.query),
            self.rrf_k,
            boosted_keys=recall.anchor_boosted_keys,
            feature_projections=ranking_feature_projections,
            feature_bindings=self.ranking_feature_bindings,
            budget=anchor_budget,
        )
        recall.concept_anchor_hits = _fuse_concept_anchors(
            candidates,
            request.query,
            self.ontology,
            self.rrf_k,
            boosted_keys=recall.anchor_boosted_keys,
            query_scoped_pattern_scan=self.query_scoped_concept_pattern_scan_enabled,
            feature_projections=ranking_feature_projections,
            feature_bindings=self.ranking_feature_bindings,
            budget=anchor_budget,
        )
        recall.scientific_anchor_hits = _fuse_scientific_mentions(
            candidates,
            request.query,
            self.rrf_k,
            boosted_keys=recall.anchor_boosted_keys,
            feature_projections=ranking_feature_projections,
            feature_bindings=self.ranking_feature_bindings,
            budget=anchor_budget,
        )
        anchor_budget.apply()
        candidates.sort(key=_candidate_sort_key)
        if request.filters.corpus == "cross_paper":
            _apply_soft_family_diversity(candidates)
            candidates.sort(key=_candidate_sort_key)
            if (
                recall.lexical_status == "ok"
                and recall.dense_status == "ok"
                and not _has_explicit_constraints(request)
            ):
                _apply_guarded_cross_language_dense_reserve(
                    candidates,
                    recall.lexical_hits,
                    recall.dense_hits,
                    request.query,
                )
                candidates.sort(key=_candidate_sort_key)

    def _apply_selective_safe_reserve(
        self, task: _SearchTask, recall: _RecallResult
    ) -> None:
        """Seat raw-query lexical and multi-view dense Top-1 boundary candidates."""
        request = task.request
        raw_lexical_top1: list[ChannelHit] = []
        # This optional channel has no public diagnostic contract.  A
        # failure must leave the already-complete baseline untouched.
        if recall.lexical_status == "ok":
            with suppress(Exception):
                raw_lexical_top1 = self.store.lexical_search(
                    project_id=request.project_id,
                    snapshot_ids=recall.snapshot_ids,
                    dense_model=self.dense_model,
                    dense_model_revision=self.dense_revision,
                    dense_dimension=self.dense_dimension,
                    query_values=[request.query],
                    must_values=request.must_terms,
                    exclude_values=request.exclude_terms,
                    claim_types=request.claim_types,
                    polarity=request.polarity,
                    scope=request.scope,
                    limit=1,
                )

        def view_hits(query_vector: Sequence[float]) -> list[ChannelHit]:
            return self.vector_index.search(
                project_id=request.project_id,
                snapshot_ids=recall.dense_snapshot_ids,
                vector=query_vector,
                limit=_CHANNEL_LIMIT,
                claim_types=request.claim_types,
                polarity=request.polarity,
                scope=request.scope,
            )

        dense_view_hits: list[list[ChannelHit]] = []
        if recall.dense_status == "ok" and recall.dense_snapshot_ids:
            dense_view_hits.append(recall.dense_hits[:_CHANNEL_LIMIT])
            additional_query_texts = _additional_dense_query_texts(task.plan)
            batch_vectors = (
                self._batch_additional_dense_vectors(additional_query_texts)
                if self.selective_safe_dense_batch_enabled
                else None
            )
            if batch_vectors is None:
                for query_text in additional_query_texts:
                    try:
                        dense_view_hits.append(
                            view_hits(self.dense_encoder.embed_query(query_text))
                        )
                    except Exception:
                        # This is the unchanged per-view fallback: one bad
                        # optional view never discards successful siblings.
                        continue
            else:
                for query_vector in batch_vectors:
                    try:
                        dense_view_hits.append(view_hits(query_vector))
                    except Exception:
                        # Successful views remain usable; never degrade or
                        # replace the production dense channel on an exception.
                        continue
        aggregated_dense_hits = _aggregate_dense_view_hits(
            dense_view_hits, limit=_CHANNEL_LIMIT
        )
        _apply_selective_safe_top12_reserve(
            recall.candidates,
            raw_lexical_top1,
            aggregated_dense_hits,
            protected_keys=recall.anchor_boosted_keys,
        )

    # -- stage 3a: the preview ------------------------------------------------

    def _preview_response(
        self,
        task: _SearchTask,
        recall: _RecallResult,
        preview_candidates: list[_Candidate],
        warnings: list[str],
    ) -> HybridSearchResponse:
        """The visible preview, sealed by a signed handoff for its final stage."""
        request = task.request
        preview_channels = ChannelDiagnostics(
            lexical=StageDiagnostic(
                attempted=bool(recall.snapshot_ids),
                status=recall.lexical_status if recall.snapshot_ids else "skipped",
                provider="sqlite-fts5-bm25f",
                returned=len(recall.lexical_hits),
            ),
            dense=StageDiagnostic(
                attempted=bool(recall.dense_snapshot_ids),
                status=recall.dense_status if recall.dense_snapshot_ids else "skipped",
                provider=self.dense_model,
                returned=len(recall.dense_hits),
            ),
            rerank=StageDiagnostic(
                attempted=False,
                status="skipped",
                provider="gpt-second-stage",
                returned=0,
            ),
            relation=StageDiagnostic(
                attempted=False,
                status="skipped",
                provider="gpt",
                returned=0,
            ),
        )
        query_plan_handoff = (
            self._issue_query_plan_handoff(
                task.protocol_request,
                task.plan,
                task.snapshots,
                preview_candidates,
                **(
                    {
                        "context": task.context,
                        "query_document": task.query_document,
                        "frozen_reference": task.frozen_reference,
                    }
                    if task.is_context_protocol
                    else {}
                ),
            )
            if request.schema_version
            in (RETRIEVAL_SEARCH_SCHEMA_VERSION, RETRIEVAL_CONTEXT_SCHEMA_VERSION)
            else None
        )
        return HybridSearchResponse(
            schema_version=request.schema_version,
            stage="preview",
            result_status=_retrieval_result_status(
                preview_channels, visible_count=len(preview_candidates)
            ),
            query_plan=task.plan,
            channels=preview_channels,
            preview=[_preview_ref(item) for item in preview_candidates],
            clusters=[],
            warnings=warnings,
            retrieval_confidence=_retrieval_confidence_observation(
                request=request,
                preview_candidates=preview_candidates,
                dense_hits=recall.dense_hits,
                snapshot_ids=recall.snapshot_ids,
                dense_snapshot_ids=recall.dense_snapshot_ids,
                lexical_status=recall.lexical_status,
                dense_status=recall.dense_status,
                exact_anchor_hits=recall.exact_anchor_hits,
                concept_anchor_hits=recall.concept_anchor_hits,
                scientific_anchor_hits=recall.scientific_anchor_hits,
            ),
            query_plan_handoff=query_plan_handoff,
        )

    # -- stage 3b: the final --------------------------------------------------

    def _final_relation_candidates(
        self,
        task: _SearchTask,
        recall: _RecallResult,
        preview_candidates: list[_Candidate],
    ) -> list[_Candidate]:
        """The exact preview the caller saw (plus same-paper closure), in order."""
        request = task.request
        verified_handoff = task.verified_handoff
        retrieved_relation_candidates = (
            []
            if verified_handoff is not None and not request.candidate_refs
            else preview_candidates
        )
        if request.candidate_refs:
            allowed_order = [
                (item.snapshot_id, item.claim_id) for item in request.candidate_refs
            ]
            allowed = set(allowed_order)
            loaded_candidates = {
                (item.document.snapshot_id, item.document.claim_id): item
                for item in recall.candidates
                if (item.document.snapshot_id, item.document.claim_id) in allowed
            }
            missing = [key for key in allowed_order if key not in loaded_candidates]
            if missing:
                loaded_documents = self.store.load_documents(
                    project_id=request.project_id,
                    dense_model=self.dense_model,
                    dense_model_revision=self.dense_revision,
                    dense_dimension=self.dense_dimension,
                    keys=missing,
                )
                active_snapshots = set(recall.snapshot_ids)
                for rank, key in enumerate(allowed_order, start=1):
                    document = loaded_documents.get(key)
                    if (
                        document is None
                        or document.snapshot_id not in active_snapshots
                        or not _matches_explicit_constraints(request, document)
                    ):
                        continue
                    loaded_candidates[key] = _Candidate(
                        document=document,
                        lexical=None,
                        dense=None,
                        rrf=1.0 / (self.rrf_k + rank),
                    )
            if set(loaded_candidates) != allowed:
                raise InputError(
                    "AI candidate refs are no longer present in the active index"
                )
            # Candidate refs are the exact preview the caller already saw.  Do
            # not replace them with a second, potentially different retrieval.
            retrieved_relation_candidates = [
                loaded_candidates[key] for key in allowed_order
            ]
        if verified_handoff is not None:
            sealed_candidates = {
                (item.snapshot_id, item.claim_id): item
                for item in verified_handoff.ordered_candidates
            }
            for candidate in retrieved_relation_candidates:
                key = (
                    candidate.document.snapshot_id,
                    candidate.document.claim_id,
                )
                sealed = sealed_candidates.get(key)
                if (
                    sealed is None
                    or sealed.paper_id != candidate.document.paper_id
                    or sealed.paper_family_id != candidate.document.paper_family_id
                ):
                    raise _query_plan_handoff_error("sealed_candidate_mismatch")
                candidate.lexical = sealed.scores.lexical
                candidate.dense = sealed.scores.dense
                candidate.rrf = sealed.scores.rrf
                candidate.rerank = sealed.scores.rerank
        same_paper_candidates = self._same_paper_candidates(
            request, task.query_document
        )
        same_paper_keys = {
            (item.document.snapshot_id, item.document.claim_id)
            for item in same_paper_candidates
        }
        return [
            *same_paper_candidates,
            *[
                item
                for item in retrieved_relation_candidates
                if (item.document.snapshot_id, item.document.claim_id)
                not in same_paper_keys
            ],
        ]

    def _label_relation_candidates(
        self,
        task: _SearchTask,
        relation_candidates: list[_Candidate],
        warnings: list[str],
        *,
        search_started_at: float,
    ) -> _RelationOutcome:
        """Set each candidate's query-relative relation (model or topic routing)."""
        if not relation_candidates:
            return _RelationOutcome(status="skipped", attempted=False, provider="gpt")
        if (
            not interactive_search_enabled()
            and not task.is_context_protocol
            and task.plan.query_type in {"paper_lookup", "topic_search"}
        ):
            for candidate in relation_candidates:
                proposed = RelationDecision(
                    candidate_key=relation_candidate_key(
                        candidate.document.snapshot_id,
                        candidate.document.claim_id,
                    ),
                    relation="related",
                    relation_direction="result_to_query",
                    relation_confidence=None,
                    scope_relation="unknown",
                )
                candidate.decision = _safe_relation_decision(
                    task.plan, candidate.document, proposed
                )
                candidate.direction = candidate.decision.relation_direction
            return _RelationOutcome(
                status="ok", attempted=True, provider="deterministic-topic-routing"
            )
        model_decisions: dict[str, RelationDecision] = {}
        if self.relation_classifier is None:
            if interactive_search_enabled():
                raise ModelUnavailable("AI relation analysis is unavailable")
            status, provider = "degraded", "gpt"
            _warn(
                warnings,
                "AI relation reranking is unavailable; preview retained",
            )
        else:
            status, provider = "ok", self.relation_classifier.name
            try:
                model_decisions = self._model_relation_decisions(
                    task,
                    relation_candidates,
                    warnings,
                    search_started_at=search_started_at,
                )
            except Exception as exc:
                _log_relation_failure(exc)
                if interactive_search_enabled():
                    if isinstance(exc, CompilerError):
                        raise
                    raise ModelOutputError(
                        "AI relation analysis failed validation"
                    ) from None
                status = "degraded"
                _warn(
                    warnings,
                    (
                        "AI relation reference lacks required context; "
                        "preview retained"
                        if isinstance(exc, ContextRelationMissingInputError)
                        else "AI relation reranking failed; preview retained"
                    ),
                )
        self._apply_relation_decisions(task, relation_candidates, model_decisions)
        return _RelationOutcome(status=status, attempted=True, provider=provider)

    def _model_relation_decisions(
        self,
        task: _SearchTask,
        relation_candidates: list[_Candidate],
        warnings: list[str],
        *,
        search_started_at: float,
    ) -> dict[str, RelationDecision]:
        """One model labelling of every candidate, with exact coverage."""
        classifier = self.relation_classifier
        assert classifier is not None
        plan = task.plan
        decisions: list[RelationDecision] = []
        if task.is_context_protocol:
            if task.context is None or task.frozen_reference is None:
                raise ValueError("frozen task context is missing")
            decisions = self._classify_frozen_context_batches(
                plan,
                [item.document for item in relation_candidates],
                request=task.protocol_request,
                context=task.context,
                query_document=task.query_document,
                frozen_reference=task.frozen_reference,
                search_started_at=search_started_at,
                warnings=warnings,
            )
        else:
            with (
                retrieval_search_budget(
                    max_completion_calls=max(
                        1,
                        math.ceil(
                            len(relation_candidates) / _RELATION_CLASSIFIER_BATCH_LIMIT
                        ),
                    ),
                    started_at=search_started_at,
                )
                if interactive_search_enabled()
                else nullcontext()
            ):
                for offset in range(
                    0,
                    len(relation_candidates),
                    _RELATION_CLASSIFIER_BATCH_LIMIT,
                ):
                    batch = relation_candidates[
                        offset : offset + _RELATION_CLASSIFIER_BATCH_LIMIT
                    ]
                    decisions.extend(
                        classifier.classify(plan, [item.document for item in batch])
                    )
                    for warning in getattr(classifier, "last_warnings", ()):
                        _warn(warnings, warning)
        expected_decision_keys = [
            relation_candidate_key(
                item.document.snapshot_id,
                item.document.claim_id,
            )
            for item in relation_candidates
        ]
        returned_decision_keys = [item.candidate_key for item in decisions]
        if (
            len(returned_decision_keys) != len(expected_decision_keys)
            or len(set(returned_decision_keys)) != len(returned_decision_keys)
            or set(returned_decision_keys) != set(expected_decision_keys)
        ):
            raise ValueError("relation classifier candidate coverage mismatch")
        model_decisions = {item.candidate_key: item for item in decisions}
        decision_order = {
            item.candidate_key: index for index, item in enumerate(decisions)
        }
        for candidate in relation_candidates:
            candidate.ai_rank = decision_order.get(
                relation_candidate_key(
                    candidate.document.snapshot_id,
                    candidate.document.claim_id,
                )
            )
        return model_decisions

    def _apply_relation_decisions(
        self,
        task: _SearchTask,
        relation_candidates: list[_Candidate],
        model_decisions: dict[str, RelationDecision],
    ) -> None:
        """Attach model decisions (bounded by safety rules outside interactive)."""
        context_interpretation = (
            task.frozen_reference.interpretation
            if task.frozen_reference is not None
            else None
        )
        for candidate in relation_candidates:
            decision = model_decisions.get(
                relation_candidate_key(
                    candidate.document.snapshot_id,
                    candidate.document.claim_id,
                )
            )
            if decision is None or interactive_search_enabled():
                candidate.decision = decision
            elif task.is_context_protocol:
                candidate.decision = _safe_context_relation_decision(
                    context_interpretation, candidate.document, decision
                )
            else:
                candidate.decision = _safe_relation_decision(
                    task.plan, candidate.document, decision
                )
            if candidate.decision is not None:
                candidate.direction = candidate.decision.relation_direction
            # A seed closes the corpus, but its graph edge is not a
            # judgment about the history-resolved task. Keep that task
            # with the context classifier instead of replacing it.
            explicit = (
                None
                if task.is_context_protocol or interactive_search_enabled()
                else _explicit_relation(task.query_document, candidate.document)
            )
            if explicit is not None:
                candidate.decision, candidate.direction = explicit

    def _final_response(
        self,
        task: _SearchTask,
        recall: _RecallResult,
        relation_candidates: list[_Candidate],
        outcome: _RelationOutcome,
        warnings: list[str],
    ) -> HybridSearchResponse:
        """Order by the model ranking, then cluster the related candidates."""
        request = task.request
        relation_candidates.sort(
            key=lambda item: (
                item.ai_rank if item.ai_rank is not None else _MODEL_RELATION_LIMIT,
                _intent_relation_priority(
                    request.intent,
                    "unrelated" if item.decision is None else item.decision.relation,
                ),
                *_candidate_sort_key(item),
            )
        )

        clusters = _cluster_candidates(
            [
                item
                for item in relation_candidates
                if item.decision is not None and item.decision.relation != "unrelated"
            ],
            limit=request.filters.limit,
        )
        sealed_final = task.verified_handoff is not None
        channels = ChannelDiagnostics(
            lexical=StageDiagnostic(
                attempted=not sealed_final and bool(recall.snapshot_ids),
                status=recall.lexical_status if recall.snapshot_ids else "skipped",
                provider="sqlite-fts5-bm25f",
                returned=len(recall.lexical_hits),
            ),
            dense=StageDiagnostic(
                attempted=not sealed_final and bool(recall.dense_snapshot_ids),
                status=recall.dense_status if recall.dense_snapshot_ids else "skipped",
                provider=self.dense_model,
                returned=len(recall.dense_hits),
            ),
            rerank=StageDiagnostic(
                attempted=False,
                status="skipped",
                provider="gpt-second-stage",
                returned=0,
            ),
            relation=StageDiagnostic(
                attempted=outcome.attempted,
                status=outcome.status,
                provider=outcome.provider,
                returned=len(relation_candidates),
            ),
        )
        return HybridSearchResponse(
            schema_version=request.schema_version,
            stage="ai_rerank",
            result_status=_retrieval_result_status(
                channels, visible_count=len(clusters)
            ),
            query_plan=task.plan,
            channels=channels,
            preview=[],
            clusters=clusters,
            warnings=warnings,
        )

    def search_claim_relations(
        self,
        *,
        project_id: str,
        active_snapshot_ids: list[str] | None,
        query_claim: QueryClaimRef,
    ) -> HybridSearchResponse:
        """Run the existing preview→relation pipeline for one stored Claim."""

        reuse_preview = self._query_plan_handoff_key is not None
        seed = HybridSearchRequest(
            schema_version=(
                RETRIEVAL_SEARCH_SCHEMA_VERSION
                if reuse_preview
                else "evidence-retrieval/2.0"
            ),
            project_id=project_id,
            stage="preview",
            query=query_claim.claim_id,
            query_claim=query_claim,
            candidate_refs=[],
            intent="claim_search",
            must_terms=[],
            exclude_terms=[],
            scope=None,
            claim_types=[],
            polarity=None,
            filters=SearchFilters(
                corpus="cross_paper",
                paper_ids=[],
                paper_family_ids=[],
                active_snapshot_ids=active_snapshot_ids,
                # Bind the final cluster limit while retaining exactly the
                # existing twelve cross-paper candidates in this private pass.
                limit=50 if reuse_preview else 12,
            ),
        )
        query_document = self._query_document(seed)
        if query_document is None:
            raise InputError("query Claim is not indexed for the active dense model")
        seed = seed.model_copy(update={"query": query_document.canonical_claim})
        if reuse_preview:
            with retrieval_search_policy(interactive=False):
                preview = self._search(
                    seed, search_started_at=time.monotonic(), preview_limit=12
                )
        else:
            preview = self.search(seed, interactive=False)
        if not any(
            channel.status == "ok"
            for channel in (preview.channels.lexical, preview.channels.dense)
        ):
            raise InputError("Claim candidate retrieval is unavailable")
        candidates = [
            QueryClaimRef(snapshot_id=item.snapshot_id, claim_id=item.claim_id)
            for item in preview.preview
            if (item.snapshot_id, item.claim_id)
            != (query_claim.snapshot_id, query_claim.claim_id)
        ]
        final_request = seed.model_copy(
            update={
                "stage": "ai_rerank",
                "candidate_refs": candidates,
                "query_plan_handoff": preview.query_plan_handoff,
                "filters": seed.filters.model_copy(update={"limit": 50}),
            }
        )
        final = self.search(final_request, interactive=False)
        relation = final.channels.relation
        if relation.status == "skipped" and relation.returned == 0:
            return final
        if relation.status != "ok":
            raise InputError("Model Claim relation classification failed")
        return final

    def _plan(
        self,
        request: HybridSearchRequest,
        planning_request: HybridSearchRequest,
        query_document: ClaimSearchDocument | None,
        warnings: list[str],
        *,
        allow_remote: bool,
    ) -> QueryPlan:
        payload: QueryPlanPayload | None = None
        planner = "deterministic"
        interactive = interactive_search_enabled()
        if allow_remote and interactive and self.query_planner is None:
            # Interactive results come only from a validated model plan; a
            # deterministic plan would be sealed into the preview (RTS-1).
            raise RetrievalPlanningError("UNAVAILABLE")
        if allow_remote and self.query_planner is not None:
            try:
                payload = self.query_planner.plan(planning_request)
                for warning in getattr(self.query_planner, "last_warnings", ()):
                    _warn(warnings, warning)
                planner = "gpt"
            except Exception as exc:
                if interactive:
                    raise _planning_failure(exc) from None
                _warn(
                    warnings,
                    "query planner unavailable; deterministic plan retained",
                )
        if payload is None:
            payload = _deterministic_plan(planning_request)
        return _merge_explicit_plan(
            request, payload, planner, query_document=query_document
        )

    def _query_document(
        self, request: HybridSearchRequest
    ) -> ClaimSearchDocument | None:
        if request.query_claim is None:
            return None
        documents = self.store.load_documents(
            project_id=request.project_id,
            dense_model=self.dense_model,
            dense_model_revision=self.dense_revision,
            dense_dimension=self.dense_dimension,
            keys=[
                (
                    request.query_claim.snapshot_id,
                    request.query_claim.claim_id,
                )
            ],
        )
        return documents.get(
            (request.query_claim.snapshot_id, request.query_claim.claim_id)
        )

    def _same_paper_candidates(
        self,
        request: HybridSearchRequest,
        query_document: ClaimSearchDocument | None,
    ) -> list[_Candidate]:
        if query_document is None:
            return []
        documents = self.store.load_snapshot_documents(
            project_id=request.project_id,
            snapshot_id=query_document.snapshot_id,
            dense_model=self.dense_model,
            dense_model_revision=self.dense_revision,
            dense_dimension=self.dense_dimension,
        )
        return [
            _Candidate(
                document=document,
                lexical=None,
                dense=None,
                rrf=0.0,
                same_paper_default=True,
            )
            for document in documents
            if (
                document.snapshot_id,
                document.claim_id,
            )
            != (
                query_document.snapshot_id,
                query_document.claim_id,
            )
            and _matches_explicit_constraints(request, document)
        ]

    def _resolve_snapshots(
        self,
        request: HybridSearchRequest,
        query_document: ClaimSearchDocument | None,
        warnings: list[str],
    ) -> list[ResolvedSnapshot]:
        paper_ids = list(request.filters.paper_ids)
        if request.filters.corpus == "same_paper":
            if query_document is not None:
                paper_ids = [query_document.paper_id]
            elif not paper_ids and not request.filters.paper_family_ids:
                _warn(
                    warnings,
                    "same-paper search requires a query Claim, paper, or family",
                )
                return []
        selected = self.store.resolve_snapshots(
            project_id=request.project_id,
            index_version="evidence-retrieval/2.0",
            dense_model=self.dense_model,
            dense_model_revision=self.dense_revision,
            dense_dimension=self.dense_dimension,
            explicit_snapshot_ids=request.filters.active_snapshot_ids,
            paper_ids=paper_ids,
            paper_family_ids=request.filters.paper_family_ids,
        )
        if query_document is None:
            if request.query_claim is not None:
                _warn(
                    warnings,
                    "query Claim is not present in the active retrieval model",
                )
            return selected
        selected = [
            item
            for item in selected
            if item.paper_family_id != query_document.paper_family_id
        ]
        selected.sort(key=lambda item: item.snapshot_id)
        return selected


def _warn(warnings: list[str], value: str) -> None:
    if value not in warnings and len(warnings) < 32:
        warnings.append(value)


def _query_plan_handoff_error(reason: str) -> QueryPlanHandoffError:
    # The response stays non-oracular; only the private log names the check.
    logger.warning("Query plan handoff rejected: %s", reason)
    return QueryPlanHandoffError(
        "query plan handoff is invalid or expired; run preview again"
    )


# Fixed envelope messages raised above; other text (e.g. a pydantic error, which
# can quote request content) is reduced to its exception type.
_HANDOFF_ENVELOPE_REASONS = frozenset(
    {
        "unsupported handoff envelope",
        "handoff payload is too large",
        "invalid handoff signature size",
        "invalid handoff signature",
        "handoff payload must be an object",
        "handoff payload is not canonical",
        "handoff model is not canonical",
        "handoff key identity mismatch",
        "handoff protocol mismatch",
    }
)


def _handoff_envelope_reason(exc: Exception) -> str:
    message = str(exc)
    if type(exc) is ValueError and message in _HANDOFF_ENVELOPE_REASONS:
        return "envelope: " + message
    return "envelope: " + type(exc).__name__


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _query_plan_handoff_request_binding(request: HybridSearchRequest) -> str:
    return _canonical_sha256(
        {
            **(
                {
                    "clarification_selection": None
                    if request.clarification_selection is None
                    else request.clarification_selection.model_dump(mode="json")
                }
                if request.schema_version == RETRIEVAL_CONTEXT_SCHEMA_VERSION
                else {}
            ),
            **(
                {
                    "conversation_history": [
                        item.model_dump(mode="json")
                        for item in request.conversation_history
                    ],
                    "context_submission": (
                        None
                        if request.context_submission is None
                        else request.context_submission.model_dump(mode="json")
                    ),
                    "complete_filters": request.filters.model_dump(mode="json"),
                }
                if request.schema_version == RETRIEVAL_CONTEXT_SCHEMA_VERSION
                else {}
            ),
            "schema_version": request.schema_version,
            "project_id": request.project_id,
            "query": request.query,
            "query_claim": (
                None
                if request.query_claim is None
                else request.query_claim.model_dump(mode="json")
            ),
            "intent": request.intent,
            "must_terms": request.must_terms,
            "exclude_terms": request.exclude_terms,
            "scope": (
                None if request.scope is None else request.scope.model_dump(mode="json")
            ),
            "claim_types": request.claim_types,
            "polarity": request.polarity,
            "filters": {
                "corpus": request.filters.corpus,
                "paper_ids": request.filters.paper_ids,
                "paper_family_ids": request.filters.paper_family_ids,
                "limit": request.filters.limit,
            },
        }
    )


def _context_request_binding(request: HybridSearchRequest) -> str:
    return _query_plan_handoff_request_binding(
        request.model_copy(
            update={"clarification_selection": None, "context_submission": None}
        )
    )


def _effective_plan_query(plan: QueryPlan) -> str:
    return plan.resolved_query or plan.original_query


def _query_plan_handoff_mac_input(payload_bytes: bytes) -> bytes:
    return (
        _QUERY_PLAN_HANDOFF_MAC_DOMAIN
        + _QUERY_PLAN_HANDOFF_KEY_ID.encode("ascii")
        + b"\0"
        + payload_bytes
    )


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    if not value or "=" in value or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
        raise ValueError("invalid base64url")
    decoded = base64.b64decode(
        value + "=" * ((4 - len(value) % 4) % 4),
        altchars=b"-_",
        validate=True,
    )
    if _base64url_encode(decoded) != value:
        raise ValueError("non-canonical base64url")
    return decoded


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("duplicate JSON key")
        output[key] = value
    return output


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _unique(values: list[str], maximum: int) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = constraint_text(value)
        if not key or key in seen:
            continue
        output.append(value)
        seen.add(key)
        if len(output) >= maximum:
            break
    return output


def _planning_request(
    request: HybridSearchRequest,
    query_document: ClaimSearchDocument | None,
) -> HybridSearchRequest:
    if query_document is None:
        return request
    context_parts = [query_document.canonical_claim]
    if query_document.original_claim:
        context_parts.append("Original wording: " + query_document.original_claim)
    if query_document.entities:
        context_parts.append("Entities: " + "; ".join(query_document.entities))
    if normalized_text(request.query) != normalized_text(
        query_document.canonical_claim
    ):
        context_parts.append("User retrieval instruction: " + request.query)
    query_text = unicodedata.normalize("NFC", "\n".join(context_parts))[:8_000]
    seed_scope_filter = _scope_filter(query_document.scope)
    seed_scope = (
        {} if seed_scope_filter is None else _scope_filter_payload(seed_scope_filter)
    )
    if request.scope is not None:
        seed_scope.update(_scope_filter_payload(request.scope))
    payload = request.model_dump(mode="json")
    payload.update(
        {
            "query": query_text,
            "scope": seed_scope or None,
            "claim_types": request.claim_types or [query_document.claim_type],
            "polarity": request.polarity or query_document.polarity,
        }
    )
    return HybridSearchRequest.model_validate(payload, strict=True)


def _scope_filter_payload(scope: SearchScopeFilter) -> dict[str, Any]:
    """Keep sparse filters while preserving the closed nullable time shape."""

    payload = scope.model_dump(mode="json", exclude_none=True)
    if scope.time is not None:
        payload["time"] = scope.time.model_dump(mode="json", exclude_none=False)
    return payload


def _effective_query_type(request: HybridSearchRequest) -> str:
    """Route noun phrases as topics while preserving explicit Claim intents."""

    if request.query_claim is not None or request.intent != "claim_search":
        return request.intent
    return "topic_search" if _query_profile(request) == "topic" else "claim_search"


def _deterministic_plan(request: HybridSearchRequest) -> QueryPlanPayload:
    optional = lexical_tokens(request.query, maximum=64)
    quoted = re.findall(
        r"[\"'\u201c\u201d]([^\"'\u201c\u201d]{1,200})[\"'\u201c\u201d]",
        request.query,
    )
    entities = [
        *quoted,
        *re.findall(r"\b[A-Z][A-Z0-9._+-]{1,31}\b", request.query),
        *_query_anchor_tokens(request.query),
    ]
    polarity = request.polarity
    if polarity is None and has_negation(request.query):
        polarity = "negated"
    return QueryPlanPayload(
        query_type=_effective_query_type(request),
        canonical_claim=request.query,
        must_terms=request.must_terms,
        optional_terms=optional,
        exclude_terms=request.exclude_terms,
        entities=_unique(entities, 64),
        polarity=polarity,
        claim_types=request.claim_types,
        scope_constraints=request.scope,
    )


def _compiled_task_plan(
    request: HybridSearchRequest, compiled: CompiledTaskContract
) -> QueryPlan:
    """Render one task-owned plan without model-authored retrieval expansions.

    Caller eligibility remains on the original request at the actual stores.
    It does not become the proposition's polarity, scope, or source authority.
    Query units are separately consumed by lexical recall, not asserted to be
    independently executed question/answer tasks.
    """
    if compiled.status != "ready" or compiled.interpretation is None:
        raise ValueError("pending task cannot produce a retrieval plan")
    interpretation = compiled.interpretation
    return QueryPlan(
        query_type=_effective_query_type(request),
        original_query=request.query,
        resolved_query=compiled.retrieval_text,
        canonical_claim=interpretation.proposition or compiled.retrieval_text,
        must_terms=list(request.must_terms),
        optional_terms=[],
        exclude_terms=list(request.exclude_terms),
        entities=list(interpretation.entities),
        polarity=interpretation.polarity,
        claim_types=list(request.claim_types),
        scope_constraints=interpretation.scope,
        planner="gpt",
    )


def _validate_compiled_task_plan(
    plan: QueryPlan, request: HybridSearchRequest, compiled: CompiledTaskContract
) -> None:
    expected = _compiled_task_plan(request, compiled)
    if plan.model_dump(mode="json") != expected.model_dump(mode="json"):
        raise ValueError("retrieval plan does not match compiled execution task")


def _merge_explicit_plan(
    request: HybridSearchRequest,
    payload: QueryPlanPayload,
    planner: str,
    *,
    query_document: ClaimSearchDocument | None,
) -> QueryPlan:
    must_terms = _unique([*request.must_terms, *payload.must_terms], 64)
    excluded = _unique([*request.exclude_terms, *payload.exclude_terms], 64)
    excluded_keys = {constraint_text(item) for item in excluded}
    optional = [
        item
        for item in _unique(payload.optional_terms, 64)
        if constraint_text(item) not in excluded_keys
    ]
    scope_payload = (
        {}
        if payload.scope_constraints is None
        else _scope_filter_payload(payload.scope_constraints)
    )
    if request.scope is not None:
        scope_payload.update(_scope_filter_payload(request.scope))
    planned_scope = (
        None
        if not scope_payload
        else SearchScopeFilter.model_validate(scope_payload, strict=True)
    )
    scope = (
        planned_scope
        if planned_scope is not None and scope_values(planned_scope)
        else None
    )
    return QueryPlan(
        query_type=_effective_query_type(request),
        original_query=request.query,
        canonical_claim=(
            payload.canonical_claim
            if query_document is None
            else query_document.canonical_claim
        ),
        must_terms=must_terms,
        optional_terms=optional,
        exclude_terms=excluded,
        entities=_unique(
            [
                *(query_document.entities if query_document is not None else []),
                *payload.entities,
            ],
            64,
        ),
        polarity=request.polarity or payload.polarity,
        claim_types=request.claim_types or payload.claim_types,
        scope_constraints=scope,
        planner=planner,
    )


def _augment_plan_with_ontology(
    plan: QueryPlan, ontology: RetrievalOntology
) -> QueryPlan:
    """Add only reviewed labels; never traverse broader/related relations."""

    aliases = ontology.aliases_for_query(_effective_plan_query(plan))
    if not aliases:
        return plan
    return plan.model_copy(
        update={
            "optional_terms": _unique([*aliases, *plan.optional_terms], 64),
        }
    )


def _balanced_query_excerpt(value: str) -> str:
    """Keep the beginning, middle and end inside a 128-token encoder budget."""

    normalized = unicodedata.normalize("NFKC", value).strip()
    has_cjk = any(_is_cjk_character(character) for character in normalized)
    maximum = 180 if has_cjk else 360
    if len(normalized) <= maximum:
        return normalized
    separator = " … "
    available = maximum - len(separator) * 2
    head = available // 3
    middle = available // 3
    tail = available - head - middle
    midpoint = len(normalized) // 2
    middle_start = max(head, midpoint - middle // 2)
    middle_end = min(len(normalized) - tail, middle_start + middle)
    return separator.join(
        (
            normalized[:head].strip(),
            normalized[middle_start:middle_end].strip(),
            normalized[-tail:].strip(),
        )
    )


def _dense_query_text(plan: QueryPlan) -> str:
    # The installed multilingual bi-encoder is capped at 128 tokens.  Put
    # caller constraints and reviewed aliases first, then a balanced excerpt;
    # otherwise a long paragraph silently drops its middle/end and every alias.
    parts: list[str] = []
    if plan.must_terms:
        parts.append("Required terms: " + "; ".join(plan.must_terms))
    if plan.entities:
        parts.append("Entities: " + "; ".join(plan.entities))
    if plan.scope_constraints is not None:
        parts.append("Scope: " + "; ".join(scope_values(plan.scope_constraints)))
    if plan.optional_terms:
        parts.append("Expansion terms: " + "; ".join(plan.optional_terms[:16]))
    parts.append("Query: " + _balanced_query_excerpt(plan.canonical_claim))
    if normalized_text(_effective_plan_query(plan)) != normalized_text(
        plan.canonical_claim
    ):
        parts.append(
            "Original: " + _balanced_query_excerpt(_effective_plan_query(plan))
        )
    return "\n".join(parts)[:16_000]


def _additional_dense_query_texts(plan: QueryPlan) -> list[str]:
    """Return normalized-distinct balanced views beyond the production view."""

    production = _dense_query_text(plan)
    seen = {normalized_text(production)}
    proposals = [_balanced_query_excerpt(_effective_plan_query(plan))]
    if normalized_text(plan.canonical_claim) != normalized_text(
        _effective_plan_query(plan)
    ):
        proposals.append(_balanced_query_excerpt(plan.canonical_claim))
    views: list[str] = []
    for value in proposals:
        normalized = normalized_text(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        views.append(value)
    return views


def _aggregate_dense_view_hits(
    rankings: list[list[ChannelHit]], *, limit: int
) -> list[ChannelHit]:
    """Collapse finite dense views into one stable max-cosine ranking."""

    if limit < 1:
        return []
    best: dict[tuple[str, str], float] = {}
    for ranking in rankings:
        scored = [(hit, float(hit.score)) for hit in ranking]
        if any(not math.isfinite(score) for _, score in scored):
            # One invalid score makes the independent view untrustworthy.  Do
            # not allow another item from that view to become the reserve Top-1.
            continue
        for hit, score in scored:
            key = (hit.snapshot_id, hit.claim_id)
            previous = best.get(key)
            if previous is None or score > previous:
                best[key] = score
    return [
        ChannelHit(snapshot_id, claim_id, score)
        for (snapshot_id, claim_id), score in sorted(
            best.items(), key=lambda item: (-item[1], item[0][0], item[0][1])
        )[:limit]
    ]


def _query_is_full_span_reviewed_ontology_label(
    query: str, ontology: RetrievalOntology
) -> bool:
    """Return true only when one reviewed label covers the normalized query."""

    normalized = ontology_normalized_text(query)
    if not normalized:
        return False
    return any(
        match.start == 0 and match.end == len(normalized)
        for match in ontology.matches(query, suppress_nested=False)
    )


def _selective_safe_reserve_has_capacity(
    candidates: Sequence[_Candidate],
    *,
    protected_keys: set[tuple[str, str]],
) -> bool:
    """Check original boundary seats without changing candidate order or scores."""

    if len(candidates) <= _MODEL_RELATION_LIMIT:
        return False
    return any(
        (item.document.snapshot_id, item.document.claim_id) not in protected_keys
        for item in candidates[_MODEL_RELATION_LIMIT - 2 : _MODEL_RELATION_LIMIT]
    )


def _apply_selective_safe_top12_reserve(
    candidates: list[_Candidate],
    raw_lexical_hits: list[ChannelHit],
    aggregated_dense_hits: list[ChannelHit],
    *,
    protected_keys: set[tuple[str, str]],
) -> None:
    """Seat eligible channel Top-1s while retaining baseline scores and order.

    Only candidates already present in the baseline pool can qualify.  A
    missing Top-1 is not replaced by Top-2.  The two roles are considered in
    raw-lexical then aggregated-dense order, and duplicate roles share a seat.
    Only unprotected baseline ranks 11 and 12 can ever be used as seats.
    """

    if len(candidates) <= _MODEL_RELATION_LIMIT:
        return
    baseline_keys = [
        (item.document.snapshot_id, item.document.claim_id) for item in candidates
    ]
    if len(baseline_keys) != len(set(baseline_keys)):
        return
    candidates_by_key = dict(zip(baseline_keys, candidates, strict=True))
    requested: list[tuple[str, str]] = []
    for ranking in (raw_lexical_hits, aggregated_dense_hits):
        if not ranking:
            continue
        key = (ranking[0].snapshot_id, ranking[0].claim_id)
        if key in candidates_by_key and key not in requested:
            requested.append(key)
    if not requested:
        return

    baseline_top_keys = set(baseline_keys[:_MODEL_RELATION_LIMIT])
    protected_top_keys = baseline_top_keys.intersection(protected_keys)
    required_keys = set(requested)
    seat_keys = set(baseline_keys[_MODEL_RELATION_LIMIT - 2 : _MODEL_RELATION_LIMIT])
    top = list(candidates[:_MODEL_RELATION_LIMIT])
    top_keys = set(baseline_keys[:_MODEL_RELATION_LIMIT])
    promoted = False
    for key in requested:
        if key in top_keys:
            continue
        eviction_index = next(
            (
                index
                for index in range(
                    _MODEL_RELATION_LIMIT - 1,
                    _MODEL_RELATION_LIMIT - 3,
                    -1,
                )
                if (
                    top[index].document.snapshot_id,
                    top[index].document.claim_id,
                )
                in seat_keys
                and (
                    top[index].document.snapshot_id,
                    top[index].document.claim_id,
                )
                not in protected_top_keys
                and (
                    top[index].document.snapshot_id,
                    top[index].document.claim_id,
                )
                not in required_keys
            ),
            None,
        )
        if eviction_index is None:
            continue
        evicted = top.pop(eviction_index)
        top_keys.remove((evicted.document.snapshot_id, evicted.document.claim_id))
        top.append(candidates_by_key[key])
        top_keys.add(key)
        promoted = True
    if not promoted:
        return
    tail = [
        item
        for item in candidates
        if (item.document.snapshot_id, item.document.claim_id) not in top_keys
    ]
    candidates[:] = [*top, *tail]


def _rrf(
    lexical: list[ChannelHit], dense: list[ChannelHit], k: int
) -> dict[tuple[str, str], float]:
    scores: dict[tuple[str, str], float] = {}
    for ranking in (lexical, dense):
        seen: set[tuple[str, str]] = set()
        for rank, item in enumerate(ranking, start=1):
            key = (item.snapshot_id, item.claim_id)
            if key in seen:
                continue
            seen.add(key)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    return dict(sorted(scores.items(), key=lambda item: (-item[1], item[0])))


def _is_cjk_character(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x3040 <= codepoint <= 0x30FF
        or 0xAC00 <= codepoint <= 0xD7AF
    )


def _cjk_query_bigrams(value: str) -> set[str]:
    return {
        token
        for token in lexical_tokens(value, maximum=128)
        if len(token) == 2 and all(_is_cjk_character(item) for item in token)
    }


def _materialized_feature_values(
    projections: MappingRankingFeatures | None,
    key: tuple[str, str],
    feature_name: str,
    bindings: RankingFeatureBindings | None,
) -> tuple[str, ...] | None:
    if projections is None or bindings is None:
        return None
    return validated_feature_values(
        projections.get(key), feature_name, bindings=bindings
    )


def _discount_sparse_cjk_lexical_channel(
    candidates: list[_Candidate],
    lexical_hits: list[ChannelHit],
    query: str,
    k: int,
    *,
    feature_projections: MappingRankingFeatures | None = None,
    feature_bindings: RankingFeatureBindings | None = None,
) -> None:
    """Stop one common CJK bigram from overpowering multilingual dense recall.

    CJK FTS intentionally uses adjacent bigrams joined by OR.  That is useful
    for recall, but a long question can otherwise give a full rank-fusion vote
    to several Claims which share only a generic suffix such as ``盆地`` while
    the exact cross-language evidence is found by the dense channel.  Discount
    only this fail-closed pattern: a long unquoted CJK query, no lexical result
    matching two query bigrams, and the singleton term recurring in at least
    three candidate documents.  Rare singleton names and every multi-bigram
    match retain the original lexical score.
    """

    if not candidates or not lexical_hits or any(mark in query for mark in "\"'“”‘’"):
        return
    query_bigrams = _cjk_query_bigrams(query)
    if len(query_bigrams) < _SPARSE_CJK_MIN_QUERY_BIGRAMS:
        return

    tokens_by_key: dict[tuple[str, str], set[str]] = {}
    for item in candidates:
        key = (item.document.snapshot_id, item.document.claim_id)
        values = _materialized_feature_values(
            feature_projections, key, "sparse_cjk_tokens", feature_bindings
        )
        tokens_by_key[key] = (
            set(values)
            if values is not None
            else set(
                lexical_tokens("\n".join(lexical_fields(item.document)), maximum=4_096)
            )
        )
    overlap_by_key = {
        key: query_bigrams.intersection(tokens) for key, tokens in tokens_by_key.items()
    }
    lexical_keys = [
        (item.snapshot_id, item.claim_id)
        for item in lexical_hits
        if (item.snapshot_id, item.claim_id) in overlap_by_key
    ]
    if not lexical_keys or max(len(overlap_by_key[key]) for key in lexical_keys) > 1:
        return

    document_frequency = {
        token: sum(
            token in document_tokens for document_tokens in tokens_by_key.values()
        )
        for token in query_bigrams
    }
    candidates_by_key = {
        (item.document.snapshot_id, item.document.claim_id): item for item in candidates
    }
    for rank, key in enumerate(lexical_keys, start=1):
        overlap = overlap_by_key[key]
        if len(overlap) != 1:
            continue
        token = next(iter(overlap))
        if document_frequency[token] < _SPARSE_CJK_COMMON_DOCUMENTS:
            continue
        lexical_contribution = 1.0 / (k + rank)
        candidate = candidates_by_key[key]
        candidate.rrf -= lexical_contribution * (1.0 - _SPARSE_CJK_LEXICAL_WEIGHT)


def _apply_soft_family_diversity(candidates: list[_Candidate]) -> None:
    """Gently reduce only the fourth and later result from one paper family.

    A paper can legitimately contribute several atomic Claims, so this is not
    a family cap.  The small monotonic decay only breaks near-boundary crowding
    when another paper has almost the same fused score.
    """

    if len({item.document.paper_family_id for item in candidates}) < 2:
        return
    counts: dict[str, int] = {}
    for item in candidates:
        family_id = item.document.paper_family_id
        count = counts.get(family_id, 0) + 1
        counts[family_id] = count
        overflow = count - _SOFT_FAMILY_DIVERSITY_START
        if overflow > 0:
            item.rrf *= _SOFT_FAMILY_DIVERSITY_FACTOR**overflow


def _apply_guarded_cross_language_dense_reserve(
    candidates: list[_Candidate],
    lexical_hits: list[ChannelHit],
    dense_hits: list[ChannelHit],
    query: str,
) -> None:
    """Promote one exceptionally clear cross-language dense result to rank 3.

    The reserve is intentionally fail-closed: only dense rank 1 can qualify,
    it must have a strong absolute score and margin, be absent from the full
    lexical channel, use a different script from a sufficiently long CJK
    query, and introduce a paper family not already represented in the Top 3.
    """

    if (
        len(candidates) < 3
        or len(dense_hits) < 2
        or any(mark in query for mark in "\"'“”‘’")
        or len(_cjk_query_bigrams(query)) < _SPARSE_CJK_MIN_QUERY_BIGRAMS
    ):
        return
    dense_first, dense_second = dense_hits[:2]
    if (
        dense_first.score < _CROSS_LANGUAGE_DENSE_MIN_SCORE
        or dense_first.score - dense_second.score < _CROSS_LANGUAGE_DENSE_MIN_MARGIN
    ):
        return
    key = (dense_first.snapshot_id, dense_first.claim_id)
    if key in {(item.snapshot_id, item.claim_id) for item in lexical_hits}:
        return
    candidate = next(
        (
            item
            for item in candidates
            if (item.document.snapshot_id, item.document.claim_id) == key
        ),
        None,
    )
    if candidate is None or candidate.rerank is not None:
        return
    claim_text = "\n".join(
        (candidate.document.canonical_claim, candidate.document.original_claim or "")
    )
    if any(_is_cjk_character(character) for character in claim_text):
        return
    top_three_families = {item.document.paper_family_id for item in candidates[:3]}
    if candidate.document.paper_family_id in top_three_families:
        return
    current_rank = next(
        index for index, item in enumerate(candidates, start=1) if item is candidate
    )
    if current_rank <= 3:
        return
    second_score = candidates[1].rank_value()
    third_score = candidates[2].rank_value()
    if second_score <= third_score:
        return
    candidate.rrf = (second_score + third_score) / 2.0


_LATIN_QUERY_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9._+-]{2,63}")


def _query_anchor_tokens(value: str) -> list[str]:
    """Extract explicit technical identifiers without asking for query filters.

    Latin islands inside a CJK question are unusually informative (for example
    TopoInf and GNN).  In an English question, stay conservative and retain
    acronym/camel-case/version-shaped identifiers only.
    """

    has_cjk = any(
        "\u3400" <= character <= "\u9fff" or "\uf900" <= character <= "\ufaff"
        for character in value
    )
    anchors: list[str] = []
    for match in _LATIN_QUERY_TOKEN_RE.finditer(value):
        raw = match.group(0).strip("._+-")
        normalized = normalized_text(raw)
        if not normalized:
            continue
        looks_technical = (
            has_cjk
            or raw.isupper()
            or any(character.isupper() for character in raw[1:])
            or any(character.isdigit() for character in raw)
            or any(character in "._+-" for character in raw)
        )
        if looks_technical and normalized not in anchors:
            anchors.append(normalized)
        if len(anchors) >= 8:
            break
    return anchors


def _retrieval_confidence_observation(
    *,
    request: HybridSearchRequest,
    preview_candidates: list[_Candidate],
    dense_hits: list[ChannelHit],
    snapshot_ids: list[str],
    dense_snapshot_ids: list[str],
    lexical_status: str,
    dense_status: str,
    exact_anchor_hits: int,
    concept_anchor_hits: int,
    scientific_anchor_hits: int,
) -> RetrievalConfidence:
    """Observe bounded retrieval signals without making a policy decision.

    Only the raw user query is used for direct lexical coverage.  Planner
    translations and ontology expansion therefore cannot manufacture this
    observation.  The function is read-only over candidates and channel hits.
    """

    query_tokens = set(lexical_tokens(request.query, maximum=256))
    document_tokens: set[str] = set()
    if preview_candidates:
        document_tokens = set(
            lexical_tokens(
                dense_document_text(preview_candidates[0].document),
                maximum=4_096,
            )
        )
    direct_overlap = len(query_tokens.intersection(document_tokens))
    direct_coverage = direct_overlap / len(query_tokens) if query_tokens else 0.0

    dense_values, invalid_dense_scores = _bounded_dense_observations(dense_hits)
    dense_top1 = dense_values[0] if dense_values else None
    dense_top2 = dense_values[1] if len(dense_values) >= 2 else None
    dense_margin = (
        dense_top1 - dense_top2
        if dense_top1 is not None and dense_top2 is not None
        else None
    )
    dense_top3_mean = sum(dense_values) / len(dense_values) if dense_values else None

    reasons: list[str] = []
    if _has_explicit_constraints(request):
        reasons.append("explicit_constraints")
    if request.query_claim is not None:
        reasons.append("query_claim")
    if request.filters.corpus != "cross_paper":
        reasons.append("restricted_corpus")
    if not snapshot_ids:
        reasons.append("no_active_snapshots")
    if lexical_status != "ok" or not snapshot_ids:
        reasons.append("lexical_channel_unavailable")
    if dense_status != "ok" or not dense_snapshot_ids:
        reasons.append("dense_channel_unavailable")
    if invalid_dense_scores:
        reasons.append("invalid_dense_scores")

    strong_signal_types: list[str] = []
    if exact_anchor_hits:
        strong_signal_types.append("exact_anchor")
    if concept_anchor_hits:
        strong_signal_types.append("concept_anchor")
    if scientific_anchor_hits:
        strong_signal_types.append("scientific_anchor")

    normalized_query = unicodedata.normalize("NFKC", request.query).strip()
    return RetrievalConfidence(
        applicability="policy_inapplicable" if reasons else "applicable",
        policy_inapplicable_reasons=reasons,
        query_profile=_query_profile(request),
        long_query_truncated=(
            _balanced_query_excerpt(request.query) != normalized_query
        ),
        direct_lexical_query_tokens=len(query_tokens),
        direct_lexical_overlap=direct_overlap,
        direct_lexical_coverage=direct_coverage,
        exact_anchor_hits=min(exact_anchor_hits, 8),
        concept_anchor_hits=min(concept_anchor_hits, 8_192),
        scientific_anchor_hits=min(scientific_anchor_hits, 8_192),
        strong_signal_types=strong_signal_types,
        dense_observed_hits=len(dense_values),
        dense_top1=dense_top1,
        dense_top2=dense_top2,
        dense_margin=dense_margin,
        dense_top3_mean=dense_top3_mean,
    )


def _bounded_dense_observations(
    dense_hits: list[ChannelHit],
) -> tuple[list[float], bool]:
    values: list[float] = []
    for item in dense_hits[:3]:
        value = float(item.score)
        if not math.isfinite(value) or not -2.0 <= value <= 2.0:
            return [], True
        values.append(value)
    if any(left < right for left, right in zip(values, values[1:], strict=False)):
        return [], True
    return values, False


def _query_profile(request: HybridSearchRequest) -> str:
    """Describe query shape for stratified calibration, never for ranking."""

    if request.query_claim is not None:
        return "claim"
    value = normalized_text(request.query)
    if (
        _CLAIM_PROFILE_RE.search(value)
        or _UP_DIRECTION_RE.search(value)
        or _DOWN_DIRECTION_RE.search(value)
    ):
        return "claim"
    cjk_characters = sum(_is_cjk_character(character) for character in value)
    latin_tokens = re.findall(r"[a-z0-9][a-z0-9._+-]*", value)
    return "claim" if cjk_characters >= 18 or len(latin_tokens) >= 8 else "topic"


_ANCHOR_BOOST_CAP_CHANNELS = 2.0


class _AnchorBoostBudget:
    """Shared base order and capped total for the anchor rank channels."""

    def __init__(self, *, base_order: list[_Candidate], cap: float) -> None:
        self.base_order = base_order
        self.cap = cap
        self._totals: dict[int, float] = {}
        self._candidates: dict[int, _Candidate] = {}

    def add(self, candidate: _Candidate, boost: float) -> None:
        key = id(candidate)
        self._candidates[key] = candidate
        self._totals[key] = self._totals.get(key, 0.0) + boost

    def apply(self) -> None:
        for key, total in self._totals.items():
            self._candidates[key].rrf += min(total, self.cap)
        self._totals.clear()


def _boost(
    item: _Candidate, value: float, budget: _AnchorBoostBudget | None
) -> None:
    if budget is None:
        item.rrf += value
    else:
        budget.add(item, value)


def _fuse_exact_anchors(
    candidates: list[_Candidate],
    anchors: list[str],
    k: int,
    *,
    boosted_keys: set[tuple[str, str]] | None = None,
    feature_projections: MappingRankingFeatures | None = None,
    feature_bindings: RankingFeatureBindings | None = None,
    budget: _AnchorBoostBudget | None = None,
) -> int:
    """Add one exact-match rank channel per distinct technical concept."""

    if not candidates or not anchors:
        return 0
    anchor_groups: list[list[str]] = []
    for anchor in anchors:
        overlapping = [
            index
            for index, group in enumerate(anchor_groups)
            if any(
                anchor in existing.split("-") or existing in anchor.split("-")
                for existing in group
            )
        ]
        if not overlapping:
            anchor_groups.append([anchor])
            continue
        group = anchor_groups[overlapping[0]]
        group.append(anchor)
        for index in reversed(overlapping[1:]):
            group.extend(anchor_groups.pop(index))
    document_texts = {
        (item.document.snapshot_id, item.document.claim_id): normalized_text(
            dense_document_text(item.document)
        )
        for item in candidates
    }
    document_tokens: dict[tuple[str, str], set[str]] = {}
    for key, text in document_texts.items():
        values = _materialized_feature_values(
            feature_projections, key, "exact_anchor_tokens", feature_bindings
        )
        document_tokens[key] = (
            set(values)
            if values is not None
            else set(lexical_tokens(text, maximum=4_096))
        )
    base_order = (
        budget.base_order
        if budget is not None
        else sorted(candidates, key=_candidate_sort_key)
    )
    hit_count = 0
    for group in anchor_groups:
        matched = [
            item
            for item in base_order
            if any(
                anchor
                in document_tokens[(item.document.snapshot_id, item.document.claim_id)]
                for anchor in group
            )
        ]
        if not matched:
            phrase_options = [
                phrase_matches
                for anchor in group
                if (
                    phrase_matches := _unambiguous_anchor_phrase_matches(
                        anchor,
                        base_order,
                        document_tokens,
                        document_texts,
                    )
                )
            ]
            # A grouped concept receives at most one equivalent phrase channel.
            # Multiple qualifying spellings are ambiguous, so retain no bonus.
            if len(phrase_options) == 1:
                matched = phrase_options[0]
        if matched:
            hit_count += 1
        for rank, item in enumerate(matched, start=1):
            _boost(item, 1.0 / (k + rank), budget)
            if boosted_keys is not None:
                boosted_keys.add((item.document.snapshot_id, item.document.claim_id))
    return hit_count


def _fuse_concept_anchors(
    candidates: list[_Candidate],
    query: str,
    ontology: RetrievalOntology,
    k: int,
    *,
    boosted_keys: set[tuple[str, str]] | None = None,
    query_scoped_pattern_scan: bool = False,
    feature_projections: MappingRankingFeatures | None = None,
    feature_bindings: RankingFeatureBindings | None = None,
    budget: _AnchorBoostBudget | None = None,
) -> int:
    """Fuse one deterministic rank channel per reviewed query concept.

    Documents must contain an explicit label for the same concept.  Ontology
    hierarchy and related edges are not traversed, so a close term cannot gain
    the exact-alias bonus merely because it is nearby in the concept graph.
    """

    query_concepts = ontology.concepts_in(query)
    if not candidates or not query_concepts:
        return 0
    query_ids = {item.concept_id for item in query_concepts}
    base_order = (
        budget.base_order
        if budget is not None
        else sorted(candidates, key=_candidate_sort_key)
    )
    document_ids: dict[tuple[str, str], set[str]] = {}
    for item in base_order:
        key = (item.document.snapshot_id, item.document.claim_id)
        values = (
            _materialized_feature_values(
                feature_projections,
                key,
                "ontology_concept_ids",
                feature_bindings,
            )
            if query_scoped_pattern_scan
            else None
        )
        document_ids[key] = (
            query_ids.intersection(values)
            if values is not None
            else _document_query_concept_ids(
                item.document,
                ontology,
                query_ids,
                query_scoped_pattern_scan=query_scoped_pattern_scan,
            )
        )
    hit_count = 0
    for concept_id in sorted(query_ids):
        matched = [
            item
            for item in base_order
            if concept_id
            in document_ids[(item.document.snapshot_id, item.document.claim_id)]
        ]
        if matched:
            hit_count += 1
        for rank, item in enumerate(matched, start=1):
            _boost(item, 1.0 / (k + rank), budget)
            if boosted_keys is not None:
                boosted_keys.add((item.document.snapshot_id, item.document.claim_id))
    return hit_count


def _document_query_concept_ids(
    document: ClaimSearchDocument,
    ontology: RetrievalOntology,
    query_ids: set[str],
    *,
    query_scoped_pattern_scan: bool,
) -> set[str]:
    """Resolve document membership, falling back to the exact legacy scan."""

    text = concept_document_text(document)
    if query_scoped_pattern_scan:
        try:
            return ontology.query_scoped_concept_ids(text, query_ids)
        except Exception:
            # This optimization has no public diagnostic surface.  Its failure
            # must preserve the legacy membership and response byte-for-byte.
            pass
    return {
        concept.concept_id
        for concept in ontology.concepts_in(text, suppress_nested=False)
    }


def _fuse_scientific_mentions(
    candidates: list[_Candidate],
    query: str,
    k: int,
    *,
    boosted_keys: set[tuple[str, str]] | None = None,
    feature_projections: MappingRankingFeatures | None = None,
    feature_bindings: RankingFeatureBindings | None = None,
    budget: _AnchorBoostBudget | None = None,
) -> int:
    """Give exact number+comparison+unit mentions their own rank channels."""

    query_mentions = tuple(sorted(scientific_mention_keys(query)))
    if not candidates or not query_mentions:
        return 0
    base_order = (
        budget.base_order
        if budget is not None
        else sorted(candidates, key=_candidate_sort_key)
    )
    document_mentions: dict[tuple[str, str], set[str]] = {}
    for item in base_order:
        key = (item.document.snapshot_id, item.document.claim_id)
        values = _materialized_feature_values(
            feature_projections, key, "scientific_mention_keys", feature_bindings
        )
        document_mentions[key] = (
            set(values)
            if values is not None
            else scientific_mention_keys(dense_document_text(item.document))
        )
    hit_count = 0
    for mention in query_mentions:
        matched = [
            item
            for item in base_order
            if mention
            in document_mentions[(item.document.snapshot_id, item.document.claim_id)]
        ]
        if matched:
            hit_count += 1
        for rank, item in enumerate(matched, start=1):
            _boost(item, 1.0 / (k + rank), budget)
            if boosted_keys is not None:
                boosted_keys.add((item.document.snapshot_id, item.document.claim_id))
    return hit_count


def _unambiguous_anchor_phrase_matches(
    anchor: str,
    candidates: list[_Candidate],
    document_tokens: dict[tuple[str, str], set[str]],
    document_texts: dict[tuple[str, str], str],
) -> list[_Candidate]:
    """Bridge one technical ``hyphen-token`` to an exact spaced phrase.

    This is deliberately fail-closed.  It applies only when the existing
    technical anchor has no exact-token match, every ASCII component is long
    enough to be meaningful, the contiguous phrase resolves to one paper
    family, and the rarest component occurs nowhere outside those matches.
    """

    if re.fullmatch(r"[a-z]{3,}(?:-[a-z]{3,})+", anchor) is None:
        return []
    if any(anchor in tokens for tokens in document_tokens.values()):
        return []
    components = tuple(anchor.split("-"))
    phrase = re.compile(
        r"(?<![a-z0-9_])"
        + r"\s+".join(re.escape(component) for component in components)
        + r"(?![a-z0-9_])"
    )

    matched = [
        item
        for item in candidates
        if phrase.search(
            document_texts[(item.document.snapshot_id, item.document.claim_id)]
        )
    ]
    if not matched or len({item.document.paper_family_id for item in matched}) != 1:
        return []
    component_frequencies = [
        sum(component in tokens for tokens in document_tokens.values())
        for component in components
    ]
    if min(component_frequencies) != len(matched):
        return []
    return matched


def _candidate_sort_key(candidate: _Candidate) -> tuple[Any, ...]:
    return (
        -candidate.rank_value(),
        candidate.document.snapshot_id,
        candidate.document.claim_id,
    )


def _intent_relation_priority(intent: str, relation: str) -> int:
    if intent == "contradiction_search":
        order = {
            "contradicts": 0,
            "qualifies": 1,
            "refines": 1,
            "related": 2,
            "supplements": 2,
            "equivalent": 3,
            "supports": 3,
            "unrelated": 4,
        }
    elif intent == "supporting_evidence":
        order = {
            "supports": 0,
            "equivalent": 1,
            "qualifies": 2,
            "refines": 2,
            "supplements": 2,
            "related": 3,
            "contradicts": 4,
            "unrelated": 5,
        }
    else:
        order = {
            "equivalent": 0,
            "supports": 1,
            "contradicts": 1,
            "qualifies": 2,
            "refines": 2,
            "supplements": 2,
            "related": 3,
            "unrelated": 4,
        }
    return order.get(relation, 9)


def _retrieval_result_status(
    channels: ChannelDiagnostics, *, visible_count: int
) -> str:
    """Report operational result state, never scientific truth or completeness."""

    if any(
        diagnostic.status == "degraded"
        for diagnostic in (
            channels.lexical,
            channels.dense,
            channels.rerank,
            channels.relation,
        )
    ):
        return "degraded"
    return "matches" if visible_count else "no_direct_answer"


def _has_explicit_constraints(request: HybridSearchRequest) -> bool:
    return bool(
        request.must_terms
        or request.exclude_terms
        or request.scope is not None
        or request.claim_types
        or request.polarity is not None
    )


def _matches_explicit_constraints(
    request: HybridSearchRequest, document: ClaimSearchDocument
) -> bool:
    if request.must_terms or request.exclude_terms:
        tokens = content_token_set(document)
        if not all(term_present(item, tokens) for item in request.must_terms):
            return False
        if any(term_present(item, tokens) for item in request.exclude_terms):
            return False
    if request.claim_types and document.claim_type not in request.claim_types:
        return False
    if request.polarity is not None and document.polarity != request.polarity:
        return False
    return request.scope is None or _scope_hard_match(request.scope, document.scope)


def _scope_hard_match(query: SearchScopeFilter, candidate: SearchScope) -> bool:
    """Apply only caller-explicit filters as exact constraints."""

    for name in (
        "location",
        "formation",
        "sample_type",
        "method",
        "condition",
        "measurement",
        "entities",
    ):
        left = _hard_scope_dimension_values(query, name)
        if not left:
            continue
        right = _hard_scope_dimension_values(candidate, name)
        if not right or left.isdisjoint(right):
            return False
    if query.time is not None:
        if candidate.time is None:
            return False
        if query.time.label and (
            not candidate.time.label
            or constraint_text(query.time.label)
            != constraint_text(candidate.time.label)
        ):
            return False
        numeric_disjoint = _time_disjoint(query.time, candidate.time)
        if numeric_disjoint is True:
            return False
        if (
            query.time.start is not None or query.time.end is not None
        ) and numeric_disjoint is None:
            return False
    return not query.uncertainty or bool(
        candidate.uncertainty
        and constraint_text(query.uncertainty) == constraint_text(candidate.uncertainty)
    )


def _scope_dimension_values(scope: Any, name: str) -> set[str]:
    value = getattr(scope, name, None)
    if not value:
        return set()
    return {normalized for item in value if (normalized := normalized_text(item))}


def _hard_scope_dimension_values(scope: Any, name: str) -> set[str]:
    value = getattr(scope, name, None)
    if not value:
        return set()
    return {normalized for item in value if (normalized := constraint_text(item))}


def _time_interval_years(value: Any) -> tuple[float, float] | None:
    if value is None:
        return None
    return time_interval_years(value.start, value.end, value.unit)


def _time_unit_unrecognized(scope: SearchScopeFilter) -> bool:
    time = scope.time
    return bool(
        time is not None
        and (time.start is not None or time.end is not None)
        and time_unit_factor(time.unit) is None
    )


def _time_disjoint(left: Any, right: Any) -> bool | None:
    left_interval = _time_interval_years(left)
    right_interval = _time_interval_years(right)
    if left_interval is None or right_interval is None:
        return None
    return left_interval[1] < right_interval[0] or right_interval[1] < left_interval[0]


def _scope_relation(query: SearchScopeFilter, candidate: SearchScope) -> str:
    compared = 0
    all_equal = True
    query_within = True
    candidate_within = True
    unknown = False
    for name in (
        "location",
        "formation",
        "sample_type",
        "method",
        "condition",
        "measurement",
        "entities",
    ):
        left = _scope_dimension_values(query, name)
        if not left:
            continue
        compared += 1
        right = _scope_dimension_values(candidate, name)
        if not right:
            unknown = True
            all_equal = False
            continue
        if left.isdisjoint(right):
            # Free-text scientific labels are not ontology IDs: e.g. “华南” and
            # “South China” may denote the same Scope.  Only the explicit caller
            # filter above treats an exact mismatch as a hard exclusion.
            unknown = True
            all_equal = False
            continue
        all_equal = all_equal and left == right
        query_within = query_within and left.issubset(right)
        candidate_within = candidate_within and right.issubset(left)
    if query.time is not None:
        compared += 1
        if candidate.time is None:
            unknown = True
            all_equal = False
        else:
            if query.time.label and not candidate.time.label:
                unknown = True
                all_equal = False
            elif query.time.label and candidate.time.label:
                if normalized_text(query.time.label) != normalized_text(
                    candidate.time.label
                ):
                    unknown = True
                    all_equal = False
            if query.time.start is not None or query.time.end is not None:
                left_interval = _time_interval_years(query.time)
                right_interval = _time_interval_years(candidate.time)
                if left_interval is None or right_interval is None:
                    unknown = True
                    all_equal = False
                elif (
                    left_interval[1] < right_interval[0]
                    or right_interval[1] < left_interval[0]
                ):
                    return "disjoint"
                else:
                    equal = _interval_equal(left_interval, right_interval)
                    all_equal = all_equal and equal
                    query_within = query_within and (
                        left_interval[0] >= right_interval[0]
                        and left_interval[1] <= right_interval[1]
                    )
                    candidate_within = candidate_within and (
                        right_interval[0] >= left_interval[0]
                        and right_interval[1] <= left_interval[1]
                    )
    if query.uncertainty:
        compared += 1
        if not candidate.uncertainty or normalized_text(
            query.uncertainty
        ) != normalized_text(candidate.uncertainty):
            unknown = True
            all_equal = False
    if compared == 0:
        return "unknown"
    if unknown:
        return "unknown"
    if all_equal:
        return "equivalent"
    if query_within:
        return "query_within_candidate"
    if candidate_within:
        return "candidate_within_query"
    return "overlap"


def _interval_equal(left: tuple[float, float], right: tuple[float, float]) -> bool:
    return all(
        first == second
        or (
            math.isfinite(first)
            and math.isfinite(second)
            and math.isclose(first, second, rel_tol=1e-12, abs_tol=1e-9)
        )
        for first, second in zip(left, right, strict=True)
    )


def _scope_filter(scope: SearchScope) -> SearchScopeFilter | None:
    time = None
    if scope.time is not None and (
        scope.time.label is not None
        or scope.time.start is not None
        or scope.time.end is not None
    ):
        unit = scope.time.unit
        if (
            scope.time.start is not None or scope.time.end is not None
        ) and unit is None:
            unit = "unspecified"
        time = SearchScopeFilterTime(
            label=scope.time.label,
            start=scope.time.start,
            end=scope.time.end,
            unit=unit,
        )
    payload = {
        "location": _filter_scope_values(scope.location),
        "time": time,
        "formation": _filter_scope_values(scope.formation),
        "sample_type": _filter_scope_values(scope.sample_type),
        "method": _filter_scope_values(scope.method),
        "condition": _filter_scope_values(scope.condition),
        "measurement": _filter_scope_values(scope.measurement),
        "uncertainty": scope.uncertainty,
        "entities": _filter_scope_values(scope.entities),
    }
    if not any(value is not None for value in payload.values()):
        return None
    return SearchScopeFilter(**payload)


def _filter_scope_values(values: list[str]) -> list[str] | None:
    """Drop stored values equal under the filter's NFKC+lower key (NdCl²⁺, NdCl₂⁺).

    The filter rejects such repeats, and Scope comparison already treats them
    as one value, so keeping the first changes no comparison.
    """
    unique: dict[str, str] = {}
    for value in values:
        unique.setdefault(unicodedata.normalize("NFKC", value).lower(), value)
    # Stored Scopes are unbounded; SearchScopeFilter permits 64 values per list.
    return list(unique.values())[:64] or None


def _entity_conflict(plan: QueryPlan, document: ClaimSearchDocument) -> bool:
    def canonical_ids(values: list[str]) -> set[str]:
        return {
            normalized_text(item)
            for item in values
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{1,31}:[^\s:]{1,128}", item)
        }

    query_entities = canonical_ids(plan.entities)
    if plan.scope_constraints is not None and plan.scope_constraints.entities:
        query_entities.update(canonical_ids(plan.scope_constraints.entities))
    candidate_entities = canonical_ids(document.entities + document.scope.entities)
    return bool(
        query_entities
        and candidate_entities
        and query_entities.isdisjoint(candidate_entities)
    )


class ContextRelationMissingInputError(ValueError):
    """The relation interpreter declined; never create fallback labels."""


def _safe_context_relation_decision(
    interpretation: ContextRelationInterpretation | None,
    document: ClaimSearchDocument,
    proposed: RelationDecision,
) -> RelationDecision:
    """Normalize the model's declared reference, not a separate retrieval plan.

    This does not certify the interpretation's semantic correctness. Caller
    filters have their own candidate-admission path and are deliberately not
    copied into the reference proposition's polarity or scientific Scope.
    """
    if interpretation is None or proposed is None:
        raise ValueError("context relation requires an interpretation and decision")
    if interpretation.kind == "missing_context":
        raise ContextRelationMissingInputError()
    if interpretation.kind == "non_propositional":
        if proposed.relation not in {"related", "unrelated"}:
            raise ValueError("non-propositional tasks cannot have Claim relations")
        return proposed.model_copy(
            update={
                "candidate_key": relation_candidate_key(
                    document.snapshot_id, document.claim_id
                ),
                "relation_direction": "result_to_query",
                "scope_relation": "unknown",
            }
        )
    # A typed adapter into the unchanged conservative checks. This is NOT the
    # query planner's output and never feeds recall, ranking or the response.
    reference = QueryPlan(
        query_type="claim_search",
        original_query=interpretation.proposition,
        resolved_query=interpretation.proposition,
        canonical_claim=interpretation.proposition,
        polarity=interpretation.polarity,
        scope_constraints=interpretation.scope,
        entities=interpretation.entities,
        must_terms=[],
        optional_terms=[],
        exclude_terms=[],
        claim_types=[],
        planner="gpt",
    )
    return _safe_relation_decision(reference, document, proposed)


def _safe_relation_decision(
    plan: QueryPlan,
    document: ClaimSearchDocument,
    proposed: RelationDecision | None,
) -> RelationDecision:
    scope_relation = (
        "unknown"
        if plan.scope_constraints is None
        else _scope_relation(plan.scope_constraints, document.scope)
    )
    overlap = max(
        token_overlap(plan.canonical_claim, document.canonical_claim),
        token_overlap(_effective_plan_query(plan), document.canonical_claim),
        token_overlap(_effective_plan_query(plan), document.original_claim or ""),
    )
    polarity_conflict = plan.polarity is not None and plan.polarity != document.polarity
    entity_conflict = _entity_conflict(plan, document)
    scientific_conflict = _claim_signature_conflict(
        plan.canonical_claim, document.canonical_claim
    )
    if proposed is None:
        if entity_conflict:
            relation = "related" if overlap >= 0.08 else "unrelated"
            confidence = 0.7
        elif (
            polarity_conflict
            and not scientific_conflict
            and plan.polarity in {"affirmed", "negated"}
            and document.polarity in {"affirmed", "negated"}
            and scope_relation not in {"disjoint", "unknown"}
            and overlap >= 0.3
        ):
            relation = "contradicts"
            confidence = min(0.85, 0.5 + overlap / 2)
        elif not polarity_conflict and not scientific_conflict and overlap >= 0.72:
            relation = (
                "equivalent"
                if scope_relation in {"equivalent", "overlap", "unknown"}
                else "qualifies"
            )
            confidence = min(0.9, 0.5 + overlap / 2)
        elif overlap >= 0.12:
            relation = "related"
            confidence = min(0.8, 0.4 + overlap)
        else:
            relation = "unrelated"
            confidence = 0.7
        proposed = RelationDecision(
            candidate_key=relation_candidate_key(
                document.snapshot_id, document.claim_id
            ),
            relation=relation,
            relation_direction="result_to_query",
            relation_confidence=confidence,
            scope_relation=scope_relation,
        )
    relation = proposed.relation
    if relation == "equivalent" and (
        polarity_conflict or entity_conflict or scientific_conflict
    ):
        relation = "related"
    if relation == "contradicts" and scope_relation in {"disjoint", "unknown"}:
        relation = "related"
    if relation == "equivalent" and (
        scope_relation == "disjoint"
        or (scope_relation == "unknown" and plan.scope_constraints is not None)
    ):
        relation = "related"
    direction = (
        proposed.relation_direction
        if relation in {"supports", "refines", "qualifies"}
        else "result_to_query"
    )
    return RelationDecision(
        candidate_key=relation_candidate_key(document.snapshot_id, document.claim_id),
        relation=relation,
        relation_direction=direction,
        relation_confidence=proposed.relation_confidence,
        scope_relation=scope_relation,
    )


def _explicit_relation(
    query: ClaimSearchDocument | None,
    candidate: ClaimSearchDocument,
) -> tuple[RelationDecision, str] | None:
    if query is None:
        return None
    # Explicit graph edges are authoritative only inside their owning immutable
    # snapshot.  Content-addressed claim IDs can legitimately repeat elsewhere.
    if query.snapshot_id != candidate.snapshot_id:
        return None
    relation = next(
        (
            item
            for item in query.explicit_relations
            if item.other_claim_id == candidate.claim_id
        ),
        None,
    )
    relative_to_query = True
    if relation is None:
        relation = next(
            (
                item
                for item in candidate.explicit_relations
                if item.other_claim_id == query.claim_id
            ),
            None,
        )
        relative_to_query = False
    if relation is None:
        return None
    mapped = "equivalent" if relation.relation == "equivalent_to" else relation.relation
    if relative_to_query:
        direction = (
            "query_to_result" if relation.direction == "outgoing" else "result_to_query"
        )
    else:
        direction = (
            "result_to_query" if relation.direction == "outgoing" else "query_to_result"
        )
    if relation.scope_overlap == "equivalent":
        scope_relation = "equivalent"
    elif relation.scope_overlap == "partial":
        scope_relation = "overlap"
    elif relation.scope_overlap == "source_within_target":
        scope_relation = (
            "query_within_candidate"
            if direction == "query_to_result"
            else "candidate_within_query"
        )
    else:
        scope_relation = (
            "candidate_within_query"
            if direction == "query_to_result"
            else "query_within_candidate"
        )
    return (
        RelationDecision(
            candidate_key=relation_candidate_key(
                candidate.snapshot_id, candidate.claim_id
            ),
            relation=mapped,
            relation_direction=direction,
            relation_confidence=relation.confidence,
            scope_relation=scope_relation,
        ),
        direction,
    )


def _documents_cluster_compatible(
    representative: _Candidate, candidate: _Candidate
) -> bool:
    left = representative.document
    right = candidate.document
    if representative.decision is None or candidate.decision is None:
        return False
    if representative.decision.relation != candidate.decision.relation:
        return False
    if left.polarity != right.polarity:
        return False
    if _claim_signature_conflict(left.canonical_claim, right.canonical_claim):
        return False
    if _qualifier_preview_conflict(left.qualifiers, right.qualifiers):
        return False
    left_scope = _scope_filter(left.scope)
    scope_relation = (
        "unknown" if left_scope is None else _scope_relation(left_scope, right.scope)
    )
    if scope_relation == "disjoint":
        return False
    return (
        left.scoped_claim_id == right.scoped_claim_id
        or left.proposition_key == right.proposition_key
        or normalized_text(left.canonical_claim)
        == normalized_text(right.canonical_claim)
        or token_overlap(left.canonical_claim, right.canonical_claim) >= 0.72
    )


@lru_cache(maxsize=4_096)
def _numeric_scientific_signature(value: str) -> tuple[str, ...]:
    return tuple(sorted(scientific_mention_keys(value)))


def _direction_signature(value: str) -> str | None:
    normalized = normalized_text(value)
    upward = _UP_DIRECTION_RE.search(normalized) is not None
    downward = _DOWN_DIRECTION_RE.search(normalized) is not None
    if upward == downward:
        return None
    return "up" if upward else "down"


def _claim_signature_conflict(left: str, right: str) -> bool:
    left_keys = _numeric_scientific_signature(left)
    right_keys = _numeric_scientific_signature(right)
    left_by_unit: dict[str, set[str]] = {}
    right_by_unit: dict[str, set[str]] = {}
    for key in left_keys:
        unit = key.rsplit("|unit:", maxsplit=1)[-1]
        left_by_unit.setdefault(unit, set()).add(key)
    for key in right_keys:
        unit = key.rsplit("|unit:", maxsplit=1)[-1]
        right_by_unit.setdefault(unit, set()).add(key)
    for unit in left_by_unit.keys() & right_by_unit.keys():
        if left_by_unit[unit].isdisjoint(right_by_unit[unit]):
            return True
    left_direction = _direction_signature(left)
    right_direction = _direction_signature(right)
    return bool(
        left_direction is not None
        and right_direction is not None
        and left_direction != right_direction
    )


def _qualifier_preview_conflict(left: list[str], right: list[str]) -> bool:
    if not left or not right:
        return False
    left_values = {normalized_text(item) for item in left}
    right_values = {normalized_text(item) for item in right}
    # Clustering is allowed to under-merge. Differing explicit qualifiers remain
    # separate instead of relying on high Claim-text overlap to erase conditions.
    return left_values != right_values


def _member_ref(candidate: _Candidate) -> SearchMemberRef:
    if candidate.decision is None:
        raise RuntimeError("candidate relation must be classified before projection")
    document = candidate.document
    return SearchMemberRef(
        claim_id=document.claim_id,
        snapshot_id=document.snapshot_id,
        paper_id=document.paper_id,
        paper_family_id=document.paper_family_id,
        relation=candidate.decision.relation,
        relation_direction=candidate.direction,
        relation_confidence=candidate.decision.relation_confidence,
        scope_relation=candidate.decision.scope_relation,
        scores=RetrievalScores(
            lexical=candidate.lexical,
            dense=candidate.dense,
            rrf=candidate.rrf,
            rerank=candidate.rerank,
        ),
    )


def _preview_ref(candidate: _Candidate) -> PreviewMemberRef:
    document = candidate.document
    return PreviewMemberRef(
        claim_id=document.claim_id,
        snapshot_id=document.snapshot_id,
        paper_id=document.paper_id,
        paper_family_id=document.paper_family_id,
        scores=RetrievalScores(
            lexical=candidate.lexical,
            dense=candidate.dense,
            rrf=candidate.rrf,
            rerank=candidate.rerank,
        ),
    )


def _cluster_candidates(
    candidates: list[_Candidate], *, limit: int
) -> list[SearchCluster]:
    groups: list[_ClusterGroup] = []
    for candidate in candidates:
        if candidate.decision is None or candidate.decision.relation == "unrelated":
            continue
        joined = False
        if not candidate.same_paper_default:
            for group in groups:
                if not _documents_cluster_compatible(group.representative, candidate):
                    continue
                family_id = candidate.document.paper_family_id
                if family_id not in group.family_ids:
                    group.family_ids.add(family_id)
                    if len(group.preview) < 20:
                        group.preview.append(candidate)
                joined = True
                break
        if not joined:
            groups.append(
                _ClusterGroup(
                    representative=candidate,
                    preview=[candidate],
                    family_ids={candidate.document.paper_family_id},
                )
            )
    clusters: list[SearchCluster] = []
    for group in _diverse_cluster_groups(groups, limit=limit):
        representative = group.representative
        members = [_member_ref(item) for item in group.preview]
        identity = "\0".join(
            (
                representative.document.scoped_claim_id,
                representative.decision.relation
                if representative.decision is not None
                else "related",
                representative.document.snapshot_id,
                representative.document.claim_id,
            )
        )
        cluster_id = (
            "cluster:sha256:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
        )
        clusters.append(
            SearchCluster(
                cluster_id=cluster_id,
                relation=members[0].relation,
                representative=members[0],
                member_refs=members,
                distinct_paper_families=len(group.family_ids),
            )
        )
    return clusters


_FAMILY_DIVERSITY_WINDOW = 3


def _diverse_cluster_groups(
    groups: list[_ClusterGroup], *, limit: int
) -> list[_ClusterGroup]:
    """Diversify paper families by demotion only, never by dropping a group.

    Groups keep their ranked order, except that a group whose paper family
    already appears among the last ``_FAMILY_DIVERSITY_WINDOW`` selections
    waits behind the best-ranked group of another family. A second strong
    result from one paper is therefore demoted a few places instead of being
    replaced by every weaker family's first result.
    """

    pending = list(groups)
    selected: list[_ClusterGroup] = []
    while pending and len(selected) < limit:
        recent = {
            item.representative.document.paper_family_id
            for item in selected[-_FAMILY_DIVERSITY_WINDOW:]
        }
        chosen = next(
            (
                index
                for index, group in enumerate(pending)
                if group.representative.document.paper_family_id not in recent
            ),
            0,
        )
        selected.append(pending.pop(chosen))
    return selected
