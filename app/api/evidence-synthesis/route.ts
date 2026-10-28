import {
  buildClaimComparisonEvidenceEntities,
  buildClaimComparisonSide,
  CLAIM_COMPARISON_SCHEMA_VERSION,
  type ClaimComparisonAssessment,
  type ClaimComparisonPair,
  type ClaimComparisonSide,
  type NormalizedClaimDraft,
} from "@/lib/claim-comparison";
import {
  parseEvidenceGraph,
  sha256Hex,
  type ClaimModality,
  type ClaimPolarity,
  type ClaimType,
  type EvidenceGraph,
  type Scope,
} from "@/lib/evidence-graph";
import {
  EvidenceRetrievalStoreError,
  EvidenceSearchContractError,
  loadEvidenceRetrievalDocumentsByRefs,
  loadLatestEvidenceRetrievalAssessmentsByRefs,
  parseEvidenceSearchRequest,
  resolveEvidenceRetrievalCorpus,
  toEvidenceRetrievalSearchRequestDto,
  type EvidenceRetrievalAssessment,
  type EvidenceRetrievalDocument,
  type QueryClaimRef,
} from "@/lib/evidence-retrieval";
import { CompilerClient } from "@/lib/server/compiler-client";
import { RetrievalClient } from "@/lib/server/retrieval-client";
import {
  ApiError,
  assertSameOrigin,
  getBindings,
  getCompilerConfiguration,
  PROJECT_ID,
  readJsonObject,
  safeError,
} from "@/lib/server/evidence-store";
import { ensureRetrievalCoverage } from "@/lib/server/retrieval-sync";
import { streamedResponse, wantsStreamedResponse } from "@/lib/server/streamed-response";
import {
  compactEvidencePath,
  emptyRetrievalChannels,
  evidenceRefKey,
  loadEvidenceSources,
  type EvidenceSourceRow,
} from "@/lib/server/retrieval-view";

const MAX_REQUEST_BYTES = 128 * 1024;
const MAX_VIEWPOINT_CHARACTERS = 32_000;
const RETRIEVAL_LIMIT = 12;
const ID_RE = /^[A-Za-z][A-Za-z0-9._:-]{0,127}$/u;
const SNAPSHOT_ID_RE = /^snapshot:sha256:[a-f0-9]{64}$/u;
const CLAIM_TYPES = new Set<ClaimType>([
  "descriptive", "comparative", "correlational", "causal", "mechanistic", "methodological", "predictive",
]);
const POLARITIES = new Set<ClaimPolarity>(["affirmed", "negated", "uncertain"]);
const MODALITIES = new Set<ClaimModality>(["asserted", "hypothesis", "conditional"]);

type GraphRow = { snapshotId: string; graphJson: string };

type PaperSource = { kind: "paper_claim"; snapshotId: string; claimId: string };
type NormalizedSource = {
  kind: "normalized_claim";
  claim: Pick<NormalizedClaimDraft, "id" | "canonicalClaim" | "claimType" | "polarity" | "modality" | "scope">;
};

function objectValue(value: unknown, path: string) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ApiError(400, "INVALID_EVIDENCE_SYNTHESIS", `${path} must be an object`);
  }
  return value as Record<string, unknown>;
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[], path: string) {
  const unknown = Object.keys(value).filter((key) => !keys.includes(key));
  const missing = keys.filter((key) => !Object.hasOwn(value, key));
  if (unknown.length || missing.length) {
    throw new ApiError(400, "INVALID_EVIDENCE_SYNTHESIS", `${path} has an invalid closed shape`);
  }
}

function textValue(value: unknown, path: string, maximum: number) {
  if (typeof value !== "string" || !value.trim() || Array.from(value).length > maximum) {
    throw new ApiError(400, "INVALID_EVIDENCE_SYNTHESIS", `${path} must be bounded non-empty text`);
  }
  return value.normalize("NFC").trim();
}

function idValue(value: unknown, path: string) {
  const valueText = textValue(value, path, 128);
  if (!ID_RE.test(valueText)) throw new ApiError(400, "INVALID_EVIDENCE_SYNTHESIS", `${path} is invalid`);
  return valueText;
}

function stringArray(value: unknown, path: string, maximumItems: number) {
  if (!Array.isArray(value) || value.length > maximumItems) {
    throw new ApiError(400, "INVALID_EVIDENCE_SYNTHESIS", `${path} must be a bounded array`);
  }
  const values = value.map((item, index) => textValue(item, `${path}[${index}]`, 1_000));
  const identities = values.map((item) => item.normalize("NFKC").toLocaleLowerCase("und").replace(/\s+/gu, " "));
  if (new Set(identities).size !== values.length) {
    throw new ApiError(400, "INVALID_EVIDENCE_SYNTHESIS", `${path} must contain unique values`);
  }
  return values;
}

function nullableText(value: unknown, path: string, maximum: number) {
  return value === null ? null : textValue(value, path, maximum);
}

function nullableNumber(value: unknown, path: string) {
  if (value === null) return null;
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new ApiError(400, "INVALID_EVIDENCE_SYNTHESIS", `${path} must be a finite number or null`);
  }
  return value;
}

function parseScope(value: unknown, path: string): Scope {
  const scope = objectValue(value, path);
  exactKeys(scope, [
    "location", "time", "formation", "sample_type", "method", "condition", "measurement", "uncertainty", "entities",
  ], path);
  let time: Scope["time"] = null;
  if (scope.time !== null) {
    const rawTime = objectValue(scope.time, `${path}.time`);
    exactKeys(rawTime, ["label", "start", "end", "unit"], `${path}.time`);
    time = {
      label: nullableText(rawTime.label, `${path}.time.label`, 1_000),
      start: nullableNumber(rawTime.start, `${path}.time.start`),
      end: nullableNumber(rawTime.end, `${path}.time.end`),
      unit: nullableText(rawTime.unit, `${path}.time.unit`, 1_000),
    };
    if (time.label === null && time.start === null && time.end === null) {
      throw new ApiError(400, "INVALID_EVIDENCE_SYNTHESIS", `${path}.time requires a label or numeric bound`);
    }
    if ((time.start !== null || time.end !== null) && time.unit === null) {
      throw new ApiError(400, "INVALID_EVIDENCE_SYNTHESIS", `${path}.time numeric bounds require a unit`);
    }
    if (time.start !== null && time.end !== null && time.start > time.end) {
      throw new ApiError(400, "INVALID_EVIDENCE_SYNTHESIS", `${path}.time has inverted bounds`);
    }
  }
  return {
    location: stringArray(scope.location, `${path}.location`, 64),
    time,
    formation: stringArray(scope.formation, `${path}.formation`, 64),
    sample_type: stringArray(scope.sample_type, `${path}.sample_type`, 64),
    method: stringArray(scope.method, `${path}.method`, 64),
    condition: stringArray(scope.condition, `${path}.condition`, 64),
    measurement: stringArray(scope.measurement, `${path}.measurement`, 64),
    uncertainty: nullableText(scope.uncertainty, `${path}.uncertainty`, 2_000),
    entities: stringArray(scope.entities, `${path}.entities`, 128),
  };
}

function parseSource(value: unknown): PaperSource | NormalizedSource {
  const source = objectValue(value, "$.source");
  if (source.kind === "paper_claim") {
    exactKeys(source, ["kind", "snapshotId", "claimId"], "$.source");
    const snapshotId = textValue(source.snapshotId, "$.source.snapshotId", 128);
    if (!SNAPSHOT_ID_RE.test(snapshotId)) {
      throw new ApiError(400, "INVALID_EVIDENCE_SYNTHESIS", "$.source.snapshotId is invalid");
    }
    return { kind: "paper_claim", snapshotId, claimId: idValue(source.claimId, "$.source.claimId") };
  }
  if (source.kind === "normalized_claim") {
    exactKeys(source, ["kind", "claim"], "$.source");
    const claim = objectValue(source.claim, "$.source.claim");
    exactKeys(claim, ["id", "canonicalClaim", "claimType", "polarity", "modality", "scope"], "$.source.claim");
    if (!CLAIM_TYPES.has(claim.claimType as ClaimType)
      || !POLARITIES.has(claim.polarity as ClaimPolarity)
      || !MODALITIES.has(claim.modality as ClaimModality)) {
      throw new ApiError(400, "INVALID_EVIDENCE_SYNTHESIS", "$.source.claim has invalid scientific Claim fields");
    }
    return {
      kind: "normalized_claim",
      claim: {
        id: idValue(claim.id, "$.source.claim.id"),
        canonicalClaim: textValue(claim.canonicalClaim, "$.source.claim.canonicalClaim", 8_000),
        claimType: claim.claimType as ClaimType,
        polarity: claim.polarity as ClaimPolarity,
        modality: claim.modality as ClaimModality,
        scope: parseScope(claim.scope, "$.source.claim.scope"),
      },
    };
  }
  throw new ApiError(400, "INVALID_EVIDENCE_SYNTHESIS", "$.source.kind is unsupported");
}

async function loadGraphs(DB: D1Database, snapshotIds: string[]) {
  if (!snapshotIds.length) return new Map<string, EvidenceGraph>();
  const rows = await DB.prepare(`WITH requested AS (SELECT value AS snapshot_id FROM json_each(?))
    SELECT s.id AS snapshotId, s.graph_json AS graphJson FROM requested r
    JOIN evidence_graph_snapshots s ON s.id = r.snapshot_id WHERE s.project_id = ?`)
    .bind(JSON.stringify(snapshotIds), PROJECT_ID).all<GraphRow>();
  const parsed = await Promise.all(rows.results.map(async (row) => {
    try {
      return [row.snapshotId, await parseEvidenceGraph(JSON.parse(row.graphJson))] as const;
    } catch {
      throw new ApiError(
        409,
        "STORED_SNAPSHOT_INCOMPATIBLE",
        "Stored evidence no longer satisfies the current contract. Recompile the source artifact.",
      );
    }
  }));
  return new Map(parsed);
}

function publicScopeClaim(side: ClaimComparisonSide) {
  return {
    id: side.claim_id,
    canonicalClaim: side.canonical_claim,
    claimType: side.claim_type,
    polarity: side.polarity,
    modality: side.modality,
    scope: side.scope,
    sourceSnapshotId: side.source_snapshot_id,
    sourcePaperId: side.source_paper_id,
    sourcePaperFamilyId: side.source_paper_family_id,
  };
}

function publicEvidence(side: ClaimComparisonSide, selectedIds: string[]) {
  const selected = new Set(selectedIds);
  return side.evidence_entities.filter((entity) => selected.has(entity.id)).map((entity) => ({
    id: entity.id,
    kind: entity.kind,
    text: Array.from(entity.text).slice(0, 2_000).join(""),
    anchorIds: entity.anchor_ids,
  }));
}

function publicPath(document: EvidenceRetrievalDocument) {
  return compactEvidencePath(document);
}

function publicAssessment(assessment: EvidenceRetrievalAssessment | null) {
  return assessment === null ? null : {
    verdict: assessment.verdict,
    confidence: assessment.confidence,
    rationale: assessment.rationale,
    limitations: assessment.limitations,
    verifierModel: assessment.verifierModel,
    verifierPromptVersion: assessment.verifierPromptVersion,
  };
}

function resultGroup(item: ClaimComparisonAssessment) {
  if (item.verdict === "abstained"
    || item.relation === "no_relation"
    || item.relation === "insufficient_to_determine"
    || item.scopeAlignment === "unknown"
    || item.scopeAlignment === "disjoint") return null;
  if (item.relation === "supports" && item.direction === "candidate_to_query") {
    return item.scopeAlignment === "equivalent" || item.scopeAlignment === "query_within_candidate"
      ? "support" as const
      : "qualify" as const;
  }
  if (item.relation === "contradicts") {
    return item.scopeAlignment === "equivalent" ? "contradict" as const : "qualify" as const;
  }
  if (["qualifies", "refines"].includes(item.relation) && item.direction === "candidate_to_query") {
    return "qualify" as const;
  }
  return "context" as const;
}

function baseEmptyCompare(
  sourceClaim: ClaimComparisonSide,
  manifests: Array<{ paperFamilyId: string; documentCount: number }>,
  excludedSourceFamily: number,
  warnings: string[],
  channels = emptyRetrievalChannels(),
  indexSync = { checkedSnapshots: 0, pendingSnapshots: 0, durationMs: 0 },
) {
  return {
    ok: true,
    action: "compare" as const,
    sourceClaim: publicScopeClaim(sourceClaim),
    coverage: {
      activePaperFamilies: new Set(manifests.map((manifest) => manifest.paperFamilyId)).size,
      activeClaims: manifests.reduce((sum, manifest) => sum + manifest.documentCount, 0),
      retrievedCandidates: 0,
      comparedCandidates: 0,
      excludedSourceFamily,
      acceptedRelations: 0,
      retrievalLimit: RETRIEVAL_LIMIT,
      complete: false,
      asOf: new Date().toISOString(),
    },
    channels,
    indexSync,
    groups: { support: [], contradict: [], qualify: [], context: [] },
    warnings,
  };
}

async function normalize(payload: Record<string, unknown>, signal: AbortSignal) {
  exactKeys(payload, ["action", "source"], "$");
  const source = objectValue(payload.source, "$.source");
  exactKeys(source, ["kind", "text"], "$.source");
  if (source.kind !== "viewpoint") {
    throw new ApiError(400, "INVALID_EVIDENCE_SYNTHESIS", "normalize source must be a viewpoint");
  }
  const userText = textValue(source.text, "$.source.text", MAX_VIEWPOINT_CHARACTERS);
  const configuration = await getCompilerConfiguration();
  if (!configuration.sharedSecret) {
    throw new ApiError(503, "CLAIM_COMPARISON_NOT_CONFIGURED", "Claim comparison requires the shared Compiler credential");
  }
  const client = new CompilerClient(configuration);
  const normalization = await client.normalizeClaimComparison({
    schema_version: CLAIM_COMPARISON_SCHEMA_VERSION,
    project_id: PROJECT_ID,
    user_text: userText,
  }, signal);
  return Response.json({ ok: true, action: "normalize", normalization });
}

/** The comparison's own side: a stored paper Claim, or the normalized viewpoint. */
async function comparisonSourceSide(
  DB: D1Database,
  source: PaperSource | NormalizedSource,
  queryDocument: EvidenceRetrievalDocument | null,
): Promise<{ sourceClaim: ClaimComparisonSide; sourceFamilyId: string | null }> {
  if (source.kind !== "paper_claim") {
    return {
      sourceClaim: {
        claim_id: source.claim.id,
        canonical_claim: source.claim.canonicalClaim,
        claim_type: source.claim.claimType,
        polarity: source.claim.polarity,
        modality: source.claim.modality,
        scope: source.claim.scope,
        source_snapshot_id: null,
        source_paper_id: null,
        source_paper_family_id: null,
        evidence_entities: [],
      },
      sourceFamilyId: null,
    };
  }
  if (!queryDocument) throw new ApiError(404, "QUERY_CLAIM_NOT_FOUND", "The selected Claim is unavailable");
  const [graphs, assessments] = await Promise.all([
    loadGraphs(DB, [source.snapshotId]),
    loadLatestEvidenceRetrievalAssessmentsByRefs(DB, PROJECT_ID, [{ snapshotId: source.snapshotId, claimId: source.claimId }]),
  ]);
  const graph = graphs.get(source.snapshotId);
  if (!graph) throw new ApiError(404, "SNAPSHOT_NOT_FOUND", "The selected evidence graph is unavailable");
  return {
    sourceClaim: buildClaimComparisonSide(
      queryDocument,
      buildClaimComparisonEvidenceEntities(graph, source.claimId, assessments[0] ?? null),
    ),
    sourceFamilyId: queryDocument.paperFamilyId,
  };
}

/** Candidate sides built from the immutable projection, graph and source of each ref. */
async function comparisonCandidateSides(DB: D1Database, refs: QueryClaimRef[]) {
  const [documents, assessments, sources, graphs] = await Promise.all([
    loadEvidenceRetrievalDocumentsByRefs(DB, PROJECT_ID, refs),
    loadLatestEvidenceRetrievalAssessmentsByRefs(DB, PROJECT_ID, refs),
    loadEvidenceSources(DB, PROJECT_ID, [...new Set(refs.map((ref) => ref.snapshotId))]),
    loadGraphs(DB, [...new Set(refs.map((ref) => ref.snapshotId))]),
  ]);
  const assessmentMap = new Map(assessments.map((assessment) => [evidenceRefKey(assessment), assessment]));
  const candidateSides = new Map<string, ClaimComparisonSide>();
  const documentMap = new Map<string, EvidenceRetrievalDocument>();
  for (const document of documents) {
    const key = evidenceRefKey(document);
    const graph = graphs.get(document.snapshotId);
    const sourceRow = sources.get(document.snapshotId);
    if (!graph || !sourceRow || sourceRow.artifactId !== document.paperFamilyId) {
      throw new ApiError(502, "RETRIEVAL_REFERENCE_MISMATCH", "Retrieved evidence does not match its immutable source");
    }
    documentMap.set(key, document);
    candidateSides.set(key, buildClaimComparisonSide(
      document,
      buildClaimComparisonEvidenceEntities(graph, document.claimId, assessmentMap.get(key) ?? null),
    ));
  }
  if (candidateSides.size !== refs.length) {
    throw new ApiError(502, "RETRIEVAL_REFERENCE_NOT_FOUND", "Retrieval returned a Claim absent from the immutable projection");
  }
  return { assessmentMap, candidateSides, documentMap, sources };
}

/** One verified comparison as the public result item. */
function publicComparisonItem(
  final: ClaimComparisonAssessment,
  pair: ClaimComparisonPair,
  document: EvidenceRetrievalDocument,
  sourceRow: EvidenceSourceRow,
  assessment: EvidenceRetrievalAssessment | null,
) {
  return {
    pairId: final.pairId,
    comparisonId: final.comparisonId,
    verdict: final.verdict,
    relation: final.relation,
    direction: final.direction,
    stance: final.stance,
    scopeAlignment: final.scopeAlignment,
    directness: final.directness,
    confidence: final.confidence,
    rationale: final.rationale,
    limitations: final.limitations,
    candidate: {
      snapshotId: document.snapshotId,
      paperId: document.paperId,
      paperFamilyId: document.paperFamilyId,
      source: {
        artifactId: sourceRow.artifactId,
        artifactName: sourceRow.artifactName,
        artifactVersionId: sourceRow.artifactVersionId,
        versionNumber: Number(sourceRow.versionNumber),
        mediaType: sourceRow.mediaType,
        downloadUrl: `/api/artifacts/${sourceRow.sha256}`,
      },
      claim: publicScopeClaim(pair.candidate),
      assessment: publicAssessment(assessment),
      evidenceAnchors: document.evidenceAnchors.slice(0, 16).map((anchor) => ({
        id: anchor.anchorId,
        page: anchor.page,
        section: anchor.section,
        quote: anchor.quote === null ? null : Array.from(anchor.quote).slice(0, 2_000).join(""),
        figure: anchor.figure,
        table: anchor.table,
        regionLabel: anchor.regionLabel,
      })),
      shortestEvidencePath: publicPath(document),
    },
    selectedEvidence: {
      query: publicEvidence(pair.query, final.queryEvidenceIds),
      candidate: publicEvidence(pair.candidate, final.candidateEvidenceIds),
    },
  };
}

async function compare(payload: Record<string, unknown>, signal: AbortSignal) {
  exactKeys(payload, ["action", "source"], "$");
  const source = parseSource(payload.source);
  const { DB } = await getBindings();
  const searchRequest = source.kind === "paper_claim"
    ? parseEvidenceSearchRequest({
      stage: "preview",
      query_claim: { snapshot_id: source.snapshotId, claim_id: source.claimId },
      intent: "claim_search",
      corpus: "cross_paper",
      limit: RETRIEVAL_LIMIT,
    })
    : parseEvidenceSearchRequest({
      stage: "preview",
      query: source.claim.canonicalClaim,
      intent: "claim_search",
      corpus: "cross_paper",
      limit: RETRIEVAL_LIMIT,
    });
  const resolved = await resolveEvidenceRetrievalCorpus(DB, PROJECT_ID, searchRequest);
  const { sourceClaim, sourceFamilyId } = await comparisonSourceSide(DB, source, resolved.queryDocument);

  const corpusManifests = resolved.manifests.filter((manifest) => manifest.paperFamilyId !== sourceFamilyId);
  const excludedSourceFamily = resolved.manifests.length - corpusManifests.length;
  if (!corpusManifests.length) {
    return Response.json(baseEmptyCompare(sourceClaim, corpusManifests, excludedSourceFamily, [
      "证据库中没有其他可用于比较的独立论文。",
    ]));
  }

  const configuration = await getCompilerConfiguration();
  if (!configuration.sharedSecret) {
    throw new ApiError(503, "RETRIEVAL_NOT_CONFIGURED", "Retrieval service requires the shared Compiler credential");
  }
  const retrieval = new RetrievalClient({ ...configuration, sharedSecret: configuration.sharedSecret, interactive: true });
  const coverageStartedAt = Date.now();
  const coverage = await ensureRetrievalCoverage(DB, retrieval, resolved.manifests, signal);
  const sync = { ...coverage, durationMs: Math.max(0, Date.now() - coverageStartedAt) };
  const previewResult = await retrieval.search(
    toEvidenceRetrievalSearchRequestDto(PROJECT_ID, resolved),
    resolved.manifests.map((manifest) => manifest.snapshotId),
    signal,
  );
  const previewRefs = new Map<string, QueryClaimRef>();
  const excludedByFamily = new Set<string>();
  for (const ref of previewResult.preview) {
    if (ref.paper_family_id === sourceFamilyId) {
      excludedByFamily.add(evidenceRefKey({ snapshotId: ref.snapshot_id, claimId: ref.claim_id }));
      continue;
    }
    const converted = { snapshotId: ref.snapshot_id, claimId: ref.claim_id };
    previewRefs.set(evidenceRefKey(converted), converted);
  }
  const boundedPreviewRefs = [...previewRefs.values()].slice(0, RETRIEVAL_LIMIT);
  const boundedPreviewKeys = new Set(boundedPreviewRefs.map((ref) => evidenceRefKey(ref)));
  const sealedPreviewKeys = new Set(previewResult.preview.map((ref) => (
    evidenceRefKey({ snapshotId: ref.snapshot_id, claimId: ref.claim_id })
  )));
  const previewWarnings = [...new Set([...sync.warnings, ...previewResult.warnings])];
  if (!boundedPreviewRefs.length) {
    if (previewResult.channels.lexical.status !== "ok" && previewResult.channels.dense.status !== "ok") {
      previewWarnings.unshift("关键词与语义检索暂时受限。");
    }
    const response = baseEmptyCompare(
      sourceClaim,
      corpusManifests,
      excludedSourceFamily + excludedByFamily.size,
      previewWarnings,
      previewResult.channels,
      {
        checkedSnapshots: sync.checkedSnapshots,
        pendingSnapshots: sync.pendingSnapshots,
        durationMs: sync.durationMs,
      },
    );
    response.coverage.retrievedCandidates = previewResult.preview.length;
    return Response.json(response);
  }

  // The final ranks exactly the sealed preview under the plan that produced it
  // (RTS-1): the signed handoff stays server-side and binds the preview's
  // ordered candidates, so the source family is excluded after ranking.
  if (!previewResult.query_plan_handoff) {
    throw new ApiError(502, "INVALID_RETRIEVAL_RESPONSE", "Retrieval preview did not seal its query plan");
  }
  const finalSearchRequest = parseEvidenceSearchRequest({
    stage: "ai_rerank",
    ...(source.kind === "paper_claim"
      ? { query_claim: { snapshot_id: source.snapshotId, claim_id: source.claimId } }
      : { query: source.claim.canonicalClaim }),
    candidate_refs: previewResult.preview.map((ref) => ({
      snapshot_id: ref.snapshot_id,
      claim_id: ref.claim_id,
    })),
    query_plan_handoff: previewResult.query_plan_handoff,
    intent: "claim_search",
    corpus: "cross_paper",
    limit: RETRIEVAL_LIMIT,
  });
  const finalCorpus = { ...resolved, request: finalSearchRequest };
  const finalResult = await retrieval.search(
    toEvidenceRetrievalSearchRequestDto(PROJECT_ID, finalCorpus),
    resolved.manifests.map((manifest) => manifest.snapshotId),
    signal,
  );
  if (finalResult.channels.relation.status !== "ok") {
    throw new ApiError(
      503,
      "CLAIM_RELATION_CLASSIFIER_UNAVAILABLE",
      "候选 Claim 的关系分析未完成，请稍后重试",
    );
  }
  const classifiedRefs = new Map<string, QueryClaimRef>();
  for (const cluster of finalResult.clusters) {
    if (cluster.relation === "unrelated") continue;
    for (const member of cluster.member_refs) {
      const memberKey = evidenceRefKey({ snapshotId: member.snapshot_id, claimId: member.claim_id });
      if (member.paper_family_id === sourceFamilyId) {
        excludedByFamily.add(memberKey);
        continue;
      }
      if (!sealedPreviewKeys.has(memberKey)) {
        throw new ApiError(
          502,
          "RETRIEVAL_REFERENCE_MISMATCH",
          "Classified evidence does not belong to the immutable preview",
        );
      }
      // Ranked, but beyond the comparison cap.
      if (!boundedPreviewKeys.has(memberKey)) continue;
      classifiedRefs.set(memberKey, {
        snapshotId: member.snapshot_id,
        claimId: member.claim_id,
      });
    }
  }
  const refs = [...classifiedRefs.values()].slice(0, RETRIEVAL_LIMIT);
  const warnings = [...new Set([
    ...previewWarnings,
    ...finalResult.warnings,
  ])];
  if (!refs.length) {
    const response = baseEmptyCompare(
      sourceClaim,
      corpusManifests,
      excludedSourceFamily + excludedByFamily.size,
      [...warnings, "分析现有候选后，暂未发现与当前观点可比较的论文 Claim。"],
      finalResult.channels,
      {
        checkedSnapshots: sync.checkedSnapshots,
        pendingSnapshots: sync.pendingSnapshots,
        durationMs: sync.durationMs,
      },
    );
    response.coverage.retrievedCandidates = previewResult.preview.length;
    return Response.json(response);
  }

  const { assessmentMap, candidateSides, documentMap, sources } = await comparisonCandidateSides(DB, refs);
  const pairs: ClaimComparisonPair[] = await Promise.all(refs.map(async (ref) => {
    const candidate = candidateSides.get(evidenceRefKey(ref))!;
    const digest = await sha256Hex([
      sourceClaim.claim_id, candidate.source_snapshot_id, candidate.claim_id,
    ].join("\u0000"));
    return { id: `pair:sha256:${digest}`, query: sourceClaim, candidate };
  }));
  const compiler = new CompilerClient(configuration);
  const envelope = await compiler.analyzeClaimComparisons({
    schema_version: CLAIM_COMPARISON_SCHEMA_VERSION,
    project_id: PROJECT_ID,
    pairs,
  }, signal);
  const groups: Record<"support" | "contradict" | "qualify" | "context", unknown[]> = {
    support: [], contradict: [], qualify: [], context: [],
  };
  const pairMap = new Map(pairs.map((pair) => [pair.id, pair]));
  for (const final of envelope.verification?.assessments ?? []) {
    const pair = pairMap.get(final.pairId);
    if (!pair) throw new ApiError(502, "CLAIM_COMPARISON_PAIR_MISMATCH", "Compiler returned an unknown comparison pair");
    const group = resultGroup(final);
    if (group === null) continue;
    const document = documentMap.get(evidenceRefKey({
      snapshotId: pair.candidate.source_snapshot_id!,
      claimId: pair.candidate.claim_id,
    }))!;
    groups[group].push(publicComparisonItem(
      final,
      pair,
      document,
      sources.get(document.snapshotId)!,
      assessmentMap.get(evidenceRefKey(document)) ?? null,
    ));
  }
  const acceptedRelations = groups.support.length + groups.contradict.length + groups.qualify.length + groups.context.length;
  return Response.json({
    ok: true,
    action: "compare",
    sourceClaim: publicScopeClaim(sourceClaim),
    coverage: {
      activePaperFamilies: new Set(corpusManifests.map((manifest) => manifest.paperFamilyId)).size,
      activeClaims: corpusManifests.reduce((sum, manifest) => sum + manifest.documentCount, 0),
      retrievedCandidates: previewResult.preview.length,
      comparedCandidates: pairs.length,
      excludedSourceFamily: excludedSourceFamily + excludedByFamily.size,
      acceptedRelations,
      retrievalLimit: RETRIEVAL_LIMIT,
      complete: false,
      asOf: new Date().toISOString(),
    },
    channels: finalResult.channels,
    indexSync: {
      checkedSnapshots: sync.checkedSnapshots,
      pendingSnapshots: sync.pendingSnapshots,
      durationMs: sync.durationMs,
    },
    groups,
    warnings,
  });
}

async function synthesisPost(request: Request) {
  try {
    assertSameOrigin(request);
    const payload = await readJsonObject(request, MAX_REQUEST_BYTES);
    const action = payload.action;
    if (action === "normalize") return await normalize(payload, request.signal);
    if (action === "compare") return await compare(payload, request.signal);
    throw new ApiError(400, "INVALID_EVIDENCE_SYNTHESIS", "$.action must be normalize or compare");
  } catch (error) {
    if (error instanceof EvidenceSearchContractError) {
      return safeError(new ApiError(400, "INVALID_EVIDENCE_SYNTHESIS", error.message, { path: error.path }));
    }
    if (error instanceof EvidenceRetrievalStoreError) {
      return safeError(new ApiError(error.status, error.code, error.message));
    }
    return safeError(error);
  }
}

/** A comparison can outlast Cloudflare's 100 s limit; the browser asks for a stream. */
export async function POST(request: Request) {
  return wantsStreamedResponse(request) ? streamedResponse(request, synthesisPost) : synthesisPost(request);
}
