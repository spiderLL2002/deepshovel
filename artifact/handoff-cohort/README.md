# Consecutive-slot structural handoff check

The selection rule was fixed before graph GETs: the original first 10 planned-preview entries from the saved Nd loading query, followed by the original first 10 from the saved Asmari query. No relevance/success filtering, replacement, deduplication, or additional model query was performed. `selection.json` records all 20 slots and original ranks plus receipt hashes. There are 20 unique claim/snapshot pairs and 13 unique snapshots. Exactly 13 same-host read-only GETs returned HTTP 200; there were no retries or deadlines.

Run offline using only Python's standard library:

```
python3 check.py
```

This regenerates `outcomes.json` and `summary.json`. `prepare_handoff.py` is a byte-identical copy of the artifact's actual adapter, not a rewritten algorithm. The preview-to-input mapping only maps snapshotId/paperId/claim.id to snapshot_id/paper_id/claim_id. The original shortest path's node identities/kinds and links are retained. `input_required_fields_present` checks the three identity fields required by that adapter; it is not a claim that omitted scientific fields have been validated.

The adapter selects existing grounds edges plus stable directed reasoning routes from anchored observations to the selected claim. Our additional checks require positive integer page locations for all selected anchors, existing artifact-version records whose IDs match their recorded SHA-256, and agreement with the retrieval source-version ID. Source PDF bytes were not fetched or checked in this cohort. A failure would be retained with its exact exception or failed metadata check.

| Specified query's consecutive slots | Shortest contains both observation and anchor | Full graph yields anchored observation→reasoning→claim route with page/source identity |
|---|---:|---:|
| Nd loading, first 10 | 9/10 | 10/10 |
| Asmari, first 10 | 7/10 | 10/10 |
| Total | 16/20 | 20/20 |

All 20 minimum adapter inputs and source metadata checks passed. Four returned shortest paths omitted observations, although their recorded full graphs supplied anchored routes. The shortest-column test is presence of both node kinds, not an assertion that the shortest path itself passes the full adapter. There were no failures in these 20 slots. No scientific accuracy, random-corpus coverage, or user-benefit rate follows from these counts. Nd time-series and adsorption-stop tasks still returned only related evidence; these structural findings do not resolve those failures.

Published `graphs/` files are minimal structural projections: original node IDs/types, selected source/page metadata, and original edge identity/endpoints/relation. Semantic text, observations' scientific values, quotations, account metadata and source PDFs are omitted. Projection hashes identify projection bytes; they are not represented as hashes of the original complete graph or new production snapshots. Recorded raw HTTP-body and receipt hashes are separate provenance references. Production snapshot IDs remain unchanged.

The exact adapter was also executed locally against the complete original graph receipts. All 20 outcomes, selected observation IDs and edge IDs matched projected-graph runs (except the necessarily different input-file hash). Every projected field was checked against its original stored value; `consistency.json` records this check. Raw graph receipts remain in the private evidence directory. No provenance edges or snapshots were invented. `manifest.json` hashes the released files.
