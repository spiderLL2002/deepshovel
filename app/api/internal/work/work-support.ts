/** Shared helpers for durable background work handlers. */
import {
  ApiError,
  durableFailure,
  getCompilerConfiguration,
} from "@/lib/server/evidence-store";
import { RetrievalClient } from "@/lib/server/retrieval-client";
import { failWorkAttempt, type WorkItem } from "@/lib/server/work-queue";

export type WorkPassSummary = {
  claimed: number;
  succeeded: number;
  deferred: number;
  retried: number;
  failed: number;
  cancelled: number;
};

export function emptyPass(): WorkPassSummary {
  return { claimed: 0, succeeded: 0, deferred: 0, retried: 0, failed: 0, cancelled: 0 };
}

export async function retrievalClient() {
  const configuration = await getCompilerConfiguration();
  if (!configuration.sharedSecret) {
    throw new ApiError(503, "RETRIEVAL_NOT_CONFIGURED", "Background retrieval work requires the shared Compiler credential", { retryable: true });
  }
  return new RetrievalClient({ ...configuration, sharedSecret: configuration.sharedSecret });
}

export async function recordFailure(
  DB: D1Database,
  item: WorkItem,
  error: unknown,
  summary: WorkPassSummary,
  progress?: Record<string, unknown>,
) {
  const cause = durableFailure(error);
  const attempts = item.attempts + 1;
  await failWorkAttempt(DB, item, cause, progress);
  if (!cause.retryable || attempts >= item.maxAttempts) summary.failed += 1;
  else summary.retried += 1;
}
