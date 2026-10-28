"""Declared numeric-time arithmetic and per-search resource accounting."""

import hashlib
import time

import pytest
from test_retrieval_service import _document

from compiler_service.graph_contract import canonical_json
from compiler_service.retrieval_contract import (
    ClaimSearchDocument,
    ContextRelationInterpretation,
    FrozenCompiledContextTaskReference,
    relation_candidate_key,
)
from compiler_service.retrieval_providers import (
    context_planning_identity,
    numeric_time_compatibility_fact,
    retrieval_search_budget,
)


def test_search_budget_context_does_not_cross_user_wait():
    with retrieval_search_budget(max_completion_calls=2) as preview:
        preview.begin_completion()
    with retrieval_search_budget(
        max_completion_calls=1, started_at=time.monotonic()
    ) as final:
        assert final is not preview
        assert final.usage.completion_calls == 0


def _time_fixture(*, left=None, right=None, proposition="P holds.", claim="Q holds."):
    left = left or {"label": None, "start": 10, "end": 20, "unit": "Ma"}
    right = right or {"label": None, "start": 30, "end": 40, "unit": "Ma"}
    # Arithmetic consumes the declared projection only. The current sealed wire
    # shape is used here; execution-level compilation is covered separately.
    interpretation = ContextRelationInterpretation.model_validate(
        {
            "kind": "single_proposition",
            "proposition": proposition,
            "polarity": None,
            "entities": [],
            "scope": {"time": left},
        }
    )
    reference = FrozenCompiledContextTaskReference(
        interpreter_identity_sha256="a" * 64,
        task_input_sha256="b" * 64,
        interpretation_sha256=hashlib.sha256(
            canonical_json(interpretation.model_dump(mode="json")).encode()
        ).hexdigest(),
        interpretation=interpretation,
        proposal_json="{}",
        compiled_task_sha256="c" * 64,
    )
    candidate = ClaimSearchDocument.model_validate(
        _document("candidate", claim, time=right)
    )
    return reference, candidate


def test_numeric_time_fact_preserves_declared_endpoints_and_identity():
    reference, candidate = _time_fixture()
    fact = numeric_time_compatibility_fact(reference, candidate)
    assert fact == {
        "rule_version": "deepshovel-declared-numeric-time/1.1",
        "reference_sha256": reference.reference_sha256,
        "candidate_key": relation_candidate_key(
            candidate.snapshot_id, candidate.claim_id
        ),
        "reference_time": {"label": None, "start": 10, "end": 20, "unit": "Ma"},
        "candidate_time": {"label": None, "start": 30, "end": 40, "unit": "Ma"},
        "declared_interval_relation": "disjoint",
    }


def test_numeric_time_missing_bounds_and_free_text_never_infer_disjoint():
    for left in (
        {"label": None, "start": 10, "end": None, "unit": "Ma"},
        {"label": "Jurassic", "start": None, "end": None, "unit": None},
    ):
        reference, candidate = _time_fixture(left=left)
        assert (
            numeric_time_compatibility_fact(reference, candidate)[
                "declared_interval_relation"
            ]
            == "unknown"
        )
    reference, candidate = _time_fixture()
    candidate.scope.time = None
    assert (
        numeric_time_compatibility_fact(reference, candidate)[
            "declared_interval_relation"
        ]
        == "unknown"
    )


def test_numeric_time_different_or_unrecognized_units_are_unknown():
    for unit in ("ka", "Ga", "ma", "million years", "years"):
        reference, candidate = _time_fixture(
            right={"label": None, "start": 30, "end": 40, "unit": unit}
        )
        assert (
            numeric_time_compatibility_fact(reference, candidate)[
                "declared_interval_relation"
            ]
            == "unknown"
        )


def test_numeric_time_full_text_approximation_overrides_precise_structured_scope():
    for qualifier in (
        "approximately",
        "about",
        "ca.",
        "circa",
        "约",
        "至少",
        "before",
        "after",
        "<",
        ">",
        "≤",
        "≥",
        "~",
        "≈",
        "±",
    ):
        for source in ("proposition", "claim", "label"):
            kwargs = (
                {source: f"P {qualifier} 10 Ma."}
                if source != "label"
                else {
                    "right": {
                        "label": f"{qualifier} 30 Ma",
                        "start": 30,
                        "end": 40,
                        "unit": "Ma",
                    }
                }
            )
            reference, candidate = _time_fixture(**kwargs)
            assert (
                numeric_time_compatibility_fact(reference, candidate)[
                    "declared_interval_relation"
                ]
                == "unknown"
            ), (source, qualifier)


def test_numeric_time_overlap_touching_and_reversed_interval():
    for start, end, expected in (
        (15, 25, "interval_overlap"),
        (20, 30, "unknown"),
        (40, 30, "unknown"),
    ):
        reference, candidate = _time_fixture(
            right={"label": None, "start": start, "end": end, "unit": "Ma"}
        )
        assert (
            numeric_time_compatibility_fact(reference, candidate)[
                "declared_interval_relation"
            ]
            == expected
        )


def test_numeric_time_nonfinite_injected_endpoint_rejected_before_payload():
    reference, candidate = _time_fixture()
    # Defend the helper even against model_copy bypassing ordinary typed admission.
    candidate.scope.time = candidate.scope.time.model_copy(
        update={"start": float("nan")}
    )
    with pytest.raises(ValueError, match="finite"):
        numeric_time_compatibility_fact(reference, candidate)


def test_numeric_time_rule_identity_changes_only_context_fingerprint(monkeypatch):
    import compiler_service.retrieval_providers as providers

    context = context_planning_identity()
    monkeypatch.setattr(providers, "_NUMERIC_TIME_RULE_VERSION", "changed-rule")
    changed = context_planning_identity()
    assert changed != context
    assert changed["task_interpreter"] == context["task_interpreter"]
