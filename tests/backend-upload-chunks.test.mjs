import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { register } from "node:module";
import test from "node:test";
import { UPLOAD_CHUNK_BYTES } from "../lib/artifacts/upload-limits.mjs";

const loader = `
let root;
export function initialize(data) { root = data.root; }
export async function resolve(specifier, context, next) {
  if (specifier === "cloudflare:workers") return { url: "data:text/javascript,export const env=globalThis.__chunkBindings", shortCircuit:true };
  if (specifier === "../route.ts" && context.parentURL.endsWith("/artifacts/upload/route.ts")) return { url: "data:text/javascript,export const POST=(request)=>globalThis.__chunkImport(request)", shortCircuit:true };
  if (specifier.startsWith("@/")) {
    let path=specifier.slice(2); if (!/\\.[cm]?[jt]s$/.test(path)) path += ".ts";
    return {url:new URL(path,root).href,shortCircuit:true};
  }
  return next(specifier,context);
}`;
register(`data:text/javascript,${encodeURIComponent(loader)}`, { data: { root: new URL("../", import.meta.url).href } });
const { PUT, POST, DELETE } = await import("../app/api/artifacts/upload/route.ts");

class Bucket {
  objects = new Map();
  listCalls = [];
  sequence = 0;
  async get(key) { return this.objects.get(key) ?? null; }
  async put(key, value, options = {}) {
    const current = this.objects.get(key);
    if (options.onlyIf?.get("if-none-match") === "*" && current) return null;
    if (options.onlyIf?.has("if-match") && options.onlyIf.get("if-match") !== current?.httpEtag) return null;
    const data = typeof value === "string" ? new TextEncoder().encode(value) : value.slice();
    const object = { key, size: data.length, uploaded: new Date(), httpEtag: `"${++this.sequence}"`, ...options,
      arrayBuffer: async () => data.slice().buffer, text: async () => new TextDecoder().decode(data) };
    this.objects.set(key, object); return object;
  }
  async delete(keys) { for (const key of Array.isArray(keys) ? keys : [keys]) this.objects.delete(key); }
  async list({ prefix, cursor, limit = 1000 }) {
    this.listCalls.push({ prefix, cursor, limit });
    const values = [...this.objects.values()].filter(x => x.key.startsWith(prefix) && (!cursor || x.key > cursor))
      .sort((a, b) => a.key < b.key ? -1 : a.key > b.key ? 1 : 0);
    const objects = values.slice(0, limit);
    return { objects, truncated: values.length > limit, cursor: objects.at(-1)?.key };
  }
}
const CLEANUP_KEY = "upload-maintenance/expired-parts-v1.json";
function setup() {
  const bucket = new Bucket();
  globalThis.__chunkBindings ??= { DB: {} };
  globalThis.__chunkBindings.BUCKET = bucket;
  const bytes = new Uint8Array(UPLOAD_CHUNK_BYTES + 37).fill(97);
  bytes.set(new TextEncoder().encode("%PDF-1.7\n"));
  const headers = { "content-type": "application/pdf", "x-artifact-name": "paper.pdf", "x-upload-id": randomUUID(),
    "x-upload-size": String(bytes.length), "x-upload-sha256": createHash("sha256").update(bytes).digest("hex") };
  const request = (method, index, data = bytes, extra = {}) => new Request("https://deepshovel.test/api/artifacts/upload", {
    method, headers: { ...headers, ...(index === undefined ? {} : { "x-upload-index": String(index) }), ...extra },
    ...(method === "PUT" ? { body: data.slice(index * UPLOAD_CHUNK_BYTES, (index + 1) * UPLOAD_CHUNK_BYTES) } : {}),
  });
  return { bucket, bytes, headers, request };
}

test("out-of-order and retried chunks reconstruct exact bytes; lost final responses replay without reimport", async () => {
  const { bucket, bytes, request } = setup(); let imports = 0;
  globalThis.__chunkImport = async (req) => {
    imports++; assert.deepEqual(new Uint8Array(await req.arrayBuffer()), bytes);
    assert.equal(req.headers.get("content-length"), String(bytes.length));
    return Response.json({ ok: true, version: { sha256: createHash("sha256").update(bytes).digest("hex") } }, { status: 201 });
  };
  for (const index of [1, 0, 1]) assert.equal((await PUT(request("PUT", index))).status, 200);
  const result = await POST(request("POST")); assert.equal(result.status, 201);
  const payload = await result.json();
  assert.equal([...bucket.objects.keys()].filter(key => key !== CLEANUP_KEY).length, 1, "only the small receipt remains beside maintenance state");
  assert.deepEqual(await (await POST(request("POST"))).json(), payload);
  assert.equal(imports, 1);
});

test("missing, wrong-sized, and corrupted chunks never reach artifact import", async () => {
  const { bytes, request } = setup();
  globalThis.__chunkImport = () => { throw new Error("must not import damaged upload"); };
  assert.equal((await PUT(request("PUT", 0, bytes.slice(0, 10)))).status, 400);
  await PUT(request("PUT", 0));
  assert.equal((await (await POST(request("POST"))).json()).error.code, "UPLOAD_INCOMPLETE");
  const altered = bytes.slice(); altered[altered.length - 1] ^= 1;
  await PUT(request("PUT", 1, altered));
  assert.equal((await (await POST(request("POST"))).json()).error.code, "UPLOAD_DIGEST_MISMATCH");
});

test("upload parts are isolated by actor, cleaned on cancellation, and abandoned parts expire", async () => {
  const { bucket, request } = setup();
  await bucket.put("upload-parts/old/0", "expired");
  bucket.objects.get("upload-parts/old/0").uploaded = new Date(0);
  await bucket.put("artifacts/protected-source", "keep");
  await PUT(request("PUT", 0));
  assert.equal(bucket.objects.has("upload-parts/old/0"), false);
  assert.equal((await (await POST(request("POST", undefined, undefined, { "oai-authenticated-user-email": "another@test" }))).json()).error.code, "UPLOAD_INCOMPLETE");
  assert.equal((await DELETE(request("DELETE"))).status, 200);
  assert.deepEqual([...bucket.objects.keys()].filter(key => key !== CLEANUP_KEY), ["artifacts/protected-source"]);
});

test("cleanup advances one bounded page per cooldown and preserves valid receipts", async (t) => {
  const { bucket, request } = setup();
  let now = Date.now();
  t.mock.method(Date, "now", () => now);
  for (let i = 0; i < 257; i++) {
    const key = `upload-parts/old/${String(i).padStart(4, "0")}`;
    await bucket.put(key, "expired"); bucket.objects.get(key).uploaded = new Date(0);
  }
  await bucket.put("upload-parts/recent/receipt", "valid receipt");
  assert.equal((await PUT(request("PUT", 0))).status, 200);
  assert.equal(bucket.listCalls.length, 1);
  assert.equal(bucket.listCalls[0].limit, 256);
  assert.equal([...bucket.objects.keys()].filter(k => k.startsWith("upload-parts/old/")).length, 1);
  await PUT(request("PUT", 0));
  assert.equal(bucket.listCalls.length, 1, "another file/part cannot trigger a full rescan during cooldown");
  now += 61_000;
  await PUT(request("PUT", 0));
  assert.equal(bucket.listCalls.length, 2);
  assert.ok(bucket.listCalls[1].cursor);
  assert.equal([...bucket.objects.keys()].filter(k => k.startsWith("upload-parts/old/")).length, 0);
  assert.equal(await (await bucket.get("upload-parts/recent/receipt")).text(), "valid receipt");
});

test("competing cleaners share a lease; failed cleanup never rejects upload and remains retryable", async (t) => {
  const { bucket, request } = setup();
  let now = Date.now(); t.mock.method(Date, "now", () => now);
  await bucket.put("upload-parts/old/0", "expired"); bucket.objects.get("upload-parts/old/0").uploaded = new Date(0);
  const originalDelete = bucket.delete.bind(bucket);
  let fail = true;
  bucket.delete = async (keys) => { if (fail) throw new Error("temporary R2 failure"); return originalDelete(keys); };
  const responses = await Promise.all([PUT(request("PUT", 0)), PUT(request("PUT", 0))]);
  assert.deepEqual(responses.map(x => x.status), [200, 200]);
  assert.equal(bucket.listCalls.length, 1);
  assert.ok(bucket.objects.has("upload-parts/old/0"));
  fail = false; now += 61_000;
  assert.equal((await PUT(request("PUT", 0))).status, 200);
  assert.equal(bucket.objects.has("upload-parts/old/0"), false);
});

test("a former cleanup owner cannot overwrite a successor's cursor", async (t) => {
  const { bucket, request } = setup();
  const originalList = bucket.list.bind(bucket);
  bucket.list = async (options) => {
    const page = await originalList(options);
    // Simulate a successor taking over after the first owner's lease expired.
    await bucket.put(CLEANUP_KEY, JSON.stringify({ cursor: "successor", nextRun: Date.now() + 60_000, leaseUntil: 0 }));
    return page;
  };
  assert.equal((await PUT(request("PUT", 0))).status, 200);
  assert.equal(JSON.parse(await (await bucket.get(CLEANUP_KEY)).text()).cursor, "successor");
});

test("a crashed lease and rejected continuation cursor recover without failing uploads", async (t) => {
  const { bucket, request } = setup();
  let now = Date.now(); t.mock.method(Date, "now", () => now);
  await bucket.put(CLEANUP_KEY, JSON.stringify({ cursor: "stale", nextRun: 0, leaseUntil: now + 60_000 }));
  assert.equal((await PUT(request("PUT", 0))).status, 200);
  assert.equal(bucket.listCalls.length, 0);
  now += 61_000;
  const originalList = bucket.list.bind(bucket);
  bucket.list = async (options) => {
    if (options.cursor === "stale") throw new Error("invalid provider cursor");
    return originalList(options);
  };
  assert.equal((await PUT(request("PUT", 0))).status, 200);
  assert.equal(JSON.parse(await (await bucket.get(CLEANUP_KEY)).text()).cursor, null);
  now += 61_000;
  assert.equal((await PUT(request("PUT", 0))).status, 200);
  assert.equal(bucket.listCalls.length, 1);
});
