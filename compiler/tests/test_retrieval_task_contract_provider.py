"""New operation wire and caller-owned relation binding; synthetic, offline only."""

import hashlib
import json
from copy import deepcopy
from unittest.mock import Mock

import pytest
from test_retrieval_providers import _planner_payload, _response, _sdk
from test_retrieval_task_contract import document, proposal, quote, request

from compiler_service.errors import ModelJobBudgetExceeded, ModelOutputError
from compiler_service.graph_contract import canonical_json
from compiler_service.model_client import ModelJsonClient
from compiler_service.retrieval_contract import (
    QueryPlan,
    RetrievalContextState,
    relation_candidate_key,
)
from compiler_service.retrieval_providers import (
    ModelQueryPlanner,
    ModelRelationClassifier,
    QueryPlanningInputTooLarge,
    compiled_context_relation_input,
    context_planning_identity,
    freeze_compiled_context_task_reference,
    ordered_context_candidate_manifest_sha256,
    retrieval_search_budget,
    task_only_projection,
    validate_compiled_context_task_reference,
)
from compiler_service.retrieval_task_contract import (
    InteractiveContextPlanningOutput,
    compile_task_contract,
)


def test_interactive_planning_combines_routing_and_operation_in_one_call():
    from types import SimpleNamespace

    req, context, operation, _, _ = _ready()
    req = type(req).model_validate(
        {
            **req.model_dump(),
            "conversation_history": [
                {"role": "user", "content": "previous original task"},
                {"role": "assistant", "content": "context only"},
            ],
        },
        strict=True,
    )
    context = RetrievalContextState(supplements=["exact supplement"])
    wire = InteractiveContextPlanningOutput.model_validate(
        {
            "decision": {
                "action": "retrieve",
                "proposal": operation.model_dump(mode="json"),
            }
        }
    )
    calls = []

    class Client:
        def with_job_control(self, control):
            return self

        def complete(self, **kwargs):
            calls.append(kwargs)
            kwargs["validate"](wire)
            return SimpleNamespace(value=wire)

    with retrieval_search_budget(max_completion_calls=1, interactive=True):
        result = ModelQueryPlanner(Client()).plan_interactive_context(
            req, context=context
        )
    assert result == wire
    assert len(calls) == 1
    call = calls[0]
    assert call["profile"].interactive
    payload = json.loads(call["user_content"])
    assert set(payload) == {"task_context", "source_inventory"}
    assert payload["task_context"] == task_only_projection(req, context=context)
    assert call["schema"] is InteractiveContextPlanningOutput
    assert len(call["system_prompt"].encode()) < 2500
    assert "schema" not in payload


@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("action", ["clarify", "needs_context"])
def test_interactive_planning_preserves_context_routing_without_second_call(
    selected, action
):
    from types import SimpleNamespace

    req, _, _, _, _ = _ready()
    context = RetrievalContextState(selected_query=req.query if selected else None)
    payload = (
        {"action": "needs_context", "needs_context": {"question": "Which source?"}}
        if action == "needs_context"
        else {
            "action": "clarify",
            "clarification": {
                "question": "Which process?",
                "options": [
                    {
                        "option_id": "a",
                        "label": "A",
                        "resolved_query": "What controls A?",
                    },
                    {
                        "option_id": "b",
                        "label": "B",
                        "resolved_query": "Why does B vary?",
                    },
                ],
            },
        }
    )
    wire = InteractiveContextPlanningOutput.model_validate({"decision": payload})
    client = Mock(spec=["complete", "with_job_control"])
    client.with_job_control.return_value = client
    client.complete.return_value = SimpleNamespace(value=wire)
    if selected and action == "clarify":
        with pytest.raises(
            ModelOutputError, match="cannot request another clarification"
        ):
            ModelQueryPlanner(client).plan_interactive_context(req, context=context)
    else:
        assert (
            ModelQueryPlanner(client).plan_interactive_context(req, context=context)
            == wire
        )
    assert client.complete.call_count == 1


def test_interactive_planning_locally_rejects_invented_quotes_and_mixed_branches():
    from types import SimpleNamespace

    from pydantic import ValidationError

    req, context, operation, _, _ = _ready()
    payload = {"action": "retrieve", "proposal": operation.model_dump(mode="json")}
    mixed = {**payload, "needs_context": {"question": "Extra branch?"}}
    with pytest.raises(ValidationError):
        InteractiveContextPlanningOutput.model_validate({"decision": mixed})
    payload["proposal"]["questions"][0]["quote"] = "invented question"
    wire = InteractiveContextPlanningOutput.model_validate({"decision": payload})
    client = Mock(spec=["complete", "with_job_control"])
    client.with_job_control.return_value = client
    client.complete.return_value = SimpleNamespace(value=wire)
    with pytest.raises(ValueError):
        ModelQueryPlanner(client).plan_interactive_context(req, context=context)
    assert client.complete.call_count == 1


def _ready():
    req = request("What controls A? Why does B vary?")
    context = RetrievalContextState()
    operation = proposal(
        "answer_questions",
        req.query,
        questions=[quote("What controls A?"), quote("Why does B vary?")],
    )
    compiled = compile_task_contract(operation, req, context=context)
    frozen = freeze_compiled_context_task_reference(
        req, context=context, compiled_task=compiled
    )
    return req, context, operation, compiled, frozen


def _plan(req):
    return QueryPlan(
        **_planner_payload("DERIVED ANSWER MUST NOT ENTER").model_dump(),
        original_query=req.query,
        resolved_query="DERIVED RESOLVED",
        planner="gpt",
    )


def _wire(candidate):
    return {
        "decisions": [
            {
                "candidate_key": relation_candidate_key(
                    candidate.snapshot_id, candidate.claim_id
                ),
                "relation": "related",
                "relation_direction": "result_to_query",
                "relation_confidence": 0.6,
                "scope_relation": "unknown",
            }
        ]
    }


def test_operation_sdk_preserves_task_only_input_and_closed_wire():
    seed = document()
    req = request(
        "Inspect this Claim.",
        seed=True,
        conversation_history=[
            {"role": "user", "content": "  original history\n"},
            {"role": "assistant", "content": "not evidence"},
        ],
    )
    context = RetrievalContextState(
        supplements=["exact supplement"], selected_query="Inspect this Claim."
    )
    op = proposal(
        "inspect_seed", req.query, target={"kind": "seed"}, evidence_direction="any"
    )
    create = Mock(return_value=_response(op.model_dump_json()))
    result = ModelQueryPlanner(
        ModelJsonClient(_sdk(create), sleep=lambda _: None)
    ).interpret_compiled_task(
        req,
        context=context,
        query_document=seed,
    )
    assert result.status == "ready"
    assert result.interpretation.proposition == seed.canonical_claim
    call = create.call_args.kwargs
    payload = json.loads(call["messages"][1]["content"])
    assert set(payload) == {"task_context", "source_inventory", "schema"}
    assert payload["task_context"] == task_only_projection(
        req, context=context, query_document=seed
    )
    assert payload["source_inventory"][-1]["capabilities"] == [
        "claim_representation",
        "scope_attributes",
    ]
    assert "additionalProperties" in canonical_json(payload["schema"])
    assert "filters" not in payload["task_context"]
    assert "extra_body" not in call
    assert call["model"] == "gpt-6-luna"
    assert call["timeout"] is None
    assert create.call_count == 1


@pytest.mark.parametrize(
    "kind,status",
    [("missing", "needs_context"), ("original", "capability_unavailable")],
)
def test_service_compilation_owns_pending_states(kind, status):
    req, context = request("Verify source S."), RetrievalContextState()
    target = (
        {"kind": "missing_referent", "mention": quote("source S")}
        if kind == "missing"
        else {"kind": "input_quote", "reference": quote("source S")}
    )
    op = proposal("verify_original_source", req.query, target=target)
    create = Mock(return_value=_response(op.model_dump_json()))
    result = ModelQueryPlanner(
        ModelJsonClient(_sdk(create), sleep=lambda _: None)
    ).interpret_compiled_task(req, context=context)
    assert result.status == status
    assert result.interpretation is None and result.query_units == []
    with pytest.raises(ValueError):
        freeze_compiled_context_task_reference(
            req, context=context, compiled_task=result
        )


def test_operation_invalid_quote_is_single_attempt_model_failure():
    req, context, op, _, _ = _ready()
    wire = op.model_dump(mode="json")
    wire["questions"][0]["quote"] = "invented question"
    create = Mock(return_value=_response(json.dumps(wire)))
    with pytest.raises(ModelOutputError):
        ModelQueryPlanner(
            ModelJsonClient(_sdk(create), sleep=lambda _: None)
        ).interpret_compiled_task(req, context=context)
    assert create.call_count == 6


@pytest.mark.parametrize("mutation", ["seed", "capabilities", "status"])
def test_operation_cannot_self_report_available_source_or_execution_state(mutation):
    req, context, op, _, _ = _ready()
    wire = op.model_dump(mode="json")
    if mutation == "seed":
        wire = {
            "operation": "inspect_seed",
            "instruction": quote(req.query),
            "target": {"kind": "seed"},
            "evidence_direction": "any",
        }
    elif mutation == "capabilities":
        wire["source_inventory"] = [
            {"source_id": "seed", "capabilities": ["original_page"]}
        ]
    else:
        wire["status"] = "ready"
    create = Mock(return_value=_response(json.dumps(wire)))
    planner = ModelQueryPlanner(ModelJsonClient(_sdk(create), sleep=lambda _: None))
    with pytest.raises(ModelOutputError):
        planner.interpret_compiled_task(req, context=context)
    assert create.call_count == 6


def test_operation_oversize_fails_before_sdk(monkeypatch):
    import compiler_service.retrieval_providers as providers

    create = Mock()
    monkeypatch.setattr(providers, "_CONTEXT_PLANNER_REQUEST_BYTES", 1)
    with pytest.raises(QueryPlanningInputTooLarge):
        ModelQueryPlanner(
            ModelJsonClient(_sdk(create), sleep=lambda _: None)
        ).interpret_compiled_task(
            request("What controls A?"),
            context=RetrievalContextState(),
        )
    create.assert_not_called()


def test_operation_shares_planner_deadline_and_call_count(monkeypatch):
    import compiler_service.retrieval_providers as providers

    req, context, op, _, _ = _ready()
    now = [100.0]
    monkeypatch.setattr(providers.time, "monotonic", lambda: now[0])
    planner_wire = {
        "action": "retrieve",
        "resolved_query": req.query,
        "query_plan": _planner_payload(req.query).model_dump(),
    }
    create = Mock(
        side_effect=[
            _response(json.dumps(planner_wire)),
            _response(op.model_dump_json()),
        ]
    )
    planner = ModelQueryPlanner(ModelJsonClient(_sdk(create), sleep=lambda _: None))
    with retrieval_search_budget(max_completion_calls=2):
        planner.plan_context(req, context=context)
        now[0] = 118.0
        planner.interpret_compiled_task(req, context=context)
        assert create.call_args.kwargs["timeout"] is None
        with pytest.raises(ModelJobBudgetExceeded):
            planner.interpret_compiled_task(req, context=context)
    assert create.call_count == 2


@pytest.mark.parametrize(
    "mutation", ["proposal", "compiled", "identity", "task", "interpretation"]
)
def test_frozen_reference_recompiles_and_rejects_tampering(mutation):
    req, context, _, _, frozen = _ready()
    assert (
        validate_compiled_context_task_reference(frozen, req, context=context).status
        == "ready"
    )
    if mutation == "proposal":
        frozen.proposal_json = frozen.proposal_json.replace(
            "Why does B vary?", "Where is B?"
        )
    else:
        field = {
            "compiled": "compiled_task_sha256",
            "identity": "interpreter_identity_sha256",
            "task": "task_input_sha256",
            "interpretation": "interpretation_sha256",
        }[mutation]
        setattr(frozen, field, "f" * 64)
    with pytest.raises(ValueError):
        validate_compiled_context_task_reference(frozen, req, context=context)


@pytest.mark.parametrize(
    "mutation", ["none", "echo", "coverage", "confidence", "strong"]
)
def test_relation_new_raw_wire_and_caller_binding(mutation):
    req, context, _, compiled, frozen = _ready()
    candidate = document()
    config = "c" * 64
    manifest = ordered_context_candidate_manifest_sha256([candidate])

    def respond(**kwargs):
        payload = json.loads(kwargs["messages"][1]["content"])
        prompt = kwargs["messages"][0]["content"]
        assert "query_plan" not in prompt
        assert "frozen_reference" not in prompt
        assert "Echo reference_sha256" not in prompt
        assert "DERIVED" not in kwargs["messages"][1]["content"]
        assert payload["compiled_task"] == compiled.model_dump(mode="json")
        assert set(payload["schema"]["properties"]) == {"decisions"}
        wire = _wire(candidate)
        if mutation == "echo":
            wire["reference_sha256"] = frozen.reference_sha256
        elif mutation == "coverage":
            wire["decisions"] = []
        elif mutation == "confidence":
            wire["decisions"][0]["relation_confidence"] = None
        elif mutation == "strong":
            wire["decisions"][0]["relation"] = "supports"
        return _response(json.dumps(wire))

    create = Mock(side_effect=respond)
    classifier = ModelRelationClassifier(
        ModelJsonClient(_sdk(create), sleep=lambda _: None)
    )
    kwargs = dict(
        request=req,
        context=context,
        frozen_reference=frozen,
        configuration_sha256=config,
        batch_index=3,
    )
    if mutation == "none":
        result = classifier.classify_compiled_context(_plan(req), [candidate], **kwargs)
        assert result.reference_sha256 == frozen.reference_sha256
        assert result.configuration_sha256 == config
        assert result.batch_index == 3
        assert result.ordered_manifest_sha256 == manifest
        sent_content = create.call_args.kwargs["messages"][1]["content"]
        independently_built = compiled_context_relation_input(
            req, context=context, frozen_reference=frozen, candidates=[candidate]
        )
        assert sent_content == canonical_json(independently_built)
        assert (
            result.model_input_projection_sha256
            == hashlib.sha256(sent_content.encode()).hexdigest()
        )
    else:
        with pytest.raises(ModelOutputError):
            classifier.classify_compiled_context(_plan(req), [candidate], **kwargs)
    assert create.call_count == (1 if mutation == "none" else 6)


def test_manifest_binds_order_and_full_uncompacted_bytes():
    first = document()
    second = first.model_copy(update={"claim_id": "claim:second"}, deep=True)
    original = ordered_context_candidate_manifest_sha256([first, second])
    assert original != ordered_context_candidate_manifest_sha256([second, first])
    second.evidence_context = "changed evidence tail"
    assert original != ordered_context_candidate_manifest_sha256([first, second])
    with pytest.raises(ValueError):
        ordered_context_candidate_manifest_sha256([first, first])


def test_compact_projection_change_is_separately_bound(monkeypatch):
    import compiler_service.retrieval_providers as providers

    req, context, _, _, frozen = _ready()
    candidate = document()
    manifest = ordered_context_candidate_manifest_sha256([candidate])
    baseline = compiled_context_relation_input(
        req, context=context, frozen_reference=frozen, candidates=[candidate]
    )
    original_compact = providers._compact_relation_candidate

    def changed_compact(item):
        value, truncated = original_compact(item)
        value["claim"] = "different model-visible projection"
        return value, truncated

    monkeypatch.setattr(providers, "_compact_relation_candidate", changed_compact)
    changed = compiled_context_relation_input(
        req, context=context, frozen_reference=frozen, candidates=[candidate]
    )
    assert manifest == ordered_context_candidate_manifest_sha256([candidate])
    assert hashlib.sha256(canonical_json(baseline).encode()).hexdigest() != (
        hashlib.sha256(canonical_json(changed).encode()).hexdigest()
    )


def test_sdk_mutation_cannot_rebind_call_snapshot():
    req, context, _, _, frozen = _ready()
    candidate = document()
    before = deepcopy(frozen)
    manifest = ordered_context_candidate_manifest_sha256([candidate])
    wire = _wire(candidate)

    def respond(**kwargs):
        candidate.canonical_claim = "mutated by caller while SDK runs"
        frozen.compiled_task_sha256 = "f" * 64
        return _response(json.dumps(wire))

    result = ModelRelationClassifier(
        ModelJsonClient(_sdk(Mock(side_effect=respond)), sleep=lambda _: None)
    ).classify_compiled_context(
        _plan(req),
        [candidate],
        request=req,
        context=context,
        frozen_reference=frozen,
        configuration_sha256="c" * 64,
        batch_index=0,
    )
    assert result.reference_sha256 == before.reference_sha256
    assert result.ordered_manifest_sha256 == manifest


def test_new_context_identity_names_compiler_and_decisions_wire():
    identity = context_planning_identity()
    assert identity["pipeline_version"] == "deepshovel-retrieval/2.3-task-contract-1"
    assert "compiler" in identity["task_interpreter"]
    assert "caller_relation_envelope_schema_sha256" in identity


def test_interactive_model_profile_is_explicit_and_scoped():
    from compiler_service.model_client import INTERACTIVE_COMPLETION_PROFILE
    from compiler_service.retrieval_providers import interactive_search_enabled

    req, context, operation, _, _ = _ready()
    profiles = []

    class Client:
        def with_job_control(self, control):
            return self

        def complete(self, **kwargs):
            from types import SimpleNamespace

            profiles.append(kwargs["profile"])
            return SimpleNamespace(value=operation)

    planner = ModelQueryPlanner(Client())
    with retrieval_search_budget(max_completion_calls=1, interactive=True):
        assert interactive_search_enabled()
        planner.interpret_compiled_task(req, context=context)
    assert not interactive_search_enabled()
    with retrieval_search_budget(max_completion_calls=1, interactive=False):
        planner.interpret_compiled_task(req, context=context)
    assert profiles[0] == INTERACTIVE_COMPLETION_PROFILE
    assert profiles[0].interactive is True
    assert profiles[1].interactive is False


def test_strict_task_operation_null_indices_compile_without_retry():
    req, context, operation, _, _ = _ready()
    wire = operation.model_dump(mode="json")
    wire["instruction"]["index"] = None
    for question in wire["questions"]:
        question["index"] = None
    create = Mock(return_value=_response(json.dumps(wire)))
    planner = ModelQueryPlanner(ModelJsonClient(_sdk(create), sleep=lambda _: None))
    compiled = planner.interpret_compiled_task(req, context=context)
    assert compiled.status == "ready"
    assert compiled.proposal.model_dump(mode="json") == operation.model_dump(
        mode="json"
    )
    assert create.call_count == 1


def test_search_has_no_application_deadline_in_either_mode(monkeypatch):
    import compiler_service.retrieval_providers as providers

    now = [0.0]
    monkeypatch.setattr(providers.time, "monotonic", lambda: now[0])
    with retrieval_search_budget(max_completion_calls=2, interactive=True) as control:
        now[0] = 86_400.0
        control.checkpoint()
        assert control.limits.deadline_seconds is None
    assert not providers.interactive_search_enabled()
    with retrieval_search_budget(max_completion_calls=2, interactive=False) as control:
        now[0] = 172_800.0
        control.checkpoint()
        assert control.limits.deadline_seconds is None


def test_interactive_budget_allows_three_large_pending_primary_attempts():
    import asyncio
    from types import SimpleNamespace

    from pydantic import BaseModel

    from compiler_service.model_client import INTERACTIVE_COMPLETION_PROFILE

    class Answer(BaseModel):
        value: int

    state = {"started": 0, "closed": 0}

    class Provider:
        async def acreate(self, **kwargs):
            state["started"] += 1
            try:
                # The winner deliberately waits for all three GPT launches. Earlier
                # attempts remain pending, like a slow first response in production.
                if state["started"] == 3:
                    return SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                finish_reason="stop",
                                message=SimpleNamespace(content='{"value":1}'),
                            )
                        ]
                    )
                await asyncio.Event().wait()
            finally:
                state["closed"] += 1

    class UnusedBackup:
        calls = 0

        async def acreate(self, **kwargs):
            self.calls += 1
            raise AssertionError("Pending GPT requests must not activate the backup")

    backup = UnusedBackup()
    with retrieval_search_budget(max_completion_calls=1, interactive=True) as control:
        result = ModelJsonClient(
            Provider(),
            fallback_client=backup,
            job_control=control,
            race_interval=0.01,
        ).complete(
            system_prompt="Return JSON",
            user_content="x" * 800_000,
            schema=Answer,
            profile=INTERACTIVE_COMPLETION_PROFILE,
        )
    assert result.value.value == 1
    assert state == {"started": 3, "closed": 3}
    assert control.usage.request_attempts == 3
    assert backup.calls == 0


def test_interactive_planning_actual_sdk_schema_has_object_root_and_supported_unions():
    from openai.lib._pydantic import to_strict_json_schema

    from compiler_service.retrieval_task_contract import TaskOperationProposal

    before = TaskOperationProposal.model_json_schema()
    schema = to_strict_json_schema(InteractiveContextPlanningOutput)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["decision"]
    assert "anyOf" not in schema
    assert "anyOf" in schema["properties"]["decision"]

    def check(value):
        if isinstance(value, dict):
            assert not ({"oneOf", "discriminator", "allOf", "not"} & value.keys())
            if value.get("type") == "object":
                assert value["additionalProperties"] is False
                assert set(value["required"]) == set(value["properties"])
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)

    check(schema)
    assert TaskOperationProposal.model_json_schema() == before
    assert "oneOf" in before  # Legacy/batch schema remains untouched.


@pytest.mark.parametrize("kind", ["input_quote", "seed", "missing_referent"])
@pytest.mark.parametrize("with_attributes", [True, False])
def test_interactive_proposition_wire_matches_compiler_attribute_requirements(
    kind, with_attributes
):
    from pydantic import ValidationError
    from test_retrieval_task_contract import attributes

    text = "Material R increases output S."
    target = {
        "input_quote": {"kind": kind, "reference": quote(text)},
        "seed": {"kind": kind},
        "missing_referent": {"kind": kind, "mention": quote("the following claim")},
    }[kind]
    payload = {
        "decision": {
            "action": "retrieve",
            "proposal": {
                "operation": "search_proposition",
                "instruction": quote("Find evidence for the following claim."),
                "target": target,
                "evidence_direction": "any",
                "attributes": attributes(text) if with_attributes else None,
            },
        }
    }
    if with_attributes != (kind == "input_quote"):
        with pytest.raises(ValidationError):
            InteractiveContextPlanningOutput.model_validate(payload, strict=True)
        return
    wire = InteractiveContextPlanningOutput.model_validate(payload, strict=True)
    req = request(
        "Find evidence for the following claim."
        + (f" {text}" if kind == "input_quote" else ""),
        seed=kind == "seed",
    )
    compiled = compile_task_contract(
        wire.decision.proposal,
        req,
        context=RetrievalContextState(),
        query_document=document() if kind == "seed" else None,
    )
    expected_status = "needs_context" if kind == "missing_referent" else "ready"
    assert compiled.status == expected_status


def test_interactive_planner_runs_through_real_model_client_race_and_validation():
    req, context, operation, _, _ = _ready()
    wire = InteractiveContextPlanningOutput.model_validate(
        {
            "decision": {
                "action": "retrieve",
                "proposal": operation.model_dump(mode="json"),
            },
        }
    )
    calls = []

    class Transport:
        async def acreate(self, **kwargs):
            calls.append(kwargs)
            return _response(wire.model_dump_json())

    planner = ModelQueryPlanner(ModelJsonClient(Transport()))
    with retrieval_search_budget(max_completion_calls=1, interactive=True):
        result = planner.plan_interactive_context(req, context=context)
    assert result == wire
    assert len(calls) == 1
    assert calls[0]["response_schema"] is InteractiveContextPlanningOutput
