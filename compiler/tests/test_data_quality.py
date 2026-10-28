from pathlib import Path

from compiler_service.data_quality import inspect_document
from compiler_service.data_quality_store import DataQualityStore
from compiler_service.models import DocumentIR, ParseResponse, ParseRun


def _document() -> DocumentIR:
    digest = "a" * 64
    return DocumentIR.model_validate(
        {
            "artifact": {
                "paper_id": f"paper:{digest}",
                "artifact_id": "artifact:00000000-0000-4000-8000-000000000001",
                "version_id": f"artifact-version:sha256:{digest}",
                "artifact_sha256": digest,
                "media_type": "application/pdf",
                "locator": "upload://paper.pdf",
                "file_name": "paper.pdf",
            },
            "parser": {
                "name": "mineru",
                "version": "test",
                "backend": "pipeline",
                "method": "auto",
                "transport": "remote",
            },
            "page_count": 1,
            "pages": [{
                "number": 1,
                "width": None,
                "height": None,
                "blocks": [{
                    "id": "source:block:1",
                    "kind": "table",
                    "page": 1,
                    "source_index": 0,
                    "bbox": None,
                    "text": (
                        "| Sample | Value |\n| --- | --- |\n"
                        "| A | 1 |\n| A | 1 |\n| B | |"
                    ),
                    "level": None,
                    "structured": {},
                }],
            }],
            "markdown": (
                "| Sample | Value |\n| --- | --- |\n| A | 1 |\n| A | 1 |\n| B | |\n\n"
                "Data availability: https://zenodo.org/records/12345"
            ),
            "warnings": [],
        },
        strict=True,
    )


def test_inspection_reports_table_and_dataset_without_fetching() -> None:
    report = inspect_document(_document())
    assert report["schema_version"] == "data-quality-report/1.0"
    assert report["summary"]["paper_table_count"] == 1
    assert report["summary"]["dataset_count"] == 1
    assert report["summary"]["warning"] >= 1
    assert report["paper_tables"][0]["columns"] == ["Sample", "Value"]
    assert report["paper_tables"][0]["rows"] == [["A", "1"], ["A", "1"], ["B", ""]]
    assert report["paper_tables"][0]["preview_truncated"] is False
    assert report["datasets"][0]["repository"] == "zenodo"
    accessibility = next(
        check
        for check in report["datasets"][0]["checks"]
        if check["rule_id"] == "metadata.accessibility"
    )
    assert accessibility["execution_status"] == "not_run"
    assert accessibility["conclusion"] == "no_conclusion"


def test_merged_html_headers_and_rows_keep_every_value_in_its_column() -> None:
    document = _document()
    document.markdown = ""
    document.pages[0].blocks[0].structured = {
        "table_body": (
            "<table><tr><td rowspan=2>Region</td><td rowspan=2>Sample</td>"
            "<td colspan=2>Concentration (mg/kg)</td></tr>"
            "<tr><td>La</td><td>Ce</td></tr>"
            "<tr><td rowspan=2>North</td><td>N1</td><td>12.5</td><td>25.0</td></tr>"
            "<tr><td>N2</td><td>15.0</td><td>30.0</td></tr>"
            "<tr><td rowspan=2>South</td><td>S1</td><td>8.0</td><td>16.0</td></tr>"
            "<tr><td>S2</td><td>10.0</td><td>20.0</td></tr></table>"
        )
    }
    report = inspect_document(document)
    table = report["paper_tables"][0]
    assert table["columns"] == [
        "Region", "Sample", "Concentration (mg/kg) / La", "Concentration (mg/kg) / Ce",
    ]
    assert table["rows"] == [
        ["North", "N1", "12.5", "25.0"],
        ["North", "N2", "15.0", "30.0"],
        ["South", "S1", "8.0", "16.0"],
        ["South", "S2", "10.0", "20.0"],
    ]
    assert (table["row_count"], table["column_count"]) == (4, 4)
    assert report["inspection_version"] == 2


def test_html_grid_preserves_missing_cells_and_duplicate_headers() -> None:
    document = _document()
    document.markdown = ""
    document.pages[0].blocks[0].structured = {
        "table_body": "<table><tr><th>Value</th><th>Value</th></tr>"
        "<tr><td>1</td><td>2</td></tr><tr><td>3</td></tr></table>"
    }
    table = inspect_document(document)["paper_tables"][0]
    assert table["columns"] == ["Value", "Value"]
    assert table["rows"] == [["1", "2"], ["3", ""]]
    assert any(c["conclusion"] == "warning" for c in table["checks"])


def test_invalid_html_span_is_reported_without_a_false_pass() -> None:
    document = _document()
    document.markdown = ""
    document.pages[0].blocks[0].structured = {
        "table_body": "<table><tr><td colspan=9999999>Value</td></tr>"
        "<tr><td>1</td></tr></table>"
    }
    report = inspect_document(document)
    assert report["paper_tables"] == []
    assert report["summary"]["pass"] == 0
    assert any("未作数据质量结论" in warning for warning in report["warnings"])


def test_document_job_is_idempotent_per_artifact_version(tmp_path: Path) -> None:
    parsed = ParseResponse(
        run=ParseRun(
            run_id="parse:test",
            backend="mineru-pipeline",
            backend_version="test",
            duration_ms=1,
        ),
        document=_document(),
    )
    store = DataQualityStore(tmp_path)
    first, created = store.create_document(parsed)
    replay, replay_created = store.create_document(parsed)
    assert created is True
    assert replay_created is False
    assert replay["job"]["id"] == first["job"]["id"]

    job_directory = tmp_path / "data-quality" / first["job"]["id"]
    assert job_directory.exists()
    assert store.delete_version(parsed.document.artifact.version_id) is True
    assert store.delete_version(parsed.document.artifact.version_id) is False
    assert store.find_version(parsed.document.artifact.version_id) is None
    assert not job_directory.exists()

    recreated, recreated_new = store.create_document(parsed)
    assert recreated_new is True
    assert recreated["job"]["id"] != first["job"]["id"]
