from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from conftest import raw_graph_payload

from compiler_service import compiler as compiler_module
from compiler_service.canonical import graph_sha256
from compiler_service.claim_tolerance import claims_affected_by_violations
from compiler_service.compiler import (
    EvidenceCompiler,
    _canonicalize_exact_quote_regions,
    _provenance_contract_violations,
    compact_document_ir,
)
from compiler_service.config import (
    GPT_MAX_ATTEMPTS,
    GPT_MODEL,
)
from compiler_service.errors import (
    ContractIssue,
    ContractViolation,
    ModelOutputError,
)
from compiler_service.graph_contract import PaperEvidenceGraph, canonical_json
from compiler_service.job_control import ModelJobControl
from compiler_service.model_client import ModelJsonClient
from compiler_service.normalize import normalize_mineru
from compiler_service.source_catalog import build_source_catalog
from compiler_service.source_links import link_referenced_visuals


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


class _Prompt26Responder:
    """Return one whole extraction graph and, optionally, one verifier report."""

    def __init__(
        self,
        document,
        *,
        invalid_extraction_attempts: int = 0,
        invalid_verifier_attempts: int = 0,
        verdict: str = "qualified",
        on_verifier: Any = None,
        response_variant: str | None = None,
        empty_extraction: bool = False,
        extraction_payload_transform: Any = None,
        verification_payload_transform: Any = None,
    ) -> None:
        self.document = document
        self.invalid_extraction_attempts = invalid_extraction_attempts
        self.invalid_verifier_attempts = invalid_verifier_attempts
        self.verdict = verdict
        self.on_verifier = on_verifier
        self.response_variant = response_variant
        self.empty_extraction = empty_extraction
        self.extraction_payload_transform = extraction_payload_transform
        self.verification_payload_transform = verification_payload_transform
        self.stages: list[str] = []
        self.stage_attempts: dict[str, int] = {}

    def __call__(self, **kwargs: Any) -> SimpleNamespace:
        request = json.loads(kwargs["messages"][1]["content"])
        stage = "verification" if "extraction_graph" in request else "extraction"
        self.stages.append(stage)
        attempt = self.stage_attempts.get(stage, 0) + 1
        self.stage_attempts[stage] = attempt
        if stage == "verification":
            payload = self._verification(request, attempt)
            if self.verification_payload_transform is not None:
                payload = self.verification_payload_transform(payload)
            return _response(payload)
        payload = self._extraction(request, attempt)
        if self.extraction_payload_transform is not None:
            payload = self.extraction_payload_transform(payload)
        return _response(payload)

    def _extraction(self, request: dict[str, Any], attempt: int) -> dict[str, Any]:
        if self.empty_extraction:
            return {
                "schema_version": "1.0",
                "paper_id": self.document.artifact.paper_id,
                "claims": [],
                "observations": [],
                "reasoning": [],
                "assumptions": [],
                "anchors": [],
                "visual_evidence": [],
                "scopes": [],
                "edges": [],
            }
        result = raw_graph_payload(self.document.artifact)
        first_text = next(
            block
            for page in self.document.pages
            for block in page.blocks
            if block.kind == "paragraph" and block.text
        )
        result["anchors"][0]["locator"]["quote"] = first_text.text
        result["anchors"][0]["locator"]["source_span_id"] = next(
            item["source_span_id"]
            for item in request["trusted_source_spans"]
            if item["exact_text"] == first_text.text
        )
        if first_text.bbox is not None:
            result["anchors"][0]["locator"]["region"] = first_text.bbox.model_dump(
                mode="json"
            ) | {"unit": "normalized"}
        if attempt <= self.invalid_extraction_attempts:
            return result | {"unexpected": True}
        if self.response_variant == "reordered":
            return dict(reversed(tuple(result.items())))
        return result

    def _verification(
        self,
        request: dict[str, Any],
        attempt: int,
    ) -> dict[str, Any]:
        if self.on_verifier is not None:
            self.on_verifier()
        graph = PaperEvidenceGraph.model_validate(
            request["extraction_graph"], strict=True
        )
        assessments = []
        if attempt > self.invalid_verifier_attempts:
            assessments = [
                {
                    "id": f"assessment.local.{index}",
                    "claim_id": claim.id,
                    "scoped_claim_id": claim.scoped_claim_id,
                    "verdict": self.verdict,
                    "confidence": 0.7,
                    "evidence_ids": [
                        "anchor.text.1",
                        f"obs.local.{claim.id.rsplit('.', 1)[-1]}",
                    ],
                    "rationale": "The cited evidence determines this verdict.",
                    "limitations": [],
                }
                for index, claim in enumerate(graph.claims, start=1)
            ]
        return {
            "schema_version": "1.0",
            "paper_id": graph.paper_id,
            "graph_sha256": request["graph_sha256"],
            "verifier_model": GPT_MODEL,
            "assessments": assessments,
            "warnings": [],
        }


def _compiler(document, **responder_kwargs: Any):
    responder = _Prompt26Responder(document, **responder_kwargs)
    create = Mock(side_effect=responder)
    compiler = EvidenceCompiler(ModelJsonClient(_sdk(create), sleep=lambda _: None))
    return compiler, create, responder


def _expand_to_independent_claims(
    payload: dict[str, Any],
    count: int,
) -> dict[str, Any]:
    """Give each Claim its own reasoning branch over shared valid evidence."""

    template_claim = payload["claims"][0]
    template_observation = payload["observations"][0]
    template_reason = payload["reasoning"][0]
    claims = []
    observations = []
    reasoning = []
    edges = []
    for index in range(1, count + 1):
        claim_id = f"claim.local.{index}"
        reason_id = f"reason.local.{index}"
        observation_id = f"obs.local.{index}"
        claims.append(
            {
                **deepcopy(template_claim),
                "id": claim_id,
                "statement": f"Material finding {index} follows from treatment.",
                "proposition_key": None,
                "scoped_claim_id": None,
            }
        )
        reasoning.append(
            {
                **deepcopy(template_reason),
                "id": reason_id,
                "statement": f"Evidence branch {index} supports this finding.",
            }
        )
        observations.append(
            {
                **deepcopy(template_observation),
                "id": observation_id,
                "statement": f"The treated sample reached 5 mg in run {index}.",
            }
        )
        edges.extend(
            [
                {
                    "id": f"edge.grounds.{index}",
                    "source_id": "anchor.text.1",
                    "target_id": observation_id,
                    "relation": "grounds",
                    "rationale": None,
                    "confidence": None,
                },
                {
                    "id": f"edge.premise.{index}",
                    "source_id": observation_id,
                    "target_id": reason_id,
                    "relation": "premise_for",
                    "rationale": None,
                    "confidence": None,
                },
                {
                    "id": f"edge.support.{index}",
                    "source_id": reason_id,
                    "target_id": claim_id,
                    "relation": "supports",
                    "rationale": "Direct evidence branch.",
                    "confidence": 0.8,
                },
            ]
        )
    return payload | {
        "claims": claims,
        "observations": observations,
        "reasoning": reasoning,
        "edges": edges,
    }


def test_compiles_document_ir_with_one_prompt26_extraction_without_verifier(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document
    compiler, create, responder = _compiler(document)

    outcome = compiler.compile(document, created_at="2026-08-15T00:00:00Z")

    assert responder.stages == ["extraction"]
    assert create.call_count == outcome.extraction_attempts == 1
    assert outcome.envelope.root_artifact_version_id == artifact.version_id
    assert outcome.envelope.verification is None
    assert outcome.envelope.graph["paper_id"] == artifact.paper_id
    assert len(outcome.envelope.graph["claims"]) == 1
    assert [item["statement"] for item in outcome.envelope.graph["observations"]] == [
        "The treated sample reached 5 mg."
    ]
    assert outcome.envelope.graph["observations"][0]["value"] == {
        "raw": "5",
        "numeric": 5.0,
        "comparator": None,
        "unit": "mg",
        "uncertainty": None,
    }
    assert not any(
        name.startswith("evidence_pipeline") for name in __import__("sys").modules
    )


def test_compiler_hydrates_text_provenance_from_caller_owned_source_span(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document

    def rewrite_model_owned_coordinates(payload: dict[str, Any]) -> dict[str, Any]:
        anchor = payload["anchors"][0]
        anchor["artifact_id"] = "artifact:model-invented"
        anchor["version_id"] = "artifact-version:model-invented"
        anchor["artifact_sha256"] = "f" * 64
        anchor["locator"]["page"] = 99
        anchor["locator"]["quote"] = "The model paraphrased this source."
        anchor["locator"]["region"] = None
        return payload

    compiler, create, _ = _compiler(
        document,
        extraction_payload_transform=rewrite_model_owned_coordinates,
    )

    outcome = compiler.compile(document)

    anchor = next(
        item
        for item in outcome.envelope.graph["anchors"]
        if item["id"] == "anchor.text.1"
    )
    assert anchor["selector"]["page"] == 1
    assert anchor["quote"] == "The treated sample reached 5 mg."
    request = json.loads(create.call_args.kwargs["messages"][1]["content"])
    assert request["trusted_source_spans"]


def test_compiler_rejects_unknown_caller_owned_source_span(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document

    def replace_source_reference(payload: dict[str, Any]) -> dict[str, Any]:
        payload["anchors"][0]["locator"]["source_span_id"] = (
            "source-span:sha256:" + "f" * 64
        )
        return payload

    compiler, create, _ = _compiler(
        document,
        extraction_payload_transform=replace_source_reference,
    )

    with pytest.raises(ModelOutputError) as caught:
        compiler.compile(document)

    assert create.call_count == GPT_MAX_ATTEMPTS
    assert {diagnostic.contract_issue for diagnostic in caught.value.diagnostics} == {
        ContractIssue.PROVENANCE_SOURCE_REFERENCE_INVALID
    }


def test_compile_uses_one_model_control_for_the_single_extraction(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document
    compiler, create, responder = _compiler(document)
    control = ModelJobControl()

    outcome = compiler.compile(document, model_control=control)

    assert responder.stages == ["extraction"]
    assert control.usage.completion_calls == len(create.call_args_list)
    assert control.usage.request_attempts == outcome.extraction_attempts
    assert control.usage.request_attempts == len(create.call_args_list)
    assert all("max_tokens" not in call.kwargs for call in create.call_args_list)
    assert all(call.kwargs["timeout"] is None for call in create.call_args_list)


def test_compact_document_ir_removes_only_redundant_model_payload(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document
    table = next(
        block
        for page in document.pages
        for block in page.blocks
        if block.kind == "table"
    )
    table.structured["recognized_cells"] = [
        {"row": 1, "column": "Value", "raw_value": "5 mg"}
    ]

    compact = compact_document_ir(document)
    full_payload = canonical_json(
        {"trusted_artifact_context": artifact, "document_ir": document}
    ).encode()
    compact_payload = canonical_json(
        {"trusted_artifact_context": artifact, "document_ir": compact}
    ).encode()

    assert "markdown" not in compact
    assert len(compact_payload) < len(full_payload) * 0.8
    compact_blocks = [block for page in compact["pages"] for block in page["blocks"]]
    assert all(
        {"id", "kind", "page", "source_index", "bbox", "text"} <= block.keys()
        for block in compact_blocks
    )
    compact_table = next(block for block in compact_blocks if block["kind"] == "table")
    assert compact_table["structured"] == {
        "table_body": table.structured["table_body"],
        "recognized_cells": [{"row": 1, "column": "Value", "raw_value": "5 mg"}],
    }
    assert compact_table["text"] == "Table 1. Treated and control measurements."
    assert compact_table["structured"]["table_body"] not in compact_table["text"]
    paragraph = next(block for block in compact_blocks if block["kind"] == "paragraph")
    assert "structured" not in paragraph


def test_compact_document_ir_withholds_unsafe_structure_from_verifier_view(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document
    table = next(
        block
        for page in document.pages
        for block in page.blocks
        if block.kind == "table"
    )
    table.structured["recognized_cells"] = {"unexpected": "private-unsafe-9.8-mg"}
    catalog = build_source_catalog(document)
    assert catalog.structured_sources is not None
    assert table.id in catalog.structured_sources.unsafe_block_ids

    compact = compact_document_ir(
        document,
        unsafe_structured_block_ids=(table.id,),
        extra_warnings=catalog.warnings,
    )
    compact_table = next(
        block
        for page in compact["pages"]
        for block in page["blocks"]
        if block["id"] == table.id
    )

    assert "structured" not in compact_table
    assert compact_table["text"]
    assert table.structured["table_body"] in compact_table["text"]
    assert compact["warnings"] == list(
        dict.fromkeys((*document.warnings, *catalog.warnings))
    )
    assert "private-unsafe-9.8-mg" not in canonical_json(compact)


def _with_structured_code_block(document, structured: dict[str, Any]):
    payload = document.model_dump(mode="json")
    page = payload["pages"][0]
    page["blocks"].append(
        {
            "id": "block.synthetic-code",
            "kind": "other",
            "page": 1,
            "source_index": max(block["source_index"] for block in page["blocks"]) + 1,
            "bbox": {"x": 0.55, "y": 0.05, "width": 0.35, "height": 0.1},
            "text": None,
            "level": None,
            "structured": structured,
        }
    )
    return document.__class__.model_validate(payload, strict=True)


@pytest.mark.parametrize(
    ("structured", "quote"),
    [
        ({"code_body": "  result = sample + control  "}, "result = sample + control"),
        (
            {"code_caption": [{"text": "  Simulation source listing.  "}]},
            "Simulation source listing.",
        ),
    ],
)
def test_structured_code_exact_span_passes_final_provenance_and_region_repair(
    artifact, mineru_output, structured, quote
) -> None:
    document = _with_structured_code_block(
        normalize_mineru(mineru_output, artifact).document,
        structured,
    )
    payload = raw_graph_payload(artifact)
    payload["anchors"][0]["locator"].update(
        {
            "page": 1,
            "quote": quote,
            "region": {
                "x": 0.1,
                "y": 0.2,
                "width": 0.4,
                "height": 0.1,
                "unit": "normalized",
            },
        }
    )
    graph = PaperEvidenceGraph.model_validate(payload, strict=True)

    corrected = _canonicalize_exact_quote_regions(graph, document)

    assert corrected.anchors[0].locator.region is not None
    assert corrected.anchors[0].locator.region.model_dump(mode="json") == {
        "x": 0.55,
        "y": 0.05,
        "width": 0.35,
        "height": 0.1,
        "unit": "normalized",
    }
    assert _provenance_contract_violations(corrected, document) == ()


@pytest.mark.parametrize(
    ("structured", "quote"),
    [
        (
            {"unknown_code_path": "untrusted structured quote"},
            "untrusted structured quote",
        ),
        ({"code_body": "trusted exact code"}, "trusted exact code mismatch"),
    ],
)
def test_unadmitted_or_inexact_structured_quote_fails_final_provenance(
    artifact, mineru_output, structured, quote
) -> None:
    document = _with_structured_code_block(
        normalize_mineru(mineru_output, artifact).document,
        structured,
    )
    payload = raw_graph_payload(artifact)
    payload["anchors"][0]["locator"].update({"page": 1, "quote": quote, "region": None})
    graph = PaperEvidenceGraph.model_validate(payload, strict=True)

    violations = _provenance_contract_violations(graph, document)

    assert violations[0].issue is ContractIssue.PROVENANCE_QUOTE_NOT_FOUND


def test_unique_exact_quote_copies_frozen_region_and_is_idempotent(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document
    payload = raw_graph_payload(artifact)
    payload["anchors"][0]["locator"]["region"] = {
        "x": 0.2,
        "y": 0.2,
        "width": 0.3,
        "height": 0.1,
        "unit": "normalized",
    }
    graph = PaperEvidenceGraph.model_validate(payload, strict=True)

    corrected = _canonicalize_exact_quote_regions(graph, document)
    expected = document.pages[0].blocks[0].bbox
    assert expected is not None
    assert corrected.anchors[0].locator.region is not None
    assert corrected.anchors[0].locator.region.model_dump(mode="json") == (
        expected.model_dump(mode="json") | {"unit": "normalized"}
    )
    again = _canonicalize_exact_quote_regions(corrected, document)
    assert again.model_dump(mode="json") == corrected.model_dump(mode="json")
    assert _provenance_contract_violations(again, document) == ()


def test_duplicate_exact_quote_does_not_guess_a_region(artifact, mineru_output) -> None:
    document = normalize_mineru(mineru_output, artifact).document
    document_payload = document.model_dump(mode="json")
    duplicate = deepcopy(document_payload["pages"][0]["blocks"][0])
    duplicate.update(
        {
            "id": "block.duplicate.quote",
            "source_index": 99,
            "bbox": {"x": 0.6, "y": 0.2, "width": 0.3, "height": 0.1},
        }
    )
    document_payload["pages"][0]["blocks"].append(duplicate)
    duplicated_document = document.__class__.model_validate(
        document_payload, strict=True
    )
    payload = raw_graph_payload(artifact)
    wrong_region = {
        "x": 0.2,
        "y": 0.2,
        "width": 0.3,
        "height": 0.1,
        "unit": "normalized",
    }
    payload["anchors"][0]["locator"]["region"] = wrong_region
    graph = PaperEvidenceGraph.model_validate(payload, strict=True)

    unchanged = _canonicalize_exact_quote_regions(graph, duplicated_document)
    assert unchanged.anchors[0].locator.region is not None
    assert unchanged.anchors[0].locator.region.model_dump(mode="json") == wrong_region
    violations = _provenance_contract_violations(unchanged, duplicated_document)
    assert violations[0].issue is ContractIssue.PROVENANCE_REGION_MISMATCH


def test_unique_exact_quote_without_bbox_sets_region_to_null(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document
    document_payload = document.model_dump(mode="json")
    document_payload["pages"][0]["blocks"][0]["bbox"] = None
    no_bbox_document = document.__class__.model_validate(document_payload, strict=True)
    graph = PaperEvidenceGraph.model_validate(raw_graph_payload(artifact), strict=True)

    corrected = _canonicalize_exact_quote_regions(graph, no_bbox_document)
    assert corrected.anchors[0].locator.region is None
    assert _provenance_contract_violations(corrected, no_bbox_document) == ()


def test_source_linked_visual_anchor_keeps_whole_visual_region_idempotently(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document
    payload = raw_graph_payload(artifact)
    payload["anchors"][0]["locator"].update(
        {
            "quote": "Table 1. Treated and control measurements.",
            "region": {
                "x": 0.1,
                "y": 0.4,
                "width": 0.8,
                "height": 0.4,
                "unit": "normalized",
            },
        }
    )
    graph = PaperEvidenceGraph.model_validate(payload, strict=True)
    linked = link_referenced_visuals(graph, document)
    corrected = _canonicalize_exact_quote_regions(linked, document)

    visual_anchor = next(
        anchor for anchor in corrected.anchors if anchor.id == "anchor.table.1"
    )
    table_block = next(
        block for block in document.pages[0].blocks if block.kind == "table"
    )
    assert table_block.bbox is not None
    assert visual_anchor.locator.region is not None
    assert visual_anchor.locator.region.model_dump(mode="json") == (
        table_block.bbox.model_dump(mode="json") | {"unit": "normalized"}
    )
    again = _canonicalize_exact_quote_regions(corrected, document)
    assert again.model_dump(mode="json") == corrected.model_dump(mode="json")


def test_optional_verifier_is_global_graph_hash_bound_and_phase_ordered(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document
    table = next(
        block
        for page in document.pages
        for block in page.blocks
        if block.kind == "table"
    )
    table.structured["recognized_cells"] = {
        "unexpected": "private-verifier-unsafe-7.3-mg"
    }
    catalog = build_source_catalog(document)
    assert catalog.structured_sources is not None
    expected_compact = compact_document_ir(
        document,
        unsafe_structured_block_ids=catalog.structured_sources.unsafe_block_ids,
        extra_warnings=catalog.warnings,
    )
    phases: list[str] = []

    def observe_verifier() -> None:
        assert phases == ["validating"]

    def without_withheld_table(payload: dict[str, Any]) -> dict[str, Any]:
        # The unsafe table structure is withheld from the model, so a faithful
        # extraction carries no cells for it (see the unverifiable-table test).
        payload["visual_evidence"] = []
        payload["edges"] = [
            edge for edge in payload["edges"] if edge["id"] != "edge.grounds.visual.1"
        ]
        return payload

    compiler, create, responder = _compiler(
        document,
        on_verifier=observe_verifier,
        verdict="contradicted",
        extraction_payload_transform=without_withheld_table,
    )
    outcome = compiler.compile(
        document,
        verify=True,
        created_at="2026-08-15T00:00:00Z",
        on_validating=lambda: phases.append("validating"),
    )

    assert responder.stages == ["extraction", "verification"]
    assert create.call_count == 2
    assert phases == ["validating"]
    assert outcome.extraction_attempts == 1
    assert outcome.verification_attempts == 1
    assert outcome.envelope.verification is not None
    assert (
        outcome.envelope.verification.report["assessments"][0]["verdict"]
        == "contradicted"
    )
    assert outcome.envelope.verification.report["graph_sha256"] == graph_sha256(
        outcome.envelope.graph
    )
    assert outcome.envelope.graph["assessments"] == []
    verification_input = json.loads(
        create.call_args_list[-1].kwargs["messages"][1]["content"]
    )
    assert verification_input["document_ir"] == expected_compact
    verification_claims = verification_input["extraction_graph"]["claims"]
    assert verification_input["verification_claim_manifest"] == {
        "claim_count": len(verification_claims),
        "claims": [
            {
                "claim_id": claim["id"],
                "scoped_claim_id": claim["scoped_claim_id"],
            }
            for claim in verification_claims
        ],
    }
    serialized_verification_input = create.call_args_list[-1].kwargs["messages"][1][
        "content"
    ]
    assert serialized_verification_input.rfind(
        '"verification_claim_manifest":'
    ) > serialized_verification_input.rfind('"graph_sha256":')
    assert "private-verifier-unsafe-7.3-mg" not in canonical_json(
        verification_input["document_ir"]
    )
    assert "markdown" not in verification_input["document_ir"]


def test_extraction_stage_retries_schema_failure_then_compiles(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document
    compiler, create, responder = _compiler(
        document,
        invalid_extraction_attempts=1,
    )

    outcome = compiler.compile(document)

    assert responder.stages == ["extraction", "extraction"]
    assert create.call_count == outcome.extraction_attempts == 2
    extraction_calls = create.call_args_list
    assert (
        extraction_calls[0].kwargs["messages"][1]["content"]
        == extraction_calls[1].kwargs["messages"][1]["content"]
    )
    retry_prompt = extraction_calls[1].kwargs["messages"][0]["content"]
    assert "Retry correction from bounded validator diagnostics only" in retry_prompt
    assert "extra_forbidden" in retry_prompt


def test_verifier_retries_unassessed_claim_then_accepts_complete_report(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document
    compiler, create, responder = _compiler(document, invalid_verifier_attempts=1)

    outcome = compiler.compile(document, verify=True)

    assert responder.stages == ["extraction", "verification", "verification"]
    assert create.call_count == 3
    assert outcome.verification_attempts == 2
    assert outcome.envelope.verification is not None
    assessments = outcome.envelope.verification.report["assessments"]
    assert len(assessments) == len(outcome.envelope.graph["claims"]) == 1
    retry_prompt = create.call_args_list[-1].kwargs["messages"][0]["content"]
    assert "verification_claim_unassessed" in retry_prompt
    assert "exactly one assessment for every Claim" in retry_prompt
    assert "do not omit, merge, or replace any Claim" in retry_prompt
    assert "verification_claim_manifest.claims" in retry_prompt
    assert "verification_claim_manifest.claim_count" in retry_prompt
    assert '"loc":["assessments",0,"claim_id"]' in retry_prompt


def test_verifier_omission_reports_only_ordered_manifest_index(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document

    def extraction(payload: dict[str, Any]) -> dict[str, Any]:
        return _expand_to_independent_claims(payload, 6)

    def verification(payload: dict[str, Any]) -> dict[str, Any]:
        payload["assessments"] = [
            assessment
            for index, assessment in enumerate(payload["assessments"])
            if index not in {1, 4}
        ]
        return payload

    compiler, create, responder = _compiler(
        document,
        extraction_payload_transform=extraction,
        verification_payload_transform=verification,
    )

    with pytest.raises(ModelOutputError) as caught:
        compiler.compile(document, verify=True)

    assert responder.stages == ["extraction"] + ["verification"] * GPT_MAX_ATTEMPTS
    assert all(
        diagnostic.contract_issue is ContractIssue.VERIFICATION_CLAIM_UNASSESSED
        and diagnostic.contract_loc == ("assessments", 1, "claim_id")
        for diagnostic in caught.value.diagnostics
    )
    verification_calls = create.call_args_list[1:]
    manifest = json.loads(verification_calls[0].kwargs["messages"][1]["content"])[
        "verification_claim_manifest"
    ]
    assert manifest["claim_count"] == 6
    assert [entry["claim_id"] for entry in manifest["claims"]] == [
        f"claim.local.{index}" for index in range(1, 7)
    ]
    for call in verification_calls[1:]:
        feedback = call.kwargs["messages"][0]["content"]
        assert '"loc":["assessments",1,"claim_id"]' in feedback
        assert "verification_claim_manifest.claims" in feedback
        assert "claim.local.2" not in feedback
        assert "claim.local.5" not in feedback


def test_two_of_ten_malformed_claims_are_discarded_without_retry(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document

    def transform(payload: dict[str, Any]) -> dict[str, Any]:
        expanded = _expand_to_independent_claims(payload, 10)
        expanded["claims"][2]["claim_type"] = "not-a-real-type"
        expanded["claims"][7]["unexpected"] = True
        return expanded

    compiler, create, responder = _compiler(
        document,
        extraction_payload_transform=transform,
    )

    outcome = compiler.compile(document)

    assert responder.stages == ["extraction"]
    assert create.call_count == outcome.extraction_attempts == 1
    assert len(outcome.envelope.graph["claims"]) == 8
    assert len(outcome.envelope.graph["reasoning"]) == 8
    assert len(outcome.envelope.graph["observations"]) == 8


def test_three_of_ten_malformed_claims_exceed_tolerance_and_retry_to_failure(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document

    def transform(payload: dict[str, Any]) -> dict[str, Any]:
        expanded = _expand_to_independent_claims(payload, 10)
        for index in (1, 4, 8):
            expanded["claims"][index]["unexpected"] = True
        return expanded

    compiler, create, responder = _compiler(
        document,
        extraction_payload_transform=transform,
    )

    with pytest.raises(ModelOutputError):
        compiler.compile(document)

    assert responder.stages == ["extraction"] * GPT_MAX_ATTEMPTS
    assert create.call_count == GPT_MAX_ATTEMPTS


def test_one_of_ten_claims_with_a_broken_private_evidence_path_is_discarded(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document

    def transform(payload: dict[str, Any]) -> dict[str, Any]:
        expanded = _expand_to_independent_claims(payload, 10)
        expanded["edges"] = [
            edge for edge in expanded["edges"] if edge["id"] != "edge.premise.5"
        ]
        return expanded

    compiler, create, responder = _compiler(
        document,
        extraction_payload_transform=transform,
    )

    outcome = compiler.compile(document)

    assert responder.stages == ["extraction"]
    assert create.call_count == 1
    assert len(outcome.envelope.graph["claims"]) == 9
    assert len(outcome.envelope.graph["reasoning"]) == 9


def test_fabricated_table_value_is_rejected_with_located_feedback(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document
    attempts = {"count": 0}

    def extraction(payload: dict[str, Any]) -> dict[str, Any]:
        attempts["count"] += 1
        if attempts["count"] == 1:
            cell = payload["visual_evidence"][0]["cells"][0]
            cell.update({"raw_value": "7 mg", "numeric_value": 7})
        return payload

    compiler, create, responder = _compiler(
        document,
        extraction_payload_transform=extraction,
    )

    outcome = compiler.compile(document)

    assert responder.stages == ["extraction", "extraction"]
    retry_prompt = create.call_args_list[-1].kwargs["messages"][0]["content"]
    assert "provenance_visual_value_not_found" in retry_prompt
    assert '"loc":["visual_evidence",0,"cells",0' in retry_prompt
    assert "structured.table_body" in retry_prompt
    (visual,) = outcome.envelope.graph["visual_evidence"]
    assert visual["cells"][0]["raw_value"] == "5 mg"


def test_invalid_span_on_one_claims_private_anchor_isolates_only_that_claim(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document

    def extraction(payload: dict[str, Any]) -> dict[str, Any]:
        expanded = _expand_to_independent_claims(payload, 10)
        bad_anchor = deepcopy(expanded["anchors"][0])
        bad_anchor["id"] = "anchor.text.private"
        bad_anchor["locator"]["source_span_id"] = "source-span:does-not-exist"
        expanded["anchors"].append(bad_anchor)
        observation = expanded["observations"][4]
        observation["anchor_ids"] = ["anchor.text.private"]
        for edge in expanded["edges"]:
            if edge["id"] == "edge.grounds.5":
                edge["source_id"] = "anchor.text.private"
        return expanded

    compiler, create, responder = _compiler(
        document, extraction_payload_transform=extraction
    )

    outcome = compiler.compile(document)

    # One mistyped span ID no longer forces a whole-document re-extraction.
    assert responder.stages == ["extraction"]
    assert len(outcome.envelope.graph["claims"]) == 9
    assert {
        "kind": "claim_removed",
        "target": "claim.local.5",
        "detail": "extraction:provenance_source_reference_invalid",
    } in _audit_entries(outcome)
    assert "compiler_discarded_local_claims:1" in (
        outcome.envelope.compiler_audit.warnings
    )


def test_duplicate_claim_identity_is_located_and_isolated(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document

    def extraction(payload: dict[str, Any]) -> dict[str, Any]:
        expanded = _expand_to_independent_claims(payload, 10)
        duplicate = expanded["claims"][7]
        duplicate["statement"] = expanded["claims"][2]["statement"]
        duplicate["polarity"] = "negated"
        return expanded

    compiler, create, responder = _compiler(
        document, extraction_payload_transform=extraction
    )

    outcome = compiler.compile(document)

    assert responder.stages == ["extraction"]
    assert len(outcome.envelope.graph["claims"]) == 9
    assert {
        "kind": "claim_removed",
        "target": "claim.local.8",
        "detail": "extraction:canonical_id_collision",
    } in _audit_entries(outcome)


def test_orphan_observation_is_pruned_and_audited_without_retry(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document

    def extraction(payload: dict[str, Any]) -> dict[str, Any]:
        expanded = _expand_to_independent_claims(payload, 10)
        expanded["observations"].append(
            {**deepcopy(expanded["observations"][0]), "id": "obs.orphan"}
        )
        expanded["edges"].append(
            {
                "id": "edge.grounds.orphan",
                "source_id": "anchor.text.1",
                "target_id": "obs.orphan",
                "relation": "grounds",
                "rationale": None,
                "confidence": None,
            }
        )
        return expanded

    compiler, create, responder = _compiler(
        document, extraction_payload_transform=extraction
    )

    outcome = compiler.compile(document)

    # Previously its missing premise_for was a global failure: no Claim
    # depends on it, so nothing could be isolated and every attempt retried.
    assert responder.stages == ["extraction"]
    assert len(outcome.envelope.graph["claims"]) == 10
    assert "obs.orphan" not in {item.id for item in outcome.raw_graph.observations}
    assert {
        "kind": "node_removed",
        "target": "obs.orphan",
        "detail": "extraction:unreachable",
    } in _audit_entries(outcome)


def test_orphan_anchor_with_an_invalid_span_is_pruned_without_retry(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document

    def extraction(payload: dict[str, Any]) -> dict[str, Any]:
        expanded = _expand_to_independent_claims(payload, 10)
        orphan = deepcopy(expanded["anchors"][0])
        orphan["id"] = "anchor.orphan"
        orphan["locator"]["source_span_id"] = "source-span:does-not-exist"
        expanded["anchors"].append(orphan)
        return expanded

    compiler, create, responder = _compiler(
        document, extraction_payload_transform=extraction
    )

    outcome = compiler.compile(document)

    assert responder.stages == ["extraction"]
    assert len(outcome.envelope.graph["claims"]) == 10
    assert {
        "kind": "node_removed",
        "target": "anchor.orphan",
        "detail": "extraction:unreachable",
    } in _audit_entries(outcome)


def test_malformed_observation_isolates_only_the_claim_that_uses_it(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document

    def extraction(payload: dict[str, Any]) -> dict[str, Any]:
        expanded = _expand_to_independent_claims(payload, 10)
        expanded["observations"][4]["unexpected"] = True
        return expanded

    compiler, create, responder = _compiler(
        document, extraction_payload_transform=extraction
    )

    outcome = compiler.compile(document)

    # A malformed Claim record was already isolated; a malformed Observation
    # exclusive to one Claim used to fail the whole response.
    assert responder.stages == ["extraction"]
    assert len(outcome.envelope.graph["claims"]) == 9
    entries = _audit_entries(outcome)
    assert {
        "kind": "claim_removed",
        "target": "claim.local.5",
        "detail": "extraction:dependency_invalid",
    } in entries
    assert {
        "kind": "node_removed",
        "target": "obs.local.5",
        "detail": "extraction:schema_invalid",
    } in entries


def test_malformed_claim_with_an_unusable_id_is_dropped_without_a_target(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document

    def extraction(payload: dict[str, Any]) -> dict[str, Any]:
        expanded = _expand_to_independent_claims(payload, 10)
        expanded["claims"][3]["id"] = "claim 4"
        return expanded

    compiler, create, responder = _compiler(
        document, extraction_payload_transform=extraction
    )

    outcome = compiler.compile(document)

    # The raw ID used to become an audit target that failed envelope
    # validation after both paid stages.
    assert responder.stages == ["extraction"]
    assert len(outcome.envelope.graph["claims"]) == 9
    assert {
        "kind": "claim_removed",
        "target": None,
        "detail": "extraction:schema_invalid",
    } in _audit_entries(outcome)


def test_bad_edge_from_a_shared_anchor_isolates_only_its_dependent_claim(
    artifact,
) -> None:
    graph = PaperEvidenceGraph.model_validate(
        _expand_to_independent_claims(raw_graph_payload(artifact), 10), strict=True
    )
    index = next(
        position
        for position, edge in enumerate(graph.edges)
        if edge.id == "edge.grounds.5"
    )
    # anchor.text.1 grounds every Claim's Observation; the edge's target,
    # obs.local.5, feeds only claim.local.5.
    assert graph.edges[index].source_id == "anchor.text.1"
    violation = ContractViolation(
        ContractIssue.CANONICAL_EDGE_COLLAPSE, ("edges", index)
    )

    assert claims_affected_by_violations(graph, (violation,)) == {
        "claim.local.5": ContractIssue.CANONICAL_EDGE_COLLAPSE
    }


def test_quote_index_is_built_once_per_extraction_admission(
    artifact, mineru_output, monkeypatch
) -> None:
    document = normalize_mineru(mineru_output, artifact).document
    built: list[object] = []

    class CountingQuoteIndex(compiler_module._QuoteIndex):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            built.append(self)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(compiler_module, "_QuoteIndex", CountingQuoteIndex)

    def extraction(payload: dict[str, Any]) -> dict[str, Any]:
        # A bad span on one Claim's private anchor forces a second admission
        # round, which re-runs every provenance check.
        expanded = _expand_to_independent_claims(payload, 10)
        bad_anchor = deepcopy(expanded["anchors"][0])
        bad_anchor["id"] = "anchor.text.private"
        bad_anchor["locator"]["source_span_id"] = "source-span:does-not-exist"
        expanded["anchors"].append(bad_anchor)
        expanded["observations"][4]["anchor_ids"] = ["anchor.text.private"]
        for edge in expanded["edges"]:
            if edge["id"] == "edge.grounds.5":
                edge["source_id"] = "anchor.text.private"
        return expanded

    compiler, create, responder = _compiler(
        document, extraction_payload_transform=extraction
    )

    outcome = compiler.compile(document)

    assert len(outcome.envelope.graph["claims"]) == 9
    assert len(built) == 1


def test_clean_verified_compile_canonicalizes_once(
    artifact, mineru_output, monkeypatch
) -> None:
    document = normalize_mineru(mineru_output, artifact).document
    calls: list[str] = []
    original = compiler_module.to_canonical_graph

    def counting(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append("canonicalize")
        return original(*args, **kwargs)

    monkeypatch.setattr(compiler_module, "to_canonical_graph", counting)
    compiler, create, responder = _compiler(document)

    outcome = compiler.compile(document, verify=True)

    assert responder.stages == ["extraction", "verification"]
    assert outcome.envelope.verification is not None
    # Previously three passes: admission, after verification, and a final
    # re-check that could only detect nondeterminism.
    assert calls == ["canonicalize"]


def test_small_extraction_gets_no_silent_tolerance(artifact, mineru_output) -> None:
    document = normalize_mineru(mineru_output, artifact).document

    def extraction(payload: dict[str, Any]) -> dict[str, Any]:
        expanded = _expand_to_independent_claims(payload, 4)
        expanded["claims"][1]["unexpected"] = True
        return expanded

    compiler, create, responder = _compiler(
        document, extraction_payload_transform=extraction
    )

    # One of four Claims is 25%, above the 20% cumulative budget.
    with pytest.raises(ModelOutputError):
        compiler.compile(document)
    assert responder.stages == ["extraction"] * GPT_MAX_ATTEMPTS


def test_optional_projection_failures_never_discard_a_paid_compile(
    artifact, mineru_output, monkeypatch
) -> None:
    document = normalize_mineru(mineru_output, artifact).document

    class BrokenClassifier:
        def classify_document(self, _document):
            raise RuntimeError("OpenAlex is unavailable")

    def broken_citation_source(_document):
        raise ValueError("bibliography segmentation failed")

    monkeypatch.setattr(
        "compiler_service.compiler.build_citation_source", broken_citation_source
    )
    responder = _Prompt26Responder(document)
    compiler = EvidenceCompiler(
        ModelJsonClient(_sdk(Mock(side_effect=responder)), sleep=lambda _: None),
        topic_classifier=BrokenClassifier(),
    )

    outcome = compiler.compile(document, verify=True)

    assert outcome.envelope.openalex is None
    assert outcome.citation_source.references == []
    assert outcome.citation_source.mentions == []
    assert outcome.citation_source.artifact_version_id == artifact.version_id
    assert outcome.envelope.compiler_audit.warnings == [
        "citation_source_unavailable",
        "openalex_classification_unavailable",
    ]
    assert len(outcome.envelope.graph["claims"]) == 1


def _audit_entries(outcome) -> list[dict[str, Any]]:
    assert outcome.envelope.compiler_audit is not None
    return [
        item.model_dump(mode="json") for item in outcome.envelope.compiler_audit.entries
    ]


def test_verifier_retry_supplies_only_the_bad_assessment_and_keeps_every_claim(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document
    attempts = {"count": 0}

    def extraction(payload: dict[str, Any]) -> dict[str, Any]:
        return _expand_to_independent_claims(payload, 10)

    def verification(payload: dict[str, Any]) -> dict[str, Any]:
        attempts["count"] += 1
        if attempts["count"] == 1:
            del payload["assessments"][4]["id"]
        return payload

    compiler, create, responder = _compiler(
        document,
        extraction_payload_transform=extraction,
        verification_payload_transform=verification,
    )

    outcome = compiler.compile(document, verify=True)

    assert responder.stages == ["extraction", "verification", "verification"]
    assert outcome.verification_attempts == 2
    assert len(outcome.envelope.graph["claims"]) == 10
    assert outcome.envelope.verification is not None
    report = outcome.envelope.verification.report
    assert [item["claim_id"] for item in report["assessments"]] == [
        f"claim.local.{index}" for index in range(1, 11)
    ]
    assert report["graph_sha256"] == graph_sha256(outcome.envelope.graph)
    assert not any(
        warning.startswith("compiler_") for warning in report["warnings"]
    )
    retry_prompt = create.call_args_list[-1].kwargs["messages"][0]["content"]
    assert '"loc":["assessments",4,"claim_id"]' in retry_prompt
    entries = _audit_entries(outcome)
    assert {
        "kind": "verification_attempts_merged",
        "target": None,
        "detail": "contributing_attempts=2",
    } in entries
    assert not any(entry["kind"] == "claim_removed" for entry in entries)


def test_unknown_verifier_claim_id_is_reported_and_requested_again(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document
    attempts = {"count": 0}

    def extraction(payload: dict[str, Any]) -> dict[str, Any]:
        return _expand_to_independent_claims(payload, 5)

    def verification(payload: dict[str, Any]) -> dict[str, Any]:
        attempts["count"] += 1
        if attempts["count"] == 1:
            # A typo must not silently filter the assessment and then delete
            # the real Claim as unassessed.
            payload["assessments"][2]["claim_id"] = "claim-local-3"
        return payload

    compiler, create, responder = _compiler(
        document,
        extraction_payload_transform=extraction,
        verification_payload_transform=verification,
    )

    outcome = compiler.compile(document, verify=True)

    assert responder.stages == ["extraction", "verification", "verification"]
    assert len(outcome.envelope.graph["claims"]) == 5
    retry_prompt = create.call_args_list[-1].kwargs["messages"][0]["content"]
    assert "verification_claim_unknown" in retry_prompt
    assert '"loc":["assessments",2,"claim_id"]' in retry_prompt
    entries = _audit_entries(outcome)
    assert {
        "kind": "verification_unknown_claim_ids",
        "target": None,
        "detail": "count=1",
    } in entries


def test_persistent_verifier_error_removes_a_claim_only_after_bounded_attempts(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document

    def extraction(payload: dict[str, Any]) -> dict[str, Any]:
        return _expand_to_independent_claims(payload, 10)

    def verification(payload: dict[str, Any]) -> dict[str, Any]:
        del payload["assessments"][4]["id"]
        return payload

    compiler, create, responder = _compiler(
        document,
        extraction_payload_transform=extraction,
        verification_payload_transform=verification,
    )

    outcome = compiler.compile(document, verify=True)

    # Every fresh attempt is spent before the Compiler gives up on one Claim,
    # and the removal is explicit in both the report and the audit.
    assert responder.stages == ["extraction"] + ["verification"] * GPT_MAX_ATTEMPTS
    assert outcome.verification_attempts == GPT_MAX_ATTEMPTS
    assert len(outcome.envelope.graph["claims"]) == 9
    assert outcome.envelope.verification is not None
    report = outcome.envelope.verification.report
    assert len(report["assessments"]) == 9
    assert report["graph_sha256"] == graph_sha256(outcome.envelope.graph)
    assert "compiler_discarded_local_claims:1" in report["warnings"]
    assert "compiler_removed_unverified_claims:1" in report["warnings"]
    assert {
        "kind": "claim_removed",
        "target": "claim.local.5",
        "detail": "verification:verification_claim_unassessed",
    } in _audit_entries(outcome)


def test_verifier_rationale_number_mismatch_does_not_retry_or_drop_claim(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document

    def verification(payload: dict[str, Any]) -> dict[str, Any]:
        payload["assessments"][0]["rationale"] = (
            "The review commentary mentions an unmatched result of 999 kg."
        )
        return payload

    compiler, create, responder = _compiler(
        document,
        verification_payload_transform=verification,
    )

    outcome = compiler.compile(document, verify=True)

    assert responder.stages == ["extraction", "verification"]
    assert create.call_count == 2
    assert outcome.extraction_attempts == 1
    assert outcome.verification_attempts == 1
    assert len(outcome.envelope.graph["claims"]) == 1


def test_extraction_and_verifier_share_one_cumulative_twenty_percent_budget(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document

    def extraction(payload: dict[str, Any]) -> dict[str, Any]:
        expanded = _expand_to_independent_claims(payload, 10)
        expanded["claims"][1]["unexpected"] = True
        return expanded

    def verification(payload: dict[str, Any]) -> dict[str, Any]:
        del payload["assessments"][3]["id"]
        del payload["assessments"][6]["id"]
        return payload

    compiler, create, responder = _compiler(
        document,
        extraction_payload_transform=extraction,
        verification_payload_transform=verification,
    )

    with pytest.raises(ModelOutputError):
        compiler.compile(document, verify=True)

    assert responder.stages == ["extraction"] + ["verification"] * GPT_MAX_ATTEMPTS
    assert create.call_count == 1 + GPT_MAX_ATTEMPTS


def test_single_claim_failure_is_never_converted_to_empty_success(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document

    def transform(payload: dict[str, Any]) -> dict[str, Any]:
        payload["claims"][0]["unexpected"] = True
        return payload

    compiler, create, _ = _compiler(
        document,
        extraction_payload_transform=transform,
    )

    with pytest.raises(ModelOutputError):
        compiler.compile(document)
    assert create.call_count == GPT_MAX_ATTEMPTS


def test_pipeline_compile_is_deterministic_and_run_hash_covers_model_content(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document
    first_compiler, _, _ = _compiler(document)
    second_compiler, _, _ = _compiler(document)
    changed_compiler, _, _ = _compiler(
        document,
        response_variant="reordered",
    )

    first = first_compiler.compile(document, created_at="2026-08-15T00:00:00Z")
    second = second_compiler.compile(document, created_at="2026-08-15T00:00:00Z")
    changed = changed_compiler.compile(document, created_at="2026-08-15T00:00:00Z")

    assert first.envelope.graph == second.envelope.graph == changed.envelope.graph
    assert first.envelope.extraction.run_id == second.envelope.extraction.run_id
    assert first.envelope.extraction.run_id != changed.envelope.extraction.run_id


def test_compile_links_a_model_selected_text_reference_to_visual_region(
    artifact, mineru_output
) -> None:
    content = deepcopy(mineru_output.content_list)
    content[0]["text"] = "According to Table 1, the treated sample reached 5 mg."
    document = normalize_mineru(
        replace(mineru_output, content_list=content), artifact
    ).document
    compiler, _, _ = _compiler(document)

    outcome = compiler.compile(document)

    anchors = outcome.envelope.graph["anchors"]
    text_anchor = next(item for item in anchors if item["anchor_type"] == "text")
    table_anchor = next(item for item in anchors if item["anchor_type"] == "table")
    assert table_anchor["selector"]["region"] == {
        "x": 0.1,
        "y": 0.4,
        "width": 0.8,
        "height": 0.4,
        "unit": "normalized",
    }
    observation = outcome.envelope.graph["observations"][0]
    assert text_anchor["id"] in observation["anchor_ids"]
    assert table_anchor["id"] in observation["anchor_ids"]


def test_zero_claim_extraction_gets_only_caller_owned_provenance(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document
    compiler, create, responder = _compiler(document, empty_extraction=True)
    phases: list[str] = []

    outcome = compiler.compile(
        document,
        on_validating=lambda: phases.append("validating"),
    )

    assert responder.stages == ["extraction"]
    assert create.call_count == 1
    assert phases == ["validating"]
    assert outcome.envelope.graph["claims"] == []
    assert outcome.envelope.graph["observations"] == []
    assert len(outcome.envelope.graph["anchors"]) == 1
    anchor = outcome.envelope.graph["anchors"][0]
    assert anchor["id"].startswith("anchor.provenance.")
    assert anchor["artifact_version_id"] == artifact.version_id


def test_zero_claim_empty_ocr_gets_page_metadata_provenance(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document
    empty_document = document.model_copy(
        update={
            "pages": [page.model_copy(update={"blocks": []}) for page in document.pages]
        }
    )
    compiler, create, responder = _compiler(empty_document, empty_extraction=True)

    outcome = compiler.compile(empty_document)

    assert responder.stages == ["extraction"]
    assert create.call_count == 1
    assert outcome.envelope.graph["claims"] == []
    assert outcome.envelope.graph["observations"] == []
    assert len(outcome.envelope.graph["anchors"]) == 1
    anchor = outcome.envelope.graph["anchors"][0]
    assert anchor["anchor_type"] == "metadata"
    assert anchor["artifact_version_id"] == artifact.version_id
    assert anchor["selector"]["page"] == 1
    assert anchor["selector"].get("quote") is None
    assert anchor["selector"].get("region") is None


def test_verifier_exhaustion_fails_closed_after_bounded_fresh_attempts(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document
    compiler, create, responder = _compiler(
        document,
        invalid_verifier_attempts=GPT_MAX_ATTEMPTS,
    )

    with pytest.raises(
        ModelOutputError,
        match=rf"{GPT_MAX_ATTEMPTS} fresh attempts",
    ) as caught:
        compiler.compile(document, verify=True)

    assert responder.stages == [
        "extraction",
        *(["verification"] * GPT_MAX_ATTEMPTS),
    ]
    assert create.call_count == 1 + GPT_MAX_ATTEMPTS
    assert all(
        item.contract_issue is ContractIssue.VERIFICATION_CLAIM_UNASSESSED
        for item in caught.value.diagnostics
    )


def test_parser_version_changes_snapshot_and_extraction_run_provenance(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document
    older = document.model_copy(
        update={"parser": document.parser.model_copy(update={"version": "3.4.4"})}
    )
    current_compiler, _, _ = _compiler(document)
    older_compiler, _, _ = _compiler(older)

    current = current_compiler.compile(document, created_at="2026-08-15T00:00:00Z")
    previous = older_compiler.compile(older, created_at="2026-08-15T00:00:00Z")

    assert current.envelope.extraction.run_id != previous.envelope.extraction.run_id
    current_generator = current.envelope.graph["snapshots"][0]["generator"]
    previous_generator = previous.envelope.graph["snapshots"][0]["generator"]
    assert current_generator["version"] == (
        "compiler/1.0+mineru/3.1.15-remote-pipeline"
    )
    assert previous_generator["version"] == (
        "compiler/1.0+mineru/3.4.4-remote-pipeline"
    )
