CREATE TABLE `artifact_deletion_authorizations` (
	`artifact_id` text PRIMARY KEY NOT NULL,
	`requested_by` text NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	FOREIGN KEY (`artifact_id`) REFERENCES `artifacts`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE TRIGGER `artifact_deletion_authorization_no_active_jobs`
BEFORE INSERT ON `artifact_deletion_authorizations`
WHEN EXISTS (
  SELECT 1 FROM `compile_jobs` j
  JOIN `artifact_versions` v ON v.id = j.artifact_version_id
  WHERE v.artifact_id = NEW.artifact_id AND j.status IN ('queued', 'running')
)
BEGIN SELECT RAISE(ABORT, 'artifact has active compile jobs'); END;
--> statement-breakpoint
CREATE TRIGGER `artifacts_delete_requires_authorization`
BEFORE DELETE ON `artifacts`
WHEN NOT EXISTS (SELECT 1 FROM `artifact_deletion_authorizations` d WHERE d.artifact_id = OLD.id)
BEGIN SELECT RAISE(ABORT, 'artifact deletion is not authorized'); END;
--> statement-breakpoint
DROP TRIGGER `artifact_versions_immutable_delete`;
--> statement-breakpoint
CREATE TRIGGER `artifact_versions_immutable_delete` BEFORE DELETE ON `artifact_versions`
WHEN NOT EXISTS (SELECT 1 FROM `artifact_deletion_authorizations` d WHERE d.artifact_id = OLD.artifact_id)
BEGIN SELECT RAISE(ABORT, 'artifact version is immutable'); END;
--> statement-breakpoint
DROP TRIGGER `evidence_graph_snapshots_immutable_delete`;
--> statement-breakpoint
CREATE TRIGGER `evidence_graph_snapshots_immutable_delete` BEFORE DELETE ON `evidence_graph_snapshots`
WHEN NOT EXISTS (
  SELECT 1 FROM `artifact_versions` v JOIN `artifact_deletion_authorizations` d ON d.artifact_id = v.artifact_id
  WHERE v.id = OLD.root_artifact_version_id
)
BEGIN SELECT RAISE(ABORT, 'evidence graph snapshot is immutable'); END;
--> statement-breakpoint
DROP TRIGGER `verification_runs_immutable_delete`;
--> statement-breakpoint
CREATE TRIGGER `verification_runs_immutable_delete` BEFORE DELETE ON `verification_runs`
WHEN NOT EXISTS (
  SELECT 1 FROM `evidence_graph_snapshots` s
  JOIN `artifact_versions` v ON v.id = s.root_artifact_version_id
  JOIN `artifact_deletion_authorizations` d ON d.artifact_id = v.artifact_id
  WHERE s.id = OLD.snapshot_id
)
BEGIN SELECT RAISE(ABORT, 'verification run is immutable'); END;
--> statement-breakpoint
DROP TRIGGER `graph_review_events_immutable_delete`;
--> statement-breakpoint
CREATE TRIGGER `graph_review_events_immutable_delete` BEFORE DELETE ON `graph_review_events`
WHEN NOT EXISTS (
  SELECT 1 FROM `evidence_graph_snapshots` s
  JOIN `artifact_versions` v ON v.id = s.root_artifact_version_id
  JOIN `artifact_deletion_authorizations` d ON d.artifact_id = v.artifact_id
  WHERE s.id = OLD.snapshot_id
)
BEGIN SELECT RAISE(ABORT, 'graph review event is immutable'); END;
--> statement-breakpoint
DROP TRIGGER `evidence_claim_index_immutable_delete`;
--> statement-breakpoint
CREATE TRIGGER `evidence_claim_index_immutable_delete` BEFORE DELETE ON `evidence_claim_index`
WHEN NOT EXISTS (
  SELECT 1 FROM `evidence_graph_snapshots` s
  JOIN `artifact_versions` v ON v.id = s.root_artifact_version_id
  JOIN `artifact_deletion_authorizations` d ON d.artifact_id = v.artifact_id
  WHERE s.id = OLD.snapshot_id
)
BEGIN SELECT RAISE(ABORT, 'evidence claim index is immutable'); END;
--> statement-breakpoint
DROP TRIGGER `evidence_claim_terms_immutable_delete`;
--> statement-breakpoint
CREATE TRIGGER `evidence_claim_terms_immutable_delete` BEFORE DELETE ON `evidence_claim_terms`
WHEN NOT EXISTS (
  SELECT 1 FROM `evidence_graph_snapshots` s
  JOIN `artifact_versions` v ON v.id = s.root_artifact_version_id
  JOIN `artifact_deletion_authorizations` d ON d.artifact_id = v.artifact_id
  WHERE s.id = OLD.snapshot_id
)
BEGIN SELECT RAISE(ABORT, 'evidence claim terms are immutable'); END;
--> statement-breakpoint
DROP TRIGGER `evidence_claim_relations_immutable_delete`;
--> statement-breakpoint
CREATE TRIGGER `evidence_claim_relations_immutable_delete` BEFORE DELETE ON `evidence_claim_relations`
WHEN NOT EXISTS (
  SELECT 1 FROM `evidence_graph_snapshots` s
  JOIN `artifact_versions` v ON v.id = s.root_artifact_version_id
  JOIN `artifact_deletion_authorizations` d ON d.artifact_id = v.artifact_id
  WHERE s.id = OLD.snapshot_id
)
BEGIN SELECT RAISE(ABORT, 'evidence claim relations are immutable'); END;
--> statement-breakpoint
DROP TRIGGER `evidence_assessment_index_immutable_delete`;
--> statement-breakpoint
CREATE TRIGGER `evidence_assessment_index_immutable_delete` BEFORE DELETE ON `evidence_assessment_index`
WHEN NOT EXISTS (
  SELECT 1 FROM `evidence_graph_snapshots` s
  JOIN `artifact_versions` v ON v.id = s.root_artifact_version_id
  JOIN `artifact_deletion_authorizations` d ON d.artifact_id = v.artifact_id
  WHERE s.id = OLD.snapshot_id
)
BEGIN SELECT RAISE(ABORT, 'evidence assessment index is immutable'); END;
--> statement-breakpoint
DROP TRIGGER `evidence_search_manifests_immutable_delete`;
--> statement-breakpoint
CREATE TRIGGER `evidence_search_manifests_immutable_delete` BEFORE DELETE ON `evidence_search_manifests`
WHEN NOT EXISTS (
  SELECT 1 FROM `evidence_graph_snapshots` s
  JOIN `artifact_versions` v ON v.id = s.root_artifact_version_id
  JOIN `artifact_deletion_authorizations` d ON d.artifact_id = v.artifact_id
  WHERE s.id = OLD.snapshot_id
)
BEGIN SELECT RAISE(ABORT, 'evidence search manifest is immutable'); END;
--> statement-breakpoint
DROP TRIGGER `audit_events_immutable_delete`;
--> statement-breakpoint
CREATE TRIGGER `audit_events_immutable_delete` BEFORE DELETE ON `audit_events`
WHEN NOT EXISTS (
  SELECT 1 FROM `artifact_deletion_authorizations` d WHERE
    OLD.entity_id = d.artifact_id OR
    OLD.entity_id IN (SELECT id FROM `artifact_versions` WHERE artifact_id = d.artifact_id) OR
    OLD.entity_id IN (
      SELECT j.id FROM `compile_jobs` j JOIN `artifact_versions` v ON v.id = j.artifact_version_id
      WHERE v.artifact_id = d.artifact_id
    ) OR
    OLD.entity_id IN (
      SELECT s.id FROM `evidence_graph_snapshots` s JOIN `artifact_versions` v ON v.id = s.root_artifact_version_id
      WHERE v.artifact_id = d.artifact_id
    ) OR
    OLD.entity_id IN (
      SELECT r.id FROM `verification_runs` r
      JOIN `evidence_graph_snapshots` s ON s.id = r.snapshot_id
      JOIN `artifact_versions` v ON v.id = s.root_artifact_version_id
      WHERE v.artifact_id = d.artifact_id
    )
)
BEGIN SELECT RAISE(ABORT, 'audit event is immutable'); END;
