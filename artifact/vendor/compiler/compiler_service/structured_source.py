"""Trusted coverage boundary for model-visible structured visual sources.

Text ``SourceSpan`` objects cannot faithfully represent a table cell or plotted
point that exists only in retained structured JSON.  This module inventories
those values without asking a model to reproduce provenance.  Every record is
bound to an immutable artifact version, block, RFC 6901 JSON Pointer, exact JSON
scalar (or an exact substring of one scalar), and the block's frozen geometry.

Only deliberately supported table and figure shapes can be materialized as
``StructuredVisualEvidence``.  Unknown model-visible structure is never
silently dropped: strict construction fails closed, while audit construction
returns explicit warnings and unsafe record IDs.
"""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

from .graph_contract import (
    MAX_TABLE_CELLS,
    MAX_VISUAL_AXES,
    MAX_VISUAL_POINTS_PER_SERIES,
    MAX_VISUAL_SERIES,
    SourceAnchor,
    StructuredVisualEvidence,
    TableCell,
    VisualAxis,
    VisualPoint,
    VisualSeries,
    canonical_json,
)
from .models import DocumentBlock, DocumentIR

MAX_STRUCTURED_SOURCE_RECORDS = 4_096
MAX_STRUCTURED_SCALAR_CHARS = 16_000
MAX_STRUCTURED_DEPTH = 32

_VISUAL_KINDS = frozenset({"table", "figure", "chart", "image"})
_REDUNDANT_KEYS = frozenset(
    {"bbox", "img_path", "page_idx", "text", "text_level", "type"}
)
_FIGURE_TOP_LEVEL_KEYS = frozenset({"axes", "series"})
_AXIS_KEYS = frozenset({"id", "direction", "label", "unit", "scale"})
_SERIES_KEYS = frozenset({"id", "label", "x_axis_id", "y_axis_id", "points"})
_POINT_KEYS = frozenset({"x", "y", "uncertainty"})
_CELL_KEYS = frozenset({"row", "column", "raw_value", "numeric_value", "unit"})
_TAG_TOKEN = re.compile(r"<!--[\s\S]*?-->|<![^>]*>|</?[^>]+>|[^<]+")
_TAG_NAME = re.compile(
    r"^<\s*(?P<closing>/)?\s*(?P<name>[A-Za-z][A-Za-z0-9]*)"
    r"(?:\s+[^<>]*)?\s*(?P<self>/)?\s*>$"
)
_ALLOWED_TABLE_TAGS = frozenset({"table", "thead", "tbody", "tfoot", "tr", "th", "td"})
# Benign inline markup MinerU emits inside cells. It never changes cell
# boundaries; superscripts keep a ``^`` marker in the visible value.
_INLINE_CELL_TAGS = frozenset(
    {"b", "strong", "i", "em", "u", "sup", "sub", "span", "small", "font", "br"}
)
_VOID_CELL_TAGS = frozenset({"br"})
_SPAN_ATTRIBUTE = re.compile(
    r"""\b(?P<name>rowspan|colspan)\s*=\s*["']?(?P<value>[0-9]+)["']?""",
    re.IGNORECASE,
)
_MAX_TABLE_SPAN = 64

JsonScalar: TypeAlias = str | int | float | bool
RecordRole = Literal[
    "table_cell",
    "table_header",
    "table_metadata",
    "figure_point",
    "figure_axis",
    "figure_series",
    "unsupported",
]


class StructuredSourceError(ValueError):
    """Structured content cannot safely cross the extraction boundary."""

    def __init__(self, message: str, *, warnings: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.warnings = warnings


@dataclass(frozen=True, slots=True)
class StructuredSourceGeometry:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True, slots=True)
class StructuredSourceRecord:
    """One exact model-visible scalar with deterministic provenance."""

    id: str
    artifact_id: str
    version_id: str
    block_id: str
    page: int
    block_kind: str
    json_pointer: str
    exact_scalar: JsonScalar
    exact_scalar_json: str
    scalar_start_offset: int | None
    scalar_end_offset: int | None
    geometry: StructuredSourceGeometry | None
    role: RecordRole
    direct_evidence: bool
    safe_for_visual_hydration: bool


@dataclass(frozen=True, slots=True)
class TrustedTableCell:
    row: int
    column: str
    raw_value: str
    numeric_value: int | float | None
    unit: str | None
    source_record_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TrustedFigurePoint:
    x: int | float | str
    y: int | float | str
    uncertainty: int | float | None
    source_record_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TrustedFigureAxis:
    key: str
    direction: Literal["x", "y"]
    label: str
    unit: str | None
    scale: Literal["linear", "log", "categorical"]
    source_record_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TrustedFigureSeries:
    label: str
    x_axis_key: str
    y_axis_key: str
    points: tuple[TrustedFigurePoint, ...]
    source_record_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StructuredVisualTemplate:
    id: str
    block_id: str
    page: int
    visual_kind: Literal["table", "figure"]
    cells: tuple[TrustedTableCell, ...]
    axes: tuple[TrustedFigureAxis, ...]
    series: tuple[TrustedFigureSeries, ...]

    @property
    def source_record_ids(self) -> tuple[str, ...]:
        values: list[str] = []
        values.extend(
            record_id for cell in self.cells for record_id in cell.source_record_ids
        )
        values.extend(
            record_id for axis in self.axes for record_id in axis.source_record_ids
        )
        for series in self.series:
            values.extend(series.source_record_ids)
            values.extend(
                record_id
                for point in series.points
                for record_id in point.source_record_ids
            )
        return tuple(dict.fromkeys(values))


@dataclass(frozen=True, slots=True)
class StructuredSourceCatalog:
    artifact_id: str
    version_id: str
    records: tuple[StructuredSourceRecord, ...]
    visual_templates: tuple[StructuredVisualTemplate, ...]
    warnings: tuple[str, ...]
    unsafe_record_ids: tuple[str, ...]
    unsafe_block_ids: tuple[str, ...]

    @property
    def by_id(self) -> dict[str, StructuredSourceRecord]:
        return {record.id: record for record in self.records}

    @property
    def template_by_block_id(self) -> dict[str, StructuredVisualTemplate]:
        return {template.block_id: template for template in self.visual_templates}

    def assert_safe(self) -> None:
        if self.warnings or self.unsafe_record_ids:
            raise StructuredSourceError(
                "model-visible structured content is not safely representable",
                warnings=self.warnings,
            )


@dataclass(frozen=True, slots=True)
class HydratedStructuredVisual:
    anchor: SourceAnchor
    visual: StructuredVisualEvidence
    source_record_ids: tuple[str, ...]


@dataclass(slots=True)
class _CellDraft:
    row: int
    column: str
    raw_value: str
    numeric_value: int | float | None
    unit: str | None
    record_ids: list[str]


@dataclass(frozen=True, slots=True)
class _HtmlCell:
    row_index: int
    column_index: int
    is_header: bool
    exact_text: str
    start_offset: int
    end_offset: int
    visible_text: str = ""
    rowspan: int = 1
    colspan: int = 1


class _CatalogBuilder:
    def __init__(self, document: DocumentIR) -> None:
        self.document = document
        self.records: list[StructuredSourceRecord] = []
        self.templates: list[StructuredVisualTemplate] = []
        self.warnings: list[str] = []
        self.unsafe_ids: list[str] = []
        self.unsafe_block_ids: list[str] = []

    def build(self) -> StructuredSourceCatalog:
        for page in self.document.pages:
            for block in page.blocks:
                if block.kind not in _VISUAL_KINDS:
                    continue
                retained = retained_model_structure(block)
                if not retained:
                    continue
                self._consume_block(block, retained)
                if len(self.records) > MAX_STRUCTURED_SOURCE_RECORDS:
                    self._warn(
                        block,
                        "",
                        "structured source record capacity exceeded; content was not "
                        "silently truncated",
                    )
                    break
        record_ids = [record.id for record in self.records]
        if len(record_ids) != len(set(record_ids)):
            raise StructuredSourceError("deterministic structured record ID collision")
        return StructuredSourceCatalog(
            artifact_id=self.document.artifact.artifact_id,
            version_id=self.document.artifact.version_id,
            records=tuple(self.records),
            visual_templates=tuple(self.templates),
            warnings=tuple(dict.fromkeys(self.warnings)),
            unsafe_record_ids=tuple(dict.fromkeys(self.unsafe_ids)),
            unsafe_block_ids=tuple(dict.fromkeys(self.unsafe_block_ids)),
        )

    def _consume_block(self, block: DocumentBlock, retained: dict[str, Any]) -> None:
        supported: set[str] = set()
        if block.kind == "table":
            cells: list[_CellDraft] = []
            if "table_body" in retained:
                supported.add("table_body")
                cells.extend(self._consume_html_table(block, retained["table_body"]))
            if "recognized_cells" in retained:
                supported.add("recognized_cells")
                cells = self._merge_cells(
                    block,
                    cells,
                    self._consume_recognized_cells(block, retained["recognized_cells"]),
                )
            if cells:
                self.templates.append(
                    StructuredVisualTemplate(
                        id=_stable_id(
                            "structured-template",
                            self.document,
                            block,
                            "",
                            None,
                            None,
                            {"visual_kind": "table"},
                        ),
                        block_id=block.id,
                        page=block.page,
                        visual_kind="table",
                        cells=tuple(
                            TrustedTableCell(
                                row=cell.row,
                                column=cell.column,
                                raw_value=cell.raw_value,
                                numeric_value=cell.numeric_value,
                                unit=cell.unit,
                                source_record_ids=tuple(dict.fromkeys(cell.record_ids)),
                            )
                            for cell in sorted(
                                cells, key=lambda item: (item.row, item.column)
                            )
                        ),
                        axes=(),
                        series=(),
                    )
                )
        elif "axes" in retained or "series" in retained:
            supported.update(_FIGURE_TOP_LEVEL_KEYS)
            template = self._consume_figure(block, retained)
            if template is not None:
                self.templates.append(template)

        for key in sorted(set(retained) - supported):
            pointer = "/" + _pointer_token(key)
            ids = self._consume_unsupported(block, retained[key], pointer, depth=1)
            self.unsafe_ids.extend(ids)
            self._warn(
                block,
                pointer,
                "model-visible structured content has no lossless visual mapping",
            )

    def _consume_recognized_cells(
        self, block: DocumentBlock, value: Any
    ) -> list[_CellDraft]:
        if not isinstance(value, list):
            ids = self._consume_unsupported(block, value, "/recognized_cells", depth=1)
            self.unsafe_ids.extend(ids)
            self._warn(
                block,
                "/recognized_cells",
                "recognized_cells must be a list of exact cell objects",
            )
            return []
        if len(value) > MAX_TABLE_CELLS:
            self._warn(
                block,
                "/recognized_cells",
                f"table has more than {MAX_TABLE_CELLS} retained cells",
            )
            return []
        output: list[_CellDraft] = []
        for index, item in enumerate(value):
            pointer = f"/recognized_cells/{index}"
            if not isinstance(item, dict) or not set(item) <= _CELL_KEYS:
                ids = self._consume_unsupported(block, item, pointer, depth=2)
                self.unsafe_ids.extend(ids)
                self._warn(block, pointer, "recognized cell shape is not lossless")
                continue
            row = item.get("row")
            column = item.get("column")
            raw_value = item.get("raw_value")
            numeric_value = item.get("numeric_value")
            unit = item.get("unit")
            valid = (
                type(row) is int
                and row >= 1
                and isinstance(column, str)
                and bool(column.strip())
                and isinstance(raw_value, str)
                and bool(raw_value.strip())
                and raw_value == raw_value.strip()
                and (numeric_value is None or type(numeric_value) in {int, float})
                and (unit is None or (isinstance(unit, str) and bool(unit.strip())))
            )
            if not valid:
                ids = self._consume_unsupported(block, item, pointer, depth=2)
                self.unsafe_ids.extend(ids)
                self._warn(block, pointer, "recognized cell fields are invalid")
                continue
            record_ids: list[str] = []
            for field in ("row", "column", "raw_value", "numeric_value", "unit"):
                scalar = item.get(field)
                if scalar is None:
                    continue
                record = self._record(
                    block,
                    f"{pointer}/{field}",
                    scalar,
                    role=(
                        "table_cell"
                        if field in {"raw_value", "numeric_value"}
                        else "table_metadata"
                    ),
                    direct_evidence=field in {"raw_value", "numeric_value"},
                    safe=True,
                )
                record_ids.append(record.id)
            output.append(
                _CellDraft(
                    row=row,
                    column=column.strip(),
                    raw_value=raw_value,
                    numeric_value=numeric_value,
                    unit=unit,
                    record_ids=record_ids,
                )
            )
        return output

    def _consume_html_table(self, block: DocumentBlock, value: Any) -> list[_CellDraft]:
        pointer = "/table_body"
        if not isinstance(value, str) or not value.strip():
            ids = self._consume_unsupported(block, value, pointer, depth=1)
            self.unsafe_ids.extend(ids)
            self._warn(block, pointer, "table_body is not a non-empty HTML scalar")
            return []
        try:
            rows = _parse_simple_html_table(value)
            grid = _table_grid(rows)
        except StructuredSourceError as error:
            record = self._record(
                block,
                pointer,
                value,
                role="unsupported",
                direct_evidence=False,
                safe=False,
            )
            self.unsafe_ids.append(record.id)
            self._warn(block, pointer, str(error))
            return []
        header_count = 0
        for row in rows:
            if row and all(item.is_header for item in row):
                header_count += 1
            else:
                break
        data_grid = grid[header_count:]
        if not data_grid:
            self._warn(block, pointer, "table_body has no data rows")
            return []
        width = max(len(row) for row in grid)
        data_cells = sum(
            1
            for row in data_grid
            for item in row
            if item is not None and item.exact_text
        )
        if data_cells > MAX_TABLE_CELLS:
            self._warn(
                block,
                pointer,
                f"table_body has more than {MAX_TABLE_CELLS} data cells",
            )
            return []
        columns = _column_labels(grid[:header_count], width)
        for row in rows[:header_count]:
            for item in row:
                if not item.exact_text:
                    continue
                self._record(
                    block,
                    pointer,
                    item.exact_text,
                    start=item.start_offset,
                    end=item.end_offset,
                    role="table_header",
                    direct_evidence=False,
                    safe=True,
                )
        record_ids: dict[tuple[int, int], str] = {}
        output: list[_CellDraft] = []
        for row_number, row in enumerate(data_grid, start=1):
            for column_index, item in enumerate(row):
                if item is None or not item.exact_text:
                    continue
                key = (item.row_index, item.column_index)
                record_id = record_ids.get(key)
                if record_id is None:
                    record_id = self._record(
                        block,
                        pointer,
                        item.exact_text,
                        start=item.start_offset,
                        end=item.end_offset,
                        role="table_cell",
                        direct_evidence=True,
                        safe=True,
                    ).id
                    record_ids[key] = record_id
                output.append(
                    _CellDraft(
                        row=row_number,
                        column=columns[column_index],
                        raw_value=item.visible_text or item.exact_text,
                        numeric_value=None,
                        unit=None,
                        record_ids=[record_id],
                    )
                )
        return output

    def _merge_cells(
        self,
        block: DocumentBlock,
        first: list[_CellDraft],
        second: list[_CellDraft],
    ) -> list[_CellDraft]:
        by_coordinate = {(item.row, item.column): item for item in first}
        for item in second:
            key = (item.row, item.column)
            previous = by_coordinate.get(key)
            if previous is None:
                by_coordinate[key] = item
                continue
            if previous.raw_value != item.raw_value:
                self._warn(
                    block,
                    "/recognized_cells",
                    "recognized_cells conflicts with table_body at one coordinate",
                )
                self.unsafe_ids.extend(previous.record_ids)
                self.unsafe_ids.extend(item.record_ids)
                continue
            if (
                previous.unit is not None
                and item.unit is not None
                and previous.unit != item.unit
            ):
                self._warn(block, "/recognized_cells", "cell units conflict")
                self.unsafe_ids.extend(previous.record_ids + item.record_ids)
                continue
            previous.numeric_value = (
                item.numeric_value
                if item.numeric_value is not None
                else previous.numeric_value
            )
            previous.unit = item.unit or previous.unit
            previous.record_ids.extend(item.record_ids)
        return list(by_coordinate.values())

    def _consume_figure(
        self, block: DocumentBlock, retained: dict[str, Any]
    ) -> StructuredVisualTemplate | None:
        if set(retained) - _FIGURE_TOP_LEVEL_KEYS:
            return None
        axes_value = retained.get("axes")
        series_value = retained.get("series")
        if not isinstance(axes_value, list) or not isinstance(series_value, list):
            self._warn(block, "", "figure axes and series must both be lists")
            return None
        if not 1 <= len(axes_value) <= MAX_VISUAL_AXES:
            self._warn(block, "/axes", "figure axis capacity or emptiness is invalid")
            return None
        if not 1 <= len(series_value) <= MAX_VISUAL_SERIES:
            self._warn(
                block, "/series", "figure series capacity or emptiness is invalid"
            )
            return None

        axes: list[TrustedFigureAxis] = []
        axis_keys: set[str] = set()
        for index, item in enumerate(axes_value):
            pointer = f"/axes/{index}"
            if not isinstance(item, dict) or not set(item) <= _AXIS_KEYS:
                self._warn(block, pointer, "figure axis shape is unsupported")
                return None
            direction = item.get("direction")
            label = item.get("label")
            unit = item.get("unit")
            scale = item.get("scale", "linear")
            key = item.get("id", f"axis_{index + 1}")
            if (
                direction not in {"x", "y"}
                or not isinstance(label, str)
                or not label.strip()
                or (
                    unit is not None and (not isinstance(unit, str) or not unit.strip())
                )
                or scale not in {"linear", "log", "categorical"}
                or not isinstance(key, str)
                or not key
                or key in axis_keys
            ):
                self._warn(block, pointer, "figure axis fields are invalid")
                return None
            axis_keys.add(key)
            record_ids = [
                self._record(
                    block,
                    f"{pointer}/{field}",
                    value,
                    role="figure_axis",
                    direct_evidence=False,
                    safe=True,
                ).id
                for field, value in item.items()
                if value is not None
            ]
            axes.append(
                TrustedFigureAxis(
                    key=key,
                    direction=direction,
                    label=label.strip(),
                    unit=unit,
                    scale=scale,
                    source_record_ids=tuple(record_ids),
                )
            )
        default_x = next((item.key for item in axes if item.direction == "x"), None)
        default_y = next((item.key for item in axes if item.direction == "y"), None)
        if default_x is None or default_y is None:
            self._warn(block, "/axes", "figure requires x and y axes")
            return None

        series: list[TrustedFigureSeries] = []
        for series_index, item in enumerate(series_value):
            pointer = f"/series/{series_index}"
            if not isinstance(item, dict) or not set(item) <= _SERIES_KEYS:
                self._warn(block, pointer, "figure series shape is unsupported")
                return None
            label = item.get("label")
            x_axis = item.get("x_axis_id", default_x)
            y_axis = item.get("y_axis_id", default_y)
            points_value = item.get("points")
            if (
                not isinstance(label, str)
                or not label.strip()
                or x_axis not in axis_keys
                or y_axis not in axis_keys
                or not isinstance(points_value, list)
                or not 1 <= len(points_value) <= MAX_VISUAL_POINTS_PER_SERIES
            ):
                self._warn(block, pointer, "figure series fields are invalid")
                return None
            series_record_ids = [
                self._record(
                    block,
                    f"{pointer}/{field}",
                    item[field],
                    role="figure_series",
                    direct_evidence=False,
                    safe=True,
                ).id
                for field in ("id", "label", "x_axis_id", "y_axis_id")
                if field in item
            ]
            points: list[TrustedFigurePoint] = []
            for point_index, point in enumerate(points_value):
                point_pointer = f"{pointer}/points/{point_index}"
                if (
                    not isinstance(point, dict)
                    or not set(point) <= _POINT_KEYS
                    or "x" not in point
                    or "y" not in point
                    or not _point_scalar(point["x"])
                    or not _point_scalar(point["y"])
                    or (
                        point.get("uncertainty") is not None
                        and type(point["uncertainty"]) not in {int, float}
                    )
                ):
                    self._warn(block, point_pointer, "figure point is not lossless")
                    return None
                point_record_ids = [
                    self._record(
                        block,
                        f"{point_pointer}/{field}",
                        value,
                        role="figure_point",
                        direct_evidence=True,
                        safe=True,
                    ).id
                    for field, value in point.items()
                    if value is not None
                ]
                points.append(
                    TrustedFigurePoint(
                        x=point["x"],
                        y=point["y"],
                        uncertainty=point.get("uncertainty"),
                        source_record_ids=tuple(point_record_ids),
                    )
                )
            series.append(
                TrustedFigureSeries(
                    label=label.strip(),
                    x_axis_key=x_axis,
                    y_axis_key=y_axis,
                    points=tuple(points),
                    source_record_ids=tuple(series_record_ids),
                )
            )
        return StructuredVisualTemplate(
            id=_stable_id(
                "structured-template",
                self.document,
                block,
                "",
                None,
                None,
                {"visual_kind": "figure"},
            ),
            block_id=block.id,
            page=block.page,
            visual_kind="figure",
            cells=(),
            axes=tuple(axes),
            series=tuple(series),
        )

    def _consume_unsupported(
        self, block: DocumentBlock, value: Any, pointer: str, *, depth: int
    ) -> list[str]:
        if depth > MAX_STRUCTURED_DEPTH:
            self._warn(block, pointer, "structured JSON exceeds safe depth")
            return []
        if value is None:
            return []
        if type(value) in {str, int, float, bool}:
            record = self._record(
                block,
                pointer,
                value,
                role="unsupported",
                direct_evidence=False,
                safe=False,
            )
            return [record.id]
        output: list[str] = []
        if isinstance(value, list):
            for index, item in enumerate(value):
                output.extend(
                    self._consume_unsupported(
                        block, item, f"{pointer}/{index}", depth=depth + 1
                    )
                )
            return output
        if isinstance(value, dict):
            for key in sorted(value):
                output.extend(
                    self._consume_unsupported(
                        block,
                        value[key],
                        f"{pointer}/{_pointer_token(key)}",
                        depth=depth + 1,
                    )
                )
            return output
        self._warn(block, pointer, "structured JSON contains an unsupported value")
        return output

    def _record(
        self,
        block: DocumentBlock,
        pointer: str,
        scalar: JsonScalar,
        *,
        role: RecordRole,
        direct_evidence: bool,
        safe: bool,
        start: int | None = None,
        end: int | None = None,
    ) -> StructuredSourceRecord:
        exact_json = canonical_json(scalar)
        if len(exact_json) > MAX_STRUCTURED_SCALAR_CHARS:
            self._warn(block, pointer, "structured scalar exceeds safe length")
            safe = False
        record = StructuredSourceRecord(
            id=_stable_id(
                "structured-source",
                self.document,
                block,
                pointer,
                start,
                end,
                scalar,
            ),
            artifact_id=self.document.artifact.artifact_id,
            version_id=self.document.artifact.version_id,
            block_id=block.id,
            page=block.page,
            block_kind=block.kind,
            json_pointer=pointer,
            exact_scalar=scalar,
            exact_scalar_json=exact_json,
            scalar_start_offset=start,
            scalar_end_offset=end,
            geometry=_geometry(block),
            role=role,
            direct_evidence=direct_evidence,
            safe_for_visual_hydration=safe,
        )
        self.records.append(record)
        if not safe:
            self.unsafe_ids.append(record.id)
        return record

    def _warn(self, block: DocumentBlock, pointer: str, message: str) -> None:
        location = pointer or "/"
        self.unsafe_block_ids.append(block.id)
        self.warnings.append(f"{block.id}{location}: {message}")


def build_structured_source_catalog(
    document: DocumentIR,
    *,
    fail_on_unsafe: bool = True,
) -> StructuredSourceCatalog:
    """Inventory retained visual structure and fail closed by default."""

    catalog = _CatalogBuilder(document).build()
    if fail_on_unsafe:
        catalog.assert_safe()
    return catalog


def model_visible_block_content(
    block: DocumentBlock,
) -> tuple[str | None, dict[str, Any]]:
    """Return the single authoritative compact text/structure view for a block."""

    model_text = _model_visible_block_text(block)
    if block.kind not in _VISUAL_KINDS:
        return model_text, {}
    output: dict[str, Any] = {}
    for key, value in block.structured.items():
        if key in _REDUNDANT_KEYS or _empty(value):
            continue
        if model_text is not None and _duplicates_text(value, model_text):
            continue
        output[key] = value
    return model_text, output


def retained_model_structure(block: DocumentBlock) -> dict[str, Any]:
    """Return structure retained by the compact model-facing Document IR."""

    return model_visible_block_content(block)[1]


def validate_structured_review_ledger(
    catalog: StructuredSourceCatalog, reviewed_record_ids: list[str] | tuple[str, ...]
) -> None:
    """Require every retained structured scalar to be reviewed exactly once."""

    reviewed = list(reviewed_record_ids)
    if len(reviewed) != len(set(reviewed)):
        raise StructuredSourceError("structured review ledger contains duplicates")
    expected = {record.id for record in catalog.records}
    supplied = set(reviewed)
    if supplied != expected:
        missing = sorted(expected - supplied)
        unknown = sorted(supplied - expected)
        raise StructuredSourceError(
            "structured review ledger is incomplete or contains unknown IDs: "
            f"missing={missing}, unknown={unknown}"
        )


def structured_record_payload(record: StructuredSourceRecord) -> dict[str, Any]:
    """Return the bounded model-facing representation of one trusted record."""

    return {
        "structured_source_id": record.id,
        "page": record.page,
        "block_kind": record.block_kind,
        "json_pointer": record.json_pointer,
        "exact_scalar": record.exact_scalar,
        "scalar_start_offset": record.scalar_start_offset,
        "scalar_end_offset": record.scalar_end_offset,
        "geometry": (
            None
            if record.geometry is None
            else {
                "x": record.geometry.x,
                "y": record.geometry.y,
                "width": record.geometry.width,
                "height": record.geometry.height,
            }
        ),
        "role": record.role,
        "direct_evidence": record.direct_evidence,
    }


def hydrate_selected_structured_visuals(
    document: DocumentIR,
    catalog: StructuredSourceCatalog,
    selected_record_ids: list[str] | tuple[str, ...],
) -> tuple[HydratedStructuredVisual, ...]:
    """Materialize strict visual evidence for selected exact source records."""

    if (
        catalog.artifact_id != document.artifact.artifact_id
        or catalog.version_id != document.artifact.version_id
    ):
        raise StructuredSourceError("structured catalog belongs to another artifact")
    selected = list(selected_record_ids)
    if not selected or len(selected) != len(set(selected)):
        raise StructuredSourceError(
            "selected structured source IDs must be non-empty and unique"
        )
    records = catalog.by_id
    unknown = set(selected) - records.keys()
    if unknown:
        raise StructuredSourceError(
            f"selected structured source IDs are unknown: {sorted(unknown)}"
        )
    for record_id in selected:
        record = records[record_id]
        if not record.safe_for_visual_hydration:
            raise StructuredSourceError(
                f"structured source {record_id} cannot hydrate visual evidence"
            )
        _validate_frozen_record(document, record)

    selected_by_block: dict[str, set[str]] = {}
    for record_id in selected:
        selected_by_block.setdefault(records[record_id].block_id, set()).add(record_id)
    templates = catalog.template_by_block_id
    block_index = {block.id: block for page in document.pages for block in page.blocks}
    output: list[HydratedStructuredVisual] = []
    for block_id in sorted(selected_by_block):
        template = templates.get(block_id)
        if template is None:
            raise StructuredSourceError(
                f"block {block_id} has no safe structured visual template"
            )
        block = block_index[block_id]
        selected_for_block = selected_by_block[block_id]
        anchor = _hydrate_anchor(document, block)
        if template.visual_kind == "table":
            chosen_cells = [
                item
                for item in template.cells
                if selected_for_block & set(item.source_record_ids)
            ]
            if not chosen_cells:
                raise StructuredSourceError(
                    "table metadata/header cannot be direct visual evidence alone"
                )
            cells = [
                TableCell(
                    row=item.row,
                    column=item.column,
                    raw_value=item.raw_value,
                    numeric_value=item.numeric_value,
                    unit=item.unit,
                )
                for item in chosen_cells
            ]
            visual = StructuredVisualEvidence(
                id=_visual_id(template, selected_for_block),
                anchor_id=anchor.id,
                visual_kind="table",
                axes=[],
                series=[],
                cells=cells,
                features=[],
            )
            grounded_ids = tuple(
                record_id
                for item in chosen_cells
                for record_id in item.source_record_ids
                if record_id in selected_for_block
            )
        else:
            axis_ids = {
                axis.key: _entity_id(
                    "axis", {"template_id": template.id, "axis_key": axis.key}
                )
                for axis in template.axes
            }
            axes = [
                VisualAxis(
                    id=axis_ids[item.key],
                    direction=item.direction,
                    label=item.label,
                    unit=item.unit,
                    scale=item.scale,
                )
                for item in template.axes
            ]
            series: list[VisualSeries] = []
            grounded: list[str] = []
            for series_index, item in enumerate(template.series):
                chosen_points = [
                    point
                    for point in item.points
                    if selected_for_block & set(point.source_record_ids)
                ]
                if not chosen_points:
                    continue
                grounded.extend(
                    record_id
                    for point in chosen_points
                    for record_id in point.source_record_ids
                    if record_id in selected_for_block
                )
                series.append(
                    VisualSeries(
                        id=_entity_id(
                            "series",
                            {
                                "template_id": template.id,
                                "series_index": series_index,
                                "label": item.label,
                            },
                        ),
                        label=item.label,
                        x_axis_id=axis_ids[item.x_axis_key],
                        y_axis_id=axis_ids[item.y_axis_key],
                        points=[
                            VisualPoint(
                                x=point.x,
                                y=point.y,
                                uncertainty=point.uncertainty,
                            )
                            for point in chosen_points
                        ],
                    )
                )
            if not series:
                raise StructuredSourceError(
                    "figure axis/series metadata cannot be direct evidence alone"
                )
            visual = StructuredVisualEvidence(
                id=_visual_id(template, selected_for_block),
                anchor_id=anchor.id,
                visual_kind="figure",
                axes=axes,
                series=series,
                cells=[],
                features=[],
            )
            grounded_ids = tuple(grounded)
        output.append(
            HydratedStructuredVisual(
                anchor=anchor,
                visual=visual,
                source_record_ids=tuple(dict.fromkeys(grounded_ids)),
            )
        )
    return tuple(output)


def _validate_frozen_record(
    document: DocumentIR, record: StructuredSourceRecord
) -> None:
    block = next(
        (
            block
            for page in document.pages
            for block in page.blocks
            if block.id == record.block_id
        ),
        None,
    )
    if block is None or block.page != record.page or block.kind != record.block_kind:
        raise StructuredSourceError(
            f"structured source {record.id} block identity changed"
        )
    if _geometry(block) != record.geometry:
        raise StructuredSourceError(f"structured source {record.id} geometry changed")
    value = _resolve_pointer(block.structured, record.json_pointer)
    if record.scalar_start_offset is None:
        if canonical_json(value) != record.exact_scalar_json:
            raise StructuredSourceError(f"structured source {record.id} scalar changed")
        return
    if not isinstance(value, str) or record.scalar_end_offset is None:
        raise StructuredSourceError(
            f"structured source {record.id} scalar offsets are invalid"
        )
    exact = value[record.scalar_start_offset : record.scalar_end_offset]
    if exact != record.exact_scalar:
        raise StructuredSourceError(
            f"structured source {record.id} scalar fragment changed"
        )


def _hydrate_anchor(document: DocumentIR, block: DocumentBlock) -> SourceAnchor:
    geometry = _geometry(block)
    region = (
        None
        if geometry is None
        else {
            "x": geometry.x,
            "y": geometry.y,
            "width": geometry.width,
            "height": geometry.height,
            "unit": "normalized",
        }
    )
    anchor_id = _entity_id(
        "anchor",
        {
            "artifact_id": document.artifact.artifact_id,
            "version_id": document.artifact.version_id,
            "block_id": block.id,
            "structured": True,
        },
    )
    return SourceAnchor.model_validate(
        {
            "id": anchor_id,
            "artifact_id": document.artifact.artifact_id,
            "version_id": document.artifact.version_id,
            "artifact_sha256": document.artifact.artifact_sha256,
            "media_type": document.artifact.media_type,
            "anchor_type": "table" if block.kind == "table" else "figure",
            "locator": {
                "page": block.page,
                "section": None,
                "paragraph": None,
                "figure": None,
                "table": None,
                "row_range": None,
                "column_names": [],
                "series": None,
                "region": region,
                "quote": None,
            },
        },
        strict=True,
    )


def _parse_simple_html_table(value: str) -> tuple[tuple[_HtmlCell, ...], ...]:
    """Parse one MinerU table into source cells with exact offsets.

    ``rowspan``/``colspan`` attributes are honoured by :func:`_table_grid`;
    benign inline tags may appear inside a cell; empty cells are kept so the
    column grid stays aligned. Anything else fails closed.
    """

    stack: list[str] = []
    rows: list[list[_HtmlCell]] = []
    current_row: list[_HtmlCell] | None = None
    current_cell: tuple[str, int, int, int, int] | None = None
    inline: list[str] = []
    saw_table = False
    cursor = 0
    for token in _TAG_TOKEN.finditer(value):
        if token.start() != cursor:
            raise StructuredSourceError("table_body contains malformed HTML")
        cursor = token.end()
        raw = token.group(0)
        if not raw.startswith("<"):
            if current_cell is None and raw.strip():
                raise StructuredSourceError("table_body has text outside cells")
            continue
        if raw.startswith("<!--"):
            if current_cell is None:
                continue
            raise StructuredSourceError("table_body cell contains a comment")
        match = _TAG_NAME.match(raw)
        if match is None:
            raise StructuredSourceError("table_body uses unsupported HTML markup")
        name = match.group("name").lower()
        closing = bool(match.group("closing"))
        if current_cell is not None and name in _INLINE_CELL_TAGS:
            if name in _VOID_CELL_TAGS:
                continue
            if match.group("self"):
                raise StructuredSourceError("table_body cell markup is invalid")
            if closing:
                if not inline or inline[-1] != name:
                    raise StructuredSourceError("table_body cell markup is unbalanced")
                inline.pop()
            else:
                inline.append(name)
            continue
        if name not in _ALLOWED_TABLE_TAGS or match.group("self"):
            raise StructuredSourceError("table_body uses unsupported HTML tags")
        if not closing:
            if name == "table":
                if saw_table:
                    raise StructuredSourceError("table_body contains multiple tables")
                saw_table = True
            if name == "tr":
                if current_row is not None:
                    raise StructuredSourceError("table_body nests rows")
                current_row = []
            if name in {"td", "th"}:
                if current_row is None or current_cell is not None:
                    raise StructuredSourceError("table_body cell nesting is invalid")
                rowspan, colspan = _cell_spans(raw)
                current_cell = (name, token.end(), len(current_row), rowspan, colspan)
            stack.append(name)
            continue
        if not stack or stack[-1] != name:
            raise StructuredSourceError("table_body tags are not balanced")
        stack.pop()
        if name in {"td", "th"}:
            if current_cell is None or current_row is None or inline:
                raise StructuredSourceError("table_body closing cell is invalid")
            opened, start, column_index, rowspan, colspan = current_cell
            raw_text = value[start : token.start()]
            left = len(raw_text) - len(raw_text.lstrip())
            right = len(raw_text.rstrip())
            exact = raw_text[left:right] if right > left else ""
            current_row.append(
                _HtmlCell(
                    row_index=len(rows),
                    column_index=column_index,
                    is_header=opened == "th",
                    exact_text=exact,
                    start_offset=start + left,
                    end_offset=start + max(left, right),
                    visible_text=visible_cell_text(exact),
                    rowspan=rowspan,
                    colspan=colspan,
                )
            )
            current_cell = None
        elif name == "tr":
            if current_row is None or current_cell is not None:
                raise StructuredSourceError("table_body row is incomplete")
            rows.append(current_row)
            current_row = None
    if (
        cursor != len(value)
        or stack
        or current_row is not None
        or current_cell is not None
    ):
        raise StructuredSourceError("table_body HTML is incomplete")
    if not saw_table or not rows:
        raise StructuredSourceError("table_body contains no table rows")
    if not any(item.exact_text for row in rows for item in row):
        raise StructuredSourceError("table_body contains only empty cells")
    return tuple(tuple(row) for row in rows)


def _cell_spans(tag: str) -> tuple[int, int]:
    spans = {"rowspan": 1, "colspan": 1}
    for match in _SPAN_ATTRIBUTE.finditer(tag):
        number = int(match.group("value"))
        if not 1 <= number <= _MAX_TABLE_SPAN:
            raise StructuredSourceError("table_body cell span is out of range")
        spans[match.group("name").lower()] = number
    return spans["rowspan"], spans["colspan"]


def _table_grid(
    rows: tuple[tuple[_HtmlCell, ...], ...],
) -> list[list[_HtmlCell | None]]:
    """Expand row/column spans so every cell lands on its true coordinate."""

    occupied: dict[tuple[int, int], _HtmlCell] = {}
    height = len(rows)
    width = 0
    for row_index, row in enumerate(rows):
        column = 0
        for item in row:
            while (row_index, column) in occupied:
                column += 1
            for row_offset in range(item.rowspan):
                for column_offset in range(item.colspan):
                    position = (row_index + row_offset, column + column_offset)
                    if position in occupied:
                        raise StructuredSourceError("table_body cell spans overlap")
                    occupied[position] = item
            height = max(height, row_index + item.rowspan)
            column += item.colspan
            width = max(width, column)
    if width == 0:
        raise StructuredSourceError("table_body has no columns")
    if width * height > MAX_STRUCTURED_SOURCE_RECORDS:
        raise StructuredSourceError("table_body grid is too large")
    return [
        [occupied.get((row_index, column)) for column in range(width)]
        for row_index in range(height)
    ]


def _column_labels(
    header_grid: list[list[_HtmlCell | None]], width: int, *, make_unique: bool = True
) -> list[str]:
    labels: list[str] = []
    for column in range(width):
        parts: list[str] = []
        for row in header_grid:
            item = row[column] if column < len(row) else None
            text = None if item is None else (item.visible_text or item.exact_text)
            if text and text not in parts:
                parts.append(text)
        labels.append(" / ".join(parts) if parts else f"column_{column + 1}")
    if not make_unique:
        return labels
    seen: dict[str, int] = {}
    unique: list[str] = []
    for label in labels:
        count = seen.get(label, 0) + 1
        seen[label] = count
        unique.append(label if count == 1 else f"{label} #{count}")
    return unique


def table_quality_grid(value: str) -> tuple[list[str], list[list[str]]]:
    """Expand MinerU spans for table checks without dropping shorter HTML rows.

    MinerU also emits headers as ``td``. In that case retain the inspector's
    first-row header convention, including its explicitly spanned subheaders.
    Duplicate labels stay duplicated so the quality check can report them.
    """
    rows = _parse_simple_html_table(value)
    grid = _table_grid(rows)
    header_count = 0
    for row in rows:
        if row and all(cell.is_header for cell in row):
            header_count += 1
        else:
            break
    if not header_count:
        header_count = max((cell.rowspan for cell in rows[0]), default=1)
    if header_count >= len(grid):
        raise StructuredSourceError("table_body has no data rows")
    columns = _column_labels(grid[:header_count], len(grid[0]), make_unique=False)
    body = [
        [cell.visible_text if cell is not None else "" for cell in row]
        for row in grid[header_count:]
    ]
    return columns, body


_CELL_TAG = re.compile(r"<\s*(/?)\s*([A-Za-z][A-Za-z0-9]*)[^>]*>")


def visible_cell_text(exact: str) -> str:
    """Display text of one cell: entities decoded, ``^`` before superscripts."""

    if not exact:
        return ""
    output: list[str] = []
    position = 0
    for match in _CELL_TAG.finditer(exact):
        output.append(exact[position : match.start()])
        position = match.end()
        closing, name = match.group(1), match.group(2).lower()
        if name == "br":
            output.append(" ")
        elif name == "sup" and not closing:
            output.append("^")
    output.append(exact[position:])
    text = html.unescape("".join(output))
    return " ".join(unicodedata.normalize("NFC", text).split())


def _resolve_pointer(value: Any, pointer: str) -> Any:
    if pointer == "":
        return value
    if not pointer.startswith("/"):
        raise StructuredSourceError("structured source JSON Pointer is invalid")
    current = value
    for encoded in pointer[1:].split("/"):
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                raise StructuredSourceError("structured source JSON Pointer is stale")
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit() or int(token) >= len(current):
                raise StructuredSourceError("structured source JSON Pointer is stale")
            current = current[int(token)]
        else:
            raise StructuredSourceError("structured source JSON Pointer is stale")
    return current


def _stable_id(
    prefix: str,
    document: DocumentIR,
    block: DocumentBlock,
    pointer: str,
    start: int | None,
    end: int | None,
    scalar: Any,
) -> str:
    return _entity_id(
        prefix,
        {
            "artifact_id": document.artifact.artifact_id,
            "version_id": document.artifact.version_id,
            "block_id": block.id,
            "json_pointer": pointer,
            "scalar_start_offset": start,
            "scalar_end_offset": end,
            "exact_scalar": scalar,
        },
    )


def _entity_id(prefix: str, payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
    return f"{prefix}:structured:{digest}"


def _visual_id(
    template: StructuredVisualTemplate, selected_record_ids: set[str]
) -> str:
    return _entity_id(
        "visual",
        {
            "template_id": template.id,
            "selected_record_ids": sorted(selected_record_ids),
        },
    )


def _pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _geometry(block: DocumentBlock) -> StructuredSourceGeometry | None:
    if block.bbox is None:
        return None
    return StructuredSourceGeometry(
        x=block.bbox.x,
        y=block.bbox.y,
        width=block.bbox.width,
        height=block.bbox.height,
    )


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _duplicates_text(value: Any, text: str) -> bool:
    if isinstance(value, str):
        return value in text
    return isinstance(value, list) and all(
        isinstance(item, str) and item in text for item in value
    )


def _model_visible_block_text(block: DocumentBlock) -> str | None:
    """Apply the same table-body de-duplication as compact_document_ir."""

    text = block.text
    if block.kind != "table" or text is None:
        return text
    table_body = block.structured.get("table_body")
    if not isinstance(table_body, str) or not table_body:
        return text
    normalized_body = unicodedata.normalize("NFC", table_body)
    if normalized_body not in text:
        return text
    before, _, after = text.partition(normalized_body)
    remainder = "\n".join(
        fragment.strip() for fragment in (before, after) if fragment.strip()
    )
    return remainder or None


def _point_scalar(value: Any) -> bool:
    return type(value) in {int, float} or (
        isinstance(value, str) and bool(value.strip()) and value == value.strip()
    )


__all__ = [
    "HydratedStructuredVisual",
    "MAX_STRUCTURED_SOURCE_RECORDS",
    "StructuredSourceCatalog",
    "StructuredSourceError",
    "StructuredSourceGeometry",
    "StructuredSourceRecord",
    "StructuredVisualTemplate",
    "build_structured_source_catalog",
    "hydrate_selected_structured_visuals",
    "model_visible_block_content",
    "retained_model_structure",
    "structured_record_payload",
    "validate_structured_review_ledger",
]
