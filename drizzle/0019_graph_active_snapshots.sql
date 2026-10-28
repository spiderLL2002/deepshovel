CREATE TABLE `evidence_active_snapshots` (
	`snapshot_id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`index_version` text NOT NULL,
	`paper_id` text NOT NULL,
	`paper_family_id` text NOT NULL,
	`document_count` integer NOT NULL,
	`created_at` text NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `evidence_active_paper_idx` ON `evidence_active_snapshots` (`project_id`,`index_version`,`paper_id`);--> statement-breakpoint
CREATE INDEX `evidence_active_family_idx` ON `evidence_active_snapshots` (`project_id`,`paper_family_id`);--> statement-breakpoint
CREATE TABLE `evidence_family_snapshots` (
	`snapshot_id` text NOT NULL,
	`project_id` text NOT NULL,
	`index_version` text NOT NULL,
	`paper_id` text NOT NULL,
	`paper_family_id` text NOT NULL,
	`document_count` integer NOT NULL,
	`created_at` text NOT NULL,
	PRIMARY KEY(`project_id`, `index_version`, `paper_family_id`)
);
--> statement-breakpoint
CREATE UNIQUE INDEX `evidence_family_snapshot_idx` ON `evidence_family_snapshots` (`snapshot_id`);--> statement-breakpoint
CREATE INDEX `evidence_family_paper_winner_idx` ON `evidence_family_snapshots` (`project_id`,`index_version`,`paper_id`,julianday("created_at") DESC,"created_at" DESC,"snapshot_id" DESC);--> statement-breakpoint
CREATE INDEX `evidence_manifest_family_winner_idx` ON `evidence_retrieval_manifests` (`project_id`,`index_version`,`paper_family_id`,julianday("created_at") DESC,"created_at" DESC,"snapshot_id" DESC);
--> statement-breakpoint
-- Backfill metadata once. The source manifest is the transaction's completion marker.
INSERT INTO evidence_family_snapshots
SELECT snapshot_id,project_id,index_version,paper_id,paper_family_id,document_count,created_at FROM (
 SELECT m.snapshot_id,m.project_id,m.index_version,m.paper_id,m.paper_family_id,m.document_count,m.created_at,
 ROW_NUMBER() OVER(PARTITION BY project_id,index_version,paper_family_id
 ORDER BY julianday(created_at) DESC,created_at DESC,snapshot_id DESC) AS rank
 FROM evidence_retrieval_manifests m WHERE index_version='evidence-retrieval/2.0'
) WHERE rank=1;
--> statement-breakpoint
INSERT INTO evidence_active_snapshots
SELECT snapshot_id,project_id,index_version,paper_id,paper_family_id,document_count,created_at FROM (
 SELECT f.*,ROW_NUMBER() OVER(PARTITION BY project_id,index_version,paper_id
 ORDER BY julianday(created_at) DESC,created_at DESC,snapshot_id DESC) AS rank
 FROM evidence_family_snapshots f
) WHERE rank=1;
--> statement-breakpoint
-- Explicit delete+insert lets downstream graph counters observe every activation.
-- INSERT OR REPLACE would skip DELETE triggers when recursive_triggers is off.
CREATE TRIGGER evidence_family_insert AFTER INSERT ON evidence_family_snapshots BEGIN
 DELETE FROM evidence_active_snapshots
 WHERE project_id=NEW.project_id AND index_version=NEW.index_version AND paper_id=NEW.paper_id
 AND snapshot_id IS NOT (SELECT snapshot_id FROM evidence_family_snapshots
 WHERE project_id=NEW.project_id AND index_version=NEW.index_version AND paper_id=NEW.paper_id
 ORDER BY julianday(created_at) DESC,created_at DESC,snapshot_id DESC LIMIT 1);
 INSERT OR IGNORE INTO evidence_active_snapshots
 SELECT snapshot_id,project_id,index_version,paper_id,paper_family_id,document_count,created_at
 FROM evidence_family_snapshots
 WHERE project_id=NEW.project_id AND index_version=NEW.index_version AND paper_id=NEW.paper_id
 ORDER BY julianday(created_at) DESC,created_at DESC,snapshot_id DESC LIMIT 1;
END;
--> statement-breakpoint
CREATE TRIGGER evidence_family_delete AFTER DELETE ON evidence_family_snapshots BEGIN
 DELETE FROM evidence_active_snapshots
 WHERE project_id=OLD.project_id AND index_version=OLD.index_version AND paper_id=OLD.paper_id
 AND snapshot_id IS NOT (SELECT snapshot_id FROM evidence_family_snapshots
 WHERE project_id=OLD.project_id AND index_version=OLD.index_version AND paper_id=OLD.paper_id
 ORDER BY julianday(created_at) DESC,created_at DESC,snapshot_id DESC LIMIT 1);
 INSERT OR IGNORE INTO evidence_active_snapshots
 SELECT snapshot_id,project_id,index_version,paper_id,paper_family_id,document_count,created_at
 FROM evidence_family_snapshots
 WHERE project_id=OLD.project_id AND index_version=OLD.index_version AND paper_id=OLD.paper_id
 ORDER BY julianday(created_at) DESC,created_at DESC,snapshot_id DESC LIMIT 1;
END;
--> statement-breakpoint
CREATE TRIGGER evidence_manifest_activate AFTER INSERT ON evidence_retrieval_manifests
WHEN NEW.index_version='evidence-retrieval/2.0' BEGIN
 DELETE FROM evidence_family_snapshots
 WHERE project_id=NEW.project_id AND index_version=NEW.index_version AND paper_family_id=NEW.paper_family_id
 AND snapshot_id IS NOT (SELECT snapshot_id FROM evidence_retrieval_manifests
 WHERE project_id=NEW.project_id AND index_version=NEW.index_version AND paper_family_id=NEW.paper_family_id
 ORDER BY julianday(created_at) DESC,created_at DESC,snapshot_id DESC LIMIT 1);
 INSERT OR IGNORE INTO evidence_family_snapshots
 SELECT snapshot_id,project_id,index_version,paper_id,paper_family_id,document_count,created_at
 FROM evidence_retrieval_manifests
 WHERE project_id=NEW.project_id AND index_version=NEW.index_version AND paper_family_id=NEW.paper_family_id
 ORDER BY julianday(created_at) DESC,created_at DESC,snapshot_id DESC LIMIT 1;
END;
--> statement-breakpoint
CREATE TRIGGER evidence_manifest_deactivate AFTER DELETE ON evidence_retrieval_manifests
WHEN OLD.index_version='evidence-retrieval/2.0' BEGIN
 DELETE FROM evidence_family_snapshots
 WHERE project_id=OLD.project_id AND index_version=OLD.index_version AND paper_family_id=OLD.paper_family_id
 AND snapshot_id IS NOT (SELECT snapshot_id FROM evidence_retrieval_manifests
 WHERE project_id=OLD.project_id AND index_version=OLD.index_version AND paper_family_id=OLD.paper_family_id
 ORDER BY julianday(created_at) DESC,created_at DESC,snapshot_id DESC LIMIT 1);
 INSERT OR IGNORE INTO evidence_family_snapshots
 SELECT snapshot_id,project_id,index_version,paper_id,paper_family_id,document_count,created_at
 FROM evidence_retrieval_manifests
 WHERE project_id=OLD.project_id AND index_version=OLD.index_version AND paper_family_id=OLD.paper_family_id
 ORDER BY julianday(created_at) DESC,created_at DESC,snapshot_id DESC LIMIT 1;
END;
