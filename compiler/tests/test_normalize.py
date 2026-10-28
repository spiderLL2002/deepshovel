from __future__ import annotations

import json
from copy import deepcopy

import pytest
from conftest import FIXTURES
from pydantic import ValidationError

from compiler_service.errors import ContractError
from compiler_service.mineru import MineruOutput
from compiler_service.models import BoundingBox
from compiler_service.normalize import _bbox, normalize_mineru


def test_real_pipeline_geometry_maps_legacy_bbox_through_page_size() -> None:
    fixture = json.loads(
        (FIXTURES / "mineru-pipeline-geometry.json").read_text(encoding="utf-8")
    )
    width, height = fixture["page_size"]
    result = _bbox(
        fixture["content_list_bbox"],
        width,
        height,
        [tuple(fixture["middle_bbox"])],
    )

    assert result is not None
    assert result.model_dump() == pytest.approx(
        {
            "x": 105 / 612,
            "y": 49 / 792,
            "width": (509 - 105) / 612,
            "height": (65 - 49) / 792,
        },
        abs=0.002,
    )


def test_exact_middle_bbox_uses_constant_time_lookup(monkeypatch) -> None:
    middle = (60.0, 160.0, 300.0, 240.0)

    def unexpected_scan(*args, **kwargs) -> float:
        del args, kwargs
        raise AssertionError("exact coordinate matches must not scan the page")

    monkeypatch.setattr(
        "compiler_service.normalize._relative_edge_error", unexpected_scan
    )
    result = _bbox(
        [100, 200, 500, 300],
        600,
        800,
        [middle],
        frozenset({middle}),
    )

    assert result is not None
    assert result.model_dump() == pytest.approx(
        {"x": 0.1, "y": 0.2, "width": 0.4, "height": 0.1}
    )


def test_normalizes_official_1000_mapping_against_real_page_geometry(
    artifact, mineru_output
) -> None:
    parsed = normalize_mineru(mineru_output, artifact)

    assert parsed.document.page_count == 1
    assert parsed.document.pages[0].width == 600
    assert parsed.document.pages[0].height == 800
    text = parsed.document.pages[0].blocks[0]
    assert text.bbox is not None
    assert text.bbox.x == pytest.approx(0.1)
    assert text.bbox.y == pytest.approx(0.2)
    assert text.bbox.width == pytest.approx(0.4)
    assert text.bbox.height == pytest.approx(0.1)
    table = parsed.document.pages[0].blocks[1]
    assert table.kind == "table"
    assert "5 mg" in table.text
    assert table.structured["table_body"].startswith("<table>")
    assert parsed.document.warnings == []


def test_chart_caption_and_footnote_are_searchable_block_text(
    artifact, mineru_output
) -> None:
    content = deepcopy(mineru_output.content_list)
    chart = content[1]
    chart["type"] = "chart"
    chart.pop("table_caption")
    chart.pop("table_body")
    chart.pop("table_footnote")
    chart["chart_caption"] = ["Figure 1. Measured response by treatment."]
    chart["chart_footnote"] = ["Error bars show one standard deviation."]

    block = (
        normalize_mineru(_copy_output(mineru_output, content=content), artifact)
        .document.pages[0]
        .blocks[1]
    )

    assert block.kind == "chart"
    assert block.text == (
        "Figure 1. Measured response by treatment.\n"
        "Error bars show one standard deviation."
    )


def test_list_items_are_searchable_block_text(artifact, mineru_output) -> None:
    content = deepcopy(mineru_output.content_list)
    item = content[0]
    item["type"] = "list"
    item.pop("text")
    item["list_items"] = [
        "1. First bibliography entry.",
        "2. Second bibliography entry.",
    ]

    block = (
        normalize_mineru(_copy_output(mineru_output, content=content), artifact)
        .document.pages[0]
        .blocks[0]
    )

    assert block.kind == "list"
    assert block.text == ("1. First bibliography entry.\n2. Second bibliography entry.")


@pytest.mark.parametrize(
    ("starting_page", "first_fragment", "continuation"),
    [
        (
            5,
            "Pore water REE concentrations are up to two orders of magnitude "
            "enriched relative to ambient seawater. In general, the highest "
            "pore water REE concentrations were observed in the North Pacific",
            "(Abbott et al., 2015b) with lower concentrations observed in "
            "the Tasman Sea. The enrichment is not as high as has been "
            "observed at other North Pacific sites (Deng et al., 2017).",
        ),
        (
            14,
            "In conclusion, both Ca phosphates and Fe-rich clays are "
            "potential REY-controlling phases. Jarvis suggested a combination "
            "of Fe phases. As Fe phases but not Fe-",
            "(oxyhydr)oxides release some REY to the porewater. Clay "
            "minerals have similarly been described as a major phase "
            "influencing porewater patterns (Abbott et al., 2019).",
        ),
    ],
    ids=["physical-pages-five-to-six", "physical-pages-fourteen-to-fifteen"],
)
def test_cross_page_merged_paragraph_is_split_using_physical_middle_spans(
    artifact, mineru_output, starting_page, first_fragment, continuation
) -> None:
    pages = [
        {"page_idx": index, "page_size": [600, 800], "para_blocks": []}
        for index in range(starting_page + 1)
    ]
    first_bbox = [60, 680, 540, 760]
    continuation_bbox = [72, 64, 528, 320]
    pages[starting_page - 1]["para_blocks"].append(
        {
            "type": "text",
            "bbox": first_bbox,
            "lines": [{"spans": [{"type": "text", "content": first_fragment}]}],
        }
    )
    pages[starting_page]["para_blocks"].extend(
        [
            {
                "type": "text",
                "bbox": [60, 24, 300, 52],
                "lines": [
                    {
                        "spans": [
                            {
                                "type": "text",
                                "content": "Unrelated next-page running header.",
                            }
                        ]
                    }
                ],
            },
            {
                "type": "text",
                "bbox": continuation_bbox,
                "lines": [{"spans": [{"type": "text", "content": continuation}]}],
            },
        ]
    )
    content = [
        {
            "type": "text",
            "page_idx": starting_page - 1,
            "bbox": [100, 850, 900, 950],
            "text": f"{first_fragment} {continuation}",
        }
    ]

    document = normalize_mineru(
        _copy_output(
            mineru_output,
            content=content,
            middle={"pdf_info": pages},
        ),
        artifact,
    ).document

    first = document.pages[starting_page - 1].blocks[0]
    continued = document.pages[starting_page].blocks[0]
    assert first.text == first_fragment
    assert first.page == starting_page
    assert first.id == f"block:p{starting_page}:000001"
    assert continued.text == continuation
    assert continued.page == starting_page + 1
    assert continued.id == f"block:p{starting_page + 1}:000001"
    assert first.source_index == continued.source_index == 0
    assert continued.structured["page_idx"] == starting_page
    assert continued.structured["bbox"] == continuation_bbox
    assert continued.bbox is not None
    assert continued.bbox.model_dump() == pytest.approx(
        {"x": 0.12, "y": 0.08, "width": 0.76, "height": 0.32}
    )


@pytest.mark.parametrize(
    "first_bbox",
    [[60, 680, 540, 760], [60, 320, 540, 460]],
    ids=["real-038-near-page-bottom", "non-bottom-paragraph-before-visuals"],
)
def test_preproc_page_prefix_disambiguates_cross_page_merged_para_block(
    artifact, mineru_output, first_bbox
) -> None:
    first_fragment = (
        "In general, the highest pore water REE concentrations were "
        "observed in the North Pacific with lower concentrations elsewhere."
    )
    continuation = (
        "The enrichment is not as high as has been observed at other "
        "North Pacific sites including Deng et al., 2017."
    )
    merged = f"{first_fragment} {continuation}"
    continuation_bbox = [72, 64, 528, 320]

    def paragraph(text, bbox):
        return {
            "type": "text",
            "bbox": bbox,
            "lines": [{"spans": [{"type": "text", "content": text}]}],
        }

    pages = [
        {
            "page_idx": 0,
            "page_size": [600, 800],
            "para_blocks": [paragraph(merged, first_bbox)],
            "preproc_blocks": [paragraph(first_fragment, first_bbox)],
        },
        {
            "page_idx": 1,
            "page_size": [600, 800],
            "para_blocks": [{"type": "text", "bbox": continuation_bbox, "lines": []}],
            "preproc_blocks": [paragraph(continuation, continuation_bbox)],
        },
    ]
    content = [{"type": "text", "page_idx": 0, "bbox": first_bbox, "text": merged}]

    document = normalize_mineru(
        _copy_output(mineru_output, content=content, middle={"pdf_info": pages}),
        artifact,
    ).document

    assert document.pages[0].blocks[0].text == first_fragment
    assert document.pages[1].blocks[0].text == continuation
    assert document.pages[1].blocks[0].structured["bbox"] == continuation_bbox


def test_cross_page_paragraph_rejects_ambiguous_real_continuation_geometry(
    artifact, mineru_output
) -> None:
    first_fragment = (
        "The original physical page establishes a unique scientific "
        "statement before the continuation begins."
    )
    continuation = (
        "The next physical page supplies a continuation long enough "
        "to match the strict source-text anchor."
    )
    merged = f"{first_fragment} {continuation}"
    first_bbox = [60, 320, 540, 460]

    def paragraph(text, bbox):
        return {
            "type": "text",
            "bbox": bbox,
            "lines": [{"spans": [{"type": "text", "content": text}]}],
        }

    pages = [
        {
            "page_idx": 0,
            "page_size": [600, 800],
            "para_blocks": [paragraph(merged, first_bbox)],
            "preproc_blocks": [paragraph(first_fragment, first_bbox)],
        },
        {
            "page_idx": 1,
            "page_size": [600, 800],
            "para_blocks": [],
            "preproc_blocks": [
                paragraph(continuation, [72, 64, 528, 320]),
                paragraph(continuation, [72, 340, 528, 560]),
            ],
        },
    ]
    content = [{"type": "text", "page_idx": 0, "bbox": first_bbox, "text": merged}]

    document = normalize_mineru(
        _copy_output(mineru_output, content=content, middle={"pdf_info": pages}),
        artifact,
    ).document

    assert document.pages[0].blocks[0].text == merged
    assert document.pages[1].blocks == []


@pytest.mark.parametrize(
    "next_page_text",
    [None, "An unrelated paragraph that supplies no evidence of continuation."],
    ids=["missing-middle-spans", "nonmatching-middle-spans"],
)
def test_cross_page_paragraph_is_not_split_without_unique_page_evidence(
    artifact, mineru_output, next_page_text
) -> None:
    first_fragment = (
        "Pore water REE concentrations are up to two orders of magnitude "
        "enriched relative to ambient seawater."
    )
    continuation = (
        "The enrichment is not as high as has been observed at other North "
        "Pacific sites (Deng et al., 2017)."
    )
    next_blocks = (
        []
        if next_page_text is None
        else [
            {
                "type": "text",
                "bbox": [72, 64, 528, 320],
                "lines": [{"spans": [{"type": "text", "content": next_page_text}]}],
            }
        ]
    )
    pages = [
        {
            "page_idx": 0,
            "page_size": [600, 800],
            "para_blocks": [
                {
                    "type": "text",
                    "bbox": [60, 680, 540, 760],
                    "lines": [{"spans": [{"type": "text", "content": first_fragment}]}],
                }
            ],
        },
        {"page_idx": 1, "page_size": [600, 800], "para_blocks": next_blocks},
    ]
    content = [
        {
            "type": "text",
            "page_idx": 0,
            "bbox": [100, 850, 900, 950],
            "text": f"{first_fragment} {continuation}",
        }
    ]

    document = normalize_mineru(
        _copy_output(mineru_output, content=content, middle={"pdf_info": pages}),
        artifact,
    ).document

    assert len(document.pages[0].blocks) == 1
    assert document.pages[0].blocks[0].text == f"{first_fragment} {continuation}"
    assert document.pages[1].blocks == []


def test_figure_caption_and_footnote_are_searchable_block_text(
    artifact, mineru_output
) -> None:
    content = deepcopy(mineru_output.content_list)
    figure = content[1]
    figure["type"] = "figure"
    figure.pop("table_caption")
    figure.pop("table_body")
    figure.pop("table_footnote")
    figure["figure_caption"] = ["Figure 1. Measured response by treatment."]
    figure["figure_footnote"] = ["Error bars show one standard deviation."]

    block = (
        normalize_mineru(_copy_output(mineru_output, content=content), artifact)
        .document.pages[0]
        .blocks[1]
    )

    assert block.kind == "figure"
    assert block.text == (
        "Figure 1. Measured response by treatment.\n"
        "Error bars show one standard deviation."
    )


def test_also_accepts_pipeline_page_unit_bbox(artifact, mineru_output) -> None:
    content = deepcopy(mineru_output.content_list)
    content[0]["bbox"] = [60, 160, 300, 240]
    output = MineruOutput(
        version=mineru_output.version,
        method=mineru_output.method,
        duration_ms=mineru_output.duration_ms,
        markdown=mineru_output.markdown,
        content_list=content,
        middle_json=mineru_output.middle_json,
    )

    block = normalize_mineru(output, artifact).document.pages[0].blocks[0]
    assert block.bbox is not None
    assert block.bbox.model_dump() == pytest.approx(
        {"x": 0.1, "y": 0.2, "width": 0.4, "height": 0.1}
    )


def test_missing_bbox_remains_unknown(artifact, mineru_output) -> None:
    content = deepcopy(mineru_output.content_list)
    content[0].pop("bbox")
    output = MineruOutput(
        version=mineru_output.version,
        method=mineru_output.method,
        duration_ms=mineru_output.duration_ms,
        markdown=mineru_output.markdown,
        content_list=content,
        middle_json=mineru_output.middle_json,
    )

    assert normalize_mineru(output, artifact).document.pages[0].blocks[0].bbox is None


def test_unrelated_bbox_is_not_turned_into_a_region(artifact, mineru_output) -> None:
    content = deepcopy(mineru_output.content_list)
    content[0]["bbox"] = [700, 700, 800, 800]
    output = MineruOutput(
        version=mineru_output.version,
        method=mineru_output.method,
        duration_ms=mineru_output.duration_ms,
        markdown=mineru_output.markdown,
        content_list=content,
        middle_json=mineru_output.middle_json,
    )

    assert normalize_mineru(output, artifact).document.pages[0].blocks[0].bbox is None


@pytest.mark.parametrize("page_index", [True, 0.0])
def test_content_page_index_requires_a_real_integer(
    artifact, mineru_output, page_index
) -> None:
    content = deepcopy(mineru_output.content_list)
    content[0]["page_idx"] = page_index
    output = _copy_output(mineru_output, content=content)

    with pytest.raises(ContractError, match="page_idx is invalid"):
        normalize_mineru(output, artifact)


def test_middle_page_index_rejects_boolean(artifact, mineru_output) -> None:
    middle = deepcopy(mineru_output.middle_json)
    middle["pdf_info"][0]["page_idx"] = True

    with pytest.raises(ContractError, match="page_idx is invalid"):
        normalize_mineru(_copy_output(mineru_output, middle=middle), artifact)


@pytest.mark.parametrize("invalid", [True, float("inf"), float("nan")])
def test_content_bbox_rejects_boolean_and_nonfinite_numbers(
    artifact, mineru_output, invalid
) -> None:
    content = deepcopy(mineru_output.content_list)
    content[0]["bbox"][0] = invalid

    with pytest.raises(ContractError, match="four finite JSON numbers"):
        normalize_mineru(_copy_output(mineru_output, content=content), artifact)


@pytest.mark.parametrize("invalid", [True, float("inf"), float("nan")])
def test_middle_geometry_rejects_boolean_and_nonfinite_numbers(
    artifact, mineru_output, invalid
) -> None:
    middle = deepcopy(mineru_output.middle_json)
    middle["pdf_info"][0]["page_size"][0] = invalid

    with pytest.raises(ContractError, match="positive finite numbers"):
        normalize_mineru(_copy_output(mineru_output, middle=middle), artifact)


def test_pydantic_models_globally_reject_nonfinite_numbers() -> None:
    with pytest.raises(ValidationError, match="finite number"):
        BoundingBox(x=float("inf"), y=0.0, width=0.1, height=0.1)


def _copy_output(
    original: MineruOutput,
    *,
    content: list[dict] | None = None,
    middle: dict | None = None,
) -> MineruOutput:
    return MineruOutput(
        version=original.version,
        method=original.method,
        duration_ms=original.duration_ms,
        markdown=original.markdown,
        content_list=original.content_list if content is None else content,
        middle_json=original.middle_json if middle is None else middle,
    )


def test_mineru_ref_text_blocks_are_citations(artifact, mineru_output) -> None:
    content = deepcopy(mineru_output.content_list)
    item = next(entry for entry in content if entry.get("type") == "text")
    item["type"] = "ref_text"
    item["text"] = "[1] Alpha A. Prior work. 2020."

    document = normalize_mineru(
        _copy_output(mineru_output, content=content), artifact
    ).document

    kinds = {
        block.text: block.kind for page in document.pages for block in page.blocks
    }
    assert kinds["[1] Alpha A. Prior work. 2020."] == "citation"
