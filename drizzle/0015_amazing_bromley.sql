PRAGMA foreign_keys=OFF;--> statement-breakpoint
CREATE TABLE `__new_citation_assertions` (
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
	CONSTRAINT "citation_assertion_id_check" CHECK(length("id") = 90
    AND "id" GLOB 'citation-assertion:sha256:*'
    AND substr("id", 27) NOT GLOB '*[^0-9a-f]*'),
	CONSTRAINT "citation_assertion_function_check" CHECK("citation_function" IN ('result_comparison', 'replication', 'critique')),
	CONSTRAINT "citation_assertion_stance_check" CHECK("stance" IN ('supports', 'contradicts', 'qualifies', 'unclear')),
	CONSTRAINT "citation_assertion_basis_check" CHECK("confirmation_basis" IN ('mere_attribution', 'verbal_endorsement',
    'verbal_critique', 'consistent_with_citing_evidence', 'contradicted_by_citing_evidence',
    'qualified_by_citing_evidence', 'direct_replication', 'reanalysis', 'meta_analytic_synthesis')),
	CONSTRAINT "citation_assertion_target_check" CHECK(("target_entity_kind" IS NULL AND "target_entity_id" IS NULL)
    OR ("target_entity_kind" IN ('source_anchor', 'structured_visual_evidence', 'observation', 'reasoning', 'atomic_claim')
      AND "target_entity_id" IS NOT NULL)),
	CONSTRAINT "citation_assertion_confidence_check" CHECK("confidence" >= 0 AND "confidence" <= 1)
);
--> statement-breakpoint
INSERT INTO `__new_citation_assertions`("id", "run_id", "project_id", "mention_id", "attributed_proposition", "citation_function", "stance", "confirmation_basis", "target_entity_kind", "target_entity_id", "citing_evidence_entity_ids_json", "confidence", "created_at") SELECT "id", "run_id", "project_id", "mention_id", "attributed_proposition", "citation_function", "stance", "confirmation_basis", "target_entity_kind", "target_entity_id", "citing_evidence_entity_ids_json", "confidence", "created_at" FROM `citation_assertions`;--> statement-breakpoint
DROP TABLE `citation_assertions`;--> statement-breakpoint
ALTER TABLE `__new_citation_assertions` RENAME TO `citation_assertions`;--> statement-breakpoint
CREATE INDEX `citation_assertion_run_idx` ON `citation_assertions` (`run_id`,`created_at`);--> statement-breakpoint
CREATE INDEX `citation_assertion_target_idx` ON `citation_assertions` (`target_entity_kind`,`target_entity_id`);--> statement-breakpoint
CREATE TABLE `__new_citation_assessments` (
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
	CONSTRAINT "citation_assessment_id_check" CHECK(length("id") = 91
    AND "id" GLOB 'citation-assessment:sha256:*'
    AND substr("id", 28) NOT GLOB '*[^0-9a-f]*'),
	CONSTRAINT "citation_assessment_faithfulness_check" CHECK("faithfulness" IN ('faithful', 'partial', 'overgeneralized',
    'contradicted_by_target', 'not_found', 'ambiguous', 'indirect')),
	CONSTRAINT "citation_assessment_basis_check" CHECK("confirmation_basis" IN ('mere_attribution', 'verbal_endorsement',
    'verbal_critique', 'consistent_with_citing_evidence', 'contradicted_by_citing_evidence',
    'qualified_by_citing_evidence', 'direct_replication', 'reanalysis', 'meta_analytic_synthesis')),
	CONSTRAINT "citation_assessment_independence_check" CHECK("independence" IN ('independent', 'shared_authors', 'shared_data', 'derived_data', 'unknown')),
	CONSTRAINT "citation_assessment_confidence_check" CHECK("confidence" >= 0 AND "confidence" <= 1),
	CONSTRAINT "citation_assessment_hash_check" CHECK(length("extraction_sha256") = 64
    AND "extraction_sha256" NOT GLOB '*[^0-9a-f]*')
);
--> statement-breakpoint
INSERT INTO `__new_citation_assessments`("id", "assertion_id", "project_id", "faithfulness", "confirmation_basis", "independence", "confidence", "evidence_ids_json", "limitations_json", "extraction_sha256", "verifier_model", "verifier_prompt_version", "created_at") SELECT "id", "assertion_id", "project_id", "faithfulness", "confirmation_basis", "independence", "confidence", "evidence_ids_json", "limitations_json", "extraction_sha256", "verifier_model", "verifier_prompt_version", "created_at" FROM `citation_assessments`;--> statement-breakpoint
DROP TABLE `citation_assessments`;--> statement-breakpoint
ALTER TABLE `__new_citation_assessments` RENAME TO `citation_assessments`;--> statement-breakpoint
CREATE UNIQUE INDEX `citation_assessments_assertion_id_unique` ON `citation_assessments` (`assertion_id`);--> statement-breakpoint
CREATE INDEX `citation_assessment_quality_idx` ON `citation_assessments` (`faithfulness`,`confirmation_basis`,`independence`);--> statement-breakpoint
CREATE TABLE `__new_citation_reception_runs` (
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
	CONSTRAINT "citation_reception_run_id_check" CHECK(length("id") = 84
    AND "id" GLOB 'citation-run:sha256:*'
    AND substr("id", 21) NOT GLOB '*[^0-9a-f]*'),
	CONSTRAINT "citation_reception_run_processor_check" CHECK("processor_version" = 'citation-reception-processor/1.0.0'),
	CONSTRAINT "citation_reception_run_resolution_check" CHECK("resolution_method" IN ('openalex_work_id', 'doi', 'title')
    AND "resolution_confidence" >= 0 AND "resolution_confidence" <= 1),
	CONSTRAINT "citation_reception_run_hash_check" CHECK(length("assertions_sha256") = 64
    AND "assertions_sha256" NOT GLOB '*[^0-9a-f]*'),
	CONSTRAINT "citation_reception_run_count_check" CHECK("assertion_count" >= 0),
	CONSTRAINT "citation_reception_run_verifier_check" CHECK(("assertion_count" = 0
      AND "verification_run_id" IS NULL AND "verifier_model" IS NULL AND "verifier_prompt_version" IS NULL)
    OR ("assertion_count" > 0
      AND "verification_run_id" IS NOT NULL AND "verifier_model" IS NOT NULL AND "verifier_prompt_version" IS NOT NULL))
);
--> statement-breakpoint
INSERT INTO `__new_citation_reception_runs`("id", "project_id", "citing_snapshot_id", "reference_id", "cited_snapshot_id", "processor_version", "resolution_method", "resolution_confidence", "extraction_run_id", "extraction_model", "extraction_prompt_version", "assertions_sha256", "verification_run_id", "verifier_model", "verifier_prompt_version", "assertion_count", "created_at") SELECT "id", "project_id", "citing_snapshot_id", "reference_id", "cited_snapshot_id", "processor_version", "resolution_method", "resolution_confidence", "extraction_run_id", "extraction_model", "extraction_prompt_version", "assertions_sha256", "verification_run_id", "verifier_model", "verifier_prompt_version", "assertion_count", "created_at" FROM `citation_reception_runs`;--> statement-breakpoint
DROP TABLE `citation_reception_runs`;--> statement-breakpoint
ALTER TABLE `__new_citation_reception_runs` RENAME TO `citation_reception_runs`;--> statement-breakpoint
CREATE UNIQUE INDEX `citation_reception_run_pair_idx` ON `citation_reception_runs` (`processor_version`,`citing_snapshot_id`,`reference_id`,`cited_snapshot_id`);--> statement-breakpoint
CREATE INDEX `citation_reception_run_target_idx` ON `citation_reception_runs` (`project_id`,`cited_snapshot_id`,`processor_version`,`created_at`);--> statement-breakpoint
CREATE INDEX `citation_reception_run_source_idx` ON `citation_reception_runs` (`project_id`,`citing_snapshot_id`,`processor_version`,`created_at`);--> statement-breakpoint
PRAGMA foreign_keys=ON;--> statement-breakpoint
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
