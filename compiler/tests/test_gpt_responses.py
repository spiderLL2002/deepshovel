"""Offline regression coverage for the streamed Responses completion boundary."""

import json
from types import SimpleNamespace as NS

import httpx
import pytest
from openai import PermissionDeniedError
from pydantic import BaseModel, ConfigDict

from compiler_service.errors import ModelOutputError, ModelRequestError
from compiler_service.gpt_responses import GptResponsesTransport
from compiler_service.job_control import ModelJobControl
from compiler_service.model_client import ModelJsonClient


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    count: int


@pytest.mark.parametrize(
    "schema_name,effort",
    [
        ("PaperEvidenceGraph", "max"),
        ("ContextQueryPlanningOutput", "medium"),
        ("ClaimNormalizationDraftBatch", "medium"),
        ("RelationDecisionBatch", "high"),
        ("CitationAssessmentBatch", "high"),
    ],
)
def test_stage_effort_and_safe_usage_metadata(schema_name, effort):
    schema = type(schema_name, (Answer,), {})
    event = completed_event(
        [NS(type="message", content=[NS(type="output_text", text='{"count":9}')])]
    )
    event.response.usage.input_tokens_details = NS(cached_tokens=3)
    event.response.usage.output_tokens_details = NS(reasoning_tokens=2)
    sdk = FakeSDK([event])
    logs = []
    transport = GptResponsesTransport(sdk, observer=lambda m, _: logs.append(m))
    transport.create(
        response_schema=schema,
        model="gpt-5.6-luna",
        messages=[
            {"role": "system", "content": "JSON"},
            {"role": "user", "content": "Count"},
        ],
    )
    assert sdk.calls[0]["reasoning"] == {"effort": effort}
    assert logs[0]["reasoning_effort"] == effort
    assert logs[0]["usage_details"] == {
        "cached_input_tokens": 3,
        "reasoning_output_tokens": 2,
    }
    assert logs[0]["started_at"] <= logs[0]["finished_at"]


class Reasoning:
    type = "reasoning"

    def __getattr__(self, name):
        raise AssertionError(f"Private reasoning field was inspected: {name}")


class FakeStream:
    def __init__(self, events):
        self.events = events
        self.closed = False

    def __iter__(self):
        return iter(self.events)

    def close(self):
        self.closed = True


class FakeSDK:
    base_url = "https://invalid.example/v1/"

    def __init__(self, events):
        self.events = events
        self.calls = []
        self.streams = []
        self.responses = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        stream = FakeStream(self.events)
        self.streams.append(stream)
        return stream


@pytest.mark.parametrize(
    "error_type", [httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError]
)
def test_midstream_transport_error_retries_gpt_and_keeps_safe_diagnostics(error_type):
    from unittest.mock import Mock

    class BrokenStream(FakeStream):
        def __iter__(self):
            yield NS(
                type="response.output_text.delta",
                output_index=0,
                content_index=0,
                delta='{"count":',
            )
            raise error_type("SECRET_INPUT sk-test-secret https://private.invalid")

    class RecoveringSDK(FakeSDK):
        def create(self, **kwargs):
            stream = super().create(**kwargs)
            if len(self.calls) == 1:
                stream = BrokenStream([])
                self.streams[-1] = stream
            return stream

    sdk = RecoveringSDK(
        [
            completed_event(
                [
                    NS(
                        type="message",
                        content=[NS(type="output_text", text='{"count":9}')],
                    )
                ]
            )
        ]
    )
    fallback = Mock()
    sleeps = []
    result = ModelJsonClient(
        GptResponsesTransport(sdk),
        fallback_client=fallback,
        sleep=sleeps.append,
    ).complete(system_prompt="JSON", user_content="Count", schema=Answer)
    assert len(sdk.calls) == 2
    assert result.value.count == 9
    fallback.create.assert_not_called()
    assert sum(sleeps) == 5
    diagnostic = result.diagnostics[0]
    assert diagnostic.request_exception_type == error_type.__name__
    assert diagnostic.request_error_stage == "stream"
    assert diagnostic.request_error_location.startswith("gpt_responses.py:")
    assert diagnostic.request_error_kind == (
        "timeout" if error_type is httpx.ReadTimeout else "connection_error"
    )
    assert "SECRET_INPUT" not in diagnostic.model_dump_json()
    assert "sk-test-secret" not in diagnostic.model_dump_json()
    assert all(stream.closed for stream in sdk.streams)


def test_local_finalization_bug_is_recorded_without_retry_or_secret(caplog):
    from unittest.mock import Mock

    event = completed_event([])
    event.response.output = (
        None  # Reproduce a local parser TypeError, not a network outage.
    )
    sdk = FakeSDK([event])
    fallback = Mock()
    with pytest.raises(ModelRequestError) as caught:
        ModelJsonClient(
            GptResponsesTransport(sdk), fallback_client=fallback, sleep=lambda _: None
        ).complete(
            system_prompt="JSON",
            user_content="PRIVATE_PAPER sk-test-secret",
            schema=Answer,
        )
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.request_exception_type == "TypeError"
    assert diagnostic.request_error_stage == "finalize"
    assert diagnostic.request_error_location.startswith("gpt_responses.py:")
    assert len(sdk.calls) == 1
    fallback.create.assert_not_called()
    assert "exception_type=TypeError" in caplog.text
    assert "stage=finalize" in caplog.text
    assert "PRIVATE_PAPER" not in caplog.text
    assert "sk-test-secret" not in caplog.text


@pytest.mark.parametrize(
    "code,retried",
    [
        ("server_error", True),
        ("rate_limit_exceeded", True),
        ("invalid_api_key", False),
        ("insufficient_quota", False),
        ("secret_provider_value", False),
    ],
)
def test_real_sdk_sse_error_classification(code, retried):
    from unittest.mock import Mock

    from openai import OpenAI

    calls = []

    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            body = {
                "error": {
                    "code": code,
                    "message": "SECRET_INPUT sk-test-secret",
                    "type": "server_error",
                }
            }
        else:
            body = {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": '{"count":9}'}],
                        }
                    ],
                    "usage": None,
                    "model": "gpt-5.6-luna",
                    "id": "mock-success",
                },
            }
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="data: " + json.dumps(body) + "\n\n",
            request=request,
        )

    fallback = Mock()
    with OpenAI(
        api_key="test-only",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    ) as sdk:
        client = ModelJsonClient(
            GptResponsesTransport(sdk), fallback_client=fallback, sleep=lambda _: None
        )
        if retried:
            result = client.complete(
                system_prompt="JSON", user_content="Count", schema=Answer
            )
            diagnostic = result.diagnostics[0]
            assert result.value.count == 9
        else:
            with pytest.raises(ModelRequestError) as caught:
                client.complete(
                    system_prompt="JSON", user_content="Count", schema=Answer
                )
            diagnostic = caught.value.diagnostics[0]
    assert len(calls) == (2 if retried else 1)
    assert diagnostic.request_exception_type == "APIError"
    assert diagnostic.request_provider_code == (
        "other" if code == "secret_provider_value" else code
    )
    assert diagnostic.request_http_status is None
    assert "SECRET_INPUT" not in diagnostic.model_dump_json()
    fallback.create.assert_not_called()


@pytest.mark.parametrize(
    "reason,attempts",
    [
        ("max_output_tokens", 6),
        ("content_filter", 1),
        (None, 6),
        ("unknown_SECRET_INPUT", 6),
    ],
)
@pytest.mark.parametrize("partial", ["", '{"count":9}'])
def test_incomplete_output_never_becomes_fallback_success(reason, attempts, partial):
    from unittest.mock import Mock

    sdk = FakeSDK(
        [
            NS(
                type="response.output_text.done",
                output_index=0,
                content_index=0,
                text=partial,
            ),
            NS(
                type="response.incomplete",
                response=NS(
                    incomplete_details=NS(reason=reason),
                    usage=NS(input_tokens=40, output_tokens=12, total_tokens=52),
                ),
            ),
        ]
    )
    fallback = Mock()
    with pytest.raises(ModelOutputError) as caught:
        ModelJsonClient(
            GptResponsesTransport(sdk), fallback_client=fallback, sleep=lambda _: None
        ).complete(system_prompt="JSON", user_content="Count", schema=Answer)
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.classification == (
        "truncated_json" if reason == "max_output_tokens" else "incomplete_output"
    )
    assert diagnostic.finish_reason == {
        "max_output_tokens": "length",
        "content_filter": "content_filter",
    }.get(reason, "other")
    assert diagnostic.request_provider_code == (
        reason if reason in {"max_output_tokens", "content_filter"} else "other"
    )
    assert diagnostic.usage.completion_tokens == 12
    import hashlib

    assert diagnostic.content_bytes == len(partial.encode())
    assert diagnostic.content_sha256 == hashlib.sha256(partial.encode()).hexdigest()
    assert "SECRET_INPUT" not in diagnostic.model_dump_json()
    assert len(sdk.calls) == attempts
    fallback.create.assert_not_called()
    assert all(stream.closed for stream in sdk.streams)


@pytest.mark.parametrize("code", ["invalid_api_key", "insufficient_quota", "unknown"])
def test_nontransient_stream_error_does_not_downgrade(code):
    from unittest.mock import Mock

    sdk = FakeSDK([NS(type="response.failed", response=NS(error=NS(code=code)))])
    fallback = Mock()
    with pytest.raises(ModelRequestError):
        ModelJsonClient(
            GptResponsesTransport(sdk), fallback_client=fallback, sleep=lambda _: None
        ).complete(system_prompt="JSON", user_content="Count", schema=Answer)
    assert len(sdk.calls) == 1
    fallback.create.assert_not_called()


def test_explicit_transient_stream_error_can_use_emergency_fallback():
    from unittest.mock import Mock

    sdk = FakeSDK(
        [NS(type="response.failed", response=NS(error=NS(code="server_error")))]
    )
    fallback = Mock()
    fallback.create.return_value = NS(
        choices=[NS(finish_reason="stop", message=NS(content='{"count":9}'))],
        usage=None,
    )
    result = ModelJsonClient(
        GptResponsesTransport(sdk), fallback_client=fallback, sleep=lambda _: None
    ).complete(system_prompt="JSON", user_content="Count", schema=Answer)
    assert len(sdk.calls) == 6
    assert result.model == "deepseek-v4-flash"
    fallback.create.assert_called_once()


def done_events():
    return [
        NS(type="response.reasoning_text.delta", delta="PRIVATE_REASONING_SENTINEL"),
        NS(
            type="response.output_text.delta",
            output_index=0,
            content_index=0,
            delta='{"count":',
        ),
        NS(
            type="response.output_text.done",
            output_index=0,
            content_index=0,
            text='{"count":7}',
        ),
    ]


def completed_event(output):
    return NS(
        type="response.completed",
        response=NS(
            status="completed",
            output=output,
            usage=NS(input_tokens=11, output_tokens=5, total_tokens=16),
            model="gpt-5.6-luna",
            id="mock-completed",
        ),
    )


def run(events):
    sdk = FakeSDK(events)
    logs = []
    control = ModelJobControl()
    client = ModelJsonClient(
        GptResponsesTransport(
            sdk, observer=lambda metadata, text: logs.append((metadata, text))
        ),
        sleep=lambda _: None,
    ).with_job_control(control)
    return sdk, logs, control, client


def complete(client):
    return client.complete(
        system_prompt="Return JSON only.",
        user_content="Count the supplied items.",
        schema=Answer,
    )


def test_completed_empty_output_uses_done_and_preserves_request_contract():
    sdk, logs, control, client = run(done_events() + [completed_event([])])
    result = complete(client)
    assert result.value.count == 7
    assert result.attempts == control.usage.request_attempts == 1
    request = sdk.calls[0]
    assert request["model"] == "gpt-6-luna"
    assert request["reasoning"] == {"effort": "max"}
    assert request["stream"] is True and request["store"] is False
    assert request["input"] == [
        {"role": "system", "content": "Return JSON only."},
        {"role": "user", "content": "Count the supplied items."},
    ]
    assert request["timeout"] is None
    assert control.limits.max_request_seconds is None
    output_schema = request["text"]["format"]
    assert output_schema["type"] == "json_schema" and output_schema["strict"] is True
    assert output_schema["schema"]["required"] == ["count"]
    assert output_schema["schema"]["additionalProperties"] is False
    assert logs[0][0]["status"] == "completed"
    assert logs[0][1] == '{"count":7}'
    assert all(s.closed for s in sdk.streams)
    assert "PRIVATE_REASONING_SENTINEL" not in json.dumps(logs)


def test_eof_partial_text_fails_with_six_attempt_budget():
    sdk, logs, control, client = run(done_events())
    with pytest.raises(ModelRequestError):
        complete(client)
    assert len(sdk.calls) == control.usage.request_attempts == 6
    assert len(logs) == 6
    assert all(m["status"] == "failed" and m["output_is_partial"] for m, _ in logs)
    assert all(text == '{"count":7}' for _, text in logs)
    assert all(s.closed for s in sdk.streams)
    assert "PRIVATE_REASONING_SENTINEL" not in json.dumps(logs)


def test_completed_output_never_reads_or_persists_reasoning_items():
    output = [
        Reasoning(),
        NS(type="message", content=[NS(type="output_text", text='{"count":9}')]),
    ]
    sdk, logs, _, client = run([completed_event(output)])
    assert complete(client).value.count == 9
    assert logs[0][1] == '{"count":9}'
    assert len(sdk.calls) == 1


def test_http_failure_logs_only_allowlisted_provenance_and_diagnostic_serializes():
    response = httpx.Response(
        403,
        headers={
            "cf-ray": "0123456789abcdef-SJC",
            "server": "cloudflare",
            "content-type": "text/html; charset=UTF-8",
            "authorization": "Bearer SECRET_SENTINEL",
            "set-cookie": "SECRET_SENTINEL",
        },
        request=httpx.Request("POST", "https://invalid.example/v1/responses"),
    )
    failure = PermissionDeniedError(
        "SECRET_SENTINEL",
        response=response,
        body={
            "code": "access_denied",
            "type": "permission_error",
            "message": "SECRET_SENTINEL",
        },
    )
    sdk, logs, _, client = run([])

    def fail(**kwargs):
        sdk.calls.append(kwargs)
        raise failure

    sdk.create = fail
    with pytest.raises(ModelRequestError):
        complete(client)
    assert len(sdk.calls) == 1
    metadata, text = logs[0]
    assert text is None and metadata["http_status"] == 403
    assert metadata["provider_error_code"] == "access_denied"
    assert metadata["provider_error_type"] == "permission_error"
    assert set(metadata["response_headers"]) == {"cf-ray", "server", "content-type"}
    assert "SECRET_SENTINEL" not in json.dumps(logs)


@pytest.mark.parametrize("cancel", [False, True])
def test_interactive_async_stream_has_no_deadline_and_closes_on_cancel(
    monkeypatch, cancel
):
    import asyncio

    from compiler_service import gpt_responses, interactive_model_stream

    state = []
    clock = [0.0]
    entered = None
    monkeypatch.setattr(gpt_responses, "time", NS(monotonic=lambda: clock[0]))

    class Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            state.append("stream_closed")

        def __aiter__(self):
            return self.events()

        async def events(self):
            yield NS(
                type="response.output_text.delta",
                output_index=0,
                content_index=0,
                delta='{"count":9}',
            )
            entered.set()
            if cancel:
                try:
                    await asyncio.Event().wait()
                finally:
                    state.append("cancelled")
            else:
                clock[0] = 100.0  # Past both former 20-second and 75-second limits.
                yield completed_event(
                    [
                        NS(
                            type="message",
                            content=[NS(type="output_text", text='{"count":9}')],
                        )
                    ]
                )

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["max_retries"] == 0
            assert kwargs["timeout"] is None
            self.responses = self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            state.append("client_closed")

        async def create(self, **kwargs):
            assert kwargs["timeout"] is None
            return Stream()

    sdk = FakeSDK([])
    sdk.api_key = "offline-test-only"
    monkeypatch.setattr(interactive_model_stream, "AsyncOpenAI", Client)
    logs = []
    transport = GptResponsesTransport(sdk, observer=lambda m, _: logs.append(m))

    async def run():
        nonlocal entered
        entered = asyncio.Event()
        task = asyncio.create_task(
            transport.acreate(
                response_schema=Answer,
                model="offline-test-model",
                timeout=None,
                messages=[
                    {"role": "system", "content": "JSON"},
                    {"role": "user", "content": "Count"},
                ],
            )
        )
        if cancel:
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert (await task).choices[0].message.content == '{"count":9}'

    asyncio.run(run())
    assert state[-2:] == ["stream_closed", "client_closed"]
    assert logs[0]["status"] == ("cancelled" if cancel else "completed")
    if not cancel:
        assert logs[0]["elapsed_seconds"] == 100.0
    assert sdk.calls == []


@pytest.mark.parametrize(
    "schema_name",
    ["InteractiveRelationRankingOutput", "InteractiveContextPlanningOutput"],
)
@pytest.mark.parametrize("interactive,effort", [(True, "medium"), (False, "max")])
def test_simple_ranking_effort_override_is_interactive_only(
    monkeypatch, interactive, effort, schema_name
):
    import asyncio

    from compiler_service import gpt_responses

    schema = type(schema_name, (Answer,), {})
    event = completed_event(
        [NS(type="message", content=[NS(type="output_text", text='{"count":9}')])]
    )
    sdk = FakeSDK([event])

    async def consume(sdk, *, create_stream, on_event, **_):
        stream = create_stream(sdk)
        try:
            for event in stream:
                if on_event(event):
                    break
        finally:
            stream.close()

    monkeypatch.setattr(gpt_responses, "consume_model_stream", consume)
    transport = GptResponsesTransport(sdk)
    kwargs = dict(
        response_schema=schema,
        model="offline-model",
        messages=[
            {"role": "system", "content": "JSON"},
            {"role": "user", "content": "Rank"},
        ],
    )
    result = (
        asyncio.run(transport.acreate(**kwargs))
        if interactive
        else transport.create(**kwargs)
    )
    assert result.choices[0].message.content == '{"count":9}'
    assert sdk.calls[0]["reasoning"] == {"effort": effort}


@pytest.mark.parametrize("interactive", [False, True])
@pytest.mark.parametrize("fault", [None, "missing", "duplicate", "unexpected"])
def test_relation_wire_schema_reaches_responses_and_coverage_stays_strict(
    monkeypatch, interactive, fault
):
    from contextlib import nullcontext

    from test_retrieval_providers import _planner_payload
    from test_retrieval_service import _document

    from compiler_service import gpt_responses
    from compiler_service.errors import ContractIssue
    from compiler_service.retrieval_contract import (
        ClaimSearchDocument,
        QueryPlan,
        relation_candidate_key,
    )
    from compiler_service.retrieval_providers import (
        ModelRelationClassifier,
        retrieval_search_policy,
    )

    document = ClaimSearchDocument.model_validate(_document("claim-1", "Mercury risk"))
    second = ClaimSearchDocument.model_validate(_document("claim-2", "Mercury risk"))
    key = relation_candidate_key(document.snapshot_id, document.claim_id)
    second_key = relation_candidate_key(second.snapshot_id, second.claim_id)
    wire_key = "candidate:1" if interactive else key
    decision = dict(
        candidate_key=wire_key,
        relation="related",
        relation_direction="result_to_query",
        relation_confidence=0.5,
        scope_relation="unknown",
    )
    second_wire_key = "candidate:2" if interactive else second_key
    decisions = [{**decision, "candidate_key": second_wire_key}, decision]
    if fault == "missing":
        decisions = []
    elif fault == "duplicate":
        decisions = [decision, decision]
    elif fault == "unexpected":
        decisions = [{**decision, "candidate_key": "candidate:unexpected"}]
    sdk = FakeSDK(
        [
            completed_event(
                [
                    NS(
                        type="message",
                        content=[
                            NS(
                                type="output_text",
                                text=json.dumps({"decisions": decisions}),
                            )
                        ],
                    )
                ]
            )
        ]
    )

    async def consume(sdk, *, create_stream, on_event, **_):
        stream = create_stream(sdk)
        try:
            for event in stream:
                if on_event(event):
                    break
        finally:
            stream.close()

    monkeypatch.setattr(gpt_responses, "consume_model_stream", consume)
    classifier = ModelRelationClassifier(
        ModelJsonClient(
            GptResponsesTransport(sdk),
            sleep=lambda _: None,
            race_interval=0,
        )
    )
    plan = QueryPlan(
        **_planner_payload("Mercury risk").model_dump(),
        original_query="Mercury risk",
        planner="gpt",
    )
    with (
        retrieval_search_policy(interactive=interactive),
        pytest.raises(ModelOutputError) if fault else nullcontext() as caught,
    ):
        result = classifier.classify(plan, [document, second])
    if fault:
        assert all(
            item.contract_issue is ContractIssue.RETRIEVAL_RELATION_CANDIDATE_COVERAGE
            for item in caught.value.diagnostics
        )
    else:
        assert [item.candidate_key for item in result] == [second_key, key]
    assert sdk.calls
    for call in sdk.calls:
        assert call.get("max_output_tokens") == (32_768 if interactive else None)
        assert call["reasoning"] == {"effort": "high"}
        assert call["timeout"] is None
        wire = call["text"]["format"]
        assert wire["strict"] is True
        schema = wire["schema"]
        assert schema["properties"]["decisions"]["minItems"] == 2
        assert schema["properties"]["decisions"]["maxItems"] == 2
        props = schema["$defs"]["RelationDecision"]["properties"]
        assert props["candidate_key"]["enum"] == [wire_key, second_wire_key]
        payload = json.loads(call["input"][1]["content"])
        assert payload["candidates"][0]["candidate_key"] == wire_key
        assert payload["verification_candidate_manifest"]["candidate_keys"] == [
            wire_key, second_wire_key
        ]
        assert props["relation_confidence"] == {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
        }

        # Check the emitted schema, including referenced definitions, against
        # strict-mode object requirements without discarding dynamic constraints.
        def assert_closed_objects(value):
            if isinstance(value, dict):
                if value.get("type") == "object" or "properties" in value:
                    assert value.get("additionalProperties") is False
                    assert set(value.get("required", [])) == set(
                        value.get("properties", {})
                    )
                for child in value.values():
                    assert_closed_objects(child)
            elif isinstance(value, list):
                for child in value:
                    assert_closed_objects(child)

        assert_closed_objects(schema)
