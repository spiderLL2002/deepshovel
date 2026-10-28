"""Deterministic Unicode/CJK text preparation for lexical retrieval."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable

from .retrieval_contract import ClaimSearchDocument, SearchScope, SearchScopeFilter

_SPACE_RE = re.compile(r"\s+")
# Capital delta denotes a different quantity (Δ17O, capital-delta
# mass-independent anomaly) from lower-case delta (δ18O); casefold would merge
# them. U+2206 INCREMENT is the same symbol in many PDFs.
_DELTA_VARIANTS = str.maketrans({"\u2206": "\u0394"})
_CAPITAL_DELTA = "\u0394"
# Joined inside a number (0.7045, 1,234) so a decimal is one token, not "0"
# plus "7045"; "/" joins isotope or unit ratios (87sr/86sr) and also yields
# the parts.
_NUMBER_JOINERS = frozenset({".", ","})


def normalized_text(value: str) -> str:
    return _SPACE_RE.sub(" ", unicodedata.normalize("NFKC", value).casefold()).strip()


def constraint_text(value: str) -> str:
    """Match the public NFKC+lower contract used by the TypeScript gateway."""

    return _SPACE_RE.sub(" ", unicodedata.normalize("NFKC", value).lower()).strip()


def _is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x3040 <= codepoint <= 0x30FF
        or 0xAC00 <= codepoint <= 0xD7AF
    )


def lexical_text(value: str) -> str:
    """Tokenizer normalization: NFKC and casefold, keeping capital delta."""

    text = unicodedata.normalize("NFKC", value).translate(_DELTA_VARIANTS)
    if _CAPITAL_DELTA in text:
        folded = _CAPITAL_DELTA.join(
            part.casefold() for part in text.split(_CAPITAL_DELTA)
        )
    else:
        folded = text.casefold()
    return _SPACE_RE.sub(" ", folded).strip()


def _word_character(character: str) -> bool:
    return unicodedata.category(character)[0] in {"L", "N"} or character in {
        "_",
        "+",
        "-",
    }


def lexical_tokens(
    value: str,
    *,
    maximum: int = 512,
    deduplicate: bool = True,
    isolated_cjk_unigrams: bool = False,
) -> list[str]:
    """Return stable word tokens plus CJK unigrams and adjacent bigrams.

    SQLite's built-in Unicode tokenizer does not provide deterministic word
    segmentation for every CJK script.  Pre-tokenizing those runs keeps both
    indexing and querying independent of host locale or an optional extension.

    ``isolated_cjk_unigrams`` (query side) keeps a CJK unigram only when it is
    a one-character run such as 汞 or 硫; longer runs contribute bigrams,
    which already cover their characters.
    """

    text = lexical_text(value)
    tokens: list[str] = []
    word: list[str] = []
    cjk_run: list[str] = []
    limit = min(32_768, maximum * 8)

    def flush_word() -> None:
        if not word:
            return
        joined = "".join(word)
        word.clear()
        if "/" in joined:
            parts = [part for part in joined.split("/") if part]
            if len(parts) > 1:
                tokens.append("/".join(parts))
            tokens.extend(parts)
            return
        tokens.append(joined)

    def flush_cjk() -> None:
        if not cjk_run:
            return
        if not isolated_cjk_unigrams or len(cjk_run) == 1:
            tokens.extend(cjk_run)
        tokens.extend(
            left + right for left, right in zip(cjk_run, cjk_run[1:], strict=False)
        )
        cjk_run.clear()

    for index, character in enumerate(text):
        if _is_cjk(character):
            flush_word()
            cjk_run.append(character)
            continue
        flush_cjk()
        if _word_character(character) or (
            word
            and index + 1 < len(text)
            and (
                (
                    character in _NUMBER_JOINERS
                    and word[-1].isdigit()
                    and text[index + 1].isdigit()
                )
                or (character == "/" and _word_character(text[index + 1]))
            )
        ):
            word.append(character)
        else:
            flush_word()
        if len(tokens) >= limit:
            break
    flush_word()
    flush_cjk()

    output: list[str] = []
    counts: Counter[str] = Counter()
    for token in tokens:
        cleaned = token.strip("_+-")
        if not cleaned:
            continue
        if deduplicate and counts[cleaned]:
            continue
        # Cap pathological repetition while retaining real BM25 term frequency.
        if not deduplicate and counts[cleaned] >= 8:
            continue
        output.append(cleaned)
        counts[cleaned] += 1
        if len(output) >= maximum:
            break
    return output


def fts_query(values: Iterable[str]) -> str:
    collected: list[str] = []
    seen: set[str] = set()
    for value in values:
        # Characters of a multi-character CJK run are already covered by its
        # bigrams; retaining them turns particles such as “的 / 方 / 法” into
        # broad OR clauses. A one-character run (汞, 铁, 硫) is a word in its
        # own right and is kept.
        for token in lexical_tokens(value, maximum=128, isolated_cjk_unigrams=True):
            if token in seen:
                continue
            seen.add(token)
            collected.append(token)
            if len(collected) >= 256:
                break
        if len(collected) >= 256:
            break
    # Quoted tokens are FTS5 phrases: a decimal or ratio token ("0.7045",
    # "87sr/86sr") matches its parts adjacently instead of OR-ing a bare "0".
    tokens = ['"' + token.replace('"', '""') + '"' for token in collected]
    return " OR ".join(tokens)


def tokenized_field(values: Iterable[str]) -> str:
    return " ".join(lexical_tokens("\n".join(values), maximum=4_096, deduplicate=False))


_ENGLISH_NEGATION = re.compile(
    r"(?<![a-z])(?:not|never|cannot|no(?!\.\s*\d))(?![a-z])|n't(?![a-z])"
)
# Explicit negation of a predicate only. Descriptive compounds such as 不同
# (different), 无机碳 (inorganic carbon), 无氧 (anoxic) or 未来 (future) are
# affirmed statements and must never turn a query into a negated-Claim filter.
_CHINESE_NEGATION = re.compile(
    r"不是|不会|不能|不可能|不存在|不支持|不影响|不改变|不导致|不引起|不相关|"
    r"不显著|不一致|不受|不足以|不再|并不|并非|没有|未见|未发现|未观察到|"
    r"未检测到|未检出|未能|无法|无关|无明显|无显著"
)


def has_negation(value: str) -> bool:
    """Conservative explicit-negation test for a free-text query."""

    text = lexical_text(value)
    return bool(_ENGLISH_NEGATION.search(text) or _CHINESE_NEGATION.search(text))


def content_values(document: ClaimSearchDocument) -> list[str]:
    """Searchable content of one Claim document, without section headers."""

    values = [
        document.canonical_claim,
        document.original_claim or "",
        document.paper_title,
        document.evidence_context,
        *document.entities,
        *document.key_terms,
        *document.qualifiers,
        *scope_values(document.scope),
    ]
    for item in document.abbreviations:
        values.append(item.short_form)
        if item.long_form is not None:
            values.append(item.long_form)
    return values


def content_token_set(document: ClaimSearchDocument) -> set[str]:
    return set(
        lexical_tokens("\n".join(content_values(document)), maximum=8_192)
    )


def term_present(term: str, tokens: set[str]) -> bool:
    """Token-level containment shared with the FTS ``AND`` clause semantics.

    ``Ca`` does not match ``can``, excluding ``rat`` keeps ``ratio``, and a
    section header is never content.
    """

    term_tokens = lexical_tokens(term, maximum=128)
    return bool(term_tokens) and all(token in tokens for token in term_tokens)


# Year factors for scientific time units; one table for indexing, filtering and
# post-filtering. Keys are lower-case with spaces removed.
TIME_UNIT_FACTORS: dict[str, float] = {
    "a": 1.0,
    "y": 1.0,
    "yr": 1.0,
    "yrs": 1.0,
    "year": 1.0,
    "years": 1.0,
    "年": 1.0,
    "bp": 1.0,
    "yrbp": 1.0,
    "yearsbp": 1.0,
    "calbp": 1.0,
    "calyrbp": 1.0,
    "百年": 100.0,
    "ka": 1_000.0,
    "kyr": 1_000.0,
    "kyrs": 1_000.0,
    "kabp": 1_000.0,
    "calkabp": 1_000.0,
    "kyrbp": 1_000.0,
    "千年": 1_000.0,
    "thousandyears": 1_000.0,
    "万年": 10_000.0,
    "ma": 1_000_000.0,
    "myr": 1_000_000.0,
    "myrs": 1_000_000.0,
    "百万年": 1_000_000.0,
    "millionyears": 1_000_000.0,
    "亿年": 100_000_000.0,
    "ga": 1_000_000_000.0,
    "gyr": 1_000_000_000.0,
    "gyrs": 1_000_000_000.0,
    "十亿年": 1_000_000_000.0,
    "billionyears": 1_000_000_000.0,
}


def time_unit_factor(unit: str | None) -> float | None:
    key = constraint_text(unit or "").replace(" ", "").replace(".", "")
    return TIME_UNIT_FACTORS.get(key)


def time_interval_years(
    start: float | int | None, end: float | int | None, unit: str | None
) -> tuple[float, float] | None:
    """Ordered interval in years, or ``None`` without bounds or a known unit.

    Geological ages are often written old-to-young ("66–56 Ma"); the interval
    is the same either way, so the bounds are ordered.
    """

    if start is None and end is None:
        return None
    factor = time_unit_factor(unit)
    if factor is None:
        return None
    low = -math.inf if start is None else float(start) * factor
    high = math.inf if end is None else float(end) * factor
    if start is not None and end is not None and low > high:
        low, high = high, low
    return low, high


def scope_values(scope: SearchScope | SearchScopeFilter) -> list[str]:
    values: list[str] = []
    for name in (
        "location",
        "formation",
        "sample_type",
        "method",
        "condition",
        "measurement",
        "entities",
    ):
        current = getattr(scope, name)
        if current:
            values.extend(current)
    if scope.time is not None:
        if scope.time.label:
            values.append(scope.time.label)
        if scope.time.start is not None:
            values.append(str(scope.time.start))
        if scope.time.end is not None:
            values.append(str(scope.time.end))
        if scope.time.unit:
            values.append(scope.time.unit)
    if scope.uncertainty:
        values.append(scope.uncertainty)
    return values


def dense_document_text(document: ClaimSearchDocument) -> str:
    abbreviations = [
        item.short_form
        if item.long_form is None
        else f"{item.short_form} = {item.long_form}"
        for item in document.abbreviations
    ]
    sections = [
        "[claim]",
        document.canonical_claim,
        "[original]",
        document.original_claim or "",
        "[paper]",
        document.paper_title,
        "[entities and terms]",
        "; ".join(document.entities + document.key_terms + abbreviations),
        "[qualifiers and scope]",
        "; ".join(document.qualifiers + scope_values(document.scope)),
        "[evidence context]",
        document.evidence_context,
    ]
    return "\n".join(sections)[:32_000]


def lexical_fields(document: ClaimSearchDocument) -> tuple[str, ...]:
    abbreviations = [item.short_form for item in document.abbreviations]
    abbreviations.extend(
        item.long_form for item in document.abbreviations if item.long_form is not None
    )
    return (
        tokenized_field(document.entities),
        tokenized_field(document.key_terms),
        tokenized_field(abbreviations),
        tokenized_field([document.original_claim or ""]),
        tokenized_field([document.canonical_claim]),
        tokenized_field([document.paper_title]),
        tokenized_field([document.evidence_context]),
        tokenized_field(document.qualifiers),
        tokenized_field(scope_values(document.scope)),
    )


def token_overlap(left: str, right: str) -> float:
    left_tokens = set(lexical_tokens(left, maximum=512))
    right_tokens = set(lexical_tokens(right, maximum=512))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens.intersection(right_tokens)) / len(
        left_tokens.union(right_tokens)
    )
