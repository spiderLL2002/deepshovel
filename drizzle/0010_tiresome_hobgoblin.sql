CREATE TABLE `corpus_claim_graph_completions` (
	`project_id` text NOT NULL,
	`snapshot_id` text NOT NULL,
	`claim_id` text NOT NULL,
	`processor_version` text NOT NULL,
	`candidate_count` integer NOT NULL,
	`admitted_edge_count` integer NOT NULL,
	`skipped_cycle_count` integer NOT NULL,
	`classifier_model` text NOT NULL,
	`completed_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY(`snapshot_id`, `claim_id`, `processor_version`),
	FOREIGN KEY (`snapshot_id`,`claim_id`) REFERENCES `evidence_retrieval_documents`(`snapshot_id`,`claim_id`) ON UPDATE no action ON DELETE restrict,
	CONSTRAINT "corpus_claim_graph_completion_counts_check" CHECK("corpus_claim_graph_completions"."candidate_count" >= 0
    AND "corpus_claim_graph_completions"."admitted_edge_count" >= 0 AND "corpus_claim_graph_completions"."skipped_cycle_count" >= 0),
	CONSTRAINT "corpus_claim_graph_completion_processor_check" CHECK(length("corpus_claim_graph_completions"."processor_version") BETWEEN 1 AND 100),
	CONSTRAINT "corpus_claim_graph_completion_model_check" CHECK(length("corpus_claim_graph_completions"."classifier_model") BETWEEN 1 AND 200)
);
--> statement-breakpoint
CREATE INDEX `corpus_claim_graph_completion_project_idx` ON `corpus_claim_graph_completions` (`project_id`,`processor_version`,`completed_at`);--> statement-breakpoint
CREATE TABLE `corpus_claim_graph_edges` (
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
	CONSTRAINT "corpus_claim_graph_edge_id_check" CHECK(length("corpus_claim_graph_edges"."id") = 82
    AND "corpus_claim_graph_edges"."id" GLOB 'claim-edge:sha256:*'
    AND substr("corpus_claim_graph_edges"."id", 19) NOT GLOB '*[^0-9a-f]*'),
	CONSTRAINT "corpus_claim_graph_edge_distinct_check" CHECK("corpus_claim_graph_edges"."source_snapshot_id" <> "corpus_claim_graph_edges"."target_snapshot_id"
    OR "corpus_claim_graph_edges"."source_claim_id" <> "corpus_claim_graph_edges"."target_claim_id"),
	CONSTRAINT "corpus_claim_graph_edge_relation_check" CHECK("corpus_claim_graph_edges"."relation" = 'directly_implies'),
	CONSTRAINT "corpus_claim_graph_edge_model_relation_check" CHECK("corpus_claim_graph_edges"."model_relation" IN ('supports', 'refines', 'qualifies')),
	CONSTRAINT "corpus_claim_graph_edge_confidence_check" CHECK("corpus_claim_graph_edges"."confidence" IS NULL
    OR ("corpus_claim_graph_edges"."confidence" >= 0 AND "corpus_claim_graph_edges"."confidence" <= 1)),
	CONSTRAINT "corpus_claim_graph_edge_processor_check" CHECK(length("corpus_claim_graph_edges"."processor_version") BETWEEN 1 AND 100)
);
--> statement-breakpoint
CREATE UNIQUE INDEX `corpus_claim_graph_edge_endpoints_idx` ON `corpus_claim_graph_edges` (`processor_version`,`source_snapshot_id`,`source_claim_id`,`target_snapshot_id`,`target_claim_id`);--> statement-breakpoint
CREATE INDEX `corpus_claim_graph_edge_project_idx` ON `corpus_claim_graph_edges` (`project_id`,`processor_version`,`created_at`);--> statement-breakpoint
CREATE INDEX `corpus_claim_graph_edge_source_idx` ON `corpus_claim_graph_edges` (`source_snapshot_id`,`source_claim_id`);--> statement-breakpoint
CREATE INDEX `corpus_claim_graph_edge_target_idx` ON `corpus_claim_graph_edges` (`target_snapshot_id`,`target_claim_id`);--> statement-breakpoint
CREATE TRIGGER `corpus_claim_graph_edges_reject_cycles`
BEFORE INSERT ON `corpus_claim_graph_edges`
BEGIN
  SELECT CASE WHEN EXISTS (
    WITH RECURSIVE reachable(snapshot_id, claim_id) AS (
      SELECT NEW.target_snapshot_id, NEW.target_claim_id
      UNION
      SELECT edge.target_snapshot_id, edge.target_claim_id
      FROM corpus_claim_graph_edges edge
      JOIN reachable node
        ON edge.source_snapshot_id = node.snapshot_id
       AND edge.source_claim_id = node.claim_id
      WHERE edge.project_id = NEW.project_id
        AND edge.processor_version = NEW.processor_version
    )
    SELECT 1 FROM reachable
    WHERE snapshot_id = NEW.source_snapshot_id
      AND claim_id = NEW.source_claim_id
  ) THEN RAISE(ABORT, 'corpus claim graph cycle') END;
END;
