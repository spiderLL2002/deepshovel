from __future__ import annotations

from typing import Any

import pytest

from compiler_service.errors import (
    OpenAlexAuthenticationFailed,
    OpenAlexRateLimited,
    OpenAlexUnavailable,
)
from compiler_service.models import (
    ArtifactContext,
    DocumentBlock,
    DocumentIR,
    DocumentPage,
    OpenAlexClassificationRequest,
    ParserMetadata,
)
from compiler_service.openalex_topics import (
    OpenAlexTopicClassifier,
    classification_request,
)


def _topic(topic_id: int, name: str = "Evidence Synthesis") -> dict[str, Any]:
    return {
        "id": f"https://openalex.org/T{topic_id}",
        "display_name": name,
        "score": 0.8,
        "subfield": {
            "id": "https://openalex.org/subfields/1702",
            "display_name": "Artificial Intelligence",
        },
        "field": {
            "id": "https://openalex.org/fields/17",
            "display_name": "Computer Science",
        },
        "domain": {
            "id": "https://openalex.org/domains/3",
            "display_name": "Physical Sciences",
        },
    }


class _Response:
    def __init__(
        self,
        status_code: int,
        payload: Any,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError("unexpected HTTP failure")

    def json(self) -> Any:
        return self._payload


class _Client:
    def __init__(self, responses: list[_Response | Exception]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, *, params: dict[str, str]) -> _Response:
        self.requests.append((url, params))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _doi_document(
    pages: list[list[tuple[str, str]]], *, markdown: str = ""
) -> DocumentIR:
    return DocumentIR(
        artifact=ArtifactContext(
            paper_id="paper:doi-fixture",
            artifact_id="artifact:doi-fixture",
            version_id="artifact-version:doi-fixture",
            artifact_sha256="a" * 64,
            media_type="application/pdf",
            locator="paper.pdf",
            file_name="paper.pdf",
        ),
        parser=ParserMetadata(
            name="mineru",
            version="1",
            backend="pipeline",
            method="auto",
            transport="remote",
        ),
        page_count=len(pages),
        pages=[
            DocumentPage(
                number=page_index,
                blocks=[
                    DocumentBlock.model_validate(
                        {
                            "id": f"block:{page_index}:{block_index}",
                            "kind": kind,
                            "page": page_index,
                            "source_index": block_index,
                            "bbox": None,
                            "text": text,
                        }
                    )
                    for block_index, (kind, text) in enumerate(blocks)
                ],
            )
            for page_index, blocks in enumerate(pages, start=1)
        ],
        markdown=markdown,
    )


def test_document_fields_use_title_abstract_and_exact_doi() -> None:
    document = DocumentIR(
        artifact=ArtifactContext(
            paper_id="paper:one",
            artifact_id="artifact:one",
            version_id="artifact-version:one",
            artifact_sha256="a" * 64,
            media_type="application/pdf",
            locator="paper.pdf",
            file_name="paper.pdf",
        ),
        parser=ParserMetadata(
            name="mineru",
            version="1",
            backend="pipeline",
            method="auto",
            transport="remote",
        ),
        page_count=1,
        pages=[
            DocumentPage(
                number=1,
                blocks=[
                    DocumentBlock(
                        id="block:title",
                        kind="title",
                        page=1,
                        source_index=0,
                        bbox=None,
                        text="A classified paper",
                    ),
                    DocumentBlock(
                        id="block:abstract",
                        kind="paragraph",
                        page=1,
                        source_index=1,
                        bbox=None,
                        text="This is the abstract.",
                    ),
                    DocumentBlock(
                        id="block:doi",
                        kind="other",
                        page=1,
                        source_index=2,
                        bbox=None,
                        text="DOI: 10.1234/ABC.5",
                    ),
                ],
            )
        ],
        markdown=(
            "# A classified paper\n\n# Abstract\nThis is the abstract.\n\n"
            "# Introduction\nDOI: 10.1234/ABC.5"
        ),
    )
    request = classification_request(document)
    assert request.title == "A classified paper"
    assert request.abstract == "This is the abstract."
    assert request.doi == "10.1234/abc.5"


def test_article_doi_ignores_later_figure_dois_present_in_full_markdown() -> None:
    article_doi = "10.1371/journal.pone.0208230"
    document = _doi_document(
        [
            [("paragraph", f"Citation: https://doi.org/{article_doi}")],
            [("paragraph", "Introduction and first results.")],
            [("figure", f"Figure 1. https://doi.org/{article_doi}.g001")],
            [("table", f"Table 1. https://doi.org/{article_doi}.t001")],
        ],
        markdown=(
            f"# Article\nhttps://doi.org/{article_doi}\n\n"
            f"Figure 1. https://doi.org/{article_doi}.g001\n"
            f"Table 1. https://doi.org/{article_doi}.t001"
        ),
    )

    assert classification_request(document).doi == article_doi


def test_article_doi_ignores_unheaded_bibliography_in_full_markdown() -> None:
    article_doi = "10.24425/amm.2026.157780"
    document = _doi_document(
        [
            [("paragraph", f"DOI: https://doi.org/{article_doi}")],
            [("paragraph", "Current paper methods and results.")],
            [("paragraph", "Prior work: https://doi.org/10.9999/cited-study")],
        ],
        markdown=(
            f"DOI: https://doi.org/{article_doi}\n\n"
            "# Literature consulted\n"
            "Previous source https://doi.org/10.9999/cited-study\n"
            "Another source https://doi.org/10.8888/other-paper"
        ),
    )

    assert classification_request(document).doi == article_doi


def test_front_page_visual_and_citation_dois_cannot_override_article_identity() -> None:
    article_doi = "10.1371/journal.pone.0208230"
    document = _doi_document(
        [
            [
                ("paragraph", f"https://doi.org/{article_doi}"),
                ("figure", f"Figure DOI: https://doi.org/{article_doi}.g001"),
                ("table", f"Table DOI: https://doi.org/{article_doi}.t001"),
                ("citation", "Reference DOI: 10.9999/another-paper"),
            ],
        ],
    )

    assert classification_request(document).doi == article_doi


def test_second_page_doi_is_allowed_but_third_page_identity_is_not() -> None:
    article_doi = "10.1234/second-page"
    document = _doi_document(
        [
            [("title", "A paper without metadata on its first page")],
            [("other", f"https://doi.org/{article_doi}")],
            [("paragraph", "DOI: 10.9999/later-cited-work")],
        ],
        markdown="https://doi.org/10.9999/later-cited-work",
    )

    assert classification_request(document).doi == article_doi


def test_markdown_only_or_later_page_doi_never_authenticates_paper_identity() -> None:
    document = _doi_document(
        [
            [("title", "Paper with no DOI in actual front matter")],
            [("paragraph", "The first two pages contain no identity.")],
            [("paragraph", "DOI: 10.9999/later-cited-work")],
        ],
        markdown="DOI: 10.9999/later-cited-work",
    )

    assert classification_request(document).doi is None


def test_conflicting_front_matter_dois_still_fail_closed() -> None:
    document = _doi_document(
        [
            [("paragraph", "DOI: 10.1234/first-article")],
            [("other", "https://doi.org/10.5678/second-article")],
        ],
    )

    assert classification_request(document).doi is None


def test_explicit_reference_heading_stops_front_matter_identity_scan() -> None:
    article_doi = "10.1234/current-paper"
    document = _doi_document(
        [
            [
                ("paragraph", f"DOI: {article_doi}"),
                ("title", "References"),
                ("paragraph", "DOI: 10.9999/cited-work"),
            ],
            [("paragraph", "DOI: 10.8888/another-cited-work")],
        ],
    )

    assert classification_request(document).doi == article_doi


def test_parser_whitespace_after_doi_resolver_slash_preserves_exact_identity() -> None:
    article_doi = "10.1038/s43247-026-03848-7"
    document = _doi_document(
        [[("paragraph", f"Cite this article: https://doi.org/ {article_doi}")]],
    )

    assert classification_request(document).doi == article_doi


def test_joined_journal_label_requires_an_independently_corroborated_doi() -> None:
    article_doi = "10.1038/s43247-026-03848-7"
    document = _doi_document(
        [
            [
                (
                    "paragraph",
                    f"Cite this article as: https://doi.org/ {article_doi}",
                ),
                (
                    "other",
                    f"https://doi.org/{article_doi}Communications Earth & Environment",
                ),
            ],
        ],
        markdown=(f"https://doi.org/{article_doi}Communications Earth & Environment"),
    )

    assert classification_request(document).doi == article_doi


def test_joined_journal_label_without_independent_doi_never_guesses_a_split() -> None:
    document = _doi_document(
        [
            [
                (
                    "other",
                    "https://doi.org/10.1038/s43247-026-03848-7Communications "
                    "Earth & Environment",
                ),
            ],
        ],
    )

    assert classification_request(document).doi is None


def test_legitimate_communications_doi_suffix_is_never_generically_truncated() -> None:
    article_doi = "10.1234/researchcommunications"
    document = _doi_document(
        [[("paragraph", f"DOI: {article_doi}")]],
    )

    assert classification_request(document).doi == article_doi


def test_openalex_work_topics_are_used_without_running_local_model() -> None:
    topic = _topic(100)
    client = _Client(
        [
            _Response(
                200,
                {
                    "id": "https://openalex.org/W1",
                    "topics": [topic],
                    "primary_topic": topic,
                },
            )
        ]
    )

    def forbidden_pipeline(**_: Any) -> Any:
        raise AssertionError("local model must not run for an OpenAlex Work match")

    classifier = OpenAlexTopicClassifier(
        client=client,
        pipeline_factory=forbidden_pipeline,
    )
    result = classifier.classify(
        OpenAlexClassificationRequest(title="Paper", doi="10.1234/example")
    )
    assert result.source == "openalex_work"
    assert result.openalex_work_id == "https://openalex.org/W1"
    assert result.primary_topic.id == "https://openalex.org/T100"
    assert len(client.requests) == 1


def test_openalex_uses_official_short_doi_and_api_key() -> None:
    topic = _topic(100)
    client = _Client(
        [_Response(200, {"id": "https://openalex.org/W1", "topics": [topic]})]
    )
    classifier = OpenAlexTopicClassifier(api_key="test-key", client=client)

    classifier.classify(
        OpenAlexClassificationRequest(title="Paper", doi="10.1234/example")
    )

    url, params = client.requests[0]
    assert url.endswith("/works/doi:10.1234/example")
    assert params["api_key"] == "test-key"


def test_openalex_retries_transient_504_with_bounded_backoff() -> None:
    topic = _topic(100)
    client = _Client(
        [
            _Response(504, {"error": "Gateway Timeout"}),
            _Response(200, {"id": "https://openalex.org/W1", "topics": [topic]}),
        ]
    )
    sleeps: list[float] = []
    classifier = OpenAlexTopicClassifier(
        client=client,
        sleep=sleeps.append,
    )

    result = classifier.classify(
        OpenAlexClassificationRequest(title="Paper", doi="10.1234/example")
    )

    assert result.source == "openalex_work"
    assert sleeps == [1.0]
    assert len(client.requests) == 2


def test_openalex_reports_repeated_504_as_retryable_unavailable() -> None:
    client = _Client([_Response(504, {}) for _ in range(3)])
    sleeps: list[float] = []
    classifier = OpenAlexTopicClassifier(client=client, sleep=sleeps.append)

    with pytest.raises(OpenAlexUnavailable, match="HTTP 504") as caught:
        classifier.classify(
            OpenAlexClassificationRequest(title="Paper", doi="10.1234/example")
        )

    assert caught.value.code == "OPENALEX_UNAVAILABLE"
    assert caught.value.retryable is True
    assert sleeps == [1.0, 2.0]


def test_openalex_authentication_failure_is_not_retried() -> None:
    client = _Client([_Response(401, {})])
    classifier = OpenAlexTopicClassifier(client=client, sleep=lambda _: None)

    with pytest.raises(OpenAlexAuthenticationFailed, match="HTTP 401"):
        classifier.classify(
            OpenAlexClassificationRequest(title="Paper", doi="10.1234/example")
        )

    assert len(client.requests) == 1


def test_openalex_rate_limit_respects_bounded_retry_after() -> None:
    client = _Client(
        [
            _Response(429, {}, headers={"retry-after": "90"}),
            _Response(429, {}, headers={"retry-after": "invalid"}),
            _Response(429, {}),
        ]
    )
    sleeps: list[float] = []
    classifier = OpenAlexTopicClassifier(client=client, sleep=sleeps.append)

    with pytest.raises(OpenAlexRateLimited, match="3 attempts") as caught:
        classifier.classify(
            OpenAlexClassificationRequest(title="Paper", doi="10.1234/example")
        )

    assert caught.value.code == "OPENALEX_RATE_LIMITED"
    assert sleeps == [30.0, 2.0]


def test_official_title_abstract_model_falls_back_and_hydrates_hierarchy() -> None:
    client = _Client(
        [
            _Response(404, None),
            _Response(
                200,
                {"results": [_topic(11048), _topic(12048, "Second")]},
            ),
        ]
    )
    factory_calls: list[dict[str, Any]] = []
    model_inputs: list[str] = []

    class Pipeline:
        def __call__(self, value: str) -> list[dict[str, Any]]:
            model_inputs.append(value)
            return [
                {"label": "1048: Evidence Synthesis", "score": 0.7},
                {"label": "2048: Second", "score": 0.2},
                {"label": "3048: Retired", "score": 0.1},
            ]

    def factory(**kwargs: Any) -> Pipeline:
        factory_calls.append(kwargs)
        return Pipeline()

    classifier = OpenAlexTopicClassifier(client=client, pipeline_factory=factory)
    result = classifier.classify(
        OpenAlexClassificationRequest(
            title="Paper title",
            abstract="Paper abstract",
            doi="10.1234/not-found",
        )
    )
    assert result.source == "official_title_abstract_model"
    assert result.matched_doi is None
    assert [topic.id for topic in result.topics] == [
        "https://openalex.org/T11048",
        "https://openalex.org/T12048",
    ]
    assert model_inputs == ["<TITLE> Paper title\n<ABSTRACT> Paper abstract"]
    assert client.requests[1][1]["filter"] == "id:T11048|T12048|T13048"
    assert factory_calls[0]["model"].startswith("OpenAlex/")
    assert factory_calls[0]["top_k"] == 10


def test_reference_list_doi_is_not_used_as_the_current_paper_identity() -> None:
    document = DocumentIR(
        artifact=ArtifactContext(
            paper_id="paper:one",
            artifact_id="artifact:one",
            version_id="artifact-version:one",
            artifact_sha256="a" * 64,
            media_type="application/pdf",
            locator="paper.pdf",
            file_name="paper.pdf",
        ),
        parser=ParserMetadata(
            name="mineru",
            version="1",
            backend="pipeline",
            method="auto",
            transport="remote",
        ),
        page_count=2,
        pages=[
            DocumentPage(
                number=1,
                blocks=[
                    DocumentBlock(
                        id="block:title",
                        kind="title",
                        page=1,
                        source_index=0,
                        bbox=None,
                        text="Paper without a front-matter DOI",
                    )
                ],
            ),
            DocumentPage(
                number=2,
                blocks=[
                    DocumentBlock(
                        id="block:body",
                        kind="paragraph",
                        page=2,
                        source_index=1,
                        bbox=None,
                        text="Body text without a DOI.",
                    ),
                    DocumentBlock(
                        id="block:reference",
                        kind="citation",
                        page=2,
                        source_index=2,
                        bbox=None,
                        text="Reference. https://doi.org/10.9999/cited-work",
                    ),
                ],
            ),
        ],
        markdown=(
            "# Paper without a front-matter DOI\n\n# Introduction\nText.\n\n"
            "# References\nReference. https://doi.org/10.9999/cited-work"
        ),
    )
    assert classification_request(document).doi is None
