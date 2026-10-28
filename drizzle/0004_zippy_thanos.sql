CREATE TABLE `evidence_assessment_index` (
	`project_id` text NOT NULL,
	`verification_id` text NOT NULL,
	`snapshot_id` text NOT NULL,
	`assessment_id` text NOT NULL,
	`claim_id` text NOT NULL,
	`verdict` text NOT NULL,
	`confidence` real NOT NULL,
	`evidence_count` integer NOT NULL,
	`created_at` text NOT NULL,
	PRIMARY KEY(`verification_id`, `assessment_id`),
	FOREIGN KEY (`verification_id`) REFERENCES `verification_runs`(`id`) ON UPDATE no action ON DELETE restrict,
	FOREIGN KEY (`snapshot_id`) REFERENCES `evidence_graph_snapshots`(`id`) ON UPDATE no action ON DELETE restrict,
	CONSTRAINT "evidence_assessment_verdict_check" CHECK("evidence_assessment_index"."verdict" IN ('supported', 'contradicted', 'qualified', 'uncertain', 'insufficient_evidence')),
	CONSTRAINT "evidence_assessment_confidence_check" CHECK("evidence_assessment_index"."confidence" >= 0 AND "evidence_assessment_index"."confidence" <= 1),
	CONSTRAINT "evidence_assessment_count_check" CHECK("evidence_assessment_index"."evidence_count" >= 0)
);
--> statement-breakpoint
CREATE INDEX `evidence_assessment_claim_idx` ON `evidence_assessment_index` (`project_id`,`snapshot_id`,`claim_id`,`created_at`);--> statement-breakpoint
CREATE TABLE `evidence_claim_index` (
	`project_id` text NOT NULL,
	`snapshot_id` text NOT NULL,
	`paper_id` text NOT NULL,
	`claim_id` text NOT NULL,
	`source_claim_id` text,
	`proposition_key` text NOT NULL,
	`proposition` text NOT NULL,
	`claim_type` text NOT NULL,
	`polarity` text NOT NULL,
	`modality` text NOT NULL,
	`scope_json` text NOT NULL,
	`document_length` integer NOT NULL,
	`path_json` text,
	`path_depth` integer,
	`path_quality` real NOT NULL,
	`index_version` text NOT NULL,
	PRIMARY KEY(`snapshot_id`, `claim_id`),
	FOREIGN KEY (`snapshot_id`) REFERENCES `evidence_graph_snapshots`(`id`) ON UPDATE no action ON DELETE restrict,
	CONSTRAINT "evidence_claim_document_length_check" CHECK("evidence_claim_index"."document_length" >= 1),
	CONSTRAINT "evidence_claim_path_quality_check" CHECK("evidence_claim_index"."path_quality" >= 0 AND "evidence_claim_index"."path_quality" <= 1)
);
--> statement-breakpoint
CREATE INDEX `evidence_claim_project_proposition_idx` ON `evidence_claim_index` (`project_id`,`proposition_key`);--> statement-breakpoint
CREATE INDEX `evidence_claim_project_paper_idx` ON `evidence_claim_index` (`project_id`,`paper_id`,`snapshot_id`);--> statement-breakpoint
CREATE INDEX `evidence_claim_project_claim_idx` ON `evidence_claim_index` (`project_id`,`claim_id`);--> statement-breakpoint
CREATE TABLE `evidence_claim_relations` (
	`project_id` text NOT NULL,
	`snapshot_id` text NOT NULL,
	`edge_id` text NOT NULL,
	`source_claim_id` text NOT NULL,
	`target_claim_id` text NOT NULL,
	`relation` text NOT NULL,
	`confidence` real,
	`scope_overlap` text NOT NULL,
	PRIMARY KEY(`snapshot_id`, `edge_id`),
	FOREIGN KEY (`snapshot_id`) REFERENCES `evidence_graph_snapshots`(`id`) ON UPDATE no action ON DELETE restrict
);
--> statement-breakpoint
CREATE INDEX `evidence_relation_source_idx` ON `evidence_claim_relations` (`project_id`,`snapshot_id`,`source_claim_id`);--> statement-breakpoint
CREATE INDEX `evidence_relation_target_idx` ON `evidence_claim_relations` (`project_id`,`snapshot_id`,`target_claim_id`);--> statement-breakpoint
CREATE TABLE `evidence_claim_terms` (
	`project_id` text NOT NULL,
	`snapshot_id` text NOT NULL,
	`claim_id` text NOT NULL,
	`term` text NOT NULL,
	`field` text NOT NULL,
	`frequency` integer NOT NULL,
	`field_weight` real NOT NULL,
	PRIMARY KEY(`snapshot_id`, `claim_id`, `term`, `field`),
	FOREIGN KEY (`snapshot_id`) REFERENCES `evidence_graph_snapshots`(`id`) ON UPDATE no action ON DELETE restrict,
	CONSTRAINT "evidence_term_frequency_check" CHECK("evidence_claim_terms"."frequency" >= 1),
	CONSTRAINT "evidence_term_weight_check" CHECK("evidence_claim_terms"."field_weight" > 0)
);
--> statement-breakpoint
CREATE INDEX `evidence_term_lookup_idx` ON `evidence_claim_terms` (`project_id`,`term`,`snapshot_id`,`claim_id`);--> statement-breakpoint
CREATE INDEX `evidence_term_claim_idx` ON `evidence_claim_terms` (`snapshot_id`,`claim_id`);--> statement-breakpoint
CREATE TABLE `evidence_search_manifests` (
	`snapshot_id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`index_version` text NOT NULL,
	`claim_count` integer NOT NULL,
	`term_count` integer NOT NULL,
	`relation_count` integer NOT NULL,
	`created_at` text NOT NULL,
	FOREIGN KEY (`snapshot_id`) REFERENCES `evidence_graph_snapshots`(`id`) ON UPDATE no action ON DELETE restrict,
	CONSTRAINT "evidence_search_manifest_count_check" CHECK("evidence_search_manifests"."claim_count" >= 0 AND "evidence_search_manifests"."term_count" >= 0 AND "evidence_search_manifests"."relation_count" >= 0)
);
--> statement-breakpoint
CREATE INDEX `evidence_search_manifest_project_idx` ON `evidence_search_manifests` (`project_id`,`index_version`);--> statement-breakpoint
CREATE TRIGGER `evidence_claim_index_immutable_update` BEFORE UPDATE ON `evidence_claim_index`
BEGIN SELECT RAISE(ABORT, 'evidence claim index is immutable'); END;--> statement-breakpoint
CREATE TRIGGER `evidence_claim_index_immutable_delete` BEFORE DELETE ON `evidence_claim_index`
BEGIN SELECT RAISE(ABORT, 'evidence claim index is immutable'); END;--> statement-breakpoint
CREATE TRIGGER `evidence_claim_terms_immutable_update` BEFORE UPDATE ON `evidence_claim_terms`
BEGIN SELECT RAISE(ABORT, 'evidence claim terms are immutable'); END;--> statement-breakpoint
CREATE TRIGGER `evidence_claim_terms_immutable_delete` BEFORE DELETE ON `evidence_claim_terms`
BEGIN SELECT RAISE(ABORT, 'evidence claim terms are immutable'); END;--> statement-breakpoint
CREATE TRIGGER `evidence_claim_relations_immutable_update` BEFORE UPDATE ON `evidence_claim_relations`
BEGIN SELECT RAISE(ABORT, 'evidence claim relations are immutable'); END;--> statement-breakpoint
CREATE TRIGGER `evidence_claim_relations_immutable_delete` BEFORE DELETE ON `evidence_claim_relations`
BEGIN SELECT RAISE(ABORT, 'evidence claim relations are immutable'); END;--> statement-breakpoint
CREATE TRIGGER `evidence_assessment_index_immutable_update` BEFORE UPDATE ON `evidence_assessment_index`
BEGIN SELECT RAISE(ABORT, 'evidence assessment index is immutable'); END;--> statement-breakpoint
CREATE TRIGGER `evidence_assessment_index_immutable_delete` BEFORE DELETE ON `evidence_assessment_index`
BEGIN SELECT RAISE(ABORT, 'evidence assessment index is immutable'); END;--> statement-breakpoint
CREATE TRIGGER `evidence_search_manifests_immutable_update` BEFORE UPDATE ON `evidence_search_manifests`
BEGIN SELECT RAISE(ABORT, 'evidence search manifest is immutable'); END;--> statement-breakpoint
CREATE TRIGGER `evidence_search_manifests_immutable_delete` BEFORE DELETE ON `evidence_search_manifests`
BEGIN SELECT RAISE(ABORT, 'evidence search manifest is immutable'); END;
