"use client";

import { useId, useState } from "react";

type Props = {
  page: number;
  pageSize: number;
  total: number | null;
  hasMore?: boolean;
  cursorPagination?: boolean;
  disabled?: boolean;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
  itemLabel?: string;
  pageSizes?: readonly number[];
};

const PAGE_SIZES = [10, 20, 50] as const;

export function PaginationControls({ page, pageSize, total, hasMore = false, cursorPagination = false, disabled = false, onPageChange, onPageSizeChange, itemLabel = "条记录", pageSizes = PAGE_SIZES }: Props) {
  const totalPages = Math.max(1, Math.ceil((total ?? 0) / pageSize));
  const pageInputId = useId();
  const [pageDraft, setPageDraft] = useState({ page, value: String(page) });
  const visibleDraft = pageDraft.page === page ? pageDraft.value : String(page);
  const goToDraftPage = () => {
    const requested = Number.parseInt(visibleDraft, 10);
    const next = Number.isFinite(requested) ? Math.min(totalPages, Math.max(1, requested)) : page;
    setPageDraft({ page: next, value: String(next) });
    if (next !== page) onPageChange(next);
  };

  return (
    <nav className="pagination-controls" aria-label="分页导航">
      <div className="pagination-summary" aria-live="polite">
        <strong>第 {page}{total === null ? "" : ` / ${totalPages}`} 页</strong>
        {total === null ? <span>按需加载{itemLabel}</span> : <span>共 {total.toLocaleString("zh-CN")} {itemLabel}</span>}
      </div>
      <label className="pagination-size">
        <span>每页</span>
        <select value={pageSize} disabled={disabled} onChange={(event) => onPageSizeChange(Number(event.target.value))}>
          {pageSizes.map((size) => <option value={size} key={size}>{size} 条</option>)}
        </select>
      </label>
      <div className="pagination-actions">
        <button className="text-button" type="button" disabled={disabled || page <= 1} onClick={() => onPageChange(page - 1)}>上一页</button>
        {!cursorPagination ? <form onSubmit={(event) => { event.preventDefault(); goToDraftPage(); }}>
          <label htmlFor={pageInputId}>跳至</label>
          <input
            id={pageInputId}
            inputMode="numeric"
            min={1}
            max={totalPages}
            value={visibleDraft}
            disabled={disabled}
            aria-label={`跳转页码，范围 1 到 ${totalPages}`}
            onChange={(event) => setPageDraft({ page, value: event.target.value.replace(/[^0-9]/gu, "") })}
          />
          <button className="button secondary" type="submit" disabled={disabled}>跳转</button>
        </form> : null}
        <button className="text-button" type="button" disabled={disabled || (cursorPagination ? !hasMore : page >= totalPages)} onClick={() => onPageChange(page + 1)}>下一页</button>
      </div>
    </nav>
  );
}
