import { UPLOAD_CHUNK_BYTES } from "../lib/artifacts/upload-limits.mjs";
import { createHash, randomUUID } from "node:crypto";
import { createReadStream } from "node:fs";
import { appendFile, mkdir, readFile, readdir, stat, writeFile } from "node:fs/promises";
import path from "node:path";

export const EVENT_SCHEMA_VERSION = "deepshovel-acceptance-event/1.0";
export const COMPILER_STAGES = ["queued", "parsing", "extracting", "validating"];
const TERMINAL_STATUSES = new Set(["completed", "failed"]);
const RETRYABLE_HTTP_STATUSES = new Set([408, 425, 429]);
const MAX_RESPONSE_BYTES = 1024 * 1024;
const MAX_PDF_BYTES = 50 * 1024 * 1024;

function stableTextCompare(left, right) {
  const a = left.normalize("NFC");
  const b = right.normalize("NFC");
  return a < b ? -1 : a > b ? 1 : 0;
}

async function hashPdf(filePath) {
  const before = await stat(filePath);
  if (!before.isFile()) throw new Error("Acceptance inputs must be regular PDF files");
  if (before.size <= 0 || before.size > MAX_PDF_BYTES) {
    throw new Error("Acceptance PDFs must be non-empty and no larger than 50 MiB");
  }
  const digest = createHash("sha256");
  let byteSize = 0;
  for await (const chunk of createReadStream(filePath)) {
    digest.update(chunk);
    byteSize += chunk.byteLength;
  }
  const after = await stat(filePath);
  if (byteSize !== before.size || after.size !== before.size || after.mtimeMs !== before.mtimeMs) {
    throw new Error("An acceptance PDF changed while its identity was being calculated");
  }
  return { sha256: digest.digest("hex"), byteSize };
}

/** Build a deterministic, non-recursive inventory from one or more directories. */
export async function inventoryPdfDirectories(sourceDirs) {
  if (!Array.isArray(sourceDirs) || sourceDirs.length === 0) {
    throw new Error("At least one --source-dir value is required");
  }
  const inventory = [];
  const digests = new Set();
  for (const [directoryIndex, sourceDir] of sourceDirs.entries()) {
    const entries = (await readdir(sourceDir, { withFileTypes: true }))
      .filter((entry) => entry.isFile() && entry.name.toLowerCase().endsWith(".pdf"))
      .sort((left, right) => stableTextCompare(left.name, right.name));
    const normalizedNames = new Set();
    for (const entry of entries) {
      const fileName = entry.name.normalize("NFC");
      if (normalizedNames.has(fileName)) {
        throw new Error("A source directory contains duplicate NFC-normalized PDF names");
      }
      normalizedNames.add(fileName);
      const filePath = path.join(sourceDir, entry.name);
      const identity = await hashPdf(filePath);
      if (digests.has(identity.sha256)) throw new Error("Acceptance inputs contain duplicate PDF bytes");
      digests.add(identity.sha256);
      inventory.push({
        order: inventory.length + 1,
        sourceGroup: `${String(directoryIndex + 1).padStart(2, "0")}-${path.basename(sourceDir).normalize("NFC")}`,
        fileName,
        filePath,
        mediaType: "application/pdf",
        ...identity,
      });
    }
  }
  return inventory;
}

export async function readVerifiedPdf(item) {
  const bytes = await readFile(item.filePath);
  const sha256 = createHash("sha256").update(bytes).digest("hex");
  if (bytes.byteLength !== item.byteSize || sha256 !== item.sha256) {
    throw new Error("An acceptance PDF changed after inventory");
  }
  return bytes;
}

function validateBaseUrl(value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error("--base-url must be an absolute URL");
  }
  const loopback = ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname);
  if (url.protocol !== "https:" && !(loopback && url.protocol === "http:")) {
    throw new Error("--base-url must use HTTPS, except for a loopback HTTP service");
  }
  if (url.username || url.password || url.search || url.hash) {
    throw new Error("--base-url must not contain credentials, a query, or a fragment");
  }
  url.pathname = `${url.pathname.replace(/\/$/, "")}/`;
  return url;
}

export class AcceptanceApiError extends Error {
  constructor(operation, status, code, retryable) {
    super(`${operation} failed (${status || "network"}/${code})`);
    this.name = "AcceptanceApiError";
    this.operation = operation;
    this.status = status;
    this.code = code;
    this.retryable = retryable;
  }
}

async function boundedJson(response, operation) {
  const declared = response.headers.get("content-length");
  if (declared !== null && (!Number.isSafeInteger(Number(declared)) || Number(declared) < 0
    || Number(declared) > MAX_RESPONSE_BYTES)) {
    throw new AcceptanceApiError(operation, response.status, "RESPONSE_TOO_LARGE", false);
  }
  if (!response.body) throw new AcceptanceApiError(operation, response.status, "EMPTY_RESPONSE", false);
  const chunks = [];
  let total = 0;
  try {
    for await (const chunk of response.body) {
      total += chunk.byteLength;
      if (total > MAX_RESPONSE_BYTES) {
        try { await response.body.cancel(); } catch { /* already closed */ }
        throw new AcceptanceApiError(operation, response.status, "RESPONSE_TOO_LARGE", false);
      }
      chunks.push(chunk);
    }
  } catch (error) {
    if (error instanceof AcceptanceApiError) throw error;
    try { await response.body.cancel(); } catch { /* aborted or already closed */ }
    throw new AcceptanceApiError(operation, 0, "NETWORK_ERROR", true);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw new AcceptanceApiError(operation, response.status, "INVALID_JSON", false);
  }
}

function errorMetadata(payload, status) {
  const error = isObject(payload) && isObject(payload.error) ? payload.error : {};
  const code = typeof error.code === "string" && /^[A-Z][A-Z0-9_]{0,79}$/.test(error.code)
    ? error.code
    : "REQUEST_FAILED";
  const retryable = typeof error.details?.retryable === "boolean"
    ? error.details.retryable
    : RETRYABLE_HTTP_STATUSES.has(status) || status >= 500;
  return { code, retryable };
}

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function requireText(value, code) {
  if (typeof value !== "string" || !value) throw new AcceptanceApiError("contract", 0, code, false);
  return value;
}

function parseJob(payload, expected = {}) {
  if (!isObject(payload) || payload.ok !== true || !isObject(payload.job)) {
    throw new AcceptanceApiError("compile", 0, "INVALID_JOB_ENVELOPE", false);
  }
  const job = payload.job;
  const id = requireText(job.id, "INVALID_JOB_ID");
  const artifactVersionId = requireText(job.artifactVersionId, "INVALID_ARTIFACT_VERSION_ID");
  if (expected.jobId && id !== expected.jobId) {
    throw new AcceptanceApiError("compile", 0, "JOB_ID_MISMATCH", false);
  }
  if (expected.artifactVersionId && artifactVersionId !== expected.artifactVersionId) {
    throw new AcceptanceApiError("compile", 0, "ARTIFACT_VERSION_MISMATCH", false);
  }
  if (!["queued", "running", "completed", "failed"].includes(job.status)
    || ![...COMPILER_STAGES, "importing", "completed", "failed"].includes(job.stage)) {
    throw new AcceptanceApiError("compile", 0, "INVALID_JOB_STATE", false);
  }
  if (!isObject(job.timings)) throw new AcceptanceApiError("compile", 0, "INVALID_JOB_TIMINGS", false);
  const timings = {};
  for (const key of [...COMPILER_STAGES, "total"]) {
    const duration = job.timings[key];
    if (duration !== null && (!Number.isSafeInteger(duration) || duration < 0)) {
      throw new AcceptanceApiError("compile", 0, "INVALID_JOB_TIMINGS", false);
    }
    timings[key] = duration ?? null;
  }
  const error = isObject(job.error) ? {
    code: typeof job.error.code === "string" ? job.error.code.slice(0, 80) : "COMPILE_FAILED",
    retryable: job.error.retryable === true,
  } : null;
  return {
    id,
    artifactVersionId,
    status: job.status,
    stage: job.stage,
    progress: Number.isFinite(job.progress) ? Number(job.progress) : null,
    timings,
    snapshotId: typeof job.snapshotId === "string" ? job.snapshotId : null,
    error,
    createdAt: requireText(job.createdAt, "INVALID_JOB_TIMESTAMP"),
    updatedAt: requireText(job.updatedAt, "INVALID_JOB_TIMESTAMP"),
    expiresAt: requireText(job.expiresAt, "INVALID_JOB_TIMESTAMP"),
  };
}

function retryDelay(attempt) {
  return 500 * (2 ** (attempt - 1));
}

function retryableResponseFailure(error, status) {
  if (!(error instanceof AcceptanceApiError)) return false;
  if (error.code === "NETWORK_ERROR" && error.retryable) return true;
  return status >= 500 && ["EMPTY_RESPONSE", "INVALID_JSON"].includes(error.code);
}

export class AcceptanceApiClient {
  constructor({ baseUrl, runId, actor, cookie, fetchImpl = fetch, sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)), onRetry = async () => {} }) {
    if (typeof runId !== "string" || !/^[A-Za-z0-9._:@/-]{1,64}$/.test(runId)) {
      throw new Error("Acceptance API client requires a valid run ID");
    }
    this.baseUrl = validateBaseUrl(baseUrl);
    this.runId = runId;
    this.fetch = fetchImpl;
    this.sleep = sleep;
    this.onRetry = onRetry;
    this.commonHeaders = {
      origin: this.baseUrl.origin,
      "sec-fetch-site": "same-origin",
    };
    if (actor) this.commonHeaders["oai-authenticated-user-email"] = actor;
    if (cookie) this.commonHeaders.cookie = cookie;
  }

  url(relative) {
    return new URL(relative.replace(/^\//, ""), this.baseUrl);
  }

  async request(operation, relative, init, attempts = 3) {
    const { timeoutMs = 120_000, ...requestInit } = init;
    for (let attempt = 1; attempt <= attempts; attempt += 1) {
      let response;
      let error;
      try {
        response = await this.fetch(this.url(relative), {
          ...requestInit,
          headers: { ...this.commonHeaders, ...requestInit.headers },
          redirect: "manual",
          signal: AbortSignal.timeout(timeoutMs),
        });
      } catch {
        error = new AcceptanceApiError(operation, 0, "NETWORK_ERROR", true);
      }
      if (response) {
        let payload;
        try {
          payload = await boundedJson(response, operation);
        } catch (cause) {
          if (!retryableResponseFailure(cause, response.status)) throw cause;
          const status = cause.code === "NETWORK_ERROR" ? 0 : response.status;
          error = new AcceptanceApiError(operation, status, cause.code, true);
        }
        if (!error) {
          if (response.ok) return payload;
          const metadata = errorMetadata(payload, response.status);
          error = new AcceptanceApiError(operation, response.status, metadata.code, metadata.retryable);
        }
      }
      if (!error.retryable || attempt === attempts) throw error;
      await this.onRetry({ operation, attempt, status: error.status, code: error.code });
      await this.sleep(retryDelay(attempt));
    }
    throw new AcceptanceApiError(operation, 0, "REQUEST_FAILED", false);
  }

  async upload(item, bytes) {
    const started = performance.now();
    const headers = {
      "content-type": item.mediaType,
      "x-artifact-name": encodeURIComponent(item.fileName),
      "idempotency-key": `acceptance:upload:${item.sha256}`,
    };
    let payload;
    if (bytes.byteLength <= UPLOAD_CHUNK_BYTES) {
      payload = await this.request("upload", "api/artifacts", {
        method: "POST", headers, body: bytes, timeoutMs: 180_000,
      });
    } else {
      headers["x-upload-id"] = randomUUID();
      headers["x-upload-sha256"] = item.sha256;
      headers["x-upload-size"] = String(bytes.byteLength);
      let next = 0;
      let failed = false;
      const count = Math.ceil(bytes.byteLength / UPLOAD_CHUNK_BYTES);
      const workers = await Promise.allSettled(Array.from({ length: Math.min(4, count) }, async () => {
        while (!failed && next < count) {
          const index = next++;
          try {
            await this.request("upload_part", "api/artifacts/upload", {
              method: "PUT", headers: { ...headers, "x-upload-index": String(index) },
              body: bytes.slice(index * UPLOAD_CHUNK_BYTES, (index + 1) * UPLOAD_CHUNK_BYTES), timeoutMs: 90_000,
            });
          } catch (error) { failed = true; throw error; }
        }
      }));
      try {
        const failure = workers.find((worker) => worker.status === "rejected");
        if (failure) throw failure.reason;
        payload = await this.request("upload", "api/artifacts/upload", {
          method: "POST", headers, timeoutMs: 90_000,
        });
      } catch (error) {
        await this.request("upload_cleanup", "api/artifacts/upload", {
          method: "DELETE", headers, timeoutMs: 15_000,
        }).catch(() => undefined);
        throw error;
      }
    }
    if (!isObject(payload) || payload.ok !== true || !isObject(payload.artifact) || !isObject(payload.version)) {
      throw new AcceptanceApiError("upload", 0, "INVALID_UPLOAD_ENVELOPE", false);
    }
    const expectedVersionId = `artifact-version:sha256:${item.sha256}`;
    if (payload.version.versionId !== expectedVersionId || payload.version.sha256 !== item.sha256
      || payload.version.byteSize !== item.byteSize || payload.version.fileName !== item.fileName) {
      throw new AcceptanceApiError("upload", 0, "UPLOAD_PROVENANCE_MISMATCH", false);
    }
    return {
      artifactId: requireText(payload.artifact.id, "INVALID_ARTIFACT_ID"),
      artifactVersionId: expectedVersionId,
      duplicate: payload.duplicate === true,
      elapsedMs: Math.round(performance.now() - started),
    };
  }

  async submit(item, artifactVersionId) {
    const started = performance.now();
    const payload = await this.request("compile_submit", "api/compile", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "idempotency-key": `acceptance:compile:${this.runId}:${item.sha256}`,
      },
      body: JSON.stringify({ artifact_version_id: artifactVersionId }),
      timeoutMs: 180_000,
    });
    return {
      job: parseJob(payload, { artifactVersionId }),
      duplicate: payload.duplicate === true,
      elapsedMs: Math.round(performance.now() - started),
    };
  }

  async poll(jobId, artifactVersionId) {
    const payload = await this.request(
      "compile_poll",
      `api/compile?job_id=${encodeURIComponent(jobId)}`,
      { method: "GET", timeoutMs: 45_000 },
    );
    return parseJob(payload, { jobId, artifactVersionId });
  }
}

export function safeJobMetadata(job) {
  return {
    job_id: job.id,
    artifact_version_id: job.artifactVersionId,
    status: job.status,
    stage: job.stage,
    progress: job.progress,
    timings_ms: { ...job.timings },
    snapshot_id: job.snapshotId,
    error_code: job.error?.code ?? null,
    error_retryable: job.error?.retryable ?? null,
    created_at: job.createdAt,
    updated_at: job.updatedAt,
    expires_at: job.expiresAt,
  };
}

export class JsonlEventLog {
  constructor(filePath, runId = randomUUID()) {
    this.filePath = filePath;
    this.runId = runId;
    this.pending = Promise.resolve();
  }

  async initialize() {
    await mkdir(path.dirname(this.filePath), { recursive: true });
  }

  append(event, fields = {}) {
    const record = {
      schema_version: EVENT_SCHEMA_VERSION,
      event,
      run_id: this.runId,
      at: new Date().toISOString(),
      ...fields,
    };
    this.pending = this.pending.then(() => appendFile(this.filePath, `${JSON.stringify(record)}\n`, { encoding: "utf8", mode: 0o600 }));
    return this.pending;
  }

  async flush() {
    await this.pending;
  }
}

export async function readEventLog(filePath) {
  const content = await readFile(filePath, "utf8");
  const events = [];
  for (const [index, line] of content.split("\n").entries()) {
    if (!line.trim()) continue;
    let event;
    try { event = JSON.parse(line); } catch { throw new Error(`Invalid JSONL event at line ${index + 1}`); }
    if (!isObject(event) || event.schema_version !== EVENT_SCHEMA_VERSION || typeof event.run_id !== "string") {
      throw new Error(`Invalid acceptance event contract at line ${index + 1}`);
    }
    events.push(event);
  }
  if (!events.length || events[0].event !== "run_started") throw new Error("Resume log has no run_started event");
  const runId = events[0].run_id;
  if (events.some((event) => event.run_id !== runId)) throw new Error("Resume log contains multiple run IDs");
  return { runId, events };
}

export function reconstructProgress(inventory, events) {
  const states = new Map(inventory.map((item) => [item.sha256, { item, upload: null, submission: null, job: null }]));
  const inventoryEvents = events.filter((event) => event.event === "inventory_item");
  if (events.length) {
    if (inventoryEvents.length !== inventory.length) {
      throw new Error("Resume log does not contain the complete PDF inventory");
    }
    const expected = inventory.map((item) => `${item.order}:${item.sha256}:${item.byteSize}:${item.fileName}`);
    const actual = inventoryEvents.map((event) => `${event.order}:${event.sha256}:${event.byte_size}:${event.file_name}`);
    if (JSON.stringify(actual) !== JSON.stringify(expected)) {
      throw new Error("Resume inventory does not match the current PDF bytes and fixed order");
    }
  }
  for (const event of events) {
    const state = typeof event.sha256 === "string" ? states.get(event.sha256) : null;
    if (!state) continue;
    if (event.event === "upload_completed") {
      state.upload = {
        artifactId: event.artifact_id,
        artifactVersionId: event.artifact_version_id,
        duplicate: event.duplicate === true,
        elapsedMs: event.elapsed_ms,
      };
    } else if (event.event === "compile_submitted") {
      state.submission = { duplicate: event.duplicate === true, elapsedMs: event.elapsed_ms };
      state.job = jobFromEvent(event);
    } else if (event.event === "job_observed") {
      state.job = jobFromEvent(event);
    }
  }
  return states;
}

function jobFromEvent(event) {
  return {
    id: event.job_id,
    artifactVersionId: event.artifact_version_id,
    status: event.status,
    stage: event.stage,
    progress: event.progress,
    timings: { ...event.timings_ms },
    snapshotId: event.snapshot_id,
    error: event.error_code ? { code: event.error_code, retryable: event.error_retryable === true } : null,
    createdAt: event.created_at,
    updatedAt: event.updated_at,
    expiresAt: event.expires_at,
  };
}

export function isTerminalJob(job) {
  return Boolean(job) && TERMINAL_STATUSES.has(job.status);
}

export function percentile(values, fraction) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.max(0, Math.ceil(fraction * sorted.length) - 1)];
}

export function stageIntervals(job) {
  const start = Date.parse(job.createdAt);
  if (!Number.isFinite(start)) return null;
  let cursor = start;
  const intervals = {};
  for (const stage of COMPILER_STAGES) {
    const duration = job.timings[stage];
    if (!Number.isSafeInteger(duration) || duration < 0) return null;
    intervals[stage] = { startMs: cursor, endMs: cursor + duration, durationMs: duration };
    cursor += duration;
  }
  intervals.gpt = {
    startMs: intervals.extracting.startMs,
    endMs: intervals.validating.endMs,
    durationMs: intervals.extracting.durationMs + intervals.validating.durationMs,
  };
  return intervals;
}

export function peakConcurrency(intervals) {
  const points = intervals.filter((interval) => interval.endMs > interval.startMs).flatMap((interval) => [
    { at: interval.startMs, delta: 1 },
    { at: interval.endMs, delta: -1 },
  ]).sort((left, right) => left.at - right.at || left.delta - right.delta);
  let current = 0;
  let peak = 0;
  for (const point of points) {
    current += point.delta;
    peak = Math.max(peak, current);
  }
  return peak;
}

function overlaps(left, right) {
  return left.startMs < right.endMs && right.startMs < left.endMs;
}

export function analyzeStates(states, { expectedCount = 14, requiredGptPeak = 2 } = {}) {
  const ordered = [...states.values()].sort((left, right) => left.item.order - right.item.order);
  const completed = ordered.filter((state) => state.job?.status === "completed");
  const failed = ordered.filter((state) => state.job?.status === "failed");
  const intervalRows = completed.map((state) => ({ state, intervals: stageIntervals(state.job) }))
    .filter((row) => row.intervals !== null);
  const timings = Object.fromEntries([...COMPILER_STAGES, "total"].map((stage) => {
    const values = completed.map((state) => state.job.timings[stage]).filter(Number.isSafeInteger);
    return [stage, { p50_ms: percentile(values, 0.5), p95_ms: percentile(values, 0.95) }];
  }));
  const parsingIntervals = intervalRows.map((row) => row.intervals.parsing);
  const gptIntervals = intervalRows.map((row) => row.intervals.gpt);
  const parsingOrder = [...intervalRows].sort((left, right) => left.intervals.parsing.startMs - right.intervals.parsing.startMs);
  const job2ParseOverlapsJob1Gpt = parsingOrder.length >= 2
    ? overlaps(parsingOrder[1].intervals.parsing, parsingOrder[0].intervals.gpt)
    : false;
  const starts = completed.map((state) => Date.parse(state.job.createdAt)).filter(Number.isFinite);
  const ends = completed.map((state) => Date.parse(state.job.updatedAt)).filter(Number.isFinite);
  const mineruParsingPeak = peakConcurrency(parsingIntervals);
  const gptPeak = peakConcurrency(gptIntervals);
  const concurrentDocumentChecksApply = ordered.length !== 1;
  const stageIntervalReceipts = intervalRows
    .sort((left, right) => left.state.item.order - right.state.item.order)
    .map(({ state, intervals }) => {
      const compilerEndMs = Date.parse(state.job.createdAt) + state.job.timings.total;
      const gatewayEndMs = Date.parse(state.job.updatedAt);
      const receipt = { order: state.item.order, job_id: state.job.id };
      for (const stage of [...COMPILER_STAGES, "gpt"]) {
        receipt[stage] = {
          start_at: new Date(intervals[stage].startMs).toISOString(),
          end_at: new Date(intervals[stage].endMs).toISOString(),
          duration_ms: intervals[stage].durationMs,
        };
      }
      receipt.gateway_tail = {
        start_at: new Date(compilerEndMs).toISOString(),
        end_at: new Date(Math.max(compilerEndMs, gatewayEndMs)).toISOString(),
        duration_ms: Math.max(0, gatewayEndMs - compilerEndMs),
      };
      return receipt;
    });
  const checks = {
    expected_count: ordered.length === expectedCount,
    all_completed: completed.length === expectedCount && failed.length === 0,
    mineru_parsing_peak_is_one: mineruParsingPeak === 1,
    gpt_peak_reached: concurrentDocumentChecksApply
      ? gptPeak >= requiredGptPeak
      : "not_applicable",
    job2_parse_overlaps_job1_gpt: concurrentDocumentChecksApply
      ? job2ParseOverlapsJob1Gpt
      : "not_applicable",
  };
  return {
    document_count: ordered.length,
    completed_count: completed.length,
    failed_count: failed.length,
    makespan_ms: starts.length === completed.length && ends.length === completed.length
      ? Math.max(...ends) - Math.min(...starts)
      : null,
    stage_percentiles: timings,
    stage_intervals: stageIntervalReceipts,
    mineru_parsing_peak: mineruParsingPeak,
    gpt_peak: gptPeak,
    job2_parse_overlaps_job1_gpt: job2ParseOverlapsJob1Gpt,
    checks,
    passed: Object.values(checks).every((check) => check === true || check === "not_applicable"),
  };
}

function csvCell(value) {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function summaryCsv(states) {
  const headers = [
    "order", "source_group", "file_name", "sha256", "byte_size", "artifact_id",
    "artifact_version_id", "job_id", "status", "stage", "snapshot_id", "error_code",
    "upload_duplicate", "compile_duplicate", "upload_ms", "submit_ms", "queued_ms",
    "parsing_ms", "extracting_ms", "validating_ms", "compiler_total_ms", "created_at", "completed_at",
    "queued_start_at", "queued_end_at", "parsing_start_at", "parsing_end_at", "extracting_start_at",
    "extracting_end_at", "validating_start_at", "validating_end_at", "gpt_start_at", "gpt_end_at",
    "gateway_tail_ms",
  ];
  const rows = [...states.values()].sort((left, right) => left.item.order - right.item.order).map((state) => {
    const intervals = state.job?.status === "completed" ? stageIntervals(state.job) : null;
    const compilerEndMs = intervals ? Date.parse(state.job.createdAt) + state.job.timings.total : null;
    const gatewayEndMs = intervals ? Date.parse(state.job.updatedAt) : null;
    return [
      state.item.order,
      state.item.sourceGroup,
      state.item.fileName,
      state.item.sha256,
      state.item.byteSize,
      state.upload?.artifactId,
      state.upload?.artifactVersionId,
      state.job?.id,
      state.job?.status ?? "not_submitted",
      state.job?.stage,
      state.job?.snapshotId,
      state.job?.error?.code,
      state.upload?.duplicate,
      state.submission?.duplicate,
      state.upload?.elapsedMs,
      state.submission?.elapsedMs,
      state.job?.timings.queued,
      state.job?.timings.parsing,
      state.job?.timings.extracting,
      state.job?.timings.validating,
      state.job?.timings.total,
      state.job?.createdAt,
      isTerminalJob(state.job) ? state.job.updatedAt : null,
      intervals ? new Date(intervals.queued.startMs).toISOString() : null,
      intervals ? new Date(intervals.queued.endMs).toISOString() : null,
      intervals ? new Date(intervals.parsing.startMs).toISOString() : null,
      intervals ? new Date(intervals.parsing.endMs).toISOString() : null,
      intervals ? new Date(intervals.extracting.startMs).toISOString() : null,
      intervals ? new Date(intervals.extracting.endMs).toISOString() : null,
      intervals ? new Date(intervals.validating.startMs).toISOString() : null,
      intervals ? new Date(intervals.validating.endMs).toISOString() : null,
      intervals ? new Date(intervals.gpt.startMs).toISOString() : null,
      intervals ? new Date(intervals.gpt.endMs).toISOString() : null,
      intervals ? Math.max(0, gatewayEndMs - compilerEndMs) : null,
    ];
  });
  return `${[headers, ...rows].map((row) => row.map(csvCell).join(",")).join("\n")}\n`;
}

export async function writeSummaryCsv(filePath, states) {
  await mkdir(path.dirname(filePath), { recursive: true });
  await writeFile(filePath, summaryCsv(states), { encoding: "utf8", mode: 0o600 });
}
