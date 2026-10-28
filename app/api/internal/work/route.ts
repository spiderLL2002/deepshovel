import {
  ApiError,
  assertInternalWorker,
  readJsonObject,
  safeError,
} from "@/lib/server/evidence-store";
import { runWorkTick } from "./work-runner.ts";

const DEFAULT_BUDGET_MS = 20_000;
const MAX_BUDGET_MS = 55_000;

/**
 * One bounded pass of durable background work, called in a loop by the local
 * work pump (deploy/runtime/compile-sync.py). Loopback-only, credential
 * protected and unreachable through the Tunnel. Example on the Mac:
 *
 *   curl -X POST http://127.0.0.1:8100/api/internal/work \
 *     -H "authorization: Bearer $COMPILER_SHARED_SECRET" \
 *     -H 'content-type: application/json' -d '{"budget_ms":20000}'
 */
export async function POST(request: Request) {
  try {
    await assertInternalWorker(request);
    const body = await readJsonObject(request, 1024);
    const unknown = Object.keys(body).filter((key) => key !== "budget_ms");
    if (unknown.length) throw new ApiError(400, "INVALID_WORK_TICK", `Unknown fields: ${unknown.join(", ")}`);
    const budget = body.budget_ms === undefined ? DEFAULT_BUDGET_MS : body.budget_ms;
    if (!Number.isSafeInteger(budget) || (budget as number) < 1_000 || (budget as number) > MAX_BUDGET_MS) {
      throw new ApiError(400, "INVALID_WORK_TICK", `budget_ms must be an integer from 1000 to ${MAX_BUDGET_MS}`);
    }
    const summary = await runWorkTick({ budgetMs: budget as number });
    return Response.json({ ok: true, ...summary }, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    return safeError(error);
  }
}
