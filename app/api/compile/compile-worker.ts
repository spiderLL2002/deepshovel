/**
 * Server-side progression of every active Compile job, run by the work pump.
 * The browser and GET endpoints only observe the resulting D1 state.
 */
import { CompilerClient, MAX_JOB_LOOKUP_IDS, type CompilerJob } from "@/lib/server/compiler-client.ts";
import { durableFailure, getCompilerConfiguration } from "@/lib/server/evidence-store";
import {
  listActiveCompileJobsForWorker,
  PENDING_UPSTREAM_PREFIX,
  type CompileJobRow,
} from "./compile-job-store.ts";
import { advanceCompileJob } from "./compile-store.ts";

const ACTIVE_JOB_WINDOW = 500;
const RECONCILE_GRACE_MS = 10_000;

export type CompileProgressSummary = {
  active: number;
  advanced: number;
  completed: number;
  failed: number;
  errors: { jobId: string; code: string }[];
  more: boolean;
};

type Candidate = { row: CompileJobRow; upstream?: CompilerJob | null; rank: number; imports: boolean };

function isReservation(row: CompileJobRow) {
  return row.upstreamJobId.startsWith(PENDING_UPSTREAM_PREFIX);
}

function importDue(row: CompileJobRow, now: string) {
  return !(row.importLeaseUntil && row.importLeaseUntil > now)
    && !(row.nextImportAt && row.nextImportAt > now);
}

function unchanged(row: CompileJobRow, upstream: CompilerJob) {
  return upstream.status === row.stage
    && upstream.updatedAt === row.updatedAt
    && upstream.expiresAt === row.expiresAt;
}

/**
 * Advance active jobs in priority order until `deadline`: finished Compiler
 * results first (their imports make results visible), then due import retries,
 * then progress updates, then reservation reconciliation. Thousands of queued
 * jobs therefore never starve imports, and unchanged jobs cost no D1 write.
 */
export async function advanceCompileJobs({
  deadline,
  maxImports = 4,
  advance = advanceCompileJob,
  compiler,
}: {
  deadline: number;
  maxImports?: number;
  /** Test seams; production uses the real step and Compiler client. */
  advance?: typeof advanceCompileJob;
  compiler?: Pick<CompilerClient, "listJobs">;
}): Promise<CompileProgressSummary> {
  const rows = await listActiveCompileJobsForWorker(ACTIVE_JOB_WINDOW);
  const summary: CompileProgressSummary = {
    active: rows.length,
    advanced: 0,
    completed: 0,
    failed: 0,
    errors: [],
    more: rows.length >= ACTIVE_JOB_WINDOW,
  };
  if (!rows.length) return summary;
  const now = new Date().toISOString();
  const attached = rows.filter((row) => !isReservation(row) && row.stage !== "importing");
  const upstream = new Map<string, CompilerJob | null>();
  let statusAvailable = true;
  if (attached.length) {
    const client = compiler ?? new CompilerClient(await getCompilerConfiguration());
    for (let offset = 0; offset < attached.length; offset += MAX_JOB_LOOKUP_IDS) {
      const chunk = attached.slice(offset, offset + MAX_JOB_LOOKUP_IDS);
      try {
        const jobs = await client.listJobs(chunk.map((row) => row.upstreamJobId));
        const byId = new Map(jobs.map((job) => [job.id, job]));
        for (const row of chunk) upstream.set(row.id, byId.get(row.upstreamJobId) ?? null);
      } catch (error) {
        // The Compiler is unavailable: skip progress this pass, keep importing.
        statusAvailable = false;
        summary.errors.push({ jobId: "*", code: durableFailure(error).code });
        break;
      }
    }
  }
  const candidates: Candidate[] = [];
  for (const row of rows) {
    if (isReservation(row)) {
      if (Date.now() > Date.parse(row.expiresAt) + RECONCILE_GRACE_MS) {
        candidates.push({ row, rank: 3, imports: false });
      }
    } else if (row.stage === "importing") {
      if (importDue(row, now)) candidates.push({ row, rank: 1, imports: true });
    } else if (statusAvailable && upstream.has(row.id)) {
      const job = upstream.get(row.id) ?? null;
      if (job === null) candidates.push({ row, upstream: null, rank: 2, imports: false });
      else if (job.status === "completed") candidates.push({ row, upstream: job, rank: 0, imports: true });
      else if (!unchanged(row, job)) candidates.push({ row, upstream: job, rank: 2, imports: false });
    }
  }
  candidates.sort((left, right) => left.rank - right.rank
    || left.row.createdAt.localeCompare(right.row.createdAt)
    || left.row.id.localeCompare(right.row.id));
  let imports = 0;
  for (const candidate of candidates) {
    if (Date.now() >= deadline) {
      summary.more = true;
      break;
    }
    if (candidate.imports && imports >= maxImports) {
      summary.more = true;
      continue;
    }
    try {
      const next = await advance(candidate.row, candidate.upstream);
      summary.advanced += 1;
      if (candidate.imports) imports += 1;
      if (next.status === "completed") summary.completed += 1;
      if (next.status === "failed") summary.failed += 1;
    } catch (error) {
      summary.errors.push({ jobId: candidate.row.id, code: durableFailure(error).code });
    }
  }
  return summary;
}
