"""Signed 2.3 task restoration at the real service/fake-provider boundary."""

import base64
import hashlib
import hmac
import json

import pytest
from test_retrieval_clarification import forbidden
from test_retrieval_context import (
    ContextPlanner,
    final_request,
    make_service,
    request23,
)
from test_retrieval_service import _document, _index_request, _manifest

from compiler_service.errors import QueryPlanHandoffError
from compiler_service.graph_contract import canonical_json
from compiler_service.retrieval_contract import HybridSearchRequest
from compiler_service.retrieval_providers import context_task_projection
from compiler_service.retrieval_service import _query_plan_handoff_mac_input


def forbid_recall(monkeypatch, service, planner):
    for owner, method in (
        (planner, "plan_context"),
        (service, "_verify_context_continuation"),
        (service.store, "lexical_search"),
        (service.vector_index, "search"),
        (service.dense_encoder, "embed_query"),
    ):
        monkeypatch.setattr(owner, method, forbidden)


def continue_request(service, kind):
    root = request23()
    if kind == "none":
        return root
    pending = service.search(root)
    if kind == "n1":
        return request23(
            submission={
                "context_token": pending.needs_context.context_token,
                "content": "  Pacific ecology 🌊\n",
            }
        )
    return request23(
        selection={
            "clarification_token": pending.clarification.clarification_token,
            "option_id": "B",
        }
    )


@pytest.mark.parametrize("kind", ["none", "n1", "c2"])
def test_final_restores_preview_context_and_exact_original_request(
    tmp_path, monkeypatch, kind
):
    actions = {
        "none": ["retrieve"],
        "n1": ["needs_context", "retrieve"],
        "c2": ["clarify", "retrieve"],
    }[kind]
    planner = ContextPlanner(*actions)
    service = make_service(tmp_path, planner)
    base = continue_request(service, kind)
    preview = service.search(base)
    assert preview.preview
    final = final_request(base, preview)
    sealed = service._verify_query_plan_handoff_envelope(final)
    assert sealed.context_state.model_dump(mode="json") == planner.seen[-1][1]
    projection = context_task_projection(base, context=sealed.context_state)
    assert (
        sealed.task_context_sha256
        == hashlib.sha256(canonical_json(projection).encode()).hexdigest()
    )
    forbid_recall(monkeypatch, service, planner)
    result = service.search(final)
    assert result.query_plan == preview.query_plan
    classifier = service.relation_classifier
    assert len(classifier.context_seen) == 1
    assert classifier.task_context_seen == [
        (final.model_dump(mode="json"), planner.seen[-1][1], None)
    ]
    assert classifier.context_seen[0][1] == [r.claim_id for r in preview.preview]


@pytest.mark.parametrize("kind", ["n1", "c2"])
def test_expired_pending_token_does_not_expire_fresh_final_handoff(
    tmp_path, monkeypatch, kind
):
    now = [1000.0]
    planner = ContextPlanner("needs_context" if kind == "n1" else "clarify", "retrieve")
    service = make_service(tmp_path, planner, query_plan_handoff_clock=lambda: now[0])
    base = continue_request(service, kind)  # Pending token expires at 2800.
    now[0] = 2799
    preview = service.search(base)
    now[0] = 2801
    final = final_request(base, preview)  # Outer token remains valid until 2919.
    forbid_recall(monkeypatch, service, planner)
    result = service.search(final)
    assert result.query_plan == preview.query_plan
    assert service.relation_classifier.task_context_seen[0][1] == planner.seen[-1][1]


@pytest.mark.parametrize("change", ["expired", "signature", "config"])
def test_invalid_outer_handoff_rejects_before_state_or_relation(
    tmp_path, monkeypatch, change
):
    now = [1000.0]
    planner = ContextPlanner("retrieve")
    service = make_service(tmp_path, planner, query_plan_handoff_clock=lambda: now[0])
    base = request23()
    preview = service.search(base)
    final = final_request(base, preview)
    if change == "expired":
        now[0] += 120
    elif change == "config":
        service.rrf_k += 1
    else:
        parts = final.query_plan_handoff.split(".")
        parts[-1] = ("A" if parts[-1][0] != "A" else "B") + parts[-1][1:]
        final = final.model_copy(update={"query_plan_handoff": ".".join(parts)})
    forbid_recall(monkeypatch, service, planner)
    monkeypatch.setattr(service, "_query_document", forbidden)
    monkeypatch.setattr(
        service.relation_classifier, "classify_compiled_context", forbidden
    )
    with pytest.raises(QueryPlanHandoffError):
        service.search(final)


@pytest.mark.parametrize("change", ["context_state", "task_context_sha256"])
def test_even_authenticated_inconsistent_context_binding_is_rejected(
    tmp_path, monkeypatch, change
):
    planner = ContextPlanner("retrieve")
    service = make_service(tmp_path, planner)
    base = request23()
    preview = service.search(base)
    final = final_request(base, preview)
    parts = final.query_plan_handoff.split(".")
    payload = json.loads(
        base64.urlsafe_b64decode(parts[2] + "=" * (-len(parts[2]) % 4))
    )
    if change == "context_state":
        payload[change]["supplements"] = ["Not accepted in this preview"]
    else:
        payload[change] = "0" * 64
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
    forbid_recall(monkeypatch, service, planner)
    monkeypatch.setattr(
        service.relation_classifier, "classify_compiled_context", forbidden
    )
    with pytest.raises(QueryPlanHandoffError):
        service.search(final)


@pytest.mark.parametrize("change", ["canonical_claim", "scope"])
def test_seed_projection_drift_without_manifest_change_rejects_before_relation(
    tmp_path, monkeypatch, change
):
    planner = ContextPlanner("retrieve")
    service = make_service(tmp_path, planner)
    service.index_snapshot(
        _index_request(
            _manifest("seed-snapshot", paper_id="seed-paper", family_id="seed-family"),
            [
                _document(
                    "seed",
                    "A source-scoped task",
                    snapshot_id="seed-snapshot",
                    paper_id="seed-paper",
                    family_id="seed-family",
                )
            ],
        )
    )
    data = request23().model_dump(mode="json")
    data["query_claim"] = {"snapshot_id": "seed-snapshot", "claim_id": "seed"}
    base = HybridSearchRequest.model_validate(data, strict=True)
    preview = service.search(base)
    assert preview.preview
    final = final_request(base, preview)
    seed = service._query_document(base)
    # First establish that the original full seed is passed to the consumer.
    service.search(final)
    assert service.relation_classifier.task_context_seen[0][2] == seed.model_dump(
        mode="json"
    )
    replacement = (
        "Changed canonical source"
        if change == "canonical_claim"
        else (seed.scope.model_copy(update={"location": ["Changed source location"]}))
    )
    monkeypatch.setattr(
        service,
        "_query_document",
        lambda request: seed.model_copy(update={change: replacement}),
    )
    forbid_recall(monkeypatch, service, planner)
    monkeypatch.setattr(
        service.relation_classifier, "classify_compiled_context", forbidden
    )
    with pytest.raises(QueryPlanHandoffError):
        service.search(final)
