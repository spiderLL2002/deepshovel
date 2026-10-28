"""Bounded synthetic operation/reference tests; no model or scientific labels."""

import pytest
from pydantic import ValidationError

from compiler_service.retrieval_contract import (
    ClaimSearchDocument,
    HybridSearchRequest,
    RetrievalContextState,
)
from compiler_service.retrieval_task_contract import (
    CompiledTaskContract,
    TaskOperationProposal,
    TaskQuote,
    compile_task_contract,
    task_contract_input_projection,
)


def request(text, *, seed=False, **updates):
    return HybridSearchRequest.model_validate(
        {
            "schema_version": "evidence-retrieval/2.3",
            "project_id": "project:test",
            "stage": "preview",
            "query": text,
            "query_claim": {"snapshot_id": "snapshot:test", "claim_id": "claim:test"}
            if seed
            else None,
            "candidate_refs": [],
            "intent": "claim_search",
            "must_terms": [],
            "exclude_terms": [],
            "scope": None,
            "claim_types": [],
            "polarity": None,
            "filters": {
                "corpus": "cross_paper",
                "paper_ids": [],
                "paper_family_ids": [],
                "active_snapshot_ids": None,
                "limit": 12,
            },
            **updates,
        },
        strict=True,
    )


def document():
    return ClaimSearchDocument.model_validate(
        {
            "index_version": "evidence-retrieval/2.0",
            "snapshot_id": "snapshot:test",
            "paper_id": "paper:test",
            "paper_family_id": "family:test",
            "root_artifact_version_id": "artifact:test",
            "claim_id": "claim:test",
            "source_claim_id": None,
            "original_claim": None,
            "canonical_claim": "Material R increases output S under condition T.",
            "proposition_key": "proposition:sha256:" + "a" * 64,
            "scoped_claim_id": "claim:sha256:" + "b" * 64,
            "paper_title": "Synthetic title",
            "evidence_context": "",
            "entities": ["R"],
            "key_terms": [],
            "abbreviations": [],
            "qualifiers": [],
            "claim_type": "descriptive",
            "polarity": "affirmed",
            "modality": "conditional",
            "attribution": "authors_conclusion",
            "rhetorical_role": "terminal_conclusion",
            "scope": {
                "location": ["region V"],
                "time": None,
                "formation": [],
                "sample_type": ["R"],
                "method": [],
                "condition": ["T"],
                "measurement": ["S"],
                "uncertainty": "conditional",
                "entities": ["R", "S"],
            },
            "evidence_anchors": [],
            "shortest_evidence_path": {
                "root_claim_id": "claim:test",
                "nodes": [],
                "links": [],
                "depth": 0,
                "mean_edge_confidence": None,
            },
            "path_quality": 0,
            "extractor_prompt_version": "test",
        },
        strict=True,
    )


def quote(text, source="query", **kwargs):
    return {"source": source, "quote": text, **kwargs}


def proposal(operation, instruction, **kwargs):
    return TaskOperationProposal.model_validate(
        {
            "operation": operation,
            "instruction": quote(instruction),
            **kwargs,
        },
        strict=True,
    )


def compile(proposed, req, **kwargs):
    return compile_task_contract(
        proposed, req, context=RetrievalContextState(), **kwargs
    )


def attributes(target):
    return {
        "polarity": {"value": "affirmed", "witness": quote(target)},
        "entities": [],
        "scope": {
            "location": [],
            "time": None,
            "formation": [],
            "sample_type": [],
            "method": [],
            "condition": [],
            "measurement": [],
            "uncertainty": None,
            "entities": [],
        },
    }


def test_seed_comparison_hydrates_full_scope_and_preserves_counter_direction():
    text = "Compare the supplied claim and seek counterevidence."
    result = compile(
        proposal(
            "inspect_seed", text, target={"kind": "seed"}, evidence_direction="counter"
        ),
        request(text, seed=True),
        query_document=document(),
    )
    assert result.status == "ready"
    assert result.interpretation.proposition == document().canonical_claim
    assert result.interpretation.polarity == "affirmed"
    for key, value in document().scope.model_dump().items():
        if value is not None and value != []:
            assert getattr(result.interpretation.scope, key) == value
    assert result.interpretation.entities == document().scope.entities
    assert "Evidence direction: counter" in result.retrieval_text
    assert result.source_inventory[-1].capabilities == [
        "claim_representation",
        "scope_attributes",
    ]
    assert (
        CompiledTaskContract.model_validate(result.model_dump(mode="json"), strict=True)
        == result
    )
    assert (
        compile(result.proposal, request(text, seed=True), query_document=document())
        == result
    )


def test_questions_retain_exact_units_and_unknown_answers():
    text = "How does R form? When does S appear?"
    result = compile(
        proposal(
            "answer_questions",
            text,
            questions=[quote("How does R form?"), quote("When does S appear?")],
        ),
        request(text),
    )
    assert result.interpretation.kind == "non_propositional"
    assert result.query_units == ["How does R form?", "When does S appear?"]
    assert result.interpretation.proposition is None


def test_exact_input_proposition_uses_only_target_attributes():
    target = "R increases S under condition T."
    text = "Find evidence: " + target
    attrs = attributes(target)
    attrs["scope"]["condition"] = [{"value": "T", "witness": quote("condition T")}]
    result = compile(
        proposal(
            "search_proposition",
            "Find evidence:",
            target={"kind": "input_quote", "reference": quote(target)},
            evidence_direction="any",
            attributes=attrs,
        ),
        request(text),
    )
    assert result.interpretation.proposition == target
    assert result.interpretation.scope.condition == ["T"]


@pytest.mark.parametrize(
    "operation,extra",
    [
        ("verify_original_source", {}),
        ("correct_from_claim", {"premise": quote("R decreases S")}),
        ("search_proposition", {"evidence_direction": "any", "attributes": None}),
    ],
)
def test_missing_object_asks_for_named_referent_not_known_topic(operation, extra):
    text = "Inspect source Z about R decreases S."
    result = compile(
        proposal(
            operation,
            text,
            target={"kind": "missing_referent", "mention": quote("source Z")},
            **extra,
        ),
        request(text),
    )
    assert result.status == "needs_context"
    assert "source Z" in result.needs_context.question
    assert "R decreases S" not in result.needs_context.question
    assert result.interpretation is None and result.query_units == []


@pytest.mark.parametrize("seed", [False, True])
def test_existing_object_without_original_page_capability_does_not_reask(seed):
    text = "Verify the original source for claim text R."
    target = (
        {"kind": "seed"}
        if seed
        else {"kind": "input_quote", "reference": quote("claim text R")}
    )
    result = compile(
        proposal("verify_original_source", text, target=target),
        request(text, seed=seed),
        **({"query_document": document()} if seed else {}),
    )
    assert result.status == "capability_unavailable"
    assert result.needs_context is None and result.retrieval_text == ""


def test_correction_from_given_seed_keeps_premise_and_source_distinct():
    text = "Correct R decreases S using the given Claim."
    result = compile(
        proposal(
            "correct_from_claim",
            text,
            target={"kind": "seed"},
            premise=quote("R decreases S"),
        ),
        request(text, seed=True),
        query_document=document(),
    )
    assert (
        result.status == "ready" and result.interpretation.kind == "non_propositional"
    )
    assert result.query_units == ["R decreases S", document().canonical_claim]


@pytest.mark.parametrize(
    "bad_quote",
    [
        quote("absent"),
        quote("R", "history", index=0),
        quote("R", "supplement", index=0),
        quote("R", "selected"),
    ],
)
def test_missing_or_assistant_reference_fails(bad_quote):
    req = request(
        "Find R.", conversation_history=[{"role": "assistant", "content": "R"}]
    )
    with pytest.raises(ValueError):
        compile(proposal("answer_questions", "Find R.", questions=[bad_quote]), req)


def test_ambiguous_quote_and_duplicate_question_fail():
    for text, questions in [
        ("R and R", [quote("R")]),
        ("Find R.", [quote("R"), quote("R")]),
    ]:
        with pytest.raises(ValueError):
            compile(
                proposal("answer_questions", text, questions=questions), request(text)
            )


@pytest.mark.parametrize(
    "wire",
    [
        quote("R", index=0),
        quote("R", "history"),
        quote(" "),
        quote("e\u0301"),
        {"source": "filters", "quote": "R"},
    ],
)
def test_quote_wire_is_closed_and_exact(wire):
    with pytest.raises(ValidationError):
        TaskQuote.model_validate(wire, strict=True)


def test_attribute_witness_outside_target_fails_even_when_in_original_task():
    text = "Only sources from Zone V. R increases S."
    attrs = attributes("R increases S.")
    attrs["scope"]["location"] = [{"value": "Zone V", "witness": quote("Zone V")}]
    with pytest.raises(ValueError, match="inside"):
        compile(
            proposal(
                "search_proposition",
                text,
                target={"kind": "input_quote", "reference": quote("R increases S.")},
                evidence_direction="any",
                attributes=attrs,
            ),
            request(text),
        )


def test_seed_attributes_are_not_model_editable():
    text = "Compare R."
    with pytest.raises(ValueError, match="server-owned"):
        compile(
            proposal(
                "search_proposition",
                text,
                target={"kind": "seed"},
                evidence_direction="any",
                attributes=attributes(text),
            ),
            request(text, seed=True),
            query_document=document(),
        )


def test_input_identity_preserves_full_context_but_excludes_caller_filters():
    text = "How does R form?"
    op = proposal("answer_questions", text, questions=[quote(text)])
    plain = compile(op, request(text))
    filtered = compile(
        op, request(text, scope={"location": ["Zone V"]}, polarity="negated")
    )
    assert plain == filtered
    context = RetrievalContextState(
        supplements=["Additional user task"], selected_query=text
    )
    projection = task_contract_input_projection(request(text), context=context)
    assert projection["context"] == {
        "supplements": ["Additional user task"],
        "selected_query": text,
    }
    changed = compile_task_contract(op, request(text), context=context)
    assert changed.task_input_sha256 != plain.task_input_sha256


def test_locatable_question_is_not_claimed_semantic_validation():
    # Document the remaining semantic limit instead of pretending punctuation is NLP.
    text = "Which material increases S?"
    result = compile(
        proposal(
            "search_proposition",
            text,
            target={"kind": "input_quote", "reference": quote(text)},
            evidence_direction="any",
            attributes=attributes(text),
        ),
        request(text),
    )
    assert result.interpretation.proposition == text
    # This structural success remains a semantic failure in a real-model gate.


@pytest.mark.parametrize("start,valid", [("100", True), ("10", False)])
def test_numeric_values_are_complete_source_tokens(start, valid):
    text = "R increases S between 100 and 200 Ma."
    attrs = attributes(text)
    attrs["scope"]["time"] = {
        "label": {"value": "100 and 200 Ma", "witness": quote("100 and 200 Ma")},
        "start": {"value": start, "witness": quote("100 and 200 Ma")},
        "end": {"value": "200", "witness": quote("100 and 200 Ma")},
        "unit": {"value": "Ma", "witness": quote("100 and 200 Ma")},
    }
    op = proposal(
        "search_proposition",
        text,
        target={"kind": "input_quote", "reference": quote(text)},
        evidence_direction="any",
        attributes=attrs,
    )
    if valid:
        result = compile(op, request(text))
        assert result.interpretation.scope.time.start == 100
        assert result.interpretation.scope.time.end == 200
    else:
        with pytest.raises(ValueError, match="complete token"):
            compile(op, request(text))


def test_short_attribute_value_can_use_unambiguous_longer_witness():
    text = "Compare C: R increases S under condition C."
    attrs = attributes("R increases S under condition C.")
    attrs["scope"]["condition"] = [
        {"value": "C", "witness": quote("under condition C")}
    ]
    result = compile(
        proposal(
            "search_proposition",
            "Compare C:",
            target={
                "kind": "input_quote",
                "reference": quote("R increases S under condition C."),
            },
            evidence_direction="any",
            attributes=attrs,
        ),
        request(text),
    )
    assert result.interpretation.scope.condition == ["C"]


def test_long_missing_mention_retains_proposal_and_uses_bounded_question():
    text = "Supply missing object " + "R" * 1100
    op = proposal(
        "verify_original_source",
        text,
        target={"kind": "missing_referent", "mention": quote(text)},
    )
    result = compile(op, request(text))
    assert result.status == "needs_context"
    assert len(result.needs_context.question) <= 1000
    assert result.proposal.root.target.mention.quote == text


@pytest.mark.parametrize("source", ["query", "selected"])
def test_nonindexed_quote_accepts_strict_schema_null_without_changing_canonical_wire(
    source,
):
    explicit_null = TaskQuote.model_validate(
        {"source": source, "quote": "R", "index": None}, strict=True
    )
    absent = TaskQuote.model_validate({"source": source, "quote": "R"}, strict=True)
    assert explicit_null.model_dump(mode="json") == absent.model_dump(mode="json")
    assert "index" not in explicit_null.model_dump(mode="json")
    with pytest.raises(ValidationError):
        TaskQuote.model_validate(
            {"source": source, "quote": "R", "index": 0}, strict=True
        )


@pytest.mark.parametrize("source", ["history", "supplement"])
def test_indexed_quotes_still_require_a_real_index(source):
    with pytest.raises(ValidationError):
        TaskQuote.model_validate(
            {"source": source, "quote": "R", "index": None}, strict=True
        )
