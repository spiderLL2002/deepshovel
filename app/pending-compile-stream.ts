/** Explicit Compile-all reads one cursor page at a time; never retain the corpus. */
export async function streamPendingCompile<T>(options: {
  signal: AbortSignal;
  read: (cursor: string | null, signal: AbortSignal) => Promise<{ items: T[]; nextCursor: string | null }>;
  submit: (item: T) => Promise<void>;
  waitForCapacity: (signal: AbortSignal) => Promise<void>;
  onProgress: (submitted: number) => void;
}) {
  let cursor: string | null = null;
  let submitted = 0;
  do {
    options.signal.throwIfAborted();
    const page = await options.read(cursor, options.signal);
    options.signal.throwIfAborted();
    for (const item of page.items) {
      await options.waitForCapacity(options.signal);
      options.signal.throwIfAborted();
      await options.submit(item);
      submitted += 1;
      options.onProgress(submitted);
    }
    if (page.nextCursor && page.nextCursor === cursor) throw new Error("待 Compile 文献分页游标重复");
    cursor = page.nextCursor;
  } while (cursor);
  return submitted;
}
