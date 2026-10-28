import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import { register } from "node:module";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";

const PROJECT_ID = "DEEPSHOVEL-PROJECT";
const PROCESSOR_VERSION = "evidence-knowledge-graph/4.0";
const routeState = {
  DB: null,
  responses: [],
  searches: [],
  batchStarts: [],
  batchGets: [],
  batchCancels: [],
  batchProgress: [],
  startedBatches: new Set(),
  synchronizations: [],
  configurations: [],
};
globalThis.__knowledgeGraphRouteState = routeState;
globalThis.__knowledgeGraphSha256 = (value) => createHash("sha256").update(value).digest("hex");

const storeStub = `
export const PROJECT_ID=${JSON.stringify(PROJECT_ID)};
export class ApiError extends Error { constructor(status,code,message,details){ super(message); this.status=status; this.code=code; this.details=details; } }
export async function getBindings(){ return {DB:globalThis.__knowledgeGraphRouteState.DB}; }
export async function getCompilerConfiguration(){ return {baseUrl:"http://retrieval.test",sharedSecret:"knowledge-graph-route-test-secret-0123456789"}; }
export function assertSameOrigin(request){ const origin=request.headers.get("origin"); if(origin&&origin!==new URL(request.url).origin) throw new ApiError(403,"CROSS_ORIGIN_WRITE_REJECTED","Cross-origin write rejected"); }
export async function readJsonObject(request,maximum){ const text=await request.text(); if(new TextEncoder().encode(text).byteLength>maximum) throw new ApiError(413,"REQUEST_TOO_LARGE","request too large"); const value=JSON.parse(text); if(!value||typeof value!=="object"||Array.isArray(value)) throw new ApiError(400,"INVALID_JSON","body must be an object"); return value; }
export function safeError(error){ return Response.json({ok:false,error:{code:error?.code??"INTERNAL_ERROR",message:error instanceof Error?error.message:String(error),...(error?.details===undefined?{}:{details:error.details})}}, {status:error?.status??500}); }
export const sha256Hex=(value)=>globalThis.__knowledgeGraphSha256(value);
export function durableFailure(error){ if(error instanceof ApiError){ const retryable=typeof error.details?.retryable==="boolean"?error.details.retryable:[408,429,503,504].includes(error.status); return {code:error.code,message:error.message,retryable}; } return {code:"INTERNAL_ERROR",message:"Unexpected background processing failure",retryable:true}; }
`;
const retrievalClientStub = `
import { ApiError } from "@/lib/server/evidence-store";
export class RetrievalClient {
  constructor(configuration){ globalThis.__knowledgeGraphRouteState.configurations.push(configuration); }
  async search(request,allowedSnapshotIds,signal){
    const state=globalThis.__knowledgeGraphRouteState;
    state.searches.push({request:structuredClone(request),allowedSnapshotIds:[...allowedSnapshotIds],signal});
    if(!state.responses.length) throw new Error("unexpected retrieval search");
    const response=state.responses.shift();
    if(response instanceof Error) throw response;
    return structuredClone(response);
  }
  async startClaimRelationBatch(projectId,clientRequestId,activeSnapshotIds,claimRefs,reprocess,signal){
    const state=globalThis.__knowledgeGraphRouteState;
    state.batchStarts.push({projectId,clientRequestId,activeSnapshotIds:[...activeSnapshotIds],claimRefs:structuredClone(claimRefs),reprocess,signal});
    if(!state.batchProgress.length) throw new Error("unexpected batch start");
    const progress=structuredClone(state.batchProgress.shift());
    state.startedBatches.add(progress.batch_id);
    return progress;
  }
  async getClaimRelationBatch(batchId,activeSnapshotIds,signal,after){
    const state=globalThis.__knowledgeGraphRouteState;
    state.batchGets.push({batchId,activeSnapshotIds:[...activeSnapshotIds],signal,after});
    // The Compiler keeps batches in memory: unknown (or restarted) means 404.
    if(!state.startedBatches.has(batchId)) throw new ApiError(404,"UPSTREAM_JOB_NOT_FOUND","Claim relation batch was not found",{retryable:false});
    if(!state.batchProgress.length) throw new Error("unexpected batch poll");
    return structuredClone(state.batchProgress.shift());
  }
  async cancelClaimRelationBatch(batchId,activeSnapshotIds,signal){
    const state=globalThis.__knowledgeGraphRouteState;
    state.batchCancels.push({batchId,activeSnapshotIds:[...activeSnapshotIds],signal});
    if(!state.batchProgress.length) throw new Error("unexpected batch cancel");
    return structuredClone(state.batchProgress.shift());
  }
}
`;
const retrievalSyncStub = `
export async function synchronizeEvidenceRetrievalCorpus(_DB,_client,manifests,signal){
  globalThis.__knowledgeGraphRouteState.synchronizations.push({snapshotIds:manifests.map((item)=>item.snapshotId),signal});
}
`;
const loader = `
let rootUrl,storeUrl,retrievalClientUrl,retrievalSyncUrl;
export function initialize(data){({rootUrl,storeUrl,retrievalClientUrl,retrievalSyncUrl}=data);}
export async function resolve(specifier,context,nextResolve){
  if(specifier==="@/lib/server/evidence-store"||specifier==="@/lib/server/evidence-store.ts") return {url:storeUrl,shortCircuit:true};
  if(specifier==="@/lib/server/retrieval-client"||specifier==="@/lib/server/retrieval-client.ts") return {url:retrievalClientUrl,shortCircuit:true};
  if(specifier==="@/lib/server/openalex-classifier-client") return {url:"data:text/javascript,"+encodeURIComponent("export class OpenAlexClassifierClient { async classify(input){ return globalThis.__knowledgeGraphClassify(input); } }"),shortCircuit:true};
  if(specifier==="@/lib/server/retrieval-sync"||specifier==="@/lib/server/retrieval-sync.ts") return {url:retrievalSyncUrl,shortCircuit:true};
  if(specifier==="@/lib/evidence-retrieval") return {url:new URL("lib/evidence-retrieval/index.ts",rootUrl).href,shortCircuit:true};
  if(specifier==="@/lib/evidence-graph") return {url:new URL("lib/evidence-graph/index.ts",rootUrl).href,shortCircuit:true};
  if(specifier==="@/lib/evidence-knowledge-graph") return {url:new URL("lib/evidence-knowledge-graph/index.ts",rootUrl).href,shortCircuit:true};
  if(specifier.startsWith("@/")){let path=specifier.slice(2);if(!/\\.[cm]?[jt]sx?$/.test(path))path+=".ts";return {url:new URL(path,rootUrl).href,shortCircuit:true};}
  return nextResolve(specifier,context);
}`;
register(`data:text/javascript,${encodeURIComponent(loader)}`, {
  parentURL: import.meta.url,
  data: {
    rootUrl: new URL("../", import.meta.url).href,
    storeUrl: `data:text/javascript,${encodeURIComponent(storeStub)}`,
    retrievalClientUrl: `data:text/javascript,${encodeURIComponent(retrievalClientStub)}`,
    retrievalSyncUrl: `data:text/javascript,${encodeURIComponent(retrievalSyncStub)}`,
  },
});

const { POST: POST_CLASSIFICATIONS } = await import("../app/api/evidence-knowledge-graph/classifications/route.ts");
const graphRoute = await import("../app/api/evidence-knowledge-graph/route.ts");
const { GET } = graphRoute;
const { DELETE: DELETE_BATCH, GET: GET_BATCH, POST: POST_BATCH } = await import("../app/api/evidence-knowledge-graph/batch/route.ts");
const { persistBatchItems } = await import("../app/api/evidence-knowledge-graph/relation-batches.ts");
const { runClaimRelationWork } = await import("../app/api/internal/work/claim-relation-work.ts");
const { requestWork, getWorkItem } = await import("../lib/server/work-queue.ts");
const { evidenceRetrievalProjectionStatements } = await import("../lib/evidence-retrieval/index.ts");
const { persistOpenAlexClassification } = await import("../app/api/evidence-graphs/import-store.ts");

class Statement {
  args = [];
  constructor(owner, sql) { this.owner = owner; this.sql = sql; }
  bind(...args) { this.args = args; return this; }
  async first() { return this.owner.database.prepare(this.sql).get(...this.args); }
  async all() { return { results: this.owner.database.prepare(this.sql).all(...this.args) }; }
  async run() {
    const result = this.owner.database.prepare(this.sql).run(...this.args);
    return { success: true, meta: { changes: Number(result.changes) } };
  }
}

class SqliteD1 {
  queries = [];
  constructor(database) { this.database = database; }
  prepare(sql) { this.queries.push(sql); return new Statement(this, sql); }
  async batch(statements) {
    this.database.exec("BEGIN IMMEDIATE");
    try {
      const results = [];
      for (const statement of statements) results.push(await statement.run());
      this.database.exec("COMMIT");
      return results;
    } catch (error) {
      this.database.exec("ROLLBACK");
      throw error;
    }
  }
}

function migrate(database) {
  const names = fs.readdirSync(new URL("../drizzle/", import.meta.url))
    .filter((name) => /^\d{4}_.+\.sql$/u.test(name))
    .sort();
  for (const name of names) {
    const sql = fs.readFileSync(new URL(`../drizzle/${name}`, import.meta.url), "utf8");
    for (const statement of sql.split("--> statement-breakpoint")) {
      if (statement.trim()) database.exec(statement);
    }
  }
}

function digest(label) {
  return createHash("sha256").update(`knowledge-graph-route:${label}`).digest("hex");
}

function pairDigest(left, right) {
  const [first, second] = [left, right].sort((a, b) =>
    `${a.snapshotId}\u0000${a.claimId}`.localeCompare(`${b.snapshotId}\u0000${b.claimId}`, "en"));
  return createHash("sha256").update([
    PROCESSOR_VERSION,
    first.snapshotId,
    first.claimId,
    second.snapshotId,
    second.claimId,
  ].join("\u0000")).digest("hex");
}

function scope() {
  return {
    location: [],
    time: null,
    formation: [],
    sample_type: [],
    method: [],
    condition: [],
    measurement: [],
    uncertainty: null,
    entities: [],
  };
}

function record(label, ordinal) {
  const rootSha = digest(`root:${label}`);
  const graphSha = digest(`graph:${label}`);
  const claimSha = digest(`claim:${label}`);
  const propositionSha = digest(`proposition:${label}`);
  return {
    label,
    artifactId: `artifact:00000000-0000-4000-8000-${String(ordinal).padStart(12, "0")}`,
    versionId: `artifact-version:sha256:${rootSha}`,
    rootSha,
    graphSha,
    snapshotId: `snapshot:sha256:${graphSha}`,
    claimId: `claim:sha256:${claimSha}`,
    propositionKey: `proposition:sha256:${propositionSha}`,
    paperId: `paper:knowledge-graph:${ordinal}`,
    createdAt: new Date(Date.parse("2026-08-18T00:00:00.000Z") + ordinal * 1000).toISOString(),
  };
}

function documentFor(item) {
  return {
    indexVersion: "evidence-retrieval/2.0",
    snapshotId: item.snapshotId,
    paperId: item.paperId,
    paperFamilyId: item.artifactId,
    rootArtifactVersionId: item.versionId,
    claimId: item.claimId,
    sourceClaimId: null,
    originalClaim: null,
    canonicalClaim: `Canonical Claim ${item.label}`,
    propositionKey: item.propositionKey,
    scopedClaimId: item.claimId,
    paperTitle: `Paper ${item.label}`,
    evidenceContext: "",
    entities: [],
    keyTerms: [],
    abbreviations: [],
    qualifiers: [],
    claimType: "causal",
    polarity: "affirmed",
    modality: "asserted",
    attribution: "authors_conclusion",
    rhetoricalRole: "terminal_conclusion",
    scope: scope(),
    evidenceAnchors: [],
    explicitRelations: [],
    shortestEvidencePath: {
      rootClaimId: item.claimId,
      nodes: [{ id: item.claimId, kind: "atomic_claim", label: `Claim ${item.label}` }],
      links: [],
      depth: 0,
      meanEdgeConfidence: null,
    },
    pathQuality: 1,
    extractorPromptVersion: "compiler-extractor/1.0.0",
  };
}

async function fixture(labels) {
  routeState.responses.length = 0;
  routeState.searches.length = 0;
  routeState.batchStarts.length = 0;
  routeState.batchGets.length = 0;
  routeState.batchCancels.length = 0;
  routeState.batchProgress.length = 0;
  routeState.startedBatches.clear();
  routeState.synchronizations.length = 0;
  routeState.configurations.length = 0;
  const database = new DatabaseSync(":memory:");
  database.exec("PRAGMA foreign_keys=ON");
  migrate(database);
  const gateway = new SqliteD1(database);
  routeState.DB = gateway;
  const records = labels.map((label, index) => record(label, index + 1));
  for (const item of records) {
    database.prepare("INSERT INTO artifacts (id,project_id,preferred_name,artifact_type,created_by,created_at) VALUES (?,?,?,?,?,?)")
      .run(item.artifactId, PROJECT_ID, `Paper ${item.label}`, "paper", "owner@deepshovel.local", item.createdAt);
    database.prepare(`INSERT INTO artifact_versions
      (id,artifact_id,version_number,sha256,source_locator,media_type,original_file_name,
       byte_size,object_key,client_request_id,request_hash,created_by,created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
      item.versionId,
      item.artifactId,
      1,
      item.rootSha,
      `upload://${item.label}.pdf`,
      "application/pdf",
      `${item.label}.pdf`,
      100,
      `artifacts/${item.rootSha}`,
      `upload-${item.label}`,
      digest(`request:${item.label}`),
      "owner@deepshovel.local",
      item.createdAt,
    );
    database.prepare(`INSERT INTO evidence_graph_snapshots
      (id,project_id,root_artifact_version_id,schema_version,graph_sha256,graph_json,
       counts_json,extractor_run_id,extractor_model,extractor_prompt_version,created_by,created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`).run(
      item.snapshotId,
      PROJECT_ID,
      item.versionId,
      "scientific-evidence-graph/1.0",
      item.graphSha,
      "{}",
      JSON.stringify({ claims: 1 }),
      `extractor-run:${item.label}`,
      "test-model",
      "compiler-extractor/1.0.0",
      "owner@deepshovel.local",
      item.createdAt,
    );
    const projection = {
      manifest: {
        indexVersion: "evidence-retrieval/2.0",
        snapshotId: item.snapshotId,
        paperId: item.paperId,
        paperFamilyId: item.artifactId,
        rootArtifactVersionId: item.versionId,
        rootArtifactSha256: item.rootSha,
        graphSha256: item.graphSha,
        graphSchemaVersion: "scientific-evidence-graph/1.0",
        extractorPromptVersion: "compiler-extractor/1.0.0",
        documentCount: 1,
        createdAt: item.createdAt,
      },
      documents: [documentFor(item)],
    };
    await gateway.batch(evidenceRetrievalProjectionStatements(gateway, PROJECT_ID, projection));
  }
  return { database, gateway, records };
}

function diagnostics({ relation = "skipped", relationReturned = 0 } = {}) {
  return {
    lexical: { attempted: true, status: "ok", provider: "sqlite-test", returned: 1 },
    dense: { attempted: false, status: "skipped", provider: "dense-test", returned: 0 },
    rerank: { attempted: false, status: "skipped", provider: "rerank-test", returned: 0 },
    relation: {
      attempted: relation === "ok",
      status: relation,
      provider: relation === "ok" ? "gpt-test" : "gpt-test",
      returned: relation === "ok" ? relationReturned : 0,
    },
  };
}

function queryPlan() {
  return {
    query_type: "claim_search",
    original_query: "fixture",
    canonical_claim: "fixture",
    must_terms: [],
    optional_terms: [],
    exclude_terms: [],
    entities: [],
    polarity: null,
    claim_types: [],
    scope_constraints: null,
    planner: "deterministic",
  };
}

function scores() {
  return { lexical: 1, dense: null, rrf: 1, rerank: null };
}

function member(item, relation, relationDirection, confidence = 0.8) {
  return {
    snapshot_id: item.snapshotId,
    claim_id: item.claimId,
    paper_id: item.paperId,
    paper_family_id: item.artifactId,
    relation,
    relation_direction: relationDirection,
    relation_confidence: confidence,
    scope_relation: "overlap",
    scores: scores(),
  };
}

function finalResponse(members) {
  return {
    schema_version: "evidence-retrieval/2.0",
    stage: "ai_rerank",
    result_status: members.length ? "matches" : "no_direct_answer",
    query_plan: queryPlan(),
    channels: diagnostics(members.length
      ? { relation: "ok", relationReturned: members.length }
      : undefined),
    preview: [],
    clusters: members.map((item, index) => ({
      cluster_id: `cluster:${index}`,
      relation: item.relation,
      representative: item,
      member_refs: [item],
      distinct_paper_families: 1,
    })),
    warnings: [],
  };
}

function getRequest(parameters = "") {
  return new Request(`http://deepshovel.test/api/evidence-knowledge-graph${parameters}`);
}

async function classify(gateway, item, {
  topicId,
  topicName,
  subfieldId,
  subfieldName,
  fieldId = "https://openalex.org/fields/17",
  fieldName = "Computer Science",
  domainId = "https://openalex.org/domains/3",
  domainName = "Physical Sciences",
  secondaryTopics = [],
}) {
  const primaryTopic = {
    id: topicId,
    displayName: topicName,
    score: 0.9,
    subfield: { id: subfieldId, displayName: subfieldName },
    field: { id: fieldId, displayName: fieldName },
    domain: { id: domainId, displayName: domainName },
  };
  await persistOpenAlexClassification(gateway, item.snapshotId, {
    source: "official_title_abstract_model",
    classifier: "OpenAlex/official-model",
    openAlexWorkId: null,
    matchedDoi: null,
    primaryTopic,
    topics: [primaryTopic, ...secondaryTopics],
  }, item.createdAt);
}

function batchProgress(batchId, { status = "running", total = 1, items = [], completed = items.length, after } = {}) {
  const succeeded = items.filter((item) => item.status === "succeeded").length;
  return {
    schema_version: "evidence-retrieval/2.0",
    batch_id: batchId,
    status,
    reprocess: false,
    total,
    completed,
    succeeded,
    failed: completed - succeeded,
    items,
    ...(after === undefined ? {} : { item_offset: after, next_cursor: after + items.length, retired: false }),
  };
}

function succeededItem(item, members = []) {
  return {
    query_claim: { snapshot_id: item.snapshotId, claim_id: item.claimId },
    status: "succeeded",
    response: finalResponse(members),
    error_code: null,
  };
}

function failedItem(item) {
  return {
    query_claim: { snapshot_id: item.snapshotId, claim_id: item.claimId },
    status: "failed",
    response: null,
    error_code: "CLAIM_RELATION_PROCESSING_FAILED",
  };
}

function emptySummary() {
  return { claimed: 0, succeeded: 0, deferred: 0, retried: 0, failed: 0, cancelled: 0 };
}

/** Make every pending item due now, as if its backoff or poll delay elapsed. */
function makeDue(database) {
  database.prepare("UPDATE work_items SET next_attempt_at = '2000-01-01T00:00:00.000Z' WHERE state = 'pending'").run();
}

async function tick(gateway) {
  const summary = emptySummary();
  await runClaimRelationWork(gateway, Date.now() + 10_000, summary);
  return summary;
}

function counts(database) {
  return {
    completions: database.prepare("SELECT COUNT(*) AS n FROM corpus_claim_graph_completions").get().n,
    edges: database.prepare("SELECT COUNT(*) AS n FROM corpus_claim_graph_edges").get().n,
  };
}

test("GET exposes active Claims as pending until server-side work processes them idempotently", async () => {
  const { database, gateway, records } = await fixture(["query"]);
  const [query] = records;
  try {
    const pending = await (await GET(getRequest())).json();
    assert.deepEqual(pending.counts, { all: 1, unprocessed: 1, processed: 0, edges: 0 });
    assert.equal(pending.claims[0].status, "pending");

    await requestWork(gateway, PROJECT_ID, "claim_relations", [query.snapshotId]);
    routeState.batchProgress.push(batchProgress("relation-batch:sha256:" + digest("one"), { after: 0 }));
    const first = await tick(gateway);
    assert.equal(first.claimed, 1);
    assert.equal(first.deferred, 1, "a running batch yields and is polled on a later pass");
    assert.equal(routeState.batchGets.length, 1, "the batch is looked up before it is created");
    assert.equal(routeState.batchStarts.length, 1);
    assert.deepEqual(routeState.batchStarts[0].claimRefs, [{ snapshotId: query.snapshotId, claimId: query.claimId }]);

    makeDue(database);
    routeState.batchProgress.push(batchProgress("relation-batch:sha256:" + digest("one"), {
      status: "completed", items: [succeededItem(query)], after: 0,
    }));
    const second = await tick(gateway);
    assert.equal(second.succeeded, 1);
    assert.deepEqual(counts(database), { completions: 1, edges: 0 });
    const item = await getWorkItem(gateway, PROJECT_ID, "claim_relations", query.snapshotId);
    assert.equal(item.state, "succeeded");

    const processed = await (await GET(getRequest("?status=processed"))).json();
    assert.deepEqual(processed.counts, { all: 1, unprocessed: 0, processed: 1, edges: 0 });

    // Re-requesting finished work costs nothing: no Compiler batch, no new rows.
    await requestWork(gateway, PROJECT_ID, "claim_relations", [query.snapshotId]);
    const replay = await tick(gateway);
    assert.equal(replay.succeeded, 1);
    assert.equal(routeState.batchStarts.length, 1);
    assert.deepEqual(counts(database), { completions: 1, edges: 0 });
  } finally {
    database.close();
  }
});

test("relation work resumes after a lost Compiler batch and never records failed items", async () => {
  const { database, gateway, records } = await fixture(["query", "candidate"]);
  const [query, candidate] = records;
  try {
    await requestWork(gateway, PROJECT_ID, "claim_relations", [query.snapshotId]);
    routeState.batchProgress.push(batchProgress("relation-batch:sha256:" + digest("lost"), { after: 0 }));
    await tick(gateway);
    assert.equal(routeState.batchStarts.length, 1);

    // The Compiler restarts and forgets the in-memory batch.
    routeState.startedBatches.clear();
    makeDue(database);
    const lost = await tick(gateway);
    assert.equal(lost.deferred, 1);
    const resumed = await getWorkItem(gateway, PROJECT_ID, "claim_relations", query.snapshotId);
    assert.equal(resumed.state, "pending");
    assert.equal(resumed.attempts, 0, "a lost batch is resumed, not counted as a failure");
    assert.equal(resumed.progress.generation, 1);
    assert.equal(resumed.progress.batchId, undefined);

    makeDue(database);
    routeState.batchProgress.push(batchProgress("relation-batch:sha256:" + digest("second"), {
      status: "completed", items: [failedItem(query)], after: 0,
    }));
    const failed = await tick(gateway);
    assert.equal(routeState.batchStarts.length, 2);
    assert.notEqual(routeState.batchStarts[1].clientRequestId, routeState.batchStarts[0].clientRequestId);
    assert.equal(failed.retried, 1);
    assert.deepEqual(counts(database), { completions: 0, edges: 0 }, "a failed item never becomes a completion");
    const retrying = await getWorkItem(gateway, PROJECT_ID, "claim_relations", query.snapshotId);
    assert.equal(retrying.state, "pending");
    assert.equal(retrying.attempts, 1);
    assert.equal(retrying.lastError.code, "CLAIM_RELATION_PROCESSING_INCOMPLETE");

    makeDue(database);
    routeState.batchProgress.push(batchProgress("relation-batch:sha256:" + digest("third"), {
      status: "completed",
      items: [succeededItem(query, [member(candidate, "supports", "query_to_result", 0.9)])],
      after: 0,
    }));
    const done = await tick(gateway);
    assert.equal(done.succeeded, 1);
    assert.deepEqual(counts(database), { completions: 1, edges: 1 });
  } finally {
    database.close();
  }
});

test("relation work waits for its own index and caps concurrent Compiler batches", async () => {
  // One paper waits for its index; the other thirteen exceed the cap by one.
  const labels = Array.from({ length: 14 }, (_, index) => `p${index + 1}`);
  const { database, gateway, records } = await fixture(labels);
  try {
    await requestWork(gateway, PROJECT_ID, "retrieval_index", [records[0].snapshotId]);
    await requestWork(gateway, PROJECT_ID, "claim_relations", records.map((item) => item.snapshotId));
    for (const label of labels) {
      routeState.batchProgress.push(batchProgress("relation-batch:sha256:" + digest(`cap-${label}`), { after: 0 }));
    }
    const summary = await tick(gateway);
    assert.equal(summary.claimed, 14);
    assert.equal(routeState.batchStarts.length, 12, "at most twelve Compiler batches run at once");
    assert.ok(routeState.batchStarts.every((start) => start.claimRefs[0].snapshotId !== records[0].snapshotId),
      "a paper is analysed only after its own retrieval index is ready");
    assert.equal(summary.deferred, 14);
  } finally {
    database.close();
  }
});

test("the batch API only requests, observes and cancels server-side work", async () => {
  const { database, records } = await fixture(["query", "candidate"]);
  const [query] = records;
  const post = (body) => POST_BATCH(new Request("http://deepshovel.test/api/evidence-knowledge-graph/batch", {
    method: "POST",
    headers: { "content-type": "application/json", origin: "http://deepshovel.test" },
    body: JSON.stringify(body),
  }));
  const status = (snapshotId, method = "GET") => (method === "GET" ? GET_BATCH : DELETE_BATCH)(new Request(
    `http://deepshovel.test/api/evidence-knowledge-graph/batch?snapshotId=${encodeURIComponent(snapshotId)}`,
    { method, headers: { origin: "http://deepshovel.test" } },
  ));
  try {
    const started = await post({ snapshot_id: query.snapshotId });
    assert.equal(started.status, 202);
    assert.deepEqual(await started.json(), { ok: true, requested: 1, pendingSnapshots: 1 });
    const observed = await (await status(query.snapshotId)).json();
    assert.equal(observed.work.state, "pending");

    const all = await (await post({})).json();
    assert.deepEqual(all, { ok: true, requested: 1, pendingSnapshots: 2 }, "only finished or missing items are re-armed");

    database.prepare("UPDATE work_items SET progress_json = json_set(progress_json, '$.batchId', ?) WHERE subject_id = ?")
      .run("relation-batch:sha256:" + digest("cancel"), query.snapshotId);
    routeState.batchProgress.push(batchProgress("relation-batch:sha256:" + digest("cancel"), { status: "cancelled", completed: 1, items: [] }));
    const cancelled = await (await status(query.snapshotId, "DELETE")).json();
    assert.equal(cancelled.cancelled, true);
    assert.equal(cancelled.work.state, "cancelled");
    assert.equal(routeState.batchCancels.length, 1, "the in-memory Compiler batch is stopped too");

    const deleted = records[1];
    database.prepare("INSERT INTO artifact_deletion_authorizations (artifact_id, requested_by) VALUES (?, ?)").run(deleted.artifactId, "test");
    database.prepare("DELETE FROM evidence_active_snapshots WHERE snapshot_id = ?").run(deleted.snapshotId);
    assert.equal((await post({ snapshot_id: deleted.snapshotId })).status, 404);
    assert.equal(routeState.batchStarts.length + routeState.batchGets.length + routeState.searches.length, 0,
      "requesting or observing work never contacts the Compiler");
    assert.equal((await post({ mode: "reprocess_all" })).status, 400);
  } finally {
    database.close();
  }
});

test("GET exposes the official OpenAlex hierarchy and scopes the existing Claim list at every level", async () => {
  const { database, gateway, records } = await fixture(["artificial-intelligence", "software"]);
  const [ai, software] = records;
  try {
    await classify(gateway, ai, {
      topicId: "https://openalex.org/T100",
      topicName: "Evidence Synthesis",
      subfieldId: "https://openalex.org/subfields/1702",
      subfieldName: "Artificial Intelligence",
    });
    await classify(gateway, software, {
      topicId: "https://openalex.org/T200",
      topicName: "Software Testing",
      subfieldId: "https://openalex.org/subfields/1712",
      subfieldName: "Software",
    });

    const all = await (await GET(getRequest("?status=all"))).json();
    assert.equal(all.taxonomy.unclassifiedPapers, 0);
    assert.deepEqual(
      all.taxonomy.nodes.map((node) => [node.level, node.displayName, node.parentId, node.paperCount, node.claimCount]),
      [
        ["domain", "Physical Sciences", null, 2, 2],
        ["field", "Computer Science", "https://openalex.org/domains/3", 2, 2],
        ["subfield", "Artificial Intelligence", "https://openalex.org/fields/17", 1, 1],
        ["subfield", "Software", "https://openalex.org/fields/17", 1, 1],
        ["topic", "Evidence Synthesis", "https://openalex.org/subfields/1702", 1, 1],
        ["topic", "Software Testing", "https://openalex.org/subfields/1712", 1, 1],
      ],
    );

    const taxonomyOnly = await (await GET(getRequest("?view=taxonomy"))).json();
    assert.equal(taxonomyOnly.taxonomy.nodes.length, 6);
    assert.deepEqual(taxonomyOnly.counts, { all: 2, unprocessed: 2, processed: 0, edges: 0 });
    assert.equal("claims" in taxonomyOnly, false);

    const claimsOnly = await (await GET(getRequest("?view=claims&status=all&level=topic&id=https%3A%2F%2Fopenalex.org%2FT100"))).json();
    assert.equal(claimsOnly.taxonomy, null);
    assert.deepEqual(claimsOnly.claims.map((claim) => claim.claimId), [ai.claimId]);

    for (const parameters of [
      "level=domain&id=https%3A%2F%2Fopenalex.org%2Fdomains%2F3",
      "level=field&id=https%3A%2F%2Fopenalex.org%2Ffields%2F17",
      "level=subfield&id=https%3A%2F%2Fopenalex.org%2Fsubfields%2F1702",
      "level=topic&id=https%3A%2F%2Fopenalex.org%2FT100",
    ]) {
      const scoped = await (await GET(getRequest(`?status=all&${parameters}`))).json();
      assert.equal(
        scoped.scopeCounts.all,
        parameters.startsWith("level=domain") || parameters.startsWith("level=field") ? 2 : 1,
      );
      assert.deepEqual(
        scoped.claims.map((claim) => claim.claimId),
        parameters.startsWith("level=domain") || parameters.startsWith("level=field")
          ? [software.claimId, ai.claimId]
          : [ai.claimId],
      );
    }
  } finally {
    database.close();
  }
});

test("taxonomy overview counts Claims from manifests and distinguishes the primary Topic path", async () => {
  const { database, gateway, records } = await fixture(["first-paper", "second-paper"]);
  try {
    const secondaryTopic = {
      id: "https://openalex.org/T200",
      displayName: "Secondary Topic",
      score: 0.6,
      subfield: { id: "https://openalex.org/subfields/1712", displayName: "Software" },
      field: { id: "https://openalex.org/fields/17", displayName: "Computer Science" },
      domain: { id: "https://openalex.org/domains/3", displayName: "Physical Sciences" },
    };
    for (const item of records) {
      await classify(gateway, item, {
        topicId: "https://openalex.org/T100",
        topicName: "Primary Topic",
        subfieldId: "https://openalex.org/subfields/1702",
        subfieldName: "Artificial Intelligence",
        secondaryTopics: [secondaryTopic],
      });
    }

    gateway.queries.length = 0;
    const response = await GET(getRequest("?view=taxonomy"));
    assert.equal(response.status, 200);
    const body = await response.json();
    assert.deepEqual(body.counts, { all: 2, unprocessed: 2, processed: 0, edges: 0 });

    const nodes = new Map(body.taxonomy.nodes.map((node) => [node.id, node]));
    assert.deepEqual(
      [nodes.get("https://openalex.org/domains/3").claimCount, nodes.get("https://openalex.org/domains/3").primaryClaimCount],
      [2, 2],
    );
    assert.deepEqual(
      [nodes.get("https://openalex.org/T100").claimCount, nodes.get("https://openalex.org/T100").primaryClaimCount],
      [2, 2],
    );
    assert.deepEqual(
      [nodes.get("https://openalex.org/T200").claimCount, nodes.get("https://openalex.org/T200").primaryClaimCount],
      [2, 0],
    );
    const topicNodes = body.taxonomy.nodes.filter((node) => node.level === "topic");
    assert.equal(topicNodes.reduce((sum, node) => sum + node.claimCount, 0), 4);
    assert.equal(topicNodes.reduce((sum, node) => sum + node.primaryClaimCount, 0), body.counts.all);
    assert.equal(
      gateway.queries.some((sql) => /evidence_retrieval_documents/u.test(sql)),
      false,
      "taxonomy overview must not expand the Claim document table",
    );
  } finally {
    database.close();
  }
});

test("persisted batch results store directional and comparison relations while excluding weak matches", async () => {
  const { database, gateway, records } = await fixture([
    "query", "supports", "refines", "equivalent", "contradicts", "supplements", "related", "unrelated", "same-paper",
  ]);
  const [query, supports, refines, equivalent, contradicts, supplements, related, unrelated, samePaper] = records;
  try {
    const persisted = await persistBatchItems(gateway, batchProgress("relation-batch:sha256:" + digest("kinds"), {
      status: "completed",
      items: [succeededItem(query, [
        member(supports, "supports", "query_to_result", 0.91),
        member(refines, "refines", "result_to_query", 0.88),
        member(equivalent, "equivalent", "result_to_query", 0.95),
        member(contradicts, "contradicts", "result_to_query", 0.94),
        member(supplements, "supplements", "result_to_query", 0.86),
        member(related, "related", "result_to_query", 0.7),
        member(unrelated, "unrelated", "result_to_query", 0.9),
        member({ ...samePaper, artifactId: query.artifactId }, "supports", "result_to_query", 0.99),
      ])],
    }));
    assert.deepEqual(persisted, { persisted: 1, edges: 5, skipped: 0 });
    const edges = database.prepare(`SELECT source_snapshot_id AS sourceSnapshotId,
      source_claim_id AS sourceClaimId, target_snapshot_id AS targetSnapshotId,
      target_claim_id AS targetClaimId, relation, model_relation AS modelRelation
      FROM corpus_claim_graph_edges ORDER BY model_relation`).all().map((row) => ({ ...row }));
    assert.deepEqual(edges.map((edge) => edge.modelRelation), [
      "contradicts", "equivalent_to", "refines", "supplements", "supports",
    ]);
    assert.ok(edges.some((edge) => edge.relation === "equivalent_to" && edge.modelRelation === "equivalent_to"));
    assert.ok(edges.some((edge) => edge.relation === "contradicts" && edge.modelRelation === "contradicts"));
    assert.ok(edges.some((edge) => edge.relation === "supplements" && edge.modelRelation === "supplements"));
    assert.ok(edges.some((edge) => edge.sourceClaimId === refines.claimId && edge.targetClaimId === query.claimId));
    assert.ok(edges.some((edge) => edge.sourceClaimId === query.claimId && edge.targetClaimId === supports.claimId));
    const excluded = new Set([related.claimId, unrelated.claimId, samePaper.claimId]);
    assert.ok(edges.every((edge) => !excluded.has(edge.sourceClaimId) && !excluded.has(edge.targetClaimId)));
  } finally {
    database.close();
  }
});

test("one unordered Claim pair can store only one directed relation", async () => {
  const { database, gateway, records } = await fixture(["query", "candidate"]);
  const [query, candidate] = records;
  try {
    const pair = pairDigest(query, candidate);
    database.prepare(`INSERT INTO corpus_claim_graph_edges
      (id,pair_key,project_id,source_snapshot_id,source_claim_id,target_snapshot_id,target_claim_id,
       relation,model_relation,confidence,processor_version,created_at)
      VALUES (?,?,?,?,?,?,?,'directly_implies','supports',?,?,?)`).run(
      `claim-edge:sha256:${pair}`,
      `claim-pair:sha256:${pair}`,
      PROJECT_ID,
      candidate.snapshotId,
      candidate.claimId,
      query.snapshotId,
      query.claimId,
      0.8,
      PROCESSOR_VERSION,
      "2026-08-18T01:00:00.000Z",
    );
    const persisted = await persistBatchItems(gateway, batchProgress("relation-batch:sha256:" + digest("pair"), {
      status: "completed",
      items: [succeededItem(query, [member(candidate, "supports", "query_to_result", 0.9)])],
    }));
    assert.equal(persisted.persisted, 1);
    assert.equal(database.prepare("SELECT COUNT(*) AS count FROM corpus_claim_graph_edges").get().count, 1);
    assert.equal(database.prepare("SELECT COUNT(*) AS count FROM corpus_claim_graph_completions").get().count, 1);

    const processed = await (await GET(getRequest("?status=processed"))).json();
    assert.equal(processed.counts.processed, 1);
    assert.equal(processed.counts.edges, 1);
    assert.equal(processed.claims[0].claimId, query.claimId);
    const taxonomyOverview = await (await GET(getRequest("?view=taxonomy"))).json();
    assert.deepEqual(taxonomyOverview.counts, processed.counts);
  } finally {
    database.close();
  }
});

test("directed Claim relations that would close a longer cycle are ignored", async () => {
  const { database, records } = await fixture(["a", "b", "c"]);
  const [a, b, c] = records;
  try {
    const insert = (source, target) => {
      const pair = pairDigest(source, target);
      return database.prepare(`INSERT OR IGNORE INTO corpus_claim_graph_edges
        (id,pair_key,project_id,source_snapshot_id,source_claim_id,target_snapshot_id,target_claim_id,
         relation,model_relation,confidence,processor_version,created_at)
        VALUES (?,?,?,?,?,?,?,'directly_implies','supports',?,?,?)`).run(
        `claim-edge:sha256:${pair}`,
        `claim-pair:sha256:${pair}`,
        PROJECT_ID,
        source.snapshotId,
        source.claimId,
        target.snapshotId,
        target.claimId,
        0.9,
        PROCESSOR_VERSION,
        "2026-08-18T01:00:00.000Z",
      );
    };
    assert.equal(insert(a, b).changes, 1);
    assert.equal(insert(b, c).changes, 1);
    assert.equal(insert(c, a).changes, 0);
    assert.equal(database.prepare("SELECT COUNT(*) AS count FROM corpus_claim_graph_edges").get().count, 2);
  } finally {
    database.close();
  }
});

test("reprocessed batch results replace incident edges only after a successful classification", async () => {
  const { database, gateway, records } = await fixture(["query", "old", "replacement"]);
  const [query, oldCandidate, replacement] = records;
  const reprocess = (items) => ({ ...batchProgress("relation-batch:sha256:" + digest("reprocess"), { status: "completed", items }), reprocess: true });
  try {
    await persistBatchItems(gateway, batchProgress("relation-batch:sha256:" + digest("initial"), {
      status: "completed", items: [succeededItem(query, [member(oldCandidate, "supports", "query_to_result", 0.8)])],
    }));
    await persistBatchItems(gateway, reprocess([succeededItem(query, [member(replacement, "refines", "result_to_query", 0.93)])]));
    const edges = database.prepare(`SELECT source_claim_id AS sourceClaimId,
      target_claim_id AS targetClaimId FROM corpus_claim_graph_edges`).all();
    assert.deepEqual(edges.map((row) => ({ ...row })), [{
      sourceClaimId: replacement.claimId,
      targetClaimId: query.claimId,
    }]);
    assert.equal(database.prepare("SELECT COUNT(*) AS count FROM corpus_claim_graph_completions").get().count, 1);

    const unavailable = succeededItem(query, []);
    unavailable.response.channels.relation = { attempted: true, status: "degraded", provider: "gpt-test", returned: 0 };
    const skipped = await persistBatchItems(gateway, reprocess([unavailable]));
    assert.deepEqual(skipped, { persisted: 0, edges: 0, skipped: 1 });
    const preserved = database.prepare(`SELECT source_claim_id AS sourceClaimId,
      target_claim_id AS targetClaimId FROM corpus_claim_graph_edges`).all();
    assert.deepEqual(preserved.map((row) => ({ ...row })), edges.map((row) => ({ ...row })));
  } finally {
    database.close();
  }
});

test("the public graph API exposes no relation deletion", () => {
  assert.equal(graphRoute.DELETE, undefined);
});

test("graph reads stay bounded and neighborhood does not fetch corpus totals or manifests", async () => {
  const { database, gateway, records } = await fixture(["bounded-first", "bounded-second"]);
  try {
    gateway.queries.length = 0;
    const response = await GET(getRequest("?view=claims&status=all&limit=1"));
    assert.equal(response.status, 200);
    const body = await response.json();
    assert.equal(body.claims.length, 1);
    assert.equal(body.pageInfo.hasMore, true);
    assert.equal(gateway.queries.some((sql) => /manifest_json|evidence_retrieval_manifests|graph_json/u.test(sql)), false);
    assert.equal(gateway.queries.some((sql) => /SELECT\s+(?:r\.ordinal,\s*)?d\.snapshot_id[\s\S]*d\.document_json\s+AS\s+documentJson/u.test(sql)), false);
    assert.ok(gateway.queries.some((sql) => /WITH page AS MATERIALIZED/u.test(sql)));
    const hydrationSql = gateway.queries.find((sql) => sql.includes("AS canonicalClaim"));
    const plan = database.prepare(`EXPLAIN QUERY PLAN ${hydrationSql}`)
      .all(JSON.stringify([{ snapshotId: records[0].snapshotId, claimId: records[0].claimId }]), PROJECT_ID);
    assert.ok(plan.some((row) => /SEARCH d USING INDEX sqlite_autoindex_evidence_retrieval_documents_1/u.test(row.detail)));
    assert.equal(plan.some((row) => row.detail.includes("evidence_retrieval_document_proposition_idx")), false);
    const next = await (await GET(getRequest(`?view=claims&status=all&limit=1&cursor=${body.pageInfo.nextCursor}`))).json();
    assert.equal(next.claims.length, 1);
    assert.notEqual(next.claims[0].claimId, body.claims[0].claimId);

    gateway.queries.length = 0;
    const center = records[0];
    const neighborhood = await GET(getRequest(`?view=neighborhood&claimSnapshotId=${center.snapshotId}&claimId=${center.claimId}`));
    assert.equal(neighborhood.status, 200);
    const graph = await neighborhood.json();
    assert.equal(graph.neighborhood.nodes.length, 1);
    assert.equal("counts" in graph, false);
    assert.equal(gateway.queries.some((sql) => /graph_read_counts|graph_taxonomy_nodes|manifest_json/u.test(sql)), false);
  } finally { database.close(); }
});

test("graph read aggregates follow activation, completions, taxonomy and edge deletion exactly", async () => {
  const { database, gateway, records } = await fixture(["aggregate-source", "aggregate-target"]);
  const [source, target] = records;
  try {
    for (const item of records) await classify(gateway, item, {
      topicId: "https://openalex.org/T100", topicName: "Shared Topic",
      subfieldId: "https://openalex.org/subfields/1702", subfieldName: "Artificial Intelligence",
    });
    await persistBatchItems(gateway, batchProgress("relation-batch:sha256:" + digest("aggregate"), {
      status: "completed", items: [succeededItem(source, [member(target, "supports", "query_to_result", 0.9)])],
    }));
    const scopeQuery = "?view=claims&status=all&level=topic&id=https%3A%2F%2Fopenalex.org%2FT100";
    const before = await (await GET(getRequest(scopeQuery))).json();
    assert.deepEqual(before.scopeCounts, { all: 2, processed: 1, unprocessed: 1, edges: 1 });
    const active = database.prepare("SELECT * FROM evidence_active_snapshots WHERE snapshot_id=?").get(source.snapshotId);
    database.prepare("DELETE FROM evidence_active_snapshots WHERE snapshot_id=?").run(source.snapshotId);
    const removed = await (await GET(getRequest(scopeQuery))).json();
    assert.deepEqual(removed.counts, { all: 1, processed: 0, unprocessed: 1, edges: 0 });
    assert.deepEqual(removed.scopeCounts, removed.counts);
    const columns = Object.keys(active);
    database.prepare(`INSERT INTO evidence_active_snapshots (${columns.join(",")}) VALUES (${columns.map(() => "?").join(",")})`).run(...columns.map((key) => active[key]));
    const restored = await (await GET(getRequest(scopeQuery))).json();
    assert.deepEqual(restored.scopeCounts, before.scopeCounts);
    database.prepare(`DELETE FROM corpus_claim_graph_edges
      WHERE (source_snapshot_id = ? AND source_claim_id = ?) OR (target_snapshot_id = ? AND target_claim_id = ?)`)
      .run(source.snapshotId, source.claimId, source.snapshotId, source.claimId);
    const deleted = await (await GET(getRequest(scopeQuery))).json();
    assert.deepEqual(deleted.scopeCounts, { all: 2, processed: 1, unprocessed: 1, edges: 0 });
  } finally { database.close(); }
});

test("substring graph search advances a bounded empty window and binds its cursor to filters", async () => {
  const { database, records } = await fixture(Array.from({ length: 252 }, (_, index) => `scan-${index}`));
  try {
    const first = await (await GET(getRequest("?view=claims&status=all&query=absent-needle&limit=10"))).json();
    assert.equal(first.claims.length, 0);
    assert.equal(first.pageInfo.total, null);
    assert.equal(first.pageInfo.hasMore, true);
    assert.equal(first.searchInfo.scannedClaims, 250);
    const second = await (await GET(getRequest(`?view=claims&status=all&query=absent-needle&limit=10&cursor=${first.pageInfo.nextCursor}`))).json();
    assert.equal(second.searchInfo.scannedClaims, 2);
    assert.equal(second.searchInfo.complete, true);
    const wrongQuery = await GET(getRequest(`?view=claims&status=all&query=changed&cursor=${first.pageInfo.nextCursor}`));
    assert.equal(wrongQuery.status, 400);
    assert.equal((await GET(getRequest("?page=99999999"))).status, 400);
    assert.equal(database.prepare(`SELECT COUNT(*) AS n FROM evidence_retrieval_documents d JOIN evidence_retrieval_manifests m
      ON m.snapshot_id=d.snapshot_id WHERE d.created_at<>m.created_at`).get().n, 0);
    assert.equal(records.length, 252);
  } finally { database.close(); }
});

test("taxonomy cursors page in snapshot order using the scoped metadata index", async () => {
  const { database, gateway, records } = await fixture(["scope-first", "scope-second", "scope-third"]);
  try {
    for (const item of records) await classify(gateway, item, {
      topicId: "https://openalex.org/T100", topicName: "Shared Topic",
      subfieldId: "https://openalex.org/subfields/1702", subfieldName: "Artificial Intelligence",
    });
    const prefix = "?view=claims&status=all&limit=1&level=topic&id=https%3A%2F%2Fopenalex.org%2FT100";
    const ids = [];
    let cursor = null;
    for (let index = 0; index < records.length; index++) {
      const response = await GET(getRequest(prefix + (cursor ? `&cursor=${cursor}` : "")));
      assert.equal(response.status, 200);
      const body = await response.json();
      assert.equal(body.claims.length, 1);
      ids.push(body.claims[0].claimId);
      cursor = body.pageInfo.nextCursor;
    }
    assert.deepEqual(ids, records.toReversed().map((item) => item.claimId));
    assert.equal(cursor, null);
    const plan = database.prepare(`EXPLAIN QUERY PLAN SELECT snapshot_id FROM graph_taxonomy_memberships
      WHERE project_id=? AND level=? AND id=? AND claim_count>processed_count
      AND (created_at,snapshot_id) <= (?,?) ORDER BY created_at DESC,snapshot_id DESC LIMIT 51`)
      .all(PROJECT_ID, "topic", "https://openalex.org/T100", "2099-01-01", "z");
    assert.ok(plan.some((row) => row.detail.includes("graph_taxonomy_membership_pending_idx")));
    assert.equal(plan.some((row) => row.detail.includes("TEMP B-TREE")), false);
  } finally { database.close(); }
});

test("classification backfill reads only its indexed pending queue and exits cheaply when complete", async () => {
  const { database, gateway, records } = await fixture(["pending-first", "pending-second"]);
  const topic = {
    id: "https://openalex.org/T100", display_name: "Topic", score: 0.9,
    subfield: { id: "https://openalex.org/subfields/1702", display_name: "Artificial Intelligence" },
    field: { id: "https://openalex.org/fields/17", display_name: "Computer Science" },
    domain: { id: "https://openalex.org/domains/3", display_name: "Physical Sciences" },
  };
  const calls = [];
  globalThis.__knowledgeGraphClassify = async (input) => {
    calls.push(input);
    return { source: "official_title_abstract_model", classifier: "offline-fixture", openalex_work_id: null,
      matched_doi: null, primary_topic: topic, topics: [topic] };
  };
  const request = () => new Request("http://deepshovel.test/api/evidence-knowledge-graph/classifications", {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ limit: 1 }),
  });
  try {
    assert.equal(database.prepare("SELECT COUNT(*) AS n FROM graph_unclassified_snapshots").get().n, 2);
    const first = await POST_CLASSIFICATIONS(request());
    assert.equal(first.status, 200);
    assert.deepEqual(await first.json(), { ok: true, classified: 1, remaining: 1 });
    assert.equal(calls.length, 1);
    assert.equal(database.prepare("SELECT snapshot_id FROM graph_unclassified_snapshots").get().snapshot_id, records[1].snapshotId);
    assert.equal((await POST_CLASSIFICATIONS(request())).status, 200);
    gateway.queries.length = 0;
    assert.deepEqual(await (await POST_CLASSIFICATIONS(request())).json(), { ok: true, classified: 0, remaining: 0 });
    assert.equal(calls.length, 2);
    assert.equal(gateway.queries.length, 1);
    assert.ok(gateway.queries[0].includes("graph_read_counts"));
    const plan = database.prepare("EXPLAIN QUERY PLAN SELECT snapshot_id FROM graph_unclassified_snapshots WHERE project_id=? ORDER BY created_at,snapshot_id LIMIT 16").all(PROJECT_ID);
    assert.ok(plan.some((row) => row.detail.includes("graph_unclassified_queue_idx")));
    database.prepare("DELETE FROM openalex_paper_topics WHERE snapshot_id=?").run(records[0].snapshotId);
    database.prepare("DELETE FROM openalex_paper_classifications WHERE snapshot_id=?").run(records[0].snapshotId);
    assert.equal(database.prepare("SELECT COUNT(*) AS n FROM graph_unclassified_snapshots").get().n, 1);
  } finally { delete globalThis.__knowledgeGraphClassify; database.close(); }
});


test("relation work drains result pages by cursor and skips Claims that disappeared", async () => {
  const { database, gateway, records } = await fixture(["query", "candidate"]);
  const [query, candidate] = records;
  const batchId = "relation-batch:sha256:" + digest("cursor-batch");
  try {
    // Both Claims of both papers are analysed in one batch for this snapshot set.
    await requestWork(gateway, PROJECT_ID, "claim_relations", [query.snapshotId]);
    routeState.batchProgress.push(batchProgress(batchId, { after: 0, total: 1 }));
    await tick(gateway);
    makeDue(database);
    routeState.batchProgress.push({ ...batchProgress(batchId, {
      status: "completed", total: 1, completed: 1, items: [succeededItem({ ...candidate, claimId: "missing-claim" })], after: 0,
    }) });
    const skipped = await tick(gateway);
    assert.equal(skipped.retried, 1, "a page whose Claim disappeared is skipped, not a whole-pass failure");
    assert.deepEqual(counts(database), { completions: 0, edges: 0 });
    const afterSkip = await getWorkItem(gateway, PROJECT_ID, "claim_relations", query.snapshotId);
    assert.equal(afterSkip.state, "pending");

    makeDue(database);
    const pageBatch = "relation-batch:sha256:" + digest("paged");
    routeState.batchProgress.push(batchProgress(pageBatch, { after: 0, total: 1 }));
    await tick(gateway);
    makeDue(database);
    routeState.batchProgress.push(batchProgress(pageBatch, {
      status: "completed", total: 1, completed: 1, items: [succeededItem(query)], after: 0,
    }));
    const finished = await tick(gateway);
    assert.equal(finished.succeeded, 1);
    assert.equal(routeState.batchGets.at(-1).after, 0);
    assert.deepEqual(counts(database), { completions: 1, edges: 0 });
  } finally { database.close(); }
});

