"use client";

import { useId, useMemo, useState } from "react";
import type { EvidencePath } from "@/lib/evidence-graph";
import {
  citationReceptionBucketOrder,
  citationReceptionPresentation,
  confirmationNeedsOwnEvidence,
  summarizeCitationReceptionItems,
} from "../citation-reception-display.ts";
import type {
  CitationReceptionDto,
  CitationReceptionEvidencePreviewDto,
  CitationReceptionItemDto,
  CitationReceptionSummaryDto,
} from "../workspace-types";
import { formatDate } from "../workspace-types";
import { ScientificContent, SourceAnchorView } from "./scientific-content-lazy";

type Props = {
  path: EvidencePath;
  reception: CitationReceptionDto | null;
  loading: boolean;
  error: string;
  analyzing: boolean;
  actionError: string;
  onRetry: () => void;
  onAnalyze: () => void;
  onOpenSnapshot: (snapshotId: string) => void;
};

const itemOrder = new Intl.Collator("zh-CN", { numeric: true });

function sourceLocation(preview: CitationReceptionEvidencePreviewDto) {
  const statementRepeatsQuote = preview.statement.trim() === (preview.source.anchor.quote ?? "").trim();
  return (
    <div className="citation-reception-source">
      {!statementRepeatsQuote ? <ScientificContent value={preview.statement} /> : null}
      <SourceAnchorView
        anchor={preview.source.anchor}
        mediaType={preview.source.mediaType}
        sha256={preview.source.sha256}
        sourceTitle={preview.source.artifactName}
      />
    </div>
  );
}

function targetLabel(item: CitationReceptionItemDto, path: EvidencePath) {
  if (item.target.entityKind === "atomic_claim") return "本篇观点";
  const sameKind = path.entities
    .filter((entity) => entity.kind === item.target.entityKind)
    .sort((left, right) => itemOrder.compare(left.id, right.id));
  const index = sameKind.findIndex((entity) => entity.id === item.target.entityId);
  const ordinal = index >= 0 ? ` ${index + 1}` : "";
  if (item.target.entityKind === "observation") return `观察${ordinal}`;
  if (item.target.entityKind === "reasoning") return `推理${ordinal}`;
  if (item.target.entityKind === "structured_visual_evidence") return `图表证据${ordinal}`;
  return "原文位置";
}

function paperMetadata(item: CitationReceptionItemDto) {
  const authors = item.citingPaper.authors.filter(Boolean);
  const authorText = authors.length > 3
    ? `${authors.slice(0, 3).join("、")} 等`
    : authors.join("、");
  return [authorText, item.citingPaper.year].filter(Boolean).join(" · ") || "书目信息待补全";
}

function CitationReceptionEntry({
  item,
  path,
  defaultOpen,
  onOpenSnapshot,
}: {
  item: CitationReceptionItemDto;
  path: EvidencePath;
  defaultOpen: boolean;
  onOpenSnapshot: (snapshotId: string) => void;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const display = citationReceptionPresentation(item);
  const hasStrongMode = confirmationNeedsOwnEvidence(item.assessment.confirmationMode);
  const markerText = [
    item.marker ? `引用标记 ${item.marker}` : null,
    item.mentionCount > 1 ? `正文出现 ${item.mentionCount} 次` : null,
  ].filter(Boolean).join(" · ");

  return (
    <details
      className="citation-reception-entry"
      data-tone={display.tone}
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        <div className="citation-reception-entry-heading">
          <div className="citation-reception-tags" aria-label="引用评价">
            <span className={`citation-reception-verdict ${display.tone}`}>{display.verdictLabel}</span>
            <span>{display.faithfulnessLabel}</span>
            <span>{display.independenceLabel}</span>
          </div>
          <h4><ScientificContent value={item.citingPaper.title} inline /></h4>
          <p>{paperMetadata(item)}{markerText ? ` · ${markerText}` : ""}</p>
        </div>
        <span className="citation-reception-disclosure">{open ? "收起" : "展开详情"}</span>
      </summary>

      <div className="citation-reception-entry-body">
        <section aria-label="引用论文对本篇结果的表述">
          <div className="citation-reception-section-label">
            <strong>引用论文如何表述本篇结果</strong>
            <span>{display.confirmationLabel}</span>
          </div>
          <div className="citation-attributed-proposition">
            <ScientificContent value={item.attributedProposition} />
          </div>
          {sourceLocation(item.citationContext)}
        </section>

        {item.confirmingEvidence ? (
          <section aria-label="引用论文自身的验证证据">
            <div className="citation-reception-section-label">
              <strong>引用论文提供的证据</strong>
              <span>综合判断 {display.confidencePercent}%</span>
            </div>
            {sourceLocation(item.confirmingEvidence)}
          </section>
        ) : (
          <p className={`citation-confirmation-note${hasStrongMode ? " warning" : ""}`}>
            {hasStrongMode ? "引用论文提供的独立证据仍需核验。" : "这条记录仅反映引用论文的文字表述。"}
          </p>
        )}

        <div className="citation-target-match">
          <span>对应本篇内容：{targetLabel(item, path)}</span>
          <ScientificContent value={item.target.statement} inline />
        </div>
        {display.boundaryGuide ? <p className="citation-boundary-note">需进一步核对：{display.boundaryGuide}。</p> : null}
        <div className="citation-reception-actions">
          <button className="text-button" type="button" onClick={() => onOpenSnapshot(item.citingPaper.snapshotId)}>
            查看引用论文的完整证据
          </button>
        </div>
      </div>
    </details>
  );
}

function summaryMetrics(summary: CitationReceptionSummaryDto) {
  return [
    ["独立直接复现", summary.independentReplications, "positive"],
    ["独立结果一致", summary.independentConsistentFindings, "positive"],
    ["再分析与综合", summary.reanalysesOrSyntheses, "positive"],
    ["文字表述一致", summary.verbalEndorsements, "neutral"],
    ["限定", summary.qualifications, "caution"],
    ["矛盾", summary.contradictions, "negative"],
    ["需核对", summary.needsReview, "caution"],
  ] as const;
}

function CitationReceptionSkeleton() {
  return (
    <div className="citation-reception-skeleton" aria-hidden="true">
      <span /><span /><span />
    </div>
  );
}

export function CitationReceptionSection({
  path,
  reception,
  loading,
  error,
  analyzing,
  actionError,
  onRetry,
  onAnalyze,
  onOpenSnapshot,
}: Props) {
  const headingId = useId();
  const pathEntityIds = useMemo(() => new Set(path.entities.map((entity) => entity.id)), [path.entities]);
  const items = useMemo(() => {
    if (!reception) return [];
    return reception.items
      .filter((item) => item.target.rootClaimIds.includes(path.root_claim_id)
        || pathEntityIds.has(item.target.entityId))
      .sort((left, right) => {
        const leftDisplay = citationReceptionPresentation(left);
        const rightDisplay = citationReceptionPresentation(right);
        return citationReceptionBucketOrder(leftDisplay.bucket) - citationReceptionBucketOrder(rightDisplay.bucket)
          || right.assessment.confidence - left.assessment.confidence
          || itemOrder.compare(left.citingPaper.title, right.citingPaper.title);
      });
  }, [path.root_claim_id, pathEntityIds, reception]);
  const summary = useMemo(() => summarizeCitationReceptionItems(items), [items]);
  const coverage = reception?.coverage;
  const coverageText = coverage
    ? `已分析 ${coverage.analyzedActivePapers} / ${coverage.eligibleActivePapers} 篇已入库论文${coverage.asOf ? ` · 截至 ${formatDate(coverage.asOf)}` : ""}`
    : "";

  return (
    <section className="citation-reception" aria-labelledby={headingId}>
      <header className="citation-reception-header">
        <div>
          <h3 id={headingId}>后续文献的引用与验证</h3>
          {coverageText ? <p>{coverageText}</p> : null}
        </div>
        {reception?.status === "ready" && items.length ? (
          <span>{summary.totalAssertions} 条引用判断 · {summary.distinctCitingPaperFamilies} 篇论文</span>
        ) : null}
      </header>

      {loading && !reception ? (
        <div className="citation-reception-state" role="status" aria-live="polite">
          <strong>正在读取后续引用证据…</strong>
          <CitationReceptionSkeleton />
        </div>
      ) : error ? (
        <div className="citation-reception-state error" role="alert">
          <div><strong>后续引用证据暂时无法读取</strong><p>请稍后重试。</p></div>
          <button className="button secondary" type="button" onClick={onRetry}>重新读取</button>
        </div>
      ) : analyzing && reception?.status === "not_started" ? (
        <div className="citation-reception-state" role="status" aria-live="polite">
          <div><strong>正在分析已入库论文中的引用…</strong></div>
          <CitationReceptionSkeleton />
        </div>
      ) : reception?.status === "not_started" ? (
        <div className="citation-reception-state">
          <div><strong>尚未运行后续引用分析</strong></div>
          <button className="button secondary" type="button" disabled={analyzing} onClick={onAnalyze}>
            {analyzing ? "正在启动…" : "分析入库论文中的引用"}
          </button>
        </div>
      ) : reception?.status === "processing" ? (
        <div className="citation-reception-state" role="status" aria-live="polite">
          <div><strong>正在分析已入库论文中的引用…</strong></div>
          <CitationReceptionSkeleton />
        </div>
      ) : reception?.status === "unavailable" ? (
        <div className="citation-reception-state"><div><strong>暂时无法建立后续引用关系</strong><p>论文身份信息仍需核对。</p></div></div>
      ) : reception?.status === "failed" ? (
        <div className="citation-reception-state error" role="alert">
          <div><strong>后续引用分析未完成</strong></div>
          <button className="button secondary" type="button" onClick={onRetry}>重新读取</button>
        </div>
      ) : reception?.status === "ready" && items.length ? (
        <>
          <dl className="citation-reception-summary" aria-label="当前观点的后续引用概况">
            {summaryMetrics(summary).filter(([, count]) => count > 0).map(([label, count, tone]) => (
              <div key={label} data-tone={tone}><dt>{label}</dt><dd>{count}</dd></div>
            ))}
          </dl>
          {!coverage?.complete ? (
            <div className="citation-coverage-action">
              <button className="text-button" type="button" disabled={analyzing} onClick={onAnalyze}>
                {analyzing ? "正在补充分析…" : "继续分析其余论文"}
              </button>
            </div>
          ) : null}
          <div className="citation-reception-list">
            {items.map((item, index) => (
              <CitationReceptionEntry
                key={item.id}
                item={item}
                path={path}
                defaultOpen={index === 0}
                onOpenSnapshot={onOpenSnapshot}
              />
            ))}
          </div>
          {reception.truncated ? (
            <p className="citation-coverage-note">还有更多后续引用记录。</p>
          ) : null}
          {reception.warnings.length ? (
            <p className="citation-coverage-note">另有 {reception.warnings.length} 项覆盖限制，相关记录未计入独立确认。</p>
          ) : null}
        </>
      ) : reception?.status === "ready" ? (
        <div className="citation-reception-state empty">
          <div>
            <strong>暂无可核实的后续引用结论</strong>
            <p>{reception.total === 0
              ? "已分析的入库论文中暂未发现引用记录。"
              : reception.truncated
                ? "仍有引用记录待展开。"
                : "现有引用记录尚未与当前观点建立可靠对应。"}</p>
          </div>
          {!coverage?.complete ? (
            <button className="button secondary" type="button" disabled={analyzing} onClick={onAnalyze}>
              {analyzing ? "正在补充分析…" : "继续分析其余论文"}
            </button>
          ) : null}
        </div>
      ) : null}
      {actionError ? <p className="citation-action-error" role="alert">{actionError}</p> : null}
    </section>
  );
}
