import {
  ApiError,
  assertSameOrigin,
  getActor,
  normalizeIdempotencyKey,
  readJsonObject,
  safeError,
  sha256Hex,
} from "@/lib/server/evidence-store";
import {
  cancelCompileJobs,
  getCompileJob,
  listCompileJobs,
  submitCompileJob,
} from "./compile-store.ts";

function assertCompileOrigin(request: Request) {
  assertSameOrigin(request);
  if (request.headers.get("sec-fetch-site") === "cross-site") {
    throw new ApiError(403, "CROSS_ORIGIN_WRITE_REJECTED", "Cross-origin Compile request rejected");
  }
}

/**
 * Read-only: lists the actor's jobs or returns one job. Progress, result import
 * and retries are owned by the server-side work pump, never by a GET.
 */
export async function GET(request: Request) {
  try {
    assertCompileOrigin(request);
    const actor = getActor(request);
    const parameters = new URL(request.url).searchParams;
    const unknown = [...parameters.keys()].filter((key) => key !== "job_id");
    if (unknown.length) throw new ApiError(400, "INVALID_COMPILE_QUERY", `Unknown query parameters: ${unknown.join(", ")}`);
    const jobId = parameters.get("job_id");
    if (jobId !== null && !jobId.trim()) {
      throw new ApiError(400, "INVALID_COMPILE_JOB_ID", "job_id must not be empty");
    }
    return Response.json(jobId === null
      ? await listCompileJobs(actor)
      : await getCompileJob(actor, jobId.trim()));
  } catch (error) {
    return safeError(error);
  }
}

export async function POST(request: Request) {
  try {
    assertCompileOrigin(request);
    const actor = getActor(request);
    const body = await readJsonObject(request, 16 * 1024);
    const unknown = Object.keys(body).filter((key) => key !== "artifact_version_id");
    if (unknown.length) throw new ApiError(400, "INVALID_COMPILE_REQUEST", `Unknown fields: ${unknown.join(", ")}`);
    const artifactVersionId = typeof body.artifact_version_id === "string" ? body.artifact_version_id.trim() : "";
    const fallback = `compile:${await sha256Hex(JSON.stringify({ actor, artifactVersionId }))}`;
    const key = normalizeIdempotencyKey(request.headers.get("idempotency-key"), fallback);
    const result = await submitCompileJob(actor, key, artifactVersionId);
    return Response.json(result, { status: result.duplicate ? 200 : 202 });
  } catch (error) {
    return safeError(error);
  }
}

export async function DELETE(request: Request) {
  try {
    assertCompileOrigin(request);
    const actor = getActor(request);
    const body = await readJsonObject(request, 16 * 1024);
    const unknown = Object.keys(body).filter((key) => key !== "job_ids" && key !== "all");
    if (unknown.length) throw new ApiError(400, "INVALID_COMPILE_CANCEL_REQUEST", `Unknown fields: ${unknown.join(", ")}`);
    const all = body.all === true;
    const rawIds = body.job_ids;
    if (all === (rawIds !== undefined)) {
      throw new ApiError(400, "INVALID_COMPILE_CANCEL_REQUEST", "Specify either all=true or job_ids");
    }
    let jobIds: string[] | null = null;
    if (!all) {
      if (!Array.isArray(rawIds) || rawIds.length < 1 || rawIds.length > 50
        || rawIds.some((id) => typeof id !== "string" || !id.trim())) {
        throw new ApiError(400, "INVALID_COMPILE_CANCEL_REQUEST", "job_ids must contain 1 to 50 job IDs");
      }
      jobIds = [...new Set(rawIds.map((id) => (id as string).trim()))];
    }
    return Response.json(await cancelCompileJobs(actor, jobIds));
  } catch (error) {
    return safeError(error);
  }
}
