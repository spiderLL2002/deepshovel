"use client";

import { createElement, Fragment } from "react";
import { canCancelActivity, isInterruptedActivity, normalizeActivities } from "../activity-state.ts";
import type { Activity } from "../workspace-types.ts";

type Props = {
  activity: Activity[];
  emptyTitle?: string;
  onCancel: (activity: Activity) => void;
  onRetry?: (activity: Activity) => void;
  /** Remove a failed record from the list (never deletes the paper). */
  onDismiss?: (activity: Activity) => void;
  selected?: ReadonlySet<string>;
  onSelect?: (id: string) => void;
  disabled?: boolean;
};

const ACTIVITY_STATE_LABEL: Record<Activity["state"], string> = {
  working: "进行中",
  done: "已完成",
  failed: "失败",
  cancelled: "已终止",
};

/** Render the normalized activity list without trusting callers to deduplicate it. */
export function ActivityList({ activity, emptyTitle = "没有进行中的任务", onCancel, onRetry, onDismiss, selected, onSelect, disabled = false }: Props) {
  const visibleActivity = normalizeActivities(activity);
  if (!visibleActivity.length) {
    return createElement("div", { className: "view-empty compact" },
      createElement("h3", null, emptyTitle),
    );
  }
  return createElement(Fragment, null, visibleActivity.map((item) => createElement(
    "article",
    { className: `activity-row ${item.state}`, key: item.id },
    isInterruptedActivity(item) && onSelect ? createElement("input", {
      type: "checkbox", "aria-label": `选择 ${item.label}`,
      checked: selected?.has(item.id) ?? false, disabled,
      onChange: () => onSelect(item.id),
    }) : createElement("span", {
      className: "state-dot",
      role: "img",
      "aria-label": `状态：${ACTIVITY_STATE_LABEL[item.state]}`,
    }),
    createElement("div", null,
      createElement("strong", { title: item.label }, item.label),
      createElement("p", null, item.detail),
    ),
    createElement("div", { className: "activity-row-actions" },
      canCancelActivity(item)
        ? createElement("button", {
          className: "button danger activity-cancel-task",
          type: "button",
          "aria-label": `终止 ${item.label}`,
          onClick: () => onCancel(item),
        }, "终止")
        : null,
      isInterruptedActivity(item) && item.kind === "compile" && item.artifactVersionId && onRetry
        ? createElement("button", {
          className: "button activity-retry-task",
          disabled,
          type: "button",
          "aria-label": `重试 ${item.label}`,
          onClick: () => onRetry(item),
        }, "重试")
        : null,
      isInterruptedActivity(item) && onDismiss ? createElement("button", {
        className: "button secondary", type: "button", disabled,
        "aria-label": `移除记录 ${item.label}`, onClick: () => onDismiss(item),
      }, "移除记录") : null,
    ),
    typeof item.progress === "number"
      ? createElement("progress", {
        max: 100,
        value: item.progress,
        "aria-label": `${item.label} 进度`,
      })
      : null,
  )));
}
