import assert from "node:assert/strict";
import test from "node:test";

import {
  RETRIEVAL_SESSION_TTL_MS,
  createRetrievalSession,
  resolveRetrievalSession,
} from "../lib/server/retrieval-sessions.ts";
import { isRetrievalSessionId } from "../lib/evidence-retrieval/contract.ts";
import { migratedDatabase, SqliteD1 } from "./helpers/sqlite-d1.mjs";

const HANDOFF = `v1.derived-auth-v1.e30.${"a".repeat(43)}`;
const CONTEXT = `n1.derived-auth-v1.e30.${"b".repeat(43)}`;

test("a session ID resolves to its one token of its one kind until it expires", async () => {
  const database = migratedDatabase();
  const DB = new SqliteD1(database);
  const now = new Date("2026-10-19T00:00:00.000Z");
  const id = await createRetrievalSession(DB, "query_plan_handoff", HANDOFF, now);
  assert.equal(isRetrievalSessionId(id), true);
  assert.equal(id.includes(HANDOFF), false);
  // Kind-bound: a handoff session is not a context capability.
  assert.equal(await resolveRetrievalSession(DB, "context", id, now), null);
  const expired = new Date(now.getTime() + RETRIEVAL_SESSION_TTL_MS);
  assert.equal(await resolveRetrievalSession(DB, "query_plan_handoff", id, expired), null);
  for (const value of [null, HANDOFF, "rs1.short", `rs1.${"!".repeat(43)}`]) {
    assert.equal(await resolveRetrievalSession(DB, "query_plan_handoff", value, now), null);
  }
});

test("use slides the expiry so a final can follow a late continuation preview", async () => {
  const database = migratedDatabase();
  const DB = new SqliteD1(database);
  const created = new Date("2026-10-19T00:00:00.000Z");
  const id = await createRetrievalSession(DB, "context", CONTEXT, created);
  // The user answers the context question just before the session expires.
  const preview = new Date(created.getTime() + RETRIEVAL_SESSION_TTL_MS - 1_000);
  assert.equal(await resolveRetrievalSession(DB, "context", id, preview), CONTEXT);
  // The final resends the same continuation after the original expiry.
  const final = new Date(created.getTime() + RETRIEVAL_SESSION_TTL_MS + 60_000);
  assert.equal(await resolveRetrievalSession(DB, "context", id, final), CONTEXT);
  // An idle session still expires one TTL after its last use.
  const idle = new Date(final.getTime() + RETRIEVAL_SESSION_TTL_MS);
  assert.equal(await resolveRetrievalSession(DB, "context", id, idle), null);
});

test("each write prunes a bounded batch of expired sessions and keeps live ones", async () => {
  const database = migratedDatabase();
  const DB = new SqliteD1(database);
  const start = new Date("2026-10-19T00:00:00.000Z");
  const ids = [];
  for (let index = 0; index < 205; index += 1) {
    ids.push(await createRetrievalSession(DB, "context", CONTEXT, start));
  }
  assert.equal(new Set(ids).size, 205);
  const later = new Date(start.getTime() + RETRIEVAL_SESSION_TTL_MS + 1);
  const live = await createRetrievalSession(DB, "clarification", CONTEXT, later);
  const count = () => Number(database.prepare("SELECT COUNT(*) AS n FROM retrieval_sessions").get().n);
  assert.equal(count(), 206 - 200);
  await createRetrievalSession(DB, "clarification", CONTEXT, later);
  assert.equal(count(), 2);
  assert.equal(await resolveRetrievalSession(DB, "clarification", live, later), CONTEXT);
});

test("the schema refuses malformed session rows", () => {
  const database = migratedDatabase();
  const insert = database.prepare(`INSERT INTO retrieval_sessions (id, kind, token, created_at, expires_at)
    VALUES (?, ?, ?, '2026-10-19T00:00:00.000Z', '2026-10-19T00:30:00.000Z')`);
  const valid = `rs1.${"A".repeat(43)}`;
  assert.throws(() => insert.run(valid, "preview", HANDOFF), /CHECK/u);
  assert.throws(() => insert.run("rs2.x", "context", HANDOFF), /CHECK/u);
  assert.throws(() => insert.run(valid, "context", ""), /CHECK/u);
  insert.run(valid, "context", HANDOFF);
});
