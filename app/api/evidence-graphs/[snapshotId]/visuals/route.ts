import type { ArtifactVersion, SourceAnchor, StructuredVisualEvidence } from "@/lib/evidence-graph";
import { ApiError, getBindings, PROJECT_ID, safeError } from "@/lib/server/evidence-store";

type RouteContext = { params: Promise<{ snapshotId: string }> };
type VisualKind = "all" | "figure" | "table";

const SNAPSHOT_ID_RE = /^snapshot:sha256:[a-f0-9]{64}$/;
const DEFAULT_LIMIT = 5;
const MAX_LIMIT = 50;

type VisualPageRow = {
  rootArtifactVersionId: string;
  sourceTitle: string | null;
  sourceVersionJson: string | null;
  allCount: number;
  figureCount: number;
  tableCount: number;
  anchorJson: string | null;
  visualJson: string | null;
  recordKind: "figure" | "table" | null;
  recordLabel: string | null;
};

const VISUAL_PAGE_SQL = `WITH
  snapshot AS (
    SELECT s.root_artifact_version_id AS rootArtifactVersionId,
      s.graph_json AS graphJson,
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
      ) AS sourceVersionJson
    FROM evidence_graph_snapshots s
    WHERE s.project_id = ? AND s.id = ?
    LIMIT 1
  ),
  anchors AS (
    SELECT CAST(anchor.key AS INTEGER) AS sourceOrder,
      anchor.value AS anchorJson,
      json_extract(anchor.value, '$.id') AS anchorId,
      json_extract(anchor.value, '$.anchor_type') AS anchorType,
      json_extract(anchor.value, '$.selector.page') AS pageNumber,
      json_extract(anchor.value, '$.quote') AS quote,
      COALESCE(json_extract(anchor.value, '$.selector.region.unit'), 'none') || '|' ||
        COALESCE(json_extract(anchor.value, '$.selector.region.x'), '') || '|' ||
        COALESCE(json_extract(anchor.value, '$.selector.region.y'), '') || '|' ||
        COALESCE(json_extract(anchor.value, '$.selector.region.width'), '') || '|' ||
        COALESCE(json_extract(anchor.value, '$.selector.region.height'), '') AS regionKey
    FROM snapshot, json_each(snapshot.graphJson, '$.anchors') AS anchor
    WHERE json_extract(anchor.value, '$.anchor_type') IN ('figure', 'table')
  ),
  visuals AS (
    SELECT visual.value AS visualJson,
      json_extract(visual.value, '$.anchor_id') AS anchorId,
      json_extract(visual.value, '$.visual_kind') AS visualKind
    FROM snapshot, json_each(snapshot.graphJson, '$.visual_evidence') AS visual
  ),
  candidates AS (
    SELECT anchors.*,
      visuals.visualJson,
      COALESCE(visuals.visualKind, anchors.anchorType) AS recordKind,
      COALESCE(
        NULLIF(TRIM(CASE
          WHEN COALESCE(visuals.visualKind, anchors.anchorType) = 'figure'
            THEN json_extract(anchors.anchorJson, '$.selector.figure')
          ELSE json_extract(anchors.anchorJson, '$.selector.table')
        END), ''),
        CASE WHEN COALESCE(visuals.visualKind, anchors.anchorType) = 'figure'
          THEN '未编号图' ELSE '未编号表格' END
      ) AS recordLabel
    FROM anchors
    LEFT JOIN visuals ON visuals.anchorId = anchors.anchorId
  ),
  ranked AS (
    SELECT candidates.*,
      ROW_NUMBER() OVER (
        PARTITION BY recordKind, pageNumber, recordLabel, regionKey
        ORDER BY (visualJson IS NOT NULL) DESC,
          (quote IS NOT NULL AND TRIM(quote) <> '') DESC,
          sourceOrder ASC,
          anchorId ASC
      ) AS duplicateRank
    FROM candidates
  ),
  records AS (
    SELECT * FROM ranked WHERE duplicateRank = 1
  ),
  stats AS (
    SELECT COUNT(*) AS allCount,
      COALESCE(SUM(recordKind = 'figure'), 0) AS figureCount,
      COALESCE(SUM(recordKind = 'table'), 0) AS tableCount
    FROM records
  ),
  filtered AS (
    SELECT * FROM records
    WHERE ? = 'all' OR recordKind = ?
  ),
  pageRecords AS (
    SELECT * FROM filtered
    ORDER BY (pageNumber IS NULL) ASC, pageNumber ASC,
      recordLabel COLLATE NOCASE ASC, anchorId ASC
    LIMIT ? OFFSET ?
  )
  SELECT snapshot.rootArtifactVersionId,
    snapshot.sourceTitle,
    snapshot.sourceVersionJson,
    stats.allCount,
    stats.figureCount,
    stats.tableCount,
    pageRecords.anchorJson,
    pageRecords.visualJson,
    pageRecords.recordKind,
    pageRecords.recordLabel
  FROM snapshot
  CROSS JOIN stats
  LEFT JOIN pageRecords ON 1 = 1`;

function positiveInteger(value: string | null, fallback: number, maximum: number) {
  if (value === null) return fallback;
  if (!/^[1-9][0-9]{0,8}$/u.test(value)) {
    throw new ApiError(400, "INVALID_VISUAL_PAGE", "page and limit must be positive integers");
  }
  const parsed = Number(value);
  if (parsed > maximum) throw new ApiError(400, "INVALID_VISUAL_PAGE", "page or limit exceeds the supported range");
  return parsed;
}

function parseRequest(request: Request) {
  const parameters = new URL(request.url).searchParams;
  if (![...parameters.keys()].every((key) => ["kind", "page", "limit"].includes(key) && parameters.getAll(key).length === 1)) {
    throw new ApiError(400, "INVALID_VISUAL_PAGE_REQUEST", "Visual page parameters are invalid");
  }
  const kind = (parameters.get("kind") ?? "all") as VisualKind;
  if (!["all", "figure", "table"].includes(kind)) {
    throw new ApiError(400, "INVALID_VISUAL_KIND", "kind must be all, figure or table");
  }
  return {
    kind,
    page: positiveInteger(parameters.get("page"), 1, 100_000_000),
    limit: positiveInteger(parameters.get("limit"), DEFAULT_LIMIT, MAX_LIMIT),
  };
}

export async function GET(request: Request, context: RouteContext) {
  try {
    const { snapshotId } = await context.params;
    if (!SNAPSHOT_ID_RE.test(snapshotId)) throw new ApiError(400, "INVALID_SNAPSHOT_ID", "Snapshot ID is invalid");
    const { kind, page, limit } = parseRequest(request);
    const { DB } = await getBindings();
    const result = await DB.prepare(VISUAL_PAGE_SQL)
      .bind(PROJECT_ID, snapshotId, kind, kind, limit, (page - 1) * limit)
      .all<VisualPageRow>();
    const first = result.results[0];
    if (!first) throw new ApiError(404, "SNAPSHOT_NOT_FOUND", "Evidence graph snapshot was not found");

    const total = kind === "figure"
      ? Number(first.figureCount)
      : kind === "table"
        ? Number(first.tableCount)
        : Number(first.allCount);
    const records = result.results.flatMap((row) => (
      row.anchorJson && row.recordKind && row.recordLabel
        ? [{
            anchor: JSON.parse(row.anchorJson) as SourceAnchor,
            kind: row.recordKind,
            label: row.recordLabel,
            visual: row.visualJson ? JSON.parse(row.visualJson) as StructuredVisualEvidence : null,
          }]
        : []
    ));

    return Response.json({
      ok: true,
      snapshotId,
      rootArtifactVersionId: first.rootArtifactVersionId,
      sourceTitle: first.sourceTitle,
      sourceVersion: first.sourceVersionJson ? JSON.parse(first.sourceVersionJson) as ArtifactVersion : null,
      records,
      counts: {
        all: Number(first.allCount),
        figure: Number(first.figureCount),
        table: Number(first.tableCount),
      },
      pageInfo: {
        page,
        pageSize: limit,
        total,
        totalPages: Math.max(1, Math.ceil(total / limit)),
        hasMore: page * limit < total,
      },
    });
  } catch (error) {
    return safeError(error);
  }
}
