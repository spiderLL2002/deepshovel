# Recorded argument inputs at the comparison boundary

This directory verifies a **public-source fix made on 29 October 2026**. It does not describe the earlier public service as already having this behavior. The original claim-detail expander and the five source modules under `../vendor/` remain unchanged. The fix changes the subsequent comparison projection and its Python model-input boundary.

## Run

From the repository root, with Node.js 22.13+ and the Python dependencies in `repro/requirements.lock`:

```sh
python artifact/arguments/comparison-input/verify_comparison_input.py --out runs/comparison-input
```

There are no live model, service, database or network calls. The script invokes the actual TypeScript projection, validates its JSON using the actual Python request schema, and invokes the actual two-stage comparison analyzer with a recording stub replacing only the model transport. That stub returns explicitly synthetic abstentions. It measures input retention and deterministic guards, not model reasoning quality.

The [saved report](runs/2026-10-29-public-fix/report.json), [actual F2 request](runs/2026-10-29-public-fix/f2-request.json), and [both recorded model inputs](runs/2026-10-29-public-fix/f2-model-inputs.json) are new executions. They do not overwrite the earlier production-expander results in the parent package.

## What changed

The unchanged F2 closure contains 41 entities and 37 edges. The former comparison projection retained 35 evidence entities, dropped all three assumption nodes, and sent no dependency edges or completeness status. The new projection retains **38 evidence entities, including the three assumptions, and all 37 typed edges** (18 `premise_for`, three `assumption_for`). The root Claim is represented separately on the comparison side, while artifact and version records remain source provenance.

Assumptions retain their own identity, `kind=assumption`, type, explicitness, Scope, statement and anchors. They remain conditions of an inference and are excluded from raw-evidence eligibility. Their source-anchor text is not used as a substitute for their role. Typed edge endpoints refer to retained entities or the root Claim.

The Python schema accepts and validates these fields. Both model stages receive the dependency context. The verifier still may cite only the first stage's frozen `selected_evidence_ids`, but now sees unselected premises and assumptions as context rather than silently deleting them. This retains the existing evidence-selection constraint while making a selected source's recorded conditions inspectable.

`evidence_completeness` records path-depth truncation, the 256-entity limit, omitted entity and edge counts, and bounded-content truncation. Empty textual viewpoints use `not_applicable`; old request formats without this information are explicitly `unknown`. `complete` means the recorded argument input survived projection; it does not mean extraction found every scientific premise.

When either side is `truncated`, the model's output schema, Python pair-bound validator, and Web response validator require `insufficient_to_determine`. The API also returns per-pair `comparisonInputs` and an existing-UI-compatible warning, including when abstention leaves no displayed relation. The fix does not add a general token-budget policy.

The comparison processor, extractor prompt and verifier prompt are versioned `1.1.0`. Request run fingerprints and frozen comparison identities bind the complete serialized sides, including assumptions, edges and completeness; the check confirms that changing an edge changes the frozen comparison ID. The inspected comparison path calls the analyzer directly and has no result cache keyed only by the unchanged pair label. Old `1.0.0` comparison envelopes fail the updated Web provenance check.

## Bounded controls

| Input | Retained entities | Retained edges | Result |
|---|---:|---:|---|
| Actual F2, default depth 32 | 38 | 37 | All three assumptions and all recorded dependency edges reach both model stages |
| Same graph, controlled depth 2 | 6 | 4 | Depth truncation survives serialization; a strong relation is rejected |
| Synthetic draft with 260 duplicated observation inputs | 256 of 298 | 236 of 557 | 42 omitted entities and 321 omitted edges are explicit; a strong relation is rejected |

The budget input has a distinct control snapshot label and no original snapshot manifest. It is a software boundary control, not another scientific observation. Four additional negative controls reject a missing assumption endpoint, an assumption recast as reasoning, an assumption used alone as raw support, and omissions falsely labeled complete. The three main cases verify both actual analyzer payloads, including assumptions absent from the first stage's selected evidence IDs.

One recorded paper and one claim are covered here. The earlier seven-claim expander check remains separate. An extraction that never recorded a scientifically necessary premise can still be structurally valid; this change does not add premise discovery or automatic scientific truth verification.

## Attribution

The unchanged source-derived F2 fixture is attributed to Taghipour et al. (2019), *Petroleum Science*, [DOI 10.1007/s12182-019-0331-9](https://doi.org/10.1007/s12182-019-0331-9), CC BY 4.0, as documented in the parent package and repository notices. Code and accompanying author-produced checks use the repository's AGPL-3.0-only license. No private checkout was changed or deployed.
