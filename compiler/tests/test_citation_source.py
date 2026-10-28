from __future__ import annotations

from compiler_service.citation_source import build_citation_source
from compiler_service.models import DocumentBlock, DocumentIR, DocumentPage


def _utf16_slice(value: str, start: int, end: int) -> str:
    encoded = value.encode("utf-16-le")
    return encoded[start * 2 : end * 2].decode("utf-16-le")


def _document(artifact, parser_metadata, blocks: list[DocumentBlock]) -> DocumentIR:
    return DocumentIR(
        artifact=artifact,
        parser=parser_metadata,
        page_count=1,
        pages=[DocumentPage(number=1, blocks=blocks)],
        markdown="citation reception test",
    )


def _block(source_index: int, kind: str, text: str) -> DocumentBlock:
    return DocumentBlock(
        id=f"block.citation.{source_index}",
        kind=kind,
        page=1,
        source_index=source_index,
        bbox=None,
        text=text,
    )


def test_citation_source_is_deterministic_and_keeps_all_resolved_mentions(
    artifact, parser_metadata
) -> None:
    document = _document(
        artifact,
        parser_metadata,
        [
            _block(0, "title", "Results"),
            _block(1, "paragraph", "Methods followed the published protocol [1]."),
            _block(
                2,
                "paragraph",
                "Our 🧪 independent replication confirmed a 12% increase "
                "[1, 2, 99], "
                "while Smith (2021) was inconsistent with our null subgroup result.",
            ),
            _block(3, "title", "References"),
            _block(
                4,
                "citation",
                "[1] Alpha A. Result one. 2020. doi:10.1234/ABC.1\n"
                "[2] Smith B. Result two. 2021. doi:10.5678/XYZ.2",
            ),
        ],
    )

    first = build_citation_source(document)
    second = build_citation_source(document)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert [item.ordinal for item in first.references] == [1, 2]
    assert [item.doi for item in first.references] == [
        "10.1234/abc.1",
        "10.5678/xyz.2",
    ]
    assert [item.marker for item in first.mentions] == [
        "[1]",
        "[1, 2, 99]",
        "[1, 2, 99]",
        "Smith (2021)",
    ]
    assert [item.group_size for item in first.mentions] == [1, 3, 3, 1]
    assert first.mentions[0].source_block_id == "block.citation.1"
    assert all(
        item.source_block_id == "block.citation.2" for item in first.mentions[1:]
    )
    assert all(
        _utf16_slice(item.exact_quote, item.char_start, item.char_end) == item.marker
        for item in first.mentions
    )
    assert first.mentions[0].candidate_functions == []
    numeric = first.mentions[1]
    assert numeric.char_start > numeric.exact_quote.index(numeric.marker)
    assert all(
        item.candidate_functions
        == [
            "replication",
            "critique",
            "result_comparison",
        ]
        for item in first.mentions[1:]
    )


def test_citation_source_bounds_a_long_source_block_around_the_marker(
    artifact, parser_metadata
) -> None:
    long_context = (
        "prefix " * 8_000
        + "Our independent replication confirmed the reported result [1]."
    )
    document = _document(
        artifact,
        parser_metadata,
        [
            _block(0, "paragraph", long_context),
            _block(1, "title", "References"),
            _block(2, "citation", "[1] Alpha A. Result one. 2020."),
        ],
    )

    source = build_citation_source(document)

    assert len(source.mentions) == 1
    mention = source.mentions[0]
    assert len(mention.exact_quote) <= 32_000
    assert len(mention.context_text) <= 64_000
    assert _utf16_slice(mention.exact_quote, mention.char_start, mention.char_end) == (
        "[1]"
    )


def test_citation_source_uses_the_marker_sentence_not_the_whole_paragraph(
    artifact, parser_metadata
) -> None:
    document = _document(
        artifact,
        parser_metadata,
        [
            _block(
                0,
                "paragraph",
                "An unrelated opening sentence. "
                "Smith et al. (2020) reported the same result [1]. "
                "An unrelated closing sentence.",
            ),
            _block(1, "title", "References"),
            _block(2, "citation", "[1] Smith A. Same result. 2020."),
        ],
    )

    source = build_citation_source(document)

    numeric = next(item for item in source.mentions if item.marker == "[1]")
    assert numeric.exact_quote == ("Smith et al. (2020) reported the same result [1].")
    assert _utf16_slice(numeric.exact_quote, numeric.char_start, numeric.char_end) == (
        "[1]"
    )


def test_citation_source_resolves_flattened_superscript_and_citation_cue_numbers(
    artifact, parser_metadata
) -> None:
    document = _document(
        artifact,
        parser_metadata,
        [
            _block(
                0,
                "paragraph",
                "Prior studies20–22 support this result. Kato et al. 24 agreed.",
            ),
            _block(1, "title", "References"),
            _block(
                2,
                "citation",
                "[20] A. 2020.\n[21] B. 2021.\n[22] C. 2022.\n[24] Kato. 2024.",
            ),
        ],
    )

    source = build_citation_source(document)

    assert [item.marker for item in source.mentions] == [
        "20–22",
        "20–22",
        "20–22",
        "24",
    ]
    assert [item.group_size for item in source.mentions] == [3, 3, 3, 1]
    assert all(
        _utf16_slice(item.exact_quote, item.char_start, item.char_end) == item.marker
        for item in source.mentions
    )


def test_citation_source_keeps_each_unnumbered_list_item_as_one_reference(
    artifact, parser_metadata
) -> None:
    reference_block = _block(2, "list", "")
    reference_block = reference_block.model_copy(
        update={
            "structured": {
                "list_items": [
                    "Abbott, A. First study. 2019.",
                    "Haley, B. Second study. 2004.",
                ]
            }
        }
    )
    document = _document(
        artifact,
        parser_metadata,
        [
            _block(0, "paragraph", "Abbott et al. (2019) reported this result."),
            _block(1, "title", "References"),
            reference_block,
        ],
    )

    source = build_citation_source(document)

    assert [item.raw_text for item in source.references] == [
        "Abbott, A. First study. 2019.",
        "Haley, B. Second study. 2004.",
    ]
    assert [item.marker for item in source.mentions] == ["Abbott et al. (2019)"]


def test_citation_source_preserves_surname_particles_and_fullwidth_parentheses(
    artifact, parser_metadata
) -> None:
    document = _document(
        artifact,
        parser_metadata,
        [
            _block(
                0,
                "paragraph",
                "Van Dover et al. (2017) and De Carlo（1991） reported this.",
            ),
            _block(1, "title", "References"),
            _block(
                2,
                "citation",
                "[1] Van Dover C. Biodiversity loss. 2017.\n"
                "[2] De Carlo E. Paleoceanographic study. 1991.",
            ),
        ],
    )

    source = build_citation_source(document)

    assert [item.marker for item in source.mentions] == [
        "Van Dover et al. (2017)",
        "De Carlo（1991）",
    ]


def test_citation_source_tolerates_spaced_or_decorated_reference_heading(
    artifact, parser_metadata
) -> None:
    for heading in ("Refer enc es", "REFERENCES*"):
        document = _document(
            artifact,
            parser_metadata,
            [
                _block(0, "paragraph", "Prior work [1] reported this."),
                _block(1, "paragraph", heading),
                _block(2, "list", "[1] Alpha A. Prior work. 2020."),
            ],
        )

        source = build_citation_source(document)

        assert len(source.references) == 1
        assert [item.marker for item in source.mentions] == ["[1]"]


def test_citation_source_repairs_reference_year_ocr_and_compacted_et_al(
    artifact, parser_metadata
) -> None:
    document = _document(
        artifact,
        parser_metadata,
        [
            _block(0, "paragraph", "Kato etal．（2011） reported this result."),
            _block(1, "paragraph", "Refer enc es"),
            _block(
                2,
                "list",
                "KatoY, FujinagaK and NakamuraK. 2O11. Deep-sea mud.",
            ),
        ],
    )

    source = build_citation_source(document)

    assert source.references[0].year == 2011
    assert [item.marker for item in source.mentions] == ["Kato etal．（2011）"]


def test_citation_source_recovers_author_year_after_compacted_by_cue(
    artifact, parser_metadata
) -> None:
    document = _document(
        artifact,
        parser_metadata,
        [
            _block(0, "paragraph", "This was suggestedbyDeCarlo(1991)."),
            _block(1, "paragraph", "REFERENCES*"),
            _block(2, "list", "De Carlo, E. Tracer study. (1991)."),
        ],
    )

    source = build_citation_source(document)

    assert [item.marker for item in source.mentions] == ["DeCarlo(1991)"]


def test_citation_source_repairs_a_displaced_numeric_reference_label(
    artifact, parser_metadata
) -> None:
    document = _document(
        artifact,
        parser_metadata,
        [
            _block(0, "paragraph", "Earlier mining results [112] were negative."),
            _block(1, "paragraph", "References"),
            _block(
                2,
                "list",
                "Jones DO, et al. Biological responses to mining.112. PLoS One 2017.",
            ),
        ],
    )

    source = build_citation_source(document)

    assert source.references[0].label == "112"
    assert "mining. PLoS" in source.references[0].raw_text
    assert [item.marker for item in source.mentions] == ["[112]"]


def test_citation_source_repairs_space_after_doi_slash(
    artifact, parser_metadata
) -> None:
    document = _document(
        artifact,
        parser_metadata,
        [
            _block(0, "paragraph", "Prior work [1] reported this."),
            _block(1, "paragraph", "References"),
            _block(
                2,
                "list",
                "[1] Alpha A. Prior work. https://doi.org/10.2343/ geochemj.2.0361",
            ),
        ],
    )

    source = build_citation_source(document)

    assert source.references[0].doi == "10.2343/geochemj.2.0361"


def test_citation_source_does_not_guess_between_duplicate_numeric_labels(
    artifact, parser_metadata
) -> None:
    document = _document(
        artifact,
        parser_metadata,
        [
            _block(0, "paragraph", "Our result was consistent with prior work [1]."),
            _block(1, "title", "References"),
            _block(
                2,
                "citation",
                "[1] Alpha A. Result one. 2020.\n[1] Beta B. Different result. 2021.",
            ),
        ],
    )

    source = build_citation_source(document)

    assert len(source.references) == 2
    assert source.mentions == []
    assert any(
        "duplicate numeric bibliography labels" in item for item in source.warnings
    )


def test_citation_source_keeps_correctness_without_correction_false_positive(
    artifact, parser_metadata
) -> None:
    document = _document(
        artifact,
        parser_metadata,
        [
            _block(0, "paragraph", "The analysis corrected for age [1]."),
            _block(1, "paragraph", "The earlier conclusion was accurate [1]."),
            _block(2, "paragraph", "先前研究的结论是正确的 [1]。"),
            _block(3, "title", "References"),
            _block(4, "citation", "[1] Alpha A. Result one. 2020."),
        ],
    )

    source = build_citation_source(document)

    assert [item.exact_quote for item in source.mentions] == [
        "The analysis corrected for age [1].",
        "The earlier conclusion was accurate [1].",
        "先前研究的结论是正确的 [1]。",
    ]
    assert [item.candidate_functions for item in source.mentions] == [
        [],
        ["result_comparison"],
        ["result_comparison"],
    ]


def test_citation_source_recognizes_explicit_scientific_comparison_phrases(
    artifact, parser_metadata
) -> None:
    document = _document(
        artifact,
        parser_metadata,
        [
            _block(
                0,
                "paragraph",
                "It has been shown by Yasukawa et al. [1] that hydrothermal "
                "components do not play a role in REE enrichment.",
            ),
            _block(
                1,
                "paragraph",
                "Clay minerals have similarly been described as a major phase "
                "influencing porewater patterns (Abbott et al., 2019).",
            ),
            _block(
                2,
                "paragraph",
                "The enrichment is not as high as has been observed at other "
                "North Pacific sites (Deng et al., 2017).",
            ),
            _block(
                3,
                "paragraph",
                "By combining new data from Indian Ocean sites [4], we "
                "constructed a comprehensive compositional dataset.",
            ),
            _block(
                4,
                "paragraph",
                "While studies have described the geochemistry of special "
                "regions [5], their microbial ecology remains uncharacterized.",
            ),
            _block(
                5,
                "paragraph",
                "Data from supplementary material [6] reveal that Gd and Lu "
                "concentrations are strongly correlated with total REE.",
            ),
            _block(
                6,
                "paragraph",
                "Pelagic clay cores include a layer containing unusual "
                "amounts of fish debris [7].",
            ),
            _block(
                7,
                "paragraph",
                "A similarly described laboratory protocol was used to "
                "prepare the sediment samples [8].",
            ),
            _block(
                8,
                "paragraph",
                "The requisite environmental condition for the formation of "
                "phillipsite in marine sediment is similar to that of pelagic "
                "clay enriched in BCP grains having a high REY content20–22.",
            ),
            _block(
                9,
                "paragraph",
                "The experimental method is similar to a previously "
                "described laboratory protocol [9].",
            ),
            _block(
                10,
                "paragraph",
                "Our measured results were similar to previously reported "
                "observations [10].",
            ),
            _block(11, "title", "References"),
            _block(
                12,
                "citation",
                "[1] Yasukawa K. Hydrothermal components. 2015.\n"
                "[2] Abbott A. Clay minerals. 2019.\n"
                "[3] Deng Y. North Pacific porewater. 2017.\n"
                "[4] Ocean survey. 2016.\n"
                "[5] Geochemistry survey. 2017.\n"
                "[6] Supplemental data. 2018.\n"
                "[7] Fish debris survey. 2020.\n"
                "[8] Laboratory protocol. 2021.\n"
                "[9] Prior laboratory protocol. 2022.\n"
                "[10] Earlier measurement. 2023.\n"
                "[20] Pelagic clay background. 2013.\n"
                "[21] Yasukawa K. Indian Ocean mud. 2015.\n"
                "[22] Yasukawa K. REY-rich mud. 2016.",
            ),
        ],
    )

    source = build_citation_source(document)

    assert [(item.marker, item.candidate_functions) for item in source.mentions] == [
        ("[1]", []),
        ("Abbott et al., 2019", ["result_comparison"]),
        ("Deng et al., 2017", ["result_comparison"]),
        ("[4]", []),
        ("[5]", []),
        ("[6]", []),
        ("[7]", []),
        ("[8]", []),
        ("20–22", []),
        ("20–22", []),
        ("20–22", []),
        ("[9]", []),
        ("[10]", ["result_comparison"]),
    ]


def test_author_year_groups_keep_exact_targets_and_full_group_size(
    artifact, parser_metadata
) -> None:
    document = _document(
        artifact,
        parser_metadata,
        [
            _block(
                0,
                "paragraph",
                "These data are consistent with prior reports "
                "(Ikonomidou et al., 1999, 2001; "
                "Jevtovic-Todorovic et al., 2003; Olney et al., 2002).",
            ),
            _block(1, "title", "References"),
            _block(
                2,
                "citation",
                "[1] Ikonomidou C. First result. 1999.\n"
                "[2] Ikonomidou C. Second result. 2001.\n"
                "[3] Jevtovic-Todorovic V. Third result. 2003.\n"
                "[4] Olney J. Fourth result. 2002.",
            ),
        ],
    )

    source = build_citation_source(document)

    assert [item.marker for item in source.mentions] == [
        "Ikonomidou et al., 1999",
        "2001",
        "Jevtovic-Todorovic et al., 2003",
        "Olney et al., 2002",
    ]
    assert [item.group_size for item in source.mentions] == [4, 4, 4, 4]


def test_critical_limitation_cues_are_retained_without_method_use_false_positive(
    artifact, parser_metadata
) -> None:
    document = _document(
        artifact,
        parser_metadata,
        [
            _block(
                0,
                "paragraph",
                "The model in Collins (1997) failed to generate punctuation.",
            ),
            _block(
                1,
                "paragraph",
                "We achieved better accuracy by using RFTagger "
                "(Schmid and Laws, 2008).",
            ),
            _block(2, "title", "References"),
            _block(
                3,
                "citation",
                "[1] Collins M. Parsing model. 1997.\n"
                "[2] Schmid H. and Laws F. RFTagger. 2008.",
            ),
        ],
    )

    source = build_citation_source(document)

    assert [(item.marker, item.candidate_functions) for item in source.mentions] == [
        ("Collins (1997)", ["critique"]),
        ("Schmid and Laws, 2008", []),
    ]


def _source(artifact, parser_metadata, body: str, references: str, *, kind="citation"):
    reference_block = _block(2, kind, references)
    if kind == "list":
        # One unnumbered entry per list item, as MinerU emits bibliographies.
        reference_block = reference_block.model_copy(
            update={"text": "", "structured": {"list_items": references.split("\n")}}
        )
    return build_citation_source(
        _document(
            artifact,
            parser_metadata,
            [
                _block(0, "paragraph", body),
                _block(1, "title", "References"),
                reference_block,
            ],
        )
    )


def test_phrase_openers_and_bare_years_never_become_author_year_citations(
    artifact, parser_metadata
) -> None:
    source = _source(
        artifact,
        parser_metadata,
        "In 2020 the outbreak spread. The 2019 season was dry. Since 2015 "
        "rates rose. Smith 2018 is unrelated text. In March 2021 we sampled.",
        "Inman, A. A study in 2020.\nThe Society. Report. 2019.\n"
        "Smith, B. Rates. 2018.\nMarch, C. Sampling. 2021.",
        kind="list",
    )
    assert source.mentions == []


def test_surname_must_be_the_leading_author_and_end_at_a_word_boundary(
    artifact, parser_metadata
) -> None:
    source = _source(
        artifact,
        parser_metadata,
        "As reported (Li, 2020), and later by Müller et al. (2021).",
        "Liu, X., Li, Y. A coauthored study. 2020.\n"
        "Mueller, K. Other. 2019.\nMüller, A., Weber, B., Klein, C. Isotopes. 2021.",
        kind="list",
    )
    assert [item.marker for item in source.mentions] == ["Müller et al. (2021)"]


def test_cjk_author_year_resolves_the_name_suffix(artifact, parser_metadata) -> None:
    source = _source(
        artifact,
        parser_metadata,
        "研究表明张三等（2020）发现汞异常，与李四和王五(2019)的结果一致。",
        "张三, 赵六. 汞异常研究[J]. 地质学报, 2020.\n"
        "李四, 王五. 火山作用[J]. 岩石学报, 2019.",
        kind="list",
    )
    assert [item.marker for item in source.mentions] == [
        "张三等（2020）",
        "李四和王五(2019)",
    ]


def test_synthetic_ordinals_never_resolve_numeric_markers(
    artifact, parser_metadata
) -> None:
    source = _source(
        artifact,
        parser_metadata,
        "Area of 5 km2 shows p53 expression [1] and H3K4me3 marks.",
        "Abbott, A. First study. 2019.\nHaley, B. Second study. 2004.",
        kind="list",
    )
    # An unnumbered bibliography has display ordinals only.
    assert [item.label for item in source.references] == ["1", "2"]
    assert source.mentions == []


def test_units_genes_and_charges_are_not_flattened_superscripts(
    artifact, parser_metadata
) -> None:
    source = _source(
        artifact,
        parser_metadata,
        "Over 5 km2 the Fe2+ and Sox2 levels rose, as shown previously3.",
        "[1] A. 2001.\n[2] B. 2002.\n[3] C. 2003.\n[5] E. 2005.",
    )
    assert [item.marker for item in source.mentions] == ["3"]


def test_unicode_and_markup_superscripts_resolve(artifact, parser_metadata) -> None:
    source = _source(
        artifact,
        parser_metadata,
        "Mercury anomalies were reported¹,² and dated$^{3}$; area 4 m² only.",
        "[1] A. 2001.\n[2] B. 2002.\n[3] C. 2003.\n[4] D. 2004.",
    )
    assert [item.marker for item in source.mentions] == ["¹,²", "¹,²", "$^{3}$"]


def test_inline_entries_numbered_headings_and_tail_sections_segment(
    artifact, parser_metadata
) -> None:
    document = _document(
        artifact,
        parser_metadata,
        [
            _block(0, "paragraph", "Prior work [1] and [2] agree."),
            _block(1, "title", "5. References"),
            _block(
                2, "paragraph", "[1] Alpha A. First. 2020. [2] Beta B. Second. 2021."
            ),
            _block(3, "title", "Author contributions"),
            _block(4, "paragraph", "[3] Not a reference. 2022."),
        ],
    )
    source = build_citation_source(document)
    assert [item.label for item in source.references] == ["1", "2"]
    assert [item.marker for item in source.mentions] == ["[1]", "[2]"]


def test_doi_broken_across_lines_is_rejoined(artifact, parser_metadata) -> None:
    source = _source(
        artifact,
        parser_metadata,
        "Prior work [1].",
        "[1] Alpha A. Title. 2020. https://doi.org/10.1029/2019GL08-\n5123.",
    )
    assert source.references[0].doi == "10.1029/2019gl08-5123"


def test_abbreviations_do_not_split_the_cited_sentence(
    artifact, parser_metadata
) -> None:
    source = _source(
        artifact,
        parser_metadata,
        "Several tracers, e.g. Hg and Fig. 2 data, agree with prior work [1].",
        "[1] Alpha A. Title. 2020.",
    )
    (mention,) = source.mentions
    assert mention.exact_quote.startswith("Several tracers, e.g. Hg")
