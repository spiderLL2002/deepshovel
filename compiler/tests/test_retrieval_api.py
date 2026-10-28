from __future__ import annotations

from time import sleep
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import APIConnectionError, APITimeoutError
from test_retrieval_providers import _response, _sdk
from test_retrieval_service import (
    _HANDOFF_SECRET_A,
    _manifest,
    _search_request,
    _service,
)

import compiler_service.api as api_module
from compiler_service.api import create_app
from compiler_service.config import GPT_MAX_ATTEMPTS, Settings
from compiler_service.errors import ModelJobBudgetExceeded, QueryPlanHandoffError
from compiler_service.mineru import FakeMineruBackend
from compiler_service.model_client import ModelJsonClient
from compiler_service.retrieval_contract import (
    MAX_RETRIEVAL_BODY_BYTES,
    ChannelDiagnostics,
    HybridSearchResponse,
    ManifestCheckResponse,
    ManifestCheckStatus,
    QueryPlan,
    StageDiagnostic,
)
from compiler_service.retrieval_providers import (
    ModelQueryPlanner,
    QueryPlanningInputTooLarge,
)


class StubRetrieval:
    dense_revision = "stub-revision"
    model_readiness = {
        "dense": False,
        "vector_store": True,
    }

    def check_manifests(self, request):
        return ManifestCheckResponse(
            manifests=[
                ManifestCheckStatus(
                    snapshot_id=item.snapshot_id,
                    status="missing",
                    document_count=item.document_count,
                    indexed_document_count=0,
                    manifest_sha256=None,
                    dense_complete=False,
                )
                for item in request.manifests
            ]
        )

    def search_claim_relations(self, **kwargs):
        query_claim = kwargs["query_claim"]
        skipped = StageDiagnostic(
            attempted=False,
            status="skipped",
            provider="test",
            returned=0,
        )
        return HybridSearchResponse(
            stage="ai_rerank",
            result_status="no_direct_answer",
            query_plan=QueryPlan(
                query_type="claim_search",
                canonical_claim=query_claim.claim_id,
                must_terms=[],
                optional_terms=[],
                exclude_terms=[],
                entities=[],
                polarity=None,
                claim_types=[],
                scope_constraints=None,
                original_query=query_claim.claim_id,
                planner="deterministic",
            ),
            channels=ChannelDiagnostics(
                lexical=skipped,
                dense=skipped,
                rerank=skipped,
                relation=skipped,
            ),
            preview=[],
            clusters=[],
            warnings=[],
        )


class FlakyStubRetrieval(StubRetrieval):
    def __init__(self, error: Exception | None = None) -> None:
        self.attempts: dict[str, int] = {}
        self.error = error or RuntimeError("transient failure")

    def search_claim_relations(self, **kwargs):
        claim_id = kwargs["query_claim"].claim_id
        self.attempts[claim_id] = self.attempts.get(claim_id, 0) + 1
        if self.attempts[claim_id] == 1:
            raise self.error
        return super().search_claim_relations(**kwargs)


def _settings(tmp_path, *, secret: str | None) -> Settings:
    return Settings(data_dir=tmp_path / "compiler", shared_secret=secret)


def _check_payload() -> dict:
    return {
        "schema_version": "evidence-retrieval/2.0",
        "project_id": "project-1",
        "manifests": [_manifest(created_at="2026-08-18T10:00Z")],
    }


def test_http_responses_close_connections_including_boundary_failures(
    tmp_path, mineru_output, import_envelope
) -> None:
    secret = "retrieval-test-secret-0123456789abcdef"
    app = create_app(
        settings=_settings(tmp_path, secret=secret),
        mineru_backend=FakeMineruBackend(mineru_output),
        evidence_compiler=SimpleNamespace(envelope=import_envelope),
        retrieval_service=StubRetrieval(),
    )
    auth = {"Authorization": f"Bearer {secret}"}
    with TestClient(app) as client:
        responses = [
            (client.get("/ready"), 200),
            (client.post("/v1/retrieval/index", json={}), 401),
            (client.post("/v1/retrieval/index", json={}, headers=auth), 400),
            (
                client.post(
                    "/v1/retrieval/index",
                    content=b"{}",
                    headers={
                        **auth,
                        "Content-Length": str(MAX_RETRIEVAL_BODY_BYTES + 1),
                    },
                ),
                413,
            ),
        ]
    for response, status in responses:
        assert response.status_code == status
        assert response.headers.get_list("connection") == ["close"]


def test_invalid_clarification_token_has_safe_409_http_mapping(
    tmp_path,
    mineru_output,
    import_envelope,
) -> None:
    secret = "retrieval-test-secret-0123456789abcdef"
    service = _service(
        tmp_path / "retrieval", query_plan_handoff_secret=_HANDOFF_SECRET_A
    )
    app = create_app(
        settings=_settings(tmp_path, secret=secret),
        mineru_backend=FakeMineruBackend(mineru_output),
        evidence_compiler=SimpleNamespace(envelope=import_envelope),
        retrieval_service=service,
    )
    payload = _search_request(
        "private original request",
        schema_version="evidence-retrieval/2.3",
        stage="preview",
    ).model_dump(mode="json")
    payload["clarification_selection"] = {
        "clarification_token": "private-invalid-token",
        "option_id": "A",
    }
    with TestClient(app) as client:
        response = client.post(
            "/v1/retrieval/search",
            json=payload,
            headers={"Authorization": f"Bearer {secret}"},
        )
    assert response.status_code == 409
    assert "CLARIFICATION_REPREVIEW_REQUIRED" in response.text
    assert "private original request" not in response.text
    assert "private-invalid-token" not in response.text


@pytest.mark.parametrize(
    "kind,status",
    [
        ("OUTPUT_INVALID", 502),
        ("TIMEOUT", 504),
        ("UPSTREAM_FAILED", 502),
        ("UNAVAILABLE", 503),
        ("PROVIDER_INVALID", 502),
        ("INPUT_TOO_LARGE", 413),
        ("BUDGET_EXCEEDED", 503),
        ("INTERNAL_ERROR", 500),
    ],
)
def test_retrieval_planning_errors_are_safe_http_codes(
    tmp_path,
    mineru_output,
    import_envelope,
    monkeypatch,
    kind,
    status,
):
    private = "private-query-token-model-content-do-not-expose"
    sdk_request = httpx.Request("POST", "https://invalid.example")
    create = Mock()
    if kind == "OUTPUT_INVALID":
        create.return_value = _response('{"private":"' + private + '"}')
    elif kind == "TIMEOUT":
        create.side_effect = APITimeoutError(request=sdk_request)
    elif kind == "UPSTREAM_FAILED":
        create.side_effect = APIConnectionError(request=sdk_request, message=private)
    elif kind == "INTERNAL_ERROR":
        create.side_effect = RuntimeError(private)
    planner = ModelQueryPlanner(ModelJsonClient(_sdk(create), sleep=lambda _: None))
    if kind == "UNAVAILABLE":
        planner = None
    elif kind not in ("OUTPUT_INVALID", "TIMEOUT", "UPSTREAM_FAILED", "INTERNAL_ERROR"):
        errors = {
            "INPUT_TOO_LARGE": QueryPlanningInputTooLarge(private),
            "BUDGET_EXCEEDED": ModelJobBudgetExceeded(private),
        }

        def fail(request, **_context):
            if kind == "PROVIDER_INVALID":
                return {"private": private}
            raise errors[kind]

        planner = SimpleNamespace(plan_context=fail, plan_interactive_context=fail)
    service = _service(tmp_path / "retrieval", planner=planner)

    def no_state(*args, **kwargs):
        raise AssertionError("failed planning must precede retrieval state")

    monkeypatch.setattr(service, "_query_document", no_state)
    monkeypatch.setattr(service, "_resolve_snapshots", no_state)
    secret = "retrieval-test-secret-0123456789abcdef"
    app = create_app(
        settings=_settings(tmp_path, secret=secret),
        mineru_backend=FakeMineruBackend(mineru_output),
        evidence_compiler=SimpleNamespace(envelope=import_envelope),
        retrieval_service=service,
    )
    request = _search_request(
        private, schema_version="evidence-retrieval/2.3", stage="preview"
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/retrieval/search",
            json=request.model_dump(mode="json"),
            headers={"Authorization": f"Bearer {secret}"},
        )
    assert response.status_code == status
    assert "RETRIEVAL_PLANNING_" + kind in response.text
    assert private not in response.text and "diagnostics" not in response.text
    assert '"retryable":false' in response.text
    expected_attempts = {
        "OUTPUT_INVALID": GPT_MAX_ATTEMPTS,
        "TIMEOUT": GPT_MAX_ATTEMPTS,
        "UPSTREAM_FAILED": GPT_MAX_ATTEMPTS,
        "INTERNAL_ERROR": 1,
    }.get(kind, 0)
    assert create.call_count == expected_attempts
    assert all(call.kwargs["timeout"] is None for call in create.call_args_list)


def test_retrieval_requires_secret_even_in_development(
    tmp_path, mineru_output, import_envelope
) -> None:
    app = create_app(
        settings=_settings(tmp_path, secret=None),
        mineru_backend=FakeMineruBackend(mineru_output),
        evidence_compiler=SimpleNamespace(envelope=import_envelope),
        retrieval_service=StubRetrieval(),
    )
    with TestClient(app) as client:
        response = client.post("/v1/retrieval/manifests:check", json=_check_payload())
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_retrieval_body_is_replayed_after_actual_size_check(
    tmp_path, mineru_output, import_envelope
) -> None:
    secret = "retrieval-test-secret-0123456789abcdef"
    app = create_app(
        settings=_settings(tmp_path, secret=secret),
        mineru_backend=FakeMineruBackend(mineru_output),
        evidence_compiler=SimpleNamespace(envelope=import_envelope),
        retrieval_service=StubRetrieval(),
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/retrieval/manifests:check",
            json=_check_payload(),
            headers={"Authorization": f"Bearer {secret}"},
        )
    assert response.status_code == 200
    assert response.json()["manifests"][0]["status"] == "missing"


def test_chunked_body_without_content_length_is_still_bounded(
    tmp_path, mineru_output, import_envelope
) -> None:
    secret = "retrieval-test-secret-0123456789abcdef"
    app = create_app(
        settings=_settings(tmp_path, secret=secret),
        mineru_backend=FakeMineruBackend(mineru_output),
        evidence_compiler=SimpleNamespace(envelope=import_envelope),
        retrieval_service=StubRetrieval(),
    )

    def chunks():
        for _ in range(MAX_RETRIEVAL_BODY_BYTES // (1024 * 1024) + 1):
            yield b"x" * (1024 * 1024)

    with TestClient(app) as client:
        response = client.post(
            "/v1/retrieval/manifests:check",
            content=chunks(),
            headers={
                "Authorization": f"Bearer {secret}",
                "Content-Type": "application/json",
            },
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"


def test_health_reports_cold_state_without_loading_models(
    monkeypatch, tmp_path, mineru_output, import_envelope
) -> None:
    def fail_if_loaded(*args, **kwargs):
        del args, kwargs
        raise AssertionError("health must not initialize retrieval models")

    monkeypatch.setattr(api_module, "FastEmbedDenseEncoder", fail_if_loaded)
    app = create_app(
        settings=_settings(tmp_path, secret=None),
        mineru_backend=FakeMineruBackend(mineru_output),
        evidence_compiler=SimpleNamespace(envelope=import_envelope),
    )
    with TestClient(app) as client:
        health = client.get("/health")
    assert health.status_code == 200
    retrieval = health.json()["retrieval"]
    assert retrieval["status"] == "uninitialized"
    assert retrieval["models_ready"] == {
        "dense": False,
        "vector_store": False,
    }
    assert retrieval["warmup_command"] == (
        "python -m compiler_service.retrieval_warmup"
    )


def test_claim_relation_batch_returns_immediately_and_can_be_polled(
    tmp_path, mineru_output, import_envelope
) -> None:
    secret = "retrieval-test-secret-0123456789abcdef"
    app = create_app(
        settings=_settings(tmp_path, secret=secret),
        mineru_backend=FakeMineruBackend(mineru_output),
        evidence_compiler=SimpleNamespace(envelope=import_envelope),
        retrieval_service=StubRetrieval(),
    )
    payload = {
        "schema_version": "evidence-retrieval/2.0",
        "project_id": "project-1",
        "client_request_id": "batch-request-1",
        "active_snapshot_ids": ["snapshot-1"],
        "claim_refs": [
            {"snapshot_id": "snapshot-1", "claim_id": f"claim-{index}"}
            for index in range(1, 65)
        ],
    }
    headers = {"Authorization": f"Bearer {secret}"}
    with TestClient(app) as client:
        started = client.post(
            "/v1/retrieval/claim-relations:batch",
            json=payload,
            headers=headers,
        )
        assert started.status_code == 202
        batch_id = started.json()["batch_id"]
        for _ in range(300):
            progress = client.get(
                f"/v1/retrieval/claim-relations:batch/{batch_id}",
                headers=headers,
            )
            if progress.json()["status"] == "completed":
                break
            sleep(0.01)
    assert progress.status_code == 200
    assert progress.json()["total"] == 64
    assert progress.json()["completed"] == 64
    assert progress.json()["succeeded"] == 64
    assert progress.json()["failed"] == 0
    assert progress.json()["reprocess"] is False


def test_claim_relation_batch_can_be_cancelled_before_queued_work_starts(
    tmp_path, mineru_output, import_envelope
) -> None:
    secret = "retrieval-test-secret-0123456789abcdef"
    app = create_app(
        settings=_settings(tmp_path, secret=secret),
        mineru_backend=FakeMineruBackend(mineru_output),
        evidence_compiler=SimpleNamespace(envelope=import_envelope),
        retrieval_service=StubRetrieval(),
    )
    payload = {
        "schema_version": "evidence-retrieval/2.0",
        "project_id": "project-1",
        "client_request_id": "cancel-batch-request-1",
        "active_snapshot_ids": ["snapshot-1"],
        "claim_refs": [
            {"snapshot_id": "snapshot-1", "claim_id": "claim-1"},
            {"snapshot_id": "snapshot-1", "claim_id": "claim-2"},
        ],
        "reprocess": True,
    }
    headers = {"Authorization": f"Bearer {secret}"}
    with TestClient(app) as client:
        started = client.post(
            "/v1/retrieval/claim-relations:batch", json=payload, headers=headers
        )
        cancelled = client.delete(
            f"/v1/retrieval/claim-relations:batch/{started.json()['batch_id']}",
            headers=headers,
        )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["reprocess"] is True
    assert cancelled.json()["completed"] == 2
    assert cancelled.json()["failed"] == 2
    with TestClient(app) as client:
        restarted = client.post(
            "/v1/retrieval/claim-relations:batch", json=payload, headers=headers
        )
        assert restarted.status_code == 202
        assert restarted.json()["status"] == "running"


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("transient failure"),
        # Rejected between its own preview and final: a fresh preview suffices.
        QueryPlanHandoffError("query plan handoff is invalid or expired"),
    ],
)
def test_claim_relation_batch_retries_each_transient_item_once(
    tmp_path, mineru_output, import_envelope, error
) -> None:
    secret = "retrieval-test-secret-0123456789abcdef"
    retrieval = FlakyStubRetrieval(error)
    app = create_app(
        settings=_settings(tmp_path, secret=secret),
        mineru_backend=FakeMineruBackend(mineru_output),
        evidence_compiler=SimpleNamespace(envelope=import_envelope),
        retrieval_service=retrieval,
    )
    payload = {
        "schema_version": "evidence-retrieval/2.0",
        "project_id": "project-1",
        "client_request_id": "retry-batch-request-1",
        "active_snapshot_ids": ["snapshot-1"],
        "claim_refs": [{"snapshot_id": "snapshot-1", "claim_id": "claim-1"}],
    }
    headers = {"Authorization": f"Bearer {secret}"}
    with TestClient(app) as client:
        started = client.post(
            "/v1/retrieval/claim-relations:batch", json=payload, headers=headers
        )
        batch_id = started.json()["batch_id"]
        for _ in range(400):
            progress = client.get(
                f"/v1/retrieval/claim-relations:batch/{batch_id}",
                headers=headers,
            )
            if progress.json()["status"] == "completed":
                break
            sleep(0.01)
    assert progress.json()["succeeded"] == 1
    assert progress.json()["failed"] == 0
    assert retrieval.attempts == {"claim-1": 2}


def test_snapshot_deletion_retires_only_batches_using_that_project_corpus(
    tmp_path, mineru_output, import_envelope
) -> None:
    from compiler_service.retrieval_contract import DeleteSnapshotsResponse

    secret = "retrieval-test-secret-0123456789abcdef"
    retrieval = StubRetrieval()
    retrieval.search_claim_relations = Mock(wraps=retrieval.search_claim_relations)
    retrieval.delete_snapshots = Mock(
        return_value=DeleteSnapshotsResponse(
            deleted_snapshots=1, deleted_documents=1, warnings=[]
        )
    )
    app = create_app(
        settings=_settings(tmp_path, secret=secret),
        mineru_backend=FakeMineruBackend(mineru_output),
        evidence_compiler=SimpleNamespace(envelope=import_envelope),
        retrieval_service=retrieval,
    )
    headers = {"Authorization": f"Bearer {secret}"}
    with TestClient(app) as client:
        batches = []
        for project, snapshots in [
            ("project-1", ["snapshot-1", "snapshot-2"]),
            ("project-1", ["snapshot-2"]),
            ("project-2", ["snapshot-1"]),
            ("project-1", None),
            ("project-2", None),
        ]:
            started = client.post(
                "/v1/retrieval/claim-relations:batch",
                headers=headers,
                json={
                    "schema_version": "evidence-retrieval/2.0",
                    "project_id": project,
                    "client_request_id": f"delete-test-{len(batches)}",
                    "active_snapshot_ids": snapshots,
                    "claim_refs": [
                        {
                            "snapshot_id": snapshots[-1] if snapshots else "snapshot-2",
                            "claim_id": "claim-1",
                        }
                    ],
                },
            )
            assert started.status_code == 202
            batches.append(started.json()["batch_id"])
        deleted = client.post(
            "/v1/retrieval/snapshots:delete",
            headers=headers,
            json={
                "schema_version": "evidence-retrieval/2.0",
                "index_version": "evidence-retrieval/2.0",
                "project_id": "project-1",
                "snapshot_ids": ["snapshot-1"],
            },
        )
        assert deleted.status_code == 200
        statuses = [
            client.get(
                f"/v1/retrieval/claim-relations:batch/{batch_id}", headers=headers
            ).json()["status"]
            for batch_id in batches
        ]
        assert statuses == ["cancelled", "running", "running", "cancelled", "running"]
        assert retrieval.search_claim_relations.call_count == 0
        retrieval.delete_snapshots.assert_called_once()


def test_interactive_policy_requires_auth_and_exact_header_and_excludes_batches(
    tmp_path, mineru_output, import_envelope
):
    secret = "retrieval-test-secret-0123456789abcdef"
    search_options = []
    batch_options = []

    class RecordingRetrieval(StubRetrieval):
        def search(self, request, **options):
            search_options.append(options)
            response = super().search_claim_relations(
                query_claim=SimpleNamespace(claim_id=request.query)
            )
            return response.model_copy(update={"stage": request.stage})

        def search_claim_relations(self, **options):
            batch_options.append(options)
            return super().search_claim_relations(**options)

    app = create_app(
        settings=_settings(tmp_path, secret=secret),
        mineru_backend=FakeMineruBackend(mineru_output),
        evidence_compiler=SimpleNamespace(envelope=import_envelope),
        retrieval_service=RecordingRetrieval(),
    )
    payload = _search_request("fixture query").model_dump(mode="json")
    authorization = {"Authorization": f"Bearer {secret}"}
    interactive = {**authorization, "x-deepshovel-interactive": "1"}
    with TestClient(app) as client:
        for headers in (
            {"x-deepshovel-interactive": "1"},
            {"Authorization": "Bearer wrong", "x-deepshovel-interactive": "1"},
        ):
            response = client.post(
                "/v1/retrieval/search", json=payload, headers=headers
            )
            assert response.status_code == 401
        assert search_options == []
        for headers in (
            authorization,
            {**authorization, "x-deepshovel-interactive": "true"},
            interactive,
        ):
            response = client.post(
                "/v1/retrieval/search", json=payload, headers=headers
            )
            assert response.status_code == 200
        assert search_options == [{}, {}, {"interactive": True}]

        started = client.post(
            "/v1/retrieval/claim-relations:batch",
            headers=interactive,
            json={
                "schema_version": "evidence-retrieval/2.0",
                "project_id": "project-1",
                "client_request_id": "policy-isolation-batch",
                "active_snapshot_ids": ["snapshot-1"],
                "claim_refs": [{"snapshot_id": "snapshot-1", "claim_id": "claim-1"}],
            },
        )
        assert started.status_code == 202
        for _ in range(100):
            progress = client.get(
                "/v1/retrieval/claim-relations:batch/" + started.json()["batch_id"],
                headers=authorization,
            )
            if progress.json()["status"] == "completed":
                break
            sleep(0.01)
        assert progress.json()["succeeded"] == 1
        assert len(batch_options) == 1
        assert "interactive" not in batch_options[0]
        assert search_options == [{}, {}, {"interactive": True}]


def test_batch_cursor_pages_follow_completion_order_and_can_replay():
    from compiler_service.api import (
        _claim_relation_batch_response,
        _ClaimRelationBatchState,
    )
    from compiler_service.errors import InputError
    from compiler_service.retrieval_contract import (
        ClaimRelationBatchItem,
        ClaimRelationBatchRequest,
    )

    request = ClaimRelationBatchRequest.model_validate(
        {
            "schema_version": "evidence-retrieval/2.0",
            "project_id": "project-1",
            "client_request_id": "cursor-test",
            "active_snapshot_ids": None,
            "claim_refs": [
                {"snapshot_id": "snapshot-1", "claim_id": f"claim-{i}"}
                for i in range(35)
            ],
        }
    )
    state = _ClaimRelationBatchState(
        request=request,
        batch_id="batch-page",
        order={
            (ref.snapshot_id, ref.claim_id): index
            for index, ref in enumerate(request.claim_refs)
        },
    )
    for ref in reversed(request.claim_refs):
        state.items[(ref.snapshot_id, ref.claim_id)] = ClaimRelationBatchItem(
            query_claim=ref, status="failed", response=None, error_code="TEST_FAILURE"
        )
    first = _claim_relation_batch_response(state, 0)
    assert first == _claim_relation_batch_response(state, 0)
    assert first.completed == 35 and first.next_cursor == 16
    assert first.items[0].query_claim.claim_id == "claim-34"
    second = _claim_relation_batch_response(state, 16)
    third = _claim_relation_batch_response(state, second.next_cursor)
    assert [
        item.query_claim.claim_id for item in first.items + second.items + third.items
    ] == [f"claim-{i}" for i in reversed(range(35))]
    assert third.next_cursor == 35
    assert _claim_relation_batch_response(state, 35).items == []
    legacy = _claim_relation_batch_response(state)
    assert legacy.items[0].query_claim.claim_id == "claim-0"
    assert "next_cursor" not in legacy.model_dump()
    with pytest.raises(InputError):
        _claim_relation_batch_response(state, 36)
    state.cancelled = True
    assert len(_claim_relation_batch_response(state, 0).items) == 16
    state.retired = True
    retired = _claim_relation_batch_response(state, 0)
    assert retired.items == [] and retired.next_cursor == 35 and retired.retired


def test_batch_null_corpus_still_requires_auth_and_passes_project(
    tmp_path, mineru_output, import_envelope
):
    secret = "retrieval-test-secret-0123456789abcdef"
    retrieval = StubRetrieval()
    retrieval.search_claim_relations = Mock(wraps=retrieval.search_claim_relations)
    app = create_app(
        settings=_settings(tmp_path, secret=secret),
        mineru_backend=FakeMineruBackend(mineru_output),
        evidence_compiler=SimpleNamespace(envelope=import_envelope),
        retrieval_service=retrieval,
    )
    body = {
        "schema_version": "evidence-retrieval/2.0",
        "project_id": "project-1",
        "client_request_id": "null-corpus",
        "active_snapshot_ids": None,
        "claim_refs": [{"snapshot_id": "snapshot-5000", "claim_id": "claim-1"}],
    }
    headers = {"Authorization": f"Bearer {secret}"}
    with TestClient(app) as client:
        assert (
            client.post(
                "/v1/retrieval/claim-relations:batch?after=0", json=body
            ).status_code
            == 401
        )
        started = client.post(
            "/v1/retrieval/claim-relations:batch?after=0", json=body, headers=headers
        )
        assert started.status_code == 202
        url = "/v1/retrieval/claim-relations:batch/" + started.json()["batch_id"]
        for _ in range(300):
            response = client.get(url + "?after=0", headers=headers)
            if response.json()["status"] == "completed":
                break
            sleep(0.01)
        assert response.json()["next_cursor"] == 1
        assert response.json() == client.get(url + "?after=0", headers=headers).json()
        assert client.get(url + "?after=1", headers=headers).json()["items"] == []
    assert (
        retrieval.search_claim_relations.call_args.kwargs["project_id"] == "project-1"
    )
    assert (
        retrieval.search_claim_relations.call_args.kwargs["active_snapshot_ids"] is None
    )
