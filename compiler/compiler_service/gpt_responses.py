"""Responses streaming transport for the existing bounded JSON validator loop.

Only final message text is inspected. Reasoning items are never read or logged.
The observer receives safe request metadata and final output text,
never credentials or unfiltered headers.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
from openai import APIConnectionError, APITimeoutError
from openai.lib._pydantic import to_strict_json_schema

from .interactive_model_stream import consume_model_stream

# Extraction keeps the strongest requested setting. Simpler input preparation
# uses medium; evidence-sensitive judgments use high. Unknown schemas retain max.
STAGE_REASONING_EFFORT = {
    "PaperEvidenceGraph": "max",
    "VerificationReport": "high",
    "ContextQueryPlanningOutput": "medium",
    "TaskOperationProposal": "medium",
    "QueryPlanPayload": "medium",
    "ClaimNormalizationDraftBatch": "medium",
    "ContextTaskRelationDecisionsWire": "high",
    "RelationDecisionBatch": "high",
    "ClaimComparisonDecisionDraftBatch": "high",
    "ClaimComparisonAssessmentBatch": "high",
    "CitationAssertionDraftBatch": "high",
    "CitationAssessmentBatch": "high",
}


class IncompleteModelOutput(ValueError):
    """A terminal but unfinished output is not a connection outage."""

    def __init__(self, reason: str | None):
        self.finish_reason = {
            "max_output_tokens": "length",
            "length": "length",  # Chat Completions backup transport
            "content_filter": "content_filter",
        }.get(reason, "other")
        self.code = (
            reason if reason in {"max_output_tokens", "content_filter"} else "other"
        )
        self.usage = None
        self.content_bytes = None
        self.content_sha256 = None
        super().__init__("Responses output was incomplete")


# Codes are allowlisted, not copied from provider messages (which may echo input).
_SAFE_PROVIDER_CODES = {
    "server_error",
    "rate_limit_exceeded",
    "invalid_api_key",
    "insufficient_quota",
    "invalid_request_error",
    "model_not_found",
    "permission_denied",
    "context_length_exceeded",
    "max_output_tokens",
    "content_filter",
}


def safe_failure_context(exc: Exception) -> dict:
    """Persist code locations and types, never messages, locals, input or paths."""
    name = type(exc).__name__
    result = {
        "request_exception_type": name
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", name)
        else "Exception"
    }
    stage = getattr(exc, "_model_error_stage", None)
    if stage in {"request", "stream", "finalize"}:
        result["request_error_stage"] = stage
    code = getattr(exc, "code", None)
    if isinstance(code, str):
        result["request_provider_code"] = (
            code if code in _SAFE_PROVIDER_CODES else "other"
        )
    frame = exc.__traceback__
    while frame is not None:
        # Only our own parser/caller positions; no arbitrary filesystem paths.
        filename = frame.tb_frame.f_code.co_filename.replace("\\", "/")
        if filename.endswith(
            ("/compiler_service/gpt_responses.py", "/compiler_service/model_client.py")
        ):
            result["request_error_location"] = (
                f"{filename.rsplit('/', 1)[-1]}:{frame.tb_lineno}"
            )
        frame = frame.tb_next
    return result


def safe_http_error_metadata(exc: Exception) -> dict:
    """Safe provenance and bounded plain-text HTTP rejection details."""
    result = {}
    for name in ("code", "type"):
        value = getattr(exc, name, None)
        if (
            isinstance(value, str)
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", value)
            and not value.lower().startswith(("sk-", "sk_"))
        ):
            result["provider_error_" + name] = value
    response = getattr(exc, "response", None)
    if isinstance(response, httpx.Response):
        if response.status_code == 403:
            body = response.text
            result["http_error_body_bytes"] = len(response.content)
            if len(body) <= 500 and response.headers.get("content-type", "").startswith(
                "text/plain"
            ):
                body = re.sub(r"(?i)bearer\s+\S+|sk-[A-Za-z0-9_-]+", "[REDACTED]", body)
                result["http_rejection_text"] = body
        patterns = {
            "x-oneapi-request-id": r"[A-Za-z0-9_.:-]{1,128}",
            "x-upstream-request-id": r"[A-Za-z0-9_.:-]{1,128}",
            "x-request-id": r"[A-Za-z0-9_.:-]{1,128}",
            "x-new-api-request-id": r"[A-Za-z0-9_.:-]{1,128}",
            "request-id": r"[A-Za-z0-9_.:-]{1,128}",
            "cf-ray": r"[a-fA-F0-9]{8,32}(?:-[A-Z]{3})?",
            "server": r"[A-Za-z][A-Za-z0-9 ./_-]{0,63}",
            "content-type": (
                r"[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+(?:; ?charset=[A-Za-z0-9_-]+)?"
            ),
        }
        result["response_headers"] = {
            name: response.headers[name]
            for name, pattern in patterns.items()
            if name in response.headers
            and re.fullmatch(pattern, response.headers[name])
        }
    return result


class GptResponsesTransport:
    def __init__(
        self,
        sdk: Any,
        *,
        observer: Callable[[dict, str | None], None] | None = None,
        reasoning_effort: str | None = None,
    ):
        self.sdk = sdk
        self.observer = observer
        if reasoning_effort not in {None, "medium", "high", "max"}:
            raise ValueError("Unsupported stage reasoning effort")
        self.reasoning_effort = reasoning_effort

    def create(self, *, response_schema: Any, **request: Any) -> Any:
        flow = self._response_flow(response_schema=response_schema, **request)
        arguments, on_event, on_stream = next(flow)
        stream = None
        try:
            stream = self.sdk.responses.create(**arguments)
            on_stream(stream)
            for event in stream:
                if on_event(event):
                    break
        except BaseException as error:
            return self._finish_flow(flow, stream=stream, error=error)
        return self._finish_flow(flow, stream=stream)

    async def acreate(
        self,
        *,
        response_schema: Any,
        timeout: None = None,
        interactive: bool = True,
        **request: Any,
    ) -> Any:
        # All interactive attempts stay alive until success, failure or cancellation.
        flow = self._response_flow(
            response_schema=response_schema,
            timeout=None,
            interactive=interactive,
            **request,
        )
        arguments, on_event, on_stream = next(flow)
        try:
            await consume_model_stream(
                self.sdk,
                create_stream=lambda client: client.responses.create(**arguments),
                on_event=on_event,
                on_stream=on_stream,
            )
        except BaseException as error:
            return self._finish_flow(flow, error=error)
        return self._finish_flow(flow)

    @staticmethod
    def _finish_flow(flow, *, stream=None, error=None):
        try:
            flow.send((stream, error))
        except StopIteration as completed:
            return completed.value
        raise RuntimeError("unexpected Responses parser suspension")

    def _response_flow(self, *, response_schema: Any, **request: Any):
        """One parser/audit flow shared by synchronous batch and async attempts."""
        started = time.monotonic()
        timeout = request.get("timeout")
        effort = self.reasoning_effort or STAGE_REASONING_EFFORT.get(
            response_schema.__name__, "max"
        )
        if request.get("interactive", False) and response_schema.__name__ in {
            "InteractiveRelationRankingOutput",
            "InteractiveContextPlanningOutput",
        }:
            effort = "medium"
        if response_schema.__name__ == "PaperEvidenceGraph":
            effort = "max"
        metadata = {
            "started_at": datetime.now(UTC).isoformat(),
            "schema": response_schema.__name__,
            "requested_model": request["model"],
            "reasoning_effort": effort,
            "stream": True,
            "store": False,
            "timeout_seconds": timeout,
            "system_prompt_sha256": hashlib.sha256(
                request["messages"][0]["content"].encode()
            ).hexdigest(),
            "input_sha256": hashlib.sha256(
                request["messages"][1]["content"].encode()
            ).hexdigest(),
        }
        text = None
        final_parts = {}
        stream = None
        http_request = httpx.Request(
            "POST", str(self.sdk.base_url).rstrip("/") + "/responses"
        )
        stage = "request"
        try:
            stream_arguments = dict(
                model=request["model"],
                input=request["messages"],
                reasoning={"effort": effort},
                stream=True,
                store=False,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": response_schema.__name__,
                        "strict": True,
                        "schema": (
                            request["response_json_schema"]
                            if request.get("response_json_schema") is not None
                            else to_strict_json_schema(response_schema)
                        ),
                    }
                },
                timeout=timeout,
            )

            # Explicitly reserve space for private reasoning plus the bounded
            # relation JSON rather than leaving the provider allowance unspecified.
            # This is a token allowance, never an interactive time deadline.
            if (
                request.get("interactive", False)
                and response_schema.__name__ == "RelationDecisionBatch"
            ):
                stream_arguments["max_output_tokens"] = 32_768
                metadata["requested_max_output_tokens"] = 32_768

            def observe_stream(value):
                nonlocal stage
                stage = "stream"
                # Only allowlisted correlation IDs, never arbitrary response headers.
                response = getattr(value, "response", None)
                if isinstance(response, httpx.Response):
                    metadata["request_ids"] = {
                        name: response.headers[name]
                        for name in (
                            "x-request-id",
                            "x-new-api-request-id",
                            "request-id",
                            "x-oneapi-request-id",
                            "x-upstream-request-id",
                        )
                        if name in response.headers
                        and re.fullmatch(
                            r"[A-Za-z0-9_.:-]{1,128}", response.headers[name]
                        )
                    }

            completed = None

            def consume_event(event):
                nonlocal completed
                if timeout is not None and time.monotonic() - started >= timeout:
                    raise APITimeoutError(request=http_request)
                if event.type in {
                    "response.output_text.delta",
                    "response.output_text.done",
                }:
                    index = (event.output_index, event.content_index)
                    if event.type == "response.output_text.done":
                        final_parts[index] = event.text
                    else:
                        final_parts[index] = final_parts.get(index, "") + event.delta
                if event.type == "response.completed":
                    if event.response.status != "completed":
                        raise APIConnectionError(
                            message="Responses completion status mismatch",
                            request=http_request,
                        )
                    completed = event.response
                    return True
                if event.type == "response.incomplete":
                    metadata["terminal_event"] = event.type
                    details = getattr(event.response, "incomplete_details", None)
                    failure = IncompleteModelOutput(getattr(details, "reason", None))
                    # Fingerprint final-answer text only; never inspect reasoning.
                    partial = "".join(
                        final_parts[index] for index in sorted(final_parts)
                    ).encode()
                    failure.content_bytes = len(partial)
                    failure.content_sha256 = hashlib.sha256(partial).hexdigest()
                    metadata["output_bytes"] = failure.content_bytes
                    metadata["output_sha256"] = failure.content_sha256
                    raw_usage = getattr(event.response, "usage", None)
                    failure.usage = SimpleNamespace(
                        prompt_tokens=getattr(raw_usage, "input_tokens", None),
                        completion_tokens=getattr(raw_usage, "output_tokens", None),
                        total_tokens=getattr(raw_usage, "total_tokens", None),
                    )
                    metadata["incomplete_reason"] = failure.code
                    metadata["usage"] = vars(failure.usage)
                    metadata["reasoning_output_tokens"] = getattr(
                        getattr(raw_usage, "output_tokens_details", None),
                        "reasoning_tokens",
                        None,
                    )
                    raise failure
                if event.type in {"response.failed", "error"}:
                    metadata["terminal_event"] = event.type
                    failure = getattr(getattr(event, "response", None), "error", None)
                    code = getattr(failure, "code", None) or getattr(
                        event, "code", None
                    )
                    # Only explicit transient server failures qualify as an
                    # availability failure. Never downgrade on unknown errors.
                    if code not in {"server_error", "rate_limit_exceeded"}:
                        failure_error = ValueError(
                            "Responses returned a nontransient or unknown "
                            "terminal error"
                        )
                        failure_error.code = code
                        raise failure_error
                    raise APIConnectionError(
                        message="Responses stream did not complete",
                        request=http_request,
                    )
                return False

            stream, error = yield stream_arguments, consume_event, observe_stream
            if error is not None:
                raise error
            if completed is None:
                raise APIConnectionError(
                    message="Responses stream ended without response.completed",
                    request=http_request,
                )
            stage = "finalize"
            text = "".join(
                part.text
                for item in completed.output
                if item.type == "message"
                for part in item.content
                if part.type == "output_text"
            )
            if not text:
                text = "".join(final_parts[index] for index in sorted(final_parts))
            raw_usage = completed.usage
            metadata["usage_details"] = (
                None
                if raw_usage is None
                else {
                    "cached_input_tokens": getattr(
                        getattr(raw_usage, "input_tokens_details", None),
                        "cached_tokens",
                        None,
                    ),
                    "reasoning_output_tokens": getattr(
                        getattr(raw_usage, "output_tokens_details", None),
                        "reasoning_tokens",
                        None,
                    ),
                }
            )
            usage = (
                None
                if raw_usage is None
                else SimpleNamespace(
                    prompt_tokens=raw_usage.input_tokens,
                    completion_tokens=raw_usage.output_tokens,
                    total_tokens=raw_usage.total_tokens,
                )
            )
            metadata.update(
                status="completed",
                reported_model=completed.model,
                response_id=completed.id,
                output_bytes=len(text.encode()),
                output_sha256=hashlib.sha256(text.encode()).hexdigest(),
                usage=None if usage is None else vars(usage),
            )
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop", message=SimpleNamespace(content=text)
                    )
                ],
                usage=usage,
            )
        except asyncio.CancelledError:
            metadata.update(status="cancelled")
            raise
        except Exception as exc:
            exc._model_error_stage = stage
            text = (
                text
                or "".join(final_parts[index] for index in sorted(final_parts))
                or None
            )
            metadata.update(
                status="failed",
                error_type=type(exc).__name__,
                http_status=getattr(exc, "status_code", None),
            )
            metadata.update(safe_http_error_metadata(exc))
            metadata.update(safe_failure_context(exc))
            metadata["output_is_partial"] = text is not None
            raise
        finally:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    metadata["stream_close_failed"] = True
            metadata["elapsed_seconds"] = round(time.monotonic() - started, 3)
            metadata["finished_at"] = datetime.now(UTC).isoformat()
            if self.observer is not None:
                # An optional audit sink must not change retry/validation behavior.
                with suppress(Exception):
                    self.observer(metadata, text)
