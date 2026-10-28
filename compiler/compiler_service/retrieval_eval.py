"""Bounded, deterministic offline evaluation for claim retrieval runs.

The evaluator never imports retrieval providers and never calls a model or the
network.  It consumes either JSON arrays or JSONL records:

Judgment record::

    {
      "query_id": "q-1",
      "query_text": "Does A improve B?",
      "judgments": [
        {
          "document_id": "claim-1",
          "relevance": 3,
          "relation": "supports",
          "cluster_id": "gold-proposition-1"
        }
      ]
    }

Run record (the result list order is the rank order)::

    {
      "query_id": "q-1",
      "results": [
        {
          "document_id": "claim-1",
          "score": 12.4,
          "relation": "supports",
          "cluster_id": "predicted-proposition-1",
          "traceable": true
        }
      ]
    }

``cluster_id`` is mandatory on run results so Redundancy@10 remains measurable
for every baseline.  Set it to ``document_id`` for a no-collapse baseline.
Gold relation and cluster labels are optional; their metrics are emitted only
when at least one corresponding gold label exists.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Literal, TypeVar

from pydantic import Field, StringConstraints, ValidationError, field_validator

from .models import StrictModel

EVALUATION_SCHEMA_VERSION = "evidence-retrieval-eval/1.0"
MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_JSONL_LINE_BYTES = 2 * 1024 * 1024
MAX_QUERIES = 10_000
MAX_JUDGMENTS_PER_QUERY = 100_000
MAX_RESULTS_PER_QUERY = 1_000
MAX_RUNS = 16

RelationLabel = Literal[
    "equivalent",
    "supports",
    "contradicts",
    "qualifies",
    "refines",
    "supplements",
    "related",
    "unrelated",
]
RELATION_LABEL_ORDER: tuple[str, ...] = (
    "equivalent",
    "supports",
    "contradicts",
    "qualifies",
    "refines",
    "supplements",
    "related",
    "unrelated",
)

EvalIdentifier = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=256,
        pattern=r"^[^\s\x00-\x1f\x7f]+$",
    ),
]
QueryText = Annotated[str, StringConstraints(min_length=1, max_length=8_000)]


class EvaluationInputError(ValueError):
    """Raised when an evaluation input fails a closed boundary."""


class DocumentJudgment(StrictModel):
    document_id: EvalIdentifier
    relevance: Literal[0, 1, 2, 3]
    relation: RelationLabel | None = None
    cluster_id: EvalIdentifier | None = None


class QueryJudgments(StrictModel):
    query_id: EvalIdentifier
    query_text: QueryText
    judgments: Annotated[
        list[DocumentJudgment],
        Field(max_length=MAX_JUDGMENTS_PER_QUERY),
    ]

    @field_validator("query_text")
    @classmethod
    def query_text_is_canonical(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("query_text must not have surrounding whitespace")
        if "\x00" in value:
            raise ValueError("query_text must not contain NUL")
        return value

    @field_validator("judgments")
    @classmethod
    def judgments_are_unique(
        cls, values: list[DocumentJudgment]
    ) -> list[DocumentJudgment]:
        document_ids = [item.document_id for item in values]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("judgment document_id values must be unique per query")
        return values


class RunResult(StrictModel):
    document_id: EvalIdentifier
    score: Annotated[float | int, Field()] | None = None
    relation: RelationLabel | None = None
    cluster_id: EvalIdentifier
    traceable: bool


class QueryRun(StrictModel):
    query_id: EvalIdentifier
    results: Annotated[list[RunResult], Field(max_length=MAX_RESULTS_PER_QUERY)]

    @field_validator("results")
    @classmethod
    def results_are_unique(cls, values: list[RunResult]) -> list[RunResult]:
        document_ids = [item.document_id for item in values]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("run document_id values must be unique per query")
        return values


EvalQuery = TypeVar("EvalQuery", QueryJudgments, QueryRun)


class PrecisionRecallF1(StrictModel):
    precision: float
    recall: float
    f1: float
    true_positives: int
    false_positives: int
    false_negatives: int


class RelationMetrics(StrictModel):
    evaluated_pairs: int
    labels: list[str]
    macro_f1: float
    micro_precision: float
    micro_recall: float
    micro_f1: float
    per_label: dict[str, PrecisionRecallF1]


class ClusterMetrics(StrictModel):
    evaluated_documents: int
    predicted_pairs: int
    gold_pairs: int
    true_positive_pairs: int
    pairwise_precision: float
    pairwise_recall: float
    pairwise_f1: float


class RunMetrics(StrictModel):
    query_count: int
    positive_query_count: int
    zero_relevant_query_count: int
    result_count: int
    ndcg_at_10: float
    recall_at_100: float
    recall_at_12: float
    precision_at_10: float
    mrr: float
    hit_at_1: float
    hit_at_3: float
    hit_at_12: float
    false_positive_query_count: int
    false_positive_query_rate: float
    mean_results_on_zero_relevant: float
    explicit_hard_negative_count: int
    explicit_hard_negative_hits_at_12: int
    hard_negative_intrusion_rate_at_12: float
    redundancy_at_10: float
    traceability_rate: float
    relation: RelationMetrics | None
    clustering: ClusterMetrics | None


class EvaluationReport(StrictModel):
    schema_version: Literal["evidence-retrieval-eval/1.0"]
    primary_metric: Literal["ndcg_at_10"]
    query_count: int
    runs: dict[str, RunMetrics]
    ranking_by_ndcg_at_10: list[str]


def _clean_metric(value: float) -> float:
    """Keep serialized metrics stable while retaining useful precision."""

    if abs(value) < 5e-13:
        return 0.0
    return round(value, 12)


def _safe_ratio(numerator: int | float, denominator: int | float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _dcg(grades: Sequence[int]) -> float:
    return sum(
        ((2**grade) - 1) / math.log2(rank + 2) for rank, grade in enumerate(grades)
    )


def _pair_count(counts: Counter[object]) -> int:
    return sum(count * (count - 1) // 2 for count in counts.values())


def _index_queries(records: Sequence[EvalQuery], *, kind: str) -> dict[str, EvalQuery]:
    indexed: dict[str, EvalQuery] = {}
    for record in records:
        if record.query_id in indexed:
            raise EvaluationInputError(f"duplicate {kind} query_id: {record.query_id}")
        indexed[record.query_id] = record
    return indexed


def _validate_run_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name):
        raise EvaluationInputError(
            "run name must be 1-64 ASCII letters, digits, dots, underscores, or hyphens"
        )
    return name


def _relation_metrics(
    judgments: Mapping[str, QueryJudgments], runs: Mapping[str, QueryRun]
) -> RelationMetrics | None:
    labeled_pairs: list[tuple[str, str | None]] = []
    for query_id in sorted(judgments):
        result_by_id = {item.document_id: item for item in runs[query_id].results}
        for judgment in judgments[query_id].judgments:
            if judgment.relation is None:
                continue
            result = result_by_id.get(judgment.document_id)
            labeled_pairs.append(
                (judgment.relation, result.relation if result is not None else None)
            )
    if not labeled_pairs:
        return None

    present_labels = {gold for gold, _ in labeled_pairs}
    present_labels.update(predicted for _, predicted in labeled_pairs if predicted)
    labels = [label for label in RELATION_LABEL_ORDER if label in present_labels]
    per_label: dict[str, PrecisionRecallF1] = {}
    total_tp = 0
    total_fp = 0
    total_fn = 0
    for label in labels:
        tp = sum(
            gold == label and predicted == label for gold, predicted in labeled_pairs
        )
        fp = sum(
            gold != label and predicted == label for gold, predicted in labeled_pairs
        )
        fn = sum(
            gold == label and predicted != label for gold, predicted in labeled_pairs
        )
        precision = _safe_ratio(tp, tp + fp)
        recall = _safe_ratio(tp, tp + fn)
        per_label[label] = PrecisionRecallF1(
            precision=_clean_metric(precision),
            recall=_clean_metric(recall),
            f1=_clean_metric(_f1(precision, recall)),
            true_positives=tp,
            false_positives=fp,
            false_negatives=fn,
        )
        total_tp += tp
        total_fp += fp
        total_fn += fn

    micro_precision = _safe_ratio(total_tp, total_tp + total_fp)
    micro_recall = _safe_ratio(total_tp, total_tp + total_fn)
    macro_f1 = sum(item.f1 for item in per_label.values()) / len(per_label)
    return RelationMetrics(
        evaluated_pairs=len(labeled_pairs),
        labels=labels,
        macro_f1=_clean_metric(macro_f1),
        micro_precision=_clean_metric(micro_precision),
        micro_recall=_clean_metric(micro_recall),
        micro_f1=_clean_metric(_f1(micro_precision, micro_recall)),
        per_label=per_label,
    )


def _cluster_metrics(
    judgments: Mapping[str, QueryJudgments], runs: Mapping[str, QueryRun]
) -> ClusterMetrics | None:
    evaluated_documents = 0
    predicted_pairs = 0
    gold_pairs = 0
    true_positive_pairs = 0

    for query_id in sorted(judgments):
        labeled = [
            item
            for item in judgments[query_id].judgments
            if item.cluster_id is not None
        ]
        if not labeled:
            continue
        result_by_id = {item.document_id: item for item in runs[query_id].results}
        gold_counts: Counter[object] = Counter()
        predicted_counts: Counter[object] = Counter()
        contingency: Counter[object] = Counter()
        for judgment in labeled:
            gold_key: object = ("gold", judgment.cluster_id)
            result = result_by_id.get(judgment.document_id)
            predicted_key: object
            if result is None:
                predicted_key = ("missing", judgment.document_id)
            else:
                predicted_key = ("predicted", result.cluster_id)
            gold_counts[gold_key] += 1
            predicted_counts[predicted_key] += 1
            contingency[(gold_key, predicted_key)] += 1

        evaluated_documents += len(labeled)
        gold_pairs += _pair_count(gold_counts)
        predicted_pairs += _pair_count(predicted_counts)
        true_positive_pairs += _pair_count(contingency)

    if evaluated_documents == 0:
        return None
    precision = _safe_ratio(true_positive_pairs, predicted_pairs)
    recall = _safe_ratio(true_positive_pairs, gold_pairs)
    return ClusterMetrics(
        evaluated_documents=evaluated_documents,
        predicted_pairs=predicted_pairs,
        gold_pairs=gold_pairs,
        true_positive_pairs=true_positive_pairs,
        pairwise_precision=_clean_metric(precision),
        pairwise_recall=_clean_metric(recall),
        pairwise_f1=_clean_metric(_f1(precision, recall)),
    )


def _evaluate_one(
    judgments: Mapping[str, QueryJudgments], runs: Mapping[str, QueryRun]
) -> RunMetrics:
    ndcg_values: list[float] = []
    recall_values: list[float] = []
    precision_values: list[float] = []
    reciprocal_ranks: list[float] = []
    hit_1_values: list[float] = []
    hit_3_values: list[float] = []
    hit_12_values: list[float] = []
    recall_12_values: list[float] = []
    redundancy_values: list[float] = []
    result_count = 0
    traceable_count = 0
    zero_relevant_query_count = 0
    false_positive_query_count = 0
    zero_relevant_result_count = 0
    explicit_hard_negative_count = 0
    explicit_hard_negative_hits_at_12 = 0

    for query_id in sorted(judgments):
        query_judgments = judgments[query_id]
        results = runs[query_id].results
        judgment_by_id = {item.document_id: item for item in query_judgments.judgments}
        relevant_ids = {
            item.document_id for item in query_judgments.judgments if item.relevance > 0
        }
        explicit_hard_negative_ids = {
            item.document_id
            for item in query_judgments.judgments
            if item.relevance == 0
        }
        grades = [
            judgment_by_id.get(result.document_id).relevance
            if result.document_id in judgment_by_id
            else 0
            for result in results
        ]

        if relevant_ids:
            actual_dcg = _dcg(grades[:10])
            ideal_grades = sorted(
                (item.relevance for item in query_judgments.judgments), reverse=True
            )[:10]
            ndcg_values.append(_safe_ratio(actual_dcg, _dcg(ideal_grades)))
            returned_ids_100 = {item.document_id for item in results[:100]}
            returned_ids_12 = {item.document_id for item in results[:12]}
            recalled = len(returned_ids_100 & relevant_ids)
            recalled_12 = len(returned_ids_12 & relevant_ids)
            recall_values.append(_safe_ratio(recalled, len(relevant_ids)))
            recall_12_values.append(_safe_ratio(recalled_12, len(relevant_ids)))
            precision_values.append(sum(grade > 0 for grade in grades[:10]) / 10)
            first_relevant_rank = next(
                (rank for rank, grade in enumerate(grades, start=1) if grade > 0), None
            )
            reciprocal_ranks.append(
                0.0 if first_relevant_rank is None else 1 / first_relevant_rank
            )
            hit_1_values.append(float(any(grade > 0 for grade in grades[:1])))
            hit_3_values.append(float(any(grade > 0 for grade in grades[:3])))
            hit_12_values.append(float(any(grade > 0 for grade in grades[:12])))
        else:
            zero_relevant_query_count += 1
            zero_relevant_result_count += len(results)
            if results:
                false_positive_query_count += 1

        explicit_hard_negative_count += len(explicit_hard_negative_ids)
        explicit_hard_negative_hits_at_12 += len(
            {item.document_id for item in results[:12]} & explicit_hard_negative_ids
        )

        top_results = results[:10]
        redundancy_values.append(
            0.0
            if not top_results
            else (len(top_results) - len({item.cluster_id for item in top_results}))
            / 10
        )
        result_count += len(results)
        traceable_count += sum(item.traceable for item in results)

    query_count = len(judgments)
    positive_query_count = query_count - zero_relevant_query_count
    return RunMetrics(
        query_count=query_count,
        positive_query_count=positive_query_count,
        zero_relevant_query_count=zero_relevant_query_count,
        result_count=result_count,
        ndcg_at_10=_clean_metric(_safe_ratio(sum(ndcg_values), positive_query_count)),
        recall_at_100=_clean_metric(
            _safe_ratio(sum(recall_values), positive_query_count)
        ),
        recall_at_12=_clean_metric(
            _safe_ratio(sum(recall_12_values), positive_query_count)
        ),
        precision_at_10=_clean_metric(
            _safe_ratio(sum(precision_values), positive_query_count)
        ),
        mrr=_clean_metric(_safe_ratio(sum(reciprocal_ranks), positive_query_count)),
        hit_at_1=_clean_metric(_safe_ratio(sum(hit_1_values), positive_query_count)),
        hit_at_3=_clean_metric(_safe_ratio(sum(hit_3_values), positive_query_count)),
        hit_at_12=_clean_metric(_safe_ratio(sum(hit_12_values), positive_query_count)),
        false_positive_query_count=false_positive_query_count,
        false_positive_query_rate=_clean_metric(
            _safe_ratio(false_positive_query_count, zero_relevant_query_count)
        ),
        mean_results_on_zero_relevant=_clean_metric(
            _safe_ratio(zero_relevant_result_count, zero_relevant_query_count)
        ),
        explicit_hard_negative_count=explicit_hard_negative_count,
        explicit_hard_negative_hits_at_12=explicit_hard_negative_hits_at_12,
        hard_negative_intrusion_rate_at_12=_clean_metric(
            _safe_ratio(
                explicit_hard_negative_hits_at_12,
                explicit_hard_negative_count,
            )
        ),
        redundancy_at_10=_clean_metric(sum(redundancy_values) / query_count),
        traceability_rate=_clean_metric(_safe_ratio(traceable_count, result_count)),
        relation=_relation_metrics(judgments, runs),
        clustering=_cluster_metrics(judgments, runs),
    )


def evaluate_runs(
    judgments: Sequence[QueryJudgments],
    named_runs: Mapping[str, Sequence[QueryRun]],
) -> EvaluationReport:
    """Evaluate named, already-materialized runs against one fixed query set."""

    if not judgments:
        raise EvaluationInputError("judgments must contain at least one query")
    if len(judgments) > MAX_QUERIES:
        raise EvaluationInputError(f"judgments exceed {MAX_QUERIES} queries")
    if not named_runs:
        raise EvaluationInputError("at least one named run is required")
    if len(named_runs) > MAX_RUNS:
        raise EvaluationInputError(f"runs exceed the {MAX_RUNS}-run limit")

    typed_judgments = _index_queries(judgments, kind="judgment")
    expected_query_ids = set(typed_judgments)
    metrics: dict[str, RunMetrics] = {}

    for name in sorted(named_runs):
        _validate_run_name(name)
        run_records = named_runs[name]
        if len(run_records) > MAX_QUERIES:
            raise EvaluationInputError(f"run {name!r} exceeds {MAX_QUERIES} queries")
        typed_run = _index_queries(run_records, kind=f"run {name!r}")
        actual_query_ids = set(typed_run)
        unknown = sorted(actual_query_ids - expected_query_ids)
        if unknown:
            raise EvaluationInputError(
                f"run {name!r} contains unknown query_id values: {unknown[:5]}"
            )
        missing = sorted(expected_query_ids - actual_query_ids)
        if missing:
            raise EvaluationInputError(
                f"run {name!r} is missing query_id values: {missing[:5]}"
            )
        metrics[name] = _evaluate_one(typed_judgments, typed_run)

    ranking = sorted(metrics, key=lambda name: (-metrics[name].ndcg_at_10, name))
    return EvaluationReport(
        schema_version=EVALUATION_SCHEMA_VERSION,
        primary_metric="ndcg_at_10",
        query_count=len(typed_judgments),
        runs=metrics,
        ranking_by_ndcg_at_10=ranking,
    )


def _duplicate_key_rejector(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise EvaluationInputError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise EvaluationInputError(f"non-finite JSON number is forbidden: {value}")


def _decode_json(raw: bytes, *, context: str) -> object:
    try:
        text = raw.decode("utf-8", errors="strict")
        return json.loads(
            text,
            object_pairs_hook=_duplicate_key_rejector,
            parse_constant=_reject_json_constant,
        )
    except EvaluationInputError:
        raise
    except UnicodeDecodeError as exc:
        raise EvaluationInputError(f"{context} must be UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise EvaluationInputError(
            f"{context} is invalid JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc
    except (RecursionError, ValueError) as exc:
        raise EvaluationInputError(
            f"{context} is invalid or too deeply nested"
        ) from exc


def _load_raw_records(path: Path) -> list[object]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise EvaluationInputError(f"cannot stat evaluation input {path}") from exc
    if size <= 0:
        raise EvaluationInputError(f"evaluation input is empty: {path}")
    if size > MAX_INPUT_BYTES:
        raise EvaluationInputError(
            f"evaluation input exceeds {MAX_INPUT_BYTES} bytes: {path}"
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise EvaluationInputError(f"cannot read evaluation input {path}") from exc
    if len(raw) > MAX_INPUT_BYTES:
        raise EvaluationInputError(
            f"evaluation input exceeds {MAX_INPUT_BYTES} bytes: {path}"
        )

    suffix = path.suffix.lower()
    if suffix == ".json":
        payload = _decode_json(raw, context=str(path))
        if not isinstance(payload, list):
            raise EvaluationInputError(f"JSON input must be a top-level array: {path}")
        return payload
    if suffix != ".jsonl":
        raise EvaluationInputError("evaluation inputs must use .json or .jsonl")

    lines = raw.splitlines()
    if not lines:
        raise EvaluationInputError(f"JSONL input is empty: {path}")
    records: list[object] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            raise EvaluationInputError(
                f"JSONL input contains a blank line at {line_number}: {path}"
            )
        if len(line) > MAX_JSONL_LINE_BYTES:
            raise EvaluationInputError(
                f"JSONL line {line_number} exceeds {MAX_JSONL_LINE_BYTES} bytes"
            )
        records.append(_decode_json(line, context=f"{path}:{line_number}"))
    return records


def _load_models(path: Path, model: type[EvalQuery]) -> tuple[EvalQuery, ...]:
    records = _load_raw_records(path)
    if not records:
        raise EvaluationInputError(f"evaluation input has no records: {path}")
    if len(records) > MAX_QUERIES:
        raise EvaluationInputError(f"evaluation input exceeds {MAX_QUERIES} queries")
    parsed: list[EvalQuery] = []
    for index, record in enumerate(records):
        try:
            parsed.append(model.model_validate(record, strict=True))
        except ValidationError as exc:
            raise EvaluationInputError(
                f"invalid record {index + 1} in {path}: {exc}"
            ) from exc
    _index_queries(parsed, kind="input")
    return tuple(parsed)


def load_judgments(path: str | Path) -> tuple[QueryJudgments, ...]:
    return _load_models(Path(path), QueryJudgments)


def load_run(path: str | Path) -> tuple[QueryRun, ...]:
    return _load_models(Path(path), QueryRun)


def _parse_run_argument(value: str) -> tuple[str, Path]:
    name, separator, raw_path = value.partition("=")
    if not separator or not raw_path:
        raise EvaluationInputError("--run must use NAME=PATH")
    return _validate_run_name(name), Path(raw_path)


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate bounded offline claim-retrieval runs."
    )
    parser.add_argument("--judgments", required=True, type=Path)
    parser.add_argument(
        "--run",
        action="append",
        required=True,
        metavar="NAME=PATH",
        help="named run; repeat for bm25-only, dense-only, hybrid, reranked, etc.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _argument_parser()
    args = parser.parse_args(argv)
    try:
        if len(args.run) > MAX_RUNS:
            raise EvaluationInputError(f"runs exceed the {MAX_RUNS}-run limit")
        named_paths: dict[str, Path] = {}
        for value in args.run:
            name, path = _parse_run_argument(value)
            if name in named_paths:
                raise EvaluationInputError(f"duplicate run name: {name}")
            named_paths[name] = path
        report = evaluate_runs(
            load_judgments(args.judgments),
            {name: load_run(path) for name, path in named_paths.items()},
        )
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
