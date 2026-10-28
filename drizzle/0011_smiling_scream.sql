DROP TRIGGER IF EXISTS `corpus_claim_graph_edges_reject_cycles`;--> statement-breakpoint
CREATE TABLE `openalex_paper_classifications` (
	`snapshot_id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`source` text NOT NULL,
	`classifier` text NOT NULL,
	`openalex_work_id` text,
	`matched_doi` text,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	FOREIGN KEY (`snapshot_id`) REFERENCES `evidence_graph_snapshots`(`id`) ON UPDATE no action ON DELETE restrict,
	CONSTRAINT "openalex_paper_classification_source_check" CHECK("openalex_paper_classifications"."source" IN ('openalex_work', 'official_title_abstract_model')),
	CONSTRAINT "openalex_paper_classification_classifier_check" CHECK(length("openalex_paper_classifications"."classifier") BETWEEN 1 AND 300)
);
--> statement-breakpoint
CREATE INDEX `openalex_paper_classification_project_idx` ON `openalex_paper_classifications` (`project_id`,`created_at`);--> statement-breakpoint
CREATE TABLE `openalex_paper_topics` (
	`snapshot_id` text NOT NULL,
	`topic_id` text NOT NULL,
	`project_id` text NOT NULL,
	`display_name` text NOT NULL,
	`score` real NOT NULL,
	`is_primary` integer NOT NULL,
	`ordinal` integer NOT NULL,
	`subfield_id` text NOT NULL,
	`subfield_name` text NOT NULL,
	`field_id` text NOT NULL,
	`field_name` text NOT NULL,
	`domain_id` text NOT NULL,
	`domain_name` text NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY(`snapshot_id`, `topic_id`),
	FOREIGN KEY (`snapshot_id`) REFERENCES `openalex_paper_classifications`(`snapshot_id`) ON UPDATE no action ON DELETE restrict,
	CONSTRAINT "openalex_paper_topic_id_check" CHECK("openalex_paper_topics"."topic_id" GLOB 'https://openalex.org/T[0-9]*'),
	CONSTRAINT "openalex_paper_topic_score_check" CHECK("openalex_paper_topics"."score" >= 0 AND "openalex_paper_topics"."score" <= 1),
	CONSTRAINT "openalex_paper_topic_ordinal_check" CHECK("openalex_paper_topics"."ordinal" BETWEEN 0 AND 2)
);
--> statement-breakpoint
CREATE UNIQUE INDEX `openalex_paper_topic_ordinal_idx` ON `openalex_paper_topics` (`snapshot_id`,`ordinal`);--> statement-breakpoint
CREATE INDEX `openalex_paper_topic_domain_idx` ON `openalex_paper_topics` (`project_id`,`domain_id`,`snapshot_id`);--> statement-breakpoint
CREATE INDEX `openalex_paper_topic_field_idx` ON `openalex_paper_topics` (`project_id`,`field_id`,`snapshot_id`);--> statement-breakpoint
CREATE INDEX `openalex_paper_topic_subfield_idx` ON `openalex_paper_topics` (`project_id`,`subfield_id`,`snapshot_id`);--> statement-breakpoint
CREATE INDEX `openalex_paper_topic_topic_idx` ON `openalex_paper_topics` (`project_id`,`topic_id`,`snapshot_id`);--> statement-breakpoint
PRAGMA foreign_keys=OFF;--> statement-breakpoint
CREATE TABLE `__new_corpus_claim_graph_completions` (
	`project_id` text NOT NULL,
	`snapshot_id` text NOT NULL,
	`claim_id` text NOT NULL,
	`processor_version` text NOT NULL,
	`candidate_count` integer NOT NULL,
	`edge_count` integer NOT NULL,
	`classifier_model` text NOT NULL,
	`completed_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY(`snapshot_id`, `claim_id`, `processor_version`),
	FOREIGN KEY (`snapshot_id`,`claim_id`) REFERENCES `evidence_retrieval_documents`(`snapshot_id`,`claim_id`) ON UPDATE no action ON DELETE restrict,
	CONSTRAINT "corpus_claim_graph_completion_counts_check" CHECK("candidate_count" >= 0
    AND "edge_count" >= 0),
	CONSTRAINT "corpus_claim_graph_completion_processor_check" CHECK(length("processor_version") BETWEEN 1 AND 100),
	CONSTRAINT "corpus_claim_graph_completion_model_check" CHECK(length("classifier_model") BETWEEN 1 AND 200)
);
--> statement-breakpoint
INSERT INTO `__new_corpus_claim_graph_completions`("project_id", "snapshot_id", "claim_id", "processor_version", "candidate_count", "edge_count", "classifier_model", "completed_at") SELECT "project_id", "snapshot_id", "claim_id", "processor_version", "candidate_count", "admitted_edge_count", "classifier_model", "completed_at" FROM `corpus_claim_graph_completions`;--> statement-breakpoint
DROP TABLE `corpus_claim_graph_completions`;--> statement-breakpoint
ALTER TABLE `__new_corpus_claim_graph_completions` RENAME TO `corpus_claim_graph_completions`;--> statement-breakpoint
PRAGMA foreign_keys=ON;--> statement-breakpoint
CREATE INDEX `corpus_claim_graph_completion_project_idx` ON `corpus_claim_graph_completions` (`project_id`,`processor_version`,`completed_at`);
