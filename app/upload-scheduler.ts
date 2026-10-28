import { UPLOAD_CHUNK_BYTES } from "../lib/artifacts/upload-limits.mjs";
import { inferArtifactMimeType } from "../lib/artifacts/media-types.ts";
import { readJson, responseError, type ArtifactVersionDto } from "./workspace-types.ts";

type PendingUpload = {
  signal: AbortSignal;
  serialKey: string | null;
  resolve: (release: () => void) => void;
  reject: (cause: unknown) => void;
  abort: () => void;
};

function cancelledUpload() {
  return new DOMException("Upload cancelled", "AbortError");
}

const MAX_UPLOAD_ATTEMPTS = 3;
const UPLOAD_RETRY_DELAYS_MS = [2_000, 5_000] as const;
const MAX_RETRY_AFTER_MS = 5_000;

function retryableUploadStatus(status: number) {
  return status === 408 || status === 425 || status === 429 || status >= 500;
}

function retryAfterMilliseconds(value: string | null) {
  if (!value) return 0;
  const requested = /^\d+$/.test(value)
    ? Number(value) * 1_000
    : Date.parse(value) - Date.now();
  if (!Number.isFinite(requested) || requested <= 0) return 0;
  return Math.min(requested, MAX_RETRY_AFTER_MS);
}

function uploadRetryDelay(delayMs: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(cancelledUpload());
      return;
    }
    const onAbort = () => {
      globalThis.clearTimeout(timer);
      signal.removeEventListener("abort", onAbort);
      reject(cancelledUpload());
    };
    const timer = globalThis.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, delayMs);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

export async function fetchUploadWithRetry(
  request: () => Promise<Response>,
  signal: AbortSignal,
  sleep: (delayMs: number, signal: AbortSignal) => Promise<void> = uploadRetryDelay,
): Promise<Response> {
  let lastError: unknown = new Error("Upload failed");
  for (let attempt = 1; attempt <= MAX_UPLOAD_ATTEMPTS; attempt += 1) {
    if (signal.aborted) throw cancelledUpload();
    let retryAfterMs = 0;
    try {
      const response = await request();
      if (response.ok || !retryableUploadStatus(response.status) || attempt === MAX_UPLOAD_ATTEMPTS) {
        return response;
      }
      lastError = new Error(`Upload failed with HTTP ${response.status}`);
      retryAfterMs = retryAfterMilliseconds(response.headers.get("retry-after"));
      await response.body?.cancel().catch(() => undefined);
    } catch (cause) {
      if (signal.aborted) throw cancelledUpload();
      lastError = cause;
      if (attempt === MAX_UPLOAD_ATTEMPTS) throw cause;
    }
    const baselineMs = UPLOAD_RETRY_DELAYS_MS[attempt - 1] ?? UPLOAD_RETRY_DELAYS_MS.at(-1) ?? 0;
    await sleep(Math.max(baselineMs, retryAfterMs), signal);
  }
  throw lastError;
}

/** Both upload entry points share one request identity across transient retries. */
export async function uploadArtifact(
  file: File,
  signal: AbortSignal,
  { artifactId, mediaType = inferArtifactMimeType(file.name, file.type), errorMessage = "文献入库失败" }: {
    artifactId?: string;
    mediaType?: string;
    errorMessage?: string;
  } = {},
): Promise<ArtifactVersionDto> {
  const idempotencyKey = crypto.randomUUID();
  const headers: Record<string, string> = {
    "content-type": mediaType,
    "x-artifact-name": encodeURIComponent(file.name.normalize("NFC")),
    ...(artifactId ? { "x-artifact-id": artifactId } : {}),
    "idempotency-key": idempotencyKey,
  };
  let response: Response;
  if (file.size <= UPLOAD_CHUNK_BYTES) {
    response = await fetchUploadWithRetry(() => fetch("/api/artifacts", {
      method: "POST", headers, body: file, signal,
    }), signal);
  } else {
    const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
    headers["x-upload-sha256"] = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
    headers["x-upload-id"] = idempotencyKey;
    headers["x-upload-size"] = String(file.size);
    let next = 0;
    let failed = false;
    const count = Math.ceil(file.size / UPLOAD_CHUNK_BYTES);
    const workers = await Promise.allSettled(Array.from({ length: Math.min(4, count) }, async () => {
      while (!failed && next < count) {
        const index = next++;
        try {
          const part = await fetchUploadWithRetry(() => fetch("/api/artifacts/upload", {
            method: "PUT", headers: { ...headers, "x-upload-index": String(index) },
            body: file.slice(index * UPLOAD_CHUNK_BYTES, (index + 1) * UPLOAD_CHUNK_BYTES), signal,
          }), signal);
          if (!part.ok) throw new Error(responseError(await readJson(part), errorMessage));
          await part.body?.cancel();
        } catch (error) { failed = true; throw error; }
      }
    }));
    try {
      const failure = workers.find((worker) => worker.status === "rejected");
      if (failure?.status === "rejected") throw failure.reason;
      response = await fetchUploadWithRetry(() => fetch("/api/artifacts/upload", {
        method: "POST", headers, signal,
      }), signal);
      if (!response.ok) throw new Error(responseError(await readJson(response), errorMessage));
    } catch (error) {
      await fetch("/api/artifacts/upload", {
        method: "DELETE", headers, signal: AbortSignal.timeout(10_000),
      }).catch(() => undefined);
      throw error;
    }
  }
  const payload = await readJson(response);
  if (!response.ok) throw new Error(responseError(payload, errorMessage));
  return (payload as { version: ArtifactVersionDto }).version;
}

export function consumeUploadSelection<T>(files: ArrayLike<T>, clearInput: () => void): T[] {
  const selected = Array.from(files);
  clearInput();
  return selected;
}

export class UploadScheduler {
  private active = 0;
  private readonly activeSerialKeys = new Set<string>();
  private readonly pending: PendingUpload[] = [];
  private readonly limit: number;

  constructor(limit: number) {
    if (!Number.isInteger(limit) || limit < 1) throw new RangeError("Upload concurrency must be a positive integer");
    this.limit = limit;
  }

  async run<T>(signal: AbortSignal, operation: () => Promise<T>, serialKey?: string): Promise<T> {
    const release = await this.acquire(signal, serialKey ?? null);
    try {
      if (signal.aborted) throw cancelledUpload();
      return await operation();
    } finally {
      release();
    }
  }

  private canStart(serialKey: string | null) {
    return this.active < this.limit && (serialKey === null || !this.activeSerialKeys.has(serialKey));
  }

  private reserve(serialKey: string | null) {
    this.active += 1;
    if (serialKey !== null) this.activeSerialKeys.add(serialKey);
    return this.releaseOnce(serialKey);
  }

  private acquire(signal: AbortSignal, serialKey: string | null): Promise<() => void> {
    if (signal.aborted) return Promise.reject(cancelledUpload());
    if (this.canStart(serialKey)) {
      return Promise.resolve(this.reserve(serialKey));
    }
    return new Promise((resolve, reject) => {
      const waiter: PendingUpload = {
        signal,
        serialKey,
        resolve,
        reject,
        abort: () => {
          const index = this.pending.indexOf(waiter);
          if (index >= 0) this.pending.splice(index, 1);
          reject(cancelledUpload());
        },
      };
      signal.addEventListener("abort", waiter.abort, { once: true });
      this.pending.push(waiter);
    });
  }

  private releaseOnce(serialKey: string | null) {
    let released = false;
    return () => {
      if (released) return;
      released = true;
      this.active -= 1;
      if (serialKey !== null) this.activeSerialKeys.delete(serialKey);
      this.startNext();
    };
  }

  private startNext() {
    while (this.pending.length && this.active < this.limit) {
      const index = this.pending.findIndex((waiter) => this.canStart(waiter.serialKey));
      if (index < 0) return;
      const [waiter] = this.pending.splice(index, 1);
      waiter.signal.removeEventListener("abort", waiter.abort);
      if (waiter.signal.aborted) {
        waiter.reject(cancelledUpload());
        continue;
      }
      waiter.resolve(this.reserve(waiter.serialKey));
    }
  }
}
