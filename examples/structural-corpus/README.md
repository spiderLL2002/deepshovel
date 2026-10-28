# Thirty small paper graph examples

`manifest.json` fixes the selection rule: the first 30 distinct active paper IDs with nonempty claims and observations, ordered by stored snapshot ID, before structural checks. Each JSON file is a text-free projection of a genuine recorded production graph. Node/edge identifiers, edge types, page metadata and source identities are retained. Text, quotations, scientific values, scope text, document names and PDFs are omitted.

Run `python3 examples/structural-corpus/check.py` from the repository root. It verifies all projection hashes, distinct paper identities and edge endpoints, and reports grounded observation-to-claim reachability. It does not compare scientific claims, estimate retrieval quality or prove source correctness. These projections cannot be imported as complete production PaperEvidenceGraphs; their format is explicitly `deepshovel-text-free-graph/1`.

The original stored graph hash is a provenance reference, not the hash of the projected file. The manifest separately records the SHA-256 of each released projection. The separately supplied F2 and Nd cases provide licensed source PDFs for actual source inspection.
