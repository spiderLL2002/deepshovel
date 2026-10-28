CREATE TABLE `evidence_retrieval_assessments` (
	`project_id` text NOT NULL,
	`verification_id` text NOT NULL,
	`snapshot_id` text NOT NULL,
	`assessment_id` text NOT NULL,
	`claim_id` text NOT NULL,
	`assessment_json` text NOT NULL,
	`index_version` text NOT NULL,
	`created_at` text NOT NULL,
	PRIMARY KEY(`verification_id`, `assessment_id`),
	FOREIGN KEY (`verification_id`) REFERENCES `verification_runs`(`id`) ON UPDATE no action ON DELETE restrict,
	FOREIGN KEY (`snapshot_id`) REFERENCES `evidence_graph_snapshots`(`id`) ON UPDATE no action ON DELETE restrict
);
--> statement-breakpoint
CREATE INDEX `evidence_retrieval_assessment_claim_idx` ON `evidence_retrieval_assessments` (`project_id`,`snapshot_id`,`claim_id`,`created_at`);--> statement-breakpoint
CREATE TABLE `evidence_retrieval_documents` (
	`project_id` text NOT NULL,
	`snapshot_id` text NOT NULL,
	`paper_id` text NOT NULL,
	`paper_family_id` text NOT NULL,
	`claim_id` text NOT NULL,
	`scoped_claim_id` text NOT NULL,
	`proposition_key` text NOT NULL,
	`document_json` text NOT NULL,
	`index_version` text NOT NULL,
	`created_at` text NOT NULL,
	PRIMARY KEY(`snapshot_id`, `claim_id`),
	FOREIGN KEY (`snapshot_id`) REFERENCES `evidence_graph_snapshots`(`id`) ON UPDATE no action ON DELETE restrict,
	FOREIGN KEY (`paper_family_id`) REFERENCES `artifacts`(`id`) ON UPDATE no action ON DELETE restrict
);
--> statement-breakpoint
CREATE INDEX `evidence_retrieval_document_project_claim_idx` ON `evidence_retrieval_documents` (`project_id`,`claim_id`);--> statement-breakpoint
CREATE INDEX `evidence_retrieval_document_project_paper_idx` ON `evidence_retrieval_documents` (`project_id`,`paper_id`,`snapshot_id`);--> statement-breakpoint
CREATE INDEX `evidence_retrieval_document_project_family_idx` ON `evidence_retrieval_documents` (`project_id`,`paper_family_id`,`snapshot_id`);--> statement-breakpoint
CREATE INDEX `evidence_retrieval_document_proposition_idx` ON `evidence_retrieval_documents` (`project_id`,`proposition_key`);--> statement-breakpoint
CREATE TABLE `evidence_retrieval_manifests` (
	`snapshot_id` text PRIMARY KEY NOT NULL,
	`project_id` text NOT NULL,
	`paper_id` text NOT NULL,
	`paper_family_id` text NOT NULL,
	`root_artifact_version_id` text NOT NULL,
	`root_artifact_sha256` text NOT NULL,
	`graph_sha256` text NOT NULL,
	`graph_schema_version` text NOT NULL,
	`extractor_prompt_version` text NOT NULL,
	`index_version` text NOT NULL,
	`document_count` integer NOT NULL,
	`manifest_json` text NOT NULL,
	`created_at` text NOT NULL,
	FOREIGN KEY (`snapshot_id`) REFERENCES `evidence_graph_snapshots`(`id`) ON UPDATE no action ON DELETE restrict,
	FOREIGN KEY (`paper_family_id`) REFERENCES `artifacts`(`id`) ON UPDATE no action ON DELETE restrict,
	FOREIGN KEY (`root_artifact_version_id`) REFERENCES `artifact_versions`(`id`) ON UPDATE no action ON DELETE restrict,
	CONSTRAINT "evidence_retrieval_manifest_count_check" CHECK("evidence_retrieval_manifests"."document_count" >= 0),
	CONSTRAINT "evidence_retrieval_manifest_root_sha256_check" CHECK(length("evidence_retrieval_manifests"."root_artifact_sha256") = 64 AND "evidence_retrieval_manifests"."root_artifact_sha256" NOT GLOB '*[^0-9a-f]*'),
	CONSTRAINT "evidence_retrieval_manifest_graph_sha256_check" CHECK(length("evidence_retrieval_manifests"."graph_sha256") = 64 AND "evidence_retrieval_manifests"."graph_sha256" NOT GLOB '*[^0-9a-f]*')
);
--> statement-breakpoint
CREATE INDEX `evidence_retrieval_manifest_active_family_idx` ON `evidence_retrieval_manifests` (`project_id`,`index_version`,`paper_family_id`,`created_at`);--> statement-breakpoint
CREATE INDEX `evidence_retrieval_manifest_active_paper_idx` ON `evidence_retrieval_manifests` (`project_id`,`index_version`,`paper_id`,`created_at`);--> statement-breakpoint
-- Backfill every deployed v1 Claim before removing the old index. graph_json is
-- used only inside this one-time migration to recover fields absent from v1;
-- the v2 search/synchronization hot path reads document_json exclusively.
INSERT INTO `evidence_retrieval_documents` (
	`project_id`, `snapshot_id`, `paper_id`, `paper_family_id`, `claim_id`,
	`scoped_claim_id`, `proposition_key`, `document_json`, `index_version`, `created_at`
)
SELECT c.project_id, c.snapshot_id, c.paper_id, v.artifact_id, c.claim_id,
	c.claim_id, c.proposition_key,
	json_object(
		'index_version', 'evidence-retrieval/2.0',
		'snapshot_id', c.snapshot_id,
		'paper_id', c.paper_id,
		'paper_family_id', v.artifact_id,
		'root_artifact_version_id', s.root_artifact_version_id,
		'claim_id', c.claim_id,
		'source_claim_id', c.source_claim_id,
		'original_claim', (
			SELECT json_extract(anchor.value, '$.quote')
			FROM json_each(s.graph_json, '$.anchors') anchor
			WHERE json_extract(anchor.value, '$.id') IN (
				SELECT direct_anchor.value
				FROM json_each(s.graph_json, '$.claims') graph_claim,
					json_each(graph_claim.value, '$.anchor_ids') direct_anchor
				WHERE json_extract(graph_claim.value, '$.id') = c.claim_id
			)
			AND json_extract(anchor.value, '$.quote') IS NOT NULL
			ORDER BY json_extract(anchor.value, '$.id') LIMIT 1
		),
		'canonical_claim', c.proposition,
		'proposition_key', c.proposition_key,
		'scoped_claim_id', c.claim_id,
		'paper_title', COALESCE((
			SELECT json_extract(artifact.value, '$.canonical_name')
			FROM json_each(s.graph_json, '$.artifacts') artifact
			WHERE json_extract(artifact.value, '$.id') = v.artifact_id LIMIT 1
		), paper.preferred_name),
		'evidence_context', substr(COALESCE((
			SELECT group_concat(json_extract(node.value, '$.label'), char(10))
			FROM json_each(c.path_json, '$.nodes') node
			WHERE json_extract(node.value, '$.kind') IN ('source_anchor', 'observation', 'reasoning')
		), ''), 1, 16000),
		'entities', json(COALESCE(json_extract(c.scope_json, '$.entities'), '[]')),
		'key_terms', json(COALESCE((
			SELECT json_group_array(term) FROM (
				SELECT term FROM evidence_claim_terms terms
				WHERE terms.snapshot_id = c.snapshot_id AND terms.claim_id = c.claim_id
				AND terms.field <> 'proposition'
				ORDER BY terms.field_weight DESC, terms.term LIMIT 256
			)
		), '[]')),
		'abbreviations', json('[]'),
		'qualifiers', json(COALESCE((
			SELECT json_extract(claim.value, '$.qualifiers')
			FROM json_each(s.graph_json, '$.claims') claim
			WHERE json_extract(claim.value, '$.id') = c.claim_id LIMIT 1
		), '[]')),
		'claim_type', c.claim_type,
		'polarity', c.polarity,
		'modality', c.modality,
		'attribution', 'authors_conclusion',
		'rhetorical_role', 'terminal_conclusion',
		'scope', json(c.scope_json),
		'evidence_anchors', json(COALESCE((
			SELECT json_group_array(json_object(
				'anchor_id', json_extract(anchor.value, '$.id'),
				'artifact_version_id', json_extract(anchor.value, '$.artifact_version_id'),
				'anchor_type', json_extract(anchor.value, '$.anchor_type'),
				'page', json_extract(anchor.value, '$.selector.page'),
				'section', json_extract(anchor.value, '$.selector.section'),
				'paragraph', json_extract(anchor.value, '$.selector.paragraph'),
				'quote', json_extract(anchor.value, '$.quote'),
				'figure', json_extract(anchor.value, '$.selector.figure'),
				'table', json_extract(anchor.value, '$.selector.table'),
				'row_start', json_extract(anchor.value, '$.selector.row_start'),
				'row_end', json_extract(anchor.value, '$.selector.row_end'),
				'row_range_label', json_extract(anchor.value, '$.selector.row_range_label'),
				'column_names', json(COALESCE(json_extract(anchor.value, '$.selector.column_names'), '[]')),
				'series', json_extract(anchor.value, '$.selector.series'),
				'char_start', json_extract(anchor.value, '$.selector.char_start'),
				'char_end', json_extract(anchor.value, '$.selector.char_end'),
				'region', json_extract(anchor.value, '$.selector.region'),
				'region_label', json_extract(anchor.value, '$.selector.region_label'),
				'dataset_path', json_extract(anchor.value, '$.selector.dataset_path')
			))
			FROM json_each(s.graph_json, '$.anchors') anchor
			WHERE json_extract(anchor.value, '$.id') IN (
				SELECT json_extract(path_node.value, '$.id')
				FROM json_each(c.path_json, '$.nodes') path_node
				WHERE json_extract(path_node.value, '$.kind') = 'source_anchor'
			)
		), '[]')),
		'explicit_relations', json(COALESCE((
			SELECT json_group_array(json_object(
				'edge_id', relation.edge_id,
				'source_claim_id', relation.source_claim_id,
				'target_claim_id', relation.target_claim_id,
				'other_claim_id', CASE WHEN relation.source_claim_id = c.claim_id
					THEN relation.target_claim_id ELSE relation.source_claim_id END,
				'relation', relation.relation,
				'direction', CASE WHEN relation.source_claim_id = c.claim_id
					THEN 'outgoing' ELSE 'incoming' END,
				'confidence', relation.confidence,
				'scope_overlap', relation.scope_overlap
			)) FROM evidence_claim_relations relation
			WHERE relation.snapshot_id = c.snapshot_id
			AND (relation.source_claim_id = c.claim_id OR relation.target_claim_id = c.claim_id)
		), '[]')),
		'shortest_evidence_path', json(COALESCE(c.path_json, json_object(
			'root_claim_id', c.claim_id, 'nodes', json('[]'), 'links', json('[]'),
			'depth', 0, 'mean_edge_confidence', NULL
		))),
		'path_quality', c.path_quality,
		'extractor_prompt_version', s.extractor_prompt_version
	),
	'evidence-retrieval/2.0', m.created_at
FROM evidence_claim_index c
JOIN evidence_search_manifests m ON m.snapshot_id = c.snapshot_id
JOIN evidence_graph_snapshots s ON s.id = c.snapshot_id
JOIN artifact_versions v ON v.id = s.root_artifact_version_id
JOIN artifacts paper ON paper.id = v.artifact_id;--> statement-breakpoint
-- Preserve independent verifier details from the immutable report JSON.
INSERT INTO `evidence_retrieval_assessments` (
	`project_id`, `verification_id`, `snapshot_id`, `assessment_id`, `claim_id`,
	`assessment_json`, `index_version`, `created_at`
)
SELECT old.project_id, old.verification_id, old.snapshot_id, old.assessment_id, old.claim_id,
	json_object(
		'index_version', 'evidence-retrieval/2.0',
		'verification_id', old.verification_id,
		'snapshot_id', old.snapshot_id,
		'assessment_id', old.assessment_id,
		'claim_id', old.claim_id,
		'scoped_claim_id', COALESCE(json_extract(raw.value, '$.scoped_claim_id'), old.claim_id),
		'verdict', old.verdict,
		'confidence', old.confidence,
		'evidence_ids', json(COALESCE(json_extract(raw.value, '$.evidence_ids'), '[]')),
		'rationale', COALESCE(json_extract(raw.value, '$.rationale'), 'Legacy verifier assessment migrated without a stored rationale.'),
		'limitations', json(COALESCE(json_extract(raw.value, '$.limitations'), '[]')),
		'verifier_model', run.verifier_model,
		'verifier_prompt_version', run.verifier_prompt_version,
		'created_at', old.created_at
	),
	'evidence-retrieval/2.0', old.created_at
FROM evidence_assessment_index old
JOIN verification_runs run ON run.id = old.verification_id
LEFT JOIN json_each(run.report_json, '$.assessments') raw
	ON json_extract(raw.value, '$.id') = old.assessment_id;--> statement-breakpoint
-- Backfill manifests last. This includes v1 zero-Claim manifests and therefore
-- preserves the rule that the newest empty extraction suppresses older Claims.
INSERT INTO `evidence_retrieval_manifests` (
	`snapshot_id`, `project_id`, `paper_id`, `paper_family_id`,
	`root_artifact_version_id`, `root_artifact_sha256`, `graph_sha256`,
	`graph_schema_version`, `extractor_prompt_version`, `index_version`,
	`document_count`, `manifest_json`, `created_at`
)
SELECT m.snapshot_id, m.project_id, json_extract(s.graph_json, '$.paper_id'),
	v.artifact_id, s.root_artifact_version_id, v.sha256, s.graph_sha256,
	s.schema_version, s.extractor_prompt_version, 'evidence-retrieval/2.0',
	(SELECT COUNT(*) FROM evidence_retrieval_documents d WHERE d.snapshot_id = m.snapshot_id),
	json_object(
		'index_version', 'evidence-retrieval/2.0',
		'snapshot_id', m.snapshot_id,
		'paper_id', json_extract(s.graph_json, '$.paper_id'),
		'paper_family_id', v.artifact_id,
		'root_artifact_version_id', s.root_artifact_version_id,
		'root_artifact_sha256', v.sha256,
		'graph_sha256', s.graph_sha256,
		'graph_schema_version', s.schema_version,
		'extractor_prompt_version', s.extractor_prompt_version,
		'document_count', (SELECT COUNT(*) FROM evidence_retrieval_documents d WHERE d.snapshot_id = m.snapshot_id),
		'created_at', m.created_at
	),
	m.created_at
FROM evidence_search_manifests m
JOIN evidence_graph_snapshots s ON s.id = m.snapshot_id
JOIN artifact_versions v ON v.id = s.root_artifact_version_id;--> statement-breakpoint
DROP TABLE `evidence_assessment_index`;--> statement-breakpoint
DROP TABLE `evidence_claim_terms`;--> statement-breakpoint
DROP TABLE `evidence_claim_relations`;--> statement-breakpoint
DROP TABLE `evidence_claim_index`;--> statement-breakpoint
DROP TABLE `evidence_search_manifests`;--> statement-breakpoint
CREATE TRIGGER `evidence_retrieval_documents_immutable_update`
BEFORE UPDATE ON `evidence_retrieval_documents`
BEGIN SELECT RAISE(ABORT, 'evidence retrieval document is immutable'); END;--> statement-breakpoint
CREATE TRIGGER `evidence_retrieval_documents_immutable_delete`
BEFORE DELETE ON `evidence_retrieval_documents`
WHEN NOT EXISTS (
	SELECT 1 FROM `evidence_graph_snapshots` s
	JOIN `artifact_versions` v ON v.id = s.root_artifact_version_id
	JOIN `artifact_deletion_authorizations` d ON d.artifact_id = v.artifact_id
	WHERE s.id = OLD.snapshot_id
)
BEGIN SELECT RAISE(ABORT, 'evidence retrieval document is immutable'); END;--> statement-breakpoint
CREATE TRIGGER `evidence_retrieval_assessments_immutable_update`
BEFORE UPDATE ON `evidence_retrieval_assessments`
BEGIN SELECT RAISE(ABORT, 'evidence retrieval assessment is immutable'); END;--> statement-breakpoint
CREATE TRIGGER `evidence_retrieval_assessments_immutable_delete`
BEFORE DELETE ON `evidence_retrieval_assessments`
WHEN NOT EXISTS (
	SELECT 1 FROM `evidence_graph_snapshots` s
	JOIN `artifact_versions` v ON v.id = s.root_artifact_version_id
	JOIN `artifact_deletion_authorizations` d ON d.artifact_id = v.artifact_id
	WHERE s.id = OLD.snapshot_id
)
BEGIN SELECT RAISE(ABORT, 'evidence retrieval assessment is immutable'); END;--> statement-breakpoint
CREATE TRIGGER `evidence_retrieval_manifests_immutable_update`
BEFORE UPDATE ON `evidence_retrieval_manifests`
BEGIN SELECT RAISE(ABORT, 'evidence retrieval manifest is immutable'); END;--> statement-breakpoint
CREATE TRIGGER `evidence_retrieval_manifests_immutable_delete`
BEFORE DELETE ON `evidence_retrieval_manifests`
WHEN NOT EXISTS (
	SELECT 1 FROM `evidence_graph_snapshots` s
	JOIN `artifact_versions` v ON v.id = s.root_artifact_version_id
	JOIN `artifact_deletion_authorizations` d ON d.artifact_id = v.artifact_id
	WHERE s.id = OLD.snapshot_id
)
BEGIN SELECT RAISE(ABORT, 'evidence retrieval manifest is immutable'); END;
