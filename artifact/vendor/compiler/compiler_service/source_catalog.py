"""Deterministic, exact-source catalog and section-aware model shards.

This module deliberately performs no semantic extraction.  It turns trusted
``DocumentIR`` block text into small exact substrings, records the document's
heading sequence, and assigns every span to exactly one shard.  Model-facing
stages may reuse a few neighbouring spans as read-only context, but ownership
never overlaps.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Literal, TypeAlias

from .graph_contract import canonical_json
from .models import BlockKind, DocumentBlock, DocumentIR
from .structured_source import (
    JsonScalar,
    StructuredSourceCatalog,
    StructuredSourceRecord,
    build_structured_source_catalog,
)

MAX_SOURCE_SPAN_CHARS = 2_000
DEFAULT_TARGET_SHARD_CHARS = 12_000
DEFAULT_MAX_OWNED_SPANS_PER_SHARD = 32
MAX_OWNED_SPANS_PER_SHARD = 64
DEFAULT_CONTEXT_SPAN_COUNT = 1
MAX_CONTEXT_SPAN_COUNT = 4
MAX_STRUCTURED_WARNINGS = 32
MAX_STRUCTURED_WARNING_CHARS = 1_000

_SENTENCE_BOUNDARIES = frozenset("。！？；!?;")
_TRAILING_CLOSERS = frozenset("”’\"'）)]】》」』")
_JOINER = "\u200d"
_VARIATION_SELECTORS = frozenset(("\ufe0e", "\ufe0f"))
_CONTINUES_FROM_PATTERN = re.compile(r"(?:上\s*接|continued\s+from)", re.IGNORECASE)
_CONTINUES_ON_PATTERN = re.compile(r"(?:下\s*转|continued\s+(?:on|at))", re.IGNORECASE)

SourcePathItem: TypeAlias = str | int
SourcePath: TypeAlias = tuple[SourcePathItem, ...]
SourceKind: TypeAlias = Literal[
    "block_text", "code_body", "code_caption", "structured_visual"
]
ContinuationHint: TypeAlias = Literal[
    "continues_from_previous_page",
    "continues_on_later_page",
    "bidirectional_continuation",
]
SegmentBoundaryHint: TypeAlias = Literal[
    "document_title",
    "continues_from_previous_page",
    "after_continues_on_later_page",
]


@dataclass(frozen=True, slots=True)
class SourceBoundingBox:
    """Frozen normalized geometry copied from one trusted DocumentIR block."""

    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """One exact, bounded substring of a trusted DocumentIR source value.

    ``source_path`` points to either ``DocumentBlock.text`` or one retained
    structured ``code_body``/``code_caption`` string leaf. Offsets are Python
    Unicode-code-point offsets into that exact string. ``exact_text`` is never
    normalized, reconstructed, or concatenated.
    """

    id: str
    artifact_id: str
    version_id: str
    block_id: str
    page: int
    kind: BlockKind
    level: int | None
    bbox: SourceBoundingBox | None
    exact_text: str
    start_offset: int
    end_offset: int
    section_title: str | None
    section_heading_id: str | None
    source_path: SourcePath = ("text",)
    source_kind: SourceKind = "block_text"
    document_order: int = 0
    document_segment_id: str = ""
    continuation_hint: ContinuationHint | None = None
    direct_evidence_eligible: bool = True
    structured_role: str | None = None
    structured_json_pointer: str | None = None
    structured_exact_scalar: JsonScalar | None = None

    @property
    def nfc_text(self) -> str:
        """Return the validator-facing NFC view without changing the source."""

        return unicodedata.normalize("NFC", self.exact_text)


@dataclass(frozen=True, slots=True)
class SourceHeading:
    """A global heading record, including possible adjacent-paper titles."""

    id: str
    block_id: str
    page: int
    level: int | None
    exact_text: str
    start_offset: int
    end_offset: int
    span_ids: tuple[str, ...]
    is_document_title: bool
    document_order: int = 0


@dataclass(frozen=True, slots=True)
class SourceSegment:
    """A mechanical document segment whose paper membership is model-reviewed.

    Titles and continuation markers create boundaries or hints only. They never
    decide whether a segment belongs to the target or an adjacent paper.
    """

    id: str
    order: int
    span_ids: tuple[str, ...]
    page_start: int
    page_end: int
    opening_heading_id: str | None
    opening_title: str | None
    boundary_hints: tuple[SegmentBoundaryHint, ...]


@dataclass(frozen=True, slots=True)
class SourceShard:
    """A bounded set of owned spans plus non-owning adjacent context."""

    id: str
    owned_span_ids: tuple[str, ...]
    context_span_ids: tuple[str, ...]
    section_titles: tuple[str, ...]
    character_count: int
    page_start: int
    page_end: int


@dataclass(frozen=True, slots=True)
class SourceCatalog:
    """Complete deterministic source catalog for one immutable artifact."""

    artifact_id: str
    version_id: str
    spans: tuple[SourceSpan, ...]
    headings: tuple[SourceHeading, ...]
    segments: tuple[SourceSegment, ...]
    shards: tuple[SourceShard, ...]
    structured_sources: StructuredSourceCatalog | None = None
    warnings: tuple[str, ...] = ()

    @property
    def by_id(self) -> dict[str, SourceSpan]:
        """Index spans by ID; callers receive a disposable mapping."""

        return {span.id: span for span in self.spans}

    @property
    def heading_by_id(self) -> dict[str, SourceHeading]:
        return {heading.id: heading for heading in self.headings}

    @property
    def segment_by_id(self) -> dict[str, SourceSegment]:
        return {segment.id: segment for segment in self.segments}

    @property
    def owner_by_span_id(self) -> dict[str, str]:
        return {
            span_id: shard.id
            for shard in self.shards
            for span_id in shard.owned_span_ids
        }


_Slice: TypeAlias = tuple[int, int]


@dataclass(frozen=True, slots=True)
class _ExactTextSource:
    value: str
    path: SourcePath
    kind: SourceKind


def build_source_catalog(
    document: DocumentIR,
    *,
    max_span_chars: int = MAX_SOURCE_SPAN_CHARS,
    target_shard_chars: int = DEFAULT_TARGET_SHARD_CHARS,
    max_owned_spans: int = DEFAULT_MAX_OWNED_SPANS_PER_SHARD,
    context_span_count: int = DEFAULT_CONTEXT_SPAN_COUNT,
) -> SourceCatalog:
    """Build exact spans, a heading catalog, and uniquely owned shards."""

    _validate_limits(
        max_span_chars=max_span_chars,
        target_shard_chars=target_shard_chars,
        max_owned_spans=max_owned_spans,
        context_span_count=context_span_count,
    )
    artifact = document.artifact
    spans: list[SourceSpan] = []
    headings: list[SourceHeading] = []
    current_heading_id: str | None = None
    current_section_title: str | None = None
    structured_catalog = build_structured_source_catalog(document, fail_on_unsafe=False)
    unsafe_blocks = set(structured_catalog.unsafe_block_ids)
    safe_template_record_ids = {
        record_id
        for template in structured_catalog.visual_templates
        if template.block_id not in unsafe_blocks
        for record_id in template.source_record_ids
    }
    structured_by_block: dict[str, list[StructuredSourceRecord]] = {}
    for record in structured_catalog.records:
        if (
            record.id in safe_template_record_ids
            and record.safe_for_visual_hydration
            and record.block_id not in unsafe_blocks
        ):
            structured_by_block.setdefault(record.block_id, []).append(record)

    for page in document.pages:
        for block in page.blocks:
            block_start_order = len(spans)
            block_bbox = _source_bbox(block)
            heading_bounds = _heading_bounds(block)
            heading_id: str | None = None
            if heading_bounds is not None:
                heading_start, heading_end = heading_bounds
                heading_id = _source_id(
                    "source-heading",
                    artifact.artifact_id,
                    artifact.version_id,
                    block.id,
                    heading_start,
                    heading_end,
                )
                current_heading_id = heading_id
                assert block.text is not None
                current_section_title = block.text[heading_start:heading_end]

            block_span_ids: list[str] = []
            for source in _exact_text_sources(block):
                slices = (
                    _equation_text_slices(source.value, max_span_chars)
                    if block.kind == "equation" and source.kind == "block_text"
                    else _exact_text_slices(source.value, max_span_chars)
                )
                for start, end in slices:
                    exact_text = source.value[start:end]
                    span_id = _source_id(
                        "source-span",
                        artifact.artifact_id,
                        artifact.version_id,
                        block.id,
                        start,
                        end,
                        source_path=source.path,
                    )
                    spans.append(
                        SourceSpan(
                            id=span_id,
                            artifact_id=artifact.artifact_id,
                            version_id=artifact.version_id,
                            block_id=block.id,
                            page=block.page,
                            kind=block.kind,
                            level=block.level,
                            bbox=block_bbox,
                            exact_text=exact_text,
                            start_offset=start,
                            end_offset=end,
                            section_title=current_section_title,
                            section_heading_id=current_heading_id,
                            source_path=source.path,
                            source_kind=source.kind,
                            continuation_hint=_continuation_hint(exact_text),
                        )
                    )
                    if source.path == ("text",):
                        block_span_ids.append(span_id)

            for record in structured_by_block.get(block.id, ()):
                spans.append(
                    _structured_source_span(
                        document,
                        block,
                        record,
                        section_title=current_section_title,
                        section_heading_id=current_heading_id,
                    )
                )

            if heading_bounds is not None and heading_id is not None:
                heading_start, heading_end = heading_bounds
                assert block.text is not None
                headings.append(
                    SourceHeading(
                        id=heading_id,
                        block_id=block.id,
                        page=block.page,
                        level=block.level,
                        exact_text=block.text[heading_start:heading_end],
                        start_offset=heading_start,
                        end_offset=heading_end,
                        span_ids=tuple(block_span_ids),
                        is_document_title=(block.kind == "title" or block.level == 1),
                        document_order=block_start_order,
                    )
                )

    assigned_spans, segments = _assign_document_segments(
        artifact.artifact_id,
        artifact.version_id,
        tuple(spans),
        tuple(headings),
    )
    shards = _build_shards(
        artifact.artifact_id,
        artifact.version_id,
        assigned_spans,
        target_shard_chars=target_shard_chars,
        max_owned_spans=max_owned_spans,
        context_span_count=context_span_count,
    )
    catalog = SourceCatalog(
        artifact_id=artifact.artifact_id,
        version_id=artifact.version_id,
        spans=assigned_spans,
        headings=tuple(headings),
        segments=segments,
        shards=shards,
        structured_sources=structured_catalog,
        warnings=_bounded_structured_warnings(structured_catalog.warnings),
    )
    _validate_catalog(catalog, max_owned_spans=max_owned_spans)
    return catalog


def _validate_limits(
    *,
    max_span_chars: int,
    target_shard_chars: int,
    max_owned_spans: int,
    context_span_count: int,
) -> None:
    values = (
        max_span_chars,
        target_shard_chars,
        max_owned_spans,
        context_span_count,
    )
    if any(type(value) is not int for value in values):
        raise TypeError("source catalog limits must be integers")
    if not 1 <= max_span_chars <= MAX_SOURCE_SPAN_CHARS:
        raise ValueError(f"max_span_chars must be from 1 to {MAX_SOURCE_SPAN_CHARS}")
    if target_shard_chars < 1:
        raise ValueError("target_shard_chars must be positive")
    if not 1 <= max_owned_spans <= MAX_OWNED_SPANS_PER_SHARD:
        raise ValueError(
            f"max_owned_spans must be from 1 to {MAX_OWNED_SPANS_PER_SHARD}"
        )
    if not 0 <= context_span_count <= MAX_CONTEXT_SPAN_COUNT:
        raise ValueError(
            f"context_span_count must be from 0 to {MAX_CONTEXT_SPAN_COUNT}"
        )


def _heading_bounds(block: DocumentBlock) -> _Slice | None:
    text = block.text
    if text is None or (block.kind != "title" and block.level is None):
        return None
    start, end = _trim_whitespace(text, 0, len(text))
    return None if start == end else (start, end)


def _exact_text_sources(block: DocumentBlock) -> tuple[_ExactTextSource, ...]:
    """Return trusted text leaves without reconstructing structured values."""

    output: list[_ExactTextSource] = []
    if block.text is not None:
        output.append(_ExactTextSource(block.text, ("text",), "block_text"))

    for key, kind in (
        ("code_body", "code_body"),
        ("code_caption", "code_caption"),
    ):
        value = block.structured.get(key)
        for path, exact in _structured_string_leaves(value, ("structured", key)):
            # Newer normalizers may also retain a code field in block.text.
            # Prefer the canonical text source when the same exact leaf is
            # already represented, avoiding duplicate evidence spans.
            if block.text is not None and exact and exact in block.text:
                continue
            output.append(_ExactTextSource(exact, path, kind))
    return tuple(output)


def _structured_string_leaves(
    value: object, path: SourcePath
) -> tuple[tuple[SourcePath, str], ...]:
    if isinstance(value, str):
        return ((path, value),)
    if isinstance(value, list):
        return tuple(
            item
            for index, child in enumerate(value)
            for item in _structured_string_leaves(child, (*path, index))
        )
    if isinstance(value, dict):
        return tuple(
            item
            for key in sorted(value)
            for item in _structured_string_leaves(value[key], (*path, key))
        )
    return ()


def resolve_source_span_text(block: DocumentBlock, span: SourceSpan) -> str:
    """Resolve and verify one span against its exact DocumentIR source leaf."""

    if block.id != span.block_id:
        raise ValueError("source span references a different block")
    value: object
    if span.source_path == ("text",):
        if span.source_kind != "block_text":
            raise ValueError("source span kind does not match block text")
        value = block.text
    elif span.source_path[:2] in {
        ("structured", "code_body"),
        ("structured", "code_caption"),
    }:
        if span.source_kind != span.source_path[1]:
            raise ValueError("source span kind does not match structured code")
        value = block.structured
        for component in span.source_path[1:]:
            if isinstance(component, str) and isinstance(value, dict):
                if component not in value:
                    raise ValueError("source span structured path is missing")
                value = value[component]
            elif isinstance(component, int) and isinstance(value, list):
                if component < 0 or component >= len(value):
                    raise ValueError("source span structured index is invalid")
                value = value[component]
            else:
                raise ValueError("source span structured path is invalid")
    elif span.source_kind == "structured_visual" and span.source_path[:1] == (
        "structured",
    ):
        value = _resolve_structured_path(block.structured, span.source_path[1:])
        if span.structured_json_pointer != _json_pointer(span.source_path[1:]):
            raise ValueError("structured visual source pointer is inconsistent")
        source_scalar_text = _source_scalar_text(value)
        if (
            span.start_offset == 0
            and span.end_offset == len(source_scalar_text)
            and canonical_json(span.structured_exact_scalar) != canonical_json(value)
        ):
            raise ValueError("structured visual scalar is inconsistent")
    else:
        raise ValueError("source span path is invalid")

    if span.source_kind == "structured_visual":
        value = source_scalar_text
    elif not isinstance(value, str):
        raise ValueError("source span path does not resolve to text")
    if (
        span.start_offset < 0
        or span.end_offset <= span.start_offset
        or span.end_offset > len(value)
    ):
        raise ValueError("source span offsets are invalid")
    exact = value[span.start_offset : span.end_offset]
    if exact != span.exact_text:
        raise ValueError("source span is not an exact source slice")
    return exact


def _structured_source_span(
    document: DocumentIR,
    block: DocumentBlock,
    record: StructuredSourceRecord,
    *,
    section_title: str | None,
    section_heading_id: str | None,
) -> SourceSpan:
    exact_text = (
        record.exact_scalar
        if isinstance(record.exact_scalar, str)
        else canonical_json(record.exact_scalar)
    )
    if not isinstance(exact_text, str) or not exact_text:
        raise RuntimeError("safe structured source scalar must be non-empty")
    start = record.scalar_start_offset or 0
    end = record.scalar_end_offset
    if end is None:
        end = start + len(exact_text)
    return SourceSpan(
        id=record.id,
        artifact_id=document.artifact.artifact_id,
        version_id=document.artifact.version_id,
        block_id=record.block_id,
        page=record.page,
        kind=block.kind,
        level=block.level,
        bbox=_source_bbox(block),
        exact_text=exact_text,
        start_offset=start,
        end_offset=end,
        section_title=section_title,
        section_heading_id=section_heading_id,
        source_path=("structured", *_pointer_components(record.json_pointer)),
        source_kind="structured_visual",
        direct_evidence_eligible=record.direct_evidence,
        structured_role=record.role,
        structured_json_pointer=record.json_pointer,
        structured_exact_scalar=record.exact_scalar,
    )


def _pointer_components(pointer: str) -> SourcePath:
    if not pointer.startswith("/"):
        raise RuntimeError("structured source JSON Pointer is invalid")
    output: list[SourcePathItem] = []
    for encoded in pointer[1:].split("/"):
        token = encoded.replace("~1", "/").replace("~0", "~")
        output.append(int(token) if token.isdigit() else token)
    return tuple(output)


def _json_pointer(path: SourcePath) -> str:
    return "".join(
        "/" + str(component).replace("~", "~0").replace("/", "~1") for component in path
    )


def _resolve_structured_path(value: object, path: SourcePath) -> object:
    for component in path:
        if isinstance(component, str) and isinstance(value, dict):
            if component not in value:
                raise ValueError("source span structured path is missing")
            value = value[component]
        elif isinstance(component, int) and isinstance(value, list):
            if component < 0 or component >= len(value):
                raise ValueError("source span structured index is invalid")
            value = value[component]
        else:
            raise ValueError("source span structured path is invalid")
    return value


def _source_scalar_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if type(value) in {int, float, bool}:
        return canonical_json(value)
    raise ValueError("structured visual source path does not resolve to a scalar")


def _bounded_structured_warnings(warnings: tuple[str, ...]) -> tuple[str, ...]:
    normalized = sorted(
        {
            unicodedata.normalize("NFC", warning)[:MAX_STRUCTURED_WARNING_CHARS]
            for warning in warnings
        },
        key=lambda value: value.encode("utf-8"),
    )
    selected = normalized[:MAX_STRUCTURED_WARNINGS]
    remaining = len(normalized) - len(selected)
    if remaining:
        selected.append(f"structured-source: {remaining} additional warning(s) omitted")
    return tuple(selected)


def _continuation_hint(value: str) -> ContinuationHint | None:
    from_previous = _CONTINUES_FROM_PATTERN.search(value) is not None
    on_later = _CONTINUES_ON_PATTERN.search(value) is not None
    if from_previous and on_later:
        return "bidirectional_continuation"
    if from_previous:
        return "continues_from_previous_page"
    if on_later:
        return "continues_on_later_page"
    return None


def _assign_document_segments(
    artifact_id: str,
    version_id: str,
    spans: tuple[SourceSpan, ...],
    headings: tuple[SourceHeading, ...],
) -> tuple[tuple[SourceSpan, ...], tuple[SourceSegment, ...]]:
    if not spans:
        return (), ()

    title_by_first_span = {
        heading.span_ids[0]: heading
        for heading in headings
        if heading.is_document_title and heading.span_ids
    }
    groups: list[tuple[tuple[SourceSpan, ...], tuple[SegmentBoundaryHint, ...]]] = []
    current: list[SourceSpan] = []
    current_hints: list[SegmentBoundaryHint] = []
    previous_continues_on = False

    def flush() -> None:
        nonlocal current, current_hints
        if current:
            groups.append((tuple(current), tuple(dict.fromkeys(current_hints))))
            current = []
            current_hints = []

    for span in spans:
        title_start = span.id in title_by_first_span
        continues_from = span.continuation_hint in {
            "continues_from_previous_page",
            "bidirectional_continuation",
        }
        if current and (title_start or continues_from or previous_continues_on):
            flush()
        if not current:
            if title_start:
                current_hints.append("document_title")
            if continues_from:
                current_hints.append("continues_from_previous_page")
            if previous_continues_on:
                current_hints.append("after_continues_on_later_page")
        current.append(span)
        previous_continues_on = span.continuation_hint in {
            "continues_on_later_page",
            "bidirectional_continuation",
        }
    flush()

    assigned: list[SourceSpan] = []
    segments: list[SourceSegment] = []
    for segment_order, (group, boundary_hints) in enumerate(groups):
        first = group[0]
        segment_id = _document_segment_id(artifact_id, version_id, first.id)
        title = title_by_first_span.get(first.id)
        span_ids: list[str] = []
        for span in group:
            assigned_span = replace(
                span,
                document_order=len(assigned),
                document_segment_id=segment_id,
            )
            assigned.append(assigned_span)
            span_ids.append(assigned_span.id)
        segments.append(
            SourceSegment(
                id=segment_id,
                order=segment_order,
                span_ids=tuple(span_ids),
                page_start=min(span.page for span in group),
                page_end=max(span.page for span in group),
                opening_heading_id=None if title is None else title.id,
                opening_title=None if title is None else title.exact_text,
                boundary_hints=boundary_hints,
            )
        )
    return tuple(assigned), tuple(segments)


def _document_segment_id(artifact_id: str, version_id: str, first_span_id: str) -> str:
    payload = "\0".join(("document-segment", artifact_id, version_id, first_span_id))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"document-segment:sha256:{digest}"


def _exact_text_slices(text: str, maximum: int) -> tuple[_Slice, ...]:
    if not text or not text.strip():
        return ()
    boundaries = _safe_boundary_offsets(text)
    output: list[_Slice] = []
    start = 0
    for end in (*boundaries, len(text)):
        if end <= start:
            continue
        output.extend(_bounded_slices(text, start, end, maximum))
        start = end
    return tuple(output)


def _equation_text_slices(text: str, maximum: int) -> tuple[_Slice, ...]:
    """Keep each equation line intact unless the hard span limit requires a split.

    MinerU serializes decimal glyphs with spaces (for example ``1 . 3 3``).
    A prose period boundary would therefore sever one scientific value across
    independently reviewed spans.  Newlines remain trusted layout boundaries;
    an overlong formula line uses the same exact bounded fallback while also
    preferring not to cut through a TeX control sequence.
    """

    if not text or not text.strip():
        return ()
    output: list[_Slice] = []
    start = 0
    for end in (*_newline_boundary_offsets(text), len(text)):
        if end <= start:
            continue
        output.extend(
            _bounded_slices(
                text,
                start,
                end,
                maximum,
                avoid_tex_tokens=True,
            )
        )
        start = end
    return tuple(output)


def _newline_boundary_offsets(text: str) -> tuple[int, ...]:
    output: list[int] = []
    index = 0
    while index < len(text):
        if text[index] == "\r":
            index += 2 if index + 1 < len(text) and text[index + 1] == "\n" else 1
            output.append(index)
            continue
        if text[index] == "\n":
            index += 1
            output.append(index)
            continue
        index += 1
    if output and output[-1] == len(text):
        output.pop()
    return tuple(output)


def _safe_boundary_offsets(text: str) -> tuple[int, ...]:
    output: list[int] = []
    index = 0
    length = len(text)
    math_ends = _complete_math_ends(text)
    while index < length:
        if index in math_ends:
            index = math_ends[index]
            continue
        character = text[index]
        end: int | None = None
        if character == "\r":
            end = index + (2 if index + 1 < length and text[index + 1] == "\n" else 1)
        elif character == "\n":
            end = index + 1
        elif character in _SENTENCE_BOUNDARIES:
            end = _include_trailing_closers(text, index + 1)
        elif character == ".":
            candidate = _include_trailing_closers(text, index + 1)
            if candidate == length or text[candidate].isspace():
                end = candidate
        if end is None:
            index += 1
            continue
        output.append(end)
        index = end
    if output and output[-1] == length:
        output.pop()
    return tuple(output)


def _complete_math_ends(text: str) -> dict[int, int]:
    """Protect paired TeX delimiters without changing source text or offsets.

    Consume escaped characters first so literal dollars cannot open math.
    Unpaired delimiters do not suppress ordinary sentence boundaries. The
    existing hard span limit still applies after sentence segmentation.
    """

    tokens = list(re.finditer(r"\\.|\${1,2}", text))
    closers = {"$": "$", "$$": "$$", r"\(": r"\)", r"\[": r"\]"}
    output: dict[int, int] = {}
    index = 0
    while index < len(tokens):
        opening = tokens[index]
        closing = closers.get(opening.group())
        if closing is not None:
            for next_index in range(index + 1, len(tokens)):
                candidate = tokens[next_index]
                if candidate.group() == closing:
                    output[opening.start()] = candidate.end()
                    index = next_index
                    break
        index += 1
    return output


def _include_trailing_closers(text: str, start: int) -> int:
    end = start
    while end < len(text) and text[end] in _TRAILING_CLOSERS:
        end += 1
    return end


def _bounded_slices(
    text: str,
    start: int,
    end: int,
    maximum: int,
    *,
    avoid_tex_tokens: bool = False,
) -> tuple[_Slice, ...]:
    output: list[_Slice] = []
    cursor = start
    while cursor < end:
        limit = min(cursor + maximum, end)
        split = limit
        if limit < end:
            whitespace = _last_whitespace_boundary(text, cursor, limit)
            if whitespace is not None:
                split = whitespace
            if avoid_tex_tokens:
                split = _avoid_tex_token_split(text, cursor, split)
            split = _avoid_unicode_split(text, cursor, split, limit)
        trimmed_start, trimmed_end = _trim_whitespace(text, cursor, split)
        if trimmed_start < trimmed_end:
            output.append((trimmed_start, trimmed_end))
        cursor = split
    return tuple(output)


def _avoid_tex_token_split(text: str, start: int, candidate: int) -> int:
    """Move a hard split before a TeX control token when that still progresses."""

    if candidate <= start or candidate >= len(text):
        return candidate
    slash = text.rfind("\\", start, candidate)
    if slash <= start:
        return candidate
    tail = text[slash + 1 : candidate]
    next_character = text[candidate]
    cuts_control_word = all(
        character.isascii() and character.isalpha() for character in tail
    ) and (next_character.isascii() and next_character.isalpha())
    cuts_control_symbol = not tail
    if cuts_control_word or cuts_control_symbol:
        return slash
    return candidate


def _last_whitespace_boundary(text: str, start: int, limit: int) -> int | None:
    # Do not create a tiny prefix merely because it contains early whitespace.
    minimum = start + max(1, (limit - start) // 2)
    for index in range(limit, minimum, -1):
        if text[index - 1].isspace():
            return index
    return None


def _avoid_unicode_split(text: str, start: int, candidate: int, hard_limit: int) -> int:
    end = candidate
    while end > start and end < len(text):
        next_character = text[end]
        previous_character = text[end - 1]
        if not (
            _unicode_extension(next_character)
            or next_character == _JOINER
            or previous_character == _JOINER
        ):
            break
        end -= 1
    return hard_limit if end == start else end


def _unicode_extension(value: str) -> bool:
    return (
        unicodedata.combining(value) != 0
        or unicodedata.category(value) in {"Mn", "Mc", "Me"}
        or value in _VARIATION_SELECTORS
    )


def _trim_whitespace(text: str, start: int, end: int) -> _Slice:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _source_bbox(block: DocumentBlock) -> SourceBoundingBox | None:
    if block.bbox is None:
        return None
    return SourceBoundingBox(
        x=block.bbox.x,
        y=block.bbox.y,
        width=block.bbox.width,
        height=block.bbox.height,
    )


def _source_id(
    kind: str,
    artifact_id: str,
    version_id: str,
    block_id: str,
    start: int,
    end: int,
    *,
    source_path: SourcePath = ("text",),
) -> str:
    parts = (kind, artifact_id, version_id, block_id, str(start), str(end))
    # Keep the established IDs for ordinary block.text spans. Structured
    # leaves add their exact typed path so equal offsets cannot collide.
    if source_path != ("text",):
        rendered_path = "/".join(
            f"i:{item}" if isinstance(item, int) else f"s:{item}"
            for item in source_path
        )
        parts = (*parts, rendered_path)
    payload = "\0".join(parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{kind}:sha256:{digest}"


def _build_shards(
    artifact_id: str,
    version_id: str,
    spans: tuple[SourceSpan, ...],
    *,
    target_shard_chars: int,
    max_owned_spans: int,
    context_span_count: int,
) -> tuple[SourceShard, ...]:
    if not spans:
        return ()
    sections = _contiguous_sections(spans)
    owned_batches: list[tuple[SourceSpan, ...]] = []
    current: list[SourceSpan] = []
    current_chars = 0

    def flush() -> None:
        nonlocal current, current_chars
        if current:
            owned_batches.append(tuple(current))
            current = []
            current_chars = 0

    for section in sections:
        section_chars = sum(len(span.exact_text) for span in section)
        section_fits = (
            len(section) <= max_owned_spans and section_chars <= target_shard_chars
        )
        if section_fits:
            would_overflow = (
                len(current) + len(section) > max_owned_spans
                or current_chars + section_chars > target_shard_chars
            )
            if current and would_overflow:
                flush()
            current.extend(section)
            current_chars += section_chars
            continue

        # An oversized section is split only after preserving the preceding
        # heading boundary. Within that section, source order remains exact.
        flush()
        for span in section:
            span_chars = len(span.exact_text)
            if current and (
                len(current) == max_owned_spans
                or current_chars + span_chars > target_shard_chars
            ):
                flush()
            current.append(span)
            current_chars += span_chars
        flush()
    flush()

    positions = {span.id: index for index, span in enumerate(spans)}
    output: list[SourceShard] = []
    for owned in owned_batches:
        first = positions[owned[0].id]
        last = positions[owned[-1].id]
        context_start = max(0, first - context_span_count)
        context_end = min(len(spans), last + 1 + context_span_count)
        owned_id_set = {item.id for item in owned}
        context = tuple(
            span.id
            for span in spans[context_start:context_end]
            if span.id not in owned_id_set
        )
        owned_ids = tuple(span.id for span in owned)
        section_titles = tuple(
            dict.fromkeys(
                span.section_title for span in owned if span.section_title is not None
            )
        )
        digest_payload = "\0".join(
            ("source-shard", artifact_id, version_id, *owned_ids)
        )
        digest = hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()
        output.append(
            SourceShard(
                id=f"source-shard:sha256:{digest}",
                owned_span_ids=owned_ids,
                context_span_ids=context,
                section_titles=section_titles,
                character_count=sum(len(span.exact_text) for span in owned),
                page_start=min(span.page for span in owned),
                page_end=max(span.page for span in owned),
            )
        )
    return tuple(output)


def _contiguous_sections(
    spans: tuple[SourceSpan, ...],
) -> tuple[tuple[SourceSpan, ...], ...]:
    output: list[tuple[SourceSpan, ...]] = []
    current: list[SourceSpan] = []
    current_key: tuple[str, str | None] | None = None
    initialized = False
    for span in spans:
        key = (span.document_segment_id, span.section_heading_id)
        if initialized and key != current_key:
            output.append(tuple(current))
            current = []
        current.append(span)
        current_key = key
        initialized = True
    if current:
        output.append(tuple(current))
    return tuple(output)


def _validate_catalog(catalog: SourceCatalog, *, max_owned_spans: int) -> None:
    span_ids = tuple(span.id for span in catalog.spans)
    if len(span_ids) != len(set(span_ids)):
        raise RuntimeError("source span IDs must be unique")
    heading_ids = tuple(heading.id for heading in catalog.headings)
    if len(heading_ids) != len(set(heading_ids)):
        raise RuntimeError("source heading IDs must be unique")
    segment_ids = tuple(segment.id for segment in catalog.segments)
    if len(segment_ids) != len(set(segment_ids)):
        raise RuntimeError("document segment IDs must be unique")
    if [segment.order for segment in catalog.segments] != list(
        range(len(catalog.segments))
    ):
        raise RuntimeError("document segment order must be complete")
    segmented_ids = [
        span_id for segment in catalog.segments for span_id in segment.span_ids
    ]
    if len(segmented_ids) != len(set(segmented_ids)) or segmented_ids != list(span_ids):
        raise RuntimeError("document segments must partition source spans in order")
    segment_by_span = {
        span_id: segment.id
        for segment in catalog.segments
        for span_id in segment.span_ids
    }
    for order, span in enumerate(catalog.spans):
        if span.document_order != order:
            raise RuntimeError("source span document order must be complete")
        if span.document_segment_id != segment_by_span.get(span.id):
            raise RuntimeError("source span segment identity is inconsistent")
        if span.source_kind == "block_text" and span.source_path != ("text",):
            raise RuntimeError("block text span path is inconsistent")
        if span.source_kind in {"code_body", "code_caption"} and (
            span.source_path[:2] != ("structured", span.source_kind)
        ):
            raise RuntimeError("structured code span path is inconsistent")
        if span.source_kind == "structured_visual" and (
            span.source_path[:1] != ("structured",)
            or span.structured_json_pointer is None
            or span.structured_role is None
        ):
            raise RuntimeError("structured visual span provenance is inconsistent")
    owned_ids = [
        span_id for shard in catalog.shards for span_id in shard.owned_span_ids
    ]
    if len(owned_ids) != len(set(owned_ids)) or set(owned_ids) != set(span_ids):
        raise RuntimeError("every source span must have exactly one shard owner")
    known = set(span_ids)
    for shard in catalog.shards:
        if not shard.owned_span_ids or len(shard.owned_span_ids) > max_owned_spans:
            raise RuntimeError("source shard ownership bound was violated")
        if set(shard.owned_span_ids) & set(shard.context_span_ids):
            raise RuntimeError("source shard context must be read-only")
        if not set(shard.context_span_ids) <= known:
            raise RuntimeError("source shard context references an unknown span")


__all__ = [
    "DEFAULT_CONTEXT_SPAN_COUNT",
    "DEFAULT_MAX_OWNED_SPANS_PER_SHARD",
    "DEFAULT_TARGET_SHARD_CHARS",
    "MAX_CONTEXT_SPAN_COUNT",
    "MAX_OWNED_SPANS_PER_SHARD",
    "MAX_SOURCE_SPAN_CHARS",
    "MAX_STRUCTURED_WARNINGS",
    "ContinuationHint",
    "SegmentBoundaryHint",
    "SourceBoundingBox",
    "SourceCatalog",
    "SourceHeading",
    "SourceKind",
    "SourcePath",
    "SourcePathItem",
    "SourceSegment",
    "SourceShard",
    "SourceSpan",
    "build_source_catalog",
    "resolve_source_span_text",
]
