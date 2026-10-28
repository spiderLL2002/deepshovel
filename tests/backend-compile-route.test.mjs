import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

import {
  EVIDENCE_GRAPH_SCHEMA_VERSION,
  createAtomicClaim,
  hashEvidenceGraph,
  sha256Hex,
} from "../lib/evidence-graph/index.ts";

const bindings = {
  DB: null,
  BUCKET: null,
  COMPILER_BASE_URL: "http://127.0.0.1:8011",
  COMPILER_SHARED_SECRET: "route-test-compiler-secret-0123456789",
};
globalThis.__compileRouteTestBindings = bindings;

const loader = `
let rootUrl;
export function initialize(data) { rootUrl = data.rootUrl; }
export async function resolve(specifier, context, nextResolve) {
  if (specifier === "cloudflare:workers") {
    return {
      url: "data:text/javascript,export const env=globalThis.__compileRouteTestBindings",
      shortCircuit: true,
    };
  }
  if (specifier.startsWith("@/")) {
    let path = specifier.slice(2);
    if (path === "lib/evidence-graph" || path === "lib/evidence-retrieval") path += "/index.ts";
    else if (!/\\.[cm]?[jt]sx?$/.test(path)) path += ".ts";
    return { url: new URL(path, rootUrl).href, shortCircuit: true };
  }
  return nextResolve(specifier, context);
}`;
register(`data:text/javascript,${encodeURIComponent(loader)}`, {
  parentURL: import.meta.url,
  data: { rootUrl: new URL("../", import.meta.url).href },
});

const { DELETE, GET, POST } = await import("../app/api/compile/route.ts");
const { updateCompileJobFromUpstream } = await import("../app/api/compile/compile-job-store.ts");
const { refreshCompileJob } = await import("../app/api/compile/compile-store.ts");
const { safeError } = await import("../lib/server/evidence-store.ts");
const upstreamJobId = "4d63b26c-6548-4a62-8218-14aa13580cb2";

class Statement {
  args = [];

  constructor(db, sql) {
    this.db = db;
    this.sql = sql.replace(/\s+/g, " ").trim();
  }

  bind(...args) {
    this.args = args;
    return this;
  }

  first() { return this.db.first(this.sql, this.args); }
  all() { return this.db.all(this.sql, this.args); }
  run() { return this.db.run(this.sql, this.args); }
}

class MemoryD1 {
  compileJobs = new Map();
  snapshots = new Map();
  verifications = new Map();
  manifests = new Map();
  audits = new Map();
  preparedSql = [];

  constructor(artifact) {
    this.artifact = artifact;
  }

  prepare(sql) { this.preparedSql.push(sql); return new Statement(this, sql); }

  async first(sql, args) {
    if (sql.includes("FROM artifact_versions")) {
      return sql.includes("AS sourceLocator")
        ? { ...this.artifact, sourceLocator: this.artifact.locator }
        : { ...this.artifact };
    }
    if (sql.includes("FROM compile_jobs")) {
      // D1 returns row copies; never alias the stored state into callers.
      const rows = [...this.compileJobs.values()].map((row) => ({ ...row }));
      if (sql.includes("client_request_id = ?")) {
        return rows.find((row) => row.projectId === args[0] && row.createdBy === args[1] && row.clientRequestId === args[2]) ?? null;
      }
      if (sql.includes("upstream_job_id = ?")) {
        return rows.find((row) => row.projectId === args[0] && row.upstreamJobId === args[1]) ?? null;
      }
      if (sql.includes("created_by = ? AND id = ?")) {
        return rows.find((row) => row.projectId === args[0] && row.createdBy === args[1] && row.id === args[2]) ?? null;
      }
      if (sql.includes("WHERE project_id = ? AND id = ? LIMIT 1")) {
        return rows.find((row) => row.projectId === args[0] && row.id === args[1]) ?? null;
      }
    }
    if (sql.includes("FROM evidence_graph_snapshots")) {
      const rows = [...this.snapshots.values()];
      if (sql.includes("graph_sha256 = ?")) return rows.find((row) => row.graphSha256 === args[1]) ?? null;
      if (sql.includes("extractor_run_id = ?")) return rows.find((row) => row.extractorRunId === args[1]) ?? null;
    }
    if (sql.includes("FROM verification_runs")) {
      const rows = [...this.verifications.values()];
      if (sql.includes("verifier_run_id = ?")) return rows.find((row) => row.verifierRunId === args[1]) ?? null;
      if (sql.includes("report_sha256 = ?")) return rows.find((row) => row.reportSha256 === args[1]) ?? null;
    }
    if (sql.includes("FROM evidence_search_manifests")) return this.manifests.get(args[1]) ?? null;
    return null;
  }

  async all(sql, args) {
    if (sql.includes("FROM artifact_versions")) {
      const versionIds = JSON.parse(args[1]);
      return { results: versionIds.includes(this.artifact.versionId) ? [{
        id: this.artifact.versionId,
        artifactId: this.artifact.artifactId,
        sha256: this.artifact.sha256,
        mediaType: this.artifact.mediaType,
        sourceLocator: this.artifact.locator,
        artifactType: this.artifact.artifactType,
      }] : [] };
    }
    if (sql.includes("FROM evidence_graph_snapshots")) {
      return { results: [...this.snapshots.values()].filter((row) => (
        row.graphSha256 === args[1] || row.extractorRunId === args[2]
      )) };
    }
    if (sql.includes("FROM verification_runs")) {
      return { results: [...this.verifications.values()].filter((row) => (
        row.verifierRunId === args[1] || row.reportSha256 === args[2]
      )) };
    }
    if (!sql.includes("FROM compile_jobs")) return { results: [] };
    const actorRows = [...this.compileJobs.values()]
      .filter((row) => row.projectId === args[0] && row.createdBy === args[1])
      .sort((left, right) => right.createdAt.localeCompare(left.createdAt));
    const terminalIds = new Set(actorRows
      .filter((row) => row.status === "completed" || row.status === "failed")
      .slice(0, 25)
      .map((row) => row.id));
    const results = actorRows
      .filter((row) => row.status === "queued" || row.status === "running" || terminalIds.has(row.id))
      .map((row) => ({ ...row, fileName: this.artifact.fileName }));
    return { results };
  }

  async run(sql, args) {
    if (sql.includes("UPDATE compile_jobs SET status = 'failed'") && this.beforeFailJob) {
      const callback = this.beforeFailJob;
      this.beforeFailJob = null;
      await callback();
    }
    const changes = this.execute(sql, args);
    return { success: true, meta: { changes } };
  }

  async batch(statements) {
    const backup = {
      compileJobs: structuredClone(this.compileJobs),
      snapshots: structuredClone(this.snapshots),
      verifications: structuredClone(this.verifications),
      manifests: structuredClone(this.manifests),
      audits: structuredClone(this.audits),
    };
    try {
      return statements.map((statement) => ({
        success: true,
        meta: { changes: this.execute(statement.sql, statement.args) },
      }));
    } catch (error) {
      Object.assign(this, backup);
      throw error;
    }
  }

  execute(sql, args) {
    if (sql.startsWith("INSERT INTO compile_jobs")) return this.insertCompileJob(args);
    if (sql.startsWith("UPDATE compile_jobs SET upstream_job_id")) return this.attachUpstream(args);
    if (sql.includes("UPDATE compile_jobs SET status = 'running'")) return this.claimImport(args);
    if (sql.startsWith("UPDATE compile_jobs SET import_attempts = import_attempts + 1")) return this.recordImportRetry(args);
    if (sql.startsWith("UPDATE compile_jobs SET status = ?, stage = ?")) return this.updateCompilerState(args);
    if (sql.includes("UPDATE compile_jobs SET status = 'failed'")) return this.failJob(sql, args);
    if (sql.startsWith("UPDATE compile_jobs SET updated_at = ?")) return this.reclaimImport(args);
    if (sql.includes("UPDATE compile_jobs SET status = 'completed'")) return this.completeJob(args);
    if (sql.startsWith("INSERT OR IGNORE INTO audit_events")) {
      if (sql.includes("FROM compile_jobs")
        && ![...this.compileJobs.values()].some((row) => row.projectId === args[6] && row.id === args[7])) return 0;
      return this.insertAudit(args, true);
    }
    if (sql.startsWith("INSERT INTO audit_events")) return this.insertGraphAudit(sql, args);
    if (sql.startsWith("INSERT INTO evidence_graph_snapshots")) return this.insertSnapshot(args);
    if (sql.startsWith("INSERT INTO verification_runs")) return this.insertVerification(args);
    if (sql.startsWith("INSERT INTO evidence_search_manifests")) {
      this.manifests.set(args[0], { snapshotId: args[0], indexVersion: args[2] });
      return 1;
    }
    if (sql.startsWith("INSERT INTO evidence_")) return 1;
    return 0;
  }

  insertCompileJob(args) {
    const [id, projectId, artifactVersionId, upstreamId, status, stage, progress, timingsJson,
      clientRequestId, requestHash, createdBy, createdAt, updatedAt, expiresAt] = args;
    if (this.compileJobs.has(id) || [...this.compileJobs.values()].some((row) =>
      (row.projectId === projectId && row.upstreamJobId === upstreamId)
      || (row.projectId === projectId && row.createdBy === createdBy && row.clientRequestId === clientRequestId))) {
      throw new Error("UNIQUE compile_jobs");
    }
    this.compileJobs.set(id, {
      id, projectId, artifactVersionId, upstreamJobId: upstreamId, status, stage, progress,
      timingsJson, errorJson: null, snapshotId: null, clientRequestId, requestHash,
      createdBy, createdAt, updatedAt, expiresAt,
    });
    return 1;
  }

  attachUpstream(args) {
    const [upstreamId, status, stage, progress, timingsJson, errorJson, createdAt,
      updatedAt, expiresAt, projectId, id, expectedUpstream, expectedUpdatedAt] = args;
    const row = this.compileJobs.get(id);
    if (!row || row.projectId !== projectId || row.upstreamJobId !== expectedUpstream
      || row.updatedAt !== expectedUpdatedAt || row.status !== "queued" || row.stage !== "queued") return 0;
    if ([...this.compileJobs.values()].some((other) => other.id !== id && other.projectId === projectId && other.upstreamJobId === upstreamId)) {
      throw new Error("UNIQUE upstream_job_id");
    }
    Object.assign(row, { upstreamJobId: upstreamId, status, stage, progress, timingsJson, errorJson, createdAt, updatedAt, expiresAt });
    return 1;
  }

  claimImport(args) {
    const [timingsJson, updatedAt, expiresAt, projectId, id, expectedUpdatedAt] = args;
    const row = this.compileJobs.get(id);
    if (!row || row.projectId !== projectId || row.updatedAt !== expectedUpdatedAt
      || ["completed", "failed"].includes(row.status) || row.stage === "importing") return 0;
    Object.assign(row, { status: "running", stage: "importing", progress: 97, timingsJson, errorJson: null, updatedAt, expiresAt,
      importLeaseUntil: new Date(Date.now() + 5 * 60 * 1000).toISOString() });
    return 1;
  }

  recordImportRetry(args) {
    const [importErrorJson, nextImportAt, updatedAt, projectId, id, expectedUpdatedAt] = args;
    const row = this.compileJobs.get(id);
    if (!row || row.projectId !== projectId || row.updatedAt !== expectedUpdatedAt
      || row.status !== "running" || row.stage !== "importing") return 0;
    Object.assign(row, { importAttempts: (row.importAttempts ?? 0) + 1, importErrorJson, nextImportAt, importLeaseUntil: null, updatedAt });
    return 1;
  }

  updateCompilerState(args) {
    const [status, stage, progress, timingsJson, errorJson, updatedAt, expiresAt,
      projectId, id, expectedUpdatedAt, expectedStatus, expectedStage] = args;
    const row = this.compileJobs.get(id);
    if (!row || row.projectId !== projectId || row.updatedAt !== expectedUpdatedAt
      || row.status !== expectedStatus || row.stage !== expectedStage
      || ["completed", "failed"].includes(row.status) || row.stage === "importing") return 0;
    Object.assign(row, { status, stage, progress, timingsJson, errorJson, updatedAt, expiresAt });
    return 1;
  }

  failJob(sql, args) {
    const [errorJson, updatedAt, projectId, id, expectedUpdatedAt, at] = args;
    const row = this.compileJobs.get(id);
    if (!row || row.projectId !== projectId || row.updatedAt !== expectedUpdatedAt
      || ["completed", "failed"].includes(row.status)
      || (sql.includes("stage <> 'importing'") && row.stage === "importing")) return 0;
    // An idle-import cancellation must lose to a live import lease.
    if (sql.includes("import_lease_until IS NULL OR import_lease_until <=")
      && (row.stage !== "importing" || (row.importLeaseUntil && row.importLeaseUntil > at))) return 0;
    Object.assign(row, { status: "failed", stage: "failed", progress: 100, errorJson, updatedAt, importLeaseUntil: null, nextImportAt: null });
    return 1;
  }

  reclaimImport(args) {
    const [updatedAt, projectId, id, expectedUpdatedAt, at] = args;
    const row = this.compileJobs.get(id);
    if (!row || row.projectId !== projectId || row.updatedAt !== expectedUpdatedAt
      || row.status !== "running" || row.stage !== "importing"
      || (row.importLeaseUntil && row.importLeaseUntil > at)
      || (row.nextImportAt && row.nextImportAt > at)) return 0;
    Object.assign(row, { updatedAt, importLeaseUntil: new Date(Date.now() + 5 * 60 * 1000).toISOString() });
    return 1;
  }

  completeJob(args) {
    const [snapshotId, updatedAt, projectId, id, expectedUpdatedAt] = args;
    const row = this.compileJobs.get(id);
    if (!row || row.projectId !== projectId || row.updatedAt !== expectedUpdatedAt
      || row.status !== "running" || row.stage !== "importing") return 0;
    Object.assign(row, { status: "completed", stage: "completed", progress: 100, snapshotId, errorJson: null, updatedAt,
      importLeaseUntil: null, nextImportAt: null });
    return 1;
  }

  insertAudit(args, ignore) {
    const [id, projectId, eventType, entityId, actor, payloadJson] = args;
    if (this.audits.has(id)) {
      if (ignore) return 0;
      throw new Error("UNIQUE audit_events");
    }
    this.audits.set(id, { id, projectId, eventType, entityId, actor, payloadJson });
    return 1;
  }

  insertGraphAudit(sql, args) {
    const eventType = sql.includes("verification.imported") ? "verification.imported" : "evidence_graph.imported";
    return this.insertAudit([args[0], args[1], eventType, args[2], args[3], args[4]], false);
  }

  insertSnapshot(args) {
    const [id, projectId, rootArtifactVersionId, schemaVersion, graphSha256, graphJson,
      countsJson, extractorRunId, extractorModel, extractorPromptVersion, createdBy, createdAt] = args;
    if ([...this.snapshots.values()].some((row) => row.graphSha256 === graphSha256 || row.extractorRunId === extractorRunId)) {
      throw new Error("UNIQUE evidence_graph_snapshots");
    }
    this.snapshots.set(id, { id, projectId, rootArtifactVersionId, schemaVersion, graphSha256,
      graphJson, countsJson, extractorRunId, extractorModel, extractorPromptVersion, createdBy, createdAt });
    return 1;
  }

  insertVerification(args) {
    const [id, projectId, snapshotId, verifierRunId, verifierModel, verifierPromptVersion,
      reportSha256, reportJson, createdBy, createdAt] = args;
    if ([...this.verifications.values()].some((row) => row.verifierRunId === verifierRunId || row.reportSha256 === reportSha256)) {
      throw new Error("UNIQUE verification_runs");
    }
    this.verifications.set(id, { id, projectId, snapshotId, verifierRunId, verifierModel,
      verifierPromptVersion, reportSha256, reportJson, createdBy, createdAt });
    return 1;
  }
}

class MemoryR2 {
  constructor(key, bytes) {
    this.key = key;
    this.bytes = new Uint8Array(bytes);
  }

  async get(key) {
    if (key !== this.key) return null;
    return {
      size: this.bytes.byteLength,
      arrayBuffer: async () => this.bytes.slice().buffer,
    };
  }
}

function scope() {
  return {
    location: ["Basin X"], time: null, formation: [], sample_type: ["shale"],
    method: ["ICP-MS"], condition: [], measurement: ["Hg concentration"],
    uncertainty: null, entities: ["Hg"],
  };
}

async function graphEnvelope(context, overrides = {}) {
  const claim = await createAtomicClaim({
    proposition: "The Hg anomaly supports coeval volcanic activity.",
    source_id: "claim:compiler:1",
    scope: scope(),
  });
  const graph = {
    schema_version: EVIDENCE_GRAPH_SCHEMA_VERSION,
    paper_id: overrides.paperId ?? context.paperId,
    artifacts: [{ kind: "artifact", id: context.artifactId,
      canonical_name: overrides.canonicalName ?? context.fileName, artifact_type: context.artifactType }],
    artifact_versions: [{ kind: "artifact_version", id: context.versionId, artifact_id: context.artifactId,
      version: overrides.versionLabel ?? context.versionId,
      locator: Object.hasOwn(overrides, "locator") ? overrides.locator : context.locator,
      sha256: context.sha256, media_type: context.mediaType }],
    anchors: [{ kind: "source_anchor", id: "anchor:compiler:1", artifact_version_id: context.versionId,
      anchor_type: "text", selector: { page: 1, section: "Results", paragraph: 1, table: null,
        row_start: null, row_end: null, row_range_label: null, column_names: [], figure: null,
        series: null, char_start: null, char_end: null, region: null, region_label: null,
        dataset_path: null }, quote: null, quote_sha256: null }],
    visual_evidence: [],
    observations: [{ kind: "observation", id: "observation:compiler:1",
      statement: "Hg increases in the target interval.", observation_type: "trend",
      anchor_ids: ["anchor:compiler:1"], scope: scope(), value: null }],
    assumptions: [],
    reasoning: [{ kind: "reasoning", id: "reasoning:compiler:1",
      statement: "The proxy anomaly is consistent with volcanic input.", reasoning_type: "abductive",
      anchor_ids: [], scope: scope(), limitations: ["The proxy is not unique."] }],
    claims: [claim],
    edges: [
      { kind: "edge", id: "edge:compiler:ground", relation: "grounds", source_id: "anchor:compiler:1", target_id: "observation:compiler:1", rationale: null, confidence: 1, scope_overlap: null },
      { kind: "edge", id: "edge:compiler:premise", relation: "premise_for", source_id: "observation:compiler:1", target_id: "reasoning:compiler:1", rationale: null, confidence: 0.8, scope_overlap: null },
      { kind: "edge", id: "edge:compiler:infer", relation: "infers", source_id: "reasoning:compiler:1", target_id: claim.id, rationale: null, confidence: 0.7, scope_overlap: null },
    ],
    assessments: [], snapshots: [],
  };
  const graphSha256 = await hashEvidenceGraph(graph);
  const defaultAssessments = [{ id: "assessment:compiler:1", claim_id: "claim:compiler:1",
    scoped_claim_id: claim.id, verdict: "qualified", confidence: 0.7,
    evidence_ids: ["anchor:compiler:1", "observation:compiler:1", "reasoning:compiler:1"],
    rationale: "The evidence path supports a qualified conclusion.", limitations: ["The proxy is not unique."] }];
  const report = {
    schema_version: "1.0", paper_id: graph.paper_id, graph_sha256: graphSha256,
    verifier_model: "gpt-5.6-luna",
    assessments: Object.hasOwn(overrides, "assessments") ? overrides.assessments : defaultAssessments,
    warnings: [],
  };
  return {
    graph,
    root_artifact_version_id: context.versionId,
    extraction: { run_id: `extraction:sha256:${graphSha256}`, model: "gpt-5.6-luna", prompt_version: "compiler-extractor/1.0.0" },
    verification: { run_id: `verification:sha256:${graphSha256}`, model: "gpt-5.6-luna", prompt_version: "compiler-verifier/1.0.0", report },
  };
}

async function storedFixture(bytes, fileName, mediaType, artifactType) {
  const sha256 = await sha256Hex(bytes);
  const context = {
    paperId: `paper:${sha256}`,
    artifactId: "artifact:compile-route",
    artifactType,
    versionId: `artifact-version:sha256:${sha256}`,
    sha256,
    mediaType,
    locator: `upload://${encodeURIComponent(fileName)}`,
    fileName,
    objectKey: `artifacts/${sha256}`,
    byteSize: bytes.byteLength,
  };
  return { bytes, context };
}

async function fixture() {
  const stored = await storedFixture(
    new TextEncoder().encode("%PDF-1.7\ncompile route fixture"),
    "compile-route.pdf",
    "application/pdf",
    "paper",
  );
  const { bytes, context } = stored;
  return { bytes, context, result: await graphEnvelope(context) };
}

function jobEnvelope(status, error = null, id = upstreamJobId) {
  return {
    schema_version: "compiler-job/1.0",
    job: {
      id,
      status,
      created_at: "2026-08-15T05:00:00Z",
      updated_at: status === "queued" ? "2026-08-15T05:00:01Z" : "2026-08-15T05:00:02Z",
      expires_at: "2026-08-16T05:00:00Z",
      stage_timings_ms: { queued: 10, parsing: 20, extracting: 30, validating: 40, total: status === "completed" || status === "failed" ? 100 : null },
      error,
      links: { self: `/v1/compile-jobs/${id}`, result: `/v1/compile-jobs/${id}/result` },
    },
  };
}

function jsonResponse(value, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } });
}

function compileRequest(versionId, actor = "alice@example.test", key = "compile-request-1") {
  return new Request("http://localhost/api/compile", {
    method: "POST",
    headers: { "content-type": "application/json", "idempotency-key": key, "oai-authenticated-user-email": actor },
    body: JSON.stringify({ artifact_version_id: versionId }),
  });
}

/** One worker step for an actor's job, reported like the former advancing GET. */
async function poll(id, actor = "alice@example.test") {
  try {
    return Response.json(await refreshCompileJob(actor, id));
  } catch (error) {
    return safeError(error);
  }
}

function pollRequest(id, actor = "alice@example.test") {
  return new Request(`http://localhost/api/compile?job_id=${encodeURIComponent(id)}`, {
    headers: { "oai-authenticated-user-email": actor },
  });
}

function cancelRequest(body, actor = "alice@example.test") {
  return new Request("http://localhost/api/compile", {
    method: "DELETE",
    headers: { "content-type": "application/json", "oai-authenticated-user-email": actor },
    body: JSON.stringify(body),
  });
}

function installFixture(fixtureValue, fetchImpl, bytes = fixtureValue.bytes) {
  bindings.DB = new MemoryD1(fixtureValue.context);
  bindings.BUCKET = new MemoryR2(fixtureValue.context.objectKey, bytes);
  globalThis.fetch = fetchImpl;
  return bindings.DB;
}

test("real Compile route deduplicates submission, polls once, and imports one immutable graph", async () => {
  const value = await fixture();
  const calls = { submit: 0, status: 0, result: 0 };
  const db = installFixture(value, async (url, init) => {
    assert.equal(new Headers(init.headers).get("authorization"), "Bearer route-test-compiler-secret-0123456789");
    const path = new URL(url).pathname;
    if (init.method === "POST") {
      assert.equal(init.body.get("verify"), "true", "Compile must request the verification required for import");
      calls.submit += 1;
      await Promise.resolve();
      return jsonResponse(jobEnvelope("queued"));
    }
    if (path.endsWith("/result")) {
      calls.result += 1;
      return jsonResponse(value.result);
    }
    calls.status += 1;
    return jsonResponse(jobEnvelope("completed"));
  });

  const [left, right] = await Promise.all([
    POST(compileRequest(value.context.versionId)),
    POST(compileRequest(value.context.versionId)),
  ]);
  const submissions = await Promise.all([left.json(), right.json()]);
  assert.deepEqual([left.status, right.status].sort(), [200, 202]);
  assert.equal(calls.submit, 1);
  assert.equal(db.compileJobs.size, 1);
  const localJobId = submissions[0].job.id;
  assert.equal(localJobId, submissions[1].job.id);
  assert.notEqual(localJobId, upstreamJobId);
  const reusedKey = await POST(compileRequest(`artifact-version:sha256:${"b".repeat(64)}`));
  assert.equal(reusedKey.status, 409);
  assert.equal((await reusedKey.json()).error.code, "IDEMPOTENCY_KEY_REUSED");

  const [pollA, pollB] = await Promise.all([
    poll(localJobId),
    poll(localJobId),
  ]);
  assert.equal(pollA.status, 200);
  assert.equal(pollB.status, 200);
  assert.equal(calls.result, 1);
  assert.equal(db.snapshots.size, 1);
  assert.equal(db.verifications.size, 1);
  assert.equal(db.compileJobs.get(localJobId).status, "completed");
  assert.deepEqual(JSON.parse([...db.snapshots.values()][0].graphJson).assessments, []);
  assert.equal([...db.audits.values()].filter((item) => item.eventType === "compile.completed").length, 1);

  for (const [id, audit] of db.audits) {
    if (audit.eventType === "compile.submitted" || audit.eventType === "compile.completed") db.audits.delete(id);
  }
  const replay = await poll(localJobId);
  assert.equal((await replay.json()).job.status, "completed");
  assert.equal([...db.audits.values()].filter((item) => item.eventType === "compile.submitted").length, 1);
  assert.equal([...db.audits.values()].filter((item) => item.eventType === "compile.completed").length, 1);

  const otherActor = await poll(localJobId, "mallory@example.test");
  assert.equal(otherActor.status, 404);
  assert.equal((await otherActor.json()).error.code, "COMPILE_JOB_NOT_FOUND");

  // Observers never drive work: GET only reads the D1 state, actor-scoped.
  const before = { ...calls };
  const observed = await GET(pollRequest(localJobId));
  assert.equal(observed.status, 200);
  assert.equal((await observed.json()).job.status, "completed");
  assert.deepEqual(calls, before);
  const foreign = await GET(pollRequest(localJobId, "mallory@example.test"));
  assert.equal(foreign.status, 404);

  const crossSite = await GET(new Request(`http://localhost/api/compile?job_id=${localJobId}`, {
    headers: { origin: "https://attacker.example", "oai-authenticated-user-email": "alice@example.test" },
  }));
  assert.equal(crossSite.status, 403);
  assert.equal((await crossSite.json()).error.code, "CROSS_ORIGIN_WRITE_REJECTED");

  const crossSitePost = await POST(new Request("http://localhost/api/compile", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "sec-fetch-site": "cross-site",
      "oai-authenticated-user-email": "alice@example.test",
    },
    body: JSON.stringify({ artifact_version_id: value.context.versionId }),
  }));
  assert.equal(crossSitePost.status, 403);
  assert.equal((await crossSitePost.json()).error.code, "CROSS_ORIGIN_WRITE_REJECTED");

});

test("Compile job listing keeps every active job beyond the recent terminal window", async () => {
  const value = await fixture();
  const db = installFixture(value, async () => { throw new Error("list must not contact Compiler"); });
  const addJob = (index, status) => {
    const id = `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`;
    const timestamp = new Date(Date.UTC(2026, 7, 15, 0, 0, index)).toISOString();
    db.compileJobs.set(id, {
      id,
      projectId: "DEEPSHOVEL-PROJECT",
      artifactVersionId: value.context.versionId,
      upstreamJobId: `upstream:${id}`,
      status,
      stage: status === "running" ? "extracting" : "completed",
      progress: status === "running" ? 50 : 100,
      timingsJson: "{}",
      errorJson: null,
      snapshotId: status === "completed" ? `snapshot:${id}` : null,
      clientRequestId: `request:${id}`,
      requestHash: "hash",
      createdBy: "alice@example.test",
      createdAt: timestamp,
      updatedAt: timestamp,
      expiresAt: new Date(Date.UTC(2026, 7, 16, 0, 0, index)).toISOString(),
    });
  };
  for (let index = 0; index < 30; index += 1) addJob(index, "running");
  for (let index = 100; index < 130; index += 1) addJob(index, "completed");

  const response = await GET(new Request("http://localhost/api/compile", {
    headers: { "oai-authenticated-user-email": "alice@example.test" },
  }));
  const payload = await response.json();
  assert.equal(response.status, 200);
  assert.equal(payload.jobs.filter((job) => job.status === "running").length, 30);
  assert.equal(payload.jobs.filter((job) => job.status === "completed").length, 25);
  assert.ok(payload.jobs.every((job) => job.fileName === value.context.fileName));
});

test("Compile cancellation is actor-scoped and marks selected or all active jobs terminal", async () => {
  const value = await fixture();
  let upstreamDeletes = 0;
  let upstreamSubmissions = 0;
  const db = installFixture(value, async (_url, init) => {
    if (init.method === "POST") {
      upstreamSubmissions += 1;
      const id = `4d63b26c-6548-4a62-8218-${String(upstreamSubmissions).padStart(12, "0")}`;
      return jsonResponse(jobEnvelope("queued", null, id));
    }
    if (init.method !== "DELETE") throw new Error("cancellation must only delete upstream jobs");
    upstreamDeletes += 1;
    return new Response(null, { status: 204 });
  });
  const first = await POST(compileRequest(value.context.versionId, "alice@example.test", "cancel-1"));
  const firstJob = (await first.json()).job;
  db.preparedSql.length = 0;
  const selected = await DELETE(cancelRequest({ job_ids: [firstJob.id] }));
  assert.equal(selected.status, 200);
  assert.equal((await selected.json()).cancelled.length, 1);
  assert.equal(db.compileJobs.get(firstJob.id).status, "failed");
  assert.equal(JSON.parse(db.compileJobs.get(firstJob.id).errorJson).code, "COMPILE_CANCELLED");
  assert.equal(db.preparedSql.filter((sql) => sql.includes("id IN (SELECT value FROM json_each(?))")).length, 1);

  const second = await POST(compileRequest(value.context.versionId, "alice@example.test", "cancel-2"));
  const secondJob = (await second.json()).job;
  const all = await DELETE(cancelRequest({ all: true }));
  assert.equal(all.status, 200);
  assert.equal((await all.json()).cancelled.length, 1);
  assert.equal(db.compileJobs.get(secondJob.id).status, "failed");
  assert.equal(upstreamDeletes, 4);

  const otherActor = await DELETE(cancelRequest({ job_ids: [secondJob.id] }, "mallory@example.test"));
  assert.equal(otherActor.status, 404);
});

test("the final importing commit stage cannot be cancelled into a failed job", async () => {
  const value = await fixture();
  let releaseResult;
  let signalResultRequested;
  let upstreamDeletes = 0;
  const resultRequested = new Promise((resolve) => { signalResultRequested = resolve; });
  const resultResponse = new Promise((resolve) => { releaseResult = resolve; });
  const db = installFixture(value, async (url, init) => {
    if (init.method === "POST") return jsonResponse(jobEnvelope("queued"));
    if (init.method === "DELETE") {
      upstreamDeletes += 1;
      return new Response(null, { status: 204 });
    }
    if (new URL(url).pathname.endsWith("/result")) {
      signalResultRequested();
      return resultResponse;
    }
    return jsonResponse(jobEnvelope("completed"));
  });

  const submitted = await POST(compileRequest(
    value.context.versionId,
    "alice@example.test",
    "compile-import-cancel-race",
  ));
  const localJobId = (await submitted.json()).job.id;
  const polling = poll(localJobId);
  await resultRequested;
  assert.equal(db.compileJobs.get(localJobId).stage, "importing");

  const cancellation = await DELETE(cancelRequest({ job_ids: [localJobId] }));
  assert.equal(cancellation.status, 200);
  assert.deepEqual((await cancellation.json()).cancelled, []);
  assert.equal(db.compileJobs.get(localJobId).stage, "importing");
  assert.equal(upstreamDeletes, 0);

  releaseResult(jsonResponse(value.result));
  const completed = await polling;
  assert.equal((await completed.json()).job.status, "completed");
  assert.equal(db.compileJobs.get(localJobId).status, "completed");
  assert.equal(db.snapshots.size, 1);
});

test("cancellation loses safely when an extracting job concurrently claims import", async () => {
  const value = await fixture();
  let upstreamDeletes = 0;
  const db = installFixture(value, async (_url, init) => {
    if (init.method === "POST") return jsonResponse(jobEnvelope("queued"));
    if (init.method === "DELETE") {
      upstreamDeletes += 1;
      return new Response(null, { status: 204 });
    }
    throw new Error("no poll is expected in this state-race fixture");
  });
  const submitted = await POST(compileRequest(
    value.context.versionId,
    "alice@example.test",
    "compile-cancel-import-cas",
  ));
  const localJobId = (await submitted.json()).job.id;
  const row = db.compileJobs.get(localJobId);
  Object.assign(row, { status: "running", stage: "extracting", progress: 68 });

  let releaseFailure;
  let signalFailure;
  const failureReached = new Promise((resolve) => { signalFailure = resolve; });
  const failureRelease = new Promise((resolve) => { releaseFailure = resolve; });
  db.beforeFailJob = async () => {
    signalFailure();
    await failureRelease;
  };

  const cancelling = DELETE(cancelRequest({ job_ids: [localJobId] }));
  await failureReached;
  Object.assign(row, {
    status: "running", stage: "importing", progress: 97,
  });
  releaseFailure();

  const response = await cancelling;
  assert.equal(response.status, 200);
  assert.deepEqual((await response.json()).cancelled, []);
  assert.equal(row.status, "running");
  assert.equal(row.stage, "importing");
  assert.equal(upstreamDeletes, 0);
  assert.ok(db.preparedSql.some((sql) => sql.includes("stage <> 'importing'")));
});

test("Compile API boundary exposes nested D1 contention as a retryable store error", async () => {
  const previous = { DB: bindings.DB, BUCKET: bindings.BUCKET };
  bindings.DB = {
    prepare() {
      return {
        bind() { return this; },
        async all() {
          throw new Error("D1 query failed", {
            cause: new Error("D1_ERROR: database is locked: SQLITE_BUSY"),
          });
        },
      };
    },
  };
  bindings.BUCKET = {};
  const originalWarn = console.warn;
  console.warn = () => {};
  let response;
  try {
    response = await GET(new Request("http://localhost/api/compile", {
      headers: { "oai-authenticated-user-email": "alice@example.test" },
    }));
  } finally {
    console.warn = originalWarn;
    Object.assign(bindings, previous);
  }

  const body = await response.json();
  assert.equal(response.status, 503);
  assert.equal(response.headers.get("retry-after"), "1");
  assert.equal(body.error.code, "STORE_BUSY");
  assert.equal(body.error.details.retryable, true);
  assert.doesNotMatch(JSON.stringify(body), /sqlite|database is locked/i);
});

test("Compile route rejects corrupt R2 bytes before contacting Compiler", async () => {
  const value = await fixture();
  let calls = 0;
  const corrupt = new TextEncoder().encode("%PDF-1.7\ncorrupt bytes same-ish");
  const db = installFixture(value, async () => { calls += 1; return jsonResponse(jobEnvelope("queued")); }, corrupt);
  db.artifact.byteSize = corrupt.byteLength;

  const response = await POST(compileRequest(value.context.versionId));
  assert.equal(response.status, 409);
  assert.equal((await response.json()).error.code, "ARTIFACT_HASH_MISMATCH");
  assert.equal(calls, 0);
  assert.equal(db.compileJobs.size, 0);
});

test("Compile route accepts all supported source media and rejects unsupported text", async () => {
  const cases = [
    [new TextEncoder().encode("%PDF-1.7\nfixture"), "paper.pdf", "application/pdf", "paper"],
    [new Uint8Array([0x50, 0x4b, 0x03, 0x04, 1]), "paper.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "paper"],
    [new Uint8Array([0x50, 0x4b, 0x03, 0x04, 2]), "data.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "dataset"],
    [new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1]), "figure.png", "image/png", "figure"],
    [new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 1]), "figure.jpg", "image/jpeg", "figure"],
    [new Uint8Array([0x49, 0x49, 0x2a, 0x00, 1]), "scan.tiff", "image/tiff", "figure"],
  ];
  for (const [bytes, fileName, mediaType, artifactType] of cases) {
    const stored = await storedFixture(bytes, fileName, mediaType, artifactType);
    const value = { ...stored, result: await graphEnvelope(stored.context) };
    let submits = 0;
    const db = installFixture(value, async (url, init) => {
      if (init.method === "POST") {
        submits += 1;
        return jsonResponse(jobEnvelope("queued"));
      }
      if (new URL(url).pathname.endsWith("/result")) return jsonResponse(value.result);
      return jsonResponse(jobEnvelope("completed"));
    });
    const response = await POST(compileRequest(value.context.versionId, "alice@example.test", `compile-${fileName}`));
    assert.equal(response.status, 202, fileName);
    assert.equal(submits, 1, fileName);
    assert.equal(db.compileJobs.size, 1, fileName);
    const localJobId = (await response.json()).job.id;
    const completed = await poll(localJobId);
    assert.equal((await completed.json()).job.status, "completed", fileName);
    assert.equal(db.snapshots.size, 1, fileName);
  }

  const unsupported = await storedFixture(
    new TextEncoder().encode("plain text"),
    "notes.txt",
    "text/plain",
    "paper",
  );
  let unsupportedCalls = 0;
  const unsupportedDb = installFixture(unsupported, async () => {
    unsupportedCalls += 1;
    return jsonResponse(jobEnvelope("queued"));
  });
  const rejected = await POST(compileRequest(unsupported.context.versionId, "alice@example.test", "compile-unsupported"));
  assert.equal(rejected.status, 415);
  assert.equal((await rejected.json()).error.code, "COMPILER_INPUT_TYPE_UNSUPPORTED");
  assert.equal(unsupportedCalls, 0);
  assert.equal(unsupportedDb.compileJobs.size, 0);

  const crossType = await storedFixture(
    new Uint8Array([0x50, 0x4b, 0x03, 0x04, 3]),
    "paper-became-data.xlsx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "paper",
  );
  let crossTypeCalls = 0;
  const crossTypeDb = installFixture(crossType, async () => {
    crossTypeCalls += 1;
    return jsonResponse(jobEnvelope("queued"));
  });
  const crossTypeResponse = await POST(compileRequest(
    crossType.context.versionId,
    "alice@example.test",
    "compile-cross-type",
  ));
  assert.equal(crossTypeResponse.status, 409);
  assert.equal((await crossTypeResponse.json()).error.code, "ARTIFACT_TYPE_MISMATCH");
  assert.equal(crossTypeCalls, 0);
  assert.equal(crossTypeDb.compileJobs.size, 0);

  const oversized = await storedFixture(
    new TextEncoder().encode("%PDF-1.7\nsmall body with forged metadata"),
    "oversized.pdf",
    "application/pdf",
    "paper",
  );
  oversized.context.byteSize = 50 * 1024 * 1024 + 1;
  let oversizedCalls = 0;
  const oversizedDb = installFixture(oversized, async () => {
    oversizedCalls += 1;
    return jsonResponse(jobEnvelope("queued"));
  });
  const tooLarge = await POST(compileRequest(oversized.context.versionId, "alice@example.test", "compile-oversized"));
  assert.equal(tooLarge.status, 413);
  assert.equal((await tooLarge.json()).error.code, "COMPILER_INPUT_TOO_LARGE");
  assert.equal(oversizedCalls, 0);
  assert.equal(oversizedDb.compileJobs.size, 0);
});

test("Compile polling fails closed on protocol and provenance forgery", async () => {
  const protocolValue = await fixture();
  const wrongId = "f7acdb0c-1700-4d85-a852-31ed4f2670de";
  const protocolDb = installFixture(protocolValue, async (_url, init) =>
    jsonResponse(init.method === "POST" ? jobEnvelope("queued") : jobEnvelope("completed", null, wrongId)));
  const submitted = await POST(compileRequest(protocolValue.context.versionId));
  const localJobId = (await submitted.json()).job.id;
  const protocol = await poll(localJobId);
  const protocolBody = await protocol.json();
  assert.equal(protocol.status, 200);
  assert.equal(protocolBody.job.status, "failed");
  assert.equal(protocolBody.job.error.code, "COMPILER_JOB_MISMATCH");
  assert.equal(protocolDb.snapshots.size, 0);

  const forgeries = [
    { paperId: "paper:forged" },
    { locator: null },
    { canonicalName: "renamed-by-upstream.pdf" },
    { versionLabel: "forged-version-label" },
  ];
  for (const [index, forgery] of forgeries.entries()) {
    const provenanceValue = await fixture();
    provenanceValue.result = await graphEnvelope(provenanceValue.context, forgery);
    const provenanceDb = installFixture(provenanceValue, async (url, init) => {
      if (init.method === "POST") return jsonResponse(jobEnvelope("queued"));
      if (new URL(url).pathname.endsWith("/result")) return jsonResponse(provenanceValue.result);
      return jsonResponse(jobEnvelope("completed"));
    });
    const provenanceSubmit = await POST(compileRequest(
      provenanceValue.context.versionId,
      "alice@example.test",
      `compile-provenance-${index}`,
    ));
    const provenanceJobId = (await provenanceSubmit.json()).job.id;
    const provenance = await poll(provenanceJobId);
    const provenanceBody = await provenance.json();
    assert.equal(provenance.status, 200);
    assert.equal(provenanceBody.job.status, "failed");
    assert.equal(provenanceBody.job.error.code, "COMPILER_PROVENANCE_MISMATCH");
    assert.equal(provenanceDb.snapshots.size, 0);
  }
});

test("Compile result rejects invalid run metadata and incomplete verifier coverage", async () => {
  const cases = [
    {
      code: "COMPILER_EXTRACTION_CONTRACT_MISMATCH",
      mutate(result) { result.extraction.prompt_version = "untrusted-extractor/1"; },
    },
    {
      code: "COMPILER_VERIFICATION_CONTRACT_MISMATCH",
      mutate(result) { result.verification.run_id = "verification:untrusted:1"; },
    },
    {
      code: "INVALID_VERIFICATION_REPORT",
      mutate(result) { result.verification.report.assessments = []; },
    },
    {
      code: "INVALID_VERIFICATION_REPORT",
      mutate(result) { result.verification.report.assessments[0].scoped_claim_id = null; },
    },
    {
      code: "COMPILER_RESULT_CONTRACT_MISMATCH",
      mutate(result) { delete result.root_artifact_version_id; },
    },
  ];
  for (const [index, resultCase] of cases.entries()) {
    const value = await fixture();
    resultCase.mutate(value.result);
    const db = installFixture(value, async (url, init) => {
      if (init.method === "POST") return jsonResponse(jobEnvelope("queued"));
      if (new URL(url).pathname.endsWith("/result")) return jsonResponse(value.result);
      return jsonResponse(jobEnvelope("completed"));
    });
    const submitted = await POST(compileRequest(
      value.context.versionId,
      "alice@example.test",
      `compile-result-contract-${index}`,
    ));
    const localJobId = (await submitted.json()).job.id;
    const polled = await poll(localJobId);
    const body = await polled.json();
    assert.equal(body.job.status, "failed");
    assert.equal(body.job.error.code, resultCase.code);
    assert.equal(db.snapshots.size, 0);
  }
});

test("Compiler preserves verifier verdicts as assessments without rejecting the graph", async () => {
  for (const verdict of ["contradicted", "insufficient_evidence", "uncertain"]) {
    const value = await fixture();
    value.result.extraction.prompt_version = "compiler-extractor/1.0.0";
    value.result.verification.prompt_version = "compiler-verifier/1.0.0";
    value.result.verification.report.assessments[0].verdict = verdict;
    const db = installFixture(value, async (url, init) => {
      if (init.method === "POST") return jsonResponse(jobEnvelope("queued"));
      if (new URL(url).pathname.endsWith("/result")) return jsonResponse(value.result);
      return jsonResponse(jobEnvelope("completed"));
    });

    const submitted = await POST(compileRequest(
      value.context.versionId,
      "alice@example.test",
      `compile-verdict-${verdict}`,
    ));
    const polled = await poll((await submitted.json()).job.id);

    assert.equal((await polled.json()).job.status, "completed");
    assert.equal(db.snapshots.size, 1);
    assert.equal(db.verifications.size, 1);
    assert.equal(
      JSON.parse([...db.verifications.values()][0].reportJson).assessments[0].verdict,
      verdict,
    );
  }

  const incomplete = await fixture();
  incomplete.result.extraction.prompt_version = "compiler-extractor/1.0.0";
  incomplete.result.verification.prompt_version = "compiler-verifier/1.0.0";
  incomplete.result.verification.report.assessments = [];
  const incompleteDb = installFixture(incomplete, async (url, init) => {
    if (init.method === "POST") return jsonResponse(jobEnvelope("queued"));
    if (new URL(url).pathname.endsWith("/result")) return jsonResponse(incomplete.result);
    return jsonResponse(jobEnvelope("completed"));
  });
  const incompleteSubmitted = await POST(compileRequest(
    incomplete.context.versionId,
    "alice@example.test",
    "compile-incomplete-verification",
  ));
  const incompletePolled = await poll((await incompleteSubmitted.json()).job.id);
  const incompleteBody = await incompletePolled.json();

  assert.equal(incompleteBody.job.status, "failed");
  assert.equal(incompleteBody.job.error.code, "INVALID_VERIFICATION_REPORT");
  assert.equal(incompleteDb.snapshots.size, 0);
  assert.equal(incompleteDb.verifications.size, 0);
});

test("Compiler permits null verification only for a zero-claim result", async () => {
  const empty = await fixture();
  empty.result.graph.claims = [];
  empty.result.graph.observations = [];
  empty.result.graph.reasoning = [];
  empty.result.graph.edges = [];
  const emptyGraphSha256 = await hashEvidenceGraph(empty.result.graph);
  empty.result.extraction = {
    run_id: `extraction:sha256:${emptyGraphSha256}`,
    model: "gpt-5.6-luna",
    prompt_version: "compiler-extractor/1.0.0",
  };
  empty.result.verification = null;
  const emptyDb = installFixture(empty, async (url, init) => {
    if (init.method === "POST") return jsonResponse(jobEnvelope("queued"));
    if (new URL(url).pathname.endsWith("/result")) return jsonResponse(empty.result);
    return jsonResponse(jobEnvelope("completed"));
  });
  const emptySubmitted = await POST(compileRequest(
    empty.context.versionId,
    "alice@example.test",
    "compile-empty",
  ));
  const emptyPolled = await poll((await emptySubmitted.json()).job.id);

  assert.equal((await emptyPolled.json()).job.status, "completed");
  assert.equal(emptyDb.snapshots.size, 1);
  assert.equal(emptyDb.verifications.size, 0);

  const nonempty = await fixture();
  nonempty.result.extraction.prompt_version = "compiler-extractor/1.0.0";
  nonempty.result.verification = null;
  const nonemptyDb = installFixture(nonempty, async (url, init) => {
    if (init.method === "POST") return jsonResponse(jobEnvelope("queued"));
    if (new URL(url).pathname.endsWith("/result")) return jsonResponse(nonempty.result);
    return jsonResponse(jobEnvelope("completed"));
  });
  const nonemptySubmitted = await POST(compileRequest(
    nonempty.context.versionId,
    "alice@example.test",
    "compile-nonempty-null-verification",
  ));
  const nonemptyPolled = await poll((await nonemptySubmitted.json()).job.id);
  const nonemptyBody = await nonemptyPolled.json();

  assert.equal(nonemptyBody.job.status, "failed");
  assert.equal(nonemptyBody.job.error.code, "COMPILER_VERIFICATION_MISSING");
  assert.equal(nonemptyDb.snapshots.size, 0);
  assert.equal(nonemptyDb.verifications.size, 0);
});

test("a failed Compiler job may renew expiry for terminal retention", async () => {
  const value = await fixture();
  const failure = { code: "MODEL_OUTPUT_INVALID", message: "Model output failed validation", retryable: false };
  const renewedExpiry = "2026-08-16T05:00:02Z";
  const db = installFixture(value, async (_url, init) => {
    if (init.method === "POST") return jsonResponse(jobEnvelope("queued"));
    const terminal = jobEnvelope("failed", failure);
    terminal.job.expires_at = renewedExpiry;
    return jsonResponse(terminal);
  });
  const submitted = await POST(compileRequest(value.context.versionId));
  const localJobId = (await submitted.json()).job.id;

  const first = await poll(localJobId);
  const firstBody = await first.json();
  assert.equal(first.status, 200);
  assert.equal(firstBody.job.status, "failed");
  assert.equal(firstBody.job.error.code, "MODEL_OUTPUT_INVALID");
  assert.equal(firstBody.job.expiresAt, renewedExpiry);
  const second = await poll(localJobId);
  assert.equal((await second.json()).job.status, "failed");
  assert.equal([...db.audits.values()].filter((item) => item.eventType === "compile.failed").length, 1);
});

test("a completed Compiler job may renew expiry before its result is imported", async () => {
  const value = await fixture();
  const renewedExpiry = "2026-08-16T05:00:02Z";
  const db = installFixture(value, async (url, init) => {
    if (init.method === "POST") return jsonResponse(jobEnvelope("queued"));
    if (new URL(url).pathname.endsWith("/result")) return jsonResponse(value.result);
    const terminal = jobEnvelope("completed");
    terminal.job.expires_at = renewedExpiry;
    return jsonResponse(terminal);
  });
  const submitted = await POST(compileRequest(
    value.context.versionId,
    "alice@example.test",
    "compile-renewed-completion",
  ));
  const localJobId = (await submitted.json()).job.id;

  const completed = await poll(localJobId);
  const body = await completed.json();
  assert.equal(completed.status, 200);
  assert.equal(body.job.status, "completed");
  assert.equal(body.job.expiresAt, renewedExpiry);
  assert.equal(db.snapshots.size, 1);
});

test("Compiler expiry changes remain bounded and terminal-only", async () => {
  const cases = [
    {
      name: "non-terminal renewal",
      status: "extracting",
      error: null,
      expiresAt: "2026-08-16T05:00:01Z",
    },
    {
      name: "terminal shortening",
      status: "failed",
      error: { code: "MODEL_OUTPUT_INVALID", message: "Invalid output", retryable: false },
      expiresAt: "2026-08-16T04:59:59Z",
    },
    {
      name: "terminal renewal beyond the retention limit",
      status: "failed",
      error: { code: "MODEL_OUTPUT_INVALID", message: "Invalid output", retryable: false },
      expiresAt: "2026-08-16T06:00:03Z",
    },
  ];

  for (const [index, expiryCase] of cases.entries()) {
    const value = await fixture();
    const db = installFixture(value, async (_url, init) => {
      if (init.method === "POST") return jsonResponse(jobEnvelope("queued"));
      const envelope = jobEnvelope(expiryCase.status, expiryCase.error);
      envelope.job.expires_at = expiryCase.expiresAt;
      return jsonResponse(envelope);
    });
    const submitted = await POST(compileRequest(
      value.context.versionId,
      "alice@example.test",
      `compile-invalid-expiry-${index}`,
    ));
    const localJobId = (await submitted.json()).job.id;

    const rejected = await poll(localJobId);
    const body = await rejected.json();
    // An upstream that rewrote immutable metadata can never converge, so the
    // job is reported failed once instead of erroring on every later pass.
    assert.equal(rejected.status, 200, expiryCase.name);
    assert.equal(body.job.status, "failed", expiryCase.name);
    assert.equal(body.job.error.code, "COMPILER_JOB_MISMATCH", expiryCase.name);
    assert.equal(body.job.error.retryable, false, expiryCase.name);
    assert.equal(db.compileJobs.get(localJobId).expiresAt, "2026-08-16T05:00:00Z", expiryCase.name);
    assert.equal(db.snapshots.size, 0, expiryCase.name);
  }
});

test("a non-JSON upstream 503 stays retryable and does not fail the local job", async () => {
  const value = await fixture();
  let statuses = 0;
  const db = installFixture(value, async (url, init) => {
    if (init.method === "POST") return jsonResponse(jobEnvelope("queued"));
    if (new URL(url).pathname.endsWith("/result")) return jsonResponse(value.result);
    statuses += 1;
    if (statuses === 1) {
      return new Response("<html>temporarily unavailable</html>", {
        status: 503,
        headers: { "content-type": "text/html" },
      });
    }
    return jsonResponse(jobEnvelope("completed"));
  });
  const submitted = await POST(compileRequest(value.context.versionId));
  const localJobId = (await submitted.json()).job.id;

  const transient = await poll(localJobId);
  const transientBody = await transient.json();
  assert.equal(transient.status, 503);
  assert.equal(transientBody.error.code, "UPSTREAM_REQUEST_FAILED");
  assert.equal(db.compileJobs.get(localJobId).status, "queued");
  assert.equal([...db.audits.values()].filter((item) => item.eventType === "compile.failed").length, 0);

  const recovered = await poll(localJobId);
  assert.equal((await recovered.json()).job.status, "completed");
});

test("the gateway import lease recovers after the bodyless GET transport retry is exhausted", async () => {
  const value = await fixture();
  let statusPolls = 0;
  let resultCalls = 0;
  const futureUpdatedAt = "2026-08-16T04:00:00Z";
  const db = installFixture(value, async (url, init) => {
    if (init.method === "POST") return jsonResponse(jobEnvelope("queued"));
    if (new URL(url).pathname.endsWith("/result")) {
      resultCalls += 1;
      if (resultCalls <= 2) throw new Error("temporary result transport failure");
      return jsonResponse(value.result);
    }
    statusPolls += 1;
    const status = jobEnvelope(statusPolls === 1 ? "validating" : "completed");
    status.job.updated_at = futureUpdatedAt;
    return jsonResponse(status);
  });
  const submitted = await POST(compileRequest(value.context.versionId));
  const localJobId = (await submitted.json()).job.id;

  const validating = await poll(localJobId);
  assert.equal((await validating.json()).job.stage, "validating");
  assert.equal(db.compileJobs.get(localJobId).updatedAt, futureUpdatedAt);

  const transient = await poll(localJobId);
  assert.equal(transient.status, 200);
  const transientJob = (await transient.json()).job;
  assert.equal(transientJob.stage, "importing");
  assert.equal(transientJob.importRetry.attempts, 1);
  assert.equal(transientJob.importRetry.lastError.code, "COMPILER_UNAVAILABLE");
  const importing = db.compileJobs.get(localJobId);
  assert.equal(importing.stage, "importing");
  assert.equal(importing.importLeaseUntil, null, "a failed attempt releases its lease at once");
  assert.ok(Date.parse(importing.nextImportAt) > Date.now(), "the next attempt waits for its backoff");

  const early = await poll(localJobId);
  assert.equal((await early.json()).job.stage, "importing");
  assert.equal(resultCalls, 2, "no attempt starts before the backoff is due");

  importing.nextImportAt = "2000-01-01T00:00:00.000Z";
  const recovered = await poll(localJobId);
  assert.equal((await recovered.json()).job.status, "completed");
  assert.equal(resultCalls, 3);
});

test("an unknown submission outcome is reconciled by key and never replayed", async () => {
  const value = await fixture();
  let posts = 0;
  let lookup = "unavailable";
  const lookups = [];
  const db = installFixture(value, async (url, init) => {
    if (init.method === "POST") {
      posts += 1;
      assert.match(new Headers(init.headers).get("idempotency-key"), /^[0-9a-f-]{36}$/);
      assert.match(init.body.get("submit_not_after_ms"), /^\d+$/);
      throw new Error("route-test-compiler-secret-0123456789 must never cross the API boundary");
    }
    const target = new URL(url);
    if (target.searchParams.has("idempotency_key")) {
      lookups.push(target.searchParams.get("idempotency_key"));
      if (lookup === "unavailable") throw new Error("connection refused");
      if (lookup === "missing") {
        return jsonResponse({ error: { code: "JOB_NOT_FOUND", message: "compile job was not found", retryable: false } }, 404);
      }
      return jsonResponse(jobEnvelope("parsing"));
    }
    return jsonResponse(jobEnvelope("parsing"));
  });

  const first = await POST(compileRequest(value.context.versionId));
  assert.equal(first.status, 202);
  const firstBody = await first.json();
  assert.doesNotMatch(JSON.stringify(firstBody), /route-test-compiler-secret-0123456789/);
  assert.equal(firstBody.job.status, "queued");
  const reservation = db.compileJobs.get(firstBody.job.id);
  assert.match(reservation.upstreamJobId, /^pending:/);

  const replay = await POST(compileRequest(value.context.versionId));
  const replayBody = await replay.json();
  assert.equal(replay.status, 200);
  assert.equal(replayBody.duplicate, true);
  assert.equal(posts, 1, "the browser replay never re-sends the paid Compiler POST");

  // Before the Compiler-side deadline nothing is concluded.
  assert.equal((await (await poll(reservation.id)).json()).job.status, "queued");
  assert.deepEqual(lookups, []);

  reservation.expiresAt = "2000-01-01T00:00:00.000Z";
  const unreachable = await poll(reservation.id);
  assert.equal(unreachable.status, 503, "an unreachable Compiler leaves the outcome open");
  assert.equal(db.compileJobs.get(reservation.id).status, "queued");

  lookup = "accepted";
  const adopted = await (await poll(reservation.id)).json();
  assert.equal(adopted.job.status, "running");
  assert.equal(adopted.job.stage, "parsing");
  assert.equal(db.compileJobs.get(reservation.id).upstreamJobId, upstreamJobId);
  // One bodyless lookup is retried once on a transport failure; never a POST.
  assert.deepEqual(lookups, [reservation.id, reservation.id, reservation.id]);
  assert.equal(posts, 1);
});

test("a submission the Compiler never recorded fails as retryable after its deadline", async () => {
  const value = await fixture();
  let posts = 0;
  const db = installFixture(value, async (url, init) => {
    if (init.method === "POST") {
      posts += 1;
      throw new Error("timeout");
    }
    if (new URL(url).searchParams.has("idempotency_key")) {
      return jsonResponse({ error: { code: "JOB_NOT_FOUND", message: "compile job was not found", retryable: false } }, 404);
    }
    throw new Error("unexpected request");
  });
  const submitted = await (await POST(compileRequest(value.context.versionId))).json();
  db.compileJobs.get(submitted.job.id).expiresAt = "2000-01-01T00:00:00.000Z";
  const failed = await (await poll(submitted.job.id)).json();
  assert.equal(failed.job.status, "failed");
  assert.equal(failed.job.error.code, "COMPILER_SUBMISSION_ABANDONED");
  assert.equal(failed.job.error.retryable, true);
  assert.equal(posts, 1);
  assert.equal([...db.audits.values()].filter((item) => item.eventType === "compile.failed").length, 1);
});

test("an upstream job ID cannot be attached to two gateway jobs", async () => {
  const value = await fixture();
  let calls = 0;
  let deletes = 0;
  const db = installFixture(value, async (_url, init) => {
    if (init.method === "DELETE") {
      deletes += 1;
      return new Response(null, { status: 204 });
    }
    calls += 1;
    return jsonResponse(jobEnvelope("queued"));
  });

  const first = await POST(compileRequest(value.context.versionId, "alice@example.test", "compile-owner-1"));
  assert.equal(first.status, 202);
  const second = await POST(compileRequest(value.context.versionId, "alice@example.test", "compile-owner-2"));
  const secondBody = await second.json();
  assert.equal(second.status, 502);
  assert.equal(secondBody.error.code, "COMPILER_JOB_ID_REUSED");
  assert.equal(calls, 2);

  const jobs = [...db.compileJobs.values()];
  assert.equal(jobs.length, 2);
  assert.equal(jobs.filter((row) => row.upstreamJobId === upstreamJobId).length, 1);
  assert.equal(jobs.filter((row) => row.status === "failed").length, 1);
  assert.equal([...db.audits.values()].filter((item) => item.eventType === "compile.failed").length, 1);
  assert.equal(deletes, 2, "the rejected duplicate upstream job is cancelled and purged");
});

test("a late submission response cannot resurrect an expired reservation", async () => {
  const value = await fixture();
  let resolveUpstream;
  let upstreamDeletes = 0;
  const upstream = new Promise((resolve) => { resolveUpstream = resolve; });
  const db = installFixture(value, async (url, init) => {
    if (init.method === "DELETE") {
      upstreamDeletes += 1;
      return new Response(null, { status: 204 });
    }
    if (new URL(url).searchParams.has("idempotency_key")) {
      return jsonResponse({ error: { code: "JOB_NOT_FOUND", message: "compile job was not found", retryable: false } }, 404);
    }
    return upstream;
  });

  const pendingSubmission = POST(compileRequest(
    value.context.versionId,
    "alice@example.test",
    "compile-late-submit",
  ));
  for (let index = 0; index < 2000 && db.compileJobs.size === 0; index += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
  assert.equal(db.compileJobs.size, 1);
  const reservation = [...db.compileJobs.values()][0];
  reservation.expiresAt = "2000-01-01T00:00:00.000Z";

  const expired = await poll(reservation.id);
  assert.equal((await expired.json()).job.status, "failed");
  resolveUpstream(jsonResponse(jobEnvelope("queued")));
  const lateResponse = await pendingSubmission;
  assert.equal((await lateResponse.json()).job.status, "failed");

  const finalRow = db.compileJobs.get(reservation.id);
  assert.equal(finalRow.status, "failed");
  assert.match(finalRow.upstreamJobId, /^pending:/);
  assert.equal([...db.audits.values()].filter((item) => item.eventType === "compile.submitted").length, 0);
  assert.equal(upstreamDeletes, 2, "the unattached Compiler job is cancelled and purged");
});

test("a Compiler response arriving after Artifact deletion purges its unowned upstream job", async () => {
  const value = await fixture();
  let resolveUpstream;
  let upstreamDeletes = 0;
  const upstream = new Promise((resolve) => { resolveUpstream = resolve; });
  const db = installFixture(value, async (_url, init) => {
    if (init.method === "DELETE") {
      upstreamDeletes += 1;
      return new Response(null, { status: 204 });
    }
    return upstream;
  });

  const submission = POST(compileRequest(
    value.context.versionId,
    "alice@example.test",
    "compile-deleted-during-submit",
  ));
  for (let index = 0; index < 2000 && db.compileJobs.size === 0; index += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
  assert.equal(db.compileJobs.size, 1);
  db.compileJobs.clear(); // Simulates the final atomic whole-Artifact purge.

  resolveUpstream(jsonResponse(jobEnvelope("queued")));
  const response = await submission;
  assert.equal(response.status, 404);
  assert.equal((await response.json()).error.code, "COMPILE_JOB_NOT_FOUND");
  assert.equal(upstreamDeletes, 2);
  assert.equal(db.compileJobs.size, 0);
});

test("same-timestamp concurrent polls cannot regress a Compiler stage", async () => {
  const value = await fixture();
  let statusCalls = 0;
  const db = installFixture(value, async (_url, init) => {
    if (init.method === "POST") return jsonResponse(jobEnvelope("queued"));
    statusCalls += 1;
    if (statusCalls === 1) {
      for (let index = 0; index < 2000 && statusCalls < 2; index += 1) {
        await new Promise((resolve) => setImmediate(resolve));
      }
      const validating = jobEnvelope("validating");
      validating.job.updated_at = "2026-08-15T05:00:01Z";
      return jsonResponse(validating);
    }
    for (let index = 0; index < 2000 && [...db.compileJobs.values()][0]?.stage !== "validating"; index += 1) {
      await new Promise((resolve) => setImmediate(resolve));
    }
    const extracting = jobEnvelope("extracting");
    extracting.job.updated_at = "2026-08-15T05:00:01Z";
    return jsonResponse(extracting);
  });
  const submitted = await POST(compileRequest(value.context.versionId));
  const localJobId = (await submitted.json()).job.id;

  await Promise.all([poll(localJobId), poll(localJobId)]);
  assert.equal(db.compileJobs.get(localJobId).stage, "validating");
});

function upstreamFromStored(row, overrides = {}) {
  return {
    id: row.upstreamJobId,
    status: row.stage,
    createdAt: row.createdAt,
    updatedAt: row.updatedAt,
    expiresAt: row.expiresAt,
    stageTimingsMs: JSON.parse(row.timingsJson),
    error: row.errorJson ? JSON.parse(row.errorJson) : null,
    ...overrides,
  };
}

test("unchanged Compile polls do not write compile_jobs or fire projection updates", async () => {
  const value = await fixture();
  const db = installFixture(value, async () => jsonResponse(jobEnvelope("queued")));
  const submitted = await POST(compileRequest(value.context.versionId));
  const id = (await submitted.json()).job.id;
  const row = structuredClone(db.compileJobs.get(id));
  db.preparedSql.length = 0;
  for (let i = 0; i < 3; i += 1) {
    assert.deepEqual(await updateCompileJobFromUpstream(row, upstreamFromStored(row)), row);
  }
  assert.ok(db.preparedSql.length > 0, "poll must still observe concurrent local state");
  assert.ok(db.preparedSql.every((sql) => sql.trimStart().startsWith("SELECT")),
    "no UPDATE means no compile_jobs UPDATE projection trigger can fire");

  const changed = await updateCompileJobFromUpstream(row, upstreamFromStored(row, {
    stageTimingsMs: { ...JSON.parse(row.timingsJson), queued: 11 },
  }));
  assert.equal(JSON.parse(changed.timingsJson).queued, 11);
  assert.equal(db.preparedSql.filter((sql) => sql.startsWith("UPDATE compile_jobs")).length, 1);
});

test("unchanged upstream poll returns cancellation or import that won concurrently", async () => {
  const value = await fixture();
  const db = installFixture(value, async () => jsonResponse(jobEnvelope("queued")));
  const submitted = await POST(compileRequest(value.context.versionId));
  const id = (await submitted.json()).job.id;
  const beforeUpstream = structuredClone(db.compileJobs.get(id));
  for (const winner of [
    { status: "failed", stage: "failed", progress: 100,
      errorJson: JSON.stringify({ code: "CANCELLED", message: "Cancelled", retryable: false }) },
    { status: "running", stage: "importing", progress: 97 },
  ]) {
    db.compileJobs.set(id, { ...beforeUpstream, ...winner, updatedAt: "2026-08-15T05:00:03Z" });
    db.preparedSql.length = 0;
    const receipt = await updateCompileJobFromUpstream(beforeUpstream, upstreamFromStored(beforeUpstream));
    assert.equal(receipt.stage, winner.stage);
    assert.equal(receipt.status, winner.status);
    assert.equal(receipt.updatedAt, "2026-08-15T05:00:03Z");
    assert.ok(db.preparedSql.every((sql) => sql.trimStart().startsWith("SELECT")));
  }
});

test("unchanged upstream fields cannot bypass ownership or transition validation", async () => {
  const value = await fixture();
  const db = installFixture(value, async () => jsonResponse(jobEnvelope("extracting")));
  const submitted = await POST(compileRequest(value.context.versionId));
  const row = structuredClone(db.compileJobs.get((await submitted.json()).job.id));
  db.preparedSql.length = 0;
  for (const [overrides, code] of [
    [{ id: "unowned-upstream-job" }, "COMPILER_JOB_MISMATCH"],
    [{ createdAt: "2026-08-15T04:00:00Z" }, "COMPILER_JOB_MISMATCH"],
    [{ expiresAt: "2026-08-17T05:00:00Z" }, "COMPILER_JOB_MISMATCH"],
    [{ updatedAt: "2026-08-15T05:00:01Z" }, "COMPILER_STATE_REGRESSION"],
    [{ status: "parsing" }, "COMPILER_STATE_REGRESSION"],
  ]) {
    await assert.rejects(updateCompileJobFromUpstream(row, upstreamFromStored(row, overrides)),
      (error) => error.code === code);
  }
  assert.equal(db.preparedSql.length, 0, "validation precedes both fast-path reads and writes");
});
