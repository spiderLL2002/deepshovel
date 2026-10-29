"""Input transport checks; scientific outcomes are evaluated with live models separately."""
from copy import deepcopy

import pytest
from pydantic import ValidationError
from test_retrieval_task_contract import document
from test_retrieval_task_contract_provider import _ready

from compiler_service.retrieval_contract import HybridSearchRequest, RetrievalArgumentContext
from compiler_service.retrieval_providers import compiled_context_relation_input, context_task_projection, retrieval_search_policy


def argument(doc):
    return {
        "snapshot_id": doc.snapshot_id, "claim_id": doc.claim_id,
        "nodes": [
            {"id": doc.claim_id, "kind": "atomic_claim", "statement": doc.canonical_claim, "scope": None, "anchor_ids": []},
            {"id": "assumption:friction", "kind": "assumption", "statement": "The friction coefficient is 0.6.", "scope": None, "anchor_ids": []},
        ],
        "edges": [{"source_id": "assumption:friction", "target_id": doc.claim_id, "relation": "assumption_for"}],
        "truncated": False,
    }


def test_final_model_input_retains_assumptions_and_dependency_edges():
    req, context, _, _, frozen = _ready()
    doc = document()
    raw = req.model_dump(mode="json")
    raw.update(stage="ai_rerank", query_plan_handoff="v1.testkey.testingpayload." + "a" * 43, candidate_refs=[{"snapshot_id": doc.snapshot_id, "claim_id": doc.claim_id}],
               argument_contexts=[argument(doc)])
    final = HybridSearchRequest.model_validate(raw)
    assert context_task_projection(req, context=context) == context_task_projection(final, context=context)
    with retrieval_search_policy(interactive=True):
        payload = compiled_context_relation_input(final, context=context, frozen_reference=frozen, candidates=[doc])
    assert payload["query"] == req.query
    assert payload["candidates"][0]["argument"] == argument(doc)
    changed = deepcopy(raw)
    changed["argument_contexts"][0]["nodes"][1]["statement"] = "The friction coefficient is 0.4."
    with retrieval_search_policy(interactive=True):
        other = compiled_context_relation_input(HybridSearchRequest.model_validate(changed), context=context,
                                               frozen_reference=frozen, candidates=[doc])
    assert payload != other


def test_argument_requires_real_endpoints_and_root_when_complete():
    raw = argument(document())
    raw["edges"][0]["source_id"] = "assumption:absent"
    with pytest.raises(ValidationError, match="endpoints"):
        RetrievalArgumentContext.model_validate(raw)
    raw = argument(document())
    raw["nodes"] = []
    raw["edges"] = []
    with pytest.raises(ValidationError, match="contain its claim"):
        RetrievalArgumentContext.model_validate(raw)
    raw["truncated"] = True
    assert RetrievalArgumentContext.model_validate(raw).truncated


def test_argument_cannot_be_injected_for_unrequested_snapshot():
    req, _, _, _, _ = _ready()
    raw = req.model_dump(mode="json")
    raw.update(stage="ai_rerank", argument_contexts=[argument(document())])
    with pytest.raises(ValidationError, match="requested claims"):
        HybridSearchRequest.model_validate(raw)
