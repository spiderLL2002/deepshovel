import {
  CompilerClient,
  type CompilerArtifactContext,
  type CompilerJob,
} from "@/lib/server/compiler-client.ts";
import { parseDocumentExtraction } from "@/lib/document-extraction/index.ts";
import {
  ApiError,
  durableFailure,
  getBindings,
  getCompilerConfiguration,
  PROJECT_ID,
  sha256Hex,
  validateArtifactBytes,
} from "@/lib/server/evidence-store";
import {
  attachUpstreamJob,
  cancelIdleImport,
  claimCompletedImport,
  claimDueImport,
  compileJobReceipt,
  completeCompileImport,
  ensureJobAudits,
  ensureSubmittedAudit,
  findCompileJob,
  findCompileJobs,
  findIdempotentJob,
  importRetryDelayMs,
  listActiveCompileJobRows,
  listActorCompileJobs,
  markCompileJobFailed,
  markResultStaged,
  MAX_IMPORT_ATTEMPTS,
  PENDING_UPSTREAM_PREFIX,
  recordImportRetry,
  reserveCompileJob,
  updateCompileJobFromUpstream,
  type CompileJobRow,
  type JobError,
} from "./compile-job-store.ts";
import {
  parseEvidenceImportEnvelope,
  type EvidenceImport,
} from "../evidence-graphs/import-contract.ts";
import { persistEvidenceImport } from "../evidence-graphs/import-store.ts";
import {
  COMPILER_EXTRACTION_PROMPT_VERSION,
  COMPILER_VERIFICATION_PROMPT_VERSION,
} from "@/lib/compiler/prompt-version.ts";
import { enrichBibliographicMetadata } from "@/lib/server/bibliographic-metadata.ts";
import { persistDocumentExtraction } from "@/lib/server/document-extraction-store.ts";

const VERSION_ID_RE = /^artifact-version:sha256:[a-f0-9]{64}$/;
const EXTRACTION_RUN_ID_RE = /^extraction:sha256:[a-f0-9]{64}$/;
const VERIFICATION_RUN_ID_RE = /^verification:sha256:[a-f0-9]{64}$/;
const MAX_COMPILER_INPUT_BYTES = 50 * 1024 * 1024;
const COMPILER_MEDIA_TYPES = new Set([
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "image/png",
  "image/jpeg",
  "image/tiff",
]);

function artifactTypeForCompilerMedia(mediaType: string) {
  if (mediaType.startsWith("image/")) return "figure";
  if (mediaType === "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") return "dataset";
  return "paper";
}

type ArtifactRow = CompilerArtifactContext & {
  objectKey: string;
  byteSize: number;
  artifactType: string;
  deletionAuthorized: number;
};

async function client() {
  return new CompilerClient(await getCompilerConfiguration());
}

async function removeCompilerJobCompletely(jobId: string) {
  const compiler = await client();
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      await compiler.remove(jobId);
    } catch (error) {
      if (error instanceof ApiError && error.code === "UPSTREAM_JOB_NOT_FOUND") return;
      throw error;
    }
  }
}

async function loadArtifactMetadata(versionId: string) {
  if (!VERSION_ID_RE.test(versionId)) {
    throw new ApiError(400, "INVALID_ARTIFACT_VERSION_ID", "artifact_version_id must be a canonical SHA-256 version ID");
  }
  const { DB } = await getBindings();
  const row = await DB.prepare(`SELECT a.id AS artifactId, a.artifact_type AS artifactType,
    v.id AS versionId, v.sha256, v.media_type AS mediaType,
    v.source_locator AS locator, v.original_file_name AS fileName,
    v.object_key AS objectKey, v.byte_size AS byteSize,
    EXISTS(SELECT 1 FROM artifact_deletion_authorizations d WHERE d.artifact_id = a.id) AS deletionAuthorized
    FROM artifact_versions v JOIN artifacts a ON a.id = v.artifact_id
    WHERE a.project_id = ? AND v.id = ? LIMIT 1`)
    .bind(PROJECT_ID, versionId).first<Omit<ArtifactRow, "paperId">>();
  if (!row) throw new ApiError(404, "ARTIFACT_VERSION_NOT_FOUND", "Artifact version was not found");
  if (row.deletionAuthorized) {
    throw new ApiError(409, "ARTIFACT_DELETION_IN_PROGRESS", "Artifact deletion is in progress");
  }
  if (row.versionId !== `artifact-version:sha256:${row.sha256}` || row.objectKey !== `artifacts/${row.sha256}`) {
    throw new ApiError(409, "ARTIFACT_IDENTITY_MISMATCH", "Artifact metadata is not content-addressed by its stored SHA-256");
  }
  if (!COMPILER_MEDIA_TYPES.has(row.mediaType)) {
    throw new ApiError(415, "COMPILER_INPUT_TYPE_UNSUPPORTED", "Compile accepts PDF, DOCX, XLSX, PNG, JPEG, and TIFF artifact versions");
  }
  if (row.artifactType !== artifactTypeForCompilerMedia(row.mediaType)) {
    throw new ApiError(409, "ARTIFACT_TYPE_MISMATCH", "Artifact type does not match the Compiler input media type");
  }
  if (!Number.isSafeInteger(row.byteSize) || row.byteSize <= 0 || row.byteSize > MAX_COMPILER_INPUT_BYTES) {
    throw new ApiError(413, "COMPILER_INPUT_TOO_LARGE", "Compile accepts non-empty inputs up to 50 MiB");
  }
  return { ...row, paperId: `paper:${row.sha256}` } satisfies ArtifactRow;
}

async function loadArtifact(versionId: string) {
  const context = await loadArtifactMetadata(versionId);
  const { BUCKET } = await getBindings();
  const object = await BUCKET.get(context.objectKey);
  if (!object) throw new ApiError(404, "ARTIFACT_BYTES_NOT_FOUND", "Artifact bytes were not found");
  if (object.size !== context.byteSize) {
    throw new ApiError(409, "ARTIFACT_SIZE_MISMATCH", "Stored artifact size does not match immutable metadata");
  }
  const bytes = new Uint8Array(await object.arrayBuffer());
  if (bytes.byteLength !== context.byteSize) {
    throw new ApiError(409, "ARTIFACT_SIZE_MISMATCH", "Read artifact bytes do not match immutable metadata");
  }
  if (await sha256Hex(bytes) !== context.sha256) {
    throw new ApiError(409, "ARTIFACT_HASH_MISMATCH", "Stored artifact bytes do not match immutable provenance");
  }
  validateArtifactBytes(context.fileName, context.mediaType, bytes);
  return {
    bytes,
    context: {
      paperId: context.paperId,
      artifactId: context.artifactId,
      versionId: context.versionId,
      sha256: context.sha256,
      mediaType: context.mediaType,
      locator: context.locator,
      fileName: context.fileName,
    } satisfies CompilerArtifactContext,
  };
}

export async function dataQualityStatus(artifactVersionId: string) {
  let upstream;
  try {
    upstream = await (await client()).dataQualityStatus(artifactVersionId);
  } catch (error) {
    if (error instanceof ApiError && error.code === "UPSTREAM_JOB_NOT_FOUND") {
      throw new ApiError(404, "DATA_QUALITY_JOB_NOT_FOUND", "Data Quality has not been run for this ArtifactVersion");
    }
    throw error;
  }
  const report = upstream.status === "completed"
    ? await (await client()).dataQualityResult(upstream.id)
    : null;
  return { ok: true as const, job: upstream, report };
}

export async function submitDataQuality(artifactVersionId: string) {
  const artifact = await loadArtifact(artifactVersionId);
  if (artifact.context.mediaType !== "application/pdf") {
    throw new ApiError(415, "DATA_QUALITY_INPUT_UNSUPPORTED", "Data Quality accepts PDF papers only");
  }
  const upstream = await (await client()).submitDataQuality(artifact.bytes, artifact.context);
  return { ok: true as const, job: upstream, report: null };
}

function jobFailure(cause: unknown, fallbackCode: string, fallbackMessage: string): JobError {
  if (cause instanceof ApiError) {
    const details = cause.details && typeof cause.details === "object" && !Array.isArray(cause.details)
      ? cause.details as Record<string, unknown>
      : {};
    const retryable = typeof details.retryable === "boolean"
      ? details.retryable
      : cause.status === 503 || cause.status === 504;
    return { code: cause.code, message: cause.message.slice(0, 4000), retryable };
  }
  return { code: fallbackCode, message: fallbackMessage, retryable: true };
}

// A transport failure, timeout or unreadable 2xx body leaves the submission's
// outcome unknown: the Compiler may already own a paid job for this request.
const UNKNOWN_SUBMISSION_CODES = new Set([
  "COMPILER_UNAVAILABLE",
  "INVALID_COMPILER_RESPONSE",
  "INVALID_COMPILER_JSON",
  "EMPTY_COMPILER_RESPONSE",
  "COMPILER_RESPONSE_TOO_LARGE",
  "COMPILER_PROTOCOL_MISMATCH",
]);

function isUnknownSubmissionOutcome(error: unknown) {
  return error instanceof ApiError && UNKNOWN_SUBMISSION_CODES.has(error.code);
}

// Wait this long after a reservation's Compiler-side deadline before
// concluding from a missing key that the submission never arrived.
const RECONCILE_GRACE_MS = 10_000;

export async function submitCompileJob(
  actor: string,
  clientRequestId: string,
  artifactVersionId: string,
) {
  const requestHash = await sha256Hex(JSON.stringify({ artifactVersionId, verify: true }));
  const replay = await findIdempotentJob(actor, clientRequestId);
  if (replay) {
    if (replay.requestHash !== requestHash) {
      throw new ApiError(409, "IDEMPOTENCY_KEY_REUSED", "Idempotency key was already used for a different Compile request");
    }
    return { ok: true as const, job: compileJobReceipt(await ensureJobAudits(replay)), duplicate: true };
  }

  const artifact = await loadArtifact(artifactVersionId);
  const reservation = await reserveCompileJob(actor, clientRequestId, requestHash, artifactVersionId);
  if (reservation.duplicate) {
    return {
      ok: true as const,
      job: compileJobReceipt(await ensureJobAudits(reservation.row)),
      duplicate: true,
    };
  }
  let submitted: CompilerJob;
  try {
    // The gateway job ID is the Compiler idempotency key, and the reservation
    // expiry is the Compiler's deadline for creating the job at all.
    submitted = await (await client()).submit(artifact.bytes, artifact.context, true, {
      idempotencyKey: reservation.row.id,
      notAfterMs: Date.parse(reservation.row.expiresAt),
    });
  } catch (error) {
    if (isUnknownSubmissionOutcome(error)) {
      // Never replay the POST. The work pump reconciles the reservation by its
      // key once the deadline passed: it adopts the accepted job or fails it.
      return { ok: true as const, job: compileJobReceipt(reservation.row), duplicate: false };
    }
    await markCompileJobFailed(
      reservation.row,
      jobFailure(error, "COMPILER_SUBMISSION_FAILED", "Compiler submission failed"),
    );
    throw error;
  }
  let row: CompileJobRow;
  try {
    row = await attachUpstreamJob(reservation.row, submitted);
  } catch (error) {
    await removeCompilerJobCompletely(submitted.id);
    throw error;
  }
  if (row.upstreamJobId !== submitted.id) {
    await removeCompilerJobCompletely(submitted.id);
  }
  return { ok: true as const, job: compileJobReceipt(row), duplicate: false };
}

function assertCompilerResultProvenance(imported: EvidenceImport, context: ArtifactRow) {
  const graph = imported.graph;
  const artifact = graph.artifacts[0];
  const version = graph.artifact_versions[0];
  const valid = imported.rootArtifactVersionId === context.versionId
    && graph.paper_id === context.paperId
    && artifact.id === context.artifactId
    && artifact.artifact_type === context.artifactType
    && artifact.canonical_name === context.fileName
    && version.id === context.versionId
    && version.artifact_id === context.artifactId
    && version.version === context.versionId
    && version.sha256 === context.sha256
    && version.media_type === context.mediaType
    && version.locator === context.locator;
  if (!valid) {
    throw new ApiError(422, "COMPILER_PROVENANCE_MISMATCH", "Compiler result does not match the submitted artifact provenance");
  }
  if (imported.extraction.promptVersion !== COMPILER_EXTRACTION_PROMPT_VERSION
    || !EXTRACTION_RUN_ID_RE.test(imported.extraction.runId)) {
    throw new ApiError(422, "COMPILER_EXTRACTION_CONTRACT_MISMATCH", "Compiler extraction metadata does not match the supported contract");
  }
  if (!imported.verification) {
    if (graph.claims.length === 0) return;
    throw new ApiError(422, "COMPILER_VERIFICATION_MISSING", "Verified Compile result did not include an independent verification report");
  }
  if (imported.verification.promptVersion !== COMPILER_VERIFICATION_PROMPT_VERSION
    || !VERIFICATION_RUN_ID_RE.test(imported.verification.runId)) {
    throw new ApiError(422, "COMPILER_VERIFICATION_CONTRACT_MISMATCH", "Compiler verification metadata does not match the supported contract");
  }
}

function assertCompilerRawEnvelope(payload: unknown) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)
    || !Object.hasOwn(payload, "root_artifact_version_id")) {
    throw new ApiError(422, "COMPILER_RESULT_CONTRACT_MISMATCH", "Compiler result must explicitly identify its root artifact version");
  }
}

const STAGED_RESULT_PREFIX = "compile-results/";
const MAX_STAGED_RESULT_BYTES = 8 * 1024 * 1024;
const MAX_STAGED_EXTRACTION_BYTES = 20 * 1024 * 1024;

function stagedKey(row: CompileJobRow, name: "result" | "citation-source" | "document-extraction") {
  return `${STAGED_RESULT_PREFIX}${row.id}/${name}.json`;
}

async function readStaged(row: CompileJobRow, name: "result" | "citation-source" | "document-extraction") {
  const { BUCKET } = await getBindings();
  const object = await BUCKET.get(stagedKey(row, name));
  if (!object) return undefined;
  const limit = name === "document-extraction" ? MAX_STAGED_EXTRACTION_BYTES : MAX_STAGED_RESULT_BYTES;
  if (object.size > limit) throw new ApiError(422, "STAGED_COMPILE_RESULT_TOO_LARGE", "Staged Compile result exceeds its size bound");
  return JSON.parse(await object.text()) as unknown;
}

async function writeStaged(row: CompileJobRow, name: "result" | "citation-source" | "document-extraction", value: unknown) {
  const { BUCKET } = await getBindings();
  await BUCKET.put(stagedKey(row, name), JSON.stringify(value), {
    httpMetadata: { contentType: "application/json" },
  });
}

/** Remove the R2 copy once its import committed (best effort; reruns are idempotent). */
async function discardStagedResult(row: CompileJobRow) {
  if (!row.resultStaged) return;
  try {
    const { BUCKET } = await getBindings();
    await BUCKET.delete([
      stagedKey(row, "result"),
      stagedKey(row, "citation-source"),
      stagedKey(row, "document-extraction"),
    ]);
  } catch (error) {
    console.warn("Staged Compile result was not removed", { jobId: row.id, errorKind: (error as Error)?.name });
  }
}

type ImportSources = {
  row: CompileJobRow;
  payload: unknown;
  citationSource: unknown;
  extraction: Awaited<ReturnType<CompilerClient["documentExtraction"]>> | undefined;
};

/**
 * Read the completed result from its R2 copy when one exists, else from the
 * Compiler, copying it to R2 first. The paid result then survives the
 * Compiler's job expiry, so a failed import can be repaired and replayed.
 */
async function loadImportSources(initial: CompileJobRow): Promise<ImportSources> {
  let row = initial;
  if (row.resultStaged) {
    const payload = await readStaged(row, "result");
    if (payload !== undefined) {
      const citationSource = await readStaged(row, "citation-source").catch(() => undefined);
      const extractionPayload = await readStaged(row, "document-extraction").catch(() => undefined);
      let extraction: ImportSources["extraction"];
      try {
        extraction = extractionPayload === undefined ? undefined : parseDocumentExtraction(extractionPayload);
      } catch {
        extraction = undefined;
      }
      return { row, payload, citationSource, extraction };
    }
  }
  const compiler = await client();
  const payload = await compiler.result(row.upstreamJobId);
  assertCompilerRawEnvelope(payload);
  let citationSource: unknown;
  try {
    citationSource = await compiler.citationSource(row.upstreamJobId);
  } catch {
    // Citation Reception is a rebuildable corpus projection. A rolling
    // Compiler deployment or damaged optional projection must not discard an
    // otherwise valid immutable Paper Evidence Graph.
    citationSource = undefined;
  }
  let extraction: ImportSources["extraction"];
  try {
    extraction = await compiler.documentExtraction(row.upstreamJobId);
  } catch {
    extraction = undefined;
  }
  try {
    await writeStaged(row, "result", payload);
    if (citationSource !== undefined) await writeStaged(row, "citation-source", citationSource);
    if (extraction !== undefined) await writeStaged(row, "document-extraction", extraction);
    row = await markResultStaged(row);
  } catch (error) {
    // Staging protects against later expiry; it never blocks this import.
    console.warn("Compile result was not staged", { jobId: row.id, errorKind: (error as Error)?.name });
  }
  return { row, payload, citationSource, extraction };
}

async function persistDocumentProjection(row: CompileJobRow, sources: ImportSources, context: ArtifactRow) {
  if (!sources.extraction) return;
  try {
    const extraction = sources.extraction;
    if (extraction.artifact_version_id !== context.versionId
      || extraction.artifact_sha256 !== context.sha256) {
      throw new ApiError(422, "DOCUMENT_EXTRACTION_PROVENANCE_MISMATCH", "Compiler document extraction does not match the submitted artifact");
    }
    const enriched = await enrichBibliographicMetadata(extraction);
    if (enriched.status !== "deferred") {
      await persistDocumentExtraction(enriched.extraction, {
        bibliographicStatus: enriched.status,
      });
    }
  } catch (extractionError) {
    // The Evidence Graph is the primary Compile result. A temporary
    // projection/cache failure is repaired lazily by the document endpoint
    // and must not discard an otherwise valid immutable graph.
    console.warn("Compile document extraction was not persisted", {
      jobId: row.id,
      errorCode: extractionError instanceof ApiError ? extractionError.code : "DOCUMENT_EXTRACTION_UNAVAILABLE",
    });
  }
}

async function importCompletedJob(leased: CompileJobRow) {
  let row = leased;
  try {
    const sources = await loadImportSources(row);
    row = sources.row;
    let imported: EvidenceImport;
    try {
      imported = await parseEvidenceImportEnvelope(
        sources.payload,
        new Date().toISOString(),
        sources.citationSource,
      );
    } catch (error) {
      if (!(error instanceof ApiError) || error.code !== "INVALID_CITATION_SOURCE") throw error;
      imported = await parseEvidenceImportEnvelope(sources.payload);
    }
    const context = await loadArtifactMetadata(row.artifactVersionId);
    assertCompilerResultProvenance(imported, context);
    await persistDocumentProjection(row, sources, context);
    const persisted = await persistEvidenceImport(imported, row.createdBy);
    const completed = await completeCompileImport(row, persisted.body.snapshot.id, persisted.body.duplicate);
    if (completed.status === "completed") await discardStagedResult(row);
    return completed;
  } catch (error) {
    const cause = durableFailure(error);
    const attempts = Number(row.importAttempts ?? 0) + 1;
    if (!cause.retryable || attempts >= MAX_IMPORT_ATTEMPTS) {
      // Never auto-rerun a paid Compile because its import failed: the result
      // is retained (R2 copy) for a repaired re-import instead.
      return markCompileJobFailed(row, {
        code: cause.code,
        message: attempts > 1
          ? `Import failed after ${attempts} attempts: ${cause.message}`.slice(0, 4000)
          : cause.message,
        retryable: false,
      });
    }
    return recordImportRetry(
      row,
      cause,
      new Date(Date.now() + importRetryDelayMs(attempts)).toISOString(),
    );
  }
}

/** Read-only job state for observers; the work pump advances jobs. */
export async function getCompileJob(actor: string, id: string) {
  const row = await findCompileJob(actor, id);
  return { ok: true as const, job: compileJobReceipt(row) };
}

/**
 * Adopt the Compiler job that a lost submission response created, or fail the
 * reservation once the Compiler proves none exists. Never resubmits.
 */
async function reconcileSubmission(row: CompileJobRow) {
  if (Date.now() <= Date.parse(row.expiresAt) + RECONCILE_GRACE_MS) return row;
  const accepted = await (await client()).findByIdempotencyKey(row.id);
  if (accepted) return attachUpstreamJob(row, accepted);
  return markCompileJobFailed(row, {
    code: "COMPILER_SUBMISSION_ABANDONED",
    message: "Compiler submission did not finish before its reservation expired",
    retryable: true,
  });
}

function isApiError(error: unknown, code: string) {
  return error instanceof ApiError && error.code === code;
}

async function applyUpstream(row: CompileJobRow, upstream: CompilerJob) {
  if (upstream.status === "completed") {
    const lease = await claimCompletedImport(row, upstream);
    return lease.claimed ? importCompletedJob(lease.row) : lease.row;
  }
  return updateCompileJobFromUpstream(row, upstream);
}

/**
 * Advance one active job by a single step: reconcile a pending submission,
 * mirror Compiler progress, or claim and run a due import. `upstream` is the
 * already-fetched Compiler state (`null` when the Compiler no longer has the
 * job); omit it to fetch the state here. Transient failures are thrown for the
 * caller to retry later; permanent ones become a failed job.
 */
export async function advanceCompileJob(initial: CompileJobRow, upstream?: CompilerJob | null) {
  const row = initial;
  if (row.status === "completed" || row.status === "failed") return ensureJobAudits(row);
  if (row.upstreamJobId.startsWith(PENDING_UPSTREAM_PREFIX)) return reconcileSubmission(row);
  await ensureSubmittedAudit(row);
  if (row.stage === "importing") {
    const lease = await claimDueImport(row);
    return lease.claimed ? importCompletedJob(lease.row) : lease.row;
  }
  type StatusRead = { ok: true; job: CompilerJob } | { ok: false; failure: JobError };
  const readStatus = async (): Promise<StatusRead> => {
    try {
      return { ok: true, job: await (await client()).status(row.upstreamJobId) };
    } catch (error) {
      const failure = jobFailure(error, "COMPILER_STATUS_FAILED", "Compiler status could not be validated");
      if (failure.retryable) throw error;
      return { ok: false, failure };
    }
  };
  let current = upstream;
  if (current === undefined) {
    const read = await readStatus();
    if (!read.ok) return markCompileJobFailed(row, read.failure);
    current = read.job;
  }
  if (current === null) {
    return markCompileJobFailed(row, {
      code: "UPSTREAM_JOB_NOT_FOUND",
      message: "Compiler no longer has this compile job",
      retryable: false,
    });
  }
  try {
    return await applyUpstream(row, current);
  } catch (error) {
    if (isApiError(error, "COMPILER_JOB_MISMATCH")) {
      // A foreign or rewritten upstream job can never converge.
      return markCompileJobFailed(row, jobFailure(error, "COMPILER_JOB_MISMATCH", "Compiler job state is inconsistent"));
    }
    if (!isApiError(error, "COMPILER_STATE_REGRESSION")) throw error;
  }
  // An older state can be a stale read that lost a race with a newer write.
  // Re-read once; only a regression that persists is a real inconsistency.
  const fresh = await findCompileJob(row.createdBy, row.id);
  if (fresh.status === "completed" || fresh.status === "failed" || fresh.stage === "importing") return fresh;
  const reread = await readStatus();
  if (!reread.ok) return markCompileJobFailed(fresh, reread.failure);
  try {
    return await applyUpstream(fresh, reread.job);
  } catch (error) {
    if (isApiError(error, "COMPILER_STATE_REGRESSION") || isApiError(error, "COMPILER_JOB_MISMATCH")) {
      return markCompileJobFailed(fresh, jobFailure(error, "COMPILER_STATE_REGRESSION", "Compiler job state regressed"));
    }
    throw error;
  }
}

/** Advance one actor-owned job immediately (maintenance and tests). */
export async function refreshCompileJob(actor: string, id: string) {
  const row = await advanceCompileJob(await findCompileJob(actor, id));
  return { ok: true as const, job: compileJobReceipt(row) };
}

export async function listCompileJobs(actor: string) {
  return listActorCompileJobs(actor);
}

const CANCELLED_JOB: JobError = {
  code: "COMPILE_CANCELLED",
  message: "Compile task was stopped by the user",
  retryable: false,
};

function isCancelledJob(row: CompileJobRow) {
  if (row.status !== "failed" || !row.errorJson) return false;
  try { return (JSON.parse(row.errorJson) as { code?: unknown }).code === CANCELLED_JOB.code; } catch { return false; }
}

async function cancelOneCompileJob(row: CompileJobRow) {
  if (row.status === "completed") return { row, cancelled: false as const };
  if (isCancelledJob(row)) {
    if (!row.upstreamJobId.startsWith(PENDING_UPSTREAM_PREFIX)) {
      await removeCompilerJobCompletely(row.upstreamJobId);
    }
    return { row, cancelled: true as const };
  }
  if (row.status === "failed") return { row, cancelled: false as const };
  if (row.stage === "importing") {
    // Only an import waiting between attempts (no live lease) can be stopped.
    const stopped = await cancelIdleImport(row, CANCELLED_JOB);
    return { row: stopped, cancelled: isCancelledJob(stopped) };
  }

  let current = row;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    if (current.status === "completed" || current.stage === "importing") {
      return { row: current, cancelled: false as const };
    }
    current = await markCompileJobFailed(current, CANCELLED_JOB, { preserveImporting: true });
    if (isCancelledJob(current)) {
      if (!current.upstreamJobId.startsWith(PENDING_UPSTREAM_PREFIX)) {
        await removeCompilerJobCompletely(current.upstreamJobId);
      }
      return { row: current, cancelled: true as const };
    }
    if (current.status === "failed" || current.status === "completed" || current.stage === "importing") {
      return { row: current, cancelled: false as const };
    }
  }
  throw new ApiError(409, "COMPILE_CANCEL_CONFLICT", "Compile task changed while it was being stopped");
}

export async function cancelCompileJobs(actor: string, jobIds: string[] | null) {
  const rows = jobIds === null
    ? await listActiveCompileJobRows(actor)
    : await findCompileJobs(actor, jobIds);
  // A running import holds a lease and finishes its atomic commit; an import
  // that is waiting between failed attempts can be stopped.
  const active = rows.filter((row) =>
    row.status === "queued" || row.status === "running"
    || (jobIds !== null && isCancelledJob(row)));
  const cancelled: ReturnType<typeof compileJobReceipt>[] = [];
  for (const row of active) {
    const result = await cancelOneCompileJob(row);
    if (result.cancelled) cancelled.push(compileJobReceipt(result.row));
  }
  return { ok: true as const, cancelled };
}
