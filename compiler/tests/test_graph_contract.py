from __future__ import annotations

import hashlib
import json
import subprocess
from copy import deepcopy

import pytest
from conftest import REPOSITORY, raw_graph_payload
from pydantic import ValidationError

from compiler_service.canonical import (
    EXTRACTION_PROMPT_VERSION,
    VERIFICATION_PROMPT_VERSION,
    graph_sha256,
    to_canonical_graph,
)
from compiler_service.errors import (
    ContractIssue,
    ContractValidationError,
    ContractViolation,
)
from compiler_service.graph_contract import (
    MAX_TABLE_CELLS,
    MAX_VISUAL_AXES,
    MAX_VISUAL_FEATURES,
    MAX_VISUAL_POINTS_PER_SERIES,
    MAX_VISUAL_SERIES,
    PaperEvidenceGraph,
    VerificationReport,
    _derivation_cycle_edge,
    canonical_hash_json,
    canonical_json,
)
from compiler_service.prompts import EXTRACTION_JSON_EXAMPLE, EXTRACTION_SYSTEM_PROMPT


def _typescript_graph_hash(graph: dict) -> str:
    script = """
import {
  hashEvidenceGraph,
  parseEvidenceGraph,
} from './lib/evidence-graph/index.ts';
const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const graph = JSON.parse(Buffer.concat(chunks).toString('utf8'));
const parsed = await parseEvidenceGraph(graph);
process.stdout.write(await hashEvidenceGraph(parsed));
"""
    result = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
        cwd=REPOSITORY,
        input=json.dumps(graph, ensure_ascii=False).encode(),
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
    return result.stdout.decode()

def test_hash_json_matches_typescript_number_boundaries() -> None:
    values = [42.0, 1e-5, 1e-6, 1e-7, 1e20, 1e21, -0.0]
    payload = {"values": values}
    serialized = canonical_hash_json(payload)
    assert canonical_json({"value": 42.0}) == '{"value":42.0}'
    assert serialized == (
        '{"values":[42,0.00001,0.000001,1e-7,100000000000000000000,1e+21,0]}'
    )
    script = """
import { hashCanonical } from './lib/evidence-graph/index.ts';
const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const value = JSON.parse(Buffer.concat(chunks).toString('utf8'));
process.stdout.write(await hashCanonical(value));
"""
    result = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
        cwd=REPOSITORY,
        input=json.dumps(payload, ensure_ascii=False).encode(),
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr.decode()
    expected = hashlib.sha256(serialized.encode()).hexdigest()
    assert result.stdout.decode() == expected


def test_claim_identity_is_scope_deterministic(artifact) -> None:
    first = PaperEvidenceGraph.model_validate(raw_graph_payload(artifact), strict=True)
    payload = raw_graph_payload(artifact)
    payload["scopes"][0]["condition"] = "field"
    second = PaperEvidenceGraph.model_validate(payload, strict=True)

    assert first.claims[0].proposition_key == second.claims[0].proposition_key
    assert first.claims[0].scoped_claim_id != second.claims[0].scoped_claim_id


def test_scope_entity_duplicate_is_typed_and_value_free(artifact) -> None:
    payload = raw_graph_payload(artifact)
    payload["scopes"][0]["entities"] = ["secret sample", "secret sample"]

    with pytest.raises(ValidationError) as caught:
        PaperEvidenceGraph.model_validate(payload, strict=True)

    errors = caught.value.errors(include_url=False, include_input=False)
    assert errors == [
        {
            "type": "scope_entity_duplicate",
            "loc": ("scopes", 0, "entities"),
            "msg": "scope entities must be unique",
        }
    ]
    assert "ctx" not in errors[0]
    assert "input" not in errors[0]


def test_rejects_direct_observation_to_claim_shortcut(artifact) -> None:
    payload = raw_graph_payload(artifact)
    payload["edges"][2] = {
        "id": "edge.invalid.1",
        "source_id": "obs.local.1",
        "target_id": "claim.local.1",
        "relation": "supports",
        "rationale": None,
        "confidence": None,
    }
    with pytest.raises(ContractValidationError) as caught:
        PaperEvidenceGraph.model_validate(payload, strict=True)
    assert caught.value.violations == (
        ContractViolation(
            ContractIssue.GRAPH_EDGE_ENDPOINT_INVALID, ("edges", 2, "relation")
        ),
    )


def test_accepts_shared_reasoning_across_terminal_claims(artifact) -> None:
    payload = raw_graph_payload(artifact)
    second_claim = {
        **payload["claims"][0],
        "id": "claim.local.2",
        "statement": "A distinct independently testable conclusion.",
        "proposition_key": None,
        "scoped_claim_id": None,
    }
    payload["claims"].append(second_claim)
    payload["edges"].append(
        {
            "id": "edge.shared.reasoning",
            "source_id": "reason.local.1",
            "target_id": "claim.local.2",
            "relation": "supports",
            "rationale": None,
            "confidence": None,
        }
    )

    graph = PaperEvidenceGraph.model_validate(payload, strict=True)

    assert len(graph.claims) == 2


def test_accepts_multilevel_reasoning_dag_and_rejects_dangling_reasoning(
    artifact, parser_metadata
) -> None:
    payload = raw_graph_payload(artifact)
    payload["observations"].append(
        {
            **payload["observations"][0],
            "id": "obs.local.2",
            "statement": "The untreated comparison remained below the treatment.",
            "raw_value": None,
            "unit": None,
        }
    )
    payload["reasoning"].append(
        {
            **payload["reasoning"][0],
            "id": "reason.local.2",
            "statement": (
                "The measurement and comparison jointly establish a treatment effect."
            ),
        }
    )
    payload["edges"] = [
        edge for edge in payload["edges"] if edge["id"] != "edge.support.1"
    ]
    payload["edges"].extend(
        [
            {
                "id": "edge.grounds.2",
                "source_id": "anchor.text.1",
                "target_id": "obs.local.2",
                "relation": "grounds",
                "rationale": None,
                "confidence": None,
            },
            {
                "id": "edge.premise.2",
                "source_id": "obs.local.2",
                "target_id": "reason.local.2",
                "relation": "premise_for",
                "rationale": None,
                "confidence": None,
            },
            {
                "id": "edge.reasoning.backbone",
                "source_id": "reason.local.1",
                "target_id": "reason.local.2",
                "relation": "premise_for",
                "rationale": None,
                "confidence": None,
            },
            {
                "id": "edge.support.synthesis",
                "source_id": "reason.local.2",
                "target_id": "claim.local.1",
                "relation": "supports",
                "rationale": None,
                "confidence": None,
            },
        ]
    )

    graph = PaperEvidenceGraph.model_validate(payload, strict=True)
    canonical = to_canonical_graph(
        graph,
        artifact,
        parser=parser_metadata,
        created_at="2026-08-16T00:00:00Z",
    )
    assert any(
        edge["relation"] == "premise_for"
        and edge["source_id"] == "reason.local.1"
        and edge["target_id"] == "reason.local.2"
        for edge in canonical["edges"]
    )
    assert any(
        edge["relation"] == "infers" and edge["source_id"] == "reason.local.2"
        for edge in canonical["edges"]
    )

    payload["edges"] = [
        edge for edge in payload["edges"] if edge["id"] != "edge.support.synthesis"
    ]
    with pytest.raises(ContractValidationError) as caught:
        PaperEvidenceGraph.model_validate(payload, strict=True)
    # Both reasoning steps now end without a Claim, and the Claim has lost its
    # only conclusion; each is reported at its own location.
    assert caught.value.violations == (
        ContractViolation(ContractIssue.GRAPH_REASONING_UNCONCLUDED, ("reasoning", 0)),
        ContractViolation(ContractIssue.GRAPH_REASONING_UNCONCLUDED, ("reasoning", 1)),
        ContractViolation(ContractIssue.GRAPH_CLAIM_CONCLUSION_MISSING, ("claims", 0)),
    )


def test_rejects_claim_as_upstream_reasoning_premise(artifact) -> None:
    payload = raw_graph_payload(artifact)
    payload["reasoning"].append(
        {
            **payload["reasoning"][0],
            "id": "reason.local.2",
        }
    )
    payload["edges"].append(
        {
            "id": "edge.claim.premise",
            "source_id": "claim.local.1",
            "target_id": "reason.local.2",
            "relation": "premise_for",
            "rationale": None,
            "confidence": None,
        }
    )

    with pytest.raises(ContractValidationError) as caught:
        PaperEvidenceGraph.model_validate(payload, strict=True)
    assert caught.value.violations == (
        ContractViolation(
            ContractIssue.GRAPH_EDGE_ENDPOINT_INVALID,
            ("edges", len(payload["edges"]) - 1, "relation"),
        ),
    )


def _add_reasoning_cycle(payload: dict) -> None:
    payload["reasoning"].append({**payload["reasoning"][0], "id": "reason.local.2"})
    for index, (source, target) in enumerate(
        (("reason.local.1", "reason.local.2"), ("reason.local.2", "reason.local.1"))
    ):
        payload["edges"].append(
            {
                "id": f"edge.cycle.{index}",
                "source_id": source,
                "target_id": target,
                "relation": "premise_for",
                "rationale": None,
                "confidence": None,
            }
        )


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        pytest.param(
            lambda payload: payload["anchors"].append(deepcopy(payload["anchors"][0])),
            ((ContractIssue.GRAPH_ID_DUPLICATE, ("anchors", 2, "id")),),
            id="duplicate-id",
        ),
        pytest.param(
            lambda payload: payload["visual_evidence"][0].update({"id": "obs.local.1"}),
            ((ContractIssue.GRAPH_ID_DUPLICATE, ("visual_evidence", 0, "id")),),
            id="cross-collection-id",
        ),
        pytest.param(
            lambda payload: (
                payload["claims"][0].update({"scope_id": "scope.missing"}),
                payload["reasoning"][0].update({"anchor_ids": ["anchor.missing"]}),
            ),
            (
                (ContractIssue.GRAPH_REFERENCE_UNKNOWN, ("claims", 0, "scope_id")),
                (ContractIssue.GRAPH_REFERENCE_UNKNOWN, ("reasoning", 0, "anchor_ids")),
            ),
            id="unknown-references-reported-together",
        ),
        pytest.param(
            lambda payload: payload["observations"][0].update(
                {"anchor_ids": ["anchor.text.1", "anchor.text.1"]}
            ),
            (
                (
                    ContractIssue.GRAPH_REFERENCE_DUPLICATE,
                    ("observations", 0, "anchor_ids"),
                ),
            ),
            id="duplicate-reference",
        ),
        pytest.param(
            lambda payload: payload["claims"][0].update(
                {"proposition_key": "proposition:sha256:" + "0" * 64}
            ),
            (
                (
                    ContractIssue.GRAPH_IDENTITY_MISMATCH,
                    ("claims", 0, "proposition_key"),
                ),
            ),
            id="identity",
        ),
        pytest.param(
            lambda payload: payload["edges"][3].update({"target_id": "claim.missing"}),
            ((ContractIssue.GRAPH_REFERENCE_UNKNOWN, ("edges", 3, "target_id")),),
            id="dangling-edge",
        ),
        pytest.param(
            lambda payload: payload["edges"].append(
                {**payload["edges"][2], "id": "edge.premise.copy"}
            ),
            ((ContractIssue.GRAPH_EDGE_DUPLICATE, ("edges", 4)),),
            id="duplicate-edge",
        ),
        pytest.param(
            lambda payload: payload["edges"].pop(2),
            ((ContractIssue.GRAPH_REASONING_PREMISE_MISSING, ("reasoning", 0)),),
            id="missing-premise",
        ),
        pytest.param(
            # The lowest-index edge ON the cycle, not the downstream support edge.
            _add_reasoning_cycle,
            ((ContractIssue.GRAPH_DERIVATION_CYCLE, ("edges", 4)),),
            id="cycle",
        ),
    ],
)
def test_structural_failures_name_their_collection_index_and_field(
    artifact, mutate, expected
) -> None:
    payload = raw_graph_payload(artifact)
    mutate(payload)
    with pytest.raises(ContractValidationError) as caught:
        PaperEvidenceGraph.model_validate(payload, strict=True)
    assert caught.value.violations == tuple(
        ContractViolation(issue, loc) for issue, loc in expected
    )


def test_derivation_cycle_check_is_iterative_on_deep_chains() -> None:
    depth = 50_000
    chain = [(index, f"n{index}", f"n{index + 1}") for index in range(depth)]
    assert _derivation_cycle_edge(chain) is None
    assert _derivation_cycle_edge([*chain, (depth, f"n{depth}", "n0")]) == 0



def test_canonical_reports_claim_comparison_scope_issue(
    artifact, parser_metadata
) -> None:
    payload = raw_graph_payload(artifact)
    payload["scopes"].append(
        {
            **payload["scopes"][0],
            "id": "scope.local.2",
            "location": "independent field site",
            "entities": ["different treatment", "different amount"],
        }
    )
    payload["claims"].append(
        {
            **payload["claims"][0],
            "id": "claim.local.2",
            "statement": "A distinct field result was observed.",
            "scope_id": "scope.local.2",
            "proposition_key": None,
            "scoped_claim_id": None,
        }
    )
    payload["edges"].append(
        {
            "id": "edge.reasoning.second-claim",
            "source_id": "reason.local.1",
            "target_id": "claim.local.2",
            "relation": "supports",
            "rationale": None,
            "confidence": None,
        }
    )
    payload["edges"].append(
        {
            "id": "edge.claim.comparison",
            "source_id": "claim.local.1",
            "target_id": "claim.local.2",
            "relation": "supports",
            "rationale": None,
            "confidence": None,
        }
    )
    graph = PaperEvidenceGraph.model_validate(payload, strict=True)

    with pytest.raises(ContractValidationError) as caught:
        to_canonical_graph(
            graph,
            artifact,
            parser=parser_metadata,
            created_at="2026-08-16T00:00:00Z",
        )
    assert caught.value.contract_issue is (
        ContractIssue.CANONICAL_CLAIM_COMPARISON_SCOPE_INVALID
    )


def test_extractor_anchor_region_rejects_pixel_coordinates(artifact) -> None:
    payload = raw_graph_payload(artifact)
    payload["anchors"][0]["locator"]["region"]["unit"] = "pixel"

    with pytest.raises(ValidationError, match="normalized"):
        PaperEvidenceGraph.model_validate(payload, strict=True)


def test_extractor_locator_accepts_omitted_nullable_coordinates(artifact) -> None:
    payload = raw_graph_payload(artifact)
    locator = payload["anchors"][0]["locator"]
    quote = locator["quote"]
    payload["anchors"][0]["locator"] = {"page": 1, "quote": quote}

    graph = PaperEvidenceGraph.model_validate(payload, strict=True)
    parsed = graph.anchors[0].locator

    assert parsed.page == 1
    assert parsed.quote == quote
    assert parsed.region is None
    assert parsed.column_names == []


def test_pipeline_top_level_collections_are_uncapped_but_visuals_stay_bounded() -> None:
    extraction_schema = PaperEvidenceGraph.model_json_schema(mode="validation")
    verification_schema = VerificationReport.model_json_schema(mode="validation")

    assert extraction_schema["additionalProperties"] is False
    assert verification_schema["additionalProperties"] is False
    for key in (
        "claims",
        "observations",
        "reasoning",
        "assumptions",
        "anchors",
        "visual_evidence",
        "scopes",
        "edges",
    ):
        assert "minItems" not in extraction_schema["properties"][key]
        assert "maxItems" not in extraction_schema["properties"][key]
    visual_schema = extraction_schema["$defs"]["StructuredVisualEvidence"]
    series_schema = extraction_schema["$defs"]["VisualSeries"]
    assert extraction_schema["$defs"]["Region"]["properties"]["unit"] == {
        "const": "normalized",
        "title": "Unit",
        "type": "string",
    }
    assert {
        key: visual_schema["properties"][key]["maxItems"]
        for key in ("axes", "series", "cells", "features")
    } == {
        "axes": MAX_VISUAL_AXES,
        "series": MAX_VISUAL_SERIES,
        "cells": MAX_TABLE_CELLS,
        "features": MAX_VISUAL_FEATURES,
    }
    assert (
        series_schema["properties"]["points"]["maxItems"]
        == MAX_VISUAL_POINTS_PER_SERIES
    )
    assert EXTRACTION_PROMPT_VERSION == "compiler-extractor/1.0.0"
    assert VERIFICATION_PROMPT_VERSION == "compiler-verifier/1.0.0"
    prompt_words = " ".join(EXTRACTION_SYSTEM_PROMPT.split())
    assert "Target 1-5" not in prompt_words
    assert "absolute maximum" not in prompt_words
    assert "Prefer fewer well-supported nodes" not in prompt_words
    assert "there is no target, minimum, preferred range, or content quota" in (
        prompt_words
    )
    assert "directed acyclic graph" in prompt_words
    assert "Claims are the terminal, paper-level conclusions" in prompt_words
    assert "This is argument reconstruction, not sentence extraction" in prompt_words
    assert "one quote -> one Observation" in prompt_words
    assert "Intermediate Reasoning may feed later Reasoning" in prompt_words
    assert "Extract every materially important, independently truth-evaluable" not in (
        prompt_words
    )
    assert "never omit a qualifying conclusion" not in prompt_words

    example = PaperEvidenceGraph.model_validate(
        json.loads(EXTRACTION_JSON_EXAMPLE),
        strict=True,
    )
    assert len(example.claims) == 1
    assert len(example.observations) == 2
    assert len(example.reasoning) == 2
    assert any(
        edge.relation == "premise_for"
        and edge.source_id == "reason.comparison.1"
        and edge.target_id == "reason.synthesis.1"
        for edge in example.edges
    )
    verifier_schema = VerificationReport.model_json_schema()
    assert verifier_schema["properties"]["verifier_model"] == {
        "enum": ["gpt-6-luna", "gpt-5.6-luna", "deepseek-v4-flash"],
        "title": "Verifier Model",
        "type": "string",
    }
    assert "minItems" not in verifier_schema["properties"]["assessments"]
    assert "maxItems" not in verifier_schema["properties"]["assessments"]


def test_verification_rejects_known_node_outside_claim_evidence_path(
    artifact,
) -> None:
    payload = raw_graph_payload(artifact)
    payload["edges"] = [
        edge for edge in payload["edges"] if edge["id"] != "edge.grounds.visual.1"
    ]
    graph = PaperEvidenceGraph.model_validate(payload, strict=True)
    report = VerificationReport.model_validate(
        {
            "schema_version": "1.0",
            "paper_id": graph.paper_id,
            "graph_sha256": "a" * 64,
            "verifier_model": "gpt-5.6-luna",
            "assessments": [
                {
                    "id": "assessment.local.1",
                    "claim_id": graph.claims[0].id,
                    "scoped_claim_id": graph.claims[0].scoped_claim_id,
                    "verdict": "supported",
                    "confidence": 0.8,
                    "evidence_ids": ["visual.table.1"],
                    "rationale": "The cited table supports the claim.",
                    "limitations": [],
                }
            ],
            "warnings": [],
        },
        strict=True,
    )

    with pytest.raises(ContractValidationError) as caught:
        report.validate_against(graph)
    assert (
        caught.value.contract_issue is ContractIssue.VERIFICATION_EVIDENCE_OUTSIDE_PATH
    )


def test_verification_missing_claim_reports_safe_original_index(
    raw_graph: PaperEvidenceGraph,
) -> None:
    report = VerificationReport.model_validate(
        {
            "schema_version": "1.0",
            "paper_id": raw_graph.paper_id,
            "graph_sha256": "a" * 64,
            "verifier_model": "gpt-5.6-luna",
            "assessments": [],
            "warnings": [],
        },
        strict=True,
    )

    with pytest.raises(ContractValidationError) as caught:
        report.validate_against(raw_graph)

    assert caught.value.contract_issue is ContractIssue.VERIFICATION_CLAIM_UNASSESSED
    assert caught.value.loc == ("assessments", 0, "claim_id")
    assert raw_graph.claims[0].id not in json.dumps(list(caught.value.loc))


def test_verification_requires_non_circular_direct_scientific_evidence(
    raw_graph: PaperEvidenceGraph,
) -> None:
    claim = raw_graph.claims[0]

    def report(verdict: str, evidence_ids: list[str]) -> VerificationReport:
        return VerificationReport.model_validate(
            {
                "schema_version": "1.0",
                "paper_id": raw_graph.paper_id,
                "graph_sha256": "a" * 64,
                "verifier_model": "gpt-5.6-luna",
                "assessments": [
                    {
                        "id": "assessment.local.evidence-policy",
                        "claim_id": claim.id,
                        "scoped_claim_id": claim.scoped_claim_id,
                        "verdict": verdict,
                        "confidence": 0.8,
                        "evidence_ids": evidence_ids,
                        "rationale": "The cited path determines this verdict.",
                        "limitations": [],
                    }
                ],
                "warnings": [],
            },
            strict=True,
        )

    with pytest.raises(ValidationError, match="requires evidence_ids"):
        report("supported", [])

    # A lack of citable evidence is itself representable, but only by the one
    # verdict whose semantics explicitly permit an empty evidence list.
    report("insufficient_evidence", []).validate_against(raw_graph)

    for evidence_ids in ([claim.id], ["reason.local.1"]):
        rejected = report("supported", evidence_ids)
        with pytest.raises(ContractValidationError) as caught:
            rejected.validate_against(raw_graph)
        assert (
            caught.value.contract_issue
            is ContractIssue.VERIFICATION_EVIDENCE_OUTSIDE_PATH
        )

    report("supported", ["obs.local.1", "reason.local.1"]).validate_against(raw_graph)


def test_verification_rationale_wording_is_advisory_but_evidence_path_is_strict(
    artifact,
) -> None:
    def report(
        graph: PaperEvidenceGraph,
        evidence_ids: list[str],
        rationale: str,
        *,
        limitations: list[str] | None = None,
        warnings: list[str] | None = None,
    ) -> VerificationReport:
        return VerificationReport.model_validate(
            {
                "schema_version": "1.0",
                "paper_id": graph.paper_id,
                "graph_sha256": "a" * 64,
                "verifier_model": "gpt-5.6-luna",
                "assessments": [
                    {
                        "id": "assessment.local.numeric",
                        "claim_id": graph.claims[0].id,
                        "scoped_claim_id": graph.claims[0].scoped_claim_id,
                        "verdict": "qualified",
                        "confidence": 0.8,
                        "evidence_ids": evidence_ids,
                        "rationale": rationale,
                        "limitations": limitations or [],
                    }
                ],
                "warnings": warnings or [],
            },
            strict=True,
        )

    no_cells_payload = raw_graph_payload(artifact)
    no_cells_payload["visual_evidence"] = []
    no_cells_payload["edges"] = [
        edge
        for edge in no_cells_payload["edges"]
        if edge["id"] != "edge.grounds.visual.1"
    ]
    no_cells = PaperEvidenceGraph.model_validate(no_cells_payload, strict=True)

    report(
        no_cells,
        ["obs.local.1", "reason.local.1"],
        "The compared values are 8.59 and 8.52.",
    ).validate_against(no_cells)
    report(
        no_cells,
        ["obs.local.1", "reason.local.1"],
        "The cited result is greater than 5 kg.",
    ).validate_against(no_cells)

    off_path = report(
        no_cells,
        ["anchor.table.1"],
        "The verifier cites a valid-looking but unrelated node.",
    )
    with pytest.raises(ContractValidationError) as caught:
        off_path.validate_against(no_cells)
    assert (
        caught.value.contract_issue
        is ContractIssue.VERIFICATION_EVIDENCE_OUTSIDE_PATH
    )


def test_canonical_observation_numeric_is_parsed_from_the_raw_value(
    artifact,
    parser_metadata,
) -> None:
    coded_payload = raw_graph_payload(artifact)
    coded_payload["visual_evidence"] = []
    coded_payload["edges"] = [
        edge for edge in coded_payload["edges"] if edge["id"] != "edge.grounds.visual.1"
    ]
    coded_payload["observations"][0].update(
        {
            "statement": "The source reports a coded result.",
            "raw_value": "5widgets",
            "unit": None,
        }
    )
    coded_payload["anchors"][0]["locator"]["quote"] = (
        "The source reports a coded result of 5widgets."
    )
    coded = PaperEvidenceGraph.model_validate(coded_payload, strict=True)
    coded_canonical = to_canonical_graph(
        coded,
        artifact,
        parser=parser_metadata,
        created_at="2026-08-17T00:00:00Z",
    )
    # ``numeric`` is a lossy canonical convenience parsed from the lossless
    # opaque raw value; it is not a second scientific assertion.
    assert coded_canonical["observations"][0]["value"]["numeric"] == 5

    qualitative_payload = json.loads(json.dumps(coded_payload))
    qualitative_payload["observations"][0]["raw_value"] = "reported qualitatively"
    qualitative = PaperEvidenceGraph.model_validate(qualitative_payload, strict=True)
    qualitative_canonical = to_canonical_graph(
        qualitative,
        artifact,
        parser=parser_metadata,
        created_at="2026-08-17T00:00:00Z",
    )
    assert qualitative_canonical["observations"][0]["value"]["numeric"] is None


@pytest.mark.parametrize(
    ("relation", "canonical_relation"),
    (
        ("supports", "infers"),
        ("contradicts", "contradicts"),
        ("qualifies", "qualifies"),
    ),
)
def test_raw_reasoning_relations_keep_their_stance_in_canonical_form(
    artifact,
    parser_metadata,
    relation: str,
    canonical_relation: str,
) -> None:
    payload = raw_graph_payload(artifact)
    payload["claims"][0].update(
        {
            "statement": "The reported treatment result was 5 mg.",
            "anchor_ids": [],
            "proposition_key": None,
            "scoped_claim_id": None,
        }
    )
    payload["edges"][-1]["relation"] = relation
    raw = PaperEvidenceGraph.model_validate(payload, strict=True)

    canonical = to_canonical_graph(
        raw,
        artifact,
        parser=parser_metadata,
        created_at="2026-08-17T00:00:00Z",
    )

    conclusion = next(
        edge for edge in canonical["edges"] if edge["id"] == "edge.support.1"
    )
    # A refutation or limitation is never stored as an inference; supporting
    # conclusions keep the legacy ``infers`` relation (unchanged hashes).
    assert conclusion["relation"] == canonical_relation
    assert conclusion["scope_overlap"] is None
    assert _typescript_graph_hash(canonical) == graph_sha256(canonical)


def test_reasoning_may_both_support_and_qualify_one_claim(
    artifact,
    parser_metadata,
) -> None:
    payload = raw_graph_payload(artifact)
    payload["claims"][0].update(
        {
            "statement": "The reported treatment result was 5 mg.",
            "anchor_ids": [],
            "proposition_key": None,
            "scoped_claim_id": None,
        }
    )
    conclusion = deepcopy(payload["edges"][-1])
    payload["edges"].append(
        conclusion | {"id": "edge.qualify.1", "relation": "qualifies"}
    )
    raw = PaperEvidenceGraph.model_validate(payload, strict=True)

    canonical = to_canonical_graph(
        raw,
        artifact,
        parser=parser_metadata,
        created_at="2026-08-17T00:00:00Z",
    )

    relations = sorted(
        edge["relation"]
        for edge in canonical["edges"]
        if edge["id"] in {"edge.support.1", "edge.qualify.1"}
    )
    assert relations == ["infers", "qualifies"]
    assert _typescript_graph_hash(canonical) == graph_sha256(canonical)


def test_each_observation_owns_exactly_one_reasoning_path(artifact) -> None:
    shared = raw_graph_payload(artifact)
    shared["observations"].append(
        {
            **shared["observations"][0],
            "id": "obs.local.2",
            "statement": "A second narrow observation reached 5 mg.",
        }
    )
    shared["edges"].extend(
        [
            {
                "id": "edge.grounds.second.1",
                "source_id": "anchor.text.1",
                "target_id": "obs.local.2",
                "relation": "grounds",
                "rationale": None,
                "confidence": None,
            },
            {
                "id": "edge.premise.second.1",
                "source_id": "obs.local.2",
                "target_id": "reason.local.1",
                "relation": "premise_for",
                "rationale": None,
                "confidence": None,
            },
        ]
    )
    accepted = PaperEvidenceGraph.model_validate(shared, strict=True)
    assert accepted.observations[0].anchor_ids == accepted.observations[1].anchor_ids
    accepted.validate_observation_path_ownership()

    split = raw_graph_payload(artifact)
    split["reasoning"].append(
        {
            **split["reasoning"][0],
            "id": "reason.local.2",
            "statement": "An independent reasoning path uses the same observation.",
        }
    )
    split["edges"].extend(
        [
            {
                "id": "edge.premise.duplicate.1",
                "source_id": "obs.local.1",
                "target_id": "reason.local.2",
                "relation": "premise_for",
                "rationale": None,
                "confidence": None,
            },
            {
                "id": "edge.support.second.1",
                "source_id": "reason.local.2",
                "target_id": "claim.local.1",
                "relation": "supports",
                "rationale": None,
                "confidence": None,
            },
        ]
    )
    rejected = PaperEvidenceGraph.model_validate(split, strict=True)
    with pytest.raises(ContractValidationError) as caught:
        rejected.validate_observation_path_ownership()
    assert caught.value.contract_issue is ContractIssue.OBSERVATION_PATH_OWNERSHIP


def test_python_envelope_graph_and_hash_are_accepted_by_typescript(
    import_envelope,
) -> None:
    script = """
import { hashEvidenceGraph, parseEvidenceGraph } from './lib/evidence-graph/index.ts';
const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const envelope = JSON.parse(Buffer.concat(chunks).toString('utf8'));
const graph = await parseEvidenceGraph(envelope.graph);
const hash = await hashEvidenceGraph(graph);
process.stdout.write(JSON.stringify({hash, visuals: graph.visual_evidence.length}));
"""
    result = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
        cwd=REPOSITORY,
        input=json.dumps(
            import_envelope.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
        ).encode(),
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
    actual = json.loads(result.stdout)
    assert actual["hash"] == graph_sha256(import_envelope.graph)
    assert actual["visuals"] == 1


def test_canonical_preserves_table_raw_value(
    artifact, raw_graph, parser_metadata
) -> None:
    raw_graph.visual_evidence[0].cells[0].raw_value = "  5 mg*  "
    graph = to_canonical_graph(
        raw_graph,
        artifact,
        parser=parser_metadata,
        created_at="2026-08-15T00:00:00Z",
    )
    assert graph["visual_evidence"][0]["cells"][0]["raw_value"] == "  5 mg*  "


def test_integral_table_numeric_value_hash_is_accepted_by_typescript(
    artifact, parser_metadata
) -> None:
    payload = raw_graph_payload(artifact)
    payload["visual_evidence"][0]["cells"][0].update(
        raw_value="42.0 mg",
        numeric_value=42.0,
    )
    raw_graph = PaperEvidenceGraph.model_validate(payload, strict=True)
    graph = to_canonical_graph(
        raw_graph,
        artifact,
        parser=parser_metadata,
        created_at="2026-08-15T00:00:00Z",
    )

    numeric_value = graph["visual_evidence"][0]["cells"][0]["numeric_value"]
    assert numeric_value == 42
    assert isinstance(numeric_value, int)
    assert graph["visual_evidence"][0]["cells"][0]["raw_value"] == "42.0 mg"
    assert _typescript_graph_hash(graph) == graph_sha256(graph)


def test_integral_figure_numbers_hash_is_accepted_by_typescript(
    artifact, parser_metadata
) -> None:
    payload = raw_graph_payload(artifact)
    locator = payload["anchors"][1]["locator"]
    payload["anchors"][1]["anchor_type"] = "figure"
    locator.update(figure="Figure 1", table=None, column_names=[])
    payload["visual_evidence"][0] = {
        "id": "visual.figure.1",
        "anchor_id": "anchor.table.1",
        "visual_kind": "figure",
        "axes": [
            {
                "id": "axis.x.1",
                "direction": "x",
                "label": "Input",
                "unit": None,
                "scale": "linear",
            },
            {
                "id": "axis.y.1",
                "direction": "y",
                "label": "Output",
                "unit": None,
                "scale": "linear",
            },
        ],
        "series": [
            {
                "id": "series.local.1",
                "label": "Measurements",
                "x_axis_id": "axis.x.1",
                "y_axis_id": "axis.y.1",
                "points": [
                    {"x": 42.0, "y": 1e-5, "uncertainty": 1e-6},
                    {"x": 1e-7, "y": 1e20, "uncertainty": -0.0},
                    {"x": 1e21, "y": 7.0, "uncertainty": None},
                ],
            }
        ],
        "cells": [],
        "features": [
            {
                "kind": "peak",
                "description": "The series has a measured peak.",
                "series_id": "series.local.1",
                "value": 42.0,
                "unit": None,
            }
        ],
    }
    payload["edges"][1]["source_id"] = "visual.figure.1"
    raw_graph = PaperEvidenceGraph.model_validate(payload, strict=True)
    graph = to_canonical_graph(
        raw_graph,
        artifact,
        parser=parser_metadata,
        created_at="2026-08-15T00:00:00Z",
    )

    visual = graph["visual_evidence"][0]
    points = visual["series"][0]["points"]
    assert isinstance(points[0]["x"], int)
    assert isinstance(points[1]["y"], int)
    assert isinstance(points[1]["uncertainty"], int)
    assert isinstance(points[2]["x"], int)
    assert isinstance(points[2]["y"], int)
    assert isinstance(visual["features"][0]["value"], int)
    assert _typescript_graph_hash(graph) == graph_sha256(graph)


def test_python_verification_report_is_accepted_by_typescript(
    import_envelope, raw_graph
) -> None:
    envelope = import_envelope.model_dump(mode="json", exclude_none=True)
    envelope["verification"] = {
        "run_id": "verification:sha256:" + "b" * 64,
        "model": "gpt-5.6-luna",
        "prompt_version": "compiler-verifier/1.0.0",
        "report": {
            "schema_version": "1.0",
            "paper_id": raw_graph.paper_id,
            "graph_sha256": graph_sha256(import_envelope.graph),
            "verifier_model": "gpt-5.6-luna",
            "assessments": [
                {
                    "id": "assessment.local.1",
                    "claim_id": "claim.local.1",
                    "scoped_claim_id": raw_graph.claims[0].scoped_claim_id,
                    "verdict": "qualified",
                    "confidence": 0.7,
                    "evidence_ids": ["obs.local.1", "reason.local.1"],
                    "rationale": "Evidence is limited to one experiment.",
                    "limitations": ["Single experiment"],
                }
            ],
            "warnings": ["Original visual values require review."],
        },
    }
    script = """
import {
  attachCompilerVerificationReport,
  hashEvidenceGraph,
  parseEvidenceGraph,
  validateCompilerVerificationReport,
} from './lib/evidence-graph/index.ts';
const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const envelope = JSON.parse(Buffer.concat(chunks).toString('utf8'));
const graph = await parseEvidenceGraph(envelope.graph);
const report = validateCompilerVerificationReport(envelope.verification.report);
if (report.graph_sha256 !== await hashEvidenceGraph(graph)) throw new Error('hash');
const attached = await attachCompilerVerificationReport(
  graph,
  report,
  {created_at: '2026-08-15T00:00:00Z'},
);
process.stdout.write(JSON.stringify({
  frozen: graph.assessments.length,
  attached: attached.assessments.length,
}));
"""
    result = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", script],
        cwd=REPOSITORY,
        input=json.dumps(envelope, ensure_ascii=False).encode(),
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert json.loads(result.stdout) == {"frozen": 0, "attached": 1}
