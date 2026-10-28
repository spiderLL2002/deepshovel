"""FastAPI boundary for direct smoke tests and durable asynchronous compilation."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import logging
import tempfile
import threading
import zipfile
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from . import __version__
from .citation_reception import CitationPairRequest, CitationReceptionAnalyzer
from .claim_comparison import (
    ClaimComparisonAnalyzer,
    ClaimComparisonRequest,
    ClaimNormalizationRequest,
)
from .compiler import EvidenceCompiler
from .config import (
    GPT_MODEL,
    MAX_UPLOAD_BYTES,
    MINERU_BACKEND,
    MINERU_PROTOCOL_VERSION,
    Settings,
)
from .data_quality import DATA_QUALITY_INSPECTION_VERSION, inspect_document
from .data_quality_store import DataQualityStore
from .document_extraction import build_document_extraction
from .errors import (
    ArtifactHashMismatch,
    AuthenticationError,
    CompilerError,
    InputError,
    JobCancelled,
    JobNotFound,
    ModelUnavailable,
    QueryPlanHandoffError,
    ServiceRestarted,
    UnsupportedMediaType,
)
from .fallback_history import FallbackHistory
from .http_transport import CloseHttpConnectionMiddleware
from .job_control import model_request_cancellation
from .job_store import JobStore
from .mineru import MineruBackend, MineruRemoteBackend
from .model_client import ModelJsonClient
from .models import (
    ArtifactContext,
    CompileRequest,
    JobListResponse,
    OpenAlexClassificationRequest,
    ParseResponse,
    sanitize_file_name,
)
from .normalize import normalize_mineru
from .openalex_topics import OpenAlexTopicClassifier
from .release import compiler_release
from .retrieval_contract import (
    MAX_RETRIEVAL_BODY_BYTES,
    ClaimRelationBatchItem,
    ClaimRelationBatchRequest,
    ClaimRelationBatchResponse,
    DeleteSnapshotsRequest,
    HybridSearchRequest,
    HybridSearchResponse,
    IndexSnapshotRequest,
    ManifestCheckRequest,
)
from .retrieval_providers import (
    FastEmbedDenseEncoder,
    ModelQueryPlanner,
    ModelRelationClassifier,
)
from .retrieval_service import RetrievalService

logger = logging.getLogger(__name__)
# Each attempt already runs the full model retry ladder. Repeat an item once for
# unexpected or retryable failures only; deterministic input/contract errors are
# final. The Web work queue retries whole batches later with backoff.
_CLAIM_RELATION_BATCH_ATTEMPTS = 2


async def _run_interactive_request(request: Request, function, *args, **kwargs):
    """Wait without a deadline; disconnects cancel the active model requests."""
    cancelled = threading.Event()

    def check_cancelled():
        if cancelled.is_set():
            raise JobCancelled("Interactive request was cancelled")

    def run():
        with model_request_cancellation(check_cancelled):
            return function(*args, **kwargs)

    worker = asyncio.create_task(asyncio.to_thread(run))
    try:
        while not worker.done():
            # This only polls disconnects; it never expires the worker.
            await asyncio.wait({worker}, timeout=0.1)
            if not worker.done() and await request.is_disconnected():
                cancelled.set()
        return await worker
    finally:
        cancelled.set()
        if not worker.done():
            with suppress(CompilerError, asyncio.CancelledError):
                await asyncio.shield(worker)


@dataclass(slots=True)
class _ClaimRelationBatchState:
    request: ClaimRelationBatchRequest
    batch_id: str
    order: dict[tuple[str, str], int]
    items: dict[tuple[str, str], ClaimRelationBatchItem] = field(default_factory=dict)
    finished: bool = False
    cancelled: bool = False
    retired: bool = False
    task: asyncio.Task[None] | None = None


_SUPPORTED_MEDIA = {
    "application/pdf": {"pdf"},
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {"xlsx"},
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {"docx"},
    "image/png": {"png"},
    "image/jpeg": {"jpg", "jpeg"},
    "image/tiff": {"tif", "tiff"},
}


class _LazyRetrieval:
    """The retrieval service, created on first use (it loads local models)."""

    def __init__(
        self,
        configured: Settings,
        gpt_client: ModelJsonClient | None,
        service: RetrievalService | Any | None,
    ) -> None:
        self._configured = configured
        self._gpt_client = gpt_client
        self._service = service
        self._lock = threading.Lock()

    @property
    def current(self) -> RetrievalService | Any | None:
        return self._service

    def get(self) -> RetrievalService | Any:
        if self._service is not None:
            return self._service
        configured = self._configured
        gpt_client = self._gpt_client
        with self._lock:
            if self._service is None:
                try:
                    dense = FastEmbedDenseEncoder(
                        configured.retrieval_dense_model,
                        cache_dir=configured.data_dir / "retrieval" / "models",
                        model_revision=configured.retrieval_dense_model_revision,
                    )
                    self._service = RetrievalService(
                        data_dir=configured.data_dir,
                        dense_encoder=dense,
                        query_planner=(
                            None
                            if gpt_client is None
                            or not configured.retrieval_remote_models
                            else ModelQueryPlanner(gpt_client)
                        ),
                        relation_classifier=(
                            None
                            if gpt_client is None
                            or not configured.retrieval_remote_models
                            else ModelRelationClassifier(gpt_client)
                        ),
                        rrf_k=configured.retrieval_rrf_k,
                        materialized_ranking_features_enabled=(
                            configured.retrieval_materialized_ranking_features
                        ),
                        query_plan_handoff_secret=configured.shared_secret,
                        qdrant_url=configured.retrieval_qdrant_url,
                        qdrant_api_key=configured.retrieval_qdrant_api_key,
                        vector_backend=configured.retrieval_vector_backend,
                    )
                except Exception as exc:
                    raise CompilerError("retrieval service is unavailable") from exc
        return self._service

    def invoke(self, method: str, payload: Any, **options: Any) -> Any:
        try:
            return getattr(self.get(), method)(payload, **options)
        except CompilerError:
            raise
        except Exception as exc:
            raise CompilerError("retrieval operation failed") from exc

    def health(self) -> dict[str, Any]:
        configured = self._configured
        current = self._service
        if current is None:
            readiness = {
                "dense": False,
                "vector_store": False,
            }
            status = "uninitialized"
            dense_revision = configured.retrieval_dense_model_revision
        else:
            readiness = getattr(
                current,
                "model_readiness",
                {"dense": True, "vector_store": True},
            )
            status = "ready" if all(readiness.values()) else "cold"
            dense_revision = getattr(
                current, "dense_revision", configured.retrieval_dense_model_revision
            )
        return {
            "status": status,
            "index_version": "evidence-retrieval/2.0",
            "dense_model": configured.retrieval_dense_model,
            "dense_model_revision": dense_revision,
            "models_ready": readiness,
            "rrf_k": configured.retrieval_rrf_k,
            "concurrency": configured.retrieval_concurrency,
            "materialized_ranking_features": {
                "configured": configured.retrieval_materialized_ranking_features,
                "service_enabled": bool(
                    getattr(current, "materialized_ranking_features_enabled", False)
                ),
                "preflight": getattr(
                    current, "materialized_ranking_feature_preflight", None
                ),
            },
            "vector_store": (
                "numpy-memmap"
                if configured.retrieval_vector_backend == "numpy"
                else "qdrant-local"
                if configured.retrieval_qdrant_url is None
                else "qdrant-server"
            ),
            "warmup_command": "python -m compiler_service.retrieval_warmup",
        }


@dataclass(slots=True)
class _AppServices:
    """The collaborators every route group shares; built once by create_app."""

    settings: Settings
    mineru: MineruBackend
    compiler: EvidenceCompiler | Any | None
    jobs: JobStore
    data_quality: DataQualityStore
    topic_classifier: OpenAlexTopicClassifier
    citation_analyzer: CitationReceptionAnalyzer | Any | None
    claim_comparison_analyzer: ClaimComparisonAnalyzer | Any | None
    retrieval: _LazyRetrieval


def _log_feature_preflight(future: asyncio.Future[Any]) -> None:
    if future.cancelled():
        return
    error = future.exception()
    if error is not None:
        logger.warning(
            "Materialized ranking-feature backfill failed; exact features "
            "remain in use (%s)",
            type(error).__name__,
        )


def create_app(
    *,
    settings: Settings | None = None,
    mineru_backend: MineruBackend | None = None,
    evidence_compiler: EvidenceCompiler | Any | None = None,
    job_store: JobStore | None = None,
    retrieval_service: RetrievalService | Any | None = None,
    citation_analyzer: CitationReceptionAnalyzer | Any | None = None,
    claim_comparison_analyzer: ClaimComparisonAnalyzer | Any | None = None,
) -> FastAPI:
    """Composition root: build the services, then install each route group."""
    services = _build_services(
        settings=settings,
        mineru_backend=mineru_backend,
        evidence_compiler=evidence_compiler,
        job_store=job_store,
        retrieval_service=retrieval_service,
        citation_analyzer=citation_analyzer,
        claim_comparison_analyzer=claim_comparison_analyzer,
    )
    app = FastAPI(
        title="DeepShovel Compiler",
        version=__version__,
        lifespan=lambda application: _lifespan(services, application),
    )
    app.state.settings = services.settings
    app.state.mineru = services.mineru
    app.state.compiler = services.compiler
    app.state.jobs = services.jobs
    app.state.data_quality_jobs = services.data_quality
    app.state.retrieval = services.retrieval.current
    app.state.citation_analyzer = services.citation_analyzer
    app.state.claim_comparison_analyzer = services.claim_comparison_analyzer
    _install_boundary(app, services)
    _register_status_routes(app, services)
    _register_document_routes(app, services)
    _register_analysis_routes(app, services)
    _register_retrieval_routes(app, services)
    _register_compile_job_routes(app, services)
    return app


def _build_services(
    *,
    settings: Settings | None,
    mineru_backend: MineruBackend | None,
    evidence_compiler: EvidenceCompiler | Any | None,
    job_store: JobStore | None,
    retrieval_service: RetrievalService | Any | None,
    citation_analyzer: CitationReceptionAnalyzer | Any | None,
    claim_comparison_analyzer: ClaimComparisonAnalyzer | Any | None,
) -> _AppServices:
    configured = settings or Settings.from_environment()
    backend = mineru_backend or MineruRemoteBackend(
        url=configured.mineru_url,
        timeout_seconds=configured.mineru_timeout_seconds,
        api_token=configured.mineru_api_token,
    )
    gpt_client = (
        ModelJsonClient.from_api_key(
            configured.gpt_api_key,
            deepseek_api_key=configured.deepseek_api_key,
            on_fallback=FallbackHistory(
                configured.data_dir / "model-fallback.json"
            ).record,
        )
        if configured.gpt_api_key
        else None
    )
    topic_classifier = OpenAlexTopicClassifier(
        api_key=configured.openalex_api_key,
        cache_dir=configured.data_dir / "openalex" / "models",
    )
    compiler = evidence_compiler
    if compiler is None and gpt_client is not None:
        compiler = EvidenceCompiler(
            gpt_client,
            topic_classifier=topic_classifier,
        )
    active_citation_analyzer = citation_analyzer
    if active_citation_analyzer is None and gpt_client is not None:
        active_citation_analyzer = CitationReceptionAnalyzer(gpt_client)
    active_claim_comparison_analyzer = claim_comparison_analyzer
    if active_claim_comparison_analyzer is None and gpt_client is not None:
        active_claim_comparison_analyzer = ClaimComparisonAnalyzer(gpt_client)
    return _AppServices(
        settings=configured,
        mineru=backend,
        compiler=compiler,
        jobs=job_store
        or JobStore(configured.data_dir, ttl_seconds=configured.job_ttl_seconds),
        data_quality=DataQualityStore(configured.data_dir),
        topic_classifier=topic_classifier,
        citation_analyzer=active_citation_analyzer,
        claim_comparison_analyzer=active_claim_comparison_analyzer,
        retrieval=_LazyRetrieval(configured, gpt_client, retrieval_service),
    )


@asynccontextmanager
async def _lifespan(services: _AppServices, app: FastAPI) -> AsyncIterator[None]:
    configured = services.settings
    store = services.jobs
    recovery = store.recover_incomplete()
    app.state.running_jobs = set()
    app.state.claim_relation_batches = {}
    app.state.mineru_slots = asyncio.Semaphore(configured.mineru_concurrency)
    app.state.gpt_slots = asyncio.Semaphore(configured.gpt_concurrency)
    app.state.retrieval_slots = asyncio.Semaphore(configured.retrieval_concurrency)
    # One shared bulk budget: simultaneous imports and graph jobs together
    # must not double the requested 64-call ceiling.
    app.state.bulk_model_slots = asyncio.Semaphore(64)
    app.state.bulk_model_worker_slots = threading.BoundedSemaphore(64)
    app.state.retrieval_index_lock = asyncio.Lock()
    app.state.mineru_executor = ThreadPoolExecutor(
        max_workers=configured.mineru_concurrency,
        thread_name_prefix="deepshovel-mineru"
    )
    app.state.gpt_executor = ThreadPoolExecutor(
        max_workers=configured.gpt_concurrency,
        thread_name_prefix="deepshovel-gpt",
    )
    app.state.retrieval_executor = ThreadPoolExecutor(
        max_workers=configured.retrieval_concurrency,
        thread_name_prefix="deepshovel-retrieval",
    )
    app.state.data_quality_executor = ThreadPoolExecutor(
        max_workers=2, thread_name_prefix="deepshovel-data-quality"
    )
    feature_preflight: asyncio.Future[Any] | None = None
    if configured.retrieval_materialized_ranking_features:
        # Serve immediately: every sidecar row is validated on read and
        # falls back to exact computation, so the backfill is background
        # work and never blocks readiness (RTX-2).
        feature_preflight = asyncio.get_running_loop().run_in_executor(
            app.state.retrieval_executor,
            lambda: services.retrieval.get().prepare_materialized_ranking_features(),
        )
        feature_preflight.add_done_callback(_log_feature_preflight)
    if configured.environment == "production":
        await asyncio.get_running_loop().run_in_executor(
            app.state.retrieval_executor,
            lambda: services.retrieval.get().warm_models(),
        )
    # Resume interrupted jobs from their saved inputs in creation order; the
    # parser and model slots bound how many actually run at once.
    for job_id in recovery.resumed:
        if services.compiler is None:
            await asyncio.to_thread(
                store.fail,
                job_id,
                ModelUnavailable("GPT_API_KEY is not configured"),
            )
        else:
            _start_job(app, job_id, store, services.mineru, services.compiler)
    if recovery.resumed or recovery.failed:
        logger.warning(
            "Compiler restart recovery resumed %s and failed %s job(s)",
            len(recovery.resumed),
            len(recovery.failed),
        )
    cleanup = asyncio.create_task(_cleanup_loop(store))
    try:
        yield
    finally:
        cleanup.cancel()
        await asyncio.gather(cleanup, return_exceptions=True)
        for task in tuple(app.state.running_jobs):
            task.cancel()
        await asyncio.gather(*tuple(app.state.running_jobs), return_exceptions=True)
        app.state.mineru_executor.shutdown(wait=False, cancel_futures=True)
        app.state.gpt_executor.shutdown(wait=False, cancel_futures=True)
        app.state.retrieval_executor.shutdown(wait=False, cancel_futures=True)
        app.state.data_quality_executor.shutdown(wait=False, cancel_futures=True)


def _install_boundary(app: FastAPI, services: _AppServices) -> None:
    """Authentication, the corpus body limit and the error mapping."""
    configured = services.settings

    @app.middleware("http")
    async def authenticate(request: Request, call_next: Any) -> Any:
        secret = configured.shared_secret
        retrieval_request = request.url.path.startswith("/v1/retrieval/")
        citation_request = request.url.path.startswith("/v1/citation-receptions/")
        claim_comparison_request = request.url.path.startswith("/v1/claim-comparisons/")
        corpus_request = (
            retrieval_request or citation_request or claim_comparison_request
        )
        if corpus_request and secret is None:
            error = AuthenticationError(
                "corpus-processing endpoints require configured compiler credentials"
            )
            return _error_response(error)
        if secret and request.url.path.startswith("/v1/"):
            authorization = request.headers.get("authorization", "")
            bearer = (
                authorization[7:] if authorization[:7].casefold() == "bearer " else ""
            )
            # Compare bytes: str comparison raises TypeError for non-ASCII input,
            # which would turn a bad credential into an unauthenticated 500.
            valid = hmac.compare_digest(
                bearer.encode("utf-8", "surrogateescape"), secret.encode("utf-8")
            )
            if not valid:
                error = AuthenticationError("valid compiler credentials are required")
                return _error_response(error)
        if corpus_request and request.method == "POST":
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    length = int(content_length)
                except ValueError:
                    return _error_response(InputError("invalid Content-Length"))
                if length < 0 or length > MAX_RETRIEVAL_BODY_BYTES:
                    return _retrieval_too_large_response()
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > MAX_RETRIEVAL_BODY_BYTES:
                    return _retrieval_too_large_response()
                body.extend(chunk)
            # Starlette's cached request replays _body to FastAPI after middleware.
            request._body = bytes(body)
        return await call_next(request)

    # Outermost user middleware also covers authentication/body-limit responses.
    app.add_middleware(CloseHttpConnectionMiddleware)

    @app.exception_handler(CompilerError)
    async def compiler_error(_: Request, error: CompilerError) -> JSONResponse:
        return _error_response(error)

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        _: Request, error: RequestValidationError
    ) -> JSONResponse:
        details = "; ".join(
            ".".join(str(part) for part in item["loc"]) + ": " + item["msg"]
            for item in error.errors()[:8]
        )
        return _error_response(InputError(details or "request validation failed"))


def _register_status_routes(app: FastAPI, services: _AppServices) -> None:
    configured = services.settings
    backend = services.mineru
    topic_classifier = services.topic_classifier
    release = compiler_release()

    @app.get("/ready")
    async def ready() -> JSONResponse:
        # Process supervision must not wait on the remote parser. The lifespan
        # completes local index validation and model warmup before HTTP serves.
        # Keep /health as the separate dependency diagnostic endpoint.
        retrieval_status = services.retrieval.health()["status"]
        available = services.compiler is not None and (
            configured.environment != "production" or retrieval_status == "ready"
        )
        return JSONResponse(
            status_code=200 if available else 503,
            content={
                "status": "ok" if available else "degraded",
                "service": "deepshovel-compiler",
                "retrieval": {"status": retrieval_status},
            },
        )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        availability = await asyncio.to_thread(backend.availability)
        mineru_ready = (
            availability.available
            and availability.protocol_version == MINERU_PROTOCOL_VERSION
        )
        status = "ok" if mineru_ready and services.compiler is not None else "degraded"
        return {
            "status": status,
            "service": "deepshovel-compiler",
            "version": __version__,
            "release": release,
            "environment": configured.environment,
            "auth_required": configured.shared_secret is not None,
            "mineru": {
                "status": "available" if mineru_ready else "unavailable",
                "backend": MINERU_BACKEND,
                "transport": "remote",
                "version": availability.version,
                "protocol_version": availability.protocol_version,
                "expected_protocol_version": MINERU_PROTOCOL_VERSION,
            },
            "gpt": {
                "configured": services.compiler is not None,
                "model": GPT_MODEL,
                "concurrency": configured.gpt_concurrency,
            },
            "openalex": {
                "classifier": topic_classifier.model_name,
                "model_ready": topic_classifier.ready,
            },
            "retrieval": services.retrieval.health(),
            "job_store": {"status": "available"},
        }


def _register_document_routes(app: FastAPI, services: _AppServices) -> None:
    """Direct parse/compile, OpenAlex classification, data quality, extraction."""
    configured = services.settings
    backend = services.mineru
    data_quality_store = services.data_quality

    @app.post("/v1/parse")
    async def parse(
        request: Request,
        file: Annotated[UploadFile, File()],
        paper_id: Annotated[str, Form()],
        artifact_id: Annotated[str, Form()],
        version_id: Annotated[str, Form()],
        artifact_sha256: Annotated[str, Form()],
        media_type: Annotated[str, Form()],
        locator: Annotated[str, Form()],
    ) -> JSONResponse:
        content, artifact = await _trusted_upload(
            file=file,
            paper_id=paper_id,
            artifact_id=artifact_id,
            version_id=version_id,
            artifact_sha256=artifact_sha256,
            media_type=media_type,
            locator=locator,
        )
        async with request.app.state.mineru_slots:
            parsed = await _run_mineru_blocking(
                request.app.state.mineru_executor,
                _direct_parse,
                configured,
                backend,
                artifact,
                content,
            )
        return _json_model(parsed)

    @app.post("/v1/compile")
    async def compile_direct(request: Request, payload: CompileRequest) -> JSONResponse:
        active_compiler = _require_compiler(services.compiler)
        async with request.app.state.bulk_model_slots, request.app.state.gpt_slots:
            outcome = await _run_bulk_blocking(
                request.app,
                request.app.state.gpt_executor,
                active_compiler.compile,
                payload.document,
                verify=payload.verify,
            )
        return _json_model(outcome.envelope)

    @app.post("/v1/openalex/classify")
    async def classify_openalex(
        request: Request, payload: OpenAlexClassificationRequest
    ) -> JSONResponse:
        classification = await _run_blocking(
            request.app.state.gpt_executor,
            services.topic_classifier.classify,
            payload,
        )
        return _json_model(classification, exclude_none=False)

    @app.post("/v1/data-quality-jobs", status_code=202)
    async def create_data_quality_job(
        request: Request,
        file: Annotated[UploadFile, File()],
        paper_id: Annotated[str, Form()],
        artifact_id: Annotated[str, Form()],
        version_id: Annotated[str, Form()],
        artifact_sha256: Annotated[str, Form()],
        media_type: Annotated[str, Form()],
        locator: Annotated[str, Form()],
    ) -> JSONResponse:
        content, artifact = await _trusted_upload(
            file=file,
            paper_id=paper_id,
            artifact_id=artifact_id,
            version_id=version_id,
            artifact_sha256=artifact_sha256,
            media_type=media_type,
            locator=locator,
        )
        if artifact.media_type != "application/pdf":
            raise UnsupportedMediaType("Data Quality accepts PDF papers only")
        response = await asyncio.to_thread(
            data_quality_store.create_upload, artifact, content
        )
        if response["job"]["status"] == "queued":
            task = asyncio.create_task(
                _run_data_quality_upload(
                    request.app, response["job"]["id"], data_quality_store, backend
                )
            )
            request.app.state.running_jobs.add(task)
            task.add_done_callback(request.app.state.running_jobs.discard)
        return JSONResponse(response, status_code=202)

    @app.get("/v1/data-quality-jobs")
    async def get_data_quality_job(version_id: str) -> JSONResponse:
        response = await asyncio.to_thread(data_quality_store.find_version, version_id)
        if response is None:
            raise JobNotFound("data quality job was not found")
        return JSONResponse(response)

    @app.get("/v1/data-quality-jobs/{job_id}/result")
    async def get_data_quality_result(job_id: str) -> JSONResponse:
        report = await asyncio.to_thread(data_quality_store.result, job_id)
        tables = report.get("paper_tables")
        if report.get("inspection_version") != DATA_QUALITY_INSPECTION_VERSION or (
            isinstance(tables, list)
            and any(
                not isinstance(table, dict)
                or "columns" not in table
                or "rows" not in table
                for table in tables
            )
        ):
            parsed = await asyncio.to_thread(data_quality_store.load_document, job_id)
            report = await asyncio.to_thread(inspect_document, parsed.document)
            await asyncio.to_thread(data_quality_store.complete, job_id, report)
        return JSONResponse(report)

    @app.get("/v1/document-extractions/{version_id}")
    async def get_version_document_extraction(version_id: str) -> JSONResponse:
        parsed = await asyncio.to_thread(
            data_quality_store.load_document_version, version_id
        )
        projection = await asyncio.to_thread(build_document_extraction, parsed.document)
        return _json_model(projection, exclude_none=False)

    @app.delete("/v1/document-extractions/{version_id}", status_code=204)
    async def delete_version_document_extraction(version_id: str) -> Response:
        await asyncio.to_thread(data_quality_store.delete_version, version_id)
        return Response(status_code=204)


def _register_analysis_routes(app: FastAPI, services: _AppServices) -> None:
    """Citation reception and Claim comparison (corpus-processing endpoints)."""

    @app.post("/v1/citation-receptions/analyze")
    async def analyze_citation_reception(
        request: Request,
        payload: CitationPairRequest,
    ) -> JSONResponse:
        analyzer = _require_citation_analyzer(services.citation_analyzer)
        result = await _run_interactive_request(request, analyzer.analyze, payload)
        return _json_model(result, exclude_none=False)

    @app.post("/v1/claim-comparisons/normalize")
    async def normalize_claim_comparison_input(
        request: Request,
        payload: ClaimNormalizationRequest,
    ) -> JSONResponse:
        analyzer = _require_claim_comparison_analyzer(
            services.claim_comparison_analyzer
        )
        result = await _run_interactive_request(request, analyzer.normalize, payload)
        return _json_model(result, exclude_none=False)

    @app.post("/v1/claim-comparisons/analyze")
    async def analyze_claim_comparisons(
        request: Request,
        payload: ClaimComparisonRequest,
    ) -> JSONResponse:
        analyzer = _require_claim_comparison_analyzer(
            services.claim_comparison_analyzer
        )
        result = await _run_interactive_request(request, analyzer.analyze, payload)
        return _json_model(result, exclude_none=False)


def _register_retrieval_routes(app: FastAPI, services: _AppServices) -> None:
    """Index synchronization, search, Claim-relation batches and deletion."""
    invoke_retrieval = services.retrieval.invoke

    @app.post("/v1/retrieval/manifests:check")
    async def check_retrieval_manifests(
        request: Request, payload: ManifestCheckRequest
    ) -> JSONResponse:
        async with request.app.state.retrieval_slots:
            response = await _run_blocking(
                request.app.state.retrieval_executor,
                invoke_retrieval,
                "check_manifests",
                payload,
            )
        return _json_model(response, exclude_none=False)

    @app.post("/v1/retrieval/index")
    async def index_retrieval_snapshot(
        request: Request, payload: IndexSnapshotRequest
    ) -> JSONResponse:
        async with (
            request.app.state.retrieval_index_lock,
            request.app.state.retrieval_slots,
        ):
            response = await _run_blocking(
                request.app.state.retrieval_executor,
                invoke_retrieval,
                "index_snapshot",
                payload,
            )
        return _json_model(response, exclude_none=False)

    @app.post("/v1/retrieval/search")
    async def search_retrieval(
        request: Request, payload: HybridSearchRequest
    ) -> JSONResponse:
        options = (
            {"interactive": True}
            if request.headers.get("x-deepshovel-interactive") == "1"
            else {}
        )
        if options:
            response = await _run_interactive_request(
                request, invoke_retrieval, "search", payload, **options
            )
        else:
            response = await asyncio.to_thread(invoke_retrieval, "search", payload)
        return _json_model(response, exclude_none=False)

    @app.post("/v1/retrieval/claim-relations:batch", status_code=202)
    async def start_claim_relation_batch(
        request: Request,
        payload: ClaimRelationBatchRequest,
        after: Annotated[int | None, Query(ge=0, le=4096)] = None,
    ) -> JSONResponse:
        digest = hashlib.sha256(
            f"{payload.project_id}\0{payload.client_request_id}".encode()
        ).hexdigest()
        batch_id = f"relation-batch:sha256:{digest}"
        existing = request.app.state.claim_relation_batches.get(batch_id)
        if existing is not None and existing.cancelled:
            request.app.state.claim_relation_batches.pop(batch_id)
            existing = None
        if existing is not None:
            if existing.request != payload:
                raise InputError("Claim relation batch idempotency key was reused")
            return _json_model(
                _claim_relation_batch_response(existing, after),
                status_code=202,
                exclude_none=False,
            )
        completed_ids = [
            key
            for key, state in request.app.state.claim_relation_batches.items()
            if state.finished
        ]
        while len(request.app.state.claim_relation_batches) >= 32 and completed_ids:
            request.app.state.claim_relation_batches.pop(completed_ids.pop(0), None)
        if len(request.app.state.claim_relation_batches) >= 32:
            raise InputError("too many active Claim relation batches")
        state = _ClaimRelationBatchState(
            request=payload,
            batch_id=batch_id,
            order={
                (item.snapshot_id, item.claim_id): index
                for index, item in enumerate(payload.claim_refs)
            },
        )
        request.app.state.claim_relation_batches[batch_id] = state
        task = asyncio.create_task(
            _run_claim_relation_batch(request.app, services.retrieval.get(), state)
        )
        state.task = task
        request.app.state.running_jobs.add(task)
        task.add_done_callback(request.app.state.running_jobs.discard)
        return _json_model(
            _claim_relation_batch_response(state, after),
            status_code=202,
            exclude_none=False,
        )

    @app.get("/v1/retrieval/claim-relations:batch/{batch_id}")
    async def get_claim_relation_batch(
        batch_id: str, after: Annotated[int | None, Query(ge=0, le=4096)] = None
    ) -> JSONResponse:
        state = app.state.claim_relation_batches.get(batch_id)
        if state is None:
            raise JobNotFound("Claim relation batch was not found")
        return _json_model(
            _claim_relation_batch_response(state, after), exclude_none=False
        )

    @app.delete("/v1/retrieval/claim-relations:batch/{batch_id}")
    async def cancel_claim_relation_batch(
        batch_id: str, after: Annotated[int | None, Query(ge=0, le=4096)] = None
    ) -> JSONResponse:
        state = app.state.claim_relation_batches.get(batch_id)
        if state is None:
            raise JobNotFound("Claim relation batch was not found")
        await _cancel_claim_relation_batch(state)
        return _json_model(
            _claim_relation_batch_response(state, after), exclude_none=False
        )

    @app.post("/v1/retrieval/snapshots:delete")
    async def delete_retrieval_snapshots(
        request: Request, payload: DeleteSnapshotsRequest
    ) -> JSONResponse:
        # A batch freezes its comparison corpus. Retire it before removing any
        # member so queued retries and late results cannot outlive that corpus.
        deleted = set(payload.snapshot_ids)
        for state in list(app.state.claim_relation_batches.values()):
            if state.request.project_id == payload.project_id and (
                state.request.active_snapshot_ids is None
                or deleted.intersection(state.request.active_snapshot_ids)
            ):
                state.retired = True
                await _cancel_claim_relation_batch(state)
        async with (
            request.app.state.retrieval_index_lock,
            request.app.state.retrieval_slots,
        ):
            response = await _run_blocking(
                request.app.state.retrieval_executor,
                invoke_retrieval,
                "delete_snapshots",
                payload,
            )
        return _json_model(response, exclude_none=False)


def _register_compile_job_routes(app: FastAPI, services: _AppServices) -> None:
    """Durable asynchronous compile jobs: create, look up, read, delete."""
    store = services.jobs
    backend = services.mineru

    @app.post("/v1/compile-jobs", status_code=202)
    async def create_job(
        request: Request,
        file: Annotated[UploadFile, File()],
        paper_id: Annotated[str, Form()],
        artifact_id: Annotated[str, Form()],
        version_id: Annotated[str, Form()],
        artifact_sha256: Annotated[str, Form()],
        media_type: Annotated[str, Form()],
        locator: Annotated[str, Form()],
        # Claim-bearing results are importable only with a verifier report.
        verify: Annotated[bool, Form()] = True,
        submit_not_after_ms: Annotated[int | None, Form(ge=0)] = None,
    ) -> JSONResponse:
        _require_compiler(services.compiler)
        idempotency_key = request.headers.get("idempotency-key")
        content, artifact = await _trusted_upload(
            file=file,
            paper_id=paper_id,
            artifact_id=artifact_id,
            version_id=version_id,
            artifact_sha256=artifact_sha256,
            media_type=media_type,
            locator=locator,
        )
        if idempotency_key is None:
            if submit_not_after_ms is not None:
                raise InputError("a submission deadline requires an idempotency key")
            response = await asyncio.to_thread(
                store.create,
                artifact=artifact,
                verify=verify,
                content=content,
            )
            created = True
        else:
            # The gateway's job ID is the key: a replayed submission returns the
            # accepted job instead of paying for a second parse and model run.
            response, created = await asyncio.to_thread(
                partial(
                    store.create_idempotent,
                    artifact=artifact,
                    verify=verify,
                    content=content,
                    idempotency_key=idempotency_key.strip(),
                    not_after_ms=submit_not_after_ms,
                )
            )
        if created:
            _start_job(request.app, response.job.id, store, backend, services.compiler)
        return _json_model(
            response, status_code=202 if created else 200, exclude_none=False
        )

    @app.get("/v1/compile-jobs")
    async def find_jobs(
        idempotency_key: str | None = None,
        ids: Annotated[str | None, Query(max_length=4_000)] = None,
    ) -> JSONResponse:
        """Bodyless lookups: one job by caller key, or several by ID."""

        if (idempotency_key is None) == (ids is None):
            raise InputError("specify exactly one of idempotency_key or ids")
        if idempotency_key is not None:
            return _json_model(
                await asyncio.to_thread(
                    store.find_by_idempotency_key, idempotency_key.strip()
                ),
                exclude_none=False,
            )
        requested = [value.strip() for value in (ids or "").split(",")]
        jobs = await asyncio.to_thread(store.get_many, requested)
        return _json_model(
            JobListResponse(jobs=[item.job for item in jobs]), exclude_none=False
        )

    @app.get("/v1/compile-jobs/{job_id}")
    async def get_job(job_id: str) -> JSONResponse:
        return _json_model(
            await asyncio.to_thread(store.get, job_id), exclude_none=False
        )

    @app.get("/v1/compile-jobs/{job_id}/result")
    async def get_result(job_id: str) -> JSONResponse:
        return _json_model(await asyncio.to_thread(store.result, job_id))

    @app.get("/v1/compile-jobs/{job_id}/citation-source")
    async def get_citation_source(job_id: str) -> JSONResponse:
        return _json_model(
            await asyncio.to_thread(store.citation_source, job_id),
            exclude_none=False,
        )

    @app.get("/v1/compile-jobs/{job_id}/document-extraction")
    async def get_compile_job_document_extraction(job_id: str) -> JSONResponse:
        parsed = await asyncio.to_thread(store.load_completed_document, job_id)
        projection = await asyncio.to_thread(build_document_extraction, parsed.document)
        return _json_model(projection, exclude_none=False)

    @app.delete("/v1/compile-jobs/{job_id}", status_code=204)
    async def delete_job(job_id: str) -> Response:
        current = await asyncio.to_thread(store.get, job_id)
        if current.job.status in {"queued", "parsing", "extracting", "validating"}:
            await asyncio.to_thread(store.cancel_active, job_id)
        else:
            await asyncio.to_thread(store.delete_terminal, job_id)
        return Response(status_code=204)


def _claim_relation_batch_response(
    state: _ClaimRelationBatchState,
    after: int | None = None,
) -> ClaimRelationBatchResponse:
    items = sorted(
        state.items.values(),
        key=lambda item: state.order[
            (item.query_claim.snapshot_id, item.query_claim.claim_id)
        ],
    )
    succeeded = sum(item.status == "succeeded" for item in items)
    failed = len(items) - succeeded
    finished = state.finished or len(items) == len(state.request.claim_refs)
    status = "cancelled" if state.cancelled else "completed" if finished else "running"
    completed = len(items)
    cursor_fields = {}
    if after is not None:
        if after > completed:
            raise InputError("batch cursor exceeds completed results")
        # Dict insertion order records completion order, not submission order.
        # Retain items so a lost response can replay exactly the same page.
        items = []
        page_bytes = 0
        if not state.retired:
            for item in list(state.items.values())[after : after + 16]:
                item_bytes = len(item.model_dump_json().encode("utf-8"))
                if page_bytes + item_bytes > 7_500_000:
                    if not items:
                        raise InputError(
                            "one batch result exceeds the response byte limit"
                        )
                    break
                items.append(item)
                page_bytes += item_bytes
        cursor_fields = {
            "item_offset": after,
            "next_cursor": completed if state.retired else after + len(items),
            "retired": state.retired,
        }
    return ClaimRelationBatchResponse(
        batch_id=state.batch_id,
        status=status,
        reprocess=state.request.reprocess,
        total=len(state.request.claim_refs),
        completed=completed,
        succeeded=succeeded,
        failed=failed,
        items=items,
        **cursor_fields,
    )


async def _run_claim_relation_batch(
    app: FastAPI,
    retrieval: RetrievalService,
    state: _ClaimRelationBatchState,
) -> None:
    # Preserve a short cancellation window after returning the durable 202
    # identity; otherwise a fast local provider can finish before the caller's
    # immediately following DELETE reaches the event loop.
    await asyncio.sleep(1.0)

    async def run_one(query_claim: Any) -> None:
        key = (query_claim.snapshot_id, query_claim.claim_id)
        for attempt in range(1, _CLAIM_RELATION_BATCH_ATTEMPTS + 1):
            try:
                async with app.state.bulk_model_slots, app.state.retrieval_slots:
                    response: HybridSearchResponse = await _run_bulk_blocking(
                        app,
                        app.state.retrieval_executor,
                        retrieval.search_claim_relations,
                        project_id=state.request.project_id,
                        active_snapshot_ids=state.request.active_snapshot_ids,
                        query_claim=query_claim,
                    )
                state.items[key] = ClaimRelationBatchItem(
                    query_claim=query_claim,
                    status="succeeded",
                    response=response,
                    error_code=None,
                )
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "Claim relation batch item attempt %s/%s failed with %s",
                    attempt,
                    _CLAIM_RELATION_BATCH_ATTEMPTS,
                    type(exc).__name__,
                )
                # A rejected handoff needs a fresh preview, which the next
                # attempt makes at the cost of one more planner call.
                if (
                    isinstance(exc, CompilerError)
                    and not exc.retryable
                    and not isinstance(exc, QueryPlanHandoffError)
                ):
                    break
                if attempt < _CLAIM_RELATION_BATCH_ATTEMPTS:
                    await asyncio.sleep(float(attempt))
        state.items[key] = ClaimRelationBatchItem(
            query_claim=query_claim,
            status="failed",
            response=None,
            error_code="CLAIM_RELATION_PROCESSING_FAILED",
        )

    try:
        await asyncio.gather(
            *(run_one(query_claim) for query_claim in state.request.claim_refs)
        )
    finally:
        _finish_cancelled_claim_relation_batch(state)


async def _cancel_claim_relation_batch(state: _ClaimRelationBatchState) -> None:
    state.cancelled = True
    task = state.task
    if task is not None and not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    _finish_cancelled_claim_relation_batch(state)


def _finish_cancelled_claim_relation_batch(
    state: _ClaimRelationBatchState,
) -> None:
    if len(state.items) < len(state.request.claim_refs):
        for query_claim in state.request.claim_refs:
            key = (query_claim.snapshot_id, query_claim.claim_id)
            if key not in state.items:
                state.items[key] = ClaimRelationBatchItem(
                    query_claim=query_claim,
                    status="failed",
                    response=None,
                    error_code="CLAIM_RELATION_PROCESSING_CANCELLED",
                )
    state.finished = True


def _start_job(
    app: FastAPI,
    job_id: str,
    store: JobStore,
    backend: MineruBackend,
    compiler: Any,
) -> None:
    task = asyncio.create_task(_run_job(app, job_id, store, backend, compiler))
    app.state.running_jobs.add(task)
    task.add_done_callback(app.state.running_jobs.discard)


async def _parse_job_document(
    app: FastAPI,
    job_id: str,
    store: JobStore,
    backend: MineruBackend,
) -> ParseResponse | None:
    """Run (or resume) the parsing stage; ``None`` means the job left it."""

    async with app.state.mineru_slots:
        # Cancellation may occur while this task waits for parser capacity.
        # Do not spend a parser turn on work that is already terminal.
        current = await asyncio.to_thread(store.get, job_id)
        if current.job.status == "queued":
            await asyncio.to_thread(store.transition, job_id, "parsing")
        elif current.job.status != "parsing":
            return None
        work = await asyncio.to_thread(store.work, job_id)
        # A restart may have happened after the DocumentIR was persisted but
        # before the stage transition; reuse it instead of re-parsing.
        parsed = await asyncio.to_thread(store.load_saved_document, job_id)
        if parsed is None:
            parsed = await _run_mineru_blocking(
                app.state.mineru_executor,
                _parse_file,
                backend,
                work.artifact,
                work.input_path,
            )
            current = await asyncio.to_thread(store.get, job_id)
            if current.job.status != "parsing":
                return None
            await asyncio.to_thread(store.save_document, job_id, parsed)
        if work.artifact.media_type == "application/pdf":
            data_quality_response, created = await asyncio.to_thread(
                app.state.data_quality_jobs.create_document, parsed
            )
            if created:
                quality_task = asyncio.create_task(
                    _run_data_quality_document(
                        app,
                        data_quality_response["job"]["id"],
                        app.state.data_quality_jobs,
                    )
                )
                app.state.running_jobs.add(quality_task)
                quality_task.add_done_callback(app.state.running_jobs.discard)
        await asyncio.to_thread(store.transition, job_id, "extracting")
    return parsed


async def _run_job(
    app: FastAPI,
    job_id: str,
    store: JobStore,
    backend: MineruBackend,
    compiler: Any,
) -> None:
    try:
        current = await asyncio.to_thread(store.get, job_id)
        if current.job.status in {"queued", "parsing"}:
            parsed = await _parse_job_document(app, job_id, store, backend)
            if parsed is None:
                return
        elif current.job.status in {"extracting", "validating"}:
            # Restart recovery: continue from the persisted DocumentIR.
            parsed = await asyncio.to_thread(store.load_saved_document, job_id)
            if parsed is None:
                raise ServiceRestarted(
                    "Compiler restarted and the saved document is unavailable"
                )
        else:
            return
        work = await asyncio.to_thread(store.work, job_id)

        def validating() -> None:
            store.require_active(job_id)
            # A job resumed in the validating stage is already there.
            if store.get(job_id).job.status == "extracting":
                store.transition(job_id, "validating")

        async with app.state.bulk_model_slots, app.state.gpt_slots:
            current = await asyncio.to_thread(store.get, job_id)
            if current.job.status not in {"extracting", "validating"}:
                return
            outcome = await _run_bulk_blocking(
                app,
                app.state.gpt_executor,
                compiler.compile,
                parsed.document,
                verify=work.verify,
                on_validating=validating,
                check_cancelled=partial(store.require_active, job_id),
            )
        await asyncio.to_thread(
            store.complete,
            job_id,
            outcome.envelope,
            outcome.citation_source,
            raw_graph=getattr(outcome, "raw_graph", None),
        )
    except JobNotFound:
        # A terminal job may be purged while an in-flight blocking call unwinds.
        return
    except Exception as exc:
        if isinstance(exc, CompilerError):
            logger.error(
                "Compile job %s failed with %s: %s",
                job_id,
                exc.code,
                exc,
            )
        else:
            logger.error(
                "Compile job %s failed with unexpected %s",
                job_id,
                type(exc).__name__,
            )
        try:
            await asyncio.to_thread(store.fail, job_id, exc)
        except JobNotFound:
            return


async def _run_data_quality_upload(
    app: FastAPI,
    job_id: str,
    store: DataQualityStore,
    backend: MineruBackend,
) -> None:
    try:
        await asyncio.to_thread(store.transition, job_id, "running", "parsing", 12)
        artifact, input_path = await asyncio.to_thread(store.input, job_id)
        async with app.state.mineru_slots:
            parsed = await _run_mineru_blocking(
                app.state.mineru_executor,
                _parse_file,
                backend,
                artifact,
                input_path,
            )
            await asyncio.to_thread(store.save_document, job_id, parsed)
        await _run_data_quality_document(app, job_id, store)
    except Exception as exc:
        logger.error("Data Quality job %s failed with %s", job_id, type(exc).__name__)
        await asyncio.to_thread(store.fail, job_id)


async def _run_data_quality_document(
    app: FastAPI,
    job_id: str,
    store: DataQualityStore,
) -> None:
    try:
        await asyncio.to_thread(store.transition, job_id, "running", "checking", 48)
        parsed = await asyncio.to_thread(store.load_document, job_id)
        report = await _run_blocking(
            app.state.data_quality_executor,
            inspect_document,
            parsed.document,
        )
        await asyncio.to_thread(store.complete, job_id, report)
    except Exception:
        logger.error("Data Quality job %s failed", job_id, exc_info=True)
        await asyncio.to_thread(store.fail, job_id)


async def _run_bulk_blocking(
    app: FastAPI,
    executor: ThreadPoolExecutor,
    function: Any,
    /,
    *args: Any,
    **kwargs: Any,
) -> Any:
    # Keep the slot until the real blocking operation ends, even if the HTTP
    # coroutine is cancelled. Cancellation cannot release a still-running call.
    def run() -> Any:
        with app.state.bulk_model_worker_slots:
            return function(*args, **kwargs)

    return await _run_blocking(executor, run)


async def _run_mineru_blocking(
    executor: ThreadPoolExecutor, function: Any, /, *args: Any, **kwargs: Any
) -> Any:
    """Retain the enclosing parser slot until its real thread has stopped."""
    worker = executor.submit(partial(function, *args, **kwargs))
    future = asyncio.wrap_future(worker)
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(future)
            break
        except asyncio.CancelledError:
            if worker.cancel():
                # No thread has started: discard queued work immediately.
                raise
            cancelled = True
            if future.done():
                break
        except Exception:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
    if cancelled:
        # Retrieve a possible late exception without admitting the late result.
        if future.done() and not future.cancelled():
            future.exception()
        raise asyncio.CancelledError
    return result


async def _run_blocking(
    executor: ThreadPoolExecutor, function: Any, /, *args: Any, **kwargs: Any
) -> Any:
    """Run one long blocking boundary on its dedicated capacity pool."""

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, partial(function, *args, **kwargs))


async def _trusted_upload(
    *,
    file: UploadFile,
    paper_id: str,
    artifact_id: str,
    version_id: str,
    artifact_sha256: str,
    media_type: str,
    locator: str,
) -> tuple[bytes, ArtifactContext]:
    name = sanitize_file_name(file.filename or "")
    normalized_media = media_type.split(";", 1)[0].strip().lower()
    _validate_media(name, normalized_media)
    if file.content_type and file.content_type != "application/octet-stream":
        supplied = file.content_type.split(";", 1)[0].strip().lower()
        if supplied != normalized_media:
            raise UnsupportedMediaType(
                "multipart file media type does not match trusted media_type"
            )
    content = await _read_bounded(file)
    _validate_signature(content, normalized_media)
    actual = hashlib.sha256(content).hexdigest()
    if not hmac.compare_digest(actual, artifact_sha256):
        raise ArtifactHashMismatch("uploaded bytes do not match artifact_sha256")
    try:
        artifact = ArtifactContext.model_validate(
            {
                "paper_id": paper_id,
                "artifact_id": artifact_id,
                "version_id": version_id,
                "artifact_sha256": artifact_sha256,
                "media_type": normalized_media,
                "locator": locator,
                "file_name": name,
            },
            strict=True,
        )
    except ValueError as exc:
        raise InputError(f"invalid trusted artifact context: {exc}") from exc
    return content, artifact


async def _read_bounded(file: UploadFile) -> bytes:
    # Starlette has already spooled multipart data to a seekable temporary file.
    # One bounded read avoids dozens of thread-pool hops and a list-plus-join copy.
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise InputError(f"uploads are limited to {MAX_UPLOAD_BYTES} bytes")
    if not content:
        raise InputError("uploaded artifact is empty")
    return content


def _validate_media(file_name: str, media_type: str) -> None:
    allowed = _SUPPORTED_MEDIA.get(media_type)
    if not allowed:
        raise UnsupportedMediaType(
            "Compiler accepts PDF, DOCX, XLSX, PNG, JPEG, or TIFF MinerU inputs"
        )
    extension = Path(file_name).suffix.lower().lstrip(".")
    if extension not in allowed:
        raise UnsupportedMediaType("file extension does not match media_type")


def _validate_signature(content: bytes, media_type: str) -> None:
    """Reject content whose trusted MIME label disagrees with its file signature."""

    if media_type == "application/pdf":
        valid = content.startswith(b"%PDF-")
    elif media_type == "image/png":
        valid = content.startswith(b"\x89PNG\r\n\x1a\n")
    elif media_type == "image/jpeg":
        valid = content.startswith(b"\xff\xd8\xff") and content.endswith(b"\xff\xd9")
    elif media_type == "image/tiff":
        valid = content.startswith((b"II*\x00", b"MM\x00*"))
    elif media_type in {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }:
        valid = _valid_ooxml(content, media_type)
    else:  # pragma: no cover - media allowlist and signature map stay in lockstep
        valid = False
    if not valid:
        raise UnsupportedMediaType(
            "uploaded bytes do not match the trusted media_type signature"
        )


def _valid_ooxml(content: bytes, media_type: str) -> bool:
    if not content.startswith(b"PK\x03\x04"):
        return False
    expected_part = (
        "xl/workbook.xml"
        if media_type
        == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else "word/document.xml"
    )
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = set(archive.namelist())
            return "[Content_Types].xml" in names and expected_part in names
    except (OSError, zipfile.BadZipFile):
        return False


def _direct_parse(
    settings: Settings,
    backend: MineruBackend,
    artifact: ArtifactContext,
    content: bytes,
) -> ParseResponse:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="direct-parse-", dir=settings.data_dir
    ) as raw:
        root = Path(raw)
        input_path = root / artifact.file_name
        input_path.write_bytes(content)
        return _parse_file(backend, artifact, input_path)


def _parse_file(
    backend: MineruBackend,
    artifact: ArtifactContext,
    input_path: Path,
) -> ParseResponse:
    # Hash the staged artifact without materializing another copy of a file that
    # may already be close to the upload limit. ``file_digest`` streams from the
    # descriptor and is available throughout the supported Python range.
    with input_path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if not hmac.compare_digest(digest, artifact.artifact_sha256):
        raise ArtifactHashMismatch("persisted input bytes no longer match provenance")
    output = backend.parse(input_path, method="auto")
    return normalize_mineru(output, artifact)


def _require_compiler(compiler: Any | None) -> Any:
    if compiler is None:
        from .errors import ModelUnavailable

        raise ModelUnavailable("GPT_API_KEY is not configured")
    return compiler


def _require_citation_analyzer(analyzer: Any | None) -> Any:
    if analyzer is None:
        from .errors import ModelUnavailable

        raise ModelUnavailable("Citation Reception requires GPT_API_KEY")
    return analyzer


def _require_claim_comparison_analyzer(analyzer: Any | None) -> Any:
    if analyzer is None:
        from .errors import ModelUnavailable

        raise ModelUnavailable("Claim comparison requires GPT_API_KEY")
    return analyzer


async def _cleanup_loop(store: JobStore) -> None:
    while True:
        await asyncio.sleep(600)
        try:
            await asyncio.to_thread(store.cleanup_expired)
        except asyncio.CancelledError:
            raise
        except Exception:
            # One failed sweep (for example a full disk) must not end cleanup
            # for the rest of the process lifetime; retry on the next tick.
            logger.exception("expired job cleanup failed")


def _json_model(
    model: Any, *, status_code: int = 200, exclude_none: bool = True
) -> JSONResponse:
    return JSONResponse(
        model.model_dump(mode="json", exclude_none=exclude_none),
        status_code=status_code,
    )


def _error_response(error: CompilerError) -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "code": error.code,
                "message": str(error),
                "retryable": error.retryable,
            }
        },
        status_code=error.status_code,
        headers={"WWW-Authenticate": "Bearer"} if error.status_code == 401 else None,
    )


def _retrieval_too_large_response() -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "code": "REQUEST_TOO_LARGE",
                "message": (
                    "retrieval requests are limited to "
                    f"{MAX_RETRIEVAL_BODY_BYTES} bytes"
                ),
                "retryable": False,
            }
        },
        status_code=413,
    )


__all__ = ["create_app"]
