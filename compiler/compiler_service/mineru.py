"""Bounded remote MinerU boundary plus explicit fake/unavailable test backends."""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, BinaryIO, Protocol

import httpx

from .config import (
    MAX_MINERU_RESPONSE_BYTES,
    MAX_MINERU_TIMEOUT_SECONDS,
    MAX_UPLOAD_BYTES,
    MINERU_API_URL,
    MINERU_BACKEND,
    MINERU_JOBS_URL,
    MINERU_MAX_ATTEMPTS,
    MINERU_PARSE_METHOD,
    MINERU_PROTOCOL_VERSION,
    validate_mineru_token,
    validate_mineru_url,
)
from .errors import MineruFailed, MineruUnavailable
from .json_boundary import strict_json_loads

logger = logging.getLogger(__name__)

_RETRY_DELAY_SECONDS = 3.0
# The jobs API documents a 3 s poll; a full parser answers 429/507 with a wait.
_JOB_POLL_SECONDS = 3.0
_JOB_CAPACITY_WAIT_SECONDS = (1.0, 60.0, 15.0)  # minimum, maximum, default
_JOB_STATUS_LIMIT = 256 * 1024
_JOB_ID = re.compile(r"(?:(n[1-9][0-9]{0,2})-)?[0-9a-f]{32}")
_JOB_STATES = {"uploading", "queued", "running", "completed", "failed"}
_JOB_STATUS_KEYS = {
    "task_id",
    "status",
    "file_names",
    "created_at",
    "started_at",
    "completed_at",
    "expires_at",
    "error",
    "status_url",
    "result_url",
}

_PARSE_RESPONSE_KEYS = {
    "task_id",
    "status",
    "backend",
    "file_names",
    "created_at",
    "started_at",
    "completed_at",
    "error",
    "status_url",
    "result_url",
    "queued_ahead",
    "version",
    "results",
}
_RESULT_KEYS = {"md_content", "middle_json", "content_list"}
_FIXED_FORM = {
    "lang_list": "ch",
    "backend": MINERU_BACKEND,
    "parse_method": MINERU_PARSE_METHOD,
    "formula_enable": "true",
    "table_enable": "true",
    "image_analysis": "false",
    "return_md": "true",
    "return_middle_json": "true",
    "return_model_output": "false",
    "return_content_list": "true",
    "return_images": "false",
    "response_format_zip": "false",
    "return_original_file": "false",
    "start_page_id": "0",
    "end_page_id": "99999",
}
# The jobs API fixes every parsing option server-side except images.
_JOB_FORM = {"return_images": "false"}


@dataclass(frozen=True, slots=True)
class MineruAvailability:
    available: bool
    version: str | None
    protocol_version: int | None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class MineruOutput:
    version: str
    method: str
    duration_ms: int
    markdown: str
    content_list: list[dict[str, Any]]
    middle_json: dict[str, Any]


class MineruBackend(Protocol):
    name: str

    def availability(self) -> MineruAvailability: ...

    def parse(self, input_path: Path, *, method: str = "auto") -> MineruOutput: ...


class MineruRemoteBackend:
    """Call the allowlisted lab service with fixed parsing and output options."""

    name = "mineru-remote"

    def __init__(
        self,
        *,
        url: str = MINERU_API_URL,
        timeout_seconds: int = 900,
        api_token: str | None = None,
        max_response_bytes: int = MAX_MINERU_RESPONSE_BYTES,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        validate_mineru_url(url)
        validate_mineru_token(url, api_token)
        if (
            type(timeout_seconds) is not int
            or not 1 <= timeout_seconds <= MAX_MINERU_TIMEOUT_SECONDS
        ):
            raise ValueError(
                f"MinerU timeout must be an integer from 1 to "
                f"{MAX_MINERU_TIMEOUT_SECONDS}"
            )
        if type(max_response_bytes) is not int or max_response_bytes <= 0:
            raise ValueError("MinerU response limit must be a positive integer")
        self.url = url
        self.jobs = url == MINERU_JOBS_URL
        self.health_url = url.rsplit("/", 1)[0] + "/health"
        self._api_token = api_token
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.transport = transport
        self.sleep = sleep
        self.clock = clock
        self._health_cache: tuple[float, MineruAvailability] | None = None
        self._health_lock = threading.Lock()

    def availability(self) -> MineruAvailability:
        with self._health_lock:
            now = self.clock()
            if self._health_cache and now - self._health_cache[0] < 15:
                return self._health_cache[1]
            try:
                payload = self._json_request(
                    "GET",
                    self.health_url,
                    limit=256 * 1024,
                    timeout_seconds=5,
                    max_attempts=1,
                )
                availability = (
                    _validate_jobs_health(payload)
                    if self.jobs
                    else _validate_health(payload)
                )
            except MineruFailed as exc:
                availability = MineruAvailability(False, None, None, str(exc))
            self._health_cache = (now, availability)
            return availability

    def parse(self, input_path: Path, *, method: str = "auto") -> MineruOutput:
        if method != MINERU_PARSE_METHOD:
            raise ValueError("remote MinerU parse method is fixed to auto")
        try:
            size = input_path.stat().st_size
            if size > MAX_UPLOAD_BYTES:
                raise MineruFailed(
                    f"staged MinerU input exceeds {MAX_UPLOAD_BYTES} bytes"
                )
        except OSError as exc:
            raise MineruFailed("Compiler could not read the staged input") from exc
        if size == 0:
            raise MineruFailed("Compiler staged an empty MinerU input")
        started = self.clock()
        if self.jobs:
            output = self._parse_job(input_path, method)
            return replace(output, duration_ms=round((self.clock() - started) * 1_000))
        try:
            with input_path.open("rb") as content:
                files = {"files": (input_path.name, content, _media_type(input_path))}
                output = self._json_request(
                    "POST",
                    self.url,
                    files=files,
                    data=_FIXED_FORM,
                    limit=self.max_response_bytes,
                    validator=lambda payload: _validate_parse_response(
                        payload, method, duration_ms=0
                    ),
                )
        except OSError as exc:
            raise MineruFailed("Compiler could not read the staged input") from exc
        duration_ms = round((self.clock() - started) * 1_000)
        if not isinstance(output, MineruOutput):  # pragma: no cover - type guard
            raise AssertionError("MinerU response validator returned a wrong type")
        return replace(output, duration_ms=duration_ms)

    def _parse_job(self, input_path: Path, method: str) -> MineruOutput:
        """Submit one input to the jobs API, poll its job, then fetch the result."""

        deadline = self.clock() + self.timeout_seconds
        job = self._submit_job(input_path, deadline)
        task_id = job["task_id"]
        # Build job URLs from the configured base, never from server-sent links.
        status_url = self._job_status_url(task_id)
        failed_polls = 0
        while job["status"] != "completed":
            if job["status"] == "failed":
                # The job's error text is untrusted server output; keep the fact.
                raise MineruFailed("remote MinerU job failed")
            self._pause(_JOB_POLL_SECONDS, deadline)
            try:
                job = self._json_request(
                    "GET",
                    status_url,
                    limit=_JOB_STATUS_LIMIT,
                    deadline=deadline,
                    permanent_statuses=frozenset({404}),
                    validator=lambda payload: _validate_job_status(payload, task_id),
                )
                failed_polls = 0
            except _PermanentFailure:
                raise
            except MineruFailed:
                # The job keeps running while the route is briefly unreachable.
                failed_polls += 1
                if failed_polls >= MINERU_MAX_ATTEMPTS:
                    raise
        output = self._json_request(
            "GET",
            f"{status_url}/result",
            limit=self.max_response_bytes,
            # A complete download consumes this service's result. Do not retry
            # validation failures against an already deleted result.
            max_attempts=1,
            deadline=deadline,
            permanent_statuses=frozenset({404}),
            validator=lambda payload: _validate_parse_response(
                payload, method, duration_ms=0, job_id=task_id
            ),
        )
        if not isinstance(output, MineruOutput):  # pragma: no cover - type guard
            raise AssertionError("MinerU response validator returned a wrong type")
        return output

    def _submit_job(self, input_path: Path, deadline: float) -> dict[str, str]:
        try:
            with input_path.open("rb") as content:
                files = {"files": (input_path.name, content, _media_type(input_path))}
                while True:
                    try:
                        job = self._json_request(
                            "POST",
                            self.url,
                            files=files,
                            data=_JOB_FORM,
                            limit=_JOB_STATUS_LIMIT,
                            # An ambiguous upload failure may already have
                            # created a job. Only explicit capacity refusals
                            # below are safe to resubmit.
                            max_attempts=1,
                            deadline=deadline,
                            permanent_statuses=frozenset({413, 422}),
                            capacity_statuses=frozenset({429, 507}),
                            validator=_validate_job_status,
                        )
                        if not isinstance(job, dict):  # pragma: no cover
                            raise AssertionError("MinerU job validator type")
                        return job
                    except _CapacityFull as full:
                        logger.warning(
                            "MinerU jobs API is at capacity; resubmitting in %.0fs",
                            full.wait,
                        )
                        self._pause(full.wait, deadline)
        except OSError as exc:
            raise MineruFailed("Compiler could not read the staged input") from exc

    def _pause(self, seconds: float, deadline: float) -> None:
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise MineruFailed(
                f"remote MinerU exceeded its {self.timeout_seconds}s total deadline"
            )
        self.sleep(min(seconds, remaining))

    def _job_status_url(self, task_id: str) -> str:
        matched = _JOB_ID.fullmatch(task_id)
        node = matched.group(1) if matched else None
        # The public gateway now prefixes IDs with the assigned node and
        # redirects polling/downloads to that node. Derive its exact address;
        # never follow arbitrary server links or enable unrestricted redirects.
        if self.url == MINERU_JOBS_URL and node:
            return f"https://mineru-{node}.iiot-shxt.xyz/jobs/{task_id}"
        return f"{self.url}/{task_id}"

    def _is_job_url(self, url: str) -> bool:
        if not self.jobs:
            return False
        task_id = url.removesuffix("/result").rsplit("/", 1)[-1]
        if not _JOB_ID.fullmatch(task_id):
            return False
        status_url = self._job_status_url(task_id)
        return url in {status_url, f"{status_url}/result"}

    def _json_request(
        self,
        method: str,
        url: str,
        *,
        files: Mapping[str, tuple[str, BinaryIO, str]] | None = None,
        data: Mapping[str, str] | None = None,
        limit: int,
        validator: Callable[[dict[str, Any]], Any] | None = None,
        timeout_seconds: int | None = None,
        max_attempts: int = MINERU_MAX_ATTEMPTS,
        deadline: float | None = None,
        permanent_statuses: frozenset[int] = frozenset(),
        capacity_statuses: frozenset[int] = frozenset(),
    ) -> Any:
        if url not in {self.url, self.health_url} and not self._is_job_url(url):
            raise ValueError("MinerU request URL is outside the configured boundary")
        headers = {"Accept": "application/json"}
        if self._api_token is not None:
            headers["Authorization"] = f"Bearer {self._api_token}"
        last_error = "remote MinerU request failed"
        effective_timeout = timeout_seconds or self.timeout_seconds
        if deadline is None:
            deadline = self.clock() + effective_timeout
        with httpx.Client(
            follow_redirects=False,
            transport=self.transport,
            trust_env=False,
        ) as client:
            for attempt in range(1, max_attempts + 1):
                try:
                    remaining = deadline - self.clock()
                    if remaining <= 0:
                        raise _DeadlineExceeded
                    timeout = httpx.Timeout(
                        remaining,
                        connect=min(5.0, remaining),
                    )
                    _rewind_files(files)
                    with client.stream(
                        method,
                        url,
                        files=files,
                        data=data,
                        headers=headers,
                        timeout=timeout,
                    ) as response:
                        if response.status_code in capacity_statuses:
                            raise _CapacityFull(
                                _retry_after(response.headers.get("retry-after"))
                            )
                        if response.status_code in permanent_statuses:
                            raise _PermanentFailure(
                                f"remote MinerU returned HTTP {response.status_code}"
                            )
                        if not 200 <= response.status_code < 300:
                            raise _RetryableResponse(
                                f"remote MinerU returned HTTP "
                                f"{response.status_code}"
                            )
                        media_type = (
                            response.headers.get("content-type", "")
                            .split(";", 1)[0]
                            .strip()
                            .lower()
                        )
                        if media_type != "application/json":
                            raise _RetryableResponse(
                                "remote MinerU returned a non-JSON response"
                            )
                        body = _read_response(
                            response,
                            limit,
                            deadline=deadline,
                            clock=self.clock,
                        )
                    decoded = strict_json_loads(body)
                    if not isinstance(decoded, dict):
                        raise _RetryableResponse(
                            "remote MinerU JSON root must be an object"
                        )
                    result = decoded if validator is None else validator(decoded)
                    if self.clock() > deadline:
                        raise _DeadlineExceeded
                    return result
                except (_CapacityFull, _PermanentFailure):
                    raise
                except _DeadlineExceeded:
                    raise MineruFailed(
                        "remote MinerU exceeded its "
                        f"{effective_timeout}s total deadline"
                    ) from None
                except _ResponseTooLarge:
                    last_error = f"remote MinerU response exceeds {limit} bytes"
                except MineruFailed as exc:
                    last_error = str(exc)
                except (
                    _RetryableResponse,
                    httpx.HTTPError,
                    RecursionError,
                    UnicodeError,
                    ValueError,
                ) as exc:
                    last_error = str(exc) or type(exc).__name__
                except Exception as exc:
                    # Retry unexpected request-boundary failures too, but never
                    # persist or log arbitrary exception text that may contain
                    # source data, paths, or secrets.
                    last_error = (
                        "remote MinerU request raised " + type(exc).__name__
                    )
                if attempt < max_attempts:
                    logger.warning(
                        "MinerU request attempt %s/%s failed; retrying in %.0fs: %s",
                        attempt,
                        max_attempts,
                        _RETRY_DELAY_SECONDS,
                        last_error,
                    )
                    remaining = deadline - self.clock()
                    if remaining <= 0:
                        raise MineruFailed(
                            f"remote MinerU exceeded its {effective_timeout}s "
                            "total deadline"
                        )
                    self.sleep(min(_RETRY_DELAY_SECONDS, remaining))
        final_message = (
            f"remote MinerU failed after {max_attempts} attempts: {last_error}"
        )
        logger.error(final_message)
        raise MineruFailed(final_message)


class UnavailableMineruBackend:
    """Intentional production state when the remote parser cannot be configured."""

    name = "unavailable"

    def __init__(self, reason: str = "MinerU is unavailable") -> None:
        self.reason = reason

    def availability(self) -> MineruAvailability:
        return MineruAvailability(False, None, None, self.reason)

    def parse(self, input_path: Path, *, method: str = "auto") -> MineruOutput:
        del input_path, method
        raise MineruUnavailable(self.reason)


class FakeMineruBackend:
    """Explicit dependency-injected test backend; never selected by environment."""

    name = "fake"

    def __init__(self, output: MineruOutput) -> None:
        self.output = output

    def availability(self) -> MineruAvailability:
        return MineruAvailability(True, self.output.version, MINERU_PROTOCOL_VERSION)

    def parse(self, input_path: Path, *, method: str = "auto") -> MineruOutput:
        del input_path, method
        return self.output


class _RetryableResponse(RuntimeError):
    pass


class _ResponseTooLarge(RuntimeError):
    pass


class _DeadlineExceeded(RuntimeError):
    pass


class _CapacityFull(RuntimeError):
    def __init__(self, wait: float) -> None:
        super().__init__("remote MinerU is at capacity")
        self.wait = wait


class _PermanentFailure(MineruFailed):
    """A response no retry can change, such as an unknown job or rejected input."""


def _retry_after(value: str | None) -> float:
    minimum, maximum, default = _JOB_CAPACITY_WAIT_SECONDS
    try:
        seconds = float(value) if value is not None else default
    except ValueError:
        seconds = default
    if seconds != seconds:  # NaN
        seconds = default
    return min(maximum, max(minimum, seconds))


def _rewind_files(
    files: Mapping[str, tuple[str, BinaryIO, str]] | None,
) -> None:
    """Start every fresh retry from byte zero of each staged upload."""

    if files is None:
        return
    for _, content, _ in files.values():
        content.seek(0)


def _read_response(
    response: httpx.Response,
    limit: int,
    *,
    deadline: float,
    clock: Callable[[], float],
) -> bytearray:
    body = bytearray()
    for chunk in response.iter_bytes():
        if clock() > deadline:
            raise _DeadlineExceeded
        if len(chunk) > limit - len(body):
            raise _ResponseTooLarge
        body.extend(chunk)
        if clock() > deadline:
            raise _DeadlineExceeded
    if not body:
        raise _RetryableResponse("remote MinerU returned an empty response")
    # ``json.loads`` accepts bytearray directly; retaining it avoids a second
    # response-sized allocation at the 64 MiB boundary.
    return body


def _validate_health(payload: dict[str, Any]) -> MineruAvailability:
    status = payload.get("status")
    version = payload.get("version")
    protocol = payload.get("protocol_version")
    if status != "healthy":
        return MineruAvailability(False, None, None, "upstream health is not healthy")
    if not _bounded_printable(version, 64):
        return MineruAvailability(False, None, None, "invalid upstream version")
    if type(protocol) is not int or protocol != MINERU_PROTOCOL_VERSION:
        return MineruAvailability(
            False,
            version,
            protocol if type(protocol) is int else None,
            "unsupported upstream protocol version",
        )
    return MineruAvailability(True, version, protocol)


def _validate_jobs_health(payload: dict[str, Any]) -> MineruAvailability:
    if payload.get("status") != "ok":
        return MineruAvailability(False, None, None, "upstream health is not ok")
    # The jobs API reports no version; each result is checked as protocol 1.
    return MineruAvailability(True, None, MINERU_PROTOCOL_VERSION)


def _validate_job_status(
    payload: dict[str, Any], task_id: str | None = None
) -> dict[str, str]:
    if not set(payload) >= _JOB_STATUS_KEYS:
        raise MineruFailed("remote MinerU job status has an unexpected shape")
    job_id = payload["task_id"]
    if (
        not isinstance(job_id, str)
        or not _JOB_ID.fullmatch(job_id)
        or task_id not in {None, job_id}
    ):
        raise MineruFailed("remote MinerU job id is invalid")
    if payload["status"] not in _JOB_STATES:
        raise MineruFailed("remote MinerU job status is invalid")
    return {"task_id": job_id, "status": payload["status"]}


def _validate_parse_response(
    payload: dict[str, Any], method: str, duration_ms: int, job_id: str | None = None
) -> MineruOutput:
    if set(payload) != _PARSE_RESPONSE_KEYS:
        raise MineruFailed("remote MinerU response has an unexpected root shape")
    task_id = payload["task_id"]
    try:
        if job_id is not None:
            # A jobs-API result must be the job that was submitted.
            if task_id != job_id:
                raise ValueError
        elif not isinstance(task_id, str) or len(task_id) != 36:
            raise ValueError
        else:
            uuid.UUID(task_id)
    except (ValueError, AttributeError) as exc:
        raise MineruFailed("remote MinerU task_id is invalid") from exc
    if (
        payload["status"] != "completed"
        or payload["backend"] != MINERU_BACKEND
        or payload["error"] is not None
    ):
        raise MineruFailed("remote MinerU did not return a completed pipeline result")
    version = payload["version"]
    if not _bounded_printable(version, 64):
        raise MineruFailed("remote MinerU version is invalid")
    if type(payload["queued_ahead"]) is not int or payload["queued_ahead"] < 0:
        raise MineruFailed("remote MinerU queued_ahead is invalid")
    for key in ("created_at", "started_at", "completed_at", "status_url", "result_url"):
        value = payload[key]
        if not _bounded_printable(value, 2_000):
            raise MineruFailed(f"remote MinerU {key} is invalid")
    names = payload["file_names"]
    results = payload["results"]
    if (
        not isinstance(names, list)
        or len(names) != 1
        or not _bounded_printable(names[0], 180)
        or not isinstance(results, dict)
        or len(results) != 1
        or names[0] not in results
    ):
        raise MineruFailed("remote MinerU must return one matching file result")
    result = results[names[0]]
    if not isinstance(result, dict) or set(result) != _RESULT_KEYS:
        raise MineruFailed("remote MinerU file result has an unexpected shape")
    markdown = result["md_content"]
    middle_raw = result["middle_json"]
    content_raw = result["content_list"]
    if not all(isinstance(item, str) for item in (markdown, middle_raw, content_raw)):
        raise MineruFailed("remote MinerU structured outputs must be JSON strings")
    try:
        middle = strict_json_loads(middle_raw)
        content = strict_json_loads(content_raw)
    except (RecursionError, ValueError) as exc:
        raise MineruFailed(
            "remote MinerU embedded structured output is invalid JSON"
        ) from exc
    if not isinstance(content, list) or not all(
        isinstance(item, dict) for item in content
    ):
        raise MineruFailed("remote MinerU content_list must be an object array")
    if not isinstance(middle, dict) or not isinstance(middle.get("pdf_info"), list):
        raise MineruFailed("remote MinerU middle_json must contain pdf_info pages")
    if middle.get("_backend") != MINERU_BACKEND:
        raise MineruFailed("remote MinerU middle_json backend is not pipeline")
    if middle.get("_version_name") != version:
        raise MineruFailed("remote MinerU result version metadata is inconsistent")
    return MineruOutput(
        version=version,
        method=method,
        duration_ms=duration_ms,
        markdown=markdown,
        content_list=content,
        middle_json=middle,
    )


def _media_type(path: Path) -> str:
    extension = path.suffix.lower()
    return {
        ".pdf": "application/pdf",
        ".docx": (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }.get(extension, "application/octet-stream")


def _bounded_printable(value: Any, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= maximum
        and all(character.isprintable() for character in value)
    )


__all__ = [
    "FakeMineruBackend",
    "MineruAvailability",
    "MineruBackend",
    "MineruOutput",
    "MineruRemoteBackend",
    "UnavailableMineruBackend",
]
