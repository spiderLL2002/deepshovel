import { CompilerClient } from "@/lib/server/compiler-client.ts";
import {
  RETRIEVAL_MAX_MANIFESTS,
  RetrievalClient,
} from "@/lib/server/retrieval-client.ts";
import {
  ApiError,
  getBindings,
  getCompilerConfiguration,
  PROJECT_ID,
} from "@/lib/server/evidence-store";
import { documentExtractionObjectKey } from "@/lib/server/document-extraction-store.ts";

const COMPILER_JOB_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

type VersionRow = { id: string; sha256: string; objectKey: string; sharedReference: number };
type JobRow = { id: string; upstreamJobId: string; status: string; errorJson: string | null; resultStaged?: number };
type SnapshotRow = { id: string };

async function forEachConcurrent<T>(
  items: readonly T[],
  maximumConcurrency: number,
  operation: (item: T) => Promise<void>,
) {
  let next = 0;
  const workers = Array.from(
    { length: Math.min(maximumConcurrency, items.length) },
    async () => {
      while (next < items.length) {
        const item = items[next];
        next += 1;
        await operation(item);
      }
    },
  );
  const settled = await Promise.allSettled(workers);
  const failure = settled.find((result): result is PromiseRejectedResult => result.status === "rejected");
  if (failure) throw failure.reason;
}

async function removeCompilerJobs(jobs: JobRow[]) {
  const upstreamJobs = [...new Map(jobs
    .filter((job) => COMPILER_JOB_ID_RE.test(job.upstreamJobId))
    .map((job) => [job.upstreamJobId, job])).values()];
  if (!upstreamJobs.length) return;
  const client = new CompilerClient(await getCompilerConfiguration());
  await forEachConcurrent(upstreamJobs, 4, async (job) => {
    try {
      await client.remove(job.upstreamJobId);
      const cancellation = job.errorJson == null
        ? null
        : (() => { try { return JSON.parse(job.errorJson) as { code?: unknown }; } catch { return null; } })();
      if (job.status === "queued" || job.status === "running" || cancellation?.code === "COMPILE_CANCELLED") {
        await client.remove(job.upstreamJobId);
      }
    } catch (error) {
      if (error instanceof ApiError && error.code === "UPSTREAM_JOB_NOT_FOUND") return;
      throw error;
    }
  });
}

async function removeCompilerDocuments(versions: VersionRow[]) {
  if (!versions.length) return;
  const client = new CompilerClient(await getCompilerConfiguration());
  await forEachConcurrent(versions, 4, async (version) => {
    await client.removeDocumentExtraction(version.id);
  });
}

async function removeRetrievalSnapshots(snapshots: SnapshotRow[]) {
  if (!snapshots.length) return;
  const configuration = await getCompilerConfiguration();
  if (!configuration.sharedSecret) {
    throw new ApiError(
      503,
      "RETRIEVAL_NOT_CONFIGURED",
      "Artifact deletion requires the shared retrieval-service credential",
    );
  }
  const client = new RetrievalClient({
    ...configuration,
    sharedSecret: configuration.sharedSecret,
  });
  for (let index = 0; index < snapshots.length; index += RETRIEVAL_MAX_MANIFESTS) {
    await client.deleteSnapshots(
      PROJECT_ID,
      snapshots.slice(index, index + RETRIEVAL_MAX_MANIFESTS).map((snapshot) => snapshot.id),
    );
  }
}

/** Purge one logical Artifact and every derived record. External projections
 * and bytes go first; the authorization row prevents a new Compile from
 * racing the final D1 batch. */
export async function deleteArtifactCompletely(artifactId: string, actor: string) {
  const { DB, BUCKET } = await getBindings();
  const artifact = await DB.prepare(
    "SELECT id FROM artifacts WHERE project_id = ? AND id = ? LIMIT 1",
  ).bind(PROJECT_ID, artifactId).first<{ id: string }>();
  if (!artifact) throw new ApiError(404, "ARTIFACT_NOT_FOUND", "Artifact was not found");

  try {
    await DB.batch([
      DB.prepare(`UPDATE compile_jobs SET status = 'failed', stage = 'failed', progress = 100,
      error_json = ?, updated_at = ? WHERE project_id = ? AND status IN ('queued', 'running')
      AND artifact_version_id IN (
        SELECT id FROM artifact_versions WHERE artifact_id = ?
      )`).bind(
      JSON.stringify({ code: "COMPILE_CANCELLED", message: "Compile task was stopped because the document was deleted", retryable: false }),
      new Date().toISOString(),
      PROJECT_ID,
      artifactId,
      ),
      DB.prepare(`INSERT OR IGNORE INTO artifact_deletion_authorizations
        (artifact_id, requested_by) VALUES (?, ?)`).bind(artifactId, actor),
    ]);
  } catch (error) {
    const active = await DB.prepare(`SELECT 1 AS active FROM compile_jobs j
      JOIN artifact_versions v ON v.id = j.artifact_version_id
      WHERE v.artifact_id = ? AND j.status IN ('queued', 'running') LIMIT 1`)
      .bind(artifactId).first<{ active: number }>();
    if (active) throw new ApiError(409, "ARTIFACT_HAS_ACTIVE_COMPILE", "Wait for active Compile jobs to finish before deleting this artifact");
    throw error;
  }

  // The authorization is a database-enforced write barrier. Read the purge
  // set only after acquiring it so a version committed by a concurrent upload
  // immediately before the barrier cannot escape R2 cleanup.
  const [versionsResult, jobsResult, snapshotsResult] = await Promise.all([
    DB.prepare(`SELECT v.id, v.sha256, v.object_key AS objectKey,
      EXISTS(SELECT 1 FROM artifact_versions other
        WHERE other.object_key = v.object_key AND other.artifact_id <> v.artifact_id) AS sharedReference
      FROM artifact_versions v WHERE v.artifact_id = ? ORDER BY v.version_number`)
      .bind(artifactId).all<VersionRow>(),
    DB.prepare(`SELECT j.id, j.upstream_job_id AS upstreamJobId, j.status,
      j.error_json AS errorJson, j.result_staged AS resultStaged
      FROM compile_jobs j JOIN artifact_versions v ON v.id = j.artifact_version_id
      WHERE v.artifact_id = ? ORDER BY j.created_at`).bind(artifactId).all<JobRow>(),
    DB.prepare(`SELECT s.id FROM evidence_graph_snapshots s
      JOIN artifact_versions v ON v.id = s.root_artifact_version_id
      WHERE v.artifact_id = ?`).bind(artifactId).all<SnapshotRow>(),
  ]);
  const versions = versionsResult.results;
  const jobs = jobsResult.results;
  const snapshots = snapshotsResult.results;
  await removeCompilerJobs(jobs);
  await removeCompilerDocuments(versions);
  // The rebuildable FTS/vector projection is external to D1. Purge it before
  // deleting source bytes or immutable D1 identities; a partial multi-page
  // failure is safe to retry because the service deletion is idempotent.
  await removeRetrievalSnapshots(snapshots);

  const deletableBucket = BUCKET as unknown as { delete(key: string): Promise<void> };
  await forEachConcurrent(
    versions.filter((version) => !version.sharedReference),
    8,
    async (version) => {
      const extractionKey = documentExtractionObjectKey(version.sha256);
      if (await BUCKET.head(extractionKey)) await deletableBucket.delete(extractionKey);
      await deletableBucket.delete(version.objectKey);
    },
  );
  // R2 copies of completed Compile results kept for import recovery.
  await forEachConcurrent(jobs.filter((job) => Number(job.resultStaged) === 1), 8, async (job) => {
    await BUCKET.delete(["result", "citation-source", "document-extraction"]
      .map((name) => `compile-results/${job.id}/${name}.json`));
  });

  const snapshotForArtifact = `SELECT s.id FROM evidence_graph_snapshots s
    JOIN artifact_versions v ON v.id = s.root_artifact_version_id WHERE v.artifact_id = ?`;
  const verificationForArtifact = `SELECT r.id FROM verification_runs r
    JOIN evidence_graph_snapshots s ON s.id = r.snapshot_id
    JOIN artifact_versions v ON v.id = s.root_artifact_version_id WHERE v.artifact_id = ?`;
  const jobsForArtifact = `SELECT j.id FROM compile_jobs j
    JOIN artifact_versions v ON v.id = j.artifact_version_id WHERE v.artifact_id = ?`;
  const versionsForArtifact = "SELECT id FROM artifact_versions WHERE artifact_id = ?";
  const citationRunsForArtifact = `SELECT run.id FROM citation_reception_runs run
    WHERE run.citing_snapshot_id IN (${snapshotForArtifact})
       OR run.cited_snapshot_id IN (${snapshotForArtifact})`;
  await DB.batch([
    DB.prepare(`DELETE FROM audit_events WHERE project_id = ? AND (
      entity_id = ? OR entity_id IN (${versionsForArtifact}) OR
      entity_id IN (${jobsForArtifact}) OR entity_id IN (${snapshotForArtifact}) OR
      entity_id IN (${verificationForArtifact}))`).bind(
      PROJECT_ID, artifactId, artifactId, artifactId, artifactId, artifactId,
    ),
    DB.prepare(`DELETE FROM graph_review_events WHERE snapshot_id IN (${snapshotForArtifact})`).bind(artifactId),
    DB.prepare(`DELETE FROM corpus_claim_graph_edges
      WHERE source_snapshot_id IN (${snapshotForArtifact})
         OR target_snapshot_id IN (${snapshotForArtifact})`).bind(artifactId, artifactId),
    DB.prepare(`DELETE FROM corpus_claim_graph_completions
      WHERE snapshot_id IN (${snapshotForArtifact})`).bind(artifactId),
    DB.prepare(`DELETE FROM work_items WHERE project_id = ?
      AND subject_id IN (${snapshotForArtifact})`).bind(PROJECT_ID, artifactId),
    DB.prepare(`DELETE FROM openalex_paper_topics
      WHERE snapshot_id IN (${snapshotForArtifact})`).bind(artifactId),
    DB.prepare(`DELETE FROM openalex_paper_classifications
      WHERE snapshot_id IN (${snapshotForArtifact})`).bind(artifactId),
    // Reception is derived from both A and B. Authorizing deletion of either
    // endpoint permits the frozen run and its children to be purged, while A's
    // bibliography/mention facts remain when only B is deleted.
    DB.prepare(`DELETE FROM citation_assessments WHERE assertion_id IN (
      SELECT assertion.id FROM citation_assertions assertion
      WHERE assertion.run_id IN (${citationRunsForArtifact})
    )`).bind(artifactId, artifactId),
    DB.prepare(`DELETE FROM citation_assertions
      WHERE run_id IN (${citationRunsForArtifact})`).bind(artifactId, artifactId),
    DB.prepare(`DELETE FROM citation_reception_runs
      WHERE id IN (${citationRunsForArtifact})`).bind(artifactId, artifactId),
    DB.prepare(`DELETE FROM citation_mentions
      WHERE citing_snapshot_id IN (${snapshotForArtifact})`).bind(artifactId),
    DB.prepare(`DELETE FROM bibliographic_references
      WHERE citing_snapshot_id IN (${snapshotForArtifact})`).bind(artifactId),
    DB.prepare(`DELETE FROM citation_source_manifests
      WHERE snapshot_id IN (${snapshotForArtifact})`).bind(artifactId),
    DB.prepare(`DELETE FROM evidence_retrieval_assessments WHERE snapshot_id IN (${snapshotForArtifact})`).bind(artifactId),
    DB.prepare(`DELETE FROM evidence_retrieval_documents WHERE snapshot_id IN (${snapshotForArtifact})`).bind(artifactId),
    DB.prepare(`DELETE FROM evidence_retrieval_manifests WHERE snapshot_id IN (${snapshotForArtifact})`).bind(artifactId),
    DB.prepare(`DELETE FROM verification_runs WHERE snapshot_id IN (${snapshotForArtifact})`).bind(artifactId),
    DB.prepare(`DELETE FROM compile_jobs WHERE artifact_version_id IN (${versionsForArtifact})`).bind(artifactId),
    DB.prepare(`DELETE FROM evidence_graph_snapshots WHERE id IN (${snapshotForArtifact})`).bind(artifactId),
    DB.prepare("DELETE FROM artifact_versions WHERE artifact_id = ?").bind(artifactId),
    DB.prepare("DELETE FROM artifacts WHERE project_id = ? AND id = ?").bind(PROJECT_ID, artifactId),
  ]);

  return {
    ok: true as const,
    artifactId,
    deleted: { versions: versions.length, compileJobs: jobs.length, snapshots: snapshots.length },
  };
}
