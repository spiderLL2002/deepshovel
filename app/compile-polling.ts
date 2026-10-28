import type { CompileJobDto } from "./workspace-types.ts";
import { asRecord, compileResponseError, readJson } from "./workspace-types.ts";

const MAX_SUBMISSION_ATTEMPTS = 3;
export const MAX_COMPILE_RUN_ATTEMPTS = 3;
const RETRYABLE_SUBMISSION_STATUSES = new Set([408, 425, 429]);

/** Serialize lightweight gateway submissions and invalidate work that has not started yet. */
export class CompileSubmissionQueue {
  private currentGeneration = 0;
  private tail: Promise<void> = Promise.resolve();
  private tasks = new Map<string, { generation: number; started: boolean; cancelled: boolean }>();

  generation() {
    return this.currentGeneration;
  }

  isCurrent(generation: number) {
    return generation === this.currentGeneration;
  }

  isTaskCurrent(generation: number, taskId: string) {
    const task = this.tasks.get(taskId);
    return this.isCurrent(generation)
      && task?.generation === generation
      && !task.cancelled;
  }

  cancel(taskId: string) {
    const task = this.tasks.get(taskId);
    if (!task || task.cancelled) return null;
    task.cancelled = true;
    return task.started ? "running" as const : "queued" as const;
  }

  cancelPending() {
    this.currentGeneration += 1;
    const cancelled = [...this.tasks.entries()].map(([id, task]) => {
      task.cancelled = true;
      return { id, started: task.started };
    });
    return cancelled;
  }

  run<T>(generation: number, taskId: string, operation: () => Promise<T>): Promise<T | undefined> {
    if (this.tasks.has(taskId)) throw new Error("Compile submission task ID is already queued");
    const task = { generation, started: false, cancelled: false };
    this.tasks.set(taskId, task);
    const scheduled = this.tail.then(async () => {
      if (!this.isTaskCurrent(generation, taskId)) {
        if (this.tasks.get(taskId) === task) this.tasks.delete(taskId);
        return undefined;
      }
      task.started = true;
      try {
        return await operation();
      } finally {
        if (this.tasks.get(taskId) === task) this.tasks.delete(taskId);
      }
    });
    this.tail = scheduled.then(() => undefined, () => undefined);
    return scheduled;
  }
}

type CompileSubmissionOptions = {
  idempotencyKey: string;
  request?: typeof fetch;
  sleep?: (milliseconds: number) => Promise<void>;
};

function submissionRetryDelay(attempt: number) {
  return 300 * (2 ** (attempt - 1));
}

function isRetryableSubmissionStatus(status: number) {
  return RETRYABLE_SUBMISSION_STATUSES.has(status) || status >= 500;
}

/** Submit once logically, retrying only transient transport failures with one stable key. */
export async function submitCompileJob(
  artifactVersionId: string,
  {
    idempotencyKey,
    request = fetch,
    sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
  }: CompileSubmissionOptions,
) {
  const init: RequestInit = {
    method: "POST",
    headers: { "content-type": "application/json", "idempotency-key": idempotencyKey },
    body: JSON.stringify({ artifact_version_id: artifactVersionId }),
  };

  for (let attempt = 1; attempt <= MAX_SUBMISSION_ATTEMPTS; attempt += 1) {
    let response: Response;
    try {
      response = await request("/api/compile", init);
    } catch (cause) {
      if (attempt === MAX_SUBMISSION_ATTEMPTS) throw cause;
      await sleep(submissionRetryDelay(attempt));
      continue;
    }

    const payload = await readJson(response);
    if (!response.ok) {
      const error = new Error(compileResponseError(payload, "Compile 任务提交失败"));
      if (!isRetryableSubmissionStatus(response.status) || attempt === MAX_SUBMISSION_ATTEMPTS) {
        throw error;
      }
      await sleep(submissionRetryDelay(attempt));
      continue;
    }

    const job = asRecord(payload).job as CompileJobDto | undefined;
    if (!job?.id || job.artifactVersionId !== artifactVersionId) {
      throw new Error("Compiler 返回的任务与当前文献版本不一致，请重新提交 Compile");
    }
    return job;
  }

  throw new Error("Compile 任务提交失败");
}

function isActive(job: CompileJobDto) {
  return job.status === "queued" || job.status === "running";
}

export function nextCompileRunAttempt(job: CompileJobDto, currentAttempt: number) {
  const cancelled = job.error?.code === "COMPILE_CANCELLED" || job.error?.code === "JOB_CANCELLED";
  if (job.status !== "failed" || cancelled || job.error?.retryable === false
    || currentAttempt >= MAX_COMPILE_RUN_ATTEMPTS) return null;
  return currentAttempt + 1;
}

/** Keep visible task state aligned when submission immediately replays a terminal job. */
export function compileActivityStatus(job: CompileJobDto) {
  const active = isActive(job);
  const cancelled = job.error?.code === "COMPILE_CANCELLED" || job.error?.code === "JOB_CANCELLED";
  return {
    state: cancelled ? "cancelled" as const
      : job.status === "failed" ? "failed" as const
        : job.status === "completed" ? "done" as const
          : "working" as const,
    cancellable: active && job.stage !== "importing",
    progress: job.progress,
    stage: job.stage === "completed" || job.stage === "failed" ? undefined : job.stage,
  };
}
