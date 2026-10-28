"""Internal opaque handoff contract; no services, models or scientific fixtures."""

import json

import pytest
from pydantic import ValidationError
from test_retrieval_providers import _planner_payload

from compiler_service.retrieval_contract import (
    QueryPlan,
    QueryPlanHandoffPayload,
    RetrievalContextState,
)


def frozen_reference():
    from test_retrieval_task_contract import proposal, request

    from compiler_service.retrieval_providers import (
        freeze_compiled_context_task_reference,
    )
    from compiler_service.retrieval_task_contract import compile_task_contract

    task = request("fictional task")
    context = RetrievalContextState()
    compiled = compile_task_contract(
        proposal(
            "answer_questions",
            task.query,
            questions=[{"source": "query", "quote": task.query}],
        ),
        task,
        context=context,
    )
    return freeze_compiled_context_task_reference(
        task, context=context, compiled_task=compiled
    ).model_dump(mode="json")


def payload(version="2.3"):
    value = {
        "schema_version": "deepshovel-query-plan-handoff/1.0",
        "issuer": "deepshovel-compiler",
        "audience": "/v1/retrieval/search:ai_rerank",
        "key_id": "derived-auth-v1",
        "issued_at": 100,
        "expires_at": 220,
        "retrieval_schema_version": f"evidence-retrieval/{version}",
        "request_binding_sha256": "a" * 64,
        "corpus_binding_sha256": "b" * 64,
        "configuration_binding_sha256": "c" * 64,
        "query_plan": QueryPlan(
            **_planner_payload("fictional task").model_dump(),
            original_query="fictional task",
            planner="gpt",
        ).model_dump(mode="json"),
        "ordered_candidates": [],
    }
    if version == "2.3":
        value.update(
            context_state={"supplements": [], "selected_query": None},
            task_context_sha256="d" * 64,
            frozen_task_reference=frozen_reference(),
        )
    return value


@pytest.mark.parametrize(
    "field", ["context_state", "task_context_sha256", "frozen_task_reference"]
)
def test_context_handoff_requires_each_nonnull_binding(field):
    value = payload()
    value.pop(field)
    with pytest.raises(ValidationError):
        QueryPlanHandoffPayload.model_validate(value, strict=True)
    value[field] = None
    with pytest.raises(ValidationError):
        QueryPlanHandoffPayload.model_validate(value, strict=True)


def test_context_roundtrip_preserves_exact_unicode_and_aggregate_limit():
    value = payload()
    value["context_state"] = {
        "supplements": ["😀" * 8000, "é" * 384],
        "selected_query": "  café\n",
    }
    token = QueryPlanHandoffPayload.model_validate(value, strict=True)
    assert token.model_dump(mode="json") == value
    assert QueryPlanHandoffPayload.model_validate_json(token.model_dump_json()) == token
    value["context_state"]["supplements"][-1] += "x"
    with pytest.raises(ValidationError):
        QueryPlanHandoffPayload.model_validate(value, strict=True)


@pytest.mark.parametrize("text", ["e\u0301", "\ud800", " \n", "x" * 8001])
def test_context_handoff_rejects_invalid_text_without_repair(text):
    for state in ({"supplements": [text]}, {"selected_query": text}):
        with pytest.raises(ValidationError):
            QueryPlanHandoffPayload.model_validate(
                {**payload(), "context_state": state}, strict=True
            )


def test_legacy_bytes_unchanged_and_explicit_context_fields_forbidden():
    value = payload("2.1")
    token = QueryPlanHandoffPayload.model_validate(value, strict=True)
    expected = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    assert token.model_dump_json().encode() == expected.encode()
    for field, present in (
        ("context_state", {"supplements": [], "selected_query": None}),
        ("task_context_sha256", "d" * 64),
        ("frozen_task_reference", frozen_reference()),
    ):
        for forbidden in (None, present):
            with pytest.raises(ValidationError):
                QueryPlanHandoffPayload.model_validate(
                    {**value, field: forbidden}, strict=True
                )


def test_retired_22_handoffs_are_no_longer_accepted():
    with pytest.raises(ValidationError):
        QueryPlanHandoffPayload.model_validate(payload("2.2"), strict=True)


def test_closed_context_payload_and_digest_shape():
    for updates in (
        {"extra": True},
        {"context_state": {"extra": True}},
        {"task_context_sha256": "not-a-sha256"},
        {"expires_at": 100},
    ):
        with pytest.raises(ValidationError):
            QueryPlanHandoffPayload.model_validate(
                {**payload(), **updates}, strict=True
            )
    schema = QueryPlanHandoffPayload.model_json_schema()
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["RetrievalContextState"]["additionalProperties"] is False


def test_existing_duplicate_candidate_rejection_is_preserved():
    candidate = {
        "claim_id": "claim-1",
        "snapshot_id": "snapshot-1",
        "paper_id": "paper-1",
        "paper_family_id": "family-1",
        "scores": {"lexical": None, "dense": None, "rrf": 0.0, "rerank": None},
    }
    value = {**payload(), "ordered_candidates": [candidate, candidate]}
    with pytest.raises(ValidationError, match="candidates must be unique"):
        QueryPlanHandoffPayload.model_validate(value, strict=True)
