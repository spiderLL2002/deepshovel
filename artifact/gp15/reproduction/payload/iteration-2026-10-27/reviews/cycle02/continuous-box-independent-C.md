# Independent continuous-box numerical review - C

Status: PASS. Same cycle02 scientific check, not new validation or another review cycle.

Construction uses four original raw CSVs, the original PAAS/mass rows and original frozen protocol. The B module was read for its contract but not imported, executed or used as a construction function. C coefficients come from symmetric element perturbations through an independent generic OLS route. Complete corner predictions are evaluated directly from changed concentrations.

Exact rational arithmetic controls equality and certificate signs. Legacy agreement uses a fixed 1e-10 pH tolerance; square-root differences are checked with independent rational repeated-squaring inequalities. Decimal90 errors are display values only.

|Comparison group|Checked|Mismatches|Maximum absolute displayed error|
|---|---|---|---|
|raw_primary_support|2|0|0|
|original_primary_layer_membership|12|0|0|
|independent_affine_identity|192|0|0|
|comparison_key_coverage|1|0|0|
|legacy_primary_identity|60|0|1.36893045530299492412398668847977659972174560016845536666921168540769484408E-15|
|B_matching_enclosure_and_legacy_arithmetic|6108|0|0|
|legacy_corner_layer|576|0|1.88568800548164494533132553293933591536871928778700512817975045865072189225E-15|
|legacy_corner_shifted_error|3456|0|1.94712962103410704605751841003540307019661565179334016578009057473934961930556E-15|
|legacy_corner_aggregate|2016|0|1.2657918756929066780292735081267364278418254340397394716635222763481683139397E-15|
|B_layer_membership|12|0|0|
|B_all48_C|48|0|0|
|B_all48_mean_R|48|0|0|
|B_layer_scalar_exact|60|0|0|
|B_all108_component_lower|108|0|0|
|B_all18_pair_certificates|18|0|0|
|B_all18_envelope_exact_fields|18|0|0|
|B_output_manifest_hash|7|0|0|
|B_frozen_input_hash|6|0|0|
|B_frozen_script_hash|1|0|0|
|B_source_manifest_counts|1|0|0|

Raw primary counts: {'leg1': 24, 'leg2': 55}; memberships: {'leg1': 28, 'leg2': 63}.
Pair ranking counts: {'leg1': {'model_better_throughout_box': 9}, 'leg2': {'reference_better_throughout_box': 9}}.

The maximum of the squared norm of an affine residual vector over a box is attained at a vertex by convexity. A minimum among vertices alone is insufficient. When all component residual lower limits are positive and their minimizing-vertex sets intersect, monotonicity in the positive orthant supplies a global minimum. The checker verifies those conditions independently.

Leg1: a missing minimum proof does not defeat a strictly negative upper MSE difference; that upper bound alone can certify the model is better throughout the declared box. Leg2: the positive component criterion and a strictly positive minimum MSE difference certify the opposite ranking. Actual pair results are in JSON.

Exact input/checker hashes, all 18 pair margins, errors/tolerances and any failures are retained in JSON and the comparison CSV. No B frozen output, manuscript, state or original source was changed. No bootstrap was repeated.
