CREATE TABLE `library_artifact_summaries` (
	`search_id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`artifact_id` text NOT NULL,
	`project_id` text NOT NULL,
	`created_at` text NOT NULL,
	`preferred_name` text NOT NULL,
	`original_file_name` text NOT NULL,
	`latest_version_id` text,
	`version_count` integer NOT NULL,
	`latest_compile_job_id` text,
	`latest_snapshot_id` text,
	`library_status` text NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX library_artifact_id_idx ON library_artifact_summaries(artifact_id);
--> statement-breakpoint
CREATE INDEX `library_project_created_idx` ON `library_artifact_summaries` (`project_id`,`created_at`,`artifact_id`);--> statement-breakpoint
CREATE INDEX `library_project_status_created_idx` ON `library_artifact_summaries` (`project_id`,`library_status`,`created_at`,`artifact_id`);--> statement-breakpoint
CREATE VIRTUAL TABLE library_name_search USING fts5(tokens,content='',contentless_delete=1,detail='none',tokenize='ascii');
--> statement-breakpoint
CREATE TABLE `library_status_counts` (
	`project_id` text NOT NULL,
	`status` text NOT NULL,
	`artifact_count` integer NOT NULL,
	PRIMARY KEY(`project_id`, `status`)
);
--> statement-breakpoint
CREATE TABLE `library_verification_summaries` (
	`verification_id` text PRIMARY KEY NOT NULL,
	`limitation_count` integer NOT NULL
);

--> statement-breakpoint
CREATE VIEW library_artifact_projection AS
SELECT a.id AS artifact_id,a.project_id,a.created_at,a.preferred_name,
 COALESCE(v.original_file_name,'') AS original_file_name,v.id AS latest_version_id,
 (SELECT COUNT(*) FROM artifact_versions vc WHERE vc.artifact_id=a.id) AS version_count,
 j.id AS latest_compile_job_id,s.id AS latest_snapshot_id,
 CASE WHEN j.status IN ('queued','running') THEN 'processing'
 WHEN s.id IS NOT NULL THEN 'completed' WHEN j.status='failed' THEN 'failed' ELSE 'pending' END AS library_status
FROM artifacts a
LEFT JOIN artifact_versions v ON v.id=(SELECT id FROM artifact_versions WHERE artifact_id=a.id ORDER BY version_number DESC,created_at DESC,id DESC LIMIT 1)
LEFT JOIN compile_jobs j ON j.id=(SELECT id FROM compile_jobs WHERE project_id=a.project_id AND artifact_version_id=v.id ORDER BY updated_at DESC,created_at DESC,id DESC LIMIT 1)
LEFT JOIN evidence_graph_snapshots s ON s.id=(SELECT id FROM evidence_graph_snapshots WHERE project_id=a.project_id AND root_artifact_version_id=v.id ORDER BY created_at DESC,id DESC LIMIT 1);
--> statement-breakpoint
CREATE TRIGGER library_refresh_artifacts_insert AFTER INSERT ON artifacts BEGIN INSERT INTO library_artifact_summaries(artifact_id,project_id,created_at,preferred_name,original_file_name,latest_version_id,version_count,latest_compile_job_id,latest_snapshot_id,library_status) SELECT * FROM library_artifact_projection WHERE artifact_id=NEW.id ON CONFLICT(artifact_id) DO UPDATE SET project_id=excluded.project_id,created_at=excluded.created_at,preferred_name=excluded.preferred_name,original_file_name=excluded.original_file_name,latest_version_id=excluded.latest_version_id,version_count=excluded.version_count,latest_compile_job_id=excluded.latest_compile_job_id,latest_snapshot_id=excluded.latest_snapshot_id,library_status=excluded.library_status; END;
--> statement-breakpoint
CREATE TRIGGER library_refresh_artifacts_update AFTER UPDATE ON artifacts BEGIN INSERT INTO library_artifact_summaries(artifact_id,project_id,created_at,preferred_name,original_file_name,latest_version_id,version_count,latest_compile_job_id,latest_snapshot_id,library_status) SELECT * FROM library_artifact_projection WHERE artifact_id=NEW.id ON CONFLICT(artifact_id) DO UPDATE SET project_id=excluded.project_id,created_at=excluded.created_at,preferred_name=excluded.preferred_name,original_file_name=excluded.original_file_name,latest_version_id=excluded.latest_version_id,version_count=excluded.version_count,latest_compile_job_id=excluded.latest_compile_job_id,latest_snapshot_id=excluded.latest_snapshot_id,library_status=excluded.library_status; END;
--> statement-breakpoint
CREATE TRIGGER library_refresh_artifact_versions_insert AFTER INSERT ON artifact_versions BEGIN INSERT INTO library_artifact_summaries(artifact_id,project_id,created_at,preferred_name,original_file_name,latest_version_id,version_count,latest_compile_job_id,latest_snapshot_id,library_status) SELECT * FROM library_artifact_projection WHERE artifact_id=NEW.artifact_id ON CONFLICT(artifact_id) DO UPDATE SET project_id=excluded.project_id,created_at=excluded.created_at,preferred_name=excluded.preferred_name,original_file_name=excluded.original_file_name,latest_version_id=excluded.latest_version_id,version_count=excluded.version_count,latest_compile_job_id=excluded.latest_compile_job_id,latest_snapshot_id=excluded.latest_snapshot_id,library_status=excluded.library_status; END;
--> statement-breakpoint
CREATE TRIGGER library_refresh_artifact_versions_update AFTER UPDATE ON artifact_versions BEGIN INSERT INTO library_artifact_summaries(artifact_id,project_id,created_at,preferred_name,original_file_name,latest_version_id,version_count,latest_compile_job_id,latest_snapshot_id,library_status) SELECT * FROM library_artifact_projection WHERE artifact_id=NEW.artifact_id ON CONFLICT(artifact_id) DO UPDATE SET project_id=excluded.project_id,created_at=excluded.created_at,preferred_name=excluded.preferred_name,original_file_name=excluded.original_file_name,latest_version_id=excluded.latest_version_id,version_count=excluded.version_count,latest_compile_job_id=excluded.latest_compile_job_id,latest_snapshot_id=excluded.latest_snapshot_id,library_status=excluded.library_status; END;
--> statement-breakpoint
CREATE TRIGGER library_refresh_artifact_versions_delete AFTER DELETE ON artifact_versions BEGIN INSERT INTO library_artifact_summaries(artifact_id,project_id,created_at,preferred_name,original_file_name,latest_version_id,version_count,latest_compile_job_id,latest_snapshot_id,library_status) SELECT * FROM library_artifact_projection WHERE artifact_id=OLD.artifact_id ON CONFLICT(artifact_id) DO UPDATE SET project_id=excluded.project_id,created_at=excluded.created_at,preferred_name=excluded.preferred_name,original_file_name=excluded.original_file_name,latest_version_id=excluded.latest_version_id,version_count=excluded.version_count,latest_compile_job_id=excluded.latest_compile_job_id,latest_snapshot_id=excluded.latest_snapshot_id,library_status=excluded.library_status; END;
--> statement-breakpoint
CREATE TRIGGER library_refresh_compile_jobs_insert AFTER INSERT ON compile_jobs BEGIN INSERT INTO library_artifact_summaries(artifact_id,project_id,created_at,preferred_name,original_file_name,latest_version_id,version_count,latest_compile_job_id,latest_snapshot_id,library_status) SELECT * FROM library_artifact_projection WHERE artifact_id=(SELECT artifact_id FROM artifact_versions WHERE id=NEW.artifact_version_id) ON CONFLICT(artifact_id) DO UPDATE SET project_id=excluded.project_id,created_at=excluded.created_at,preferred_name=excluded.preferred_name,original_file_name=excluded.original_file_name,latest_version_id=excluded.latest_version_id,version_count=excluded.version_count,latest_compile_job_id=excluded.latest_compile_job_id,latest_snapshot_id=excluded.latest_snapshot_id,library_status=excluded.library_status; END;
--> statement-breakpoint
CREATE TRIGGER library_refresh_compile_jobs_update AFTER UPDATE ON compile_jobs BEGIN INSERT INTO library_artifact_summaries(artifact_id,project_id,created_at,preferred_name,original_file_name,latest_version_id,version_count,latest_compile_job_id,latest_snapshot_id,library_status) SELECT * FROM library_artifact_projection WHERE artifact_id=(SELECT artifact_id FROM artifact_versions WHERE id=NEW.artifact_version_id) ON CONFLICT(artifact_id) DO UPDATE SET project_id=excluded.project_id,created_at=excluded.created_at,preferred_name=excluded.preferred_name,original_file_name=excluded.original_file_name,latest_version_id=excluded.latest_version_id,version_count=excluded.version_count,latest_compile_job_id=excluded.latest_compile_job_id,latest_snapshot_id=excluded.latest_snapshot_id,library_status=excluded.library_status; END;
--> statement-breakpoint
CREATE TRIGGER library_refresh_compile_jobs_delete AFTER DELETE ON compile_jobs BEGIN INSERT INTO library_artifact_summaries(artifact_id,project_id,created_at,preferred_name,original_file_name,latest_version_id,version_count,latest_compile_job_id,latest_snapshot_id,library_status) SELECT * FROM library_artifact_projection WHERE artifact_id=(SELECT artifact_id FROM artifact_versions WHERE id=OLD.artifact_version_id) ON CONFLICT(artifact_id) DO UPDATE SET project_id=excluded.project_id,created_at=excluded.created_at,preferred_name=excluded.preferred_name,original_file_name=excluded.original_file_name,latest_version_id=excluded.latest_version_id,version_count=excluded.version_count,latest_compile_job_id=excluded.latest_compile_job_id,latest_snapshot_id=excluded.latest_snapshot_id,library_status=excluded.library_status; END;
--> statement-breakpoint
CREATE TRIGGER library_refresh_evidence_graph_snapshots_insert AFTER INSERT ON evidence_graph_snapshots BEGIN INSERT INTO library_artifact_summaries(artifact_id,project_id,created_at,preferred_name,original_file_name,latest_version_id,version_count,latest_compile_job_id,latest_snapshot_id,library_status) SELECT * FROM library_artifact_projection WHERE artifact_id=(SELECT artifact_id FROM artifact_versions WHERE id=NEW.root_artifact_version_id) ON CONFLICT(artifact_id) DO UPDATE SET project_id=excluded.project_id,created_at=excluded.created_at,preferred_name=excluded.preferred_name,original_file_name=excluded.original_file_name,latest_version_id=excluded.latest_version_id,version_count=excluded.version_count,latest_compile_job_id=excluded.latest_compile_job_id,latest_snapshot_id=excluded.latest_snapshot_id,library_status=excluded.library_status; END;
--> statement-breakpoint
CREATE TRIGGER library_refresh_evidence_graph_snapshots_update AFTER UPDATE ON evidence_graph_snapshots BEGIN INSERT INTO library_artifact_summaries(artifact_id,project_id,created_at,preferred_name,original_file_name,latest_version_id,version_count,latest_compile_job_id,latest_snapshot_id,library_status) SELECT * FROM library_artifact_projection WHERE artifact_id=(SELECT artifact_id FROM artifact_versions WHERE id=NEW.root_artifact_version_id) ON CONFLICT(artifact_id) DO UPDATE SET project_id=excluded.project_id,created_at=excluded.created_at,preferred_name=excluded.preferred_name,original_file_name=excluded.original_file_name,latest_version_id=excluded.latest_version_id,version_count=excluded.version_count,latest_compile_job_id=excluded.latest_compile_job_id,latest_snapshot_id=excluded.latest_snapshot_id,library_status=excluded.library_status; END;
--> statement-breakpoint
CREATE TRIGGER library_refresh_evidence_graph_snapshots_delete AFTER DELETE ON evidence_graph_snapshots BEGIN INSERT INTO library_artifact_summaries(artifact_id,project_id,created_at,preferred_name,original_file_name,latest_version_id,version_count,latest_compile_job_id,latest_snapshot_id,library_status) SELECT * FROM library_artifact_projection WHERE artifact_id=(SELECT artifact_id FROM artifact_versions WHERE id=OLD.root_artifact_version_id) ON CONFLICT(artifact_id) DO UPDATE SET project_id=excluded.project_id,created_at=excluded.created_at,preferred_name=excluded.preferred_name,original_file_name=excluded.original_file_name,latest_version_id=excluded.latest_version_id,version_count=excluded.version_count,latest_compile_job_id=excluded.latest_compile_job_id,latest_snapshot_id=excluded.latest_snapshot_id,library_status=excluded.library_status; END;
--> statement-breakpoint
CREATE TRIGGER library_remove_artifact AFTER DELETE ON artifacts BEGIN DELETE FROM library_artifact_summaries WHERE artifact_id=OLD.id; END;
--> statement-breakpoint
CREATE TRIGGER library_summary_insert AFTER INSERT ON library_artifact_summaries BEGIN
 INSERT INTO library_status_counts VALUES(NEW.project_id,NEW.library_status,1) ON CONFLICT(project_id,status) DO UPDATE SET artifact_count=artifact_count+1;
INSERT INTO library_name_search(rowid,tokens)
 WITH RECURSIVE names(value) AS (SELECT lower(NEW.preferred_name) UNION SELECT lower(NEW.original_file_name)),
 positions(value,pos) AS (SELECT value,1 FROM names WHERE length(value)>0 UNION ALL SELECT value,pos+1 FROM positions WHERE pos<length(value)), sizes(n) AS (VALUES(1),(2),(3)),
 grams(token) AS (SELECT DISTINCT 'u'||printf('%x',unicode(substr(value,pos,1)))||iif(n>=2,'x'||printf('%x',unicode(substr(value,pos+1,1))), '')||iif(n=3,'x'||printf('%x',unicode(substr(value,pos+2,1))), '') FROM positions CROSS JOIN sizes WHERE pos+n-1<=length(value)
 UNION SELECT 'p'||lower(hex(NEW.project_id)) UNION SELECT 's'||NEW.library_status)
 SELECT NEW.search_id,group_concat(token,' ') FROM grams; END;
--> statement-breakpoint
CREATE TRIGGER library_summary_delete AFTER DELETE ON library_artifact_summaries BEGIN
 UPDATE library_status_counts SET artifact_count=artifact_count-1 WHERE project_id=OLD.project_id AND status=OLD.library_status;
 DELETE FROM library_name_search WHERE rowid=OLD.search_id; END;
--> statement-breakpoint
CREATE TRIGGER library_summary_status AFTER UPDATE ON library_artifact_summaries WHEN OLD.library_status<>NEW.library_status OR OLD.project_id<>NEW.project_id BEGIN
 UPDATE library_status_counts SET artifact_count=artifact_count-1 WHERE project_id=OLD.project_id AND status=OLD.library_status;
 INSERT INTO library_status_counts VALUES(NEW.project_id,NEW.library_status,1) ON CONFLICT(project_id,status) DO UPDATE SET artifact_count=artifact_count+1; END;
--> statement-breakpoint
CREATE TRIGGER library_summary_names AFTER UPDATE ON library_artifact_summaries WHEN OLD.preferred_name<>NEW.preferred_name OR OLD.original_file_name<>NEW.original_file_name OR OLD.library_status<>NEW.library_status OR OLD.project_id<>NEW.project_id BEGIN
 DELETE FROM library_name_search WHERE rowid=NEW.search_id;
INSERT INTO library_name_search(rowid,tokens)
 WITH RECURSIVE names(value) AS (SELECT lower(NEW.preferred_name) UNION SELECT lower(NEW.original_file_name)),
 positions(value,pos) AS (SELECT value,1 FROM names WHERE length(value)>0 UNION ALL SELECT value,pos+1 FROM positions WHERE pos<length(value)), sizes(n) AS (VALUES(1),(2),(3)),
 grams(token) AS (SELECT DISTINCT 'u'||printf('%x',unicode(substr(value,pos,1)))||iif(n>=2,'x'||printf('%x',unicode(substr(value,pos+1,1))), '')||iif(n=3,'x'||printf('%x',unicode(substr(value,pos+2,1))), '') FROM positions CROSS JOIN sizes WHERE pos+n-1<=length(value)
 UNION SELECT 'p'||lower(hex(NEW.project_id)) UNION SELECT 's'||NEW.library_status)
 SELECT NEW.search_id,group_concat(token,' ') FROM grams; END;
--> statement-breakpoint
CREATE TRIGGER library_verification_insert AFTER INSERT ON verification_runs BEGIN INSERT INTO library_verification_summaries VALUES(NEW.id,(SELECT COUNT(DISTINCT limitation.value) FROM json_each(NEW.report_json,'$.assessments') assessment,json_each(assessment.value,'$.limitations') limitation WHERE limitation.type='text')); END;
--> statement-breakpoint
CREATE TRIGGER library_verification_delete AFTER DELETE ON verification_runs BEGIN DELETE FROM library_verification_summaries WHERE verification_id=OLD.id; END;
--> statement-breakpoint
INSERT INTO library_verification_summaries SELECT vr.id,(SELECT COUNT(DISTINCT limitation.value) FROM json_each(vr.report_json,'$.assessments') assessment,json_each(assessment.value,'$.limitations') limitation WHERE limitation.type='text') FROM verification_runs vr;
--> statement-breakpoint
INSERT INTO library_artifact_summaries(artifact_id,project_id,created_at,preferred_name,original_file_name,latest_version_id,version_count,latest_compile_job_id,latest_snapshot_id,library_status) SELECT * FROM library_artifact_projection ORDER BY created_at,artifact_id;
