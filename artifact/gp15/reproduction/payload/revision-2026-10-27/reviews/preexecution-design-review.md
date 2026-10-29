# Independent major revision design review

This review reconsiders the paper from its actual finite empirical target. It does not inherit the previous writing-gate approval. No new response calculations were performed.

The user review identifies addressable design interpretation issues rather than an algebraic or unit failure. The core fixed-model versus fixed-baseline comparison can remain scientifically useful without resolving the causal source of the station-block contrast. Its practical implication must be the insufficiency of depth-range matching alone on these observations, not a general rule locating where the proxy works.

## Required evidence before submission readiness

### R1 Contribution and operational use

The text identifies the new result as opposite error ordering of one unchanged model against one unchanged training-depth baseline on actual matched support. Depth-range agreement alone is not sufficient for improvement. No criterion predicting performance in another sea is established. Original regional precedents and coarse1–2pH ancient objective remain explicit.

### R2 Finite sampled-support estimand

Primary estimand is six equal-weight sampled layer means from eligible matched bottles, not area-weighted regional means or a coherent geographic vertical profile. Different stations across layers and inclusive duplicate boundary memberships are explicit in abstract/methods/results/captions as needed.

### R3 Station map and support matrix

Source-bound coordinate map and station-by-six-layer bottle-count matrix show all primary stations including empty cells. Counts sum to original28/63memberships and unique24/55bottles; map cannot imply representative area coverage. Station coordinate choice and latitude range perlayer are traceable.

### R4 Common-station diagnostic

Intersection is based on presence in all six primary inclusive layers and uses equal-station means in each layer. Both legs and both stations perleg retained; no pvalues/bootstrap/population validation. This is post-review exploration and does not replace primary support or repair regional representativeness.

### R5 Bias decomposition

Use exactly the six original equal-layer-weight errors and divisor6: MSE=bias²+mean((e-bias)²), with identity residual recorded. Report both methods and both legs, centered RMSE and bias²/MSE. Centering is diagnostic, not target-trained corrected validation. Remaining mismatch is not pure vertical shape because geography changes across layers.

### R6 Depth conflict disclosure

Delete slightly and report sample14984, station31/event6912, depths360/261m, matching bottle/time and accepted flags with source rows. PrimarypHdepth is operational, not adjudicated true depth; alternateREEdepth uses frozen calculation on matched support. Maxdifference scope must be stated; no unmeasured true-depth claim.

### R7 Abundance and relative shape

Define normalized concentrationsRj, A=mean4(Rj), s=slope(Rj/A), alpha=A*s exactly. The10%common multiplier gives delta_pH=0.1alpha/beta with signs and originaltrainingrange. It is algebraic sensitivity, not measured uncertainty or causal attribution. If layer values use meanvector, documentweights; meanalpha must not be replaced by meanA*means withoutcovariance.

### R8 Training pH provenance

Original selection is nonmissing G2phts25p0 and not a direct-measurement-only filter; merged GLODAP may include carbonate-system-derivedpH. No unverified calculated fraction is asserted. Keep targetscale25C,total,ambient-pressure consistent withGP15.

### R9 Precision and abstract emphasis

Reader-facing errors normally rounded to3–4decimalplaces, with completeprecision files retained. Abstract need not count144scenarios; finite scenarios do not create independent validations or samples.

### R10 Frozen evidence and exploration boundary

Original R1–8 frozen inputs/code/config/results remain byteunchanged. New protocol/code/inputbindings are fixed before execution, marked post-reviewexploratory because targetresponses alreadyexposed. No newfit, clippedpredictions, favorable subset or restoredindependent-holdout label.

### R11 Numerical and source verification

Independent numerical implementation or direct original-row checks verify newmetrics and support, not merely importnewprogram. Input/version/hash andexact source locators preserved. Allpositive/negative/nonestimableresults retained.

### R12 Review closure and submission readiness

At least two substantive review iterations compare exactstablemanuscript/outputhashes and track eachmajorpoint to change/evidence/limitation. A clean review is scopedjudgment, not journalacceptance or perfection. Any unresolvedscientific blocker prevents declaring submission-ready; ordinaryauthors/fundingmetadata are not invented.

## Implementation cautions

The abundance-shape identity must be applied at a declared aggregation level. For bottle-level values, mean(alpha)=mean(A*s), generally not mean(A)mean(s). A layer identity can instead be constructed from the identically weighted four-element mean vector. The common concentration multiplier illustration should not be presented as a measured GP15 effect or explanatory mechanism.

The centered-error diagnostic retains the changing geographic composition of the six layers. It separates mean error from remaining sampled-layer mismatch; it does not recover a geographically complete vertical profile. Bias-square share is a share of MSE, not explained variance and not a causal attribution to analytical bias.

The reported99mdepth discrepancy must not be described as the maximum over every original source record unless that exact scope has been checked. A maximum among eligible paired200–800m union records is a different statement.

The common-station diagnostic was supplied after the original responses were known. Freezing the new computation improves traceability but does not retrospectively preregister that result or create an independent validation set.

## Initial disposition

Major revision is warranted. No additional new-cruise requirement follows for the explicitly finite descriptive claim. The diagnostic program must still be statically reviewed and independently recomputed after root freeze; this criteria document is not an approval of unexecuted numerical results.
