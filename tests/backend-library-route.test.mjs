import assert from "node:assert/strict";
import { register } from "node:module";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";
import { readFileSync } from "node:fs";

const bindings = { DB: null, BUCKET: {} };
globalThis.__libraryRouteTestBindings = bindings;

const storeStub = `
export const PROJECT_ID = "DEEPSHOVEL-PROJECT";
export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message); this.name = "ApiError"; this.status = status; this.code = code; this.details = details;
  }
}
export async function getBindings() { return globalThis.__libraryRouteTestBindings; }
export function safeError(error) {
  if (error instanceof ApiError) {
    return Response.json({ok:false,error:{code:error.code,message:error.message}}, {status:error.status});
  }
  return Response.json({ok:false,error:{code:"INTERNAL_ERROR",message:String(error)}}, {status:500});
}`;

const loader = `
let rootUrl; let storeUrl;
export function initialize(data) { rootUrl=data.rootUrl; storeUrl=data.storeUrl; }
export async function resolve(specifier, context, nextResolve) {
  if (specifier === "@/lib/server/evidence-store") return {url:storeUrl,shortCircuit:true};
  if (specifier.startsWith("@/")) {
    let path=specifier.slice(2);
    if (["lib/evidence-graph", "lib/evidence-retrieval"].includes(path)) path += "/index";
    if (!/\\.[cm]?[jt]sx?$/.test(path)) path += ".ts";
    return {url:new URL(path,rootUrl).href,shortCircuit:true};
  }
  return nextResolve(specifier,context);
}`;
register(`data:text/javascript,${encodeURIComponent(loader)}`, {
  parentURL: import.meta.url,
  data: { rootUrl: new URL("../", import.meta.url).href, storeUrl: `data:text/javascript,${encodeURIComponent(storeStub)}` },
});

const { GET } = await import("../app/api/library/route.ts");

class SqliteD1Statement {
  args = [];
  constructor(owner, sql) { this.owner = owner; this.sql = sql; }
  bind(...args) { this.args = args; return this; }
  async all() { return { results: this.owner.database.prepare(this.sql).all(...this.args) }; }
}

class SqliteD1 {
  prepareCount = 0;
  constructor(database) { this.database = database; }
  prepare(sql) { this.prepareCount += 1; return new SqliteD1Statement(this, sql); }
}

class PatternLimitedSqliteD1 extends SqliteD1 {
  prepare(sql) {
    const statement = super.prepare(sql);
    const all = statement.all.bind(statement);
    statement.all = async () => {
      if (/\bLIKE\s+\?/iu.test(sql) && statement.args.slice(2, 4).some((value) => (
        typeof value === "string" && Buffer.byteLength(value, "utf8") > 50
      ))) {
        throw new Error("D1_ERROR: LIKE or GLOB pattern too complex: SQLITE_ERROR");
      }
      return all();
    };
    return statement;
  }
}

const IDS = {
  alpha: "artifact:00000000-0000-4000-8000-000000000001",
  beta: "artifact:00000000-0000-4000-8000-000000000002",
  gamma: "artifact:00000000-0000-4000-8000-000000000003",
};

function createDatabase(splitSql = (sql) => [sql]) {
  const database = new DatabaseSync(":memory:");
  database.exec(`
    CREATE TABLE artifacts (
      id TEXT PRIMARY KEY, project_id TEXT NOT NULL, preferred_name TEXT NOT NULL,
      artifact_type TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE TABLE artifact_versions (
      id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL, version_number INTEGER NOT NULL,
      sha256 TEXT NOT NULL, source_locator TEXT NOT NULL, media_type TEXT NOT NULL, original_file_name TEXT NOT NULL,
      byte_size INTEGER NOT NULL, created_at TEXT NOT NULL
    );
    CREATE TABLE compile_jobs (
      id TEXT PRIMARY KEY, project_id TEXT NOT NULL, artifact_version_id TEXT NOT NULL,
      status TEXT NOT NULL, stage TEXT NOT NULL, progress INTEGER NOT NULL,
      timings_json TEXT NOT NULL, error_json TEXT, snapshot_id TEXT,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE evidence_graph_snapshots (
      id TEXT PRIMARY KEY, project_id TEXT NOT NULL, root_artifact_version_id TEXT NOT NULL,
      counts_json TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE TABLE verification_runs (
      id TEXT PRIMARY KEY, project_id TEXT NOT NULL, snapshot_id TEXT NOT NULL,
      report_json TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE TABLE graph_review_events (
      seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL, project_id TEXT NOT NULL,
      snapshot_id TEXT NOT NULL, decision TEXT NOT NULL, rationale TEXT NOT NULL,
      reviewer TEXT NOT NULL, created_at TEXT NOT NULL
    );
  `);
  database.exec(`
    CREATE INDEX artifact_version_number_idx ON artifact_versions(artifact_id,version_number);
    CREATE INDEX compile_job_artifact_created_idx ON compile_jobs(artifact_version_id,created_at);
    CREATE INDEX evidence_graph_artifact_created_idx ON evidence_graph_snapshots(root_artifact_version_id,created_at);
    CREATE INDEX verification_snapshot_created_idx ON verification_runs(snapshot_id,created_at);
    CREATE INDEX graph_review_snapshot_seq_idx ON graph_review_events(snapshot_id,seq);
    CREATE UNIQUE INDEX graph_review_id_idx ON graph_review_events(id);
  `);
  const project = "DEEPSHOVEL-PROJECT";
  const insertArtifact = database.prepare("INSERT INTO artifacts VALUES (?, ?, ?, ?, ?)");
  insertArtifact.run(IDS.alpha, project, "Alpha provenance paper · 论文", "paper", "2026-01-01T00:00:00.000Z");
  insertArtifact.run(IDS.beta, project, "Beta isotope paper · 论文", "paper", "2026-01-05T00:00:00.000Z");
  insertArtifact.run(IDS.gamma, project, "Gamma tectonics paper · 论文", "paper", "2026-01-05T00:00:00.000Z");

  const insertVersion = database.prepare("INSERT INTO artifact_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)");
  insertVersion.run("version:alpha:1", IDS.alpha, 1, "a".repeat(64), "upload://alpha-v1.pdf", "application/pdf", "alpha-v1.pdf", 100, "2026-01-02T00:00:00.000Z");
  insertVersion.run("version:alpha:2", IDS.alpha, 2, "b".repeat(64), "upload://alpha-latest.pdf", "application/pdf", "alpha-latest.pdf", 200, "2026-01-04T00:00:00.000Z");
  insertVersion.run("version:beta:1", IDS.beta, 1, "c".repeat(64), "upload://beta-latest.pdf", "application/pdf", "beta-latest.pdf", 300, "2026-01-04T12:00:00.000Z");
  insertVersion.run("version:gamma:1", IDS.gamma, 1, "d".repeat(64), "upload://gamma-latest.pdf", "application/pdf", "gamma-latest.pdf", 400, "2026-01-11T00:00:00.000Z");

  const insertJob = database.prepare("INSERT INTO compile_jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)");
  insertJob.run("job:alpha:old-version", project, "version:alpha:1", "completed", "completed", 100, "{}", null, "snapshot:old-alpha", "2026-01-12T00:00:00.000Z", "2026-01-12T00:00:00.000Z");
  insertJob.run("job:alpha:current:old", project, "version:alpha:2", "running", "extracting", 60, '{"parsing":1200}', null, null, "2026-01-06T00:00:00.000Z", "2026-01-06T00:00:00.000Z");
  insertJob.run("job:alpha:current:new", project, "version:alpha:2", "completed", "completed", 100, '{"parsing":1200,"extracting":3400}', null, "snapshot:alpha", "2026-01-07T00:00:00.000Z", "2026-01-07T00:00:00.000Z");

  const insertSnapshot = database.prepare("INSERT INTO evidence_graph_snapshots VALUES (?, ?, ?, ?, ?)");
  insertSnapshot.run("snapshot:old-alpha", project, "version:alpha:1", '{"atomic_claims":99}', "2026-01-13T00:00:00.000Z");
  insertSnapshot.run("snapshot:alpha", project, "version:alpha:2", '{"atomic_claims":7,"observations":8}', "2026-01-08T00:00:00.000Z");
  insertSnapshot.run("snapshot:beta", project, "version:beta:1", '{"atomic_claims":3}', "2026-01-10T00:00:00.000Z");

  const insertVerification = database.prepare("INSERT INTO verification_runs VALUES (?, ?, ?, ?, ?)");
  insertVerification.run("verification:alpha:1", project, "snapshot:alpha", '{"assessments":[{"limitations":["Earlier limitation"]}]}', "2026-01-08T01:00:00.000Z");
  insertVerification.run("verification:alpha:2", project, "snapshot:alpha", '{"assessments":[{"limitations":["OCR geometry","Single study"]},{"limitations":["OCR geometry"]}]}', "2026-01-08T02:00:00.000Z");
  insertVerification.run("verification:old-alpha", project, "snapshot:old-alpha", '{"assessments":[]}', "2026-01-14T00:00:00.000Z");

  const insertReview = database.prepare("INSERT INTO graph_review_events (id, project_id, snapshot_id, decision, rationale, reviewer, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)");
  insertReview.run("review:alpha:1", project, "snapshot:alpha", "needs_revision", "Check table", "first@example.com", "2026-01-08T03:00:00.000Z");
  insertReview.run("review:alpha:2", project, "snapshot:alpha", "accepted", "Checked source", "second@example.com", "2026-01-09T00:00:00.000Z");
  for (const statement of splitSql(readFileSync(new URL("../drizzle/0018_library_read_models.sql", import.meta.url), "utf8"))) database.exec(statement);
  return database;
}

function request(parameters = "") {
  return new Request(`http://localhost/api/library${parameters ? `?${parameters}` : ""}`);
}

test("Compile summary refresh looks up a missing snapshot through a covering version index", () => {
  const database = createDatabase();
  try {
    // Include the competing production index that previously made SQLite walk
    // project history for a newly submitted version with no snapshot.
    database.exec("CREATE INDEX evidence_graph_project_created_idx ON evidence_graph_snapshots(project_id,created_at)");
    database.exec(readFileSync(new URL("../drizzle/0024_snapshot_version_lookup.sql", import.meta.url), "utf8"));
    const plan = database.prepare("EXPLAIN QUERY PLAN SELECT * FROM library_artifact_projection WHERE artifact_id=?")
      .all(IDS.gamma).map((row) => row.detail).join("\n");
    assert.match(plan, /COVERING INDEX evidence_graph_project_version_latest_idx \(project_id=\? AND root_artifact_version_id=\?\)/);
    assert.doesNotMatch(plan, /evidence_graph_project_created_idx|SCAN evidence_graph_snapshots/);

    // Execute the actual INSERT/UPDATE triggers, not just an isolated query.
    database.prepare("INSERT INTO compile_jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
      .run("job:gamma", "DEEPSHOVEL-PROJECT", "version:gamma:1", "queued", "queued", 0, "{}", null, null,
        "2026-01-12T00:00:00.000Z", "2026-01-12T00:00:00.000Z");
    database.exec("UPDATE compile_jobs SET status='running',stage='parsing',progress=10 WHERE id='job:gamma'");
    const summary = database.prepare("SELECT latest_compile_job_id,latest_snapshot_id,library_status FROM library_artifact_summaries WHERE artifact_id=?").get(IDS.gamma);
    assert.equal(summary.latest_compile_job_id, "job:gamma");
    assert.equal(summary.latest_snapshot_id, null);
    assert.equal(summary.library_status, "processing");

    // Cover ordering ties and scope isolation without reading graph JSON.
    database.exec(`INSERT INTO evidence_graph_snapshots VALUES
      ('snapshot:gamma:a','DEEPSHOVEL-PROJECT','version:gamma:1','{}','2026-01-13'),
      ('snapshot:gamma:z','DEEPSHOVEL-PROJECT','version:gamma:1','{}','2026-01-13'),
      ('snapshot:gamma:other','OTHER-PROJECT','version:gamma:1','{}','2026-01-14')`);
    database.exec("UPDATE compile_jobs SET status='completed',stage='completed',progress=100 WHERE id='job:gamma'");
    const completed = database.prepare("SELECT latest_snapshot_id,library_status FROM library_artifact_summaries WHERE artifact_id=?").get(IDS.gamma);
    assert.equal(completed.latest_snapshot_id, "snapshot:gamma:z");
    assert.equal(completed.library_status, "completed");
  } finally {
    database.close();
  }
});

test("library API returns one current-version summary per Artifact in one SQL query", async () => {
  const database = createDatabase();
  try {
    const db = new SqliteD1(database);
    bindings.DB = db;
    const response = await GET(request("status=all&limit=100"));
    const body = await response.json();
    assert.equal(response.status, 200);
    assert.equal(body.ok, true);
    assert.equal(body.artifacts.length, 3);
    assert.equal(db.prepareCount, 1);

    const alpha = body.artifacts.find((artifact) => artifact.artifactId === IDS.alpha);
    assert.equal(alpha.title, "Alpha provenance paper · 论文");
    assert.equal(alpha.versionCount, 2);
    assert.equal(alpha.latestVersion.id, "version:alpha:2");
    assert.equal(alpha.latestVersion.artifactId, IDS.alpha);
    assert.equal(alpha.latestVersion.locator, "upload://alpha-latest.pdf");
    assert.equal(alpha.compile.id, "job:alpha:current:new");
    assert.equal(alpha.compile.artifactVersionId, "version:alpha:2");
    assert.deepEqual(alpha.compile.timings, { parsing: 1200, extracting: 3400 });
    assert.equal(alpha.snapshot.id, "snapshot:alpha");
    assert.deepEqual(alpha.snapshot.counts, { atomic_claims: 7, observations: 8 });
    assert.deepEqual(alpha.machineVerification, {
      status: "assessed", runCount: 2, limitationCount: 2,
    });
    assert.equal(alpha.humanReview.id, "review:alpha:2");
    assert.equal(alpha.humanReview.decision, "accepted");

    const beta = body.artifacts.find((artifact) => artifact.artifactId === IDS.beta);
    assert.equal(beta.machineVerification.status, "unassessed");
    assert.equal(beta.machineVerification.runCount, 0);
    assert.equal(beta.machineVerification.limitationCount, 0);
    assert.equal(beta.humanReview, null);

    const gamma = body.artifacts.find((artifact) => artifact.artifactId === IDS.gamma);
    assert.equal(gamma.snapshot, null);
    assert.deepEqual(gamma.machineVerification, {
      status: "unassessed", runCount: 0, limitationCount: 0,
    });
  } finally {
    database.close();
  }
});

test("library API uses a stable keyset cursor without overlap", async () => {
  const database = createDatabase();
  try {
    bindings.DB = new SqliteD1(database);
    const firstResponse = await GET(request("query=%E8%AE%BA%E6%96%87&status=all&limit=2"));
    const first = await firstResponse.json();
    assert.deepEqual(first.artifacts.map((artifact) => artifact.artifactId), [IDS.gamma, IDS.beta]);
    assert.equal(first.pageInfo.hasMore, true);
    assert.ok(first.pageInfo.nextCursor);

    const secondResponse = await GET(request(`query=%E8%AE%BA%E6%96%87&status=all&limit=2&cursor=${encodeURIComponent(first.pageInfo.nextCursor)}`));
    const second = await secondResponse.json();
    assert.deepEqual(second.artifacts.map((artifact) => artifact.artifactId), [IDS.alpha]);
    assert.equal(second.pageInfo.hasMore, false);
    assert.equal(second.pageInfo.nextCursor, null);
    assert.equal(new Set([...first.artifacts, ...second.artifacts].map((artifact) => artifact.artifactId)).size, 3);
  } finally {
    database.close();
  }
});

test("library API filters metadata and rejects malformed pagination contracts", async () => {
  const database = createDatabase();
  try {
    bindings.DB = new SqliteD1(database);
    const filteredResponse = await GET(request("query=gamma&status=all"));
    const filtered = await filteredResponse.json();
    assert.deepEqual(filtered.artifacts.map((artifact) => artifact.artifactId), [IDS.gamma]);
    const byFileResponse = await GET(request("query=alpha-latest.pdf&status=all"));
    assert.deepEqual((await byFileResponse.json()).artifacts.map((artifact) => artifact.artifactId), [IDS.alpha]);
    const oldVersionFile = await GET(request("query=alpha-v1.pdf&status=all"));
    assert.deepEqual((await oldVersionFile.json()).artifacts, []);

    const cursorPage = await (await GET(request("query=%E8%AE%BA%E6%96%87&status=all&limit=1"))).json();
    const mismatched = await GET(request(`query=gamma&status=all&cursor=${encodeURIComponent(cursorPage.pageInfo.nextCursor)}`));
    assert.equal(mismatched.status, 400);
    assert.equal((await mismatched.json()).error.code, "INVALID_LIBRARY_CURSOR");

    for (const parameters of ["limit=0", "limit=101", "cursor=garbage", "status=unknown", "unknown=true", "limit=2&limit=3"]) {
      const response = await GET(request(parameters));
      assert.equal(response.status, 400, parameters);
      assert.equal((await response.json()).ok, false, parameters);
    }
  } finally {
    database.close();
  }
});

test("library API preserves literal long, wildcard and Unicode queries under the real D1 LIKE limit", async () => {
  const database = createDatabase();
  try {
    const longPrefix = "z".repeat(64);
    const literal = `${longPrefix}_100%\\literal`;
    database.prepare("UPDATE artifacts SET preferred_name = ? WHERE id = ?")
      .run(`${literal} · 论文`, IDS.gamma);
    database.prepare("UPDATE artifacts SET preferred_name = ? WHERE id = ?")
      .run(`${longPrefix} · 论文`, IDS.alpha);
    database.prepare("UPDATE artifact_versions SET original_file_name = ? WHERE id = ?")
      .run(`source_${literal}.pdf`, "version:gamma:1");
    bindings.DB = new PatternLimitedSqliteD1(database);

    for (const { query, expected } of [
      { query: longPrefix, expected: [IDS.gamma, IDS.alpha] },
      { query: `SOURCE_${longPrefix}`, expected: [IDS.gamma] },
      { query: "__deepshovel_browser_probe_no_match_9c32__", expected: [] },
      { query: "_", expected: [IDS.gamma] },
      { query: "%", expected: [IDS.gamma] },
      { query: "\\", expected: [IDS.gamma] },
      { query: "_100%\\literal", expected: [IDS.gamma] },
      { query: "论文", expected: [IDS.gamma, IDS.beta, IDS.alpha] },
    ]) {
      const response = await GET(request(new URLSearchParams({ query, status: "all" }).toString()));
      const body = await response.json();
      assert.equal(response.status, 200, query);
      assert.deepEqual(body.artifacts.map((artifact) => artifact.artifactId), expected, query);
    }

    const first = await (await GET(request(new URLSearchParams({
      query: longPrefix,
      status: "all",
      limit: "1",
    }).toString()))).json();
    assert.deepEqual(first.artifacts.map((artifact) => artifact.artifactId), [IDS.gamma]);
    assert.equal(first.pageInfo.hasMore, true);
    const second = await (await GET(request(new URLSearchParams({
      query: longPrefix,
      status: "all",
      limit: "1",
      cursor: first.pageInfo.nextCursor,
    }).toString()))).json();
    assert.deepEqual(second.artifacts.map((artifact) => artifact.artifactId), [IDS.alpha]);
    assert.equal(second.pageInfo.hasMore, false);
  } finally {
    database.close();
  }
});

test("library API preserves a withdrawn latest review event for audit-aware UI", async () => {
  const database = createDatabase();
  try {
    database.prepare("INSERT INTO graph_review_events (id, project_id, snapshot_id, decision, rationale, reviewer, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)")
      .run("review:alpha:withdrawn", "DEEPSHOVEL-PROJECT", "snapshot:alpha", "withdrawn", "Undo confirmation", "second@example.com", "2026-01-10T01:00:00.000Z");
    bindings.DB = new SqliteD1(database);
    const response = await GET(request("query=Alpha&status=all"));
    const body = await response.json();
    assert.equal(body.artifacts[0].humanReview.decision, "withdrawn");
    assert.equal(body.artifacts[0].humanReview.id, "review:alpha:withdrawn");
  } finally {
    database.close();
  }
});

test("library API defaults to pending and exposes durable processing/completed/failed views", async () => {
  const database = createDatabase();
  try {
    const project = "DEEPSHOVEL-PROJECT";
    database.prepare("INSERT INTO artifacts VALUES (?, ?, ?, ?, ?)")
      .run("artifact:00000000-0000-4000-8000-000000000004", project, "Delta failed paper", "paper", "2026-01-06T00:00:00.000Z");
    database.prepare("INSERT INTO artifact_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)")
      .run("version:delta:1", "artifact:00000000-0000-4000-8000-000000000004", 1, "e".repeat(64), "upload://delta.pdf", "application/pdf", "delta.pdf", 500, "2026-01-06T00:00:00.000Z");
    database.prepare("INSERT INTO compile_jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
      .run("job:delta:1", project, "version:delta:1", "failed", "failed", 100, "{}", '{"code":"MODEL_OUTPUT_INVALID"}', null, "2026-01-07T00:00:00.000Z", "2026-01-07T00:00:00.000Z");
    database.prepare("INSERT INTO compile_jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
      .run("job:gamma:1", project, "version:gamma:1", "running", "extracting", 60, "{}", null, null, "2026-01-12T00:00:00.000Z", "2026-01-12T00:00:00.000Z");
    bindings.DB = new SqliteD1(database);

    assert.deepEqual((await (await GET(request())).json()).artifacts, []);
    const processing = await (await GET(request("status=processing"))).json();
    assert.deepEqual(processing.artifacts.map((artifact) => artifact.artifactId), [IDS.gamma]);
    assert.equal(processing.artifacts[0].libraryStatus, "processing");
    const completed = await (await GET(request("status=completed"))).json();
    assert.deepEqual(completed.artifacts.map((artifact) => artifact.artifactId), [IDS.beta, IDS.alpha]);
    assert.ok(completed.artifacts.every((artifact) => artifact.libraryStatus === "completed"));
    const failed = await (await GET(request("status=failed"))).json();
    assert.deepEqual(failed.artifacts.map((artifact) => artifact.artifactId), ["artifact:00000000-0000-4000-8000-000000000004"]);

    const cursorPage = await (await GET(request("status=completed&limit=1"))).json();
    const mismatch = await GET(request(`status=all&cursor=${encodeURIComponent(cursorPage.pageInfo.nextCursor)}`));
    assert.equal(mismatch.status, 400);
    assert.equal((await mismatch.json()).error.code, "INVALID_LIBRARY_CURSOR");
  } finally {
    database.close();
  }
});

test("library summaries retain exact counts and state transitions without reading report JSON", async () => {
  const database = createDatabase();
  try {
    const db = new SqliteD1(database);
    const prepare = db.prepare.bind(db);
    db.prepare = (sql) => {
      assert.doesNotMatch(sql, /graph_json|report_json|json_each|\bOFFSET\b|COUNT\(\*\) OVER/iu);
      return prepare(sql);
    };
    bindings.DB = db;
    assert.equal((await (await GET(request("status=all&limit=1"))).json()).pageInfo.total, 3);
    assert.equal((await (await GET(request("status=completed&limit=1"))).json()).pageInfo.total, 2);
    database.prepare("UPDATE compile_jobs SET status='running',updated_at='2026-02-01' WHERE id='job:alpha:current:new'").run();
    assert.equal((await (await GET(request("status=processing"))).json()).pageInfo.total, 1);
    database.prepare("UPDATE compile_jobs SET status='failed',updated_at='2026-02-02' WHERE id='job:alpha:current:new'").run();
    const completed = await (await GET(request("status=completed"))).json();
    assert.equal(completed.pageInfo.total, 2, "failed recompile retains the valid snapshot");
    assert.equal((await (await GET(request("status=failed"))).json()).pageInfo.total, 0);
    const search = await (await GET(request("status=all&query=alpha"))).json();
    assert.equal(search.pageInfo.total, null);
    assert.equal(search.pageInfo.totalPages, null);
    const exact = await (await GET(request("status=all&artifact_version_id=version:alpha:1"))).json();
    assert.deepEqual(exact.artifacts.map((row) => row.artifactId), [IDS.alpha]);
    assert.equal(exact.artifacts[0].latestVersion.id, "version:alpha:2");
    assert.equal((await GET(request("status=all&page=25000"))).status, 400);
  } finally { database.close(); }
});

test("library name index chooses rare grams and keeps short Unicode literal searches indexed", async () => {
  const database = createDatabase();
  try {
    const insert = database.prepare("INSERT INTO artifacts VALUES (?, 'DEEPSHOVEL-PROJECT', ?, 'paper', '2025-01-01')");
    database.exec("BEGIN");
    for (let index = 0; index < 1500; index += 1) insert.run(`artifact:common:${index}`, `the common scientific paper ${index}`);
    insert.run("artifact:rare", "the common scientific paper 稀有𠮷😀suffix");
    database.exec("COMMIT");
    const plans = [];
    const db = new SqliteD1(database);
    const prepare = db.prepare.bind(db);
    db.prepare = (sql) => {
      const statement = prepare(sql);
      const bind = statement.bind.bind(statement);
      statement.bind = (...args) => {
        plans.push(database.prepare(`EXPLAIN QUERY PLAN ${sql}`).all(...args).map((row) => row.detail).join("\n"));
        return bind(...args);
      };
      return statement;
    };
    bindings.DB = db;
    for (const query of ["the common scientific paper 稀有𠮷😀suffix", "稀", "稀有", "𠮷😀", "😀"]) {
      const result = await (await GET(request(new URLSearchParams({status:"all",query}).toString()))).json();
      assert.deepEqual(result.artifacts.map((row) => row.artifactId), ["artifact:rare"], query);
    }
    assert.ok(plans.every((plan) => /SCAN library_name_search VIRTUAL TABLE INDEX/.test(plan)), plans.join("\n"));
    assert.ok(plans.every((plan) => !/SCAN (?:verification_runs|graph_review_events|g)\b/.test(plan)), plans.join("\n"));

  } finally { database.close(); }
});

test("library substring verification is capped and cursor resumes after scanned false positives", async () => {
  const database = createDatabase();
  try {
    const insert = database.prepare("INSERT INTO artifacts VALUES (?, 'DEEPSHOVEL-PROJECT', ?, 'paper', '2025-01-01')");
    insert.run("artifact:exact-old", "abc xyz");
    database.exec("BEGIN");
    for (let index = 0; index < 1030; index += 1) insert.run(`artifact:false:${index}`, "abc|bc |c x| xy|xyz");
    database.exec("COMMIT");
    bindings.DB = new SqliteD1(database);
    const first = await (await GET(request("status=all&query=abc%20xyz&limit=20"))).json();
    assert.deepEqual(first.artifacts, []);
    assert.equal(first.pageInfo.hasMore, true);
    assert.ok(first.pageInfo.nextCursor);
    const second = await (await GET(request(`status=all&query=abc%20xyz&limit=20&cursor=${first.pageInfo.nextCursor}`))).json();
    assert.deepEqual(second.artifacts.map((row) => row.artifactId), ["artifact:exact-old"]);
    assert.equal(second.pageInfo.hasMore, false);
    const before = database.prepare("SELECT search_id FROM library_artifact_summaries WHERE artifact_id=?").get("artifact:exact-old");
    database.prepare("UPDATE artifacts SET preferred_name=? WHERE id=?").run("renamed abc xyz", "artifact:exact-old");
    database.exec("VACUUM");
    assert.deepEqual(database.prepare("SELECT search_id FROM library_artifact_summaries WHERE artifact_id=?").get("artifact:exact-old"), before);
  } finally { database.close(); }
});

test("historical snapshot pages stay bounded and preserve old filenames and review state", async () => {
  const database = createDatabase();
  try {
    database.exec(`ALTER TABLE evidence_graph_snapshots ADD COLUMN schema_version TEXT DEFAULT '1.0.0';
      ALTER TABLE evidence_graph_snapshots ADD COLUMN graph_sha256 TEXT DEFAULT 'hash';
      ALTER TABLE evidence_graph_snapshots ADD COLUMN extractor_run_id TEXT DEFAULT 'run';
      ALTER TABLE evidence_graph_snapshots ADD COLUMN extractor_model TEXT DEFAULT 'fixture';
      ALTER TABLE evidence_graph_snapshots ADD COLUMN extractor_prompt_version TEXT DEFAULT '1.0.0';`);
    database.exec(readFileSync(new URL("../drizzle/0021_snapshot_list_read_models.sql", import.meta.url), "utf8"));
    const db = new SqliteD1(database);
    const prepare = db.prepare.bind(db);
    db.prepare = (sql) => {
      assert.doesNotMatch(sql, /graph_json|report_json|json_each|\bOFFSET\b|COUNT\(\*\) OVER/iu);
      return prepare(sql);
    };
    bindings.DB = db;
    const { listEvidenceGraphSnapshotPage } = await import("../app/api/evidence-graphs/import-store.ts");
    const first = await listEvidenceGraphSnapshotPage({pageSize:1});
    assert.equal(first.snapshots.length, 1);
    assert.equal(first.snapshots[0].id, "snapshot:old-alpha");
    assert.equal(first.pageInfo.total, 3);
    const cursor = JSON.parse(Buffer.from(first.pageInfo.nextCursor, "base64url").toString());
    const second = await listEvidenceGraphSnapshotPage({pageSize:1,cursor});
    assert.equal(second.snapshots[0].id, "snapshot:beta");
    const historical = await listEvidenceGraphSnapshotPage({query:"alpha-v1.pdf"});
    assert.equal(historical.snapshots[0].id, "snapshot:old-alpha");
    assert.equal(historical.pageInfo.total, null);
    assert.equal((await listEvidenceGraphSnapshotPage({status:"confirmed"})).pageInfo.total, 1);
    database.prepare("INSERT INTO graph_review_events(id,project_id,snapshot_id,decision,rationale,reviewer,created_at) VALUES(?,?,?,?,?,?,?)")
      .run("review:withdraw", "DEEPSHOVEL-PROJECT", "snapshot:alpha", "withdrawn", "", "fixture", "2026-04-01");
    assert.equal((await listEvidenceGraphSnapshotPage({status:"confirmed"})).pageInfo.total, 0);
    assert.equal((await listEvidenceGraphSnapshotPage({status:"unreviewed"})).pageInfo.total, 3);
  } finally { database.close(); }
});


test("library migration preserves complete triggers through Wrangler SQL splitting", async () => {
  const { unstable_splitSqlQuery } = await import("wrangler");
  const database = createDatabase(unstable_splitSqlQuery);
  try {
    database.prepare("UPDATE artifacts SET preferred_name=? WHERE id=?").run("new 论文𠮷😀 title", IDS.gamma);
    bindings.DB = new SqliteD1(database);
    const body = await (await GET(request("status=all&query=" + encodeURIComponent("论文𠮷😀")))).json();
    assert.deepEqual(body.artifacts.map((row) => row.artifactId), [IDS.gamma]);
  } finally { database.close(); }
});

test("all-status library search keeps a failed new version recoverable alongside its untouched older snapshots", async () => {
  const database = createDatabase();
  try {
    bindings.DB = new SqliteD1(database);
    const project = "DEEPSHOVEL-PROJECT";
    database.prepare("INSERT INTO artifact_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)")
      .run("version:alpha:3", IDS.alpha, 3, "f".repeat(64), "upload://alpha-revision.pdf", "application/pdf", "alpha-revision.pdf", 250, "2026-02-01T00:00:00.000Z");
    database.prepare("INSERT INTO compile_jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
      .run("job:alpha:cancelled", project, "version:alpha:3", "failed", "failed", 68, "{}", '{"code":"CANCELLED"}', null, "2026-02-01T01:00:00.000Z", "2026-02-01T01:01:00.000Z");
    const hiddenByCompleted = await (await GET(request("status=completed&query=Alpha"))).json();
    assert.equal(hiddenByCompleted.artifacts.length, 0);
    const recovered = await (await GET(request("status=all&query=Alpha"))).json();
    assert.equal(recovered.artifacts.length, 1);
    assert.equal(recovered.artifacts[0].libraryStatus, "failed");
    assert.equal(recovered.artifacts[0].latestVersion.id, "version:alpha:3");
    assert.ok(recovered.artifacts[0].latestVersion.downloadUrl);
    assert.equal(recovered.artifacts[0].versionCount, 3);
    assert.equal(recovered.artifacts[0].snapshot, null, "older evidence must not be mislabeled as the new version");
    assert.equal(database.prepare("SELECT count(*) AS n FROM evidence_graph_snapshots WHERE root_artifact_version_id IN ('version:alpha:1','version:alpha:2')").get().n, 2);
  } finally {
    database.close();
  }
});
