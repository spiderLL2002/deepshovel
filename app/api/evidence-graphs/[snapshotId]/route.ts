import {
  EvidenceGraphValidationError,
  createEvidencePathExpander,
  parseEvidenceGraph,
} from "@/lib/evidence-graph";
import { ApiError, getBindings, PROJECT_ID, safeError } from "@/lib/server/evidence-store";

type RouteContext = { params: Promise<{ snapshotId: string }> };
const SNAPSHOT_ID_RE = /^snapshot:sha256:[a-f0-9]{64}$/;

type ReviewRow = {
  id: string;
  snapshotId: string;
  decision: "accepted" | "rejected" | "needs_revision" | "withdrawn";
  rationale: string;
  reviewer: string;
  createdAt: string;
};

type VerificationRow = Record<string, unknown> & { reportJson: string };

function countVerificationLimitations(report: unknown) {
  if (!report || typeof report !== "object" || Array.isArray(report)) return 0;
  const records = (report as { assessments?: unknown }).assessments;
  if (!Array.isArray(records)) return 0;
  const seen = new Set<string>();
  for (const assessment of records) {
    if (!assessment || typeof assessment !== "object" || Array.isArray(assessment)) continue;
    const rawLimitations = (assessment as { limitations?: unknown }).limitations;
    if (!Array.isArray(rawLimitations)) continue;
    for (const value of rawLimitations) {
      if (typeof value === "string" && value.trim()) seen.add(value.trim());
    }
  }
  return seen.size;
}

async function parseStoredGraph(graphJson: string) {
  try {
    return await parseEvidenceGraph(JSON.parse(graphJson));
  } catch (error) {
    if (error instanceof EvidenceGraphValidationError) {
      throw new ApiError(
        409,
        "STORED_SNAPSHOT_INCOMPATIBLE",
        "Stored evidence no longer satisfies the current evidence contract. Recompile the source artifact.",
        {
          retryable: false,
          action: "recompile",
          validation: { code: error.code, path: error.path },
        },
      );
    }
    if (error instanceof SyntaxError) {
      throw new ApiError(
        409,
        "STORED_SNAPSHOT_INCOMPATIBLE",
        "Stored evidence no longer satisfies the current evidence contract. Recompile the source artifact.",
        {
          retryable: false,
          action: "recompile",
          validation: { code: "INVALID_JSON", path: "$" },
        },
      );
    }
    throw error;
  }
}

export async function GET(_request: Request, context: RouteContext) {
  try {
    const { snapshotId } = await context.params;
    if (!SNAPSHOT_ID_RE.test(snapshotId)) throw new ApiError(400, "INVALID_SNAPSHOT_ID", "Snapshot ID is invalid");
    const { DB } = await getBindings();
    const row = await DB.prepare(`SELECT id, root_artifact_version_id AS rootArtifactVersionId,
      schema_version AS schemaVersion, graph_sha256 AS graphSha256,
      graph_json AS graphJson, counts_json AS countsJson,
      extractor_run_id AS extractorRunId, extractor_model AS extractorModel,
      extractor_prompt_version AS extractorPromptVersion, created_at AS createdAt
      FROM evidence_graph_snapshots WHERE project_id = ? AND id = ? LIMIT 1`)
      .bind(PROJECT_ID, snapshotId).first<{
        id: string;
        rootArtifactVersionId: string;
        schemaVersion: string;
        graphSha256: string;
        graphJson: string;
        countsJson: string;
        extractorRunId: string;
        extractorModel: string;
        extractorPromptVersion: string;
        createdAt: string;
      }>();
    if (!row) throw new ApiError(404, "SNAPSHOT_NOT_FOUND", "Evidence graph snapshot was not found");
    const graph = await parseStoredGraph(row.graphJson);
    const [reviews, verifications] = await Promise.all([
      DB.prepare(`SELECT id, snapshot_id AS snapshotId, decision, rationale, reviewer, created_at AS createdAt
        FROM graph_review_events WHERE project_id = ? AND snapshot_id = ? ORDER BY seq DESC`)
        .bind(PROJECT_ID, snapshotId).all<ReviewRow>(),
      DB.prepare(`SELECT id, verifier_run_id AS runId, verifier_model AS model,
        verifier_prompt_version AS promptVersion, report_sha256 AS reportSha256,
        report_json AS reportJson, created_at AS createdAt
        FROM verification_runs WHERE project_id = ? AND snapshot_id = ? ORDER BY created_at DESC`)
        .bind(PROJECT_ID, snapshotId).all<VerificationRow>(),
    ]);
    const expandPath = createEvidencePathExpander(graph);
    const paths = graph.claims.map((claim) => expandPath(claim.id));
    const latestReviewEvent = reviews.results[0] ?? null;
    const parsedVerifications = verifications.results.map((item) => ({
      ...item,
      report: JSON.parse(String(item.reportJson)),
      reportJson: undefined,
    }));
    const limitationCount = parsedVerifications[0] ? countVerificationLimitations(parsedVerifications[0].report) : 0;
    return Response.json({
      ok: true,
      snapshot: {
        id: row.id,
        rootArtifactVersionId: row.rootArtifactVersionId,
        schemaVersion: row.schemaVersion,
        graphSha256: row.graphSha256,
        counts: JSON.parse(row.countsJson),
        extraction: { runId: row.extractorRunId, model: row.extractorModel, promptVersion: row.extractorPromptVersion },
        machineVerification: {
          status: verifications.results.length > 0 ? "assessed" : "unassessed",
          runCount: verifications.results.length,
          limitationCount,
        },
        latestReview: latestReviewEvent?.decision === "withdrawn" ? null : latestReviewEvent,
        detailUrl: `/api/evidence-graphs/${encodeURIComponent(row.id)}`,
        createdAt: row.createdAt,
      },
      graph,
      paths,
      reviews: reviews.results,
      verifications: parsedVerifications,
    });
  } catch (error) {
    return safeError(error);
  }
}
