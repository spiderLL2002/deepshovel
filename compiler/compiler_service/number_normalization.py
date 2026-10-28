"""Shared exact decimal primitives for scientific source validation."""

from __future__ import annotations

import math
import re
import unicodedata
from decimal import Decimal, InvalidOperation

DECIMAL_BODY = (
    r"(?:[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]*)?"
    r"|[0-9]+(?:\.[0-9]*)?|\.[0-9]+)"
)
DECIMAL_TOKEN = rf"[+-]?{DECIMAL_BODY}(?:[eE][+-]?[0-9]{{1,6}})?"
RAW_DECIMAL_TOKEN = (
    rf"[+\-−﹣－]?{DECIMAL_BODY}"
    rf"(?:[eE][+\-−﹣－]?[0-9]{{1,6}})?"
)
DECIMAL_LITERAL_RE = re.compile(
    rf"^(?P<sign>[+-]?)(?P<mantissa>{DECIMAL_BODY})"
    rf"(?:[eE](?P<exponent>[+-]?[0-9]{{1,6}}))?$"
)
NUMBER_TOKEN_RE = re.compile(
    rf"(?<![0-9.,])(?P<number>{DECIMAL_TOKEN})"
    rf"(?![0-9]|[.,](?=[0-9]))"
)
CARET_EXPRESSION_RE = re.compile(
    rf"(?:(?P<coefficient>{RAW_DECIMAL_TOKEN})\s*×\s*)?10\s*\^\s*"
    rf"(?P<exponent>[+\-−﹣－]?[0-9]+)"
)
AMBIGUOUS_MULTIPLICATION_RE = re.compile(
    rf"(?:(?:{RAW_DECIMAL_TOKEN})\s*)?×\s*10\s*"
    rf"[+\-−﹣－]\s*[0-9]+"
)
MINUS_TRANSLATION = str.maketrans({"−": "-", "﹣": "-", "－": "-"})
SUPERSCRIPT_TRANSLATION = str.maketrans(
    {
        "⁺": "+",
        "⁻": "-",
        "⁰": "0",
        "¹": "1",
        "²": "2",
        "³": "3",
        "⁴": "4",
        "⁵": "5",
        "⁶": "6",
        "⁷": "7",
        "⁸": "8",
        "⁹": "9",
    }
)


def numeric_key(raw: str, *, power: int = 0) -> str | None:
    """Return the exact decimal identity without binary-float conversion."""

    match = DECIMAL_LITERAL_RE.fullmatch(raw.translate(MINUS_TRANSLATION))
    if match is None:
        return None
    compact = match.group("mantissa").replace(",", "")
    integer, dot, fraction = compact.partition(".")
    if not dot:
        fraction = ""
    digits = (integer + fraction).lstrip("0")
    if not digits:
        return "dec:0"
    trailing_zeroes = len(digits) - len(digits.rstrip("0"))
    coefficient = digits.rstrip("0")
    exponent = int(match.group("exponent") or "0")
    exponent += power - len(fraction) + trailing_zeroes
    sign = "-" if match.group("sign") == "-" else ""
    return f"dec:{sign}{coefficient}e{exponent:+d}"


_UNIT_TOKEN = r"(?:[A-Za-z%‰°µμΩÅ/][^\s±=<>≤≥~,;]*)"
_SUPERSCRIPT_EXPONENT = "[⁺⁻]?[⁰¹²³⁴⁵⁶⁷⁸⁹]{1,6}"
_PLAIN_NUMBER_RE = re.compile(
    rf"^(?P<sign>[+-]?)(?P<mantissa>{DECIMAL_BODY})"
    r"(?:[eE](?P<exponent>[+-]?[0-9]{1,6})"
    r"|\s*(?:[×x*·⋅]|\\times)\s*10\s*"
    r"(?:\^\s*\{?\s*(?P<caret>[+-]?[0-9]{1,6})\s*\}?"
    rf"|(?P<superscript>{_SUPERSCRIPT_EXPONENT})))?"
    rf"(?:\s*(?:±|\+/-|\+-)\s*{DECIMAL_BODY}(?:[eE][+-]?[0-9]{{1,6}})?)?"
    rf"(?:\s*{_UNIT_TOKEN}){{0,4}}$"
)


def parse_plain_number(raw: str) -> float | int | None:
    """Return the value of one unambiguous number, else ``None``.

    Accepts an optional sign (including U+2212), thousands-grouped or plain
    decimals, ``e``/``×10^n``/``×10⁻ⁿ`` exponents, an optional ``± error``
    (the central value is returned) and up to four trailing unit tokens.
    Ranges, lists, decimal commas, ambiguous ``×10-3`` flattening and
    non-finite results are ambiguous and return ``None``.
    """

    text = unicodedata.normalize("NFC", raw).translate(MINUS_TRANSLATION).strip()
    text = text.replace("\u2009", " ").replace("\u00a0", " ")
    match = _PLAIN_NUMBER_RE.fullmatch(text)
    if match is None:
        return None
    exponent = 0
    if match.group("exponent") is not None:
        exponent = int(match.group("exponent"))
    elif match.group("caret") is not None:
        exponent = int(match.group("caret"))
    elif match.group("superscript") is not None:
        exponent = superscript_exponent(match.group("superscript")) or 0
    mantissa = match.group("mantissa").replace(",", "")
    try:
        value = Decimal(f"{match.group('sign')}{mantissa}E{exponent}")
    except InvalidOperation:
        return None
    result = float(value)
    if not math.isfinite(result):
        return None
    if result.is_integer() and abs(result) < 2**53:
        return int(result)
    return result


def identifier_character(value: str) -> bool:
    return value == "_" or value.isalnum() or value in {"⁺", "⁻"}


def expression_key(raw: str) -> str:
    normalized = unicodedata.normalize("NFKC", raw).translate(MINUS_TRANSLATION)
    return "expr:" + "".join(normalized.split())


def superscript_exponent(raw: str) -> int | None:
    normalized = raw.translate(SUPERSCRIPT_TRANSLATION)
    if not re.fullmatch(r"[+-]?[0-9]{1,6}", normalized):
        return None
    return int(normalized)


__all__ = [
    "AMBIGUOUS_MULTIPLICATION_RE",
    "CARET_EXPRESSION_RE",
    "MINUS_TRANSLATION",
    "NUMBER_TOKEN_RE",
    "RAW_DECIMAL_TOKEN",
    "expression_key",
    "identifier_character",
    "numeric_key",
    "parse_plain_number",
    "superscript_exponent",
]
