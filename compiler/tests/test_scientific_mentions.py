from __future__ import annotations

from decimal import Decimal

from compiler_service.graph_contract import Scope
from compiler_service.scientific_mentions import (
    latex_equation_scientific_mention_keys,
    scientific_mention_key_counts,
    scientific_mention_keys,
)


def _key(
    numeric: str = "dec:5e+0",
    *,
    comparator: str = "bare",
    unit: str = "bare",
) -> str:
    return f"mention:{numeric}|cmp:{comparator}|unit:{unit}"


def _opaque_unit(value: str) -> str:
    return "opaque:" + value.encode("utf-8").hex()


def _formula_variable(name: str) -> str:
    return f"formula-var:{name}"


def test_binds_normalized_explicit_units_without_conversion() -> None:
    mg = {_key(unit="mg")}
    kg = {_key(unit="kg")}
    assert scientific_mention_keys("5mg") == mg
    assert scientific_mention_keys("5 mg") == mg
    assert scientific_mention_keys("5毫克") == mg
    assert scientific_mention_keys("5kg") == kg
    assert scientific_mention_keys("5 千克") == kg
    assert mg != kg

    compound = {_key(unit="mg/kg")}
    assert scientific_mention_keys("5mg/kg") == compound
    assert scientific_mention_keys("5 mg / kg") == compound
    assert scientific_mention_keys("5毫克/千克") == compound

    assert scientific_mention_keys("5%") == {_key(unit="%")}
    assert scientific_mention_keys("5 percent") == {_key(unit="%")}
    assert scientific_mention_keys("5°C") == {_key(unit="degC")}
    assert scientific_mention_keys("5 摄氏度") == {_key(unit="degC")}
    assert scientific_mention_keys("5度") == {_key(unit="deg")}
    assert scientific_mention_keys("5nm") == {_key(unit="nm")}
    assert scientific_mention_keys("5 纳米") == {_key(unit="nm")}


def test_occurrence_counts_preserve_repeated_quantities() -> None:
    key = _key(unit="mg")
    assert scientific_mention_keys("5 mg then 5毫克") == {key}
    assert scientific_mention_key_counts("5 mg then 5毫克") == {key: 2}


def test_binds_symbols_words_and_chinese_comparison_direction() -> None:
    assert scientific_mention_keys("> 5 mg") == {_key(comparator="gt", unit="mg")}
    assert scientific_mention_keys("higher than 5mg") == {
        _key(comparator="gt", unit="mg")
    }
    assert scientific_mention_keys("高于5毫克") == {_key(comparator="gt", unit="mg")}
    assert scientific_mention_keys("< 5kg") == {_key(comparator="lt", unit="kg")}
    assert scientific_mention_keys("低于 5 千克") == {_key(comparator="lt", unit="kg")}
    assert scientific_mention_keys("≥5nm") == {_key(comparator="ge", unit="nm")}
    assert scientific_mention_keys("不低于5纳米") == {_key(comparator="ge", unit="nm")}
    assert scientific_mention_keys("≤ 5%") == {_key(comparator="le", unit="%")}
    assert scientific_mention_keys("不高于5百分比") == {_key(comparator="le", unit="%")}
    assert scientific_mention_keys("约为5°C") == {
        _key(comparator="approx", unit="degC")
    }
    assert scientific_mention_keys("5 mg以上") == {_key(comparator="ge", unit="mg")}
    assert scientific_mention_keys("the value was 5.") == {_key()}


def test_masks_navigation_and_freezes_unknown_or_complex_units() -> None:
    assert scientific_mention_keys(
        "Figure 15, Table 5, page 6, 图7和第8页；结果为9 mg。"
    ) == {_key("dec:9e+0", unit="mg")}
    assert scientific_mention_keys("Smith (2020), § 4 and refs. 10-12") == set()
    assert scientific_mention_keys("H2O sample A5 and model5foo") == set()
    assert scientific_mention_keys("[5] unknown and Figure 6 unknown") == set()
    unknown = {_key(unit=_opaque_unit("unknown"))}
    assert scientific_mention_keys("5unknown") == unknown
    assert scientific_mention_keys("5 unknown") == unknown
    assert scientific_mention_keys("5 mg/unknown") == {
        _key(unit=_opaque_unit("mg/unknown"))
    }
    assert scientific_mention_keys("5 mg/kg/s") == {_key(unit=_opaque_unit("mg/kg/s"))}

    concentration = scientific_mention_keys("5 µg/m³")
    assert concentration == {_key(unit=_opaque_unit("ug/m3"))}
    assert scientific_mention_keys("5 μg/m³") == concentration
    assert scientific_mention_keys("6 µg/m³") != concentration

    per_litre = scientific_mention_keys("5 mg·L−1")
    assert per_litre == {_key(unit=_opaque_unit("mg*L-1"))}
    assert scientific_mention_keys("5 mg·kg−1") != per_litre
    spaced_per_litre = scientific_mention_keys("5 mg L−1")
    assert spaced_per_litre == {_key(unit=_opaque_unit("mgL-1"))}
    assert scientific_mention_keys("5 mg kg−1") != spaced_per_litre
    assert scientific_mention_keys("5 mg/L^-1") == {_key(unit=_opaque_unit("mg/L^-1"))}


def test_preserves_high_precision_and_scientific_notation_exactly() -> None:
    exact = {_key("dec:123456789012345678901e-21", unit="mg")}
    assert scientific_mention_keys("0.123456789012345678901 mg") == exact
    assert scientific_mention_keys("123456789012345678901e-21mg") == exact

    tiny = {_key("dec:2e-120", unit="nm")}
    assert scientific_mention_keys("2.000e-120 nm") == tiny
    assert scientific_mention_keys(Decimal("2.000e-120")) == {_key("dec:2e-120")}
    assert scientific_mention_keys("2 × 10⁻³ mg") == {_key("dec:2e-3", unit="mg")}
    assert scientific_mention_keys("0.002mg") == {_key("dec:2e-3", unit="mg")}


def test_narrowly_normalizes_latex_equation_serialization() -> None:
    latex = (
        r"D ( \alpha ) = 1 8 0 ^ { \circ } + 2 \alpha ^ { - } "
        r"4 \beta\tag{}"
    )
    rendered = "D ( α ) = 180° + 2 α - 4 β"
    compact = "D(α)=180°+2α−4β"
    expected = {
        _key("dec:18e+1", comparator="eq", unit="deg"),
        _key("dec:2e+0", unit=_formula_variable("alpha")),
        _key("dec:4e+0", unit=_formula_variable("beta")),
    }

    assert latex_equation_scientific_mention_keys(latex) == expected
    assert scientific_mention_keys(latex) == expected
    assert scientific_mention_keys(rendered) == expected
    assert scientific_mention_keys(compact) == expected
    assert (
        latex_equation_scientific_mention_keys(r"\sin \alpha = n \sin \beta\tag{}")
        == set()
    )


def test_latex_scalar_stitching_is_suffix_bound_and_formula_safe() -> None:
    inline = scientific_mention_keys(
        r"Results were $4 3 . 4 8^{\circ}$, $1 4 . 5 1^{\circ}$, "
        r"$4 3 . 0 0^{\circ}$, and $5 6^{\circ}$."
    )
    assert inline == {
        _key("dec:4348e-2", unit="deg"),
        _key("dec:1451e-2", unit="deg"),
        _key("dec:43e+0", unit="deg"),
        _key("dec:56e+0", unit="deg"),
    }
    assert scientific_mention_keys(r"$L=\ 2 7 . 2 9 \ \mathrm{c m}$") == {
        _key("dec:2729e-2", comparator="eq", unit="cm")
    }
    celsius = scientific_mention_keys(r"$T=2 0^{\circ}\mathrm { C }$")
    assert celsius == {_key("dec:2e+1", comparator="eq", unit="degC")}
    assert scientific_mention_keys("T=20 °F") - celsius

    # A spaced run without an immediately recognized scalar suffix can be a
    # vector/list. A symbol-bound inline equation plus degree/unit suffix is
    # unambiguous enough to repair without touching ordinary prose lists.
    assert scientific_mention_keys(r"$A=[1 8 0]$") == set()
    assert _key("dec:18e+1", unit="deg") not in scientific_mention_keys(
        "ordinary prose 1 8 0 degrees"
    )
    assert scientific_mention_keys(
        r"The value \alpha = 1 8 0 ^ { \circ } was measured"
    ) == {_key("dec:18e+1", comparator="eq", unit="deg")}
    assert scientific_mention_keys(r"$x=5\tag{18}$") == {_key(comparator="eq")}
    assert scientific_mention_keys("x=-4 mg") == {
        _key("dec:-4e+0", comparator="eq", unit="mg")
    }


def test_latex_aliases_match_rendered_scientific_mentions() -> None:
    equation = r"\begin{equation}D(\alpha)=180^{\circ}+2\alpha-4\beta\end{equation}"
    assert scientific_mention_keys(equation) == scientific_mention_keys(
        "D(α)=180°+2α−4β"
    )
    assert scientific_mention_keys(r"$x\leq 5\,\mathrm{mg}$") == (
        scientific_mention_keys("x≤5 mg")
    )
    assert scientific_mention_keys(r"$m=\frac{10}{2}\,\mathrm{mg}$") == (
        scientific_mention_keys("m=10/2 mg")
    )
    assert scientific_mention_keys(
        r"The relation is D(\alpha)=1 8 0^{\circ}+2\alpha-4\beta."
    ) == scientific_mention_keys("D(α)=180°+2α−4β")


def test_math_islands_respect_escaped_dollar_parity_and_multiple_islands() -> None:
    stitched = {_key("dec:18e+1", unit="mg")}
    assert _key("dec:18e+1", unit="mg") not in scientific_mention_keys(r"\$1 8 0 mg\$")
    assert scientific_mention_keys(r"\\$1 8 0 mg\\$") == stitched
    assert _key("dec:18e+1", unit="mg") not in scientific_mention_keys(
        r"\\\$1 8 0 mg\\\$"
    )
    assert scientific_mention_keys(r"\\\\$1 8 0 mg\\\\$") == stitched
    assert scientific_mention_keys(
        r"$1 8 0 mg$ and $4 3 . 4 8^{\circ}$"
    ) == stitched | {_key("dec:4348e-2", unit="deg")}


def test_outer_closing_delimiters_terminate_recognized_units() -> None:
    assert scientific_mention_keys("mass (5 mg)") == {_key(unit="mg")}
    assert scientific_mention_keys("[5 nm]") == {_key(unit="nm")}
    assert scientific_mention_keys("5 m/s]") == {_key(unit="m/s")}
    assert scientific_mention_keys("x=(5 mg)") == {_key(comparator="eq", unit="mg")}
    assert scientific_mention_keys("x=[5 mg]") == {_key(comparator="eq", unit="mg")}
    assert scientific_mention_keys("less than (5 mg)") == {
        _key(comparator="lt", unit="mg")
    }
    assert scientific_mention_keys("condition (5 mg)") == {_key(unit="mg")}

    # Delimiters that begin inside the unit remain part of a frozen opaque
    # expression; only an outer trailing closer terminates a recognized unit.
    assert scientific_mention_keys("5 mg/(kg)") == {_key(unit=_opaque_unit("mg/(kg)"))}
    assert scientific_mention_keys("5 mg[protein]") == {
        _key(unit=_opaque_unit("mg[protein]"))
    }

    parenthesized_celsius = scientific_mention_keys(r"$T=(2 0^{\circ}\mathrm { C })$")
    assert parenthesized_celsius == {_key("dec:2e+1", comparator="eq", unit="degC")}
    assert scientific_mention_keys(r"$T=[2 0^{\circ}\mathrm { C }]$") == (
        parenthesized_celsius
    )


def test_formula_variables_are_local_and_physical_units_win() -> None:
    assert scientific_mention_keys("a 2x increase") == {
        _key("dec:2e+0", unit=_opaque_unit("x"))
    }
    assert scientific_mention_keys("2X magnification") == {
        _key("dec:2e+0", unit=_opaque_unit("X"))
    }
    assert scientific_mention_keys(r"formula prose 2\alpha \tag{18}") == {
        _key("dec:2e+0", unit=_formula_variable("alpha"))
    }
    assert scientific_mention_keys(r"$y=2x-3\gamma$") == {
        _key("dec:2e+0", comparator="eq", unit=_formula_variable("x")),
        _key("dec:3e+0", unit=_formula_variable("gamma")),
    }
    pure = {
        _key("dec:2e+0", unit=_formula_variable("x")),
        _key("dec:3e+0", unit=_formula_variable("y")),
    }
    assert scientific_mention_keys("2x-3y") == pure
    assert latex_equation_scientific_mention_keys("2x-3y") == pure
    assert scientific_mention_keys("-2x+3y") == {
        _key("dec:-2e+0", unit=_formula_variable("x")),
        _key("dec:3e+0", unit=_formula_variable("y")),
    }
    assert scientific_mention_keys("2x-4y") - pure
    assert scientific_mention_keys("2y-3x") - pure
    assert scientific_mention_keys("I=5 A") == {_key(comparator="eq", unit="A")}
    assert scientific_mention_keys("I=5 Ω") == {_key(comparator="eq", unit="ohm")}
    assert scientific_mention_keys("P=5 W") == {_key(comparator="eq", unit="W")}
    assert scientific_mention_keys("1e-3 mg") == {_key("dec:1e-3", unit="mg")}
    assert scientific_mention_keys("x=2e-3 mg") == {
        _key("dec:2e-3", comparator="eq", unit="mg")
    }
    assert scientific_mention_keys(r"$d=5\,\mathrm { ~ n m }$") == {
        _key(comparator="eq", unit="nm")
    }

    mixed = r"F=2n+3x+4\lambda+5λ+6γ"
    assert scientific_mention_keys(mixed) == {
        _key("dec:2e+0", comparator="eq", unit=_formula_variable("n")),
        _key("dec:3e+0", unit=_formula_variable("x")),
        _key("dec:4e+0", unit=_formula_variable("lambda")),
        _key("dec:5e+0", unit=_formula_variable("lambda")),
        _key("dec:6e+0", unit=_formula_variable("gamma")),
    }


def test_outer_braced_latex_unit_hydrates_only_its_scalar() -> None:
    equation = (
        r"d _ { 1 } = { \frac { 2 k - 1 } { 4 n } } \lambda = "
        r"{ \frac { 2 - 1 } { 4 { \times } 1 . 3 3 } } { \times } "
        r"6 2 2 . 5 = 1 1 6 . 7 { \mathrm { ~ n m } }\tag{11}"
    )
    forced = latex_equation_scientific_mention_keys(equation)
    generic = scientific_mention_keys(equation)

    assert forced == generic
    assert _key("dec:1167e-1", comparator="eq", unit="nm") in forced
    assert _key("dec:133e-2") in forced
    assert _key("dec:6225e-1") in forced
    assert _key("dec:133e-2", unit="nm") not in forced
    assert _key("dec:6225e-1", unit="nm") not in forced
    assert scientific_mention_keys("1.34") - forced
    assert scientific_mention_keys("622.6") - forced


def test_latex_equations_reject_semantic_changes_and_evaluation() -> None:
    source = latex_equation_scientific_mention_keys(
        r"D(\alpha)=180^{\circ}+2\alpha-4\beta"
    )
    for changed in (
        "D(α)=181°+2α−4β",
        "D(α)<180°+2α−4β",
        "D(α)=180°+3α−4β",
        "D(α)=180°+2β−4β",
    ):
        assert scientific_mention_keys(changed) - source

    measured = latex_equation_scientific_mention_keys(r"m=5\,\mathrm{mg}")
    assert not scientific_mention_keys("m = 5 mg") - measured
    assert scientific_mention_keys("m = 5 kg") - measured

    unevaluated = latex_equation_scientific_mention_keys(r"m=\frac{10}{2}\,\mathrm{mg}")
    assert scientific_mention_keys("m = 5 mg") - unevaluated


def test_scans_only_scientific_scope_fields() -> None:
    scope = Scope(
        id="scope.local.5",
        location="station 12 km offshore",
        time="2026",
        formation=None,
        sample_type="5 mg specimen",
        method="method77",
        condition="temperature above 5 °C",
        measurement="diameter 5纳米",
        uncertainty="below 5%",
        entities=["cohort 5"],
    )

    keys = scientific_mention_keys(scope)
    assert _key("dec:12e+0", unit="km") in keys
    assert _key(unit="mg") in keys
    assert _key(comparator="gt", unit="degC") in keys
    assert _key(unit="nm") in keys
    assert _key(comparator="lt", unit="%") in keys
    assert _key() in keys
    # Scope.id is intentionally excluded, and embedded alphanumeric labels do
    # not donate their numeric suffixes.
    assert _key("dec:77e+0") not in keys

    assert scientific_mention_keys(scope.model_dump(mode="python")) == keys
