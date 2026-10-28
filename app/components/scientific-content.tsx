"use client";

import type { ComponentProps, ComponentPropsWithoutRef } from "react";
import Image from "next/image";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import type { Root, RootContent } from "hast";
import type { SourceAnchor, StructuredVisualEvidence } from "@/lib/evidence-graph";
import { prepareScientificMarkdown } from "../scientific-markdown";
import { PdfPagePreview } from "./pdf-page-preview";
import { PdfRegionPreview } from "./pdf-region-preview";
import { useSourceFormulaContext } from "./source-formula-context";

export type ScientificContentProps = {
  value: string;
  inline?: boolean;
  className?: string;
};

const markdownPlugins: ComponentProps<typeof ReactMarkdown>["remarkPlugins"] = [
  [remarkGfm, { singleTilde: false }],
  remarkMath,
];
function neutralMathFallback() {
  return (tree: Root) => {
    function visit(node: Root | RootContent) {
      if (node.type === "element" && Array.isArray(node.properties.className)
        && node.properties.className.includes("katex-error")) {
        node.tagName = "code";
        node.properties = {
          className: ["scientific-math-fragment"],
          title: "公式片段无法完整显示，已保留原文；请对照原始页。",
        };
      }
      if ("children" in node) node.children.forEach(visit);
    }
    visit(tree);
  };
}
// Paper and model text is untrusted. KaTeX's default maxSize is unbounded, so
// `\rule{2000em}{2000em}` could paint over the page; cap user sizes and keep
// URL/HTML commands (\href, \url, \htmlClass…) disabled.
const katexOptions = { maxSize: 10, maxExpand: 1000, trust: false } as const;
const htmlPlugins: ComponentProps<typeof ReactMarkdown>["rehypePlugins"] = [
  [rehypeKatex, katexOptions],
  neutralMathFallback,
];

// react-markdown passes its hast `node`; it must not reach the DOM as an attribute.
type MarkdownElementProps<Tag extends "a" | "table"> = ComponentPropsWithoutRef<Tag> & { node?: unknown };

function withoutNode<Props extends { node?: unknown }>(props: Props): Omit<Props, "node"> {
  const copy = { ...props };
  delete copy.node;
  return copy;
}

function SafeLink(allProps: MarkdownElementProps<"a">) {
  const { href, children, ...props } = withoutNode(allProps);
  // react-markdown blanks unsafe URLs (javascript:, data:, …) to "". Render
  // those as plain text instead of a live link that would reopen this page.
  if (!href) return <span {...props}>{children}</span>;
  return (
    <a {...props} href={href} target="_blank" rel="noreferrer noopener">
      {children}
    </a>
  );
}

function MarkdownTable(allProps: MarkdownElementProps<"table">) {
  const { children, ...props } = withoutNode(allProps);
  return (
    <div className="scientific-table-scroll" tabIndex={0} aria-label="论文表格，可横向滚动">
      <table {...props}>{children}</table>
    </div>
  );
}

/** Render model- and paper-derived prose without executing embedded HTML. */
export function ScientificContent({ value, inline = false, className = "" }: ScientificContentProps) {
  if (!value) return null;
  const Wrapper = inline ? "span" : "div";
  const { markdown, incompleteMath } = prepareScientificMarkdown(value, inline);
  return (
    <Wrapper className={`scientific-content${inline ? " inline" : ""}${className ? ` ${className}` : ""}`}>
      <ReactMarkdown
        remarkPlugins={markdownPlugins}
        rehypePlugins={htmlPlugins}
        skipHtml
        components={inline ? {
          a: SafeLink,
          p: ({ children }) => <span>{children}</span>,
          img: ({ alt }) => <span className="markdown-image-reference">图像引用：{alt || "未命名图像"}</span>,
        } : {
          a: SafeLink,
          img: ({ alt }) => <span className="markdown-image-reference">图像引用：{alt || "未命名图像"}</span>,
          table: MarkdownTable,
        }}
      >
        {markdown}
      </ReactMarkdown>
      {incompleteMath ? <small className="scientific-math-note">公式片段不完整，见原页</small> : null}
    </Wrapper>
  );
}

function tableRows(visual: StructuredVisualEvidence) {
  const rows = [...new Set(visual.cells.map((cell) => cell.row))].sort((left, right) => left - right);
  const columns = [...new Set(visual.cells.map((cell) => cell.column))];
  const cells = new Map(visual.cells.map((cell) => [`${cell.row}\u0000${cell.column}`, cell]));
  return { rows, columns, cells };
}

function collapsedTableRows(visual: StructuredVisualEvidence) {
  const rowCount = new Set(visual.cells.map((cell) => cell.row)).size;
  return visual.cells.length <= rowCount + 1 && visual.cells.some((cell) => (
    (cell.raw_value.match(/[+-]?(?:\d+(?:\.\d*)?|\.\d+)/g)?.length ?? 0) >= 4
  ));
}

export type StructuredVisualEvidenceViewProps = {
  visual: StructuredVisualEvidence;
  riskHighlight?: { rows: number[]; columns: string[]; cells: string[] };
};

export function StructuredVisualEvidenceView({ visual, riskHighlight }: StructuredVisualEvidenceViewProps) {
  if (visual.visual_kind === "table" && visual.cells.length) {
    if (collapsedTableRows(visual)) {
      return (
        <figure className="structured-visual table-visual degraded">
          <p className="visual-quality-note">表格内容按行转写；列结构请对照原表核验。</p>
          <ol>
            {visual.cells.map((cell) => (
              <li key={`${cell.row}:${cell.column}`} data-risk={riskHighlight?.rows.includes(cell.row) || riskHighlight?.columns.includes(cell.column) ? "true" : undefined}>
                <span>第 {cell.row} 行</span>
                <ScientificContent value={`${cell.raw_value}${cell.unit && !cell.raw_value.includes(cell.unit) ? ` ${cell.unit}` : ""}`} />
              </li>
            ))}
          </ol>
        </figure>
      );
    }
    const { rows, columns, cells } = tableRows(visual);
    return (
      <figure className="structured-visual table-visual">
        <div className="structured-table-scroll" tabIndex={0} aria-label="结构化表格证据，可横向滚动">
          <table>
            <thead><tr><th scope="col">行</th>{columns.map((column) => <th scope="col" data-risk={riskHighlight?.columns.includes(column) ? "true" : undefined} key={column}><ScientificContent value={column} inline /></th>)}</tr></thead>
            <tbody>
              {rows.map((row) => (
                <tr data-risk={riskHighlight?.rows.includes(row) ? "true" : undefined} key={row}>
                  <th scope="row" data-risk={riskHighlight?.rows.includes(row) ? "true" : undefined}>{row}</th>
                  {columns.map((column) => {
                    const cell = cells.get(`${row}\u0000${column}`);
                    const risky = riskHighlight?.cells.includes(`${row}\u0000${column}`) || riskHighlight?.columns.includes(column) || riskHighlight?.rows.includes(row);
                    return <td data-risk={risky ? "true" : undefined} key={column}>{cell ? <ScientificContent value={`${cell.raw_value}${cell.unit ? ` ${cell.unit}` : ""}`} inline /> : "—"}</td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <figcaption>已从论文表格中识别 {visual.cells.length} 个单元格；关键数值仍需对照原件核验。</figcaption>
      </figure>
    );
  }

  return (
    <figure className="structured-visual figure-visual">
      <div className="visual-summary-grid">
        {visual.axes.length ? (
          <section><h4>坐标轴</h4><ul>{visual.axes.map((axis) => <li key={axis.id}>{axis.direction.toUpperCase()} · <ScientificContent value={`${axis.label}${axis.unit ? ` (${axis.unit})` : ""}`} inline /></li>)}</ul></section>
        ) : null}
        {visual.series.length ? (
          <section><h4>数据系列</h4><ul>{visual.series.map((series) => <li key={series.id}><ScientificContent value={series.label} inline /><span>{series.points.length} 个点</span></li>)}</ul></section>
        ) : null}
        {visual.features.length ? (
          <section><h4>可核验特征</h4><ul>{visual.features.map((feature, index) => <li key={`${feature.kind}:${index}`}><ScientificContent value={feature.description} inline /></li>)}</ul></section>
        ) : null}
      </div>
      <figcaption>以下内容为图表结构摘要，关键结论请对照原图核验。</figcaption>
    </figure>
  );
}

export type SourceAnchorViewProps = {
  anchor: SourceAnchor;
  mediaType?: string;
  sha256?: string;
  sourceTitle?: string;
  showVisualExcerpt?: boolean;
};

export function SourceAnchorView({
  anchor,
  mediaType,
  sha256,
  sourceTitle = "原始文献",
  showVisualExcerpt = true,
}: SourceAnchorViewProps) {
  const formulaContext = useSourceFormulaContext(anchor.id);
  const location = [
    anchor.selector.page ? `第 ${anchor.selector.page} 页` : null,
    anchor.selector.section,
    anchor.selector.figure,
    anchor.selector.table,
  ].filter(Boolean).join(" · ") || "原文位置";
  const page = anchor.selector.page;
  const visualRegion = mediaType === "application/pdf"
    && page
    && anchor.selector.region?.unit === "normalized"
    && ["figure", "table"].includes(anchor.anchor_type)
    ? anchor.selector.region
    : null;

  return (
    <div className="source-anchor-content">
      <div className="source-anchor-heading">
        <ScientificContent value={location} inline />
        {sha256 && mediaType === "application/pdf" ? (
          <PdfPagePreview sha256={sha256} page={page} title={sourceTitle} />
        ) : sha256 ? (
          <a href={`/api/artifacts/${sha256}`} target="_blank" rel="noreferrer">打开原始图像</a>
        ) : null}
      </div>
      {anchor.quote
        ? <><ScientificContent value={formulaContext ?? anchor.quote} />
          {formulaContext ? <small className="scientific-math-note" title="已展开相邻文字，以完整显示公式；原始引用记录保持不变。">含相邻公式上下文</small> : null}</>
        : <p className="muted">该位置对应图表区域，不含可直接核对的文本引文。</p>}
      {showVisualExcerpt && visualRegion && sha256 && page ? (
        <figure className="original-visual-excerpt">
          <PdfRegionPreview
            url={`/api/artifacts/${sha256}?disposition=inline`}
            page={page}
            region={visualRegion}
            label={anchor.selector.figure ?? anchor.selector.table ?? location}
          />
          <figcaption>原 PDF 第 {page} 页的定位区域。点击“打开原始页”可放大核对上下文。</figcaption>
        </figure>
      ) : null}
    </div>
  );
}

export type SourceMediaPreviewProps = { mediaType: string; sha256: string; title: string };

export function SourceMediaPreview({ mediaType, sha256, title }: SourceMediaPreviewProps) {
  const url = `/api/artifacts/${sha256}`;
  if (mediaType.startsWith("image/")) {
    return <figure className="source-media-preview"><Image src={url} alt={title} width={1600} height={1200} sizes="(max-width: 832px) 100vw, 70vw" unoptimized /><figcaption>上传的原始图像</figcaption></figure>;
  }
  if (mediaType === "application/pdf") {
    return (
      <div className="source-document-actions">
        <PdfPagePreview sha256={sha256} page={1} title={title} triggerLabel="预览 PDF" />
      </div>
    );
  }
  return null;
}
