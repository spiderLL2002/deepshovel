import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import test from "node:test";

import { evidenceSearchRelationGroupOrder, isUnfilteredCompletedLibrary, responseError } from "../app/workspace-types.ts";

test("stored legacy snapshots receive an actionable localized message", () => {
  const message = responseError({
    error: {
      code: "STORED_SNAPSHOT_INCOMPATIBLE",
      message: "Stored evidence no longer satisfies the current contract.",
    },
  }, "证据图读取失败");

  assert.match(message, /旧结果与当前版本不兼容/u);
  assert.match(message, /重新 Compile/u);
  assert.doesNotMatch(message, /Unexpected evidence-store/u);
});

test("unknown API errors preserve their bounded server message", () => {
  assert.equal(
    responseError({ error: { code: "UNKNOWN", message: "Specific bounded failure" } }, "fallback"),
    "Specific bounded failure",
  );
  assert.equal(responseError({}, "fallback"), "fallback");
});

test("expired clarification and preview bindings offer a localized fresh-search action", () => {
  for (const code of ["CLARIFICATION_REPREVIEW_REQUIRED", "QUERY_PLAN_HANDOFF_REPREVIEW_REQUIRED"]) {
    const message = responseError({ error: { code, message: "Upstream binding expired" } }, "fallback");
    assert.match(message, /已失效/u);
    assert.match(message, /重新检索/u);
    assert.doesNotMatch(message, /Upstream/u);
  }
});

test("planning classifications display distinct actionable Chinese messages without private detail", () => {
  const codes = [
    "RETRIEVAL_PLANNING_OUTPUT_INVALID", "RETRIEVAL_PLANNING_TIMEOUT",
    "RETRIEVAL_PLANNING_UPSTREAM_FAILED", "RETRIEVAL_PLANNING_UNAVAILABLE",
    "RETRIEVAL_PLANNING_PROVIDER_INVALID", "RETRIEVAL_PLANNING_INPUT_TOO_LARGE",
    "RETRIEVAL_PLANNING_BUDGET_EXCEEDED", "RETRIEVAL_PLANNING_INTERNAL_ERROR",
  ];
  const messages = codes.map((code) => responseError({ error: {
    code, message: "raw-provider-message private-query c1.private.payload.signature",
  } }, "generic failure"));
  assert.equal(new Set(messages).size, codes.length);
  for (const message of messages) {
    assert.match(message, /请/u);
    assert.doesNotMatch(message, /raw-provider|private-query|c1\.|generic failure|没有相关证据|无结果/u);
  }
  assert.match(messages[0], /校验/u);
  assert.match(messages[1], /超时/u);
  assert.match(messages[5], /传输限制/u);
});

test("relation sections preserve the intent recorded on the completed response", () => {
  assert.deepEqual(evidenceSearchRelationGroupOrder("claim_search"), [
    "equivalent", "support", "contradict", "qualify", "supplement", "related",
  ]);
  assert.deepEqual(evidenceSearchRelationGroupOrder("supporting_evidence").slice(0, 2), [
    "support", "equivalent",
  ]);
  assert.equal(evidenceSearchRelationGroupOrder("contradiction_search")[0], "contradict");
});

test("the all-status library view stays separate from the completed synthesis corpus", () => {
  assert.equal(isUnfilteredCompletedLibrary("completed", ""), true);
  assert.equal(isUnfilteredCompletedLibrary("completed", "南海"), false);
  assert.equal(isUnfilteredCompletedLibrary("completed", "rare earth"), false);
  assert.equal(isUnfilteredCompletedLibrary("pending", ""), false);
  assert.equal(isUnfilteredCompletedLibrary("processing", ""), false);
  assert.equal(isUnfilteredCompletedLibrary("all", ""), false);

  const workspace = readFileSync(new URL("../app/workspace.tsx", import.meta.url), "utf8");
  assert.match(workspace, /if \(isUnfilteredCompletedLibrary\(status, query\)\)/u);
  assert.match(workspace, /loadCompletedLibrary\(\)/u);
  assert.match(workspace, /loadLibrary\(\{ query: libraryQueryRef\.current, status: "all" \}\)/u);
  assert.match(workspace, /fetch\("\/api\/library\?limit=20&status=completed"/u);
  assert.doesNotMatch(workspace, /libraryStatusRef/u);
});

test("review timestamps interpret SQLite UTC without changing other business date parsing", () => {
  const moduleUrl = new URL("../app/workspace-types.ts", import.meta.url).href;
  const script = `
    import { formatDate, formatReviewDate } from ${JSON.stringify(moduleUrl)};
    console.log(JSON.stringify([
      formatReviewDate("2026-10-20 09:04:12"),
      formatReviewDate("2026-10-20 09:04:12.123"),
      formatReviewDate("2026-10-20T09:04:12Z"),
      formatReviewDate("2026-10-20T17:04:12+08:00"),
      formatReviewDate("not-a-date"),
      formatDate("2026-10-20 09:04:12"),
    ]));
  `;
  const values = JSON.parse(execFileSync(process.execPath,
    ["--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
    { env: { ...process.env, TZ: "Asia/Shanghai" }, encoding: "utf8" }));
  assert.match(values[0], /17:04$/);
  assert.deepEqual(values.slice(1, 4), [values[0], values[0], values[0]]);
  assert.equal(values[4], "not-a-date");
  assert.match(values[5], /09:04$/, "generic date parsing remains unchanged");
});
