"use client";

import { useEffect, useRef, useState } from "react";
import type { SnapshotDetailDto, SnapshotSummaryDto } from "../workspace-types";
import { formatReviewDate } from "../workspace-types";
import { ScientificContent } from "./scientific-content-lazy";
import { ClaimListDisclosure, ExpandableClaim } from "./claim-disclosure";
import { PaginationControls } from "./pagination-controls";

type HumanDecision = "accepted" | "rejected" | "needs_revision" | "withdrawn";

type Props = {
  snapshots: SnapshotSummaryDto[];
  query: string;
  filter: ReviewFilter;
  pageInfo: { page: number; pageSize: number; total: number | null; totalPages: number | null; hasMore: boolean };
  listLoading: boolean;
  selectedId: string | null;
  detail: SnapshotDetailDto | null;
  loading: boolean;
  disabled: boolean;
  sourceTitle: (snapshot: SnapshotSummaryDto) => string;
  onSelect: (snapshotId: string) => void;
  onReview: (snapshotId: string, decision: HumanDecision, rationale: string) => Promise<void>;
  onOpenPaper: (snapshotId: string) => void;
  onOpenLibrary: () => void;
  onSearch: (query: string) => void;
  onFilterChange: (filter: ReviewFilter) => void;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
};

export type ReviewFilter = "all" | "unreviewed" | "confirmed";

function hasActiveReview(snapshot: SnapshotSummaryDto) {
  const decision = snapshot.latestReview?.decision;
  return Boolean(decision && decision !== "withdrawn");
}

function reviewLabel(snapshot: SnapshotSummaryDto) {
  if (snapshot.latestReview?.decision === "accepted") return "人工复核已通过";
  if (snapshot.latestReview?.decision === "needs_revision") return "人工复核要求修订";
  if (snapshot.latestReview?.decision === "rejected") return "人工复核发现问题";
  return "尚未进行人工复核";
}

function reviewDraftKey(snapshotId: string) {
  return `deepshovel.review-draft:${snapshotId}`;
}

function readReviewDraft(snapshotId: string) {
  try {
    return typeof window === "undefined" ? "" : window.sessionStorage.getItem(reviewDraftKey(snapshotId)) ?? "";
  } catch {
    return "";
  }
}

function writeReviewDraft(snapshotId: string, value: string | null) {
  try {
    if (value === null) window.sessionStorage.removeItem(reviewDraftKey(snapshotId));
    else window.sessionStorage.setItem(reviewDraftKey(snapshotId), value);
  } catch {
    // The in-memory draft remains usable when browser storage is unavailable.
  }
}

export function ReviewWorkspace({ snapshots, query, filter, pageInfo, listLoading, selectedId, detail, loading, disabled, sourceTitle, onSelect, onReview, onOpenPaper, onOpenLibrary, onSearch, onFilterChange, onPageChange, onPageSizeChange }: Props) {
  const [searchDraft, setSearchDraft] = useState(query);
  const [rationaleDraft, setRationaleDraft] = useState<{ snapshotId: string | null; value: string }>(() => ({
    snapshotId: selectedId,
    value: selectedId ? readReviewDraft(selectedId) : "",
  }));
  const [submitting, setSubmitting] = useState(false);
  const [claimDisclosure, setClaimDisclosure] = useState({ key: "", expanded: false });
  const [savedNotice, setSavedNotice] = useState<{ snapshotId: string | null; value: string }>({ snapshotId: null, value: "" });
  const reviewInFlight = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const selected = snapshots.find((snapshot) => snapshot.id === selectedId)
    ?? (detail?.snapshot.id === selectedId ? detail.snapshot : null);
  const claimsExpanded = claimDisclosure.key === selectedId && claimDisclosure.expanded;
  const rationale = selectedId
    ? rationaleDraft.snapshotId === selectedId ? rationaleDraft.value : readReviewDraft(selectedId)
    : "";
  const visibleSavedNotice = savedNotice.snapshotId === selectedId ? savedNotice.value : "";
  const rationaleLength = rationale.trim().length;
  const rationaleInvalid = rationaleLength < 3 || rationaleLength > 4000;
  const submit = async (decision: HumanDecision) => {
    const note = rationale.trim();
    if (note.length < 3 || note.length > 4000 || !selected || reviewInFlight.current) return;
    reviewInFlight.current = true;
    setSubmitting(true);
    try {
      await onReview(selected.id, decision, note);
      if (mounted.current) {
        writeReviewDraft(selected.id, null);
        setRationaleDraft({ snapshotId: selected.id, value: "" });
        setSavedNotice({ snapshotId: selected.id, value: "复核记录已保存。" });
      }
    } catch {
      // The workspace keeps the error visible; preserve the researcher's note.
    } finally {
      reviewInFlight.current = false;
      if (mounted.current) setSubmitting(false);
    }
  };

  return (
    <section className="review-view" aria-labelledby="review-title">
      <header className="view-header page-intro">
        <div>
          <h1 id="review-title">人工复核</h1>
        </div>
      </header>

      <div className="review-filter" role="group" aria-label="人工复核筛选">
        {([
          ["all", "全部"],
          ["unreviewed", "未复核"],
          ["confirmed", "已通过"],
        ] as const).map(([value, label]) => (
          <button type="button" aria-pressed={filter === value} onClick={() => onFilterChange(value)} key={value}>{label}</button>
        ))}
      </div>

      <form className="review-search" onSubmit={(event) => { event.preventDefault(); onSearch(searchDraft.trim()); }}>
        <label htmlFor="review-query">搜索待复核文献</label>
        <div>
          <input id="review-query" value={searchDraft} maxLength={200} placeholder="输入论文名或文件名" onChange={(event) => setSearchDraft(event.target.value)} />
          <button className="button secondary" type="submit">搜索</button>
          {query ? <button className="text-button" type="button" onClick={() => { setSearchDraft(""); onSearch(""); }}>清除</button> : null}
        </div>
        <button className="button secondary review-library-link" type="button" onClick={onOpenLibrary}>在文献库查找论文</button>
      </form>

      {snapshots.length || selected ? (
        <div className="review-split">
          <div className="review-queue" aria-label="可进行人工复核的文献列表">
            {snapshots.map((snapshot) => (
              <button
                className="review-queue-row"
                disabled={disabled}
                aria-current={snapshot.id === selectedId ? "true" : undefined}
                onClick={() => onSelect(snapshot.id)}
                key={snapshot.id}
              >
                <strong>{sourceTitle(snapshot)}</strong>
              </button>
            ))}
            {!snapshots.length && !listLoading ? <div className="queue-empty">{pageInfo.hasMore ? "本页没有匹配，可继续下一页。" : "当前筛选条件下没有文献。"}</div> : null}
            {listLoading ? <div className="view-loading compact"><span />正在读取复核队列…</div> : null}
            {snapshots.length || pageInfo.hasMore || pageInfo.page > 1 ? <PaginationControls cursorPagination hasMore={pageInfo.hasMore} page={pageInfo.page} pageSize={pageInfo.pageSize} total={pageInfo.total} disabled={listLoading} itemLabel="篇文献" onPageChange={onPageChange} onPageSizeChange={onPageSizeChange} /> : null}
          </div>

          <div className="review-inspector" aria-live="polite">
            {loading ? <div className="view-loading"><span />正在读取文献…</div> : selected && detail?.snapshot.id === selected.id ? (
              <>
                <header>
                  <div>
                    <h2>{sourceTitle(selected)}</h2>
                  </div>
                  <button className="text-button" type="button" onClick={() => onOpenPaper(selected.id)}>查看证据详情</button>
                </header>

                <div className="review-claim-preview">
                  {(claimsExpanded ? detail.graph.claims : detail.graph.claims.slice(0, 1)).map((claim) => <ExpandableClaim value={claim.proposition} key={claim.id} />)}
                  <ClaimListDisclosure total={detail.graph.claims.length} expanded={claimsExpanded} onToggle={() => setClaimDisclosure({ key: selectedId ?? "", expanded: !claimsExpanded })} />
                </div>

                {selected.latestReview && selected.latestReview.decision !== "withdrawn" ? (
                  <div className="current-review">
                    <strong>{reviewLabel(selected)}</strong>
                    <ScientificContent value={selected.latestReview.rationale} />
                    <small>{selected.latestReview.reviewer} · {formatReviewDate(selected.latestReview.createdAt)}</small>
                  </div>
                ) : null}

                <form className="review-form" onSubmit={(event) => event.preventDefault()}>
                  <label htmlFor="review-rationale">复核说明</label>
                  <textarea
                    id="review-rationale"
                    value={rationale}
                    onChange={(event) => {
                      const value = event.target.value;
                      setSavedNotice({ snapshotId: selected.id, value: "" });
                      setRationaleDraft({ snapshotId: selected.id, value });
                      writeReviewDraft(selected.id, value);
                    }}
                    maxLength={4000}
                    aria-invalid={rationaleLength > 4000}
                    aria-describedby={rationaleLength > 4000 ? "review-rationale-limit" : undefined}
                    placeholder="请说明已核对的 Claim、Observation 或原文位置，并记录复核依据。"
                  />
                  {rationaleLength > 4000 ? <p id="review-rationale-limit" role="alert">复核说明最多 4000 字，当前 {rationaleLength} 字，请缩短后再提交。</p> : null}
                  <div className="review-actions">
                    <button className="button" type="button" disabled={disabled || submitting || rationaleInvalid} onClick={() => void submit("accepted")}>标记为已通过</button>
                    <button className="button secondary" type="button" disabled={disabled || submitting || rationaleInvalid} onClick={() => void submit("needs_revision")}>标记为需修订</button>
                    <button className="button danger" type="button" disabled={disabled || submitting || rationaleInvalid} onClick={() => void submit("rejected")}>标记为存在问题</button>
                    {hasActiveReview(selected) ? (
                      <button className="text-button" type="button" disabled={disabled || submitting || rationaleInvalid} onClick={() => void submit("withdrawn")}>撤销复核结论</button>
                    ) : null}
                  </div>
                  {submitting || visibleSavedNotice ? <p className="review-save-status" role="status" aria-live="polite">{submitting ? "正在保存复核记录…" : visibleSavedNotice}</p> : null}
                </form>
              </>
            ) : <div className="view-empty compact"><h2>选择一篇文献</h2><p>可根据研究需要，对论文证据进行人工复核。</p></div>}
          </div>
        </div>
      ) : <div className="view-empty"><h2>暂无可复核的文献</h2><p>请先在文献库完成一次 Compile。</p></div>}
    </section>
  );
}
