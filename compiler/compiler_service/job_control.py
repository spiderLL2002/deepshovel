"""Per-compile Model resource budgets, deadlines, and cancellation checks."""

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from .config import DEEPSEEK_MAX_ATTEMPTS, GPT_MAX_ATTEMPTS
from .errors import (
    ModelJobBudgetExceeded,
    ModelJobDeadlineExceeded,
)

_REQUEST_CANCELLATION: ContextVar[Callable[[], None] | None] = ContextVar(
    "model_request_cancellation", default=None
)


@contextmanager
def model_request_cancellation(check: Callable[[], None]) -> Iterator[None]:
    """Carry a caller disconnect into controls created inside its worker."""
    token = _REQUEST_CANCELLATION.set(check)
    try:
        yield
    finally:
        _REQUEST_CANCELLATION.reset(token)


@dataclass(frozen=True, slots=True)
class ModelJobLimits:
    """Hard limits shared by every model stage in one compile invocation."""

    # The Compiler uses one whole-paper
    # extraction and, when Claims exist, one whole-graph verifier. Each logical
    # call owns six GPT attempts and up to three DeepSeek fallback attempts.
    # These ceilings make that simple
    # topology an executable invariant instead of merely a prompt convention.
    max_completion_calls: int = 2
    max_request_attempts: int = 2 * (GPT_MAX_ATTEMPTS + DEEPSEEK_MAX_ATTEMPTS)
    max_prompt_bytes: int = 192 * 1024 * 1024
    # No application time cutoff. Optional explicit limits remain available to
    # offline fault tests and historical callers, not production stage policy.
    deadline_seconds: float | None = None
    max_request_seconds: float | None = None

    def __post_init__(self) -> None:
        integer_fields = (
            "max_completion_calls",
            "max_request_attempts",
            "max_prompt_bytes",
        )
        for field_name in integer_fields:
            value = getattr(self, field_name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{field_name} must be a positive integer")
        for field_name in ("deadline_seconds", "max_request_seconds"):
            value = getattr(self, field_name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{field_name} must be a positive number")
            if value <= 0 or not math.isfinite(value):
                raise ValueError(f"{field_name} must be a positive number")


DEFAULT_GPT_JOB_LIMITS = ModelJobLimits()


@dataclass(frozen=True, slots=True)
class ModelJobUsage:
    """Value-free counters suitable for diagnostics and tests."""

    completion_calls: int
    request_attempts: int
    prompt_bytes: int


class ModelJobControl:
    """Thread-safe accounting and cooperative cancellation for one compile."""

    def __init__(
        self,
        *,
        limits: ModelJobLimits = DEFAULT_GPT_JOB_LIMITS,
        check_cancelled: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limits = limits
        self._check_cancelled = check_cancelled or _REQUEST_CANCELLATION.get()
        self._clock = clock
        self._started_at = clock()
        self._deadline = (
            None
            if limits.deadline_seconds is None
            else self._started_at + limits.deadline_seconds
        )
        self._lock = threading.Lock()
        self._completion_calls = 0
        self._request_attempts = 0
        self._prompt_bytes = 0

    @property
    def usage(self) -> ModelJobUsage:
        with self._lock:
            return ModelJobUsage(
                completion_calls=self._completion_calls,
                request_attempts=self._request_attempts,
                prompt_bytes=self._prompt_bytes,
            )

    def checkpoint(self, *, enforce_deadline: bool = True) -> None:
        """Reject cancelled or expired work without mutating its counters."""

        if self._check_cancelled is not None:
            self._check_cancelled()
        now = self._clock()
        with self._lock:
            if enforce_deadline:
                self._require_before_deadline(now)

    def begin_completion(self, *, enforce_deadline: bool = True) -> None:
        """Reserve one logical JSON completion across all pipeline stages."""

        self.checkpoint(enforce_deadline=enforce_deadline)
        now = self._clock()
        with self._lock:
            if enforce_deadline:
                self._require_before_deadline(now)
            if self._completion_calls >= self.limits.max_completion_calls:
                raise ModelJobBudgetExceeded(
                    "Model compile job exceeded its completion-call budget"
                )
            self._completion_calls += 1

    def begin_request_attempt(
        self,
        *,
        messages: Sequence[dict[str, str]],
        enforce_deadline: bool = True,
    ) -> float | None:
        """Atomically reserve one outbound SDK attempt and return its timeout."""

        self.checkpoint(enforce_deadline=enforce_deadline)
        prompt_bytes = _messages_size_bytes(messages)
        now = self._clock()
        with self._lock:
            if enforce_deadline:
                self._require_before_deadline(now)
            prospective = (
                (
                    "request-attempt",
                    self._request_attempts + 1,
                    self.limits.max_request_attempts,
                ),
                (
                    "prompt-byte",
                    self._prompt_bytes + prompt_bytes,
                    self.limits.max_prompt_bytes,
                ),
            )
            for resource, value, limit in prospective:
                if value > limit:
                    raise ModelJobBudgetExceeded(
                        f"Model compile job exceeded its {resource} budget"
                    )
            self._request_attempts += 1
            self._prompt_bytes += prompt_bytes
            if not enforce_deadline:
                return None
            remaining = None if self._deadline is None else self._deadline - now
        if self.limits.max_request_seconds is None:
            return remaining
        if remaining is None:
            return float(self.limits.max_request_seconds)
        return min(float(self.limits.max_request_seconds), remaining)

    def _require_before_deadline(self, now: float) -> None:
        if self._deadline is not None and now >= self._deadline:
            raise ModelJobDeadlineExceeded("Model compile job exceeded its deadline")


def _messages_size_bytes(messages: Sequence[dict[str, str]]) -> int:
    """Count the complete JSON message payload sent again on every retry."""

    payload: Any = messages
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return len(encoded)


__all__ = [
    "DEFAULT_GPT_JOB_LIMITS",
    "ModelJobControl",
    "ModelJobLimits",
    "ModelJobUsage",
    "model_request_cancellation",
]
