"""Single-key hydration remains a point lookup as unrelated metadata grows."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from test_retrieval_service import _document, _index_request, _manifest, _service


def measured(store, work):
    original = store._connect
    observations = []

    @contextmanager
    def connect():
        with original() as connection:

            class Observed:
                def __getattr__(self, name):
                    return getattr(connection, name)

                def execute(self, sql, args=()):
                    if not sql.lstrip().startswith("SELECT"):
                        return connection.execute(sql, args)
                    plan = [
                        row["detail"]
                        for row in connection.execute("EXPLAIN QUERY PLAN " + sql, args)
                    ]
                    steps = 0

                    def tick():
                        nonlocal steps
                        steps += 1
                        return 0

                    connection.set_progress_handler(tick, 1)
                    try:
                        rows = connection.execute(sql, args).fetchall()
                    finally:
                        connection.set_progress_handler(None, 0)
                    observations.append((plan, steps))
                    return SimpleNamespace(fetchall=lambda: rows)

            yield Observed()

    with patch.object(store, "_connect", connect):
        result = work()
    return result, observations


def copy_row(connection, table, changes):
    row = dict(connection.execute(f"SELECT * FROM {table} LIMIT 1").fetchone())
    row.pop("rowid", None)
    row.update(changes)
    connection.execute(
        f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
        list(row.values()),
    )


def test_requested_documents_and_manifests_use_point_lookups(tmp_path):
    service = _service(tmp_path)
    service.index_snapshot(
        _index_request(_manifest(), [_document("seed", "Mercury source.")])
    )
    store = service.store
    store._ranking_features_enabled = True
    store._ensure_ranking_feature_schema()
    with store._connect() as connection:
        connection.execute("""INSERT INTO retrieval_ranking_features
          SELECT project_id,snapshot_id,index_version,dense_model,dense_model_revision,
                 dense_dimension,claim_id,'{"marker":"correct"}'
          FROM retrieval_documents""")
    args = dict(
        project_id="project-1",
        dense_model=service.dense_model,
        dense_model_revision=service.dense_revision,
        dense_dimension=service.dense_dimension,
    )
    sink = {}

    def load():
        return store.load_documents(
            **args, keys=[("snapshot-1", "seed")], ranking_feature_sink=sink
        )

    def manifests():
        return store.get_manifests(
            **args, index_version="evidence-retrieval/2.0", snapshot_ids=["snapshot-1"]
        )

    before, doc_small = measured(store, load)
    _, manifest_small = measured(store, manifests)
    with store._connect() as connection:
        for index in range(600):
            change = {"snapshot_id": f"unrelated-{index}"}
            copy_row(connection, "retrieval_manifests", change)
            copy_row(
                connection,
                "retrieval_documents",
                {**change, "document_json": "invalid unrelated JSON"},
            )
        for change in (
            {"project_id": "other"},
            {"dense_model": "other"},
            {"dense_model_revision": "other"},
            {"dense_dimension": 999},
        ):
            copy_row(connection, "retrieval_manifests", change)
            copy_row(
                connection,
                "retrieval_documents",
                {**change, "document_json": "invalid excluded JSON"},
            )
            copy_row(
                connection,
                "retrieval_ranking_features",
                {**change, "projection_json": '{"marker":"wrong"}'},
            )
    after, doc_large = measured(store, load)
    actual_manifests, manifest_large = measured(store, manifests)
    assert before == after and list(after) == [("snapshot-1", "seed")]
    assert list(actual_manifests) == ["snapshot-1"]
    assert sink[("snapshot-1", "seed")].projection_payload == {"marker": "correct"}
    for small, large in ((doc_small, doc_large), (manifest_small, manifest_large)):
        assert len(small) == len(large) == 1
        plan, steps = large[0]
        assert plan[0].startswith("SCAN r") or plan[0].startswith("SCAN requested")
        assert steps <= small[0][1] + 20, (small, large)
        assert steps < 300, (plan, steps)
    document_plan = "\n".join(doc_large[0][0])
    assert "snapshot_id=?" in document_plan and "claim_id=?" in document_plan
    assert (
        "dense_model=?" in document_plan and "dense_model_revision=?" in document_plan
    )
    assert "dense_dimension=?" in document_plan and "project_id=?" in document_plan
    assert "LEFT-JOIN" in document_plan
