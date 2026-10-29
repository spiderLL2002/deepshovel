# Compact reproducibility bundle

Run `python3 evaluate.py` with Python 3 (standard library only). It reads local JSON and prints metrics, then asserts agreement within 1e-12 with expected_metrics.json. No network, model, production server or external package is used.

The frozen table covers 114 common fully judged queries. Three additional successful queries (q75, q112, q118) were excluded because their saved pools omitted newly returned candidates. Twelve queries are legacy broad topics; 102 are newer source-derived questions. No new labels were created for this package.

queries.json holds IDs, questions and batch; pool_ids.json holds candidate claim IDs; qrels.json holds grade 0–3 and the separately assessed scope_error Boolean; runs.json holds the four saved top-12 rankings. No source excerpts, canonical claims, expected-answer text, private filesystem paths, credentials or application source are included. Query texts are research questions, not a redistribution of full papers. IDs are opaque content identifiers.

Grades summarize direct compatible evidence (3), compatible partial answer (2), background or scope conflict (1), and unrelated evidence (0), with query-specific rubrics used in the original assessment. This compact package supports arithmetic reproduction only; it cannot independently validate label correctness without the original restricted source material. scope_error denotes an assessed explicit scope conflict rather than every grade-1 result.

nDCG@10 uses gains 2^grade−1 and the ideal ranking of the explicit judged pool. P@10 counts grades >=2 and divides by 10. Scope mismatch@10 counts scope_error=true and divides by 10. Hit@3 tests whether any of the first three grades is at least2. The script checks all16 table values. Every included ranking has twelve items. All ordinary graded ideal gains are positive. Results do not imply exhaustive corpus recall, expert agreement, or a causal advantage of scoped identifiers. Baseline retrieval uses raw queries; live preview includes query planning and excludes final model reranking/relation classification.

This package has not been published. It is a minimal export from the frozen diagnostic evidence, not the complete experimental environment.

Six fixed-ID annotation-samples now provide complete rubrics and172 candidates with source context for method inspection; they are separate from this arithmetic-only core.
