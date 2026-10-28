/**
 * Shared Claim-relation batch helpers for the background work queue and the
 * knowledge-graph routes. The Compiler keeps a batch only in memory; durable
 * progress is the per-Claim completion rows written here, so a lost batch is
 * resumed by starting a new one for the Claims that are still unprocessed.
 */
import {
  claimRefKey,
  EVIDENCE_KNOWLEDGE_GRAPH_VERSION,
  projectClaimRelation,
  type ClaimRef,
  type ClaimRelationEdge,
} from "@/lib/evidence-knowledge-graph";
import {
  loadEvidenceRetrievalDocumentsByRefs,
  type RetrievalSearchResponse,
} from "@/lib/evidence-retrieval";
import type { RetrievalClient } from "@/lib/server/retrieval-client";
import { ApiError, PROJECT_ID, sha256Hex } from "@/lib/server/evidence-store";

type CompletionRow = {
  snapshotId: string;
  claimId: string;
  candidateCount: number;
  edgeCount: number;
  classifierModel: string;
  completedAt: string;
};

export const MAX_BATCH_CLAIMS = 4_096;

export type PendingClaimRow = { snapshotId: string; claimId: string };

/** Unprocessed Claims of the given snapshots, oldest first. */
export async function unprocessedClaims(DB: D1Database, snapshotIds: readonly string[], limit = MAX_BATCH_CLAIMS) {
  if (!snapshotIds.length) return [];
  const rows = await DB.prepare(`WITH target(snapshot_id) AS (SELECT value FROM json_each(?))
    SELECT document.snapshot_id AS snapshotId, document.claim_id AS claimId
    FROM evidence_retrieval_documents document
    JOIN target ON target.snapshot_id = document.snapshot_id
    LEFT JOIN corpus_claim_graph_completions completion
      ON completion.snapshot_id = document.snapshot_id
     AND completion.claim_id = document.claim_id
     AND completion.processor_version = ?
    WHERE document.project_id = ? AND completion.snapshot_id IS NULL
    ORDER BY document.created_at, document.snapshot_id, document.claim_id
    LIMIT ?`)
    .bind(JSON.stringify(snapshotIds), EVIDENCE_KNOWLEDGE_GRAPH_VERSION, PROJECT_ID, limit)
    .all<PendingClaimRow>();
  return rows.results;
}

/** A deterministic client request ID for one batch of Claims over one corpus. */
export async function relationBatchRequestId(refs: readonly PendingClaimRow[], corpusSnapshotIds: readonly string[]) {
  const digest = await sha256Hex(JSON.stringify([
    EVIDENCE_KNOWLEDGE_GRAPH_VERSION,
    "unprocessed",
    [...corpusSnapshotIds].sort(),
    ...refs.map((ref) => [ref.snapshotId, ref.claimId]),
  ]));
  return `claim-relation-batch:sha256:${digest}`;
}

/** The Compiler derives batch IDs this way; computing it allows GET-before-POST. */
export async function relationBatchId(clientRequestId: string) {
  return `relation-batch:sha256:${await sha256Hex(`${PROJECT_ID}\u0000${clientRequestId}`)}`;
}

/** Persist every finished item of one progress page as a completion row. */
export async function persistBatchItems(
  DB: D1Database,
  progress: Awaited<ReturnType<RetrievalClient["getClaimRelationBatch"]>>,
) {
  let persisted = 0;
  let edges = 0;
  let skipped = 0;
  const succeededRefs = progress.items.flatMap((item) => item.status === "succeeded" && item.response
    ? [{ snapshotId: item.query_claim.snapshot_id, claimId: item.query_claim.claim_id }]
    : []);
  const documents = await loadEvidenceRetrievalDocumentsByRefs(DB, PROJECT_ID, succeededRefs);
  const paperFamilyByRef = new Map(documents.map((document) => [
    `${document.snapshotId}\u0000${document.claimId}`,
    document.paperFamilyId,
  ]));
  for (const item of progress.items) {
    if (item.status !== "succeeded" || !item.response) continue;
    const ref: ClaimRef = {
      snapshotId: item.query_claim.snapshot_id,
      claimId: item.query_claim.claim_id,
    };
    const paperFamilyId = paperFamilyByRef.get(`${ref.snapshotId}\u0000${ref.claimId}`);
    // A Claim deleted or superseded meanwhile is skipped, not a whole-page failure.
    if (!paperFamilyId) {
      skipped += 1;
      continue;
    }
    const relation = item.response.channels.relation;
    if (relation.status !== "ok" && !(relation.status === "skipped" && relation.returned === 0)) {
      // Never record a failed classification as a successful completion; the
      // Claim stays unprocessed and the next batch retries it.
      skipped += 1;
      continue;
    }
    const direct = relation.status === "ok"
      ? claimRelationCandidates(ref, paperFamilyId, item.response)
      : [];
    const classifier = relation.status === "skipped" ? "not-invoked:no-candidates" : relation.provider;
    const completion = await commitCompletion(DB, ref, relation.returned, direct, classifier, progress.reprocess);
    persisted += 1;
    edges += Number(completion.edgeCount);
  }
  return { persisted, edges, skipped };
}

export function assertBatchClaims(refs: readonly PendingClaimRow[]) {
  if (refs.length > MAX_BATCH_CLAIMS) {
    throw new ApiError(500, "KNOWLEDGE_GRAPH_BATCH_TOO_LARGE", "Claim relation batches are bounded");
  }
}

async function loadCompletion(DB: D1Database, ref: ClaimRef) {
  return DB.prepare(`SELECT snapshot_id AS snapshotId, claim_id AS claimId,
      candidate_count AS candidateCount, edge_count AS edgeCount,
      classifier_model AS classifierModel,
      completed_at AS completedAt
    FROM corpus_claim_graph_completions
    WHERE project_id = ? AND snapshot_id = ? AND claim_id = ? AND processor_version = ?
    LIMIT 1`)
    .bind(PROJECT_ID, ref.snapshotId, ref.claimId, EVIDENCE_KNOWLEDGE_GRAPH_VERSION)
    .first<CompletionRow>();
}


export function claimRelationCandidates(
  query: ClaimRef,
  queryPaperFamilyId: string,
  result: RetrievalSearchResponse,
) {
  const proposed = new Map<string, ClaimRelationEdge>();
  for (const cluster of result.clusters) {
    for (const member of cluster.member_refs) {
      if (member.paper_family_id === queryPaperFamilyId) continue;
      const candidate = { snapshotId: member.snapshot_id, claimId: member.claim_id };
      const edge = projectClaimRelation(
        query,
        candidate,
        member.relation,
        member.relation_direction,
        member.relation_confidence,
      );
      if (edge === null) continue;
      const key = [claimRefKey(edge.source), claimRefKey(edge.target)].sort().join("\u0001");
      const current = proposed.get(key);
      if (!current || (edge.confidence ?? -1) > (current.confidence ?? -1)) proposed.set(key, edge);
    }
  }
  return [...proposed.values()];
}


async function edgeIdentity(edge: ClaimRelationEdge) {
  const [left, right] = [edge.source, edge.target].sort((first, second) =>
    claimRefKey(first).localeCompare(claimRefKey(second), "en"));
  const digest = await sha256Hex([
    EVIDENCE_KNOWLEDGE_GRAPH_VERSION,
    left.snapshotId,
    left.claimId,
    right.snapshotId,
    right.claimId,
  ].join("\u0000"));
  return {
    id: `claim-edge:sha256:${digest}`,
    pairKey: `claim-pair:sha256:${digest}`,
  };
}


async function persistCompletion(
  DB: D1Database,
  ref: ClaimRef,
  candidateCount: number,
  proposed: ClaimRelationEdge[],
  classifierModel: string,
  replaceExisting: boolean,
) {
  const createdAt = new Date().toISOString();
  const edgeRows = await Promise.all(proposed.map(async (edge) => ({ edge, ...await edgeIdentity(edge) })));
  const proposedEdgeIds = JSON.stringify(edgeRows.map(({ id }) => id));
  await DB.batch([
    ...(replaceExisting ? [
      DB.prepare(`DELETE FROM corpus_claim_graph_edges
        WHERE project_id = ? AND processor_version = ?
          AND ((source_snapshot_id = ? AND source_claim_id = ?)
            OR (target_snapshot_id = ? AND target_claim_id = ?))`).bind(
        PROJECT_ID,
        EVIDENCE_KNOWLEDGE_GRAPH_VERSION,
        ref.snapshotId,
        ref.claimId,
        ref.snapshotId,
        ref.claimId,
      ),
      DB.prepare(`DELETE FROM corpus_claim_graph_completions
        WHERE project_id = ? AND snapshot_id = ? AND claim_id = ? AND processor_version = ?`).bind(
        PROJECT_ID,
        ref.snapshotId,
        ref.claimId,
        EVIDENCE_KNOWLEDGE_GRAPH_VERSION,
      ),
    ] : []),
    ...edgeRows.map(({ edge, id, pairKey }) => DB.prepare(`INSERT OR IGNORE INTO corpus_claim_graph_edges
      (id, pair_key, project_id, source_snapshot_id, source_claim_id, target_snapshot_id,
       target_claim_id, relation, model_relation, confidence, processor_version, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`).bind(
      id,
      pairKey,
      PROJECT_ID,
      edge.source.snapshotId,
      edge.source.claimId,
      edge.target.snapshotId,
      edge.target.claimId,
      edge.relation,
      edge.modelRelation,
      edge.confidence,
      EVIDENCE_KNOWLEDGE_GRAPH_VERSION,
      createdAt,
    )),
    DB.prepare(`INSERT OR IGNORE INTO corpus_claim_graph_completions
      (project_id, snapshot_id, claim_id, processor_version, candidate_count,
       edge_count, classifier_model, completed_at)
      VALUES (?, ?, ?, ?, ?,
        (SELECT COUNT(*) FROM corpus_claim_graph_edges
         WHERE project_id = ? AND processor_version = ?
           AND id IN (SELECT value FROM json_each(?))), ?, ?)`).bind(
      PROJECT_ID,
      ref.snapshotId,
      ref.claimId,
      EVIDENCE_KNOWLEDGE_GRAPH_VERSION,
      candidateCount,
      PROJECT_ID,
      EVIDENCE_KNOWLEDGE_GRAPH_VERSION,
      proposedEdgeIds,
      classifierModel,
      createdAt,
    ),
  ]);
  const completion = await loadCompletion(DB, ref);
  if (!completion) throw new ApiError(500, "KNOWLEDGE_GRAPH_COMPLETION_MISSING", "Claim processing did not commit");
  return completion;
}


export async function commitCompletion(
  DB: D1Database,
  ref: ClaimRef,
  candidateCount: number,
  proposed: ClaimRelationEdge[],
  classifierModel: string,
  reprocess: boolean,
) {
  const existing = await loadCompletion(DB, ref);
  if (existing && !reprocess) return existing;
  return persistCompletion(DB, ref, candidateCount, proposed, classifierModel, reprocess);
}
