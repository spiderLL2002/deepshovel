import type { ArtifactVersion } from "@/lib/evidence-graph";
import { enrichBibliographicMetadata } from "@/lib/server/bibliographic-metadata.ts";
import { CompilerClient } from "@/lib/server/compiler-client.ts";
import {
  loadDocumentExtraction,
  loadRefreshableDocumentExtraction,
  persistDocumentExtraction,
} from "@/lib/server/document-extraction-store.ts";
import {
  ApiError,
  getBindings,
  getCompilerConfiguration,
  PROJECT_ID,
  safeError,
} from "@/lib/server/evidence-store";

type RouteContext = { params: Promise<{ snapshotId: string }> };
type DocumentSourceRow = {
  rootArtifactVersionId: string;
  sourceTitle: string | null;
  sourceVersionJson: string | null;
  artifactSha256: string;
  upstreamJobId: string | null;
};

const SNAPSHOT_ID_RE = /^snapshot:sha256:[a-f0-9]{64}$/u;
const COMPILER_JOB_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu;
const DEFAULT_VISUAL_LIMIT = 5;
const MAX_VISUAL_LIMIT = 50;
type DocumentSection = "metadata" | "text" | "visuals";
type VisualKind = "all" | "figure" | "table";

const DOCUMENT_SOURCE_SQL = `SELECT
  s.root_artifact_version_id AS rootArtifactVersionId,
  (
    SELECT json_extract(artifact.value, '$.canonical_name')
    FROM json_each(s.graph_json, '$.artifacts') AS artifact
    LIMIT 1
  ) AS sourceTitle,
  COALESCE(
    (
      SELECT version.value
      FROM json_each(s.graph_json, '$.artifact_versions') AS version
      WHERE json_extract(version.value, '$.id') = s.root_artifact_version_id
      LIMIT 1
    ),
    (
      SELECT version.value
      FROM json_each(s.graph_json, '$.artifact_versions') AS version
      LIMIT 1
    )
  ) AS sourceVersionJson,
  v.sha256 AS artifactSha256,
  (
    SELECT j.upstream_job_id
    FROM compile_jobs j
    WHERE j.project_id = s.project_id
      AND j.artifact_version_id = s.root_artifact_version_id
      AND j.status = 'completed'
    ORDER BY j.updated_at DESC
    LIMIT 1
  ) AS upstreamJobId
FROM evidence_graph_snapshots s
JOIN artifact_versions v ON v.id = s.root_artifact_version_id
WHERE s.project_id = ? AND s.id = ?
LIMIT 1`;

async function fetchAndFreeze(row: DocumentSourceRow, fallback: Awaited<ReturnType<typeof loadRefreshableDocumentExtraction>>) {
  const compiler = new CompilerClient(await getCompilerConfiguration());
  let extraction;
  try {
    extraction = await compiler.documentExtractionByVersion(row.rootArtifactVersionId);
  } catch (versionError) {
    try {
      if (!row.upstreamJobId || !COMPILER_JOB_ID_RE.test(row.upstreamJobId)) throw versionError;
      extraction = await compiler.documentExtraction(row.upstreamJobId);
    } catch (jobError) {
      if (!fallback) throw jobError;
      extraction = fallback;
    }
  }
  if (extraction.artifact_version_id !== row.rootArtifactVersionId
    || extraction.artifact_sha256 !== row.artifactSha256) {
    throw new ApiError(502, "DOCUMENT_EXTRACTION_PROVENANCE_MISMATCH", "Document extraction does not match the selected paper");
  }
  let enriched: Awaited<ReturnType<typeof enrichBibliographicMetadata>>;
  try {
    enriched = await enrichBibliographicMetadata(extraction);
  } catch {
    // Bibliographic enrichment is repairable and must never hide a valid
    // locally extracted document when an authority service is unavailable.
    return extraction;
  }
  if (enriched.status === "deferred") return enriched.extraction;
  return (await persistDocumentExtraction(enriched.extraction, {
    bibliographicStatus: enriched.status,
  })).extraction;
}

function positiveInteger(value: string | null, fallback: number, maximum: number) {
  if (value === null) return fallback;
  if (!/^[1-9][0-9]{0,8}$/u.test(value)) {
    throw new ApiError(400, "INVALID_DOCUMENT_PAGE", "page and limit must be positive integers");
  }
  const parsed = Number(value);
  if (parsed > maximum) throw new ApiError(400, "INVALID_DOCUMENT_PAGE", "page or limit exceeds the supported range");
  return parsed;
}

function parseRequest(request: Request) {
  const parameters = new URL(request.url).searchParams;
  if (!parameters.size) return null;
  const allowed = new Set(["section", "kind", "page", "limit"]);
  if (![...parameters.keys()].every((key) => allowed.has(key) && parameters.getAll(key).length === 1)) {
    throw new ApiError(400, "INVALID_DOCUMENT_REQUEST", "Document section parameters are invalid");
  }
  const section = parameters.get("section") as DocumentSection | null;
  if (!section || !["metadata", "text", "visuals"].includes(section)) {
    throw new ApiError(400, "INVALID_DOCUMENT_SECTION", "section must be metadata, text or visuals");
  }
  if (section !== "visuals" && [...parameters.keys()].some((key) => key !== "section")) {
    throw new ApiError(400, "INVALID_DOCUMENT_REQUEST", "Only the visuals section accepts kind, page and limit");
  }
  const kind = (parameters.get("kind") ?? "all") as VisualKind;
  if (!["all", "figure", "table"].includes(kind)) {
    throw new ApiError(400, "INVALID_DOCUMENT_VISUAL_KIND", "kind must be all, figure or table");
  }
  return {
    section,
    kind,
    page: positiveInteger(parameters.get("page"), 1, 100_000_000),
    limit: positiveInteger(parameters.get("limit"), DEFAULT_VISUAL_LIMIT, MAX_VISUAL_LIMIT),
  };
}

export async function GET(request: Request, context: RouteContext) {
  try {
    const { snapshotId } = await context.params;
    if (!SNAPSHOT_ID_RE.test(snapshotId)) {
      throw new ApiError(400, "INVALID_SNAPSHOT_ID", "Snapshot ID must be content-addressed");
    }
    const selection = parseRequest(request);
    const { DB } = await getBindings();
    const row = await DB.prepare(DOCUMENT_SOURCE_SQL)
      .bind(PROJECT_ID, snapshotId)
      .first<DocumentSourceRow>();
    if (!row) throw new ApiError(404, "SNAPSHOT_NOT_FOUND", "Evidence graph snapshot was not found");

    let extraction = await loadDocumentExtraction(row.artifactSha256);
    if (!extraction) {
      const fallback = await loadRefreshableDocumentExtraction(row.artifactSha256);
      extraction = await fetchAndFreeze(row, fallback);
    }
    const sourceVersion = row.sourceVersionJson
      ? JSON.parse(row.sourceVersionJson) as ArtifactVersion
      : null;
    const base = {
      ok: true,
      snapshotId,
      rootArtifactVersionId: row.rootArtifactVersionId,
      sourceTitle: row.sourceTitle,
      sourceVersion,
    } as const;
    if (!selection) {
      return Response.json({ ...base, extraction }, { headers: { "cache-control": "private, no-store" } });
    }
    if (selection.section === "metadata") {
      return Response.json({ ...base, section: selection.section, extraction: {
        schema_version: extraction.schema_version,
        artifact_version_id: extraction.artifact_version_id,
        artifact_sha256: extraction.artifact_sha256,
        parser: extraction.parser,
        metadata: extraction.metadata,
        field_sources: extraction.field_sources,
        stats: extraction.stats,
        warnings: extraction.warnings,
      } }, {
        headers: { "cache-control": "private, no-store" },
      });
    }
    if (selection.section === "text") {
      return Response.json({ ...base, section: selection.section, markdown: extraction.markdown }, {
        headers: { "cache-control": "private, no-store" },
      });
    }
    const counts = {
      all: extraction.visuals.length,
      figure: extraction.visuals.filter((record) => record.kind === "figure").length,
      table: extraction.visuals.filter((record) => record.kind === "table").length,
    };
    const filtered = selection.kind === "all"
      ? extraction.visuals
      : extraction.visuals.filter((record) => record.kind === selection.kind);
    const offset = (selection.page - 1) * selection.limit;
    return Response.json({
      ...base,
      section: selection.section,
      kind: selection.kind,
      records: filtered.slice(offset, offset + selection.limit),
      counts,
      pageInfo: {
        page: selection.page,
        pageSize: selection.limit,
        total: filtered.length,
        totalPages: Math.max(1, Math.ceil(filtered.length / selection.limit)),
        hasMore: selection.page * selection.limit < filtered.length,
      },
    }, { headers: { "cache-control": "private, no-store" } });
  } catch (error) {
    return safeError(error);
  }
}
