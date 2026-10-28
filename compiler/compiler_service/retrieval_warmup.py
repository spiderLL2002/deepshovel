"""Explicitly download and load the configured local retrieval models."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from typing import Any

from .config import Settings
from .retrieval_providers import FastEmbedDenseEncoder
from .retrieval_service import RetrievalService


def warmup(
    settings: Settings | None = None,
    *,
    dense_factory: Callable[..., Any] = FastEmbedDenseEncoder,
    service_factory: Callable[..., Any] = RetrievalService,
    materialized_ranking_feature_project_ids: Sequence[str] | None = None,
    materialized_ranking_feature_maintenance: bool = False,
) -> dict[str, Any]:
    """Materialize the multilingual dense model without starting HTTP."""

    configured = settings or Settings.from_environment()
    materialized_enabled = (
        configured.retrieval_materialized_ranking_features
        or materialized_ranking_feature_maintenance
    )
    if materialized_ranking_feature_project_ids and not materialized_enabled:
        raise ValueError(
            "project-specific ranking feature maintenance requires explicit enablement"
        )
    cache_dir = configured.data_dir / "retrieval" / "models"
    dense = dense_factory(
        configured.retrieval_dense_model,
        cache_dir=cache_dir,
        model_revision=configured.retrieval_dense_model_revision,
    )
    passage = "地质学证据表明这是一条多语言科学声明。"
    query = "multilingual scientific evidence"
    passage_vectors = dense.embed_documents([passage])
    query_vector = dense.embed_query(query)
    if (
        len(passage_vectors) != 1
        or len(passage_vectors[0]) != dense.dimension
        or len(query_vector) != dense.dimension
    ):
        raise RuntimeError("retrieval model warmup returned an invalid shape")
    service = service_factory(
        data_dir=configured.data_dir,
        dense_encoder=dense,
        rrf_k=configured.retrieval_rrf_k,
        materialized_ranking_features_enabled=materialized_enabled,
        qdrant_url=configured.retrieval_qdrant_url,
        qdrant_api_key=configured.retrieval_qdrant_api_key,
        vector_backend=configured.retrieval_vector_backend,
    )
    repair = service.repair_dense_projections()
    ranking_features = (
        service.prepare_materialized_ranking_features(
            project_ids=materialized_ranking_feature_project_ids
        )
        if materialized_enabled
        else {
            "status": "disabled",
            "configured": False,
            "maintenance_override": False,
        }
    )
    return {
        "status": "ready",
        "dense_model": dense.name,
        "dense_model_revision": dense.revision,
        "dense_dimension": dense.dimension,
        "dense_repair": repair,
        "materialized_ranking_features": {
            **ranking_features,
            "configured": configured.retrieval_materialized_ranking_features,
            "maintenance_override": materialized_ranking_feature_maintenance,
        },
    }


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--materialized-ranking-features-maintenance",
        action="store_true",
        help="Explicitly prepare sidecars without enabling the serving process.",
    )
    parser.add_argument(
        "--ranking-feature-project-id",
        action="append",
        default=None,
        help="Limit explicit sidecar maintenance to one project; repeatable.",
    )
    args = parser.parse_args(argv)
    print(
        json.dumps(
            warmup(
                materialized_ranking_feature_project_ids=(
                    args.ranking_feature_project_id
                ),
                materialized_ranking_feature_maintenance=(
                    args.materialized_ranking_features_maintenance
                ),
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
