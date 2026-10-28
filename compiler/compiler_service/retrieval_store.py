"""Persistent SQLite FTS5 and Qdrant-local retrieval projection stores."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any, Protocol

from .graph_contract import canonical_json
from .qdrant_local_compat import (
    PayloadMaskCache,
    install_read_only_local_cosine_patch,
    payload_mask_cache_scope,
)
from .retrieval_contract import (
    ClaimSearchDocument,
    IndexSnapshotRequest,
    RetrievalManifest,
    SearchScope,
    SearchScopeFilter,
)
from .retrieval_ranking_features import LoadedRankingFeatureProjection
from .retrieval_text import (
    constraint_text,
    fts_query,
    lexical_fields,
    lexical_tokens,
    time_interval_years,
)

_OPEN_TIME_BOUND = 1e100
_SCOPE_ARRAY_FIELDS = (
    "location",
    "formation",
    "sample_type",
    "method",
    "condition",
    "measurement",
    "entities",
)
_RANKING_FEATURE_DELETE_TRIGGER_NAME = "retrieval_documents_delete_ranking_features"
_FEATURE_IDENTITY = (
    "project_id",
    "snapshot_id",
    "index_version",
    "dense_model",
    "dense_model_revision",
    "dense_dimension",
)
_RANKING_FEATURE_DELETE_TRIGGER_SQL = f"""
CREATE TRIGGER {_RANKING_FEATURE_DELETE_TRIGGER_NAME}
AFTER DELETE ON retrieval_documents
BEGIN
  DELETE FROM retrieval_ranking_features
  WHERE project_id = OLD.project_id
    AND snapshot_id = OLD.snapshot_id
    AND index_version = OLD.index_version
    AND dense_model = OLD.dense_model
    AND dense_model_revision = OLD.dense_model_revision
    AND dense_dimension = OLD.dense_dimension
    AND claim_id = OLD.claim_id;
END
""".strip()


@dataclass(frozen=True, slots=True)
class StoredManifest:
    manifest_sha256: str
    document_count: int
    indexed_document_count: int
    dense_complete: bool
    collection_name: str


@dataclass(frozen=True, slots=True)
class ChannelHit:
    snapshot_id: str
    claim_id: str
    score: float


@dataclass(frozen=True, slots=True)
class DeletedProjection:
    deleted_snapshots: int
    deleted_documents: int
    collections: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResolvedSnapshot:
    snapshot_id: str
    paper_id: str
    paper_family_id: str
    dense_complete: bool


@dataclass(frozen=True, slots=True)
class RepairProjection:
    project_id: str
    manifest: RetrievalManifest


@dataclass(frozen=True, slots=True)
class RankingFeaturePreflightReceipt:
    project_ids: tuple[str, ...]
    source_documents: int
    inserted: int
    updated: int
    unchanged: int
    deleted_orphans: int
    schema_created: bool
    delete_trigger_created: bool


class VectorIndex(Protocol):
    model_name: str
    model_revision: str
    dimension: int
    collection_name: str

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
    ) -> list[str]: ...

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
    ) -> list[ChannelHit]: ...

    def delete_snapshots(
        self,
        *,
        project_id: str,
        snapshot_ids: Sequence[str],
        collections: Iterable[str],
    ) -> None: ...


def _guard_vector_index(*, mutation: bool = False):
    def decorate(method):
        @wraps(method)
        def guarded(self, *args, **kwargs):
            with self._collection_lock:
                if mutation and self._payload_mask_cache is not None:
                    # Clear before SDK writes, including partially failed writes.
                    self._payload_mask_cache.clear()
                with payload_mask_cache_scope(
                    None if mutation else self._payload_mask_cache
                ):
                    return method(self, *args, **kwargs)

        return guarded

    return decorate


class QdrantVectorIndex:
    """Qdrant index: exact local search by default; remote server optional."""

    def __init__(
        self,
        *,
        data_dir: Path,
        model_name: str,
        model_revision: str,
        dimension: int,
        url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        from qdrant_client import QdrantClient

        self.model_name = model_name
        self.model_revision = model_revision
        self.dimension = dimension
        identity = hashlib.sha256(
            f"{model_name}\0{model_revision}".encode()
        ).hexdigest()[:16]
        self.collection_name = f"claims_v2_{identity}_{dimension}"
        if url is None:
            self.local_compatibility = install_read_only_local_cosine_patch()
            location = data_dir / "qdrant"
            location.mkdir(parents=True, exist_ok=True)
            self._client = QdrantClient(path=str(location))
        else:
            self.local_compatibility = None
            self._client = QdrantClient(url=url, api_key=api_key)
        self._collection_lock = threading.RLock()
        self._payload_mask_cache = PayloadMaskCache() if url is None else None

    @_guard_vector_index(mutation=True)
    def _ensure_collection(self, collection_name: str | None = None) -> str:
        from qdrant_client import models

        name = collection_name or self.collection_name
        if self._client.collection_exists(name):
            return name
        with self._collection_lock:
            if not self._client.collection_exists(name):
                self._client.create_collection(
                    collection_name=name,
                    vectors_config=models.VectorParams(
                        size=self.dimension, distance=models.Distance.COSINE
                    ),
                )
        return name

    @_guard_vector_index(mutation=True)
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
        from qdrant_client import models

        if not (
            len(claim_ids)
            == len(claim_types)
            == len(polarities)
            == len(scopes)
            == len(vectors)
        ):
            raise ValueError("claim/vector counts do not match")
        if not claim_ids:
            return []
        self._ensure_collection()
        point_ids = [
            str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    "\0".join(
                        (
                            self.model_name,
                            self.model_revision,
                            project_id,
                            snapshot_id,
                            claim_id,
                        )
                    ),
                )
            )
            for claim_id in claim_ids
        ]
        points = []
        for point_id, claim_id, claim_type, polarity, scope, vector in zip(
            point_ids,
            claim_ids,
            claim_types,
            polarities,
            scopes,
            vectors,
            strict=True,
        ):
            if len(vector) != self.dimension:
                raise ValueError("dense vector dimension does not match collection")
            scope_payload = _scope_index_payload(scope)
            points.append(
                models.PointStruct(
                    id=point_id,
                    vector=list(vector),
                    payload={
                        "project_id": project_id,
                        "snapshot_id": snapshot_id,
                        "claim_id": claim_id,
                        "claim_type": claim_type,
                        "polarity": polarity,
                        "dense_model": self.model_name,
                        "dense_model_revision": self.model_revision,
                        **_qdrant_scope_payload(scope_payload),
                    },
                )
            )
        self._client.upsert(
            collection_name=self.collection_name,
            points=points,
            wait=True,
        )
        return point_ids

    @_guard_vector_index()
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
        from qdrant_client import models

        if not snapshot_ids or not self._client.collection_exists(self.collection_name):
            return []
        if len(vector) != self.dimension:
            raise ValueError("dense query vector dimension does not match collection")
        conditions: list[Any] = [
            models.FieldCondition(
                key="project_id", match=models.MatchValue(value=project_id)
            ),
            models.FieldCondition(
                key="dense_model", match=models.MatchValue(value=self.model_name)
            ),
            models.FieldCondition(
                key="dense_model_revision",
                match=models.MatchValue(value=self.model_revision),
            ),
            models.FieldCondition(
                key="snapshot_id", match=models.MatchAny(any=list(snapshot_ids))
            ),
        ]
        if claim_types:
            conditions.append(
                models.FieldCondition(
                    key="claim_type", match=models.MatchAny(any=list(claim_types))
                )
            )
        if polarity is not None:
            conditions.append(
                models.FieldCondition(
                    key="polarity", match=models.MatchValue(value=polarity)
                )
            )
        if scope is not None:
            scope_payload = _scope_index_payload(scope)
            if _scope_numeric_is_unusable(scope, scope_payload):
                return []
            for name in _SCOPE_ARRAY_FIELDS:
                values = scope_payload[name]
                if values:
                    conditions.append(
                        models.FieldCondition(
                            key=f"scope_{name}",
                            match=models.MatchAny(any=values),
                        )
                    )
            time_payload = scope_payload["time"]
            if time_payload is not None:
                if time_payload["label"]:
                    conditions.append(
                        models.FieldCondition(
                            key="scope_time_label",
                            match=models.MatchValue(value=time_payload["label"]),
                        )
                    )
                if time_payload["numeric_known"]:
                    # Overlap with the ordered interval, also for points
                    # indexed old-to-young before bounds were ordered.
                    conditions.append(
                        models.Filter(
                            should=[
                                models.Filter(
                                    must=[
                                        models.FieldCondition(
                                            key=low,
                                            range=models.Range(
                                                lte=time_payload["end"]
                                            ),
                                        ),
                                        models.FieldCondition(
                                            key=high,
                                            range=models.Range(
                                                gte=time_payload["start"]
                                            ),
                                        ),
                                    ]
                                )
                                for low, high in (
                                    ("scope_time_start", "scope_time_end"),
                                    ("scope_time_end", "scope_time_start"),
                                )
                            ]
                        )
                    )
            if scope_payload["uncertainty"]:
                conditions.append(
                    models.FieldCondition(
                        key="scope_uncertainty",
                        match=models.MatchValue(value=scope_payload["uncertainty"]),
                    )
                )
        query_filter = models.Filter(must=conditions)

        def query_points(search_limit: int) -> Any:
            return self._client.query_points(
                collection_name=self.collection_name,
                query=list(vector),
                query_filter=query_filter,
                limit=search_limit,
                with_payload=True,
                with_vectors=False,
            )

        def channel_hits(points: Sequence[Any]) -> list[ChannelHit]:
            output: list[ChannelHit] = []
            for point in points:
                payload = point.payload or {}
                snapshot_id = payload.get("snapshot_id")
                claim_id = payload.get("claim_id")
                if isinstance(snapshot_id, str) and isinstance(claim_id, str):
                    output.append(
                        ChannelHit(
                            snapshot_id=snapshot_id,
                            claim_id=claim_id,
                            score=float(point.score),
                        )
                    )
            return output

        # Embedded Qdrant selects Top-K before the identity tie-break below. Ask
        # for K+1 so a score tie crossing the truncation boundary is observable.
        # Remote Qdrant keeps its existing request size and cost profile.
        requested_limit = limit + 1 if self.local_compatibility is not None else limit
        response = query_points(requested_limit)
        hits = channel_hits(response.points)
        ordered_hits = sorted(
            hits,
            key=lambda item: (-item.score, item.snapshot_id, item.claim_id),
        )

        if (
            self.local_compatibility is not None
            and limit > 0
            and len(ordered_hits) > limit
            and ordered_hits[limit - 1].score == ordered_hits[-1].score
        ):
            # The boundary cuts through an exact-score group. Resolve membership
            # over the complete filtered local result, not over Qdrant's arbitrary
            # partial tie selection. This slower path is reached only on a tie.
            exhaustive_filter = models.Filter(
                must=[
                    *conditions,
                    models.HasVectorCondition(has_vector=""),
                ]
            )
            match_count = int(
                self._client.count(
                    collection_name=self.collection_name,
                    count_filter=exhaustive_filter,
                    exact=True,
                ).count
            )
            if match_count < len(response.points):
                raise RuntimeError("embedded Qdrant tie count is inconsistent")
            if match_count > len(response.points):
                response = query_points(match_count)
                if len(response.points) != match_count:
                    raise RuntimeError(
                        "embedded Qdrant tie expansion was not exhaustive"
                    )
                ordered_hits = sorted(
                    channel_hits(response.points),
                    key=lambda item: (-item.score, item.snapshot_id, item.claim_id),
                )
        # Qdrant does not promise an identity tie-break for equal scores.
        # Resolve those ties before hits enter rank fusion or confidence, then
        # restore the caller-requested K after local boundary probing.
        return ordered_hits[:limit]

    @_guard_vector_index(mutation=True)
    def delete_snapshots(
        self,
        *,
        project_id: str,
        snapshot_ids: Sequence[str],
        collections: Iterable[str],
    ) -> None:
        from qdrant_client import models

        for collection in sorted(set(collections)):
            if not self._client.collection_exists(collection):
                continue
            self._client.delete(
                collection_name=collection,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="project_id",
                                match=models.MatchValue(value=project_id),
                            ),
                            models.FieldCondition(
                                key="snapshot_id",
                                match=models.MatchAny(any=list(snapshot_ids)),
                            ),
                        ]
                    )
                ),
                wait=True,
            )


class RetrievalStore:
    """SQLite metadata/FTS projection scoped by model and project."""

    def __init__(
        self, data_dir: Path, *, ranking_features_enabled: bool = False
    ) -> None:
        if type(ranking_features_enabled) is not bool:
            raise ValueError("ranking_features_enabled must be Boolean")
        data_dir.mkdir(parents=True, exist_ok=True)
        self._path = data_dir / "retrieval.sqlite3"
        self._ranking_features_enabled = ranking_features_enabled
        self._ranking_feature_table_available = False
        self._ranking_feature_delete_trigger_available = False
        self._existing_connection_mode: str | None = None
        self._manifest_identities: dict[str, tuple[str, str]] = {}
        self._initialize()
        if ranking_features_enabled:
            with self._connect() as connection:
                self._ranking_feature_table_available = bool(
                    connection.execute(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE type = 'table' "
                        "AND name = 'retrieval_ranking_features'"
                    ).fetchone()
                )
                self._ranking_feature_delete_trigger_available = bool(
                    self._ranking_feature_delete_trigger_is_canonical(connection)
                )

    @classmethod
    def for_ranking_feature_maintenance(
        cls, data_dir: Path, *, writable: bool
    ) -> RetrievalStore:
        """Open an existing projection without creating/repairing base schema.

        Only the explicit sidecar preflight may create its own table/trigger.
        SQLite's ro/rw URI modes also prevent a missing-file race from silently
        creating a new empty retrieval database.
        """

        if type(writable) is not bool:
            raise ValueError("writable must be Boolean")
        path = (data_dir / "retrieval.sqlite3").resolve()
        if not path.is_file():
            raise ValueError("ranking feature maintenance requires an existing index")
        store = cls.__new__(cls)
        store._path = path
        store._ranking_features_enabled = writable
        store._ranking_feature_table_available = False
        store._ranking_feature_delete_trigger_available = False
        store._existing_connection_mode = "rw" if writable else "ro"
        store._manifest_identities = {}
        connection = store._connect()
        try:
            connection.execute(
                "SELECT project_id,snapshot_id,index_version,dense_model,"
                "dense_model_revision,dense_dimension,document_count,"
                "indexed_document_count FROM retrieval_manifests LIMIT 0"
            )
            connection.execute(
                "SELECT project_id,snapshot_id,index_version,dense_model,"
                "dense_model_revision,dense_dimension,claim_id,document_json "
                "FROM retrieval_documents LIMIT 0"
            )
            connection.execute("SELECT rowid FROM retrieval_claim_fts LIMIT 0")
        finally:
            connection.close()
        return store

    def _connect(self) -> sqlite3.Connection:
        if self._existing_connection_mode is None:
            connection = sqlite3.connect(self._path, timeout=30)
        else:
            connection = sqlite3.connect(
                self._path.as_uri() + "?mode=" + self._existing_connection_mode,
                timeout=30,
                uri=True,
            )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS retrieval_manifests (
                  project_id TEXT NOT NULL,
                  snapshot_id TEXT NOT NULL,
                  index_version TEXT NOT NULL,
                  dense_model TEXT NOT NULL,
                  dense_model_revision TEXT NOT NULL,
                  dense_dimension INTEGER NOT NULL,
                  collection_name TEXT NOT NULL,
                  manifest_sha256 TEXT NOT NULL,
                  manifest_json TEXT NOT NULL,
                  document_count INTEGER NOT NULL,
                  indexed_document_count INTEGER NOT NULL,
                  dense_complete INTEGER NOT NULL,
                  created_at TEXT NOT NULL,
                  PRIMARY KEY (
                    project_id, snapshot_id, index_version,
                    dense_model, dense_model_revision, dense_dimension
                  )
                );

                CREATE TABLE IF NOT EXISTS retrieval_documents (
                  rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                  project_id TEXT NOT NULL,
                  snapshot_id TEXT NOT NULL,
                  index_version TEXT NOT NULL,
                  dense_model TEXT NOT NULL,
                  dense_model_revision TEXT NOT NULL,
                  dense_dimension INTEGER NOT NULL,
                  claim_id TEXT NOT NULL,
                  paper_id TEXT NOT NULL,
                  paper_family_id TEXT NOT NULL,
                  claim_type TEXT NOT NULL,
                  polarity TEXT NOT NULL,
                  scope_filter_json TEXT NOT NULL,
                  point_id TEXT,
                  dense_complete INTEGER NOT NULL,
                  document_json TEXT NOT NULL,
                  UNIQUE (
                    project_id, snapshot_id, index_version,
                    dense_model, dense_model_revision, dense_dimension, claim_id
                  )
                );

                CREATE INDEX IF NOT EXISTS retrieval_documents_lookup
                ON retrieval_documents (
                  project_id, snapshot_id, dense_model, dense_model_revision,
                  dense_dimension, claim_id
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS retrieval_claim_fts USING fts5(
                  project_id UNINDEXED,
                  snapshot_id UNINDEXED,
                  dense_model UNINDEXED,
                  entities,
                  key_terms,
                  abbreviations,
                  original_claim,
                  canonical_claim,
                  paper_title,
                  evidence_context,
                  qualifiers,
                  scope,
                  tokenize = 'unicode61 remove_diacritics 0'
                );
                """
            )

    def _ensure_ranking_feature_schema(self) -> None:
        if not self._ranking_features_enabled:
            raise RuntimeError("materialized ranking features are disabled")
        if (
            self._ranking_feature_table_available
            and self._ranking_feature_delete_trigger_available
        ):
            return
        with self._connect() as connection:
            self._create_ranking_feature_schema(connection)
        self._ranking_feature_table_available = True
        self._ranking_feature_delete_trigger_available = True

    @staticmethod
    def _ranking_feature_delete_trigger_is_canonical(
        connection: sqlite3.Connection,
    ) -> bool:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
            (_RANKING_FEATURE_DELETE_TRIGGER_NAME,),
        ).fetchone()
        return bool(row and row[0] == _RANKING_FEATURE_DELETE_TRIGGER_SQL)

    @classmethod
    def _create_ranking_feature_schema(cls, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS retrieval_ranking_features (
              project_id TEXT NOT NULL,
              snapshot_id TEXT NOT NULL,
              index_version TEXT NOT NULL,
              dense_model TEXT NOT NULL,
              dense_model_revision TEXT NOT NULL,
              dense_dimension INTEGER NOT NULL,
              claim_id TEXT NOT NULL,
              projection_json TEXT NOT NULL,
              PRIMARY KEY (
                project_id, snapshot_id, index_version,
                dense_model, dense_model_revision, dense_dimension, claim_id
              )
            )
            """
        )
        if not cls._ranking_feature_delete_trigger_is_canonical(connection):
            connection.execute(
                f"DROP TRIGGER IF EXISTS {_RANKING_FEATURE_DELETE_TRIGGER_NAME}"
            )
            connection.execute(_RANKING_FEATURE_DELETE_TRIGGER_SQL)

    @staticmethod
    def manifest_sha256(manifest: RetrievalManifest) -> str:
        payload = canonical_json(manifest.model_dump(mode="json"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get_manifest(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        index_version: str,
        dense_model: str,
        dense_model_revision: str,
        dense_dimension: int,
    ) -> StoredManifest | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT manifest_sha256, document_count, indexed_document_count,
                       dense_complete, collection_name
                FROM retrieval_manifests
                WHERE project_id = ? AND snapshot_id = ? AND index_version = ?
                  AND dense_model = ? AND dense_model_revision = ?
                  AND dense_dimension = ?
                """,
                (
                    project_id,
                    snapshot_id,
                    index_version,
                    dense_model,
                    dense_model_revision,
                    dense_dimension,
                ),
            ).fetchone()
        if row is None:
            return None
        return StoredManifest(
            manifest_sha256=row["manifest_sha256"],
            document_count=row["document_count"],
            indexed_document_count=row["indexed_document_count"],
            dense_complete=bool(row["dense_complete"]),
            collection_name=row["collection_name"],
        )

    def get_manifests(
        self,
        *,
        project_id: str,
        snapshot_ids: Sequence[str],
        index_version: str,
        dense_model: str,
        dense_model_revision: str,
        dense_dimension: int,
    ) -> dict[str, StoredManifest]:
        """Resolve a bounded manifest batch with one connection and one lookup."""

        if not snapshot_ids:
            return {}
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TEMP TABLE requested_retrieval_snapshots (
                  snapshot_id TEXT PRIMARY KEY
                ) WITHOUT ROWID
                """
            )
            connection.executemany(
                "INSERT INTO requested_retrieval_snapshots (snapshot_id) VALUES (?)",
                ((snapshot_id,) for snapshot_id in snapshot_ids),
            )
            rows = connection.execute(
                """
                SELECT m.snapshot_id, m.manifest_sha256, m.document_count,
                       m.indexed_document_count, m.dense_complete,
                       m.collection_name
                FROM requested_retrieval_snapshots requested
                CROSS JOIN retrieval_manifests m
                  ON m.snapshot_id = requested.snapshot_id
                WHERE m.project_id = ? AND m.index_version = ?
                  AND m.dense_model = ? AND m.dense_model_revision = ?
                  AND m.dense_dimension = ?
                """,
                (
                    project_id,
                    index_version,
                    dense_model,
                    dense_model_revision,
                    dense_dimension,
                ),
            ).fetchall()
        return {
            row["snapshot_id"]: StoredManifest(
                manifest_sha256=row["manifest_sha256"],
                document_count=row["document_count"],
                indexed_document_count=row["indexed_document_count"],
                dense_complete=bool(row["dense_complete"]),
                collection_name=row["collection_name"],
            )
            for row in rows
        }

    def validate_projection_chunk(
        self,
        request: IndexSnapshotRequest,
        *,
        dense_model: str,
        dense_model_revision: str,
        dense_dimension: int,
    ) -> tuple[str, ...]:
        """Fail before neural work when a chunk violates snapshot immutability."""

        manifest = request.manifest
        manifest_hash = self.manifest_sha256(manifest)
        with self._connect() as connection:
            stored_manifest = connection.execute(
                """
                SELECT manifest_sha256, document_count
                FROM retrieval_manifests
                WHERE project_id = ? AND snapshot_id = ? AND index_version = ?
                  AND dense_model = ? AND dense_model_revision = ?
                  AND dense_dimension = ?
                """,
                (
                    request.project_id,
                    manifest.snapshot_id,
                    manifest.index_version,
                    dense_model,
                    dense_model_revision,
                    dense_dimension,
                ),
            ).fetchone()
            if stored_manifest is not None and (
                stored_manifest["manifest_sha256"] != manifest_hash
                or stored_manifest["document_count"] != manifest.document_count
            ):
                raise ValueError("immutable retrieval manifest conflict")
            count = connection.execute(
                """
                SELECT COUNT(*) AS value
                FROM retrieval_documents
                WHERE project_id = ? AND snapshot_id = ? AND index_version = ?
                  AND dense_model = ? AND dense_model_revision = ?
                  AND dense_dimension = ?
                """,
                (
                    request.project_id,
                    manifest.snapshot_id,
                    manifest.index_version,
                    dense_model,
                    dense_model_revision,
                    dense_dimension,
                ),
            ).fetchone()
            indexed_count = int(count["value"])
            existing = self._projection_documents(
                connection,
                request,
                dense_model=dense_model,
                dense_model_revision=dense_model_revision,
                dense_dimension=dense_dimension,
            )
            new_count = 0
            document_jsons = tuple(
                canonical_json(document.model_dump(mode="json"))
                for document in request.documents
            )
            for document, document_json in zip(
                request.documents, document_jsons, strict=True
            ):
                prior = existing.get(document.claim_id)
                if prior is None:
                    new_count += 1
                elif prior["document_json"] != document_json:
                    raise ValueError("immutable retrieval document conflict")
            if indexed_count + new_count > manifest.document_count:
                raise ValueError("indexed documents exceed immutable manifest total")
        return document_jsons

    def upsert_projection(
        self,
        request: IndexSnapshotRequest,
        *,
        dense_model: str,
        dense_model_revision: str,
        dense_dimension: int,
        collection_name: str,
        dense_complete: bool,
        point_ids: Sequence[str | None],
        document_jsons: Sequence[str],
        ranking_feature_jsons: Sequence[str] | None = None,
    ) -> None:
        if len(point_ids) != len(request.documents) or len(document_jsons) != len(
            request.documents
        ):
            raise ValueError(
                "point IDs and canonical documents must exactly cover indexed documents"
            )
        if ranking_feature_jsons is not None and len(ranking_feature_jsons) != len(
            request.documents
        ):
            raise ValueError("ranking features must exactly cover indexed documents")
        if ranking_feature_jsons is not None:
            self._ensure_ranking_feature_schema()
        manifest = request.manifest
        manifest_hash = self.manifest_sha256(manifest)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            stored_manifest = connection.execute(
                """
                SELECT manifest_sha256, document_count
                FROM retrieval_manifests
                WHERE project_id = ? AND snapshot_id = ? AND index_version = ?
                  AND dense_model = ? AND dense_model_revision = ?
                  AND dense_dimension = ?
                """,
                (
                    request.project_id,
                    manifest.snapshot_id,
                    manifest.index_version,
                    dense_model,
                    dense_model_revision,
                    dense_dimension,
                ),
            ).fetchone()
            if stored_manifest is not None and (
                stored_manifest["manifest_sha256"] != manifest_hash
                or stored_manifest["document_count"] != manifest.document_count
            ):
                raise ValueError("immutable retrieval manifest conflict")
            existing = self._projection_documents(
                connection,
                request,
                dense_model=dense_model,
                dense_model_revision=dense_model_revision,
                dense_dimension=dense_dimension,
            )
            feature_values: Sequence[str | None] = (
                ranking_feature_jsons
                if ranking_feature_jsons is not None
                else (None,) * len(request.documents)
            )
            for document, point_id, document_json, feature_json in zip(
                request.documents,
                point_ids,
                document_jsons,
                feature_values,
                strict=True,
            ):
                prior = existing.get(document.claim_id)
                if prior is not None:
                    if prior["document_json"] != document_json:
                        raise ValueError("immutable retrieval document conflict")
                    if dense_complete:
                        connection.execute(
                            """
                            UPDATE retrieval_documents
                            SET point_id = ?, dense_complete = 1
                            WHERE rowid = ?
                            """,
                            (point_id, prior["rowid"]),
                        )
                    if feature_json is not None:
                        self._upsert_ranking_feature(
                            connection,
                            project_id=request.project_id,
                            document=document,
                            dense_model=dense_model,
                            dense_model_revision=dense_model_revision,
                            dense_dimension=dense_dimension,
                            projection_json=feature_json,
                        )
                    continue
                cursor = connection.execute(
                    """
                    INSERT INTO retrieval_documents (
                      project_id, snapshot_id, index_version, dense_model,
                      dense_model_revision, dense_dimension, claim_id,
                      paper_id, paper_family_id, claim_type, polarity,
                      scope_filter_json, point_id, dense_complete, document_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request.project_id,
                        document.snapshot_id,
                        document.index_version,
                        dense_model,
                        dense_model_revision,
                        dense_dimension,
                        document.claim_id,
                        document.paper_id,
                        document.paper_family_id,
                        document.claim_type,
                        document.polarity,
                        canonical_json(_scope_index_payload(document.scope)),
                        point_id,
                        int(dense_complete),
                        document_json,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO retrieval_claim_fts (
                      rowid, project_id, snapshot_id, dense_model,
                      entities, key_terms, abbreviations, original_claim,
                      canonical_claim, paper_title, evidence_context,
                      qualifiers, scope
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cursor.lastrowid,
                        request.project_id,
                        document.snapshot_id,
                        dense_model,
                        *lexical_fields(document),
                    ),
                )
                if feature_json is not None:
                    self._upsert_ranking_feature(
                        connection,
                        project_id=request.project_id,
                        document=document,
                        dense_model=dense_model,
                        dense_model_revision=dense_model_revision,
                        dense_dimension=dense_dimension,
                        projection_json=feature_json,
                    )
            counts = connection.execute(
                """
                SELECT COUNT(*) AS indexed_count,
                       COALESCE(SUM(dense_complete), 0) AS dense_count
                FROM retrieval_documents
                WHERE project_id = ? AND snapshot_id = ? AND index_version = ?
                  AND dense_model = ? AND dense_model_revision = ?
                  AND dense_dimension = ?
                """,
                (
                    request.project_id,
                    manifest.snapshot_id,
                    manifest.index_version,
                    dense_model,
                    dense_model_revision,
                    dense_dimension,
                ),
            ).fetchone()
            indexed_count = int(counts["indexed_count"])
            dense_count = int(counts["dense_count"])
            if indexed_count > manifest.document_count:
                raise ValueError("indexed documents exceed immutable manifest total")
            projection_dense_complete = (
                indexed_count == manifest.document_count
                and dense_count == manifest.document_count
            )
            connection.execute(
                """
                INSERT INTO retrieval_manifests (
                  project_id, snapshot_id, index_version, dense_model,
                  dense_model_revision, dense_dimension, collection_name,
                  manifest_sha256,
                  manifest_json, document_count, indexed_document_count,
                  dense_complete, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                  project_id, snapshot_id, index_version,
                  dense_model, dense_model_revision, dense_dimension
                ) DO UPDATE SET
                  collection_name = excluded.collection_name,
                  manifest_sha256 = excluded.manifest_sha256,
                  manifest_json = excluded.manifest_json,
                  document_count = excluded.document_count,
                  indexed_document_count = excluded.indexed_document_count,
                  dense_complete = excluded.dense_complete,
                  created_at = excluded.created_at
                """,
                (
                    request.project_id,
                    manifest.snapshot_id,
                    manifest.index_version,
                    dense_model,
                    dense_model_revision,
                    dense_dimension,
                    collection_name,
                    manifest_hash,
                    canonical_json(manifest.model_dump(mode="json")),
                    manifest.document_count,
                    indexed_count,
                    int(projection_dense_complete),
                    manifest.created_at,
                ),
            )
            connection.commit()

    @staticmethod
    def _upsert_ranking_feature(
        connection: sqlite3.Connection,
        *,
        project_id: str,
        document: ClaimSearchDocument,
        dense_model: str,
        dense_model_revision: str,
        dense_dimension: int,
        projection_json: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO retrieval_ranking_features (
              project_id, snapshot_id, index_version, dense_model,
              dense_model_revision, dense_dimension, claim_id, projection_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (
              project_id, snapshot_id, index_version,
              dense_model, dense_model_revision, dense_dimension, claim_id
            ) DO UPDATE SET projection_json = excluded.projection_json
            """,
            (
                project_id,
                document.snapshot_id,
                document.index_version,
                dense_model,
                dense_model_revision,
                dense_dimension,
                document.claim_id,
                projection_json,
            ),
        )

    def complete_ranking_feature_project_ids(
        self,
        *,
        dense_model: str,
        dense_model_revision: str,
        dense_dimension: int,
    ) -> tuple[str, ...]:
        """Return projects with at least one complete immutable manifest."""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT project_id
                FROM retrieval_manifests
                WHERE dense_model = ? AND dense_model_revision = ?
                  AND dense_dimension = ?
                  AND indexed_document_count = document_count
                ORDER BY project_id ASC
                """,
                (dense_model, dense_model_revision, dense_dimension),
            ).fetchall()
        return tuple(row["project_id"] for row in rows)

    @staticmethod
    def _prepare_feature_receipt_schema(connection: sqlite3.Connection) -> None:
        """Install transactional invalidation before trusting any stored receipt."""

        columns = ", ".join(_FEATURE_IDENTITY)
        declarations = ", ".join(
            f"{name} {'INTEGER' if name == 'dense_dimension' else 'TEXT'} NOT NULL"
            for name in _FEATURE_IDENTITY
        )
        tables = {
            "retrieval_feature_generations": "generation INTEGER NOT NULL",
            "retrieval_feature_receipts": (
                "generation INTEGER NOT NULL, contract_sha256 TEXT NOT NULL, "
                "source_sha256 TEXT NOT NULL, sidecar_sha256 TEXT NOT NULL, "
                "document_count INTEGER NOT NULL"
            ),
        }
        reset = False
        for name, extra in tables.items():
            if not connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone():
                reset = True
            connection.execute(
                f"CREATE TABLE IF NOT EXISTS {name} "
                f"({declarations}, {extra}, PRIMARY KEY ({columns})) WITHOUT ROWID"
            )
        for table in (
            "retrieval_documents",
            "retrieval_ranking_features",
            "retrieval_manifests",
        ):
            for operation in ("INSERT", "UPDATE", "DELETE"):
                name = f"{table}_feature_generation_{operation.lower()}"
                statements = []
                if operation == "INSERT":
                    # REPLACE can remove a row by explicit rowid without firing
                    # DELETE triggers when recursive_triggers is off (SQLite's
                    # default). Invalidate that previous identity before loss.
                    statements.append(
                        "INSERT INTO retrieval_feature_generations "
                        f"({columns}, generation) SELECT {columns}, 1 FROM {table} "
                        f"WHERE rowid=NEW.rowid ON CONFLICT ({columns}) DO UPDATE "
                        "SET generation = generation + 1;"
                    )
                for source in (
                    ("OLD", "NEW")
                    if operation == "UPDATE"
                    else ("OLD",)
                    if operation == "DELETE"
                    else ("NEW",)
                ):
                    values = ", ".join(
                        f"{source}.{column}" for column in _FEATURE_IDENTITY
                    )
                    statements.append(
                        "INSERT INTO retrieval_feature_generations "
                        f"({columns}, generation) "
                        f"VALUES ({values}, 1) ON CONFLICT ({columns}) DO UPDATE "
                        "SET generation = generation + 1;"
                    )
                timing = "BEFORE" if operation == "INSERT" else "AFTER"
                sql = (
                    f"CREATE TRIGGER {name} {timing} {operation} ON {table} BEGIN "
                    + " ".join(statements)
                    + " END"
                )
                prior = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",
                    (name,),
                ).fetchone()
                if prior is None or prior[0] != sql:
                    reset = True
                    connection.execute(f"DROP TRIGGER IF EXISTS {name}")
                    connection.execute(sql)
        if reset:
            # Unknown/old databases and missing/changed triggers cannot reuse
            # receipts. Enumerate identities once, including orphan and empty
            # partitions. No source/sidecar bodies are loaded here.
            connection.execute("DELETE FROM retrieval_feature_receipts")
            for table in (
                "retrieval_documents",
                "retrieval_ranking_features",
                "retrieval_manifests",
            ):
                connection.execute(
                    "INSERT OR IGNORE INTO retrieval_feature_generations "
                    f"({columns}, generation) "
                    f"SELECT {columns}, 1 FROM {table} GROUP BY {columns}"
                )

    def prepare_ranking_feature_receipts(
        self,
        *,
        project_ids: Sequence[str] | None,
        dense_model: str,
        dense_model_revision: str,
        dense_dimension: int,
        contract_sha256: str,
        prepare: Callable[
            [ClaimSearchDocument, str, LoadedRankingFeatureProjection | None], str
        ],
    ) -> RankingFeaturePreflightReceipt:
        """Validate dirty snapshots in bounded pages under one write transaction.

        Generations bind every source, sidecar and manifest mutation. A clean
        receipt reads only metadata; its complete content digests were produced
        under the same transaction that certified that generation and contract.
        """
        if not self._ranking_features_enabled:
            raise RuntimeError("materialized ranking features are disabled")
        identity_where = " AND ".join(f"{key}=?" for key in _FEATURE_IDENTITY)

        def identity_join(left: str, right: str) -> str:
            return " AND ".join(
                f"{left}.{key}={right}.{key}" for key in _FEATURE_IDENTITY
            )

        source_where = " AND ".join("d." + key + "=?" for key in _FEATURE_IDENTITY)
        inserted = updated = unchanged = deleted_orphans = source_count = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            table_before = bool(
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='retrieval_ranking_features'"
                ).fetchone()
            )
            trigger_before = self._ranking_feature_delete_trigger_is_canonical(
                connection
            )
            try:
                self._create_ranking_feature_schema(connection)
                self._prepare_feature_receipt_schema(connection)
                if not trigger_before or not table_before:
                    connection.execute("DELETE FROM retrieval_feature_receipts")
                partitions = connection.execute(
                    f"""SELECT g.*, m.document_count AS expected_count,
                        m.indexed_document_count AS indexed_count,
                        r.generation AS verified_generation, r.contract_sha256,
                        r.document_count AS verified_count
                    FROM retrieval_feature_generations g
                    LEFT JOIN retrieval_manifests m ON {identity_join("g", "m")}
                    LEFT JOIN retrieval_feature_receipts r ON {identity_join("g", "r")}
                    WHERE g.dense_model=? AND g.dense_model_revision=?
                      AND g.dense_dimension=?
                    ORDER BY {", ".join("g." + key for key in _FEATURE_IDENTITY)}""",
                    (dense_model, dense_model_revision, dense_dimension),
                ).fetchall()
                available = {
                    row["project_id"]
                    for row in partitions
                    if row["expected_count"] is not None
                    and row["expected_count"] == row["indexed_count"]
                }
                selected = available if project_ids is None else set(project_ids)
                if any(not item for item in selected):
                    raise ValueError("ranking feature project IDs must be nonempty")
                if selected - available:
                    raise ValueError(
                        "ranking feature projects have no complete local index"
                    )
                for partition in partitions:
                    if (
                        project_ids is not None
                        and partition["project_id"] not in selected
                    ):
                        continue
                    identity = tuple(partition[key] for key in _FEATURE_IDENTITY)
                    complete = (
                        partition["expected_count"] is not None
                        and partition["expected_count"] == partition["indexed_count"]
                    )
                    expected = partition["expected_count"] if complete else 0
                    if (
                        partition["generation"] == partition["verified_generation"]
                        and partition["contract_sha256"] == contract_sha256
                        and partition["verified_count"] == expected
                    ):
                        source_count += expected
                        unchanged += expected
                        continue
                    deleted_orphans += connection.execute(
                        f"""DELETE FROM retrieval_ranking_features
                        WHERE {identity_where} AND NOT EXISTS (
                            SELECT 1 FROM retrieval_documents d
                            WHERE {identity_join("d", "retrieval_ranking_features")}
                            AND d.claim_id=retrieval_ranking_features.claim_id)""",
                        identity,
                    ).rowcount
                    source_digest = hashlib.sha256()
                    sidecar_digest = hashlib.sha256()
                    observed = 0
                    if complete:
                        cursor = connection.execute(
                            f"""SELECT d.claim_id, d.document_json, f.projection_json
                            FROM retrieval_documents d
                            LEFT JOIN retrieval_ranking_features f
                            ON {identity_join("d", "f")} AND d.claim_id=f.claim_id
                            WHERE {source_where}
                            ORDER BY d.claim_id""",
                            identity,
                        )
                        while rows := cursor.fetchmany(128):
                            for row in rows:
                                source = row["document_json"]
                                document = ClaimSearchDocument.model_validate(
                                    json.loads(source), strict=True
                                )
                                if (
                                    document.snapshot_id,
                                    document.index_version,
                                    document.claim_id,
                                ) != (
                                    partition["snapshot_id"],
                                    partition["index_version"],
                                    row["claim_id"],
                                ):
                                    raise ValueError(
                                        "ranking feature source identity changed"
                                    )
                                prior = row["projection_json"]
                                loaded = None
                                if isinstance(prior, str):
                                    try:
                                        loaded = LoadedRankingFeatureProjection(
                                            projection_json=prior,
                                            projection_payload=json.loads(prior),
                                            observed_source_document_json_sha256=hashlib.sha256(
                                                source.encode()
                                            ).hexdigest(),
                                        )
                                    except ValueError:
                                        loaded = None
                                projection = prepare(document, source, loaded)
                                if prior == projection:
                                    unchanged += 1
                                else:
                                    inserted += int(prior is None)
                                    updated += int(prior is not None)
                                    self._upsert_ranking_feature(
                                        connection,
                                        project_id=partition["project_id"],
                                        document=document,
                                        dense_model=dense_model,
                                        dense_model_revision=dense_model_revision,
                                        dense_dimension=dense_dimension,
                                        projection_json=projection,
                                    )
                                    stored = connection.execute(
                                        "SELECT projection_json "
                                        "FROM retrieval_ranking_features "
                                        f"WHERE {identity_where} AND claim_id=?",
                                        (*identity, row["claim_id"]),
                                    ).fetchone()
                                    if stored is None or stored[0] != projection:
                                        raise RuntimeError(
                                            "ranking feature preflight did not converge"
                                        )
                                for digest, content in (
                                    (source_digest, source),
                                    (sidecar_digest, projection),
                                ):
                                    for part in (row["claim_id"], content):
                                        encoded = part.encode("utf-8")
                                        digest.update(len(encoded).to_bytes(8, "big"))
                                        digest.update(encoded)
                                observed += 1
                        if observed != expected:
                            raise ValueError(
                                "ranking features must exactly cover "
                                "complete source documents"
                            )
                    source_count += observed
                    generation = connection.execute(
                        "SELECT generation FROM retrieval_feature_generations "
                        f"WHERE {identity_where}",
                        identity,
                    ).fetchone()[0]
                    connection.execute(
                        "INSERT OR REPLACE INTO retrieval_feature_receipts "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            *identity,
                            generation,
                            contract_sha256,
                            source_digest.hexdigest(),
                            sidecar_digest.hexdigest(),
                            observed,
                        ),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                self._ranking_feature_table_available = table_before
                self._ranking_feature_delete_trigger_available = trigger_before
                raise
        self._ranking_feature_table_available = True
        self._ranking_feature_delete_trigger_available = True
        return RankingFeaturePreflightReceipt(
            project_ids=tuple(sorted(selected)),
            source_documents=source_count,
            inserted=inserted,
            updated=updated,
            unchanged=unchanged,
            deleted_orphans=deleted_orphans,
            schema_created=not table_before,
            delete_trigger_created=not trigger_before,
        )

    def preflight_ranking_feature_projections(
        self,
        *,
        project_ids: Sequence[str],
        cleanup_all_model_orphans: bool = False,
        dense_model: str,
        dense_model_revision: str,
        dense_dimension: int,
        projections: Sequence[tuple[str, ClaimSearchDocument, str, str]],
    ) -> RankingFeaturePreflightReceipt:
        """Atomically converge complete documents to exact expected sidecars."""

        if not self._ranking_features_enabled:
            raise RuntimeError("materialized ranking features are disabled")
        if type(cleanup_all_model_orphans) is not bool:
            raise ValueError("cleanup_all_model_orphans must be Boolean")
        normalized_projects = tuple(sorted(set(project_ids)))
        if any(not project_id for project_id in normalized_projects):
            raise ValueError("ranking feature project IDs must be nonempty")
        incoming: dict[tuple[str, str, str, str], tuple[str, str]] = {}
        for project_id, document, source_document_json, projection_json in projections:
            if project_id not in normalized_projects:
                raise ValueError("ranking feature projection project is not selected")
            try:
                projection_payload = json.loads(projection_json)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "ranking feature projection must be valid JSON"
                ) from exc
            if not isinstance(projection_payload, dict):
                raise ValueError("ranking feature projection must be a JSON object")
            key = (
                project_id,
                document.snapshot_id,
                document.index_version,
                document.claim_id,
            )
            if key in incoming:
                raise ValueError("duplicate ranking feature projection")
            incoming[key] = (source_document_json, projection_json)

        table_available_before = False
        trigger_available_before = False
        inserted = 0
        updated = 0
        unchanged = 0
        deleted_orphans = 0
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                table_available_before = bool(
                    connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                        "AND name = 'retrieval_ranking_features'"
                    ).fetchone()
                )
                trigger_available_before = bool(
                    self._ranking_feature_delete_trigger_is_canonical(connection)
                )
                self._create_ranking_feature_schema(connection)
                connection.execute(
                    "CREATE TEMP TABLE requested_ranking_feature_projects "
                    "(project_id TEXT PRIMARY KEY) WITHOUT ROWID"
                )
                connection.executemany(
                    "INSERT INTO requested_ranking_feature_projects (project_id) "
                    "VALUES (?)",
                    ((project_id,) for project_id in normalized_projects),
                )
                source_rows = connection.execute(
                    """
                    SELECT d.project_id, d.snapshot_id, d.index_version,
                           d.claim_id, d.document_json
                    FROM retrieval_documents d
                    JOIN requested_ranking_feature_projects p
                      ON p.project_id = d.project_id
                    JOIN retrieval_manifests m
                      ON m.project_id = d.project_id
                     AND m.snapshot_id = d.snapshot_id
                     AND m.index_version = d.index_version
                     AND m.dense_model = d.dense_model
                     AND m.dense_model_revision = d.dense_model_revision
                     AND m.dense_dimension = d.dense_dimension
                    WHERE d.dense_model = ? AND d.dense_model_revision = ?
                      AND d.dense_dimension = ?
                      AND m.indexed_document_count = m.document_count
                    ORDER BY d.project_id, d.snapshot_id, d.claim_id
                    """,
                    (dense_model, dense_model_revision, dense_dimension),
                ).fetchall()
                expected_sources = {
                    (
                        row["project_id"],
                        row["snapshot_id"],
                        row["index_version"],
                        row["claim_id"],
                    ): row["document_json"]
                    for row in source_rows
                }
                if set(incoming) != set(expected_sources):
                    raise ValueError(
                        "ranking features must exactly cover complete source documents"
                    )
                if any(
                    incoming[key][0] != source_document_json
                    for key, source_document_json in expected_sources.items()
                ):
                    raise ValueError("ranking feature source document changed")

                deleted_orphans = connection.execute(
                    """
                    DELETE FROM retrieval_ranking_features
                    WHERE (
                      ? OR project_id IN (
                        SELECT project_id FROM requested_ranking_feature_projects
                      )
                    )
                      AND dense_model = ? AND dense_model_revision = ?
                      AND dense_dimension = ?
                      AND NOT EXISTS (
                        SELECT 1
                        FROM retrieval_documents d
                        WHERE d.project_id = retrieval_ranking_features.project_id
                          AND d.snapshot_id = retrieval_ranking_features.snapshot_id
                          AND d.index_version = retrieval_ranking_features.index_version
                          AND d.dense_model = retrieval_ranking_features.dense_model
                          AND d.dense_model_revision =
                            retrieval_ranking_features.dense_model_revision
                          AND d.dense_dimension =
                            retrieval_ranking_features.dense_dimension
                          AND d.claim_id = retrieval_ranking_features.claim_id
                      )
                    """,
                    (
                        int(cleanup_all_model_orphans),
                        dense_model,
                        dense_model_revision,
                        dense_dimension,
                    ),
                ).rowcount

                existing_rows = connection.execute(
                    """
                    SELECT f.project_id, f.snapshot_id, f.index_version,
                           f.claim_id, f.projection_json
                    FROM retrieval_ranking_features f
                    JOIN requested_ranking_feature_projects p
                      ON p.project_id = f.project_id
                    WHERE f.dense_model = ? AND f.dense_model_revision = ?
                      AND f.dense_dimension = ?
                    """,
                    (dense_model, dense_model_revision, dense_dimension),
                ).fetchall()
                existing = {
                    (
                        row["project_id"],
                        row["snapshot_id"],
                        row["index_version"],
                        row["claim_id"],
                    ): row["projection_json"]
                    for row in existing_rows
                }
                documents = {
                    (
                        project_id,
                        document.snapshot_id,
                        document.index_version,
                        document.claim_id,
                    ): document
                    for project_id, document, _source, _projection in projections
                }
                for key in sorted(incoming):
                    projection_json = incoming[key][1]
                    prior = existing.get(key)
                    if prior == projection_json:
                        unchanged += 1
                        continue
                    if prior is None:
                        inserted += 1
                    else:
                        updated += 1
                    self._upsert_ranking_feature(
                        connection,
                        project_id=key[0],
                        document=documents[key],
                        dense_model=dense_model,
                        dense_model_revision=dense_model_revision,
                        dense_dimension=dense_dimension,
                        projection_json=projection_json,
                    )

                stored_rows = connection.execute(
                    """
                    SELECT f.project_id, f.snapshot_id, f.index_version,
                           f.claim_id, f.projection_json
                    FROM retrieval_ranking_features f
                    JOIN requested_ranking_feature_projects p
                      ON p.project_id = f.project_id
                    WHERE f.dense_model = ? AND f.dense_model_revision = ?
                      AND f.dense_dimension = ?
                    """,
                    (dense_model, dense_model_revision, dense_dimension),
                ).fetchall()
                stored = {
                    (
                        row["project_id"],
                        row["snapshot_id"],
                        row["index_version"],
                        row["claim_id"],
                    ): row["projection_json"]
                    for row in stored_rows
                    if (
                        row["project_id"],
                        row["snapshot_id"],
                        row["index_version"],
                        row["claim_id"],
                    )
                    in incoming
                }
                if stored != {key: value[1] for key, value in incoming.items()}:
                    raise RuntimeError("ranking feature preflight did not converge")
                remaining_orphans = connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM retrieval_ranking_features f
                    WHERE (
                      ? OR f.project_id IN (
                        SELECT project_id FROM requested_ranking_feature_projects
                      )
                    )
                      AND f.dense_model = ? AND f.dense_model_revision = ?
                      AND f.dense_dimension = ?
                      AND NOT EXISTS (
                        SELECT 1
                        FROM retrieval_documents d
                        WHERE d.project_id = f.project_id
                          AND d.snapshot_id = f.snapshot_id
                          AND d.index_version = f.index_version
                          AND d.dense_model = f.dense_model
                          AND d.dense_model_revision = f.dense_model_revision
                          AND d.dense_dimension = f.dense_dimension
                          AND d.claim_id = f.claim_id
                      )
                    """,
                    (
                        int(cleanup_all_model_orphans),
                        dense_model,
                        dense_model_revision,
                        dense_dimension,
                    ),
                ).fetchone()[0]
                if remaining_orphans:
                    raise RuntimeError(
                        "ranking feature orphan cleanup did not converge"
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                self._ranking_feature_table_available = table_available_before
                self._ranking_feature_delete_trigger_available = (
                    trigger_available_before
                )
                raise
        self._ranking_feature_table_available = True
        self._ranking_feature_delete_trigger_available = True
        return RankingFeaturePreflightReceipt(
            project_ids=normalized_projects,
            source_documents=len(incoming),
            inserted=inserted,
            updated=updated,
            unchanged=unchanged,
            deleted_orphans=deleted_orphans,
            schema_created=not table_available_before,
            delete_trigger_created=not trigger_available_before,
        )

    def load_ranking_feature_source_documents(
        self,
        *,
        project_id: str,
        dense_model: str,
        dense_model_revision: str,
        dense_dimension: int,
        ranking_feature_sink: (
            dict[tuple[str, str], LoadedRankingFeatureProjection] | None
        ) = None,
    ) -> list[tuple[ClaimSearchDocument, str]]:
        """Load ordered source bytes and optional sidecars from one SQL snapshot."""

        with self._connect() as connection:
            load_features = ranking_feature_sink is not None and bool(
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'retrieval_ranking_features'"
                ).fetchone()
            )
            feature_select = (
                ", f.projection_json AS ranking_feature_json" if load_features else ""
            )
            feature_join = (
                """
                LEFT JOIN retrieval_ranking_features f
                  ON f.project_id = d.project_id
                 AND f.snapshot_id = d.snapshot_id
                 AND f.index_version = d.index_version
                 AND f.dense_model = d.dense_model
                 AND f.dense_model_revision = d.dense_model_revision
                 AND f.dense_dimension = d.dense_dimension
                 AND f.claim_id = d.claim_id
                """
                if load_features
                else ""
            )
            rows = connection.execute(
                f"""
                SELECT d.document_json{feature_select}
                FROM retrieval_documents d
                JOIN retrieval_manifests m
                  ON m.project_id = d.project_id
                 AND m.snapshot_id = d.snapshot_id
                 AND m.index_version = d.index_version
                 AND m.dense_model = d.dense_model
                 AND m.dense_model_revision = d.dense_model_revision
                 AND m.dense_dimension = d.dense_dimension
                {feature_join}
                WHERE d.project_id = ? AND d.dense_model = ?
                  AND d.dense_model_revision = ? AND d.dense_dimension = ?
                  AND m.indexed_document_count = m.document_count
                ORDER BY d.snapshot_id ASC, d.claim_id ASC
                """,
                (project_id, dense_model, dense_model_revision, dense_dimension),
            ).fetchall()
        documents = []
        for row in rows:
            source_json = row["document_json"]
            document = ClaimSearchDocument.model_validate(
                json.loads(source_json), strict=True
            )
            documents.append((document, source_json))
            if load_features and ranking_feature_sink is not None:
                projection_json = row["ranking_feature_json"]
                # Missing, corrupt, and non-text sidecars must be rebuilt.
                try:
                    if not isinstance(projection_json, str):
                        raise TypeError("ranking feature projection is not text")
                    payload = json.loads(projection_json)
                except (TypeError, ValueError):
                    continue
                ranking_feature_sink[(document.snapshot_id, document.claim_id)] = (
                    LoadedRankingFeatureProjection(
                        projection_json=projection_json,
                        projection_payload=payload,
                        observed_source_document_json_sha256=hashlib.sha256(
                            source_json.encode("utf-8")
                        ).hexdigest(),
                    )
                )
        return documents

    def upsert_ranking_feature_projections(
        self,
        *,
        project_id: str,
        dense_model: str,
        dense_model_revision: str,
        dense_dimension: int,
        projections: Sequence[tuple[ClaimSearchDocument, str]],
    ) -> None:
        """Persist a complete, precomputed sidecar batch transactionally."""

        self._ensure_ranking_feature_schema()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for document, projection_json in projections:
                self._upsert_ranking_feature(
                    connection,
                    project_id=project_id,
                    document=document,
                    dense_model=dense_model,
                    dense_model_revision=dense_model_revision,
                    dense_dimension=dense_dimension,
                    projection_json=projection_json,
                )
            connection.commit()

    @staticmethod
    def _projection_documents(
        connection: sqlite3.Connection,
        request: IndexSnapshotRequest,
        *,
        dense_model: str,
        dense_model_revision: str,
        dense_dimension: int,
    ) -> dict[str, sqlite3.Row]:
        """Load a bounded indexing chunk's prior rows with one SQL lookup."""

        if not request.documents:
            return {}
        connection.execute(
            "CREATE TEMP TABLE requested_projection_claims "
            "(claim_id TEXT PRIMARY KEY) WITHOUT ROWID"
        )
        connection.executemany(
            "INSERT INTO requested_projection_claims (claim_id) VALUES (?)",
            ((document.claim_id,) for document in request.documents),
        )
        rows = connection.execute(
            """
            SELECT d.claim_id, d.rowid, d.document_json,
                   d.point_id, d.dense_complete
            FROM retrieval_documents d
            JOIN requested_projection_claims requested USING (claim_id)
            WHERE d.project_id = ? AND d.snapshot_id = ? AND d.index_version = ?
              AND d.dense_model = ? AND d.dense_model_revision = ?
              AND d.dense_dimension = ?
            """,
            (
                request.project_id,
                request.manifest.snapshot_id,
                request.manifest.index_version,
                dense_model,
                dense_model_revision,
                dense_dimension,
            ),
        ).fetchall()
        return {row["claim_id"]: row for row in rows}

    def resolve_snapshots(
        self,
        *,
        project_id: str,
        index_version: str,
        dense_model: str,
        dense_model_revision: str,
        dense_dimension: int,
        explicit_snapshot_ids: Sequence[str] | None,
        paper_ids: Sequence[str],
        paper_family_ids: Sequence[str],
    ) -> list[ResolvedSnapshot]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT snapshot_id, manifest_sha256, dense_complete, created_at
                FROM retrieval_manifests
                WHERE project_id = ? AND index_version = ? AND dense_model = ?
                  AND dense_model_revision = ? AND dense_dimension = ?
                  AND indexed_document_count = document_count
                ORDER BY julianday(created_at) DESC, created_at DESC, snapshot_id DESC
                """,
                (
                    project_id,
                    index_version,
                    dense_model,
                    dense_model_revision,
                    dense_dimension,
                ),
            ).fetchall()
            # Paper identities are immutable per manifest hash: parse each
            # manifest once per process instead of on every search (RTS-9).
            missing = [
                row["manifest_sha256"]
                for row in rows
                if row["manifest_sha256"] not in self._manifest_identities
            ]
            for offset in range(0, len(missing), 500):
                chunk = missing[offset : offset + 500]
                for found in connection.execute(
                    "SELECT manifest_sha256, manifest_json FROM retrieval_manifests "
                    "WHERE manifest_sha256 IN (SELECT value FROM json_each(?))",
                    (json.dumps(chunk),),
                ):
                    payload = json.loads(found["manifest_json"])
                    self._manifest_identities[found["manifest_sha256"]] = (
                        payload["paper_id"],
                        payload["paper_family_id"],
                    )
        explicit = None if explicit_snapshot_ids is None else set(explicit_snapshot_ids)
        allowed_papers = set(paper_ids)
        allowed_families = set(paper_family_ids)
        candidates: list[ResolvedSnapshot] = []
        for row in rows:
            if explicit is not None and row["snapshot_id"] not in explicit:
                continue
            paper_id, paper_family_id = self._manifest_identities[
                row["manifest_sha256"]
            ]
            if allowed_papers and paper_id not in allowed_papers:
                continue
            if allowed_families and paper_family_id not in allowed_families:
                continue
            candidates.append(
                ResolvedSnapshot(
                    snapshot_id=row["snapshot_id"],
                    paper_id=paper_id,
                    paper_family_id=paper_family_id,
                    dense_complete=bool(row["dense_complete"]),
                )
            )
        if explicit is not None:
            return candidates
        selected: list[ResolvedSnapshot] = []
        seen_papers: set[str] = set()
        seen_families: set[str] = set()
        for item in candidates:
            if item.paper_id in seen_papers or item.paper_family_id in seen_families:
                continue
            selected.append(item)
            seen_papers.add(item.paper_id)
            seen_families.add(item.paper_family_id)
        return selected

    def lexical_search(
        self,
        *,
        project_id: str,
        snapshot_ids: Sequence[str],
        dense_model: str,
        dense_model_revision: str,
        dense_dimension: int,
        query_values: Sequence[str],
        must_values: Sequence[str],
        exclude_values: Sequence[str],
        claim_types: Sequence[str],
        polarity: str | None,
        scope: SearchScopeFilter | None,
        limit: int,
    ) -> list[ChannelHit]:
        match = fts_query(query_values)
        for required in must_values:
            tokens = lexical_tokens(required, maximum=128)
            if not tokens:
                continue
            clause = " AND ".join(
                '"' + token.replace('"', '""') + '"' for token in tokens
            )
            match = f"({match}) AND ({clause})" if match else f"({clause})"
        for excluded in exclude_values:
            tokens = lexical_tokens(excluded, maximum=128)
            if not tokens:
                continue
            clause = " AND ".join(
                '"' + token.replace('"', '""') + '"' for token in tokens
            )
            if match:
                match = f"({match}) NOT ({clause})"
        if not match or not snapshot_ids:
            return []
        with self._connect() as connection:
            connection.execute(
                "CREATE TEMP TABLE active_retrieval_snapshots "
                "(snapshot_id TEXT PRIMARY KEY) WITHOUT ROWID"
            )
            connection.executemany(
                "INSERT INTO active_retrieval_snapshots (snapshot_id) VALUES (?)",
                ((item,) for item in snapshot_ids),
            )
            filters = [
                "retrieval_claim_fts MATCH ?",
                "d.project_id = ?",
                "d.dense_model = ?",
                "d.dense_model_revision = ?",
                "d.dense_dimension = ?",
            ]
            parameters: list[Any] = [
                match,
                project_id,
                dense_model,
                dense_model_revision,
                dense_dimension,
            ]
            if claim_types:
                filters.append(
                    "d.claim_type IN (" + ",".join("?" for _ in claim_types) + ")"
                )
                parameters.extend(claim_types)
            if polarity is not None:
                filters.append("d.polarity = ?")
                parameters.append(polarity)
            if scope is not None:
                _append_sql_scope_filter(filters, parameters, scope)
            parameters.append(limit)
            rows = connection.execute(
                f"""
                SELECT d.snapshot_id, d.claim_id,
                  bm25(
                    retrieval_claim_fts,
                    0.0, 0.0, 0.0,
                    6.0, 6.0, 6.0, 4.0, 3.5, 2.0, 1.0, 3.0, 4.5
                  ) AS lexical_rank
                FROM retrieval_claim_fts
                JOIN retrieval_documents d ON d.rowid = retrieval_claim_fts.rowid
                JOIN active_retrieval_snapshots a ON a.snapshot_id = d.snapshot_id
                JOIN retrieval_manifests m
                  ON m.project_id = d.project_id
                 AND m.snapshot_id = d.snapshot_id
                 AND m.index_version = d.index_version
                 AND m.dense_model = d.dense_model
                 AND m.dense_model_revision = d.dense_model_revision
                 AND m.dense_dimension = d.dense_dimension
                WHERE {" AND ".join(filters)}
                ORDER BY lexical_rank ASC, d.snapshot_id ASC, d.claim_id ASC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [
            ChannelHit(
                snapshot_id=row["snapshot_id"],
                claim_id=row["claim_id"],
                score=max(0.0, -float(row["lexical_rank"])),
            )
            for row in rows
        ]

    def load_documents(
        self,
        *,
        project_id: str,
        dense_model: str,
        dense_model_revision: str,
        dense_dimension: int,
        keys: Sequence[tuple[str, str]],
        ranking_feature_sink: (
            dict[tuple[str, str], LoadedRankingFeatureProjection] | None
        ) = None,
        ranking_feature_predicate: Callable[[ClaimSearchDocument], bool] | None = None,
    ) -> dict[tuple[str, str], ClaimSearchDocument]:
        if not keys:
            return {}
        with self._connect() as connection:
            connection.execute(
                "CREATE TEMP TABLE requested_retrieval_documents "
                "(snapshot_id TEXT, claim_id TEXT, PRIMARY KEY(snapshot_id, claim_id)) "
                "WITHOUT ROWID"
            )
            connection.executemany(
                "INSERT OR IGNORE INTO requested_retrieval_documents "
                "(snapshot_id, claim_id) VALUES (?, ?)",
                keys,
            )
            load_ranking_features = (
                ranking_feature_sink is not None
                and self._ranking_features_enabled
                and self._ranking_feature_table_available
            )
            projection_select = (
                ", f.projection_json AS ranking_feature_json"
                if load_ranking_features
                else ""
            )
            projection_join = (
                """
                LEFT JOIN retrieval_ranking_features f
                  ON f.project_id = d.project_id
                 AND f.snapshot_id = d.snapshot_id
                 AND f.index_version = d.index_version
                 AND f.dense_model = d.dense_model
                 AND f.dense_model_revision = d.dense_model_revision
                 AND f.dense_dimension = d.dense_dimension
                 AND f.claim_id = d.claim_id
                """
                if load_ranking_features
                else ""
            )
            rows = connection.execute(
                f"""
                SELECT d.snapshot_id, d.claim_id, d.document_json{projection_select}
                FROM requested_retrieval_documents r
                CROSS JOIN retrieval_documents d
                  ON r.snapshot_id = d.snapshot_id AND r.claim_id = d.claim_id
                CROSS JOIN retrieval_manifests m
                  ON m.project_id = d.project_id
                 AND m.snapshot_id = d.snapshot_id
                 AND m.index_version = d.index_version
                 AND m.dense_model = d.dense_model
                 AND m.dense_model_revision = d.dense_model_revision
                 AND m.dense_dimension = d.dense_dimension
                {projection_join}
                WHERE d.project_id = ? AND d.dense_model = ?
                  AND d.dense_model_revision = ? AND d.dense_dimension = ?
                """,
                (project_id, dense_model, dense_model_revision, dense_dimension),
            ).fetchall()
        documents: dict[tuple[str, str], ClaimSearchDocument] = {}
        for row in rows:
            key = (row["snapshot_id"], row["claim_id"])
            source_json = row["document_json"]
            document = ClaimSearchDocument.model_validate(
                json.loads(source_json), strict=True
            )
            documents[key] = document
            if (
                load_ranking_features
                and row["ranking_feature_json"]
                and (
                    ranking_feature_predicate is None
                    or ranking_feature_predicate(document)
                )
            ):
                ranking_feature_json = row["ranking_feature_json"]
                try:
                    if isinstance(ranking_feature_json, bytes):
                        ranking_feature_json = ranking_feature_json.decode("utf-8")
                    if not isinstance(ranking_feature_json, str):
                        raise TypeError("ranking feature projection is not text")
                    feature_payload = json.loads(ranking_feature_json)
                except (TypeError, ValueError):
                    feature_payload = None
                    ranking_feature_json = ""
                ranking_feature_sink[key] = LoadedRankingFeatureProjection(
                    projection_json=ranking_feature_json,
                    projection_payload=feature_payload,
                    observed_source_document_json_sha256=hashlib.sha256(
                        source_json.encode("utf-8")
                    ).hexdigest(),
                )
        return documents

    def load_snapshot_documents(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        dense_model: str,
        dense_model_revision: str,
        dense_dimension: int,
    ) -> list[ClaimSearchDocument]:
        """Load every Claim from one complete immutable snapshot in stable order."""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT d.document_json
                FROM retrieval_documents d
                JOIN retrieval_manifests m
                  ON m.project_id = d.project_id
                 AND m.snapshot_id = d.snapshot_id
                 AND m.index_version = d.index_version
                 AND m.dense_model = d.dense_model
                 AND m.dense_model_revision = d.dense_model_revision
                 AND m.dense_dimension = d.dense_dimension
                WHERE d.project_id = ? AND d.snapshot_id = ?
                  AND d.dense_model = ? AND d.dense_model_revision = ?
                  AND d.dense_dimension = ?
                  AND m.indexed_document_count = m.document_count
                ORDER BY d.claim_id ASC
                """,
                (
                    project_id,
                    snapshot_id,
                    dense_model,
                    dense_model_revision,
                    dense_dimension,
                ),
            ).fetchall()
        return [
            ClaimSearchDocument.model_validate(
                json.loads(row["document_json"]), strict=True
            )
            for row in rows
        ]

    def list_dense_repairs(
        self,
        *,
        dense_model: str,
        dense_model_revision: str,
        dense_dimension: int,
    ) -> list[RepairProjection]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT project_id, manifest_json
                FROM retrieval_manifests
                WHERE dense_model = ? AND dense_model_revision = ?
                  AND dense_dimension = ?
                  AND indexed_document_count = document_count
                  AND dense_complete = 0
                ORDER BY project_id, snapshot_id
                """,
                (dense_model, dense_model_revision, dense_dimension),
            ).fetchall()
        return [
            RepairProjection(
                project_id=row["project_id"],
                manifest=RetrievalManifest.model_validate(
                    json.loads(row["manifest_json"]), strict=True
                ),
            )
            for row in rows
        ]

    def iter_repair_chunks(
        self,
        *,
        project_id: str,
        snapshot_id: str,
        dense_model: str,
        dense_model_revision: str,
        dense_dimension: int,
        maximum_documents: int = 2_048,
        maximum_bytes: int = 7 * 1024 * 1024,
    ) -> Iterator[list[ClaimSearchDocument]]:
        last_rowid = 0
        chunk: list[ClaimSearchDocument] = []
        chunk_bytes = 0
        while True:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT rowid, document_json
                    FROM retrieval_documents
                    WHERE project_id = ? AND snapshot_id = ?
                      AND dense_model = ? AND dense_model_revision = ?
                      AND dense_dimension = ? AND rowid > ?
                    ORDER BY rowid
                    LIMIT 64
                    """,
                    (
                        project_id,
                        snapshot_id,
                        dense_model,
                        dense_model_revision,
                        dense_dimension,
                        last_rowid,
                    ),
                ).fetchall()
            if not rows:
                break
            for row in rows:
                last_rowid = int(row["rowid"])
                document_bytes = len(row["document_json"].encode())
                if chunk and (
                    len(chunk) >= maximum_documents
                    or chunk_bytes + document_bytes > maximum_bytes
                ):
                    yield chunk
                    chunk = []
                    chunk_bytes = 0
                chunk.append(
                    ClaimSearchDocument.model_validate(
                        json.loads(row["document_json"]), strict=True
                    )
                )
                chunk_bytes += document_bytes
        if chunk:
            yield chunk

    def plan_delete_snapshots(
        self, *, project_id: str, snapshot_ids: Sequence[str]
    ) -> DeletedProjection:
        with self._connect() as connection:
            connection.execute(
                "CREATE TEMP TABLE deleting_retrieval_snapshots "
                "(snapshot_id TEXT PRIMARY KEY) WITHOUT ROWID"
            )
            connection.executemany(
                "INSERT INTO deleting_retrieval_snapshots (snapshot_id) VALUES (?)",
                ((item,) for item in snapshot_ids),
            )
            manifests = connection.execute(
                """
                SELECT m.snapshot_id, m.collection_name
                FROM retrieval_manifests m
                JOIN deleting_retrieval_snapshots s USING (snapshot_id)
                WHERE m.project_id = ?
                """,
                (project_id,),
            ).fetchall()
            rows = connection.execute(
                """
                SELECT d.rowid
                FROM retrieval_documents d
                JOIN deleting_retrieval_snapshots s USING (snapshot_id)
                WHERE d.project_id = ?
                """,
                (project_id,),
            ).fetchall()
        return DeletedProjection(
            deleted_snapshots=len({row["snapshot_id"] for row in manifests}),
            deleted_documents=len(rows),
            collections=tuple(row["collection_name"] for row in manifests),
        )

    def commit_delete_snapshots(
        self, *, project_id: str, snapshot_ids: Sequence[str]
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TEMP TABLE deleting_retrieval_snapshots "
                "(snapshot_id TEXT PRIMARY KEY) WITHOUT ROWID"
            )
            connection.executemany(
                "INSERT INTO deleting_retrieval_snapshots (snapshot_id) VALUES (?)",
                ((item,) for item in snapshot_ids),
            )
            rows = connection.execute(
                """
                SELECT d.rowid
                FROM retrieval_documents d
                JOIN deleting_retrieval_snapshots s USING (snapshot_id)
                WHERE d.project_id = ?
                """,
                (project_id,),
            ).fetchall()
            if rows:
                connection.executemany(
                    "DELETE FROM retrieval_claim_fts WHERE rowid = ?",
                    ((row["rowid"],) for row in rows),
                )
            ranking_feature_table_available = bool(
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'retrieval_ranking_features'"
                ).fetchone()
            )
            if ranking_feature_table_available:
                connection.execute(
                    """
                    DELETE FROM retrieval_ranking_features
                    WHERE project_id = ? AND snapshot_id IN (
                      SELECT snapshot_id FROM deleting_retrieval_snapshots
                    )
                    """,
                    (project_id,),
                )
            connection.execute(
                """
                DELETE FROM retrieval_documents
                WHERE project_id = ? AND snapshot_id IN (
                  SELECT snapshot_id FROM deleting_retrieval_snapshots
                )
                """,
                (project_id,),
            )
            connection.execute(
                """
                DELETE FROM retrieval_manifests
                WHERE project_id = ? AND snapshot_id IN (
                  SELECT snapshot_id FROM deleting_retrieval_snapshots
                )
                """,
                (project_id,),
            )
            connection.commit()


def _scope_index_payload(scope: SearchScope | SearchScopeFilter) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for name in _SCOPE_ARRAY_FIELDS:
        values = getattr(scope, name, None) or []
        payload[name] = list(dict.fromkeys(constraint_text(item) for item in values))
    time = getattr(scope, "time", None)
    if time is None:
        payload["time"] = None
    else:
        numeric_requested = time.start is not None or time.end is not None
        interval = time_interval_years(time.start, time.end, time.unit)
        numeric_known = numeric_requested and interval is not None
        payload["time"] = {
            "label": constraint_text(time.label) if time.label else None,
            "numeric_requested": numeric_requested,
            "numeric_known": numeric_known,
            "start": (
                None
                if interval is None
                else max(interval[0], -_OPEN_TIME_BOUND)
            ),
            "end": (
                None
                if interval is None
                else min(interval[1], _OPEN_TIME_BOUND)
            ),
        }
    uncertainty = getattr(scope, "uncertainty", None)
    payload["uncertainty"] = constraint_text(uncertainty) if uncertainty else None
    return payload


def _scope_numeric_is_unusable(
    scope: SearchScopeFilter, payload: dict[str, Any]
) -> bool:
    return bool(
        scope.time is not None
        and (scope.time.start is not None or scope.time.end is not None)
        and not payload["time"]["numeric_known"]
    )


def _qdrant_scope_payload(payload: dict[str, Any]) -> dict[str, Any]:
    output = {f"scope_{name}": payload[name] for name in _SCOPE_ARRAY_FIELDS}
    output["scope_uncertainty"] = payload["uncertainty"]
    time = payload["time"]
    if time is not None:
        output["scope_time_label"] = time["label"]
        if time["numeric_known"]:
            output["scope_time_start"] = time["start"]
            output["scope_time_end"] = time["end"]
    return output


def _append_sql_scope_filter(
    filters: list[str], parameters: list[Any], scope: SearchScopeFilter
) -> None:
    payload = _scope_index_payload(scope)
    for name in _SCOPE_ARRAY_FIELDS:
        values = payload[name]
        if not values:
            continue
        placeholders = ",".join("?" for _ in values)
        filters.append(
            "EXISTS (SELECT 1 FROM json_each(d.scope_filter_json, ?) "
            f"WHERE value IN ({placeholders}))"
        )
        parameters.append(f"$.{name}")
        parameters.extend(values)
    time = payload["time"]
    if time is not None:
        filters.append("json_extract(d.scope_filter_json, '$.time') IS NOT NULL")
        if time["label"]:
            filters.append("json_extract(d.scope_filter_json, '$.time.label') = ?")
            parameters.append(time["label"])
        if _scope_numeric_is_unusable(scope, payload):
            filters.append("0 = 1")
        elif time["numeric_known"]:
            filters.extend(
                [
                    "json_extract(d.scope_filter_json, '$.time.numeric_known') = 1",
                    # Rows indexed before bounds were ordered may be stored
                    # old-to-young; compare the ordered interval either way.
                    "MIN(CAST(json_extract(d.scope_filter_json, '$.time.start') "
                    "AS REAL), CAST(json_extract(d.scope_filter_json, "
                    "'$.time.end') AS REAL)) <= ?",
                    "MAX(CAST(json_extract(d.scope_filter_json, '$.time.start') "
                    "AS REAL), CAST(json_extract(d.scope_filter_json, "
                    "'$.time.end') AS REAL)) >= ?",
                ]
            )
            parameters.extend([time["end"], time["start"]])
    if payload["uncertainty"]:
        filters.append("json_extract(d.scope_filter_json, '$.uncertainty') = ?")
        parameters.append(payload["uncertainty"])
