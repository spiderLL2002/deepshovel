import {
  ACCEPTED_ARTIFACT_MIME_TYPES,
  ARTIFACT_EXTENSIONS_BY_MIME,
} from "../artifacts/media-types.ts";

// Shared Cloudflare boundary helpers. Domain rules live in lib/evidence-graph.
export const PROJECT_ID = "DEEPSHOVEL-PROJECT";
export const MAX_ARTIFACT_BYTES = 50 * 1024 * 1024;
export const MAX_JSON_BYTES = 8 * 1024 * 1024;

export type Bindings = {
  DB: D1Database;
  BUCKET: R2Bucket;
  COMPILER_BASE_URL?: string;
  COMPILER_SHARED_SECRET?: string;
};

export class ApiError extends Error {
  public readonly status: number;
  public readonly code: string;
  public readonly details?: unknown;

  constructor(
    status: number,
    code: string,
    message: string,
    details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export async function getBindings(): Promise<Bindings> {
  const { env } = await import("cloudflare:workers");
  const bindings = env as unknown as Partial<Bindings>;
  if (!bindings.DB) throw new ApiError(503, "DATABASE_UNAVAILABLE", "Evidence database binding is unavailable");
  if (!bindings.BUCKET) throw new ApiError(503, "OBJECT_STORAGE_UNAVAILABLE", "Source object storage binding is unavailable");
  return bindings as Bindings;
}

export async function getCompilerConfiguration() {
  const { env } = await import("cloudflare:workers");
  const bindings = env as unknown as Partial<Bindings>;
  const baseUrl = bindings.COMPILER_BASE_URL?.trim();
  if (!baseUrl) throw new ApiError(503, "COMPILER_NOT_CONFIGURED", "Compiler service is not configured");
  return {
    baseUrl,
    sharedSecret: bindings.COMPILER_SHARED_SECRET?.trim() || undefined,
  };
}

/** Sites injects this header at the private access boundary. */
export function getActor(request: Request) {
  return request.headers.get("cf-access-authenticated-user-email")
    ?? request.headers.get("oai-authenticated-user-email")
    ?? "owner@deepshovel.local";
}

/** Same-origin is a CSRF check; actor identity still comes from the Sites boundary. */
export function assertSameOrigin(request: Request) {
  const fetchSite = request.headers.get("sec-fetch-site")?.trim().toLowerCase();
  if (fetchSite === "cross-site") {
    throw new ApiError(403, "CROSS_ORIGIN_WRITE_REJECTED", "Cross-origin write rejected");
  }
  const origin = request.headers.get("origin");
  if (!origin) return;
  if (new URL(origin).origin === new URL(request.url).origin) return;
  // Cloudflare Tunnel terminates the public origin before forwarding to the
  // loopback Worker URL. Sec-Fetch-Site is browser-controlled and preserves
  // the original page/request relationship across that reverse proxy.
  if (fetchSite === "same-origin") return;
  throw new ApiError(403, "CROSS_ORIGIN_WRITE_REJECTED", "Cross-origin write rejected");
}

const LOOPBACK_ADDRESSES = new Set(["127.0.0.1", "::1", "::ffff:127.0.0.1"]);
const LOOPBACK_HOSTNAMES = new Set(["127.0.0.1", "localhost", "[::1]"]);

function isLoopbackHost(value: string) {
  try {
    return LOOPBACK_HOSTNAMES.has(new URL(`http://${value}`).hostname);
  } catch {
    return false;
  }
}

/**
 * True only for a request made directly on this Mac, never for public traffic.
 *
 * Every request that crosses Cloudflare carries edge headers (`cf-ray`,
 * `cdn-loop`) and the real client address in `cf-connecting-ip`. For a direct
 * loopback connection, workerd/miniflare fills `cf-connecting-ip` with the TCP
 * peer address. The host must also be a loopback name, which rejects
 * DNS-rebinding pages. The Tunnel additionally answers every `/api/internal`
 * path with 404, so this check is a second, independent layer.
 */
export function isLocalMaintainerRequest(request: Request) {
  const headers = request.headers;
  if (headers.has("cf-ray") || headers.has("cdn-loop") || headers.has("cf-visitor")) return false;
  const address = headers.get("cf-connecting-ip")?.trim();
  if (address === undefined || !LOOPBACK_ADDRESSES.has(address)) return false;
  const host = headers.get("host");
  if (host !== null && !isLoopbackHost(host)) return false;
  return isLoopbackHost(new URL(request.url).host);
}

/** Destructive maintenance (for example deleting a paper) is local-only. */
export function assertLocalMaintainer(request: Request) {
  if (!isLocalMaintainerRequest(request)) {
    throw new ApiError(
      403,
      "MAINTENANCE_LOCAL_ONLY",
      "This maintenance operation is only available on the server itself",
    );
  }
}

function constantTimeEqual(left: string, right: string) {
  const encoder = new TextEncoder();
  const a = encoder.encode(left);
  const b = encoder.encode(right);
  let difference = a.length ^ b.length;
  for (let index = 0; index < Math.max(a.length, b.length); index += 1) {
    difference |= (a[index] ?? 0) ^ (b[index] ?? 0);
  }
  return difference === 0;
}

/**
 * The local work pump: a loopback request that never crossed Cloudflare and
 * carries the service credential shared with the Compiler. Unlike interactive
 * maintenance it does not depend on the peer-address header, which some local
 * runtimes omit; the credential is the authority and the network checks are an
 * independent second layer (the Tunnel also answers /api/internal with 404).
 */
export async function assertInternalWorker(request: Request) {
  const headers = request.headers;
  const host = headers.get("host");
  if (headers.has("cf-ray") || headers.has("cdn-loop") || headers.has("cf-visitor")
    || (host !== null && !isLoopbackHost(host)) || !isLoopbackHost(new URL(request.url).host)) {
    throw new ApiError(403, "MAINTENANCE_LOCAL_ONLY", "Background work is only available on the server itself");
  }
  const { sharedSecret } = await getCompilerConfiguration();
  if (!sharedSecret) {
    throw new ApiError(503, "WORKER_NOT_CONFIGURED", "Background work requires the shared service credential", { retryable: true });
  }
  const authorization = headers.get("authorization") ?? "";
  const token = authorization.startsWith("Bearer ") ? authorization.slice(7) : "";
  if (!token || !constantTimeEqual(token, sharedSecret)) {
    throw new ApiError(401, "WORKER_UNAUTHORIZED", "Background work requires the service credential");
  }
}

export function sanitizeFileName(value: string) {
  return value.normalize("NFC").replace(/[\\/\u0000-\u001f\u007f]/g, "_").trim().slice(0, 180);
}

export function decodeArtifactNameHeader(value: string | null) {
  if (!value) return "";
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

export function normalizeMimeType(value: string) {
  return value.split(";", 1)[0].trim().toLowerCase();
}

function extensionOf(fileName: string) {
  const index = fileName.lastIndexOf(".");
  return index < 0 ? "" : fileName.slice(index + 1).toLowerCase();
}

function startsWith(bytes: Uint8Array, signature: number[]) {
  return signature.every((byte, index) => bytes[index] === byte);
}

function assertUtf8Text(bytes: Uint8Array) {
  let decoded: string;
  try {
    decoded = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    throw new ApiError(415, "INVALID_UTF8", "Text artifacts must be valid UTF-8");
  }
  if (/\u0000/.test(decoded) || /[\u0001-\u0008\u000b\u000c\u000e-\u001f]/.test(decoded)) {
    throw new ApiError(415, "BINARY_TEXT_MISMATCH", "Text artifact contains binary control bytes");
  }
}

/** Validate the declared media type against both the file name and actual bytes. */
export function validateArtifactBytes(fileName: string, mimeType: string, bytes: Uint8Array) {
  const safeName = sanitizeFileName(fileName);
  if (!safeName) throw new ApiError(400, "FILE_NAME_REQUIRED", "X-Artifact-Name is required");
  if (!ACCEPTED_ARTIFACT_MIME_TYPES.has(mimeType)) {
    throw new ApiError(415, "UNSUPPORTED_ARTIFACT_TYPE", "Unsupported scientific document type");
  }
  if (bytes.byteLength === 0) throw new ApiError(400, "ARTIFACT_BODY_REQUIRED", "Artifact body is required");
  if (bytes.byteLength > MAX_ARTIFACT_BYTES) {
    throw new ApiError(413, "ARTIFACT_TOO_LARGE", "Artifacts are limited to 50 MiB");
  }
  const allowedExtensions = ARTIFACT_EXTENSIONS_BY_MIME.get(mimeType);
  if (allowedExtensions && !allowedExtensions.has(extensionOf(safeName))) {
    throw new ApiError(415, "FILE_EXTENSION_MISMATCH", "File extension does not match Content-Type");
  }

  if (mimeType === "application/pdf" && !startsWith(bytes, [0x25, 0x50, 0x44, 0x46, 0x2d])) {
    throw new ApiError(415, "CONTENT_SIGNATURE_MISMATCH", "PDF signature does not match Content-Type");
  }
  if (["text/csv", "text/tab-separated-values", "text/plain"].includes(mimeType)) {
    const knownBinary = startsWith(bytes, [0x25, 0x50, 0x44, 0x46, 0x2d])
      || startsWith(bytes, [0x50, 0x4b, 0x03, 0x04])
      || startsWith(bytes, [0x89, 0x50, 0x4e, 0x47]);
    if (knownBinary) throw new ApiError(415, "CONTENT_SIGNATURE_MISMATCH", "Binary content does not match a text Content-Type");
    assertUtf8Text(bytes);
  }
  if ([
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ].includes(mimeType) && !startsWith(bytes, [0x50, 0x4b, 0x03, 0x04])) {
    throw new ApiError(415, "CONTENT_SIGNATURE_MISMATCH", "Office document signature does not match Content-Type");
  }
  if (mimeType === "image/png" && !startsWith(bytes, [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])) {
    throw new ApiError(415, "CONTENT_SIGNATURE_MISMATCH", "PNG signature does not match Content-Type");
  }
  if (mimeType === "image/jpeg" && !startsWith(bytes, [0xff, 0xd8, 0xff])) {
    throw new ApiError(415, "CONTENT_SIGNATURE_MISMATCH", "JPEG signature does not match Content-Type");
  }
  if (mimeType === "image/tiff"
    && !startsWith(bytes, [0x49, 0x49, 0x2a, 0x00])
    && !startsWith(bytes, [0x4d, 0x4d, 0x00, 0x2a])) {
    throw new ApiError(415, "CONTENT_SIGNATURE_MISMATCH", "TIFF signature does not match Content-Type");
  }
  return safeName;
}

export async function readBoundedBody(request: Request, maximumBytes = MAX_ARTIFACT_BYTES) {
  const declaredLength = request.headers.get("content-length");
  let expectedLength: number | null = null;
  if (declaredLength !== null) {
    const parsedLength = Number(declaredLength);
    if (!Number.isInteger(parsedLength) || parsedLength < 0) {
      throw new ApiError(400, "INVALID_CONTENT_LENGTH", "Invalid Content-Length");
    }
    if (parsedLength > maximumBytes) throw new ApiError(413, "ARTIFACT_TOO_LARGE", `Request body exceeds ${maximumBytes} bytes`);
    expectedLength = parsedLength;
  }
  if (!request.body) throw new ApiError(400, "ARTIFACT_BODY_REQUIRED", "Artifact body is required");
  const reader = request.body.getReader();
  const bytes = expectedLength === null ? null : new Uint8Array(expectedLength);
  const chunks: Uint8Array[] = [];
  let total = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    const nextTotal = total + value.byteLength;
    if (nextTotal > maximumBytes) {
      await reader.cancel();
      throw new ApiError(413, "ARTIFACT_TOO_LARGE", `Request body exceeds ${maximumBytes} bytes`);
    }
    if (bytes) {
      if (nextTotal > bytes.byteLength) {
        await reader.cancel();
        throw new ApiError(400, "CONTENT_LENGTH_MISMATCH", "Request body exceeds its declared Content-Length");
      }
      bytes.set(value, total);
    } else {
      chunks.push(value);
    }
    total = nextTotal;
  }
  if (bytes) {
    if (total !== bytes.byteLength) {
      throw new ApiError(400, "CONTENT_LENGTH_MISMATCH", "Request body does not match its declared Content-Length");
    }
    return bytes;
  }
  const combined = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    combined.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return combined;
}

export async function readJsonObject(request: Request, maximumBytes = MAX_JSON_BYTES): Promise<Record<string, unknown>> {
  const bytes = await readBoundedBody(request, maximumBytes);
  try {
    const value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new ApiError(400, "INVALID_JSON_OBJECT", "JSON request body must be an object");
    }
    return value as Record<string, unknown>;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(400, "INVALID_JSON", "Invalid JSON request payload");
  }
}

export async function sha256Hex(value: Uint8Array | string) {
  const input = typeof value === "string" ? new TextEncoder().encode(value) : value;
  const digestInput = input.buffer instanceof ArrayBuffer
    && input.byteOffset === 0
    && input.byteLength === input.buffer.byteLength
    ? input.buffer
    : input.slice().buffer;
  const digest = await crypto.subtle.digest("SHA-256", digestInput);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function normalizeIdempotencyKey(value: string | null | undefined, fallback: string) {
  const key = value?.trim() || fallback;
  if (key.length > 180 || !/^[A-Za-z0-9._:@/-]+$/.test(key)) {
    throw new ApiError(400, "INVALID_IDEMPOTENCY_KEY", "Idempotency key is invalid");
  }
  return key;
}

type StoredObjectResult = { object: R2Object; stored: boolean };
type ConditionalR2Bucket = Omit<R2Bucket, "put"> & {
  put(
    key: string,
    value: Uint8Array,
    options: {
      httpMetadata: { contentType: string };
      customMetadata: Record<string, string>;
      onlyIf: Headers;
    },
  ): Promise<R2Object | null>;
};

/** Atomically deduplicate by digest without sharing request-bound R2 objects across invocations. */
export async function putContentAddressedObject(
  bucket: R2Bucket,
  sha256: string,
  bytes: Uint8Array,
  mimeType: string,
): Promise<StoredObjectResult> {
  const key = `artifacts/${sha256}`;
  const existing = await bucket.head(key);
  if (existing) return { object: existing, stored: false };

  const object = await (bucket as ConditionalR2Bucket).put(key, bytes, {
    httpMetadata: { contentType: mimeType },
    customMetadata: { sha256 },
    onlyIf: new Headers({ "if-none-match": "*" }),
  });
  if (object) return { object, stored: true };

  // R2 is strongly consistent: a failed create-only write has a visible winner.
  const winner = await bucket.head(key);
  if (winner) return { object: winner, stored: false };
  throw new ApiError(
    503,
    "OBJECT_STORAGE_WRITE_CONFLICT",
    "Object storage write conflicted; retry with the same content digest.",
    { retryable: true },
  );
}

export function apiError(code: string, message: string, status = 400, details?: unknown) {
  return Response.json({ ok: false, error: { code, message, ...(details === undefined ? {} : { details }) } }, { status });
}

function errorFingerprint(error: unknown) {
  const messages: string[] = [];
  const seen = new Set<object>();
  let current = error;
  for (let depth = 0; depth < 5 && current !== null && current !== undefined; depth += 1) {
    if (typeof current === "string") {
      messages.push(current);
      break;
    }
    if (typeof current !== "object" || seen.has(current)) break;
    seen.add(current);
    const record = current as { message?: unknown; cause?: unknown };
    if (typeof record.message === "string") messages.push(record.message);
    current = record.cause;
  }
  return messages.join("\n").toLowerCase();
}

function includesAny(value: string, markers: readonly string[]) {
  return markers.some((marker) => value.includes(marker));
}

function isRetryableDetails(details: unknown) {
  return Boolean(details && typeof details === "object"
    && "retryable" in details
    && (details as { retryable?: unknown }).retryable === true);
}

function storeErrorResponse(
  code: string,
  message: string,
  status: number,
  details: unknown,
  retryAfterSeconds?: number,
) {
  const response = apiError(code, message, status, details);
  response.headers.set("cache-control", "no-store");
  if (retryAfterSeconds !== undefined) {
    response.headers.set("retry-after", String(retryAfterSeconds));
  }
  return response;
}

function errorKind(error: unknown) {
  if (!(error instanceof Error)) return typeof error;
  return /^[A-Za-z][A-Za-z0-9]*Error$/.test(error.name) ? error.name : "Error";
}

function logStoreError(code: string, error: unknown, transient = false) {
  const context = { code, errorKind: errorKind(error) };
  if (transient) console.warn("DeepShovel evidence-store transient", context);
  else console.error("DeepShovel evidence-store error", context);
}

const STORE_BUSY_MARKERS = [
  "sqlite_busy",
  "sqlite_locked",
  "database is locked",
  "database table is locked",
  "d1 db is overloaded",
  "requests queued for too long",
  "too many requests queued",
  "d1 db storage operation exceeded timeout",
] as const;

const STORE_UNAVAILABLE_MARKERS = [
  "binding is unavailable",
  "d1 db reset because its code was updated",
  "internal error while starting up d1 db storage caused object to be reset",
  "network connection lost",
  "internal error in d1 db storage caused object to be reset",
  "cannot resolve d1 db due to transient issue on remote node",
] as const;

export type DurableFailure = { code: string; message: string; retryable: boolean };

/**
 * Classify a failure of background work as a value-free, durable cause.
 *
 * Only failures that can succeed later without a code or data change are
 * retryable: transient store contention, unavailable services and typed
 * retryable API errors. Validation, provenance, size and schema failures and
 * programming errors are final; retrying them only repeats the same work.
 * Unknown errors stay retryable, but callers bound them by an attempt budget.
 */
export function durableFailure(error: unknown): DurableFailure {
  if (error instanceof ApiError) {
    const details = error.details && typeof error.details === "object" && !Array.isArray(error.details)
      ? error.details as { retryable?: unknown }
      : {};
    const retryable = typeof details.retryable === "boolean"
      ? details.retryable
      : [408, 429, 503, 504].includes(error.status);
    return { code: error.code, message: error.message.slice(0, 4000), retryable };
  }
  const fingerprint = errorFingerprint(error);
  if (includesAny(fingerprint, ["string or blob too big", "sqlite_toobig", "too large"])) {
    return { code: "STORE_VALUE_TOO_LARGE", message: "A stored value exceeds the database size limit", retryable: false };
  }
  if (includesAny(fingerprint, STORE_BUSY_MARKERS)) {
    return { code: "STORE_BUSY", message: "Evidence store was temporarily busy", retryable: true };
  }
  if (includesAny(fingerprint, STORE_UNAVAILABLE_MARKERS)) {
    return { code: "STORE_UNAVAILABLE", message: "Evidence store was temporarily unavailable", retryable: true };
  }
  if (fingerprint.includes("unique constraint failed")) {
    // A concurrent writer won; the next attempt takes the idempotent path.
    return { code: "CONCURRENT_WRITE_CONFLICT", message: "A concurrent write conflicted", retryable: true };
  }
  if (fingerprint.includes("constraint failed")) {
    return { code: "STORE_CONSTRAINT_VIOLATION", message: "A database constraint rejected the write", retryable: false };
  }
  if (includesAny(fingerprint, ["no such table", "no column named", "no such column"])) {
    return { code: "STORE_SCHEMA_INVALID", message: "The evidence store schema is not initialized", retryable: false };
  }
  if (error instanceof TypeError || error instanceof RangeError || error instanceof SyntaxError) {
    return { code: "INTERNAL_ERROR", message: "Unexpected background processing failure", retryable: false };
  }
  return { code: "INTERNAL_ERROR", message: "Unexpected background processing failure", retryable: true };
}

export function safeError(error: unknown) {
  if (error instanceof ApiError) {
    return storeErrorResponse(
      error.code,
      error.message,
      error.status,
      error.details,
      isRetryableDetails(error.details) ? 1 : undefined,
    );
  }
  if (error instanceof SyntaxError) return apiError("INVALID_JSON", "Invalid JSON request payload.");

  const fingerprint = errorFingerprint(error);
  if (includesAny(fingerprint, ["no such table", "no column named"])) {
    logStoreError("STORE_INITIALIZING", error, true);
    return storeErrorResponse(
      "STORE_INITIALIZING",
      "Evidence store is initializing. Retry shortly.",
      503,
      { retryable: true },
      2,
    );
  }
  if (includesAny(fingerprint, STORE_BUSY_MARKERS)) {
    logStoreError("STORE_BUSY", error, true);
    return storeErrorResponse(
      "STORE_BUSY",
      "Evidence store is temporarily busy. Retry with the same idempotency key.",
      503,
      { retryable: true },
      1,
    );
  }
  if (includesAny(fingerprint, STORE_UNAVAILABLE_MARKERS)) {
    logStoreError("STORE_UNAVAILABLE", error, true);
    return storeErrorResponse(
      "STORE_UNAVAILABLE",
      "Evidence store is temporarily unavailable. Retry shortly.",
      503,
      { retryable: true },
      2,
    );
  }
  if (fingerprint.includes("unique constraint failed")) {
    return storeErrorResponse(
      "CONCURRENT_WRITE_CONFLICT",
      "A concurrent write conflicted; retry with the same idempotency key.",
      409,
      { retryable: true },
      1,
    );
  }
  if (fingerprint.includes("cannot perform i/o on behalf of a different request")) {
    logStoreError("STORE_REQUEST_CONTEXT_INVALID", error);
    return storeErrorResponse(
      "STORE_REQUEST_CONTEXT_INVALID",
      "Evidence store request context was invalid.",
      500,
      { retryable: false },
    );
  }
  logStoreError("INTERNAL_ERROR", error);
  return storeErrorResponse(
    "INTERNAL_ERROR",
    "Unexpected evidence-store error.",
    500,
    { retryable: false },
  );
}

export function artifactTypeForMime(contentType: string) {
  if (contentType.startsWith("image/")) return "figure";
  if (contentType === "text/csv" || contentType === "text/tab-separated-values") return "table";
  if (contentType.includes("spreadsheet")) return "dataset";
  return "paper";
}
