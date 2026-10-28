"""Durable feature receipts invalidate on SQLite writes, without full JSON reloads."""

import sqlite3
from dataclasses import replace

import pytest
import test_retrieval_service as fixtures
from test_retrieval_feature_activation import service_at


def seed(path, count=2, snapshot="snapshot-1", service=None):
    service = service or service_at(path, enabled=True)
    documents = [
        fixtures._document(
            f"claim-{index:03}", f"Monazite rare earth {index}", snapshot_id=snapshot
        )
        for index in range(count)
    ]
    chunks = [documents[i : i + 32] for i in range(0, count, 32)] or [[]]
    for chunk in chunks:
        service.index_snapshot(
            fixtures._index_request(
                fixtures._manifest(snapshot, document_count=count), chunk
            )
        )
    return service


def trace(service, monkeypatch):
    statements = []
    original = service.store._connect

    def connected():
        connection = original()
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(service.store, "_connect", connected)
    return statements


def source_reads(statements):
    return [
        s
        for s in statements
        if s.lstrip().upper().startswith("SELECT") and "d.document_json" in s
    ]


def test_receipt_survives_new_service_without_reading_source_or_sidecar_json(
    tmp_path, monkeypatch
):
    service = seed(tmp_path)
    assert service.prepare_materialized_ranking_features()["unchanged"] == 2
    reopened = service_at(tmp_path, enabled=True)
    statements = trace(reopened, monkeypatch)
    assert reopened.prepare_materialized_ranking_features()["unchanged"] == 2
    assert not source_reads(statements)
    assert not any("SELECT" in s and "projection_json" in s for s in statements)


@pytest.mark.parametrize("change", ["source", "sidecar", "missing", "manifest"])
def test_direct_sql_invalidates_only_changed_snapshot(tmp_path, monkeypatch, change):
    service = seed(tmp_path)
    seed(tmp_path, snapshot="snapshot-2", service=service)
    service.prepare_materialized_ranking_features()
    with sqlite3.connect(tmp_path / "retrieval/retrieval.sqlite3") as connection:
        if change == "source":
            connection.execute(
                "UPDATE retrieval_documents SET document_json=document_json || ' ' "
                "WHERE snapshot_id='snapshot-1'"
            )
        elif change == "sidecar":
            connection.execute(
                "UPDATE retrieval_ranking_features SET projection_json='{}' "
                "WHERE snapshot_id='snapshot-1'"
            )
        elif change == "missing":
            connection.execute(
                "DELETE FROM retrieval_ranking_features WHERE snapshot_id='snapshot-1'"
            )
        else:
            connection.execute(
                "UPDATE retrieval_manifests SET created_at=created_at || ' ' "
                "WHERE snapshot_id='snapshot-1'"
            )
    statements = trace(service, monkeypatch)
    receipt = service.prepare_materialized_ranking_features()
    assert receipt["source_documents"] == 4
    assert len(source_reads(statements)) == 1
    assert "snapshot-1" in source_reads(statements)[0]
    statements.clear()
    service.prepare_materialized_ranking_features()
    assert not source_reads(statements)


@pytest.mark.parametrize("replacement", [False, True])
def test_missing_trigger_forces_full_validation_before_receipts_are_reused(
    tmp_path, monkeypatch, replacement
):
    service = seed(tmp_path)
    service.prepare_materialized_ranking_features()
    with sqlite3.connect(tmp_path / "retrieval/retrieval.sqlite3") as connection:
        connection.execute(
            "DROP TRIGGER retrieval_ranking_features_feature_generation_update"
        )
        if replacement:
            connection.execute(
                "CREATE TRIGGER retrieval_ranking_features_feature_generation_update "
                "AFTER UPDATE ON retrieval_ranking_features BEGIN SELECT 1; END"
            )
        connection.execute("UPDATE retrieval_ranking_features SET projection_json='{}'")
    statements = trace(service, monkeypatch)
    assert service.prepare_materialized_ranking_features()["updated"] == 2
    assert source_reads(statements)
    statements.clear()
    service.prepare_materialized_ranking_features()
    assert not source_reads(statements)


def test_contract_change_invalidates_persistent_receipt(tmp_path, monkeypatch):
    service = seed(tmp_path)
    service.prepare_materialized_ranking_features()
    service.ranking_feature_bindings = replace(
        service.ranking_feature_bindings, ontology_content_sha256="changed-ontology"
    )
    statements = trace(service, monkeypatch)
    assert service.prepare_materialized_ranking_features()["updated"] == 2
    assert source_reads(statements)


def test_empty_snapshot_delete_and_recreate_have_distinct_generations(tmp_path):
    service = seed(tmp_path, count=0)
    assert service.prepare_materialized_ranking_features()["project_count"] == 1
    path = tmp_path / "retrieval/retrieval.sqlite3"
    with sqlite3.connect(path) as connection:
        initial = connection.execute(
            "SELECT generation FROM retrieval_feature_receipts"
        ).fetchone()[0]
        connection.execute("DELETE FROM retrieval_manifests")
    assert service.prepare_materialized_ranking_features()["project_count"] == 0
    seed(tmp_path, count=0, service=service)
    assert service.prepare_materialized_ranking_features()["project_count"] == 1
    with sqlite3.connect(path) as connection:
        final = connection.execute(
            "SELECT generation FROM retrieval_feature_receipts"
        ).fetchone()[0]
    assert final > initial


def test_new_model_same_snapshot_cannot_use_old_model_receipt(tmp_path, monkeypatch):
    service = seed(tmp_path)
    service.prepare_materialized_ranking_features()
    with sqlite3.connect(tmp_path / "retrieval/retrieval.sqlite3") as connection:
        for table in (
            "retrieval_manifests",
            "retrieval_documents",
            "retrieval_ranking_features",
        ):
            columns = [
                row[1]
                for row in connection.execute(f"PRAGMA table_info({table})")
                if row[1] != "rowid"
            ]
            selected = [
                "'different-model-revision'" if name == "dense_model_revision" else name
                for name in columns
            ]
            connection.execute(
                f"INSERT INTO {table} ({','.join(columns)}) "
                f"SELECT {','.join(selected)} FROM {table}"
            )
    service.dense_revision = "different-model-revision"
    statements = trace(service, monkeypatch)
    assert service.prepare_materialized_ranking_features()["source_documents"] == 2
    assert source_reads(statements)
    with sqlite3.connect(tmp_path / "retrieval/retrieval.sqlite3") as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM retrieval_feature_receipts"
            ).fetchone()[0]
            == 2
        )


def test_repair_failure_rolls_back_receipts_and_all_sidecar_changes(
    tmp_path, monkeypatch
):
    service = seed(tmp_path)
    service.prepare_materialized_ranking_features()
    path = tmp_path / "retrieval/retrieval.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE retrieval_ranking_features SET projection_json='{}'")
        before = connection.execute(
            "SELECT * FROM retrieval_feature_receipts"
        ).fetchall()
    original = service.store._upsert_ranking_feature
    calls = []

    def fail_second(*args, **kwargs):
        calls.append(True)
        if len(calls) == 2:
            raise RuntimeError("injected repair failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(service.store, "_upsert_ranking_feature", fail_second)
    with pytest.raises(RuntimeError, match="injected repair failure"):
        service.prepare_materialized_ranking_features()
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute("SELECT * FROM retrieval_feature_receipts").fetchall()
            == before
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM retrieval_ranking_features "
                "WHERE projection_json='{}'"
            ).fetchone()[0]
            == 2
        )


def test_dirty_snapshot_larger_than_one_page_is_fully_validated(tmp_path):
    service = seed(tmp_path, count=130)
    with sqlite3.connect(tmp_path / "retrieval/retrieval.sqlite3") as connection:
        connection.execute("DELETE FROM retrieval_ranking_features")
    receipt = service.prepare_materialized_ranking_features()
    assert receipt["inserted"] == receipt["source_documents"] == 130
    assert service.prepare_materialized_ranking_features()["unchanged"] == 130


def test_direct_orphan_insert_and_source_delete_cannot_hide_behind_receipt(tmp_path):
    service = seed(tmp_path)
    service.prepare_materialized_ranking_features()
    path = tmp_path / "retrieval/retrieval.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO retrieval_ranking_features SELECT project_id, 'orphan', "
            "index_version, dense_model, dense_model_revision, dense_dimension, "
            "claim_id, projection_json FROM retrieval_ranking_features"
        )
    assert service.prepare_materialized_ranking_features()["deleted_orphans"] == 2
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM retrieval_documents WHERE claim_id='claim-000'")
    with pytest.raises(ValueError, match="exactly cover"):
        service.prepare_materialized_ranking_features()


@pytest.mark.parametrize(
    "table",
    ["retrieval_documents", "retrieval_ranking_features", "retrieval_manifests"],
)
def test_explicit_rowid_replace_invalidates_removed_identity(tmp_path, table):
    service = seed(tmp_path)
    service.prepare_materialized_ranking_features()
    with sqlite3.connect(tmp_path / "retrieval/retrieval.sqlite3") as connection:
        connection.execute("PRAGMA recursive_triggers=OFF")
        before = connection.execute(
            "SELECT generation FROM retrieval_feature_generations "
            "WHERE snapshot_id='snapshot-1'"
        ).fetchone()[0]
        columns = [
            row[1]
            for row in connection.execute(f"PRAGMA table_info({table})")
            if row[1] != "rowid"
        ]
        values = ["'moved'" if name == "snapshot_id" else name for name in columns]
        connection.execute(
            f"INSERT OR REPLACE INTO {table} (rowid,{','.join(columns)}) "
            f"SELECT rowid,{','.join(values)} FROM {table} LIMIT 1"
        )
        after = connection.execute(
            "SELECT generation FROM retrieval_feature_generations "
            "WHERE snapshot_id='snapshot-1'"
        ).fetchone()[0]
        assert after > before
        assert connection.execute(
            "SELECT generation FROM retrieval_feature_generations "
            "WHERE snapshot_id='moved'"
        ).fetchone()[0] > 0
