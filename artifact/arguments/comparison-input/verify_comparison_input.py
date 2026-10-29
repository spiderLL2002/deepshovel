"""TS projection -> strict Python API model -> both actual analyzer prompts.

The recording model is a deterministic transport stub, not a scientific judge.
Run from any directory with the public repro Python dependencies and Node >=22.13.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

from pydantic import ValidationError

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "compiler"))

from compiler_service.claim_comparison import (
    ClaimComparisonAnalyzer,
    ClaimComparisonAssessmentBatch,
    ClaimComparisonDecisionDraftBatch,
    ClaimComparisonRequest,
    _extraction_schema_for_request,
    _freeze_comparisons,
    _validate_decision_drafts,
)
from compiler_service.model_client import Completion


class RecordingModel:
    """No network or model API: capture the real analyzer's serialized inputs."""

    def __init__(self):
        self.payloads = []

    def with_job_control(self, _control):
        return self

    def complete(self, *, user_content, schema, validate, **_kwargs):
        payload = json.loads(user_content)
        self.payloads.append(payload)
        pair = payload["request"]["pairs"][0]
        candidate = pair["candidate"]
        raw = [
            item["id"]
            for item in candidate["evidence_entities"]
            if item["kind"]
            in {"source_anchor", "observation", "structured_visual_evidence"}
        ]
        core = {
            "pair_id": pair["id"],
            "relation": "insufficient_to_determine",
            "direction": "none",
            "stance": "unclear",
            "scope_alignment": "unknown",
            "directness": "insufficient",
            "query_evidence_ids": [],
            "candidate_evidence_ids": raw[:1],
            "confidence": 0.0,
            "rationale": "Synthetic transport stub; no scientific judgment made.",
            "limitations": [
                "Transport-only control; truncated inputs remain insufficient."
            ],
        }
        if schema is ClaimComparisonDecisionDraftBatch:
            value = schema.model_validate({"comparisons": [core]}, strict=True)
        else:
            assert schema is ClaimComparisonAssessmentBatch
            frozen = payload["frozen_comparisons"][0]
            core.update(comparison_id=frozen["id"], verdict="maintained")
            value = schema.model_validate(
                {
                    "extraction_sha256": payload["extraction_sha256"],
                    "assessments": [core],
                },
                strict=True,
            )
        validate(value)
        return Completion(
            value=value,
            attempts=1,
            content_sha256=hashlib.sha256(user_content.encode()).hexdigest(),
            diagnostics=(),
        )


def run(out: Path):
    cases = json.loads(
        subprocess.check_output(
            [
                "node",
                "--experimental-strip-types",
                "--no-warnings",
                str(HERE / "project-inputs.mjs"),
            ],
            text=True,
        )
    )
    results = []
    negative_controls = []
    out.mkdir(parents=True, exist_ok=True)
    for case in cases:
        request = ClaimComparisonRequest.model_validate(case["request"], strict=True)
        expected = request.pairs[0].candidate.model_dump(mode="json")
        model = RecordingModel()
        result = ClaimComparisonAnalyzer(model).analyze(request)
        extraction = model.payloads[0]["request"]["pairs"][0]["candidate"]
        verification = model.payloads[1]["request"]["pairs"][0]["candidate"]
        assert extraction == expected
        assert {
            k: v for k, v in verification.items() if k != "selected_evidence_ids"
        } == expected
        assert (
            verification["selected_evidence_ids"]
            == result.extraction.comparisons[0].candidate_evidence_ids
        )
        assumptions = [
            item
            for item in expected["evidence_entities"]
            if item["kind"] == "assumption"
        ]
        assert not {item["id"] for item in assumptions} & set(
            verification["selected_evidence_ids"]
        )
        endpoints = {item["id"] for item in expected["evidence_entities"]} | {
            expected["claim_id"]
        }
        assert all(
            edge["source_id"] in endpoints and edge["target_id"] in endpoints
            for edge in expected["evidence_edges"]
        )

        # A strong result remains structurally valid as a decision, but the
        # pair-bound validator must reject it when input is truncated.
        draft = result.extraction.comparisons[0].model_dump(mode="json")
        del draft["id"]
        draft.update(
            relation="supports",
            direction="candidate_to_query",
            stance="supportive",
            scope_alignment="equivalent",
            directness="direct",
        )
        strong = ClaimComparisonDecisionDraftBatch.model_validate(
            {"comparisons": [draft]}, strict=True
        )
        rejected = False
        try:
            _validate_decision_drafts(request, strong)
        except ValueError as error:
            assert "truncated recorded input" in str(error)
            rejected = True
        truncated = expected["evidence_completeness"]["status"] == "truncated"
        assert rejected == truncated
        if truncated:
            constraints = _extraction_schema_for_request(request)["properties"][
                "comparisons"
            ]["prefixItems"][0]["allOf"]
            assert any(
                c.get("properties", {}).get("relation")
                == {"const": "insufficient_to_determine"}
                for c in constraints
            )
        else:
            assert len(assumptions) == 3
            assert len(expected["evidence_edges"]) == 37
            # The frozen comparison ID binds the full argument, not just Claim IDs.
            altered = deepcopy(case["request"])
            altered["pairs"][0]["candidate"]["evidence_edges"].pop()
            changed = ClaimComparisonRequest.model_validate(altered, strict=True)
            a = _freeze_comparisons(request, strong.comparisons)[0].id
            b = _freeze_comparisons(changed, strong.comparisons)[0].id
            assert a != b
            for name in (
                "missing-assumption-endpoint",
                "assumption-recast-as-reasoning",
            ):
                invalid = deepcopy(case["request"])
                evidence = invalid["pairs"][0]["candidate"]["evidence_entities"]
                assumption = next(
                    item for item in evidence if item["kind"] == "assumption"
                )
                if name == "missing-assumption-endpoint":
                    evidence.remove(assumption)
                else:
                    assumption["kind"] = "reasoning"
                try:
                    ClaimComparisonRequest.model_validate(invalid, strict=True)
                except ValidationError as error:
                    expected_error = (
                        "dependency edge endpoints"
                        if name == "missing-assumption-endpoint"
                        else "assumption_for"
                    )
                    assert expected_error in str(error)
                    negative_controls.append(name)
                else:
                    raise AssertionError(f"Invalid dependency input accepted: {name}")
            only_assumption = deepcopy(strong.model_dump(mode="json"))
            only_assumption["comparisons"][0]["candidate_evidence_ids"] = [
                assumptions[0]["id"]
            ]
            try:
                _validate_decision_drafts(
                    request,
                    ClaimComparisonDecisionDraftBatch.model_validate(
                        only_assumption, strict=True
                    ),
                )
            except ValueError as error:
                assert "raw evidence" in str(error)
                negative_controls.append("assumption-alone-is-not-raw-evidence")
            else:
                raise AssertionError("An assumption alone established strong support")
            (out / "f2-request.json").write_text(
                json.dumps(case["request"], indent=2) + "\n"
            )
            (out / "f2-model-inputs.json").write_text(
                json.dumps(model.payloads, indent=2) + "\n"
            )
        results.append(
            {
                "case": case["name"],
                "provenance": case["provenance"],
                "entities": len(expected["evidence_entities"]),
                "assumptions": len(assumptions),
                "edges": len(expected["evidence_edges"]),
                "completeness": expected["evidence_completeness"],
                "extraction_preserves_projection": True,
                "verification_preserves_unselected_dependency_context": True,
                "strong_result_rejected": rejected,
            }
        )
    invalid = deepcopy(cases[2]["request"])
    invalid["pairs"][0]["candidate"]["evidence_completeness"]["status"] = "complete"
    try:
        ClaimComparisonRequest.model_validate(invalid, strict=True)
    except ValidationError as error:
        assert "completeness contradicts omissions" in str(error)
        negative_controls.append("budget-omissions-mislabeled-complete")
    else:
        raise AssertionError("Omitted input was accepted as complete")
    paths = [
        "lib/claim-comparison/projection.ts",
        "lib/claim-comparison/types.ts",
        "lib/claim-comparison/validation.ts",
        "app/api/evidence-synthesis/route.ts",
        "compiler/compiler_service/claim_comparison.py",
    ]
    report = {
        "kind": "Public-release fix verification; not an original production run",
        "model": "Deterministic recording stub; no model or network calls",
        "real_papers": 1,
        "real_claims": 1,
        "cases": results,
        "negative_controls_rejected": negative_controls,
        "source_sha256": {
            p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths
        },
        "argument_change_alters_frozen_comparison_id": True,
        "limits": "Preserves recorded input only; does not discover unextracted premises or validate scientific truth.",
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=HERE / "runs/local")
    run(parser.parse_args().out)
