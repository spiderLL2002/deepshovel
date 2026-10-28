"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { readJson, responseError } from "../workspace-types";
import { ScientificContent } from "./scientific-content-lazy";
import { ClaimListDisclosure } from "./claim-disclosure";
import { PaginationControls } from "./pagination-controls";
import { TaxonomyPanorama } from "./taxonomy-panorama";
import type { TaxonomySceneNode } from "./taxonomy-scene";

type ClaimProcessingStatus = "pending" | "processed";
export type OpenAlexSearchScope = {
  level: "domain" | "field" | "subfield" | "topic";
  id: string;
  label: string;
};

type KnowledgeGraphClaim = {
  snapshotId: string;
  claimId: string;
  paperId: string;
  paperFamilyId: string;
  canonicalClaim: string;
  paperTitle: string;
  status: ClaimProcessingStatus;
  processedAt: string | null;
  error: unknown;
  incomingEdges: number;
  outgoingEdges: number;
  comparisonEdges: number;
};

type GraphNode = Pick<KnowledgeGraphClaim,
  "snapshotId" | "claimId" | "canonicalClaim" | "paperTitle"
>;
type GraphEdge = {
  id: string;
  sourceSnapshotId: string;
  sourceClaimId: string;
  targetSnapshotId: string;
  targetClaimId: string;
  modelRelation: "equivalent_to" | "supports" | "contradicts" | "refines" | "qualifies" | "supplements";
  confidence: number | null;
  createdAt: string;
};
type Neighborhood = {
  center: { snapshotId: string; claimId: string };
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
};
type TaxonomyNode = TaxonomySceneNode;
type Taxonomy = { nodes: TaxonomyNode[]; unclassifiedPapers: number };
type KnowledgeGraphPage = {
  counts: { all: number; unprocessed: number; processed: number; edges: number };
  scopeCounts: { all: number; unprocessed: number; processed: number; edges: number };
  claims: KnowledgeGraphClaim[];
  pageInfo: { page: number; pageSize: number; total: number | null; totalPages: number | null; hasMore: boolean; nextCursor: string | null };
  searchInfo?: { scannedClaims: number; complete: boolean; countsAreCorpusTotals: boolean };
  taxonomy: Taxonomy | null;
  neighborhood: Neighborhood | null;
};
type KnowledgeGraphTaxonomyPage = {
  counts: KnowledgeGraphPage["counts"];
  taxonomy: Taxonomy;
};
type Props = {
  onOpenSnapshot: (snapshotId: string, claimId: string) => void;
  onSearchScope: (scope: OpenAlexSearchScope) => void;
};

const EMPTY_COUNTS = { all: 0, unprocessed: 0, processed: 0, edges: 0 };
const EMPTY_TAXONOMY: Taxonomy = { nodes: [], unclassifiedPapers: 0 };
const DEFAULT_PAGE_SIZE = 20;
const TAXONOMY_CACHE_TTL_MS = 60_000;
const CLASSIFICATION_BACKFILL_BATCH_SIZE = 16;
const CLASSIFICATION_BACKFILL_IDLE_TIMEOUT_MS = 2_000;
const CLASSIFICATION_BACKFILL_FALLBACK_DELAY_MS = 1_000;
let taxonomyCache: { page: KnowledgeGraphTaxonomyPage; cachedAt: number } | null = null;
const LEVEL_ORDER: OpenAlexSearchScope["level"][] = ["domain", "field", "subfield", "topic"];
const GRAPH_RELATIONS = ["supports", "refines", "qualifies", "equivalent_to", "contradicts", "supplements"] as const;
const MIN_GRAPH_SCALE = 0.35;
const MAX_GRAPH_SCALE = 2;
const GRAPH_VIEW_PADDING = 32;

type GraphCanvasView = { scale: number; x: number; y: number };

function clampGraphScale(scale: number) {
  return Math.min(MAX_GRAPH_SCALE, Math.max(MIN_GRAPH_SCALE, scale));
}

function claimKey(snapshotId: string, claimId: string) {
  return `${snapshotId}\u0000${claimId}`;
}

function readTaxonomyCache() {
  return taxonomyCache && Date.now() - taxonomyCache.cachedAt < TAXONOMY_CACHE_TTL_MS
    ? taxonomyCache.page
    : null;
}

function scheduleIdleWork(work: () => void) {
  if (typeof window.requestIdleCallback === "function") {
    const handle = window.requestIdleCallback(() => work(), {
      timeout: CLASSIFICATION_BACKFILL_IDLE_TIMEOUT_MS,
    });
    return () => window.cancelIdleCallback(handle);
  }
  const handle = window.setTimeout(work, CLASSIFICATION_BACKFILL_FALLBACK_DELAY_MS);
  return () => window.clearTimeout(handle);
}

async function requestPage(
  cursor: string | null,
  pageSize: number,
  query: string,
  signal: AbortSignal,
  scope: OpenAlexSearchScope | null = null,
  claim: { snapshotId: string; claimId: string } | null = null,
) {
  const parameters = new URLSearchParams({ view: "claims", status: "all", limit: String(pageSize) });
  if (cursor) parameters.set("cursor", cursor);
  if (query) parameters.set("query", query);
  if (scope) {
    parameters.set("level", scope.level);
    parameters.set("id", scope.id);
  }
  if (claim) {
    parameters.set("claimSnapshotId", claim.snapshotId);
    parameters.set("claimId", claim.claimId);
  }
  const response = await fetch(`/api/evidence-knowledge-graph?${parameters}`, { cache: "no-store", signal });
  const payload = await readJson(response);
  if (!response.ok) throw new Error(responseError(payload, "证据图谱读取失败"));
  return payload as KnowledgeGraphPage;
}

async function requestTaxonomy(signal: AbortSignal, force = false) {
  if (!force && taxonomyCache && Date.now() - taxonomyCache.cachedAt < TAXONOMY_CACHE_TTL_MS) {
    return taxonomyCache.page;
  }
  const response = await fetch("/api/evidence-knowledge-graph?view=taxonomy", { cache: "no-store", signal });
  const payload = await readJson(response);
  if (!response.ok) throw new Error(responseError(payload, "OpenAlex 分类读取失败"));
  const page = payload as KnowledgeGraphTaxonomyPage;
  taxonomyCache = { page, cachedAt: Date.now() };
  return page;
}

function relationText(relation: GraphEdge["modelRelation"]) {
  if (relation === "equivalent_to") return "等价";
  if (relation === "contradicts") return "矛盾";
  if (relation === "refines") return "细化";
  if (relation === "qualifies") return "限定";
  if (relation === "supplements") return "补充";
  return "支持";
}

function isDirectionalRelation(relation: GraphEdge["modelRelation"]) {
  return relation === "supports" || relation === "refines" || relation === "qualifies";
}

function relationMarker(relation: GraphEdge["modelRelation"]) {
  return isDirectionalRelation(relation) ? `url(#claim-arrow-${relation})` : undefined;
}

function scopeFromNode(node: TaxonomyNode): OpenAlexSearchScope {
  return { level: node.level, id: node.id, label: node.displayName };
}

function TaxonomyLedger({
  taxonomy,
  path,
  claims,
  claimCount,
  relationCount,
  activeClaimKey,
  onReset,
  onSelectTaxonomy,
  onSearchTaxonomy,
  onSelectClaim,
  onOpenSnapshot,
}: {
  taxonomy: Taxonomy;
  path: TaxonomyNode[];
  claims: KnowledgeGraphClaim[];
  claimCount: number;
  relationCount: number;
  activeClaimKey: string | null;
  onReset: () => void;
  onSelectTaxonomy: (node: TaxonomyNode, levelIndex: number) => void;
  onSearchTaxonomy: (node: TaxonomyNode) => void;
  onSelectClaim: (claim: KnowledgeGraphClaim) => void;
  onOpenSnapshot: (snapshotId: string, claimId: string) => void;
}) {
  const disclosureKey = path.at(-1)?.id ?? "";
  const [claimDisclosure, setClaimDisclosure] = useState({ key: "", expanded: false });
  const claimsExpanded = claimDisclosure.key === disclosureKey && claimDisclosure.expanded;
  return (
    <section className="taxonomy-ledger" aria-label="OpenAlex 分类图谱">
      <TaxonomyPanorama
        nodes={taxonomy.nodes}
        path={path}
        claimCount={claimCount}
        relationCount={relationCount}
        onReset={onReset}
        onNavigatePath={(levelIndex) => onSelectTaxonomy(path[levelIndex], levelIndex)}
        onSelectTaxonomy={onSelectTaxonomy}
        onSearchTaxonomy={onSearchTaxonomy}
      />
      {path.length === LEVEL_ORDER.length ? (
        <section className="taxonomy-claim-results" aria-labelledby="taxonomy-claim-results-title">
          <header><div><h3 id="taxonomy-claim-results-title">Topic Claims</h3><small>{claims.length} 条观点</small></div></header>
          <div className="taxonomy-column-list">
            {(claimsExpanded ? claims : claims.slice(0, 1)).map((claim, index) => {
              const key = claimKey(claim.snapshotId, claim.claimId);
              return (
                <article className="taxonomy-entry taxonomy-claim-entry" data-state={activeClaimKey === key ? "current" : "branch"} key={key}>
                  <button className="taxonomy-entry-main" type="button" aria-pressed={activeClaimKey === key} onClick={() => onSelectClaim(claim)}>
                    <span>{String(index + 1).padStart(2, "0")}</span>
                    <div><small>{claim.paperTitle}</small><strong><ScientificContent value={claim.canonicalClaim} inline /></strong><em>指向当前观点 {claim.incomingEdges} · 当前观点指向 {claim.outgoingEdges} · 对照关系 {claim.comparisonEdges}</em></div>
                  </button>
                  <div className="taxonomy-entry-actions">
                    <button className="text-button" type="button" onClick={() => onOpenSnapshot(claim.snapshotId, claim.claimId)}>查看证据</button>
                  </div>
                </article>
              );
            })}
            <ClaimListDisclosure total={claims.length} expanded={claimsExpanded} onToggle={() => setClaimDisclosure({ key: disclosureKey, expanded: !claimsExpanded })} />
          </div>
        </section>
      ) : null}
    </section>
  );
}

function NeighborhoodNode({
  node,
  selected,
  onSelect,
  onOpenSnapshot,
}: {
  node: GraphNode;
  selected?: boolean;
  onSelect: () => void;
  onOpenSnapshot: () => void;
}) {
  return (
    <article className={`neighborhood-node${selected ? " selected" : ""}`}>
      <button type="button" className="neighborhood-node-main" onClick={onSelect}>
        <small>{node.paperTitle}</small>
        <strong><ScientificContent value={node.canonicalClaim} inline /></strong>
      </button>
      <button type="button" className="text-button" onClick={onOpenSnapshot}>查看证据来源</button>
    </article>
  );
}

function NeighborhoodGraph({
  graph,
  onSelect,
  onOpenSnapshot,
}: {
  graph: Neighborhood;
  onSelect: (node: GraphNode) => void;
  onOpenSnapshot: (snapshotId: string, claimId: string) => void;
}) {
  const nodes = new Map(graph.nodes.map((node) => [claimKey(node.snapshotId, node.claimId), node]));
  const centerKey = claimKey(graph.center.snapshotId, graph.center.claimId);
  const center = nodes.get(centerKey);
  type NeighborEntry = { node: GraphNode; edge: GraphEdge; kind: "incoming" | "outgoing" | "comparison" };
  const entries = graph.edges.flatMap((edge): NeighborEntry[] => {
    const sourceKey = claimKey(edge.sourceSnapshotId, edge.sourceClaimId);
    const targetKey = claimKey(edge.targetSnapshotId, edge.targetClaimId);
    if (sourceKey !== centerKey && targetKey !== centerKey) return [];
    const otherKey = sourceKey === centerKey ? targetKey : sourceKey;
    const node = nodes.get(otherKey);
    if (!node) return [];
    if (!isDirectionalRelation(edge.modelRelation)) return [{ node, edge, kind: "comparison" }];
    return [{ node, edge, kind: targetKey === centerKey ? "incoming" : "outgoing" }];
  });
  const incoming = entries.filter((entry) => entry.kind === "incoming");
  const outgoing = entries.filter((entry) => entry.kind === "outgoing");
  const comparisons = entries.filter((entry) => entry.kind === "comparison");
  const relationCounts = Object.fromEntries(GRAPH_RELATIONS.map((relation) => [
    relation,
    entries.filter(({ edge }) => edge.modelRelation === relation).length,
  ])) as Record<GraphEdge["modelRelation"], number>;
  const left = [...incoming, ...comparisons.filter((_, index) => index % 2 === 0)];
  const right = [...outgoing, ...comparisons.filter((_, index) => index % 2 === 1)];
  const nodeWidth = 300;
  const nodeHeight = 118;
  const centerWidth = 352;
  const centerHeight = 168;
  const perColumn = 5;
  const columnGap = 62;
  const rowGap = 24;
  const incomingColumns = Math.max(1, Math.ceil(left.length / perColumn));
  const outgoingColumns = Math.max(1, Math.ceil(right.length / perColumn));
  const rows = Math.max(1, Math.min(perColumn, left.length), Math.min(perColumn, right.length));
  const height = Math.max(620, 96 + rows * nodeHeight + Math.max(0, rows - 1) * rowGap);
  const centerX = 60 + incomingColumns * (nodeWidth + columnGap) + 100;
  const centerY = (height - centerHeight) / 2;
  const outgoingStartX = centerX + centerWidth + 170;
  const width = outgoingStartX + outgoingColumns * (nodeWidth + columnGap) + 60;
  const sidePoints = <T,>(items: T[], startX: number, reverseColumns = false) => items.map((item, index) => {
    const column = Math.floor(index / perColumn);
    const row = index % perColumn;
    const itemsInColumn = Math.min(perColumn, items.length - column * perColumn);
    const columnHeight = itemsInColumn * nodeHeight + Math.max(0, itemsInColumn - 1) * rowGap;
    const visualColumn = reverseColumns ? incomingColumns - column - 1 : column;
    return {
      item,
      x: startX + visualColumn * (nodeWidth + columnGap),
      y: (height - columnHeight) / 2 + row * (nodeHeight + rowGap),
    };
  });
  const incomingPoints = sidePoints(left, 60, true);
  const outgoingPoints = sidePoints(right, outgoingStartX);
  const canvasViewportRef = useRef<HTMLDivElement | null>(null);
  const canvasViewRef = useRef<GraphCanvasView>({ scale: 1, x: 0, y: 0 });
  const dragRef = useRef<{
    pointerId: number;
    clientX: number;
    clientY: number;
    x: number;
    y: number;
  } | null>(null);
  const [canvasView, setCanvasViewState] = useState<GraphCanvasView>({ scale: 1, x: 0, y: 0 });
  const [canvasDragging, setCanvasDragging] = useState(false);
  const setCanvasView = useCallback((next: GraphCanvasView) => {
    canvasViewRef.current = next;
    setCanvasViewState(next);
  }, []);
  const centerCanvasAtScale = useCallback((scale: number) => {
    const viewport = canvasViewportRef.current;
    if (!viewport) return;
    const boundedScale = clampGraphScale(scale);
    setCanvasView({
      scale: boundedScale,
      x: (viewport.clientWidth - width * boundedScale) / 2,
      y: (viewport.clientHeight - height * boundedScale) / 2,
    });
  }, [height, setCanvasView, width]);
  const fitCanvas = useCallback(() => {
    const viewport = canvasViewportRef.current;
    if (!viewport) return;
    const scale = clampGraphScale(Math.min(
      1,
      (viewport.clientWidth - GRAPH_VIEW_PADDING * 2) / width,
      (viewport.clientHeight - GRAPH_VIEW_PADDING * 2) / height,
    ));
    centerCanvasAtScale(scale);
  }, [centerCanvasAtScale, height, width]);
  const zoomCanvasAt = useCallback((scale: number, anchorX?: number, anchorY?: number) => {
    const viewport = canvasViewportRef.current;
    if (!viewport) return;
    const current = canvasViewRef.current;
    const nextScale = clampGraphScale(scale);
    const x = anchorX ?? viewport.clientWidth / 2;
    const y = anchorY ?? viewport.clientHeight / 2;
    const graphX = (x - current.x) / current.scale;
    const graphY = (y - current.y) / current.scale;
    setCanvasView({
      scale: nextScale,
      x: x - graphX * nextScale,
      y: y - graphY * nextScale,
    });
  }, [setCanvasView]);
  useEffect(() => {
    const frame = window.requestAnimationFrame(fitCanvas);
    return () => window.cancelAnimationFrame(frame);
  }, [fitCanvas, graph.center.claimId, graph.center.snapshotId]);
  useEffect(() => {
    const viewport = canvasViewportRef.current;
    if (!viewport) return;
    const handleWheel = (event: WheelEvent) => {
      event.preventDefault();
      const bounds = viewport.getBoundingClientRect();
      const speed = event.ctrlKey ? 0.004 : 0.0015;
      zoomCanvasAt(
        canvasViewRef.current.scale * Math.exp(-event.deltaY * speed),
        event.clientX - bounds.left,
        event.clientY - bounds.top,
      );
    };
    viewport.addEventListener("wheel", handleWheel, { passive: false });
    return () => viewport.removeEventListener("wheel", handleWheel);
  }, [zoomCanvasAt]);
  useEffect(() => {
    const viewport = canvasViewportRef.current;
    if (!viewport || typeof ResizeObserver === "undefined") return;
    let previousWidth = viewport.clientWidth;
    let previousHeight = viewport.clientHeight;
    const observer = new ResizeObserver(() => {
      if (viewport.clientWidth === previousWidth && viewport.clientHeight === previousHeight) return;
      previousWidth = viewport.clientWidth;
      previousHeight = viewport.clientHeight;
      fitCanvas();
    });
    observer.observe(viewport);
    return () => observer.disconnect();
  }, [fitCanvas]);
  const handleCanvasPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || (event.target as Element).closest("button, a, input, select, textarea")) return;
    const current = canvasViewRef.current;
    dragRef.current = {
      pointerId: event.pointerId,
      clientX: event.clientX,
      clientY: event.clientY,
      x: current.x,
      y: current.y,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    setCanvasDragging(true);
  };
  const handleCanvasPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    setCanvasView({
      ...canvasViewRef.current,
      x: drag.x + event.clientX - drag.clientX,
      y: drag.y + event.clientY - drag.clientY,
    });
  };
  const stopCanvasDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    setCanvasDragging(false);
  };
  const handleCanvasKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return;
    const current = canvasViewRef.current;
    const panStep = event.shiftKey ? 96 : 48;
    if (event.key === "+" || event.key === "=") zoomCanvasAt(current.scale * 1.2);
    else if (event.key === "-") zoomCanvasAt(current.scale / 1.2);
    else if (event.key === "0") fitCanvas();
    else if (event.key === "1") centerCanvasAtScale(1);
    else if (event.key === "ArrowLeft") setCanvasView({ ...current, x: current.x + panStep });
    else if (event.key === "ArrowRight") setCanvasView({ ...current, x: current.x - panStep });
    else if (event.key === "ArrowUp") setCanvasView({ ...current, y: current.y + panStep });
    else if (event.key === "ArrowDown") setCanvasView({ ...current, y: current.y - panStep });
    else return;
    event.preventDefault();
  };
  if (!center) return <p className="stage-empty">当前 Claim 不在现行文献范围内。</p>;
  return (
    <div className="neighborhood-graph">
      <div className="neighborhood-canvas-header">
        <div className="neighborhood-legend" aria-label="当前 Claim 关系方向统计">
          <span>指向当前 Claim <b>{incoming.length}</b></span>
          <span>对照关系 <b>{comparisons.length}</b></span>
          <span>由当前 Claim 指向 <b>{outgoing.length}</b></span>
        </div>
        <div className="neighborhood-relation-key" aria-label="Claim 六类关系图例">
          {GRAPH_RELATIONS.map((relation) => (
            <span key={relation} data-relation={relation} data-directional={isDirectionalRelation(relation)}>
              <i aria-hidden="true" />{relationText(relation)} <b>{relationCounts[relation]}</b>
            </span>
          ))}
        </div>
      </div>
      <div className="neighborhood-canvas-toolbar">
        <p><span aria-hidden="true">↕</span> 滚轮缩放 · 拖动空白处平移 · 双指触控板可缩放</p>
        <div className="neighborhood-canvas-controls" aria-label="关系图视图控制">
          <button className="icon-button" type="button" aria-label="缩小关系图" title="缩小（−）" disabled={canvasView.scale <= MIN_GRAPH_SCALE} onClick={() => zoomCanvasAt(canvasViewRef.current.scale / 1.2)}>−</button>
          <output aria-live="polite" aria-label="当前缩放比例">{Math.round(canvasView.scale * 100)}%</output>
          <button className="icon-button" type="button" aria-label="放大关系图" title="放大（+）" disabled={canvasView.scale >= MAX_GRAPH_SCALE} onClick={() => zoomCanvasAt(canvasViewRef.current.scale * 1.2)}>+</button>
          <button className="neighborhood-view-button" type="button" onClick={fitCanvas}>适应窗口</button>
          <button className="neighborhood-view-button" type="button" onClick={() => centerCanvasAtScale(1)}>100%</button>
        </div>
      </div>
      <div
        className="neighborhood-canvas-scroll"
        data-dragging={canvasDragging}
        ref={canvasViewportRef}
        tabIndex={0}
        role="region"
        aria-label="Claim 直接关系图。滚轮缩放，拖动空白处平移；键盘加减号缩放，方向键平移，数字 0 适应窗口，数字 1 恢复百分之百。"
        onKeyDown={handleCanvasKeyDown}
        onPointerDown={handleCanvasPointerDown}
        onPointerMove={handleCanvasPointerMove}
        onPointerUp={stopCanvasDrag}
        onPointerCancel={stopCanvasDrag}
        onLostPointerCapture={() => {
          dragRef.current = null;
          setCanvasDragging(false);
        }}
      >
      <div
        className="neighborhood-canvas-stage"
        style={{
          width,
          height,
          transform: `translate3d(${canvasView.x}px, ${canvasView.y}px, 0) scale(${canvasView.scale})`,
        }}
      >
      <svg className="neighborhood-canvas" viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="img" aria-label={`${incoming.length} 条 Claim 指向当前 Claim，当前 Claim 指向 ${outgoing.length} 条 Claim，另有 ${comparisons.length} 条等价、矛盾或补充关系`}>
        <defs>
          {(["supports", "refines", "qualifies"] as const).map((relation) => (
            <marker key={relation} id={`claim-arrow-${relation}`} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="strokeWidth">
              <path d="M 0 0 L 8 4 L 0 8 z" className={`claim-arrow-${relation}`} />
            </marker>
          ))}
        </defs>
        <g className="neighborhood-edges" aria-hidden="true">
          <circle cx={centerX + centerWidth / 2} cy={centerY + centerHeight / 2} r="150" />
          {incomingPoints.map(({ item: { edge, kind }, x, y }, index) => {
            const startX = x + nodeWidth;
            const startY = y + nodeHeight / 2;
            const endX = centerX - 7;
            const endY = centerY + centerHeight / 2;
            const middleX = startX + (endX - startX) * 0.54;
            const middleY = startY + (endY - startY) * 0.54;
            return (
              <g key={edge.id}>
                <path data-relation={edge.modelRelation} markerEnd={kind === "incoming" ? relationMarker(edge.modelRelation) : undefined} d={`M ${startX} ${startY} C ${startX + 82 + index % 3 * 12} ${startY}, ${endX - 100} ${endY}, ${endX} ${endY}`} />
                <text data-relation={edge.modelRelation} x={middleX} y={middleY - 8}>{relationText(edge.modelRelation)}</text>
              </g>
            );
          })}
          {outgoingPoints.map(({ item: { edge, kind }, x, y }, index) => {
            const startX = centerX + centerWidth + 7;
            const startY = centerY + centerHeight / 2;
            const endX = x;
            const endY = y + nodeHeight / 2;
            const middleX = startX + (endX - startX) * 0.48;
            const middleY = startY + (endY - startY) * 0.48;
            return (
              <g key={edge.id}>
                <path data-relation={edge.modelRelation} markerEnd={kind === "outgoing" ? relationMarker(edge.modelRelation) : undefined} d={`M ${startX} ${startY} C ${startX + 100} ${startY}, ${endX - 82 - index % 3 * 12} ${endY}, ${endX} ${endY}`} />
                <text data-relation={edge.modelRelation} x={middleX} y={middleY - 8}>{relationText(edge.modelRelation)}</text>
              </g>
            );
          })}
        </g>
        {incomingPoints.map(({ item: { node, edge }, x, y }) => (
          <foreignObject key={edge.id} x={x} y={y} width={nodeWidth} height={nodeHeight}>
            <NeighborhoodNode node={node} onSelect={() => onSelect(node)} onOpenSnapshot={() => onOpenSnapshot(node.snapshotId, node.claimId)} />
          </foreignObject>
        ))}
        <foreignObject x={centerX} y={centerY} width={centerWidth} height={centerHeight}>
          <div className="neighborhood-center-node">
            <span>当前 Claim · 被指向 {incoming.length} / 指向其他观点 {outgoing.length} / 对照关系 {comparisons.length}</span>
            <NeighborhoodNode node={center} selected onSelect={() => onSelect(center)} onOpenSnapshot={() => onOpenSnapshot(center.snapshotId, center.claimId)} />
          </div>
        </foreignObject>
        {outgoingPoints.map(({ item: { node, edge }, x, y }) => (
          <foreignObject key={edge.id} x={x} y={y} width={nodeWidth} height={nodeHeight}>
            <NeighborhoodNode node={node} onSelect={() => onSelect(node)} onOpenSnapshot={() => onOpenSnapshot(node.snapshotId, node.claimId)} />
          </foreignObject>
        ))}
      </svg>
      </div>
      </div>
      {graph.truncated ? <p className="knowledge-graph-note warning">与当前 Claim 直接关联的观点超过展示上限，目前仅显示其中一部分。</p> : null}
      <section className="neighborhood-edge-ledger" aria-labelledby="neighborhood-edge-ledger-title">
        <header>
          <span>RELATION LEDGER</span>
          <h3 id="neighborhood-edge-ledger-title">可逐条核对的 Claim 关系</h3>
          <p>下列记录与关系图展示同一组直接关系，并以文字标明方向和类型。</p>
        </header>
        {entries.length ? (
          <ol>
            {entries.map(({ node, edge, kind }) => {
              const direction = kind === "incoming" ? "指向当前 Claim" : kind === "outgoing" ? "由当前 Claim 指向" : "与当前 Claim 比较";
              const accessibleRelation = kind === "incoming"
                ? `${node.canonicalClaim} ${relationText(edge.modelRelation)} 当前 Claim`
                : kind === "outgoing"
                  ? `当前 Claim ${relationText(edge.modelRelation)} ${node.canonicalClaim}`
                  : `当前 Claim 与 ${node.canonicalClaim} 的关系是${relationText(edge.modelRelation)}`;
              return (
                <li aria-label={accessibleRelation} key={edge.id}>
                  <div>
                    <span>{direction}</span>
                    <strong data-relation={edge.modelRelation}>{relationText(edge.modelRelation)}</strong>
                    <small>{node.paperTitle}</small>
                    <ScientificContent value={node.canonicalClaim} />
                  </div>
                  <div className="neighborhood-edge-actions">
                    <button className="text-button" type="button" onClick={() => onSelect(node)}>查看这条 Claim</button>
                    <button className="text-button" type="button" onClick={() => onOpenSnapshot(node.snapshotId, node.claimId)}>查看证据来源</button>
                  </div>
                </li>
              );
            })}
          </ol>
        ) : <p className="stage-empty">当前 Claim 暂无已保存的直接关系。</p>}
      </section>
    </div>
  );
}

export function EvidenceKnowledgeGraph({ onOpenSnapshot, onSearchScope }: Props) {
  const initialTaxonomyPage = readTaxonomyCache();
  const [claimQuery, setClaimQuery] = useState("");
  const [claimQueryDraft, setClaimQueryDraft] = useState("");
  const [counts, setCounts] = useState(initialTaxonomyPage?.counts ?? EMPTY_COUNTS);
  const [claims, setClaims] = useState<KnowledgeGraphClaim[]>([]);
  const [pageInfo, setPageInfo] = useState({ page: 1, pageSize: DEFAULT_PAGE_SIZE, total: null as number | null, totalPages: null as number | null, hasMore: false, nextCursor: null as string | null });
  const claimCursors = useRef(new Map<number, string>());
  const [searchInfo, setSearchInfo] = useState<KnowledgeGraphPage["searchInfo"]>();
  const [taxonomy, setTaxonomy] = useState<Taxonomy>(initialTaxonomyPage?.taxonomy ?? EMPTY_TAXONOMY);
  const [path, setPath] = useState<TaxonomyNode[]>([]);
  const [neighborhood, setNeighborhood] = useState<Neighborhood | null>(null);
  const [loading, setLoading] = useState(false);
  const [taxonomyLoading, setTaxonomyLoading] = useState(!initialTaxonomyPage);
  const [classifying, setClassifying] = useState(false);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const readController = useRef<AbortController | null>(null);
  const taxonomyController = useRef<AbortController | null>(null);
  const readGeneration = useRef(0);
  const backfillStarted = useRef(false);
  const pageSizeRef = useRef(DEFAULT_PAGE_SIZE);
  const claimQueryRef = useRef("");
  const neighborhoodRef = useRef<HTMLElement | null>(null);
  const mounted = useRef(true);

  const selectedTopic = path.at(-1)?.level === "topic" ? path.at(-1)! : null;
  const selectedScope = useMemo(() => selectedTopic
    ? { level: selectedTopic.level, id: selectedTopic.id, label: selectedTopic.displayName } satisfies OpenAlexSearchScope
    : null, [selectedTopic]);
  const selectedScopeRef = useRef(selectedScope);
  const activeGraphClaimKey = neighborhood
    ? claimKey(neighborhood.center.snapshotId, neighborhood.center.claimId)
    : null;

  useEffect(() => {
    selectedScopeRef.current = selectedScope;
  }, [selectedScope]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      readController.current?.abort();
      taxonomyController.current?.abort();
    };
  }, []);

  const loadTaxonomyPage = useCallback(async (force = false) => {
    taxonomyController.current?.abort();
    const controller = new AbortController();
    taxonomyController.current = controller;
    setTaxonomyLoading(true);
    setError("");
    try {
      const page = await requestTaxonomy(controller.signal, force);
      if (!mounted.current || controller.signal.aborted) return;
      setCounts(page.counts);
      setTaxonomy(page.taxonomy);
    } catch (cause) {
      if (!controller.signal.aborted && mounted.current) {
        setError(cause instanceof Error ? cause.message : "OpenAlex 分类读取失败");
      }
    } finally {
      if (mounted.current && taxonomyController.current === controller) {
        setTaxonomyLoading(false);
        taxonomyController.current = null;
      }
    }
  }, []);

  const loadPage = useCallback(async (
    targetPage = 1,
    pageSize = pageSizeRef.current,
    scope: OpenAlexSearchScope | null = null,
    claim: { snapshotId: string; claimId: string } | null = null,
  ) => {
    const generation = readGeneration.current + 1;
    readGeneration.current = generation;
    readController.current?.abort();
    const controller = new AbortController();
    readController.current = controller;
    setLoading(true);
    setError("");
    try {
      if (targetPage === 1) claimCursors.current.clear();
      const page = await requestPage(claimCursors.current.get(targetPage) ?? null, pageSize, claimQueryRef.current, controller.signal, scope, claim);
      if (!mounted.current || generation !== readGeneration.current) return;
      setCounts(page.counts);
      pageSizeRef.current = page.pageInfo.pageSize;
      setClaims(page.claims);
      if (page.pageInfo.nextCursor) claimCursors.current.set(targetPage + 1, page.pageInfo.nextCursor);
      setPageInfo({ ...page.pageInfo, page: targetPage });
      setSearchInfo(page.searchInfo);
      if (page.taxonomy) setTaxonomy(page.taxonomy);
      if (claim) setNeighborhood(page.neighborhood);
    } catch (cause) {
      if (!controller.signal.aborted && mounted.current && generation === readGeneration.current) {
        setError(cause instanceof Error ? cause.message : "证据图谱读取失败");
      }
    } finally {
      if (mounted.current && generation === readGeneration.current) {
        setLoading(false);
        if (readController.current === controller) readController.current = null;
      }
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (selectedScope) {
        void loadPage(1, pageSizeRef.current, selectedScope);
        return;
      }
      setClaims([]);
      setNeighborhood(null);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadPage, selectedScope]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadTaxonomyPage(), 0);
    return () => window.clearTimeout(timer);
  }, [loadTaxonomyPage]);

  useEffect(() => {
    if (!neighborhood || !neighborhoodRef.current) return;
    const timer = window.setTimeout(() => neighborhoodRef.current?.scrollIntoView({
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      block: "start",
    }), 40);
    return () => window.clearTimeout(timer);
  }, [neighborhood]);

  useEffect(() => {
    if (!taxonomy.unclassifiedPapers || backfillStarted.current) return;
    const controller = new AbortController();
    const cancelIdleWork = scheduleIdleWork(() => {
      if (controller.signal.aborted || backfillStarted.current) return;
      backfillStarted.current = true;
      setClassifying(true);
      setActionError("");
      void (async () => {
        try {
          const response = await fetch("/api/evidence-knowledge-graph/classifications", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ limit: CLASSIFICATION_BACKFILL_BATCH_SIZE }),
            signal: controller.signal,
          });
          const payload = await readJson(response);
          if (!response.ok) throw new Error(responseError(payload, "OpenAlex 分类失败"));
          if (mounted.current) {
            await loadTaxonomyPage(true);
            const currentScope = selectedScopeRef.current;
            if (currentScope) await loadPage(1, pageSizeRef.current, currentScope);
          }
        } catch (cause) {
          if (!controller.signal.aborted && mounted.current) {
            setActionError(cause instanceof Error ? cause.message : "OpenAlex 分类失败");
          }
        } finally {
          if (mounted.current) setClassifying(false);
        }
      })();
    });
    return () => {
      cancelIdleWork();
      controller.abort();
    };
  }, [loadPage, loadTaxonomyPage, taxonomy.unclassifiedPapers]);

  const selectClaim = async (claim: Pick<KnowledgeGraphClaim, "snapshotId" | "claimId">) => {
    const controller = new AbortController();
    readController.current?.abort();
    readController.current = controller;
    setLoading(true);
    try {
      const parameters = new URLSearchParams({ view: "neighborhood", claimSnapshotId: claim.snapshotId, claimId: claim.claimId });
      const response = await fetch(`/api/evidence-knowledge-graph?${parameters}`, { cache: "no-store", signal: controller.signal });
      const payload = await readJson(response);
      if (!response.ok) throw new Error(responseError(payload, "直接关系读取失败"));
      const page = payload as { neighborhood: Neighborhood | null };
      if (!controller.signal.aborted && mounted.current) setNeighborhood(page.neighborhood);
    } catch (cause) {
      if (!controller.signal.aborted && mounted.current) setError(cause instanceof Error ? cause.message : "直接关系读取失败");
    } finally {
      if (mounted.current && readController.current === controller) setLoading(false);
    }
  };

  return (
    <section className="knowledge-graph-view" aria-labelledby="knowledge-graph-title">
      <header className="view-header page-intro knowledge-graph-header">
        <div>
          <h1 id="knowledge-graph-title">证据图谱</h1>
        </div>
      </header>

      {classifying ? <p className="knowledge-graph-note" role="status">正在补充论文分类…</p> : null}
      {actionError ? <p className="search-error" role="alert">{actionError}</p> : null}
      {error ? <p className="search-error" role="alert">{error}</p> : null}

      <div className="taxonomy-browser">
        {selectedTopic ? (
          <div className="taxonomy-controls">
            <div><strong>{selectedTopic.displayName}</strong></div>
            <form className="knowledge-claim-search" onSubmit={(event) => { event.preventDefault(); const query = claimQueryDraft.trim(); claimQueryRef.current = query; setClaimQuery(query); void loadPage(1, pageSizeRef.current, selectedScope); }}>
              <label htmlFor="knowledge-claim-query">搜索 Claim</label>
              <input id="knowledge-claim-query" value={claimQueryDraft} maxLength={200} placeholder="输入论文名或 Claim 关键词" onChange={(event) => setClaimQueryDraft(event.target.value)} />
              <button className="button secondary" type="submit">搜索</button>
              {claimQuery ? <button className="text-button" type="button" onClick={() => { setClaimQuery(""); setClaimQueryDraft(""); claimQueryRef.current = ""; void loadPage(1, pageSizeRef.current, selectedScope); }}>清除</button> : null}
            </form>
          </div>
        ) : null}
        {loading && selectedTopic && !claims.length ? <p className="taxonomy-loading" role="status">正在加载该主题的 Claim…</p> : null}
        {taxonomyLoading && !taxonomy.nodes.length ? (
          <div className="view-loading"><span />正在读取论文分类…</div>
        ) : taxonomy.nodes.length ? (
          <TaxonomyLedger
            taxonomy={taxonomy}
            path={path}
            claims={claims}
            claimCount={counts.all}
            relationCount={counts.edges}
            activeClaimKey={activeGraphClaimKey}
            onReset={() => { setPath([]); setNeighborhood(null); }}
            onSelectTaxonomy={(node, levelIndex) => {
              setPath([...path.slice(0, levelIndex), node]);
              setNeighborhood(null);
              if (node.level === "topic") setClaims([]);
            }}
            onSearchTaxonomy={(node) => onSearchScope(scopeFromNode(node))}
            onSelectClaim={(claim) => void selectClaim(claim)}
            onOpenSnapshot={onOpenSnapshot}
          />
        ) : (
          <div className="view-empty compact">
            <h2>暂无可浏览的证据图谱</h2>
          </div>
        )}
        {selectedTopic && !loading && !claims.length ? <p className="stage-empty">{pageInfo.hasMore ? "本页没有匹配的 Claim，可继续下一页。" : claimQuery ? "本页没有匹配的 Claim。" : "该 Topic 中没有 Claim。"}</p> : null}
        {selectedTopic && searchInfo ? <p role="status">本页检查 {searchInfo.scannedClaims.toLocaleString("zh-CN")} 条，找到 {claims.length} 条匹配。{searchInfo.complete ? "已到检索范围末尾。" : "可继续下一页检索。"}</p> : null}
        {selectedTopic && (claims.length || pageInfo.hasMore || pageInfo.page > 1) ? <PaginationControls cursorPagination hasMore={pageInfo.hasMore} page={pageInfo.page} pageSize={pageInfo.pageSize} total={pageInfo.total} disabled={loading} itemLabel="条 Claim" onPageChange={(page) => void loadPage(page, pageSizeRef.current, selectedScope)} onPageSizeChange={(pageSize) => { pageSizeRef.current = pageSize; void loadPage(1, pageSize, selectedScope); }} /> : null}
      </div>

      {neighborhood ? (
        <section className="neighborhood-section" ref={neighborhoodRef} aria-labelledby="neighborhood-title">
          <header><div><h2 id="neighborhood-title">Claim 直接关系图</h2><p>→ 支持 / 细化 / 限定　— 等价 / 矛盾 / 补充</p></div><span>{neighborhood.edges.length} 条关系</span></header>
          <NeighborhoodGraph graph={neighborhood} onSelect={(node) => void selectClaim(node)} onOpenSnapshot={onOpenSnapshot} />
        </section>
      ) : null}
    </section>
  );
}
