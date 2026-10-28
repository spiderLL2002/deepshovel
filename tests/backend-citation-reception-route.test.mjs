import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import { register } from "node:module";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";

const PROJECT_ID = "DEEPSHOVEL-PROJECT";
const routeState = { DB: null, active: [], calls: [], responseFactory: null };
globalThis.__citationRouteState = routeState;
globalThis.__citationRouteDigest = (value) => createHash("sha256").update(value).digest("hex");

const storeStub = `
export const PROJECT_ID=${JSON.stringify(PROJECT_ID)};
export class ApiError extends Error { constructor(status,code,message,details){ super(message); this.status=status; this.code=code; this.details=details; } }
export async function getBindings(){ return {DB:globalThis.__citationRouteState.DB}; }
export async function getCompilerConfiguration(){ return {baseUrl:"http://compiler.test",sharedSecret:"citation-route-test-secret-0123456789"}; }
export function assertSameOrigin(request){ const origin=request.headers.get("origin"); if(origin&&origin!==new URL(request.url).origin) throw new ApiError(403,"CROSS_ORIGIN_WRITE_REJECTED","Cross-origin write rejected"); }
export async function readJsonObject(request,maximum){ const text=await request.text(); if(new TextEncoder().encode(text).byteLength>maximum) throw new ApiError(413,"REQUEST_TOO_LARGE","request too large"); const value=JSON.parse(text); if(!value||typeof value!=="object"||Array.isArray(value)) throw new ApiError(400,"INVALID_JSON","body must be an object"); return value; }
export function safeError(error){ return Response.json({ok:false,error:{code:error?.code??"INTERNAL_ERROR",message:error instanceof Error?error.message:String(error)}},{status:error?.status??500}); }
export const sha256Hex=async(value)=>globalThis.__citationRouteDigest(value);
`;
const citationStub = `
export const CITATION_RECEPTION_SCHEMA_VERSION="citation-reception/1.0";
export const CITATION_RECEPTION_PROCESSOR_VERSION="citation-reception-processor/1.0.0";
export function buildCitationCandidateEntities(graph){ return structuredClone(graph.candidates??[]); }
`;
const graphStub = `
export const SNAPSHOT_ID_RE=/^snapshot:sha256:([a-f0-9]{64})$/u;
function canonical(value){if(Array.isArray(value))return value.map(canonical);if(value&&typeof value==="object"){const out={};for(const key of Object.keys(value).sort())out[key]=canonical(value[key]);return out;}return value;}
export const canonicalStringify=(value)=>JSON.stringify(canonical(value));
export async function parseEvidenceGraph(value){return value;}
`;
const retrievalStub = `
export async function loadActiveEvidenceRetrievalManifests(){ return structuredClone(globalThis.__citationRouteState.active); }
`;
const compilerStub = `
export class CompilerClient {
  constructor(configuration){ this.configuration=configuration; }
  async analyzeCitationReception(request){
    globalThis.__citationRouteState.calls.push(structuredClone(request));
    return structuredClone(globalThis.__citationRouteState.responseFactory(request));
  }
}
`;
const loader = `
let rootUrl,storeUrl,citationUrl,graphUrl,retrievalUrl,compilerUrl;
export function initialize(data){({rootUrl,storeUrl,citationUrl,graphUrl,retrievalUrl,compilerUrl}=data);}
export async function resolve(specifier,context,nextResolve){
  if(specifier==="@/lib/server/evidence-store")return {url:storeUrl,shortCircuit:true};
  if(specifier==="@/lib/citation-reception")return {url:citationUrl,shortCircuit:true};
  if(specifier==="@/lib/evidence-graph")return {url:graphUrl,shortCircuit:true};
  if(specifier==="@/lib/evidence-retrieval")return {url:retrievalUrl,shortCircuit:true};
  if(specifier==="@/lib/server/compiler-client")return {url:compilerUrl,shortCircuit:true};
  if(specifier.startsWith("@/")){let path=specifier.slice(2);if(!/\.[cm]?[jt]sx?$/.test(path))path+=".ts";return {url:new URL(path,rootUrl).href,shortCircuit:true};}
  return nextResolve(specifier,context);
}`;
register(`data:text/javascript,${encodeURIComponent(loader)}`, {
  parentURL: import.meta.url,
  data: {
    rootUrl: new URL("../", import.meta.url).href,
    storeUrl: `data:text/javascript,${encodeURIComponent(storeStub)}`,
    citationUrl: `data:text/javascript,${encodeURIComponent(citationStub)}`,
    graphUrl: `data:text/javascript,${encodeURIComponent(graphStub)}`,
    retrievalUrl: `data:text/javascript,${encodeURIComponent(retrievalStub)}`,
    compilerUrl: `data:text/javascript,${encodeURIComponent(compilerStub)}`,
  },
});

const { GET, POST } = await import("../app/api/citation-reception/route.ts");

class Statement {
  args = [];
  constructor(owner, sql) { this.owner = owner; this.sql = sql; }
  bind(...args) { this.args = args; return this; }
  async first() { return this.owner.database.prepare(this.sql).get(...this.args); }
  async all() { return { results: this.owner.database.prepare(this.sql).all(...this.args) }; }
  async run() { const result = this.owner.database.prepare(this.sql).run(...this.args); return { success: true, meta: { changes: Number(result.changes) } }; }
}
class SqliteD1 {
  constructor(database) { this.database = database; }
  prepare(sql) { return new Statement(this, sql); }
  async batch(statements) {
    this.database.exec("BEGIN IMMEDIATE");
    try {
      const values = [];
      for (const statement of statements) values.push(await statement.run());
      this.database.exec("COMMIT");
      return values;
    } catch (error) {
      this.database.exec("ROLLBACK");
      throw error;
    }
  }
}

function migrate(database) {
  const names = fs.readdirSync(new URL("../drizzle/", import.meta.url))
    .filter((name) => /^\d{4}_.+\.sql$/u.test(name)).sort();
  for (const name of names) {
    const sql = fs.readFileSync(new URL(`../drizzle/${name}`, import.meta.url), "utf8");
    for (const statement of sql.split("--> statement-breakpoint")) if (statement.trim()) database.exec(statement);
  }
}

const digest = (label) => createHash("sha256").update(`citation-route:${label}`).digest("hex");
function paper(label) {
  const rootSha = digest(`root:${label}`);
  const graphSha = digest(`graph:${label}`);
  const artifactId = `artifact:${label}`;
  const versionId = `artifact-version:sha256:${rootSha}`;
  const snapshotId = `snapshot:sha256:${graphSha}`;
  const anchorId = `anchor:${label}`;
  const observationId = `observation:${label}`;
  const claimId = `claim:${label}`;
  const graph = {
    paper_id: `paper:${label}`,
    artifacts: [], artifact_versions: [], visual_evidence: [], assumptions: [], reasoning: [], claims: [],
    anchors: [{ kind: "source_anchor", id: anchorId, artifact_version_id: versionId, anchor_type: "text",
      selector: { page: 2, section: "Results", paragraph: null, table: null, row_start: null, row_end: null,
        row_range_label: null, column_names: [], figure: null, series: null, char_start: null, char_end: null,
        region: null, region_label: null, dataset_path: null }, quote: `${label} source evidence`, quote_sha256: digest(`quote:${label}`) }],
    observations: [{ kind: "observation", id: observationId, statement: `${label} measured the result`, anchor_ids: [anchorId] }],
    candidates: [{ id: observationId, kind: "observation", text: `${label} measured the result`, anchor_ids: [anchorId], root_claim_ids: [claimId] }],
  };
  return { label, rootSha, graphSha, artifactId, versionId, snapshotId, anchorId, observationId, claimId, graph };
}

function seedPaper(database, item, ordinal) {
  database.prepare(`INSERT INTO artifacts (id,project_id,preferred_name,artifact_type,created_by,created_at)
    VALUES (?,?,?,?,?,?)`).run(item.artifactId, PROJECT_ID, `Paper ${item.label}`, "paper", "test", `2026-08-20T00:00:0${ordinal}Z`);
  database.prepare(`INSERT INTO artifact_versions
    (id,artifact_id,version_number,sha256,source_locator,media_type,original_file_name,byte_size,object_key,etag,client_request_id,request_hash,created_by,created_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    item.versionId, item.artifactId, 1, item.rootSha, `${item.label}.pdf`, "application/pdf", `${item.label}.pdf`, 10,
    `artifacts/${item.rootSha}`, null, `request:${item.label}`, digest(`request:${item.label}`), "test", `2026-08-20T00:00:0${ordinal}Z`,
  );
  database.prepare(`INSERT INTO evidence_graph_snapshots
    (id,project_id,root_artifact_version_id,schema_version,graph_sha256,graph_json,counts_json,extractor_run_id,extractor_model,extractor_prompt_version,created_by,created_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    item.snapshotId, PROJECT_ID, item.versionId, "scientific-evidence-graph/1.0", item.graphSha,
    JSON.stringify(item.graph), "{}", `extractor:${item.label}`, "gpt-5.6-luna", "compiler-extractor/1.0.0", "test", `2026-08-20T00:00:0${ordinal}Z`,
  );
}

function seedIdentity(database, item, doi, workId) {
  database.prepare(`INSERT INTO openalex_paper_classifications
    (snapshot_id,project_id,source,classifier,openalex_work_id,matched_doi,created_at)
    VALUES (?,?,?,?,?,?,?)`).run(item.snapshotId, PROJECT_ID, "openalex_work", "OpenAlex Work API Topics", workId, doi, "2026-08-20T00:01:00Z");
}

function responseFor(request) {
  const assertionId = `citation-assertion:sha256:${digest("assertion")}`;
  return {
    schemaVersion: "citation-reception/1.0",
    processorVersion: "citation-reception-processor/1.0.0",
    citingSnapshotId: request.citing_snapshot_id,
    citedSnapshotId: request.cited_snapshot_id,
    referenceId: request.reference.id,
    extraction: {
      runId: `citation-extraction:sha256:${digest("extraction")}`,
      model: "gpt-5.6-luna", promptVersion: "citation-extractor/1.0.0", attempts: 1,
      assertionsSha256: digest("frozen-assertions"),
      assertions: [{ id: assertionId, mentionId: request.mentions[0].id,
        attributedProposition: "B measured the same direction.", function: "result_comparison", stance: "supports",
        confirmationBasis: "consistent_with_citing_evidence", targetEntityId: "observation:b", targetEntityKind: "observation",
        citingEvidenceEntityIds: ["observation:a"], confidence: 0.93 }],
    },
    verification: {
      runId: `citation-verification:sha256:${digest("verification")}`,
      model: "gpt-5.6-luna", promptVersion: "citation-verifier/1.0.0", attempts: 1,
      extractionSha256: digest("frozen-assertions"),
      assessments: [{ assertionId, faithfulness: "faithful", confirmationBasis: "consistent_with_citing_evidence",
        independence: "independent", confidence: 0.91, evidenceIds: ["observation:a", "observation:b"], limitations: [] }],
    },
  };
}

function emptyResponseFor(request) {
  return {
    schemaVersion: "citation-reception/1.0",
    processorVersion: "citation-reception-processor/1.0.0",
    citingSnapshotId: request.citing_snapshot_id,
    citedSnapshotId: request.cited_snapshot_id,
    referenceId: request.reference.id,
    extraction: {
      runId: `citation-extraction:sha256:${digest("empty-extraction")}`,
      model: "gpt-5.6-luna", promptVersion: "citation-extractor/1.0.0", attempts: 1,
      assertionsSha256: digest("empty-frozen-assertions"), assertions: [],
    },
    verification: null,
  };
}

const database = new DatabaseSync(":memory:");
database.exec("PRAGMA foreign_keys=ON");
migrate(database);
routeState.DB = new SqliteD1(database);
routeState.responseFactory = responseFor;
const citing = paper("a");
const target = paper("b");
seedPaper(database, citing, 1);
seedPaper(database, target, 2);
seedIdentity(database, target, "10.1234/target", "https://openalex.org/W100");
routeState.active = [citing, target].map((item) => ({ snapshotId: item.snapshotId }));

const referenceId = `citation-reference:sha256:${digest("reference")}`;
const mentionId = `citation-mention:sha256:${digest("mention")}`;
database.prepare(`INSERT INTO citation_source_manifests
  (snapshot_id,project_id,schema_version,extractor_version,source_sha256,reference_count,mention_count,warnings_json,created_at)
  VALUES (?,?,?,?,?,?,?,?,?)`).run(
  citing.snapshotId, PROJECT_ID, "citation-source/1.0", "citation-source-extractor/1.0", digest("source"), 1, 1, "[]", "2026-08-20T00:02:00Z",
);
database.prepare(`INSERT INTO bibliographic_references
  (id,project_id,citing_snapshot_id,ordinal,label,raw_text,normalized_doi,year,source_block_id,page,created_at)
  VALUES (?,?,?,?,?,?,?,?,?,?,?)`).run(
  referenceId, PROJECT_ID, citing.snapshotId, 7, "7", "B. DOI: 10.1234/target", "10.1234/target", 2025,
  "block:reference", 8, "2026-08-20T00:02:00Z",
);
database.prepare(`INSERT INTO citation_mentions
  (id,project_id,citing_snapshot_id,reference_id,marker,exact_quote,context_text,source_block_id,context_block_ids_json,page,bbox_json,char_start,char_end,group_size,candidate_functions_json,created_at)
  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
  mentionId, PROJECT_ID, citing.snapshotId, referenceId, "[7]", "Our result agrees with B [7].",
  "Our independent result agrees with B [7].", "block:result", "[\"block:result\"]", 4, null, 25, 28, 1,
  "[\"result_comparison\"]", "2026-08-20T00:02:00Z",
);

test("Citation Reception GET distinguishes pending coverage, POST analyzes, and GET hydrates exact A evidence", async () => {
  const before = await GET(new Request(`https://example.test/api/citation-reception?snapshotId=${encodeURIComponent(target.snapshotId)}`));
  assert.equal(before.status, 200);
  const pending = await before.json();
  assert.equal(pending.status, "not_started");
  assert.deepEqual(pending.coverage, {
    eligibleActivePapers: 1, analyzedActivePapers: 0, complete: false, asOf: pending.coverage.asOf,
  });

  const process = await POST(new Request("https://example.test/api/citation-reception", {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ snapshotId: target.snapshotId }),
  }));
  assert.equal(process.status, 200);
  assert.equal((await process.json()).result.processedPairs, 1);
  assert.equal(routeState.calls.length, 1);
  assert.equal(routeState.calls[0].reference.id, referenceId);
  assert.equal(routeState.calls[0].mentions[0].group_size, 1);

  const after = await GET(new Request(`https://example.test/api/citation-reception?snapshotId=${encodeURIComponent(target.snapshotId)}`));
  assert.equal(after.status, 200);
  const ready = await after.json();
  assert.equal(ready.status, "ready");
  assert.equal(ready.coverage.complete, true);
  assert.equal(ready.summary.independentConsistentFindings, 1);
  assert.equal(ready.items[0].target.entityId, "observation:b");
  assert.equal(ready.items[0].citationContext.statement, "Our result agrees with B [7].");
  assert.equal(ready.items[0].confirmingEvidence.statement, "a measured the result");
  assert.equal(ready.items[0].citationContext.source.anchor.selector.page, 4);

  routeState.responseFactory = emptyResponseFor;
  const reprocess = await POST(new Request("https://example.test/api/citation-reception", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ snapshotId: target.snapshotId, reprocess: true }),
  }));
  assert.equal(reprocess.status, 200);
  assert.equal(database.prepare("SELECT COUNT(*) AS count FROM citation_reception_runs").get().count, 1);

  const empty = await GET(new Request(`https://example.test/api/citation-reception?snapshotId=${encodeURIComponent(target.snapshotId)}`));
  assert.equal(empty.status, 200);
  const emptyReady = await empty.json();
  assert.equal(emptyReady.status, "ready");
  assert.equal(emptyReady.coverage.analyzedActivePapers, 1);
  assert.equal(emptyReady.coverage.complete, true);
  assert.deepEqual(emptyReady.items, []);
});

test("authoritative DOI matching abstains when multiple active local paper families collide", async () => {
  const duplicate = paper("c");
  seedPaper(database, duplicate, 3);
  seedIdentity(database, duplicate, "10.1234/target", "https://openalex.org/W100");
  routeState.active.push({ snapshotId: duplicate.snapshotId });

  const response = await GET(new Request(`https://example.test/api/citation-reception?snapshotId=${encodeURIComponent(target.snapshotId)}`));
  assert.equal(response.status, 200);
  const payload = await response.json();
  assert.equal(payload.status, "unavailable");
  assert.match(payload.warnings[0], /多篇已入库论文/u);

  const process = await POST(new Request("https://example.test/api/citation-reception", {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ snapshotId: target.snapshotId }),
  }));
  assert.equal(process.status, 409);
  const plain = await process.json();
  assert.equal(plain.error.code, "CITATION_TARGET_IDENTITY_AMBIGUOUS");

  // The browser's streamed request answers at once and ends with the same result.
  const streamed = await POST(new Request("https://example.test/api/citation-reception", {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/x-ndjson" },
    body: JSON.stringify({ snapshotId: target.snapshotId }),
  }));
  assert.equal(streamed.status, 200);
  assert.match(streamed.headers.get("content-type"), /^application\/x-ndjson/u);
  const lines = (await streamed.text()).trim().split("\n").map((line) => JSON.parse(line));
  assert.deepEqual(lines[0], { type: "started" });
  assert.deepEqual(lines.at(-1), { type: "result", status: 409, retryAfter: null, body: plain });
});
