import {
  buildCitationCandidateEntities,
  CITATION_RECEPTION_PROCESSOR_VERSION,
  CITATION_RECEPTION_SCHEMA_VERSION,
  type CitationCandidateEntity,
  type CitationConfirmationBasis,
  type CitationPairRequest,
  type CitationReceptionEnvelope,
} from "@/lib/citation-reception";
import {
  canonicalStringify,
  parseEvidenceGraph,
  SNAPSHOT_ID_RE,
  type EvidenceEntity,
  type EvidenceGraph,
  type SourceAnchor,
} from "@/lib/evidence-graph";
import { loadActiveEvidenceRetrievalManifests } from "@/lib/evidence-retrieval";
import { CompilerClient } from "@/lib/server/compiler-client";
import { settleWithConcurrency } from "@/lib/server/concurrency";
import {
  ApiError,
  assertSameOrigin,
  getBindings,
  getCompilerConfiguration,
  PROJECT_ID,
  readJsonObject,
  safeError,
  sha256Hex,
} from "@/lib/server/evidence-store";
import { streamedResponse, wantsStreamedResponse } from "@/lib/server/streamed-response";

const MAX_PROCESS_BYTES = 1_024;
const MAX_PAIRS_PER_REQUEST = 12;
const MAX_PUBLIC_ITEMS = 100;
const MAX_CANDIDATES_PER_PAPER = 1_024;
const MAX_CANDIDATE_TEXT = 4_000;
const DOI_RE = /^10\.[0-9]{4,9}\/[\-._;()/:a-z0-9]+$/iu;
const STRONG_BASES = new Set<CitationConfirmationBasis>([
  "consistent_with_citing_evidence", "contradicted_by_citing_evidence",
  "qualified_by_citing_evidence", "direct_replication", "reanalysis",
  "meta_analytic_synthesis",
]);

type TargetRow = {
  snapshotId: string;
  graphJson: string;
  rootArtifactVersionId: string;
  artifactId: string;
  artifactName: string;
  sha256: string;
  mediaType: string;
  identitySource: string | null;
  openAlexWorkId: string | null;
  matchedDoi: string | null;
};

type EligibleRow = {
  referenceId: string;
  citingSnapshotId: string;
  rawText: string;
  normalizedDoi: string | null;
  year: number | null;
  mentionId: string;
  marker: string;
  exactQuote: string;
  contextText: string;
  page: number;
  groupSize: number;
  candidateFunctionsJson: string;
};

type ReceptionRow = {
  citingSnapshotId: string;
  assertionId: string;
  mentionId: string;
  attributedProposition: string;
  citationFunction: "result_comparison" | "replication" | "critique";
  stance: "supports" | "contradicts" | "qualifies" | "unclear";
  targetEntityKind: CitationCandidateEntity["kind"] | null;
  targetEntityId: string | null;
  citingEvidenceEntityIdsJson: string;
  assertionConfidence: number;
  faithfulness: "faithful" | "partial" | "overgeneralized" | "contradicted_by_target" | "not_found" | "ambiguous" | "indirect";
  verifiedConfirmationBasis: CitationConfirmationBasis;
  independence: "independent" | "shared_authors" | "shared_data" | "derived_data" | "unknown";
  assessmentConfidence: number;
  limitationsJson: string;
  marker: string;
  exactQuote: string;
  page: number;
  bboxJson: string | null;
  charStart: number;
  charEnd: number;
  citingArtifactId: string;
  citingArtifactName: string;
  citingArtifactVersionId: string;
  citingArtifactSha256: string;
  citingMediaType: string;
  processedAt: string;
};

type CitationGraphContext = {
  graph: EvidenceGraph;
  entities: Map<string, EvidenceEntity>;
  candidates: CitationCandidateEntity[];
  candidatesById: Map<string, CitationCandidateEntity>;
};

function normalizeDoi(value: string | null) {
  if (value === null) return null;
  const normalized = value.trim().toLowerCase()
    .replace(/^https?:\/\/(?:dx\.)?doi\.org\//u, "")
    .replace(/^doi\s*:\s*/u, "")
    .replace(/[.,;:)>\]}]+$/u, "");
  return DOI_RE.test(normalized) ? normalized : null;
}

async function parseStoredGraph(value: string) {
  try {
    return await parseEvidenceGraph(JSON.parse(value));
  } catch {
    throw new ApiError(
      409,
      "STORED_SNAPSHOT_INCOMPATIBLE",
      "Stored evidence no longer satisfies the current contract. Recompile the source artifact.",
    );
  }
}

async function loadTarget(DB: D1Database, snapshotId: string) {
  const row = await DB.prepare(`SELECT s.id AS snapshotId, s.graph_json AS graphJson,
      s.root_artifact_version_id AS rootArtifactVersionId,
      a.id AS artifactId, a.preferred_name AS artifactName,
      v.sha256, v.media_type AS mediaType,
      c.source AS identitySource, c.openalex_work_id AS openAlexWorkId,
      c.matched_doi AS matchedDoi
    FROM evidence_graph_snapshots s
    JOIN artifact_versions v ON v.id = s.root_artifact_version_id
    JOIN artifacts a ON a.id = v.artifact_id
    LEFT JOIN openalex_paper_classifications c
      ON c.project_id = s.project_id AND c.snapshot_id = s.id
    WHERE s.project_id = ? AND a.project_id = ? AND s.id = ? LIMIT 1`)
    .bind(PROJECT_ID, PROJECT_ID, snapshotId).first<TargetRow>();
  if (!row) throw new ApiError(404, "SNAPSHOT_NOT_FOUND", "Evidence graph snapshot was not found");
  return row;
}

function authoritativeDoi(row: TargetRow) {
  if (row.identitySource !== "openalex_work" || !row.openAlexWorkId) return null;
  return normalizeDoi(row.matchedDoi);
}

async function identityIsUnique(
  DB: D1Database,
  doi: string,
  activeSnapshotIds: string[],
  targetArtifactId: string,
) {
  if (!activeSnapshotIds.length) return true;
  const rows = await DB.prepare(`SELECT DISTINCT v.artifact_id AS artifactId
    FROM openalex_paper_classifications c
    JOIN evidence_graph_snapshots s ON s.id = c.snapshot_id
    JOIN artifact_versions v ON v.id = s.root_artifact_version_id
    WHERE c.project_id = ? AND c.source = 'openalex_work'
      AND c.openalex_work_id IS NOT NULL AND lower(c.matched_doi) = ?
      AND c.snapshot_id IN (SELECT value FROM json_each(?))`)
    .bind(PROJECT_ID, doi, JSON.stringify(activeSnapshotIds)).all<{ artifactId: string }>();
  return new Set([targetArtifactId, ...rows.results.map((row) => row.artifactId)]).size === 1;
}

function parseJsonArray<T>(value: string, label: string): T[] {
  try {
    const parsed = JSON.parse(value);
    if (Array.isArray(parsed)) return parsed as T[];
  } catch { /* handled below */ }
  throw new ApiError(500, "STORED_CITATION_RECEPTION_INVALID", `${label} is invalid`);
}

function boundedCandidates(candidates: CitationCandidateEntity[], side: "citing" | "cited") {
  const targetPriority: Record<CitationCandidateEntity["kind"], number> = side === "cited"
    ? { atomic_claim: 0, observation: 1, structured_visual_evidence: 2, reasoning: 3, source_anchor: 4 }
    : { observation: 0, structured_visual_evidence: 1, reasoning: 2, source_anchor: 3, atomic_claim: 4 };
  return candidates
    .sort((left, right) => targetPriority[left.kind] - targetPriority[right.kind] || left.id.localeCompare(right.id))
    .slice(0, MAX_CANDIDATES_PER_PAPER)
    // Cut by code point: half of a surrogate pair is not valid text for the Compiler.
    .map((item) => ({ ...item, text: Array.from(item.text).slice(0, MAX_CANDIDATE_TEXT).join("") }));
}

async function loadEligibleRows(DB: D1Database, doi: string, activeSnapshotIds: string[]) {
  if (!activeSnapshotIds.length) return [];
  const rows = await DB.prepare(`SELECT r.id AS referenceId,
      r.citing_snapshot_id AS citingSnapshotId, r.raw_text AS rawText,
      r.normalized_doi AS normalizedDoi, r.year,
      m.id AS mentionId, m.marker, m.exact_quote AS exactQuote,
      m.context_text AS contextText, m.page,
      m.group_size AS groupSize,
      m.candidate_functions_json AS candidateFunctionsJson
    FROM bibliographic_references r
    JOIN citation_mentions m ON m.citing_snapshot_id = r.citing_snapshot_id
      AND m.reference_id = r.id
    WHERE r.project_id = ? AND lower(r.normalized_doi) = ?
      AND json_array_length(m.candidate_functions_json) > 0
      AND r.citing_snapshot_id IN (SELECT value FROM json_each(?))
    ORDER BY r.citing_snapshot_id, r.ordinal, m.page, m.id`)
    .bind(PROJECT_ID, doi, JSON.stringify(activeSnapshotIds)).all<EligibleRow>();
  return rows.results;
}

async function loadEligibleCitingSnapshotIds(
  DB: D1Database,
  doi: string,
  activeSnapshotIds: string[],
) {
  if (!activeSnapshotIds.length) return new Set<string>();
  const rows = await DB.prepare(`SELECT DISTINCT r.citing_snapshot_id AS citingSnapshotId
    FROM bibliographic_references r
    JOIN citation_mentions m ON m.citing_snapshot_id = r.citing_snapshot_id
      AND m.reference_id = r.id
    WHERE r.project_id = ? AND lower(r.normalized_doi) = ?
      AND json_array_length(m.candidate_functions_json) > 0
      AND r.citing_snapshot_id IN (SELECT value FROM json_each(?))`)
    .bind(PROJECT_ID, doi, JSON.stringify(activeSnapshotIds))
    .all<{ citingSnapshotId: string }>();
  return new Set(rows.results.map((row) => row.citingSnapshotId));
}

function groupEligible(rows: EligibleRow[]) {
  const groups = new Map<string, EligibleRow[]>();
  for (const row of rows) {
    const values = groups.get(row.referenceId) ?? [];
    values.push(row);
    groups.set(row.referenceId, values);
  }
  return [...groups.values()];
}

async function runIdFor(envelope: CitationReceptionEnvelope) {
  return `citation-run:sha256:${await sha256Hex(canonicalStringify({
    processorVersion: envelope.processorVersion,
    citingSnapshotId: envelope.citingSnapshotId,
    referenceId: envelope.referenceId,
    citedSnapshotId: envelope.citedSnapshotId,
    assertionsSha256: envelope.extraction.assertionsSha256,
  }))}`;
}

async function persistReception(
  DB: D1Database,
  envelope: CitationReceptionEnvelope,
  reprocess: boolean,
) {
  const existing = await DB.prepare(`SELECT id FROM citation_reception_runs
    WHERE project_id = ? AND processor_version = ? AND citing_snapshot_id = ?
      AND reference_id = ? AND cited_snapshot_id = ? LIMIT 1`)
    .bind(
      PROJECT_ID,
      envelope.processorVersion,
      envelope.citingSnapshotId,
      envelope.referenceId,
      envelope.citedSnapshotId,
    ).first<{ id: string }>();
  if (existing && !reprocess) return { id: existing.id, duplicate: true };
  const runId = await runIdFor(envelope);
  const verification = envelope.verification;
  const createdAt = new Date().toISOString();
  const assessmentIds = new Map(await Promise.all((verification?.assessments ?? []).map(async (item) => [
    item.assertionId,
    `citation-assessment:sha256:${await sha256Hex(canonicalStringify({
      assertionId: item.assertionId,
      extractionSha256: verification!.extractionSha256,
      verifierPromptVersion: verification!.promptVersion,
    }))}`,
  ] as const)));
  await DB.batch([
    ...(existing && reprocess ? [
      DB.prepare(`DELETE FROM citation_assessments WHERE assertion_id IN (
        SELECT id FROM citation_assertions WHERE run_id = ?)` ).bind(existing.id),
      DB.prepare("DELETE FROM citation_assertions WHERE run_id = ?").bind(existing.id),
      DB.prepare("DELETE FROM citation_reception_runs WHERE id = ?").bind(existing.id),
    ] : []),
    DB.prepare(`INSERT INTO citation_reception_runs
      (id, project_id, citing_snapshot_id, reference_id, cited_snapshot_id,
       processor_version, resolution_method, resolution_confidence,
       extraction_run_id, extraction_model, extraction_prompt_version,
       assertions_sha256, verification_run_id, verifier_model,
       verifier_prompt_version, assertion_count, created_at)
      VALUES (?, ?, ?, ?, ?, ?, 'doi', 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)`).bind(
      runId,
      PROJECT_ID,
      envelope.citingSnapshotId,
      envelope.referenceId,
      envelope.citedSnapshotId,
      envelope.processorVersion,
      envelope.extraction.runId,
      envelope.extraction.model,
      envelope.extraction.promptVersion,
      envelope.extraction.assertionsSha256,
      verification?.runId ?? null,
      verification?.model ?? null,
      verification?.promptVersion ?? null,
      envelope.extraction.assertions.length,
      createdAt,
    ),
    ...envelope.extraction.assertions.map((item) => DB.prepare(`INSERT INTO citation_assertions
      (id, run_id, project_id, mention_id, attributed_proposition,
       citation_function, stance, confirmation_basis, target_entity_kind,
       target_entity_id, citing_evidence_entity_ids_json, confidence, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`).bind(
      item.id,
      runId,
      PROJECT_ID,
      item.mentionId,
      item.attributedProposition,
      item.function,
      item.stance,
      item.confirmationBasis,
      item.targetEntityKind,
      item.targetEntityId,
      canonicalStringify(item.citingEvidenceEntityIds),
      item.confidence,
      createdAt,
    )),
    ...(verification?.assessments ?? []).map((item) => DB.prepare(`INSERT INTO citation_assessments
      (id, assertion_id, project_id, faithfulness, confirmation_basis,
       independence, confidence, evidence_ids_json, limitations_json,
       extraction_sha256, verifier_model, verifier_prompt_version, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`).bind(
      assessmentIds.get(item.assertionId),
      item.assertionId,
      PROJECT_ID,
      item.faithfulness,
      item.confirmationBasis,
      item.independence,
      item.confidence,
      canonicalStringify(item.evidenceIds),
      canonicalStringify(item.limitations),
      verification!.extractionSha256,
      verification!.model,
      verification!.promptVersion,
      createdAt,
    )),
  ]);
  return { id: runId, duplicate: false };
}

async function processPair(
  DB: D1Database,
  client: CompilerClient,
  citedSnapshotId: string,
  citedCandidates: CitationCandidateEntity[],
  citing: CitationGraphContext,
  rows: EligibleRow[],
  reprocess: boolean,
  signal: AbortSignal,
) {
  const first = rows[0];
  const request: CitationPairRequest = {
    schema_version: CITATION_RECEPTION_SCHEMA_VERSION,
    project_id: PROJECT_ID,
    citing_snapshot_id: first.citingSnapshotId,
    cited_snapshot_id: citedSnapshotId,
    reference: {
      id: first.referenceId,
      raw_text: first.rawText,
      doi: normalizeDoi(first.normalizedDoi),
      year: first.year,
    },
    mentions: rows.slice(0, 128).map((row) => ({
      id: row.mentionId,
      marker: row.marker,
      exact_quote: row.exactQuote,
      context_text: row.contextText,
      page: Number(row.page),
      group_size: Number(row.groupSize),
      candidate_functions: parseJsonArray(row.candidateFunctionsJson, "citation mention candidate functions"),
    })),
    citing_entities: boundedCandidates(citing.candidates, "citing"),
    cited_entities: boundedCandidates(citedCandidates, "cited"),
  };
  if (!request.citing_entities.length || !request.cited_entities.length) {
    throw new ApiError(409, "CITATION_PAIR_HAS_NO_EVIDENCE", "Both papers need alignable evidence before citation analysis");
  }
  const envelope = await client.analyzeCitationReception(request, signal);
  const persisted = await persistReception(DB, envelope, reprocess);
  return { status: persisted.duplicate ? "duplicate" as const : "processed" as const, runId: persisted.id };
}

async function loadReceptionRows(DB: D1Database, targetSnapshotId: string, activeSnapshotIds: string[]) {
  if (!activeSnapshotIds.length) return [];
  const rows = await DB.prepare(`SELECT cr.citing_snapshot_id AS citingSnapshotId,
      ca.id AS assertionId, ca.mention_id AS mentionId,
      ca.attributed_proposition AS attributedProposition,
      ca.citation_function AS citationFunction, ca.stance,
      ca.target_entity_kind AS targetEntityKind, ca.target_entity_id AS targetEntityId,
      ca.citing_evidence_entity_ids_json AS citingEvidenceEntityIdsJson,
      ca.confidence AS assertionConfidence,
      cv.faithfulness, cv.confirmation_basis AS verifiedConfirmationBasis,
      cv.independence, cv.confidence AS assessmentConfidence,
      cv.limitations_json AS limitationsJson,
      cm.marker, cm.exact_quote AS exactQuote,
      cm.page, cm.bbox_json AS bboxJson, cm.char_start AS charStart,
      cm.char_end AS charEnd,
      a.id AS citingArtifactId, a.preferred_name AS citingArtifactName,
      v.id AS citingArtifactVersionId, v.sha256 AS citingArtifactSha256,
      v.media_type AS citingMediaType, cr.created_at AS processedAt
    FROM citation_reception_runs cr
    JOIN citation_assertions ca ON ca.run_id = cr.id
    JOIN citation_assessments cv ON cv.assertion_id = ca.id
    JOIN citation_mentions cm ON cm.id = ca.mention_id
    JOIN evidence_graph_snapshots s ON s.id = cr.citing_snapshot_id
    JOIN artifact_versions v ON v.id = s.root_artifact_version_id
    JOIN artifacts a ON a.id = v.artifact_id
    WHERE cr.project_id = ? AND cr.cited_snapshot_id = ?
      AND cr.processor_version = ?
      AND ca.target_entity_id IS NOT NULL
      AND cv.confirmation_basis <> 'mere_attribution'
      AND cr.citing_snapshot_id IN (SELECT value FROM json_each(?))
    ORDER BY cv.confidence DESC, cr.created_at DESC, ca.id`)
    .bind(
      PROJECT_ID,
      targetSnapshotId,
      CITATION_RECEPTION_PROCESSOR_VERSION,
      JSON.stringify(activeSnapshotIds),
    ).all<ReceptionRow>();
  return rows.results;
}

async function loadAnalyzedSnapshotIds(DB: D1Database, targetSnapshotId: string, activeSnapshotIds: string[]) {
  if (!activeSnapshotIds.length) return new Set<string>();
  const rows = await DB.prepare(`SELECT DISTINCT citing_snapshot_id AS citingSnapshotId
    FROM citation_reception_runs
    WHERE project_id = ? AND cited_snapshot_id = ? AND processor_version = ?
      AND citing_snapshot_id IN (SELECT value FROM json_each(?))`)
    .bind(
      PROJECT_ID,
      targetSnapshotId,
      CITATION_RECEPTION_PROCESSOR_VERSION,
      JSON.stringify(activeSnapshotIds),
    ).all<{ citingSnapshotId: string }>();
  return new Set(rows.results.map((row) => row.citingSnapshotId));
}

async function loadProcessedReferenceIds(DB: D1Database, targetSnapshotId: string) {
  const rows = await DB.prepare(`SELECT reference_id AS referenceId
    FROM citation_reception_runs
    WHERE project_id = ? AND cited_snapshot_id = ? AND processor_version = ?`)
    .bind(PROJECT_ID, targetSnapshotId, CITATION_RECEPTION_PROCESSOR_VERSION)
    .all<{ referenceId: string }>();
  return new Set(rows.results.map((row) => row.referenceId));
}

function allEntities(graph: EvidenceGraph) {
  return new Map<string, EvidenceEntity>([
    ...graph.artifacts,
    ...graph.artifact_versions,
    ...graph.anchors,
    ...graph.visual_evidence,
    ...graph.observations,
    ...graph.assumptions,
    ...graph.reasoning,
    ...graph.claims,
  ].map((item) => [item.id, item]));
}

function citationGraphContext(graph: EvidenceGraph): CitationGraphContext {
  const candidates = buildCitationCandidateEntities(graph);
  return {
    graph,
    entities: allEntities(graph),
    candidates,
    candidatesById: new Map(candidates.map((candidate) => [candidate.id, candidate])),
  };
}

async function loadCitationGraphContexts(DB: D1Database, snapshotIds: string[]) {
  const requested = [...new Set(snapshotIds)];
  if (!requested.length) return new Map<string, CitationGraphContext>();
  const rows = await DB.prepare(`SELECT id AS snapshotId, graph_json AS graphJson
    FROM evidence_graph_snapshots
    WHERE project_id = ? AND id IN (SELECT value FROM json_each(?))`)
    .bind(PROJECT_ID, JSON.stringify(requested))
    .all<{ snapshotId: string; graphJson: string }>();
  const contexts = new Map(await Promise.all(rows.results.map(async (row) => [
    row.snapshotId,
    citationGraphContext(await parseStoredGraph(row.graphJson)),
  ] as const)));
  if (contexts.size !== requested.length) {
    throw new ApiError(
      500,
      "CITATION_SOURCE_SNAPSHOT_MISSING",
      "A citation source snapshot is missing from the active corpus",
    );
  }
  return contexts;
}

function anchorFor(entity: EvidenceEntity | undefined, entities: Map<string, EvidenceEntity>) {
  if (!entity) return null;
  if (entity.kind === "source_anchor") return entity;
  if (entity.kind === "structured_visual_evidence") {
    const anchor = entities.get(entity.anchor_id);
    return anchor?.kind === "source_anchor" ? anchor : null;
  }
  if ("anchor_ids" in entity) {
    const anchor = entities.get(entity.anchor_ids[0]);
    return anchor?.kind === "source_anchor" ? anchor : null;
  }
  return null;
}

function mentionAnchor(row: ReceptionRow): SourceAnchor {
  let region: { x: number; y: number; width: number; height: number; unit: "normalized" } | null = null;
  if (row.bboxJson) {
    try {
      const value = JSON.parse(row.bboxJson) as Record<string, unknown>;
      if (["x", "y", "width", "height"].every((key) => typeof value[key] === "number")) {
        region = { x: value.x as number, y: value.y as number, width: value.width as number, height: value.height as number, unit: "normalized" };
      }
    } catch { /* a missing visual region does not remove the exact quote */ }
  }
  return {
    kind: "source_anchor",
    id: row.mentionId,
    artifact_version_id: row.citingArtifactVersionId,
    anchor_type: "citation",
    selector: {
      page: Number(row.page), section: null, paragraph: null, table: null,
      row_start: null, row_end: null, row_range_label: null, column_names: [],
      figure: null, series: null, char_start: Number(row.charStart),
      char_end: Number(row.charEnd), region, region_label: null, dataset_path: null,
    },
    quote: row.exactQuote,
    quote_sha256: null,
  };
}

function sourceFor(row: ReceptionRow, anchor: SourceAnchor) {
  return {
    artifactName: row.citingArtifactName,
    artifactVersionId: row.citingArtifactVersionId,
    mediaType: row.citingMediaType,
    sha256: row.citingArtifactSha256,
    anchor,
  };
}

async function hydrateItem(
  row: ReceptionRow,
  mentionCount: number,
  targetCandidates: Map<string, CitationCandidateEntity>,
  citingContexts: Map<string, CitationGraphContext>,
) {
  const target = targetCandidates.get(row.targetEntityId!)!;
  const citing = citingContexts.get(row.citingSnapshotId)!;
  const citingGraph = citing.graph;
  const citingEntities = citing.entities;
  const evidenceIds = parseJsonArray<string>(row.citingEvidenceEntityIdsJson, "citation assertion evidence IDs");
  const evidenceCandidate = evidenceIds.map((item) => citing.candidatesById.get(item)).find(Boolean);
  const evidenceEntity = evidenceCandidate && citingEntities.get(evidenceCandidate.id);
  const evidenceAnchor = anchorFor(evidenceEntity, citingEntities);
  const citationAnchor = mentionAnchor(row);
  citationAnchor.quote_sha256 = await sha256Hex(row.exactQuote);
  const confirmingEvidence = evidenceCandidate && evidenceAnchor ? {
    statement: evidenceCandidate.text,
    source: sourceFor(row, evidenceAnchor),
  } : null;
  return {
    id: row.assertionId,
    target: {
      entityId: target.id,
      entityKind: target.kind,
      rootClaimIds: target.root_claim_ids,
      statement: target.text,
      alignmentConfidence: Math.min(Number(row.assertionConfidence), Number(row.assessmentConfidence)),
    },
    citingPaper: {
      snapshotId: row.citingSnapshotId,
      paperId: citingGraph.paper_id,
      paperFamilyId: row.citingArtifactId,
      title: row.citingArtifactName,
      authors: [] as string[],
      year: null,
    },
    attributedProposition: row.attributedProposition,
    marker: row.marker,
    mentionCount,
    citationContext: {
      statement: row.exactQuote,
      source: sourceFor(row, citationAnchor),
    },
    confirmingEvidence,
    assessment: {
      intent: [row.citationFunction],
      stance: row.stance,
      confirmationMode: row.verifiedConfirmationBasis,
      independence: row.independence,
      faithfulness: row.faithfulness,
      confidence: Number(row.assessmentConfidence),
      limitationCount: parseJsonArray<string>(row.limitationsJson, "citation assessment limitations").length,
    },
  };
}

function receptionGroups(rows: ReceptionRow[]) {
  const groups = new Map<string, { row: ReceptionRow; mentionCount: number }>();
  for (const row of rows) {
    const key = [
      row.citingSnapshotId,
      row.targetEntityId,
      row.attributedProposition.normalize("NFKC").toLocaleLowerCase(),
      row.citationFunction,
      row.stance,
      row.verifiedConfirmationBasis,
    ].join("\u0000");
    const group = groups.get(key);
    if (group) group.mentionCount += 1;
    else groups.set(key, { row, mentionCount: 1 });
  }
  return [...groups.values()];
}

function summary(rows: ReceptionRow[]) {
  const counts = {
    independentReplications: 0,
    independentConsistentFindings: 0,
    reanalysesOrSyntheses: 0,
    verbalEndorsements: 0,
    qualifications: 0,
    contradictions: 0,
    needsReview: 0,
  };
  const paperFamilies = new Set<string>();
  for (const row of rows) {
    paperFamilies.add(row.citingArtifactId);
    const reliable = row.faithfulness === "faithful"
      && Number(row.assessmentConfidence) >= 0.7;
    if (!reliable || (STRONG_BASES.has(row.verifiedConfirmationBasis) && row.independence === "unknown")) {
      counts.needsReview += 1;
    }
    if (!reliable) continue;
    if (row.stance === "qualifies") counts.qualifications += 1;
    if (row.stance === "contradicts") counts.contradictions += 1;
    if (row.stance !== "supports") continue;
    if (row.verifiedConfirmationBasis === "direct_replication" && row.independence === "independent") {
      counts.independentReplications += 1;
    }
    if (row.verifiedConfirmationBasis === "consistent_with_citing_evidence" && row.independence === "independent") {
      counts.independentConsistentFindings += 1;
    }
    if (row.verifiedConfirmationBasis === "reanalysis" || row.verifiedConfirmationBasis === "meta_analytic_synthesis") {
      counts.reanalysesOrSyntheses += 1;
    }
    if (row.verifiedConfirmationBasis === "verbal_endorsement") counts.verbalEndorsements += 1;
  }
  return {
    totalAssertions: rows.length,
    distinctCitingPaperFamilies: paperFamilies.size,
    ...counts,
  };
}

async function publicReception(DB: D1Database, target: TargetRow) {
  const active = await loadActiveEvidenceRetrievalManifests(DB, PROJECT_ID);
  const activeSnapshotIds = active.map((item) => item.snapshotId);
  const doi = authoritativeDoi(target);
  const base = {
    ok: true as const,
    targetSnapshotId: target.snapshotId,
    coverage: {
      eligibleActivePapers: 0,
      analyzedActivePapers: 0,
      complete: false,
      asOf: new Date().toISOString(),
    },
    summary: summary([]),
    items: [] as Awaited<ReturnType<typeof hydrateItem>>[],
    total: 0,
    truncated: false,
    warnings: [] as string[],
  };
  if (!doi || !await identityIsUnique(DB, doi, activeSnapshotIds, target.artifactId)) {
    return {
      ...base,
      status: "unavailable" as const,
      warnings: [doi
        ? "同一 DOI 对应多篇已入库论文，暂时无法自动确定对应关系。"
        : "本篇尚未通过 OpenAlex Work 确认唯一 DOI，因此暂不建立引用关系。"],
    };
  }
  const eligiblePapers = await loadEligibleCitingSnapshotIds(DB, doi, activeSnapshotIds);
  eligiblePapers.delete(target.snapshotId);
  const [rows, analyzedPapers] = await Promise.all([
    loadReceptionRows(DB, target.snapshotId, activeSnapshotIds),
    loadAnalyzedSnapshotIds(DB, target.snapshotId, activeSnapshotIds),
  ]);
  const targetCandidates = buildCitationCandidateEntities(await parseStoredGraph(target.graphJson));
  const targetCandidatesById = new Map(targetCandidates.map((candidate) => [candidate.id, candidate]));
  const relevantRows = rows.filter((row) => {
    const candidate = row.targetEntityId && targetCandidatesById.get(row.targetEntityId);
    return candidate && candidate.kind === row.targetEntityKind;
  });
  const groups = receptionGroups(relevantRows);
  const publicGroups = groups.slice(0, MAX_PUBLIC_ITEMS);
  const citingContexts = await loadCitationGraphContexts(
    DB,
    publicGroups.map((group) => group.row.citingSnapshotId),
  );
  const items = await Promise.all(publicGroups.map((group) => (
    hydrateItem(group.row, group.mentionCount, targetCandidatesById, citingContexts)
  )));
  const complete = analyzedPapers.size >= eligiblePapers.size;
  return {
    ...base,
    status: eligiblePapers.size > 0 && analyzedPapers.size === 0 ? "not_started" as const : "ready" as const,
    coverage: {
      eligibleActivePapers: eligiblePapers.size,
      analyzedActivePapers: analyzedPapers.size,
      complete,
      asOf: rows[0]?.processedAt ?? new Date().toISOString(),
    },
    summary: summary(relevantRows),
    items,
    total: groups.length,
    truncated: groups.length > items.length,
    warnings: complete ? [] : ["当前仅展示已完成分析的入库论文，仍有部分论文尚待分析。"],
  };
}

export async function GET(request: Request) {
  try {
    const url = new URL(request.url);
    const snapshotId = url.searchParams.get("snapshotId");
    if (url.searchParams.size !== 1 || !snapshotId || !SNAPSHOT_ID_RE.test(snapshotId)) {
      throw new ApiError(400, "INVALID_CITATION_RECEPTION_QUERY", "snapshotId must identify one evidence snapshot");
    }
    const { DB } = await getBindings();
    return Response.json(await publicReception(DB, await loadTarget(DB, snapshotId)));
  } catch (error) {
    return safeError(error);
  }
}

function parseProcessRequest(payload: Record<string, unknown>) {
  const keys = Object.keys(payload).sort().join(",");
  if ((keys !== "snapshotId" && keys !== "reprocess,snapshotId")
    || typeof payload.snapshotId !== "string" || !SNAPSHOT_ID_RE.test(payload.snapshotId)
    || ("reprocess" in payload && typeof payload.reprocess !== "boolean")) {
    throw new ApiError(400, "INVALID_CITATION_RECEPTION_PROCESS_REQUEST", "snapshotId must identify one evidence snapshot");
  }
  return { snapshotId: payload.snapshotId, reprocess: payload.reprocess === true };
}

async function receptionPost(request: Request) {
  try {
    assertSameOrigin(request);
    const { snapshotId, reprocess } = parseProcessRequest(await readJsonObject(request, MAX_PROCESS_BYTES));
    const { DB } = await getBindings();
    const target = await loadTarget(DB, snapshotId);
    const doi = authoritativeDoi(target);
    if (!doi) {
      throw new ApiError(409, "CITATION_TARGET_IDENTITY_UNAVAILABLE", "Target paper does not have an authoritative OpenAlex Work DOI");
    }
    const active = await loadActiveEvidenceRetrievalManifests(DB, PROJECT_ID);
    const activeSnapshotIds = active.map((item) => item.snapshotId);
    if (!await identityIsUnique(DB, doi, activeSnapshotIds, target.artifactId)) {
      throw new ApiError(409, "CITATION_TARGET_IDENTITY_AMBIGUOUS", "Multiple active local papers share this DOI");
    }
    const pairs = groupEligible((await loadEligibleRows(DB, doi, activeSnapshotIds))
      .filter((row) => row.citingSnapshotId !== snapshotId));
    const alreadyProcessed = reprocess ? new Set<string>() : await loadProcessedReferenceIds(DB, snapshotId);
    const pendingPairs = reprocess ? pairs : pairs.filter((pair) => !alreadyProcessed.has(pair[0].referenceId));
    const selected = pendingPairs.slice(0, MAX_PAIRS_PER_REQUEST);
    if (selected.length === 0) {
      return Response.json({
        ok: true,
        result: {
          targetSnapshotId: snapshotId,
          eligiblePairs: pairs.length,
          attemptedPairs: 0,
          processedPairs: 0,
          existingPairs: alreadyProcessed.size,
          failedPairs: 0,
          remainingPairs: 0,
        },
      });
    }
    const targetCandidates = buildCitationCandidateEntities(await parseStoredGraph(target.graphJson));
    const configuration = await getCompilerConfiguration();
    if (!configuration.sharedSecret) {
      throw new ApiError(503, "CITATION_ANALYZER_NOT_CONFIGURED", "Citation analysis requires the shared Compiler credential");
    }
    const client = new CompilerClient({ ...configuration, sharedSecret: configuration.sharedSecret });
    const citingContexts = await loadCitationGraphContexts(
      DB,
      selected.map((pair) => pair[0].citingSnapshotId),
    );
    const settled = await settleWithConcurrency(selected, 3, (pair) => (
      processPair(
        DB,
        client,
        target.snapshotId,
        targetCandidates,
        citingContexts.get(pair[0].citingSnapshotId)!,
        pair,
        reprocess,
        request.signal,
      )
    ));
    const processed = settled.filter((item) => item.status === "fulfilled" && item.value.status === "processed").length;
    const duplicates = settled.filter((item) => item.status === "fulfilled" && item.value.status === "duplicate").length;
    const failed = settled.filter((item) => item.status === "rejected").length;
    if (failed === selected.length && selected.length > 0) {
      const firstFailure = settled.find((item): item is PromiseRejectedResult => item.status === "rejected");
      throw firstFailure?.reason ?? new ApiError(503, "CITATION_ANALYSIS_FAILED", "Citation analysis failed");
    }
    return Response.json({
      ok: true,
      result: {
        targetSnapshotId: snapshotId,
        eligiblePairs: pairs.length,
        attemptedPairs: selected.length,
        processedPairs: processed,
        existingPairs: alreadyProcessed.size + duplicates,
        failedPairs: failed,
        remainingPairs: Math.max(0, pendingPairs.length - selected.length),
      },
    });
  } catch (error) {
    return safeError(error);
  }
}

/** Up to 12 model-checked pairs can outlast Cloudflare's 100 s limit; the browser asks for a stream. */
export async function POST(request: Request) {
  return wantsStreamedResponse(request) ? streamedResponse(request, receptionPost) : receptionPost(request);
}
