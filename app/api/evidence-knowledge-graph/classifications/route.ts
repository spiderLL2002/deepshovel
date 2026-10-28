import { OpenAlexClassifierClient } from "@/lib/server/openalex-classifier-client";
import { mapWithConcurrency } from "@/lib/server/concurrency";
import {
  ApiError,
  assertSameOrigin,
  getBindings,
  getCompilerConfiguration,
  PROJECT_ID,
  readJsonObject,
  safeError,
} from "@/lib/server/evidence-store";
import { parseOpenAlexClassification } from "../../evidence-graphs/import-contract.ts";
import { persistOpenAlexClassification } from "../../evidence-graphs/import-store.ts";

const MAX_REQUEST_BYTES = 256;
const MAX_BATCH = 16;

type MissingRow = { snapshotId: string; title: string };

export async function POST(request: Request) {
  try {
    assertSameOrigin(request);
    const payload = await readJsonObject(request, MAX_REQUEST_BYTES);
    if (Object.keys(payload).some((key) => key !== "limit")
      || (payload.limit !== undefined && (!Number.isInteger(payload.limit) || Number(payload.limit) < 1 || Number(payload.limit) > MAX_BATCH))) {
      throw new ApiError(400, "INVALID_OPENALEX_BACKFILL_REQUEST", `limit must be an integer from 1 to ${MAX_BATCH}`);
    }
    const limit = payload.limit === undefined ? MAX_BATCH : Number(payload.limit);
    const { DB } = await getBindings();
    const pendingCount = await DB.prepare(`SELECT unclassified_papers AS total
      FROM graph_read_counts WHERE project_id = ?`).bind(PROJECT_ID).first<{ total: number }>();
    if (!Number(pendingCount?.total ?? 0)) return Response.json({ ok: true, classified: 0, remaining: 0 });
    const rows = await DB.prepare(`WITH pending AS MATERIALIZED (
      SELECT active.snapshot_id, manifest.root_artifact_version_id
      FROM graph_unclassified_snapshots active INDEXED BY graph_unclassified_queue_idx
      CROSS JOIN evidence_retrieval_manifests manifest ON manifest.snapshot_id = active.snapshot_id
      WHERE active.project_id = ?
      ORDER BY active.created_at, active.snapshot_id LIMIT ?
    ) SELECT pending.snapshot_id AS snapshotId,
      COALESCE(NULLIF((SELECT json_extract(document.document_json, '$.paper_title')
        FROM evidence_retrieval_documents document WHERE document.snapshot_id = pending.snapshot_id
        ORDER BY document.claim_id LIMIT 1), ''), artifact.preferred_name) AS title
      FROM pending CROSS JOIN artifact_versions version ON version.id = pending.root_artifact_version_id
      CROSS JOIN artifacts artifact ON artifact.id = version.artifact_id`)
      .bind(PROJECT_ID, limit).all<MissingRow>();
    if (!rows.results.length) return Response.json({ ok: true, classified: 0, remaining: 0 });
    const configuration = await getCompilerConfiguration();
    if (!configuration.sharedSecret) {
      throw new ApiError(503, "COMPILER_NOT_CONFIGURED", "OpenAlex 分类需要 Compiler 共享凭据");
    }
    const client = new OpenAlexClassifierClient({ ...configuration, sharedSecret: configuration.sharedSecret });
    await mapWithConcurrency(rows.results, 8, async (row) => {
      const classification = parseOpenAlexClassification(await client.classify(
        { title: row.title },
        request.signal,
      ), "$");
      if (!classification) throw new ApiError(502, "OPENALEX_CLASSIFICATION_MISSING", "OpenAlex 分类器没有返回分类");
      await persistOpenAlexClassification(DB, row.snapshotId, classification);
    });
    const remainingRow = await DB.prepare(`SELECT unclassified_papers AS total
      FROM graph_read_counts WHERE project_id = ?`)
      .bind(PROJECT_ID).first<{ total: number }>();
    return Response.json({
      ok: true,
      classified: rows.results.length,
      remaining: Number(remainingRow?.total ?? 0),
    });
  } catch (error) {
    return safeError(error);
  }
}
