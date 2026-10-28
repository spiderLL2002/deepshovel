import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  activeActivityCount,
  activityTaskButtonLabel,
  canCancelActivity,
  canCancelAllActivity,
  isInterruptedActivity,
  normalizeActivities,
  reconcileCompileActivities,
  upsertActivity,
} from "../app/activity-state.ts";
import { ActivityList } from "../app/components/activity-list.ts";

function compileActivity({
  id,
  jobId,
  artifactId = "artifact:old",
  state = "working",
  updatedAt = "2026-08-17T08:00:00.000Z",
  label = "同名论文.pdf",
  cancellable = true,
  stage = "extracting",
}) {
  return {
    id,
    jobId,
    artifactId,
    artifactVersionId: `version:${artifactId}`,
    kind: "compile",
    state,
    detail: state === "working" ? "正在提取" : "Compile 失败",
    label,
    cancellable,
    progress: state === "working" ? 68 : 100,
    sourceUpdatedAt: updatedAt,
    stage,
  };
}

function uploadActivity({
  id,
  artifactId = "artifact:old",
  state = "done",
  label = "同名论文.pdf",
}) {
  return {
    id,
    artifactId,
    artifactVersionId: `version:${artifactId}`,
    kind: "upload",
    label,
    state,
    detail: state === "done" ? "上传完成" : state === "working" ? "正在上传" : "上传未完成",
    cancellable: state === "working",
  };
}

test("activity state collapses a local submission and resumed row for the same service job", () => {
  const resumed = compileActivity({ id: "resume:job-a", jobId: "job-a" });
  const submitted = compileActivity({
    id: "local-a",
    jobId: "job-a",
    state: "failed",
    cancellable: false,
    updatedAt: "2026-08-17T08:01:00.000Z",
  });

  const result = upsertActivity([resumed], submitted);
  assert.equal(result.length, 1);
  assert.equal(result[0].id, "local-a");
  assert.equal(result[0].jobId, "job-a");
  assert.equal(result[0].state, "failed");
  assert.equal(result[0].cancellable, false);
});

test("compile reconciliation removes service jobs no longer listed but keeps recent upload history", () => {
  const recentUpload = {
    id: "upload-a",
    artifactId: "artifact:kept",
    artifactVersionId: "version:kept",
    kind: "upload",
    label: "已上传.pdf",
    state: "done",
    detail: "上传完成",
    cancellable: false,
  };
  const stale = compileActivity({ id: "resume:stale", jobId: "stale" });
  const pendingSubmission = {
    id: "local-pending",
    artifactId: "artifact:pending",
    artifactVersionId: "version:pending",
    kind: "compile",
    label: "待提交.pdf",
    state: "working",
    detail: "正在提交 Compile 任务",
    cancellable: false,
  };

  const result = reconcileCompileActivities(
    [stale, recentUpload, pendingSubmission],
    [],
    new Set(),
  );
  assert.deepEqual(result.map((item) => item.id), ["upload-a", "local-pending"]);
});

test("compile supersedes the completed upload row for the exact ArtifactVersion", () => {
  const upload = uploadActivity({ id: "upload-done" });
  const pendingCompile = compileActivity({
    id: "local-submit",
    jobId: undefined,
    cancellable: false,
    stage: "queued",
  });

  const result = upsertActivity([upload], pendingCompile);
  assert.deepEqual(result.map((item) => item.id), ["local-submit"]);
  assert.equal(activeActivityCount(result), 1);

  const html = renderToStaticMarkup(createElement(ActivityList, {
    activity: result,
    onCancel() {},
  }));
  assert.equal(html.match(/<article/gu)?.length, 1);
  assert.doesNotMatch(html, /上传完成/u);
  assert.match(html, /同名论文\.pdf/u);
  assert.match(html, /aria-label="状态：进行中"/u);
});

test("compaction keeps non-success uploads and every active Compile task", () => {
  const compileA = compileActivity({ id: "active-a", jobId: "active-a" });
  const compileB = compileActivity({ id: "active-b", jobId: "active-b" });
  const items = normalizeActivities([
    compileB,
    compileA,
    uploadActivity({ id: "upload-done", state: "done" }),
    uploadActivity({ id: "upload-working", state: "working" }),
    uploadActivity({ id: "upload-failed", state: "failed" }),
    uploadActivity({ id: "upload-cancelled", state: "cancelled" }),
  ]);

  assert.ok(items.some((item) => item.id === "active-a"));
  assert.ok(items.some((item) => item.id === "active-b"));
  assert.ok(items.some((item) => item.id === "upload-working"));
  assert.ok(items.some((item) => item.id === "upload-failed"));
  assert.ok(items.some((item) => item.id === "upload-cancelled"));
  assert.ok(!items.some((item) => item.id === "upload-done"));
  assert.equal(activeActivityCount(items), 3);
});

test("a newer Compile job supersedes old terminal jobs for one version", () => {
  const oldFailed = compileActivity({
    id: "old-failed",
    jobId: "old-failed",
    state: "failed",
    cancellable: false,
    updatedAt: "2026-08-17T08:00:00.000Z",
  });
  const newActive = compileActivity({
    id: "new-active",
    jobId: "new-active",
    updatedAt: "2026-08-17T09:00:00.000Z",
  });

  const activeResult = reconcileCompileActivities(
    [oldFailed],
    [newActive],
    new Set(["old-failed", "new-active"]),
  );
  assert.deepEqual(activeResult.map((item) => item.id), ["new-active"]);

  const newestFailed = { ...newActive, state: "failed", cancellable: false, progress: 100 };
  const terminalResult = normalizeActivities([newestFailed, oldFailed]);
  assert.deepEqual(terminalResult.map((item) => item.id), ["new-active"]);
});

test("same filenames on distinct ArtifactVersions never collapse", () => {
  const left = compileActivity({ id: "left", jobId: "left", artifactId: "artifact:left" });
  const right = compileActivity({ id: "right", jobId: "right", artifactId: "artifact:right" });
  const result = normalizeActivities([left, right]);
  assert.equal(result.length, 2);
  assert.deepEqual(new Set(result.map((item) => item.artifactVersionId)), new Set(["version:artifact:left", "version:artifact:right"]));
});

test("task badge counts normalized active work, not duplicate or completed history", () => {
  const duplicate = compileActivity({ id: "resume:job-a", jobId: "job-a" });
  const canonical = compileActivity({ id: "local-a", jobId: "job-a" });
  const upload = {
    id: "upload-active",
    kind: "upload",
    label: "新文献.pdf",
    state: "working",
    detail: "正在上传",
    cancellable: true,
  };
  const completed = { ...upload, id: "upload-done", state: "done", detail: "上传完成", cancellable: false };

  assert.equal(normalizeActivities([duplicate, canonical, upload, completed]).length, 3);
  assert.equal(activeActivityCount([duplicate, canonical, upload, completed]), 2);
});

test("activity list component renders one row and one cancel action for a duplicated service job", () => {
  const duplicate = compileActivity({ id: "resume:job-a", jobId: "job-a" });
  const canonical = compileActivity({ id: "local-a", jobId: "job-a" });
  const completed = {
    id: "upload-done",
    kind: "upload",
    label: "已上传.pdf",
    state: "done",
    detail: "上传完成",
    cancellable: false,
  };
  const html = renderToStaticMarkup(createElement(ActivityList, {
    activity: [duplicate, canonical, completed],
    onCancel() {},
  }));

  assert.equal(html.match(/title="同名论文\.pdf"/gu)?.length, 1);
  assert.equal(html.match(/<article/gu)?.length, 2);
  assert.equal(html.match(/>终止<\/button>/gu)?.length, 1);
  assert.match(html, /class="button danger activity-cancel-task"/u);
  assert.match(html, /aria-label="终止 同名论文\.pdf"/u);
  assert.match(html, /aria-label="状态：进行中"/u);
  assert.match(html, /aria-label="状态：已完成"/u);
  assert.match(html, /已上传\.pdf/u);
});

test("cancel-all includes a local queued submission but excludes importing", () => {
  const localQueued = compileActivity({
    id: "local-queued",
    jobId: undefined,
    cancellable: false,
    stage: "queued",
  });
  const importing = compileActivity({
    id: "importing",
    jobId: "importing",
    cancellable: true,
    stage: "importing",
  });
  assert.equal(canCancelActivity(localQueued), true);
  assert.equal(canCancelActivity({ ...localQueued, stage: undefined }), false);
  assert.equal(canCancelActivity(importing), false);
  assert.equal(canCancelAllActivity(localQueued), true);
  assert.equal(canCancelAllActivity(importing), false);
  const html = renderToStaticMarkup(createElement(ActivityList, {
    activity: [localQueued, importing],
    onCancel() {},
  }));
  assert.match(html, /aria-label="终止 同名论文\.pdf"/u);
  assert.equal(html.match(/>终止<\/button>/gu)?.length, 1);
  assert.equal(activityTaskButtonLabel(2), "当前任务，2 项进行中");
  assert.equal(activityTaskButtonLabel(0), "当前任务，没有进行中的任务");
});

test("per-task cancellation follows the shared control height and retains a 44px touch target", () => {
  const css = readFileSync(new URL("../app/styles/overlays-responsive.css", import.meta.url), "utf8");
  const rule = css.match(/\.activity-cancel-task\s*\{[^}]+\}/u)?.[0] ?? "";
  assert.match(rule, /min-width:\s*2\.75rem/u);
  assert.match(rule, /min-height:\s*var\(--control-height\)/u);
  const foundation = readFileSync(new URL("../app/styles/foundation.css", import.meta.url), "utf8");
  assert.match(foundation, /@media\s*\(pointer:\s*coarse\)[^{]*\{\s*:root\s*\{\s*--control-height:\s*2\.75rem/u);
});

test("activity list supports a failed-task empty state", () => {
  const html = renderToStaticMarkup(createElement(ActivityList, {
    activity: [],
    emptyTitle: "没有失败任务",
    onCancel() {},
  }));
  assert.match(html, /没有失败任务/u);
});

test("failed Compile rows expose an explicit retry action", () => {
  const html = renderToStaticMarkup(createElement(ActivityList, {
    activity: [compileActivity({ id: "failed-retry", state: "failed", cancellable: false })],
    onCancel() {},
    onRetry() {},
  }));
  assert.match(html, /class="button activity-retry-task"/u);
  assert.match(html, /aria-label="重试 同名论文\.pdf"/u);
  assert.match(html, />重试<\/button>/u);
});

test("cancelled Compile stays reachable with retry and record controls", () => {
  const cancelled = compileActivity({ id: "cancelled-retry", state: "cancelled", cancellable: false });
  assert.equal(isInterruptedActivity(cancelled), true);
  assert.equal(isInterruptedActivity({ ...cancelled, state: "working" }), false);
  assert.equal(isInterruptedActivity({ ...cancelled, state: "done" }), false);
  const html = renderToStaticMarkup(createElement(ActivityList, {
    activity: [cancelled],
    onCancel() {},
    onRetry() {},
    onSelect() {},
    onDismiss() {},
  }));
  assert.match(html, /aria-label="重试 同名论文\.pdf"/u);
  assert.match(html, /aria-label="选择 同名论文\.pdf"/u);
  assert.match(html, /aria-label="移除记录 同名论文\.pdf"/u);
  assert.doesNotMatch(html, /aria-label="终止 同名论文\.pdf"/u);
});

test("terminal history is capped without dropping any active task", () => {
  const active = Array.from({ length: 75 }, (_, index) => compileActivity({
    id: `active-${index}`,
    jobId: `active-job-${index}`,
    artifactId: `artifact:active-${index}`,
    label: `运行中-${index}.pdf`,
  }));
  const terminal = Array.from({ length: 80 }, (_, index) => compileActivity({
    id: `terminal-${index}`,
    jobId: `terminal-job-${index}`,
    artifactId: `artifact:terminal-${index}`,
    state: "failed",
    cancellable: false,
    label: `已结束-${index}.pdf`,
  }));

  const normalized = normalizeActivities([...active, ...terminal]);
  assert.equal(normalized.filter((item) => item.state === "working").length, 75);
  assert.equal(normalized.filter((item) => item.state !== "working").length, 50);
  assert.equal(activeActivityCount(normalized), 75);
  assert.ok(active.every((item) => normalized.some((entry) => entry.id === item.id)));

  const patched = upsertActivity(normalized, {
    ...active.at(-1),
    state: "failed",
    detail: "Compile 失败",
    cancellable: false,
  });
  assert.equal(activeActivityCount(patched), 74);
  assert.equal(patched.filter((item) => item.state !== "working").length, 50);
});
