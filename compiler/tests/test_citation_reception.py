from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from compiler_service.citation_reception import (
    CitationAssertionDraftBatch,
    CitationAssessmentBatch,
    CitationPairRequest,
    CitationReceptionAnalyzer,
    _validate_drafts,
)
from compiler_service.errors import ContractIssue, ContractValidationError
from compiler_service.graph_contract import canonical_hash_json, canonical_json
from compiler_service.model_client import Completion


def citation_pair_payload() -> dict[str, Any]:
    return {
        "schema_version": "citation-reception/1.0",
        "project_id": "project.citation.test",
        "citing_snapshot_id": "snapshot.citing",
        "cited_snapshot_id": "snapshot.cited",
        "reference": {
            "id": "citation.reference.1",
            "raw_text": "Smith B. Result two. 2021.",
            "doi": None,
            "year": 2021,
        },
        "mentions": [
            {
                "id": "citation.mention.1",
                "marker": "[1]",
                "exact_quote": "Our result was consistent with Smith [1].",
                "context_text": "Our measured increase was consistent with Smith [1].",
                "page": 4,
                "group_size": 1,
                "candidate_functions": ["result_comparison"],
            }
        ],
        "citing_entities": [
            {
                "id": "a.observation.1",
                "kind": "observation",
                "text": "A measured the same 12% increase.",
                "anchor_ids": ["a.anchor.1"],
                "root_claim_ids": ["a.claim.1"],
            }
        ],
        "cited_entities": [
            {
                "id": "b.claim.1",
                "kind": "atomic_claim",
                "text": "Treatment increased the outcome by 12%.",
                "anchor_ids": ["b.anchor.1"],
                "root_claim_ids": ["b.claim.1"],
            }
        ],
    }


class ScriptedCitationModel:
    def __init__(
        self,
        *,
        verifier_hash: str | None = None,
        empty_extraction: bool = False,
        targetless_verbal: bool = False,
        omit_target_evidence: bool = False,
        verifier_basis: str = "consistent_with_citing_evidence",
    ) -> None:
        self.verifier_hash = verifier_hash
        self.empty_extraction = empty_extraction
        self.targetless_verbal = targetless_verbal
        self.omit_target_evidence = omit_target_evidence
        self.verifier_basis = verifier_basis
        self.payloads: list[dict[str, Any]] = []
        self.system_prompts: list[str] = []

    def with_job_control(self, control: Any) -> ScriptedCitationModel:
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
        if schema is CitationAssertionDraftBatch:
            value = CitationAssertionDraftBatch.model_validate(
                {
                    "assertions": []
                    if self.empty_extraction
                    else [
                        {
                            "mention_id": "citation.mention.1",
                            "attributed_proposition": (
                                "Treatment increased the outcome by 12%."
                            ),
                            "function": "result_comparison",
                            "stance": "supports",
                            "confirmation_basis": (
                                "verbal_endorsement"
                                if self.targetless_verbal
                                else "consistent_with_citing_evidence"
                            ),
                            "target_entity_id": (
                                None if self.targetless_verbal else "b.claim.1"
                            ),
                            "citing_evidence_entity_ids": (
                                [] if self.targetless_verbal else ["a.observation.1"]
                            ),
                            "confidence": 1e-7,
                        }
                    ]
                },
                strict=True,
            )
            content_sha256 = "a" * 64
        else:
            assert schema is CitationAssessmentBatch
            frozen = payload["frozen_assertions"][0]
            value = CitationAssessmentBatch.model_validate(
                {
                    "extraction_sha256": (
                        self.verifier_hash or payload["extraction_sha256"]
                    ),
                    "assessments": [
                        {
                            "assertion_id": frozen["id"],
                            "faithfulness": "faithful",
                            "confirmation_basis": self.verifier_basis,
                            "independence": "unknown",
                            "confidence": 0.9,
                            "evidence_ids": (
                                ["a.observation.1"]
                                if self.omit_target_evidence
                                else ["a.observation.1", "b.claim.1"]
                            ),
                            "limitations": [],
                        }
                    ],
                },
                strict=True,
            )
            content_sha256 = "b" * 64
        validate(value)
        return Completion(
            value=value,
            attempts=1,
            content_sha256=content_sha256,
            diagnostics=(),
        )


def test_pair_analyzer_uses_web_compatible_frozen_hash_and_binds_verifier() -> None:
    gpt = ScriptedCitationModel()
    request = CitationPairRequest.model_validate(citation_pair_payload(), strict=True)

    result = CitationReceptionAnalyzer(gpt).analyze(request)

    assertions_payload = [
        item.model_dump(mode="json") for item in result.extraction.assertions
    ]
    expected = hashlib.sha256(
        canonical_hash_json(assertions_payload).encode("utf-8")
    ).hexdigest()
    legacy_python_hash = hashlib.sha256(
        canonical_json(assertions_payload).encode("utf-8")
    ).hexdigest()
    assert expected != legacy_python_hash
    assert result.extraction.assertions_sha256 == expected
    assert result.verification is not None
    assert result.verification.extraction_sha256 == expected
    assert gpt.payloads[1]["extraction_sha256"] == expected
    assert gpt.payloads[1]["frozen_assertions"] == assertions_payload
    assert gpt.payloads[1]["pair"] == request.model_dump(mode="json")
    assert gpt.payloads[0]["pair"]["mentions"][0]["group_size"] == 1


def test_pair_analyzer_rejects_a_verifier_for_a_different_extraction() -> None:
    gpt = ScriptedCitationModel(verifier_hash="0" * 64)
    request = CitationPairRequest.model_validate(citation_pair_payload(), strict=True)

    with pytest.raises(ContractValidationError) as error:
        CitationReceptionAnalyzer(gpt).analyze(request)
    assert error.value.contract_issue is ContractIssue.CITATION_VERIFIER_HASH_MISMATCH


def test_verifier_receives_only_entities_used_by_the_frozen_assertion() -> None:
    payload = citation_pair_payload()
    payload["citing_entities"].append(
        {
            "id": "a.observation.unused",
            "kind": "observation",
            "text": "An unrelated citing-paper observation.",
            "anchor_ids": [],
            "root_claim_ids": [],
        }
    )
    payload["cited_entities"].append(
        {
            "id": "b.claim.unused",
            "kind": "atomic_claim",
            "text": "An unrelated cited-paper Claim.",
            "anchor_ids": [],
            "root_claim_ids": [],
        }
    )
    request = CitationPairRequest.model_validate(payload, strict=True)
    gpt = ScriptedCitationModel()

    CitationReceptionAnalyzer(gpt).analyze(request)

    assert len(gpt.payloads[0]["pair"]["citing_entities"]) == 2
    assert [
        item["id"] for item in gpt.payloads[1]["pair"]["citing_entities"]
    ] == ["a.observation.1"]
    assert [
        item["id"] for item in gpt.payloads[1]["pair"]["cited_entities"]
    ] == ["b.claim.1"]


def test_faithful_verifier_target_reference_omission_is_closed() -> None:
    request = CitationPairRequest.model_validate(citation_pair_payload(), strict=True)

    result = CitationReceptionAnalyzer(
        ScriptedCitationModel(omit_target_evidence=True)
    ).analyze(request)

    assert result.verification is not None
    assert result.verification.assessments[0].evidence_ids == [
        "a.observation.1",
        "b.claim.1",
    ]


def test_draft_uniqueness_uses_the_same_whitespace_identity_as_frozen_ids() -> None:
    request = CitationPairRequest.model_validate(citation_pair_payload(), strict=True)
    assertions = [
        {
            "mention_id": "citation.mention.1",
            "attributed_proposition": proposition,
            "function": "result_comparison",
            "stance": "supports",
            "confirmation_basis": "verbal_endorsement",
            "target_entity_id": "b.claim.1",
            "citing_evidence_entity_ids": [],
            "confidence": 0.8,
        }
        for proposition in (
            "Treatment increased the outcome.",
            "Treatment   increased\n the outcome.",
        )
    ]
    batch = CitationAssertionDraftBatch.model_validate(
        {"assertions": assertions}, strict=True
    )

    with pytest.raises(ContractValidationError) as error:
        _validate_drafts(request, batch)
    assert error.value.contract_issue is ContractIssue.CITATION_ASSERTION_DUPLICATE


def test_directional_evidence_basis_must_match_stance() -> None:
    request = CitationPairRequest.model_validate(citation_pair_payload(), strict=True)
    batch = CitationAssertionDraftBatch.model_validate(
        {
            "assertions": [
                {
                    "mention_id": "citation.mention.1",
                    "attributed_proposition": "Treatment increased the outcome.",
                    "function": "result_comparison",
                    "stance": "qualifies",
                    "confirmation_basis": "consistent_with_citing_evidence",
                    "target_entity_id": "b.claim.1",
                    "citing_evidence_entity_ids": ["a.observation.1"],
                    "confidence": 0.8,
                }
            ]
        },
        strict=True,
    )

    with pytest.raises(ContractValidationError) as error:
        _validate_drafts(request, batch)
    assert error.value.contract_issue is ContractIssue.CITATION_BASIS_STANCE_MISMATCH


def test_verifier_cannot_downgrade_a_support_stance_to_mere_attribution() -> None:
    request = CitationPairRequest.model_validate(citation_pair_payload(), strict=True)

    with pytest.raises(ContractValidationError) as error:
        CitationReceptionAnalyzer(
            ScriptedCitationModel(verifier_basis="mere_attribution")
        ).analyze(request)

    assert error.value.contract_issue is ContractIssue.CITATION_BASIS_STANCE_MISMATCH


def test_extractor_rejects_a_non_evaluative_attribution() -> None:
    request = CitationPairRequest.model_validate(citation_pair_payload(), strict=True)
    batch = CitationAssertionDraftBatch.model_validate(
        {
            "assertions": [
                {
                    "mention_id": "citation.mention.1",
                    "attributed_proposition": "Treatment increased the outcome.",
                    "function": "result_comparison",
                    "stance": "unclear",
                    "confirmation_basis": "mere_attribution",
                    "target_entity_id": "b.claim.1",
                    "citing_evidence_entity_ids": [],
                    "confidence": 0.8,
                }
            ]
        },
        strict=True,
    )

    with pytest.raises(ContractValidationError) as error:
        _validate_drafts(request, batch)

    assert error.value.contract_issue is ContractIssue.CITATION_NON_EVALUATIVE


def test_evidence_backed_assertion_requires_a_matching_b_target() -> None:
    request = CitationPairRequest.model_validate(citation_pair_payload(), strict=True)
    batch = CitationAssertionDraftBatch.model_validate(
        {
            "assertions": [
                {
                    "mention_id": "citation.mention.1",
                    "attributed_proposition": "Treatment increased the outcome.",
                    "function": "result_comparison",
                    "stance": "supports",
                    "confirmation_basis": "consistent_with_citing_evidence",
                    "target_entity_id": None,
                    "citing_evidence_entity_ids": ["a.observation.1"],
                    "confidence": 0.8,
                }
            ]
        },
        strict=True,
    )

    with pytest.raises(ContractValidationError) as error:
        _validate_drafts(request, batch)

    assert error.value.contract_issue is ContractIssue.CITATION_TARGET_REQUIRED


def test_grouped_marker_can_produce_no_assertion_when_b_is_not_specific() -> None:
    payload = citation_pair_payload()
    payload["mentions"][0].update(
        {
            "marker": "[1, 2]",
            "exact_quote": "Prior studies reported the effect [1, 2].",
            "context_text": "Prior studies reported the effect [1, 2].",
            "group_size": 2,
        }
    )
    request = CitationPairRequest.model_validate(payload, strict=True)
    gpt = ScriptedCitationModel(empty_extraction=True)

    result = CitationReceptionAnalyzer(gpt).analyze(request)

    assert result.extraction.assertions == []
    assert result.verification is None
    assert gpt.payloads[0]["pair"]["mentions"][0]["group_size"] == 2
    assert "group_size greater than one" in gpt.system_prompts[0]

    targetless = ScriptedCitationModel(targetless_verbal=True)
    unaligned = CitationReceptionAnalyzer(targetless).analyze(request)
    assert unaligned.extraction.assertions == []
    assert unaligned.verification is None
    assert len(targetless.payloads) == 1
