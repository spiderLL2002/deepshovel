# Argument comparison evaluation

Thirty fixed comparisons from five papers test matching conclusions, contradictions, changed conditions, method attribution, and unsupported transfers. Cases and acceptable relations were frozen before model execution; `source-check.json` supplies the source passages and checking metadata.

| Model input | Reference relation matches |
|---|---:|
| Claim and scope, three runs | 28/30; 28/30; 29/30 |
| Full recorded argument, three runs | 29/30; 30/30; 30/30 |
| Original source passages, three runs | 29/30; 29/30; 30/30 |

The passage control uses only the source-anchor quotations already present in each expanded argument, without its reasoning statements or dependency links. It uses the same query, claim, scope, prompt and model configuration; token counts are not matched. All repetitions use the same 30 frozen cases.

A fresh public clone ran `scripts/reproduce_paper.py --live` successfully: all ten research stages, 60 new model calls and 30 public queries completed. Every designated claim appeared in the search preview; 29/30 received an accepted relation in the final output. Median full-query time was 36.1 seconds. [Execution report](../../repro/paper-live-execution.json) and [summary](replication-summary.json) record the run.

The expanded one-command run at commit `2f1621f` also completed all stages, including 90 fresh model calls and 30 public queries. All 30 public queries returned an accepted target relation; median time was 33.2 seconds (range 28.5–58.5 seconds). The [complete execution report](../../repro/paper-live-complete-execution.json) and `public-reproduction-queries-2/` preserve this repetition.

All earlier runs remain available: `fixed-candidate-ablation/` uses the first prompt; `fixed-candidate-ablation-v2/`, `fixed-candidate-replication/` and `fixed-candidate-replication-2/` use the current relation prompt. Complete public query outputs are stored as compressed JSON, with expiring handoff tokens omitted. `source-passages-baseline/`, `source-passages-replication-1/` and `source-passages-replication-2/` contain the three passage-control runs.

From the repository root, recompute the preserved scores:

```sh
python3 artifact/semantic-evaluation/verify_results.py --all
```

To run new model comparisons, install the Compiler dependencies, provide `GPT_API_KEY` through your environment, and run:

```sh
export DEEPSHOVEL_REPO="$PWD"
compiler/.venv/bin/python artifact/semantic-evaluation/run_fixed_candidate_ablation.py --out runs/semantic-replicate
```

To repeat the public site's retrieval and final comparison:

```sh
python3 artifact/semantic-evaluation/run_semantic_live.py \
  --cases artifact/semantic-evaluation/semantic-cases.frozen.json \
  --out runs/semantic-live --workers 2
```

The paired experiment holds retrieval fixed. The public-site command retrieves candidates afresh and records target retrieval separately from relation correctness. See [execution details](FIXED-CANDIDATE-ABLATION.md) for the exact model invocation and output format.

## Inspect a cross-paper connection

In the released `q110-1` query, van Soelen et al. (2018) supplies the 1.6–8 kyr Fiskegrav ecosystem-collapse estimate. A separate Mettam et al. (2017) claim is returned as supplementary evidence: continued bioturbation after the last macrofossil distinguishes the locally defined extinction horizon from final ecosystem collapse. Users can inspect each argument and open its original page (PDF pages 7 and 2). The [source and runtime bindings](cross-paper-example.json) identify both papers, claims, dependency paths and the preserved public response.
