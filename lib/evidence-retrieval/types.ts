import type {
  AnchorRegion,
  AssessmentVerdict,
  ClaimModality,
  ClaimPolarity,
  ClaimType,
  EvidenceEntity,
  Scope,
  ScopeOverlap,
} from "../evidence-graph/types.ts";

import {
  EVIDENCE_RETRIEVAL_CONTEXT_PROTOCOL_VERSION,
  EVIDENCE_RETRIEVAL_INDEX_VERSION,
  EVIDENCE_RETRIEVAL_SEARCH_PROTOCOL_VERSION,
} from "../shared-contract.generated.ts";

// Shared with the Compiler through contracts/shared-contract.json. The search
// protocol (2.1) is search-only; the persisted retrieval projection stays 2.0.
export {
  EVIDENCE_RETRIEVAL_CONTEXT_PROTOCOL_VERSION,
  EVIDENCE_RETRIEVAL_INDEX_VERSION,
  EVIDENCE_RETRIEVAL_SEARCH_PROTOCOL_VERSION,
  QUERY_PLAN_HANDOFF_MAX_LENGTH,
  RETRIEVAL_CONTEXT_MAX_BYTES,
  RETRIEVAL_CONTEXT_MAX_CONTENT_LENGTH,
  RETRIEVAL_CONTEXT_MAX_TURNS,
} from "../shared-contract.generated.ts";
export type ConversationTurn = { role: "user" | "assistant"; content: string };
export type EvidenceRetrievalSearchProtocolVersion =
  | typeof EVIDENCE_RETRIEVAL_INDEX_VERSION
  | typeof EVIDENCE_RETRIEVAL_SEARCH_PROTOCOL_VERSION
  | typeof EVIDENCE_RETRIEVAL_CONTEXT_PROTOCOL_VERSION;

export type EvidenceRetrievalAttribution = "authors_conclusion";
export type EvidenceRetrievalRhetoricalRole = "terminal_conclusion";

export interface EvidencePathNode {
  id: string;
  kind: EvidenceEntity["kind"];
  label: string;
}

export interface EvidencePathLink {
  fromId: string;
  toId: string;
  relation: string;
  linkType: "edge" | "provenance";
  confidence: number | null;
}

export interface ShortestEvidencePath {
  rootClaimId: string;
  nodes: EvidencePathNode[];
  links: EvidencePathLink[];
  depth: number;
  meanEdgeConfidence: number | null;
}

export interface CompactEvidenceAnchor {
  anchorId: string;
  artifactVersionId: string;
  anchorType: "text" | "figure" | "table" | "dataset" | "metadata" | "citation";
  page: number | null;
  section: string | null;
  paragraph: number | null;
  quote: string | null;
  figure: string | null;
  table: string | null;
  rowStart: number | null;
  rowEnd: number | null;
  rowRangeLabel: string | null;
  columnNames: string[];
  series: string | null;
  charStart: number | null;
  charEnd: number | null;
  region: AnchorRegion | null;
  regionLabel: string | null;
  datasetPath: string | null;
}

export interface EvidenceRetrievalAbbreviation {
  shortForm: string;
  /** Only an explicitly present source expansion is retained. It is never inferred. */
  longForm: string | null;
}

export type ExplicitClaimRelationType =
  | "equivalent_to"
  | "supports"
  | "contradicts"
  | "refines"
  | "qualifies"
  | "supplements";

export interface ExplicitClaimRelation {
  edgeId: string;
  sourceClaimId: string;
  targetClaimId: string;
  otherClaimId: string;
  relation: ExplicitClaimRelationType;
  direction: "outgoing" | "incoming";
  confidence: number | null;
  scopeOverlap: ScopeOverlap;
}

/** One immutable AtomicClaim search document. */
export interface EvidenceRetrievalDocument {
  indexVersion: typeof EVIDENCE_RETRIEVAL_INDEX_VERSION;
  snapshotId: string;
  paperId: string;
  /** Stable graph Artifact identity across byte versions. */
  paperFamilyId: string;
  rootArtifactVersionId: string;
  claimId: string;
  sourceClaimId: string | null;
  /** Exact source-anchor text only; null when no exact quote can be retained safely. */
  originalClaim: string | null;
  canonicalClaim: string;
  propositionKey: string;
  scopedClaimId: string;
  paperTitle: string;
  evidenceContext: string;
  entities: string[];
  keyTerms: string[];
  abbreviations: EvidenceRetrievalAbbreviation[];
  qualifiers: string[];
  claimType: ClaimType;
  polarity: ClaimPolarity;
  modality: ClaimModality;
  attribution: EvidenceRetrievalAttribution;
  rhetoricalRole: EvidenceRetrievalRhetoricalRole;
  scope: Scope;
  evidenceAnchors: CompactEvidenceAnchor[];
  explicitRelations: ExplicitClaimRelation[];
  shortestEvidencePath: ShortestEvidencePath;
  pathQuality: number;
  extractorPromptVersion: string;
}

/** Completion and freshness identity for one snapshot, including zero-Claim snapshots. */
export interface EvidenceRetrievalManifest {
  indexVersion: typeof EVIDENCE_RETRIEVAL_INDEX_VERSION;
  snapshotId: string;
  paperId: string;
  paperFamilyId: string;
  rootArtifactVersionId: string;
  rootArtifactSha256: string;
  graphSha256: string;
  graphSchemaVersion: string;
  extractorPromptVersion: string;
  documentCount: number;
  createdAt: string;
}

export interface EvidenceRetrievalAssessment {
  indexVersion: typeof EVIDENCE_RETRIEVAL_INDEX_VERSION;
  verificationId: string;
  snapshotId: string;
  assessmentId: string;
  claimId: string;
  scopedClaimId: string;
  verdict: AssessmentVerdict;
  confidence: number;
  evidenceIds: string[];
  rationale: string;
  limitations: string[];
  verifierModel: string;
  verifierPromptVersion: string;
  createdAt: string;
}

export interface EvidenceRetrievalProjection {
  manifest: EvidenceRetrievalManifest;
  documents: EvidenceRetrievalDocument[];
}

export interface EvidenceRetrievalSnapshotBundle {
  manifest: EvidenceRetrievalManifest;
  documents: EvidenceRetrievalDocument[];
  assessments: EvidenceRetrievalAssessment[];
}

/** D1-to-Compiler synchronization payload; verifier assessments stay in D1. */
export type EvidenceRetrievalIndexBundle = Pick<
  EvidenceRetrievalSnapshotBundle,
  "manifest" | "documents"
>;

export interface EvidenceRetrievalProjectionMetadata {
  snapshotId: string;
  rootArtifactVersionId: string;
  graphSha256: string;
  extractorPromptVersion: string;
  createdAt: string;
}

export interface EvidenceRetrievalAssessmentMetadata {
  verificationId: string;
  verifierPromptVersion: string;
  createdAt: string;
}

export interface QueryClaimRef {
  snapshotId: string;
  claimId: string;
}

export type EvidenceSearchIntent = "claim_search" | "supporting_evidence" | "contradiction_search";
export type EvidenceSearchStage = "preview" | "ai_rerank";
export type SearchScope = Partial<Scope>;
export type OpenAlexTaxonomyScope = {
  level: "domain" | "field" | "subfield" | "topic";
  id: string;
};

export interface EvidenceSearchRequest {
  /** The browser protocol; omitted only by internal 2.0/2.1 callers (synthesis, knowledge graph). */
  searchProtocol?: typeof EVIDENCE_RETRIEVAL_CONTEXT_PROTOCOL_VERSION;
  clarificationSelection?: { clarificationToken: string; optionId: string };
  conversationHistory?: ConversationTurn[];
  contextSubmission?: { contextToken: string; content: string };
  stage: EvidenceSearchStage;
  query: string | null;
  queryClaim: QueryClaimRef | null;
  candidateRefs: QueryClaimRef[];
  /** Server-issued preview plan binding. Null keeps legacy final requests compatible. */
  queryPlanHandoff: string | null;
  intent: EvidenceSearchIntent;
  corpus: "same_paper" | "cross_paper";
  paperIds: string[];
  paperFamilyIds: string[];
  openAlexScope: OpenAlexTaxonomyScope | null;
  mustTerms: string[];
  excludeTerms: string[];
  scope: SearchScope | null;
  claimTypes: ClaimType[];
  polarity: ClaimPolarity | null;
  limit: number;
}

export interface ResolvedEvidenceSearchCorpus {
  request: EvidenceSearchRequest;
  queryText: string;
  queryDocument: EvidenceRetrievalDocument | null;
  manifests: EvidenceRetrievalManifest[];
}

/* Exact snake_case HTTP DTOs shared with the local retrieval service. */

export interface EvidencePathLinkDto {
  from_id: string;
  to_id: string;
  relation: string;
  link_type: "edge" | "provenance";
  confidence: number | null;
}

export interface ShortestEvidencePathDto {
  root_claim_id: string;
  nodes: EvidencePathNode[];
  links: EvidencePathLinkDto[];
  depth: number;
  mean_edge_confidence: number | null;
}

export interface CompactEvidenceAnchorDto {
  anchor_id: string;
  artifact_version_id: string;
  anchor_type: CompactEvidenceAnchor["anchorType"];
  page: number | null;
  section: string | null;
  paragraph: number | null;
  quote: string | null;
  figure: string | null;
  table: string | null;
  row_start: number | null;
  row_end: number | null;
  row_range_label: string | null;
  column_names: string[];
  series: string | null;
  char_start: number | null;
  char_end: number | null;
  region: AnchorRegion | null;
  region_label: string | null;
  dataset_path: string | null;
}

export interface EvidenceRetrievalDocumentDto {
  index_version: typeof EVIDENCE_RETRIEVAL_INDEX_VERSION;
  snapshot_id: string;
  paper_id: string;
  paper_family_id: string;
  root_artifact_version_id: string;
  claim_id: string;
  source_claim_id: string | null;
  original_claim: string | null;
  canonical_claim: string;
  proposition_key: string;
  scoped_claim_id: string;
  paper_title: string;
  evidence_context: string;
  entities: string[];
  key_terms: string[];
  abbreviations: Array<{ short_form: string; long_form: string | null }>;
  qualifiers: string[];
  claim_type: ClaimType;
  polarity: ClaimPolarity;
  modality: ClaimModality;
  attribution: EvidenceRetrievalAttribution;
  rhetorical_role: EvidenceRetrievalRhetoricalRole;
  scope: Scope;
  evidence_anchors: CompactEvidenceAnchorDto[];
  explicit_relations: Array<{
    edge_id: string;
    source_claim_id: string;
    target_claim_id: string;
    other_claim_id: string;
    relation: ExplicitClaimRelationType;
    direction: "outgoing" | "incoming";
    confidence: number | null;
    scope_overlap: ScopeOverlap;
  }>;
  shortest_evidence_path: ShortestEvidencePathDto;
  path_quality: number;
  extractor_prompt_version: string;
}

export interface EvidenceRetrievalManifestDto {
  index_version: typeof EVIDENCE_RETRIEVAL_INDEX_VERSION;
  snapshot_id: string;
  paper_id: string;
  paper_family_id: string;
  root_artifact_version_id: string;
  root_artifact_sha256: string;
  graph_sha256: string;
  graph_schema_version: string;
  extractor_prompt_version: string;
  document_count: number;
  created_at: string;
}

export interface EvidenceRetrievalAssessmentDto {
  index_version: typeof EVIDENCE_RETRIEVAL_INDEX_VERSION;
  verification_id: string;
  snapshot_id: string;
  assessment_id: string;
  claim_id: string;
  scoped_claim_id: string;
  verdict: AssessmentVerdict;
  confidence: number;
  evidence_ids: string[];
  rationale: string;
  limitations: string[];
  verifier_model: string;
  verifier_prompt_version: string;
  created_at: string;
}

export interface EvidenceRetrievalIndexRequestDto {
  schema_version: typeof EVIDENCE_RETRIEVAL_INDEX_VERSION;
  project_id: string;
  manifest: EvidenceRetrievalManifestDto;
  documents: EvidenceRetrievalDocumentDto[];
}

export interface EvidenceRetrievalManifestCheckRequestDto {
  schema_version: typeof EVIDENCE_RETRIEVAL_INDEX_VERSION;
  project_id: string;
  manifests: EvidenceRetrievalManifestDto[];
}

export interface EvidenceRetrievalSearchRequestDto {
  argument_contexts?: import("./arguments.ts").RetrievalArgumentContext[];
  schema_version: EvidenceRetrievalSearchProtocolVersion;
  project_id: string;
  stage: EvidenceSearchStage;
  query: string;
  query_claim: { snapshot_id: string; claim_id: string } | null;
  candidate_refs: Array<{ snapshot_id: string; claim_id: string }>;
  query_plan_handoff?: string;
  clarification_selection?: { clarification_token: string; option_id: string };
  conversation_history?: ConversationTurn[];
  context_submission?: { context_token: string; content: string };
  intent: EvidenceSearchIntent;
  must_terms: string[];
  exclude_terms: string[];
  scope: SearchScope | null;
  claim_types: ClaimType[];
  polarity: ClaimPolarity | null;
  filters: {
    corpus: "same_paper" | "cross_paper";
    openalex_scope?: OpenAlexTaxonomyScope;
    paper_ids: string[];
    paper_family_ids: string[];
    active_snapshot_ids: string[] | `sha256-v1:${string}` | null;
    limit: number;
  };
}

export type RetrievalManifestCheckStatus = "indexed" | "missing" | "stale" | "degraded";
export type RetrievalManifestIndexStatus = "indexed" | "unchanged" | "degraded";
export type RetrievalManifestStatus = RetrievalManifestCheckStatus | RetrievalManifestIndexStatus;

export interface RetrievalManifestState<Status extends RetrievalManifestStatus> {
  snapshot_id: string;
  status: Status;
  indexed_document_count: number;
  document_count: number;
  manifest_sha256: string | null;
  dense_complete: boolean;
}

export interface EvidenceRetrievalManifestCheckResponseDto {
  schema_version: typeof EVIDENCE_RETRIEVAL_INDEX_VERSION;
  manifests: RetrievalManifestState<RetrievalManifestCheckStatus>[];
}

export interface EvidenceRetrievalIndexResponseDto {
  schema_version: typeof EVIDENCE_RETRIEVAL_INDEX_VERSION;
  manifests: RetrievalManifestState<RetrievalManifestIndexStatus>[];
  indexed_documents: number;
  warnings: string[];
}

export interface RetrievalStageDiagnostics {
  attempted: boolean;
  status: "ok" | "degraded" | "skipped";
  provider: string;
  returned: number;
}

/** Operational retrieval state; it is not a scientific truth judgment. */
export type RetrievalResultStatus = "matches" | "no_direct_answer" | "degraded" | "clarification_required" | "needs_context";

export type RetrievalStrongSignal =
  | "exact_anchor"
  | "concept_anchor"
  | "scientific_anchor";

export type RetrievalPolicyInapplicableReason =
  | "explicit_constraints"
  | "query_claim"
  | "restricted_corpus"
  | "no_active_snapshots"
  | "lexical_channel_unavailable"
  | "dense_channel_unavailable"
  | "invalid_dense_scores";

/** Observe-only retrieval telemetry; it is neither a probability nor a live decision. */
export interface RetrievalConfidence {
  mode: "observe";
  decision: "return";
  would_abstain: null;
  applicability: "applicable" | "policy_inapplicable";
  policy_inapplicable_reasons: RetrievalPolicyInapplicableReason[];
  query_profile: "topic" | "claim";
  long_query_truncated: boolean;
  direct_lexical_query_tokens: number;
  direct_lexical_overlap: number;
  direct_lexical_coverage: number;
  exact_anchor_hits: number;
  concept_anchor_hits: number;
  scientific_anchor_hits: number;
  strong_signal_types: RetrievalStrongSignal[];
  dense_observed_hits: number;
  dense_top1: number | null;
  dense_top2: number | null;
  dense_margin: number | null;
  dense_top3_mean: number | null;
}

export type RetrievalRelation =
  | "equivalent"
  | "supports"
  | "contradicts"
  | "qualifies"
  | "refines"
  | "supplements"
  | "related"
  | "unrelated";

export type RetrievalScopeRelation =
  | "equivalent"
  | "overlap"
  | "query_within_candidate"
  | "candidate_within_query"
  | "disjoint"
  | "unknown";

export interface RetrievalMemberRef {
  claim_id: string;
  snapshot_id: string;
  paper_id: string;
  paper_family_id: string;
  relation: RetrievalRelation;
  relation_direction: "query_to_result" | "result_to_query";
  relation_confidence: number | null;
  scope_relation: RetrievalScopeRelation;
  scores: {
    lexical: number | null;
    dense: number | null;
    rrf: number;
    rerank: number | null;
  };
}

export interface RetrievalPreviewRef {
  claim_id: string;
  snapshot_id: string;
  paper_id: string;
  paper_family_id: string;
  scores: RetrievalMemberRef["scores"];
}

export interface RetrievalQueryPlan {
  query_type: "paper_lookup" | "topic_search" | "claim_search" | "supporting_evidence" | "contradiction_search";
  original_query: string;
  /** 2.2 only: the signed user-selected interpretation, without replacing the original query. */
  resolved_query?: string;
  canonical_claim: string;
  must_terms: string[];
  optional_terms: string[];
  exclude_terms: string[];
  entities: string[];
  polarity: ClaimPolarity | null;
  claim_types: ClaimType[];
  scope_constraints: SearchScope | null;
  planner: "gpt" | "deterministic";
}

export interface RetrievalCluster {
  cluster_id: string;
  relation: RetrievalRelation;
  representative: RetrievalMemberRef;
  member_refs: RetrievalMemberRef[];
  distinct_paper_families: number;
}

export interface EvidenceRetrievalResolvedSearchResponseDto {
  schema_version: EvidenceRetrievalSearchProtocolVersion;
  stage: EvidenceSearchStage;
  result_status: Exclude<RetrievalResultStatus, "clarification_required" | "needs_context">;
  query_plan: RetrievalQueryPlan;
  query_plan_handoff?: string;
  channels: {
    lexical: RetrievalStageDiagnostics;
    dense: RetrievalStageDiagnostics;
    rerank: RetrievalStageDiagnostics;
    relation: RetrievalStageDiagnostics;
  };
  preview: RetrievalPreviewRef[];
  clusters: RetrievalCluster[];
  warnings: string[];
  retrieval_confidence: RetrievalConfidence | null;
}

export interface RetrievalClarification {
  question: string;
  options: Array<{ option_id: string; label: string; resolved_query: string }>;
  clarification_token: string;
}

export type EvidenceRetrievalClarificationResponseDto = Omit<
  EvidenceRetrievalResolvedSearchResponseDto,
  "schema_version" | "stage" | "result_status" | "query_plan" | "query_plan_handoff" | "retrieval_confidence"
> & {
  schema_version: typeof EVIDENCE_RETRIEVAL_CONTEXT_PROTOCOL_VERSION;
  stage: "preview";
  result_status: "clarification_required";
  query_plan: null;
  clarification: RetrievalClarification;
  query_plan_handoff?: never;
  retrieval_confidence: null;
};

export type EvidenceRetrievalSearchResponseDto =
  | EvidenceRetrievalResolvedSearchResponseDto
  | EvidenceRetrievalClarificationResponseDto
  | EvidenceRetrievalNeedsContextResponseDto;

export type EvidenceRetrievalNeedsContextResponseDto = Omit<
  EvidenceRetrievalClarificationResponseDto, "schema_version" | "result_status" | "clarification"
> & {
  schema_version: typeof EVIDENCE_RETRIEVAL_CONTEXT_PROTOCOL_VERSION;
  result_status: "needs_context";
  needs_context: { question: string; context_token: string };
};

export type RetrievalSearchResponse = EvidenceRetrievalSearchResponseDto;
export type RetrievalIndexResponse = EvidenceRetrievalIndexResponseDto;

export interface EvidenceRetrievalDeleteSnapshotsRequestDto {
  schema_version: typeof EVIDENCE_RETRIEVAL_INDEX_VERSION;
  index_version: typeof EVIDENCE_RETRIEVAL_INDEX_VERSION;
  project_id: string;
  snapshot_ids: string[];
}

export interface EvidenceRetrievalDeleteSnapshotsResponseDto {
  schema_version: typeof EVIDENCE_RETRIEVAL_INDEX_VERSION;
  deleted_snapshots: number;
  deleted_documents: number;
  warnings: string[];
}

export type RetrievalDeleteResponse = EvidenceRetrievalDeleteSnapshotsResponseDto;
