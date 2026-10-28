import {
  ApiError,
  assertLocalMaintainer,
  assertSameOrigin,
  readJsonObject,
  safeError,
} from "@/lib/server/evidence-store";
import { compileJobReceipt, reopenFailedImport } from "../../compile/compile-job-store.ts";

/**
 * Local maintenance only: import a failed job's retained Compile result again.
 * Compile results are copied to R2 before their first import attempt, so a
 * result whose import failed (for example because of a validator bug) can be
 * imported after the fix without paying for a new parse and model run:
 *
 *   curl -X POST http://127.0.0.1:8100/api/internal/compile \
 *     -H 'content-type: application/json' \
 *     -d '{"action":"reimport","job_id":"<compile job id>"}'
 */
export async function POST(request: Request) {
  try {
    assertLocalMaintainer(request);
    assertSameOrigin(request);
    const body = await readJsonObject(request, 1024);
    if (Object.keys(body).sort().join(",") !== "action,job_id" || body.action !== "reimport"
      || typeof body.job_id !== "string") {
      throw new ApiError(400, "INVALID_MAINTENANCE_REQUEST", "Specify {\"action\":\"reimport\",\"job_id\":\"…\"}");
    }
    const row = await reopenFailedImport(body.job_id.trim());
    return Response.json({ ok: true, job: compileJobReceipt(row) }, { status: 202 });
  } catch (error) {
    return safeError(error);
  }
}
