# Reproduction results and paper coverage

| Command or step | Expected result | Boundary |
| --- | --- | --- |
| `scripts/verify-release.py` | Every listed file matches SHA-256 and size | Identifies bytes; does not authenticate a publisher |
| Two F2 verifier runs | Payload, permitted path and current PDF identity pass | Operator reading remains a local entry |
| F2 adapter | 33 nodes, 33 edges; other returned fields unchanged | Requires the separately provided graph |
| 20-slot cohort | 16 shortest traces with both node kinds; 20 anchored full-graph routes | Fixed two-query structural cohort; no accuracy estimate |
| Additional corpus | 30 distinct paper structures with verified projection hashes and valid endpoints | No PDFs, quotations or scientific correctness labels |
| Frozen diagnostic | 114 queries and stored metrics match within 1e-12 | Frozen AI judgments; not human ground truth or a new model run |
| Negative controls | Changed payload and wrong PDF are rejected | Limited controls; not a general robustness claim |
| HTTP smoke | Editor, PDF renderer, source PDF and health endpoint are served | Does not simulate a human browser task |
| Extended scope fixture | 18/18 channel membership checks | Artificial vectors and metadata filters |
| Extended admission fixture | Valid control accepted; six corruptions rejected | Does not exercise end-to-end repair budget |
| Extended RDF | 115 triples; isomorphic RDFLib/jsonld.js graphs; original bytes recovered | One F2 record and local vocabulary |

`report.json` records the actual interpreter, platform, exit status, commands and elapsed time. Each command has separate stdout/stderr logs. GitHub Actions preserves these files per commit. The workflow fails if a reported check fails. Recorded author/AI walkthroughs in `artifact/` are preserved examples; CI logs are new executions.

The small artifact does not reproduce full-corpus model extraction, the installed production indexes, human usability, or scientific truth. It makes the exact narrower paper results executable by readers without access to the author's machine.
