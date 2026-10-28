import assert from "node:assert/strict";
import test from "node:test";
import { containsCredential, readBoundedJson } from "../lib/server/service-response.ts";

const failure = (kind) => new Error(kind);

test("service JSON supports chunked multibyte text and releases the reader", async () => {
  const bytes = new TextEncoder().encode('{"title":"证据"}');
  const response = new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(bytes.slice(0, 12));
      controller.enqueue(bytes.slice(12));
      controller.close();
    },
  }), { headers: { "content-type": "application/problem+json" } });
  assert.deepEqual(await readBoundedJson(response, bytes.length, failure), { title: "证据" });
  assert.equal(response.body.locked, false);
});

test("service JSON cancels oversized streams with or without Content-Length", async () => {
  for (const headers of [{}, { "content-length": "1000" }]) {
    let cancelled = false;
    const response = new Response(new ReadableStream({
      pull(controller) { controller.enqueue(new Uint8Array(10)); },
      cancel() { cancelled = true; },
    }), { headers: { "content-type": "application/json", ...headers } });
    await assert.rejects(readBoundedJson(response, 5, failure), /too_large/);
    assert.equal(cancelled, true);
    assert.equal(response.body.locked, false);
  }
});

test("service JSON rejects malformed UTF-8 and invalid response headers", async () => {
  await assert.rejects(readBoundedJson(new Response(new Uint8Array([0xff]), {
    headers: { "content-type": "application/json" },
  }), 100, failure), /json/);
  for (const headers of [{ "content-type": "text/html" }, { "content-type": "application/json", "content-length": "-1" }]) {
    await assert.rejects(readBoundedJson(new Response("{}", { headers }), 100, failure), /invalid/);
  }
});

test("credential screening traverses both nested keys and values", () => {
  assert.equal(containsCredential({ rows: [{ detail: "Bearer private-token" }] }, "private-token"), true);
  assert.equal(containsCredential({ rows: [{ "private-token": false }] }, "private-token"), true);
  assert.equal(containsCredential({ rows: [{ detail: "public" }] }, "private-token"), false);
});
