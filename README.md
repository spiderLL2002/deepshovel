# DeepShovel 2.0

DeepShovel turns scientific papers into **source-grounded reasoning networks**. Each conclusion connects to the observations, assumptions, intermediate reasoning and original evidence needed to interpret it. Researchers can inspect why a result holds, compare it with a new claim, and give research agents an evidence base for further investigation.

[Demo paper](paper/main.pdf) · [Public workbench](https://deepshovel.iiot-shxt.xyz/) · [English walkthrough](artifact/live-demo/README.md) · [Releases](https://github.com/spiderLL2002/deepshovel/releases)

## Verify the public evaluation subsets and reproduce the GP15 study

The public evaluation package provides subsets of the full evaluation data for inspection and reference: 114 retrieval questions with 3,515 relevance judgments, and 30 semantic comparison cases from five papers. The full evaluation comprises 487 retrieval questions with 15,017 judgments and 323 semantic cases from 54 test papers. The commands below compute scores for the released subsets and reproduce the GP15 research study. To keep the package compact, the complete paper collection is not bundled; available source links and source-location records support inspection.

Use **Python 3.12** and **Node.js 22.13+**. From a fresh clone:

```sh
git clone https://github.com/spiderLL2002/deepshovel.git
cd deepshovel
python3.12 scripts/reproduce_paper.py --out runs/paper
```

The command creates one Python environment, installs locked dependencies, verifies the released files, recomputes retrieval and semantic scores on the public subsets, runs the production argument expander, prepares model inputs, restores two original research inputs, and executes all ten GP15 analysis stages. Outputs and logs are collected in the chosen directory, with a single `report.json` recording completion.

For fresh model comparisons and public-site searches, provide `GPT_API_KEY` in your environment and add `--live`:

```sh
python3.12 scripts/reproduce_paper.py --out runs/paper-live --live
```

This performs the same computation and then issues 60 paired model calls, 30 source-passage controls and 30 two-stage public searches. Each call records its input, output and timing. Use a new output directory for each run. On Windows, use the Python 3.12 executable available on your PATH.

## Evidence and demonstrations

The deployment snapshot on 29 October 2026 contains **9,506 active papers, 41,092 claims and 71,291 reasoning steps**. [Recorded counts](artifact/semantic-evaluation/deployed-corpus-counts.json) include observations, assumptions, source anchors and dependency edges.

| Material | Contents |
| --- | --- |
| [Argument comparison](artifact/semantic-evaluation/README.md) | Public subset: 30 source-grounded queries, frozen reference relations, both prompt runs, model inputs and original responses, source audit, and executable rerun scripts |
| [Argument inspection](artifact/arguments/README.md) | F2 claim with its recorded premises, all three assumptions, source links and production dependency expansion |
| [Retrieval evaluation](artifact/evaluation/README.md) | Public subset: 114 questions, 3,515 pooled judgments, BM25/dense/hybrid rankings and metric computation |
| [GP15 research](artifact/gp15/README.md) | Public observations, fixed protocol, ten analysis stages, four RMSEs and the six error contrasts supporting the derived claim |
| [Research trace](artifact/research-trace/README.md) | Queries, returned arguments, source inspection and subsequent research decisions |
| [Public demonstration](artifact/live-demo/README.md) | Attendee walkthrough and captured interface views |

The current interactive comparison receives each candidate's recorded argument, including assumptions omitted from its short claim. One model call ranks candidates and judges their relations to the whole query. The same dependencies can be opened in the inspector. The [semantic evaluation](artifact/semantic-evaluation/README.md) includes the corresponding production prompt and source hashes.

## Run the application

```sh
npm run install:ci
python3 scripts/setup-local-config.py
python3.12 -m venv compiler/.venv
compiler/.venv/bin/python -m pip install -e './compiler[test]'
```

Configure your services using the supplied environment templates, then start `npm run dev:compiler` and `npm run dev` in two terminals. Upload papers through the literature library to build your own collection. The released examples provide six complete recorded argument graphs for inspection and evaluation.

[Self-hosting guide](docs/SELF_HOSTING.md) · [Compiler configuration](compiler/README.md) · [Architecture](docs/ARCHITECTURE.md) · [Earlier observation-reuse artifact](artifact/README.md)

## License and citation

Project code and author-created artifact materials are **AGPL-3.0-only**. Scientific datasets, papers, author software and vendored components retain their licenses and attribution. The GP15 package includes CC BY 4.0 measurements and the original model's MIT notice; two supporting inputs are fetched from their original providers with SHA-256 verification. See [LICENSE](LICENSE), [NOTICE](NOTICE) and [third-party notices](THIRD_PARTY_NOTICES.md).

Use [CITATION.cff](CITATION.cff) when citing this software. Versioned releases and file manifests identify the exact source and materials.
