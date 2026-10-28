#!/usr/bin/env node
// Offline scale check: creates isolated synthetic databases by default; existing-db permits only readonly /tmp copies.
// node --experimental-strip-types --no-warnings scripts/benchmark-pagination.mjs [--count=500000] [--report=/tmp/pagination-scale.json]
// --existing-db=/tmp/migrated-copy.sqlite opens that copy readonly, without seeding.
// Synthetic phases run sequentially; --keep=true retains only the final graph DB.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { register } from "node:module";
import { DatabaseSync } from "node:sqlite";

const args = Object.fromEntries(process.argv.slice(2).map((arg) => {
  const match = /^--(count|report|existing-db|keep|phase)=(.+)$/.exec(arg);
  if (!match) throw new Error(`Unsupported argument: ${arg}`);
  return [match[1], match[2]];
}));
const count = Number(args.count ?? 500000);
assert.ok(Number.isSafeInteger(count) && count >= 100 && count <= 500000);
const reportPath = args.report ?? `/tmp/deepshovel-pagination-${count}.json`;
assert.ok(path.resolve(reportPath).startsWith("/tmp/"), "reports must be isolated under /tmp");
if (!args["existing-db"] && !args.phase) {
  const reports = [];
  for (const phase of ["library", "graph"]) {
    const childReport = reportPath.replace(/\.json$/, "") + `-${phase}.json`;
    await new Promise((resolve, reject) => {
      const child = spawn(process.execPath, ["--experimental-strip-types", "--no-warnings", process.argv[1], `--count=${count}`, `--phase=${phase}`, `--report=${childReport}`, `--keep=${phase === "graph" && args.keep === "true"}`], { stdio: "inherit" });
      child.on("error", reject);
      child.on("exit", (code) => code === 0 ? resolve() : reject(new Error(`${phase} benchmark exited ${code}`)));
    });
    reports.push(JSON.parse(fs.readFileSync(childReport, "utf8")));
  }
  fs.writeFileSync(reportPath, JSON.stringify({ kind: "sequential-isolated-metadata-benchmarks", count, reports }, null, 2));
  console.log(JSON.stringify({ reportPath }));
  process.exit(0);
}
const phase = args.phase;
if (phase) assert.ok(["library", "graph"].includes(phase), "phase must be library or graph");
const root = new URL("../", import.meta.url);
const existingPath = args["existing-db"];
if (existingPath) assert.ok(path.resolve(existingPath).startsWith("/tmp/"), "existing benchmark copies must be under /tmp");
const temporary = fs.mkdtempSync(path.join("/tmp", "deepshovel-pagination-"));
const databasePath = existingPath ?? path.join(temporary, "metadata.sqlite");
const PROJECT_ID = "DEEPSHOVEL-PROJECT";
const globalKey = "__paginationBenchmarkBindings";
globalThis[globalKey] = { DB: null };
const stub = `
export const PROJECT_ID=${JSON.stringify(PROJECT_ID)};
export class ApiError extends Error {constructor(status,code,message,details){super(message);this.status=status;this.code=code;this.details=details;}}
export const getActor=()=>"benchmark";
export const getBindings=async()=>globalThis.${globalKey};
export const safeError=e=>Response.json({ok:false,error:{code:e.code??'INTERNAL_ERROR',message:String(e.message??e)}},{status:e.status??500});
export const assertSameOrigin=()=>{throw new Error('Writes disabled in benchmark')};
export const getCompilerConfiguration=()=>{throw new Error('Compiler disabled in benchmark')};
export const readJsonObject=()=>{throw new Error('Writes disabled in benchmark')};
export const sha256Hex=()=>{throw new Error('Writes disabled in benchmark')};
`;
const noNetwork = `export class RetrievalClient {constructor(){throw new Error('Network disabled in benchmark')}};export const ensureRetrievalCoverage=()=>{throw new Error('Network disabled in benchmark')};`;
const loader = `
let rootUrl,storeUrl,networkUrl;
export function initialize(data){({rootUrl,storeUrl,networkUrl}=data)}
export async function resolve(specifier,context,nextResolve){
 if(/^@\\/lib\\/server\\/evidence-store(?:\\.ts)?$/.test(specifier))return{url:storeUrl,shortCircuit:true};
 if(/^@\\/lib\\/server\\/(?:retrieval-client|retrieval-sync)(?:\\.ts)?$/.test(specifier))return{url:networkUrl,shortCircuit:true};
 if(['@/lib/evidence-retrieval','@/lib/evidence-graph','@/lib/evidence-knowledge-graph'].includes(specifier))return{url:new URL(specifier.slice(2)+'/index.ts',rootUrl).href,shortCircuit:true};
 if(specifier.startsWith('@/')){let p=specifier.slice(2);if(!/\\.[cm]?[jt]sx?$/.test(p))p+='.ts';return{url:new URL(p,rootUrl).href,shortCircuit:true};}
 return nextResolve(specifier,context);
}`;
register(`data:text/javascript,${encodeURIComponent(loader)}`, { parentURL: import.meta.url, data: {
  rootUrl: root.href, storeUrl: `data:text/javascript,${encodeURIComponent(stub)}`, networkUrl: `data:text/javascript,${encodeURIComponent(noNetwork)}`,
} });
const { GET: libraryGet } = await import("../app/api/library/route.ts");
const { GET: graphGet } = await import("../app/api/evidence-knowledge-graph/route.ts");
const { GET: snapshotsGet } = await import("../app/api/evidence-graphs/route.ts");
const database = new DatabaseSync(databasePath, { readOnly: Boolean(existingPath) });
const measurements = [];
const statements = [];
const statsBinary = fs.existsSync("/opt/homebrew/opt/sqlite/bin/sqlite3") ? "/opt/homebrew/opt/sqlite/bin/sqlite3" : "/usr/bin/sqlite3";
const statsSqliteVersion = spawnSync(statsBinary, ["--version"], { encoding: "utf8" }).stdout?.trim();
function statementCounters(sql, bindings) {
  const parameters = bindings.map((value, index) => {
    const expression = value === null ? "NULL" : typeof value === "number" ? String(value) : `CAST(X'${Buffer.from(String(value)).toString("hex")}' AS TEXT)`;
    return `.parameter set ?${index + 1} "${expression}"`;
  });
  const input = ['.parameter init', ...parameters, '.stats on', sql + ';'].join('\n');
  const run = spawnSync(statsBinary, ["-batch", "-readonly", databasePath], { input, encoding: "utf8", maxBuffer: 4 * 1024 * 1024 });
  if (run.status !== 0) return { unavailable: run.stderr?.trim() || run.error?.message || "CLI failure" };
  return Object.fromEntries(["Fullscan Steps", "Sort Operations", "Virtual Machine Steps", "Page cache hits", "Page cache misses"].map((label) => [label, Number(new RegExp(`^${label}:\\s*(\\d+)`, 'm').exec(run.stdout)?.[1] ?? NaN)]));
}
class D1Statement {
  args = [];
  constructor(sql) { this.sql = sql; }
  bind(...values) { this.args = values; return this; }
  async all() { statements.push(this); return { results: database.prepare(this.sql).all(...this.args) }; }
  async first() { statements.push(this); return database.prepare(this.sql).get(...this.args) ?? null; }
  async run() { throw new Error("Route writes disabled in benchmark"); }
}
globalThis[globalKey].DB = { prepare: (sql) => new D1Statement(sql) };
const digest = (label) => createHash("sha256").update(label).digest("hex");
function identity(i) {
  const sha = digest(`synthetic-bytes-${i}`), graphSha = digest(`synthetic-snapshot-${i}`);
  return { artifact: `artifact:00000000-0000-4000-8000-${String(i).padStart(12, "0")}`, sha,
    version: `artifact-version:sha256:${sha}`, snapshot: `snapshot:sha256:${graphSha}`, graphSha,
    claim: `claim:sha256:${digest(`synthetic-claim-${i}`)}`,
    createdAt: new Date(Date.UTC(2020, 0, 1) + i * 1000).toISOString() };
}
const migrations = fs.readdirSync(new URL("drizzle/", root)).filter((name) => /^\d{4}_.+\.sql$/.test(name)).sort();
const started = performance.now();
try {
  let statusCounts;
  if (!existingPath) {
  database.enableDefensive(false); // Disposable fixture only; allow journal_mode=OFF.
  database.exec("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA temp_store=MEMORY; PRAGMA cache_size=-32768; PRAGMA max_page_count=850000;");
  for (const name of migrations) for (const sql of fs.readFileSync(new URL(`drizzle/${name}`, root), "utf8").split("--> statement-breakpoint")) {
    if (sql.trim()) database.exec(sql);
  }
  database.exec("PRAGMA foreign_keys=OFF; PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;");
  const deferredIndexes = phase === "graph" ? database.prepare("SELECT name,sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL AND name NOT LIKE 'library_name_search%'").all() : [];
  for (const { name } of deferredIndexes) database.exec(`DROP INDEX "${name.replaceAll('"', '""')}"`);
  // Seed read fixtures directly, not scientific imports. Keep real summary insertion
  // triggers so the actual FTS tokenizer/projection/count maintenance is exercised.
  for (const { name } of database.prepare("SELECT name FROM sqlite_master WHERE type='trigger'").all()) {
    if (name !== "library_summary_insert") database.exec(`DROP TRIGGER "${name.replaceAll('"', '""')}"`);
  }
  const artifact = database.prepare("INSERT INTO artifacts(id,project_id,preferred_name,artifact_type,created_by,created_at) VALUES(?,?,?,'paper','benchmark',?)");
  const version = database.prepare("INSERT INTO artifact_versions(id,artifact_id,version_number,sha256,source_locator,media_type,original_file_name,byte_size,object_key,client_request_id,request_hash,created_by,created_at) VALUES(?,?,1,?,'synthetic:metadata-only','application/pdf',?,100000,?,?,?,'benchmark',?)");
  const summary = database.prepare("INSERT INTO library_artifact_summaries(artifact_id,project_id,created_at,preferred_name,original_file_name,latest_version_id,version_count,latest_snapshot_id,library_status) VALUES(?,?,?,?,?,?,1,?,?)");
  const snapshot = database.prepare("INSERT INTO evidence_graph_snapshots(id,project_id,root_artifact_version_id,schema_version,graph_sha256,graph_json,counts_json,extractor_run_id,extractor_model,extractor_prompt_version,created_by,created_at) VALUES(?,?,?,'metadata-fixture',?,'{}',?,?,'not-run','not-run','benchmark',?)");
  const document = database.prepare("INSERT INTO evidence_retrieval_documents(project_id,snapshot_id,paper_id,paper_family_id,claim_id,scoped_claim_id,proposition_key,document_json,index_version,created_at) VALUES(?,?,?,?,?,?,?,?,'evidence-retrieval/2.0',?)");
  const active = database.prepare("INSERT INTO graph_active_claims(snapshot_id,claim_id,project_id,created_at,completed_at) VALUES(?,?,?,?,?)");
  const activeSnapshot = database.prepare("INSERT INTO evidence_active_snapshots(snapshot_id,project_id,index_version,paper_id,paper_family_id,document_count,created_at) VALUES(?,?,'evidence-retrieval/2.0',?,?,1,?)");
  const membership = database.prepare("INSERT INTO graph_taxonomy_memberships(snapshot_id,project_id,level,id,display_name,parent_id,is_primary,claim_count,processed_count,created_at) VALUES(?,?,'topic',?,?,'https://openalex.org/subfields/1000',1,1,?,?)");
  const topicColumns = database.prepare("PRAGMA table_info(openalex_paper_topics)").all().map((row) => row.name);
  const topic = database.prepare(`INSERT INTO openalex_paper_topics(${topicColumns.join(',')}) VALUES(${topicColumns.map(() => '?').join(',')})`);
  statusCounts = { pending: 0, completed: 0, failed: 0, processing: 0 };
  database.exec("BEGIN");
  for (let i = 0; i < count; i++) {
    const id = identity(i);
    const title = `Marine sediment evidence analysis ${String(i).padStart(6, "0")}${i === 3 ? ' 稀土 Qz9RareSuffix' : ''}`;
    const fileName = `${title}.pdf`;
    const status = i < 3 ? "failed" : i < 6 ? "processing" : i % 10 === 0 ? "completed" : "pending";
    statusCounts[status]++;
    if (phase !== "graph") {
    artifact.run(id.artifact, PROJECT_ID, title, id.createdAt);
    version.run(id.version, id.artifact, id.sha, fileName, id.sha, id.sha, id.sha, id.createdAt);
    if (status === "completed") snapshot.run(id.snapshot, PROJECT_ID, id.version, id.graphSha, '{"claims":1,"observations":0,"reasoning":0,"assumptions":0,"sourceAnchors":0,"visualEvidence":0,"edges":0}', `benchmark:${i}`, id.createdAt);
    summary.run(id.artifact, PROJECT_ID, id.createdAt, title, fileName, id.version, status === "completed" ? id.snapshot : null, status);
    }
    if (phase !== "library") {
    const canonical = `Marine sediment evidence Claim ${i}${i === 3 ? ' 稀土 Qz9RareSuffix' : ''}`;
    document.run(PROJECT_ID, id.snapshot, `paper:${i}`, id.artifact, id.claim, id.claim, `proposition:sha256:${digest(`proposition-${i}`)}`, JSON.stringify({ canonical_claim: canonical, paper_title: title }), id.createdAt);
    active.run(id.snapshot, id.claim, PROJECT_ID, id.createdAt, i < 5 ? id.createdAt : null);
    activeSnapshot.run(id.snapshot, PROJECT_ID, `paper:${i}`, id.artifact, id.createdAt);
    const topicId = i < 5 ? 'https://openalex.org/T1001' : 'https://openalex.org/T1000';
    const topicName = i < 5 ? 'Rare oldest topic' : 'Common topic';
    membership.run(id.snapshot, PROJECT_ID, topicId, topicName, i < 5 ? 1 : 0, id.createdAt);
    const topicRecord = { ordinal: 0, snapshot_id: id.snapshot, project_id: PROJECT_ID, topic_id: topicId, display_name: topicName, domain_id: 'https://openalex.org/domains/1', domain_name: 'Physical sciences', field_id: 'https://openalex.org/fields/10', field_name: 'Earth sciences', subfield_id: 'https://openalex.org/subfields/1000', subfield_name: 'Geochemistry', is_primary: 1, score: 1, created_at: id.createdAt };
    if (i < 5) topic.run(...topicColumns.map((name) => topicRecord[name] ?? null));
    }
    if ((i + 1) % 25000 === 0) {
      database.exec("COMMIT; BEGIN");
      console.log(JSON.stringify({ seeded: i + 1, elapsedMs: Math.round(performance.now() - started), diskBytes: fs.statSync(databasePath).size, rss: process.memoryUsage().rss }));
      await new Promise((resolve) => setImmediate(resolve));
    }
  }
  database.exec("COMMIT");
  if (phase !== "library") {
  database.prepare("INSERT INTO graph_read_counts VALUES(?,?,5,0,0)").run(PROJECT_ID, count);
  const taxon = database.prepare("INSERT INTO graph_taxonomy_nodes VALUES(?,'topic',?,?,'https://openalex.org/subfields/1000',?,?,?, ?,0)");
  taxon.run(PROJECT_ID, 'https://openalex.org/T1000', 'Common topic', count - 5, count - 5, count - 5, 0);
  taxon.run(PROJECT_ID, 'https://openalex.org/T1001', 'Rare oldest topic', 5, 5, 5, 5);
  }
  for (const { sql } of deferredIndexes) database.exec(sql);
  database.exec("ANALYZE");
  }
  const seedMs = existingPath ? null : performance.now() - started;
  const actualCount = Number(database.prepare("SELECT COUNT(*) AS n FROM library_artifact_summaries").get().n);
  async function measure(name, handler, parameters) {
    const request = () => new Request(`http://benchmark.invalid/api?${new URLSearchParams(parameters)}`);
    const latencies = [];
    let payload, responseBytes, statementPlans;
    for (let repetition = 0; repetition < 3; repetition++) {
      statements.length = 0;
      const start = performance.now();
      const response = await handler(request());
      const body = await response.text();
      latencies.push(performance.now() - start);
      assert.equal(response.status, 200, `${name}: ${body}`);
      payload = JSON.parse(body);
      responseBytes = Buffer.byteLength(body);
      if (repetition === 0) statementPlans = statements.map(({ sql, args: bindings }) => ({ sql, bindings, plan: database.prepare(`EXPLAIN QUERY PLAN ${sql}`).all(...bindings) }));
    }
    const result = { name, latencyMs: latencies, responseBytes, rows: (payload.artifacts ?? payload.claims ?? payload.snapshots ?? payload.taxonomy?.nodes ?? payload.neighborhood?.nodes ?? []).length, pageInfo: payload.pageInfo, searchInfo: payload.searchInfo, statements: statementPlans, memory: process.memoryUsage() };
    result.statements = statementPlans.map((entry) => ({ ...entry, cliCounters: statementCounters(entry.sql, entry.bindings) }));
    measurements.push(result);
    console.log(JSON.stringify({ ...result, statements: statementPlans.length, pageInfo: result.pageInfo ? { ...result.pageInfo, nextCursor: Boolean(result.pageInfo.nextCursor) } : undefined }));
    return payload;
  }
  const deepLibrary = database.prepare("SELECT created_at,artifact_id,search_id FROM library_artifact_summaries ORDER BY created_at ASC,artifact_id ASC LIMIT 1 OFFSET 50").get();
  const deep = database.prepare("SELECT created_at,snapshot_id,claim_id FROM graph_active_claims ORDER BY created_at ASC,snapshot_id ASC,claim_id ASC LIMIT 1 OFFSET 50").get();
  if (phase !== "graph") {
  const firstLibrary = await measure('library-first', libraryGet, { status: 'all', limit: '20' });
  if (firstLibrary.pageInfo.nextCursor && deepLibrary) {
    const cursor = JSON.parse(Buffer.from(firstLibrary.pageInfo.nextCursor, 'base64url').toString());
    Object.assign(cursor, { createdAt: deepLibrary.created_at, artifactId: deepLibrary.artifact_id, searchId: deepLibrary.search_id });
    await measure('library-deep-cursor-near-end', libraryGet, { status: 'all', limit: '20', cursor: Buffer.from(JSON.stringify(cursor)).toString('base64url') });
  }
  const rareStatus = await measure('library-rare-failed-oldest', libraryGet, { status: 'failed', limit: '20' });
  if (!existingPath) assert.equal(rareStatus.artifacts.length, 3);
  for (const query of ['Marine sediment', 'Marine sediment evidence analysis 000003 稀土 Qz9RareSuffix', '稀土', 'zzzz-no-match']) {
    const result = await measure(`library-search:${query}`, libraryGet, { status: 'all', limit: '20', query });
    if (!existingPath && query === '稀土') assert.equal(result.artifacts.length, 1);
  }
  }
  if (phase !== "library") {
  const firstGraph = await measure('graph-first', graphGet, { view: 'claims', status: 'all', limit: '20' });
  if (firstGraph.pageInfo.nextCursor && deep) {
    const cursor = JSON.parse(Buffer.from(firstGraph.pageInfo.nextCursor, 'base64url').toString());
    Object.assign(cursor, { createdAt: deep.created_at, snapshotId: deep.snapshot_id, claimId: deep.claim_id });
    await measure('graph-deep-cursor-near-end', graphGet, { view: 'claims', status: 'all', limit: '20', cursor: Buffer.from(JSON.stringify(cursor)).toString('base64url') });
  }
  const rareProcessed = await measure('graph-rare-processed-oldest', graphGet, { view: 'claims', status: 'processed', limit: '20' });
  if (!existingPath) assert.equal(rareProcessed.claims.length, 5);
  const rareTopicId = existingPath ? database.prepare("SELECT id FROM graph_taxonomy_nodes WHERE level='topic' AND claim_count>0 ORDER BY claim_count,id LIMIT 1").get()?.id ?? 'https://openalex.org/T1001' : 'https://openalex.org/T1001';
  const commonTopicId = existingPath ? database.prepare("SELECT id FROM graph_taxonomy_nodes WHERE level='topic' ORDER BY claim_count DESC,id LIMIT 1").get()?.id ?? 'https://openalex.org/T1000' : 'https://openalex.org/T1000';
  const rareTopic = await measure('graph-rare-topic-oldest', graphGet, { view: 'claims', status: 'all', level: 'topic', id: rareTopicId, limit: '20' });
  if (!existingPath) assert.equal(rareTopic.claims.length, 5);
  await measure('graph-common-topic', graphGet, { view: 'claims', status: 'all', level: 'topic', id: commonTopicId, limit: '20' });
  await measure('graph-search-no-match-bounded', graphGet, { view: 'claims', query: 'zzzz-no-match', limit: '20' });
  await measure('graph-taxonomy', graphGet, { view: 'taxonomy' });
  await measure('graph-neighborhood', graphGet, { view: 'neighborhood', claimSnapshotId: deep.snapshot_id, claimId: deep.claim_id });
  }
  if (existingPath) await measure('evidence-list-first', snapshotsGet, { limit: '20' });
  const report = {
    kind: existingPath ? 'existing-copy-readonly-real-route-benchmark' : 'metadata-only-real-route-benchmark', databasePath, phase, count: phase === "graph" ? count : actualCount, claims: Number(database.prepare('SELECT COUNT(*) AS n FROM graph_active_claims').get().n), statusCounts, seedMs,
    node: process.version, statsSqliteVersion, sqlite: database.prepare('select sqlite_version() AS version').get().version,
    routeHashes: Object.fromEntries(["app/api/library/route.ts", "app/api/evidence-knowledge-graph/route.ts", "app/api/evidence-graphs/import-store.ts"].map((name) => [name, digest(fs.readFileSync(new URL(name, root)))])),
    migrations: migrations.map((name) => ({ name, sha256: digest(fs.readFileSync(new URL(`drizzle/${name}`, root))) })),
    edges: Number(database.prepare('SELECT COUNT(*) AS n FROM corpus_claim_graph_edges').get().n),
    peakRssBytes: process.resourceUsage().maxRSS * 1024,
    databaseBytes: fs.statSync(databasePath).size,
    allocatedPageBytes: Number(database.prepare('PRAGMA page_count').get().page_count) * Number(database.prepare('PRAGMA page_size').get().page_size),
    measurements, limitations: [existingPath ? "Readonly migrated copy of existing metadata; no writes, ANALYZE or migrations performed by benchmark." : "Synthetic database ANALYZE run after seeding.",existingPath ? 'Readonly database-copy measurements only; no models or network calls.' : 'Synthetic metadata seeded sequentially as independent library and graph databases to cap disk use; one Claim per paper; no PDFs, full text, scientific imports, models or network calls.', existingPath ? 'Existing copy is not synthetic; timings include shared-machine I/O contention and are reported separately from public HTTP.' : 'Read fixtures directly populated; only library summary insert triggers retained during seeding. Graph secondary indexes are deferred only during seeding and rebuilt from the exact migration SQL before any GET. This measures real GET SQL, not import throughput.', 'CLI counters replay the exact captured SQL/bindings in a separate readonly SQLite connection/version; fullscan/VM steps are not exact application-level rows read. EXPLAIN comes from the Node connection.', 'Three sequential local SQLite calls; first and warm timings are not Cloudflare/public network latency.', existingPath ? 'Existing relation data retained in readonly copy.' : 'Synthetic graph has no relation edges: this does not benchmark high-degree neighborhoods.'],
  };
  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ reportPath, databaseBytes: report.databaseBytes, totalMs: performance.now() - started }));
} catch (error) {
  fs.writeFileSync(reportPath, JSON.stringify({ kind: "benchmark-incomplete", phase, requestedCount: count, databasePath,
    error: String(error), diskBytes: fs.existsSync(databasePath) ? fs.statSync(databasePath).size : 0,
    pageLimitBytes: 850000 * 4096, measurements,
    limitation: "Incomplete run is not evidence of 500,000-row completion. SQLite page allocation is capped to protect host disk." }, null, 2));
  throw error;
} finally {
  database.close();
  if (existingPath || args.keep !== "true") fs.rmSync(temporary, { recursive: true, force: true });
  else console.log(JSON.stringify({ retainedDatabase: databasePath }));
}
