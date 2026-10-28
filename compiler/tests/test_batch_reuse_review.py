"""Small offline checks of signed batch reuse boundaries."""

from unittest.mock import Mock

import pytest
from test_retrieval_service import (
    _HANDOFF_SECRET_A,
    FakeRelationClassifier,
    _document,
    _index_request,
    _manifest,
    _service,
)

from compiler_service.retrieval_contract import QueryClaimRef
from compiler_service.retrieval_service import QueryPlanHandoffError


def setup_service(tmp_path):
    classifier = FakeRelationClassifier()
    service = _service(
        tmp_path, classifier=classifier, query_plan_handoff_secret=_HANDOFF_SECRET_A
    )
    service.index_snapshot(
        _index_request(
            _manifest(document_count=2),
            [
                _document("seed", "Mercury records volcanic emissions."),
                _document("sibling", "Mercury records sedimentary processes."),
            ],
        )
    )
    service.index_snapshot(
        _index_request(
            _manifest("snapshot-2", paper_id="paper-2", family_id="family-2"),
            [
                _document(
                    "other",
                    "Mercury records volcanic emissions.",
                    snapshot_id="snapshot-2",
                    paper_id="paper-2",
                    family_id="family-2",
                )
            ],
        )
    )
    return service, classifier


def test_empty_signed_batch_preview_keeps_same_paper_candidates(tmp_path):
    service, classifier = setup_service(tmp_path)
    service.store.lexical_search = Mock(return_value=[])
    service.vector_index.search = Mock(return_value=[])
    result = service.search_claim_relations(
        project_id="project-1",
        active_snapshot_ids=None,
        query_claim=QueryClaimRef(snapshot_id="snapshot-1", claim_id="seed"),
    )
    assert result.schema_version == "evidence-retrieval/2.1"
    assert classifier.seen == [("snapshot-1", "sibling")]
    assert service.store.lexical_search.call_count == 1
    assert result.channels.lexical.status == "skipped"


def test_signed_batch_rejects_changed_project_corpus_before_final(tmp_path):
    service, classifier = setup_service(tmp_path)
    search = service.search

    def changed_corpus(request, **kwargs):
        if request.stage == "ai_rerank":
            service.index_snapshot(
                _index_request(
                    _manifest("snapshot-3", paper_id="paper-3", family_id="family-3"),
                    [
                        _document(
                            "new",
                            "New mercury source.",
                            snapshot_id="snapshot-3",
                            paper_id="paper-3",
                            family_id="family-3",
                        )
                    ],
                )
            )
        return search(request, **kwargs)

    service.search = changed_corpus
    with pytest.raises(QueryPlanHandoffError):
        service.search_claim_relations(
            project_id="project-1",
            active_snapshot_ids=None,
            query_claim=QueryClaimRef(snapshot_id="snapshot-1", claim_id="seed"),
        )
    assert classifier.seen == []
