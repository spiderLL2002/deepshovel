import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

const root = new URL("../", import.meta.url);
const data = (source) => `data:text/javascript,${encodeURIComponent(source)}`;
const projection = data(`
export const toEvidenceRetrievalManifestDto = m => ({snapshot_id:m.snapshotId,index_version:m.indexVersion});
export const toEvidenceRetrievalIndexRequestDto = (_project,b) => ({manifest:toEvidenceRetrievalManifestDto(b.manifest),documents:b.documents});
export const loadEvidenceRetrievalIndexBundles = (db, project, ids) => db.load(project,ids);
`);
register(data(`
let root,projection;
export function initialize(d){({root,projection}=d);}
export function resolve(s,c,next){
  if(s === "@/lib/evidence-retrieval")return {url:projection,shortCircuit:true};
  if(s.startsWith("@/"))return {url:new URL(s.slice(2)+".ts",root).href,shortCircuit:true};
  return next(s,c);
}`), { parentURL: import.meta.url, data: { root: root.href, projection } });
const { ensureRetrievalCoverage, indexEvidenceRetrievalSnapshot, needsIndexing } = await import("../lib/server/retrieval-sync.ts");

const manifest = (id, documentCount = 1) => ({ snapshotId: id, indexVersion: "evidence-retrieval/2.0", documentCount });

function fakeDb({ documents = 1 } = {}) {
  const requested = [];
  const loads = [];
  return {
    requested,
    loads,
    async load(_project, ids) {
      loads.push(ids);
      return ids.map((id) => ({
        manifest: manifest(id, documents),
        documents: Array.from({ length: documents }, (_, index) => ({ claim_id: `claim-${id}-${index}` })),
      }));
    },
    prepare(sql) {
      return {
        bind: (...args) => ({
          run: async () => {
            if (sql.includes("INSERT INTO work_items")) requested.push({ kind: args[0], subjects: JSON.parse(args[8]) });
            return { meta: { changes: 1 } };
          },
        }),
      };
    },
  };
}

function fakeClient(states, { indexed = new Set(), failIndex = false } = {}) {
  const calls = [];
  return {
    calls,
    async checkManifests(_project, manifests) {
      calls.push({ operation: "check", ids: manifests.map((item) => item.snapshot_id) });
      return { manifests: manifests.map((item) => ({
        snapshot_id: item.snapshot_id,
        document_count: 1,
        indexed_document_count: states[item.snapshot_id] === "indexed" || states[item.snapshot_id] === "degraded" ? 1 : 0,
        status: states[item.snapshot_id] ?? "missing",
      })) };
    },
    async indexSnapshot(_project, m, documents) {
      calls.push({ operation: "index", id: m.snapshot_id, documents: documents.length });
      if (failIndex) throw new Error("index unavailable");
      for (const document of documents) indexed.add(document.claim_id);
      return {
        indexed_documents: documents.length,
        manifests: [{ indexed_document_count: indexed.size, status: "indexed" }],
        warnings: [],
      };
    },
    async deleteSnapshots(_project, ids) {
      calls.push({ operation: "delete", ids });
      return { deleted_snapshots: ids.length, deleted_documents: 0, warnings: [] };
    },
  };
}

test("a search never indexes: missing snapshots go to the durable queue with a warning", async () => {
  const db = fakeDb();
  const client = fakeClient({ a: "indexed", b: "missing", c: "stale", d: "degraded" });
  const coverage = await ensureRetrievalCoverage(db, client, ["a", "b", "c", "d"].map((id) => manifest(id)));
  assert.equal(coverage.checkedSnapshots, 4);
  assert.equal(coverage.pendingSnapshots, 2);
  assert.deepEqual(db.requested, [{ kind: "retrieval_index", subjects: ["b", "c"] }]);
  assert.deepEqual(client.calls.map((call) => call.operation), ["check"], "no index or embedding on the search path");
  assert.equal(db.loads.length, 0);
  assert.equal(coverage.warnings.length, 2);
  assert.match(coverage.warnings[0], /2 篇论文的检索索引仍在后台建立/u);
  assert.match(coverage.warnings[1], /1 篇论文的语义向量通道当前降级/u);
});

test("fully indexed corpora produce no queue writes or warnings", async () => {
  const db = fakeDb();
  const coverage = await ensureRetrievalCoverage(db, fakeClient({ a: "indexed" }), [manifest("a")]);
  assert.deepEqual(coverage, { checkedSnapshots: 1, pendingSnapshots: 0, warnings: [] });
  assert.deepEqual(db.requested, []);
});

test("background indexing batches documents and resumes from the saved chunk", async () => {
  const db = fakeDb({ documents: 70 });
  const indexed = new Set();
  const client = fakeClient({}, { indexed });
  const target = manifest("big", 70);
  const partial = await indexEvidenceRetrievalSnapshot(db, client, target, undefined, {
    deadline: Date.now() - 1,
    batchDocuments: 32,
  });
  assert.deepEqual(partial, {
    nextChunk: 1, totalChunks: 3, indexedDocuments: 32, done: false, status: "partial", warnings: [],
  });
  const resumed = await indexEvidenceRetrievalSnapshot(db, client, target, undefined, {
    startChunk: partial.nextChunk,
    batchDocuments: 32,
  });
  assert.equal(resumed.done, true);
  assert.equal(resumed.status, "indexed");
  assert.deepEqual(client.calls.filter((call) => call.operation === "index").map((call) => call.documents), [32, 32, 6]);
  assert.equal(indexed.size, 70);
});

test("a stale manifest is replaced and an incomplete index is a retryable failure", async () => {
  const db = fakeDb();
  const client = fakeClient({});
  await indexEvidenceRetrievalSnapshot(db, client, manifest("stale"), { status: "stale", document_count: 1, indexed_document_count: 1 });
  assert.deepEqual(client.calls.map((call) => call.operation), ["delete", "index"]);

  const failing = fakeClient({}, { failIndex: true });
  await assert.rejects(indexEvidenceRetrievalSnapshot(db, failing, manifest("x")), /index unavailable/u);

  const incomplete = {
    ...fakeClient({}),
    async indexSnapshot() {
      return { indexed_documents: 1, manifests: [{ indexed_document_count: 0, status: "indexed" }], warnings: [] };
    },
  };
  await assert.rejects(indexEvidenceRetrievalSnapshot(db, incomplete, manifest("y")), (error) => (
    error.code === "RETRIEVAL_SYNC_INCOMPLETE" && error.details.retryable === true
  ));
});

test("index state classification matches the manifest counts", () => {
  assert.equal(needsIndexing(manifest("a"), undefined), true);
  assert.equal(needsIndexing(manifest("a"), { status: "indexed", document_count: 1, indexed_document_count: 1 }), false);
  assert.equal(needsIndexing(manifest("a", 2), { status: "indexed", document_count: 1, indexed_document_count: 1 }), true);
  assert.equal(needsIndexing(manifest("a"), { status: "degraded", document_count: 1, indexed_document_count: 1 }), false);
  assert.equal(needsIndexing(manifest("a", 2), { status: "degraded", document_count: 2, indexed_document_count: 1 }), true);
});
