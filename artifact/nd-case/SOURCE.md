# Nd case: source and attribution

The two observation tuples are curated for the demo artifact from the authors' article and workbook. “Author-curated” denotes prepared demo content, not a claim that the original study authors reviewed this artifact.

**Article:** Mark Nestmeyer, Alex J. McCoy-West, Yuan Mei, and Kevin W. Burton (2026), *The stable isotope fractionation of neodymium on ferrihydrite revisited*, Advances in Geochemistry and Cosmochemistry 2(1):1099. [Article DOI](https://doi.org/10.33063/agc.v2i1.1099). Copyright the authors, 2026. Page 1 explicitly identifies **CC BY 4.0**; [license](https://creativecommons.org/licenses/by/4.0/). `source/article.pdf` is an unchanged copy. `source/page8-check.png` is a rendered page for verification. The JSON is a curated representation, not a modification of the publication.

**Dataset:** [10.25903/tfqa-tt39](https://doi.org/10.25903/tfqa-tt39), identified by article page 1. Local archived author workbook `Data_Nd_Fhy.xlsx`, sheet `Table1`, was read directly with cached numeric values and formulas. Its redistribution license was not established because the dataset landing page was unavailable during this check. The workbook is therefore **not copied**. Only the requested factual values/cell references are represented. SHA256 and the inspected cells appear in `verification-record.json`.

## Exact scope and provenance

- **Time-series offset:** paper p7, §3.2; Δ146Nd solid minus liquid = −0.098 ± 0.012 ‰ over 24–168 h. The text explicitly calls this a 95% confidence interval for the mean. The workbook has three ≥24 h time-series observations at 24, 48, and 168 h (`B9:B11`), not four sampling times. The aggregate and CI are quoted from the paper, not recomputed from individual-row errors.
- **Variable-loading factor:** paper p8, §3.2 and Eq.5; 0.5–2.5 mL stock, 48–144 h. The paper reports average 10³lnα = −0.113 ± 0.030 and calls it equal to Δ146Nd solid minus liquid. We preserve that original representation. The aggregate uncertainty method is not asserted; workbook column L's per-row propagated error does not establish the aggregate uncertainty construction.
- **Shared methods:** paper p3, §2.1 confirms pH 5.5 and 0.01 mol/L NaCl, 50 mL final experiment volume. These refer to experiment conditions, not the pH used to synthesize ferrihydrite.
- **Uptake:** author workbook `Table1!D9`, `D10`, `D11` = 17.689740240893922%, 28.096093519586024%, 47.59157120633573%, with durations at `B9:B11` = 24, 48, 168 h. Figure 2c is on paper p7. Rounded article prose uses 18%, 28%, and 48%.
- **Digitization:** archived candidate associations are separate from workbook observations. They retain their original time-only matching rule, `CANDIDATE_ASSOCIATION_NEEDS_REVIEW` status. They are neither independent samples nor verified experiment identity matches.

Local `curated:*` tuple aliases identify the prepared observation layer. The composite parent reference is recorded separately. Known paper context is `W7165146192`, Claim 02 with composite scope. The recorded loading-series receipt supplies the actual parent claim/snapshot and the observation/anchor on page 8. The PDF SHA-256 equals the returned artifact-version suffix. The time-series observation remains page-curated. See production-link.json. Two curated tuples linking one PDF do not mean that the production graph already contains two corresponding split claims.

The verification record retains direct pypdf readings of the relevant CC BY pages and selected openpyxl cell readings. Page 8 was also rendered and visually inspected to confirm the variable-series range and reported 10³lnα value. No private keys, production database, or unrelated papers are included.
