import { sql } from "drizzle-orm";
import { check, foreignKey, index, integer, primaryKey, real, sqliteTable, text, uniqueIndex } from "drizzle-orm/sqlite-core";

/** A stable scientific object. Its byte representations live in artifact_versions. */
export const artifacts = sqliteTable("artifacts", {
  id: text("id").primaryKey(),
  projectId: text("project_id").notNull(),
  preferredName: text("preferred_name").notNull(),
  artifactType: text("artifact_type").notNull(),
  createdBy: text("created_by").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  index("artifact_project_created_idx").on(table.projectId, table.createdAt),
  check("artifact_id_check", sql`length(${table.id}) > 9 AND ${table.id} GLOB 'artifact:*'`),
  check("artifact_type_check", sql`${table.artifactType} IN ('paper', 'figure', 'table', 'dataset', 'code', 'web', 'other')`),
]);

/** Ephemeral capability used only by the atomic whole-artifact purge transaction. */
export const artifactDeletionAuthorizations = sqliteTable("artifact_deletion_authorizations", {
  artifactId: text("artifact_id").primaryKey().references(() => artifacts.id, { onDelete: "cascade" }),
  requestedBy: text("requested_by").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
});

/** Immutable, content-addressed bytes for one Artifact. */
export const artifactVersions = sqliteTable("artifact_versions", {
  id: text("id").primaryKey(),
  artifactId: text("artifact_id").notNull().references(() => artifacts.id, { onDelete: "restrict" }),
  versionNumber: integer("version_number").notNull(),
  sha256: text("sha256").notNull(),
  sourceLocator: text("source_locator").notNull(),
  mediaType: text("media_type").notNull(),
  originalFileName: text("original_file_name").notNull(),
  byteSize: integer("byte_size").notNull(),
  objectKey: text("object_key").notNull(),
  etag: text("etag"),
  clientRequestId: text("client_request_id").notNull(),
  requestHash: text("request_hash").notNull(),
  createdBy: text("created_by").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  uniqueIndex("artifact_version_sha256_idx").on(table.sha256),
  uniqueIndex("artifact_version_object_key_idx").on(table.objectKey),
  uniqueIndex("artifact_version_number_idx").on(table.artifactId, table.versionNumber),
  uniqueIndex("artifact_version_request_idx").on(table.createdBy, table.clientRequestId),
  index("artifact_version_artifact_created_idx").on(table.artifactId, table.createdAt),
  check("artifact_version_id_check", sql`${table.id} = 'artifact-version:sha256:' || ${table.sha256}`),
  check("artifact_version_number_check", sql`${table.versionNumber} >= 1`),
  check("artifact_version_sha256_check", sql`length(${table.sha256}) = 64 AND ${table.sha256} NOT GLOB '*[^0-9a-f]*'`),
  check("artifact_version_request_hash_check", sql`length(${table.requestHash}) = 64 AND ${table.requestHash} NOT GLOB '*[^0-9a-f]*'`),
  check("artifact_version_size_check", sql`${table.byteSize} > 0`),
]);

/** One immutable, schema-valid Paper Evidence Graph produced by an extractor run. */
export const evidenceGraphSnapshots = sqliteTable("evidence_graph_snapshots", {
  id: text("id").primaryKey(),
  projectId: text("project_id").notNull(),
  rootArtifactVersionId: text("root_artifact_version_id").notNull().references(() => artifactVersions.id, { onDelete: "restrict" }),
  schemaVersion: text("schema_version").notNull(),
  graphSha256: text("graph_sha256").notNull(),
  graphJson: text("graph_json").notNull(),
  countsJson: text("counts_json").notNull(),
  extractorRunId: text("extractor_run_id").notNull(),
  extractorModel: text("extractor_model").notNull(),
  extractorPromptVersion: text("extractor_prompt_version").notNull(),
  createdBy: text("created_by").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  uniqueIndex("evidence_graph_sha256_idx").on(table.projectId, table.graphSha256),
  uniqueIndex("evidence_graph_run_idx").on(table.projectId, table.extractorRunId),
  index("evidence_graph_artifact_created_idx").on(table.rootArtifactVersionId, table.createdAt),
  index("evidence_graph_project_created_idx").on(table.projectId, table.createdAt),
  index("evidence_graph_project_version_latest_idx").on(table.projectId, table.rootArtifactVersionId, sql`${table.createdAt} DESC`, sql`${table.id} DESC`),
  check("evidence_graph_id_check", sql`${table.id} = 'snapshot:sha256:' || ${table.graphSha256}`),
  check("evidence_graph_sha256_check", sql`length(${table.graphSha256}) = 64 AND ${table.graphSha256} NOT GLOB '*[^0-9a-f]*'`),
]);

/** Independent verifier output. It never mutates the extraction snapshot. */
export const verificationRuns = sqliteTable("verification_runs", {
  id: text("id").primaryKey(),
  projectId: text("project_id").notNull(),
  snapshotId: text("snapshot_id").notNull().references(() => evidenceGraphSnapshots.id, { onDelete: "restrict" }),
  verifierRunId: text("verifier_run_id").notNull(),
  verifierModel: text("verifier_model").notNull(),
  verifierPromptVersion: text("verifier_prompt_version").notNull(),
  reportSha256: text("report_sha256").notNull(),
  reportJson: text("report_json").notNull(),
  createdBy: text("created_by").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  uniqueIndex("verification_report_sha256_idx").on(table.projectId, table.reportSha256),
  uniqueIndex("verification_run_id_idx").on(table.projectId, table.verifierRunId),
  index("verification_snapshot_created_idx").on(table.snapshotId, table.createdAt),
  check("verification_report_sha256_check", sql`length(${table.reportSha256}) = 64 AND ${table.reportSha256} NOT GLOB '*[^0-9a-f]*'`),
]);

/** Mutable orchestration state; scientific outputs remain immutable snapshots. */
export const compileJobs = sqliteTable("compile_jobs", {
  id: text("id").primaryKey(),
  projectId: text("project_id").notNull(),
  artifactVersionId: text("artifact_version_id").notNull().references(() => artifactVersions.id, { onDelete: "restrict" }),
  upstreamJobId: text("upstream_job_id").notNull(),
  status: text("status").notNull(),
  stage: text("stage").notNull(),
  progress: integer("progress").notNull(),
  timingsJson: text("timings_json").notNull(),
  errorJson: text("error_json"),
  snapshotId: text("snapshot_id").references(() => evidenceGraphSnapshots.id, { onDelete: "restrict" }),
  clientRequestId: text("client_request_id").notNull(),
  requestHash: text("request_hash").notNull(),
  createdBy: text("created_by").notNull(),
  createdAt: text("created_at").notNull(),
  updatedAt: text("updated_at").notNull(),
  expiresAt: text("expires_at").notNull(),
  /** Bounded import retries; the lease is held only while an importer runs. */
  importAttempts: integer("import_attempts").notNull().default(0),
  importLeaseUntil: text("import_lease_until"),
  nextImportAt: text("next_import_at"),
  importErrorJson: text("import_error_json"),
  /** 1 once the completed Compiler result has been copied to R2. */
  resultStaged: integer("result_staged").notNull().default(0),
}, (table) => [
  uniqueIndex("compile_job_upstream_idx").on(table.projectId, table.upstreamJobId),
  uniqueIndex("compile_job_request_idx").on(table.projectId, table.createdBy, table.clientRequestId),
  index("compile_job_artifact_created_idx").on(table.artifactVersionId, table.createdAt),
  index("compile_job_status_updated_idx").on(table.projectId, table.status, table.updatedAt),
  check("compile_job_status_check", sql`${table.status} IN ('queued', 'running', 'completed', 'failed')`),
  check("compile_job_stage_check", sql`${table.stage} IN ('queued', 'parsing', 'extracting', 'validating', 'importing', 'completed', 'failed')`),
  check("compile_job_progress_check", sql`${table.progress} >= 0 AND ${table.progress} <= 100`),
  check("compile_job_request_hash_check", sql`length(${table.requestHash}) = 64 AND ${table.requestHash} NOT GLOB '*[^0-9a-f]*'`),
]);

/**
 * Server-side retrieval continuation state: the browser carries an opaque ID,
 * never a Compiler-signed token. Expired rows are pruned on write.
 */
export const retrievalSessions = sqliteTable("retrieval_sessions", {
  id: text("id").primaryKey(),
  kind: text("kind", { enum: ["query_plan_handoff", "context", "clarification"] }).notNull(),
  token: text("token").notNull(),
  createdAt: text("created_at").notNull(),
  expiresAt: text("expires_at").notNull(),
}, (table) => [
  index("retrieval_sessions_expiry_idx").on(table.expiresAt),
  check("retrieval_sessions_kind_check", sql`${table.kind} IN ('query_plan_handoff', 'context', 'clarification')`),
  check("retrieval_sessions_id_check", sql`${table.id} GLOB 'rs1.*' AND length(${table.id}) = 47`),
  check("retrieval_sessions_token_check", sql`length(${table.token}) BETWEEN 1 AND 700000`),
]);

/**
 * Durable background work (transactional outbox). Rows are written in the same
 * batch as the facts that create them and processed by the local work pump.
 * Subjects are soft references; a missing subject cancels its item.
 */
export const workItems = sqliteTable("work_items", {
  id: text("id").primaryKey(),
  projectId: text("project_id").notNull(),
  kind: text("kind").notNull(),
  subjectId: text("subject_id").notNull(),
  state: text("state").notNull(),
  priority: integer("priority").notNull(),
  attempts: integer("attempts").notNull().default(0),
  maxAttempts: integer("max_attempts").notNull(),
  nextAttemptAt: text("next_attempt_at").notNull(),
  leaseToken: text("lease_token"),
  leaseExpiresAt: text("lease_expires_at"),
  progressJson: text("progress_json").notNull().default("{}"),
  lastErrorJson: text("last_error_json"),
  createdAt: text("created_at").notNull(),
  updatedAt: text("updated_at").notNull(),
  completedAt: text("completed_at"),
}, (table) => [
  uniqueIndex("work_item_subject_idx").on(table.projectId, table.kind, table.subjectId),
  index("work_item_due_idx").on(table.projectId, table.state, table.nextAttemptAt, table.priority),
  index("work_item_recent_idx").on(table.projectId, table.kind, table.updatedAt),
  check("work_item_kind_check", sql`${table.kind} IN ('retrieval_index', 'claim_relations')`),
  check("work_item_state_check", sql`${table.state} IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')`),
  check("work_item_attempts_check", sql`${table.attempts} >= 0 AND ${table.maxAttempts} >= 1`),
  check("work_item_progress_check", sql`json_valid(${table.progressJson})`),
  check("work_item_error_check", sql`${table.lastErrorJson} IS NULL OR json_valid(${table.lastErrorJson})`),
  check("work_item_lease_check", sql`(${table.state} = 'running') = (${table.leaseToken} IS NOT NULL)`),
]);

/** Immutable AtomicClaim documents used to synchronize the external hybrid index. */
export const evidenceRetrievalDocuments = sqliteTable("evidence_retrieval_documents", {
  projectId: text("project_id").notNull(),
  snapshotId: text("snapshot_id").notNull().references(() => evidenceGraphSnapshots.id, { onDelete: "restrict" }),
  paperId: text("paper_id").notNull(),
  paperFamilyId: text("paper_family_id").notNull().references(() => artifacts.id, { onDelete: "restrict" }),
  claimId: text("claim_id").notNull(),
  scopedClaimId: text("scoped_claim_id").notNull(),
  propositionKey: text("proposition_key").notNull(),
  documentJson: text("document_json").notNull(),
  indexVersion: text("index_version").notNull(),
  createdAt: text("created_at").notNull(),
}, (table) => [
  primaryKey({ columns: [table.snapshotId, table.claimId] }),
  index("evidence_retrieval_document_project_claim_idx").on(table.projectId, table.claimId),
  index("evidence_retrieval_document_project_paper_idx").on(table.projectId, table.paperId, table.snapshotId),
  index("evidence_retrieval_document_project_family_idx").on(table.projectId, table.paperFamilyId, table.snapshotId),
  index("evidence_retrieval_document_proposition_idx").on(table.projectId, table.propositionKey),
]);

/** Rebuildable SQLite-normalized text for exact literal recall, maintained by triggers. */
export const evidenceQuickSearch = sqliteTable("evidence_quick_search", {
  projectId: text("project_id").notNull(),
  snapshotId: text("snapshot_id").notNull(),
  claimId: text("claim_id").notNull(),
  searchText: text("search_text").notNull(),
}, (table) => [
  primaryKey({ columns: [table.snapshotId, table.claimId] }),
  index("evidence_quick_search_project_snapshot_idx").on(table.projectId, table.snapshotId),
]);

/** Append-only verifier documents kept separate from extraction projections. */
export const evidenceRetrievalAssessments = sqliteTable("evidence_retrieval_assessments", {
  projectId: text("project_id").notNull(),
  verificationId: text("verification_id").notNull().references(() => verificationRuns.id, { onDelete: "restrict" }),
  snapshotId: text("snapshot_id").notNull().references(() => evidenceGraphSnapshots.id, { onDelete: "restrict" }),
  assessmentId: text("assessment_id").notNull(),
  claimId: text("claim_id").notNull(),
  assessmentJson: text("assessment_json").notNull(),
  indexVersion: text("index_version").notNull(),
  createdAt: text("created_at").notNull(),
}, (table) => [
  primaryKey({ columns: [table.verificationId, table.assessmentId] }),
  index("evidence_retrieval_assessment_claim_idx").on(table.projectId, table.snapshotId, table.claimId, table.createdAt),
]);

/** Completion/freshness marker, including valid snapshots containing zero Claims. */
export const evidenceRetrievalManifests = sqliteTable("evidence_retrieval_manifests", {
  snapshotId: text("snapshot_id").primaryKey().references(() => evidenceGraphSnapshots.id, { onDelete: "restrict" }),
  projectId: text("project_id").notNull(),
  paperId: text("paper_id").notNull(),
  paperFamilyId: text("paper_family_id").notNull().references(() => artifacts.id, { onDelete: "restrict" }),
  rootArtifactVersionId: text("root_artifact_version_id").notNull().references(() => artifactVersions.id, { onDelete: "restrict" }),
  rootArtifactSha256: text("root_artifact_sha256").notNull(),
  graphSha256: text("graph_sha256").notNull(),
  graphSchemaVersion: text("graph_schema_version").notNull(),
  extractorPromptVersion: text("extractor_prompt_version").notNull(),
  indexVersion: text("index_version").notNull(),
  documentCount: integer("document_count").notNull(),
  manifestJson: text("manifest_json").notNull(),
  createdAt: text("created_at").notNull(),
}, (table) => [
  index("evidence_retrieval_manifest_active_family_idx").on(table.projectId, table.indexVersion, table.paperFamilyId, table.createdAt),
  index("evidence_retrieval_manifest_active_paper_idx").on(table.projectId, table.indexVersion, table.paperId, table.createdAt),
  index("evidence_manifest_family_winner_idx").on(table.projectId, table.indexVersion, table.paperFamilyId, sql`julianday(${table.createdAt}) DESC`, sql`${table.createdAt} DESC`, sql`${table.snapshotId} DESC`),
  check("evidence_retrieval_manifest_count_check", sql`${table.documentCount} >= 0`),
  check("evidence_retrieval_manifest_root_sha256_check", sql`length(${table.rootArtifactSha256}) = 64 AND ${table.rootArtifactSha256} NOT GLOB '*[^0-9a-f]*'`),
  check("evidence_retrieval_manifest_graph_sha256_check", sql`length(${table.graphSha256}) = 64 AND ${table.graphSha256} NOT GLOB '*[^0-9a-f]*'`),
]);

/** One completed corpus-level direct-inference pass for an active AtomicClaim. */
export const corpusClaimGraphCompletions = sqliteTable("corpus_claim_graph_completions", {
  projectId: text("project_id").notNull(),
  snapshotId: text("snapshot_id").notNull(),
  claimId: text("claim_id").notNull(),
  processorVersion: text("processor_version").notNull(),
  candidateCount: integer("candidate_count").notNull(),
  edgeCount: integer("edge_count").notNull(),
  classifierModel: text("classifier_model").notNull(),
  completedAt: text("completed_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  primaryKey({ columns: [table.snapshotId, table.claimId, table.processorVersion] }),
  foreignKey({
    columns: [table.snapshotId, table.claimId],
    foreignColumns: [evidenceRetrievalDocuments.snapshotId, evidenceRetrievalDocuments.claimId],
  }).onDelete("restrict"),
  index("corpus_claim_graph_completion_project_idx").on(
    table.projectId,
    table.processorVersion,
    table.completedAt,
  ),
  check("corpus_claim_graph_completion_counts_check", sql`${table.candidateCount} >= 0
    AND ${table.edgeCount} >= 0`),
  check("corpus_claim_graph_completion_processor_check", sql`length(${table.processorVersion}) BETWEEN 1 AND 100`),
  check("corpus_claim_graph_completion_model_check", sql`length(${table.classifierModel}) BETWEEN 1 AND 200`),
]);

/** One OpenAlex-official paper classification for an immutable snapshot. */
export const openAlexPaperClassifications = sqliteTable("openalex_paper_classifications", {
  snapshotId: text("snapshot_id").primaryKey().references(() => evidenceGraphSnapshots.id, { onDelete: "restrict" }),
  projectId: text("project_id").notNull(),
  source: text("source").notNull(),
  classifier: text("classifier").notNull(),
  openAlexWorkId: text("openalex_work_id"),
  matchedDoi: text("matched_doi"),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  index("openalex_paper_classification_project_idx").on(table.projectId, table.createdAt),
  index("openalex_paper_classification_work_idx").on(table.projectId, table.openAlexWorkId, table.snapshotId),
  index("openalex_paper_classification_doi_idx").on(table.projectId, table.matchedDoi, table.snapshotId),
  check("openalex_paper_classification_source_check", sql`${table.source} IN ('openalex_work', 'official_title_abstract_model')`),
  check("openalex_paper_classification_classifier_check", sql`length(${table.classifier}) BETWEEN 1 AND 300`),
]);

/** Up to three OpenAlex Topics; each carries the official four-level hierarchy. */
export const openAlexPaperTopics = sqliteTable("openalex_paper_topics", {
  snapshotId: text("snapshot_id").notNull().references(() => openAlexPaperClassifications.snapshotId, { onDelete: "restrict" }),
  topicId: text("topic_id").notNull(),
  projectId: text("project_id").notNull(),
  displayName: text("display_name").notNull(),
  score: real("score").notNull(),
  isPrimary: integer("is_primary", { mode: "boolean" }).notNull(),
  ordinal: integer("ordinal").notNull(),
  subfieldId: text("subfield_id").notNull(),
  subfieldName: text("subfield_name").notNull(),
  fieldId: text("field_id").notNull(),
  fieldName: text("field_name").notNull(),
  domainId: text("domain_id").notNull(),
  domainName: text("domain_name").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  primaryKey({ columns: [table.snapshotId, table.topicId] }),
  uniqueIndex("openalex_paper_topic_ordinal_idx").on(table.snapshotId, table.ordinal),
  index("openalex_paper_topic_domain_idx").on(table.projectId, table.domainId, table.snapshotId),
  index("openalex_paper_topic_field_idx").on(table.projectId, table.fieldId, table.snapshotId),
  index("openalex_paper_topic_subfield_idx").on(table.projectId, table.subfieldId, table.snapshotId),
  index("openalex_paper_topic_topic_idx").on(table.projectId, table.topicId, table.snapshotId),
  check("openalex_paper_topic_id_check", sql`${table.topicId} GLOB 'https://openalex.org/T[0-9]*'`),
  check("openalex_paper_topic_score_check", sql`${table.score} >= 0 AND ${table.score} <= 1`),
  check("openalex_paper_topic_ordinal_check", sql`${table.ordinal} BETWEEN 0 AND 2`),
]);

/** Rebuildable cross-paper Claim relations; implication is directed, comparison is symmetric. */
export const corpusClaimGraphEdges = sqliteTable("corpus_claim_graph_edges", {
  id: text("id").primaryKey(),
  pairKey: text("pair_key"),
  projectId: text("project_id").notNull(),
  sourceSnapshotId: text("source_snapshot_id").notNull(),
  sourceClaimId: text("source_claim_id").notNull(),
  targetSnapshotId: text("target_snapshot_id").notNull(),
  targetClaimId: text("target_claim_id").notNull(),
  relation: text("relation").notNull(),
  modelRelation: text("model_relation").notNull(),
  confidence: real("confidence"),
  processorVersion: text("processor_version").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  foreignKey({
    columns: [table.sourceSnapshotId, table.sourceClaimId],
    foreignColumns: [evidenceRetrievalDocuments.snapshotId, evidenceRetrievalDocuments.claimId],
  }).onDelete("restrict"),
  foreignKey({
    columns: [table.targetSnapshotId, table.targetClaimId],
    foreignColumns: [evidenceRetrievalDocuments.snapshotId, evidenceRetrievalDocuments.claimId],
  }).onDelete("restrict"),
  uniqueIndex("corpus_claim_graph_edge_endpoints_idx").on(
    table.processorVersion,
    table.sourceSnapshotId,
    table.sourceClaimId,
    table.targetSnapshotId,
    table.targetClaimId,
  ),
  uniqueIndex("corpus_claim_graph_edge_pair_idx").on(table.processorVersion, table.pairKey),
  index("corpus_claim_graph_edge_project_idx").on(table.projectId, table.processorVersion, table.createdAt),
  index("corpus_claim_graph_edge_source_idx").on(table.sourceSnapshotId, table.sourceClaimId),
  index("corpus_claim_graph_edge_target_idx").on(table.targetSnapshotId, table.targetClaimId),
  check("corpus_claim_graph_edge_id_check", sql`length(${table.id}) = 82
    AND ${table.id} GLOB 'claim-edge:sha256:*'
    AND substr(${table.id}, 19) NOT GLOB '*[^0-9a-f]*'`),
  check("corpus_claim_graph_edge_pair_check", sql`${table.pairKey} IS NULL OR (
    length(${table.pairKey}) = 82
    AND ${table.pairKey} GLOB 'claim-pair:sha256:*'
    AND substr(${table.pairKey}, 19) NOT GLOB '*[^0-9a-f]*')`),
  check("corpus_claim_graph_edge_distinct_check", sql`${table.sourceSnapshotId} <> ${table.targetSnapshotId}
    OR ${table.sourceClaimId} <> ${table.targetClaimId}`),
  check("corpus_claim_graph_edge_relation_check", sql`(${table.relation} = 'directly_implies'
      AND ${table.modelRelation} IN ('supports', 'refines', 'qualifies'))
    OR (${table.relation} = ${table.modelRelation}
      AND ${table.modelRelation} IN ('equivalent_to', 'contradicts', 'supplements'))`),
  check("corpus_claim_graph_edge_confidence_check", sql`${table.confidence} IS NULL
    OR (${table.confidence} >= 0 AND ${table.confidence} <= 1)`),
  check("corpus_claim_graph_edge_processor_check", sql`length(${table.processorVersion}) BETWEEN 1 AND 100`),
]);

/** Immutable, deterministic bibliography/mention projection for one snapshot. */
export const citationSourceManifests = sqliteTable("citation_source_manifests", {
  snapshotId: text("snapshot_id").primaryKey().references(() => evidenceGraphSnapshots.id, { onDelete: "restrict" }),
  projectId: text("project_id").notNull(),
  schemaVersion: text("schema_version").notNull(),
  extractorVersion: text("extractor_version").notNull(),
  sourceSha256: text("source_sha256").notNull(),
  referenceCount: integer("reference_count").notNull(),
  mentionCount: integer("mention_count").notNull(),
  warningsJson: text("warnings_json").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  uniqueIndex("citation_source_manifest_hash_idx").on(table.projectId, table.sourceSha256),
  index("citation_source_manifest_project_idx").on(table.projectId, table.createdAt),
  check("citation_source_manifest_version_check", sql`${table.schemaVersion} = 'citation-source/1.0'
    AND ${table.extractorVersion} = 'citation-source-extractor/1.0'`),
  check("citation_source_manifest_hash_check", sql`length(${table.sourceSha256}) = 64
    AND ${table.sourceSha256} NOT GLOB '*[^0-9a-f]*'`),
  check("citation_source_manifest_counts_check", sql`${table.referenceCount} >= 0
    AND ${table.mentionCount} >= 0`),
]);

/** One source-authored bibliography entry; local-work resolution remains derived. */
export const bibliographicReferences = sqliteTable("bibliographic_references", {
  id: text("id").primaryKey(),
  projectId: text("project_id").notNull(),
  citingSnapshotId: text("citing_snapshot_id").notNull().references(() => citationSourceManifests.snapshotId, { onDelete: "restrict" }),
  ordinal: integer("ordinal").notNull(),
  label: text("label").notNull(),
  rawText: text("raw_text").notNull(),
  normalizedDoi: text("normalized_doi"),
  year: integer("year"),
  sourceBlockId: text("source_block_id").notNull(),
  page: integer("page").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  uniqueIndex("bibliographic_reference_snapshot_id_idx").on(table.citingSnapshotId, table.id),
  uniqueIndex("bibliographic_reference_ordinal_idx").on(table.citingSnapshotId, table.ordinal),
  index("bibliographic_reference_doi_idx").on(table.projectId, table.normalizedDoi, table.citingSnapshotId),
  index("bibliographic_reference_snapshot_idx").on(table.citingSnapshotId, table.ordinal),
  check("bibliographic_reference_id_check", sql`length(${table.id}) = 90
    AND ${table.id} GLOB 'citation-reference:sha256:*'
    AND substr(${table.id}, 27) NOT GLOB '*[^0-9a-f]*'`),
  check("bibliographic_reference_ordinal_check", sql`${table.ordinal} >= 1`),
  check("bibliographic_reference_page_check", sql`${table.page} >= 1`),
  check("bibliographic_reference_year_check", sql`${table.year} IS NULL
    OR ${table.year} BETWEEN 1800 AND 2199`),
]);

/** One exact in-text marker linked to one bibliography entry. */
export const citationMentions = sqliteTable("citation_mentions", {
  id: text("id").primaryKey(),
  projectId: text("project_id").notNull(),
  citingSnapshotId: text("citing_snapshot_id").notNull(),
  referenceId: text("reference_id").notNull(),
  marker: text("marker").notNull(),
  exactQuote: text("exact_quote").notNull(),
  contextText: text("context_text").notNull(),
  sourceBlockId: text("source_block_id").notNull(),
  contextBlockIdsJson: text("context_block_ids_json").notNull(),
  page: integer("page").notNull(),
  bboxJson: text("bbox_json"),
  charStart: integer("char_start").notNull(),
  charEnd: integer("char_end").notNull(),
  groupSize: integer("group_size").notNull(),
  candidateFunctionsJson: text("candidate_functions_json").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  foreignKey({
    columns: [table.citingSnapshotId, table.referenceId],
    foreignColumns: [bibliographicReferences.citingSnapshotId, bibliographicReferences.id],
  }).onDelete("restrict"),
  uniqueIndex("citation_mention_snapshot_id_idx").on(table.citingSnapshotId, table.id),
  index("citation_mention_reference_idx").on(table.citingSnapshotId, table.referenceId, table.page),
  check("citation_mention_id_check", sql`length(${table.id}) = 88
    AND ${table.id} GLOB 'citation-mention:sha256:*'
    AND substr(${table.id}, 25) NOT GLOB '*[^0-9a-f]*'`),
  check("citation_mention_page_check", sql`${table.page} >= 1`),
  check("citation_mention_offsets_check", sql`${table.charStart} >= 0
    AND ${table.charEnd} > ${table.charStart}`),
  check("citation_mention_group_check", sql`${table.groupSize} BETWEEN 1 AND 100`),
]);

/** One rebuildable A-reference-to-local-B processing run, including valid zero assertions. */
export const citationReceptionRuns = sqliteTable("citation_reception_runs", {
  id: text("id").primaryKey(),
  projectId: text("project_id").notNull(),
  citingSnapshotId: text("citing_snapshot_id").notNull(),
  referenceId: text("reference_id").notNull(),
  citedSnapshotId: text("cited_snapshot_id").notNull().references(() => evidenceGraphSnapshots.id, { onDelete: "restrict" }),
  processorVersion: text("processor_version").notNull(),
  resolutionMethod: text("resolution_method").notNull(),
  resolutionConfidence: real("resolution_confidence").notNull(),
  extractionRunId: text("extraction_run_id").notNull(),
  extractionModel: text("extraction_model").notNull(),
  extractionPromptVersion: text("extraction_prompt_version").notNull(),
  assertionsSha256: text("assertions_sha256").notNull(),
  verificationRunId: text("verification_run_id"),
  verifierModel: text("verifier_model"),
  verifierPromptVersion: text("verifier_prompt_version"),
  assertionCount: integer("assertion_count").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  foreignKey({
    columns: [table.citingSnapshotId, table.referenceId],
    foreignColumns: [bibliographicReferences.citingSnapshotId, bibliographicReferences.id],
  }).onDelete("restrict"),
  uniqueIndex("citation_reception_run_pair_idx").on(
    table.processorVersion,
    table.citingSnapshotId,
    table.referenceId,
    table.citedSnapshotId,
  ),
  index("citation_reception_run_target_idx").on(table.projectId, table.citedSnapshotId, table.processorVersion, table.createdAt),
  index("citation_reception_run_source_idx").on(table.projectId, table.citingSnapshotId, table.processorVersion, table.createdAt),
  check("citation_reception_run_id_check", sql`length(${table.id}) = 84
    AND ${table.id} GLOB 'citation-run:sha256:*'
    AND substr(${table.id}, 21) NOT GLOB '*[^0-9a-f]*'`),
  check("citation_reception_run_processor_check", sql`${table.processorVersion} = 'citation-reception-processor/1.0.0'`),
  check("citation_reception_run_resolution_check", sql`${table.resolutionMethod} IN ('openalex_work_id', 'doi', 'title')
    AND ${table.resolutionConfidence} >= 0 AND ${table.resolutionConfidence} <= 1`),
  check("citation_reception_run_hash_check", sql`length(${table.assertionsSha256}) = 64
    AND ${table.assertionsSha256} NOT GLOB '*[^0-9a-f]*'`),
  check("citation_reception_run_count_check", sql`${table.assertionCount} >= 0`),
  check("citation_reception_run_verifier_check", sql`(${table.assertionCount} = 0
      AND ${table.verificationRunId} IS NULL AND ${table.verifierModel} IS NULL AND ${table.verifierPromptVersion} IS NULL)
    OR (${table.assertionCount} > 0
      AND ${table.verificationRunId} IS NOT NULL AND ${table.verifierModel} IS NOT NULL AND ${table.verifierPromptVersion} IS NOT NULL)`),
]);

/** One model-extracted proposition that A attributes to local work B. */
export const citationAssertions = sqliteTable("citation_assertions", {
  id: text("id").primaryKey(),
  runId: text("run_id").notNull().references(() => citationReceptionRuns.id, { onDelete: "restrict" }),
  projectId: text("project_id").notNull(),
  mentionId: text("mention_id").notNull().references(() => citationMentions.id, { onDelete: "restrict" }),
  attributedProposition: text("attributed_proposition").notNull(),
  citationFunction: text("citation_function").notNull(),
  stance: text("stance").notNull(),
  confirmationBasis: text("confirmation_basis").notNull(),
  targetEntityKind: text("target_entity_kind"),
  targetEntityId: text("target_entity_id"),
  citingEvidenceEntityIdsJson: text("citing_evidence_entity_ids_json").notNull(),
  confidence: real("confidence").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  index("citation_assertion_run_idx").on(table.runId, table.createdAt),
  index("citation_assertion_target_idx").on(table.targetEntityKind, table.targetEntityId),
  check("citation_assertion_id_check", sql`length(${table.id}) = 90
    AND ${table.id} GLOB 'citation-assertion:sha256:*'
    AND substr(${table.id}, 27) NOT GLOB '*[^0-9a-f]*'`),
  check("citation_assertion_function_check", sql`${table.citationFunction} IN ('result_comparison', 'replication', 'critique')`),
  check("citation_assertion_stance_check", sql`${table.stance} IN ('supports', 'contradicts', 'qualifies', 'unclear')`),
  check("citation_assertion_basis_check", sql`${table.confirmationBasis} IN ('mere_attribution', 'verbal_endorsement',
    'verbal_critique', 'consistent_with_citing_evidence', 'contradicted_by_citing_evidence',
    'qualified_by_citing_evidence', 'direct_replication', 'reanalysis', 'meta_analytic_synthesis')`),
  check("citation_assertion_target_check", sql`(${table.targetEntityKind} IS NULL AND ${table.targetEntityId} IS NULL)
    OR (${table.targetEntityKind} IN ('source_anchor', 'structured_visual_evidence', 'observation', 'reasoning', 'atomic_claim')
      AND ${table.targetEntityId} IS NOT NULL)`),
  check("citation_assertion_confidence_check", sql`${table.confidence} >= 0 AND ${table.confidence} <= 1`),
]);

/** Independent verifier result for one frozen CitationAssertion. */
export const citationAssessments = sqliteTable("citation_assessments", {
  id: text("id").primaryKey(),
  assertionId: text("assertion_id").notNull().unique().references(() => citationAssertions.id, { onDelete: "restrict" }),
  projectId: text("project_id").notNull(),
  faithfulness: text("faithfulness").notNull(),
  confirmationBasis: text("confirmation_basis").notNull(),
  independence: text("independence").notNull(),
  confidence: real("confidence").notNull(),
  evidenceIdsJson: text("evidence_ids_json").notNull(),
  limitationsJson: text("limitations_json").notNull(),
  extractionSha256: text("extraction_sha256").notNull(),
  verifierModel: text("verifier_model").notNull(),
  verifierPromptVersion: text("verifier_prompt_version").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  index("citation_assessment_quality_idx").on(table.faithfulness, table.confirmationBasis, table.independence),
  check("citation_assessment_id_check", sql`length(${table.id}) = 91
    AND ${table.id} GLOB 'citation-assessment:sha256:*'
    AND substr(${table.id}, 28) NOT GLOB '*[^0-9a-f]*'`),
  check("citation_assessment_faithfulness_check", sql`${table.faithfulness} IN ('faithful', 'partial', 'overgeneralized',
    'contradicted_by_target', 'not_found', 'ambiguous', 'indirect')`),
  check("citation_assessment_basis_check", sql`${table.confirmationBasis} IN ('mere_attribution', 'verbal_endorsement',
    'verbal_critique', 'consistent_with_citing_evidence', 'contradicted_by_citing_evidence',
    'qualified_by_citing_evidence', 'direct_replication', 'reanalysis', 'meta_analytic_synthesis')`),
  check("citation_assessment_independence_check", sql`${table.independence} IN ('independent', 'shared_authors', 'shared_data', 'derived_data', 'unknown')`),
  check("citation_assessment_confidence_check", sql`${table.confidence} >= 0 AND ${table.confidence} <= 1`),
  check("citation_assessment_hash_check", sql`length(${table.extractionSha256}) = 64
    AND ${table.extractionSha256} NOT GLOB '*[^0-9a-f]*'`),
]);

/** Append-only human review-state events about a whole snapshot. */
export const graphReviewEvents = sqliteTable("graph_review_events", {
  seq: integer("seq").primaryKey({ autoIncrement: true }),
  id: text("id").notNull().unique(),
  projectId: text("project_id").notNull(),
  snapshotId: text("snapshot_id").notNull().references(() => evidenceGraphSnapshots.id, { onDelete: "restrict" }),
  decision: text("decision").notNull(),
  rationale: text("rationale").notNull(),
  reviewer: text("reviewer").notNull(),
  clientRequestId: text("client_request_id").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  uniqueIndex("graph_review_request_idx").on(table.projectId, table.reviewer, table.clientRequestId),
  index("graph_review_snapshot_seq_idx").on(table.snapshotId, table.seq),
  check("graph_review_decision_check", sql`${table.decision} IN ('accepted', 'rejected', 'needs_revision', 'withdrawn')`),
]);

/** Small append-only provenance log for storage and import events. */
export const auditEvents = sqliteTable("audit_events", {
  seq: integer("seq").primaryKey({ autoIncrement: true }),
  id: text("id").notNull().unique(),
  projectId: text("project_id").notNull(),
  eventType: text("event_type").notNull(),
  entityType: text("entity_type").notNull(),
  entityId: text("entity_id").notNull(),
  actor: text("actor").notNull(),
  payloadJson: text("payload_json").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
  index("audit_project_seq_idx").on(table.projectId, table.seq),
  index("audit_entity_seq_idx").on(table.entityType, table.entityId, table.seq),
]);

/** Rebuildable compact library read models; immutable evidence remains authoritative. */
export const libraryArtifactSummaries = sqliteTable("library_artifact_summaries", {
  searchId: integer("search_id").primaryKey({ autoIncrement: true }),
  artifactId: text("artifact_id").notNull(),
  projectId: text("project_id").notNull(),
  createdAt: text("created_at").notNull(),
  preferredName: text("preferred_name").notNull(),
  originalFileName: text("original_file_name").notNull(),
  latestVersionId: text("latest_version_id"),
  versionCount: integer("version_count").notNull(),
  latestCompileJobId: text("latest_compile_job_id"),
  latestSnapshotId: text("latest_snapshot_id"),
  libraryStatus: text("library_status").notNull(),
}, (table) => [
  uniqueIndex("library_artifact_id_idx").on(table.artifactId),
  index("library_project_created_idx").on(table.projectId, table.createdAt, table.artifactId),
  index("library_project_status_created_idx").on(table.projectId, table.libraryStatus, table.createdAt, table.artifactId),
]);
export const libraryStatusCounts = sqliteTable("library_status_counts", {
  projectId: text("project_id").notNull(),
  status: text("status").notNull(),
  artifactCount: integer("artifact_count").notNull(),
}, (table) => [primaryKey({columns: [table.projectId, table.status]})]);
export const libraryVerificationSummaries = sqliteTable("library_verification_summaries", {
  verificationId: text("verification_id").primaryKey(),
  limitationCount: integer("limitation_count").notNull(),
});

/** Manifest completion updates one family winner, then one paper winner. No graph JSON. */
export const evidenceFamilySnapshots = sqliteTable("evidence_family_snapshots", {
  snapshotId: text("snapshot_id").notNull(),
  projectId: text("project_id").notNull(),
  indexVersion: text("index_version").notNull(),
  paperId: text("paper_id").notNull(),
  paperFamilyId: text("paper_family_id").notNull(),
  documentCount: integer("document_count").notNull(),
  createdAt: text("created_at").notNull(),
}, (table) => [
  primaryKey({ columns: [table.projectId, table.indexVersion, table.paperFamilyId] }),
  uniqueIndex("evidence_family_snapshot_idx").on(table.snapshotId),
  index("evidence_family_paper_winner_idx").on(table.projectId, table.indexVersion, table.paperId, sql`julianday(${table.createdAt}) DESC`, sql`${table.createdAt} DESC`, sql`${table.snapshotId} DESC`),
]);
export const evidenceActiveSnapshots = sqliteTable("evidence_active_snapshots", {
  snapshotId: text("snapshot_id").primaryKey(),
  projectId: text("project_id").notNull(),
  indexVersion: text("index_version").notNull(),
  paperId: text("paper_id").notNull(),
  paperFamilyId: text("paper_family_id").notNull(),
  documentCount: integer("document_count").notNull(),
  createdAt: text("created_at").notNull(),
}, (table) => [
  uniqueIndex("evidence_active_paper_idx").on(table.projectId, table.indexVersion, table.paperId),
  index("evidence_active_family_idx").on(table.projectId, table.paperFamilyId),
]);

/** Rebuildable, incrementally maintained graph browsing metadata. */
export const graphReadCounts = sqliteTable("graph_read_counts", {
  projectId: text("project_id").primaryKey(),
  allCount: integer("all_count").notNull().default(0),
  processedCount: integer("processed_count").notNull().default(0),
  edgeCount: integer("edge_count").notNull().default(0),
  unclassifiedPapers: integer("unclassified_papers").notNull().default(0),
});
export const graphActiveClaims = sqliteTable("graph_active_claims", {
  snapshotId: text("snapshot_id").notNull(),
  claimId: text("claim_id").notNull(),
  projectId: text("project_id").notNull(),
  createdAt: text("created_at").notNull(),
  completedAt: text("completed_at"),
}, (table) => [
  primaryKey({ columns: [table.snapshotId, table.claimId] }),
  index("graph_active_claims_page_idx").on(table.projectId, sql`${table.createdAt} DESC`, sql`${table.snapshotId} DESC`, sql`${table.claimId} DESC`),
  index("graph_active_claims_status_page_idx").on(table.projectId, sql`(${table.completedAt} IS NULL)`, sql`${table.createdAt} DESC`, sql`${table.snapshotId} DESC`, sql`${table.claimId} DESC`),
]);
export const graphTaxonomyNodes = sqliteTable("graph_taxonomy_nodes", {
  projectId: text("project_id").notNull(),
  level: text("level").notNull(),
  id: text("id").notNull(),
  displayName: text("display_name").notNull(),
  parentId: text("parent_id"),
  paperCount: integer("paper_count").notNull().default(0),
  claimCount: integer("claim_count").notNull().default(0),
  primaryClaimCount: integer("primary_claim_count").notNull().default(0),
  processedCount: integer("processed_count").notNull().default(0),
  edgeCount: integer("edge_count").notNull().default(0),
}, (table) => [primaryKey({ columns: [table.projectId, table.level, table.id] })]);
export const graphTaxonomyMemberships = sqliteTable("graph_taxonomy_memberships", {
  snapshotId: text("snapshot_id").notNull(),
  projectId: text("project_id").notNull(),
  level: text("level").notNull(),
  id: text("id").notNull(),
  displayName: text("display_name").notNull(),
  parentId: text("parent_id"),
  isPrimary: integer("is_primary").notNull(),
  createdAt: text("created_at").notNull(),
  claimCount: integer("claim_count").notNull(),
  processedCount: integer("processed_count").notNull(),
}, (table) => [
  primaryKey({ columns: [table.snapshotId, table.level, table.id] }),
  index("graph_taxonomy_membership_scope_idx").on(table.projectId, table.level, table.id, sql`${table.createdAt} DESC`, sql`${table.snapshotId} DESC`),
  index("graph_taxonomy_membership_pending_idx").on(table.projectId, table.level, table.id, sql`${table.createdAt} DESC`, sql`${table.snapshotId} DESC`).where(sql`${table.claimCount}>${table.processedCount}`),
  index("graph_taxonomy_membership_processed_idx").on(table.projectId, table.level, table.id, sql`${table.createdAt} DESC`, sql`${table.snapshotId} DESC`).where(sql`${table.processedCount}>0`),
]);

/** FTS5 contentless virtual table; the migration owns tokenizer and shadow tables. */
export const libraryNameSearch = sqliteTable("library_name_search", { tokens: text("tokens") });

/** All immutable snapshots remain independently browsable, including old versions. */
export const snapshotListSummaries = sqliteTable("snapshot_list_summaries", {
  snapshotId: text("snapshot_id").primaryKey(),
  projectId: text("project_id").notNull(),
  artifactId: text("artifact_id").notNull(),
  title: text("title").notNull(),
  originalFileName: text("original_file_name").notNull(),
  createdAt: text("created_at").notNull(),
  latestReviewId: text("latest_review_id"),
  reviewStatus: text("review_status").notNull(),
}, (table) => [
  index("snapshot_list_created_idx").on(table.projectId, table.createdAt, table.snapshotId),
  index("snapshot_list_status_created_idx").on(table.projectId, table.reviewStatus, table.createdAt, table.snapshotId),
  index("snapshot_list_artifact_idx").on(table.artifactId),
]);
export const snapshotListCounts = sqliteTable("snapshot_list_counts", {
  projectId: text("project_id").notNull(),
  status: text("status").notNull(),
  snapshotCount: integer("snapshot_count").notNull(),
}, (table) => [primaryKey({columns: [table.projectId, table.status]})]);

/** Only active papers awaiting their first OpenAlex classification. */
export const graphUnclassifiedSnapshots = sqliteTable("graph_unclassified_snapshots", {
  snapshotId: text("snapshot_id").primaryKey(),
  projectId: text("project_id").notNull(),
  createdAt: text("created_at").notNull(),
}, (table) => [index("graph_unclassified_queue_idx").on(table.projectId, table.createdAt, table.snapshotId)]);
