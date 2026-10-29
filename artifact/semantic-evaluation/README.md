# Argument comparison evaluation

Thirty fixed comparisons from five papers test matching conclusions, contradictions, changed conditions, method attribution, and unsupported transfers. Cases and acceptable relations were frozen before model execution; `source-check.json` supplies the source passages and checking metadata.

| Model input | Matched reference relation | Mean model time |
|---|---:|---:|
| Claim and scope | 28/30 | 3.98 s |
| Same claim and scope, with its recorded argument | 29/30 | 3.53 s |

The current public-site run retrieved the designated claim in 29/30 queries and returned an accepted relation in 27/30 queries; one query-planning call failed. Both complete public runs are retained as compressed JSON files. Expiring request handoff tokens are omitted.

Both conditions use the production GPT-6-Luna interactive prompt and configuration. Each case has the same candidate and query; only the argument field changes. The latest 60 calls completed successfully. The initial prompt run is also preserved in `fixed-candidate-ablation/`; the current prompt run is `fixed-candidate-ablation-v2/`. All inputs, original responses, configuration, timings, and hashes are included.

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
