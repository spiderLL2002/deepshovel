"""Document IR -> independently extracted and optionally verified import envelope."""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from typing import Any

from .canonical import (
    EXTRACTION_PROMPT_VERSION,
    VERIFICATION_PROMPT_VERSION,
    graph_sha256,
    to_canonical_graph,
    utc_now,
)
from .citation_source import CitationSourceProjection, build_citation_source
from .claim_tolerance import (
    ParsedGraph,
    ParsedVerification,
    claims_affected_by_violations,
    max_local_claim_errors,
    parse_graph_with_local_claim_tolerance,
    parse_verification_with_local_claim_tolerance,
    prune_graph_to_claims,
)
from .compiler_audit import CompilerAudit
from .config import GPT_MODEL, MAX_DOCUMENT_JSON_BYTES
from .errors import (
    ContractError,
    ContractIssue,
    ContractValidationError,
    ContractViolation,
    InputError,
    JobCancelled,
    ModelOutputError,
)
from .graph_contract import (
    PaperEvidenceGraph,
    Region,
    VerificationReport,
    canonical_json,
)
from .job_control import ModelJobControl
from .model_client import ModelJsonClient
from .models import (
    CompilerAuditRecord,
    DocumentBlock,
    DocumentIR,
    ImportEnvelope,
    ModelRun,
    VerificationEnvelope,
)
from .openalex_topics import OpenAlexTopicClassifier
from .prompts import EXTRACTION_SYSTEM_PROMPT, VERIFICATION_SYSTEM_PROMPT
from .source_catalog import (
    SourceCatalog,
    build_source_catalog,
    resolve_source_span_text,
)
from .source_links import link_referenced_visuals
from .structured_source import model_visible_block_content
from .verification_stage import VerificationAccumulator, payload_digest
from .visual_provenance import bind_structured_visuals

_MAX_AGGREGATED_CONTRACT_ISSUES = 4


@dataclass(frozen=True, slots=True)
class CompileOutcome:
    envelope: ImportEnvelope
    citation_source: CitationSourceProjection
    raw_graph: PaperEvidenceGraph
    extraction_attempts: int
    verification_attempts: int | None


@dataclass(slots=True)
class _ExtractionState:
    """Holder for the latest accepted extraction attempt (validate callback)."""

    parsed: ParsedGraph | None = None
    canonical: dict[str, Any] | None = None
    raw: PaperEvidenceGraph | None = None
    audit: CompilerAudit = field(default_factory=CompilerAudit)


@dataclass(frozen=True, slots=True)
class _Verified:
    envelope: VerificationEnvelope
    canonical: dict[str, Any]
    raw: PaperEvidenceGraph
    attempts: int


class EvidenceCompiler:
    def __init__(
        self,
        gpt: ModelJsonClient,
        *,
        topic_classifier: OpenAlexTopicClassifier | Any | None = None,
    ) -> None:
        self.gpt = gpt
        self.topic_classifier = topic_classifier

    def compile(
        self,
        document: DocumentIR,
        *,
        verify: bool = False,
        created_at: str | None = None,
        on_validating: Callable[[], None] | None = None,
        check_cancelled: Callable[[], None] | None = None,
        model_control: ModelJobControl | None = None,
    ) -> CompileOutcome:
        if model_control is not None and check_cancelled is not None:
            raise ValueError(
                "model_control and check_cancelled cannot both be provided"
            )
        control = model_control or ModelJobControl(check_cancelled=check_cancelled)
        controlled_gpt = self.gpt.with_job_control(control)
        control.checkpoint()
        timestamp = created_at or utc_now()
        source_catalog = build_source_catalog(document)
        structured_sources = source_catalog.structured_sources
        compact_document = compact_document_ir(
            document,
            unsafe_structured_block_ids=()
            if structured_sources is None
            else structured_sources.unsafe_block_ids,
            extra_warnings=source_catalog.warnings,
        )
        user_content = _bounded_model_content(
            {
                "trusted_artifact_context": document.artifact,
                "trusted_source_spans": _trusted_source_span_payload(source_catalog),
                "document_ir": compact_document,
            }
        )
        state = _ExtractionState()

        def parse_extraction(payload: dict[str, Any]) -> PaperEvidenceGraph:
            state.parsed = parse_graph_with_local_claim_tolerance(payload)
            return state.parsed.graph

        def validate_extraction(candidate: PaperEvidenceGraph) -> None:
            assert state.parsed is not None
            audit = CompilerAudit()
            for claim_id, reason in state.parsed.removed_claims:
                audit.record("claim_removed", claim_id, reason)
            for node_id in state.parsed.invalid_node_ids:
                audit.record("node_removed", node_id, "extraction:schema_invalid")
            for node_id in state.parsed.unreachable_node_ids:
                audit.record("node_removed", node_id, "extraction:unreachable")
            enriched, canonical_graph = _admit_extraction(
                candidate,
                document,
                source_catalog=source_catalog,
                parsed=state.parsed,
                audit=audit,
                timestamp=timestamp,
            )
            state.canonical = canonical_graph
            state.raw = enriched
            state.audit = audit

        extraction = controlled_gpt.complete(
            system_prompt=EXTRACTION_SYSTEM_PROMPT,
            user_content=user_content,
            schema=PaperEvidenceGraph,
            parse_payload=parse_extraction,
            validate=validate_extraction,
        )
        assert state.canonical is not None and state.raw is not None
        assert state.parsed is not None
        graph = state.canonical
        raw_graph = state.raw
        audit = state.audit
        original_claim_count = state.parsed.original_claim_count
        extraction_run = ModelRun(
            run_id=_run_id(
                "extraction",
                extraction.content_sha256,
                EXTRACTION_PROMPT_VERSION,
                document,
                model=extraction.model,
            ),
            model=extraction.model,
            prompt_version=EXTRACTION_PROMPT_VERSION,
        )
        if on_validating is not None:
            # Extraction is complete. The independent verifier and final
            # envelope checks belong to the validating stage.
            control.checkpoint()
            on_validating()
        verification_envelope = None
        verification_attempts = None
        if verify and raw_graph.claims:
            verified = self._verify(
                controlled_gpt,
                document,
                compact_document,
                graph,
                raw_graph,
                original_claim_count=original_claim_count,
                timestamp=timestamp,
                audit=audit,
            )
            graph = verified.canonical
            raw_graph = verified.raw
            verification_envelope = verified.envelope
            verification_attempts = verified.attempts
        elif original_claim_count != len(raw_graph.claims):
            audit.warn(
                "compiler_discarded_local_claims:"
                f"{original_claim_count - len(raw_graph.claims)}"
            )
        control.checkpoint()
        citation_source = self._citation_source(document, audit)
        control.checkpoint()
        openalex = self._openalex(document, audit)
        control.checkpoint()
        return CompileOutcome(
            envelope=ImportEnvelope(
                graph=graph,
                root_artifact_version_id=document.artifact.version_id,
                extraction=extraction_run,
                verification=verification_envelope,
                openalex=openalex,
                compiler_audit=CompilerAuditRecord.model_validate(
                    audit.payload(), strict=True
                ),
            ),
            citation_source=citation_source,
            raw_graph=raw_graph,
            extraction_attempts=extraction.attempts,
            verification_attempts=verification_attempts,
        )

    def _verify(
        self,
        controlled_gpt: ModelJsonClient,
        document: DocumentIR,
        compact_document: dict[str, Any],
        graph: dict[str, Any],
        raw_graph: PaperEvidenceGraph,
        *,
        original_claim_count: int,
        timestamp: str,
        audit: CompilerAudit,
    ) -> _Verified:
        """Run the independent verifier until every Claim has one assessment.

        Verifier mistakes are verifier errors: an invalid, missing, or
        misattributed assessment is requested again (fresh attempt with located
        feedback) while every valid assessment from earlier attempts is kept.
        Only if the bounded attempts end with some Claims still unassessed are
        those Claims removed -- within the cumulative local budget and with an
        explicit audit record -- otherwise verification fails closed.
        """

        frozen_hash = graph_sha256(graph)
        accumulator = VerificationAccumulator(raw_graph)
        attempt: dict[str, Any] = {}

        def parse_report(payload: dict[str, Any]) -> VerificationReport:
            parsed = parse_verification_with_local_claim_tolerance(
                payload,
                valid_claim_ids=accumulator.claims,
            )
            attempt["parsed"] = parsed
            attempt["digest"] = payload_digest(payload)
            return parsed.report

        def validate_report(report: VerificationReport) -> None:
            if report.graph_sha256 != frozen_hash:
                raise ContractValidationError(
                    ContractIssue.VERIFICATION_GRAPH_HASH_MISMATCH,
                    loc=("graph_sha256",),
                )
            if report.paper_id != raw_graph.paper_id:
                raise ContractValidationError(
                    ContractIssue.VERIFICATION_PAPER_ID_MISMATCH,
                    loc=("paper_id",),
                )
            parsed: ParsedVerification = attempt["parsed"]
            findings = accumulator.absorb(
                report,
                digest=attempt["digest"],
                schema_invalid_claim_ids=parsed.schema_invalid_claim_ids,
                unattributable_records=parsed.unattributable_records,
            )
            if accumulator.missing(report.verifier_model):
                raise accumulator.retry_error(report.verifier_model, findings)
            attempt["model"] = report.verifier_model

        verifier_content = _bounded_model_content(
            {
                "graph_sha256": frozen_hash,
                "extraction_graph": raw_graph,
                "document_ir": compact_document,
                "verification_claim_manifest": _verification_claim_manifest(raw_graph),
            }
        )
        removed: tuple[str, ...] = ()
        content_sha256: str | None = None
        try:
            verification = controlled_gpt.complete(
                system_prompt=VERIFICATION_SYSTEM_PROMPT,
                user_content=verifier_content,
                schema=VerificationReport,
                parse_payload=parse_report,
                validate=validate_report,
            )
            model = attempt["model"]
            attempts = verification.attempts
            content_sha256 = verification.content_sha256
        except ModelOutputError as error:
            model = accumulator.best_model()
            if model is None:
                raise
            removed = accumulator.missing(model)
            already_dropped = original_claim_count - len(raw_graph.claims)
            if len(removed) >= len(raw_graph.claims) or (
                already_dropped + len(removed)
                > max_local_claim_errors(original_claim_count)
            ):
                raise
            attempts = len(error.diagnostics) or accumulator.attempts
        final_raw = raw_graph
        if removed:
            final_raw = prune_graph_to_claims(
                raw_graph, set(accumulator.claims) - set(removed)
            )
            for claim_id in removed:
                finding = accumulator.last_findings.get(claim_id)
                issue = (
                    ContractIssue.VERIFICATION_CLAIM_UNASSESSED
                    if finding is None
                    else finding.issue
                )
                audit.record("claim_removed", claim_id, f"verification:{issue.value}")
        # Canonicalization is deterministic: without removals the extraction's
        # canonical graph is already the final one.
        final_canonical = (
            graph
            if final_raw is raw_graph
            else _canonicalize(final_raw, document, timestamp)
        )
        warnings: list[str] = []
        if original_claim_count != len(final_raw.claims):
            warnings.append(
                "compiler_discarded_local_claims:"
                f"{original_claim_count - len(final_raw.claims)}"
            )
        if removed:
            warnings.append(f"compiler_removed_unverified_claims:{len(removed)}")
        merged = accumulator.merged(
            model,
            graph=final_raw,
            graph_sha256=graph_sha256(final_canonical),
            warnings=warnings,
        )
        if len(merged.contributions) > 1:
            audit.record(
                "verification_attempts_merged",
                None,
                f"contributing_attempts={len(merged.contributions)}",
            )
        for original_id, renamed_id in merged.renamed:
            audit.record("verification_assessment_renamed", renamed_id, original_id)
        if accumulator.unknown_claim_ids:
            audit.record(
                "verification_unknown_claim_ids",
                None,
                f"count={len(accumulator.unknown_claim_ids)}",
            )
        if accumulator.unattributable_records:
            audit.record(
                "verification_unattributable_assessments",
                None,
                f"count={accumulator.unattributable_records}",
            )
        if content_sha256 is None or len(merged.contributions) > 1:
            content_sha256 = hashlib.sha256(
                "\0".join(merged.contributions).encode()
            ).hexdigest()
        envelope = VerificationEnvelope(
            run_id=_run_id(
                "verification",
                content_sha256,
                VERIFICATION_PROMPT_VERSION,
                document,
                model=model,
            ),
            model=model,
            prompt_version=VERIFICATION_PROMPT_VERSION,
            report=merged.report.model_dump(mode="json"),
        )
        return _Verified(
            envelope=envelope,
            canonical=final_canonical,
            raw=final_raw,
            attempts=attempts,
        )

    def _citation_source(
        self, document: DocumentIR, audit: CompilerAudit
    ) -> CitationSourceProjection:
        """Build the rebuildable citation projection; never fail a paid compile."""

        try:
            return build_citation_source(document)
        except JobCancelled:
            raise
        except Exception:
            audit.warn("citation_source_unavailable")
            return CitationSourceProjection(
                artifact_version_id=document.artifact.version_id,
                references=[],
                mentions=[],
                warnings=["citation source extraction failed; projection is empty"],
            )

    def _openalex(self, document: DocumentIR, audit: CompilerAudit) -> Any:
        """Optional topic classification; an outage leaves it empty."""

        if self.topic_classifier is None:
            return None
        try:
            return self.topic_classifier.classify_document(document)
        except JobCancelled:
            raise
        except Exception:
            audit.warn("openalex_classification_unavailable")
            return None


def _admit_extraction(
    candidate: PaperEvidenceGraph,
    document: DocumentIR,
    *,
    source_catalog: SourceCatalog,
    parsed: ParsedGraph,
    audit: CompilerAudit,
    timestamp: str,
) -> tuple[PaperEvidenceGraph, dict[str, Any]]:
    """Hydrate Compiler-owned provenance, then isolate bad local Claims.

    Every rewrite is recorded in ``audit``. Hard provenance violations (invalid
    source references, quotes, visual values, path ownership, canonical
    identity) remove only the affected Claims' exclusive closure, within the
    cumulative local budget; anything global or over budget is raised for a
    fresh extraction attempt with located feedback.
    """

    enriched, unresolved_anchor_ids = _hydrate_caller_owned_source_anchors(
        candidate,
        document,
        source_catalog=source_catalog,
    )
    audit.record_graph_rewrites(candidate, enriched, stage="source_span_hydration")
    if unresolved_anchor_ids and not enriched.claims:
        raise ContractValidationError.from_violations(
            _unresolved_anchor_violations(enriched, unresolved_anchor_ids)
        )
    for stage, step in (
        ("zero_claim_provenance", _ensure_zero_claim_provenance_anchor),
        ("visual_reference_link", link_referenced_visuals),
    ):
        updated = step(enriched, document)
        audit.record_graph_rewrites(enriched, updated, stage=stage)
        enriched = updated
    quotes = _QuoteIndex(document, source_catalog)
    updated = _canonicalize_exact_quote_regions(enriched, document, quotes=quotes)
    audit.record_graph_rewrites(enriched, updated, stage="exact_quote_region")
    enriched = updated

    while True:
        enriched, violations = bind_structured_visuals(
            enriched,
            document,
            source_catalog=source_catalog,
            record=audit.record,
        )
        violations = (
            *_unresolved_anchor_violations(enriched, unresolved_anchor_ids),
            *violations,
            *_extraction_contract_violations(enriched, document, quotes=quotes),
        )
        canonical = None
        if not violations:
            try:
                canonical = _canonicalize(enriched, document, timestamp)
            except ContractValidationError as error:
                if claims_affected_by_violations(enriched, error.violations) is None:
                    raise
                violations = error.violations
        if canonical is not None:
            return enriched, canonical
        affected = claims_affected_by_violations(enriched, violations)
        if affected is None:
            raise ContractValidationError.from_violations(tuple(violations))
        retained = {claim.id for claim in enriched.claims} - set(affected)
        total_dropped = parsed.original_claim_count - len(retained)
        if not retained or total_dropped > max_local_claim_errors(
            parsed.original_claim_count
        ):
            raise ContractValidationError.from_violations(tuple(violations))
        for claim_id, issue in affected.items():
            audit.record("claim_removed", claim_id, f"extraction:{issue.value}")
        enriched = prune_graph_to_claims(enriched, retained)


def _canonicalize(
    graph: PaperEvidenceGraph, document: DocumentIR, timestamp: str
) -> dict[str, Any]:
    """Canonicalize, keeping located contract issues and hiding the rest."""

    try:
        return to_canonical_graph(
            graph,
            document.artifact,
            parser=document.parser,
            created_at=timestamp,
        )
    except ContractValidationError:
        raise
    except ContractError as exc:
        raise ContractValidationError(ContractIssue.CANONICALIZATION_INVALID) from exc


def _unresolved_anchor_violations(
    graph: PaperEvidenceGraph, unresolved_anchor_ids: Collection[str]
) -> tuple[ContractViolation, ...]:
    if not unresolved_anchor_ids:
        return ()
    return tuple(
        ContractViolation(
            ContractIssue.PROVENANCE_SOURCE_REFERENCE_INVALID,
            ("anchors", index, "locator", "source_span_id"),
        )
        for index, anchor in enumerate(graph.anchors)
        if anchor.id in unresolved_anchor_ids
    )


def compact_document_ir(
    document: DocumentIR,
    *,
    unsafe_structured_block_ids: Collection[str] = (),
    extra_warnings: Collection[str] = (),
) -> dict[str, Any]:
    """Return the de-duplicated model view after structured-source admission.

    Unsafe structured payloads stay out of every downstream model call. Their
    ordinary block text and geometry remain available, and bounded catalog
    warnings make the omission explicit instead of silently truncating source
    material.
    """

    unsafe_blocks = frozenset(unsafe_structured_block_ids)
    pages: list[dict[str, Any]] = []
    for page in document.pages:
        blocks: list[dict[str, Any]] = []
        for block in page.blocks:
            if block.id in unsafe_blocks:
                text, structured = block.text, {}
            else:
                text, structured = model_visible_block_content(block)
            item: dict[str, Any] = {
                "id": block.id,
                "kind": block.kind,
                "page": block.page,
                "source_index": block.source_index,
                "bbox": None
                if block.bbox is None
                else block.bbox.model_dump(mode="json"),
                "text": text,
            }
            if structured:
                item["structured"] = structured
            blocks.append(item)
        pages.append(
            {
                "number": page.number,
                "width": page.width,
                "height": page.height,
                "blocks": blocks,
            }
        )
    return {
        "schema_version": document.schema_version,
        "artifact": document.artifact.model_dump(mode="json"),
        "parser": document.parser.model_dump(mode="json"),
        "page_count": document.page_count,
        "pages": pages,
        "warnings": list(dict.fromkeys((*document.warnings, *extra_warnings))),
    }


def _bounded_model_content(payload: dict[str, Any]) -> str:
    content = canonical_json(payload)
    if len(content.encode("utf-8")) > MAX_DOCUMENT_JSON_BYTES:
        raise InputError(f"compact model input exceeds {MAX_DOCUMENT_JSON_BYTES} bytes")
    return content


def _verification_claim_manifest(graph: PaperEvidenceGraph) -> dict[str, Any]:
    """Keep caller-owned assessment identities adjacent to verifier generation."""

    return {
        "claim_count": len(graph.claims),
        "claims": [
            {
                "claim_id": claim.id,
                "scoped_claim_id": claim.scoped_claim_id,
            }
            for claim in graph.claims
        ],
    }


def _trusted_source_span_payload(catalog: SourceCatalog) -> list[dict[str, Any]]:
    """Expose exact caller-owned text choices without delegating provenance.

    Structured-visual scalar records are intentionally omitted here: table
    cells and plotted points are checked against the trusted structured
    templates by ``visual_provenance.bind_structured_visuals`` after
    extraction. Every listed value resolves byte-for-byte against one
    DocumentIR source.
    """

    return [
        {
            "source_span_id": span.id,
            "page": span.page,
            "kind": span.kind,
            "source_kind": span.source_kind,
            "section_title": span.section_title,
            "exact_text": span.exact_text,
        }
        for span in catalog.spans
        if span.source_kind != "structured_visual"
    ]


def _hydrate_caller_owned_source_anchors(
    graph: PaperEvidenceGraph,
    document: DocumentIR,
    *,
    source_catalog: SourceCatalog,
) -> tuple[PaperEvidenceGraph, tuple[str, ...]]:
    """Replace model-written source coordinates with one trusted source span.

    Textual provenance is accepted only through ``source_span_id``. The model
    still owns graph semantics and graph-local anchor IDs, but it cannot own or
    reconstruct artifact identity, page, quote, or geometry. Anchors whose
    reference is missing or invalid are returned unhydrated by ID so the caller
    can isolate only the Claims that depend on them.
    """

    if not graph.anchors:
        return graph, ()

    source_spans = {
        span.id: span
        for span in source_catalog.spans
        if span.source_kind != "structured_visual"
    }
    blocks = {block.id: block for page in document.pages for block in page.blocks}
    payload = graph.model_dump(mode="json")
    unresolved: list[str] = []
    hydrated = False
    artifact = document.artifact

    for anchor, raw_anchor in zip(graph.anchors, payload["anchors"], strict=True):
        source_span_id = anchor.locator.source_span_id
        requires_source_span = (
            anchor.anchor_type in {"text", "citation", "dataset"}
            or anchor.locator.quote is not None
        )
        if source_span_id is None:
            if requires_source_span:
                unresolved.append(anchor.id)
            continue

        span = source_spans.get(source_span_id)
        block = None if span is None else blocks.get(span.block_id)
        if span is None or block is None:
            unresolved.append(anchor.id)
            continue
        try:
            exact_text = resolve_source_span_text(block, span)
        except ValueError:
            unresolved.append(anchor.id)
            continue

        if span.kind in {"figure", "chart", "image"}:
            anchor_type = "figure"
        elif span.kind == "table":
            anchor_type = "table"
        elif span.kind == "citation":
            anchor_type = "citation"
        else:
            anchor_type = "text"
        bbox = span.bbox
        raw_anchor.update(
            {
                "artifact_id": artifact.artifact_id,
                "version_id": artifact.version_id,
                "artifact_sha256": artifact.artifact_sha256,
                "media_type": artifact.media_type,
                "anchor_type": anchor_type,
                "locator": {
                    "page": span.page,
                    "source_span_id": span.id,
                    "section": span.section_title,
                    "paragraph": None,
                    "figure": None,
                    "table": None,
                    "row_range": None,
                    "column_names": [],
                    "series": None,
                    "region": None
                    if bbox is None
                    else {
                        "x": bbox.x,
                        "y": bbox.y,
                        "width": bbox.width,
                        "height": bbox.height,
                        "unit": "normalized",
                    },
                    "quote": exact_text,
                },
            }
        )
        hydrated = True

    output = (
        PaperEvidenceGraph.model_validate(payload, strict=True) if hydrated else graph
    )
    return output, tuple(unresolved)


def _ensure_zero_claim_provenance_anchor(
    graph: PaperEvidenceGraph,
    document: DocumentIR,
) -> PaperEvidenceGraph:
    """Attach one caller-owned source coordinate to an empty extraction.

    Zero Claims are a valid scientific outcome.  Requiring the model to copy a
    title merely to keep the ArtifactVersion reachable creates an unrelated
    failure mode, so the caller supplies this navigation-only anchor from the
    first trusted DocumentIR block.  If OCR yielded no blocks at all, a page-1
    metadata coordinate keeps the ArtifactVersion reachable without inventing
    source text.  No scientific node or interpretation is synthesized.
    """

    if graph.claims or graph.anchors:
        return graph
    first_block = next(
        (block for page in document.pages for block in page.blocks),
        None,
    )
    quote = None
    anchor_type = "metadata"
    if (
        first_block is not None
        and first_block.text is not None
        and first_block.text.strip()
    ):
        quote = first_block.text[:16_000]
        anchor_type = "text"
    source_coordinate = "page:1:metadata" if first_block is None else first_block.id
    digest = hashlib.sha256(
        f"{document.artifact.version_id}\0{source_coordinate}\0zero-claim".encode()
    ).hexdigest()[:24]
    bbox = None if first_block is None else first_block.bbox
    page_number = document.pages[0].number if first_block is None else first_block.page
    payload = graph.model_dump(mode="json")
    payload["anchors"] = [
        {
            "id": f"anchor.provenance.{digest}",
            "artifact_id": document.artifact.artifact_id,
            "version_id": document.artifact.version_id,
            "artifact_sha256": document.artifact.artifact_sha256,
            "media_type": document.artifact.media_type,
            "anchor_type": anchor_type,
            "locator": {
                "page": page_number,
                "section": None,
                "paragraph": None,
                "figure": None,
                "table": None,
                "row_range": None,
                "column_names": [],
                "series": None,
                "region": None
                if bbox is None
                else {
                    "x": bbox.x,
                    "y": bbox.y,
                    "width": bbox.width,
                    "height": bbox.height,
                    "unit": "normalized",
                },
                "quote": quote,
            },
        }
    ]
    return PaperEvidenceGraph.model_validate(payload, strict=True)


def _extraction_contract_violations(
    graph: PaperEvidenceGraph,
    document: DocumentIR,
    *,
    quotes: _QuoteIndex | None = None,
) -> tuple[ContractViolation, ...]:
    """Collect hard source-provenance and evidence-path violations.

    Scientific value/comparator/unit and formula-semantic disagreements are
    verifier findings. They must not reject an otherwise
    traceable, structurally valid extraction before the independent verifier
    can assess and annotate the affected Claim.
    """

    collected: list[ContractViolation] = []
    seen_issues: set[ContractIssue] = set()

    def add(violations: tuple[ContractViolation, ...]) -> None:
        for violation in violations:
            if violation.issue in seen_issues:
                continue
            if len(seen_issues) == _MAX_AGGREGATED_CONTRACT_ISSUES:
                continue
            seen_issues.add(violation.issue)
            collected.append(violation)

    add(_provenance_contract_violations(graph, document, quotes=quotes))
    if len(seen_issues) < _MAX_AGGREGATED_CONTRACT_ISSUES:
        try:
            graph.validate_observation_path_ownership()
        except ContractValidationError as error:
            add(error.violations)
    return tuple(collected)


def _canonicalize_exact_quote_regions(
    graph: PaperEvidenceGraph,
    document: DocumentIR,
    *,
    quotes: _QuoteIndex | None = None,
) -> PaperEvidenceGraph:
    """Copy frozen geometry only for one uniquely quote-matched source block."""

    if not graph.anchors:
        return graph

    payload = graph.model_dump(mode="json")
    quotes = quotes or _QuoteIndex(document, build_source_catalog(document))
    updated = False
    for anchor in payload["anchors"]:
        locator = anchor["locator"]
        page = locator.get("page")
        quote = locator.get("quote")
        if (
            not isinstance(page, int)
            or isinstance(page, bool)
            or page < 1
            or page > document.page_count
            or not isinstance(quote, str)
            or not quote
        ):
            continue
        exact = unicodedata.normalize("NFC", quote)
        allowed_kinds: set[str] | None = None
        if anchor["anchor_type"] == "figure":
            allowed_kinds = {"figure", "image", "chart"}
        elif anchor["anchor_type"] == "table":
            allowed_kinds = {"table"}
        matching = quotes.matching_blocks(page, exact)
        if allowed_kinds is not None:
            matching = [block for block in matching if block.kind in allowed_kinds]
        if len(matching) != 1:
            continue
        bbox = matching[0].bbox
        region = (
            None
            if bbox is None
            else Region(
                x=bbox.x,
                y=bbox.y,
                width=bbox.width,
                height=bbox.height,
                unit="normalized",
            ).model_dump(mode="json")
        )
        if locator.get("region") != region:
            locator["region"] = region
            updated = True
    return PaperEvidenceGraph.model_validate(payload, strict=True) if updated else graph


def _provenance_contract_violations(
    graph: PaperEvidenceGraph,
    document: DocumentIR,
    *,
    quotes: _QuoteIndex | None = None,
) -> tuple[ContractViolation, ...]:
    """Collect distinct provenance failures without exposing source values."""

    artifact = document.artifact
    if graph.paper_id != artifact.paper_id:
        return (
            ContractViolation(
                ContractIssue.PROVENANCE_PAPER_ID_MISMATCH,
                ("paper_id",),
            ),
        )
    violations: list[ContractViolation] = []
    seen: set[ContractIssue] = set()

    def add(issue: ContractIssue, loc: tuple[str | int, ...]) -> None:
        if issue in seen:
            return
        seen.add(issue)
        violations.append(ContractViolation(issue, loc))

    expected = {
        "artifact_id": artifact.artifact_id,
        "version_id": artifact.version_id,
        "artifact_sha256": artifact.artifact_sha256,
        "media_type": artifact.media_type,
    }
    source_by_page = {page.number: page.blocks for page in document.pages}
    quotes = quotes or _QuoteIndex(document, build_source_catalog(document))
    for anchor_index, anchor in enumerate(graph.anchors):
        actual = {
            "artifact_id": anchor.artifact_id,
            "version_id": anchor.version_id,
            "artifact_sha256": anchor.artifact_sha256,
            "media_type": anchor.media_type,
        }
        if actual != expected:
            add(
                ContractIssue.PROVENANCE_ARTIFACT_IDENTITY_MISMATCH,
                ("anchors", anchor_index),
            )
            continue
        page = anchor.locator.page
        if page is None or page > document.page_count:
            add(
                ContractIssue.PROVENANCE_PAGE_INVALID,
                ("anchors", anchor_index, "locator", "page"),
            )
            continue
        quote = anchor.locator.quote
        blocks = source_by_page.get(page, [])
        matching = blocks
        if quote is not None:
            matching = quotes.matching_blocks(page, unicodedata.normalize("NFC", quote))
            if not matching:
                add(
                    ContractIssue.PROVENANCE_QUOTE_NOT_FOUND,
                    ("anchors", anchor_index, "locator", "quote"),
                )
                # Region validity depends on the same matched source block. A
                # missing quote is the actionable root failure for this anchor.
                continue
        elif anchor.anchor_type in {"figure", "table"}:
            kinds = (
                {"figure", "image", "chart"}
                if anchor.anchor_type == "figure"
                else {"table"}
            )
            matching = [block for block in blocks if block.kind in kinds]
            if not matching:
                add(
                    ContractIssue.PROVENANCE_VISUAL_BLOCK_NOT_FOUND,
                    ("anchors", anchor_index, "locator"),
                )
                # Do not emit a derivative region failure without a source block.
                continue
        region = anchor.locator.region
        if region is not None:
            if region.unit != "normalized":
                add(
                    ContractIssue.PROVENANCE_REGION_UNIT_INVALID,
                    ("anchors", anchor_index, "locator", "region"),
                )
                continue
            if not any(_same_region(region, block.bbox) for block in matching):
                add(
                    ContractIssue.PROVENANCE_REGION_MISMATCH,
                    ("anchors", anchor_index, "locator", "region"),
                )
    return tuple(violations)


class _QuoteIndex:
    """Per-page NFC block text and admitted code spans, computed once per compile.

    Exact-quote lookups run for every anchor on every isolation round; with this
    index each lookup reads only the anchor's own page (GRF-16). Matching is
    unchanged: ordinary block text by substring, then structured ``code_body``
    and ``code_caption`` leaves admitted by ``SourceCatalog`` by exact equality,
    each resolved against its artifact, version, page, block, and source slice.
    Structured content is intentionally not searched recursively.
    """

    def __init__(self, document: DocumentIR, source_catalog: SourceCatalog) -> None:
        self._text: dict[int, list[tuple[DocumentBlock, str]]] = {
            page.number: [
                (block, unicodedata.normalize("NFC", block.text or ""))
                for block in page.blocks
            ]
            for page in document.pages
        }
        blocks = {
            page.number: {block.id: block for block in page.blocks}
            for page in document.pages
        }
        artifact = document.artifact
        self._code: dict[int, list[tuple[DocumentBlock, str]]] = {}
        for span in source_catalog.spans:
            if (
                span.source_kind not in {"code_body", "code_caption"}
                or span.artifact_id != artifact.artifact_id
                or span.version_id != artifact.version_id
            ):
                continue
            block = blocks.get(span.page, {}).get(span.block_id)
            if block is None or block.page != span.page:
                continue
            try:
                resolved = resolve_source_span_text(block, span)
            except ValueError:
                continue
            self._code.setdefault(span.page, []).append(
                (block, unicodedata.normalize("NFC", resolved))
            )

    def matching_blocks(self, page: int, exact_nfc_quote: str) -> list[DocumentBlock]:
        matching = [
            block
            for block, text in self._text.get(page, ())
            if exact_nfc_quote in text
        ]
        matched = {block.id for block in matching}
        for block, text in self._code.get(page, ()):
            if text == exact_nfc_quote and block.id not in matched:
                matching.append(block)
                matched.add(block.id)
        return matching


def _same_region(region: object, bbox: object) -> bool:
    if bbox is None:
        return False
    return all(
        abs(float(getattr(region, key)) - float(getattr(bbox, key))) <= 0.001
        for key in ("x", "y", "width", "height")
    )


def _run_id(
    stage: str,
    content_hash: str,
    prompt_version: str,
    document: DocumentIR,
    *,
    model: str = GPT_MODEL,
) -> str:
    parser = document.parser
    payload = "\0".join(
        (
            model,
            prompt_version,
            content_hash,
            parser.name,
            parser.version,
            parser.backend,
            parser.method,
            parser.transport,
        )
    )
    return f"{stage}:sha256:{hashlib.sha256(payload.encode()).hexdigest()}"


__all__ = ["CompileOutcome", "EvidenceCompiler", "compact_document_ir"]
