from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from compiler_service.config import MINERU_API_URL, MINERU_JOBS_URL
from compiler_service.errors import MineruFailed
from compiler_service.mineru import MineruRemoteBackend, _validate_parse_response


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class AdvancingStream(httpx.SyncByteStream):
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock

    def __iter__(self):
        self.clock.advance(0.6)
        yield b'{"status":'
        self.clock.advance(0.6)
        yield b'"completed"}'


def _parse_payload() -> dict:
    middle = {
        "_backend": "pipeline",
        "_version_name": "3.1.15",
        "pdf_info": [{"page_idx": 0, "page_size": [544, 743]}],
    }
    content = [
        {
            "type": "text",
            "text": "A measured result.",
            "bbox": [100, 100, 500, 200],
            "page_idx": 0,
        }
    ]
    return {
        "task_id": "d6c9397a-d380-4362-a3fb-63179fc5b1a0",
        "status": "completed",
        "backend": "pipeline",
        "file_names": ["sample"],
        "created_at": "2026-08-15T07:01:00Z",
        "started_at": "2026-08-15T07:01:00Z",
        "completed_at": "2026-08-15T07:01:18Z",
        "error": None,
        # The lab service currently emits HTTP links. They are untrusted metadata:
        # the adapter validates bounded strings but never follows them.
        "status_url": "http://mineru.acemap.cn/tasks/ignored",
        "result_url": "http://mineru.acemap.cn/tasks/ignored/result",
        "queued_ahead": 0,
        "version": "3.1.15",
        "results": {
            "sample": {
                "md_content": "A measured result.",
                "middle_json": json.dumps(middle),
                "content_list": json.dumps(content),
            }
        },
    }


def _json_response(payload: object, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        headers={"content-type": "application/json"},
        content=json.dumps(payload).encode(),
    )


def test_remote_parse_uses_exact_fixed_form_and_never_follows_result_urls(
    tmp_path: Path,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "POST"
        assert str(request.url) == MINERU_API_URL
        assert "authorization" not in request.headers
        body = request.read()
        required = {
            b'name="backend"\r\n\r\npipeline',
            b'name="parse_method"\r\n\r\nauto',
            b'name="formula_enable"\r\n\r\ntrue',
            b'name="table_enable"\r\n\r\ntrue',
            b'name="return_md"\r\n\r\ntrue',
            b'name="return_middle_json"\r\n\r\ntrue',
            b'name="return_content_list"\r\n\r\ntrue',
            b'name="return_model_output"\r\n\r\nfalse',
            b'name="return_images"\r\n\r\nfalse',
            b'name="image_analysis"\r\n\r\nfalse',
            b'name="files"; filename="sample.pdf"',
        }
        assert all(fragment in body for fragment in required)
        assert b'name="server_url"' not in body
        return _json_response(_parse_payload())

    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    backend = MineruRemoteBackend(transport=httpx.MockTransport(handler))

    output = backend.parse(source)

    assert len(seen) == 1
    assert output.version == "3.1.15"
    assert output.content_list[0]["text"] == "A measured result."
    assert output.middle_json["pdf_info"][0]["page_size"] == [544, 743]


def test_empty_invalid_json_and_schema_failures_retry_only_three_times(
    tmp_path: Path,
) -> None:
    bodies = [b"", b"{not-json", json.dumps({}).encode()]
    calls = 0
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        body = bodies[calls]
        calls += 1
        return httpx.Response(
            200, headers={"content-type": "application/json"}, content=body
        )

    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    backend = MineruRemoteBackend(
        transport=httpx.MockTransport(handler), sleep=sleeps.append
    )

    with pytest.raises(MineruFailed, match="failed after 3 attempts"):
        backend.parse(source)
    assert calls == 3
    assert sleeps == [3.0, 3.0]


def test_every_http_error_retries_three_times(
    caplog: pytest.LogCaptureFixture, tmp_path: Path,
) -> None:
    responses = [503, 429, 200]
    calls = 0

    def transient(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        status = responses[calls]
        calls += 1
        return _json_response(_parse_payload(), status)

    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    backend = MineruRemoteBackend(
        transport=httpx.MockTransport(transient), sleep=lambda _: None
    )
    assert backend.parse(source).version == "3.1.15"
    assert calls == 3

    rejected_calls = 0
    sleeps: list[float] = []

    def rejected(_: httpx.Request) -> httpx.Response:
        nonlocal rejected_calls
        rejected_calls += 1
        return _json_response({"detail": "bad request"}, 400)

    backend = MineruRemoteBackend(
        transport=httpx.MockTransport(rejected), sleep=sleeps.append
    )
    caplog.set_level("WARNING", logger="compiler_service.mineru")
    caplog.clear()
    with pytest.raises(
        MineruFailed,
        match="failed after 3 attempts: remote MinerU returned HTTP 400",
    ):
        backend.parse(source)
    assert rejected_calls == 3
    assert sleeps == [3.0, 3.0]
    messages = [record.getMessage() for record in caplog.records]
    assert sum(
        "retrying in 3s" in message and "HTTP 400" in message
        for message in messages
    ) == 2
    assert any(
        "failed after 3 attempts" in message and "HTTP 400" in message
        for message in messages
    )


def test_unexpected_request_errors_retry_without_leaking_exception_text(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    marker = "SECRET-REQUEST-DETAIL"
    calls = 0
    sleeps: list[float] = []

    def broken(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise RuntimeError(marker)

    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    backend = MineruRemoteBackend(
        transport=httpx.MockTransport(broken), sleep=sleeps.append
    )
    caplog.set_level("WARNING", logger="compiler_service.mineru")

    with pytest.raises(
        MineruFailed,
        match="failed after 3 attempts: remote MinerU request raised RuntimeError",
    ) as error:
        backend.parse(source)

    assert calls == 3
    assert sleeps == [3.0, 3.0]
    assert marker not in str(error.value)
    assert marker not in caplog.text


def test_remote_retry_rewinds_the_staged_file_stream(tmp_path: Path) -> None:
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.read())
        status = 503 if len(bodies) == 1 else 200
        return _json_response(_parse_payload(), status)

    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-1.7\nretry-marker")
    backend = MineruRemoteBackend(
        transport=httpx.MockTransport(handler), sleep=lambda _: None
    )

    assert backend.parse(source).version == "3.1.15"
    assert len(bodies) == 2
    assert all(b"%PDF-1.7\nretry-marker" in body for body in bodies)


def test_remote_parse_streams_file_without_path_read_bytes(
    monkeypatch, tmp_path: Path
) -> None:
    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-1.7\nstream-marker")

    def forbidden_read_bytes(_: Path) -> bytes:
        raise AssertionError("MinerU upload must stream the staged file")

    def handler(request: httpx.Request) -> httpx.Response:
        assert b"stream-marker" in request.read()
        return _json_response(_parse_payload())

    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)
    backend = MineruRemoteBackend(
        transport=httpx.MockTransport(handler)
    )

    assert backend.parse(source).version == "3.1.15"


def test_remote_response_limit_is_enforced_before_json_decode(tmp_path: Path) -> None:
    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    sleeps: list[float] = []
    calls = 0

    def oversized(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _json_response(_parse_payload())

    backend = MineruRemoteBackend(
        max_response_bytes=8,
        transport=httpx.MockTransport(oversized),
        sleep=sleeps.append,
    )
    with pytest.raises(MineruFailed, match="failed after 3 attempts.*exceeds 8 bytes"):
        backend.parse(source)
    assert calls == 3
    assert sleeps == [3.0, 3.0]


def test_retry_attempts_share_one_absolute_deadline(tmp_path: Path) -> None:
    clock = FakeClock()
    calls = 0
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        clock.advance(0.8)
        return _json_response({}, 503)

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.advance(seconds)

    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    backend = MineruRemoteBackend(
        timeout_seconds=2,
        transport=httpx.MockTransport(handler),
        sleep=sleep,
        clock=clock,
    )

    with pytest.raises(MineruFailed, match="2s total deadline"):
        backend.parse(source)
    assert calls == 1
    assert sleeps == [1.2]


def test_streaming_response_checks_absolute_deadline_per_chunk(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    backend = MineruRemoteBackend(
        timeout_seconds=1,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                headers={"content-type": "application/json"},
                stream=AdvancingStream(clock),
            )
        ),
        clock=clock,
    )

    with pytest.raises(MineruFailed, match="1s total deadline"):
        backend.parse(source)


@pytest.mark.parametrize(
    "body",
    [
        b'{"queued_ahead":NaN}',
        b'{"queued_ahead":Infinity}',
        b'{"queued_ahead":-Infinity}',
        b'{"queued_ahead":1e999}',
    ],
)
def test_remote_outer_json_rejects_non_standard_numbers(
    tmp_path: Path, body: bytes
) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=body,
        )

    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    backend = MineruRemoteBackend(
        transport=httpx.MockTransport(handler), sleep=lambda _: None
    )
    with pytest.raises(MineruFailed, match="failed after 3 attempts"):
        backend.parse(source)
    assert calls == 3


def test_remote_embedded_json_rejects_non_standard_numbers(tmp_path: Path) -> None:
    payload = _parse_payload()
    payload["results"]["sample"]["content_list"] = (
        '[{"type":"text","page_idx":NaN}]'
    )
    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    backend = MineruRemoteBackend(
        transport=httpx.MockTransport(lambda _: _json_response(payload)),
        sleep=lambda _: None,
    )
    with pytest.raises(MineruFailed, match="failed after 3 attempts"):
        backend.parse(source)


def test_remote_excessively_nested_json_retries_without_recursion_failure(
    tmp_path: Path,
) -> None:
    calls = 0
    body = ("[" * 1_100 + "0" + "]" * 1_100).encode()

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=body,
        )

    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    backend = MineruRemoteBackend(
        transport=httpx.MockTransport(handler), sleep=lambda _: None
    )

    with pytest.raises(MineruFailed, match="failed after 3 attempts"):
        backend.parse(source)
    assert calls == 3


@pytest.mark.parametrize(
    "url",
    [
        "http://mineru.acemap.cn/file_parse",
        "https://example.com/file_parse",
        "https://mineru.acemap.cn/other",
        "https://mineru.acemap.cn/file_parse?target=http://127.0.0.1",
        "https://user@mineru.acemap.cn/file_parse",
    ],
)
def test_remote_url_is_strictly_allowlisted(url: str) -> None:
    with pytest.raises(ValueError, match="allowlisted HTTPS"):
        MineruRemoteBackend(url=url)


def test_health_requires_healthy_protocol_one() -> None:
    calls = 0

    def healthy(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert 0 < request.extensions["timeout"]["read"] <= 5
        return _json_response(
            {"status": "healthy", "version": "3.1.15", "protocol_version": 1}
        )

    backend = MineruRemoteBackend(transport=httpx.MockTransport(healthy))
    assert backend.availability().available is True
    assert backend.availability().available is True
    assert calls == 1

    mismatch = httpx.MockTransport(
        lambda request: _json_response(
            {"status": "healthy", "version": "3.1.15", "protocol_version": 2}
        )
    )
    unavailable = MineruRemoteBackend(transport=mismatch).availability()
    assert unavailable.available is False
    assert unavailable.protocol_version == 2
    assert unavailable.reason == "unsupported upstream protocol version"

    boolean = httpx.MockTransport(
        lambda request: _json_response(
            {"status": "healthy", "version": "3.1.15", "protocol_version": True}
        )
    )
    assert MineruRemoteBackend(transport=boolean).availability().available is False


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("task_id", 1, "task_id"),
        ("version", "3.1.15\n", "version"),
        ("queued_ahead", True, "queued_ahead"),
        ("file_names", ["x" * 181], "one matching file result"),
    ],
)
def test_parse_response_rejects_ambiguous_or_unbounded_scalars(
    field: str, value: object, message: str
) -> None:
    payload = _parse_payload()
    payload[field] = value
    with pytest.raises(MineruFailed, match=message):
        _validate_parse_response(payload, "auto", 1)


@pytest.mark.parametrize("port", [18006])
def test_tunnel_health_and_parse_authentication(tmp_path, port):
    url = f"http://127.0.0.1:{port}/file_parse"
    seen = []

    def handler(request):
        seen.append(str(request.url))
        assert request.headers["authorization"] == "Bearer test-token"
        if request.method == "GET":
            assert str(request.url) == f"http://127.0.0.1:{port}/health"
            return _json_response(
                {
                    "status": "healthy",
                    "version": "3.1.15",
                    "protocol_version": 1,
                    "workers_ready": 4,
                    "workers_total": 4,
                }
            )
        assert str(request.url) == url
        return _json_response(_parse_payload())

    backend = MineruRemoteBackend(
        url=url, api_token="test-token", transport=httpx.MockTransport(handler)
    )
    assert backend.availability().available
    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    assert backend.parse(source).version == "3.1.15"
    assert len(seen) == 2
    with pytest.raises(ValueError, match="outside"):
        backend._json_request("GET", "https://example.com", limit=1024)
    assert len(seen) == 2


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:18006/file_parse",
        "http://127.0.0.1:18008/file_parse",
        "http://127.0.0.1:18007/file_parse",
        "http://127.0.0.1:18006/file_parse?x=1",
        "http://127.0.0.1:18006/file_parse#x",
        "http://user@127.0.0.1:18006/file_parse",
        "http://127.0.0.1:18006/other",
    ],
)
def test_tunnel_allowlist_requires_exact_endpoint(url):
    with pytest.raises(ValueError):
        MineruRemoteBackend(url=url, api_token="test-token")


def test_mineru_credentials_reject_lab_endpoint_and_header_controls():
    with pytest.raises(ValueError, match="approved loopback"):
        MineruRemoteBackend(api_token="test-token")
    with pytest.raises(ValueError, match="ASCII"):
        MineruRemoteBackend(
            url="http://127.0.0.1:18006/file_parse", api_token="bad\r\ntoken"
        )


_JOB_ID = "8499308c9f614fc1b3e4f898f8799fbb"


def _job_status(status: str) -> dict:
    return {
        "task_id": _JOB_ID,
        "status": status,
        "file_names": ["sample"],
        "created_at": "2026-10-20T00:00:00+00:00",
        "started_at": None,
        "completed_at": None,
        "expires_at": None,
        "error": None,
        # Server links are untrusted metadata; the client builds its own URLs.
        "status_url": "https://example.com/jobs/elsewhere",
        "result_url": "https://example.com/jobs/elsewhere/result",
    }


def _job_result() -> dict:
    payload = _parse_payload()
    payload["task_id"] = _JOB_ID
    return payload


def _jobs_backend(handler, sleeps: list[float], clock: FakeClock | None = None):
    clock = clock or FakeClock()

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.advance(seconds)

    return MineruRemoteBackend(
        url=MINERU_JOBS_URL,
        transport=httpx.MockTransport(handler),
        sleep=sleep,
        clock=clock,
    )


@pytest.mark.parametrize("task_id", [_JOB_ID, f"n1-{_JOB_ID}"])
def test_jobs_api_submits_polls_and_fetches_only_the_submitted_job(
    tmp_path: Path, task_id: str,
) -> None:
    seen: list[str] = []
    states = iter(["running", "completed"])

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url}")
        assert "authorization" not in request.headers
        if request.method == "POST":
            body = request.read()
            assert b'name="return_images"\r\n\r\nfalse' in body
            assert b'name="files"; filename="sample.pdf"' in body
            # Parsing options are fixed by the jobs service, not sent.
            assert b'name="backend"' not in body
            return _json_response({**_job_status("queued"), "task_id": task_id}, 202)
        if str(request.url).endswith("/result"):
            return _json_response({**_job_result(), "task_id": task_id})
        return _json_response({**_job_status(next(states)), "task_id": task_id})

    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    sleeps: list[float] = []
    output = _jobs_backend(handler, sleeps).parse(source)

    jobs_url = (
        "https://mineru-n1.iiot-shxt.xyz/jobs"
        if task_id.startswith("n1-")
        else MINERU_JOBS_URL
    )
    base = f"{jobs_url}/{task_id}"
    assert seen == [
        f"POST {MINERU_JOBS_URL}",
        f"GET {base}",
        f"GET {base}",
        f"GET {base}/result",
    ]
    assert sleeps == [3.0, 3.0]
    assert output.version == "3.1.15"
    assert output.content_list[0]["text"] == "A measured result."


def test_jobs_api_resubmits_after_its_capacity_wait(tmp_path: Path) -> None:
    posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posts
        if request.method == "POST":
            posts += 1
            if posts == 1:
                return httpx.Response(429, headers={"retry-after": "7"})
            return _json_response(_job_status("completed"), 202)
        return _json_response(_job_result())

    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    sleeps: list[float] = []
    assert _jobs_backend(handler, sleeps).parse(source).version == "3.1.15"
    assert posts == 2
    assert sleeps == [7.0]


@pytest.mark.parametrize(
    ("failure", "message", "requests"),
    [
        ("rejected input", "HTTP 422", 1),
        ("unknown job", "HTTP 404", 2),
        ("failed job", "job failed", 2),
        ("other job", "task_id is invalid", 3),
    ],
)
def test_jobs_api_failures_end_the_parse_without_blind_retries(
    tmp_path: Path, failure: str, message: str, requests: int
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        if request.method == "POST":
            if failure == "rejected input":
                return _json_response({"detail": "invalid"}, 422)
            return _json_response(_job_status("queued"), 202)
        if str(request.url).endswith("/result"):
            payload = _job_result()
            payload["task_id"] = "0" * 32
            return _json_response(payload)
        if failure == "unknown job":
            return _json_response({"detail": "Job not found or expired"}, 404)
        return _json_response(
            _job_status("failed" if failure == "failed job" else "completed")
        )

    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    with pytest.raises(MineruFailed, match=message):
        _jobs_backend(handler, []).parse(source)
    assert len(seen) == requests


def test_jobs_api_does_not_resubmit_an_ambiguous_upload_failure(tmp_path):
    posts = []

    def handler(request):
        posts.append(request.method)
        raise httpx.ReadError("response lost after upload")

    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-1.7\n")
    with pytest.raises(MineruFailed):
        _jobs_backend(handler, []).parse(source)
    assert posts == ["POST"]


def test_jobs_api_health_and_request_boundary() -> None:
    def healthy(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://parse.iiot-shxt.xyz/health"
        return _json_response({"status": "ok"})

    backend = _jobs_backend(healthy, [])
    availability = backend.availability()
    assert availability.available is True
    assert availability.protocol_version == 1
    down = _jobs_backend(lambda _: _json_response({"status": "down"}), [])
    assert down.availability().available is False
    for url in (
        f"{MINERU_JOBS_URL}/{_JOB_ID}/result/result",
        f"{MINERU_JOBS_URL}/{_JOB_ID}/",
        f"{MINERU_JOBS_URL}/not-a-job-id",
        "https://parse.iiot-shxt.xyz/other",
        f"https://mineru-n2.iiot-shxt.xyz/jobs/n1-{_JOB_ID}",
        f"https://mineru-n1.iiot-shxt.xyz.evil.test/jobs/n1-{_JOB_ID}",
        f"http://mineru-n1.iiot-shxt.xyz/jobs/n1-{_JOB_ID}",
        f"https://mineru-n1.iiot-shxt.xyz/jobs/n1-{_JOB_ID}?redirect=elsewhere",
    ):
        with pytest.raises(ValueError, match="outside"):
            backend._json_request("GET", url, limit=1024)
    with pytest.raises(ValueError, match="approved loopback"):
        MineruRemoteBackend(url=MINERU_JOBS_URL, api_token="test-token")
