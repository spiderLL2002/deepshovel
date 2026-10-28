"""Task-only service wiring with explicit fake outputs, not scientific labels."""

import base64
import hashlib
import hmac
import json
from unittest.mock import Mock

import pytest
from test_retrieval_clarification import forbidden
from test_retrieval_context import (
    ContextPlanner,
    final_request,
    make_service,
    request23,
)
from test_retrieval_relation_reference_service import P, setup_service

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


@pytest.mark.parametrize("action", ["clarify", "needs_context"])
def test_planner_pending_never_interprets_or_recalls(tmp_path, monkeypatch, action):
    planner = ContextPlanner(action)
    service = make_service(tmp_path, planner)
    monkeypatch.setattr(planner, "interpret_compiled_task", forbidden)
    monkeypatch.setattr(service.store, "lexical_search", forbidden)
    result = service.search(request23())
    assert result.result_status == (
        "clarification_required" if action == "clarify" else "needs_context"
    )
    assert result.query_plan is None and not result.preview
    assert result.query_plan_handoff is None


def test_interpreter_missing_returns_existing_signed_pending_before_recall(
    tmp_path, monkeypatch
):
    service, planner, classifier, request = setup_service(tmp_path)
    question = f"请提供任务所指“{P}”的实际 Claim 或来源对象。"
    calls = []

    def missing(request, **kwargs):
        calls.append((request, kwargs))
        return compile_task_contract(
            TaskOperationProposal.model_validate(
                {
                    "operation": "verify_original_source",
                    "instruction": {"source": "query", "quote": P},
                    "target": {
                        "kind": "missing_referent",
                        "mention": {"source": "query", "quote": P},
                    },
                },
                strict=True,
            ),
            request,
            context=kwargs["context"],
        )

    monkeypatch.setattr(planner, "interpret_compiled_task", missing)
    monkeypatch.setattr(service, "_resolve_snapshots", forbidden)
    monkeypatch.setattr(service.store, "lexical_search", forbidden)
    result = service.search(request)
    assert result.result_status == "needs_context"
    assert result.needs_context.question == question
    assert result.needs_context.context_token.startswith("n1.")
    assert result.query_plan is None and result.query_plan_handoff is None
    assert result.preview == [] and result.clusters == []
    assert len(calls) == 1 and classifier.calls == 0


@pytest.mark.parametrize("invalid", [None, "wrong_type", "invalid_copy"])
def test_missing_or_invalid_interpreter_fails_without_fallback(
    tmp_path, monkeypatch, invalid
):
    service, planner, _, request = setup_service(tmp_path)
    if invalid is None:
        method = None
    elif invalid == "wrong_type":

        def method(*args, **kwargs):
            return {"kind": "non_propositional"}
    else:
        value = planner.interpret_compiled_task(
            request, context=RetrievalContextState()
        )
        value = value.model_copy(update={"retrieval_text": "invented replacement"})

        def method(*args, **kwargs):
            return value

    monkeypatch.setattr(planner, "interpret_compiled_task", method)
    monkeypatch.setattr(service.store, "lexical_search", forbidden)
    with pytest.raises(RetrievalPlanningError) as error:
        service.search(request)
    assert error.value.code in {
        "RETRIEVAL_PLANNING_UNAVAILABLE",
        "RETRIEVAL_PLANNING_PROVIDER_INVALID",
    }


def test_empty_preview_still_freezes_once_and_final_never_reinterprets(
    tmp_path, monkeypatch
):
    service, planner, classifier, request = setup_service(
        tmp_path, scope={"location": ["Excluded area"]}
    )
    original = planner.interpret_compiled_task
    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(planner, "interpret_compiled_task", counted)
    preview = service.search(request)
    assert preview.preview == [] and calls == [1]
    final = final_request(request, preview)
    sealed = service._verify_query_plan_handoff_envelope(final)
    assert sealed.frozen_task_reference.interpretation.kind == "single_proposition"
    monkeypatch.setattr(planner, "interpret_compiled_task", forbidden)
    monkeypatch.setattr(planner, "plan_context", forbidden)
    result = service.search(final)
    assert result.preview == [] and classifier.calls == 0


@pytest.mark.parametrize("query_type", ["topic_search", "paper_lookup"])
def test_derived_type_cannot_override_frozen_single_reference(
    tmp_path, monkeypatch, query_type
):
    import compiler_service.retrieval_service as runtime

    service, planner, classifier, request = setup_service(tmp_path)
    # Explicit routing-seam stub, not evidence that a real planner emits this type.
    monkeypatch.setattr(runtime, "_effective_query_type", lambda request: query_type)
    preview = service.search(request)
    assert preview.query_plan.query_type == query_type
    monkeypatch.setattr(planner, "interpret_compiled_task", forbidden)
    result = service.search(final_request(request, preview))
    assert classifier.calls == 1
    assert result.channels.relation.provider == classifier.name
    assert result.channels.relation.status == "ok"


@pytest.mark.parametrize("field", ["task_input_sha256", "interpreter_identity_sha256"])
def test_even_test_resigned_reference_binding_drift_fails_before_relation(
    tmp_path, monkeypatch, field
):
    service, planner, classifier, request = setup_service(tmp_path)
    final = final_request(request, service.search(request))
    parts = final.query_plan_handoff.split(".")
    payload = json.loads(
        base64.urlsafe_b64decode(parts[2] + "=" * (-len(parts[2]) % 4))
    )
    payload["frozen_task_reference"][field] = "0" * 64
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
    monkeypatch.setattr(planner, "plan_context", forbidden)
    monkeypatch.setattr(planner, "interpret_compiled_task", forbidden)
    monkeypatch.setattr(classifier, "classify_compiled_context", forbidden)
    with pytest.raises(QueryPlanHandoffError):
        service.search(final)


@pytest.mark.parametrize("slow_stage", ["plan_context", "interpret_compiled_task"])
def test_slow_custom_provider_preserves_recall_without_time_cutoff(
    tmp_path, monkeypatch, slow_stage
):
    import compiler_service.retrieval_providers as providers

    service, planner, _, request = setup_service(tmp_path)
    expected = service.search(request)
    now = [100.0]
    monkeypatch.setattr(providers.time, "monotonic", lambda: now[0])
    original = getattr(planner, slow_stage)

    def slow(*args, **kwargs):
        output = original(*args, **kwargs)
        now[0] = 126.0
        return output

    stage = Mock(side_effect=slow)
    recall = Mock(wraps=service.store.lexical_search)
    monkeypatch.setattr(planner, slow_stage, stage)
    monkeypatch.setattr(service.store, "lexical_search", recall)
    actual = service.search(request)
    stage.assert_called_once()
    assert recall.call_count > 0
    assert actual.query_plan == expected.query_plan
    assert actual.preview == expected.preview
    assert actual.warnings == expected.warnings


def test_slow_second_batch_preserves_all_labels_without_reinterpreting(
    tmp_path, monkeypatch
):
    import compiler_service.retrieval_service as runtime

    service, planner, classifier, request = setup_service(tmp_path)
    preview = service.search(request)
    expected = service.search(final_request(request, preview))
    classifier.calls = 0
    now = [100.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(runtime, "_RELATION_CLASSIFIER_BATCH_LIMIT", 1)
    monkeypatch.setattr(planner, "interpret_compiled_task", forbidden)
    original = classifier.classify_compiled_context

    def slow(*args, **kwargs):
        result = original(*args, **kwargs)
        now[0] = 110.0 if classifier.calls == 1 else 126.0
        return result

    monkeypatch.setattr(classifier, "classify_compiled_context", slow)
    result = service.search(final_request(request, preview))
    assert classifier.calls == 2
    assert result.channels.relation.status == "ok"
    assert result.clusters == expected.clusters
    assert result.warnings == expected.warnings


def test_context_batches_preserve_and_deduplicate_provider_warnings(
    tmp_path, monkeypatch
):
    import compiler_service.retrieval_service as runtime

    service, _, classifier, request = setup_service(tmp_path)
    preview = service.search(request)
    monkeypatch.setattr(runtime, "_RELATION_CLASSIFIER_BATCH_LIMIT", 1)
    original = classifier.classify_compiled_context
    warning = "relation model inputs were deterministically compacted"

    def warned(*args, **kwargs):
        result = original(*args, **kwargs)
        classifier.last_warnings = (warning, f"batch {classifier.calls} warning")
        return result

    monkeypatch.setattr(classifier, "classify_compiled_context", warned)
    result = service.search(final_request(request, preview))
    assert classifier.calls == 2
    assert result.channels.relation.status == "ok"
    assert result.warnings.count(warning) == 1
    assert "batch 1 warning" in result.warnings
    assert "batch 2 warning" in result.warnings
