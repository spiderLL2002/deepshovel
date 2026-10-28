"""Conservative list-layout normalization must preserve scientific content."""

from __future__ import annotations

import pytest

from compiler_service import scientific_mentions as mentions


@pytest.mark.parametrize(
    "markers",
    [
        ("(1)", "(2)"),
        ("1.", "2."),
        ("1)", "2)"),
        ("1、", "2、"),
        ("（１）", "（２）"),
        ("１．", "２．"),
        ("１）", "２）"),
        ("１、", "２、"),
    ],
)
def test_numbered_prose_has_the_same_keys_and_counts_as_unlabelled_prose(markers):
    bodies = ("Compare the measured 5 mg.", "Retain the measured 5 mg.")
    numbered = "\n".join(
        f"{marker} {body}" for marker, body in zip(markers, bodies, strict=True)
    )
    plain = "\n".join(bodies)
    assert mentions.scientific_mention_keys(numbered) == {
        "mention:dec:5e+0|cmp:bare|unit:mg"
    }
    assert (
        mentions.scientific_mention_key_counts(numbered)
        == (mentions.scientific_mention_key_counts(plain))
        == {"mention:dec:5e+0|cmp:bare|unit:mg": 2}
    )


def test_mask_changes_only_marker_characters_and_preserves_offsets():
    source = "  （１） 比较含量 5 mg。\r\n  （２） 保留含量 7 mg。"
    expected = "      比较含量 5 mg。\r\n      保留含量 7 mg。"
    masked = mentions._mask_ordered_list_markers(source)
    assert masked == expected
    assert len(masked) == len(source)
    for body in ("比较含量 5 mg。", "保留含量 7 mg。", "\r\n"):
        assert masked.index(body) == source.index(body)


def test_blank_lines_can_separate_items_but_keep_their_exact_bytes():
    source = "(1) Compare the samples.\n \t\n(2) Retain the evidence."
    expected = "    Compare the samples.\n \t\n    Retain the evidence."
    assert mentions._mask_ordered_list_markers(source) == expected
    assert mentions.scientific_mention_keys(source) == set()


@pytest.mark.parametrize(
    "source",
    [
        "(1) Compare the samples.\nAn ordinary paragraph.\n(2) Retain evidence.",
        "(1) Compare the samples.",
        "(2) Compare the samples.\n(3) Retain the evidence.",
        "(1) Compare the samples.\n(3) Retain the evidence.",
        "(1) Compare the samples.\n(1) Retain the evidence.",
        "(2) Compare the samples.\n(1) Retain the evidence.",
        "(01) Compare the samples.\n(02) Retain the evidence.",
        "(1) Compare the samples.\n  (2) Retain the evidence.",
        "(1) Compare the samples.\n2) Retain the evidence.",
        "1. Compare the samples.\n2、 Retain the evidence.",
        "1.Compare the samples.\n2.Retain the evidence.",
        "(1)\n(2)",
        "1.\n2.",
        "First (1) compare samples; then (2) retain evidence.",
    ],
)
def test_uncertain_or_non_list_layout_is_unchanged(source):
    assert mentions._mask_ordered_list_markers(source) == source


@pytest.mark.parametrize(
    "source",
    [
        "(1) mg\n(2) kg",
        "1. mg\n2. kg",
        "（１） 毫克\n（２） 千克",
        "(5) mg",
        "1.5 mg\n2.5 mg",
        "-1. sample\n-2. sample",
        "+1. sample\n+2. sample",
        "-1 mg\n-2 mg",
        "1\n2",
        "1) 13C\n2) 14C",
        "(1) 5 mg\n(2) 6 mg",
    ],
)
def test_bare_numbers_units_decimals_signs_and_isotopes_are_not_markers(source):
    assert mentions._mask_ordered_list_markers(source) == source


@pytest.mark.parametrize(
    "source",
    [
        "(1)+(2)",
        "(1) x\n(2) y",
        "(1) + x\n(2) + y",
        "(1) = x\n(2) = y",
        "(1) α\n(2) β",
    ],
)
def test_formula_operands_and_variables_are_not_markers(source):
    assert mentions._mask_ordered_list_markers(source) == source


@pytest.mark.parametrize(
    "opening,closing",
    [
        ("```text\n", "\n```"),
        ("~~~\n", "\n~~~"),
        ("$$\n", "\n$$"),
        ("\\[\n", "\n\\]"),
        ("\\begin{align}\n", "\n\\end{align}"),
        ("$$\n", ""),
        ("```text\n", ""),
    ],
)
def test_explicit_code_and_math_blocks_preserve_list_shaped_content(opening, closing):
    source = opening + "(1) Compare the measured 5 mg.\n(2) Retain the 7 mg." + closing
    assert mentions._mask_ordered_list_markers(source) == source


def test_indented_code_is_not_reinterpreted_as_a_numbered_list():
    source = "    (1) Compare the measured 5 mg.\n    (2) Retain the 7 mg."
    assert mentions._mask_ordered_list_markers(source) == source


def test_equation_entrypoint_never_invokes_prose_list_mask(monkeypatch):
    def forbidden(value):
        raise AssertionError("typed equation entered prose layout normalization")

    monkeypatch.setattr(mentions, "_mask_ordered_list_markers", forbidden)
    assert mentions.latex_equation_scientific_mention_keys("(1)+(2)") == {
        "mention:dec:1e+0|cmp:bare|unit:bare",
        "mention:dec:2e+0|cmp:bare|unit:bare",
    }


def test_mask_does_not_hide_mutated_body_quantities_or_occurrence_counts():
    original = "(1) Compare the measured 5 mg.\n(2) Retain the measured 5 mg."
    mutated = "(1) Compare the measured 5 mg.\n(2) Retain the measured 7 mg."
    assert mentions.scientific_mention_key_counts(original) == {
        "mention:dec:5e+0|cmp:bare|unit:mg": 2
    }
    assert mentions.scientific_mention_key_counts(mutated) == {
        "mention:dec:5e+0|cmp:bare|unit:mg": 1,
        "mention:dec:7e+0|cmp:bare|unit:mg": 1,
    }
    assert mentions.scientific_mention_keys(mutated) - mentions.scientific_mention_keys(
        original
    ) == {"mention:dec:7e+0|cmp:bare|unit:mg"}


def test_list_body_inline_math_preserves_formula_keys_and_counts():
    body_one = r"Compare the equation $x=5\,\mathrm{mg}$."
    body_two = r"Retain the equation $y=7\,\mathrm{kg}$."
    numbered = f"(1) {body_one}\n(2) {body_two}"
    plain = f"{body_one}\n{body_two}"
    assert mentions._mask_ordered_list_markers(numbered) == (
        f"    {body_one}\n    {body_two}"
    )
    expected = {
        "mention:dec:5e+0|cmp:eq|unit:mg": 1,
        "mention:dec:7e+0|cmp:eq|unit:kg": 1,
    }
    assert (
        mentions.scientific_mention_key_counts(numbered)
        == (mentions.scientific_mention_key_counts(plain))
        == expected
    )


@pytest.mark.parametrize(
    "source",
    [
        "(1) Sr/Ca\n(2) Sr/Ca",
        "(1) 毫克浓度\n(2) 千克质量",
        "`start\n(1) Compare 5 mg.\n(2) Retain 7 mg.\nend`",
    ],
)
def test_unit_prefixes_and_multiline_code_do_not_lose_real_numeric_keys(source):
    assert mentions._mask_ordered_list_markers(source) == source
    assert {
        "mention:dec:1e+0|cmp:bare|unit:bare",
        "mention:dec:2e+0|cmp:bare|unit:bare",
    } <= mentions.scientific_mention_keys(source)
