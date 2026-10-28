"""Strict JSON decoding shared by untrusted model and parser boundaries."""

from __future__ import annotations

import json
import math
from typing import Any


def strict_json_loads(value: str | bytes | bytearray) -> Any:
    """Decode RFC-style JSON while rejecting constants and duplicate keys."""

    return json.loads(
        value,
        parse_constant=_reject_constant,
        parse_float=_finite_float,
        object_pairs_hook=_unique_object,
    )


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value!r}")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("JSON number is outside the finite float range")
    return parsed


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


__all__ = ["strict_json_loads"]
