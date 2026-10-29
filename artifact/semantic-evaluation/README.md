# Argument comparison evaluation

Thirty fixed comparisons from five papers test matching conclusions, contradictions, changed conditions, method attribution, and unsupported transfers. Cases and acceptable relations were frozen before model execution; `source-check.json` supplies the source passages and checking metadata.

| Model input | Reference relation matches |
|---|---:|
| Claim and scope, two runs | 28/30; 28/30 |
| Full recorded argument, two runs | 29/30; 30/30 |
| Original source passages, one run | 29/30 |

The passage control uses only the source-anchor quotations already present in each expanded argument, without its reasoning statements or dependency links. It uses the same query, claim, scope, prompt and model configuration; token counts are not matched. The two repeated conditions use the same 30 cases.

A fresh public clone ran `scripts/reproduce_paper.py --live` successfully: all ten research stages, 60 new model calls and 30 public queries completed. Every designated claim appeared in the search preview; 29/30 received an accepted relation in the final output. Median full-query time was 36.1 seconds. [Execution report](../../repro/paper-live-execution.json) and [summary](replication-summary.json) record the run.

All earlier runs remain available: `fixed-candidate-ablation/` uses the first prompt; `fixed-candidate-ablation-v2/` and `fixed-candidate-replication/` use the current relation prompt. Complete public query outputs are stored as compressed JSON, with expiring handoff tokens omitted. `source-passages-baseline/` contains its frozen inputs and original outputs.

From the repository root, recompute the preserved scores:

```sh
python3 artifact/semantic-evaluation/verify_results.py
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
