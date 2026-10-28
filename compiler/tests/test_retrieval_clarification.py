"""Clarification contract checks shared by the 2.3 context protocol.

The clarification-only protocol ``evidence-retrieval/2.2`` (c1 tokens, its
decision/selected planner prompts and its configuration identity) was retired
on 2026-10-19. Clarification now exists only inside 2.3, where the choice is
carried by a c2 context token; these offline checks do not judge model
ambiguity recognition.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_retrieval_service import _HANDOFF_SECRET_A, _search_request, _service

from compiler_service.retrieval_contract import (
    ClarificationPrompt,
    HybridSearchRequest,
    HybridSearchResponse,
    QueryPlanHandoffPayload,
)
from compiler_service.retrieval_service import ClarificationSelectionError

ORIGINAL = (
    "Ask me to choose before answering. [A] Ordos tectonics? [B] Pacific ecology?"
)
OPTIONS = {
    "question": "Which task should I retrieve?",
    "options": [
        {"option_id": "A", "label": "Ordos", "resolved_query": "Ordos tectonics"},
        {"option_id": "B", "label": "Pacific", "resolved_query": "Pacific ecology"},
    ],
}


def forbidden(*args, **kwargs):
    raise AssertionError("retrieval/provider must not be called")


def test_retired_clarification_protocol_is_rejected_at_every_boundary():
    value = _search_request(
        ORIGINAL, schema_version="evidence-retrieval/2.3", stage="preview"
    ).model_dump(mode="json")
    value["schema_version"] = "evidence-retrieval/2.2"
    with pytest.raises(ValidationError):
        HybridSearchRequest.model_validate(value, strict=True)
    skipped = {"attempted": False, "status": "skipped", "provider": "x", "returned": 0}
    pending = {
        "schema_version": "evidence-retrieval/2.2",
        "stage": "preview",
        "result_status": "clarification_required",
        "query_plan": None,
        "clarification": {**OPTIONS, "clarification_token": "c1.x.y.z"},
        "channels": {
            name: skipped for name in ("lexical", "dense", "rerank", "relation")
        },
        "preview": [],
        "clusters": [],
        "warnings": [],
    }
    with pytest.raises(ValidationError):
        HybridSearchResponse.model_validate(pending, strict=True)
    assert "evidence-retrieval/2.2" not in str(
        QueryPlanHandoffPayload.model_json_schema()
    )


def test_a_retired_c1_choice_is_not_a_23_continuation(tmp_path: Path, monkeypatch):
    service = _service(tmp_path, query_plan_handoff_secret=_HANDOFF_SECRET_A)
    value = _search_request(
        ORIGINAL, schema_version="evidence-retrieval/2.3", stage="preview"
    ).model_dump(mode="json")
    value["clarification_selection"] = {
        "clarification_token": "c1.derived-auth-v1.e30." + "a" * 43,
        "option_id": "A",
    }
    monkeypatch.setattr(service, "_resolve_snapshots", forbidden)
    monkeypatch.setattr(service, "_query_document", forbidden)
    with pytest.raises(ClarificationSelectionError) as caught:
        service.search(HybridSearchRequest.model_validate(value, strict=True))
    assert caught.value.status_code == 409


def test_contract_bounds_and_legacy_omission():
    for version in ("evidence-retrieval/2.0", "evidence-retrieval/2.1"):
        request = _search_request("Ordos", schema_version=version, stage="preview")
        assert "clarification_selection" not in request.model_dump(mode="json")
        value = request.model_dump(mode="json")
        value["clarification_selection"] = {
            "clarification_token": "x",
            "option_id": "A",
        }
        with pytest.raises(ValidationError):
            HybridSearchRequest.model_validate(value, strict=True)
    for field, invalid in (
        ("question", "x" * 1001),
        ("options", OPTIONS["options"][:1]),
        ("options", OPTIONS["options"] * 3),
    ):
        value = deepcopy(OPTIONS)
        value[field] = invalid
        with pytest.raises(ValidationError):
            ClarificationPrompt.model_validate(value, strict=True)
    for field, invalid in (
        ("option_id", "x" * 33),
        ("option_id", "a b"),
        ("label", "x" * 201),
        ("resolved_query", "e\u0301"),
        ("resolved_query", "x" * 8001),
    ):
        value = deepcopy(OPTIONS)
        value["options"][0][field] = invalid
        with pytest.raises(ValidationError):
            ClarificationPrompt.model_validate(value, strict=True)
    for field in ("option_id", "resolved_query"):
        value = deepcopy(OPTIONS)
        value["options"][1][field] = value["options"][0][field]
        with pytest.raises(ValidationError):
            ClarificationPrompt.model_validate(value, strict=True)
