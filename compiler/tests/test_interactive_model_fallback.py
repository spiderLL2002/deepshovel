"""GPT hedging, availability-only fallback, validation and cancellation."""

import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from openai import APIStatusError, APITimeoutError
from pydantic import BaseModel

from compiler_service.config import DEEPSEEK_MODEL, GPT_MODEL
from compiler_service.errors import (
    JobCancelled,
    ModelOutputError,
    ModelRequestError,
    ModelUnavailable,
)
from compiler_service.gpt_responses import IncompleteModelOutput
from compiler_service.job_control import ModelJobControl, ModelJobLimits
from compiler_service.model_client import (
    INTERACTIVE_COMPLETION_PROFILE,
    DeepSeekJsonTransport,
    ModelJsonClient,
)


class Answer(BaseModel):
    value: int
    verifier_model: str = "unknown"


def output(content='{"value":1,"verifier_model":"invented"}'):
    return NS(choices=[NS(finish_reason="stop", message=NS(content=content))])


class Provider:
    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = []
        self.closed = 0
        self.cancelled = 0

    async def acreate(self, **request):
        number = len(self.calls)
        self.calls.append(request)
        try:
            return await self.behavior(number)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.closed += 1


def client(primary, secondary, **kwargs):
    return ModelJsonClient(
        primary, fallback_client=secondary, race_interval=0.01, **kwargs
    )


def complete(model, validate=None):
    return model.complete(
        system_prompt="Return JSON",
        user_content="Rank",
        schema=Answer,
        validate=validate,
        profile=INTERACTIVE_COMPLETION_PROFILE,
    )


async def pending(_):
    await asyncio.Event().wait()


@pytest.mark.parametrize("bad", ["", "broken json", '{"value":"bad"}', '{"value":-1}'])
@pytest.mark.parametrize("delay", [0.002, 0.05])
def test_valid_gpt_retry_wins_without_calling_backup(bad, delay):
    async def primary(number):
        if number < 2:
            return output(bad)
        await asyncio.sleep(delay)
        return output()

    async def success(_):
        await asyncio.sleep(delay)
        return output()

    def validate(value):
        if value.value < 0:
            raise ValueError("ungrounded")

    gpt, ds = Provider(primary), Provider(success)
    result = complete(client(gpt, ds), validate)
    assert result.value.verifier_model == result.model == GPT_MODEL
    assert len(gpt.calls) == gpt.closed == 3
    assert len(ds.calls) == ds.closed == 0
    for request in [*gpt.calls, *ds.calls]:
        assert request["timeout"] is None and request["interactive"] is True
        assert request["response_schema"] is Answer


def test_later_valid_round_wins_and_closes_all_older_pending_requests():
    async def gpt_response(number):
        if number == 2:
            return output()
        await asyncio.Event().wait()

    gpt, ds = Provider(gpt_response), Provider(pending)
    result = complete(client(gpt, ds))
    assert result.model == GPT_MODEL
    assert len(gpt.calls) == gpt.closed == 3
    assert len(ds.calls) == ds.closed == ds.cancelled == 0
    assert gpt.cancelled == 2


def test_three_rounds_are_scheduled_at_absolute_intervals_without_cancelling_old_io():
    starts = []

    async def wait(number):
        starts.append((number, asyncio.get_running_loop().time()))
        if len(starts) == 3:
            return output()
        await asyncio.Event().wait()

    gpt, ds = Provider(wait), Provider(wait)
    model = client(gpt, ds)
    assert ModelJsonClient(gpt)._race_interval == 10
    complete(model)
    assert [number for number, _ in starts] == [0, 1, 2]
    origin = starts[0][1]
    assert starts[1][1] - origin >= 0.009
    assert starts[2][1] - origin >= 0.019
    assert ds.calls == []


def test_nontransient_gpt_failure_never_calls_backup():
    async def fail(_):
        raise APIStatusError(
            "denied",
            response=httpx.Response(
                401, request=httpx.Request("POST", "https://test.invalid")
            ),
            body=None,
        )

    gpt, ds = Provider(fail), Provider(fail)
    with pytest.raises(ModelRequestError) as caught:
        complete(client(gpt, ds))
    assert len(gpt.calls) == len(caught.value.diagnostics) == 3
    assert ds.calls == []


def test_pending_gpt_after_other_failures_never_starts_backup():
    async def scenario():
        release = asyncio.Event()

        async def primary(number):
            if number == 0:
                await release.wait()
                return output()
            raise RuntimeError("unavailable")

        async def fail(_):
            raise RuntimeError("unavailable")

        gpt, ds = Provider(primary), Provider(fail)
        model = client(gpt, ds)
        task = asyncio.create_task(
            model._race(
                system_prompt="JSON",
                user_content="Rank",
                schema=Answer,
                parse_payload=None,
                validate=None,
            )
        )
        await asyncio.sleep(0.06)
        assert len(gpt.calls) == 3 and ds.calls == []
        assert not task.done()
        release.set()
        assert (await task).model == GPT_MODEL
        assert gpt.closed == 3 and ds.closed == 0

    asyncio.run(scenario())


def test_all_invalid_responses_are_rejected_under_same_domain_validator():
    async def invalid(_):
        return output('{"value":-1}')

    validate = Mock(side_effect=ValueError("ungrounded"))
    ds = Provider(invalid)
    with pytest.raises(ModelOutputError) as caught:
        complete(client(Provider(invalid), ds), validate)
    assert validate.call_count == len(caught.value.diagnostics) == 3
    assert ds.calls == []


def test_user_cancellation_poll_closes_all_pending_tasks():
    checks = 0

    def check():
        nonlocal checks
        checks += 1
        if checks > 15:
            raise JobCancelled("user cancelled")

    control = ModelJobControl(check_cancelled=check)
    gpt, ds = Provider(pending), Provider(pending)
    with pytest.raises(JobCancelled):
        complete(client(gpt, ds, job_control=control))
    assert gpt.closed == len(gpt.calls) and ds.closed == len(ds.calls)
    assert gpt.cancelled == len(gpt.calls) and ds.cancelled == len(ds.calls)


def test_expired_shared_deadline_does_not_expire_interactive_race():
    now = [0]
    control = ModelJobControl(
        limits=ModelJobLimits(deadline_seconds=1, max_request_seconds=1),
        clock=lambda: now[0],
    )
    now[0] = 1000

    async def success(_):
        return output()

    result = complete(client(Provider(success), Provider(pending), job_control=control))
    assert result.value.value == 1
    assert control.usage.completion_calls == 1 and control.usage.request_attempts == 1


class PrivateDelta:
    def __init__(self, content):
        self.content = content

    @property
    def reasoning_content(self):
        pytest.fail("private reasoning must never be read")


class Stream:
    def __init__(self, hold=False, finish="stop"):
        self.hold, self.finish, self.closed = hold, finish, False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True

    async def __aiter__(self):
        yield NS(choices=[NS(delta=PrivateDelta(None), finish_reason=None)])
        if self.hold:
            await asyncio.Event().wait()
        yield NS(
            choices=[NS(delta=PrivateDelta('{"value":1}'), finish_reason=self.finish)]
        )


class Sdk:
    def __init__(self, stream):
        self.closed = False
        self.create = AsyncMock(return_value=stream)
        self.chat = NS(completions=NS(create=self.create))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True


@pytest.mark.parametrize("hold", [False, True])
def test_deepseek_async_nonthinking_stream_closes_on_completion_or_cancellation(hold):
    stream = Stream(hold)
    sdk = Sdk(stream)
    transport = DeepSeekJsonTransport(
        NS(base_url="https://test.invalid"), async_client_factory=lambda: sdk
    )

    async def run():
        task = asyncio.create_task(
            transport.acreate(
                response_schema=Answer,
                model=DEEPSEEK_MODEL,
                messages=[{"role": "system", "content": "JSON"}],
                timeout=None,
                interactive=True,
            )
        )
        if hold:
            await asyncio.sleep(0.005)
            assert not task.done()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert (await task).choices[0].message.content == '{"value":1}'

    asyncio.run(run())
    assert stream.closed and sdk.closed
    request = sdk.create.call_args.kwargs
    assert request["timeout"] is None and request["max_tokens"] == 16384
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in request


def test_deepseek_truncated_json_cannot_win_even_if_json_happens_to_parse():
    sdk = Sdk(Stream(finish="length"))
    transport = DeepSeekJsonTransport(
        NS(base_url="https://test.invalid"), async_client_factory=lambda: sdk
    )
    with pytest.raises(IncompleteModelOutput):
        asyncio.run(
            transport.acreate(
                response_schema=Answer,
                model=DEEPSEEK_MODEL,
                messages=[{"role": "system", "content": "JSON"}],
            )
        )


def test_default_batch_keeps_six_primary_then_three_fallback_attempts():
    request = httpx.Request("POST", "https://test.invalid")
    primary = Mock(side_effect=APITimeoutError(request=request))
    fallback = Mock(side_effect=[APITimeoutError(request=request)] * 2 + [output()])
    model = ModelJsonClient(
        NS(create=primary), fallback_client=NS(create=fallback), sleep=lambda _: None
    )
    result = model.complete(
        system_prompt="Return JSON", user_content="batch", schema=Answer
    )
    assert primary.call_count == 6 and fallback.call_count == 3 and result.attempts == 9


def test_batch_deepseek_generation_configuration_remains_unchanged():
    create = Mock(return_value=output())
    transport = DeepSeekJsonTransport(NS(chat=NS(completions=NS(create=create))))
    transport.create(
        response_schema=Answer,
        model=DEEPSEEK_MODEL,
        messages=[{"role": "system", "content": "JSON"}],
    )
    request = create.call_args.kwargs
    assert request["extra_body"] == {"thinking": {"type": "enabled"}}
    assert request["reasoning_effort"] == "high" and request["max_tokens"] == 65536
    assert request["stream"] is False


@pytest.mark.parametrize("provider", [GPT_MODEL, DEEPSEEK_MODEL])
def test_winner_telemetry_is_value_free_and_only_outages_raise_fallback_alarm(
    provider, caplog
):
    async def win(_):
        return output()

    async def unavailable(_):
        raise APITimeoutError(request=httpx.Request("POST", "https://test.invalid"))

    gpt = Provider(win if provider == GPT_MODEL else unavailable)
    ds = Provider(win if provider == DEEPSEEK_MODEL else pending)
    callback = Mock()
    with caplog.at_level("INFO", logger="compiler_service.model_client"):
        result = complete(client(gpt, ds, on_fallback=callback))
    assert result.model == provider
    assert callback.call_count == (1 if provider == DEEPSEEK_MODEL else 0)
    assert "Interactive model winner schema=Answer" in caplog.text
    launched = 4 if provider == DEEPSEEK_MODEL else 1
    assert f"model={provider}" in caplog.text and f"launched={launched}" in caplog.text
    assert "invented" not in caplog.text and "verifier_model" not in caplog.text


def test_cancellation_during_validation_wins_over_valid_model_result():
    cancelled = False

    def check():
        if cancelled:
            raise JobCancelled("cancelled")

    def validate(_):
        nonlocal cancelled
        cancelled = True

    async def win(_):
        return output()

    model = client(
        Provider(win),
        Provider(pending),
        job_control=ModelJobControl(check_cancelled=check),
    )
    with pytest.raises(JobCancelled):
        complete(model, validate)


def test_unconfigured_primary_does_not_silently_use_backup():
    async def unavailable(_):
        raise ModelUnavailable("provider not available")

    async def win(_):
        await asyncio.sleep(0.002)
        return output()

    backup = Provider(win)
    with pytest.raises(ModelRequestError):
        complete(client(Provider(unavailable), backup))
    assert backup.calls == []


def test_fallback_starts_after_all_primary_failures_and_retries_stay_bounded():
    events = []

    async def unavailable(number):
        await asyncio.sleep(0.03 if number == 0 else 0)
        events.append(f"gpt{number}")
        raise APITimeoutError(request=httpx.Request("POST", "https://test.invalid"))

    async def backup(number):
        assert set(events[:3]) == {"gpt0", "gpt1", "gpt2"}
        events.append(f"backup{number}")
        if number < 2:
            raise APITimeoutError(request=httpx.Request("POST", "https://test.invalid"))
        return output()

    gpt, ds = Provider(unavailable), Provider(backup)
    result = complete(client(gpt, ds))
    assert len(gpt.calls) == len(ds.calls) == 3
    assert result.attempts == 6 and result.model == DEEPSEEK_MODEL
    assert result.value.verifier_model == DEEPSEEK_MODEL
