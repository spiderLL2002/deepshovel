import {
  ApiError,
  assertSameOrigin,
  getActor,
  getBindings,
  normalizeIdempotencyKey,
  PROJECT_ID,
  readJsonObject,
  safeError,
  sha256Hex,
} from "@/lib/server/evidence-store";

type RouteContext = { params: Promise<{ snapshotId: string }> };
const SNAPSHOT_ID_RE = /^snapshot:sha256:[a-f0-9]{64}$/;
const DECISIONS = new Set(["accepted", "rejected", "needs_revision", "withdrawn"]);
const MAX_REVIEW_REQUEST_BYTES = 16 * 1024;

type ReviewRow = {
  id: string;
  snapshotId: string;
  decision: string;
  rationale: string;
  reviewer: string;
  createdAt: string;
};

async function findReplay(DB: D1Database, actor: string, idempotencyKey: string) {
  return DB.prepare(`SELECT id, snapshot_id AS snapshotId, decision, rationale, reviewer,
    created_at AS createdAt FROM graph_review_events
    WHERE project_id = ? AND reviewer = ? AND client_request_id = ? LIMIT 1`)
    .bind(PROJECT_ID, actor, idempotencyKey).first<ReviewRow>();
}

function assertSameReviewRequest(
  replay: ReviewRow,
  snapshotId: string,
  decision: string,
  rationale: string,
) {
  if (replay.snapshotId !== snapshotId || replay.decision !== decision || replay.rationale !== rationale) {
    throw new ApiError(409, "IDEMPOTENCY_KEY_REUSED", "Idempotency key was already used for a different review request");
  }
}

export async function POST(request: Request, context: RouteContext) {
  try {
    assertSameOrigin(request);
    const { snapshotId } = await context.params;
    if (!SNAPSHOT_ID_RE.test(snapshotId)) throw new ApiError(400, "INVALID_SNAPSHOT_ID", "Snapshot ID is invalid");
    const actor = getActor(request);
    const payload = await readJsonObject(request, MAX_REVIEW_REQUEST_BYTES);
    const unknown = Object.keys(payload).filter((key) => key !== "decision" && key !== "rationale");
    if (unknown.length) throw new ApiError(400, "INVALID_REVIEW_REQUEST", `Unknown fields: ${unknown.join(", ")}`);
    const decision = typeof payload.decision === "string" ? payload.decision : "";
    const rationale = typeof payload.rationale === "string" ? payload.rationale.trim() : "";
    if (!DECISIONS.has(decision)) throw new ApiError(400, "INVALID_REVIEW_DECISION", "Review decision is invalid");
    if (rationale.length < 3 || rationale.length > 4_000) {
      throw new ApiError(400, "INVALID_REVIEW_RATIONALE", "Review rationale must contain 3 to 4000 characters");
    }
    const requestHash = await sha256Hex(JSON.stringify({ snapshotId, decision, rationale }));
    const idempotencyKey = normalizeIdempotencyKey(request.headers.get("idempotency-key"), `graph-review:${requestHash}`);
    const { DB } = await getBindings();
    const replay = await findReplay(DB, actor, idempotencyKey);
    if (replay) {
      assertSameReviewRequest(replay, snapshotId, decision, rationale);
      return Response.json({ ok: true, review: replay, duplicate: true });
    }
    const snapshot = await DB.prepare("SELECT id FROM evidence_graph_snapshots WHERE project_id = ? AND id = ? LIMIT 1")
      .bind(PROJECT_ID, snapshotId).first();
    if (!snapshot) throw new ApiError(404, "SNAPSHOT_NOT_FOUND", "Evidence graph snapshot was not found");

    const id = crypto.randomUUID();
    try {
      await DB.prepare(`INSERT INTO graph_review_events
        (id, project_id, snapshot_id, decision, rationale, reviewer, client_request_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)`)
        .bind(id, PROJECT_ID, snapshotId, decision, rationale, actor, idempotencyKey).run();
    } catch (error) {
      const winner = await findReplay(DB, actor, idempotencyKey);
      if (!winner) throw error;
      assertSameReviewRequest(winner, snapshotId, decision, rationale);
      return Response.json({ ok: true, review: winner, duplicate: true });
    }
    return Response.json({
      ok: true,
      review: { id, snapshotId, decision, rationale, reviewer: actor, createdAt: new Date().toISOString() },
      duplicate: false,
    }, { status: 201 });
  } catch (error) {
    return safeError(error);
  }
}
