"""Conservative, exact keys for numeric scientific mentions.

The extractor deliberately keeps decimal text out of binary floating-point
arithmetic.  A key binds the exact normalized decimal to the comparison
direction and to a recognized adjacent unit::

    mention:dec:5e+0|cmp:gt|unit:mg/kg

No unit conversion is performed.  Unknown attached alphabetic labels are not
treated as units, and navigation labels such as ``Figure 5`` are masked before
scientific mentions are collected.
Consecutive prose-list markers are likewise layout, not measured quantities;
ambiguous numeric lines and explicit math/code contexts retain their numbers.
"""

from __future__ import annotations

import math
import re
import string
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from . import number_normalization as _numbers

_REFERENCE_ATOM = r"[Ss]?[0-9]+(?:\.[0-9]+)*[A-Za-z]?"
_REFERENCE_ITEM = (
    rf"(?:{_REFERENCE_ATOM}(?:\s*\([A-Za-z0-9]+\))?"
    rf"|\(\s*{_REFERENCE_ATOM}\s*\))"
)
_REFERENCE_LIST = (
    rf"{_REFERENCE_ITEM}(?:\s*(?:[,;、]|and|or|to|through|[-–—至和及])"
    rf"\s*{_REFERENCE_ITEM})*"
)
_REFERENCE_NUMBER_RE = re.compile(
    rf"(?:"
    rf"(?:图|表|公式|方程|章节?|节)\s*{_REFERENCE_LIST}"
    rf"|第\s*{_REFERENCE_LIST}\s*(?:页|章节?|节|公式|方程)"
    rf")",
    re.IGNORECASE,
)
_EN_REFERENCE_NUMBER_RE = re.compile(
    rf"(?<![A-Za-z])(?:fig(?:ure)?s?|tables?|pages?|pp?\.?|"
    rf"eq(?:uation)?s?\.?|sec(?:tion)?s?\.?|references?|refs?\.?|"
    rf"citations?)\s*{_REFERENCE_LIST}",
    re.IGNORECASE,
)
_SECTION_SYMBOL_RE = re.compile(rf"§+\s*{_REFERENCE_LIST}", re.IGNORECASE)
_BRACKET_CITATION_RE = re.compile(r"\[\s*[0-9]+(?:\s*(?:[,;]|–|—|-)\s*[0-9]+)*\s*\]")
_AUTHOR_YEAR_RE = re.compile(
    r"\b[A-Z][A-Za-z'’-]{1,40}(?:\s+et\s+al\.)?\s*"
    r"\(\s*(?:19|20)[0-9]{2}[a-z]?\s*\)"
)
_PARENTHETICAL_AUTHOR_YEAR_RE = re.compile(
    r"\(\s*[A-Z][A-Za-z'’-]{1,40}(?:\s+et\s+al\.)?\s*,\s*"
    r"(?:19|20)[0-9]{2}[a-z]?\s*\)"
)

_ORDERED_LIST_MARKER_RE = re.compile(
    r"^(?P<indent>[ \t　]*)(?P<marker>"
    r"\([1-9１-９][0-9０-９]{0,5}\)|（[1-9１-９][0-9０-９]{0,5}）"
    r"|[1-9１-９][0-9０-９]{0,5}[.)、．）])(?P<gap>[ \t　]*)(?P<body>[^\r\n]+)"
)
_LIST_LITERAL_START_RE = re.compile(
    r"^[ \t]*(?P<fence>`{3,}|~{3,})[^\r\n]*"
    r"|(?P<code>`+)"
    r"|(?P<dollar>\${1,2})|(?P<tex>\\[\[(])"
    r"|\\begin\{(?P<environment>[A-Za-z*]{1,32})\}",
    re.MULTILINE,
)


def _list_literal_ranges(value: str) -> list[tuple[int, int]]:
    """Protect literal contexts only; never parse or normalize their contents."""

    def escaped(position: int) -> bool:
        previous = position - 1
        while previous >= 0 and value[previous] == "\\":
            previous -= 1
        return (position - previous - 1) % 2 == 1

    ranges: list[tuple[int, int]] = []
    cursor = 0
    while (opening := _LIST_LITERAL_START_RE.search(value, cursor)) is not None:
        cursor = opening.end()
        if escaped(opening.start()):
            continue
        if fence := opening.group("fence"):
            closing = re.compile(
                rf"^[ \t]*{re.escape(fence[0])}{{{len(fence)},}}[ \t]*(?:\r?$)",
                re.MULTILINE,
            ).search(value, cursor)
            end = len(value) if closing is None else closing.end()
        elif code := opening.group("code"):
            # Backticks inside a code span close only at the same run length;
            # unlike prose, a preceding backslash is literal here.
            closing = re.compile(rf"(?<!`){re.escape(code)}(?!`)").search(value, cursor)
            end = len(value) if closing is None else closing.end()
        else:
            closer = (
                opening.group("dollar")
                or {"\\[": "\\]", "\\(": "\\)"}.get(opening.group("tex"))
                or "\\end{" + str(opening.group("environment")) + "}"
            )
            closing_at = value.find(closer, cursor)
            while closing_at >= 0 and escaped(closing_at):
                closing_at = value.find(closer, closing_at + len(closer))
            end = len(value) if closing_at < 0 else closing_at + len(closer)
        ranges.append((opening.start(), end))
        cursor = end
    return ranges


def _mask_ordered_list_markers(value: str) -> str:
    """Mask a structurally confirmed run of 1, 2, … prose-list markers.

    Same-indent/same-delimiter items may be separated by blank lines, not by
    ordinary text. Quantities, variable/expression-leading bodies, indented
    code, and math/literal spans abstain. This deliberately does not recognize
    every legal list: dropping a real quantity is worse than retaining an
    uncertain marker. Only marker characters change, preserving all offsets.
    """

    if "\n" not in value and "\r" not in value:
        return value
    literals = iter(_list_literal_ranges(value))
    protected = next(literals, None)
    spans: list[tuple[int, int]] = []
    run: list[tuple[int, int]] = []
    signature: tuple[str, str] | None = None
    offset = 0

    def flush() -> None:
        if len(run) >= 2:
            spans.extend(run)
        run.clear()

    for line in value.splitlines(keepends=True):
        start = offset
        offset += len(line)
        while protected is not None and protected[1] <= start:
            protected = next(literals, None)
        if not line.strip():
            continue
        match = _ORDERED_LIST_MARKER_RE.match(line)
        if match is None:
            flush()
            continue
        indent = match.group("indent")
        marker = unicodedata.normalize("NFKC", match.group("marker"))
        body = match.group("body").strip()
        marker_start = start + match.start("marker")
        # NFKC is applied only to the marker, never to scientific body text.
        # Unit checks are confined to this line, so a newline cannot donate a
        # suffix. One-letter variables, isotope-leading text and operators are
        # intentionally not sufficient prose evidence.
        # A lone unknown unit or a chemical expression (Sr/Ca, CO2, …) is not
        # prose. Require an initial multi-letter word followed by another word;
        # otherwise leave an ambiguous quantity/list item untouched.
        first_word = re.match(r"[A-Za-z]{3,}[ \t]+[A-Za-z]{2,}\b", body)
        prose = first_word is not None or _CJK_TEXT_RE.match(body) is not None
        physical = _physical_unit_at(body, 0) if body else None
        cjk_unit_prefix = any(
            _CJK_TEXT_RE.match(alias) is not None and body.startswith(alias)
            for alias in _RAW_UNIT_ALIASES
        )
        if (
            "\t" in indent
            or len(indent) >= 4
            or marker.endswith(".")
            and not match.group("gap")
            or not prose
            or physical is not None
            or cjk_unit_prefix
            or protected is not None
            and protected[0] <= marker_start < protected[1]
        ):
            flush()
            continue
        number = int(marker.strip("().、"))
        item_signature = (indent, "()" if marker.startswith("(") else marker[-1])
        if number != len(run) + 1 or signature != item_signature:
            flush()
        if number == 1:
            signature = item_signature
        elif not run:
            continue
        run.append((marker_start, start + match.end("marker")))
    flush()
    if not spans:
        return value
    output: list[str] = []
    cursor = 0
    for start, end in spans:
        output.extend((value[cursor:start], " " * (end - start)))
        cursor = end
    output.append(value[cursor:])
    return "".join(output)


_SUPERSCRIPT_EXPRESSION_RE = re.compile(
    rf"(?:(?P<coefficient>{_numbers.RAW_DECIMAL_TOKEN})\s*×\s*)?10"
    rf"(?P<exponent>[⁺⁻⁰¹²³⁴⁵⁶⁷⁸⁹]"
    rf"[+\-−﹣－0-9⁺⁻⁰¹²³⁴⁵⁶⁷⁸⁹]*)"
)

# This is a deliberately tiny TeX serialization bridge, not a TeX parser.
# MinerU occasionally retains math commands while inserting whitespace between
# every rendered glyph. Only explicit equation/math contexts use the scalar
# stitching rules below.
_LATEX_VARIABLES = {
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "epsilon": "ε",
    "zeta": "ζ",
    "eta": "η",
    "theta": "θ",
    "iota": "ι",
    "kappa": "κ",
    "lambda": "λ",
    "nu": "ν",
    "xi": "ξ",
    "pi": "π",
    "rho": "ρ",
    "sigma": "σ",
    "tau": "τ",
    "phi": "φ",
    "varphi": "ϕ",
    "varepsilon": "ϵ",
    "vartheta": "ϑ",
    "varpi": "ϖ",
    "varrho": "ϱ",
    "varsigma": "ς",
    "chi": "χ",
    "psi": "ψ",
    "omega": "ω",
}
_LATEX_VARIABLE_NAMES = "|".join(_LATEX_VARIABLES)
_LATEX_FORMULA_MARKER_RE = re.compile(
    rf"\\(?:{_LATEX_VARIABLE_NAMES}|circ|tag|mathrm|text|cdot|times|"
    r"sin|cos|tan|log|ln|exp|frac|sqrt|pm|mp|leqslant|geqslant|"
    r"leq|geq|le|ge|lt|gt|neq|ne|approx)\b"
)
_LATEX_TAG_RE = re.compile(r"\\tag\s*\{[^{}\r\n]{0,64}\}")
_LATEX_MATH_ISLAND_RE = re.compile(
    r"\\\((?P<paren>[^\r\n]{1,4096})\\\)"
    r"|\\\[(?P<bracket>[^\r\n]{1,4096})\\\]"
)
_LATEX_SPACING_RE = re.compile(r"\\(?:[,;:!]|\s|quad\b|qquad\b)")
_LATEX_UNIT_WRAPPER_RE = re.compile(
    r"\\(?:mathrm|text)\s*\{(?P<body>[^{}\r\n]{1,64})\}"
)
_LATEX_BRACED_UNIT_WRAPPER_RE = re.compile(
    r"\{\s*\\(?:mathrm|text)\s*\{(?P<body>[^{}\r\n]{1,64})\}\s*\}"
)
_LATEX_VARIABLE_RE = re.compile(rf"\\(?P<name>{_LATEX_VARIABLE_NAMES})\b")
_LATEX_DEGREE_SUFFIX_RE = re.compile(r"^\s*\^\s*(?:\{\s*\\circ\s*\}|\\circ\b)")
_LATEX_DEGREE_RE = re.compile(
    rf"(?P<number>{_numbers.RAW_DECIMAL_TOKEN})\s*\^\s*"
    r"(?:\{\s*\\circ\s*\}|\\circ\b)"
)
_LATEX_DEGREE_SCALE_RE = re.compile(r"°\s*(?P<scale>[CF])\b")
_LATEX_SPACED_SCALAR_RE = re.compile(
    r"(?<![0-9.])(?P<number>[0-9](?:[ \t]+[0-9])*"
    r"(?:[ \t]*\.[ \t]*[0-9](?:[ \t]+[0-9])*)?)(?![0-9.])"
)
_LATEX_UNIT_EXPONENT_RE = re.compile(
    r"(?P<unit>[A-Za-zµμ]+)\s*\^\s*\{\s*"
    r"(?P<exponent>[+\-−﹣－]?[0-9]{1,3})\s*\}"
)
_FORMULA_BINARY_OPERATOR_RE = re.compile(
    r"(?P<left>[°A-Za-zα-ω)])(?P<operator>[+\-−﹣－])(?=[0-9])"
)
_PLAIN_COMPACT_FORMULA_RE = re.compile(
    r"(?:=|<=|>=|<|>|≤|≥)[^\r\n]{0,512}"
    r"(?:°|[0-9]\s*[A-Za-zα-ω](?![A-Za-z]))"
)
_CJK_TEXT_RE = re.compile(r"[\u3400-\u9fff]")
_ASCII_WORD_RE = re.compile(r"[A-Za-z]+")
_ASCII_FORMULA_TERM_RE = re.compile(
    rf"(?<![0-9A-Za-z_.])(?P<number>{_numbers.RAW_DECIMAL_TOKEN})"
    r"(?P<spacing>\s*)"
    r"(?P<variable>[A-Za-z])(?![A-Za-z])"
)
_LATEX_COEFFICIENT_VARIABLE_RE = re.compile(
    rf"(?<![0-9A-Za-z_.])(?P<number>{_numbers.RAW_DECIMAL_TOKEN})"
    rf"(?P<spacing>\s*)\\(?P<name>{_LATEX_VARIABLE_NAMES})\b"
)
_LATEX_CONTEXT_COMMAND_RE = re.compile(r"\\[A-Za-z]+")
_LATEX_ENVIRONMENT_RE = re.compile(r"\\(?:begin|end)\s*\{[A-Za-z*]{1,32}\}")
_LATEX_RELATION_RE = re.compile(
    r"\\(?P<name>leqslant|geqslant|leq|geq|le|ge|lt|gt|neq|ne|approx)\b"
)
_LATEX_RELATIONS = {
    "leqslant": "≤",
    "leq": "≤",
    "le": "≤",
    "geqslant": "≥",
    "geq": "≥",
    "ge": "≥",
    "lt": "<",
    "gt": ">",
    "neq": "≠",
    "ne": "≠",
    "approx": "≈",
}
_LATEX_SIMPLE_FRAC_RE = re.compile(
    r"\\(?:frac|dfrac|tfrac)\s*"
    r"\{\s*([A-Za-z0-9.Ͱ-Ͽ]+|\\[A-Za-z]+)\s*\}\s*"
    r"\{\s*([A-Za-z0-9.Ͱ-Ͽ]+|\\[A-Za-z]+)\s*\}"
)
_LATEX_INLINE_SPACED_SCALAR_RE = re.compile(
    rf"(?<![A-Za-z0-9_])"
    rf"(?:\\(?:{_LATEX_VARIABLE_NAMES})|[A-Za-zͰ-Ͽ])"
    r"(?:\s*[_^]\s*(?:\{[^{}\r\n]{1,80}\}|[A-Za-z0-9Ͱ-Ͽ]))*"
    r"(?:\s*(?:\\left\s*)?\([^()\r\n]{1,160}(?:\\right\s*)?\))?\s*"
    r"(?:=|<=|>=|<|>|≤|≥|≈|"
    r"\\(?:leqslant|geqslant|leq|geq|le|ge|lt|gt|approx)\b)\s*"
    r"[+\-−]?[0-9](?:[ \t]+[0-9])+"
    r"(?:[ \t]*\.[ \t]*[0-9](?:[ \t]+[0-9])*)?"
    r"\s*(?:\^\s*(?:\{\s*\\circ\s*\}|\\circ\b)"
    r"(?:\s*\\(?:mathrm|text)\s*\{[^{}\r\n]{1,64}\})?"
    r"|(?:\\(?:mathrm|text)\s*\{[^{}\r\n]{1,64}\}"
    r"|[A-Za-zµμ°%]{1,16}))"
)
_ASCII_VARIABLE_PLACEHOLDERS = {
    variable: f"FormulaVariable{ord(variable):04X}" for variable in string.ascii_letters
}
_SUPERSCRIPT_CHARACTERS = str.maketrans(
    {
        "+": "⁺",
        "-": "⁻",
        "0": "⁰",
        "1": "¹",
        "2": "²",
        "3": "³",
        "4": "⁴",
        "5": "⁵",
        "6": "⁶",
        "7": "⁷",
        "8": "⁸",
        "9": "⁹",
    }
)
_OPAQUE_UNIT_SYMBOLS = frozenset("/／·⋅×*^+−﹣－-%％‰°℃℉_()[]{}")
_OPAQUE_UNIT_STOPWORDS = frozenset(
    {
        "and",
        "are",
        "had",
        "has",
        "have",
        "indicate",
        "indicated",
        "indicates",
        "is",
        "or",
        "show",
        "showed",
        "shows",
        "than",
        "that",
        "then",
        "to",
        "was",
        "were",
        "with",
        "without",
        "yielded",
        "yields",
    }
)
_MAX_OPAQUE_UNIT_CHARS = 64

# Aliases normalize spelling and Unicode variants only.  Different magnitudes
# remain different units; there is intentionally no dimensional conversion.
_RAW_UNIT_ALIASES = {
    "微摩尔": "umol",
    "毫摩尔": "mmol",
    "千摩尔": "kmol",
    "摩尔": "mol",
    "摄氏度": "degC",
    "华氏度": "degF",
    "开尔文": "K",
    "千帕": "kPa",
    "兆帕": "MPa",
    "帕": "Pa",
    "毫克": "mg",
    "千克": "kg",
    "微克": "ug",
    "纳克": "ng",
    "克": "g",
    "毫米": "mm",
    "厘米": "cm",
    "千米": "km",
    "微米": "um",
    "纳米": "nm",
    "米": "m",
    "毫升": "mL",
    "微升": "uL",
    "升": "L",
    "毫秒": "ms",
    "微秒": "us",
    "分钟": "min",
    "小时": "h",
    "年": "yr",
    "天": "day",
    "秒": "s",
    "赫兹": "Hz",
    "度": "deg",
    "百分比": "%",
    "°C": "degC",
    "°c": "degC",
    "℃": "degC",
    "°F": "degF",
    "°f": "degF",
    "degree Celsius": "degC",
    "degrees Celsius": "degC",
    "degree Fahrenheit": "degF",
    "degrees Fahrenheit": "degF",
    "µmol": "umol",
    "μmol": "umol",
    "umol": "umol",
    "mmol": "mmol",
    "kmol": "kmol",
    "nmol": "nmol",
    "mol": "mol",
    "GHz": "GHz",
    "MHz": "MHz",
    "kHz": "kHz",
    "Hz": "Hz",
    "GPa": "GPa",
    "MPa": "MPa",
    "kPa": "kPa",
    "hPa": "hPa",
    "Pa": "Pa",
    "mbar": "mbar",
    "bar": "bar",
    "GeV": "GeV",
    "MeV": "MeV",
    "keV": "keV",
    "eV": "eV",
    "MW": "MW",
    "kW": "kW",
    "mW": "mW",
    "MJ": "MJ",
    "kJ": "kJ",
    "mJ": "mJ",
    "kV": "kV",
    "mV": "mV",
    "mA": "mA",
    "µA": "uA",
    "μA": "uA",
    "uA": "uA",
    "Wb": "Wb",
    "Bq": "Bq",
    "Gy": "Gy",
    "Sv": "Sv",
    "ohm": "ohm",
    "Ω": "ohm",
    "lm": "lm",
    "lx": "lx",
    "A": "A",
    "V": "V",
    "W": "W",
    "J": "J",
    "N": "N",
    "C": "C",
    "T": "T",
    "S": "S",
    "F": "F",
    "H": "H",
    "kg": "kg",
    "mg": "mg",
    "µg": "ug",
    "μg": "ug",
    "ug": "ug",
    "ng": "ng",
    "pg": "pg",
    "fg": "fg",
    "km": "km",
    "cm": "cm",
    "mm": "mm",
    "µm": "um",
    "μm": "um",
    "um": "um",
    "nm": "nm",
    "pm": "pm",
    "mL": "mL",
    "ml": "mL",
    "µL": "uL",
    "μL": "uL",
    "uL": "uL",
    "nL": "nL",
    "nl": "nL",
    "L": "L",
    "ms": "ms",
    "µs": "us",
    "μs": "us",
    "us": "us",
    "ns": "ns",
    "ps": "ps",
    "mins": "min",
    "min": "min",
    "hrs": "h",
    "hr": "h",
    "days": "day",
    "day": "day",
    "yrs": "yr",
    "yr": "yr",
    "ppm": "ppm",
    "ppb": "ppb",
    "ppt": "ppt",
    "wt%": "wt%",
    "vol%": "vol%",
    "mM": "mM",
    "µM": "uM",
    "μM": "uM",
    "uM": "uM",
    "nM": "nM",
    "kbp": "kbp",
    "bp": "bp",
    "percent": "%",
    "deg": "deg",
    "°": "deg",
    "rad": "rad",
    "Ma": "Ma",
    "Ga": "Ga",
    "ka": "ka",
    "K": "K",
    "g": "g",
    "m": "m",
    "s": "s",
    "h": "h",
    "%": "%",
    "％": "%",
    "‰": "‰",
}


def _unit_aliases() -> tuple[tuple[str, str], ...]:
    aliases: dict[str, str] = {}
    for alias, canonical in _RAW_UNIT_ALIASES.items():
        for spelling in (alias, unicodedata.normalize("NFKC", alias)):
            previous = aliases.setdefault(spelling, canonical)
            if previous != canonical:  # pragma: no cover - import-time invariant
                raise RuntimeError(f"conflicting unit alias: {spelling}")
    for name, symbol in _LATEX_VARIABLES.items():
        aliases[symbol] = f"formula-var:{name}"
    for variable, placeholder in _ASCII_VARIABLE_PLACEHOLDERS.items():
        aliases[placeholder] = f"formula-var:{variable}"
    return tuple(sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True))


_UNIT_ALIASES = _unit_aliases()

_PREFIX_COMPARATORS = (
    (re.compile(r"(?:大于或等于|高于或等于|不低于|不少于|至少)\s*$"), "ge"),
    (re.compile(r"(?:小于或等于|低于或等于|不高于|不超过|至多|最多)\s*$"), "le"),
    (re.compile(r"(?:高于|大于|超过|多于)\s*$"), "gt"),
    (re.compile(r"(?:低于|小于|少于)\s*$"), "lt"),
    (re.compile(r"(?:大约|约为|约|近似于|近似)\s*$"), "approx"),
    (re.compile(r"等于\s*$"), "eq"),
    (
        re.compile(
            r"(?<![A-Za-z])(?:no\s+less\s+than|not\s+below|at\s+least)\s*$",
            re.IGNORECASE,
        ),
        "ge",
    ),
    (
        re.compile(
            r"(?<![A-Za-z])(?:no\s+more\s+than|not\s+above|at\s+most)\s*$",
            re.IGNORECASE,
        ),
        "le",
    ),
    (
        re.compile(
            r"(?<![A-Za-z])(?:greater\s+than|higher\s+than|more\s+than|above|over)\s*$",
            re.IGNORECASE,
        ),
        "gt",
    ),
    (
        re.compile(
            r"(?<![A-Za-z])(?:less\s+than|lower\s+than|fewer\s+than|below|under)\s*$",
            re.IGNORECASE,
        ),
        "lt",
    ),
    (
        re.compile(
            r"(?<![A-Za-z])(?:equal\s+to|equals|exactly)\s*$",
            re.IGNORECASE,
        ),
        "eq",
    ),
    (
        re.compile(
            r"(?<![A-Za-z])(?:approximately|approx\.?|about|around)\s*$",
            re.IGNORECASE,
        ),
        "approx",
    ),
    (re.compile(r"(?:>=|≥)\s*$"), "ge"),
    (re.compile(r"(?:<=|≤)\s*$"), "le"),
    (re.compile(r">\s*$"), "gt"),
    (re.compile(r"<\s*$"), "lt"),
    (re.compile(r"(?:==|=)\s*$"), "eq"),
    (re.compile(r"(?:≈|~)\s*$"), "approx"),
)

_SUFFIX_COMPARATORS = (
    (re.compile(r"^\s*(?:及以上|或以上|以上)"), "ge"),
    (re.compile(r"^\s*(?:及以下|或以下|以下)"), "le"),
    (
        re.compile(r"^\s*(?:or\s+more|and\s+above)\b", re.IGNORECASE),
        "ge",
    ),
    (
        re.compile(r"^\s*(?:or\s+less|and\s+below)\b", re.IGNORECASE),
        "le",
    ),
)

_SCOPE_FIELDS = (
    "location",
    "time",
    "formation",
    "sample_type",
    "method",
    "condition",
    "measurement",
    "uncertainty",
    "entities",
)


def _attached_label_character(value: str) -> bool:
    """Return whether a preceding character can make a numeric label.

    ASCII identifiers such as H2O and model5 are excluded.  CJK scientific
    prose normally has no whitespace before a measurement (``高于5毫克``), so
    an ideograph is not treated as part of an alphanumeric label here.
    """

    return (
        value == "_"
        or (value.isascii() and value.isalnum())
        or value
        in {
            "⁺",
            "⁻",
        }
    )


def _unit_component(value: str, start: int) -> tuple[str, int] | None:
    for alias, canonical in _UNIT_ALIASES:
        if not value.startswith(alias, start):
            continue
        end = start + len(alias)
        if (
            end == len(value)
            or not _numbers.identifier_character(value[end])
            or _suffix_comparator(value, end) is not None
        ):
            return canonical, end
    return None


def _opaque_unit_character(value: str) -> bool:
    return value.isalnum() or value in _OPAQUE_UNIT_SYMBOLS or value in {"µ", "μ"}


def _opaque_unit_value(raw: str) -> str | None:
    normalized = (
        unicodedata.normalize("NFKC", raw)
        .translate(_numbers.MINUS_TRANSLATION)
        .replace("µ", "u")
        .replace("μ", "u")
        .replace("／", "/")
        .replace("·", "*")
        .replace("⋅", "*")
        .replace("×", "*")
    )
    normalized = "".join(normalized.split())
    if not normalized:
        return None
    return f"opaque:{normalized.encode('utf-8').hex()}"


def _opaque_unit(
    value: str,
    start: int,
) -> tuple[str, int] | None:
    """Freeze one unrecognized adjacent unit token without interpreting it.

    Returning no mention for an unfamiliar unit is unsafe: both the value and
    unit can then change without creating an unsupported key.  Instead, retain
    a bounded normalized UTF-8 hex token.  Hex keeps the key delimiter-safe and
    is trivial to reproduce in TypeScript without floating point or hashing.
    """

    if start >= len(value) or not (
        value[start].isalpha()
        or value[start].isdigit()
        or value[start] in {"%", "％", "‰", "°", "℃", "℉", "µ", "μ"}
    ):
        return None
    end = start
    while (
        end < len(value)
        and end - start < _MAX_OPAQUE_UNIT_CHARS
        and _opaque_unit_character(value[end])
    ):
        end += 1
    raw = value[start:end].rstrip(".")
    end = start + len(raw)
    if not raw or raw.casefold() in _OPAQUE_UNIT_STOPWORDS:
        return None
    opaque = _opaque_unit_value(raw)
    return None if opaque is None else (opaque, end)


def _spaced_exponent_unit(
    value: str,
    *,
    unit_start: int,
    first_end: int,
) -> tuple[str, int] | None:
    tail_start = first_end + len(value[first_end:]) - len(value[first_end:].lstrip())
    if tail_start == first_end:
        return None
    tail = _opaque_unit(value, tail_start)
    if tail is None:
        return None
    _, tail_end = tail
    raw_tail = value[tail_start:tail_end]
    if re.search(r"(?:\^[+-]?[0-9]+|[+-][0-9]+)$", raw_tail) is None:
        return None
    opaque = _opaque_unit_value(value[unit_start:tail_end])
    return None if opaque is None else (opaque, tail_end)


def _trailing_unit_closer(value: str, end: int) -> bool:
    """Return whether a recognized unit ends at an outer closing delimiter.

    Parentheses/brackets that begin inside a unit expression are handled by
    the opaque-unit path before this boundary is reached. A closing delimiter
    immediately after an otherwise complete recognized unit therefore closes
    surrounding prose/math punctuation rather than extending that unit.
    """

    return end < len(value) and value[end] in ")]}"


def _adjacent_unit(value: str, start: int) -> tuple[str, int] | None:
    unit_start = start + len(value[start:]) - len(value[start:].lstrip())
    first = _unit_component(value, unit_start)
    if first is None:
        # Suffix comparators such as ``5 or more`` and ``5以上`` are not
        # unknown units. Leave their text for the comparator parser.
        if _suffix_comparator(value, unit_start) is not None:
            return "bare", start
        opaque = _opaque_unit(value, unit_start)
        return opaque if opaque is not None else ("bare", start)
    first_unit, first_end = first
    if _suffix_comparator(value, first_end) is not None:
        return first_unit, first_end
    slash_start = first_end + len(value[first_end:]) - len(value[first_end:].lstrip())
    if slash_start == len(value) or value[slash_start] not in {"/", "／"}:
        # A recognized prefix followed by exponent/product syntax is not the
        # simple prefix unit. Freeze the whole expression so ``mg·L−1`` cannot
        # authorize ``mg·kg−1`` merely because both start with ``mg``.
        if _trailing_unit_closer(value, first_end):
            return first_unit, first_end
        if first_end < len(value) and value[first_end] in _OPAQUE_UNIT_SYMBOLS:
            opaque = _opaque_unit(value, unit_start)
            if opaque is not None:
                return opaque
        spaced = _spaced_exponent_unit(
            value,
            unit_start=unit_start,
            first_end=first_end,
        )
        if spaced is not None:
            return spaced
        return first_unit, first_end
    denominator_start = slash_start + 1
    denominator_start += len(value[denominator_start:]) - len(
        value[denominator_start:].lstrip()
    )
    denominator = _unit_component(value, denominator_start)
    if denominator is None:
        opaque = _opaque_unit(value, unit_start)
        return opaque
    denominator_unit, denominator_end = denominator
    # Do not silently accept only the first part of a longer compound unit.
    another_slash = (
        denominator_end
        + len(value[denominator_end:])
        - len(value[denominator_end:].lstrip())
    )
    if another_slash < len(value) and value[another_slash] in {"/", "／"}:
        opaque = _opaque_unit(value, unit_start)
        return opaque
    if _trailing_unit_closer(value, denominator_end):
        return f"{first_unit}/{denominator_unit}", denominator_end
    if denominator_end < len(value) and value[denominator_end] in (
        _OPAQUE_UNIT_SYMBOLS
    ):
        opaque = _opaque_unit(value, unit_start)
        if opaque is not None:
            return opaque
    return f"{first_unit}/{denominator_unit}", denominator_end


def _physical_unit_at(value: str, start: int) -> tuple[str, int] | None:
    unit_start = start + len(value[start:]) - len(value[start:].lstrip())
    unit = _unit_component(value, unit_start)
    if unit is None or unit[0].startswith("formula-var:"):
        return None
    return unit


def _unwrap_latex_unit(match: re.Match[str]) -> str:
    body = _LATEX_SPACING_RE.sub(" ", match.group("body")).replace("~", " ")
    compact = "".join(body.split())
    unit = _physical_unit_at(compact, 0)
    if unit is None or unit[1] != len(compact):
        return match.group(0)
    return compact


def _normalize_latex_unit_exponent(match: re.Match[str]) -> str:
    unit = match.group("unit")
    recognized = _physical_unit_at(unit, 0)
    if recognized is None or recognized[1] != len(unit):
        return match.group(0)
    exponent = match.group("exponent").translate(_numbers.MINUS_TRANSLATION)
    return unit + exponent.translate(_SUPERSCRIPT_CHARACTERS)


def _spaced_scalar_has_safe_suffix(value: str, end: int) -> bool:
    suffix = value[end:]
    if _LATEX_DEGREE_SUFFIX_RE.match(suffix) is not None:
        return True
    unit = _physical_unit_at(value, end)
    return unit is not None


def _normalize_spaced_latex_scalars(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        raw = match.group("number")
        if re.search(r"[0-9][ \t]+[0-9]|[ \t]\.|\.[ \t]", raw) is None:
            return raw
        compact = "".join(raw.split())
        if re.fullmatch(_numbers.DECIMAL_BODY, compact) is not None and (
            "." in raw or _spaced_scalar_has_safe_suffix(value, match.end())
        ):
            return compact
        # In math, ``1 8 0`` can also be a vector/list serialization.  Without
        # a recognized scalar suffix it donates no fragmentary numeric keys.
        return " " * len(raw)

    return _LATEX_SPACED_SCALAR_RE.sub(replace, value)


def _normalize_micro_unit(value: str) -> str:
    return re.sub(
        r"\\mu\s*(?=(?:mol|g|m|L|A|s|M)\b)",
        "µ",
        value,
    )


def _normalize_ascii_formula_variables(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        variable_start = match.start("variable")
        variable = match.group("variable")
        suffix = value[match.end("variable") :]
        if variable in {"e", "E"} and re.match(r"[+\-][0-9]", suffix):
            return match.group(0)
        unit = _physical_unit_at(value, variable_start)
        if unit is not None:
            return match.group(0)
        return (
            match.group("number")
            + match.group("spacing")
            + _ASCII_VARIABLE_PLACEHOLDERS[variable]
        )

    return _ASCII_FORMULA_TERM_RE.sub(replace, value)


def _split_formula_binary_operators(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        if (
            match.group("left") in {"e", "E"}
            and match.start() > 0
            and value[match.start() - 1].isdigit()
        ):
            return match.group(0)
        return f"{match.group('left')} {match.group('operator')} "

    return _FORMULA_BINARY_OPERATOR_RE.sub(replace, value)


def _mask_ambiguous_formula_scalars(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        raw = match.group("number")
        if re.search(r"[0-9][ \t]+[0-9]|[ \t]\.|\.[ \t]", raw) is None:
            return raw
        compact = "".join(raw.split())
        if "." in raw and re.fullmatch(_numbers.DECIMAL_BODY, compact) is not None:
            return compact
        return " " * len(raw)

    return _LATEX_SPACED_SCALAR_RE.sub(replace, value)


def _normalize_local_formula_terms(
    value: str,
    *,
    allow_ascii_variables: bool,
    mask_spaced_scalars: bool,
) -> str:
    normalized = value
    if allow_ascii_variables or mask_spaced_scalars:
        normalized = _LATEX_SPACING_RE.sub(" ", normalized).replace("~", " ")
    normalized = _LATEX_COEFFICIENT_VARIABLE_RE.sub(
        lambda match: (
            match.group("number")
            + match.group("spacing")
            + _LATEX_VARIABLES[match.group("name")]
        ),
        normalized,
    )
    if mask_spaced_scalars:
        normalized = _mask_ambiguous_formula_scalars(normalized)
    has_formula_terms = (
        allow_ascii_variables
        or re.search(rf"{_numbers.RAW_DECIMAL_TOKEN}\s*[α-ω]", normalized) is not None
    )
    if has_formula_terms:
        # Split on the original one-character variable boundary. ASCII
        # placeholders end in hexadecimal digits, so delaying this step would
        # let the first placeholder greedily absorb the following term.
        normalized = _split_formula_binary_operators(normalized)
    if allow_ascii_variables:
        normalized = _normalize_ascii_formula_variables(normalized)
    return normalized


def _unescaped_dollar(value: str, start: int) -> int | None:
    cursor = value.find("$", start)
    while cursor >= 0:
        slash_count = 0
        previous = cursor - 1
        while previous >= 0 and value[previous] == "\\":
            slash_count += 1
            previous -= 1
        if slash_count % 2 == 0:
            return cursor
        cursor = value.find("$", cursor + 1)
    return None


def _normalize_dollar_math_islands(
    value: str,
) -> tuple[str, bool]:
    output: list[str] = []
    cursor = 0
    found = False
    while True:
        opening = _unescaped_dollar(value, cursor)
        if opening is None:
            output.append(value[cursor:])
            break
        closing = _unescaped_dollar(value, opening + 1)
        if closing is None:
            output.append(value[cursor:])
            break
        body = value[opening + 1 : closing]
        if 1 <= len(body) <= 4_096 and "\n" not in body and "\r" not in body:
            output.append(value[cursor:opening])
            output.append(" ")
            output.append(
                _normalize_formula_fragment(
                    body,
                    stitch_spaced_scalars=True,
                )
            )
            output.append(" ")
            found = True
        else:
            output.append(value[cursor : closing + 1])
        cursor = closing + 1
    return "".join(output), found


def _normalize_formula_fragment(
    value: str,
    *,
    stitch_spaced_scalars: bool,
) -> str:
    normalized = _LATEX_TAG_RE.sub(_spaces, value)
    normalized = _LATEX_ENVIRONMENT_RE.sub(_spaces, normalized)
    normalized = _LATEX_RELATION_RE.sub(
        lambda match: _LATEX_RELATIONS[match.group("name")],
        normalized,
    )
    while True:
        fraction = _LATEX_SIMPLE_FRAC_RE.sub(r"\1/\2", normalized)
        if fraction == normalized:
            break
        normalized = fraction
    normalized = _LATEX_BRACED_UNIT_WRAPPER_RE.sub(
        _unwrap_latex_unit,
        normalized,
    )
    normalized = _LATEX_UNIT_WRAPPER_RE.sub(_unwrap_latex_unit, normalized)
    normalized = _LATEX_SPACING_RE.sub(" ", normalized).replace("~", " ")
    normalized = _normalize_micro_unit(normalized)
    normalized = normalized.replace(r"\cdot", "·")
    normalized = _LATEX_UNIT_EXPONENT_RE.sub(
        _normalize_latex_unit_exponent,
        normalized,
    )
    if stitch_spaced_scalars:
        normalized = _normalize_spaced_latex_scalars(normalized)
    normalized = _LATEX_DEGREE_RE.sub(
        lambda match: f"{match.group('number')}°",
        normalized,
    )
    normalized = _LATEX_DEGREE_SCALE_RE.sub(
        lambda match: f"°{match.group('scale')}",
        normalized,
    )
    normalized = _LATEX_VARIABLE_RE.sub(
        lambda match: _LATEX_VARIABLES[match.group("name")],
        normalized,
    )
    normalized = _split_formula_binary_operators(normalized)
    normalized = _normalize_ascii_formula_variables(normalized)
    return (
        normalized.replace("$", " ")
        .replace(r"\(", " ")
        .replace(r"\)", " ")
        .replace(r"\[", " ")
        .replace(r"\]", " ")
    )


def _looks_like_whole_equation(value: str) -> bool:
    if "=" not in value or _CJK_TEXT_RE.search(value) is not None:
        return False
    has_math_marker = _LATEX_FORMULA_MARKER_RE.search(value) is not None or any(
        character == "°" or character in _LATEX_VARIABLES.values()
        for character in value
    )
    if not has_math_marker:
        return False

    scrubbed = _LATEX_TAG_RE.sub(" ", value)
    scrubbed = _LATEX_ENVIRONMENT_RE.sub(" ", scrubbed)
    scrubbed = _LATEX_UNIT_WRAPPER_RE.sub(" ", scrubbed)
    scrubbed = _LATEX_FORMULA_MARKER_RE.sub(" ", scrubbed)
    scrubbed = "".join(
        " " if character in _LATEX_VARIABLES.values() else character
        for character in scrubbed
    )
    return all(len(word) == 1 for word in _ASCII_WORD_RE.findall(scrubbed))


def _looks_like_formula_only(value: str) -> bool:
    if (
        _CJK_TEXT_RE.search(value) is not None
        or re.search(r"[0-9]", value) is None
        or re.search(r"(?:[=<>^{}+\-−﹣－]|\\\s)", value) is None
    ):
        return False
    scrubbed = _LATEX_TAG_RE.sub(" ", value)
    scrubbed = _LATEX_BRACED_UNIT_WRAPPER_RE.sub(" ", scrubbed)
    scrubbed = _LATEX_UNIT_WRAPPER_RE.sub(" ", scrubbed)
    # Commands are ignored only to decide whether the whole residual string is
    # formula-shaped. They are never interpreted as variables or quantities.
    scrubbed = _LATEX_ENVIRONMENT_RE.sub(" ", scrubbed)
    scrubbed = _LATEX_CONTEXT_COMMAND_RE.sub(" ", scrubbed)
    scrubbed = "".join(
        " " if character in _LATEX_VARIABLES.values() else character
        for character in scrubbed
    )
    return all(len(word) == 1 for word in _ASCII_WORD_RE.findall(scrubbed))


def _narrow_latex_scientific_view(
    value: str,
    *,
    equation_context: bool,
) -> str:
    if equation_context:
        return _normalize_formula_fragment(value, stitch_spaced_scalars=True)

    inline_normalized = _LATEX_INLINE_SPACED_SCALAR_RE.sub(
        lambda match: _normalize_formula_fragment(
            match.group(0),
            stitch_spaced_scalars=True,
        ),
        value,
    )
    inline_normalized = _LATEX_RELATION_RE.sub(
        lambda match: _LATEX_RELATIONS[match.group("name")],
        inline_normalized,
    )
    tagged = _LATEX_TAG_RE.sub(_spaces, inline_normalized)
    normalized, found_island = _normalize_dollar_math_islands(tagged)

    def replace_island(match: re.Match[str]) -> str:
        nonlocal found_island
        found_island = True
        body = next(
            group
            for group in (
                match.group("paren"),
                match.group("bracket"),
            )
            if group is not None
        )
        normalized = _normalize_formula_fragment(
            body,
            stitch_spaced_scalars=True,
        )
        return f" {normalized} "

    normalized = _LATEX_MATH_ISLAND_RE.sub(replace_island, normalized)
    if found_island:
        return _normalize_local_formula_terms(
            normalized,
            allow_ascii_variables=False,
            mask_spaced_scalars=False,
        )
    if _looks_like_whole_equation(value):
        return _normalize_formula_fragment(
            value,
            stitch_spaced_scalars=True,
        )
    formula_only = _looks_like_formula_only(value)
    return _normalize_local_formula_terms(
        tagged,
        allow_ascii_variables=formula_only,
        mask_spaced_scalars=formula_only,
    )


def _number_context_allowed(value: str, start: int, end: int) -> bool:
    if start and (
        _attached_label_character(value[start - 1]) or value[start - 1] == "^"
    ):
        return False
    if end == len(value) or not _numbers.identifier_character(value[end]):
        return True
    unit = _adjacent_unit(value, end)
    return unit is not None and unit[0] != "bare"


def _prefix_comparator(value: str, start: int) -> str | None:
    prefix = value[max(0, start - 64) : start]
    prefix = re.sub(r"[\s(\[{]{1,8}$", "", prefix)
    return next(
        (
            canonical
            for pattern, canonical in _PREFIX_COMPARATORS
            if pattern.search(prefix)
        ),
        None,
    )


def _suffix_comparator(value: str, start: int) -> str | None:
    suffix = value[start : start + 48]
    return next(
        (
            canonical
            for pattern, canonical in _SUFFIX_COMPARATORS
            if pattern.search(suffix)
        ),
        None,
    )


def _mention_key(
    value: str,
    *,
    numeric: str,
    start: int,
    end: int,
) -> str | None:
    unit = _adjacent_unit(value, end)
    if unit is None:
        return None
    canonical_unit, unit_end = unit
    prefix = _prefix_comparator(value, start)
    suffix = _suffix_comparator(value, unit_end)
    if prefix is not None and suffix is not None and prefix != suffix:
        return None
    comparator = prefix or suffix or "bare"
    return f"mention:{numeric}|cmp:{comparator}|unit:{canonical_unit}"


def _spaces(match: re.Match[str]) -> str:
    return " " * len(match.group(0))


def _mask_navigation_references(value: str) -> str:
    masked = _PARENTHETICAL_AUTHOR_YEAR_RE.sub(_spaces, value)
    masked = _AUTHOR_YEAR_RE.sub(_spaces, masked)
    masked = _EN_REFERENCE_NUMBER_RE.sub(_spaces, masked)
    masked = _REFERENCE_NUMBER_RE.sub(_spaces, masked)
    masked = _SECTION_SYMBOL_RE.sub(_spaces, masked)

    def mask_bracket(match: re.Match[str]) -> str:
        unit = _adjacent_unit(masked, match.end())
        if unit is not None and unit[0] != "bare" and not unit[0].startswith("opaque:"):
            return match.group(0)
        return _spaces(match)

    return _BRACKET_CITATION_RE.sub(mask_bracket, masked)


def _power_mentions(value: str) -> tuple[str, list[str]]:
    output: list[str] = []
    working = value

    def replace_power(match: re.Match[str], *, superscript: bool) -> str:
        if _number_context_allowed(working, match.start(), match.end()):
            exponent = (
                _numbers.superscript_exponent(match.group("exponent"))
                if superscript
                else int(match.group("exponent").translate(_numbers.MINUS_TRANSLATION))
                if re.fullmatch(
                    r"[+-]?[0-9]{1,6}",
                    match.group("exponent").translate(_numbers.MINUS_TRANSLATION),
                )
                else None
            )
            numeric = (
                _numbers.numeric_key(match.group("coefficient") or "1", power=exponent)
                if exponent is not None
                else None
            )
            if numeric is not None:
                mention = _mention_key(
                    working,
                    numeric=numeric,
                    start=match.start(),
                    end=match.end(),
                )
                if mention is not None:
                    output.append(mention)
        # Always mask a syntactic power expression, even if its surrounding
        # identifier/unit is invalid, so fragments cannot be admitted as numbers.
        return _spaces(match)

    working = _SUPERSCRIPT_EXPRESSION_RE.sub(
        lambda match: replace_power(match, superscript=True), working
    )
    working = _numbers.CARET_EXPRESSION_RE.sub(
        lambda match: replace_power(match, superscript=False), working
    )

    def replace_ambiguous(match: re.Match[str]) -> str:
        if _number_context_allowed(working, match.start(), match.end()):
            mention = _mention_key(
                working,
                numeric=_numbers.expression_key(match.group(0)),
                start=match.start(),
                end=match.end(),
            )
            if mention is not None:
                output.append(mention)
        return _spaces(match)

    working = _numbers.AMBIGUOUS_MULTIPLICATION_RE.sub(replace_ambiguous, working)
    return working, output


def _text_mention_key_occurrences(
    value: str,
    *,
    equation_context: bool = False,
) -> list[str]:
    normalized_text = unicodedata.normalize("NFC", value)
    if not equation_context:
        normalized_text = _mask_ordered_list_markers(normalized_text)
    scientific = _mask_navigation_references(
        _narrow_latex_scientific_view(
            normalized_text,
            equation_context=equation_context,
        )
    )
    without_powers, output = _power_mentions(scientific)
    normalized = unicodedata.normalize("NFKC", without_powers).translate(
        _numbers.MINUS_TRANSLATION
    )
    for match in _numbers.NUMBER_TOKEN_RE.finditer(normalized):
        if not _number_context_allowed(normalized, match.start(), match.end()):
            continue
        numeric = _numbers.numeric_key(match.group("number"))
        if numeric is None:
            continue
        mention = _mention_key(
            normalized,
            numeric=numeric,
            start=match.start(),
            end=match.end(),
        )
        if mention is not None:
            output.append(mention)
    return output


def _text_mention_keys(
    value: str,
    *,
    equation_context: bool = False,
) -> set[str]:
    return set(_text_mention_key_occurrences(value, equation_context=equation_context))


def latex_equation_scientific_mention_keys(value: str) -> set[str]:
    """Return keys from one explicitly typed equation/LaTeX source span.

    This context permits only notation-level normalization: bounded TeX
    wrappers and spacing, exact formula-variable aliases, scalar digit spacing
    when followed by a recognized unit/degree suffix, and degree notation.  It
    never evaluates, simplifies, rounds, converts, or solves an expression.
    """

    return _text_mention_keys(value, equation_context=True)


def _scope_values(value: Any) -> list[Any] | None:
    if isinstance(value, Mapping):
        if not all(field in value for field in _SCOPE_FIELDS):
            return None
        return [value[field] for field in _SCOPE_FIELDS]
    model_fields = getattr(type(value), "model_fields", {})
    if not all(field in model_fields for field in _SCOPE_FIELDS):
        return None
    return [getattr(value, field) for field in _SCOPE_FIELDS]


def scientific_mention_keys(value: Any) -> set[str]:
    """Return exact comparator-and-unit-bound keys from a scientific value.

    Besides scalar text/numbers, the function accepts the Compiler ``Scope``
    model (or its complete mapping form) and scans only its scientific fields;
    identity fields such as ``id`` are deliberately excluded.  Lists and tuples
    are aggregated to support ``Scope.entities`` without broad arbitrary-object
    traversal.
    """

    if value is None or isinstance(value, bool):
        return set()
    if isinstance(value, Decimal):
        if not value.is_finite():
            return set()
        numeric = _numbers.numeric_key(str(value))
        return (
            {f"mention:{numeric}|cmp:bare|unit:bare"} if numeric is not None else set()
        )
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return set()
        numeric = _numbers.numeric_key(str(value))
        return (
            {f"mention:{numeric}|cmp:bare|unit:bare"} if numeric is not None else set()
        )
    if isinstance(value, str):
        return _text_mention_keys(value)
    scope_values = _scope_values(value)
    if scope_values is not None:
        return set().union(*(scientific_mention_keys(item) for item in scope_values))
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return set().union(*(scientific_mention_keys(item) for item in value))
    return set()


def scientific_mention_key_counts(value: Any) -> Counter[str]:
    """Return occurrence counts without weakening the conservative key rules."""

    if value is None or isinstance(value, bool):
        return Counter()
    if isinstance(value, str):
        return Counter(_text_mention_key_occurrences(value))
    scope_values = _scope_values(value)
    if scope_values is not None:
        output: Counter[str] = Counter()
        for item in scope_values:
            output.update(scientific_mention_key_counts(item))
        return output
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        output = Counter()
        for item in value:
            output.update(scientific_mention_key_counts(item))
        return output
    return Counter(scientific_mention_keys(value))


__all__ = [
    "latex_equation_scientific_mention_keys",
    "scientific_mention_key_counts",
    "scientific_mention_keys",
]
