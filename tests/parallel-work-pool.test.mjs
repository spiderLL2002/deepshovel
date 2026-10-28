import assert from "node:assert/strict";
import test from "node:test";

import {
  KNOWLEDGE_GRAPH_BATCH_CONCURRENCY,
  KNOWLEDGE_GRAPH_MAX_RETRIES,
  runParallelWorkPool,
  runWithRetries,
} from "../app/parallel-work-pool.ts";

test("claim processing retries every failure at most three times", async () => {
  assert.equal(KNOWLEDGE_GRAPH_MAX_RETRIES, 3);
  let calls = 0;
  const retries = [];
  const result = await runWithRetries(
    async (attempt) => {
      calls += 1;
      if (attempt < 4) throw new Error(`failure ${attempt}`);
      return "processed";
    },
    {
      retryDelayMs: 0,
      onRetry: (retryCount, error) => retries.push([retryCount, error.message]),
    },
  );

  assert.equal(result, "processed");
  assert.equal(calls, 4);
  assert.deepEqual(retries, [
    [1, "failure 1"],
    [2, "failure 2"],
    [3, "failure 3"],
  ]);
});

test("claim processing exposes the final failure after three retries", async () => {
  let calls = 0;
  await assert.rejects(
    () => runWithRetries(
      async () => {
        calls += 1;
        throw new Error(`failure ${calls}`);
      },
      { retryDelayMs: 0 },
    ),
    /failure 4/,
  );
  assert.equal(calls, 4);
});

test("claim processing abort prevents further retries", async () => {
  const controller = new AbortController();
  let calls = 0;
  await assert.rejects(
    () => runWithRetries(
      async () => {
        calls += 1;
        controller.abort();
        throw new Error("cancelled request");
      },
      { retryDelayMs: 0, signal: controller.signal },
    ),
    /cancelled request/,
  );
  assert.equal(calls, 1);
});

test("knowledge-graph batch work starts sixty-four Claims concurrently", async () => {
  assert.equal(KNOWLEDGE_GRAPH_BATCH_CONCURRENCY, 64);
  let active = 0;
  let maximumActive = 0;
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  let sixtyFourStartedResolve;
  const sixtyFourStarted = new Promise((resolve) => { sixtyFourStartedResolve = resolve; });
  const processed = [];

  const running = runParallelWorkPool(
    Array.from({ length: 80 }, (_, index) => index),
    KNOWLEDGE_GRAPH_BATCH_CONCURRENCY,
    async (item) => {
      active += 1;
      maximumActive = Math.max(maximumActive, active);
      if (maximumActive === 64) sixtyFourStartedResolve();
      await gate;
      processed.push(item);
      active -= 1;
    },
  );

  await Promise.race([
    sixtyFourStarted,
    new Promise((_, reject) => setTimeout(() => reject(new Error("64 workers did not start")), 1_000)),
  ]);
  assert.equal(active, 64);
  release();
  await running;
  assert.equal(maximumActive, 64);
  assert.deepEqual(processed.sort((left, right) => left - right), Array.from({ length: 80 }, (_, index) => index));
});

test("parallel work pool rejects invalid concurrency", async () => {
  await assert.rejects(() => runParallelWorkPool([1], 0, async () => {}), RangeError);
});
