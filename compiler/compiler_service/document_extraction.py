"""Deterministic, versioned document-content projection from trusted DocumentIR."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints

from .errors import ContractError
from .graph_contract import StructuredVisualEvidence, canonical_json
from .models import (
    BoundingBox,
    DocumentBlock,
    DocumentIR,
    Sha256,
    ShortText,
    StrictModel,
)
from .openalex_topics import document_doi
from .structured_source import (
    StructuredSourceError,
    build_structured_source_catalog,
    hydrate_selected_structured_visuals,
)

DOCUMENT_EXTRACTION_SCHEMA_VERSION = "document-extraction/1.0"
MAX_DOCUMENT_EXTRACTION_BYTES = 20 * 1024 * 1024
MAX_VISUAL_LOCATORS = 4_096
MAX_ENRICHED_VISUALS = 512
MAX_WARNINGS = 64
MAX_METADATA_TEXT = 10_000
MAX_ABSTRACT_TEXT = 100_000
MAX_VISUAL_TEXT = 10_000
MAX_LABEL_TEXT = 1_000
MAX_METADATA_CONTEXT = 40_000

METADATA_FIELDS = (
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
)

MetadataText = Annotated[str, StringConstraints(max_length=MAX_METADATA_TEXT)]
AbstractText = Annotated[str, StringConstraints(max_length=MAX_ABSTRACT_TEXT)]
VisualText = Annotated[str, StringConstraints(max_length=MAX_VISUAL_TEXT)]
LabelText = Annotated[str, StringConstraints(max_length=MAX_LABEL_TEXT)]
FieldSource = Literal["rule", "artifact", "doi_registry", "empty"]
VisualSourceKind = Literal["figure", "chart", "image", "table"]

_TITLE_RE = re.compile(r"^\s{0,3}#\s+(.+?)\s*$", re.MULTILINE)
_ABSTRACT_RE = re.compile(
    r"^\s{0,3}#{0,6}[ \t]*(?:"
    r"a[ \t]*b[ \t]*s[ \t]*t[ \t]*r[ \t]*a[ \t]*c[ \t]*t|"
    r"\u6458[ \t]*\u8981)[ \t]*[:\uff1a]?[ \t]*(.*)$",
    re.IGNORECASE,
)
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
_URL_RE = re.compile(r"https?://[^\s)>\"]+", re.IGNORECASE)
_ISSN_RE = re.compile(r"\b\d{4}-\d{3}[\dXx]\b")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_PAGE_RANGE_RE = re.compile(r"\b\d+\s*[-\u2013]\s*\d+\b")
_EXPLICIT_VOLUME_RE = re.compile(
    r"\bvol(?:ume)?\.?\s*(\d+)\b(?:\s*[,;]?\s*(?:no\.?|issue)\s*(\d+))?",
    re.IGNORECASE,
)
_JOURNAL_YEAR_VOLUME_RE = re.compile(
    r"^(?P<journal>[A-Z][^\n]{2,240}?)\s*,?\s+"
    r"(?P<volume>\d{1,4})\s*\((?P<year>(?:19|20)\d{2})\)\s+"
    r"(?P<locator>[A-Za-z]?\d{3,12}(?:\s*[-\u2013]\s*[A-Za-z]?\d{1,12})?)"
    r"\s*[.]?$"
)
_JOURNAL_VOL_PAGES_RE = re.compile(
    r"^(?P<journal>[A-Z][^\n]{2,240}?)\s*,\s*"
    r"(?:(?P<year>(?:19|20)\d{2})\s*,\s*)?"
    r"vol(?:ume)?[.]?\s*(?P<volume>\d{1,4})"
    r"(?:\s*,?\s*(?:no[.]?|issue)\s*(?P<issue>[A-Za-z0-9.-]+))?"
    r"\s*,?\s*(?:pp?[.:]?|pages?)\s*"
    r"(?P<pages>[A-Za-z]?\d+\s*[-\u2013]\s*[A-Za-z]?\d+)"
    r"\s*[.]?$",
    re.IGNORECASE,
)
_JOURNAL_VOLUME_PAGES_RE = re.compile(
    r"^(?P<journal>[A-Z][^\n]{2,240}?)\s*,\s*"
    r"(?P<volume>\d{1,4})[.]?\s*(?:pp?[.:]?|pages?)\s*"
    r"(?P<pages>[A-Za-z]?\d+\s*[-\u2013]\s*[A-Za-z]?\d+)"
    r"(?:[^\n]*)$",
    re.IGNORECASE,
)
_COPYRIGHT_PUBLISHER_RE = re.compile(
    r"(?:\u00a9|copyright\s*(?:\(c\)|\u00a9)?)\s*(?:19|20)\d{2}\s+"
    r"(?P<publisher>[^\n;]{2,100}?)"
    r"(?=\s*(?:all\s+rights|$))",
    re.IGNORECASE,
)
_PUBLISHED_BY_RE = re.compile(
    r"\bpublished\s+by\s+(?P<publisher>[^\n;]{2,100}?)"
    r"(?=\s+(?:all\s+rights|this\b|under\b|on\s+behalf\b)|$)",
    re.IGNORECASE,
)
_HEADER_FOOTER_TYPES = frozenset({"header", "footer", "page_header", "page_footer"})


class DocumentExtractionParser(StrictModel):
    name: Literal["mineru"]
    version: ShortText
    backend: Literal["pipeline"]
    method: Literal["auto"]


class DocumentMetadata(StrictModel):
    title: MetadataText
    abstract: AbstractText
    year: MetadataText
    author: MetadataText
    journal: MetadataText
    issn: MetadataText
    volume: MetadataText
    issue: MetadataText
    page: MetadataText
    article_number: MetadataText
    doi: MetadataText
    link: MetadataText
    publisher: MetadataText
    language: MetadataText


class DocumentMetadataSources(StrictModel):
    title: FieldSource
    abstract: FieldSource
    year: FieldSource
    author: FieldSource
    journal: FieldSource
    issn: FieldSource
    volume: FieldSource
    issue: FieldSource
    page: FieldSource
    article_number: FieldSource
    doi: FieldSource
    link: FieldSource
    publisher: FieldSource
    language: FieldSource


class DocumentExtractionStats(StrictModel):
    page_count: Annotated[int, Field(ge=1)]
    text_block_count: Annotated[int, Field(ge=0)]
    figure_count: Annotated[int, Field(ge=0)]
    table_count: Annotated[int, Field(ge=0)]
    image_count: Annotated[int, Field(ge=0)]
    visual_count: Annotated[int, Field(ge=0)]


class DocumentExtractionStructuredVisual(StructuredVisualEvidence):
    kind: Literal["structured_visual_evidence"] = "structured_visual_evidence"


class DocumentExtractionVisual(StrictModel):
    block_id: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=128,
            pattern=r"^[A-Za-z][A-Za-z0-9._:-]*$",
        ),
    ]
    kind: Literal["figure", "table"]
    source_kind: VisualSourceKind
    page: Annotated[int, Field(ge=1)]
    region: BoundingBox | None
    label: LabelText
    caption: VisualText
    footnote: VisualText
    visual: DocumentExtractionStructuredVisual | None


class DocumentExtraction(StrictModel):
    schema_version: Literal["document-extraction/1.0"] = (
        DOCUMENT_EXTRACTION_SCHEMA_VERSION
    )
    artifact_version_id: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=128,
            pattern=r"^[A-Za-z][A-Za-z0-9._:-]*$",
        ),
    ]
    artifact_sha256: Sha256
    parser: DocumentExtractionParser
    metadata: DocumentMetadata
    field_sources: DocumentMetadataSources
    stats: DocumentExtractionStats
    markdown: Annotated[str, StringConstraints(max_length=8_000_000)]
    visuals: list[DocumentExtractionVisual] = Field(max_length=MAX_VISUAL_LOCATORS)
    warnings: list[ShortText] = Field(max_length=MAX_WARNINGS)


def build_document_extraction(document: DocumentIR) -> DocumentExtraction:
    """Project one validated DocumentIR without model calls or raw HTML fields."""

    visual_blocks = [
        block
        for page in document.pages
        for block in page.blocks
        if block.kind in {"figure", "chart", "image", "table"}
    ]
    warnings: list[str] = []
    if len(visual_blocks) > MAX_VISUAL_LOCATORS:
        omitted = len(visual_blocks) - MAX_VISUAL_LOCATORS
        visual_blocks = visual_blocks[:MAX_VISUAL_LOCATORS]
        warnings.append(
            f"{omitted} visual locators were omitted after the first "
            f"{MAX_VISUAL_LOCATORS} items to keep the document extraction within "
            "its response boundary."
        )

    warnings.extend(document.warnings)
    metadata, field_sources = _metadata(document)
    visuals = _visuals(document, visual_blocks, warnings)
    counts = Counter(item.source_kind for item in visuals)
    table_count = counts["table"]
    image_count = counts["image"]
    figure_count = len(visuals) - table_count
    projection = _projection(
        document,
        metadata,
        field_sources,
        visuals,
        warnings,
        figure_count=figure_count,
        table_count=table_count,
        image_count=image_count,
    )

    if _encoded_size(projection) > MAX_DOCUMENT_EXTRACTION_BYTES and any(
        item.visual is not None for item in visuals
    ):
        warnings.append(
            "Structured visual payloads were omitted because the document "
            "extraction exceeded its response boundary; Markdown and source "
            "locations are retained."
        )
        visuals = [
            DocumentExtractionVisual(
                block_id=item.block_id,
                kind=item.kind,
                source_kind=item.source_kind,
                page=item.page,
                region=item.region,
                label=item.label,
                caption=item.caption,
                footnote=item.footnote,
                visual=None,
            )
            for item in visuals
        ]
        projection = _projection(
            document,
            metadata,
            field_sources,
            visuals,
            warnings,
            figure_count=figure_count,
            table_count=table_count,
            image_count=image_count,
        )

    if _encoded_size(projection) > MAX_DOCUMENT_EXTRACTION_BYTES:
        warnings.append(
            "Visual captions and footnotes were omitted because the document "
            "extraction exceeded its response boundary; the complete Markdown "
            "body and every retained source location remain available."
        )
        visuals = [
            DocumentExtractionVisual(
                block_id=item.block_id,
                kind=item.kind,
                source_kind=item.source_kind,
                page=item.page,
                region=item.region,
                label=f"{item.source_kind.title()} {index}",
                caption="",
                footnote="",
                visual=None,
            )
            for index, item in enumerate(visuals, start=1)
        ]
        projection = _projection(
            document,
            metadata,
            field_sources,
            visuals,
            warnings,
            figure_count=figure_count,
            table_count=table_count,
            image_count=image_count,
        )

    if _encoded_size(projection) > MAX_DOCUMENT_EXTRACTION_BYTES:
        raise ContractError(
            "document extraction Markdown and visual locations exceed the "
            f"{MAX_DOCUMENT_EXTRACTION_BYTES}-byte response boundary"
        )
    return projection


def _projection(
    document: DocumentIR,
    metadata: DocumentMetadata,
    field_sources: DocumentMetadataSources,
    visuals: list[DocumentExtractionVisual],
    warnings: list[str],
    *,
    figure_count: int,
    table_count: int,
    image_count: int,
) -> DocumentExtraction:
    text_block_count = sum(
        1
        for page in document.pages
        for block in page.blocks
        if block.kind not in {"figure", "chart", "image", "table"}
        and block.text is not None
    )
    return DocumentExtraction(
        artifact_version_id=document.artifact.version_id,
        artifact_sha256=document.artifact.artifact_sha256,
        parser=DocumentExtractionParser(
            name=document.parser.name,
            version=document.parser.version,
            backend=document.parser.backend,
            method=document.parser.method,
        ),
        metadata=metadata,
        field_sources=field_sources,
        stats=DocumentExtractionStats(
            page_count=document.page_count,
            text_block_count=text_block_count,
            figure_count=figure_count,
            table_count=table_count,
            image_count=image_count,
            visual_count=len(visuals),
        ),
        markdown=document.markdown,
        visuals=visuals,
        warnings=_bounded_warnings(warnings),
    )


def _visuals(
    document: DocumentIR,
    blocks: list[DocumentBlock],
    warnings: list[str],
) -> list[DocumentExtractionVisual]:
    try:
        catalog = build_structured_source_catalog(document, fail_on_unsafe=False)
    except (ContractError, StructuredSourceError, ValueError):
        catalog = None
        warnings.append(
            "Structured visual catalog could not be safely materialized; "
            "source locations are retained."
        )

    templates = {} if catalog is None else catalog.template_by_block_id
    unsafe_blocks = set() if catalog is None else set(catalog.unsafe_block_ids)
    if catalog is not None:
        warnings.extend(catalog.warnings)

    ordinals: Counter[str] = Counter()
    enriched_count = 0
    enrichment_limited = False
    output: list[DocumentExtractionVisual] = []
    for block in blocks:
        source_kind = block.kind
        if source_kind not in {"figure", "chart", "image", "table"}:
            raise AssertionError("visual block kind escaped its filter")
        ordinals[source_kind] += 1
        caption = _visual_text(block.structured.get(f"{source_kind}_caption"))
        footnote = _visual_text(block.structured.get(f"{source_kind}_footnote"))
        caption = _bounded_visual_text(caption, block.id, "caption", warnings)
        footnote = _bounded_visual_text(footnote, block.id, "footnote", warnings)
        label = _clean(caption.splitlines()[0] if caption else "")
        if not label:
            label = f"{source_kind.title()} {ordinals[source_kind]}"
        label = label[:MAX_LABEL_TEXT]

        visual = None
        template = templates.get(block.id)
        if template is not None and block.id not in unsafe_blocks:
            if enriched_count >= MAX_ENRICHED_VISUALS:
                enrichment_limited = True
            else:
                try:
                    hydrated = hydrate_selected_structured_visuals(
                        document,
                        catalog,
                        list(template.source_record_ids),
                    )
                    if len(hydrated) == 1:
                        visual = DocumentExtractionStructuredVisual.model_validate(
                            hydrated[0].visual.model_dump(mode="python"), strict=True
                        )
                        enriched_count += 1
                except (ContractError, StructuredSourceError, ValueError):
                    warnings.append(
                        f"{block.id}: structured visual could not be safely "
                        "materialized; source location is retained."
                    )

        output.append(
            DocumentExtractionVisual(
                block_id=block.id,
                kind="table" if source_kind == "table" else "figure",
                source_kind=source_kind,
                page=block.page,
                region=block.bbox,
                label=label,
                caption=caption,
                footnote=footnote,
                visual=visual,
            )
        )

    if enrichment_limited:
        warnings.append(
            f"Structured visual enrichment was limited to {MAX_ENRICHED_VISUALS} "
            "items; every Markdown body and visual source location is retained."
        )
    return output


def _metadata(
    document: DocumentIR,
) -> tuple[DocumentMetadata, DocumentMetadataSources]:
    markdown = document.markdown.replace("\r\n", "\n").replace("\r", "\n")
    page_text = _metadata_page_text(document)
    context = _metadata_context(page_text, markdown)
    header = _header_metadata(document)
    values = {field: "" for field in METADATA_FIELDS}
    sources: dict[str, FieldSource] = {field: "empty" for field in METADATA_FIELDS}

    title = _title(document, markdown)
    if title:
        values["title"] = title
        sources["title"] = "rule"
    else:
        fallback = _clean(Path(document.artifact.file_name).stem)
        if fallback:
            values["title"] = fallback
            sources["title"] = "artifact"

    values["abstract"] = _abstract(markdown)
    values["year"] = header.get("year", "") or _year(context)
    values["author"] = _explicit_field(context, ("author", "authors", "\u4f5c\u8005"))
    if not values["author"]:
        values["author"] = _author(markdown, title)
    values["journal"] = _explicit_field(
        context, ("journal", "\u671f\u520a")
    ) or header.get("journal", "")
    values["publisher"] = _explicit_field(
        context, ("publisher", "\u51fa\u7248\u8005", "\u51fa\u7248\u793e")
    ) or header.get("publisher", "")

    issn = _ISSN_RE.search(context)
    values["issn"] = "" if issn is None else issn.group(0)
    values["volume"], values["issue"] = _volume_issue(context)
    values["volume"] = values["volume"] or header.get("volume", "")
    values["issue"] = values["issue"] or header.get("issue", "")
    values["page"] = _page_range(context) or header.get("page", "")
    values["article_number"] = header.get("article_number", "")

    values["doi"] = document_doi(document) or ""
    if values["doi"]:
        values["link"] = f"https://doi.org/{values['doi']}"
    else:
        url = _URL_RE.search(context)
        values["link"] = "" if url is None else url.group(0).rstrip(".,;)")
    values["language"] = _language(markdown)

    for field in METADATA_FIELDS:
        limit = MAX_ABSTRACT_TEXT if field == "abstract" else MAX_METADATA_TEXT
        values[field] = values[field][:limit]
        if values[field] and sources[field] == "empty":
            sources[field] = "rule"
    return DocumentMetadata(**values), DocumentMetadataSources(**sources)


def _title(document: DocumentIR, markdown: str) -> str:
    for page in document.pages:
        for block in page.blocks:
            if block.kind == "title" and block.text:
                return _clean(block.text)
    match = _TITLE_RE.search(markdown)
    return "" if match is None else _clean(match.group(1))


def _abstract(markdown: str) -> str:
    lines = markdown.split("\n")
    for index, line in enumerate(lines):
        match = _ABSTRACT_RE.match(line)
        if match is None:
            continue
        parts = [_clean(match.group(1))] if _clean(match.group(1)) else []
        for next_line in lines[index + 1 :]:
            if _HEADING_RE.match(next_line):
                break
            cleaned = _clean(next_line)
            if not cleaned:
                if parts:
                    break
                continue
            parts.append(cleaned)
            if len(" ".join(parts)) >= MAX_ABSTRACT_TEXT:
                break
        return _clean(" ".join(parts))[:MAX_ABSTRACT_TEXT]
    return ""


def _author(markdown: str, title: str) -> str:
    lines = markdown.splitlines()
    title_line = -1
    for index, line in enumerate(lines):
        match = _TITLE_RE.fullmatch(line)
        if match is not None and _clean(match.group(1)) == title:
            title_line = index
            break
    if title_line < 0:
        return ""
    for line in lines[title_line + 1 : title_line + 8]:
        candidate = _clean(line.replace("\\*", " "))
        if not candidate:
            continue
        lowered = candidate.casefold()
        if line.lstrip().startswith("#") or any(
            token in lowered
            for token in ("abstract", "article info", "university", "institute")
        ):
            break
        if "@" in candidate or re.search(r"\b\d{5,}\b", candidate):
            continue
        if not (2 <= len(candidate.split()) <= 80):
            continue
        candidate = re.sub(r"(?<=\w)\s+[a-z](?=\s*(?:[,;]|$))", "", candidate)
        candidate = re.sub(r"[*\u2020\u2021\u00a7]+", " ", candidate)
        candidate = re.sub(r"\s*[,;]\s*", "; ", candidate)
        candidate = re.sub(r"(?:;\s*){2,}", "; ", candidate)
        return _clean(candidate).strip(" ;")
    return ""


def _metadata_page_text(document: DocumentIR) -> dict[int, str]:
    output: dict[int, str] = {}
    for page in document.pages:
        values = [
            block.text
            for block in page.blocks
            if block.kind not in {"figure", "chart", "image", "table"} and block.text
        ]
        text = "\n".join(values).strip()
        if text:
            output[page.number] = text
    return output


def _metadata_context(page_text: dict[int, str], markdown: str) -> str:
    if not page_text:
        return markdown[:MAX_METADATA_CONTEXT]
    pages = sorted(page_text)
    selected = pages[:2]
    blocks = [f"[Page {page}]\n{page_text[page][:8_000]}" for page in selected]
    return "\n\n".join(blocks)[:MAX_METADATA_CONTEXT]


def _header_metadata(document: DocumentIR) -> dict[str, str]:
    """Read conservative bibliographic facts from front-matter headers/footers."""

    values: dict[str, str] = {}
    for page in document.pages:
        if page.number > 2:
            break
        for block in page.blocks:
            if not block.text:
                continue
            raw_type = block.structured.get("type")
            if not isinstance(raw_type, str):
                continue
            structured_type = raw_type.casefold().replace("-", "_").replace(" ", "_")
            if structured_type not in _HEADER_FOOTER_TYPES:
                continue
            for raw_line in block.text.splitlines():
                line = _clean(raw_line)
                if not line:
                    continue
                _merge_journal_header(values, line)
                publisher = _publisher_from_copyright(line)
                if publisher:
                    values.setdefault("publisher", publisher)
    return values


def _merge_journal_header(values: dict[str, str], line: str) -> None:
    match = _JOURNAL_YEAR_VOLUME_RE.fullmatch(line)
    if match is not None:
        values.setdefault("journal", _journal_name(match.group("journal")))
        values.setdefault("volume", match.group("volume"))
        values.setdefault("year", match.group("year"))
        locator = _locator(match.group("locator"))
        if _PAGE_RANGE_RE.fullmatch(locator):
            values.setdefault("page", locator)
        else:
            values.setdefault("article_number", locator)
        return

    match = _JOURNAL_VOL_PAGES_RE.fullmatch(line)
    if match is not None:
        values.setdefault("journal", _journal_name(match.group("journal")))
        values.setdefault("volume", match.group("volume"))
        if match.group("year"):
            values.setdefault("year", match.group("year"))
        if match.group("issue"):
            values.setdefault("issue", _clean(match.group("issue")))
        values.setdefault("page", _locator(match.group("pages")))
        return

    match = _JOURNAL_VOLUME_PAGES_RE.fullmatch(line)
    if match is not None:
        values.setdefault("journal", _journal_name(match.group("journal")))
        values.setdefault("volume", match.group("volume"))
        values.setdefault("page", _locator(match.group("pages")))


def _publisher_from_copyright(line: str) -> str:
    published_by = _PUBLISHED_BY_RE.search(line)
    match = published_by or _COPYRIGHT_PUBLISHER_RE.search(line)
    if match is None:
        return ""
    publisher = _clean(match.group("publisher")).strip(" ,;")
    if publisher.casefold().rstrip(".") in {
        "author",
        "authors",
        "the author",
        "the authors",
    }:
        return ""
    return publisher


def _journal_name(value: str) -> str:
    return _clean(value).rstrip(" ,.;")


def _locator(value: str) -> str:
    return re.sub(r"\s*[-\u2013]\s*", "-", _clean(value))


def _explicit_field(text: str, names: tuple[str, ...]) -> str:
    joined = "|".join(re.escape(name) for name in names)
    pattern = re.compile(
        rf"^\s*(?:{joined})\s*[:\uff1a]\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE
    )
    match = pattern.search(text)
    return "" if match is None else _clean(match.group(1))


def _year(text: str) -> str:
    candidates: list[tuple[int, str]] = []
    lowered = text.lower()
    for match in _YEAR_RE.finditer(text):
        year = match.group(0)
        nearby = lowered[max(0, match.start() - 40) : match.end() + 40]
        score = int(year)
        if any(token in nearby for token in ("published", "copyright", "\u51fa\u7248")):
            score += 10_000
        elif any(token in nearby for token in ("accepted", "received")):
            score += 1_000
        candidates.append((score, year))
    return max(candidates)[1] if candidates else ""


def _volume_issue(text: str) -> tuple[str, str]:
    explicit = _EXPLICIT_VOLUME_RE.search(text)
    if explicit is not None:
        return explicit.group(1), explicit.group(2) or ""
    return "", ""


def _page_range(text: str) -> str:
    for line in text.splitlines():
        lowered = line.lower()
        if not any(
            token in lowered
            for token in ("pages", "pp.", "page range", "\u9875\u7801", "\u9875")
        ):
            continue
        match = _PAGE_RANGE_RE.search(line)
        if match is not None:
            return _locator(match.group(0))
    return ""


def _language(text: str) -> str:
    sample = text[:4_000]
    chinese = len(re.findall(r"[\u4e00-\u9fff]", sample))
    latin = len(re.findall(r"[A-Za-z]", sample))
    if chinese >= 20 and chinese >= latin * 0.1:
        return "Chinese"
    if latin >= 30:
        return "English"
    return ""


def _visual_text(value: Any) -> str:
    values: list[str] = []
    stack = [value]
    length = 0
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            cleaned = unicodedata.normalize("NFC", current).strip()
            if cleaned:
                remaining = MAX_VISUAL_TEXT + 1 - length
                values.append(cleaned[:remaining])
                length += min(len(cleaned), remaining)
                if length > MAX_VISUAL_TEXT:
                    break
        elif isinstance(current, list):
            stack.extend(reversed(current))
    return "\n".join(values)


def _bounded_visual_text(
    value: str, block_id: str, field: str, warnings: list[str]
) -> str:
    if len(value) <= MAX_VISUAL_TEXT:
        return value
    warnings.append(
        f"{block_id}: {field} exceeded {MAX_VISUAL_TEXT} characters and was bounded."
    )
    return value[:MAX_VISUAL_TEXT]


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value or "").strip())


def _bounded_warnings(values: list[str]) -> list[str]:
    normalized = [
        _clean(value)[:1_000]
        for value in values
        if isinstance(value, str) and _clean(value)
    ]
    unique = list(dict.fromkeys(normalized))
    if len(unique) <= MAX_WARNINGS:
        return unique
    retained = unique[: MAX_WARNINGS - 1]
    retained.append(
        f"{len(unique) - len(retained)} additional document extraction "
        "warnings omitted."
    )
    return retained


def _encoded_size(projection: DocumentExtraction) -> int:
    return len(
        canonical_json(projection.model_dump(mode="json", exclude_none=False)).encode(
            "utf-8"
        )
    )


__all__ = [
    "DOCUMENT_EXTRACTION_SCHEMA_VERSION",
    "DocumentExtraction",
    "DocumentExtractionParser",
    "DocumentExtractionStats",
    "DocumentExtractionVisual",
    "DocumentMetadata",
    "DocumentMetadataSources",
    "build_document_extraction",
]
