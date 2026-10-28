-- Durable background work (transactional outbox) processed by the local work
-- pump through POST /api/internal/work/tick. Rows are written in the same D1
-- batch as the facts that create the work (for example a snapshot import), so
-- work is never lost between a commit and an external side effect. Subjects are
-- soft references: a missing subject finishes its item as cancelled.
CREATE TABLE `work_items` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`kind` text NOT NULL,
	`subject_id` text NOT NULL,
	`state` text NOT NULL,
	`priority` integer NOT NULL,
	`attempts` integer NOT NULL DEFAULT 0,
	`max_attempts` integer NOT NULL,
	`next_attempt_at` text NOT NULL,
	`lease_token` text,
	`lease_expires_at` text,
	`progress_json` text NOT NULL DEFAULT '{}',
	`last_error_json` text,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL,
	`completed_at` text,
	CONSTRAINT `work_item_kind_check` CHECK(`kind` IN ('retrieval_index', 'claim_relations')),
	CONSTRAINT `work_item_state_check` CHECK(`state` IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')),
	CONSTRAINT `work_item_attempts_check` CHECK(`attempts` >= 0 AND `max_attempts` >= 1),
	CONSTRAINT `work_item_progress_check` CHECK(json_valid(`progress_json`)),
	CONSTRAINT `work_item_error_check` CHECK(`last_error_json` IS NULL OR json_valid(`last_error_json`)),
	CONSTRAINT `work_item_lease_check` CHECK((`state` = 'running') = (`lease_token` IS NOT NULL))
);
--> statement-breakpoint
CREATE UNIQUE INDEX `work_item_subject_idx` ON `work_items` (`project_id`,`kind`,`subject_id`);
--> statement-breakpoint
CREATE INDEX `work_item_due_idx` ON `work_items` (`project_id`,`state`,`next_attempt_at`,`priority`);
--> statement-breakpoint
CREATE INDEX `work_item_recent_idx` ON `work_items` (`project_id`,`kind`,`updated_at`);
--> statement-breakpoint
-- Compile import recovery: bounded attempts with backoff, the last real cause,
-- an explicit import lease and the R2 copy of the paid Compiler result.
ALTER TABLE `compile_jobs` ADD `import_attempts` integer NOT NULL DEFAULT 0;
--> statement-breakpoint
ALTER TABLE `compile_jobs` ADD `import_lease_until` text;
--> statement-breakpoint
ALTER TABLE `compile_jobs` ADD `next_import_at` text;
--> statement-breakpoint
ALTER TABLE `compile_jobs` ADD `import_error_json` text;
--> statement-breakpoint
ALTER TABLE `compile_jobs` ADD `result_staged` integer NOT NULL DEFAULT 0;
--> statement-breakpoint
-- Snapshots imported before this release have no outbox rows. Enqueue an index
-- check for every active snapshot (already indexed snapshots finish after one
-- manifest check) and Claim-relation analysis only for active snapshots that
-- still have Claims without a completion, i.e. analysis that was interrupted
-- because no browser tab was polling it.
INSERT OR IGNORE INTO `work_items`
	(`id`, `project_id`, `kind`, `subject_id`, `state`, `priority`, `attempts`,
	 `max_attempts`, `next_attempt_at`, `progress_json`, `created_at`, `updated_at`)
SELECT 'retrieval_index:' || a.snapshot_id, a.project_id, 'retrieval_index', a.snapshot_id,
	'pending', 50, 0, 8, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), '{}',
	strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
FROM `evidence_active_snapshots` a;
--> statement-breakpoint
INSERT OR IGNORE INTO `work_items`
	(`id`, `project_id`, `kind`, `subject_id`, `state`, `priority`, `attempts`,
	 `max_attempts`, `next_attempt_at`, `progress_json`, `created_at`, `updated_at`)
SELECT 'claim_relations:' || a.snapshot_id, a.project_id, 'claim_relations', a.snapshot_id,
	'pending', 100, 0, 6, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), '{}',
	strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
FROM `evidence_active_snapshots` a
WHERE a.document_count > 0 AND EXISTS (
	SELECT 1 FROM `evidence_retrieval_documents` d
	LEFT JOIN `corpus_claim_graph_completions` c
		ON c.snapshot_id = d.snapshot_id AND c.claim_id = d.claim_id
		AND c.processor_version = 'evidence-knowledge-graph/4.0'
	WHERE d.snapshot_id = a.snapshot_id AND c.snapshot_id IS NULL
);
