CREATE TABLE `snapshot_list_counts` (
	`project_id` text NOT NULL,
	`status` text NOT NULL,
	`snapshot_count` integer NOT NULL,
	PRIMARY KEY(`project_id`, `status`)
);
--> statement-breakpoint
CREATE TABLE `snapshot_list_summaries` (
	`snapshot_id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`artifact_id` text NOT NULL,
	`title` text NOT NULL,
	`original_file_name` text NOT NULL,
	`created_at` text NOT NULL,
	`latest_review_id` text,
	`review_status` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX `snapshot_list_created_idx` ON `snapshot_list_summaries` (`project_id`,`created_at`,`snapshot_id`);--> statement-breakpoint
CREATE INDEX `snapshot_list_status_created_idx` ON `snapshot_list_summaries` (`project_id`,`review_status`,`created_at`,`snapshot_id`);--> statement-breakpoint
CREATE INDEX `snapshot_list_artifact_idx` ON `snapshot_list_summaries` (`artifact_id`);
--> statement-breakpoint
CREATE VIEW snapshot_list_projection AS
SELECT s.id AS snapshot_id,s.project_id,a.id AS artifact_id,a.preferred_name AS title,
 av.original_file_name,s.created_at,r.id AS latest_review_id,
 CASE WHEN r.decision='accepted' THEN 'confirmed' WHEN r.decision IS NULL OR r.decision='withdrawn' THEN 'unreviewed' ELSE 'other' END AS review_status
FROM evidence_graph_snapshots s JOIN artifact_versions av ON av.id=s.root_artifact_version_id
JOIN artifacts a ON a.id=av.artifact_id AND a.project_id=s.project_id
LEFT JOIN graph_review_events r ON r.id=(SELECT id FROM graph_review_events WHERE project_id=s.project_id AND snapshot_id=s.id ORDER BY seq DESC LIMIT 1);
--> statement-breakpoint
CREATE TRIGGER snapshot_list_insert AFTER INSERT ON evidence_graph_snapshots BEGIN
 INSERT INTO snapshot_list_summaries SELECT * FROM snapshot_list_projection WHERE snapshot_id=NEW.id; END;
--> statement-breakpoint
CREATE TRIGGER snapshot_list_delete AFTER DELETE ON evidence_graph_snapshots BEGIN
 DELETE FROM snapshot_list_summaries WHERE snapshot_id=OLD.id; END;
--> statement-breakpoint
CREATE TRIGGER snapshot_list_title AFTER UPDATE OF preferred_name ON artifacts BEGIN
 UPDATE snapshot_list_summaries SET title=NEW.preferred_name WHERE artifact_id=NEW.id; END;
--> statement-breakpoint
CREATE TRIGGER snapshot_list_review_insert AFTER INSERT ON graph_review_events BEGIN
 UPDATE snapshot_list_summaries SET (latest_review_id,review_status)=(SELECT latest_review_id,review_status FROM snapshot_list_projection WHERE snapshot_id=NEW.snapshot_id) WHERE snapshot_id=NEW.snapshot_id; END;
--> statement-breakpoint
CREATE TRIGGER snapshot_list_review_delete AFTER DELETE ON graph_review_events BEGIN
 UPDATE snapshot_list_summaries SET (latest_review_id,review_status)=(SELECT latest_review_id,review_status FROM snapshot_list_projection WHERE snapshot_id=OLD.snapshot_id) WHERE snapshot_id=OLD.snapshot_id; END;
--> statement-breakpoint
CREATE TRIGGER snapshot_list_count_insert AFTER INSERT ON snapshot_list_summaries BEGIN
 INSERT INTO snapshot_list_counts VALUES(NEW.project_id,NEW.review_status,1) ON CONFLICT(project_id,status) DO UPDATE SET snapshot_count=snapshot_count+1; END;
--> statement-breakpoint
CREATE TRIGGER snapshot_list_count_delete AFTER DELETE ON snapshot_list_summaries BEGIN
 UPDATE snapshot_list_counts SET snapshot_count=snapshot_count-1 WHERE project_id=OLD.project_id AND status=OLD.review_status; END;
--> statement-breakpoint
CREATE TRIGGER snapshot_list_count_update AFTER UPDATE ON snapshot_list_summaries WHEN OLD.review_status<>NEW.review_status OR OLD.project_id<>NEW.project_id BEGIN
 UPDATE snapshot_list_counts SET snapshot_count=snapshot_count-1 WHERE project_id=OLD.project_id AND status=OLD.review_status;
 INSERT INTO snapshot_list_counts VALUES(NEW.project_id,NEW.review_status,1) ON CONFLICT(project_id,status) DO UPDATE SET snapshot_count=snapshot_count+1; END;
--> statement-breakpoint
INSERT INTO snapshot_list_summaries SELECT * FROM snapshot_list_projection;
