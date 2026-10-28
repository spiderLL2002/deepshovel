import assert from "node:assert/strict";
import test from "node:test";
import { setImmediate } from "node:timers/promises";
import { watchDataQuality } from "../app/data-quality-polling.ts";

test("data checks keep polling unchanged running status and stop at completion", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const results = [];
  let calls = 0;
  t.mock.method(globalThis, "fetch", async (url) => {
    assert.equal(url, "/api/data-quality?artifact_version_id=version%3Aone");
    calls += 1;
    return Response.json({ job: { status: calls < 3 ? "running" : "completed", progress: calls * 30 }, report: null });
  });
  const watching = watchDataQuality("version:one", new AbortController().signal, (result) => results.push(result));
  await setImmediate();
  assert.equal(calls, 1);
  t.mock.timers.tick(1_800);
  await setImmediate();
  assert.equal(calls, 2);
  t.mock.timers.tick(1_800);
  await watching;
  t.mock.timers.tick(10_000);
  assert.equal(calls, 3);
  assert.deepEqual(results.map((result) => result.job.status), ["running", "running", "completed"]);
});

test("changing the selected version discards a late response even when fetch ignores abort", async (t) => {
  const controller = new AbortController();
  let resolveResponse;
  t.mock.method(globalThis, "fetch", () => new Promise((resolve) => { resolveResponse = resolve; }));
  const watching = watchDataQuality("version:old", controller.signal, () => assert.fail("stale result was published"));
  controller.abort();
  resolveResponse(Response.json({ job: { status: "completed" }, report: { old: true } }));
  await watching;
});

test("new uploads wait for job creation and abort releases a pending polling timer", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const controller = new AbortController();
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => {
    calls += 1;
    return Response.json({}, { status: 404 });
  });
  const watching = watchDataQuality("version:new", controller.signal, (result) => assert.equal(result.job, null), true);
  await setImmediate();
  t.mock.timers.tick(1_800);
  await setImmediate();
  assert.equal(calls, 2);
  controller.abort();
  await watching;
  t.mock.timers.tick(10_000);
  assert.equal(calls, 2);
});
