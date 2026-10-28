"""Stable service errors; internal traces never cross the HTTP boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _FrozenDiagnostic(BaseModel):
    """Immutable, JSON-serializable metadata that cannot contain model text."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ContractIssue(StrEnum):
    """Allowlisted contract failures safe to persist and return to the model."""

    PROVENANCE_PAPER_ID_MISMATCH = "provenance_paper_id_mismatch"
    PROVENANCE_ARTIFACT_IDENTITY_MISMATCH = "provenance_artifact_identity_mismatch"
    PROVENANCE_PAGE_INVALID = "provenance_page_invalid"
    PROVENANCE_SOURCE_REFERENCE_INVALID = "provenance_source_reference_invalid"
    PROVENANCE_QUOTE_NOT_FOUND = "provenance_quote_not_found"
    PROVENANCE_VISUAL_BLOCK_NOT_FOUND = "provenance_visual_block_not_found"
    PROVENANCE_VISUAL_BLOCK_AMBIGUOUS = "provenance_visual_block_ambiguous"
    PROVENANCE_VISUAL_VALUE_NOT_FOUND = "provenance_visual_value_not_found"
    PROVENANCE_VISUAL_VALUE_UNVERIFIABLE = "provenance_visual_value_unverifiable"
    PROVENANCE_VISUAL_CELL_AMBIGUOUS = "provenance_visual_cell_ambiguous"
    PROVENANCE_VISUAL_NUMERIC_MISMATCH = "provenance_visual_numeric_mismatch"
    PROVENANCE_REGION_UNIT_INVALID = "provenance_region_unit_invalid"
    PROVENANCE_REGION_MISMATCH = "provenance_region_mismatch"
    GRAPH_ANCHOR_REQUIRED = "graph_anchor_required"
    GRAPH_ORPHAN_NODES = "graph_orphan_nodes"
    GRAPH_ID_DUPLICATE = "graph_id_duplicate"
    GRAPH_REFERENCE_UNKNOWN = "graph_reference_unknown"
    GRAPH_REFERENCE_DUPLICATE = "graph_reference_duplicate"
    GRAPH_IDENTITY_MISMATCH = "graph_identity_mismatch"
    GRAPH_EDGE_ENDPOINT_INVALID = "graph_edge_endpoint_invalid"
    GRAPH_EDGE_DUPLICATE = "graph_edge_duplicate"
    GRAPH_DERIVATION_CYCLE = "graph_derivation_cycle"
    GRAPH_REASONING_PREMISE_MISSING = "graph_reasoning_premise_missing"
    GRAPH_REASONING_UNCONCLUDED = "graph_reasoning_unconcluded"
    GRAPH_CLAIM_CONCLUSION_MISSING = "graph_claim_conclusion_missing"
    CANONICALIZATION_INVALID = "canonicalization_invalid"
    CANONICAL_CLAIM_COMPARISON_SCOPE_INVALID = (
        "canonical_claim_comparison_scope_invalid"
    )
    CANONICAL_EDGE_COLLAPSE = "canonical_edge_collapse"
    CANONICAL_ID_COLLISION = "canonical_id_collision"
    CANONICAL_PATH_INVALID = "canonical_path_invalid"
    OBSERVATION_PATH_OWNERSHIP = "observation_path_ownership"
    VERIFICATION_GRAPH_HASH_MISMATCH = "verification_graph_hash_mismatch"
    VERIFICATION_PAPER_ID_MISMATCH = "verification_paper_id_mismatch"
    VERIFICATION_ASSESSMENT_ID_DUPLICATE = "verification_assessment_id_duplicate"
    VERIFICATION_CLAIM_UNKNOWN = "verification_claim_unknown"
    VERIFICATION_CLAIM_ASSESSED_MULTIPLE_TIMES = (
        "verification_claim_assessed_multiple_times"
    )
    VERIFICATION_SCOPED_CLAIM_ID_MISMATCH = "verification_scoped_claim_id_mismatch"
    VERIFICATION_EVIDENCE_ID_DUPLICATE = "verification_evidence_id_duplicate"
    VERIFICATION_EVIDENCE_ID_UNKNOWN = "verification_evidence_id_unknown"
    VERIFICATION_EVIDENCE_OUTSIDE_PATH = "verification_evidence_outside_path"
    VERIFICATION_CLAIM_UNASSESSED = "verification_claim_unassessed"
    RETRIEVAL_RELATION_CANDIDATE_COVERAGE = "retrieval_relation_candidate_coverage"
    RETRIEVAL_RELATION_CONFIDENCE_MISSING = "retrieval_relation_confidence_missing"
    CITATION_MENTION_UNKNOWN = "citation_mention_unknown"
    CITATION_FUNCTION_UNSUPPORTED = "citation_function_unsupported"
    CITATION_NON_EVALUATIVE = "citation_non_evaluative"
    CITATION_ASSERTION_DUPLICATE = "citation_assertion_duplicate"
    CITATION_TARGET_UNKNOWN = "citation_target_unknown"
    CITATION_TARGET_REQUIRED = "citation_target_required"
    CITATION_BASIS_STANCE_MISMATCH = "citation_basis_stance_mismatch"
    CITATION_EVIDENCE_INVALID = "citation_evidence_invalid"
    CITATION_EVIDENCE_REQUIRED = "citation_evidence_required"
    CITATION_VERIFIER_HASH_MISMATCH = "citation_verifier_hash_mismatch"
    CITATION_VERIFIER_COVERAGE = "citation_verifier_coverage"
    CITATION_VERIFIER_STRENGTHENED = "citation_verifier_strengthened"
    CITATION_VERIFIER_DIRECTION_CHANGED = "citation_verifier_direction_changed"
    CITATION_VERIFIER_TARGET_INVALID = "citation_verifier_target_invalid"


class ModelRequestErrorKind(StrEnum):
    """Allowlisted request-boundary failures; never persist provider text."""

    CONNECTION = "connection_error"
    TIMEOUT = "timeout"
    INVALID_REQUEST = "invalid_request"
    AUTHENTICATION = "authentication_failed"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    INVALID_PARAMETERS = "invalid_parameters"
    RATE_LIMITED = "rate_limited"
    PROVIDER_ERROR = "provider_error"
    HTTP_ERROR = "http_error"
    UNEXPECTED = "unexpected_error"


@dataclass(frozen=True, slots=True)
class ContractViolation:
    """One allowlisted contract issue and its internal field-only location."""

    issue: ContractIssue
    loc: tuple[str | int, ...] = ()


class ModelTokenUsage(_FrozenDiagnostic):
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    prompt_cache_hit_tokens: int | None = Field(default=None, ge=0)
    prompt_cache_miss_tokens: int | None = Field(default=None, ge=0)


class ModelJsonFailure(_FrozenDiagnostic):
    classification: Literal[
        "syntax_error",
        "duplicate_key",
        "non_standard_constant",
        "non_finite_number",
        "strict_value_error",
        "truncated_json",
    ]
    pos: int | None = Field(default=None, ge=0)


_SchemaLocName = Annotated[
    str,
    Field(
        min_length=1,
        max_length=48,
        pattern=r"^(?:<key>|[A-Za-z_][A-Za-z0-9_]{0,47})$",
    ),
]
_SchemaLocIndex = Annotated[int, Field(ge=0, le=1_000_000)]


class ModelSchemaIssue(_FrozenDiagnostic):
    loc: tuple[_SchemaLocName | _SchemaLocIndex, ...] = Field(max_length=4)
    type: str = Field(
        min_length=1,
        max_length=48,
        pattern=r"^[a-z][a-z0-9_]{0,47}$",
    )


class ModelContractIssue(_FrozenDiagnostic):
    issue: ContractIssue
    loc: tuple[_SchemaLocName | _SchemaLocIndex, ...] = Field(max_length=4)


class ModelAttemptDiagnostic(_FrozenDiagnostic):
    attempt: int = Field(ge=1)
    classification: Literal[
        "request_failed",
        "empty_content",
        "truncated_json",
        "incomplete_output",
        "invalid_json",
        "invalid_json_root",
        "schema_validation_failed",
        "contract_validation_failed",
        "success",
    ]
    finish_reason: (
        Literal[
            "stop",
            "length",
            "content_filter",
            "tool_calls",
            "function_call",
            "other",
        ]
        | None
    ) = None
    content_bytes: int | None = Field(default=None, ge=0)
    content_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    usage: ModelTokenUsage | None = None
    json_error: ModelJsonFailure | None = None
    schema_errors: tuple[ModelSchemaIssue, ...] = Field(default=(), max_length=8)
    contract_issue: ContractIssue | None = None
    contract_loc: tuple[_SchemaLocName | _SchemaLocIndex, ...] = Field(
        default=(), max_length=4
    )
    # Up to four distinct rule codes, one located witness each.
    contract_issues: tuple[ModelContractIssue, ...] = Field(
        default=(), max_length=4
    )
    request_error_kind: ModelRequestErrorKind | None = None
    request_http_status: int | None = Field(default=None, ge=400, le=599)
    request_exception_type: str | None = Field(
        default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,79}$"
    )
    request_error_stage: Literal["request", "stream", "finalize"] | None = None
    request_provider_code: str | None = Field(default=None, pattern=r"^[a-z_]{1,64}$")
    request_error_location: str | None = Field(
        default=None, pattern=r"^(gpt_responses|model_client)\.py:[0-9]{1,7}$"
    )

    @model_validator(mode="after")
    def validate_request_detail(self) -> ModelAttemptDiagnostic:
        request_failed = self.classification == "request_failed"
        if request_failed != (self.request_error_kind is not None):
            raise ValueError(
                "request failure diagnostics require exactly one safe error kind"
            )
        status_kinds = {
            ModelRequestErrorKind.INVALID_REQUEST,
            ModelRequestErrorKind.AUTHENTICATION,
            ModelRequestErrorKind.INSUFFICIENT_BALANCE,
            ModelRequestErrorKind.PERMISSION_DENIED,
            ModelRequestErrorKind.NOT_FOUND,
            ModelRequestErrorKind.CONFLICT,
            ModelRequestErrorKind.INVALID_PARAMETERS,
            ModelRequestErrorKind.RATE_LIMITED,
            ModelRequestErrorKind.PROVIDER_ERROR,
            ModelRequestErrorKind.HTTP_ERROR,
        }
        if self.request_error_kind in status_kinds:
            if self.request_http_status is None:
                raise ValueError("HTTP request failure diagnostics require a status")
        elif self.request_http_status is not None:
            raise ValueError("non-HTTP request failures cannot carry a status")
        return self


class CompilerError(RuntimeError):
    code = "COMPILER_ERROR"
    status_code = 500
    retryable = False

    def __init__(self, message: str) -> None:
        super().__init__(message)


class InputError(CompilerError):
    code = "INVALID_INPUT"
    status_code = 400


class QueryPlanHandoffError(InputError):
    """A public, non-oracular signal that a fresh preview is required."""

    code = "QUERY_PLAN_HANDOFF_REPREVIEW_REQUIRED"
    status_code = 409


class QueryPlanHandoffUnavailable(CompilerError):
    code = "QUERY_PLAN_HANDOFF_UNAVAILABLE"
    status_code = 503
    retryable = True


class AuthenticationError(CompilerError):
    code = "UNAUTHORIZED"
    status_code = 401


class ArtifactHashMismatch(InputError):
    code = "ARTIFACT_SHA256_MISMATCH"


class UnsupportedMediaType(CompilerError):
    code = "UNSUPPORTED_MEDIA_TYPE"
    status_code = 415


class MineruUnavailable(CompilerError):
    code = "MINERU_UNAVAILABLE"
    status_code = 503


class MineruFailed(CompilerError):
    code = "MINERU_FAILED"
    status_code = 502
    retryable = True


class MineruRejected(MineruFailed):
    """A legacy upstream-rejection code; MinerU failures are retryable."""

    code = "MINERU_REQUEST_REJECTED"


class OpenAlexUnavailable(CompilerError):
    code = "OPENALEX_UNAVAILABLE"
    status_code = 503
    retryable = True


class OpenAlexRateLimited(OpenAlexUnavailable):
    code = "OPENALEX_RATE_LIMITED"


class OpenAlexAuthenticationFailed(CompilerError):
    code = "OPENALEX_AUTHENTICATION_FAILED"
    status_code = 502


class OpenAlexRequestRejected(CompilerError):
    code = "OPENALEX_REQUEST_REJECTED"
    status_code = 502


class OpenAlexInvalidResponse(CompilerError):
    code = "OPENALEX_INVALID_RESPONSE"
    status_code = 502


class ModelUnavailable(CompilerError):
    code = "GPT_UNAVAILABLE"
    status_code = 503
    retryable = True


_GPT_REQUEST_CODES = {
    ModelRequestErrorKind.CONNECTION: "GPT_CONNECTION_ERROR",
    ModelRequestErrorKind.TIMEOUT: "GPT_TIMEOUT",
    ModelRequestErrorKind.INVALID_REQUEST: "GPT_INVALID_REQUEST",
    ModelRequestErrorKind.AUTHENTICATION: "GPT_AUTHENTICATION_FAILED",
    ModelRequestErrorKind.INSUFFICIENT_BALANCE: "GPT_INSUFFICIENT_BALANCE",
    ModelRequestErrorKind.PERMISSION_DENIED: "GPT_PERMISSION_DENIED",
    ModelRequestErrorKind.NOT_FOUND: "GPT_MODEL_NOT_FOUND",
    ModelRequestErrorKind.CONFLICT: "GPT_CONFLICT",
    ModelRequestErrorKind.INVALID_PARAMETERS: "GPT_INVALID_PARAMETERS",
    ModelRequestErrorKind.RATE_LIMITED: "GPT_RATE_LIMITED",
    ModelRequestErrorKind.PROVIDER_ERROR: "GPT_PROVIDER_ERROR",
    ModelRequestErrorKind.HTTP_ERROR: "GPT_HTTP_ERROR",
    ModelRequestErrorKind.UNEXPECTED: "GPT_UNEXPECTED_ERROR",
}
_GPT_RETRYABLE_REQUEST_KINDS = {
    ModelRequestErrorKind.CONNECTION,
    ModelRequestErrorKind.TIMEOUT,
    ModelRequestErrorKind.RATE_LIMITED,
    ModelRequestErrorKind.PROVIDER_ERROR,
}


class ModelRequestError(CompilerError):
    """One safely classified request failure with no provider body or key."""

    def __init__(
        self,
        kind: ModelRequestErrorKind,
        *,
        http_status: int | None = None,
        diagnostics: tuple[ModelAttemptDiagnostic, ...] = (),
    ) -> None:
        self.kind = kind
        self.http_status = http_status
        self.diagnostics = tuple(diagnostics)
        self.code = _GPT_REQUEST_CODES[kind]
        self.retryable = kind in _GPT_RETRYABLE_REQUEST_KINDS
        self.status_code = 503 if self.retryable else 502
        status = "" if http_status is None else f", HTTP {http_status}"
        super().__init__(f"Model request failed ({kind.value}{status})")


class ModelJobBudgetExceeded(CompilerError):
    code = "GPT_JOB_BUDGET_EXCEEDED"
    status_code = 503


class ModelJobDeadlineExceeded(CompilerError):
    code = "GPT_JOB_DEADLINE_EXCEEDED"
    status_code = 504


class ModelOutputError(CompilerError):
    code = "MODEL_OUTPUT_INVALID"
    status_code = 502
    retryable = True

    def __init__(
        self,
        message: str,
        *,
        diagnostics: tuple[ModelAttemptDiagnostic, ...] = (),
    ) -> None:
        super().__init__(message)
        self.diagnostics = tuple(diagnostics)


class ContractError(CompilerError):
    code = "OUTPUT_CONTRACT_INVALID"
    status_code = 422


class ContractValidationError(ContractError):
    """A categorized contract failure whose details may contain sensitive text."""

    def __init__(
        self,
        contract_issue: ContractIssue,
        *,
        loc: tuple[str | int, ...] = (),
    ) -> None:
        super().__init__("output failed contract validation")
        self.contract_issue = contract_issue
        # This path is sanitized against the authoritative response schema before
        # it enters diagnostics or retry feedback. It never contains field values.
        self.loc = tuple(loc)
        self.violations = (ContractViolation(contract_issue, self.loc),)

    @classmethod
    def from_violations(
        cls, violations: tuple[ContractViolation, ...]
    ) -> ContractValidationError:
        """Create one failure from a nonempty, deterministic issue collection."""

        if not violations:
            raise ValueError("contract violations must not be empty")
        first = violations[0]
        error = cls(first.issue, loc=first.loc)
        error.violations = tuple(violations)
        return error


class JobNotFound(CompilerError):
    code = "JOB_NOT_FOUND"
    status_code = 404


class JobNotComplete(CompilerError):
    code = "JOB_NOT_COMPLETE"
    status_code = 409


class JobNotTerminal(CompilerError):
    code = "JOB_NOT_TERMINAL"
    status_code = 409


class JobCancelled(CompilerError):
    code = "JOB_CANCELLED"
    status_code = 409


class IdempotencyKeyReused(CompilerError):
    """A caller key already names a compile job for different input."""

    code = "IDEMPOTENCY_KEY_REUSED"
    status_code = 409


class SubmissionExpired(CompilerError):
    """The caller's submission deadline passed before the job was recorded."""

    code = "COMPILE_SUBMISSION_EXPIRED"
    status_code = 409


class ServiceRestarted(CompilerError):
    """Restart recovery could not resume a job from its saved progress."""

    code = "SERVICE_RESTARTED"
    status_code = 503
    retryable = True
