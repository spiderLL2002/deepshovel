"""Disk-backed exact vector index for retrieval at library scale (RTX-1).

Embedded Qdrant unpickles every collection into RAM as float64 and scans with
a Python filter loop. This index keeps unit-normalized vectors in an
append-only float16 memory-mapped matrix (half the bytes of float32, paged in
on demand) and point metadata in SQLite. A search selects the candidate rows
with indexed SQL, then scores exactly with one matrix-vector product.

It implements the same ``VectorIndex`` protocol, point identity, filters and
deterministic tie-break as ``QdrantVectorIndex`` and is selected with
``RETRIEVAL_VECTOR_BACKEND=numpy`` after ``python -m
compiler_service.retrieval_vectors migrate`` has copied the embedded Qdrant
collection. Writes order the vector slot before the metadata commit, so an
interrupted write leaves an unreferenced slot, never a row without a vector.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import threading
import uuid
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from .retrieval_contract import SearchScope, SearchScopeFilter
from .retrieval_store import (
    _SCOPE_ARRAY_FIELDS,
    ChannelHit,
    _qdrant_scope_payload,
    _scope_index_payload,
    _scope_numeric_is_unusable,
)

VECTOR_STORE_FORMAT = "deepshovel-vectors/1"
_INITIAL_CAPACITY = 1_024
_SCORE_DTYPE = np.float32


def collection_name_for(model_name: str, model_revision: str, dimension: int) -> str:
    """Same collection identity as ``QdrantVectorIndex``."""

    identity = hashlib.sha256(f"{model_name}\0{model_revision}".encode()).hexdigest()
    return f"claims_v2_{identity[:16]}_{dimension}"


def point_id_for(
    model_name: str,
    model_revision: str,
    project_id: str,
    snapshot_id: str,
    claim_id: str,
) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            "\0".join((model_name, model_revision, project_id, snapshot_id, claim_id)),
        )
    )


class NumpyVectorIndex:
    """Exact cosine search over a float16 memmap plus SQLite point metadata."""

    local_compatibility = None

    def __init__(
        self,
        *,
        data_dir: Path,
        model_name: str,
        model_revision: str,
        dimension: int,
    ) -> None:
        if type(dimension) is not int or not 1 <= dimension <= 8_192:
            raise ValueError("vector dimension must be an integer from 1 to 8192")
        self.model_name = model_name
        self.model_revision = model_revision
        self.dimension = dimension
        self.collection_name = collection_name_for(
            model_name, model_revision, dimension
        )
        self._root = data_dir / "vectors" / self.collection_name
        self._root.mkdir(parents=True, exist_ok=True)
        self._db_path = self._root / "points.sqlite3"
        self._matrix_path = self._root / "vectors.f16"
        self._lock = threading.RLock()
        self._matrix: np.memmap | None = None
        self._initialize()

    # -- storage -------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS vector_meta (
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS vector_points (
                  slot INTEGER PRIMARY KEY,
                  point_id TEXT NOT NULL UNIQUE,
                  project_id TEXT NOT NULL,
                  snapshot_id TEXT NOT NULL,
                  claim_id TEXT NOT NULL,
                  claim_type TEXT NOT NULL,
                  polarity TEXT NOT NULL,
                  scope_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS vector_points_snapshot
                  ON vector_points (project_id, snapshot_id);
                """
            )
            expected = {
                "format": VECTOR_STORE_FORMAT,
                "model_name": self.model_name,
                "model_revision": self.model_revision,
                "dimension": str(self.dimension),
                "dtype": "float16",
            }
            stored = dict(
                connection.execute("SELECT key, value FROM vector_meta").fetchall()
            )
            if stored and stored != expected:
                raise RuntimeError("vector store identity does not match the model")
            if not stored:
                connection.executemany(
                    "INSERT INTO vector_meta (key, value) VALUES (?, ?)",
                    sorted(expected.items()),
                )

    def _rows_capacity(self) -> int:
        if not self._matrix_path.exists():
            return 0
        size = self._matrix_path.stat().st_size
        return size // (2 * self.dimension)

    def _open_matrix(self, minimum_rows: int = 0) -> np.memmap | None:
        capacity = self._rows_capacity()
        if minimum_rows > capacity:
            new_capacity = max(_INITIAL_CAPACITY, capacity)
            while new_capacity < minimum_rows:
                new_capacity *= 2
            self._matrix = None
            with open(self._matrix_path, "ab") as stream:
                stream.truncate(new_capacity * 2 * self.dimension)
            capacity = new_capacity
        if capacity == 0:
            return None
        if self._matrix is None or self._matrix.shape[0] != capacity:
            self._matrix = np.memmap(
                self._matrix_path,
                dtype=np.float16,
                mode="r+",
                shape=(capacity, self.dimension),
            )
        return self._matrix

    # -- protocol ------------------------------------------------------------

    def upsert(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        claim_ids: Sequence[str],
        claim_types: Sequence[str],
        polarities: Sequence[str],
        scopes: Sequence[SearchScope],
        vectors: Sequence[Sequence[float]],
    ) -> list[str]:
        if not (
            len(claim_ids)
            == len(claim_types)
            == len(polarities)
            == len(scopes)
            == len(vectors)
        ):
            raise ValueError("claim/vector counts do not match")
        entries = [
            (
                claim_id,
                claim_type,
                polarity,
                _qdrant_scope_payload(_scope_index_payload(scope)),
                vector,
            )
            for claim_id, claim_type, polarity, scope, vector in zip(
                claim_ids, claim_types, polarities, scopes, vectors, strict=True
            )
        ]
        return self.upsert_payloads(
            project_id=project_id, snapshot_id=snapshot_id, entries=entries
        )

    def upsert_payloads(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        entries: Sequence[tuple[str, str, str, dict[str, Any], Sequence[float]]],
    ) -> list[str]:
        """Write points whose scope is already in the indexed payload shape."""

        if not entries:
            return []
        unit = _unit_rows([entry[4] for entry in entries], self.dimension)
        point_ids = [
            point_id_for(
                self.model_name, self.model_revision, project_id, snapshot_id, entry[0]
            )
            for entry in entries
        ]
        with self._lock, self._connect() as connection:
            existing = {
                row["point_id"]: int(row["slot"])
                for row in connection.execute(
                    "SELECT point_id, slot FROM vector_points WHERE point_id IN "
                    "(SELECT value FROM json_each(?))",
                    (json.dumps(point_ids),),
                )
            }
            next_slot = int(
                connection.execute(
                    "SELECT COALESCE(MAX(slot), -1) + 1 FROM vector_points"
                ).fetchone()[0]
            )
            slots: list[int] = []
            for point_id in point_ids:
                slot = existing.get(point_id)
                if slot is None:
                    slot = next_slot
                    next_slot += 1
                    existing[point_id] = slot
                slots.append(slot)
            matrix = self._open_matrix(max(slots) + 1)
            assert matrix is not None
            matrix[np.asarray(slots)] = unit.astype(np.float16)
            matrix.flush()
            connection.executemany(
                """
                INSERT INTO vector_points (
                  slot, point_id, project_id, snapshot_id, claim_id,
                  claim_type, polarity, scope_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(point_id) DO UPDATE SET
                  claim_type = excluded.claim_type,
                  polarity = excluded.polarity,
                  scope_json = excluded.scope_json
                """,
                [
                    (
                        slot,
                        point_id,
                        project_id,
                        snapshot_id,
                        entry[0],
                        entry[1],
                        entry[2],
                        json.dumps(entry[3], sort_keys=True, separators=(",", ":")),
                    )
                    for slot, point_id, entry in zip(
                        slots, point_ids, entries, strict=True
                    )
                ],
            )
        return point_ids

    def search(
        self,
        *,
        project_id: str,
        snapshot_ids: Sequence[str],
        vector: Sequence[float],
        limit: int,
        claim_types: Sequence[str],
        polarity: str | None,
        scope: SearchScopeFilter | None,
    ) -> list[ChannelHit]:
        if not snapshot_ids or limit <= 0:
            return []
        if len(vector) != self.dimension:
            raise ValueError("dense query vector dimension does not match collection")
        query_payload = None
        if scope is not None:
            query_payload = _scope_index_payload(scope)
            if _scope_numeric_is_unusable(scope, query_payload):
                return []
        clauses = [
            "project_id = ?",
            "snapshot_id IN (SELECT value FROM json_each(?))",
        ]
        parameters: list[Any] = [project_id, json.dumps(list(snapshot_ids))]
        if claim_types:
            clauses.append("claim_type IN (SELECT value FROM json_each(?))")
            parameters.append(json.dumps(list(claim_types)))
        if polarity is not None:
            clauses.append("polarity = ?")
            parameters.append(polarity)
        columns = "slot, snapshot_id, claim_id" + (
            ", scope_json" if query_payload is not None else ""
        )
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"SELECT {columns} FROM vector_points WHERE {' AND '.join(clauses)} "
                "ORDER BY slot",
                parameters,
            ).fetchall()
            if query_payload is not None:
                rows = [
                    row
                    for row in rows
                    if _payload_matches(json.loads(row["scope_json"]), query_payload)
                ]
            if not rows:
                return []
            matrix = self._open_matrix()
            if matrix is None:
                return []
            slots = np.fromiter((row["slot"] for row in rows), dtype=np.int64)
            query = _unit_rows([vector], self.dimension)[0].astype(_SCORE_DTYPE)
            scores = np.asarray(matrix[slots], dtype=_SCORE_DTYPE) @ query
        hits = [
            ChannelHit(
                snapshot_id=row["snapshot_id"],
                claim_id=row["claim_id"],
                score=float(score),
            )
            for row, score in zip(rows, scores.tolist(), strict=True)
        ]
        hits.sort(key=lambda item: (-item.score, item.snapshot_id, item.claim_id))
        return hits[:limit]

    def delete_snapshots(
        self,
        *,
        project_id: str,
        snapshot_ids: Sequence[str],
        collections: Iterable[str],
    ) -> None:
        if self.collection_name not in set(collections) or not snapshot_ids:
            return
        with self._lock, self._connect() as connection:
            # Freed slots stay allocated until ``compact``; they are never
            # referenced again, so a deleted vector cannot resurface.
            connection.execute(
                "DELETE FROM vector_points WHERE project_id = ? AND snapshot_id IN "
                "(SELECT value FROM json_each(?))",
                (project_id, json.dumps(list(snapshot_ids))),
            )

    def count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM vector_points").fetchone()
            return int(row[0])


def _unit_rows(vectors: Sequence[Sequence[float]], dimension: int) -> np.ndarray:
    matrix = np.asarray(vectors, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[1] != dimension:
        raise ValueError("dense vector dimension does not match collection")
    if not np.all(np.isfinite(matrix)):
        raise ValueError("dense vectors must be finite")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def _payload_matches(point: dict[str, Any], query: dict[str, Any]) -> bool:
    """The Qdrant scope filter of ``QdrantVectorIndex.search`` over one payload."""

    for name in _SCOPE_ARRAY_FIELDS:
        wanted = query[name]
        if wanted and not set(wanted).intersection(point.get(f"scope_{name}") or ()):
            return False
    time = query["time"]
    if time is not None:
        if time["label"] and point.get("scope_time_label") != time["label"]:
            return False
        if time["numeric_known"]:
            start = point.get("scope_time_start")
            end = point.get("scope_time_end")
            if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                return False
            low, high = min(start, end), max(start, end)
            if not (low <= time["end"] and high >= time["start"]):
                return False
    return not (
        query["uncertainty"] and point.get("scope_uncertainty") != query["uncertainty"]
    )


def migrate_from_qdrant(
    source: Any, target: NumpyVectorIndex, *, batch: int = 512
) -> int:
    """Copy every point of the embedded Qdrant collection into ``target``.

    Idempotent: points keep their identity, so a re-run overwrites in place.
    """

    client = source._client  # noqa: SLF001 - one-off maintenance copy
    if not client.collection_exists(source.collection_name):
        return 0
    copied = 0
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=source.collection_name,
            limit=batch,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        grouped: dict[tuple[str, str], list[tuple[Any, ...]]] = {}
        for point in points:
            payload = point.payload or {}
            vector = point.vector
            if isinstance(vector, dict):
                vector = next(iter(vector.values()))
            scope = {
                key: value for key, value in payload.items() if key.startswith("scope_")
            }
            key = (payload["project_id"], payload["snapshot_id"])
            grouped.setdefault(key, []).append(
                (
                    payload["claim_id"],
                    payload["claim_type"],
                    payload["polarity"],
                    scope,
                    vector,
                )
            )
        for (project_id, snapshot_id), entries in sorted(grouped.items()):
            target.upsert_payloads(
                project_id=project_id, snapshot_id=snapshot_id, entries=entries
            )
            copied += len(entries)
        if offset is None:
            return copied


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m compiler_service.retrieval_vectors",
        description="Copy the embedded Qdrant collection into the numpy vector store.",
    )
    parser.add_argument("command", choices=["migrate", "status"])
    arguments = parser.parse_args(argv)
    from .config import Settings
    from .retrieval_providers import FastEmbedDenseEncoder
    from .retrieval_store import QdrantVectorIndex

    configured = Settings.from_environment()
    root = configured.data_dir / "retrieval"
    # The encoder only reports its identity here; the ONNX model is not loaded.
    dense = FastEmbedDenseEncoder(
        configured.retrieval_dense_model,
        cache_dir=root / "models",
        model_revision=configured.retrieval_dense_model_revision,
    )
    target = NumpyVectorIndex(
        data_dir=root,
        model_name=dense.name,
        model_revision=dense.revision,
        dimension=dense.dimension,
    )
    if arguments.command == "status":
        print(
            json.dumps({"collection": target.collection_name, "points": target.count()})
        )
        return 0
    source = QdrantVectorIndex(
        data_dir=root,
        model_name=dense.name,
        model_revision=dense.revision,
        dimension=dense.dimension,
    )
    copied = migrate_from_qdrant(source, target)
    print(
        json.dumps(
            {
                "collection": target.collection_name,
                "copied_points": copied,
                "points": target.count(),
                "next_step": "set RETRIEVAL_VECTOR_BACKEND=numpy and restart",
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
