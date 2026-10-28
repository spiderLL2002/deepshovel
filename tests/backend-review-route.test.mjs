import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

const bindings = { DB: null, BUCKET: {} };
globalThis.__reviewRouteTestBindings = bindings;

const storeStub = `
export const PROJECT_ID = "DEEPSHOVEL-PROJECT";
export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message); this.name = "ApiError"; this.status = status; this.code = code; this.details = details;
  }
}
export function assertSameOrigin() {}
export function getActor() { return "test@example.com"; }
export async function getBindings() { return globalThis.__reviewRouteTestBindings; }
export function normalizeIdempotencyKey(value, fallback) { return value?.trim() || fallback; }
export async function readJsonObject(request) { return request.json(); }
export async function sha256Hex(value) {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
export function safeError(error) {
  if (error instanceof ApiError) {
    return Response.json({ ok: false, error: { code: error.code, message: error.message } }, { status: error.status });
  }
  return Response.json({ ok: false, error: { code: "INTERNAL_ERROR", message: String(error) } }, { status: 500 });
}`;

const loader = `
let rootUrl;
let storeUrl;
export function initialize(data) { rootUrl = data.rootUrl; storeUrl = data.storeUrl; }
export async function resolve(specifier, context, nextResolve) {
  if (specifier === "cloudflare:workers") {
    return {
      url: "data:text/javascript,export const env=globalThis.__reviewRouteTestBindings",
      shortCircuit: true,
    };
  }
  if (specifier === "@/lib/server/evidence-store") {
    return { url: storeUrl, shortCircuit: true };
  }
  if (specifier.startsWith("@/")) {
    let path = specifier.slice(2);
    if (path === "lib/evidence-graph" || path === "lib/evidence-retrieval") path += "/index.ts";
    else if (!/\\.[cm]?[jt]sx?$/.test(path)) path += ".ts";
    return { url: new URL(path, rootUrl).href, shortCircuit: true };
  }
  return nextResolve(specifier, context);
}`;

register(`data:text/javascript,${encodeURIComponent(loader)}`, {
  parentURL: import.meta.url,
  data: {
    rootUrl: new URL("../", import.meta.url).href,
    storeUrl: `data:text/javascript,${encodeURIComponent(storeStub)}`,
  },
});

const { POST } = await import("../app/api/evidence-graphs/[snapshotId]/reviews/route.ts");
const { listEvidenceGraphSnapshots } = await import("../app/api/evidence-graphs/import-store.ts");

class Statement {
  args = [];

  constructor(db, sql) {
    this.db = db;
    this.sql = sql;
  }

  bind(...args) {
    this.args = args;
    return this;
  }

  first() {
    return this.db.first(this.sql, this.args);
  }

  run() {
    return this.db.run(this.sql, this.args);
  }

  all() {
    return this.db.all(this.sql, this.args);
  }
}

class MemoryD1 {
  reviews = [];
  verificationCounts = new Map();

  constructor(snapshots) {
    this.snapshots = snapshots;
  }

  prepare(sql) {
    return new Statement(this, sql);
  }

  async first(sql, args) {
    if (sql.includes("FROM graph_review_events") && sql.includes("client_request_id")) {
      return this.reviews.find((review) => review.projectId === args[0]
        && review.reviewer === args[1] && review.clientRequestId === args[2]) ?? null;
    }
    if (sql.includes("FROM evidence_graph_snapshots")) {
      return this.snapshots.find((snapshot) => snapshot.projectId === args[0] && snapshot.id === args[1]) ?? null;
    }
    return null;
  }

  async run(sql, args) {
    if (!sql.includes("INSERT INTO graph_review_events")) return { success: true };
    if (this.reviews.some((review) => review.projectId === args[1]
      && review.reviewer === args[5] && review.clientRequestId === args[6])) {
      throw new Error("UNIQUE constraint failed: graph_review_events.client_request_id");
    }
    this.reviews.push({
      seq: this.reviews.length + 1,
      id: args[0],
      projectId: args[1],
      snapshotId: args[2],
      decision: args[3],
      rationale: args[4],
      reviewer: args[5],
      clientRequestId: args[6],
      createdAt: `2026-08-15T00:00:0${this.reviews.length + 1}Z`,
    });
    return { success: true };
  }

  async all(sql, args) {
    if (!sql.includes("FROM snapshot_list_summaries")) return { results: [] };
    const rows = this.snapshots
      .filter((snapshot) => snapshot.projectId === args[0])
      .sort((left, right) => right.createdAt.localeCompare(left.createdAt))
      .slice(0, 50)
      .map((snapshot) => {
        const latest = this.reviews
          .filter((review) => review.projectId === snapshot.projectId && review.snapshotId === snapshot.id)
          .sort((left, right) => right.seq - left.seq)[0];
        return {
          ...snapshot,
          latestReviewId: latest?.id ?? null,
          latestReviewDecision: latest?.decision ?? null,
          latestReviewRationale: latest?.rationale ?? null,
          latestReviewer: latest?.reviewer ?? null,
          latestReviewCreatedAt: latest?.createdAt ?? null,
          verificationRunCount: this.verificationCounts.get(snapshot.id) ?? 0,
        };
      });
    return { results: rows };
  }
}

const FIRST_SNAPSHOT = `snapshot:sha256:${"a".repeat(64)}`;
const SECOND_SNAPSHOT = `snapshot:sha256:${"b".repeat(64)}`;

function snapshot(id, createdAt) {
  return {
    id,
    projectId: "DEEPSHOVEL-PROJECT",
    rootArtifactVersionId: `artifact-version:sha256:${"c".repeat(64)}`,
    schemaVersion: "1.0",
    graphSha256: id.slice("snapshot:sha256:".length),
    countsJson: JSON.stringify({ claims: 3 }),
    extractorRunId: `extractor:${id.at(-1)}`,
    extractorModel: "gpt-5.6-luna",
    extractorPromptVersion: "extractor/1",
    createdAt,
  };
}

function reviewRequest(snapshotId, idempotencyKey, decision, rationale) {
  return POST(new Request(`http://localhost/api/evidence-graphs/${snapshotId}/reviews`, {
    method: "POST",
    headers: { "content-type": "application/json", "idempotency-key": idempotencyKey },
    body: JSON.stringify({ decision, rationale }),
  }), { params: Promise.resolve({ snapshotId }) });
}

test("review idempotency keys are bound to the snapshot and normalized payload", async () => {
  const db = new MemoryD1([
    snapshot(FIRST_SNAPSHOT, "2026-08-15T00:00:00Z"),
    snapshot(SECOND_SNAPSHOT, "2026-08-14T00:00:00Z"),
  ]);
  bindings.DB = db;

  const created = await reviewRequest(FIRST_SNAPSHOT, "review-key", "accepted", "  Checked against the source.  ");
  assert.equal(created.status, 201);
  assert.equal(db.reviews.length, 1);

  const replay = await reviewRequest(FIRST_SNAPSHOT, "review-key", "accepted", "Checked against the source.");
  assert.equal(replay.status, 200);
  assert.equal((await replay.json()).duplicate, true);
  assert.equal(db.reviews.length, 1);

  const changedPayload = await reviewRequest(FIRST_SNAPSHOT, "review-key", "rejected", "Checked against the source.");
  assert.equal(changedPayload.status, 409);
  assert.equal((await changedPayload.json()).error.code, "IDEMPOTENCY_KEY_REUSED");

  const changedSnapshot = await reviewRequest(SECOND_SNAPSHOT, "review-key", "accepted", "Checked against the source.");
  assert.equal(changedSnapshot.status, 409);
  assert.equal((await changedSnapshot.json()).error.code, "IDEMPOTENCY_KEY_REUSED");
  assert.equal(db.reviews.length, 1);
});

test("withdrawal is append-only and derives an unreviewed current state", async () => {
  const db = new MemoryD1([
    snapshot(FIRST_SNAPSHOT, "2026-08-15T00:00:00Z"),
    snapshot(SECOND_SNAPSHOT, "2026-08-14T00:00:00Z"),
  ]);
  db.verificationCounts.set(FIRST_SNAPSHOT, 2);
  bindings.DB = db;

  assert.equal((await reviewRequest(FIRST_SNAPSHOT, "review-accept", "accepted", "Checked against the source.")).status, 201);
  assert.equal((await reviewRequest(FIRST_SNAPSHOT, "review-withdraw", "withdrawn", "Withdrawing the human marker.")).status, 201);
  assert.deepEqual(db.reviews.map((review) => review.decision), ["accepted", "withdrawn"]);

  const summaries = await listEvidenceGraphSnapshots();
  const first = summaries.find((item) => item.id === FIRST_SNAPSHOT);
  const second = summaries.find((item) => item.id === SECOND_SNAPSHOT);
  assert.equal(first.latestReview, null);
  assert.deepEqual(first.machineVerification, {
    status: "assessed",
    runCount: 2,
    limitationCount: 0,
  });
  assert.equal(second.latestReview, null);
  assert.deepEqual(second.machineVerification, {
    status: "unassessed",
    runCount: 0,
    limitationCount: 0,
  });
});
