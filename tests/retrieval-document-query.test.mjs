import assert from "node:assert/strict";
import fs from "node:fs";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";

import { RETRIEVAL_DOCUMENTS_BY_SNAPSHOT_SQL } from "../lib/evidence-retrieval/persistence.ts";

function migrated() {
  const database = new DatabaseSync(":memory:");
  const names = fs.readdirSync(new URL("../drizzle/", import.meta.url)).filter((name) => /^\d{4}_.+\.sql$/u.test(name)).sort();
  for (const name of names) {
    const sql = fs.readFileSync(new URL(`../drizzle/${name}`, import.meta.url), "utf8");
    for (const statement of sql.split("--> statement-breakpoint")) if (statement.trim()) database.exec(statement);
  }
  return database;
}

test("snapshot document loads use the primary key, not a project-wide index scan", () => {
  // Without planner statistics (local D1 has none) the unguarded query chose
  // the (project_id, proposition_key) index and read every document of the
  // project: about 7 s per snapshot sync on the live 41k-Claim library.
  const database = migrated();
  try {
    const detail = database.prepare(`EXPLAIN QUERY PLAN ${RETRIEVAL_DOCUMENTS_BY_SNAPSHOT_SQL}`)
      .all("DEEPSHOVEL-PROJECT", "evidence-retrieval/2.0", JSON.stringify(["snapshot:a", "snapshot:b"]))
      .map((row) => row.detail)
      .join("\n");
    assert.match(detail, /SEARCH d USING INDEX sqlite_autoindex_evidence_retrieval_documents_1 \(snapshot_id=\?\)/u);
    assert.doesNotMatch(detail, /evidence_retrieval_document_proposition_idx|SCAN d\b/u);
  } finally {
    database.close();
  }
});
