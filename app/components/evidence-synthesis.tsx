"use client";

import { memo, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { Scope, StructuredVisualEvidence } from "@/lib/evidence-graph";
import { watchDataQuality } from "../data-quality-polling.ts";
import { runParallelWorkPool } from "../parallel-work-pool";
import { fetchStreamed } from "../streamed-fetch";
import type {
  EvidenceSynthesisAnchorDto,
  EvidenceSynthesisCompareResponse,
  EvidenceSynthesisEvidenceItemDto,
  EvidenceSynthesisNormalizeResponse,
  EvidenceSynthesisNormalizedClaimDto,
  EvidenceSynthesisPathDto,
  EvidenceSynthesisScopeAlignmentDto,
  DataQualityCheckDto,
  DataQualityJobDto,
  DataQualityReportDto,
  LibraryArtifactDto,
  SnapshotDetailDto,
} from "../workspace-types";
import { readJson, responseError } from "../workspace-types";
import { ScientificContent, StructuredVisualEvidenceView } from "./scientific-content-lazy";
import { ClaimListDisclosure, ExpandableClaim } from "./claim-disclosure";
import { UiIcon } from "./ui-icon";
import { PaginationControls } from "./pagination-controls";
import { PdfPagePreview } from "./pdf-page-preview";
import { PdfRegionPreview } from "./pdf-region-preview";

type SynthesisMode = "paper" | "viewpoint";
type GroupKey = "support" | "contradict" | "qualify" | "context";
type ComparisonStatus = "idle" | "running" | "completed" | "cancelled";
type JobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

type PaperClaimSource = {
  kind: "paper_claim";
  snapshotId: string;
  claimId: string;
};

type NormalizedClaimSource = {
  kind: "normalized_claim";
  claim: Pick<EvidenceSynthesisNormalizedClaimDto, "id" | "canonicalClaim" | "claimType" | "polarity" | "modality" | "scope">;
};

type ComparableSource = {
  key: string;
  id: string;
  canonicalClaim: string;
  scope: Scope;
  requestSource: PaperClaimSource | NormalizedClaimSource;
};

type ComparisonJob = {
  source: ComparableSource;
  status: JobStatus;
  result: EvidenceSynthesisCompareResponse | null;
  error: string;
};

type ServiceEvidenceItem = {
  comparisonId: string;
  verdict: string;
  relation: string;
  direction: string | null;
  stance: string;
  directness: string;
  scopeAlignment: string;
  rationale: string | null;
  limitations: string[];
  candidate: {
    snapshotId: string;
    paperFamilyId: string;
    source: { artifactName: string; mediaType: string; downloadUrl: string };
    claim: { id: string; canonicalClaim: string; scope: Scope };
    evidenceAnchors: EvidenceSynthesisAnchorDto[];
    shortestEvidencePath: EvidenceSynthesisPathDto;
  };
  selectedEvidence: EvidenceSynthesisEvidenceItemDto["selectedEvidence"];
};

type ServiceCompareResponse = Omit<EvidenceSynthesisCompareResponse, "groups"> & {
  groups: Record<keyof EvidenceSynthesisCompareResponse["groups"], ServiceEvidenceItem[]>;
};

type Props = {
  active?: boolean;
  mode: SynthesisMode;
  artifacts: LibraryArtifactDto[];
  onOpenSnapshot: (snapshotId: string, claimId: string) => void;
  onUploadPdf: (file: File) => Promise<{ artifactVersionId: string; fileName: string }>;
};

type EvidenceModule = "reasoning" | "data";

const COMPARISON_CONCURRENCY = 2;
const DEFAULT_VISIBLE_CLAIMS = 2;

function SynthesisSurface({ separated, children }: { separated: boolean; children: ReactNode }) {
  return separated ? <div className="evidence-synthesis">{children}</div> : <>{children}</>;
}

function visibleDataQualityWarning(warning: string) {
  return !warning.startsWith("MinerU-derived table/chart/image structures")
    && !warning.includes("未发现明确的数据可用性链接");
}

function HistoricalPaperPicker({ selectedSnapshotId, disabled, onSelect }: { selectedSnapshotId: string; disabled: boolean; onSelect: (artifact: LibraryArtifactDto) => void }) {
  const [opened, setOpened] = useState(false);
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const cursors = useRef(new Map<number, string>());
  const [artifacts, setArtifacts] = useState<LibraryArtifactDto[]>([]);
  const [pageInfo, setPageInfo] = useState({ page: 1, pageSize: 20, total: null as number | null, totalPages: null as number | null, hasMore: false, nextCursor: null as string | null });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!opened) return;
    const controller = new AbortController();
    const frame = window.requestAnimationFrame(() => void (async () => {
      setLoading(true);
      setError("");
      try {
        const parameters = new URLSearchParams({ status: "completed", limit: String(pageSize) });
        if (page === 1) cursors.current.clear();
        const cursor = cursors.current.get(page);
        if (cursor) parameters.set("cursor", cursor);
        if (query) parameters.set("query", query);
        const response = await fetch(`/api/library?${parameters}`, { cache: "no-store", signal: controller.signal });
        const payload = await readJson(response);
        if (!response.ok) throw new Error(responseError(payload, "历史论文读取失败"));
        if (controller.signal.aborted) return;
        const result = payload as { artifacts: LibraryArtifactDto[]; pageInfo: typeof pageInfo };
        setArtifacts(result.artifacts);
        if (result.pageInfo.nextCursor) cursors.current.set(page + 1, result.pageInfo.nextCursor);
        setPageInfo({ ...result.pageInfo, page });
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "历史论文读取失败");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })());
    return () => { window.cancelAnimationFrame(frame); controller.abort(); };
  }, [opened, page, pageSize, query]);

  return (
    <details className="synthesis-history-picker" onToggle={(event) => setOpened(event.currentTarget.open)}>
      <summary><UiIcon name="library" /><span>选择历史论文</span></summary>
      <div>
        <form onSubmit={(event) => { event.preventDefault(); setPage(1); setQuery(draft.trim()); }}>
          <label htmlFor="synthesis-history-query">搜索论文</label>
          <input id="synthesis-history-query" value={draft} maxLength={200} placeholder="输入论文名或文件名" onChange={(event) => setDraft(event.target.value)} />
          <button className="button secondary" type="submit">搜索</button>
        </form>
        {error ? <p className="row-error" role="alert">{error}</p> : null}
        {loading ? <div className="view-loading compact"><span />正在读取论文…</div> : (
          <div className="synthesis-history-list">
            {artifacts.map((artifact) => <button type="button" disabled={disabled} aria-current={artifact.snapshot?.id === selectedSnapshotId ? "true" : undefined} key={artifact.artifactId} onClick={() => onSelect(artifact)}><strong>{artifact.title}</strong><small>v{artifact.latestVersion?.versionNumber ?? 1}</small></button>)}
            {!artifacts.length ? <p>{pageInfo.hasMore ? "本页没有匹配，可继续下一页。" : "没有匹配的已完成论文。"}</p> : null}
          </div>
        )}
        {artifacts.length || pageInfo.hasMore || page > 1 ? <PaginationControls cursorPagination hasMore={pageInfo.hasMore} page={pageInfo.page} pageSize={pageInfo.pageSize} total={pageInfo.total} disabled={loading} itemLabel="篇论文" onPageChange={setPage} onPageSizeChange={(size) => { setPageSize(size); setPage(1); }} /> : null}
      </div>
    </details>
  );
}
const GROUPS: Array<{ key: GroupKey; title: string }> = [
  { key: "support", title: "支持且范围匹配" },
  { key: "contradict", title: "实质冲突" },
  { key: "qualify", title: "限定与适用边界" },
  { key: "context", title: "补充 / 等价" },
];

function timeSummary(scope: Scope) {
  if (!scope.time) return "";
  if (scope.time.label) return scope.time.label;
  const bounds = [scope.time.start, scope.time.end]
    .filter((value): value is number => value !== null)
    .join("–");
  return bounds ? `${bounds}${scope.time.unit ? ` ${scope.time.unit}` : ""}` : "";
}

function scopeSummary(scope: Scope) {
  const values = [
    ...scope.entities,
    ...scope.location,
    timeSummary(scope),
    ...scope.formation,
    ...scope.sample_type,
    ...scope.method,
    ...scope.condition,
    ...scope.measurement,
    scope.uncertainty,
  ].filter((value): value is string => Boolean(value));
  return values.join(" · ") || "适用范围待核对";
}

function evidenceItem(item: ServiceEvidenceItem): EvidenceSynthesisEvidenceItemDto {
  const { candidate } = item;
  return {
    id: item.comparisonId,
    verdict: item.verdict,
    relation: item.relation,
    direction: item.direction,
    stance: item.stance,
    directness: item.directness,
    candidateClaim: {
      snapshotId: candidate.snapshotId,
      claimId: candidate.claim.id,
      canonicalClaim: candidate.claim.canonicalClaim,
      scope: candidate.claim.scope,
    },
    scopeAlignment: { relation: item.scopeAlignment },
    rationale: item.rationale,
    limitations: item.limitations,
    paper: {
      paperFamilyId: candidate.paperFamilyId,
      title: candidate.source.artifactName,
    },
    candidateSource: {
      mediaType: candidate.source.mediaType,
      downloadUrl: candidate.source.downloadUrl,
    },
    exactAnchors: candidate.evidenceAnchors,
    shortestEvidencePath: candidate.shortestEvidencePath,
    selectedEvidence: item.selectedEvidence,
  };
}

function hasComparableScope(item: EvidenceSynthesisEvidenceItemDto) {
  return [
    "equivalent",
    "overlap",
    "query_within_candidate",
    "candidate_within_query",
  ].includes(item.scopeAlignment.relation);
}

function hasEvidenceTrace(item: EvidenceSynthesisEvidenceItemDto) {
  return Boolean(
    item.candidateClaim.snapshotId
    && item.candidateClaim.claimId
    && item.candidateClaim.canonicalClaim
    && item.paper.paperFamilyId
    && item.exactAnchors.length
    && item.shortestEvidencePath.nodes.length,
  );
}

function conservativeGroup(item: EvidenceSynthesisEvidenceItemDto): GroupKey | null {
  const relation = item.relation;
  const alignment = item.scopeAlignment.relation;
  const direction = item.direction;
  if (item.verdict === "abstained"
    || ["no_relation", "insufficient_to_determine"].includes(relation)
    || ["unknown", "disjoint"].includes(alignment)) {
    return null;
  }
  if (!hasEvidenceTrace(item)) return null;
  if (relation === "supports") {
    if (direction !== "candidate_to_query" || !hasComparableScope(item)) return null;
    return ["equivalent", "query_within_candidate"].includes(alignment) ? "support" : "qualify";
  }
  if (relation === "contradicts") {
    if (!hasComparableScope(item)) return null;
    return alignment === "equivalent" ? "contradict" : "qualify";
  }
  if (["qualifies", "refines"].includes(relation)) {
    return direction === "candidate_to_query" ? "qualify" : "context";
  }
  if (["equivalent", "supplements"].includes(relation)) return "context";
  return null;
}

function regroupEvidence(rawGroups: ServiceCompareResponse["groups"]) {
  const grouped: EvidenceSynthesisCompareResponse["groups"] = {
    support: [],
    contradict: [],
    qualify: [],
    context: [],
  };
  const seen = new Set<string>();
  for (const { key } of GROUPS) {
    for (const rawItem of rawGroups[key]) {
      const item = evidenceItem(rawItem);
      const group = conservativeGroup(item);
      if (!group || seen.has(item.id)) continue;
      seen.add(item.id);
      grouped[group].push(item);
    }
  }
  return grouped;
}

function compareResponse(value: unknown): EvidenceSynthesisCompareResponse {
  const response = value as ServiceCompareResponse;
  return {
    ok: true,
    action: "compare",
    sourceClaim: response.sourceClaim,
    coverage: response.coverage,
    groups: regroupEvidence(response.groups),
    warnings: response.warnings,
  };
}

function normalizedClaimSelectable(claim: EvidenceSynthesisNormalizedClaimDto) {
  return claim.status === "ready";
}

function claimTypeLabel(value: string) {
  const labels: Record<string, string> = {
    descriptive: "描述",
    comparative: "比较",
    correlational: "相关",
    causal: "因果",
    mechanistic: "机制",
    methodological: "方法",
    predictive: "预测",
  };
  return labels[value.toLowerCase()] ?? (value || "类型待核对");
}

function normalizedStatusLabel(value: string) {
  const labels: Record<string, string> = {
    ready: "可比较",
    needs_user_definition: "需要补充说明",
  };
  return labels[value.toLowerCase()] ?? "可比性待核对";
}

function relationLabel(value: string) {
  const labels: Record<string, string> = {
    supports: "支持",
    contradicts: "矛盾",
    qualifies: "限定",
    refines: "细化",
    equivalent: "等价",
    supplements: "补充",
  };
  return labels[value] ?? "";
}

function scopeAlignmentLabel(alignment: EvidenceSynthesisScopeAlignmentDto) {
  const labels: Record<string, string> = {
    equivalent: "Scope 一致",
    overlap: "Scope 重叠",
    query_within_candidate: "当前观点的适用范围更窄",
    candidate_within_query: "候选 Claim 范围更窄",
  };
  return labels[alignment.relation] ?? "";
}

function directnessLabel(value: string) {
  const labels: Record<string, string> = {
    direct: "直接关系",
    indirect: "间接关系",
  };
  return labels[value] ?? "";
}

function stanceLabel(value: string) {
  const labels: Record<string, string> = {
    supportive: "立场：支持",
    contradictory: "立场：相反",
    qualifying: "立场：限定",
    neutral: "立场：中性",
  };
  return labels[value] ?? "";
}

function anchorLocator(anchor: EvidenceSynthesisAnchorDto) {
  return [
    anchor.page !== null ? `第 ${anchor.page} 页` : null,
    anchor.section,
    anchor.figure,
    anchor.table,
    anchor.regionLabel,
  ].filter(Boolean).join(" · ") || "原文位置待核对";
}

function pathNodeKind(value: string) {
  const labels: Record<string, string> = {
    source_anchor: "原文位置",
    structured_visual_evidence: "原始图表",
    observation: "观察",
    assumption: "假设",
    reasoning: "推理",
    atomic_claim: "Claim",
  };
  return labels[value] ?? "证据节点";
}

function pathRelation(value: string) {
  const labels: Record<string, string> = {
    grounds: "直接支撑",
    premise_for: "作为前提",
    assumption_for: "引入假设",
    infers: "推导",
    contradicts: "推理反驳",
    qualifies: "推理限定",
    provenance: "来源",
  };
  return labels[value] ?? value;
}

function ClaimScope({ scope }: { scope: Scope }) {
  return (
    <details className="synthesis-scope-summary">
      <summary>范围与关键词</summary>
      <ScientificContent value={scopeSummary(scope)} inline />
    </details>
  );
}

function CoverageDetails({ result }: { result: EvidenceSynthesisCompareResponse }) {
  const { coverage } = result;
  return (
    <details className="synthesis-coverage">
      <summary>检索范围与覆盖</summary>
      <div>
        <p>当前可检索 {coverage.activePaperFamilies} 篇独立论文（版本已去重） · {coverage.activeClaims} 条 Claim</p>
        <p>本次找到 {coverage.retrievedCandidates} 条候选证据，其中 {coverage.comparedCandidates} 条已纳入完整比较 · 最多返回 {coverage.retrievalLimit} 条</p>
        <p>已确认直接关系 {coverage.acceptedRelations} 条</p>
        {coverage.excludedSourceFamily ? <p>已排除来自当前论文的 {coverage.excludedSourceFamily} 条候选证据。</p> : null}
        {!coverage.complete ? <p>当前结果仅覆盖部分候选。</p> : null}
        <p>数据截至：{coverage.asOf}</p>
        {result.warnings.map((warning) => <p key={warning}>{warning}</p>)}
      </div>
    </details>
  );
}

function EvidencePathDetails({ path }: { path: EvidenceSynthesisPathDto }) {
  return (
    <details className="synthesis-path">
      <summary>查看完整证据链</summary>
      <ol>
        {path.nodes.map((node) => (
          <li key={node.id}>
            <span>{pathNodeKind(node.kind)}</span>
            <ScientificContent value={node.label} />
          </li>
        ))}
      </ol>
      {path.links.length ? (
        <p className="synthesis-path-relations">路径关系：{path.links.map((link) => pathRelation(link.relation)).join(" → ")}</p>
      ) : null}
      {path.truncated ? <p className="synthesis-missing-trace">当前仅展示部分证据链；请打开论文详情查看完整路径。</p> : null}
    </details>
  );
}

function SelectedEvidenceDetails({ item }: { item: EvidenceSynthesisEvidenceItemDto }) {
  const { query, candidate } = item.selectedEvidence;
  if (!query.length && !candidate.length) return null;
  return (
    <details className="synthesis-selected-evidence">
      <summary>查看关系判断所依据的证据</summary>
      <div>
        {query.length ? (
          <section>
            <strong>当前 Claim 的证据</strong>
            <ol>{query.map((entity, index) => <li key={`${entity.id || entity.kind}:${index}`}><span>{pathNodeKind(entity.kind)}</span><ScientificContent value={entity.text} /></li>)}</ol>
          </section>
        ) : null}
        {candidate.length ? (
          <section>
            <strong>候选 Claim 的证据</strong>
            <ol>{candidate.map((entity, index) => <li key={`${entity.id || entity.kind}:${index}`}><span>{pathNodeKind(entity.kind)}</span><ScientificContent value={entity.text} /></li>)}</ol>
          </section>
        ) : null}
      </div>
    </details>
  );
}

function EvidenceRow({
  group,
  index,
  item,
  onOpenSnapshot,
}: {
  group: GroupKey;
  index: number;
  item: EvidenceSynthesisEvidenceItemDto;
  onOpenSnapshot: (snapshotId: string, claimId: string) => void;
}) {
  const paperTitle = item.paper.title;
  const originalPage = item.exactAnchors.find((anchor) => anchor.page !== null)?.page ?? 1;
  const originalHref = item.candidateSource.mediaType.toLowerCase().includes("pdf")
    ? `${item.candidateSource.downloadUrl}?disposition=inline#page=${originalPage}`
    : item.candidateSource.downloadUrl;
  return (
    <article className="synthesis-evidence-row" data-relation={group}>
      <header>
        <span>{String(index + 1).padStart(2, "0")}</span>
        <div>
          <p className="synthesis-paper-family" title={paperTitle}>{paperTitle}</p>
          <h5><ScientificContent value={item.candidateClaim.canonicalClaim} inline /></h5>
          <ClaimScope scope={item.candidateClaim.scope} />
        </div>
        <strong>{relationLabel(item.relation)}</strong>
      </header>

      <div className="synthesis-judgment">
        <div>
          <span>适用范围</span>
          <strong>{scopeAlignmentLabel(item.scopeAlignment)}</strong>
        </div>
        <div>
          <span>关系依据</span>
          <p className="synthesis-relation-metadata">{stanceLabel(item.stance)} · {directnessLabel(item.directness)}</p>
          {item.rationale ? <ScientificContent value={item.rationale} /> : null}
          {item.limitations.length ? <ul>{item.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul> : null}
        </div>
      </div>

      <SelectedEvidenceDetails item={item} />

      <section className="synthesis-anchors" aria-label="原文位置">
        <header><strong>原文位置</strong><span>{item.exactAnchors.length} 处</span></header>
        <ol>
          {item.exactAnchors.map((anchor) => (
            <li key={anchor.id}>
              <span>{anchorLocator(anchor)}</span>
              {anchor.quote ? <ScientificContent value={anchor.quote} /> : <p>请结合原文位置核对。</p>}
            </li>
          ))}
        </ol>
      </section>

      <EvidencePathDetails path={item.shortestEvidencePath} />
      <div className="synthesis-evidence-actions">
        <a className="text-button" href={originalHref} target="_blank" rel="noopener">在新标签页中打开原件</a>
        <button className="text-button" type="button" onClick={() => onOpenSnapshot(item.candidateClaim.snapshotId, item.candidateClaim.claimId)}>打开论文完整证据</button>
      </div>
    </article>
  );
}

function ResultGroup({
  group,
  items,
  sourceIndex,
  onOpenSnapshot,
}: {
  group: (typeof GROUPS)[number];
  items: EvidenceSynthesisEvidenceItemDto[];
  sourceIndex: number;
  onOpenSnapshot: (snapshotId: string, claimId: string) => void;
}) {
  const headingId = `synthesis-source-${sourceIndex}-${group.key}`;
  return (
    <section className="synthesis-relation-group" data-relation={group.key} aria-labelledby={headingId}>
      <header>
        <div><h4 id={headingId}>{group.title}</h4></div>
        <span>{items.length} 条</span>
      </header>
      {items.map((item, index) => <EvidenceRow group={group.key} index={index} item={item} onOpenSnapshot={onOpenSnapshot} key={item.id} />)}
    </section>
  );
}

function evidenceOverview(result: EvidenceSynthesisCompareResponse) {
  const support = result.groups.support.length;
  const contradict = result.groups.contradict.length;
  const qualify = result.groups.qualify.length;
  if (support > 0 && contradict > 0) {
    return {
      tone: "mixed",
      text: "当前文献库中同时存在适用范围可比的支持证据与冲突证据。",
    };
  }
  if (contradict > 0 && support === 0) {
    return {
      tone: "contradict",
      text: "当前文献库中存在适用范围可比的冲突证据。",
    };
  }
  if (qualify > 0) {
    return {
      tone: "qualify",
      text: "现有证据表明，该观点具有明确的适用边界。",
    };
  }
  if (support > 0) {
    return {
      tone: "support",
      text: "当前文献库中，适用范围可比的证据支持该观点。",
    };
  }
  return {
    tone: "insufficient",
    text: "当前证据尚不足以确认观点关系。",
  };
}

function ComparisonResult({
  index,
  job,
  onOpenSnapshot,
}: {
  index: number;
  job: ComparisonJob;
  onOpenSnapshot: (snapshotId: string, claimId: string) => void;
}) {
  const overview = job.result ? evidenceOverview(job.result) : null;
  return (
    <section className="synthesis-source-result" aria-labelledby={`synthesis-result-${index}`}>
      <header>
        <span>S{String(index + 1).padStart(2, "0")}</span>
        <div>
          <h3 id={`synthesis-result-${index}`}><ScientificContent value={job.source.canonicalClaim} inline /></h3>
          <ClaimScope scope={job.source.scope} />
        </div>
        <strong className={`synthesis-job-state ${job.status}`}>{job.status === "queued" ? "排队中" : job.status === "running" ? "正在比较" : job.status === "completed" ? "已完成" : job.status === "failed" ? "未完成" : "已取消"}</strong>
      </header>
      {job.status === "queued" || job.status === "running" ? (
        <div className="synthesis-result-loading" role="status"><span /><span /><span /><p>{job.status === "queued" ? "正在排队，稍后将自动开始…" : "正在读取候选 Claim 的证据链并核对 Scope…"}</p></div>
      ) : null}
      {job.status === "failed" ? <div className="synthesis-local-error" role="alert"><strong>这条观点暂未完成比较</strong><p>{job.error}。请稍后重试。</p></div> : null}
      {job.status === "cancelled" ? <p className="synthesis-cancelled-note">这条观点的比较已取消。</p> : null}
      {job.result ? (
        <>
          {overview ? <div className="synthesis-overview" data-tone={overview.tone}><span>证据概览</span><strong>{overview.text}</strong></div> : null}
          <CoverageDetails result={job.result} />
          {GROUPS.filter((group) => job.result!.groups[group.key].length).map((group) => (
            <ResultGroup group={group} items={job.result!.groups[group.key]} sourceIndex={index} onOpenSnapshot={onOpenSnapshot} key={group.key} />
          ))}
        </>
      ) : null}
    </section>
  );
}

type DataQualityTableDto = DataQualityReportDto["paper_tables"][number];

function normalizedVisualLabel(value: string) {
  return value.normalize("NFKC").trim().toLocaleLowerCase("und").replace(/[\s_:：·.–—-]+/gu, "");
}

function tableVisualMatch(detail: SnapshotDetailDto | null, table: DataQualityTableDto, tableIndex: number) {
  if (!detail) return null;
  const visuals = new Map(detail.graph.visual_evidence.map((visual) => [visual.anchor_id, visual]));
  const candidates = detail.graph.anchors
    .filter((anchor) => anchor.anchor_type === "table")
    .map((anchor) => ({ anchor, visual: visuals.get(anchor.id) ?? null }));
  if (!candidates.length) return null;
  const target = normalizedVisualLabel(table.name);
  const ranked = candidates.map((candidate, index) => {
    const label = normalizedVisualLabel(candidate.anchor.selector.table ?? "");
    const samePage = Boolean(table.page && candidate.anchor.selector.page === table.page);
    const labelScore = label && target === label ? 8 : label && (target.includes(label) || label.includes(target)) ? 4 : 0;
    return { ...candidate, index, score: labelScore + (samePage ? 6 : 0) + (candidate.visual ? 1 : 0) };
  }).sort((left, right) => right.score - left.score || left.index - right.index);
  const best = ranked[0];
  if (best.score > 1) return best;
  if (candidates.length === detail.graph.visual_evidence.filter((visual) => visual.visual_kind === "table").length) {
    return candidates[tableIndex] ?? null;
  }
  return null;
}

function checkColumn(table: DataQualityTableDto, check: DataQualityCheckDto, visual: StructuredVisualEvidence) {
  const prefix = `${table.name} · `;
  if (!check.object_name.startsWith(prefix)) return null;
  const requested = normalizedVisualLabel(check.object_name.slice(prefix.length));
  const columns = [...new Set(visual.cells.map((cell) => cell.column))];
  return columns.find((column) => normalizedVisualLabel(column) === requested)
    ?? columns.find((column) => {
      const normalized = normalizedVisualLabel(column);
      return normalized.includes(requested) || requested.includes(normalized);
    })
    ?? null;
}

function visualRiskHighlight(table: DataQualityTableDto, visual: StructuredVisualEvidence) {
  const rows = new Set<number>();
  const columns = new Set<string>();
  const cells = new Set<string>();
  const riskChecks = table.checks.filter((check) => check.conclusion === "warning" || check.conclusion === "high_warning");
  const markCell = (row: number, column: string) => {
    rows.add(row);
    columns.add(column);
    cells.add(`${row}\u0000${column}`);
  };

  for (const check of riskChecks) {
    if (check.rule_id === "structure.duplicates") {
      const tableColumns = [...new Set(visual.cells.map((cell) => cell.column))];
      const byRow = new Map<number, Map<string, string>>();
      for (const cell of visual.cells) {
        const row = byRow.get(cell.row) ?? new Map<string, string>();
        row.set(cell.column, cell.raw_value.trim());
        byRow.set(cell.row, row);
      }
      const signatures = new Map<string, number[]>();
      for (const [row, values] of byRow) {
        const signature = tableColumns.map((column) => values.get(column) ?? "").join("\u0000");
        signatures.set(signature, [...(signatures.get(signature) ?? []), row]);
      }
      for (const duplicateRows of signatures.values()) {
        if (duplicateRows.length < 2) continue;
        for (const row of duplicateRows) rows.add(row);
      }
      continue;
    }

    const column = checkColumn(table, check, visual);
    if (!column) continue;
    columns.add(column);
    const columnCells = visual.cells.filter((cell) => cell.column === column);
    if (check.rule_id === "structure.missing") {
      for (const cell of columnCells) {
        if (["", "nan", "null", "none", "na", "n/a", "-", "—"].includes(cell.raw_value.trim().toLocaleLowerCase("und"))) {
          markCell(cell.row, column);
        }
      }
    } else if (check.rule_id === "numeric.outliers") {
      const numeric = columnCells.flatMap((cell) => {
        const value = cell.numeric_value ?? (/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/u.test(cell.raw_value.trim()) ? Number(cell.raw_value) : null);
        return value === null || !Number.isFinite(value) ? [] : [{ cell, value }];
      });
      if (numeric.length >= 4) {
        const ordered = numeric.map((item) => item.value).sort((left, right) => left - right);
        const q1 = ordered[Math.floor(ordered.length / 4)];
        const q3 = ordered[Math.floor((ordered.length * 3) / 4)];
        const iqr = q3 - q1;
        if (iqr) for (const item of numeric) if (item.value < q1 - 3 * iqr || item.value > q3 + 3 * iqr) markCell(item.cell.row, column);
      }
    } else if (check.rule_id === "geo.range") {
      const latitude = /latitude|\blat\b|纬度/iu.test(column);
      const minimum = latitude ? -90 : -180;
      const maximum = latitude ? 90 : 180;
      for (const cell of columnCells) {
        const value = cell.numeric_value ?? Number(cell.raw_value);
        if (Number.isFinite(value) && (value < minimum || value > maximum)) markCell(cell.row, column);
      }
    }
  }
  return { rows: [...rows], columns: [...columns], cells: [...cells] };
}

function reportTableVisual(table: DataQualityTableDto): StructuredVisualEvidence | null {
  if (!table.columns?.length || !table.rows?.length) return null;
  return {
    kind: "structured_visual_evidence",
    id: `data-quality:${normalizedVisualLabel(table.name)}`,
    anchor_id: "",
    visual_kind: "table",
    axes: [],
    series: [],
    features: [],
    cells: table.rows.flatMap((row, rowIndex) => table.columns.map((column, columnIndex) => {
      const rawValue = row[columnIndex] ?? "";
      const numericValue = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/u.test(rawValue.trim()) ? Number(rawValue) : null;
      return { row: rowIndex + 1, column, raw_value: rawValue, numeric_value: numericValue, unit: null };
    })),
  };
}

const DataQualityTablePreview = memo(function DataQualityTablePreview({ detail, table, tableIndex }: { detail: SnapshotDetailDto | null; table: DataQualityTableDto; tableIndex: number }) {
  const { version, page, region, visual, highlight } = useMemo(() => {
    const match = tableVisualMatch(detail, table, tableIndex);
    const matchedVersion = detail?.graph.artifact_versions.find((item) => item.id === detail.snapshot.rootArtifactVersionId)
      ?? detail?.graph.artifact_versions[0]
      ?? null;
    const matchedVisual = match?.visual?.visual_kind === "table" ? match.visual : reportTableVisual(table);
    return {
      version: matchedVersion,
      page: match?.anchor.selector.page ?? table.page,
      region: match?.anchor.selector.region?.unit === "normalized" ? match.anchor.selector.region : table.region,
      visual: matchedVisual,
      highlight: matchedVisual ? visualRiskHighlight(table, matchedVisual) : null,
    };
  }, [detail, table, tableIndex]);
  const highlightedCount = highlight?.cells.length ?? 0;
  return (
    <div className="data-quality-preview" aria-label={`${table.name} 原表与风险位置`}>
      <section>
        <header><strong>原表</strong>{version?.media_type === "application/pdf" ? <PdfPagePreview sha256={version.sha256} page={page} title={table.name} triggerLabel="打开原始页" /> : null}</header>
        {version?.media_type === "application/pdf" && page && region ? (
          <PdfRegionPreview url={`/api/artifacts/${version.sha256}?disposition=inline`} page={page} region={region} label={table.name} presentation="gallery" minimumContentWidth={720} />
        ) : <p className="muted">已定位到{page ? `第 ${page} 页` : "论文原件"}；当前结果没有可安全裁切的表格边界，请打开原始页核对。</p>}
      </section>
      <section>
        <header><strong>结构化表格与风险位置</strong><span>{highlightedCount ? `已定位 ${highlightedCount} 个风险单元格` : "风险列已标色"}</span></header>
        {visual ? <StructuredVisualEvidenceView visual={visual} riskHighlight={highlight ?? undefined} /> : <p className="muted">这张表没有可与校验结果可靠对应的结构化单元格，请以左侧原表为准。</p>}
        {table.preview_truncated ? <p className="data-quality-preview-note">表格较大，此处显示前 500 行、前 100 列；完整结构请对照左侧原表。</p> : null}
        {visual && !highlightedCount && highlight?.columns.length ? <p className="data-quality-preview-note">当前规则只能定位到风险列，无法从报告中安全推断具体单元格。</p> : null}
      </section>
    </div>
  );
});

function DataQualityCheckTable({ checks }: { checks: DataQualityCheckDto[] }) {
  const conclusionLabel = (value: string) => ({
    pass: "通过",
    warning: "存在风险",
    high_warning: "存在风险",
    no_conclusion: "未作结论",
  } as Record<string, string>)[value] ?? value;
  return (
    <div className="data-quality-table-wrap">
      <table className="data-quality-table">
        <thead><tr><th>检查对象</th><th>规则</th><th>结论</th><th>检查证据</th><th>判定标准</th></tr></thead>
        <tbody>{checks.map((check, index) => (
          <tr key={`${check.rule_id}:${check.object_name}:${index}`} data-conclusion={check.conclusion}>
            <th scope="row">{check.object_name}</th>
            <td>{check.rule_name}</td>
            <td><span>{conclusionLabel(check.conclusion)}</span></td>
            <td>{check.evidence}</td>
            <td>{check.criterion}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function DataQualityPanel({ job, report, detail, error, onStart }: {
  job: DataQualityJobDto | null;
  report: DataQualityReportDto | null;
  detail: SnapshotDetailDto | null;
  error: string;
  onStart: () => void;
}) {
  const [openTable, setOpenTable] = useState<number | null>(null);
  const datasetChecks = report?.datasets.flatMap((dataset) => dataset.checks) ?? [];
  const visibleWarnings = report?.warnings.filter(visibleDataQualityWarning) ?? [];
  return (
    <section className="data-quality-ledger" aria-labelledby="data-quality-title" aria-busy={job?.status === "queued" || job?.status === "running"}>
      <header>
        <div><h3 id="data-quality-title">表格与开放数据校验</h3></div>
        {job ? <span data-status={job.status}>{job.status === "completed" ? "检查完成" : job.status === "failed" ? "检查失败" : `检查中 ${job.progress}%`}</span> : null}
      </header>
      {!job ? (
        <div className="data-quality-empty">
          <strong>这篇论文尚未运行数据检查</strong>
          <p>读取论文表格，并识别文中明确声明的数据集入口。</p>
          <button className="button" type="button" onClick={onStart}>运行数据检查</button>
        </div>
      ) : null}
      {job && job.status !== "completed" && job.status !== "failed" ? (
        <div className="data-quality-progress" role="status">
          <progress max="100" value={job.progress} />
          <strong>{job.stage === "parsing" ? "正在读取论文结构" : job.stage === "checking" ? "正在检查表格并识别数据入口" : "正在等待数据检查"}</strong>
        </div>
      ) : null}
      {error || job?.status === "failed" ? (
        <div className="data-quality-error" role="alert"><strong>数据检查未完成</strong><p>{error || job?.error?.message || "请稍后重试"}</p><button className="text-button" type="button" onClick={onStart}>重新检查</button></div>
      ) : null}
      {report ? (
        <>
          <div className="data-quality-summary" aria-label="数据校验摘要">
            <span><b>{report.summary.paper_table_count}</b> 论文表格</span>
            <span><b>{report.summary.dataset_count}</b> 数据入口</span>
            <span><b>{report.summary.pass}</b> 通过</span>
            <span data-tone="warning"><b>{report.summary.warning + report.summary.high_warning}</b> 存在风险</span>
          </div>
          {visibleWarnings.length ? <ul className="data-quality-notes">{visibleWarnings.map((warning) => <li key={warning}>{warning}</li>)}</ul> : null}
          {report.paper_tables.map((table, index) => {
            const expanded = openTable === index;
            return (
              <section className="data-quality-object" key={`${table.name}:${table.page ?? "unknown"}:${index}`}>
                <header>
                  <div><h4>{table.name}</h4><p>{table.page ? `第 ${table.page} 页 · ` : ""}{table.row_count} 行 × {table.column_count} 列</p></div>
                  <button className="button secondary" type="button" aria-expanded={expanded} onClick={() => setOpenTable(expanded ? null : index)}>{expanded ? "收起原表" : "查看原表与风险位置"}</button>
                </header>
                {expanded ? <DataQualityTablePreview detail={detail} table={table} tableIndex={index} /> : null}
                <DataQualityCheckTable checks={table.checks} />
              </section>
            );
          })}
          {datasetChecks.length ? <section className="data-quality-object dataset"><header><div><h4>开放数据入口</h4><p>{report.datasets.length} 个入口</p></div></header><DataQualityCheckTable checks={datasetChecks} /></section> : null}
        </>
      ) : null}
    </section>
  );
}

function cancelPendingComparisonJobs(jobs: ComparisonJob[]): ComparisonJob[] {
  return jobs.map((job) => ["queued", "running"].includes(job.status)
    ? { ...job, status: "cancelled", error: "" }
    : job);
}

export function EvidenceSynthesis({ active = true, mode, artifacts, onOpenSnapshot, onUploadPdf }: Props) {
  const idPrefix = `evidence-synthesis-${mode}`;
  const completedArtifacts = useMemo(
    () => artifacts.filter((artifact) => artifact.libraryStatus === "completed" && artifact.snapshot),
    [artifacts],
  );
  const [selectedSnapshotId, setSelectedSnapshotId] = useState("");
  const [remoteSelectedArtifact, setRemoteSelectedArtifact] = useState<LibraryArtifactDto | null>(null);
  const activeSnapshotId = selectedSnapshotId || completedArtifacts[0]?.snapshot?.id || remoteSelectedArtifact?.snapshot?.id || "";
  const [detail, setDetail] = useState<SnapshotDetailDto | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [detailReload, setDetailReload] = useState(0);
  const [selectedPaperClaims, setSelectedPaperClaims] = useState<Set<string>>(new Set());
  const [paperClaimsExpanded, setPaperClaimsExpanded] = useState(false);
  const [viewpoint, setViewpoint] = useState("");
  const [normalization, setNormalization] = useState<EvidenceSynthesisNormalizeResponse | null>(null);
  const [normalizationBusy, setNormalizationBusy] = useState(false);
  const [normalizationError, setNormalizationError] = useState("");
  const [selectedNormalizedClaims, setSelectedNormalizedClaims] = useState<Set<string>>(new Set());
  const [normalizedClaimsExpanded, setNormalizedClaimsExpanded] = useState(false);
  const [comparisonStatus, setComparisonStatus] = useState<ComparisonStatus>("idle");
  const [jobs, setJobs] = useState<ComparisonJob[]>([]);
  const [resultsExpanded, setResultsExpanded] = useState(false);
  const [evidenceModule, setEvidenceModule] = useState<EvidenceModule>("reasoning");
  // Roving-tabindex tabs need arrow/Home/End keys, or the inactive tab is unreachable by keyboard.
  const moveEvidenceTab = (event: { key: string; preventDefault(): void }) => {
    const order: EvidenceModule[] = ["reasoning", "data"];
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const current = order.indexOf(evidenceModule);
    const next = event.key === "Home" ? 0
      : event.key === "End" ? order.length - 1
        : (current + (event.key === "ArrowRight" ? 1 : order.length - 1)) % order.length;
    setEvidenceModule(order[next]);
    document.getElementById(`${idPrefix}-tab-${order[next]}`)?.focus();
  };
  const [pendingVersionId, setPendingVersionId] = useState("");
  const [uploadingPdf, setUploadingPdf] = useState(false);
  const [dataQualityVersionId, setDataQualityVersionId] = useState("");
  const [dataQualityReload, setDataQualityReload] = useState(0);
  const dataQualityActionController = useRef<AbortController | null>(null);
  const [dataQualityJob, setDataQualityJob] = useState<DataQualityJobDto | null>(null);
  const [dataQualityReport, setDataQualityReport] = useState<DataQualityReportDto | null>(null);
  const [dataQualityError, setDataQualityError] = useState("");
  const pdfInput = useRef<HTMLInputElement>(null);
  const detailController = useRef<AbortController | null>(null);
  const loadedDetailKey = useRef("");
  const normalizationController = useRef<AbortController | null>(null);
  const comparisonController = useRef<AbortController | null>(null);
  const normalizationGeneration = useRef(0);
  const comparisonGeneration = useRef(0);

  const resetComparison = useCallback(() => {
    comparisonGeneration.current += 1;
    comparisonController.current?.abort();
    comparisonController.current = null;
    setComparisonStatus("idle");
    setJobs([]);
    setResultsExpanded(false);
  }, []);

  useEffect(() => {
    if (!active) return;
    return () => {
      detailController.current?.abort();
      dataQualityActionController.current?.abort();
      normalizationController.current?.abort();
      normalizationController.current = null;
      normalizationGeneration.current += 1;
      setNormalizationBusy(false);
      comparisonController.current?.abort();
      comparisonController.current = null;
      comparisonGeneration.current += 1;
      setJobs(cancelPendingComparisonJobs);
      setComparisonStatus((current) => current === "running" ? "cancelled" : current);
    };
  }, [active]);

  useEffect(() => {
    if (!active || mode !== "paper" || !activeSnapshotId) {
      detailController.current?.abort();
      detailController.current = null;
      return;
    }
    const requestKey = `${activeSnapshotId}:${detailReload}`;
    if (loadedDetailKey.current === requestKey) return;
    loadedDetailKey.current = "";
    detailController.current?.abort();
    const controller = new AbortController();
    detailController.current = controller;
    const frame = window.requestAnimationFrame(() => void (async () => {
      setDetail(null);
      setDetailLoading(true);
      setDetailError("");
      setSelectedPaperClaims(new Set());
      setPaperClaimsExpanded(false);
      resetComparison();
      try {
        const response = await fetch(`/api/evidence-graphs/${encodeURIComponent(activeSnapshotId)}`, {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = await readJson(response);
        if (!response.ok) throw new Error(responseError(payload, "论文证据读取失败，请稍后重试"));
        const next = payload as SnapshotDetailDto;
        if (next.snapshot.id !== activeSnapshotId) {
          throw new Error("论文证据未能正确载入");
        }
        if (controller.signal.aborted) return;
        loadedDetailKey.current = requestKey;
        setDetail(next);
        setSelectedPaperClaims(new Set(next.graph.claims.slice(0, DEFAULT_VISIBLE_CLAIMS).map((claim) => claim.id)));
      } catch (cause) {
        if (controller.signal.aborted) return;
        setDetailError(cause instanceof Error ? cause.message : "论文证据读取失败，请稍后重试");
      } finally {
        if (!controller.signal.aborted) setDetailLoading(false);
        if (detailController.current === controller) detailController.current = null;
      }
    })());
    return () => {
      window.cancelAnimationFrame(frame);
      controller.abort();
    };
  }, [active, activeSnapshotId, detailReload, mode, resetComparison]);

  const paperClaims = useMemo(
    () => detail?.snapshot.id === activeSnapshotId ? detail.graph.claims : [],
    [activeSnapshotId, detail],
  );
  const normalizedClaims = useMemo(
    () => normalization?.normalization.claims ?? [],
    [normalization],
  );
  const selectableNormalizedClaims = normalizedClaims.filter(normalizedClaimSelectable);
  const selectedArtifact = completedArtifacts.find((artifact) => artifact.snapshot?.id === activeSnapshotId)
    ?? (remoteSelectedArtifact?.snapshot?.id === activeSnapshotId ? remoteSelectedArtifact : null);
  const activeVersionId = selectedArtifact?.latestVersion?.id || pendingVersionId;

  useEffect(() => {
    if (!pendingVersionId) return;
    const completed = completedArtifacts.find((artifact) => artifact.latestVersion?.id === pendingVersionId && artifact.snapshot);
    if (!completed?.snapshot) return;
    const frame = window.requestAnimationFrame(() => {
      setSelectedSnapshotId(completed.snapshot!.id);
      setPendingVersionId("");
    });
    return () => window.cancelAnimationFrame(frame);
  }, [completedArtifacts, pendingVersionId]);

  const visibleDataQualityJob = dataQualityVersionId === activeVersionId ? dataQualityJob : null;
  const visibleDataQualityReport = dataQualityVersionId === activeVersionId ? dataQualityReport : null;
  const visibleDataQualityError = dataQualityVersionId === activeVersionId ? dataQualityError : "";

  useEffect(() => () => dataQualityActionController.current?.abort(), [activeVersionId]);

  useEffect(() => {
    if (!active || evidenceModule !== "data" || !activeVersionId) return;
    const controller = new AbortController();
    const frame = window.requestAnimationFrame(() => {
      void watchDataQuality(activeVersionId, controller.signal, (next) => {
        setDataQualityVersionId(activeVersionId);
        setDataQualityJob(next.job);
        setDataQualityReport(next.report);
        setDataQualityError("");
      }, Boolean(pendingVersionId)).catch((cause) => {
        if (controller.signal.aborted) return;
        setDataQualityVersionId(activeVersionId);
        setDataQualityJob(null);
        setDataQualityReport(null);
        setDataQualityError(cause instanceof Error ? cause.message : "数据检查状态读取失败，请稍后重试");
      });
    });
    return () => { window.cancelAnimationFrame(frame); controller.abort(); };
  }, [active, activeVersionId, dataQualityReload, evidenceModule, pendingVersionId]);

  const startDataQuality = async () => {
    if (!activeVersionId) return;
    dataQualityActionController.current?.abort();
    const controller = new AbortController();
    dataQualityActionController.current = controller;
    setDataQualityVersionId(activeVersionId);
    setDataQualityJob(null);
    setDataQualityReport(null);
    setDataQualityError("");
    try {
      const response = await fetch("/api/data-quality", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ artifact_version_id: activeVersionId }),
        signal: controller.signal,
      });
      const payload = await readJson(response);
      if (controller.signal.aborted) return;
      if (!response.ok) throw new Error(responseError(payload, "数据检查启动失败，请稍后重试"));
      setDataQualityJob((payload as { job: DataQualityJobDto }).job);
      setDataQualityReport(null);
      setDataQualityReload((value) => value + 1);
    } catch (cause) {
      if (!controller.signal.aborted) setDataQualityError(cause instanceof Error ? cause.message : "数据检查启动失败，请稍后重试");
    } finally {
      if (dataQualityActionController.current === controller) dataQualityActionController.current = null;
    }
  };

  const uploadPdf = async (file: File | undefined) => {
    if (!file) return;
    if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
      setDataQualityError("每次仅可上传一份 PDF 进行论文校验");
      return;
    }
    setUploadingPdf(true);
    setDataQualityError("");
    try {
      const receipt = await onUploadPdf(file);
      setPendingVersionId(receipt.artifactVersionId);
      setEvidenceModule("reasoning");
    } catch (cause) {
      setDataQualityError(cause instanceof Error ? cause.message : "PDF 上传失败，请稍后重试");
    } finally {
      setUploadingPdf(false);
      if (pdfInput.current) pdfInput.current.value = "";
    }
  };

  const sources = useMemo<ComparableSource[]>(() => {
    if (mode === "paper") {
      return paperClaims.flatMap((claim) => selectedPaperClaims.has(claim.id) ? [{
        key: `paper:${activeSnapshotId}:${claim.id}`,
        id: claim.id,
        canonicalClaim: claim.proposition,
        scope: claim.scope,
        requestSource: { kind: "paper_claim" as const, snapshotId: activeSnapshotId, claimId: claim.id },
      }] : []);
    }
    return normalizedClaims.flatMap((claim) => selectedNormalizedClaims.has(claim.id) && normalizedClaimSelectable(claim) ? [{
      key: `normalized:${claim.id}`,
      id: claim.id,
      canonicalClaim: claim.canonicalClaim,
      scope: claim.scope,
      requestSource: {
        kind: "normalized_claim" as const,
        claim: {
          id: claim.id,
          canonicalClaim: claim.canonicalClaim,
          claimType: claim.claimType,
          polarity: claim.polarity,
          modality: claim.modality,
          scope: claim.scope,
        },
      },
    }] : []);
  }, [activeSnapshotId, mode, normalizedClaims, paperClaims, selectedNormalizedClaims, selectedPaperClaims]);

  const compare = async () => {
    if (!sources.length || comparisonStatus === "running") return;
    const generation = comparisonGeneration.current + 1;
    comparisonGeneration.current = generation;
    comparisonController.current?.abort();
    const controller = new AbortController();
    comparisonController.current = controller;
    setComparisonStatus("running");
    setJobs(sources.map((source) => ({ source, status: "queued", result: null, error: "" })));

    const patchJob = (key: string, patch: Partial<ComparisonJob>) => {
      if (controller.signal.aborted || comparisonGeneration.current !== generation) return;
      setJobs((current) => current.map((job) => job.source.key === key ? { ...job, ...patch } : job));
    };

    await runParallelWorkPool(sources, COMPARISON_CONCURRENCY, async (source) => {
      if (controller.signal.aborted || comparisonGeneration.current !== generation) return;
      patchJob(source.key, { status: "running" });
      try {
        const response = await fetchStreamed("/api/evidence-synthesis", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ action: "compare", source: source.requestSource }),
          signal: controller.signal,
        });
        const payload = await readJson(response);
        if (!response.ok) throw new Error(responseError(payload, "当前 Claim 比较失败，请稍后重试"));
        const result = compareResponse(payload);
        patchJob(source.key, { status: "completed", result, error: "" });
      } catch (cause) {
        if (controller.signal.aborted || comparisonGeneration.current !== generation) return;
        patchJob(source.key, {
          status: "failed",
          result: null,
          error: cause instanceof Error ? cause.message : "当前 Claim 比较失败，请稍后重试",
        });
      }
    });

    if (!controller.signal.aborted && comparisonGeneration.current === generation) {
      comparisonController.current = null;
      setComparisonStatus("completed");
    }
  };

  const cancelComparison = () => {
    if (comparisonStatus !== "running") return;
    comparisonGeneration.current += 1;
    comparisonController.current?.abort();
    comparisonController.current = null;
    setJobs(cancelPendingComparisonJobs);
    setComparisonStatus("cancelled");
  };

  const normalizeViewpoint = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const text = viewpoint.trim();
    if (!text || normalizationBusy || comparisonStatus === "running") return;
    const generation = normalizationGeneration.current + 1;
    normalizationGeneration.current = generation;
    normalizationController.current?.abort();
    const controller = new AbortController();
    normalizationController.current = controller;
    setNormalizationBusy(true);
    setNormalizationError("");
    setNormalization(null);
    setSelectedNormalizedClaims(new Set());
    setNormalizedClaimsExpanded(false);
    resetComparison();
    try {
      const response = await fetchStreamed("/api/evidence-synthesis", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ action: "normalize", source: { kind: "viewpoint", text } }),
        signal: controller.signal,
      });
      const payload = await readJson(response);
      if (!response.ok) throw new Error(responseError(payload, "当前观点尚未整理为可比较的 Claim"));
      const next = payload as EvidenceSynthesisNormalizeResponse;
      if (controller.signal.aborted || normalizationGeneration.current !== generation) return;
      setNormalization(next);
      setSelectedNormalizedClaims(new Set(next.normalization.claims.filter(normalizedClaimSelectable).slice(0, DEFAULT_VISIBLE_CLAIMS).map((claim) => claim.id)));
    } catch (cause) {
      if (controller.signal.aborted || normalizationGeneration.current !== generation) return;
      setNormalizationError(cause instanceof Error ? cause.message : "当前观点尚未整理为可比较的 Claim");
    } finally {
      if (!controller.signal.aborted && normalizationGeneration.current === generation) setNormalizationBusy(false);
      if (normalizationController.current === controller) normalizationController.current = null;
    }
  };

  const togglePaperClaim = (claimId: string) => {
    if (comparisonStatus === "running") return;
    resetComparison();
    setSelectedPaperClaims((current) => {
      const next = new Set(current);
      if (next.has(claimId)) next.delete(claimId); else next.add(claimId);
      return next;
    });
  };

  const toggleNormalizedClaim = (claimId: string) => {
    if (comparisonStatus === "running") return;
    resetComparison();
    setSelectedNormalizedClaims((current) => {
      const next = new Set(current);
      if (next.has(claimId)) next.delete(claimId); else next.add(claimId);
      return next;
    });
  };

  const settledJobs = jobs.filter((job) => ["completed", "failed", "cancelled"].includes(job.status)).length;
  const failedJobs = jobs.filter((job) => job.status === "failed").length;
  const comparisonDisabled = !sources.length || comparisonStatus === "running" || normalizationBusy || detailLoading;

  return (
    <section className={mode === "paper" ? "synthesis-page" : "evidence-synthesis"} aria-labelledby={`${idPrefix}-title`}>
      <header className="synthesis-heading page-intro">
        <div>
          {mode === "paper" ? null : <span className="ledger-label">观点</span>}
          {mode === "paper" ? <h1 id={`${idPrefix}-title`}>论文校验</h1> : <h2 id={`${idPrefix}-title`}>先明确待核对观点，再比较论文证据</h2>}
          {mode === "paper" ? <p>逐条核对论文 Claim 的推理链与数据质量。</p> : null}
        </div>
      </header>

      <SynthesisSurface separated={mode === "paper"}>
        {mode === "paper" ? (
          <>
            <section className="synthesis-paper-actions" aria-label="选择要校验的论文">
              <button className="synthesis-upload-action" type="button" disabled={uploadingPdf} onClick={() => pdfInput.current?.click()}><UiIcon name="upload" /><span>{uploadingPdf ? "正在上传…" : "上传新 PDF 并分析"}</span></button>
              <HistoricalPaperPicker selectedSnapshotId={activeSnapshotId} disabled={comparisonStatus === "running" || uploadingPdf} onSelect={(artifact) => { if (!artifact.snapshot) return; resetComparison(); setPendingVersionId(""); setRemoteSelectedArtifact(artifact); setSelectedSnapshotId(artifact.snapshot.id); }} />
              <input ref={pdfInput} hidden type="file" accept="application/pdf,.pdf" onChange={(event) => void uploadPdf(event.target.files?.[0])} />
            </section>

            <nav className="synthesis-evidence-tabs" role="tablist" aria-label="论文校验类型">
              <button id={`${idPrefix}-tab-reasoning`} data-state={pendingVersionId ? "running" : selectedArtifact ? "completed" : "idle"} role="tab" type="button" aria-selected={evidenceModule === "reasoning"} aria-controls={`${idPrefix}-panel-reasoning`} tabIndex={evidenceModule === "reasoning" ? 0 : -1} onKeyDown={moveEvidenceTab} onClick={() => setEvidenceModule("reasoning")}><span>推理校验</span>{pendingVersionId ? <strong>正在提取 Claim 并核验证据链</strong> : !selectedArtifact ? <strong>等待选择论文</strong> : null}</button>
              <button id={`${idPrefix}-tab-data`} data-state={visibleDataQualityJob?.status ?? "idle"} role="tab" type="button" aria-selected={evidenceModule === "data"} aria-controls={`${idPrefix}-panel-data`} tabIndex={evidenceModule === "data" ? 0 : -1} onKeyDown={moveEvidenceTab} onClick={() => setEvidenceModule("data")}><span>数据校验</span>{visibleDataQualityJob?.status === "completed" ? <strong>{visibleDataQualityReport?.summary.check_count ?? 0} 项检查已完成</strong> : visibleDataQualityJob?.status === "failed" ? <strong>检查失败，可独立重试</strong> : visibleDataQualityJob ? <strong>正在{visibleDataQualityJob.stage === "parsing" ? "解析论文" : "检查数据"} · {visibleDataQualityJob.progress}%</strong> : !selectedArtifact ? <strong>等待选择论文</strong> : null}</button>
            </nav>
          </>
        ) : null}

      <div id={`${idPrefix}-panel-reasoning`} role={mode === "paper" ? "tabpanel" : undefined} aria-labelledby={mode === "paper" ? `${idPrefix}-tab-reasoning` : undefined} hidden={mode === "paper" && evidenceModule !== "reasoning"} className="synthesis-workbench">
        <aside className="synthesis-docket" aria-label={mode === "paper" ? "论文 Claim 选择" : "选择待核对观点"}>
          {mode === "paper" ? (
            <>
              {detailLoading ? (
                <div className="synthesis-docket-loading" role="status"><span /><span /><span /><p>正在读取论文 Claim 与 Scope…</p></div>
              ) : null}
              {detailError ? (
                <div className="synthesis-docket-error" role="alert"><strong>论文证据未载入</strong><p>{detailError}。</p><button className="text-button" type="button" onClick={() => setDetailReload((value) => value + 1)}>重新读取</button></div>
              ) : null}
              {!detailLoading && !detailError && selectedArtifact ? (
                <fieldset className="synthesis-claim-selector">
                  <legend>
                    <span>论文 Claims</span>
                    <small>{paperClaims.length ? `已选 ${selectedPaperClaims.size} / ${paperClaims.length}` : "本篇暂无"}</small>
                  </legend>
                  {paperClaims.length ? (
                    <>
                      <div className="synthesis-select-actions">
                        <button className="text-button" type="button" disabled={comparisonStatus === "running" || selectedPaperClaims.size === paperClaims.length} onClick={() => { resetComparison(); setSelectedPaperClaims(new Set(paperClaims.map((claim) => claim.id))); }}>全选</button>
                        <button className="text-button" type="button" disabled={comparisonStatus === "running" || !selectedPaperClaims.size} onClick={() => { resetComparison(); setSelectedPaperClaims(new Set()); }}>清除</button>
                      </div>
                      <ol>
                        {(paperClaimsExpanded ? paperClaims : paperClaims.slice(0, DEFAULT_VISIBLE_CLAIMS)).map((claim, index) => (
                          <li key={claim.id}>
                            <div className="synthesis-claim-row">
                              <label className="synthesis-claim-control" aria-label={`选择 Claim ${index + 1}`}>
                              <input type="checkbox" disabled={comparisonStatus === "running"} checked={selectedPaperClaims.has(claim.id)} onChange={() => togglePaperClaim(claim.id)} />
                              <span>{String(index + 1).padStart(2, "0")}</span>
                              </label>
                              <span className="synthesis-claim-copy">
                                <ExpandableClaim value={claim.proposition} />
                                <ClaimScope scope={claim.scope} />
                                <small>{claimTypeLabel(claim.claim_type)} · {claim.polarity} · {claim.modality}</small>
                              </span>
                            </div>
                          </li>
                        ))}
                      </ol>
                      <ClaimListDisclosure total={paperClaims.length} visibleCount={DEFAULT_VISIBLE_CLAIMS} expanded={paperClaimsExpanded} onToggle={() => setPaperClaimsExpanded((current) => !current)} />
                    </>
                  ) : <div className="synthesis-zero-claims"><strong>本篇论文暂无可比较的观点。</strong></div>}
                </fieldset>
              ) : null}
            </>
          ) : (
            <>
              <form className="synthesis-viewpoint-form" onSubmit={normalizeViewpoint}>
                <label htmlFor={`${idPrefix}-viewpoint`}>观点或研究问题</label>
                <textarea
                  id={`${idPrefix}-viewpoint`}
                  rows={7}
                  maxLength={8000}
                  value={viewpoint}
                  disabled={normalizationBusy || comparisonStatus === "running"}
                  placeholder="例如：在温带山地流域中，大规模植被恢复会在 20 年尺度上降低年径流量。"
                  onChange={(event) => {
                    normalizationGeneration.current += 1;
                    normalizationController.current?.abort();
                    normalizationController.current = null;
                    setNormalizationBusy(false);
                    setViewpoint(event.target.value);
                    setNormalization(null);
                    setNormalizationError("");
                    setSelectedNormalizedClaims(new Set());
                    setNormalizedClaimsExpanded(false);
                    resetComparison();
                  }}
                />
                <p>请尽量写明研究对象、关系方向与成立条件。</p>
                <button className="button" type="submit" disabled={!viewpoint.trim() || normalizationBusy || comparisonStatus === "running"}>{normalizationBusy ? "正在整理观点…" : "整理为可比较的 Claim"}</button>
              </form>

              {normalizationBusy ? <div className="synthesis-docket-loading" role="status"><span /><span /><span /><p>正在核对观点边界与 Scope…</p></div> : null}
              {normalizationError ? <div className="synthesis-docket-error" role="alert"><strong>观点尚未整理完成</strong><p>{normalizationError}。已输入文本仍保留，可修改后重试。</p></div> : null}
              {normalization ? (
                <fieldset className="synthesis-claim-selector normalized">
                  <legend><span>可比较的 Claim</span><small>已选 {selectedNormalizedClaims.size} / {selectableNormalizedClaims.length}</small></legend>
                  {normalization.normalization.clarification ? <div className="synthesis-clarification" role="status"><strong>需要补充确认的信息</strong><ScientificContent value={normalization.normalization.clarification} /></div> : null}
                  {normalizedClaims.length ? (
                    <>
                      {selectableNormalizedClaims.length ? <div className="synthesis-select-actions"><button className="text-button" type="button" disabled={comparisonStatus === "running" || selectedNormalizedClaims.size === selectableNormalizedClaims.length} onClick={() => { resetComparison(); setSelectedNormalizedClaims(new Set(selectableNormalizedClaims.map((claim) => claim.id))); }}>全选可比较的 Claim</button><button className="text-button" type="button" disabled={comparisonStatus === "running" || !selectedNormalizedClaims.size} onClick={() => { resetComparison(); setSelectedNormalizedClaims(new Set()); }}>清除</button></div> : null}
                      <ol>
                        {(normalizedClaimsExpanded ? normalizedClaims : normalizedClaims.slice(0, DEFAULT_VISIBLE_CLAIMS)).map((claim, index) => {
                          const selectable = normalizedClaimSelectable(claim);
                          const origin = claim.originSpan;
                          return (
                            <li key={claim.id} data-status={selectable ? "ready" : "clarification"}>
                              <div className="synthesis-claim-row">
                                <label className="synthesis-claim-control" aria-label={`选择 Claim ${index + 1}`}>
                                <input type="checkbox" checked={selectedNormalizedClaims.has(claim.id)} disabled={!selectable || comparisonStatus === "running"} onChange={() => toggleNormalizedClaim(claim.id)} />
                                <span>{String(index + 1).padStart(2, "0")}</span>
                                </label>
                                <span className="synthesis-claim-copy">
                                  <ExpandableClaim value={claim.canonicalClaim} />
                                  <ClaimScope scope={claim.scope} />
                                  <small>{normalizedStatusLabel(claim.status)} · {claimTypeLabel(claim.claimType)}{claim.polarity ? ` · ${claim.polarity}` : ""}{claim.modality ? ` · ${claim.modality}` : ""}</small>
                                  {origin ? <small className="synthesis-origin-span">原始表述：{origin}</small> : null}
                                  {claim.clarification ? <ScientificContent value={claim.clarification} /> : null}
                                </span>
                              </div>
                            </li>
                          );
                        })}
                      </ol>
                      <ClaimListDisclosure total={normalizedClaims.length} visibleCount={DEFAULT_VISIBLE_CLAIMS} expanded={normalizedClaimsExpanded} onToggle={() => setNormalizedClaimsExpanded((current) => !current)} />
                    </>
                  ) : <div className="synthesis-zero-claims"><strong>尚未形成可比较的 Claim</strong><p>{normalization.normalization.clarification || "请补充研究对象、关系方向或成立条件后重试。"}</p></div>}
                </fieldset>
              ) : null}
            </>
          )}

          <div className="synthesis-run-controls">
            <button className="button" type="button" disabled={comparisonDisabled} onClick={() => void compare()}>{comparisonStatus === "running" ? "正在比较…" : sources.length ? `比较选中的 ${sources.length} 条 Claim` : "请先选择 Claim"}</button>
            {comparisonStatus === "running" ? <button className="button secondary" type="button" onClick={cancelComparison}>取消本次比较</button> : null}
            {jobs.length ? (
              <div className="synthesis-run-progress" aria-live="polite">
                <progress max={jobs.length} value={settledJobs} aria-label="证据综合进度" />
                <p>已完成 {settledJobs} / {jobs.length}{failedJobs ? ` · ${failedJobs} 条未完成` : ""}{comparisonStatus === "cancelled" ? " · 本次已取消" : ""}</p>
              </div>
            ) : null}
          </div>
        </aside>

        <section className="synthesis-ledger" aria-labelledby={`${idPrefix}-ledger-title`} aria-busy={comparisonStatus === "running"}>
          <header>
            <div><span className="ledger-label">关系分析</span><h3 id={`${idPrefix}-ledger-title`}>按所选 Claim 汇总的比较结果</h3></div>
            <span>{jobs.length ? `${jobs.length} 条已选 Claim` : "尚未开始"}</span>
          </header>
          {!jobs.length ? (
            <div className="synthesis-ledger-guide">
              <ol>{GROUPS.map((group) => <li key={group.key}><span>{group.title}</span></li>)}</ol>
            </div>
          ) : <div className="synthesis-results">
            {(resultsExpanded ? jobs : jobs.slice(0, DEFAULT_VISIBLE_CLAIMS)).map((job, index) => <ComparisonResult index={index} job={job} onOpenSnapshot={onOpenSnapshot} key={job.source.key} />)}
            <ClaimListDisclosure total={jobs.length} visibleCount={DEFAULT_VISIBLE_CLAIMS} expanded={resultsExpanded} onToggle={() => setResultsExpanded((current) => !current)} />
          </div>}
        </section>
      </div>
        {mode === "paper" && evidenceModule === "data" ? (
          <div id={`${idPrefix}-panel-data`} role="tabpanel" aria-labelledby={`${idPrefix}-tab-data`}>
            <DataQualityPanel job={visibleDataQualityJob} report={visibleDataQualityReport} detail={detail} error={visibleDataQualityError} onStart={() => void startDataQuality()} />
          </div>
        ) : null}
      </SynthesisSurface>
    </section>
  );
}
