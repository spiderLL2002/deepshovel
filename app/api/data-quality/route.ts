import {
  ApiError,
  assertSameOrigin,
  readJsonObject,
  safeError,
} from "@/lib/server/evidence-store";
import { dataQualityStatus, submitDataQuality } from "../compile/compile-store.ts";

const VERSION_ID_RE = /^artifact-version:sha256:[a-f0-9]{64}$/;

function assertOrigin(request: Request) {
  assertSameOrigin(request);
  if (request.headers.get("sec-fetch-site") === "cross-site") {
    throw new ApiError(403, "CROSS_ORIGIN_WRITE_REJECTED", "Cross-origin Data Quality request rejected");
  }
}

function versionId(value: unknown) {
  const id = typeof value === "string" ? value.trim() : "";
  if (!VERSION_ID_RE.test(id)) {
    throw new ApiError(400, "INVALID_ARTIFACT_VERSION_ID", "artifact_version_id must be a canonical SHA-256 version ID");
  }
  return id;
}

export async function GET(request: Request) {
  try {
    assertOrigin(request);
    const parameters = new URL(request.url).searchParams;
    const unknown = [...parameters.keys()].filter((key) => key !== "artifact_version_id");
    if (unknown.length) throw new ApiError(400, "INVALID_DATA_QUALITY_QUERY", `Unknown query parameters: ${unknown.join(", ")}`);
    return Response.json(await dataQualityStatus(versionId(parameters.get("artifact_version_id"))));
  } catch (error) {
    return safeError(error);
  }
}

export async function POST(request: Request) {
  try {
    assertOrigin(request);
    const body = await readJsonObject(request, 16 * 1024);
    const unknown = Object.keys(body).filter((key) => key !== "artifact_version_id");
    if (unknown.length) throw new ApiError(400, "INVALID_DATA_QUALITY_REQUEST", `Unknown fields: ${unknown.join(", ")}`);
    return Response.json(await submitDataQuality(versionId(body.artifact_version_id)), { status: 202 });
  } catch (error) {
    return safeError(error);
  }
}
