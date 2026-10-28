# Protocol-correct same-host case study

Three distinct questions were tested serially through the already-running same-host service on 2026-10-27. This is not a public-network success: the separately saved public attempts received Cloudflare403. The preliminary same-host default requests were rejected because the interactive client requires a preview handoff. Those attempts remain unchanged. This run corrects the protocol following the real browser UI (evidence-search.tsx), rather than inventing a successful result or adding independent queries.

Each question used protocol evidence-retrieval/2.3, cross_paper, claim_search and limit50, first preview then final ai_rerank with the returned handoff and exact preview candidate references. The optional fast non-model UI preview was not called. No client application timeout or automatic retries were used. The server may use its existing internal GPT request hedging and fallback policy; these receipts do not enumerate internal provider attempts. Files retain complete NDJSON and receipt bodies. Opaque handoff session handles are internal run artifacts and need not be published.

| Case | Planned preview seconds | Final request seconds | Total to final seconds | Preview claims | Final clusters |
|---|---:|---:|---:|---:|---:|
| 1 | 21.194 | 13.350 | 34.554 | 30 | 1 |
| 2 | 14.423 | 12.534 | 26.970 | 30 | 3 |
| 3 | 20.245 | 28.871 | 49.129 | 30 | 2 |

All six requests returned outer HTTP200 and final NDJSON result status200, with application ok=true. Each preview returned30 candidates. Each final relation channel reports attempted=true, status=ok, provider=gpt, returned30; its separate rerank channel reports skipped. Thus the observed implementation runs model relation analysis but does not report a separate cross-encoder stage. Each planned preview reports lexical/dense retrieval, while model relation/rerank are skipped until final. These are three one-off functional timings on a warm existing service, not a latency benchmark or user-task timing. No source-extraction job was run.

## What the results actually establish

- Case1 asks the time-series offset after24h. It returns one final cluster labeled **related**, scopeRelation=overlap, with the merged-scope Nd claim reporting −0.113±0.030‰ and source pages8,15,3. It does **not** return the requested time-series estimate −0.098±0.012‰ as an answer. Do not equate successful HTTP completion or resultStatus=matches with scientific answer correctness.
- Case2 asks the variable-loading 48–144h estimate. The same claim ID is now labeled **supports**, scopeRelation=overlap, and leads three clusters. Its −0.113±0.030‰ value corresponds to the author's loading-series quantity; the source's numeric range and exact definition still require source inspection. Two other clusters are related material.
- Case3 asks whether stable isotopic offset implies adsorption has stopped. It returns two **related** Nd-paper clusters, including the same −0.113 claim and a seawater-speciation modeling claim. It does not return a direct yes/no answer or the uptake observations needed to answer. This is a limitation of current retrieval coverage, not evidence that the model concluded adsorption stopped.

The same claim identifier appears in all three outputs. Its stored time scope combines “24–168h for the time series” and “at least48h” for variable-adsorption experiments. All three queryPlan.scopeConstraints are null; conditions remain in the original question/compiled task text. The results therefore show a **query-relative relation change (related→supports)** for a shared candidate, not disjoint scope-bound candidate sets or hard experimental-series exclusion. The source paper is W7165146192.pdf, and the shared claim ID is claim:sha256:7b42ede80ffdd60acbe7910e22b552d44a84622e8e373a09febbeed914422e1c. Exact scopes, source identities, anchor pages and saved shortest paths are in summary.json and full receipts. Paths are returned metadata; this run did not independently revalidate every source quote.

## Configuration and disclosure

Source config requests alias gpt-6-luna, with deepseek-v4-flash as backup. The current interactive planning/ranking schemas request medium reasoning in gpt_responses.py. These are configured aliases/settings; public receipts expose only planner/provider label gpt and do not establish the underlying model identity or deployment revision. No provider credentials were read or printed. Existing service code/model settings and public Access policy were not modified.

Suggested paper sentence: “In three fresh serial same-host cases, planned preview completed in14.4–21.2s and final results in27.0–49.1s total. The shared Nd claim was classified as related for the time-series question and supporting for the loading-series question; the uptake question produced related evidence only. This demonstrates query-relative presentation, while exposing incomplete experiment separation and answer coverage. Public POST attempts from the test environment were blocked by Cloudflare.”

This small scripted case study is neither an accuracy estimate nor an audience study; do not report3/3 scientific success. The completed two-stage calls are protocol corrections for the original three queries and do not erase the preceding failures or define an availability rate.
