# Post-exposure analytic envelope of the original hypothetical REE box

Post-exposure mathematical envelope for the declared exact-decimal fixed model, original shared four-element REE box and nine discrete pH-offset pairs only. No fitting, new independent validation, real measurement-error hard bound or joint-covariance propagation.

The decimal input tokens, released coefficients and original offsets are treated as exact rational numbers. This mathematical model approximates the legacy binary-float implementation to the recorded 10⁻¹⁰ pH matching tolerance; it is not formal verification of every IEEE operation. All original layer and corner matching records passed before any envelope was released.

For each of the nine discrete offset pairs, e_k(u)=e_k(0)+(t−o)+Σ_j C_kj u_j with −1≤u_j≤1. The four u values are shared coherently by all target bottles. The reference error is independent of u. RMSE is a convex norm of an affine vector, so its maximum is attained at a vertex. A finite vertex minimum does not establish an interior lower bound.

C has layer-common signs (+,+,−,−). If every exact component lower limit e_k(0)+(t−o)−Σ_j|C_kj| is strictly positive, u=(−1,−1,+1,+1) simultaneously minimizes all errors in the positive orthant and hence their norm. Only then is the minimum below certified; otherwise it is unavailable under this criterion. The exact MSE difference has the same sign as the RMSE difference. Fractions determine certificates; 50-digit Decimal square roots are display values only.

|Leg|o|t|Positive-error condition|Lower ΔRMSE display|Upper ΔRMSE display|Strict ranking certificate|
|---|---|---|---|---|---|---|
|leg1|0|-0.02|no|unproved|-0.048298|model_better_throughout_box|
|leg1|0|0|no|unproved|-0.044461|model_better_throughout_box|
|leg1|0|0.02|no|unproved|-0.041274|model_better_throughout_box|
|leg1|-0.0043|-0.02|no|unproved|-0.047412|model_better_throughout_box|
|leg1|-0.0043|0|no|unproved|-0.043726|model_better_throughout_box|
|leg1|-0.0043|0.02|no|unproved|-0.040662|model_better_throughout_box|
|leg1|-0.0125|-0.02|no|unproved|-0.045817|model_better_throughout_box|
|leg1|-0.0125|0|no|unproved|-0.042401|model_better_throughout_box|
|leg1|-0.0125|0.02|no|unproved|-0.039556|model_better_throughout_box|
|leg2|0|-0.02|yes|+0.070726|+0.143504|reference_better_throughout_box|
|leg2|0|0|yes|+0.071794|+0.145507|reference_better_throughout_box|
|leg2|0|0.02|yes|+0.072411|+0.146859|reference_better_throughout_box|
|leg2|-0.0043|-0.02|yes|+0.071009|+0.144008|reference_better_throughout_box|
|leg2|-0.0043|0|yes|+0.071955|+0.145842|reference_better_throughout_box|
|leg2|-0.0043|0.02|yes|+0.072506|+0.147092|reference_better_throughout_box|
|leg2|-0.0125|-0.02|yes|+0.071461|+0.144851|reference_better_throughout_box|
|leg2|-0.0125|0|yes|+0.072217|+0.146409|reference_better_throughout_box|
|leg2|-0.0125|0.02|yes|+0.072660|+0.147492|reference_better_throughout_box|

The SI table rounds displayed RMSE differences to six decimals; CSV uses 50 significant digits. Displayed square-root values are approximations, not directed-rounded bound endpoints; the exact MSE certificates determine ranking. The accompanying CSV/JSON retains exact rational MSE bounds/differences and complete component margins. The certificate covers the continuous four-coordinate REE box only at the nine original discrete pH choices. No continuous pH interval, alternative covariance structure, sample-specific random errors, coefficient propagation, causal attribution or real-world error bound is established. Unproved minima remain blank; no sub-box or new optimization is used.

The program author is reviewer B. Author checks are not independent numerical validation; a separate source/code/results review is required before this output can be adopted in the manuscript.
