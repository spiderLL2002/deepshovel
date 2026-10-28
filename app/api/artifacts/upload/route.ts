import {
  ApiError, assertSameOrigin, getActor, getBindings, MAX_ARTIFACT_BYTES,
  readBoundedBody, safeError, sha256Hex,
} from "@/lib/server/evidence-store";
import { UPLOAD_CHUNK_BYTES } from "@/lib/artifacts/upload-limits.mjs";
import { POST as importArtifact } from "../route.ts";

type UploadBucket = R2Bucket & {
  delete(keys: string | string[]): Promise<void>;
  list(options: { prefix: string; cursor?: string; limit?: number }): Promise<{
    objects: R2Object[]; truncated: boolean; cursor?: string;
  }>;
};
const PREFIX = "upload-parts/";
const RETENTION_MS = 24 * 60 * 60 * 1000;
const CLEANUP_STATE_KEY = "upload-maintenance/expired-parts-v1.json";
const CLEANUP_PAGE_SIZE = 256;
const CLEANUP_COOLDOWN_MS = 60_000;
const CLEANUP_LEASE_MS = 60_000;
type CleanupState = { cursor: string | null; nextRun: number; leaseUntil: number };
type ConditionalCleanupBucket = {
  put(key: string, value: string, options: { onlyIf: Headers }): Promise<R2Object | null>;
};

async function context(request: Request) {
  assertSameOrigin(request);
  const id = request.headers.get("x-upload-id") ?? "";
  const digest = request.headers.get("x-upload-sha256") ?? "";
  const size = Number(request.headers.get("x-upload-size"));
  if (!/^[a-f0-9-]{36}$/.test(id) || !/^[a-f0-9]{64}$/.test(digest)
    || !Number.isSafeInteger(size) || size < 1 || size > MAX_ARTIFACT_BYTES) {
    throw new ApiError(400, "INVALID_UPLOAD", "Invalid upload identity, digest or size");
  }
  const actor = await sha256Hex(getActor(request));
  const prefix = `${PREFIX}${actor}/${id}/`;
  const bucket = (await getBindings()).BUCKET as UploadBucket;
  const signature = await sha256Hex(JSON.stringify([
    digest, size, request.headers.get("content-type"),
    request.headers.get("x-artifact-name"), request.headers.get("x-artifact-id"),
  ]));
  return { bucket, prefix, digest, size, signature, count: Math.ceil(size / UPLOAD_CHUNK_BYTES) };
}

async function clearExpired(bucket: UploadBucket) {
  // State lives outside the scanned prefix. R2 conditional writes coordinate
  // isolates; never retain a bucket, response, or Promise across requests.
  try {
    const now = Date.now();
    const prior = await bucket.get(CLEANUP_STATE_KEY);
    let state: CleanupState = { cursor: null, nextRun: 0, leaseUntil: 0 };
    if (prior && prior.size <= 4096) {
      try {
        const saved: unknown = JSON.parse(await prior.text());
        if (saved && typeof saved === "object" && "cursor" in saved
          && (saved.cursor === null || typeof saved.cursor === "string")
          && "nextRun" in saved && typeof saved.nextRun === "number" && Number.isFinite(saved.nextRun)
          && "leaseUntil" in saved && typeof saved.leaseUntil === "number" && Number.isFinite(saved.leaseUntil)) {
          state = { cursor: saved.cursor, nextRun: saved.nextRun, leaseUntil: saved.leaseUntil };
        }
      } catch { /* Invalid maintenance metadata restarts a bounded sweep. */ }
    }
    if (state.nextRun > now || state.leaseUntil > now) return;
    const conditional = bucket as unknown as ConditionalCleanupBucket;
    const lease = await conditional.put(CLEANUP_STATE_KEY, JSON.stringify({
      ...state, leaseUntil: now + CLEANUP_LEASE_MS, owner: crypto.randomUUID(),
    }), { onlyIf: new Headers(prior ? { "if-match": prior.httpEtag } : { "if-none-match": "*" }) });
    if (!lease) return;
    let cursor = state.cursor;
    try {
      let page;
      try {
        page = await bucket.list({ prefix: PREFIX, limit: CLEANUP_PAGE_SIZE,
          ...(cursor ? { cursor } : {}) });
      } catch (error) {
        // A stale provider cursor must not permanently wedge maintenance.
        cursor = null;
        throw error;
      }
      const expired = page.objects.filter((object) => object.key.startsWith(PREFIX)
        && object.uploaded && object.uploaded.getTime() < now - RETENTION_MS)
        .map((object) => object.key);
      if (expired.length) await bucket.delete(expired);
      cursor = page.truncated ? page.cursor ?? null : null;
    } finally {
      // Failed deletes retry the same page; a crashed isolate retries after its
      // lease expires. A former owner cannot overwrite a successor's progress.
      await conditional.put(CLEANUP_STATE_KEY, JSON.stringify({
        cursor, nextRun: Date.now() + CLEANUP_COOLDOWN_MS, leaseUntil: 0,
      }), { onlyIf: new Headers({ "if-match": lease.httpEtag }) });
    }
  } catch {
    // Best-effort maintenance never turns a valid upload into a failed request.
  }
}

export async function PUT(request: Request) {
  try {
    const upload = await context(request);
    const rawIndex = request.headers.get("x-upload-index");
    const index = Number(rawIndex);
    if (rawIndex === null || !Number.isSafeInteger(index) || index < 0 || index >= upload.count) {
      throw new ApiError(400, "INVALID_UPLOAD_PART", "Invalid upload part index");
    }
    const bytes = await readBoundedBody(request, UPLOAD_CHUNK_BYTES);
    const expected = Math.min(UPLOAD_CHUNK_BYTES, upload.size - index * UPLOAD_CHUNK_BYTES);
    if (bytes.length !== expected) throw new ApiError(400, "UPLOAD_PART_SIZE_MISMATCH", "Upload part size does not match");
    if (index === 0) await clearExpired(upload.bucket);
    await upload.bucket.put(`${upload.prefix}${index}`, bytes, {
      customMetadata: { signature: upload.signature },
    });
    return Response.json({ ok: true, index });
  } catch (error) { return safeError(error); }
}

export async function POST(request: Request) {
  try {
    const upload = await context(request);
    const receiptKey = `${upload.prefix}receipt`;
    const cached = await upload.bucket.get(receiptKey);
    if (cached) {
      if (cached.customMetadata?.signature !== upload.signature) {
        throw new ApiError(409, "UPLOAD_ID_REUSED", "Upload identity was reused for another file");
      }
      return new Response(await cached.text(), { headers: { "content-type": "application/json" } });
    }
    const bytes = new Uint8Array(upload.size);
    const keys: string[] = [];
    for (let index = 0; index < upload.count; index += 1) {
      const key = `${upload.prefix}${index}`;
      const part = await upload.bucket.get(key);
      const expected = Math.min(UPLOAD_CHUNK_BYTES, upload.size - index * UPLOAD_CHUNK_BYTES);
      if (!part || part.size !== expected || part.customMetadata?.signature !== upload.signature) {
        throw new ApiError(409, "UPLOAD_INCOMPLETE", "Upload is incomplete or contains a mismatched part");
      }
      bytes.set(new Uint8Array(await part.arrayBuffer()), index * UPLOAD_CHUNK_BYTES);
      keys.push(key);
    }
    if (await sha256Hex(bytes) !== upload.digest) {
      throw new ApiError(422, "UPLOAD_DIGEST_MISMATCH", "Uploaded bytes do not match the original file digest");
    }
    const headers = new Headers(request.headers);
    headers.set("content-length", String(bytes.length));
    headers.set("idempotency-key", `chunk-upload:${upload.prefix}`);
    const response = await importArtifact(new Request(new URL("/api/artifacts", request.url), {
      method: "POST", headers, body: bytes,
    }));
    if (!response.ok) return response;
    const body = await response.text();
    // Cache the small receipt before removing parts so a lost response is retryable.
    await upload.bucket.put(receiptKey, body, { customMetadata: { signature: upload.signature } });
    await upload.bucket.delete(keys);
    return new Response(body, { status: response.status, headers: { "content-type": "application/json" } });
  } catch (error) { return safeError(error); }
}

export async function DELETE(request: Request) {
  try {
    const upload = await context(request);
    await upload.bucket.delete(Array.from({ length: upload.count }, (_, index) => `${upload.prefix}${index}`));
    return Response.json({ ok: true });
  } catch (error) { return safeError(error); }
}
