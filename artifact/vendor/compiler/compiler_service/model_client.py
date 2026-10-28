"""GPT Responses JSON boundary with bounded fresh retries."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from types import SimpleNamespace
from typing import Any, Generic, TypeVar

import httpx
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, OpenAI
from pydantic import BaseModel, ValidationError

from .config import (
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MAX_ATTEMPTS,
    DEEPSEEK_MODEL,
    GPT_BASE_URL,
    GPT_MAX_ATTEMPTS,
    GPT_MODEL,
    MODEL_RETRY_DELAY_SECONDS,
)
from .errors import (
    CompilerError,
    ContractError,
    ContractIssue,
    ContractValidationError,
    JobCancelled,
    ModelAttemptDiagnostic,
    ModelContractIssue,
    ModelJobBudgetExceeded,
    ModelJsonFailure,
    ModelOutputError,
    ModelRequestError,
    ModelRequestErrorKind,
    ModelSchemaIssue,
    ModelTokenUsage,
    ModelUnavailable,
)
from .gpt_responses import (
    GptResponsesTransport,
    IncompleteModelOutput,
    safe_failure_context,
)
from .graph_contract import PaperEvidenceGraph
from .interactive_model_stream import consume_model_stream
from .job_control import ModelJobControl
from .json_boundary import strict_json_loads

LOGGER = logging.getLogger(__name__)

SchemaT = TypeVar("SchemaT", bound=BaseModel)

_FINISH_REASONS = {
    "stop",
    "length",
    "content_filter",
    "tool_calls",
    "function_call",
}
_USAGE_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
)
_MAX_SCHEMA_ERRORS = 8
_MAX_CONTRACT_ISSUES = 4
_MAX_CONTRACT_VIOLATIONS_SCANNED = 64
_MAX_LOC_PARTS = 4
_MAX_ERROR_MESSAGE = 3_900
_MAX_RETRY_FEEDBACK = 4_096
_SAFE_ERROR_TYPE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_SAFE_LOC_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,47}$")
_RETRY_COMPLETION_GUARD = (
    "This is a fresh retry after a rejected result. Use minimal internal "
    "reasoning, finish that private reasoning within about 2000 tokens, and "
    "begin the final JSON object immediately. Do not restate the "
    "schema or source, enumerate candidates, narrate analysis, or preface the "
    "JSON. Reserve the completion budget for exactly one complete, closed JSON "
    "object. Preserve every required key from the authoritative schema in every "
    "object. Nullable does not mean optional: emit JSON null instead of omitting "
    "a required nullable key. Use exact authoritative enum values."
)


@dataclass(frozen=True, slots=True)
class Completion(Generic[SchemaT]):
    value: SchemaT
    attempts: int
    content_sha256: str
    diagnostics: tuple[ModelAttemptDiagnostic, ...]
    model: str = GPT_MODEL


@dataclass(frozen=True, slots=True)
class CompletionProfile:
    """Bound one pipeline stage without changing the model boundary."""

    max_attempts: int = GPT_MAX_ATTEMPTS
    interactive: bool = False
    fallback_attempts: int = DEEPSEEK_MAX_ATTEMPTS

    def __post_init__(self) -> None:
        if (
            type(self.max_attempts) is not int
            or not 1 <= self.max_attempts <= GPT_MAX_ATTEMPTS
        ):
            raise ValueError("stage max_attempts exceeds the configured boundary")
        if type(self.interactive) is not bool:
            raise ValueError("interactive must be a boolean")
        if (
            type(self.fallback_attempts) is not int
            or not 1 <= self.fallback_attempts <= DEEPSEEK_MAX_ATTEMPTS
        ):
            raise ValueError("stage fallback_attempts exceeds the configured boundary")


DEFAULT_COMPLETION_PROFILE = CompletionProfile()
INTERACTIVE_COMPLETION_PROFILE = CompletionProfile(
    interactive=True,
    max_attempts=3,
    fallback_attempts=3,
)


class DeepSeekJsonTransport:
    """Adapt the fallback API to the same JSON and contract validation loop."""

    def __init__(self, sdk: Any, *, async_client_factory: Any = None) -> None:
        self.sdk = sdk
        self._async_client_factory = async_client_factory

    def create(self, *, response_schema: Any, **request: Any) -> Any:
        return self.sdk.chat.completions.create(
            **self._arguments(response_schema, request, interactive=False)
        )

    @staticmethod
    def _arguments(response_schema: Any, request: dict, *, interactive: bool) -> dict:
        messages = [dict(message) for message in request["messages"]]
        messages[0]["content"] += (
            "\n\nReturn exactly one JSON object matching this authoritative schema:\n"
            + json.dumps(response_schema.model_json_schema(), ensure_ascii=False)
        )
        arguments = dict(
            model=request["model"],
            messages=messages,
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "disabled" if interactive else "enabled"}},
            max_tokens=16_384 if interactive else 65_536,
            stream=interactive,
            timeout=None if interactive else request.get("timeout"),
        )
        if not interactive:
            arguments["reasoning_effort"] = "high"
        return arguments

    async def acreate(self, *, response_schema: Any, **request: Any) -> Any:
        arguments = self._arguments(response_schema, request, interactive=True)
        parts: list[str] = []
        finish_reason = None
        usage = None

        def on_event(event: Any) -> bool:
            nonlocal finish_reason, usage
            if getattr(event, "usage", None) is not None:
                usage = event.usage
            if not event.choices:
                return False
            choice = event.choices[0]
            content = getattr(choice.delta, "content", None)
            if isinstance(content, str):
                parts.append(content)
            finish_reason = choice.finish_reason
            return finish_reason is not None

        await consume_model_stream(
            self.sdk,
            create_stream=lambda sdk: sdk.chat.completions.create(**arguments),
            on_event=on_event,
            async_client_factory=self._async_client_factory,
        )
        if finish_reason is None:
            raise APIConnectionError(
                message="Model stream ended without a completed answer",
                request=httpx.Request("POST", DEEPSEEK_BASE_URL + "/chat/completions"),
            )
        if finish_reason != "stop":
            raise IncompleteModelOutput(finish_reason)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason=finish_reason,
                    message=SimpleNamespace(content="".join(parts)),
                )
            ],
            usage=usage,
        )


class ModelJsonClient:
    def __init__(
        self,
        client: Any,
        *,
        job_control: ModelJobControl | None = None,
        sleep: Callable[[float], None] = time.sleep,
        fallback_client: Any | None = None,
        on_fallback: Callable[[], None] | None = None,
        race_interval: float = 10.0,
    ) -> None:
        self._client = client
        self._job_control = job_control
        self._sleep = sleep
        self._fallback_client = fallback_client
        self._on_fallback = on_fallback
        self._race_interval = race_interval

    @classmethod
    def from_api_key(
        cls,
        api_key: str | None,
        *,
        deepseek_api_key: str | None = None,
        on_fallback: Callable[[], None] | None = None,
    ) -> ModelJsonClient:
        if not api_key:
            raise ModelUnavailable("GPT_API_KEY is not configured")
        return cls(
            GptResponsesTransport(
                OpenAI(
                    api_key=api_key,
                    base_url=GPT_BASE_URL,
                    max_retries=0,
                    timeout=None,
                    default_headers={"User-Agent": "DeepShovel/1.0"},
                )
            ),
            on_fallback=on_fallback,
            fallback_client=(
                DeepSeekJsonTransport(
                    OpenAI(
                        api_key=deepseek_api_key,
                        base_url=DEEPSEEK_BASE_URL,
                        max_retries=0,
                        timeout=None,
                        default_headers={"User-Agent": "DeepShovel/1.0"},
                    )
                )
                if deepseek_api_key
                else None
            ),
        )

    def with_job_control(self, control: ModelJobControl) -> ModelJsonClient:
        """Return an isolated view sharing only the underlying SDK client."""

        return ModelJsonClient(
            self._client,
            job_control=control,
            sleep=self._sleep,
            fallback_client=self._fallback_client,
            on_fallback=self._on_fallback,
            race_interval=self._race_interval,
        )

    def complete(
        self,
        *,
        system_prompt: str,
        user_content: str,
        schema: type[SchemaT],
        response_json_schema: dict[str, Any] | None = None,
        parse_payload: Callable[[dict[str, Any]], SchemaT] | None = None,
        validate: Callable[[SchemaT], None] | None = None,
        profile: CompletionProfile = DEFAULT_COMPLETION_PROFILE,
    ) -> Completion[SchemaT]:
        if "json" not in system_prompt.casefold():
            raise ValueError("system prompt must explicitly require JSON")
        arguments = dict(
            system_prompt=system_prompt,
            user_content=user_content,
            schema=schema,
            response_json_schema=response_json_schema,
            parse_payload=parse_payload,
            validate=validate,
        )
        if profile.interactive:
            return asyncio.run(self._race(**arguments))
        return self._complete_sequential(**arguments, profile=profile)

    async def _race(
        self,
        *,
        system_prompt: str,
        user_content: str,
        schema: type[SchemaT],
        response_json_schema: dict[str, Any] | None = None,
        parse_payload: Any,
        validate: Any,
    ) -> Completion[SchemaT]:
        """Hedge GPT at 0/10/20 seconds; use fallback only after unavailability."""
        started = asyncio.get_running_loop().time()
        control = self._job_control
        if control is not None:
            control.begin_completion(enforce_deadline=False)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        outcomes: asyncio.Queue = asyncio.Queue()
        workers: list[asyncio.Task] = []
        diagnostics: list[ModelAttemptDiagnostic] = []
        finished = 0
        scheduling_done = False
        fallback_started = False

        def checkpoint() -> None:
            if control is not None:
                control.checkpoint(enforce_deadline=False)

        async def attempt(client: Any, model: str, number: int) -> None:
            try:
                checkpoint()
                if control is not None:
                    control.begin_request_attempt(
                        messages=messages, enforce_deadline=False
                    )
                response = None
                request_error = None
                try:
                    response = await client.acreate(
                        model=model,
                        messages=messages,
                        response_schema=schema,
                        **(
                            {"response_json_schema": response_json_schema}
                            if response_json_schema is not None
                            else {}
                        ),
                        timeout=None,
                        interactive=True,
                    )
                except Exception as exc:
                    request_error = exc
                checkpoint()

                # Replay exactly one completed response through the existing strict
                # JSON/schema/domain boundary, without another network request.
                def replay(**_):
                    if request_error is not None:
                        raise request_error
                    return response

                validator = ModelJsonClient(SimpleNamespace(create=replay))
                result = validator._complete_sequential(
                    system_prompt=system_prompt,
                    user_content=user_content,
                    schema=schema,
                    parse_payload=parse_payload,
                    validate=validate,
                    profile=CompletionProfile(max_attempts=1),
                    _primary_model=model,
                )
                checkpoint()
                await outcomes.put((number, result))
            except asyncio.CancelledError:
                raise
            except (
                JobCancelled,
                ModelJobBudgetExceeded,
                ModelOutputError,
                ModelRequestError,
            ) as exc:
                await outcomes.put((number, exc))
            except Exception as exc:
                kind, status = _classify_request_error(exc)
                await outcomes.put(
                    (
                        number,
                        ModelRequestError(
                            kind,
                            http_status=status,
                            diagnostics=(
                                ModelAttemptDiagnostic(
                                    attempt=number,
                                    classification="request_failed",
                                    request_error_kind=kind,
                                    request_http_status=status,
                                    **safe_failure_context(exc),
                                ),
                            ),
                        ),
                    )
                )

        async def schedule(client: Any, model: str) -> None:
            started = asyncio.get_running_loop().time()
            try:
                for round_number in range(3):
                    await asyncio.sleep(
                        max(
                            0,
                            started
                            + round_number * self._race_interval
                            - asyncio.get_running_loop().time(),
                        )
                    )
                    checkpoint()
                    workers.append(
                        asyncio.create_task(
                            attempt(client, model, len(workers) + 1)
                        )
                    )
                await outcomes.put((0, None))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await outcomes.put((0, exc))

        async def watch_cancellation() -> None:
            try:
                while True:
                    checkpoint()
                    await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await outcomes.put((0, exc))

        scheduler = asyncio.create_task(schedule(self._client, GPT_MODEL))
        watcher = asyncio.create_task(watch_cancellation())
        try:
            while True:
                number, outcome = await outcomes.get()
                checkpoint()
                if number == 0:
                    if outcome is not None:
                        raise outcome
                    scheduling_done = True
                else:
                    finished += 1
                    if isinstance(outcome, Completion):
                        LOGGER.info(
                            "Interactive model winner schema=%s model=%s "
                            "launched=%s elapsed_s=%.3f",
                            schema.__name__,
                            outcome.model,
                            len(workers),
                            asyncio.get_running_loop().time() - started,
                        )
                        checkpoint()
                        diagnostics.extend(
                            item.model_copy(update={"attempt": number})
                            for item in outcome.diagnostics
                        )
                        return Completion(
                            value=outcome.value,
                            attempts=len(workers),
                            content_sha256=outcome.content_sha256,
                            diagnostics=tuple(diagnostics),
                            model=outcome.model,
                        )
                    if not isinstance(outcome, (ModelOutputError, ModelRequestError)):
                        raise outcome
                    diagnostics.extend(
                        item.model_copy(update={"attempt": number})
                        for item in outcome.diagnostics
                    )
                if scheduling_done and finished == len(workers):
                    # A slow or invalid GPT response is not an outage. Wait for
                    # every GPT request before considering the backup provider,
                    # and retain the same availability-only gate as batch work.
                    if (
                        not fallback_started
                        and self._fallback_client is not None
                        and diagnostics
                        and all(
                            item.classification == "request_failed"
                            and item.request_error_kind is not None
                            and _retryable_request_error(item.request_error_kind)
                            for item in diagnostics
                        )
                    ):
                        checkpoint()
                        fallback_started = True
                        scheduling_done = False
                        LOGGER.warning(
                            "Interactive model fallback activated schema=%s "
                            "reason=all_gpt_attempts_unavailable attempts=%s",
                            schema.__name__,
                            len(workers),
                        )
                        if self._on_fallback is not None:
                            self._on_fallback()
                        scheduler = asyncio.create_task(
                            schedule(self._fallback_client, DEEPSEEK_MODEL)
                        )
                        continue
                    if diagnostics and all(
                        item.classification == "request_failed" for item in diagnostics
                    ):
                        last = diagnostics[-1]
                        raise ModelRequestError(
                            last.request_error_kind or ModelRequestErrorKind.UNEXPECTED,
                            http_status=last.request_http_status,
                            diagnostics=tuple(diagnostics),
                        )
                    raise ModelOutputError(
                        "All interactive model requests failed validation",
                        diagnostics=tuple(diagnostics),
                    )
        finally:
            for task in [scheduler, watcher, *workers]:
                task.cancel()
            await asyncio.gather(scheduler, watcher, *workers, return_exceptions=True)

    def _complete_sequential(
        self,
        *,
        system_prompt: str,
        user_content: str,
        schema: type[SchemaT],
        response_json_schema: dict[str, Any] | None = None,
        parse_payload: Callable[[dict[str, Any]], SchemaT] | None = None,
        validate: Callable[[SchemaT], None] | None = None,
        profile: CompletionProfile = DEFAULT_COMPLETION_PROFILE,
        _primary_model: str = GPT_MODEL,
    ) -> Completion[SchemaT]:
        if "json" not in system_prompt.casefold():
            raise ValueError("system prompt must explicitly require JSON")
        if self._job_control is not None:
            self._job_control.begin_completion()
        diagnostics: list[ModelAttemptDiagnostic] = []
        input_id = hashlib.sha256(user_content.encode()).hexdigest()[:16]

        def record_diagnostic(diagnostic: ModelAttemptDiagnostic) -> None:
            diagnostics.append(diagnostic)
            if diagnostic.classification != "success":
                LOGGER.warning(
                    "Model attempt rejected input=%s schema=%s model=%s attempt=%s "
                    "elapsed_s=%.1f classification=%s error_kind=%s "
                    "http_status=%s contract_issue=%s "
                    "exception_type=%s stage=%s provider_code=%s location=%s",
                    input_id,
                    schema.__name__,
                    model,
                    diagnostic.attempt,
                    time.monotonic() - attempt_started,
                    diagnostic.classification,
                    diagnostic.request_error_kind,
                    diagnostic.request_http_status,
                    diagnostic.contract_issue,
                    diagnostic.request_exception_type,
                    diagnostic.request_error_stage,
                    diagnostic.request_provider_code,
                    diagnostic.request_error_location,
                )

        primary_attempts = (
            GPT_MAX_ATTEMPTS
            if self._fallback_client is not None
            else profile.max_attempts
        )
        max_attempts = primary_attempts + (
            profile.fallback_attempts if self._fallback_client is not None else 0
        )
        for slot in range(1, max_attempts + 1):
            using_fallback = slot > primary_attempts
            attempt = len(diagnostics) + 1
            if slot == primary_attempts + 1:
                # Only availability failures may activate the backup provider.
                if not all(
                    item.classification == "request_failed"
                    and item.request_error_kind is not None
                    and _retryable_request_error(item.request_error_kind)
                    for item in diagnostics
                ):
                    LOGGER.warning(
                        "Model fallback suppressed input=%s schema=%s "
                        "reason=gpt_output_or_nontransient_error attempts=%s",
                        input_id,
                        schema.__name__,
                        len(diagnostics),
                    )
                    break
                LOGGER.warning(
                    "Model fallback activated input=%s schema=%s reason=%s attempts=%s",
                    input_id,
                    schema.__name__,
                    "all_gpt_attempts_unavailable",
                    len(diagnostics),
                )
            attempt_started = time.monotonic()
            client = self._fallback_client if using_fallback else self._client
            model = DEEPSEEK_MODEL if using_fallback else _primary_model
            if attempt > 1:
                # Small waits keep cancellation responsive between fresh attempts.
                previous = diagnostics[-1]
                delay = MODEL_RETRY_DELAY_SECONDS
                if previous.classification == "request_failed":
                    delay = min(60.0, delay * 2 ** min(attempt - 2, 4))
                for _ in range(int(delay)):
                    if self._job_control is not None:
                        self._job_control.checkpoint()
                    self._sleep(1.0)
            previous_diagnostic = diagnostics[-1] if diagnostics else None
            messages = _fresh_messages(
                system_prompt=system_prompt,
                user_content=user_content,
                previous_diagnostic=previous_diagnostic,
                schema=schema,
            )
            request_timeout = None
            if self._job_control is not None:
                request_timeout = self._job_control.begin_request_attempt(
                    messages=messages,
                )
            if slot == primary_attempts + 1 and self._on_fallback is not None:
                self._on_fallback()
            try:
                response = client.create(
                    model=model,
                    messages=messages,
                    response_schema=schema,
                    **(
                        {"response_json_schema": response_json_schema}
                        if response_json_schema is not None
                        else {}
                    ),
                    timeout=request_timeout,
                )
            except IncompleteModelOutput as exc:
                record_diagnostic(
                    ModelAttemptDiagnostic(
                        attempt=attempt,
                        classification=(
                            "truncated_json"
                            if exc.finish_reason == "length"
                            else "incomplete_output"
                        ),
                        finish_reason=exc.finish_reason,
                        usage=_token_usage(exc),
                        content_bytes=exc.content_bytes,
                        content_sha256=exc.content_sha256,
                        **safe_failure_context(exc),
                    )
                )
                if self._job_control is not None:
                    self._job_control.checkpoint()
                if exc.finish_reason == "content_filter":
                    raise ModelOutputError(
                        "Model output was filtered", diagnostics=tuple(diagnostics)
                    ) from None
                continue
            except CompilerError:
                raise
            except Exception as exc:
                # SDK/network retries are disabled; each loop is exactly one attempt.
                kind, http_status = _classify_request_error(exc)
                diagnostic = ModelAttemptDiagnostic(
                    attempt=attempt,
                    classification="request_failed",
                    request_error_kind=kind,
                    request_http_status=http_status,
                    **safe_failure_context(exc),
                )
                record_diagnostic(diagnostic)
                if self._job_control is not None:
                    # Cancellation or deadline during a failed blocking request
                    # still wins over the generic request-failure retry path.
                    self._job_control.checkpoint()
                if not _retryable_request_error(kind):
                    raise ModelRequestError(
                        kind, http_status=http_status, diagnostics=tuple(diagnostics)
                    ) from None
                continue
            if self._job_control is not None:
                # Cancellation or deadline during the blocking SDK request wins
                # over a late model response.
                self._job_control.checkpoint()
            choice = _first_choice(response)
            finish_reason = _finish_reason(choice)
            usage = _token_usage(response)
            try:
                # reasoning_content is intentionally never read or persisted.
                content = choice.message.content
            except (AttributeError, IndexError):
                content = None
            content_bytes, content_sha256 = _content_fingerprint(content)
            if not isinstance(content, str) or not content.strip():
                classification = _with_truncation("empty_content", finish_reason)
                record_diagnostic(
                    ModelAttemptDiagnostic(
                        attempt=attempt,
                        classification=classification,
                        finish_reason=finish_reason,
                        content_bytes=content_bytes,
                        content_sha256=content_sha256,
                        usage=usage,
                        json_error=_truncation_error(finish_reason),
                    )
                )
                continue
            try:
                payload = strict_json_loads(content)
            except (RecursionError, ValueError) as exc:
                classification = _with_truncation("invalid_json", finish_reason)
                record_diagnostic(
                    ModelAttemptDiagnostic(
                        attempt=attempt,
                        classification=classification,
                        finish_reason=finish_reason,
                        content_bytes=content_bytes,
                        content_sha256=content_sha256,
                        usage=usage,
                        json_error=_json_failure(exc, finish_reason),
                    )
                )
                continue
            if not isinstance(payload, dict):
                record_diagnostic(
                    ModelAttemptDiagnostic(
                        attempt=attempt,
                        classification=_with_truncation(
                            "invalid_json_root", finish_reason
                        ),
                        finish_reason=finish_reason,
                        content_bytes=content_bytes,
                        content_sha256=content_sha256,
                        usage=usage,
                        json_error=_truncation_error(finish_reason),
                    )
                )
                continue
            try:
                # Provider identity is host-owned metadata, not model self-report.
                if "verifier_model" in payload:
                    payload["verifier_model"] = model
                value = (
                    schema.model_validate(payload, strict=True)
                    if parse_payload is None
                    else parse_payload(payload)
                )
            except ValidationError as exc:
                record_diagnostic(
                    ModelAttemptDiagnostic(
                        attempt=attempt,
                        classification=_with_truncation(
                            "schema_validation_failed", finish_reason
                        ),
                        finish_reason=finish_reason,
                        content_bytes=content_bytes,
                        content_sha256=content_sha256,
                        usage=usage,
                        json_error=_truncation_error(finish_reason),
                        schema_errors=_schema_issues(exc, schema),
                    )
                )
                continue
            except ContractValidationError as exc:
                # Graph-structure rules raise located contract issues while the
                # payload is parsed (GRF-8); they retry exactly like validate().
                record_diagnostic(
                    _contract_failure(
                        exc,
                        schema,
                        attempt=attempt,
                        finish_reason=finish_reason,
                        content_bytes=content_bytes,
                        content_sha256=content_sha256,
                        usage=usage,
                    )
                )
                continue
            try:
                if validate is not None:
                    validate(value)
            except (ValueError, ContractError) as exc:
                record_diagnostic(
                    _contract_failure(
                        exc if isinstance(exc, ContractValidationError) else None,
                        schema,
                        attempt=attempt,
                        finish_reason=finish_reason,
                        content_bytes=content_bytes,
                        content_sha256=content_sha256,
                        usage=usage,
                    )
                )
                continue
            if self._job_control is not None:
                self._job_control.checkpoint()
            record_diagnostic(
                ModelAttemptDiagnostic(
                    attempt=attempt,
                    classification="success",
                    finish_reason=finish_reason,
                    content_bytes=content_bytes,
                    content_sha256=content_sha256,
                    usage=usage,
                )
            )
            if attempt > 1:
                LOGGER.warning(
                    "Model recovered input=%s schema=%s model=%s "
                    "attempts=%s prior_failures=%s",
                    input_id,
                    schema.__name__,
                    model,
                    attempt,
                    ",".join(
                        str(
                            item.contract_issue
                            or item.request_error_kind
                            or item.classification
                        )
                        for item in diagnostics[:-1]
                    ),
                )
            return Completion(
                value=value,
                attempts=attempt,
                content_sha256=content_sha256,
                diagnostics=tuple(diagnostics),
                model=model,
            )
        frozen_diagnostics = tuple(diagnostics)
        if frozen_diagnostics and all(
            item.classification == "request_failed" for item in frozen_diagnostics
        ):
            last = frozen_diagnostics[-1]
            if last.request_error_kind is None:  # pragma: no cover - model invariant
                raise AssertionError("request failure is missing its safe kind")
            raise ModelRequestError(
                last.request_error_kind,
                http_status=last.request_http_status,
                diagnostics=frozen_diagnostics,
            )
        attempt_diagnostics = [
            item.model_dump(mode="json", exclude_none=True)
            for item in frozen_diagnostics
        ]
        summary = json.dumps(
            attempt_diagnostics,
            ensure_ascii=True,
            separators=(",", ":"),
        )
        message = (
            "Model returned no valid JSON after "
            f"{len(diagnostics)} fresh attempts: " + summary
        )
        raise ModelOutputError(
            message[:_MAX_ERROR_MESSAGE], diagnostics=frozen_diagnostics
        )


def _classify_request_error(
    exc: Exception,
) -> tuple[ModelRequestErrorKind, int | None]:
    if isinstance(exc, (APITimeoutError, httpx.TimeoutException)):
        return ModelRequestErrorKind.TIMEOUT, None
    if isinstance(
        exc, (APIConnectionError, httpx.NetworkError, httpx.RemoteProtocolError)
    ):
        return ModelRequestErrorKind.CONNECTION, None
    if isinstance(exc, APIStatusError):
        status = exc.status_code
        if status == 400:
            kind = ModelRequestErrorKind.INVALID_REQUEST
        elif status == 401:
            kind = ModelRequestErrorKind.AUTHENTICATION
        elif status == 402:
            kind = ModelRequestErrorKind.INSUFFICIENT_BALANCE
        elif status == 403:
            kind = ModelRequestErrorKind.PERMISSION_DENIED
        elif status == 404:
            kind = ModelRequestErrorKind.NOT_FOUND
        elif status == 409:
            kind = ModelRequestErrorKind.CONFLICT
        elif status == 422:
            kind = ModelRequestErrorKind.INVALID_PARAMETERS
        elif status == 429:
            kind = ModelRequestErrorKind.RATE_LIMITED
        elif status >= 500:
            kind = ModelRequestErrorKind.PROVIDER_ERROR
        else:
            kind = ModelRequestErrorKind.HTTP_ERROR
        return kind, status
    # The SDK can raise APIError directly while iterating an HTTP-200 SSE body.
    # There is no HTTP failure status to invent here. Only explicit transient
    # codes qualify as an availability failure; auth/quota/unknown still stop.
    if isinstance(exc, APIError) and exc.code in {
        "server_error",
        "rate_limit_exceeded",
    }:
        return ModelRequestErrorKind.CONNECTION, None
    return ModelRequestErrorKind.UNEXPECTED, None


def _retryable_request_error(kind: ModelRequestErrorKind) -> bool:
    return kind in {
        ModelRequestErrorKind.CONNECTION,
        ModelRequestErrorKind.TIMEOUT,
        ModelRequestErrorKind.RATE_LIMITED,
        ModelRequestErrorKind.PROVIDER_ERROR,
    }


def _first_choice(response: Any) -> Any:
    try:
        return response.choices[0]
    except (AttributeError, IndexError, TypeError):
        return None


def _finish_reason(choice: Any) -> str | None:
    try:
        value = choice.finish_reason
    except AttributeError:
        return None
    if not isinstance(value, str):
        return None
    return value if value in _FINISH_REASONS else "other"


def _token_usage(response: Any) -> ModelTokenUsage | None:
    try:
        usage = response.usage
    except AttributeError:
        return None
    values: dict[str, int] = {}
    for field in _USAGE_FIELDS:
        try:
            value = getattr(usage, field)
        except AttributeError:
            continue
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            values[field] = value
    return ModelTokenUsage(**values) if values else None


def _content_fingerprint(content: Any) -> tuple[int | None, str | None]:
    if not isinstance(content, str):
        return None, None
    encoded = content.encode("utf-8")
    return len(encoded), hashlib.sha256(encoded).hexdigest()


def _fresh_messages(
    *,
    system_prompt: str,
    user_content: str,
    previous_diagnostic: ModelAttemptDiagnostic | None,
    schema: type[BaseModel],
) -> list[dict[str, str]]:
    retry_feedback = _retry_feedback(previous_diagnostic, schema=schema)
    effective_system_prompt = system_prompt
    if retry_feedback is not None:
        effective_system_prompt += (
            "\n\nRetry correction from bounded validator diagnostics only:\n"
            + retry_feedback
        )
    return [
        {"role": "system", "content": effective_system_prompt},
        {"role": "user", "content": user_content},
    ]


_VISUAL_PROVENANCE_ISSUES = frozenset(
    {
        ContractIssue.PROVENANCE_VISUAL_BLOCK_AMBIGUOUS,
        ContractIssue.PROVENANCE_VISUAL_VALUE_NOT_FOUND,
        ContractIssue.PROVENANCE_VISUAL_VALUE_UNVERIFIABLE,
        ContractIssue.PROVENANCE_VISUAL_CELL_AMBIGUOUS,
        ContractIssue.PROVENANCE_VISUAL_NUMERIC_MISMATCH,
    }
)
_VISUAL_PROVENANCE_CORRECTION = (
    "Copy every table cell raw_value exactly from one cell of the cited "
    "table's structured.table_body (row is the 1-based data row after header "
    "rows; column is that cell's header text), keep numeric_value equal to that "
    "cell's number or null, and copy figure points only from structured series. "
    "Bind each table or figure anchor to one source block through its "
    "source_span_id or exact region. A table or figure without structured "
    "cells or points in the compact Document IR gets no visual_evidence."
)


def _retry_feedback(
    diagnostic: ModelAttemptDiagnostic | None,
    *,
    schema: type[BaseModel] | None = None,
) -> str | None:
    """Build bounded guidance from safe metadata, never prior model text."""

    detail = _retry_feedback_detail(diagnostic, schema=schema)
    if detail is None:
        return None
    feedback = _RETRY_COMPLETION_GUARD + "\n" + detail
    return feedback[:_MAX_RETRY_FEEDBACK]


def _retry_feedback_detail(
    diagnostic: ModelAttemptDiagnostic | None,
    *,
    schema: type[BaseModel] | None,
) -> str | None:
    """Describe only the immediately preceding bounded failure."""

    if diagnostic is None or diagnostic.classification == "request_failed":
        return None
    if (
        diagnostic.classification == "truncated_json"
        or diagnostic.finish_reason == "length"
    ):
        return (
            "Previous output exhausted the token budget. Preserve the required "
            "coverage of every materially qualifying Claim and every distinct "
            "complete evidence path required by the original prompt, "
            "including every required model or figure anchor. Do not omit an "
            "independent evidence path. Reduce only optional payload: set optional "
            "edge rationale to null; omit edges and visual structures that are not "
            "used by any required path; "
            "deduplicate and reuse anchors and scopes; use the shortest exact "
            "quote spans and concise nonredundant wording and qualifiers. Never "
            "drop a materially qualifying Claim merely to shorten the response. "
            "Keep claim "
            "statements complete and non-telegraphic. Emit minified JSON with no "
            "indentation, optional whitespace, or blank lines, and close every "
            "array and object before the budget ends."
        )
    if diagnostic.classification == "schema_validation_failed":
        error_types = {issue.type for issue in diagnostic.schema_errors}
        corrections: list[str] = []
        if "missing" in error_types:
            corrections.append(
                "Every missing location names a required key: include it; when "
                "that key is nullable and no grounded value exists, emit null"
            )
        if "literal_error" in error_types:
            corrections.append(
                "For every enum field, use an exact authoritative schema value"
            )
        if error_types - {"missing", "literal_error"}:
            corrections.append(
                "Recheck each listed location against its authoritative field type"
            )
        correction = "; ".join(corrections) or (
            "Recheck each listed location against the authoritative schema"
        )
        prefix = (
            "Previous output failed authoritative schema validation. "
            + correction
            + ". Never invent, translate, or normalize schema values. Bounded "
            "loc/type pairs: "
        )
        suffix = (
            " " + _paper_graph_schema_observation_preflight()
            if schema is PaperEvidenceGraph
            else ""
        )
        rendered: list[str] = []
        current_length = len(prefix) + 2 + len(suffix)
        detail_budget = _MAX_RETRY_FEEDBACK - len(_RETRY_COMPLETION_GUARD) - 1
        for issue in diagnostic.schema_errors:
            item = json.dumps(
                {"loc": list(issue.loc), "type": issue.type},
                ensure_ascii=True,
                separators=(",", ":"),
            )
            added_length = len(item) + (1 if rendered else 0)
            if current_length + added_length > detail_budget:
                break
            rendered.append(item)
            current_length += added_length
        return prefix + "[" + ",".join(rendered) + "]" + suffix
    if diagnostic.classification in {"invalid_json", "invalid_json_root"}:
        return (
            "Previous output was not one valid JSON object. Return exactly one "
            "complete JSON object with no prose or wrapper."
        )
    if diagnostic.classification == "empty_content":
        return "Previous output was empty. Return one complete JSON object."
    if diagnostic.classification == "contract_validation_failed":
        contract_issues = _diagnostic_contract_issues(diagnostic)
        if len(contract_issues) > 1:
            return _grouped_contract_feedback(contract_issues)
        issue = (
            contract_issues[0].issue if contract_issues else diagnostic.contract_issue
        )
        safe_diagnostic = _contract_diagnostic(diagnostic)
        if issue is ContractIssue.VERIFICATION_CLAIM_UNASSESSED:
            return (
                "Previous verifier report omitted one or more input Claims. "
                + safe_diagnostic
                + " The safe assessments index identifies the zero-based "
                "position of a missing entry in verification_claim_manifest.claims; "
                "it contains no Claim identity or source text. Regenerate the "
                "complete report and emit exactly one assessment for every Claim "
                "in extraction_graph.claims, following the supplied "
                "verification_claim_manifest.claims in its original order. Copy "
                "each manifest claim_id and scoped_claim_id exactly; do not omit, "
                "merge, or replace any Claim, including a Claim judged "
                "insufficient. Before closing the JSON, verify the assessment "
                "count equals verification_claim_manifest.claim_count and every "
                "identity matches the entry at the same index. Return only the "
                "complete verifier JSON object."
            )
        if issue is ContractIssue.OBSERVATION_PATH_OWNERSHIP:
            return (
                "Previous extraction graph failed graph contract validation. "
                + safe_diagnostic
                + " The safe location is one witness; audit every observation. "
                "Each observation must have exactly one outgoing premise_for edge. "
                "Split a broad observation into separate narrow observations that "
                "may reuse the same source anchor, and attach each to one reasoning "
                "path only. Regenerate and return only one complete compact graph "
                "JSON object."
            )
        if issue is ContractIssue.PROVENANCE_SOURCE_REFERENCE_INVALID:
            return (
                "Previous extraction selected an invalid caller-owned source "
                "reference. "
                + safe_diagnostic
                + " The safe location is one witness; audit every textual "
                "anchor. Copy exactly one supplied trusted_source_spans "
                "source_span_id into locator.source_span_id, never invent or "
                "alter an ID, and leave model-written page, section, region, "
                "and quote null for Compiler hydration. Regenerate and return "
                "only one complete compact graph JSON object."
            )
        if issue is ContractIssue.PROVENANCE_QUOTE_NOT_FOUND:
            return (
                "Previous extraction graph failed graph contract validation. "
                + safe_diagnostic
                + " The safe location is one witness; audit every anchor. Each "
                "locator.quote must be copied exactly as one NFC-normalized "
                "substring from a single source block on locator.page; never "
                "paraphrase or join text across blocks. Regenerate and return only "
                "one complete compact graph JSON object."
            )
        if issue in _VISUAL_PROVENANCE_ISSUES:
            return (
                "Previous extraction graph failed visual source validation. "
                + safe_diagnostic
                + " The safe location is one witness; audit every visual_evidence "
                "item. "
                + _VISUAL_PROVENANCE_CORRECTION
                + " Regenerate and return only one complete compact graph JSON "
                "object."
            )
        if issue is ContractIssue.PROVENANCE_REGION_MISMATCH:
            return (
                "Previous extraction graph failed graph contract validation. "
                + safe_diagnostic
                + " The safe location is one witness; audit every anchor. Each "
                "locator.region must use the exact normalized bounding box of the "
                "same matched source block; never estimate it or copy a nearby "
                "block. If no authoritative box is available, use a null region "
                "while retaining another valid source coordinate. Regenerate and "
                "return only one complete compact graph JSON object."
            )
        citation_contract = _citation_contract_correction(
            schema, issue, safe_diagnostic
        )
        if citation_contract is not None:
            return citation_contract
        claim_comparison_contract = _claim_comparison_contract_correction(schema)
        if claim_comparison_contract is not None:
            return claim_comparison_contract
        return (
            "Previous output failed graph contract validation. "
            + safe_diagnostic
            + " Regenerate exactly one complete JSON object conforming to the "
            "authoritative response schema for this request. Preserve all required "
            "claim or assessment coverage and every complete evidence path. "
            "Recheck references, endpoint types, provenance paths, canonical "
            "uniqueness, and derivation DAG constraints without inventing a lower "
            "node limit or omitting a required collection."
        )
    return None


def _citation_contract_correction(
    schema: type[BaseModel],
    issue: ContractIssue | None,
    safe_diagnostic: str,
) -> str | None:
    if schema.__name__ not in {
        "CitationAssertionDraftBatch",
        "CitationAssessmentBatch",
    }:
        return None
    common = (
        "Previous Citation Reception output failed its closed contract. "
        + safe_diagnostic
        + " Return one complete JSON object with every required field. "
    )
    corrections = {
        ContractIssue.CITATION_MENTION_UNKNOWN: (
            "Use mention_id only from pair.mentions."
        ),
        ContractIssue.CITATION_FUNCTION_UNSUPPORTED: (
            "For each assertion, copy function only from that mention's "
            "candidate_functions."
        ),
        ContractIssue.CITATION_NON_EVALUATIVE: (
            "A mere attribution with no evaluative stance is outside Citation "
            "Reception scope. Remove that assertion; if it is the only one, "
            "return an empty assertions array."
        ),
        ContractIssue.CITATION_ASSERTION_DUPLICATE: (
            "Remove duplicate propositions for the same mention; emit at most "
            "one atomic assertion for that target-specific proposition."
        ),
        ContractIssue.CITATION_TARGET_UNKNOWN: (
            "Use target_entity_id only from pair.cited_entities, or JSON null."
        ),
        ContractIssue.CITATION_TARGET_REQUIRED: (
            "Evidence-backed reception requires a materially matching supplied "
            "B target. If none matches, do not invent a target or a targetless "
            "confirmation; remove the assertion, especially for method-use."
        ),
        ContractIssue.CITATION_BASIS_STANCE_MISMATCH: (
            "Make stance and confirmation_basis coherent: supports uses "
            "verbal_endorsement or consistent_with_citing_evidence; contradicts "
            "uses verbal_critique or contradicted_by_citing_evidence; qualifies "
            "uses verbal_critique or qualified_by_citing_evidence. Use direct "
            "replication, reanalysis, or meta-analytic synthesis only for that "
            "explicit design. mere_attribution is only coherent with unclear."
        ),
        ContractIssue.CITATION_EVIDENCE_INVALID: (
            "Use only supplied entity IDs allowed by the current stage, with no "
            "duplicates."
        ),
        ContractIssue.CITATION_EVIDENCE_REQUIRED: (
            "An evidence-backed basis requires at least one relevant supplied A "
            "Observation, visual, source-anchor, or Reasoning ID in the required "
            "evidence field; otherwise use the stance-matching verbal basis."
        ),
        ContractIssue.CITATION_VERIFIER_HASH_MISMATCH: (
            "Echo extraction_sha256 exactly from the request."
        ),
        ContractIssue.CITATION_VERIFIER_COVERAGE: (
            "Return exactly one assessment for every frozen assertion_id, with "
            "no duplicates or omissions."
        ),
        ContractIssue.CITATION_VERIFIER_STRENGTHENED: (
            "Do not strengthen confirmation_basis beyond the frozen assertion."
        ),
        ContractIssue.CITATION_VERIFIER_DIRECTION_CHANGED: (
            "Do not change consistent, contradicted, or qualified evidence "
            "direction; either echo it or conservatively downgrade to the "
            "stance-matching verbal basis."
        ),
        ContractIssue.CITATION_VERIFIER_TARGET_INVALID: (
            "When target_entity_id is null use not_found, ambiguous, or indirect. "
            "A faithful assessment of a resolved target must include that B "
            "target ID in evidence_ids."
        ),
    }
    correction = corrections.get(issue)
    if correction is None:
        correction = (
            "Recheck all mention, candidate-function, A/B entity, stance/basis, "
            "evidence, hash, and exact-coverage rules."
        )
    return common + correction + " Return only the corrected complete JSON object."


def _claim_comparison_contract_correction(
    schema: type[BaseModel],
) -> str | None:
    """Give value-free retries for the closed Claim-comparison stages."""

    name = schema.__name__
    if name == "ClaimNormalizationDraftBatch":
        return (
            "Previous Claim normalization failed its closed contract. Return one "
            "complete JSON object. Copy every origin_span exactly from user_text; "
            "emit only atomic propositions explicitly present in the input. Never "
            "answer a research question or add a scientific direction, value, "
            "condition, or Scope. Questions must remain uncertain hypotheses or "
            "conditional comparisons. Keep status and clarification coherent; "
            "zero Claims requires needs_user_definition with a clarification."
        )
    if name == "ClaimComparisonDecisionDraftBatch":
        return (
            "Previous Claim comparison failed its closed contract. Return exactly "
            "one decision per supplied pair_id in input order. Use only that "
            "pair's evidence IDs. Recheck relation, direction, stance, Scope "
            "alignment, directness, and polarity together. Disjoint or unknown "
            "Scope cannot prove a direct relation, and disjoint Scope is never "
            "contradiction. supports, equivalent, and contradicts require selected "
            "candidate raw evidence; otherwise use no_relation or "
            "insufficient_to_determine."
        )
    if name == "ClaimComparisonAssessmentBatch":
        return (
            "Previous Claim-comparison verification failed its closed contract. "
            "Echo extraction_sha256 and return exactly one assessment per frozen "
            "comparison_id in input order. Use only frozen selected evidence IDs. "
            "Maintain the frozen classification exactly, change relation only to "
            "an explicitly weaker result, or abstain with "
            "insufficient_to_determine. Never strengthen relation, evidence, "
            "directness, Scope alignment, or confidence."
        )
    return None


def _paper_graph_schema_observation_preflight() -> str:
    """Return a fixed, history-free Observation rule for graph schema retries."""

    return (
        "PaperEvidenceGraph preflight for the new graph: build every Observation "
        "source-first. For text numbers, emit/reuse the trusted SourceAnchor with "
        "the shortest exact quote stating the same complete subject/context, "
        "retained numbers and units; put its ID in that Observation.anchor_ids "
        "and emit its direct grounds edge. For visual numbers, require reliable "
        "cells/points with direct grounds to that Observation; other-node or "
        "unlinked evidence never counts. Audit statement and raw_value "
        "independently and retain every grounded number in mixed fields. If a "
        "number has no own source, remove only it, keep a complete qualitative "
        "fact with subject/context, and emit lossless grounded raw_value/unit or "
        "null both if none. Never move, spell out, approximate/round, turn into a "
        "range/ratio, or re-encode an unsupported number. Preserve required Claim "
        "coverage and complete paths; never invent facts."
    )


def _contract_diagnostic(diagnostic: ModelAttemptDiagnostic) -> str:
    """Render only the bounded allowlisted issue and sanitized field path."""

    issue = diagnostic.contract_issue
    issue_feedback = (
        "Safe issue code: " + issue.value + ". " if issue is not None else ""
    )
    rendered = json.dumps(
        {
            "issue": issue.value if issue is not None else None,
            "loc": list(diagnostic.contract_loc),
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return issue_feedback + "Safe validator diagnostic: " + rendered + "."


def _grouped_contract_feedback(issues: tuple[ModelContractIssue, ...]) -> str:
    """Render several same-output issues without retaining rejected values."""

    rendered = json.dumps(
        [{"issue": item.issue.value, "loc": list(item.loc)} for item in issues],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    prefix = (
        "Only last-output safe diagnostics: "
        + rendered
        + ". Unstable loc witnesses; audit all rules across full new graph. "
    )
    corrections: list[str] = []
    for item in issues:
        correction = _grouped_contract_correction(item.issue)
        if correction not in corrections:
            corrections.append(correction)
    suffix = (
        " Return complete compact graph JSON; keep all materially qualifying "
        "Claims and distinct complete evidence paths."
    )
    detail_budget = _MAX_RETRY_FEEDBACK - len(_RETRY_COMPLETION_GUARD) - 1
    detail = prefix
    for correction in corrections:
        candidate = detail + correction + " "
        if len(candidate) + len(suffix) > detail_budget:
            break
        detail = candidate
    return (detail + suffix).rstrip()


def _grouped_contract_correction(issue: ContractIssue) -> str:
    if issue is ContractIssue.PROVENANCE_SOURCE_REFERENCE_INVALID:
        return (
            "For every textual anchor, copy exactly one supplied "
            "trusted_source_spans source_span_id into locator.source_span_id; "
            "never invent or alter it, and leave page, section, region, and "
            "quote null for Compiler hydration."
        )
    if issue is ContractIssue.PROVENANCE_QUOTE_NOT_FOUND:
        return (
            "For every anchor, copy locator.quote exactly as one NFC-normalized "
            "substring from one source block on locator.page; never paraphrase "
            "or join blocks."
        )
    if issue is ContractIssue.PROVENANCE_REGION_MISMATCH:
        return (
            "For every anchor, use the exact normalized bounding box of the same "
            "matched source block for locator.region; if no authoritative box "
            "exists, use null while retaining another valid coordinate."
        )
    if issue in _VISUAL_PROVENANCE_ISSUES:
        return _VISUAL_PROVENANCE_CORRECTION
    if issue is ContractIssue.OBSERVATION_PATH_OWNERSHIP:
        return (
            "For every observation, emit exactly one outgoing premise_for edge; "
            "split a broad observation into narrow observations when separate "
            "reasoning paths are required."
        )
    return (
        "Recheck the listed location against the authoritative response schema, "
        "references, provenance, canonical uniqueness, and derivation rules "
        "without omitting any required collection."
    )


def _contract_failure(
    error: ContractValidationError | None,
    schema: type[BaseModel],
    *,
    attempt: int,
    finish_reason: str | None,
    content_bytes: int | None,
    content_sha256: str | None,
    usage: ModelTokenUsage | None,
) -> ModelAttemptDiagnostic:
    """Record a contract failure with only its allowlisted, located issues."""

    contract_issues = _safe_contract_issues(error, schema)
    primary = contract_issues[0] if contract_issues else None
    return ModelAttemptDiagnostic(
        attempt=attempt,
        classification=_with_truncation("contract_validation_failed", finish_reason),
        finish_reason=finish_reason,
        content_bytes=content_bytes,
        content_sha256=content_sha256,
        usage=usage,
        json_error=_truncation_error(finish_reason),
        contract_issue=None if primary is None else primary.issue,
        contract_loc=() if primary is None else primary.loc,
        contract_issues=contract_issues,
    )


def _with_truncation(classification: str, finish_reason: str | None) -> str:
    return "truncated_json" if finish_reason == "length" else classification


def _truncation_error(finish_reason: str | None) -> ModelJsonFailure | None:
    if finish_reason != "length":
        return None
    return ModelJsonFailure(classification="truncated_json")


def _json_failure(
    error: RecursionError | ValueError, finish_reason: str | None
) -> ModelJsonFailure:
    if finish_reason == "length":
        classification = "truncated_json"
    elif isinstance(error, json.JSONDecodeError):
        classification = "syntax_error"
    else:
        detail = str(error)
        if detail.startswith("duplicate JSON object key"):
            classification = "duplicate_key"
        elif detail.startswith("non-standard JSON constant"):
            classification = "non_standard_constant"
        elif detail.startswith("JSON number is outside"):
            classification = "non_finite_number"
        else:
            classification = "strict_value_error"
    pos = error.pos if isinstance(error, json.JSONDecodeError) else None
    return ModelJsonFailure(classification=classification, pos=pos)


def _schema_issues(
    error: ValidationError, schema: type[BaseModel]
) -> tuple[ModelSchemaIssue, ...]:
    issues: list[ModelSchemaIssue] = []
    for item in error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    )[:_MAX_SCHEMA_ERRORS]:
        raw_type = item.get("type")
        issue_type = (
            raw_type
            if isinstance(raw_type, str) and _SAFE_ERROR_TYPE.fullmatch(raw_type)
            else "validation_error"
        )
        issues.append(
            ModelSchemaIssue(
                loc=_safe_loc(item.get("loc", ()), schema),
                type=issue_type,
            )
        )
    return tuple(issues)


def _safe_contract_issues(
    error: ContractValidationError | None,
    schema: type[BaseModel],
) -> tuple[ModelContractIssue, ...]:
    """Bound the diagnostic to distinct issue codes, one located witness each."""

    if error is None:
        return ()
    issues: list[ModelContractIssue] = []
    seen_issues: set[ContractIssue] = set()
    for violation in error.violations[:_MAX_CONTRACT_VIOLATIONS_SCANNED]:
        issue = violation.issue
        if not isinstance(issue, ContractIssue) or issue in seen_issues:
            continue
        if len(seen_issues) == _MAX_CONTRACT_ISSUES:
            break
        seen_issues.add(issue)
        issues.append(
            ModelContractIssue(issue=issue, loc=_safe_loc(violation.loc, schema))
        )
    return tuple(issues)


def _diagnostic_contract_issues(
    diagnostic: ModelAttemptDiagnostic,
) -> tuple[ModelContractIssue, ...]:
    """Read grouped diagnostics while supporting legacy singleton records."""

    if diagnostic.contract_issues:
        return diagnostic.contract_issues
    if diagnostic.contract_issue is None:
        return ()
    return (
        ModelContractIssue(
            issue=diagnostic.contract_issue,
            loc=diagnostic.contract_loc,
        ),
    )


def _safe_loc(
    raw_loc: object,
    schema: type[BaseModel],
) -> tuple[str | int, ...]:
    """Bound a validator path to schema field names and non-negative indices."""

    if not isinstance(raw_loc, (tuple, list)):
        return ()
    field_names = _schema_field_names(schema)
    loc: list[str | int] = []
    for part in raw_loc[:_MAX_LOC_PARTS]:
        if (
            isinstance(part, int)
            and not isinstance(part, bool)
            and 0 <= part <= 1_000_000
        ):
            loc.append(part)
        elif isinstance(part, str) and part in field_names:
            loc.append(part[:48])
        else:
            loc.append("<key>")
    return tuple(loc)


@cache
def _schema_field_names(schema: type[BaseModel]) -> frozenset[str]:
    """Cache the authoritative field allowlist across fresh model attempts."""

    names: set[str] = set()
    pending: list[Any] = [schema.model_json_schema()]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                names.update(
                    key
                    for key in properties
                    if isinstance(key, str) and _SAFE_LOC_NAME.fullmatch(key)
                )
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    return frozenset(names)


__all__ = ["Completion", "ModelJsonClient"]
