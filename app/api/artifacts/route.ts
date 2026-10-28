import {
  ApiError,
  artifactTypeForMime,
  assertSameOrigin,
  decodeArtifactNameHeader,
  getActor,
  getBindings,
  normalizeIdempotencyKey,
  normalizeMimeType,
  PROJECT_ID,
  putContentAddressedObject,
  readBoundedBody,
  safeError,
  sha256Hex,
  validateArtifactBytes,
} from "@/lib/server/evidence-store";

type ArtifactVersionRow = {
  artifactId: string;
  preferredName: string;
  artifactType: string;
  artifactCreatedAt: string;
  versionId: string;
  versionNumber: number;
  sha256: string;
  sourceLocator: string;
  mediaType: string;
  originalFileName: string;
  byteSize: number;
  objectKey: string;
  createdAt: string;
  clientRequestId: string;
  requestHash: string;
};

const ARTIFACT_ID_RE = /^artifact:[0-9a-f-]{36}$/;
const SELECT_VERSION = `SELECT
  a.id AS artifactId, a.preferred_name AS preferredName,
  a.artifact_type AS artifactType, a.created_at AS artifactCreatedAt,
  v.id AS versionId, v.version_number AS versionNumber, v.sha256,
  v.source_locator AS sourceLocator, v.media_type AS mediaType,
  v.original_file_name AS originalFileName, v.byte_size AS byteSize,
  v.object_key AS objectKey, v.created_at AS createdAt,
  v.client_request_id AS clientRequestId, v.request_hash AS requestHash
  FROM artifact_versions v JOIN artifacts a ON a.id = v.artifact_id`;

function versionReceipt(row: ArtifactVersionRow) {
  return {
    id: row.versionId,
    versionId: row.versionId,
    artifactId: row.artifactId,
    versionNumber: row.versionNumber,
    sha256: row.sha256,
    locator: row.sourceLocator,
    mediaType: row.mediaType,
    fileName: row.originalFileName,
    byteSize: row.byteSize,
    downloadUrl: `/api/artifacts/${row.sha256}`,
    createdAt: row.createdAt,
  };
}

function artifactReceipt(row: ArtifactVersionRow) {
  return {
    id: row.artifactId,
    name: row.preferredName,
    artifactType: row.artifactType,
    createdAt: row.artifactCreatedAt,
  };
}

async function liveVersionByDigest(DB: D1Database, sha256: string) {
  return DB.prepare(`${SELECT_VERSION}
    LEFT JOIN artifact_deletion_authorizations d ON d.artifact_id = a.id
    WHERE a.project_id = ? AND v.sha256 = ? AND d.artifact_id IS NULL
    ORDER BY a.created_at, v.version_number LIMIT 1`)
    .bind(PROJECT_ID, sha256).first<ArtifactVersionRow>();
}

async function removeObjectUnlessLiveReference(
  DB: D1Database,
  BUCKET: R2Bucket,
  sha256: string,
  bytes: Uint8Array,
  mediaType: string,
) {
  let live = await liveVersionByDigest(DB, sha256);
  if (live) return live;
  const deletableBucket = BUCKET as unknown as { delete(key: string): Promise<void> };
  await deletableBucket.delete(`artifacts/${sha256}`);
  // A same-digest upload can commit between the first lookup and R2 delete.
  // Recreate the content-addressed object if that happened.
  live = await liveVersionByDigest(DB, sha256);
  if (live) await putContentAddressedObject(BUCKET, sha256, bytes, live.mediaType || mediaType);
  return live;
}

async function ensureLiveVersionObject(
  DB: D1Database,
  BUCKET: R2Bucket,
  row: ArtifactVersionRow,
  bytes: Uint8Array,
) {
  await putContentAddressedObject(BUCKET, row.sha256, bytes, row.mediaType);
  const live = await liveVersionByDigest(DB, row.sha256);
  if (live?.versionId === row.versionId) return live;
  await removeObjectUnlessLiveReference(DB, BUCKET, row.sha256, bytes, row.mediaType);
  throw new ApiError(409, "ARTIFACT_DELETION_IN_PROGRESS", "Artifact deletion is in progress");
}

function assertDuplicateTarget(row: ArtifactVersionRow, targetArtifactId: string | null) {
  if (targetArtifactId && row.artifactId !== targetArtifactId) {
    throw new ApiError(
      409,
      "ARTIFACT_BYTES_ALREADY_STORED",
      "These bytes already belong to another document and cannot be added as a version here",
    );
  }
}

export async function GET() {
  try {
    const { DB } = await getBindings();
    const result = await DB.prepare(`${SELECT_VERSION}
      WHERE a.project_id = ? ORDER BY a.created_at DESC, v.version_number DESC LIMIT 500`)
      .bind(PROJECT_ID).all<ArtifactVersionRow>();
    const grouped = new Map<string, ReturnType<typeof artifactReceipt> & {
      versions: ReturnType<typeof versionReceipt>[];
    }>();
    for (const row of result.results) {
      const current = grouped.get(row.artifactId) ?? { ...artifactReceipt(row), versions: [] };
      current.versions.push(versionReceipt(row));
      grouped.set(row.artifactId, current);
    }
    return Response.json({ ok: true, artifacts: [...grouped.values()] });
  } catch (error) {
    return safeError(error);
  }
}

export async function POST(request: Request) {
  try {
    assertSameOrigin(request);
    const actor = getActor(request);
    const mediaType = normalizeMimeType(request.headers.get("content-type") ?? "");
    const suppliedName = decodeArtifactNameHeader(request.headers.get("x-artifact-name"));
    const targetArtifactId = request.headers.get("x-artifact-id")?.trim() || null;
    if (targetArtifactId && !ARTIFACT_ID_RE.test(targetArtifactId)) {
      throw new ApiError(400, "INVALID_ARTIFACT_ID", "Artifact ID is invalid");
    }

    const bytes = await readBoundedBody(request);
    const fileName = validateArtifactBytes(suppliedName, mediaType, bytes);
    const sha256 = await sha256Hex(bytes);
    const versionId = `artifact-version:sha256:${sha256}`;
    const objectKey = `artifacts/${sha256}`;
    const requestHash = await sha256Hex(JSON.stringify({ fileName, mediaType, sha256, targetArtifactId }));
    const idempotencyKey = normalizeIdempotencyKey(
      request.headers.get("idempotency-key"),
      `artifact-version:${requestHash}`,
    );
    const { DB, BUCKET } = await getBindings();

    if (targetArtifactId) {
      const target = await DB.prepare(`SELECT a.id,
        EXISTS(SELECT 1 FROM artifact_deletion_authorizations d
          WHERE d.artifact_id = a.id) AS deletionAuthorized
        FROM artifacts a WHERE a.project_id = ? AND a.id = ? LIMIT 1`)
        .bind(PROJECT_ID, targetArtifactId).first<{ id: string; deletionAuthorized: number }>();
      if (!target) throw new ApiError(404, "ARTIFACT_NOT_FOUND", "Target artifact was not found");
      if (target.deletionAuthorized) {
        throw new ApiError(409, "ARTIFACT_DELETION_IN_PROGRESS", "Artifact deletion is in progress");
      }
    }

    const replay = await DB.prepare(`${SELECT_VERSION}
      WHERE v.created_by = ? AND v.client_request_id = ? LIMIT 1`)
      .bind(actor, idempotencyKey).first<ArtifactVersionRow>();
    if (replay) {
      if (replay.requestHash !== requestHash) {
        throw new ApiError(409, "IDEMPOTENCY_KEY_REUSED", "Idempotency key was already used for different bytes or metadata");
      }
      const live = await ensureLiveVersionObject(DB, BUCKET, replay, bytes);
      return Response.json({ ok: true, artifact: artifactReceipt(live), version: versionReceipt(live), duplicate: true });
    }

    const duplicate = await liveVersionByDigest(DB, sha256);
    if (duplicate) {
      assertDuplicateTarget(duplicate, targetArtifactId);
      const live = await ensureLiveVersionObject(DB, BUCKET, duplicate, bytes);
      return Response.json({ ok: true, artifact: artifactReceipt(live), version: versionReceipt(live), duplicate: true });
    }

    let artifactId = targetArtifactId;
    let versionNumber = 1;
    let createArtifact = false;
    if (artifactId) {
      const latest = await DB.prepare(
        "SELECT COALESCE(MAX(version_number), 0) AS versionNumber FROM artifact_versions WHERE artifact_id = ?",
      ).bind(artifactId).first<{ versionNumber: number }>();
      versionNumber = Number(latest?.versionNumber ?? 0) + 1;
    } else {
      artifactId = `artifact:${crypto.randomUUID()}`;
      createArtifact = true;
    }

    const stored = await putContentAddressedObject(BUCKET, sha256, bytes, mediaType);
    const eventId = crypto.randomUUID();
    const artifactInsert = DB.prepare(`INSERT INTO artifacts
      (id, project_id, preferred_name, artifact_type, created_by)
      VALUES (?, ?, ?, ?, ?)`)
      .bind(artifactId, PROJECT_ID, fileName, artifactTypeForMime(mediaType), actor);
    const versionInsert = DB.prepare(`INSERT INTO artifact_versions
      (id, artifact_id, version_number, sha256, source_locator, media_type,
       original_file_name, byte_size, object_key, etag, client_request_id,
       request_hash, created_by)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
      .bind(versionId, artifactId, versionNumber, sha256, `upload://${encodeURIComponent(fileName)}`,
        mediaType, fileName, bytes.byteLength, objectKey, stored.object.etag,
        idempotencyKey, requestHash, actor);
    const eventInsert = DB.prepare(`INSERT INTO audit_events
      (id, project_id, event_type, entity_type, entity_id, actor, payload_json)
      VALUES (?, ?, 'artifact_version.stored', 'artifact_version', ?, ?, ?)`)
      .bind(eventId, PROJECT_ID, versionId, actor, JSON.stringify({ artifactId, versionNumber, sha256, mediaType, fileName }));

    try {
      await DB.batch(createArtifact ? [artifactInsert, versionInsert, eventInsert] : [versionInsert, eventInsert]);
    } catch (error) {
      const winner = await liveVersionByDigest(DB, sha256)
        ?? await removeObjectUnlessLiveReference(DB, BUCKET, sha256, bytes, mediaType);
      if (!winner) {
        if (targetArtifactId) {
          const target = await DB.prepare(`SELECT a.id,
            EXISTS(SELECT 1 FROM artifact_deletion_authorizations d
              WHERE d.artifact_id = a.id) AS deletionAuthorized
            FROM artifacts a WHERE a.project_id = ? AND a.id = ? LIMIT 1`)
            .bind(PROJECT_ID, targetArtifactId).first<{ id: string; deletionAuthorized: number }>();
          if (!target || target.deletionAuthorized) {
            throw new ApiError(409, "ARTIFACT_DELETION_IN_PROGRESS", "Artifact deletion is in progress");
          }
        }
        throw error;
      }
      assertDuplicateTarget(winner, targetArtifactId);
      const live = await ensureLiveVersionObject(DB, BUCKET, winner, bytes);
      return Response.json({ ok: true, artifact: artifactReceipt(live), version: versionReceipt(live), duplicate: true });
    }

    const now = new Date().toISOString();
    const row: ArtifactVersionRow = {
      artifactId,
      preferredName: fileName,
      artifactType: artifactTypeForMime(mediaType),
      artifactCreatedAt: now,
      versionId,
      versionNumber,
      sha256,
      sourceLocator: `upload://${encodeURIComponent(fileName)}`,
      mediaType,
      originalFileName: fileName,
      byteSize: bytes.byteLength,
      objectKey,
      createdAt: now,
      clientRequestId: idempotencyKey,
      requestHash,
    };
    const live = await ensureLiveVersionObject(DB, BUCKET, row, bytes);
    return Response.json({ ok: true, artifact: artifactReceipt(live), version: versionReceipt(live), duplicate: false }, { status: 201 });
  } catch (error) {
    return safeError(error);
  }
}

// Whole-Artifact deletion is local maintenance only: see app/api/internal/artifacts.
