from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from typing import Annotated, Literal
from unittest.mock import Mock
from unittest.mock import call as mock_call

import httpx
import pytest
from conftest import raw_graph_payload
from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import StringConstraints

from compiler_service.config import (
    GPT_MAX_ATTEMPTS,
    GPT_MODEL,
)
from compiler_service.errors import (
    ContractIssue,
    ContractValidationError,
    ContractViolation,
    ModelAttemptDiagnostic,
    ModelOutputError,
    ModelRequestError,
    ModelRequestErrorKind,
    ModelSchemaIssue,
)
from compiler_service.graph_contract import PaperEvidenceGraph, VerificationReport
from compiler_service.model_client import (
    CompletionProfile,
    ModelJsonClient,
    _retry_feedback,
)
from compiler_service.models import StrictModel


class TinySchema(StrictModel):
    value: int


class RequiredNullableSchema(StrictModel):
    value: int
    unit: str | None


class SecretRejectingSchema(StrictModel):
    value: Annotated[str, StringConstraints(pattern=r"^safe$")]


class ManyErrorsSchema(StrictModel):
    values: list[int]


class EnumListSchema(StrictModel):
    kinds: list[Literal["claim", "observation"]]


class DiagnosticLocatorSchema(StrictModel):
    quote: str
    region: str | None


class DiagnosticAnchorSchema(StrictModel):
    locator: DiagnosticLocatorSchema


class DiagnosticObservationSchema(StrictModel):
    statement: str
    raw_value: str | None = None


class ContractPathSchema(StrictModel):
    anchors: list[DiagnosticAnchorSchema]
    observations: list[DiagnosticObservationSchema]


_PAPER_GRAPH_SCHEMA_OBSERVATION_PREFLIGHT_RULES = (
    "PaperEvidenceGraph preflight for the new graph",
    "build every Observation source-first",
    "trusted SourceAnchor with the shortest exact quote",
    "same complete subject/context, retained numbers and units",
    "ID in that Observation.anchor_ids",
    "direct grounds edge",
    "reliable cells/points with direct grounds to that Observation",
    "other-node or unlinked evidence never counts",
    "Audit statement and raw_value independently",
    "retain every grounded number in mixed fields",
    "number has no own source, remove only it",
    "complete qualitative fact with subject/context",
    "lossless grounded raw_value/unit or null both if none",
    "Never move, spell out, approximate/round",
    "range/ratio",
    "re-encode an unsupported number",
    "Preserve required Claim coverage and complete paths",
    "never invent facts",
)


def _assert_paper_graph_schema_observation_preflight(feedback: str) -> None:
    for rule in _PAPER_GRAPH_SCHEMA_OBSERVATION_PREFLIGHT_RULES:
        assert rule in feedback
    assert "Static scan proved" not in feedback
    assert "document_source_token_absent" not in feedback


class _Message:
    def __init__(self, content: str | None) -> None:
        self.content = content

    @property
    def reasoning_content(self) -> str:
        raise AssertionError("reasoning_content must never be read")


def _response(
    content: str | None,
    *,
    finish_reason: str | None = None,
    usage: object | None = None,
):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=_Message(content),
                finish_reason=finish_reason,
            )
        ],
        usage=usage,
    )


def _sdk(create: Mock) -> SimpleNamespace:
    return SimpleNamespace(create=create)


def _status_error(status: int) -> APIStatusError:
    request = httpx.Request("POST", "https://api.iiot-shxt.xyz/v1/responses")
    response = httpx.Response(status, request=request)
    return APIStatusError("redacted provider error", response=response, body=None)


def test_uses_fixed_responses_transport_arguments_without_output_cap() -> None:
    create = Mock(return_value=_response('{"value":7}'))
    sdk = _sdk(create)

    result = ModelJsonClient(sdk, sleep=lambda _: None).complete(
        system_prompt='Return JSON only: {"value":1}',
        user_content="paper",
        schema=TinySchema,
        profile=CompletionProfile(max_attempts=3),
    )

    assert result.value.value == 7
    kwargs = create.call_args.kwargs
    assert kwargs == {
        "model": GPT_MODEL,
        "timeout": None,
        "response_schema": TinySchema,
        "messages": [
            {"role": "system", "content": 'Return JSON only: {"value":1}'},
            {"role": "user", "content": "paper"},
        ],
    }
    assert not {
        "temperature",
        "top_p",
        "presence_penalty",
        "frequency_penalty",
        "max_tokens",
    }.intersection(kwargs)


def test_stage_profile_bounds_attempts() -> None:
    request = httpx.Request("POST", "https://api.iiot-shxt.xyz/v1/responses")
    create = Mock(
        side_effect=[
            APIConnectionError(request=request),
            _response('{"value":7}'),
        ]
    )
    sleep = Mock()

    result = ModelJsonClient(_sdk(create), sleep=sleep).complete(
        system_prompt="Return JSON",
        user_content="bounded stage",
        schema=TinySchema,
        profile=CompletionProfile(
            max_attempts=2,
        ),
    )

    assert result.attempts == 2
    assert create.call_count == 2
    assert result.diagnostics[0].request_error_kind is (
        ModelRequestErrorKind.CONNECTION
    )
    assert result.diagnostics[0].request_http_status is None
    assert sleep.call_args_list == [mock_call(1.0)] * 5
    for call in create.call_args_list:
        assert "reasoning_effort" not in call.kwargs
        assert "temperature" not in call.kwargs
        assert "max_tokens" not in call.kwargs
        assert call.kwargs["response_schema"] is TinySchema


@pytest.mark.parametrize(
    ("status", "kind", "code"),
    [
        (400, ModelRequestErrorKind.INVALID_REQUEST, "GPT_INVALID_REQUEST"),
        (
            401,
            ModelRequestErrorKind.AUTHENTICATION,
            "GPT_AUTHENTICATION_FAILED",
        ),
        (
            402,
            ModelRequestErrorKind.INSUFFICIENT_BALANCE,
            "GPT_INSUFFICIENT_BALANCE",
        ),
        (
            403,
            ModelRequestErrorKind.PERMISSION_DENIED,
            "GPT_PERMISSION_DENIED",
        ),
        (404, ModelRequestErrorKind.NOT_FOUND, "GPT_MODEL_NOT_FOUND"),
        (409, ModelRequestErrorKind.CONFLICT, "GPT_CONFLICT"),
        (
            422,
            ModelRequestErrorKind.INVALID_PARAMETERS,
            "GPT_INVALID_PARAMETERS",
        ),
        (418, ModelRequestErrorKind.HTTP_ERROR, "GPT_HTTP_ERROR"),
    ],
)
def test_permanent_http_request_failures_stop_with_specific_code(
    status: int,
    kind: ModelRequestErrorKind,
    code: str,
) -> None:
    create = Mock(side_effect=_status_error(status))
    sleep = Mock()

    with pytest.raises(ModelRequestError) as caught:
        ModelJsonClient(_sdk(create), sleep=sleep).complete(
            system_prompt="Return JSON",
            user_content="paper",
            schema=TinySchema,
            profile=CompletionProfile(max_attempts=3),
        )

    assert caught.value.kind is kind
    assert caught.value.http_status == status
    assert caught.value.code == code
    assert caught.value.retryable is False
    assert create.call_count == 1
    sleep.assert_not_called()
    assert caught.value.diagnostics[0].request_error_kind is kind
    assert caught.value.diagnostics[0].request_http_status == status


@pytest.mark.parametrize(
    ("errors", "kind", "code", "http_status"),
    [
        (
            [
                APITimeoutError(
                    httpx.Request("POST", "https://api.iiot-shxt.xyz/v1/responses")
                )
                for _ in range(3)
            ],
            ModelRequestErrorKind.TIMEOUT,
            "GPT_TIMEOUT",
            None,
        ),
        (
            [_status_error(429) for _ in range(3)],
            ModelRequestErrorKind.RATE_LIMITED,
            "GPT_RATE_LIMITED",
            429,
        ),
        (
            [_status_error(503) for _ in range(3)],
            ModelRequestErrorKind.PROVIDER_ERROR,
            "GPT_PROVIDER_ERROR",
            503,
        ),
    ],
)
def test_retryable_request_failures_use_two_bounded_backoffs(
    errors: list[Exception],
    kind: ModelRequestErrorKind,
    code: str,
    http_status: int | None,
) -> None:
    create = Mock(side_effect=errors)
    sleep = Mock()

    with pytest.raises(ModelRequestError) as caught:
        ModelJsonClient(_sdk(create), sleep=sleep).complete(
            system_prompt="Return JSON",
            user_content="paper",
            schema=TinySchema,
            profile=CompletionProfile(max_attempts=3),
        )

    assert caught.value.kind is kind
    assert caught.value.http_status == http_status
    assert caught.value.code == code
    assert caught.value.retryable is True
    assert create.call_count == 3
    assert sleep.call_args_list == [mock_call(1.0)] * 15
    assert [item.request_error_kind for item in caught.value.diagnostics] == [
        kind,
        kind,
        kind,
    ]


def test_unexpected_request_exception_is_not_retried() -> None:
    create = Mock(side_effect=RuntimeError("local sdk failure"))
    sleep = Mock()

    with pytest.raises(ModelRequestError) as caught:
        ModelJsonClient(_sdk(create), sleep=sleep).complete(
            system_prompt="Return JSON",
            user_content="paper",
            schema=TinySchema,
            profile=CompletionProfile(max_attempts=3),
        )

    assert caught.value.kind is ModelRequestErrorKind.UNEXPECTED
    assert caught.value.code == "GPT_UNEXPECTED_ERROR"
    assert caught.value.retryable is False
    assert create.call_count == 1
    sleep.assert_not_called()


@pytest.mark.parametrize("attempts", [True, 0, -1, 7, 1.5])
def test_stage_profile_rejects_invalid_attempt_budget(attempts):
    with pytest.raises(ValueError, match="configured boundary"):
        CompletionProfile(max_attempts=attempts)


def test_success_diagnostic_has_only_bounded_metadata() -> None:
    content = '{"value":7}'
    usage = SimpleNamespace(
        prompt_tokens=120,
        completion_tokens=31,
        total_tokens=151,
        prompt_cache_hit_tokens=80,
        prompt_cache_miss_tokens="not-an-integer",
        reasoning_tokens=999,
        secret="SECRET-USAGE-MARKER",
    )
    create = Mock(return_value=_response(content, finish_reason="stop", usage=usage))

    result = ModelJsonClient(_sdk(create), sleep=lambda _: None).complete(
        system_prompt="Return JSON",
        user_content="paper",
        schema=TinySchema,
        profile=CompletionProfile(max_attempts=3),
    )

    diagnostic = result.diagnostics[0]
    assert diagnostic.classification == "success"
    assert diagnostic.finish_reason == "stop"
    assert diagnostic.content_bytes == len(content.encode("utf-8"))
    assert diagnostic.content_sha256 == hashlib.sha256(content.encode()).hexdigest()
    assert diagnostic.usage is not None
    assert diagnostic.usage.model_dump(exclude_none=True) == {
        "prompt_tokens": 120,
        "completion_tokens": 31,
        "total_tokens": 151,
        "prompt_cache_hit_tokens": 80,
    }
    serialized = json.dumps(diagnostic.model_dump(mode="json"))
    assert "SECRET-USAGE-MARKER" not in serialized
    assert "reasoning_tokens" not in serialized


def test_retries_failures_with_exactly_three_total_attempts() -> None:
    create = Mock(
        side_effect=[
            _response(""),
            _response("{not json"),
            _response(json.dumps({"value": "not-an-int"})),
        ]
    )
    sdk = _sdk(create)

    assert GPT_MAX_ATTEMPTS == 6
    with pytest.raises(
        ModelOutputError,
        match=r"3 fresh attempts",
    ):
        ModelJsonClient(sdk, sleep=lambda _: None).complete(
            system_prompt="Return a JSON object",
            user_content="paper",
            schema=TinySchema,
            profile=CompletionProfile(max_attempts=3),
        )
    assert create.call_count == 3


def test_third_fresh_attempt_can_succeed_after_empty_and_invalid_json() -> None:
    create = Mock(
        side_effect=[_response(None), _response("[]"), _response('{"value":9}')]
    )
    sdk = _sdk(create)

    completion = ModelJsonClient(sdk, sleep=lambda _: None).complete(
        system_prompt="Return JSON",
        user_content="paper",
        schema=TinySchema,
        profile=CompletionProfile(max_attempts=3),
    )

    assert completion.attempts == 3
    assert completion.value.value == 9
    calls = [call.kwargs for call in create.call_args_list]
    assert all(call["response_schema"] is TinySchema for call in calls)


def test_json_recursion_failure_is_a_retryable_invalid_output(monkeypatch) -> None:
    from compiler_service import model_client

    original_loads = model_client.strict_json_loads
    decode_calls = 0

    def decode(content: str):
        nonlocal decode_calls
        decode_calls += 1
        if decode_calls == 1:
            raise RecursionError("nested JSON")
        return original_loads(content)

    monkeypatch.setattr(model_client, "strict_json_loads", decode)
    create = Mock(side_effect=[_response('{"value":10}'), _response('{"value":11}')])

    completion = ModelJsonClient(_sdk(create), sleep=lambda _: None).complete(
        system_prompt="Return JSON",
        user_content="paper",
        schema=TinySchema,
        profile=CompletionProfile(max_attempts=3),
    )

    assert completion.attempts == 2
    assert completion.value.value == 11
    assert completion.diagnostics[0].classification == "invalid_json"
    assert completion.diagnostics[0].json_error is not None
    assert completion.diagnostics[0].json_error.classification == ("strict_value_error")


def test_retry_feedback_is_bounded_safe_and_each_attempt_stays_fresh() -> None:
    paper_marker = "SECRET-PAPER-MARKER-DO-NOT-COPY"
    model_marker = "SECRET-MODEL-OUTPUT-MARKER-DO-NOT-COPY"
    system_prompt = "Return JSON using the authoritative schema."
    create = Mock(
        side_effect=[
            _response(
                '{"kinds":["' + model_marker,
                finish_reason="length",
            ),
            _response(
                json.dumps({"kinds": [model_marker] * 12}),
                finish_reason="stop",
            ),
            _response('{"kinds":["claim"]}', finish_reason="stop"),
        ]
    )

    completion = ModelJsonClient(_sdk(create), sleep=lambda _: None).complete(
        system_prompt=system_prompt,
        user_content=paper_marker,
        schema=EnumListSchema,
        profile=CompletionProfile(max_attempts=3),
    )

    assert completion.attempts == 3
    assert completion.value.kinds == ["claim"]
    calls = [call.kwargs for call in create.call_args_list]
    assert calls[0]["messages"] == [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": paper_marker},
    ]
    fixed_kwargs = {
        "model": GPT_MODEL,
        "timeout": None,
        "response_schema": EnumListSchema,
    }
    for kwargs in calls:
        assert {
            key: value for key, value in kwargs.items() if key != "messages"
        } == fixed_kwargs
        assert [message["role"] for message in kwargs["messages"]] == [
            "system",
            "user",
        ]
        assert kwargs["messages"][1]["content"] == paper_marker
        assert paper_marker not in kwargs["messages"][0]["content"]
        assert model_marker not in kwargs["messages"][0]["content"]

    second_system = calls[1]["messages"][0]["content"]
    assert second_system.startswith(system_prompt)
    assert "Use minimal internal reasoning" in second_system
    assert "within about 2000 tokens" in second_system
    assert "begin the final JSON object immediately" in second_system
    assert "one complete, closed JSON object" in second_system
    assert "Preserve every required key" in second_system
    assert "Nullable does not mean optional" in second_system
    assert "Previous output exhausted the token budget" in second_system
    assert "coverage of every materially qualifying Claim" in second_system
    assert "every distinct complete evidence path" in second_system
    assert "Do not omit an independent evidence path" in second_system
    assert "Reduce only optional payload" in second_system
    assert "Never drop a materially qualifying Claim" in second_system
    assert "Keep claim statements complete and non-telegraphic" in second_system
    assert "Emit minified JSON" in second_system
    assert "fewer claims" not in second_system
    assert "central-claim coverage" not in second_system
    assert "target 1-5" not in second_system.casefold()
    assert "every retained claim" not in second_system

    third_system = calls[2]["messages"][0]["content"]
    assert third_system.startswith(system_prompt)
    assert "begin the final JSON object immediately" in third_system
    assert "exhausted the token budget" not in third_system
    assert "exact authoritative schema value" in third_system
    assert third_system.count('"type":"literal_error"') == 8
    assert '"loc":["kinds",0]' in third_system
    assert '"loc":["kinds",7]' in third_system
    assert '"loc":["kinds",8]' not in third_system
    assert len(third_system) - len(system_prompt) <= 4_200


def test_missing_required_nullable_key_gets_explicit_null_feedback() -> None:
    system_prompt = "Return JSON using the authoritative schema."
    create = Mock(
        side_effect=[
            _response('{"value":7}', finish_reason="stop"),
            _response('{"value":7,"unit":null}', finish_reason="stop"),
        ]
    )

    completion = ModelJsonClient(_sdk(create), sleep=lambda _: None).complete(
        system_prompt=system_prompt,
        user_content="paper",
        schema=RequiredNullableSchema,
        profile=CompletionProfile(max_attempts=3),
    )

    assert completion.attempts == 2
    feedback = create.call_args_list[1].kwargs["messages"][0]["content"]
    assert '"loc":["unit"]' in feedback
    assert '"type":"missing"' in feedback
    assert "missing location names a required key" in feedback
    assert "nullable and no grounded value exists, emit null" in feedback
    assert "Nullable does not mean optional" in feedback
    assert "exact authoritative schema value" not in feedback


def test_paper_graph_schema_retry_adds_complete_history_free_preflight() -> None:
    long_name_a = "A" * 48
    long_name_b = "B" * 48
    long_error_type = "z" * 48
    diagnostic = ModelAttemptDiagnostic(
        attempt=2,
        classification="schema_validation_failed",
        finish_reason="stop",
        schema_errors=tuple(
            ModelSchemaIssue(
                loc=(long_name_a, 1_000_000, long_name_b, index),
                type=long_error_type,
            )
            for index in range(8)
        ),
    )

    feedback = _retry_feedback(diagnostic, schema=PaperEvidenceGraph)

    assert feedback is not None
    assert feedback.count(f'"type":"{long_error_type}"') == 8
    assert f'"loc":["{long_name_a}",1000000,"{long_name_b}",7]' in feedback
    _assert_paper_graph_schema_observation_preflight(feedback)
    assert feedback.index("Recheck each listed location") < feedback.index(
        _PAPER_GRAPH_SCHEMA_OBSERVATION_PREFLIGHT_RULES[0]
    )
    assert feedback.endswith("never invent facts.")
    assert len(feedback) <= 4_096
    assert len(feedback.encode("utf-8")) == len(feedback)


@pytest.mark.parametrize(
    "schema",
    [TinySchema, ContractPathSchema, VerificationReport],
)
def test_schema_retry_preflight_is_exactly_paper_graph_scoped(
    schema: type[StrictModel],
) -> None:
    diagnostic = ModelAttemptDiagnostic(
        attempt=1,
        classification="schema_validation_failed",
        finish_reason="stop",
        schema_errors=(ModelSchemaIssue(loc=("value",), type="literal_error"),),
    )

    feedback = _retry_feedback(diagnostic, schema=schema)

    assert feedback == _retry_feedback(diagnostic)
    assert feedback is not None
    assert "exact authoritative schema value" in feedback
    assert "PaperEvidenceGraph preflight" not in feedback


def test_schema_and_contract_failure_summaries_never_echo_model_content() -> None:
    marker = "SECRET-PAPER-MARKER-DO-NOT-LEAK"
    schema_create = Mock(return_value=_response(json.dumps({"value": marker})))
    with pytest.raises(ModelOutputError) as schema_error:
        ModelJsonClient(_sdk(schema_create), sleep=lambda _: None).complete(
            system_prompt="Return JSON",
            user_content="paper",
            schema=SecretRejectingSchema,
            profile=CompletionProfile(max_attempts=3),
        )
    assert marker not in str(schema_error.value)
    assert "schema_validation_failed" in str(schema_error.value)
    assert len(str(schema_error.value)) <= 3_900
    assert all(item.schema_errors for item in schema_error.value.diagnostics)
    diagnostics_json = json.dumps(
        [item.model_dump(mode="json") for item in schema_error.value.diagnostics]
    )
    assert marker not in diagnostics_json
    assert '"loc": ["value"]' in diagnostics_json
    assert '"type": "string_pattern_mismatch"' in diagnostics_json
    assert "input" not in diagnostics_json
    assert "ctx" not in diagnostics_json
    assert "url" not in diagnostics_json

    valid_create = Mock(return_value=_response('{"value":7}'))

    def reject(_: TinySchema) -> None:
        raise ValueError(marker)

    with pytest.raises(ModelOutputError) as contract_error:
        ModelJsonClient(_sdk(valid_create), sleep=lambda _: None).complete(
            system_prompt="Return JSON",
            user_content="paper",
            schema=TinySchema,
            validate=reject,
            profile=CompletionProfile(max_attempts=3),
        )
    assert marker not in str(contract_error.value)
    assert "contract_validation_failed" in str(contract_error.value)
    contract_diagnostics = json.dumps(
        [item.model_dump(mode="json") for item in contract_error.value.diagnostics]
    )
    assert marker not in contract_diagnostics
    assert all(item.contract_issue is None for item in contract_error.value.diagnostics)
    assert all(
        marker not in call.kwargs["messages"][0]["content"]
        for call in valid_create.call_args_list
    )


def test_allowlisted_contract_issue_is_diagnostic_and_retry_feedback_only() -> None:
    marker = "SECRET-CONTRACT-MESSAGE-DO-NOT-LEAK"
    create = Mock(return_value=_response('{"value":7}'))

    def reject(_: TinySchema) -> None:
        error = ContractValidationError(
            ContractIssue.PROVENANCE_QUOTE_NOT_FOUND,
        )
        error.__cause__ = ValueError(marker)
        raise error

    with pytest.raises(ModelOutputError) as caught:
        ModelJsonClient(_sdk(create), sleep=lambda _: None).complete(
            system_prompt="Return JSON",
            user_content="paper",
            schema=TinySchema,
            validate=reject,
            profile=CompletionProfile(max_attempts=3),
        )

    assert all(
        item.contract_issue is ContractIssue.PROVENANCE_QUOTE_NOT_FOUND
        for item in caught.value.diagnostics
    )
    assert all(item.contract_loc == () for item in caught.value.diagnostics)
    serialized = json.dumps(
        [item.model_dump(mode="json") for item in caught.value.diagnostics]
    )
    assert marker not in serialized
    assert marker not in str(caught.value)
    assert "provenance_quote_not_found" in serialized
    calls = [call.kwargs for call in create.call_args_list]
    assert "provenance_quote_not_found" not in calls[0]["messages"][0]["content"]
    assert all(
        "Safe issue code: provenance_quote_not_found." in call["messages"][0]["content"]
        for call in calls[1:]
    )
    assert all(
        "Safe failing field path" not in call["messages"][0]["content"]
        for call in calls[1:]
    )
    assert all(marker not in call["messages"][0]["content"] for call in calls)


@pytest.mark.parametrize(
    ("issue", "loc", "directed_feedback"),
    [
        (
            ContractIssue.PROVENANCE_QUOTE_NOT_FOUND,
            ("anchors", 0, "locator", "quote"),
            "locator.quote must be copied exactly",
        ),
        (
            ContractIssue.PROVENANCE_REGION_MISMATCH,
            ("anchors", 0, "locator", "region"),
            "locator.region must use the exact normalized bounding box",
        ),
        (
            ContractIssue.OBSERVATION_PATH_OWNERSHIP,
            ("observations", 0),
            "exactly one outgoing premise_for edge",
        ),
    ],
)
def test_contract_retry_feedback_includes_allowlisted_issue_and_safe_loc(
    issue: ContractIssue,
    loc: tuple[str | int, ...],
    directed_feedback: str,
) -> None:
    system_prompt = "Return JSON using the authoritative schema."
    payload = json.dumps(
        {
            "anchors": [{"locator": {"quote": "safe", "region": None}}],
            "observations": [{"statement": "safe"}],
        }
    )
    create = Mock(return_value=_response(payload, finish_reason="stop"))

    def reject(_: ContractPathSchema) -> None:
        raise ContractValidationError(issue, loc=loc)

    with pytest.raises(ModelOutputError) as caught:
        ModelJsonClient(_sdk(create), sleep=lambda _: None).complete(
            system_prompt=system_prompt,
            user_content="paper",
            schema=ContractPathSchema,
            validate=reject,
            profile=CompletionProfile(max_attempts=3),
        )

    assert all(item.contract_loc == loc for item in caught.value.diagnostics)
    calls = [call.kwargs for call in create.call_args_list]
    assert calls[0]["messages"] == [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "paper"},
    ]
    expected_diagnostic = json.dumps(
        {"issue": issue.value, "loc": list(loc)},
        ensure_ascii=True,
        separators=(",", ":"),
    )
    for call in calls[1:]:
        feedback = call["messages"][0]["content"]
        assert expected_diagnostic in feedback
        assert directed_feedback in feedback
        assert len(feedback) - len(system_prompt) <= 4_200


def test_contract_retry_groups_distinct_issues_from_only_current_output() -> None:
    model_marker = "SECRET-REJECTED-MODEL-VALUE-MUST-NOT-LEAK"
    system_prompt = "Return JSON using the authoritative schema."
    payload = json.dumps(
        {
            "anchors": [{"locator": {"quote": model_marker, "region": "normalized"}}],
            "observations": [{"statement": "safe"}],
        }
    )
    create = Mock(return_value=_response(payload, finish_reason="stop"))

    def reject_first(_: ContractPathSchema) -> None:
        if create.call_count != 1:
            return
        raise ContractValidationError.from_violations(
            (
                ContractViolation(
                    ContractIssue.PROVENANCE_REGION_MISMATCH,
                    ("anchors", 0, "locator", "region"),
                ),
                ContractViolation(
                    ContractIssue.PROVENANCE_QUOTE_NOT_FOUND,
                    ("anchors", 0, "locator", "quote"),
                ),
                ContractViolation(
                    ContractIssue.OBSERVATION_PATH_OWNERSHIP,
                    ("observations", 0),
                ),
                ContractViolation(
                    ContractIssue.PROVENANCE_REGION_MISMATCH,
                    ("anchors", 999, "locator", "region"),
                ),
            )
        )

    completion = ModelJsonClient(_sdk(create), sleep=lambda _: None).complete(
        system_prompt=system_prompt,
        user_content="paper",
        schema=ContractPathSchema,
        validate=reject_first,
        profile=CompletionProfile(max_attempts=3),
    )

    assert completion.attempts == 2
    diagnostic = completion.diagnostics[0]
    assert diagnostic.contract_issue is ContractIssue.PROVENANCE_REGION_MISMATCH
    assert diagnostic.contract_loc == ("anchors", 0, "locator", "region")
    # One located witness per distinct rule; the repeated region rule is dropped.
    assert [item.issue for item in diagnostic.contract_issues] == [
        ContractIssue.PROVENANCE_REGION_MISMATCH,
        ContractIssue.PROVENANCE_QUOTE_NOT_FOUND,
        ContractIssue.OBSERVATION_PATH_OWNERSHIP,
    ]
    feedback = create.call_args_list[1].kwargs["messages"][0]["content"]
    assert "provenance_region_mismatch" in feedback
    assert "provenance_quote_not_found" in feedback
    assert "observation_path_ownership" in feedback
    assert '"loc":["anchors",0,"locator","region"]' in feedback
    assert '"loc":["anchors",0,"locator","quote"]' in feedback
    assert '"loc":["observations",0]' in feedback
    assert '"loc":["anchors",999' not in feedback
    assert "Only last-output safe diagnostics" in feedback
    assert "Unstable loc witnesses; audit all rules across full new graph" in feedback
    assert (
        feedback.index("For every anchor, use the exact normalized bounding box")
        < feedback.index("For every anchor, copy locator.quote exactly")
        < feedback.index("For every observation, emit exactly one outgoing premise_for")
    )
    assert model_marker not in feedback
    assert len(feedback) - len(system_prompt) <= 4_096


def test_contract_issue_group_is_deduplicated_and_capped_at_four() -> None:
    marker = "SECRET-FIFTH-LOCATION-MUST-NOT-LEAK"
    create = Mock(return_value=_response('{"value":7}', finish_reason="stop"))
    violations = (
        ContractViolation(ContractIssue.PROVENANCE_QUOTE_NOT_FOUND, ("value",)),
        ContractViolation(ContractIssue.PROVENANCE_QUOTE_NOT_FOUND, (marker,)),
        ContractViolation(ContractIssue.PROVENANCE_REGION_MISMATCH, ("value",)),
        ContractViolation(ContractIssue.PROVENANCE_PAGE_INVALID, ("value",)),
        ContractViolation(ContractIssue.OBSERVATION_PATH_OWNERSHIP, ("value",)),
        ContractViolation(ContractIssue.PROVENANCE_PAPER_ID_MISMATCH, (marker,)),
    )
    distinct = [violations[index].issue for index in (0, 2, 3, 4)]

    def reject(_: TinySchema) -> None:
        raise ContractValidationError.from_violations(violations)

    with pytest.raises(ModelOutputError) as caught:
        ModelJsonClient(_sdk(create), sleep=lambda _: None).complete(
            system_prompt="Return JSON",
            user_content="paper",
            schema=TinySchema,
            validate=reject,
            profile=CompletionProfile(max_attempts=3),
        )

    assert all(
        len(diagnostic.contract_issues) == 4 for diagnostic in caught.value.diagnostics
    )
    assert all(
        [item.issue for item in diagnostic.contract_issues] == distinct
        for diagnostic in caught.value.diagnostics
    )
    assert marker not in str(caught.value)
    assert all(
        marker not in call.kwargs["messages"][0]["content"]
        for call in create.call_args_list
    )


def test_grouped_contract_feedback_is_not_accumulated_after_schema_failure() -> None:
    valid = {
        "anchors": [{"locator": {"quote": "safe", "region": None}}],
        "observations": [{"statement": "safe"}],
    }
    schema_invalid = {**valid, "unexpected": True}
    create = Mock(
        side_effect=[
            _response(json.dumps(valid), finish_reason="stop"),
            _response(json.dumps(schema_invalid), finish_reason="stop"),
            _response(json.dumps(valid), finish_reason="stop"),
        ]
    )

    def reject_first(_: ContractPathSchema) -> None:
        if create.call_count == 1:
            raise ContractValidationError.from_violations(
                (
                    ContractViolation(
                        ContractIssue.PROVENANCE_REGION_MISMATCH,
                        ("anchors", 0, "locator", "region"),
                    ),
                    ContractViolation(
                        ContractIssue.PROVENANCE_QUOTE_NOT_FOUND,
                        ("anchors", 0, "locator", "quote"),
                    ),
                )
            )

    completion = ModelJsonClient(_sdk(create), sleep=lambda _: None).complete(
        system_prompt="Return JSON",
        user_content="paper",
        schema=ContractPathSchema,
        validate=reject_first,
        profile=CompletionProfile(max_attempts=3),
    )

    assert completion.attempts == 3
    second_feedback = create.call_args_list[1].kwargs["messages"][0]["content"]
    assert "provenance_region_mismatch" in second_feedback
    assert "provenance_quote_not_found" in second_feedback
    assert "use the exact normalized bounding box" in second_feedback
    assert "copy locator.quote exactly" in second_feedback
    third_feedback = create.call_args_list[2].kwargs["messages"][0]["content"]
    assert "extra_forbidden" in third_feedback
    assert "provenance_region_mismatch" not in third_feedback
    assert "provenance_quote_not_found" not in third_feedback
    assert "use the exact normalized bounding box" not in third_feedback
    assert "copy locator.quote exactly" not in third_feedback


def test_parse_stage_contract_issue_takes_the_located_retry_path() -> None:
    create = Mock(return_value=_response('{"value":7}', finish_reason="stop"))

    def parse(_: dict) -> TinySchema:
        raise ContractValidationError(
            ContractIssue.GRAPH_REFERENCE_UNKNOWN, loc=("value",)
        )

    with pytest.raises(ModelOutputError) as caught:
        ModelJsonClient(_sdk(create), sleep=lambda _: None).complete(
            system_prompt="Return JSON",
            user_content="paper",
            schema=TinySchema,
            parse_payload=parse,
            profile=CompletionProfile(max_attempts=2),
        )

    assert [
        (item.classification, item.contract_issue, item.contract_loc)
        for item in caught.value.diagnostics
    ] == [
        (
            "contract_validation_failed",
            ContractIssue.GRAPH_REFERENCE_UNKNOWN,
            ("value",),
        )
    ] * 2
    feedback = create.call_args_list[1].kwargs["messages"][0]["content"]
    assert "Safe issue code: graph_reference_unknown." in feedback


def test_graph_structure_failure_reaches_the_model_located(artifact) -> None:
    payload = raw_graph_payload(artifact)
    payload["edges"][3]["target_id"] = "claim.missing"
    create = Mock(return_value=_response(json.dumps(payload), finish_reason="stop"))

    with pytest.raises(ModelOutputError) as caught:
        ModelJsonClient(_sdk(create), sleep=lambda _: None).complete(
            system_prompt="Return JSON",
            user_content="paper",
            schema=PaperEvidenceGraph,
            profile=CompletionProfile(max_attempts=2),
        )

    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.classification == "contract_validation_failed"
    assert diagnostic.contract_issue is ContractIssue.GRAPH_REFERENCE_UNKNOWN
    assert diagnostic.contract_loc == ("edges", 3, "target_id")
    feedback = create.call_args_list[1].kwargs["messages"][0]["content"]
    assert (
        '{"issue":"graph_reference_unknown","loc":["edges",3,"target_id"]}'
        in feedback
    )
    assert "value_error" not in feedback
    assert "claim.missing" not in feedback


def test_contract_loc_is_capped_and_unknown_parts_never_leak() -> None:
    marker = "SECRET-LOC-VALUE-MUST-NOT-LEAK"
    payload = json.dumps(
        {
            "anchors": [{"locator": {"quote": "safe", "region": None}}],
            "observations": [{"statement": "safe"}],
        }
    )
    create = Mock(return_value=_response(payload, finish_reason="stop"))

    def reject(_: ContractPathSchema) -> None:
        raise ContractValidationError(
            ContractIssue.PROVENANCE_QUOTE_NOT_FOUND,
            loc=("anchors", 0, marker, -1, "quote"),
        )

    with pytest.raises(ModelOutputError) as caught:
        ModelJsonClient(_sdk(create), sleep=lambda _: None).complete(
            system_prompt="Return JSON",
            user_content="paper",
            schema=ContractPathSchema,
            validate=reject,
            profile=CompletionProfile(max_attempts=3),
        )

    assert all(
        item.contract_loc == ("anchors", 0, "<key>", "<key>")
        for item in caught.value.diagnostics
    )
    serialized = json.dumps(
        [item.model_dump(mode="json") for item in caught.value.diagnostics]
    )
    assert marker not in serialized
    assert marker not in str(caught.value)
    for call in create.call_args_list:
        assert marker not in call.kwargs["messages"][0]["content"]
    assert (
        '"loc":["anchors",0,"<key>","<key>"]'
        in create.call_args_list[1].kwargs["messages"][0]["content"]
    )


@pytest.mark.parametrize(
    "issue",
    [
        ContractIssue.CANONICAL_PATH_INVALID,
        ContractIssue.VERIFICATION_EVIDENCE_OUTSIDE_PATH,
    ],
)
def test_generic_contract_retry_is_schema_neutral_and_preserves_coverage(
    issue: ContractIssue,
) -> None:
    create = Mock(return_value=_response('{"value":7}', finish_reason="stop"))

    def reject(_: TinySchema) -> None:
        raise ContractValidationError(
            issue,
            loc=("value",),
        )

    with pytest.raises(ModelOutputError):
        ModelJsonClient(_sdk(create), sleep=lambda _: None).complete(
            system_prompt="Return JSON",
            user_content="paper",
            schema=TinySchema,
            validate=reject,
            profile=CompletionProfile(max_attempts=3),
        )

    for call in create.call_args_list[1:]:
        feedback = call.kwargs["messages"][0]["content"]
        assert "authoritative response schema for this request" in feedback
        assert "Preserve all required claim or assessment coverage" in feedback
        assert "every complete evidence path" in feedback
        assert "compact graph" not in feedback
        assert "1-4 central claims" not in feedback
        assert "at most 12 observations" not in feedback
        assert "Fix the existing compact graph" not in feedback
        assert len(feedback.removeprefix("Return JSON")) <= 4_096


def test_retry_uses_only_immediately_previous_contract_diagnostic() -> None:
    marker = "SECRET-CONTRACT-CONTENT-MUST-NOT-LEAK"
    create = Mock(return_value=_response('{"value":7}', finish_reason="stop"))

    def reject_until_third(_: TinySchema) -> None:
        if create.call_count == 1:
            raise ContractValidationError(
                ContractIssue.PROVENANCE_REGION_MISMATCH,
            )
        if create.call_count == 2:
            error = ContractValidationError(
                ContractIssue.OBSERVATION_PATH_OWNERSHIP,
            )
            error.__cause__ = ValueError(marker)
            raise error

    completion = ModelJsonClient(_sdk(create), sleep=lambda _: None).complete(
        system_prompt="Return JSON",
        user_content="paper",
        schema=TinySchema,
        validate=reject_until_third,
        profile=CompletionProfile(max_attempts=3),
    )

    assert completion.attempts == 3
    final_feedback = create.call_args_list[2].kwargs["messages"][0]["content"]
    assert "observation_path_ownership" in final_feedback
    assert "provenance_region_mismatch" not in final_feedback
    assert "A prior attempt" not in final_feedback
    assert "exactly one outgoing premise_for edge" in final_feedback
    assert "begin the final JSON object immediately" in final_feedback
    assert marker not in final_feedback


def test_contract_retry_that_later_truncates_uses_only_length_correction() -> None:
    create = Mock(
        side_effect=[
            _response('{"value":7}', finish_reason="stop"),
            _response('{"value":', finish_reason="length"),
            _response('{"value":9}', finish_reason="stop"),
        ]
    )

    def reject_first(_: TinySchema) -> None:
        if create.call_count == 1:
            raise ContractValidationError(
                ContractIssue.PROVENANCE_QUOTE_NOT_FOUND,
                loc=("value",),
            )

    completion = ModelJsonClient(_sdk(create), sleep=lambda _: None).complete(
        system_prompt="Return JSON",
        user_content="paper",
        schema=TinySchema,
        validate=reject_first,
        profile=CompletionProfile(max_attempts=3),
    )

    assert completion.attempts == 3
    second_feedback = create.call_args_list[1].kwargs["messages"][0]["content"]
    assert "provenance_quote_not_found" in second_feedback
    assert "audit every anchor" in second_feedback
    third_feedback = create.call_args_list[2].kwargs["messages"][0]["content"]
    assert "exhausted the token budget" in third_feedback
    assert "provenance_quote_not_found" not in third_feedback
    assert "begin the final JSON object immediately" in third_feedback


def test_schema_diagnostics_are_capped_and_never_retain_rejected_inputs() -> None:
    marker = "SECRET-PYDANTIC-INPUT-DO-NOT-LEAK"
    content = json.dumps({"values": [f"{marker}-{index}" for index in range(12)]})
    create = Mock(return_value=_response(content, finish_reason="stop"))

    with pytest.raises(ModelOutputError) as caught:
        ModelJsonClient(_sdk(create), sleep=lambda _: None).complete(
            system_prompt="Return JSON",
            user_content="paper",
            schema=ManyErrorsSchema,
            profile=CompletionProfile(max_attempts=3),
        )

    assert len(caught.value.diagnostics) == 3
    assert all(len(item.schema_errors) == 8 for item in caught.value.diagnostics)
    serialized = json.dumps(
        [item.model_dump(mode="json") for item in caught.value.diagnostics]
    )
    assert marker not in serialized
    assert marker not in str(caught.value)


def test_length_finish_reason_is_classified_as_truncated_json() -> None:
    marker = "SECRET-TRUNCATED-CONTENT-DO-NOT-LEAK"
    content = '{"value":"' + marker
    usage = SimpleNamespace(
        prompt_tokens=17,
        completion_tokens=32,
        total_tokens=49,
        prompt_cache_hit_tokens=None,
        prompt_cache_miss_tokens=None,
    )
    create = Mock(return_value=_response(content, finish_reason="length", usage=usage))

    with pytest.raises(ModelOutputError) as caught:
        ModelJsonClient(_sdk(create), sleep=lambda _: None).complete(
            system_prompt="Return JSON",
            user_content="paper",
            schema=TinySchema,
            profile=CompletionProfile(max_attempts=3),
        )

    assert all(
        item.classification == "truncated_json" for item in caught.value.diagnostics
    )
    assert all(
        item.json_error is not None
        and item.json_error.classification == "truncated_json"
        and isinstance(item.json_error.pos, int)
        for item in caught.value.diagnostics
    )
    assert marker not in str(caught.value)


def test_strict_json_diagnostic_records_classification_and_position_only() -> None:
    marker = "SECRET-INVALID-JSON-DO-NOT-LEAK"
    content = '{"value":' + marker
    create = Mock(return_value=_response(content, finish_reason="stop"))

    with pytest.raises(ModelOutputError) as caught:
        ModelJsonClient(_sdk(create), sleep=lambda _: None).complete(
            system_prompt="Return JSON",
            user_content="paper",
            schema=TinySchema,
            profile=CompletionProfile(max_attempts=3),
        )

    assert all(
        item.classification == "invalid_json" for item in caught.value.diagnostics
    )
    assert all(
        item.json_error is not None
        and item.json_error.classification == "syntax_error"
        and item.json_error.pos == len('{"value":')
        for item in caught.value.diagnostics
    )
    assert marker not in str(caught.value)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_non_standard_json_numbers_are_rejected(constant: str) -> None:
    create = Mock(return_value=_response('{"value":' + constant + "}"))

    with pytest.raises(ModelOutputError) as error:
        ModelJsonClient(_sdk(create), sleep=lambda _: None).complete(
            system_prompt="Return JSON",
            user_content="paper",
            schema=TinySchema,
            profile=CompletionProfile(max_attempts=3),
        )

    assert create.call_count == 3
    assert "invalid_json" in str(error.value)


@pytest.mark.parametrize("failure", [429, 500])
def test_fallback_runs_only_after_six_failed_gpt_attempts(failure):
    from compiler_service.config import DEEPSEEK_MODEL

    primary = Mock(
        side_effect=_status_error(failure) if isinstance(failure, int) else None,
        return_value=_response("bad json" if failure == "json" else '{"value":"bad"}'),
    )
    fallback = Mock(return_value=_response('{"value":7}'))
    client = ModelJsonClient(
        _sdk(primary), fallback_client=_sdk(fallback), sleep=lambda _: None
    )
    result = client.complete(
        system_prompt="Return JSON", user_content="paper", schema=TinySchema
    )
    assert primary.call_count == 6
    assert fallback.call_count == 1
    assert result.attempts == 7
    assert result.model == DEEPSEEK_MODEL
    assert result.value.value == 7
    assert len(result.diagnostics) == 7
    assert fallback.call_args.kwargs["model"] == DEEPSEEK_MODEL


@pytest.mark.parametrize("content", ["bad json", '{"value":"bad"}', '{"value":7}'])
def test_output_rejections_never_activate_fallback(content):
    primary = Mock(return_value=_response(content))
    fallback = Mock()

    def validate(value):
        raise ContractValidationError(ContractIssue.PROVENANCE_SOURCE_REFERENCE_INVALID)

    with pytest.raises(ModelOutputError):
        ModelJsonClient(
            _sdk(primary), fallback_client=_sdk(fallback), sleep=lambda _: None
        ).complete(
            system_prompt="Return JSON",
            user_content="paper",
            schema=TinySchema,
            validate=validate,
        )
    assert primary.call_count == 6
    fallback.assert_not_called()


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 422])
def test_nontransient_failure_stops_even_with_fallback(status):
    primary = Mock(side_effect=_status_error(status))
    fallback = Mock()
    with pytest.raises(ModelRequestError):
        ModelJsonClient(
            _sdk(primary), fallback_client=_sdk(fallback), sleep=lambda _: None
        ).complete(system_prompt="Return JSON", user_content="paper", schema=TinySchema)
    assert primary.call_count == 1
    fallback.assert_not_called()


def test_mixed_output_and_network_failures_stay_on_gpt():
    primary = Mock(side_effect=[_response("bad json"), *[_status_error(500)] * 5])
    fallback = Mock()
    with pytest.raises(ModelOutputError):
        ModelJsonClient(
            _sdk(primary), fallback_client=_sdk(fallback), sleep=lambda _: None
        ).complete(system_prompt="Return JSON", user_content="paper", schema=TinySchema)
    fallback.assert_not_called()


def test_transient_backoff_is_bounded_and_recovery_logged(caplog):
    primary = Mock(side_effect=[*[_status_error(500)] * 5, _response('{"value":7}')])
    sleep = Mock()
    ModelJsonClient(_sdk(primary), sleep=sleep).complete(
        system_prompt="Return JSON", user_content="paper", schema=TinySchema
    )
    assert sleep.call_count == 5 + 10 + 20 + 40 + 60
    assert "Model recovered" in caplog.text


def test_gpt_success_does_not_call_fallback_and_next_stage_starts_with_gpt():
    primary = Mock(side_effect=[*[_status_error(500)] * 6, _response('{"value":8}')])
    fallback = Mock(return_value=_response('{"value":7}'))
    client = ModelJsonClient(
        _sdk(primary), fallback_client=_sdk(fallback), sleep=lambda _: None
    )
    first = client.complete(
        system_prompt="Return JSON", user_content="paper", schema=TinySchema
    )
    second = client.complete(
        system_prompt="Return JSON", user_content="verify", schema=TinySchema
    )
    assert first.attempts == 7
    assert second.attempts == 1
    assert second.model == GPT_MODEL
    assert primary.call_count == 7
    assert fallback.call_count == 1


def test_fallback_output_still_passes_schema_and_contract_validation():
    primary = Mock(side_effect=_status_error(500))
    fallback = Mock(
        side_effect=[
            _response('{"value":"wrong"}'),
            _response('{"value":7}'),
            _response('{"value":8}'),
        ]
    )
    client = ModelJsonClient(
        _sdk(primary), fallback_client=_sdk(fallback), sleep=lambda _: None
    )

    def validate(value):
        if value.value != 8:
            raise ValueError("Rejected by the shared contract")

    result = client.complete(
        system_prompt="Return JSON",
        user_content="paper",
        schema=TinySchema,
        validate=validate,
    )
    assert result.attempts == 9
    assert [d.classification for d in result.diagnostics[-3:]] == [
        "schema_validation_failed",
        "contract_validation_failed",
        "success",
    ]


def test_fallback_retries_are_bounded_and_preserved_by_job_control():
    from compiler_service.job_control import ModelJobControl

    primary = Mock(side_effect=_status_error(500))
    fallback = Mock(side_effect=_status_error(500))
    control = ModelJobControl()
    client = ModelJsonClient(
        _sdk(primary), fallback_client=_sdk(fallback), sleep=lambda _: None
    ).with_job_control(control)
    for _ in range(2):
        with pytest.raises(ModelRequestError):
            client.complete(
                system_prompt="Return JSON", user_content="paper", schema=TinySchema
            )
    assert primary.call_count == 12
    assert fallback.call_count == 6
    assert control.usage.request_attempts == 18


def test_factory_sets_application_user_agent_on_both_clients():
    client = ModelJsonClient.from_api_key("test-gpt", deepseek_api_key="test-deepseek")
    assert client._client.sdk.default_headers["User-Agent"] == "DeepShovel/1.0"
    assert client._fallback_client.sdk.default_headers["User-Agent"] == "DeepShovel/1.0"
    assert client._client.sdk.max_retries == 0
    assert client._fallback_client.sdk.max_retries == 0
    client._client.sdk.close()
    client._fallback_client.sdk.close()


def test_deepseek_transport_supplies_schema_without_mutating_messages():
    from compiler_service.model_client import DeepSeekJsonTransport

    create = Mock(return_value=_response('{"value":7}'))
    sdk = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    messages = [
        {"role": "system", "content": "Return JSON"},
        {"role": "user", "content": "paper"},
    ]
    DeepSeekJsonTransport(sdk).create(
        model="deepseek-v4-flash",
        messages=messages,
        response_schema=TinySchema,
        timeout=None,
    )
    sent = create.call_args.kwargs
    assert sent["response_format"] == {"type": "json_object"}
    assert '"required": ["value"]' in sent["messages"][0]["content"]
    assert messages[0]["content"] == "Return JSON"
    assert sent["timeout"] is None


def test_cancellation_after_gpt_failures_never_starts_fallback():
    from compiler_service.errors import JobCancelled
    from compiler_service.job_control import ModelJobControl

    primary = Mock(side_effect=_status_error(500))
    fallback = Mock(return_value=_response('{"value":7}'))

    def check_cancelled():
        if primary.call_count == 6:
            raise JobCancelled("cancelled")

    client = ModelJsonClient(
        _sdk(primary), fallback_client=_sdk(fallback), sleep=lambda _: None
    ).with_job_control(ModelJobControl(check_cancelled=check_cancelled))
    with pytest.raises(JobCancelled):
        client.complete(
            system_prompt="Return JSON", user_content="paper", schema=TinySchema
        )
    assert primary.call_count == 6
    fallback.assert_not_called()


def test_fallback_verifier_identity_is_assigned_before_contract_validation():
    class VerifierIdentity(StrictModel):
        verifier_model: Literal["gpt-5.6-luna", "deepseek-v4-flash"]

    primary = Mock(side_effect=_status_error(500))
    fallback = Mock(return_value=_response('{"verifier_model":"gpt-5.6-luna"}'))
    validate = Mock()
    result = ModelJsonClient(
        _sdk(primary), fallback_client=_sdk(fallback), sleep=lambda _: None
    ).complete(
        system_prompt="Return JSON",
        user_content="paper",
        schema=VerifierIdentity,
        validate=validate,
    )
    assert result.value.verifier_model == result.model == "deepseek-v4-flash"
    assert validate.call_args.args[0].verifier_model == "deepseek-v4-flash"


def test_fallback_notification_is_once_per_activation_not_per_retry():
    from compiler_service.job_control import ModelJobControl

    primary = Mock(side_effect=[*[_status_error(500)] * 6, _response('{"value":8}')])
    fallback = Mock(side_effect=[_status_error(500), _response('{"value":7}')])
    notification = Mock()
    client = ModelJsonClient(
        _sdk(primary),
        fallback_client=_sdk(fallback),
        on_fallback=notification,
        sleep=lambda _: None,
    ).with_job_control(ModelJobControl())
    client.complete(
        system_prompt="Return JSON", user_content="paper", schema=TinySchema
    )
    notification.assert_called_once_with()
    client.complete(
        system_prompt="Return JSON", user_content="verify", schema=TinySchema
    )
    notification.assert_called_once_with()
