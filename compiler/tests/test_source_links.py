from __future__ import annotations

import re

import pytest
from conftest import raw_graph_payload

from compiler_service.canonical import to_canonical_graph
from compiler_service.graph_contract import PaperEvidenceGraph
from compiler_service.models import (
    BoundingBox,
    DocumentBlock,
    DocumentIR,
    DocumentPage,
    ParserMetadata,
)
from compiler_service.source_links import (
    _derived_reference_keys,
    _period_ends_sentence,
    _reference_keys,
    link_referenced_visuals,
)


@pytest.mark.parametrize(
    "value",
    [
        "e.g. next",
        "Figs. 2 and 3",
        "Fig. 2",
        "Eq. 1",
        "i. item",
        "1.25 value",
        "Dr. Smith",
        "中文. 后文",
        "end.",
    ],
)
def test_period_scanner_preserves_legacy_sentence_boundaries(value: str) -> None:
    """The linear scanner must remain equivalent to the former regex scan."""

    def legacy(index: int) -> bool:
        if index + 1 < len(value) and not value[index + 1].isspace():
            return False
        token = re.search(r"([A-Za-z]+)$", value[:index])
        return token is None or token.group(1).casefold() not in {
            "fig",
            "eq",
            "e",
            "i",
        }

    period_indexes = [
        index for index, character in enumerate(value) if character == "."
    ]
    assert period_indexes
    assert [_period_ends_sentence(value, index) for index in period_indexes] == [
        legacy(index) for index in period_indexes
    ]


def _raw_graph_with_only_text_anchor(artifact, quote: str, page: int, region):
    payload = raw_graph_payload(artifact)
    payload["observations"][0].update(
        {"statement": quote, "raw_value": None, "unit": None}
    )
    payload["anchors"] = [payload["anchors"][0]]
    payload["anchors"][0]["locator"].update(
        {"page": page, "quote": quote, "region": region}
    )
    payload["visual_evidence"] = []
    payload["edges"] = [
        edge for edge in payload["edges"] if edge["id"] != "edge.grounds.visual.1"
    ]
    return payload


def _document(artifact, pages: list[DocumentPage]) -> DocumentIR:
    return DocumentIR(
        artifact=artifact,
        parser=ParserMetadata(
            name="mineru",
            version="3.1.15",
            backend="pipeline",
            method="auto",
            transport="remote",
        ),
        page_count=len(pages),
        pages=pages,
        markdown="",
        warnings=[],
    )


def _empty_page(number: int) -> DocumentPage:
    return DocumentPage(number=number, width=600, height=800, blocks=[])


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Table 5.1", [("table", "5.1")]),
        ("Fig.5A", [("figure", "5a")]),
        ("图5a", [("figure", "5a")]),
        ("Figure S5", [("figure", "S5")]),
        ("表S2", [("table", "S2")]),
        ("图０５Ａ", [("figure", "5a")]),
    ],
)
def test_visual_label_parser_preserves_the_complete_common_token(
    value, expected
) -> None:
    assert _reference_keys(value) == expected


@pytest.mark.parametrize(
    "value",
    ["Fig. 5(a)", "Table 5.1.2", "Table 5.A", "图5-1", "Figure S5AB"],
)
def test_visual_label_parser_rejects_unsupported_composite_tokens(value) -> None:
    assert _reference_keys(value) == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("由表5数据得到的比较曲线见图15。", [("table", "5")]),
        ("Comparison curve based on Table 5; see Figure 15.", [("table", "5")]),
        (
            "The curve was derived from the data in Table S2.",
            [("table", "S2")],
        ),
        ("表5数据用于绘制图15。", [("table", "5")]),
        ("基于表5数据绘制图15。", [("table", "5")]),
    ],
)
def test_visual_derivation_parser_accepts_only_explicit_data_sources(
    value, expected
) -> None:
    assert _derived_reference_keys(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "另见表5。",
        "与表5比较。",
        "根据表5进行比较。",
        "See Table 5.",
        "Compare with Table 5.",
        "Figure 15 is a schematic reference to Table 5.",
        "As shown in Table 5, the categories are defined.",
    ],
)
def test_visual_derivation_parser_rejects_navigation_only_mentions(value) -> None:
    assert _derived_reference_keys(value) == []


@pytest.mark.parametrize(
    ("quote", "caption", "kind", "caption_field"),
    [
        ("See Fig. 5.", "Figure 5A. Panel result.", "figure", "figure_caption"),
        ("参见图5。", "图5a 分图结果", "figure", "figure_caption"),
        ("See Table 5.", "Table 5.1. Detailed values.", "table", "table_caption"),
    ],
)
def test_partial_visual_tokens_never_match_a_more_specific_label(
    artifact, quote, caption, kind, caption_field
) -> None:
    text_bbox = BoundingBox(x=0.1, y=0.08, width=0.8, height=0.08)
    document = _document(
        artifact,
        [
            DocumentPage(
                number=1,
                width=600,
                height=800,
                blocks=[
                    DocumentBlock(
                        id="block:p1:000001",
                        kind="paragraph",
                        page=1,
                        source_index=0,
                        bbox=text_bbox,
                        text=quote,
                    ),
                    DocumentBlock(
                        id="block:p1:000002",
                        kind=kind,
                        page=1,
                        source_index=1,
                        bbox=BoundingBox(x=0.1, y=0.24, width=0.8, height=0.5),
                        text=caption,
                        structured={caption_field: [caption]},
                    ),
                ],
            )
        ],
    )
    raw = PaperEvidenceGraph.model_validate(
        _raw_graph_with_only_text_anchor(
            artifact,
            quote,
            1,
            text_bbox.model_dump() | {"unit": "normalized"},
        ),
        strict=True,
    )

    enriched = link_referenced_visuals(raw, document)

    assert enriched.model_dump(mode="json") == raw.model_dump(mode="json")


def test_links_cross_page_chinese_figure_reference_and_canonical_grounds(
    artifact, parser_metadata
) -> None:
    text_region = {
        "x": 0.1,
        "y": 0.2,
        "width": 0.7,
        "height": 0.08,
        "unit": "normalized",
    }
    quote = "图5中霓现象的出射光在最小视角处分布最为集中。"
    caption = "图 5 霓现象的出射光方向模拟"
    document = _document(
        artifact,
        [
            _empty_page(1),
            DocumentPage(
                number=2,
                width=600,
                height=800,
                blocks=[
                    DocumentBlock(
                        id="block:p2:000001",
                        kind="image",
                        page=2,
                        source_index=0,
                        bbox=BoundingBox(x=0.12, y=0.28, width=0.74, height=0.42),
                        text=caption,
                        structured={
                            "image_caption": [caption],
                            "img_path": "images/figure-5.jpg",
                        },
                    )
                ],
            ),
            DocumentPage(
                number=3,
                width=600,
                height=800,
                blocks=[
                    DocumentBlock(
                        id="block:p3:000002",
                        kind="paragraph",
                        page=3,
                        source_index=1,
                        bbox=BoundingBox(x=0.1, y=0.2, width=0.7, height=0.08),
                        text=quote,
                    )
                ],
            ),
        ],
    )
    raw_payload = _raw_graph_with_only_text_anchor(artifact, quote, 3, text_region)
    raw_payload["assumptions"] = [
        {
            "id": "assumption.local.1",
            "statement": "模拟采用球形液珠。",
            "assumption_type": "methodological",
            "anchor_ids": ["anchor.text.1"],
        }
    ]
    raw_payload["edges"].append(
        {
            "id": "edge.assumption.1",
            "source_id": "assumption.local.1",
            "target_id": "reason.local.1",
            "relation": "assumption_for",
            "rationale": None,
            "confidence": None,
        }
    )
    raw = PaperEvidenceGraph.model_validate(raw_payload, strict=True)

    enriched = link_referenced_visuals(raw, document)

    figure = next(
        anchor for anchor in enriched.anchors if anchor.anchor_type == "figure"
    )
    assert figure.locator.page == 2
    assert figure.locator.figure == "图5"
    assert figure.locator.quote == caption
    assert figure.locator.region is not None
    assert figure.locator.region.model_dump() == {
        "x": 0.12,
        "y": 0.28,
        "width": 0.74,
        "height": 0.42,
        "unit": "normalized",
    }
    for node in (
        *enriched.observations,
        *enriched.reasoning,
        *enriched.assumptions,
        *enriched.claims,
    ):
        assert figure.id in node.anchor_ids

    canonical = to_canonical_graph(
        enriched,
        artifact,
        parser=parser_metadata,
        created_at="2026-08-15T00:00:00Z",
    )
    assert any(
        edge["source_id"] == figure.id
        and edge["target_id"] == "obs.local.1"
        and edge["relation"] == "grounds"
        for edge in canonical["edges"]
    )


def test_reuses_existing_table_anchor_across_chinese_english_label_forms(
    artifact,
) -> None:
    quote = "表5给出虹霓中红光视角夹角的理论值与实测值。"
    caption = "Table 5. 虹霓中红光视角的夹角"
    text_bbox = BoundingBox(x=0.08, y=0.12, width=0.82, height=0.08)
    table_bbox = BoundingBox(x=0.08, y=0.34, width=0.82, height=0.36)
    document = _document(
        artifact,
        [
            DocumentPage(
                number=1,
                width=600,
                height=800,
                blocks=[
                    DocumentBlock(
                        id="block:p1:000001",
                        kind="paragraph",
                        page=1,
                        source_index=0,
                        bbox=text_bbox,
                        text=quote,
                    ),
                    DocumentBlock(
                        id="block:p1:000002",
                        kind="table",
                        page=1,
                        source_index=1,
                        bbox=table_bbox,
                        text=caption + "\n<table><tr><td>8.59</td></tr></table>",
                        structured={
                            "table_caption": [caption],
                            "table_body": "<table><tr><td>8.59</td></tr></table>",
                        },
                    ),
                ],
            )
        ],
    )
    payload = raw_graph_payload(artifact)
    payload["anchors"][0]["locator"].update(
        {
            "page": 1,
            "quote": quote,
            "region": text_bbox.model_dump() | {"unit": "normalized"},
        }
    )
    payload["anchors"][1]["locator"].update(
        {
            "page": 1,
            "table": "表 5",
            "region": table_bbox.model_dump() | {"unit": "normalized"},
        }
    )
    raw = PaperEvidenceGraph.model_validate(payload, strict=True)

    enriched = link_referenced_visuals(raw, document)

    assert len(enriched.anchors) == len(raw.anchors)
    reused = next(
        anchor for anchor in enriched.anchors if anchor.id == "anchor.table.1"
    )
    assert reused.locator.quote == caption
    assert reused.locator.region is not None
    for node in (
        *enriched.observations,
        *enriched.reasoning,
        *enriched.claims,
    ):
        assert "anchor.table.1" in node.anchor_ids


def test_duplicate_labels_choose_the_nearest_caption_across_pages(artifact) -> None:
    quote = "As shown in Fig. 5, the response has an extremum."
    text_bbox = BoundingBox(x=0.1, y=0.1, width=0.8, height=0.1)

    def figure(page: int, source_index: int, caption: str) -> DocumentBlock:
        return DocumentBlock(
            id=f"block:p{page}:{source_index + 1:06d}",
            kind="figure",
            page=page,
            source_index=source_index,
            bbox=BoundingBox(x=0.1, y=0.3, width=0.8, height=0.5),
            text=caption,
            structured={"figure_caption": [caption]},
        )

    document = _document(
        artifact,
        [
            DocumentPage(
                number=1,
                width=600,
                height=800,
                blocks=[figure(1, 0, "Figure 5. Earlier appendix copy.")],
            ),
            _empty_page(2),
            DocumentPage(
                number=3,
                width=600,
                height=800,
                blocks=[
                    DocumentBlock(
                        id="block:p3:000002",
                        kind="paragraph",
                        page=3,
                        source_index=1,
                        bbox=text_bbox,
                        text=quote,
                    )
                ],
            ),
            DocumentPage(
                number=4,
                width=600,
                height=800,
                blocks=[figure(4, 2, "Fig. 5. Nearby results copy.")],
            ),
        ],
    )
    raw = PaperEvidenceGraph.model_validate(
        _raw_graph_with_only_text_anchor(
            artifact,
            quote,
            3,
            text_bbox.model_dump() | {"unit": "normalized"},
        ),
        strict=True,
    )

    enriched = link_referenced_visuals(raw, document)

    figure_anchor = next(
        anchor for anchor in enriched.anchors if anchor.anchor_type == "figure"
    )
    assert figure_anchor.locator.page == 4
    assert figure_anchor.locator.quote == "Fig. 5. Nearby results copy."


def test_chart_source_reference_links_its_input_table_to_the_same_nodes(
    artifact, parser_metadata
) -> None:
    quote = "图15表明实测虹霓视角结果与理论计算结果吻合程度很高。"
    chart_caption = "图15 实测值与理论值的比较曲线"
    chart_text = chart_caption + "\n由表5数据得到的比较曲线见图15。"
    table_caption = "表5 虹霓中红光视角的夹角"
    text_bbox = BoundingBox(x=0.08, y=0.08, width=0.84, height=0.08)
    chart_bbox = BoundingBox(x=0.1, y=0.18, width=0.8, height=0.32)
    table_bbox = BoundingBox(x=0.08, y=0.56, width=0.84, height=0.3)
    document = _document(
        artifact,
        [
            DocumentPage(
                number=1,
                width=600,
                height=800,
                blocks=[
                    DocumentBlock(
                        id="block:p1:000001",
                        kind="paragraph",
                        page=1,
                        source_index=0,
                        bbox=text_bbox,
                        text=quote,
                    ),
                    DocumentBlock(
                        id="block:p1:000002",
                        kind="chart",
                        page=1,
                        source_index=1,
                        bbox=chart_bbox,
                        text=chart_text,
                        structured={"chart_caption": [chart_caption]},
                    ),
                    DocumentBlock(
                        id="block:p1:000003",
                        kind="table",
                        page=1,
                        source_index=2,
                        bbox=table_bbox,
                        text=(
                            table_caption
                            + "\n<table><tr><td>8.59</td><td>8.52</td></tr></table>"
                        ),
                        structured={
                            "table_caption": [table_caption],
                            "table_body": (
                                "<table><tr><td>8.59</td><td>8.52</td></tr></table>"
                            ),
                        },
                    ),
                ],
            )
        ],
    )
    raw = PaperEvidenceGraph.model_validate(
        _raw_graph_with_only_text_anchor(
            artifact,
            quote,
            1,
            text_bbox.model_dump() | {"unit": "normalized"},
        ),
        strict=True,
    )

    enriched = link_referenced_visuals(raw, document)

    figure = next(
        anchor for anchor in enriched.anchors if anchor.anchor_type == "figure"
    )
    table = next(anchor for anchor in enriched.anchors if anchor.anchor_type == "table")
    assert figure.locator.figure == "图15"
    assert figure.locator.quote == chart_caption
    assert table.locator.table == "表5"
    assert table.locator.quote == table_caption
    for node in (
        *enriched.observations,
        *enriched.reasoning,
        *enriched.claims,
    ):
        assert figure.id in node.anchor_ids
        assert table.id in node.anchor_ids

    canonical = to_canonical_graph(
        enriched,
        artifact,
        parser=parser_metadata,
        created_at="2026-08-15T00:00:00Z",
    )
    grounded = {
        edge["source_id"]
        for edge in canonical["edges"]
        if edge["target_id"] == "obs.local.1" and edge["relation"] == "grounds"
    }
    assert {figure.id, table.id} <= grounded


def test_navigation_only_visual_mentions_do_not_become_node_grounds(
    artifact, parser_metadata
) -> None:
    quote = "Figure 15 shows the fitted curve."
    chart_caption = "Figure 15. Fitted curve."
    table_caption = "Table 5. Input values."
    text_bbox = BoundingBox(x=0.08, y=0.08, width=0.84, height=0.08)
    document = _document(
        artifact,
        [
            DocumentPage(
                number=1,
                width=600,
                height=800,
                blocks=[
                    DocumentBlock(
                        id="block:p1:000001",
                        kind="paragraph",
                        page=1,
                        source_index=0,
                        bbox=text_bbox,
                        text=quote,
                    ),
                    DocumentBlock(
                        id="block:p1:000002",
                        kind="chart",
                        page=1,
                        source_index=1,
                        bbox=BoundingBox(x=0.1, y=0.2, width=0.8, height=0.3),
                        text=(
                            chart_caption
                            + "\nSee Table 5 for comparison; Figure 2 is a schematic."
                        ),
                        structured={"chart_caption": [chart_caption]},
                    ),
                    DocumentBlock(
                        id="block:p1:000003",
                        kind="table",
                        page=1,
                        source_index=2,
                        bbox=BoundingBox(x=0.1, y=0.56, width=0.8, height=0.28),
                        text=table_caption,
                        structured={"table_caption": [table_caption]},
                    ),
                    DocumentBlock(
                        id="block:p1:000004",
                        kind="figure",
                        page=1,
                        source_index=3,
                        bbox=BoundingBox(x=0.1, y=0.86, width=0.3, height=0.1),
                        text="Figure 2. Schematic.",
                        structured={"figure_caption": ["Figure 2. Schematic."]},
                    ),
                ],
            )
        ],
    )
    raw = PaperEvidenceGraph.model_validate(
        _raw_graph_with_only_text_anchor(
            artifact,
            quote,
            1,
            text_bbox.model_dump() | {"unit": "normalized"},
        ),
        strict=True,
    )

    enriched = link_referenced_visuals(raw, document)

    visual_anchors = [
        anchor
        for anchor in enriched.anchors
        if anchor.anchor_type in {"figure", "table"}
    ]
    assert len(visual_anchors) == 1
    assert visual_anchors[0].locator.figure == "Figure 15"
    canonical = to_canonical_graph(
        enriched,
        artifact,
        parser=parser_metadata,
        created_at="2026-08-15T00:00:00Z",
    )
    grounded_visuals = {
        edge["source_id"]
        for edge in canonical["edges"]
        if edge["target_id"] == "obs.local.1"
        and edge["relation"] == "grounds"
        and edge["source_id"] != "anchor.text.1"
    }
    assert grounded_visuals == {visual_anchors[0].id}


def test_visual_reference_expansion_ignores_missing_geometry_and_stops_cycles(
    artifact,
) -> None:
    quote = "图15给出比较结果。"
    chart_caption = "图15 比较结果"
    table_caption = "表5 原始数据"
    text_bbox = BoundingBox(x=0.1, y=0.08, width=0.8, height=0.08)
    document = _document(
        artifact,
        [
            DocumentPage(
                number=1,
                width=600,
                height=800,
                blocks=[
                    DocumentBlock(
                        id="block:p1:000001",
                        kind="paragraph",
                        page=1,
                        source_index=0,
                        bbox=text_bbox,
                        text=quote,
                    ),
                    DocumentBlock(
                        id="block:p1:000002",
                        kind="chart",
                        page=1,
                        source_index=1,
                        bbox=BoundingBox(x=0.1, y=0.2, width=0.8, height=0.3),
                        text=(
                            chart_caption + "\n数据来自表5；图99和表77未提供可信定位。"
                        ),
                        structured={"chart_caption": [chart_caption]},
                    ),
                    DocumentBlock(
                        id="block:p1:000003",
                        kind="table",
                        page=1,
                        source_index=2,
                        bbox=BoundingBox(x=0.1, y=0.56, width=0.8, height=0.28),
                        text=table_caption + "\n表5数据由图15计算得到。",
                        structured={"table_caption": [table_caption]},
                    ),
                    DocumentBlock(
                        id="block:p1:000004",
                        kind="figure",
                        page=1,
                        source_index=3,
                        bbox=None,
                        text="图99 无坐标图像",
                        structured={"figure_caption": ["图99 无坐标图像"]},
                    ),
                ],
            )
        ],
    )
    raw = PaperEvidenceGraph.model_validate(
        _raw_graph_with_only_text_anchor(
            artifact,
            quote,
            1,
            text_bbox.model_dump() | {"unit": "normalized"},
        ),
        strict=True,
    )

    enriched = link_referenced_visuals(raw, document)

    visual_anchors = [
        anchor
        for anchor in enriched.anchors
        if anchor.anchor_type in {"figure", "table"}
    ]
    assert len(visual_anchors) == 2
    assert {anchor.locator.figure for anchor in visual_anchors} == {"图15", None}
    assert {anchor.locator.table for anchor in visual_anchors} == {"表5", None}
    assert all("99" not in (anchor.locator.quote or "") for anchor in visual_anchors)


def test_recovers_figure_reference_from_unique_quote_previous_sentence(
    artifact,
) -> None:
    quote = "按照虹现象的模拟方法，同理可得在霓现象中光强在其最小视角处分布最为集中．"
    paragraph = "图5中霓现象的出射方向存在一个最小夹角，称作最小视角．" + quote
    figure_caption = "图5 霓现象出射光方向模拟"
    table_caption = "表5 模拟输入数据"
    text_bbox = BoundingBox(x=0.08, y=0.1, width=0.84, height=0.12)
    document = _document(
        artifact,
        [
            _empty_page(1),
            DocumentPage(
                number=2,
                width=600,
                height=800,
                blocks=[
                    DocumentBlock(
                        id="block:p2:000001",
                        kind="image",
                        page=2,
                        source_index=0,
                        bbox=BoundingBox(x=0.1, y=0.2, width=0.8, height=0.35),
                        text=figure_caption + "\n由表5数据得到。",
                        structured={"image_caption": [figure_caption]},
                    ),
                    DocumentBlock(
                        id="block:p2:000002",
                        kind="table",
                        page=2,
                        source_index=1,
                        bbox=BoundingBox(x=0.1, y=0.6, width=0.8, height=0.25),
                        text=table_caption,
                        structured={"table_caption": [table_caption]},
                    ),
                ],
            ),
            DocumentPage(
                number=3,
                width=600,
                height=800,
                blocks=[
                    DocumentBlock(
                        id="block:p3:000001",
                        kind="paragraph",
                        page=3,
                        source_index=2,
                        bbox=text_bbox,
                        text=paragraph,
                    )
                ],
            ),
        ],
    )
    raw = PaperEvidenceGraph.model_validate(
        _raw_graph_with_only_text_anchor(
            artifact,
            quote,
            3,
            text_bbox.model_dump() | {"unit": "normalized"},
        ),
        strict=True,
    )

    enriched = link_referenced_visuals(raw, document)

    visuals = [
        anchor
        for anchor in enriched.anchors
        if anchor.anchor_type in {"figure", "table"}
    ]
    assert len(visuals) == 1
    assert visuals[0].locator.figure == "图5"
    assert visuals[0].locator.quote == figure_caption
    for node in (*enriched.observations, *enriched.reasoning, *enriched.claims):
        assert visuals[0].id in node.anchor_ids


@pytest.mark.parametrize(
    "paragraph",
    [
        "图5给出模拟结果。中间另有一句。该结果支持模型。",
        "图5给出模拟结果。\n该结果支持模型。",
        "图5与图6给出两类结果。该结果支持模型。",
    ],
)
def test_context_recovery_rejects_far_cross_paragraph_or_ambiguous_refs(
    artifact, paragraph
) -> None:
    quote = "该结果支持模型。"
    text_bbox = BoundingBox(x=0.08, y=0.1, width=0.84, height=0.12)
    blocks = [
        DocumentBlock(
            id="block:p1:text",
            kind="paragraph",
            page=1,
            source_index=0,
            bbox=text_bbox,
            text=paragraph,
        )
    ]
    for index in (5, 6):
        caption = f"图{index} 模拟结果"
        blocks.append(
            DocumentBlock(
                id=f"block:p1:figure:{index}",
                kind="image",
                page=1,
                source_index=index,
                bbox=BoundingBox(x=0.1, y=0.2 + index / 20, width=0.8, height=0.1),
                text=caption,
                structured={"image_caption": [caption]},
            )
        )
    document = _document(
        artifact,
        [DocumentPage(number=1, width=600, height=800, blocks=blocks)],
    )
    raw = PaperEvidenceGraph.model_validate(
        _raw_graph_with_only_text_anchor(
            artifact,
            quote,
            1,
            text_bbox.model_dump() | {"unit": "normalized"},
        ),
        strict=True,
    )

    enriched = link_referenced_visuals(raw, document)

    assert enriched.model_dump(mode="json") == raw.model_dump(mode="json")


def test_context_recovery_requires_a_unique_document_block_match(artifact) -> None:
    quote = "该结果支持模型。"
    text_bbox = BoundingBox(x=0.08, y=0.1, width=0.84, height=0.08)
    blocks = [
        DocumentBlock(
            id=f"block:p1:text:{label}",
            kind="paragraph",
            page=1,
            source_index=index,
            bbox=text_bbox,
            text=f"图{label}给出模拟结果。{quote}",
        )
        for index, label in enumerate((5, 6))
    ]
    for index, label in enumerate((5, 6), start=2):
        caption = f"图{label} 模拟结果"
        blocks.append(
            DocumentBlock(
                id=f"block:p1:figure:{label}",
                kind="image",
                page=1,
                source_index=index,
                bbox=BoundingBox(x=0.1, y=0.4 + index / 20, width=0.8, height=0.1),
                text=caption,
                structured={"image_caption": [caption]},
            )
        )
    document = _document(
        artifact,
        [DocumentPage(number=1, width=600, height=800, blocks=blocks)],
    )
    raw = PaperEvidenceGraph.model_validate(
        _raw_graph_with_only_text_anchor(
            artifact,
            quote,
            1,
            text_bbox.model_dump() | {"unit": "normalized"},
        ),
        strict=True,
    )

    enriched = link_referenced_visuals(raw, document)

    assert enriched.model_dump(mode="json") == raw.model_dump(mode="json")


def test_core_geometric_model_figure_one_reference_is_linked(artifact) -> None:
    quote = "如图1所示，球形液珠内的光线发生折射与反射。"
    caption = "图1 球形液珠几何光学模型"
    text_bbox = BoundingBox(x=0.1, y=0.08, width=0.8, height=0.1)
    document = _document(
        artifact,
        [
            DocumentPage(
                number=1,
                width=600,
                height=800,
                blocks=[
                    DocumentBlock(
                        id="block:p1:000001",
                        kind="paragraph",
                        page=1,
                        source_index=0,
                        bbox=text_bbox,
                        text=quote,
                    ),
                    DocumentBlock(
                        id="block:p1:000002",
                        kind="image",
                        page=1,
                        source_index=1,
                        bbox=BoundingBox(x=0.15, y=0.24, width=0.7, height=0.54),
                        text=caption,
                        structured={"image_caption": [caption]},
                    ),
                ],
            )
        ],
    )
    raw = PaperEvidenceGraph.model_validate(
        _raw_graph_with_only_text_anchor(
            artifact,
            quote,
            1,
            text_bbox.model_dump() | {"unit": "normalized"},
        ),
        strict=True,
    )

    enriched = link_referenced_visuals(raw, document)

    figure = next(
        anchor for anchor in enriched.anchors if anchor.anchor_type == "figure"
    )
    assert figure.locator.figure == "图1"
    assert figure.locator.quote == caption


def test_does_not_invent_links_for_false_references_or_missing_geometry(
    artifact,
) -> None:
    quote = "图形分析与 Figure 9 的文字讨论未提供可定位图像。"
    region = BoundingBox(x=0.1, y=0.1, width=0.8, height=0.1)
    caption = "Figure 9. Simulated directions."
    document = _document(
        artifact,
        [
            DocumentPage(
                number=1,
                width=600,
                height=800,
                blocks=[
                    DocumentBlock(
                        id="block:p1:000001",
                        kind="paragraph",
                        page=1,
                        source_index=0,
                        bbox=region,
                        text=quote,
                    ),
                    DocumentBlock(
                        id="block:p1:000002",
                        kind="image",
                        page=1,
                        source_index=1,
                        bbox=None,
                        text=caption,
                        structured={"image_caption": [caption]},
                    ),
                    DocumentBlock(
                        id="block:p1:000003",
                        kind="image",
                        page=1,
                        source_index=2,
                        bbox=BoundingBox(x=0.1, y=0.4, width=0.8, height=0.4),
                        text="A prose sentence mentioning Figure 9 later.",
                        structured={},
                    ),
                ],
            )
        ],
    )
    raw = PaperEvidenceGraph.model_validate(
        _raw_graph_with_only_text_anchor(
            artifact,
            quote,
            1,
            region.model_dump() | {"unit": "normalized"},
        ),
        strict=True,
    )

    enriched = link_referenced_visuals(raw, document)

    assert enriched.model_dump(mode="json") == raw.model_dump(mode="json")


def test_required_visual_anchor_links_scale_beyond_small_graphs(
    artifact,
) -> None:
    quote = "如图5所示。"
    region = BoundingBox(x=0.1, y=0.1, width=0.8, height=0.1)
    caption = "图5 模拟结果"
    document = _document(
        artifact,
        [
            DocumentPage(
                number=1,
                width=600,
                height=800,
                blocks=[
                    DocumentBlock(
                        id="block:p1:000001",
                        kind="paragraph",
                        page=1,
                        source_index=0,
                        bbox=region,
                        text=quote,
                    ),
                    DocumentBlock(
                        id="block:p1:000002",
                        kind="image",
                        page=1,
                        source_index=1,
                        bbox=BoundingBox(x=0.1, y=0.3, width=0.8, height=0.5),
                        text=caption,
                        structured={"image_caption": [caption]},
                    ),
                ],
            )
        ],
    )
    payload = _raw_graph_with_only_text_anchor(
        artifact,
        quote,
        1,
        region.model_dump() | {"unit": "normalized"},
    )
    identity = {
        "artifact_id": artifact.artifact_id,
        "version_id": artifact.version_id,
        "artifact_sha256": artifact.artifact_sha256,
        "media_type": artifact.media_type,
    }
    existing_anchor_count = 64
    for index in range(1, existing_anchor_count + 1):
        payload["anchors"].append(
            {
                "id": f"anchor.metadata.{index}",
                **identity,
                "anchor_type": "metadata",
                "locator": {
                    "page": 1,
                    "section": f"Metadata {index}",
                    "paragraph": None,
                    "figure": None,
                    "table": None,
                    "row_range": None,
                    "column_names": [],
                    "series": None,
                    "region": None,
                    "quote": None,
                },
            }
        )
    raw = PaperEvidenceGraph.model_validate(payload, strict=True)

    enriched = link_referenced_visuals(raw, document)

    assert len(raw.anchors) == existing_anchor_count + 1
    assert not any(anchor.anchor_type == "figure" for anchor in raw.anchors)
    assert len(enriched.anchors) == existing_anchor_count + 2
    figure = next(
        anchor for anchor in enriched.anchors if anchor.anchor_type == "figure"
    )
    assert figure.locator.quote == caption
    assert figure.locator.figure == "图5"
