# New AI source audit: F2 and Nd prepared observations

Original inputs were not changed. No previous source-task-check or paper-evaluation report was consulted. Local PDFs were read before target JSON. Page numbers below are physical PDF pages (1-based).

## Findings

The prepared F2 scientific reading is supported as an author-reported analytical estimate. Its retained p.11 anchor box is wrong: the box is in the left column, while the F2 sentence is in the right lower column. Page/section locators remain correct. The retained scalar numeric=55 needs explicit lower-bound handling; it does not represent the complete 55–57 summary. Neither the summary nor modeled estimates are verified operating safety limits.

Both Nd prepared aggregate tuples preserve the reported quantities, values, distinct experiment series, times, and conditions. The time-series ±0.012 is expressly a mean confidence interval; the loading-series ±0.030 construction is not established from the paper and the export correctly declines to infer it. pH 5.5 is an adjusted target, and equilibrium is isotopic rather than complete elemental equilibration. Workbook A1 references and full-precision uptake/candidate provenance are unassessable from the PDFs; rounded uptake values are supported.

The status counts below are field counts, not a quality score: {"supported": 31, "unsupported": 1, "requires_qualification": 1, "unassessable": 5}.

## Field-by-field evidence

### 1. F2 prepared observation — value and unit

Status: **supported**

Exported: 55–57 MPa

PDF pages: 11, 13. Source evidence: Section 6, right column below Fig. 12: F2 can undergo an average maximum pore pressure of 55–57 MPa. Section 8 repeats maximum pore pressure of 55–57 MPa.

Exact author-reported summary, not a newly calculated interval.

### 2. F2 prepared observation — quantity definition

Status: **supported**

Exported: Maximum sustainable pore pressure, author-reported modeled summary

PDF pages: 9, 10, 11. Source evidence: Section 5 defines maximum sustainable pore pressure Ppmax separately from the pore pressure increase required to cause failure. Table 3 F2: Ppi 22.04; Ppmax 57.4/57.3; ΔPp 35.4/35.3 MPa.

Correctly names absolute pore pressure; not increment and not an observed safe operating pressure.

### 3. F2 prepared observation — experiment/model context

Status: **supported**

Exported: Modeled F2 fault reactivation during gas injection, Asmari reservoir

PDF pages: 3, 9, 13. Source evidence: Methodology uses well logs to estimate stress; Section 5 plots Mohr diagrams; Section 8 estimates critical pore pressure due to gas injection for faults in Asmari reservoir.

Analytical/model estimate is faithful; no experimental validation claimed.

### 4. F2 prepared observation — conditions

Status: **supported**

Exported: F2; Asmari; Gachsaran SW Iran; zero cohesion; friction 0.6; three analytical methods

PDF pages: 1, 3, 4, 11, 13. Source evidence: p.3: faults were considered cohesionless (C=0), friction can be set to 0.6 absent data; Table 1 p.4 F2 C=0, μ=0.6. p.11: principal stresses taken within a specified range (e.g. 10 m) of middle part of fault planes.

Listed conditions supported. C=0 and μ=0.6 are assumptions, not measured fault properties; modeled context preserves that meaning. Mid-fault sampling detail remains an important reuse limit.

### 5. F2 prepared observation — time

Status: **unassessable**

Exported: Recorded scope time=null; prepared field absent

PDF pages: 11, 13. Source evidence: Source describes current stress field and pore pressure, without a dated time-series observation attached to the 55–57 summary.

No numerical calendar time or injection duration can be established for this value; absence is not a contradiction.

### 6. F2 prepared observation — uncertainty explanation

Status: **supported**

Exported: Author-reported modeled range; Table 4 separately lists formulation-specific Pc and PCr

PDF pages: 11. Source evidence: Table 4 F2, equation 3/5: Pc=55.03/55.31 MPa; PCr=57.58/57.81 MPa. Narrative summary=55–57 MPa.

Faithful descriptive label. It must not become a confidence interval, measurement error, or exact min/max envelope: 57.81 exceeds 57.

### 7. F2 prepared observation — page/section/table locator

Status: **supported**

Exported: PDF p.11 Section 6 and Table 4; conclusions p.13

PDF pages: 11, 13. Source evidence: Verified complete page renders show Section 6, Table 4 on p.11 and Section 8 on p.13.

Prepared textual locator is correct.

### 8. F2 prepared observation — qualified reading scientific content

Status: **supported**

Exported: Preserve 55–57 author summary; distinguish Pc from PCr; not pressure increment

PDF pages: 10, 11, 13. Source evidence: Table 3 distinguishes Ppmax from ΔPp; Table 4 has the exact quoted Pc/PCr values.

No scientific overreach in prepared notes when confined to modeled F2 in this study. The source does not prove safety of actual injection operations.

### 9. F2 prepared observation — qualified reading procedural claims

Status: **unassessable**

Exported: Automated UI verification; source-checked entries; generic full-graph adapter union

PDF pages: 11, 13. Source evidence: These are process claims outside the article.

This source audit cannot certify UI execution or adapter behavior and does not adopt that wording as evidence.

### 10. F2 prepared observation — recorded numeric scalar

Status: **requires_qualification**

Exported: recorded_observations[obs.f2.assessed].value.numeric=55; raw=55–57 MPa

PDF pages: 11, 13. Source evidence: Source gives the interval-like author summary 55–57, not a single point estimate 55.

Raw text is faithful; numeric=55 is only the lower bound. Consumers must not use it as the whole result or mean. Consider explicit low/high representation.

### 11. F2 prepared observation — recorded text anchor quote

Status: **supported**

Exported: anchor.text.f2.stability quote and anchor.text.methods.all quote

PDF pages: 11, 13. Source evidence: p.11 right lower paragraph contains the most stable/lowest Ts and Tsm/55–57 sentence. p.13 left conclusion contains all three methods/F2/55–57.

Quotes match content, disregarding OCR typography.

### 12. F2 prepared observation — recorded p.11 anchor region

Status: **unsupported**

Exported: x=.0789916 y=.7987342 width=.410084 height=.111392; normalized

PDF pages: 11. Source evidence: Full-page render: target sentence is in right column, approximately x=.51–.92, y=.65–.69. Exported box occupies left column x=.079–.489, y=.799–.910.

Box does not enclose F2 target quote under usual top-left normalized coordinates. This is a region-locator error, not a wrong-page or wrong-value error. Approximate replacement is not a precision-tested box.

### 13. F2 prepared observation — recorded p.13 anchor region

Status: **supported**

Exported: x=.0789916 y=.2113925 width=.410084 height=.539241; normalized

PDF pages: 13. Source evidence: Full-page render shows the target conclusion in the left column around y=.53–.59, enclosed by the supplied region.

Region is broad but contains quote; not a tight sentence box.

### 14. Nd time-series plateau — material

Status: **supported**

Exported: 2-line ferrihydrite

PDF pages: 2, 7. Source evidence: p.2 describes synthesis of 2-line ferrihydrite; p.7 Section 3.1 confirms characteristic XRD humps of 2-line ferrihydrite.

Applies to laboratory adsorption experiments, not seawater or a field isotope measurement.

### 15. Nd time-series plateau — conditions

Status: **supported**

Exported: pH 5.5; NaCl 0.01 mol/L; volume 50 mL

PDF pages: 3. Source evidence: Section 2.1: final NaCl concentration 0.01 mol/L and volume 50 mL; pH adjusted to 5.5. For runs longer than 24 h pH adjusted daily back to 5.5.

Numbers supported; pH 5.5 is an adjusted target, not evidence of uninterrupted constant pH. Do not infer temperature from the separate molecular dynamics simulation.

### 16. Nd time-series plateau — methods locator

Status: **supported**

Exported: p.3 Section 2.1; definition p.3 where supplied

PDF pages: 2, 3. Source evidence: Adsorption methods begin on p.2 and continue on p.3, where concentration/volume/pH and isotope definitions occur.

p.3 is correct for listed aqueous conditions; material synthesis itself is on p.2.

### 17. Nd variable-loading factor — material

Status: **supported**

Exported: 2-line ferrihydrite

PDF pages: 2, 7. Source evidence: p.2 describes synthesis of 2-line ferrihydrite; p.7 Section 3.1 confirms characteristic XRD humps of 2-line ferrihydrite.

Applies to laboratory adsorption experiments, not seawater or a field isotope measurement.

### 18. Nd variable-loading factor — conditions

Status: **supported**

Exported: pH 5.5; NaCl 0.01 mol/L; volume 50 mL

PDF pages: 3. Source evidence: Section 2.1: final NaCl concentration 0.01 mol/L and volume 50 mL; pH adjusted to 5.5. For runs longer than 24 h pH adjusted daily back to 5.5.

Numbers supported; pH 5.5 is an adjusted target, not evidence of uninterrupted constant pH. Do not infer temperature from the separate molecular dynamics simulation.

### 19. Nd variable-loading factor — methods locator

Status: **supported**

Exported: p.3 Section 2.1; definition p.3 where supplied

PDF pages: 2, 3. Source evidence: Adsorption methods begin on p.2 and continue on p.3, where concentration/volume/pH and isotope definitions occur.

p.3 is correct for listed aqueous conditions; material synthesis itself is on p.2.

### 20. Nd time-series plateau — value/unit

Status: **supported**

Exported: −0.098 ±0.012 ‰

PDF pages: 7. Source evidence: Section 3.2: experiments with duration ≥24 h give −0.098 ±0.012 ‰; Fig. 2b labels same value.

Exact aggregate as reported, not an individual table row.

### 21. Nd time-series plateau — quantity definition

Status: **supported**

Exported: Δ146NdSol−Liq = δ146NdSol − δ146NdLiq

PDF pages: 3, 7. Source evidence: Equation 2 defines solid-minus-liquid difference; Equation 1 defines δ relative to JNdi-1 in per mille.

Sign means solid lower in δ than liquid; no inversion.

### 22. Nd time-series plateau — experiment context

Status: **supported**

Exported: Time series: isotopic plateau subset

PDF pages: 3, 4, 7. Source evidence: Methods p.3: identical input experiments at variable duration 0.5–168 h; Table 1 separates Time series from Variable adsorption in equilibrium. p.7 takes ≥24 h subset.

Correct subset, not pooled with variable-loading experiments.

### 23. Nd time-series plateau — time and loading

Status: **supported**

Exported: 24–168 h; observed 24,48,168 h; stock 1 mL

PDF pages: 4, 7. Source evidence: Table 1 time-series experiments 6,7,9: durations 24,48,168 h, each Fhy stock 1.00 mL.

Duration endpoints describe observed subset span, not continuous measurements at all intervening times.

### 24. Nd time-series plateau — uncertainty explanation

Status: **supported**

Exported: 95% confidence interval for mean, explicitly stated by authors

PDF pages: 7. Source evidence: Section 3.2 calls it a 95% confidence interval (i.e. how well constrained the mean value); figure/caption also use 95% se interval.

Source explicitly supports CI-for-mean reading. Not 2×SD of replicate analyses or propagated per-row errors in Table 1.

### 25. Nd time-series plateau — article locator

Status: **supported**

Exported: p.7 Section 3.2/Fig.2; Table 1 p.4; definition p.3

PDF pages: 3, 4, 7. Source evidence: Verified page renders and text locate value and rows at these pages.

Correct PDF page locators.

### 26. Nd time-series plateau — workbook/table cell locator

Status: **unassessable**

Exported: Table1!B9:D11, K9:L11; table cells B9:C11

PDF pages: 4. Source evidence: PDF Table 1 uses experiment numbers/column headings, not spreadsheet A1 cells.

PDF confirms corresponding experiment rows 6,7,9 but cannot establish workbook sheet coordinates. Workbook not supplied/read in this scoped audit.

### 27. Nd time-series plateau — qualified reading

Status: **supported**

Exported: Stable isotopic offset coexists with increasing sorbed fraction

PDF pages: 7, 8. Source evidence: p.8: constant offset after 24 h despite ongoing Nd uptake demonstrates elemental equilibration occurred more slowly. Table 1 uptake rises 17.69→28.10→47.59%.

Supported coexistence for this experiment; does not establish universal decoupling or complete elemental equilibrium.

### 28. Nd variable-loading factor — value/unit

Status: **supported**

Exported: −0.113 ±0.030; dimensionless scaled logarithmic factor

PDF pages: 8. Source evidence: Section 3.2 after Eq.5: average α=0.999886±0.000030, corresponding average 10³ ln α=−0.113±0.030.

Correct logarithmic representation, kept distinct from per-mille δ notation.

### 29. Nd variable-loading factor — quantity definition

Status: **supported**

Exported: 10³ ln αSolid−Liquid; α=(1000+δSolid)/(1000+δLiquid)

PDF pages: 8. Source evidence: Equation 5 gives exact ratio of 1000+δ terms.

Correct orientation and scaling.

### 30. Nd variable-loading factor — authors equivalence

Status: **supported**

Exported: Authors state equal to Δ146NdSol−Liq; retain original representation

PDF pages: 8. Source evidence: Text after Eq.5 says this value is equal to Δ146NdSol−Liq.

Faithful attributed statement. Algebraically log factor and δ difference are not generally identical; do not promote this sentence to a universal exact identity.

### 31. Nd variable-loading factor — experiment context

Status: **supported**

Exported: Variable ferrihydrite loading in equilibrium

PDF pages: 3, 4, 8. Source evidence: p.8 second experiment set varies stock amount to obtain variable sorbed Nd; experiments ran ≥48 h and formed parallel trends interpreted as isotopic equilibrium.

Correct series; the source itself notes incomplete adsorption means open exchange model cannot be confirmed.

### 32. Nd variable-loading factor — time and loading

Status: **supported**

Exported: 48–144 h; observed 48,96,144 h; stock 0.5–2.5 mL

PDF pages: 4, 8. Source evidence: Table 1 experiments 10–18 list 48,96,144 h and 0.50–2.50 mL; p.8 text repeats ranges.

Supported jointly; stock volume is not a universal concentration or fraction adsorbed.

### 33. Nd variable-loading factor — uncertainty explanation

Status: **supported**

Exported: ±0.030 as reported; aggregate construction not established here

PDF pages: 4, 8. Source evidence: p.8 supplies ±0.030 without a named CI/SD construction there. p.4 footnotes describe replicate 2×SD and row-level propagated uncertainty.

Conservative explanation correct. PDF does not justify assigning the aggregate ±0.030 a 95% CI or row propagated-error meaning.

### 34. Nd variable-loading factor — article locator

Status: **supported**

Exported: p.8 Section 3.2 Eq.5; Table1 p.4

PDF pages: 4, 8. Source evidence: Full p.8 render shows value beneath Eq.5, under Section 3.2 continued from p.7.

Correct even though Section 3.2 heading appears on preceding page.

### 35. Nd variable-loading factor — workbook/table cell locator

Status: **unassessable**

Exported: Table1!B13:D21, E13:L21; table cells B13:C21

PDF pages: 4. Source evidence: PDF Table1 experiments 10–18 corresponds in scientific content but has no A1 labels.

Workbook coordinates and underlying precision cannot be independently certified from PDF.

### 36. Nd variable-loading factor — qualified reading

Status: **supported**

Exported: Loading aggregate kept distinct from time-series plateau; no independent human labels

PDF pages: 4, 7, 8. Source evidence: Separate table groups and distinct aggregate statements on pp.7–8. p.8 says open exchange model cannot be confirmed.

Scientific separation faithful. Human-label status is a provenance statement outside PDF; this audit adds only AI review.

### 37. Nd ancillary uptake — rounded values and experiment identities

Status: **supported**

Exported: 24 h expt6 ~17.69%; 48 h expt7 ~28.10%; 168 h expt9 ~47.59%

PDF pages: 4, 7, 8. Source evidence: Table1 exact displayed percentages 17.69,28.10,47.59, with times 24,48,168 h. Figure2c shows same increasing uptake.

Exported full-precision values round correctly to article values.

### 38. Nd ancillary uptake — full precision/workbook/digitized identity

Status: **unassessable**

Exported: 17.689740240893922,28.096093519586024,47.59157120633573; cells/pixels/candidates

PDF pages: 4, 7. Source evidence: Article contains rounded table values and Fig.2c; no workbook cached values, digitization pixel coordinates or association algorithm.

PDF cannot certify all decimals, spreadsheet cell identity, or digitization provenance. These remain candidates, as export already states.

## Input integrity and method

Independently read local source PDFs before inspecting target JSON; no prior source-task-check or paper-evaluation reports read. Text extraction plus full-page visual inspection. No input files edited.

Full-page visual inspection: F2 pp.11,13; Nd pp.4,7,8. The remaining relevant methodology/definition pages were read as complete extracted text. No aggregate or uncertainty was reverse-engineered from rounded rows. A1 references are not treated as printed PDF cell locators. Procedural verification and production snapshot claims are not certified by source-paper evidence.

- `second-case/adapter-reuse-ui-export-v14.json` SHA-256 `fe7f6ff55691c4e29bc06893063a06dcd067b4ba7107d190c545ecbc2aa5ebd5`
- `second-case/source.pdf` SHA-256 `f87d7cdcf0d08a7557993c1966b56183882c0b87c68809d0519ea8060382de99`
- `nd-case/case.json` SHA-256 `e97584bd96e19075ddc7cc9736592279083be332c8eaa98d2adfd9cdb3bfd767`
- `nd-case/source/article.pdf` SHA-256 `0bbc272cba062a9eb0131e24632ea6d8a48b86a8cdf3cda7cd44dfa9f2932e7c`

## Minimal correction list (without editing inputs)

1. Retained anchor `anchor.text.f2.stability`, `selector.region`: preserve original coordinates but add a new operator/source reading identifying p.11 right lower column. A replacement box would need precise measurement before use. Prepared textual locator “p.11 Section 6 and Table 4” is already correct.
2. Retained observation `obs.f2.assessed`, `value.numeric=55`: preserve original; ensure derivative consumers read this only as lower bound, or represent lower=55 and upper=57 while retaining raw author summary. Prepared value “55–57” does not need scientific correction.

No change to either Nd aggregate value or uncertainty label is required by this PDF-only audit. Workbook coordinates and full-precision values must remain unassessed until the workbook is separately supplied and checked; no expanded retrieval was performed.
