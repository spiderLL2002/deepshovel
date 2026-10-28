import { ApiError, getBindings, PROJECT_ID, safeError } from "@/lib/server/evidence-store";

type RouteContext = { params: Promise<{ sha256: string }> };
type ArtifactMetadata = { fileName: string; mimeType: string; objectKey: string };
type ByteRange = { start: number; end: number; length: number };
type RangedR2Bucket = {
  get(key: string, options: { range: { offset: number; length: number } }): Promise<{ body: ReadableStream<Uint8Array> } | null>;
};

const SHA256_RE = /^[a-f0-9]{64}$/;

class InvalidByteRange extends Error {}

function encodedFileName(fileName: string) {
  return encodeURIComponent(fileName).replace(/[!'()*]/g, (character) =>
    `%${character.charCodeAt(0).toString(16).toUpperCase()}`,
  );
}

function asciiFileName(fileName: string) {
  return fileName
    .normalize("NFKD")
    .replace(/[^\x20-\x7e]/g, "_")
    .replace(/["\\/]/g, "_")
    .trim()
    .slice(0, 180) || "artifact";
}

function contentDisposition(mode: "attachment" | "inline", fileName: string) {
  return `${mode}; filename="${asciiFileName(fileName)}"; filename*=UTF-8''${encodedFileName(fileName)}`;
}

/** Parse one RFC 9110 byte range. Multipart ranges are intentionally unsupported. */
function parseSingleByteRange(value: string, size: number): ByteRange {
  const match = /^bytes=(\d*)-(\d*)$/.exec(value);
  if (!match || (!match[1] && !match[2]) || !Number.isSafeInteger(size) || size <= 0) {
    throw new InvalidByteRange();
  }

  const total = BigInt(size);
  let start: bigint;
  let end: bigint;

  if (!match[1]) {
    const suffixLength = BigInt(match[2]);
    if (suffixLength <= BigInt(0)) throw new InvalidByteRange();
    start = suffixLength >= total ? BigInt(0) : total - suffixLength;
    end = total - BigInt(1);
  } else {
    start = BigInt(match[1]);
    if (start >= total) throw new InvalidByteRange();
    end = match[2] ? BigInt(match[2]) : total - BigInt(1);
    if (end < start) throw new InvalidByteRange();
    if (end >= total) end = total - BigInt(1);
  }

  const numericStart = Number(start);
  const numericEnd = Number(end);
  return { start: numericStart, end: numericEnd, length: numericEnd - numericStart + 1 };
}

function responseHeaders(
  metadata: ArtifactMetadata,
  sha256: string,
  etag: string,
  disposition: "attachment" | "inline",
) {
  return new Headers({
    "accept-ranges": "bytes",
    "cache-control": "private, no-store",
    "content-disposition": contentDisposition(disposition, metadata.fileName),
    // Native PDF viewers cannot load in a CSP-sandboxed document.
    "content-security-policy": disposition === "inline"
      ? "frame-ancestors 'self'"
      : "default-src 'none'; sandbox",
    "content-type": metadata.mimeType,
    "cross-origin-resource-policy": "same-origin",
    etag,
    "x-artifact-sha256": sha256,
    "x-content-type-options": "nosniff",
  });
}

function requestedDisposition(request: Request, mimeType: string) {
  const value = new URL(request.url).searchParams.get("disposition");
  if (value === null) return "attachment" as const;
  if (value !== "inline") {
    throw new ApiError(400, "INVALID_ARTIFACT_DISPOSITION", "Artifact disposition must be inline when specified");
  }
  if (mimeType !== "application/pdf") {
    throw new ApiError(415, "ARTIFACT_INLINE_UNSUPPORTED", "Inline artifact preview is currently limited to PDF files");
  }
  return "inline" as const;
}

export async function GET(request: Request, context: RouteContext) {
  try {
    const { sha256 } = await context.params;
    if (!SHA256_RE.test(sha256)) throw new ApiError(400, "INVALID_ARTIFACT_HASH", "Artifact hash must be 64 lowercase hexadecimal characters");
    const { DB, BUCKET } = await getBindings();
    const row = await DB.prepare(`SELECT v.original_file_name AS fileName,
      v.media_type AS mimeType, v.object_key AS objectKey
      FROM artifact_versions v JOIN artifacts a ON a.id = v.artifact_id
      WHERE a.project_id = ? AND v.sha256 = ? AND v.id = ? LIMIT 1`)
      .bind(PROJECT_ID, sha256, `artifact-version:sha256:${sha256}`)
      .first<ArtifactMetadata>();
    if (!row) throw new ApiError(404, "ARTIFACT_NOT_FOUND", "Artifact metadata was not found");

    const disposition = requestedDisposition(request, row.mimeType);
    const rangeHeader = request.headers.get("range");
    if (rangeHeader !== null) {
      const metadata = await BUCKET.head(row.objectKey);
      if (!metadata) throw new ApiError(404, "ARTIFACT_BYTES_NOT_FOUND", "Artifact bytes were not found");
      const headers = responseHeaders(row, sha256, metadata.httpEtag, disposition);
      let range: ByteRange;
      try {
        range = parseSingleByteRange(rangeHeader, metadata.size);
      } catch (error) {
        if (!(error instanceof InvalidByteRange)) throw error;
        headers.set("content-range", `bytes */${metadata.size}`);
        return new Response(null, { status: 416, headers });
      }
      // The runtime supports ranged gets; the repository's intentionally minimal
      // ambient Worker declaration only describes the one-argument overload.
      const object = await (BUCKET as unknown as RangedR2Bucket).get(row.objectKey, {
        range: { offset: range.start, length: range.length },
      });
      if (!object) throw new ApiError(404, "ARTIFACT_BYTES_NOT_FOUND", "Artifact bytes were not found");
      headers.set("content-length", String(range.length));
      headers.set("content-range", `bytes ${range.start}-${range.end}/${metadata.size}`);
      return new Response(object.body, { status: 206, headers });
    }

    const object = await BUCKET.get(row.objectKey);
    if (!object) throw new ApiError(404, "ARTIFACT_BYTES_NOT_FOUND", "Artifact bytes were not found");
    const headers = responseHeaders(row, sha256, object.httpEtag, disposition);
    headers.set("content-length", String(object.size));
    return new Response(object.body, { headers });
  } catch (error) {
    return safeError(error);
  }
}
