# DeepShovel 2.0

Scientific claim retrieval and source-linked observation reuse. This public release contains the complete Web/Compiler application source, a small executable paper artifact, two licensed source PDFs, and 30 additional text-free graph examples. The 9,000-paper production collection, account credentials, and Atlas reference datasets are not distributed.

[Public workbench](https://deepshovel.iiot-shxt.xyz/) · [Versioned releases](https://github.com/spiderLL2002/deepshovel/releases) · [Reproduction CI](https://github.com/spiderLL2002/deepshovel/actions/workflows/reproduce.yml) · [Issues](https://github.com/spiderLL2002/deepshovel/issues)

## Start with the paper artifact

Download the release archive, or clone this repository. Python 3.12 is the reference interpreter. The browser editor and core verification use Python's standard library: no API key, model weights, Node, Cloudflare account, or production database is needed.

```sh
git clone https://github.com/spiderLL2002/deepshovel.git
cd deepshovel
python3 scripts/reproduce.py --output runs/core
python3 artifact/server.py
```

Open **http://127.0.0.1:8137/generic-reuse.html**. Import `artifact/second-case/retrieved-result-only.json`, select `artifact/second-case/full-graph.json`, and click **Build observation branch**. Select `obs.f2.assessed` and `anchor.text.f2.stability`. Open `artifact/second-case/source.pdf`; inspect page 11 before preparing your reading. [Detailed attendee workflow](artifact/README.md).

The source reports **55–57 MPa**. The imported scalar is **55**, and the imported rectangle names the wrong column. Save your own qualification, export the record and verify it:

```sh
python3 artifact/verify_reuse.py --record my-reuse.json --source-pdf artifact/second-case/source.pdf
python3 artifact/export_prov.py --record my-reuse.json --out my-reuse.jsonld
```

Your output filename must be new. Source identity, recorded graph linkage and the operator's scientific reading are separate checks.

## Reproduce the paper's recorded checks

The core command verifies distributed file hashes, both F2 exports and their PDF, the 33-node/33-edge branch, the frozen 20-slot cohort (16/20 shortest traces with both node kinds; 20/20 full-graph routes), 30 further structural examples, the frozen 114-query AI diagnostic, negative controls, and HTTP availability of the editor assets. Results and captured logs go to the output directory, not the input files.

The extended command additionally executes the bundled production-code scope/admission fixtures and checks RDFLib against jsonld.js. Python dependencies and the JavaScript consumer have complete version lockfiles.

```sh
python3.12 -m venv .repro-venv
.repro-venv/bin/python -m pip install -r repro/requirements.lock
.repro-venv/bin/python scripts/reproduce.py --extended --output runs/extended
```

The extended tier also needs Node.js 22+ and npm; its isolated `npm ci` fetches the pinned JSON-LD consumer. On Windows, use `.repro-venv\Scripts\python.exe`. [Expected outputs and claim coverage](repro/RESULTS.md) · [Version and environment information](repro/ENVIRONMENT.md).

GitHub Actions runs the same commands from a fresh checkout. Download `core-*` and `extended-*` run artifacts for complete logs and machine-readable reports. CI is an automated execution by a separate machine, not a claim of an independent human review or an ACM badge.

## What the small dataset contains

| Material | Included | Supported use |
| --- | --- | --- |
| F2 reservoir case | Licensed PDF, complete recorded graph, retrieval result, actual reuse exports | Source reading, branch construction, file identity, record verification, RDF consumption |
| Nd adsorption case | Licensed PDF, prepared tuples and source anchors | Inspect different experiments and quantity/uncertainty representations |
| Original two-query cohort | 20 frozen result slots, 13 graph projections | Reproduce the reported structural counts |
| Additional corpus | 30 distinct active paper snapshots, text-free projections, selection rule and hashes | Inspect graph structure and run directed-path checks |
| Retrieval diagnostic | 114 frozen queries, rankings and AI judgments | Recompute stored metrics, without rerunning current models |

The extra 30 projections contain IDs, edge types, page metadata and source hashes; they omit scientific text, quotations, numerical readings and PDFs. They are not 30 fully curated scientific examples and cannot reproduce ranking over the full production corpus. Original snapshots are identified separately from the projection-file hashes. [Corpus details](examples/structural-corpus/README.md).

## Run the full application

The `app/`, `lib/`, `compiler/`, `db/`, `drizzle/`, `worker/`, `scripts/`, `deploy/` and `control_app/` directories contain the application source. Node dependencies are locked in `package-lock.json`; the Compiler has its own install instructions and environment template.

```sh
npm run install:ci
python3 scripts/setup-local-config.py
python3.12 -m venv compiler/.venv
compiler/.venv/bin/python -m pip install -e './compiler[test]'
# Configure your own services in compiler/.env.local, then use two terminals:
npm run dev:compiler
npm run dev
```

[Full application setup and service boundaries](docs/SELF_HOSTING.md) · [Compiler reference](compiler/README.md) · [Architecture](docs/ARCHITECTURE.md).

A fresh installation has an empty library and no Atlas dataset. New parsing/extraction and current retrieval require configured external services, compatible model access and your own keys. The recorded artifact is the key-free way to reproduce this paper's checks. Full-corpus retrieval rankings and repeated model extraction are not asserted to be bit-identical. Maintainer-specific macOS tunnel/deployment scripts are optional and should be configured for your own host.

## License and citation

Original project code and author-created artifact code/data are released under **AGPL-3.0-only**. Copyright and attribution remain with their holders; network users of modified versions must be offered the corresponding source as required by AGPL. The two scientific PDFs remain under **CC BY 4.0** with their authors' attribution. PDF.js and other third-party components retain their own licenses. See [LICENSE](LICENSE), [NOTICE](NOTICE) and [third-party notices](THIRD_PARTY_NOTICES.md).

Use [CITATION.cff](CITATION.cff) to cite this software. The release tag and SHA-256 manifests identify the supplied version. A long-term archival DOI is not claimed; [archiving instructions](repro/ARCHIVING.md) describe the remaining step.
