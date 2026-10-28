"use client";

import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { UiIcon } from "./ui-icon";
import { acquireDocument, loadPdfModule } from "./pdf-document";

export type PdfPagePreviewProps = {
  sha256: string;
  page?: number | null;
  title: string;
  triggerLabel?: string;
  triggerClassName?: string;
};

function pageNumber(value: number | null | undefined) {
  return Number.isSafeInteger(value) && Number(value) > 0 ? Number(value) : 1;
}

export function pdfInlineUrl(sha256: string, page?: number | null) {
  return `/api/artifacts/${encodeURIComponent(sha256)}?disposition=inline#page=${pageNumber(page)}&zoom=page-fit`;
}

export function PdfPagePreview({
  sha256,
  page,
  title,
  triggerLabel = "打开原始页",
  triggerClassName = "pdf-preview-trigger",
}: PdfPagePreviewProps) {
  const [open, setOpen] = useState(false);
  const headingId = useId();
  const descriptionId = useId();
  const dialog = useRef<HTMLElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const selectedPage = pageNumber(page);
  const sourceUrl = pdfInlineUrl(sha256, selectedPage);

  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.documentElement.style.overflow;
    document.documentElement.style.overflow = "hidden";
    closeButton.current?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = [...(dialog.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
      ) ?? [])].filter((element) => !element.hidden);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable.at(-1) ?? first;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      document.documentElement.style.overflow = previousOverflow;
      returnFocus.current?.focus();
    };
  }, [open]);

  return (
    <>
      <button
        type="button"
        className={triggerClassName}
        onClick={(event) => {
          returnFocus.current = event.currentTarget;
          setOpen(true);
        }}
      >
        {triggerLabel}
      </button>
      {open ? createPortal(
        <div
          className="pdf-preview-layer"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setOpen(false);
          }}
        >
          <section
            ref={dialog}
            className="pdf-preview-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby={headingId}
            aria-describedby={descriptionId}
          >
            <header className="pdf-preview-header">
              <div>
                <h2 id={headingId}>{title}</h2>
                <p id={descriptionId}>已定位至第 {selectedPage} 页 · 可缩放、翻页和旋转原件</p>
              </div>
              <div className="pdf-preview-actions">
                <a href={sourceUrl} target="_blank" rel="noreferrer noopener">打开完整原件<UiIcon name="external" size={15} /></a>
                <button
                  ref={closeButton}
                  className="icon-button"
                  type="button"
                  aria-label="关闭 PDF 预览"
                  onClick={() => setOpen(false)}
                >
                  <UiIcon name="close" />
                </button>
              </div>
            </header>
            <PdfPageViewer url={`/api/artifacts/${encodeURIComponent(sha256)}?disposition=inline`} initialPage={selectedPage} title={title} />
          </section>
        </div>,
        document.body,
      ) : null}
    </>
  );
}

function PdfPageViewer({ url, initialPage, title }: { url: string; initialPage: number; title: string }) {
  const [page, setPage] = useState(initialPage);
  const [pageCount, setPageCount] = useState<number | null>(null);
  const [zoom, setZoom] = useState(1);
  const [rotation, setRotation] = useState(0);
  const [retry, setRetry] = useState(0);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const host = hostRef.current;
    const canvas = canvasRef.current;
    if (!host || !canvas) return;
    let disposed = false;
    let generation = 0;
    let frame = 0;
    let renderTask: { cancel(): void; promise: Promise<unknown> } | null = null;
    const render = async (currentGeneration: number) => {
      const isCurrent = () => !disposed && currentGeneration === generation;
      if (!isCurrent()) return;
      setState("loading");
      const lease = acquireDocument(url);
      try {
        const pdf = await lease.promise;
        if (!isCurrent()) return;
        setPageCount(pdf.numPages);
        const requestedPage = Math.min(Math.max(1, page), pdf.numPages);
        if (requestedPage !== page) { setPage(requestedPage); return; }
        const pdfPage = await pdf.getPage(requestedPage);
        const pdfjs = await loadPdfModule();
        if (!isCurrent()) return;
        const angle = (pdfPage.rotate + rotation) % 360;
        const base = pdfPage.getViewport({ scale: 1, rotation: angle });
        const fit = Math.min(Math.max(1, host.clientWidth - 32) / base.width, Math.max(1, host.clientHeight - 32) / base.height);
        const viewport = pdfPage.getViewport({ scale: fit * zoom, rotation: angle });
        const scale = Math.min(window.devicePixelRatio || 1, 2);
        const buffer = document.createElement("canvas");
        buffer.width = Math.max(1, Math.floor(viewport.width * scale));
        buffer.height = Math.max(1, Math.floor(viewport.height * scale));
        const task = pdfPage.render({
          canvas: buffer, viewport, annotationMode: pdfjs.AnnotationMode.DISABLE,
          background: "rgb(255, 255, 255)", transform: [scale, 0, 0, scale, 0, 0],
        });
        renderTask = task;
        await task.promise;
        if (!isCurrent()) return;
        canvas.width = buffer.width;
        canvas.height = buffer.height;
        canvas.style.width = `${viewport.width}px`;
        canvas.style.height = `${viewport.height}px`;
        canvas.getContext("2d")?.drawImage(buffer, 0, 0);
        setState("ready");
      } catch (error) {
        if (isCurrent() && !(error instanceof Error && error.name === "RenderingCancelledException")) setState("error");
      } finally {
        lease.release();
      }
    };
    const schedule = () => {
      generation += 1;
      renderTask?.cancel();
      cancelAnimationFrame(frame);
      const nextGeneration = generation;
      frame = requestAnimationFrame(() => void render(nextGeneration));
    };
    let width = host.clientWidth;
    let height = host.clientHeight;
    const observer = new ResizeObserver(() => {
      if (host.clientWidth === width && host.clientHeight === height) return;
      width = host.clientWidth;
      height = host.clientHeight;
      schedule();
    });
    observer.observe(host);
    schedule();
    return () => {
      disposed = true;
      generation += 1;
      observer.disconnect();
      cancelAnimationFrame(frame);
      renderTask?.cancel();
    };
  }, [url, page, zoom, rotation, retry]);

  return (
    <div className="pdf-page-viewer">
      <div className="pdf-page-toolbar" role="toolbar" aria-label="PDF 页面工具">
        <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button>
        <span aria-live="polite">第 {page} / {pageCount ?? "…"} 页</span>
        <button type="button" disabled={!pageCount || page >= pageCount} onClick={() => setPage(page + 1)}>下一页</button>
        <button type="button" disabled={zoom <= 0.5} onClick={() => setZoom(Math.max(0.5, zoom - 0.25))}>缩小</button>
        <span>{Math.round(zoom * 100)}%</span>
        <button type="button" disabled={zoom >= 3} onClick={() => setZoom(Math.min(3, zoom + 0.25))}>放大</button>
        <button type="button" onClick={() => setZoom(1)}>适合页面</button>
        <button type="button" onClick={() => setRotation((rotation + 90) % 360)}>旋转 90°</button>
      </div>
      <div className={`pdf-page-canvas-host ${state}`} ref={hostRef} tabIndex={0} aria-label="PDF 页面，可滚动查看">
        <canvas ref={canvasRef} role="img" aria-label={`${title}，第 ${page} 页，旋转 ${rotation}°`} />
        {state === "loading" ? <p className="pdf-page-status" role="status">正在读取原始页…</p> : null}
        {state === "error" ? <div className="pdf-page-status" role="alert"><p>原始页加载失败，请重试或打开完整原件。</p><button type="button" onClick={() => setRetry(retry + 1)}>重试加载</button></div> : null}
      </div>
    </div>
  );
}
