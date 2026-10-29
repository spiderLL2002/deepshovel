# Supplementary tables

**Table S1. Original dataset versions and primary sample support.** pH is total scale at 25 °C. Layer memberships can repeat boundary bottles and are not independent samples.

|Leg|REE dataset/version|pH dataset/version|Unique bottles|Stations|Memberships|
|---|---|---|---|---|---|
|1|BCO-DMO 932161 v1|BCO-DMO 838157 v2|24|6|28|
|2|BCO-DMO 932559 v1|BCO-DMO 838173 v2|55|8|63|

**Table S2. All original fixed aggregation comparisons.** Errors are in pH units and rounded for presentation; full precision remains in the original result CSV/JSON. ΔRMSE=model minus fixed-depth baseline. Leg 1 half-open has only five layers meeting the three-station criterion, although all six remain in the descriptive aggregate.

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

**Table S3. Original feature-domain description of primary means.** Alpha is in 10⁻⁸ per atomic-number unit; original training range 2.5649–3.2036. Domain labels describe alpha only, not joint validity. No subset is rescored. Reference pH is the original GLODAP product average, without a direct-measurement-only restriction.

|Depth m|Leg 1 alpha|Position|Leg 1 pH|Leg 2 alpha|Position|Leg 2 pH|
|---|---|---|---|---|---|---|
|200–300|2.1772|Below|7.770|1.6138|Below|7.696|
|300–400|2.6113|Within|7.658|1.9797|Below|7.640|
|400–500|3.2827|Above|7.530|2.3405|Below|7.514|
|500–600|3.3071|Above|7.414|2.6744|Within|7.521|
|600–700|3.8592|Above|7.365|2.9819|Within|7.436|
|700–800|3.7843|Above|7.362|3.0364|Within|7.499|

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

**Table S6. Post-review common-station diagnostic.** Intersecting station labels across all six primary inclusive layers leaves only two per leg. Within each layer, station means receive equal weight; six layers then receive equal weight. This exploratory diagnostic cannot replace the primary estimand, recover missing stations, demonstrate regional representativeness, or create independent validation. No interval or p value is assigned.

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

There are 53 nonzero depth conflicts among 79 unique primary bottles (58 among 84 eligible union pairs); the maximum is 99 m in both scopes and also among all 173 identity-matched nonzero conflicts. All 84 eligible union timestamps agree after ISO normalization (Leg 1: 26, Leg 2: 58), including the 79 primary pairs (24, 55). No timestamp exclusion was added. Full conflicts and source hashes remain in depth_conflicts.csv and summary.json.

**Table S8. Exact common-concentration illustration for the original six training alpha values.** Let A = mean of four normalized concentrations and s = their OLS slope after dividing each by A. Then alpha = A × s. Multiplying all four concentrations by 1.10 leaves relative shape unchanged and changes the fixed prediction by 0.10 alpha/beta. This is an algebraic sensitivity, not evidence for the cause of GP15 residuals.

|Training depth m|Training alpha ×10⁸|Δpredicted pH at +10%|
|---|---|---|
|200–300|2.5649|-0.0643|
|300–400|2.7586|-0.0692|
|400–500|2.8339|-0.0711|
|500–600|2.9132|-0.0731|
|600–700|3.0248|-0.0758|
|700–800|3.2036|-0.0803|

Bottle-level A, s and alpha and both record-weighted/station-equal layer descriptions are retained in abundance_shape_bottles.csv and abundance_shape_layers.csv. For weighted layer means, mean(alpha)=mean(A)×s_of_mean_composition; in general mean(alpha)=mean(A)×mean(s)+covariance(A,s), so multiplying separately averaged A and s is insufficient. These algebraic components do not independently identify concentration supply, mixing, complexation or particle exchange.

## Source and analysis status

Tables S1–S3 retain original R8 results with presentation rounding. Tables S4–S8 derive from the root-frozen post-review diagnostic v2 and are explicitly exploratory/descriptive. Raw data, original frozen outputs and fixed model coefficients were not changed. Exact SHA, source-file locators and derived field mappings are in the adjacent figure-source-manifest.json. Full precision remains in the source CSV/JSON files.
