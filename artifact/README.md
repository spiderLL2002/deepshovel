# Source linked reuse demonstration

Run `python3 artifact/server.py` from the repository root and open http://127.0.0.1:8137/generic-reuse.html. No installation or API key is needed for this browser workflow. All PDFs and rendering assets are served locally; imported material is not sent to a model service.

## F2 decision from empty fields

1. Import `second-case/retrieved-result-only.json`. Its shortest trace has no selectable observation, so saving is blocked.
2. Choose `second-case/full-graph.json` as the stored graph and click **Build observation branch**.
3. Select `obs.f2.assessed` and `anchor.text.f2.stability`.
4. Open `second-case/source.pdf` using **Open local source PDF**. Inspect PDF page 11's right column and Table 4. The imported region points left; the original scalar is 55 while the raw range is 55–57 MPa.
5. Enter your prepared quantity, value, uncertainty interpretation, conditions and textual locator. The case concerns modeled maximum sustainable pore pressure with zero cohesion and friction coefficient 0.6. Preserve the author-reported range and distinguish it from formulation-specific table values or a confidence interval.
6. Select a reuse purpose and conclusion. The interface preserves your reading separately from imported values and file/path checks.
7. Save and download your record. Reselect the PDF after reload to repeat its identity check.

From the repository root:

```sh
python3 artifact/verify_reuse.py --record my-reuse.json --source-pdf artifact/second-case/source.pdf
python3 artifact/export_prov.py --record my-reuse.json --out my-reuse.jsonld
```

These commands verify and project **your actual downloaded record**. The supplied comparison and inline-source exports are recorded examples, not substitutes for your actions. Output filenames must be new.

## Other included material

- `nd-case/index.html`: prepared experiment tuples, source pages and a local reuse decision.
- `contract-replay.html`: display of frozen fixture outputs; use the production runners to recompute them.
- `handoff-cohort/`: inputs, graph projections and runner for the reported fixed 20 slots.
- `rdf-route/`: current domain-route projection, queries and independent consumer programs.
- `evaluation/`: frozen 114-query AI diagnostic and exact metric recomputation.
- `independent-walkthrough/` and `recipient-check/`: recorded AI functional executions; neither is a human study.

For the supported single-command verification, expected counts, pinned dependencies and current CI logs, use the root [README](../README.md) and [results guide](../repro/RESULTS.md). `HISTORICAL-GUIDE.md` preserves the earlier detailed author workflow and references to local QA receipts that are not prerequisites for this public package.

## Record boundaries

The current generic record schema is `generic-observation-reuse/1.5`. A recorded direct grounding edge and a permitted observation/reasoning-to-claim route are required before saving. The adapter chooses deterministic paths; the operator selects an observation and anchor. A branch union is retained; RDF only projects a uniquely recoverable route.

A source hash identifies file bytes, not scientific truth or correct rectangle placement. Operator fields remain explicit local entries. The saved working original result retains imported fields plus any constructed observation branch. See [record reading guide](RECORD_READING_GUIDE.md).

Sources remain under their own CC BY 4.0 licenses: [F2 attribution](second-case/SOURCE.md), [Nd attribution](nd-case/SOURCE.md). The workbook is cited but not redistributed. PDF.js license and resource hashes are in `vendor/pdfjs/`.
