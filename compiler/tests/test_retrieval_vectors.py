from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from compiler_service.retrieval_contract import SearchScope, SearchScopeFilter
from compiler_service.retrieval_store import QdrantVectorIndex
from compiler_service.retrieval_vectors import (
    NumpyVectorIndex,
    collection_name_for,
    migrate_from_qdrant,
)

MODEL = "fake-multilingual"
REVISION = "revision-1"


def _scope(**overrides) -> SearchScope:
    payload = {
        "location": [],
        "time": None,
        "formation": [],
        "sample_type": [],
        "method": [],
        "condition": [],
        "measurement": [],
        "uncertainty": None,
        "entities": [],
    }
    payload.update(overrides)
    return SearchScope.model_validate(payload, strict=True)


def _points(dimension: int = 8, count: int = 40):
    generator = np.random.default_rng(7)
    vectors = generator.normal(size=(count, dimension)).tolist()
    claims = [f"claim-{index:02d}" for index in range(count)]
    types = ["causal" if index % 3 == 0 else "descriptive" for index in range(count)]
    polarities = ["negated" if index % 5 == 0 else "affirmed" for index in range(count)]
    scopes = [
        _scope(
            location=["South China"] if index % 2 else ["Ordos Basin"],
            time={
                "label": None,
                "start": 250.0 + index,
                "end": 252.0 + index,
                "unit": "Ma",
            },
        )
        for index in range(count)
    ]
    return claims, types, polarities, scopes, vectors


def _index(cls, path: Path, dimension: int = 8):
    return cls(
        data_dir=path,
        model_name=MODEL,
        model_revision=REVISION,
        dimension=dimension,
    )


def _fill(index, *, project: str = "project-1", snapshot: str = "snapshot-1"):
    claims, types, polarities, scopes, vectors = _points()
    index.upsert(
        project_id=project,
        snapshot_id=snapshot,
        claim_ids=claims,
        claim_types=types,
        polarities=polarities,
        scopes=scopes,
        vectors=vectors,
    )
    return vectors


@pytest.mark.parametrize(
    "filters",
    [
        {},
        {"claim_types": ["causal"]},
        {"polarity": "negated"},
        {"scope": {"location": ["south china"]}},
        {
            "scope": {
                "time": {"label": None, "start": 260.0, "end": 270.0, "unit": "Ma"}
            }
        },
    ],
)
def test_numpy_index_matches_embedded_qdrant_ranking(tmp_path: Path, filters) -> None:
    numpy_index = _index(NumpyVectorIndex, tmp_path / "numpy")
    qdrant_index = _index(QdrantVectorIndex, tmp_path / "qdrant")
    vectors = _fill(numpy_index)
    _fill(qdrant_index)
    assert numpy_index.collection_name == qdrant_index.collection_name
    query = (np.asarray(vectors[3]) + 0.3 * np.asarray(vectors[11])).tolist()
    scope = filters.get("scope")
    arguments = {
        "project_id": "project-1",
        "snapshot_ids": ["snapshot-1"],
        "vector": query,
        "limit": 10,
        "claim_types": filters.get("claim_types", []),
        "polarity": filters.get("polarity"),
        "scope": None
        if scope is None
        else SearchScopeFilter.model_validate(scope, strict=True),
    }
    expected = qdrant_index.search(**arguments)
    actual = numpy_index.search(**arguments)
    assert expected
    assert [(item.snapshot_id, item.claim_id) for item in actual] == [
        (item.snapshot_id, item.claim_id) for item in expected
    ]
    # float16 storage keeps cosine scores within 1e-3 of the float64 scan.
    for left, right in zip(actual, expected, strict=True):
        assert left.score == pytest.approx(right.score, abs=1e-3)


def test_numpy_index_upsert_is_idempotent_and_delete_is_project_scoped(
    tmp_path: Path,
) -> None:
    index = _index(NumpyVectorIndex, tmp_path)
    vectors = _fill(index)
    _fill(index)
    _fill(index, project="project-2")
    assert index.count() == 80
    hits = index.search(
        project_id="project-1",
        snapshot_ids=["snapshot-1"],
        vector=vectors[0],
        limit=1,
        claim_types=[],
        polarity=None,
        scope=None,
    )
    assert hits[0].claim_id == "claim-00"
    assert hits[0].score == pytest.approx(1.0, abs=1e-3)
    index.delete_snapshots(
        project_id="project-1",
        snapshot_ids=["snapshot-1"],
        collections=[index.collection_name],
    )
    assert index.count() == 40
    assert (
        index.search(
            project_id="project-1",
            snapshot_ids=["snapshot-1"],
            vector=vectors[0],
            limit=5,
            claim_types=[],
            polarity=None,
            scope=None,
        )
        == []
    )
    # Reopening reads the same matrix and metadata from disk.
    reopened = _index(NumpyVectorIndex, tmp_path)
    assert reopened.count() == 40


def test_numpy_index_refuses_another_model_identity(tmp_path: Path) -> None:
    _fill(_index(NumpyVectorIndex, tmp_path))
    collection = collection_name_for(MODEL, REVISION, 8)
    meta = tmp_path / "vectors" / collection / "points.sqlite3"
    assert meta.exists()
    import sqlite3

    with sqlite3.connect(meta) as connection:
        connection.execute(
            "UPDATE vector_meta SET value = 'other' WHERE key = 'model_name'"
        )
    with pytest.raises(RuntimeError, match="identity"):
        _index(NumpyVectorIndex, tmp_path)


def test_migration_copies_every_embedded_qdrant_point(tmp_path: Path) -> None:
    source = _index(QdrantVectorIndex, tmp_path)
    vectors = _fill(source)
    _fill(source, snapshot="snapshot-2")
    target = _index(NumpyVectorIndex, tmp_path)

    assert migrate_from_qdrant(source, target, batch=7) == 80
    assert migrate_from_qdrant(source, target, batch=7) == 80
    assert target.count() == 80
    arguments = {
        "project_id": "project-1",
        "snapshot_ids": ["snapshot-1", "snapshot-2"],
        "vector": vectors[5],
        "limit": 6,
        "claim_types": [],
        "polarity": None,
        "scope": SearchScopeFilter.model_validate(
            {"location": ["ordos basin"]}, strict=True
        ),
    }
    migrated = target.search(**arguments)
    assert [(item.snapshot_id, item.claim_id) for item in migrated] == [
        (item.snapshot_id, item.claim_id) for item in source.search(**arguments)
    ]
