# R8 REE–pH: independent results and contribution review

**Recommendation: advance a narrowly framed empirical regional-transfer manuscript, subject to root's final claim/novelty gate.** The fixed-model result is reproducible and adds a new matched-observation applicability benchmark; it does not refute the pH-complexation mechanism, the original global-average relationship, or the stated coarse Precambrian reconstruction objective. This is a reanalysis of existing public measurements.

## Independent computation and provenance

The independent script reads the original four GP15 CSV files and exact author normalizers, uses a general high-precision Decimal regression, and recomputes means with fsum. It never imports or executes the frozen implementation. All source SHA values and the frozen plan/input/code are checked. Frozen outputs are read only as comparison targets. Original reference/notebook outputs are read without executing them. Only review files are written.

Checks cover all 84 eligible depth-union bottle predictions, 962 union-ID statuses, 48 layer rows, eight aggregate cases, 18 pH scenarios, 14 whole-station deletions, 32 leg-by-corner cases with 288 crossed pH scenarios and their layer errors, and both complete 1,000-draw station resampling calculations. There are 15,276 numerical comparisons and 5,034 identity/status comparisons, with zero mismatches. The maximum absolute numerical discrepancy is 2.67×10⁻¹⁵, consistent with floating-point summation. Eight fixed source spot checks preserve raw CSV lines/fields and text.

Freeze preceded joint-response computation according to root's recorded sequence (2026-10-25 15:57:54.858807 UTC; execution 15:58:00–01). This reviewer first read Leg2 sample chemistry only after explicit post-run authorization. Leg1 was exploration-exposed. Leg2 is a prospectively unexamined station block within the same cruise and laboratory system, not a separate-laboratory validation experiment.

## Recomputed result and support

| Leg / fixed variant | Model RMSE | Depth baseline RMSE | Model − baseline RMSE | Supported layers |
|---|---:|---:|---:|---:|
| leg1 / primary | 0.07021356 | 0.17008381 | -0.09987025 | 6/6 |
| leg1 / half_open | 0.07531704 | 0.16420955 | -0.08889251 | 5/6 |
| leg1 / ree_reported_depth | 0.05355735 | 0.18170037 | -0.12814302 | 6/6 |
| leg1 / station_equal | 0.07148796 | 0.17420567 | -0.10271770 | 6/6 |
| leg2 / primary | 0.21827126 | 0.11029510 | +0.10797617 | 6/6 |
| leg2 / half_open | 0.22422518 | 0.11494328 | +0.10928190 | 6/6 |
| leg2 / ree_reported_depth | 0.22017300 | 0.12030254 | +0.09987046 | 6/6 |
| leg2 / station_equal | 0.21826010 | 0.11767510 | +0.10058500 | 6/6 |

The primary Leg1 comparison uses **24 unique bottles**, 28 layer memberships and six station labels. Its 26 eligible rows are the union of the two reported-depth definitions, not 26 primary observations. Primary Leg2 uses **55 unique bottles**, 63 memberships and eight station labels, from a depth-union of 58 eligible rows. Four Leg1 and eight Leg2 boundary bottles occur in two inclusive layers. The half-open sensitivity removes that repetition and retains the Leg2 skill deficit.

Root's source-bound geographic QA (`reviews/round8-primary-station-geography.json`, SHA-256 `537205a67582507ec313114ef5bc081a21334a49d29c2c975fbbe90c9efe30c1`) identifies Leg1 primary stations from 19.681 to 53.677°N and Leg2 from 20°S to 18.906°N. The nearest cross-leg station pair, 18.3/18.6, is about 116.36 km apart. This dossier was inspected as source-identity context; it is not a new performance subgroup analysis. Distinct station coordinates do not prove statistical independence, and nearby stations may share water masses. The study must not call the legs geographically independent replicates.

Leg2 six-layer record counts are 16/14/17/7/5/4; station counts are 7/7/7/5/4/3. All six primary layers satisfy the prespecified minimum, but the deepest layer has only three station labels. Leg1 half-open has only five supported layers, so its six-layer comparison remains descriptive rather than meeting the full-domain support rule under every convention. No unsupported layer is hidden.

Leg2 model-minus-observed layer errors are +0.27209, +0.23704, +0.27215, +0.18167, +0.18934 and +0.11278 pH. Its across-layer mean bias is +0.21084. This is a consistently positive discrepancy in the published measurements, not an isolated sign chosen after inspection. The depth baseline also has positive error and is not established truth. Leg1 aggregate improvement is not a claim that every layer wins: its first two primary layers have smaller absolute baseline errors than model errors.

## Fixed robustness and uncertainty scope

| Check | Leg1 delta-RMSE range | Leg2 delta-RMSE range | Scope |
|---|---:|---:|---|
| All station deletions | -0.10804879 to -0.08817818 | +0.09632277 to +0.12209400 | Fixed support / declared scenarios |
| All 144 corner × pH cases per leg | -0.14642549 to -0.03955641 | +0.07072632 to +0.14749158 | Fixed support / declared scenarios |

leg1: independently reproduced 975/1,000 complete-support station draws; conditional delta-RMSE percentile interval -0.14237485 to -0.07818753.

leg2: independently reproduced 978/1,000 complete-support station draws; conditional delta-RMSE percentile interval +0.06060946 to +0.13169221.

All six Leg1 and eight Leg2 station-deletion cases preserve the original nonempty-layer support. Both legs retain their skill direction throughout all declared pH offsets, all enumerated REE corners, and the four aggregation variants. These are deterministic sensitivity findings, not p-values. The station percentile ranges have no established nominal confidence coverage because adjacent transect stations need not be independent or probability sampled. The 16 reference-repeatability corners do not cover every possible systematic laboratory bias, establish a continuous-box bound, or estimate sample-specific covariance. The directions cannot be averaged away as independent analytical noise.

## Depth matching and calibration-feature support

The source notebook contains six training alpha values spanning 2.564942791181893×10⁻⁸ to 3.2035971215737773×10⁻⁸ and observed training layer pH 7.5843256070–7.7308787125. These observed pH endpoints are not exactly the fitted-line pH endpoints. The independent numeric receipt records every primary layer's domain label directly from the saved notebook outputs; this is a post-run interpretation annotation, not a revised exclusion, hypothesis or subgroup score.

- Leg2 200–500 m has mean alpha below the training feature range, producing high-pH extrapolations. Its first two observed pH means lie inside the training observed-pH range, while the third is below.
- Leg2 500–800 m has mean alpha inside the training feature range, but all three observed pH means lie below the training observed-pH range. Thus feature membership alone is also insufficient to guarantee the original alpha-to-pH mapping in this station block.
- Only Leg1 300–400 m lies inside both saved ranges. The aggregate Leg1 improvement therefore cannot be portrayed as an all-interpolation success against an all-extrapolation failure.

The complete six-layer comparison mixes feature interpolation, feature extrapolation and target-distribution shifts. It must be called regional transfer/applicability testing, not an entirely within-calibration-range falsification. Do not re-score selected in-range layers after seeing the response. These patterns do not uniquely identify water-mass mixing, dissolved inorganic carbon, alkalinity, organic complexation, scavenging or assay effects as the cause.

## Strongest defensible contribution and nearest-neighbour challenge

**Core claim:** On new GP15 matched parent-bottle measurements aggregated into the original six 100 m layers, the unchanged Lin–Catling calibration has different relative skill in the two station blocks. It improves on the fixed original depth-only pH means in the exploration-exposed first block, but performs worse in the prospectively reserved second block; this contrast remains under the prespecified depth, weighting, station-deletion and enumerated source-method scenarios. Restricting application to nominal 200–800 m depth therefore does not itself demonstrate quantitative regional transfer or added skill over the depth-only reference.

This is more than reproducing an author curve or substituting a regression model: the original modern calibration linked global depth-layer means, whereas the new test observes REE and pH on matched parent bottles from an excluded cruise, keeps the original mapping fixed, and checks whether its apparent information beyond depth transfers. The new evidence is the measured magnitude, sign and support of this applicability contrast. It can inform the decision to require independent regional calibration checks before treating the original mapping as a quantitative regional estimator. It does not establish a replacement calibration or an environmental mechanism.

Fair comparison to the original paper is essential:

- Lin & Catling 2024, DOI 10.2475/001c.118215, PDF page 3 §2.1 and page 5 §3.2: global empirical 200–800 m calibration and acknowledged possible changes among water masses.
- PDF pages 8–9 §4.2: explicitly narrow modern training pH range and uncertainty in linear extrapolation; existing Ce/oxygen/Y–Ho checks also recognize mixed controls. The present study must not claim discovery of these caveats.
- PDF page 9 Fig. 4 and page 14 Fig. 10 already compare selected modern seawater REE patterns with carbonate-derived patterns and location-matched/interpolated GLODAP pH. Fig. 10 includes North Pacific TPS 24 271-1 at 184 and 640 m. Therefore no “first regional comparison” claim is justified. These are not the current new-cruise same-bottle six-layer frozen prediction-versus-baseline benchmark.
- PDF page 15 §4.6 explicitly frames Precambrian usefulness at approximately 1–2 pH units (around 1.5 units uncertainty). A present 0.218 pH modern-layer RMSE does **not** exceed or disprove that stated coarse paleo objective. The manuscript should address modern regional transfer precision and the evidential reach of the calibration, not claim that the entire proxy is invalid.

The recorded Crossref/OpenAIRE, publisher and DeepShovel search found no direct duplicate of this specific matched GP15 fixed-calibration comparison. OpenAlex/Semantic Scholar failures and ranked-return coverage limits remain disclosed. The allowed novelty statement is only that no substantive duplicate was found as of the recorded search date within the declared scope. The searches do not certify universal priority.

## Five-gate assessment

| Gate | Assessment for this narrow manuscript |
|---|---|
| No discovered substantive duplicate | Provisionally satisfied within the recorded search. Known regional examples, mixed controls and extrapolation uncertainty must be credited; novelty is the new paired transfer benchmark. |
| Traceable raw observations | Satisfied: four original CSVs, source-method attachments, exact author-code/normalizer versions and row/field/SHA bindings are retained. Aliquot differences and unknown covariance remain explicit. |
| Recomputable key results | Satisfied by the independent full finite recomputation documented here. Numerical success alone is not scientific validation. |
| Validation strength matches claims | Satisfied for the reported GP15 station-block applicability comparison, with the prospective-response chronology and shared cruise/lab disclosed. Not satisfied for causal mechanisms, worldwide transfer rates, independent-laboratory replication or paleo accuracy rejection. |
| Clear scientific contribution | Satisfied for a bounded empirical technical manuscript about where the fixed mapping adds predictive information beyond its depth reference. It is neither a new model nor a new mixing mechanism. Journal suitability and external scientific acceptance remain unassessed. |

Proceeding does not require inflating the claim or searching for a significant subset. Preserve both favourable Leg1 evidence and adverse Leg2 evidence, every fixed variant and the feature-domain labels. If the intended manuscript instead claims that all REE-pH methods or Precambrian reconstructions are disproved, the evidence gate fails.

## Figure and deliverable review

The frozen PNG was visually inspected. All six layers and all four fixed Leg2 variants agree with the recomputation; no clipping, missing labels or unreadable panels were found. The two upper panels use distinct x-axis extents, so readers must use numeric ticks, not visual horizontal displacement, to compare legs. For manuscript figures, add training feature-domain/target-range context and actual unique-bottle/station counts in the caption, retaining the frozen original figure as the run artifact. This is presentation of existing results, not authority to alter the analysis.

## Review files and binding

- Independent script: `reviews/round8-ree-transfer-independent-recompute.py` — SHA-256 `4061708bca0e1e21162223d8182a2b7bbef569a230deff38367a5a05bcd3ed0e`
- Numeric receipt: `reviews/round8-ree-transfer-independent-numeric.json` — SHA-256 `b61df8353e870aeb159a6d7bdea454e7facff91ce7249a643b2be88a7e732cff`
- Frozen plan SHA-256 `6f5defd55abc658c961b7a8396fbe490aa2f38dcda84e8ded1bd9287e4c22820`
- Frozen protocol SHA-256 `08483ad527d609ab123e0a8949af23e47f9bf921b5e1dc48b5721706c99acc79`
- Frozen code SHA-256 `af466ab78796d559e83d5610ab72168f42de960b6b8e3085c01092398207baeb`
- Main aggregate output SHA-256 `13d6831201d631d6ce39ef99ba5ef35be057a20f58795214956b02157a9b1b18`
- The numeric receipt retains all source and output SHAs, exact original-row spot checks, comparison counts and domain annotations.

Recorded UTC: 2026-10-25T16:09:08.646581+00:00
