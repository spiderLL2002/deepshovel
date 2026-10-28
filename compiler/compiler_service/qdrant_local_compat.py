"""Audited compatibility patch for deterministic embedded-Qdrant cosine search."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import threading
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

_EXPECTED_QDRANT_CLIENT_VERSION = "1.19.0"
_EXPECTED_LOCAL_DISTANCES_SHA256 = (
    "5b07c14d2f636c81b03f033de8ecff55b6fcf2886f805f5d1f1b30f4da9f5d3e"
)
_EXPECTED_LOCAL_COLLECTION_SHA256 = (
    "f0a9d283300f6b5aa9df7b7d3c6e403a51eee36aa0bad5d677960cc01dcffd89"
)
_PATCH_LOCK = threading.Lock()
_EXPECTED_PAYLOAD_FILTERS_SHA256 = (
    "901d70099e79abdc9121c5774bcb341138cf18d5a47edb1398bb8f821fea7654"
)
_MATCH_ANY_SET_THRESHOLD = 32
# Each mask evaluation owns its cache. Retain the condition object to prevent
# identity reuse, and cache unsupported conditions too, without retaining any
# filter or corpus globally after the query finishes.
_MATCH_ANY_CACHE: ContextVar[dict[int, Any] | None] = ContextVar(
    "qdrant_local_match_any_cache", default=None
)
_ORIGINAL_CHECK_MATCH: Any = None
_ORIGINAL_PAYLOAD_MASK: Any = None
_MATCH_ANY_TYPE: Any = None
_MASK_CACHE: ContextVar[Any] = ContextVar(
    "qdrant_local_payload_mask_cache", default=None
)


class PayloadMaskCache:
    """Per-index LRU; callers serialize queries and invalidate before mutation."""

    def __init__(self, *, max_entries: int = 8, max_bytes: int = 8 * 1024 * 1024):
        self.max_entries = min(max_entries, 8)
        self.max_bytes = min(max_bytes, 8 * 1024 * 1024)
        self.entries: OrderedDict[Any, Any] = OrderedDict()
        self.nbytes = 0

    def clear(self) -> None:
        self.entries.clear()
        self.nbytes = 0

    def calculate(self, payloads, payload_filter, ids_inv, deleted_per_vector):
        try:
            serialized = json.dumps(
                None
                if payload_filter is None
                else payload_filter.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError):
            return _calculate_compiled_mask(
                payloads, payload_filter, ids_inv, deleted_per_vector
            )
        key = (
            id(payloads),
            id(ids_inv),
            id(deleted_per_vector),
            len(payloads),
            hashlib.sha256(serialized.encode()).digest(),
        )
        if key in self.entries:
            self.entries.move_to_end(key)
            return self.entries[key][-1]
        mask = _calculate_compiled_mask(
            payloads, payload_filter, ids_inv, deleted_per_vector
        )
        if self.max_entries > 0 and mask.nbytes <= self.max_bytes:
            mask.setflags(write=False)
            while self.entries and (
                len(self.entries) >= self.max_entries
                or self.nbytes + mask.nbytes > self.max_bytes
            ):
                self.nbytes -= self.entries.popitem(last=False)[1][-1].nbytes
            # Retain inputs so their identities cannot be recycled while cached.
            self.entries[key] = (payloads, ids_inv, deleted_per_vector, mask)
            self.nbytes += mask.nbytes
        return mask


@contextmanager
def payload_mask_cache_scope(cache: PayloadMaskCache | None):
    token = _MASK_CACHE.set(cache)
    try:
        yield
    finally:
        _MASK_CACHE.reset(token)


def _compiled_check_match(condition: Any, value: Any) -> bool:
    cache = _MATCH_ANY_CACHE.get()
    if (
        cache is None
        or not isinstance(condition, _MATCH_ANY_TYPE)
        or len(condition.any) < _MATCH_ANY_SET_THRESHOLD
    ):
        return _ORIGINAL_CHECK_MATCH(condition, value)
    key = id(condition)
    if key not in cache:
        operand_type = type(condition.any[0])
        if operand_type in (str, int) and all(
            type(item) is operand_type for item in condition.any
        ):
            cache[key] = (condition, operand_type, frozenset(condition.any))
        else:
            cache[key] = None
    compiled = cache[key]
    if compiled is not None and type(value) in (str, int, bool, float, type(None)):
        # Official values_match distinguishes bool/int/float despite Python's
        # cross-type equality. Sets are used only for exact built-in types.
        return type(value) is compiled[1] and value in compiled[2]
    return _ORIGINAL_CHECK_MATCH(condition, value)


def _query_scoped_payload_mask(*args: Any, **kwargs: Any) -> Any:
    cache = _MASK_CACHE.get()
    if cache is not None:
        return cache.calculate(*args, **kwargs)
    return _calculate_compiled_mask(*args, **kwargs)


def _calculate_compiled_mask(*args: Any, **kwargs: Any) -> Any:
    token = _MATCH_ANY_CACHE.set({})
    try:
        return _ORIGINAL_PAYLOAD_MASK(*args, **kwargs)
    finally:
        _MATCH_ANY_CACHE.reset(token)


def _read_only_cosine_similarity(query: Any, vectors: Any) -> Any:
    """Match Qdrant local cosine arithmetic without mutating either operand."""

    import numpy as np
    from qdrant_client.local.distances import EPSILON

    vector_norms = np.linalg.norm(vectors, axis=-1)[:, np.newaxis]
    normalized_vectors = vectors / np.where(vector_norms != 0.0, vector_norms, EPSILON)

    if len(query.shape) == 1:
        query_norm = np.linalg.norm(query)
        normalized_query = query / np.where(query_norm != 0.0, query_norm, EPSILON)
        return np.dot(normalized_vectors, normalized_query)

    query_norms = np.linalg.norm(query, axis=-1)[:, np.newaxis]
    normalized_query = query / np.where(query_norms != 0.0, query_norms, EPSILON)
    return np.dot(normalized_query, normalized_vectors.T)


def install_read_only_local_cosine_patch() -> dict[str, str]:
    """Install the exact-version local patch or fail before opening the index."""

    version = importlib.metadata.version("qdrant-client")
    if version != _EXPECTED_QDRANT_CLIENT_VERSION:
        raise RuntimeError(
            "embedded Qdrant requires the audited qdrant-client "
            f"{_EXPECTED_QDRANT_CLIENT_VERSION}; found {version}"
        )

    from qdrant_client.http import models
    from qdrant_client.local import distances, local_collection, payload_filters

    distances_path = Path(distances.__file__).resolve()
    distances_sha256 = hashlib.sha256(distances_path.read_bytes()).hexdigest()
    if distances_sha256 != _EXPECTED_LOCAL_DISTANCES_SHA256:
        raise RuntimeError(
            "embedded Qdrant local cosine source differs from the audited wheel: "
            f"path={distances_path}, expected={_EXPECTED_LOCAL_DISTANCES_SHA256}, "
            f"observed={distances_sha256}"
        )
    collection_path = Path(local_collection.__file__).resolve()
    collection_sha256 = hashlib.sha256(collection_path.read_bytes()).hexdigest()
    if collection_sha256 != _EXPECTED_LOCAL_COLLECTION_SHA256:
        raise RuntimeError(
            "embedded Qdrant local collection source differs from the audited wheel: "
            f"path={collection_path}, expected={_EXPECTED_LOCAL_COLLECTION_SHA256}, "
            f"observed={collection_sha256}"
        )
    if local_collection.calculate_distance is not distances.calculate_distance:
        raise RuntimeError("embedded Qdrant local distance binding differs")
    payload_path = Path(payload_filters.__file__).resolve()
    payload_sha256 = hashlib.sha256(payload_path.read_bytes()).hexdigest()
    if payload_sha256 != _EXPECTED_PAYLOAD_FILTERS_SHA256:
        raise RuntimeError(
            "embedded Qdrant payload filters differ from the audited wheel"
        )

    global _ORIGINAL_CHECK_MATCH, _ORIGINAL_PAYLOAD_MASK, _MATCH_ANY_TYPE
    with _PATCH_LOCK:
        if (
            local_collection.calculate_payload_mask
            is not payload_filters.calculate_payload_mask
        ):
            raise RuntimeError("embedded Qdrant local payload mask binding differs")
        if _ORIGINAL_CHECK_MATCH is None:
            _ORIGINAL_CHECK_MATCH = payload_filters.check_match
            _ORIGINAL_PAYLOAD_MASK = payload_filters.calculate_payload_mask
        elif (
            payload_filters.check_match is not _compiled_check_match
            or payload_filters.calculate_payload_mask is not _query_scoped_payload_mask
        ):
            raise RuntimeError("embedded Qdrant payload filter patch binding differs")
        payload_filters.check_match = _compiled_check_match
        _MATCH_ANY_TYPE = models.MatchAny
        payload_filters.calculate_payload_mask = _query_scoped_payload_mask
        local_collection.calculate_payload_mask = _query_scoped_payload_mask
        distances.cosine_similarity = _read_only_cosine_similarity
        if (
            distances.calculate_distance.__globals__.get("cosine_similarity")
            is not _read_only_cosine_similarity
        ):
            raise RuntimeError("embedded Qdrant cosine patch is not active")

    return {
        "qdrant_client_version": version,
        "local_distances_sha256": distances_sha256,
        "local_collection_sha256": collection_sha256,
        "payload_filters_sha256": payload_sha256,
        "payload_filter_patch": "query_scoped_match_any_sets_v1",
        "payload_mask_cache": "index_lru_8_entries_8_mib_v1",
        "patch": "read_only_cosine_similarity_v1",
    }
