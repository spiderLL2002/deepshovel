PRAGMA foreign_keys=OFF;--> statement-breakpoint
CREATE TABLE `__new_citation_reception_runs` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`citing_snapshot_id` text NOT NULL,
	`reference_id` text NOT NULL,
	`cited_snapshot_id` text NOT NULL,
	`processor_version` text NOT NULL,
	`resolution_method` text NOT NULL,
	`resolution_confidence` real NOT NULL,
	`extraction_run_id` text NOT NULL,
	`extraction_model` text NOT NULL,
	`extraction_prompt_version` text NOT NULL,
	`assertions_sha256` text NOT NULL,
	`verification_run_id` text,
	`verifier_model` text,
	`verifier_prompt_version` text,
	`assertion_count` integer NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	FOREIGN KEY (`cited_snapshot_id`) REFERENCES `evidence_graph_snapshots`(`id`) ON UPDATE no action ON DELETE restrict,
	FOREIGN KEY (`citing_snapshot_id`,`reference_id`) REFERENCES `bibliographic_references`(`citing_snapshot_id`,`id`) ON UPDATE no action ON DELETE restrict,
	CONSTRAINT "citation_reception_run_id_check" CHECK(length("id") = 84
	    AND "id" GLOB 'citation-run:sha256:*'
	    AND substr("id", 21) NOT GLOB '*[^0-9a-f]*'),
	CONSTRAINT "citation_reception_run_processor_check" CHECK("processor_version" = 'citation-reception-processor/1.0.0'),
	CONSTRAINT "citation_reception_run_resolution_check" CHECK("resolution_method" IN ('openalex_work_id', 'doi', 'title')
	    AND "resolution_confidence" >= 0 AND "resolution_confidence" <= 1),
	CONSTRAINT "citation_reception_run_hash_check" CHECK(length("assertions_sha256") = 64
	    AND "assertions_sha256" NOT GLOB '*[^0-9a-f]*'),
	CONSTRAINT "citation_reception_run_count_check" CHECK("assertion_count" >= 0),
	CONSTRAINT "citation_reception_run_verifier_check" CHECK(("assertion_count" = 0
	      AND "verification_run_id" IS NULL AND "verifier_model" IS NULL AND "verifier_prompt_version" IS NULL)
	    OR ("assertion_count" > 0
	      AND "verification_run_id" IS NOT NULL AND "verifier_model" IS NOT NULL AND "verifier_prompt_version" IS NOT NULL))
);
--> statement-breakpoint
INSERT INTO `__new_citation_reception_runs`("id", "project_id", "citing_snapshot_id", "reference_id", "cited_snapshot_id", "processor_version", "resolution_method", "resolution_confidence", "extraction_run_id", "extraction_model", "extraction_prompt_version", "assertions_sha256", "verification_run_id", "verifier_model", "verifier_prompt_version", "assertion_count", "created_at") SELECT "id", "project_id", "citing_snapshot_id", "reference_id", "cited_snapshot_id", "processor_version", "resolution_method", "resolution_confidence", "extraction_run_id", "extraction_model", "extraction_prompt_version", "assertions_sha256", "verification_run_id", "verifier_model", "verifier_prompt_version", "assertion_count", "created_at" FROM `citation_reception_runs`;--> statement-breakpoint
DROP TABLE `citation_reception_runs`;--> statement-breakpoint
ALTER TABLE `__new_citation_reception_runs` RENAME TO `citation_reception_runs`;--> statement-breakpoint
PRAGMA foreign_keys=ON;--> statement-breakpoint
CREATE UNIQUE INDEX `citation_reception_run_pair_idx` ON `citation_reception_runs` (`processor_version`,`citing_snapshot_id`,`reference_id`,`cited_snapshot_id`);--> statement-breakpoint
CREATE INDEX `citation_reception_run_target_idx` ON `citation_reception_runs` (`project_id`,`cited_snapshot_id`,`processor_version`,`created_at`);--> statement-breakpoint
CREATE INDEX `citation_reception_run_source_idx` ON `citation_reception_runs` (`project_id`,`citing_snapshot_id`,`processor_version`,`created_at`);
--> statement-breakpoint
CREATE TRIGGER `citation_reception_runs_delete_write_barrier`
BEFORE INSERT ON `citation_reception_runs`
WHEN EXISTS (
	SELECT 1 FROM `evidence_graph_snapshots` s
	JOIN `artifact_versions` v ON v.id = s.root_artifact_version_id
	JOIN `artifact_deletion_authorizations` d ON d.artifact_id = v.artifact_id
	WHERE s.id IN (NEW.citing_snapshot_id, NEW.cited_snapshot_id)
)
BEGIN SELECT RAISE(ABORT, 'artifact deletion is in progress'); END;
--> statement-breakpoint
CREATE TRIGGER `citation_reception_runs_immutable_update`
BEFORE UPDATE ON `citation_reception_runs`
BEGIN SELECT RAISE(ABORT, 'citation reception run is immutable'); END;
