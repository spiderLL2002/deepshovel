/**
 * Background retrieval indexing: one batched manifest check for due items,
 * then resumable per-snapshot indexing that saves its chunk position.
 */
import { loadEvidenceRetrievalManifestsBySnapshotIds } from "@/lib/evidence-retrieval";
import { PROJECT_ID } from "@/lib/server/evidence-store";
import type { RetrievalClient } from "@/lib/server/retrieval-client";
import {
  checkRetrievalManifests,
  indexEvidenceRetrievalSnapshot,
  needsIndexing,
} from "@/lib/server/retrieval-sync";
import {
  cancelLeasedWork,
  claimDueWork,
  completeWork,
  deferWork,
} from "@/lib/server/work-queue";
import { recordFailure, retrievalClient, type WorkPassSummary } from "./work-support.ts";

const INDEX_ITEMS_PER_PASS = 64;

/** Index work: one batched manifest check, then resumable per-snapshot indexing. */
export async function runRetrievalIndexWork(DB: D1Database, deadline: number, summary: WorkPassSummary) {
  const items = await claimDueWork(DB, PROJECT_ID, { kinds: ["retrieval_index"], limit: INDEX_ITEMS_PER_PASS });
  summary.claimed += items.length;
  if (!items.length) return false;
  const manifests = new Map((await loadEvidenceRetrievalManifestsBySnapshotIds(
    DB, PROJECT_ID, items.map((item) => item.subjectId),
  )).map((manifest) => [manifest.snapshotId, manifest]));
  let client: RetrievalClient;
  let states: Awaited<ReturnType<typeof checkRetrievalManifests>>;
  const present = items.filter((item) => manifests.has(item.subjectId));
  for (const item of items) {
    if (!manifests.has(item.subjectId)) {
      await cancelLeasedWork(DB, item, "snapshot_removed");
      summary.cancelled += 1;
    }
  }
  if (!present.length) return false;
  try {
    client = await retrievalClient();
    states = await checkRetrievalManifests(client, present.map((item) => manifests.get(item.subjectId)!));
  } catch (error) {
    for (const item of present) await recordFailure(DB, item, error, summary);
    return false;
  }
  let more = false;
  for (const item of present) {
    const manifest = manifests.get(item.subjectId)!;
    const state = states.get(item.subjectId);
    if (!needsIndexing(manifest, state)) {
      await completeWork(DB, item, { status: state?.status ?? "indexed", documents: manifest.documentCount });
      summary.succeeded += 1;
      continue;
    }
    if (Date.now() >= deadline) {
      await deferWork(DB, item, 0, item.progress);
      summary.deferred += 1;
      more = true;
      continue;
    }
    const startChunk = Number.isSafeInteger(item.progress.nextChunk) ? Number(item.progress.nextChunk) : 0;
    try {
      const progress = await indexEvidenceRetrievalSnapshot(DB, client, manifest, state, { startChunk, deadline });
      if (progress.done) {
        await completeWork(DB, item, {
          status: progress.status,
          documents: manifest.documentCount,
          ...(progress.warnings.length ? { warnings: progress.warnings.slice(0, 8) } : {}),
        });
        summary.succeeded += 1;
      } else {
        await deferWork(DB, item, 0, { nextChunk: progress.nextChunk, totalChunks: progress.totalChunks });
        summary.deferred += 1;
        more = true;
      }
    } catch (error) {
      await recordFailure(DB, item, error, summary, { nextChunk: startChunk });
    }
  }
  return more;
}

