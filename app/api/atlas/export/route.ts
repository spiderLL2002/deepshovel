const MAX_BODY_BYTES = 2 * 1024 * 1024;
const object = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const text = (value: unknown, max = 20_000): value is string =>
  typeof value === "string" && value.length <= max;
const nonempty = (value: unknown, max = 20_000) => text(value, max) && value.trim().length > 0;

function validPatch(data: Record<string, unknown>) {
  return data.schema_version === "geochemistry-research-patch-v1"
    && data.canonical_file === "geochemistry.csv"
    && data.semantics === "proposal_only_no_direct_mutation"
    && Array.isArray(data.operations) && data.operations.length > 0 && data.operations.length <= 1000
    && data.operations.every((op: unknown) => object(op)
      && ["create", "update", "logical_exclude"].includes(String(op.operation))
      && nonempty(op.record_id, 512) && nonempty(op.reason)
      && text(op.field, 256) && text(op.proposed_value)
      && (op.operation === "logical_exclude" || (nonempty(op.field, 256) && nonempty(op.proposed_value)))
      && (op.source_record_snapshot === null || (object(op.source_record_snapshot)
        && nonempty(op.source_record_snapshot.record_id, 512)
        && [op.source_record_snapshot.source_id, op.source_record_snapshot.source_locator]
          .every(value => value === undefined || value === null || text(value)))));
}

function validComparison(data: Record<string, unknown>) {
  const pair = data.comparison, display = data.display;
  if (data.schema_version !== "d3-visualization-profile-v2" || data.story !== "comparison"
    || data.theme !== "evidence-dark" || !nonempty(data.title, 120) || !text(data.subtitle, 1000)
    || !["global", "regional"].includes(String(data.spatial_scope))
    || !nonempty(data.default_region, 100) || !object(data.filters) || !object(pair) || !object(display)) return false;
  const elements = ["As", "Cr", "Cu", "Hg", "Ni", "Pb", "Zn"];
  if (!elements.includes(String(pair.x)) || !elements.includes(String(pair.y)) || pair.x === pair.y
    || !(pair.medium === null || ["rock", "soil", "water", "sediment"].includes(String(pair.medium)))
    || !["distribution", "heat", "combined"].includes(String(display.map_mode))
    || !["value", "medium", "element"].includes(String(display.color_by))
    || ![1, 2, 5].includes(Number(display.anomaly_grid_degrees))
    || typeof display.show_anomaly_points !== "boolean" || typeof display.show_anomaly_regions !== "boolean") return false;
  if (data.default_region === "custom") {
    const region = data.custom_region;
    if (!object(region) || !nonempty(region.label, 80) || !object(region.bounds)) return false;
    const {w, s, e, n} = region.bounds;
    if (![w, s, e, n].every(value => typeof value === "number" && Number.isFinite(value))) return false;
    return Number(w) >= -180 && Number(e) <= 180 && Number(w) < Number(e)
      && Number(s) >= -90 && Number(n) <= 90 && Number(s) < Number(n);
  }
  return data.custom_region === null;
}

function failure(message: string, status = 400) {
  return Response.json({error: message}, {status, headers: {"cache-control": "no-store"}});
}

/** Echo validated client-only export data as an attachment; never persist or execute proposals. */
export async function POST(request: Request) {
  const origin = request.headers.get("origin");
  const fetchSite = request.headers.get("sec-fetch-site")?.trim().toLowerCase();
  // The Tunnel forwards the public origin to a loopback Worker URL. Preserve
  // the browser's same-origin relationship, as other write routes do.
  if (fetchSite === "cross-site"
    || (origin && origin !== new URL(request.url).origin && fetchSite !== "same-origin")) {
    return failure("只允许同源导出。", 403);
  }
  if (request.headers.get("content-type")?.split(";")[0].trim() !== "application/x-www-form-urlencoded") {
    return failure("请使用导出表单。", 415);
  }
  if (Number(request.headers.get("content-length")) > MAX_BODY_BYTES) return failure("导出内容超过 2 MiB。", 413);
  if (!request.body) return failure("缺少导出内容。");
  const reader = request.body.getReader(), chunks: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      length += chunk.value.byteLength;
      if (length > MAX_BODY_BYTES) { await reader.cancel(); return failure("导出内容超过 2 MiB。", 413); }
      chunks.push(chunk.value);
    }
    const bytes = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    const form = new URLSearchParams(new TextDecoder("utf-8", {fatal: true}).decode(bytes));
    if (form.getAll("kind").length !== 1 || form.getAll("payload").length !== 1) return failure("导出参数无效。");
    const kind = form.get("kind"), data: unknown = JSON.parse(form.get("payload")!);
    if (!object(data) || !((kind === "patch" && validPatch(data)) || (kind === "comparison" && validComparison(data)))) {
      return failure("导出结构无效，请重新生成。");
    }
    const filename = kind === "patch" ? "geochemistry-research-patch.json" : "geochemistry-comparison-profile.json";
    return new Response(JSON.stringify(data, null, 2) + "\n", {
      headers: {
        "content-type": "application/json; charset=utf-8",
        "content-disposition": `attachment; filename="${filename}"`,
        "cache-control": "private, no-store",
        "x-content-type-options": "nosniff",
      },
    });
  } catch {
    return failure("无法读取导出内容，请重新生成。");
  } finally {
    reader.releaseLock();
  }
}
