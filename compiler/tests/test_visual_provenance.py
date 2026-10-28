from __future__ import annotations

from copy import deepcopy

import pytest
from conftest import raw_graph_payload

from compiler_service.errors import ContractIssue
from compiler_service.graph_contract import PaperEvidenceGraph
from compiler_service.models import DocumentBlock, DocumentIR, DocumentPage
from compiler_service.normalize import normalize_mineru
from compiler_service.number_normalization import parse_plain_number
from compiler_service.source_catalog import build_source_catalog
from compiler_service.structured_source import (
    build_structured_source_catalog,
    visible_cell_text,
)
from compiler_service.visual_provenance import bind_structured_visuals, value_key


def _table_document(artifact, parser_metadata, *bodies: str) -> DocumentIR:
    blocks = [
        DocumentBlock.model_validate(
            {
                "id": f"block.table.{index}",
                "kind": "table",
                "page": 1,
                "source_index": index,
                "bbox": {
                    "x": 0.1,
                    "y": 0.1 + 0.4 * index,
                    "width": 0.8,
                    "height": 0.3,
                },
                "text": f"Table {index + 1}. Results.",
                "level": None,
                "structured": {"table_body": body},
            },
            strict=True,
        )
        for index, body in enumerate(bodies)
    ]
    return DocumentIR(
        artifact=artifact,
        parser=parser_metadata,
        page_count=1,
        pages=[DocumentPage(number=1, width=612, height=792, blocks=blocks)],
        markdown="",
        warnings=[],
    )


MERGED = (
    "<table>"
    "<tr><th rowspan='2'>Sample</th><th colspan=\"2\">SiO<sub>2</sub> (wt%)</th></tr>"
    "<tr><th>Mean</th><th>SD</th></tr>"
    "<tr><td rowspan=2>A</td><td>45.2</td><td>0.3</td></tr>"
    "<tr><td>46.1</td><td></td></tr>"
    "<tr><td>B</td><td>1.2×10<sup>-3</sup></td><td>&lt;0.01</td></tr>"
    "</table>"
)


def test_merged_cells_land_on_their_true_grid_coordinates(
    artifact, parser_metadata
) -> None:
    catalog = build_structured_source_catalog(
        _table_document(artifact, parser_metadata, MERGED)
    )

    (template,) = catalog.visual_templates
    cells = {(cell.row, cell.column): cell.raw_value for cell in template.cells}
    assert cells == {
        (1, "Sample"): "A",
        (1, "SiO2 (wt%) / Mean"): "45.2",
        (1, "SiO2 (wt%) / SD"): "0.3",
        # The rowspan value applies to the second data row as well, and the
        # empty SD cell no longer shifts later cells or rejects the table.
        (2, "Sample"): "A",
        (2, "SiO2 (wt%) / Mean"): "46.1",
        (3, "Sample"): "B",
        (3, "SiO2 (wt%) / Mean"): "1.2×10^-3",
        (3, "SiO2 (wt%) / SD"): "<0.01",
    }
    spanned = [cell for cell in template.cells if cell.raw_value == "A"]
    assert spanned[0].source_record_ids == spanned[1].source_record_ids
    assert not catalog.warnings


@pytest.mark.parametrize(
    ("body", "message"),
    (
        ("<table><tr><td>1</td><script>x</script></tr></table>", "unsupported"),
        ("<table><tr><td><b>1</td></tr></table>", "invalid"),
        ("<table><tr><td colspan='0'>1</td></tr></table>", "span"),
        ("<table><tr><td></td></tr></table>", "empty"),
    ),
)
def test_unsupported_table_markup_still_fails_closed(
    artifact, parser_metadata, body: str, message: str
) -> None:
    catalog = build_structured_source_catalog(
        _table_document(artifact, parser_metadata, body), fail_on_unsafe=False
    )
    assert not catalog.visual_templates
    assert catalog.unsafe_block_ids == ("block.table.0",)
    assert message in catalog.warnings[0]


def test_visible_cell_text_and_value_keys_agree_across_notations() -> None:
    assert visible_cell_text("1.2×10<sup>-3</sup>") == "1.2×10^-3"
    assert visible_cell_text("SiO<sub>2</sub><br/>wt&#37;") == "SiO2 wt%"
    assert value_key("1.2×10⁻³") == value_key("1.2×10<sup>-3</sup>")
    assert value_key("−5") == value_key("-5")
    assert parse_plain_number("1.2×10^-3") == pytest.approx(0.0012)


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        ("1,234 mg", 1234),
        ("2.5×10⁻³", 0.0025),
        ("1.2 × 10^-3", 0.0012),
        ("−5", -5),
        ("12.5 ± 0.3", 12.5),
        ("3.2–4.5", None),
        ("3,5", None),
        ("1e400", None),
        ("1.2 x 10-3", None),
        ("n = 25", None),
    ),
)
def test_plain_numbers_are_exact_or_explicitly_ambiguous(raw, expected) -> None:
    assert parse_plain_number(raw) == expected


def _graph_with_cells(artifact, *cells: dict, region=None) -> PaperEvidenceGraph:
    payload = raw_graph_payload(artifact)
    payload["visual_evidence"][0]["cells"] = list(cells)
    if region is not None:
        payload["anchors"][1]["locator"]["region"] = region
    return PaperEvidenceGraph.model_validate(payload, strict=True)


def _cell(row: int, column: str, raw: str, numeric=None) -> dict:
    return {
        "row": row,
        "column": column,
        "raw_value": raw,
        "numeric_value": numeric,
        "unit": None,
    }


def _merged_document(artifact, parser_metadata):
    return _table_document(artifact, parser_metadata, MERGED)


_TABLE_REGION = {"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.3, "unit": "normalized"}


def test_exact_cells_bind_without_rewrite(artifact, parser_metadata) -> None:
    document = _merged_document(artifact, parser_metadata)
    graph = _graph_with_cells(
        artifact,
        _cell(1, "SiO2 (wt%) / Mean", "45.2", 45.2),
        _cell(3, "SiO2 (wt%) / SD", "<0.01"),
        region=_TABLE_REGION,
    )
    rewrites: list[tuple] = []

    bound, violations = bind_structured_visuals(
        graph,
        document,
        source_catalog=build_source_catalog(document),
        record=lambda *item: rewrites.append(item),
    )

    assert violations == ()
    assert bound is graph
    assert rewrites == []


def test_cell_coordinates_are_compiler_owned_and_audited(
    artifact, parser_metadata
) -> None:
    document = _merged_document(artifact, parser_metadata)
    graph = _graph_with_cells(
        artifact,
        # Model counted the header rows and used the leaf header label.
        _cell(4, "Mean", "46.1", 46.1),
        _cell(5, "Mean", "1.2×10⁻³", 0.0012),
        region=_TABLE_REGION,
    )
    rewrites: list[tuple] = []
    catalog = build_source_catalog(document)

    bound, violations = bind_structured_visuals(
        graph,
        document,
        source_catalog=catalog,
        record=lambda *item: rewrites.append(item),
    )

    assert violations == ()
    cells = [
        (cell.row, cell.column, cell.raw_value)
        for cell in bound.visual_evidence[0].cells
    ]
    assert cells == [
        (2, "SiO2 (wt%) / Mean", "46.1"),
        (3, "SiO2 (wt%) / Mean", "1.2×10⁻³"),
    ]
    assert [item[0] for item in rewrites] == ["table_cell_coordinates_hydrated"] * 2
    again, again_violations = bind_structured_visuals(
        bound, document, source_catalog=catalog
    )
    assert again is bound and again_violations == ()


def test_fabricated_values_and_contradicting_numbers_are_located(
    artifact, parser_metadata
) -> None:
    document = _merged_document(artifact, parser_metadata)
    graph = _graph_with_cells(
        artifact,
        _cell(1, "SiO2 (wt%) / Mean", "47.9", 47.9),
        _cell(2, "SiO2 (wt%) / Mean", "46.1", 4.61),
        region=_TABLE_REGION,
    )

    _, violations = bind_structured_visuals(
        graph, document, source_catalog=build_source_catalog(document)
    )

    assert [(item.issue, item.loc) for item in violations] == [
        (
            ContractIssue.PROVENANCE_VISUAL_VALUE_NOT_FOUND,
            ("visual_evidence", 0, "cells", 0, "raw_value"),
        ),
        (
            ContractIssue.PROVENANCE_VISUAL_NUMERIC_MISMATCH,
            ("visual_evidence", 0, "cells", 1, "numeric_value"),
        ),
    ]


def test_ambiguous_or_unstructured_visual_sources_are_rejected(
    artifact, parser_metadata
) -> None:
    document = _table_document(artifact, parser_metadata, MERGED, MERGED)
    graph = _graph_with_cells(artifact, _cell(1, "SiO2 (wt%) / Mean", "45.2"))
    graph = graph.model_copy(deep=True)
    graph.anchors[1].locator.region = None

    _, violations = bind_structured_visuals(
        graph, document, source_catalog=build_source_catalog(document)
    )
    assert [item.issue for item in violations] == [
        ContractIssue.PROVENANCE_VISUAL_BLOCK_AMBIGUOUS
    ]

    unsafe = _table_document(
        artifact, parser_metadata, "<table><tr><td>45.2</td><script/></tr></table>"
    )
    bound_graph = _graph_with_cells(
        artifact, _cell(1, "column_1", "45.2"), region=_TABLE_REGION
    )
    _, violations = bind_structured_visuals(
        bound_graph, unsafe, source_catalog=build_source_catalog(unsafe)
    )
    assert [(item.issue, item.loc) for item in violations] == [
        (ContractIssue.PROVENANCE_VISUAL_VALUE_UNVERIFIABLE, ("visual_evidence", 0))
    ]


def test_fixture_table_cell_matches_the_normalized_source(
    artifact, mineru_output
) -> None:
    document = normalize_mineru(mineru_output, artifact).document
    graph = PaperEvidenceGraph.model_validate(
        raw_graph_payload(artifact), strict=True
    )
    catalog = build_source_catalog(document)

    bound, violations = bind_structured_visuals(graph, document, source_catalog=catalog)
    assert violations == ()
    assert bound is graph

    payload = deepcopy(raw_graph_payload(artifact))
    payload["visual_evidence"][0]["cells"][0]["raw_value"] = "7 mg"
    payload["visual_evidence"][0]["cells"][0]["numeric_value"] = 7
    _, violations = bind_structured_visuals(
        PaperEvidenceGraph.model_validate(payload, strict=True),
        document,
        source_catalog=catalog,
    )
    assert [item.issue for item in violations] == [
        ContractIssue.PROVENANCE_VISUAL_VALUE_NOT_FOUND
    ]
