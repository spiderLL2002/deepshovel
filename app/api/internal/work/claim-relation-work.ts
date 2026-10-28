/**
 * Claim-relation analysis for imported snapshots. The Compiler keeps a batch
 * only in memory; durable progress is the per-Claim completion rows, so a lost
 * batch is replaced by a new one for the Claims that are still unprocessed.
 */
import {
  loadActiveEvidenceRetrievalManifests,
  type EvidenceRetrievalManifest,
} from "@/lib/evidence-retrieval";
import { ApiError, PROJECT_ID } from "@/lib/server/evidence-store";
import type { RetrievalClient } from "@/lib/server/retrieval-client";
import {
  cancelLeasedWork,
  claimDueWork,
  completeWork,
  deferWork,
  getWorkItem,
  type WorkItem,
} from "@/lib/server/work-queue";
import {
  persistBatchItems,
  relationBatchId,
  relationBatchRequestId,
  unprocessedClaims,
} from "../../evidence-knowledge-graph/relation-batches.ts";
import { recordFailure, retrievalClient, type WorkPassSummary } from "./work-support.ts";

const RELATION_ITEMS_PER_PASS = 16;
// The Compiler keeps at most 32 batches in memory and runs each batch's Claims
// concurrently under its 64 bulk model slots; interactive answers use separate
// slots. A batch lasts about one model round-trip whatever its size, and most
// snapshots hold ~5 Claims, so 12 batches fill the bulk slots while leaving
// batch capacity for interactive analysis. (4 left ~20 slots busy: ~7 Claims/min.)
const MAX_ACTIVE_RELATION_BATCHES = 12;
const BATCH_POLL_DELAY_MS = 3_000;
const DEPENDENCY_DELAY_MS = 15_000;

type RelationProgress = {
  generation: number;
  batchId?: string;
  clientRequestId?: string;
  cursor?: number;
  total?: number;
  persisted: number;
  edges: number;
  failedClaims?: number;
};

function relationProgress(item: WorkItem): RelationProgress {
  const value = item.progress;
  return {
    generation: Number.isSafeInteger(value.generation) ? Number(value.generation) : 0,
    ...(typeof value.batchId === "string" ? { batchId: value.batchId } : {}),
    ...(typeof value.clientRequestId === "string" ? { clientRequestId: value.clientRequestId } : {}),
    ...(Number.isSafeInteger(value.cursor) ? { cursor: Number(value.cursor) } : {}),
    ...(Number.isSafeInteger(value.total) ? { total: Number(value.total) } : {}),
    persisted: Number.isSafeInteger(value.persisted) ? Number(value.persisted) : 0,
    edges: Number.isSafeInteger(value.edges) ? Number(value.edges) : 0,
  };
}

async function activeRelationBatches(DB: D1Database) {
  const row = await DB.prepare(`SELECT COUNT(*) AS active FROM work_items
    WHERE project_id = ? AND kind = 'claim_relations' AND state IN ('pending', 'running')
      AND json_extract(progress_json, '$.batchId') IS NOT NULL`).bind(PROJECT_ID).first<{ active: number }>();
  return Number(row?.active ?? 0);
}

function isMissingBatch(error: unknown) {
  return error instanceof ApiError && (error.code === "UPSTREAM_JOB_NOT_FOUND" || error.status === 404);
}

/**
 * Claim-relation analysis for one snapshot. The Compiler batch lives only in
 * its memory, so progress is the D1 completion rows: a lost batch (restart) is
 * replaced by a new one for the Claims still unprocessed. A batch is looked up
 * before it is created, so an unacknowledged start is never sent twice.
 */
async function runClaimRelationsItem(DB: D1Database, item: WorkItem, active: EvidenceRetrievalManifest[], summary: WorkPassSummary) {
  if (!active.some((manifest) => manifest.snapshotId === item.subjectId)) {
    await cancelLeasedWork(DB, item, "snapshot_not_active");
    summary.cancelled += 1;
    return false;
  }
  const index = await getWorkItem(DB, PROJECT_ID, "retrieval_index", item.subjectId);
  if (index && index.state !== "succeeded") {
    if (index.state === "failed" || index.state === "cancelled") {
      await recordFailure(DB, item, new ApiError(503, "RETRIEVAL_INDEX_UNAVAILABLE",
        "The paper is not searchable, so its Claim relations cannot be analysed yet", { retryable: true }), summary);
    } else {
      await deferWork(DB, item, DEPENDENCY_DELAY_MS);
      summary.deferred += 1;
    }
    return false;
  }
  const corpus = active.map((manifest) => manifest.snapshotId);
  const client = await retrievalClient();
  const progress = relationProgress(item);
  let batch: Awaited<ReturnType<RetrievalClient["getClaimRelationBatch"]>> | null = null;
  if (progress.batchId) {
    try {
      batch = await client.getClaimRelationBatch(progress.batchId, corpus, undefined, progress.cursor ?? 0);
    } catch (error) {
      if (!isMissingBatch(error)) throw error;
      // The Compiler restarted: resume with the Claims that are still pending.
      const next: RelationProgress = { generation: progress.generation + 1, persisted: progress.persisted, edges: progress.edges };
      await deferWork(DB, item, 0, next);
      summary.deferred += 1;
      return true;
    }
  } else {
    const refs = await unprocessedClaims(DB, [item.subjectId]);
    if (!refs.length) {
      await completeWork(DB, item, { ...progress, remaining: 0 });
      summary.succeeded += 1;
      return false;
    }
    if (await activeRelationBatches(DB) >= MAX_ACTIVE_RELATION_BATCHES) {
      await deferWork(DB, item, DEPENDENCY_DELAY_MS, { ...progress });
      summary.deferred += 1;
      return false;
    }
    const clientRequestId = `${await relationBatchRequestId(refs, corpus)}:g${progress.generation}`;
    const batchId = await relationBatchId(clientRequestId);
    try {
      batch = await client.getClaimRelationBatch(batchId, corpus, undefined, 0);
    } catch (error) {
      if (!isMissingBatch(error)) throw error;
      batch = await client.startClaimRelationBatch(PROJECT_ID, clientRequestId, corpus, refs, false, undefined, true, 0);
    }
    progress.batchId = batch.batch_id;
    progress.clientRequestId = clientRequestId;
    progress.cursor = 0;
    progress.total = batch.total;
  }
  const persisted = await persistBatchItems(DB, batch);
  progress.persisted += persisted.persisted;
  progress.edges += persisted.edges;
  progress.cursor = batch.next_cursor ?? batch.completed;
  const drained = progress.cursor >= batch.completed;
  if (batch.status === "running" || !drained) {
    await deferWork(DB, item, drained ? BATCH_POLL_DELAY_MS : 0, { ...progress });
    summary.deferred += 1;
    return !drained;
  }
  if (batch.status === "cancelled") {
    // Cancelled or retired on the Compiler side (for example the corpus lost a
    // snapshot): start over with the Claims that are still pending.
    await deferWork(DB, item, 0, { generation: progress.generation + 1, persisted: progress.persisted, edges: progress.edges });
    summary.deferred += 1;
    return true;
  }
  const remaining = (await unprocessedClaims(DB, [item.subjectId], 1)).length;
  if (remaining) {
    await recordFailure(DB, item, new ApiError(503, "CLAIM_RELATION_PROCESSING_INCOMPLETE",
      "Some Claims could not be analysed; they will be retried", { retryable: true }), summary,
    { generation: progress.generation + 1, persisted: progress.persisted, edges: progress.edges, failedClaims: batch.failed });
    return false;
  }
  await completeWork(DB, item, { ...progress, remaining: 0 });
  summary.succeeded += 1;
  return false;
}

export async function runClaimRelationWork(DB: D1Database, deadline: number, summary: WorkPassSummary) {
  const items = await claimDueWork(DB, PROJECT_ID, { kinds: ["claim_relations"], limit: RELATION_ITEMS_PER_PASS });
  summary.claimed += items.length;
  if (!items.length) return false;
  let more = items.length >= RELATION_ITEMS_PER_PASS;
  const active = await loadActiveEvidenceRetrievalManifests(DB, PROJECT_ID);
  for (const item of items) {
    if (Date.now() >= deadline) {
      await deferWork(DB, item, 0, item.progress);
      summary.deferred += 1;
      more = true;
      continue;
    }
    try {
      more = await runClaimRelationsItem(DB, item, active, summary) || more;
    } catch (error) {
      await recordFailure(DB, item, error, summary, item.progress);
    }
  }
  return more;
}

