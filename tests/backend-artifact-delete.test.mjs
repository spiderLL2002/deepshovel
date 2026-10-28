import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import { register } from "node:module";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";

const bindings = { DB: null, BUCKET: null };
const removedCompilerJobs = [];
const removedCompilerVersions = [];
const removedRetrievalSnapshots = [];
globalThis.__artifactDeleteBindings = bindings;
globalThis.__removedCompilerJobs = removedCompilerJobs;
globalThis.__removedCompilerVersions = removedCompilerVersions;
globalThis.__removedRetrievalSnapshots = removedRetrievalSnapshots;
globalThis.__artifactCompilerRemoveError = null;
globalThis.__artifactCompilerVersionRemoveError = null;
globalThis.__artifactRetrievalDeleteError = null;
globalThis.__artifactStoreHooks = {
  async readBytes(request) { return new Uint8Array(await request.arrayBuffer()); },
  async sha256(value) {
    return createHash("sha256").update(typeof value === "string" ? value : value).digest("hex");
  },
  async put(bucket, sha256, bytes, mediaType) {
    const key = `artifacts/${sha256}`;
    const existing = await bucket.head(key);
    if (existing) return { object: existing, stored: false };
    const object = await bucket.put(key, bytes, { httpMetadata: { contentType: mediaType } });
    return { object, stored: true };
  },
};

const storeStub = `
export const PROJECT_ID = "DEEPSHOVEL-PROJECT";
export class ApiError extends Error { constructor(status,code,message,details){ super(message); this.status=status; this.code=code; this.details=details; } }
globalThis.__artifactDeleteApiError = ApiError;
export async function getBindings(){ return globalThis.__artifactDeleteBindings; }
export async function getCompilerConfiguration(){ return {baseUrl:"http://127.0.0.1:8011",sharedSecret:"artifact-delete-test-secret-0123456789"}; }
export function assertSameOrigin(request){ const origin=request.headers.get("origin"); if(origin&&origin!==new URL(request.url).origin) throw new ApiError(403,"CROSS_ORIGIN_WRITE_REJECTED","Cross-origin write rejected"); }
export function getActor(){ return "owner@deepshovel.local"; }
export function assertLocalMaintainer(request){ if(request.headers.has("cf-ray")||request.headers.get("cf-connecting-ip")!=="127.0.0.1") throw new ApiError(403,"MAINTENANCE_LOCAL_ONLY","local only"); }
export async function readJsonObject(request){ let value; try { value=JSON.parse(await request.text()); } catch { throw new ApiError(400,"INVALID_JSON","Invalid JSON request payload"); } if(!value||typeof value!=="object"||Array.isArray(value)) throw new ApiError(400,"INVALID_JSON_OBJECT","JSON request body must be an object"); return value; }
export function normalizeMimeType(value){ return value.split(";",1)[0].trim().toLowerCase(); }
export function safeError(error){ return Response.json({ok:false,error:{code:error.code??"INTERNAL_ERROR",message:error.message}}, {status:error.status??500}); }
export const artifactTypeForMime=()=>"paper";
export const decodeArtifactNameHeader=(value)=>decodeURIComponent(value??"");
export const normalizeIdempotencyKey=(value,fallback)=>value??fallback;
export const putContentAddressedObject=(...args)=>globalThis.__artifactStoreHooks.put(...args);
export const readBoundedBody=(request)=>globalThis.__artifactStoreHooks.readBytes(request);
export const sha256Hex=(value)=>globalThis.__artifactStoreHooks.sha256(value);
export const validateArtifactBytes=(name)=>name||"fixture.pdf";`;
const compilerStub = `
export class CompilerClient {
  async remove(jobId){ globalThis.__removedCompilerJobs.push(jobId); if(globalThis.__artifactCompilerRemoveError) throw globalThis.__artifactCompilerRemoveError; }
  async removeDocumentExtraction(versionId){ globalThis.__removedCompilerVersions.push(versionId); if(globalThis.__artifactCompilerVersionRemoveError) throw globalThis.__artifactCompilerVersionRemoveError; }
}
export const compilerProgress=()=>0;
`;
const retrievalStub = `
export const RETRIEVAL_MAX_MANIFESTS=4096;
export class RetrievalClient { async deleteSnapshots(projectId,snapshotIds){ globalThis.__removedRetrievalSnapshots.push({projectId,snapshotIds}); if(globalThis.__artifactRetrievalDeleteError) throw globalThis.__artifactRetrievalDeleteError; return {deleted_snapshots:snapshotIds.length,deleted_documents:0,warnings:[]}; } }
`;
const loader = `
let rootUrl,storeUrl,compilerUrl,retrievalUrl; export function initialize(data){rootUrl=data.rootUrl;storeUrl=data.storeUrl;compilerUrl=data.compilerUrl;retrievalUrl=data.retrievalUrl;}
export async function resolve(specifier,context,nextResolve){
 if(specifier==="@/lib/server/evidence-store"||specifier==="@/lib/server/evidence-store.ts") return {url:storeUrl,shortCircuit:true};
 if(specifier==="@/lib/server/compiler-client.ts") return {url:compilerUrl,shortCircuit:true};
 if(specifier==="@/lib/server/retrieval-client.ts") return {url:retrievalUrl,shortCircuit:true};
 if(specifier.startsWith("@/")){let path=specifier.slice(2);if(!/\\.[cm]?[jt]sx?$/.test(path))path+=".ts";return {url:new URL(path,rootUrl).href,shortCircuit:true};}
 return nextResolve(specifier,context);
}`;
register(`data:text/javascript,${encodeURIComponent(loader)}`, { parentURL: import.meta.url, data: {
  rootUrl: new URL("../", import.meta.url).href,
  storeUrl: `data:text/javascript,${encodeURIComponent(storeStub)}`,
  compilerUrl: `data:text/javascript,${encodeURIComponent(compilerStub)}`,
  retrievalUrl: `data:text/javascript,${encodeURIComponent(retrievalStub)}`,
} });

const artifactsRoute = await import("../app/api/artifacts/route.ts");
const { POST } = artifactsRoute;
const { DELETE } = await import("../app/api/internal/artifacts/route.ts");
const { markCompileJobFailed } = await import("../app/api/compile/compile-job-store.ts");
const artifactId = "artifact:00000000-0000-4000-8000-000000000001";
const sha = "a".repeat(64);
const versionId = `artifact-version:sha256:${sha}`;
const upstreamJobId = "4d63b26c-6548-4a62-8218-14aa13580cb2";
const snapshotSha = "c".repeat(64);
const snapshotId = `snapshot:sha256:${snapshotSha}`;

class Statement {
  args = [];
  constructor(owner, sql) { this.owner = owner; this.sql = sql; }
  bind(...args) { this.args = args; return this; }
  async first() {
    if (this.owner.beforeFirst) await this.owner.beforeFirst(this.sql, this.args);
    return this.owner.database.prepare(this.sql).get(...this.args);
  }
  async all() { return { results: this.owner.database.prepare(this.sql).all(...this.args) }; }
  async run() {
    if (this.sql.includes("INSERT OR IGNORE INTO audit_events") && this.owner.beforeAuditInsert) {
      const callback = this.owner.beforeAuditInsert;
      this.owner.beforeAuditInsert = null;
      await callback();
    }
    if (this.sql.includes("INSERT OR IGNORE INTO artifact_deletion_authorizations")
      && this.owner.beforeAuthorization) {
      const callback = this.owner.beforeAuthorization;
      this.owner.beforeAuthorization = null;
      callback();
    }
    const result = this.owner.database.prepare(this.sql).run(...this.args);
    return { success: true, meta: { changes: Number(result.changes) } };
  }
}
class SqliteD1 {
  constructor(database) { this.database = database; }
  prepare(sql) { return new Statement(this, sql); }
  async batch(statements) {
    if (this.beforeBatch) await this.beforeBatch(statements);
    this.database.exec("BEGIN IMMEDIATE");
    try { const results = []; for (const statement of statements) results.push(await statement.run()); this.database.exec("COMMIT"); return results; }
    catch (error) { this.database.exec("ROLLBACK"); throw error; }
  }
}
class MemoryR2 {
  deleted = [];
  objects = new Map();
  async head(key) { return this.objects.get(key) ?? null; }
  async put(key, bytes, options = {}) {
    const object = {
      key,
      size: bytes.byteLength,
      etag: `etag-${this.objects.size + 1}`,
      httpMetadata: options.httpMetadata,
    };
    this.objects.set(key, object);
    return object;
  }
  async delete(key) {
    if (this.beforeDelete) await this.beforeDelete(key);
    this.deleted.push(key);
    this.objects.delete(key);
  }
}

function migrate(database) {
  const names = fs.readdirSync(new URL("../drizzle/", import.meta.url)).filter((name) => /^\d{4}_.+\.sql$/u.test(name)).sort();
  for (const name of names) {
    const sql = fs.readFileSync(new URL(`../drizzle/${name}`, import.meta.url), "utf8");
    for (const statement of sql.split("--> statement-breakpoint")) if (statement.trim()) database.exec(statement);
  }
}

function fixture(status = "completed") {
  removedCompilerJobs.length = 0;
  removedCompilerVersions.length = 0;
  removedRetrievalSnapshots.length = 0;
  globalThis.__artifactCompilerRemoveError = null;
  globalThis.__artifactCompilerVersionRemoveError = null;
  globalThis.__artifactRetrievalDeleteError = null;
  const database = new DatabaseSync(":memory:");
  migrate(database);
  database.prepare("INSERT INTO artifacts (id,project_id,preferred_name,artifact_type,created_by) VALUES (?,?,?,?,?)")
    .run(artifactId, "DEEPSHOVEL-PROJECT", "fixture.pdf", "paper", "owner@deepshovel.local");
  database.prepare(`INSERT INTO artifact_versions
    (id,artifact_id,version_number,sha256,source_locator,media_type,original_file_name,byte_size,object_key,client_request_id,request_hash,created_by)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`).run(versionId, artifactId, 1, sha, "upload://fixture.pdf", "application/pdf", "fixture.pdf", 12, `artifacts/${sha}`, "upload-1", "b".repeat(64), "owner@deepshovel.local");
  database.prepare(`INSERT INTO compile_jobs
    (id,project_id,artifact_version_id,upstream_job_id,status,stage,progress,timings_json,snapshot_id,client_request_id,request_hash,created_by,created_at,updated_at,expires_at)
    VALUES (?,?,?,?,?,?,?,?,NULL,?,?,?,?,?,?)`).run("web-job-1", "DEEPSHOVEL-PROJECT", versionId, upstreamJobId, status, status === "running" ? "extracting" : "completed", status === "running" ? 60 : 100, "{}", "compile-1", "c".repeat(64), "owner@deepshovel.local", "2026-08-16T00:00:00Z", "2026-08-16T00:00:00Z", "2026-08-17T00:00:00Z");
  database.prepare("INSERT INTO audit_events (id,project_id,event_type,entity_type,entity_id,actor,payload_json) VALUES (?,?,?,?,?,?,?)")
    .run("audit-1", "DEEPSHOVEL-PROJECT", "artifact_version.stored", "artifact_version", versionId, "owner@deepshovel.local", JSON.stringify({ artifactId, fileName: "fixture.pdf" }));
  const bucket = new MemoryR2();
  bucket.objects.set(`artifacts/${sha}`, {
    key: `artifacts/${sha}`, size: 12, etag: "etag-fixture",
    httpMetadata: { contentType: "application/pdf" },
  });
  bindings.DB = new SqliteD1(database); bindings.BUCKET = bucket;
  return { database, bucket, gatewayDb: bindings.DB };
}

function addSnapshot(database) {
  database.prepare(`INSERT INTO evidence_graph_snapshots
    (id, project_id, root_artifact_version_id, schema_version, graph_sha256,
     graph_json, counts_json, extractor_run_id, extractor_model,
     extractor_prompt_version, created_by, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
    .run(
      snapshotId,
      "DEEPSHOVEL-PROJECT",
      versionId,
      "scientific-evidence-graph/1.0",
      snapshotSha,
      "{}",
      "{}",
      "extractor-run:artifact-delete",
      "test-model",
      "compiler-extractor/1.0.0",
      "owner@deepshovel.local",
      "2026-08-16T00:00:00Z",
    );
  return snapshotId;
}

function addKnowledgeGraphDeletionFixture(database) {
  database.exec("PRAGMA foreign_keys=ON");
  addSnapshot(database);
  const survivorArtifactId = "artifact:00000000-0000-4000-8000-000000000002";
  const survivorSha = "d".repeat(64);
  const survivorVersionId = `artifact-version:sha256:${survivorSha}`;
  const survivorSnapshotSha = "e".repeat(64);
  const survivorSnapshotId = `snapshot:sha256:${survivorSnapshotSha}`;
  const deletedClaimId = `claim:sha256:${"1".repeat(64)}`;
  const survivorClaimId = `claim:sha256:${"2".repeat(64)}`;
  const createdAt = "2026-08-16T01:00:00.000Z";

  database.prepare("INSERT INTO artifacts (id,project_id,preferred_name,artifact_type,created_by) VALUES (?,?,?,?,?)")
    .run(survivorArtifactId, "DEEPSHOVEL-PROJECT", "survivor.pdf", "paper", "owner@deepshovel.local");
  database.prepare(`INSERT INTO artifact_versions
    (id,artifact_id,version_number,sha256,source_locator,media_type,original_file_name,
     byte_size,object_key,client_request_id,request_hash,created_by,created_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    survivorVersionId,
    survivorArtifactId,
    1,
    survivorSha,
    "upload://survivor.pdf",
    "application/pdf",
    "survivor.pdf",
    10,
    `artifacts/${survivorSha}`,
    "upload-survivor",
    "3".repeat(64),
    "owner@deepshovel.local",
    createdAt,
  );
  database.prepare(`INSERT INTO evidence_graph_snapshots
    (id,project_id,root_artifact_version_id,schema_version,graph_sha256,graph_json,
     counts_json,extractor_run_id,extractor_model,extractor_prompt_version,created_by,created_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    survivorSnapshotId,
    "DEEPSHOVEL-PROJECT",
    survivorVersionId,
    "scientific-evidence-graph/1.0",
    survivorSnapshotSha,
    "{}",
    "{}",
    "extractor-run:artifact-delete-survivor",
    "test-model",
    "compiler-extractor/1.0.0",
    "owner@deepshovel.local",
    createdAt,
  );

  const projections = [
    {
      snapshotId,
      paperId: "paper:artifact-delete-target",
      paperFamilyId: artifactId,
      rootArtifactVersionId: versionId,
      rootArtifactSha256: sha,
      graphSha256: snapshotSha,
      claimId: deletedClaimId,
      propositionKey: `proposition:sha256:${"4".repeat(64)}`,
      createdAt: "2026-08-16T00:00:00.000Z",
    },
    {
      snapshotId: survivorSnapshotId,
      paperId: "paper:artifact-delete-survivor",
      paperFamilyId: survivorArtifactId,
      rootArtifactVersionId: survivorVersionId,
      rootArtifactSha256: survivorSha,
      graphSha256: survivorSnapshotSha,
      claimId: survivorClaimId,
      propositionKey: `proposition:sha256:${"5".repeat(64)}`,
      createdAt,
    },
  ];
  for (const projection of projections) {
    database.prepare(`INSERT INTO evidence_retrieval_documents
      (project_id,snapshot_id,paper_id,paper_family_id,claim_id,scoped_claim_id,
       proposition_key,document_json,index_version,created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?)`).run(
      "DEEPSHOVEL-PROJECT",
      projection.snapshotId,
      projection.paperId,
      projection.paperFamilyId,
      projection.claimId,
      projection.claimId,
      projection.propositionKey,
      "{}",
      "evidence-retrieval/2.0",
      projection.createdAt,
    );
    database.prepare(`INSERT INTO evidence_retrieval_manifests
      (snapshot_id,project_id,paper_id,paper_family_id,root_artifact_version_id,
       root_artifact_sha256,graph_sha256,graph_schema_version,extractor_prompt_version,
       index_version,document_count,manifest_json,created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
      projection.snapshotId,
      "DEEPSHOVEL-PROJECT",
      projection.paperId,
      projection.paperFamilyId,
      projection.rootArtifactVersionId,
      projection.rootArtifactSha256,
      projection.graphSha256,
      "scientific-evidence-graph/1.0",
      "compiler-extractor/1.0.0",
      "evidence-retrieval/2.0",
      1,
      "{}",
      projection.createdAt,
    );
    database.prepare(`INSERT INTO openalex_paper_classifications
      (snapshot_id,project_id,source,classifier,openalex_work_id,matched_doi,created_at)
      VALUES (?,?,'official_title_abstract_model','OpenAlex/official-model',NULL,NULL,?)`).run(
      projection.snapshotId,
      "DEEPSHOVEL-PROJECT",
      projection.createdAt,
    );
    database.prepare(`INSERT INTO openalex_paper_topics
      (snapshot_id,topic_id,project_id,display_name,score,is_primary,ordinal,
       subfield_id,subfield_name,field_id,field_name,domain_id,domain_name,created_at)
      VALUES (?,?,'DEEPSHOVEL-PROJECT','Evidence Synthesis',0.9,1,0,
       'https://openalex.org/subfields/1702','Artificial Intelligence',
       'https://openalex.org/fields/17','Computer Science',
       'https://openalex.org/domains/3','Physical Sciences',?)`).run(
      projection.snapshotId,
      `https://openalex.org/T${projection.snapshotId === snapshotId ? "100" : "200"}`,
      projection.createdAt,
    );
  }
  database.prepare(`INSERT INTO corpus_claim_graph_completions
    (project_id,snapshot_id,claim_id,processor_version,candidate_count,
     edge_count,classifier_model,completed_at)
    VALUES (?,?,?,?,?,?,?,?)`).run(
    "DEEPSHOVEL-PROJECT",
    snapshotId,
    deletedClaimId,
    "evidence-knowledge-graph/2.0",
    1,
    1,
    "gpt-test",
    createdAt,
  );
  database.prepare(`INSERT INTO corpus_claim_graph_edges
    (id,project_id,source_snapshot_id,source_claim_id,target_snapshot_id,target_claim_id,
     relation,model_relation,confidence,processor_version,created_at)
    VALUES (?,?,?,?,?,?,'directly_implies','supports',?,?,?)`).run(
    `claim-edge:sha256:${"6".repeat(64)}`,
    "DEEPSHOVEL-PROJECT",
    snapshotId,
    deletedClaimId,
    survivorSnapshotId,
    survivorClaimId,
    0.9,
    "evidence-knowledge-graph/2.0",
    createdAt,
  );
  return {
    survivorArtifactId,
    survivorSnapshotId,
    survivorClaimId,
  };
}

function addCitationReceptionDeletionFixture(database, citingSnapshotId, citedSnapshotId) {
  const projectId = "DEEPSHOVEL-PROJECT";
  const createdAt = "2026-08-16T02:00:00.000Z";
  const referenceId = `citation-reference:sha256:${"7".repeat(64)}`;
  const mentionId = `citation-mention:sha256:${"8".repeat(64)}`;
  const runId = `citation-run:sha256:${"9".repeat(64)}`;
  const assertionId = `citation-assertion:sha256:${"a".repeat(64)}`;
  const assessmentId = `citation-assessment:sha256:${"b".repeat(64)}`;
  database.prepare(`INSERT INTO citation_source_manifests
    (snapshot_id,project_id,schema_version,extractor_version,source_sha256,
     reference_count,mention_count,warnings_json,created_at)
    VALUES (?,?,'citation-source/1.0','citation-source-extractor/1.0',?,1,1,'[]',?)`).run(
    citingSnapshotId,
    projectId,
    "c".repeat(64),
    createdAt,
  );
  database.prepare(`INSERT INTO bibliographic_references
    (id,project_id,citing_snapshot_id,ordinal,label,raw_text,normalized_doi,
     year,source_block_id,page,created_at)
    VALUES (?,?,?,1,'1','Smith (2024). Prior experiment.','10.1234/example',
     2024,'block:references:1',8,?)`).run(referenceId, projectId, citingSnapshotId, createdAt);
  database.prepare(`INSERT INTO citation_mentions
    (id,project_id,citing_snapshot_id,reference_id,marker,exact_quote,context_text,
     source_block_id,context_block_ids_json,page,bbox_json,char_start,char_end,
     group_size,candidate_functions_json,created_at)
    VALUES (?,?,?,?,'[1]','Our result agrees [1].','Our result agrees [1].',
     'block:results:1','["block:results:1"]',3,NULL,18,21,1,
     '["result_comparison"]',?)`).run(
    mentionId,
    projectId,
    citingSnapshotId,
    referenceId,
    createdAt,
  );
  database.prepare(`INSERT INTO citation_reception_runs
    (id,project_id,citing_snapshot_id,reference_id,cited_snapshot_id,
     processor_version,resolution_method,resolution_confidence,extraction_run_id,
     extraction_model,extraction_prompt_version,assertions_sha256,
     verification_run_id,verifier_model,verifier_prompt_version,assertion_count,created_at)
    VALUES (?,?,?,?,?,'citation-reception-processor/1.0.0','doi',0.99,
     'citation-extraction:fixture','gpt-5.6-luna','citation-extractor/1.0.0',?,
     'citation-verification:fixture','gpt-5.6-luna','citation-verifier/1.0.0',1,?)`).run(
    runId,
    projectId,
    citingSnapshotId,
    referenceId,
    citedSnapshotId,
    "d".repeat(64),
    createdAt,
  );
  database.prepare(`INSERT INTO citation_assertions
    (id,run_id,project_id,mention_id,attributed_proposition,citation_function,
     stance,confirmation_basis,target_entity_kind,target_entity_id,
     citing_evidence_entity_ids_json,confidence,created_at)
    VALUES (?,?,?,?,'The cited experiment is reproducible.','result_comparison',
     'supports','consistent_with_citing_evidence','source_anchor','anchor:cited:1',
     '["observation:citing:1"]',0.9,?)`).run(
    assertionId,
    runId,
    projectId,
    mentionId,
    createdAt,
  );
  database.prepare(`INSERT INTO citation_assessments
    (id,assertion_id,project_id,faithfulness,confirmation_basis,independence,
     confidence,evidence_ids_json,limitations_json,extraction_sha256,
     verifier_model,verifier_prompt_version,created_at)
    VALUES (?,?,?,'faithful','consistent_with_citing_evidence','independent',
     0.9,'["anchor:cited:1"]','[]',?,'gpt-5.6-luna',
     'citation-verifier/1.0.0',?)`).run(
    assessmentId,
    assertionId,
    projectId,
    "d".repeat(64),
    createdAt,
  );
  return { referenceId, mentionId, runId, assertionId, assessmentId };
}

function request(body, extraHeaders = {}) {
  return new Request("http://127.0.0.1:8100/api/internal/artifacts", {
    method: "DELETE",
    headers: { "content-type": "application/json", origin: "http://127.0.0.1:8100", "cf-connecting-ip": "127.0.0.1", ...extraHeaders },
    body: JSON.stringify(body),
  });
}

function uploadRequest(bytes, { targetArtifactId = null, idempotencyKey = null } = {}) {
  const headers = new Headers({
    "content-type": "application/pdf",
    origin: "http://deepshovel.test",
    "x-artifact-name": encodeURIComponent("fixture.pdf"),
  });
  if (targetArtifactId) headers.set("x-artifact-id", targetArtifactId);
  if (idempotencyKey) headers.set("idempotency-key", idempotencyKey);
  return new Request("http://deepshovel.test/api/artifacts", {
    method: "POST", headers, body: bytes,
  });
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

test("the public artifact route exposes no deletion", () => {
  assert.equal(artifactsRoute.DELETE, undefined);
});

test("maintenance deletion refuses requests that arrived through Cloudflare", async () => {
  const { database, bucket } = fixture();
  try {
    const response = await DELETE(request(
      { artifactId, confirmArtifactId: artifactId },
      { "cf-ray": "8f00000000000000-SJC", "cf-connecting-ip": "203.0.113.7" },
    ));
    assert.equal(response.status, 403);
    assert.equal((await response.json()).error.code, "MAINTENANCE_LOCAL_ONLY");
    assert.deepEqual(bucket.deleted, []);
    assert.deepEqual(removedCompilerJobs, []);
    assert.equal(database.prepare("SELECT COUNT(*) AS count FROM artifacts").get().count, 1);
  } finally { database.close(); }
});

test("artifact deletion removes original bytes, terminal Compiler job, and all D1 identity", async () => {
  const { database, bucket } = fixture();
  try {
    assert.throws(() => database.prepare("DELETE FROM artifact_versions WHERE id=?").run(versionId), /immutable/u);
    const response = await DELETE(request({ artifactId, confirmArtifactId: artifactId }));
    assert.equal(response.status, 200);
    assert.deepEqual((await response.json()).deleted, { versions: 1, compileJobs: 1, snapshots: 0 });
    assert.deepEqual(removedCompilerJobs, [upstreamJobId]);
    assert.deepEqual(removedCompilerVersions, [versionId]);
    assert.deepEqual(bucket.deleted, [`artifacts/${sha}`]);
    for (const table of ["artifacts", "artifact_versions", "compile_jobs", "audit_events", "artifact_deletion_authorizations"]) {
      assert.equal(database.prepare(`SELECT COUNT(*) AS count FROM ${table}`).get().count, 0, table);
    }
  } finally { database.close(); }
});

test("artifact deletion purges external retrieval snapshots before bytes and D1 identity", async () => {
  const { database, bucket } = fixture();
  addSnapshot(database);
  try {
    const response = await DELETE(request({ artifactId, confirmArtifactId: artifactId }));
    assert.equal(response.status, 200);
    assert.deepEqual(removedRetrievalSnapshots, [{
      projectId: "DEEPSHOVEL-PROJECT",
      snapshotIds: [snapshotId],
    }]);
    assert.deepEqual(bucket.deleted, [`artifacts/${sha}`]);
    assert.equal(database.prepare("SELECT COUNT(*) AS count FROM evidence_graph_snapshots").get().count, 0);
  } finally { database.close(); }
});

test("artifact deletion removes Claim completion and cross-paper incident edges without dangling records", async () => {
  const { database } = fixture();
  const survivor = addKnowledgeGraphDeletionFixture(database);
  try {
    assert.equal(database.prepare("SELECT COUNT(*) AS count FROM corpus_claim_graph_completions").get().count, 1);
    assert.equal(database.prepare("SELECT COUNT(*) AS count FROM corpus_claim_graph_edges").get().count, 1);
    assert.equal(database.prepare("SELECT COUNT(*) AS count FROM openalex_paper_classifications").get().count, 2);
    assert.equal(database.prepare("SELECT COUNT(*) AS count FROM openalex_paper_topics").get().count, 2);

    const response = await DELETE(request({ artifactId, confirmArtifactId: artifactId }));
    assert.equal(response.status, 200);
    assert.equal((await response.json()).deleted.snapshots, 1);
    assert.deepEqual(removedRetrievalSnapshots, [{
      projectId: "DEEPSHOVEL-PROJECT",
      snapshotIds: [snapshotId],
    }]);
    assert.equal(database.prepare("SELECT COUNT(*) AS count FROM corpus_claim_graph_completions").get().count, 0);
    assert.equal(database.prepare("SELECT COUNT(*) AS count FROM corpus_claim_graph_edges").get().count, 0);
    assert.equal(database.prepare("SELECT COUNT(*) AS count FROM openalex_paper_classifications").get().count, 1);
    assert.equal(database.prepare("SELECT COUNT(*) AS count FROM openalex_paper_topics").get().count, 1);
    assert.equal(database.prepare("SELECT COUNT(*) AS count FROM evidence_retrieval_documents").get().count, 1);
    assert.equal(database.prepare("SELECT COUNT(*) AS count FROM evidence_retrieval_manifests").get().count, 1);
    assert.equal(database.prepare("SELECT COUNT(*) AS count FROM artifacts WHERE id = ?")
      .get(survivor.survivorArtifactId).count, 1);
    assert.equal(database.prepare("SELECT COUNT(*) AS count FROM evidence_graph_snapshots WHERE id = ?")
      .get(survivor.survivorSnapshotId).count, 1);
    assert.equal(database.prepare("SELECT COUNT(*) AS count FROM evidence_retrieval_documents WHERE claim_id = ?")
      .get(survivor.survivorClaimId).count, 1);
    assert.deepEqual(database.prepare("PRAGMA foreign_key_check").all(), []);
  } finally {
    database.close();
  }
});

test("citation deletion keeps A source facts when deleting B and purges them when deleting A", async (t) => {
  await t.test("deleting cited B removes only the derived reception chain", async () => {
    const { database } = fixture();
    const survivor = addKnowledgeGraphDeletionFixture(database);
    addCitationReceptionDeletionFixture(database, survivor.survivorSnapshotId, snapshotId);
    try {
      assert.throws(() => database.prepare(`UPDATE citation_source_manifests
        SET warnings_json = '["changed"]' WHERE snapshot_id = ?`).run(survivor.survivorSnapshotId), /immutable/u);
      assert.throws(() => database.prepare("DELETE FROM bibliographic_references").run(), /immutable/u);
      assert.throws(() => database.prepare(`INSERT INTO citation_reception_runs
        (id,project_id,citing_snapshot_id,reference_id,cited_snapshot_id,
         processor_version,resolution_method,resolution_confidence,extraction_run_id,
         extraction_model,extraction_prompt_version,assertions_sha256,
         verification_run_id,verifier_model,verifier_prompt_version,assertion_count)
        SELECT ?,project_id,citing_snapshot_id,reference_id,cited_snapshot_id,
         processor_version,resolution_method,resolution_confidence,'negative-count-run',
         extraction_model,extraction_prompt_version,assertions_sha256,
         NULL,NULL,NULL,-1 FROM citation_reception_runs LIMIT 1`).run(
        `citation-run:sha256:${"f".repeat(64)}`,
      ), /citation_reception_run_count_check/u);

      const response = await DELETE(request({ artifactId, confirmArtifactId: artifactId }));
      assert.equal(response.status, 200);
      for (const table of ["citation_reception_runs", "citation_assertions", "citation_assessments"]) {
        assert.equal(database.prepare(`SELECT COUNT(*) AS count FROM ${table}`).get().count, 0, table);
      }
      for (const table of ["citation_source_manifests", "bibliographic_references", "citation_mentions"]) {
        assert.equal(database.prepare(`SELECT COUNT(*) AS count FROM ${table}`).get().count, 1, table);
      }
      assert.equal(database.prepare(`SELECT COUNT(*) AS count FROM citation_source_manifests
        WHERE snapshot_id = ?`).get(survivor.survivorSnapshotId).count, 1);
      assert.deepEqual(database.prepare("PRAGMA foreign_key_check").all(), []);
    } finally {
      database.close();
    }
  });

  await t.test("deleting citing A removes source facts and the derived reception chain in FK order", async () => {
    const { database } = fixture();
    const survivor = addKnowledgeGraphDeletionFixture(database);
    addCitationReceptionDeletionFixture(database, snapshotId, survivor.survivorSnapshotId);
    try {
      const response = await DELETE(request({ artifactId, confirmArtifactId: artifactId }));
      assert.equal(response.status, 200);
      for (const table of [
        "citation_assessments",
        "citation_assertions",
        "citation_reception_runs",
        "citation_mentions",
        "bibliographic_references",
        "citation_source_manifests",
      ]) {
        assert.equal(database.prepare(`SELECT COUNT(*) AS count FROM ${table}`).get().count, 0, table);
      }
      assert.equal(database.prepare("SELECT COUNT(*) AS count FROM evidence_graph_snapshots WHERE id = ?")
        .get(survivor.survivorSnapshotId).count, 1);
      assert.deepEqual(database.prepare("PRAGMA foreign_key_check").all(), []);
    } finally {
      database.close();
    }
  });

  await t.test("deletion authorization blocks new source facts and reception runs at either endpoint", async () => {
    const { database } = fixture();
    const survivor = addKnowledgeGraphDeletionFixture(database);
    addCitationReceptionDeletionFixture(database, snapshotId, survivor.survivorSnapshotId);
    const barrier = /deletion is in progress/u;
    try {
      database.prepare(`INSERT INTO artifact_deletion_authorizations
        (artifact_id,requested_by) VALUES (?,?)`).run(artifactId, "owner@deepshovel.local");
      assert.throws(() => database.prepare(`INSERT INTO citation_source_manifests
        SELECT * FROM citation_source_manifests WHERE snapshot_id = ?`).run(snapshotId), barrier);
      assert.throws(() => database.prepare(`INSERT INTO bibliographic_references
        SELECT ?,project_id,citing_snapshot_id,ordinal + 1,label,raw_text,normalized_doi,
         year,source_block_id,page,created_at FROM bibliographic_references LIMIT 1`).run(
        `citation-reference:sha256:${"e".repeat(64)}`,
      ), barrier);
      assert.throws(() => database.prepare(`INSERT INTO citation_mentions
        SELECT ?,project_id,citing_snapshot_id,reference_id,marker,exact_quote,context_text,
         source_block_id,context_block_ids_json,page,bbox_json,char_start,char_end,
         group_size,candidate_functions_json,created_at FROM citation_mentions LIMIT 1`).run(
        `citation-mention:sha256:${"e".repeat(64)}`,
      ), barrier);
      assert.throws(() => database.prepare(`INSERT INTO citation_reception_runs
        SELECT ?,project_id,citing_snapshot_id,reference_id,cited_snapshot_id,
         processor_version,resolution_method,resolution_confidence,'barrier-run',
         extraction_model,extraction_prompt_version,assertions_sha256,
         verification_run_id,verifier_model,verifier_prompt_version,assertion_count,created_at
         FROM citation_reception_runs LIMIT 1`).run(
        `citation-run:sha256:${"e".repeat(64)}`,
      ), barrier);
    } finally {
      database.close();
    }

    const { database: citedDatabase } = fixture();
    const citedSurvivor = addKnowledgeGraphDeletionFixture(citedDatabase);
    addCitationReceptionDeletionFixture(citedDatabase, citedSurvivor.survivorSnapshotId, snapshotId);
    try {
      citedDatabase.prepare(`INSERT INTO artifact_deletion_authorizations
        (artifact_id,requested_by) VALUES (?,?)`).run(artifactId, "owner@deepshovel.local");
      assert.throws(() => citedDatabase.prepare(`INSERT INTO citation_reception_runs
        SELECT ?,project_id,citing_snapshot_id,reference_id,cited_snapshot_id,
         processor_version,resolution_method,resolution_confidence,'cited-barrier-run',
         extraction_model,extraction_prompt_version,assertions_sha256,
         verification_run_id,verifier_model,verifier_prompt_version,assertion_count,created_at
         FROM citation_reception_runs LIMIT 1`).run(
        `citation-run:sha256:${"e".repeat(64)}`,
      ), barrier);
    } finally {
      citedDatabase.close();
    }
  });
});

test("retrieval purge failure leaves source bytes and immutable D1 identity for a safe retry", async () => {
  const { database, bucket } = fixture();
  addSnapshot(database);
  globalThis.__artifactRetrievalDeleteError = new globalThis.__artifactDeleteApiError(
    503,
    "RETRIEVAL_UNAVAILABLE",
    "retrieval service is unavailable",
    { retryable: true },
  );
  try {
    const response = await DELETE(request({ artifactId, confirmArtifactId: artifactId }));
    assert.equal(response.status, 503);
    assert.deepEqual(removedRetrievalSnapshots, [{
      projectId: "DEEPSHOVEL-PROJECT",
      snapshotIds: [snapshotId],
    }]);
    assert.deepEqual(bucket.deleted, []);
    assert.ok(bucket.objects.has(`artifacts/${sha}`));
    assert.equal(database.prepare("SELECT COUNT(*) AS count FROM artifacts").get().count, 1);
    assert.equal(database.prepare("SELECT COUNT(*) AS count FROM evidence_graph_snapshots").get().count, 1);
  } finally {
    globalThis.__artifactRetrievalDeleteError = null;
    database.close();
  }
});

test("artifact deletion treats an already purged Compiler job as idempotent success", async () => {
  const { database, bucket } = fixture();
  globalThis.__artifactCompilerRemoveError = new globalThis.__artifactDeleteApiError(
    502,
    "UPSTREAM_JOB_NOT_FOUND",
    "compile job was not found",
    { retryable: false },
  );
  try {
    const response = await DELETE(request({ artifactId, confirmArtifactId: artifactId }));
    assert.equal(response.status, 200);
    assert.deepEqual(removedCompilerJobs, [upstreamJobId]);
    assert.deepEqual(bucket.deleted, [`artifacts/${sha}`]);
    assert.equal(database.prepare("SELECT COUNT(*) AS count FROM artifacts").get().count, 0);
    assert.equal(database.prepare("SELECT COUNT(*) AS count FROM compile_jobs").get().count, 0);
  } finally {
    globalThis.__artifactCompilerRemoveError = null;
    database.close();
  }
});

test("active Compile is cancelled before deletion and mismatched confirmation is rejected", async () => {
  const { database, bucket } = fixture("running");
  try {
    const active = await DELETE(request({ artifactId, confirmArtifactId: artifactId }));
    assert.equal(active.status, 200);
    assert.deepEqual(bucket.deleted, [`artifacts/${sha}`]);
    assert.deepEqual(removedCompilerJobs, [upstreamJobId, upstreamJobId]);
    assert.equal(database.prepare("SELECT COUNT(*) AS count FROM artifacts").get().count, 0);
  } finally { database.close(); }

  const { database: mismatchDatabase, bucket: mismatchBucket } = fixture();
  try {
    const mismatch = await DELETE(request({ artifactId, confirmArtifactId: `${artifactId}-wrong` }));
    assert.equal(mismatch.status, 400);
    assert.equal((await mismatch.json()).error.code, "INVALID_DELETE_CONFIRMATION");
    assert.deepEqual(mismatchBucket.deleted, []);
  } finally { mismatchDatabase.close(); }
});

test("selected deletion uses the complete purge boundary and all deletion is unavailable", async () => {
  const { database, bucket } = fixture();
  try {
    const response = await DELETE(request({ artifactIds: [artifactId], confirm: "DELETE_SELECTED" }));
    assert.equal(response.status, 200);
    assert.equal((await response.json()).deletedCount, 1);
    assert.deepEqual(bucket.deleted, [`artifacts/${sha}`]);
    assert.equal(database.prepare("SELECT COUNT(*) AS count FROM artifacts").get().count, 0);
  } finally { database.close(); }

  const { database: allDatabase, bucket: allBucket } = fixture();
  try {
    const response = await DELETE(request({ all: true, confirm: "DELETE_ALL" }));
    assert.equal(response.status, 400);
    assert.equal((await response.json()).error.code, "INVALID_DELETE_CONFIRMATION");
    assert.deepEqual(allBucket.deleted, []);
    assert.equal(allDatabase.prepare("SELECT COUNT(*) AS count FROM artifacts").get().count, 1);
  } finally { allDatabase.close(); }
});

test("deletion authorization is a write barrier and captures a just-committed version", async () => {
  const { database, bucket, gatewayDb } = fixture();
  const lateSha = "d".repeat(64);
  const lateVersionId = `artifact-version:sha256:${lateSha}`;
  gatewayDb.beforeAuthorization = () => {
    database.prepare(`INSERT INTO artifact_versions
      (id,artifact_id,version_number,sha256,source_locator,media_type,original_file_name,byte_size,object_key,client_request_id,request_hash,created_by)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`).run(
      lateVersionId, artifactId, 2, lateSha, "upload://late.pdf", "application/pdf",
      "late.pdf", 8, `artifacts/${lateSha}`, "upload-late", "e".repeat(64),
      "owner@deepshovel.local",
    );
  };
  try {
    const response = await DELETE(request({ artifactId, confirmArtifactId: artifactId }));
    assert.equal(response.status, 200);
    assert.equal((await response.json()).deleted.versions, 2);
    assert.deepEqual(bucket.deleted.sort(), [`artifacts/${lateSha}`, `artifacts/${sha}`].sort());
  } finally { database.close(); }

  const { database: barrierDatabase } = fixture();
  try {
    barrierDatabase.prepare(`INSERT INTO artifact_deletion_authorizations
      (artifact_id, requested_by) VALUES (?, ?)`).run(artifactId, "owner@deepshovel.local");
    assert.throws(() => barrierDatabase.prepare(`INSERT INTO artifact_versions
      (id,artifact_id,version_number,sha256,source_locator,media_type,original_file_name,byte_size,object_key,client_request_id,request_hash,created_by)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`).run(
      lateVersionId, artifactId, 2, lateSha, "upload://late.pdf", "application/pdf",
      "late.pdf", 8, `artifacts/${lateSha}`, "upload-late", "e".repeat(64),
      "owner@deepshovel.local",
    ), /deletion is in progress/u);
    assert.throws(() => barrierDatabase.prepare(`INSERT INTO compile_jobs
      (id,project_id,artifact_version_id,upstream_job_id,status,stage,progress,timings_json,
       snapshot_id,client_request_id,request_hash,created_by,created_at,updated_at,expires_at)
      VALUES (?,?,?,?,?,?,?,?,NULL,?,?,?,?,?,?)`).run(
      "web-job-late", "DEEPSHOVEL-PROJECT", versionId,
      "742a6035-3bd3-47c6-86a9-3ade0faab131", "queued", "queued", 0, "{}",
      "compile-late", "f".repeat(64), "owner@deepshovel.local",
      "2026-08-16T00:00:00Z", "2026-08-16T00:00:00Z", "2026-08-17T00:00:00Z",
    ), /deletion is in progress/u);
  } finally { barrierDatabase.close(); }
});

test("an upload already past its precheck cannot leave R2 bytes behind deletion", async () => {
  const { database, bucket, gatewayDb } = fixture();
  const bytes = new TextEncoder().encode("%PDF-1.7\nconcurrent upload");
  const digest = await globalThis.__artifactStoreHooks.sha256(bytes);
  const reachedBatch = deferred();
  const releaseBatch = deferred();
  gatewayDb.beforeBatch = async (statements) => {
    if (!statements.some((statement) => statement.sql.includes("INSERT INTO artifact_versions"))) return;
    gatewayDb.beforeBatch = null;
    reachedBatch.resolve();
    await releaseBatch.promise;
  };
  try {
    const uploading = POST(uploadRequest(bytes, { targetArtifactId: artifactId }));
    await reachedBatch.promise;
    assert.ok(bucket.objects.has(`artifacts/${digest}`), "R2 put must finish before the deletion barrier");

    const deletion = await DELETE(request({ artifactId, confirmArtifactId: artifactId }));
    assert.equal(deletion.status, 200);
    releaseBatch.resolve();

    const upload = await uploading;
    assert.equal(upload.status, 409);
    assert.equal((await upload.json()).error.code, "ARTIFACT_DELETION_IN_PROGRESS");
    assert.equal(database.prepare("SELECT COUNT(*) AS count FROM artifacts").get().count, 0);
    assert.equal(bucket.objects.size, 0, "failed in-flight upload must compensate its R2 object");
  } finally {
    releaseBatch.resolve();
    database.close();
  }
});

test("duplicate and replay repair paths recheck liveness after R2 access", async () => {
  const originalSha = globalThis.__artifactStoreHooks.sha256;
  globalThis.__artifactStoreHooks.sha256 = async (value) => (
    typeof value === "string" ? "b".repeat(64) : sha
  );
  try {
    for (const replay of [false, true]) {
      const { database, bucket, gatewayDb } = fixture();
      const bytes = new TextEncoder().encode("%PDF-1.7\nfixture replay");
      const reachedLiveness = deferred();
      const releaseLiveness = deferred();
      let liveLookups = 0;
      gatewayDb.beforeFirst = async (sql) => {
        if (!sql.includes("LEFT JOIN artifact_deletion_authorizations")) return;
        liveLookups += 1;
        const gateAt = replay ? 1 : 2;
        if (liveLookups !== gateAt) return;
        gatewayDb.beforeFirst = null;
        reachedLiveness.resolve();
        await releaseLiveness.promise;
      };
      try {
        const uploading = POST(uploadRequest(bytes, replay ? { idempotencyKey: "upload-1" } : {}));
        await reachedLiveness.promise;
        const deletion = await DELETE(request({ artifactId, confirmArtifactId: artifactId }));
        assert.equal(deletion.status, 200);
        releaseLiveness.resolve();
        const upload = await uploading;
        assert.equal(upload.status, 409, replay ? "replay" : "duplicate");
        assert.equal(bucket.objects.size, 0, replay ? "replay" : "duplicate");
      } finally {
        releaseLiveness.resolve();
        database.close();
      }
    }
  } finally {
    globalThis.__artifactStoreHooks.sha256 = originalSha;
  }
});

test("R2 compensation restores bytes when a live same-digest reference wins the race", async () => {
  const { database, bucket, gatewayDb } = fixture();
  const bytes = new TextEncoder().encode("%PDF-1.7\nshared digest");
  const digest = await globalThis.__artifactStoreHooks.sha256(bytes);
  const digestKey = `artifacts/${digest}`;
  const reachedBatch = deferred();
  const releaseBatch = deferred();
  gatewayDb.beforeBatch = async (statements) => {
    if (!statements.some((statement) => statement.sql.includes("INSERT INTO artifact_versions"))) return;
    gatewayDb.beforeBatch = null;
    reachedBatch.resolve();
    await releaseBatch.promise;
  };
  let insertedSurvivor = false;
  bucket.beforeDelete = (key) => {
    if (key !== digestKey || insertedSurvivor) return;
    insertedSurvivor = true;
    const survivorId = "artifact:00000000-0000-4000-8000-000000000002";
    database.prepare("INSERT INTO artifacts (id,project_id,preferred_name,artifact_type,created_by) VALUES (?,?,?,?,?)")
      .run(survivorId, "DEEPSHOVEL-PROJECT", "survivor.pdf", "paper", "owner@deepshovel.local");
    database.prepare(`INSERT INTO artifact_versions
      (id,artifact_id,version_number,sha256,source_locator,media_type,original_file_name,byte_size,object_key,client_request_id,request_hash,created_by)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`).run(
      `artifact-version:sha256:${digest}`, survivorId, 1, digest,
      "upload://survivor.pdf", "application/pdf", "survivor.pdf", bytes.byteLength,
      digestKey, "upload-survivor", "9".repeat(64), "owner@deepshovel.local",
    );
  };
  try {
    const uploading = POST(uploadRequest(bytes, { targetArtifactId: artifactId }));
    await reachedBatch.promise;
    const deletion = await DELETE(request({ artifactId, confirmArtifactId: artifactId }));
    assert.equal(deletion.status, 200);
    releaseBatch.resolve();
    const upload = await uploading;
    assert.equal(upload.status, 409);
    assert.equal((await upload.json()).error.code, "ARTIFACT_BYTES_ALREADY_STORED");
    assert.equal(insertedSurvivor, true);
    assert.ok(bucket.objects.has(digestKey), "a newly live reference must restore the deleted object");
  } finally {
    releaseBatch.resolve();
    database.close();
  }
});

test("a targeted duplicate cannot silently attach bytes owned by another Artifact", async () => {
  const originalSha = globalThis.__artifactStoreHooks.sha256;
  globalThis.__artifactStoreHooks.sha256 = async (value) => (
    typeof value === "string" ? "b".repeat(64) : sha
  );
  const { database } = fixture();
  const targetId = "artifact:00000000-0000-4000-8000-000000000002";
  database.prepare("INSERT INTO artifacts (id,project_id,preferred_name,artifact_type,created_by) VALUES (?,?,?,?,?)")
    .run(targetId, "DEEPSHOVEL-PROJECT", "target.pdf", "paper", "owner@deepshovel.local");
  try {
    const response = await POST(uploadRequest(
      new TextEncoder().encode("%PDF-1.7\nduplicate target"),
      { targetArtifactId: targetId },
    ));
    assert.equal(response.status, 409);
    assert.equal((await response.json()).error.code, "ARTIFACT_BYTES_ALREADY_STORED");
    assert.equal(database.prepare("SELECT COUNT(*) AS count FROM artifact_versions WHERE artifact_id = ?")
      .get(targetId).count, 0);
  } finally {
    globalThis.__artifactStoreHooks.sha256 = originalSha;
    database.close();
  }
});

test("the atomic deletion barrier captures a Compile reservation committed just before it", async () => {
  const { database, gatewayDb } = fixture();
  const lateUpstreamId = "742a6035-3bd3-47c6-86a9-3ade0faab131";
  gatewayDb.beforeBatch = async (statements) => {
    if (!statements[0]?.sql.includes("UPDATE compile_jobs SET status = 'failed'")) return;
    gatewayDb.beforeBatch = null;
    database.prepare(`INSERT INTO compile_jobs
      (id,project_id,artifact_version_id,upstream_job_id,status,stage,progress,timings_json,
       snapshot_id,client_request_id,request_hash,created_by,created_at,updated_at,expires_at)
      VALUES (?,?,?,?,?,?,?,?,NULL,?,?,?,?,?,?)`).run(
      "web-job-late", "DEEPSHOVEL-PROJECT", versionId, lateUpstreamId,
      "queued", "queued", 0, "{}", "compile-late", "f".repeat(64),
      "owner@deepshovel.local", "2026-08-16T00:00:00Z",
      "2026-08-16T00:00:00Z", "2026-08-17T00:00:00Z",
    );
  };
  try {
    const response = await DELETE(request({ artifactId, confirmArtifactId: artifactId }));
    assert.equal(response.status, 200);
    assert.equal((await response.json()).deleted.compileJobs, 2);
    assert.deepEqual(
      removedCompilerJobs,
      [upstreamJobId, lateUpstreamId, lateUpstreamId],
      "terminal jobs are purged once; deletion-cancelled jobs are cancelled then purged",
    );
  } finally { database.close(); }
});

test("a terminal Compile audit cannot be appended after its Artifact is purged", async () => {
  const { database, gatewayDb } = fixture("running");
  const reachedAudit = deferred();
  const releaseAudit = deferred();
  gatewayDb.beforeAuditInsert = async () => {
    reachedAudit.resolve();
    await releaseAudit.promise;
  };
  const row = {
    id: "web-job-1",
    projectId: "DEEPSHOVEL-PROJECT",
    artifactVersionId: versionId,
    upstreamJobId,
    status: "running",
    stage: "extracting",
    progress: 60,
    timingsJson: "{}",
    errorJson: null,
    snapshotId: null,
    clientRequestId: "compile-1",
    requestHash: "c".repeat(64),
    createdBy: "owner@deepshovel.local",
    createdAt: "2026-08-16T00:00:00Z",
    updatedAt: "2026-08-16T00:00:00Z",
    expiresAt: "2026-08-17T00:00:00Z",
  };
  try {
    const terminalUpdate = markCompileJobFailed(row, {
      code: "UPSTREAM_FAILED", message: "fixture", retryable: false,
    });
    await reachedAudit.promise;
    const deletion = await DELETE(request({ artifactId, confirmArtifactId: artifactId }));
    assert.equal(deletion.status, 200);
    releaseAudit.resolve();
    await terminalUpdate;
    assert.equal(database.prepare("SELECT COUNT(*) AS count FROM audit_events").get().count, 0);
    assert.equal(database.prepare("SELECT COUNT(*) AS count FROM compile_jobs").get().count, 0);
  } finally {
    releaseAudit.resolve();
    database.close();
  }
});
