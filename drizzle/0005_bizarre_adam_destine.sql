CREATE TABLE `compile_jobs` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`artifact_version_id` text NOT NULL,
	`upstream_job_id` text NOT NULL,
	`status` text NOT NULL,
	`stage` text NOT NULL,
	`progress` integer NOT NULL,
	`timings_json` text NOT NULL,
	`error_json` text,
	`snapshot_id` text,
	`client_request_id` text NOT NULL,
	`request_hash` text NOT NULL,
	`created_by` text NOT NULL,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL,
	`expires_at` text NOT NULL,
	FOREIGN KEY (`artifact_version_id`) REFERENCES `artifact_versions`(`id`) ON UPDATE no action ON DELETE restrict,
	FOREIGN KEY (`snapshot_id`) REFERENCES `evidence_graph_snapshots`(`id`) ON UPDATE no action ON DELETE restrict,
	CONSTRAINT "compile_job_status_check" CHECK("compile_jobs"."status" IN ('queued', 'running', 'completed', 'failed')),
	CONSTRAINT "compile_job_stage_check" CHECK("compile_jobs"."stage" IN ('queued', 'parsing', 'extracting', 'validating', 'importing', 'completed', 'failed')),
	CONSTRAINT "compile_job_progress_check" CHECK("compile_jobs"."progress" >= 0 AND "compile_jobs"."progress" <= 100),
	CONSTRAINT "compile_job_request_hash_check" CHECK(length("compile_jobs"."request_hash") = 64 AND "compile_jobs"."request_hash" NOT GLOB '*[^0-9a-f]*')
);
--> statement-breakpoint
CREATE UNIQUE INDEX `compile_job_upstream_idx` ON `compile_jobs` (`project_id`,`upstream_job_id`);--> statement-breakpoint
CREATE UNIQUE INDEX `compile_job_request_idx` ON `compile_jobs` (`project_id`,`created_by`,`client_request_id`);--> statement-breakpoint
CREATE INDEX `compile_job_artifact_created_idx` ON `compile_jobs` (`artifact_version_id`,`created_at`);--> statement-breakpoint
CREATE INDEX `compile_job_status_updated_idx` ON `compile_jobs` (`project_id`,`status`,`updated_at`);