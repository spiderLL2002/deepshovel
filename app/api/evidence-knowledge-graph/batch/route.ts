import { SNAPSHOT_ID_RE } from "@/lib/evidence-graph/canonical";
import { loadActiveEvidenceRetrievalManifests } from "@/lib/evidence-retrieval";
import {
  ApiError,
  assertSameOrigin,
  getBindings,
  getCompilerConfiguration,
  PROJECT_ID,
  readJsonObject,
  safeError,
} from "@/lib/server/evidence-store";
import { RetrievalClient } from "@/lib/server/retrieval-client";
import { cancelWork, getWorkItem, requestWork, type WorkItem } from "@/lib/server/work-queue";
import { unprocessedClaims } from "../relation-batches.ts";

/**
 * Claim-relation analysis is durable background work owned by the server-side
 * work queue (see app/api/internal/work). These endpoints only request,
 * observe or cancel that work; none of them runs the analysis or persists
 * results, so closing the browser never loses progress.
 */
const MAX_REQUEST_BYTES = 256;

function publicItem(item: WorkItem | null, snapshotId: string) {
  if (!item) return { snapshotId, state: "none" as const };
  return {
    snapshotId,
    id: item.id,
    state: item.state,
    attempts: item.attempts,
    total: Number.isSafeInteger(item.progress.total) ? Number(item.progress.total) : null,
    persisted: Number.isSafeInteger(item.progress.persisted) ? Number(item.progress.persisted) : 0,
    edges: Number.isSafeInteger(item.progress.edges) ? Number(item.progress.edges) : 0,
    error: item.lastError ? { code: item.lastError.code, message: item.lastError.message } : null,
    updatedAt: item.updatedAt,
  };
}

function parseSnapshotId(value: unknown) {
  if (value === undefined || value === null) return null;
  if (typeof value !== "string" || !SNAPSHOT_ID_RE.test(value.trim())) {
    throw new ApiError(400, "INVALID_KNOWLEDGE_GRAPH_BATCH_REQUEST", "snapshot_id is invalid");
  }
  return value.trim();
}

function snapshotParameter(request: Request) {
  const parameters = new URL(request.url).searchParams;
  if ([...parameters.keys()].some((key) => key !== "snapshotId") || parameters.getAll("snapshotId").length !== 1) {
    throw new ApiError(400, "INVALID_KNOWLEDGE_GRAPH_BATCH_REQUEST", "snapshotId is required");
  }
  return parseSnapshotId(parameters.get("snapshotId"))!;
}

/** Request analysis of unprocessed Claims for one active snapshot, or for all of them. */
export async function POST(request: Request) {
  try {
    assertSameOrigin(request);
    const payload = await readJsonObject(request, MAX_REQUEST_BYTES);
    const unknown = Object.keys(payload).filter((key) => key !== "snapshot_id");
    if (unknown.length) {
      throw new ApiError(400, "INVALID_KNOWLEDGE_GRAPH_BATCH_REQUEST", "Batch request contains unknown fields");
    }
    const snapshotId = parseSnapshotId(payload.snapshot_id);
    const { DB } = await getBindings();
    const active = (await loadActiveEvidenceRetrievalManifests(DB, PROJECT_ID)).map((manifest) => manifest.snapshotId);
    const targets = snapshotId ? active.filter((id) => id === snapshotId) : active;
    if (snapshotId && !targets.length) {
      throw new ApiError(404, "KNOWLEDGE_GRAPH_SNAPSHOT_NOT_ACTIVE", "The paper was deleted or replaced by a newer version");
    }
    const pending: string[] = [];
    for (let offset = 0; offset < targets.length; offset += 256) {
      const chunk = targets.slice(offset, offset + 256);
      const refs = await unprocessedClaims(DB, chunk);
      pending.push(...new Set(refs.map((ref) => ref.snapshotId)));
    }
    const requested = await requestWork(DB, PROJECT_ID, "claim_relations", pending);
    return Response.json({ ok: true, requested, pendingSnapshots: pending.length }, { status: 202 });
  } catch (error) {
    return safeError(error);
  }
}

/** Read-only progress of one snapshot's Claim-relation work item. */
export async function GET(request: Request) {
  try {
    const snapshotId = snapshotParameter(request);
    const { DB } = await getBindings();
    const item = await getWorkItem(DB, PROJECT_ID, "claim_relations", snapshotId);
    return Response.json({ ok: true, work: publicItem(item, snapshotId) }, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    return safeError(error);
  }
}

/** Stop queued or running analysis for one snapshot; completed Claims stay. */
export async function DELETE(request: Request) {
  try {
    assertSameOrigin(request);
    const snapshotId = snapshotParameter(request);
    const { DB } = await getBindings();
    const item = await getWorkItem(DB, PROJECT_ID, "claim_relations", snapshotId);
    const cancelled = await cancelWork(DB, PROJECT_ID, "claim_relations", snapshotId, "user_cancelled");
    const batchId = typeof item?.progress.batchId === "string" ? item.progress.batchId : null;
    if (cancelled && batchId) {
      // Best effort: stop model spend for the in-memory Compiler batch too.
      try {
        const configuration = await getCompilerConfiguration();
        if (configuration.sharedSecret) {
          const active = (await loadActiveEvidenceRetrievalManifests(DB, PROJECT_ID)).map((manifest) => manifest.snapshotId);
          await new RetrievalClient({ ...configuration, sharedSecret: configuration.sharedSecret })
            .cancelClaimRelationBatch(batchId, active.length ? active : [snapshotId]);
        }
      } catch {
        // The batch may already be gone (for example after a Compiler restart).
      }
    }
    const current = await getWorkItem(DB, PROJECT_ID, "claim_relations", snapshotId);
    return Response.json({ ok: true, cancelled, work: publicItem(current, snapshotId) });
  } catch (error) {
    return safeError(error);
  }
}
