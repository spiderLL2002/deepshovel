# Original ordered selection flow

Already-exposed original inputs: deterministic SI display and reviewer QC. No new validation, fitting, sample exclusion, score replacement, measurement uncertainty model, geographical weighting or causal inference.

One terminal status is assigned to each sample-ID union record by the original if/elif order. Terminal counts partition records, but possible failure causes are not mutually exclusive. Later causes were not evaluated after an earlier assignment. This exporter copies statuses, not their causes.

Raw record and sample-ID counts describe these files; file count is not independent-study count. GeoFish rows can be present before eligibility. A layer membership can repeat the same boundary bottle.

## leg1

|Metric / original status|Unit|Count|
|---|---|---|
|raw_ree_data_records|CSV data record|372|
|raw_ph_data_records|CSV data record|157|
|raw_ree_unique_sample_IDs|unique source sample ID|372|
|raw_ph_unique_sample_IDs|unique source sample ID|157|
|raw_sample_ID_union|unique sample ID|446|
|raw_shared_sample_IDs|unique sample ID|83|
|raw_ree_only_sample_IDs|unique sample ID|289|
|raw_ph_only_sample_IDs|unique sample ID|74|
|not_shared_sample_id|unique audit sample ID|363|
|station_or_event_mismatch|unique audit sample ID|0|
|leg2_station_or_sample_seen_in_leg1|unique audit sample ID|0|
|missing_finite_depth|unique audit sample ID|0|
|outside_predeclared_depth_union|unique audit sample ID|57|
|bottle_flag_not_allowed|unique audit sample ID|0|
|missing_or_invalid_response|unique audit sample ID|0|
|eligible_in_at_least_one_depth_definition|unique audit sample ID|26|
|eligible_depth_union_unique_bottles|unique paired parent-bottle ID|26|
|primary_unique_bottles|unique paired parent-bottle ID|24|
|primary_layer_memberships|sample-layer membership|28|

## leg2

|Metric / original status|Unit|Count|
|---|---|---|
|raw_ree_data_records|CSV data record|472|
|raw_ph_data_records|CSV data record|190|
|raw_ree_unique_sample_IDs|unique source sample ID|472|
|raw_ph_unique_sample_IDs|unique source sample ID|190|
|raw_sample_ID_union|unique sample ID|516|
|raw_shared_sample_IDs|unique sample ID|146|
|raw_ree_only_sample_IDs|unique sample ID|326|
|raw_ph_only_sample_IDs|unique sample ID|44|
|not_shared_sample_id|unique audit sample ID|370|
|station_or_event_mismatch|unique audit sample ID|0|
|leg2_station_or_sample_seen_in_leg1|unique audit sample ID|0|
|missing_finite_depth|unique audit sample ID|0|
|outside_predeclared_depth_union|unique audit sample ID|83|
|bottle_flag_not_allowed|unique audit sample ID|5|
|missing_or_invalid_response|unique audit sample ID|0|
|eligible_in_at_least_one_depth_definition|unique audit sample ID|58|
|eligible_depth_union_unique_bottles|unique paired parent-bottle ID|58|
|primary_unique_bottles|unique paired parent-bottle ID|55|
|primary_layer_memberships|sample-layer membership|63|

The entering/unassigned columns in CSV describe the ordered partition of original terminal assignments. They are not new independent cause-specific failure rates.
