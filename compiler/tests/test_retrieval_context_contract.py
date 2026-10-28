"""Bounded 2.3 context wire checks, independent of scientific judgments."""

from copy import deepcopy

import pytest
from pydantic import ValidationError
from test_retrieval_clarification import OPTIONS
from test_retrieval_providers import _planner_payload
from test_retrieval_service import _search_request

from compiler_service.retrieval_contract import (
    ContextClarificationTokenPayload,
    ContextQueryPlanningOutput,
    ContextSubmission,
    ContextTokenPayload,
    ConversationTurn,
    HybridSearchRequest,
    HybridSearchResponse,
    RetrievalContextState,
    RetrievalOpenAlexScope,
)

TOKEN = "n1.derived-auth-v1.payload." + "a" * 43


def request23(**updates):
    value = _search_request(
        "What about the second case?",
        schema_version="evidence-retrieval/2.3",
        stage="preview",
    ).model_dump(mode="json")
    value.update(updates)
    return HybridSearchRequest.model_validate(value, strict=True)


def test_history_preserves_roles_order_whitespace_and_unicode_codepoints():
    history = [
        {"role": "user", "content": "  café\n"},
        {"role": "assistant", "content": "😀" * 8000},
    ]
    request = request23(conversation_history=history)
    assert request.model_dump(mode="json")["conversation_history"] == history
    assert (
        ContextSubmission(context_token=TOKEN, content="  detail\n").content
        == "  detail\n"
    )


@pytest.mark.parametrize("content", ["", " \n\t", "e\u0301", "\ud800", "a" * 8001])
def test_context_text_rejects_without_repair(content):
    for model, payload in (
        (ConversationTurn, {"role": "user", "content": content}),
        (ContextSubmission, {"context_token": TOKEN, "content": content}),
        (RetrievalContextState, {"supplements": [content], "selected_query": None}),
    ):
        with pytest.raises(ValidationError):
            model.model_validate(payload, strict=True)


def test_history_count_and_aggregate_byte_boundary():
    exact = [{"role": "user", "content": "a" * 8000} for _ in range(4)]
    exact.append({"role": "assistant", "content": "b" * 768})
    assert len(request23(conversation_history=exact).conversation_history) == 5
    exact[-1]["content"] += "b"
    with pytest.raises(ValidationError):
        request23(conversation_history=exact)
    with pytest.raises(ValidationError):
        request23(conversation_history=[{"role": "user", "content": "x"}] * 17)
    with pytest.raises(ValidationError):
        request23(conversation_history=[{"role": "system", "content": "x"}])


def test_supplement_bounds_and_mutual_exclusion():
    assert len(RetrievalContextState(supplements=["x"] * 8).supplements) == 8
    with pytest.raises(ValidationError):
        RetrievalContextState(supplements=["x"] * 9)
    with pytest.raises(ValidationError):
        RetrievalContextState(supplements=["😀" * 8000, "😀" * 193])
    with pytest.raises(ValidationError):
        request23(
            context_submission={"context_token": TOKEN, "content": "detail"},
            clarification_selection={
                "clarification_token": "c2.token",
                "option_id": "A",
            },
        )


@pytest.mark.parametrize("version", ["2.0", "2.1", "2.2"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("conversation_history", []),
        ("conversation_history", None),
        ("context_submission", None),
    ],
)
def test_legacy_rejects_new_fields_even_empty(version, field, value):
    with pytest.raises(ValidationError):
        request23(schema_version=f"evidence-retrieval/{version}", **{field: value})


@pytest.mark.parametrize(
    "level,path",
    [
        ("domain", "domains/1"),
        ("field", "fields/2"),
        ("subfield", "subfields/3"),
        ("topic", "T4"),
    ],
)
def test_openalex_binding_has_level_specific_url(level, path):
    value = {"level": level, "id": f"https://openalex.org/{path}"}
    scope = RetrievalOpenAlexScope.model_validate(value, strict=True)
    assert scope.model_dump() == value
    filters = request23().filters.model_dump(mode="json")
    request = request23(filters={**filters, "openalex_scope": value})
    assert request.filters.model_dump(mode="json")["openalex_scope"] == value
    assert "openalex_scope" not in filters


@pytest.mark.parametrize(
    "value",
    [
        {"level": "domain", "id": "https://openalex.org/T1"},
        {"level": "topic", "id": "https://openalex.org/domains/1"},
        {"level": "topic", "id": "https://evil.test/T1"},
        {"level": "topic", "id": "http://openalex.org/T1"},
        {"level": "topic", "id": "https://openalex.org/T1\n"},
        {"level": "topic", "id": "https://openalex.org/T" + "1" * 100},
        {"level": "topic", "id": "https://openalex.org/T1", "extra": "value"},
    ],
)
def test_openalex_binding_rejects_malformed_or_mismatched_identity(value):
    with pytest.raises(ValidationError):
        RetrievalOpenAlexScope.model_validate(value, strict=True)


@pytest.mark.parametrize("version", ["2.0", "2.1", "2.2"])
@pytest.mark.parametrize(
    "scope", [None, {"level": "topic", "id": "https://openalex.org/T1"}]
)
def test_legacy_compiler_filters_reject_openalex_binding_even_null(version, scope):
    filters = request23().filters.model_dump(mode="json")
    with pytest.raises(ValidationError):
        request23(
            schema_version=f"evidence-retrieval/{version}",
            filters={**filters, "openalex_scope": scope},
        )


def _branches():
    return [
        {
            "action": "retrieve",
            "resolved_query": "Second basin tectonics",
            "query_plan": _planner_payload("Second basin tectonics").model_dump(),
        },
        {"action": "clarify", "clarification": OPTIONS},
        {
            "action": "needs_context",
            "needs_context": {"question": "Which second case?"},
        },
    ]


@pytest.mark.parametrize("wire", _branches())
def test_context_output_has_only_active_nonnull_branch(wire):
    assert (
        ContextQueryPlanningOutput.model_validate(wire, strict=True).root.action
        == wire["action"]
    )
    for field in ("query_plan", "clarification", "needs_context", "resolved_query"):
        invalid = deepcopy(wire)
        invalid[field] = None
        with pytest.raises(ValidationError):
            ContextQueryPlanningOutput.model_validate(invalid, strict=True)
    invalid = deepcopy(wire)
    invalid.pop(next(field for field in wire if field != "action"))
    with pytest.raises(ValidationError):
        ContextQueryPlanningOutput.model_validate(invalid, strict=True)


def test_context_tokens_are_closed_purpose_specific_and_bounded():
    payload = {
        "issued_at": 100,
        "expires_at": 1900,
        "request_binding_sha256": "a" * 64,
        "configuration_binding_sha256": "b" * 64,
        "context": {"supplements": ["  user detail\n"], "selected_query": None},
    }
    token = ContextTokenPayload.model_validate(payload, strict=True)
    assert token.context.supplements == ["  user detail\n"]
    with pytest.raises(ValidationError):
        ContextClarificationTokenPayload.model_validate(token.model_dump(), strict=True)
    with pytest.raises(ValidationError):
        ContextTokenPayload.model_validate({**payload, "expires_at": 1901}, strict=True)
    choice = ContextClarificationTokenPayload.model_validate(
        {**payload, "clarification": OPTIONS}
    )
    assert choice.audience.endswith(":context-clarification")
    with pytest.raises(ValidationError):
        ContextClarificationTokenPayload.model_validate(
            {
                **payload,
                "clarification": OPTIONS,
                "context": {"supplements": [], "selected_query": "selected task"},
            }
        )


def test_needs_context_precedes_every_channel_and_is_preview_only():
    skipped = {
        "attempted": False,
        "status": "skipped",
        "provider": "test",
        "returned": 0,
    }
    pending = {
        "schema_version": "evidence-retrieval/2.3",
        "stage": "preview",
        "result_status": "needs_context",
        "query_plan": None,
        "needs_context": {"question": "Which case?", "context_token": TOKEN},
        "channels": {
            name: deepcopy(skipped)
            for name in ("lexical", "dense", "rerank", "relation")
        },
        "preview": [],
        "clusters": [],
        "warnings": [],
    }
    assert (
        HybridSearchResponse.model_validate(pending).needs_context.question
        == "Which case?"
    )
    for updates in (
        {"schema_version": "evidence-retrieval/2.2"},
        {"schema_version": "evidence-retrieval/2.1"},
        {"stage": "ai_rerank"},
        {"needs_context": None},
        {"query_plan_handoff": "v1.key.payload." + "a" * 43},
    ):
        with pytest.raises(ValidationError):
            HybridSearchResponse.model_validate({**pending, **updates})
    for name in pending["channels"]:
        invalid = deepcopy(pending)
        invalid["channels"][name]["attempted"] = True
        with pytest.raises(ValidationError):
            HybridSearchResponse.model_validate(invalid)
