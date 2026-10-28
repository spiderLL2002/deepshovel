import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

const bindings = { DB: null, BUCKET: null };
globalThis.__artifactDownloadTestBindings = bindings;

const storeStub = `
export const PROJECT_ID = "DEEPSHOVEL-PROJECT";
export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message); this.name = "ApiError"; this.status = status; this.code = code; this.details = details;
  }
}
export async function getBindings() { return globalThis.__artifactDownloadTestBindings; }
export function safeError(error) {
  if (error instanceof ApiError) {
    return Response.json({ ok: false, error: { code: error.code, message: error.message } }, { status: error.status });
  }
  return Response.json({ ok: false, error: { code: "INTERNAL_ERROR", message: String(error) } }, { status: 500 });
}`;

const loader = `
let rootUrl; let storeUrl;
export function initialize(data) { rootUrl = data.rootUrl; storeUrl = data.storeUrl; }
export async function resolve(specifier, context, nextResolve) {
  if (specifier === "@/lib/server/evidence-store") return { url: storeUrl, shortCircuit: true };
  if (specifier.startsWith("@/")) {
    let path = specifier.slice(2);
    if (!/\\.[cm]?[jt]sx?$/.test(path)) path += ".ts";
    return { url: new URL(path, rootUrl).href, shortCircuit: true };
  }
  return nextResolve(specifier, context);
}`;
register(`data:text/javascript,${encodeURIComponent(loader)}`, {
  parentURL: import.meta.url,
  data: {
    rootUrl: new URL("../", import.meta.url).href,
    storeUrl: `data:text/javascript,${encodeURIComponent(storeStub)}`,
  },
});

const { GET } = await import("../app/api/artifacts/[sha256]/route.ts");
const sha256 = "a".repeat(64);
const pdfBytes = new TextEncoder().encode("%PDF-1.7\n0123456789abcdefghijklmnopqrstuvwxyz");

class Statement {
  constructor(row) { this.row = row; }
  bind() { return this; }
  async first() { return this.row; }
}

class MemoryDB {
  constructor(row) { this.row = row; }
  prepare() { return new Statement(this.row); }
}

class MemoryR2 {
  constructor(bytes = pdfBytes) {
    this.bytes = bytes;
    this.gets = [];
    this.heads = [];
  }

  async head(key) {
    this.heads.push(key);
    return { size: this.bytes.byteLength, httpEtag: '"fixture-etag"' };
  }

  async get(key, options) {
    this.gets.push({ key, options });
    const range = options?.range;
    const body = range
      ? this.bytes.slice(range.offset, range.offset + range.length)
      : this.bytes;
    return {
      body,
      size: this.bytes.byteLength,
      httpEtag: '"fixture-etag"',
    };
  }
}

function fixture(mimeType = "application/pdf", bytes = pdfBytes) {
  const bucket = new MemoryR2(bytes);
  bindings.DB = new MemoryDB({
    fileName: "对虹霓视角理论的实验检验.pdf",
    mimeType,
    objectKey: `sha256/${sha256}`,
  });
  bindings.BUCKET = bucket;
  return bucket;
}

function request(path = "", headers) {
  return GET(new Request(`http://deepshovel.test/api/artifacts/${sha256}${path}`, { headers }), {
    params: Promise.resolve({ sha256 }),
  });
}

test("ordinary artifact requests remain full attachment downloads", async () => {
  const bucket = fixture();
  const response = await request();

  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-disposition"), /^attachment;/);
  assert.match(response.headers.get("content-disposition"), /filename\*=UTF-8''%E5%AF%B9%E8%99%B9%E9%9C%93/u);
  assert.equal(response.headers.get("content-length"), String(pdfBytes.byteLength));
  assert.equal(response.headers.get("accept-ranges"), "bytes");
  assert.equal(response.headers.get("cross-origin-resource-policy"), "same-origin");
  assert.deepEqual(new Uint8Array(await response.arrayBuffer()), pdfBytes);
  assert.equal(bucket.heads.length, 0);
  assert.deepEqual(bucket.gets, [{ key: `sha256/${sha256}`, options: undefined }]);
});

test("explicit PDF preview returns a same-origin inline representation", async () => {
  fixture();
  const response = await request("?disposition=inline");

  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-disposition"), /^inline;/);
  assert.equal(response.headers.get("content-type"), "application/pdf");
  assert.equal(response.headers.get("location"), null);
  assert.equal(response.headers.get("content-security-policy"), "frame-ancestors 'self'");
});

test("one valid byte range is fetched from R2 and returned as 206", async () => {
  const bucket = fixture();
  const response = await request("?disposition=inline", { range: "bytes=9-18" });

  assert.equal(response.status, 206);
  assert.equal(response.headers.get("content-range"), `bytes 9-18/${pdfBytes.byteLength}`);
  assert.equal(response.headers.get("content-length"), "10");
  assert.equal(new TextDecoder().decode(await response.arrayBuffer()), "0123456789");
  assert.deepEqual(bucket.gets, [{
    key: `sha256/${sha256}`,
    options: { range: { offset: 9, length: 10 } },
  }]);
});

test("open-ended, suffix, and oversized-end ranges normalize exactly", async () => {
  const bytes = new TextEncoder().encode("0123456789");
  const cases = [
    ["bytes=7-", "789", "bytes 7-9/10"],
    ["bytes=-4", "6789", "bytes 6-9/10"],
    ["bytes=-40", "0123456789", "bytes 0-9/10"],
    ["bytes=7-40", "789", "bytes 7-9/10"],
  ];
  for (const [range, expectedBody, expectedContentRange] of cases) {
    fixture("application/pdf", bytes);
    const response = await request("?disposition=inline", { range });
    assert.equal(response.status, 206, range);
    assert.equal(response.headers.get("content-range"), expectedContentRange, range);
    assert.equal(await response.text(), expectedBody, range);
  }
});

test("malformed, multipart, and unsatisfiable ranges return 416 without reading bytes", async () => {
  for (const range of ["bytes=", "bytes=9-2", "bytes=999-", "bytes=-0", "bytes=0-1,4-5", "items=0-1"]) {
    const bucket = fixture();
    const response = await request("?disposition=inline", { range });
    assert.equal(response.status, 416, range);
    assert.equal(response.headers.get("content-range"), `bytes */${pdfBytes.byteLength}`, range);
    assert.equal(await response.text(), "", range);
    assert.equal(bucket.gets.length, 0, range);
    assert.equal(bucket.heads.length, 1, range);
  }
});

test("inline mode is exact and cannot expose non-PDF artifacts", async () => {
  fixture("image/png");
  const unsupported = await request("?disposition=inline");
  assert.equal(unsupported.status, 415);
  assert.equal((await unsupported.json()).error.code, "ARTIFACT_INLINE_UNSUPPORTED");

  fixture();
  const invalid = await request("?disposition=download");
  assert.equal(invalid.status, 400);
  assert.equal((await invalid.json()).error.code, "INVALID_ARTIFACT_DISPOSITION");
});
