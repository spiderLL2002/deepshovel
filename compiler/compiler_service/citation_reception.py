"""Independent pair-level extraction and verification for Citation Reception."""

from __future__ import annotations

import hashlib
import unicodedata
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from .citation_source import CitationFunctionCandidate, NormalizedDoi
from .config import GPT_MODEL, MODEL_MAX_ATTEMPTS
from .errors import ContractIssue, ContractValidationError
from .graph_contract import canonical_hash_json, canonical_json
from .job_control import ModelJobControl, ModelJobLimits
from .model_client import ModelJsonClient
from .models import Identifier, Sha256, StrictModel

CITATION_RECEPTION_SCHEMA_VERSION = "citation-reception/1.0"
CITATION_RECEPTION_PROCESSOR_VERSION = "citation-reception-processor/1.0.0"
CITATION_EXTRACTION_PROMPT_VERSION = "citation-extractor/1.0.0"
CITATION_VERIFICATION_PROMPT_VERSION = "citation-verifier/1.0.0"

CitationEntityKind = Literal[
    "source_anchor",
    "structured_visual_evidence",
    "observation",
    "reasoning",
    "atomic_claim",
]
CitationStance = Literal["supports", "contradicts", "qualifies", "unclear"]
ConfirmationBasis = Literal[
    "mere_attribution",
    "verbal_endorsement",
    "verbal_critique",
    "consistent_with_citing_evidence",
    "contradicted_by_citing_evidence",
    "qualified_by_citing_evidence",
    "direct_replication",
    "reanalysis",
    "meta_analytic_synthesis",
]
CitationFaithfulness = Literal[
    "faithful",
    "partial",
    "overgeneralized",
    "contradicted_by_target",
    "not_found",
    "ambiguous",
    "indirect",
]
CitationIndependence = Literal[
    "independent",
    "shared_authors",
    "shared_data",
    "derived_data",
    "unknown",
]

_STRONG_CONFIRMATION = frozenset(
    {
        "consistent_with_citing_evidence",
        "direct_replication",
        "reanalysis",
        "meta_analytic_synthesis",
    }
)
_EVIDENCE_BACKED_BASIS = _STRONG_CONFIRMATION | frozenset(
    {"contradicted_by_citing_evidence", "qualified_by_citing_evidence"}
)
_EVIDENCE_KINDS = frozenset(
    {"source_anchor", "structured_visual_evidence", "observation", "reasoning"}
)
_BASIS_RANK: dict[str, int] = {
    "mere_attribution": 0,
    "verbal_endorsement": 1,
    "verbal_critique": 1,
    "consistent_with_citing_evidence": 2,
    "contradicted_by_citing_evidence": 2,
    "qualified_by_citing_evidence": 2,
    "reanalysis": 3,
    "meta_analytic_synthesis": 3,
    "direct_replication": 4,
}
_DIRECTIONAL_BASIS: dict[str, str] = {
    "consistent_with_citing_evidence": "supports",
    "contradicted_by_citing_evidence": "contradicts",
    "qualified_by_citing_evidence": "qualifies",
}


def _basis_matches_stance(basis: str, stance: str) -> bool:
    if basis in _DIRECTIONAL_BASIS:
        return _DIRECTIONAL_BASIS[basis] == stance
    if basis == "verbal_endorsement":
        return stance == "supports"
    if basis == "verbal_critique":
        return stance in {"contradicts", "qualifies"}
    if basis == "mere_attribution":
        return stance == "unclear"
    return True


BoundedScientificText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32_000)
]


class CitationPairReference(StrictModel):
    id: Identifier
    raw_text: BoundedScientificText
    doi: NormalizedDoi | None
    year: Annotated[int, Field(ge=1800, le=2199)] | None


class CitationPairMention(StrictModel):
    id: Identifier
    marker: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
    ]
    exact_quote: BoundedScientificText
    context_text: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64_000)
    ]
    page: Annotated[int, Field(ge=1)]
    group_size: Annotated[int, Field(ge=1, le=100)]
    candidate_functions: Annotated[
        list[CitationFunctionCandidate], Field(min_length=1, max_length=3)
    ]

    @model_validator(mode="after")
    def unique_functions(self) -> CitationPairMention:
        if self.marker not in self.exact_quote:
            raise ValueError("citation mention marker must occur in its exact quote")
        if len(self.candidate_functions) != len(set(self.candidate_functions)):
            raise ValueError("citation mention candidate functions must be unique")
        return self


class CitationCandidateEntity(StrictModel):
    id: Identifier
    kind: CitationEntityKind
    text: BoundedScientificText
    anchor_ids: Annotated[list[Identifier], Field(max_length=64)]
    root_claim_ids: Annotated[list[Identifier], Field(max_length=256)]

    @model_validator(mode="after")
    def unique_references(self) -> CitationCandidateEntity:
        if len(self.anchor_ids) != len(set(self.anchor_ids)):
            raise ValueError("citation entity anchor IDs must be unique")
        if len(self.root_claim_ids) != len(set(self.root_claim_ids)):
            raise ValueError("citation entity root Claim IDs must be unique")
        return self


class CitationPairRequest(StrictModel):
    schema_version: Literal["citation-reception/1.0"] = (
        CITATION_RECEPTION_SCHEMA_VERSION
    )
    project_id: Identifier
    citing_snapshot_id: Identifier
    cited_snapshot_id: Identifier
    reference: CitationPairReference
    mentions: Annotated[list[CitationPairMention], Field(min_length=1, max_length=128)]
    citing_entities: Annotated[
        list[CitationCandidateEntity], Field(min_length=1, max_length=4_096)
    ]
    cited_entities: Annotated[
        list[CitationCandidateEntity], Field(min_length=1, max_length=4_096)
    ]

    @model_validator(mode="after")
    def closed_pair(self) -> CitationPairRequest:
        if self.citing_snapshot_id == self.cited_snapshot_id:
            raise ValueError("Citation Reception requires two distinct snapshots")
        for label, values in (
            ("mention", self.mentions),
            ("citing entity", self.citing_entities),
            ("cited entity", self.cited_entities),
        ):
            ids = [item.id for item in values]
            if len(ids) != len(set(ids)):
                raise ValueError(f"{label} IDs must be unique")
        return self


class CitationAssertionDraft(StrictModel):
    mention_id: Identifier
    attributed_proposition: BoundedScientificText
    function: CitationFunctionCandidate
    stance: CitationStance
    confirmation_basis: ConfirmationBasis
    target_entity_id: Identifier | None
    citing_evidence_entity_ids: Annotated[list[Identifier], Field(max_length=128)]
    confidence: Annotated[float, Field(ge=0, le=1)]


class CitationAssertionDraftBatch(StrictModel):
    assertions: Annotated[list[CitationAssertionDraft], Field(max_length=256)]


class CitationAssertion(StrictModel):
    id: Identifier
    mention_id: Identifier
    attributed_proposition: BoundedScientificText
    function: CitationFunctionCandidate
    stance: CitationStance
    confirmation_basis: ConfirmationBasis
    target_entity_id: Identifier | None
    target_entity_kind: CitationEntityKind | None
    citing_evidence_entity_ids: Annotated[list[Identifier], Field(max_length=128)]
    confidence: Annotated[float, Field(ge=0, le=1)]

    @model_validator(mode="after")
    def closed_target_and_evidence(self) -> CitationAssertion:
        if (self.target_entity_id is None) != (self.target_entity_kind is None):
            raise ValueError("citation assertion target identity must be coherent")
        if len(self.citing_evidence_entity_ids) != len(
            set(self.citing_evidence_entity_ids)
        ):
            raise ValueError("citation assertion evidence IDs must be unique")
        return self


class CitationAssessment(StrictModel):
    assertion_id: Identifier
    faithfulness: CitationFaithfulness
    confirmation_basis: ConfirmationBasis
    independence: CitationIndependence
    confidence: Annotated[float, Field(ge=0, le=1)]
    evidence_ids: Annotated[list[Identifier], Field(max_length=256)]
    limitations: Annotated[
        list[
            Annotated[
                str,
                StringConstraints(
                    strip_whitespace=True,
                    min_length=1,
                    max_length=2_000,
                ),
            ]
        ],
        Field(max_length=32),
    ]

    @model_validator(mode="after")
    def unique_support(self) -> CitationAssessment:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("citation assessment evidence IDs must be unique")
        if len(self.limitations) != len(set(self.limitations)):
            raise ValueError("citation assessment limitations must be unique")
        return self


class CitationAssessmentBatch(StrictModel):
    extraction_sha256: Sha256
    assessments: Annotated[list[CitationAssessment], Field(max_length=256)]


class CitationModelRun(StrictModel):
    run_id: Identifier
    model: Literal["gpt-6-luna", "gpt-5.6-luna", "deepseek-v4-flash"] = GPT_MODEL
    prompt_version: str
    attempts: Annotated[int, Field(ge=1, le=9)]


class CitationExtractionEnvelope(CitationModelRun):
    prompt_version: Literal["citation-extractor/1.0.0"] = (
        CITATION_EXTRACTION_PROMPT_VERSION
    )
    assertions_sha256: Sha256
    assertions: list[CitationAssertion]

    @model_validator(mode="after")
    def frozen_assertions_match_hash(self) -> CitationExtractionEnvelope:
        assertion_ids = [item.id for item in self.assertions]
        if len(assertion_ids) != len(set(assertion_ids)):
            raise ValueError("citation assertion IDs must be unique")
        if self.assertions_sha256 != _assertions_sha256(self.assertions):
            raise ValueError("citation extraction hash does not match assertions")
        return self


class CitationVerificationEnvelope(CitationModelRun):
    prompt_version: Literal["citation-verifier/1.0.0"] = (
        CITATION_VERIFICATION_PROMPT_VERSION
    )
    extraction_sha256: Sha256
    assessments: list[CitationAssessment]


class CitationReceptionEnvelope(StrictModel):
    schema_version: Literal["citation-reception/1.0"] = (
        CITATION_RECEPTION_SCHEMA_VERSION
    )
    processor_version: Literal["citation-reception-processor/1.0.0"] = (
        CITATION_RECEPTION_PROCESSOR_VERSION
    )
    citing_snapshot_id: Identifier
    cited_snapshot_id: Identifier
    reference_id: Identifier
    extraction: CitationExtractionEnvelope
    verification: CitationVerificationEnvelope | None

    @model_validator(mode="after")
    def verification_covers_frozen_extraction(self) -> CitationReceptionEnvelope:
        if self.extraction.prompt_version != CITATION_EXTRACTION_PROMPT_VERSION:
            raise ValueError("citation extraction provenance is incompatible")
        assertions = self.extraction.assertions
        if (not assertions) != (self.verification is None):
            raise ValueError(
                "citation verification must be present exactly when assertions exist"
            )
        if self.verification is None:
            return self
        if self.verification.prompt_version != CITATION_VERIFICATION_PROMPT_VERSION:
            raise ValueError("citation verification provenance is incompatible")
        if self.verification.extraction_sha256 != self.extraction.assertions_sha256:
            raise ValueError("citation verifier extraction hash mismatch")
        assertion_ids = {item.id for item in assertions}
        assessed_ids = [item.assertion_id for item in self.verification.assessments]
        if (
            len(assessed_ids) != len(set(assessed_ids))
            or set(assessed_ids) != assertion_ids
        ):
            raise ValueError(
                "citation verifier must assess every assertion exactly once"
            )
        return self


_EXTRACTION_SCHEMA = CitationAssertionDraftBatch.model_json_schema()
_VERIFICATION_SCHEMA = CitationAssessmentBatch.model_json_schema()

_EXTRACTION_PROMPT = """
You extract target-specific scientific Citation Reception assertions. Return
exactly one JSON object matching the supplied Schema. The citing paper A and
cited paper B are already resolved by the caller; never identify or substitute
another paper. Emit only result_comparison, replication, or critique assertions
that the supplied A context explicitly makes about B. Background, method-use,
and mere topical co-citation produce no assertion. One output assertion is one
atomic proposition A attributes to B and one specific mention_id.

group_size greater than one means the marker cites multiple references. Never
copy a proposition attributed to the whole citation group onto the current B.
Emit an assertion only when A's supplied context specifically attributes that
proposition to the resolved B; when the attribution cannot be disambiguated,
emit no assertion for that mention.

stance describes how A's evaluated result relates to the selected B
proposition, not whether A accuses B of an error. Use contradicts when A and B
report opposite outcomes on a materially comparable question, and qualifies
when A preserves B's result but narrows its scope or relative performance.
function must be one of that mention's candidate_functions; never choose a
function merely because another enum value sounds plausible.

Choose target_entity_id only from cited_entities when one candidate matches the
same subject, measured outcome, direction, method/condition, and material scope;
otherwise return null. Choose citing_evidence_entity_ids only from
citing_entities and only for A's own Observation, visual, source anchor, or
Reasoning evidence. The citation sentence itself is not A's independent
evidence. mere_attribution means A only reports B. verbal_endorsement means A
supports B in words without its own evidentiary path; verbal_critique means A
criticizes or limits B in words without its own evidentiary path. When A's own
evidence is explicitly compared with B, use consistent_with_citing_evidence
only with supports, contradicted_by_citing_evidence with contradicts, and
qualified_by_citing_evidence with qualifies. All three require explicit A
evidence IDs. direct_replication, reanalysis, and meta_analytic_synthesis also
require explicit A evidence and their named design. Prefer the conservative B
proposition that is both explicit in A's context and supported by a supplied B
entity; do not select an incidental detail that has no matching B candidate
when the broader compared result does. Do not infer independence, author
overlap, or dataset lineage. Preserve negation, quantities, units, comparison
direction, and qualifications.
mere_attribution and an unclear stance describe a non-evaluative report and are
outside this extractor's scope: return zero assertions for that mention.
Evidence-backed reception must select a materially matching B target. If A only
reports a benefit from using B's method or tool, that is method-use even when A
says "better accuracy"; do not turn A's benefit into a result attributed to B.
When no supplied B result entity matches that performance proposition, return
zero assertions instead of inventing a targetless confirmation.
""".strip()


_VERIFICATION_PROMPT = """
You independently verify frozen scientific Citation Reception assertions.
Return exactly one JSON object matching the supplied Schema and exactly one
assessment per assertion_id. Never rewrite an assertion or choose a replacement
target. Compare A's exact context with the selected B entity, including subject,
outcome, direction, quantity, unit, method, condition, and scope. faithful is
allowed only when B actually supports the attributed proposition. Use partial
or overgeneralized when A drops material scope; contradicted_by_target when B
says the opposite; not_found when no B target was selected or found; ambiguous
when attribution among a citation group is unclear; indirect for second-hand
attribution.

Separately verify confirmation_basis. A statement that B reported or correctly
showed X is at most verbal_endorsement; an unevidenced negative judgment is
verbal_critique. Concrete A evidence that agrees, conflicts, or supplies a
material limitation must respectively use consistent_with_citing_evidence,
contradicted_by_citing_evidence, or qualified_by_citing_evidence. The A evidence
must actually ground that stance. direct_replication, reanalysis, and
meta_analytic_synthesis require their named design. Never strengthen the
extractor basis or change one evidence-direction basis into another.
If an evidence-backed basis is not actually grounded, preserve the frozen
stance by downgrading supports to verbal_endorsement and contradicts or
qualifies to verbal_critique when A explicitly evaluates B. mere_attribution is
only coherent with an unclear stance and a context that merely reports B.
When A reports its own result on the same evaluation target and explicitly
compares it with B, that result can materially qualify relative performance
even when A and B use different methods; use qualified_by_citing_evidence when
the supplied A evidence grounds that narrower comparison.
Independence is unknown unless the supplied text explicitly establishes it;
never infer it from different paper titles. evidence_ids may use only supplied
A/B entity IDs. A faithful assessment is bound to the frozen selected B target;
include that target ID in evidence_ids. Keep limitations concise and factual;
do not expose hidden reasoning.
""".strip()


class CitationReceptionAnalyzer:
    def __init__(self, gpt: ModelJsonClient) -> None:
        self._gpt = gpt

    def analyze(self, request: CitationPairRequest) -> CitationReceptionEnvelope:
        control = ModelJobControl(
            limits=ModelJobLimits(
                max_completion_calls=2,
                max_request_attempts=2 * MODEL_MAX_ATTEMPTS,
                max_prompt_bytes=48 * 1024 * 1024 * MODEL_MAX_ATTEMPTS,
            )
        )
        client = self._gpt.with_job_control(control)
        request_payload = request.model_dump(mode="json")
        request_sha256 = hashlib.sha256(
            canonical_hash_json(request_payload).encode("utf-8")
        ).hexdigest()
        extraction = client.complete(
            system_prompt=_EXTRACTION_PROMPT,
            user_content=canonical_json(
                {
                    "task": "extract citation reception assertions",
                    "pair": request_payload,
                    "schema": _EXTRACTION_SCHEMA,
                }
            ),
            schema=CitationAssertionDraftBatch,
            validate=lambda value: _validate_drafts(request, value),
        )
        assertions = _freeze_assertions(request, extraction.value.assertions)
        assertions_sha256 = _assertions_sha256(assertions)
        extraction_run = CitationExtractionEnvelope(
            run_id=_run_id(
                "citation-extraction",
                extraction.content_sha256,
                CITATION_EXTRACTION_PROMPT_VERSION,
                request_sha256,
            ),
            prompt_version=CITATION_EXTRACTION_PROMPT_VERSION,
            attempts=extraction.attempts,
            model=extraction.model,
            assertions_sha256=assertions_sha256,
            assertions=assertions,
        )
        if not assertions:
            return CitationReceptionEnvelope(
                citing_snapshot_id=request.citing_snapshot_id,
                cited_snapshot_id=request.cited_snapshot_id,
                reference_id=request.reference.id,
                extraction=extraction_run,
                verification=None,
            )

        verification = client.complete(
            system_prompt=_VERIFICATION_PROMPT,
            user_content=canonical_json(
                {
                    "task": "verify frozen citation reception assertions",
                    "pair": _verification_pair_payload(request, assertions),
                    "frozen_assertions": [
                        item.model_dump(mode="json") for item in assertions
                    ],
                    "extraction_sha256": assertions_sha256,
                    "schema": _VERIFICATION_SCHEMA,
                }
            ),
            schema=CitationAssessmentBatch,
            validate=lambda value: _validate_assessments(
                request, assertions, assertions_sha256, value
            ),
        )
        assessments = _bind_faithful_target_evidence(assertions, verification.value)
        if assessments is not verification.value:
            _validate_assessments(request, assertions, assertions_sha256, assessments)
        verification_run = CitationVerificationEnvelope(
            run_id=_run_id(
                "citation-verification",
                verification.content_sha256,
                CITATION_VERIFICATION_PROMPT_VERSION,
                request_sha256,
            ),
            prompt_version=CITATION_VERIFICATION_PROMPT_VERSION,
            attempts=verification.attempts,
            model=verification.model,
            extraction_sha256=assertions_sha256,
            assessments=assessments.assessments,
        )
        return CitationReceptionEnvelope(
            citing_snapshot_id=request.citing_snapshot_id,
            cited_snapshot_id=request.cited_snapshot_id,
            reference_id=request.reference.id,
            extraction=extraction_run,
            verification=verification_run,
        )


def _validate_drafts(
    request: CitationPairRequest, batch: CitationAssertionDraftBatch
) -> None:
    mentions = {item.id: item for item in request.mentions}
    citing = {item.id: item for item in request.citing_entities}
    cited = {item.id: item for item in request.cited_entities}
    identities: set[tuple[str, str]] = set()
    for index, item in enumerate(batch.assertions):
        base_loc = ("assertions", index)
        mention = mentions.get(item.mention_id)
        if mention is None:
            raise ContractValidationError(
                ContractIssue.CITATION_MENTION_UNKNOWN,
                loc=base_loc + ("mention_id",),
            )
        if item.function not in mention.candidate_functions:
            raise ContractValidationError(
                ContractIssue.CITATION_FUNCTION_UNSUPPORTED,
                loc=base_loc + ("function",),
            )
        if item.confirmation_basis == "mere_attribution":
            raise ContractValidationError(
                ContractIssue.CITATION_NON_EVALUATIVE,
                loc=base_loc + ("confirmation_basis",),
            )
        identity = (
            item.mention_id,
            _proposition_identity(item.attributed_proposition),
        )
        if identity in identities:
            raise ContractValidationError(
                ContractIssue.CITATION_ASSERTION_DUPLICATE,
                loc=base_loc + ("attributed_proposition",),
            )
        identities.add(identity)
        if item.target_entity_id is not None and item.target_entity_id not in cited:
            raise ContractValidationError(
                ContractIssue.CITATION_TARGET_UNKNOWN,
                loc=base_loc + ("target_entity_id",),
            )
        if (
            item.confirmation_basis in _EVIDENCE_BACKED_BASIS
            and item.target_entity_id is None
        ):
            raise ContractValidationError(
                ContractIssue.CITATION_TARGET_REQUIRED,
                loc=base_loc + ("target_entity_id",),
            )
        if not _basis_matches_stance(item.confirmation_basis, item.stance):
            raise ContractValidationError(
                ContractIssue.CITATION_BASIS_STANCE_MISMATCH,
                loc=base_loc + ("confirmation_basis",),
            )
        if len(item.citing_evidence_entity_ids) != len(
            set(item.citing_evidence_entity_ids)
        ):
            raise ContractValidationError(
                ContractIssue.CITATION_EVIDENCE_INVALID,
                loc=base_loc + ("citing_evidence_entity_ids",),
            )
        for entity_id in item.citing_evidence_entity_ids:
            entity = citing.get(entity_id)
            if entity is None or entity.kind not in _EVIDENCE_KINDS:
                raise ContractValidationError(
                    ContractIssue.CITATION_EVIDENCE_INVALID,
                    loc=base_loc + ("citing_evidence_entity_ids",),
                )
        if (
            item.confirmation_basis in _EVIDENCE_BACKED_BASIS
            and not item.citing_evidence_entity_ids
        ):
            raise ContractValidationError(
                ContractIssue.CITATION_EVIDENCE_REQUIRED,
                loc=base_loc + ("citing_evidence_entity_ids",),
            )


def _freeze_assertions(
    request: CitationPairRequest, drafts: list[CitationAssertionDraft]
) -> list[CitationAssertion]:
    cited = {item.id: item for item in request.cited_entities}
    output: list[CitationAssertion] = []
    for item in drafts:
        target = cited.get(item.target_entity_id or "")
        if target is None:
            continue
        normalized = _proposition_identity(item.attributed_proposition)
        digest = hashlib.sha256(
            f"{request.citing_snapshot_id}\0{request.cited_snapshot_id}\0"
            f"{request.reference.id}\0{item.mention_id}\0{normalized}".encode()
        ).hexdigest()
        output.append(
            CitationAssertion(
                id=f"citation-assertion:sha256:{digest}",
                mention_id=item.mention_id,
                attributed_proposition=item.attributed_proposition,
                function=item.function,
                stance=item.stance,
                confirmation_basis=item.confirmation_basis,
                target_entity_id=item.target_entity_id,
                target_entity_kind=target.kind,
                citing_evidence_entity_ids=item.citing_evidence_entity_ids,
                confidence=item.confidence,
            )
        )
    return output


def _proposition_identity(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _validate_assessments(
    request: CitationPairRequest,
    assertions: list[CitationAssertion],
    assertions_sha256: str,
    batch: CitationAssessmentBatch,
) -> None:
    if batch.extraction_sha256 != assertions_sha256:
        raise ContractValidationError(
            ContractIssue.CITATION_VERIFIER_HASH_MISMATCH,
            loc=("extraction_sha256",),
        )
    by_id = {item.id: item for item in assertions}
    actual = [item.assertion_id for item in batch.assessments]
    if len(actual) != len(set(actual)) or set(actual) != set(by_id):
        raise ContractValidationError(
            ContractIssue.CITATION_VERIFIER_COVERAGE,
            loc=("assessments",),
        )
    citing_kinds = {item.id: item.kind for item in request.citing_entities}
    for index, assessment in enumerate(batch.assessments):
        base_loc = ("assessments", index)
        assertion = by_id[assessment.assertion_id]
        allowed_evidence = set(assertion.citing_evidence_entity_ids)
        if assertion.target_entity_id is not None:
            allowed_evidence.add(assertion.target_entity_id)
        if not set(assessment.evidence_ids).issubset(allowed_evidence):
            raise ContractValidationError(
                ContractIssue.CITATION_EVIDENCE_INVALID,
                loc=base_loc + ("evidence_ids",),
            )
        if (
            _BASIS_RANK[assessment.confirmation_basis]
            > _BASIS_RANK[assertion.confirmation_basis]
        ):
            raise ContractValidationError(
                ContractIssue.CITATION_VERIFIER_STRENGTHENED,
                loc=base_loc + ("confirmation_basis",),
            )
        if not _basis_matches_stance(assessment.confirmation_basis, assertion.stance):
            raise ContractValidationError(
                ContractIssue.CITATION_BASIS_STANCE_MISMATCH,
                loc=base_loc + ("confirmation_basis",),
            )
        if (
            assessment.confirmation_basis in _DIRECTIONAL_BASIS
            and assertion.confirmation_basis in _DIRECTIONAL_BASIS
            and assessment.confirmation_basis != assertion.confirmation_basis
        ):
            raise ContractValidationError(
                ContractIssue.CITATION_VERIFIER_DIRECTION_CHANGED,
                loc=base_loc + ("confirmation_basis",),
            )
        if assessment.confirmation_basis in _EVIDENCE_BACKED_BASIS:
            has_citing_evidence = bool(
                set(assessment.evidence_ids) & set(assertion.citing_evidence_entity_ids)
            ) and any(
                citing_kinds.get(item) in _EVIDENCE_KINDS
                for item in assessment.evidence_ids
            )
            if not has_citing_evidence:
                raise ContractValidationError(
                    ContractIssue.CITATION_EVIDENCE_REQUIRED,
                    loc=base_loc + ("evidence_ids",),
                )
        if assertion.target_entity_id is None and assessment.faithfulness not in {
            "not_found",
            "ambiguous",
            "indirect",
        }:
            raise ContractValidationError(
                ContractIssue.CITATION_VERIFIER_TARGET_INVALID,
                loc=base_loc + ("faithfulness",),
            )


def _bind_faithful_target_evidence(
    assertions: list[CitationAssertion], batch: CitationAssessmentBatch
) -> CitationAssessmentBatch:
    """Close an allowlisted verifier reference omission without changing its verdict."""

    by_id = {item.id: item for item in assertions}
    assessments: list[CitationAssessment] = []
    updated = False
    for item in batch.assessments:
        assertion = by_id[item.assertion_id]
        target_id = assertion.target_entity_id
        if (
            item.faithfulness == "faithful"
            and target_id is not None
            and target_id not in item.evidence_ids
        ):
            if len(item.evidence_ids) >= 256:
                raise ValueError("faithful citation target evidence exceeds bounds")
            item = item.model_copy(
                update={"evidence_ids": [*item.evidence_ids, target_id]}
            )
            updated = True
        assessments.append(item)
    if not updated:
        return batch
    return CitationAssessmentBatch(
        extraction_sha256=batch.extraction_sha256,
        assessments=assessments,
    )


def _verification_pair_payload(
    request: CitationPairRequest,
    assertions: list[CitationAssertion],
) -> dict[str, object]:
    """Keep only the frozen assertion reservoir in the independent verifier call."""

    mention_ids = {item.mention_id for item in assertions}
    citing_ids = {
        entity_id
        for item in assertions
        for entity_id in item.citing_evidence_entity_ids
    }
    cited_ids = {
        item.target_entity_id
        for item in assertions
        if item.target_entity_id is not None
    }
    return {
        "schema_version": request.schema_version,
        "project_id": request.project_id,
        "citing_snapshot_id": request.citing_snapshot_id,
        "cited_snapshot_id": request.cited_snapshot_id,
        "reference": request.reference.model_dump(mode="json"),
        "mentions": [
            item.model_dump(mode="json")
            for item in request.mentions
            if item.id in mention_ids
        ],
        "citing_entities": [
            item.model_dump(mode="json")
            for item in request.citing_entities
            if item.id in citing_ids
        ],
        "cited_entities": [
            item.model_dump(mode="json")
            for item in request.cited_entities
            if item.id in cited_ids
        ],
    }


def _run_id(
    prefix: str,
    content_sha256: str,
    prompt_version: str,
    request_sha256: str,
) -> str:
    digest = hashlib.sha256(
        f"{content_sha256}\0{prompt_version}\0{request_sha256}".encode()
    ).hexdigest()
    return f"{prefix}:sha256:{digest}"


def _assertions_sha256(assertions: list[CitationAssertion]) -> str:
    payload = [item.model_dump(mode="json") for item in assertions]
    return hashlib.sha256(canonical_hash_json(payload).encode("utf-8")).hexdigest()


__all__ = [
    "CITATION_EXTRACTION_PROMPT_VERSION",
    "CITATION_RECEPTION_PROCESSOR_VERSION",
    "CITATION_RECEPTION_SCHEMA_VERSION",
    "CITATION_VERIFICATION_PROMPT_VERSION",
    "CitationAssessment",
    "CitationAssertion",
    "CitationCandidateEntity",
    "CitationPairRequest",
    "CitationReceptionAnalyzer",
    "CitationReceptionEnvelope",
]
