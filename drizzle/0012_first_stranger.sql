PRAGMA foreign_keys=OFF;--> statement-breakpoint
CREATE TABLE `__new_corpus_claim_graph_edges` (
	`id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`source_snapshot_id` text NOT NULL,
	`source_claim_id` text NOT NULL,
	`target_snapshot_id` text NOT NULL,
	`target_claim_id` text NOT NULL,
	`relation` text NOT NULL,
	`model_relation` text NOT NULL,
	`confidence` real,
	`processor_version` text NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	FOREIGN KEY (`source_snapshot_id`,`source_claim_id`) REFERENCES `evidence_retrieval_documents`(`snapshot_id`,`claim_id`) ON UPDATE no action ON DELETE restrict,
	FOREIGN KEY (`target_snapshot_id`,`target_claim_id`) REFERENCES `evidence_retrieval_documents`(`snapshot_id`,`claim_id`) ON UPDATE no action ON DELETE restrict,
	CONSTRAINT "corpus_claim_graph_edge_id_check" CHECK(length("id") = 82
    AND "id" GLOB 'claim-edge:sha256:*'
    AND substr("id", 19) NOT GLOB '*[^0-9a-f]*'),
	CONSTRAINT "corpus_claim_graph_edge_distinct_check" CHECK("source_snapshot_id" <> "target_snapshot_id"
    OR "source_claim_id" <> "target_claim_id"),
	CONSTRAINT "corpus_claim_graph_edge_relation_check" CHECK(("relation" = 'directly_implies'
      AND "model_relation" IN ('supports', 'refines', 'qualifies'))
    OR ("relation" = "model_relation"
      AND "model_relation" IN ('equivalent_to', 'contradicts', 'supplements'))),
	CONSTRAINT "corpus_claim_graph_edge_confidence_check" CHECK("confidence" IS NULL
    OR ("confidence" >= 0 AND "confidence" <= 1)),
	CONSTRAINT "corpus_claim_graph_edge_processor_check" CHECK(length("processor_version") BETWEEN 1 AND 100)
);
--> statement-breakpoint
INSERT INTO `__new_corpus_claim_graph_edges`("id", "project_id", "source_snapshot_id", "source_claim_id", "target_snapshot_id", "target_claim_id", "relation", "model_relation", "confidence", "processor_version", "created_at") SELECT "id", "project_id", "source_snapshot_id", "source_claim_id", "target_snapshot_id", "target_claim_id", "relation", "model_relation", "confidence", "processor_version", "created_at" FROM `corpus_claim_graph_edges`;--> statement-breakpoint
DROP TABLE `corpus_claim_graph_edges`;--> statement-breakpoint
ALTER TABLE `__new_corpus_claim_graph_edges` RENAME TO `corpus_claim_graph_edges`;--> statement-breakpoint
PRAGMA foreign_keys=ON;--> statement-breakpoint
CREATE UNIQUE INDEX `corpus_claim_graph_edge_endpoints_idx` ON `corpus_claim_graph_edges` (`processor_version`,`source_snapshot_id`,`source_claim_id`,`target_snapshot_id`,`target_claim_id`);--> statement-breakpoint
CREATE INDEX `corpus_claim_graph_edge_project_idx` ON `corpus_claim_graph_edges` (`project_id`,`processor_version`,`created_at`);--> statement-breakpoint
CREATE INDEX `corpus_claim_graph_edge_source_idx` ON `corpus_claim_graph_edges` (`source_snapshot_id`,`source_claim_id`);--> statement-breakpoint
CREATE INDEX `corpus_claim_graph_edge_target_idx` ON `corpus_claim_graph_edges` (`target_snapshot_id`,`target_claim_id`);
