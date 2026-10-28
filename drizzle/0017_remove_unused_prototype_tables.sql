-- The prototype was replaced by immutable artifacts/evidence graphs in 0002.
-- None of these tables belongs to the current schema or has a runtime caller.
-- Drop dependent tables first; current scientific graph tables remain intact.
DROP TABLE `compile_outputs`;
--> statement-breakpoint
DROP TABLE `evidence_candidates`;
--> statement-breakpoint
DROP TABLE `extraction_batches`;
--> statement-breakpoint
DROP TABLE `compile_snapshots`;
--> statement-breakpoint
DROP TABLE `review_decisions`;
--> statement-breakpoint
DROP TABLE `source_artifacts`;
--> statement-breakpoint
DROP TABLE `version_events`;
