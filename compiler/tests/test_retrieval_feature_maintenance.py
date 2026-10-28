"""Sidecar-only maintenance on small real SQLite stores, without model loading."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

from compiler_service.graph_contract import canonical_json
from compiler_service.retrieval_contract import ClaimSearchDocument
from compiler_service.retrieval_store import RetrievalStore

MODEL = "fixture-dense-model"
REVISION = "fixture-revision:fastembed-0.8.0"
DIMENSION = 2
PROJECT = "selected-project"
FEATURES = {
    "exact_anchor_tokens",
    "sparse_cjk_tokens",
    "scientific_mention_keys",
    "ontology_concept_ids",
}
COMPILER = Path(__file__).resolve().parents[1]


def document(claim_id):
    text = "Monazite contains rare earth elements at 10 mg/kg in 海盆沉积物."
    digest = hashlib.sha256(text.encode()).hexdigest()
    return ClaimSearchDocument.model_validate(
        {
            "index_version": "evidence-retrieval/2.0",
            "snapshot_id": "snapshot-1",
            "paper_id": "paper-1",
            "paper_family_id": "family-1",
            "root_artifact_version_id": "artifact-1",
            "claim_id": claim_id,
            "source_claim_id": None,
            "original_claim": text,
            "canonical_claim": text,
            "proposition_key": f"proposition:sha256:{digest}",
            "scoped_claim_id": f"claim:sha256:{digest}",
            "paper_title": "Fixture",
            "evidence_context": text,
            "entities": ["rare earth elements"],
            "key_terms": ["monazite"],
            "abbreviations": [],
            "qualifiers": [],
            "claim_type": "descriptive",
            "polarity": "affirmed",
            "modality": "asserted",
            "attribution": "authors_conclusion",
            "rhetorical_role": "terminal_conclusion",
            "scope": {
                "location": [],
                "time": None,
                "formation": [],
                "sample_type": [],
                "method": [],
                "condition": [],
                "measurement": [],
                "uncertainty": None,
                "entities": [],
            },
            "evidence_anchors": [],
            "shortest_evidence_path": {
                "root_claim_id": claim_id,
                "nodes": [],
                "links": [],
                "depth": 0,
                "mean_edge_confidence": None,
            },
            "path_quality": 1.0,
            "extractor_prompt_version": "fixture-v1",
            "explicit_relations": [],
        },
        strict=True,
    )


@pytest.fixture
def database(tmp_path):
    store = RetrievalStore(tmp_path / "retrieval", ranking_features_enabled=True)
    path = tmp_path / "retrieval" / "retrieval.sqlite3"
    connection = sqlite3.connect(path)
    try:
        RetrievalStore._create_ranking_feature_schema(connection)
        for project, count in ((PROJECT, 2), ("other-project", 1)):
            connection.execute(
                "INSERT INTO retrieval_manifests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    project,
                    "snapshot-1",
                    "evidence-retrieval/2.0",
                    MODEL,
                    REVISION,
                    DIMENSION,
                    "fixture",
                    "a" * 64,
                    "{}",
                    count,
                    count,
                    1,
                    "fixture",
                ),
            )
            for number in range(count):
                doc = document(f"claim-{number}")
                connection.execute(
                    "INSERT INTO retrieval_documents "
                    "(project_id,snapshot_id,index_version,"
                    "dense_model,dense_model_revision,dense_dimension,claim_id,paper_id,"
                    "paper_family_id,claim_type,polarity,scope_filter_json,point_id,"
                    "dense_complete,document_json) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        project,
                        doc.snapshot_id,
                        doc.index_version,
                        MODEL,
                        REVISION,
                        DIMENSION,
                        doc.claim_id,
                        doc.paper_id,
                        doc.paper_family_id,
                        doc.claim_type,
                        doc.polarity,
                        "{}",
                        None,
                        1,
                        canonical_json(doc.model_dump(mode="json")),
                    ),
                )
                connection.execute(
                    "INSERT INTO retrieval_ranking_features VALUES (?,?,?,?,?,?,?,?)",
                    (
                        project,
                        doc.snapshot_id,
                        doc.index_version,
                        MODEL,
                        REVISION,
                        DIMENSION,
                        doc.claim_id,
                        "{}",
                    ),
                )
        connection.execute(
            "INSERT INTO retrieval_claim_fts(project_id,original_claim) VALUES (?,?)",
            (PROJECT, "unchanged FTS evidence"),
        )
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()
    # These durable sentinels must survive both planning and application.
    for relative in ("retrieval/qdrant/collection.bin", "retrieval/models/model.bin"):
        sentinel = tmp_path / relative
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_bytes(b"immutable\x00\xff")
    del store
    return tmp_path, path


def snapshot(path):
    connection = sqlite3.connect(path)
    try:
        return tuple(connection.iterdump())
    finally:
        connection.close()


def checkpointed_bytes(path):
    connection = sqlite3.connect(path)
    try:
        busy, _, _ = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        assert busy == 0
    finally:
        connection.close()
    return path.read_bytes()


def primary_snapshot(path):
    connection = sqlite3.connect(path)
    try:
        schema = connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
        ).fetchall()
        tables = {}
        for kind, name, _, _ in schema:
            if kind == "table" and name != "retrieval_ranking_features":
                quoted = '"' + name.replace('"', '""') + '"'
                try:
                    tables[name] = connection.execute(
                        f"SELECT rowid,* FROM {quoted}"
                    ).fetchall()
                except sqlite3.OperationalError as error:
                    assert "no such column: rowid" in str(error)
                    tables[name] = connection.execute(
                        f"SELECT * FROM {quoted}"
                    ).fetchall()
        foreign = connection.execute(
            "SELECT * FROM retrieval_ranking_features WHERE project_id != ?", (PROJECT,)
        ).fetchall()
        return schema, tables, foreign
    finally:
        connection.close()


def maintain(data_dir, **overrides):
    from compiler_service.retrieval_feature_maintenance import maintain_ranking_features

    arguments = dict(
        data_dir=data_dir,
        project_ids=[PROJECT],
        dense_model=MODEL,
        dense_model_revision=REVISION,
        dense_dimension=DIMENSION,
    )
    return maintain_ranking_features(**(arguments | overrides))


def cli(data_dir, *extra):
    script = """
import json, sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
forbidden = {'compiler_service.retrieval_service',
             'compiler_service.retrieval_providers', 'fastembed', 'qdrant_client',
             'numpy', 'onnxruntime', 'torch'}
class Blocker:
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + '.')
               for name in forbidden):
            raise AssertionError('forbidden model import: ' + fullname)
        return None
sys.meta_path.insert(0, Blocker())
from compiler_service.retrieval_feature_maintenance import main
raise SystemExit(main(json.loads(sys.argv[3])))
"""
    args = [
        "--data-dir",
        str(data_dir),
        "--project-id",
        PROJECT,
        "--dense-model",
        MODEL,
        "--dense-model-revision",
        REVISION,
        "--dense-dimension",
        str(DIMENSION),
        *extra,
    ]
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "-c",
            script,
            str(COMPILER),
            sysconfig.get_path("purelib"),
            json.dumps(args),
        ],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def test_dry_run_preserves_complete_database_and_durable_files(database):
    data_dir, path = database
    before = snapshot(path)
    receipt = maintain(data_dir)
    assert receipt["schema_version"] == "deepshovel-ranking-feature-maintenance/1.0"
    assert receipt["status"] == "planned" and receipt["apply"] is False
    assert receipt["maintenance"] is None and receipt["source_documents"] == 2
    assert set(receipt["feature_states"]) == FEATURES
    assert all(
        state == {"ready": 2, "legacy_fallback": 0}
        for state in receipt["feature_states"].values()
    )
    assert snapshot(path) == before
    assert (
        data_dir / "retrieval/qdrant/collection.bin"
    ).read_bytes() == b"immutable\x00\xff"
    assert (
        data_dir / "retrieval/models/model.bin"
    ).read_bytes() == b"immutable\x00\xff"


def test_apply_only_selected_sidecars_and_second_run_is_byte_idempotent(database):
    data_dir, path = database
    before = primary_snapshot(path)
    receipt = maintain(data_dir, apply=True)
    assert receipt["status"] == "ready" and receipt["apply"] is True
    assert receipt["maintenance"]["updated"] == 2
    assert receipt["maintenance"]["project_ids"] in ([PROJECT], (PROJECT,))
    assert primary_snapshot(path) == before
    first_bytes = checkpointed_bytes(path)
    second = maintain(data_dir, apply=True)
    assert second["maintenance"]["updated"] == 0
    assert second["maintenance"]["unchanged"] == 2
    assert checkpointed_bytes(path) == first_bytes
    assert primary_snapshot(path) == before
    assert (
        data_dir / "retrieval/qdrant/collection.bin"
    ).read_bytes() == b"immutable\x00\xff"
    assert (
        data_dir / "retrieval/models/model.bin"
    ).read_bytes() == b"immutable\x00\xff"


def test_unknown_project_or_exact_model_identity_is_rejected_without_changes(database):
    data_dir, path = database
    before = snapshot(path)
    for overrides in (
        {"project_ids": ["unknown-project"]},
        {"dense_model": "unknown-model"},
        {"dense_model_revision": "fixture-revision"},
        {"dense_dimension": 3},
    ):
        with pytest.raises(ValueError):
            maintain(data_dir, apply=True, **overrides)
        assert snapshot(path) == before


def test_missing_database_is_never_created(tmp_path):
    missing = tmp_path / "missing-compiler"
    for apply in (False, True):
        with pytest.raises((ValueError, FileNotFoundError)):
            maintain(missing, apply=apply)
        assert not missing.exists()
        with pytest.raises((ValueError, FileNotFoundError)):
            RetrievalStore.for_ranking_feature_maintenance(
                missing / "retrieval", writable=apply
            )
        assert not missing.exists()


def test_dry_run_does_not_create_absent_sidecar_schema(database):
    data_dir, path = database
    connection = sqlite3.connect(path)
    try:
        connection.execute("DROP TRIGGER retrieval_documents_delete_ranking_features")
        connection.execute("DROP TABLE retrieval_ranking_features")
        connection.commit()
    finally:
        connection.close()
    before = snapshot(path)
    assert maintain(data_dir)["status"] == "planned"
    assert snapshot(path) == before


def test_source_change_between_planning_and_preflight_fails_atomically(
    database, monkeypatch
):
    data_dir, path = database
    original = RetrievalStore.preflight_ranking_feature_projections
    observed = {}

    def change_then_preflight(store, **kwargs):
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                "UPDATE retrieval_documents SET document_json=document_json || ' ' "
                "WHERE project_id=? AND claim_id='claim-0'",
                (PROJECT,),
            )
            connection.commit()
        finally:
            connection.close()
        observed["after_external_change"] = snapshot(path)
        return original(store, **kwargs)

    monkeypatch.setattr(
        RetrievalStore, "preflight_ranking_feature_projections", change_then_preflight
    )
    with pytest.raises(ValueError, match="source document changed"):
        maintain(data_dir, apply=True)
    assert snapshot(path) == observed["after_external_change"]


def test_cli_plans_and_applies_without_service_or_model_imports(database):
    data_dir, _ = database
    for extra, status in (((), "planned"), (("--apply",), "ready")):
        completed = cli(data_dir, *extra)
        assert completed.returncode == 0, completed.stderr
        receipt = json.loads(completed.stdout)
        assert receipt["status"] == status
        assert receipt["dense_model_revision"] == REVISION


def test_cli_error_never_outputs_source_text(database):
    data_dir, path = database
    sentinel = "PRIVATE_SOURCE_SENTINEL_DO_NOT_PRINT"
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "UPDATE retrieval_documents SET document_json=? WHERE project_id=?",
            (json.dumps({"canonical_claim": sentinel}), PROJECT),
        )
        connection.commit()
    finally:
        connection.close()
    before = snapshot(path)
    completed = cli(data_dir, "--apply")
    assert completed.returncode != 0
    assert sentinel not in completed.stdout + completed.stderr
    assert "Traceback" not in completed.stdout + completed.stderr
    assert snapshot(path) == before
