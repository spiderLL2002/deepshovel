CREATE TABLE `bibliographic_references` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`citing_snapshot_id` text NOT NULL,
	`ordinal` integer NOT NULL,
	`label` text NOT NULL,
	`raw_text` text NOT NULL,
	`normalized_doi` text,
	`year` integer,
	`source_block_id` text NOT NULL,
	`page` integer NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	FOREIGN KEY (`citing_snapshot_id`) REFERENCES `citation_source_manifests`(`snapshot_id`) ON UPDATE no action ON DELETE restrict,
	CONSTRAINT "bibliographic_reference_id_check" CHECK(length("bibliographic_references"."id") = 90
    AND "bibliographic_references"."id" GLOB 'citation-reference:sha256:*'
    AND substr("bibliographic_references"."id", 27) NOT GLOB '*[^0-9a-f]*'),
	CONSTRAINT "bibliographic_reference_ordinal_check" CHECK("bibliographic_references"."ordinal" >= 1),
	CONSTRAINT "bibliographic_reference_page_check" CHECK("bibliographic_references"."page" >= 1),
	CONSTRAINT "bibliographic_reference_year_check" CHECK("bibliographic_references"."year" IS NULL
    OR "bibliographic_references"."year" BETWEEN 1800 AND 2199)
);
--> statement-breakpoint
CREATE UNIQUE INDEX `bibliographic_reference_snapshot_id_idx` ON `bibliographic_references` (`citing_snapshot_id`,`id`);--> statement-breakpoint
CREATE UNIQUE INDEX `bibliographic_reference_ordinal_idx` ON `bibliographic_references` (`citing_snapshot_id`,`ordinal`);--> statement-breakpoint
CREATE INDEX `bibliographic_reference_doi_idx` ON `bibliographic_references` (`project_id`,`normalized_doi`,`citing_snapshot_id`);--> statement-breakpoint
CREATE INDEX `bibliographic_reference_snapshot_idx` ON `bibliographic_references` (`citing_snapshot_id`,`ordinal`);--> statement-breakpoint
CREATE TABLE `citation_assertions` (
	`id` text PRIMARY KEY NOT NULL,
	`run_id` text NOT NULL,
	`project_id` text NOT NULL,
	`mention_id` text NOT NULL,
	`attributed_proposition` text NOT NULL,
	`citation_function` text NOT NULL,
	`stance` text NOT NULL,
	`confirmation_basis` text NOT NULL,
	`target_entity_kind` text,
	`target_entity_id` text,
	`citing_evidence_entity_ids_json` text NOT NULL,
	`confidence` real NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	FOREIGN KEY (`run_id`) REFERENCES `citation_reception_runs`(`id`) ON UPDATE no action ON DELETE restrict,
	FOREIGN KEY (`mention_id`) REFERENCES `citation_mentions`(`id`) ON UPDATE no action ON DELETE restrict,
	CONSTRAINT "citation_assertion_id_check" CHECK(length("citation_assertions"."id") = 90
    AND "citation_assertions"."id" GLOB 'citation-assertion:sha256:*'
    AND substr("citation_assertions"."id", 27) NOT GLOB '*[^0-9a-f]*'),
	CONSTRAINT "citation_assertion_function_check" CHECK("citation_assertions"."citation_function" IN ('result_comparison', 'replication', 'critique')),
	CONSTRAINT "citation_assertion_stance_check" CHECK("citation_assertions"."stance" IN ('supports', 'contradicts', 'qualifies', 'unclear')),
	CONSTRAINT "citation_assertion_basis_check" CHECK("citation_assertions"."confirmation_basis" IN ('mere_attribution', 'verbal_endorsement',
    'consistent_with_citing_evidence', 'direct_replication', 'reanalysis', 'meta_analytic_synthesis')),
	CONSTRAINT "citation_assertion_target_check" CHECK(("citation_assertions"."target_entity_kind" IS NULL AND "citation_assertions"."target_entity_id" IS NULL)
    OR ("citation_assertions"."target_entity_kind" IN ('source_anchor', 'structured_visual_evidence', 'observation', 'reasoning', 'atomic_claim')
      AND "citation_assertions"."target_entity_id" IS NOT NULL)),
	CONSTRAINT "citation_assertion_confidence_check" CHECK("citation_assertions"."confidence" >= 0 AND "citation_assertions"."confidence" <= 1)
);
--> statement-breakpoint
CREATE INDEX `citation_assertion_run_idx` ON `citation_assertions` (`run_id`,`created_at`);--> statement-breakpoint
CREATE INDEX `citation_assertion_target_idx` ON `citation_assertions` (`target_entity_kind`,`target_entity_id`);--> statement-breakpoint
CREATE TABLE `citation_assessments` (
	`id` text PRIMARY KEY NOT NULL,
	`assertion_id` text NOT NULL,
	`project_id` text NOT NULL,
	`faithfulness` text NOT NULL,
	`confirmation_basis` text NOT NULL,
	`independence` text NOT NULL,
	`confidence` real NOT NULL,
	`evidence_ids_json` text NOT NULL,
	`limitations_json` text NOT NULL,
	`extraction_sha256` text NOT NULL,
	`verifier_model` text NOT NULL,
	`verifier_prompt_version` text NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	FOREIGN KEY (`assertion_id`) REFERENCES `citation_assertions`(`id`) ON UPDATE no action ON DELETE restrict,
	CONSTRAINT "citation_assessment_id_check" CHECK(length("citation_assessments"."id") = 91
    AND "citation_assessments"."id" GLOB 'citation-assessment:sha256:*'
    AND substr("citation_assessments"."id", 28) NOT GLOB '*[^0-9a-f]*'),
	CONSTRAINT "citation_assessment_faithfulness_check" CHECK("citation_assessments"."faithfulness" IN ('faithful', 'partial', 'overgeneralized',
    'contradicted_by_target', 'not_found', 'ambiguous', 'indirect')),
	CONSTRAINT "citation_assessment_basis_check" CHECK("citation_assessments"."confirmation_basis" IN ('mere_attribution', 'verbal_endorsement',
    'consistent_with_citing_evidence', 'direct_replication', 'reanalysis', 'meta_analytic_synthesis')),
	CONSTRAINT "citation_assessment_independence_check" CHECK("citation_assessments"."independence" IN ('independent', 'shared_authors', 'shared_data', 'derived_data', 'unknown')),
	CONSTRAINT "citation_assessment_confidence_check" CHECK("citation_assessments"."confidence" >= 0 AND "citation_assessments"."confidence" <= 1),
	CONSTRAINT "citation_assessment_hash_check" CHECK(length("citation_assessments"."extraction_sha256") = 64
    AND "citation_assessments"."extraction_sha256" NOT GLOB '*[^0-9a-f]*')
);
--> statement-breakpoint
CREATE UNIQUE INDEX `citation_assessments_assertion_id_unique` ON `citation_assessments` (`assertion_id`);--> statement-breakpoint
CREATE INDEX `citation_assessment_quality_idx` ON `citation_assessments` (`faithfulness`,`confirmation_basis`,`independence`);--> statement-breakpoint
CREATE TABLE `citation_mentions` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`citing_snapshot_id` text NOT NULL,
	`reference_id` text NOT NULL,
	`marker` text NOT NULL,
	`exact_quote` text NOT NULL,
	`context_text` text NOT NULL,
	`source_block_id` text NOT NULL,
	`context_block_ids_json` text NOT NULL,
	`page` integer NOT NULL,
	`bbox_json` text,
	`char_start` integer NOT NULL,
	`char_end` integer NOT NULL,
	`group_size` integer NOT NULL,
	`candidate_functions_json` text NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	FOREIGN KEY (`citing_snapshot_id`,`reference_id`) REFERENCES `bibliographic_references`(`citing_snapshot_id`,`id`) ON UPDATE no action ON DELETE restrict,
	CONSTRAINT "citation_mention_id_check" CHECK(length("citation_mentions"."id") = 88
    AND "citation_mentions"."id" GLOB 'citation-mention:sha256:*'
    AND substr("citation_mentions"."id", 25) NOT GLOB '*[^0-9a-f]*'),
	CONSTRAINT "citation_mention_page_check" CHECK("citation_mentions"."page" >= 1),
	CONSTRAINT "citation_mention_offsets_check" CHECK("citation_mentions"."char_start" >= 0
    AND "citation_mentions"."char_end" > "citation_mentions"."char_start"),
	CONSTRAINT "citation_mention_group_check" CHECK("citation_mentions"."group_size" BETWEEN 1 AND 100)
);
--> statement-breakpoint
CREATE UNIQUE INDEX `citation_mention_snapshot_id_idx` ON `citation_mentions` (`citing_snapshot_id`,`id`);--> statement-breakpoint
CREATE INDEX `citation_mention_reference_idx` ON `citation_mentions` (`citing_snapshot_id`,`reference_id`,`page`);--> statement-breakpoint
CREATE TABLE `citation_reception_runs` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`citing_snapshot_id` text NOT NULL,
	`reference_id` text NOT NULL,
	`cited_snapshot_id` text NOT NULL,
	`processor_version` text NOT NULL,
	`resolution_method` text NOT NULL,
	`resolution_confidence` real NOT NULL,
	`extraction_run_id` text NOT NULL,
	`extraction_model` text NOT NULL,
	`extraction_prompt_version` text NOT NULL,
	`assertions_sha256` text NOT NULL,
	`verification_run_id` text,
	`verifier_model` text,
	`verifier_prompt_version` text,
	`assertion_count` integer NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	FOREIGN KEY (`cited_snapshot_id`) REFERENCES `evidence_graph_snapshots`(`id`) ON UPDATE no action ON DELETE restrict,
	FOREIGN KEY (`citing_snapshot_id`,`reference_id`) REFERENCES `bibliographic_references`(`citing_snapshot_id`,`id`) ON UPDATE no action ON DELETE restrict,
	CONSTRAINT "citation_reception_run_id_check" CHECK(length("citation_reception_runs"."id") = 84
    AND "citation_reception_runs"."id" GLOB 'citation-run:sha256:*'
    AND substr("citation_reception_runs"."id", 21) NOT GLOB '*[^0-9a-f]*'),
	CONSTRAINT "citation_reception_run_processor_check" CHECK("citation_reception_runs"."processor_version" = 'citation-reception-processor/1.0.0'),
	CONSTRAINT "citation_reception_run_resolution_check" CHECK("citation_reception_runs"."resolution_method" IN ('openalex_work_id', 'doi', 'title')
    AND "citation_reception_runs"."resolution_confidence" >= 0 AND "citation_reception_runs"."resolution_confidence" <= 1),
	CONSTRAINT "citation_reception_run_hash_check" CHECK(length("citation_reception_runs"."assertions_sha256") = 64
	    AND "citation_reception_runs"."assertions_sha256" NOT GLOB '*[^0-9a-f]*'),
	CONSTRAINT "citation_reception_run_count_check" CHECK("citation_reception_runs"."assertion_count" >= 0),
	CONSTRAINT "citation_reception_run_verifier_check" CHECK(("citation_reception_runs"."assertion_count" = 0
      AND "citation_reception_runs"."verification_run_id" IS NULL AND "citation_reception_runs"."verifier_model" IS NULL AND "citation_reception_runs"."verifier_prompt_version" IS NULL)
    OR ("citation_reception_runs"."assertion_count" > 0
      AND "citation_reception_runs"."verification_run_id" IS NOT NULL AND "citation_reception_runs"."verifier_model" IS NOT NULL AND "citation_reception_runs"."verifier_prompt_version" IS NOT NULL))
);
--> statement-breakpoint
CREATE UNIQUE INDEX `citation_reception_run_pair_idx` ON `citation_reception_runs` (`processor_version`,`citing_snapshot_id`,`reference_id`,`cited_snapshot_id`);--> statement-breakpoint
CREATE INDEX `citation_reception_run_target_idx` ON `citation_reception_runs` (`project_id`,`cited_snapshot_id`,`processor_version`,`created_at`);--> statement-breakpoint
CREATE INDEX `citation_reception_run_source_idx` ON `citation_reception_runs` (`project_id`,`citing_snapshot_id`,`processor_version`,`created_at`);--> statement-breakpoint
CREATE TABLE `citation_source_manifests` (
	`snapshot_id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`schema_version` text NOT NULL,
	`extractor_version` text NOT NULL,
	`source_sha256` text NOT NULL,
	`reference_count` integer NOT NULL,
	`mention_count` integer NOT NULL,
	`warnings_json` text NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	FOREIGN KEY (`snapshot_id`) REFERENCES `evidence_graph_snapshots`(`id`) ON UPDATE no action ON DELETE restrict,
	CONSTRAINT "citation_source_manifest_version_check" CHECK("citation_source_manifests"."schema_version" = 'citation-source/1.0'
    AND "citation_source_manifests"."extractor_version" = 'citation-source-extractor/1.0'),
	CONSTRAINT "citation_source_manifest_hash_check" CHECK(length("citation_source_manifests"."source_sha256") = 64
    AND "citation_source_manifests"."source_sha256" NOT GLOB '*[^0-9a-f]*'),
	CONSTRAINT "citation_source_manifest_counts_check" CHECK("citation_source_manifests"."reference_count" >= 0
    AND "citation_source_manifests"."mention_count" >= 0)
);
--> statement-breakpoint
CREATE UNIQUE INDEX `citation_source_manifest_hash_idx` ON `citation_source_manifests` (`project_id`,`source_sha256`);--> statement-breakpoint
CREATE INDEX `citation_source_manifest_project_idx` ON `citation_source_manifests` (`project_id`,`created_at`);--> statement-breakpoint
CREATE INDEX `openalex_paper_classification_work_idx` ON `openalex_paper_classifications` (`project_id`,`openalex_work_id`,`snapshot_id`);--> statement-breakpoint
CREATE INDEX `openalex_paper_classification_doi_idx` ON `openalex_paper_classifications` (`project_id`,`matched_doi`,`snapshot_id`);--> statement-breakpoint
CREATE TRIGGER `citation_source_manifests_delete_write_barrier`
BEFORE INSERT ON `citation_source_manifests`
WHEN EXISTS (
	SELECT 1 FROM `evidence_graph_snapshots` s
	JOIN `artifact_versions` v ON v.id = s.root_artifact_version_id
	JOIN `artifact_deletion_authorizations` d ON d.artifact_id = v.artifact_id
	WHERE s.id = NEW.snapshot_id
)
BEGIN SELECT RAISE(ABORT, 'artifact deletion is in progress'); END;--> statement-breakpoint
CREATE TRIGGER `citation_source_manifests_immutable_update`
BEFORE UPDATE ON `citation_source_manifests`
BEGIN SELECT RAISE(ABORT, 'citation source manifest is immutable'); END;--> statement-breakpoint
CREATE TRIGGER `citation_source_manifests_immutable_delete`
BEFORE DELETE ON `citation_source_manifests`
WHEN NOT EXISTS (
	SELECT 1 FROM `evidence_graph_snapshots` s
	JOIN `artifact_versions` v ON v.id = s.root_artifact_version_id
	JOIN `artifact_deletion_authorizations` d ON d.artifact_id = v.artifact_id
	WHERE s.id = OLD.snapshot_id
)
BEGIN SELECT RAISE(ABORT, 'citation source manifest is immutable'); END;--> statement-breakpoint
CREATE TRIGGER `bibliographic_references_delete_write_barrier`
BEFORE INSERT ON `bibliographic_references`
WHEN EXISTS (
	SELECT 1 FROM `evidence_graph_snapshots` s
	JOIN `artifact_versions` v ON v.id = s.root_artifact_version_id
	JOIN `artifact_deletion_authorizations` d ON d.artifact_id = v.artifact_id
	WHERE s.id = NEW.citing_snapshot_id
)
BEGIN SELECT RAISE(ABORT, 'artifact deletion is in progress'); END;--> statement-breakpoint
CREATE TRIGGER `bibliographic_references_immutable_update`
BEFORE UPDATE ON `bibliographic_references`
BEGIN SELECT RAISE(ABORT, 'bibliographic reference is immutable'); END;--> statement-breakpoint
CREATE TRIGGER `bibliographic_references_immutable_delete`
BEFORE DELETE ON `bibliographic_references`
WHEN NOT EXISTS (
	SELECT 1 FROM `evidence_graph_snapshots` s
	JOIN `artifact_versions` v ON v.id = s.root_artifact_version_id
	JOIN `artifact_deletion_authorizations` d ON d.artifact_id = v.artifact_id
	WHERE s.id = OLD.citing_snapshot_id
)
BEGIN SELECT RAISE(ABORT, 'bibliographic reference is immutable'); END;--> statement-breakpoint
CREATE TRIGGER `citation_mentions_delete_write_barrier`
BEFORE INSERT ON `citation_mentions`
WHEN EXISTS (
	SELECT 1 FROM `evidence_graph_snapshots` s
	JOIN `artifact_versions` v ON v.id = s.root_artifact_version_id
	JOIN `artifact_deletion_authorizations` d ON d.artifact_id = v.artifact_id
	WHERE s.id = NEW.citing_snapshot_id
)
BEGIN SELECT RAISE(ABORT, 'artifact deletion is in progress'); END;--> statement-breakpoint
CREATE TRIGGER `citation_mentions_immutable_update`
BEFORE UPDATE ON `citation_mentions`
BEGIN SELECT RAISE(ABORT, 'citation mention is immutable'); END;--> statement-breakpoint
CREATE TRIGGER `citation_mentions_immutable_delete`
BEFORE DELETE ON `citation_mentions`
WHEN NOT EXISTS (
	SELECT 1 FROM `evidence_graph_snapshots` s
	JOIN `artifact_versions` v ON v.id = s.root_artifact_version_id
	JOIN `artifact_deletion_authorizations` d ON d.artifact_id = v.artifact_id
	WHERE s.id = OLD.citing_snapshot_id
)
BEGIN SELECT RAISE(ABORT, 'citation mention is immutable'); END;--> statement-breakpoint
CREATE TRIGGER `citation_reception_runs_delete_write_barrier`
BEFORE INSERT ON `citation_reception_runs`
WHEN EXISTS (
	SELECT 1 FROM `evidence_graph_snapshots` s
	JOIN `artifact_versions` v ON v.id = s.root_artifact_version_id
	JOIN `artifact_deletion_authorizations` d ON d.artifact_id = v.artifact_id
	WHERE s.id IN (NEW.citing_snapshot_id, NEW.cited_snapshot_id)
)
BEGIN SELECT RAISE(ABORT, 'artifact deletion is in progress'); END;--> statement-breakpoint
CREATE TRIGGER `citation_reception_runs_immutable_update`
BEFORE UPDATE ON `citation_reception_runs`
BEGIN SELECT RAISE(ABORT, 'citation reception run is immutable'); END;--> statement-breakpoint
CREATE TRIGGER `citation_assertions_immutable_update`
BEFORE UPDATE ON `citation_assertions`
BEGIN SELECT RAISE(ABORT, 'citation assertion is immutable'); END;--> statement-breakpoint
CREATE TRIGGER `citation_assessments_immutable_update`
BEFORE UPDATE ON `citation_assessments`
BEGIN SELECT RAISE(ABORT, 'citation assessment is immutable'); END;
