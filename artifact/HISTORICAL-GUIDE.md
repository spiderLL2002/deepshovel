> Public release: use the root [README](../README.md) and `python3 scripts/reproduce.py` for the supported, newly executed reproduction path. Historical QA references below describe the original author workspace; current public execution logs are downloadable from GitHub Actions.

# DeepShovel paper artifact

From this directory, run:

```sh
python3 server.py
```

Open `http://127.0.0.1:8137/generic-reuse.html`. The standard-library server binds only to 127.0.0.1:8137 and serves this artifact directory. Stop it with Ctrl+C. No package installation is needed for the browser interfaces. A port collision is reported without stopping another process. `/health` returns the `deepshovel-paper-contract-replay-v1` signature.

The Desktop `.command` launcher is a convenience on the author’s Mac, not a submission prerequisite. The portable entry point is `python3 server.py`.

## Recommended workflow: schema 1.5 F2 record

Use this single path for the current end-to-end demonstration. All commands below run from this artifact directory. It uses a recorded retrieval result plus a separately supplied stored graph; it does not issue a fresh hosted query.

1. Start the server as above and open `http://127.0.0.1:8137/generic-reuse.html`. Import [second-case/retrieved-result-only.json](second-case/retrieved-result-only.json). This input retains the recorded shortest path without a prebuilt observation branch.
2. In **Stored graph JSON file**, choose [second-case/full-graph.json](second-case/full-graph.json), then click **Build observation branch**. Select the observation `obs.f2.assessed` and its source anchor `anchor.text.f2.stability` in the resulting observation branch. The original shortest path remains available.
3. Click **Open local source PDF** in the original-context panel and select [second-case/source.pdf](second-case/source.pdf). The embedded viewer follows the recorded anchor’s `selector.page` to PDF p.11; inspect the F2 passage and Table 4 and check the separate file-hash status. Read the source before entering the quantity, value, unit, uncertainty interpretation, conditions and locator. Choose a reuse purpose and reading conclusion and record any qualification. For this case, the author-reported summary is 55–57 MPa; the source-reading notes in the recorded example distinguish that summary from formulation-specific table values and the imported anchor region.
4. Save and download your prepared schema 1.5 record. Place the downloaded JSON in this artifact directory as `my-reuse.json` for the following commands. The saved fields reflect your entries; they are not automatic scientific certification.
5. Verify your downloaded `my-reuse.json` and its PDF independently. The included PDF below applies to this F2 walkthrough; for your own paper, replace `second-case/source.pdf` with your own inspected PDF path:

```sh
python3 verify_reuse.py --record my-reuse.json --source-pdf second-case/source.pdf
```

6. Produce a new PROV-O JSON-LD view with the standard-library exporter:

```sh
python3 export_prov.py --record my-reuse.json --out my-reuse.jsonld
```

The output filename must not exist. Export verifies the saved record but does not reopen the PDF; it records the current file state as `not_rechecked` separately from the saved file-check state. See [prov/README.md](prov/README.md) for the minimal mapping, SPARQL examples, and an **optional** independent RDF QA setup that creates its own local virtual environment and installs pinned RDFLib. No third-party package is required for the six steps above. Optional [JavaScript JSON-LD consumer QA](rdfjs/README.md) provides a pinned, isolated npm setup; after that setup, run `node rdfjs/consume.cjs --module "$rdfjs_deps" --input recipient-check/input-record.jsonld --out rdfjs/rerun-output` to read the current single F2 record independently.

## Inline local PDF reading

After importing a result, **Open local source PDF** opens the same file chooser as **Local source PDF** in the file-check section. The browser hashes the selected bytes and gives an independent copy to the local PDF.js worker. Rendering and file identity are separate: a rendered PDF does not imply a matching hash or scientific support, and a rendering error does not change the observed file-check result.

The original-context pane provides **Previous**, **Next**, **Page**, **Zoom** (Fit width, 125%, 150%, 200%), and **Open original PDF**. Enter a page and leave the field to navigate. The source-page viewport is keyboard focusable for scrolling within the page. A selected recorded anchor with `selector.page` navigates to that page; no page is inferred from label text. Missing or out-of-range pages show an explicit status. The viewer displays source pages without correcting recorded regions, drawing new overlays, filling scientific fields, or judging support. Text search, selection, and annotation are outside this minimal viewer.

PDF bytes remain in the current browser session and are not included in saved decisions, downloaded JSON, or localStorage. After reload, choose the PDF again to view it. A restored file check is explicitly historical, while page controls remain disabled until a PDF is loaded. Save requirements and schema 1.5 are unchanged.

The renderer, worker, character maps, standard fonts, and WebAssembly assets are vendored locally; no CDN or Node runtime is needed to run this artifact. See [PDF.js dependency and license record](vendor/pdfjs/DEPENDENCIES.md), [Apache license](vendor/pdfjs/LICENSE), and [resource SHA-256 manifest](vendor/pdfjs/vendor-sha256.json). Browser/renderer receipts are in [qa/inline-pdf-viewer](qa/inline-pdf-viewer/). The new [inline-source F2 export](second-case/inline-source-reuse-ui-export-v15.json) is a separate schema 1.5 record; older exports are retained.

## Packet self-check (included example only)

This command verifies the distributed comparison example, **not your newly downloaded record**:

```sh
python3 verify_reuse.py --record second-case/comparison-reuse-ui-export-v15.json --source-pdf second-case/source.pdf
```

Use `my-reuse.json` in both the verification and PROV commands above to check and project your own decision.

## Additional browser interfaces

- `generic-reuse.html`: import a direct production result or `{result: ...}` from JSON file or pasted text. Select a real observation and source anchor from its path, enter scientific fields and your reading conclusion, optionally hash a local PDF, then save and download a decision. The sample button loads the included recorded Nd result; its PDF check uses the included article. For arbitrary imported papers, a selected local PDF renders in the original-context pane using the bundled PDF.js renderer; supplied source URLs remain available separately.
- `nd-case/index.html`: inspect two author-curated tuples and save an artifact-local reuse decision. Its source viewer includes full original pages 3, 4 (Table 1), 7, and 8. Figure and equation locations are recorded separately in Nd decision schema 1.1.
- `contract-replay.html`: switch among 108 previously recorded contract outputs. The HTML is also usable as a manually opened local file.

Current generic decision schema `generic-observation-reuse/1.5` preserves `reference`, selected nodes, `original_scope`, `original_result`, `prepared_observation`, `source_identity_check`, and explicit `source_reading`. The file check is `pending`, `verified`, `mismatch`, `missing_expected_hash`, or `error`; `verified` requires a real PDF hash matching the selected anchor’s explicit `artifact_version_id` (`resolution: direct_anchor`) or a recorded artifact-version→selected-anchor `locates` edge (`recorded_locates`). Missing binding stays `unresolved` and yields `missing_expected_hash` after file hashing; a lone artifact-version node is insufficient. A restored verified state describes the earlier file check, and the interface asks users to reselect the PDF to open or recheck it.

Exports include a `derived-observation:sha256:` ID and `canonical_payload`. Recompute the ID by hashing the exact canonical string as UTF-8, or regenerate it by recursively sorting payload object keys, preserving array order, and emitting JSON without whitespace. The outer record ID is excluded from its payload hash. LocalStorage uses a separate key for each workflow; reset affects only that workflow. No imported data is sent to an external service. Saved records describe explicit user preparation and retain production references; they do not modify production graphs.

## Recorded contract fixture

This standalone English interface embeds the existing fixture.json, results.json, and summary.json from `../evidence/scope_contract/`. It replays all 9 × 3 × 2 × 2 saved configurations, without executing retrieval or model inference. Source hash keys are reduced to filenames to avoid exposing machine paths; values are unchanged. Source-derived series/loading records and constructed variants are identified separately. The clustering panel is a separate fixed check, not clusters recomputed for the selected query.

The fixture uses assigned relations and artificial dense vectors. It is not a quality benchmark or human ground truth, and does not validate source paths, new snapshots, or numeric temporal reasoning. Raw embedded records and hashes can be expanded in the interface.

No production repository files are changed.

## Offline production contract reproduction

The submission now includes exact recorded production source excerpts in `vendor/`, pinned public dependencies, and portable runner copies in `replays/`. No private checkout is required for these two offline fixtures. See [vendor/README.md](vendor/README.md) for installation and limitations; after installing `vendor/requirements.txt`, run `python replays/run_all.py`. Actual vendor-only source verification is stored in `qa/vendor-final-scope/` (18/18 checks) and `qa/vendor-final-admission/` (valid control accepted, 6/6 corruptions rejected), including loaded-module paths.

Fresh-install verification also passed using a new isolated Python 3.12.13 venv and the pinned requirements: see `qa/clean-install-verification.json` and `qa/clean-replay/`. Both fixtures reproduced successfully without the original checkout or site-packages.


## Generic input contract

Use a direct result object or an envelope containing `result`. Required nonempty string fields are `paper_id`, `snapshot_id`, `claim_id`, and `canonical_claim`. Supply `scope` as an object and `shortest_evidence_path.nodes` as an array of uniquely identified `{id, kind, ...}` nodes. For saving, the selected path must record a direct source-anchor→observation `grounds` edge and a directed observation→current-claim route. Edges may be supplied as `links` (`fromId`/`toId`) or `edges` (`source_id`/`target_id`). An optional `observation_evidence_path` supplies a distinct recorded inspection branch; the original shortest path remains intact. Missing required links disable saving. File-hash matching uses only the selected-anchor binding described above. An optional `source_url`, `sourceUrl`, or `url` supplies the original HTTP(S) source link. Every exported decision preserves the original result.

Historical schema 1.2 introduced `field_differences`, retained in current schema 1.5: imported uncertainty and scope time/condition text alongside the operator’s prepared text, with `comparison_type: "textual"` and `text_differs`. The UI updates these comparisons while editing. They record wording differences, not semantic validation. `source_reading.authorship` remains `local operator entry`. Historical exports retain their original schema.

## Deterministic observation-path handoff

`prepare_handoff.py` is a standard-library-only adapter from an existing result (direct JSON or `{result: ...}`) and a complete recorded canonical PaperEvidenceGraph (direct or `{graph: ..., snapshot: ...}`). It verifies paper/claim membership and recorded snapshot metadata, then follows original directed reasoning edges backwards from the selected claim. For every reachable observation with an actual anchor→observation `grounds` edge, it retains the original anchor and one deterministic shortest reasoning route to the claim. The output union contains only byte-value-identical original node/edge objects; no scientific extraction, added edge, inferred relation or new snapshot occurs. Anchor `artifact_version_id` remains intact for source binding. This is structural adaptation, not independent cryptographic revalidation of the production graph hash or scientific truth.

```sh
python3 prepare_handoff.py --result second-case/result.json --graph second-case/full-graph.json --out second-case/my-derived-handoff.json
```

Use `--observation-id` to select one recorded observation; omission includes all observations with qualifying paths. `--out` must not exist and cannot be either input. Missing graph/identity/anchored chain produces `STRUCTURE_ERROR` and preserves inputs. Nd cannot acquire new branches without a complete graph; its original retrieval path can be retained separately without inventing missing data.

The real second case was run to `second-case/derived-handoff.json`; `qa/prepare-handoff-verification.json` checks every emitted node and edge against the recorded graph and proves the original shortest path and all other result fields are unchanged. The original shortest path still contains no observation. The input result already had a separately assembled observation branch; the adapter replaces only that branch with its deterministic general-schema union and does not change `result.json`.


## Structural provenance gate (current schema 1.5)

The generic workflow lists only source anchors with a recorded direct `grounds` edge to the selected observation. Saving also requires a directed reasoning path from that observation to the current claim within the selected inspection path. Missing graph links disable saving; the tool does not synthesize edges. The original shortest path and any explicitly supplied full-graph observation branch remain unchanged and inspectable.

New records use `generic-observation-reuse/1.5`, with `structural_link_state: "linked"` and `observation_reaches_claim: true`. The separate `source_identity_check` preserves verified, pending, or mismatch file identity states. Neither check is a scientific support decision. Experiment/context fields, textual differences, and source-reading conclusions remain operator-entered. Historical exports keep their earlier schema.


## Recorded verification history and cohort checks

These are completed receipts and preserved examples, separate from the current workflow above.

- Schema 1.5: [latest comparison export](second-case/comparison-reuse-ui-export-v15.json); [earlier successful browser-built export](second-case/browser-built-reuse-ui-export-v15.json), [browser UI verification](qa/generic-reuse-v15-ui-test.json). The separate [focused verification](qa/generic-v15-adapter-verification.json) compares the actual 33-node/33-edge F2 JavaScript output to the Python output and records metadata/grounds failures and verifier policy checks; it is separate from browser UI validation.
- [Schema 1.4 UI export](second-case/adapter-reuse-ui-export-v14.json), [browser verification](qa/generic-reuse-v14-ui-test.json), and [independent contract verification](qa/generic-v14-contract-verification.json) retain the completed adapter-based F2 walkthrough and checks.
- [Cohort README](handoff-cohort/README.md) provides the recorded multi-claim adapter check and rerun instructions; [summary](handoff-cohort/summary.json) and [outcomes](handoff-cohort/outcomes.json) retain its results.


To replay the historical schema 1.4 check, use `python3 verify_reuse.py --record second-case/adapter-reuse-ui-export-v14.json --source-pdf second-case/source.pdf`. The standard-library CLI reports integrity, graph structure, and source identity separately; omit `--source-pdf` for `not_rechecked` file status. Failed integrity/structure or a supplied PDF mismatch returns nonzero. Operator reading is displayed without scientific certification. See [CLI verification](qa/verify-reuse-cli-verification.json).


## Build a branch in the browser (schema 1.5)

After importing a result, the optional **Stored graph JSON file** chooser accepts a complete recorded graph, directly or wrapped in `{graph, snapshot}`. Click **Build observation branch** to check paper, claim, and snapshot metadata and construct the same deterministic union as `prepare_handoff.py`. The browser hashes the exact selected graph-file bytes. It retains the original shortest path and other result fields, and leaves scientific preparation fields blank. Missing graph keeps the button disabled; mismatched metadata or no grounded reasoning chain reports `STRUCTURE_ERROR` and leaves the imported result unchanged.

For a recorded shortest-result-only walkthrough, import [retrieved-result-only.json](second-case/retrieved-result-only.json), then choose [full-graph.json](second-case/full-graph.json). The original observation branch was removed in this derived input; [provenance](qa/retrieved-result-only-provenance.json) documents the unchanged remaining fields. This is a recorded result with a separate stored graph, not a fresh hosted query.

Schema 1.5 aligns Save and independent verification with the adapter: observation/reasoning → reasoning uses `premise_for`; reasoning → current claim uses `infers`, `supports`, `contradicts`, or `qualifies`. The anchor → observation edge remains `grounds`. These are recorded structural checks. `verify_reuse.py` explicitly labels historical 1.4 records as using their original unrestricted directed-reachability policy, and 1.5 records as using the restricted reasoning policy. Historical exports remain unchanged. 

The generic Reading conclusion selector includes **Do not reuse for this purpose** (`do_not_reuse`); this explicit operator choice is independent of linked graph and verified file states and preserves schema 1.5.

The field comparison also displays the selected observation’s recorded `value.numeric`, `value.raw`, and `value.unit` beside prepared value/unit; missing fields stay “not supplied” and the exported `observation_representation` comparison remains textual in schema 1.5.

See [Record reading guide](RECORD_READING_GUIDE.md) for authoritative record bytes, relation roles, source-check timing, selector versus prepared-text fields, and historical verification policies.

## Direct RDF route query (exporter 0.2.0)

[The current RDF route example](rdf-route/README.md) exposes ordered original node IDs/kinds, edge relations and the selected grounds edge as domain RDF, alongside the prepared fields and standard PROV preparation lineage. It preserves the same c29 export used in the earlier 57-triple examples. Actual RDFLib/jsonld.js checks yield isomorphic 115-triple graphs and exact original-byte recovery; the earlier files remain versioned historical results. The uniquely recoverable route is projected only when unambiguous.
