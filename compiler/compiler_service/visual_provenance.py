"""Check model-written table cells and figure points against the source.

``StructuredVisualEvidence`` carries values the model copied from a table or a
plotted series. Text anchors are hydrated from exact source spans, but these
values were previously admitted on the model's word. This module binds each
visual to exactly one source block and checks every cell and point against the
trusted structured template that ``structured_source`` built from the parser
output:

* a value that does not occur in the source structure is a provenance
  violation, exactly like a quote that is not in the document;
* table coordinates are Compiler-owned: a value found at other coordinates is
  moved to its true row/column when that cell is unambiguous (and audited);
* a ``numeric_value`` that contradicts an unambiguous source number is a
  violation; ambiguous source text (ranges, ``±``, footnote marks) leaves the
  model's interpretation to the independent verifier.
"""

from __future__ import annotations

import html
import math
import re
import unicodedata
from collections.abc import Callable

from .errors import ContractIssue, ContractViolation
from .graph_contract import PaperEvidenceGraph, SourceAnchor, StructuredVisualEvidence
from .models import DocumentBlock, DocumentIR
from .number_normalization import parse_plain_number
from .source_catalog import SourceCatalog
from .structured_source import StructuredVisualTemplate, TrustedTableCell

_TABLE_KINDS = frozenset({"table"})
_FIGURE_KINDS = frozenset({"figure", "image", "chart"})
_REGION_TOLERANCE = 0.001
_SUPERSCRIPTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾", "0123456789+-=()")
_SUBSCRIPTS = str.maketrans("₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎", "0123456789+-=()")
_SUPERSCRIPT_RUN = re.compile("[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾]+")
_TAG = re.compile(r"<\s*(/?)\s*([A-Za-z][A-Za-z0-9]*)[^>]*>")
_DASHES = str.maketrans({"−": "-", "–": "-", "—": "-", "‐": "-", "‑": "-"})

RecordRewrite = Callable[[str, str | None, str | None], None]


def value_key(text: str) -> str:
    """Comparable form of one cell value as written in HTML, Unicode or TeX."""

    def tag(match: re.Match[str]) -> str:
        name = match.group(2).lower()
        if name == "br":
            return " "
        if name == "sup" and not match.group(1):
            return "^"
        return ""

    value = html.unescape(_TAG.sub(tag, text))
    value = _SUPERSCRIPT_RUN.sub(
        lambda match: "^" + match.group(0).translate(_SUPERSCRIPTS), value
    )
    value = value.translate(_SUBSCRIPTS).translate(_DASHES)
    value = value.replace("{", "").replace("}", "").replace("$", "")
    value = unicodedata.normalize("NFC", value)
    return " ".join(value.split()).casefold()


def _loose_key(text: str) -> str:
    return re.sub(r"[\s^_]", "", value_key(text))


def _values_match(model_text: str, source_text: str) -> bool:
    if value_key(model_text) == value_key(source_text):
        return True
    if _loose_key(model_text) == _loose_key(source_text):
        return True
    model_number = parse_plain_number(model_text)
    source_number = parse_plain_number(source_text)
    return (
        model_number is not None
        and source_number is not None
        and _close(model_number, source_number)
    )


def _close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-12)


def _label_key(text: str) -> str:
    return _loose_key(text)


def _label_matches(model_column: str, source_column: str) -> bool:
    model = _label_key(model_column)
    if model == _label_key(source_column):
        return True
    return any(model == _label_key(part) for part in source_column.split(" / "))


def _same_region(region: object, bbox: object) -> bool:
    if bbox is None:
        return False
    return all(
        abs(float(getattr(region, key)) - float(getattr(bbox, key)))
        <= _REGION_TOLERANCE
        for key in ("x", "y", "width", "height")
    )


class _Binder:
    def __init__(self, document: DocumentIR, catalog: SourceCatalog) -> None:
        self.document = document
        self.spans = {span.id: span for span in catalog.spans}
        self.blocks = {
            block.id: block for page in document.pages for block in page.blocks
        }
        self.blocks_by_page: dict[int, list[DocumentBlock]] = {}
        for page in document.pages:
            self.blocks_by_page.setdefault(page.number, []).extend(page.blocks)
        structured = catalog.structured_sources
        unsafe = set() if structured is None else set(structured.unsafe_block_ids)
        self.templates: dict[str, StructuredVisualTemplate] = (
            {}
            if structured is None
            else {
                template.block_id: template
                for template in structured.visual_templates
                if template.block_id not in unsafe
            }
        )

    def block_for(
        self, visual: StructuredVisualEvidence, anchor: SourceAnchor
    ) -> tuple[DocumentBlock | None, ContractIssue | None]:
        kinds = _TABLE_KINDS if visual.visual_kind == "table" else _FIGURE_KINDS
        locator = anchor.locator
        if locator.source_span_id is not None:
            span = self.spans.get(locator.source_span_id)
            block = None if span is None else self.blocks.get(span.block_id)
            if block is None or block.kind not in kinds:
                return None, ContractIssue.PROVENANCE_VISUAL_BLOCK_NOT_FOUND
            return block, None
        if locator.page is None:
            return None, ContractIssue.PROVENANCE_VISUAL_BLOCK_NOT_FOUND
        candidates = [
            block
            for block in self.blocks_by_page.get(locator.page, ())
            if block.kind in kinds
        ]
        if locator.region is not None:
            candidates = [
                block
                for block in candidates
                if _same_region(locator.region, block.bbox)
            ]
        if locator.quote is not None:
            quote = unicodedata.normalize("NFC", locator.quote)
            quoted = [
                block
                for block in candidates
                if quote in unicodedata.normalize("NFC", block.text or "")
            ]
            candidates = quoted or candidates
        if not candidates:
            return None, ContractIssue.PROVENANCE_VISUAL_BLOCK_NOT_FOUND
        if len(candidates) > 1:
            # Several tables/figures on the page and nothing names one: the
            # values cannot be checked against a single source.
            return None, ContractIssue.PROVENANCE_VISUAL_BLOCK_AMBIGUOUS
        return candidates[0], None


def bind_structured_visuals(
    graph: PaperEvidenceGraph,
    document: DocumentIR,
    *,
    source_catalog: SourceCatalog,
    record: RecordRewrite | None = None,
) -> tuple[PaperEvidenceGraph, tuple[ContractViolation, ...]]:
    """Return the graph with Compiler-owned table coordinates and violations.

    Idempotent: a second call on its own output makes no rewrite, so it can be
    re-run after Claim isolation to recompute located violations.
    """

    if not graph.visual_evidence:
        return graph, ()
    binder = _Binder(document, source_catalog)
    anchors = {anchor.id: anchor for anchor in graph.anchors}
    violations: list[ContractViolation] = []
    payload: dict | None = None
    for index, visual in enumerate(graph.visual_evidence):
        if not visual.cells and not visual.series:
            continue
        anchor = anchors.get(visual.anchor_id)
        if anchor is None:
            continue
        block, issue = binder.block_for(visual, anchor)
        if issue is not None or block is None:
            violations.append(
                ContractViolation(
                    issue or ContractIssue.PROVENANCE_VISUAL_BLOCK_NOT_FOUND,
                    ("visual_evidence", index, "anchor_id"),
                )
            )
            continue
        template = binder.templates.get(block.id)
        if template is None or template.visual_kind != visual.visual_kind:
            violations.append(
                ContractViolation(
                    ContractIssue.PROVENANCE_VISUAL_VALUE_UNVERIFIABLE,
                    ("visual_evidence", index),
                )
            )
            continue
        if visual.visual_kind == "table":
            cells, cell_violations, changes = _bind_table_cells(index, visual, template)
            violations.extend(cell_violations)
            if changes:
                if payload is None:
                    payload = graph.model_dump(mode="json")
                payload["visual_evidence"][index]["cells"] = cells
                if record is not None:
                    for change in changes:
                        record("table_cell_coordinates_hydrated", visual.id, change)
        else:
            violations.extend(_check_figure_points(index, visual, template))
    if payload is None:
        return graph, tuple(violations)
    return PaperEvidenceGraph.model_validate(payload, strict=True), tuple(violations)


def _bind_table_cells(
    index: int,
    visual: StructuredVisualEvidence,
    template: StructuredVisualTemplate,
) -> tuple[list[dict], list[ContractViolation], list[str]]:
    violations: list[ContractViolation] = []
    changes: list[str] = []
    cells: list[dict] = []
    taken: set[tuple[int, str]] = set()
    for cell_index, cell in enumerate(visual.cells):
        dumped = cell.model_dump(mode="json")
        cells.append(dumped)
        base = ("visual_evidence", index, "cells", cell_index)
        candidates = [
            item
            for item in template.cells
            if _values_match(cell.raw_value, item.raw_value)
        ]
        if not candidates:
            violations.append(
                ContractViolation(
                    ContractIssue.PROVENANCE_VISUAL_VALUE_NOT_FOUND,
                    (*base, "raw_value"),
                )
            )
            continue
        chosen = _choose_cell(cell.row, cell.column, candidates)
        if chosen is None:
            violations.append(
                ContractViolation(ContractIssue.PROVENANCE_VISUAL_CELL_AMBIGUOUS, base)
            )
            continue
        coordinate = (chosen.row, chosen.column)
        if coordinate in taken:
            violations.append(
                ContractViolation(ContractIssue.PROVENANCE_VISUAL_CELL_AMBIGUOUS, base)
            )
            continue
        taken.add(coordinate)
        if (cell.row, cell.column) != coordinate:
            dumped["row"] = chosen.row
            dumped["column"] = chosen.column
            changes.append(f"cells[{cell_index}]")
        expected = (
            chosen.numeric_value
            if chosen.numeric_value is not None
            else parse_plain_number(chosen.raw_value)
        )
        if (
            expected is not None
            and cell.numeric_value is not None
            and not _close(float(cell.numeric_value), float(expected))
        ):
            violations.append(
                ContractViolation(
                    ContractIssue.PROVENANCE_VISUAL_NUMERIC_MISMATCH,
                    (*base, "numeric_value"),
                )
            )
    return cells, violations, changes


def _choose_cell(
    row: int, column: str, candidates: list[TrustedTableCell]
) -> TrustedTableCell | None:
    exact = [
        item
        for item in candidates
        if item.row == row and _label_matches(column, item.column)
    ]
    if len(exact) == 1:
        return exact[0]
    if len(candidates) == 1:
        return candidates[0]
    by_column = [item for item in candidates if _label_matches(column, item.column)]
    if len(by_column) == 1:
        return by_column[0]
    by_row = [item for item in candidates if item.row == row]
    if len(by_row) == 1:
        return by_row[0]
    return None


def _check_figure_points(
    index: int,
    visual: StructuredVisualEvidence,
    template: StructuredVisualTemplate,
) -> list[ContractViolation]:
    trusted = [
        (point.x, point.y) for series in template.series for point in series.points
    ]
    violations: list[ContractViolation] = []
    for series_index, series in enumerate(visual.series):
        for point_index, point in enumerate(series.points):
            if not any(
                _coordinate_matches(point.x, x) and _coordinate_matches(point.y, y)
                for x, y in trusted
            ):
                violations.append(
                    ContractViolation(
                        ContractIssue.PROVENANCE_VISUAL_VALUE_NOT_FOUND,
                        (
                            "visual_evidence",
                            index,
                            "series",
                            series_index,
                            "points",
                            point_index,
                        ),
                    )
                )
    return violations


def _coordinate_matches(model: object, source: object) -> bool:
    if isinstance(model, bool) or isinstance(source, bool):
        return model is source
    if isinstance(model, (int, float)) and isinstance(source, (int, float)):
        return _close(float(model), float(source))
    return _values_match(str(model), str(source))


__all__ = ["bind_structured_visuals", "value_key"]
