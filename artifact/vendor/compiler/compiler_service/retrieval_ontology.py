"""Small, versioned, fail-closed concept aliases for retrieval recall.

The evidence graph remains immutable.  This module only contributes reviewed
orthographic and multilingual labels to the rebuildable retrieval projection.
Hierarchy and related-concept edges are deliberately not expanded as synonyms.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

_DASH_RE = re.compile(r"[\u2010-\u2015\u2212\ufe58\ufe63\uff0d]")
_SPACE_RE = re.compile(r"\s+")
_LATIN_OR_NUMBER_RE = re.compile(r"[a-z0-9]", re.IGNORECASE)
_ACRONYM_RE = re.compile(r"[A-Z0-9][A-Z0-9+\-]{1,7}")


def ontology_normalized_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold()
    text = _DASH_RE.sub("-", text)
    return _SPACE_RE.sub(" ", text).strip()


def _case_preserving_text(value: str) -> str:
    text = _DASH_RE.sub("-", unicodedata.normalize("NFKC", value))
    return _SPACE_RE.sub(" ", text).strip()


def is_acronym_label(label: str) -> bool:
    """Upper-case abbreviations (MAR, REE, ICP-MS) match case-sensitively."""

    return bool(_ACRONYM_RE.fullmatch(label)) and sum(
        character.isupper() for character in label
    ) >= 2


def _label_pattern(label: str, *, case_sensitive: bool = False) -> re.Pattern[str]:
    escaped = re.escape(label)
    # Treat spaces and Unicode/ASCII hyphens as orthographic separators only.
    escaped = escaped.replace(r"\ ", r"[\s\-]*").replace(r"\-", r"[\s\-]*")
    if case_sensitive:
        return re.compile(r"(?<![A-Za-z0-9])" + escaped + r"(?![A-Za-z0-9])")
    if _LATIN_OR_NUMBER_RE.search(label):
        return re.compile(r"(?<![a-z0-9])" + escaped + r"(?![a-z0-9])")
    return re.compile(escaped)


@dataclass(frozen=True, slots=True)
class _LabelRule:
    concept_id: str
    label: str
    pattern: re.Pattern[str]
    case_sensitive: bool
    context_required: bool
    blocked_contexts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OntologyConcept:
    concept_id: str
    concept_type: str
    labels: tuple[str, ...]
    # Labels that are ambiguous on their own (MAR is also "mass accumulation
    # rate"): they count only when another label of the concept is present.
    context_required_labels: tuple[str, ...] = ()
    # label -> words in which an occurrence is not this concept (南海 inside
    # 东南海岸, 成岩 inside 形成岩浆).
    blocked_contexts: tuple[tuple[str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True, slots=True)
class ConceptMatch:
    concept: OntologyConcept
    label: str
    start: int
    end: int


class RetrievalOntology:
    """Resolve only explicitly reviewed labels; never infer scientific truth."""

    def __init__(
        self,
        *,
        revision: str,
        concepts: tuple[OntologyConcept, ...],
        relations: tuple[dict[str, str], ...] = (),
    ) -> None:
        self.revision = revision
        self.concepts = concepts
        self.relations = relations
        self._concepts_by_id = {concept.concept_id: concept for concept in concepts}
        rules: dict[str, list[_LabelRule]] = {}
        for concept in concepts:
            blocked = dict(concept.blocked_contexts)
            for label in concept.labels:
                normalized = ontology_normalized_text(label)
                if not normalized:
                    continue
                case_sensitive = is_acronym_label(label)
                rules.setdefault(concept.concept_id, []).append(
                    _LabelRule(
                        concept_id=concept.concept_id,
                        label=label,
                        pattern=_label_pattern(
                            _case_preserving_text(label)
                            if case_sensitive
                            else normalized,
                            case_sensitive=case_sensitive,
                        ),
                        case_sensitive=case_sensitive,
                        context_required=label in concept.context_required_labels,
                        blocked_contexts=tuple(
                            ontology_normalized_text(item)
                            for item in blocked.get(label, ())
                        ),
                    )
                )
        self._rules_by_concept = {key: tuple(value) for key, value in rules.items()}

    def _concept_matches(
        self, concept_id: str, normalized: str, case_text: str
    ) -> list[ConceptMatch]:
        concept = self._concepts_by_id[concept_id]
        found: list[tuple[_LabelRule, int, int]] = []
        for rule in self._rules_by_concept.get(concept_id, ()):
            text = case_text if rule.case_sensitive else normalized
            for occurrence in rule.pattern.finditer(text):
                start, end = occurrence.start(), occurrence.end()
                if rule.blocked_contexts and _inside_blocked_context(
                    normalized, start, end, rule.blocked_contexts
                ):
                    continue
                found.append((rule, start, end))
        if found and all(rule.context_required for rule, _start, _end in found):
            return []
        return [
            ConceptMatch(concept=concept, label=rule.label, start=start, end=end)
            for rule, start, end in found
        ]

    @classmethod
    def from_path(cls, path: Path) -> RetrievalOntology:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != "deepshovel-retrieval-ontology/1.0":
            raise ValueError("unsupported retrieval ontology schema")
        concepts: list[OntologyConcept] = []
        seen_ids: set[str] = set()
        seen_labels: dict[str, str] = {}
        for value in payload.get("concepts", []):
            concept_id = value["id"]
            if concept_id in seen_ids:
                raise ValueError(f"duplicate retrieval concept ID: {concept_id}")
            labels = tuple(dict.fromkeys(value.get("labels", [])))
            if not labels:
                raise ValueError(f"retrieval concept has no labels: {concept_id}")
            context_required = tuple(value.get("context_required_labels", []))
            blocked = value.get("blocked_contexts", {})
            if not set(context_required) <= set(labels) or not set(blocked) <= set(
                labels
            ):
                raise ValueError(
                    f"retrieval concept rule names an unknown label: {concept_id}"
                )
            if set(context_required) == set(labels):
                raise ValueError(
                    f"retrieval concept needs one context-free label: {concept_id}"
                )
            for label in labels:
                normalized = ontology_normalized_text(label)
                prior = seen_labels.get(normalized)
                if prior is not None and prior != concept_id:
                    raise ValueError(
                        "ambiguous exact ontology label "
                        f"{label!r}: {prior}, {concept_id}"
                    )
                seen_labels[normalized] = concept_id
            seen_ids.add(concept_id)
            concepts.append(
                OntologyConcept(
                    concept_id=concept_id,
                    concept_type=value["type"],
                    labels=labels,
                    context_required_labels=context_required,
                    blocked_contexts=tuple(
                        (label, tuple(items)) for label, items in blocked.items()
                    ),
                )
            )
        relations = tuple(payload.get("relations", []))
        for relation in relations:
            if (
                relation.get("subject") not in seen_ids
                or relation.get("object") not in seen_ids
            ):
                raise ValueError("ontology relation references an unknown concept")
        return cls(
            revision=payload["revision"],
            concepts=tuple(concepts),
            relations=relations,
        )

    def matches(
        self, value: str, *, suppress_nested: bool = True
    ) -> list[ConceptMatch]:
        normalized = ontology_normalized_text(value)
        case_text = _case_preserving_text(value)
        matches: list[ConceptMatch] = []
        seen: set[tuple[str, int, int]] = set()
        for concept in self.concepts:
            for match in self._concept_matches(
                concept.concept_id, normalized, case_text
            ):
                key = (concept.concept_id, match.start, match.end)
                if key in seen:
                    continue
                seen.add(key)
                matches.append(match)
        matches.sort(
            key=lambda item: (
                -(item.end - item.start),
                item.start,
                item.concept.concept_id,
            )
        )
        if suppress_nested:
            selected: list[ConceptMatch] = []
            for match in matches:
                if any(
                    other.concept.concept_id != match.concept.concept_id
                    and other.start <= match.start
                    and other.end >= match.end
                    for other in selected
                ):
                    continue
                selected.append(match)
            matches = selected
        return sorted(
            matches, key=lambda item: (item.start, item.end, item.concept.concept_id)
        )

    def concepts_in(
        self, value: str, *, suppress_nested: bool = True
    ) -> tuple[OntologyConcept, ...]:
        output: list[OntologyConcept] = []
        seen: set[str] = set()
        for match in self.matches(value, suppress_nested=suppress_nested):
            if match.concept.concept_id in seen:
                continue
            seen.add(match.concept.concept_id)
            output.append(match.concept)
        return tuple(output)

    def query_scoped_concept_ids(self, value: str, concept_ids: set[str]) -> set[str]:
        """Return only requested IDs using their own reviewed label patterns.

        This is the membership projection of ``concepts_in`` with nested-match
        suppression disabled. Patterns belonging to other concept IDs cannot
        affect that projection and are therefore not evaluated.
        """

        if not concept_ids:
            return set()
        normalized = ontology_normalized_text(value)
        case_text = _case_preserving_text(value)
        return {
            concept_id
            for concept_id in sorted(concept_ids)
            if concept_id in self._concepts_by_id
            and self._concept_matches(concept_id, normalized, case_text)
        }

    def aliases_for_query(self, value: str, *, maximum: int = 48) -> list[str]:
        aliases: list[str] = []
        seen = {ontology_normalized_text(value)}
        for concept in self.concepts_in(value):
            for label in concept.labels:
                normalized = ontology_normalized_text(label)
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                aliases.append(label)
                if len(aliases) >= maximum:
                    return aliases
        return aliases


def _inside_blocked_context(
    text: str, start: int, end: int, contexts: tuple[str, ...]
) -> bool:
    for context in contexts:
        position = text.find(context, max(0, end - len(context)))
        while position != -1 and position <= start:
            if position + len(context) >= end:
                return True
            position = text.find(context, position + 1)
    return False


@cache
def default_retrieval_ontology() -> RetrievalOntology:
    return RetrievalOntology.from_path(
        Path(__file__).with_name("retrieval_ontology.json")
    )


def concept_document_text(document: Any) -> str:
    """Bounded fields used only to test a reviewed concept label occurrence."""

    scope = document.scope
    scope_values: list[str] = []
    for name in (
        "location",
        "formation",
        "sample_type",
        "method",
        "condition",
        "measurement",
        "entities",
    ):
        scope_values.extend(getattr(scope, name) or [])
    abbreviations = [item.short_form for item in document.abbreviations]
    abbreviations.extend(
        item.long_form for item in document.abbreviations if item.long_form
    )
    return "\n".join(
        [
            document.canonical_claim,
            document.original_claim or "",
            document.paper_title,
            *document.entities,
            *abbreviations,
            *scope_values,
        ]
    )[:64_000]
