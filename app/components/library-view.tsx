"use client";

import { useState } from "react";
import type { ArtifactVersionDto, LibraryArtifactDto } from "../workspace-types";
import { formatBytes, shortHash } from "../workspace-types";
import { UiIcon } from "./ui-icon";
import { PaginationControls } from "./pagination-controls";

export type LibraryStatus = "pending" | "processing" | "completed" | "failed" | "all";

type Props = {
  artifacts: LibraryArtifactDto[];
  loading: boolean;
  query: string;
  disabled: boolean;
  pageInfo: { page: number; pageSize: number; total: number | null; totalPages: number | null; hasMore: boolean };
  onSearch: (query: string) => void;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
  onAddArtifact: () => void;
  onCompile: (version: ArtifactVersionDto) => void;
  onAddVersion: (artifactId: string) => void;
  onOpenSnapshot: (snapshotId: string) => void;
};

const COMPILABLE_MEDIA_TYPES = new Set([
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "image/png",
  "image/jpeg",
  "image/tiff",
]);

export function hasCompilableLibraryVersion(item: LibraryArtifactDto) {
  return Boolean(item.latestVersion && COMPILABLE_MEDIA_TYPES.has(item.latestVersion.mediaType));
}

function machineLabel(item: LibraryArtifactDto) {
  if (item.libraryStatus === "pending") return "待 Compile";
  if (item.libraryStatus === "processing") return "Compile 进行中";
  if (item.libraryStatus === "failed") return "Compile 未完成，可重试";
  if (item.machineVerification.status === "assessed") {
    return "证据已评估";
  }
  if (item.snapshot) return "尚未评估";
  return "证据尚未生成";
}

function formatCalendarDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? value
    : new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium" }).format(date);
}

function emptyTitle(hasQuery: boolean) {
  if (hasQuery) return "没有匹配的文献";
  return "还没有可查看的文献";
}

export function LibraryView({
  artifacts,
  loading,
  query,
  disabled,
  pageInfo,
  onSearch,
  onPageChange,
  onPageSizeChange,
  onAddArtifact,
  onCompile,
  onAddVersion,
  onOpenSnapshot,
}: Props) {
  const [draft, setDraft] = useState(query);
  const emptyStateTitle = pageInfo.hasMore ? "本页没有匹配，可继续下一页" : emptyTitle(Boolean(query));
  return (
    <section className="library-view" aria-labelledby="library-title">
      <header className="view-header page-intro library-header">
        <div><h1 id="library-title">文献库</h1></div>
      </header>

      <div className="library-toolbar">
        <div className="library-upload-control">
          <button className="button library-upload-button" disabled={disabled} onClick={onAddArtifact}><UiIcon name="upload" />上传文献</button>
        </div>
        <form className="library-search" onSubmit={(event) => { event.preventDefault(); onSearch(draft.trim()); }}>
          <label htmlFor="library-query">搜索文献</label>
          <div>
            <input id="library-query" value={draft} onChange={(event) => setDraft(event.target.value)} maxLength={180} placeholder="输入文件名" />
            <button className="button secondary" type="submit">搜索</button>
          </div>
        </form>
        {query ? <button className="text-button" type="button" onClick={() => { setDraft(""); onSearch(""); }}>清除筛选</button> : null}
      </div>

      <div className="library-table" role="table" aria-label="文献与 Compile 状态">
        <div className="library-row library-head" role="row">
          <span role="columnheader">文献</span>
          <span role="columnheader">提取结果</span>
          <span role="columnheader">分析状态</span>
          <span role="columnheader">更新时间</span>
          <span role="columnheader">操作</span>
        </div>
        {artifacts.map((item) => {
          const version = item.latestVersion;
          const snapshot = item.snapshot;
          const statusClass = item.libraryStatus;
          return (
            <article className="library-row" role="row" key={item.artifactId}>
              <div className="library-title-cell" role="cell">
                <strong title={item.title}>{item.title}</strong>
                {version ? <small>{formatBytes(version.byteSize)}</small> : <small>暂无可用版本</small>}
              </div>
              <div className="library-count-cell" role="cell">
                <strong>{snapshot?.counts.claims ?? 0}</strong>
                <span>论文观点</span>
                <small>{snapshot ? `${snapshot.counts.observations ?? 0} 项观察结果 · ${snapshot.counts.anchors ?? 0} 处原文` : "尚未提取证据"}</small>
              </div>
              <div role="cell">
                <span className={`compile-state ${statusClass}`}>{machineLabel(item)}</span>
              </div>
              <time role="cell" dateTime={item.updatedAt}>{formatCalendarDate(item.updatedAt)}</time>
              <div className="library-actions" role="cell">
                {snapshot ? <button className="button secondary" type="button" onClick={() => onOpenSnapshot(snapshot.id)}>打开证据</button> : null}
                {version && hasCompilableLibraryVersion(item) && ["pending", "failed"].includes(item.libraryStatus) ? (
                  <button className="button secondary" type="button" disabled={disabled} onClick={() => onCompile(version)}>
                    {item.libraryStatus === "failed" ? "重试 Compile" : "Compile"}
                  </button>
                ) : null}
                <details className="row-menu">
                  <summary aria-label={`${item.title} 更多操作`}><UiIcon name="more" /></summary>
                  <div>
                    <button type="button" disabled={disabled} onClick={() => onAddVersion(item.artifactId)}>上传新版本</button>
                    {version ? <a href={version.downloadUrl}>下载原件</a> : null}
                    {version ? (
                      <details>
                        <summary>文件版本</summary>
                        <p>v{version.versionNumber} · {shortHash(version.sha256)}</p>
                        <p className="mono">{version.id}</p>
                      </details>
                    ) : null}
                  </div>
                </details>
              </div>
            </article>
          );
        })}
      </div>

      {loading ? <div className="view-loading"><span />正在读取文献库…</div> : null}
      {!loading && !artifacts.length ? (
        <div className="view-empty">
          <h2>{emptyStateTitle}</h2>
          {!query ? (
            <div className="empty-actions">
              <button className="button" disabled={disabled} onClick={onAddArtifact}><UiIcon name="upload" />上传文献</button>
            </div>
          ) : null}
        </div>
      ) : null}
      {artifacts.length || pageInfo.hasMore || pageInfo.page > 1 ? (
        <PaginationControls
          page={pageInfo.page}
          pageSize={pageInfo.pageSize}
          total={pageInfo.total}
          cursorPagination
          hasMore={pageInfo.hasMore}
          disabled={loading}
          itemLabel="篇文献"
          onPageChange={onPageChange}
          onPageSizeChange={onPageSizeChange}
        />
      ) : null}

    </section>
  );
}
