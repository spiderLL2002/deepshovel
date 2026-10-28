from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from compiler_service.api import create_app
from compiler_service.claim_comparison import (
    ClaimComparisonAnalyzer,
    ClaimComparisonAssessment,
    ClaimComparisonAssessmentBatch,
    ClaimComparisonDecision,
    ClaimComparisonDecisionDraft,
    ClaimComparisonDecisionDraftBatch,
    ClaimComparisonRequest,
    ClaimNormalizationDraft,
    ClaimNormalizationDraftBatch,
    ClaimNormalizationRequest,
    ComparisonScope,
    _compact_model_request_payload,
    _comparisons_sha256,
    _extraction_schema_for_request,
    _validate_assessment_transitions,
    _validate_decision_drafts,
    _validate_normalization,
    _verification_request_payload,
)
from compiler_service.config import Settings
from compiler_service.errors import ModelAttemptDiagnostic
from compiler_service.graph_contract import canonical_hash_json
from compiler_service.mineru import FakeMineruBackend
from compiler_service.model_client import Completion, _retry_feedback


def _scope() -> dict[str, Any]:
    return {
        "location": [],
        "time": None,
        "formation": [],
        "sample_type": ["adults"],
        "method": ["randomized trial"],
        "condition": ["standard dose"],
        "measurement": ["outcome score"],
        "uncertainty": None,
        "entities": ["treatment", "outcome"],
    }


def comparison_payload(*, raw_candidate: bool = True) -> dict[str, Any]:
    candidate_entities = [
        {
            "id": "candidate.observation.1",
            "kind": "observation" if raw_candidate else "reasoning",
            "text": "The candidate paper reports a higher outcome after treatment.",
            "anchor_ids": ["candidate.anchor.1"],
            "root_claim_ids": ["candidate.claim.1"],
        },
        {
            "id": "candidate.assessment.1",
            "kind": "assessment_summary",
            "text": "The paper-level verifier marked its own Claim supported.",
            "anchor_ids": [],
            "root_claim_ids": ["candidate.claim.1"],
        },
    ]
    return {
        "schema_version": "claim-comparison/1.0",
        "project_id": "project.claim-comparison.test",
        "pairs": [
            {
                "id": "pair.1",
                "query": {
                    "claim_id": "query.claim.1",
                    "canonical_claim": "The treatment increases the outcome.",
                    "claim_type": "causal",
                    "polarity": "affirmed",
                    "modality": "asserted",
                    "scope": _scope(),
                    "source_snapshot_id": None,
                    "source_paper_id": None,
                    "source_paper_family_id": None,
                    "evidence_entities": [],
                },
                "candidate": {
                    "claim_id": "candidate.claim.1",
                    "canonical_claim": "The treatment increases the outcome.",
                    "claim_type": "causal",
                    "polarity": "affirmed",
                    "modality": "asserted",
                    "scope": _scope(),
                    "source_snapshot_id": "snapshot.candidate.1",
                    "source_paper_id": "paper.candidate.1",
                    "source_paper_family_id": "family.candidate.1",
                    "evidence_entities": candidate_entities,
                },
            }
        ],
    }


class ScriptedComparisonModel:
    def __init__(self, *, empty_normalization: bool = False) -> None:
        self.empty_normalization = empty_normalization
        self.payloads: list[dict[str, Any]] = []
        self.system_prompts: list[str] = []

    def with_job_control(self, control: Any) -> ScriptedComparisonModel:
        del control
        return self

    def complete(
        self,
        *,
        system_prompt: str,
        user_content: str,
        schema: type[Any],
        validate: Any,
        **kwargs: Any,
    ) -> Completion[Any]:
        del kwargs
        payload = json.loads(user_content)
        self.payloads.append(payload)
        self.system_prompts.append(system_prompt)
        if schema is ClaimNormalizationDraftBatch:
            value = ClaimNormalizationDraftBatch.model_validate(
                {
                    "status": (
                        "needs_user_definition"
                        if self.empty_normalization
                        else "ready"
                    ),
                    "clarification": (
                        "State a checkable scientific proposition."
                        if self.empty_normalization
                        else None
                    ),
                    "claims": []
                    if self.empty_normalization
                    else [
                        {
                            "origin_span": "Does the treatment affect the outcome?",
                            "canonical_claim": (
                                "The treatment may affect the outcome."
                            ),
                            "claim_type": "causal",
                            "polarity": "uncertain",
                            "modality": "hypothesis",
                            "scope": _scope(),
                            "status": "ready",
                            "clarification": None,
                        }
                    ],
                },
                strict=True,
            )
            content_sha256 = "a" * 64
        elif schema is ClaimComparisonDecisionDraftBatch:
            value = ClaimComparisonDecisionDraftBatch.model_validate(
                {
                    "comparisons": [
                        {
                            "pair_id": pair["id"],
                            "relation": "supports",
                            "direction": "candidate_to_query",
                            "stance": "supportive",
                            "scope_alignment": "equivalent",
                            "directness": "direct",
                            "query_evidence_ids": [],
                            "candidate_evidence_ids": [
                                "candidate.observation.1"
                            ],
                            "rationale": (
                                "The candidate reports a directly comparable outcome."
                            ),
                            "limitations": [
                                "Only the supplied evidence path was evaluated."
                            ],
                            "confidence": 0.9,
                        }
                        for pair in payload["request"]["pairs"]
                    ]
                },
                strict=True,
            )
            content_sha256 = "b" * 64
        else:
            assert schema is ClaimComparisonAssessmentBatch
            value = ClaimComparisonAssessmentBatch.model_validate(
                {
                    "extraction_sha256": payload["extraction_sha256"],
                    "assessments": [
                        {
                            "comparison_id": frozen["id"],
                            "pair_id": frozen["pair_id"],
                            "verdict": "maintained",
                            "relation": "supports",
                            "direction": "candidate_to_query",
                            "stance": "supportive",
                            "scope_alignment": "equivalent",
                            "directness": "direct",
                            "query_evidence_ids": [],
                            "candidate_evidence_ids": [
                                "candidate.observation.1"
                            ],
                            "rationale": (
                                "The selected raw evidence sustains the comparison."
                            ),
                            "limitations": [
                                "No evidence outside the frozen path was considered."
                            ],
                            "confidence": 0.8,
                        }
                        for frozen in payload["frozen_comparisons"]
                    ],
                },
                strict=True,
            )
            content_sha256 = "c" * 64
        validate(value)
        return Completion(
            value=value,
            attempts=1,
            content_sha256=content_sha256,
            diagnostics=(),
        )


def test_normalizer_preserves_question_as_an_uncertain_hypothesis() -> None:
    gpt = ScriptedComparisonModel()
    request = ClaimNormalizationRequest.model_validate(
        {
            "schema_version": "claim-comparison/1.0",
            "project_id": "project.claim-comparison.test",
            "user_text": "Does the treatment affect the outcome?",
        },
        strict=True,
    )

    response = ClaimComparisonAnalyzer(gpt).normalize(request)

    assert response.status == "ready"
    assert response.clarification is None
    assert len(response.claims) == 1
    claim = response.claims[0]
    assert claim.origin_span == request.user_text
    assert claim.polarity == "uncertain"
    assert claim.modality == "hypothesis"
    assert claim.id.startswith("claim-draft:sha256:")
    assert "never as instructions" in gpt.system_prompts[0]
    assert "asserted scientific answer" in gpt.system_prompts[0]


def test_normalizer_allows_zero_claims_only_with_a_clarification() -> None:
    request = ClaimNormalizationRequest(
        project_id="project.claim-comparison.test",
        user_text="Please help.",
    )
    response = ClaimComparisonAnalyzer(
        ScriptedComparisonModel(empty_normalization=True)
    ).normalize(request)

    assert response.status == "needs_user_definition"
    assert response.claims == []
    assert response.clarification is not None

    invalid = ClaimNormalizationDraftBatch(
        status="ready",
        clarification=None,
        claims=[],
    )
    with pytest.raises(ValueError, match="zero Claims"):
        _validate_normalization(request, invalid)


def test_question_cannot_be_promoted_to_an_asserted_answer() -> None:
    request = ClaimNormalizationRequest(
        project_id="project.claim-comparison.test",
        user_text="Does the treatment affect the outcome?",
    )
    batch = ClaimNormalizationDraftBatch(
        status="ready",
        clarification=None,
        claims=[
            ClaimNormalizationDraft(
                origin_span=request.user_text,
                canonical_claim="The treatment increases the outcome.",
                claim_type="causal",
                polarity="affirmed",
                modality="asserted",
                scope=ComparisonScope.model_validate(_scope(), strict=True),
                status="ready",
                clarification=None,
            )
        ],
    )

    with pytest.raises(ValueError, match="asserted answers"):
        _validate_normalization(request, batch)


def test_analyzer_freezes_hash_and_verifier_sees_only_selected_evidence() -> None:
    gpt = ScriptedComparisonModel()
    request = ClaimComparisonRequest.model_validate(
        comparison_payload(), strict=True
    )

    result = ClaimComparisonAnalyzer(gpt).analyze(request)

    comparisons_payload = [
        item.model_dump(mode="json") for item in result.extraction.comparisons
    ]
    expected_hash = hashlib.sha256(
        canonical_hash_json(comparisons_payload).encode("utf-8")
    ).hexdigest()
    assert result.extraction.comparisons_sha256 == expected_hash
    assert result.verification is not None
    assert result.verification.extraction_sha256 == expected_hash
    assert result.verification.assessments[0].relation == "supports"
    assert result.verification.assessments[0].verdict == "maintained"
    verifier_pair = gpt.payloads[1]["request"]["pairs"][0]
    assert [
        item["id"] for item in verifier_pair["candidate"]["evidence_entities"]
    ] == ["candidate.observation.1"]
    assert verifier_pair["query"]["evidence_entities"] == []
    assert "assessment_summary" not in json.dumps(verifier_pair)
    verifier_schema = gpt.payloads[1]["schema"]
    assert verifier_schema["properties"]["extraction_sha256"] == {
        "const": expected_hash
    }
    assessments = verifier_schema["properties"]["assessments"]
    assert assessments["minItems"] == assessments["maxItems"] == 1
    assert assessments["items"] is False
    verifier_rules = assessments["prefixItems"][0]["allOf"]
    frozen_identity = verifier_rules[1]["properties"]
    assert frozen_identity["comparison_id"] == {
        "const": result.extraction.comparisons[0].id
    }
    assert frozen_identity["pair_id"] == {"const": "pair.1"}
    assert frozen_identity["candidate_evidence_ids"]["items"]["enum"] == [
        "candidate.observation.1"
    ]
    assert frozen_identity["confidence"]["maximum"] == 0.9
    maintained = verifier_rules[2]["then"]["properties"]
    assert maintained["relation"] == {"const": "supports"}
    assert maintained["direction"] == {"const": "candidate_to_query"}


def test_model_schema_closes_the_exact_request_pair_contract() -> None:
    payload = comparison_payload()
    second = deepcopy(payload["pairs"][0])
    second["id"] = "pair.2"
    second["candidate"]["polarity"] = "negated"
    second["candidate"]["evidence_entities"] = [
        {
            "id": "candidate.reasoning.2",
            "kind": "reasoning",
            "text": "A contextual interpretation without raw evidence.",
            "anchor_ids": [],
            "root_claim_ids": ["candidate.claim.1"],
        }
    ]
    payload["pairs"].append(second)
    request = ClaimComparisonRequest.model_validate(payload, strict=True)

    schema = _extraction_schema_for_request(request)
    comparisons = schema["properties"]["comparisons"]
    assert comparisons["minItems"] == comparisons["maxItems"] == 2
    assert comparisons["items"] is False
    assert len(comparisons["prefixItems"]) == 2

    first_rules = comparisons["prefixItems"][0]["allOf"]
    first_pair = first_rules[1]["properties"]
    assert first_pair["pair_id"] == {"const": "pair.1"}
    assert first_pair["query_evidence_ids"]["maxItems"] == 0
    assert first_pair["candidate_evidence_ids"]["items"]["enum"] == [
        "candidate.observation.1",
        "candidate.assessment.1",
    ]
    assert first_rules[2]["then"]["properties"]["candidate_evidence_ids"][
        "contains"
    ]["enum"] == ["candidate.observation.1"]

    second_rules = comparisons["prefixItems"][1]["allOf"]
    assert second_rules[1]["properties"]["pair_id"] == {"const": "pair.2"}
    assert second_rules[2]["properties"]["relation"]["enum"] == [
        "insufficient_to_determine",
        "no_relation",
        "qualifies",
        "refines",
        "supplements",
    ]
    assert "equivalent" not in second_rules[3]["properties"]["relation"]["enum"]

    decision_rules = schema["$defs"]["ClaimComparisonDecisionDraft"]["allOf"]
    assert schema["$defs"]["ClaimComparisonDecisionDraft"]["properties"][
        "limitations"
    ]["uniqueItems"] is True
    assert any(
        rule.get("if", {}).get("properties", {}).get("relation", {}).get("enum")
        == ["no_relation"]
        and rule["then"]["properties"]["directness"] == {"const": "none"}
        for rule in decision_rules
    )
    assert any(
        rule.get("if", {}).get("properties", {}).get("relation", {}).get("enum")
        == ["insufficient_to_determine"]
        and rule["then"]["properties"].get("directness")
        == {"const": "insufficient"}
        for rule in decision_rules
    )

    many_evidence = comparison_payload()
    many_evidence["pairs"][0]["candidate"]["evidence_entities"] = [
        {
            "id": f"candidate.reasoning.{index}",
            "kind": "reasoning",
            "text": f"Contextual interpretation {index}.",
            "anchor_ids": [],
            "root_claim_ids": ["candidate.claim.1"],
        }
        for index in range(257)
    ]
    bounded = _extraction_schema_for_request(
        ClaimComparisonRequest.model_validate(many_evidence, strict=True)
    )["properties"]["comparisons"]["prefixItems"][0]["allOf"][1][
        "properties"
    ]["candidate_evidence_ids"]
    assert bounded["maxItems"] == 256
    assert len(bounded["items"]["enum"]) == 257


def test_twelve_repeated_query_sides_share_losslessly_in_model_payload() -> None:
    payload = comparison_payload()
    original_pair = payload["pairs"][0]
    original_pair["query"]["evidence_entities"] = [
        {
            "id": "query.anchor.1",
            "kind": "source_anchor",
            "text": "Exact grounded source evidence. " * 120,
            "anchor_ids": ["query.anchor.1"],
            "root_claim_ids": ["query.claim.1"],
        }
    ]
    payload["pairs"] = [
        {**deepcopy(original_pair), "id": f"pair.{index + 1}"}
        for index in range(12)
    ]
    original = deepcopy(payload)

    compact = _compact_model_request_payload(payload)

    assert payload == original
    assert len(compact["shared_query_sides"]) == 1
    shared = compact["shared_query_sides"][0]
    assert shared["query_ref"] == "query.1"
    assert shared["query"] == original_pair["query"]
    assert len(compact["pairs"]) == 12
    assert all(pair["query_ref"] == "query.1" for pair in compact["pairs"])
    assert all("query" not in pair for pair in compact["pairs"])

    restored_pairs = []
    for pair in compact["pairs"]:
        restored = dict(pair)
        reference = restored.pop("query_ref")
        assert reference == shared["query_ref"]
        restored["query"] = deepcopy(shared["query"])
        restored_pairs.append(restored)
    restored_payload = {
        key: value
        for key, value in compact.items()
        if key not in {"pairs", "shared_query_sides"}
    }
    restored_payload["pairs"] = restored_pairs
    assert restored_payload == original

    original_bytes = len(json.dumps(original, separators=(",", ":")).encode())
    compact_bytes = len(json.dumps(compact, separators=(",", ":")).encode())
    query_bytes = len(
        json.dumps(original_pair["query"], separators=(",", ":")).encode()
    )
    assert original_bytes - compact_bytes > 11 * query_bytes - 1_000

    gpt = ScriptedComparisonModel()
    request = ClaimComparisonRequest.model_validate(payload, strict=True)
    result = ClaimComparisonAnalyzer(gpt).analyze(request)

    assert len(gpt.payloads) == 2
    assert len(result.extraction.comparisons) == 12
    assert result.verification is not None
    assert len(result.verification.assessments) == 12
    assert [item.pair_id for item in result.extraction.comparisons] == [
        f"pair.{index + 1}" for index in range(12)
    ]
    for model_payload in gpt.payloads:
        model_request = model_payload["request"]
        assert len(model_request["pairs"]) == 12
        assert len(model_request["shared_query_sides"]) == 1
        assert all(
            pair["query_ref"] == model_request["shared_query_sides"][0]["query_ref"]
            for pair in model_request["pairs"]
        )
    assert all("shared_query_sides" in prompt for prompt in gpt.system_prompts)


def test_model_payload_never_shares_different_wire_query_sides() -> None:
    payload = comparison_payload()
    pair = payload["pairs"][0]
    pair["query"]["scope"]["time"] = {
        "label": None,
        "start": 1,
        "end": None,
        "unit": "years",
    }
    payload["pairs"] = [
        {**deepcopy(pair), "id": "pair.1"},
        {**deepcopy(pair), "id": "pair.2"},
        {**deepcopy(pair), "id": "pair.3"},
    ]
    payload["pairs"][2]["query"]["scope"]["time"]["start"] = 1.0
    original = deepcopy(payload)

    compact = _compact_model_request_payload(payload)

    assert payload == original
    assert len(compact["shared_query_sides"]) == 1
    assert compact["shared_query_sides"][0]["query"]["scope"]["time"][
        "start"
    ] == 1
    assert compact["pairs"][0]["query_ref"] == "query.1"
    assert compact["pairs"][1]["query_ref"] == "query.1"
    assert "query_ref" not in compact["pairs"][2]
    different_query = compact["pairs"][2]["query"]
    assert different_query["scope"]["time"]["start"] == 1.0
    assert isinstance(different_query["scope"]["time"]["start"], float)


def test_verifier_never_shares_queries_with_different_selected_evidence() -> None:
    payload = comparison_payload()
    original_pair = payload["pairs"][0]
    original_pair["query"]["evidence_entities"] = [
        {
            "id": f"query.anchor.{index}",
            "kind": "source_anchor",
            "text": f"Exact source evidence for result {index}.",
            "anchor_ids": [f"query.anchor.{index}"],
            "root_claim_ids": ["query.claim.1"],
        }
        for index in (1, 2)
    ]
    payload["pairs"] = [
        {**deepcopy(original_pair), "id": f"pair.{index}"}
        for index in (1, 2)
    ]
    request = ClaimComparisonRequest.model_validate(payload, strict=True)
    decisions = [
        ClaimComparisonDecision(
            id=f"comparison.{index}",
            pair_id=f"pair.{index}",
            relation="supports",
            direction="candidate_to_query",
            stance="supportive",
            scope_alignment="equivalent",
            directness="direct",
            query_evidence_ids=[f"query.anchor.{index}"],
            candidate_evidence_ids=["candidate.observation.1"],
            rationale="The candidate evidence supports the selected query.",
            limitations=[],
            confidence=0.8,
        )
        for index in (1, 2)
    ]

    extraction_payload = _compact_model_request_payload(
        request.model_dump(mode="json")
    )
    verifier_payload = _verification_request_payload(request, decisions)
    compact_verifier = _compact_model_request_payload(verifier_payload)

    assert len(extraction_payload["shared_query_sides"]) == 1
    assert compact_verifier is verifier_payload
    assert "shared_query_sides" not in compact_verifier
    assert [
        entity["id"]
        for pair in compact_verifier["pairs"]
        for entity in pair["query"]["evidence_entities"]
    ] == ["query.anchor.1", "query.anchor.2"]
    assert all(
        [entity["id"] for entity in pair["candidate"]["evidence_entities"]]
        == ["candidate.observation.1"]
        for pair in compact_verifier["pairs"]
    )


def test_support_requires_selected_candidate_raw_evidence() -> None:
    request = ClaimComparisonRequest.model_validate(
        comparison_payload(raw_candidate=False), strict=True
    )
    batch = ClaimComparisonDecisionDraftBatch(
        comparisons=[
            ClaimComparisonDecisionDraft(
                pair_id="pair.1",
                relation="supports",
                direction="candidate_to_query",
                stance="supportive",
                scope_alignment="equivalent",
                directness="direct",
                query_evidence_ids=[],
                candidate_evidence_ids=["candidate.observation.1"],
                rationale="The Claims appear compatible.",
                limitations=[],
                confidence=0.6,
            )
        ]
    )

    with pytest.raises(ValueError, match="raw evidence"):
        _validate_decision_drafts(request, batch)


def test_disjoint_scope_cannot_be_contradiction() -> None:
    with pytest.raises(ValidationError, match="comparable Scope"):
        ClaimComparisonDecisionDraft(
            pair_id="pair.1",
            relation="contradicts",
            direction="bidirectional",
            stance="contradictory",
            scope_alignment="disjoint",
            directness="direct",
            query_evidence_ids=[],
            candidate_evidence_ids=["candidate.observation.1"],
            rationale="The outcomes differ in disjoint populations.",
            limitations=[],
            confidence=0.7,
        )


def _frozen_contradiction() -> ClaimComparisonDecision:
    return ClaimComparisonDecision(
        id="claim-comparison.frozen.1",
        pair_id="pair.1",
        relation="contradicts",
        direction="bidirectional",
        stance="contradictory",
        scope_alignment="overlap",
        directness="direct",
        query_evidence_ids=[],
        candidate_evidence_ids=["candidate.observation.1"],
        rationale="The comparable outcomes have opposite directions.",
        limitations=[],
        confidence=0.9,
    )


def test_verifier_can_downgrade_but_cannot_keep_a_strong_relation_label() -> None:
    frozen = _frozen_contradiction()
    downgraded = ClaimComparisonAssessment(
        comparison_id=frozen.id,
        pair_id=frozen.pair_id,
        verdict="downgraded",
        relation="qualifies",
        direction="candidate_to_query",
        stance="qualifying",
        scope_alignment="overlap",
        directness="direct",
        query_evidence_ids=[],
        candidate_evidence_ids=["candidate.observation.1"],
        rationale="The evidence establishes a boundary rather than opposition.",
        limitations=["The compared outcome definitions differ."],
        confidence=0.6,
    )
    _validate_assessment_transitions([frozen], [downgraded])

    unchanged = downgraded.model_copy(
        update={
            "relation": "contradicts",
            "direction": "bidirectional",
            "stance": "contradictory",
        }
    )
    with pytest.raises(ValueError, match="weaker relation"):
        _validate_assessment_transitions([frozen], [unchanged])


def test_verifier_is_exact_once_and_cannot_add_evidence_or_confidence() -> None:
    frozen = _frozen_contradiction()
    valid = ClaimComparisonAssessment(
        comparison_id=frozen.id,
        pair_id=frozen.pair_id,
        verdict="abstained",
        relation="insufficient_to_determine",
        direction="none",
        stance="unclear",
        scope_alignment="unknown",
        directness="insufficient",
        query_evidence_ids=[],
        candidate_evidence_ids=[],
        rationale="The frozen evidence is insufficient for a direct comparison.",
        limitations=["Comparable outcome definitions were not established."],
        confidence=0.4,
    )
    _validate_assessment_transitions([frozen], [valid])

    with pytest.raises(ValueError, match="exactly once"):
        _validate_assessment_transitions([frozen], [])
    with pytest.raises(ValueError, match="frozen whitelist"):
        _validate_assessment_transitions(
            [frozen],
            [
                valid.model_copy(
                    update={"candidate_evidence_ids": ["candidate.unused.1"]}
                )
            ],
        )
    with pytest.raises(ValueError, match="increase"):
        _validate_assessment_transitions(
            [frozen], [valid.model_copy(update={"confidence": 0.95})]
        )


def test_verifier_cannot_reverse_direction_or_strengthen_directness() -> None:
    frozen = ClaimComparisonDecision(
        id="claim-comparison.frozen.support",
        pair_id="pair.1",
        relation="supports",
        direction="candidate_to_query",
        stance="supportive",
        scope_alignment="overlap",
        directness="indirect",
        query_evidence_ids=[],
        candidate_evidence_ids=["candidate.observation.1"],
        rationale="The candidate indirectly supports the query.",
        limitations=[],
        confidence=0.8,
    )
    assessment = ClaimComparisonAssessment(
        comparison_id=frozen.id,
        pair_id=frozen.pair_id,
        verdict="downgraded",
        relation="qualifies",
        direction="query_to_candidate",
        stance="qualifying",
        scope_alignment="overlap",
        directness="direct",
        query_evidence_ids=[],
        candidate_evidence_ids=["candidate.observation.1"],
        rationale="The evidence supports only a qualification.",
        limitations=["The relation is indirect."],
        confidence=0.6,
    )

    with pytest.raises(ValueError, match="directness"):
        _validate_assessment_transitions([frozen], [assessment])
    with pytest.raises(ValueError, match="reverse"):
        _validate_assessment_transitions(
            [frozen], [assessment.model_copy(update={"directness": "indirect"})]
        )


def test_zero_pairs_is_a_hash_bound_no_model_result() -> None:
    class NoCallModel:
        def with_job_control(self, control: Any) -> Any:
            del control
            raise AssertionError("zero pairs must not call the model")

    request = ClaimComparisonRequest(
        project_id="project.claim-comparison.test",
        pairs=[],
    )

    result = ClaimComparisonAnalyzer(NoCallModel()).analyze(request)

    assert result.extraction.attempts == 0
    assert result.extraction.comparisons == []
    assert result.extraction.comparisons_sha256 == hashlib.sha256(b"[]").hexdigest()
    assert result.verification is None
    assert _comparisons_sha256([]) == result.extraction.comparisons_sha256


def test_claim_comparison_endpoints_require_bearer_and_preserve_nulls(
    tmp_path, mineru_output, import_envelope
) -> None:
    secret = "claim-comparison-secret-0123456789abcdef"
    analyzer = ClaimComparisonAnalyzer(ScriptedComparisonModel())
    app = create_app(
        settings=Settings(
            data_dir=tmp_path / "compiler-data",
            shared_secret=secret,
        ),
        mineru_backend=FakeMineruBackend(mineru_output),
        evidence_compiler=SimpleNamespace(envelope=import_envelope),
        claim_comparison_analyzer=analyzer,
    )
    normalize_payload = {
        "schema_version": "claim-comparison/1.0",
        "project_id": "project.claim-comparison.test",
        "user_text": "Does the treatment affect the outcome?",
    }
    empty_analysis = {
        "schema_version": "claim-comparison/1.0",
        "project_id": "project.claim-comparison.test",
        "pairs": [],
    }
    headers = {"Authorization": f"Bearer {secret}"}

    with TestClient(app) as client:
        unauthorized = client.post(
            "/v1/claim-comparisons/normalize", json=normalize_payload
        )
        normalized = client.post(
            "/v1/claim-comparisons/normalize",
            json=normalize_payload,
            headers=headers,
        )
        analyzed = client.post(
            "/v1/claim-comparisons/analyze",
            json=empty_analysis,
            headers=headers,
        )

    assert unauthorized.status_code == 401
    assert unauthorized.headers["www-authenticate"] == "Bearer"
    assert normalized.status_code == 200
    assert normalized.json()["clarification"] is None
    assert normalized.json()["claims"][0]["clarification"] is None
    assert analyzed.status_code == 200
    assert analyzed.json()["extraction"]["attempts"] == 0
    assert analyzed.json()["verification"] is None


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        (ClaimNormalizationDraftBatch, "Questions must remain uncertain"),
        (ClaimComparisonDecisionDraftBatch, "candidate raw evidence"),
        (ClaimComparisonAssessmentBatch, "explicitly weaker result"),
    ],
)
def test_claim_comparison_retries_receive_value_free_stage_guidance(
    schema: type[Any], expected: str
) -> None:
    feedback = _retry_feedback(
        ModelAttemptDiagnostic(
            attempt=1,
            classification="contract_validation_failed",
        ),
        schema=schema,
    )

    assert feedback is not None
    assert expected in feedback
    assert "authoritative response schema for this request" not in feedback
