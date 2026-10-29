# Independent check of selection, depth-weight and domain displays — B

Result: **PASS**. All inputs are already exposed. This check is not a new review cycle or new validation.

I rebuilt the classification from four original CSVs without importing or copying C's exporter. The original if/elif gives one first-matching terminal category per union ID; the underlying causes need not be mutually exclusive. Zero terminal counts do not mean that later untested causes were absent.

|Leg|REE rows|pH rows|ID union|Shared IDs|Accepted union|Primary bottles|Memberships|
|---|---:|---:|---:|---:|---:|---:|---:|
|leg1|372|157|446|83|26|24|28|
|leg2|472|190|516|146|58|55|63|

All 962 original terminal classifications and C provenance rows were checked; all38 flow counts,36 flow ID sets, ordered entering/remaining counts and12 primary memberships were independently compared. Differences: **0**. Detailed per-field comparison counts and exact input/output SHA identities are in the JSON.

The12 squared-error differences were recalculated exactly from stored decimal error tokens with Fraction. Leg1 has two positive and four negative differences; Leg2 has six strictly positive differences. For nonnegative normalized depth weights on these unchanged six means, the weighted MSE difference is their weighted average. Hence Leg2's ordering holds for every such vector; Leg1 permits either sign. No weight vector or new weighted score was chosen.

The48-row domain source was restricted to its12 original primary rows. Both directional labels and within/outside booleans were rederived from the extrema of the six saved global training alpha/pH means. Leg1 cells (within/within,within/outside,outside/within,outside/outside) are1/0/0/5; Leg2 cells are0/3/2/1, total1/3/2/6. These ranges are not the full range of1755 source records and do not define a calibrated joint validity region. Observed target pH is required; there is no prospective suitability rule or new subset score.

I read the three Markdown explanations; their limits match these calculations. No bootstrap or continuous-box calculation was repeated. Root may use this evidence when drafting a later manuscript, which still needs its own complete-version review.

**Independence boundary:** I authored the continuous-box program. This independent check addresses C's selection/weight/domain display only and provides no independent validation of my continuous-box code/results.
