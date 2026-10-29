# Supplementary tables

**Table S1. Source versions and primary sampling support.** Unique bottles differ from layer memberships because the primary implementation includes both interval endpoints. All pH values are on the total scale at 25°C.

|Cruise leg|REE dataset and version|pH dataset and version|Unique primary bottles|Stations|Layer memberships|
|---|---|---|---:|---:|---:|
|Leg 1|BCO-DMO 932161, v1|BCO-DMO 838157, v2|24|6|28|
|Leg 2|BCO-DMO 932559, v1|BCO-DMO 838173, v2|55|8|63|

**Table S2. Across-layer predictive errors under all fixed aggregation conventions.** ΔRMSE is REE-model RMSE minus fixed-depth-baseline RMSE. Negative values favor the REE model. These are point summaries, not confidence limits.

|Leg|Convention|REE RMSE|Baseline RMSE|ΔRMSE|
|---|---|---:|---:|---:|
|1|Primary|0.070214|0.170084|−0.099870|
|1|Half-open intervals|0.075317|0.164210|−0.088893|
|1|REE-reported depth|0.053557|0.181700|−0.128143|
|1|Equal station weights|0.071488|0.174206|−0.102718|
|2|Primary|0.218271|0.110295|+0.107976|
|2|Half-open intervals|0.224225|0.114943|+0.109282|
|2|REE-reported depth|0.220173|0.120303|+0.099870|
|2|Equal station weights|0.218260|0.117675|+0.100585|

**Table S3. Descriptive position of primary layer means relative to the original training-alpha range.** Alpha is expressed in units of 10⁻⁸. The training range is 2.564943–3.203597 in these units. This post-result annotation neither defines a new subset nor changes the primary error calculation.

|Depth (m)|Leg 1 alpha|Domain position|Leg 1 observed pH|Leg 2 alpha|Domain position|Leg 2 observed pH|
|---|---:|---|---:|---:|---|---:|
|200–300|2.177232|Below|7.770460|1.613811|Below|7.696456|
|300–400|2.611308|Within|7.657633|1.979721|Below|7.639750|
|400–500|3.282676|Above|7.530350|2.340469|Below|7.514182|
|500–600|3.307087|Above|7.414267|2.674399|Within|7.520929|
|600–700|3.859232|Above|7.365420|2.981906|Within|7.436140|
|700–800|3.784343|Above|7.362233|3.036375|Within|7.499050|

