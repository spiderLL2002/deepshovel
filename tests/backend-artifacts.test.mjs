import assert from "node:assert/strict";
import { AsyncLocalStorage } from "node:async_hooks";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const sourceUrl = new URL("../lib/server/evidence-store.ts", import.meta.url);
const source = await readFile(sourceUrl, "utf8");
const transpiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText.replace(
  '"../artifacts/media-types.ts"',
  JSON.stringify(new URL("../lib/artifacts/media-types.ts", import.meta.url).href),
);
const store = await import(
  `data:text/javascript;base64,${Buffer.from(transpiled).toString("base64")}`
);

function utf8(value) {
  return new TextEncoder().encode(value);
}

function expectApiError(fn, code) {
  assert.throws(fn, (error) => {
    assert.equal(error.name, "ApiError");
    assert.equal(error.code, code);
    return true;
  });
}

class MemoryR2Bucket {
  objects = new Map();
  puts = [];

  constructor(requestScope = null) {
    this.requestScope = requestScope;
  }

  requestBound(object) {
    if (!this.requestScope) return object;
    const owner = this.requestScope.getStore();
    return new Proxy(object, {
      get: (target, property, receiver) => {
        if (this.requestScope.getStore() !== owner) {
          throw new Error("Cannot perform I/O on behalf of a different request");
        }
        return Reflect.get(target, property, receiver);
      },
    });
  }

  async head(key) {
    const object = this.objects.get(key);
    return object ? this.requestBound(object) : null;
  }

  async put(key, bytes, options) {
    // Yield before the create-only check so both callers race deterministically.
    await Promise.resolve();
    const onlyIf = options?.onlyIf instanceof Headers ? options.onlyIf : new Headers();
    if (onlyIf.get("if-none-match") === "*" && this.objects.has(key)) return null;
    const copy = new Uint8Array(bytes);
    const object = {
      key,
      size: copy.byteLength,
      etag: `etag-${this.puts.length + 1}`,
      httpEtag: `"etag-${this.puts.length + 1}"`,
      uploaded: new Date(0),
      httpMetadata: options?.httpMetadata,
      customMetadata: options?.customMetadata,
    };
    this.puts.push({ key, bytes: copy, options });
    this.objects.set(key, object);
    return this.requestBound(object);
  }
}

test("artifact validation checks declared type against bytes and extension", () => {
  const validPdf = utf8("%PDF-1.7\nminimal fixture");
  assert.equal(
    store.validateArtifactBytes("paper.pdf", "application/pdf", validPdf),
    "paper.pdf",
  );

  expectApiError(
    () => store.validateArtifactBytes("paper.pdf", "application/pdf", utf8("not a pdf")),
    "CONTENT_SIGNATURE_MISMATCH",
  );
  expectApiError(
    () => store.validateArtifactBytes("paper.txt", "application/pdf", validPdf),
    "FILE_EXTENSION_MISMATCH",
  );
});

test("text artifacts must be UTF-8 and cannot disguise known binary bytes", () => {
  assert.equal(
    store.validateArtifactBytes("table.csv", "text/csv", utf8("sample,value\nA,1\n")),
    "table.csv",
  );

  expectApiError(
    () => store.validateArtifactBytes("table.csv", "text/csv", new Uint8Array([0xc3, 0x28])),
    "INVALID_UTF8",
  );
  expectApiError(
    () => store.validateArtifactBytes("table.csv", "text/csv", utf8("%PDF-1.7\n")),
    "CONTENT_SIGNATURE_MISMATCH",
  );
});

test("server SHA-256, not a client identifier, determines artifact-version identity", async () => {
  const bytes = utf8("abc");
  const digest = await store.sha256Hex(bytes);
  assert.equal(
    digest,
    "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
  );
  assert.equal(
    `artifact-version:sha256:${digest}`,
    `artifact-version:sha256:${await store.sha256Hex(bytes)}`,
  );
  const backing = utf8("prefixabcsuffix");
  assert.equal(await store.sha256Hex(backing.subarray(6, 9)), digest);
});

test("bounded request reads preallocate declared bodies and reject length mismatches", async () => {
  const streamed = new ReadableStream({
    start(controller) {
      controller.enqueue(utf8("abc"));
      controller.enqueue(utf8("def"));
      controller.close();
    },
  });
  const request = new Request("http://localhost/upload", {
    method: "POST",
    headers: { "content-length": "6" },
    body: streamed,
    duplex: "half",
  });
  assert.deepEqual(await store.readBoundedBody(request, 10), utf8("abcdef"));

  const short = new Request("http://localhost/upload", {
    method: "POST",
    headers: { "content-length": "4" },
    body: "abc",
  });
  await assert.rejects(
    () => store.readBoundedBody(short, 10),
    (error) => error.code === "CONTENT_LENGTH_MISMATCH",
  );
});

test("media types map to stable scientific artifact classes", () => {
  assert.equal(store.artifactTypeForMime("application/pdf"), "paper");
  assert.equal(store.artifactTypeForMime("image/png"), "figure");
  assert.equal(store.artifactTypeForMime("text/csv"), "table");
  assert.equal(
    store.artifactTypeForMime("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "dataset",
  );
});

test("same-origin writes survive a loopback reverse proxy without admitting cross-site requests", () => {
  assert.doesNotThrow(() => store.assertSameOrigin(new Request("http://127.0.0.1:8100/api/artifacts", {
    headers: {
      origin: "https://deepshovel.iiot-shxt.xyz",
      "sec-fetch-site": "same-origin",
    },
  })));
  assert.doesNotThrow(() => store.assertSameOrigin(new Request("https://deepshovel.iiot-shxt.xyz/api/artifacts", {
    headers: { origin: "https://deepshovel.iiot-shxt.xyz" },
  })));
  expectApiError(
    () => store.assertSameOrigin(new Request("http://127.0.0.1:8100/api/artifacts", {
      headers: { origin: "https://attacker.example", "sec-fetch-site": "cross-site" },
    })),
    "CROSS_ORIGIN_WRITE_REJECTED",
  );
  expectApiError(
    () => store.assertSameOrigin(new Request("http://127.0.0.1:8100/api/artifacts", {
      headers: { origin: "https://attacker.example" },
    })),
    "CROSS_ORIGIN_WRITE_REJECTED",
  );
});

test("content-addressed R2 writes deduplicate identical bytes at the digest key", async () => {
  const bucket = new MemoryR2Bucket();
  const bytes = utf8("sample,value\nA,b.d.l.\n");
  const digest = await store.sha256Hex(bytes);

  const first = await store.putContentAddressedObject(bucket, digest, bytes, "text/csv");
  const replay = await store.putContentAddressedObject(bucket, digest, bytes, "text/csv");

  assert.equal(first.stored, true);
  assert.equal(replay.stored, false);
  assert.equal(first.object.key, `artifacts/${digest}`);
  assert.equal(replay.object.key, first.object.key);
  assert.equal(bucket.puts.length, 1);
  assert.equal(bucket.puts[0].options.customMetadata.sha256, digest);
});

test("concurrent content-addressed writes use one atomic create-only R2 put", async () => {
  const bucket = new MemoryR2Bucket();
  const bytes = utf8("same immutable bytes");
  const digest = await store.sha256Hex(bytes);

  const [left, right] = await Promise.all([
    store.putContentAddressedObject(bucket, digest, bytes, "text/plain"),
    store.putContentAddressedObject(bucket, digest, bytes, "text/plain"),
  ]);

  assert.equal(bucket.puts.length, 1);
  assert.deepEqual([left.stored, right.stored].sort(), [false, true]);
  assert.equal(left.object.key, right.object.key);
});

test("concurrent uploads never share request-bound R2 objects across actors", async () => {
  const requestScope = new AsyncLocalStorage();
  const bucket = new MemoryR2Bucket(requestScope);
  const bytes = utf8("same request-bound immutable bytes");
  const digest = await store.sha256Hex(bytes);
  const upload = (actor) => requestScope.run(actor, async () => {
    const result = await store.putContentAddressedObject(bucket, digest, bytes, "text/plain");
    return { stored: result.stored, etag: result.object.etag };
  });

  const results = await Promise.all([upload("request:left"), upload("request:right")]);

  assert.equal(bucket.puts.length, 1);
  assert.deepEqual(results.map(({ stored }) => stored).sort(), [false, true]);
  assert.ok(results.every(({ etag }) => etag === "etag-1"));
});

test("safe errors classify nested transient D1 failures without exposing raw causes", async () => {
  const locked = new Error("D1 query failed", {
    cause: new Error("D1_ERROR: database is locked: SQLITE_BUSY"),
  });
  const originalWarn = console.warn;
  console.warn = () => {};
  let busy;
  let unavailable;
  try {
    busy = store.safeError(locked);
    unavailable = store.safeError(new Error("D1 request failed", {
      cause: new Error("Network connection lost."),
    }));
  } finally {
    console.warn = originalWarn;
  }

  const busyBody = await busy.json();
  assert.equal(busy.status, 503);
  assert.equal(busy.headers.get("retry-after"), "1");
  assert.equal(busyBody.error.code, "STORE_BUSY");
  assert.equal(busyBody.error.details.retryable, true);
  assert.doesNotMatch(JSON.stringify(busyBody), /sqlite|database is locked/i);

  const unavailableBody = await unavailable.json();
  assert.equal(unavailable.status, 503);
  assert.equal(unavailable.headers.get("retry-after"), "2");
  assert.equal(unavailableBody.error.code, "STORE_UNAVAILABLE");
  assert.equal(unavailableBody.error.details.retryable, true);
  assert.doesNotMatch(JSON.stringify(unavailableBody), /network connection lost/i);
});

test("safe errors distinguish request-context defects and sanitize unknown logging", async () => {
  const originalError = console.error;
  const logs = [];
  console.error = (...values) => { logs.push(values); };
  let contextResponse;
  let unknownResponse;
  try {
    contextResponse = store.safeError(new Error(
      "Cannot perform I/O on behalf of a different request: private detail",
    ));
    unknownResponse = store.safeError(new Error("private scientific input must not be logged"));
  } finally {
    console.error = originalError;
  }

  const contextBody = await contextResponse.json();
  assert.equal(contextResponse.status, 500);
  assert.equal(contextBody.error.code, "STORE_REQUEST_CONTEXT_INVALID");
  assert.equal(contextBody.error.details.retryable, false);

  const unknownBody = await unknownResponse.json();
  assert.equal(unknownResponse.status, 500);
  assert.equal(unknownBody.error.code, "INTERNAL_ERROR");
  assert.equal(unknownBody.error.details.retryable, false);
  assert.doesNotMatch(JSON.stringify({ logs, contextBody, unknownBody }), /private scientific input|private detail/i);
});
