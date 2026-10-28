import { canonicalStringify } from "../evidence-graph/index.ts";
import {
  parseDocumentExtraction,
  type DocumentExtraction,
} from "../document-extraction/index.ts";
import { ApiError, getBindings, PROJECT_ID, sha256Hex } from "./evidence-store.ts";

const MAX_DOCUMENT_EXTRACTION_BYTES = 20 * 1024 * 1024;
const DOCUMENT_EXTRACTION_GENERATOR_VERSION = "1.0.0";
const DOCUMENT_METADATA_PROFILE = "doi-registration";
const SHA256_RE = /^[a-f0-9]{64}$/u;

export type FrozenBibliographicStatus = "matched" | "not-found" | "not-applicable";

type PersistDocumentExtractionOptions = {
  bibliographicStatus: FrozenBibliographicStatus;
};

type ConditionalDocumentBucket = Omit<R2Bucket, "put"> & {
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

export function documentExtractionObjectKey(artifactSha256: string) {
  if (!SHA256_RE.test(artifactSha256)) throw new TypeError("Artifact SHA-256 is invalid");
  return `document-extractions/${artifactSha256}.json`;
}

async function hasLiveArtifactVersion(
  DB: D1Database,
  artifactVersionId: string,
  artifactSha256: string,
) {
  return Boolean(await DB.prepare(`SELECT 1 AS live
    FROM artifact_versions v JOIN artifacts a ON a.id = v.artifact_id
    LEFT JOIN artifact_deletion_authorizations d ON d.artifact_id = a.id
    WHERE a.project_id = ? AND v.id = ? AND v.sha256 = ?
      AND d.artifact_id IS NULL LIMIT 1`)
    .bind(PROJECT_ID, artifactVersionId, artifactSha256)
    .first<{ live: number }>());
}

async function decodeStoredExtraction(object: R2ObjectBody, artifactSha256: string) {
  if (object.size <= 0 || object.size > MAX_DOCUMENT_EXTRACTION_BYTES) {
    throw new ApiError(502, "DOCUMENT_EXTRACTION_SIZE_INVALID", "Stored document extraction has an invalid size");
  }
  const bytes = new Uint8Array(await object.arrayBuffer());
  if (bytes.byteLength !== object.size) {
    throw new ApiError(502, "DOCUMENT_EXTRACTION_SIZE_MISMATCH", "Stored document extraction size does not match its metadata");
  }
  const digest = await sha256Hex(bytes);
  const recordedDigest = object.customMetadata?.extractionSha256;
  if (!recordedDigest || recordedDigest !== digest) {
    throw new ApiError(502, "DOCUMENT_EXTRACTION_HASH_MISMATCH", "Stored document extraction failed its integrity check");
  }
  let decoded: unknown;
  try {
    decoded = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  } catch {
    throw new ApiError(502, "DOCUMENT_EXTRACTION_JSON_INVALID", "Stored document extraction is not valid UTF-8 JSON");
  }
  if (decoded && typeof decoded === "object" && !Array.isArray(decoded)) {
    const legacy = decoded as {
      metadata?: Record<string, unknown>;
      field_sources?: Record<string, unknown>;
    };
    if (legacy.metadata && legacy.field_sources
      && !Object.hasOwn(legacy.metadata, "article_number")
      && !Object.hasOwn(legacy.field_sources, "article_number")) {
      legacy.metadata.article_number = "";
      legacy.field_sources.article_number = "empty";
    }
  }
  let extraction: DocumentExtraction;
  try {
    extraction = parseDocumentExtraction(decoded);
  } catch (error) {
    throw new ApiError(
      502,
      "DOCUMENT_EXTRACTION_CONTRACT_INVALID",
      error instanceof Error ? error.message : "Stored document extraction is invalid",
    );
  }
  if (extraction.artifact_sha256 !== artifactSha256) {
    throw new ApiError(409, "DOCUMENT_EXTRACTION_PROVENANCE_MISMATCH", "Document extraction belongs to another artifact");
  }
  return extraction;
}

function isCurrentExtraction(object: R2Object) {
  const status = object.customMetadata?.bibliographicStatus;
  return object.customMetadata?.generatorVersion === DOCUMENT_EXTRACTION_GENERATOR_VERSION
    && object.customMetadata?.metadataProfile === DOCUMENT_METADATA_PROFILE
    && (status === "matched" || status === "not-found" || status === "not-applicable");
}

function extractionMetadata(
  extraction: DocumentExtraction,
  extractionSha256: string,
  options: PersistDocumentExtractionOptions,
) {
  return {
    artifactSha256: extraction.artifact_sha256,
    bibliographicStatus: options.bibliographicStatus,
    extractionSha256,
    generatorVersion: DOCUMENT_EXTRACTION_GENERATOR_VERSION,
    metadataProfile: DOCUMENT_METADATA_PROFILE,
    schemaVersion: extraction.schema_version,
  };
}

export async function loadDocumentExtraction(artifactSha256: string) {
  const { BUCKET } = await getBindings();
  const object = await BUCKET.get(documentExtractionObjectKey(artifactSha256));
  return object && isCurrentExtraction(object)
    ? decodeStoredExtraction(object, artifactSha256)
    : null;
}

/** Load a valid older projection as the source for an in-place profile refresh. */
export async function loadRefreshableDocumentExtraction(artifactSha256: string) {
  const { BUCKET } = await getBindings();
  const object = await BUCKET.get(documentExtractionObjectKey(artifactSha256));
  return object ? decodeStoredExtraction(object, artifactSha256) : null;
}

/** Freeze the first valid projection for one immutable ArtifactVersion. */
export async function persistDocumentExtraction(
  extraction: DocumentExtraction,
  options: PersistDocumentExtractionOptions,
) {
  const validated = parseDocumentExtraction(extraction);
  const canonical = canonicalStringify(validated);
  const bytes = new TextEncoder().encode(canonical);
  if (bytes.byteLength > MAX_DOCUMENT_EXTRACTION_BYTES) {
    throw new ApiError(413, "DOCUMENT_EXTRACTION_TOO_LARGE", "Document extraction exceeds the 20 MiB persistence boundary");
  }
  const extractionSha256 = await sha256Hex(bytes);
  const key = documentExtractionObjectKey(validated.artifact_sha256);
  const { DB, BUCKET } = await getBindings();
  if (!await hasLiveArtifactVersion(DB, validated.artifact_version_id, validated.artifact_sha256)) {
    throw new ApiError(409, "ARTIFACT_DELETION_IN_PROGRESS", "Artifact is unavailable or being deleted");
  }
  const existing = await BUCKET.get(key);
  if (existing) {
    if (!isCurrentExtraction(existing)) {
      if (!existing.httpEtag) {
        throw new ApiError(502, "DOCUMENT_EXTRACTION_ETAG_MISSING", "Stored document extraction cannot be refreshed safely");
      }
      const replaced = await (BUCKET as ConditionalDocumentBucket).put(key, bytes, {
        httpMetadata: { contentType: "application/json; charset=utf-8" },
        customMetadata: extractionMetadata(validated, extractionSha256, options),
        onlyIf: new Headers({ "if-match": existing.httpEtag }),
      });
      if (replaced) {
        if (await hasLiveArtifactVersion(DB, validated.artifact_version_id, validated.artifact_sha256)) {
          return { extraction: validated, stored: true as const, objectKey: key, extractionSha256 };
        }
        await (BUCKET as unknown as { delete(key: string): Promise<void> }).delete(key);
        throw new ApiError(409, "ARTIFACT_DELETION_IN_PROGRESS", "Artifact was deleted while its document extraction was being refreshed");
      }
      const winner = await BUCKET.get(key);
      if (!winner || !isCurrentExtraction(winner)) {
        throw new ApiError(503, "DOCUMENT_EXTRACTION_WRITE_CONFLICT", "Document extraction refresh conflicted; retry shortly", { retryable: true });
      }
      const frozen = await decodeStoredExtraction(winner, validated.artifact_sha256);
      if (canonicalStringify(frozen) !== canonical) {
        throw new ApiError(409, "DOCUMENT_EXTRACTION_ALREADY_FROZEN", "A different current extraction is already frozen for this artifact version");
      }
      if (!await hasLiveArtifactVersion(DB, validated.artifact_version_id, validated.artifact_sha256)) {
        throw new ApiError(409, "ARTIFACT_DELETION_IN_PROGRESS", "Artifact is unavailable or being deleted");
      }
      return { extraction: frozen, stored: false as const, objectKey: key, extractionSha256 };
    }
    const frozen = await decodeStoredExtraction(existing, validated.artifact_sha256);
    if (canonicalStringify(frozen) !== canonical) {
      throw new ApiError(409, "DOCUMENT_EXTRACTION_ALREADY_FROZEN", "A different extraction is already frozen for this artifact version");
    }
    if (!await hasLiveArtifactVersion(DB, validated.artifact_version_id, validated.artifact_sha256)) {
      throw new ApiError(409, "ARTIFACT_DELETION_IN_PROGRESS", "Artifact is unavailable or being deleted");
    }
    return { extraction: frozen, stored: false as const, objectKey: key, extractionSha256 };
  }

  const created = await (BUCKET as ConditionalDocumentBucket).put(key, bytes, {
    httpMetadata: { contentType: "application/json; charset=utf-8" },
    customMetadata: extractionMetadata(validated, extractionSha256, options),
    onlyIf: new Headers({ "if-none-match": "*" }),
  });
  if (created) {
    if (await hasLiveArtifactVersion(DB, validated.artifact_version_id, validated.artifact_sha256)) {
      return { extraction: validated, stored: true as const, objectKey: key, extractionSha256 };
    }
    await (BUCKET as unknown as { delete(key: string): Promise<void> }).delete(key);
    throw new ApiError(409, "ARTIFACT_DELETION_IN_PROGRESS", "Artifact was deleted while its document extraction was being stored");
  }

  const winner = await BUCKET.get(key);
  if (!winner) {
    throw new ApiError(503, "DOCUMENT_EXTRACTION_WRITE_CONFLICT", "Document extraction storage conflicted; retry shortly", { retryable: true });
  }
  const frozen = await decodeStoredExtraction(winner, validated.artifact_sha256);
  if (canonicalStringify(frozen) !== canonical) {
    throw new ApiError(409, "DOCUMENT_EXTRACTION_ALREADY_FROZEN", "A different extraction is already frozen for this artifact version");
  }
  if (!await hasLiveArtifactVersion(DB, validated.artifact_version_id, validated.artifact_sha256)) {
    throw new ApiError(409, "ARTIFACT_DELETION_IN_PROGRESS", "Artifact is unavailable or being deleted");
  }
  return { extraction: frozen, stored: false as const, objectKey: key, extractionSha256 };
}
