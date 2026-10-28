# Reading a reuse record

The authoritative integrity unit is the exported, exact `canonical_payload` UTF-8 string and its `record_id`. Its parsed value must strictly equal `payload`. The verifier checks this pair; screenshots and convenient field summaries are views of the record.

**Retrieval context and the decision layer.** Import retains the recorded result. Build preserves its original shortest path and all other fields while adding or replacing only `observation_evidence_path` with a deterministic recorded-graph union. The exported `original_result` is this working result at save time. Selected observation/anchor references, prepared fields, and the operator’s conclusion belong to the outer reuse-decision layer, not the retrieval-result contract. The unmodified input file remains separate. When the input is an envelope, only its `result` object is within the import contract; additional transport metadata such as an outer `selection` is outside the saved-result guarantee.

**Routes and relations.** The displayed selected route is one inspection trace through the selected union, not the entire stored graph. Other included observation branches remain in that union; the complete graph is a separate input. Retrieval `supports`, a route’s terminal `infers`, and an operator’s `qualified_reuse` have different roles: query-result classification, recorded reasoning relation, and a reuse decision. Neither structural linkage nor a file-hash match establishes scientific support.

**File checks.** A recorded `verified` state describes the file checked when saving. Running `verify_reuse.py` with `--source-pdf` independently hashes those supplied bytes against the selected-anchor binding. Without that argument, the current file check is `not_rechecked`, even if the saved record says verified.

**Source fields and prepared text.** Original anchor selectors, including any left-column rectangle coordinates, remain recorded source metadata. The prepared textual locator names the right column, while prepared values and textual differences are operator-entered representations; no corrected rectangle coordinates are generated. A display comparison does not relocate or repair the original anchor.

**Versions.** Historical schema 1.4 uses its original unrestricted directed-reachability check. Schema 1.5 requires the adapter’s typed reasoning relations. The verifier labels the policy used. Old records are not migrated or rehashed. The explicit `do_not_reuse` choice and additional observation-representation comparison remain compatible string/field extensions within 1.5; they do not retroactively change old exports.

Use the [README](README.md) for current verification commands and provenance files. These checks verify integrity and recorded structure; the scientific reading remains the operator’s entry.
