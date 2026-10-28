"""Regression contract for the active one-pass extraction pipeline."""

from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from conftest import raw_graph_payload

from compiler_service.canonical import (
    EXTRACTION_PROMPT_VERSION,
    VERIFICATION_PROMPT_VERSION,
    to_canonical_graph,
)
from compiler_service.compiler import (
    EvidenceCompiler,
    _extraction_contract_violations,
)
from compiler_service.config import GPT_MODEL
from compiler_service.errors import ContractIssue
from compiler_service.graph_contract import PaperEvidenceGraph
from compiler_service.model_client import ModelJsonClient
from compiler_service.models import (
    BoundingBox,
    DocumentBlock,
    DocumentIR,
    DocumentPage,
    ParserMetadata,
)
from compiler_service.normalize import normalize_mineru
from compiler_service.prompts import (
    EXTRACTION_SYSTEM_PROMPT,
    VERIFICATION_SYSTEM_PROMPT,
)


def _response(payload: object) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))
            )
        ]
    )


def _sdk(create: Mock) -> SimpleNamespace:
    return SimpleNamespace(create=create)


def _empty_extraction(paper_id: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "paper_id": paper_id,
        "claims": [],
        "observations": [],
        "reasoning": [],
        "assumptions": [],
        "anchors": [],
        "visual_evidence": [],
        "scopes": [],
        "edges": [],
    }


def test_pipeline_removes_claim_quota_language_and_top_level_schema_caps() -> None:
    assert EXTRACTION_PROMPT_VERSION == "compiler-extractor/1.0.0"
    assert VERIFICATION_PROMPT_VERSION == "compiler-verifier/1.0.0"

    prompt = " ".join(EXTRACTION_SYSTEM_PROMPT.split()).casefold()
    assert "target 1-5" not in prompt
    assert "absolute maximum" not in prompt
    assert "prefer fewer well-supported nodes" not in prompt
    assert "there is no target, minimum, preferred range, or content quota" in prompt
    assert "may return many" in prompt
    assert "a claim is a terminal argument-level scientific conclusion" in prompt
    assert "not a raw result record" in prompt
    assert "standalone equation" in prompt
    assert "fit formula" in prompt
    assert "isolated measurement" in prompt
    assert "these belong in observation or structuredvisualevidence" in prompt
    assert "if removing the exact constants and formula serialization" in prompt
    assert "the item is evidence rather than a claim" in prompt
    assert "one claim per formula" in prompt
    assert '"sample c has fit y = ..." is an observation, not a claim' in prompt

    properties = PaperEvidenceGraph.model_json_schema(mode="validation")["properties"]
    for collection in (
        "claims",
        "observations",
        "reasoning",
        "assumptions",
        "anchors",
        "visual_evidence",
        "scopes",
        "edges",
    ):
        assert "maxItems" not in properties[collection]

    verifier_prompt = " ".join(VERIFICATION_SYSTEM_PROMPT.split()).casefold()
    assert "for every claim, explicitly compare" in verifier_prompt
    assert "scientific values, comparison directions, units, and formula semantics" in (
        verifier_prompt
    )
    assert "advisory per-claim finding" in verifier_prompt
    assert "mismatch details in its limitations" in verifier_prompt
    assert "rationale is advisory verifier commentary" in verifier_prompt
    assert "not an extraction admission boundary" in verifier_prompt
    assert "never reject or omit the whole graph" in verifier_prompt


def test_pipeline_verifier_rejects_causal_proof_without_causal_evidence() -> None:
    prompt = " ".join(VERIFICATION_SYSTEM_PROMPT.split()).casefold()

    assert 'explicitly says that observations "prove", "establish", or' in prompt
    assert '"demonstrate" that one thing "causes" another' in prompt
    assert "no independent controlled experiment, intervention, or causal test" in (
        prompt
    )
    assert "must be insufficient_evidence" in prompt
    assert "use contradicted instead if the source explicitly rules out" in prompt
    assert "never assign supported, qualified, or uncertain to categorical" in prompt
    assert "correct associated facts do not qualify" in prompt
    assert 'explicitly tentative ("may", "could", "suggests")' in prompt
    assert "may receive qualified or uncertain" in prompt
    assert "use qualified or uncertain unless" not in prompt


def test_pipeline_schema_accepts_one_hundred_claims_without_old_cap(
    artifact,
    parser_metadata,
) -> None:
    def alphabetic_label(index: int) -> str:
        value = index
        output = ""
        while True:
            value, remainder = divmod(value, 26)
            output = chr(ord("A") + remainder) + output
            if value == 0:
                return output
            value -= 1

    anchor_identity = {
        "artifact_id": artifact.artifact_id,
        "version_id": artifact.version_id,
        "artifact_sha256": artifact.artifact_sha256,
        "media_type": artifact.media_type,
    }
    payload = _empty_extraction(artifact.paper_id)
    payload["anchors"] = [
        {
            "id": "anchor.shared",
            **anchor_identity,
            "anchor_type": "text",
            "locator": {
                "page": 1,
                "section": None,
                "paragraph": None,
                "figure": None,
                "table": None,
                "row_range": None,
                "column_names": [],
                "series": None,
                "region": None,
                "quote": "The source reports many independent findings.",
            },
        }
    ]
    payload["scopes"] = [
        {
            "id": f"scope.{index}",
            "location": None,
            "time": None,
            "formation": None,
            "sample_type": None,
            "method": None,
            "condition": None,
            "measurement": None,
            "uncertainty": None,
            "entities": [],
        }
        for index in range(100)
    ]
    payload["claims"] = [
        {
            "id": f"claim.local.{index}",
            "statement": (
                "Independent material finding code "
                f"{alphabetic_label(index)} is retained."
            ),
            "claim_type": "descriptive",
            "polarity": "affirmed",
            "scope_id": f"scope.{index}",
            "anchor_ids": ["anchor.shared"],
            "qualifiers": [],
            "proposition_key": None,
            "scoped_claim_id": None,
        }
        for index in range(100)
    ]
    payload["observations"] = [
        {
            "id": f"obs.local.{index}",
            "statement": "The source reports many independent findings.",
            "observation_type": "source_assertion",
            "anchor_ids": ["anchor.shared"],
            "scope_id": f"scope.{index}",
            "raw_value": None,
            "unit": None,
        }
        for index in range(100)
    ]
    payload["reasoning"] = [
        {
            "id": f"reason.local.{index}",
            "statement": (
                f"The source establishes finding code {alphabetic_label(index)}."
            ),
            "reasoning_type": "deductive",
            "anchor_ids": ["anchor.shared"],
        }
        for index in range(100)
    ]
    payload["edges"] = [
        edge
        for index in range(100)
        for edge in (
            {
                "id": f"edge.grounds.{index}",
                "source_id": "anchor.shared",
                "target_id": f"obs.local.{index}",
                "relation": "grounds",
                "rationale": None,
                "confidence": None,
            },
            {
                "id": f"edge.premise.{index}",
                "source_id": f"obs.local.{index}",
                "target_id": f"reason.local.{index}",
                "relation": "premise_for",
                "rationale": None,
                "confidence": None,
            },
            {
                "id": f"edge.support.{index}",
                "source_id": f"reason.local.{index}",
                "target_id": f"claim.local.{index}",
                "relation": "supports",
                "rationale": None,
                "confidence": None,
            },
        )
    ]

    graph = PaperEvidenceGraph.model_validate(payload, strict=True)
    canonical = to_canonical_graph(
        graph,
        artifact,
        parser=parser_metadata,
        created_at="2026-08-17T00:00:00Z",
    )

    assert len(graph.claims) == 100
    assert len(graph.observations) == 100
    assert len(graph.reasoning) == 100
    assert len(graph.edges) == 300
    assert len({claim.scoped_claim_id for claim in graph.claims}) == 100
    assert len(canonical["claims"]) == 100
    assert {claim["id"] for claim in canonical["claims"]} <= set(
        canonical["snapshots"][0]["entity_ids"]
    )


def test_zero_claim_model_output_gets_caller_owned_canonical_provenance(
    artifact,
    mineru_output,
) -> None:
    document = normalize_mineru(mineru_output, artifact).document
    create = Mock(return_value=_response(_empty_extraction(artifact.paper_id)))
    compiler = EvidenceCompiler(ModelJsonClient(_sdk(create), sleep=lambda _: None))

    outcome = compiler.compile(document, created_at="2026-08-17T00:00:00Z")

    assert create.call_count == 1
    assert outcome.extraction_attempts == 1
    assert outcome.envelope.extraction.prompt_version == "compiler-extractor/1.0.0"
    assert outcome.envelope.graph["claims"] == []
    assert outcome.envelope.graph["observations"] == []
    assert outcome.envelope.graph["reasoning"] == []
    assert outcome.envelope.graph["edges"] == []
    assert len(outcome.envelope.graph["anchors"]) == 1
    anchor = outcome.envelope.graph["anchors"][0]
    assert anchor["id"].startswith("anchor.provenance.")
    assert anchor["artifact_version_id"] == artifact.version_id
    assert anchor["quote"] == document.pages[0].blocks[0].text
    assert anchor["id"] in outcome.envelope.graph["snapshots"][0]["entity_ids"]


def test_pipeline_scientific_mismatch_reaches_non_blocking_negative_verifier(
    artifact,
    mineru_output,
) -> None:
    document = normalize_mineru(mineru_output, artifact).document
    extraction_payload = raw_graph_payload(artifact)
    extraction_payload["claims"][0].update(
        {
            "statement": "Treatment follows x = z + 1 at 6 kg.",
            "proposition_key": None,
            "scoped_claim_id": None,
        }
    )
    extraction_payload["observations"][0].update(
        {
            "statement": "The treated sample reached 6 kg with x = z + 1.",
            "raw_value": "6",
            "unit": "kg",
        }
    )
    extraction_payload["reasoning"][0]["statement"] = (
        "The comparison attributes x = z + 1 at 6 kg to treatment."
    )
    verifier_inputs: list[PaperEvidenceGraph] = []

    def respond(**kwargs: Any) -> SimpleNamespace:
        user_payload = json.loads(kwargs["messages"][1]["content"])
        if "extraction_graph" not in user_payload:
            payload = deepcopy(extraction_payload)
            payload["anchors"][0]["locator"]["source_span_id"] = next(
                item["source_span_id"]
                for item in user_payload["trusted_source_spans"]
                if item["exact_text"] == payload["anchors"][0]["locator"]["quote"]
            )
            return _response(payload)
        graph = PaperEvidenceGraph.model_validate(
            user_payload["extraction_graph"], strict=True
        )
        verifier_inputs.append(graph)
        claim = graph.claims[0]
        return _response(
            {
                "schema_version": "1.0",
                "paper_id": graph.paper_id,
                "graph_sha256": user_payload["graph_sha256"],
                "verifier_model": GPT_MODEL,
                "assessments": [
                    {
                        "id": "assessment.negative.1",
                        "claim_id": claim.id,
                        "scoped_claim_id": claim.scoped_claim_id,
                        "verdict": "contradicted",
                        "confidence": 0.8,
                        "evidence_ids": ["anchor.text.1", "obs.local.1"],
                        "rationale": "The cited evidence contradicts the conclusion.",
                        "limitations": [
                            "The extracted value, unit, and formula semantics "
                            "differ from the exact source."
                        ],
                    }
                ],
                "warnings": [],
            }
        )

    create = Mock(side_effect=respond)
    compiler = EvidenceCompiler(ModelJsonClient(_sdk(create), sleep=lambda _: None))

    outcome = compiler.compile(
        document,
        verify=True,
        created_at="2026-08-17T00:00:00Z",
    )

    assert create.call_count == 2
    assert outcome.extraction_attempts == 1
    assert outcome.verification_attempts == 1
    assert len(verifier_inputs) == 1
    verifier_graph = verifier_inputs[0]
    assert verifier_graph.observations[0].raw_value == "6"
    assert verifier_graph.observations[0].unit == "kg"
    assert "x = z + 1" in verifier_graph.claims[0].statement
    assert len(outcome.envelope.graph["claims"]) == 1
    assert outcome.envelope.graph["observations"][0]["value"]["numeric"] == 6
    assert outcome.envelope.graph["observations"][0]["value"]["unit"] == "kg"
    assert outcome.envelope.verification is not None
    assert outcome.envelope.verification.prompt_version == "compiler-verifier/1.0.0"
    assert (
        outcome.envelope.verification.report["assessments"][0]["verdict"]
        == "contradicted"
    )


def _formula_document(artifact, source_formula: str) -> DocumentIR:
    return DocumentIR(
        artifact=artifact,
        parser=ParserMetadata(
            name="mineru",
            version="3.1.15",
            backend="pipeline",
            method="auto",
            transport="remote",
        ),
        page_count=1,
        pages=[
            DocumentPage(
                number=1,
                width=None,
                height=None,
                blocks=[
                    DocumentBlock(
                        id="block.equation.1",
                        kind="equation",
                        page=1,
                        source_index=0,
                        bbox=BoundingBox(
                            x=0.1,
                            y=0.2,
                            width=0.6,
                            height=0.1,
                        ),
                        text=source_formula,
                        level=None,
                        structured={},
                    )
                ],
            )
        ],
        markdown=source_formula,
        warnings=[],
    )


def _formula_graph(
    artifact,
    *,
    source_formula: str,
    asserted_formula: str,
    raw_value: str,
    unit: str,
) -> PaperEvidenceGraph:
    anchor_identity = {
        "artifact_id": artifact.artifact_id,
        "version_id": artifact.version_id,
        "artifact_sha256": artifact.artifact_sha256,
        "media_type": artifact.media_type,
    }
    return PaperEvidenceGraph.model_validate(
        {
            "schema_version": "1.0",
            "paper_id": artifact.paper_id,
            "claims": [
                {
                    "id": "claim.formula.1",
                    "statement": asserted_formula,
                    "claim_type": "descriptive",
                    "polarity": "affirmed",
                    "scope_id": "scope.formula.1",
                    "anchor_ids": ["anchor.formula.1"],
                    "qualifiers": [],
                    "proposition_key": None,
                    "scoped_claim_id": None,
                }
            ],
            "observations": [
                {
                    "id": "obs.formula.1",
                    "statement": asserted_formula,
                    "observation_type": "measurement",
                    "anchor_ids": ["anchor.formula.1"],
                    "scope_id": "scope.formula.1",
                    "raw_value": raw_value,
                    "unit": unit,
                }
            ],
            "reasoning": [
                {
                    "id": "reason.formula.1",
                    "statement": asserted_formula,
                    "reasoning_type": "deductive",
                    "anchor_ids": ["anchor.formula.1"],
                }
            ],
            "assumptions": [],
            "anchors": [
                {
                    "id": "anchor.formula.1",
                    **anchor_identity,
                    "anchor_type": "text",
                    "locator": {
                        "page": 1,
                        "section": None,
                        "paragraph": None,
                        "figure": None,
                        "table": None,
                        "row_range": None,
                        "column_names": [],
                        "series": None,
                        "region": {
                            "x": 0.1,
                            "y": 0.2,
                            "width": 0.6,
                            "height": 0.1,
                            "unit": "normalized",
                        },
                        "quote": source_formula,
                    },
                }
            ],
            "visual_evidence": [],
            "scopes": [
                {
                    "id": "scope.formula.1",
                    "location": None,
                    "time": None,
                    "formation": None,
                    "sample_type": None,
                    "method": None,
                    "condition": None,
                    "measurement": None,
                    "uncertainty": None,
                    "entities": [],
                }
            ],
            "edges": [
                {
                    "id": "edge.grounds.formula.1",
                    "source_id": "anchor.formula.1",
                    "target_id": "obs.formula.1",
                    "relation": "grounds",
                    "rationale": None,
                    "confidence": None,
                },
                {
                    "id": "edge.premise.formula.1",
                    "source_id": "obs.formula.1",
                    "target_id": "reason.formula.1",
                    "relation": "premise_for",
                    "rationale": None,
                    "confidence": None,
                },
                {
                    "id": "edge.support.formula.1",
                    "source_id": "reason.formula.1",
                    "target_id": "claim.formula.1",
                    "relation": "supports",
                    "rationale": None,
                    "confidence": None,
                },
            ],
        },
        strict=True,
    )


def test_pipeline_admission_accepts_formatting_neutral_mineru_latex(artifact) -> None:
    source_formula = r"\(x = 5\,\mathrm{mg}\)"
    document = _formula_document(artifact, source_formula)
    graph = _formula_graph(
        artifact,
        source_formula=source_formula,
        asserted_formula="$x = 5 mg$",
        raw_value="=5",
        unit="mg",
    )

    assert _extraction_contract_violations(graph, document) == ()


@pytest.mark.parametrize(
    (
        "source_formula",
        "good_formula",
        "bad_formula",
        "good_raw",
        "bad_raw",
        "good_unit",
        "bad_unit",
    ),
    [
        (
            r"\(x = 5\,\mathrm{mg}\)",
            "$x = 5 mg$",
            "$x = 6 mg$",
            "=5",
            "=6",
            "mg",
            "mg",
        ),
        (
            r"\(x = 5\,\mathrm{mg}\)",
            "$x = 5 mg$",
            "$x = 5 kg$",
            "=5",
            "=5",
            "mg",
            "kg",
        ),
        (
            r"\(x > 5\,\mathrm{mg}\)",
            "$x > 5 mg$",
            "$x < 5 mg$",
            ">5",
            "<5",
            "mg",
            "mg",
        ),
    ],
    ids=["value", "unit", "comparator"],
)
def test_pipeline_scientific_formula_mutations_are_advisory_at_admission(
    artifact,
    source_formula: str,
    good_formula: str,
    bad_formula: str,
    good_raw: str,
    bad_raw: str,
    good_unit: str,
    bad_unit: str,
) -> None:
    document = _formula_document(artifact, source_formula)
    accepted = _formula_graph(
        artifact,
        source_formula=source_formula,
        asserted_formula=good_formula,
        raw_value=good_raw,
        unit=good_unit,
    )
    rejected = _formula_graph(
        artifact,
        source_formula=source_formula,
        asserted_formula=bad_formula,
        raw_value=bad_raw,
        unit=bad_unit,
    )

    assert _extraction_contract_violations(accepted, document) == ()
    assert _extraction_contract_violations(rejected, document) == ()

    canonical = to_canonical_graph(
        rejected,
        artifact,
        parser=document.parser,
        created_at="2026-08-17T00:00:00Z",
    )
    assert canonical["claims"]
    assert canonical["observations"][0]["value"]["raw"] == bad_raw
    assert canonical["observations"][0]["value"]["unit"] == bad_unit


def test_pipeline_keeps_exact_quote_and_path_ownership_as_hard_gates(
    artifact,
) -> None:
    source_formula = r"\(x = 5\,\mathrm{mg}\)"
    document = _formula_document(artifact, source_formula)
    graph = _formula_graph(
        artifact,
        source_formula=source_formula,
        asserted_formula="$x = 6 kg$",
        raw_value="=6",
        unit="kg",
    )

    bad_quote_payload = graph.model_dump(mode="json")
    bad_quote_payload["anchors"][0]["locator"]["quote"] = "not in the source"
    bad_quote = PaperEvidenceGraph.model_validate(bad_quote_payload, strict=True)
    assert ContractIssue.PROVENANCE_QUOTE_NOT_FOUND in {
        violation.issue
        for violation in _extraction_contract_violations(bad_quote, document)
    }

    orphan_payload = graph.model_dump(mode="json")
    orphan_payload["observations"].append(
        {
            **orphan_payload["observations"][0],
            "id": "obs.formula.orphan",
        }
    )
    orphan = PaperEvidenceGraph.model_validate(orphan_payload, strict=True)
    assert ContractIssue.OBSERVATION_PATH_OWNERSHIP in {
        violation.issue
        for violation in _extraction_contract_violations(orphan, document)
    }
