export const KNOWLEDGE_GRAPH_BATCH_CONCURRENCY = 64;
export const KNOWLEDGE_GRAPH_MAX_RETRIES = 3;
export const KNOWLEDGE_GRAPH_BATCH_TRANSPORT_RETRIES = 20;

type RetryOptions = {
  signal?: AbortSignal;
  maxRetries?: number;
  retryDelayMs?: number;
  onRetry?: (retryCount: number, error: unknown) => void;
};

function waitForRetry(delayMs: number, signal?: AbortSignal) {
  if (signal?.aborted) return Promise.reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
  return new Promise<void>((resolve, reject) => {
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal?.reason ?? new DOMException("Aborted", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, delayMs);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export async function runWithRetries<T>(
  operation: (attempt: number) => Promise<T>,
  options: RetryOptions = {},
) {
  const maxRetries = options.maxRetries ?? KNOWLEDGE_GRAPH_MAX_RETRIES;
  const retryDelayMs = options.retryDelayMs ?? 1_000;
  if (!Number.isInteger(maxRetries) || maxRetries < 0) {
    throw new RangeError("maxRetries must be a non-negative integer");
  }
  if (!Number.isFinite(retryDelayMs) || retryDelayMs < 0) {
    throw new RangeError("retryDelayMs must be a non-negative number");
  }

  for (let retryCount = 0; ; retryCount += 1) {
    if (options.signal?.aborted) {
      throw options.signal.reason ?? new DOMException("Aborted", "AbortError");
    }
    try {
      return await operation(retryCount + 1);
    } catch (error) {
      if (options.signal?.aborted || retryCount >= maxRetries) throw error;
      const nextRetryCount = retryCount + 1;
      options.onRetry?.(nextRetryCount, error);
      await waitForRetry(retryDelayMs * nextRetryCount, options.signal);
    }
  }
}

export async function runParallelWorkPool<T>(
  items: readonly T[],
  concurrency: number,
  worker: (item: T, index: number) => Promise<void>,
) {
  if (!Number.isInteger(concurrency) || concurrency < 1) {
    throw new RangeError("concurrency must be a positive integer");
  }
  let nextIndex = 0;
  const workerCount = Math.min(items.length, concurrency);
  await Promise.all(Array.from({ length: workerCount }, async () => {
    while (nextIndex < items.length) {
      const index = nextIndex;
      nextIndex += 1;
      await worker(items[index]!, index);
    }
  }));
}
