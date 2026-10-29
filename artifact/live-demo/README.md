# English walkthrough of the running DeepShovel service

Browser captures from **29 October 2026** at https://deepshovel.iiot-shxt.xyz/. Full views accompany the crops used in the paper.

## Live sequence (allow about 3–5 minutes for a guided visit)

The time allocation is a presentation plan, not a latency measurement. New retrieval uses the connected service; current model outputs may differ from saved ones.

1. Open **观点校验** (Claim verification). Paste the exact query below into **待核对观点** (Claim to check) and click **检索证据** (Retrieve evidence).
2. In the F2 result, click **展开原文与证据链** (Expand source and evidence chain), then **查看代表论文** (Open representative paper).
3. In **观点与证据** (Claims and evidence), select **CLAIM 04**, the F2 pressure claim. Its detail view lists eight observations, ten reasoning steps and three assumptions. The paper contains seven claims, fifteen observations and thirty source anchors in total; these are different scopes of counting.
4. Expand **3 项前提或测量假设** (Three premises or measurement assumptions). Inspect zero cohesion, friction coefficient 0.6 and the stress-sampling location. These are conditions for the modeled conclusion.
5. At the pressure observation, click **打开原始页** (Open original page). The actual PDF viewer opens page **11 of 14**. The pressure statement is in the right column below Figure 12. Use **放大** (Zoom in) or **打开完整原件** (Open full original) if desired.

Exact query:

> For fault F2 in the Asmari reservoir, the estimated maximum sustainable pore pressure during gas injection is 55–57 MPa under the assessed stress state, assuming zero cohesion and a friction coefficient of 0.6.

## Change a condition

Return to Claim verification and compare these two queries:

> For fault F2 in the Asmari reservoir, with zero cohesion and friction coefficient 0.6, the maximum sustainable pore pressure during gas injection is 55–57 MPa.

> For fault F2 in the Asmari reservoir, with zero cohesion and friction coefficient 0.4, the maximum sustainable pore pressure during gas injection is 55–57 MPa.

The latest recorded public run returned **equivalent** for 0.6 and **qualifies** for 0.4. Open the result's assumptions to locate the source coefficient of 0.6 and then follow its original-page link. [Requests and responses](../semantic-evaluation/f2-live/) preserve this pair; [evaluation instructions](../semantic-evaluation/README.md) explain how to issue fresh queries.

## Capture sequence

|Capture|Visible state|
|---|---|
|[01-results.jpg](01-results.jpg)|Live result list|
|[02-expanded-evidence.jpg](02-expanded-evidence.jpg)|Expanded source snippets and evidence chain|
|[03-claim-detail.jpg](03-claim-detail.jpg)|F2 claim, qualified support and original-page button|
|[04-assumptions.jpg](04-assumptions.jpg)|Reasoning steps and the expanded three assumptions|
|[06-source-page.jpg](06-source-page.jpg)|Loaded original PDF page 11/14 in the service viewer|

The accompanying `.txt` files preserve accessible UI text; `01-results.txt` is a short state diff, while the expanded/detail/page captures are full states. The initial 01–04 captures were taken around 10:54–10:57 China Standard Time. The repeat and final source-page capture were collected during the same day's verification. No timing from capture intervals is used as service performance evidence.

## Continue into executable evidence (offline)

- [Recorded-input completeness](../arguments/README.md): execute the unchanged production expander and compare all seven claims with an independent reference; inspect depth truncation and controlled missing inputs. The ordinary search preview and claim-detail expansion serve different purposes.
- [Research decision trace](../research-trace/README.md): follow the actual Hongo retrieval, original-source inspection and recorded rejection rationale. This is a historical saved trace, not a newly generated decision.
- [GP15 research reproduction](../gp15/README.md): inspect source-to-protocol-to-claim mappings and regenerate numerical outputs. Model calls are not needed to rerun the calculations.

At a conference, the presenter can request fresh counterevidence or a new research question through the connected service and agent; those calls require network access and configured services. Fixed records and deterministic checks remain usable offline after dependency/input setup. A full investigation is not promised to finish during the short demo slot.

## Source and interpretation

Taghipour et al. (2019), *Estimation of the current stress field and fault reactivation analysis in the Asmari reservoir, SW Iran*, Petroleum Science 16, 513–526, DOI https://doi.org/10.1007/s12182-019-0331-9, CC BY 4.0. Its PDF is also provided at [source.pdf](../second-case/source.pdf).

The conclusion is an approximate maximum sustainable pore pressure under the assessed fault/stress conditions. It is not a pressure increment, a statistical confidence interval, or a universally safe injection prescription. A historical source rectangle points to the wrong column despite the correct page and quotation; the complete original-page view enables checking that discrepancy. We preserve the recorded source data rather than silently correcting it for the demonstration.
