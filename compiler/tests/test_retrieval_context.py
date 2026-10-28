"""Bounded context state and signed execution tests, not scientific GT judgments."""

import hashlib
from copy import deepcopy

import pytest
from test_retrieval_clarification import OPTIONS, forbidden
from test_retrieval_service import (
    _HANDOFF_SECRET_A,
    FakeRelationClassifier,
    _document,
    _index_request,
    _manifest,
    _search_request,
    _service,
    _translated_plan,
)

from compiler_service.errors import QueryPlanHandoffError
from compiler_service.graph_contract import canonical_json
from compiler_service.retrieval_contract import (
    CallerBoundContextRelationBatch,
    ContextQueryPlanningOutput,
    HybridSearchRequest,
    RetrievalContextState,
)
from compiler_service.retrieval_providers import (
    compiled_context_relation_input,
    ordered_context_candidate_manifest_sha256,
)
from compiler_service.retrieval_service import (
    ClarificationSelectionError,
    ContextSubmissionError,
    RetrievalPlanningError,
)
from compiler_service.retrieval_task_contract import (
    TaskOperationProposal,
    compile_task_contract,
)

HISTORY = [
    {"role": "user", "content": "  Compare Ordos tectonics and Pacific ecology.\n"},
    {
        "role": "assistant",
        "content": "First: Ordos tectonics. Second: Pacific ecology.",
    },
]


def request23(*, history=None, selection=None, submission=None):
    data = _search_request(
        "Find evidence for the second topic.",
        schema_version="evidence-retrieval/2.3",
        stage="preview",
        limit=12,
        intent="supporting_evidence",
    ).model_dump(mode="json")
    data["conversation_history"] = deepcopy(HISTORY if history is None else history)
    if selection is not None:
        data["clarification_selection"] = selection
    if submission is not None:
        data["context_submission"] = submission
    return HybridSearchRequest.model_validate(data, strict=True)


def output(action, query="Pacific ecology"):
    if action == "retrieve":
        data = {
            "action": action,
            "resolved_query": query,
            "query_plan": _translated_plan(query).model_dump(mode="json"),
        }
    elif action == "clarify":
        data = {"action": action, "clarification": OPTIONS}
    else:
        data = {
            "action": action,
            "needs_context": {"question": "Which topic do you mean?"},
        }
    return ContextQueryPlanningOutput.model_validate(data, strict=True)


class ContextPlanner:
    name = "fake-context-planner"

    def __init__(self, *actions):
        self.actions = list(actions)
        self.seen = []

    def plan_context(self, request, *, context, query_document=None):
        self.query_document = query_document
        self.seen.append(
            (request.model_dump(mode="json"), context.model_dump(mode="json"))
        )
        return output(self.actions.pop(0))

    def interpret_compiled_task(self, request, *, context, query_document=None):
        # Explicit synthetic operation: the fixture selects the exact user-owned
        # topic or authenticated selection, never the assistant's paraphrase.
        target = (
            {"source": "selected", "quote": context.selected_query}
            if context.selected_query is not None
            else {"source": "history", "index": 0, "quote": "Pacific ecology"}
        )
        proposal = TaskOperationProposal.model_validate(
            {
                "operation": "answer_questions",
                "instruction": {"source": "query", "quote": request.query},
                "questions": [target],
            },
            strict=True,
        )
        return compile_task_contract(
            proposal, request, context=context, query_document=query_document
        )


def compiled_model_input_sha256(request, **kwargs):
    projection = compiled_context_relation_input(request, **kwargs)
    return hashlib.sha256(canonical_json(projection).encode()).hexdigest()


class ContextClassifier(FakeRelationClassifier):
    def __init__(self):
        super().__init__()
        self.context_seen = []
        self.task_context_seen = []

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
        self.task_context_seen.append(
            (
                request.model_dump(mode="json"),
                context.model_dump(mode="json"),
                query_document.model_dump(mode="json") if query_document else None,
            )
        )
        self.context_seen.append(
            (plan.model_dump(mode="json"), [item.claim_id for item in candidates])
        )
        return CallerBoundContextRelationBatch(
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
            decisions=super().classify(plan, candidates),
        )


def make_service(tmp_path, planner, **kwargs):
    service = _service(
        tmp_path,
        planner=planner,
        classifier=ContextClassifier(),
        query_plan_handoff_secret=_HANDOFF_SECRET_A,
        **kwargs,
    )
    service.index_snapshot(
        _index_request(
            _manifest(document_count=2),
            [
                _document("pacific", "Pacific ecology"),
                _document("ordos", "Ordos tectonics"),
            ],
        )
    )
    return service


def final_request(base, preview):
    data = base.model_dump(mode="json")
    data.update(
        stage="ai_rerank",
        query_plan_handoff=preview.query_plan_handoff,
        candidate_refs=[
            {"snapshot_id": item.snapshot_id, "claim_id": item.claim_id}
            for item in preview.preview
        ],
    )
    return HybridSearchRequest.model_validate(data, strict=True)


def test_pending_never_reads_or_recalls_and_preserves_history(tmp_path, monkeypatch):
    planner = ContextPlanner("needs_context")
    service = make_service(tmp_path, planner)
    for owner, method in [
        (service, "_query_document"),
        (service, "_resolve_snapshots"),
        (service.store, "lexical_search"),
        (service.vector_index, "search"),
        (service.dense_encoder, "embed_query"),
    ]:
        monkeypatch.setattr(owner, method, forbidden)
    result = service.search(request23())
    assert result.result_status == "needs_context"
    assert result.query_plan is None and result.preview == result.clusters == []
    assert result.needs_context.context_token.startswith("n1.derived-auth-v1.")
    assert "query_plan_handoff" not in result.model_dump(mode="json")
    assert "clarification" not in result.model_dump(mode="json")
    assert planner.seen[0][0]["conversation_history"] == HISTORY
    assert planner.seen[0][1] == {"supplements": [], "selected_query": None}
    assert all(
        not channel.attempted and channel.status == "skipped" and channel.returned == 0
        for channel in [
            result.channels.lexical,
            result.channels.dense,
            result.channels.rerank,
            result.channels.relation,
        ]
    )


def test_repeated_supplement_select_then_supplement_retains_entire_state(
    tmp_path, monkeypatch
):
    planner = ContextPlanner(
        "needs_context", "needs_context", "clarify", "needs_context", "retrieve"
    )
    service = make_service(tmp_path, planner)
    base = request23()
    first = service.search(base)
    second = service.search(
        request23(
            submission={
                "context_token": first.needs_context.context_token,
                "content": "  The second one.\n",
            }
        )
    )
    assert second.result_status == "needs_context"
    choice = service.search(
        request23(
            submission={
                "context_token": second.needs_context.context_token,
                "content": "Please offer the two named topics.",
            }
        )
    )
    selected_request = request23(
        selection={
            "clarification_token": choice.clarification.clarification_token,
            "option_id": "B",
        }
    )
    selected = service.search(selected_request)
    assert selected.result_status == "needs_context"
    last_request = request23(
        submission={
            "context_token": selected.needs_context.context_token,
            "content": "Pacific marine ecology.",
        }
    )
    queries = []
    lexical = service.store.lexical_search

    def record_lexical(**kwargs):
        queries.extend(kwargs["query_values"])
        return lexical(**kwargs)

    monkeypatch.setattr(service.store, "lexical_search", record_lexical)
    preview = service.search(last_request)
    assert preview.preview and preview.query_plan.original_query == base.query
    assert "Input 1: Pacific ecology" in preview.query_plan.resolved_query
    assert all("Ordos" not in query for query in queries)
    assert planner.seen[-1][1] == {
        "supplements": [
            "  The second one.\n",
            "Please offer the two named topics.",
            "Pacific marine ecology.",
        ],
        "selected_query": "Pacific ecology",
    }
    for owner, method in [
        (planner, "plan_context"),
        (service.store, "lexical_search"),
        (service.vector_index, "search"),
        (service.dense_encoder, "embed_query"),
    ]:
        monkeypatch.setattr(owner, method, forbidden)
    final = service.search(final_request(last_request, preview))
    assert final.query_plan == preview.query_plan
    assert final.channels.lexical.status == final.channels.dense.status == "skipped"
    assert service.relation_classifier.context_seen[0][
        0
    ] == preview.query_plan.model_dump(mode="json")
    assert service.relation_classifier.context_seen[0][1] == [
        item.claim_id for item in preview.preview
    ]
    assert service.relation_classifier.task_context_seen[0][0] == final_request(
        last_request, preview
    ).model_dump(mode="json")
    assert service.relation_classifier.task_context_seen[0][1] == planner.seen[-1][1]
    signed_refs = {(item.snapshot_id, item.claim_id): item for item in preview.preview}
    for cluster in final.clusters:
        for item in cluster.member_refs:
            assert item.scores == signed_refs[item.snapshot_id, item.claim_id].scores


@pytest.mark.parametrize("kind", ["context", "choice"])
@pytest.mark.parametrize(
    "change",
    [
        "text",
        "role",
        "order",
        "delete",
        "query",
        "project",
        "filter",
        "config",
        "expire",
        "signature",
        "purpose",
    ],
)
def test_continuation_drift_rejected_before_any_planning_or_store(
    tmp_path, monkeypatch, kind, change
):
    planner = ContextPlanner("needs_context" if kind == "context" else "clarify")
    now = [1000.0]
    service = make_service(tmp_path, planner, query_plan_handoff_clock=lambda: now[0])
    pending = service.search(request23())
    if kind == "context":
        token = pending.needs_context.context_token
        request = request23(
            submission={"context_token": token, "content": "Pacific ecology."}
        )
        expected_error = ContextSubmissionError
    else:
        token = pending.clarification.clarification_token
        request = request23(selection={"clarification_token": token, "option_id": "B"})
        expected_error = ClarificationSelectionError
    data = request.model_dump(mode="json")
    if change == "text":
        data["conversation_history"][0]["content"] += " changed"
    elif change == "role":
        data["conversation_history"][0]["role"] = "assistant"
    elif change == "order":
        data["conversation_history"].reverse()
    elif change == "delete":
        data["conversation_history"].pop()
    elif change == "query":
        data["query"] += " changed"
    elif change == "project":
        data["project_id"] = "other-project"
    elif change == "filter":
        data["filters"]["active_snapshot_ids"] = ["other"]
    elif change == "config":
        service.rrf_k += 1
    elif change == "expire":
        now[0] += 1800
    else:
        # Keep the public shape valid while corrupting MAC or crossing purposes.
        parts = token.split(".")
        if change == "signature":
            parts[-1] = ("A" if parts[-1][0] != "A" else "B") + parts[-1][1:]
        elif kind == "context":
            other = service._issue_context_token(
                request23(),
                RetrievalContextState(),
                output("clarify").root.clarification,
            )
            parts = other.split(".")
            parts[0] = "n1"
        else:
            parts = service._issue_context_token(
                request23(), RetrievalContextState()
            ).split(".")
            parts[0] = "c2"
        if kind == "context":
            data["context_submission"]["context_token"] = ".".join(parts)
        else:
            data["clarification_selection"]["clarification_token"] = ".".join(parts)
    monkeypatch.setattr(planner, "plan_context", forbidden)
    monkeypatch.setattr(service, "_query_document", forbidden)
    with pytest.raises(expected_error):
        service.search(HybridSearchRequest.model_validate(data, strict=True))


@pytest.mark.parametrize(
    "change", ["history", "submission", "query", "candidate_order", "manifest"]
)
def test_final_binding_rejects_drift_without_replanning(tmp_path, monkeypatch, change):
    planner = ContextPlanner("needs_context", "retrieve")
    service = make_service(tmp_path, planner)
    pending = service.search(request23())
    base = request23(
        submission={
            "context_token": pending.needs_context.context_token,
            "content": "Pacific ecology.",
        }
    )
    preview = service.search(base)
    data = final_request(base, preview).model_dump(mode="json")
    if change == "history":
        data["conversation_history"][0]["content"] += "!"
    elif change == "submission":
        data["context_submission"]["content"] += "!"
    elif change == "query":
        data["query"] += "!"
    elif change == "candidate_order":
        assert len(data["candidate_refs"]) == 2
        data["candidate_refs"].reverse()
    else:
        service.index_snapshot(
            _index_request(
                _manifest("new-snapshot", paper_id="new-paper", family_id="new-family"),
                [
                    _document(
                        "new",
                        "new",
                        snapshot_id="new-snapshot",
                        paper_id="new-paper",
                        family_id="new-family",
                    )
                ],
            )
        )
    monkeypatch.setattr(planner, "plan_context", forbidden)
    monkeypatch.setattr(service.store, "lexical_search", forbidden)
    with pytest.raises(QueryPlanHandoffError):
        service.search(HybridSearchRequest.model_validate(data, strict=True))


def test_selected_cannot_reclarify_and_limits_cannot_reset(tmp_path):
    planner = ContextPlanner("clarify", "clarify")
    service = make_service(tmp_path, planner)
    choice = service.search(request23())
    with pytest.raises(RetrievalPlanningError) as caught:
        service.search(
            request23(
                selection={
                    "clarification_token": choice.clarification.clarification_token,
                    "option_id": "B",
                }
            )
        )
    assert caught.value.code == "RETRIEVAL_PLANNING_PROVIDER_INVALID"
    token = service._issue_context_token(
        request23(), RetrievalContextState(supplements=["prior"] * 8)
    )
    with pytest.raises(RetrievalPlanningError) as caught:
        service.search(
            request23(submission={"context_token": token, "content": "ninth"})
        )
    assert caught.value.code == "RETRIEVAL_PLANNING_INPUT_TOO_LARGE"


def test_valid_pending_token_accepts_future_text_and_replay_within_ttl(tmp_path):
    planner = ContextPlanner("needs_context", "retrieve", "retrieve")
    service = make_service(tmp_path, planner)
    pending = service.search(request23())
    token = pending.needs_context.context_token
    for text in ["Pacific ecology.", "Pacific marine ecology."]:
        result = service.search(
            request23(submission={"context_token": token, "content": text})
        )
        assert result.query_plan_handoff
        assert planner.seen[-1][1]["supplements"] == [text]
    # Future user content is not pre-signed by n1; each resulting final is.


def test_context_task_cannot_be_overridden_by_original_seed_graph_edge(tmp_path):
    planner = ContextPlanner("retrieve")
    service = make_service(tmp_path, planner)
    seed = _document(
        "seed",
        "Ordos tectonics",
        snapshot_id="seed-snapshot",
        paper_id="seed-paper",
        family_id="seed-family",
        explicit_relations=[
            {
                "edge_id": "edge-1",
                "source_claim_id": "seed",
                "target_claim_id": "other",
                "other_claim_id": "other",
                "relation": "supports",
                "direction": "outgoing",
                "confidence": 0.9,
                "scope_overlap": "equivalent",
            }
        ],
    )
    other = _document(
        "other",
        "Ordos tectonics related claim",
        snapshot_id="seed-snapshot",
        paper_id="seed-paper",
        family_id="seed-family",
    )
    service.index_snapshot(
        _index_request(
            _manifest(
                "seed-snapshot",
                paper_id="seed-paper",
                family_id="seed-family",
                document_count=2,
            ),
            [seed, other],
        )
    )
    data = request23().model_dump(mode="json")
    data["query_claim"] = {"snapshot_id": "seed-snapshot", "claim_id": "seed"}
    base = HybridSearchRequest.model_validate(data, strict=True)
    preview = service.search(base)
    final = service.search(final_request(base, preview))
    assert planner.query_document is not None
    assert planner.query_document.canonical_claim == "Ordos tectonics"
    found = [
        member
        for cluster in final.clusters
        for member in cluster.member_refs
        if member.snapshot_id == "seed-snapshot" and member.claim_id == "other"
    ]
    assert len(found) == 1 and found[0].relation == "related"
    assert "Input 1: Pacific ecology" in final.query_plan.resolved_query


def test_taxonomy_identity_bound_even_when_snapshot_set_is_unchanged(tmp_path):
    planner = ContextPlanner("needs_context")
    service = make_service(tmp_path, planner)
    data = request23().model_dump(mode="json")
    data["filters"]["openalex_scope"] = {
        "level": "topic",
        "id": "https://openalex.org/T1",
    }
    base = HybridSearchRequest.model_validate(data, strict=True)
    pending = service.search(base)
    data["context_submission"] = {
        "context_token": pending.needs_context.context_token,
        "content": "Pacific ecology.",
    }
    data["filters"]["openalex_scope"]["id"] = "https://openalex.org/T2"
    with pytest.raises(ContextSubmissionError):
        service.search(HybridSearchRequest.model_validate(data, strict=True))
    assert len(planner.seen) == 1
