# Fixed-ID annotation inspection samples

The six IDs q16, q30, q50, q70, q90 and q110 were fixed before this export to provide a small, inspectable spread through the saved query sequence. All six exist in the114-query paired set, so no substitutions occurred. This is not random sampling and cannot estimate annotation reliability or system accuracy.

Each JSON exports the **entire saved pool** for its query, including unfavorable/irrelevant candidates; no scores were edited or candidates removed. It retains the question, recorded expected answer and full original per-query rubric, canonical/original claim, scope, source identifiers, anchor pages where recorded, grade, separately recorded scope_error and rationale. This permits semantic inspection of the existing reasoning.

Source quotations are limited to800 characters in total per candidate in stored anchor order. Each anchor records whether its quote was truncated and the original character length. Additional anchors remain represented with location metadata and an empty excerpt once the budget is spent. This mechanical limit is not a claim that the excerpt contains every fact needed to judge the candidate. Canonical and original claim fields and the original rubric are retained as requested. Missing source metadata remains null. No full papers/PDFs, credentials, private filesystem paths or application code are included.

Exact underlying annotation model IDs and full original annotation prompts are not recorded in the frozen label files and are marked accordingly. Filenames are provenance labels, not proof of annotator identity. No annotator count or agreement rate is asserted.

manifest.json records every selected query, pool file, candidate count, hash and validation result. verify.py checks every exported grade/scope_error pair against ../qrels.json and the complete ID set against ../pool_ids.json. The original114-query metrics and frozen evidence are unchanged. This export has not been published.
