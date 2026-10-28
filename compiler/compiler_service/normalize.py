"""Normalize versioned MinerU output into a small, stable Document IR."""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections import defaultdict
from typing import Any

from .errors import ContractError
from .mineru import MineruOutput
from .models import (
    ArtifactContext,
    BoundingBox,
    DocumentBlock,
    DocumentIR,
    DocumentPage,
    ParseResponse,
    ParserMetadata,
    ParseRun,
)

_KIND_MAP = {
    "text": "paragraph",
    "paragraph": "paragraph",
    "title": "title",
    "list": "list",
    "equation": "equation",
    "interline_equation": "equation",
    "image": "image",
    "figure": "figure",
    "chart": "chart",
    "table": "table",
    "citation": "citation",
    # MinerU's bibliography entries; without this they become "other" and the
    # citation-source projection never sees an explicit reference block.
    "ref_text": "citation",
}

_MIN_PAGE_TEXT_ANCHOR = 32


def normalize_mineru(output: MineruOutput, artifact: ArtifactContext) -> ParseResponse:
    pages_raw = output.middle_json.get("pdf_info")
    if not isinstance(pages_raw, list) or not pages_raw:
        raise ContractError("MinerU output contains no pages")
    by_page: dict[int, list[DocumentBlock]] = defaultdict(list)
    page_geometry = [_page_geometry(page) for page in pages_raw]
    page_text_blocks = [_middle_page_text_blocks(page) for page in pages_raw]
    for page_index, raw_page in enumerate(pages_raw):
        middle_index = raw_page.get("page_idx")
        if type(middle_index) is not int or middle_index != page_index:
            raise ContractError(
                f"middle_json.pdf_info[{page_index}].page_idx is invalid"
            )
    for index, raw in enumerate(output.content_list):
        if not isinstance(raw, dict):
            raise ContractError(f"content_list[{index}] must be an object")
        page_idx = raw.get("page_idx")
        if type(page_idx) is not int or page_idx < 0 or page_idx >= len(pages_raw):
            raise ContractError(f"content_list[{index}].page_idx is invalid")
        segments = _page_segments(raw, page_idx, page_geometry, page_text_blocks)
        for segment_page_idx, segment in segments:
            page_number = segment_page_idx + 1
            raw_type = str(segment.get("type", "other"))
            kind = _KIND_MAP.get(raw_type, "other")
            text = _block_text(segment)
            bbox = _bbox(segment.get("bbox"), *page_geometry[segment_page_idx])
            level = _level(segment)
            structured = _json_object(segment)
            block = DocumentBlock(
                id=f"block:p{page_number}:{index + 1:06d}",
                kind=kind,
                page=page_number,
                source_index=index,
                bbox=bbox,
                text=text,
                level=level,
                structured=structured,
            )
            by_page[page_number].append(block)

    pages: list[DocumentPage] = []
    for index in range(len(pages_raw)):
        width, height, _, _ = page_geometry[index]
        pages.append(
            DocumentPage(
                number=index + 1,
                width=width,
                height=height,
                blocks=by_page[index + 1],
            )
        )
    document = DocumentIR(
        artifact=artifact,
        parser=ParserMetadata(
            name="mineru",
            version=output.version,
            backend="pipeline",
            method=output.method,
            transport="remote",
        ),
        page_count=len(pages),
        pages=pages,
        markdown=unicodedata.normalize("NFC", output.markdown),
        warnings=[],
    )
    run_payload = {
        "artifact_sha256": artifact.artifact_sha256,
        "backend": "mineru-pipeline",
        "version": output.version,
        "method": output.method,
    }
    digest = hashlib.sha256(
        json.dumps(run_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return ParseResponse(
        run=ParseRun(
            run_id=f"parse:sha256:{digest}",
            backend="mineru-pipeline",
            backend_version=output.version,
            duration_ms=output.duration_ms,
        ),
        document=document,
    )


def _middle_page_text_blocks(
    page: dict[str, Any],
) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Retain only physically page-owned, bounded paragraph/span evidence."""

    output: list[tuple[str, tuple[float, float, float, float]]] = []
    seen: set[tuple[str, tuple[float, float, float, float]]] = set()
    for key in ("para_blocks", "preproc_blocks"):
        roots = page.get(key)
        if not isinstance(roots, list):
            continue
        stack = list(reversed(roots))
        while stack:
            block = stack.pop()
            if not isinstance(block, dict):
                continue
            children = block.get("blocks")
            if isinstance(children, list):
                stack.extend(reversed(children))
            if block.get("type") not in {"text", "paragraph"}:
                continue
            bbox = block.get("bbox")
            if not _finite_number_list(bbox, 4):
                continue
            lines = block.get("lines")
            if not isinstance(lines, list):
                continue
            fragments: list[str] = []
            for line in lines:
                if not isinstance(line, dict):
                    continue
                spans = line.get("spans")
                if isinstance(spans, list):
                    for span in spans:
                        if isinstance(span, dict):
                            _collect_text(
                                span.get("content", span.get("text")), fragments
                            )
                else:
                    _collect_text(line.get("text"), fragments)
            text = " ".join(part for part in fragments if part.strip())
            if not text:
                continue
            item = (text, tuple(float(value) for value in bbox))
            if item not in seen:
                output.append(item)
                seen.add(item)
    return output


def _page_segments(
    raw: dict[str, Any],
    page_idx: int,
    page_geometry: list[
        tuple[
            float | None,
            float | None,
            list[tuple[float, float, float, float]],
            frozenset[tuple[float, float, float, float]],
        ]
    ],
    page_text_blocks: list[list[tuple[str, tuple[float, float, float, float]]]],
) -> list[tuple[int, dict[str, Any]]]:
    """Split a merged paragraph only where real next-page spans prove its cut."""

    original = [(page_idx, raw)]
    text = raw.get("text")
    if (
        raw.get("type") not in {"text", "paragraph"}
        or not isinstance(text, str)
        or page_idx + 1 >= len(page_geometry)
    ):
        return original
    first_bbox = _bbox(raw.get("bbox"), *page_geometry[page_idx])
    if first_bbox is None:
        return original

    normalized_text, original_offsets = _page_text_key(text)
    if len(normalized_text) < _MIN_PAGE_TEXT_ANCHOR * 2:
        return original

    confirmed_prefixes: set[int] = set()
    for page_text, middle_bbox in page_text_blocks[page_idx]:
        middle_region = _bbox(list(middle_bbox), *page_geometry[page_idx])
        if middle_region != first_bbox:
            continue
        normalized_page_text, _ = _page_text_key(page_text)
        anchor = normalized_page_text[:_MIN_PAGE_TEXT_ANCHOR]
        if len(anchor) >= _MIN_PAGE_TEXT_ANCHOR and normalized_text.startswith(anchor):
            confirmed_prefixes.add(
                _matching_prefix(normalized_text, normalized_page_text)
            )
    if not confirmed_prefixes:
        return original

    continuation_matches: list[tuple[int, tuple[float, float, float, float]]] = []
    for page_text, middle_bbox in page_text_blocks[page_idx + 1]:
        if _bbox(list(middle_bbox), *page_geometry[page_idx + 1]) is None:
            continue
        normalized_page_text, _ = _page_text_key(page_text)
        offset = _unique_continuation_offset(normalized_text, normalized_page_text)
        if offset is not None and offset in confirmed_prefixes:
            continuation_matches.append((offset, middle_bbox))
    if not continuation_matches:
        return original

    continuation_matches.sort(key=lambda item: item[0])
    offset, continuation_bbox = continuation_matches[0]
    if any(
        other_offset == offset and other_bbox != continuation_bbox
        for other_offset, other_bbox in continuation_matches[1:]
    ):
        return original
    split = original_offsets[offset]
    while split and text[split - 1] in "([{":
        split -= 1
    first_text = text[:split].rstrip()
    continued_text = text[split:].lstrip()
    if not first_text or not continued_text:
        return original

    first = {**raw, "text": first_text}
    continued = {
        **raw,
        "text": continued_text,
        "page_idx": page_idx + 1,
        "bbox": list(continuation_bbox),
    }
    return [(page_idx, first), (page_idx + 1, continued)]


def _page_text_key(value: str) -> tuple[str, list[int]]:
    characters: list[str] = []
    offsets: list[int] = []
    for index, character in enumerate(value):
        for normalized in unicodedata.normalize("NFKC", character).casefold():
            if normalized.isalnum():
                characters.append(normalized)
                offsets.append(index)
    return "".join(characters), offsets


def _matching_prefix(first: str, second: str) -> int:
    for index, (left, right) in enumerate(zip(first, second, strict=False)):
        if left != right:
            return index
    return min(len(first), len(second))


def _unique_continuation_offset(text: str, continuation: str) -> int | None:
    for length in (64, 48, 40, _MIN_PAGE_TEXT_ANCHOR):
        if len(continuation) < length:
            continue
        anchor = continuation[:length]
        first = text.find(anchor)
        if first >= _MIN_PAGE_TEXT_ANCHOR and first == text.rfind(anchor):
            return first
    return None


def _level(raw: dict[str, Any]) -> int | None:
    value = raw.get("text_level")
    if value is None:
        return None
    if type(value) is int and 1 <= value <= 12:
        return value
    raise ContractError("MinerU text_level must be an integer from 1 to 12")


def _bbox(
    value: Any,
    page_width: float | None,
    page_height: float | None,
    middle_bboxes: list[tuple[float, float, float, float]],
    middle_bbox_set: frozenset[tuple[float, float, float, float]] | None = None,
) -> BoundingBox | None:
    if value is None:
        return None
    if not _finite_number_list(value, 4):
        raise ContractError("MinerU bbox must contain four finite JSON numbers")
    if page_width is None or page_height is None:
        return None
    supplied = tuple(float(item) for item in value)
    mapped = (
        supplied[0] * page_width / 1_000,
        supplied[1] * page_height / 1_000,
        supplied[2] * page_width / 1_000,
        supplied[3] * page_height / 1_000,
    )
    # Pipeline middle.json is the coordinate authority.  Older/current
    # content_list producers have emitted both page-unit and 0..1000 mappings.
    # Select the hypothesis closest to an actual middle block before normalizing.
    candidates = (supplied, mapped)
    if middle_bboxes:
        exact_candidates = (
            frozenset(middle_bboxes) if middle_bbox_set is None else middle_bbox_set
        )
        exact = next(
            (candidate for candidate in candidates if candidate in exact_candidates),
            None,
        )
        if exact is not None:
            page_bbox = exact
            error = 0.0
        else:
            _, page_bbox, error = min(
                (
                    (
                        candidate,
                        middle,
                        _relative_edge_error(
                            candidate, middle, page_width, page_height
                        ),
                    )
                    for candidate in candidates
                    for middle in middle_bboxes
                ),
                key=lambda item: item[2],
            )
        if error > 0.05:
            return None
    elif all(0 <= item <= 1_000 for item in supplied):
        page_bbox = mapped
    else:
        return None
    x0, y0, x1, y1 = page_bbox
    if (
        x0 < 0
        or y0 < 0
        or x1 <= x0
        or y1 <= y0
        or x1 > page_width * 1.001
        or y1 > page_height * 1.001
    ):
        return None
    return BoundingBox(
        x=x0 / page_width,
        y=y0 / page_height,
        width=(x1 - x0) / page_width,
        height=(y1 - y0) / page_height,
    )


def _relative_edge_error(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
    page_width: float,
    page_height: float,
) -> float:
    return (
        abs(first[0] - second[0]) / page_width
        + abs(first[2] - second[2]) / page_width
        + abs(first[1] - second[1]) / page_height
        + abs(first[3] - second[3]) / page_height
    )


def _page_geometry(
    raw_page: Any,
) -> tuple[
    float | None,
    float | None,
    list[tuple[float, float, float, float]],
    frozenset[tuple[float, float, float, float]],
]:
    if not isinstance(raw_page, dict):
        raise ContractError("MinerU middle_json pages must be objects")
    size = raw_page.get("page_size")
    if size is None:
        return None, None, [], frozenset()
    if not _finite_number_list(size, 2) or not all(item > 0 for item in size):
        raise ContractError("MinerU page_size must contain two positive finite numbers")
    bboxes: list[tuple[float, float, float, float]] = []
    for key in ("para_blocks", "preproc_blocks"):
        _collect_bboxes(raw_page.get(key), bboxes)
    return float(size[0]), float(size[1]), bboxes, frozenset(bboxes)


def _collect_bboxes(
    value: Any, output: list[tuple[float, float, float, float]]
) -> None:
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, list):
            stack.extend(reversed(current))
        elif isinstance(current, dict):
            bbox = current.get("bbox")
            if bbox is not None:
                if not _finite_number_list(bbox, 4):
                    raise ContractError(
                        "MinerU middle bbox must contain four finite JSON numbers"
                    )
                output.append(tuple(float(item) for item in bbox))
            stack.append(current.get("blocks"))


def _block_text(raw: dict[str, Any]) -> str | None:
    fragments: list[str] = []
    for key in (
        "text",
        "list_items",
        "equation",
        "latex",
        "table_body",
        "figure_caption",
        "image_caption",
        "table_caption",
        "chart_caption",
        "figure_footnote",
        "image_footnote",
        "table_footnote",
        "chart_footnote",
    ):
        _collect_text(raw.get(key), fragments)
    normalized = "\n".join(part for part in fragments if part.strip())
    return unicodedata.normalize("NFC", normalized) if normalized else None


def _collect_text(value: Any, output: list[str]) -> None:
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            output.append(current)
        elif isinstance(current, list):
            stack.extend(reversed(current))
        elif isinstance(current, dict):
            stack.extend(
                current[key]
                for key in reversed(("text", "content", "latex"))
                if key in current
            )


def _json_object(value: dict[str, Any]) -> dict[str, Any]:
    try:
        clean = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (RecursionError, TypeError, ValueError) as exc:
        raise ContractError("MinerU block is not finite JSON") from exc
    if not isinstance(clean, dict):
        raise ContractError("MinerU block must remain an object")
    return clean


def _finite_number_list(value: Any, length: int) -> bool:
    if not isinstance(value, list) or len(value) != length:
        return False
    for item in value:
        if type(item) not in {int, float}:
            return False
        try:
            if not math.isfinite(item):
                return False
        except OverflowError:
            return False
    return True


__all__ = ["normalize_mineru"]
