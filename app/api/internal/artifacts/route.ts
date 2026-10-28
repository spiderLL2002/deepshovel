import {
  ApiError,
  assertLocalMaintainer,
  assertSameOrigin,
  getActor,
  normalizeMimeType,
  readJsonObject,
  safeError,
} from "@/lib/server/evidence-store";
import { deleteArtifactCompletely } from "../../artifacts/delete-store.ts";

const ARTIFACT_ID_RE = /^artifact:[0-9a-f-]{36}$/;
const MAX_DELETE_REQUEST_BYTES = 64 * 1024;
const MAX_SELECTED_ARTIFACTS = 1_000;

/**
 * Local maintenance only: permanently purge whole Artifacts.
 *
 * The public site cannot delete papers. This route is unreachable through the
 * Cloudflare Tunnel (every `/api/internal` path is answered with 404 at the
 * edge) and additionally rejects any request that did not arrive directly on
 * the Mac's loopback interface. Run it on the Mac, for example:
 *
 *   curl -X DELETE http://127.0.0.1:8100/api/internal/artifacts \
 *     -H 'content-type: application/json' \
 *     -d '{"artifactId":"artifact:…","confirmArtifactId":"artifact:…"}'
 */
export async function DELETE(request: Request) {
  try {
    assertLocalMaintainer(request);
    assertSameOrigin(request);
    const contentType = normalizeMimeType(request.headers.get("content-type") ?? "");
    if (contentType !== "application/json") {
      throw new ApiError(415, "CONTENT_TYPE_UNSUPPORTED", "Artifact deletion requires application/json");
    }
    const body = await readJsonObject(request, MAX_DELETE_REQUEST_BYTES);
    const keys = Object.keys(body).sort().join(",");
    let artifactIds: string[];
    let single = false;
    if (keys === "artifactId,confirmArtifactId"
      && typeof body.artifactId === "string" && typeof body.confirmArtifactId === "string"
      && ARTIFACT_ID_RE.test(body.artifactId) && body.artifactId === body.confirmArtifactId) {
      artifactIds = [body.artifactId];
      single = true;
    } else if (keys === "artifactIds,confirm" && body.confirm === "DELETE_SELECTED"
      && Array.isArray(body.artifactIds) && body.artifactIds.length >= 1
      && body.artifactIds.length <= MAX_SELECTED_ARTIFACTS
      && body.artifactIds.every((id) => typeof id === "string" && ARTIFACT_ID_RE.test(id))) {
      artifactIds = [...new Set(body.artifactIds as string[])];
    } else {
      throw new ApiError(400, "INVALID_DELETE_CONFIRMATION", "Artifact deletion confirmation is invalid");
    }
    const actor = getActor(request);
    const deleted = [];
    for (const artifactId of artifactIds) deleted.push(await deleteArtifactCompletely(artifactId, actor));
    if (single) return Response.json(deleted[0]);
    return Response.json({ ok: true, deletedCount: deleted.length, artifacts: deleted });
  } catch (error) {
    return safeError(error);
  }
}
