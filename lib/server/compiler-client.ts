import { ApiError } from "./evidence-store.ts";
import { containsCredential, readBoundedJson } from "./service-response.ts";
import {
  parseCitationReceptionEnvelope,
  type CitationPairRequest,
} from "../citation-reception/index.ts";
import {
  parseClaimComparisonEnvelope,
  parseClaimComparisonNormalization,
  type ClaimComparisonAnalyzeRequest,
  type ClaimComparisonNormalizeRequest,
} from "../claim-comparison/index.ts";
import { parseDocumentExtraction } from "../document-extraction/index.ts";

export const COMPILER_JOB_SCHEMA = "compiler-job/1.0";
export const COMPILER_JOB_LIST_SCHEMA = "compiler-job-list/1.0";
export const MAX_JOB_LOOKUP_IDS = 100;
const JOB_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const IDEMPOTENCY_KEY_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/;
const ISO_UTC_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/;
const UPSTREAM_ERROR_CODE_RE = /^[A-Z][A-Z0-9_]{0,79}$/;
const SHARED_SECRET_RE = /^[\x21-\x7e]{32,512}$/;
const MAX_STATUS_BYTES = 64 * 1024;
const MAX_JOB_LIST_BYTES = MAX_JOB_LOOKUP_IDS * MAX_STATUS_BYTES;
const MAX_RESULT_BYTES = 8 * 1024 * 1024;
const MAX_DOCUMENT_EXTRACTION_BYTES = 20 * 1024 * 1024;
const ARTIFACT_VERSION_ID_RE = /^artifact-version:sha256:[a-f0-9]{64}$/u;

export type CompilerStage = "queued" | "parsing" | "extracting" | "validating" | "completed" | "failed";

export type CompilerJob = {
  id: string;
  status: CompilerStage;
  createdAt: string;
  updatedAt: string;
  expiresAt: string;
  stageTimingsMs: {
    queued: number | null;
    parsing: number | null;
    extracting: number | null;
    validating: number | null;
    total: number | null;
  };
  error: { code: string; message: string; retryable: boolean } | null;
  links: { self: string; result: string };
};

export type CompilerArtifactContext = {
  paperId: string;
  artifactId: string;
  versionId: string;
  sha256: string;
  mediaType: string;
  locator: string;
  fileName: string;
};

export type CompilerDataQualityJob = {
  id: string;
  artifactVersionId: string;
  status: "queued" | "running" | "completed" | "failed";
  stage: "queued" | "parsing" | "checking" | "completed" | "failed";
  progress: number;
  createdAt: string;
  updatedAt: string;
  error: { code: string; message: string; retryable: boolean } | null;
};

export type CompilerClientConfig = {
  baseUrl: string;
  sharedSecret?: string;
  fetch?: typeof fetch;
};

function objectValue(value: unknown, path: string) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ApiError(502, "INVALID_COMPILER_RESPONSE", `${path} must be an object`);
  }
  return value as Record<string, unknown>;
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[], path: string) {
  const unknown = Object.keys(value).filter((key) => !keys.includes(key));
  const missing = keys.filter((key) => !(key in value));
  if (unknown.length || missing.length) {
    throw new ApiError(502, "INVALID_COMPILER_RESPONSE", `${path} has an invalid shape`);
  }
}

function textValue(value: unknown, path: string, maximum = 1000) {
  if (typeof value !== "string" || !value.trim() || value.length > maximum) {
    throw new ApiError(502, "INVALID_COMPILER_RESPONSE", `${path} must be a non-empty string`);
  }
  return value.trim();
}

function timestampValue(value: unknown, path: string) {
  const text = textValue(value, path, 64);
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?Z$/.exec(text);
  const instant = Date.parse(text);
  const date = new Date(instant);
  const calendarMatches = parts
    && date.getUTCFullYear() === Number(parts[1])
    && date.getUTCMonth() + 1 === Number(parts[2])
    && date.getUTCDate() === Number(parts[3])
    && date.getUTCHours() === Number(parts[4])
    && date.getUTCMinutes() === Number(parts[5])
    && date.getUTCSeconds() === Number(parts[6]);
  if (!ISO_UTC_RE.test(text) || !Number.isFinite(instant) || !calendarMatches) {
    throw new ApiError(502, "INVALID_COMPILER_RESPONSE", `${path} must be an ISO-8601 UTC timestamp`);
  }
  return text;
}

function nullableDuration(value: unknown, path: string) {
  if (value === null) return null;
  if (!Number.isSafeInteger(value) || (value as number) < 0) {
    throw new ApiError(502, "INVALID_COMPILER_RESPONSE", `${path} must be a non-negative integer duration or null`);
  }
  return value as number;
}

function boundedJson(response: Response, maximumBytes: number) {
  return readBoundedJson(response, maximumBytes, (kind, message) => new ApiError(502, {
    invalid: "INVALID_COMPILER_RESPONSE",
    too_large: "COMPILER_RESPONSE_TOO_LARGE",
    empty: "EMPTY_COMPILER_RESPONSE",
    json: "INVALID_COMPILER_JSON",
  }[kind], message));
}

function parseCompilerError(value: unknown) {
  if (value === null) return null;
  const error = objectValue(value, "$.job.error");
  exactKeys(error, ["code", "message", "retryable"], "$.job.error");
  if (typeof error.retryable !== "boolean") {
    throw new ApiError(502, "INVALID_COMPILER_RESPONSE", "$.job.error.retryable must be boolean");
  }
  return {
    code: textValue(error.code, "$.job.error.code", 160),
    message: textValue(error.message, "$.job.error.message", 4000),
    retryable: error.retryable,
  };
}

/** Strictly validate the small, versioned status envelope returned by Compiler. */
export function parseCompilerJobEnvelope(value: unknown): CompilerJob {
  const envelope = objectValue(value, "$");
  exactKeys(envelope, ["schema_version", "job"], "$");
  if (envelope.schema_version !== COMPILER_JOB_SCHEMA) {
    throw new ApiError(502, "COMPILER_PROTOCOL_MISMATCH", `Compiler must use ${COMPILER_JOB_SCHEMA}`);
  }
  const job = objectValue(envelope.job, "$.job");
  exactKeys(job, ["id", "status", "created_at", "updated_at", "expires_at", "stage_timings_ms", "error", "links"], "$.job");
  const id = textValue(job.id, "$.job.id", 64);
  if (!JOB_ID_RE.test(id)) throw new ApiError(502, "INVALID_COMPILER_RESPONSE", "Compiler job ID must be a UUID");
  const status = job.status;
  if (!["queued", "parsing", "extracting", "validating", "completed", "failed"].includes(String(status))) {
    throw new ApiError(502, "INVALID_COMPILER_RESPONSE", "Compiler returned an unknown job status");
  }
  const timings = objectValue(job.stage_timings_ms, "$.job.stage_timings_ms");
  exactKeys(timings, ["queued", "parsing", "extracting", "validating", "total"], "$.job.stage_timings_ms");
  const links = objectValue(job.links, "$.job.links");
  exactKeys(links, ["self", "result"], "$.job.links");
  const createdAt = timestampValue(job.created_at, "$.job.created_at");
  const updatedAt = timestampValue(job.updated_at, "$.job.updated_at");
  const expiresAt = timestampValue(job.expires_at, "$.job.expires_at");
  if (Date.parse(createdAt) > Date.parse(updatedAt) || Date.parse(updatedAt) > Date.parse(expiresAt)) {
    throw new ApiError(502, "INVALID_COMPILER_RESPONSE", "Compiler job timestamps are not monotonic");
  }
  const error = parseCompilerError(job.error);
  if ((status === "failed") !== (error !== null)) {
    throw new ApiError(502, "INVALID_COMPILER_RESPONSE", "Only failed Compiler jobs may contain an error");
  }
  const selfLink = textValue(links.self, "$.job.links.self", 500);
  const resultLink = textValue(links.result, "$.job.links.result", 500);
  if (selfLink !== `/v1/compile-jobs/${id}` || resultLink !== `/v1/compile-jobs/${id}/result`) {
    throw new ApiError(502, "INVALID_COMPILER_RESPONSE", "Compiler job links do not match the returned job ID");
  }
  return {
    id,
    status: status as CompilerStage,
    createdAt,
    updatedAt,
    expiresAt,
    stageTimingsMs: {
      queued: nullableDuration(timings.queued, "$.job.stage_timings_ms.queued"),
      parsing: nullableDuration(timings.parsing, "$.job.stage_timings_ms.parsing"),
      extracting: nullableDuration(timings.extracting, "$.job.stage_timings_ms.extracting"),
      validating: nullableDuration(timings.validating, "$.job.stage_timings_ms.validating"),
      total: nullableDuration(timings.total, "$.job.stage_timings_ms.total"),
    },
    error,
    links: { self: selfLink, result: resultLink },
  };
}

function parseDataQualityJobEnvelope(value: unknown): CompilerDataQualityJob {
  const envelope = objectValue(value, "$");
  exactKeys(envelope, ["schema_version", "job"], "$");
  if (envelope.schema_version !== "data-quality-job/1.0") {
    throw new ApiError(502, "COMPILER_PROTOCOL_MISMATCH", "Compiler returned an unsupported Data Quality job schema");
  }
  const job = objectValue(envelope.job, "$.job");
  exactKeys(job, ["id", "artifact_version_id", "status", "stage", "progress", "created_at", "updated_at", "error"], "$.job");
  const id = textValue(job.id, "$.job.id", 64);
  if (!JOB_ID_RE.test(id)) throw new ApiError(502, "INVALID_COMPILER_RESPONSE", "Data Quality job ID must be a UUID");
  const status = String(job.status);
  const stage = String(job.stage);
  if (!["queued", "running", "completed", "failed"].includes(status)
    || !["queued", "parsing", "checking", "completed", "failed"].includes(stage)
    || !Number.isSafeInteger(job.progress) || Number(job.progress) < 0 || Number(job.progress) > 100) {
    throw new ApiError(502, "INVALID_COMPILER_RESPONSE", "Compiler returned an invalid Data Quality status");
  }
  const error = parseCompilerError(job.error);
  if ((status === "failed") !== (error !== null)) {
    throw new ApiError(502, "INVALID_COMPILER_RESPONSE", "Only failed Data Quality jobs may contain an error");
  }
  return {
    id,
    artifactVersionId: textValue(job.artifact_version_id, "$.job.artifact_version_id", 160),
    status: status as CompilerDataQualityJob["status"],
    stage: stage as CompilerDataQualityJob["stage"],
    progress: Number(job.progress),
    createdAt: timestampValue(job.created_at, "$.job.created_at"),
    updatedAt: timestampValue(job.updated_at, "$.job.updated_at"),
    error,
  };
}

function safeBaseUrl(value: string) {
  let url: URL;
  try { url = new URL(value); } catch { throw new ApiError(503, "COMPILER_NOT_CONFIGURED", "Compiler base URL is invalid"); }
  const local = isLoopback(url);
  if (url.protocol !== "https:" && !(local && url.protocol === "http:")) {
    throw new ApiError(503, "COMPILER_NOT_CONFIGURED", "Compiler must use HTTPS except on localhost");
  }
  if (url.username || url.password || url.search || url.hash) {
    throw new ApiError(503, "COMPILER_NOT_CONFIGURED", "Compiler base URL must not contain credentials, query, or fragment data");
  }
  url.pathname = `${url.pathname.replace(/\/$/, "")}/`;
  return url;
}

function isLoopback(url: URL) {
  return url.hostname === "127.0.0.1" || url.hostname === "localhost" || url.hostname === "[::1]";
}

function upstreamFailure(payload: unknown, status: number) {
  const value = payload && typeof payload === "object" && !Array.isArray(payload)
    ? payload as Record<string, unknown>
    : {};
  const officialError = value.error && typeof value.error === "object" && !Array.isArray(value.error)
    ? value.error as Record<string, unknown>
    : null;
  const detail = officialError ?? (
    value.detail && typeof value.detail === "object" && !Array.isArray(value.detail)
      ? value.detail as Record<string, unknown>
      : value
  );
  const rawCode = typeof detail.code === "string" ? detail.code.trim() : "REQUEST_FAILED";
  const code = UPSTREAM_ERROR_CODE_RE.test(rawCode) ? rawCode : "REQUEST_FAILED";
  const retryable = typeof detail.retryable === "boolean"
    ? detail.retryable
    : status === 408 || status === 429 || status >= 500;
  const rawMessage = typeof detail.message === "string" ? detail.message : `Compiler returned HTTP ${status}`;
  const message = rawMessage.replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim().slice(0, 1000)
    || `Compiler returned HTTP ${status}`;
  return new ApiError(retryable ? 503 : 502, `UPSTREAM_${code}`, message, { retryable });
}


function artifactForm(bytes: Uint8Array<ArrayBuffer>, context: CompilerArtifactContext) {
  const form = new FormData();
  form.set("file", new Blob([bytes], { type: context.mediaType }), context.fileName);
  form.set("paper_id", context.paperId);
  form.set("artifact_id", context.artifactId);
  form.set("version_id", context.versionId);
  form.set("artifact_sha256", context.sha256);
  form.set("media_type", context.mediaType);
  form.set("locator", context.locator);
  return form;
}

export class CompilerClient {
  private readonly baseUrl: URL;
  private readonly request: typeof fetch;
  private readonly config: CompilerClientConfig;

  constructor(config: CompilerClientConfig) {
    this.config = config;
    this.baseUrl = safeBaseUrl(config.baseUrl);
    if (!isLoopback(this.baseUrl) && !config.sharedSecret) {
      throw new ApiError(503, "COMPILER_NOT_CONFIGURED", "Remote Compiler requires a shared service credential");
    }
    if (config.sharedSecret && !SHARED_SECRET_RE.test(config.sharedSecret)) {
      throw new ApiError(503, "COMPILER_NOT_CONFIGURED", "Compiler shared credential has an invalid format");
    }
    this.request = config.fetch ?? fetch;
  }

  private url(path: string) {
    return new URL(path.replace(/^\//, ""), this.baseUrl);
  }

  private async invoke(path: string, init: RequestInit, maximumBytes: number, expectNoContent = false) {
    const headers = new Headers(init.headers);
    if (this.config.sharedSecret) headers.set("authorization", `Bearer ${this.config.sharedSecret}`);
    const requestInit: RequestInit = {
      ...init,
      headers,
      // workerd implements `follow` and `manual`, but deliberately does not
      // implement the browser-only `error` mode. Inspect manual redirects
      // below so no Location is ever followed.
      redirect: "manual",
      referrerPolicy: "no-referrer",
    };
    const isBodylessGet = (init.method ?? "GET").toUpperCase() === "GET" && init.body == null;
    const attempts = isBodylessGet ? 2 : 1;
    let response: Response | undefined;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      try {
        response = await this.request(this.url(path), requestInit);
        break;
      } catch {
        // A timed-out or cancelled signal stays aborted, so replaying it would
        // fail instantly; only a connection failure is worth one more attempt.
        if (attempt + 1 === attempts || requestInit.signal?.aborted) {
          throw new ApiError(503, "COMPILER_UNAVAILABLE", "Compiler service is unavailable", { retryable: true });
        }
      }
    }
    if (!response) throw new ApiError(503, "COMPILER_UNAVAILABLE", "Compiler service is unavailable", { retryable: true });
    if (response.status >= 300 && response.status < 400) {
      try { await response.body?.cancel(); } catch { /* body is already closed */ }
      throw new ApiError(502, "COMPILER_REDIRECT_REJECTED", "Compiler redirects are not allowed", { retryable: false });
    }
    if (expectNoContent && response.status === 204) {
      try { await response.body?.cancel(); } catch { /* body is already closed */ }
      return null;
    }
    if (expectNoContent && response.ok) {
      try { await response.body?.cancel(); } catch { /* body is already closed */ }
      throw new ApiError(502, "INVALID_COMPILER_RESPONSE", "Compiler deletion must return HTTP 204");
    }
    let payload: unknown;
    try {
      payload = await boundedJson(response, maximumBytes);
    } catch (error) {
      if (response.ok) throw error;
      try { await response.body?.cancel(); } catch { /* already consumed or locked */ }
      throw upstreamFailure({}, response.status);
    }
    if (this.config.sharedSecret && containsCredential(payload, this.config.sharedSecret)) {
      throw new ApiError(502, "COMPILER_RESPONSE_REJECTED", "Compiler response contained forbidden credential material");
    }
    if (!response.ok) throw upstreamFailure(payload, response.status);
    return payload;
  }

  async submit(
    bytes: Uint8Array<ArrayBuffer>,
    context: CompilerArtifactContext,
    verify = true,
    options: { idempotencyKey?: string; notAfterMs?: number } = {},
  ) {
    const form = artifactForm(bytes, context);
    form.set("verify", String(verify));
    const headers = new Headers();
    if (options.idempotencyKey !== undefined) {
      if (!IDEMPOTENCY_KEY_RE.test(options.idempotencyKey)) {
        throw new ApiError(500, "INVALID_COMPILER_REQUEST", "Compiler idempotency key is invalid");
      }
      headers.set("idempotency-key", options.idempotencyKey);
      // The Compiler records no job after this instant on the shared host
      // clock, so a later lookup that finds nothing is conclusive.
      if (options.notAfterMs !== undefined) form.set("submit_not_after_ms", String(Math.trunc(options.notAfterMs)));
    }
    const payload = await this.invoke("v1/compile-jobs", { method: "POST", body: form, headers, signal: AbortSignal.timeout(60_000) }, MAX_STATUS_BYTES);
    return parseCompilerJobEnvelope(payload);
  }

  /** Bodyless reconciliation of a submission whose response was lost. */
  async findByIdempotencyKey(idempotencyKey: string) {
    if (!IDEMPOTENCY_KEY_RE.test(idempotencyKey)) {
      throw new ApiError(500, "INVALID_COMPILER_REQUEST", "Compiler idempotency key is invalid");
    }
    try {
      const payload = await this.invoke(
        `v1/compile-jobs?idempotency_key=${encodeURIComponent(idempotencyKey)}`,
        { method: "GET", signal: AbortSignal.timeout(10_000) },
        MAX_STATUS_BYTES,
      );
      return parseCompilerJobEnvelope(payload);
    } catch (error) {
      if (error instanceof ApiError && error.code === "UPSTREAM_JOB_NOT_FOUND") return null;
      throw error;
    }
  }

  /** One bodyless status read for up to 100 jobs; unknown IDs are omitted. */
  async listJobs(jobIds: readonly string[]) {
    if (!jobIds.length || jobIds.length > MAX_JOB_LOOKUP_IDS) {
      throw new ApiError(500, "INVALID_COMPILER_REQUEST", `Compiler job lookups accept 1-${MAX_JOB_LOOKUP_IDS} IDs`);
    }
    for (const jobId of jobIds) {
      if (!JOB_ID_RE.test(jobId)) throw new ApiError(400, "INVALID_COMPILE_JOB_ID", "Compile job ID must be a UUID");
    }
    const payload = await this.invoke(
      `v1/compile-jobs?ids=${jobIds.map(encodeURIComponent).join(",")}`,
      { method: "GET", signal: AbortSignal.timeout(10_000) },
      MAX_JOB_LIST_BYTES,
    );
    const envelope = objectValue(payload, "$");
    exactKeys(envelope, ["schema_version", "jobs"], "$");
    if (envelope.schema_version !== COMPILER_JOB_LIST_SCHEMA || !Array.isArray(envelope.jobs)) {
      throw new ApiError(502, "COMPILER_PROTOCOL_MISMATCH", `Compiler must use ${COMPILER_JOB_LIST_SCHEMA}`);
    }
    const requested = new Set(jobIds);
    const jobs = envelope.jobs.map((job) => parseCompilerJobEnvelope({ schema_version: COMPILER_JOB_SCHEMA, job }));
    if (jobs.some((job) => !requested.has(job.id)) || new Set(jobs.map((job) => job.id)).size !== jobs.length) {
      throw new ApiError(502, "COMPILER_JOB_MISMATCH", "Compiler returned jobs that were not requested");
    }
    return jobs;
  }

  async status(jobId: string) {
    if (!JOB_ID_RE.test(jobId)) throw new ApiError(400, "INVALID_COMPILE_JOB_ID", "Compile job ID must be a UUID");
    const payload = await this.invoke(`v1/compile-jobs/${encodeURIComponent(jobId)}`, { method: "GET", signal: AbortSignal.timeout(10_000) }, MAX_STATUS_BYTES);
    const job = parseCompilerJobEnvelope(payload);
    if (job.id !== jobId) throw new ApiError(502, "COMPILER_JOB_MISMATCH", "Compiler returned a different job ID");
    return job;
  }

  async result(jobId: string) {
    if (!JOB_ID_RE.test(jobId)) throw new ApiError(400, "INVALID_COMPILE_JOB_ID", "Compile job ID must be a UUID");
    return this.invoke(`v1/compile-jobs/${encodeURIComponent(jobId)}/result`, { method: "GET", signal: AbortSignal.timeout(30_000) }, MAX_RESULT_BYTES);
  }

  async citationSource(jobId: string) {
    if (!JOB_ID_RE.test(jobId)) throw new ApiError(400, "INVALID_COMPILE_JOB_ID", "Compile job ID must be a UUID");
    return this.invoke(
      `v1/compile-jobs/${encodeURIComponent(jobId)}/citation-source`,
      { method: "GET", signal: AbortSignal.timeout(30_000) },
      MAX_RESULT_BYTES,
    );
  }

  async documentExtraction(jobId: string) {
    if (!JOB_ID_RE.test(jobId)) throw new ApiError(400, "INVALID_COMPILE_JOB_ID", "Compile job ID must be a UUID");
    const payload = await this.invoke(
      `v1/compile-jobs/${encodeURIComponent(jobId)}/document-extraction`,
      { method: "GET", signal: AbortSignal.timeout(30_000) },
      MAX_DOCUMENT_EXTRACTION_BYTES,
    );
    try {
      return parseDocumentExtraction(payload);
    } catch (error) {
      throw new ApiError(
        502,
        "INVALID_DOCUMENT_EXTRACTION_RESPONSE",
        error instanceof Error ? error.message : "Compiler returned invalid document extraction data",
      );
    }
  }

  async documentExtractionByVersion(artifactVersionId: string) {
    if (!ARTIFACT_VERSION_ID_RE.test(artifactVersionId)) {
      throw new ApiError(400, "INVALID_ARTIFACT_VERSION_ID", "Artifact version ID must be content-addressed");
    }
    const payload = await this.invoke(
      `v1/document-extractions/${encodeURIComponent(artifactVersionId)}`,
      { method: "GET", signal: AbortSignal.timeout(30_000) },
      MAX_DOCUMENT_EXTRACTION_BYTES,
    );
    try {
      const extraction = parseDocumentExtraction(payload);
      if (extraction.artifact_version_id !== artifactVersionId) {
        throw new TypeError("Document extraction belongs to another artifact version");
      }
      return extraction;
    } catch (error) {
      throw new ApiError(
        502,
        "INVALID_DOCUMENT_EXTRACTION_RESPONSE",
        error instanceof Error ? error.message : "Compiler returned invalid document extraction data",
      );
    }
  }

  async removeDocumentExtraction(artifactVersionId: string) {
    if (!ARTIFACT_VERSION_ID_RE.test(artifactVersionId)) {
      throw new ApiError(400, "INVALID_ARTIFACT_VERSION_ID", "Artifact version ID must be content-addressed");
    }
    await this.invoke(
      `v1/document-extractions/${encodeURIComponent(artifactVersionId)}`,
      { method: "DELETE", signal: AbortSignal.timeout(10_000) },
      MAX_STATUS_BYTES,
      true,
    );
  }

  async submitDataQuality(bytes: Uint8Array<ArrayBuffer>, context: CompilerArtifactContext) {
    const form = artifactForm(bytes, context);
    return parseDataQualityJobEnvelope(await this.invoke(
      "v1/data-quality-jobs",
      { method: "POST", body: form, signal: AbortSignal.timeout(60_000) },
      MAX_STATUS_BYTES,
    ));
  }

  async dataQualityStatus(artifactVersionId: string) {
    return parseDataQualityJobEnvelope(await this.invoke(
      `v1/data-quality-jobs?version_id=${encodeURIComponent(artifactVersionId)}`,
      { method: "GET", signal: AbortSignal.timeout(10_000) },
      MAX_STATUS_BYTES,
    ));
  }

  async dataQualityResult(jobId: string) {
    if (!JOB_ID_RE.test(jobId)) throw new ApiError(400, "INVALID_DATA_QUALITY_JOB_ID", "Data Quality job ID must be a UUID");
    return this.invoke(
      `v1/data-quality-jobs/${encodeURIComponent(jobId)}/result`,
      { method: "GET", signal: AbortSignal.timeout(30_000) },
      MAX_RESULT_BYTES,
    );
  }

  async analyzeCitationReception(request: CitationPairRequest, signal?: AbortSignal) {
    const body = JSON.stringify(request);
    if (new TextEncoder().encode(body).byteLength > MAX_RESULT_BYTES) {
      throw new ApiError(413, "CITATION_PAIR_TOO_LARGE", "Citation pair exceeds the 8 MiB analysis boundary");
    }
    const payload = await this.invoke(
      "v1/citation-receptions/analyze",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body,
        signal,
      },
      MAX_RESULT_BYTES,
    );
    try {
      return await parseCitationReceptionEnvelope(payload, request);
    } catch (error) {
      throw new ApiError(
        502,
        "INVALID_CITATION_RECEPTION_RESPONSE",
        error instanceof Error ? error.message : "Compiler returned invalid Citation Reception data",
      );
    }
  }

  async normalizeClaimComparison(request: ClaimComparisonNormalizeRequest, signal?: AbortSignal) {
    const body = JSON.stringify(request);
    if (new TextEncoder().encode(body).byteLength > MAX_RESULT_BYTES) {
      throw new ApiError(413, "CLAIM_NORMALIZATION_TOO_LARGE", "Claim normalization exceeds the 8 MiB boundary");
    }
    const payload = await this.invoke(
      "v1/claim-comparisons/normalize",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body,
        signal,
      },
      MAX_RESULT_BYTES,
    );
    try {
      return parseClaimComparisonNormalization(payload, request.user_text);
    } catch (error) {
      throw new ApiError(
        502,
        "INVALID_CLAIM_NORMALIZATION_RESPONSE",
        error instanceof Error ? error.message : "Compiler returned invalid Claim normalization data",
      );
    }
  }

  async analyzeClaimComparisons(request: ClaimComparisonAnalyzeRequest, signal?: AbortSignal) {
    const body = JSON.stringify(request);
    if (new TextEncoder().encode(body).byteLength > MAX_RESULT_BYTES) {
      throw new ApiError(413, "CLAIM_COMPARISON_TOO_LARGE", "Claim comparison exceeds the 8 MiB analysis boundary");
    }
    const payload = await this.invoke(
      "v1/claim-comparisons/analyze",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body,
        signal,
      },
      MAX_RESULT_BYTES,
    );
    try {
      return await parseClaimComparisonEnvelope(payload, request);
    } catch (error) {
      throw new ApiError(
        502,
        "INVALID_CLAIM_COMPARISON_RESPONSE",
        error instanceof Error ? error.message : "Compiler returned invalid Claim comparison data",
      );
    }
  }

  async remove(jobId: string) {
    if (!JOB_ID_RE.test(jobId)) throw new ApiError(400, "INVALID_COMPILE_JOB_ID", "Compile job ID must be a UUID");
    await this.invoke(
      `v1/compile-jobs/${encodeURIComponent(jobId)}`,
      { method: "DELETE", signal: AbortSignal.timeout(10_000) },
      MAX_STATUS_BYTES,
      true,
    );
  }
}

export function compilerProgress(status: CompilerStage) {
  return ({ queued: 3, parsing: 28, extracting: 68, validating: 90, completed: 96, failed: 100 } as const)[status];
}
