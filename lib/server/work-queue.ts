/**
 * Durable background work items (a transactional outbox) for the Web tier.
 *
 * Work is recorded in D1 together with the facts that create it and executed
 * by the local work pump through `POST /api/internal/work/tick`. Each item is
 * leased by one runner at a time; a lease that outlives a crashed runner is
 * reclaimed. Attempts, backoff, progress and the last failure cause persist,
 * so work resumes after any restart from its saved progress. Browser code only
 * observes these rows.
 */
import type { DurableFailure } from "./evidence-store.ts";

export type WorkKind = "retrieval_index" | "claim_relations";
export type WorkState = "pending" | "running" | "succeeded" | "failed" | "cancelled";

export type WorkItem = {
  id: string;
  projectId: string;
  kind: WorkKind;
  subjectId: string;
  state: WorkState;
  priority: number;
  attempts: number;
  maxAttempts: number;
  nextAttemptAt: string;
  leaseToken: string | null;
  leaseExpiresAt: string | null;
  progress: Record<string, unknown>;
  lastError: DurableFailure | null;
  createdAt: string;
  updatedAt: string;
  completedAt: string | null;
};

type WorkRow = Omit<WorkItem, "progress" | "lastError"> & {
  progressJson: string;
  lastErrorJson: string | null;
};

export const WORK_KINDS: readonly WorkKind[] = ["retrieval_index", "claim_relations"];

const POLICY: Record<WorkKind, { priority: number; maxAttempts: number; baseDelayMs: number; maxDelayMs: number }> = {
  // Index work blocks searchability and relation analysis; run it first.
  retrieval_index: { priority: 50, maxAttempts: 8, baseDelayMs: 30_000, maxDelayMs: 30 * 60_000 },
  claim_relations: { priority: 100, maxAttempts: 6, baseDelayMs: 60_000, maxDelayMs: 60 * 60_000 },
};

export const WORK_LEASE_MS = 5 * 60_000;
const MAX_PROGRESS_BYTES = 16 * 1024;

const SELECT_WORK = `SELECT id, project_id AS projectId, kind, subject_id AS subjectId,
  state, priority, attempts, max_attempts AS maxAttempts,
  next_attempt_at AS nextAttemptAt, lease_token AS leaseToken,
  lease_expires_at AS leaseExpiresAt, progress_json AS progressJson,
  last_error_json AS lastErrorJson, created_at AS createdAt,
  updated_at AS updatedAt, completed_at AS completedAt FROM work_items`;

export function workItemId(kind: WorkKind, subjectId: string) {
  return `${kind}:${subjectId}`;
}

function parseObject(value: string | null): Record<string, unknown> | null {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : null;
  } catch {
    return null;
  }
}

function fromRow(row: WorkRow): WorkItem {
  const { progressJson, lastErrorJson, ...rest } = row;
  const error = parseObject(lastErrorJson);
  return {
    ...rest,
    attempts: Number(rest.attempts),
    maxAttempts: Number(rest.maxAttempts),
    priority: Number(rest.priority),
    progress: parseObject(progressJson) ?? {},
    lastError: error && typeof error.code === "string" && typeof error.message === "string"
      ? { code: error.code, message: error.message, retryable: error.retryable === true }
      : null,
  };
}

function encodeProgress(progress: Record<string, unknown>) {
  const encoded = JSON.stringify(progress);
  if (new TextEncoder().encode(encoded).byteLength > MAX_PROGRESS_BYTES) {
    throw new TypeError("Work progress exceeds its durable size bound");
  }
  return encoded;
}

function encodeError(error: DurableFailure) {
  return JSON.stringify({
    code: error.code.slice(0, 160),
    message: error.message.slice(0, 2_000),
    retryable: error.retryable,
  });
}

function changed(result: D1Result) {
  return Number(result.meta.changes ?? 0) > 0;
}

export function retryDelayMs(kind: WorkKind, attempts: number) {
  const policy = POLICY[kind];
  return Math.min(policy.maxDelayMs, policy.baseDelayMs * 2 ** Math.max(0, attempts - 1));
}

/**
 * Create the item if it does not exist. Suitable for the same D1 batch as the
 * import that creates the work, which makes the outbox atomic with the facts.
 */
export function enqueueWorkStatement(
  DB: D1Database,
  projectId: string,
  kind: WorkKind,
  subjectId: string,
  now: string,
) {
  const policy = POLICY[kind];
  return DB.prepare(`INSERT OR IGNORE INTO work_items
    (id, project_id, kind, subject_id, state, priority, attempts, max_attempts,
     next_attempt_at, progress_json, created_at, updated_at)
    VALUES (?, ?, ?, ?, 'pending', ?, 0, ?, ?, '{}', ?, ?)`).bind(
    workItemId(kind, subjectId),
    projectId,
    kind,
    subjectId,
    policy.priority,
    policy.maxAttempts,
    now,
    now,
    now,
  );
}

/**
 * Ask for (more) work on subjects: create missing items and re-arm finished
 * ones with a fresh attempt budget. Pending or running items are unchanged, so
 * repeated requests never duplicate or interrupt work.
 */
export async function requestWork(
  DB: D1Database,
  projectId: string,
  kind: WorkKind,
  subjectIds: readonly string[],
  now = new Date().toISOString(),
) {
  const unique = [...new Set(subjectIds)];
  if (!unique.length) return 0;
  const policy = POLICY[kind];
  let requested = 0;
  for (let offset = 0; offset < unique.length; offset += 256) {
    const chunk = unique.slice(offset, offset + 256);
    const result = await DB.prepare(`INSERT INTO work_items
      (id, project_id, kind, subject_id, state, priority, attempts, max_attempts,
       next_attempt_at, progress_json, created_at, updated_at)
      SELECT ? || ':' || value, ?, ?, value, 'pending', ?, 0, ?, ?, '{}', ?, ?
      FROM json_each(?) WHERE true
      ON CONFLICT(project_id, kind, subject_id) DO UPDATE SET
        state = 'pending', attempts = 0, next_attempt_at = excluded.next_attempt_at,
        progress_json = '{}', last_error_json = NULL, completed_at = NULL,
        updated_at = excluded.updated_at
      WHERE work_items.state IN ('succeeded', 'failed', 'cancelled')`).bind(
      kind,
      projectId,
      kind,
      policy.priority,
      policy.maxAttempts,
      now,
      now,
      now,
      JSON.stringify(chunk),
    ).run();
    requested += Number(result.meta.changes ?? 0);
  }
  return requested;
}

export async function getWorkItem(DB: D1Database, projectId: string, kind: WorkKind, subjectId: string) {
  const row = await DB.prepare(`${SELECT_WORK} WHERE project_id = ? AND kind = ? AND subject_id = ? LIMIT 1`)
    .bind(projectId, kind, subjectId).first<WorkRow>();
  return row ? fromRow(row) : null;
}

/**
 * Lease due items: pending items whose next attempt is due, plus running items
 * whose runner died (expired lease). Each lease is a compare-and-swap, so two
 * concurrent ticks never run the same item.
 */
export async function claimDueWork(
  DB: D1Database,
  projectId: string,
  {
    kinds = WORK_KINDS,
    limit,
    now = new Date(),
    leaseMs = WORK_LEASE_MS,
  }: { kinds?: readonly WorkKind[]; limit: number; now?: Date; leaseMs?: number },
) {
  const at = now.toISOString();
  const candidates = await DB.prepare(`${SELECT_WORK}
    WHERE project_id = ? AND kind IN (SELECT value FROM json_each(?))
      AND ((state = 'pending' AND next_attempt_at <= ?)
        OR (state = 'running' AND lease_expires_at <= ?))
    ORDER BY priority, next_attempt_at, created_at, id
    LIMIT ?`).bind(projectId, JSON.stringify(kinds), at, at, limit).all<WorkRow>();
  const leased: WorkItem[] = [];
  for (const row of candidates.results) {
    const token = crypto.randomUUID();
    const leaseUntil = new Date(now.getTime() + leaseMs).toISOString();
    const result = await DB.prepare(`UPDATE work_items SET state = 'running',
      lease_token = ?, lease_expires_at = ?, updated_at = ?
      WHERE id = ? AND updated_at = ? AND state = ?
        AND (state = 'pending' OR lease_expires_at <= ?)`).bind(
      token, leaseUntil, at, row.id, row.updatedAt, row.state, at,
    ).run();
    if (changed(result)) {
      leased.push(fromRow({ ...row, state: "running", leaseToken: token, leaseExpiresAt: leaseUntil, updatedAt: at }));
    }
  }
  return leased;
}

async function settle(
  DB: D1Database,
  item: WorkItem,
  fields: {
    state: WorkState;
    attempts?: number;
    nextAttemptAt?: string;
    progress?: Record<string, unknown>;
    error?: DurableFailure | null;
  },
) {
  const now = new Date().toISOString();
  const terminal = fields.state === "succeeded" || fields.state === "failed" || fields.state === "cancelled";
  const result = await DB.prepare(`UPDATE work_items SET state = ?, attempts = ?,
    next_attempt_at = ?, lease_token = NULL, lease_expires_at = NULL,
    progress_json = ?, last_error_json = ?, updated_at = ?, completed_at = ?
    WHERE id = ? AND state = 'running' AND lease_token = ?`).bind(
    fields.state,
    fields.attempts ?? item.attempts,
    fields.nextAttemptAt ?? item.nextAttemptAt,
    encodeProgress(fields.progress ?? item.progress),
    fields.error === undefined
      ? (item.lastError ? encodeError(item.lastError) : null)
      : fields.error === null ? null : encodeError(fields.error),
    now,
    terminal ? now : null,
    item.id,
    item.leaseToken,
  ).run();
  // A lost lease means a cancellation or a reclaim won; that writer owns the row.
  return changed(result);
}

export function completeWork(DB: D1Database, item: WorkItem, progress?: Record<string, unknown>) {
  return settle(DB, item, { state: "succeeded", progress, error: null });
}

export function cancelLeasedWork(DB: D1Database, item: WorkItem, reason: string, progress?: Record<string, unknown>) {
  return settle(DB, item, {
    state: "cancelled",
    progress: { ...(progress ?? item.progress), reason },
  });
}

/** Yield without consuming an attempt, e.g. while polling or waiting on a dependency. */
export function deferWork(DB: D1Database, item: WorkItem, delayMs: number, progress?: Record<string, unknown>) {
  return settle(DB, item, {
    state: "pending",
    nextAttemptAt: new Date(Date.now() + Math.max(0, delayMs)).toISOString(),
    progress,
  });
}

/** Record a failed attempt; retry with backoff or fail once the budget is spent. */
export function failWorkAttempt(
  DB: D1Database,
  item: WorkItem,
  error: DurableFailure,
  progress?: Record<string, unknown>,
) {
  const attempts = item.attempts + 1;
  if (!error.retryable || attempts >= item.maxAttempts) {
    return settle(DB, item, { state: "failed", attempts, progress, error });
  }
  return settle(DB, item, {
    state: "pending",
    attempts,
    nextAttemptAt: new Date(Date.now() + retryDelayMs(item.kind, attempts)).toISOString(),
    progress,
    error,
  });
}

/** Cancel queued or running work for subjects (a running lease then fails its CAS). */
export async function cancelWork(DB: D1Database, projectId: string, kind: WorkKind, subjectId: string, reason: string) {
  const now = new Date().toISOString();
  const result = await DB.prepare(`UPDATE work_items SET state = 'cancelled',
    lease_token = NULL, lease_expires_at = NULL,
    progress_json = json_set(progress_json, '$.reason', ?),
    updated_at = ?, completed_at = ?
    WHERE project_id = ? AND kind = ? AND subject_id = ? AND state IN ('pending', 'running')`).bind(
    reason, now, now, projectId, kind, subjectId,
  ).run();
  return changed(result);
}

/** A bounded view for observers: open items first, then recently finished ones. */
export async function listRecentWork(
  DB: D1Database,
  projectId: string,
  {
    kinds = WORK_KINDS,
    openLimit = 50,
    finishedLimit = 25,
  }: { kinds?: readonly WorkKind[]; openLimit?: number; finishedLimit?: number } = {},
) {
  const kindList = JSON.stringify(kinds);
  const [open, finished, counts] = await Promise.all([
    DB.prepare(`${SELECT_WORK} WHERE project_id = ? AND kind IN (SELECT value FROM json_each(?))
      AND state IN ('pending', 'running')
      ORDER BY state = 'running' DESC, priority, next_attempt_at, id LIMIT ?`)
      .bind(projectId, kindList, openLimit).all<WorkRow>(),
    DB.prepare(`${SELECT_WORK} WHERE project_id = ? AND kind IN (SELECT value FROM json_each(?))
      AND state IN ('succeeded', 'failed', 'cancelled')
      ORDER BY updated_at DESC, id DESC LIMIT ?`)
      .bind(projectId, kindList, finishedLimit).all<WorkRow>(),
    DB.prepare(`SELECT kind, state, COUNT(*) AS count FROM work_items
      WHERE project_id = ? AND kind IN (SELECT value FROM json_each(?))
      GROUP BY kind, state`).bind(projectId, kindList).all<{ kind: WorkKind; state: WorkState; count: number }>(),
  ]);
  return {
    open: open.results.map(fromRow),
    finished: finished.results.map(fromRow),
    counts: counts.results.map((row) => ({ kind: row.kind, state: row.state, count: Number(row.count) })),
  };
}

export async function countOpenWork(DB: D1Database, projectId: string) {
  const row = await DB.prepare(`SELECT COUNT(*) AS open FROM work_items
    WHERE project_id = ? AND state IN ('pending', 'running')`).bind(projectId).first<{ open: number }>();
  return Number(row?.open ?? 0);
}
