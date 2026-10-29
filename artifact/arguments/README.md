# Recorded-input completeness and scope evidence

This package executes the **existing DeepShovel claim-detail expander** on an actual saved Asmari graph. It adds a reference check and executable evidence, not a replacement graph-expansion algorithm or a new production feature.

## Run

Use Node.js 22.13 or newer. No package installation, credentials, model calls, database, or network access is required.

```sh
node --experimental-strip-types --no-warnings verify.mjs --out runs/local
```

The command verifies all vendored-source and fixture hashes, validates the recorded graph, executes the original module, compares its output with an independent fixed-point reference, and writes `report.json`, `f2-full-evidence-path.json`, and `f2-depth-2.json` under the requested output directory. The checked execution is saved in `runs/verified/`.

## What the real F2 example shows

The recorded conclusion is the approximate **55–57 MPa maximum sustainable pore pressure for F2 under the paper's modeled conditions**, not a pressure increment or a generally validated operational threshold. The source is Taghipour et al. (2019), *Petroleum Science*, [DOI 10.1007/s12182-019-0331-9](https://doi.org/10.1007/s12182-019-0331-9), licensed CC BY 4.0. The upstream source record preserves the correct page and quotation but a known wrong-column rectangle; this package leaves it unchanged.

`reason.f2.evidence` needs the recorded pressure/stability observation **and** agreement of the three analytical methods. It also has three assumption inputs: zero cohesion, friction 0.6, and stress-sampling location. The downstream `reason.f2.safety` depends on that step, current-stress synthesis, and critical-pressure transition. The full backward dependency set contains 18 `premise_for` and 3 `assumption_for` edges.

| Actual representation | Entities | Edges | Recorded premise edges retained | Recorded assumption edges retained |
|---|---:|---:|---:|---:|
| Saved search shortest preview | 7 | 6 | 1/18 | 1/3 |
| Historical auxiliary observation-reuse export | 33 | 33 | 17/18 | 0/3 |
| Existing claim-detail expander, default depth 32 | 41 | 37 | **18/18** | **3/3** |

These are three different implemented paths. The old auxiliary export is preserved as a comparison, including its missing edge; it is not used as evidence that the claim-detail expander loses that dependency. The original imported values, identifiers, and source records have not been edited to obtain the result.

The package also executes all seven claims in the same recorded paper. All seven full paths match an independently constructed reference edge set. For explicit depth limits 1, 2, and 3, all 21 bounded runs report `truncated=true`; 19 omit at least one recorded premise or assumption edge, and all 19 are flagged. The other two still omit deeper source/provenance content. These are parameter checks on seven claims from **one paper**, not 21 additional scientific examples.

## Missing-input and depth controls

Three controlled mutations of the real input are rejected by the unchanged structural validator: removing a referenced observation, removing a referenced assumption, and removing an observation's premise edge. Each actual error code is saved in the report.

One further counterexample is retained deliberately. An unpublished draft graph with an internal reasoning edge omitted can remain structurally valid. In that control, the old snapshot manifest is removed because the input represents a new draft, not an authenticated existing snapshot. A comparison with the frozen reference detects the omitted edge; the production validator cannot infer an unrecorded scientific dependency from graph structure alone. Thus `truncated=false` means the configured traversal did not stop early, not that extraction recovered every scientifically necessary premise.

## How this corresponds to the application

The vendored files are byte-identical to the source revision in `module-provenance.json` and carry the original AGPL-3.0-only license. Their only dependencies are other included source files.

- `lib/evidence-graph/paths.ts:104`: validates and indexes the source graph, then expands every incoming derivation edge and structural source reference. Claim-to-claim comparisons are opt-in. Default maximum depth is 32.
- `app/api/evidence-graphs/[snapshotId]/route.ts:105`: the real claim-detail API calls this expander for the graph's claims and returns the paths.
- `app/components/evidence-synthesis.tsx:452`: a truncated path is shown as a partial evidence chain with a direction to open paper details.
- `lib/server/retrieval-view.ts:67`: the separate search-preview compactor limits nodes, links, and optionally labels; the search route requests truncation tracking.
- `app/components/evidence-search.tsx:174`: a truncated search preview directs the reader to full paper details.

The test exercises depth-bounded traversal. It does **not** establish a token-budget policy or a general rule preventing a saved interpretation whenever a scientific premise is missing.

## Existing scope-sensitive run

`fixtures/scope-presentation.json` is a compact, quotation-free projection of three saved same-host requests from 27 October 2026. The same Nd candidate was returned as `related` for a time-series question, `supports` for a variable-loading question, and `related` for the question whether stable isotopic offset implies adsorption has stopped. The source hashes and original candidate/snapshot identifiers are retained.

This is an observed query-relative relation change. All three planner scope-constraint fields are null, and the candidate's recorded time scope combines experiments. It therefore demonstrates conditional presentation while exposing incomplete experimental-series separation. The last question did not receive the uptake evidence needed for a direct answer. No three-out-of-three accuracy claim is made.

Current implementation boundaries are visible in `compiler/compiler_service/retrieval_service.py`: `_scope_relation` treats different free-text scientific labels conservatively as unknown; a numeric non-overlapping time interval can establish disjointness. Around lines 4538–4545, a proposed contradiction with disjoint/unknown scope is downgraded to `related`, and equivalence is also guarded. Semantic relation proposals otherwise depend on the model and available evidence. This package rechecks the saved scope records; it does not replay the live model.

## Interpretation

The executable result directly supports preservation of **all recorded inputs** by the claim-detail expansion in this case, and explicit signaling of depth truncation. It does not measure extraction fidelity, scientific truth, corpus-wide reliability, expert agreement, or user benefit. Input completeness remains a source-reading and scientific-review question. The separate scientific-reproduction package checks the GP15 numerical analysis.
