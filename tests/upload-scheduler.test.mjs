import assert from "node:assert/strict";
import test from "node:test";
import { consumeUploadSelection, fetchUploadWithRetry, uploadArtifact, UploadScheduler } from "../app/upload-scheduler.ts";

const deferred = () => {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
};

test("upload selection is copied before clearing the live file list", () => {
  const liveSelection = ["paper-one.pdf", "paper-two.pdf"];
  const selected = consumeUploadSelection(liveSelection, () => liveSelection.splice(0));
  assert.deepEqual(selected, ["paper-one.pdf", "paper-two.pdf"]);
  assert.deepEqual(liveSelection, []);
});

test("upload retry is bounded to transient responses and network failures", async () => {
  const sleeps = [];
  let calls = 0;
  const response = await fetchUploadWithRetry(async () => {
    calls += 1;
    if (calls === 1) throw new TypeError("temporary network failure");
    return calls === 2
      ? new Response(null, { status: 503, headers: { "retry-after": "6" } })
      : Response.json({ ok: true });
  }, new AbortController().signal, async (delayMs) => sleeps.push(delayMs));

  assert.equal(response.status, 200);
  assert.equal(calls, 3);
  assert.deepEqual(sleeps, [2_000, 5_000]);
});

test("upload retry caps an excessive Retry-After value", async () => {
  const sleeps = [];
  let calls = 0;
  const response = await fetchUploadWithRetry(async () => {
    calls += 1;
    return calls === 1
      ? new Response(null, { status: 429, headers: { "retry-after": "120" } })
      : Response.json({ ok: true });
  }, new AbortController().signal, async (delayMs) => sleeps.push(delayMs));

  assert.equal(response.status, 200);
  assert.deepEqual(sleeps, [5_000]);
});

test("upload retry accepts a future Retry-After date and ignores invalid values", async () => {
  const sleeps = [];
  let calls = 0;
  const future = new Date(Date.now() + 4_000).toUTCString();
  const response = await fetchUploadWithRetry(async () => {
    calls += 1;
    if (calls === 1) return new Response(null, { status: 503, headers: { "retry-after": future } });
    if (calls === 2) return new Response(null, { status: 503, headers: { "retry-after": "1.5" } });
    return Response.json({ ok: true });
  }, new AbortController().signal, async (delayMs) => sleeps.push(delayMs));

  assert.equal(response.status, 200);
  assert.ok(sleeps[0] >= 2_000 && sleeps[0] <= 5_000);
  assert.equal(sleeps[1], 5_000);
});

test("upload retry can be cancelled while waiting", async () => {
  const controller = new AbortController();
  const waiting = fetchUploadWithRetry(
    async () => new Response(null, { status: 503 }),
    controller.signal,
  );
  controller.abort();
  await assert.rejects(waiting, { name: "AbortError" });
});

test("upload retry does not replay ordinary 4xx responses", async () => {
  let calls = 0;
  const response = await fetchUploadWithRetry(async () => {
    calls += 1;
    return Response.json({ error: { code: "INVALID_FILE" } }, { status: 400 });
  }, new AbortController().signal, async () => assert.fail("must not sleep"));

  assert.equal(response.status, 400);
  assert.equal(calls, 1);
});

test("upload retry stops immediately when the user cancels", async () => {
  const controller = new AbortController();
  let calls = 0;
  await assert.rejects(fetchUploadWithRetry(async () => {
    calls += 1;
    controller.abort();
    throw new TypeError("cancelled request");
  }, controller.signal, async () => assert.fail("must not sleep")), { name: "AbortError" });
  assert.equal(calls, 1);
});

test("upload scheduler applies one global limit across batches", async () => {
  const scheduler = new UploadScheduler(2);
  const gates = Array.from({ length: 4 }, deferred);
  let active = 0;
  let peak = 0;
  const run = (index) => scheduler.run(new AbortController().signal, async () => {
    active += 1;
    peak = Math.max(peak, active);
    await gates[index].promise;
    active -= 1;
  });

  const firstBatch = [run(0), run(1), run(2)];
  const secondBatch = [run(3)];
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(active, 2);
  assert.equal(peak, 2);

  gates[0].resolve();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(active, 2);
  gates[1].resolve();
  gates[2].resolve();
  gates[3].resolve();
  await Promise.all([...firstBatch, ...secondBatch]);
  assert.equal(peak, 2);
});

test("cancelling a queued upload never consumes a slot", async () => {
  const scheduler = new UploadScheduler(1);
  const gate = deferred();
  const first = scheduler.run(new AbortController().signal, () => gate.promise);
  const queuedController = new AbortController();
  const queued = scheduler.run(queuedController.signal, async () => assert.fail("cancelled operation ran"));
  queuedController.abort();
  await assert.rejects(queued, { name: "AbortError" });
  gate.resolve();
  await first;
});

test("uploads for one artifact stay ordered across appended batches without blocking other artifacts", async () => {
  const scheduler = new UploadScheduler(2);
  const firstGate = deferred();
  const secondGate = deferred();
  const unrelatedGate = deferred();
  const started = [];

  const firstVersion = scheduler.run(new AbortController().signal, async () => {
    started.push("version-1");
    await firstGate.promise;
  }, "artifact:one");
  await new Promise((resolve) => setImmediate(resolve));

  const secondVersion = scheduler.run(new AbortController().signal, async () => {
    started.push("version-2");
    await secondGate.promise;
  }, "artifact:one");
  const unrelated = scheduler.run(new AbortController().signal, async () => {
    started.push("unrelated");
    await unrelatedGate.promise;
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(started, ["version-1", "unrelated"]);
  unrelatedGate.resolve();
  await unrelated;
  assert.deepEqual(started, ["version-1", "unrelated"]);

  firstGate.resolve();
  await firstVersion;
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(started, ["version-1", "unrelated", "version-2"]);
  secondGate.resolve();
  await secondVersion;
});


test("artifact uploads preserve bytes, version target and one idempotency key across retries", async (t) => {
  const requests = [];
  const version = { id: "version:one", artifactId: "artifact:one", fileName: "évidence.pdf" };
  const file = new File(["%PDF-1.7"], "e\u0301vidence.pdf");
  const controller = new AbortController();
  t.mock.method(globalThis, "fetch", async (url, options) => {
    requests.push({ url, ...options });
    return requests.length === 1 ? new Response(null, { status: 503 }) : Response.json({ version });
  });

  assert.deepEqual(await uploadArtifact(file, controller.signal, { artifactId: "artifact:one" }), version);
  assert.equal(requests.length, 2);
  const key = requests[0].headers["idempotency-key"];
  assert.ok(key);
  for (const request of requests) {
    assert.equal(request.url, "/api/artifacts");
    assert.equal(request.body, file);
    assert.equal(request.signal, controller.signal);
    assert.equal(request.headers["content-type"], "application/pdf");
    assert.equal(request.headers["x-artifact-name"], encodeURIComponent("évidence.pdf"));
    assert.equal(request.headers["x-artifact-id"], "artifact:one");
    assert.equal(request.headers["idempotency-key"], key);
  }
});

test("large uploads send bounded parts and finalize only after all parts have arrived", async () => {
  const original = globalThis.fetch;
  const bytes = new Uint8Array(600_000).fill(65);
  const received = new Map();
  let identity;
  globalThis.fetch = async (url, init) => {
    assert.equal(url, "/api/artifacts/upload");
    identity ??= init.headers["x-upload-id"];
    assert.equal(init.headers["x-upload-id"], identity);
    if (init.method === "PUT") {
      const part = new Uint8Array(await init.body.arrayBuffer());
      assert.ok(part.length <= 256 * 1024);
      received.set(Number(init.headers["x-upload-index"]), part);
      return Response.json({ ok: true });
    }
    assert.equal(init.method, "POST");
    assert.equal(received.size, 3);
    const joined = Buffer.concat([...received].sort((a, b) => a[0] - b[0]).map(([, part]) => part));
    assert.deepEqual(new Uint8Array(joined), bytes);
    return Response.json({ ok: true, version: { versionId: "uploaded" } });
  };
  try {
    const result = await uploadArtifact(new File([bytes], "paper.pdf", { type: "application/pdf" }), new AbortController().signal);
    assert.equal(result.versionId, "uploaded");
  } finally { globalThis.fetch = original; }
});
