"use client";

import { useId, useMemo, useState } from "react";
import type {
  AtomicClaim,
  EvidenceEntity,
  EvidencePath,
  Observation,
  Reasoning,
  Scope,
  SourceAnchor,
  StructuredVisualEvidence,
} from "@/lib/evidence-graph";
import { assessmentPresentation, type AssessmentVerdict } from "../assessment-display.ts";
import type { CitationReceptionDto, SnapshotDetailDto } from "../workspace-types";
import { asRecord, formatDate, formatReviewDate } from "../workspace-types";
import { CitationReceptionSection } from "./citation-reception";
import {
  ScientificContent,
  SourceAnchorView,
  SourceMediaPreview,
  StructuredVisualEvidenceView,
} from "./scientific-content-lazy";
import { UiIcon } from "./ui-icon";
import { SourceFormulaContextProvider } from "./source-formula-context";

type Props = {
  initialClaimId?: string | null;
  detail: SnapshotDetailDto | null;
  loading: boolean;
  sourceTitle?: string;
  citationReception: CitationReceptionDto | null;
  citationReceptionLoading: boolean;
  citationReceptionError: string;
  citationReceptionAnalyzing: boolean;
  citationReceptionActionError: string;
  onBack: () => void;
  backLabel: "观点校验" | "论文校验" | "文献库" | "证据图谱" | "数据图表库" | "人工复核";
  onCompareClaim: (claim: AtomicClaim) => void;
  onRetryCitationReception: () => void;
  onAnalyzeCitationReception: () => void;
  onOpenCitationSnapshot: (snapshotId: string) => void;
  onOpenReview: () => void;
};

type ModelAssessment = {
  claimId: string;
  verdict: AssessmentVerdict;
  confidence: number;
  limitations: string[];
};

const REVIEW_DECISION_LABEL: Record<SnapshotDetailDto["reviews"][number]["decision"], string> = {
  accepted: "人工复核已通过",
  rejected: "人工复核发现问题",
  needs_revision: "人工复核要求修订",
  withdrawn: "复核结论已撤销",
};

function scopeText(scope: Scope) {
  const values = [
    ...scope.entities,
    ...scope.location,
    scope.time?.label ?? null,
    ...scope.formation,
    ...scope.sample_type,
    ...scope.method,
    ...scope.condition,
    ...scope.measurement,
    scope.uncertainty,
  ].filter((item): item is string => Boolean(item));
  return values.length ? values.join(" · ") : "未注明研究范围";
}

const evidenceOrder = new Intl.Collator("zh-CN", { numeric: true });

function sourceContext(path: EvidencePath, anchor: SourceAnchor) {
  const version = path.entities.find((item): item is Extract<EvidenceEntity, { kind: "artifact_version" }> => (
    item.kind === "artifact_version" && item.id === anchor.artifact_version_id
  ));
  const artifact = version
    ? path.entities.find((item): item is Extract<EvidenceEntity, { kind: "artifact" }> => (
      item.kind === "artifact" && item.id === version.artifact_id
    ))
    : null;
  return { version, artifact };
}

function SourceEvidence({
  anchor,
  visual,
  path,
}: {
  anchor: SourceAnchor;
  visual: StructuredVisualEvidence | null;
  path: EvidencePath;
}) {
  const { version, artifact } = sourceContext(path, anchor);
  return (
    <div className={`source-evidence${visual ? " has-structure" : ""}`}>
      <SourceAnchorView
        anchor={anchor}
        mediaType={version?.media_type}
        sha256={version?.sha256}
        sourceTitle={artifact?.canonical_name}
      />
      {visual ? (
        <details className="machine-visual-transcript">
          <summary>{visual.visual_kind === "table" ? "查看表格转写（辅助）" : "查看图表摘要（辅助）"}</summary>
          <div><StructuredVisualEvidenceView visual={visual} /></div>
        </details>
      ) : null}
    </div>
  );
}

function anchorsForNode(path: EvidencePath, node: Observation | Reasoning) {
  const entities = new Map(path.entities.map((entity) => [entity.id, entity]));
  const anchorIds = new Set(node.anchor_ids);
  if (node.kind === "observation") {
    for (const edge of path.edges) {
      if (edge.target_id !== node.id || edge.relation !== "grounds") continue;
      const source = entities.get(edge.source_id);
      if (source?.kind === "source_anchor") anchorIds.add(source.id);
      if (source?.kind === "structured_visual_evidence") anchorIds.add(source.anchor_id);
    }
  }
  return [...anchorIds]
    .map((id) => entities.get(id))
    .filter((entity): entity is SourceAnchor => entity?.kind === "source_anchor")
    .sort((left, right) => {
      const leftVisual = left.anchor_type === "figure" || left.anchor_type === "table";
      const rightVisual = right.anchor_type === "figure" || right.anchor_type === "table";
      return Number(leftVisual) - Number(rightVisual) || evidenceOrder.compare(left.id, right.id);
    });
}

function EvidenceObservation({
  observation,
  path,
  visualByAnchor,
  index,
}: {
  observation: Observation;
  path: EvidencePath;
  visualByAnchor: Map<string, StructuredVisualEvidence>;
  index: number;
}) {
  const anchors = anchorsForNode(path, observation);
  const headingId = useId();
  return (
    <section className="evidence-unit" aria-labelledby={headingId}>
      <header>
        <span>观察结果 {index + 1}</span>
        <h4 id={headingId}><ScientificContent value={observation.statement} inline /></h4>
      </header>
      {anchors.length ? (
        <div className="evidence-sources" aria-label="这项观察对应的原文依据">
          {anchors.map((anchor) => (
            <SourceEvidence key={anchor.id} anchor={anchor} visual={visualByAnchor.get(anchor.id) ?? null} path={path} />
          ))}
        </div>
      ) : null}
    </section>
  );
}

function modelAssessments(detail: SnapshotDetailDto) {
  const latest = detail.verifications[0];
  const report = asRecord(latest?.report);
  const rawAssessments = Array.isArray(report.assessments) ? report.assessments : [];
  const byClaim = new Map<string, ModelAssessment>();
  for (const raw of rawAssessments) {
    const item = asRecord(raw);
    const claimId = typeof item.claim_id === "string" ? item.claim_id : "";
    const verdict = item.verdict;
    if (!claimId || !["supported", "contradicted", "qualified", "uncertain", "insufficient_evidence"].includes(String(verdict))) continue;
    const assessment: ModelAssessment = {
      claimId,
      verdict: verdict as ModelAssessment["verdict"],
      confidence: typeof item.confidence === "number" && Number.isFinite(item.confidence) ? item.confidence : 0,
      limitations: Array.isArray(item.limitations)
        ? item.limitations.filter((value): value is string => typeof value === "string")
        : [],
    };
    byClaim.set(claimId, assessment);
  }
  return byClaim;
}

function EvidencePathPanel({
  path,
  index,
  assessment,
  citationReception,
  citationReceptionLoading,
  citationReceptionError,
  citationReceptionAnalyzing,
  citationReceptionActionError,
  onCompare,
  onRetryCitationReception,
  onAnalyzeCitationReception,
  onOpenCitationSnapshot,
  onOpenReview,
}: {
  path: EvidencePath;
  index: number;
  assessment: ModelAssessment | null;
  citationReception: CitationReceptionDto | null;
  citationReceptionLoading: boolean;
  citationReceptionError: string;
  citationReceptionAnalyzing: boolean;
  citationReceptionActionError: string;
  onCompare: (claim: AtomicClaim) => void;
  onRetryCitationReception: () => void;
  onAnalyzeCitationReception: () => void;
  onOpenCitationSnapshot: (snapshotId: string) => void;
  onOpenReview: () => void;
}) {
  const pathContent = useMemo(() => {
    const observations = path.entities
      .filter((entity): entity is Observation => entity.kind === "observation")
      .sort((left, right) => evidenceOrder.compare(left.id, right.id));
    const reasoning = path.entities
      .filter((entity): entity is Reasoning => entity.kind === "reasoning")
      .sort((left, right) => evidenceOrder.compare(left.id, right.id));
    const assumptions = path.entities
      .filter((entity): entity is Extract<EvidenceEntity, { kind: "assumption" }> => entity.kind === "assumption")
      .sort((left, right) => evidenceOrder.compare(left.id, right.id));
    const visualByAnchor = new Map(
      path.entities
        .filter((entity): entity is StructuredVisualEvidence => entity.kind === "structured_visual_evidence")
        .map((visual) => [visual.anchor_id, visual]),
    );
    const observationAnchorIds = new Set(
      observations.flatMap((observation) => anchorsForNode(path, observation).map((anchor) => anchor.id)),
    );
    return { observations, reasoning, assumptions, visualByAnchor, observationAnchorIds };
  }, [path]);
  const claim = path.entities.find((entity): entity is AtomicClaim => entity.kind === "atomic_claim" && entity.id === path.root_claim_id);
  const assessmentDisplay = assessment ? assessmentPresentation(assessment) : null;

  return (
    <article className="claim-panel">
      <header className="claim-ledger-head">
        <span className="claim-record-number">CLAIM {String(index + 1).padStart(2, "0")}</span>
        <div className="claim-summary-copy">
          <h2 className="claim-proposition"><ScientificContent value={claim?.proposition ?? path.root_claim_id} inline /></h2>
          <small><ScientificContent value={claim ? scopeText(claim.scope) : "未注明研究范围"} inline /></small>
        </div>
        <span className={`assessment-badge ${assessmentDisplay?.tone ?? "neutral"}`}>
          {assessmentDisplay?.verdictLabel ?? "尚未评估"}
        </span>
      </header>
      <div className="claim-panel-body">
        {assessment ? (
          <div className="assessment-summary">
            <div>
              <span>论文内证据评估</span>
              <strong>{assessmentDisplay?.verdictLabel} · {assessmentDisplay?.confidencePercent}%</strong>
            </div>
            <p className="assessment-rationale">{assessmentDisplay?.guide}</p>
          </div>
        ) : null}

        <div className="evidence-path" aria-label="原文证据">
          <section className="evidence-observations" aria-labelledby={`observations-${path.root_claim_id}`}>
            <h3 id={`observations-${path.root_claim_id}`}>观察结果与原文依据</h3>
            {pathContent.observations.map((observation, index) => (
              <EvidenceObservation
                key={observation.id}
                observation={observation}
                path={path}
                visualByAnchor={pathContent.visualByAnchor}
                index={index}
              />
            ))}
          </section>

          {pathContent.reasoning.length ? (
            <section className="reasoning-sequence" aria-labelledby={`reasoning-${path.root_claim_id}`}>
              <h3 id={`reasoning-${path.root_claim_id}`}>从观察结果到论文观点</h3>
              <ol>
                {pathContent.reasoning.map((reasoning, index) => (
                  <li key={reasoning.id}>
                    <span>推理 {index + 1}</span>
                    <ScientificContent value={reasoning.statement} />
                    {anchorsForNode(path, reasoning).filter((anchor) => !pathContent.observationAnchorIds.has(anchor.id)).map((anchor) => (
                      <SourceEvidence key={anchor.id} anchor={anchor} visual={pathContent.visualByAnchor.get(anchor.id) ?? null} path={path} />
                    ))}
                  </li>
                ))}
              </ol>
            </section>
          ) : null}

          {pathContent.assumptions.length ? (
            <details className="path-assumptions">
              <summary>{pathContent.assumptions.length} 项前提或测量假设</summary>
              <ul>
                {pathContent.assumptions.map((assumption) => (
                  <li key={assumption.id}><ScientificContent value={assumption.statement} /></li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>
        <CitationReceptionSection
          path={path}
          reception={citationReception}
          loading={citationReceptionLoading}
          error={citationReceptionError}
          analyzing={citationReceptionAnalyzing}
          actionError={citationReceptionActionError}
          onRetry={onRetryCitationReception}
          onAnalyze={onAnalyzeCitationReception}
          onOpenSnapshot={onOpenCitationSnapshot}
        />
        <div className="claim-actions">
          {claim ? <button className="button secondary" type="button" onClick={() => onCompare(claim)}>查找支持与反例</button> : null}
          <button className="text-button" type="button" onClick={onOpenReview}>记录人工复核</button>
        </div>
      </div>
    </article>
  );
}

function SourceView({ detail }: { detail: SnapshotDetailDto }) {
  const anchorsByVersion = new Map<string, typeof detail.graph.anchors>();
  for (const anchor of detail.graph.anchors) {
    const items = anchorsByVersion.get(anchor.artifact_version_id) ?? [];
    items.push(anchor);
    anchorsByVersion.set(anchor.artifact_version_id, items);
  }
  return (
    <div className="source-view">
      {detail.graph.artifact_versions.map((version) => {
        const artifact = detail.graph.artifacts.find((item) => item.id === version.artifact_id);
        const anchors = anchorsByVersion.get(version.id) ?? [];
        return (
          <section className="source-record" key={version.id}>
            <header>
              <div>
                <h2>{artifact?.canonical_name ?? version.id}</h2>
                <p>{version.media_type} · {anchors.length} 处原文位置</p>
              </div>
              <a className="button secondary" href={`/api/artifacts/${version.sha256}`}>下载原件</a>
            </header>
            <SourceMediaPreview mediaType={version.media_type} sha256={version.sha256} title={artifact?.canonical_name ?? version.id} />
            {anchors.length ? (
              <ol className="anchor-list">
                {anchors.map((anchor) => (
                  <li key={anchor.id}>
                    <SourceAnchorView anchor={anchor} mediaType={version.media_type} sha256={version.sha256} sourceTitle={artifact?.canonical_name} showVisualExcerpt={false} />
                  </li>
                ))}
              </ol>
            ) : <p className="muted">当前证据未引用这一文件版本。</p>}
          </section>
        );
      })}
    </div>
  );
}

function RunView({ detail, onOpenReview }: { detail: SnapshotDetailDto; onOpenReview: () => void }) {
  return (
    <div className="run-view">
      <dl className="run-metadata">
        <div><dt>分析时间</dt><dd>{formatDate(detail.snapshot.createdAt)}</dd></div>
        <div><dt>证据评估</dt><dd>{detail.verifications.length ? "已完成" : "未完成"}</dd></div>
      </dl>
      <section className="review-history compact-history">
        <header>
          <div>
            <h2>人工复核</h2>
          </div>
          <button className="button secondary" type="button" onClick={onOpenReview}>进入人工复核</button>
        </header>
        {detail.reviews.length ? detail.reviews.map((review) => (
          <div className="review-history-entry" key={review.id}>
            <strong>{REVIEW_DECISION_LABEL[review.decision]}</strong>
            <ScientificContent value={review.rationale} />
            <small>{review.reviewer} · {formatReviewDate(review.createdAt)}</small>
          </div>
        )) : <p className="muted">尚未进行人工复核。</p>}
      </section>
    </div>
  );
}

export function PaperDetail({
  initialClaimId,
  detail,
  loading,
  sourceTitle,
  citationReception,
  citationReceptionLoading,
  citationReceptionError,
  citationReceptionAnalyzing,
  citationReceptionActionError,
  onBack,
  backLabel,
  onCompareClaim,
  onRetryCitationReception,
  onAnalyzeCitationReception,
  onOpenCitationSnapshot,
  onOpenReview,
}: Props) {
  const [tab, setTab] = useState<"claims" | "source" | "run">("claims");
  const [activeClaimId, setActiveClaimId] = useState<string | null>(initialClaimId ?? null);
  if (loading) return <div className="view-loading paper-view-state" aria-live="polite"><span />正在读取论文证据…</div>;
  if (!detail) return <div className="view-empty paper-view-state"><h2>没有可显示的证据</h2><p>请返回{backLabel}，选择一篇已完成 Compile 的文献。</p><button className="button secondary" onClick={onBack}>返回{backLabel}</button></div>;

  const assessments = modelAssessments(detail);
  const assessmentForPath = (path: EvidencePath) => {
    const claim = detail.graph.claims.find((item) => item.id === path.root_claim_id);
    return assessments.get(claim?.source_id ?? "") ?? assessments.get(claim?.id ?? "") ?? null;
  };
  const limitationCount = new Set([...assessments.values()].flatMap((item) => item.limitations)).size;
  const activePath = detail.paths.find((path) => path.root_claim_id === activeClaimId) ?? detail.paths[0] ?? null;
  const activePathIndex = activePath ? detail.paths.findIndex((path) => path.root_claim_id === activePath.root_claim_id) : -1;
  const tabs = [
    { id: "claims" as const, label: "观点与证据" },
    { id: "source" as const, label: "原件与定位" },
    { id: "run" as const, label: "人工复核记录" },
  ];
  const moveTab = (event: React.KeyboardEvent<HTMLButtonElement>, current: typeof tab) => {
    const index = tabs.findIndex((item) => item.id === current);
    const nextIndex = event.key === "ArrowRight" ? (index + 1) % tabs.length
      : event.key === "ArrowLeft" ? (index - 1 + tabs.length) % tabs.length
        : event.key === "Home" ? 0
          : event.key === "End" ? tabs.length - 1
            : -1;
    if (nextIndex < 0) return;
    event.preventDefault();
    const next = tabs[nextIndex].id;
    setTab(next);
    document.getElementById(`paper-tab-${next}`)?.focus();
  };

  return (
    <SourceFormulaContextProvider
      snapshotId={detail.snapshot.id}
      versionId={detail.snapshot.rootArtifactVersionId}
      sha256={detail.graph.artifact_versions.find((version) => version.id === detail.snapshot.rootArtifactVersionId)?.sha256 ?? ""}
      anchors={detail.graph.anchors}
    >
    <section className="paper-view" aria-labelledby="paper-title">
      <header className="paper-header">
        <button className="back-button" type="button" onClick={onBack}><UiIcon name="arrow-left" size={16} />{backLabel}</button>
        <div className="paper-title-row">
          <div>
            <span className="ledger-label">论文证据</span>
            <h1 id="paper-title">{sourceTitle ?? "论文证据"}</h1>
            <p>{detail.graph.claims.length} 条观点 · {detail.graph.observations.length} 项观察结果 · {detail.graph.anchors.length} 处原文位置</p>
          </div>
          <div className="paper-statuses">
            <span className={detail.verifications.length ? "status-chip assessed" : "status-chip"}>
              {detail.verifications.length ? `证据评估完成${limitationCount ? ` · ${limitationCount} 项需核对` : ""}` : "尚未评估"}
            </span>
          </div>
        </div>
      </header>

      <div className="paper-tabs" role="tablist" aria-label="论文详情视图">
        {tabs.map((item) => (
          <button
            id={`paper-tab-${item.id}`}
            role="tab"
            type="button"
            aria-controls={`paper-panel-${item.id}`}
            aria-selected={tab === item.id}
            tabIndex={tab === item.id ? 0 : -1}
            onClick={() => setTab(item.id)}
            onKeyDown={(event) => moveTab(event, item.id)}
            key={item.id}
          >{item.label}</button>
        ))}
      </div>

      <div id={`paper-panel-${tab}`} className="paper-tab-panel" role="tabpanel" aria-labelledby={`paper-tab-${tab}`} tabIndex={0}>
        {tab === "claims" ? (
          <div className="paper-workbench">
            <nav className="paper-claim-index" aria-label="论文 Claim 列表">
              <header>
                <span>Claim docket</span>
                <strong>{detail.paths.length} 条可核验观点</strong>
              </header>
              <div>
                {detail.paths.map((path, index) => {
                  const claim = path.entities.find((entity): entity is AtomicClaim => entity.kind === "atomic_claim" && entity.id === path.root_claim_id);
                  const display = assessmentForPath(path);
                  const presentation = display ? assessmentPresentation(display) : null;
                  return (
                    <button
                      className="paper-claim-index-row"
                      type="button"
                      aria-current={activePath?.root_claim_id === path.root_claim_id ? "true" : undefined}
                      onClick={() => setActiveClaimId(path.root_claim_id)}
                      key={path.root_claim_id}
                    >
                      <span>CLAIM {String(index + 1).padStart(2, "0")}</span>
                      <strong><ScientificContent value={claim?.proposition ?? path.root_claim_id} inline /></strong>
                      <small>{presentation?.verdictLabel ?? "尚未评估"}</small>
                    </button>
                  );
                })}
              </div>
            </nav>
            <div className="claim-list" aria-live="polite">
              {activePath ? (
              <EvidencePathPanel
                key={activePath.root_claim_id}
                path={activePath}
                index={activePathIndex}
                assessment={assessmentForPath(activePath)}
                citationReception={citationReception}
                citationReceptionLoading={citationReceptionLoading}
                citationReceptionError={citationReceptionError}
                citationReceptionAnalyzing={citationReceptionAnalyzing}
                citationReceptionActionError={citationReceptionActionError}
                onCompare={onCompareClaim}
                onRetryCitationReception={onRetryCitationReception}
                onAnalyzeCitationReception={onAnalyzeCitationReception}
                onOpenCitationSnapshot={onOpenCitationSnapshot}
                onOpenReview={onOpenReview}
              />
              ) : <p className="muted">暂无可展开的论文观点。</p>}
            </div>
          </div>
        ) : tab === "source" ? <SourceView detail={detail} /> : <RunView detail={detail} onOpenReview={onOpenReview} />}
      </div>
    </section>
    </SourceFormulaContextProvider>
  );
}
