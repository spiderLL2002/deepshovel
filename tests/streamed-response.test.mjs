import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

const storeStub = `
export class ApiError extends Error { constructor(status, code, message) { super(message); this.status = status; this.code = code; } }
export function safeError(error) {
  return Response.json({ ok: false, error: { code: error?.code ?? "INTERNAL_ERROR" } },
    { status: error?.status ?? 500, headers: { "retry-after": "2" } });
}
`;
const loader = `
let storeUrl;
export function initialize(data) { ({ storeUrl } = data); }
export async function resolve(specifier, context, nextResolve) {
  if (specifier === "@/lib/server/evidence-store") return { url: storeUrl, shortCircuit: true };
  return nextResolve(specifier, context);
}`;
const storeUrl = `data:text/javascript,${encodeURIComponent(storeStub)}`;
register(`data:text/javascript,${encodeURIComponent(loader)}`, { parentURL: import.meta.url, data: { storeUrl } });

const { streamedResponse, wantsStreamedResponse } = await import("../lib/server/streamed-response.ts");
const { fetchStreamed, STREAM_INTERRUPTED_MESSAGE, STREAM_INVALID_MESSAGE } = await import("../app/streamed-fetch.ts");
const { ApiError } = await import(storeUrl);

const post = (headers = {}) => new Request("https://deepshovel.test/api/x", { method: "POST", headers, body: "{}" });
const lines = async (response) => (await response.text()).trim().split("\n").map((line) => JSON.parse(line));

test("a streamed answer starts at once, keeps the connection alive, then carries the JSON answer", async (t) => {
  let release;
  const held = new Promise(resolve => { release = resolve; });
  t.after(() => release());
  const response = streamedResponse(post({ accept: "application/x-ndjson" }), async (request) => {
    assert.equal(await request.text(), "{}");
    await held;
    return Response.json({ ok: true, value: 1 }, { status: 202 });
  }, 10);
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type"), /^application\/x-ndjson/u);
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  assert.deepEqual(JSON.parse((await reader.read()).value), { type: "started" });
  for (let i = 0; i < 2; i++) assert.deepEqual(JSON.parse((await reader.read()).value), { type: "heartbeat" });
  release();
  assert.deepEqual(JSON.parse((await reader.read()).value), { type: "result", status: 202, retryAfter: null, body: { ok: true, value: 1 } });
  assert.equal((await reader.read()).done, true);
  reader.releaseLock();
});

test("a failure behind the stream becomes the route's error status and body", async () => {
  const response = streamedResponse(post(), async () => {
    throw new ApiError(409, "QUERY_PLAN_HANDOFF_REPREVIEW_REQUIRED", "stale");
  }, 10);
  assert.deepEqual((await lines(response)).at(-1), {
    type: "result", status: 409, retryAfter: "2", body: { ok: false, error: { code: "QUERY_PLAN_HANDOFF_REPREVIEW_REQUIRED" } },
  });
});

test("a framework Request facade keeps its body without native Request reconstruction", async () => {
  const original = post();
  const facade = {
    url: original.url,
    headers: original.headers,
    signal: original.signal,
    json() {
      assert.equal(this, facade, "framework methods retain their receiver");
      return original.json();
    },
    toString: () => "[object Request]",
  };
  assert.throws(() => new Request(facade), /URL/);
  const response = streamedResponse(facade, async request => {
    assert.deepEqual(await request.json(), {});
    assert.notEqual(request.signal, original.signal);
    return Response.json({ ok: true });
  });
  assert.deepEqual((await lines(response)).at(-1).body, { ok: true });
});

test("a client that disconnects aborts the work behind the stream", async () => {
  let signal;
  const response = streamedResponse(post(), (request) => {
    signal = request.signal;
    return new Promise(() => {});
  }, 10);
  const reader = response.body.getReader();
  assert.equal(JSON.parse(new TextDecoder().decode((await reader.read()).value)).type, "started");
  await reader.cancel("closed by the browser");
  assert.equal(signal.aborted, true);
});

test("only an explicit NDJSON accept header selects the stream", () => {
  assert.equal(wantsStreamedResponse(post({ accept: "application/json, application/x-ndjson;q=0.9" })), true);
  assert.equal(wantsStreamedResponse(post({ accept: "application/json" })), false);
  assert.equal(wantsStreamedResponse(post({ accept: "application/x-ndjson;q=0" })), false);
  assert.equal(wantsStreamedResponse(post()), false);
});

test("the browser helper turns the result line back into an ordinary response", async (t) => {
  const original = globalThis.fetch;
  t.after(() => { globalThis.fetch = original; });
  let accept;
  globalThis.fetch = async (_input, init) => {
    accept = new Headers(init.headers).get("accept");
    return streamedResponse(post(), async () => Response.json(
      { ok: false, error: { code: "RETRIEVAL_RELATION_UNAVAILABLE" } },
      { status: 503, headers: { "retry-after": "5" } },
    ), 10);
  };
  const response = await fetchStreamed("/api/x", { method: "POST", headers: { "content-type": "application/json" }, body: "{}" });
  assert.equal(accept, "application/x-ndjson");
  assert.equal(response.status, 503);
  assert.equal(response.headers.get("retry-after"), "5");
  assert.deepEqual(await response.json(), { ok: false, error: { code: "RETRIEVAL_RELATION_UNAVAILABLE" } });

  // A failure before the stream starts (for example at the proxy) passes through.
  globalThis.fetch = async () => Response.json({ ok: false }, { status: 403 });
  assert.equal((await fetchStreamed("/api/x", { method: "POST" })).status, 403);

  globalThis.fetch = async () => new Response('{"type":"started"}\n{"type":"heartbeat"}\n', {
    headers: { "content-type": "application/x-ndjson; charset=utf-8" },
  });
  await assert.rejects(fetchStreamed("/api/x", { method: "POST" }), { message: STREAM_INTERRUPTED_MESSAGE });
});

test("request abort stops the upstream work and closes the heartbeat stream", async () => {
  const controller = new AbortController();
  let upstream;
  const request = new Request(post(), { signal: controller.signal });
  const response = streamedResponse(request, async (inner) => {
    upstream = inner.signal;
    await new Promise(resolve => inner.signal.addEventListener("abort", resolve, { once: true }));
    throw inner.signal.reason;
  }, 10);
  const reader = response.body.getReader();
  await reader.read();
  controller.abort();
  await assert.rejects(reader.read(), { name: "AbortError" });
  assert.equal(upstream.aborted, true);
  reader.releaseLock();
});

test("a connection failure before headers offers recovery without replaying the POST", async (t) => {
  const original = globalThis.fetch;
  t.after(() => { globalThis.fetch = original; });
  let calls = 0;
  globalThis.fetch = async () => {
    calls++;
    throw new TypeError("Failed to fetch");
  };
  await assert.rejects(fetchStreamed("/api/x", { method: "POST", body: "{}" }), {
    message: STREAM_INTERRUPTED_MESSAGE,
  });
  assert.equal(calls, 1);

  const controller = new AbortController();
  const reason = new DOMException("Cancelled by the user", "AbortError");
  globalThis.fetch = async () => {
    controller.abort(reason);
    throw new TypeError("Failed to fetch");
  };
  await assert.rejects(
    fetchStreamed("/api/x", { method: "POST", signal: controller.signal }),
    error => error === reason,
  );
});

test("an unreadable route response fails instead of publishing a successful empty answer", async () => {
  const response = streamedResponse(post(), async () => new Response("not JSON"), 10);
  await assert.rejects(response.text());
});

test("browser framing survives split Unicode and rejects malformed or truncated results", async (t) => {
  const original = globalThis.fetch;
  t.after(() => { globalThis.fetch = original; });
  const answer = { ok: true, text: "汞异常 🧪" };
  const encoded = new TextEncoder().encode(
    '{"type":"started"}\n{"type":"heartbeat"}\n'
    + JSON.stringify({ type: "result", status: 200, retryAfter: null, body: answer }),
  );
  globalThis.fetch = async () => new Response(new ReadableStream({
    start(controller) {
      // A one-byte split cuts both Chinese and astral code points.
      for (const byte of encoded) controller.enqueue(new Uint8Array([byte]));
      controller.close();
    },
  }), { headers: { "content-type": "application/x-ndjson" } });
  assert.deepEqual(await (await fetchStreamed("/api/x", {})).json(), answer);
  for (const line of [
    "null\n",
    '{"type":"result","status":200.5,"retryAfter":null,"body":{}}\n',
    '{"type":"result","status":200,"retryAfter":null}\n',
    '{"type":"result"',
  ]) {
    globalThis.fetch = async () => new Response(line, { headers: { "content-type": "application/x-ndjson" } });
    await assert.rejects(fetchStreamed("/api/x", {}), { message: STREAM_INVALID_MESSAGE });
  }
});
