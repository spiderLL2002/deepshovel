"""Durable compile jobs backed by SQLite and atomic per-job files."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .citation_source import CitationSourceProjection
from .errors import (
    CompilerError,
    IdempotencyKeyReused,
    InputError,
    JobCancelled,
    JobNotComplete,
    JobNotFound,
    JobNotTerminal,
    SubmissionExpired,
)
from .graph_contract import PaperEvidenceGraph, canonical_json
from .models import (
    ArtifactContext,
    CompileJob,
    ImportEnvelope,
    JobFailure,
    JobLinks,
    JobResponse,
    JobStatus,
    ParseResponse,
    StageTimings,
)

_STAGES = ("queued", "parsing", "extracting", "validating")
_NEXT = {
    "queued": "parsing",
    "parsing": "extracting",
    "extracting": "validating",
    "validating": "completed",
}
_UUID_TOKEN = (
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}"
)
_TOMB_NAME = re.compile(
    rf"^\.delete-(?P<job_id>{_UUID_TOKEN})-(?P<nonce>[0-9a-f]{{32}})$"
)
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
# A job that keeps dying with the process (for example memory exhaustion while
# parsing one document) must not restart forever. Queued jobs that never ran
# do not consume this budget.
MAX_JOB_RECOVERIES = 2
MAX_JOB_LOOKUP_IDS = 100


@dataclass(frozen=True, slots=True)
class JobWork:
    id: str
    artifact: ArtifactContext
    verify: bool
    input_path: Path


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    """Startup recovery outcome, in job creation order."""

    resumed: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()


class JobStore:
    def __init__(self, root: Path, *, ttl_seconds: int = 86_400) -> None:
        self.root = root.resolve()
        self.jobs_root = self.root / "jobs"
        self.database = self.root / "jobs.sqlite3"
        self.ttl_seconds = ttl_seconds
        self._files_lock = threading.RLock()
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self._reconcile_files()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS compile_jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    artifact_json TEXT NOT NULL,
                    verify INTEGER NOT NULL,
                    input_path TEXT NOT NULL,
                    result_path TEXT,
                    created_ms INTEGER NOT NULL,
                    updated_ms INTEGER NOT NULL,
                    expires_ms INTEGER NOT NULL,
                    stage_started_ms INTEGER NOT NULL,
                    timings_json TEXT NOT NULL,
                    error_json TEXT
                )"""
            )
            db.execute(
                """CREATE INDEX IF NOT EXISTS compile_jobs_expiry_idx
                   ON compile_jobs(status, expires_ms)"""
            )
            # Additive, idempotent upgrades of databases created by earlier
            # releases. Existing rows keep their state and gain no key.
            columns = {
                row["name"]
                for row in db.execute("PRAGMA table_info(compile_jobs)").fetchall()
            }
            if "idempotency_key" not in columns:
                db.execute("ALTER TABLE compile_jobs ADD COLUMN idempotency_key TEXT")
            if "recovery_count" not in columns:
                db.execute(
                    """ALTER TABLE compile_jobs
                       ADD COLUMN recovery_count INTEGER NOT NULL DEFAULT 0"""
                )
            db.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS compile_jobs_idempotency_idx
                   ON compile_jobs(idempotency_key)
                   WHERE idempotency_key IS NOT NULL"""
            )

    def _reconcile_files(self) -> int:
        """Repair only canonical direct job directories and deletion tombs."""

        with self._files_lock:
            with self._connect() as db:
                job_ids = {
                    row["id"]
                    for row in db.execute("SELECT id FROM compile_jobs").fetchall()
                }
            try:
                entries = sorted(self.jobs_root.iterdir(), key=lambda path: path.name)
            except OSError:
                return 0
            canonical: list[tuple[str, Path]] = []
            tombs: list[tuple[str, Path]] = []
            for entry in entries:
                if not _direct_directory(entry, self.jobs_root):
                    continue
                tomb_match = _TOMB_NAME.fullmatch(entry.name)
                if tomb_match is not None:
                    tombs.append((tomb_match.group("job_id"), entry))
                    continue
                job_id = _canonical_uuid(entry.name)
                if job_id is not None:
                    canonical.append((job_id, entry))

            reconciled = 0
            for job_id, tomb in tombs:
                if job_id not in job_ids:
                    reconciled += int(_remove_direct_tree(tomb, self.jobs_root))
                    continue
                destination = self.jobs_root / job_id
                if destination.is_symlink():
                    continue
                if destination.exists():
                    if _direct_directory(destination, self.jobs_root):
                        reconciled += int(_remove_direct_tree(tomb, self.jobs_root))
                    continue
                try:
                    os.replace(tomb, destination)
                except OSError:
                    continue
                reconciled += 1

            for job_id, directory in canonical:
                if job_id not in job_ids:
                    reconciled += int(_remove_direct_tree(directory, self.jobs_root))
            return reconciled

    def recover_incomplete(self) -> RecoveryReport:
        """Resume interrupted jobs from their persisted inputs after a restart.

        Status never moves backwards, so gateways that already observed a stage
        see the same or a newer state. Queued jobs restart from their saved
        input. Parsing jobs re-parse, or reuse a DocumentIR that was persisted
        just before the restart. Extracting and validating jobs resume from the
        saved DocumentIR, so the parser is not paid twice; model work that was
        in flight is redone. A job whose saved progress is missing, or that has
        already been interrupted ``MAX_JOB_RECOVERIES`` times, fails with the
        retryable ``SERVICE_RESTARTED`` error as before.
        """

        now = _now_ms()
        resumed: list[str] = []
        failed: list[str] = []
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                """SELECT id,status,created_ms,updated_ms,stage_started_ms,
                          timings_json,input_path,recovery_count
                   FROM compile_jobs
                   WHERE status NOT IN ('completed','failed')
                   ORDER BY created_ms,id"""
            ).fetchall()
            for row in rows:
                current = row["status"]
                job_dir = self.jobs_root / row["id"]
                if current in {"queued", "parsing"}:
                    saved = Path(row["input_path"]).is_file()
                else:
                    saved = (job_dir / "document-ir.json").is_file()
                recoveries = row["recovery_count"] + int(current != "queued")
                if saved and recoveries <= MAX_JOB_RECOVERIES:
                    if current != "queued":
                        db.execute(
                            """UPDATE compile_jobs SET updated_ms=?,recovery_count=?
                               WHERE id=?""",
                            (max(now, row["updated_ms"] + 1), recoveries, row["id"]),
                        )
                    resumed.append(row["id"])
                    continue
                failure = json.dumps(
                    {
                        "code": "SERVICE_RESTARTED",
                        "message": (
                            "Compiler restarted before this job completed"
                            if saved
                            else "Compiler restarted and the saved job input is gone"
                        ),
                        "retryable": True,
                    },
                    separators=(",", ":"),
                )
                timings = json.loads(row["timings_json"])
                if current in _STAGES:
                    timings[current] = max(0, now - row["stage_started_ms"])
                timings["total"] = max(0, now - row["created_ms"])
                db.execute(
                    """UPDATE compile_jobs SET status='failed',updated_ms=?,
                       expires_ms=?,timings_json=?,error_json=? WHERE id=?""",
                    (
                        max(now, row["updated_ms"] + 1),
                        now + self.ttl_seconds * 1_000,
                        json.dumps(timings, separators=(",", ":")),
                        failure,
                        row["id"],
                    ),
                )
                failed.append(row["id"])
        return RecoveryReport(resumed=tuple(resumed), failed=tuple(failed))

    def create(
        self,
        *,
        artifact: ArtifactContext,
        verify: bool,
        content: bytes,
    ) -> JobResponse:
        with self._files_lock:
            # Expire indexed terminal rows on admission, but leave the linear
            # crash-tomb directory scan to startup and the periodic loop.
            self._delete_expired_records(_now_ms())
            return self._create_locked(
                artifact=artifact, verify=verify, content=content
            )

    def create_idempotent(
        self,
        *,
        artifact: ArtifactContext,
        verify: bool,
        content: bytes,
        idempotency_key: str,
        not_after_ms: int | None = None,
    ) -> tuple[JobResponse, bool]:
        """Create at most one job per caller key.

        A replay with the same key and identical artifact context returns the
        original job and ``False``; it never starts a second parse or model run.
        ``not_after_ms`` is the caller's submission deadline on this host's
        clock: once it has passed, no job is created for the key, so a caller
        that later finds no job for that key knows none will ever appear.
        """

        _validate_idempotency_key(idempotency_key)
        with self._files_lock:
            existing = self._row_by_idempotency_key(idempotency_key)
            if existing is not None:
                return self._idempotent_replay(existing, artifact, verify), False
            self._delete_expired_records(_now_ms())
            try:
                response = self._create_locked(
                    artifact=artifact,
                    verify=verify,
                    content=content,
                    idempotency_key=idempotency_key,
                    not_after_ms=not_after_ms,
                )
            except sqlite3.IntegrityError:
                existing = self._row_by_idempotency_key(idempotency_key)
                if existing is None:
                    raise
                return self._idempotent_replay(existing, artifact, verify), False
            return response, True

    def find_by_idempotency_key(self, idempotency_key: str) -> JobResponse:
        _validate_idempotency_key(idempotency_key)
        row = self._row_by_idempotency_key(idempotency_key)
        if row is None:
            raise JobNotFound("compile job was not found")
        return self._response(row)

    def get_many(self, job_ids: list[str]) -> list[JobResponse]:
        """Return the existing jobs among ``job_ids``; unknown IDs are omitted."""

        if not 1 <= len(job_ids) <= MAX_JOB_LOOKUP_IDS:
            raise InputError(
                f"job lookups accept 1 to {MAX_JOB_LOOKUP_IDS} compile job IDs"
            )
        for job_id in job_ids:
            if _canonical_uuid(job_id) is None:
                raise InputError("compile job IDs must be canonical UUIDs")
        unique = list(dict.fromkeys(job_ids))
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM compile_jobs WHERE id IN ("
                + ",".join("?" for _ in unique)
                + ")",
                unique,
            ).fetchall()
        by_id = {row["id"]: row for row in rows}
        return [self._response(by_id[job_id]) for job_id in unique if job_id in by_id]

    def _row_by_idempotency_key(self, idempotency_key: str) -> sqlite3.Row | None:
        with self._connect() as db:
            return db.execute(
                "SELECT * FROM compile_jobs WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()

    def _idempotent_replay(
        self, row: sqlite3.Row, artifact: ArtifactContext, verify: bool
    ) -> JobResponse:
        stored = ArtifactContext.model_validate_json(row["artifact_json"], strict=True)
        if stored != artifact or bool(row["verify"]) != verify:
            raise IdempotencyKeyReused(
                "idempotency key was already used for a different compile request"
            )
        return self._response(row)

    def _create_locked(
        self,
        *,
        artifact: ArtifactContext,
        verify: bool,
        content: bytes,
        idempotency_key: str | None = None,
        not_after_ms: int | None = None,
    ) -> JobResponse:
        job_id = str(uuid.uuid4())
        job_dir = self._job_dir(job_id)
        job_dir.mkdir(mode=0o700)
        suffix = Path(artifact.file_name).suffix.lower()[:16]
        input_path = job_dir / f"input{suffix}"
        try:
            _atomic_write(input_path, content)
            now = _now_ms()
            if not_after_ms is not None and now > not_after_ms:
                raise SubmissionExpired(
                    "compile submission arrived after its caller deadline"
                )
            expires = now + self.ttl_seconds * 1_000
            timings = {key: None for key in (*_STAGES, "total")}
            with self._connect() as db:
                db.execute(
                    """INSERT INTO compile_jobs
                       (id,status,artifact_json,verify,input_path,result_path,
                        created_ms,updated_ms,expires_ms,stage_started_ms,
                        timings_json,error_json,idempotency_key)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL,?)""",
                    (
                        job_id,
                        "queued",
                        artifact.model_dump_json(),
                        int(verify),
                        str(input_path),
                        None,
                        now,
                        now,
                        expires,
                        now,
                        json.dumps(timings, separators=(",", ":")),
                        idempotency_key,
                    ),
                )
        except Exception:
            # A failed insert must not leave an unreachable private input tree.
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        return self.get(job_id)

    def work(self, job_id: str) -> JobWork:
        row = self._row(job_id)
        return JobWork(
            id=job_id,
            artifact=ArtifactContext.model_validate_json(
                row["artifact_json"], strict=True
            ),
            verify=bool(row["verify"]),
            input_path=Path(row["input_path"]),
        )

    def transition(self, job_id: str, status: JobStatus) -> JobResponse:
        if status in {"queued", "failed"}:
            raise ValueError("use create/fail for queued or failed status")
        now = _now_ms()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM compile_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise JobNotFound("compile job was not found")
            current = row["status"]
            if _NEXT.get(current) != status:
                raise RuntimeError(f"invalid job transition {current}->{status}")
            timings = json.loads(row["timings_json"])
            timings[current] = max(0, now - row["stage_started_ms"])
            if status == "completed":
                timings["total"] = max(0, now - row["created_ms"])
            expires = (
                now + self.ttl_seconds * 1_000
                if status == "completed"
                else row["expires_ms"]
            )
            db.execute(
                """UPDATE compile_jobs SET status=?,updated_ms=?,stage_started_ms=?,
                   expires_ms=?,timings_json=? WHERE id=?""",
                (
                    status,
                    now,
                    now,
                    expires,
                    json.dumps(timings, separators=(",", ":")),
                    job_id,
                ),
            )
        return self.get(job_id)

    def complete(
        self,
        job_id: str,
        envelope: ImportEnvelope,
        citation_source: CitationSourceProjection,
        *,
        raw_graph: PaperEvidenceGraph | None = None,
    ) -> JobResponse:
        job_dir = self._job_dir(job_id)
        result_path = job_dir / "result.json"
        citation_path = job_dir / "citation-source.json"
        raw_path = job_dir / "raw-graph.json"
        if citation_source.artifact_version_id != envelope.root_artifact_version_id:
            raise ValueError(
                "citation source artifact version does not match compile result"
            )
        if raw_graph is not None and raw_graph.paper_id != envelope.graph["paper_id"]:
            raise ValueError("raw graph paper identity does not match compile result")
        try:
            _atomic_write(
                result_path,
                (
                    # Required nullable audit fields must survive a save/load
                    # round trip (a global rewrite has no target identifier).
                    canonical_json(envelope.model_dump(mode="json"))
                    + "\n"
                ).encode("utf-8"),
            )
            _atomic_write(
                citation_path,
                (
                    canonical_json(citation_source.model_dump(mode="json")) + "\n"
                ).encode("utf-8"),
            )
            if raw_graph is not None:
                _atomic_write(
                    raw_path,
                    (
                        canonical_json(raw_graph.model_dump(mode="json")) + "\n"
                    ).encode("utf-8"),
                    private=True,
                )
        except Exception:
            result_path.unlink(missing_ok=True)
            citation_path.unlink(missing_ok=True)
            raw_path.unlink(missing_ok=True)
            raise
        with self._connect() as db:
            db.execute(
                "UPDATE compile_jobs SET result_path=? WHERE id=?",
                (str(result_path), job_id),
            )
        try:
            return self.transition(job_id, "completed")
        except Exception:
            result_path.unlink(missing_ok=True)
            citation_path.unlink(missing_ok=True)
            raw_path.unlink(missing_ok=True)
            with self._connect() as db:
                db.execute(
                    "UPDATE compile_jobs SET result_path=NULL WHERE id=?",
                    (job_id,),
                )
            # delete_terminal may have removed the original directory while a
            # blocking model call was returning. _atomic_write recreates its
            # parent, so remove that new directory only when it is now empty.
            with suppress(OSError):
                job_dir.rmdir()
            raise

    def save_document(self, job_id: str, parsed: ParseResponse) -> Path:
        row = self._row(job_id)
        if row["status"] != "parsing":
            raise RuntimeError("document IR can only be saved while parsing")
        path = self._job_dir(job_id) / "document-ir.json"
        try:
            _atomic_write(
                path,
                (canonical_json(parsed.model_dump(mode="json")) + "\n").encode("utf-8"),
            )
            if self._row(job_id)["status"] != "parsing":
                raise RuntimeError("compile job left parsing during persistence")
        except Exception:
            path.unlink(missing_ok=True)
            # If delete_terminal won the race, _atomic_write may have recreated
            # only this private directory. Never recurse into a non-empty tree.
            with suppress(OSError):
                path.parent.rmdir()
            raise
        return path

    def load_completed_document(self, job_id: str) -> ParseResponse:
        """Load the immutable DocumentIR retained by one completed compile job."""

        row = self._row(job_id)
        if row["status"] != "completed" or not row["result_path"]:
            raise JobNotComplete("compile job has not completed")
        path = self._job_dir(job_id) / "document-ir.json"
        try:
            parsed = ParseResponse.model_validate_json(
                path.read_text(encoding="utf-8"), strict=True
            )
            artifact = ArtifactContext.model_validate_json(
                row["artifact_json"], strict=True
            )
        except (OSError, ValueError) as exc:
            raise JobNotComplete("compile job document is unavailable") from exc
        if parsed.document.artifact != artifact:
            raise JobNotComplete("compile job document identity is invalid")
        return parsed

    def load_saved_document(self, job_id: str) -> ParseResponse | None:
        """Return the intact DocumentIR an active job persisted, if any.

        Restart recovery uses this to resume extraction without re-parsing.
        Missing, unreadable or foreign files are treated as absent.
        """

        row = self._row(job_id)
        if row["status"] not in _STAGES:
            return None
        path = self._job_dir(job_id) / "document-ir.json"
        try:
            parsed = ParseResponse.model_validate_json(
                path.read_text(encoding="utf-8"), strict=True
            )
            artifact = ArtifactContext.model_validate_json(
                row["artifact_json"], strict=True
            )
        except (OSError, ValueError):
            return None
        if parsed.document.artifact != artifact:
            return None
        return parsed

    def fail(self, job_id: str, error: CompilerError | Exception) -> JobResponse:
        now = _now_ms()
        if isinstance(error, CompilerError):
            failure = {
                "code": error.code,
                "message": str(error)[:4_000] or "Compiler operation failed",
                "retryable": error.retryable,
            }
        else:
            # Arbitrary exception text may contain Pydantic input_value, parser
            # content, model output, paths, or secrets. It never crosses the job
            # persistence/API boundary.
            failure = {
                "code": "INTERNAL_ERROR",
                "message": "Unexpected compiler failure",
                "retryable": False,
            }
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM compile_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise JobNotFound("compile job was not found")
            if row["status"] in {"completed", "failed"}:
                return self._response(row)
            timings = json.loads(row["timings_json"])
            current = row["status"]
            if current in _STAGES:
                timings[current] = max(0, now - row["stage_started_ms"])
            timings["total"] = max(0, now - row["created_ms"])
            db.execute(
                """UPDATE compile_jobs SET status='failed',updated_ms=?,
                   expires_ms=?,timings_json=?,error_json=? WHERE id=?""",
                (
                    now,
                    now + self.ttl_seconds * 1_000,
                    json.dumps(timings, separators=(",", ":")),
                    json.dumps(failure, separators=(",", ":")),
                    job_id,
                ),
            )
        return self.get(job_id)

    def get(self, job_id: str) -> JobResponse:
        return self._response(self._row(job_id))

    def require_active(self, job_id: str) -> None:
        """Raise the persisted cancellation when in-flight work must stop."""

        row = self._row(job_id)
        if row["status"] in _STAGES:
            return
        if row["status"] == "failed" and row["error_json"] is not None:
            try:
                failure = json.loads(row["error_json"])
            except (TypeError, ValueError):
                failure = None
            if isinstance(failure, dict) and failure.get("code") == (JobCancelled.code):
                raise JobCancelled("Compile job was cancelled")
        raise JobNotTerminal("compile job is no longer active")

    def result(self, job_id: str) -> ImportEnvelope:
        row = self._row(job_id)
        if row["status"] != "completed" or not row["result_path"]:
            raise JobNotComplete("compile job has not completed")
        try:
            return ImportEnvelope.model_validate_json(
                Path(row["result_path"]).read_text(encoding="utf-8"), strict=True
            )
        except (OSError, ValueError) as exc:
            raise JobNotComplete("compile job result is unavailable") from exc

    def citation_source(self, job_id: str) -> CitationSourceProjection:
        row = self._row(job_id)
        if row["status"] != "completed" or not row["result_path"]:
            raise JobNotComplete("compile job has not completed")
        path = Path(row["result_path"]).with_name("citation-source.json")
        try:
            return CitationSourceProjection.model_validate_json(
                path.read_text(encoding="utf-8"), strict=True
            )
        except (OSError, ValueError) as exc:
            raise JobNotComplete("compile job citation source is unavailable") from exc

    def cleanup_expired(self, *, now_ms: int | None = None) -> int:
        now = _now_ms() if now_ms is None else now_ms
        with self._files_lock:
            self._reconcile_files()
            return self._delete_expired_records(now)

    def _delete_expired_records(self, now_ms: int) -> int:
        with self._connect() as db:
            job_ids = [
                row["id"]
                for row in db.execute(
                    """SELECT id FROM compile_jobs
                       WHERE expires_ms<=?
                         AND status IN ('completed','failed')""",
                    (now_ms,),
                ).fetchall()
            ]
        deleted = 0
        for job_id in job_ids:
            try:
                self.delete_terminal(job_id)
            except (JobNotFound, JobNotTerminal):
                continue
            except OSError:
                # rmtree can fail after the database commit. The tomb remains
                # bounded and the next reconciliation retries it.
                try:
                    self._row(job_id)
                except JobNotFound:
                    deleted += 1
            else:
                deleted += 1
        return deleted

    def delete_terminal(self, job_id: str) -> None:
        """Delete one finished job and its private working files."""

        with self._files_lock:
            _validate_uuid(job_id)
            job_dir = self._job_dir(job_id)
            tomb = self.jobs_root / f".delete-{job_id}-{uuid.uuid4().hex}"
            db = self._connect()
            moved = False
            try:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT status FROM compile_jobs WHERE id=?", (job_id,)
                ).fetchone()
                if row is None:
                    raise JobNotFound("compile job was not found")
                if row["status"] not in {"completed", "failed"}:
                    raise JobNotTerminal("active compile jobs cannot be deleted")
                if job_dir.exists():
                    os.replace(job_dir, tomb)
                    moved = True
                db.execute("DELETE FROM compile_jobs WHERE id=?", (job_id,))
                db.commit()
            except Exception:
                db.rollback()
                if moved and tomb.exists() and not job_dir.exists():
                    os.replace(tomb, job_dir)
                raise
            finally:
                db.close()
            if moved:
                shutil.rmtree(tomb)

    def cancel_active(self, job_id: str) -> JobResponse:
        """Mark active work terminal so queued stages skip and results discard."""

        _validate_uuid(job_id)
        row = self._row(job_id)
        if row["status"] in {"completed", "failed"}:
            raise JobNotTerminal("only active compile jobs can be cancelled")
        return self.fail(job_id, JobCancelled("Compile job was cancelled"))

    def _row(self, job_id: str) -> sqlite3.Row:
        _validate_uuid(job_id)
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM compile_jobs WHERE id=?", (job_id,)
            ).fetchone()
        if row is None:
            raise JobNotFound("compile job was not found")
        return row

    def _response(self, row: sqlite3.Row) -> JobResponse:
        timings = StageTimings.model_validate(
            json.loads(row["timings_json"]), strict=True
        )
        error = (
            None
            if row["error_json"] is None
            else JobFailure.model_validate(json.loads(row["error_json"]), strict=True)
        )
        job_id = row["id"]
        return JobResponse(
            job=CompileJob(
                id=job_id,
                status=row["status"],
                created_at=_iso(row["created_ms"]),
                updated_at=_iso(row["updated_ms"]),
                expires_at=_iso(row["expires_ms"]),
                stage_timings_ms=timings,
                error=error,
                links=JobLinks(
                    self=f"/v1/compile-jobs/{job_id}",
                    result=f"/v1/compile-jobs/{job_id}/result",
                ),
            )
        )

    def _job_dir(self, job_id: str) -> Path:
        _validate_uuid(job_id)
        return self.jobs_root / job_id


def _validate_uuid(value: str) -> None:
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise JobNotFound("compile job was not found") from exc
    if str(parsed) != value:
        raise JobNotFound("compile job was not found")


def _validate_idempotency_key(value: str) -> None:
    if not isinstance(value, str) or _IDEMPOTENCY_KEY.fullmatch(value) is None:
        raise InputError("compile idempotency key is invalid")


def _canonical_uuid(value: str) -> str | None:
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return None
    return value if str(parsed) == value else None


def _direct_directory(path: Path, parent: Path) -> bool:
    """Accept a direct real directory without following a symbolic link."""

    if path.parent != parent or path.is_symlink():
        return False
    try:
        return path.is_dir()
    except OSError:
        return False


def _remove_direct_tree(path: Path, parent: Path) -> bool:
    if not _direct_directory(path, parent):
        return False
    try:
        shutil.rmtree(path)
    except OSError:
        return False
    return True


def _atomic_write(path: Path, content: bytes, *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        stream = (
            os.fdopen(
                os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                "wb",
            )
            if private
            else temporary.open("xb")
        )
        with stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _iso(milliseconds: int) -> str:
    return (
        datetime.fromtimestamp(milliseconds / 1_000, UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


__all__ = [
    "MAX_JOB_LOOKUP_IDS",
    "MAX_JOB_RECOVERIES",
    "JobStore",
    "JobWork",
    "RecoveryReport",
]
