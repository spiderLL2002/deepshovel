import type { Activity } from "./workspace-types.ts";

const MAX_TERMINAL_HISTORY = 50;

/** Unsuccessful tasks remain reachable for an explicit retry or list dismissal. */
export function isInterruptedActivity(item: Activity) {
  return item.state === "failed" || item.state === "cancelled";
}

function compactSupersededActivities(items: Activity[]) {
  const compileVersions = new Set(
    items
      .filter((item) => item.kind === "compile" && item.artifactVersionId)
      .map((item) => item.artifactVersionId as string),
  );
  const activeCompileVersions = new Set(
    items
      .filter((item) => item.kind === "compile" && item.state === "working" && item.artifactVersionId)
      .map((item) => item.artifactVersionId as string),
  );
  const terminalCompileVersions = new Set<string>();

  return items.filter((item) => {
    const versionId = item.artifactVersionId;
    if (!versionId) return true;

    // Upload completion is only a hand-off state. Once the exact stored version
    // has entered Compile, keep the Compile lifecycle as the one visible row.
    if (item.kind === "upload" && item.state === "done" && compileVersions.has(versionId)) {
      return false;
    }
    if (item.kind !== "compile" || item.state === "working") return true;

    // Never hide active work. If a version is being compiled again, its active
    // job supersedes prior terminal rows; otherwise retain only the newest
    // terminal row in this newest-first activity timeline.
    if (activeCompileVersions.has(versionId) || terminalCompileVersions.has(versionId)) {
      return false;
    }
    terminalCompileVersions.add(versionId);
    return true;
  });
}

function limitTerminalHistory(items: Activity[]) {
  let terminalCount = 0;
  return items.filter((item) => {
    if (item.state === "working") return true;
    terminalCount += 1;
    return terminalCount <= MAX_TERMINAL_HISTORY;
  });
}

function sameActivity(left: Activity, right: Activity) {
  if (left.id === right.id) return true;
  return left.kind === "compile"
    && right.kind === "compile"
    && Boolean(left.jobId)
    && left.jobId === right.jobId;
}

function timestamp(value: string | undefined) {
  const parsed = value ? Date.parse(value) : Number.NaN;
  return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY;
}

function mergeMatches(matches: Activity[], incoming: Activity) {
  // Activity is ordered newest-first. Build the metadata base from oldest to
  // newest, then let the explicit incoming update win.
  const merged = [...matches].reverse().reduce<Activity>(
    (current, item) => ({ ...current, ...item }),
    incoming,
  );
  Object.assign(merged, incoming, { id: incoming.id });

  if (incoming.kind !== "compile" || !incoming.jobId) return merged;
  const stateSource = [...matches, incoming].reduce((newest, candidate) => (
    timestamp(candidate.sourceUpdatedAt) > timestamp(newest.sourceUpdatedAt)
      ? candidate
      : newest
  ), incoming);
  return {
    ...merged,
    state: stateSource.state,
    detail: stateSource.detail,
    cancellable: stateSource.cancellable,
    progress: stateSource.progress,
    stage: stateSource.stage,
    sourceUpdatedAt: stateSource.sourceUpdatedAt,
  };
}

/** Upsert one browser activity and collapse all UI rows for the same service job. */
export function upsertActivity(items: Activity[], incoming: Activity) {
  const matchingIndexes: number[] = [];
  for (let index = 0; index < items.length; index += 1) {
    if (sameActivity(items[index], incoming)) matchingIndexes.push(index);
  }
  if (!matchingIndexes.length) {
    return limitTerminalHistory(compactSupersededActivities([incoming, ...items]));
  }

  const matches = matchingIndexes.map((index) => items[index]);
  const insertAt = matchingIndexes[0];
  const matching = new Set(matchingIndexes);
  const next = items.filter((_, index) => !matching.has(index));
  next.splice(insertAt, 0, mergeMatches(matches, incoming));
  return limitTerminalHistory(compactSupersededActivities(next));
}

export function patchActivity(items: Activity[], id: string, patch: Partial<Activity>) {
  const current = items.find((item) => item.id === id);
  return current ? upsertActivity(items, { ...current, ...patch }) : items;
}

/** Defensive normalization for component callers and task-count calculation. */
export function normalizeActivities(items: Activity[]) {
  // Live state already has unique identities. Avoid rebuilding thousands of
  // upload rows one by one on every Compile poll and render.
  const ids = new Set<string>();
  const jobs = new Set<string>();
  const unique = items.every((item) => {
    if (ids.has(item.id)) return false;
    ids.add(item.id);
    if (item.kind === "compile" && item.jobId) {
      if (jobs.has(item.jobId)) return false;
      jobs.add(item.jobId);
    }
    return true;
  });
  if (unique) return limitTerminalHistory(compactSupersededActivities(items));
  let normalized: Activity[] = [];
  // Apply oldest first so the newest visible row remains authoritative.
  for (const item of [...items].reverse()) normalized = upsertActivity(normalized, item);
  return limitTerminalHistory(compactSupersededActivities(normalized));
}

/** Keep recent terminal history, but forget job rows no longer returned by the service. */
export function reconcileCompileActivities(
  items: Activity[],
  incoming: Activity[],
  listedJobIds: ReadonlySet<string>,
) {
  let next = normalizeActivities(items).filter((item) => (
    item.kind !== "compile" || !item.jobId || listedJobIds.has(item.jobId)
  ));
  for (const item of incoming) next = upsertActivity(next, item);
  return next;
}

export function activeActivityCount(items: Activity[]) {
  return normalizeActivities(items).filter((item) => item.state === "working").length;
}

/** Local queued submissions are cancellable before the gateway returns a job ID. */
export function canCancelActivity(item: Activity) {
  if (item.state !== "working") return false;
  if (item.stage === "importing") return false;
  if (item.cancellable) return true;
  return item.kind === "compile" && !item.jobId && item.stage === "queued";
}

export const canCancelAllActivity = canCancelActivity;

export function activityTaskButtonLabel(activeCount: number) {
  return activeCount > 0
    ? `当前任务，${activeCount} 项进行中`
    : "当前任务，没有进行中的任务";
}
