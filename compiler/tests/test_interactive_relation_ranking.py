"""Small numbered relation wire; no live model calls."""

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_retrieval_providers import _response
from test_retrieval_task_contract import document
from test_retrieval_task_contract_provider import _plan, _ready

from compiler_service.errors import ModelOutputError
from compiler_service.graph_contract import canonical_json
from compiler_service.model_client import ModelJsonClient
from compiler_service.retrieval_contract import relation_candidate_key
from compiler_service.retrieval_providers import (
    ModelRelationClassifier,
    QueryPlanningInputTooLarge,
    compiled_context_relation_input,
    context_planning_identity,
    retrieval_search_policy,
)


def ranked(item_id, relation="supports"):
    return dict(
        id=item_id,
        relation=relation,
        direction="result_to_query",
        scope="overlap",
        score=0.8,
    )


@pytest.mark.parametrize("invalid", [None, "duplicate", "missing", "unknown", "score"])
def test_short_wire_maps_numbered_candidates_without_semantic_rewriting(invalid):
    req, context, _, _, frozen = _ready()  # Non-propositional prior interpretation.
    first = document().model_copy(update={"canonical_claim": "Long claim. " * 100})
    second = document().model_copy(update={"claim_id": "claim:second"})
    output = {"decisions": [ranked(2), ranked(1, "unrelated")]}
    if invalid == "duplicate":
        output["decisions"][1]["id"] = 2
    elif invalid == "missing":
        output["decisions"].pop()
    elif invalid == "unknown":
        output["decisions"][0]["id"] = 3
    elif invalid == "score":
        output["decisions"][0]["score"] = 2

    create = AsyncMock(return_value=_response(json.dumps(output)))
    classifier = ModelRelationClassifier(
        ModelJsonClient(SimpleNamespace(acreate=create), race_interval=0.001)
    )
    with retrieval_search_policy(interactive=True):
        kwargs = dict(
            request=req,
            context=context,
            frozen_reference=frozen,
            configuration_sha256="a" * 64,
            batch_index=0,
        )
        if invalid:
            with pytest.raises(ModelOutputError):
                classifier.classify_compiled_context(
                    _plan(req), [first, second], **kwargs
                )
            assert create.await_count == 3
            return
        result = classifier.classify_compiled_context(
            _plan(req), [first, second], **kwargs
        )
        payload = compiled_context_relation_input(
            req, context=context, frozen_reference=frozen, candidates=[first, second]
        )
    sent = create.call_args.kwargs["messages"]
    assert json.loads(sent[1]["content"]) == payload
    assert set(payload) == {"query", "candidates"}
    assert payload["candidates"][0]["claim"] == first.canonical_claim
    assert set(payload["candidates"][0]) == {"id", "claim", "scope"}
    assert "compiled_task" not in sent[0]["content"]
    assert "candidate:sha256:" not in sent[1]["content"]
    assert (
        result.model_input_projection_sha256
        == hashlib.sha256(canonical_json(payload).encode()).hexdigest()
    )
    assert [item.candidate_key for item in result.decisions] == [
        relation_candidate_key(second.snapshot_id, second.claim_id),
        relation_candidate_key(first.snapshot_id, first.claim_id),
    ]
    assert [item.relation for item in result.decisions] == ["supports", "unrelated"]
    assert result.reference_sha256 == frozen.reference_sha256
    assert create.await_count == 1
    assert create.call_args.kwargs["timeout"] is None
    assert create.call_args.kwargs["interactive"] is True


def test_interactive_projection_has_explicit_size_limit_and_distinct_binding(
    monkeypatch,
):
    import compiler_service.retrieval_providers as providers

    req, context, _, _, frozen = _ready()
    legacy_identity = context_planning_identity()
    legacy = compiled_context_relation_input(
        req, context=context, frozen_reference=frozen, candidates=[document()]
    )
    assert "compiled_task" in legacy and "schema" in legacy
    with retrieval_search_policy(interactive=True):
        assert context_planning_identity() != legacy_identity
        monkeypatch.setattr(providers, "_INTERACTIVE_RELATION_REQUEST_BYTES", 1)
        with pytest.raises(QueryPlanningInputTooLarge, match="1 MiB"):
            compiled_context_relation_input(
                req, context=context, frozen_reference=frozen, candidates=[document()]
            )
    assert context_planning_identity() == legacy_identity
