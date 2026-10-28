"""Compiled execution wiring with synthetic stubs, not scientific judgments."""

import base64
import hashlib
import hmac
import json

import pytest
from test_retrieval_clarification import forbidden
from test_retrieval_context import final_request
from test_retrieval_relation_reference_service import NOT_P, P, setup_service

from compiler_service.errors import QueryPlanHandoffError
from compiler_service.graph_contract import canonical_json
from compiler_service.retrieval_contract import RetrievalContextState
from compiler_service.retrieval_service import (
    RetrievalPlanningError,
    _query_plan_handoff_mac_input,
)
from compiler_service.retrieval_task_contract import (
    TaskOperationProposal,
    compile_task_contract,
)


def test_compiled_quote_replaces_reverse_planner_in_actual_recall(
    tmp_path, monkeypatch
):
    service, planner, classifier, request = setup_service(tmp_path)
    compiled = planner.interpret_compiled_task(request, context=RetrievalContextState())
    lexical_inputs, dense_inputs = [], []
    lexical, dense = service.store.lexical_search, service.dense_encoder.embed_query

    def record_lexical(**kwargs):
        lexical_inputs.extend(kwargs["query_values"])
        return lexical(**kwargs)

    def record_dense(query):
        dense_inputs.append(query)
        return dense(query)

    monkeypatch.setattr(service.store, "lexical_search", record_lexical)
    monkeypatch.setattr(service.dense_encoder, "embed_query", record_dense)
    preview = service.search(request)
    assert preview.query_plan.resolved_query == compiled.retrieval_text
    assert preview.query_plan.canonical_claim == P
    assert preview.query_plan.polarity == "affirmed"
    assert preview.query_plan.scope_constraints.location == ["Zone1"]
    assert P in lexical_inputs and dense_inputs
    assert all(NOT_P not in text for text in [*lexical_inputs, *dense_inputs])
    assert all("unselected object" not in text for text in lexical_inputs)
    result = service.search(final_request(request, preview))
    assert result.channels.relation.status == "ok" and classifier.calls == 1


@pytest.mark.parametrize("missing", [True, False])
def test_source_referent_missing_and_source_capability_absent_are_distinct(
    tmp_path, monkeypatch, missing
):
    service, planner, classifier, request = setup_service(tmp_path)

    def source_operation(request, *, context, query_document=None):
        quote = {"source": "query", "quote": P}
        target = (
            {"kind": "missing_referent", "mention": quote}
            if missing
            else {"kind": "input_quote", "reference": quote}
        )
        return compile_task_contract(
            TaskOperationProposal.model_validate(
                {
                    "operation": "verify_original_source",
                    "instruction": quote,
                    "target": target,
                },
                strict=True,
            ),
            request,
            context=context,
            query_document=query_document,
        )

    monkeypatch.setattr(planner, "interpret_compiled_task", source_operation)
    for owner, name in (
        (service, "_resolve_snapshots"),
        (service.store, "lexical_search"),
        (service.dense_encoder, "embed_query"),
        (service.vector_index, "search"),
    ):
        monkeypatch.setattr(owner, name, forbidden)
    if missing:
        result = service.search(request)
        assert result.result_status == "needs_context"
        assert result.needs_context.context_token.startswith("n1.")
        assert result.query_plan is None and result.preview == result.clusters == []
    else:
        with pytest.raises(RetrievalPlanningError) as caught:
            service.search(request)
        assert caught.value.code == "RETRIEVAL_PLANNING_CAPABILITY_UNAVAILABLE"
    assert classifier.calls == 0


@pytest.mark.parametrize(
    "field", ["compiled_task_sha256", "proposal_json", "interpretation"]
)
def test_resigned_compiled_reference_is_recompiled_before_final_classification(
    tmp_path, monkeypatch, field
):
    service, planner, classifier, request = setup_service(tmp_path)
    final = final_request(request, service.search(request))
    parts = final.query_plan_handoff.split(".")
    payload = json.loads(
        base64.urlsafe_b64decode(parts[2] + "=" * (-len(parts[2]) % 4))
    )
    reference = payload["frozen_task_reference"]
    if field == "compiled_task_sha256":
        reference[field] = "0" * 64
    elif field == "proposal_json":
        proposal = json.loads(reference[field])
        proposal["evidence_direction"] = "counter"
        reference[field] = canonical_json(proposal)
    else:
        reference[field]["proposition"] = NOT_P
    raw = canonical_json(payload).encode()
    parts[2] = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    parts[3] = (
        base64.urlsafe_b64encode(
            hmac.new(
                service._query_plan_handoff_key,
                _query_plan_handoff_mac_input(raw),
                hashlib.sha256,
            ).digest()
        )
        .decode()
        .rstrip("=")
    )
    final = final.model_copy(update={"query_plan_handoff": ".".join(parts)})
    monkeypatch.setattr(planner, "interpret_compiled_task", forbidden)
    monkeypatch.setattr(planner, "plan_context", forbidden)
    monkeypatch.setattr(service.store, "lexical_search", forbidden)
    monkeypatch.setattr(classifier, "classify_compiled_context", forbidden)
    with pytest.raises(QueryPlanHandoffError):
        service.search(final)


@pytest.mark.parametrize(
    "field",
    [
        "configuration_sha256",
        "batch_index",
        "ordered_manifest_sha256",
        "model_input_projection_sha256",
    ],
)
def test_second_batch_caller_binding_drift_discards_first_batch_labels(
    tmp_path, monkeypatch, field
):
    import compiler_service.retrieval_service as runtime

    service, _, classifier, request = setup_service(tmp_path)
    preview = service.search(request)
    monkeypatch.setattr(runtime, "_RELATION_CLASSIFIER_BATCH_LIMIT", 1)
    original = classifier.classify_compiled_context

    def drift(*args, **kwargs):
        batch = original(*args, **kwargs)
        if classifier.calls == 2:
            return batch.model_copy(
                update={field: 99 if field == "batch_index" else "0" * 64}
            )
        return batch

    monkeypatch.setattr(classifier, "classify_compiled_context", drift)
    result = service.search(final_request(request, preview))
    assert classifier.calls == 2
    assert result.channels.relation.status == "degraded"
    assert result.clusters == []
    assert result.result_status == "degraded"


def test_second_batch_mutation_rejects_even_presaved_correct_envelope(
    tmp_path, monkeypatch
):
    import compiler_service.retrieval_service as runtime

    service, _, classifier, request = setup_service(tmp_path)
    preview = service.search(request)
    monkeypatch.setattr(runtime, "_RELATION_CLASSIFIER_BATCH_LIMIT", 1)
    original = classifier.classify_compiled_context

    def mutate(plan, candidates, **kwargs):
        saved = original(plan, candidates, **kwargs)
        if classifier.calls == 2:
            # A receipt captured before mutation cannot authorize decisions
            # after the custom provider alters its caller-owned input objects.
            candidates[0].canonical_claim = "Mutated source representation."
        return saved

    monkeypatch.setattr(classifier, "classify_compiled_context", mutate)
    result = service.search(final_request(request, preview))
    assert classifier.calls == 2
    assert result.channels.relation.status == "degraded"
    assert result.result_status == "degraded" and result.clusters == []


def test_ordered_question_units_reach_lexical_query_values(tmp_path, monkeypatch):
    service, planner, _, request = setup_service(tmp_path)
    units = ["Toy material L", "response K"]

    def multiple(request, *, context, query_document=None):
        return compile_task_contract(
            TaskOperationProposal.model_validate(
                {
                    "operation": "answer_questions",
                    "instruction": {"source": "query", "quote": P},
                    "questions": [{"source": "query", "quote": unit} for unit in units],
                },
                strict=True,
            ),
            request,
            context=context,
            query_document=query_document,
        )

    seen = []
    original = service.store.lexical_search

    def record(**kwargs):
        seen.append(list(kwargs["query_values"]))
        return original(**kwargs)

    monkeypatch.setattr(planner, "interpret_compiled_task", multiple)
    monkeypatch.setattr(service.store, "lexical_search", record)
    result = service.search(request)
    assert seen[0][:2] == units
    assert (
        result.query_plan.resolved_query
        == multiple(request, context=RetrievalContextState()).retrieval_text
    )
    # This asserts retained lexical inputs, not per-question dense execution,
    # answer coverage, or the semantic correctness of the selected operation.


@pytest.mark.parametrize("stage", ["planner", "classifier"])
def test_missing_compiled_api_fails_closed(tmp_path, monkeypatch, stage):
    service, planner, classifier, request = setup_service(tmp_path)
    if stage == "planner":
        monkeypatch.setattr(planner, "interpret_compiled_task", None)
        monkeypatch.setattr(service.store, "lexical_search", forbidden)
        with pytest.raises(RetrievalPlanningError) as caught:
            service.search(request)
        assert caught.value.code == "RETRIEVAL_PLANNING_UNAVAILABLE"
    else:
        preview = service.search(request)
        monkeypatch.setattr(classifier, "classify_compiled_context", None)
        result = service.search(final_request(request, preview))
        assert result.channels.relation.status == "degraded"
        assert result.clusters == []
