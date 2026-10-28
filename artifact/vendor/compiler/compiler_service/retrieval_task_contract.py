"""Compile quoted task operations against supplied representations, without IO.

Exact quotes and hashes establish location/identity, never semantic attribution,
proposition completeness, or attribute coverage. Operation selection remains a
model proposal requiring empirical semantic review. Query units are retained
retrieval inputs, not proof that a multi-question task has been executed.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from typing import Annotated, Any, Literal

from pydantic import (
    Field,
    RootModel,
    StringConstraints,
    field_validator,
    model_validator,
)

from .graph_contract import canonical_json
from .models import Sha256, StrictModel
from .retrieval_contract import (
    ClaimPolarity,
    ClaimSearchDocument,
    ClarificationPrompt,
    ContextRelationInterpretation,
    HybridSearchRequest,
    NeedsContextPrompt,
    RetrievalContextState,
    SearchScopeFilter,
)

_SEED_FIELDS = (
    "snapshot_id",
    "claim_id",
    "canonical_claim",
    "scope",
    "claim_type",
    "polarity",
    "modality",
)
ExactText = Annotated[str, StringConstraints(min_length=1, max_length=8000)]


class TaskQuote(StrictModel):
    source: Literal["query", "history", "selected", "supplement"]
    quote: ExactText
    index: Annotated[int, Field(ge=0, le=15)] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @field_validator("quote")
    @classmethod
    def exact_text(cls, value: str) -> str:
        if not value.strip() or unicodedata.normalize("NFC", value) != value:
            raise ValueError("quote must be nonblank NFC text")
        value.encode("utf-8")
        return value

    @model_validator(mode="after")
    def index_matches_source(self) -> TaskQuote:
        if self.source in {"history", "supplement"}:
            if self.index is None:
                raise ValueError("indexed quote requires an index")
        elif self.index is not None:
            raise ValueError("index is forbidden for this quote source")
        return self


class SeedTarget(StrictModel):
    kind: Literal["seed"]


class InputQuoteTarget(StrictModel):
    kind: Literal["input_quote"]
    reference: TaskQuote


class MissingReferentTarget(StrictModel):
    kind: Literal["missing_referent"]
    mention: TaskQuote


SourceTarget = Annotated[
    SeedTarget | InputQuoteTarget | MissingReferentTarget,
    Field(discriminator="kind"),
]
EvidenceDirection = Literal["any", "support", "counter"]


class PolarityWitness(StrictModel):
    value: ClaimPolarity
    witness: TaskQuote


class BoundTextValue(StrictModel):
    value: ExactText
    witness: TaskQuote

    @field_validator("value")
    @classmethod
    def exact_value(cls, value: str) -> str:
        TaskQuote.exact_text(value)
        if value != value.strip():
            raise ValueError("attribute values must not require whitespace repair")
        return value


class TimeScopeBindings(StrictModel):
    label: BoundTextValue | None
    start: BoundTextValue | None
    end: BoundTextValue | None
    unit: BoundTextValue | None


class ScopeBindings(StrictModel):
    location: Annotated[list[BoundTextValue], Field(max_length=64)]
    time: TimeScopeBindings | None
    formation: Annotated[list[BoundTextValue], Field(max_length=64)]
    sample_type: Annotated[list[BoundTextValue], Field(max_length=64)]
    method: Annotated[list[BoundTextValue], Field(max_length=64)]
    condition: Annotated[list[BoundTextValue], Field(max_length=64)]
    measurement: Annotated[list[BoundTextValue], Field(max_length=64)]
    uncertainty: BoundTextValue | None
    entities: Annotated[list[BoundTextValue], Field(max_length=64)]


class PropositionAttributes(StrictModel):
    polarity: PolarityWitness
    entities: Annotated[list[BoundTextValue], Field(max_length=64)]
    scope: ScopeBindings


class SearchPropositionOperation(StrictModel):
    operation: Literal["search_proposition"]
    instruction: TaskQuote
    target: SourceTarget
    evidence_direction: EvidenceDirection
    attributes: PropositionAttributes | None


class InspectSeedOperation(StrictModel):
    operation: Literal["inspect_seed"]
    instruction: TaskQuote
    target: SeedTarget
    evidence_direction: EvidenceDirection


class AnswerQuestionsOperation(StrictModel):
    operation: Literal["answer_questions"]
    instruction: TaskQuote
    questions: Annotated[list[TaskQuote], Field(min_length=1, max_length=8)]


class CorrectFromClaimOperation(StrictModel):
    operation: Literal["correct_from_claim"]
    instruction: TaskQuote
    target: SourceTarget
    premise: TaskQuote


class VerifyOriginalSourceOperation(StrictModel):
    operation: Literal["verify_original_source"]
    instruction: TaskQuote
    target: SourceTarget


class TaskOperationProposal(
    RootModel[
        Annotated[
            SearchPropositionOperation
            | InspectSeedOperation
            | AnswerQuestionsOperation
            | CorrectFromClaimOperation
            | VerifyOriginalSourceOperation,
            Field(discriminator="operation"),
        ]
    ]
):
    pass


class InputPropositionOperation(SearchPropositionOperation):
    """Use supplied proposition text, including text after 'the following claim'."""

    target: InputQuoteTarget
    attributes: PropositionAttributes


class SeedPropositionOperation(SearchPropositionOperation):
    """The caller supplies indexed seed attributes; do not generate them."""

    target: SeedTarget
    attributes: None


class MissingPropositionOperation(SearchPropositionOperation):
    """Only when target content is absent from all supplied text; no attributes."""

    target: MissingReferentTarget
    attributes: None


class InteractiveTaskOperationProposal(
    RootModel[
        InputPropositionOperation
        | SeedPropositionOperation
        | MissingPropositionOperation
        | InspectSeedOperation
        | AnswerQuestionsOperation
        | CorrectFromClaimOperation
        | VerifyOriginalSourceOperation
    ]
):
    """Expose the compiler's target/attribute rules in the model's wire schema."""


class InteractiveRetrievePlanningOutput(StrictModel):
    action: Literal["retrieve"]
    proposal: InteractiveTaskOperationProposal


class InteractiveClarifyPlanningOutput(StrictModel):
    action: Literal["clarify"]
    clarification: ClarificationPrompt


class InteractiveNeedsContextPlanningOutput(StrictModel):
    action: Literal["needs_context"]
    needs_context: NeedsContextPrompt


class InteractiveContextPlanningOutput(StrictModel):
    """Object-root interactive wire; local validators retain tagged semantics."""

    decision: (
        InteractiveRetrievePlanningOutput
        | InteractiveClarifyPlanningOutput
        | InteractiveNeedsContextPlanningOutput
    )

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        schema = super().model_json_schema(*args, **kwargs)

        def supported_unions(value: Any) -> None:
            if isinstance(value, dict):
                # Operation/target variants have mutually exclusive literal tags.
                # anyOf therefore preserves their meaning, while avoiding keywords
                # outside the Responses strict subset. Only this wire is changed.
                if "oneOf" in value:
                    value["anyOf"] = value.pop("oneOf")
                value.pop("discriminator", None)
                for child in value.values():
                    supported_unions(child)
            elif isinstance(value, list):
                for child in value:
                    supported_unions(child)

        supported_unions(schema)
        return schema


class SourceInventoryEntry(StrictModel):
    source_id: str
    role: Literal["user", "assistant", "authenticated_selection", "indexed_seed"]
    capabilities: list[Literal["task_text", "claim_representation", "scope_attributes"]]
    content_sha256: Sha256


class CompiledTaskContract(StrictModel):
    version: Literal["deepshovel-compiled-task/1.0"] = "deepshovel-compiled-task/1.0"
    status: Literal["ready", "needs_context", "capability_unavailable"]
    interpretation: ContextRelationInterpretation | None
    needs_context: NeedsContextPrompt | None
    retrieval_text: str
    query_units: Annotated[list[ExactText], Field(max_length=8)]
    task_input_sha256: Sha256
    proposal: TaskOperationProposal
    source_inventory: list[SourceInventoryEntry]

    @model_validator(mode="after")
    def execution_state(self) -> CompiledTaskContract:
        if self.status == "ready":
            if self.interpretation is None or self.needs_context is not None:
                raise ValueError("ready contract requires only an interpretation")
            if not self.query_units or not self.retrieval_text:
                raise ValueError("ready contract requires retrieval inputs")
        elif self.interpretation is not None or self.query_units or self.retrieval_text:
            raise ValueError("pending contracts cannot expose execution inputs")
        if (self.status == "needs_context") != (self.needs_context is not None):
            raise ValueError("only missing context carries a question")
        return self


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def task_contract_compiler_identity() -> dict[str, Any]:
    """Versioned behavior identity, with no runtime source-file access."""
    return {
        "version": "deepshovel-task-operation-compiler/1.0",
        "proposal_schema_sha256": _sha(TaskOperationProposal.model_json_schema()),
        "compiled_schema_sha256": _sha(CompiledTaskContract.model_json_schema()),
        "rules": {
            "quote": (
                "exact NFC unique occurrence in named supplied text; "
                "no assistant source"
            ),
            "attributes": (
                "exact value substring of uniquely located target-contained witness"
            ),
            "numeric_time": (
                "complete finite decimal token; no inference or unit conversion"
            ),
            "seed": (
                "requested canonical/polarity and complete populated Scope "
                "hydrated verbatim"
            ),
            "capabilities": (
                "canonical seed and user text never establish original-page capability"
            ),
            "missing": (
                "named referent question; fixed short question for oversized mention"
            ),
            "render": (
                "operation/instruction/exact ordered units/direction; "
                "no truncation; 8000 chars"
            ),
            "semantics": (
                "location does not prove operation, attribution, "
                "completeness or coverage"
            ),
        },
    }


def task_contract_input_projection(
    request: HybridSearchRequest,
    *,
    context: RetrievalContextState,
    query_document: ClaimSearchDocument | None = None,
) -> dict[str, Any]:
    """Same exact task-only material as v1, with no provider dependency."""
    if request.schema_version != "evidence-retrieval/2.3":
        raise ValueError("task operations require protocol 2.3")
    if request.query_claim is not None:
        if query_document is None or (
            query_document.snapshot_id != request.query_claim.snapshot_id
            or query_document.claim_id != request.query_claim.claim_id
        ):
            raise ValueError("requested seed is unavailable or mismatched")
    elif query_document is not None:
        raise ValueError("unrequested seed is forbidden")
    return {
        "version": "deepshovel-task-only-projection/1.0",
        "query": request.query,
        "conversation_history": [
            t.model_dump(mode="json") for t in request.conversation_history
        ],
        "context": {
            "supplements": list(context.supplements),
            "selected_query": context.selected_query,
        },
        "query_claim_context": (
            query_document.model_dump(mode="json", include=set(_SEED_FIELDS))
            if query_document is not None
            else None
        ),
    }


def _texts(
    request: HybridSearchRequest, context: RetrievalContextState
) -> dict[str, tuple[str, str]]:
    result = {"query": (request.query, "user")}
    result.update(
        {
            f"history:{i}": (t.content, t.role)
            for i, t in enumerate(request.conversation_history)
        }
    )
    result.update(
        {f"supplement:{i}": (t, "user") for i, t in enumerate(context.supplements)}
    )
    if context.selected_query is not None:
        result["selected"] = (context.selected_query, "authenticated_selection")
    return result


def _key(quote: TaskQuote) -> str:
    return quote.source if quote.index is None else f"{quote.source}:{quote.index}"


def task_source_inventory(
    request: HybridSearchRequest,
    *,
    context: RetrievalContextState,
    query_document: ClaimSearchDocument | None = None,
) -> list[SourceInventoryEntry]:
    """Server-owned supplied representation capabilities; none grants source access."""
    projection = task_contract_input_projection(
        request, context=context, query_document=query_document
    )
    inventory = [
        SourceInventoryEntry(
            source_id=key,
            role=role,
            capabilities=["task_text"],
            content_sha256=_sha(text),
        )
        for key, (text, role) in _texts(request, context).items()
    ]
    if query_document is not None:
        inventory.append(
            SourceInventoryEntry(
                source_id="seed",
                role="indexed_seed",
                capabilities=["claim_representation", "scope_attributes"],
                content_sha256=_sha(projection["query_claim_context"]),
            )
        )
    return inventory


def _locate(
    quote: TaskQuote, texts: dict[str, tuple[str, str]]
) -> tuple[str, int, int]:
    key = _key(quote)
    if key not in texts or texts[key][1] == "assistant":
        raise ValueError("quote source unavailable or assistant-owned")
    text = texts[key][0]
    start = text.find(quote.quote)
    if start < 0 or text.find(quote.quote, start + 1) >= 0:
        raise ValueError("quote must have exactly one occurrence in its source")
    return key, start, start + len(quote.quote)


def _bound_text(
    quote: TaskQuote, target: TaskQuote, texts: dict[str, tuple[str, str]]
) -> str:
    key, start, end = _locate(quote, texts)
    target_key, target_start, target_end = _locate(target, texts)
    if key != target_key or start < target_start or end > target_end:
        raise ValueError("attribute witness must occur inside the referenced target")
    return quote.quote


def _attributes(
    attributes: PropositionAttributes,
    target: TaskQuote,
    texts: dict[str, tuple[str, str]],
) -> tuple[ClaimPolarity, list[str], SearchScopeFilter | None]:
    def bound(q: BoundTextValue) -> str:
        witness = _bound_text(q.witness, target, texts)
        if q.value not in witness:
            raise ValueError("attribute value must occur exactly in its witness")
        return q.value

    _bound_text(attributes.polarity.witness, target, texts)
    values: dict[str, Any] = {}
    for name in (
        "location",
        "formation",
        "sample_type",
        "method",
        "condition",
        "measurement",
        "entities",
    ):
        values[name] = [bound(q) for q in getattr(attributes.scope, name)] or None
    values["uncertainty"] = (
        bound(attributes.scope.uncertainty) if attributes.scope.uncertainty else None
    )
    time = attributes.scope.time
    if time is not None:

        def number(q: BoundTextValue | None) -> float | None:
            if q is None:
                return None
            value = bound(q)
            if (
                re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", value)
                is None
            ):
                raise ValueError(
                    "time endpoint witness must be a complete numeric token"
                )
            key, witness_start, _ = _locate(q.witness, texts)
            matches = list(
                re.finditer(
                    r"(?<![\w.])[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?(?![\w.])",
                    texts[key][0],
                )
            )
            if not any(
                match.group() == value
                and match.start() >= witness_start
                and match.end() <= witness_start + len(q.witness.quote)
                for match in matches
            ):
                raise ValueError("time value is not a complete token in its source")
            parsed = float(value)
            if not math.isfinite(parsed):
                raise ValueError("time endpoint must be finite")
            return parsed

        values["time"] = {
            "label": bound(time.label) if time.label else None,
            "start": number(time.start),
            "end": number(time.end),
            "unit": bound(time.unit) if time.unit else None,
        }
    scope = (
        SearchScopeFilter.model_validate(values, strict=True)
        if any(v is not None for v in values.values())
        else None
    )
    return attributes.polarity.value, [bound(q) for q in attributes.entities], scope


def compile_task_contract(
    proposal: TaskOperationProposal,
    request: HybridSearchRequest,
    *,
    context: RetrievalContextState,
    query_document: ClaimSearchDocument | None = None,
) -> CompiledTaskContract:
    """Validate references/capabilities; render data without scientific inference."""
    proposal = TaskOperationProposal.model_validate(
        proposal.model_dump(mode="json"), strict=True
    )
    projection = task_contract_input_projection(
        request, context=context, query_document=query_document
    )
    texts = _texts(request, context)
    inventory = task_source_inventory(
        request, context=context, query_document=query_document
    )
    common = dict(
        task_input_sha256=_sha(projection),
        proposal=proposal,
        source_inventory=inventory,
    )
    op = proposal.root
    _locate(op.instruction, texts)
    nonprop = ContextRelationInterpretation(
        kind="non_propositional",
        proposition=None,
        polarity=None,
        entities=[],
        scope=None,
    )

    def pending(
        status: Literal["needs_context", "capability_unavailable"],
        mention: str | None = None,
    ) -> CompiledTaskContract:
        question_text = f"请提供任务所指“{mention}”的实际 Claim 或来源对象。"
        if len(question_text) > 1000:
            question_text = "请提供本次操作所指但尚未提供的实际 Claim 或来源对象。"
        question = (
            NeedsContextPrompt(question=question_text) if mention is not None else None
        )
        return CompiledTaskContract(
            status=status,
            interpretation=None,
            needs_context=question,
            retrieval_text="",
            query_units=[],
            **common,
        )

    if isinstance(op, AnswerQuestionsOperation):
        locations = [_locate(q, texts) for q in op.questions]
        if len(set(locations)) != len(locations):
            raise ValueError("question references must be distinct")
        units = [q.quote for q in op.questions]
        interpretation = nonprop
    else:
        target = op.target
        if isinstance(target, MissingReferentTarget):
            _locate(target.mention, texts)
            if isinstance(op, SearchPropositionOperation) and op.attributes is not None:
                raise ValueError("missing targets cannot carry proposition attributes")
            if isinstance(op, CorrectFromClaimOperation):
                _locate(op.premise, texts)
            return pending("needs_context", target.mention.quote)
        if isinstance(target, SeedTarget):
            if query_document is None:
                raise ValueError("seed target requires the requested indexed seed")
            text = query_document.canonical_claim
        else:
            _locate(target.reference, texts)
            text = target.reference.quote
        if isinstance(op, VerifyOriginalSourceOperation):
            # Canonical fields and caller text do not prove original-page access.
            return pending("capability_unavailable")
        if isinstance(op, CorrectFromClaimOperation):
            _locate(op.premise, texts)
            units = [op.premise.quote, text]
            interpretation = nonprop
        elif isinstance(target, SeedTarget):
            if isinstance(op, SearchPropositionOperation) and op.attributes is not None:
                raise ValueError("seed properties are server-owned")
            scope_values = query_document.scope.model_dump(mode="json")
            scope_values = {
                k: v for k, v in scope_values.items() if v is not None and v != []
            }
            scope = (
                SearchScopeFilter.model_validate(scope_values, strict=True)
                if scope_values
                else None
            )
            interpretation = ContextRelationInterpretation(
                kind="single_proposition",
                proposition=text,
                polarity=query_document.polarity,
                entities=list(query_document.scope.entities),
                scope=scope,
            )
            units = [text]
        else:
            if not isinstance(op, SearchPropositionOperation) or op.attributes is None:
                raise ValueError("input proposition requires bound attributes")
            polarity, entities, scope = _attributes(
                op.attributes, target.reference, texts
            )
            interpretation = ContextRelationInterpretation(
                kind="single_proposition",
                proposition=text,
                polarity=polarity,
                entities=entities,
                scope=scope,
            )
            units = [text]
    # These wrappers describe the operation, not a model-authored scientific answer.
    rendered = (
        f"Operation: {op.operation}\nTask instruction: {op.instruction.quote}\n"
        + "\n".join(f"Input {i + 1}: {text}" for i, text in enumerate(units))
    )
    if hasattr(op, "evidence_direction"):
        rendered += f"\nEvidence direction: {op.evidence_direction}"
    if len(rendered) > 8000:
        raise ValueError("compiled retrieval text exceeds the unchanged text budget")
    return CompiledTaskContract(
        status="ready",
        interpretation=interpretation,
        needs_context=None,
        retrieval_text=rendered,
        query_units=units,
        **common,
    )
