"use client";

import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import type {
  DocumentExtractionVisual,
  DocumentMetadataField,
  DocumentMetadataFieldSource,
} from "@/lib/document-extraction";
import type {
  DocumentMetadataPageDto,
  DocumentTextPageDto,
  DocumentVisualPageDto,
  LibraryArtifactDto,
} from "../workspace-types";
import { readJson, responseError } from "../workspace-types";
import { PdfPagePreview } from "./pdf-page-preview";
import { PdfRegionPreview } from "./pdf-region-preview";
import { ScientificContent, SourceMediaPreview, StructuredVisualEvidenceView } from "./scientific-content-lazy";
import { UiIcon } from "./ui-icon";
import { PaginationControls } from "./pagination-controls";

type ContentTab = "metadata" | "text" | "visuals";
type VisualFilter = "all" | "figure" | "table";

const VISUAL_PAGE_SIZES = [5, 10, 20, 50] as const;
const DOCUMENT_CACHE_LIMIT = 3;
const VISUAL_CACHE_LIMIT = 12;
const CONTENT_TABS = [
  { value: "metadata", label: "元数据" },
  { value: "text", label: "正文" },
  { value: "visuals", label: "图表" },
] as const satisfies ReadonlyArray<{ value: ContentTab; label: string }>;
const METADATA_FIELDS = [
  { value: "title", label: "标题" },
  { value: "abstract", label: "摘要" },
  { value: "year", label: "年份" },
  { value: "author", label: "作者" },
  { value: "journal", label: "期刊" },
  { value: "issn", label: "ISSN" },
  { value: "volume", label: "卷" },
  { value: "issue", label: "期" },
  { value: "page", label: "页码" },
  { value: "article_number", label: "文章号" },
  { value: "doi", label: "DOI" },
  { value: "link", label: "文献链接" },
  { value: "publisher", label: "出版方" },
  { value: "language", label: "语言" },
] as const satisfies ReadonlyArray<{ value: DocumentMetadataField; label: string }>;

type Props = {
  onOpenSnapshot: (snapshotId: string) => void;
};

function paperTitle(artifact: LibraryArtifactDto) {
  return artifact.title.replace(/\.(?:pdf|docx?|xlsx?)$/iu, "");
}

function safeExternalUrl(value: string) {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

function doiUrl(value: string) {
  const doi = value.trim().replace(/^https?:\/\/(?:dx\.)?doi\.org\//iu, "");
  if (!doi || /\s/u.test(doi)) return null;
  return `https://doi.org/${doi.split("/").map((part) => encodeURIComponent(part)).join("/")}`;
}

function MetadataValue({
  field,
  source,
  value,
}: {
  field: DocumentMetadataField;
  source: DocumentMetadataFieldSource;
  value: string;
}) {
  const normalized = value.trim();
  if (!normalized) {
    return <span className="visual-metadata-empty">{source === "doi_registry" ? "DOI 登记信息未提供" : "未提取到，请对照原文"}</span>;
  }
  if (field === "abstract") return <ScientificContent value={normalized} />;
  const href = field === "link" ? safeExternalUrl(normalized) : field === "doi" ? doiUrl(normalized) : null;
  return (
    <span className="visual-metadata-value">
      {href
        ? <a href={href} target="_blank" rel="noreferrer noopener">{normalized}</a>
        : <ScientificContent value={normalized} inline />}
    </span>
  );
}

function nextContentTab(key: string, current: ContentTab) {
  const index = CONTENT_TABS.findIndex((tab) => tab.value === current);
  if (key === "Home") return CONTENT_TABS[0].value;
  if (key === "End") return CONTENT_TABS.at(-1)!.value;
  if (key === "ArrowRight") return CONTENT_TABS[(index + 1) % CONTENT_TABS.length].value;
  if (key === "ArrowLeft") return CONTENT_TABS[(index - 1 + CONTENT_TABS.length) % CONTENT_TABS.length].value;
  return null;
}

function visualMarker(record: DocumentExtractionVisual, number: string) {
  const label = record.source_kind === "table"
    ? "TABLE"
    : record.source_kind === "chart"
      ? "CHART"
      : record.source_kind === "image"
        ? "IMAGE"
        : "FIGURE";
  return `${label} ${number}`;
}

function rememberCache<K, V>(cache: Map<K, V>, key: K, value: V, limit: number) {
  cache.delete(key);
  cache.set(key, value);
  while (cache.size > limit) {
    const oldest = cache.keys().next().value;
    if (oldest === undefined) break;
    cache.delete(oldest);
  }
}

export function PaperVisualLibrary({ onOpenSnapshot }: Props) {
  const [papers, setPapers] = useState<LibraryArtifactDto[]>([]);
  const [directoryLoading, setDirectoryLoading] = useState(true);
  const [directoryError, setDirectoryError] = useState("");
  const [selectedSnapshotId, setSelectedSnapshotId] = useState("");
  const [query, setQuery] = useState("");
  const [queryDraft, setQueryDraft] = useState("");
  const [paperPage, setPaperPage] = useState(1);
  const [paperPageSize, setPaperPageSize] = useState(5);
  const paperCursors = useRef(new Map<number, string>());
  const [paperPageInfo, setPaperPageInfo] = useState({ page: 1, pageSize: 5, total: null as number | null, totalPages: null as number | null, hasMore: false, nextCursor: null as string | null });
  const [contentTab, setContentTab] = useState<ContentTab>("metadata");
  const [filter, setFilter] = useState<VisualFilter>("all");
  const [visualPage, setVisualPage] = useState(1);
  const [visualPageSize, setVisualPageSize] = useState(5);
  const [metadataPageData, setMetadataPageData] = useState<DocumentMetadataPageDto | null>(null);
  const [metadataLoading, setMetadataLoading] = useState(false);
  const [metadataError, setMetadataError] = useState("");
  const [textPageData, setTextPageData] = useState<DocumentTextPageDto | null>(null);
  const [textLoading, setTextLoading] = useState(false);
  const [textError, setTextError] = useState("");
  const [visualPageData, setVisualPageData] = useState<DocumentVisualPageDto | null>(null);
  const [visualLoading, setVisualLoading] = useState(false);
  const [visualError, setVisualError] = useState("");
  const metadataCache = useRef(new Map<string, DocumentMetadataPageDto>());
  const textCache = useRef(new Map<string, DocumentTextPageDto>());
  const visualCache = useRef(new Map<string, DocumentVisualPageDto>());
  const activeSnapshotId = papers.some((paper) => paper.snapshot?.id === selectedSnapshotId)
    ? selectedSnapshotId
    : papers[0]?.snapshot?.id ?? "";
  const activePaper = papers.find((paper) => paper.snapshot?.id === activeSnapshotId) ?? null;

  useEffect(() => {
    const controller = new AbortController();
    const frame = window.requestAnimationFrame(() => void (async () => {
      setDirectoryLoading(true);
      setDirectoryError("");
      try {
        const parameters = new URLSearchParams({ status: "completed", limit: String(paperPageSize) });
        if (paperPage === 1) paperCursors.current.clear();
        const cursor = paperCursors.current.get(paperPage);
        if (cursor) parameters.set("cursor", cursor);
        if (query) parameters.set("query", query);
        const response = await fetch(`/api/library?${parameters}`, { cache: "no-store", signal: controller.signal });
        const payload = await readJson(response);
        if (!response.ok) throw new Error(responseError(payload, "论文目录读取失败，请稍后重试"));
        if (controller.signal.aborted) return;
        const result = payload as { artifacts: LibraryArtifactDto[]; pageInfo: typeof paperPageInfo };
        setPapers(result.artifacts.filter((artifact) => artifact.snapshot && artifact.latestVersion));
        if (result.pageInfo.nextCursor) paperCursors.current.set(paperPage + 1, result.pageInfo.nextCursor);
        setPaperPageInfo({ ...result.pageInfo, page: paperPage });
      } catch (cause) {
        if (!controller.signal.aborted) setDirectoryError(cause instanceof Error ? cause.message : "论文目录读取失败，请稍后重试");
      } finally {
        if (!controller.signal.aborted) setDirectoryLoading(false);
      }
    })());
    return () => { window.cancelAnimationFrame(frame); controller.abort(); };
  }, [paperPage, paperPageSize, query]);

  useEffect(() => {
    if (!activeSnapshotId) return;
    const controller = new AbortController();
    void (async () => {
      const cached = metadataCache.current.get(activeSnapshotId);
      if (cached) {
        setMetadataPageData(cached);
        setMetadataLoading(false);
        setMetadataError("");
        return;
      }
      setMetadataPageData(null);
      setMetadataLoading(true);
      setMetadataError("");
      try {
        const response = await fetch(`/api/evidence-graphs/${encodeURIComponent(activeSnapshotId)}/document?section=metadata`, {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = await readJson(response);
        if (!response.ok) throw new Error(responseError(payload, "论文资料读取失败，请稍后重试"));
        const next = payload as DocumentMetadataPageDto;
        if (next.snapshotId !== activeSnapshotId
          || next.section !== "metadata"
          || next.extraction.artifact_version_id !== next.rootArtifactVersionId) {
          throw new Error("论文资料与所选论文不一致，请重新读取");
        }
        if (controller.signal.aborted) return;
        rememberCache(metadataCache.current, activeSnapshotId, next, DOCUMENT_CACHE_LIMIT);
        setMetadataPageData(next);
      } catch (cause) {
        if (!controller.signal.aborted) {
          setMetadataError(cause instanceof Error ? cause.message : "论文资料读取失败，请稍后重试");
        }
      } finally {
        if (!controller.signal.aborted) setMetadataLoading(false);
      }
    })();
    return () => controller.abort();
  }, [activeSnapshotId]);

  useEffect(() => {
    if (!activeSnapshotId || contentTab !== "text") return;
    const controller = new AbortController();
    void (async () => {
      const cached = textCache.current.get(activeSnapshotId);
      if (cached) {
        setTextPageData(cached);
        setTextLoading(false);
        setTextError("");
        return;
      }
      setTextPageData(null);
      setTextLoading(true);
      setTextError("");
      try {
        const response = await fetch(`/api/evidence-graphs/${encodeURIComponent(activeSnapshotId)}/document?section=text`, {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = await readJson(response);
        if (!response.ok) throw new Error(responseError(payload, "论文正文读取失败，请稍后重试"));
        const next = payload as DocumentTextPageDto;
        if (next.snapshotId !== activeSnapshotId || next.section !== "text") {
          throw new Error("论文正文与所选论文不一致，请重新读取");
        }
        if (controller.signal.aborted) return;
        rememberCache(textCache.current, activeSnapshotId, next, DOCUMENT_CACHE_LIMIT);
        setTextPageData(next);
      } catch (cause) {
        if (!controller.signal.aborted) setTextError(cause instanceof Error ? cause.message : "论文正文读取失败，请稍后重试");
      } finally {
        if (!controller.signal.aborted) setTextLoading(false);
      }
    })();
    return () => controller.abort();
  }, [activeSnapshotId, contentTab]);

  const visualRequestKey = `${activeSnapshotId}:${filter}:${visualPage}:${visualPageSize}`;
  useEffect(() => {
    if (!activeSnapshotId || contentTab !== "visuals") return;
    const controller = new AbortController();
    void (async () => {
      const cached = visualCache.current.get(visualRequestKey);
      if (cached) {
        setVisualPageData(cached);
        setVisualLoading(false);
        setVisualError("");
        return;
      }
      setVisualPageData(null);
      setVisualLoading(true);
      setVisualError("");
      try {
        const parameters = new URLSearchParams({
          section: "visuals",
          kind: filter,
          page: String(visualPage),
          limit: String(visualPageSize),
        });
        const response = await fetch(`/api/evidence-graphs/${encodeURIComponent(activeSnapshotId)}/document?${parameters}`, {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = await readJson(response);
        if (!response.ok) throw new Error(responseError(payload, "论文图表读取失败，请稍后重试"));
        const next = payload as DocumentVisualPageDto;
        if (next.snapshotId !== activeSnapshotId || next.section !== "visuals" || next.kind !== filter) {
          throw new Error("论文图表与所选论文不一致，请重新读取");
        }
        if (controller.signal.aborted) return;
        rememberCache(visualCache.current, visualRequestKey, next, VISUAL_CACHE_LIMIT);
        setVisualPageData(next);
      } catch (cause) {
        if (!controller.signal.aborted) setVisualError(cause instanceof Error ? cause.message : "论文图表读取失败，请稍后重试");
      } finally {
        if (!controller.signal.aborted) setVisualLoading(false);
      }
    })();
    return () => controller.abort();
  }, [activeSnapshotId, contentTab, filter, visualPage, visualPageSize, visualRequestKey]);

  const visibleMetadataPage = metadataPageData?.snapshotId === activeSnapshotId ? metadataPageData : null;
  const visibleTextPage = textPageData?.snapshotId === activeSnapshotId ? textPageData : null;
  const visibleVisualPage = visualPageData?.snapshotId === activeSnapshotId
    && visualPageData.kind === filter
    && visualPageData.pageInfo.page === visualPage
    && visualPageData.pageInfo.pageSize === visualPageSize
    ? visualPageData
    : null;
  const extraction = visibleMetadataPage?.extraction ?? null;
  const version = visibleMetadataPage?.sourceVersion ?? visibleTextPage?.sourceVersion ?? visibleVisualPage?.sourceVersion ?? null;
  const sourceTitle = activePaper ? paperTitle(activePaper) : visibleMetadataPage?.sourceTitle ?? "论文原件";
  const visibleVisuals = visibleVisualPage?.records ?? [];
  const currentVisualPage = visibleVisualPage?.pageInfo.page ?? visualPage;
  const figureCount = visibleVisualPage?.counts.figure ?? extraction?.stats.figure_count ?? 0;
  const tableCount = visibleVisualPage?.counts.table ?? extraction?.stats.table_count ?? 0;
  const totalVisualCount = visibleVisualPage?.counts.all ?? extraction?.stats.visual_count ?? 0;

  const handleContentTabKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>, current: ContentTab) => {
    const next = nextContentTab(event.key, current);
    if (!next) return;
    event.preventDefault();
    setContentTab(next);
    window.requestAnimationFrame(() => document.getElementById(`visual-content-tab-${next}`)?.focus());
  };

  const selectPaper = (snapshotId: string) => {
    setSelectedSnapshotId(snapshotId);
    setFilter("all");
    setVisualPage(1);
  };

  const resetVisualLedger = () => {
    setFilter("all");
    setVisualPage(1);
  };

  return (
    <section className="visual-library-view" aria-labelledby="visual-library-title">
      <header className="visual-library-header page-intro">
        <div><h1 id="visual-library-title">数据图表库</h1></div>
      </header>

      <div className="visual-library-workbench">
        <aside className="visual-paper-index" aria-label="论文目录">
          <header>
            <form onSubmit={(event) => { event.preventDefault(); setPaperPage(1); resetVisualLedger(); setQuery(queryDraft.trim()); }}>
              <label htmlFor="visual-paper-search">搜索论文</label>
              <div className="visual-paper-search-field">
                <UiIcon name="search" size={16} />
                <input id="visual-paper-search" type="search" value={queryDraft} onChange={(event) => setQueryDraft(event.target.value)} placeholder="输入论文名或文件名" />
              </div>
              <div className="visual-paper-search-actions">
                <button className="button secondary" type="submit">搜索</button>
                {query ? <button className="text-button" type="button" onClick={() => { setQueryDraft(""); setQuery(""); setPaperPage(1); resetVisualLedger(); }}>清除</button> : null}
              </div>
            </form>
          </header>
          <nav aria-label="选择论文">
            {papers.map((paper) => (
              <button
                key={paper.snapshot!.id}
                type="button"
                aria-current={paper.snapshot!.id === activeSnapshotId ? "page" : undefined}
                onClick={() => selectPaper(paper.snapshot!.id)}
              >
                <strong>{paperTitle(paper)}</strong>
              </button>
            ))}
            {!papers.length && !directoryLoading ? <p>{directoryError || (paperPageInfo.hasMore ? "本页没有匹配，可继续下一页。" : "没有匹配的论文。")}</p> : null}
          </nav>
          {papers.length || paperPageInfo.hasMore || paperPage > 1 ? <PaginationControls cursorPagination hasMore={paperPageInfo.hasMore} page={paperPageInfo.page} pageSize={paperPageInfo.pageSize} total={paperPageInfo.total} disabled={directoryLoading} itemLabel="篇论文" pageSizes={VISUAL_PAGE_SIZES} onPageChange={(page) => { setPaperPage(page); resetVisualLedger(); }} onPageSizeChange={(size) => { setPaperPageSize(size); setPaperPage(1); resetVisualLedger(); }} /> : null}
        </aside>

        <section className="visual-library-main" aria-live="polite">
          <div className="visual-paper-mobile-select">
            <form onSubmit={(event) => { event.preventDefault(); setPaperPage(1); resetVisualLedger(); setQuery(queryDraft.trim()); }}>
              <label htmlFor="visual-paper-mobile-search">搜索论文</label>
              <div className="visual-paper-search-field"><UiIcon name="search" size={16} /><input id="visual-paper-mobile-search" type="search" value={queryDraft} onChange={(event) => setQueryDraft(event.target.value)} placeholder="输入论文名或文件名" /></div>
              <button className="button secondary" type="submit">搜索</button>
            </form>
            {papers.length ? <><label htmlFor="visual-paper-select">选择本页论文</label><select id="visual-paper-select" value={activeSnapshotId} onChange={(event) => selectPaper(event.target.value)}>{papers.map((paper) => <option key={paper.snapshot!.id} value={paper.snapshot!.id}>{paperTitle(paper)}</option>)}</select></> : null}
            {papers.length || paperPageInfo.hasMore || paperPage > 1 ? <PaginationControls cursorPagination hasMore={paperPageInfo.hasMore} page={paperPageInfo.page} pageSize={paperPageInfo.pageSize} total={paperPageInfo.total} disabled={directoryLoading} itemLabel="篇论文" pageSizes={VISUAL_PAGE_SIZES} onPageChange={(page) => { setPaperPage(page); resetVisualLedger(); }} onPageSizeChange={(size) => { setPaperPageSize(size); setPaperPage(1); resetVisualLedger(); }} /> : null}
          </div>

          {directoryLoading && !papers.length ? (
            <div className="visual-library-state view-loading"><span />正在读取论文目录…</div>
          ) : !papers.length ? (
            <div className="visual-library-state view-empty"><h2>{directoryError ? "论文目录暂时无法读取" : paperPageInfo.hasMore ? "本页没有匹配，可继续下一页" : query ? "没有匹配的论文" : "还没有可查看的论文资料"}</h2>{directoryError ? <p>{directoryError}</p> : null}</div>
          ) : activePaper ? (
            <>
              <header className="visual-document-header">
                <div>
                  <span>当前论文</span>
                  <h2>{paperTitle(activePaper)}</h2>
                </div>
                <div className="visual-document-actions">
                  {extraction ? <p><strong>{extraction.stats.page_count}</strong> 页 · <strong>{figureCount}</strong> 幅图 · <strong>{tableCount}</strong> 个表格</p> : null}
                  <div className="visual-document-action-buttons">
                    {version?.media_type === "application/pdf" ? <PdfPagePreview sha256={version.sha256} page={1} title={sourceTitle} triggerLabel="打开原始 PDF" triggerClassName="text-button" /> : null}
                    <button className="text-button" type="button" onClick={() => onOpenSnapshot(activeSnapshotId)}>查看论文证据</button>
                  </div>
                </div>
              </header>

              <div className="visual-content-tabs" role="tablist" aria-label="论文资料类型">
                {CONTENT_TABS.map((tab) => (
                  <button
                    id={`visual-content-tab-${tab.value}`}
                    key={tab.value}
                    type="button"
                    role="tab"
                    aria-controls="visual-document-content"
                    aria-selected={contentTab === tab.value}
                    tabIndex={contentTab === tab.value ? 0 : -1}
                    onClick={() => setContentTab(tab.value)}
                    onKeyDown={(event) => handleContentTabKeyDown(event, tab.value)}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>

              <section id="visual-document-content" className={`visual-document-panel ${contentTab}`} role="tabpanel" aria-labelledby={`visual-content-tab-${contentTab}`}>
                {contentTab === "metadata" ? (
                  metadataLoading ? (
                    <div className="visual-library-state view-loading"><span />正在读取论文元数据…</div>
                  ) : metadataError ? (
                    <div className="visual-library-state view-empty" role="alert"><h2>论文元数据暂时无法读取</h2><p>{metadataError}</p></div>
                  ) : extraction ? (
                    <div className="visual-metadata-panel">
                      <dl className="visual-metadata-list">
                        {METADATA_FIELDS.map((field) => (
                          <div className="visual-metadata-row" key={field.value}>
                            <dt>{field.label}</dt>
                            <dd>
                              <MetadataValue
                                field={field.value}
                                source={extraction.field_sources[field.value]}
                                value={extraction.metadata[field.value]}
                              />
                            </dd>
                          </div>
                        ))}
                      </dl>
                    </div>
                  ) : null
                ) : contentTab === "text" ? (
                  textLoading ? (
                    <div className="visual-library-state view-loading"><span />正在读取论文正文…</div>
                  ) : textError ? (
                    <div className="visual-library-state view-empty" role="alert"><h2>论文正文暂时无法读取</h2><p>{textError}</p></div>
                  ) : visibleTextPage?.markdown.trim() ? (
                    <article className="visual-document-body"><ScientificContent value={visibleTextPage.markdown} /></article>
                  ) : visibleTextPage ? (
                    <div className="visual-library-state view-empty"><h2>这篇论文没有可显示的正文</h2></div>
                  ) : null
                ) : (
                  <>
                    <div className="visual-kind-filter" role="group" aria-label="按图表类型筛选">
                      {([
                        ["all", "全部", totalVisualCount],
                        ["figure", "图", figureCount],
                        ["table", "表格", tableCount],
                      ] as const).map(([value, label, count]) => (
                        <button
                          key={value}
                          type="button"
                          aria-pressed={filter === value}
                          onClick={() => { setFilter(value); setVisualPage(1); }}
                        >
                          {label}<span>{count}</span>
                        </button>
                      ))}
                    </div>

                    {visualLoading ? (
                      <div className="visual-library-state view-loading"><span />正在读取本页图表…</div>
                    ) : visualError ? (
                      <div className="visual-library-state view-empty" role="alert"><h2>论文图表暂时无法读取</h2><p>{visualError}</p></div>
                    ) : visibleVisuals.length && visibleVisualPage ? (
                      <div className="visual-record-ledger">
                        {visibleVisuals.map((record, index) => {
                          const region = record.region ? { ...record.region, unit: "normalized" as const } : null;
                          const page = record.page;
                          const hasPdfCrop = version?.media_type === "application/pdf" && region;
                          const recordNumber = String((currentVisualPage - 1) * visualPageSize + index + 1).padStart(2, "0");
                          return (
                            <article className={`visual-record ${record.kind}`} key={`${record.kind}:${record.block_id}`}>
                              <header>
                                <div>
                                  <span>{visualMarker(record, recordNumber)}</span>
                                  <h3>{record.label}</h3>
                                </div>
                                <small>第 {page} 页</small>
                              </header>
                              <div className={`visual-record-body${record.visual ? " has-transcript" : ""}`}>
                                <section className="visual-original-panel" aria-label={`${record.label} 原件`}>
                                  <div className="visual-panel-heading">
                                    <strong>{record.kind === "figure" ? "原图" : "原表"}</strong>
                                    {version?.media_type === "application/pdf" ? <PdfPagePreview sha256={version.sha256} page={page} title={sourceTitle} triggerLabel="打开原始页" /> : null}
                                  </div>
                                  {hasPdfCrop && region && version ? (
                                    <PdfRegionPreview
                                      url={`/api/artifacts/${version.sha256}?disposition=inline`}
                                      page={page}
                                      region={region}
                                      label={record.label}
                                      presentation="gallery"
                                      minimumContentWidth={record.kind === "table" ? 720 : undefined}
                                    />
                                  ) : version ? (
                                    <SourceMediaPreview mediaType={version.media_type} sha256={version.sha256} title={sourceTitle} />
                                  ) : null}
                                </section>
                                {record.visual ? (
                                  <section className="visual-transcript-panel" aria-label={`${record.label} 结构化内容`}>
                                    <div className="visual-panel-heading">
                                      <strong>{record.kind === "table" ? "表格转写" : "图表数据"}</strong>
                                      <span>Markdown / LaTeX</span>
                                    </div>
                                    <StructuredVisualEvidenceView visual={record.visual} />
                                  </section>
                                ) : null}
                              </div>
                              {record.caption || record.footnote ? (
                                <div className="visual-record-caption">
                                  <span>题注与脚注</span>
                                  <div className="visual-record-note-content">
                                    {record.caption ? <div><strong>题注</strong><ScientificContent value={record.caption} /></div> : null}
                                    {record.footnote ? <div><strong>脚注</strong><ScientificContent value={record.footnote} /></div> : null}
                                  </div>
                                </div>
                              ) : null}
                            </article>
                          );
                        })}
                        <PaginationControls page={currentVisualPage} pageSize={visualPageSize} total={visibleVisualPage.pageInfo.total} disabled={visualLoading} itemLabel="项图表" pageSizes={VISUAL_PAGE_SIZES} onPageChange={setVisualPage} onPageSizeChange={(size) => { setVisualPageSize(size); setVisualPage(1); }} />
                      </div>
                    ) : visibleVisualPage ? (
                      <div className="visual-library-state view-empty">
                        <h2>{filter === "figure" ? "这篇论文没有解析出的图" : filter === "table" ? "这篇论文没有解析出的表格" : "这篇论文没有解析出的图表"}</h2>
                      </div>
                    ) : null}
                  </>
                )}
              </section>
            </>
          ) : null}
        </section>
      </div>
    </section>
  );
}
