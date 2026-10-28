ALTER TABLE `source_artifacts` ADD `artifact_id` text;--> statement-breakpoint
ALTER TABLE `source_artifacts` ADD `content_sha256` text;--> statement-breakpoint
ALTER TABLE `source_artifacts` ADD `content_object_key` text;--> statement-breakpoint
ALTER TABLE `source_artifacts` ADD `content_verified_at` text;--> statement-breakpoint
ALTER TABLE `source_artifacts` ADD `request_hash` text;--> statement-breakpoint
CREATE UNIQUE INDEX `source_artifact_id_idx` ON `source_artifacts` (`artifact_id`) WHERE `artifact_id` IS NOT NULL;--> statement-breakpoint
CREATE UNIQUE INDEX `source_content_sha256_idx` ON `source_artifacts` (`content_sha256`) WHERE `content_sha256` IS NOT NULL;--> statement-breakpoint
CREATE UNIQUE INDEX `source_content_object_key_idx` ON `source_artifacts` (`content_object_key`) WHERE `content_object_key` IS NOT NULL;--> statement-breakpoint
CREATE TRIGGER `source_content_address_insert_check`
BEFORE INSERT ON `source_artifacts`
WHEN
  (NEW.`content_sha256` IS NOT NULL AND (length(NEW.`content_sha256`) != 64 OR NEW.`content_sha256` GLOB '*[^0-9a-f]*'))
  OR (NEW.`artifact_id` IS NOT NULL AND (NEW.`content_sha256` IS NULL OR NEW.`artifact_id` != 'artifact:sha256:' || NEW.`content_sha256`))
  OR (NEW.`request_hash` IS NOT NULL AND (length(NEW.`request_hash`) != 64 OR NEW.`request_hash` GLOB '*[^0-9a-f]*'))
BEGIN
  SELECT RAISE(ABORT, 'invalid source artifact provenance');
END;--> statement-breakpoint
CREATE TRIGGER `source_content_address_update_check`
BEFORE UPDATE OF `artifact_id`, `content_sha256`, `request_hash` ON `source_artifacts`
WHEN
  (NEW.`content_sha256` IS NOT NULL AND (length(NEW.`content_sha256`) != 64 OR NEW.`content_sha256` GLOB '*[^0-9a-f]*'))
  OR (NEW.`artifact_id` IS NOT NULL AND (NEW.`content_sha256` IS NULL OR NEW.`artifact_id` != 'artifact:sha256:' || NEW.`content_sha256`))
  OR (NEW.`request_hash` IS NOT NULL AND (length(NEW.`request_hash`) != 64 OR NEW.`request_hash` GLOB '*[^0-9a-f]*'))
BEGIN
  SELECT RAISE(ABORT, 'invalid source artifact provenance');
END;--> statement-breakpoint
CREATE TRIGGER `source_content_identity_immutable`
BEFORE UPDATE OF `artifact_id`, `content_sha256`, `content_object_key` ON `source_artifacts`
WHEN OLD.`content_sha256` IS NOT NULL AND (
  NEW.`artifact_id` IS NOT OLD.`artifact_id`
  OR NEW.`content_sha256` IS NOT OLD.`content_sha256`
  OR NEW.`content_object_key` IS NOT OLD.`content_object_key`
)
BEGIN
  SELECT RAISE(ABORT, 'source content identity is immutable');
END;--> statement-breakpoint

ALTER TABLE `compile_snapshots` ADD `basis_snapshot_id` text;--> statement-breakpoint
ALTER TABLE `compile_snapshots` ADD `expected_project_version` integer;--> statement-breakpoint
ALTER TABLE `compile_snapshots` ADD `project_version` integer;--> statement-breakpoint
ALTER TABLE `compile_snapshots` ADD `review_seq_cutoff` integer;--> statement-breakpoint
ALTER TABLE `compile_snapshots` ADD `policy_version` text;--> statement-breakpoint
ALTER TABLE `compile_snapshots` ADD `schema_version` text DEFAULT '0.2' NOT NULL;--> statement-breakpoint
ALTER TABLE `compile_snapshots` ADD `input_hash` text;--> statement-breakpoint
ALTER TABLE `compile_snapshots` ADD `snapshot_id` text;--> statement-breakpoint
ALTER TABLE `compile_snapshots` ADD `engine_version` text;--> statement-breakpoint
ALTER TABLE `compile_snapshots` ADD `object_set_hash` text;--> statement-breakpoint
ALTER TABLE `compile_snapshots` ADD `object_set_json` text;--> statement-breakpoint
ALTER TABLE `compile_snapshots` ADD `exclusion_ledger_json` text;--> statement-breakpoint
ALTER TABLE `compile_snapshots` ADD `gates_json` text;--> statement-breakpoint
ALTER TABLE `compile_snapshots` ADD `manifest_hash` text;--> statement-breakpoint
ALTER TABLE `compile_snapshots` ADD `output_manifest_json` text;--> statement-breakpoint
ALTER TABLE `compile_snapshots` ADD `output_manifest_hash` text;--> statement-breakpoint
ALTER TABLE `compile_snapshots` ADD `idempotency_key` text;--> statement-breakpoint
ALTER TABLE `compile_snapshots` ADD `request_hash` text;--> statement-breakpoint
ALTER TABLE `compile_snapshots` ADD `approved_by` text;--> statement-breakpoint
ALTER TABLE `compile_snapshots` ADD `approved_at` text;--> statement-breakpoint
ALTER TABLE `compile_snapshots` ADD `release_ready` integer DEFAULT 0 NOT NULL;--> statement-breakpoint
CREATE INDEX `compile_project_status_idx` ON `compile_snapshots` (`project_id`,`profile`,`status`,`created_at`);--> statement-breakpoint
CREATE UNIQUE INDEX `compile_idempotency_idx` ON `compile_snapshots` (`project_id`,`idempotency_key`) WHERE `idempotency_key` IS NOT NULL;--> statement-breakpoint
CREATE UNIQUE INDEX `compile_manifest_hash_idx` ON `compile_snapshots` (`project_id`,`manifest_hash`) WHERE `manifest_hash` IS NOT NULL;--> statement-breakpoint
CREATE TRIGGER `compile_provenance_insert_check`
BEFORE INSERT ON `compile_snapshots`
WHEN
  (NEW.`expected_project_version` IS NOT NULL AND NEW.`expected_project_version` < 0)
  OR (NEW.`project_version` IS NOT NULL AND NEW.`project_version` < 0)
  OR (NEW.`review_seq_cutoff` IS NOT NULL AND NEW.`review_seq_cutoff` < 0)
  OR (NEW.`input_hash` IS NOT NULL AND (length(NEW.`input_hash`) != 64 OR NEW.`input_hash` GLOB '*[^0-9a-f]*'))
  OR (NEW.`object_set_hash` IS NOT NULL AND (length(NEW.`object_set_hash`) != 64 OR NEW.`object_set_hash` GLOB '*[^0-9a-f]*'))
  OR (NEW.`manifest_hash` IS NOT NULL AND (length(NEW.`manifest_hash`) != 64 OR NEW.`manifest_hash` GLOB '*[^0-9a-f]*'))
  OR (NEW.`output_manifest_hash` IS NOT NULL AND (length(NEW.`output_manifest_hash`) != 64 OR NEW.`output_manifest_hash` GLOB '*[^0-9a-f]*'))
  OR (NEW.`request_hash` IS NOT NULL AND (length(NEW.`request_hash`) != 64 OR NEW.`request_hash` GLOB '*[^0-9a-f]*'))
BEGIN
  SELECT RAISE(ABORT, 'invalid compile provenance');
END;--> statement-breakpoint
CREATE TRIGGER `compile_provenance_update_check`
BEFORE UPDATE OF `expected_project_version`, `project_version`, `review_seq_cutoff`, `input_hash`, `object_set_hash`, `manifest_hash`, `output_manifest_hash`, `request_hash` ON `compile_snapshots`
WHEN
  (NEW.`expected_project_version` IS NOT NULL AND NEW.`expected_project_version` < 0)
  OR (NEW.`project_version` IS NOT NULL AND NEW.`project_version` < 0)
  OR (NEW.`review_seq_cutoff` IS NOT NULL AND NEW.`review_seq_cutoff` < 0)
  OR (NEW.`input_hash` IS NOT NULL AND (length(NEW.`input_hash`) != 64 OR NEW.`input_hash` GLOB '*[^0-9a-f]*'))
  OR (NEW.`object_set_hash` IS NOT NULL AND (length(NEW.`object_set_hash`) != 64 OR NEW.`object_set_hash` GLOB '*[^0-9a-f]*'))
  OR (NEW.`manifest_hash` IS NOT NULL AND (length(NEW.`manifest_hash`) != 64 OR NEW.`manifest_hash` GLOB '*[^0-9a-f]*'))
  OR (NEW.`output_manifest_hash` IS NOT NULL AND (length(NEW.`output_manifest_hash`) != 64 OR NEW.`output_manifest_hash` GLOB '*[^0-9a-f]*'))
  OR (NEW.`request_hash` IS NOT NULL AND (length(NEW.`request_hash`) != 64 OR NEW.`request_hash` GLOB '*[^0-9a-f]*'))
BEGIN
  SELECT RAISE(ABORT, 'invalid compile provenance');
END;--> statement-breakpoint
CREATE TRIGGER `compile_snapshot_immutable_update`
BEFORE UPDATE ON `compile_snapshots`
BEGIN
  SELECT RAISE(ABORT, 'compile snapshot is immutable');
END;--> statement-breakpoint
CREATE TRIGGER `compile_snapshot_immutable_delete`
BEFORE DELETE ON `compile_snapshots`
BEGIN
  SELECT RAISE(ABORT, 'compile snapshot is immutable');
END;--> statement-breakpoint

CREATE TABLE `extraction_batches` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`artifact_id` text NOT NULL,
	`parser_version` text NOT NULL,
	`parser_mode` text NOT NULL,
	`status` text DEFAULT 'proposed' NOT NULL,
	`candidate_count` integer NOT NULL,
	`idempotency_key` text NOT NULL,
	`request_hash` text NOT NULL,
	`created_by` text NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	CONSTRAINT "extraction_batch_artifact_identity_check" CHECK(length(`artifact_id`) = 80 AND `artifact_id` GLOB 'artifact:sha256:*' AND substr(`artifact_id`, 17) NOT GLOB '*[^0-9a-f]*'),
	CONSTRAINT "extraction_batch_parser_mode_check" CHECK(`parser_mode` IN ('auto', 'manual')),
	CONSTRAINT "extraction_batch_status_check" CHECK(`status` IN ('proposed', 'stored', 'failed')),
	CONSTRAINT "extraction_batch_count_check" CHECK(`candidate_count` >= 0),
	CONSTRAINT "extraction_batch_request_hash_check" CHECK(length(`request_hash`) = 64 AND `request_hash` NOT GLOB '*[^0-9a-f]*')
);--> statement-breakpoint
CREATE UNIQUE INDEX `extraction_batch_idempotency_idx` ON `extraction_batches` (`project_id`,`created_by`,`idempotency_key`);--> statement-breakpoint
CREATE INDEX `extraction_batch_artifact_idx` ON `extraction_batches` (`project_id`,`artifact_id`,`created_at`);--> statement-breakpoint
CREATE TRIGGER `extraction_batch_provenance_immutable`
BEFORE UPDATE OF `project_id`, `artifact_id`, `parser_version`, `parser_mode`, `candidate_count`, `idempotency_key`, `request_hash`, `created_by`, `created_at` ON `extraction_batches`
BEGIN
  SELECT RAISE(ABORT, 'extraction batch provenance is immutable');
END;--> statement-breakpoint

CREATE TABLE `evidence_candidates` (
	`id` text PRIMARY KEY NOT NULL,
	`batch_id` text NOT NULL,
	`project_id` text NOT NULL,
	`artifact_id` text NOT NULL,
	`kind` text NOT NULL,
	`original_text` text NOT NULL,
	`normalized_text` text NOT NULL,
	`locator_json` text NOT NULL,
	`quote_hash` text NOT NULL,
	`raw_token` text,
	`value_semantic` text DEFAULT 'not_applicable' NOT NULL,
	`parsed_value` text,
	`unit` text,
	`independence_unit` text NOT NULL,
	`independence_key` text NOT NULL,
	`parser_version` text NOT NULL,
	`parser_mode` text NOT NULL,
	`review_state` text DEFAULT 'proposed' NOT NULL,
	`revision` integer DEFAULT 1 NOT NULL,
	`stance_suggestion` text,
	`quality_json` text DEFAULT '{}' NOT NULL,
	`idempotency_key` text NOT NULL,
	`request_hash` text NOT NULL,
	`created_by` text NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	`updated_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	FOREIGN KEY (`batch_id`) REFERENCES `extraction_batches`(`id`) ON UPDATE no action ON DELETE restrict,
	CONSTRAINT "candidate_kind_check" CHECK(`kind` IN ('claim', 'fact', 'observation', 'measurement', 'mechanism', 'relation', 'hypothesis')),
	CONSTRAINT "candidate_artifact_identity_check" CHECK(length(`artifact_id`) = 80 AND `artifact_id` GLOB 'artifact:sha256:*' AND substr(`artifact_id`, 17) NOT GLOB '*[^0-9a-f]*'),
	CONSTRAINT "candidate_quote_hash_check" CHECK(length(`quote_hash`) = 64 AND `quote_hash` NOT GLOB '*[^0-9a-f]*'),
	CONSTRAINT "candidate_value_semantic_check" CHECK(`value_semantic` IN ('numeric', 'zero', 'below_detection', 'missing', 'not_applicable', 'invalid', 'text_only', 'above_range')),
	CONSTRAINT "candidate_parser_mode_check" CHECK(`parser_mode` IN ('auto', 'manual')),
	CONSTRAINT "candidate_review_state_check" CHECK(`review_state` IN ('proposed', 'accepted', 'flagged', 'rejected', 'superseded')),
	CONSTRAINT "candidate_revision_check" CHECK(`revision` >= 1),
	CONSTRAINT "candidate_stance_check" CHECK(`stance_suggestion` IS NULL OR `stance_suggestion` IN ('supports', 'opposes', 'mixed', 'neutral', 'qualifies')),
	CONSTRAINT "candidate_request_hash_check" CHECK(length(`request_hash`) = 64 AND `request_hash` NOT GLOB '*[^0-9a-f]*')
);--> statement-breakpoint
CREATE INDEX `candidate_project_review_idx` ON `evidence_candidates` (`project_id`,`review_state`,`created_at`);--> statement-breakpoint
CREATE INDEX `candidate_batch_idx` ON `evidence_candidates` (`batch_id`,`id`);--> statement-breakpoint
CREATE INDEX `candidate_artifact_kind_idx` ON `evidence_candidates` (`artifact_id`,`kind`,`created_at`);--> statement-breakpoint
CREATE UNIQUE INDEX `candidate_batch_locator_idx` ON `evidence_candidates` (`batch_id`,`locator_json`,`kind`);--> statement-breakpoint
CREATE UNIQUE INDEX `candidate_idempotency_idx` ON `evidence_candidates` (`project_id`,`created_by`,`idempotency_key`);--> statement-breakpoint
CREATE TRIGGER `candidate_provenance_immutable`
BEFORE UPDATE OF `batch_id`, `project_id`, `artifact_id`, `kind`, `original_text`, `normalized_text`, `locator_json`, `quote_hash`, `raw_token`, `value_semantic`, `parsed_value`, `unit`, `independence_unit`, `independence_key`, `parser_version`, `parser_mode`, `stance_suggestion`, `quality_json`, `idempotency_key`, `request_hash`, `created_by`, `created_at` ON `evidence_candidates`
BEGIN
  SELECT RAISE(ABORT, 'candidate provenance is immutable');
END;--> statement-breakpoint

CREATE TABLE `compile_outputs` (
	`id` text PRIMARY KEY NOT NULL,
	`compile_snapshot_id` text NOT NULL,
	`project_id` text NOT NULL,
	`kind` text NOT NULL,
	`artifact_id` text NOT NULL,
	`content_sha256` text NOT NULL,
	`object_key` text NOT NULL,
	`mime_type` text NOT NULL,
	`byte_size` integer NOT NULL,
	`status` text DEFAULT 'stored' NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	FOREIGN KEY (`compile_snapshot_id`) REFERENCES `compile_snapshots`(`id`) ON UPDATE no action ON DELETE restrict,
	CONSTRAINT "compile_output_kind_check" CHECK(`kind` IN ('manifest', 'graph', 'csv', 'brief', 'review_ledger')),
	CONSTRAINT "compile_output_status_check" CHECK(`status` IN ('pending', 'stored', 'verified', 'failed')),
	CONSTRAINT "compile_output_size_check" CHECK(`byte_size` > 0),
	CONSTRAINT "compile_output_sha256_check" CHECK(length(`content_sha256`) = 64 AND `content_sha256` NOT GLOB '*[^0-9a-f]*'),
	CONSTRAINT "compile_output_artifact_identity_check" CHECK(`artifact_id` = 'artifact:sha256:' || `content_sha256`)
);--> statement-breakpoint
CREATE UNIQUE INDEX `compile_output_kind_idx` ON `compile_outputs` (`compile_snapshot_id`,`kind`);--> statement-breakpoint
CREATE UNIQUE INDEX `compile_output_object_key_idx` ON `compile_outputs` (`object_key`);--> statement-breakpoint
CREATE INDEX `compile_output_project_created_idx` ON `compile_outputs` (`project_id`,`created_at`);--> statement-breakpoint
CREATE TRIGGER `compile_output_immutable_update`
BEFORE UPDATE ON `compile_outputs`
BEGIN
  SELECT RAISE(ABORT, 'compile output is immutable');
END;--> statement-breakpoint
CREATE TRIGGER `compile_output_immutable_delete`
BEFORE DELETE ON `compile_outputs`
BEGIN
  SELECT RAISE(ABORT, 'compile output is immutable');
END;--> statement-breakpoint
PRAGMA optimize;
