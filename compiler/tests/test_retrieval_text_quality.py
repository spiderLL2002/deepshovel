"""Regression cases for CJK tokenization, negation, time units and ontology."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from compiler_service.retrieval_ontology import default_retrieval_ontology
from compiler_service.retrieval_providers import _NUMERIC_TIME_QUALIFIERS
from compiler_service.retrieval_text import (
    fts_query,
    has_negation,
    lexical_tokens,
    time_interval_years,
    time_unit_factor,
)


def test_single_character_cjk_words_survive_mixed_queries() -> None:
    # 汞 (mercury) is a word of its own; it used to vanish whenever the
    # query also had a CJK bigram.
    assert fts_query(["汞 异常 二叠纪"]) == '"汞" OR "异常" OR "二叠" OR "叠纪"'
    # Characters inside a longer run stay covered by its bigrams.
    assert fts_query(["汞异常"]) == '"汞异" OR "异常"'
    assert fts_query(["铁"]) == '"铁"'


def test_capital_delta_decimals_and_ratios_stay_distinct_tokens() -> None:
    assert lexical_tokens("Δ17O versus δ18O") == ["Δ17o", "versus", "δ18o"]
    assert lexical_tokens("∆17O") == ["Δ17o"]
    assert lexical_tokens("87Sr/86Sr = 0.7045") == [
        "87sr/86sr",
        "87sr",
        "86sr",
        "0.7045",
    ]
    # A decimal is one FTS phrase, never a bare "0" OR clause.
    assert '"0"' not in fts_query(["0.7045"])
    assert lexical_tokens("Fig. 3. The end.") == ["fig", "3", "the", "end"]


@pytest.mark.parametrize(
    ("query", "negated"),
    (
        ("无机碳 的 埋藏", False),
        ("不同 时期 的 汞 异常", False),
        ("未来 气候 变化", False),
        ("无氧 环境", False),
        ("汞异常 不是 火山 成因", True),
        ("未发现 显著 相关", True),
        ("没有 证据 支持", True),
        ("Hg anomalies are not volcanic", True),
        ("The anomaly doesn't track volcanism", True),
        ("Sample no. 3 is enriched", False),
        ("Island populations notably differ", False),
    ),
)
def test_negation_requires_an_explicit_predicate_negation(query, negated) -> None:
    assert has_negation(query) is negated


@pytest.mark.parametrize(
    ("unit", "factor"),
    (
        ("Ma", 1e6),
        ("万年", 1e4),
        ("亿年", 1e8),
        ("百万年", 1e6),
        ("ka BP", 1e3),
        ("cal. BP", 1.0),
        ("yr BP", 1.0),
        ("Gyr", 1e9),
    ),
)
def test_time_units_include_chinese_and_before_present(unit, factor) -> None:
    assert time_unit_factor(unit) == factor


def test_time_intervals_are_ordered_and_unknown_units_are_none() -> None:
    assert time_interval_years(66, 56, "Ma") == (56e6, 66e6)
    assert time_interval_years(2.5, None, "亿年")[0] == 2.5e8
    assert time_interval_years(1, 2, "fortnights") is None


def test_time_qualifier_约_needs_a_following_number() -> None:
    assert _NUMERIC_TIME_QUALIFIERS.search("约 66 Ma")
    assert _NUMERIC_TIME_QUALIFIERS.search("约66百万年")
    assert not _NUMERIC_TIME_QUALIFIERS.search("年龄约束为 66 Ma")
    assert not _NUMERIC_TIME_QUALIFIERS.search("受制约的 66 Ma 事件")


def test_ontology_regression_false_positives_stay_fixed() -> None:
    ontology = default_retrieval_ontology()
    data = json.loads(
        (
            Path(__file__).parents[1] / "compiler_service" / "retrieval_ontology.json"
        ).read_text(encoding="utf-8")
    )
    cases = data["regression_false_positives"]
    assert cases
    for case in cases:
        concepts = {item.concept_id for item in ontology.concepts_in(case["text"])}
        assert case["concept"] not in concepts, case


@pytest.mark.parametrize(
    ("text", "concept"),
    (
        ("Mid-Atlantic Ridge (MAR) basalts", "geo:mid-atlantic-ridge"),
        ("南海北部陆坡", "geo:south-china-sea"),
        ("早期成岩作用", "process:diagenetic"),
        ("REE patterns", "chem:ree"),
        ("ICP-MS analysis", "method:icp-ms"),
        ("total organic carbon (TOC)", "measurement:toc"),
    ),
)
def test_ontology_keeps_true_matches(text, concept) -> None:
    ontology = default_retrieval_ontology()
    assert concept in {item.concept_id for item in ontology.concepts_in(text)}
    assert ontology.query_scoped_concept_ids(text, {concept}) == {concept}


def test_ambiguous_acronyms_need_context_and_acronyms_are_case_sensitive() -> None:
    ontology = default_retrieval_ontology()
    assert not ontology.query_scoped_concept_ids(
        "MAR of organic carbon", {"geo:mid-atlantic-ridge"}
    )
    assert not ontology.query_scoped_concept_ids("free ree", {"chem:ree"})
    assert ontology.query_scoped_concept_ids("REE", {"chem:ree"}) == {"chem:ree"}


def test_reversed_filter_interval_is_ordered_instead_of_rejected() -> None:
    from compiler_service.retrieval_contract import SearchScopeFilter

    scope = SearchScopeFilter.model_validate(
        {"time": {"label": None, "start": 66, "end": 56, "unit": "Ma"}},
        strict=True,
    )
    assert (scope.time.start, scope.time.end) == (56, 66)
