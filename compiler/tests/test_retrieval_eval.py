from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

import compiler_service.retrieval_eval as retrieval_eval
from compiler_service.retrieval_eval import (
    DocumentJudgment,
    EvaluationInputError,
    QueryJudgments,
    QueryRun,
    RunResult,
    evaluate_runs,
    load_judgments,
    load_run,
    main,
)


def _judgments(*, labels: bool = True) -> QueryJudgments:
    records: list[DocumentJudgment] = []
    relations = {
        1: "equivalent",
        2: "supports",
        3: "related",
    }
    for index in range(1, 11):
        records.append(
            DocumentJudgment(
                document_id=f"doc-{index}",
                relevance={1: 3, 2: 2, 3: 1}.get(index, 0),
                relation=relations.get(index) if labels else None,
                cluster_id=("gold-2" if index == 3 else f"gold-{index}")
                if labels
                else None,
            )
        )
    return QueryJudgments(
        query_id="query-1",
        query_text="Does the reported effect hold?",
        judgments=records,
    )


def _perfect_run() -> QueryRun:
    relations = {
        1: "equivalent",
        2: "supports",
        3: "related",
    }
    return QueryRun(
        query_id="query-1",
        results=[
            RunResult(
                document_id=f"doc-{index}",
                score=11 - index,
                relation=relations.get(index),
                cluster_id="gold-2" if index == 3 else f"gold-{index}",
                traceable=index != 10,
            )
            for index in range(1, 11)
        ],
    )


def test_primary_retrieval_traceability_relation_and_cluster_metrics() -> None:
    report = evaluate_runs([_judgments()], {"reranked": [_perfect_run()]})
    metrics = report.runs["reranked"]

    assert report.primary_metric == "ndcg_at_10"
    assert report.ranking_by_ndcg_at_10 == ["reranked"]
    assert metrics.ndcg_at_10 == 1
    assert metrics.recall_at_100 == 1
    assert metrics.precision_at_10 == 0.3
    assert metrics.mrr == 1
    assert metrics.redundancy_at_10 == 0.1
    assert metrics.traceability_rate == 0.9
    assert metrics.relation is not None
    assert metrics.relation.macro_f1 == 1
    assert metrics.relation.micro_f1 == 1
    assert metrics.clustering is not None
    assert metrics.clustering.predicted_pairs == 1
    assert metrics.clustering.gold_pairs == 1
    assert metrics.clustering.pairwise_precision == 1
    assert metrics.clustering.pairwise_recall == 1
    assert metrics.clustering.pairwise_f1 == 1


def test_relation_and_cluster_metrics_penalize_errors_and_missing_hits() -> None:
    judgments = QueryJudgments(
        query_id="query-1",
        query_text="A scoped relation query",
        judgments=[
            DocumentJudgment(
                document_id="d1",
                relevance=1,
                relation="supports",
                cluster_id="gold-a",
            ),
            DocumentJudgment(
                document_id="d2",
                relevance=1,
                relation="supports",
                cluster_id="gold-a",
            ),
            DocumentJudgment(
                document_id="d3",
                relevance=1,
                relation="contradicts",
                cluster_id="gold-b",
            ),
            DocumentJudgment(
                document_id="d4",
                relevance=1,
                relation="contradicts",
                cluster_id="gold-b",
            ),
        ],
    )
    run = QueryRun(
        query_id="query-1",
        results=[
            RunResult(
                document_id="d1",
                relation="supports",
                cluster_id="predicted-x",
                traceable=True,
            ),
            RunResult(
                document_id="d2",
                relation="contradicts",
                cluster_id="predicted-x",
                traceable=True,
            ),
            RunResult(
                document_id="d3",
                relation="supports",
                cluster_id="predicted-x",
                traceable=True,
            ),
        ],
    )

    metrics = evaluate_runs([judgments], {"hybrid": [run]}).runs["hybrid"]

    assert metrics.relation is not None
    assert metrics.relation.evaluated_pairs == 4
    assert metrics.relation.macro_f1 == 0.25
    assert metrics.relation.micro_precision == pytest.approx(1 / 3)
    assert metrics.relation.micro_recall == 0.25
    assert metrics.relation.micro_f1 == pytest.approx(2 / 7)
    assert metrics.clustering is not None
    assert metrics.clustering.evaluated_documents == 4
    assert metrics.clustering.predicted_pairs == 3
    assert metrics.clustering.gold_pairs == 2
    assert metrics.clustering.true_positive_pairs == 1
    assert metrics.clustering.pairwise_precision == pytest.approx(1 / 3)
    assert metrics.clustering.pairwise_recall == 0.5
    assert metrics.clustering.pairwise_f1 == 0.4


def test_optional_gold_labels_emit_null_metrics() -> None:
    report = evaluate_runs([_judgments(labels=False)], {"bm25-only": [_perfect_run()]})

    metrics = report.runs["bm25-only"]
    assert metrics.relation is None
    assert metrics.clustering is None
    assert metrics.redundancy_at_10 == 0.1


def test_zero_relevant_queries_measure_abstention_without_degrading_rank_metrics() -> (
    None
):
    positive = _judgments(labels=False)
    zero = QueryJudgments(
        query_id="query-zero",
        query_text="No evidence exists in the closed corpus",
        judgments=[],
    )
    perfect = _perfect_run()
    empty = QueryRun(query_id="query-zero", results=[])

    metrics = evaluate_runs([positive, zero], {"hybrid": [perfect, empty]}).runs[
        "hybrid"
    ]

    assert metrics.query_count == 2
    assert metrics.positive_query_count == 1
    assert metrics.zero_relevant_query_count == 1
    assert metrics.ndcg_at_10 == 1
    assert metrics.recall_at_12 == 1
    assert metrics.hit_at_12 == 1
    assert metrics.false_positive_query_count == 0
    assert metrics.false_positive_query_rate == 0
    assert metrics.mean_results_on_zero_relevant == 0


def test_json_and_jsonl_cli_compare_named_ablation_runs(tmp_path, capsys) -> None:
    judgments_path = tmp_path / "judgments.json"
    run_path = tmp_path / "run.jsonl"
    judgments_path.write_text(
        json.dumps([_judgments().model_dump(mode="json")]), encoding="utf-8"
    )
    run_path.write_text(
        json.dumps(_perfect_run().model_dump(mode="json")) + "\n", encoding="utf-8"
    )

    assert (
        main(
            [
                "--judgments",
                str(judgments_path),
                "--run",
                f"bm25-only={run_path}",
                "--run",
                f"dense-only={run_path}",
                "--run",
                f"hybrid={run_path}",
                "--run",
                f"reranked={run_path}",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)

    assert list(output["runs"]) == [
        "bm25-only",
        "dense-only",
        "hybrid",
        "reranked",
    ]
    assert output["ranking_by_ndcg_at_10"] == [
        "bm25-only",
        "dense-only",
        "hybrid",
        "reranked",
    ]
    assert output["runs"]["hybrid"]["ndcg_at_10"] == 1

    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "--judgments",
                str(judgments_path),
                "--run",
                f"hybrid={run_path}",
                "--run",
                f"hybrid={run_path}",
            ]
        )
    assert exc_info.value.code == 2
    assert "duplicate run name: hybrid" in capsys.readouterr().err


def test_strict_files_reject_duplicate_keys_records_documents_and_unknown_fields(
    tmp_path,
) -> None:
    duplicate_key = tmp_path / "duplicate-key.jsonl"
    duplicate_key.write_text(
        '{"query_id":"q1","query_id":"q2","query_text":"x",'
        '"judgments":[{"document_id":"d1","relevance":1}]}\n',
        encoding="utf-8",
    )
    with pytest.raises(EvaluationInputError, match="duplicate JSON object key"):
        load_judgments(duplicate_key)

    duplicate_query = tmp_path / "duplicate-query.json"
    record = {
        "query_id": "q1",
        "query_text": "x",
        "judgments": [{"document_id": "d1", "relevance": 1}],
    }
    duplicate_query.write_text(json.dumps([record, record]), encoding="utf-8")
    with pytest.raises(EvaluationInputError, match="duplicate input query_id"):
        load_judgments(duplicate_query)

    duplicate_document = tmp_path / "duplicate-document.json"
    duplicate_document.write_text(
        json.dumps(
            [
                {
                    "query_id": "q1",
                    "results": [
                        {
                            "document_id": "d1",
                            "cluster_id": "c1",
                            "traceable": True,
                        },
                        {
                            "document_id": "d1",
                            "cluster_id": "c1",
                            "traceable": True,
                        },
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(EvaluationInputError, match="document_id values must be unique"):
        load_run(duplicate_document)

    unknown_field = tmp_path / "unknown-field.json"
    invalid = record | {"unexpected": True}
    unknown_field.write_text(json.dumps([invalid]), encoding="utf-8")
    with pytest.raises(EvaluationInputError, match="Extra inputs are not permitted"):
        load_judgments(unknown_field)


def test_bounds_nonfinite_numbers_and_exact_query_coverage_fail_closed(
    tmp_path, monkeypatch
) -> None:
    oversized_run = tmp_path / "oversized.json"
    oversized_run.write_text(
        json.dumps(
            [
                {
                    "query_id": "query-1",
                    "results": [
                        {
                            "document_id": f"d{index}",
                            "score": index,
                            "cluster_id": f"c{index}",
                            "traceable": True,
                        }
                        for index in range(retrieval_eval.MAX_RESULTS_PER_QUERY + 1)
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(EvaluationInputError, match="List should have at most"):
        load_run(oversized_run)

    nonfinite = tmp_path / "nonfinite.jsonl"
    nonfinite.write_text(
        '{"query_id":"query-1","results":[{"document_id":"d1",'
        '"score":NaN,"cluster_id":"c1","traceable":true}]}\n',
        encoding="utf-8",
    )
    with pytest.raises(EvaluationInputError, match="non-finite JSON number"):
        load_run(nonfinite)

    judgments = [_judgments()]
    unknown = QueryRun(query_id="unknown", results=[])
    with pytest.raises(EvaluationInputError, match="unknown query_id"):
        evaluate_runs(judgments, {"dense-only": [unknown]})
    with pytest.raises(EvaluationInputError, match="missing query_id"):
        evaluate_runs(judgments, {"dense-only": []})

    bounded = tmp_path / "bounded.json"
    bounded.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(retrieval_eval, "MAX_INPUT_BYTES", 1)
    with pytest.raises(EvaluationInputError, match="exceeds 1 bytes"):
        load_judgments(bounded)


def test_model_contract_rejects_ungraded_or_non_boolean_inputs() -> None:
    with pytest.raises(ValidationError):
        DocumentJudgment(document_id="d1", relevance=4)
    with pytest.raises(ValidationError):
        RunResult(document_id="d1", cluster_id="c1", traceable=1)
