CREATE TABLE `compile_snapshots` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`run_id` text NOT NULL,
	`profile` text NOT NULL,
	`status` text NOT NULL,
	`claims_count` integer NOT NULL,
	`evidence_count` integer NOT NULL,
	`conflicts_count` integer NOT NULL,
	`invalidated_count` integer NOT NULL,
	`manifest_json` text NOT NULL,
	`created_by` text NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);
--> statement-breakpoint
CREATE INDEX `compile_project_created_idx` ON `compile_snapshots` (`project_id`,`created_at`);--> statement-breakpoint
CREATE TABLE `review_decisions` (
	`seq` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`id` text NOT NULL,
	`project_id` text NOT NULL,
	`target_id` text NOT NULL,
	`target_type` text NOT NULL,
	`decision` text NOT NULL,
	`rationale` text NOT NULL,
	`previous_state` text NOT NULL,
	`next_state` text NOT NULL,
	`basis_snapshot` text DEFAULT 'S-019' NOT NULL,
	`reviewer` text NOT NULL,
	`client_request_id` text NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `review_decisions_id_unique` ON `review_decisions` (`id`);--> statement-breakpoint
CREATE INDEX `review_project_target_idx` ON `review_decisions` (`project_id`,`target_id`,`seq`);--> statement-breakpoint
CREATE UNIQUE INDEX `review_client_request_idx` ON `review_decisions` (`project_id`,`reviewer`,`client_request_id`);--> statement-breakpoint
CREATE TABLE `source_artifacts` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`file_name` text NOT NULL,
	`source_kind` text NOT NULL,
	`mime_type` text NOT NULL,
	`declared_size_bytes` integer NOT NULL,
	`stored_size_bytes` integer,
	`object_key` text NOT NULL,
	`etag` text,
	`status` text DEFAULT 'pending' NOT NULL,
	`source_version` integer DEFAULT 1 NOT NULL,
	`replaces_source_id` text,
	`created_by` text NOT NULL,
	`client_request_id` text NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	`uploaded_at` text
);
--> statement-breakpoint
CREATE INDEX `source_project_status_idx` ON `source_artifacts` (`project_id`,`status`,`created_at`);--> statement-breakpoint
CREATE UNIQUE INDEX `source_client_request_idx` ON `source_artifacts` (`project_id`,`created_by`,`client_request_id`);--> statement-breakpoint
CREATE TABLE `version_events` (
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
CREATE UNIQUE INDEX `version_events_id_unique` ON `version_events` (`id`);--> statement-breakpoint
CREATE INDEX `version_project_seq_idx` ON `version_events` (`project_id`,`seq`);