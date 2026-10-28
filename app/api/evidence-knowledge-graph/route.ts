import {
  claimRefKey,
  EVIDENCE_KNOWLEDGE_GRAPH_VERSION,
  type ClaimRef,
  type ClaimModelRelation,
} from "@/lib/evidence-knowledge-graph";
import { SCOPED_CLAIM_ID_RE, SNAPSHOT_ID_RE } from "@/lib/evidence-graph/canonical";
import {
  EvidenceRetrievalStoreError,
  type EvidenceRetrievalDocument,
  type QueryClaimRef,
} from "@/lib/evidence-retrieval";
import {
  ApiError,
  getBindings,
  PROJECT_ID,
  safeError,
} from "@/lib/server/evidence-store";

// Claim-relation processing is background work (see ./relation-batches.ts and
// app/api/internal/work); this route only reads the derived graph.
const DEFAULT_LIMIT = 50;
const MAX_LIMIT = 100;
const MAX_CURSOR_LENGTH = 2_048;
const MAX_NEIGHBOR_EDGES = 200;

type ClaimStatusFilter = "unprocessed" | "processed" | "all";
type TaxonomyLevel = "domain" | "field" | "subfield" | "topic";
type TaxonomyScope = { level: TaxonomyLevel; id: string };
type KnowledgeReadView = "full" | "taxonomy" | "claims" | "neighborhood";

type ClaimCursor = {
  version: 2;
  filter: string;
  status: ClaimStatusFilter;
  createdAt: string;
  snapshotId: string;
  claimId: string;
};

type ClaimRow = {
  snapshotId: string;
  claimId: string;
  createdAt: string;
  completedAt: string | null;
  incomingEdges: number;
  outgoingEdges: number;
  comparisonEdges: number;
};

type EdgeRow = {
  id: string;
  sourceSnapshotId: string;
  sourceClaimId: string;
  targetSnapshotId: string;
  targetClaimId: string;
  modelRelation: ClaimModelRelation;
  confidence: number | null;
  createdAt: string;
};

function parseLimit(value: string | null) {
  if (value === null) return DEFAULT_LIMIT;
  if (!/^[1-9][0-9]{0,2}$/u.test(value)) {
    throw new ApiError(400, "INVALID_KNOWLEDGE_GRAPH_LIMIT", `limit must be an integer from 1 to ${MAX_LIMIT}`);
  }
  const limit = Number(value);
  if (limit > MAX_LIMIT) {
    throw new ApiError(400, "INVALID_KNOWLEDGE_GRAPH_LIMIT", `limit must be an integer from 1 to ${MAX_LIMIT}`);
  }
  return limit;
}

function parsePage(value: string | null) {
  if (value === null) return null;
  if (!/^[1-9][0-9]{0,8}$/u.test(value)) {
    throw new ApiError(400, "INVALID_KNOWLEDGE_GRAPH_PAGE", "page must be a positive integer");
  }
  if (Number(value) > 1) throw new ApiError(400, "KNOWLEDGE_GRAPH_CURSOR_REQUIRED", "Use nextCursor to load subsequent pages");
  return Number(value);
}

function parseStatus(value: string | null): ClaimStatusFilter {
  const status = value ?? "unprocessed";
  if (status !== "unprocessed" && status !== "processed" && status !== "all") {
    throw new ApiError(400, "INVALID_KNOWLEDGE_GRAPH_STATUS", "status must be unprocessed, processed or all");
  }
  return status;
}

function encodeCursor(cursor: ClaimCursor) {
  const bytes = new TextEncoder().encode(JSON.stringify(cursor));
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/u, "");
}

function decodeCursor(value: string | null): ClaimCursor | null {
  if (value === null) return null;
  if (!value || value.length > MAX_CURSOR_LENGTH || !/^[A-Za-z0-9_-]+$/u.test(value)) {
    throw new ApiError(400, "INVALID_KNOWLEDGE_GRAPH_CURSOR", "cursor is invalid");
  }
  try {
    const base64 = value.replaceAll("-", "+").replaceAll("_", "/")
      .padEnd(Math.ceil(value.length / 4) * 4, "=");
    const binary = atob(base64);
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    const decoded = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as Partial<ClaimCursor>;
    if (Object.keys(decoded).sort().join(",") !== "claimId,createdAt,filter,snapshotId,status,version"
      || decoded.version !== 2
      || typeof decoded.filter !== "string"
      || decoded.filter.length > 700
      || !["unprocessed", "processed", "all"].includes(decoded.status ?? "")
      || typeof decoded.createdAt !== "string"
      || !Number.isFinite(Date.parse(decoded.createdAt))
      || typeof decoded.snapshotId !== "string"
      || !SNAPSHOT_ID_RE.test(decoded.snapshotId)
      || typeof decoded.claimId !== "string"
      || !SCOPED_CLAIM_ID_RE.test(decoded.claimId)) {
      throw new Error("invalid cursor payload");
    }
    return decoded as ClaimCursor;
  } catch {
    throw new ApiError(400, "INVALID_KNOWLEDGE_GRAPH_CURSOR", "cursor is invalid");
  }
}

function parseGetRequest(request: Request) {
  const parameters = new URL(request.url).searchParams;
  for (const key of parameters.keys()) {
    if (!["status", "cursor", "limit", "page", "query", "level", "id", "claimSnapshotId", "claimId", "view"].includes(key)
      || parameters.getAll(key).length !== 1) {
      throw new ApiError(
        400,
        "INVALID_KNOWLEDGE_GRAPH_REQUEST",
        "Knowledge graph query parameters are invalid",
      );
    }
  }
  const status = parseStatus(parameters.get("status"));
  const view = (parameters.get("view") ?? "full") as KnowledgeReadView;
  if (!["full", "taxonomy", "claims", "neighborhood"].includes(view)) {
    throw new ApiError(400, "INVALID_KNOWLEDGE_GRAPH_VIEW", "view must be full, taxonomy, claims or neighborhood");
  }
  const query = parameters.get("query")?.trim() ?? "";
  if (query.length > 200) {
    throw new ApiError(400, "INVALID_KNOWLEDGE_GRAPH_QUERY", "query may contain at most 200 characters");
  }
  const cursor = decodeCursor(parameters.get("cursor"));
  const page = parsePage(parameters.get("page"));
  if (cursor && page !== null) {
    throw new ApiError(400, "INVALID_KNOWLEDGE_GRAPH_REQUEST", "cursor and page cannot be used together");
  }
  if (cursor && cursor.status !== status) {
    throw new ApiError(400, "INVALID_KNOWLEDGE_GRAPH_CURSOR", "cursor does not belong to this status filter");
  }
  const levelValue = parameters.get("level");
  const id = parameters.get("id");
  if ((levelValue === null) !== (id === null)
    || (levelValue !== null && !["domain", "field", "subfield", "topic"].includes(levelValue))
    || (id !== null && (!/^https:\/\/openalex\.org\/(?:domains|fields|subfields)\/[0-9]+$/u.test(id)
      && !/^https:\/\/openalex\.org\/T[0-9]+$/u.test(id)))) {
    throw new ApiError(400, "INVALID_OPENALEX_SCOPE", "level and id must identify one OpenAlex taxonomy node");
  }
  const claimSnapshotId = parameters.get("claimSnapshotId");
  const claimId = parameters.get("claimId");
  if ((claimSnapshotId === null) !== (claimId === null)
    || (claimSnapshotId !== null && !SNAPSHOT_ID_RE.test(claimSnapshotId))
    || (claimId !== null && !SCOPED_CLAIM_ID_RE.test(claimId))) {
    throw new ApiError(400, "INVALID_KNOWLEDGE_GRAPH_CLAIM", "claimSnapshotId and claimId must identify one Claim");
  }
  const filter = JSON.stringify([levelValue, id, query.toLocaleLowerCase("und")]);
  if (cursor && cursor.filter !== filter) {
    throw new ApiError(400, "INVALID_KNOWLEDGE_GRAPH_CURSOR", "cursor does not belong to this taxonomy or query");
  }
  return {
    view,
    filter,
    status,
    query,
    cursor,
    page,
    limit: parseLimit(parameters.get("limit")),
    taxonomyScope: levelValue && id ? { level: levelValue as TaxonomyLevel, id } : null,
    claimRef: claimSnapshotId && claimId ? { snapshotId: claimSnapshotId, claimId } : null,
  };
}

async function loadCounts(
  DB: D1Database,
  taxonomyScope: TaxonomyScope | null = null,
) {
  if (!taxonomyScope) return loadTaxonomyOverviewCounts(DB);
  if (taxonomyScope) {
    const row = await DB.prepare(`SELECT claim_count AS total, processed_count AS processed, edge_count AS edges
      FROM graph_taxonomy_nodes WHERE project_id = ? AND level = ? AND id = ?`)
      .bind(PROJECT_ID, taxonomyScope.level, taxonomyScope.id).first<{ total: number; processed: number; edges: number }>();
    const total = Number(row?.total ?? 0), processed = Number(row?.processed ?? 0);
    return { all: total, processed, unprocessed: total - processed, edges: Number(row?.edges ?? 0) };
  }

  return loadTaxonomyOverviewCounts(DB);
}

async function loadClaimRows(
  DB: D1Database,
  status: ClaimStatusFilter,
  cursor: ClaimCursor | null,
  limit: number,
  taxonomyScope: TaxonomyScope | null,
) {
  const claimStatus = status === "all" ? "1 = 1" : `(completed_at IS NULL) = ${status === "processed" ? "0" : "1"}`;
  let pageSql: string;
  let pageBindings: (string | number)[];
  if (taxonomyScope) {
    const memberStatus = status === "all" ? "claim_count > 0"
      : status === "processed" ? "processed_count > 0" : "claim_count > processed_count";
    pageSql = `WITH scoped AS MATERIALIZED (
      SELECT snapshot_id FROM graph_taxonomy_memberships
      WHERE project_id = ? AND level = ? AND id = ? AND ${memberStatus}
        ${cursor ? `AND (created_at, snapshot_id) <= (?, ?)
          AND (snapshot_id <> ? OR EXISTS (SELECT 1 FROM graph_active_claims c
            WHERE c.snapshot_id = graph_taxonomy_memberships.snapshot_id AND c.claim_id < ? AND ${claimStatus}))` : ""}
      ORDER BY created_at DESC, snapshot_id DESC LIMIT ?
    ), page AS MATERIALIZED (
      SELECT json_extract(item.value, '$.snapshotId') AS snapshotId,
        json_extract(item.value, '$.claimId') AS claimId,
        json_extract(item.value, '$.createdAt') AS createdAt,
        json_extract(item.value, '$.completedAt') AS completedAt
      FROM scoped, json_each((SELECT json_group_array(json_object(
        'snapshotId', snapshot_id, 'claimId', claim_id, 'createdAt', created_at, 'completedAt', completed_at))
        FROM (SELECT snapshot_id, claim_id, created_at, completed_at FROM graph_active_claims
          WHERE snapshot_id = scoped.snapshot_id AND ${claimStatus}
            ${cursor ? `AND (snapshot_id <> ? OR claim_id < ?)` : ""}
          ORDER BY claim_id DESC LIMIT ?))) item
      ORDER BY createdAt DESC, snapshotId DESC, claimId DESC LIMIT ?`;
    pageBindings = [PROJECT_ID, taxonomyScope.level, taxonomyScope.id,
      ...(cursor ? [cursor.createdAt, cursor.snapshotId, cursor.snapshotId, cursor.claimId] : []), limit + 1,
      ...(cursor ? [cursor.snapshotId, cursor.claimId] : []), limit + 1, limit + 1];
  } else {
    pageSql = `WITH page AS MATERIALIZED (
      SELECT snapshot_id AS snapshotId, claim_id AS claimId, created_at AS createdAt, completed_at AS completedAt
      FROM graph_active_claims WHERE project_id = ? AND ${claimStatus}
        ${cursor ? `AND (created_at, snapshot_id, claim_id) < (?, ?, ?)` : ""}
      ORDER BY created_at DESC, snapshot_id DESC, claim_id DESC LIMIT ?`;
    pageBindings = [PROJECT_ID, ...(cursor ? [cursor.createdAt, cursor.snapshotId, cursor.claimId] : []), limit + 1];
  }
  const rows = await DB.prepare(`${pageSql}
    ) SELECT page.*,
      (SELECT COUNT(*) FROM corpus_claim_graph_edges edge INDEXED BY corpus_claim_graph_edge_target_idx
        JOIN evidence_active_snapshots active ON active.snapshot_id = edge.source_snapshot_id
        WHERE edge.project_id = ? AND edge.processor_version = ? AND edge.relation = 'directly_implies'
          AND edge.target_snapshot_id = page.snapshotId AND edge.target_claim_id = page.claimId) AS incomingEdges,
      (SELECT COUNT(*) FROM corpus_claim_graph_edges edge INDEXED BY corpus_claim_graph_edge_source_idx
        JOIN evidence_active_snapshots active ON active.snapshot_id = edge.target_snapshot_id
        WHERE edge.project_id = ? AND edge.processor_version = ? AND edge.relation = 'directly_implies'
          AND edge.source_snapshot_id = page.snapshotId AND edge.source_claim_id = page.claimId) AS outgoingEdges,
      ((SELECT COUNT(*) FROM corpus_claim_graph_edges edge INDEXED BY corpus_claim_graph_edge_source_idx
        JOIN evidence_active_snapshots active ON active.snapshot_id = edge.target_snapshot_id
        WHERE edge.project_id = ? AND edge.processor_version = ? AND edge.relation <> 'directly_implies'
          AND edge.source_snapshot_id = page.snapshotId AND edge.source_claim_id = page.claimId)
       + (SELECT COUNT(*) FROM corpus_claim_graph_edges edge INDEXED BY corpus_claim_graph_edge_target_idx
        JOIN evidence_active_snapshots active ON active.snapshot_id = edge.source_snapshot_id
        WHERE edge.project_id = ? AND edge.processor_version = ? AND edge.relation <> 'directly_implies'
          AND edge.target_snapshot_id = page.snapshotId AND edge.target_claim_id = page.claimId)) AS comparisonEdges
    FROM page ORDER BY createdAt DESC, snapshotId DESC, claimId DESC`)
    .bind(
      ...pageBindings,
      ...Array.from({ length: 4 }, () => [PROJECT_ID, EVIDENCE_KNOWLEDGE_GRAPH_VERSION]).flat(),
    ).all<ClaimRow>();
  return rows.results;
}

async function completedRefKeys(DB: D1Database, refs: QueryClaimRef[]) {
  if (!refs.length) return new Set<string>();
  const rows = await DB.prepare(`WITH requested AS MATERIALIZED (
      SELECT json_extract(value, '$.snapshotId') AS snapshot_id,
        json_extract(value, '$.claimId') AS claim_id FROM json_each(?)
    )
    SELECT completion.snapshot_id AS snapshotId, completion.claim_id AS claimId
    FROM requested CROSS JOIN corpus_claim_graph_completions completion INDEXED BY sqlite_autoindex_corpus_claim_graph_completions_1
      ON completion.snapshot_id = requested.snapshot_id
     AND completion.claim_id = requested.claim_id
    WHERE completion.project_id = ? AND completion.processor_version = ?`)
    .bind(JSON.stringify(refs), PROJECT_ID, EVIDENCE_KNOWLEDGE_GRAPH_VERSION)
    .all<QueryClaimRef>();
  return new Set(rows.results.map(claimRefKey));
}

type ClaimSummary = Pick<EvidenceRetrievalDocument, "snapshotId" | "claimId" | "paperId" | "paperFamilyId" | "canonicalClaim" | "paperTitle">;

async function loadClaimSummaries(DB: D1Database, refs: QueryClaimRef[]) {
  if (!refs.length) return [];
  const rows = await DB.prepare(`WITH requested AS MATERIALIZED (
    SELECT CAST(key AS INTEGER) AS ordinal, json_extract(value, '$.snapshotId') AS snapshot_id,
      json_extract(value, '$.claimId') AS claim_id FROM json_each(?)
    ) SELECT d.snapshot_id AS snapshotId, d.claim_id AS claimId, d.paper_id AS paperId,
      d.paper_family_id AS paperFamilyId, json_extract(d.document_json, '$.canonical_claim') AS canonicalClaim,
      json_extract(d.document_json, '$.paper_title') AS paperTitle
    FROM requested r CROSS JOIN evidence_retrieval_documents d INDEXED BY sqlite_autoindex_evidence_retrieval_documents_1 ON d.snapshot_id = r.snapshot_id AND d.claim_id = r.claim_id
    CROSS JOIN graph_active_claims active INDEXED BY sqlite_autoindex_graph_active_claims_1 ON active.snapshot_id = d.snapshot_id AND active.claim_id = d.claim_id
    WHERE d.project_id = ? ORDER BY r.ordinal`)
    .bind(JSON.stringify(refs), PROJECT_ID).all<ClaimSummary>();
  return rows.results;
}

function publicClaim(document: ClaimSummary, row: ClaimRow) {
  return {
    snapshotId: document.snapshotId,
    claimId: document.claimId,
    paperId: document.paperId,
    paperFamilyId: document.paperFamilyId,
    canonicalClaim: document.canonicalClaim,
    paperTitle: document.paperTitle,
    status: row.completedAt === null ? "pending" as const : "processed" as const,
    processedAt: row.completedAt,
    error: null,
    incomingEdges: Number(row.incomingEdges),
    outgoingEdges: Number(row.outgoingEdges),
    comparisonEdges: Number(row.comparisonEdges),
  };
}


type TaxonomyRow = {
  level: TaxonomyLevel;
  id: string;
  displayName: string;
  parentId: string | null;
  paperCount: number;
  claimCount: number;
  primaryClaimCount: number;
};

async function loadTaxonomyOverviewCounts(DB: D1Database) {
  const row = await DB.prepare(`SELECT all_count AS total, processed_count AS processed, edge_count AS edges
    FROM graph_read_counts WHERE project_id = ?`).bind(PROJECT_ID)
    .first<{ total: number; processed: number; edges: number }>();
  const all = Number(row?.total ?? 0), processed = Number(row?.processed ?? 0);
  return { all, processed, unprocessed: all - processed, edges: Number(row?.edges ?? 0) };
}

async function loadTaxonomy(DB: D1Database) {
  const [rows, missing] = await Promise.all([
    DB.prepare(`SELECT level, id, display_name AS displayName, parent_id AS parentId,
      paper_count AS paperCount, claim_count AS claimCount, primary_claim_count AS primaryClaimCount
      FROM graph_taxonomy_nodes WHERE project_id = ? ORDER BY level, display_name, id`)
      .bind(PROJECT_ID).all<TaxonomyRow>(),
    DB.prepare(`SELECT unclassified_papers AS total FROM graph_read_counts WHERE project_id = ?`)
      .bind(PROJECT_ID).first<{ total: number }>(),
  ]);
  return { nodes: rows.results, unclassifiedPapers: Number(missing?.total ?? 0) };
}

async function buildNeighborhood(DB: D1Database, center: ClaimRef | null) {
  if (!center) return null;
  const active = await DB.prepare(`SELECT snapshot_id FROM graph_active_claims WHERE snapshot_id = ? AND claim_id = ? AND project_id = ?`)
    .bind(center.snapshotId, center.claimId, PROJECT_ID).first();
  if (!active) return null;
  const edgeResult = await DB.prepare(`WITH incident AS (
      SELECT * FROM corpus_claim_graph_edges INDEXED BY corpus_claim_graph_edge_source_idx
      WHERE source_snapshot_id = ? AND source_claim_id = ?
      UNION ALL
      SELECT * FROM corpus_claim_graph_edges INDEXED BY corpus_claim_graph_edge_target_idx
      WHERE target_snapshot_id = ? AND target_claim_id = ?
    ) SELECT edge.id, edge.source_snapshot_id AS sourceSnapshotId,
      edge.source_claim_id AS sourceClaimId, edge.target_snapshot_id AS targetSnapshotId,
      edge.target_claim_id AS targetClaimId, edge.model_relation AS modelRelation,
      edge.confidence, edge.created_at AS createdAt
    FROM incident edge
    JOIN evidence_active_snapshots source ON source.snapshot_id = edge.source_snapshot_id
    JOIN evidence_active_snapshots target ON target.snapshot_id = edge.target_snapshot_id
    WHERE edge.project_id = ? AND edge.processor_version = ?
    ORDER BY edge.created_at DESC, edge.id DESC LIMIT ?`)
    .bind(
      center.snapshotId, center.claimId, center.snapshotId, center.claimId,
      PROJECT_ID, EVIDENCE_KNOWLEDGE_GRAPH_VERSION,
      MAX_NEIGHBOR_EDGES + 1,
    ).all<EdgeRow>();
  const edges = edgeResult.results.slice(0, MAX_NEIGHBOR_EDGES);
  const refs = new Map<string, QueryClaimRef>([[claimRefKey(center), center]]);
  for (const edge of edges) {
    const source = { snapshotId: edge.sourceSnapshotId, claimId: edge.sourceClaimId };
    const target = { snapshotId: edge.targetSnapshotId, claimId: edge.targetClaimId };
    refs.set(claimRefKey(source), source);
    refs.set(claimRefKey(target), target);
  }
  const graphRefs = [...refs.values()];
  const [documents, completed] = await Promise.all([
    loadClaimSummaries(DB, graphRefs),
    completedRefKeys(DB, graphRefs),
  ]);
  if (!documents.some((document) => claimRefKey(document) === claimRefKey(center))) {
    throw new ApiError(404, "KNOWLEDGE_GRAPH_CLAIM_NOT_FOUND", "Claim was not found");
  }
  return {
    center,
    nodes: documents.map((document) => ({
      snapshotId: document.snapshotId,
      claimId: document.claimId,
      paperId: document.paperId,
      canonicalClaim: document.canonicalClaim,
      paperTitle: document.paperTitle,
      status: completed.has(claimRefKey(document)) ? "processed" as const : "pending" as const,
    })),
    edges,
    truncated: edgeResult.results.length > MAX_NEIGHBOR_EDGES,
  };
}

export async function GET(request: Request) {
  try {
    const { view, filter, status, query, cursor, page, limit, taxonomyScope, claimRef } = parseGetRequest(request);
    const { DB } = await getBindings();
    if (view === "neighborhood") {
      if (!claimRef) throw new ApiError(400, "INVALID_KNOWLEDGE_GRAPH_CLAIM", "neighborhood requires a Claim reference");
      return Response.json({ ok: true, neighborhood: await buildNeighborhood(DB, claimRef) });
    }
    if (view === "taxonomy") {
      const [counts, taxonomy] = await Promise.all([
        loadTaxonomyOverviewCounts(DB),
        loadTaxonomy(DB),
      ]);
      return Response.json({ ok: true, counts, taxonomy });
    }
    if (query) {
      // Substring matching has no selective index: advance a bounded cursor window,
      // never scan every stored Claim merely to compute an exact search total.
      const scanLimit = 250;
      const [counts, scoped, scanned, taxonomy, neighborhood] = await Promise.all([
        loadCounts(DB, null), taxonomyScope ? loadCounts(DB, taxonomyScope) : Promise.resolve(null),
        loadClaimRows(DB, status, cursor, scanLimit, taxonomyScope),
        view === "claims" ? Promise.resolve(null) : loadTaxonomy(DB), buildNeighborhood(DB, claimRef),
      ]);
      const scanRows = scanned.slice(0, scanLimit);
      const summaries = await loadClaimSummaries(DB, scanRows);
      if (summaries.length !== scanRows.length) {
        throw new ApiError(500, "KNOWLEDGE_GRAPH_CLAIM_NOT_FOUND", "An active Claim projection is incomplete");
      }
      const normalized = query.toLocaleLowerCase("und");
      const matches = summaries.filter((document) => document.canonicalClaim.toLocaleLowerCase("und").includes(normalized)
        || document.paperTitle.toLocaleLowerCase("und").includes(normalized));
      const selected = matches.slice(0, limit);
      const byRef = new Map(scanRows.map((row) => [claimRefKey(row), row]));
      const hasMore = matches.length > limit || scanned.length > scanLimit;
      const last = matches.length > limit ? byRef.get(claimRefKey(selected.at(-1)!)) : scanRows.at(-1);
      return Response.json({
        ok: true, counts, scopeCounts: scoped ?? counts,
        claims: selected.map((document) => publicClaim(document, byRef.get(claimRefKey(document))!)),
        taxonomy, neighborhood,
        searchInfo: { scannedClaims: scanRows.length, complete: !hasMore, countsAreCorpusTotals: true },
        pageInfo: { hasMore, page: 1, pageSize: limit, total: null, totalPages: null,
          nextCursor: hasMore && last ? encodeCursor({ version: 2, filter, status, createdAt: last.createdAt,
            snapshotId: last.snapshotId, claimId: last.claimId }) : null },
      });
    }
    const [counts, scopeCounts, rows, taxonomy, neighborhood] = await Promise.all([
      loadCounts(DB, null),
      taxonomyScope ? loadCounts(DB, taxonomyScope) : Promise.resolve(null),
      loadClaimRows(DB, status, cursor, limit, taxonomyScope),
      view === "claims" ? Promise.resolve(null) : loadTaxonomy(DB),
      buildNeighborhood(DB, claimRef),
    ]);
    const total = (scopeCounts ?? counts)[status];
    const pageNumber = page ?? 1;
    const hasMore = page === null ? rows.length > limit : pageNumber * limit < total;
    const pageRows = rows.slice(0, limit);
    const documents = await loadClaimSummaries(
      DB,
      pageRows.map((row) => ({ snapshotId: row.snapshotId, claimId: row.claimId })),
    );
    if (documents.length !== pageRows.length) {
      throw new ApiError(500, "KNOWLEDGE_GRAPH_CLAIM_NOT_FOUND", "An active Claim projection is incomplete");
    }
    const rowsByRef = new Map(pageRows.map((row) => [claimRefKey(row), row]));
    const last = pageRows.at(-1);
    return Response.json({
      ok: true,
      counts,
      scopeCounts: scopeCounts ?? counts,
      claims: documents.map((document) => publicClaim(document, rowsByRef.get(claimRefKey(document))!)),
      pageInfo: {
        hasMore,
        page: pageNumber,
        pageSize: limit,
        total,
        totalPages: Math.max(1, Math.ceil(total / limit)),
        nextCursor: hasMore && last ? encodeCursor({
          version: 2,
          filter,
          status,
          createdAt: last.createdAt,
          snapshotId: last.snapshotId,
          claimId: last.claimId,
        }) : null,
      },
      taxonomy,
      neighborhood,
    });
  } catch (error) {
    if (error instanceof EvidenceRetrievalStoreError) {
      return safeError(new ApiError(error.status, error.code, error.message));
    }
    return safeError(error);
  }
}
