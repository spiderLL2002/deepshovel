"""Ground-truth evaluation for Citation Reception.

The source track is deterministic and offline.  The reception track evaluates
already-materialized model envelopes, or can deliberately create a resumable
live Model run when ``--live-output`` is supplied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, ValidationError, model_validator

from .citation_reception import (
    CitationPairRequest,
    CitationReceptionAnalyzer,
    CitationReceptionEnvelope,
)
from .citation_source import (
    CitationFunctionCandidate,
    build_citation_source,
)
from .model_client import ModelJsonClient
from .models import (
    ArtifactContext,
    DocumentBlock,
    DocumentIR,
    DocumentPage,
    Identifier,
    ParserMetadata,
    StrictModel,
)

EVALUATION_SCHEMA_VERSION = "citation-reception-eval/1.0"
GROUND_TRUTH_SCHEMA_VERSION = "citation-reception-ground-truth/1.0"
RUN_SCHEMA_VERSION = "citation-reception-run/1.0"
MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_CASES = 1_000

EvalText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64_000)
]
EvalUrl = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=8,
        max_length=2_000,
        pattern=r"^https://[^\s]+$",
    ),
]


class EvaluationInputError(ValueError):
    """Raised when an evaluation artifact fails its closed contract."""


class CaseProvenance(StrictModel):
    dataset: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]
    record_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]
    citing_paper_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]
    cited_paper_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]
    source_url: EvalUrl
    license: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    corpus_revision: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]


class GroundTruthReference(StrictModel):
    label: Annotated[
        str, StringConstraints(pattern=r"^[0-9]{1,5}$", min_length=1, max_length=5)
    ]
    raw_text: EvalText


class ExpectedSourceMention(StrictModel):
    reference_label: Annotated[
        str, StringConstraints(pattern=r"^[0-9]{1,5}$", min_length=1, max_length=5)
    ]
    marker: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]
    group_size: Annotated[int, Field(ge=1, le=100)]
    required_functions: Annotated[
        list[CitationFunctionCandidate], Field(min_length=1, max_length=3)
    ]

    @model_validator(mode="after")
    def unique_functions(self) -> ExpectedSourceMention:
        if len(self.required_functions) != len(set(self.required_functions)):
            raise ValueError("required source functions must be unique")
        return self


class SourceGroundTruthCase(StrictModel):
    schema_version: Literal["citation-reception-ground-truth/1.0"]
    track: Literal["source"]
    case_id: Identifier
    provenance: CaseProvenance
    context: EvalText
    references: Annotated[
        list[GroundTruthReference], Field(min_length=1, max_length=100)
    ]
    expected_mentions: Annotated[list[ExpectedSourceMention], Field(max_length=100)]
    annotation_notes: EvalText

    @model_validator(mode="after")
    def closed_source_case(self) -> SourceGroundTruthCase:
        labels = [item.label for item in self.references]
        if len(labels) != len(set(labels)):
            raise ValueError("source reference labels must be unique")
        known = set(labels)
        keys = [(item.reference_label, item.marker) for item in self.expected_mentions]
        if not all(item.reference_label in known for item in self.expected_mentions):
            raise ValueError("expected mention references an unknown source label")
        if len(keys) != len(set(keys)):
            raise ValueError("expected source mentions must be unique")
        return self


class ReceptionGold(StrictModel):
    should_emit: bool
    acceptable_functions: Annotated[
        list[CitationFunctionCandidate], Field(max_length=3)
    ]
    acceptable_stances: Annotated[
        list[Literal["supports", "contradicts", "qualifies", "unclear"]],
        Field(max_length=4),
    ]
    acceptable_confirmation_bases: Annotated[
        list[
            Literal[
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
        ],
        Field(max_length=6),
    ]
    acceptable_target_entity_ids: Annotated[
        list[Identifier | None], Field(max_length=16)
    ]
    allowed_citing_evidence_entity_ids: Annotated[
        list[Identifier], Field(max_length=32)
    ]
    acceptable_faithfulness: Annotated[
        list[
            Literal[
                "faithful",
                "partial",
                "overgeneralized",
                "contradicted_by_target",
                "not_found",
                "ambiguous",
                "indirect",
            ]
        ],
        Field(max_length=7),
    ]

    @model_validator(mode="after")
    def coherent_gold(self) -> ReceptionGold:
        collections = (
            self.acceptable_functions,
            self.acceptable_stances,
            self.acceptable_confirmation_bases,
            self.acceptable_target_entity_ids,
            self.allowed_citing_evidence_entity_ids,
            self.acceptable_faithfulness,
        )
        if any(len(items) != len(set(items)) for items in collections):
            raise ValueError("reception gold collections must be unique")
        required = (
            self.acceptable_functions,
            self.acceptable_stances,
            self.acceptable_confirmation_bases,
            self.acceptable_faithfulness,
        )
        if self.should_emit and any(not items for items in required):
            raise ValueError("positive reception cases require complete labels")
        if not self.should_emit and any(required):
            raise ValueError("negative reception cases must not carry assertion labels")
        return self


class ReceptionGroundTruthCase(StrictModel):
    schema_version: Literal["citation-reception-ground-truth/1.0"]
    track: Literal["reception"]
    case_id: Identifier
    provenance: CaseProvenance
    pair: CitationPairRequest
    gold: ReceptionGold
    annotation_notes: EvalText


GroundTruthCase = SourceGroundTruthCase | ReceptionGroundTruthCase


class ReceptionRunRecord(StrictModel):
    schema_version: Literal["citation-reception-run/1.0"] = RUN_SCHEMA_VERSION
    case_id: Identifier
    status: Literal["ok", "error"]
    envelope: CitationReceptionEnvelope | None
    error: Annotated[
        str | None,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000),
    ]

    @model_validator(mode="after")
    def coherent_status(self) -> ReceptionRunRecord:
        if self.status == "ok" and (self.envelope is None or self.error is not None):
            raise ValueError("successful reception run requires only an envelope")
        if self.status == "error" and (self.error is None or self.envelope is not None):
            raise ValueError("failed reception run requires only an error")
        return self


class CaseFailure(StrictModel):
    case_id: Identifier
    issues: list[str]


class SourceMetrics(StrictModel):
    case_count: int
    passed_cases: int
    expected_mentions: int
    predicted_mentions: int
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float
    required_function_coverage: float
    group_size_accuracy: float
    failures: list[CaseFailure]


class ReceptionMetrics(StrictModel):
    case_count: int
    completed_cases: int
    positive_cases: int
    predicted_assertions: int
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float
    function_accuracy: float
    stance_accuracy: float
    confirmation_basis_accuracy: float
    target_accuracy: float
    evidence_accuracy: float
    faithfulness_accuracy: float
    unsafe_strong_confirmations: int
    failures: list[CaseFailure]


class EvaluationReport(StrictModel):
    schema_version: Literal["citation-reception-eval/1.0"]
    ground_truth_cases: int
    source: SourceMetrics
    reception_runs: dict[str, ReceptionMetrics]
    release_gate_passed: bool
    release_gate_reasons: list[str]


_STRONG_BASES = frozenset(
    {
        "consistent_with_citing_evidence",
        "contradicted_by_citing_evidence",
        "qualified_by_citing_evidence",
        "direct_replication",
        "reanalysis",
        "meta_analytic_synthesis",
    }
)


def _ratio(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else round(numerator / denominator, 12)


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return round(2 * precision * recall / (precision + recall), 12)


def _duplicate_key_rejector(pairs: list[tuple[str, object]]) -> dict[str, object]:
    output: dict[str, object] = {}
    for key, value in pairs:
        if key in output:
            raise EvaluationInputError(f"duplicate JSON object key: {key}")
        output[key] = value
    return output


def _load_jsonl(path: Path) -> list[object]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise EvaluationInputError(f"cannot read evaluation input: {path}") from exc
    if not raw or len(raw) > MAX_INPUT_BYTES:
        raise EvaluationInputError(
            f"evaluation input must contain 1-{MAX_INPUT_BYTES} bytes: {path}"
        )
    records: list[object] = []
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            raise EvaluationInputError(f"blank JSONL line at {path}:{line_number}")
        try:
            records.append(
                json.loads(
                    line.decode("utf-8", errors="strict"),
                    object_pairs_hook=_duplicate_key_rejector,
                    parse_constant=lambda value: (_ for _ in ()).throw(
                        EvaluationInputError(f"non-finite JSON number: {value}")
                    ),
                )
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvaluationInputError(
                f"invalid UTF-8 JSON at {path}:{line_number}: {exc}"
            ) from exc
    if len(records) > MAX_CASES:
        raise EvaluationInputError(f"evaluation input exceeds {MAX_CASES} cases")
    return records


def load_ground_truth(path: str | Path) -> tuple[GroundTruthCase, ...]:
    output: list[GroundTruthCase] = []
    for index, raw in enumerate(_load_jsonl(Path(path)), start=1):
        if not isinstance(raw, dict):
            raise EvaluationInputError(f"ground-truth record {index} is not an object")
        track = raw.get("track")
        model: type[SourceGroundTruthCase] | type[ReceptionGroundTruthCase]
        if track == "source":
            model = SourceGroundTruthCase
        elif track == "reception":
            model = ReceptionGroundTruthCase
        else:
            raise EvaluationInputError(f"ground-truth record {index} has bad track")
        try:
            output.append(model.model_validate(raw, strict=True))
        except ValidationError as exc:
            raise EvaluationInputError(
                f"invalid ground-truth record {index}: {exc}"
            ) from exc
    case_ids = [item.case_id for item in output]
    if not output or len(case_ids) != len(set(case_ids)):
        raise EvaluationInputError("ground truth must be nonempty with unique case IDs")
    return tuple(output)


def load_run(path: str | Path) -> tuple[ReceptionRunRecord, ...]:
    output: list[ReceptionRunRecord] = []
    for index, raw in enumerate(_load_jsonl(Path(path)), start=1):
        try:
            output.append(ReceptionRunRecord.model_validate(raw, strict=True))
        except ValidationError as exc:
            raise EvaluationInputError(f"invalid run record {index}: {exc}") from exc
    case_ids = [item.case_id for item in output]
    if len(case_ids) != len(set(case_ids)):
        raise EvaluationInputError("run case IDs must be unique")
    return tuple(output)


def _document_for_case(case: SourceGroundTruthCase) -> DocumentIR:
    digest = hashlib.sha256(case.case_id.encode("utf-8")).hexdigest()
    artifact = ArtifactContext(
        paper_id=f"paper.eval.{case.case_id}",
        artifact_id=f"artifact.eval.{case.case_id}",
        version_id=f"artifact-version:sha256:{digest}",
        artifact_sha256=digest,
        media_type="application/pdf",
        locator=f"benchmark://{case.case_id}",
        file_name=f"{case.case_id}.pdf",
    )
    blocks = [
        DocumentBlock(
            id=f"block.eval.{case.case_id}.context",
            kind="paragraph",
            page=1,
            source_index=0,
            bbox=None,
            text=case.context,
        ),
        DocumentBlock(
            id=f"block.eval.{case.case_id}.heading",
            kind="title",
            page=1,
            source_index=1,
            bbox=None,
            text="References",
        ),
        DocumentBlock(
            id=f"block.eval.{case.case_id}.references",
            kind="citation",
            page=1,
            source_index=2,
            bbox=None,
            text="\n".join(
                f"[{item.label}] {item.raw_text}" for item in case.references
            ),
        ),
    ]
    return DocumentIR(
        artifact=artifact,
        parser=ParserMetadata(
            name="mineru",
            version="citation-ground-truth-v1",
            backend="pipeline",
            method="auto",
            transport="remote",
        ),
        page_count=1,
        pages=[DocumentPage(number=1, blocks=blocks)],
        markdown=case.context,
    )


def evaluate_source(cases: Sequence[SourceGroundTruthCase]) -> SourceMetrics:
    expected_total = predicted_total = true_positives = 0
    false_positives = false_negatives = function_hits = group_hits = 0
    passed_cases = 0
    failures: list[CaseFailure] = []
    for case in cases:
        source = build_citation_source(_document_for_case(case))
        reference_labels = {item.id: item.label for item in source.references}
        predicted = {
            (reference_labels[item.reference_id], item.marker): item
            for item in source.mentions
            if item.candidate_functions
        }
        expected = {
            (item.reference_label, item.marker): item for item in case.expected_mentions
        }
        predicted_keys, expected_keys = set(predicted), set(expected)
        matched = predicted_keys & expected_keys
        expected_total += len(expected)
        predicted_total += len(predicted)
        true_positives += len(matched)
        false_positives += len(predicted_keys - expected_keys)
        false_negatives += len(expected_keys - predicted_keys)
        issues: list[str] = []
        if missing := sorted(expected_keys - predicted_keys):
            issues.append(f"missing mentions: {missing}")
        if extra := sorted(predicted_keys - expected_keys):
            issues.append(f"unexpected mentions: {extra}")
        for key in sorted(matched):
            actual = predicted[key]
            gold = expected[key]
            required = set(gold.required_functions)
            if required.issubset(actual.candidate_functions):
                function_hits += 1
            else:
                issues.append(
                    f"{key} missing functions "
                    f"{sorted(required - set(actual.candidate_functions))}"
                )
            if actual.group_size == gold.group_size:
                group_hits += 1
            else:
                issues.append(
                    f"{key} group_size={actual.group_size}, expected={gold.group_size}"
                )
        if issues:
            failures.append(CaseFailure(case_id=case.case_id, issues=issues))
        else:
            passed_cases += 1
    precision = _ratio(true_positives, true_positives + false_positives)
    recall = _ratio(true_positives, true_positives + false_negatives)
    return SourceMetrics(
        case_count=len(cases),
        passed_cases=passed_cases,
        expected_mentions=expected_total,
        predicted_mentions=predicted_total,
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        precision=precision,
        recall=recall,
        f1=_f1(precision, recall),
        required_function_coverage=_ratio(function_hits, expected_total),
        group_size_accuracy=_ratio(group_hits, expected_total),
        failures=failures,
    )


def _assessment_by_assertion(
    envelope: CitationReceptionEnvelope,
) -> dict[str, object]:
    if envelope.verification is None:
        return {}
    return {item.assertion_id: item for item in envelope.verification.assessments}


def evaluate_reception(
    cases: Sequence[ReceptionGroundTruthCase],
    run: Sequence[ReceptionRunRecord],
) -> ReceptionMetrics:
    by_case = {item.case_id: item for item in run}
    unknown = sorted(set(by_case) - {item.case_id for item in cases})
    if unknown:
        raise EvaluationInputError(f"run contains unknown reception cases: {unknown}")
    tp = fp = fn = predicted_total = completed = 0
    function_hits = stance_hits = basis_hits = target_hits = 0
    evidence_hits = faithfulness_hits = unsafe = property_denominator = 0
    failures: list[CaseFailure] = []
    for case in cases:
        record = by_case.get(case.case_id)
        issues: list[str] = []
        if record is None:
            fn += int(case.gold.should_emit)
            issues.append("missing run record")
            failures.append(CaseFailure(case_id=case.case_id, issues=issues))
            continue
        if record.status == "error":
            fn += int(case.gold.should_emit)
            issues.append(f"model error: {record.error}")
            failures.append(CaseFailure(case_id=case.case_id, issues=issues))
            continue
        completed += 1
        assert record.envelope is not None
        assertions = record.envelope.extraction.assertions
        predicted_total += len(assertions)
        if case.gold.should_emit:
            if assertions:
                tp += 1
                fp += max(0, len(assertions) - 1)
            else:
                fn += 1
                issues.append("expected one assertion, got zero")
                failures.append(CaseFailure(case_id=case.case_id, issues=issues))
                continue
        else:
            fp += len(assertions)
            if assertions:
                issues.append(
                    f"expected abstention, got {len(assertions)} assertion(s)"
                )
        if not assertions:
            if issues:
                failures.append(CaseFailure(case_id=case.case_id, issues=issues))
            continue
        gold_targets = set(case.gold.acceptable_target_entity_ids)
        selected = next(
            (
                item
                for item in assertions
                if item.target_entity_id in gold_targets
                and item.function in case.gold.acceptable_functions
            ),
            assertions[0],
        )
        assessments = _assessment_by_assertion(record.envelope)
        assessment = assessments.get(selected.id)
        final_basis = (
            selected.confirmation_basis
            if assessment is None
            else assessment.confirmation_basis
        )
        if case.gold.should_emit:
            property_denominator += 1
            function_hits += int(selected.function in case.gold.acceptable_functions)
            stance_hits += int(selected.stance in case.gold.acceptable_stances)
            basis_hits += int(final_basis in case.gold.acceptable_confirmation_bases)
            target_hits += int(selected.target_entity_id in gold_targets)
            evidence_hits += int(
                set(selected.citing_evidence_entity_ids).issubset(
                    case.gold.allowed_citing_evidence_entity_ids
                )
                and (
                    final_basis not in _STRONG_BASES
                    or bool(selected.citing_evidence_entity_ids)
                )
            )
            faithfulness_hits += int(
                assessment is not None
                and assessment.faithfulness in case.gold.acceptable_faithfulness
            )
            checks = (
                (selected.function in case.gold.acceptable_functions, "function"),
                (selected.stance in case.gold.acceptable_stances, "stance"),
                (
                    final_basis in case.gold.acceptable_confirmation_bases,
                    "confirmation basis",
                ),
                (selected.target_entity_id in gold_targets, "target"),
                (
                    assessment is not None
                    and assessment.faithfulness in case.gold.acceptable_faithfulness,
                    "faithfulness",
                ),
            )
            issues.extend(f"incorrect {label}" for ok, label in checks if not ok)
        weak_gold = not (set(case.gold.acceptable_confirmation_bases) & _STRONG_BASES)
        for assertion in assertions:
            assertion_assessment = assessments.get(assertion.id)
            bases = {assertion.confirmation_basis}
            if assertion_assessment is not None:
                bases.add(assertion_assessment.confirmation_basis)
            bad_evidence = not set(assertion.citing_evidence_entity_ids).issubset(
                case.gold.allowed_citing_evidence_entity_ids
            )
            if bases & _STRONG_BASES and (
                not case.gold.should_emit or weak_gold or bad_evidence
            ):
                unsafe += 1
                issues.append("unsafe strong confirmation")
        if issues:
            failures.append(CaseFailure(case_id=case.case_id, issues=issues))
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    return ReceptionMetrics(
        case_count=len(cases),
        completed_cases=completed,
        positive_cases=sum(item.gold.should_emit for item in cases),
        predicted_assertions=predicted_total,
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        precision=precision,
        recall=recall,
        f1=_f1(precision, recall),
        function_accuracy=_ratio(function_hits, property_denominator),
        stance_accuracy=_ratio(stance_hits, property_denominator),
        confirmation_basis_accuracy=_ratio(basis_hits, property_denominator),
        target_accuracy=_ratio(target_hits, property_denominator),
        evidence_accuracy=_ratio(evidence_hits, property_denominator),
        faithfulness_accuracy=_ratio(faithfulness_hits, property_denominator),
        unsafe_strong_confirmations=unsafe,
        failures=failures,
    )


def evaluate(
    ground_truth: Sequence[GroundTruthCase],
    runs: Mapping[str, Sequence[ReceptionRunRecord]],
) -> EvaluationReport:
    source_cases = [item for item in ground_truth if item.track == "source"]
    reception_cases = [item for item in ground_truth if item.track == "reception"]
    if not source_cases or not reception_cases:
        raise EvaluationInputError(
            "ground truth requires both source and reception tracks"
        )
    source = evaluate_source(source_cases)
    reception = {
        name: evaluate_reception(reception_cases, records)
        for name, records in sorted(runs.items())
    }
    reasons: list[str] = []
    if source.precision < 0.9:
        reasons.append("source precision below 0.90")
    if source.recall < 0.9:
        reasons.append("source recall below 0.90")
    if source.required_function_coverage < 0.9:
        reasons.append("source function coverage below 0.90")
    if source.group_size_accuracy < 0.95:
        reasons.append("source group-size accuracy below 0.95")
    if not reception:
        reasons.append("no reception run supplied")
    for name, metrics in reception.items():
        if metrics.completed_cases != metrics.case_count:
            reasons.append(
                f"{name} completed {metrics.completed_cases}/{metrics.case_count} cases"
            )
        for label, value in (
            ("precision", metrics.precision),
            ("recall", metrics.recall),
            ("F1", metrics.f1),
            ("function accuracy", metrics.function_accuracy),
            ("stance accuracy", metrics.stance_accuracy),
            ("confirmation-basis accuracy", metrics.confirmation_basis_accuracy),
            ("target accuracy", metrics.target_accuracy),
            ("evidence accuracy", metrics.evidence_accuracy),
            ("faithfulness accuracy", metrics.faithfulness_accuracy),
        ):
            threshold = 0.9 if label == "precision" else 0.8
            if value < threshold:
                reasons.append(f"{name} {label} below {threshold:.2f}")
        if metrics.unsafe_strong_confirmations:
            reasons.append(f"{name} has unsafe strong confirmations")
    return EvaluationReport(
        schema_version=EVALUATION_SCHEMA_VERSION,
        ground_truth_cases=len(ground_truth),
        source=source,
        reception_runs=reception,
        release_gate_passed=not reasons,
        release_gate_reasons=reasons,
    )


def _atomic_write_run(path: Path, records: Sequence[ReceptionRunRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(
            json.dumps(
                item.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
            for item in records
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def generate_live_run(
    cases: Sequence[ReceptionGroundTruthCase],
    path: Path,
    *,
    workers: int = 1,
    retry_errors: bool = False,
) -> tuple[ReceptionRunRecord, ...]:
    if not 1 <= workers <= 8:
        raise EvaluationInputError("live workers must be between 1 and 8")
    existing = {item.case_id: item for item in load_run(path)} if path.exists() else {}
    if retry_errors:
        existing = {
            case_id: record
            for case_id, record in existing.items()
            if record.status == "ok"
        }
    client = ModelJsonClient.from_api_key(
        os.environ.get("GPT_API_KEY", "").strip() or None,
        deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY", "").strip() or None,
    )
    analyzer = CitationReceptionAnalyzer(client)

    def run_one(case: ReceptionGroundTruthCase) -> ReceptionRunRecord:
        try:
            envelope = analyzer.analyze(case.pair)
            return ReceptionRunRecord(
                case_id=case.case_id,
                status="ok",
                envelope=envelope,
                error=None,
            )
        except Exception as exc:  # noqa: BLE001 - error is benchmark data
            return ReceptionRunRecord(
                case_id=case.case_id,
                status="error",
                envelope=None,
                error=f"{type(exc).__name__}: {str(exc)[:1900]}",
            )

    pending = [item for item in cases if item.case_id not in existing]
    for case in cases:
        if case.case_id in existing:
            print(
                f"reuse {case.case_id}: {existing[case.case_id].status}",
                file=sys.stderr,
                flush=True,
            )
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_cases = {executor.submit(run_one, case): case for case in pending}
        for completed, future in enumerate(as_completed(future_cases), start=1):
            case = future_cases[future]
            record = future.result()
            existing[case.case_id] = record
            ordered_checkpoint = [
                existing[item.case_id] for item in cases if item.case_id in existing
            ]
            _atomic_write_run(path, ordered_checkpoint)
            print(
                f"[{completed}/{len(pending)}] {case.case_id}: {record.status}",
                file=sys.stderr,
                flush=True,
            )
    return tuple(existing[item.case_id] for item in cases)


def _parse_named_run(value: str) -> tuple[str, Path]:
    name, separator, raw_path = value.partition("=")
    if not separator or not name or not raw_path:
        raise EvaluationInputError("--run must use NAME=PATH")
    if not name.replace("-", "").replace("_", "").isalnum():
        raise EvaluationInputError("run name must use letters, numbers, '-' or '_'")
    return name, Path(raw_path)


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate Citation Reception against fixed real-paper ground truth."
    )
    parser.add_argument("--ground-truth", required=True, type=Path)
    parser.add_argument(
        "--run",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="materialized reception run; repeat to compare prompts/models",
    )
    parser.add_argument(
        "--live-output",
        type=Path,
        help=(
            "run missing reception cases through configured Model and checkpoint here"
        ),
    )
    parser.add_argument("--live-name", default="live")
    parser.add_argument(
        "--live-workers",
        type=int,
        default=1,
        choices=range(1, 9),
        metavar="N",
    )
    parser.add_argument(
        "--retry-errors",
        action="store_true",
        help="rerun only checkpointed error records while reusing successful cases",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _argument_parser()
    args = parser.parse_args(argv)
    try:
        ground_truth = load_ground_truth(args.ground_truth)
        reception_cases = [item for item in ground_truth if item.track == "reception"]
        named_runs: dict[str, Sequence[ReceptionRunRecord]] = {}
        for raw in args.run:
            name, path = _parse_named_run(raw)
            if name in named_runs:
                raise EvaluationInputError(f"duplicate run name: {name}")
            named_runs[name] = load_run(path)
        if args.live_output is not None:
            if args.live_name in named_runs:
                raise EvaluationInputError(f"duplicate run name: {args.live_name}")
            named_runs[args.live_name] = generate_live_run(
                reception_cases,
                args.live_output,
                workers=args.live_workers,
                retry_errors=args.retry_errors,
            )
        report = evaluate(ground_truth, named_runs)
    except EvaluationInputError as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
