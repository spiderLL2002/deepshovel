export type JsonResponseFailure = "invalid" | "too_large" | "empty" | "json";

/** Bound the stream before allocating or parsing JSON, including error responses. */
export async function readBoundedJson(
  response: Response,
  maximumBytes: number,
  failure: (kind: JsonResponseFailure, message: string) => Error,
): Promise<unknown> {
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  try {
    const length = response.headers.get("content-length");
    if (length !== null) {
      const parsed = Number(length);
      if (!Number.isSafeInteger(parsed) || parsed < 0) throw failure("invalid", "Invalid Content-Length");
      if (parsed > maximumBytes) throw failure("too_large", "Response exceeded the configured byte boundary");
    }
    const contentType = response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
    if (contentType !== "application/json" && !contentType?.endsWith("+json")) {
      throw failure("invalid", "Service must return JSON");
    }
    if (!response.body) throw failure("empty", "Service returned an empty response");
    reader = response.body.getReader();
    const chunks: Uint8Array[] = [];
    let total = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maximumBytes) throw failure("too_large", "Response exceeded the configured byte boundary");
      chunks.push(value);
    }
    const bytes = new Uint8Array(total);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    try {
      return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as unknown;
    } catch {
      throw failure("json", "Service returned invalid JSON");
    }
  } finally {
    try { await (reader ? reader.cancel() : response.body?.cancel()); } catch { /* Stream already closed. */ }
    reader?.releaseLock();
  }
}

/** Reject credentials in either JSON keys or values before exposing a response. */
export function containsCredential(value: unknown, credential: string) {
  const pending: unknown[] = [value];
  while (pending.length) {
    const current = pending.pop();
    if (typeof current === "string") {
      if (current.includes(credential)) return true;
    } else if (current && typeof current === "object") {
      for (const [key, child] of Object.entries(current)) {
        if (key.includes(credential)) return true;
        pending.push(child);
      }
    }
  }
  return false;
}
