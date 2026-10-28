from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import httpx
import pytest
from openai import APIConnectionError
from pydantic import BaseModel, ConfigDict

from compiler_service.config import DEEPSEEK_MAX_ATTEMPTS, GPT_MAX_ATTEMPTS
from compiler_service.errors import (
    JobCancelled,
    ModelJobBudgetExceeded,
    ModelJobDeadlineExceeded,
)
from compiler_service.job_control import (
    DEFAULT_GPT_JOB_LIMITS,
    ModelJobControl,
    ModelJobLimits,
    _messages_size_bytes,
)
from compiler_service.model_client import CompletionProfile, ModelJsonClient


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    value: int


def _response(payload: object) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content=json.dumps(payload)),
            )
        ]
    )


def _sdk(create: Any) -> SimpleNamespace:
    return SimpleNamespace(create=create)


def _limits(**changes: Any) -> ModelJobLimits:
    values = {
        "max_completion_calls": 64,
        "max_request_attempts": 128,
        "max_prompt_bytes": 512 * 1024 * 1024,
        "deadline_seconds": 5_400.0,
        "max_request_seconds": 600.0,
    }
    values.update(changes)
    return ModelJobLimits(**values)


def test_default_job_limits_encode_the_two_stage_topology() -> None:
    limits = DEFAULT_GPT_JOB_LIMITS
    assert limits == ModelJobLimits(
        max_completion_calls=2,
        max_request_attempts=18,
        max_prompt_bytes=192 * 1024 * 1024,
        deadline_seconds=None,
        max_request_seconds=None,
    )
    assert limits.max_completion_calls == 2
    assert limits.max_request_attempts == 2 * (GPT_MAX_ATTEMPTS + DEEPSEEK_MAX_ATTEMPTS)
    assert limits.max_request_seconds is None
    assert limits.deadline_seconds is None


def test_default_control_has_no_elapsed_time_cutoff():
    elapsed = [0.0]
    control = ModelJobControl(clock=lambda: elapsed[0])
    elapsed[0] = 10**9
    control.checkpoint()
    control.begin_completion()
    assert (
        control.begin_request_attempt(messages=[{"role": "user", "content": "x"}])
        is None
    )


def test_default_budget_accepts_extract_and_verify_then_rejects_third_call() -> None:
    control = ModelJobControl()
    messages = [{"role": "user", "content": "bounded metadata"}]

    def reserve_call() -> None:
        control.begin_completion()
        for _ in range(GPT_MAX_ATTEMPTS):
            control.begin_request_attempt(messages=messages)

    reserve_call()
    reserve_call()

    assert control.usage.completion_calls == 2
    assert control.usage.request_attempts == 12
    before_rejection = control.usage
    with pytest.raises(ModelJobBudgetExceeded, match="completion-call"):
        control.begin_completion()
    assert control.usage == before_rejection


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"max_completion_calls": 0}, "max_completion_calls"),
        ({"max_request_attempts": True}, "max_request_attempts"),
        ({"max_prompt_bytes": -1}, "max_prompt_bytes"),
        ({"deadline_seconds": 0}, "deadline_seconds"),
        ({"deadline_seconds": float("inf")}, "deadline_seconds"),
        ({"max_request_seconds": False}, "max_request_seconds"),
    ],
)
def test_job_limits_reject_nonpositive_and_boolean_values(
    changes: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _limits(**changes)


def test_completion_and_attempt_budgets_are_distinct_and_atomic() -> None:
    control = ModelJobControl(
        limits=_limits(
            max_completion_calls=1,
            max_request_attempts=1,
            max_prompt_bytes=1_000,
        )
    )
    messages = [
        {"role": "system", "content": "JSON only"},
        {"role": "user", "content": "input"},
    ]

    control.begin_completion()
    assert control.begin_request_attempt(messages=messages) == 600
    expected_bytes = _messages_size_bytes(messages)
    assert control.usage.completion_calls == 1
    assert control.usage.request_attempts == 1
    assert control.usage.prompt_bytes == expected_bytes

    with pytest.raises(ModelJobBudgetExceeded, match="completion-call"):
        control.begin_completion()
    with pytest.raises(ModelJobBudgetExceeded, match="request-attempt"):
        control.begin_request_attempt(messages=messages)
    assert control.usage.request_attempts == 1
    assert control.usage.prompt_bytes == expected_bytes


def test_prompt_budget_rejection_does_not_partially_count() -> None:
    messages = [{"role": "user", "content": "μg evidence"}]
    prompt_bytes = _messages_size_bytes(messages)
    prompt_control = ModelJobControl(limits=_limits(max_prompt_bytes=prompt_bytes - 1))
    prompt_control.begin_completion()
    with pytest.raises(ModelJobBudgetExceeded, match="prompt-byte"):
        prompt_control.begin_request_attempt(messages=messages)
    assert prompt_control.usage.request_attempts == 0
    assert prompt_control.usage.prompt_bytes == 0


def test_job_deadline_bounds_each_request_timeout() -> None:
    now = [100.0]
    control = ModelJobControl(
        limits=_limits(deadline_seconds=25, max_request_seconds=600),
        clock=lambda: now[0],
    )
    control.begin_completion()
    now[0] = 105.0
    timeout = control.begin_request_attempt(
        messages=[{"role": "user", "content": "input"}],
    )
    assert timeout == 20

    now[0] = 125.0
    with pytest.raises(ModelJobDeadlineExceeded):
        control.checkpoint()


def test_client_counts_retry_payload_without_setting_an_output_cap() -> None:
    calls: list[dict[str, Any]] = []

    def create(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        if len(calls) == 1:
            raise APIConnectionError(
                request=httpx.Request("POST", "https://api.iiot-shxt.xyz/v1/responses")
            )
        return _response({"value": 7})

    control = ModelJobControl()
    completion = (
        ModelJsonClient(_sdk(create), sleep=lambda _seconds: None)
        .with_job_control(control)
        .complete(
            system_prompt="Return JSON only.",
            user_content="measure μg",
            schema=_Payload,
            profile=CompletionProfile(max_attempts=2),
        )
    )

    assert completion.value.value == 7
    assert completion.attempts == 2
    assert control.usage.completion_calls == 1
    assert control.usage.request_attempts == 2
    assert all("max_tokens" not in call for call in calls)
    assert control.usage.prompt_bytes == sum(
        _messages_size_bytes(call["messages"]) for call in calls
    )
    assert [call["timeout"] for call in calls] == [None, None]


def test_controlled_client_views_are_isolated_from_base_and_each_other() -> None:
    create = Mock(return_value=_response({"value": 7}))
    base = ModelJsonClient(_sdk(create), sleep=lambda _: None)
    first_control = ModelJobControl(limits=_limits(max_completion_calls=1))
    second_control = ModelJobControl()
    first = base.with_job_control(first_control)
    second = base.with_job_control(second_control)

    first.complete(
        system_prompt="Return JSON only.",
        user_content="first compile",
        schema=_Payload,
    )
    base.complete(
        system_prompt="Return JSON only.",
        user_content="uncontrolled direct call",
        schema=_Payload,
    )
    second.complete(
        system_prompt="Return JSON only.",
        user_content="second compile",
        schema=_Payload,
    )

    assert first_control.usage.completion_calls == 1
    assert first_control.usage.request_attempts == 1
    assert second_control.usage.completion_calls == 1
    assert second_control.usage.request_attempts == 1
    assert create.call_count == 3
    with pytest.raises(ModelJobBudgetExceeded, match="completion-call"):
        first.complete(
            system_prompt="Return JSON only.",
            user_content="must remain isolated",
            schema=_Payload,
        )
    assert create.call_count == 3


def test_cancelled_during_sdk_call_discards_late_response() -> None:
    cancelled = False

    def check_cancelled() -> None:
        if cancelled:
            raise JobCancelled("Compile job was cancelled")

    def create(**kwargs: Any) -> SimpleNamespace:
        nonlocal cancelled
        del kwargs
        cancelled = True
        return _response({"value": 7})

    control = ModelJobControl(check_cancelled=check_cancelled)
    client = ModelJsonClient(_sdk(create), sleep=lambda _: None).with_job_control(
        control
    )

    with pytest.raises(JobCancelled):
        client.complete(
            system_prompt="Return JSON only.",
            user_content="input",
            schema=_Payload,
            profile=CompletionProfile(max_attempts=2),
        )
    assert control.usage.completion_calls == 1
    assert control.usage.request_attempts == 1


def test_cancelled_before_completion_never_calls_sdk_or_consumes_budget() -> None:
    create = Mock(return_value=_response({"value": 7}))

    def check_cancelled() -> None:
        raise JobCancelled("Compile job was cancelled")

    control = ModelJobControl(check_cancelled=check_cancelled)
    client = ModelJsonClient(_sdk(create), sleep=lambda _: None).with_job_control(
        control
    )

    with pytest.raises(JobCancelled):
        client.complete(
            system_prompt="Return JSON only.",
            user_content="input",
            schema=_Payload,
        )
    assert create.call_count == 0
    assert control.usage.completion_calls == 0
    assert control.usage.request_attempts == 0


def test_deadline_during_sdk_call_discards_late_response() -> None:
    now = [100.0]

    def create(**kwargs: Any) -> SimpleNamespace:
        del kwargs
        now[0] = 111.0
        return _response({"value": 7})

    control = ModelJobControl(
        limits=_limits(deadline_seconds=10),
        clock=lambda: now[0],
    )
    client = ModelJsonClient(_sdk(create), sleep=lambda _: None).with_job_control(
        control
    )

    with pytest.raises(ModelJobDeadlineExceeded):
        client.complete(
            system_prompt="Return JSON only.",
            user_content="input",
            schema=_Payload,
        )
    assert control.usage.completion_calls == 1
    assert control.usage.request_attempts == 1


def test_deadline_during_failed_sdk_call_wins_over_request_failure() -> None:
    now = [100.0]

    def create(**kwargs: Any) -> SimpleNamespace:
        del kwargs
        now[0] = 111.0
        raise ConnectionError("network failed after the deadline")

    control = ModelJobControl(
        limits=_limits(deadline_seconds=10),
        clock=lambda: now[0],
    )
    client = ModelJsonClient(_sdk(create), sleep=lambda _: None).with_job_control(
        control
    )

    with pytest.raises(ModelJobDeadlineExceeded):
        client.complete(
            system_prompt="Return JSON only.",
            user_content="input",
            schema=_Payload,
        )
    assert control.usage.completion_calls == 1
    assert control.usage.request_attempts == 1


def test_cancellation_during_failed_sdk_call_wins_over_request_failure() -> None:
    cancelled = False

    def check_cancelled() -> None:
        if cancelled:
            raise JobCancelled("Compile job was cancelled")

    def create(**kwargs: Any) -> SimpleNamespace:
        nonlocal cancelled
        del kwargs
        cancelled = True
        raise ConnectionError("network failed after cancellation")

    control = ModelJobControl(check_cancelled=check_cancelled)
    client = ModelJsonClient(_sdk(create), sleep=lambda _: None).with_job_control(
        control
    )

    with pytest.raises(JobCancelled):
        client.complete(
            system_prompt="Return JSON only.",
            user_content="input",
            schema=_Payload,
        )
    assert control.usage.completion_calls == 1
    assert control.usage.request_attempts == 1


def test_compiler_errors_from_sdk_are_never_retried_or_swallowed() -> None:
    create = Mock(side_effect=JobCancelled("Compile job was cancelled"))
    client = ModelJsonClient(_sdk(create), sleep=lambda _: None).with_job_control(
        ModelJobControl()
    )

    with pytest.raises(JobCancelled):
        client.complete(
            system_prompt="Return JSON only.",
            user_content="input",
            schema=_Payload,
            profile=CompletionProfile(max_attempts=3),
        )
    assert create.call_count == 1
