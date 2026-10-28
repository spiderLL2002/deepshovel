import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { parseArgs, runAcceptance } from "../scripts/acceptance-batch.mjs";
import {
  AcceptanceApiClient,
  analyzeStates,
  inventoryPdfDirectories,
  peakConcurrency,
  stageIntervals,
} from "../scripts/acceptance-lib.mjs";

async function fixtureDirectories(filesByDirectory) {
  const root = await mkdtemp(path.join(os.tmpdir(), "deepshovel-acceptance-"));
  const directories = [];
  for (const [index, files] of filesByDirectory.entries()) {
    const directory = path.join(root, `source-${index + 1}`);
    await mkdir(directory);
    for (const [fileName, content] of Object.entries(files)) {
      await writeFile(path.join(directory, fileName), content);
    }
    directories.push(directory);
  }
  return { root, directories };
}

function state(item, job) {
  return {
    item,
    upload: { artifactId: `artifact:${item.order}`, artifactVersionId: job.artifactVersionId, duplicate: false, elapsedMs: 1 },
    submission: { duplicate: false, elapsedMs: 1 },
    job,
  };
}

function completedJob(order, createdAt, updatedAt, timings) {
  return {
    id: `00000000-0000-4000-8000-${String(order).padStart(12, "0")}`,
    artifactVersionId: `artifact-version:sha256:${String(order).repeat(64).slice(0, 64)}`,
    status: "completed",
    stage: "completed",
    progress: 100,
    timings,
    snapshotId: `snapshot:${order}`,
    error: null,
    createdAt,
    updatedAt,
    expiresAt: "2026-08-17T00:00:00.000Z",
  };
}

test("acceptance inventory has deterministic one-to-many-directory order and recomputed identities", async (t) => {
  const fixture = await fixtureDirectories([
    { "乙.pdf": Buffer.from("%PDF-1.7\nsecond"), "甲.pdf": Buffer.from("%PDF-1.7\nfirst"), "skip.txt": "ignored" },
    { "丙.PDF": Buffer.from("%PDF-1.7\nthird") },
    { "丁.pdf": Buffer.from("%PDF-1.7\nfourth") },
  ]);
  t.after(() => rm(fixture.root, { recursive: true, force: true }));

  const inventory = await inventoryPdfDirectories(fixture.directories);
  assert.deepEqual(inventory.map((item) => item.fileName), ["乙.pdf", "甲.pdf", "丙.PDF", "丁.pdf"]);
  assert.deepEqual(inventory.map((item) => item.order), [1, 2, 3, 4]);
  assert.deepEqual(inventory.map((item) => item.sourceGroup), [
    "01-source-1", "01-source-1", "02-source-2", "03-source-3",
  ]);
  for (const item of inventory) {
    const bytes = await readFile(item.filePath);
    assert.equal(item.byteSize, bytes.byteLength);
    assert.equal(item.sha256, createHash("sha256").update(bytes).digest("hex"));
    assert.equal(path.isAbsolute(item.filePath), true);
  }

  const singleDirectory = await inventoryPdfDirectories([fixture.directories[0]]);
  assert.deepEqual(singleDirectory.map((item) => item.fileName), ["乙.pdf", "甲.pdf"]);
  await assert.rejects(inventoryPdfDirectories([]), /At least one --source-dir/);
});

test("dry-run checks the combined inventory against the explicit expected count", async (t) => {
  const fixture = await fixtureDirectories([
    { "a.pdf": Buffer.from("%PDF-1.7\na"), "b.pdf": Buffer.from("%PDF-1.7\nb") },
  ]);
  t.after(() => rm(fixture.root, { recursive: true, force: true }));
  const options = {
    sourceDirs: fixture.directories,
    expectedCount: 2,
    submitWorkers: 3,
    pollMs: 2_000,
    requiredGptPeak: 2,
    dryRun: true,
    outputDir: path.join(fixture.root, "output"),
  };

  const result = await runAcceptance(options);
  assert.equal(result.states.size, 2);
  await assert.rejects(
    runAcceptance({ ...options, expectedCount: 1, outputDir: path.join(fixture.root, "mismatch") }),
    /Expected 1 PDFs, found 2/,
  );
});

test("acceptance analysis reconstructs exact stage intervals and pipeline overlap", () => {
  const item1 = { order: 1, sourceGroup: "01-a", fileName: "a.pdf", sha256: "a".repeat(64), byteSize: 10 };
  const item2 = { order: 2, sourceGroup: "02-b", fileName: "b.pdf", sha256: "b".repeat(64), byteSize: 20 };
  const job1 = completedJob(1, "2026-08-16T00:00:00.000Z", "2026-08-16T00:00:00.260Z", {
    queued: 10, parsing: 30, extracting: 100, validating: 100, total: 240,
  });
  const job2 = completedJob(2, "2026-08-16T00:00:00.030Z", "2026-08-16T00:00:00.280Z", {
    queued: 10, parsing: 30, extracting: 100, validating: 100, total: 240,
  });
  const intervals = stageIntervals(job1);
  assert.equal(intervals.parsing.startMs, Date.parse(job1.createdAt) + 10);
  assert.equal(intervals.gpt.endMs, Date.parse(job1.createdAt) + 240);
  assert.equal(peakConcurrency([intervals.parsing, intervals.parsing]), 2);

  const states = new Map([[item1.sha256, state(item1, job1)], [item2.sha256, state(item2, job2)]]);
  const analysis = analyzeStates(states, { expectedCount: 2, requiredGptPeak: 2 });
  assert.equal(analysis.mineru_parsing_peak, 1);
  assert.equal(analysis.gpt_peak, 2);
  assert.equal(analysis.job2_parse_overlaps_job1_gpt, true);
  assert.equal(analysis.stage_percentiles.parsing.p50_ms, 30);
  assert.equal(analysis.stage_percentiles.total.p95_ms, 240);
  assert.equal(analysis.stage_intervals[0].parsing.duration_ms, 30);
  assert.equal(analysis.stage_intervals[0].gateway_tail.duration_ms, 20);
  assert.equal(analysis.makespan_ms, 280);
  assert.equal(analysis.passed, true);
});

test("single-document acceptance marks parallelism checks not applicable without hiding failures", () => {
  const item = { order: 1, sourceGroup: "01-a", fileName: "a.pdf", sha256: "a".repeat(64), byteSize: 10 };
  const job = completedJob(1, "2026-08-16T00:00:00.000Z", "2026-08-16T00:00:00.260Z", {
    queued: 10, parsing: 30, extracting: 100, validating: 100, total: 240,
  });
  const states = new Map([[item.sha256, state(item, job)]]);

  const analysis = analyzeStates(states, { expectedCount: 1, requiredGptPeak: 2 });
  assert.equal(analysis.document_count, 1);
  assert.equal(analysis.completed_count, 1);
  assert.equal(analysis.gpt_peak, 1);
  assert.equal(analysis.job2_parse_overlaps_job1_gpt, false);
  assert.equal(analysis.checks.gpt_peak_reached, "not_applicable");
  assert.equal(analysis.checks.job2_parse_overlaps_job1_gpt, "not_applicable");
  assert.equal(analysis.passed, true);

  const countMismatch = analyzeStates(states, { expectedCount: 2, requiredGptPeak: 2 });
  assert.equal(countMismatch.checks.expected_count, false);
  assert.equal(countMismatch.passed, false);
});

test("multiple documents still fail when required parallelism and pipeline overlap are missing", () => {
  const item1 = { order: 1, sourceGroup: "01-a", fileName: "a.pdf", sha256: "a".repeat(64), byteSize: 10 };
  const item2 = { order: 2, sourceGroup: "02-b", fileName: "b.pdf", sha256: "b".repeat(64), byteSize: 20 };
  const timings = { queued: 10, parsing: 30, extracting: 100, validating: 100, total: 240 };
  const job1 = completedJob(1, "2026-08-16T00:00:00.000Z", "2026-08-16T00:00:00.260Z", timings);
  const job2 = completedJob(2, "2026-08-16T00:00:00.260Z", "2026-08-16T00:00:00.520Z", timings);
  const states = new Map([[item1.sha256, state(item1, job1)], [item2.sha256, state(item2, job2)]]);

  const analysis = analyzeStates(states, { expectedCount: 2, requiredGptPeak: 2 });
  assert.equal(analysis.checks.expected_count, true);
  assert.equal(analysis.checks.all_completed, true);
  assert.equal(analysis.checks.mineru_parsing_peak_is_one, true);
  assert.equal(analysis.gpt_peak, 1);
  assert.equal(analysis.checks.gpt_peak_reached, false);
  assert.equal(analysis.checks.job2_parse_overlaps_job1_gpt, false);
  assert.equal(analysis.passed, false);
});

function jsonResponse(value, status = 200) {
  const body = JSON.stringify(value);
  return new Response(body, {
    status,
    headers: { "content-type": "application/json", "content-length": String(Buffer.byteLength(body)) },
  });
}

test("acceptance Compile submission retries empty/non-JSON 5xx with one idempotency key", async () => {
  const versionId = `artifact-version:sha256:${"a".repeat(64)}`;
  const item = { sha256: "a".repeat(64) };
  const runId = "00000000-0000-4000-8000-0000000000aa";
  const calls = [];
  const retries = [];
  const delays = [];
  const client = new AcceptanceApiClient({
    baseUrl: "http://127.0.0.1:8100/",
    runId,
    fetchImpl: async (_url, init) => {
      calls.push(new Headers(init.headers).get("idempotency-key"));
      if (calls.length === 1) return new Response("upstream reset", { status: 503 });
      if (calls.length === 2) return new Response(null, { status: 502 });
      return jsonResponse({
        ok: true,
        job: {
          id: "00000000-0000-4000-8000-000000000001",
          artifactVersionId: versionId,
          status: "queued",
          stage: "queued",
          progress: 3,
          timings: { queued: null, parsing: null, extracting: null, validating: null, total: null },
          snapshotId: null,
          error: null,
          createdAt: "2026-08-16T00:00:00.000Z",
          updatedAt: "2026-08-16T00:00:00.000Z",
          expiresAt: "2026-08-17T00:00:00.000Z",
        },
      }, 202);
    },
    sleep: async (milliseconds) => { delays.push(milliseconds); },
    onRetry: async (retry) => { retries.push(retry); },
  });

  const result = await client.submit(item, versionId);
  assert.equal(result.job.artifactVersionId, versionId);
  assert.deepEqual(calls, Array(3).fill(`acceptance:compile:${runId}:${item.sha256}`));
  assert.deepEqual(delays, [500, 1000]);
  assert.deepEqual(retries.map(({ status, code }) => ({ status, code })), [
    { status: 503, code: "INVALID_JSON" },
    { status: 502, code: "EMPTY_RESPONSE" },
  ]);
});

test("acceptance client does not retry a malformed 4xx response", async () => {
  let calls = 0;
  const client = new AcceptanceApiClient({
    baseUrl: "http://127.0.0.1:8100/",
    runId: "00000000-0000-4000-8000-0000000000bb",
    fetchImpl: async () => {
      calls += 1;
      return new Response("bad request", { status: 400 });
    },
    sleep: async () => { throw new Error("must not sleep"); },
  });

  await assert.rejects(
    client.request("compile_submit", "api/compile", { method: "POST" }),
    (error) => error.code === "INVALID_JSON" && error.retryable === false,
  );
  assert.equal(calls, 1);
});

test("acceptance client classifies an aborted response body and retries it", async () => {
  const retries = [];
  let calls = 0;
  const client = new AcceptanceApiClient({
    baseUrl: "http://127.0.0.1:8100/",
    runId: "00000000-0000-4000-8000-0000000000bc",
    fetchImpl: async () => {
      calls += 1;
      if (calls === 1) {
        return new Response(new ReadableStream({
          start(controller) {
            controller.error(new DOMException("timed out", "TimeoutError"));
          },
        }), { status: 200, headers: { "content-type": "application/json" } });
      }
      return jsonResponse({ ok: true });
    },
    sleep: async () => {},
    onRetry: async (retry) => { retries.push(retry); },
  });

  assert.deepEqual(
    await client.request("compile_poll", "api/compile", { method: "GET" }),
    { ok: true },
  );
  assert.equal(calls, 2);
  assert.deepEqual(retries, [{
    operation: "compile_poll",
    attempt: 1,
    status: 0,
    code: "NETWORK_ERROR",
  }]);
});

test("fresh acceptance runs get distinct Compile keys while resume preserves the key", async () => {
  const bytes = Buffer.from("%PDF-1.7\nidentity");
  const sha256 = createHash("sha256").update(bytes).digest("hex");
  const item = {
    fileName: "identity.pdf",
    mediaType: "application/pdf",
    byteSize: bytes.byteLength,
    sha256,
  };
  const versionId = `artifact-version:sha256:${sha256}`;
  const runA = "00000000-0000-4000-8000-0000000000ca";
  const runB = "00000000-0000-4000-8000-0000000000cb";
  const requests = [];
  const fetchImpl = async (urlValue, init) => {
    const url = new URL(urlValue);
    requests.push({ path: url.pathname, key: new Headers(init.headers).get("idempotency-key") });
    if (url.pathname === "/api/artifacts") {
      return jsonResponse({
        ok: true,
        artifact: { id: `artifact:${sha256.slice(0, 8)}` },
        version: { versionId, sha256, byteSize: bytes.byteLength, fileName: item.fileName },
        duplicate: requests.filter((request) => request.path === "/api/artifacts").length > 1,
      }, 201);
    }
    return jsonResponse({
      ok: true,
      job: {
        id: "00000000-0000-4000-8000-000000000001",
        artifactVersionId: versionId,
        status: "queued",
        stage: "queued",
        progress: 3,
        timings: { queued: null, parsing: null, extracting: null, validating: null, total: null },
        snapshotId: null,
        error: null,
        createdAt: "2026-08-16T00:00:00.000Z",
        updatedAt: "2026-08-16T00:00:00.000Z",
        expiresAt: "2026-08-17T00:00:00.000Z",
      },
      duplicate: false,
    }, 202);
  };
  const client = (runId) => new AcceptanceApiClient({
    baseUrl: "http://127.0.0.1:8100/",
    runId,
    fetchImpl,
  });

  await client(runA).upload(item, bytes);
  await client(runA).submit(item, versionId);
  await client(runB).upload(item, bytes);
  await client(runB).submit(item, versionId);
  await client(runA).submit(item, versionId);

  const uploadKeys = requests.filter((request) => request.path === "/api/artifacts").map((request) => request.key);
  const compileKeys = requests.filter((request) => request.path === "/api/compile").map((request) => request.key);
  assert.deepEqual(uploadKeys, Array(2).fill(`acceptance:upload:${sha256}`));
  assert.deepEqual(compileKeys, [
    `acceptance:compile:${runA}:${sha256}`,
    `acceptance:compile:${runB}:${sha256}`,
    `acceptance:compile:${runA}:${sha256}`,
  ]);
  assert.notEqual(compileKeys[0], compileKeys[1]);
  assert.equal(compileKeys[0], compileKeys[2]);
});

function fakeWebApi() {
  const uploads = new Map();
  const jobs = new Map();
  let activeSubmits = 0;
  let maxActiveSubmits = 0;
  let submitCount = 0;
  let pollWhileSubmitting = false;
  const seenHeaders = [];
  let releaseBothSubmitted;
  const bothSubmitted = new Promise((resolve) => { releaseBothSubmitted = resolve; });
  let releaseSecondSubmit;
  const secondSubmitGate = new Promise((resolve) => { releaseSecondSubmit = resolve; });

  const fetchImpl = async (urlValue, init) => {
    const url = new URL(urlValue);
    const headers = new Headers(init.headers);
    seenHeaders.push({
      actor: headers.get("oai-authenticated-user-email"),
      cookie: headers.get("cookie"),
      origin: headers.get("origin"),
      fetchSite: headers.get("sec-fetch-site"),
    });
    if (url.pathname === "/api/artifacts" && init.method === "POST") {
      const bytes = Buffer.from(init.body);
      const sha256 = createHash("sha256").update(bytes).digest("hex");
      const fileName = decodeURIComponent(headers.get("x-artifact-name"));
      const versionId = `artifact-version:sha256:${sha256}`;
      uploads.set(versionId, { order: fileName === "a.pdf" ? 1 : 2, fileName, sha256, byteSize: bytes.byteLength });
      return jsonResponse({
        ok: true,
        artifact: { id: `artifact:${sha256.slice(0, 8)}` },
        version: { versionId, sha256, byteSize: bytes.byteLength, fileName },
        duplicate: false,
      }, 201);
    }
    if (url.pathname === "/api/compile" && init.method === "POST") {
      activeSubmits += 1;
      maxActiveSubmits = Math.max(maxActiveSubmits, activeSubmits);
      submitCount += 1;
      const { artifact_version_id: versionId } = JSON.parse(init.body);
      const upload = uploads.get(versionId);
      const order = upload.order;
      if (submitCount === 2) releaseBothSubmitted();
      await bothSubmitted;
      if (order !== 1) await Promise.race([
        secondSubmitGate,
        new Promise((_, reject) => setTimeout(
          () => reject(new Error("polling did not start while another submission was pending")),
          2_000,
        )),
      ]);
      const id = `00000000-0000-4000-8000-${String(order).padStart(12, "0")}`;
      const createdAt = order === 1 ? "2026-08-16T00:00:00.000Z" : "2026-08-16T00:00:00.030Z";
      const job = {
        id,
        artifactVersionId: versionId,
        status: "queued",
        stage: "queued",
        progress: 3,
        timings: { queued: null, parsing: null, extracting: null, validating: null, total: null },
        snapshotId: null,
        error: null,
        createdAt,
        updatedAt: createdAt,
        expiresAt: "2026-08-17T00:00:00.000Z",
      };
      jobs.set(id, { ...job, order });
      activeSubmits -= 1;
      return jsonResponse({ ok: true, job, duplicate: false }, 202);
    }
    if (url.pathname === "/api/compile" && init.method === "GET") {
      if (activeSubmits > 0 || submitCount < 2) pollWhileSubmitting = true;
      releaseSecondSubmit();
      const id = url.searchParams.get("job_id");
      const stored = jobs.get(id);
      const job = {
        ...stored,
        status: "completed",
        stage: "completed",
        progress: 100,
        timings: { queued: 10, parsing: 30, extracting: 100, validating: 100, total: 240 },
        snapshotId: `snapshot:${stored.order}`,
        updatedAt: stored.order === 1 ? "2026-08-16T00:00:00.260Z" : "2026-08-16T00:00:00.280Z",
      };
      delete job.order;
      return jsonResponse({ ok: true, job });
    }
    throw new Error(`Unexpected fake request: ${init.method} ${url.pathname}`);
  };
  return {
    fetchImpl,
    metrics: () => ({ maxActiveSubmits, pollWhileSubmitting, seenHeaders }),
  };
}

test("batch operator limits submissions, polls during submission, records only safe metadata, and resumes", async (t) => {
  const fixture = await fixtureDirectories([
    { "a.pdf": Buffer.from("%PDF-1.7\na") },
    { "b.pdf": Buffer.from("%PDF-1.7\nb") },
  ]);
  t.after(() => rm(fixture.root, { recursive: true, force: true }));
  const outputDir = path.join(fixture.root, "output");
  const api = fakeWebApi();
  const cookie = "session=acceptance-cookie-secret";
  const actor = "acceptance-actor@example.test";
  const options = {
    sourceDirs: fixture.directories,
    expectedCount: 2,
    submitWorkers: 3,
    pollMs: 250,
    requiredGptPeak: 2,
    dryRun: false,
    baseUrl: "http://127.0.0.1:8100/",
    outputDir,
    cookie,
    actor,
  };
  const result = await runAcceptance(options, { fetchImpl: api.fetchImpl, sleep: async () => {}, onProgress: () => {} });
  assert.equal(result.analysis.passed, true);
  assert.equal(api.metrics().maxActiveSubmits, 2);
  assert.equal(api.metrics().pollWhileSubmitting, true);
  assert.ok(api.metrics().seenHeaders.every((headers) => headers.actor === actor && headers.cookie === cookie));
  assert.ok(api.metrics().seenHeaders.every((headers) => headers.origin === "http://127.0.0.1:8100"));
  assert.ok(api.metrics().seenHeaders.every((headers) => headers.fetchSite === "same-origin"));

  const eventsPath = path.join(outputDir, "events.jsonl");
  const recorded = ["events.jsonl", "summary.csv", "summary.json"]
    .map(async (fileName) => readFile(path.join(outputDir, fileName), "utf8"));
  const recordedText = (await Promise.all(recorded)).join("\n");
  assert.doesNotMatch(recordedText, /acceptance-cookie-secret|acceptance-actor@example\.test|%PDF|reasoning/i);
  assert.match(recordedText, /"mineru_parsing_peak":1/);
  assert.match(recordedText, /"gpt_peak":2/);
  const summary = JSON.parse(await readFile(path.join(outputDir, "summary.json"), "utf8"));
  assert.equal(summary.schema_version, "deepshovel-acceptance-summary/1.0");
  assert.equal(summary.analysis.passed, true);

  let resumedNetworkCalls = 0;
  const resumed = await runAcceptance(
    { ...options, outputDir: undefined, resume: eventsPath },
    { fetchImpl: async () => { resumedNetworkCalls += 1; throw new Error("resume must not call completed jobs"); }, onProgress: () => {} },
  );
  assert.equal(resumedNetworkCalls, 0);
  assert.equal(resumed.analysis.passed, true);
  assert.match(await readFile(eventsPath, "utf8"), /"event":"run_resumed"/);
});

test("CLI contract accepts one-to-many directories, defaults to 14 PDFs, and limits submission workers", () => {
  const singleDirectory = parseArgs([
    "--source-dir", "/tmp/a", "--base-url", "http://127.0.0.1:8100",
    "--output-dir", "/tmp/out", "--submit-workers", "3",
  ]);
  assert.deepEqual(singleDirectory.sourceDirs, ["/tmp/a"]);
  assert.equal(singleDirectory.expectedCount, 14);
  assert.equal(singleDirectory.submitWorkers, 3);

  const manyDirectories = parseArgs([
    "--source-dir", "/tmp/a", "--source-dir", "/tmp/b", "--source-dir", "/tmp/c",
    "--base-url", "http://127.0.0.1:8100", "--output-dir", "/tmp/out", "--expected-count", "3",
  ]);
  assert.deepEqual(manyDirectories.sourceDirs, ["/tmp/a", "/tmp/b", "/tmp/c"]);
  assert.equal(manyDirectories.expectedCount, 3);

  assert.throws(() => parseArgs([
    "--source-dir", "/tmp/a", "--source-dir", "/tmp/b", "--base-url", "http://127.0.0.1:8100",
    "--output-dir", "/tmp/out", "--submit-workers", "4",
  ]), /1 to 3/);
  assert.throws(() => parseArgs([
    "--base-url", "http://127.0.0.1:8100", "--output-dir", "/tmp/out",
  ]), /At least one --source-dir/);
});
