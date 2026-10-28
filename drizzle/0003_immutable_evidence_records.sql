-- Durable evidence records are append-only. Application routes expose no
-- mutation path; these triggers keep that invariant true for direct SQL too.
CREATE TRIGGER `artifact_versions_immutable_update`
BEFORE UPDATE ON `artifact_versions`
BEGIN
  SELECT RAISE(ABORT, 'artifact version is immutable');
END;
--> statement-breakpoint
CREATE TRIGGER `artifact_versions_immutable_delete`
BEFORE DELETE ON `artifact_versions`
BEGIN
  SELECT RAISE(ABORT, 'artifact version is immutable');
END;
--> statement-breakpoint
CREATE TRIGGER `evidence_graph_snapshots_immutable_update`
BEFORE UPDATE ON `evidence_graph_snapshots`
BEGIN
  SELECT RAISE(ABORT, 'evidence graph snapshot is immutable');
END;
--> statement-breakpoint
CREATE TRIGGER `evidence_graph_snapshots_immutable_delete`
BEFORE DELETE ON `evidence_graph_snapshots`
BEGIN
  SELECT RAISE(ABORT, 'evidence graph snapshot is immutable');
END;
--> statement-breakpoint
CREATE TRIGGER `verification_runs_immutable_update`
BEFORE UPDATE ON `verification_runs`
BEGIN
  SELECT RAISE(ABORT, 'verification run is immutable');
END;
--> statement-breakpoint
CREATE TRIGGER `verification_runs_immutable_delete`
BEFORE DELETE ON `verification_runs`
BEGIN
  SELECT RAISE(ABORT, 'verification run is immutable');
END;
--> statement-breakpoint
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
--> statement-breakpoint
CREATE TRIGGER `audit_events_immutable_update`
BEFORE UPDATE ON `audit_events`
BEGIN
  SELECT RAISE(ABORT, 'audit event is immutable');
END;
--> statement-breakpoint
CREATE TRIGGER `audit_events_immutable_delete`
BEFORE DELETE ON `audit_events`
BEGIN
  SELECT RAISE(ABORT, 'audit event is immutable');
END;
