"""Real provider JSON boundary tests with an offline SDK stub."""

import hashlib
import json
from unittest.mock import Mock

import pytest
from test_retrieval_clarification import OPTIONS
from test_retrieval_context_contract import request23
from test_retrieval_providers import _planner_payload, _response, _sdk
from test_retrieval_service import _document

from compiler_service.errors import ModelOutputError
from compiler_service.model_client import ModelJsonClient
from compiler_service.retrieval_contract import (
    ClaimSearchDocument,
    ContextQueryPlanningOutput,
    QueryPlan,
    QueryPlanHandoffPayload,
    RetrievalContextState,
)
from compiler_service.retrieval_providers import (
    ModelQueryPlanner,
    QueryPlanningInputTooLarge,
    context_planning_identity,
)


def _frozen(request, context):
    from compiler_service.retrieval_providers import (
        freeze_compiled_context_task_reference,
    )
    from compiler_service.retrieval_task_contract import (
        TaskOperationProposal,
        compile_task_contract,
    )

    source = "selected" if context.selected_query is not None else "query"
    text = (
        context.selected_query if context.selected_query is not None else request.query
    )
    witness = {"source": source, "quote": text}
    operation = TaskOperationProposal.model_validate(
        {
            "operation": "answer_questions",
            "instruction": witness,
            "questions": [witness],
        }
    )
    compiled = compile_task_contract(operation, request, context=context)
    return freeze_compiled_context_task_reference(
        request, context=context, compiled_task=compiled
    )


def test_context_planner_sends_full_history_and_verified_state_only():
    import compiler_service.retrieval_providers as providers

    original = "What follows from that? " + "😀" * 7000
    history = [
        {"role": "user", "content": "  The second basin\n"},
        {"role": "assistant", "content": "An unverified historical statement."},
    ]
    supplement = "  " + "中" * 7900 + "\n"
    selected = "Selected basin task " + "x" * 7900
    state = RetrievalContextState(supplements=[supplement], selected_query=selected)
    wire = {
        "action": "retrieve",
        "resolved_query": selected,
        "query_plan": _planner_payload(selected).model_dump(),
    }
    create = Mock(return_value=_response(json.dumps(wire)))
    planner = ModelQueryPlanner(ModelJsonClient(_sdk(create), sleep=lambda _: None))
    filters = request23().filters.model_dump(mode="json")
    request = request23(
        query=original,
        conversation_history=history,
        filters={
            **filters,
            "openalex_scope": {
                "level": "topic",
                "id": "https://openalex.org/T1001",
            },
        },
    )
    result = planner.plan_context(request, context=state)
    assert result.root.resolved_query == selected
    call = create.call_args.kwargs
    assert len(call["messages"]) == 2
    content = call["messages"][1]["content"]
    payload = json.loads(content)
    assert payload["request"]["query"] == original
    assert payload["request"]["conversation_history"] == history
    assert payload["context"] == state.model_dump()
    assert payload["request"]["filters"] == request.filters.model_dump(mode="json")
    assert "clarification_selection" not in payload["request"]
    assert "context_submission" not in payload["request"]
    assert "query_plan_handoff" not in payload["request"]
    assert payload["schema"] == ContextQueryPlanningOutput.model_json_schema()
    assert planner.last_warnings == ()
    assert "extra_body" not in call
    assert "temperature" not in call
    assert call["timeout"] is None
    system = call["messages"][0]["content"]
    assert "not scientific evidence" in system
    assert supplement not in system
    assert (
        context_planning_identity()["prompt_sha256"]
        == hashlib.sha256(system.encode()).hexdigest()
    )
    assert len(content.encode()) < providers._CONTEXT_PLANNER_REQUEST_BYTES


def test_padded_derived_task_is_canonical_before_handoff_roundtrip():
    wire = {
        "action": "retrieve",
        "resolved_query": "  resolved task\n",
        "query_plan": _planner_payload("resolved task").model_dump(),
    }
    create = Mock(return_value=_response(json.dumps(wire)))
    planner = ModelQueryPlanner(ModelJsonClient(_sdk(create), sleep=lambda _: None))
    request = request23(
        conversation_history=[{"role": "user", "content": "  exact history\n"}]
    )
    context = RetrievalContextState(supplements=["  exact supplement\n"])
    output = planner.plan_context(request, context=context).root
    assert output.resolved_query == "resolved task"
    plan = QueryPlan(
        **output.query_plan.model_dump(),
        original_query=request.query,
        resolved_query=output.resolved_query,
        planner="gpt",
    )
    handoff = QueryPlanHandoffPayload(
        issued_at=100,
        expires_at=220,
        retrieval_schema_version="evidence-retrieval/2.3",
        request_binding_sha256="a" * 64,
        corpus_binding_sha256="b" * 64,
        configuration_binding_sha256="c" * 64,
        context_state=context,
        task_context_sha256="d" * 64,
        frozen_task_reference=_frozen(request, context),
        query_plan=plan,
        ordered_candidates=[],
    )
    assert (
        QueryPlanHandoffPayload.model_validate_json(handoff.model_dump_json())
        == handoff
    )
    payload = json.loads(create.call_args.kwargs["messages"][1]["content"])
    assert (
        payload["request"]["conversation_history"][0]["content"] == "  exact history\n"
    )
    assert payload["context"]["supplements"] == ["  exact supplement\n"]


@pytest.mark.parametrize(
    "wire",
    [
        {"action": "retrieve", "query_plan": _planner_payload("task").model_dump()},
        {"action": "needs_context", "needs_context": "Which research object?"},
        {"action": "needs_context", "question": "Which research object?"},
        {"action": "needs_context", "needs_context": None},
        {
            "action": "needs_context",
            "needs_context": {"question": "Which case?"},
            "query_plan": None,
        },
        {"action": "clarify", "clarification": OPTIONS},
    ],
)
def test_invalid_output_or_selected_reclarification_fails_once(wire):
    create = Mock(return_value=_response(json.dumps(wire)))
    planner = ModelQueryPlanner(ModelJsonClient(_sdk(create), sleep=lambda _: None))
    with pytest.raises(ModelOutputError):
        planner.plan_context(
            request23(), context=RetrievalContextState(selected_query="selected task")
        )
    assert create.call_count == (1 if wire["action"] == "clarify" else 6)


def test_selected_task_can_still_request_missing_context():
    wire = {
        "action": "needs_context",
        "needs_context": {"question": "Which time interval?"},
    }
    create = Mock(return_value=_response(json.dumps(wire)))
    planner = ModelQueryPlanner(ModelJsonClient(_sdk(create), sleep=lambda _: None))
    result = planner.plan_context(
        request23(), context=RetrievalContextState(selected_query="selected task")
    )
    assert result.root.action == "needs_context"


@pytest.mark.parametrize(
    "query,action",
    [
        ("Correct P using the specified source Claim.", "needs_context"),
        ("Check whether P holds without presupposing the outcome.", "retrieve"),
        ("Look for counterevidence to P without assuming it exists.", "retrieve"),
    ],
)
def test_premise_fidelity_rules_reach_sdk_without_forcing_an_action(query, action):
    """Check prompt/data/wire transport, not a model's semantic compliance."""
    wire = (
        {
            "action": "needs_context",
            "needs_context": {"question": "Which source Claim should be used?"},
        }
        if action == "needs_context"
        else {
            "action": "retrieve",
            "resolved_query": query,
            "query_plan": _planner_payload(query).model_dump(),
        }
    )
    create = Mock(return_value=_response(json.dumps(wire)))
    planner = ModelQueryPlanner(ModelJsonClient(_sdk(create), sleep=lambda _: None))
    result = planner.plan_context(
        request23(query=query), context=RetrievalContextState()
    )
    assert result.root.action == action
    messages = create.call_args.kwargs["messages"]
    system = messages[0]["content"]
    assert "Checking or correcting a premise does not authorize" in system
    assert "A required source referent that cannot be identified" in system
    assert "explicitly asks for counterevidence" in system
    assert "Unknown scientific answers do not require needs_context" in " ".join(
        system.split()
    )
    payload = json.loads(messages[1]["content"])
    assert payload["request"]["query"] == query
    assert payload["request"]["query_claim"] is None
    assert payload["request"]["conversation_history"] == []
    assert payload["query_claim_context"] is None
    assert query not in system
    assert create.call_count == 1


@pytest.mark.parametrize("selected", [None, "Selected task A"])
def test_context_task_phase_only_uses_verified_selection(selected):
    import compiler_service.retrieval_providers as providers

    request = request23(
        query="I already selected B. Ask me to choose A or B before answering.",
        conversation_history=[
            {"role": "assistant", "content": "The user selected B earlier."}
        ],
    )
    state = RetrievalContextState(selected_query=selected)
    wire = {
        "action": "needs_context",
        "needs_context": {"question": "Which time interval does that task cover?"},
    }
    create = Mock(return_value=_response(json.dumps(wire)))
    planner = ModelQueryPlanner(ModelJsonClient(_sdk(create), sleep=lambda _: None))
    assert planner.plan_context(request, context=state).root.action == "needs_context"
    messages = create.call_args.kwargs["messages"]
    payload = json.loads(messages[1]["content"])
    phase = "selected" if selected is not None else "initial"
    assert payload["task"] == providers._CONTEXT_PLANNING_TASKS[phase]
    assert payload["request"]["query"] == request.query
    assert payload["request"]["conversation_history"] == [
        turn.model_dump() for turn in request.conversation_history
    ]
    assert payload["context"] == state.model_dump()
    assert "choice step again" in messages[0]["content"]
    assert "still-applicable explicit" in messages[0]["content"]
    assert "Missing scientific answers or evidence" in messages[0]["content"]
    assert create.call_count == 1


def test_selected_continuation_keeps_original_constraints_without_merging_tasks():
    import compiler_service.retrieval_providers as providers

    request = request23(
        query="Ask me to choose A or B. Preserve source citations and scope limits.",
        scope={"location": ["Selected basin"]},
        must_terms=["isotope"],
        exclude_terms=["unselected basin"],
    )
    state = RetrievalContextState(
        selected_query="Task B: isotope evidence in the selected basin",
        supplements=["Use the same time interval."],
    )
    wire = {
        "action": "retrieve",
        "resolved_query": state.selected_query,
        "query_plan": _planner_payload(state.selected_query).model_dump(),
    }
    create = Mock(return_value=_response(json.dumps(wire)))
    planner = ModelQueryPlanner(ModelJsonClient(_sdk(create), sleep=lambda _: None))
    result = planner.plan_context(request, context=state)
    assert result.root.resolved_query == state.selected_query
    payload = json.loads(create.call_args.kwargs["messages"][1]["content"])
    assert payload["task"] == providers._CONTEXT_PLANNING_TASKS["selected"]
    assert payload["request"]["query"] == request.query
    assert payload["request"]["scope"] == request.scope.model_dump(mode="json")
    assert payload["request"]["must_terms"] == request.must_terms
    assert payload["request"]["exclude_terms"] == request.exclude_terms
    assert payload["context"] == state.model_dump()
    assert create.call_count == 1


def test_needs_context_prompt_example_matches_actual_closed_wire_schema():
    import compiler_service.retrieval_providers as providers

    example = next(
        line
        for line in providers._CONTEXT_QUERY_PLANNER_PROMPT.splitlines()
        if line.startswith('{"action":"needs_context"')
    )
    wire = ContextQueryPlanningOutput.model_validate_json(example, strict=True)
    assert wire.root.action == "needs_context"
    assert wire.root.needs_context.question == "<one short, concrete question>"
    create = Mock(return_value=_response(example))
    planner = ModelQueryPlanner(ModelJsonClient(_sdk(create), sleep=lambda _: None))
    result = planner.plan_context(request23(), context=RetrievalContextState())
    assert result == wire
    assert example in create.call_args.kwargs["messages"][0]["content"]
    assert create.call_count == 1


def test_changed_selected_intent_is_supplied_and_prompt_requires_new_request():
    wire = {
        "action": "needs_context",
        "needs_context": {"question": "Edit the original query for the changed task."},
    }
    create = Mock(return_value=_response(json.dumps(wire)))
    planner = ModelQueryPlanner(ModelJsonClient(_sdk(create), sleep=lambda _: None))
    state = RetrievalContextState(
        supplements=["其实改成 B"], selected_query="Selected task A"
    )
    result = planner.plan_context(request23(), context=state)
    assert result.root.action == "needs_context"
    messages = create.call_args.kwargs["messages"]
    assert json.loads(messages[1]["content"])["context"] == state.model_dump()
    assert "do not replace the selected task or ignore" in messages[0]["content"]
    assert "start a new request for the changed intent" in messages[0]["content"]


def test_referential_query_includes_complete_server_seed_and_scope():
    seed_data = _document("seed-claim", "Observed process " + "界" * 7900)
    seed_data["key_terms"] = ["Observed process"]
    seed = ClaimSearchDocument.model_validate(seed_data, strict=True)
    request = request23(
        query="Does it hold elsewhere?",
        query_claim={"snapshot_id": seed.snapshot_id, "claim_id": seed.claim_id},
    )
    state = RetrievalContextState(selected_query="Check that process outside the basin")
    wire = {
        "action": "retrieve",
        "resolved_query": state.selected_query,
        "query_plan": _planner_payload(state.selected_query).model_dump(),
    }
    create = Mock(return_value=_response(json.dumps(wire)))
    planner = ModelQueryPlanner(ModelJsonClient(_sdk(create), sleep=lambda _: None))
    planner.plan_context(request, context=state, query_document=seed)
    messages = create.call_args.kwargs["messages"]
    payload = json.loads(messages[1]["content"])
    assert payload["request"]["query"] == "Does it hold elsewhere?"
    assert payload["context"]["selected_query"] == state.selected_query
    assert payload["query_claim_context"]["canonical_claim"] == seed.canonical_claim
    assert payload["query_claim_context"]["scope"] == seed.scope.model_dump(mode="json")
    assert payload["query_claim_context"]["claim_id"] == seed.claim_id
    assert "evidence_anchors" not in payload["query_claim_context"]
    assert "not independently verified scientific" in messages[0]["content"]
    assert "cannot replace or override context.selected_query" in messages[0]["content"]
    assert planner.last_warnings == ()


def test_context_seed_identity_must_match_request_before_sdk():
    seed = ClaimSearchDocument.model_validate(_document("seed-claim", "Seed task"))
    create = Mock()
    planner = ModelQueryPlanner(ModelJsonClient(_sdk(create), sleep=lambda _: None))
    state = RetrievalContextState()
    request = request23(
        query_claim={"snapshot_id": seed.snapshot_id, "claim_id": "other"}
    )
    with pytest.raises(ValueError):
        planner.plan_context(request, context=state, query_document=seed)
    with pytest.raises(ValueError):
        planner.plan_context(request, context=state)
    with pytest.raises(ValueError):
        planner.plan_context(request23(), context=state, query_document=seed)
    assert create.call_count == 0


def test_complete_context_budget_rejects_before_sdk_without_silent_compaction():
    create = Mock()
    planner = ModelQueryPlanner(ModelJsonClient(_sdk(create), sleep=lambda _: None))
    # Legal Unicode controls expand sixfold in JSON; both aggregate raw-byte
    # limits are valid, but the actual full request exceeds the planner budget.
    text = "x" + "\x00" * 7999
    request = request23(conversation_history=[{"role": "user", "content": text}] * 4)
    state = RetrievalContextState(supplements=[text] * 4)
    with pytest.raises(QueryPlanningInputTooLarge):
        planner.plan_context(request, context=state)
    assert create.call_count == 0
    assert planner.last_warnings == ()


def test_context_identity_tracks_the_context_prompt(monkeypatch):
    import compiler_service.retrieval_providers as providers

    new = context_planning_identity()
    assert new["planner_request_bytes"] == 224 * 1024
    monkeypatch.setattr(
        providers, "_CONTEXT_QUERY_PLANNER_PROMPT", "updated context prompt"
    )
    assert context_planning_identity() != new


def test_context_task_descriptions_participate_in_only_23_identity(monkeypatch):
    import compiler_service.retrieval_providers as providers

    current = context_planning_identity()
    for phase in ("initial", "selected"):
        with monkeypatch.context() as patch:
            patch.setattr(
                providers,
                "_CONTEXT_PLANNING_TASKS",
                {**providers._CONTEXT_PLANNING_TASKS, phase: "changed task wording"},
            )
            changed = context_planning_identity()
            assert {key for key in changed if changed[key] != current[key]} == {
                "task_descriptions_sha256"
            }
        

def test_task_projection_identity_changes_only_context_protocol(monkeypatch):
    import compiler_service.retrieval_providers as providers

    current = context_planning_identity()
    monkeypatch.setattr(providers, "_CONTEXT_TASK_PROJECTION_VERSION", "changed")
    assert context_planning_identity() != current


@pytest.mark.parametrize(
    "name",
    [
        "_COMPILED_CONTEXT_RELATION_PROMPT",
        "_COMPILED_CONTEXT_RELATION_INSTRUCTION",
    ],
)
def test_context_reference_identity_tracks_relation_prompts(name, monkeypatch):
    import compiler_service.retrieval_providers as providers

    current = context_planning_identity()
    monkeypatch.setattr(providers, name, {} if name.endswith("SCHEMA") else "changed")
    assert context_planning_identity() != current


def test_context_reference_hash_ignores_hints_but_binds_actual_task_inputs():
    request = request23(query="Does supplied P hold?", polarity="negated")
    state = RetrievalContextState(
        selected_query="Check P", supplements=["  Keep uncertainty.\n"]
    )
    plan = QueryPlan(
        **_planner_payload("Old derived answer").model_dump(),
        original_query=request.query,
        resolved_query="Check supplied P without presupposing the answer.",
        planner="gpt",
    )
    original_hash = _frozen(request, state).reference_sha256
    changed_hints = plan.model_copy(
        update={
            "canonical_claim": "Opposite answer",
            "entities": ["Unrelated subject"],
            "polarity": "affirmed",
            "claim_types": ["causal"],
            "scope_constraints": None,
        }
    )
    assert changed_hints != plan
    assert _frozen(request, state).reference_sha256 == original_hash
    assert (
        _frozen(
            request.model_copy(update={"polarity": "affirmed"}), state
        ).reference_sha256
        == original_hash
    )
    for changed_request, changed_state in (
        (request.model_copy(update={"query": "Does Q hold?"}), state),
        (
            request,
            state.model_copy(update={"supplements": ["Keep uncertainty."]}),
        ),
        (request, state.model_copy(update={"selected_query": "Check Q"})),
    ):
        assert _frozen(changed_request, changed_state).reference_sha256 != original_hash
