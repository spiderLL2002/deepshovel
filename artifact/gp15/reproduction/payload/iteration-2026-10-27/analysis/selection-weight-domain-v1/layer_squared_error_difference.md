# Fixed primary layer squared-error differences

Already-exposed original inputs: deterministic SI display and reviewer QC. No new validation, fitting, sample exclusion, score replacement, measurement uncertainty model, geographical weighting or causal inference.

For the six fixed primary layer means only, let d_k=e_model,k^2-e_reference,k^2. For arbitrary w_k>=0 with sum(w_k)=1, the weighted MSE difference is sum(w_k*d_k); weighted RMSE has the same ordering because square root is increasing. The exporter does not choose or optimize weights or generate a new weighted score. This is about stored point errors, not the RSD scenarios, an uncertainty box, area-weighted geography, new intra-layer weights or bottle-level errors.

Exact arithmetic below is on the serialized decimal signed-error tokens. It does not increase measurement precision or claim exact physical error.

## leg1

Signs are mixed. Weight concentrated on a positive layer favors the reference, while weight concentrated on a negative layer favors the model. No all-depth-weight ordering follows.

|Layer m|Sign of d|d in pH-units squared (display)|
|---|---|---|
|200-300|positive|1.659741E-3|
|300-400|positive|2.655083E-3|
|400-500|negative|-1.606867E-2|
|500-600|negative|-3.039721E-2|
|600-700|negative|-5.636376E-2|
|700-800|negative|-4.547654E-2|

## leg2

Every d_k is strictly positive. For every nonnegative normalized weight vector on these six unchanged layer means, model weighted MSE and weighted RMSE are strictly greater than the reference.

|Layer m|Sign of d|d in pH-units squared (display)|
|---|---|---|
|200-300|positive|7.284604E-2|
|300-400|positive|5.367510E-2|
|400-500|positive|5.319675E-2|
|500-600|positive|2.076454E-2|
|600-700|positive|6.935008E-3|
|700-800|positive|5.446573E-3|

CSV retains exact terminating decimals and numerator/denominator, along with original signed-error tokens. No scenario or bottle errors are rescored.
