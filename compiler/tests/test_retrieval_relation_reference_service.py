"""Structural consumer tests with fake interpretations, not scientific successes."""

from types import SimpleNamespace

import pytest
from test_retrieval_clarification import forbidden
from test_retrieval_context import compiled_model_input_sha256, final_request
from test_retrieval_providers import _planner_payload
from test_retrieval_service import (
    _HANDOFF_SECRET_A,
    _document,
    _index_request,
    _manifest,
    _search_request,
    _service,
)

from compiler_service.retrieval_contract import (
    CallerBoundContextRelationBatch,
    ClaimSearchDocument,
    ContextQueryPlanningOutput,
    ContextRelationInterpretation,
    QueryPlan,
    RelationDecision,
    SearchScopeFilter,
    relation_candidate_key,
)
from compiler_service.retrieval_providers import (
    ordered_context_candidate_manifest_sha256,
    retrieval_search_policy,
)
from compiler_service.retrieval_service import (
    RetrievalService,
    _Candidate,
    _SearchTask,
    _safe_context_relation_decision,
)
from compiler_service.retrieval_task_contract import (
    InteractiveContextPlanningOutput,
    TaskOperationProposal,
    compile_task_contract,
)

P = "Toy material L increases response K under condition C in Zone1."
NOT_P = "Toy material L does not increase response K under condition C in Zone1."


def compiled_proposition(request, *, context, query_document=None, nonprop=False):
    target = {"source": "query", "quote": P}
    payload = {
        "operation": "answer_questions" if nonprop else "search_proposition",
        "instruction": {"source": "query", "quote": request.query},
    }
    if nonprop:
        payload["questions"] = [target]
    else:
        payload.update(
            target={"kind": "input_quote", "reference": target},
            evidence_direction="any",
            attributes={
                "polarity": {"value": "affirmed", "witness": target},
                "entities": [],
                "scope": {
                    "location": [{"value": "Zone1", "witness": target}],
                    "time": None,
                    "formation": [],
                    "sample_type": [],
                    "method": [],
                    "condition": [],
                    "measurement": [],
                    "uncertainty": None,
                    "entities": [],
                },
            },
        )
    return compile_task_contract(
        TaskOperationProposal.model_validate(payload, strict=True),
        request,
        context=context,
        query_document=query_document,
    )


def interpretation(kind="single_proposition", **updates):
    value = dict(
        kind=kind,
        proposition=P,
        polarity="affirmed",
        entities=[],
        scope={"location": ["Zone1"]},
    )
    if kind != "single_proposition":
        value.update(proposition=None, polarity=None, entities=[], scope=None)
    return ContextRelationInterpretation.model_validate(
        {**value, **updates}, strict=True
    )


def decision(doc, relation="equivalent", **updates):
    return RelationDecision.model_validate(
        {
            "candidate_key": relation_candidate_key(doc.snapshot_id, doc.claim_id),
            "relation": relation,
            "relation_direction": "result_to_query",
            "relation_confidence": 0.8,
            "scope_relation": "equivalent",
            **updates,
        },
        strict=True,
    )


class ReverseHintPlanner:
    name = "fake-reverse-hint-planner"

    def __init__(self, mode="valid"):
        self.calls = 0
        self.mode = mode

    def plan_interactive_context(self, request, *, context, query_document=None):
        self.calls += 1
        compiled = compiled_proposition(
            request,
            context=context,
            query_document=query_document,
            nonprop=self.mode == "nonprop_equivalent",
        )
        return InteractiveContextPlanningOutput.model_validate(
            {
                "decision": {
                    "action": "retrieve",
                    "proposal": compiled.proposal.model_dump(mode="json"),
                }
            },
            strict=True,
        )

    def interpret_compiled_task(self, request, *, context, query_document=None):
        return compiled_proposition(
            request,
            context=context,
            query_document=query_document,
            nonprop=self.mode == "nonprop_equivalent",
        )

    def plan_context(self, request, *, context, query_document=None):
        self.calls += 1
        plan = _planner_payload(NOT_P).model_dump(mode="json")
        plan.update(
            polarity="negated",
            entities=["unselected object"],
            scope_constraints={"location": ["Elsewhere"]},
        )
        return ContextQueryPlanningOutput.model_validate(
            {
                "action": "retrieve",
                "resolved_query": request.query,
                "query_plan": plan,
            },
            strict=True,
        )


class ReferenceClassifier:
    name = "fake-reference-classifier"

    def __init__(self, mode="valid"):
        self.mode, self.calls, self.seen, self.raw = mode, 0, [], []

    classify = staticmethod(forbidden)

    def classify_compiled_context(
        self,
        plan,
        candidates,
        *,
        request,
        context,
        frozen_reference,
        configuration_sha256,
        batch_index,
        query_document=None,
    ):
        self.calls += 1
        self.seen.append((plan, request, candidates))
        decisions = [
            decision(doc, "contradicts" if doc.polarity == "negated" else "equivalent")
            for doc in candidates
        ]
        self.raw.extend(decisions)
        if self.mode == "legacy_list":
            return decisions
        if self.mode == "missing":
            # The obsolete candidate-conditioned interpretation is now invalid wire.
            return {
                "interpretation": interpretation("missing_context"),
                "decisions": [],
            }
        batch = CallerBoundContextRelationBatch(
            reference_sha256=frozen_reference.reference_sha256,
            configuration_sha256=configuration_sha256,
            batch_index=batch_index,
            ordered_manifest_sha256=ordered_context_candidate_manifest_sha256(
                candidates
            ),
            model_input_projection_sha256=compiled_model_input_sha256(
                request,
                context=context,
                query_document=query_document,
                frozen_reference=frozen_reference,
                candidates=candidates,
            ),
            decisions=decisions,
        )
        # Deliberately bypass Pydantic here: the service must independently validate.
        if self.mode == "wrong_hash" or (
            self.mode == "changed_reference" and self.calls > 1
        ):
            return batch.model_copy(update={"reference_sha256": "0" * 64})
        if self.mode == "no_confidence":
            return batch.model_copy(
                update={
                    "decisions": [
                        d.model_copy(update={"relation_confidence": None})
                        for d in decisions
                    ]
                }
            )
        if self.mode == "incomplete":
            return batch.model_copy(update={"decisions": decisions[:-1]})
        return batch


def setup_service(tmp_path, mode="valid", **request_fields):
    planner, classifier = ReverseHintPlanner(mode), ReferenceClassifier(mode)
    service = _service(
        tmp_path,
        planner=planner,
        classifier=classifier,
        query_plan_handoff_secret=_HANDOFF_SECRET_A,
    )
    service.index_snapshot(
        _index_request(
            _manifest(document_count=2),
            [
                _document("positive", P, location=["Zone1"]),
                _document("negative", NOT_P, polarity="negated", location=["Zone1"]),
            ],
        )
    )
    request = _search_request(
        P,
        schema_version="evidence-retrieval/2.3",
        stage="preview",
        intent="supporting_evidence",
        limit=12,
        **request_fields,
    )
    return service, planner, classifier, request


def final_labels(result):
    return {
        member.claim_id: member.relation
        for cluster in result.clusters
        for member in cluster.member_refs
    }


def test_interactive_uses_one_planning_pass_and_scope_checked_model_relations(
    tmp_path, monkeypatch
):
    import compiler_service.retrieval_service as service_module

    service, planner, classifier, request = setup_service(tmp_path)
    monkeypatch.setattr(planner, "plan_context", forbidden)
    monkeypatch.setattr(planner, "interpret_compiled_task", forbidden)
    checked = []
    original = service_module._safe_context_relation_decision

    def check_reference(reference, document, proposed):
        checked.append(document.claim_id)
        return original(reference, document, proposed)

    monkeypatch.setattr(
        service_module, "_safe_context_relation_decision", check_reference
    )
    preview = service.search(request, interactive=True)
    assert planner.calls == 1 and classifier.calls == 0
    result = service.search(final_request(request, preview), interactive=True)
    assert planner.calls == 1 and classifier.calls == 1
    assert result.channels.relation.status == "ok"
    assert final_labels(result) == {"positive": "equivalent", "negative": "contradicts"}
    assert set(checked) == {"positive", "negative"}


@pytest.mark.parametrize("interactive", [False, True])
@pytest.mark.parametrize("context_protocol", [False, True])
@pytest.mark.parametrize("relation", ["contradicts", "equivalent", "supports"])
@pytest.mark.parametrize(
    ("query_scope", "document_scope", "expected_scope"),
    [
        (None, {}, "unknown"),
        ({"location": ["Zone1"]}, {"location": ["Zone2"]}, "unknown"),
        (
            {"time": {"label": None, "start": 1, "end": 2, "unit": "Ma"}},
            {"time": {"label": None, "start": 4, "end": 5, "unit": "Ma"}},
            "disjoint",
        ),
        ({"location": ["Zone1"]}, {"location": ["Zone1"]}, "equivalent"),
        (
            {"location": ["Zone1", "Zone2"]},
            {"location": ["Zone1", "Zone3"]},
            "overlap",
        ),
    ],
    ids=["absent", "unknown-label", "disjoint-time", "same-scope", "overlap"],
)
def test_actual_relation_application_guards_scopes_in_both_modes(
    interactive, context_protocol, relation, query_scope, document_scope, expected_scope
):
    """Execute the production attachment branch with synthetic model proposals."""
    document = ClaimSearchDocument.model_validate(
        _document("scope-control", P, **document_scope), strict=True
    )
    plan = QueryPlan(
        query_type="claim_search",
        original_query=P,
        canonical_claim=P,
        must_terms=[],
        optional_terms=[],
        exclude_terms=[],
        entities=[],
        claim_types=[],
        polarity="affirmed",
        scope_constraints=(
            None if query_scope is None else SearchScopeFilter(**query_scope)
        ),
        planner="gpt",
    )
    request = _search_request(P)
    task = _SearchTask(
        protocol_request=request,
        request=request,
        is_context_protocol=context_protocol,
        verified_handoff=None,
        context=None,
        frozen_reference=(
            SimpleNamespace(interpretation=interpretation(scope=query_scope))
            if context_protocol else None
        ),
        compiled_task=None,
        query_document=None,
        snapshots=[],
        plan=plan,
    )
    candidate = _Candidate(document=document, lexical=1.0, dense=None, rrf=1.0)
    proposed = decision(document, relation, relation_direction="query_to_result")
    with retrieval_search_policy(interactive=interactive):
        RetrievalService._apply_relation_decisions(
            None, task, [candidate], {proposed.candidate_key: proposed}
        )
    expected_relation = (
        "related"
        if relation in {"contradicts", "equivalent"}
        and expected_scope in {"unknown", "disjoint"}
        else relation
    )
    assert candidate.decision.relation == expected_relation
    assert candidate.decision.scope_relation == expected_scope
    assert candidate.decision.relation_confidence == proposed.relation_confidence
    assert candidate.direction == (
        "query_to_result" if relation == "supports" else "result_to_query"
    )
    assert proposed.relation == relation  # The provider record is not rewritten.


@pytest.mark.parametrize(
    "relation", ["supports", "refines", "qualifies", "contradicts", "equivalent"]
)
def test_interactive_nonpropositional_relevance_retained_without_direct_claim_relation(
    relation,
):
    document = ClaimSearchDocument.model_validate(_document("questions", P))
    candidate = _Candidate(document=document, lexical=1.0, dense=None, rrf=1.0)
    task = SimpleNamespace(
        frozen_reference=SimpleNamespace(
            interpretation=interpretation("non_propositional")
        ),
        is_context_protocol=True,
        query_document=None,
    )
    proposed = decision(document, relation, relation_direction="query_to_result")
    with retrieval_search_policy(interactive=True):
        RetrievalService._apply_relation_decisions(
            None, task, [candidate], {proposed.candidate_key: proposed}
        )
    direct = relation in {"contradicts", "equivalent"}
    assert candidate.decision.relation == ("related" if direct else relation)
    assert candidate.decision.scope_relation == "unknown"
    assert candidate.direction == ("result_to_query" if direct else "query_to_result")


def test_true_preview_final_uses_interpretation_not_reverse_plan(tmp_path, monkeypatch):
    service, planner, classifier, request = setup_service(tmp_path)
    preview = service.search(request)
    assert {item.claim_id for item in preview.preview} == {"positive", "negative"}
    assert preview.query_plan.polarity == "affirmed"
    assert preview.query_plan.scope_constraints.location == ["Zone1"]
    assert NOT_P not in preview.query_plan.resolved_query
    monkeypatch.setattr(planner, "plan_context", forbidden)
    monkeypatch.setattr(service.dense_encoder, "embed_query", forbidden)
    monkeypatch.setattr(service.store, "lexical_search", forbidden)
    result = service.search(final_request(request, preview))
    assert result.channels.relation.status == "ok"
    assert final_labels(result) == {"positive": "equivalent", "negative": "contradicts"}
    assert result.query_plan == preview.query_plan and classifier.calls == 1


def test_candidate_polarity_filter_does_not_become_reference_polarity(tmp_path):
    service, _, classifier, request = setup_service(tmp_path, polarity="negated")
    preview = service.search(request)
    assert [item.claim_id for item in preview.preview] == ["negative"]
    result = service.search(final_request(request, preview))
    assert classifier.seen[0][1].polarity == "negated"
    assert classifier.raw[0].relation == "contradicts"
    assert final_labels(result) == {"negative": "contradicts"}


def test_explicit_scope_still_excludes_candidates(tmp_path):
    service, _, classifier, request = setup_service(
        tmp_path, scope={"location": ["OtherZone"]}
    )
    preview = service.search(request)
    assert preview.preview == [] and classifier.calls == 0


@pytest.mark.parametrize(
    "mode",
    [
        "wrong_hash",
        "missing",
        "legacy_list",
        "no_confidence",
        "incomplete",
        "nonprop_equivalent",
    ],
)
def test_bad_reference_batch_is_degraded_without_fallback_labels(tmp_path, mode):
    service, _, classifier, request = setup_service(tmp_path, mode)
    preview = service.search(request)
    assert preview.preview
    result = service.search(final_request(request, preview))
    assert result.result_status == "degraded"
    assert result.channels.relation.status == "degraded"
    assert result.clusters == [] and classifier.calls == 1


def test_cross_batch_interpretation_change_rejects_all_labels(tmp_path, monkeypatch):
    import compiler_service.retrieval_service as runtime

    monkeypatch.setattr(runtime, "_RELATION_CLASSIFIER_BATCH_LIMIT", 1)
    service, _, classifier, request = setup_service(tmp_path, "changed_reference")
    preview = service.search(request)
    assert len(preview.preview) == 2
    result = service.search(final_request(request, preview))
    assert classifier.calls == 2
    assert result.channels.relation.status == "degraded" and result.clusters == []


def test_single_reference_retains_existing_conservative_conflict_checks():
    doc = ClaimSearchDocument.model_validate(_document("toy", P, location=["Zone1"]))
    proposed = decision(doc)
    assert (
        _safe_context_relation_decision(interpretation(), doc, proposed).relation
        == "equivalent"
    )
    # Unknown/disjoint Scope is not silently promoted to contradiction.
    for ref in (
        interpretation(scope=None),
        interpretation(
            scope={"time": {"label": None, "start": 1, "end": 2, "unit": "Ma"}}
        ),
    ):
        assert (
            _safe_context_relation_decision(
                ref, doc, decision(doc, "contradicts")
            ).relation
            == "related"
        )
    dated = ClaimSearchDocument.model_validate(
        _document("dated", P, time={"label": None, "start": 4, "end": 5, "unit": "Ma"})
    )
    ref = interpretation(
        scope={"time": {"label": None, "start": 1, "end": 2, "unit": "Ma"}}
    )
    safe = _safe_context_relation_decision(ref, dated, decision(dated, "contradicts"))
    assert safe.scope_relation == "disjoint" and safe.relation == "related"
    assert (
        _safe_context_relation_decision(
            interpretation(polarity="negated"), doc, proposed
        ).relation
        == "related"
    )
    numeric = ClaimSearchDocument.model_validate(
        _document("numeric", "Toy concentration is 20 mg/kg.")
    )
    assert (
        _safe_context_relation_decision(
            interpretation(proposition="Toy concentration is 10 mg/kg.", scope=None),
            numeric,
            decision(numeric),
        ).relation
        == "related"
    )
    entity = ClaimSearchDocument.model_validate(
        _document("entity", "Toy basin ecology", entities=["toy:beta"])
    )
    assert (
        _safe_context_relation_decision(
            interpretation(
                proposition="Toy basin ecology", entities=["toy:alpha"], scope=None
            ),
            entity,
            decision(entity),
        ).relation
        == "related"
    )


def test_nonpropositional_reference_has_no_whole_task_equivalence():
    doc = ClaimSearchDocument.model_validate(_document("toy", P))
    ref = interpretation("non_propositional")
    with pytest.raises(ValueError):
        _safe_context_relation_decision(ref, doc, decision(doc))
    output = _safe_context_relation_decision(ref, doc, decision(doc, "related"))
    assert output.relation == "related" and output.scope_relation == "unknown"
    with pytest.raises(ValueError):
        _safe_context_relation_decision(
            interpretation("missing_context"), doc, decision(doc)
        )


@pytest.mark.parametrize("mode", ["wrong_hash", "incomplete", "no_confidence"])
def test_interactive_relation_failure_raises_without_candidates(tmp_path, mode):
    from compiler_service.errors import ModelOutputError

    service, _, classifier, request = setup_service(tmp_path, mode)
    preview = service.search(request, interactive=True)
    with pytest.raises(ModelOutputError) as failure:
        service.search(final_request(request, preview), interactive=True)
    assert failure.value.status_code == 502
    assert failure.value.code == "MODEL_OUTPUT_INVALID"
    assert classifier.calls == 1


@pytest.mark.parametrize("missing", [False, True])
def test_interactive_relation_unavailable_is_explicit_failure(tmp_path, missing):
    from compiler_service.errors import (
        ModelRequestError,
        ModelRequestErrorKind,
        ModelUnavailable,
    )

    service, _, classifier, request = setup_service(tmp_path)
    failure = ModelRequestError(ModelRequestErrorKind.CONNECTION)

    def unavailable(*args, **kwargs):
        raise failure

    if missing:
        service.relation_classifier = None
    else:
        classifier.classify_compiled_context = unavailable
    preview = service.search(request, interactive=True)
    expected = ModelUnavailable if missing else ModelRequestError
    with pytest.raises(expected) as caught:
        service.search(final_request(request, preview), interactive=True)
    assert caught.value.status_code == 503
    if not missing:
        assert caught.value is failure


def test_interactive_failure_does_not_bypass_handoff_or_change_batch(tmp_path):
    from compiler_service.errors import QueryPlanHandoffError

    service, _, classifier, request = setup_service(tmp_path, "incomplete")
    preview = service.search(request, interactive=True)
    tampered = final_request(request, preview).model_copy(
        update={"query": "changed task"}
    )
    with pytest.raises(QueryPlanHandoffError):
        service.search(tampered, interactive=True)
    assert classifier.calls == 0
    batch_preview = service.search(request, interactive=False)
    batch_final = service.search(
        final_request(request, batch_preview), interactive=False
    )
    assert batch_final.channels.relation.status == "degraded"
    assert batch_final.preview == [] and batch_final.clusters == []


def test_final_contract_rejects_unassessed_preview(tmp_path):
    from pydantic import ValidationError

    from compiler_service.retrieval_contract import HybridSearchResponse

    service, _, _, request = setup_service(tmp_path, "incomplete")
    preview = service.search(request)
    result = service.search(final_request(request, preview))
    payload = result.model_dump()
    payload["preview"] = [item.model_dump() for item in preview.preview]
    with pytest.raises(ValidationError, match="unassessed previews"):
        HybridSearchResponse.model_validate(payload, strict=True)
