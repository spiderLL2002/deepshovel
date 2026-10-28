"""Current GPT retry sequencing, cancellation and Responses routing; no network."""

from types import SimpleNamespace as NS
from unittest.mock import Mock

import httpx
import pytest
from openai import OpenAI
from pydantic import BaseModel

from compiler_service import model_client
from compiler_service.config import GPT_MAX_ATTEMPTS, GPT_MODEL
from compiler_service.errors import JobCancelled, ModelOutputError, ModelRequestError
from compiler_service.job_control import ModelJobControl, ModelJobLimits
from compiler_service.model_client import ModelJsonClient


class Answer(BaseModel):
    count: int


def complete(client):
    return client.complete(
        system_prompt="Return JSON", user_content="test", schema=Answer
    )


def output(content):
    return NS(choices=[NS(finish_reason="stop", message=NS(content=content))])


def test_sixth_gpt_attempt_can_succeed_with_cancellable_retry_gaps():
    create = Mock(side_effect=[output("not json")] * 5 + [output('{"count":2}')])
    sleeps = []
    control = ModelJobControl(
        limits=ModelJobLimits(max_completion_calls=1, max_request_attempts=6)
    )
    result = complete(
        ModelJsonClient(NS(create=create), sleep=sleeps.append).with_job_control(
            control
        )
    )
    assert result.model == GPT_MODEL and result.attempts == GPT_MAX_ATTEMPTS == 6
    assert all(call.kwargs["model"] == GPT_MODEL for call in create.call_args_list)
    assert sum(sleeps) == 25
    assert control.usage.request_attempts == 6


def test_invalid_output_stops_after_six_attempts():
    create = Mock(return_value=output("not json"))
    with pytest.raises(ModelOutputError):
        complete(ModelJsonClient(NS(create=create), sleep=lambda _: None))
    assert create.call_count == 6


def test_cancellation_during_retry_gap_prevents_next_request():
    cancelled = False

    def sleep(_):
        nonlocal cancelled
        cancelled = True

    def check():
        if cancelled:
            raise JobCancelled("cancelled")

    create = Mock(return_value=output("not json"))
    client = ModelJsonClient(NS(create=create), sleep=sleep).with_job_control(
        ModelJobControl(check_cancelled=check)
    )
    with pytest.raises(JobCancelled):
        complete(client)
    assert create.call_count == 1


def test_real_sdk_retries_only_the_fixed_responses_endpoint(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(503, json={"error": {"message": "overloaded"}})

    def factory(**kwargs):
        return OpenAI(
            **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler))
        )

    monkeypatch.setattr(model_client, "OpenAI", factory)
    client = ModelJsonClient.from_api_key("test-gpt")
    client._sleep = lambda _: None
    with pytest.raises(ModelRequestError):
        complete(client)
    assert len(requests) == 6
    assert all(str(r.url) == "https://api.iiot-shxt.xyz/v1/responses" for r in requests)
    assert all(r.headers["authorization"] == "Bearer test-gpt" for r in requests)
