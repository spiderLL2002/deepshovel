from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from compiler_service import document_extraction
from compiler_service.api import create_app
from compiler_service.citation_source import CitationSourceProjection
from compiler_service.config import Settings
from compiler_service.data_quality_store import DataQualityStore
from compiler_service.document_extraction import build_document_extraction
from compiler_service.errors import JobNotComplete
from compiler_service.graph_contract import canonical_json
from compiler_service.job_store import JobStore
from compiler_service.mineru import FakeMineruBackend
from compiler_service.normalize import normalize_mineru


def _citation_source(version_id: str) -> CitationSourceProjection:
    return CitationSourceProjection(
        artifact_version_id=version_id,
        references=[],
        mentions=[],
        warnings=[],
    )


def test_document_extraction_has_stable_contract_and_safe_visual(
    artifact, mineru_output
) -> None:
    assert document_extraction.MAX_DOCUMENT_EXTRACTION_BYTES == 20 * 1024 * 1024
    assert document_extraction.MAX_VISUAL_LOCATORS == 4_096
    assert document_extraction.MAX_METADATA_TEXT == 10_000
    assert document_extraction.MAX_ABSTRACT_TEXT == 100_000
    assert document_extraction.MAX_VISUAL_TEXT == 10_000
    document = normalize_mineru(mineru_output, artifact).document

    payload = build_document_extraction(document).model_dump(
        mode="json", exclude_none=False
    )

    assert set(payload) == {
        "schema_version",
        "artifact_version_id",
        "artifact_sha256",
        "parser",
        "metadata",
        "field_sources",
        "stats",
        "markdown",
        "visuals",
        "warnings",
    }
    assert payload["schema_version"] == "document-extraction/1.0"
    assert payload["artifact_version_id"] == artifact.version_id
    assert payload["artifact_sha256"] == artifact.artifact_sha256
    assert payload["parser"] == {
        "name": "mineru",
        "version": "3.1.15",
        "backend": "pipeline",
        "method": "auto",
    }
    assert set(payload["metadata"]) == {
        "title",
        "abstract",
        "year",
        "author",
        "journal",
        "issn",
        "volume",
        "issue",
        "page",
        "article_number",
        "doi",
        "link",
        "publisher",
        "language",
    }
    assert set(payload["field_sources"]) == set(payload["metadata"])
    assert payload["metadata"]["title"] == "Treatment result"
    assert payload["field_sources"]["title"] == "rule"
    assert all(isinstance(value, str) for value in payload["metadata"].values())
    assert set(payload["field_sources"].values()) <= {
        "rule",
        "artifact",
        "doi_registry",
        "empty",
    }
    assert payload["stats"] == {
        "page_count": 1,
        "text_block_count": 1,
        "figure_count": 0,
        "table_count": 1,
        "image_count": 0,
        "visual_count": 1,
    }
    assert payload["markdown"] == document.markdown
    visual = payload["visuals"][0]
    assert set(visual) == {
        "block_id",
        "kind",
        "source_kind",
        "page",
        "region",
        "label",
        "caption",
        "footnote",
        "visual",
    }
    assert visual["kind"] == "table"
    assert visual["source_kind"] == "table"
    assert visual["page"] == 1
    assert visual["region"] == {
        "x": 0.1,
        "y": 0.4,
        "width": 0.8,
        "height": 0.4,
    }
    assert visual["visual"] is not None
    assert visual["visual"]["kind"] == "structured_visual_evidence"
    assert visual["visual"]["visual_kind"] == "table"
    assert "<table" not in json.dumps(payload)
    assert "table_body" not in json.dumps(payload)


def test_document_metadata_handles_spaced_abstract_and_avoids_false_issue_or_pages(
    artifact, mineru_output
) -> None:
    parsed = normalize_mineru(mineru_output, artifact)
    page = parsed.document.pages[0]
    metadata_block = page.blocks[0].model_copy(
        update={
            "id": "block:p1:metadata",
            "kind": "paragraph",
            "source_index": max(block.source_index for block in page.blocks) + 1,
            "text": "ISSN 0031-0182\nPalaeo Journal 637 (2024) 112012",
            "structured": {"type": "header"},
        }
    )
    document = parsed.document.model_copy(
        update={
            "markdown": (
                "# Treatment result\n\n"
                "Miruo Lin a, Kelai Xi b,\\*\n\n"
                "a Example University\n\n"
                "## A B S T R A C T\n\n"
                "A complete abstract retained from the parser.\n\n"
                "## 1. Introduction\n"
            ),
            "pages": [
                page.model_copy(update={"blocks": [*page.blocks, metadata_block]})
            ],
        }
    )

    metadata = build_document_extraction(document).metadata

    assert metadata.abstract == "A complete abstract retained from the parser."
    assert metadata.author == "Miruo Lin; Kelai Xi"
    assert metadata.issn == "0031-0182"
    assert metadata.journal == "Palaeo Journal"
    assert metadata.volume == "637"
    assert metadata.issue == ""
    assert metadata.page == ""
    assert metadata.article_number == "112012"


def test_document_metadata_reads_page_range_and_copyright_publisher_from_margins(
    artifact, mineru_output
) -> None:
    parsed = normalize_mineru(mineru_output, artifact)
    page = parsed.document.pages[0]
    next_index = max(block.source_index for block in page.blocks) + 1
    template = page.blocks[0]
    header = template.model_copy(
        update={
            "id": "block:p1:journal-header",
            "kind": "other",
            "source_index": next_index,
            "text": "Gondwana Research, 93. pp. 218\u2013226",
            "structured": {"type": "page-header"},
        }
    )
    footer = template.model_copy(
        update={
            "id": "block:p1:publisher-footer",
            "kind": "other",
            "source_index": next_index + 1,
            "text": "\u00a9 2021 Elsevier Ltd. All rights reserved.",
            "structured": {"type": "footer"},
        }
    )
    document = parsed.document.model_copy(
        update={
            "pages": [
                page.model_copy(update={"blocks": [*page.blocks, header, footer]})
            ]
        }
    )

    metadata = build_document_extraction(document).metadata

    assert metadata.journal == "Gondwana Research"
    assert metadata.volume == "93"
    assert metadata.issue == ""
    assert metadata.page == "218-226"
    assert metadata.article_number == ""
    assert metadata.publisher == "Elsevier Ltd."


def test_document_metadata_uses_only_safe_front_matter_doi(
    artifact, mineru_output
) -> None:
    parsed = normalize_mineru(mineru_output, artifact)
    page = parsed.document.pages[0]
    reference = page.blocks[0].model_copy(
        update={
            "id": "block:p1:reference-doi",
            "kind": "citation",
            "source_index": max(block.source_index for block in page.blocks) + 1,
            "text": "Reference DOI: 10.9999/cited-work",
            "structured": {"type": "citation"},
        }
    )
    document = parsed.document.model_copy(
        update={
            "pages": [page.model_copy(update={"blocks": [*page.blocks, reference]})],
            "markdown": parsed.document.markdown + "\n\nDOI: 10.8888/markdown-only",
        }
    )

    metadata = build_document_extraction(document).metadata

    assert metadata.doi == ""


def test_document_extraction_bounds_metadata_fields_but_retains_long_abstract(
    artifact, mineru_output
) -> None:
    parsed = normalize_mineru(mineru_output, artifact)
    page = parsed.document.pages[0]
    title = page.blocks[0].model_copy(
        update={
            "id": "block:p1:title",
            "kind": "title",
            "text": "T" * 10_001,
        }
    )
    abstract = "A" * 20_000
    document = parsed.document.model_copy(
        update={
            "pages": [page.model_copy(update={"blocks": [title, *page.blocks]})],
            "markdown": f"# Fallback\n\n## Abstract\n{abstract}\n\n## Methods\nBody.",
        }
    )

    projection = build_document_extraction(document)

    assert projection.metadata.title == "T" * 10_000
    assert projection.metadata.abstract == abstract
    assert projection.field_sources.title == "rule"
    assert projection.field_sources.abstract == "rule"


def test_document_extraction_bounds_visuals_and_drops_optional_payloads(
    monkeypatch, artifact, mineru_output
) -> None:
    parsed = normalize_mineru(mineru_output, artifact)
    page = parsed.document.pages[0]
    table = next(block for block in page.blocks if block.kind == "table")
    structured = dict(table.structured)
    structured["table_caption"] = ["C" * 10_000]
    first = table.model_copy(update={"structured": structured})
    second = table.model_copy(
        update={"id": "block:p1:000003", "source_index": table.source_index + 1}
    )
    bounded_document = parsed.document.model_copy(
        update={
            "pages": [
                page.model_copy(update={"blocks": [*page.blocks[:-1], first, second]})
            ]
        }
    )
    monkeypatch.setattr(document_extraction, "MAX_VISUAL_LOCATORS", 1)
    monkeypatch.setattr(document_extraction, "MAX_DOCUMENT_EXTRACTION_BYTES", 5_000)

    projection = build_document_extraction(bounded_document)
    payload = projection.model_dump(mode="json", exclude_none=False)

    assert projection.markdown == bounded_document.markdown
    assert len(projection.visuals) == 1
    assert projection.stats.visual_count == 1
    assert projection.stats.table_count == 1
    assert projection.visuals[0].region == first.bbox
    assert projection.visuals[0].visual is None
    assert projection.visuals[0].caption == ""
    assert projection.visuals[0].footnote == ""
    assert any("after the first 1 items" in warning for warning in projection.warnings)
    assert any(
        "captions and footnotes were omitted" in warning
        for warning in projection.warnings
    )
    assert len(canonical_json(payload).encode("utf-8")) <= 5_000


def test_document_extraction_endpoints_read_completed_and_versioned_documents(
    tmp_path, artifact, mineru_output, import_envelope
) -> None:
    parsed = normalize_mineru(mineru_output, artifact)
    root = tmp_path / "document-extraction-state"
    store = JobStore(root)
    created = store.create(artifact=artifact, verify=False, content=b"%PDF-1.4\n")
    store.transition(created.job.id, "parsing")
    store.save_document(created.job.id, parsed)
    with pytest.raises(JobNotComplete, match="has not completed"):
        store.load_completed_document(created.job.id)
    store.transition(created.job.id, "extracting")
    store.transition(created.job.id, "validating")
    store.complete(
        created.job.id,
        import_envelope,
        _citation_source(artifact.version_id),
    )
    data_quality = DataQualityStore(root)
    data_quality_response, stored = data_quality.create_document(parsed)
    assert stored is True
    data_quality_directory = root / "data-quality" / data_quality_response["job"]["id"]

    secret = "document-extraction-test-secret-0001"
    app = create_app(
        settings=Settings(data_dir=root, shared_secret=secret),
        mineru_backend=FakeMineruBackend(mineru_output),
        evidence_compiler=object(),
        job_store=store,
    )
    job_path = f"/v1/compile-jobs/{created.job.id}/document-extraction"
    version_path = f"/v1/document-extractions/{artifact.version_id}"
    headers = {"Authorization": f"Bearer {secret}"}

    with TestClient(app) as client:
        assert client.get(job_path).status_code == 401
        assert client.delete(version_path).status_code == 401
        by_job = client.get(job_path, headers=headers)
        by_version = client.get(version_path, headers=headers)
        deleted = client.delete(version_path, headers=headers)
        repeated_delete = client.delete(version_path, headers=headers)
        missing_version = client.get(version_path, headers=headers)

    assert by_job.status_code == 200
    assert by_version.status_code == 200
    assert by_job.json() == by_version.json()
    assert by_job.json()["artifact_version_id"] == artifact.version_id
    assert deleted.status_code == 204
    assert repeated_delete.status_code == 204
    assert missing_version.status_code == 404
    assert data_quality.find_version(artifact.version_id) is None
    assert not data_quality_directory.exists()
