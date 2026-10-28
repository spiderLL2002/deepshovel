"""Deterministically connect prose references to MinerU visual regions.

Model remains responsible for selecting the scientific evidence path.  This
module only restores a source-navigation link that MinerU already made
available: when a cited text anchor says ``图5``/``Fig. 5``/``表5``/``Table 5``,
the matching captioned visual block can be attached to the same graph node.
No image content or visual interpretation is introduced here.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal

from .graph_contract import PaperEvidenceGraph, canonical_json
from .models import BoundingBox, DocumentBlock, DocumentIR

_VisualKind = Literal["figure", "table"]
_LabelKey = tuple[_VisualKind, str]

_VISUAL_BLOCK_KINDS = frozenset({"figure", "table", "chart", "image"})
_CAPTION_FIELDS: dict[_VisualKind, tuple[str, ...]] = {
    "figure": ("figure_caption", "image_caption", "chart_caption"),
    "table": ("table_caption",),
}
_LABEL_TOKEN = r"(?P<number>[Ss]?[0-9]+(?:\.[0-9]+|[A-Za-z])?)"
_LABEL_END = r"(?![0-9A-Za-z])(?!(?:\.[0-9A-Za-z]))(?![-–—_/（(])"
_CN_LABEL = re.compile(rf"(?P<kind>[图表])\s*{_LABEL_TOKEN}{_LABEL_END}")
_EN_LABEL = re.compile(
    rf"(?<![A-Za-z])(?P<kind>fig(?:ure)?s?|tables?)\.?\s*"
    rf"{_LABEL_TOKEN}{_LABEL_END}",
    re.IGNORECASE,
)
_LABEL_TOKEN_PARTS = re.compile(
    r"(?P<supplement>[Ss]?)(?P<integer>[0-9]+)"
    r"(?:(?:\.(?P<decimal>[0-9]+))|(?P<panel>[A-Za-z]))?"
)
_LEADING_MARKUP = re.compile(r"^[\s#*_`]+")
_CLAUSE_BOUNDARY = re.compile(r"[。；;!?！？\n\r]")
_DERIVED_STRONG_PREFIX = re.compile(
    r"(?:"
    r"(?:数据|数值|测量值|观测值|结果)\s*(?:来自|来源于|取自)|"
    r"based\s+on(?:\s+(?:the\s+)?(?:data|values|measurements|results)\s+(?:in|from))?|"
    r"derived\s+from(?:\s+(?:the\s+)?(?:data|values|measurements|results)\s+(?:in|from))?|"
    r"(?:calculated|computed|generated|plotted)\s+from|"
    r"(?:data|values|measurements|results)\s+(?:from|in)"
    r")\s*$",
    re.IGNORECASE,
)
_DERIVED_PREFIX_REQUIRES_ACTION = re.compile(
    r"(?:由|基于|根据|依据|利用|采用|using)\s*$",
    re.IGNORECASE,
)
_DERIVED_ACTION_SUFFIX = re.compile(
    r"^\s*(?:"
    r"(?:(?:中|内)?的?)?\s*"
    r"(?:(?:数据|数值|测量值|观测值|结果)\s*)?"
    r"(?:得到|获得|计算|绘制|生成|推导|导出|用于|用来|被用于)|"
    r"(?:the\s+)?(?:data|values|measurements|results)\s+"
    r"(?:(?:was|were|is|are)\s+)?"
    r"(?:used|plotted|processed|calculated|computed|generated|derived)"
    r")",
    re.IGNORECASE,
)
_DERIVED_DATA_SUFFIX = re.compile(
    r"^\s*(?:"
    r"(?:(?:中|内)?的?)?\s*(?:数据|数值|测量值|观测值|结果)\s*"
    r"(?:被)?(?:用于|用来|得到|获得|计算|绘制|生成|推导|导出)|"
    r"(?:data|values|measurements|results)\s+"
    r"(?:(?:was|were|is|are)\s+)?"
    r"(?:used|plotted|processed|calculated|computed|generated|derived)"
    r")",
    re.IGNORECASE,
)
_REGION_TOLERANCE = 0.001
_MAX_CAPTION_CANDIDATES = 32
_MAX_VISUAL_REFERENCES = 8


@dataclass(frozen=True, slots=True)
class _VisualCandidate:
    key: _LabelKey
    label: str
    caption: str
    block_id: str
    page: int
    source_index: int
    bbox: BoundingBox
    caption_rank: int
    references: tuple[_LabelKey, ...]


def link_referenced_visuals(
    graph: PaperEvidenceGraph,
    document: DocumentIR,
) -> PaperEvidenceGraph:
    """Return a strictly revalidated graph enriched with safe visual anchors.

    Existing graph order defines priority: observations, reasoning, assumptions,
    then claims; within a node, cited anchors and references retain source order.
    A required new anchor beyond the strict graph ceiling fails closed instead
    of silently dropping a selected source link. Existing matching anchors can
    always be reused, including at the ceiling.
    """

    if not any(
        anchor.anchor_type == "text" and isinstance(anchor.locator.quote, str)
        for anchor in graph.anchors
    ):
        return graph

    candidates = _visual_candidates(document)
    if not candidates:
        return graph

    payload = graph.model_dump(mode="json")
    anchors: list[dict[str, Any]] = payload["anchors"]
    anchors_by_id = {anchor["id"]: anchor for anchor in anchors}
    used_ids = {
        item["id"]
        for collection in (
            "claims",
            "observations",
            "reasoning",
            "assumptions",
            "anchors",
            "visual_evidence",
            "scopes",
            "edges",
        )
        for item in payload[collection]
    }
    resolved: dict[str, str | None] = {}
    candidates_by_source_anchor: dict[str, tuple[_VisualCandidate, ...]] = {}
    updated = False

    for collection in ("observations", "reasoning", "assumptions", "claims"):
        for node in payload[collection]:
            linked_ids: list[str] = []
            # Only model-provided text anchors may trigger a visual source link.
            # Iterating a tuple prevents a newly linked visual from being scanned.
            for source_anchor_id in tuple(node["anchor_ids"]):
                source_anchor = anchors_by_id.get(source_anchor_id)
                if source_anchor is None or source_anchor["anchor_type"] != "text":
                    continue
                quote = source_anchor["locator"].get("quote")
                if not isinstance(quote, str):
                    continue
                if source_anchor_id not in candidates_by_source_anchor:
                    candidates_by_source_anchor[source_anchor_id] = (
                        _linked_visual_candidates(source_anchor, document, candidates)
                    )
                for candidate in candidates_by_source_anchor[source_anchor_id]:
                    updated = True
                    linked_id = _ensure_candidate_anchor(
                        candidate,
                        document=document,
                        anchors=anchors,
                        anchors_by_id=anchors_by_id,
                        used_ids=used_ids,
                        resolved=resolved,
                    )
                    if (
                        linked_id is not None
                        and linked_id not in node["anchor_ids"]
                        and linked_id not in linked_ids
                    ):
                        linked_ids.append(linked_id)
            node["anchor_ids"].extend(linked_ids)

    return PaperEvidenceGraph.model_validate(payload, strict=True) if updated else graph


def _linked_visual_candidates(
    source_anchor: dict[str, Any],
    document: DocumentIR,
    candidates: dict[_LabelKey, tuple[_VisualCandidate, ...]],
) -> tuple[_VisualCandidate, ...]:
    quote = source_anchor["locator"]["quote"]
    direct_keys = _reference_keys(quote)
    selected: list[_VisualCandidate] = []
    source_page = source_anchor["locator"].get("page")
    for key in direct_keys:
        matching = candidates.get(key)
        if not matching:
            continue
        direct = _nearest_candidate(matching, source_page)
        selected.extend(_candidate_and_direct_references(direct, candidates))

    adjacent_key = _adjacent_reference_key(source_anchor, document)
    if adjacent_key is not None and adjacent_key not in direct_keys:
        matching = candidates.get(adjacent_key)
        if matching:
            # Context recovery is direct navigation only. It must not inherit
            # the visual candidate's derived references.
            selected.append(_nearest_candidate(matching, source_page))
    return tuple(selected)


def _ensure_candidate_anchor(
    candidate: _VisualCandidate,
    *,
    document: DocumentIR,
    anchors: list[dict[str, Any]],
    anchors_by_id: dict[str, dict[str, Any]],
    used_ids: set[str],
    resolved: dict[str, str | None],
) -> str | None:
    if candidate.block_id in resolved:
        return resolved[candidate.block_id]
    existing = _matching_anchor(anchors, candidate, document)
    if existing is not None:
        _complete_existing_anchor(existing, candidate)
        resolved[candidate.block_id] = existing["id"]
        return existing["id"]
    anchor = _new_anchor(candidate, document, used_ids)
    anchors.append(anchor)
    anchors_by_id[anchor["id"]] = anchor
    used_ids.add(anchor["id"])
    resolved[candidate.block_id] = anchor["id"]
    return anchor["id"]


def _adjacent_reference_key(
    source_anchor: dict[str, Any], document: DocumentIR
) -> _LabelKey | None:
    """Recover one unambiguous reference immediately surrounding an exact quote."""

    locator = source_anchor["locator"]
    page = locator.get("page")
    quote = locator.get("quote")
    if type(page) is not int or not isinstance(quote, str):
        return None
    exact = unicodedata.normalize("NFC", quote)
    matching: list[tuple[str, int]] = []
    for document_page in document.pages:
        if document_page.number != page:
            continue
        for block in document_page.blocks:
            text = unicodedata.normalize("NFC", block.text or "")
            positions = _substring_positions(text, exact)
            if len(positions) == 1:
                matching.append((text, positions[0]))
    if len(matching) != 1:
        return None
    text, quote_start = matching[0]
    context = _same_and_previous_sentence(text, quote_start, len(exact))
    if context is None:
        return None
    keys = _reference_keys(context)
    return keys[0] if len(keys) == 1 else None


def _substring_positions(value: str, needle: str) -> list[int]:
    positions: list[int] = []
    cursor = 0
    while len(positions) < 2:
        found = value.find(needle, cursor)
        if found < 0:
            break
        positions.append(found)
        cursor = found + max(1, len(needle))
    return positions


def _same_and_previous_sentence(
    text: str, quote_start: int, quote_length: int
) -> str | None:
    paragraph_start = (
        max(
            text.rfind("\n", 0, quote_start),
            text.rfind("\r", 0, quote_start),
        )
        + 1
    )
    newline_positions = [
        position
        for position in (text.find("\n", quote_start), text.find("\r", quote_start))
        if position >= 0
    ]
    paragraph_end = min(newline_positions, default=len(text))
    quote_end = quote_start + quote_length
    if quote_end > paragraph_end:
        return None
    paragraph = text[paragraph_start:paragraph_end]
    local_start = quote_start - paragraph_start
    spans = _sentence_spans(paragraph)
    sentence_index = next(
        (
            index
            for index, (start, end) in enumerate(spans)
            if start <= local_start < end
        ),
        None,
    )
    if sentence_index is None or quote_end - paragraph_start > spans[sentence_index][1]:
        return None
    context_start = spans[max(0, sentence_index - 1)][0]
    return paragraph[context_start : spans[sentence_index][1]]


def _sentence_spans(value: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    for index, character in enumerate(value):
        if character in "。．！？!?" or (
            character == "." and _period_ends_sentence(value, index)
        ):
            spans.append((start, index + 1))
            start = index + 1
    if start < len(value):
        spans.append((start, len(value)))
    return spans or [(0, len(value))]


def _period_ends_sentence(value: str, index: int) -> bool:
    if index + 1 < len(value) and not value[index + 1].isspace():
        return False
    # Inspect only the immediately preceding ASCII token. Slicing the complete
    # prefix and applying a regex for every period made long OCR paragraphs
    # quadratic in length.
    token_start = index
    while (
        token_start > 0
        and value[token_start - 1].isascii()
        and value[token_start - 1].isalpha()
    ):
        token_start -= 1
    token = value[token_start:index].casefold()
    return not token or token not in {
        "fig",
        "eq",
        "e",
        "i",
    }


def _visual_candidates(
    document: DocumentIR,
) -> dict[_LabelKey, tuple[_VisualCandidate, ...]]:
    found: list[_VisualCandidate] = []
    for page in document.pages:
        for block in page.blocks:
            if block.kind not in _VISUAL_BLOCK_KINDS or block.bbox is None:
                continue
            visual_kind: _VisualKind = "table" if block.kind == "table" else "figure"
            caption = _caption_with_label(block, visual_kind)
            if caption is None:
                continue
            quote, label_key, label, rank = caption
            found.append(
                _VisualCandidate(
                    key=label_key,
                    label=label,
                    caption=quote,
                    block_id=block.id,
                    page=block.page,
                    source_index=block.source_index,
                    bbox=block.bbox,
                    caption_rank=rank,
                    references=tuple(
                        reference
                        for reference in _derived_reference_keys(block.text or "")
                        if reference != label_key
                    )[:_MAX_VISUAL_REFERENCES],
                )
            )

    # Structured caption fields outrank the cautious text fallback.  Remaining
    # ties follow stable source coordinates rather than response/dict ordering.
    found.sort(
        key=lambda item: (
            item.caption_rank,
            item.page,
            item.source_index,
            item.block_id,
        )
    )
    result: dict[_LabelKey, list[_VisualCandidate]] = {}
    for item in found:
        result.setdefault(item.key, []).append(item)
    return {key: tuple(values) for key, values in result.items()}


def _candidate_and_direct_references(
    candidate: _VisualCandidate,
    candidates: dict[_LabelKey, tuple[_VisualCandidate, ...]],
) -> tuple[_VisualCandidate, ...]:
    """Return the cited visual plus one source-declared reference layer."""

    linked = [candidate]
    seen = {candidate.block_id}
    for key in candidate.references:
        matching = candidates.get(key)
        if not matching:
            continue
        referenced = _nearest_candidate(matching, candidate.page)
        if referenced.block_id in seen:
            continue
        seen.add(referenced.block_id)
        linked.append(referenced)
    return tuple(linked)


def _nearest_candidate(
    candidates: tuple[_VisualCandidate, ...],
    source_page: Any,
) -> _VisualCandidate:
    page = source_page if type(source_page) is int else None
    return min(
        candidates,
        key=lambda item: (
            item.caption_rank,
            abs(item.page - page) if page is not None else item.page,
            item.page,
            item.source_index,
            item.block_id,
        ),
    )


def _caption_with_label(
    block: DocumentBlock,
    visual_kind: _VisualKind,
) -> tuple[str, _LabelKey, str, int] | None:
    block_text = unicodedata.normalize("NFC", block.text or "")
    for field in _CAPTION_FIELDS[visual_kind]:
        for caption in _caption_strings(block.structured.get(field)):
            exact = unicodedata.normalize("NFC", caption)
            if not exact.strip() or len(exact) > 16_000 or exact not in block_text:
                continue
            owned = _owned_label(exact, visual_kind)
            if owned is not None:
                key, label = owned
                return exact, key, label, 0

    # Some MinerU versions keep the caption only in block.text.  Treat only a
    # leading label on one of the first three non-empty lines as ownership; a
    # later prose mention is not enough to turn a block into a visual anchor.
    nonempty = [line for line in block_text.splitlines() if line.strip()][:3]
    for line in nonempty:
        if len(line) > 16_000:
            continue
        owned = _owned_label(line, visual_kind)
        if owned is not None:
            key, label = owned
            return line, key, label, 1
    return None


def _caption_strings(value: Any) -> list[str]:
    output: list[str] = []
    stack = [value]
    while stack and len(output) < _MAX_CAPTION_CANDIDATES:
        current = stack.pop()
        if isinstance(current, str):
            output.append(current)
        elif isinstance(current, list):
            stack.extend(reversed(current[:_MAX_CAPTION_CANDIDATES]))
        elif isinstance(current, dict):
            stack.extend(
                current[key]
                for key in reversed(("text", "content", "latex"))
                if key in current
            )
    return output


def _owned_label(
    value: str,
    expected_kind: _VisualKind,
) -> tuple[_LabelKey, str] | None:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = _LEADING_MARKUP.sub("", normalized)
    for pattern in (_CN_LABEL, _EN_LABEL):
        match = pattern.match(normalized)
        if match is None:
            continue
        key, label = _label_from_match(match)
        if key[0] == expected_kind:
            return key, label
    return None


def _reference_keys(value: str) -> list[_LabelKey]:
    normalized = unicodedata.normalize("NFKC", value)
    matches = _reference_matches(normalized)
    result: list[_LabelKey] = []
    for _, _, key in matches:
        if key not in result:
            result.append(key)
    return result


def _derived_reference_keys(value: str) -> list[_LabelKey]:
    """Find only visual references explicitly declared as data derivation."""

    normalized = unicodedata.normalize("NFKC", value)
    result: list[_LabelKey] = []
    for start, end, key in _reference_matches(normalized):
        before = _clause_tail(normalized[:start])
        after = _clause_head(normalized[end:])
        strong_prefix = _DERIVED_STRONG_PREFIX.search(before) is not None
        prefix_with_action = (
            _DERIVED_PREFIX_REQUIRES_ACTION.search(before) is not None
            and _DERIVED_ACTION_SUFFIX.match(after) is not None
        )
        data_suffix = _DERIVED_DATA_SUFFIX.match(after) is not None
        if (strong_prefix or prefix_with_action or data_suffix) and key not in result:
            result.append(key)
    return result


def _reference_matches(value: str) -> list[tuple[int, int, _LabelKey]]:
    matches = [
        (match.start(), match.end(), _label_from_match(match)[0])
        for pattern in (_CN_LABEL, _EN_LABEL)
        for match in pattern.finditer(value)
    ]
    matches.sort(key=lambda item: (item[0], item[1], item[2]))
    return matches


def _clause_tail(value: str) -> str:
    parts = _CLAUSE_BOUNDARY.split(value)
    return parts[-1][-160:]


def _clause_head(value: str) -> str:
    parts = _CLAUSE_BOUNDARY.split(value, maxsplit=1)
    return parts[0][:160]


def _label_from_match(match: re.Match[str]) -> tuple[_LabelKey, str]:
    raw_kind = match.group("kind").casefold()
    number = _normalize_label_token(match.group("number"))
    if raw_kind in {"图", "fig", "figs", "figure", "figures"}:
        kind: _VisualKind = "figure"
        label = f"图{number}" if raw_kind == "图" else f"Figure {number}"
    else:
        kind = "table"
        label = f"表{number}" if raw_kind == "表" else f"Table {number}"
    return (kind, number), label


def _normalize_label_token(value: str) -> str:
    match = _LABEL_TOKEN_PARTS.fullmatch(unicodedata.normalize("NFKC", value))
    if match is None:  # The label regex and token normalizer must stay paired.
        raise ValueError("unsupported visual label token")
    integer = match.group("integer").lstrip("0") or "0"
    prefix = "S" if match.group("supplement") else ""
    decimal = match.group("decimal")
    if decimal is not None:
        return f"{prefix}{integer}.{decimal.lstrip('0') or '0'}"
    panel = match.group("panel")
    return f"{prefix}{integer}{panel.casefold() if panel else ''}"


def _matching_anchor(
    anchors: list[dict[str, Any]],
    candidate: _VisualCandidate,
    document: DocumentIR,
) -> dict[str, Any] | None:
    expected_type = candidate.key[0]
    exact_region: list[dict[str, Any]] = []
    same_label: list[dict[str, Any]] = []
    for anchor in anchors:
        if anchor["anchor_type"] != expected_type or not _trusted_identity(
            anchor, document
        ):
            continue
        locator = anchor["locator"]
        if locator.get("page") == candidate.page and _same_region(
            locator.get("region"), candidate.bbox
        ):
            exact_region.append(anchor)
            continue
        label = locator.get("figure" if expected_type == "figure" else "table")
        if isinstance(label, str) and candidate.key in _reference_keys(label):
            same_label.append(anchor)
    matches = exact_region or same_label
    return min(matches, key=lambda item: item["id"]) if matches else None


def _trusted_identity(anchor: dict[str, Any], document: DocumentIR) -> bool:
    artifact = document.artifact
    return (
        anchor.get("artifact_id") == artifact.artifact_id
        and anchor.get("version_id") == artifact.version_id
        and anchor.get("artifact_sha256") == artifact.artifact_sha256
        and anchor.get("media_type") == artifact.media_type
    )


def _complete_existing_anchor(
    anchor: dict[str, Any], candidate: _VisualCandidate
) -> None:
    """Fill only missing locator facts from the matched authoritative block."""

    locator = anchor["locator"]
    visual_kind = candidate.key[0]
    if locator.get("page") is None:
        locator["page"] = candidate.page
    label_field = "figure" if visual_kind == "figure" else "table"
    if locator.get(label_field) is None:
        locator[label_field] = candidate.label
    if locator.get("region") is None:
        locator["region"] = candidate.bbox.model_dump(mode="json") | {
            "unit": "normalized"
        }
    if locator.get("quote") is None:
        locator["quote"] = candidate.caption


def _same_region(value: Any, bbox: BoundingBox) -> bool:
    if not isinstance(value, dict) or value.get("unit") != "normalized":
        return False
    try:
        return all(
            abs(float(value[key]) - float(getattr(bbox, key))) <= _REGION_TOLERANCE
            for key in ("x", "y", "width", "height")
        )
    except (KeyError, TypeError, ValueError):
        return False


def _new_anchor(
    candidate: _VisualCandidate,
    document: DocumentIR,
    used_ids: set[str],
) -> dict[str, Any]:
    artifact = document.artifact
    region = candidate.bbox.model_dump(mode="json") | {"unit": "normalized"}
    identity = canonical_json(
        {
            "version_id": artifact.version_id,
            "block_id": candidate.block_id,
            "kind": candidate.key[0],
            "number": candidate.key[1],
            "page": candidate.page,
            "region": region,
        }
    )
    salt = 0
    while True:
        suffix = "" if salt == 0 else f"\0{salt}"
        digest = hashlib.sha256((identity + suffix).encode()).hexdigest()
        anchor_id = f"anchor:compiler:visual:{digest}"
        if anchor_id not in used_ids:
            break
        salt += 1
    visual_kind = candidate.key[0]
    return {
        "id": anchor_id,
        "artifact_id": artifact.artifact_id,
        "version_id": artifact.version_id,
        "artifact_sha256": artifact.artifact_sha256,
        "media_type": artifact.media_type,
        "anchor_type": visual_kind,
        "locator": {
            "page": candidate.page,
            "section": None,
            "paragraph": None,
            "figure": candidate.label if visual_kind == "figure" else None,
            "table": candidate.label if visual_kind == "table" else None,
            "row_range": None,
            "column_names": [],
            "series": None,
            "region": region,
            "quote": candidate.caption,
        },
    }


__all__ = ["link_referenced_visuals"]
