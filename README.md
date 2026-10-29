# DeepShovel 2.0

DeepShovel organizes scientific literature into **source-grounded reasoning networks**. Claims connect to reasoning steps, observations and assumptions, with scientific scope and locators for the original text, figures and data. Retrieval exposes these argument structures for source inspection, conditional comparison, claim verification and agent-assisted research.

Release **`www2027-artifact-v1.1.2`** includes the Web/Compiler application, executable argument checks, a complete GP15 research reproduction and historical records showing how a retrieved argument contributed to a research decision. The production literature collection, accounts and Atlas reference datasets are not distributed.

[Demo paper](paper/main.pdf) · [Public workbench](https://deepshovel.iiot-shxt.xyz/) · [English demonstration walkthrough](artifact/live-demo/README.md) · [Versioned releases](https://github.com/spiderLL2002/deepshovel/releases) · [Reproduction CI](https://github.com/spiderLL2002/deepshovel/actions/workflows/reproduce.yml) · [Issues](https://github.com/spiderLL2002/deepshovel/issues)

## Start with the reasoning and research evidence

| Entry | Inspect or execute | Evidence provided |
| --- | --- | --- |
| [Argument expansion](artifact/arguments/README.md) | Existing production expander on a saved scientific graph | All 18 recorded premise edges and 3 assumption edges retained for F2; seven claims checked against a reference traversal; explicit depth-truncation controls |
| [GP15 research reproduction](artifact/gp15/README.md) | Public inputs, frozen protocol, raw-source checks and ten analysis stages | Four RMSEs, six exact error contrasts and the bounded C12 inference; source and result bindings |
| [Research decision trace](artifact/research-trace/README.md) | Actual query, returned claim, source chain and subsequent candidate record | Hongo2007 prior-work overlap strengthened a candidate rejection; a separately labeled retrospective GP15 identity index |
| [Live demonstration](artifact/live-demo/README.md) | English steps and captured application views | Navigate retrieval, reasoning details and original sources in the workbench |

From a clone, use **Node.js 22.13+** and **Python 3.12.13**:

```sh
git clone https://github.com/spiderLL2002/deepshovel.git
cd deepshovel
node --experimental-strip-types --no-warnings artifact/arguments/verify.mjs --out runs/arguments
python3.12 -B artifact/gp15/fetch_external_sources.py
python3.12 -B artifact/gp15/verify_demo.py
python3.12 -B artifact/research-trace/verify_trace.py
```

These checks need no account, model API, database or npm install. The fetch command restores two supporting inputs from their original providers and verifies their frozen SHA-256 values; subsequent quick checks run offline. The argument package executes existing production traversal on recorded inputs. It checks completeness relative to those inputs, while its missing-edge control shows why structural validation alone cannot discover an unrecorded scientific premise.

For the complete GP15 computation, install the locked research dependencies in an isolated environment:

```sh
python3.12 -m venv .research-venv
.research-venv/bin/python -m pip install -r artifact/gp15/reproduction/requirements-research.lock
.research-venv/bin/python -B artifact/gp15/run_portable.py --out runs/gp15-full
```

The preserved reference-environment execution completed all ten stages and matched **76 generated files byte-for-byte**. The cross-platform command above executes the same frozen stages. Scientific fields, CSVs and SVG/PDF figures require exact bytes. Four named Python-runtime fields may differ, and six named PNGs undergo explicit pixel checks: four Linux images had identical pixels, while two differed only in tiny raster details with byte-identical vector counterparts. The comparison rules and every allowed metadata, image and dependent-manifest difference are reported. The original strict entry point, `artifact/gp15/reproduction/run_reproduction.py`, remains available with the same `--out` argument. See [GP15 execution details](artifact/gp15/README.md) and [environment and CI scope](repro/ENVIRONMENT.md) for actual outcomes; no reference is rewritten to obtain a PASS. `--out` must name a new directory. On Windows, virtual-environment executables are under `Scripts/`.

Numerical reproducibility, preservation of recorded argument inputs and historical research decisions are separate forms of evidence. The packages expose their scope and failure controls.

## Comparison and scope fixes in this release

Version 1.1 preserves explicit assumption roles, typed dependency edges and truncation state through the comparison projection and both model-input stages. It also applies conservative scope guards to interactive retrieval. The [comparison-input checks](artifact/arguments/comparison-input/README.md) and [scope-control tests](artifact/arguments/scope-control-flow/README.md) execute these repaired paths. The original graph expansion and historical scientific records remain separately identified.

These are changes to the released source. The shared live service was not deployed by this artifact-publication task; its dated screenshots show the existing detail inspector. Run both Web and Compiler from the same release tag to use the repaired comparison and scope paths, or execute their key-free checks. Model replies used in structural transport tests are controlled fixtures, not evidence of semantic accuracy.

## Earlier observation-reuse artifact

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

## Preserved earlier recorded checks

The core command verifies distributed file hashes, both F2 exports and their PDF, the 33-node/33-edge branch, the frozen 20-slot cohort (16/20 shortest traces with both node kinds; 20/20 full-graph routes), 30 further structural examples, the frozen 114-query diagnostic, negative controls, and HTTP availability of the editor assets. Results and captured logs go to the output directory, not the input files.

The extended command additionally executes the bundled production-code scope/admission fixtures and checks RDFLib against jsonld.js. Python dependencies and the JavaScript consumer have complete version lockfiles.

```sh
python3.12 -m venv .repro-venv
.repro-venv/bin/python -m pip install -r repro/requirements.lock
.repro-venv/bin/python scripts/reproduce.py --extended --output runs/extended
```

The extended tier also needs Node.js 22+ and npm; its isolated `npm ci` fetches the pinned JSON-LD consumer. On Windows, use `.repro-venv\Scripts\python.exe`. [Expected outputs and claim coverage](repro/RESULTS.md) · [Version and environment information](repro/ENVIRONMENT.md).

GitHub Actions retains the earlier core, extended and Web-build jobs and adds a reasoning/research evidence job. Download their run artifacts for actual logs and reports. A configured workflow or committed local receipt is not a successful run of that workflow; inspect the Actions result for the revision being used.

## What the small dataset contains

| Material | Included | Supported use |
| --- | --- | --- |
| F2 reservoir case | Licensed PDF, complete recorded graph, retrieval result, actual reuse exports | Source reading, branch construction, file identity, record verification, RDF consumption |
| Nd adsorption case | Licensed PDF, prepared tuples and source anchors | Inspect different experiments and quantity/uncertainty representations |
| Original two-query cohort | 20 frozen result slots, 13 graph projections | Reproduce the reported structural counts |
| Additional corpus | 30 distinct active paper snapshots, text-free projections, selection rule and hashes | Inspect graph structure and run directed-path checks |
| Retrieval diagnostic | 114 frozen queries, rankings and judgments | Recompute stored metrics, without rerunning current models |

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

Original project code and author-created artifact materials are **AGPL-3.0-only**. Upstream scientific datasets, papers, author software and vendored components retain their own licenses and attribution. The GP15 package preserves CC BY 4.0 measurement/paper attribution and the original model code's MIT notice; two mixed-source supporting inputs are downloaded rather than committed. The Hongo trace distributes no original PDF, figure or source-text quotation. See [LICENSE](LICENSE), [NOTICE](NOTICE) and [third-party notices](THIRD_PARTY_NOTICES.md).

Use [CITATION.cff](CITATION.cff) to cite version 1.1.2. Source manifests and release checksums identify supplied bytes. A long-term archival DOI is not claimed; [archiving instructions](repro/ARCHIVING.md) describe that separate step.
