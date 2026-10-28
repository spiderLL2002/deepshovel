"""Strict HTTP and normalized document contracts."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)

from .shared_contract import NORMALIZED_REGION_EXTENT_MAX

Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z][A-Za-z0-9._:-]*$",
    ),
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
MediaType = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=255,
        pattern=r"^[^\s/]+/[^\s/]+$",
    ),
]
ShortText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1_000)
]


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        validate_default=True,
        allow_inf_nan=False,
    )


class ArtifactContext(StrictModel):
    paper_id: Identifier
    artifact_id: Identifier
    version_id: Identifier
    artifact_sha256: Sha256
    media_type: MediaType
    locator: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000),
    ]
    file_name: Annotated[str, StringConstraints(min_length=1, max_length=180)]


class BoundingBox(StrictModel):
    x: Annotated[float, Field(ge=0, le=1)]
    y: Annotated[float, Field(ge=0, le=1)]
    width: Annotated[float, Field(gt=0, le=1)]
    height: Annotated[float, Field(gt=0, le=1)]

    @model_validator(mode="after")
    def inside_page(self) -> BoundingBox:
        limit = NORMALIZED_REGION_EXTENT_MAX
        if self.x + self.width > limit or self.y + self.height > limit:
            raise ValueError("bounding box must fit in normalized page")
        return self


BlockKind = Literal[
    "title",
    "paragraph",
    "list",
    "equation",
    "figure",
    "table",
    "chart",
    "image",
    "citation",
    "other",
]


class DocumentBlock(StrictModel):
    id: Identifier
    kind: BlockKind
    page: Annotated[int, Field(ge=1)]
    source_index: Annotated[int, Field(ge=0)]
    bbox: BoundingBox | None
    text: str | None = Field(default=None, max_length=100_000)
    level: Annotated[int, Field(ge=1, le=12)] | None = None
    structured: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def nonempty(self) -> DocumentBlock:
        if self.text is None and not self.structured:
            raise ValueError("document block must retain text or structured content")
        return self


class DocumentPage(StrictModel):
    number: Annotated[int, Field(ge=1)]
    width: Annotated[float, Field(gt=0)] | None = None
    height: Annotated[float, Field(gt=0)] | None = None
    blocks: list[DocumentBlock]


class ParserMetadata(StrictModel):
    name: Literal["mineru"]
    version: ShortText
    backend: Literal["pipeline"]
    method: Literal["auto"]
    transport: Literal["remote"]


class DocumentIR(StrictModel):
    schema_version: Literal["document-ir/1.0"] = "document-ir/1.0"
    artifact: ArtifactContext
    parser: ParserMetadata
    page_count: Annotated[int, Field(ge=1)]
    pages: list[DocumentPage]
    markdown: str = Field(max_length=8_000_000)
    warnings: list[ShortText] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent_pages(self) -> DocumentIR:
        expected = list(range(1, self.page_count + 1))
        if [page.number for page in self.pages] != expected:
            raise ValueError("pages must be complete and ordered from 1")
        ids: set[str] = set()
        for page in self.pages:
            for block in page.blocks:
                if block.page != page.number:
                    raise ValueError(f"block {block.id} is assigned to the wrong page")
                if block.id in ids:
                    raise ValueError(f"duplicate block id: {block.id}")
                ids.add(block.id)
        return self

    def source_text(self) -> str:
        parts = [
            block.text for page in self.pages for block in page.blocks if block.text
        ]
        return "\n\n".join(parts)


class ParseRun(StrictModel):
    run_id: Identifier
    backend: Literal["mineru-pipeline"]
    backend_version: ShortText
    duration_ms: Annotated[int, Field(ge=0)]


class ParseResponse(StrictModel):
    run: ParseRun
    document: DocumentIR


class CompileRequest(StrictModel):
    document: DocumentIR
    verify: bool = True


class ModelRun(StrictModel):
    run_id: Identifier
    model: Literal["gpt-6-luna", "gpt-5.6-luna", "deepseek-v4-flash"]
    prompt_version: ShortText


class VerificationEnvelope(ModelRun):
    report: dict[str, JsonValue]


class OpenAlexHierarchyNode(StrictModel):
    id: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=500)]


class OpenAlexTopic(StrictModel):
    id: Annotated[str, StringConstraints(pattern=r"^https://openalex\.org/T[0-9]+$")]
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    score: Annotated[float, Field(ge=0, le=1)]
    subfield: OpenAlexHierarchyNode
    field: OpenAlexHierarchyNode
    domain: OpenAlexHierarchyNode


class OpenAlexClassification(StrictModel):
    source: Literal["openalex_work", "official_title_abstract_model"]
    classifier: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    openalex_work_id: Annotated[str, StringConstraints(max_length=100)] | None = None
    matched_doi: Annotated[str, StringConstraints(max_length=500)] | None = None
    primary_topic: OpenAlexTopic
    topics: Annotated[list[OpenAlexTopic], Field(min_length=1, max_length=3)]

    @model_validator(mode="after")
    def primary_is_first_and_topics_are_unique(self) -> OpenAlexClassification:
        ids = [topic.id for topic in self.topics]
        if len(ids) != len(set(ids)):
            raise ValueError("OpenAlex topics must be unique")
        if self.primary_topic != self.topics[0]:
            raise ValueError("OpenAlex primary_topic must equal topics[0]")
        return self


class OpenAlexClassificationRequest(StrictModel):
    title: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000),
    ]
    abstract: (
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, min_length=1, max_length=100_000),
        ]
        | None
    ) = None
    doi: (
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
        ]
        | None
    ) = None


AuditCode = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$"),
]


class CompilerAuditEntry(StrictModel):
    kind: AuditCode
    target: Identifier | None
    detail: Annotated[str, StringConstraints(min_length=1, max_length=300)] | None


class CompilerAuditRecord(StrictModel):
    """Value-free list of Compiler rewrites applied to model output."""

    schema_version: Literal["compiler-audit/1.0"]
    entries: Annotated[list[CompilerAuditEntry], Field(max_length=512)]
    omitted_entries: Annotated[int, Field(ge=0)]
    warnings: Annotated[
        list[Annotated[str, StringConstraints(min_length=1, max_length=300)]],
        Field(max_length=64),
    ]


class ImportEnvelope(StrictModel):
    graph: dict[str, JsonValue]
    root_artifact_version_id: Identifier
    extraction: ModelRun
    verification: VerificationEnvelope | None = None
    openalex: OpenAlexClassification | None = None
    compiler_audit: CompilerAuditRecord | None = None


JobStatus = Literal[
    "queued", "parsing", "extracting", "validating", "completed", "failed"
]


class StageTimings(StrictModel):
    queued: Annotated[int, Field(ge=0)] | None = None
    parsing: Annotated[int, Field(ge=0)] | None = None
    extracting: Annotated[int, Field(ge=0)] | None = None
    validating: Annotated[int, Field(ge=0)] | None = None
    total: Annotated[int, Field(ge=0)] | None = None


class JobFailure(StrictModel):
    code: ShortText
    message: Annotated[str, StringConstraints(min_length=1, max_length=4_000)]
    retryable: bool


class JobLinks(StrictModel):
    self: str
    result: str


class CompileJob(StrictModel):
    id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f-]{36}$")]
    status: JobStatus
    created_at: str
    updated_at: str
    expires_at: str
    stage_timings_ms: StageTimings
    error: JobFailure | None
    links: JobLinks


class JobResponse(StrictModel):
    schema_version: Literal["compiler-job/1.0"] = "compiler-job/1.0"
    job: CompileJob


class JobListResponse(StrictModel):
    """Existing jobs among the requested IDs, in request order."""

    schema_version: Literal["compiler-job-list/1.0"] = "compiler-job-list/1.0"
    jobs: list[CompileJob]


def sanitize_file_name(value: str) -> str:
    cleaned = re.sub(r"[\\/\x00-\x1f\x7f]", "_", value).strip()
    return cleaned[:180]
