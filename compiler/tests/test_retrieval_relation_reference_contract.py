"""Bounded offline admission tests for the internal 2.3 relation wire."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from compiler_service.retrieval_contract import (
    ContextRelationInterpretation,
    RelationDecisionBatch,
)


def interpretation(kind="single_proposition"):
    return {
        "kind": kind,
        "proposition": "Does supplied P hold?"
        if kind == "single_proposition"
        else None,
        "polarity": None,
        "entities": [],
        "scope": None,
    }


def decision(relation="related"):
    return {
        "candidate_key": "candidate:a",
        "relation": relation,
        "relation_direction": "result_to_query",
        "relation_confidence": 0.5,
        "scope_relation": "unknown",
    }


@pytest.mark.parametrize(
    "kind", ["single_proposition", "non_propositional", "missing_context"]
)
def test_explicit_interpretation_branches(kind):
    value = ContextRelationInterpretation.model_validate(
        interpretation(kind), strict=True
    )
    assert value.kind == kind


@pytest.mark.parametrize(
    ("kind", "update"),
    [
        ("single_proposition", {"proposition": None}),
        ("single_proposition", {"proposition": "e\u0301"}),
        ("single_proposition", {"scope": {}}),
        ("single_proposition", {"entities": ["P"] * 65}),
        ("non_propositional", {"polarity": "affirmed"}),
        ("non_propositional", {"proposition": "One selected subquestion"}),
        ("missing_context", {"entities": ["Invented source"]}),
        ("missing_context", {"scope": {"location": ["Place"]}}),
    ],
)
def test_interpretation_does_not_fill_or_normalize_invalid_content(kind, update):
    raw = {**interpretation(kind), **update}
    before = deepcopy(raw)
    with pytest.raises(ValidationError):
        ContextRelationInterpretation.model_validate(raw, strict=True)
    assert raw == before


def test_new_reference_fields_do_not_change_legacy_wire():
    legacy = {"decisions": [decision("supports")]}
    assert (
        RelationDecisionBatch.model_validate(legacy).decisions[0].relation == "supports"
    )
    with pytest.raises(ValidationError):
        RelationDecisionBatch.model_validate(
            {**legacy, "task_reference_sha256": "a" * 64}
        )
