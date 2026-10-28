import assert from "node:assert/strict";
import test from "node:test";

import { claimAnalysisActivity, isOpenWork } from "../app/work-activity.ts";

const base = {
  id: "claim_relations:snapshot:sha256:" + "a".repeat(64),
  kind: "claim_relations",
  subjectId: "snapshot:sha256:" + "a".repeat(64),
  state: "running",
  attempts: 0,
  maxAttempts: 6,
  nextAttemptAt: null,
  progress: { total: 8, persisted: 3 },
  error: null,
  fileName: "paper.pdf",
  artifactId: "artifact:1",
  artifactVersionId: "artifact-version:sha256:" + "b".repeat(64),
  updatedAt: "2026-10-18T00:00:00.000Z",
  completedAt: null,
};

test("server-side relation work renders as read-only task rows", () => {
  const running = claimAnalysisActivity(base);
  assert.equal(running.id, `claim-analysis:${base.subjectId}`);
  assert.equal(running.kind, "claim-analysis");
  assert.equal(running.state, "working");
  assert.equal(running.detail, "Claim 关系分析 3/8");
  assert.equal(running.progress, 38);
  assert.equal(running.cancellable, false);
  assert.equal(running.label, "paper.pdf · 关系分析");

  const retrying = claimAnalysisActivity({ ...base, state: "pending", attempts: 2,
    error: { code: "CLAIM_RELATION_PROCESSING_INCOMPLETE", message: "x" } });
  assert.match(retrying.detail, /将自动重试（第 3\/6 次）/u);

  assert.equal(claimAnalysisActivity({ ...base, state: "succeeded", progress: { total: 8, persisted: 8 } }).detail,
    "本篇 Claim 关系分析完成 · 8 条");
  assert.equal(claimAnalysisActivity({ ...base, state: "cancelled" }).state, "cancelled");
  const failed = claimAnalysisActivity({ ...base, state: "failed", error: { code: "SOMETHING_ELSE", message: "x" } });
  assert.equal(failed.state, "failed");
  assert.match(failed.detail, /证据图谱中重新处理/u);
  assert.equal(isOpenWork({ state: "pending" }), true);
  assert.equal(isOpenWork({ state: "failed" }), false);
});
