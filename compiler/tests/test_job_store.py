from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from compiler_service.citation_source import CitationSourceProjection
from compiler_service.errors import (
    IdempotencyKeyReused,
    InputError,
    JobCancelled,
    JobNotComplete,
    JobNotFound,
    JobNotTerminal,
    MineruRejected,
    SubmissionExpired,
)
from compiler_service.graph_contract import PaperEvidenceGraph
from compiler_service.job_store import (
    MAX_JOB_LOOKUP_IDS,
    MAX_JOB_RECOVERIES,
    JobStore,
    RecoveryReport,
)
from compiler_service.models import CompilerAuditEntry, CompilerAuditRecord
from compiler_service.normalize import normalize_mineru


def _advance_to_validating(store: JobStore, job_id: str) -> None:
    store.transition(job_id, "parsing")
    store.transition(job_id, "extracting")
    store.transition(job_id, "validating")


def _citation_source(envelope) -> CitationSourceProjection:
    return CitationSourceProjection(
        artifact_version_id=envelope.root_artifact_version_id,
        references=[],
        mentions=[],
        warnings=[],
    )


def test_store_recovers_restart_and_result_is_atomic(
    tmp_path, artifact, import_envelope
) -> None:
    store = JobStore(tmp_path / "state")
    pending = store.create(artifact=artifact, verify=False, content=b"%PDF-1.4\n")
    store.transition(pending.job.id, "parsing")
    before = store.get(pending.job.id).job

    restarted = JobStore(tmp_path / "state")
    report = restarted.recover_incomplete()
    assert report == RecoveryReport(resumed=(pending.job.id,), failed=())
    resumed = restarted.get(pending.job.id).job
    # Recovery never moves a job backwards; the saved input is re-parsed.
    assert resumed.status == "parsing"
    assert resumed.error is None
    assert resumed.created_at == before.created_at
    assert resumed.updated_at > before.updated_at

    completed = restarted.create(
        artifact=artifact, verify=False, content=b"%PDF-1.4\n"
    )
    _advance_to_validating(restarted, completed.job.id)
    restarted.complete(
        completed.job.id, import_envelope, _citation_source(import_envelope)
    )
    result = restarted.result(completed.job.id)
    assert result == import_envelope
    job_dir = restarted.work(completed.job.id).input_path.parent
    result_path = job_dir / "result.json"
    assert json.loads(result_path.read_text(encoding="utf-8"))["graph"]
    assert list(job_dir.glob(".result.json.*.tmp")) == []


def test_restart_recovery_resumes_from_saved_progress_with_a_bound(
    tmp_path, artifact, mineru_output
) -> None:
    root = tmp_path / "state"
    store = JobStore(root)
    queued = store.create(artifact=artifact, verify=False, content=b"%PDF-1.4\n")
    extracting = store.create(artifact=artifact, verify=True, content=b"%PDF-1.4\n")
    store.transition(extracting.job.id, "parsing")
    parsed = normalize_mineru(mineru_output, artifact)
    store.save_document(extracting.job.id, parsed)
    store.transition(extracting.job.id, "extracting")
    lost = store.create(artifact=artifact, verify=True, content=b"%PDF-1.4\n")
    _advance_to_validating(store, lost.job.id)

    first = JobStore(root).recover_incomplete()
    assert first.resumed == (queued.job.id, extracting.job.id)
    # A validating job without its saved DocumentIR cannot resume.
    assert first.failed == (lost.job.id,)
    failed = store.get(lost.job.id).job
    assert failed.status == "failed"
    assert failed.error is not None
    assert failed.error.code == "SERVICE_RESTARTED"
    assert failed.error.retryable is True
    assert failed.stage_timings_ms.validating is not None
    assert failed.stage_timings_ms.total is not None

    assert store.get(queued.job.id).job.status == "queued"
    assert store.get(extracting.job.id).job.status == "extracting"
    saved = store.load_saved_document(extracting.job.id)
    assert saved is not None
    assert saved.document.artifact == artifact
    assert store.load_saved_document(queued.job.id) is None

    # Queued jobs never consume the recovery budget; interrupted stages do.
    for _ in range(MAX_JOB_RECOVERIES - 1):
        again = JobStore(root).recover_incomplete()
        assert again.resumed == (queued.job.id, extracting.job.id)
    exhausted = JobStore(root).recover_incomplete()
    assert exhausted == RecoveryReport(
        resumed=(queued.job.id,), failed=(extracting.job.id,)
    )
    assert store.get(extracting.job.id).job.error.code == "SERVICE_RESTARTED"
    assert store.get(queued.job.id).job.status == "queued"


def test_restart_recovery_fails_queued_jobs_whose_input_is_gone(
    tmp_path, artifact
) -> None:
    store = JobStore(tmp_path / "state")
    queued = store.create(artifact=artifact, verify=False, content=b"%PDF-1.4\n")
    store.work(queued.job.id).input_path.unlink()

    report = JobStore(tmp_path / "state").recover_incomplete()

    assert report == RecoveryReport(resumed=(), failed=(queued.job.id,))
    failed = store.get(queued.job.id).job
    assert failed.status == "failed"
    assert failed.error.code == "SERVICE_RESTARTED"


def test_idempotent_create_returns_the_original_job_without_a_second_run(
    tmp_path, artifact
) -> None:
    store = JobStore(tmp_path / "state")
    key = "4d63b26c-6548-4a62-8218-14aa13580cb2"
    first, created = store.create_idempotent(
        artifact=artifact, verify=True, content=b"%PDF-1.4\n", idempotency_key=key
    )
    assert created is True
    replay, replay_created = store.create_idempotent(
        artifact=artifact, verify=True, content=b"%PDF-1.4\n", idempotency_key=key
    )
    assert replay_created is False
    assert replay == first
    assert store.find_by_idempotency_key(key) == first
    assert len([path for path in store.jobs_root.iterdir() if path.is_dir()]) == 1

    with pytest.raises(IdempotencyKeyReused):
        store.create_idempotent(
            artifact=artifact,
            verify=False,
            content=b"%PDF-1.4\n",
            idempotency_key=key,
        )
    with pytest.raises(JobNotFound):
        store.find_by_idempotency_key("4d63b26c-0000-4a62-8218-14aa13580cb2")
    with pytest.raises(InputError):
        store.find_by_idempotency_key("short")


def test_submission_deadline_prevents_late_job_creation(tmp_path, artifact) -> None:
    store = JobStore(tmp_path / "state")
    key = "8d63b26c-6548-4a62-8218-14aa13580cb2"
    with pytest.raises(SubmissionExpired):
        store.create_idempotent(
            artifact=artifact,
            verify=True,
            content=b"%PDF-1.4\n",
            idempotency_key=key,
            not_after_ms=1,
        )
    # Nothing is recorded, so a later lookup proves no job will ever exist.
    with pytest.raises(JobNotFound):
        store.find_by_idempotency_key(key)
    assert [path for path in store.jobs_root.iterdir() if path.is_dir()] == []


def test_batched_job_lookup_omits_unknown_ids_and_bounds_requests(
    tmp_path, artifact
) -> None:
    store = JobStore(tmp_path / "state")
    first = store.create(artifact=artifact, verify=False, content=b"%PDF-1.4\n")
    second = store.create(artifact=artifact, verify=False, content=b"%PDF-1.4\n")
    unknown = str(uuid.uuid4())

    found = store.get_many([second.job.id, unknown, first.job.id, second.job.id])

    assert [item.job.id for item in found] == [second.job.id, first.job.id]
    with pytest.raises(InputError):
        store.get_many([])
    with pytest.raises(InputError):
        store.get_many(["not-a-uuid"])
    with pytest.raises(InputError):
        store.get_many([str(uuid.uuid4()) for _ in range(MAX_JOB_LOOKUP_IDS + 1)])


def test_existing_databases_gain_recovery_and_key_columns(tmp_path, artifact) -> None:
    root = tmp_path / "state"
    root.mkdir()
    with sqlite3.connect(root / "jobs.sqlite3") as db:
        db.execute(
            """CREATE TABLE compile_jobs (
                id TEXT PRIMARY KEY, status TEXT NOT NULL,
                artifact_json TEXT NOT NULL, verify INTEGER NOT NULL,
                input_path TEXT NOT NULL, result_path TEXT,
                created_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL,
                expires_ms INTEGER NOT NULL, stage_started_ms INTEGER NOT NULL,
                timings_json TEXT NOT NULL, error_json TEXT)"""
        )
    store = JobStore(root)
    with sqlite3.connect(root / "jobs.sqlite3") as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(compile_jobs)")}
    assert {"idempotency_key", "recovery_count"} <= columns
    created, was_created = store.create_idempotent(
        artifact=artifact,
        verify=False,
        content=b"%PDF-1.4\n",
        idempotency_key="legacy-upgrade-key-0001",
    )
    assert was_created is True
    assert store.get(created.job.id).job.status == "queued"


def test_store_rejects_a_citation_source_for_another_artifact_version(
    tmp_path, artifact, import_envelope
) -> None:
    store = JobStore(tmp_path / "state")
    created = store.create(artifact=artifact, verify=False, content=b"%PDF-1.4\n")
    _advance_to_validating(store, created.job.id)
    source = CitationSourceProjection(
        artifact_version_id="artifact-version:sha256:" + "f" * 64,
        references=[],
        mentions=[],
        warnings=[],
    )

    with pytest.raises(ValueError, match="artifact version does not match"):
        store.complete(created.job.id, import_envelope, source)

    assert store.get(created.job.id).job.status == "validating"
    job_dir = store.work(created.job.id).input_path.parent
    assert not (job_dir / "result.json").exists()
    assert not (job_dir / "citation-source.json").exists()


def test_store_persists_final_raw_graph_only_as_private_job_file(
    tmp_path, artifact, import_envelope, raw_graph
) -> None:
    store = JobStore(tmp_path / "state")
    created = store.create(artifact=artifact, verify=True, content=b"%PDF-1.4\n")
    _advance_to_validating(store, created.job.id)

    completed = store.complete(
        created.job.id,
        import_envelope,
        _citation_source(import_envelope),
        raw_graph=raw_graph,
    )
    job_dir = store.work(created.job.id).input_path.parent
    raw_path = job_dir / "raw-graph.json"

    assert completed.job.status == "completed"
    assert raw_path.stat().st_mode & 0o777 == 0o600
    assert PaperEvidenceGraph.model_validate_json(raw_path.read_bytes()) == raw_graph
    assert "raw_graph" not in store.result(created.job.id).model_dump(mode="json")
    assert list(job_dir.glob(".raw-graph.json.*.tmp")) == []

    restarted = JobStore(tmp_path / "state")
    assert restarted.get(created.job.id).job.status == "completed"
    assert raw_path.is_file()
    restarted.delete_terminal(created.job.id)
    assert not job_dir.exists()


def test_store_removes_all_outputs_when_private_raw_graph_write_fails(
    monkeypatch, tmp_path, artifact, import_envelope, raw_graph
) -> None:
    from compiler_service import job_store

    store = JobStore(tmp_path / "state")
    created = store.create(artifact=artifact, verify=True, content=b"%PDF-1.4\n")
    _advance_to_validating(store, created.job.id)
    original_write = job_store._atomic_write

    def fail_private_write(path, content, *, private=False) -> None:
        if private:
            raise OSError("private graph persistence failed")
        original_write(path, content)

    monkeypatch.setattr(job_store, "_atomic_write", fail_private_write)
    with pytest.raises(OSError, match="private graph persistence failed"):
        store.complete(
            created.job.id,
            import_envelope,
            _citation_source(import_envelope),
            raw_graph=raw_graph,
        )

    job_dir = store.work(created.job.id).input_path.parent
    assert store.get(created.job.id).job.status == "validating"
    assert not (job_dir / "result.json").exists()
    assert not (job_dir / "citation-source.json").exists()
    assert not (job_dir / "raw-graph.json").exists()


def test_store_result_conflict_and_ttl_cleanup(
    tmp_path, artifact, import_envelope
) -> None:
    del import_envelope
    store = JobStore(tmp_path / "state", ttl_seconds=1)
    response = store.create(
        artifact=artifact, verify=False, content=b"%PDF-1.4\n"
    )
    job_id = response.job.id
    with pytest.raises(JobNotComplete):
        store.result(job_id)

    job_dir = store.work(job_id).input_path.parent
    assert job_dir.is_dir()
    assert store.cleanup_expired(now_ms=10**16) == 0
    store.fail(job_id, RuntimeError("expired test job"))
    assert store.cleanup_expired(now_ms=10**16) == 1
    assert not job_dir.exists()
    with pytest.raises(JobNotFound):
        store.get(job_id)


def test_store_deletes_only_terminal_job_and_private_files(
    tmp_path, artifact
) -> None:
    store = JobStore(tmp_path / "state")
    response = store.create(
        artifact=artifact, verify=False, content=b"%PDF-1.4\n"
    )
    job_id = response.job.id
    job_dir = store.work(job_id).input_path.parent

    with pytest.raises(JobNotTerminal):
        store.delete_terminal(job_id)
    assert job_dir.is_dir()

    store.fail(job_id, RuntimeError("terminal"))
    store.delete_terminal(job_id)
    assert not job_dir.exists()
    with pytest.raises(JobNotFound):
        store.get(job_id)


def test_store_cancels_only_active_jobs(tmp_path, artifact) -> None:
    store = JobStore(tmp_path / "state")
    response = store.create(
        artifact=artifact, verify=False, content=b"%PDF-1.4\n"
    )
    cancelled = store.cancel_active(response.job.id).job
    assert cancelled.status == "failed"
    assert cancelled.error is not None
    assert cancelled.error.code == "JOB_CANCELLED"
    assert cancelled.stage_timings_ms.queued is not None
    assert cancelled.stage_timings_ms.total is not None
    with pytest.raises(JobNotTerminal):
        store.cancel_active(response.job.id)


def test_store_active_checkpoint_replays_persisted_cancellation(
    tmp_path, artifact
) -> None:
    store = JobStore(tmp_path / "state")
    response = store.create(
        artifact=artifact, verify=False, content=b"%PDF-1.4\n"
    )
    job_id = response.job.id

    assert store.require_active(job_id) is None
    store.transition(job_id, "parsing")
    assert store.require_active(job_id) is None
    store.cancel_active(job_id)

    with pytest.raises(JobCancelled):
        store.require_active(job_id)


def test_failure_records_stage_and_total_timings(tmp_path, artifact) -> None:
    store = JobStore(tmp_path / "state")
    response = store.create(
        artifact=artifact, verify=False, content=b"%PDF-1.4\n"
    )
    store.transition(response.job.id, "parsing")
    failed = store.fail(response.job.id, RuntimeError("boom")).job
    assert failed.status == "failed"
    assert failed.error is not None
    assert failed.error.code == "INTERNAL_ERROR"
    assert failed.error.message == "Unexpected compiler failure"
    assert failed.stage_timings_ms.queued is not None
    assert failed.stage_timings_ms.parsing is not None
    assert failed.stage_timings_ms.total is not None


def test_legacy_remote_rejection_is_marked_retryable(
    tmp_path, artifact
) -> None:
    store = JobStore(tmp_path / "state")
    response = store.create(
        artifact=artifact, verify=False, content=b"%PDF-1.4\n"
    )
    store.transition(response.job.id, "parsing")
    failed = store.fail(
        response.job.id,
        MineruRejected("remote MinerU rejected the request with HTTP 422"),
    ).job

    assert failed.error is not None
    assert failed.error.code == "MINERU_REQUEST_REJECTED"
    assert failed.error.retryable is True


def test_internal_exception_content_is_absent_from_database_and_status(
    tmp_path, artifact
) -> None:
    marker = "SECRET-PAPER-MARKER-DO-NOT-PERSIST"
    store = JobStore(tmp_path / "state")
    response = store.create(
        artifact=artifact, verify=False, content=b"%PDF-1.4\n"
    )
    store.transition(response.job.id, "parsing")

    failed = store.fail(response.job.id, RuntimeError(marker)).job

    assert failed.error is not None
    assert marker not in failed.error.message
    assert failed.error.message == "Unexpected compiler failure"
    with sqlite3.connect(store.database) as database:
        raw = database.execute(
            "SELECT error_json FROM compile_jobs WHERE id=?", (response.job.id,)
        ).fetchone()[0]
    assert marker not in raw


def test_terminal_retention_starts_when_work_finishes(
    monkeypatch, tmp_path, artifact, import_envelope
) -> None:
    store = JobStore(tmp_path / "state", ttl_seconds=7)
    failed = store.create(
        artifact=artifact, verify=False, content=b"%PDF-1.4\n"
    )
    completed = store.create(
        artifact=artifact, verify=False, content=b"%PDF-1.4\n"
    )
    _advance_to_validating(store, completed.job.id)

    terminal_ms = 2_000_000_000_000
    monkeypatch.setattr("compiler_service.job_store._now_ms", lambda: terminal_ms)
    store.fail(failed.job.id, RuntimeError("failed"))
    store.complete(completed.job.id, import_envelope, _citation_source(import_envelope))

    with sqlite3.connect(store.database) as database:
        expirations = dict(
            database.execute(
                "SELECT id,expires_ms FROM compile_jobs WHERE id IN (?,?)",
                (failed.job.id, completed.job.id),
            ).fetchall()
        )
    assert expirations == {
        failed.job.id: terminal_ms + 7_000,
        completed.job.id: terminal_ms + 7_000,
    }


def test_failed_job_insert_does_not_leave_private_files(
    monkeypatch, tmp_path, artifact
) -> None:
    store = JobStore(tmp_path / "state")
    monkeypatch.setattr(store, "cleanup_expired", lambda: 0)

    def fail_connect() -> None:
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr(store, "_connect", fail_connect)
    with pytest.raises(sqlite3.OperationalError, match="database unavailable"):
        store.create(artifact=artifact, verify=False, content=b"%PDF-1.4\n")

    assert list(store.jobs_root.iterdir()) == []


def test_corrupt_completed_result_is_reported_as_unavailable(
    tmp_path, artifact, import_envelope
) -> None:
    store = JobStore(tmp_path / "state")
    response = store.create(
        artifact=artifact, verify=False, content=b"%PDF-1.4\n"
    )
    _advance_to_validating(store, response.job.id)
    store.complete(response.job.id, import_envelope, _citation_source(import_envelope))
    result_path = store.work(response.job.id).input_path.parent / "result.json"
    result_path.write_text("not-json", encoding="utf-8")

    with pytest.raises(JobNotComplete, match="result is unavailable"):
        store.result(response.job.id)


def test_deleted_parsing_job_is_not_recreated_by_document_persistence(
    monkeypatch, tmp_path, artifact, mineru_output
) -> None:
    store = JobStore(tmp_path / "state")
    response = store.create(
        artifact=artifact, verify=False, content=b"%PDF-1.4\n"
    )
    job_id = response.job.id
    store.transition(job_id, "parsing")
    job_dir = store.work(job_id).input_path.parent
    parsed = normalize_mineru(mineru_output, artifact)
    original_row = store._row
    deletion_started = False

    def row_then_delete(value: str):
        nonlocal deletion_started
        row = original_row(value)
        if not deletion_started:
            deletion_started = True
            store.cancel_active(value)
            store.delete_terminal(value)
        return row

    monkeypatch.setattr(store, "_row", row_then_delete)
    with pytest.raises(JobNotFound):
        store.save_document(job_id, parsed)

    assert deletion_started
    assert not job_dir.exists()


def test_startup_reconciles_only_canonical_job_directories_and_tombs(
    tmp_path, artifact
) -> None:
    root = tmp_path / "state"
    store = JobStore(root)

    restorable = store.create(
        artifact=artifact, verify=False, content=b"%PDF-1.4\n"
    )
    restorable_dir = store.work(restorable.job.id).input_path.parent
    restorable_tomb = store.jobs_root / (
        f".delete-{restorable.job.id}-{'a' * 32}"
    )
    os.replace(restorable_dir, restorable_tomb)
    duplicate_live_tomb = store.jobs_root / (
        f".delete-{restorable.job.id}-{'d' * 32}"
    )
    duplicate_live_tomb.mkdir()
    (duplicate_live_tomb / "stale").write_text("remove", encoding="utf-8")

    removable = store.create(
        artifact=artifact, verify=False, content=b"%PDF-1.4\n"
    )
    removable_dir = store.work(removable.job.id).input_path.parent
    removable_tomb = store.jobs_root / (
        f".delete-{removable.job.id}-{'b' * 32}"
    )
    os.replace(removable_dir, removable_tomb)
    with store._connect() as database:
        database.execute(
            "DELETE FROM compile_jobs WHERE id=?", (removable.job.id,)
        )

    orphan = store.jobs_root / str(uuid.uuid4())
    orphan.mkdir()
    (orphan / "orphan").write_text("remove", encoding="utf-8")
    unrelated = store.jobs_root / "not-a-job"
    unrelated.mkdir()
    uppercase_uuid = store.jobs_root / str(uuid.uuid4()).upper()
    uppercase_uuid.mkdir()
    malformed_tomb = store.jobs_root / (
        f".delete-{uuid.uuid4()}-{'not-hex' * 5}"
    )
    malformed_tomb.mkdir()
    symlink_target = tmp_path / "outside"
    symlink_target.mkdir()
    canonical_symlink = store.jobs_root / str(uuid.uuid4())
    tomb_symlink = store.jobs_root / (
        f".delete-{uuid.uuid4()}-{'c' * 32}"
    )
    canonical_symlink.symlink_to(symlink_target, target_is_directory=True)
    tomb_symlink.symlink_to(symlink_target, target_is_directory=True)

    restarted = JobStore(root)

    assert restarted.get(restorable.job.id).job.status == "queued"
    assert restorable_dir.is_dir()
    assert not restorable_tomb.exists()
    assert not duplicate_live_tomb.exists()
    assert not removable_tomb.exists()
    assert not orphan.exists()
    assert unrelated.is_dir()
    assert uppercase_uuid.is_dir()
    assert malformed_tomb.is_dir()
    assert canonical_symlink.is_symlink()
    assert tomb_symlink.is_symlink()
    assert symlink_target.is_dir()


def test_expired_cleanup_retries_a_committed_tomb_after_rmtree_failure(
    monkeypatch, tmp_path, artifact
) -> None:
    store = JobStore(tmp_path / "state", ttl_seconds=1)
    response = store.create(
        artifact=artifact, verify=False, content=b"%PDF-1.4\n"
    )
    store.fail(response.job.id, RuntimeError("terminal"))
    job_dir = store.work(response.job.id).input_path.parent
    original_rmtree = shutil.rmtree
    failed_once = False

    def flaky_rmtree(path) -> None:
        nonlocal failed_once
        if not failed_once and path.name.startswith(".delete-"):
            failed_once = True
            raise OSError("temporary filesystem failure")
        original_rmtree(path)

    monkeypatch.setattr("compiler_service.job_store.shutil.rmtree", flaky_rmtree)
    assert store.cleanup_expired(now_ms=10**16) == 1
    assert failed_once
    assert not job_dir.exists()
    with pytest.raises(JobNotFound):
        store.get(response.job.id)
    assert len(list(store.jobs_root.glob(".delete-*"))) == 1

    assert store.cleanup_expired(now_ms=10**16) == 0
    assert list(store.jobs_root.glob(".delete-*")) == []


def test_reconciliation_cannot_remove_a_create_before_database_insert(
    monkeypatch, tmp_path, artifact
) -> None:
    from compiler_service import job_store

    store = JobStore(tmp_path / "state")
    write_started = threading.Event()
    allow_write = threading.Event()
    reconcile_started = threading.Event()
    reconcile_finished = threading.Event()
    original_write = job_store._atomic_write

    def gated_write(path, content) -> None:
        original_write(path, content)
        if path.name.startswith("input"):
            write_started.set()
            assert allow_write.wait(timeout=5)

    def reconcile() -> int:
        reconcile_started.set()
        try:
            return store._reconcile_files()
        finally:
            reconcile_finished.set()

    monkeypatch.setattr(job_store, "_atomic_write", gated_write)
    with ThreadPoolExecutor(max_workers=2) as executor:
        created = executor.submit(
            store.create,
            artifact=artifact,
            verify=False,
            content=b"%PDF-1.4\n",
        )
        assert write_started.wait(timeout=5)
        reconciled = executor.submit(reconcile)
        assert reconcile_started.wait(timeout=5)
        was_blocked = not reconcile_finished.wait(timeout=0.1)
        allow_write.set()
        assert was_blocked
        response = created.result(timeout=5)
        assert reconciled.result(timeout=5) == 0

    assert store.work(response.job.id).input_path.is_file()


def test_result_round_trip_preserves_required_nullable_audit_fields(
    tmp_path, artifact, import_envelope
) -> None:
    store = JobStore(tmp_path / "state")
    created = store.create(artifact=artifact, verify=True, content=b"%PDF-1.4\n")
    _advance_to_validating(store, created.job.id)
    audit = CompilerAuditRecord(
        schema_version="compiler-audit/1.0",
        entries=[CompilerAuditEntry(kind="global_rewrite", target=None, detail=None)],
        omitted_entries=0,
        warnings=[],
    )
    envelope = import_envelope.model_copy(update={"compiler_audit": audit})
    store.complete(created.job.id, envelope, _citation_source(envelope))
    restarted = JobStore(tmp_path / "state")
    assert restarted.result(created.job.id) == envelope
