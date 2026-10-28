import {
  loadEvidenceRetrievalIndexBundles,
  toEvidenceRetrievalIndexRequestDto,
  toEvidenceRetrievalManifestDto,
  type EvidenceRetrievalDocumentDto,
  type EvidenceRetrievalManifest,
} from "@/lib/evidence-retrieval";
import { ApiError, PROJECT_ID } from "@/lib/server/evidence-store";
import { appendPublicWarning } from "@/lib/server/public-warning";
import {
  RETRIEVAL_MAX_INDEX_DOCUMENTS,
  RETRIEVAL_MAX_MANIFESTS,
  RetrievalClient,
} from "@/lib/server/retrieval-client";
import { requestWork } from "@/lib/server/work-queue";

const MAX_INDEX_REQUEST_BYTES = 7_500_000;
// Indexing runs in the background work pump, never on a search request, so it
// can batch documents normally. Each request still embeds a bounded batch.
export const RETRIEVAL_INDEX_BATCH_DOCUMENTS = 32;

type ManifestState = Awaited<ReturnType<RetrievalClient["checkManifests"]>>["manifests"][number];

export type RetrievalCoverage = {
  checkedSnapshots: number;
  /** Active snapshots the retrieval service cannot search yet. */
  pendingSnapshots: number;
  warnings: string[];
};

function chunksOf<T>(values: readonly T[], maximum: number) {
  const chunks: T[][] = [];
  for (let index = 0; index < values.length; index += maximum) {
    chunks.push(values.slice(index, index + maximum));
  }
  return chunks;
}

function utf8Bytes(value: unknown) {
  return new TextEncoder().encode(JSON.stringify(value)).byteLength;
}

/** Read the retrieval service's state for every manifest (pages of 4,096). */
export async function checkRetrievalManifests(
  client: RetrievalClient,
  manifests: readonly EvidenceRetrievalManifest[],
  signal?: AbortSignal,
) {
  const states = new Map<string, ManifestState>();
  for (const page of chunksOf(manifests, RETRIEVAL_MAX_MANIFESTS)) {
    const response = await client.checkManifests(PROJECT_ID, page.map(toEvidenceRetrievalManifestDto), signal);
    for (const state of response.manifests) states.set(state.snapshot_id, state);
  }
  return states;
}

/** True when the retrieval service must (re)receive documents for the snapshot. */
export function needsIndexing(manifest: EvidenceRetrievalManifest, state: ManifestState | undefined) {
  if (!state || state.status === "missing" || state.status === "stale") return true;
  if (state.status === "degraded") return state.indexed_document_count < manifest.documentCount;
  return state.document_count !== manifest.documentCount
    || state.indexed_document_count !== manifest.documentCount;
}

/**
 * Search-path coverage check. It never indexes and never fails a search because
 * one snapshot cannot be indexed: snapshots that are not searchable yet are
 * handed to the durable work queue and reported as a public warning.
 */
export async function ensureRetrievalCoverage(
  DB: D1Database,
  client: RetrievalClient,
  manifests: readonly EvidenceRetrievalManifest[],
  signal?: AbortSignal,
): Promise<RetrievalCoverage> {
  const warnings: string[] = [];
  const states = await checkRetrievalManifests(client, manifests, signal);
  const pending = manifests.filter((manifest) => needsIndexing(manifest, states.get(manifest.snapshotId)));
  const degraded = manifests.filter((manifest) => {
    const state = states.get(manifest.snapshotId);
    return state?.status === "degraded" && !needsIndexing(manifest, state);
  });
  if (pending.length) {
    await requestWork(DB, PROJECT_ID, "retrieval_index", pending.map((manifest) => manifest.snapshotId));
    appendPublicWarning(
      warnings,
      `${pending.length} 篇论文的检索索引仍在后台建立，本次结果暂不包含它们。`,
    );
  }
  if (degraded.length) {
    appendPublicWarning(
      warnings,
      `${degraded.length} 篇论文的语义向量通道当前降级，本次仅按词面检索这些论文。`,
    );
  }
  return { checkedSnapshots: manifests.length, pendingSnapshots: pending.length, warnings };
}

/** Bound both row count and encoded body bytes; no legal document is silently dropped. */
function indexDocumentChunks(
  projectId: string,
  manifest: ReturnType<typeof toEvidenceRetrievalManifestDto>,
  documents: readonly EvidenceRetrievalDocumentDto[],
  batchDocuments: number,
) {
  if (documents.length === 0) return [[]] as EvidenceRetrievalDocumentDto[][];
  const baseBytes = utf8Bytes({
    schema_version: manifest.index_version,
    project_id: projectId,
    manifest,
    documents: [],
  });
  const chunks: EvidenceRetrievalDocumentDto[][] = [];
  let current: EvidenceRetrievalDocumentDto[] = [];
  let currentBytes = baseBytes;
  for (const document of documents) {
    const rowBytes = utf8Bytes(document);
    const nextBytes = currentBytes + rowBytes + (current.length ? 1 : 0);
    if (current.length && (
      current.length >= Math.min(RETRIEVAL_MAX_INDEX_DOCUMENTS, batchDocuments)
      || nextBytes > MAX_INDEX_REQUEST_BYTES
    )) {
      chunks.push(current);
      current = [];
      currentBytes = baseBytes;
    }
    if (currentBytes + rowBytes > MAX_INDEX_REQUEST_BYTES) {
      throw new ApiError(
        422,
        "RETRIEVAL_PROJECTION_ROW_TOO_LARGE",
        "One retrieval projection document exceeds the index transport boundary",
        { retryable: false },
      );
    }
    current.push(document);
    currentBytes += rowBytes + (current.length > 1 ? 1 : 0);
  }
  if (current.length) chunks.push(current);
  return chunks;
}

export type SnapshotIndexProgress = {
  /** Number of leading document chunks already accepted by the service. */
  nextChunk: number;
  totalChunks: number;
  indexedDocuments: number;
  done: boolean;
  status: "indexed" | "degraded" | "partial";
  warnings: string[];
};

/**
 * Background indexing of one immutable snapshot, resumable by chunk. The
 * service persists every accepted document idempotently, so replaying a chunk
 * after a crash is harmless. Work stops between chunks at `deadline` and
 * reports where to continue.
 */
export async function indexEvidenceRetrievalSnapshot(
  DB: D1Database,
  client: RetrievalClient,
  manifest: EvidenceRetrievalManifest,
  state: ManifestState | undefined,
  {
    startChunk = 0,
    deadline = Number.POSITIVE_INFINITY,
    batchDocuments = RETRIEVAL_INDEX_BATCH_DOCUMENTS,
  }: { startChunk?: number; deadline?: number; batchDocuments?: number } = {},
): Promise<SnapshotIndexProgress> {
  const warnings: string[] = [];
  if (state?.status === "stale") {
    // A different immutable manifest exists under this ID; replace it.
    await client.deleteSnapshots(PROJECT_ID, [manifest.snapshotId]);
  }
  const [bundle] = await loadEvidenceRetrievalIndexBundles(DB, PROJECT_ID, [manifest.snapshotId]);
  if (!bundle || bundle.manifest.snapshotId !== manifest.snapshotId) {
    throw new ApiError(404, "RETRIEVAL_MANIFEST_NOT_FOUND", "The retrieval snapshot bundle is missing", { retryable: false });
  }
  const indexRequest = toEvidenceRetrievalIndexRequestDto(PROJECT_ID, bundle);
  const chunks = indexDocumentChunks(PROJECT_ID, indexRequest.manifest, indexRequest.documents, batchDocuments);
  indexRequest.documents.length = 0;
  bundle.documents.length = 0;
  let indexedDocuments = 0;
  let finalIndexedCount = 0;
  let finalStatus: string | undefined;
  let nextChunk = Math.max(0, Math.min(startChunk, chunks.length - 1));
  while (nextChunk < chunks.length) {
    if (nextChunk > startChunk && Date.now() >= deadline) {
      return { nextChunk, totalChunks: chunks.length, indexedDocuments, done: false, status: "partial", warnings };
    }
    const result = await client.indexSnapshot(PROJECT_ID, indexRequest.manifest, chunks[nextChunk]);
    indexedDocuments += result.indexed_documents;
    finalIndexedCount = result.manifests[0]?.indexed_document_count ?? 0;
    finalStatus = result.manifests[0]?.status;
    for (const warning of result.warnings) appendPublicWarning(warnings, warning);
    nextChunk += 1;
  }
  if (finalIndexedCount !== manifest.documentCount) {
    throw new ApiError(
      502,
      "RETRIEVAL_SYNC_INCOMPLETE",
      "Retrieval service did not complete the requested immutable snapshot",
      { retryable: true },
    );
  }
  return {
    nextChunk,
    totalChunks: chunks.length,
    indexedDocuments,
    done: true,
    status: finalStatus === "degraded" ? "degraded" : "indexed",
    warnings,
  };
}
