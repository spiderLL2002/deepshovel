from __future__ import annotations

from copy import deepcopy

import pytest

from compiler_service.models import DocumentBlock, DocumentIR, DocumentPage
from compiler_service.structured_source import (
    StructuredSourceError,
    StructuredSourceGeometry,
    build_structured_source_catalog,
    hydrate_selected_structured_visuals,
    retained_model_structure,
    structured_record_payload,
    validate_structured_review_ledger,
)


def _document(artifact, parser_metadata, block: DocumentBlock) -> DocumentIR:
    return DocumentIR(
        artifact=artifact,
        parser=parser_metadata,
        page_count=1,
        pages=[
            DocumentPage(
                number=1,
                width=612,
                height=792,
                blocks=[block],
            )
        ],
        markdown=block.text or "",
        warnings=[],
    )


def _block(
    structured: dict,
    *,
    kind: str = "table",
    text: str | None = "Table 1. Results.",
) -> DocumentBlock:
    return DocumentBlock.model_validate(
        {
            "id": "block.visual.1",
            "kind": kind,
            "page": 1,
            "source_index": 0,
            "bbox": {"x": 0.1, "y": 0.2, "width": 0.7, "height": 0.3},
            "text": text,
            "level": None,
            "structured": structured,
        },
        strict=True,
    )


def test_recognized_cells_have_stable_exact_records_and_hydrate(
    artifact, parser_metadata
) -> None:
    block = _block(
        {
            "type": "table",
            "page_idx": 0,
            "bbox": [100, 200, 800, 500],
            "recognized_cells": [
                {
                    "row": 1,
                    "column": "Mass",
                    "raw_value": "5.00 mg",
                    "numeric_value": 5,
                    "unit": "mg",
                }
            ],
        }
    )
    document = _document(artifact, parser_metadata, block)

    first = build_structured_source_catalog(document)
    second = build_structured_source_catalog(document)

    assert [item.id for item in first.records] == [item.id for item in second.records]
    assert {item.json_pointer for item in first.records} == {
        "/recognized_cells/0/row",
        "/recognized_cells/0/column",
        "/recognized_cells/0/raw_value",
        "/recognized_cells/0/numeric_value",
        "/recognized_cells/0/unit",
    }
    raw_record = next(
        item for item in first.records if item.json_pointer.endswith("/raw_value")
    )
    assert raw_record.exact_scalar == "5.00 mg"
    assert raw_record.exact_scalar_json == '"5.00 mg"'
    assert raw_record.geometry == StructuredSourceGeometry(0.1, 0.2, 0.7, 0.3)
    assert raw_record.direct_evidence
    assert structured_record_payload(raw_record)["geometry"] == {
        "x": 0.1,
        "y": 0.2,
        "width": 0.7,
        "height": 0.3,
    }

    validate_structured_review_ledger(first, tuple(item.id for item in first.records))
    with pytest.raises(StructuredSourceError, match="incomplete"):
        validate_structured_review_ledger(first, [raw_record.id])
    with pytest.raises(StructuredSourceError, match="duplicates"):
        validate_structured_review_ledger(
            first, [*(item.id for item in first.records), raw_record.id]
        )

    hydrated = hydrate_selected_structured_visuals(document, first, [raw_record.id])
    assert len(hydrated) == 1
    assert hydrated[0].anchor.anchor_type == "table"
    assert hydrated[0].anchor.locator.region is not None
    assert hydrated[0].anchor.locator.region.model_dump(mode="json") == {
        "x": 0.1,
        "y": 0.2,
        "width": 0.7,
        "height": 0.3,
        "unit": "normalized",
    }
    assert hydrated[0].visual.visual_kind == "table"
    assert hydrated[0].visual.cells[0].model_dump(mode="json") == {
        "row": 1,
        "column": "Mass",
        "raw_value": "5.00 mg",
        "numeric_value": 5,
        "unit": "mg",
    }
    assert hydrated[0].source_record_ids == (raw_record.id,)


def test_simple_html_cells_keep_exact_scalar_offsets_and_headers_as_context(
    artifact, parser_metadata
) -> None:
    html = (
        "<table><tr><th>Group</th><th>Value</th></tr>"
        "<tr><td>Treated</td><td> 5 mg </td></tr></table>"
    )
    document = _document(
        artifact,
        parser_metadata,
        _block(
            {
                "table_caption": ["Table 1. Results."],
                "table_body": html,
            },
            text=f"Table 1. Results.\n{html}",
        ),
    )

    catalog = build_structured_source_catalog(document)

    assert retained_model_structure(document.pages[0].blocks[0]) == {"table_body": html}
    header = next(item for item in catalog.records if item.exact_scalar == "Value")
    value = next(item for item in catalog.records if item.exact_scalar == "5 mg")
    assert header.role == "table_header"
    assert not header.direct_evidence
    assert value.role == "table_cell"
    assert value.json_pointer == "/table_body"
    assert value.scalar_start_offset is not None
    assert value.scalar_end_offset is not None
    assert (
        html[value.scalar_start_offset : value.scalar_end_offset] == value.exact_scalar
    )

    hydrated = hydrate_selected_structured_visuals(document, catalog, [value.id])
    assert hydrated[0].visual.cells[0].row == 1
    assert hydrated[0].visual.cells[0].column == "Value"
    assert hydrated[0].visual.cells[0].raw_value == "5 mg"
    with pytest.raises(StructuredSourceError, match="cannot be direct"):
        hydrate_selected_structured_visuals(document, catalog, [header.id])


def test_figure_points_hydrate_with_required_axes_and_exact_json_pointers(
    artifact, parser_metadata
) -> None:
    structured = {
        "axes": [
            {
                "id": "time",
                "direction": "x",
                "label": "Time",
                "unit": "s",
                "scale": "linear",
            },
            {
                "id": "mass",
                "direction": "y",
                "label": "Mass",
                "unit": "mg",
                "scale": "linear",
            },
        ],
        "series": [
            {
                "label": "Treatment",
                "x_axis_id": "time",
                "y_axis_id": "mass",
                "points": [
                    {"x": 1, "y": 5, "uncertainty": 0.2},
                    {"x": 2, "y": 8, "uncertainty": None},
                ],
            }
        ],
    }
    document = _document(
        artifact,
        parser_metadata,
        _block(structured, kind="figure", text="Figure 1. Mass by time."),
    )

    catalog = build_structured_source_catalog(document)
    y_record = next(
        item for item in catalog.records if item.json_pointer == "/series/0/points/0/y"
    )

    hydrated = hydrate_selected_structured_visuals(document, catalog, [y_record.id])[0]

    assert y_record.exact_scalar == 5
    assert y_record.direct_evidence
    assert hydrated.anchor.anchor_type == "figure"
    assert {axis.direction for axis in hydrated.visual.axes} == {"x", "y"}
    assert len(hydrated.visual.series) == 1
    assert hydrated.visual.series[0].label == "Treatment"
    assert [
        point.model_dump(mode="json") for point in hydrated.visual.series[0].points
    ] == [{"x": 1, "y": 5, "uncertainty": 0.2}]
    assert hydrated.source_record_ids == (y_record.id,)


def test_unknown_model_visible_structure_warns_and_fails_closed(
    artifact, parser_metadata
) -> None:
    document = _document(
        artifact,
        parser_metadata,
        _block({"recognized_cells": {"5.1": {"unit": "mg"}}}),
    )

    with pytest.raises(StructuredSourceError) as caught:
        build_structured_source_catalog(document)

    assert caught.value.warnings
    assert "recognized_cells must be a list" in caught.value.warnings[0]

    audit = build_structured_source_catalog(document, fail_on_unsafe=False)
    assert audit.unsafe_record_ids
    assert {item.exact_scalar for item in audit.records} == {"mg"}
    with pytest.raises(StructuredSourceError):
        audit.assert_safe()


def test_retained_boundary_matches_compact_filter_and_never_silently_drops_unknown(
    artifact, parser_metadata
) -> None:
    caption = "Figure 1. Measured response."
    block = _block(
        {
            "type": "figure",
            "page_idx": 0,
            "bbox": [1, 2, 3, 4],
            "img_path": "images/1.png",
            "figure_caption": [caption],
            "scientific_scalar": "9.81 m/s²",
        },
        kind="figure",
        text=caption,
    )
    document = _document(artifact, parser_metadata, block)

    assert retained_model_structure(block) == {"scientific_scalar": "9.81 m/s²"}
    with pytest.raises(StructuredSourceError) as caught:
        build_structured_source_catalog(document)
    assert any("scientific_scalar" in item for item in caught.value.warnings)


def test_hydration_rechecks_exact_scalar_and_geometry(
    artifact, parser_metadata
) -> None:
    document = _document(
        artifact,
        parser_metadata,
        _block(
            {"recognized_cells": [{"row": 1, "column": "Mass", "raw_value": "5 mg"}]}
        ),
    )
    catalog = build_structured_source_catalog(document)
    record = next(item for item in catalog.records if item.direct_evidence)
    payload = deepcopy(document.model_dump(mode="json"))
    payload["pages"][0]["blocks"][0]["structured"]["recognized_cells"][0][
        "raw_value"
    ] = "6 mg"
    changed = DocumentIR.model_validate(payload, strict=True)

    with pytest.raises(StructuredSourceError, match="scalar changed"):
        hydrate_selected_structured_visuals(changed, catalog, [record.id])
