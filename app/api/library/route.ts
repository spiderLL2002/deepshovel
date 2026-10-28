import {
  ApiError,
  getBindings,
  PROJECT_ID,
  safeError,
} from "@/lib/server/evidence-store";

const DEFAULT_LIMIT = 50;
const MAX_LIMIT = 100;
const MAX_QUERY_LENGTH = 200;
const MAX_CURSOR_LENGTH = 2_048;
const MAX_SEARCH_CANDIDATES = 1_024;
const ARTIFACT_ID_RE = /^artifact:[A-Za-z0-9._:-]{1,119}$/;

type LibraryCursor = {
  version: 3;
  searchId: number;
  createdAt: string;
  artifactId: string;
  query: string;
  status: LibraryStatus;
};

type LibraryStatus = "pending" | "processing" | "completed" | "failed" | "all";

type LibraryRow = {
  totalCount: number | null;
  searchId: number;
  searchLastId: number | null;
  searchHasMore: number;
  searchLastCreatedAt: string | null;
  searchLastArtifactId: string | null;
  artifactId: string;
  preferredName: string;
  artifactType: string;
  artifactCreatedAt: string;
  versionId: string | null;
  versionCount: number | null;
  versionNumber: number | null;
  sha256: string | null;
  sourceLocator: string | null;
  mediaType: string | null;
  originalFileName: string | null;
  byteSize: number | null;
  versionCreatedAt: string | null;
  compileJobId: string | null;
  compileArtifactVersionId: string | null;
  compileStatus: "queued" | "running" | "completed" | "failed" | null;
  compileStage: "queued" | "parsing" | "extracting" | "validating" | "importing" | "completed" | "failed" | null;
  compileProgress: number | null;
  compileTimingsJson: string | null;
  compileErrorJson: string | null;
  compileSnapshotId: string | null;
  compileCreatedAt: string | null;
  compileUpdatedAt: string | null;
  snapshotId: string | null;
  snapshotCountsJson: string | null;
  snapshotCreatedAt: string | null;
  verificationRunCount: number | null;
  verificationLimitationCount: number | null;
  reviewId: string | null;
  reviewDecision: "accepted" | "rejected" | "needs_revision" | "withdrawn" | null;
  reviewRationale: string | null;
  reviewer: string | null;
  reviewCreatedAt: string | null;
  libraryStatus: Exclude<LibraryStatus, "all">;
  updatedAtEpoch: number;
};

function librarySql({ query, status, cursor, artifactVersionId, limit }: {
  query: string; status: LibraryStatus; cursor: LibraryCursor | null; artifactVersionId: string | null; limit: number;
}) {
  const bindings: Array<string | number | null> = [];
  const predicates: string[] = [];
  const source = "library_artifact_summaries a";
  let candidatesSql = "";
  if (query) {
    const points = Array.from(query);
    const width = Math.min(3, points.length);
    const tokens = [...new Set(Array.from({ length: points.length - width + 1 }, (_, index) =>
      "u" + points.slice(index, index + width).map((point) => point.codePointAt(0)!.toString(16)).join("x")))];
    tokens.push("p" + Array.from(new TextEncoder().encode(PROJECT_ID), (byte) => byte.toString(16).padStart(2, "0")).join(""));
    if (status !== "all") tokens.push("s" + status);
    bindings.push(tokens.join(" AND "));
    if (cursor) bindings.push(cursor.searchId);
    // FTS intersects integer posting lists; ordered rowid cursors avoid sorting all
    // matches. search_id is an explicit AUTOINCREMENT key, stable across VACUUM.
    candidatesSql = `search_ids AS MATERIALIZED (
      SELECT rowid FROM library_name_search WHERE library_name_search MATCH ?
      ${cursor ? "AND rowid < ?" : ""} ORDER BY rowid DESC LIMIT ${MAX_SEARCH_CANDIDATES}
    ), search_candidates AS MATERIALIZED (
      SELECT a.* FROM search_ids ids JOIN library_artifact_summaries a ON a.search_id=ids.rowid
    ),`;
    predicates.push("(instr(lower(a.preferred_name), ?) > 0 OR instr(lower(a.original_file_name), ?) > 0)");
    bindings.push(query, query);
  } else {
    predicates.push("a.project_id = ?"); bindings.push(PROJECT_ID);
    if (status !== "all") { predicates.push("a.library_status = ?"); bindings.push(status); }
    if (cursor) {
      predicates.push("(a.created_at, a.artifact_id) < (?, ?)");
      bindings.push(cursor.createdAt, cursor.artifactId);
    }
  }
  if (artifactVersionId) {
    predicates.push("a.artifact_id = (SELECT artifact_id FROM artifact_versions WHERE id = ?)");
    bindings.push(artifactVersionId);
  }
  bindings.push(limit + 1);
  const totalSql = query || artifactVersionId ? "NULL" : status === "all"
    ? "(SELECT COALESCE(SUM(artifact_count), 0) FROM library_status_counts WHERE project_id = ?)"
    : "(SELECT COALESCE((SELECT artifact_count FROM library_status_counts WHERE project_id = ? AND status = ?), 0))";
  if (!query && !artifactVersionId) { bindings.push(PROJECT_ID); if (status !== "all") bindings.push(status); }
  return { bindings, sql: `WITH ${candidatesSql} page_artifacts AS MATERIALIZED (
    SELECT a.* FROM ${query ? "search_candidates a" : source}
    WHERE ${predicates.join(" AND ")}
    ORDER BY ${query ? "a.search_id DESC" : "a.created_at DESC, a.artifact_id DESC"} LIMIT ?
  ), metadata AS (SELECT ${totalSql} AS totalCount,
    ${query ? `(SELECT COUNT(*) = ${MAX_SEARCH_CANDIDATES} FROM search_candidates)` : "0"} AS searchHasMore,
    ${query ? "(SELECT created_at FROM search_candidates ORDER BY search_id LIMIT 1)" : "NULL"} AS searchLastCreatedAt,
    ${query ? "(SELECT artifact_id FROM search_candidates ORDER BY search_id LIMIT 1)" : "NULL"} AS searchLastArtifactId,
    ${query ? "(SELECT MIN(search_id) FROM search_candidates)" : "NULL"} AS searchLastId)
  SELECT metadata.*,
    a.search_id AS searchId, a.artifact_id AS artifactId, a.preferred_name AS preferredName,
    art.artifact_type AS artifactType, a.created_at AS artifactCreatedAt,
    v.id AS versionId, a.version_count AS versionCount, v.version_number AS versionNumber,
    v.sha256, v.source_locator AS sourceLocator, v.media_type AS mediaType,
    v.original_file_name AS originalFileName, v.byte_size AS byteSize, v.created_at AS versionCreatedAt,
    j.id AS compileJobId, j.artifact_version_id AS compileArtifactVersionId,
    j.status AS compileStatus, j.stage AS compileStage, j.progress AS compileProgress,
    j.timings_json AS compileTimingsJson, j.error_json AS compileErrorJson,
    j.snapshot_id AS compileSnapshotId, j.created_at AS compileCreatedAt, j.updated_at AS compileUpdatedAt,
    s.id AS snapshotId, s.counts_json AS snapshotCountsJson, s.created_at AS snapshotCreatedAt,
    (SELECT COUNT(*) FROM verification_runs runs WHERE runs.project_id = a.project_id AND runs.snapshot_id = s.id) AS verificationRunCount,
    COALESCE(vs.limitation_count, 0) AS verificationLimitationCount,
    r.id AS reviewId, r.decision AS reviewDecision, r.rationale AS reviewRationale,
    r.reviewer, r.created_at AS reviewCreatedAt, a.library_status AS libraryStatus,
    MAX(CAST(strftime('%s', a.created_at) AS INTEGER),
      COALESCE(CAST(strftime('%s', v.created_at) AS INTEGER), 0),
      COALESCE(CAST(strftime('%s', j.updated_at) AS INTEGER), 0),
      COALESCE(CAST(strftime('%s', s.created_at) AS INTEGER), 0),
      COALESCE(CAST(strftime('%s', vr.created_at) AS INTEGER), 0),
      COALESCE(CAST(strftime('%s', r.created_at) AS INTEGER), 0)) AS updatedAtEpoch
  FROM metadata LEFT JOIN page_artifacts a ON 1=1
  LEFT JOIN artifacts art ON art.id = a.artifact_id
  LEFT JOIN artifact_versions v ON v.id = a.latest_version_id
  LEFT JOIN compile_jobs j ON j.id = a.latest_compile_job_id
  LEFT JOIN evidence_graph_snapshots s ON s.id = a.latest_snapshot_id
  LEFT JOIN verification_runs vr ON vr.id = (
    SELECT id FROM verification_runs WHERE project_id = a.project_id AND snapshot_id = s.id
    ORDER BY created_at DESC, id DESC LIMIT 1)
  LEFT JOIN library_verification_summaries vs ON vs.verification_id = vr.id
  LEFT JOIN graph_review_events r ON r.id = (
    SELECT id FROM graph_review_events WHERE project_id = a.project_id AND snapshot_id = s.id
    ORDER BY seq DESC LIMIT 1)
  ORDER BY ${query ? "a.search_id DESC" : "a.created_at DESC, a.artifact_id DESC"}` };
}

function parseLimit(value: string | null) {
  if (value === null) return DEFAULT_LIMIT;
  if (!/^[1-9][0-9]{0,2}$/.test(value)) {
    throw new ApiError(400, "INVALID_LIBRARY_LIMIT", `limit must be an integer from 1 to ${MAX_LIMIT}`);
  }
  const parsed = Number(value);
  if (parsed > MAX_LIMIT) {
    throw new ApiError(400, "INVALID_LIBRARY_LIMIT", `limit must be an integer from 1 to ${MAX_LIMIT}`);
  }
  return parsed;
}

function parsePage(value: string | null) {
  if (value === null) return null;
  if (!/^[1-9][0-9]{0,8}$/u.test(value)) {
    throw new ApiError(400, "INVALID_LIBRARY_PAGE", "page must be a positive integer");
  }
  if (Number(value) !== 1) {
    throw new ApiError(400, "LIBRARY_CURSOR_REQUIRED", "Use the nextCursor returned by the previous page");
  }
  return 1;
}

function parseQuery(value: string | null) {
  const query = value?.trim() ?? "";
  if (query.length > MAX_QUERY_LENGTH) {
    throw new ApiError(400, "INVALID_LIBRARY_QUERY", `query may contain at most ${MAX_QUERY_LENGTH} characters`);
  }
  return query;
}

function parseStatus(value: string | null): LibraryStatus {
  const status = value ?? "pending";
  if (!["pending", "processing", "completed", "failed", "all"].includes(status)) {
    throw new ApiError(400, "INVALID_LIBRARY_STATUS", "status must be pending, processing, completed, failed or all");
  }
  return status as LibraryStatus;
}

function encodeCursor(cursor: LibraryCursor) {
  const bytes = new TextEncoder().encode(JSON.stringify(cursor));
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary)
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replace(/=+$/u, "");
}

function decodeCursor(value: string | null): LibraryCursor | null {
  if (value === null) return null;
  if (!value || value.length > MAX_CURSOR_LENGTH || !/^[A-Za-z0-9_-]+$/.test(value)) {
    throw new ApiError(400, "INVALID_LIBRARY_CURSOR", "cursor is invalid");
  }
  try {
    const base64 = value.replaceAll("-", "+").replaceAll("_", "/")
      .padEnd(Math.ceil(value.length / 4) * 4, "=");
    const binary = atob(base64);
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    const decoded = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as Partial<LibraryCursor>;
    const keys = Object.keys(decoded).sort().join(",");
    if (keys !== "artifactId,createdAt,query,searchId,status,version"
      || decoded.version !== 3
      || !Number.isSafeInteger(decoded.searchId) || decoded.searchId! < 1
      || typeof decoded.createdAt !== "string"
      || decoded.createdAt.length > 64
      || !Number.isFinite(Date.parse(decoded.createdAt))
      || typeof decoded.artifactId !== "string"
      || !ARTIFACT_ID_RE.test(decoded.artifactId)
      || typeof decoded.query !== "string"
      || decoded.query.length > MAX_QUERY_LENGTH
      || typeof decoded.status !== "string"
      || !["pending", "processing", "completed", "failed", "all"].includes(decoded.status)) {
      throw new Error("invalid cursor payload");
    }
    return decoded as LibraryCursor;
  } catch {
    throw new ApiError(400, "INVALID_LIBRARY_CURSOR", "cursor is invalid");
  }
}

function parseCounts(value: string | null) {
  if (!value) return {};
  try {
    const decoded = JSON.parse(value) as unknown;
    if (!decoded || typeof decoded !== "object" || Array.isArray(decoded)) return {};
    return Object.fromEntries(Object.entries(decoded).filter(([, count]) => (
      typeof count === "number" && Number.isInteger(count) && count >= 0
    ))) as Record<string, number>;
  } catch {
    return {};
  }
}

function parseJson<T>(value: string | null, fallback: T): T {
  if (!value) return fallback;
  try { return JSON.parse(value) as T; } catch { return fallback; }
}

function libraryReceipt(row: LibraryRow) {
  const runCount = Number(row.verificationRunCount ?? 0);
  return {
    artifactId: row.artifactId,
    title: row.preferredName,
    artifactType: row.artifactType,
    libraryStatus: row.libraryStatus,
    versionCount: Number(row.versionCount ?? 0),
    updatedAt: new Date(Number(row.updatedAtEpoch) * 1_000).toISOString(),
    latestVersion: row.versionId ? {
      id: row.versionId,
      artifactId: row.artifactId,
      versionNumber: Number(row.versionNumber),
      sha256: row.sha256,
      locator: row.sourceLocator,
      mediaType: row.mediaType,
      fileName: row.originalFileName,
      byteSize: Number(row.byteSize),
      downloadUrl: `/api/artifacts/${row.sha256}`,
      createdAt: row.versionCreatedAt,
    } : null,
    compile: row.compileJobId ? {
      id: row.compileJobId,
      artifactVersionId: row.compileArtifactVersionId,
      status: row.compileStatus,
      stage: row.compileStage,
      progress: Number(row.compileProgress),
      timings: parseJson<Record<string, number | null>>(row.compileTimingsJson, {}),
      snapshotId: row.compileSnapshotId,
      error: parseJson<Record<string, unknown> | null>(row.compileErrorJson, null),
      createdAt: row.compileCreatedAt,
      updatedAt: row.compileUpdatedAt,
    } : null,
    snapshot: row.snapshotId ? {
      id: row.snapshotId,
      counts: parseCounts(row.snapshotCountsJson),
      createdAt: row.snapshotCreatedAt,
    } : null,
    machineVerification: {
      status: runCount > 0 ? "assessed" : "unassessed",
      runCount,
      limitationCount: Number(row.verificationLimitationCount ?? 0),
    },
    humanReview: row.reviewId ? {
      id: row.reviewId,
      snapshotId: row.snapshotId,
      decision: row.reviewDecision,
      rationale: row.reviewRationale,
      reviewer: row.reviewer,
      createdAt: row.reviewCreatedAt,
    } : null,
  };
}

function parseRequest(request: Request) {
  const parameters = new URL(request.url).searchParams;
  for (const key of parameters.keys()) {
    if (!["query", "cursor", "limit", "page", "status", "artifact_version_id"].includes(key) || parameters.getAll(key).length !== 1) {
      throw new ApiError(400, "INVALID_LIBRARY_REQUEST", "Library query parameters are invalid");
    }
  }
  const query = parseQuery(parameters.get("query"));
  const status = parseStatus(parameters.get("status"));
  const cursor = decodeCursor(parameters.get("cursor"));
  const page = parsePage(parameters.get("page"));
  if (cursor && page !== null) {
    throw new ApiError(400, "INVALID_LIBRARY_REQUEST", "cursor and page cannot be used together");
  }
  if (cursor && (cursor.query !== query || cursor.status !== status)) {
    throw new ApiError(400, "INVALID_LIBRARY_CURSOR", "cursor does not belong to this query");
  }
  const artifactVersionId = parameters.get("artifact_version_id");
  if (artifactVersionId !== null && (!artifactVersionId || artifactVersionId.length > 200)) {
    throw new ApiError(400, "INVALID_LIBRARY_REQUEST", "artifact_version_id is invalid");
  }
  return { query, status, cursor, page, artifactVersionId, limit: parseLimit(parameters.get("limit")) };
}

export async function GET(request: Request) {
  try {
    const { query, status, cursor, artifactVersionId, limit } = parseRequest(request);
    const { DB } = await getBindings();
    // SQLite lower() deliberately folds ASCII only; preserve non-ASCII literal spelling.
    const normalizedQuery = query.replace(/[A-Z]/g, (character) => character.toLowerCase());
    const { sql, bindings } = librarySql({ query: normalizedQuery, status, cursor, artifactVersionId, limit });
    const result = await DB.prepare(sql).bind(...bindings).all<LibraryRow>();
    const total = result.results[0]?.totalCount ?? null;
    const candidates = result.results.filter((row) => row.artifactId !== null);
    const hasMore = candidates.length > limit || Boolean(result.results[0]?.searchHasMore);
    const rows = candidates.slice(0, limit);
    const artifacts = rows.map(libraryReceipt);
    const last = candidates.length <= limit && result.results[0]?.searchHasMore
      ? { artifactCreatedAt: result.results[0].searchLastCreatedAt!, artifactId: result.results[0].searchLastArtifactId!, searchId: result.results[0].searchLastId! }
      : rows.at(-1);
    const nextCursor = hasMore && last ? encodeCursor({
      version: 3,
      searchId: last.searchId,
      createdAt: last.artifactCreatedAt,
      artifactId: last.artifactId,
      query,
      status,
    }) : null;
    return Response.json({
      ok: true,
      artifacts,
      pageInfo: {
        hasMore,
        nextCursor,
        page: 1,
        pageSize: limit,
        total,
        totalPages: total === null ? null : Math.max(1, Math.ceil(total / limit)),
      },
    });
  } catch (error) {
    return safeError(error);
  }
}
