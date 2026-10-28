"""Durable, artifact-version keyed Data Quality jobs."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import JobNotComplete, JobNotFound
from .graph_contract import canonical_json
from .models import ArtifactContext, ParseResponse

_UUID_TOKEN = (
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}"
)
_DELETE_TOMB = re.compile(
    rf"^\.delete-(?P<job_id>{_UUID_TOKEN})-(?P<nonce>[0-9a-f]{{32}})$"
)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with os.fdopen(
            os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
            "wb",
        ) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class DataQualityStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.work_root = self.root / "data-quality"
        self.database = self.root / "jobs.sqlite3"
        self._lock = threading.RLock()
        self.work_root.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS data_quality_jobs (
                    id TEXT PRIMARY KEY,
                    artifact_version_id TEXT NOT NULL,
                    artifact_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    progress INTEGER NOT NULL,
                    input_path TEXT,
                    document_path TEXT,
                    result_path TEXT,
                    error_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            db.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS data_quality_version_idx
                   ON data_quality_jobs(artifact_version_id)"""
            )
            restarted = json.dumps(
                {
                    "code": "SERVICE_RESTARTED",
                    "message": "Compiler restarted before Data Quality completed",
                    "retryable": True,
                },
                separators=(",", ":"),
            )
            db.execute(
                """UPDATE data_quality_jobs
                   SET status='failed',stage='failed',progress=100,
                       error_json=?,updated_at=?
                   WHERE status IN ('queued','running')""",
                (restarted, _now()),
            )
        self._reconcile_delete_tombs()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def create_upload(
        self, artifact: ArtifactContext, content: bytes
    ) -> dict[str, Any]:
        with self._lock:
            existing = self.find_version(artifact.version_id)
            if existing is not None:
                if existing["job"]["status"] == "failed":
                    job_id = existing["job"]["id"]
                    path = self.work_root / job_id / "input.pdf"
                    _atomic_write(path, content)
                    with self._connect() as db:
                        db.execute(
                            """UPDATE data_quality_jobs
                               SET status='queued',stage='queued',progress=0,
                                   input_path=?,error_json=NULL,updated_at=?
                               WHERE id=?""",
                            (str(path), _now(), job_id),
                        )
                    return self.get(job_id)
                return existing
            job_id = str(uuid.uuid4())
            directory = self.work_root / job_id
            directory.mkdir(mode=0o700)
            path = directory / "input.pdf"
            _atomic_write(path, content)
            return self._insert(job_id, artifact, input_path=path, document_path=None)

    def create_document(self, parsed: ParseResponse) -> tuple[dict[str, Any], bool]:
        with self._lock:
            existing = self.find_version(parsed.document.artifact.version_id)
            if existing is not None:
                return existing, False
            job_id = str(uuid.uuid4())
            directory = self.work_root / job_id
            directory.mkdir(mode=0o700)
            path = directory / "document-ir.json"
            _atomic_write(
                path,
                (canonical_json(parsed.model_dump(mode="json")) + "\n").encode("utf-8"),
            )
            return self._insert(
                job_id, parsed.document.artifact, input_path=None, document_path=path
            ), True

    def _insert(
        self,
        job_id: str,
        artifact: ArtifactContext,
        *,
        input_path: Path | None,
        document_path: Path | None,
    ) -> dict[str, Any]:
        now = _now()
        with self._connect() as db:
            db.execute(
                """INSERT INTO data_quality_jobs
                   (id,artifact_version_id,artifact_json,status,stage,progress,
                    input_path,document_path,result_path,error_json,created_at,updated_at)
                   VALUES (?,?,?,'queued','queued',0,?,?,NULL,NULL,?,?)""",
                (
                    job_id,
                    artifact.version_id,
                    artifact.model_dump_json(),
                    str(input_path) if input_path else None,
                    str(document_path) if document_path else None,
                    now,
                    now,
                ),
            )
        return self.get(job_id)

    def transition(
        self, job_id: str, status: str, stage: str, progress: int
    ) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            result = db.execute(
                """UPDATE data_quality_jobs
                       SET status=?,stage=?,progress=?,updated_at=?
                       WHERE id=? AND status NOT IN ('completed','failed')""",
                (status, stage, progress, _now(), job_id),
            )
            if result.rowcount != 1:
                raise JobNotFound("data quality job was not active")
        return self.get(job_id)

    def save_document(self, job_id: str, parsed: ParseResponse) -> Path:
        with self._lock:
            self._row(job_id)
            path = self.work_root / job_id / "document-ir.json"
            _atomic_write(
                path,
                (canonical_json(parsed.model_dump(mode="json")) + "\n").encode(
                    "utf-8"
                ),
            )
            with self._connect() as db:
                result = db.execute(
                    """UPDATE data_quality_jobs
                       SET document_path=?,updated_at=? WHERE id=?""",
                    (str(path), _now(), job_id),
                )
                if result.rowcount != 1:
                    path.unlink(missing_ok=True)
                    raise JobNotFound("data quality job was not found")
        return path

    def load_document(self, job_id: str) -> ParseResponse:
        row = self._row(job_id)
        return self._load_document_row(row)

    def load_document_version(self, version_id: str) -> ParseResponse:
        """Load retained DocumentIR by immutable artifact version identity."""

        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM data_quality_jobs WHERE artifact_version_id=? LIMIT 1",
                (version_id,),
            ).fetchone()
        if row is None:
            raise JobNotFound("document extraction was not found")
        return self._load_document_row(row)

    def input(self, job_id: str) -> tuple[ArtifactContext, Path]:
        row = self._row(job_id)
        if not row["input_path"]:
            raise JobNotComplete("data quality input is unavailable")
        return ArtifactContext.model_validate_json(
            row["artifact_json"], strict=True
        ), Path(row["input_path"])

    def complete(self, job_id: str, report: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._row(job_id)
            path = self.work_root / job_id / "result.json"
            _atomic_write(path, (canonical_json(report) + "\n").encode("utf-8"))
            with self._connect() as db:
                result = db.execute(
                    """UPDATE data_quality_jobs
                       SET status='completed',stage='completed',progress=100,
                           result_path=?,updated_at=? WHERE id=?""",
                    (str(path), _now(), job_id),
                )
                if result.rowcount != 1:
                    path.unlink(missing_ok=True)
                    raise JobNotFound("data quality job was not found")
        return self.get(job_id)

    def fail(
        self, job_id: str, message: str = "Data quality inspection failed"
    ) -> dict[str, Any]:
        error = json.dumps(
            {
                "code": "DATA_QUALITY_FAILED",
                "message": message[:1000],
                "retryable": True,
            },
            separators=(",", ":"),
        )
        with self._lock, self._connect() as db:
            result = db.execute(
                """UPDATE data_quality_jobs
                       SET status='failed',stage='failed',progress=100,
                           error_json=?,updated_at=?
                       WHERE id=? AND status NOT IN ('completed','failed')""",
                (error, _now(), job_id),
            )
            if result.rowcount != 1:
                raise JobNotFound("data quality job was not active")
        return self.get(job_id)

    def delete_version(self, version_id: str) -> bool:
        """Delete one version-bound DocumentIR without following filesystem links."""

        with self._lock, self._connect() as db:
            row = db.execute(
                """SELECT id FROM data_quality_jobs
                   WHERE artifact_version_id=? LIMIT 1""",
                (version_id,),
            ).fetchone()
            if row is None:
                return False
            job_id = str(uuid.UUID(row["id"]))
            if job_id != row["id"]:
                raise RuntimeError("data quality job identity is invalid")
            directory = self.work_root / job_id
            tomb = self.work_root / f".delete-{job_id}-{uuid.uuid4().hex}"
            moved = False
            if directory.exists() or directory.is_symlink():
                os.replace(directory, tomb)
                moved = True
            try:
                result = db.execute(
                    """DELETE FROM data_quality_jobs
                       WHERE id=? AND artifact_version_id=?""",
                    (job_id, version_id),
                )
                if result.rowcount != 1:
                    raise RuntimeError(
                        "data quality deletion lost its database row"
                    )
                if moved:
                    _remove_direct_entry(tomb, self.work_root)
            except Exception:
                if moved and not directory.exists() and not directory.is_symlink():
                    os.replace(tomb, directory)
                raise
        return True

    def get(self, job_id: str) -> dict[str, Any]:
        return self._public(self._row(job_id))

    def find_version(self, version_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM data_quality_jobs WHERE artifact_version_id=? LIMIT 1",
                (version_id,),
            ).fetchone()
        return self._public(row) if row is not None else None

    def result(self, job_id: str) -> dict[str, Any]:
        row = self._row(job_id)
        if row["status"] != "completed" or not row["result_path"]:
            raise JobNotComplete("data quality job has not completed")
        try:
            value = json.loads(Path(row["result_path"]).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise JobNotComplete("data quality result is unavailable") from exc
        if not isinstance(value, dict):
            raise JobNotComplete("data quality result is invalid")
        return value

    def _row(self, job_id: str) -> sqlite3.Row:
        try:
            if str(uuid.UUID(job_id)) != job_id:
                raise ValueError
        except ValueError as exc:
            raise JobNotFound("data quality job was not found") from exc
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM data_quality_jobs WHERE id=?", (job_id,)
            ).fetchone()
        if row is None:
            raise JobNotFound("data quality job was not found")
        return row

    def _load_document_row(self, row: sqlite3.Row) -> ParseResponse:
        if not row["document_path"]:
            raise JobNotComplete("data quality document is unavailable")
        try:
            parsed = ParseResponse.model_validate_json(
                Path(row["document_path"]).read_text(encoding="utf-8"), strict=True
            )
            artifact = ArtifactContext.model_validate_json(
                row["artifact_json"], strict=True
            )
        except (OSError, ValueError) as exc:
            raise JobNotComplete("data quality document is unavailable") from exc
        if (
            parsed.document.artifact != artifact
            or parsed.document.artifact.version_id != row["artifact_version_id"]
        ):
            raise JobNotComplete("data quality document identity is invalid")
        return parsed

    def _public(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema_version": "data-quality-job/1.0",
            "job": {
                "id": row["id"],
                "artifact_version_id": row["artifact_version_id"],
                "status": row["status"],
                "stage": row["stage"],
                "progress": row["progress"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "error": json.loads(row["error_json"]) if row["error_json"] else None,
            },
        }

    def _reconcile_delete_tombs(self) -> None:
        with self._lock:
            with self._connect() as db:
                job_ids = {
                    row["id"]
                    for row in db.execute("SELECT id FROM data_quality_jobs").fetchall()
                }
            for tomb in self.work_root.iterdir():
                match = _DELETE_TOMB.fullmatch(tomb.name)
                if match is None:
                    continue
                job_id = match.group("job_id")
                destination = self.work_root / job_id
                if (
                    job_id in job_ids
                    and not destination.exists()
                    and not destination.is_symlink()
                ):
                    os.replace(tomb, destination)
                elif job_id not in job_ids:
                    _remove_direct_entry(tomb, self.work_root)


def _remove_direct_entry(path: Path, root: Path) -> None:
    if path.parent != root or not _DELETE_TOMB.fullmatch(path.name):
        raise RuntimeError("refusing to remove an untrusted data quality path")
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
