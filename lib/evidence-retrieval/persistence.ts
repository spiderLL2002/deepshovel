import { canonicalStringify } from "../evidence-graph/canonical.ts";
import {
  parseEvidenceRetrievalAssessmentDto,
  parseEvidenceRetrievalDocumentDto,
  parseEvidenceRetrievalManifestDto,
} from "./contract.ts";
import {
  toEvidenceRetrievalAssessmentDto,
  toEvidenceRetrievalDocumentDto,
  toEvidenceRetrievalManifestDto,
} from "./projection.ts";
import {
  EVIDENCE_RETRIEVAL_INDEX_VERSION,
  type EvidenceRetrievalAssessment,
  type EvidenceRetrievalAssessmentDto,
  type EvidenceRetrievalDocument,
  type EvidenceRetrievalDocumentDto,
  type EvidenceRetrievalIndexBundle,
  type EvidenceRetrievalManifest,
  type EvidenceRetrievalManifestDto,
  type EvidenceRetrievalProjection,
  type EvidenceRetrievalSnapshotBundle,
  type EvidenceSearchRequest,
  type QueryClaimRef,
  type ResolvedEvidenceSearchCorpus,
} from "./types.ts";

const MAX_PROJECTION_JSON_BYTES = 512 * 1024;
const MAX_BUNDLE_SNAPSHOTS = 4_096;
const MAX_INDEX_BUNDLE_SNAPSHOTS = 16;
const utf8 = new TextEncoder();

export class EvidenceRetrievalStoreError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "EvidenceRetrievalStoreError";
    this.status = status;
    this.code = code;
  }
}

function compareText(left: string, right: string) {
  return left < right ? -1 : left > right ? 1 : 0;
}

/** Bound every JSON-table parameter without weakening the surrounding D1 batch. */
export function evidenceRetrievalJsonChunks(values: unknown[]) {
  const chunks: string[] = [];
  let rows: string[] = [];
  let byteLength = 2;
  for (const value of values) {
    const row = canonicalStringify(value);
    const rowBytes = utf8.encode(row).byteLength;
    if (rowBytes + 2 > MAX_PROJECTION_JSON_BYTES) {
      throw new TypeError("One evidence-retrieval projection row exceeds the D1 JSON boundary");
    }
    const separatorBytes = rows.length === 0 ? 0 : 1;
    if (rows.length > 0 && byteLength + separatorBytes + rowBytes > MAX_PROJECTION_JSON_BYTES) {
      chunks.push(`[${rows.join(",")}]`);
      rows = [];
      byteLength = 2;
    }
    rows.push(row);
    byteLength += (rows.length === 1 ? 0 : 1) + rowBytes;
  }
  if (rows.length > 0) chunks.push(`[${rows.join(",")}]`);
  return chunks;
}

function insertJsonChunks(
  DB: D1Database,
  projectId: string,
  sql: string,
  rows: unknown[],
) {
  return evidenceRetrievalJsonChunks(rows).map((chunk) => DB.prepare(sql).bind(projectId, chunk));
}

/** All statements belong in the same snapshot-import D1 batch. */
export function evidenceRetrievalProjectionStatements(
  DB: D1Database,
  projectId: string,
  projection: EvidenceRetrievalProjection,
) {
  const documentRows = projection.documents.map((document) => ({
    snapshotId: document.snapshotId,
    paperId: document.paperId,
    paperFamilyId: document.paperFamilyId,
    claimId: document.claimId,
    scopedClaimId: document.scopedClaimId,
    propositionKey: document.propositionKey,
    document: parseEvidenceRetrievalDocumentDto(toEvidenceRetrievalDocumentDto(document)),
    indexVersion: document.indexVersion,
    createdAt: projection.manifest.createdAt,
  }));
  const documentInsert = `INSERT INTO evidence_retrieval_documents
    (project_id, snapshot_id, paper_id, paper_family_id, claim_id, scoped_claim_id,
     proposition_key, document_json, index_version, created_at)
    SELECT ?, json_extract(value, '$.snapshotId'), json_extract(value, '$.paperId'),
     json_extract(value, '$.paperFamilyId'), json_extract(value, '$.claimId'),
     json_extract(value, '$.scopedClaimId'), json_extract(value, '$.propositionKey'),
     json_extract(value, '$.document'), json_extract(value, '$.indexVersion'),
     json_extract(value, '$.createdAt') FROM json_each(?)`;
  const manifest = projection.manifest;
  const manifestDto = parseEvidenceRetrievalManifestDto(toEvidenceRetrievalManifestDto(manifest));
  return [
    ...insertJsonChunks(DB, projectId, documentInsert, documentRows),
    // The manifest is the immutable completion marker. Zero-document snapshots
    // still write it, so a new empty extraction suppresses older Claims.
    DB.prepare(`INSERT INTO evidence_retrieval_manifests
      (snapshot_id, project_id, paper_id, paper_family_id, root_artifact_version_id,
       root_artifact_sha256, graph_sha256, graph_schema_version,
       extractor_prompt_version, index_version, document_count, manifest_json, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`).bind(
      manifest.snapshotId,
      projectId,
      manifest.paperId,
      manifest.paperFamilyId,
      manifest.rootArtifactVersionId,
      manifest.rootArtifactSha256,
      manifest.graphSha256,
      manifest.graphSchemaVersion,
      manifest.extractorPromptVersion,
      manifest.indexVersion,
      manifest.documentCount,
      canonicalStringify(manifestDto),
      manifest.createdAt,
    ),
  ];
}

export function evidenceRetrievalAssessmentStatements(
  DB: D1Database,
  projectId: string,
  assessments: EvidenceRetrievalAssessment[],
) {
  const rows = assessments.map((assessment) => ({
    verificationId: assessment.verificationId,
    snapshotId: assessment.snapshotId,
    assessmentId: assessment.assessmentId,
    claimId: assessment.claimId,
    assessment: parseEvidenceRetrievalAssessmentDto(toEvidenceRetrievalAssessmentDto(assessment)),
    indexVersion: assessment.indexVersion,
    createdAt: assessment.createdAt,
  }));
  const insert = `INSERT OR IGNORE INTO evidence_retrieval_assessments
    (project_id, verification_id, snapshot_id, assessment_id, claim_id,
     assessment_json, index_version, created_at)
    SELECT ?, json_extract(value, '$.verificationId'), json_extract(value, '$.snapshotId'),
     json_extract(value, '$.assessmentId'), json_extract(value, '$.claimId'),
     json_extract(value, '$.assessment'), json_extract(value, '$.indexVersion'),
     json_extract(value, '$.createdAt') FROM json_each(?)`;
  return insertJsonChunks(DB, projectId, insert, rows);
}

type ManifestRow = {
  snapshotId: string;
  paperId: string;
  paperFamilyId: string;
  rootArtifactVersionId: string;
  rootArtifactSha256: string;
  graphSha256: string;
  graphSchemaVersion: string;
  extractorPromptVersion: string;
  indexVersion: string;
  documentCount: number;
  manifestJson: string;
  createdAt: string;
};

const MANIFEST_COLUMNS = `m.snapshot_id AS snapshotId, m.paper_id AS paperId,
  m.paper_family_id AS paperFamilyId, m.root_artifact_version_id AS rootArtifactVersionId,
  m.root_artifact_sha256 AS rootArtifactSha256, m.graph_sha256 AS graphSha256,
  m.graph_schema_version AS graphSchemaVersion,
  m.extractor_prompt_version AS extractorPromptVersion, m.index_version AS indexVersion,
  m.document_count AS documentCount, m.manifest_json AS manifestJson, m.created_at AS createdAt`;

function manifestFromRow(row: ManifestRow): EvidenceRetrievalManifest {
  const dto = parseEvidenceRetrievalManifestDto(parseJson<EvidenceRetrievalManifestDto>(
    row.manifestJson,
    "evidence_retrieval_manifests.manifest_json",
  ));
  if (row.indexVersion !== EVIDENCE_RETRIEVAL_INDEX_VERSION || dto.index_version !== row.indexVersion) {
    throw new EvidenceRetrievalStoreError(500, "RETRIEVAL_INDEX_VERSION_MISMATCH", "Stored retrieval manifest has an incompatible index version");
  }
  const columns = [
    ["snapshot_id", dto.snapshot_id, row.snapshotId],
    ["paper_id", dto.paper_id, row.paperId],
    ["paper_family_id", dto.paper_family_id, row.paperFamilyId],
    ["root_artifact_version_id", dto.root_artifact_version_id, row.rootArtifactVersionId],
    ["root_artifact_sha256", dto.root_artifact_sha256, row.rootArtifactSha256],
    ["graph_sha256", dto.graph_sha256, row.graphSha256],
    ["graph_schema_version", dto.graph_schema_version, row.graphSchemaVersion],
    ["extractor_prompt_version", dto.extractor_prompt_version, row.extractorPromptVersion],
    ["document_count", dto.document_count, Number(row.documentCount)],
    ["created_at", dto.created_at, row.createdAt],
  ] as const;
  const mismatch = columns.find(([, jsonValue, columnValue]) => jsonValue !== columnValue);
  if (mismatch) {
    throw new EvidenceRetrievalStoreError(
      500,
      "STORED_RETRIEVAL_PROJECTION_INVALID",
      `evidence_retrieval_manifests.${mismatch[0]} does not match immutable manifest_json`,
    );
  }
  return {
    indexVersion: EVIDENCE_RETRIEVAL_INDEX_VERSION,
    snapshotId: dto.snapshot_id,
    paperId: dto.paper_id,
    paperFamilyId: dto.paper_family_id,
    rootArtifactVersionId: dto.root_artifact_version_id,
    rootArtifactSha256: dto.root_artifact_sha256,
    graphSha256: dto.graph_sha256,
    graphSchemaVersion: dto.graph_schema_version,
    extractorPromptVersion: dto.extractor_prompt_version,
    documentCount: dto.document_count,
    createdAt: dto.created_at,
  };
}

function parseJson<T>(value: string, label: string): T {
  try {
    return JSON.parse(value) as T;
  } catch {
    throw new EvidenceRetrievalStoreError(500, "STORED_RETRIEVAL_PROJECTION_INVALID", `${label} contains invalid immutable JSON`);
  }
}

function documentFromDto(dto: EvidenceRetrievalDocumentDto): EvidenceRetrievalDocument {
  parseEvidenceRetrievalDocumentDto(dto);
  if (dto.index_version !== EVIDENCE_RETRIEVAL_INDEX_VERSION) {
    throw new EvidenceRetrievalStoreError(500, "RETRIEVAL_INDEX_VERSION_MISMATCH", "Stored retrieval document has an incompatible index version");
  }
  return {
    indexVersion: EVIDENCE_RETRIEVAL_INDEX_VERSION,
    snapshotId: dto.snapshot_id,
    paperId: dto.paper_id,
    paperFamilyId: dto.paper_family_id,
    rootArtifactVersionId: dto.root_artifact_version_id,
    claimId: dto.claim_id,
    sourceClaimId: dto.source_claim_id,
    originalClaim: dto.original_claim,
    canonicalClaim: dto.canonical_claim,
    propositionKey: dto.proposition_key,
    scopedClaimId: dto.scoped_claim_id,
    paperTitle: dto.paper_title,
    evidenceContext: dto.evidence_context,
    entities: dto.entities,
    keyTerms: dto.key_terms,
    abbreviations: dto.abbreviations.map((item) => ({ shortForm: item.short_form, longForm: item.long_form })),
    qualifiers: dto.qualifiers,
    claimType: dto.claim_type,
    polarity: dto.polarity,
    modality: dto.modality,
    attribution: dto.attribution,
    rhetoricalRole: dto.rhetorical_role,
    scope: dto.scope,
    evidenceAnchors: dto.evidence_anchors.map((anchor) => ({
      anchorId: anchor.anchor_id,
      artifactVersionId: anchor.artifact_version_id,
      anchorType: anchor.anchor_type,
      page: anchor.page,
      section: anchor.section,
      paragraph: anchor.paragraph,
      quote: anchor.quote,
      figure: anchor.figure,
      table: anchor.table,
      rowStart: anchor.row_start,
      rowEnd: anchor.row_end,
      rowRangeLabel: anchor.row_range_label,
      columnNames: anchor.column_names,
      series: anchor.series,
      charStart: anchor.char_start,
      charEnd: anchor.char_end,
      region: anchor.region,
      regionLabel: anchor.region_label,
      datasetPath: anchor.dataset_path,
    })),
    explicitRelations: dto.explicit_relations.map((relation) => ({
      edgeId: relation.edge_id,
      sourceClaimId: relation.source_claim_id,
      targetClaimId: relation.target_claim_id,
      otherClaimId: relation.other_claim_id,
      relation: relation.relation,
      direction: relation.direction,
      confidence: relation.confidence,
      scopeOverlap: relation.scope_overlap,
    })),
    shortestEvidencePath: {
      rootClaimId: dto.shortest_evidence_path.root_claim_id,
      nodes: dto.shortest_evidence_path.nodes,
      links: dto.shortest_evidence_path.links.map((link) => ({
        fromId: link.from_id,
        toId: link.to_id,
        relation: link.relation,
        linkType: link.link_type,
        confidence: link.confidence,
      })),
      depth: dto.shortest_evidence_path.depth,
      meanEdgeConfidence: dto.shortest_evidence_path.mean_edge_confidence,
    },
    pathQuality: dto.path_quality,
    extractorPromptVersion: dto.extractor_prompt_version,
  };
}

type DocumentRow = {
  snapshotId: string;
  paperId: string;
  paperFamilyId: string;
  claimId: string;
  scopedClaimId: string;
  propositionKey: string;
  indexVersion: string;
  documentJson: string;
};

const DOCUMENT_COLUMNS = `d.snapshot_id AS snapshotId, d.paper_id AS paperId,
  d.paper_family_id AS paperFamilyId, d.claim_id AS claimId,
  d.scoped_claim_id AS scopedClaimId, d.proposition_key AS propositionKey,
  d.index_version AS indexVersion, d.document_json AS documentJson`;

/**
 * Claim documents of the requested snapshots. The `+` keeps project_id and
 * index_version as plain filters: without planner statistics SQLite otherwise
 * drives the lookup through the (project_id, proposition_key) index and reads
 * every document of the project, about 7 s per sync of a single snapshot on a
 * 41k-Claim library. The (snapshot_id, claim_id) primary key answers in ms.
 */
export const RETRIEVAL_DOCUMENTS_BY_SNAPSHOT_SQL = `SELECT ${DOCUMENT_COLUMNS}
  FROM evidence_retrieval_documents d WHERE +d.project_id = ? AND +d.index_version = ?
  AND d.snapshot_id IN (SELECT value FROM json_each(?)) ORDER BY d.snapshot_id, d.claim_id`;

function documentFromRow(row: DocumentRow) {
  const dto = parseEvidenceRetrievalDocumentDto(parseJson<EvidenceRetrievalDocumentDto>(
    row.documentJson,
    "evidence_retrieval_documents.document_json",
  ));
  const columns = [
    ["snapshot_id", dto.snapshot_id, row.snapshotId],
    ["paper_id", dto.paper_id, row.paperId],
    ["paper_family_id", dto.paper_family_id, row.paperFamilyId],
    ["claim_id", dto.claim_id, row.claimId],
    ["scoped_claim_id", dto.scoped_claim_id, row.scopedClaimId],
    ["proposition_key", dto.proposition_key, row.propositionKey],
    ["index_version", dto.index_version, row.indexVersion],
  ] as const;
  const mismatch = columns.find(([, jsonValue, columnValue]) => jsonValue !== columnValue);
  if (mismatch) {
    throw new EvidenceRetrievalStoreError(
      500,
      "STORED_RETRIEVAL_PROJECTION_INVALID",
      `evidence_retrieval_documents.${mismatch[0]} does not match immutable document_json`,
    );
  }
  return documentFromDto(dto);
}

function assessmentFromDto(dto: EvidenceRetrievalAssessmentDto): EvidenceRetrievalAssessment {
  parseEvidenceRetrievalAssessmentDto(dto);
  if (dto.index_version !== EVIDENCE_RETRIEVAL_INDEX_VERSION) {
    throw new EvidenceRetrievalStoreError(500, "RETRIEVAL_INDEX_VERSION_MISMATCH", "Stored retrieval assessment has an incompatible index version");
  }
  return {
    indexVersion: EVIDENCE_RETRIEVAL_INDEX_VERSION,
    verificationId: dto.verification_id,
    snapshotId: dto.snapshot_id,
    assessmentId: dto.assessment_id,
    claimId: dto.claim_id,
    scopedClaimId: dto.scoped_claim_id,
    verdict: dto.verdict,
    confidence: dto.confidence,
    evidenceIds: dto.evidence_ids,
    rationale: dto.rationale,
    limitations: dto.limitations,
    verifierModel: dto.verifier_model,
    verifierPromptVersion: dto.verifier_prompt_version,
    createdAt: dto.created_at,
  };
}

type AssessmentRow = {
  verificationId: string;
  snapshotId: string;
  assessmentId: string;
  claimId: string;
  indexVersion: string;
  createdAt: string;
  assessmentJson: string;
};

const ASSESSMENT_COLUMNS = `a.verification_id AS verificationId,
  a.snapshot_id AS snapshotId, a.assessment_id AS assessmentId,
  a.claim_id AS claimId, a.index_version AS indexVersion,
  a.created_at AS createdAt, a.assessment_json AS assessmentJson`;

function assessmentFromRow(row: AssessmentRow) {
  const dto = parseEvidenceRetrievalAssessmentDto(parseJson<EvidenceRetrievalAssessmentDto>(
    row.assessmentJson,
    "evidence_retrieval_assessments.assessment_json",
  ));
  const columns = [
    ["verification_id", dto.verification_id, row.verificationId],
    ["snapshot_id", dto.snapshot_id, row.snapshotId],
    ["assessment_id", dto.assessment_id, row.assessmentId],
    ["claim_id", dto.claim_id, row.claimId],
    ["index_version", dto.index_version, row.indexVersion],
    ["created_at", dto.created_at, row.createdAt],
  ] as const;
  const mismatch = columns.find(([, jsonValue, columnValue]) => jsonValue !== columnValue);
  if (mismatch) {
    throw new EvidenceRetrievalStoreError(
      500,
      "STORED_RETRIEVAL_PROJECTION_INVALID",
      `evidence_retrieval_assessments.${mismatch[0]} does not match immutable assessment_json`,
    );
  }
  return assessmentFromDto(dto);
}

/** Manifests (without documents) for explicit snapshots, active or historical. */
export async function loadEvidenceRetrievalManifestsBySnapshotIds(
  DB: D1Database,
  projectId: string,
  snapshotIds: readonly string[],
) {
  const unique = [...new Set(snapshotIds)];
  if (!unique.length) return [];
  const rows = await DB.prepare(`SELECT ${MANIFEST_COLUMNS}
    FROM evidence_retrieval_manifests m
    WHERE m.project_id = ? AND m.index_version = ?
      AND m.snapshot_id IN (SELECT value FROM json_each(?))
    ORDER BY m.snapshot_id`)
    .bind(projectId, EVIDENCE_RETRIEVAL_INDEX_VERSION, JSON.stringify(unique)).all<ManifestRow>();
  return rows.results.map(manifestFromRow);
}

export async function loadActiveEvidenceRetrievalManifests(
  DB: D1Database,
  projectId: string,
  filters: { paperIds?: string[]; paperFamilyIds?: string[] } = {},
) {
  // Import/delete transactions already maintain the exact family-then-paper
  // winner projection. Unfiltered searches need not rank all history again.
  // Filters remain below: they apply before ranking and can select an older
  // family winner, so filtering the global projection would change semantics.
  if (!filters.paperIds?.length && !filters.paperFamilyIds?.length) {
    const rows = await DB.prepare(`SELECT ${MANIFEST_COLUMNS}
      FROM evidence_active_snapshots active
      JOIN evidence_retrieval_manifests m ON m.snapshot_id = active.snapshot_id
        AND m.project_id = active.project_id AND m.index_version = active.index_version
      WHERE active.project_id = ? AND active.index_version = ?
      ORDER BY active.paper_id, active.paper_family_id, active.snapshot_id`)
      .bind(projectId, EVIDENCE_RETRIEVAL_INDEX_VERSION).all<ManifestRow>();
    return rows.results.map(manifestFromRow);
  }
  const clauses: string[] = [];
  const args: unknown[] = [projectId, EVIDENCE_RETRIEVAL_INDEX_VERSION];
  if (filters.paperIds?.length) {
    clauses.push("m.paper_id IN (SELECT value FROM json_each(?))");
    args.push(JSON.stringify(filters.paperIds));
  }
  if (filters.paperFamilyIds?.length) {
    clauses.push("m.paper_family_id IN (SELECT value FROM json_each(?))");
    args.push(JSON.stringify(filters.paperFamilyIds));
  }
  const where = clauses.length ? ` AND ${clauses.join(" AND ")}` : "";
  const rows = await DB.prepare(`WITH family_ranked AS (
    SELECT ${MANIFEST_COLUMNS}, ROW_NUMBER() OVER (
      PARTITION BY m.paper_family_id
      ORDER BY julianday(m.created_at) DESC, m.created_at DESC, m.snapshot_id DESC
    ) AS family_rank
    FROM evidence_retrieval_manifests m
    WHERE m.project_id = ? AND m.index_version = ?${where}
  ), paper_ranked AS (
    SELECT *, ROW_NUMBER() OVER (
      PARTITION BY paperId
      ORDER BY julianday(createdAt) DESC, createdAt DESC, snapshotId DESC
    ) AS paper_rank FROM family_ranked WHERE family_rank = 1
  )
  SELECT snapshotId, paperId, paperFamilyId, rootArtifactVersionId,
    rootArtifactSha256, graphSha256, graphSchemaVersion, extractorPromptVersion,
    indexVersion, documentCount, manifestJson, createdAt
  FROM paper_ranked WHERE paper_rank = 1 ORDER BY paperId, paperFamilyId, snapshotId`)
    .bind(...args).all<ManifestRow>();
  return rows.results.map(manifestFromRow);
}

async function loadManifestBySnapshot(DB: D1Database, projectId: string, snapshotId: string) {
  const row = await DB.prepare(`SELECT ${MANIFEST_COLUMNS}
    FROM evidence_retrieval_manifests m
    WHERE m.project_id = ? AND m.snapshot_id = ? AND m.index_version = ? LIMIT 1`)
    .bind(projectId, snapshotId, EVIDENCE_RETRIEVAL_INDEX_VERSION).first<ManifestRow>();
  return row ? manifestFromRow(row) : null;
}

export async function loadEvidenceRetrievalDocumentsByRefs(
  DB: D1Database,
  projectId: string,
  refs: QueryClaimRef[],
) {
  if (refs.length === 0) return [];
  const rows = await DB.prepare(`WITH requested AS (
    SELECT CAST(key AS INTEGER) AS ordinal,
      json_extract(value, '$.snapshotId') AS snapshot_id,
      json_extract(value, '$.claimId') AS claim_id FROM json_each(?)
  )
  SELECT r.ordinal, ${DOCUMENT_COLUMNS}
  FROM requested r CROSS JOIN evidence_retrieval_documents d
    ON d.snapshot_id = r.snapshot_id AND d.claim_id = r.claim_id
  WHERE d.project_id = ? AND d.index_version = ? ORDER BY r.ordinal`)
    .bind(canonicalStringify(refs), projectId, EVIDENCE_RETRIEVAL_INDEX_VERSION)
    .all<DocumentRow & { ordinal: number }>();
  return rows.results.map(documentFromRow);
}

/** Latest verifier record per returned Claim in one bounded JSON-table query. */
export async function loadLatestEvidenceRetrievalAssessmentsByRefs(
  DB: D1Database,
  projectId: string,
  refs: QueryClaimRef[],
) {
  if (refs.length === 0) return [];
  const rows = await DB.prepare(`WITH requested AS (
    SELECT CAST(key AS INTEGER) AS ordinal,
      json_extract(value, '$.snapshotId') AS snapshot_id,
      json_extract(value, '$.claimId') AS claim_id FROM json_each(?)
  ), ranked AS (
    SELECT r.ordinal, ${ASSESSMENT_COLUMNS},
      ROW_NUMBER() OVER (
        PARTITION BY a.snapshot_id, a.claim_id
        ORDER BY a.created_at DESC, a.verification_id DESC, a.assessment_id DESC
      ) AS assessment_rank
    FROM requested r CROSS JOIN evidence_retrieval_assessments a
      ON a.snapshot_id = r.snapshot_id AND a.claim_id = r.claim_id
    WHERE a.project_id = ? AND a.index_version = ?
  )
  SELECT ordinal, verificationId, snapshotId, assessmentId, claimId,
    indexVersion, createdAt, assessmentJson
  FROM ranked WHERE assessment_rank = 1 ORDER BY ordinal`)
    .bind(canonicalStringify(refs), projectId, EVIDENCE_RETRIEVAL_INDEX_VERSION)
    .all<AssessmentRow & { ordinal: number }>();
  return rows.results.map(assessmentFromRow);
}

function bundleRequest(
  snapshotIds: string[],
  maximum: number,
  code: string,
  message: string,
) {
  const uniqueIds = [...new Set(snapshotIds)];
  if (uniqueIds.length > maximum) {
    throw new EvidenceRetrievalStoreError(413, code, message);
  }
  return { uniqueIds, encoded: canonicalStringify(uniqueIds) };
}

async function loadCoreBundleRows(
  DB: D1Database,
  projectId: string,
  requestedIds: string[],
  encodedIds: string,
) {
  const [manifestRows, documentRows] = await Promise.all([
    DB.prepare(`SELECT ${MANIFEST_COLUMNS} FROM evidence_retrieval_manifests m
      WHERE m.project_id = ? AND m.index_version = ?
      AND m.snapshot_id IN (SELECT value FROM json_each(?)) ORDER BY m.snapshot_id`)
      .bind(projectId, EVIDENCE_RETRIEVAL_INDEX_VERSION, encodedIds).all<ManifestRow>(),
    DB.prepare(RETRIEVAL_DOCUMENTS_BY_SNAPSHOT_SQL)
      .bind(projectId, EVIDENCE_RETRIEVAL_INDEX_VERSION, encodedIds)
      .all<DocumentRow>(),
  ]);
  const documents = new Map<string, EvidenceRetrievalDocument[]>();
  for (const row of documentRows.results) {
    const values = documents.get(row.snapshotId) ?? [];
    values.push(documentFromRow(row));
    documents.set(row.snapshotId, values);
  }
  const manifests = manifestRows.results.map(manifestFromRow);
  const available = new Set(manifests.map((manifest) => manifest.snapshotId));
  const missing = requestedIds.filter((id) => !available.has(id));
  if (missing.length) {
    throw new EvidenceRetrievalStoreError(404, "RETRIEVAL_MANIFEST_NOT_FOUND", `Retrieval manifests are missing for ${missing.length} snapshot(s)`);
  }
  for (const manifest of manifests) {
    if ((documents.get(manifest.snapshotId)?.length ?? 0) !== manifest.documentCount) {
      throw new EvidenceRetrievalStoreError(500, "RETRIEVAL_PROJECTION_INCOMPLETE", `Snapshot ${manifest.snapshotId} has an incomplete immutable retrieval projection`);
    }
  }
  return { manifests, documents };
}

/**
 * Load complete manifests and Claim documents for a small sync window using
 * exactly two D1 queries. Assessments intentionally remain in D1 hydration.
 */
export async function loadEvidenceRetrievalIndexBundles(
  DB: D1Database,
  projectId: string,
  snapshotIds: string[],
): Promise<EvidenceRetrievalIndexBundle[]> {
  const { uniqueIds, encoded } = bundleRequest(
    snapshotIds,
    MAX_INDEX_BUNDLE_SNAPSHOTS,
    "TOO_MANY_RETRIEVAL_INDEX_SNAPSHOTS",
    `At most ${MAX_INDEX_BUNDLE_SNAPSHOTS} retrieval snapshots may be synchronized per D1 load`,
  );
  if (uniqueIds.length === 0) return [];
  const { manifests, documents } = await loadCoreBundleRows(DB, projectId, uniqueIds, encoded);
  return manifests.map((manifest): EvidenceRetrievalIndexBundle => {
    const snapshotDocuments = documents.get(manifest.snapshotId) ?? [];
    return { manifest, documents: snapshotDocuments };
  });
}

export async function loadEvidenceRetrievalSnapshotBundles(
  DB: D1Database,
  projectId: string,
  snapshotIds: string[],
) {
  const { uniqueIds, encoded } = bundleRequest(
    snapshotIds,
    MAX_BUNDLE_SNAPSHOTS,
    "TOO_MANY_RETRIEVAL_SNAPSHOTS",
    `At most ${MAX_BUNDLE_SNAPSHOTS} retrieval snapshots may be loaded at once`,
  );
  if (uniqueIds.length === 0) return [];
  const [core, assessmentRows] = await Promise.all([
    loadCoreBundleRows(DB, projectId, uniqueIds, encoded),
    DB.prepare(`SELECT ${ASSESSMENT_COLUMNS}
      FROM evidence_retrieval_assessments a WHERE a.project_id = ? AND a.index_version = ?
      AND a.snapshot_id IN (SELECT value FROM json_each(?))
      ORDER BY a.snapshot_id, a.created_at, a.verification_id, a.assessment_id`)
      .bind(projectId, EVIDENCE_RETRIEVAL_INDEX_VERSION, encoded)
      .all<AssessmentRow>(),
  ]);
  const assessments = new Map<string, EvidenceRetrievalAssessment[]>();
  for (const row of assessmentRows.results) {
    const values = assessments.get(row.snapshotId) ?? [];
    values.push(assessmentFromRow(row));
    assessments.set(row.snapshotId, values);
  }
  return core.manifests.map((manifest): EvidenceRetrievalSnapshotBundle => {
    return {
      manifest,
      documents: core.documents.get(manifest.snapshotId) ?? [],
      assessments: assessments.get(manifest.snapshotId) ?? [],
    };
  });
}

export async function resolveEvidenceRetrievalCorpus(
  DB: D1Database,
  projectId: string,
  request: EvidenceSearchRequest,
): Promise<ResolvedEvidenceSearchCorpus> {
  let manifests = await loadActiveEvidenceRetrievalManifests(DB, projectId, {
    paperIds: request.paperIds,
    paperFamilyIds: request.paperFamilyIds,
  });
  if (request.openAlexScope && manifests.length) {
    const column = `${request.openAlexScope.level}_id`;
    const rows = await DB.prepare(`WITH candidates(snapshot_id) AS (SELECT value FROM json_each(?))
      SELECT DISTINCT topic.snapshot_id AS snapshotId
      FROM openalex_paper_topics topic JOIN candidates ON candidates.snapshot_id = topic.snapshot_id
      WHERE topic.project_id = ? AND topic.${column} = ?`)
      .bind(JSON.stringify(manifests.map((manifest) => manifest.snapshotId)), projectId, request.openAlexScope.id)
      .all<{ snapshotId: string }>();
    const allowed = new Set(rows.results.map((row) => row.snapshotId));
    manifests = manifests.filter((manifest) => allowed.has(manifest.snapshotId));
  }
  let queryDocument: EvidenceRetrievalDocument | null = null;
  if (request.queryClaim) {
    [queryDocument] = await loadEvidenceRetrievalDocumentsByRefs(DB, projectId, [request.queryClaim]);
    if (!queryDocument) {
      throw new EvidenceRetrievalStoreError(404, "QUERY_CLAIM_NOT_FOUND", "query_claim is not present in the immutable retrieval projection");
    }
    if (request.paperIds.length && !request.paperIds.includes(queryDocument.paperId)) {
      throw new EvidenceRetrievalStoreError(400, "QUERY_PAPER_FILTER_MISMATCH", "paper_ids excludes the query Claim's paper");
    }
    if (request.paperFamilyIds.length && !request.paperFamilyIds.includes(queryDocument.paperFamilyId)) {
      throw new EvidenceRetrievalStoreError(400, "QUERY_PAPER_FILTER_MISMATCH", "paper_family_ids excludes the query Claim's paper family");
    }
    const querySnapshotId = queryDocument.snapshotId;
    if (request.openAlexScope && !manifests.some((manifest) => manifest.snapshotId === querySnapshotId)) {
      throw new EvidenceRetrievalStoreError(400, "QUERY_OPENALEX_FILTER_MISMATCH", "openalex_scope excludes the query Claim's paper");
    }
    const historical = await loadManifestBySnapshot(DB, projectId, queryDocument.snapshotId);
    if (!historical) {
      throw new EvidenceRetrievalStoreError(500, "RETRIEVAL_MANIFEST_NOT_FOUND", "query_claim has no immutable retrieval manifest");
    }
    manifests = request.corpus === "same_paper"
      ? [historical]
      : [...manifests.filter((manifest) => manifest.paperFamilyId !== historical.paperFamilyId), historical]
        .sort((left, right) => compareText(left.paperId, right.paperId)
          || compareText(left.paperFamilyId, right.paperFamilyId)
          || compareText(left.snapshotId, right.snapshotId));
  }
  const queryText = request.query ?? queryDocument?.canonicalClaim;
  if (!queryText) {
    throw new EvidenceRetrievalStoreError(400, "EMPTY_RETRIEVAL_QUERY", "A text query or resolvable query Claim is required");
  }
  return { request, queryText, queryDocument, manifests };
}
