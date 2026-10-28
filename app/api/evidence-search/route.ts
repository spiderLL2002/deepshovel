import {
  EVIDENCE_RETRIEVAL_CONTEXT_PROTOCOL_VERSION,
  EvidenceRetrievalStoreError,
  EvidenceSearchContractError,
  loadEvidenceRetrievalDocumentsByRefs,
  loadLatestEvidenceRetrievalAssessmentsByRefs,
  parseEvidenceSearchRequest,
  isValidContextToken,
  isValidContextClarificationToken,
  isValidQueryPlanHandoff,
  isRetrievalSessionId,
  resolveEvidenceRetrievalCorpus,
  toEvidenceRetrievalSearchRequestDto,
  type EvidenceRetrievalAssessment,
  type EvidenceRetrievalDocument,
  type EvidenceRetrievalClarificationResponseDto,
  type EvidenceRetrievalNeedsContextResponseDto,
  type EvidenceRetrievalResolvedSearchResponseDto,
  type EvidenceSearchRequest,
  type QueryClaimRef,
  type RetrievalMemberRef,
  type RetrievalPreviewRef,
  type RetrievalSearchResponse,
} from "@/lib/evidence-retrieval";
import { RetrievalClient } from "@/lib/server/retrieval-client";
import { quickSearchRefs } from "@/lib/server/quick-search";
import {
  createRetrievalSession,
  resolveRetrievalSession,
  type RetrievalSessionKind,
} from "@/lib/server/retrieval-sessions";
import {
  ApiError,
  getBindings,
  getCompilerConfiguration,
  PROJECT_ID,
  readJsonObject,
  safeError,
} from "@/lib/server/evidence-store";
import { appendPublicWarning } from "@/lib/server/public-warning";
import { streamedResponse, wantsStreamedResponse } from "@/lib/server/streamed-response";
import { ensureRetrievalCoverage } from "@/lib/server/retrieval-sync";
import {
  compactEvidencePath,
  emptyRetrievalChannels,
  evidenceRefKey,
  loadEvidenceSources,
  serviceEvidenceRefKey,
  truncateText,
  type EvidenceSourceRow,
} from "@/lib/server/retrieval-view";

const MAX_SEARCH_REQUEST_BYTES = 4 * 1024 * 1024;
const MAX_HYDRATED_MEMBERS = 200;
const MAX_PUBLIC_EVIDENCE_ANCHORS = 16;
const MAX_PUBLIC_ORIGINAL_CLAIM_CHARACTERS = 16_000;
const MAX_PUBLIC_PATH_NODES = 24;
const MAX_PUBLIC_PATH_LINKS = 48;
const MAX_PUBLIC_PATH_LABEL_CHARACTERS = 800;
/** Stays below the Worker/service 8 MiB transport boundary. */
const MAX_SEARCH_RESPONSE_BYTES = 7 * 1024 * 1024;
const QUERY_PLAN_HANDOFF_CORPUS_DRIFT_CODES = new Set([
  "QUERY_CLAIM_NOT_FOUND",
  "QUERY_PAPER_FILTER_MISMATCH",
  "QUERY_OPENALEX_FILTER_MISMATCH",
]);

function utf8Bytes(value: unknown) {
  return new TextEncoder().encode(JSON.stringify(value)).byteLength;
}

/** Representatives always survive; other members are admitted round-robin by cluster rank. */
function selectHydrationRefs(result: RetrievalSearchResponse) {
  const selected = new Map<string, QueryClaimRef>();
  for (const item of result.preview) {
    const ref = { snapshotId: item.snapshot_id, claimId: item.claim_id };
    selected.set(evidenceRefKey(ref), ref);
  }
  for (const cluster of result.clusters) {
    const ref = { snapshotId: cluster.representative.snapshot_id, claimId: cluster.representative.claim_id };
    selected.set(evidenceRefKey(ref), ref);
  }
  for (let memberIndex = 0; selected.size < MAX_HYDRATED_MEMBERS; memberIndex += 1) {
    let found = false;
    for (const cluster of result.clusters) {
      const member = cluster.member_refs[memberIndex];
      if (!member) continue;
      found = true;
      const ref = { snapshotId: member.snapshot_id, claimId: member.claim_id };
      selected.set(evidenceRefKey(ref), ref);
      if (selected.size >= MAX_HYDRATED_MEMBERS) break;
    }
    if (!found) break;
  }
  return [...selected.values()];
}

function hydrateMember(
  ref: RetrievalMemberRef | RetrievalPreviewRef,
  documents: Map<string, EvidenceRetrievalDocument>,
  assessments: Map<string, EvidenceRetrievalAssessment>,
  sources: Map<string, EvidenceSourceRow>,
) {
  const key = serviceEvidenceRefKey(ref);
  const document = documents.get(key);
  const source = sources.get(ref.snapshot_id);
  if (!document || !source) {
    throw new ApiError(502, "RETRIEVAL_REFERENCE_NOT_FOUND", "Retrieval service returned a reference absent from the immutable D1 projection");
  }
  if (document.paperId !== ref.paper_id || document.paperFamilyId !== ref.paper_family_id
    || source.artifactId !== ref.paper_family_id) {
    throw new ApiError(502, "RETRIEVAL_REFERENCE_MISMATCH", "Retrieval service returned inconsistent paper identity");
  }
  const assessment = assessments.get(key) ?? null;
  const originalClaimTruncated = document.originalClaim !== null
    && Array.from(document.originalClaim).length > MAX_PUBLIC_ORIGINAL_CLAIM_CHARACTERS;
  const anchors = document.evidenceAnchors.slice(0, MAX_PUBLIC_EVIDENCE_ANCHORS);
  return {
    snapshotId: document.snapshotId,
    paperId: document.paperId,
    paperFamilyId: document.paperFamilyId,
    source: {
      artifactId: source.artifactId,
      artifactName: source.artifactName,
      artifactVersionId: source.artifactVersionId,
      versionNumber: Number(source.versionNumber),
      mediaType: source.mediaType,
      downloadUrl: `/api/artifacts/${source.sha256}`,
    },
    claim: {
      id: document.claimId,
      originalClaim: document.originalClaim == null
        ? null
        : truncateText(document.originalClaim, MAX_PUBLIC_ORIGINAL_CLAIM_CHARACTERS),
      originalClaimTruncated,
      canonicalClaim: document.canonicalClaim,
      propositionKey: document.propositionKey,
      scopedClaimId: document.scopedClaimId,
      claimType: document.claimType,
      polarity: document.polarity,
      modality: document.modality,
      attribution: document.attribution,
      rhetoricalRole: document.rhetoricalRole,
      scope: document.scope,
    },
    ...("relation" in ref ? {
      queryRelation: {
        relation: ref.relation,
        confidence: ref.relation_confidence,
        direction: ref.relation_direction,
        scopeRelation: ref.scope_relation,
      },
    } : {}),
    scores: ref.scores,
    evidenceAnchors: anchors.map((anchor) => ({
      id: anchor.anchorId,
      page: anchor.page,
      section: anchor.section,
      quote: anchor.quote == null ? null : truncateText(anchor.quote, 2_000),
      figure: anchor.figure,
      table: anchor.table,
      regionLabel: anchor.regionLabel,
    })),
    evidenceAnchorsTruncated: document.evidenceAnchors.length > MAX_PUBLIC_EVIDENCE_ANCHORS
      || anchors.some((anchor) => anchor.quote !== null && Array.from(anchor.quote).length > 2_000),
    shortestEvidencePath: compactEvidencePath(document, {
      appendLabelEllipsis: true,
      maxLabelCharacters: MAX_PUBLIC_PATH_LABEL_CHARACTERS,
      maxLinks: MAX_PUBLIC_PATH_LINKS,
      maxNodes: MAX_PUBLIC_PATH_NODES,
      preservePathEnds: true,
      trackLabelTruncation: true,
    }),
    assessment: assessment == null ? null : {
      verdict: assessment.verdict,
      confidence: assessment.confidence,
      evidenceCount: assessment.evidenceIds.length,
      limitationCount: assessment.limitations.length,
    },
  };
}

function publicQueryPlan(result: RetrievalSearchResponse) {
  if (result.query_plan === null) {
    throw new ApiError(502, "INVALID_RETRIEVAL_RESPONSE", "Clarification does not contain a resolved query plan");
  }
  return {
    queryType: result.query_plan.query_type,
    originalQuery: result.query_plan.original_query,
    ...(result.query_plan.resolved_query === undefined ? {} : { resolvedQuery: result.query_plan.resolved_query }),
    canonicalClaim: result.query_plan.canonical_claim,
    mustTerms: result.query_plan.must_terms,
    optionalTerms: result.query_plan.optional_terms,
    excludeTerms: result.query_plan.exclude_terms,
    entities: result.query_plan.entities,
    polarity: result.query_plan.polarity,
    claimTypes: result.query_plan.claim_types,
    scopeConstraints: result.query_plan.scope_constraints,
    planner: result.query_plan.planner,
  };
}

function publicRetrievalConfidence(result: RetrievalSearchResponse) {
  const observation = result.retrieval_confidence;
  if (observation === null) return null;
  return {
    mode: observation.mode,
    decision: observation.decision,
    wouldAbstain: observation.would_abstain,
    applicability: observation.applicability,
    policyInapplicableReasons: observation.policy_inapplicable_reasons,
    queryProfile: observation.query_profile,
    longQueryTruncated: observation.long_query_truncated,
    directLexicalQueryTokens: observation.direct_lexical_query_tokens,
    directLexicalOverlap: observation.direct_lexical_overlap,
    directLexicalCoverage: observation.direct_lexical_coverage,
    exactAnchorHits: observation.exact_anchor_hits,
    conceptAnchorHits: observation.concept_anchor_hits,
    scientificAnchorHits: observation.scientific_anchor_hits,
    strongSignalTypes: observation.strong_signal_types,
    denseObservedHits: observation.dense_observed_hits,
    denseTop1: observation.dense_top1,
    denseTop2: observation.dense_top2,
    denseMargin: observation.dense_margin,
    denseTop3Mean: observation.dense_top3_mean,
  };
}

function boundedResponse(value: Record<string, unknown>) {
  const clusters = value.clusters as Array<{ members: unknown[] }>;
  let warningAdded = false;
  while (utf8Bytes(value) > MAX_SEARCH_RESPONSE_BYTES) {
    const target = clusters.findLast((cluster) => cluster.members.length > 0);
    if (!target) {
      throw new ApiError(502, "EVIDENCE_SEARCH_RESPONSE_TOO_LARGE", "Representative evidence exceeds the bounded search response");
    }
    target.members.pop();
    if (!warningAdded) {
      appendPublicWarning(value.warnings as string[], "部分结果尚未展开；每组观点的代表性证据均已保留。");
      warningAdded = true;
    }
  }
  return value;
}

function contextRepreviewRequired() {
  return new ApiError(409, "CONTEXT_REPREVIEW_REQUIRED", "The context is no longer valid; run preview again", { retryable: false });
}

function handoffRepreviewRequired() {
  return new ApiError(
    409,
    "QUERY_PLAN_HANDOFF_REPREVIEW_REQUIRED",
    "The retrieval preview is no longer valid; run preview again",
    { retryable: false },
  );
}

function clarificationRepreviewRequired() {
  return new ApiError(
    409,
    "CLARIFICATION_REPREVIEW_REQUIRED",
    "The clarification is no longer valid; run preview again",
    { retryable: false },
  );
}

function objectField(value: unknown) {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : null;
}

/** The three continuation slots a browser request may fill, read from the raw body. */
function browserContinuations(payload: Record<string, unknown>) {
  return [
    {
      slot: "query_plan_handoff",
      value: payload.query_plan_handoff,
      compilerToken: isValidQueryPlanHandoff,
      expired: handoffRepreviewRequired,
    },
    {
      slot: "context",
      value: objectField(payload.context_submission)?.context_token,
      compilerToken: isValidContextToken,
      expired: contextRepreviewRequired,
    },
    {
      slot: "clarification",
      value: objectField(payload.clarification_selection)?.clarification_token,
      compilerToken: isValidContextClarificationToken,
      expired: clarificationRepreviewRequired,
    },
  ];
}

const SESSION_TOKEN_SHAPES: Record<RetrievalSessionKind, (value: unknown) => boolean> = {
  query_plan_handoff: isValidQueryPlanHandoff,
  context: isValidContextToken,
  clarification: isValidContextClarificationToken,
};

/**
 * Swap the session IDs of a parsed browser request for the Compiler tokens
 * they stand for. A missing, expired or wrong-kind session re-previews.
 */
async function resolveBrowserSessions(DB: D1Database, request: EvidenceSearchRequest): Promise<EvidenceSearchRequest> {
  const lookups: Array<{ kind: RetrievalSessionKind; id: string; expired: () => ApiError }> = [];
  if (request.queryPlanHandoff !== null) {
    lookups.push({ kind: "query_plan_handoff", id: request.queryPlanHandoff, expired: handoffRepreviewRequired });
  }
  if (request.contextSubmission) {
    lookups.push({ kind: "context", id: request.contextSubmission.contextToken, expired: contextRepreviewRequired });
  }
  if (request.clarificationSelection) {
    lookups.push({
      kind: "clarification",
      id: request.clarificationSelection.clarificationToken,
      expired: clarificationRepreviewRequired,
    });
  }
  const tokens = new Map<RetrievalSessionKind, string>();
  const resolved = await Promise.all(lookups.map(({ kind, id }) => resolveRetrievalSession(DB, kind, id)));
  lookups.forEach(({ kind, expired }, index) => {
    const token = resolved[index];
    if (token === null || !SESSION_TOKEN_SHAPES[kind](token)) throw expired();
    tokens.set(kind, token);
  });
  return {
    ...request,
    queryPlanHandoff: tokens.get("query_plan_handoff") ?? null,
    ...(request.contextSubmission ? {
      contextSubmission: { ...request.contextSubmission, contextToken: tokens.get("context") as string },
    } : {}),
    ...(request.clarificationSelection ? {
      clarificationSelection: {
        ...request.clarificationSelection,
        clarificationToken: tokens.get("clarification") as string,
      },
    } : {}),
  };
}

type ResolvedCorpus = Awaited<ReturnType<typeof resolveEvidenceRetrievalCorpus>>;
type CoverageSync = Awaited<ReturnType<typeof ensureRetrievalCoverage>> & { durationMs: number };
type PublicQuery = ReturnType<typeof publicBaseQuery>;

const NO_STORE = { headers: { "cache-control": "private, no-store" } };

function cancelledError() {
  return new ApiError(408, "RETRIEVAL_REQUEST_CANCELLED", "Retrieval request was cancelled", { retryable: false });
}

function assertNotCancelled(signal: AbortSignal) {
  if (signal.aborted) throw cancelledError();
}

function publicBaseQuery(searchRequest: EvidenceSearchRequest, resolved: ResolvedCorpus) {
  return {
    text: resolved.queryText,
    queryClaim: searchRequest.queryClaim,
    intent: searchRequest.intent,
    corpus: searchRequest.corpus,
    paperIds: searchRequest.paperIds,
    paperFamilyIds: searchRequest.paperFamilyIds,
    scope: searchRequest.scope,
    mustTerms: searchRequest.mustTerms,
    excludeTerms: searchRequest.excludeTerms,
    claimTypes: searchRequest.claimTypes,
    polarity: searchRequest.polarity,
    ...(searchRequest.conversationHistory === undefined ? {} : { conversationHistory: searchRequest.conversationHistory }),
  };
}

function publicIndexSync(sync: CoverageSync) {
  return {
    checkedSnapshots: sync.checkedSnapshots,
    pendingSnapshots: sync.pendingSnapshots,
    durationMs: sync.durationMs,
  };
}

/** A corpus without searchable manifests: an explicit empty answer, no model work. */
function emptyCorpusResponse(searchRequest: EvidenceSearchRequest, resolved: ResolvedCorpus, query: PublicQuery) {
  return Response.json({
    ok: true,
    stage: searchRequest.stage,
    resultStatus: "no_direct_answer",
    query,
    queryPlan: {
      queryType: searchRequest.intent,
      originalQuery: resolved.queryText,
      canonicalClaim: resolved.queryText,
      mustTerms: searchRequest.mustTerms,
      optionalTerms: [],
      excludeTerms: searchRequest.excludeTerms,
      entities: [],
      polarity: searchRequest.polarity,
      claimTypes: searchRequest.claimTypes,
      scopeConstraints: searchRequest.scope,
      planner: "deterministic",
    },
    queryPlanHandoff: null,
    channels: emptyRetrievalChannels(),
    retrievalConfidence: null,
    indexSync: { checkedSnapshots: 0, pendingSnapshots: 0, durationMs: 0 },
    clusters: [],
    previewResults: [],
    warnings: ["证据库中暂无可供检索的有效结果。"],
  }, NO_STORE);
}

/** Quick preview answers only a bare text preview; anything else needs the Compiler. */
function assertQuickPreviewSupported(searchRequest: EvidenceSearchRequest) {
  if (searchRequest.stage !== "preview" || searchRequest.queryClaim
    || searchRequest.conversationHistory?.length || searchRequest.contextSubmission
    || searchRequest.clarificationSelection || searchRequest.mustTerms.length
    || searchRequest.excludeTerms.length || searchRequest.claimTypes.length
    || searchRequest.scope || searchRequest.polarity) {
    throw new ApiError(400, "QUICK_PREVIEW_UNSUPPORTED", "This request requires full task interpretation");
  }
}

/**
 * Independent local-only first paint: no Compiler sync, planner, embedding,
 * or relation API. These candidates never become a signed final handoff.
 */
async function quickPreviewResponse(
  DB: D1Database,
  signal: AbortSignal,
  searchRequest: EvidenceSearchRequest,
  resolved: ResolvedCorpus,
  query: PublicQuery,
) {
  assertQuickPreviewSupported(searchRequest);
  assertNotCancelled(signal);
  const refs = await quickSearchRefs(DB, PROJECT_ID,
    resolved.manifests.map(manifest => manifest.snapshotId), resolved.queryText);
  const [documents, sources] = await Promise.all([
    loadEvidenceRetrievalDocumentsByRefs(DB, PROJECT_ID, refs),
    loadEvidenceSources(DB, PROJECT_ID, [...new Set(refs.map(ref => ref.snapshotId))]),
  ]);
  const documentsByKey = new Map(documents.map(document => [evidenceRefKey(document), document]));
  const previewResults = refs.flatMap(ref => {
    const document = documentsByKey.get(evidenceRefKey(ref));
    return document ? [hydrateMember({
      snapshot_id: document.snapshotId, claim_id: document.claimId,
      paper_id: document.paperId, paper_family_id: document.paperFamilyId,
      scores: { lexical: null, dense: null, rrf: 0, rerank: null },
    }, documentsByKey, new Map(), sources)] : [];
  });
  assertNotCancelled(signal);
  return Response.json(boundedResponse({
    ok: true, stage: "preview", resultStatus: previewResults.length ? "matches" : "no_direct_answer",
    query, queryPlan: null, queryPlanHandoff: null,
    previewResults, clusters: [], warnings: [],
  }), NO_STORE);
}

/** Report coverage (indexing itself is background work), then run one Compiler search. */
async function runCompilerSearch(DB: D1Database, signal: AbortSignal, resolved: ResolvedCorpus) {
  const configuration = await getCompilerConfiguration();
  if (!configuration.sharedSecret) {
    throw new ApiError(503, "RETRIEVAL_NOT_CONFIGURED", "Retrieval service requires the shared Compiler credential");
  }
  const client = new RetrievalClient({ ...configuration, sharedSecret: configuration.sharedSecret, interactive: true });
  const serviceRequest = toEvidenceRetrievalSearchRequestDto(PROJECT_ID, resolved);
  try {
    assertNotCancelled(signal);
    // Indexing happens in the background work queue; a search only reports
    // papers that are not searchable yet and never fails because of one.
    const coverageStartedAt = Date.now();
    const sync: CoverageSync = {
      ...await ensureRetrievalCoverage(DB, client, resolved.manifests, signal),
      durationMs: 0,
    };
    sync.durationMs = Math.max(0, Date.now() - coverageStartedAt);
    assertNotCancelled(signal);
    const serviceResult = await client.search(serviceRequest,
      resolved.manifests.map(manifest => manifest.snapshotId), signal);
    assertNotCancelled(signal);
    return { sync, serviceResult };
  } catch (caught) {
    assertNotCancelled(signal);
    throw caught;
  }
}

/** A 2.3 clarification or missing-context prompt; its token stays server-side. */
async function pendingContinuationResponse(
  DB: D1Database,
  searchRequest: EvidenceSearchRequest,
  serviceResult: EvidenceRetrievalClarificationResponseDto | EvidenceRetrievalNeedsContextResponseDto,
  sync: CoverageSync,
  query: PublicQuery,
) {
  if (searchRequest.searchProtocol !== EVIDENCE_RETRIEVAL_CONTEXT_PROTOCOL_VERSION) {
    throw new ApiError(502, "INVALID_RETRIEVAL_RESPONSE", "Unexpected clarification response");
  }
  return Response.json(boundedResponse({
    ok: true,
    stage: "preview",
    resultStatus: serviceResult.result_status,
    query,
    queryPlan: null,
    queryPlanHandoff: null,
    ...(serviceResult.result_status === "needs_context" ? { needsContext: {
      question: serviceResult.needs_context.question,
      contextToken: await createRetrievalSession(DB, "context", serviceResult.needs_context.context_token),
    } } : { clarification: {
      question: serviceResult.clarification.question,
      options: serviceResult.clarification.options.map((option) => ({
        optionId: option.option_id,
        label: option.label,
        resolvedQuery: option.resolved_query,
      })),
      clarificationToken: await createRetrievalSession(
        DB, "clarification", serviceResult.clarification.clarification_token),
    } }),
    channels: serviceResult.channels,
    retrievalConfidence: null,
    indexSync: publicIndexSync(sync),
    clusters: [],
    previewResults: [],
    warnings: serviceResult.warnings,
  }), NO_STORE);
}

/** Preview candidates or final clusters, hydrated from the immutable D1 projection. */
async function retrievalResultResponse(
  DB: D1Database,
  searchRequest: EvidenceSearchRequest,
  serviceResult: EvidenceRetrievalResolvedSearchResponseDto,
  sync: CoverageSync,
  query: PublicQuery,
) {
  const clusters = serviceResult.clusters.filter((cluster) => (
    cluster.relation !== "unrelated"
    && (searchRequest.intent === "claim_search" || cluster.relation !== "related")
  ));
  const hydrationRefs = selectHydrationRefs({ ...serviceResult, clusters });
  const [documents, assessments, sources] = await Promise.all([
    loadEvidenceRetrievalDocumentsByRefs(DB, PROJECT_ID, hydrationRefs),
    loadLatestEvidenceRetrievalAssessmentsByRefs(DB, PROJECT_ID, hydrationRefs),
    loadEvidenceSources(DB, PROJECT_ID, [...new Set(hydrationRefs.map((ref) => ref.snapshotId))]),
  ]);
  const documentMap = new Map(documents.map((document) => [
    evidenceRefKey({ snapshotId: document.snapshotId, claimId: document.claimId }),
    document,
  ]));
  const assessmentMap = new Map(assessments.map((assessment) => [
    evidenceRefKey({ snapshotId: assessment.snapshotId, claimId: assessment.claimId }),
    assessment,
  ]));
  const selectedKeys = new Set(hydrationRefs.map(evidenceRefKey));
  const publicClusters = clusters.map((cluster) => {
    const representative = hydrateMember(cluster.representative, documentMap, assessmentMap, sources);
    const representativeKey = serviceEvidenceRefKey(cluster.representative);
    const members = cluster.member_refs
      .filter((member) => serviceEvidenceRefKey(member) !== representativeKey && selectedKeys.has(serviceEvidenceRefKey(member)))
      .map((member) => hydrateMember(member, documentMap, assessmentMap, sources));
    return {
      id: cluster.cluster_id,
      relation: cluster.relation,
      distinctPaperFamilies: cluster.distinct_paper_families,
      representative,
      members,
    };
  });
  const previewResults = serviceResult.preview.map((item) => (
    hydrateMember(item, documentMap, assessmentMap, sources)
  ));
  const warnings = [...sync.warnings];
  for (const warning of serviceResult.warnings) appendPublicWarning(warnings, warning);
  const returnedRefs = new Set(clusters.flatMap((cluster) => (
    cluster.member_refs.map(serviceEvidenceRefKey)
  ))).size;
  if (hydrationRefs.length < returnedRefs) {
    appendPublicWarning(warnings, "已优先展示每组观点中最具代表性的证据，其余结果可继续展开查看。");
  }

  const resultStatus = serviceResult.result_status === "degraded"
    ? "degraded"
    : ((serviceResult.stage === "preview" ? previewResults.length : publicClusters.length) > 0
      ? "matches"
      : "no_direct_answer");
  return Response.json(boundedResponse({
    ok: true,
    stage: serviceResult.stage,
    resultStatus,
    query,
    queryPlan: publicQueryPlan(serviceResult),
    // The browser holds an opaque session ID; the signed handoff stays here.
    queryPlanHandoff: serviceResult.query_plan_handoff
      ? await createRetrievalSession(DB, "query_plan_handoff", serviceResult.query_plan_handoff)
      : null,
    channels: serviceResult.channels,
    retrievalConfidence: publicRetrievalConfidence(serviceResult),
    indexSync: publicIndexSync(sync),
    clusters: publicClusters,
    previewResults,
    warnings,
  }), NO_STORE);
}

/**
 * Retrieve and hydrate source-backed scientific claims. One handler per mode
 * over a shared pipeline: validate → resolve sessions and corpus → empty
 * corpus | local quick preview | Compiler search → pending prompt | results.
 */
async function searchPost(request: Request) {
  const responseMode = new URL(request.url).searchParams.get("response");
  let contextContinuation = false;
  try {
    const payload = await readJsonObject(request, MAX_SEARCH_REQUEST_BYTES);
    const continuations = browserContinuations(payload);
    if (payload.search_protocol === EVIDENCE_RETRIEVAL_CONTEXT_PROTOCOL_VERSION) {
      // A context or clarification continuation that can no longer be used
      // asks for a new preview with the supplement merged into the question;
      // a plain final only re-previews (handoff error) or is a 400.
      contextContinuation = continuations.some(({ value, slot }) => slot !== "query_plan_handoff"
        && isRetrievalSessionId(value));
    }
    // The browser holds only session IDs. A raw Compiler token comes from a
    // page loaded before server-side sessions and re-previews like an expiry.
    for (const continuation of continuations) {
      if (continuation.compilerToken(continuation.value)) throw continuation.expired();
    }
    // The whole request is validated before any binding or storage access.
    const browserRequest = parseEvidenceSearchRequest(payload, { continuations: "session_ids" });
    if (responseMode === "quick_preview") assertQuickPreviewSupported(browserRequest);
    const { DB } = await getBindings();
    const searchRequest = await resolveBrowserSessions(DB, browserRequest);
    const signedFinal = searchRequest.stage === "ai_rerank" && searchRequest.queryPlanHandoff !== null;
    let resolved: ResolvedCorpus;
    try {
      resolved = await resolveEvidenceRetrievalCorpus(DB, PROJECT_ID, searchRequest);
    } catch (error) {
      if (error instanceof EvidenceRetrievalStoreError && QUERY_PLAN_HANDOFF_CORPUS_DRIFT_CODES.has(error.code)) {
        if (contextContinuation) throw contextRepreviewRequired();
        if (signedFinal) throw handoffRepreviewRequired();
      }
      throw error;
    }
    const query = publicBaseQuery(searchRequest, resolved);
    if (resolved.manifests.length === 0) {
      if (contextContinuation) throw contextRepreviewRequired();
      if (signedFinal) throw handoffRepreviewRequired();
      return emptyCorpusResponse(searchRequest, resolved, query);
    }
    if (responseMode === "quick_preview") {
      return await quickPreviewResponse(DB, request.signal, searchRequest, resolved, query);
    }
    const { sync, serviceResult } = await runCompilerSearch(DB, request.signal, resolved);
    if (serviceResult.channels.relation.status === "degraded") {
      throw new ApiError(503, "RETRIEVAL_RELATION_UNAVAILABLE", "模型关系分析未完成，请稍后重试", { retryable: false });
    }
    if (serviceResult.result_status === "clarification_required" || serviceResult.result_status === "needs_context") {
      return await pendingContinuationResponse(DB, searchRequest, serviceResult, sync, query);
    }
    return await retrievalResultResponse(DB, searchRequest, serviceResult, sync, query);
  } catch (error) {
    if (error instanceof EvidenceSearchContractError) {
      if (contextContinuation) return safeError(contextRepreviewRequired());
      return safeError(new ApiError(400, "INVALID_EVIDENCE_SEARCH", error.message, { path: error.path }));
    }
    if (error instanceof EvidenceRetrievalStoreError) {
      return safeError(new ApiError(error.status, error.code, error.message));
    }
    return safeError(error);
  }
}

/** A final can outlast Cloudflare's 100 s limit; the browser asks for a stream. */
export async function POST(request: Request) {
  return wantsStreamedResponse(request) ? streamedResponse(request, searchPost) : searchPost(request);
}
