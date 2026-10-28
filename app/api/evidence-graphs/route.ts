import {
  ApiError,
  assertSameOrigin,
  getActor,
  readJsonObject,
  safeError,
} from "@/lib/server/evidence-store";
import { parseEvidenceImportEnvelope } from "./import-contract.ts";
import {
  listEvidenceGraphSnapshotPage,
  persistEvidenceImport,
  type SnapshotListFilter,
  type SnapshotListCursor,
} from "./import-store.ts";

const REVIEW_FILTERS = new Set<SnapshotListFilter>(["all", "unreviewed", "confirmed"]);

function positiveInteger(value: string | null, fallback: number, maximum: number) {
  if (value === null) return fallback;
  if (!/^[1-9][0-9]{0,8}$/u.test(value)) throw new ApiError(400, "INVALID_SNAPSHOT_PAGE", "page and limit must be positive integers");
  const parsed = Number(value);
  if (parsed > maximum) throw new ApiError(400, "INVALID_SNAPSHOT_PAGE", "page or limit exceeds the supported range");
  return parsed;
}

export async function GET(request: Request) {
  try {
    const parameters = new URL(request.url).searchParams;
    if (![...parameters.keys()].every((key) => ["query", "status", "page", "limit", "cursor"].includes(key) && parameters.getAll(key).length === 1)) {
      throw new ApiError(400, "INVALID_SNAPSHOT_LIST_REQUEST", "Evidence graph list parameters are invalid");
    }
    const query = parameters.get("query")?.trim() ?? "";
    if (query.length > 200) throw new ApiError(400, "INVALID_SNAPSHOT_QUERY", "query may contain at most 200 characters");
    const status = (parameters.get("status") ?? "all") as SnapshotListFilter;
    if (!REVIEW_FILTERS.has(status)) throw new ApiError(400, "INVALID_REVIEW_STATUS", "review status is invalid");
    const page = positiveInteger(parameters.get("page"), 1, 1);
    void page;
    const rawCursor = parameters.get("cursor");
    let cursor: SnapshotListCursor | null = null;
    if (rawCursor !== null) {
      try {
        if (!rawCursor || rawCursor.length > 2048 || !/^[A-Za-z0-9_-]+$/.test(rawCursor) || parameters.has("page")) throw new Error();
        const base64 = rawCursor.replaceAll("-", "+").replaceAll("_", "/").padEnd(Math.ceil(rawCursor.length / 4) * 4, "=");
        const value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(Uint8Array.from(atob(base64), (character) => character.charCodeAt(0)))) as SnapshotListCursor;
        if (Object.keys(value).sort().join(",") !== "createdAt,query,snapshotId,status,version" || value.version !== 1
          || typeof value.createdAt !== "string" || value.createdAt.length > 64 || !Number.isFinite(Date.parse(value.createdAt))
          || typeof value.snapshotId !== "string" || !/^snapshot:[A-Za-z0-9._:-]{1,150}$/.test(value.snapshotId)
          || value.query !== query || value.status !== status) throw new Error();
        cursor = value;
      } catch { throw new ApiError(400, "INVALID_SNAPSHOT_CURSOR", "Snapshot cursor is invalid or belongs to another query"); }
    }
    const pageSize = positiveInteger(parameters.get("limit"), 20, 50);
    const result = await listEvidenceGraphSnapshotPage({ query, status, cursor, pageSize });
    return Response.json({
      ok: true,
      ...result,
    });
  } catch (error) {
    return safeError(error);
  }
}

export async function POST(request: Request) {
  try {
    assertSameOrigin(request);
    const actor = getActor(request);
    const payload = await readJsonObject(request);
    const imported = await parseEvidenceImportEnvelope(payload);
    const result = await persistEvidenceImport(imported, actor);
    return Response.json(result.body, { status: result.status });
  } catch (error) {
    return safeError(error);
  }
}
