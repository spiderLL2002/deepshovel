"use client";

import { useEffect, useRef, useState } from "react";
import type { AnchorRegion } from "@/lib/evidence-graph";
import { acquireDocument, loadPdfModule } from "./pdf-document";

type Props = {
  url: string;
  page: number;
  region: AnchorRegion;
  label: string;
  presentation?: "evidence" | "gallery";
  minimumContentWidth?: number;
};

/** Render only the cited normalized page region; the original PDF remains authoritative. */
export function PdfRegionPreview({
  url,
  page,
  region,
  label,
  presentation = "evidence",
  minimumContentWidth = 0,
}: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [visible, setVisible] = useState(false);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    if (!("IntersectionObserver" in window)) {
      const frame = requestAnimationFrame(() => setVisible(true));
      return () => cancelAnimationFrame(frame);
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: "320px 0px" },
    );
    observer.observe(host);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!visible) return;
    const host = hostRef.current;
    const canvas = canvasRef.current;
    if (!host || !canvas) return;

    let disposed = false;
    let renderTask: { cancel(): void; promise: Promise<unknown> } | null = null;
    let animationFrame = 0;
    let renderGeneration = 0;
    let hasRendered = false;

    const render = async (generation: number) => {
      if (disposed || generation !== renderGeneration) return;
      if (!hasRendered) setState("loading");
      const documentLease = acquireDocument(url);
      try {
        const pdf = await documentLease.promise;
        if (disposed || generation !== renderGeneration) return;
        if (page < 1 || page > pdf.numPages) throw new Error("Page is outside the PDF");
        const pdfPage = await pdf.getPage(page);
        if (disposed || generation !== renderGeneration) return;
        const base = pdfPage.getViewport({ scale: 1 });
        const gallery = presentation === "gallery";
        const availableWidth = Math.max(
          240,
          Math.min(Math.max(host.clientWidth, minimumContentWidth), gallery ? 1120 : 720),
        );
        const availableHeight = gallery
          ? Math.min(720, Math.max(240, window.innerHeight * 0.68))
          : Math.min(320, Math.max(128, window.innerHeight * 0.34));
        const widthScale = availableWidth / (base.width * region.width);
        const heightScale = availableHeight / (base.height * region.height);
        const viewport = pdfPage.getViewport({ scale: Math.min(widthScale, heightScale) });
        const cssWidth = Math.max(1, Math.floor(viewport.width * region.width));
        const cssHeight = Math.max(1, Math.floor(viewport.height * region.height));
        const outputScale = Math.min(window.devicePixelRatio || 1, 2);
        const nextCanvas = document.createElement("canvas");

        nextCanvas.width = Math.max(1, Math.floor(cssWidth * outputScale));
        nextCanvas.height = Math.max(1, Math.floor(cssHeight * outputScale));

        const annotationMode = (await loadPdfModule()).AnnotationMode.DISABLE;
        if (disposed || generation !== renderGeneration) return;
        renderTask?.cancel();
        const nextRenderTask = pdfPage.render({
          canvas: nextCanvas,
          viewport,
          annotationMode,
          background: "rgb(255, 255, 255)",
          transform: [
            outputScale,
            0,
            0,
            outputScale,
            -region.x * viewport.width * outputScale,
            -region.y * viewport.height * outputScale,
          ],
        });
        renderTask = nextRenderTask;
        await nextRenderTask.promise;
        if (!disposed && generation === renderGeneration) {
          canvas.width = nextCanvas.width;
          canvas.height = nextCanvas.height;
          canvas.style.width = `${cssWidth}px`;
          canvas.style.height = `${cssHeight}px`;
          canvas.getContext("2d")?.drawImage(nextCanvas, 0, 0);
          hasRendered = true;
          setState("ready");
        }
      } catch (error) {
        if (!disposed && generation === renderGeneration
          && !(error instanceof Error && error.name === "RenderingCancelledException")) {
          if (!hasRendered) setState("error");
        }
      } finally {
        documentLease.release();
      }
    };

    const schedule = () => {
      renderGeneration += 1;
      renderTask?.cancel();
      cancelAnimationFrame(animationFrame);
      const generation = renderGeneration;
      animationFrame = requestAnimationFrame(() => void render(generation));
    };
    let observedWidth = host.clientWidth;
    const resize = "ResizeObserver" in window
      ? new ResizeObserver(() => {
        const width = host.clientWidth;
        if (width === observedWidth) return;
        observedWidth = width;
        schedule();
      })
      : null;
    resize?.observe(host);
    schedule();
    return () => {
      disposed = true;
      renderGeneration += 1;
      cancelAnimationFrame(animationFrame);
      resize?.disconnect();
      renderTask?.cancel();
    };
  }, [minimumContentWidth, page, presentation, region.height, region.width, region.x, region.y, url, visible]);

  return (
    <div
      className={`pdf-region-preview ${presentation} ${minimumContentWidth ? "scrollable" : ""} ${state}`}
      ref={hostRef}
      aria-label={`${label} 的原件区域`}
      tabIndex={minimumContentWidth ? 0 : undefined}
    >
      {state === "loading" ? <span className="pdf-region-loading" aria-live="polite">正在读取原件区域…</span> : null}
      <canvas ref={canvasRef} role="img" aria-label={`${label}，截取自原 PDF 第 ${page} 页`} />
      {state === "error" ? <p>暂时无法生成区域预览，请打开原始页核对。</p> : null}
    </div>
  );
}
