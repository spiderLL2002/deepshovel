# Regional transfer of a fixed rare earth element pH calibration along GEOTRACES GP15

## Abstract

A proposed rare-earth-element (REE) pH proxy relates a shale-normalized Sm–Gd–Dy–Er concentration slope to global subsurface pH means. We assess its fixed implementation for total-scale pH at 25°C using archived GEOTRACES GP15 observations, matching REE and pH by parent-bottle identity. Predictions and the original six-layer global pH reference were compared on identical eligible samples within 200–800 m. The primary support comprised 24 bottles at six stations on Leg 1 and 55 bottles at eight stations on Leg 2. Across-layer REE-model root-mean-square error was 0.070 pH units on Leg 1, below the fixed depth-reference error of 0.170; corresponding Leg 2 errors were 0.218 and 0.110. Mean model error was +0.0615 and +0.2108, respectively, with squared mean error accounting for 93.3% of Leg 2 model mean-square error. Station composition changed between layers, so the estimands are means on actual eligible samples, not area-weighted regional profiles. A post-review restriction to the two stations per leg represented in every layer retained the opposite ordering, but supplied no independent regional validation. Prespecified aggregation and conditional measurement scenarios also retained the ordering. These results diagnose transfer of the unchanged implementation relative to this particular depth reference. Preserving its original depth interval does not ensure a predictive benefit on the sampled support; criteria that could establish suitability elsewhere remain untested. The concentration-sensitive slope does not identify a mechanism or test the original coarse Precambrian objective.

**Keywords:** rare earth elements; seawater pH; GEOTRACES GP15; transfer diagnostics; sampling support; calibration

## 1 Introduction

Rare earth elements are related chemical tracers whose aqueous complexation and interactions with particles vary across the series. These properties have motivated environmental interpretations of their concentration patterns. Lin and Catling (2024) proposed estimating seawater pH from a slope through shale-normalized Sm, Gd, Dy and Er concentrations, connecting pH-dependent complexation and scavenging to relative heavy-REE enrichment. Their calibration used global means within six 100 m layers between 200 and 800 m, followed by applications to marine carbonates.

The global aggregation is important for application. The calibrated association describes depth-layer means assembled from separate REE and pH compilations, rather than a relationship fitted to paired individual bottles. A regional application therefore raises a practical question: does the fixed REE prediction outperform the pH profile already supplied by the original depth means when both are evaluated on new, matched sampling support? A favorable answer would establish a benefit relative to that reference in the tested observations; an unfavorable answer would identify a transfer limitation without requiring a new mechanism. We also distinguish the slope's concentration-scale dependence from its enrichment-trend interpretation (Lin and Catling, 2024, §2.1).

Several relevant qualifications were already explicit in the original study. Lin and Catling (2024) discussed the narrow modern calibration range, mixing, alkalinity, dissolved inorganic carbon and other controls. They compared regional seawater REE patterns with corresponding or nearby GLODAP pH and compared carbonate estimates with boron-based reconstructions. Neither regional comparison nor environmental dependence is a new finding here. Their Precambrian objective was also deliberately coarse, approximately 1–2 pH units, rather than the precision expected of a modern carbonate-system measurement.

We use archived GP15 dissolved REE and spectrophotometric pH records from two Pacific cruise legs (Shiller and Gilbert, 2024a, b; Cutter and Buckley, 2021a, b). Parent-bottle identities align the target measurements, and both the model and comparator remain fixed. We retain favorable and unfavorable results, describe which stations support each layer, and distinguish average prediction offset from the remaining across-layer error. The contribution is the direct comparison of two unchanged predictors on this matched support, beyond a regional REE-pattern illustration (Table S13). It supplies no advance regional selection rule, carbonate-preservation test or sediment-formation inference.

## 2 Data and methods

### 2 1 Measurements and source definitions

We used version 1 of the dissolved REE datasets, published in July 2024, and version 2 of the pH datasets, published in May 2021 (Table S1). The latter incorporates corrected sample identifiers. A current metadata check found the same four versions and CSV checksums. Original bytes, source rows, method documents and checksums are retained in the accompanying materials. No new measurements were generated.

The archived REE protocol describes 0.2 μm filtration, collection in cleaned high-density polyethylene bottles, enriched isotope spikes, offline SeaFAST preconcentration and high-resolution ICP-MS in low-resolution mode. pH was measured on separate, unfiltered aliquots from the same parent Go-Flo bottles using purified m-cresol purple. The reported pH is on the total scale at 25°C and ambient pressure, not in situ pH. Matching parent bottles does not make the two aliquots physically identical or remove their different filtration treatments.

The original pH reference came from the GLODAPv2.2022 merged product. Its published code retained G2phts25p0 values unequal to the missing sentinel −9999 under the stated depth and coordinate filters, without restricting the primary pH flag to directly measured values. GLODAP also includes pH calculated from two other measured carbonate-system variables, identified by primary flag 0 (Lauvset et al., 2022). The calculated fraction in the exact retained training subset has not been determined here. We therefore refer to original training pH values, rather than an exclusively direct-measurement subset.

GP15 records were joined by sample ID with station/event agreement checked. Eligibility required finite positive Sm, Gd, Dy and Er, finite pH, and flag 2 for all five. Bottle fields were used without combining surface GeoFish responses. Blank/nd tokens remained unavailable, without zero or detection-limit substitution; nd was not assumed to indicate quantitative censoring. Times were compared after ISO normalization. Table S15 reports the original ordered identity, depth and flag processing; terminal statuses partition sample IDs in the two-file union but do not identify independent exclusion causes. We did not interpolate, impute, clip predictions or exclude observations according to error.

### 2 2 Fixed implementation and comparator

We transferred the author's normalization and coefficients from code commit 99ebf142d6787361c7720b7a3e7c213fd213eddb without fitting. REE concentrations in pmol kg⁻¹ were multiplied by 10⁻¹⁵ to obtain mol g⁻¹. The fixed reference composition is Post-Archean Australian Shale (PAAS), using the author's supplied abundance file rather than an interchangeable generic shale. Its values were converted from μg g⁻¹ to mol g⁻¹ using the author's molar masses. Adopted shale abundances for Sm, Gd, Dy and Er were 6.884, 6.043, 5.325 and 3.075 μg g⁻¹, respectively.

For normalized concentrations Rⱼ at atomic numbers Zⱼ = 62, 64, 66 and 68, the ordinary least-squares slope with an intercept is

**(1)** α = Σⱼ [(Zⱼ − 65) Rⱼ] / 20.

The fixed prediction is

**(2)** Predicted pH = (α − b) / β,

where β = −3.9879452461785675 × 10⁻⁸ and b = 3.339192211653703 × 10⁻⁷. The original fit regressed six mean α values on six mean pH values and was then algebraically inverted. This released inverse is the tested implementation. Its affine mapping in the four concentrations makes averaging predictions equivalent to predicting the mean composition under identical weights. No regional intercept correction was estimated.

The comparator assigns the original stored global mean pH to each depth layer: 7.730879, 7.689871, 7.658636, 7.631553, 7.606188 and 7.584326, from shallowest to deepest. It is a fixed reference, not a fitted regional model or measured truth. Both predictions use the same target observations and weights. For layer k, the signed error is eₖ=Σᵢwₖᵢ(ŷᵢ−yᵢ), with wₖᵢ=1/nₖ for primary record means. The station-equal alternative uses wₖᵢ=1/(Jₖnⱼₖ) for a bottle in station j, with Jₖ occupied stations and nⱼₖ bottles at that station. RMSE=√(Σₖeₖ²/6) scores six equally weighted layer mean errors, not individual bottle errors. The reported difference is REE-model minus reference RMSE; a negative value favors the model. Means are arithmetic averages on the reported pH scale, matching the original calibration; no water-mixing or hydrogen-ion transformation is performed. Table S10 gives all primary means and signed errors; full precision remains in the result CSV.

### 2 3 Sampling support and depth conventions

The primary estimands are layer means on the actual eligible GP15 bottles in each leg. They are not area-weighted means, complete station profiles or estimates of a defined regional population. The station map and station-by-layer counts show how spatial composition changes with depth (Fig. 1; Table S4). Equal station weighting within an occupied layer cannot recover absent stations, and connecting layer means does not isolate a vertical gradient.

The depth reported in the pH file was chosen operationally before calculating the primary scores and applied identically to the observations and both predictions. This convention does not establish a more accurate physical sampling depth. The REE and pH depth fields can differ substantially: sample 14984 at station 31, event 6912, reports 360 m in the REE file and 261 m in the pH file. Its identifiers, normalized time and five quality flags agree, but its true depth has not been independently adjudicated. It enters 300–400 m under the REE field and 200–300 m under the pH field. Both reported values and source rows are preserved (Table S7). The same predeclared eligible depth-union pool is used, but the selected field determines in-range bottles and layer membership separately for each convention. REE-depth scoring includes 26 and 58 bottles, compared with 24 and 55 under primary pH depth; identical support applies between predictors within a convention, not across conventions.

Following the original code, primary intervals include both endpoints. An internal-boundary bottle can contribute to adjacent layers and does not become two independent observations. Three prespecified alternatives used half-open intervals with the last endpoint retained, REE-reported depth with inclusive intervals, and equal station means within primary layers. All six layers were displayed. At least three stations per layer was an occupancy criterion, not a representativeness test; weakly supported layers were flagged rather than discarded. Layer-level error metrics use equal layer weights.

Leg 1 was exploration-exposed during source inspection. Leg 2 joint responses were reserved until the original analysis was frozen, after metadata, flags, depth support and methods had been examined. Version-specific checks found no GP15 entry in the preserved training sources, but both 2018 legs share one GP15 campaign and analytical programme. Source non-overlap and reserved responses do not establish independent-study replication (Table S17). Both legs are now exposed; review-stage diagnostics are exploratory descriptions.

### 2 4 Error structure and sensitivity analyses

For six layer errors eₖ, we describe mean-square error using its exact decomposition

**(3)** MSE = mean(e)² + mean[(e − mean(e))²].

We report the mean error, its squared contribution and the square root of the centered term. Centering uses the observed target errors and is a diagnostic, not a validated bias correction. Because station composition varies between layers, centered error also cannot be interpreted solely as vertical profile-shape error.

The original dependence checks deleted each station in turn and generated 1,000 whole-station resamples, drawing six/eight labels uniformly with replacement on Legs 1/2. Summaries required at least six original stations and at least 90% complete-layer draws. Only draws retaining all six originally occupied layers were scored; the exact algorithm and seeds are given in Supplementary method S3. The resulting percentiles describe coverage-conditioned resampling, not nominal population confidence coverage. The post-review support diagnostic retained every station represented in all six primary layers, then averaged station means equally within each layer. With only two such stations per leg, no interval or independent regional-validation claim was assigned to this subset.

Conditional pH scenarios crossed observation shifts of 0, −0.0043 and −0.0125, motivated by reference-material differences, with original-reference shifts of −0.02, 0 and +0.02 motivated by GLODAP-scale qualifications. Reference shifts were applied equally to model and comparator. REE stress scenarios used coherent positive or negative changes at the maximum reference-water RSDs reported for Sm, Gd, Dy and Er: 4.1%, 5.0%, 4.0% and 3.9%. Sixteen elemental directions crossed with nine pH conditions yield 144 cases per leg. These are conditional scenarios, not sample-specific uncertainty distributions or real-error bounds. Supplementary method S4 specifies their sources and operators. A post-review calculation covers the intervening shared REE box at those nine discrete pH choices (Supplementary method S9). Coefficient uncertainty remains unpropagated.

For the four elements selected in the released model, let A be their mean normalized concentration and s the slope after division by A. These describe that subset, rather than total REE inventory or a full REE pattern. Then

**(4)** α = A s.

We checked this identity in the retained bottles and illustrated the fixed model's response to a common 10% concentration increase using the original six training slopes. This is not a causal partition of abundance and fractionation effects. For layer summaries, we used the slope of the weighted mean composition. The product of separately averaged A and s under the same weights generally omits their weighted covariance.

### 2 5 Reproducibility and AI assistance

Calculations were performed deterministically in Python 3.12 with versioned inputs, frozen configurations and source-to-result mappings. OpenAI Codex assisted literature retrieval, source checking, code development, plotting and manuscript preparation. Numerical results were obtained from the supplied Python calculations rather than generated values. Separate AI-agent implementations and source-linked review were used to check calculations and interpretation; these checks are not expert certification or journal peer review. Original analysis and post-review diagnostics remain separately identified in the reproducibility record. Reproducible scripts, dependencies and plot-source values accompany the article.

## 3 Results

### 3 1 Actual layer support and opposite error ordering

The primary support contained 24 unique bottles at six Leg 1 stations and 55 bottles at eight Leg 2 stations. Inclusive boundaries generated 28 and 63 layer memberships. The union under either reported-depth convention contained 26 and 58 eligible bottles; these are not the primary sample counts. All primary paired times agreed after normalization. Each primary layer had at least three stations, but its station set changed with depth (Fig. 1).

The Leg 1 stations span 19.681–53.677°N, yet its 500–600 and 700–800 m layers contain only stations 16, 18 and 18.3, at approximately 19.7–27°N. Shallower layers include stations farther north. Leg 2 support spans 20.000°S–18.906°N and is also incomplete by station. The means in Fig. 2 therefore describe changing eligible sample sets. Their common within-layer support makes the prediction comparison well-defined, but does not produce a complete regional vertical profile.

Across-layer model RMSE on Leg 1 was 0.070, compared with 0.170 for the fixed depth reference, a difference of −0.100 pH units. The reference was more accurate in the two shallowest layers and the REE model in the other four. On Leg 2, model and reference RMSE were 0.218 and 0.110, a difference of +0.108. Both predictions exceeded all six measured layer means, with the reference closer in every layer (Table S2; Fig. 2). Consequently, nonnegative depth weights on these same point means cannot reverse the Leg 2 ordering (Supplementary method S11).

### 3 2 Average offset and centered error

Leg 1 mean model error was +0.0615 and centered RMSE was 0.0339, compared with +0.1335 and 0.1054 for the reference. The REE model thus reduced both the average offset and the remaining across-layer error on this support. Leg 2 mean model error was +0.2108 and centered RMSE was 0.0565; reference values were +0.0992 and 0.0483. Squared mean error accounted for 93.3% of Leg 2 model MSE, compared with 80.8% for its reference (Fig. 3; Table S5).

The unfavorable Leg 2 transfer is therefore dominated by an overall positive offset, with a smaller additional difference in centered error. These are decompositions of the unchanged predictions. Removing the target-derived offset would not provide a new external performance estimate.

### 3 3 Prespecified checks and post-review diagnostics

All four fixed aggregation conventions preserved the opposite RMSE ordering: differences ranged from −0.128 to −0.089 on Leg 1 and +0.100 to +0.109 on Leg 2 (Fig. S1; Table S2). The half-open Leg 1 variant had only five layers meeting the three-station occupancy criterion; its weak layer was retained. Using the REE depth field, including relocation of sample 14984, retained the ordering.

No individual station deletion reversed it. Leg 1 deletion differences ranged from −0.108 to −0.088 and Leg 2 differences from +0.096 to +0.122. Usable whole-station resamples numbered 975 and 978 of 1,000, with conditional 2.5th–97.5th percentile ranges of −0.142 to −0.078 and +0.061 to +0.132, respectively. The finite cases retained the ordering (ranges −0.146 to −0.040 and +0.071 to +0.147). A post-review analytic envelope also established that ordering throughout the specified shared REE box at each original pH choice (Table S14). These checks do not bound real errors.

Only stations 16 and 18 on Leg 1 and stations 31 and 33 on Leg 2 occupied all six primary layers. The post-review equal-station restriction retained 10 and 21 unique bottles and yielded RMSE differences of −0.087 and +0.111, respectively (Table S6). The agreement in direction addresses this particular restriction but does not replace the primary comparison or establish representative regional sampling.

### 3 4 Calibration range and concentration sensitivity

The six global calibration-layer α means spanned 2.565–3.204 × 10⁻⁸, not the full individual-source range. Only the Leg 1 300–400 m mean lay inside both this interval and the six-product-pH-mean interval (7.584–7.731). Its REE absolute error was 0.061 versus 0.032 for the reference (Table S10). The last three Leg 2 α means were within range, but their measured pH was below the original pH interval. All twelve classifications are shown in Table S3 and Fig. S2. No exclusions or new subset scores were introduced; joint labels require observed target pH.

The identity α = A s was reproduced to floating-point precision. At all six nominal layers, lower Leg 2 A more than offset its higher s in the product A s, yielding lower α and, because β<0, higher fixed pH predictions (Table S12). These are separately sampled layer compositions, not matched water masses. A common 10% concentration increase preserves shape and changes fixed pH by 0.1α/β: a decrease of 0.064–0.080 across the original training slopes (Table S8). Neither the observed factor contrast nor this algebraic sensitivity identifies the cause of GP15 residuals.

## 4 Discussion

### 4 1 What the transfer diagnostic changes

The added empirical result is the reversal in error ordering between two sampled station groups when both the REE mapping and original depth reference remain unchanged. It sharpens an application question beyond the already known existence of environmental influences: selecting the original depth interval did not ensure that the published chemical predictor improved on its own training-depth profile in these observations. Both favorable and unfavorable transfer results are retained.

Application requires the target pH definition, eligible bottle/station/layer support and source categories to be explicit, followed by a comparison of unchanged predictors on common support. Here the ranges and error decomposition diagnose a predominantly positive Leg 2 prediction offset. They provide no pre-observation threshold for another sea area. Mechanism remains unresolved; Table S16 identifies observations that could test competing explanations.

The fixed depth reference supplies a reproducible comparator, rather than the best possible modern predictor. On the unchanged Leg 2 point means it has lower squared error in every layer, so any nonnegative normalized depth weights preserve its advantage (method S11). The separate REE-box argument keeps the original equal layer weights. These results do not establish joint invariance to arbitrary weights and measurement shifts. Recalibration would require unused responses for independent assessment.

### 4 2 Spatial support limits interpretation

The station-by-layer matrix makes clear why these results should not be called representative regional profiles. Different sets of stations contribute at different depths, so adjacent means combine vertical and spatial differences. Equal station weighting can alter the contribution of sampled stations but cannot recover missing ones. Individual deletions show that no single retained station alone determines the error ordering; they do not make the remaining stations a probability sample.

The common-station diagnostic holds station labels fixed within its small subset and preserves the direction of the primary comparison. Leg 2 station 31 spans events 6912/6922 and station 33 spans 6931/6940, with their contributions changing with depth (Table S11). The restriction therefore does not create single-event profiles. It also changes the sampled geography and uses only two stations per leg. It cannot distinguish all spatial-composition effects, establish regional coverage or supply a new confirmation after the target data and reviewer result were already known. The appropriate inference is limited to the scored observations and specified restrictions, with the full matrix available for others to assess.

For sample 14984, matching identity does not resolve 261 versus 360 m. The consistent ordering under both complete depth conventions limits one consequence of the discrepancy, but does not adjudicate physical depth or justify calling all field differences small (Table S7).

### 4 3 Error structure and the geochemical feature

On Leg 2, the model has both a larger positive offset and slightly larger centered error; Leg 1 improves both terms. This describes the unchanged predictor. Centering supplies neither a validated correction nor pure vertical-shape error because station/event composition and measurement effects remain combined.

Positive scaling preserves the direction of an enrichment trend but not the quantitative slope used in pH prediction. Lin and Catling (2024, §2.1) motivate the slope as indicating enrichment regardless of absolute concentration; their unlogged OLS feature satisfies α(λR)=λα(R) for λ>0. Fixed PAAS normalization therefore retains dependence on the four-element concentration scale. A 10% common increase shifts predicted pH by approximately −0.064 to −0.080 over the original training range while preserving all four-element ratios. This is an algebraic property of the published implementation, not a new fitted proxy or a measurement of GP15 analytical bias. It does not identify whether complexation, mixing, scavenging or supply produced the observed concentrations.

The domain labels concern six global calibration means, not a raw-observation training envelope or a calibrated joint validity region. Leg 1 improved the primary score despite feature-mean extrapolation, whereas within-range Leg 2 features did not ensure pH agreement. Even membership in both marginal intervals need not place a point on the fitted relation. These target-dependent, post-result labels cannot supply an advance acceptance rule or pointwise validation of the global calibration.

### 4 4 Uncertainty and wider geological use

Sensitivity to the chosen depth convention, weighting, individual stations and declared conditional perturbations did not change the ordering, but the checks do not exhaust uncertainty. Element fractionation can depend on analytical calibration and preconcentration procedures (Haryanto et al., 2025). Reference-water RSDs do not establish complete bias or covariance for every bottle. The Leg 1 pH report describes duplicate analyses of 12 Station 5 samples; three enter the primary paired support. Leg 2 reports single analyses without sample duplicates. This uneven replication cannot supply bottle-specific error distributions across either leg, and uniform reference-material shifts are conditional checks rather than measured bottle corrections. Shared cruise/laboratories, spatial dependence and unpropagated joint calibration uncertainty remain restrictions on inference.

The GLODAP merged product can contain measured and calculated pH, and the original selection did not distinguish these categories. Their actual composition in the retained training subset is not established here. A measured-only recalibration might change the original relationship, but it was not part of this test of the published implementation. General documentation of calculated pH cannot establish its fraction or effect in the retained training subset. The fixed-point comparison remains valid as an implementation benchmark, with the provenance definition stated explicitly.

Finally, the original ancient-ocean objective differs substantially from this modern diagnostic. Lin and Catling (2024) regarded uncertainty of approximately 1–2 pH units as potentially useful for Precambrian reconstruction. Modern RMSE of 0.218 neither exceeds nor tests that objective. This pH-at-25°C comparison does not directly test in-situ speciation responses. Dissolved GP15 records also do not assess carbonate partitioning, diagenesis or boron-based reconstructions. Other features and media, such as sedimentary Y/Ho (Liu et al., 2026), require their own evaluations. The present utility is to make the tested implementation, sampling support and observed failure mode explicit before transferring a global marine geochemical relation.

## 5 Conclusions

On the eligible GP15 layer means, fixed REE-model RMSE was 0.070 versus 0.170 for the inherited depth reference on Leg 1, and 0.218 versus 0.110 on Leg 2. The Leg 2 failure was predominantly a positive offset: squared mean error accounted for 93.3% of model MSE. Prespecified checks and the post-review common-station restriction retained the opposite ordering.

This supplies a finite counterexample to treating the original depth interval as sufficient for improvement over that reference. Changing station/event support limits the inference to sampled means; it does not establish a regional suitability criterion. The four-element slope combines concentration scale and relative shape, which must both inform its interpretation. Neither centering errors nor describing these factors identifies a mechanism or validates a correction. The practical contribution is a reproducible transfer diagnosis with explicit support and error structure; ancient carbonate reconstruction and the original coarse Precambrian objective remain untested.

## Data and code availability

All measurements are existing public BCO-DMO datasets listed in Table S1 and References, licensed CC BY 4.0. The original model code is available at the [fixed author commit](https://github.com/PingChunLin/REE_pH/tree/99ebf142d6787361c7720b7a3e7c213fd213eddb), with supporting material through [Dryad](https://doi.org/10.5061/dryad.69p8cz99r) and the linked [Zenodo record](https://zenodo.org/records/11557285). The supplied package contains versioned sources, exact row mappings, separate original and review-stage configurations, executable calculations, all reported scenarios, figures and plot-source values. Supplementary numerical files retain full precision. No newly minted public archive DOI is claimed.

## Declaration of generative AI and AI assisted technologies in manuscript preparation

OpenAI Codex assisted source retrieval and organization, development of reproducible analysis and plotting code, and drafting and revision of the manuscript. Its research use and the numerical checking workflow are described in Methods. AI-assisted internal reviews were used to identify errors and limitations and do not constitute independent expert certification or journal peer review. Source records and executable calculations accompany the work so that the scientific statements and outputs can be examined directly.

## References

Cutter, G. A., and Buckley, N. R. (2021a). Water column GeoFish and bottle pH data from Leg 1 (Seattle, WA to Hilo, HI) of the US GEOTRACES Pacific Meridional Transect (PMT) cruise (GP15, RR1814) on R/V Roger Revelle from September to October 2018. BCO-DMO, version 2, 2021-05-06. https://doi.org/10.26008/1912/bco-dmo.838157.2

Cutter, G. A., and Buckley, N. R. (2021b). Water column GeoFish and bottle pH data from Leg 2 (Hilo, HI to Papeete, French Polynesia) of the US GEOTRACES Pacific Meridional Transect (PMT) cruise (GP15, RR1815) on R/V Roger Revelle from October to November 2018. BCO-DMO, version 2, 2021-05-06. https://doi.org/10.26008/1912/bco-dmo.838173.2

Haryanto, M. J., Zhang, J., Alifia, Z., Kagaya, S., Horikawa, K., and Andreas, R. (2025). Unveiling method-derived element fractionation in determining seawater REEs using chelating resin and ICP-MS: Enhanced accuracy for finer-scale water mixing. *Talanta*, 287, 127614. https://doi.org/10.1016/j.talanta.2025.127614

Lauvset, S. K., Lange, N., Tanhua, T., Bittig, H. C., Olsen, A., Kozyr, A., Alin, S., Álvarez, M., Azetsu-Scott, K., Barbero, L., Becker, S., Brown, P. J., Carter, B. R., da Cunha, L. C., Feely, R. A., Hoppema, M., Humphreys, M. P., Ishii, M., Jeansson, E., Jiang, L.-Q., Jones, S. D., Lo Monaco, C., Murata, A., Müller, J. D., Pérez, F. F., Pfeil, B., Schirnick, C., Steinfeldt, R., Suzuki, T., Tilbrook, B., Ulfsbo, A., Velo, A., Woosley, R. J., and Key, R. M. (2022). GLODAPv2.2022: the latest version of the global interior ocean biogeochemical data product. *Earth System Science Data*, 14, 5543–5572. https://doi.org/10.5194/essd-14-5543-2022

Lin, P.-C., and Catling, D. C. (2024). The Rare Earth Element Distribution in Marine Carbonates as a Potential Proxy for Seawater pH on Early Earth. *American Journal of Science*, 324, 10. https://doi.org/10.2475/001c.118215

Liu, Y.-S., Li, W.-S., Zhang, J.-B., Lin, J., Yang, A., Zhou, L., Fan, D.-D., Zhu, X., and Zhang, Y.-H. (2026). Y/Ho ratios in marine sediments unveil Neoproterozoic ocean acidification. *Science Bulletin*, 71, 1204–1213. https://doi.org/10.1016/j.scib.2026.01.028

Shiller, A. M., and Gilbert, M. (2024a). Dissolved concentrations of rare earth elements (including Y) from Leg 1 of the US GEOTRACES Pacific Meridional Transect (PMT) cruise (GP15, RR1814) on R/V Roger Revelle from September to October 2018. BCO-DMO, version 1, 2024-07-11. https://doi.org/10.26008/1912/bco-dmo.932161.1

Shiller, A. M., and Gilbert, M. (2024b). Dissolved concentrations of rare earth elements (including Y) from Leg 2 (Hilo, HI to Papeete, French Polynesia) of the US GEOTRACES Pacific Meridional Transect (PMT) cruise (GP15, RR1815) on R/V Roger Revelle from October to November 2018. BCO-DMO, version 1, 2024-07-12. https://doi.org/10.26008/1912/bco-dmo.932559.1

## Figure captions

**Figure 1. Geography and actual sampling support.** The coordinate plot locates the eligible primary GP15 stations; representative plotted coordinates are summaries of recorded positions, whose full ranges remain in the source tables. The two station-by-layer matrices give eligible bottle counts under the primary pH-depth convention. Zero cells indicate no eligible bottle in that station-layer combination, not zero chemical concentration. Internal-boundary bottles can enter neighboring inclusive layers, so cell totals are memberships rather than independent observations. Marked common station labels are represented in all six layers; their sampling events can differ (Table S11). The sampled geography changes with depth; no area weights, complete regional profiles or unmeasured stations are implied. Data plots were produced by reproducible Python code developed with Codex assistance.

**Figure 2. Prediction comparison on eligible sample-layer means.** Observed GP15 total-scale pH at 25°C, the unchanged REE prediction and the original global depth reference are plotted with the same axes for both legs. Each comparison uses identical bottles and weights within its layer. Connecting lines aid comparison of the sampled means; they do not identify purely vertical gradients because station membership changes between layers. The 24 and 55 unique bottles generate 28 and 63 memberships, respectively. The depth reference is a fixed comparator rather than measured regional truth. Data plots were produced by reproducible Python code developed with Codex assistance.

**Figure 3. Descriptive decomposition of prediction error.** Four bars show MSE of the REE model and fixed depth reference on each leg's six equally weighted primary means, split into squared mean error and centered MSE. The components sum by identity, not by a fitted explanatory model. Mean errors are positive for all four cases; squared mean error accounts for 93.3% of Leg 2 model MSE. The centered component uses target-derived errors and does not represent externally validated bias-corrected performance or pure vertical-shape error. Data plots were produced by reproducible Python code developed with Codex assistance.
