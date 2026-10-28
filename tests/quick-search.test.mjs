import test from "node:test";
import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import { readFileSync } from "node:fs";
import { quickSearchGroups, quickSearchRefs } from "../lib/server/quick-search.ts";

const textMigration = readFileSync(new URL("../drizzle/0023_quick_search_text.sql", import.meta.url), "utf8");

test("quick recall uses local bilingual aliases, active corpus and project boundaries", async () => {
  const sqlite = new DatabaseSync(":memory:");
  try {
    sqlite.exec("CREATE TABLE evidence_retrieval_documents (project_id TEXT, snapshot_id TEXT, claim_id TEXT, document_json TEXT)");
    const insert = sqlite.prepare("INSERT INTO evidence_retrieval_documents VALUES (?, ?, ?, ?)");
    for (const [project, snapshot, claim, text] of [
      ["p", "active", "match", "Rare earth elements in deep-sea sediments"],
      ["p", "old", "old-match", "Rare earth elements in deep-sea sediments"],
      ["other", "active", "private", "Rare earth elements in deep-sea sediments"],
      ["p", "active", "unrelated", "Volcanic rainfall"],
    ]) insert.run(project, snapshot, claim, JSON.stringify({ canonical_claim: text }));
    sqlite.exec(textMigration);
    const DB = { prepare(sql) { return { bind(...args) { return {
      async all() { return { results: sqlite.prepare(sql).all(...args) }; },
    }; } }; } };
    const result = await quickSearchRefs(DB, "p", ["active"], "深海沉积物中稀土元素的富集机制");
    assert.deepEqual(result, [{ snapshotId: "active", claimId: "match" }]);
    assert.deepEqual(await quickSearchRefs(DB, "p", [], "稀土"), []);
  } finally { sqlite.close(); }
});

test("text projection exactly preserves literal scores, boundaries, active scope and top-30 ties", async () => {
  const sqlite = new DatabaseSync(":memory:");
  try {
    sqlite.exec("CREATE TABLE evidence_retrieval_documents (project_id TEXT, snapshot_id TEXT, claim_id TEXT, document_json TEXT, PRIMARY KEY(snapshot_id,claim_id))");
    const insert = sqlite.prepare("INSERT INTO evidence_retrieval_documents VALUES (?, ?, ?, ?)");
    const samples = [
      { canonical_claim: "REE at start", original_claim: "space ree space fe oae " },
      { canonical_claim: "tree reef free", original_claim: null },
      { canonical_claim: "Rare earth enrichment in sediment", original_claim: "甲乙丙丁 富集" },
      { canonical_claim: "REE,FE.OAE", original_claim: "rare earth" },
      { canonical_claim: "volcanic rainfall", original_claim: "甲乙" },
      { canonical_claim: "ÄREE STRAßE", original_claim: "丙丁" },
      { original_claim: "sediment" }, {},
    ];
    for (let i = 0; i < 128; i++) insert.run(i % 11 === 0 ? "other" : "p", `s${i % 4}`, `c${String(i).padStart(3, "0")}`, JSON.stringify(samples[i % samples.length]));
    sqlite.exec(textMigration);
    let lastSql, lastArgs;
    const DB = { prepare(sql) { lastSql = sql; return { bind(...args) { lastArgs = args; return {
      async all() { return { results: sqlite.prepare(sql).all(...args) }; },
    }; } }; } };
    for (const query of ["REE", "OAE", "rare earth sediment", "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未", "volcanic rainfall"]) {
      assert.ok(quickSearchGroups(query).length > 0);
      const actual = await quickSearchRefs(DB, "p", ["s0", "s1", "s1", "s2"], query);
      const legacy = lastSql.replace("d.search_text", "lower(coalesce(json_extract(d.document_json, '$.canonical_claim'), '') || ' ' || coalesce(json_extract(d.document_json, '$.original_claim'), ''))")
        .replace("scored AS MATERIALIZED (", "scored AS (")
        .replace("FROM active a CROSS JOIN evidence_quick_search d ON a.snapshot_id = d.snapshot_id", "FROM evidence_retrieval_documents d JOIN active a ON a.snapshot_id = d.snapshot_id");
      const expected = sqlite.prepare(legacy).all(...lastArgs).map(row => ({ snapshotId: row.snapshot_id, claimId: row.claim_id }));
      assert.deepEqual(actual, expected, query);
    }
    const plan = sqlite.prepare(`EXPLAIN QUERY PLAN ${lastSql}`).all(...lastArgs).map(row => row.detail);
    assert.ok(plan.findIndex(row => row.startsWith("SCAN json_each")) < plan.findIndex(row => row.startsWith("SEARCH d")), plan.join("\n"));
  } finally { sqlite.close(); }
});

test("projection follows insert/delete transactions and backfill uses SQLite normalization", () => {
  const sqlite = new DatabaseSync(":memory:");
  try {
    sqlite.exec("CREATE TABLE evidence_retrieval_documents (project_id TEXT, snapshot_id TEXT, claim_id TEXT, document_json TEXT, PRIMARY KEY(snapshot_id,claim_id))");
    const insert = sqlite.prepare("INSERT INTO evidence_retrieval_documents VALUES ('p','s',?,?)");
    insert.run("before", JSON.stringify({ canonical_claim: "ÄABC", original_claim: null }));
    sqlite.exec(textMigration);
    assert.equal(sqlite.prepare("SELECT search_text FROM evidence_quick_search WHERE claim_id='before'").get().search_text, "Äabc ");
    sqlite.exec("BEGIN");
    insert.run("rolled-back", JSON.stringify({ original_claim: "REE" }));
    sqlite.exec("ROLLBACK");
    assert.equal(sqlite.prepare("SELECT count(*) AS n FROM evidence_quick_search").get().n, 1);
    insert.run("after", JSON.stringify({ original_claim: "REE" }));
    assert.equal(sqlite.prepare("SELECT search_text FROM evidence_quick_search WHERE claim_id='after'").get().search_text, " ree");
    sqlite.exec("DELETE FROM evidence_retrieval_documents WHERE claim_id='before'");
    assert.deepEqual(sqlite.prepare("SELECT claim_id FROM evidence_quick_search").all().map(x => x.claim_id), ["after"]);
  } finally { sqlite.close(); }
});

test("quick recall is bounded and supports unmapped literal queries", () => {
  assert.ok(quickSearchGroups("volcanic rainfall").flat().includes("rainfall"));
  assert.ok(quickSearchGroups("没有预设别名的问题").length > 0);
  assert.ok(quickSearchGroups("文本".repeat(800)).length <= 16);
});

test("single and two-character Chinese words are recalled inside longer text", async () => {
  assert.deepEqual(quickSearchGroups("汞"), [["汞"]]);
  assert.deepEqual(quickSearchGroups("华南 二叠纪 汞"), [["华南"], ["二叠"], ["叠纪"], ["汞"]]);
  const mixed = quickSearchGroups("稀土 汞");
  assert.ok(mixed.some(group => group.includes("稀土")));
  assert.deepEqual(mixed.at(-1), ["汞"]);
  const sqlite = new DatabaseSync(":memory:");
  try {
    sqlite.exec("CREATE TABLE evidence_retrieval_documents (project_id TEXT, snapshot_id TEXT, claim_id TEXT, document_json TEXT)");
    sqlite.prepare("INSERT INTO evidence_retrieval_documents VALUES ('p', 's', 'mercury', ?)")
      .run(JSON.stringify({ canonical_claim: "华南二叠纪沉积物出现汞异常" }));
    sqlite.exec(textMigration);
    const DB = { prepare(sql) { return { bind(...args) { return {
      async all() { return { results: sqlite.prepare(sql).all(...args) }; },
    }; } }; } };
    assert.deepEqual(await quickSearchRefs(DB, "p", ["s"], "汞"), [{ snapshotId: "s", claimId: "mercury" }]);
  } finally { sqlite.close(); }
});
