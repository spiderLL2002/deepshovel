"""Deterministic bibliography and high-value inline-citation source projection.

This module records navigation facts only.  It never decides whether a cited
paper is correct and never mutates the Paper Evidence Graph.  Cross-paper
assertion extraction starts later, after the Web tier resolves both snapshots.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from .models import BoundingBox, DocumentBlock, DocumentIR, Identifier, StrictModel

CITATION_SOURCE_SCHEMA_VERSION = "citation-source/1.0"
CITATION_SOURCE_EXTRACTOR_VERSION = "citation-source-extractor/1.0"

CitationFunctionCandidate = Literal[
    "result_comparison",
    "replication",
    "critique",
]

NormalizedDoi = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=500,
        pattern=r"^10\.[0-9]{4,9}/[-._;()/:a-z0-9]+$",
    ),
]

_POST_REFERENCE_HEADING_RE = re.compile(
    r"^(?:append(?:ix|ices)|supplement(?:ary)?(?:\s+(?:materials?|information))?|"
    r"supporting\s+information|acknowledg(?:e)?ments?|author\s+contributions?|"
    r"(?:declaration\s+of\s+)?competing\s+interests?|conflicts?\s+of\s+interests?|"
    r"data\s+(?:and\s+code\s+)?availability(?:\s+statement)?|funding|"
    r"abbreviations|declarations?|附录|补充材料|支撑信息|致谢|作者贡献|利益冲突|"
    r"基金(?:项目)?|数据可用性(?:声明)?)$",
    re.IGNORECASE,
)
# "5. References", "VI REFERENCES", "参考文献：" -> the bare heading text.
_HEADING_NUMBER_RE = re.compile(
    r"^\s*(?:[0-9]+(?:\.[0-9]+)*|[IVXLC]+|[一二三四五六七八九十]+)\s*[.)、:：\s]\s*"
)
_REFERENCE_START_RE = re.compile(
    r"(?m)^\s*(?:\[(?P<bracket>[0-9]{1,5})\]|(?P<number>[0-9]{1,5})[.)])\s+"
)
_INLINE_REFERENCE_START_RE = re.compile(r"\[(?P<bracket>[0-9]{1,5})\]\s+")
_NUMERIC_MARKER_RE = re.compile(
    r"\[(?P<body>[0-9]{1,5}(?:\s*(?:[,;]|[-\u2013\u2014])\s*[0-9]{1,5})*)\]"
)
_FLATTENED_SUPERSCRIPT_RE = re.compile(
    r"(?<=[a-z\u3400-\u9fff])"
    r"(?P<body>[0-9]{1,3}(?:\s*(?:[,;]|[-\u2013\u2014])\s*[0-9]{1,3})*)"
    r"(?![0-9A-Za-z\u3400-\u9fff+\u207a])"
)
_UNICODE_SUPERSCRIPT_RE = re.compile(
    r"(?<=[^\W\d_\u3400-\u9fff]|[\u3400-\u9fff.,;)\]])"
    r"(?P<body>[\u00b9\u00b2\u00b3\u2070\u2074-\u2079]+"
    r"(?:\s*[,;\u207b\u2013\u2014-]\s*[\u00b9\u00b2\u00b3\u2070\u2074-\u2079]+)*)"
    r"(?![\u207a\u207b0-9])"
)
_MARKUP_SUPERSCRIPT_RE = re.compile(
    r"(?:\$\s*\^\s*\{?|<sup>)\s*"
    r"(?P<body>[0-9]{1,3}(?:\s*(?:[,;]|[-\u2013\u2014])\s*[0-9]{1,3})*)"
    r"\s*(?:\}?\s*\$|</sup>)"
)
_SUPERSCRIPT_DIGITS = str.maketrans(
    "\u00b9\u00b2\u00b3\u2070\u2074\u2075\u2076\u2077\u2078\u2079\u207b",
    "1230456789-",
)
# A digit run glued to these is a unit, gene, histone mark or chemical charge
# (km2, p53, H3K4me3, Fe2+), not a flattened citation superscript.
_UNIT_WORDS = frozenset(
    {
        "m", "km", "cm", "mm", "nm", "um", "μm", "g", "kg", "mg", "ug", "μg",
        "ng", "s", "ms", "l", "ml", "mol", "mmol", "kj", "pa", "kpa", "mpa",
        "gpa", "ha", "yr", "kyr", "myr", "ma", "ka", "ga", "sec", "min", "hr",
        "day", "per", "ppm", "ppb", "wt", "vol",
    }
)
_CJK_NUMBER_PREFIXES = frozenset("图表第式章节页年月日号层组期个次种类级段区批步倍度")
_FLATTENED_WORD_RE = re.compile(r"([^\W\d_]+)$")
_CITATION_CUE_NUMBER_RE = re.compile(
    r"(?:et\s+al\.|refs?\.)\s*"
    r"(?P<body>[0-9]{1,3}(?:\s*(?:[,;]|[-\u2013\u2014])\s*[0-9]{1,3})*)"
    r"(?![0-9A-Za-z])",
    re.IGNORECASE,
)
_AUTHOR_SURNAME = (
    r"(?:(?:Da|De|Del|Di|Du|La|Le|Van|Von|da|de|del|di|du|la|le|van|von)\s+)?"
    # Any script's letters (Müller, García, Øvrebø); upper-case initial is
    # checked after matching because ``re`` has no \p{Lu}.
    r"[^\W\d_][^\W\d_'\u2019-]*(?:[-'\u2019][^\W\d_]+)*"
)
_AUTHOR_YEAR_RE = re.compile(
    rf"(?<![^\W\d_'\u2019-])"
    rf"(?P<authors>(?P<author>{_AUTHOR_SURNAME})"
    rf"(?:\s+(?:and|&)\s+{_AUTHOR_SURNAME})?"
    rf"(?:\s+et\s*al[.\uff0e]?)?)\s*"
    rf"(?:(?P<inner_paren>[\(\uff08])\s*|,\s*|\s+)"
    rf"(?P<year>(?:18|19|20)[0-9]{{2}}[a-z]?)"
    rf"(?(inner_paren)\s*[\)\uff09])"
)
_ADDITIONAL_AUTHOR_YEAR_RE = re.compile(r"\s*,\s*(?P<year>(?:18|19|20)[0-9]{2}[a-z]?)")
_COMPACT_ET_AL_YEAR_RE = re.compile(
    r"(?<![A-Za-z'\u2019-])(?P<author>[A-Z][A-Za-z'\u2019-]{1,}?)"
    r"et\s*al[.\uff0e]?\s*[\(\uff08](?P<year>(?:18|19|20)[0-9]{2}[a-z]?)"
    r"[\)\uff09]"
)
_COMPACT_CUE_AUTHOR_YEAR_RE = re.compile(
    r"(?P<cue>by|from)(?P<author>[A-Z][A-Za-z'\u2019-]{1,})"
    r"[\(\uff08](?P<year>(?:18|19|20)[0-9]{2}[a-z]?)[\)\uff09]"
)
# 张三等（2020）, 张三和李四(2019): the name run is resolved against the
# bibliography by trying its 4-, 3- and 2-character suffixes.
_CJK_AUTHOR_YEAR_RE = re.compile(
    r"(?P<author>[\u4e00-\u9fff]{2,4})"
    r"(?P<etal>等人?|(?:和|与)[\u4e00-\u9fff]{2,4})?\s*"
    r"[\(\uff08]\s*(?P<year>(?:18|19|20)[0-9]{2}[a-z]?)\s*[\)\uff09]"
)
# Capitalized words that open "In 2020", "The 2019 outbreak", "Since 2015",
# "March 2021" and similar phrases; they are never a cited surname.
_NON_AUTHOR_WORDS = frozenset(
    """
    a about above according across after again against all also although among
    an and another any approximately are around as at august autumn be because
    before being below between both but by ca census century circa compared
    december during each early earlier either every except fall february fig
    figure figures following for from further given here however if in into it
    its january july june late later like many march may mid middle more most
    my no nor not november october of on once only or other our over per phase
    present previous prior recent recently section september since so some
    spring stage such summer table tables than that the their them then there
    these they this those through throughout thus to under unlike until up upon
    version was we were what when where whereas which while who winter with
    within without year years yet
    """.split()  # noqa: SIM905 - a word block reads better than a 120-item list
)
_DOI_RE = re.compile(r"\b10\.[0-9]{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
_YEAR_RE = re.compile(r"(?<![0-9])(?:18|19|20)[0-9]{2}(?![0-9])")
_OCR_YEAR_RE = re.compile(r"(?<![0-9A-Za-z])(?:18|19|20|2O)[0-9]{2}(?![0-9A-Za-z])")
_TRAILING_DOI_PUNCTUATION = ".,;:)]}>"
_SENTENCE_BOUNDARY_RE = re.compile(
    r"(?:(?<=[.!?])(?:[\"'’”)]*)\s+(?=[A-Z㐀-鿿])|"
    r"(?<=[。！？])(?=[A-Z㐀-鿿]))"
)
# A period after these is not a sentence end ("e.g. Smith", "Fig. 3").
_NON_TERMINAL_ABBREVIATION_RE = re.compile(
    r"(?:\b(?:e\.g|i\.e|et\s*al|cf|vs|viz|approx|ca|fig|figs|eq|eqs|ref|refs|"
    r"no|nos|vol|pp|ch|sec|resp|dr|prof|mr|mrs|ms|st|jr|sr|inc|ltd|co)"
    r"|\b[A-Z])\.[\"'’”)]*\s*$",
    re.IGNORECASE,
)


def _sentence_boundaries(text: str, start: int = 0) -> list[int]:
    """Sentence-start offsets, ignoring periods of common abbreviations."""

    output: list[int] = []
    for match in _SENTENCE_BOUNDARY_RE.finditer(text, start):
        preceding = text[max(0, match.start() - 12) : match.end()]
        if _NON_TERMINAL_ABBREVIATION_RE.search(preceding):
            continue
        output.append(match.end())
    return output

_FUNCTION_CUES: tuple[tuple[CitationFunctionCandidate, re.Pattern[str]], ...] = (
    (
        "replication",
        re.compile(
            r"\b(?:replicat(?:e|ed|es|ing|ion)|reproduc(?:e|ed|es|ing|ibility)|"
            r"re-?analy(?:s|z)(?:e|ed|es|ing)|failed\s+to\s+(?:replicate|reproduce)|"
            r"(?:failed|unable)\s+to\s+confirm|"
            r"(?:could|can|did)\s+not\s+confirm|"
            r"independent(?:ly)?\s+(?:confirm|verify|replicate|reproduce))\b|"
            r"复现|重复实验|重新分析|未能复现|无法复现",
            re.IGNORECASE,
        ),
    ),
    (
        "critique",
        re.compile(
            r"\b(?:contradict(?:s|ed|ory)?|inconsistent\s+with|contrary\s+to|"
            r"dispute(?:s|d)?|challenge(?:s|d)?|question(?:s|ed)?|criticiz(?:e|ed|es)|"
            r"not\s+support(?:ed)?|overstat(?:e|ed|es)|conflict(?:s|ed)?\s+with)\b|"
            r"\b(?:fail(?:s|ed)?\s+to\s+"
            r"(?:generate|predict|explain|model|capture|account|handle|achieve|reach)|"
            r"(?:can(?:not|'t)|could\s+not|does\s+not|do\s+not|did\s+not)\s+"
            r"(?:adequately\s+)?(?:model|account\s+for|work|correlate|perform|reach|"
            r"handle|support|explain|capture|generate)|"
            r"(?:can(?:not|'t)|could\s+not)\s+be\s+applied|"
            r"(?:is|are|was|were)\s+(?:in\s+error|ineffective|invalid|limited\s+to)|"
            r"underperform(?:s|ed|ing)?|falls?\s+(?:far\s+)?behind|"
            r"though\s+see\s+also)\b|"
            r"质疑|反驳|矛盾|不一致|不支持|高估|夸大",
            re.IGNORECASE,
        ),
    ),
    (
        "result_comparison",
        re.compile(
            r"\b(?:consistent\s+with|in\s+(?:good\s+)?agreement\s+with|"
            r"agree(?:s|d)?\s+with|confirm(?:s|ed|ing)?|corroborat(?:e|ed|es|ing)|"
            r"support(?:s|ed|ing)?|align(?:s|ed)?\s+with|"
            r"(?:results?|findings?|measurements?|observations?|estimates?)\s+"
            r"(?:(?:is|are|was|were)\s+)?similar\s+to|"
            r"similar\s+to\s+(?:(?:prior|previous|published)\s+)?"
            r"(?:results?|findings?|measurements?|observations?|estimates?)|"
            r"comparable\s+to|as\s+(?:also\s+)?(?:found|reported|shown|demonstrated)\s+by)\b|"
            r"\b(?:(?:has|have)\s+similarly\s+been\s+described\s+as|"
            r"not\s+as\s+high\s+as\s+has\s+been\s+observed)\b|"
            r"\b(?:opposite\s+results?|outperform(?:s|ed|ing)?|"
            r"underperform(?:s|ed|ing)?|perform(?:s|ed|ing)?\s+"
            r"(?:better|worse)\s+than)\b|"
            r"(?:results?|findings?|conclusions?|estimates?)\s+"
            r"(?:(?:is|are|was|were|remain(?:s)?|appear(?:s)?(?:\s+to\s+be)?)\s+)?"
            r"(?:correct|accurate|valid|robust)\b|"
            r"\bvalidat(?:e|ed|es|ing)\s+(?:the\s+)?"
            r"(?:results?|findings?|conclusions?|estimates?)\b|"
            r"一致|吻合|支持|证实|验证|相同结果|类似结果|"
            r"(?:结论|结果|发现|估计)(?:是|仍然|被证明为)?(?:正确|准确|可靠)",
            re.IGNORECASE,
        ),
    ),
)


class CitationReferenceSource(StrictModel):
    id: Identifier
    ordinal: Annotated[int, Field(ge=1, le=100_000)]
    label: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
    ]
    raw_text: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=32_000),
    ]
    doi: NormalizedDoi | None
    year: Annotated[int, Field(ge=1800, le=2199)] | None
    source_block_id: Identifier
    page: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def web_text_bounds(self) -> CitationReferenceSource:
        if _utf16_length(self.label) > 100 or _utf16_length(self.raw_text) > 32_000:
            raise ValueError("citation reference text exceeds Web UTF-16 bounds")
        return self


class CitationMentionSource(StrictModel):
    id: Identifier
    reference_id: Identifier
    marker: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
    ]
    exact_quote: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=32_000),
    ]
    context_text: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=64_000),
    ]
    source_block_id: Identifier
    context_block_ids: Annotated[list[Identifier], Field(min_length=1, max_length=3)]
    page: Annotated[int, Field(ge=1)]
    bbox: BoundingBox | None
    char_start: Annotated[int, Field(ge=0)]
    char_end: Annotated[int, Field(gt=0)]
    group_size: Annotated[int, Field(ge=1, le=100)]
    candidate_functions: Annotated[list[CitationFunctionCandidate], Field(max_length=3)]

    @model_validator(mode="after")
    def valid_offsets(self) -> CitationMentionSource:
        if (
            _utf16_length(self.marker) > 200
            or _utf16_length(self.exact_quote) > 32_000
            or _utf16_length(self.context_text) > 64_000
        ):
            raise ValueError("citation mention text exceeds Web UTF-16 bounds")
        if (
            self.char_end > _utf16_length(self.exact_quote)
            or self.char_start >= self.char_end
        ):
            raise ValueError("citation marker offsets must fit the exact quote")
        selected_marker = _utf16_slice(
            self.exact_quote,
            self.char_start,
            self.char_end,
        )
        if selected_marker != self.marker:
            raise ValueError("citation marker offsets must select marker verbatim")
        if len(self.candidate_functions) != len(set(self.candidate_functions)):
            raise ValueError("citation candidate functions must be unique")
        if len(self.context_block_ids) != len(set(self.context_block_ids)):
            raise ValueError("citation context block IDs must be unique")
        if self.source_block_id not in self.context_block_ids:
            raise ValueError("citation context must contain its source block")
        return self


class CitationSourceProjection(StrictModel):
    schema_version: Literal["citation-source/1.0"] = CITATION_SOURCE_SCHEMA_VERSION
    extractor_version: Literal["citation-source-extractor/1.0"] = (
        CITATION_SOURCE_EXTRACTOR_VERSION
    )
    artifact_version_id: Identifier
    references: list[CitationReferenceSource]
    mentions: list[CitationMentionSource]
    warnings: list[
        Annotated[
            str,
            StringConstraints(
                strip_whitespace=True,
                min_length=1,
                max_length=1_000,
            ),
        ]
    ]

    @model_validator(mode="after")
    def references_are_closed(self) -> CitationSourceProjection:
        reference_ids = [item.id for item in self.references]
        mention_ids = [item.id for item in self.mentions]
        if len(reference_ids) != len(set(reference_ids)):
            raise ValueError("citation reference IDs must be unique")
        if len(mention_ids) != len(set(mention_ids)):
            raise ValueError("citation mention IDs must be unique")
        unknown = {item.reference_id for item in self.mentions} - set(reference_ids)
        if unknown:
            raise ValueError("citation mentions reference unknown bibliography entries")
        if [item.ordinal for item in self.references] != sorted(
            item.ordinal for item in self.references
        ):
            raise ValueError("citation references must be ordered")
        if len(self.warnings) != len(set(self.warnings)):
            raise ValueError("citation source warnings must be unique")
        if any(_utf16_length(item) > 1_000 for item in self.warnings):
            raise ValueError("citation source warning exceeds Web UTF-16 bounds")
        return self


def build_citation_source(document: DocumentIR) -> CitationSourceProjection:
    """Build a conservative source index for the first Citation Reception slice.

    Every retained mention has an exact marker-to-reference link. Explicit
    result-comparison, replication, or critique cues are recorded as candidate
    functions; background and method-only mentions remain source facts with an
    empty candidate-functions list and are excluded from Reception analysis.
    """

    blocks = sorted(
        (block for page in document.pages for block in page.blocks),
        key=lambda item: item.source_index,
    )
    bibliography_start = _bibliography_start(blocks)
    references, supplied_label_ids = _references(document, blocks, bibliography_start)
    references_by_number, ambiguous_numeric_labels = _references_by_number(
        references, supplied_label_ids
    )
    body = _body_blocks(blocks, bibliography_start)
    mentions = _mentions(
        document,
        body,
        references,
        references_by_number,
    )
    warnings: list[str] = []
    if ambiguous_numeric_labels:
        warnings.append(
            f"{len(ambiguous_numeric_labels)} duplicate numeric bibliography "
            "labels were left unresolved"
        )
    if references and not mentions:
        warnings.append("no marker-resolved citation contexts were found")
    return CitationSourceProjection(
        artifact_version_id=document.artifact.version_id,
        references=references,
        mentions=mentions,
        warnings=warnings,
    )


def _compact(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split()).strip()


def _utf16_offsets(value: str) -> list[int]:
    offsets = [0]
    for character in value:
        offsets.append(offsets[-1] + (2 if ord(character) > 0xFFFF else 1))
    return offsets


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le", errors="surrogatepass")) // 2


def _utf16_slice(value: str, start: int, end: int) -> str | None:
    offsets = _utf16_offsets(value)
    start_index = bisect_left(offsets, start)
    end_index = bisect_left(offsets, end)
    if (
        start_index >= len(offsets)
        or end_index >= len(offsets)
        or offsets[start_index] != start
        or offsets[end_index] != end
    ):
        return None
    return value[start_index:end_index]


def _take_utf16(value: str, limit: int, *, from_end: bool = False) -> str:
    offsets = _utf16_offsets(value)
    total = offsets[-1]
    if total <= limit:
        return value
    if from_end:
        index = bisect_left(offsets, total - limit)
        return value[index:]
    index = bisect_right(offsets, limit) - 1
    return value[:index]


def _heading(value: str | None) -> str:
    return _HEADING_NUMBER_RE.sub("", _compact(value or ""), count=1).rstrip(":.：")


def _heading_key(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = _HEADING_NUMBER_RE.sub("", text, count=1)
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", text.casefold())


def _is_reference_heading(value: str | None) -> bool:
    return _heading_key(value) in {
        "reference",
        "references",
        "bibliography",
        "literaturecited",
        "workcited",
        "workscited",
        "参考文献",
        "引用文献",
        "文献",
    }


def _source_text(block: DocumentBlock) -> str:
    if block.text:
        return block.text
    list_items = block.structured.get("list_items")
    if isinstance(list_items, list) and all(
        isinstance(item, str) for item in list_items
    ):
        return "\n".join(list_items)
    return ""


def _bibliography_start(blocks: list[DocumentBlock]) -> int | None:
    for index, block in enumerate(blocks):
        if _is_reference_heading(_source_text(block)):
            return index
    explicit = [index for index, block in enumerate(blocks) if block.kind == "citation"]
    return explicit[0] if explicit else None


def _body_blocks(
    blocks: list[DocumentBlock], bibliography_start: int | None
) -> list[DocumentBlock]:
    boundary = len(blocks) if bibliography_start is None else bibliography_start
    return [
        item
        for item in blocks[:boundary]
        if item.kind in {"paragraph", "list"} and _compact(_source_text(item))
    ]


def _reference_blocks(
    blocks: list[DocumentBlock], bibliography_start: int | None
) -> list[DocumentBlock]:
    explicit = [
        item for item in blocks if item.kind == "citation" and _source_text(item)
    ]
    if explicit:
        return explicit
    if bibliography_start is None:
        return []
    output: list[DocumentBlock] = []
    for item in blocks[bibliography_start + 1 :]:
        if item.kind == "title" and _POST_REFERENCE_HEADING_RE.fullmatch(
            _heading(item.text)
        ):
            break
        if item.kind in {"paragraph", "list", "other"} and _compact(_source_text(item)):
            output.append(item)
    return output


def _inline_reference_starts(text: str) -> list[re.Match[str]]:
    """Consecutive ``[1] … [2] …`` labels flattened onto one line."""

    matches = [
        match
        for match in _INLINE_REFERENCE_START_RE.finditer(text)
        if match.start() == 0 or text[match.start() - 1].isspace()
    ]
    sequence: list[re.Match[str]] = []
    for match in matches:
        number = int(match.group("bracket"))
        if not sequence:
            if match.start() == 0 or not _compact(text[: match.start()]):
                sequence.append(match)
            continue
        if number == int(sequence[-1].group("bracket")) + 1:
            sequence.append(match)
    return sequence if len(sequence) >= 2 else []


def _split_reference_text(text: str) -> Iterable[tuple[str | None, str]]:
    text = unicodedata.normalize("NFC", text).strip()
    starts = list(_REFERENCE_START_RE.finditer(text))
    if len(starts) <= 1:
        inline = _inline_reference_starts(text)
        if inline:
            starts = inline
    if not starts:
        displaced = re.search(
            r"(?<=[a-z\)])\.(?P<number>[0-9]{1,3})\.\s+(?=[A-Z][A-Za-z])",
            text,
        )
        if displaced is not None:
            repaired = (
                text[: displaced.start()] + ". " + text[displaced.end() :]
            ).strip()
            yield displaced.group("number"), repaired
            return
        if text:
            yield None, text
        return
    if starts[0].start() > 0 and _compact(text[: starts[0].start()]):
        yield None, text[: starts[0].start()]
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        groups = match.groupdict()
        label = groups.get("bracket") or groups.get("number")
        raw = text[match.end() : end].strip()
        if raw:
            yield label, raw


def _split_reference_block(block: DocumentBlock) -> Iterable[tuple[str | None, str]]:
    list_items = block.structured.get("list_items")
    if isinstance(list_items, list) and all(
        isinstance(item, str) for item in list_items
    ):
        for item in list_items:
            yield from _split_reference_text(item)
        return
    yield from _split_reference_text(_source_text(block))


def _reference_year(value: str) -> int | None:
    parenthetical = re.findall(
        r"[\(\uff08]\s*((?:18|19|20|2O)[0-9]{2})\s*[\)\uff09]",
        value,
    )
    if parenthetical:
        return int(parenthetical[-1].replace("O", "0"))
    matches = list(_YEAR_RE.finditer(value))
    if matches:
        return int(matches[0].group(0))
    ocr_matches = list(_OCR_YEAR_RE.finditer(value))
    if not ocr_matches:
        return None
    return int(ocr_matches[0].group(0).replace("O", "0"))


def _references(
    document: DocumentIR,
    blocks: list[DocumentBlock],
    bibliography_start: int | None,
) -> tuple[list[CitationReferenceSource], frozenset[str]]:
    """Reference entries plus the IDs whose numeric label the paper printed.

    An unnumbered (author-year) bibliography still gets display ordinals, but
    those synthetic numbers never resolve a numeric marker such as ``[1]``,
    ``km2`` or ``p53``.
    """

    output: list[CitationReferenceSource] = []
    supplied_ids: set[str] = set()
    entries = [
        (block, supplied_label, raw)
        for block in _reference_blocks(blocks, bibliography_start)
        for supplied_label, raw in _split_reference_block(block)
    ]
    reserved_labels = {int(label) for _, label, _ in entries if label is not None}
    used_ordinals: set[int] = set()
    next_ordinal = 1
    for block, supplied_label, raw in entries:
        supplied = int(supplied_label) if supplied_label is not None else None
        ordinal = (
            supplied
            if supplied is not None and supplied not in used_ordinals
            else next_ordinal
        )
        while (
            ordinal in used_ordinals
            or (supplied is None and ordinal in reserved_labels)
        ) and ordinal <= 100_000:
            ordinal += 1
        if ordinal > 100_000:
            continue
        used_ordinals.add(ordinal)
        next_ordinal = max(next_ordinal, ordinal + 1)
        normalized_raw = _compact(raw)
        if not normalized_raw:
            continue
        compact = _take_utf16(normalized_raw, 32_000)
        doi = _reference_doi(compact)
        year = _reference_year(compact)
        digest = hashlib.sha256(
            f"{document.artifact.version_id}\0{block.id}\0{ordinal}\0"
            f"{normalized_raw}".encode()
        ).hexdigest()
        reference_id = f"citation-reference:sha256:{digest}"
        if supplied is not None:
            supplied_ids.add(reference_id)
        output.append(
            CitationReferenceSource(
                id=reference_id,
                ordinal=ordinal,
                label=str(supplied if supplied is not None else ordinal),
                raw_text=compact,
                doi=doi,
                year=year,
                source_block_id=block.id,
                page=block.page,
            )
        )
    output.sort(key=lambda item: (item.ordinal, item.id))
    return output, frozenset(supplied_ids)


def _reference_doi(text: str) -> str | None:
    """Extract one DOI, rejoining a DOI broken across lines.

    PDFs break long DOIs after ``/``, ``.`` or ``-`` and the line break becomes
    a space; the continuation is rejoined instead of truncating the DOI.
    """

    search = re.sub(r"(10\.[0-9]{4,9}/)\s+", r"\1", text, flags=re.IGNORECASE)
    match = _DOI_RE.search(search)
    if match is None:
        return None
    doi = match.group(0)
    cursor = match.end()
    while doi[-1:] in {"/", ".", "-", "_"}:
        continuation = re.match(r"\s+([-._;()/:A-Z0-9]+)", search[cursor:], re.I)
        if continuation is None:
            break
        doi += continuation.group(1)
        cursor += continuation.end()
    return doi.rstrip(_TRAILING_DOI_PUNCTUATION).lower()


def _candidate_functions(context: str) -> list[CitationFunctionCandidate]:
    return [name for name, pattern in _FUNCTION_CUES if pattern.search(context)]


def _references_by_number(
    references: list[CitationReferenceSource],
    supplied_label_ids: frozenset[str] | None = None,
) -> tuple[dict[int, CitationReferenceSource], frozenset[int]]:
    output: dict[int, CitationReferenceSource] = {}
    ambiguous: set[int] = set()
    for reference in references:
        if not reference.label.isdigit():
            continue
        if supplied_label_ids is not None and reference.id not in supplied_label_ids:
            continue
        label = int(reference.label)
        if label in output:
            output.pop(label)
            ambiguous.add(label)
        elif label not in ambiguous:
            output[label] = reference
    return output, frozenset(ambiguous)


def _numeric_targets(body: str) -> list[int]:
    values: list[int] = []
    tokens = re.split(r"\s*[,;]\s*", body)
    for token in tokens:
        range_match = re.fullmatch(
            r"([0-9]{1,5})\s*[-\u2013\u2014]\s*([0-9]{1,5})",
            token,
        )
        if range_match:
            start, end = map(int, range_match.groups())
            if start <= end and end - start < 100:
                values.extend(range(start, end + 1))
            continue
        if token.isdigit():
            values.append(int(token))
    return list(dict.fromkeys(values))[:100]


def _plausible_flattened_superscript(quote: str, start: int) -> bool:
    """A glued digit run cites only after a real word, not a unit or symbol."""

    previous = quote[start - 1] if start else ""
    if "\u3400" <= previous <= "\u9fff":
        return previous not in _CJK_NUMBER_PREFIXES
    word = _FLATTENED_WORD_RE.search(quote[:start])
    if word is None:
        return False
    letters = word.group(1)
    if len(letters) < 4 or letters.casefold() in _UNIT_WORDS:
        return False
    # Gene/protein symbols and mixed-case tokens (Sox2, H3K4me3, CaCO3).
    if any(character.isupper() for character in letters[1:]):
        return False
    word_start = word.start(1)
    return not (word_start and quote[word_start - 1].isdigit())


def _numeric_candidates(
    quote: str,
    references_by_number: dict[int, CitationReferenceSource],
) -> list[tuple[int, int, str, list[CitationReferenceSource], int]]:
    """Resolve bracketed and conservatively flattened numeric citations."""

    output: list[tuple[int, int, str, list[CitationReferenceSource], int]] = []
    seen_spans: set[tuple[int, int]] = set()
    patterns = (
        (_NUMERIC_MARKER_RE, False),
        (_MARKUP_SUPERSCRIPT_RE, False),
        (_UNICODE_SUPERSCRIPT_RE, True),
        (_FLATTENED_SUPERSCRIPT_RE, True),
        (_CITATION_CUE_NUMBER_RE, True),
    )
    for pattern, require_complete in patterns:
        for marker in pattern.finditer(quote):
            body_start, body_end = marker.span("body")
            if (body_start, body_end) in seen_spans:
                continue
            if pattern is _FLATTENED_SUPERSCRIPT_RE and not (
                _plausible_flattened_superscript(quote, body_start)
            ):
                continue
            if pattern is _UNICODE_SUPERSCRIPT_RE and not (
                _plausible_flattened_superscript(quote, body_start)
                or quote[body_start - 1 : body_start] in {".", ",", ";", ")", "]"}
            ):
                continue
            numbers = _numeric_targets(
                marker.group("body").translate(_SUPERSCRIPT_DIGITS)
            )
            if not numbers:
                continue
            targets = [
                references_by_number[number]
                for number in numbers
                if number in references_by_number
            ]
            if not targets or (require_complete and len(targets) != len(numbers)):
                continue
            seen_spans.add((body_start, body_end))
            output.append(
                (
                    body_start if require_complete else marker.start(),
                    body_end if require_complete else marker.end(),
                    marker.group("body") if require_complete else marker.group(0),
                    targets,
                    len(numbers),
                )
            )
    return output


def _quote_window(
    quote: str,
    marker_start: int,
    marker_end: int,
    *,
    limit: int,
) -> tuple[str, int, int]:
    offsets = _utf16_offsets(quote)
    total_units = offsets[-1]
    if total_units <= limit:
        return quote, marker_start, marker_end
    marker_start_units = offsets[marker_start]
    marker_end_units = offsets[marker_end]
    marker_units = marker_end_units - marker_start_units
    available = limit - marker_units
    desired_left_units = max(0, marker_start_units - available // 2)
    left = bisect_left(offsets, desired_left_units)
    right = bisect_right(offsets, offsets[left] + limit) - 1
    if right == len(quote) and offsets[right] - offsets[left] < limit:
        left = bisect_left(offsets, max(0, total_units - limit))
        right = bisect_right(offsets, offsets[left] + limit) - 1
    window = quote[left:right]
    leading_whitespace = len(window) - len(window.lstrip())
    window = window.strip()
    return (
        window,
        marker_start - left - leading_whitespace,
        marker_end - left - leading_whitespace,
    )


def _citation_quote(
    quote: str,
    marker_start: int,
    marker_end: int,
) -> tuple[str, int, int]:
    """Return the sentence containing a marker with marker-relative offsets.

    MinerU commonly represents a whole multi-sentence paragraph as one block.
    Keeping that entire block makes a citation's supposedly exact quote too
    broad for provenance display and comparison. Sentence boundaries are only
    accepted when the next token looks like a new sentence, which avoids
    splitting common citation forms such as ``et al. (2020)``.
    """

    boundaries = [0, *_sentence_boundaries(quote)]
    left = max((value for value in boundaries if value <= marker_start), default=0)
    right = next((value for value in boundaries if value >= marker_end), len(quote))
    sentence = quote[left:right]
    leading_whitespace = len(sentence) - len(sentence.lstrip())
    sentence = sentence.strip()
    local_start = marker_start - left - leading_whitespace
    local_end = marker_end - left - leading_whitespace
    return _quote_window(
        sentence,
        local_start,
        local_end,
        limit=8_000,
    )


def _context(
    body: list[DocumentBlock],
    index: int,
    marker_start: int,
    marker_end: int,
) -> tuple[list[Identifier], str]:
    selected = body[max(0, index - 1) : min(len(body), index + 2)]
    parts: list[str] = []
    for item in selected:
        text = unicodedata.normalize("NFC", _source_text(item)).strip()
        if item.id == body[index].id:
            text, _, _ = _quote_window(
                text,
                marker_start,
                marker_end,
                limit=48_000,
            )
        elif item.source_index < body[index].source_index:
            text = _take_utf16(text, 7_900, from_end=True)
        else:
            text = _take_utf16(text, 7_900)
        parts.append(_compact(text))
    return [item.id for item in selected], "\n\n".join(parts)


def _fold_letters(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )


def _leading_author_matches(raw_text: str, author: str) -> bool:
    """The cited surname is the first author of the reference entry.

    Up to three leading initials are allowed ("J. Smith, ..."). Spacing and
    diacritics are ignored (DeCarlo / De Carlo, Muller / Müller), and the
    surname must end at a word boundary or before a compacted initial
    ("KatoY"), so "Li" never selects "Liu".
    """

    letters = [character for character in _fold_letters(author) if character.isalnum()]
    if not letters:
        return False
    pattern = (
        r"^\W*(?:[^\W\d_]\.\s*){0,3}"
        + r"[\s'\u2019-]*".join(re.escape(character) for character in letters)
    )
    folded = _fold_letters(raw_text)
    match = re.match(pattern, folded, re.IGNORECASE)
    if match is None:
        return False
    following = folded[match.end() : match.end() + 1]
    return not (following.isalpha() and following.islower())


def _author_year_reference(
    references: list[CitationReferenceSource],
    author: str,
    year: int,
    marker_text: str,
) -> CitationReferenceSource | None:
    matching = [
        item
        for item in references
        if item.year == year and _leading_author_matches(item.raw_text, author)
    ]
    if len(matching) == 1:
        return matching[0]
    if "et al" in marker_text.casefold():
        comma_counts = {
            item.id: item.raw_text.split(str(year), 1)[0].count(",")
            for item in matching
        }
        highest = max(comma_counts.values(), default=0)
        multi_author = [item for item in matching if comma_counts[item.id] == highest]
        if len(multi_author) == 1 and highest >= 2:
            return multi_author[0]
    return None


def _cited_surname(author_group: str) -> str | None:
    """Return the surname of an author-year marker, or ``None`` for a phrase."""

    words = author_group.split()
    surname = words[-1] if words else ""
    if not surname or not surname[0].isupper():
        return None
    if _fold_letters(surname).casefold().strip("'\u2019-") in _NON_AUTHOR_WORDS:
        return None
    return author_group


def _parenthetical_spans(value: str) -> list[tuple[int, int]]:
    stack: list[int] = []
    output: list[tuple[int, int]] = []
    for index, character in enumerate(value):
        if character == "(":
            stack.append(index)
        elif character == ")" and stack:
            output.append((stack.pop(), index + 1))
    return output


def _enclosing_parenthetical(
    spans: list[tuple[int, int]], start: int, end: int
) -> tuple[int, int] | None:
    matching = [item for item in spans if item[0] < start and end < item[1]]
    return min(matching, key=lambda item: item[1] - item[0]) if matching else None


def _author_year_occurrences(
    quote: str,
) -> list[tuple[int, int, str, str, int, bool]]:
    """Return exact author-year spans, including abbreviated repeated years.

    The last element says whether the year itself is parenthesized; the
    caller also accepts markers inside a parenthetical citation group or with
    ``et al.``. Capitalized phrase openers ("In 2020") are never surnames.
    """

    output: list[tuple[int, int, str, str, int, bool]] = []
    for marker in _AUTHOR_YEAR_RE.finditer(quote):
        author = _cited_surname(marker.group("author"))
        if author is None:
            continue
        parenthesized = marker.group("inner_paren") is not None
        output.append(
            (
                marker.start(),
                marker.end(),
                marker.group(0),
                author,
                int(marker.group("year")[:4]),
                parenthesized,
            )
        )
        if parenthesized:
            continue
        cursor = marker.end()
        while additional := _ADDITIONAL_AUTHOR_YEAR_RE.match(quote, cursor):
            year_start, year_end = additional.span("year")
            output.append(
                (
                    year_start,
                    year_end,
                    additional.group("year"),
                    author,
                    int(additional.group("year")[:4]),
                    False,
                )
            )
            cursor = additional.end()
    existing_spans = {(item[0], item[1]) for item in output}
    for pattern in (_COMPACT_ET_AL_YEAR_RE, _COMPACT_CUE_AUTHOR_YEAR_RE):
        for marker in pattern.finditer(quote):
            start = marker.start("author")
            end = marker.end()
            if (start, end) in existing_spans:
                continue
            output.append(
                (
                    start,
                    end,
                    quote[start:end],
                    marker.group("author"),
                    int(marker.group("year")[:4]),
                    True,
                )
            )
            existing_spans.add((start, end))
    output.sort(key=lambda item: (item[0], item[1]))
    return output


def _cjk_author_year_candidates(
    quote: str, references: list[CitationReferenceSource]
) -> list[tuple[int, int, str, CitationReferenceSource]]:
    output: list[tuple[int, int, str, CitationReferenceSource]] = []
    for marker in _CJK_AUTHOR_YEAR_RE.finditer(quote):
        run = marker.group("author")
        year = int(marker.group("year")[:4])
        for length in range(len(run), 1, -1):
            surname = run[-length:]
            matches = [
                item
                for item in references
                if item.year == year and _leading_author_matches(item.raw_text, surname)
            ]
            if len(matches) == 1:
                start = marker.end("author") - length
                output.append(
                    (start, marker.end(), quote[start : marker.end()], matches[0])
                )
                break
    return output


def _mentions(
    document: DocumentIR,
    body: list[DocumentBlock],
    references: list[CitationReferenceSource],
    references_by_number: dict[int, CitationReferenceSource],
) -> list[CitationMentionSource]:
    output: list[CitationMentionSource] = []
    seen: set[tuple[str, str, int, int]] = set()
    author_year_cache: dict[tuple[str, int, bool], CitationReferenceSource | None] = {}
    for index, block in enumerate(body):
        quote = unicodedata.normalize("NFC", _source_text(block)).strip()
        candidates: list[tuple[int, int, str, list[CitationReferenceSource], int]] = []
        candidates.extend(_numeric_candidates(quote, references_by_number))
        author_year_occurrences = _author_year_occurrences(quote)
        parenthetical_spans = _parenthetical_spans(quote)
        group_counts: dict[tuple[int, int] | None, int] = {}
        for start, end, *_ in author_year_occurrences:
            group = _enclosing_parenthetical(parenthetical_spans, start, end)
            if group is not None:
                group_counts[group] = group_counts.get(group, 0) + 1
        for (
            start,
            end,
            marker_text,
            author,
            year,
            parenthesized,
        ) in author_year_occurrences:
            group = _enclosing_parenthetical(parenthetical_spans, start, end)
            if not (
                parenthesized
                or group is not None
                or "et al" in marker_text.casefold()
            ):
                # "Smith 2020" in running text without any citation context.
                continue
            author_year_key = (
                unicodedata.normalize("NFKC", author).casefold(),
                year,
                "et al" in marker_text.casefold(),
            )
            if author_year_key not in author_year_cache:
                author_year_cache[author_year_key] = _author_year_reference(
                    references,
                    author,
                    year,
                    marker_text,
                )
            target = author_year_cache[author_year_key]
            if target is not None:
                candidates.append(
                    (
                        start,
                        end,
                        marker_text,
                        [target],
                        1 if group is None else group_counts[group],
                    )
                )

        for start, end, marker_text, target in _cjk_author_year_candidates(
            quote, references
        ):
            candidates.append((start, end, marker_text, [target], 1))
        candidates.sort(
            key=lambda item: (
                item[0],
                item[1],
                tuple(target.id for target in item[3]),
            )
        )
        for marker_start, marker_end, marker_text, targets, group_size in candidates:
            exact_quote, char_start, char_end = _citation_quote(
                quote,
                marker_start,
                marker_end,
            )
            functions = _candidate_functions(exact_quote)
            if not functions:
                next_starts = _sentence_boundaries(quote, marker_end)
                if next_starts:
                    continuation = quote[next_starts[0] :]
                    if re.match(
                        r"(?:however|but|yet|nevertheless|nonetheless)\b",
                        continuation,
                        re.IGNORECASE,
                    ):
                        following = _sentence_boundaries(continuation)
                        if following:
                            continuation = continuation[: following[0]]
                        functions = _candidate_functions(
                            _take_utf16(continuation, 8_000)
                        )
            context_ids, context_text = _context(
                body,
                index,
                marker_start,
                marker_end,
            )
            utf16_char_start = _utf16_length(exact_quote[:char_start])
            utf16_char_end = _utf16_length(exact_quote[:char_end])
            for target in targets:
                key = (target.id, block.id, marker_start, marker_end)
                if key in seen:
                    continue
                seen.add(key)
                digest = hashlib.sha256(
                    f"{document.artifact.version_id}\0{target.id}\0{block.id}\0"
                    f"{marker_start}\0{marker_end}".encode()
                ).hexdigest()
                output.append(
                    CitationMentionSource(
                        id=f"citation-mention:sha256:{digest}",
                        reference_id=target.id,
                        marker=marker_text,
                        exact_quote=exact_quote,
                        context_text=context_text,
                        source_block_id=block.id,
                        context_block_ids=context_ids,
                        page=block.page,
                        bbox=block.bbox,
                        char_start=utf16_char_start,
                        char_end=utf16_char_end,
                        group_size=group_size,
                        candidate_functions=functions,
                    )
                )
    return output


__all__ = [
    "CITATION_SOURCE_EXTRACTOR_VERSION",
    "CITATION_SOURCE_SCHEMA_VERSION",
    "CitationMentionSource",
    "CitationReferenceSource",
    "CitationSourceProjection",
    "NormalizedDoi",
    "build_citation_source",
]
