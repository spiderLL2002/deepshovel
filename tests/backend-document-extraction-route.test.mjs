import assert from "node:assert/strict";
import { register } from "node:module";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";

const bindings = { DB: null };
const state = { extraction: null, refreshable: null };
globalThis.__documentExtractionRouteBindings = bindings;
globalThis.__documentExtractionRouteState = state;

const storeStub = `
export const PROJECT_ID="DEEPSHOVEL-PROJECT";
export class ApiError extends Error { constructor(status,code,message){super(message);this.status=status;this.code=code;} }
export async function getBindings(){return globalThis.__documentExtractionRouteBindings;}
export async function getCompilerConfiguration(){return {baseUrl:"http://127.0.0.1:8011"};}
export function safeError(error){return error instanceof ApiError
  ? Response.json({ok:false,error:{code:error.code,message:error.message}},{status:error.status})
  : Response.json({ok:false,error:{code:"INTERNAL_ERROR",message:String(error)}},{status:500});}
`;
const extractionStoreStub = `
export async function loadDocumentExtraction(){return globalThis.__documentExtractionRouteState.extraction;}
export async function loadRefreshableDocumentExtraction(){return globalThis.__documentExtractionRouteState.refreshable;}
export async function persistDocumentExtraction(extraction){return {extraction};}
`;
const bibliographicStub = `export async function enrichBibliographicMetadata(extraction){return {extraction,status:"not-applicable"};}`;
const compilerStub = `export class CompilerClient { constructor(){} async documentExtractionByVersion(){throw new Error("unexpected Compiler call");} }`;
const loader = `
let rootUrl,storeUrl,extractionStoreUrl,bibliographicUrl,compilerUrl;
export function initialize(data){({rootUrl,storeUrl,extractionStoreUrl,bibliographicUrl,compilerUrl}=data);}
export async function resolve(specifier,context,nextResolve){
  if(specifier==="@/lib/server/evidence-store") return {url:storeUrl,shortCircuit:true};
  if(specifier==="@/lib/server/document-extraction-store.ts") return {url:extractionStoreUrl,shortCircuit:true};
  if(specifier==="@/lib/server/bibliographic-metadata.ts") return {url:bibliographicUrl,shortCircuit:true};
  if(specifier==="@/lib/server/compiler-client.ts") return {url:compilerUrl,shortCircuit:true};
  if(specifier.startsWith("@/")){
    let path=specifier.slice(2);
    if(path==="lib/evidence-graph") path+="/index.ts";
    else if(!/\\.[cm]?[jt]sx?$/.test(path)) path+=".ts";
    return {url:new URL(path,rootUrl).href,shortCircuit:true};
  }
  return nextResolve(specifier,context);
}`;
register(`data:text/javascript,${encodeURIComponent(loader)}`, {
  parentURL: import.meta.url,
  data: {
    rootUrl: new URL("../", import.meta.url).href,
    storeUrl: `data:text/javascript,${encodeURIComponent(storeStub)}`,
    extractionStoreUrl: `data:text/javascript,${encodeURIComponent(extractionStoreStub)}`,
    bibliographicUrl: `data:text/javascript,${encodeURIComponent(bibliographicStub)}`,
    compilerUrl: `data:text/javascript,${encodeURIComponent(compilerStub)}`,
  },
});

const { GET } = await import("../app/api/evidence-graphs/[snapshotId]/document/route.ts");
const sha256 = "a".repeat(64);
const snapshotId = `snapshot:sha256:${"b".repeat(64)}`;
const versionId = `artifact-version:sha256:${sha256}`;

class Statement {
  args = [];
  constructor(database, sql) { this.database = database; this.sql = sql; }
  bind(...args) { this.args = args; return this; }
  async first() { return this.database.prepare(this.sql).get(...this.args) ?? null; }
}

class D1 {
  constructor(database) { this.database = database; }
  prepare(sql) { return new Statement(this.database, sql); }
}

function extraction() {
  const fields = ["title", "abstract", "year", "author", "journal", "issn", "volume", "issue", "page", "article_number", "doi", "link", "publisher", "language"];
  return {
    schema_version: "document-extraction/1.0",
    artifact_version_id: versionId,
    artifact_sha256: sha256,
    parser: { name: "mineru", version: "2.1", backend: "pipeline", method: "auto" },
    metadata: Object.fromEntries(fields.map((field) => [field, field === "title" ? "Fixture paper" : ""])),
    field_sources: Object.fromEntries(fields.map((field) => [field, field === "title" ? "rule" : "empty"])),
    stats: { page_count: 1, text_block_count: 1, figure_count: 0, table_count: 0, image_count: 0, visual_count: 0 },
    markdown: "# Fixture paper\n\nBody",
    visuals: [],
    warnings: [],
  };
}

function database() {
  const db = new DatabaseSync(":memory:");
  db.exec(`CREATE TABLE artifact_versions (id TEXT PRIMARY KEY, sha256 TEXT NOT NULL);
    CREATE TABLE evidence_graph_snapshots (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, root_artifact_version_id TEXT NOT NULL, graph_json TEXT NOT NULL);
    CREATE TABLE compile_jobs (project_id TEXT NOT NULL, artifact_version_id TEXT NOT NULL, upstream_job_id TEXT NOT NULL, status TEXT NOT NULL, updated_at TEXT NOT NULL);`);
  const graph = {
    artifacts: [{ canonical_name: "fixture.pdf" }],
    artifact_versions: [{ id: versionId, sha256, media_type: "application/pdf", locator: "upload://fixture.pdf" }],
  };
  db.prepare("INSERT INTO artifact_versions VALUES (?, ?)").run(versionId, sha256);
  db.prepare("INSERT INTO evidence_graph_snapshots VALUES (?, ?, ?, ?)")
    .run(snapshotId, "DEEPSHOVEL-PROJECT", versionId, JSON.stringify(graph));
  return db;
}

test("document endpoint returns the frozen extraction for the selected immutable snapshot", async () => {
  const db = database();
  try {
    bindings.DB = new D1(db);
    state.extraction = extraction();
    const response = await GET(new Request(`http://localhost/api/evidence-graphs/${snapshotId}/document`), {
      params: Promise.resolve({ snapshotId }),
    });
    const payload = await response.json();
    assert.equal(response.status, 200);
    assert.equal(payload.snapshotId, snapshotId);
    assert.equal(payload.rootArtifactVersionId, versionId);
    assert.equal(payload.sourceTitle, "fixture.pdf");
    assert.equal(payload.extraction.markdown, "# Fixture paper\n\nBody");
    assert.equal(response.headers.get("cache-control"), "private, no-store");
  } finally {
    state.extraction = null;
    db.close();
  }
});

test("document endpoint refreshes a valid older projection when its Compiler job has expired", async () => {
  const db = database();
  try {
    bindings.DB = new D1(db);
    state.extraction = null;
    state.refreshable = extraction();
    const response = await GET(new Request(`http://localhost/api/evidence-graphs/${snapshotId}/document`), {
      params: Promise.resolve({ snapshotId }),
    });
    const payload = await response.json();
    assert.equal(response.status, 200);
    assert.equal(payload.extraction.metadata.title, "Fixture paper");
  } finally {
    state.extraction = null;
    state.refreshable = null;
    db.close();
  }
});

test("document endpoint returns lightweight sections and paginates extraction visuals", async () => {
  const db = database();
  try {
    bindings.DB = new D1(db);
    const fixture = extraction();
    fixture.visuals = Array.from({ length: 7 }, (_, index) => ({
      block_id: `visual-${index + 1}`,
      kind: index % 2 === 0 ? "figure" : "table",
      source_kind: index % 2 === 0 ? "figure" : "table",
      page: index + 1,
      region: null,
      label: `Visual ${index + 1}`,
      caption: "",
      footnote: "",
      visual: null,
    }));
    fixture.stats = { ...fixture.stats, figure_count: 4, table_count: 3, image_count: 4, visual_count: 7 };
    state.extraction = fixture;

    const metadataResponse = await GET(new Request(`http://localhost/api/evidence-graphs/${snapshotId}/document?section=metadata`), {
      params: Promise.resolve({ snapshotId }),
    });
    const metadata = await metadataResponse.json();
    assert.equal(metadata.section, "metadata");
    assert.equal(metadata.extraction.metadata.title, "Fixture paper");
    assert.equal("markdown" in metadata.extraction, false);
    assert.equal("visuals" in metadata.extraction, false);

    const textResponse = await GET(new Request(`http://localhost/api/evidence-graphs/${snapshotId}/document?section=text`), {
      params: Promise.resolve({ snapshotId }),
    });
    const text = await textResponse.json();
    assert.equal(text.section, "text");
    assert.equal(text.markdown, "# Fixture paper\n\nBody");
    assert.equal("extraction" in text, false);

    const visualResponse = await GET(new Request(`http://localhost/api/evidence-graphs/${snapshotId}/document?section=visuals&kind=figure&page=2&limit=2`), {
      params: Promise.resolve({ snapshotId }),
    });
    const visuals = await visualResponse.json();
    assert.deepEqual(visuals.counts, { all: 7, figure: 4, table: 3 });
    assert.equal(visuals.pageInfo.total, 4);
    assert.equal(visuals.pageInfo.totalPages, 2);
    assert.equal(visuals.records.length, 2);
    assert.ok(visuals.records.every((record) => record.kind === "figure"));
  } finally {
    state.extraction = null;
    db.close();
  }
});

test("document endpoint rejects malformed section requests before storage access", async () => {
  for (const parameters of ["section=metadata&page=1", "section=visuals&limit=0", "section=unknown", "unknown=true"]) {
    const response = await GET(new Request(`http://localhost/api/evidence-graphs/${snapshotId}/document?${parameters}`), {
      params: Promise.resolve({ snapshotId }),
    });
    assert.equal(response.status, 400, parameters);
  }
});

test("document endpoint rejects a non-content-addressed snapshot id", async () => {
  const response = await GET(new Request("http://localhost/api/evidence-graphs/nope/document"), {
    params: Promise.resolve({ snapshotId: "nope" }),
  });
  assert.equal(response.status, 400);
  assert.equal((await response.json()).error.code, "INVALID_SNAPSHOT_ID");
});
