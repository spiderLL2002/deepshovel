CREATE TRIGGER `artifact_versions_delete_write_barrier`
BEFORE INSERT ON `artifact_versions`
WHEN EXISTS (
  SELECT 1 FROM `artifact_deletion_authorizations` d
  WHERE d.artifact_id = NEW.artifact_id
)
BEGIN SELECT RAISE(ABORT, 'artifact deletion is in progress'); END;
--> statement-breakpoint
CREATE TRIGGER `compile_jobs_delete_write_barrier`
BEFORE INSERT ON `compile_jobs`
WHEN EXISTS (
  SELECT 1 FROM `artifact_versions` v
  JOIN `artifact_deletion_authorizations` d ON d.artifact_id = v.artifact_id
  WHERE v.id = NEW.artifact_version_id
)
BEGIN SELECT RAISE(ABORT, 'artifact deletion is in progress'); END;
