PRAGMA foreign_keys=OFF;--> statement-breakpoint
CREATE TABLE `__new_graph_review_events` (
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
	CONSTRAINT "graph_review_decision_check" CHECK("decision" IN ('accepted', 'rejected', 'needs_revision', 'withdrawn'))
);
--> statement-breakpoint
INSERT INTO `__new_graph_review_events`("seq", "id", "project_id", "snapshot_id", "decision", "rationale", "reviewer", "client_request_id", "created_at") SELECT "seq", "id", "project_id", "snapshot_id", "decision", "rationale", "reviewer", "client_request_id", "created_at" FROM `graph_review_events`;--> statement-breakpoint
DROP TABLE `graph_review_events`;--> statement-breakpoint
ALTER TABLE `__new_graph_review_events` RENAME TO `graph_review_events`;--> statement-breakpoint
PRAGMA foreign_keys=ON;--> statement-breakpoint
CREATE UNIQUE INDEX `graph_review_events_id_unique` ON `graph_review_events` (`id`);--> statement-breakpoint
CREATE UNIQUE INDEX `graph_review_request_idx` ON `graph_review_events` (`project_id`,`reviewer`,`client_request_id`);--> statement-breakpoint
CREATE INDEX `graph_review_snapshot_seq_idx` ON `graph_review_events` (`snapshot_id`,`seq`);--> statement-breakpoint
-- Rebuilding the table drops the append-only triggers installed by 0003.
CREATE TRIGGER `graph_review_events_immutable_update`
BEFORE UPDATE ON `graph_review_events`
BEGIN
  SELECT RAISE(ABORT, 'graph review event is immutable');
END;
--> statement-breakpoint
CREATE TRIGGER `graph_review_events_immutable_delete`
BEFORE DELETE ON `graph_review_events`
BEGIN
  SELECT RAISE(ABORT, 'graph review event is immutable');
END;
