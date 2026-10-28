from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

COMPILER_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = COMPILER_ROOT.parent
FIXTURES = COMPILER_ROOT / "fixtures"
sys.path.insert(0, str(COMPILER_ROOT))

from compiler_service.canonical import to_canonical_graph  # noqa: E402
from compiler_service.graph_contract import PaperEvidenceGraph  # noqa: E402
from compiler_service.mineru import MineruOutput  # noqa: E402
from compiler_service.models import (  # noqa: E402
    ArtifactContext,
    ImportEnvelope,
    ModelRun,
    ParserMetadata,
)

PDF_BYTES = b"%PDF-1.4\n% deterministic test document\n"
PDF_SHA = hashlib.sha256(PDF_BYTES).hexdigest()


@pytest.fixture
def artifact() -> ArtifactContext:
    return ArtifactContext(
        paper_id="paper:compiler-demo",
        artifact_id="artifact:00000000-0000-0000-0000-000000000001",
        version_id=f"artifact-version:sha256:{PDF_SHA}",
        artifact_sha256=PDF_SHA,
        media_type="application/pdf",
        locator="upload://compiler-demo.pdf",
        file_name="compiler-demo.pdf",
    )


@pytest.fixture
def mineru_output() -> MineruOutput:
    return MineruOutput(
        version="3.1.15",
        method="auto",
        duration_ms=123,
        markdown=(FIXTURES / "mineru.md").read_text(encoding="utf-8"),
        content_list=json.loads(
            (FIXTURES / "mineru-content-list.json").read_text(encoding="utf-8")
        ),
        middle_json=json.loads(
            (FIXTURES / "mineru-middle.json").read_text(encoding="utf-8")
        ),
    )


@pytest.fixture
def parser_metadata() -> ParserMetadata:
    return ParserMetadata(
        name="mineru",
        version="3.1.15",
        backend="pipeline",
        method="auto",
        transport="remote",
    )


def raw_graph_payload(artifact: ArtifactContext) -> dict[str, Any]:
    anchor_identity = {
        "artifact_id": artifact.artifact_id,
        "version_id": artifact.version_id,
        "artifact_sha256": artifact.artifact_sha256,
        "media_type": artifact.media_type,
    }
    return {
        "schema_version": "1.0",
        "paper_id": artifact.paper_id,
        "claims": [
            {
                "id": "claim.local.1",
                "statement": "Treatment increases the measured amount.",
                "claim_type": "causal",
                "polarity": "affirmed",
                "scope_id": "scope.local.1",
                "anchor_ids": ["anchor.text.1"],
                "qualifiers": ["laboratory condition"],
                "proposition_key": None,
                "scoped_claim_id": None,
            }
        ],
        "observations": [
            {
                "id": "obs.local.1",
                "statement": "The treated sample reached 5 mg.",
                "observation_type": "measurement",
                "anchor_ids": ["anchor.text.1"],
                "scope_id": "scope.local.1",
                "raw_value": "5",
                "unit": "mg",
            }
        ],
        "reasoning": [
            {
                "id": "reason.local.1",
                "statement": (
                    "The controlled comparison attributes the increase to treatment."
                ),
                "reasoning_type": "causal_inference",
                "anchor_ids": ["anchor.text.1"],
            }
        ],
        "assumptions": [],
        "anchors": [
            {
                "id": "anchor.text.1",
                **anchor_identity,
                "anchor_type": "text",
                "locator": {
                    "page": 1,
                    "section": "Results",
                    "paragraph": None,
                    "figure": None,
                    "table": None,
                    "row_range": None,
                    "column_names": [],
                    "series": None,
                    "region": {
                        "x": 0.1,
                        "y": 0.2,
                        "width": 0.4,
                        "height": 0.1,
                        "unit": "normalized",
                    },
                    "quote": "The treated sample reached 5 mg.",
                },
            },
            {
                "id": "anchor.table.1",
                **anchor_identity,
                "anchor_type": "table",
                "locator": {
                    "page": 1,
                    "section": "Results",
                    "paragraph": None,
                    "figure": None,
                    "table": "Table 1",
                    "row_range": None,
                    "column_names": ["Group", "Value"],
                    "series": None,
                    "region": {
                        "x": 0.1,
                        "y": 0.4,
                        "width": 0.8,
                        "height": 0.4,
                        "unit": "normalized",
                    },
                    "quote": None,
                },
            },
        ],
        "visual_evidence": [
            {
                "id": "visual.table.1",
                "anchor_id": "anchor.table.1",
                "visual_kind": "table",
                "axes": [],
                "series": [],
                "cells": [
                    {
                        "row": 1,
                        "column": "Value",
                        "raw_value": "5 mg",
                        "numeric_value": 5,
                        "unit": "mg",
                    }
                ],
                "features": [
                    {
                        "kind": "trend",
                        "description": "The treated value exceeds control.",
                        "series_id": None,
                        "value": None,
                        "unit": None,
                    }
                ],
            }
        ],
        "scopes": [
            {
                "id": "scope.local.1",
                "location": None,
                "time": None,
                "formation": None,
                "sample_type": "treated sample",
                "method": "controlled comparison",
                "condition": "laboratory",
                "measurement": "amount",
                "uncertainty": None,
                "entities": ["treatment", "amount"],
            }
        ],
        "edges": [
            {
                "id": "edge.grounds.1",
                "source_id": "anchor.text.1",
                "target_id": "obs.local.1",
                "relation": "grounds",
                "rationale": None,
                "confidence": None,
            },
            {
                "id": "edge.grounds.visual.1",
                "source_id": "visual.table.1",
                "target_id": "obs.local.1",
                "relation": "grounds",
                "rationale": None,
                "confidence": None,
            },
            {
                "id": "edge.premise.1",
                "source_id": "obs.local.1",
                "target_id": "reason.local.1",
                "relation": "premise_for",
                "rationale": None,
                "confidence": None,
            },
            {
                "id": "edge.support.1",
                "source_id": "reason.local.1",
                "target_id": "claim.local.1",
                "relation": "supports",
                "rationale": "Controlled comparison.",
                "confidence": 0.8,
            },
        ],
    }


@pytest.fixture
def raw_graph(artifact: ArtifactContext) -> PaperEvidenceGraph:
    return PaperEvidenceGraph.model_validate(raw_graph_payload(artifact), strict=True)


@pytest.fixture
def import_envelope(
    artifact: ArtifactContext,
    raw_graph: PaperEvidenceGraph,
    parser_metadata: ParserMetadata,
) -> ImportEnvelope:
    graph = to_canonical_graph(
        raw_graph,
        artifact,
        parser=parser_metadata,
        created_at="2026-08-15T00:00:00Z",
    )
    return ImportEnvelope(
        graph=graph,
        root_artifact_version_id=artifact.version_id,
        extraction=ModelRun(
            run_id="extraction:sha256:" + "a" * 64,
            model="gpt-5.6-luna",
            prompt_version="compiler-extractor/1.0.0",
        ),
    )
