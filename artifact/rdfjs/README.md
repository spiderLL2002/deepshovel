> Current exporter 0.2.0 adds direct domain-route projection; see [the current route example](../rdf-route/README.md). The 57-triple files and reports below preserve their original earlier-version executions.

# Optional independent JavaScript JSON-LD consumer

This optional check uses official `jsonld` 9.0.0 (jsonld.js). The main artifact server/exporter/verifier still needs only Python's standard library. Node.js and npm are needed only for this check. No installed dependencies are included in the packet.

Run from the artifact directory. Choose your own isolated dependency directory; these commands create a temporary one, install the pinned lockfile with lifecycle scripts disabled, and explicitly pass its location:

```sh
rdfjs_deps=$(mktemp -d)
cp rdfjs/package.json rdfjs/package-lock.json "$rdfjs_deps/"
(cd "$rdfjs_deps" && npm ci --ignore-scripts --no-audit --no-fund --registry=https://registry.npmjs.org)
node rdfjs/consume.cjs --module "$rdfjs_deps" --input recipient-check/input-record.jsonld --out rdfjs/rerun-output
python3 verify_reuse.py --record rdfjs/rerun-output/recovered-record.json
```

`--module` is mandatory and identifies the directory containing package.json and node_modules/jsonld. The consumer does not default to an author's private environment. Its only record input is the F2 JSON-LD in recipient-check (record ID ending `54ac83a1ddad95f7e179d64b636ce9e97da1c37fc4dd34173da3c29b79fa6463`). It blocks remote JSON-LD context loading. No PDF is supplied, so the current file-check state remains `not_rechecked` even though the saved historical check is `verified`.

[report.md](report.md) states scope, results and hashes. `run.stdout.txt`, `result.json`, `dataset.json`, `recovered-record.json` and `verify.stdout.json` retain actual successful outputs; reruns write to a separate directory. `npm-source.json` and package-lock.json retain official registry provenance and dependency integrity. This is independent-library readability of ONE record, not external consumer adoption or general scientific semantic understanding. No domain RDF edges are invented.


## Same-record cross-processor comparison

`root-cross-client.json` records an additional author execution on the same input SHA-256 and record ID: RDFLib 7.6.0 and jsonld.js 9.0.0 produced identical sets of all 57 RDF terms, including literal datatype/language; both restored the exact 214323-byte source export. RDFLib SPARQL independently retrieved the prepared value, unit, conclusion and source-version hash. Recorded file identity remains `verified`; current byte check is `not_rechecked` in this format-only run.

With RDFLib installed as described in `../prov/README.md`, run from the artifact root and choose a new output path:

```sh
.prov-qa-env/bin/python rdfjs/cross_client.py --jsonld recipient-check/input-record.jsonld --js-dataset rdfjs/dataset.json --original-record second-case/comparison-reuse-ui-export-v15.json --js-recovered rdfjs/recovered-record.json --out /tmp/my-new-cross-client-result.json
```

This compares two independently maintained processors on one exported record, with the supplied JavaScript dataset also byte-identical to the author's independent Node run. It does not add another scientific case or an external deployment.
