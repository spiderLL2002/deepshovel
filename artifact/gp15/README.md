# GP15 research case: public data, fixed protocol and executable claim checks

This artifact reproduces the GP15 research case used in the DeepShovel demonstration: a fixed published REE–pH implementation, an unchanged depth reference, the exact matched samples, four primary RMSE values and the six squared-error contrasts supporting claim C12. It preserves an existing research package rather than fitting a new model for the demo. The scientific programs execute outside the DeepShovel service. Their successful execution establishes numerical reproducibility of this case; it is not a measurement of retrieval precision, extraction accuracy or semantic entailment.

## Run the claim checks

From this directory, use Python 3.12:

```sh
python3.12 -B fetch_external_sources.py
python3.12 -B verify_demo.py
```

The first command restores two supporting inputs directly from their original providers and checks their frozen SHA-256 values. Existing matching inputs are reused without network access. The second command uses only the Python standard library and runs offline. It verifies the preserved package, verifies the nine file bindings for C01/C02/C03/C12, creates a disposable copy, deletes the two old checker receipts in that copy, and executes the unchanged raw-source and exact-contrast checkers. It then prints newly computed RMSEs, matched support, six exact rational contrasts and the C12 argument. It never treats a stored PASS receipt as a new execution.

The quick command recomputes and checks the core numbers against preserved reference outputs. To regenerate all analyses, sensitivities and publication figures, run the complete ten-stage workflow with the new portable comparison wrapper:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r reproduction/requirements-research.lock
.venv/bin/python -B run_portable.py
```

The reference environment is Python 3.12.13, NumPy 2.5.4 and Matplotlib 3.10.7. All 12 locked packages have downloadable Linux CPython 3.12 wheels; several require glibc 2.27 or later, supported by Ubuntu 22.04/24.04. The portable wrapper checks every installed version against the unchanged lock, then reuses the original executor's ten stage commands, fresh-output deletion rules, independent-checker expectations and scientific-result inspections. It writes new outputs in a temporary tree. `--out NEW_DIRECTORY` chooses a new directory outside `artifact/gp15`; existing directories are rejected. It records each stage log and `portable-verification.json`, with all 76 comparisons and any expected/actual metadata differences. The original input and output files are never rewritten to obtain a PASS.

The allowed direct JSON differences are four exact runtime fields: `review-tables-v1/source_manifest.json:python_version`, `continuous-box-v1/source_manifest.json:python_version`, `selection-weight-domain-v1/input_manifest.json:interpreter`, and `figures/final_v1/figure-source-manifest.json:environment.python`. Their new values must match the interpreter executing the run. Three exact output-manifest entries may consequently have different hashes/lengths, but only after the referenced file passes the runtime-only comparison and both reference/fresh hashes and lengths match the actual files. The PNG checks below additionally allow only the six PNGs' declared hash/length entries in the original run manifest and final figure manifest, after their image checks pass. The report lists every changed field with its expected and actual value. The wrapper reconstructs expected bytes with those specific replacements; extra fields, formatting changes or any other changed value fail. It does not apply scientific numeric tolerances, remove arbitrary JSON fields or mask scientific differences.

Serialization is exact for each declared file: the original `results/ree_ph_fixed_transfer_v1/run_manifest.json` uses `json.dumps(..., indent=2)` with no terminal newline, while the other whitelisted manifests retain their declared newline. The wrapper reproduces that one file's original format explicitly; it does not trim whitespace or infer a relaxed format from changed output. Regression tests retain the real 1,861-byte reference and reject both added newlines and altered scientific fields.

CSV, scientific JSON, Markdown and every SVG/PDF remain subject to exact byte comparison. Six explicitly named PNGs have a separate decoded-pixel policy. `results/ree_ph_fixed_transfer_v1/ree_ph_transfer.png` must have exactly identical decoded RGBA pixels. The five final PNGs (`fig01_sampling_support`, `fig02_sampled_layer_means`, `fig03_error_decomposition`, `figS01_fixed_sensitivity`, `figS02_calibration_domain`, all under `iteration-2026-10-27/figures/final_v1/`) must each have **both a byte-identical SVG and a byte-identical PDF**, identical dimensions, mode, PNG format, frame count and metadata, maximum RGBA channel difference at most **2 out of 255**, and changed-pixel ratio at most **0.0001**. The ratio limit is checked using exact integer counts. Different PNG compression alone can pass when decoded pixels and metadata match. Other PNG paths have no exception.

This narrowly scoped rule follows the first Linux execution: four PNGs had exactly identical RGBA pixels despite differing compressed bytes; Figure 1 differed at 76 pixels by at most 1/255, and Figure S2 at 447 pixels by at most 2/255. All vector figures and scientific data were byte-identical. These observed raster differences are reported rather than recoded or erased. Every subsequent run must independently satisfy the fixed thresholds and vector checks; the first run's outcome does not authorize arbitrary image changes.

The report records PNG dimensions, modes, complete metadata, RGBA hashes, differing-pixel counts/ratios, maximum channel differences and the two vector hashes. It separately counts byte-identical files, runtime-only changes, PNGs with identical pixels, bounded PNG raster differences and verified dependent manifest changes. `BYTE_IDENTICAL` means all 76 files match exactly. `SCIENTIFIC_DATA_AND_VECTORS_BYTE_IDENTICAL_WITH_DECLARED_RUNTIME_AND_PNG_DIFFERENCES` means the scientific data and vectors match while the explicitly reported runtime/PNG differences passed these rules. A failed run records its differences and exits nonzero. No PNG or generated output is rewritten by the wrapper.

The original strict entry point remains available:

```sh
.venv/bin/python -B reproduction/run_reproduction.py
```

It permits no runtime or PNG exceptions and therefore requires the original runtime/build for identical metadata and encoded image bytes. See the [preserved package README](reproduction/README.md) for the ten scientific stages. The new wrapper's 19 focused tests can be run with `.venv/bin/python -B test_portable.py`; their controlled mutations test rejection of changes, not scientific or system performance. Tests cover PNG compression equivalence, both pixel thresholds, changed SVG/PDF, changed metadata, forged hashes, altered scientific fields and the real primary manifest's exact serialization.

## Actual execution and expected core results

Both commands were executed for this artifact on 2026-10-29 with Python 3.12.13. The complete workflow passed all ten stages and all 76 byte comparisons. The quick command passed both independent numerical checkers: 15,276 numeric, 5,034 identity/status and 962 audit-row checks in the original checker, plus the complete 12,526-category-check total in the selection/weight checker. Counts describe checks performed by research programs; they are not additional scientific observations or system-level semantic accuracy measures.

The historical machine-readable records are [demo-verification.json](demo-verification.json) and [full-reproduction-verification.json](full-reproduction-verification.json). The latter retains all fresh comparison results and the SHA of its original run receipt, with only the local temporary path removed. These original records retain the package hash that existed at their execution; the public URL-query projection below changes the current package manifest. Running the commands verifies the current package and generates a new outcome. Historical PASS records are not rewritten to claim verification of later package bytes.

After that public projection, the first portable wrapper was executed on the reference macOS runtime: [that execution record](verification-2026-10-29/portable-verification.json) reports all ten stages and 76 comparisons passing with `BYTE_IDENTICAL`, zero runtime exceptions and no rewritten generated outputs. Its wrapper SHA and v1 schema identify the earlier runtime-only policy. Later executions record their own wrapper SHA, v2 comparison policy and actual runtime/PNG differences; historical receipts remain unchanged.

The [v2 wrapper's reference-runtime execution](verification-2026-10-29-png-policy/portable-verification.json) also passes all ten stages and 76 comparisons with `BYTE_IDENTICAL`. It verifies the added PNG policy without claiming the outcome of a subsequent Linux run; that CI run produces its own report.

|Primary result|Leg 1|Leg 2|
|---|---:|---:|
|Fixed model RMSE|0.07021355937856119|0.21827126240140987|
|Fixed depth-reference RMSE|0.17008381101696987|0.11029509536786561|
|Model minus reference RMSE|−0.09987025163840868|+0.10797616703354426|
|Unique parent bottle IDs|24|55|
|Layer memberships|28|63|
|Layer counts, shallow to deep|5, 6, 6, 3, 5, 3|16, 14, 17, 7, 5, 4|

RMSE is the square root of the mean of six squared **layer-mean** errors. It is not bottle-level RMSE. Primary layers use the inclusive intervals 200–300, 300–400, 400–500, 500–600, 600–700 and 700–800 m, with the pH table's reported sampling depth. A sample exactly on an internal boundary contributes to both adjacent layers, explaining why membership counts exceed unique bottle counts. Half-open bins, REE-reported depths and equal station weights are retained as sensitivities.

For Leg 2, define `d[k] = model_error[k]^2 - reference_error[k]^2` for the unchanged six primary layer means. The fresh exact-contrast checker yields:

|Depth interval (m)|d[k] (pH units squared)|
|---|---:|
|200–300|0.07284603963469431|
|300–400|0.053675103676973125|
|400–500|0.053196753808448675|
|500–600|0.020764537693458113|
|600–700|0.00693500760912255|
|700–800|0.005446573145896173|

All six contrasts are strictly positive. Thus, for any nonnegative weights summing to one **on these same six unchanged means**, `MSE_model - MSE_reference = sum(w[k] * d[k]) >= min(d) > 0`; the increasing square-root function preserves the RMSE ordering. This is C12. Exact fractions are in the fresh JSON. It does not cover changing samples within a layer, geographical weighting, simultaneous REE perturbations or all possible calibration uncertainty. Leg 1 has two positive and four negative contrasts and does not have this weight-invariant ordering.

## Exact model and fixed comparator

The [frozen protocol](reproduction/payload/ree/round8/protocol-frozen-v1.json) binds the source-manifest and analysis-code hashes and specifies matching, flags, depth conventions, weights, constants and sensitivities. It was frozen at `2026-10-25T15:57:54.693118+00:00`. Its presence records a frozen protocol; claims about response exposure additionally depend on the preserved exposure/acquisition record.

For `Sm, Gd, Dy, Er`, the released procedure normalizes concentrations by the original PAAS constants and molar masses, computes the ordinary least-squares slope `alpha` against atomic numbers `62, 64, 66, 68`, then uses:

```text
pH = (alpha - 3.339192211653703e-07) / (-3.9879452461785675e-08)
```

The raw REE concentrations are in pmol/kg. Multiplication by `1e-15` converts them to mol/g; the PAAS conversion is `PAAS_ug_per_g * 1e-6 / (molar_mass_mg_per_mol * 1e-3)`. The code, original coefficient CSVs and author implementation are preserved. Neither coefficients nor comparator values are fitted to GP15.

The six fixed depth-reference pH values, in the interval order above, are:

```text
7.73087871249336, 7.6898707001764945, 7.658636222030023,
7.6315530378663246, 7.606188333846088, 7.584325607003087
```

These are the original global calibration-layer pH means saved in the author's notebook. The original calibration relates separately averaged global REE and pH data; it is not a set of same-bottle calibration pairs. The comparator uses the same target means and layer weights as the transferred model. Arithmetic means are computed on the reported pH scale; no hydrogen-ion mixing calculation is claimed.

## pH scale, temperature, pressure and sample identity

The GP15 target is **total-scale pH measured at 25°C and ambient pressure**, not in-situ pH at the sample's ocean depth. Both BCO-DMO pH dataset methods describe a 25°C bath and measurement at 25 ± 0.1°C, and their attached intercalibration reports specify the total scale: [Leg 1, version 2](https://www.bco-dmo.org/dataset/838157) and [Leg 2, version 2](https://www.bco-dmo.org/dataset/838173).

The original proxy code explicitly selects GLODAPv2.2022 `G2phts25p0`, whose official definition is **total-scale pH at 25°C and 0 dbar (surface pressure)**. The depth reference is derived from the same field. Therefore the target, calibration response and depth reference share the reported scale/temperature/pressure basis; no additional temperature or pressure conversion was applied or required to align those stated bases. The field definition is in Table 1 of the [GLODAPv2.2022 data paper](https://essd.copernicus.org/articles/14/5543/2022/), with the release identified by [NOAA](https://www.ncei.noaa.gov/access/ocean-carbon-acidification-data-system/oceans/GLODAPv2_2022/). This alignment does not remove laboratory offsets or turn the comparison into an in-situ speciation test. The original code selects nonmissing product pH rather than measured-only pH; the exact calculated-pH fraction of its selected calibration subset was not reconstructed.

Joins require exact parent sample ID and matching station/event, finite responses, allowed bottle quality flags and the prescribed depth rules. pH was measured in an unfiltered aliquot and REE in a 0.2 µm filtered aliquot from the same parent Go-Flo bottle. The match therefore establishes parent bottle identity, not identical analytical aliquots. One source depth conflict remains explicit: sample 14984 is listed at 360 m in the REE table and 261 m in the pH table. The primary convention uses the latter; the alternative-depth sensitivity is retained without adjudicating the conflicting metadata.

## Files and claim tracing

- [claim-source-map.json](claim-source-map.json) selects C01 (fixed implementation), C02 (matched support), C03 (four RMSEs) and C12 from the final research map. It retains original file hashes and adds resolving export paths; nine file bindings are checked by `verify_demo.py`.
- [Scientific manuscript](scientific-manuscript.en.md) and [scientific supplement](scientific-supplementary.en.md) are the unchanged p20 texts identified by that source map.
- [transfer-source-input.json](reproduction/payload/ree/round8/transfer-source-input.json) binds the 17 original input/supporting files to source URLs, SHA-256 values, roles and locators.
- [pairing_audit.csv](reproduction/payload/results/ree_ph_fixed_transfer_v1/pairing_audit.csv), [sample_predictions.csv](reproduction/payload/results/ree_ph_fixed_transfer_v1/sample_predictions.csv) and [layer_summary.csv](reproduction/payload/results/ree_ph_fixed_transfer_v1/layer_summary.csv) expose selection decisions, sample IDs and calculations.
- [layer_squared_error_difference.csv](reproduction/payload/iteration-2026-10-27/analysis/selection-weight-domain-v1/layer_squared_error_difference.csv) supplies the C12 contrasts; the preserved checker reconstructs their signs with exact rational arithmetic.
- [table-source-map-v2.public.json](reproduction/payload/iteration-2026-10-27/reproducibility/public-lineage/table-source-map-v2.public.json) resolves the larger research table/method index and records unavailable historical context explicitly.
- [publication-projections.json](publication-projections.json) records one non-analytical receipt's exact public redaction: query strings removed from `receipts[0..2].resolved_url` in the blind-acquisition record. It binds the historical source SHA to the public projection SHA and identifies every changed field. The historical original is not distributed or offered for download; stable BCO-DMO source URLs supply the data. The original protocol, source-input manifest and export plan retain their historical original-receipt SHA as an identity reference. The current package manifest explicitly verifies the public projection instead. No scientific source input, protocol, algorithm or result changed.

This demo index is assembled from existing research outputs. It does not assert that the service automatically ingested every historical computation or that matching SHA values establish semantic support. The causal role of the service, external agent and numerical programs must be read from the accompanying research trace.

## Attribution and redistribution scope

The four original measurement CSVs are retained unchanged under the dataset providers' CC BY 4.0 terms, with method reports and versioned identifiers:

|Content|Creators / source|Versioned identifier|
|---|---|---|
|GP15 Leg 1 REE|Alan M. Shiller and Melissa Gilbert, BCO-DMO|[10.26008/1912/bco-dmo.932161.1](https://doi.org/10.26008/1912/bco-dmo.932161.1)|
|GP15 Leg 2 REE|Alan M. Shiller and Melissa Gilbert, BCO-DMO|[10.26008/1912/bco-dmo.932559.1](https://doi.org/10.26008/1912/bco-dmo.932559.1)|
|GP15 Leg 1 pH|Gregory Cutter and Nicole Buckley, BCO-DMO|[10.26008/1912/bco-dmo.838157.2](https://doi.org/10.26008/1912/bco-dmo.838157.2)|
|GP15 Leg 2 pH|Gregory Cutter and Nicole Buckley, BCO-DMO|[10.26008/1912/bco-dmo.838173.2](https://doi.org/10.26008/1912/bco-dmo.838173.2)|

Credit Ping-Chun Lin and David C. Catling (2024), [10.2475/001c.118215](https://doi.org/10.2475/001c.118215), for the original model. Author software is pinned to repository commit `99ebf142d6787361c7720b7a3e7c213fd213eddb`, with its [unchanged MIT notice](reproduction/payload/ree/round8/raw/Lin2024-LICENSE). The article states CC BY 4.0 on PDF page 17; its unchanged PDF/text and source identity are included. Cite the original source and preserve the applicable notices when reusing these materials.

Two supporting items are **downloaded from their providers instead of committed**: the author's compiled `REE_seawater_all_pmol.csv` and the GLODAPv2.2022 `Dataset_DOIs.csv` catalogue. The software MIT notice is not used to assign a blanket license to all upstream compiled measurements. `fetch_external_sources.py` obtains these exact source bytes, verifies the frozen hashes and restores their required paths; `.gitignore` excludes the local copies. The full GLODAP merged data product is not distributed here. The source manifest retains the original URLs and attribution.

Publisher response bodies, private service sessions, authentication material and external full texts without an established redistribution basis remain excluded. Historical research files named “review” or “independent” are preserved numerical research checkers and records, not new anonymous peer-review decisions about this WWW submission. The two cruise legs share laboratory/cruise dependencies; the finite matched comparison and its diagnostics do not establish globally representative performance or a physical cause of the residuals.
