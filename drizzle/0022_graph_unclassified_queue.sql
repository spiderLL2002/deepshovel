CREATE TABLE `graph_unclassified_snapshots` (
	`snapshot_id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`created_at` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX `graph_unclassified_queue_idx` ON `graph_unclassified_snapshots` (`project_id`,`created_at`,`snapshot_id`);--> statement-breakpoint
CREATE TRIGGER graph_unclassified_activate AFTER INSERT ON evidence_active_snapshots
WHEN NOT EXISTS (SELECT 1 FROM openalex_paper_classifications WHERE snapshot_id=NEW.snapshot_id)
BEGIN
 INSERT INTO graph_unclassified_snapshots(snapshot_id,project_id,created_at) VALUES(NEW.snapshot_id,NEW.project_id,NEW.created_at);
END;
--> statement-breakpoint
CREATE TRIGGER graph_unclassified_deactivate AFTER DELETE ON evidence_active_snapshots
BEGIN
 DELETE FROM graph_unclassified_snapshots WHERE snapshot_id=OLD.snapshot_id;
END;
--> statement-breakpoint
CREATE TRIGGER graph_unclassified_classified AFTER INSERT ON openalex_paper_classifications
BEGIN
 DELETE FROM graph_unclassified_snapshots WHERE snapshot_id=NEW.snapshot_id;
END;
--> statement-breakpoint
CREATE TRIGGER graph_unclassified_removed AFTER DELETE ON openalex_paper_classifications
BEGIN
 INSERT OR IGNORE INTO graph_unclassified_snapshots(snapshot_id,project_id,created_at)
 SELECT snapshot_id,project_id,created_at FROM evidence_active_snapshots WHERE snapshot_id=OLD.snapshot_id;
END;
--> statement-breakpoint
INSERT INTO graph_unclassified_snapshots(snapshot_id,project_id,created_at)
SELECT active.snapshot_id,active.project_id,active.created_at FROM evidence_active_snapshots active
WHERE NOT EXISTS (SELECT 1 FROM openalex_paper_classifications classification WHERE classification.snapshot_id=active.snapshot_id);
