import assert from "node:assert/strict";
import { register } from "node:module";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";

const bindings = { DB: null, BUCKET: {} };
globalThis.__visualPageRouteTestBindings = bindings;

const storeStub = `
export const PROJECT_ID = "DEEPSHOVEL-PROJECT";
export class ApiError extends Error {
  constructor(status, code, message) {
    super(message); this.name = "ApiError"; this.status = status; this.code = code;
  }
}
export async function getBindings() { return globalThis.__visualPageRouteTestBindings; }
export function safeError(error) {
  if (error instanceof ApiError) return Response.json({ok:false,error:{code:error.code,message:error.message}}, {status:error.status});
  return Response.json({ok:false,error:{code:"INTERNAL_ERROR",message:String(error)}}, {status:500});
}`;

const loader = `
let rootUrl; let storeUrl;
export function initialize(data) { rootUrl=data.rootUrl; storeUrl=data.storeUrl; }
export async function resolve(specifier, context, nextResolve) {
  if (specifier === "@/lib/server/evidence-store") return {url:storeUrl,shortCircuit:true};
  if (specifier.startsWith("@/")) {
    let path=specifier.slice(2);
    if (path === "lib/evidence-graph") path += "/index.ts";
    else if (!/\\.[cm]?[jt]sx?$/.test(path)) path += ".ts";
    return {url:new URL(path,rootUrl).href,shortCircuit:true};
  }
  return nextResolve(specifier,context);
}`;
register(`data:text/javascript,${encodeURIComponent(loader)}`, {
  parentURL: import.meta.url,
  data: { rootUrl: new URL("../", import.meta.url).href, storeUrl: `data:text/javascript,${encodeURIComponent(storeStub)}` },
});

const { GET } = await import("../app/api/evidence-graphs/[snapshotId]/visuals/route.ts");
const snapshotId = `snapshot:sha256:${"a".repeat(64)}`;

class SqliteD1Statement {
  args = [];
  constructor(database, sql) { this.database = database; this.sql = sql; }
  bind(...args) { this.args = args; return this; }
  async all() { return { results: this.database.prepare(this.sql).all(...this.args) }; }
}

class SqliteD1 {
  constructor(database) { this.database = database; }
  prepare(sql) { return new SqliteD1Statement(this.database, sql); }
}

function selector(index, kind) {
  return {
    page: index + 1,
    section: "Results",
    paragraph: null,
    table: kind === "table" ? `Table ${index + 1}` : null,
    row_start: null,
    row_end: null,
    row_range_label: null,
    column_names: [],
    figure: kind === "figure" ? `Figure ${index + 1}` : null,
    series: null,
    char_start: null,
    char_end: null,
    region: { unit: "normalized", x: 0.1, y: 0.1, width: 0.5, height: 0.3 },
    region_label: null,
    dataset_path: null,
  };
}

function graphFixture() {
  const anchors = Array.from({ length: 23 }, (_, index) => {
    const kind = index % 2 === 0 ? "figure" : "table";
    return {
      kind: "source_anchor",
      id: `anchor:visual:${index + 1}`,
      artifact_version_id: "artifact-version:visual:v1",
      anchor_type: kind,
      selector: selector(index, kind),
      quote: `Caption ${index + 1}`,
      quote_sha256: null,
    };
  });
  return {
    artifacts: [{ kind: "artifact", id: "artifact:visual", canonical_name: "Visual fixture", artifact_type: "paper" }],
    artifact_versions: [{
      kind: "artifact_version",
      id: "artifact-version:visual:v1",
      artifact_id: "artifact:visual",
      version: "v1",
      locator: "visual.pdf",
      sha256: "b".repeat(64),
      media_type: "application/pdf",
    }],
    anchors,
    visual_evidence: anchors.slice(0, 6).map((anchor) => ({
      kind: "structured_visual_evidence",
      id: `visual:${anchor.id}`,
      anchor_id: anchor.id,
      visual_kind: anchor.anchor_type,
      axes: [], series: [], cells: [], features: [],
    })),
  };
}

function createDatabase() {
  const database = new DatabaseSync(":memory:");
  database.exec(`CREATE TABLE evidence_graph_snapshots (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, root_artifact_version_id TEXT NOT NULL, graph_json TEXT NOT NULL
  )`);
  database.prepare("INSERT INTO evidence_graph_snapshots VALUES (?, ?, ?, ?)")
    .run(snapshotId, "DEEPSHOVEL-PROJECT", "artifact-version:visual:v1", JSON.stringify(graphFixture()));
  return database;
}

function request(parameters = "") {
  return new Request(`http://localhost/api/evidence-graphs/${snapshotId}/visuals${parameters ? `?${parameters}` : ""}`);
}

function context(id = snapshotId) {
  return { params: Promise.resolve({ snapshotId: id }) };
}

test("visual API defaults to five records and returns only the requested page", async () => {
  const database = createDatabase();
  try {
    bindings.DB = new SqliteD1(database);
    const first = await (await GET(request(), context())).json();
    assert.equal(first.pageInfo.pageSize, 5);
    assert.equal(first.pageInfo.total, 23);
    assert.equal(first.pageInfo.totalPages, 5);
    assert.equal(first.records.length, 5);
    assert.deepEqual(first.counts, { all: 23, figure: 12, table: 11 });
    assert.equal("graph" in first, false);

    const fifth = await (await GET(request("page=5"), context())).json();
    assert.equal(fifth.records.length, 3);
    assert.equal(fifth.pageInfo.hasMore, false);
  } finally {
    database.close();
  }
});

test("visual API applies kind filters before pagination", async () => {
  const database = createDatabase();
  try {
    bindings.DB = new SqliteD1(database);
    const response = await GET(request("kind=table&page=2&limit=5"), context());
    const body = await response.json();
    assert.equal(response.status, 200);
    assert.equal(body.pageInfo.total, 11);
    assert.equal(body.records.length, 5);
    assert.ok(body.records.every((record) => record.kind === "table"));
  } finally {
    database.close();
  }
});

test("visual API rejects malformed page contracts", async () => {
  const database = createDatabase();
  try {
    bindings.DB = new SqliteD1(database);
    for (const parameters of ["limit=0", "limit=51", "page=0", "kind=chart", "unknown=true", "page=1&page=2"]) {
      const response = await GET(request(parameters), context());
      assert.equal(response.status, 400, parameters);
      assert.equal((await response.json()).ok, false, parameters);
    }
  } finally {
    database.close();
  }
});
