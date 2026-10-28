"""OpenAlex-official paper topic assignment and hierarchy hydration."""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from .config import OPENALEX_API_URL, OPENALEX_TOPIC_MODEL
from .errors import (
    CompilerError,
    OpenAlexAuthenticationFailed,
    OpenAlexInvalidResponse,
    OpenAlexRateLimited,
    OpenAlexRequestRejected,
    OpenAlexUnavailable,
)
from .models import (
    DocumentIR,
    OpenAlexClassification,
    OpenAlexClassificationRequest,
    OpenAlexHierarchyNode,
    OpenAlexTopic,
)

_DOI_LOCATOR_RE = re.compile(
    r"(?:\bdoi\s*:\s*|https?://(?:dx\.)?doi\.org/\s*)"
    r"(?P<doi>10\.\d{4,9}/[-._;()/:A-Z0-9]+)",
    re.IGNORECASE,
)
_REFERENCES_HEADING_RE = re.compile(
    r"(?im)^\s*#{0,6}\s*(?:references?|bibliography|works?\s+cited|参考文献)\s*$"
)
_ABSTRACT_HEADING_RE = re.compile(
    r"(?ims)^#{0,6}\s*abstract\s*[:.]?\s*$\n(?P<body>.+?)(?=^#{1,6}\s+|\Z)"
)
_INLINE_ABSTRACT_RE = re.compile(
    r"(?ims)^\s*abstract\s*[:.—-]\s*(?P<body>.+?)(?=\n\s*(?:keywords?|introduction)\s*[:.]?\s*$|\Z)"
)
_TRAILING_DOI_PUNCTUATION = ".,;:)]}>"
_JOINED_JOURNAL_CONTINUATION_RE = re.compile(
    r"\A\s+Earth\s*&\s*Environment\b", re.IGNORECASE
)
_OPENALEX_ATTEMPTS = 3
_OPENALEX_BACKOFF_SECONDS = (1.0, 2.0)
_OPENALEX_MAX_RETRY_AFTER_SECONDS = 30.0


def _compact(value: str) -> str:
    return " ".join(value.split())


def _title(document: DocumentIR) -> str:
    for page in document.pages:
        for block in page.blocks:
            if block.kind != "title":
                continue
            title = _compact(block.text or "")
            if title:
                return title
    stem = Path(document.artifact.file_name).stem.replace("_", " ").replace("-", " ")
    return _compact(stem) or document.artifact.file_name


def _abstract(document: DocumentIR) -> str | None:
    for pattern in (_ABSTRACT_HEADING_RE, _INLINE_ABSTRACT_RE):
        match = pattern.search(document.markdown)
        if match:
            value = _compact(match.group("body"))
            if value:
                return value[:100_000]

    blocks = [block for page in document.pages for block in page.blocks]
    for index, block in enumerate(blocks):
        text = _compact(block.text or "")
        normalized = text.casefold().rstrip(":.")
        if normalized == "abstract":
            paragraphs: list[str] = []
            total_length = 0
            for following in blocks[index + 1 :]:
                candidate = _compact(following.text or "")
                if not candidate:
                    continue
                if following.kind == "title" or candidate.casefold().rstrip(":.") in {
                    "keywords",
                    "introduction",
                }:
                    break
                paragraphs.append(candidate)
                total_length += len(candidate)
                if total_length >= 100_000:
                    break
            value = _compact(" ".join(paragraphs))
            return value[:100_000] or None
        if normalized.startswith("abstract "):
            value = _compact(re.sub(r"(?i)^abstract\s*[:.—-]?\s*", "", text))
            return value[:100_000] or None
    return None


def document_doi(document: DocumentIR) -> str | None:
    # A DOI found anywhere in full text is very often a cited work's DOI.  Work
    # identity is security-sensitive for Citation Reception, so only accept an
    # explicitly labelled DOI on the first two actual DocumentIR pages. Markdown
    # has no trustworthy page boundary and may already contain figure DOIs or an
    # unheaded bibliography within its first 50,000 characters.
    observations: list[tuple[str, str]] = []
    reached_references = False
    for page in document.pages:
        if page.number > 2 or reached_references:
            break
        for block in page.blocks[:50]:
            text = block.text or ""
            if block.kind == "title" and _REFERENCES_HEADING_RE.search(text):
                reached_references = True
                break
            if block.kind in {"citation", "figure", "table", "chart", "image"}:
                continue
            for match in _DOI_LOCATOR_RE.finditer(text):
                value = match.group("doi").rstrip(_TRAILING_DOI_PUNCTUATION)
                observations.append((value.lower(), text[match.end() :]))

    observed = {value for value, _ in observations}
    candidates: set[str] = set()
    for value, following in observations:
        # MinerU can concatenate a Nature article DOI with the next journal
        # label: "...03848-7Communications Earth & Environment". Never guess a
        # DOI split: repair this exact, independently observable boundary only
        # when another first-page/two-page block supplies the complete DOI.
        if value.endswith("communications") and (
            _JOINED_JOURNAL_CONTINUATION_RE.match(following)
        ):
            corroborated = value.removesuffix("communications")
            if corroborated not in observed:
                return None
            value = corroborated
        candidates.add(value)
    return next(iter(candidates)) if len(candidates) == 1 else None


def classification_request(document: DocumentIR) -> OpenAlexClassificationRequest:
    return OpenAlexClassificationRequest(
        title=_title(document),
        abstract=_abstract(document),
        doi=document_doi(document),
    )


class OpenAlexTopicClassifier:
    """Use OpenAlex Work data first, then its official title/abstract model."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        cache_dir: Path | None = None,
        client: httpx.Client | Any | None = None,
        pipeline_factory: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_key = api_key
        self.cache_dir = cache_dir
        # OpenAlex may return 301 when an entity was merged; its official
        # singleton guide requires clients to follow that canonical identity.
        self.client = client or httpx.Client(timeout=30.0, follow_redirects=True)
        self.pipeline_factory = pipeline_factory
        self.sleep = sleep
        self._pipeline: Any | None = None
        self._model_lock = threading.Lock()

    @property
    def model_name(self) -> str:
        return OPENALEX_TOPIC_MODEL

    @property
    def ready(self) -> bool:
        return self._pipeline is not None

    def _parameters(self) -> dict[str, str]:
        return {} if self.api_key is None else {"api_key": self.api_key}

    def _get(self, path: str, *, params: Mapping[str, str] | None = None) -> Any:
        merged = {**self._parameters(), **dict(params or {})}
        last_failure: tuple[str, int | None] | None = None
        for attempt in range(_OPENALEX_ATTEMPTS):
            try:
                response = self.client.get(f"{OPENALEX_API_URL}{path}", params=merged)
            except httpx.TimeoutException as exc:
                last_failure = ("timeout", None)
                if attempt == _OPENALEX_ATTEMPTS - 1:
                    raise OpenAlexUnavailable("OpenAlex API timed out") from exc
            except httpx.HTTPError as exc:
                last_failure = ("connection", None)
                if attempt == _OPENALEX_ATTEMPTS - 1:
                    raise OpenAlexUnavailable("OpenAlex API connection failed") from exc
            else:
                status = int(response.status_code)
                if status == 404:
                    return None
                if 200 <= status < 300:
                    try:
                        return response.json()
                    except (TypeError, ValueError) as exc:
                        last_failure = ("invalid_json", status)
                        if attempt == _OPENALEX_ATTEMPTS - 1:
                            raise OpenAlexInvalidResponse(
                                "OpenAlex API returned invalid JSON"
                            ) from exc
                elif status in {401, 403}:
                    raise OpenAlexAuthenticationFailed(
                        f"OpenAlex API rejected authentication with HTTP {status}"
                    )
                elif status == 429:
                    last_failure = ("rate_limited", status)
                    if attempt == _OPENALEX_ATTEMPTS - 1:
                        raise OpenAlexRateLimited(
                            "OpenAlex API remained rate limited after 3 attempts"
                        )
                elif status >= 500:
                    last_failure = ("provider_error", status)
                    if attempt == _OPENALEX_ATTEMPTS - 1:
                        raise OpenAlexUnavailable(
                            f"OpenAlex API remained unavailable with HTTP {status} "
                            "after 3 attempts"
                        )
                else:
                    raise OpenAlexRequestRejected(
                        f"OpenAlex API rejected the request with HTTP {status}"
                    )

            if attempt < _OPENALEX_ATTEMPTS - 1:
                delay = _OPENALEX_BACKOFF_SECONDS[attempt]
                if last_failure == ("rate_limited", 429):
                    retry_after = response.headers.get("retry-after")
                    if retry_after is not None:
                        with suppress(ValueError):
                            delay = min(
                                max(float(retry_after), 0.0),
                                _OPENALEX_MAX_RETRY_AFTER_SECONDS,
                            )
                self.sleep(delay)

        raise AssertionError(f"unreachable OpenAlex retry state: {last_failure}")

    def _work(self, doi: str | None) -> dict[str, Any] | None:
        if doi is None:
            return None
        payload = self._get(
            f"/works/{quote(f'doi:{doi}', safe=':/')}",
            params={"select": "id,doi,primary_topic,topics"},
        )
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _hierarchy(value: Any) -> OpenAlexHierarchyNode:
        if not isinstance(value, dict):
            raise CompilerError("OpenAlex topic hierarchy is incomplete")
        return OpenAlexHierarchyNode.model_validate(value, strict=True)

    @classmethod
    def _topic(cls, value: Any, *, score: float | None = None) -> OpenAlexTopic:
        if not isinstance(value, dict):
            raise CompilerError("OpenAlex returned an invalid Topic")
        try:
            return OpenAlexTopic(
                id=value["id"],
                display_name=value["display_name"],
                score=float(value["score"] if score is None else score),
                subfield=cls._hierarchy(value["subfield"]),
                field=cls._hierarchy(value["field"]),
                domain=cls._hierarchy(value["domain"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CompilerError("OpenAlex returned an invalid Topic") from exc

    def _classification_from_work(
        self, work: dict[str, Any], doi: str | None
    ) -> OpenAlexClassification | None:
        values = work.get("topics")
        if not isinstance(values, list) or not values:
            return None
        topics = [self._topic(value) for value in values[:3]]
        primary_value = work.get("primary_topic")
        if isinstance(primary_value, dict):
            primary_id = primary_value.get("id")
            topics.sort(key=lambda topic: topic.id != primary_id)
        return OpenAlexClassification(
            source="openalex_work",
            classifier="OpenAlex Work API Topics",
            openalex_work_id=(
                work.get("id") if isinstance(work.get("id"), str) else None
            ),
            matched_doi=doi,
            primary_topic=topics[0],
            topics=topics,
        )

    def _active_pipeline(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        factory = self.pipeline_factory
        if factory is None:
            try:
                from transformers import pipeline
            except ImportError as exc:
                raise CompilerError(
                    "OpenAlex official topic model is unavailable"
                ) from exc
            factory = pipeline
        kwargs: dict[str, Any] = {
            "task": "text-classification",
            "model": OPENALEX_TOPIC_MODEL,
            "top_k": 10,
            "truncation": True,
            "max_length": 512,
            "device": -1,
        }
        if self.cache_dir is not None:
            kwargs["model_kwargs"] = {"cache_dir": str(self.cache_dir)}
        self._pipeline = factory(**kwargs)
        return self._pipeline

    def warmup(self) -> None:
        with self._model_lock:
            self._active_pipeline()

    def _predictions(
        self, request: OpenAlexClassificationRequest
    ) -> list[tuple[str, float]]:
        content = f"<TITLE> {request.title}"
        if request.abstract:
            content += f"\n<ABSTRACT> {request.abstract}"
        with self._model_lock:
            result = self._active_pipeline()(content)
        if result and isinstance(result, list) and isinstance(result[0], list):
            result = result[0]
        if not isinstance(result, list):
            raise CompilerError("OpenAlex official topic model returned invalid output")
        predictions: list[tuple[str, float]] = []
        for item in result:
            if not isinstance(item, dict):
                continue
            label = item.get("label")
            score = item.get("score")
            match = (
                re.match(r"^(?:T)?([0-9]+)\s*:", label)
                if isinstance(label, str)
                else None
            )
            if match and isinstance(score, (int, float)):
                # The official classifier labels use the original 1–4521 topic
                # numbers. OpenAlex's public Topic entity IDs are those numbers
                # in the T10001+ namespace (for example 1048 -> T11048).
                label_id = int(match.group(1))
                topic_id = label_id if label_id >= 10_000 else label_id + 10_000
                predictions.append((f"https://openalex.org/T{topic_id}", float(score)))
        if not predictions:
            raise CompilerError("OpenAlex official topic model returned no Topics")
        return predictions

    def _hydrate_predictions(
        self, predictions: Sequence[tuple[str, float]]
    ) -> list[OpenAlexTopic]:
        short_ids = [topic_id.rsplit("/", 1)[-1] for topic_id, _ in predictions]
        payload = self._get(
            "/topics",
            params={
                "filter": f"id:{'|'.join(short_ids)}",
                "select": "id,display_name,subfield,field,domain",
                "per-page": str(len(short_ids)),
            },
        )
        values = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(values, list):
            raise CompilerError("OpenAlex Topics API returned invalid output")
        by_id = {
            value.get("id"): value
            for value in values
            if isinstance(value, dict) and isinstance(value.get("id"), str)
        }
        topics: list[OpenAlexTopic] = []
        for topic_id, score in predictions:
            value = by_id.get(topic_id)
            # The published title/abstract checkpoint has 4,521 labels while
            # the current OpenAlex vocabulary has 4,516 Topics. Retired labels
            # are ignored in favor of the next ranked current official Topic.
            if value is None:
                continue
            topics.append(self._topic(value, score=score))
            if len(topics) == 3:
                break
        if not topics:
            raise CompilerError("OpenAlex Topics API returned no current Topics")
        return topics

    def classify(
        self, request: OpenAlexClassificationRequest
    ) -> OpenAlexClassification:
        work = self._work(request.doi)
        if work is not None:
            classification = self._classification_from_work(work, request.doi)
            if classification is not None:
                return classification
        topics = self._hydrate_predictions(self._predictions(request))
        return OpenAlexClassification(
            source="official_title_abstract_model",
            classifier=OPENALEX_TOPIC_MODEL,
            # A failed Work lookup does not authenticate the supplied DOI.
            # Keep it out of the local-work resolver used by Citation Reception.
            matched_doi=None,
            primary_topic=topics[0],
            topics=topics,
        )

    def classify_document(self, document: DocumentIR) -> OpenAlexClassification:
        return self.classify(classification_request(document))
