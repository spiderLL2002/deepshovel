# Supplementary tables

**Table S1. Original dataset versions and primary sample support.** pH is total scale at 25 °C. Layer memberships can repeat boundary bottles and are not independent samples.

|Leg|REE dataset/version|pH dataset/version|Unique bottles|Stations|Memberships|
|---|---|---|---|---|---|
|1|BCO-DMO 932161 v1|BCO-DMO 838157 v2|24|6|28|
|2|BCO-DMO 932559 v1|BCO-DMO 838173 v2|55|8|63|

**Table S2. All original fixed aggregation comparisons.** Errors are in pH units and rounded for presentation; full precision remains in the original result CSV/JSON. ΔRMSE=model minus fixed-depth baseline. Leg 1 half-open has only five layers meeting the three-station criterion, although all six remain in the descriptive aggregate. Primary scoring uses 24/55 unique bottles; REE-depth scoring uses 26/58 from the unchanged eligible union pool. Within each convention both predictors use identical support, while scored records can differ across conventions.

|Leg|Convention|REE RMSE|Baseline RMSE|ΔRMSE|
|---|---|---|---|---|
|1|Primary|0.0702|0.1701|-0.0999|
|1|Half-open bins|0.0753|0.1642|-0.0889|
|1|REE-reported depth|0.0536|0.1817|-0.1281|
|1|Station equal|0.0715|0.1742|-0.1027|
|2|Primary|0.2183|0.1103|+0.1080|
|2|Half-open bins|0.2242|0.1149|+0.1093|
|2|REE-reported depth|0.2202|0.1203|+0.0999|
|2|Station equal|0.2183|0.1177|+0.1006|

**Table S3. Marginal ranges of the six global calibration means and primary GP15 means.** Alpha is displayed in 10⁻⁸ per atomic-number unit. The original six α means span 2.5649–3.2036 in those units; the six GLODAP product-pH means span approximately 7.584326–7.730879. Classifications use the original unrounded values and inclusive endpoints. These extrema do not describe the full range of individual observations in either source compilation. Reference pH remains a GLODAP product variable without a directly-measured-only selection.

Leg 1

|Depth m|Alpha ×10⁸|Alpha position|GP15 measured pH|Measured-pH position|
|---|---|---|---|---|
|200–300|2.1772|Below|7.770|Above|
|300–400|2.6113|Within|7.658|Within|
|400–500|3.2827|Above|7.530|Below|
|500–600|3.3071|Above|7.414|Below|
|600–700|3.8592|Above|7.365|Below|
|700–800|3.7843|Above|7.362|Below|

Leg 2

|Depth m|Alpha ×10⁸|Alpha position|GP15 measured pH|Measured-pH position|
|---|---|---|---|---|
|200–300|1.6138|Below|7.696|Within|
|300–400|1.9797|Below|7.640|Within|
|400–500|2.3405|Below|7.514|Below|
|500–600|2.6744|Within|7.521|Below|
|600–700|2.9819|Within|7.436|Below|
|700–800|3.0364|Within|7.499|Below|

**Table S3 continued. Counts of primary layer means inside the two marginal intervals.** Columns use measured GP15 pH relative to the original product-pH mean interval. Counts are layer means, not independent bottles or studies.

|Alpha interval|Measured pH inside|Measured pH outside|
|---|---|---|
|Inside|1|3|
|Outside|2|6|

Only Leg 1 300–400 m falls inside both intervals; its already reported absolute errors in Table S10 favor the depth reference. This links the same stored row, not a new within-domain performance score. A rectangle of two marginal ranges is not a joint confidence or validity region. Observed target pH is needed for the second axis, so these descriptions cannot be used as a pre-observation acceptance rule. Source labels, exact original cutpoints and counts remain in joint_domain_labels.csv, joint_domain_counts.csv and the original training-domain annotation.

**Table S4. Station × primary-depth-layer support.** Entries are eligible bottle memberships, including explicit zeros. Asterisks mark stations represented in every layer. Inclusive boundaries allow one bottle in two neighboring columns. Totals therefore sum memberships, whereas the final column counts unique bottles. No area weighting is implied.

Leg 1

|Station|200–300|300–400|400–500|500–600|600–700|700–800|Unique bottles|
|---|---|---|---|---|---|---|---|
|5|0|1|1|0|1|0|3|
|10|2|0|1|0|1|0|4|
|12|1|1|2|0|0|0|4|
|16*|1|2|1|1|2|1|5|
|18*|1|1|1|1|1|1|5|
|18.3|0|1|0|1|0|1|3|
|Totals|5|6|6|3|5|3|24|

Leg 2

|Station|200–300|300–400|400–500|500–600|600–700|700–800|Unique bottles|
|---|---|---|---|---|---|---|---|
|18.6|0|0|0|0|1|0|1|
|19|2|2|1|1|1|0|7|
|21|2|1|1|0|0|0|4|
|25|2|2|2|0|0|0|6|
|31*|2|2|4|2|2|2|11|
|33*|2|2|4|1|1|1|10|
|37|3|2|2|2|0|1|9|
|39|3|3|3|1|0|0|7|
|Totals|16|14|17|7|5|4|55|

**Table S4 continued. Recorded coordinate ranges for eligible primary bottles.** Coordinates come from the REE source CSV; negative latitude is south and negative longitude west. Ranges retain source precision and are positions of this sample support, not area boundaries. Rounded pH-file coordinates and row-level provenance remain in station_geometry.json.

|Leg|Station|Latitude °N|Longitude °E|
|---|---|---|---|
|1|5|53.677|-153.797|
|1|10|42.000|-152.000|
|1|12|37.000|-152.000|
|1|16|27.000|-152.000|
|1|18|22.000|-152.000|
|1|18.3|19.681|-154.513|
|2|18.6|18.906|-155.258|
|2|19|17.500|-152.000|
|2|21|11.000|-152.000|
|2|25|5.000|-151.996|
|2|31|-2.500 to -2.499|-152.000|
|2|33|-5.000|-152.003 to -152.001|
|2|37|-15.000|-152.000|
|2|39|-20.000|-152.000|

**Table S5. Descriptive decomposition of six-layer error.** Mean error=prediction minus measured GP15 pH. MSE=(mean error)²+centered MSE. Centered RMSE is an after-observation diagnostic; it does not represent a validated correction or revised performance. All six original means have equal weight.

|Leg|Prediction|RMSE|Mean error|Centered RMSE|Mean-error²/MSE %|
|---|---|---|---|---|---|
|1|REE|0.0702|+0.0615|0.0339|76.8|
|1|Depth baseline|0.1701|+0.1335|0.1054|61.6|
|2|REE|0.2183|+0.2108|0.0565|93.3|
|2|Depth baseline|0.1103|+0.0992|0.0483|80.8|

**Table S6. Post-review common-station diagnostic.** Intersecting station labels across all six primary inclusive layers leaves only two per leg. Within each layer, station means receive equal weight; six layers then receive equal weight. This exploratory diagnostic cannot replace the primary estimand, recover missing stations, demonstrate regional representativeness, or create independent validation. No interval or p value is assigned. Station labels do not imply constant sampling events; see Table S11.

|Leg|Stations|Unique bottles|Memberships|REE RMSE|Baseline RMSE|ΔRMSE|
|---|---|---|---|---|---|---|
|1|16, 18|10|14|0.0801|0.1672|-0.0871|
|2|31, 33|21|25|0.2806|0.1694|+0.1111|

**Table S7. Unresolved reported-depth conflict for sample 14984.** The same sample ID, station, event and normalized start time agree across records. All five required flags are 2. REE depth 360 m versus pH depth 261 m changes the assigned 100 m layer; identity agreement does not determine the true depth. The primary uses the predeclared pH-depth convention for consistent pairing to the pH target, without claiming it is physically superior; the original REE-depth sensitivity covers an alternative recorded assignment and preserves aggregate ordering.

|Field|REE record|pH record|
|---|---|---|
|Dataset/version|932559 v1|838173 v2|
|Physical CSV line|265|116|
|Sample ID|14984|14984|
|Station / event|31 / 6912|31 / 6912|
|Reported depth m|360|261|
|Start UTC|2018-11-11T02:07:00Z|2018-11-11T02:07Z|
|Required flags|Sm/Gd/Dy/Er=2|pH=2|
|Assigned inclusive layer m|REE depth 300–400 m|pH depth 200–300 m|

There are 53 nonzero depth differences among 79 unique primary bottles: 49 have absolute differences at most 2 m, three are greater than 2 m and at most 10 m, and sample 14984 is the only one greater than 10 m. These are differences between archive fields, not known physical depth errors. The eligible union has 58 nonzero differences among 84 pairs; the maximum is 99 m in both scopes and also among all 173 identity-matched nonzero conflicts. All 84 eligible union timestamps agree after ISO normalization (Leg 1: 26, Leg 2: 58), including the 79 primary pairs (24, 55). No timestamp exclusion was added. Full conflicts and source hashes remain in depth_conflicts.csv and summary.json.

**Table S8. Original six calibration pairs, shared depth reference and common-concentration illustration.** Each row pairs global depth-layer means from separate REE and pH compilations, not individually matched training bottles. The product-pH column is also the unchanged depth comparator used on GP15; it is not the value predicted by the fitted line. These are the original stored outputs, displayed with rounding and without refitting. Let A be the mean of the four normalized concentrations and s their OLS slope after division by A. Multiplication of all four concentrations by 1.10 leaves relative shape unchanged and changes the fixed prediction by 0.10 alpha/beta. This is algebraic sensitivity, not a cause of GP15 residuals.

|Training depth m|Training alpha ×10⁸|Product pH and fixed depth reference|Δpredicted pH at +10%|
|---|---|---|---|
|200–300|2.5649|7.730879|-0.0643|
|300–400|2.7586|7.689871|-0.0692|
|400–500|2.8339|7.658636|-0.0711|
|500–600|2.9132|7.631553|-0.0731|
|600–700|3.0248|7.606188|-0.0758|
|700–800|3.2036|7.584326|-0.0803|

The reference and calibration share these six pH means. The benchmark therefore asks whether the fixed REE mapping improves on this particular inherited depth profile on matched target support; it does not compare independent calibration histories, prove chemical information independent of depth, or rank all possible predictors. Original full-precision pairs remain in training-domain-annotation.json and the saved author notebook output.

Bottle-level A, s and alpha and both record-weighted/station-equal layer descriptions are retained in abundance_shape_bottles.csv and abundance_shape_layers.csv. For weighted layer means, mean(alpha)=mean(A)×s_of_mean_composition; in general mean(alpha)=mean(A)×mean(s)+covariance(A,s), so multiplying separately averaged A and s is insufficient. These algebraic components do not independently identify concentration supply, mixing, complexation or particle exchange.

## Source and analysis status

Tables S1–S3 use the original fixed-analysis results and calibration-mean annotation; S3 also displays the checked joint-domain labels. Tables S4–S8 use post-review diagnostic v2. Tables S9–S12 display fixed constants and existing numerical results through review-tables-v1. Table S13 records source comparisons, not a numerical experiment. S14 and method S9 use continuous-box-v1; S15 and methods S10–S11 use selection-weight-domain-v1. S16 proposes discriminating observations, and S17 identifies evidence roles; neither supplies new measurements.

The supplied local manuscript/table-source-map-v2.json retains complete task provenance. The portable payload index iteration-2026-10-27/reproducibility/public-lineage/table-source-map-v2.public.json maps every table and supplementary method to available source/result paths and SHA values, and identifies local-only literature context or workflow records separately. Public measurement and executable-result bindings remain included; an unavailable context file is not represented as an included full text. In the portable payload, the three new result sets are under iteration-2026-10-27/analysis/. Their source_manifest.json or input_manifest.json records bind inputs and frozen plans; output_manifest.json binds computed files. Separate checking receipts are in iteration-2026-10-27/reviews/cycle02/continuous-box-scientific-A.json, continuous-box-independent-C.json, and reviews/selection-weight-domain-independent-B.json. The old revision-2026-10-27/figures/v3/figure-source-manifest.json describes only its original figure/table source values, not these later analyses. Exact original CSV row locators remain in the underlying manifests and audit tables. Original source bytes, fixed coefficients and results are unchanged; SHA verifies byte identity, not analytical accuracy or scientific validity. All new diagnostics use exposed data, and full computational precision is retained separately from display rounding.

## Supplementary method S1. Source values and qualification

The normalization composition is the fixed PAAS row delivered with the original author implementation. The four reference abundances are not re-estimated from GP15 or replaced by another shale. REE and pH missing archive tokens remain unavailable; no zero, detection-limit or LOD/2 substitution is performed. Positive finite REEs and the required flag 2 define the reported eligibility rule. The REE intercalibration report notes that detection estimates depend on preconcentration and dilution. Generic low-sample estimates do not establish bottle-specific limits, and quality flags do not supply a complete analytical uncertainty model. No additional detection-limit filter was introduced after seeing the target data.

## Supplementary method S2. Replication and uncertainty support

The Leg 1 pH report states duplicate analyses for 12 Station 5 samples. The three retained primary samples at that station are 12333, 12331 and 12329; their archived pH SD fields are 0.0006, 0.0118 and 0.0101, respectively. The remaining primary pH samples have no reported numerical sample SD. Leg 2 reports no duplicate samples and supplies no numerical sample SDs. These fields describe uneven analytical replication, not independent parent bottles or a complete per-bottle uncertainty distribution. No inverse-variance weights are estimated from the small replicated subset, and the observed pH values, equal record weights and fixed comparison are unchanged. Reference-material offsets and coherent REE shifts remain separate conditional sensitivity scenarios; they do not estimate or replace missing sample variances, method bias or joint calibration covariance.

## Supplementary method S3. Conditional station resampling

For each leg, eligible primary bottles were grouped by their original station label, preserving every retained bottle, event and boundary membership within its block. The actual label arrays, sorted as strings in the original code, were [10, 12, 16, 18, 18.3, 5] and [18.6, 19, 21, 25, 31, 33, 37, 39]. The minimum-six-station condition was checked on this original set before drawing, not on the number of distinct labels in every resample.

Python random.Random was initialized with seeds 271828 and 271829. Each of 1,000 draws used random.choices to select six or eight labels uniformly with replacement. Repeated labels repeated the entire station bottle block, including all its events; the primary record-weighted layer means were then recomputed from this concatenated collection. Equal station weights were not substituted into this resampling procedure.

A draw was retained only when all six originally occupied layers remained nonempty. No reduced-layer score was substituted, and the three-station descriptive occupancy condition was not an additional draw-rejection rule. There were 975 retained/25 rejected draws on Leg 1 and 978 retained/22 rejected on Leg 2. These fractions exceeded the prespecified 90% availability threshold. The 2.5th and 97.5th percentiles of the retained scores were linearly interpolated at position (N−1)q in sorted arrays, where N is the retained count and q is 0.025 or 0.975. Exact stations, seeds, counts and endpoints are preserved in cluster_bootstrap.json.

The rejection rule conditions the reported distribution on complete six-layer coverage. It does not produce nominal confidence coverage for a geographic population, make the sampled stations a probability sample, or treat the six layer means as independent observations.

## Supplementary method S4. Fixed normalization and finite measurement scenarios

**Table S9. Fixed author normalization constants.** Abundances and molar masses are transcribed from the exact author PAAS and molar_mass_mgmol files. The latter stores mg mol⁻¹; the table expresses the same masses in g mol⁻¹. No updated abundance reference or atomic-mass compilation is substituted.

|Element|Atomic number|PAAS μg g⁻¹|Molar mass g mol⁻¹|
|---|---|---|---|
|Sm|62|6.884|150.360|
|Gd|64|6.043|157.250|
|Dy|66|5.325|162.500|
|Er|68|3.075|167.259|

For the selected elements, raw concentrations cᵢⱼ in pmol kg⁻¹ were converted to mol g⁻¹ by multiplying by 10⁻¹⁵. The PAAS denominator was qⱼ×10⁻⁶/Mⱼ mol g⁻¹, using the tabulated abundance qⱼ and molar mass Mⱼ in g mol⁻¹. Thus Rᵢⱼ=(cᵢⱼ×10⁻¹⁵)/(qⱼ×10⁻⁶/Mⱼ). The published OLS slope and fixed pH mapping were then applied without refitting.

In each REE scenario, cᵢⱼ*=cᵢⱼ(1+uⱼrⱼ), where uⱼ is −1 or +1 and the selected sign for an element is shared by all bottles. The selected r vector for Sm/Gd/Dy/Er is 0.041/0.050/0.040/0.039. The Sm, Gd and Er values are the GP13455 RSDs in Table 2 of the Leg 2 REE intercalibration report; the Dy value is the GSP-29 RSD in Table 3. These are the original selected maxima across those reference-water rows. They describe repeated reference-water analysis, not independent random errors assigned to target bottles.

Each of 16 elemental sign vectors was crossed with three target-pH shifts o=0, −0.0043 and −0.0125 and three original-reference shifts t=−0.02, 0 and +0.02, giving 16×3×3=144 cases per leg. The two negative target shifts are the negative of recorded-minus-assigned differences for reference materials B162 (7.9143−7.9100) and B164 (7.5532−7.5407), as reported in the pH intercalibration documents. The original-reference shift is a separate GLODAP-motivated scenario choice; it is not an estimated error distribution for the exact retained training subset.

For a bottle in layer k, errors are eᵢ,model*=h(cᵢ*)+t−(yᵢ+o) and eᵢ,reference*=dₖ+t−(yᵢ+o), where h is the unchanged REE mapping and dₖ is the stored original layer reference. Target bottles, binning and weights stay fixed within a case. The reference receives no REE perturbation, but both predictors receive the same t shift. These bottle errors are averaged within layers before the six-layer score is computed. No regression coefficient is re-estimated.

This finite stress design probes coherent scale directions and common pH offsets. Its vertices alone do not establish an interior minimum; Supplementary method S9 adds a separate post-review argument for the same shared REE box and nine original pH choices. Neither analysis represents all possible correlation structures or sample-specific errors. Joint calibration-coefficient uncertainty and the unknown bias/covariance of individual bottles are not supplied by the 144 cases.

## Supplementary method S5. Averaging and the scored target

**Table S10. Primary layer means and signed errors.** Both predictors are scored on the same eligible bottles within each row. Bottle/station counts are memberships within that layer; internal-boundary bottles can appear twice across rows. Values below are a display of the original twelve primary rows, with no recalibration, new eligibility rule or additional response test. Full-precision original numeric tokens and raw sample IDs are retained in primary_layer_scores.csv.

For each layer, both the observed pH and predicted pH are averaged using identical weights. With nₖ bottles, primary weights are 1/nₖ. Under station-equal weighting, a bottle in station j receives 1/(Jₖnⱼₖ), so station means are weighted equally and weights still sum to one. The primary error is the weighted mean of bottle errors, and the headline RMSE is the square root of the mean squared errors of the six layer means. It is not the root mean square of all bottle-level errors. Within-layer error dispersion and dependencies across layers therefore cannot be inferred from the headline score.

Leg 1

|Layer m|Bottles/stations|Measured pH|REE pH|Reference pH|REE error|Reference error|
|---|---:|---:|---:|---:|---:|---:|
|200–300|5/4|7.770460|7.827262|7.730879|0.056802|-0.039581|
|300–400|6/5|7.657633|7.718414|7.689871|0.060781|0.032237|
|400–500|6/5|7.530350|7.550065|7.658636|0.019715|0.128286|
|500–600|3/3|7.414267|7.543944|7.631553|0.129677|0.217286|
|600–700|5/4|7.365420|7.405490|7.606188|0.040070|0.240768|
|700–800|3/3|7.362233|7.424269|7.584326|0.062036|0.222092|

Leg 2

|Layer m|Bottles/stations|Measured pH|REE pH|Reference pH|REE error|Reference error|
|---|---:|---:|---:|---:|---:|---:|
|200–300|16/7|7.696456|7.968543|7.730879|0.272086|0.034422|
|300–400|14/7|7.639750|7.876788|7.689871|0.237038|0.050121|
|400–500|17/7|7.514182|7.786329|7.658636|0.272146|0.144454|
|500–600|7/5|7.520929|7.702594|7.631553|0.181665|0.110624|
|600–700|5/4|7.436140|7.625485|7.606188|0.189345|0.170048|
|700–800|4/3|7.499050|7.611826|7.584326|0.112776|0.085276|

CSV values retain original stored numeric tokens; Markdown is rounded to six decimals. Source: results/ree_ph_fixed_transfer_v1/layer_summary.csv, variant=primary.

## Supplementary method S6. Event composition within the common stations

**Table S11. Source event IDs contributing to the common-station layers.** IDs are the original sampling-event identifiers linked through the same paired sample IDs used in Table S6. The restriction fixes station labels, not event composition; no additional sample is excluded, no score is replaced, and event identifiers are not counted as independent replicates. On Leg 2, shallow and deep layers use different events at both stations, with both events contributing at 400–500 m. The common-station result is therefore not a controlled comparison of complete single-event profiles.

Leg 1

|Station|200–300|300–400|400–500|500–600|600–700|700–800|
|---|---|---|---|---|---|---|
|16|6718|6718|6718|6718|6718|6718|
|18|6753|6753|6753|6753|6753|6753|

Leg 2

|Station|200–300|300–400|400–500|500–600|600–700|700–800|
|---|---|---|---|---|---|---|
|31|6912|6912|6912, 6922|6922|6922|6922|
|33|6931|6931|6931, 6940|6940|6940|6940|

Sources: revision-2026-10-27/analysis/results-v2/common_station_layers.csv (fixed membership) and results/ree_ph_fixed_transfer_v1/sample_predictions.csv (station/event identity and raw row locators).

## Supplementary method S7. Concentration scaling of the implemented feature

The original §2.1 interpretation describes enrichment regardless of absolute concentration. Positive scaling preserves the sign of the trend, but its supplied unlogged slope has a magnitude that changes with scaling. This distinction concerns the quantitative feature used for pH prediction, not whether a qualitative enrichment trend can be recognized. In equation (1), replacing each Rⱼ by λRⱼ multiplies the numerator by λ while the denominator is unchanged. Therefore α(λR)=λα(R). The fixed pH prediction changes by (λ−1)α/β, with the published intercept cancelling. This exact homogeneity property requires no new calibration or GP15 fitting.

For λ>0, each elemental ratio is unchanged and A increases by λ, while the shape slope s after division by A is unchanged. Fixed shale normalization removes the chosen reference composition but does not normalize each sample to equal abundance. The illustration in Table S8 is therefore a qualification to interpreting the implemented feature as concentration-scale-independent; it is not a mechanism test or evidence that a particular abundance change occurred on GP15. The selected Sm–Gd–Dy–Er subset and atomic numbers were fixed by the released model before GP15 scoring; no target-driven element selection was performed. The original paper (§2.1; Table 1) discusses exclusions including Ce and Eu anomalies. This four-element diagnostic does not measure those anomalies or establish a full-series heavy/light REE ratio. Speciation, temperature/pressure response and carbonate partitioning are not isolated by this algebra or by the pH25 benchmark.

**Table S12. Actual primary mean-composition abundance and relative shape.** A is the arithmetic mean of the four selected PAAS-normalized molar concentrations, not total dissolved REE concentration, a mass inventory or a sum over the full REE series. The displayed s is the OLS slope after dividing the record-weighted four-element mean composition by its A; it is not the average of bottle-level s or an index of every feature of a full REE pattern. Mean α=A×s_of_mean_composition. Each row uses exactly the corresponding primary scoring support in Table S10. Cross-leg comparisons retain different sampled geography, and adjacent layers can contain different stations/events. This is an exposed-data chemical description, not a controlled water-mass comparison or causal division of prediction error. Displayed A and α are multiplied by 10⁸; the CSV retains original tokens and identities.

Leg 1

|Layer m|Bottles/stations|A ×10⁸|s of mean composition|Alpha ×10⁸|
|---|---:|---:|---:|---:|
|200–300|5/4|9.744497|0.223432|2.177232|
|300–400|6/5|11.343421|0.230205|2.611308|
|400–500|6/5|14.405918|0.227870|3.282676|
|500–600|3/3|14.577942|0.226856|3.307087|
|600–700|5/4|17.296597|0.223121|3.859232|
|700–800|3/3|16.194602|0.233679|3.784343|

Leg 2

|Layer m|Bottles/stations|A ×10⁸|s of mean composition|Alpha ×10⁸|
|---|---:|---:|---:|---:|
|200–300|16/7|6.420556|0.251351|1.613811|
|300–400|14/7|7.711993|0.256707|1.979721|
|400–500|17/7|8.782569|0.266490|2.340469|
|500–600|7/5|9.853869|0.271406|2.674399|
|600–700|5/4|11.806726|0.252560|2.981906|
|700–800|4/3|10.664340|0.284722|3.036375|

Source values are copied from revision-2026-10-27/analysis/results-v2/abundance_shape_layers.csv; no feature or target is re-estimated. CSV retains original tokens; Markdown rescales A/alpha only for display.

## Supplementary method S8. Calibration direction and unpropagated uncertainty

The author proxy script assigns x=layer_pH_avgs and y=layer_slope_avgs, then calls numpy.polyfit(x,y,1,cov=True). The released estimation script fixes the stored β and b and calculates pH=(α−b)/β. The calibration is therefore an α-on-pH regression followed by algebraic inversion, not a new pH-on-α regression fitted to GP15. The supplied fixed coefficients are retained regardless of the target scores.

The original code computes a conditional polynomial-fit covariance matrix. This does not by itself supply the complete uncertainty needed for target pH inference, including measurement errors, shared-source dependence and transfer discrepancy. No joint coefficient covariance is propagated in the present fixed-implementation comparison; this is an analysis-scope limitation, not a claim that a conditional coefficient covariance could never be calculated. The original fitted α band and regional target-pH error are different quantities. No pH prediction interval or calibrated uncertainty coverage for new GP15 bottles is derived from that band.

## Relation to the nearest published questions

**Table S13. Question, observational link and method relative to the nearest studies.** Analyses from one paper are shown separately to clarify the methodological distinction, not counted as independent studies. All prior regional and carbonate applications remain acknowledged; this table makes no first-study or exhaustive-novelty claim.

|Work or analysis|Observational link|Quantity compared|Scope of its evidence|
|---|---|---|---|
|Lin and Catling 2024, global calibration|Separate modern REE and GLODAP compilations summarized in six depth layers|Unlogged PAAS-normalized α regressed on pH, then inverted for use|Original depth-aggregate calibration; source of both fixed coefficients and stored pH reference|
|Lin and Catling 2024, regional/carbonate examples|Regional REE patterns with corresponding or nearby GLODAP pH; carbonate estimates with published boron estimates|REE patterns and geological pH comparisons, including original Figs 4 and 10 and carbonate applications|Established regional comparison and geological interpretation; not a discovery newly claimed here|
|Liu et al. 2026|Adsorption experiments and marine sediment records|Y/Ho fractionation and a distinct pH proxy|Relevant adjacent proxy in a different feature and medium; does not test the four-element fixed mapping on these GP15 bottles|
|This study|REE and directly measured pH linked by parent-bottle identity in two GP15 station groups|Unchanged published mapping versus unchanged original depth reference on the same layer support|Finite modern transfer diagnosis and feature-scale qualification; no new model, geological mechanism or advance regional acceptance rule|

Lin and Catling (2024) is the direct methodological predecessor and is not treated as an independent validation dataset for this manuscript. The source-paper regional examples prevent a claim that regional comparison or environmental sensitivity is new. The algebraic concentration-scale dependence is a property of the existing linear feature, not a new mathematical method. The incremental empirical claim remains the matched-support fixed-comparator error ordering and its observed error structure.

The search update dated 27 October 2026 used DeepShovel, Crossref, OpenAIRE and available publisher/author originals. Within the declared searches and actually read sources, no verified substantial duplicate of this matched GP15 fixed-comparator analysis was found. The accompanying records distinguish metadata screening from original passages read, and preserve zero-result queries, broad-query noise, unpaged rankings and unread full texts. DeepShovel comparisons of seven core propositions returned incomplete coverage with no adopted literature relations; this is neither validation nor proof of novelty. No first-ever feature-scale discovery or exhaustive absence claim is made.

## Supplementary method S9. Conditional coverage between the original REE corners

This post-review extension uses the same primary layers, weights, coefficients, reference-water scales and nine discrete pH-offset pairs as Supplementary method S4. For shared element coordinates uⱼ∈[−1,1], errors obey eₖ(u,o,t)=eₖ(0,0,0)+(t−o)+ΣⱼCₖⱼuⱼ, with Cₖⱼ=((Zⱼ−65)/(20β))rⱼ mean(Rₖⱼ). For fixed (o,t), reference RMSE is constant in u. Thus model-minus-reference RMSE is a convex norm of an affine vector minus a constant, and its maximum over the box is attained at a vertex. A minimum over vertices alone is not an interior lower bound.

All C columns have signs (+,+,−,−). When every component lower limit eₖ(0)+(t−o)−Σⱼ|Cₖⱼ| is positive, u=(−1,−1,+1,+1) simultaneously minimizes all positive layer errors and hence their norm. This condition held for every Leg 2 offset pair, but not for Leg 1. Leg 1 minima are consequently not established by this argument; its negative maximum differences nevertheless establish the reported ordering. Leg 2 has positive minimum differences. Both legs retain their respective ordering over this shared four-coordinate REE box at each original pH choice.

The original serialized decimal inputs and coefficients define the mathematical object. Exact rational MSE differences determine ordering; displayed square roots are approximate. Independent reconstructions checked all 18 certificates and the original corner/primary matches, whose largest discrepancy from legacy floating arithmetic was below 2×10⁻¹⁵ pH. This does not formally verify every floating-point execution or transform reference-water RSDs into real bottle-error bounds.

**Table S14. All original pH-offset choices and conditional REE-box coverage.** ΔRMSE is model minus reference. Entries are rounded display approximations of the mathematical extrema; exact rational MSE bounds and signs are retained in pair_envelope.csv and ranking_certificates.json. The missing Leg 1 minima are unproved by the selected sufficient condition, not omitted favorable results. No alternative optimizer or sub-box was introduced.

|Leg|o|t|Minimum ΔRMSE display|Maximum ΔRMSE display|Smaller RMSE throughout box|
|---|---|---|---|---|---|
|1|0|-0.02|Not established|-0.0483|REE model|
|1|0|0|Not established|-0.0445|REE model|
|1|0|0.02|Not established|-0.0413|REE model|
|1|-0.0043|-0.02|Not established|-0.0474|REE model|
|1|-0.0043|0|Not established|-0.0437|REE model|
|1|-0.0043|0.02|Not established|-0.0407|REE model|
|1|-0.0125|-0.02|Not established|-0.0458|REE model|
|1|-0.0125|0|Not established|-0.0424|REE model|
|1|-0.0125|0.02|Not established|-0.0396|REE model|
|2|0|-0.02|+0.0707|+0.1435|Depth reference|
|2|0|0|+0.0718|+0.1455|Depth reference|
|2|0|0.02|+0.0724|+0.1469|Depth reference|
|2|-0.0043|-0.02|+0.0710|+0.1440|Depth reference|
|2|-0.0043|0|+0.0720|+0.1458|Depth reference|
|2|-0.0043|0.02|+0.0725|+0.1471|Depth reference|
|2|-0.0125|-0.02|+0.0715|+0.1449|Depth reference|
|2|-0.0125|0|+0.0722|+0.1464|Depth reference|
|2|-0.0125|0.02|+0.0727|+0.1475|Depth reference|

The argument concerns one coherent shift per element, shared by all bottles. It does not cover independent bottle-specific perturbations, a continuous pH-offset interval, joint calibration-coefficient uncertainty, geographical representativeness or a causal mechanism. The chosen RSD box is a conditional stress domain, not a probability model or a hard bound on actual analytical errors.

## Supplementary method S10. Original selection flow and its denominators

**Table S15. Archive identity counts and original ordered selection statuses.** Counts are reconstructed from the unchanged source IDs and copied original audit assignments. The first four rows describe archive records/IDs; the next eight rows are the mutually exclusive original terminal classes, including zeros. The final two rows are primary support, not additional terminal classes. Eligible union and primary counts refer to unique paired parent-bottle IDs; layer memberships can repeat a boundary bottle.

|Archive count or original terminal class|Leg 1|Leg 2|
|---|---|---|
|REE archive records|372|472|
|pH archive records|157|190|
|Union of archive sample IDs|446|516|
|Shared sample IDs|83|146|
|Not shared: first terminal status|363|370|
|Station/event mismatch|0|0|
|Leg 2 station/sample already in Leg 1|0|0|
|Missing finite depth|0|0|
|Outside reported-depth union|57|83|
|Unaccepted bottle flag|0|5|
|Missing/invalid response after preceding checks|0|0|
|Eligible reported-depth union|26|58|
|Primary unique paired bottles|24|55|
|Primary layer memberships|28|63|

The original if/elif sequence assigns one terminal status to each sample ID in the two-file union. Potential scientific failure causes need not be disjoint: a record classified outside the depth union is not subsequently evaluated for a missing response or unaccepted flag. Zero in a later category therefore does not mean every earlier rejected record had a valid value. Shared ID alone likewise does not establish a retained chemical measurement. Exact IDs, raw row locators, ordered-stage counts and source hashes are retained in eligibility_flow.csv and audit_status_provenance.csv.

The primary 24/55 bottles are a subset of the fixed 26/58 eligible depth-union pairs, determined by the chosen reported-depth convention. Neither source-file count, station count nor layer membership is an independent-study count. This flow exposes the available paired support; it does not estimate missing-at-random selection, reconstruct unmeasured observations or make the archive cohort representative of a geographic population.

## Supplementary method S11. Pointwise layer dominance and depth weights

For the unchanged primary point means, define dₖ=e_model,k²−e_reference,k². With nonnegative across-layer weights wₖ summing to one, weighted MSE_model−weighted MSE_reference=Σₖwₖdₖ. Weighted RMSE has the same ordering because square root is increasing. Every Leg 2 dₖ is strictly positive, so its depth reference has strictly smaller weighted MSE and RMSE for every such weighting of these same six means. Leg 1 signs are positive in 200–300 and 300–400 m and negative in the other four layers. Its ordering therefore is not invariant to arbitrary depth weights: concentrating weight toward opposite-sign layers can favor either predictor.

This is a statement about the stored point comparisons, not another fitted or optimized score. No weights were selected after inspecting errors and no new weighted performance is reported. It does not change within-layer record/station weights, replace bottles, supply geographical area weights, or extend to simultaneous measurement perturbations, the REE-box calculation or bottle-level RMSE. Layer_squared_error_difference.csv retains every original signed-error token and exact rational dₖ; that arithmetic does not increase the physical precision of the measurements.

## Competing explanations and discriminating observations

**Table S16. Examples of evidence needed to investigate compatible mechanisms.** These are proposed follow-up contrasts, not observations newly supplied by this reanalysis. Mixing, carbonate-system controls and scavenging were already considered by Lin and Catling (2024); analytical fractionation is also an existing concern (Haryanto et al., 2025). None is inferred as the cause of the GP15 error contrast.

|Compatible explanation|Potentially informative independent observations or design|What the present comparison leaves unresolved|
|---|---|---|
|Water-mass composition or mixing|Same-event hydrography, carbonate-system variables and independently characterized endmember/mixing tracers, with matched spatial support|Changing stations/events are not a controlled mixing contrast and supply no mixing fraction|
|REE-selective complexation or particle exchange|Dissolved and particulate REE, ligand/particle context, and controlled or independent contrasts of pH and inventory|A and s describe composition; their product does not isolate a chemical flux or process|
|Analytical or processing offsets|Independently processed/interleaved aliquots, relevant reference waters, crossover and replicate information across the target range|The available RSD/CRM scales and sparse pH replication do not specify complete bottle-specific bias or covariance|

Such observations would constrain alternatives, not automatically identify a unique cause: mixing, chemical exchange and analytical effects can covary. An in-situ mechanism test would also need consistent temperature/pressure and carbonate-system definitions rather than interpreting the pH-at-25°C benchmark as a direct speciation experiment. The current transfer diagnosis remains the matched-support comparison of fixed predictions, without choosing one of these explanations or fitting a target-derived correction.

## Evidence roles and exposure

**Table S17. Source roles and distinct meanings of independence.** Dataset versions and file counts are provenance units, not counts of independent research programmes. The table separates empirical support from computational checks; none of the review products supplies new chemical measurements.

|Material|Role in this study|Dependency or exposure boundary|
|---|---|---|
|Four GP15 REE/pH source CSVs|Matched parent-bottle targets from two 2018 legs|One GP15 campaign with shared analytical programme; separate files and legs are not independent studies|
|Original REE and GLODAP compilations|Separate source compilations aggregated to six calibration layers|No GP15 entry found in the preserved versions; absence in these catalogues establishes only version-specific source non-overlap|
|Original six calibration-pH means|Targets in the released calibration and values of the fixed depth reference|The model and comparator share calibration provenance; neither is newly fitted to GP15|
|Reference-water and pH intercalibration reports|Motivate declared conditional perturbation scales|Not complete per-bottle error distributions, new validation observations or physical error bounds|
|Leg 1 / reserved Leg 2 joint responses|Exploration / response check after original freeze|Metadata and support were already inspected; all responses are now exposed and later diagnostics cannot reuse either as independent validation|
|Derived tables, independent code checks and AI reviews|Calculation verification and interpretation critique|Share underlying observations; computational independence does not create field-study independence|

## Supplementary figure captions

**Figure S1. Prespecified aggregation and station-deletion checks.** Points give model-minus-reference across-layer RMSE for every fixed aggregation convention. Negative values favor the REE model. Horizontal bars on primary rows span individual station deletions and are sensitivity ranges, not confidence intervals. The half-open Leg 1 calculation retains the weak layer although only five layers meet the three-station occupancy criterion. All variants score six layer means on their stated support. Data plots were produced by reproducible Python code developed with Codex assistance.

**Figure S2. Original calibration range and GP15 layer means.** The six original global calibration-layer means, fixed mapping and all twelve primary GP15 means are shown in alpha–pH space. Training pH is a retained GLODAP product variable whose directly measured versus calculated fraction is unknown; GP15 target pH is spectrophotometrically measured. The shaded alpha interval spans the six calibration-mean features; it is not the full raw-source feature envelope, a joint validity region or a confidence band. Deeper Leg 2 alpha values lie within this range while their measured pH lies below the training-pH range. This post-result annotation changes no selection or score. Data plots were produced by reproducible Python code developed with Codex assistance.
