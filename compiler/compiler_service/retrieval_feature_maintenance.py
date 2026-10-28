"""Explicit, model-free maintenance of hash-bound ranking sidecars only."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .graph_contract import canonical_json
from .retrieval_ontology import RetrievalOntology
from .retrieval_ranking_features import (
    LoadedRankingFeatureProjection,
    build_ranking_feature_projection,
    current_ranking_feature_bindings,
    validated_feature_values,
)
from .retrieval_store import RetrievalStore

FEATURE_NAMES = (
    "exact_anchor_tokens",
    "sparse_cjk_tokens",
    "scientific_mention_keys",
    "ontology_concept_ids",
)


def maintain_ranking_features(
    *,
    data_dir: Path,
    project_ids: Sequence[str],
    dense_model: str,
    dense_model_revision: str,
    dense_dimension: int,
    apply: bool = False,
) -> dict[str, Any]:
    """Build from exact stored documents, optionally commit sidecars atomically.

    Explicit stored model identity replaces model initialization or environment
    inference. This does not open Qdrant, embed, repair dense projections, alter
    serving flags, or create an absent base index. Dry-run is the default.
    """

    if type(apply) is not bool:
        raise ValueError("apply must be Boolean")
    if (
        isinstance(project_ids, (str, bytes))
        or not project_ids
        or any(not isinstance(p, str) or not p.strip() for p in project_ids)
    ):
        raise ValueError("at least one explicit nonempty project ID is required")
    if (
        any(
            not isinstance(value, str) or not value.strip()
            for value in (dense_model, dense_model_revision)
        )
        or type(dense_dimension) is not int
        or dense_dimension <= 0
    ):
        raise ValueError("an exact stored dense model/revision/dimension is required")
    selected = tuple(sorted(set(project_ids)))
    identity = {
        "dense_model": dense_model,
        "dense_model_revision": dense_model_revision,
        "dense_dimension": dense_dimension,
    }
    store = RetrievalStore.for_ranking_feature_maintenance(
        data_dir / "retrieval", writable=apply
    )
    available = store.complete_ranking_feature_project_ids(**identity)
    if any(project not in available for project in selected):
        raise ValueError("selected project/model identity has no complete local index")

    ontology_path = Path(__file__).with_name("retrieval_ontology.json")
    ontology_bytes = ontology_path.read_bytes()
    # Maintenance can be called twice in one interpreter; do not pair a cached
    # older ontology object with a newly read on-disk content hash.
    ontology = RetrievalOntology.from_path(ontology_path)
    if ontology_path.read_bytes() != ontology_bytes:
        raise ValueError("ontology changed during maintenance preparation")
    ontology_hash = hashlib.sha256(ontology_bytes).hexdigest()
    bindings = current_ranking_feature_bindings(
        ontology, ontology_content_sha256=ontology_hash
    )
    projections = []
    source_identities = []
    feature_states = {
        name: {"ready": 0, "legacy_fallback": 0} for name in FEATURE_NAMES
    }
    for project_id in selected:
        for document, source_json in store.load_ranking_feature_source_documents(
            project_id=project_id, **identity
        ):
            source_hash = hashlib.sha256(source_json.encode("utf-8")).hexdigest()
            projection = build_ranking_feature_projection(
                document,
                source_document_json=source_json,
                ontology=ontology,
                bindings=bindings,
            )
            payload = json.loads(projection)
            loaded = LoadedRankingFeatureProjection(
                projection_json=projection,
                projection_payload=payload,
                observed_source_document_json_sha256=source_hash,
            )
            for name in FEATURE_NAMES:
                entry = payload["features"][name]
                values = validated_feature_values(loaded, name, bindings=bindings)
                if values is not None:
                    feature_states[name]["ready"] += 1
                elif entry == {
                    "status": "legacy_fallback",
                    "reason": "serialized_feature_budget_exceeded",
                    "bindings": entry.get("bindings"),
                }:
                    feature_states[name]["legacy_fallback"] += 1
                else:
                    raise ValueError("new ranking feature failed binding validation")
            projections.append((project_id, document, source_json, projection))
            source_identities.append(
                [
                    project_id,
                    document.snapshot_id,
                    document.index_version,
                    document.claim_id,
                    source_hash,
                ]
            )
    # The serving process's validation will independently reject a stale binding;
    # also refuse to commit if this preparation itself crossed a source upgrade.
    if ontology_path.read_bytes() != ontology_bytes or bindings != (
        current_ranking_feature_bindings(
            ontology, ontology_content_sha256=ontology_hash
        )
    ):
        raise ValueError("ranking feature source changed during maintenance")
    maintenance = None
    if apply:
        receipt = store.preflight_ranking_feature_projections(
            project_ids=selected,
            cleanup_all_model_orphans=False,
            projections=projections,
            **identity,
        )
        maintenance = asdict(receipt)
        maintenance["project_ids"] = list(receipt.project_ids)
    return {
        "schema_version": "deepshovel-ranking-feature-maintenance/1.0",
        "status": "ready" if apply else "planned",
        "apply": apply,
        "project_ids": list(selected),
        **identity,
        "source_documents": len(projections),
        "source_document_bytes_sha256": hashlib.sha256(
            canonical_json(sorted(source_identities)).encode("utf-8")
        ).hexdigest(),
        "feature_states": feature_states,
        "bindings": asdict(bindings),
        "maintenance": maintenance,
    }


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--project-id", action="append", required=True)
    parser.add_argument("--dense-model", required=True)
    parser.add_argument(
        "--dense-model-revision",
        required=True,
        help="Exact stored revision, including any provider-version suffix.",
    )
    parser.add_argument("--dense-dimension", type=int, required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit selected sidecars. Without this option, validate/build only.",
    )
    args = parser.parse_args(argv)
    try:
        result = maintain_ranking_features(
            data_dir=args.data_dir,
            project_ids=args.project_id,
            dense_model=args.dense_model,
            dense_model_revision=args.dense_model_revision,
            dense_dimension=args.dense_dimension,
            apply=args.apply,
        )
    except Exception as error:
        # Validation errors can contain complete stored documents. Do not dump
        # their values, a traceback, secrets, or source text into a CLI receipt.
        parser.exit(
            1,
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(error).__name__,
                },
                sort_keys=True,
            )
            + "\n",
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
