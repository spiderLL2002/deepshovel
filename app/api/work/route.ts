import { ApiError, getBindings, PROJECT_ID, safeError } from "@/lib/server/evidence-store";
import { listRecentWork, WORK_KINDS, type WorkItem, type WorkKind } from "@/lib/server/work-queue";

type LabelRow = { snapshotId: string; fileName: string | null; artifactVersionId: string; artifactId: string };

function publicItem(item: WorkItem, labels: Map<string, LabelRow>) {
  const label = labels.get(item.subjectId);
  const total = Number.isSafeInteger(item.progress.total) ? Number(item.progress.total) : null;
  const persisted = Number.isSafeInteger(item.progress.persisted) ? Number(item.progress.persisted) : null;
  return {
    id: item.id,
    kind: item.kind,
    subjectId: item.subjectId,
    state: item.state,
    attempts: item.attempts,
    maxAttempts: item.maxAttempts,
    nextAttemptAt: item.state === "pending" ? item.nextAttemptAt : null,
    progress: { total, persisted },
    error: item.lastError ? { code: item.lastError.code, message: item.lastError.message } : null,
    fileName: label?.fileName ?? null,
    artifactId: label?.artifactId ?? null,
    artifactVersionId: label?.artifactVersionId ?? null,
    updatedAt: item.updatedAt,
    completedAt: item.completedAt,
  };
}

/** Read-only view of background work for the task page. Never starts work. */
export async function GET(request: Request) {
  try {
    const parameters = new URL(request.url).searchParams;
    const unknown = [...parameters.keys()].filter((key) => key !== "kind");
    if (unknown.length) throw new ApiError(400, "INVALID_WORK_QUERY", `Unknown query parameters: ${unknown.join(", ")}`);
    const kinds = parameters.getAll("kind");
    if (kinds.some((kind) => !WORK_KINDS.includes(kind as WorkKind))) {
      throw new ApiError(400, "INVALID_WORK_QUERY", "kind is invalid");
    }
    const { DB } = await getBindings();
    const view = await listRecentWork(DB, PROJECT_ID, kinds.length ? { kinds: kinds as WorkKind[] } : {});
    const subjects = [...new Set([...view.open, ...view.finished].map((item) => item.subjectId))];
    const labels = new Map<string, LabelRow>();
    if (subjects.length) {
      const rows = await DB.prepare(`SELECT s.id AS snapshotId, v.original_file_name AS fileName,
          v.id AS artifactVersionId, v.artifact_id AS artifactId
        FROM evidence_graph_snapshots s JOIN artifact_versions v ON v.id = s.root_artifact_version_id
        WHERE s.project_id = ? AND s.id IN (SELECT value FROM json_each(?))`)
        .bind(PROJECT_ID, JSON.stringify(subjects)).all<LabelRow>();
      for (const row of rows.results) labels.set(row.snapshotId, row);
    }
    return Response.json({
      ok: true,
      open: view.open.map((item) => publicItem(item, labels)),
      finished: view.finished.map((item) => publicItem(item, labels)),
      counts: view.counts,
    }, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    return safeError(error);
  }
}
