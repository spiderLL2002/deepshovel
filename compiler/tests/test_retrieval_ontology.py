from __future__ import annotations

from compiler_service.retrieval_ontology import default_retrieval_ontology
from compiler_service.retrieval_service import _balanced_query_excerpt


def test_reviewed_aliases_bridge_scripts_without_hierarchy_expansion() -> None:
    ontology = default_retrieval_ontology()

    assert [item.concept_id for item in ontology.concepts_in("鄂尔多斯盆地")] == [
        "geo:ordos-basin"
    ]
    assert "Ordos Basin" in ontology.aliases_for_query("鄂尔多斯盆地")
    assert [
        item.concept_id for item in ontology.concepts_in("western Ordos Basin")
    ] == ["geo:western-ordos-basin"]
    assert [item.concept_id for item in ontology.concepts_in("LREE")] == ["chem:lree"]


def test_mree_aliases_fail_closed_at_chinese_grammar_boundaries() -> None:
    ontology = default_retrieval_ontology()

    for explicit_mree in (
        "MREE",
        "MREEs",
        "middle REE enrichment",
        "middle rare earth elements",
    ):
        assert "chem:mree" in {
            item.concept_id for item in ontology.concepts_in(explicit_mree)
        }

    for ree_in_material in (
        "沉积物中稀土元素",
        "泥中稀土元素",
        "Z18中稀土元素",
        "海洋沉积物中稀土元素",
    ):
        concept_ids = {
            item.concept_id for item in ontology.concepts_in(ree_in_material)
        }
        assert "chem:ree" in concept_ids
        assert "chem:mree" not in concept_ids


def test_query_scoped_concept_ids_equal_full_unsuppressed_membership() -> None:
    ontology = default_retrieval_ontology()
    concept_ids = [concept.concept_id for concept in ontology.concepts]
    cases = [
        (label, concept.concept_id)
        for concept in ontology.concepts
        for label in concept.labels
    ]
    cases.append(("western Ordos Basin and rare earth elements", concept_ids[0]))

    for index, (text, primary_id) in enumerate(cases):
        requested = {
            primary_id,
            concept_ids[(index + 1) % len(concept_ids)],
            "unknown:fail-closed",
        }
        full = {
            concept.concept_id
            for concept in ontology.concepts_in(text, suppress_nested=False)
        }

        assert ontology.query_scoped_concept_ids(text, requested) == (full & requested)


def test_balanced_long_query_excerpt_keeps_beginning_middle_and_end() -> None:
    value = "BEGIN " + "a" * 900 + " MIDDLE " + "b" * 900 + " END"

    excerpt = _balanced_query_excerpt(value)

    assert "BEGIN" in excerpt
    assert "MIDDLE" in excerpt
    assert "END" in excerpt
    assert len(excerpt) <= 360
