import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

import {
  EVIDENCE_GRAPH_SCHEMA_VERSION,
  createAtomicClaim,
  hashEvidenceGraph,
  sha256Hex,
} from "../lib/evidence-graph/index.ts";
import { migratedDatabase, SqliteD1 } from "./helpers/sqlite-d1.mjs";

const SECRET = "work-queue-test-secret-0123456789abcdef";
const env = { DB: null, BUCKET: {}, COMPILER_BASE_URL: "http://127.0.0.1:8011", COMPILER_SHARED_SECRET: SECRET };
globalThis.__workQueueTestEnv = env;

const loader = `
let rootUrl;
export function initialize(data) { rootUrl = data.rootUrl; }
export async function resolve(specifier, context, nextResolve) {
  if (specifier === "cloudflare:workers") {
    return { url: "data:text/javascript,export const env=globalThis.__workQueueTestEnv", shortCircuit: true };
  }
  if (specifier.startsWith("@/")) {
    let path = specifier.slice(2);
    if (["lib/evidence-graph", "lib/evidence-retrieval", "lib/citation-reception", "lib/claim-comparison",
      "lib/document-extraction", "lib/evidence-knowledge-graph"].includes(path)) path += "/index.ts";
    else if (!/\\.[cm]?[jt]sx?$/.test(path)) path += ".ts";
    return { url: new URL(path, rootUrl).href, shortCircuit: true };
  }
  return nextResolve(specifier, context);
}`;
register(`data:text/javascript,${encodeURIComponent(loader)}`, {
  parentURL: import.meta.url,
  data: { rootUrl: new URL("../", import.meta.url).href },
});

const PROJECT_ID = "DEEPSHOVEL-PROJECT";
const queue = await import("../lib/server/work-queue.ts");
const { assertInternalWorker, durableFailure, ApiError } = await import("../lib/server/evidence-store.ts");
const { parseEvidenceImportEnvelope } = await import("../app/api/evidence-graphs/import-contract.ts");
const { persistEvidenceImport } = await import("../app/api/evidence-graphs/import-store.ts");
const { advanceCompileJobs } = await import("../app/api/compile/compile-worker.ts");

function fresh() {
  const database = migratedDatabase();
  const gateway = new SqliteD1(database);
  env.DB = gateway;
  return { database, gateway };
}

function rows(database, sql = "SELECT * FROM work_items ORDER BY id") {
  return database.prepare(sql).all().map((row) => ({ ...row }));
}

const failure = (retryable, code = "STORE_BUSY") => ({ code, message: "test failure", retryable });

test("work items lease once, survive a crashed runner and settle only with their lease", async () => {
  const { database, gateway } = fresh();
  const now = new Date().toISOString();
  await gateway.batch([queue.enqueueWorkStatement(gateway, PROJECT_ID, "retrieval_index", "snapshot:a", now)]);
  await gateway.batch([queue.enqueueWorkStatement(gateway, PROJECT_ID, "retrieval_index", "snapshot:a", now)]);
  assert.equal(rows(database).length, 1, "replayed outbox statements never duplicate work");

  const [leased] = await queue.claimDueWork(gateway, PROJECT_ID, { limit: 4 });
  assert.equal(leased.state, "running");
  assert.equal((await queue.claimDueWork(gateway, PROJECT_ID, { limit: 4 })).length, 0, "a live lease is never shared");

  // The runner dies; its lease expires and another pass reclaims the item.
  database.prepare("UPDATE work_items SET lease_expires_at = '2000-01-01T00:00:00.000Z'").run();
  const [reclaimed] = await queue.claimDueWork(gateway, PROJECT_ID, { limit: 4 });
  assert.notEqual(reclaimed.leaseToken, leased.leaseToken);
  assert.equal(await queue.completeWork(gateway, leased), false, "the stale runner cannot overwrite the new owner");
  assert.equal(await queue.completeWork(gateway, reclaimed, { status: "indexed" }), true);
  const [done] = rows(database);
  assert.equal(done.state, "succeeded");
  assert.equal(done.lease_token, null);
  assert.equal(JSON.parse(done.progress_json).status, "indexed");
  database.close();
});

test("attempts back off, defer is free and the budget or a permanent cause fails the item", async () => {
  const { database, gateway } = fresh();
  await queue.requestWork(gateway, PROJECT_ID, "claim_relations", ["snapshot:b", "snapshot:c"]);
  const [first] = await queue.claimDueWork(gateway, PROJECT_ID, { limit: 1 });
  await queue.deferWork(gateway, first, 0, { batchId: "relation-batch:sha256:x" });
  const leased = await queue.claimDueWork(gateway, PROJECT_ID, { limit: 4 });
  const item = leased.find((entry) => entry.subjectId === first.subjectId);
  const other = leased.find((entry) => entry.subjectId !== first.subjectId);
  assert.equal(item.attempts, 0, "yielding while polling never consumes an attempt");
  assert.equal(item.progress.batchId, "relation-batch:sha256:x", "progress survives across passes");

  await queue.failWorkAttempt(gateway, item, failure(true));
  let stored = rows(database).find((row) => row.subject_id === item.subjectId);
  assert.equal(stored.state, "pending");
  assert.equal(stored.attempts, 1);
  assert.ok(Date.parse(stored.next_attempt_at) >= Date.now() + 50_000, "claim-relation retries back off");
  assert.equal(JSON.parse(stored.last_error_json).code, "STORE_BUSY");

  database.prepare(`UPDATE work_items SET next_attempt_at = '2000-01-01T00:00:00.000Z',
    attempts = max_attempts - 1 WHERE subject_id = ?`).run(item.subjectId);
  const [last] = await queue.claimDueWork(gateway, PROJECT_ID, { limit: 4 });
  assert.equal(last.subjectId, item.subjectId);
  await queue.failWorkAttempt(gateway, last, failure(true));
  stored = rows(database).find((row) => row.subject_id === item.subjectId);
  assert.equal(stored.state, "failed", "the attempt budget is bounded");

  await queue.failWorkAttempt(gateway, other, failure(false, "STORE_CONSTRAINT_VIOLATION"));
  assert.equal(rows(database).find((row) => row.subject_id === other.subjectId).state, "failed",
    "a permanent cause is never retried");
  database.close();
});

test("requesting work re-arms finished items but never disturbs pending or running work", async () => {
  const { database, gateway } = fresh();
  await queue.requestWork(gateway, PROJECT_ID, "retrieval_index", ["s1", "s2", "s3"]);
  const leased = await queue.claimDueWork(gateway, PROJECT_ID, { limit: 3 });
  await queue.completeWork(gateway, leased.find((item) => item.subjectId === "s1"));
  await queue.failWorkAttempt(gateway, leased.find((item) => item.subjectId === "s2"), failure(false));
  const running = leased.find((item) => item.subjectId === "s3");

  const rearmed = await queue.requestWork(gateway, PROJECT_ID, "retrieval_index", ["s1", "s2", "s3", "s4"]);
  assert.equal(rearmed, 3, "two finished items are re-armed and one missing item is created");
  const byId = new Map(rows(database).map((row) => [row.subject_id, row]));
  assert.equal(byId.get("s1").state, "pending");
  assert.equal(byId.get("s2").state, "pending");
  assert.equal(byId.get("s2").attempts, 0);
  assert.equal(byId.get("s2").last_error_json, null);
  assert.equal(byId.get("s3").state, "running");
  assert.equal(byId.get("s3").lease_token, running.leaseToken);
  assert.equal(byId.get("s4").state, "pending");

  assert.equal(await queue.cancelWork(gateway, PROJECT_ID, "retrieval_index", "s3", "user_cancelled"), true);
  assert.equal(await queue.completeWork(gateway, running), false, "a cancelled lease cannot complete");
  assert.equal(new Map(rows(database).map((row) => [row.subject_id, row])).get("s3").state, "cancelled");

  const view = await queue.listRecentWork(gateway, PROJECT_ID, { openLimit: 2, finishedLimit: 1 });
  assert.equal(view.open.length, 2);
  assert.equal(view.finished.length, 1);
  assert.deepEqual(view.counts.find((entry) => entry.state === "pending"), { kind: "retrieval_index", state: "pending", count: 3 });
  assert.throws(() => database.prepare(`INSERT INTO work_items (id,project_id,kind,subject_id,state,priority,max_attempts,
    next_attempt_at,created_at,updated_at) VALUES ('x','p','unknown','s','pending',1,1,'n','n','n')`).run(), /CHECK/u);
  database.close();
});

function scope() {
  return { location: ["Basin X"], time: null, formation: [], sample_type: ["shale"], method: ["ICP-MS"],
    condition: [], measurement: ["Hg concentration"], uncertainty: null, entities: ["Hg"] };
}

async function storedPaper(database, label, { claims = true } = {}) {
  const bytes = new TextEncoder().encode(`%PDF-1.7\nwork queue ${label}`);
  const sha = await sha256Hex(bytes);
  const context = {
    artifactId: `artifact:00000000-0000-4000-8000-${sha.slice(0, 12)}`,
    versionId: `artifact-version:sha256:${sha}`,
    sha,
    fileName: `${label}.pdf`,
    locator: `upload://${label}.pdf`,
  };
  database.prepare("INSERT INTO artifacts (id,project_id,preferred_name,artifact_type,created_by) VALUES (?,?,?,?,?)")
    .run(context.artifactId, PROJECT_ID, context.fileName, "paper", "owner@deepshovel.local");
  database.prepare(`INSERT INTO artifact_versions (id,artifact_id,version_number,sha256,source_locator,media_type,
    original_file_name,byte_size,object_key,client_request_id,request_hash,created_by)
    VALUES (?,?,1,?,?,?,?,?,?,?,?,?)`).run(context.versionId, context.artifactId, sha, context.locator,
    "application/pdf", context.fileName, bytes.byteLength, `artifacts/${sha}`, `upload-${label}`, sha, "owner@deepshovel.local");
  const claim = await createAtomicClaim({ proposition: `The ${label} anomaly supports volcanism.`, source_id: "claim:1", scope: scope() });
  const graph = {
    schema_version: EVIDENCE_GRAPH_SCHEMA_VERSION,
    paper_id: `paper:${sha}`,
    artifacts: [{ kind: "artifact", id: context.artifactId, canonical_name: context.fileName, artifact_type: "paper" }],
    artifact_versions: [{ kind: "artifact_version", id: context.versionId, artifact_id: context.artifactId,
      version: context.versionId, locator: context.locator, sha256: sha, media_type: "application/pdf" }],
    anchors: [{ kind: "source_anchor", id: "anchor:1", artifact_version_id: context.versionId, anchor_type: "text",
      selector: { page: 1, section: "Results", paragraph: 1, table: null, row_start: null, row_end: null,
        row_range_label: null, column_names: [], figure: null, series: null, char_start: null, char_end: null,
        region: null, region_label: null, dataset_path: null }, quote: null, quote_sha256: null }],
    visual_evidence: [],
    observations: claims ? [{ kind: "observation", id: "observation:1", statement: "Hg increases.",
      observation_type: "trend", anchor_ids: ["anchor:1"], scope: scope(), value: null }] : [],
    assumptions: [],
    reasoning: claims ? [{ kind: "reasoning", id: "reasoning:1", statement: "Consistent with volcanic input.",
      reasoning_type: "abductive", anchor_ids: [], scope: scope(), limitations: [] }] : [],
    claims: claims ? [claim] : [],
    edges: claims ? [
      { kind: "edge", id: "edge:ground", relation: "grounds", source_id: "anchor:1", target_id: "observation:1", rationale: null, confidence: 1, scope_overlap: null },
      { kind: "edge", id: "edge:premise", relation: "premise_for", source_id: "observation:1", target_id: "reasoning:1", rationale: null, confidence: 0.8, scope_overlap: null },
      { kind: "edge", id: "edge:infer", relation: "infers", source_id: "reasoning:1", target_id: claim.id, rationale: null, confidence: 0.7, scope_overlap: null },
    ] : [],
    assessments: [],
    snapshots: [],
  };
  const graphSha256 = await hashEvidenceGraph(graph);
  return parseEvidenceImportEnvelope({
    graph,
    root_artifact_version_id: context.versionId,
    extraction: { run_id: `extraction:sha256:${graphSha256}`, model: "gpt-5.6-luna", prompt_version: "compiler-extractor/1.0.0" },
    verification: claims ? {
      run_id: `verification:sha256:${graphSha256}`,
      model: "gpt-5.6-luna",
      prompt_version: "compiler-verifier/1.0.0",
      report: {
        schema_version: "1.0", paper_id: graph.paper_id, graph_sha256: graphSha256, verifier_model: "gpt-5.6-luna",
        assessments: [{ id: "assessment:1", claim_id: "claim:1", scoped_claim_id: claim.id, verdict: "supported",
          confidence: 0.8, evidence_ids: ["anchor:1", "observation:1", "reasoning:1"], rationale: "Path holds.", limitations: [] }],
        warnings: [],
      },
    } : null,
  }, new Date().toISOString());
}

test("an import writes its background work in the same atomic batch as the snapshot", async () => {
  const { database } = fresh();
  const imported = await storedPaper(database, "outbox");
  await persistEvidenceImport(imported, "owner@deepshovel.local");
  assert.deepEqual(rows(database, "SELECT kind, subject_id AS subjectId, state FROM work_items ORDER BY kind"), [
    { kind: "claim_relations", subjectId: imported.snapshotId, state: "pending" },
    { kind: "retrieval_index", subjectId: imported.snapshotId, state: "pending" },
  ]);
  await persistEvidenceImport(imported, "owner@deepshovel.local");
  assert.equal(rows(database).length, 2, "an idempotent replay adds no work");

  const empty = await storedPaper(database, "zero-claims", { claims: false });
  await persistEvidenceImport(empty, "owner@deepshovel.local");
  assert.deepEqual(rows(database, `SELECT kind FROM work_items WHERE subject_id = '${empty.snapshotId}'`), [{ kind: "retrieval_index" }],
    "zero-Claim snapshots still become searchable but need no relation analysis");
  database.close();
});

test("background work is only reachable from loopback with the service credential", async () => {
  const request = (url, headers = {}) => new Request(url, { method: "POST", headers });
  await assertInternalWorker(request("http://127.0.0.1:8100/api/internal/work", { authorization: `Bearer ${SECRET}` }));
  for (const [url, headers, code] of [
    ["http://127.0.0.1:8100/api/internal/work", {}, "WORKER_UNAUTHORIZED"],
    ["http://127.0.0.1:8100/api/internal/work", { authorization: "Bearer wrong-secret-0123456789abcdefghij" }, "WORKER_UNAUTHORIZED"],
    ["http://127.0.0.1:8100/api/internal/work", { authorization: `Bearer ${SECRET}`, "cf-ray": "1" }, "MAINTENANCE_LOCAL_ONLY"],
    ["http://127.0.0.1:8100/api/internal/work", { authorization: `Bearer ${SECRET}`, host: "deepshovel.example" }, "MAINTENANCE_LOCAL_ONLY"],
    ["https://deepshovel.example/api/internal/work", { authorization: `Bearer ${SECRET}` }, "MAINTENANCE_LOCAL_ONLY"],
  ]) {
    await assert.rejects(assertInternalWorker(request(url, headers)), (error) => error.code === code, `${url} ${JSON.stringify(headers)}`);
  }
});

test("durable failure causes separate transient, permanent and unknown errors", () => {
  assert.deepEqual(durableFailure(new Error("D1_ERROR: database is locked: SQLITE_BUSY")),
    { code: "STORE_BUSY", message: "Evidence store was temporarily busy", retryable: true });
  assert.equal(durableFailure(new Error("string or blob too big: SQLITE_TOOBIG")).retryable, false);
  assert.equal(durableFailure(new Error("CHECK constraint failed: x")).retryable, false);
  assert.equal(durableFailure(new Error("UNIQUE constraint failed: x")).retryable, true);
  assert.equal(durableFailure(new Error("no such table: x")).retryable, false);
  assert.equal(durableFailure(new TypeError("x is undefined")).retryable, false);
  assert.equal(durableFailure(new Error("something new")).retryable, true);
  assert.equal(durableFailure(new ApiError(422, "COMPILER_PROVENANCE_MISMATCH", "mismatch")).retryable, false);
  assert.equal(durableFailure(new ApiError(503, "COMPILER_UNAVAILABLE", "down")).retryable, true);
  assert.equal(durableFailure(new ApiError(502, "UPSTREAM_X", "x", { retryable: true })).retryable, true);
});

async function compileRow(database, label, overrides) {
  const bytes = new TextEncoder().encode(`%PDF-1.7\ncompile ${label}`);
  const sha = await sha256Hex(bytes);
  const artifactId = `artifact:00000000-0000-4000-8000-${sha.slice(0, 12)}`;
  const versionId = `artifact-version:sha256:${sha}`;
  database.prepare("INSERT INTO artifacts (id,project_id,preferred_name,artifact_type,created_by) VALUES (?,?,?,?,?)")
    .run(artifactId, PROJECT_ID, label, "paper", "owner@deepshovel.local");
  database.prepare(`INSERT INTO artifact_versions (id,artifact_id,version_number,sha256,source_locator,media_type,
    original_file_name,byte_size,object_key,client_request_id,request_hash,created_by)
    VALUES (?,?,1,?,?,?,?,?,?,?,?,?)`).run(versionId, artifactId, sha, `upload://${label}.pdf`, "application/pdf",
    `${label}.pdf`, bytes.byteLength, `artifacts/${sha}`, `upload-${label}`, sha, "owner@deepshovel.local");
  const row = {
    id: crypto.randomUUID(), projectId: PROJECT_ID, artifactVersionId: versionId, upstreamJobId: crypto.randomUUID(),
    status: "running", stage: "extracting", progress: 68, timingsJson: "{}", errorJson: null, snapshotId: null,
    clientRequestId: `request-${label}`, requestHash: sha, createdBy: "owner@deepshovel.local",
    createdAt: "2026-10-18T00:00:00.000Z", updatedAt: "2026-10-18T00:00:01.000Z", expiresAt: "2026-10-19T00:00:00.000Z",
    ...overrides,
  };
  database.prepare(`INSERT INTO compile_jobs (id,project_id,artifact_version_id,upstream_job_id,status,stage,progress,
    timings_json,error_json,snapshot_id,client_request_id,request_hash,created_by,created_at,updated_at,expires_at,
    import_lease_until,next_import_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    row.id, row.projectId, row.artifactVersionId, row.upstreamJobId, row.status, row.stage, row.progress,
    row.timingsJson, row.errorJson, row.snapshotId, row.clientRequestId, row.requestHash, row.createdBy,
    row.createdAt, row.updatedAt, row.expiresAt, row.importLeaseUntil ?? null, row.nextImportAt ?? null);
  return row;
}

function upstream(row, status, updatedAt = row.updatedAt) {
  return { id: row.upstreamJobId, status, createdAt: row.createdAt, updatedAt, expiresAt: row.expiresAt,
    stageTimingsMs: { queued: null, parsing: null, extracting: null, validating: null, total: null }, error: null,
    links: { self: "", result: "" } };
}

test("the compile worker imports finished results first and skips unchanged or waiting jobs", async () => {
  const { database } = fresh();
  const unchanged = await compileRow(database, "unchanged");
  const progressed = await compileRow(database, "progressed", { createdAt: "2026-10-17T00:00:00.000Z" });
  const finished = await compileRow(database, "finished", { createdAt: "2026-10-18T05:00:00.000Z" });
  const reservation = await compileRow(database, "reservation", {
    status: "queued", stage: "queued", upstreamJobId: "pending:x", expiresAt: new Date(Date.now() + 60_000).toISOString(),
  });
  const backingOff = await compileRow(database, "backing-off", {
    stage: "importing", progress: 97, nextImportAt: new Date(Date.now() + 60_000).toISOString(),
  });
  await compileRow(database, "due-import", { stage: "importing", progress: 97, nextImportAt: "2000-01-01T00:00:00.000Z" });
  const statuses = new Map([
    [unchanged.upstreamJobId, upstream(unchanged, "extracting")],
    [progressed.upstreamJobId, upstream(progressed, "validating", "2026-10-18T00:00:02.000Z")],
    [finished.upstreamJobId, upstream(finished, "completed", "2026-10-18T00:00:03.000Z")],
  ]);
  const lookups = [];
  const advanced = [];
  const summary = await advanceCompileJobs({
    deadline: Date.now() + 10_000,
    compiler: { async listJobs(ids) { lookups.push(ids); return ids.map((id) => statuses.get(id)).filter(Boolean); } },
    advance: async (row, state) => {
      advanced.push([row.clientRequestId, state?.status ?? null]);
      return { ...row, status: state?.status === "completed" ? "completed" : row.status };
    },
  });
  assert.equal(lookups.length, 1, "one batched status read covers every attached job");
  assert.deepEqual(new Set(lookups[0]), new Set([unchanged.upstreamJobId, progressed.upstreamJobId, finished.upstreamJobId]));
  assert.deepEqual(advanced, [
    ["request-finished", "completed"],
    ["request-due-import", null],
    ["request-progressed", "validating"],
  ], "imports first, then due retries, then progress; unchanged, waiting and backing-off jobs are skipped");
  assert.equal(summary.active, 6);
  assert.equal(summary.completed, 1);
  assert.ok(!advanced.some(([id]) => id === reservation.clientRequestId || id === backingOff.clientRequestId));
  database.close();
});
