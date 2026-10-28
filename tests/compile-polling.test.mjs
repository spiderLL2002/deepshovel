import assert from "node:assert/strict";
import test from "node:test";

import { compileActivityStatus, CompileSubmissionQueue, nextCompileRunAttempt, submitCompileJob } from "../app/compile-polling.ts";
import { compileErrorMessage } from "../app/workspace-types.ts";

test("a failed Compile run retries at most twice and never retries cancellation", () => {
  const failed = { status: "failed", error: { code: "COMPILER_RESTARTED", message: "restarted", retryable: true } };
  assert.equal(nextCompileRunAttempt(failed, 1), 2);
  assert.equal(nextCompileRunAttempt(failed, 2), 3);
  assert.equal(nextCompileRunAttempt(failed, 3), null);
  assert.equal(nextCompileRunAttempt({ ...failed, error: { code: "COMPILE_CANCELLED", message: "cancelled", retryable: false } }, 1), null);
});

function job(id, status, stage = status) {
  return {
    id,
    artifactVersionId: `version:${id}`,
    status,
    stage,
    progress: status === "completed" ? 100 : 10,
    createdAt: "2026-08-15T00:00:00.000Z",
    updatedAt: "2026-08-15T00:00:00.000Z",
  };
}

test("immediate terminal Compile replies update task activity without polling", () => {
  assert.deepEqual(compileActivityStatus(job("completed-replay", "completed")), {
    state: "done",
    cancellable: false,
    progress: 100,
    stage: undefined,
  });
  assert.deepEqual(compileActivityStatus({
    ...job("failed-replay", "failed"),
    progress: 100,
    error: { code: "MODEL_OUTPUT_INVALID", message: "failed", retryable: false },
  }), {
    state: "failed",
    cancellable: false,
    progress: 100,
    stage: undefined,
  });
  assert.deepEqual(compileActivityStatus({
    ...job("importing", "running", "importing"),
    progress: 97,
  }), {
    state: "working",
    cancellable: false,
    progress: 97,
    stage: "importing",
  });
});

test("Compile submissions are globally serialized and queued work can be invalidated", async () => {
  const queue = new CompileSubmissionQueue();
  const generation = queue.generation();
  let releaseFirst;
  const firstGate = new Promise((resolve) => { releaseFirst = resolve; });
  const started = [];
  let active = 0;
  let peak = 0;
  const run = (label, gate = Promise.resolve()) => queue.run(generation, label, async () => {
    started.push(label);
    active += 1;
    peak = Math.max(peak, active);
    await gate;
    active -= 1;
    return label;
  });

  const first = run("first", firstGate);
  const cancelledBeforeStart = run("second");
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(started, ["first"]);

  queue.cancelPending();
  releaseFirst();
  assert.equal(await first, "first");
  assert.equal(await cancelledBeforeStart, undefined);
  assert.deepEqual(started, ["first"]);
  assert.equal(peak, 1);

  const nextGeneration = queue.generation();
  assert.equal(await queue.run(nextGeneration, "third", async () => "third"), "third");
});

test("one queued Compile submission can be cancelled by task identity without affecting its neighbor", async () => {
  const queue = new CompileSubmissionQueue();
  const generation = queue.generation();
  let releaseFirst;
  const firstGate = new Promise((resolve) => { releaseFirst = resolve; });
  const started = [];
  const first = queue.run(generation, "activity:first", async () => {
    started.push("first");
    await firstGate;
    return "first";
  });
  const second = queue.run(generation, "activity:second", async () => {
    started.push("second");
    return "second";
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(queue.cancel("activity:second"), "queued");
  releaseFirst();
  assert.equal(await first, "first");
  assert.equal(await second, undefined);
  assert.deepEqual(started, ["first"]);
  assert.equal(queue.isCurrent(generation), true);
  assert.equal(await queue.run(generation, "activity:third", async () => "third"), "third");
});

test("cancelling an in-flight submission marks only that identity so a returned POST can be cleaned up", async () => {
  const queue = new CompileSubmissionQueue();
  const generation = queue.generation();
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  let currentAfterResponse = true;
  const submitted = queue.run(generation, "activity:race", async () => {
    await gate;
    currentAfterResponse = queue.isTaskCurrent(generation, "activity:race");
    return { jobId: "job:accepted" };
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(queue.cancel("activity:race"), "running");
  release();
  assert.deepEqual(await submitted, { jobId: "job:accepted" });
  assert.equal(currentAfterResponse, false);
});

test("Compile submission retries transient statuses with one stable idempotency key", async () => {
  for (const status of [408, 425, 429, 500, 503]) {
    const versionId = `version:transient-${status}`;
    const calls = [];
    const delays = [];
    const submitted = await submitCompileJob(versionId, {
      idempotencyKey: "stable-key",
      request: async (_url, init) => {
        calls.push({
          key: new Headers(init.headers).get("idempotency-key"),
          body: init.body,
        });
        if (calls.length === 1) return new Response("not-json", { status });
        return Response.json({ ok: true, job: job(`transient-${status}`, "queued") }, { status: 202 });
      },
      sleep: async (milliseconds) => { delays.push(milliseconds); },
    });

    assert.equal(submitted.id, `transient-${status}`);
    assert.deepEqual(calls, [
      { key: "stable-key", body: JSON.stringify({ artifact_version_id: versionId }) },
      { key: "stable-key", body: JSON.stringify({ artifact_version_id: versionId }) },
    ]);
    assert.deepEqual(delays, [300]);
  }
});

test("Compile submission makes at most three attempts after network failures", async () => {
  let calls = 0;
  const delays = [];
  await assert.rejects(submitCompileJob("version:network", {
    idempotencyKey: "stable-key",
    request: async () => {
      calls += 1;
      throw new TypeError("network unavailable");
    },
    sleep: async (milliseconds) => { delays.push(milliseconds); },
  }), /network unavailable/);

  assert.equal(calls, 3);
  assert.deepEqual(delays, [300, 600]);
});

test("Compile submission does not retry ordinary 4xx or successful contract errors", async () => {
  let badRequestCalls = 0;
  await assert.rejects(submitCompileJob("version:bad-request", {
    idempotencyKey: "stable-key",
    request: async () => {
      badRequestCalls += 1;
      return Response.json({ error: { message: "invalid artifact" } }, { status: 400 });
    },
    sleep: async () => { throw new Error("must not sleep"); },
  }), /invalid artifact/);
  assert.equal(badRequestCalls, 1);

  let contractCalls = 0;
  await assert.rejects(submitCompileJob("version:expected", {
    idempotencyKey: "stable-key",
    request: async () => {
      contractCalls += 1;
      return Response.json({ ok: true, job: job("other", "queued") }, { status: 202 });
    },
    sleep: async () => { throw new Error("must not sleep"); },
  }), /与当前文献版本不一致/u);
  assert.equal(contractCalls, 1);
});

test("Compile errors identify the failed user-facing stage without internal diagnostics", () => {
  const diagnostic = 'GPT returned no valid JSON after 3 fresh attempts: [{"attempt":1,"classification":"truncated_json"},{"attempt":2,"classification":"schema_validation_failed","schema_errors":[{"loc":["claims",0,"scope"],"type":"missing"}]},{"attempt":3,"classification":"contract_validation_failed","contract_issue":"provenance_quote_not_found"}]';
  const extraction = compileErrorMessage(
    "MODEL_OUTPUT_INVALID",
    diagnostic,
    { extracting: 120_000, validating: null },
  );
  assert.match(extraction, /证据提取未完成/);
  assert.match(extraction, /重新 Compile/);
  assert.doesNotMatch(extraction, /GPT|JSON|Schema|claims|契约|provenance/iu);

  const verification = compileErrorMessage(
    "MODEL_OUTPUT_INVALID",
    diagnostic,
    { extracting: 120_000, validating: 80_000 },
  );
  assert.match(verification, /证据核验未完成/);
  assert.doesNotMatch(verification, /GPT|content|reasoning|sha256/iu);
});

test("MinerU failures show the final upstream reason after bounded retries", () => {
  const message = compileErrorMessage(
    "MINERU_FAILED",
    "remote MinerU failed after 3 attempts: remote MinerU returned HTTP 409",
  );
  assert.match(message, /3 次尝试后仍未完成/);
  assert.match(message, /HTTP 409/);
  assert.doesNotMatch(message, /文件无法解析|格式受支持/);
});
