"""Batch recall is reused without changing its twelve-candidate boundary."""

from unittest.mock import Mock

import pytest
from test_retrieval_service import (
    _HANDOFF_SECRET_A,
    _document,
    _index_request,
    _manifest,
    _service,
)

from compiler_service.retrieval_contract import QueryClaimRef


@pytest.mark.parametrize("full_corpus", [False, True])
def test_batch_reuses_preview_and_preserves_twelve_cross_paper_candidates(
    tmp_path, full_corpus
):
    service = _service(tmp_path, query_plan_handoff_secret=_HANDOFF_SECRET_A)
    # More than twelve eligible other-paper Claims exposes accidental expansion
    # when the final cluster limit (50) is bound into the signed preview.
    service.index_snapshot(
        _index_request(
            _manifest(document_count=1),
            [_document("seed", "Volcanic mercury is present in sediments.")],
        )
    )
    for i in range(16):
        sid, pid, family = f"snapshot-{i + 2}", f"paper-{i + 2}", f"family-{i + 2}"
        service.index_snapshot(
            _index_request(
                _manifest(sid, paper_id=pid, family_id=family),
                [
                    _document(
                        f"claim-{i}",
                        f"Volcanic mercury is present in sediments {i}.",
                        snapshot_id=sid,
                        paper_id=pid,
                        family_id=family,
                    )
                ],
            )
        )
    service.store.lexical_search = Mock(wraps=service.store.lexical_search)
    service.vector_index.search = Mock(wraps=service.vector_index.search)
    search = Mock(wraps=service._search)
    service._search = search
    result = service.search_claim_relations(
        project_id="project-1",
        active_snapshot_ids=None
        if full_corpus
        else [f"snapshot-{i + 1}" for i in range(17)],
        query_claim=QueryClaimRef(snapshot_id="snapshot-1", claim_id="seed"),
    )
    preview = search.call_args_list[0].args[0]
    final = search.call_args_list[1].args[0]
    assert preview.filters.limit == final.filters.limit == 50
    assert len(final.candidate_refs) == 12
    assert final.query_plan_handoff
    assert result.stage == "ai_rerank"
    assert result.channels.lexical.status == result.channels.dense.status == "skipped"
    assert service.store.lexical_search.call_count == 1
    # Dense auxiliary views are allowed in preview; no vector request in final.
    assert service.vector_index.search.call_count >= 1
    assert all(ref.snapshot_id != "snapshot-1" for ref in final.candidate_refs)
