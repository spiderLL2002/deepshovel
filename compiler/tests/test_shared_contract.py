"""Cross-language contract parity (contracts/shared-contract.json).

``node scripts/generate-shared-contract.mjs --check`` (run by the Node suite)
proves the generated modules are current; these checks prove the Compiler's
hand-written Literals still agree with them and that both languages give the
same verdict on the golden cases.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from compiler_service import (
    canonical,
    claim_comparison,
    graph_contract,
    retrieval_contract,
    retrieval_service,
    shared_contract,
)
from compiler_service.models import BoundingBox

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = json.loads((ROOT / "contracts/shared-contract.json").read_text("utf-8"))
CASES = json.loads((ROOT / "contracts/shared-contract-cases.json").read_text("utf-8"))


def _leaves(value, path=()):
    if isinstance(value, dict):
        for key, item in value.items():
            if not key.startswith("$"):
                yield from _leaves(item, (*path, key))
    else:
        yield path, tuple(value) if isinstance(value, list) else value


def test_generated_module_carries_every_contract_value_and_nothing_else():
    contract_values = {value for _, value in _leaves(CONTRACT)}
    module_values = {
        value
        for name, value in vars(shared_contract).items()
        if name.isupper() and not name.startswith("_")
    }
    assert contract_values == module_values


def test_hand_written_literals_agree_with_the_contract():
    graph = CONTRACT["evidence_graph"]
    retrieval = CONTRACT["retrieval"]
    for module in (graph_contract, retrieval_contract, claim_comparison):
        assert get_args(module.ClaimType) == tuple(graph["claim_types"])
        assert get_args(module.ClaimPolarity) == tuple(graph["claim_polarities"])
    for module in (retrieval_contract, claim_comparison):
        assert get_args(module.ClaimModality) == tuple(graph["claim_modalities"])
    assert graph["schema_version"] == canonical.SCHEMA_VERSION
    assert (
        set(graph["reasoning_conclusion_relations"])
        == canonical.REASONING_CONCLUSION_RELATIONS
    )
    assert (
        CONTRACT["compiler"]["extraction_prompt_version"]
        == canonical.EXTRACTION_PROMPT_VERSION
    )
    assert (
        CONTRACT["compiler"]["verification_prompt_version"]
        == canonical.VERIFICATION_PROMPT_VERSION
    )
    live = (
        retrieval["index_version"],
        retrieval["search_protocol_version"],
        retrieval["context_protocol_version"],
    )
    assert get_args(retrieval_contract.IndexVersion) == (retrieval["index_version"],)
    assert get_args(retrieval_contract.SearchSchemaVersion) == live
    handoff_versions = get_args(
        retrieval_contract.QueryPlanHandoffPayload.model_fields[
            "retrieval_schema_version"
        ].annotation
    )
    assert handoff_versions == live[1:]
    for retired in retrieval["retired_protocol_versions"]:
        assert retired not in live
    assert (
        retrieval["continuation_key_id"]
        == retrieval_service._QUERY_PLAN_HANDOFF_KEY_ID
    )


@pytest.mark.parametrize(
    "case", CASES["normalized_regions"], ids=lambda case: case["name"]
)
def test_normalized_region_verdicts_match_the_web(case):
    x, y, width, height = case["region"]
    outcomes = []
    for validate in (
        lambda: graph_contract.Region.model_validate(
            {"x": x, "y": y, "width": width, "height": height, "unit": "normalized"},
            strict=True,
        ),
        lambda: BoundingBox.model_validate(
            {
                "x": float(x),
                "y": float(y),
                "width": float(width),
                "height": float(height),
            },
            strict=True,
        ),
    ):
        try:
            validate()
            outcomes.append(True)
        except ValidationError:
            outcomes.append(False)
    assert outcomes == [case["valid"], case["valid"]]


@pytest.mark.parametrize("case", CASES["retrieval_session_ids"])
def test_session_id_pattern_matches_the_web(case):
    pattern = re.compile(CONTRACT["retrieval_sessions"]["id_pattern"])
    assert bool(pattern.fullmatch(case["value"])) is case["valid"]
