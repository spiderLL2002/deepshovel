# Direct RDF query of one selected scientific route

Exporter **0.2.0** adds a domain route projection while retaining the original schema-1.5 record, prepared fields and standard PROV facts. The included `input-record.jsonld` is a fresh export of the same c29 F2 record used by the historical recipient and JavaScript checks. Those earlier 57-triple packets and reports remain unchanged; they describe the earlier exporter, not this 115-triple projection.

## Query and mapping

`route.rq` queries RDF terms directly, without decoding an embedded JSON path. It returns this ordered route:

| Position | Source ID / kind | Original relation | Target ID / kind |
|---|---|---|---|
| 0 | obs.f2.assessed / observation | premise_for | reason.f2.evidence / reasoning |
| 1 | reason.f2.evidence / reasoning | premise_for | reason.f2.safety / reasoning |
| 2 | reason.f2.safety / reasoning | infers | claim:sha256:8820b173b31b5d2e9f3d59c1cebbf15bf72a8b5af94ac82a4578a99f3c57af43 / atomic_claim |

A separate recorded edge retains `anchor.text.f2.stability → grounds → obs.f2.assessed`. The terminal scientific relation is `infers`; the imported retrieval `supports` relation is not substituted.

The `ds:` vocabulary remains `urn:deepshovel:reuse:v1:`. It is a local, documented URN vocabulary, not a deployed Web ontology. Original node `kind` strings appear as `ds:nodeKind`, IDs as `ds:recordedNodeId`. New reasoning/claim resources are `ds:RecordedNode`; existing observation/anchor IRIs are reused. Each `ds:RecordedEdge` has `ds:source`, `ds:target`, the exact string `ds:relation`, `ds:recordedEdgeId`, and its complete original object in `ds:json`.

`ds:selectedRoute` links a `ds:SelectedRoute`; `ds:orderedNodes` is an RDF list, and `ds:step` links edge resources whose `ds:position` is a zero-based integer. `ds:selectedGroundsEdge` identifies the separate grounds edge, which is not counted as a route step. These scientific domain edges are not `prov:wasDerivedFrom` and do not certify science. Standard PROV continues to describe record preparation only; no person, execution time or source-PDF use is inferred.

The input selects an observation and reference claim within a wider inspection subgraph. The exporter materializes an ordered route only when exactly one simple directed path connects them: `ds:routeProjectionStatus` is `unique`. Multiple paths produce `ambiguous`, no path produces `absent`, and neither produces a `ds:selectedRoute` link. Thus ordering is recovered from recorded directions, not a fabricated user path selection or relevance ranking. Only the unique route and its selected grounds edges are projected; the complete wider subgraph remains in original JSON.

Original source numeric 55, raw `55–57 MPa`, unit, nulls, prepared `55–57`, and `qualified_reuse` wording remain unchanged. Full source values and operator reading remain available through JSON literals; the new direct RDF capability is the typed route query. `ds:originalExport` preserves the exact original UTF-8 bytes. The payload hash does not authenticate independently modified RDF triples; consumers must check projections against the restored record.

## Recorded execution and limits

On 2026-10-27, jsonld.js **9.0.0** with Node **v26.3.0** produced **115 default-graph quads / 18 subjects** and read three ordered route steps plus grounds directly from RDF terms. Its node/edge objects matched the original payload. RDFLib **7.6.0** parsed **115 triples**; `cross_client_isomorphic.py` confirmed graph isomorphism and exact original-byte recovery in both libraries. RDF-list blank-node names differ between processors, so the comparison preserves literal lexical forms, datatypes and language tags and compares graphs up to blank-node renaming. See `root-js/result.json` and `root-cross-client.json` for actual results.

| Item | Actual value |
|---|---|
| JSON-LD SHA-256 | 483b088d1acb05af66633d2ef1eee3d371c51f6be3d13257c6c107b3210a99c8 |
| Record payload SHA-256 | 54ac83a1ddad95f7e179d64b636ce9e97da1c37fc4dd34173da3c29b79fa6463 |
| Original bytes restored by both libraries | 214323 |
| Original export SHA-256 | ea03972266cc720c7b52706f76bd1fed542fc17de88b31f438a034f36c54d26d |
| Recorded / current file check | verified / not_rechecked |

The root reran the portable RDFLib/SPARQL check (`root-python/result.json`) and compared all five projected node objects and four edge objects, checked RDF-list order, and verified that all standard PROV facts matched the earlier 57-triple export. One RDF-only `infers`-to-`supports` substitution was detected against the original JSON. The unchanged verifier accepted recovered original bytes; standard-library-only export matched the candidate output. Synthetic absent/ambiguous helper checks omitted `selectedRoute`; these are not claimed as additional schema-valid records. Candidate receipts remain in the editor work area; they are not prerequisites for rerunning this portable check.

This is two independently maintained libraries reading **one fixed record**, with author-side AI QA. It is not another independent recipient trial, human comprehension experiment, external scholarly application adoption, scientific validation, or evidence of general Semantic Web interoperability. No source PDF is reopened.

## Portable reproduction

Run from the artifact root. The exporter and verifier need only Python's standard library. RDF QA is optional. Create a reader-owned environment using the same pinned packages documented in [../prov/README.md](../prov/README.md):

```sh
python3 -m venv .prov-qa-env
.prov-qa-env/bin/python -m pip install --index-url https://pypi.org/simple rdflib==7.6.0 isodate==0.7.2 pyparsing==3.3.3
.prov-qa-env/bin/python rdf-route/check.py --out-dir /tmp/deepshovel-route-python-new
```

Choose a fresh output directory; `check.py` refuses existing directories. It imports the artifact's adjacent `export_prov.py` and uses only included `recipient-check/input-record.jsonld` as its original-record source. It writes a fresh JSON-LD export, recovered original record, and `result.json`; runs `route.rq`; compares node/edge objects and ordered membership; checks preserved values/reading and unchanged PROV; and performs the single RDF-only relation-tampering negative control in memory. The saved JSON-LD remains unmodified. The included real fixture has one outgoing edge at each route step; this QA makes that fixture-specific condition explicit and does not establish general graph coverage.

For the independent JavaScript reader, use the pinned isolated npm setup from [../rdfjs/README.md](../rdfjs/README.md), with jsonld.js 9.0.0 and its checked-in lockfile:

```sh
rdfjs_deps=$(mktemp -d)
cp rdfjs/package.json rdfjs/package-lock.json "$rdfjs_deps/"
(cd "$rdfjs_deps" && npm ci --ignore-scripts --no-audit --no-fund --registry=https://registry.npmjs.org)
node rdf-route/consume.cjs --module "$rdfjs_deps" --input rdf-route/input-record.jsonld --out /tmp/deepshovel-route-js-new
python3 verify_reuse.py --record /tmp/deepshovel-route-js-new/recovered-record.json
.prov-qa-env/bin/python rdf-route/cross_client_isomorphic.py --jsonld rdf-route/input-record.jsonld --js-dataset /tmp/deepshovel-route-js-new/dataset.json --original-record second-case/comparison-reuse-ui-export-v15.json --js-recovered /tmp/deepshovel-route-js-new/recovered-record.json --out /tmp/deepshovel-route-js-new/cross-client.json
```

Use new output paths on each run. Dependency installation needs package-index access; consumers block remote JSON-LD contexts and require no external source document. JavaScript `route` is null for historical inputs without `ds:selectedRoute`; this preserves compatibility without claiming old inputs exposed route triples. The cross-client script accepts default graphs only. No private checkout path or preinstalled author environment is required.
