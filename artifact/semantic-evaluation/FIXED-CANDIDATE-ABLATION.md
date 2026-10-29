# Fixed-candidate argument ablation

Run from this directory with the production source checkout and its Python dependencies:

```sh
export DEEPSHOVEL_REPO=/path/to/deepshovel-2
export DEEPSHOVEL_ENV_FILE=/path/to/private/compiler.env
export CONCURRENCY=2
"$DEEPSHOVEL_REPO/compiler/.venv/bin/python" run_fixed_candidate_ablation.py
```

Alternatively supply `GPT_API_KEY` and optionally `DEEPSEEK_API_KEY` through the environment. `NODE_BINARY` can select Node >=22.13. No credential is included in experiment artifacts. The source checkout is imported, never edited. `--cases`, `--sources`, and `--out` accept alternative paths. Use a new `--out` directory for a new replicate. `--prepare-only` validates graph integrity and prepares candidate projections without model calls.

The frozen cases define 30 queries and their designated candidates. Every query receives two conditions: claim plus scope, and the same claim plus scope with the argument produced by the production `createRetrievalArgumentProjector`. The prompt, schema, model configuration, and candidate identity are fixed. Calls alternate condition order by case. No retrieval or query-planning model is invoked.

The production interactive `ModelJsonClient` policy is retained: GPT attempts begin at 0/10/20 seconds, the first validated result wins, and fallback is available only under the production failure conditions. Two logical calls can therefore involve more than two transport requests. There is no application time limit and no experiment-level retry. Existing result files, including failures or interrupted `running` records, are never silently rerun.

`fixed-candidate-ablation/manifest.json` records configuration and source/input hashes. `prompt-schema.json` stores the exact system prompt and strict response schema. Each case/condition JSON contains the exact serialized user input, original response text and transport metadata, validated output, errors where applicable, and elapsed time. `summary.json` scores the frozen accepted relation sets, with direction reported separately. `projected-candidates.json` contains the full model-visible candidate projections.

This is a single-run, five-paper diagnostic comparison against source-checked AI labels. It measures the effect of supplying the recorded argument under this fixed prompt; it does not measure retrieval performance or establish a general accuracy estimate.
