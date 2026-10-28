> Current exporter 0.2.0 adds direct domain-route projection; see [the current route example](../rdf-route/README.md). The 57-triple files and reports below preserve their original earlier-version executions.

# PROV-O export and downstream consumption

From the artifact root, run:

```sh
python3 export_prov.py --record second-case/browser-built-reuse-ui-export-v15.json --out reuse-record.jsonld
```

Implemented and locally checked on 2026-10-27 for round 09. CLI version **0.1.1**. The exporter uses the existing adjacent `verify_reuse.py` without modifying it and accepts **schema 1.5 only**. Main text and frozen round 08 were not changed.

## What a downstream reader can do

1. Run `python3 export_prov.py --record my-reuse.json --out my-reuse.jsonld` with Python's standard library and the adjacent verifier. Use your downloaded record saved as `my-reuse.json`, continuing the main README workflow; the included example checks below are separate. Existing output files are refused. Validation occurs before creating the output. A failed payload, graph structure or inconsistent recorded source binding/state prevents export. Absence of PDF-byte rechecking is acceptable and explicitly recorded as `not_rechecked`.
2. Parse JSON-LD in an RDF client. Standard PROV predicates expose the prepared record's input and preparation lineage. Custom predicates expose the selected observation, prepared value, conditions, uncertainty wording, reuse conclusion and source-version binding. The three `.rq` files give executable SPARQL examples.
3. Recover the exact original UTF-8 export from the `ds:originalExport` string and run the original verifier. `ds:canonicalPayload` and `ds:recordId` remain unchanged; the original selected path and objects remain available, including JSON types and nulls.

This is a demonstrated independent **RDFLib consumption and recovery check**, not evidence that an external scholarly application has adopted the format. The output uses a local URN vocabulary, not a deployed/dereferenceable Web ontology. A reader still needs that vocabulary's documented semantics for scientific fields. JSON-LD view triples are not a new signature: if the view is modified, consumers should compare projected fields to the restored verified record. The identifier checks the original canonical payload, not arbitrary changes to the RDF graph.

## Mapping and boundaries

- Imported result, selected observation, selected source anchor, prepared record, and resolved source-file version are distinct `prov:Entity` resources. The preparation represented by the saved record is a `prov:Activity` that `prov:used` the imported result, observation and anchor. No actual PDF opening or use is inferred from the source-version reference.
- The prepared record `prov:wasGeneratedBy` this preparation and `prov:wasDerivedFrom` the imported result. These predicates describe record preparation. The scientific `grounds`, `premise_for`, `supports`, `infers`, etc. edges are retained in the original path, **not** recast as PROV derivation or truth certification.
- No agent, person, authorship identity or execution time is invented. Saved wording such as “local operator entry” remains literal.
- `ds:` is `urn:deepshovel:reuse:v1:`. Strings are explicitly `xsd:string`; the two graph-check booleans are `xsd:boolean`; entity links use `@id`. Structured objects are JSON strings so that null, numeric-vs-string and array order remain recoverable. The selected source version is materialized only from verifier-resolved expected hashes. Unresolved source references remain in the original record/check report, with no fabricated resolved version entity.
- Scientific conclusions remain operator entries. Recorded file check `verified` and current check `not_rechecked` are separate. The conversion does not reopen the PDF, certify science, authenticate an actor, or reproduce model inference.

## Optional RDF QA in a reader-created environment

The browser workflow, `verify_reuse.py`, and `export_prov.py` use only the Python standard library. RDFLib is needed **only** to rerun the independent RDF parsing, SPARQL and roundtrip QA. From the artifact root, create a new local environment and install pinned QA dependencies:

```sh
python3 -m venv .prov-qa-env
.prov-qa-env/bin/python -m pip install --index-url https://pypi.org/simple rdflib==7.6.0 isodate==0.7.2 pyparsing==3.3.3
.prov-qa-env/bin/python prov/qa/check_roundtrip.py --out-dir /tmp/deepshovel-prov-qa-new-output
```

The `/tmp/deepshovel-prov-qa-new-output` path must not already exist; choose another new path for each run. Installation requires package-index access. Neither an author environment nor a private checkout is needed. The script locates the exporter, verifier and real fixture relative to its artifact location and refuses existing output directories. It records argv, stdout, return codes, runtime versions, input/code hashes, query results, the restored record and two negative controls in the requested directory.

## Author verification history

The following are completed author-side checks, not prerequisites or paths available in the distributed artifact. Runtime: Python **3.9.6**, RDFLib **7.6.0**; dependencies `isodate==0.7.2`, `pyparsing==3.3.3`. The author's isolated environment was `revision/prov-check-env`. The historical command from the artifact root was:

```sh
../prov-check-env/bin/python prov/qa/check_roundtrip.py --out-dir prov/outputs/verification-run
```

Use the reader-created environment above for a fresh run. Original command receipts, including original absolute paths, remain verbatim under `qa/historical-candidate/` (exporter 0.1.0). `outputs/historical-candidate/` preserves that JSON-LD and restored record. The original standard-library-only `python3 -S` output remains in `qa/historical-candidate/stdlib-only-output.json`.

Version 0.1.1 changes the source-version projection to an array of IRI links. The narrow in-memory two-expected-hash stub retains both links (`qa/historical-candidate/two-source-projection.json`); this checks projection behavior and does not assert that the current schema verifier accepts multiple expected hashes. Actual one-case RDF checks are recorded in `outputs/verification-run/verification.json`.

Real case: `browser-built-reuse-ui-export-v15.json`, record ID `derived-observation:sha256:08d6a63f75153b7aa4996d7249969ab311f877dac5694c2fb067c747a5474f8e`.

Observed output:

```json
{"passed": true, "rdflib_version": "7.6.0", "rdf_triples": 57, "negative_controls": 2}
```

The independent Graph parser and SPARQL queries yielded three preparation input rows; the selected observation `obs.f2.assessed`; prepared value `55–57`, unit `MPa`, conclusion `qualified_reuse`, full conditions and uncertainty wording; and source SHA-256 `f87d7cdcf0d08a7557993c1966b56183882c0b87c68809d0519ea8060382de99`. Source state query returned recorded `verified`, current `not_rechecked`.

Checks passed: exact original export bytes recovered from RDF literal; exact canonical payload and content identifier; strict JSON equality of the full recovered record; selected nodes, complete selected path, source binding/hash, prepared fields and reading retained; boolean datatypes and IRI links preserved; original verifier accepts the restored record. The historical 0.1.0 standard-library-only JSON-LD was byte-identical to its independently consumed output.

Two bounded negative controls were rejected, with no JSON-LD output created:

- Changed prepared value without updating canonical payload: integrity failure.
- Removed recorded `grounds` edges from both selected and imported path, then recomputed the outer hash: structural failure. Rehashing alone does not bypass graph checks.

## Primary standards / package sources

- [W3C PROV-O §3.1, Starting Point Terms](https://www.w3.org/TR/prov-o/#description-starting-point-terms): Entity, Activity, used, wasGeneratedBy and wasDerivedFrom.
- [W3C PROV-O wasDerivedFrom definition](https://www.w3.org/TR/prov-o/#wasDerivedFrom): used here for constructing a new data record from an existing one.
- [W3C JSON-LD 1.1 §4.2.1, Typed Values](https://www.w3.org/TR/json-ld11/#typed-values): explicit literal typing.
- [RDFLib official PyPI project](https://pypi.org/project/rdflib/): independently maintained RDF parser and SPARQL implementation; installed version captured in actual QA output.

Scope is one real schema-1.5 record and two targeted invalid variants. No claim of full PROV constraint validation, general schema coverage, external adoption or broad Web interoperability is made.
