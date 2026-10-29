# Fresh GPT-6 Astra AI source review

Date: 2026-10-27. No overall accuracy estimate is made.

Two prepared tuples, three uptake entries, one automated software UI export; initial direct PDF reading pages 3, 7, 8, supplemented by authorized Table 1 page 4 review; no paper review, prior scores, web, workbook, production rerun, or other source/code directories.

Read PDF text with pypdf and visually inspected complete rendered pages 3/4/7/8; independently recomputed SHA256 with hashlib and canonical-payload equality in initial review; evaluated scientific statements directly against PDF rather than case self-description.

Checks after Table 1 supplement: 41 total; 31 pass; 2 fail; 8 qualified. These checklist counts are not a scientific accuracy rate.

Initial report preserved unchanged as report-initial.json. Three qualified outcomes became pass based on direct Table 1 evidence; both failures remain.

## Main findings

- Two unsupported/misleading metadata assertions remain: production aggregate propagated-uncertainty claim and automated export authorship explicit user choice.
- Figure 3 is on p7; factor and Equation 5 are on p8. Historical combined export locator remains ambiguous; reported new-schema repair was not independently retested here.
- Table 1 p4 directly verifies 1.00 mL time-series stock, 24/48/168 h plateau observations, and 48/96/144 h variable-series times.
- Table 1 p4 independently verifies experiment IDs 6/7/9 and printed uptake 17.69/28.10/47.59%; exact additional workbook digits and workbook cells remain unverified.
- Three digitized candidates are not new independent samples; no generalized extraction-accuracy estimate.

## Supplement scope

Parent reports a new manuscript/schema change splitting figure and value pages. This review did not inspect or retest that change, and the historical export locator qualification and automated authorship failure remain unchanged.

## Independently recomputed hashes

- article_pdf_sha256: `0bbc272cba062a9eb0131e24632ea6d8a48b86a8cdf3cda7cd44dfa9f2932e7c`
- reuse_canonical_payload_sha256: `58ace0486a898ade6c60aea2f7d4fec366bbe7e6e9fb858a8d17f7adf02f7981`

## Itemized source support

| Check | Result | PDF page(s) | Basis |
|---|---|---|---|
| time.quantity_definition_sign_unit | pass | 3, 7 | Equation 2 defines solid-minus-liquid delta; p7 reports negative −0.098 ±0.012 per mille, matching the prepared tuple. |
| time.duration_subset | pass | 3, 7 | p3 gives full time series 0.5–168 h; p7 explicitly restricts the plateau mean to ≥24 h and states 24–168 h; tuple correctly represents a subset. |
| time.observed_times | pass | 4, 7 | Table 1 time-series rows 6, 7, 9 explicitly list 24, 48, 168 h; these are the three ≥24 h rows and match the prepared plateau subset. |
| time.loading_1_mL | pass | 4 | Table 1 time-series rows 6, 7, 9 each explicitly list Fhy stock 1.00 mL; all earlier time-series rows also list 1.00 mL. |
| time.pH | pass | 3 | Methods explicitly set pH 5.5 and readjust daily for durations >24 h. |
| time.NaCl | pass | 3 | Methods explicitly state final NaCl 0.01 mol/L. |
| time.volume | pass | 3 | Methods explicitly state final experiment volume 50 mL. |
| time.material | pass | 7 | Section 3.1 explicitly confirms synthesized 2-line ferrihydrite from XRD. |
| time.uncertainty | pass | 7 | Main text explicitly calls ±0.012 a 95% confidence interval constraining the mean; caption calls the same band 95% se, so the tuple follows the authors’ main text without inventing a recalculation. |
| time.value_figure_page_binding | pass | 7 | Both reported plateau value in section 3.2 and Figure 2a/b occur on p7. |
| loading.quantity_definition_sign_unit | pass | 8 | Equation 5 defines alpha as (1000+solid delta)/(1000+liquid delta); text reports average 10^3 ln alpha = −0.113 ±0.030, consistent with a dimensionless scaled logarithmic factor. |
| loading.authors_equivalence | qualified | 8 | Authors explicitly equate 10^3 ln alpha with delta; this review verifies attribution, not exact algebraic identity, and the tuple preserves the original factor representation. |
| loading.duration_range | pass | 8 | Text explicitly gives 48–144 h and says all experiments ran at least 48 h. |
| loading.observed_times_48_96_144 | pass | 4, 8 | Table 1 variable-adsorption rows 10–12 list 48 h, 13–15 list 96 h, and 16–18 list 144 h; this directly establishes all three distinct times. |
| loading.stock_range | pass | 8 | Text explicitly states ferrihydrite stock solution 0.5–2.5 mL; this is a stock-volume range, not a ferrihydrite mass. |
| loading.pH | pass | 3 | Methods explicitly set pH 5.5 with daily readjustment for long experiments. |
| loading.NaCl | pass | 3 | Methods explicitly state final NaCl 0.01 mol/L. |
| loading.volume | pass | 3 | Methods explicitly state final experiment volume 50 mL. |
| loading.material | pass | 7 | Section 3.1 identifies the synthesized mineral as 2-line ferrihydrite. |
| loading.uncertainty | qualified | 3, 4, 8 | p8 reports aggregate ±0.030 without its construction. Table 1 p4 defines per-row propagated uncertainty as adding uncertainties in quadrature; this does not establish how uncertainty of the average fractionation factor was constructed. Tuple appropriately leaves that construction unresolved. |
| loading.value_page | pass | 8 | The factor, Equation 5, and reported aggregate value occur on p8; case.source.page=8 is correct for these. |
| uptake.24h.PDF_approximate | pass | 4, 7, 8 | Table 1 row 6 explicitly pairs 24 h with Nd sorbed 17.69%; this agrees with the rounded prose/figure values (18%, 28%, 48%) on p7–8. |
| uptake.24h.workbook_precision | qualified | 4 | Table 1 independently confirms experiment 6, duration 24 h and printed uptake 17.69%, consistent with rounding the longer cached decimal. The workbook itself was not distributed or opened; exact additional digits and workbook-cell mapping remain unverified. |
| uptake.24h.candidate_arithmetic | pass | 7 | Recomputed candidate-minus-cached-workbook percentage-point and absolute-time differences agree; this checks arithmetic only, not workbook provenance or independent digitization. |
| uptake.48h.PDF_approximate | pass | 4, 7, 8 | Table 1 row 7 explicitly pairs 48 h with Nd sorbed 28.10%; this agrees with the rounded prose/figure values (18%, 28%, 48%) on p7–8. |
| uptake.48h.workbook_precision | qualified | 4 | Table 1 independently confirms experiment 7, duration 48 h and printed uptake 28.10%, consistent with rounding the longer cached decimal. The workbook itself was not distributed or opened; exact additional digits and workbook-cell mapping remain unverified. |
| uptake.48h.candidate_arithmetic | pass | 7 | Recomputed candidate-minus-cached-workbook percentage-point and absolute-time differences agree; this checks arithmetic only, not workbook provenance or independent digitization. |
| uptake.168h.PDF_approximate | pass | 4, 7, 8 | Table 1 row 9 explicitly pairs 168 h with Nd sorbed 47.59%; this agrees with the rounded prose/figure values (18%, 28%, 48%) on p7–8. |
| uptake.168h.workbook_precision | qualified | 4 | Table 1 independently confirms experiment 9, duration 168 h and printed uptake 47.59%, consistent with rounding the longer cached decimal. The workbook itself was not distributed or opened; exact additional digits and workbook-cell mapping remain unverified. |
| uptake.168h.candidate_arithmetic | pass | 7 | Recomputed candidate-minus-cached-workbook percentage-point and absolute-time differences agree; this checks arithmetic only, not workbook provenance or independent digitization. |
| uptake.candidate_evidence_status | qualified | 7 | Three candidate associations refer to the same three plotted observations; they are not new samples or independent measurements. Their values are visually compatible with Figure 2c, but pixel calibration and unique-nearest selection were not reproduced from independent digitization. |
| interpretation.distinct_observables | pass | 7, 8 | p7–8 explicitly describe a constant isotope offset after 24 h despite continued uptake; this supports the distinction without adding experimental evidence. |
| integrity.PDF_SHA256 | pass | not applicable | Independently hashed distributed PDF bytes: 0bbc272cba062a9eb0131e24632ea6d8a48b86a8cdf3cda7cd44dfa9f2932e7c |
| integrity.export_canonical_SHA256 | pass | not applicable | Independently hashed canonical_payload UTF-8 bytes: 58ace0486a898ade6c60aea2f7d4fec366bbe7e6e9fb858a8d17f7adf02f7981 |
| integrity.export_payload_canonicalization | pass | not applicable | Parsed canonical_payload equals payload; recursive sorted-key compact Unicode JSON serialization reproduces canonical_payload exactly. |
| integrity.export_observation | pass | 8 | Exported observation equals the prepared loading tuple field for field; source reading independently supports its p8 numerical statement. |
| production.observation_anchor_page | pass | 8 | Recorded loading observation and anchor IDs agree across case and production-link; anchor quoted average alpha/factor text matches p8 of the independently hashed PDF. This verifies the archived mapping, not a live retrieval rerun. |
| production.aggregate_uncertainty_claim | fail | 3, 4, 7, 8 | production-link.result.scope.uncertainty asserts propagated uncertainty for the average fractionation. Table 1 p4 only establishes per-row propagated uncertainties via quadrature, p3 measurement propagation, and p8 reports the aggregate without its construction; the archived average-uncertainty assertion still exceeds independently established source support. |
| export.figure_value_page_binding | qualified | 7, 8 | source_identity.page=8 correctly locates Equation 5 and the value, but source_identity.figure also names Figure 3, which is on p7. No separate figure_page is supplied; this is an ambiguous combined locator and must not be described as Figure 3 on p8. |
| export.authorship_semantics | fail | not applicable | This is an automated software UI operation record per task provenance and export notes, yet source_reading_conclusion.authorship says explicit user choice. That field cannot establish human/participant/expert authorship and is misleading for this automated record. |
| export.scientific_evidence_status | qualified | not applicable | The UI export demonstrates a saved software choice and hash integrity. The current source review is separate from the export’s own conclusion. |

All input artifacts were left unchanged. Exact workbook values were not independently verified. Rendered page images are retained beside this report.
