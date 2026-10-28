import {
  compilerProgress,
  type CompilerJob,
  type CompilerStage,
} from "@/lib/server/compiler-client.ts";
import {
  ApiError,
  getBindings,
  PROJECT_ID,
} from "@/lib/server/evidence-store";

const JOB_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SUBMISSION_RESERVATION_MS = 2 * 60 * 1000;
const IMPORT_LEASE_MS = 5 * 60 * 1000;
const TERMINAL_EXPIRY_LIMIT_MS = 25 * 60 * 60 * 1000;
export const PENDING_UPSTREAM_PREFIX = "pending:";
// The lease clock is the database host's, the same Mac as this Worker.
const IMPORT_LEASE_SQL = `import_lease_until = strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '+${IMPORT_LEASE_MS / 1000} seconds')`;
/** Import attempts before a completed Compile result is reported as failed. */
export const MAX_IMPORT_ATTEMPTS = 6;

/** 1, 2, 4, 8, 16 minutes between import attempts. */
export function importRetryDelayMs(attempts: number) {
  return Math.min(60 * 60 * 1000, 60 * 1000 * 2 ** Math.max(0, attempts - 1));
}

export type StoredStatus = "queued" | "running" | "completed" | "failed";
export type StoredStage = "queued" | "parsing" | "extracting" | "validating" | "importing" | "completed" | "failed";
export type JobError = { code: string; message: string; retryable: boolean };

export type CompileJobRow = {
  id: string;
  projectId: string;
  artifactVersionId: string;
  upstreamJobId: string;
  status: StoredStatus;
  stage: StoredStage;
  progress: number;
  timingsJson: string;
  errorJson: string | null;
  snapshotId: string | null;
  clientRequestId: string;
  requestHash: string;
  createdBy: string;
  createdAt: string;
  updatedAt: string;
  expiresAt: string;
  fileName?: string | null;
  importAttempts?: number;
  importLeaseUntil?: string | null;
  nextImportAt?: string | null;
  importErrorJson?: string | null;
  resultStaged?: number;
};

const SELECT_JOB = `SELECT id, project_id AS projectId,
  artifact_version_id AS artifactVersionId, upstream_job_id AS upstreamJobId,
  status, stage, progress, timings_json AS timingsJson, error_json AS errorJson,
  snapshot_id AS snapshotId, client_request_id AS clientRequestId,
  request_hash AS requestHash, created_by AS createdBy, created_at AS createdAt,
  updated_at AS updatedAt, expires_at AS expiresAt,
  import_attempts AS importAttempts, import_lease_until AS importLeaseUntil,
  next_import_at AS nextImportAt, import_error_json AS importErrorJson,
  result_staged AS resultStaged FROM compile_jobs`;

function parseJson<T>(value: string | null, fallback: T): T {
  if (!value) return fallback;
  try { return JSON.parse(value) as T; } catch { return fallback; }
}

export function compileJobReceipt(row: CompileJobRow) {
  const importAttempts = Number(row.importAttempts ?? 0);
  return {
    id: row.id,
    artifactVersionId: row.artifactVersionId,
    status: row.status,
    stage: row.stage,
    progress: row.progress,
    timings: parseJson<Record<string, number | null>>(row.timingsJson, {}),
    snapshotId: row.snapshotId,
    error: parseJson<JobError | null>(row.errorJson, null),
    createdAt: row.createdAt,
    updatedAt: row.updatedAt,
    expiresAt: row.expiresAt,
    ...(row.fileName ? { fileName: row.fileName } : {}),
    // Present only after a failed import attempt; the next one is automatic.
    ...(importAttempts > 0 ? {
      importRetry: {
        attempts: importAttempts,
        maxAttempts: MAX_IMPORT_ATTEMPTS,
        nextAttemptAt: row.nextImportAt ?? null,
        lastError: parseJson<JobError | null>(row.importErrorJson ?? null, null),
      },
    } : {}),
  };
}

function compilerState(job: CompilerJob) {
  if (job.status === "failed") {
    return { status: "failed" as const, stage: "failed" as const, progress: 100 };
  }
  if (job.status === "completed") {
    return { status: "running" as const, stage: "importing" as const, progress: 97 };
  }
  return {
    status: job.status === "queued" ? "queued" as const : "running" as const,
    stage: job.status,
    progress: compilerProgress(job.status),
  };
}

function timingsJson(job: CompilerJob) {
  return JSON.stringify(job.stageTimingsMs);
}

function errorJson(error: JobError | null) {
  return error ? JSON.stringify(error) : null;
}

function nextTimestamp(previous: string) {
  return new Date(Math.max(Date.now(), Date.parse(previous) + 1)).toISOString();
}

function changed(result: D1Result) {
  return Number(result.meta.changes ?? 0) > 0;
}

function auditId(event: "submitted" | "failed" | "completed", jobId: string) {
  return `audit:compile:${event}:${jobId}`;
}

async function appendAudit(
  DB: D1Database,
  row: CompileJobRow,
  event: "submitted" | "failed" | "completed",
  payload: unknown,
) {
  await DB.prepare(`INSERT OR IGNORE INTO audit_events
    (id, project_id, event_type, entity_type, entity_id, actor, payload_json)
    SELECT ?, ?, ?, 'compile_job', ?, ?, ? FROM compile_jobs j
    WHERE j.project_id = ? AND j.id = ? LIMIT 1`).bind(
    auditId(event, row.id),
    PROJECT_ID,
    `compile.${event}`,
    row.id,
    row.createdBy,
    JSON.stringify(payload),
    PROJECT_ID,
    row.id,
  ).run();
}

export async function findIdempotentJob(actor: string, clientRequestId: string) {
  const { DB } = await getBindings();
  return DB.prepare(`${SELECT_JOB} WHERE project_id = ? AND created_by = ? AND client_request_id = ? LIMIT 1`)
    .bind(PROJECT_ID, actor, clientRequestId).first<CompileJobRow>();
}

export async function findCompileJob(actor: string, id: string) {
  if (!JOB_ID_RE.test(id)) throw new ApiError(400, "INVALID_COMPILE_JOB_ID", "job_id must be a UUID");
  const { DB } = await getBindings();
  const row = await DB.prepare(`${SELECT_JOB} WHERE project_id = ? AND created_by = ? AND id = ? LIMIT 1`)
    .bind(PROJECT_ID, actor, id).first<CompileJobRow>();
  if (!row) throw new ApiError(404, "COMPILE_JOB_NOT_FOUND", "Compile job was not found");
  return row;
}

export async function findCompileJobs(actor: string, ids: string[]) {
  for (const id of ids) {
    if (!JOB_ID_RE.test(id)) throw new ApiError(400, "INVALID_COMPILE_JOB_ID", "job_id must be a UUID");
  }
  if (ids.length === 0) return [];
  const { DB } = await getBindings();
  const rows = await DB.prepare(`${SELECT_JOB} WHERE project_id = ? AND created_by = ?
    AND id IN (SELECT value FROM json_each(?))`)
    .bind(PROJECT_ID, actor, JSON.stringify(ids)).all<CompileJobRow>();
  const byId = new Map(rows.results.map((row) => [row.id, row]));
  return ids.map((id) => {
    const row = byId.get(id);
    if (!row) throw new ApiError(404, "COMPILE_JOB_NOT_FOUND", "Compile job was not found");
    return row;
  });
}

async function findUpstreamOwner(DB: D1Database, upstreamJobId: string) {
  return DB.prepare(`${SELECT_JOB} WHERE project_id = ? AND upstream_job_id = ? LIMIT 1`)
    .bind(PROJECT_ID, upstreamJobId).first<CompileJobRow>();
}

export async function reserveCompileJob(
  actor: string,
  clientRequestId: string,
  requestHash: string,
  artifactVersionId: string,
) {
  const now = new Date().toISOString();
  const id = crypto.randomUUID();
  const row: CompileJobRow = {
    id,
    projectId: PROJECT_ID,
    artifactVersionId,
    upstreamJobId: `${PENDING_UPSTREAM_PREFIX}${id}`,
    status: "queued",
    stage: "queued",
    progress: 0,
    timingsJson: "{}",
    errorJson: null,
    snapshotId: null,
    clientRequestId,
    requestHash,
    createdBy: actor,
    createdAt: now,
    updatedAt: now,
    expiresAt: new Date(Date.now() + SUBMISSION_RESERVATION_MS).toISOString(),
  };
  const { DB } = await getBindings();
  try {
    await DB.prepare(`INSERT INTO compile_jobs
      (id, project_id, artifact_version_id, upstream_job_id, status, stage,
       progress, timings_json, error_json, snapshot_id, client_request_id,
       request_hash, created_by, created_at, updated_at, expires_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?)`).bind(
      row.id,
      row.projectId,
      row.artifactVersionId,
      row.upstreamJobId,
      row.status,
      row.stage,
      row.progress,
      row.timingsJson,
      row.clientRequestId,
      row.requestHash,
      row.createdBy,
      row.createdAt,
      row.updatedAt,
      row.expiresAt,
    ).run();
    return { row, duplicate: false as const };
  } catch (error) {
    const winner = await findIdempotentJob(actor, clientRequestId);
    if (!winner) {
      const deleting = await DB.prepare(`SELECT 1 AS deleting FROM artifact_versions v
        JOIN artifact_deletion_authorizations d ON d.artifact_id = v.artifact_id
        WHERE v.id = ? LIMIT 1`).bind(artifactVersionId).first<{ deleting: number }>();
      if (deleting) {
        throw new ApiError(409, "ARTIFACT_DELETION_IN_PROGRESS", "Artifact deletion is in progress");
      }
      throw error;
    }
    if (winner.requestHash !== requestHash) {
      throw new ApiError(409, "IDEMPOTENCY_KEY_REUSED", "Idempotency key was already used for a different Compile request");
    }
    return { row: winner, duplicate: true as const };
  }
}

export async function markCompileJobFailed(
  row: CompileJobRow,
  error: JobError,
  options: { preserveImporting?: boolean } = {},
) {
  const now = nextTimestamp(row.updatedAt);
  const { DB } = await getBindings();
  const result = await DB.prepare(`UPDATE compile_jobs SET status = 'failed', stage = 'failed',
    progress = 100, error_json = ?, updated_at = ?,
    import_lease_until = NULL, next_import_at = NULL
    WHERE project_id = ? AND id = ? AND updated_at = ?
      AND status NOT IN ('completed', 'failed')${options.preserveImporting ? " AND stage <> 'importing'" : ""}`).bind(
    errorJson(error), now, PROJECT_ID, row.id, row.updatedAt,
  ).run();
  if (!changed(result)) return findCompileJob(row.createdBy, row.id);
  const failed = {
    ...row,
    status: "failed" as const,
    stage: "failed" as const,
    progress: 100,
    errorJson: errorJson(error),
    updatedAt: now,
  };
  await appendAudit(DB, failed, "failed", error);
  return failed;
}

export async function attachUpstreamJob(reservation: CompileJobRow, job: CompilerJob) {
  // A completed submission still needs a gateway-owned import lease. Keep it
  // pollable until the first GET atomically claims that import.
  const state = job.status === "completed"
    ? { status: "running" as const, stage: "validating" as const, progress: 96 }
    : compilerState(job);
  const next: CompileJobRow = {
    ...reservation,
    upstreamJobId: job.id,
    ...state,
    timingsJson: timingsJson(job),
    errorJson: errorJson(job.error),
    createdAt: job.createdAt,
    updatedAt: job.updatedAt,
    expiresAt: job.expiresAt,
  };
  const { DB } = await getBindings();
  let result: D1Result;
  try {
    result = await DB.prepare(`UPDATE compile_jobs SET upstream_job_id = ?, status = ?,
      stage = ?, progress = ?, timings_json = ?, error_json = ?, created_at = ?,
      updated_at = ?, expires_at = ? WHERE project_id = ? AND id = ?
      AND upstream_job_id = ? AND updated_at = ?
      AND status = 'queued' AND stage = 'queued'`).bind(
      next.upstreamJobId,
      next.status,
      next.stage,
      next.progress,
      next.timingsJson,
      next.errorJson,
      next.createdAt,
      next.updatedAt,
      next.expiresAt,
      PROJECT_ID,
      reservation.id,
      reservation.upstreamJobId,
      reservation.updatedAt,
    ).run();
  } catch (writeError) {
    let owner: CompileJobRow | null;
    try {
      owner = await findUpstreamOwner(DB, job.id);
    } catch {
      throw writeError;
    }
    if (!owner) throw writeError;
    if (owner.id === reservation.id) {
      await appendAudit(DB, owner, "submitted", {
        artifactVersionId: owner.artifactVersionId,
        upstreamJobId: owner.upstreamJobId,
      });
      return owner;
    }
    const error = new ApiError(502, "COMPILER_JOB_ID_REUSED", "Compiler returned a job ID already owned by another request");
    await markCompileJobFailed(reservation, {
      code: error.code,
      message: error.message,
      retryable: false,
    });
    throw error;
  }
  if (!changed(result)) return findCompileJob(reservation.createdBy, reservation.id);
  await appendAudit(DB, next, "submitted", {
    artifactVersionId: next.artifactVersionId,
    upstreamJobId: next.upstreamJobId,
  });
  if (next.status === "failed" && next.errorJson) {
    await appendAudit(DB, next, "failed", parseJson(next.errorJson, {}));
  }
  return next;
}

const STAGE_RANK: Partial<Record<CompilerStage | StoredStage, number>> = {
  queued: 0,
  parsing: 1,
  extracting: 2,
  validating: 3,
  completed: 4,
};

function hasCompatibleExpiry(row: CompileJobRow, job: CompilerJob) {
  if (job.expiresAt === row.expiresAt) return true;
  if (job.status !== "completed" && job.status !== "failed") return false;
  const previousExpiry = Date.parse(row.expiresAt);
  const updatedAt = Date.parse(job.updatedAt);
  const nextExpiry = Date.parse(job.expiresAt);
  return Number.isFinite(previousExpiry)
    && Number.isFinite(updatedAt)
    && Number.isFinite(nextExpiry)
    && nextExpiry >= previousExpiry
    && nextExpiry >= updatedAt
    && nextExpiry <= updatedAt + TERMINAL_EXPIRY_LIMIT_MS;
}

function assertUpstreamTransition(row: CompileJobRow, job: CompilerJob) {
  if (job.id !== row.upstreamJobId) {
    throw new ApiError(502, "COMPILER_JOB_MISMATCH", "Compiler returned a job that is not owned by this Compile request");
  }
  if (job.createdAt !== row.createdAt || !hasCompatibleExpiry(row, job)) {
    throw new ApiError(502, "COMPILER_JOB_MISMATCH", "Compiler changed immutable job metadata");
  }
  if (Date.parse(job.updatedAt) < Date.parse(row.updatedAt)) {
    throw new ApiError(502, "COMPILER_STATE_REGRESSION", "Compiler returned an older job state");
  }
  const currentRank = STAGE_RANK[row.stage];
  const nextRank = STAGE_RANK[job.status];
  if (job.status !== "failed" && currentRank !== undefined && nextRank !== undefined && nextRank < currentRank) {
    throw new ApiError(502, "COMPILER_STATE_REGRESSION", "Compiler job stage regressed");
  }
}

export async function updateCompileJobFromUpstream(row: CompileJobRow, job: CompilerJob) {
  assertUpstreamTransition(row, job);
  const state = compilerState(job);
  const next: CompileJobRow = {
    ...row,
    ...state,
    timingsJson: timingsJson(job),
    errorJson: errorJson(job.error),
    updatedAt: job.updatedAt,
    expiresAt: job.expiresAt,
  };
  if (next.status === row.status
    && next.stage === row.stage
    && next.progress === row.progress
    && next.timingsJson === row.timingsJson
    && next.errorJson === row.errorJson
    && next.updatedAt === row.updatedAt
    && next.expiresAt === row.expiresAt) {
    // Repeated polls must not fire the compile_jobs projection triggers. Read
    // again because cancellation or an import may have won while upstream ran.
    return findCompileJob(row.createdBy, row.id);
  }
  const { DB } = await getBindings();
  const result = await DB.prepare(`UPDATE compile_jobs SET status = ?, stage = ?, progress = ?,
    timings_json = ?, error_json = ?, updated_at = ?, expires_at = ?
    WHERE project_id = ? AND id = ? AND updated_at = ?
      AND status = ? AND stage = ?
      AND status NOT IN ('completed', 'failed') AND stage <> 'importing'`).bind(
    next.status,
    next.stage,
    next.progress,
    next.timingsJson,
    next.errorJson,
    next.updatedAt,
    next.expiresAt,
    PROJECT_ID,
    row.id,
    row.updatedAt,
    row.status,
    row.stage,
  ).run();
  if (!changed(result)) return findCompileJob(row.createdBy, row.id);
  if (next.status === "failed") {
    await appendAudit(DB, next, "failed", job.error ?? {
      code: "COMPILER_FAILED",
      message: "Compiler job failed",
      retryable: false,
    });
  }
  return next;
}

export async function claimCompletedImport(row: CompileJobRow, job: CompilerJob) {
  assertUpstreamTransition(row, job);
  // Import lease age is gateway-local state. Never let upstream clock skew pin
  // a retryable import in the importing stage indefinitely.
  const leaseAt = new Date().toISOString();
  const leaseUntil = new Date(Date.parse(leaseAt) + IMPORT_LEASE_MS).toISOString();
  const next: CompileJobRow = {
    ...row,
    status: "running",
    stage: "importing",
    progress: 97,
    timingsJson: timingsJson(job),
    errorJson: null,
    updatedAt: leaseAt,
    expiresAt: job.expiresAt,
    importLeaseUntil: leaseUntil,
  };
  const { DB } = await getBindings();
  const result = await DB.prepare(`UPDATE compile_jobs SET status = 'running',
    stage = 'importing', progress = 97, timings_json = ?, error_json = NULL,
    updated_at = ?, expires_at = ?, ${IMPORT_LEASE_SQL}
    WHERE project_id = ? AND id = ?
    AND updated_at = ? AND status NOT IN ('completed', 'failed')
    AND stage <> 'importing'`).bind(
    next.timingsJson,
    next.updatedAt,
    next.expiresAt,
    PROJECT_ID,
    row.id,
    row.updatedAt,
  ).run();
  return changed(result)
    ? { row: next, claimed: true as const }
    : { row: await findCompileJob(row.createdBy, row.id), claimed: false as const };
}

/**
 * Lease a retained import whose previous lease ended (crash, timeout or a
 * recorded failure) once its backoff is due. The lease is explicit, so a live
 * importer is never duplicated and a failed attempt never waits for the lease.
 */
export async function claimDueImport(row: CompileJobRow, now = new Date()) {
  const at = now.toISOString();
  if ((row.importLeaseUntil && row.importLeaseUntil > at) || (row.nextImportAt && row.nextImportAt > at)) {
    return { row, claimed: false as const };
  }
  const leaseAt = nextTimestamp(row.updatedAt);
  const { DB } = await getBindings();
  const result = await DB.prepare(`UPDATE compile_jobs SET updated_at = ?, ${IMPORT_LEASE_SQL}
    WHERE project_id = ? AND id = ? AND updated_at = ?
      AND status = 'running' AND stage = 'importing'
      AND (import_lease_until IS NULL OR import_lease_until <= ?)
      AND (next_import_at IS NULL OR next_import_at <= ?)`).bind(
    leaseAt, PROJECT_ID, row.id, row.updatedAt, at, at,
  ).run();
  return changed(result)
    ? {
        row: {
          ...row,
          updatedAt: leaseAt,
          importLeaseUntil: new Date(Date.parse(leaseAt) + IMPORT_LEASE_MS).toISOString(),
        },
        claimed: true as const,
      }
    : { row: await findCompileJobRow(row.id), claimed: false as const };
}

/** Release the lease after a retryable failure and schedule the next attempt. */
export async function recordImportRetry(row: CompileJobRow, error: JobError, nextAttemptAt: string) {
  const now = nextTimestamp(row.updatedAt);
  const { DB } = await getBindings();
  const result = await DB.prepare(`UPDATE compile_jobs SET import_attempts = import_attempts + 1,
    import_error_json = ?, next_import_at = ?, import_lease_until = NULL, updated_at = ?
    WHERE project_id = ? AND id = ? AND updated_at = ?
      AND status = 'running' AND stage = 'importing'`).bind(
    errorJson(error), nextAttemptAt, now, PROJECT_ID, row.id, row.updatedAt,
  ).run();
  if (!changed(result)) return findCompileJobRow(row.id);
  return {
    ...row,
    importAttempts: Number(row.importAttempts ?? 0) + 1,
    importErrorJson: errorJson(error),
    nextImportAt: nextAttemptAt,
    importLeaseUntil: null,
    updatedAt: now,
  } satisfies CompileJobRow;
}

/** Remember that the paid Compiler result now also lives in R2. */
export async function markResultStaged(row: CompileJobRow) {
  const { DB } = await getBindings();
  await DB.prepare(`UPDATE compile_jobs SET result_staged = 1
    WHERE project_id = ? AND id = ? AND result_staged = 0`).bind(PROJECT_ID, row.id).run();
  return { ...row, resultStaged: 1 } satisfies CompileJobRow;
}

/**
 * Cancel an import that is waiting between attempts. A running importer holds
 * the lease, so it can never be cancelled into a reported failure mid-commit.
 */
export async function cancelIdleImport(row: CompileJobRow, error: JobError, now = new Date()) {
  const at = now.toISOString();
  const updatedAt = nextTimestamp(row.updatedAt);
  const { DB } = await getBindings();
  const result = await DB.prepare(`UPDATE compile_jobs SET status = 'failed', stage = 'failed',
    progress = 100, error_json = ?, updated_at = ?, import_lease_until = NULL, next_import_at = NULL
    WHERE project_id = ? AND id = ? AND updated_at = ?
      AND status = 'running' AND stage = 'importing'
      AND (import_lease_until IS NULL OR import_lease_until <= ?)`).bind(
    errorJson(error), updatedAt, PROJECT_ID, row.id, row.updatedAt, at,
  ).run();
  if (!changed(result)) return findCompileJobRow(row.id);
  const failed: CompileJobRow = {
    ...row,
    status: "failed",
    stage: "failed",
    progress: 100,
    errorJson: errorJson(error),
    updatedAt,
    importLeaseUntil: null,
    nextImportAt: null,
  };
  await appendAudit(DB, failed, "failed", error);
  return failed;
}

/**
 * Maintainer recovery: reopen a failed import whose paid Compiler result is
 * kept in R2, so the work pump imports it again (for example after a fix).
 */
export async function reopenFailedImport(id: string) {
  if (!JOB_ID_RE.test(id)) throw new ApiError(400, "INVALID_COMPILE_JOB_ID", "job_id must be a UUID");
  const row = await findCompileJobRow(id);
  if (row.status !== "failed" || Number(row.resultStaged ?? 0) !== 1) {
    throw new ApiError(409, "COMPILE_IMPORT_NOT_REOPENABLE", "Only a failed import with a retained Compile result can be imported again");
  }
  const now = nextTimestamp(row.updatedAt);
  const { DB } = await getBindings();
  const result = await DB.prepare(`UPDATE compile_jobs SET status = 'running', stage = 'importing',
    progress = 97, error_json = NULL, import_attempts = 0, import_error_json = NULL,
    next_import_at = NULL, import_lease_until = NULL, updated_at = ?
    WHERE project_id = ? AND id = ? AND updated_at = ? AND status = 'failed' AND result_staged = 1`)
    .bind(now, PROJECT_ID, id, row.updatedAt).run();
  if (!changed(result)) {
    throw new ApiError(409, "COMPILE_JOB_CHANGED", "The Compile job changed while it was being reopened");
  }
  return findCompileJobRow(id);
}

/** Worker-side lookup; the worker acts for every actor's queued work. */
export async function findCompileJobRow(id: string) {
  const { DB } = await getBindings();
  const row = await DB.prepare(`${SELECT_JOB} WHERE project_id = ? AND id = ? LIMIT 1`)
    .bind(PROJECT_ID, id).first<CompileJobRow>();
  if (!row) throw new ApiError(404, "COMPILE_JOB_NOT_FOUND", "Compile job was not found");
  return row;
}

/** Oldest active jobs across actors, for the work pump. */
export async function listActiveCompileJobsForWorker(limit = 200) {
  const { DB } = await getBindings();
  const rows = await DB.prepare(`${SELECT_JOB} WHERE project_id = ?
    AND status IN ('queued', 'running') ORDER BY created_at, id LIMIT ?`)
    .bind(PROJECT_ID, limit).all<CompileJobRow>();
  return rows.results;
}

export async function completeCompileImport(
  row: CompileJobRow,
  snapshotId: string,
  duplicateSnapshot: boolean,
) {
  const now = nextTimestamp(row.updatedAt);
  const { DB } = await getBindings();
  const result = await DB.prepare(`UPDATE compile_jobs SET status = 'completed',
    stage = 'completed', progress = 100, snapshot_id = ?, error_json = NULL,
    updated_at = ?, import_lease_until = NULL, next_import_at = NULL
    WHERE project_id = ? AND id = ? AND updated_at = ?
    AND status = 'running' AND stage = 'importing'`).bind(
    snapshotId, now, PROJECT_ID, row.id, row.updatedAt,
  ).run();
  if (!changed(result)) return findCompileJob(row.createdBy, row.id);
  const completed: CompileJobRow = {
    ...row,
    status: "completed",
    stage: "completed",
    progress: 100,
    snapshotId,
    errorJson: null,
    updatedAt: now,
  };
  await appendAudit(DB, completed, "completed", {
    artifactVersionId: row.artifactVersionId,
    snapshotId,
    duplicateSnapshot,
  });
  return completed;
}

export async function ensureTerminalAudit(row: CompileJobRow) {
  const { DB } = await getBindings();
  if (row.status === "completed") {
    await appendAudit(DB, row, "completed", {
      artifactVersionId: row.artifactVersionId,
      snapshotId: row.snapshotId,
    });
  } else if (row.status === "failed") {
    await appendAudit(DB, row, "failed", parseJson(row.errorJson, {
      code: "COMPILER_FAILED",
      message: "Compiler job failed",
      retryable: false,
    }));
  }
  return row;
}

export async function ensureSubmittedAudit(row: CompileJobRow) {
  if (row.upstreamJobId.startsWith(PENDING_UPSTREAM_PREFIX)) return row;
  const { DB } = await getBindings();
  await appendAudit(DB, row, "submitted", {
    artifactVersionId: row.artifactVersionId,
    upstreamJobId: row.upstreamJobId,
  });
  return row;
}

/** Repair both lifecycle audit records after a prior state-write/audit-write split. */
export async function ensureJobAudits(row: CompileJobRow) {
  await ensureSubmittedAudit(row);
  await ensureTerminalAudit(row);
  return row;
}


export async function listActorCompileJobs(actor: string) {
  const { DB } = await getBindings();
  const rows = await DB.prepare(`SELECT selected.*, v.original_file_name AS fileName
    FROM (${SELECT_JOB} WHERE project_id = ? AND created_by = ?
    AND (status IN ('queued', 'running') OR id IN (
      SELECT id FROM compile_jobs WHERE project_id = ? AND created_by = ?
      AND status IN ('completed', 'failed') ORDER BY created_at DESC LIMIT 25
    ))) selected
    LEFT JOIN artifact_versions v ON v.id = selected.artifactVersionId
    ORDER BY selected.createdAt DESC`).bind(PROJECT_ID, actor, PROJECT_ID, actor).all<CompileJobRow>();
  return { ok: true as const, jobs: rows.results.map(compileJobReceipt) };
}

export async function listActiveCompileJobRows(actor: string) {
  const { DB } = await getBindings();
  const rows = await DB.prepare(`${SELECT_JOB} WHERE project_id = ? AND created_by = ?
    AND status IN ('queued', 'running') ORDER BY created_at`).bind(PROJECT_ID, actor).all<CompileJobRow>();
  return rows.results;
}
