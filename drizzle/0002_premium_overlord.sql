CREATE TABLE `artifact_versions` (
	`id` text PRIMARY KEY NOT NULL,
	`artifact_id` text NOT NULL,
	`version_number` integer NOT NULL,
	`sha256` text NOT NULL,
	`source_locator` text NOT NULL,
	`media_type` text NOT NULL,
	`original_file_name` text NOT NULL,
	`byte_size` integer NOT NULL,
	`object_key` text NOT NULL,
	`etag` text,
	`client_request_id` text NOT NULL,
	`request_hash` text NOT NULL,
	`created_by` text NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	FOREIGN KEY (`artifact_id`) REFERENCES `artifacts`(`id`) ON UPDATE no action ON DELETE restrict,
	CONSTRAINT "artifact_version_id_check" CHECK("artifact_versions"."id" = 'artifact-version:sha256:' || "artifact_versions"."sha256"),
	CONSTRAINT "artifact_version_number_check" CHECK("artifact_versions"."version_number" >= 1),
	CONSTRAINT "artifact_version_sha256_check" CHECK(length("artifact_versions"."sha256") = 64 AND "artifact_versions"."sha256" NOT GLOB '*[^0-9a-f]*'),
	CONSTRAINT "artifact_version_request_hash_check" CHECK(length("artifact_versions"."request_hash") = 64 AND "artifact_versions"."request_hash" NOT GLOB '*[^0-9a-f]*'),
	CONSTRAINT "artifact_version_size_check" CHECK("artifact_versions"."byte_size" > 0)
);
--> statement-breakpoint
CREATE UNIQUE INDEX `artifact_version_sha256_idx` ON `artifact_versions` (`sha256`);--> statement-breakpoint
CREATE UNIQUE INDEX `artifact_version_object_key_idx` ON `artifact_versions` (`object_key`);--> statement-breakpoint
CREATE UNIQUE INDEX `artifact_version_number_idx` ON `artifact_versions` (`artifact_id`,`version_number`);--> statement-breakpoint
CREATE UNIQUE INDEX `artifact_version_request_idx` ON `artifact_versions` (`created_by`,`client_request_id`);--> statement-breakpoint
CREATE INDEX `artifact_version_artifact_created_idx` ON `artifact_versions` (`artifact_id`,`created_at`);--> statement-breakpoint
CREATE TABLE `artifacts` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`preferred_name` text NOT NULL,
	`artifact_type` text NOT NULL,
	`created_by` text NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	CONSTRAINT "artifact_id_check" CHECK(length("artifacts"."id") > 9 AND "artifacts"."id" GLOB 'artifact:*'),
	CONSTRAINT "artifact_type_check" CHECK("artifacts"."artifact_type" IN ('paper', 'figure', 'table', 'dataset', 'code', 'web', 'other'))
);
--> statement-breakpoint
CREATE INDEX `artifact_project_created_idx` ON `artifacts` (`project_id`,`created_at`);--> statement-breakpoint
CREATE TABLE `audit_events` (
	`seq` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`id` text NOT NULL,
	`project_id` text NOT NULL,
	`event_type` text NOT NULL,
	`entity_type` text NOT NULL,
	`entity_id` text NOT NULL,
	`actor` text NOT NULL,
	`payload_json` text NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `audit_events_id_unique` ON `audit_events` (`id`);--> statement-breakpoint
CREATE INDEX `audit_project_seq_idx` ON `audit_events` (`project_id`,`seq`);--> statement-breakpoint
CREATE INDEX `audit_entity_seq_idx` ON `audit_events` (`entity_type`,`entity_id`,`seq`);--> statement-breakpoint
CREATE TABLE `evidence_graph_snapshots` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`root_artifact_version_id` text NOT NULL,
	`schema_version` text NOT NULL,
	`graph_sha256` text NOT NULL,
	`graph_json` text NOT NULL,
	`counts_json` text NOT NULL,
	`extractor_run_id` text NOT NULL,
	`extractor_model` text NOT NULL,
	`extractor_prompt_version` text NOT NULL,
	`created_by` text NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	FOREIGN KEY (`root_artifact_version_id`) REFERENCES `artifact_versions`(`id`) ON UPDATE no action ON DELETE restrict,
	CONSTRAINT "evidence_graph_id_check" CHECK("evidence_graph_snapshots"."id" = 'snapshot:sha256:' || "evidence_graph_snapshots"."graph_sha256"),
	CONSTRAINT "evidence_graph_sha256_check" CHECK(length("evidence_graph_snapshots"."graph_sha256") = 64 AND "evidence_graph_snapshots"."graph_sha256" NOT GLOB '*[^0-9a-f]*')
);
--> statement-breakpoint
CREATE UNIQUE INDEX `evidence_graph_sha256_idx` ON `evidence_graph_snapshots` (`project_id`,`graph_sha256`);--> statement-breakpoint
CREATE UNIQUE INDEX `evidence_graph_run_idx` ON `evidence_graph_snapshots` (`project_id`,`extractor_run_id`);--> statement-breakpoint
CREATE INDEX `evidence_graph_artifact_created_idx` ON `evidence_graph_snapshots` (`root_artifact_version_id`,`created_at`);--> statement-breakpoint
CREATE INDEX `evidence_graph_project_created_idx` ON `evidence_graph_snapshots` (`project_id`,`created_at`);--> statement-breakpoint
CREATE TABLE `graph_review_events` (
	`seq` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`id` text NOT NULL,
	`project_id` text NOT NULL,
	`snapshot_id` text NOT NULL,
	`decision` text NOT NULL,
	`rationale` text NOT NULL,
	`reviewer` text NOT NULL,
	`client_request_id` text NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	FOREIGN KEY (`snapshot_id`) REFERENCES `evidence_graph_snapshots`(`id`) ON UPDATE no action ON DELETE restrict,
	CONSTRAINT "graph_review_decision_check" CHECK("graph_review_events"."decision" IN ('accepted', 'rejected', 'needs_revision'))
);
--> statement-breakpoint
CREATE UNIQUE INDEX `graph_review_events_id_unique` ON `graph_review_events` (`id`);--> statement-breakpoint
CREATE UNIQUE INDEX `graph_review_request_idx` ON `graph_review_events` (`project_id`,`reviewer`,`client_request_id`);--> statement-breakpoint
CREATE INDEX `graph_review_snapshot_seq_idx` ON `graph_review_events` (`snapshot_id`,`seq`);--> statement-breakpoint
CREATE TABLE `verification_runs` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`snapshot_id` text NOT NULL,
	`verifier_run_id` text NOT NULL,
	`verifier_model` text NOT NULL,
	`verifier_prompt_version` text NOT NULL,
	`report_sha256` text NOT NULL,
	`report_json` text NOT NULL,
	`created_by` text NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	FOREIGN KEY (`snapshot_id`) REFERENCES `evidence_graph_snapshots`(`id`) ON UPDATE no action ON DELETE restrict,
	CONSTRAINT "verification_report_sha256_check" CHECK(length("verification_runs"."report_sha256") = 64 AND "verification_runs"."report_sha256" NOT GLOB '*[^0-9a-f]*')
);
--> statement-breakpoint
CREATE UNIQUE INDEX `verification_report_sha256_idx` ON `verification_runs` (`project_id`,`report_sha256`);--> statement-breakpoint
CREATE UNIQUE INDEX `verification_run_id_idx` ON `verification_runs` (`project_id`,`verifier_run_id`);--> statement-breakpoint
CREATE INDEX `verification_snapshot_created_idx` ON `verification_runs` (`snapshot_id`,`created_at`);

-- Legacy compiler tables are deliberately retained as read-inert archive data.
-- The 0.5 application has no code path that reads or writes them. A separate,
-- explicitly approved archival migration may remove them after backup policy is set.
