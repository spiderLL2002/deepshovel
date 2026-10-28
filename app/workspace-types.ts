import type { ArtifactVersion, EvidenceGraph, EvidencePath, Scope, SourceAnchor, StructuredVisualEvidence } from "@/lib/evidence-graph";
import type { DocumentExtraction } from "@/lib/document-extraction";

export type ArtifactVersionDto = {
  id: string;
  artifactId: string;
  versionNumber: number;
  sha256: string;
  locator: string;
  mediaType: string;
  fileName: string;
  byteSize: number;
  downloadUrl: string;
  createdAt: string;
};

export type ReviewDto = {
  id: string;
  snapshotId?: string;
  decision: "accepted" | "rejected" | "needs_revision" | "withdrawn";
  rationale: string;
  reviewer: string;
  createdAt: string;
};

export type SnapshotSummaryDto = {
  id: string;
  title?: string;
  rootArtifactVersionId: string;
  schemaVersion: string;
  graphSha256: string;
  counts: Record<string, number>;
  extraction: { runId: string; model: string; promptVersion: string };
  machineVerification: { status: "assessed" | "unassessed"; runCount: number; limitationCount: number };
  latestReview: ReviewDto | null;
  detailUrl: string;
  createdAt: string;
};

export type SnapshotDetailDto = {
  snapshot: SnapshotSummaryDto;
  graph: EvidenceGraph;
  paths: EvidencePath[];
  reviews: ReviewDto[];
  verifications: Array<{
    id: string;
    runId: string;
    model: string;
    promptVersion: string;
    reportSha256: string;
    report: unknown;
    createdAt: string;
  }>;
};

export type VisualPageDto = {
  snapshotId: string;
  rootArtifactVersionId: string;
  sourceTitle: string | null;
  sourceVersion: ArtifactVersion | null;
  records: Array<{
    anchor: SourceAnchor;
    kind: "figure" | "table";
    label: string;
    visual: StructuredVisualEvidence | null;
  }>;
  counts: { all: number; figure: number; table: number };
  pageInfo: { page: number; pageSize: number; total: number; totalPages: number; hasMore: boolean };
};

export type DocumentExtractionPageDto = {
  ok: true;
  snapshotId: string;
  rootArtifactVersionId: string;
  sourceTitle: string | null;
  sourceVersion: ArtifactVersion | null;
  extraction: DocumentExtraction;
};

type DocumentExtractionSectionBase = Omit<DocumentExtractionPageDto, "extraction">;

export type DocumentMetadataPageDto = DocumentExtractionSectionBase & {
  section: "metadata";
  extraction: Omit<DocumentExtraction, "markdown" | "visuals">;
};

export type DocumentTextPageDto = DocumentExtractionSectionBase & {
  section: "text";
  markdown: string;
};

export type DocumentVisualPageDto = DocumentExtractionSectionBase & {
  section: "visuals";
  kind: "all" | "figure" | "table";
  records: DocumentExtraction["visuals"];
  counts: { all: number; figure: number; table: number };
  pageInfo: { page: number; pageSize: number; total: number; totalPages: number; hasMore: boolean };
};

export type CitationReceptionStatus =
  | "not_started"
  | "processing"
  | "ready"
  | "unavailable"
  | "failed";

export type CitationReceptionSummaryDto = {
  totalAssertions: number;
  distinctCitingPaperFamilies: number;
  independentReplications: number;
  independentConsistentFindings: number;
  reanalysesOrSyntheses: number;
  verbalEndorsements: number;
  qualifications: number;
  contradictions: number;
  needsReview: number;
};

export type CitationReceptionEvidencePreviewDto = {
  statement: string;
  source: {
    artifactName: string;
    artifactVersionId: string;
    mediaType: string;
    sha256: string;
    anchor: SourceAnchor;
  };
};

export type CitationReceptionItemDto = {
  id: string;
  target: {
    entityId: string;
    entityKind:
      | "atomic_claim"
      | "observation"
      | "reasoning"
      | "structured_visual_evidence"
      | "source_anchor";
    rootClaimIds: string[];
    statement: string;
    alignmentConfidence: number;
  };
  citingPaper: {
    snapshotId: string;
    paperId: string;
    paperFamilyId: string;
    title: string;
    authors: string[];
    year: number | null;
  };
  attributedProposition: string;
  marker: string | null;
  mentionCount: number;
  citationContext: CitationReceptionEvidencePreviewDto;
  confirmingEvidence: CitationReceptionEvidencePreviewDto | null;
  assessment: {
    intent: string[];
    stance: "supports" | "contradicts" | "qualifies" | "unclear";
    confirmationMode:
      | "verbal_endorsement"
      | "verbal_critique"
      | "consistent_with_citing_evidence"
      | "contradicted_by_citing_evidence"
      | "qualified_by_citing_evidence"
      | "direct_replication"
      | "reanalysis"
      | "meta_analytic_synthesis";
    independence:
      | "independent"
      | "shared_authors"
      | "shared_data"
      | "derived_data"
      | "unknown";
    faithfulness:
      | "faithful"
      | "partial"
      | "overgeneralized"
      | "contradicted_by_target"
      | "not_found"
      | "ambiguous"
      | "indirect";
    confidence: number;
    limitationCount: number;
  };
};

export type CitationReceptionDto = {
  ok: true;
  targetSnapshotId: string;
  status: CitationReceptionStatus;
  coverage: {
    eligibleActivePapers: number;
    analyzedActivePapers: number;
    complete: boolean;
    asOf: string;
  };
  summary: CitationReceptionSummaryDto;
  items: CitationReceptionItemDto[];
  total: number;
  truncated: boolean;
  warnings: string[];
};

export type Activity = {
  id: string;
  label: string;
  state: "working" | "done" | "failed" | "cancelled";
  detail: string;
  kind?: "upload" | "compile" | "claim-analysis";
  artifactId?: string;
  artifactVersionId?: string;
  jobId?: string;
  sourceUpdatedAt?: string;
  cancellable?: boolean;
  stage?: "uploading" | "queued" | "parsing" | "extracting" | "validating" | "importing";
  progress?: number;
};

export type CompileStage = "queued" | "parsing" | "extracting" | "validating" | "importing" | "completed" | "failed";

export type CompileJobDto = {
  id: string;
  artifactVersionId: string;
  fileName?: string;
  status: "queued" | "running" | "completed" | "failed";
  stage: CompileStage;
  progress: number;
  createdAt: string;
  updatedAt: string;
  timings?: Partial<Record<Exclude<CompileStage, "failed">, number>>;
  snapshotId?: string | null;
  error?: { code: string; message: string; retryable?: boolean } | null;
};

export type LibraryArtifactDto = {
  artifactId: string;
  title: string;
  artifactType: string;
  libraryStatus: "pending" | "processing" | "completed" | "failed";
  versionCount: number;
  latestVersion: ArtifactVersionDto | null;
  compile: CompileJobDto | null;
  snapshot: {
    id: string;
    counts: Record<string, number>;
    createdAt: string;
  } | null;
  machineVerification: {
    status: "assessed" | "unassessed";
    runCount: number;
    limitationCount: number;
  };
  humanReview: ReviewDto | null;
  updatedAt: string;
};

export type DataQualityCheckDto = {
  rule_id: string;
  rule_name: string;
  category: string;
  object_name: string;
  execution_status: "executed" | "not_run" | "not_applicable" | "error";
  conclusion: "pass" | "warning" | "high_warning" | "no_conclusion";
  severity: "info" | "warning" | "high";
  evidence: string;
  criterion: string;
};

export type DataQualityReportDto = {
  schema_version: "data-quality-report/1.0";
  artifact_version_id: string;
  run_id: string;
  generated_at: string;
  summary: {
    paper_table_count: number;
    dataset_count: number;
    check_count: number;
    pass: number;
    warning: number;
    high_warning: number;
    no_conclusion: number;
  };
  paper_tables: Array<{
    name: string;
    page: number | null;
    source: string;
    row_count: number;
    column_count: number;
    region: { x: number; y: number; width: number; height: number; unit: "normalized" } | null;
    columns: string[];
    rows: string[][];
    preview_truncated: boolean;
    checks: DataQualityCheckDto[];
  }>;
  datasets: Array<{
    title: string;
    locator: string;
    doi: string | null;
    repository: string;
    accessible: boolean | null;
    access_evidence: string;
    source_evidence: string;
    checks: DataQualityCheckDto[];
  }>;
  warnings: string[];
};

export type DataQualityJobDto = {
  id: string;
  artifactVersionId: string;
  status: "queued" | "running" | "completed" | "failed";
  stage: "queued" | "parsing" | "checking" | "completed" | "failed";
  progress: number;
  createdAt: string;
  updatedAt: string;
  error: { code: string; message: string; retryable: boolean } | null;
};

export type EvidenceSynthesisNormalizedClaimDto = {
  id: string;
  originSpan: string;
  canonicalClaim: string;
  claimType: string;
  polarity: string;
  modality: string;
  scope: Scope;
  status: string;
  clarification: string | null;
};

export type EvidenceSynthesisNormalizeResponse = {
  ok: true;
  action: "normalize";
  normalization: {
    status: string;
    clarification: string | null;
    claims: EvidenceSynthesisNormalizedClaimDto[];
  };
};

export type EvidenceSynthesisScopeAlignmentDto = {
  relation: string;
};

export type EvidenceSynthesisAnchorDto = {
  id: string;
  page: number | null;
  section: string | null;
  quote: string | null;
  figure: string | null;
  table: string | null;
  regionLabel: string | null;
};

export type EvidenceSynthesisPathDto = {
  nodes: Array<{ id: string; kind: string; label: string }>;
  links: Array<{ relation: string }>;
  truncated: boolean;
};

export type EvidenceSynthesisEvidenceItemDto = {
  id: string;
  verdict: string;
  relation: string;
  direction: string | null;
  stance: string;
  directness: string;
  candidateClaim: {
    snapshotId: string;
    claimId: string;
    canonicalClaim: string;
    scope: Scope;
  };
  scopeAlignment: EvidenceSynthesisScopeAlignmentDto;
  rationale: string | null;
  limitations: string[];
  paper: {
    paperFamilyId: string;
    title: string;
  };
  candidateSource: {
    mediaType: string;
    downloadUrl: string;
  };
  exactAnchors: EvidenceSynthesisAnchorDto[];
  shortestEvidencePath: EvidenceSynthesisPathDto;
  selectedEvidence: {
    query: Array<{ id: string; kind: string; text: string; anchorIds: string[] }>;
    candidate: Array<{ id: string; kind: string; text: string; anchorIds: string[] }>;
  };
};

export type EvidenceSynthesisCoverageDto = {
  activePaperFamilies: number;
  activeClaims: number;
  retrievedCandidates: number;
  comparedCandidates: number;
  excludedSourceFamily: number;
  acceptedRelations: number;
  retrievalLimit: number;
  complete: boolean;
  asOf: string;
};

export type EvidenceSynthesisCompareResponse = {
  ok: true;
  action: "compare";
  sourceClaim: {
    id: string;
    canonicalClaim: string;
    scope: Scope;
  };
  coverage: EvidenceSynthesisCoverageDto;
  groups: {
    support: EvidenceSynthesisEvidenceItemDto[];
    contradict: EvidenceSynthesisEvidenceItemDto[];
    qualify: EvidenceSynthesisEvidenceItemDto[];
    context: EvidenceSynthesisEvidenceItemDto[];
  };
  warnings: string[];
};

export type EvidenceSearchRelation =
  | "equivalent"
  | "supports"
  | "contradicts"
  | "qualifies"
  | "refines"
  | "supplements"
  | "related";

export type EvidenceSearchIntent = "claim_search" | "supporting_evidence" | "contradiction_search";
export type EvidenceSearchRelationGroup = "related" | "equivalent" | "support" | "contradict" | "qualify" | "supplement";

/** Only an unfiltered completed listing represents the full synthesis corpus. */
export function isUnfilteredCompletedLibrary(status: string, query: string): boolean {
  return status === "completed" && query.length === 0;
}

/** Preserve the ranking intent that produced a response when arranging its relation sections. */
export function evidenceSearchRelationGroupOrder(intent: EvidenceSearchIntent): EvidenceSearchRelationGroup[] {
  if (intent === "contradiction_search") {
    return ["contradict", "equivalent", "qualify", "support", "supplement", "related"];
  }
  if (intent === "supporting_evidence") {
    return ["support", "equivalent", "qualify", "contradict", "supplement", "related"];
  }
  return ["equivalent", "support", "contradict", "qualify", "supplement", "related"];
}

export type EvidenceSearchMember = {
  snapshotId: string;
  paperId: string;
  paperFamilyId: string;
  source: {
    artifactId: string;
    artifactName: string;
    artifactVersionId: string;
    versionNumber: number;
    mediaType: string;
    downloadUrl: string;
  };
  claim: {
    id: string;
    originalClaim: string | null;
    originalClaimTruncated: boolean;
    canonicalClaim: string;
    propositionKey: string;
    scopedClaimId: string;
    claimType: string;
    polarity: string;
    modality: string;
    attribution: string;
    rhetoricalRole: string;
    scope: EvidenceGraph["claims"][number]["scope"];
  };
  queryRelation: {
    relation: EvidenceSearchRelation;
    confidence: number | null;
    direction: "query_to_result" | "result_to_query";
    scopeRelation: "equivalent" | "overlap" | "query_within_candidate" | "candidate_within_query" | "disjoint" | "unknown";
  };
  scores: {
    lexical: number | null;
    dense: number | null;
    rrf: number;
    rerank: number | null;
  };
  evidenceAnchors: Array<{
    id: string;
    page: number | null;
    section: string | null;
    quote: string | null;
    figure: string | null;
    table: string | null;
    regionLabel: string | null;
  }>;
  evidenceAnchorsTruncated: boolean;
  shortestEvidencePath: {
    rootClaimId: string;
    nodes: Array<{ id: string; kind: string; label: string }>;
    links: Array<{ fromId: string; toId: string; relation: string; linkType: "edge" | "provenance"; confidence: number | null }>;
    depth: number;
    meanEdgeConfidence: number | null;
    truncated: boolean;
  };
  assessment: {
    verdict: string;
    confidence: number;
    evidenceCount: number;
    limitationCount: number;
  } | null;
};

export type EvidenceSearchCluster = {
  id: string;
  relation: EvidenceSearchRelation;
  distinctPaperFamilies: number;
  representative: EvidenceSearchMember;
  members: EvidenceSearchMember[];
};

export type EvidenceSearchPreviewMember = Omit<EvidenceSearchMember, "queryRelation">;

export type EvidenceSearchResolvedResponse = {
  ok: true;
  stage: "preview" | "ai_rerank";
  /** Operational retrieval state, not a scientific truth or completeness claim. */
  resultStatus: "matches" | "no_direct_answer" | "degraded";
  query: {
    text: string;
    conversationHistory?: Array<{ role: "user" | "assistant"; content: string }>;
    queryClaim: { snapshotId: string; claimId: string } | null;
    intent: EvidenceSearchIntent;
    corpus: "same_paper" | "cross_paper";
    paperIds: string[];
    paperFamilyIds: string[];
    scope: Partial<EvidenceGraph["claims"][number]["scope"]> | null;
    mustTerms: string[];
    excludeTerms: string[];
    claimTypes: string[];
    polarity: string | null;
  };
  queryPlan: {
    queryType: "paper_lookup" | "topic_search" | "claim_search" | "supporting_evidence" | "contradiction_search";
    originalQuery: string;
    resolvedQuery?: string;
    canonicalClaim: string;
    mustTerms: string[];
    optionalTerms: string[];
    excludeTerms: string[];
    entities: string[];
    polarity: string | null;
    claimTypes: string[];
    scopeConstraints: Partial<EvidenceGraph["claims"][number]["scope"]> | null;
    planner: "gpt" | "deterministic";
  } | null;
  /** Server-issued preview→final plan binding; transport integrity is not scientific validation. */
  queryPlanHandoff: string | null;
  channels: Record<"lexical" | "dense" | "rerank" | "relation", {
    attempted: boolean;
    status: "ok" | "degraded" | "skipped";
    provider: string;
    returned: number;
  }>;
  /** Observe-only diagnostics for calibration; never suppresses or reorders results. */
  retrievalConfidence: {
    mode: "observe";
    decision: "return";
    wouldAbstain: null;
    applicability: "applicable" | "policy_inapplicable";
    policyInapplicableReasons: Array<
      | "explicit_constraints"
      | "query_claim"
      | "restricted_corpus"
      | "no_active_snapshots"
      | "lexical_channel_unavailable"
      | "dense_channel_unavailable"
      | "invalid_dense_scores"
    >;
    queryProfile: "topic" | "claim";
    longQueryTruncated: boolean;
    directLexicalQueryTokens: number;
    directLexicalOverlap: number;
    directLexicalCoverage: number;
    exactAnchorHits: number;
    conceptAnchorHits: number;
    scientificAnchorHits: number;
    strongSignalTypes: Array<"exact_anchor" | "concept_anchor" | "scientific_anchor">;
    denseObservedHits: number;
    denseTop1: number | null;
    denseTop2: number | null;
    denseMargin: number | null;
    denseTop3Mean: number | null;
  } | null;
  indexSync: {
    checkedSnapshots: number;
    /** Active papers whose retrieval index is still being built in the background. */
    pendingSnapshots: number;
    durationMs: number;
  };
  clusters: EvidenceSearchCluster[];
  previewResults: EvidenceSearchPreviewMember[];
  warnings: string[];
};

export type EvidenceSearchClarificationResponse = Omit<
  EvidenceSearchResolvedResponse, "stage" | "resultStatus" | "queryPlan" | "queryPlanHandoff" | "retrievalConfidence"
> & {
  stage: "preview";
  resultStatus: "clarification_required";
  queryPlan: null;
  queryPlanHandoff: null;
  retrievalConfidence: null;
  clarification: {
    question: string;
    options: Array<{ optionId: string; label: string; resolvedQuery: string }>;
    clarificationToken: string;
  };
};

export type EvidenceSearchNeedsContextResponse = Omit<EvidenceSearchClarificationResponse, "resultStatus" | "clarification"> & {
  resultStatus: "needs_context";
  needsContext: { question: string; contextToken: string };
};

export type EvidenceSearchResponse = EvidenceSearchResolvedResponse | EvidenceSearchClarificationResponse | EvidenceSearchNeedsContextResponse;

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

export async function readJson(response: Response) {
  try { return await response.json() as unknown; } catch { return {}; }
}

const API_ERROR_MESSAGES: Record<string, string> = {
  UPSTREAM_RETRIEVAL_GPT_TIMEOUT: "模型服务返回超时，本次未生成结果，请重试。",
  UPSTREAM_RETRIEVAL_MODEL_OUTPUT_INVALID: "模型未返回可用的分析结果，请重试。",
  RETRIEVAL_RELATION_UNAVAILABLE: "模型关系分析未完成，请重试。",
  RETRIEVAL_PLANNING_OUTPUT_INVALID: "模型生成的检索计划未通过校验，尚未开始检索文献。请重试；若持续失败，请联系管理员。",
  RETRIEVAL_PLANNING_TIMEOUT: "生成检索计划超时，检索尚未开始。请稍后重新尝试。",
  RETRIEVAL_PLANNING_UPSTREAM_FAILED: "检索规划服务请求失败，检索尚未开始。请稍后重新尝试。",
  RETRIEVAL_PLANNING_UNAVAILABLE: "检索规划服务当前不可用。请联系管理员检查服务配置。",
  RETRIEVAL_PLANNING_PROVIDER_INVALID: "检索规划服务返回了无法使用的响应，检索尚未开始。请稍后重新尝试。",
  RETRIEVAL_PLANNING_INPUT_TOO_LARGE: "检索规划输入超出传输限制。请精简问题后重新检索，保留必要的研究条件。",
  RETRIEVAL_PLANNING_BUDGET_EXCEEDED: "生成检索计划时已达到单次资源限制，检索尚未开始。请联系管理员检查。",
  RETRIEVAL_PLANNING_CAPABILITY_UNAVAILABLE: "当前提供的材料无法支持这项操作。请补充所需原文或调整检索问题。",
  RETRIEVAL_PLANNING_INTERNAL_ERROR: "检索规划发生内部错误，检索尚未开始。请联系管理员检查。",
  CLARIFICATION_REPREVIEW_REQUIRED: "澄清选项已失效。请修改原问题后重新检索，生成新选项后再选择。",
  CONTEXT_REPREVIEW_REQUIRED: "本次补充上下文已失效。请修改原问题，将必要补充合并后重新检索。",
  QUERY_PLAN_HANDOFF_REPREVIEW_REQUIRED: "本次检索预览已失效，请重新检索。",
  STORED_SNAPSHOT_INCOMPATIBLE: "这份旧结果与当前版本不兼容。原始文件仍完整保存在文献库中，请点击“重新 Compile”。",
  STORE_BUSY: "证据库当前繁忙，请稍后重试。",
  STORE_INITIALIZING: "证据库正在初始化，请稍后重试。",
  STORE_UNAVAILABLE: "证据库暂时不可用，请稍后重试。",
};

export function responseError(value: unknown, fallback: string) {
  const error = asRecord(asRecord(value).error);
  if (typeof error.code === "string" && API_ERROR_MESSAGES[error.code]) return API_ERROR_MESSAGES[error.code];
  return typeof error.message === "string" ? error.message : fallback;
}

export function compileErrorMessage(
  code: string | undefined,
  fallback: string,
  timings?: CompileJobDto["timings"],
) {
  if (code === "COMPILER_UNAVAILABLE") {
    return "暂时无法连接分析服务。原始文件仍安全保存在文献库中，请稍后重试 Compile。";
  }
  if (code === "COMPILER_NOT_CONFIGURED") {
    return "分析服务尚未配置。原始文件仍安全保存，请联系维护人员。";
  }
  if (code === "MINERU_UNAVAILABLE") {
    return `文档解析服务不可用：${fallback}。原始文件仍安全保存，请稍后重试 Compile。`;
  }
  if (code === "MINERU_FAILED" || code === "MINERU_REQUEST_REJECTED") {
    return `文档解析在 3 次尝试后仍未完成：${fallback}。原始文件仍安全保存，可稍后重试 Compile。`;
  }
  if (code === "MODEL_OUTPUT_INVALID") {
    const phase = timings?.validating !== null && timings?.validating !== undefined ? "证据核验" : "证据提取";
    return `${phase}未完成。原始文件仍安全保存，可稍后重新 Compile。`;
  }
  if (code === "GPT_UNAVAILABLE") {
    return "证据提取服务暂时不可用，请稍后重试。";
  }
  if (code === "OUTPUT_CONTRACT_INVALID") {
    return "分析结果未通过完整性检查，因此没有写入证据库。原始文件仍安全保存。";
  }
  if (code === "COMPILE_CANCELLED" || code === "JOB_CANCELLED") {
    return "任务已终止。原始文件仍保存在文献库中。";
  }
  return fallback;
}

export function compileResponseError(value: unknown, fallback: string) {
  const error = asRecord(asRecord(value).error);
  const message = typeof error.message === "string" ? error.message : fallback;
  return compileErrorMessage(typeof error.code === "string" ? error.code : undefined, message);
}

export function shortHash(value: string) {
  return value ? `${value.slice(0, 10)}…${value.slice(-6)}` : "—";
}

export function formatBytes(value: number) {
  if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

export function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? value
    : new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

/** Review events use SQLite CURRENT_TIMESTAMP (UTC, without a timezone suffix). */
export function formatReviewDate(value: string) {
  const timestamp = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(value)
    ? `${value.replace(" ", "T")}Z`
    : value;
  return Number.isNaN(new Date(timestamp).valueOf()) ? value : formatDate(timestamp);
}
