# Existing primary two-axis range labels

Already-exposed original inputs: deterministic SI display and reviewer QC. No new validation, fitting, sample exclusion, score replacement, measurement uncertainty model, geographical weighting or causal inference.

Labels describe the rectangle formed by extrema of the original six training alpha means and original six observed-pH means, with endpoints included. These are six global calibration-layer means, not the full feature range of the 1,755 source-compilation records or of all source bottles. They do not estimate a joint calibrated validity region, create new subset scores or establish prospective suitability; observed target pH is required to assign the second axis. Rows are layer means, not independent bottles.

Original alpha range: 2.564942791181893E-8 to 3.2035971215737773E-8.
Original observed-pH range: 7.584325607003087 to 7.73087871249336.

|Leg|Layer m|Original alpha label|Original observed-pH label|
|---|---|---|---|
|leg1|200-300|below|above|
|leg1|300-400|within|within|
|leg1|400-500|above|below|
|leg1|500-600|above|below|
|leg1|600-700|above|below|
|leg1|700-800|above|below|
|leg2|200-300|below|within|
|leg2|300-400|below|within|
|leg2|400-500|below|below|
|leg2|500-600|within|below|
|leg2|600-700|within|below|
|leg2|700-800|within|below|

## leg1

|Alpha range|Observed pH within|Observed pH outside|
|---|---|---|
|within|1|0|
|outside|0|5|

## leg2

|Alpha range|Observed pH within|Observed pH outside|
|---|---|---|
|within|0|3|
|outside|2|1|

## all_primary_rows

|Alpha range|Observed pH within|Observed pH outside|
|---|---|---|
|within|1|3|
|outside|2|6|
