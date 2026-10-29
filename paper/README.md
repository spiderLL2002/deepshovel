# WWW 2027 demonstration manuscript

[Read the four-page PDF](main.pdf). Build the source with `tectonic main.tex` from this directory. Figure 1 uses cropped public-service captures from `../artifact/live-demo/03-claim-detail.jpg` and `../artifact/live-demo/04-assumptions.jpg`. Tectonic resolves the ACM class from its TeX bundle.

The paper presents argument reconstruction, condition-sensitive comparison, and an executed agent-guided investigation. From the repository root, reproduce the evaluation and research analyses with:

```sh
python3.12 scripts/reproduce_paper.py --out runs/paper
```

Add `--live` to run fresh model comparisons and public-service queries, with `GPT_API_KEY` supplied through the environment. See the [repository instructions](../README.md) for dependencies and the [complete execution record](../repro/paper-live-complete-execution.json) for the published run. Release tags bind the paper to its source, evaluation inputs, and research package.
