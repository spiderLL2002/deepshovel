from __future__ import annotations

import hashlib
import unicodedata

import pytest

from compiler_service.models import (
    ArtifactContext,
    DocumentBlock,
    DocumentIR,
    DocumentPage,
    ParserMetadata,
)
from compiler_service.source_catalog import (
    DEFAULT_MAX_OWNED_SPANS_PER_SHARD,
    MAX_OWNED_SPANS_PER_SHARD,
    MAX_SOURCE_SPAN_CHARS,
    SourceBoundingBox,
    build_source_catalog,
    resolve_source_span_text,
)


def _artifact(*, artifact_id: str = "artifact:source-catalog") -> ArtifactContext:
    digest = hashlib.sha256(artifact_id.encode()).hexdigest()
    return ArtifactContext(
        paper_id=f"paper:{digest}",
        artifact_id=artifact_id,
        version_id=f"artifact-version:sha256:{digest}",
        artifact_sha256=digest,
        media_type="application/pdf",
        locator="upload://source-catalog.pdf",
        file_name="source-catalog.pdf",
    )


def _block(
    identifier: str,
    text: str | None,
    *,
    page: int = 1,
    source_index: int = 0,
    kind: str = "paragraph",
    level: int | None = None,
    structured: dict | None = None,
    bbox: bool = True,
) -> DocumentBlock:
    return DocumentBlock.model_validate(
        {
            "id": identifier,
            "kind": kind,
            "page": page,
            "source_index": source_index,
            "bbox": (
                {"x": 0.1, "y": 0.2, "width": 0.7, "height": 0.1} if bbox else None
            ),
            "text": text,
            "level": level,
            "structured": structured or {},
        },
        strict=True,
    )


def _document(
    pages: list[list[DocumentBlock]],
    *,
    artifact: ArtifactContext | None = None,
) -> DocumentIR:
    return DocumentIR(
        artifact=artifact or _artifact(),
        parser=ParserMetadata(
            name="mineru",
            version="test",
            backend="pipeline",
            method="auto",
            transport="remote",
        ),
        page_count=len(pages),
        pages=[
            DocumentPage(number=index, width=1000, height=1400, blocks=blocks)
            for index, blocks in enumerate(pages, start=1)
        ],
        markdown="test",
        warnings=[],
    )


def test_unicode_spans_are_exact_source_substrings_and_nfc_verifiable() -> None:
    text = "  Cafe\u0301。  第二句；emoji 👩‍🔬！\n最后一行  "
    block = _block("block.unicode", text)
    document = _document([[block]])

    catalog = build_source_catalog(document)

    assert [span.exact_text for span in catalog.spans] == [
        "Cafe\u0301。",
        "第二句；",
        "emoji 👩‍🔬！",
        "最后一行",
    ]
    for span in catalog.spans:
        exact = text[span.start_offset : span.end_offset]
        assert span.exact_text == exact
        assert span.nfc_text == unicodedata.normalize("NFC", exact)
        assert len(span.exact_text) <= MAX_SOURCE_SPAN_CHARS
    assert catalog.spans[0].nfc_text.removesuffix("。") == "Café"


def test_unpunctuated_text_uses_bounded_exact_fallback_and_stable_hashes() -> None:
    text = "甲" * 4_501
    document = _document([[_block("block.long", text)]])

    first = build_source_catalog(document)
    second = build_source_catalog(document)

    assert [len(span.exact_text) for span in first.spans] == [2_000, 2_000, 501]
    assert [(span.start_offset, span.end_offset) for span in first.spans] == [
        (0, 2_000),
        (2_000, 4_000),
        (4_000, 4_501),
    ]
    assert [span.id for span in first.spans] == [span.id for span in second.spans]
    assert [shard.id for shard in first.shards] == [shard.id for shard in second.shards]

    other = _document(
        [[_block("block.long", text)]],
        artifact=_artifact(artifact_id="artifact:other-source"),
    )
    assert first.spans[0].id != build_source_catalog(other).spans[0].id


def test_tables_and_equations_keep_text_but_structured_only_never_fakes_quote() -> None:
    blocks = [
        _block(
            "block.table.text",
            "表1。测量值为5 mg。",
            source_index=0,
            kind="table",
            structured={"table_body": "<table><td>5 mg</td></table>"},
        ),
        _block(
            "block.equation",
            "E=mc²。\n该式用于计算。",
            source_index=1,
            kind="equation",
        ),
        _block(
            "block.table.structured-only",
            None,
            source_index=2,
            kind="table",
            structured={"table_body": "<table><td>99</td></table>"},
        ),
    ]

    catalog = build_source_catalog(_document([blocks]))

    assert {span.kind for span in catalog.spans} == {"table", "equation"}
    assert all(span.block_id != "block.table.structured-only" for span in catalog.spans)
    table_span = next(span for span in catalog.spans if span.kind == "table")
    assert table_span.bbox == SourceBoundingBox(0.1, 0.2, 0.7, 0.1)
    assert "99" not in "".join(span.exact_text for span in catalog.spans)


def test_equation_decimal_serialization_stays_in_one_exact_line_span() -> None:
    formula = (
        r"d _ { 1 } = { \frac { 2 k - 1 } { 4 n } } \lambda = "
        r"{ \frac { 2 - 1 } { 4 { \times } 1 . 3 3 } } { \times } "
        r"6 2 2 . 5 = 1 1 6 . 7 { \mathrm { ~ n m } }\tag{11}"
    )
    text = f"$$\n{formula}\n$$"
    block = _block("block.equation.decimals", text, kind="equation")
    document = _document([[block]])

    first = build_source_catalog(document)
    second = build_source_catalog(document)
    spans = [span for span in first.spans if span.block_id == block.id]

    assert [span.exact_text for span in spans] == ["$$", formula, "$$"]
    formula_span = spans[1]
    assert "1 . 3 3" in formula_span.exact_text
    assert "6 2 2 . 5" in formula_span.exact_text
    assert "1 1 6 . 7" in formula_span.exact_text
    assert resolve_source_span_text(block, formula_span) == formula
    assert [span.id for span in first.spans] == [span.id for span in second.spans]
    assert set(first.owner_by_span_id) == set(first.by_id)
    assert all(
        1 <= len(shard.owned_span_ids) <= MAX_OWNED_SPANS_PER_SHARD
        for shard in first.shards
    )


@pytest.mark.parametrize(
    ("text", "formulas"),
    [
        (
            r"It is now evident from our updated shale dataset that seawater "
            r"$\delta ^ { 9 8 } \mathrm { { M o } }$ between $\sim 2 . 6 9$ "
            r"Ga and 2.50 Ga was commonly $> 1 . 0 \text{‰}$ "
            r"(and also apparent in Fig. 9), much more so than was previously "
            r"recognized.",
            [r"$\sim 2 . 6 9$", r"$> 1 . 0 \text{‰}$"],
        ),
        (
            r"Lastly, Fe oxide minerals preferentially remove lighter-mass Mo "
            r"isotopes from seawater $( \Delta ^ { 9 8 } \mathrm { M o } _ "
            r"{ \mathrm { s o l u t i o n } } .$ - "
            r"$\mathrm { \Gamma _ { o x i d e } = 0 . 8 3 \% - 2 . 1 9 \% 0 }$ "
            r"(Goldberg et al., 2009)).",
            [
                r"$( \Delta ^ { 9 8 } \mathrm { M o } _ "
                r"{ \mathrm { s o l u t i o n } } .$",
                r"$\mathrm { \Gamma _ { o x i d e } = 0 . 8 3 \% - 2 . 1 9 \% 0 }$",
            ],
        ),
    ],
)
def test_shale_inline_decimal_formulas_keep_exact_source_offsets(
    text: str, formulas: list[str]
) -> None:
    # Literal paragraph excerpts; no OCR correction or historical file dependency.
    text = f"  {text} Next sentence.  Last sentence.  "
    block = _block("block.shale.inline", text)
    catalog = build_source_catalog(_document([[block]]))

    assert [span.exact_text for span in catalog.spans[-2:]] == [
        "Next sentence.",
        "Last sentence.",
    ]
    for formula in formulas:
        assert sum(formula in span.exact_text for span in catalog.spans) == 1
    for span in catalog.spans:
        assert text[span.start_offset : span.end_offset] == span.exact_text
        assert resolve_source_span_text(block, span) == span.exact_text


def test_paragraph_paired_math_delimiters_protect_punctuation_and_newlines() -> None:
    sentences = [
        "Display $$1 . 2;\n3 . 4$$ remains whole.",
        r"Inline \(1 . 2; 3 . 4\) remains whole.",
        r"Display \[1 . 2; 3 . 4\] remains whole.",
    ]
    text = " ".join(sentences)
    catalog = build_source_catalog(_document([[_block("block.math", text)]]))

    assert [span.exact_text for span in catalog.spans] == sentences
    assert all(
        text[span.start_offset : span.end_offset] == span.exact_text
        for span in catalog.spans
    )


def test_unpaired_and_escaped_math_delimiters_keep_prose_boundaries() -> None:
    text = r"Cost \$2 . Next \$3 . Unclosed $1 . Final sentence."
    catalog = build_source_catalog(_document([[_block("block.literal", text)]]))

    assert [span.exact_text for span in catalog.spans] == [
        r"Cost \$2 .",
        r"Next \$3 .",
        "Unclosed $1 .",
        "Final sentence.",
    ]


def test_overlong_equation_lines_split_safely_without_losing_source_tokens() -> None:
    command_text = r"x \abcdefghij y"
    combining_text = "a" * 11 + "e\u0301"
    blocks = [
        _block("block.equation.tex", command_text, kind="equation"),
        _block(
            "block.equation.combining",
            combining_text,
            source_index=1,
            kind="equation",
        ),
    ]
    document = _document([blocks])

    catalog = build_source_catalog(document, max_span_chars=12)

    tex_spans = [
        span for span in catalog.spans if span.block_id == "block.equation.tex"
    ]
    combining_spans = [
        span
        for span in catalog.spans
        if span.block_id == "block.equation.combining"
    ]
    assert [span.exact_text for span in tex_spans] == [
        "x",
        r"\abcdefghij",
        "y",
    ]
    assert [span.exact_text for span in combining_spans] == ["a" * 11, "e\u0301"]
    assert all(len(span.exact_text) <= 12 for span in catalog.spans)

    by_id = {block.id: block for block in blocks}
    for block in blocks:
        assert block.text is not None
        covered = [False] * len(block.text)
        for span in catalog.spans:
            if span.block_id != block.id:
                continue
            assert (
                resolve_source_span_text(by_id[span.block_id], span)
                == span.exact_text
            )
            covered[span.start_offset : span.end_offset] = [True] * len(
                span.exact_text
            )
        assert all(
            character.isspace() or covered[index]
            for index, character in enumerate(block.text)
        )


def test_retained_code_body_and_caption_are_exact_typed_source_spans() -> None:
    code_body = "  x = 1。\ny = x + 2  "
    code_caption = "  仿真程序。  "
    code = _block(
        "block.code",
        None,
        kind="other",
        structured={
            "type": "code",
            "code_body": code_body,
            "code_caption": [{"text": code_caption}],
        },
    )

    first = build_source_catalog(_document([[code]]))
    second = build_source_catalog(_document([[code]]))

    assert [span.exact_text for span in first.spans] == [
        "x = 1。",
        "y = x + 2",
        "仿真程序。",
    ]
    assert [span.source_kind for span in first.spans] == [
        "code_body",
        "code_body",
        "code_caption",
    ]
    assert first.spans[0].source_path == ("structured", "code_body")
    assert first.spans[2].source_path == (
        "structured",
        "code_caption",
        0,
        "text",
    )
    assert [span.id for span in first.spans] == [span.id for span in second.spans]
    assert all(
        resolve_source_span_text(code, span) == span.exact_text for span in first.spans
    )
    assert len({span.id for span in first.spans}) == 3


def test_safe_structured_cells_join_segment_and_shard_review_ledger() -> None:
    document = _document(
        [[
            _block(
                "block.table.safe",
                "Table 1. Mass result.",
                kind="table",
                structured={
                    "recognized_cells": [
                        {
                            "row": 1,
                            "column": "Mass",
                            "raw_value": "5 mg",
                            "numeric_value": 5,
                            "unit": "mg",
                        }
                    ]
                },
            )
        ]]
    )

    catalog = build_source_catalog(document)
    structured = [
        span for span in catalog.spans if span.source_kind == "structured_visual"
    ]

    assert {span.structured_role for span in structured} == {
        "table_cell",
        "table_metadata",
    }
    direct_text = {
        span.exact_text for span in structured if span.direct_evidence_eligible
    }
    assert direct_text == {
        "5 mg",
        "5",
    }
    assert all(span.document_segment_id for span in structured)
    assert set(span.id for span in structured) <= {
        item for shard in catalog.shards for item in shard.owned_span_ids
    }
    block = document.pages[0].blocks[0]
    assert all(
        resolve_source_span_text(block, span) == span.exact_text
        for span in structured
    )


def test_unsafe_structured_content_is_withheld_with_bounded_warning() -> None:
    document = _document(
        [[
            _block(
                "block.table.unsafe",
                "Table 1.",
                kind="table",
                structured={"recognized_cells": {"unexpected": "9.8 m/s2"}},
            )
        ]]
    )

    catalog = build_source_catalog(document)

    assert not any(
        span.source_kind == "structured_visual" for span in catalog.spans
    )
    assert catalog.warnings
    assert all(len(item) <= 1_000 for item in catalog.warnings)


def test_document_segments_use_titles_and_continuations_only_as_stable_hints() -> None:
    page_one = [
        _block("block.target-title", "目标论文", source_index=0, level=1),
        _block("block.target-body", "目标结果。", source_index=1),
        _block("block.turn", "(下转 56 页)", source_index=2),
    ]
    page_two = [
        _block("block.resume", "(上接 47 页)", page=2, source_index=3),
        _block("block.resumed-body", "续页结果。", page=2, source_index=4),
        _block("block.other-title", "相邻论文", page=2, source_index=5, level=1),
        _block("block.other-body", "相邻内容。", page=2, source_index=6),
    ]

    first = build_source_catalog(_document([page_one, page_two]))
    second = build_source_catalog(_document([page_one, page_two]))

    assert len(first.segments) == 3
    assert [segment.id for segment in first.segments] == [
        segment.id for segment in second.segments
    ]
    assert [segment.order for segment in first.segments] == [0, 1, 2]
    assert first.segments[0].boundary_hints == ("document_title",)
    assert first.segments[1].boundary_hints == (
        "continues_from_previous_page",
        "after_continues_on_later_page",
    )
    assert first.segments[2].boundary_hints == ("document_title",)
    assert [span.document_order for span in first.spans] == list(
        range(len(first.spans))
    )
    assert (
        next(
            span for span in first.spans if span.block_id == "block.turn"
        ).continuation_hint
        == "continues_on_later_page"
    )
    assert (
        next(
            span for span in first.spans if span.block_id == "block.resume"
        ).continuation_hint
        == "continues_from_previous_page"
    )
    assert {
        span.id
        for segment in first.segments
        for span in (first.by_id[item] for item in segment.span_ids)
    } == set(first.by_id)


def test_global_heading_catalog_tracks_adjacent_paper_titles_and_sections() -> None:
    page_one = [
        _block("block.paper-a", "论文甲", source_index=0, level=1),
        _block("block.a-intro", "甲的摘要。", source_index=1),
        _block("block.a-results", "结果", source_index=2, level=2),
        _block("block.a-body", "甲的结果。", source_index=3),
    ]
    page_two = [
        _block("block.paper-b", "论文乙", page=2, source_index=4, level=1),
        _block("block.b-body", "乙的内容。", page=2, source_index=5),
    ]

    catalog = build_source_catalog(_document([page_one, page_two]))

    assert [heading.exact_text for heading in catalog.headings] == [
        "论文甲",
        "结果",
        "论文乙",
    ]
    assert [heading.is_document_title for heading in catalog.headings] == [
        True,
        False,
        True,
    ]
    section_by_block = {span.block_id: span.section_title for span in catalog.spans}
    assert section_by_block["block.a-intro"] == "论文甲"
    assert section_by_block["block.a-body"] == "结果"
    assert section_by_block["block.b-body"] == "论文乙"


def test_shards_have_unique_owners_bounded_size_and_read_only_overlap() -> None:
    blocks: list[DocumentBlock] = []
    source_index = 0
    for section in range(4):
        blocks.append(
            _block(
                f"block.heading.{section}",
                f"第{section}节",
                source_index=source_index,
                level=2,
            )
        )
        source_index += 1
        for paragraph in range(3):
            blocks.append(
                _block(
                    f"block.section.{section}.{paragraph}",
                    (f"第{section}节证据{paragraph}。" * 2),
                    source_index=source_index,
                )
            )
            source_index += 1

    catalog = build_source_catalog(
        _document([blocks]),
        target_shard_chars=30,
        max_owned_spans=4,
        context_span_count=1,
    )
    owned = [span_id for shard in catalog.shards for span_id in shard.owned_span_ids]

    assert len(catalog.shards) > 1
    assert len(owned) == len(set(owned)) == len(catalog.spans)
    assert set(owned) == set(catalog.by_id)
    assert catalog.owner_by_span_id.keys() == catalog.by_id.keys()
    for shard in catalog.shards:
        assert 1 <= len(shard.owned_span_ids) <= 4
        assert set(shard.owned_span_ids).isdisjoint(shard.context_span_ids)
        assert len(shard.context_span_ids) <= 2
        assert set(shard.context_span_ids) <= set(catalog.by_id)
        assert all(
            catalog.owner_by_span_id[span_id] != shard.id
            for span_id in shard.context_span_ids
        )
    assert any(shard.context_span_ids for shard in catalog.shards)


def test_default_shard_cap_is_32_with_exact_order_and_no_source_loss() -> None:
    blocks = [
        _block(
            f"block.default-cap.{index}",
            f"Exact source item {index:02d}",
            source_index=index,
        )
        for index in range(70)
    ]
    document = _document([blocks])

    first = build_source_catalog(document)
    second = build_source_catalog(document)
    explicit_hard_maximum = build_source_catalog(
        document,
        max_owned_spans=MAX_OWNED_SPANS_PER_SHARD,
    )

    assert DEFAULT_MAX_OWNED_SPANS_PER_SHARD == 32
    assert MAX_OWNED_SPANS_PER_SHARD == 64
    assert [len(shard.owned_span_ids) for shard in first.shards] == [32, 32, 6]
    assert [len(shard.owned_span_ids) for shard in explicit_hard_maximum.shards] == [
        64,
        6,
    ]
    assert [shard.id for shard in first.shards] == [
        shard.id for shard in second.shards
    ]
    assert [span.id for span in first.spans] == [span.id for span in second.spans]
    assert [span.id for span in first.spans] == [
        span.id for span in explicit_hard_maximum.spans
    ]

    owned = [
        span_id for shard in first.shards for span_id in shard.owned_span_ids
    ]
    assert owned == [span.id for span in first.spans]
    assert len(owned) == len(set(owned)) == 70
    assert first.owner_by_span_id.keys() == first.by_id.keys()
    for shard in first.shards:
        assert set(shard.owned_span_ids).isdisjoint(shard.context_span_ids)
        assert all(
            first.owner_by_span_id[span_id] != shard.id
            for span_id in shard.context_span_ids
        )

    block_by_id = {block.id: block for block in blocks}
    assert all(
        resolve_source_span_text(block_by_id[span.block_id], span)
        == span.exact_text
        for span in first.spans
    )
    assert [span.exact_text for span in first.spans] == [
        block.text for block in blocks
    ]


def test_default_cap_packs_complete_sections_before_splitting() -> None:
    blocks: list[DocumentBlock] = []
    source_index = 0
    for section in range(2):
        blocks.append(
            _block(
                f"block.default-section.{section}.heading",
                f"Section {section}",
                source_index=source_index,
                level=2,
            )
        )
        source_index += 1
        for paragraph in range(19):
            blocks.append(
                _block(
                    f"block.default-section.{section}.{paragraph}",
                    f"Section {section} exact item {paragraph}",
                    source_index=source_index,
                )
            )
            source_index += 1

    catalog = build_source_catalog(_document([blocks]), context_span_count=0)

    assert [len(shard.owned_span_ids) for shard in catalog.shards] == [20, 20]
    assert [shard.section_titles for shard in catalog.shards] == [
        ("Section 0",),
        ("Section 1",),
    ]
    assert [
        span_id for shard in catalog.shards for span_id in shard.owned_span_ids
    ] == [span.id for span in catalog.spans]


def test_heading_boundaries_are_preferred_when_sections_do_not_pack() -> None:
    blocks = [
        _block("block.h1", "第一节", source_index=0, level=2),
        _block("block.p1", "A" * 14, source_index=1),
        _block("block.h2", "第二节", source_index=2, level=2),
        _block("block.p2", "B" * 14, source_index=3),
    ]

    catalog = build_source_catalog(
        _document([blocks]),
        target_shard_chars=20,
        max_owned_spans=64,
        context_span_count=0,
    )

    assert len(catalog.shards) == 2
    assert catalog.shards[0].section_titles == ("第一节",)
    assert catalog.shards[1].section_titles == ("第二节",)
    first_blocks = {
        catalog.by_id[span_id].block_id for span_id in catalog.shards[0].owned_span_ids
    }
    second_blocks = {
        catalog.by_id[span_id].block_id for span_id in catalog.shards[1].owned_span_ids
    }
    assert first_blocks == {"block.h1", "block.p1"}
    assert second_blocks == {"block.h2", "block.p2"}


@pytest.mark.parametrize(
    ("keyword", "value", "error"),
    [
        ("max_span_chars", MAX_SOURCE_SPAN_CHARS + 1, ValueError),
        ("target_shard_chars", 0, ValueError),
        ("max_owned_spans", MAX_OWNED_SPANS_PER_SHARD + 1, ValueError),
        ("context_span_count", 5, ValueError),
        ("max_span_chars", True, TypeError),
    ],
)
def test_catalog_limits_fail_closed(keyword: str, value: object, error: type) -> None:
    document = _document([[_block("block.one", "一句话。")]])

    with pytest.raises(error):
        build_source_catalog(document, **{keyword: value})
