"use client";

import { useMemo, useState } from "react";
import { canCancelAllActivity, isInterruptedActivity, normalizeActivities } from "../activity-state.ts";
import type { Activity, ArtifactVersionDto, LibraryArtifactDto } from "../workspace-types";
import { formatBytes } from "../workspace-types";
import { ActivityList } from "./activity-list.ts";
import { hasCompilableLibraryVersion } from "./library-view";
import { PaginationControls } from "./pagination-controls";
import { UiIcon } from "./ui-icon";

export type ActivityFilter = "processing" | "failed" | "pending";

type Props = {
  activity: Activity[];
  pendingArtifacts: LibraryArtifactDto[];
  pendingLoading: boolean;
  compileAllProgress: number | null;
  onCompileAll: () => void;
  pendingPage: { page: number; pageSize: number; total: number | null; hasMore: boolean; query: string };
  onPendingRequest: (request: { page: number; pageSize: number; query: string }) => void;
  disabled: boolean;
  filter: ActivityFilter;
  onFilterChange: (filter: ActivityFilter) => void;
  onUpload: () => void;
  onCompile: (version: ArtifactVersionDto) => void;
  onRetry: (activity: Activity) => void;
  onRetryMany: (activity: Activity[]) => void;
  onCancel: (activity: Activity) => void;
  onCancelAll: () => void;
  /** Remove failed task records from this list only; papers are never deleted from here. */
  onDismissActivities: (items: Activity[]) => void;
};

const FILTERS: Array<{ value: ActivityFilter; label: string }> = [
  { value: "processing", label: "分析中" },
  { value: "failed", label: "失败 / 已终止" },
  { value: "pending", label: "待 Compile" },
];

function pendingVersion(item: LibraryArtifactDto) {
  return hasCompilableLibraryVersion(item) ? item.latestVersion : undefined;
}

export function ActivityView({
  activity,
  pendingArtifacts,
  pendingLoading,
  compileAllProgress,
  onCompileAll,
  pendingPage,
  onPendingRequest,
  disabled,
  filter,
  onFilterChange,
  onUpload,
  onCompile,
  onRetry,
  onRetryMany,
  onCancel,
  onCancelAll,
  onDismissActivities,
}: Props) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState(pendingPage.query);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(pendingPage.pageSize);
  const normalizedQuery = query.trim().toLocaleLowerCase("und");
  const filteredActivity = useMemo(() => normalizeActivities(activity).filter((item) => {
    const matchesStatus = filter === "processing" ? item.state === "working" : filter === "failed" && isInterruptedActivity(item);
    return matchesStatus && (!normalizedQuery
      || item.label.toLocaleLowerCase("und").includes(normalizedQuery)
      || item.detail.toLocaleLowerCase("und").includes(normalizedQuery));
  }), [activity, filter, normalizedQuery]);
  const filteredPending = pendingArtifacts;
  const retryable = useMemo(() => filteredActivity.filter((item) => item.kind === "compile" && item.artifactVersionId), [filteredActivity]);
  const canCancelAll = compileAllProgress !== null || (filter === "processing" && activity.some(canCancelAllActivity));
  const total = filter === "pending" ? pendingPage.total : filteredActivity.length;
  const currentPage = filter === "pending" ? pendingPage.page : Math.min(page, Math.max(1, Math.ceil((total ?? 0) / pageSize)));
  const start = (currentPage - 1) * pageSize;
  const visibleActivity = filteredActivity.slice(start, start + pageSize);
  const visiblePending = filteredPending;

  const selectFilter = (next: ActivityFilter) => {
    setSelected(new Set());
    onFilterChange(next);
    setPage(1);
    if (next === "pending") onPendingRequest({ page: 1, pageSize, query });
  };

  const selectableIds = visibleActivity.map((item) => item.id);
  const selectedIds = filteredActivity.map((item) => item.id).filter((id) => selected.has(id));
  const toggleSelection = (id: string) => setSelected((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const dismissItems = (ids: string[]) => {
    if (!ids.length) return;
    onDismissActivities(filteredActivity.filter((item) => ids.includes(item.id)));
    setSelected((current) => new Set([...current].filter((id) => !ids.includes(id))));
  };

  return (
    <section className="activity-view" aria-labelledby="activity-view-title">
      <header className="view-header page-intro">
        <div><h1 id="activity-view-title">当前任务</h1></div>
        <div className="activity-header-actions">
          <button className="button" type="button" disabled={disabled} onClick={onUpload}><UiIcon name="upload" />上传文献</button>
          {canCancelAll ? <button className="button danger" type="button" onClick={onCancelAll}>终止全部</button> : null}
        </div>
      </header>
      {compileAllProgress !== null ? <p role="status">批量 Compile：已提交 {compileAllProgress} 篇，正在按任务容量继续。可随时终止全部。</p> : null}
      <form className="activity-search" onSubmit={(event) => { event.preventDefault(); if (filter === "pending") onPendingRequest({ page: 1, pageSize, query }); }}>
        <label htmlFor="activity-query">搜索任务</label>
        <input id="activity-query" value={query} placeholder="输入文件名或任务状态" onChange={(event) => { setQuery(event.target.value); setPage(1); }} />
      <button className="button secondary" type="submit">搜索</button></form>
      <div className="activity-filter-bar">
        <div className="status-tabs" role="group" aria-label="按任务状态筛选">
          {FILTERS.map((item) => (
            <button key={item.value} type="button" aria-pressed={filter === item.value} onClick={() => selectFilter(item.value)}>
              {item.label}
            </button>
          ))}
        </div>
        {filter === "failed" && retryable.length ? (
          <button className="button secondary activity-batch-action" type="button" disabled={disabled} onClick={() => onRetryMany(retryable)}>
            重试全部未完成任务
          </button>
        ) : null}
        {filter === "pending" && (filteredPending.length || pendingPage.hasMore || compileAllProgress !== null) ? (
          <button className="button activity-batch-action" type="button" disabled={disabled || compileAllProgress !== null} onClick={onCompileAll}>
            {compileAllProgress === null ? "Compile 全部文件" : `已提交 ${compileAllProgress} 篇…`}
          </button>
        ) : null}
      </div>
      {filter === "failed" && (total ?? 0) > 0 ? (
        <div className="activity-selection-bar">
          <label><input type="checkbox" aria-label="全选本页任务" disabled={disabled} checked={selectableIds.length > 0 && selectableIds.every((id) => selected.has(id))} onChange={(event) => {
            const checked = event.target.checked;
            setSelected((current) => { const next = new Set(current); for (const id of selectableIds) { if (checked) next.add(id); else next.delete(id); } return next; });
          }} />全选本页</label>
          <button className="button secondary" type="button" disabled={disabled || !selectedIds.length} onClick={() => dismissItems(selectedIds)}>{`移除选中记录${selectedIds.length ? `（${selectedIds.length}）` : ""}`}</button>
        </div>
      ) : null}
      <div className="activity-workbench" aria-live="polite">
        {filter === "pending" ? (
          pendingLoading ? <div className="view-loading"><span />正在读取待 Compile 文献…</div> : visiblePending.length ? visiblePending.map((item) => {
            const version = pendingVersion(item);
            return (
              <article className="activity-row pending" key={item.artifactId}>
                <div>
                  <strong title={item.title}>{item.title}</strong>
                  <p>{version ? `${formatBytes(version.byteSize)} · 尚未 Compile` : "暂无可用文件版本"}</p>
                </div>
                <div className="activity-row-actions">
                  <button className="button activity-compile-task" type="button" disabled={disabled || !version} onClick={() => version && onCompile(version)}>Compile</button>
                </div>
              </article>
            );
          }) : <div className="view-empty compact"><h3>{pendingPage.hasMore ? "本页没有匹配，可继续下一页" : normalizedQuery ? "没有匹配的待 Compile 文献" : "没有待 Compile 的文献"}</h3></div>
        ) : (
          <ActivityList
            activity={visibleActivity}
            emptyTitle={filter === "processing" ? "没有进行中的任务" : "没有失败或已终止任务"}
            onCancel={onCancel}
            onRetry={onRetry}
            disabled={disabled}
            selected={selected}
            onSelect={filter === "failed" ? toggleSelection : undefined}
            onDismiss={filter === "failed" ? (item) => dismissItems([item.id]) : undefined}
          />
        )}
      </div>
      {total || visiblePending.length || (filter === "pending" && pendingPage.hasMore) || currentPage > 1 ? <PaginationControls page={currentPage} pageSize={filter === "pending" ? pendingPage.pageSize : pageSize} total={total} cursorPagination={filter === "pending"} hasMore={pendingPage.hasMore} disabled={filter === "pending" && pendingLoading} itemLabel={filter === "pending" ? "篇文献" : "项任务"} onPageChange={(next) => { setSelected(new Set()); if (filter === "pending") onPendingRequest({ page: next, pageSize, query }); else setPage(next); }} onPageSizeChange={(size) => { setSelected(new Set()); setPageSize(size); setPage(1); if (filter === "pending") onPendingRequest({ page: 1, pageSize: size, query }); }} /> : null}
    </section>
  );
}
