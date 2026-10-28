from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_citation_reception import citation_pair_payload

from compiler_service.citation_reception import (
    CitationAssertionDraftBatch,
    CitationReceptionAnalyzer,
)
from compiler_service.citation_reception_eval import (
    EvaluationInputError,
    ReceptionRunRecord,
    evaluate,
    load_ground_truth,
)
from compiler_service.model_client import Completion


@pytest.fixture
def ground_truth(tmp_path: Path):
    # Synthetic contract cases; no publication text or historical evaluation data.
    provenance = {
        "dataset": "synthetic-unit-test",
        "record_id": "fixture",
        "citing_paper_id": "synthetic-citing",
        "cited_paper_id": "synthetic-cited",
        "source_url": "https://example.invalid/synthetic",
        "license": "test fixture",
        "corpus_revision": "unit-v1",
    }
    common = {
        "schema_version": "citation-reception-ground-truth/1.0",
        "provenance": provenance,
        "annotation_notes": "Artificial test case.",
    }
    records = [
        {
            **common,
            "track": "source",
            "case_id": "source.fixture",
            "context": "Our result was consistent with Smith [1].",
            "references": [{"label": "1", "raw_text": "Smith B. Result two. 2021."}],
            "expected_mentions": [
                {
                    "reference_label": "1",
                    "marker": "[1]",
                    "group_size": 1,
                    "required_functions": ["result_comparison"],
                }
            ],
        }
    ]
    for index, positive in enumerate([True, True, False]):
        records.append(
            {
                **common,
                "track": "reception",
                "case_id": f"reception.fixture.{index}",
                "pair": citation_pair_payload(),
                "gold": {
                    "should_emit": positive,
                    "acceptable_functions": ["result_comparison"] if positive else [],
                    "acceptable_stances": ["supports"] if positive else [],
                    "acceptable_confirmation_bases": ["consistent_with_citing_evidence"]
                    if positive
                    else [],
                    "acceptable_target_entity_ids": ["b.claim.1"] if positive else [],
                    "allowed_citing_evidence_entity_ids": ["a.observation.1"]
                    if positive
                    else [],
                    "acceptable_faithfulness": ["faithful"] if positive else [],
                },
            }
        )
    path = tmp_path / "synthetic.jsonl"
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    return load_ground_truth(path)


class EmptyCitationModel:
    def with_job_control(self, control: Any) -> EmptyCitationModel:
        del control
        return self

    def complete(
        self,
        *,
        schema: type[Any],
        validate: Any,
        **kwargs: Any,
    ) -> Completion[Any]:
        del kwargs
        assert schema is CitationAssertionDraftBatch
        value = CitationAssertionDraftBatch(assertions=[])
        validate(value)
        return Completion(
            value=value,
            attempts=1,
            content_sha256="a" * 64,
            diagnostics=(),
        )


def test_synthetic_source_track_passes(ground_truth) -> None:

    report = evaluate(ground_truth, {})

    assert report.ground_truth_cases == 4
    assert report.source.case_count == 1
    assert report.source.precision == 1
    assert report.source.recall == 1
    assert report.source.group_size_accuracy == 1
    assert report.release_gate_passed is False
    assert report.release_gate_reasons == ["no reception run supplied"]


def test_reception_eval_counts_abstention_without_rewarding_blank_output(
    ground_truth,
) -> None:
    analyzer = CitationReceptionAnalyzer(EmptyCitationModel())
    records = [
        ReceptionRunRecord(
            case_id=case.case_id,
            status="ok",
            envelope=analyzer.analyze(case.pair),
            error=None,
        )
        for case in ground_truth
        if case.track == "reception"
    ]

    report = evaluate(ground_truth, {"empty": records})
    metrics = report.reception_runs["empty"]

    assert metrics.positive_cases == 2
    assert metrics.false_negatives == 2
    assert metrics.false_positives == 0
    assert metrics.f1 == 0
    assert metrics.unsafe_strong_confirmations == 0
    assert report.release_gate_passed is False


def test_release_gate_rejects_an_incomplete_run(ground_truth) -> None:
    analyzer = CitationReceptionAnalyzer(EmptyCitationModel())
    first_case = next(case for case in ground_truth if case.track == "reception")
    record = ReceptionRunRecord(
        case_id=first_case.case_id,
        status="ok",
        envelope=analyzer.analyze(first_case.pair),
        error=None,
    )

    report = evaluate(ground_truth, {"partial": [record]})

    assert report.release_gate_passed is False
    assert "partial completed 1/3 cases" in report.release_gate_reasons


def test_ground_truth_loader_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.jsonl"
    invalid.write_text('{"track":"source","track":"reception"}\n', encoding="utf-8")

    with pytest.raises(EvaluationInputError, match="duplicate JSON object key"):
        load_ground_truth(invalid)
