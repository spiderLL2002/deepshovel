import { canonicalStringify, type EvidenceGraph } from "@/lib/evidence-graph";
import {
  buildEvidenceRetrievalAssessments,
  buildEvidenceRetrievalProjection,
  EVIDENCE_RETRIEVAL_INDEX_VERSION,
  evidenceRetrievalAssessmentStatements,
  evidenceRetrievalJsonChunks,
  evidenceRetrievalProjectionStatements,
} from "@/lib/evidence-retrieval";
import {
  ApiError,
  getBindings,
  PROJECT_ID,
} from "@/lib/server/evidence-store";
import { enqueueWorkStatement } from "@/lib/server/work-queue";
import type { EvidenceImport, OpenAlexClassification, VerificationImport } from "./import-contract.ts";

/**
 * Background work created by an import, written in the same D1 batch as the
 * snapshot (a transactional outbox): retrieval indexing, then Claim-relation
 * analysis for Claim-bearing snapshots. Replays are INSERT OR IGNORE no-ops.
 */
function importWorkStatements(DB: D1Database, imported: EvidenceImport, createdAt: string) {
  return [
    enqueueWorkStatement(DB, PROJECT_ID, "retrieval_index", imported.snapshotId, createdAt),
    ...(imported.graph.claims.length
      ? [enqueueWorkStatement(DB, PROJECT_ID, "claim_relations", imported.snapshotId, createdAt)]
      : []),
  ];
}

type SnapshotRow = {
  id: string;
  rootArtifactVersionId: string;
  schemaVersion: string;
  graphSha256: string;
  countsJson: string;
  extractorRunId: string;
  extractorModel: string;
  extractorPromptVersion: string;
  createdAt: string;
};

type ReviewRow = {
  id: string;
  snapshotId: string;
  decision: "accepted" | "rejected" | "needs_revision" | "withdrawn";
  rationale: string;
  reviewer: string;
  createdAt: string;
};

type SnapshotListRow = SnapshotRow & {
  title: string;
  totalCount: number | null;
  scanHasMore: number;
  scanCreatedAt: string | null;
  scanSnapshotId: string | null;
  latestReviewId: string | null;
  latestReviewDecision: ReviewRow["decision"] | null;
  latestReviewRationale: string | null;
  latestReviewer: string | null;
  latestReviewCreatedAt: string | null;
  verificationRunCount: number;
  verificationLimitationCount: number;
};

type VerificationRow = {
  id: string;
  snapshotId: string;
  verifierRunId: string;
  verifierModel: string;
  verifierPromptVersion: string;
  reportSha256: string;
  createdAt: string;
};

type CitationSourceManifestRow = {
  snapshotId: string;
  schemaVersion: string;
  extractorVersion: string;
  sourceSha256: string;
  referenceCount: number;
  mentionCount: number;
};

type StoredArtifactVersionRow = {
  id: string;
  artifactId: string;
  sha256: string;
  mediaType: string;
  sourceLocator: string;
  artifactType: string;
};

const SELECT_SNAPSHOT = `SELECT id, root_artifact_version_id AS rootArtifactVersionId,
  schema_version AS schemaVersion, graph_sha256 AS graphSha256,
  counts_json AS countsJson, extractor_run_id AS extractorRunId,
  extractor_model AS extractorModel, extractor_prompt_version AS extractorPromptVersion,
  created_at AS createdAt FROM evidence_graph_snapshots`;

const SELECT_VERIFICATION = `SELECT id, snapshot_id AS snapshotId,
  verifier_run_id AS verifierRunId, verifier_model AS verifierModel,
  verifier_prompt_version AS verifierPromptVersion,
  report_sha256 AS reportSha256, created_at AS createdAt FROM verification_runs`;

function parseCounts(value: string) {
  try { return JSON.parse(value) as Record<string, number>; } catch { return {}; }
}

function snapshotReceipt(
  row: SnapshotRow,
  latestReviewEvent: ReviewRow | null = null,
  verificationRunCount = 0,
  verificationLimitationCount = 0,
) {
  const runCount = Math.max(0, Number(verificationRunCount) || 0);
  const latestReview = latestReviewEvent?.decision === "withdrawn" ? null : latestReviewEvent;
  return {
    id: row.id,
    title: "title" in row ? row.title : undefined,
    rootArtifactVersionId: row.rootArtifactVersionId,
    schemaVersion: row.schemaVersion,
    graphSha256: row.graphSha256,
    counts: parseCounts(row.countsJson),
    extraction: {
      runId: row.extractorRunId,
      model: row.extractorModel,
      promptVersion: row.extractorPromptVersion,
    },
    machineVerification: {
      status: runCount > 0 ? "assessed" as const : "unassessed" as const,
      runCount,
      limitationCount: Math.max(0, Number(verificationLimitationCount) || 0),
    },
    latestReview,
    detailUrl: `/api/evidence-graphs/${encodeURIComponent(row.id)}`,
    createdAt: row.createdAt,
  };
}

function verificationReceipt(row: VerificationRow) {
  return {
    id: row.id,
    snapshotId: row.snapshotId,
    runId: row.verifierRunId,
    model: row.verifierModel,
    promptVersion: row.verifierPromptVersion,
    reportSha256: row.reportSha256,
    createdAt: row.createdAt,
  };
}

async function assertStoredArtifactVersion(DB: D1Database, graph: EvidenceGraph) {
  const artifact = graph.artifacts[0];
  const version = graph.artifact_versions[0];
  const stored = await DB.prepare(`SELECT v.id, v.artifact_id AS artifactId, v.sha256,
      v.media_type AS mediaType, v.source_locator AS sourceLocator,
      a.artifact_type AS artifactType
      FROM artifact_versions v JOIN artifacts a ON a.id = v.artifact_id
      WHERE a.project_id = ? AND v.id IN (SELECT value FROM json_each(?))`)
    .bind(PROJECT_ID, JSON.stringify([version.id])).all<StoredArtifactVersionRow>();
  const row = stored.results.find((item) => item.id === version.id);
  if (!row) {
    throw new ApiError(422, "ARTIFACT_VERSION_NOT_STORED", `Graph references an unstored artifact version: ${version.id}`);
  }
  const mismatch = row.artifactId !== version.artifact_id
    || row.sha256 !== version.sha256
    || row.mediaType !== version.media_type
    || row.artifactType !== artifact.artifact_type
    || (version.locator !== null && row.sourceLocator !== version.locator);
  if (mismatch) {
    throw new ApiError(422, "ARTIFACT_VERSION_MISMATCH", `Stored artifact metadata or bytes do not match graph provenance: ${version.id}`);
  }
}

async function existingVerification(
  DB: D1Database,
  snapshotId: string,
  verification: VerificationImport,
) {
  const matches = await DB.prepare(`${SELECT_VERIFICATION} WHERE project_id = ?
    AND (verifier_run_id = ? OR report_sha256 = ?)`)
    .bind(PROJECT_ID, verification.runId, verification.reportSha256).all<VerificationRow>();
  const byRun = matches.results.find((row) => row.verifierRunId === verification.runId);
  const byReport = matches.results.find((row) => row.reportSha256 === verification.reportSha256);
  if (byRun) {
    if (byRun.reportSha256 !== verification.reportSha256) {
      throw new ApiError(409, "VERIFIER_RUN_REUSED", "Verifier run ID already belongs to a different report");
    }
    if (byRun.snapshotId !== snapshotId) {
      throw new ApiError(409, "VERIFIER_RUN_REUSED", "Verifier run ID already belongs to a different graph snapshot");
    }
    return byRun;
  }
  if (byReport) {
    if (byReport.verifierRunId !== verification.runId) {
      throw new ApiError(409, "VERIFICATION_REPORT_REUSED", "Verifier report already belongs to a different run ID");
    }
    if (byReport.snapshotId !== snapshotId) {
      throw new ApiError(409, "VERIFICATION_REPORT_REUSED", "Verifier report already belongs to a different graph snapshot");
    }
    return byReport;
  }
  return null;
}

function verificationStatements(
  DB: D1Database,
  snapshotId: string,
  verification: VerificationImport,
  actor: string,
  graph: EvidenceGraph,
) {
  return [
    DB.prepare(`INSERT INTO verification_runs
      (id, project_id, snapshot_id, verifier_run_id, verifier_model,
       verifier_prompt_version, report_sha256, report_json, created_by, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`).bind(
      verification.id,
      PROJECT_ID,
      snapshotId,
      verification.runId,
      verification.model,
      verification.promptVersion,
      verification.reportSha256,
      verification.reportJson,
      actor,
      verification.createdAt,
    ),
    ...evidenceRetrievalAssessmentStatements(
      DB,
      PROJECT_ID,
      buildEvidenceRetrievalAssessments(graph, snapshotId, verification.report, {
        verificationId: verification.id,
        verifierPromptVersion: verification.promptVersion,
        createdAt: verification.createdAt,
      }),
    ),
    DB.prepare(`INSERT INTO audit_events
      (id, project_id, event_type, entity_type, entity_id, actor, payload_json)
      VALUES (?, ?, 'verification.imported', 'verification_run', ?, ?, ?)`).bind(
      crypto.randomUUID(),
      PROJECT_ID,
      verification.id,
      actor,
      canonicalStringify({
        snapshotId,
        runId: verification.runId,
        model: verification.model,
        reportSha256: verification.reportSha256,
        assessmentCount: verification.report.assessments.length,
      }),
    ),
  ];
}

export function openAlexClassificationStatements(
  DB: D1Database,
  snapshotId: string,
  classification: OpenAlexClassification,
  createdAt: string,
) {
  return [
    DB.prepare(`INSERT OR IGNORE INTO openalex_paper_classifications
      (snapshot_id, project_id, source, classifier, openalex_work_id, matched_doi, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?)`).bind(
      snapshotId,
      PROJECT_ID,
      classification.source,
      classification.classifier,
      classification.openAlexWorkId,
      classification.matchedDoi,
      createdAt,
    ),
    ...classification.topics.map((topic, ordinal) => DB.prepare(`INSERT OR IGNORE INTO openalex_paper_topics
      (snapshot_id, topic_id, project_id, display_name, score, is_primary, ordinal,
       subfield_id, subfield_name, field_id, field_name, domain_id, domain_name, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`).bind(
      snapshotId,
      topic.id,
      PROJECT_ID,
      topic.displayName,
      topic.score,
      ordinal === 0 ? 1 : 0,
      ordinal,
      topic.subfield.id,
      topic.subfield.displayName,
      topic.field.id,
      topic.field.displayName,
      topic.domain.id,
      topic.domain.displayName,
      createdAt,
    )),
  ];
}

export async function persistOpenAlexClassification(
  DB: D1Database,
  snapshotId: string,
  classification: OpenAlexClassification,
  createdAt = new Date().toISOString(),
) {
  await DB.batch(openAlexClassificationStatements(DB, snapshotId, classification, createdAt));
}

async function appendVerification(
  DB: D1Database,
  snapshotId: string,
  verification: VerificationImport,
  actor: string,
  graph: EvidenceGraph,
) {
  const existing = await existingVerification(DB, snapshotId, verification);
  if (existing) {
    await DB.batch([
      ...evidenceRetrievalAssessmentStatements(
        DB,
        PROJECT_ID,
        buildEvidenceRetrievalAssessments(graph, snapshotId, verification.report, {
          verificationId: existing.id,
          verifierPromptVersion: existing.verifierPromptVersion,
          createdAt: existing.createdAt,
        }),
      ),
    ]);
    return { row: existing, duplicate: true };
  }
  try {
    await DB.batch(verificationStatements(DB, snapshotId, verification, actor, graph));
  } catch (error) {
    const winner = await existingVerification(DB, snapshotId, verification);
    if (winner) return { row: winner, duplicate: true };
    throw error;
  }
  return {
    row: {
      id: verification.id,
      snapshotId,
      verifierRunId: verification.runId,
      verifierModel: verification.model,
      verifierPromptVersion: verification.promptVersion,
      reportSha256: verification.reportSha256,
      createdAt: verification.createdAt,
    } satisfies VerificationRow,
    duplicate: false,
  };
}

async function persistenceResult(
  DB: D1Database,
  row: SnapshotRow,
  duplicate: boolean,
  verification: Awaited<ReturnType<typeof appendVerification>> | null,
) {
  const countRow = await DB.prepare(`SELECT COUNT(*) AS runCount FROM verification_runs
    WHERE project_id = ? AND snapshot_id = ?`)
    .bind(PROJECT_ID, row.id).first<{ runCount: number }>();
  const verificationRunCount = Number(countRow?.runCount ?? 0);
  return {
    body: {
      ok: true as const,
      snapshot: snapshotReceipt(row, null, verificationRunCount),
      verification: verification
        ? { ...verificationReceipt(verification.row), duplicate: verification.duplicate }
        : null,
      duplicate,
    },
    status: verification && !verification.duplicate ? 201 as const : duplicate ? 200 as const : 201 as const,
  };
}

export type SnapshotListFilter = "all" | "unreviewed" | "confirmed";

export type SnapshotListCursor = { version: 1; createdAt: string; snapshotId: string; query: string; status: SnapshotListFilter };

export function encodeSnapshotListCursor(cursor: SnapshotListCursor) {
  let binary = "";
  for (const byte of new TextEncoder().encode(JSON.stringify(cursor))) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/u, "");
}

export async function listEvidenceGraphSnapshotPage({
  query = "", status = "all", cursor = null, pageSize = 20,
}: { query?: string; status?: SnapshotListFilter; cursor?: SnapshotListCursor | null; pageSize?: number } = {}) {
  const { DB } = await getBindings();
  const bindings: Array<string | number> = [PROJECT_ID];
  const predicates = ["project_id = ?"];
  if (status !== "all") { predicates.push("review_status = ?"); bindings.push(status); }
  if (cursor) { predicates.push("(created_at, snapshot_id) < (?, ?)"); bindings.push(cursor.createdAt, cursor.snapshotId); }
  const normalizedQuery = query.replace(/[A-Z]/g, (character) => character.toLowerCase());
  if (query) bindings.push(normalizedQuery, normalizedQuery);
  bindings.push(pageSize + 1);
  const totalSql = query ? "NULL" : status === "all"
    ? "(SELECT COALESCE(SUM(snapshot_count),0) FROM snapshot_list_counts WHERE project_id=?)"
    : "(SELECT COALESCE((SELECT snapshot_count FROM snapshot_list_counts WHERE project_id=? AND status=?),0))";
  if (!query) { bindings.push(PROJECT_ID); if (status !== "all") bindings.push(status); }
  const snapshots = await DB.prepare(`WITH candidates AS MATERIALIZED (
    SELECT * FROM snapshot_list_summaries WHERE ${predicates.join(" AND ")}
    ORDER BY created_at DESC,snapshot_id DESC LIMIT ${query ? 1024 : pageSize + 1}
  ), page_snapshots AS MATERIALIZED (
    SELECT * FROM candidates ${query ? "WHERE instr(lower(title),?)>0 OR instr(lower(original_file_name),?)>0" : ""}
    ORDER BY created_at DESC,snapshot_id DESC LIMIT ?
  ), metadata AS (SELECT ${totalSql} AS totalCount,
    ${query ? "(SELECT COUNT(*)=1024 FROM candidates)" : "0"} AS scanHasMore,
    (SELECT created_at FROM candidates ORDER BY created_at,snapshot_id LIMIT 1) AS scanCreatedAt,
    (SELECT snapshot_id FROM candidates ORDER BY created_at,snapshot_id LIMIT 1) AS scanSnapshotId)
  SELECT metadata.*, s.id, p.title,
    s.root_artifact_version_id AS rootArtifactVersionId,
    s.schema_version AS schemaVersion,s.graph_sha256 AS graphSha256,s.counts_json AS countsJson,
    s.extractor_run_id AS extractorRunId,s.extractor_model AS extractorModel,
    s.extractor_prompt_version AS extractorPromptVersion,s.created_at AS createdAt,
    r.id AS latestReviewId,r.decision AS latestReviewDecision,r.rationale AS latestReviewRationale,
    r.reviewer AS latestReviewer,r.created_at AS latestReviewCreatedAt,
    (SELECT COUNT(*) FROM verification_runs runs WHERE runs.project_id=p.project_id AND runs.snapshot_id=s.id) AS verificationRunCount,
    COALESCE(vs.limitation_count,0) AS verificationLimitationCount
  FROM metadata LEFT JOIN page_snapshots p ON 1=1
  LEFT JOIN evidence_graph_snapshots s ON s.id=p.snapshot_id
  LEFT JOIN graph_review_events r ON r.id=p.latest_review_id
  LEFT JOIN verification_runs vr ON vr.id=(SELECT id FROM verification_runs WHERE project_id=p.project_id AND snapshot_id=s.id ORDER BY created_at DESC,id DESC LIMIT 1)
  LEFT JOIN library_verification_summaries vs ON vs.verification_id=vr.id
  ORDER BY p.created_at DESC,p.snapshot_id DESC`).bind(...bindings).all<SnapshotListRow>();
  const rows = snapshots.results.filter((row) => row.id !== null);
  const visible = rows.slice(0, pageSize);
  const items = visible.map((row) => {
    const latestReviewEvent = row.latestReviewId && row.latestReviewDecision
      ? { id: row.latestReviewId, snapshotId: row.id, decision: row.latestReviewDecision,
          rationale: row.latestReviewRationale ?? "", reviewer: row.latestReviewer ?? "", createdAt: row.latestReviewCreatedAt ?? "" } satisfies ReviewRow
      : null;
    return snapshotReceipt(row, latestReviewEvent, row.verificationRunCount, row.verificationLimitationCount);
  });
  const metadata = snapshots.results[0];
  const hasMore = rows.length > pageSize || Boolean(metadata?.scanHasMore);
  const last = rows.length <= pageSize && metadata?.scanHasMore
    ? { id: metadata.scanSnapshotId!, createdAt: metadata.scanCreatedAt! } : visible.at(-1);
  const total = metadata?.totalCount ?? null;
  return {
    snapshots: items,
    pageInfo: { page: 1, pageSize, total, totalPages: total === null ? null : Math.max(1, Math.ceil(total / pageSize)), hasMore,
      nextCursor: hasMore && last ? encodeSnapshotListCursor({ version: 1, createdAt: last.createdAt, snapshotId: last.id, query, status }) : null },
  };
}

export async function listEvidenceGraphSnapshots() {
  return (await listEvidenceGraphSnapshotPage({ pageSize: 50 })).snapshots;
}

async function ensureRetrievalProjection(
  DB: D1Database,
  imported: EvidenceImport,
  stored: { createdAt: string; extractorPromptVersion: string },
) {
  const manifest = await DB.prepare(`SELECT snapshot_id AS snapshotId, index_version AS indexVersion
    FROM evidence_retrieval_manifests WHERE project_id = ? AND snapshot_id = ? LIMIT 1`)
    .bind(PROJECT_ID, imported.snapshotId).first<{ snapshotId: string; indexVersion: string }>();
  if (manifest) {
    if (manifest.indexVersion !== EVIDENCE_RETRIEVAL_INDEX_VERSION) {
      throw new ApiError(409, "SEARCH_INDEX_VERSION_MISMATCH", "Snapshot already has an incompatible immutable search projection");
    }
    return;
  }
  const projection = buildEvidenceRetrievalProjection(imported.graph, {
    snapshotId: imported.snapshotId,
    rootArtifactVersionId: imported.rootArtifactVersionId,
    graphSha256: imported.graphSha256,
    extractorPromptVersion: stored.extractorPromptVersion,
    createdAt: stored.createdAt,
  });
  try {
    await DB.batch(evidenceRetrievalProjectionStatements(DB, PROJECT_ID, projection));
  } catch (error) {
    const winner = await DB.prepare(`SELECT index_version AS indexVersion FROM evidence_retrieval_manifests
      WHERE project_id = ? AND snapshot_id = ? LIMIT 1`)
      .bind(PROJECT_ID, imported.snapshotId).first<{ indexVersion: string }>();
    if (winner?.indexVersion === EVIDENCE_RETRIEVAL_INDEX_VERSION) return;
    throw error;
  }
}

const SELECT_CITATION_SOURCE_MANIFEST = `SELECT snapshot_id AS snapshotId,
  schema_version AS schemaVersion, extractor_version AS extractorVersion,
  source_sha256 AS sourceSha256, reference_count AS referenceCount,
  mention_count AS mentionCount FROM citation_source_manifests`;

function assertCitationSourceCompatible(
  stored: CitationSourceManifestRow,
  imported: NonNullable<EvidenceImport["citationSource"]>,
) {
  const projection = imported.projection;
  if (stored.schemaVersion !== projection.schemaVersion
    || stored.extractorVersion !== projection.extractorVersion) {
    throw new ApiError(
      409,
      "CITATION_SOURCE_VERSION_MISMATCH",
      "Snapshot already has an incompatible immutable citation-source projection",
    );
  }
  if (stored.sourceSha256 !== imported.sourceSha256
    || Number(stored.referenceCount) !== projection.references.length
    || Number(stored.mentionCount) !== projection.mentions.length) {
    throw new ApiError(
      409,
      "CITATION_SOURCE_CONFLICT",
      "Snapshot already has a different immutable citation-source projection",
    );
  }
}

/** Statements are intentionally append-only and may be placed in the graph's
 * creation batch. JSON-table chunks keep large bibliographies below D1's
 * parameter boundary without weakening atomicity. */
function citationSourceStatements(
  DB: D1Database,
  imported: EvidenceImport,
  createdAt: string,
) {
  const source = imported.citationSource;
  if (!source) return [];
  const projection = source.projection;
  const referenceRows = projection.references.map((reference) => ({
    id: reference.id,
    ordinal: reference.ordinal,
    label: reference.label,
    rawText: reference.rawText,
    normalizedDoi: reference.doi,
    year: reference.year,
    sourceBlockId: reference.sourceBlockId,
    page: reference.page,
  }));
  const mentionRows = projection.mentions.map((mention) => ({
    id: mention.id,
    referenceId: mention.referenceId,
    marker: mention.marker,
    exactQuote: mention.exactQuote,
    contextText: mention.contextText,
    sourceBlockId: mention.sourceBlockId,
    contextBlockIdsJson: canonicalStringify(mention.contextBlockIds),
    page: mention.page,
    bboxJson: mention.bbox === null ? null : canonicalStringify(mention.bbox),
    charStart: mention.charStart,
    charEnd: mention.charEnd,
    groupSize: mention.groupSize,
    candidateFunctionsJson: canonicalStringify(mention.candidateFunctions),
  }));
  const referenceInsert = `INSERT INTO bibliographic_references
    (id, project_id, citing_snapshot_id, ordinal, label, raw_text, normalized_doi,
     year, source_block_id, page, created_at)
    SELECT json_extract(value, '$.id'), ?, ?, json_extract(value, '$.ordinal'),
     json_extract(value, '$.label'), json_extract(value, '$.rawText'),
     json_extract(value, '$.normalizedDoi'), json_extract(value, '$.year'),
     json_extract(value, '$.sourceBlockId'), json_extract(value, '$.page'), ?
     FROM json_each(?)`;
  const mentionInsert = `INSERT INTO citation_mentions
    (id, project_id, citing_snapshot_id, reference_id, marker, exact_quote,
     context_text, source_block_id, context_block_ids_json, page, bbox_json,
     char_start, char_end, group_size, candidate_functions_json, created_at)
    SELECT json_extract(value, '$.id'), ?, ?, json_extract(value, '$.referenceId'),
     json_extract(value, '$.marker'), json_extract(value, '$.exactQuote'),
     json_extract(value, '$.contextText'), json_extract(value, '$.sourceBlockId'),
     json_extract(value, '$.contextBlockIdsJson'), json_extract(value, '$.page'),
     json_extract(value, '$.bboxJson'), json_extract(value, '$.charStart'),
     json_extract(value, '$.charEnd'), json_extract(value, '$.groupSize'),
     json_extract(value, '$.candidateFunctionsJson'), ? FROM json_each(?)`;
  return [
    DB.prepare(`INSERT INTO citation_source_manifests
      (snapshot_id, project_id, schema_version, extractor_version, source_sha256,
       reference_count, mention_count, warnings_json, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`).bind(
      imported.snapshotId,
      PROJECT_ID,
      projection.schemaVersion,
      projection.extractorVersion,
      source.sourceSha256,
      projection.references.length,
      projection.mentions.length,
      canonicalStringify(projection.warnings),
      createdAt,
    ),
    ...evidenceRetrievalJsonChunks(referenceRows).map((chunk) => DB.prepare(referenceInsert)
      .bind(PROJECT_ID, imported.snapshotId, createdAt, chunk)),
    ...evidenceRetrievalJsonChunks(mentionRows).map((chunk) => DB.prepare(mentionInsert)
      .bind(PROJECT_ID, imported.snapshotId, createdAt, chunk)),
  ];
}

async function ensureCitationSourceProjection(
  DB: D1Database,
  imported: EvidenceImport,
  createdAt: string,
) {
  const source = imported.citationSource;
  if (!source) return;
  const stored = await DB.prepare(`${SELECT_CITATION_SOURCE_MANIFEST}
    WHERE project_id = ? AND snapshot_id = ? LIMIT 1`)
    .bind(PROJECT_ID, imported.snapshotId).first<CitationSourceManifestRow>();
  if (stored) {
    assertCitationSourceCompatible(stored, source);
    return;
  }
  try {
    await DB.batch(citationSourceStatements(DB, imported, createdAt));
  } catch (error) {
    const winner = await DB.prepare(`${SELECT_CITATION_SOURCE_MANIFEST}
      WHERE project_id = ? AND snapshot_id = ? LIMIT 1`)
      .bind(PROJECT_ID, imported.snapshotId).first<CitationSourceManifestRow>();
    if (winner) {
      assertCitationSourceCompatible(winner, source);
      return;
    }
    throw error;
  }
}

async function finishDuplicateSnapshotImport(
  DB: D1Database,
  imported: EvidenceImport,
  actor: string,
  stored: SnapshotRow,
) {
  if (stored.rootArtifactVersionId !== imported.rootArtifactVersionId) {
    throw new ApiError(409, "SNAPSHOT_ROOT_MISMATCH", "This graph already exists with a different root artifact version");
  }
  await ensureRetrievalProjection(DB, imported, {
    createdAt: stored.createdAt,
    extractorPromptVersion: stored.extractorPromptVersion,
  });
  await ensureCitationSourceProjection(DB, imported, stored.createdAt);
  if (imported.openAlex) {
    await persistOpenAlexClassification(DB, stored.id, imported.openAlex, stored.createdAt);
  }
  // Snapshots imported before the work queue existed gain their outbox rows.
  await DB.batch(importWorkStatements(DB, imported, new Date().toISOString()));
  const verification = imported.verification
    ? await appendVerification(DB, stored.id, imported.verification, actor, imported.graph)
    : null;
  return persistenceResult(DB, stored, true, verification);
}

/** Persist one validated import without ever merging verifier data into graph_json. */
export async function persistEvidenceImport(imported: EvidenceImport, actor: string) {
  const { DB } = await getBindings();
  await assertStoredArtifactVersion(DB, imported.graph);

  const identities = await DB.prepare(`${SELECT_SNAPSHOT} WHERE project_id = ?
    AND (graph_sha256 = ? OR extractor_run_id = ?)`)
    .bind(PROJECT_ID, imported.graphSha256, imported.extraction.runId).all<SnapshotRow>();
  const duplicate = identities.results.find((row) => row.graphSha256 === imported.graphSha256);
  const reusedRun = identities.results.find((row) => row.extractorRunId === imported.extraction.runId);
  if (reusedRun && reusedRun.graphSha256 !== imported.graphSha256) {
    throw new ApiError(409, "EXTRACTOR_RUN_REUSED", "Extractor run ID already belongs to a different graph");
  }
  if (duplicate) {
    return finishDuplicateSnapshotImport(DB, imported, actor, duplicate);
  }
  if (imported.verification) {
    await existingVerification(DB, imported.snapshotId, imported.verification);
  }

  try {
    const projection = buildEvidenceRetrievalProjection(imported.graph, {
      snapshotId: imported.snapshotId,
      rootArtifactVersionId: imported.rootArtifactVersionId,
      graphSha256: imported.graphSha256,
      extractorPromptVersion: imported.extraction.promptVersion,
      createdAt: imported.createdAt,
    });
    await DB.batch([
      DB.prepare(`INSERT INTO evidence_graph_snapshots
        (id, project_id, root_artifact_version_id, schema_version, graph_sha256,
         graph_json, counts_json, extractor_run_id, extractor_model,
         extractor_prompt_version, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`).bind(
        imported.snapshotId,
        PROJECT_ID,
        imported.rootArtifactVersionId,
        imported.graph.schema_version,
        imported.graphSha256,
        imported.graphJson,
        imported.countsJson,
        imported.extraction.runId,
        imported.extraction.model,
        imported.extraction.promptVersion,
        actor,
        imported.createdAt,
      ),
      ...evidenceRetrievalProjectionStatements(DB, PROJECT_ID, projection),
      ...citationSourceStatements(DB, imported, imported.createdAt),
      ...(imported.openAlex
        ? openAlexClassificationStatements(DB, imported.snapshotId, imported.openAlex, imported.createdAt)
        : []),
      DB.prepare(`INSERT INTO audit_events
        (id, project_id, event_type, entity_type, entity_id, actor, payload_json)
        VALUES (?, ?, 'evidence_graph.imported', 'evidence_graph_snapshot', ?, ?, ?)`).bind(
        crypto.randomUUID(),
        PROJECT_ID,
        imported.snapshotId,
        actor,
        canonicalStringify({
          graphSha256: imported.graphSha256,
          rootArtifactVersionId: imported.rootArtifactVersionId,
          runId: imported.extraction.runId,
          model: imported.extraction.model,
          counts: imported.counts,
          ...(imported.compilerAudit ? { compilerAudit: imported.compilerAudit } : {}),
        }),
      ),
      ...(imported.verification
        ? verificationStatements(DB, imported.snapshotId, imported.verification, actor, imported.graph)
        : []),
      ...importWorkStatements(DB, imported, imported.createdAt),
    ]);
  } catch (error) {
    const winner = await DB.prepare(`${SELECT_SNAPSHOT} WHERE project_id = ? AND graph_sha256 = ? LIMIT 1`)
      .bind(PROJECT_ID, imported.graphSha256).first<SnapshotRow>();
    if (winner) {
      return finishDuplicateSnapshotImport(DB, imported, actor, winner);
    }
    const racedRun = await DB.prepare(`${SELECT_SNAPSHOT} WHERE project_id = ? AND extractor_run_id = ? LIMIT 1`)
      .bind(PROJECT_ID, imported.extraction.runId).first<SnapshotRow>();
    if (racedRun) {
      throw new ApiError(409, "EXTRACTOR_RUN_REUSED", "Extractor run ID already belongs to a different graph");
    }
    if (imported.verification) {
      await existingVerification(DB, imported.snapshotId, imported.verification);
    }
    throw error;
  }

  const snapshot: SnapshotRow = {
    id: imported.snapshotId,
    rootArtifactVersionId: imported.rootArtifactVersionId,
    schemaVersion: imported.graph.schema_version,
    graphSha256: imported.graphSha256,
    countsJson: imported.countsJson,
    extractorRunId: imported.extraction.runId,
    extractorModel: imported.extraction.model,
    extractorPromptVersion: imported.extraction.promptVersion,
    createdAt: imported.createdAt,
  };
  const verification = imported.verification
    ? {
        row: {
          id: imported.verification.id,
          snapshotId: imported.snapshotId,
          verifierRunId: imported.verification.runId,
          verifierModel: imported.verification.model,
          verifierPromptVersion: imported.verification.promptVersion,
          reportSha256: imported.verification.reportSha256,
          createdAt: imported.createdAt,
        } satisfies VerificationRow,
        duplicate: false,
      }
    : null;
  return persistenceResult(DB, snapshot, false, verification);
}
