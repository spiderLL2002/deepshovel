from __future__ import annotations

import asyncio
import hashlib
import io
import json
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from compiler_service import __version__
from compiler_service.api import _validate_signature, create_app
from compiler_service.citation_reception import (
    CitationExtractionEnvelope,
    CitationPairRequest,
    CitationReceptionEnvelope,
)
from compiler_service.citation_source import (
    CitationSourceProjection,
    build_citation_source,
)
from compiler_service.config import (
    DEFAULT_GPT_CONCURRENCY,
    DEFAULT_RETRIEVAL_CONCURRENCY,
    MAX_GPT_CONCURRENCY,
    MAX_MINERU_TIMEOUT_SECONDS,
    MAX_RETRIEVAL_CONCURRENCY,
    MINERU_PROTOCOL_VERSION,
    Settings,
)
from compiler_service.job_store import JobStore
from compiler_service.mineru import (
    FakeMineruBackend,
    MineruAvailability,
)
from compiler_service.normalize import normalize_mineru


def test_bulk_worker_slot_survives_await_cancellation_across_executors() -> None:
    from compiler_service.api import _run_bulk_blocking

    first_entered = threading.Event()
    first_release = threading.Event()
    first_finished = threading.Event()
    second_waiting = threading.Event()
    second_entered = threading.Event()

    class ObservedSlots(threading.BoundedSemaphore):
        def __enter__(self):
            if first_entered.is_set():
                second_waiting.set()
            return super().__enter__()

    app = SimpleNamespace(
        state=SimpleNamespace(bulk_model_worker_slots=ObservedSlots(1))
    )

    def first_worker() -> str:
        first_entered.set()
        try:
            assert first_release.wait(timeout=3)
            return "first"
        finally:
            first_finished.set()

    def second_worker() -> str:
        second_entered.set()
        assert first_finished.is_set()
        return "second"

    async def exercise() -> None:
        with (
            ThreadPoolExecutor(max_workers=1) as first_pool,
            ThreadPoolExecutor(max_workers=2) as second_pool,
        ):
            first = asyncio.create_task(
                _run_bulk_blocking(app, first_pool, first_worker)
            )
            second = None
            try:
                assert await asyncio.to_thread(first_entered.wait, 1)
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
                assert not first_finished.is_set()

                second = asyncio.create_task(
                    _run_bulk_blocking(app, second_pool, second_worker)
                )
                assert await asyncio.to_thread(second_waiting.wait, 1)
                assert not await asyncio.to_thread(second_entered.wait, 0.1)

                # Interactive work bypasses the bulk helper, even while a bulk
                # worker in this same executor waits for the shared slot.
                interactive = asyncio.get_running_loop().run_in_executor(
                    second_pool, lambda: "interactive"
                )
                assert await asyncio.wait_for(interactive, timeout=1) == "interactive"
                assert not second_entered.is_set()

                first_release.set()
                assert await asyncio.wait_for(second, timeout=1) == "second"
            finally:
                first_release.set()
                first.cancel()
                if second is not None:
                    await asyncio.gather(second, return_exceptions=True)

    asyncio.run(exercise())


class StaticCompiler:
    def __init__(self, envelope: Any) -> None:
        self.envelope = envelope

    def _outcome(self, document: Any) -> SimpleNamespace:
        return SimpleNamespace(
            envelope=self.envelope,
            citation_source=build_citation_source(document),
        )

    def compile(self, document: Any, **kwargs: Any) -> Any:
        callback = kwargs.get("on_validating")
        if callback:
            callback()
        return self._outcome(document)


class StaticCitationAnalyzer:
    def __init__(self, result: CitationReceptionEnvelope) -> None:
        self.result = result
        self.received: CitationPairRequest | None = None

    def analyze(self, payload: CitationPairRequest) -> CitationReceptionEnvelope:
        self.received = payload
        return self.result


class GatedCompiler(StaticCompiler):
    def __init__(self, envelope: Any) -> None:
        super().__init__(envelope)
        self.extracting = threading.Event()
        self.allow_validating = threading.Event()
        self.validating = threading.Event()
        self.allow_completion = threading.Event()

    def compile(self, document: Any, **kwargs: Any) -> Any:
        self.extracting.set()
        assert self.allow_validating.wait(timeout=5)
        kwargs["on_validating"]()
        self.validating.set()
        assert self.allow_completion.wait(timeout=5)
        return self._outcome(document)


class CancellationAwareCompiler(StaticCompiler):
    def __init__(self, envelope: Any) -> None:
        super().__init__(envelope)
        self.started = threading.Event()
        self.check_now = threading.Event()
        self.checked = threading.Event()

    def compile(self, document: Any, **kwargs: Any) -> Any:
        check_cancelled = kwargs["check_cancelled"]
        self.started.set()
        assert self.check_now.wait(timeout=5)
        try:
            check_cancelled()
        finally:
            self.checked.set()
        return self._outcome(document)


class GatedMineru(FakeMineruBackend):
    def __init__(self, output: Any) -> None:
        super().__init__(output)
        self.parsing = threading.Event()
        self.allow_result = threading.Event()

    def parse(self, *args: Any, **kwargs: Any) -> Any:
        self.parsing.set()
        assert self.allow_result.wait(timeout=5)
        return super().parse(*args, **kwargs)


class ExplodingMineru(FakeMineruBackend):
    def __init__(self, output: Any, marker: str) -> None:
        super().__init__(output)
        self.marker = marker

    def parse(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise RuntimeError(self.marker)


class SequencedMineru(FakeMineruBackend):
    """A gated parser that records whether more than one parse overlaps."""

    def __init__(self, output: Any, count: int) -> None:
        super().__init__(output)
        self.started = [threading.Event() for _ in range(count)]
        self.release = [threading.Event() for _ in range(count)]
        self.finished = [threading.Event() for _ in range(count)]
        self._lock = threading.Lock()
        self._calls = 0
        self._active = 0
        self.max_active = 0

    def parse(self, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            index = self._calls
            self._calls += 1
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        self.started[index].set()
        try:
            assert self.release[index].wait(timeout=5)
            return super().parse(*args, **kwargs)
        finally:
            with self._lock:
                self._active -= 1
            self.finished[index].set()


class ConcurrentCompiler(StaticCompiler):
    """A gated compiler that measures cross-document Model capacity."""

    def __init__(self, envelope: Any, count: int) -> None:
        super().__init__(envelope)
        self.started = [threading.Event() for _ in range(count)]
        self.release = [threading.Event() for _ in range(count)]
        self._lock = threading.Lock()
        self._calls = 0
        self._active = 0
        self.max_active = 0

    def compile(self, document: Any, **kwargs: Any) -> Any:
        with self._lock:
            index = self._calls
            self._calls += 1
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        self.started[index].set()
        try:
            assert self.release[index].wait(timeout=5)
            kwargs["on_validating"]()
            return self._outcome(document)
        finally:
            with self._lock:
                self._active -= 1


class VersionBackend:
    name = "version-test"

    def availability(self) -> MineruAvailability:
        return MineruAvailability(True, "3.1.15", 999)


def _settings(
    tmp_path,
    *,
    secret: str | None = None,
    gpt_concurrency: int = DEFAULT_GPT_CONCURRENCY,
    mineru_concurrency: int = 1,
) -> Settings:
    return Settings(
        data_dir=tmp_path / "compiler-data",
        shared_secret=secret,
        gpt_concurrency=gpt_concurrency,
        mineru_concurrency=mineru_concurrency,
    )


def _upload(artifact, content: bytes, *, digest: str | None = None) -> dict[str, Any]:
    return {
        "files": {
            "file": (artifact.file_name, content, artifact.media_type),
        },
        "data": {
            "paper_id": artifact.paper_id,
            "artifact_id": artifact.artifact_id,
            "version_id": artifact.version_id,
            "artifact_sha256": digest or hashlib.sha256(content).hexdigest(),
            "media_type": artifact.media_type,
            "locator": artifact.locator,
        },
    }


def _wait_status(client: TestClient, job_id: str, expected: str) -> dict[str, Any]:
    for _ in range(100):
        response = client.get(f"/v1/compile-jobs/{job_id}")
        assert response.status_code == 200
        body = response.json()
        if body["job"]["status"] == expected:
            return body
        time.sleep(0.01)
    pytest.fail(f"job did not reach {expected}")


def test_health_is_public_but_v1_requires_bearer_only(
    tmp_path, artifact, mineru_output, import_envelope
) -> None:
    secret = "shared-test-secret-0123456789abcdef"
    app = create_app(
        settings=_settings(tmp_path, secret=secret),
        mineru_backend=FakeMineruBackend(mineru_output),
        evidence_compiler=StaticCompiler(import_envelope),
    )
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        # A source checkout carries no installer stamp.
        assert health.json()["release"] == f"{__version__}+development"
        assert health.json()["gpt"] == {
            "configured": True,
            "model": "gpt-6-luna",
            "concurrency": DEFAULT_GPT_CONCURRENCY,
        }
        unauthorized = client.post("/v1/compile", json={})
        assert unauthorized.status_code == 401
        assert unauthorized.headers["www-authenticate"] == "Bearer"
        legacy_header = client.post(
            "/v1/compile",
            json={},
            headers={"x-compiler-token": secret},
        )
        assert legacy_header.status_code == 401
        authorized = client.post(
            "/v1/compile",
            json={},
            headers={"Authorization": f"Bearer {secret}"},
        )
        assert authorized.status_code == 400


@pytest.mark.parametrize("dense_ready", [True, False])
def test_local_readiness_never_waits_for_remote_parser(
    tmp_path, import_envelope, dense_ready
) -> None:
    class RemoteParserMustNotBeCalled:
        def availability(self):
            raise AssertionError("readiness must not contact the remote parser")

    class LocalRetrieval:
        model_readiness = {"dense": dense_ready, "vector_store": True}

        def warm_models(self):
            pass

    app = create_app(
        settings=Settings(
            data_dir=tmp_path,
            environment="production",
            shared_secret="shared-test-secret-0123456789abcdef",
        ),
        mineru_backend=RemoteParserMustNotBeCalled(),
        evidence_compiler=StaticCompiler(import_envelope),
        retrieval_service=LocalRetrieval(),
    )
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == (200 if dense_ready else 503)
    assert response.json() == {
        "status": "ok" if dense_ready else "degraded",
        "service": "deepshovel-compiler",
        "retrieval": {"status": "ready" if dense_ready else "cold"},
    }


def test_local_readiness_rejects_unconfigured_compiler(tmp_path) -> None:
    app = create_app(settings=Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["retrieval"]["status"] == "uninitialized"


def test_production_configuration_requires_shared_secret(monkeypatch) -> None:
    monkeypatch.setenv("COMPILER_ENV", "production")
    monkeypatch.delenv("COMPILER_SHARED_SECRET", raising=False)
    with pytest.raises(ValueError, match="COMPILER_SHARED_SECRET is required"):
        Settings.from_environment()


def test_configured_shared_secret_has_a_minimum_strength(tmp_path) -> None:
    with pytest.raises(ValueError, match="32-512"):
        Settings(data_dir=tmp_path, shared_secret="too-short")
    with pytest.raises(ValueError, match="non-whitespace ASCII"):
        Settings(data_dir=tmp_path, shared_secret="x" * 31 + "\n")


def test_mineru_timeout_is_bounded_in_direct_and_environment_settings(
    monkeypatch, tmp_path
) -> None:
    with pytest.raises(ValueError, match="must be an integer from 1"):
        Settings(data_dir=tmp_path, mineru_timeout_seconds=True)
    with pytest.raises(ValueError, match="must be an integer from 1"):
        Settings(
            data_dir=tmp_path,
            mineru_timeout_seconds=MAX_MINERU_TIMEOUT_SECONDS + 1,
        )
    monkeypatch.setenv(
        "COMPILER_MINERU_TIMEOUT_SECONDS",
        str(MAX_MINERU_TIMEOUT_SECONDS + 1),
    )
    with pytest.raises(ValueError, match="must not exceed"):
        Settings.from_environment()


def test_gpt_concurrency_is_bounded_and_loaded_from_environment(
    monkeypatch, tmp_path
) -> None:
    for invalid in (True, 0, MAX_GPT_CONCURRENCY + 1):
        with pytest.raises(ValueError, match="COMPILER_GPT_CONCURRENCY"):
            Settings(data_dir=tmp_path, gpt_concurrency=invalid)
    monkeypatch.setenv("COMPILER_GPT_CONCURRENCY", "17")
    assert Settings.from_environment().gpt_concurrency == 17


def test_claim_processing_defaults_to_at_least_sixty_four_parallel_workers(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv("COMPILER_GPT_CONCURRENCY", raising=False)
    monkeypatch.delenv("RETRIEVAL_CONCURRENCY", raising=False)
    settings = Settings.from_environment()
    assert DEFAULT_GPT_CONCURRENCY >= 64
    assert DEFAULT_RETRIEVAL_CONCURRENCY >= 64
    assert settings.gpt_concurrency >= 64
    assert settings.retrieval_concurrency >= 64
    assert MAX_RETRIEVAL_CONCURRENCY >= 64
    with pytest.raises(ValueError, match="RETRIEVAL_CONCURRENCY"):
        Settings(data_dir=tmp_path, retrieval_concurrency=MAX_RETRIEVAL_CONCURRENCY + 1)


def test_retrieval_remote_models_are_on_by_default_and_can_be_disabled(
    monkeypatch,
) -> None:
    assert Settings.from_environment().retrieval_remote_models is True
    monkeypatch.setenv("RETRIEVAL_REMOTE_MODELS", "false")
    assert Settings.from_environment().retrieval_remote_models is False
    monkeypatch.setenv("RETRIEVAL_REMOTE_MODELS", "sometimes")
    with pytest.raises(ValueError, match="RETRIEVAL_REMOTE_MODELS"):
        Settings.from_environment()


def test_qdrant_credentials_require_a_secure_remote_origin(tmp_path) -> None:
    with pytest.raises(ValueError, match="API_KEY requires"):
        Settings(data_dir=tmp_path, retrieval_qdrant_api_key="secret")
    with pytest.raises(ValueError, match="HTTPS origin"):
        Settings(
            data_dir=tmp_path,
            retrieval_qdrant_url="http://qdrant.example.internal:6333",
            retrieval_qdrant_api_key="secret",
        )
    with pytest.raises(ValueError, match="HTTPS origin"):
        Settings(
            data_dir=tmp_path,
            retrieval_qdrant_url="https://qdrant.example.internal/path",
        )
    assert (
        Settings(
            data_dir=tmp_path,
            retrieval_qdrant_url="http://127.0.0.1:6333",
        ).retrieval_qdrant_url
        == "http://127.0.0.1:6333"
    )
    assert (
        Settings(
            data_dir=tmp_path,
            retrieval_qdrant_url="http://[::1]:6333",
        ).retrieval_qdrant_url
        == "http://[::1]:6333"
    )


def test_health_rejects_unsupported_remote_protocol_version(
    tmp_path, import_envelope
) -> None:
    app = create_app(
        settings=_settings(tmp_path),
        mineru_backend=VersionBackend(),
        evidence_compiler=StaticCompiler(import_envelope),
    )
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body["status"] == "degraded"
    assert body["mineru"] == {
        "status": "unavailable",
        "backend": "pipeline",
        "transport": "remote",
        "version": "3.1.15",
        "protocol_version": 999,
        "expected_protocol_version": MINERU_PROTOCOL_VERSION,
    }


def test_upload_rejects_hash_mismatch_and_spoofed_pdf(
    tmp_path, artifact, mineru_output, import_envelope
) -> None:
    app = create_app(
        settings=_settings(tmp_path),
        mineru_backend=FakeMineruBackend(mineru_output),
        evidence_compiler=StaticCompiler(import_envelope),
    )
    with TestClient(app) as client:
        mismatch = client.post(
            "/v1/parse", **_upload(artifact, b"%PDF-1.7\n", digest="0" * 64)
        )
        assert mismatch.status_code == 400
        assert mismatch.json()["error"]["code"] == "ARTIFACT_SHA256_MISMATCH"

        spoofed = client.post("/v1/parse", **_upload(artifact, b"this is not a PDF"))
        assert spoofed.status_code == 415
        assert spoofed.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_upload_size_limit_is_enforced_before_parsing(
    monkeypatch, tmp_path, artifact, mineru_output, import_envelope
) -> None:
    monkeypatch.setattr("compiler_service.api.MAX_UPLOAD_BYTES", 8)
    app = create_app(
        settings=_settings(tmp_path),
        mineru_backend=FakeMineruBackend(mineru_output),
        evidence_compiler=StaticCompiler(import_envelope),
    )
    with TestClient(app) as client:
        response = client.post("/v1/parse", **_upload(artifact, b"%PDF-1.70"))
    assert response.status_code == 400
    assert "limited to 8 bytes" in response.json()["error"]["message"]


@pytest.mark.parametrize(
    ("media_type", "valid"),
    [
        ("application/pdf", b"%PDF-1.7\n"),
        ("image/png", b"\x89PNG\r\n\x1a\nrest"),
        ("image/jpeg", b"\xff\xd8\xffrest\xff\xd9"),
        ("image/tiff", b"II*\x00rest"),
    ],
)
def test_binary_signature_allowlist(media_type: str, valid: bytes) -> None:
    _validate_signature(valid, media_type)
    with pytest.raises(Exception, match="signature"):
        _validate_signature(b"spoofed bytes", media_type)


@pytest.mark.parametrize(
    ("media_type", "part"),
    [
        (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "word/document.xml",
        ),
        (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "xl/workbook.xml",
        ),
    ],
)
def test_ooxml_signature_requires_expected_package_part(
    media_type: str, part: str
) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(part, "<root/>")
    _validate_signature(buffer.getvalue(), media_type)
    with pytest.raises(Exception, match="signature"):
        _validate_signature(b"PK\x03\x04not-a-zip", media_type)


def test_job_api_preserves_null_shape_and_runs_all_stages(
    tmp_path, artifact, mineru_output, import_envelope
) -> None:
    mineru = GatedMineru(mineru_output)
    compiler = GatedCompiler(import_envelope)
    app = create_app(
        settings=_settings(tmp_path),
        mineru_backend=mineru,
        evidence_compiler=compiler,
    )
    with TestClient(app) as client:
        created = client.post("/v1/compile-jobs", **_upload(artifact, b"%PDF-1.4\n"))
        assert created.status_code == 202
        body = created.json()
        assert body["job"]["status"] == "queued"
        assert body["job"]["error"] is None
        assert body["job"]["stage_timings_ms"] == {
            "queued": None,
            "parsing": None,
            "extracting": None,
            "validating": None,
            "total": None,
        }
        job_id = body["job"]["id"]

        assert mineru.parsing.wait(timeout=5)
        parsing = _wait_status(client, job_id, "parsing")
        assert set(parsing["job"]["stage_timings_ms"]) == {
            "queued",
            "parsing",
            "extracting",
            "validating",
            "total",
        }
        premature = client.get(f"/v1/compile-jobs/{job_id}/result")
        assert premature.status_code == 409
        mineru.allow_result.set()
        assert compiler.extracting.wait(timeout=5)
        _wait_status(client, job_id, "extracting")
        compiler.allow_validating.set()
        assert compiler.validating.wait(timeout=5)
        _wait_status(client, job_id, "validating")
        compiler.allow_completion.set()
        completed = _wait_status(client, job_id, "completed")

        timings = completed["job"]["stage_timings_ms"]
        assert all(isinstance(timings[key], int) for key in timings)
        result = client.get(f"/v1/compile-jobs/{job_id}/result")
        assert result.status_code == 200
        assert result.json()["graph"] == import_envelope.graph
        assert "verification" not in result.json()
        deleted = client.delete(f"/v1/compile-jobs/{job_id}")
        assert deleted.status_code == 204
        assert deleted.content == b""
        assert client.get(f"/v1/compile-jobs/{job_id}").status_code == 404


def test_active_job_delete_cancels_and_discards_work(
    tmp_path, artifact, mineru_output, import_envelope
) -> None:
    mineru = GatedMineru(mineru_output)
    app = create_app(
        settings=_settings(tmp_path),
        mineru_backend=mineru,
        evidence_compiler=StaticCompiler(import_envelope),
    )
    with TestClient(app) as client:
        created = client.post("/v1/compile-jobs", **_upload(artifact, b"%PDF-1.4\n"))
        job_id = created.json()["job"]["id"]
        assert mineru.parsing.wait(timeout=5)

        cancelled = client.delete(f"/v1/compile-jobs/{job_id}")
        assert cancelled.status_code == 204
        failed = client.get(f"/v1/compile-jobs/{job_id}").json()["job"]
        assert failed["status"] == "failed"
        assert failed["error"]["code"] == "JOB_CANCELLED"

        mineru.allow_result.set()
        time.sleep(0.05)
        after_worker = client.get(f"/v1/compile-jobs/{job_id}").json()["job"]
        assert after_worker["status"] == "failed"
        assert client.delete(f"/v1/compile-jobs/{job_id}").status_code == 204
        assert client.get(f"/v1/compile-jobs/{job_id}").status_code == 404


def test_active_model_job_observes_persisted_cancellation_checkpoint(
    tmp_path, artifact, mineru_output, import_envelope
) -> None:
    compiler = CancellationAwareCompiler(import_envelope)
    app = create_app(
        settings=_settings(tmp_path),
        mineru_backend=FakeMineruBackend(mineru_output),
        evidence_compiler=compiler,
    )
    with TestClient(app) as client:
        created = client.post("/v1/compile-jobs", **_upload(artifact, b"%PDF-1.4\n"))
        job_id = created.json()["job"]["id"]
        assert compiler.started.wait(timeout=5)
        _wait_status(client, job_id, "extracting")

        assert client.delete(f"/v1/compile-jobs/{job_id}").status_code == 204
        compiler.check_now.set()
        assert compiler.checked.wait(timeout=5)
        failed = _wait_status(client, job_id, "failed")["job"]

        assert failed["error"]["code"] == "JOB_CANCELLED"


def test_cancelled_queued_job_never_occupies_the_parser(
    tmp_path, artifact, mineru_output, import_envelope
) -> None:
    mineru = SequencedMineru(mineru_output, 2)
    app = create_app(
        settings=_settings(tmp_path),
        mineru_backend=mineru,
        evidence_compiler=StaticCompiler(import_envelope),
    )
    with TestClient(app) as client:
        first = client.post(
            "/v1/compile-jobs", **_upload(artifact, b"%PDF-1.4\n")
        ).json()["job"]["id"]
        assert mineru.started[0].wait(timeout=5)
        second = client.post(
            "/v1/compile-jobs", **_upload(artifact, b"%PDF-1.4\n")
        ).json()["job"]["id"]

        assert client.delete(f"/v1/compile-jobs/{second}").status_code == 204
        assert _wait_status(client, second, "failed")["job"]["error"]["code"] == (
            "JOB_CANCELLED"
        )
        mineru.release[0].set()
        _wait_status(client, first, "completed")
        assert not mineru.started[1].wait(timeout=0.1)


def test_deleted_validating_job_is_not_recreated_when_model_returns(
    tmp_path, artifact, mineru_output, import_envelope
) -> None:
    compiler = GatedCompiler(import_envelope)
    app = create_app(
        settings=_settings(tmp_path),
        mineru_backend=FakeMineruBackend(mineru_output),
        evidence_compiler=compiler,
    )
    with TestClient(app) as client:
        created = client.post("/v1/compile-jobs", **_upload(artifact, b"%PDF-1.4\n"))
        job_id = created.json()["job"]["id"]
        assert compiler.extracting.wait(timeout=5)
        compiler.allow_validating.set()
        assert compiler.validating.wait(timeout=5)
        _wait_status(client, job_id, "validating")
        job_dir = app.state.jobs.work(job_id).input_path.parent

        assert client.delete(f"/v1/compile-jobs/{job_id}").status_code == 204
        assert client.delete(f"/v1/compile-jobs/{job_id}").status_code == 204
        assert not job_dir.exists()

        compiler.allow_completion.set()
        for _ in range(100):
            if not app.state.running_jobs:
                break
            time.sleep(0.01)
        assert not app.state.running_jobs
        assert not job_dir.exists()
        assert client.get(f"/v1/compile-jobs/{job_id}").status_code == 404


def test_jobs_pipeline_serial_mineru_while_gpt_runs_concurrently(
    tmp_path, artifact, mineru_output, import_envelope
) -> None:
    mineru = SequencedMineru(mineru_output, 3)
    compiler = ConcurrentCompiler(import_envelope, 3)
    app = create_app(
        settings=_settings(tmp_path, gpt_concurrency=2),
        mineru_backend=mineru,
        evidence_compiler=compiler,
    )
    with TestClient(app) as client:
        created = [
            client.post("/v1/compile-jobs", **_upload(artifact, b"%PDF-1.4\n"))
            for _ in range(3)
        ]
        assert all(response.status_code == 202 for response in created)
        job_ids = [response.json()["job"]["id"] for response in created]

        assert mineru.started[0].wait(timeout=5)
        mineru.release[0].set()
        assert compiler.started[0].wait(timeout=5)

        # The second MinerU parse starts while the first Model call is blocked.
        assert mineru.started[1].wait(timeout=5)
        mineru.release[1].set()
        assert compiler.started[1].wait(timeout=5)

        # Both Model slots may be occupied without stopping the serial parser.
        assert mineru.started[2].wait(timeout=5)
        mineru.release[2].set()
        assert mineru.finished[2].wait(timeout=5)
        assert not compiler.started[2].wait(timeout=0.05)
        assert mineru.max_active == 1
        assert compiler.max_active == 2

        compiler.release[0].set()
        assert compiler.started[2].wait(timeout=5)
        compiler.release[1].set()
        compiler.release[2].set()
        for job_id in job_ids:
            _wait_status(client, job_id, "completed")


def test_job_api_never_returns_arbitrary_internal_exception_content(
    tmp_path, artifact, mineru_output, import_envelope
) -> None:
    marker = "SECRET-PAPER-MARKER-DO-NOT-RETURN"
    app = create_app(
        settings=_settings(tmp_path),
        mineru_backend=ExplodingMineru(mineru_output, marker),
        evidence_compiler=StaticCompiler(import_envelope),
    )
    with TestClient(app) as client:
        created = client.post("/v1/compile-jobs", **_upload(artifact, b"%PDF-1.4\n"))
        assert created.status_code == 202
        failed = _wait_status(client, created.json()["job"]["id"], "failed")

    serialized = json.dumps(failed)
    assert marker not in serialized
    assert failed["job"]["error"] == {
        "code": "INTERNAL_ERROR",
        "message": "Unexpected compiler failure",
        "retryable": False,
    }


def test_completed_job_exposes_its_citation_source_endpoint(
    tmp_path, artifact, mineru_output, import_envelope
) -> None:
    store = JobStore(tmp_path / "citation-source-state")
    created = store.create(artifact=artifact, verify=False, content=b"%PDF-1.4\n")
    store.transition(created.job.id, "parsing")
    store.transition(created.job.id, "extracting")
    store.transition(created.job.id, "validating")
    source = CitationSourceProjection(
        artifact_version_id=artifact.version_id,
        references=[],
        mentions=[],
        warnings=["test citation projection"],
    )
    store.complete(created.job.id, import_envelope, source)
    app = create_app(
        settings=_settings(tmp_path),
        mineru_backend=FakeMineruBackend(mineru_output),
        evidence_compiler=StaticCompiler(import_envelope),
        job_store=store,
    )

    with TestClient(app) as client:
        response = client.get(f"/v1/compile-jobs/{created.job.id}/citation-source")

    assert response.status_code == 200
    assert response.json() == source.model_dump(mode="json")


def test_citation_reception_endpoint_is_authenticated_and_returns_null_verifier(
    tmp_path, mineru_output, import_envelope
) -> None:
    pair_payload = {
        "schema_version": "citation-reception/1.0",
        "project_id": "project.api.citation",
        "citing_snapshot_id": "snapshot.api.citing",
        "cited_snapshot_id": "snapshot.api.cited",
        "reference": {
            "id": "citation.reference.api",
            "raw_text": "Smith B. Result. 2021.",
            "doi": None,
            "year": 2021,
        },
        "mentions": [
            {
                "id": "citation.mention.api",
                "marker": "[1]",
                "exact_quote": "Our result was consistent with Smith [1].",
                "context_text": "Our result was consistent with Smith [1].",
                "page": 2,
                "group_size": 1,
                "candidate_functions": ["result_comparison"],
            }
        ],
        "citing_entities": [
            {
                "id": "a.observation.api",
                "kind": "observation",
                "text": "A measured the result.",
                "anchor_ids": ["a.anchor.api"],
                "root_claim_ids": ["a.claim.api"],
            }
        ],
        "cited_entities": [
            {
                "id": "b.claim.api",
                "kind": "atomic_claim",
                "text": "B reported the result.",
                "anchor_ids": ["b.anchor.api"],
                "root_claim_ids": ["b.claim.api"],
            }
        ],
    }
    empty_hash = hashlib.sha256(b"[]").hexdigest()
    result = CitationReceptionEnvelope(
        citing_snapshot_id="snapshot.api.citing",
        cited_snapshot_id="snapshot.api.cited",
        reference_id="citation.reference.api",
        extraction=CitationExtractionEnvelope(
            run_id="citation-extraction:sha256:" + "c" * 64,
            attempts=1,
            assertions_sha256=empty_hash,
            assertions=[],
        ),
        verification=None,
    )
    analyzer = StaticCitationAnalyzer(result)
    secret = "citation-api-secret-0123456789abcdef"
    app = create_app(
        settings=_settings(tmp_path, secret=secret),
        mineru_backend=FakeMineruBackend(mineru_output),
        evidence_compiler=StaticCompiler(import_envelope),
        citation_analyzer=analyzer,
    )

    with TestClient(app) as client:
        unauthorized = client.post(
            "/v1/citation-receptions/analyze",
            json=pair_payload,
        )
        response = client.post(
            "/v1/citation-receptions/analyze",
            json=pair_payload,
            headers={"Authorization": f"Bearer {secret}"},
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.json()["verification"] is None
    assert response.json()["extraction"]["assertions_sha256"] == empty_hash
    assert analyzer.received == CitationPairRequest.model_validate(
        pair_payload,
        strict=True,
    )


@pytest.mark.parametrize("value", [0, 5, True, 1.5])
def test_mineru_concurrency_is_bounded(tmp_path, value):
    with pytest.raises(ValueError, match="MINERU_CONCURRENCY"):
        Settings(data_dir=tmp_path, mineru_concurrency=value)


def test_mineru_environment_config(monkeypatch, tmp_path):
    monkeypatch.setenv("COMPILER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COMPILER_MINERU_URL", "http://127.0.0.1:18006/file_parse")
    monkeypatch.setenv("COMPILER_MINERU_API_TOKEN", "test-token")
    monkeypatch.setenv("COMPILER_MINERU_CONCURRENCY", "4")
    settings = Settings.from_environment()
    assert settings.mineru_concurrency == 4
    assert settings.mineru_api_token == "test-token"
    monkeypatch.setenv("COMPILER_MINERU_CONCURRENCY", "5")
    with pytest.raises(ValueError, match="MINERU_CONCURRENCY"):
        Settings.from_environment()


def test_four_mineru_jobs_run_and_fifth_waits(
    tmp_path, artifact, mineru_output, import_envelope
):
    mineru = SequencedMineru(mineru_output, 5)
    app = create_app(
        settings=_settings(tmp_path, mineru_concurrency=4),
        mineru_backend=mineru,
        evidence_compiler=StaticCompiler(import_envelope),
    )
    with TestClient(app) as client:
        try:
            created = [
                client.post("/v1/compile-jobs", **_upload(artifact, b"%PDF-1.4\n"))
                for _ in range(5)
            ]
            assert all(response.status_code == 202 for response in created)
            for started in mineru.started[:4]:
                assert started.wait(timeout=5)
            assert not mineru.started[4].wait(timeout=0.1)
            assert mineru.max_active == 4
            mineru.release[0].set()
            assert mineru.started[4].wait(timeout=5)
            assert mineru.max_active == 4
        finally:
            for release in mineru.release:
                release.set()
        for response in created:
            _wait_status(client, response.json()["job"]["id"], "completed")


def test_cancelled_parser_retains_slot_until_worker_finishes():
    from compiler_service.api import _run_mineru_blocking

    entered = threading.Event()
    release = threading.Event()
    second_entered = threading.Event()

    def blocking():
        entered.set()
        assert release.wait(timeout=5)

    async def exercise():
        slots = asyncio.Semaphore(1)
        with ThreadPoolExecutor(max_workers=2) as pool:

            async def run(function):
                async with slots:
                    await _run_mineru_blocking(pool, function)

            first = asyncio.create_task(run(blocking))
            second = None
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                first.cancel()
                second = asyncio.create_task(run(second_entered.set))
                await asyncio.sleep(0)
                first.cancel()
                assert not await asyncio.to_thread(second_entered.wait, 0.1)
                assert not first.done()
            finally:
                release.set()
            with pytest.raises(asyncio.CancelledError):
                await first
            await second
            assert second_entered.is_set()

    asyncio.run(exercise())


def test_cancelled_pending_parser_never_starts():
    from compiler_service.api import _run_mineru_blocking

    occupied = threading.Event()
    release = threading.Event()
    parsed = threading.Event()

    def occupy_pool():
        occupied.set()
        assert release.wait(timeout=5)

    async def exercise():
        with ThreadPoolExecutor(max_workers=1) as pool:
            blocker = pool.submit(occupy_pool)
            try:
                assert await asyncio.to_thread(occupied.wait, 2)
                task = asyncio.create_task(_run_mineru_blocking(pool, parsed.set))
                # Let the helper submit its work behind the occupied worker.
                await asyncio.sleep(0)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=1)
            finally:
                release.set()
            await asyncio.wrap_future(blocker)
            # Drain the executor to prove the cancelled queue entry was skipped.
            await asyncio.wrap_future(pool.submit(lambda: None))
            assert not parsed.is_set()

    asyncio.run(exercise())


class CountingCompiler(StaticCompiler):
    def __init__(self, envelope: Any) -> None:
        super().__init__(envelope)
        self.calls = 0
        self._lock = threading.Lock()

    def compile(self, document: Any, **kwargs: Any) -> Any:
        with self._lock:
            self.calls += 1
        return super().compile(document, **kwargs)


class RefusingMineru(FakeMineruBackend):
    """Fails the test if restart recovery re-parses a saved document."""

    def __init__(self, output: Any) -> None:
        super().__init__(output)
        self.calls = 0

    def parse(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("a saved DocumentIR must not be parsed again")


def test_idempotent_job_submission_never_starts_a_second_run(
    tmp_path, artifact, mineru_output, import_envelope
) -> None:
    compiler = CountingCompiler(import_envelope)
    app = create_app(
        settings=_settings(tmp_path),
        mineru_backend=FakeMineruBackend(mineru_output),
        evidence_compiler=compiler,
    )
    key = "0b7f3d64-2f4c-4f43-9a52-2f3d7cfe5a11"
    with TestClient(app) as client:
        first = client.post(
            "/v1/compile-jobs",
            headers={"idempotency-key": key},
            **_upload(artifact, b"%PDF-1.4\n"),
        )
        assert first.status_code == 202
        job_id = first.json()["job"]["id"]
        _wait_status(client, job_id, "completed")

        replay = client.post(
            "/v1/compile-jobs",
            headers={"idempotency-key": key},
            **_upload(artifact, b"%PDF-1.4\n"),
        )
        assert replay.status_code == 200
        assert replay.json()["job"]["id"] == job_id
        assert replay.json()["job"]["status"] == "completed"

        found = client.get("/v1/compile-jobs", params={"idempotency_key": key})
        assert found.status_code == 200
        assert found.json()["job"]["id"] == job_id
        missing = client.get(
            "/v1/compile-jobs",
            params={"idempotency_key": "11111111-2f4c-4f43-9a52-2f3d7cfe5a11"},
        )
        assert missing.status_code == 404

        listed = client.get("/v1/compile-jobs", params={"ids": job_id})
        assert listed.status_code == 200
        assert listed.json()["schema_version"] == "compiler-job-list/1.0"
        assert [job["id"] for job in listed.json()["jobs"]] == [job_id]
        assert client.get("/v1/compile-jobs").status_code == 400

        late = _upload(artifact, b"%PDF-1.4\n")
        late["data"]["submit_not_after_ms"] = "1"
        expired = client.post(
            "/v1/compile-jobs",
            headers={"idempotency-key": "22222222-2f4c-4f43-9a52-2f3d7cfe5a11"},
            **late,
        )
        assert expired.status_code == 409
        assert expired.json()["error"]["code"] == "COMPILE_SUBMISSION_EXPIRED"
    assert compiler.calls == 1


def test_restart_resumes_extraction_from_the_saved_document(
    tmp_path, artifact, mineru_output, import_envelope
) -> None:
    settings = _settings(tmp_path)
    store = JobStore(settings.data_dir, ttl_seconds=settings.job_ttl_seconds)
    created = store.create(artifact=artifact, verify=False, content=b"%PDF-1.4\n")
    job_id = created.job.id
    store.transition(job_id, "parsing")
    store.save_document(job_id, normalize_mineru(mineru_output, artifact))
    store.transition(job_id, "extracting")

    mineru = RefusingMineru(mineru_output)
    compiler = CountingCompiler(import_envelope)
    app = create_app(
        settings=settings,
        mineru_backend=mineru,
        evidence_compiler=compiler,
        job_store=JobStore(settings.data_dir, ttl_seconds=settings.job_ttl_seconds),
    )
    with TestClient(app) as client:
        completed = _wait_status(client, job_id, "completed")

    assert completed["job"]["error"] is None
    assert mineru.calls == 0
    assert compiler.calls == 1
