function workerCount(itemCount: number, concurrency: number) {
  if (!Number.isInteger(concurrency) || concurrency < 1) {
    throw new RangeError("concurrency must be a positive integer");
  }
  return Math.min(itemCount, concurrency);
}

/** Run a bounded number of operations while retaining input order. */
export async function mapWithConcurrency<T, R>(
  values: readonly T[],
  concurrency: number,
  operation: (value: T, index: number) => Promise<R>,
) {
  const output = new Array<R>(values.length);
  let next = 0;
  await Promise.all(Array.from({ length: workerCount(values.length, concurrency) }, async () => {
    while (next < values.length) {
      const index = next++;
      output[index] = await operation(values[index], index);
    }
  }));
  return output;
}

/** Bounded Promise.allSettled equivalent, also retaining input order. */
export function settleWithConcurrency<T, R>(
  values: readonly T[],
  concurrency: number,
  operation: (value: T, index: number) => Promise<R>,
) {
  return mapWithConcurrency(values, concurrency, async (value, index): Promise<PromiseSettledResult<R>> => {
    try {
      return { status: "fulfilled", value: await operation(value, index) };
    } catch (reason) {
      return { status: "rejected", reason };
    }
  });
}
