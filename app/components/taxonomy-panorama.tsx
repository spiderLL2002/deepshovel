"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import type { Graph as CosmosGraph } from "@cosmos.gl/graph";
import { UiIcon } from "./ui-icon";
import {
  buildTaxonomyFocusLayout,
  buildTaxonomyScene,
  distributionClaimCount,
  NO_TAXONOMY_OWNER,
  TAXONOMY_POINT_KIND,
  taxonomySceneKey,
  type TaxonomyScene,
  type TaxonomySceneLevel,
  type TaxonomySceneNode,
} from "./taxonomy-scene";

const LEVEL_ORDER: TaxonomySceneLevel[] = ["domain", "field", "subfield", "topic"];
const LEVEL_LABELS = { domain: "Domain", field: "Field", subfield: "Subfield", topic: "Topic" } as const;
const GRAPHITE = [0.09, 0.14, 0.25, 1] as const;
const ACCENT = [0.184, 0.392, 0.91, 1] as const;
const CONTEXT = [0.36, 0.43, 0.54, 0.66] as const;
const CLAIM = [0.322, 0.384, 0.478, 1] as const;
const CLAIM_FOCUSED = [0.18, 0.39, 0.76, 1] as const;
const CLAIM_DIMMED = [0.77, 0.81, 0.87, 1] as const;
const LINK_CONTEXT = [0.43, 0.51, 0.63, 0.34] as const;
const LINK_ACTIVE = [0.184, 0.392, 0.91, 0.78] as const;
const RING_POINT_COUNT = 4 * 112;

type Props = {
  nodes: TaxonomySceneNode[];
  path: TaxonomySceneNode[];
  claimCount: number;
  relationCount: number;
  onReset: () => void;
  onNavigatePath: (levelIndex: number) => void;
  onSelectTaxonomy: (node: TaxonomySceneNode, levelIndex: number) => void;
  onSearchTaxonomy: (node: TaxonomySceneNode) => void;
};

type HoveredPoint = {
  index: number;
  title: string;
  detail: string;
};

type SceneWorkerResponse = { key: string; scene: TaxonomyScene };

let sceneCache: { key: string; scene: TaxonomyScene } | null = null;
let graphModulePromise: Promise<typeof import("@cosmos.gl/graph")> | null = null;

function writeColor(target: Float32Array, index: number, color: readonly number[]) {
  const offset = index * 4;
  target[offset] = color[0];
  target[offset + 1] = color[1];
  target[offset + 2] = color[2];
  target[offset + 3] = color[3];
}

function loadGraphModule() {
  graphModulePromise ??= import("@cosmos.gl/graph");
  return graphModulePromise;
}

function childrenIndex(nodes: TaxonomySceneNode[]) {
  const index = new Map<string | null, TaxonomySceneNode[]>();
  nodes.forEach((node) => {
    const siblings = index.get(node.parentId) ?? [];
    siblings.push(node);
    index.set(node.parentId, siblings);
  });
  index.forEach((siblings) => siblings.sort((left, right) => distributionClaimCount(right) - distributionClaimCount(left) || left.displayName.localeCompare(right.displayName, "zh-CN")));
  return index;
}

function descendantsOf(focus: TaxonomySceneNode | null, children: Map<string | null, TaxonomySceneNode[]>, nodeIndexById: Map<string, number>) {
  if (!focus) return null;
  const descendants = new Set<number>();
  const queue = [focus];
  while (queue.length) {
    const node = queue.pop()!;
    const index = nodeIndexById.get(node.id);
    if (index !== undefined) descendants.add(index);
    queue.push(...(children.get(node.id) ?? []));
  }
  return descendants;
}

function useTaxonomyScene(nodes: TaxonomySceneNode[]) {
  const key = useMemo(() => taxonomySceneKey(nodes), [nodes]);
  const [state, setState] = useState<{ key: string; scene: TaxonomyScene } | null>(() => sceneCache?.key === key ? sceneCache : null);

  useEffect(() => {
    if (sceneCache?.key === key) {
      const cached = sceneCache;
      const timer = window.setTimeout(() => setState(cached), 0);
      return () => window.clearTimeout(timer);
    }
    let cancelled = false;
    let worker: Worker | null = null;
    const commit = (nextScene: TaxonomyScene) => {
      if (cancelled) return;
      sceneCache = { key, scene: nextScene };
      setState(sceneCache);
    };
    const buildOnMainThread = () => window.setTimeout(() => {
      if (!cancelled) commit(buildTaxonomyScene(nodes));
    }, 0);

    if (typeof Worker === "undefined") {
      const timer = buildOnMainThread();
      return () => {
        cancelled = true;
        window.clearTimeout(timer);
      };
    }

    try {
      worker = new Worker(new URL("./taxonomy-scene.worker.ts", import.meta.url), { type: "module" });
      worker.onmessage = (event: MessageEvent<SceneWorkerResponse>) => {
        if (event.data.key !== key) return;
        commit(event.data.scene);
        worker?.terminate();
        worker = null;
      };
      worker.onerror = () => {
        worker?.terminate();
        worker = null;
        buildOnMainThread();
      };
      worker.postMessage({ key, nodes });
    } catch {
      buildOnMainThread();
    }
    return () => {
      cancelled = true;
      worker?.terminate();
    };
  }, [key, nodes]);

  return state?.key === key ? state.scene : null;
}

function useReducedMotion() {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return reduced;
}

function GraphControlIcon({ kind }: { kind: "in" | "out" | "fit" }) {
  if (kind === "fit") return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5" /></svg>;
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="11" cy="11" r="6.5" />
      <path d="m16 16 4 4M8 11h6" />
      {kind === "in" ? <path d="M11 8v6" /> : null}
    </svg>
  );
}

export function TaxonomyPanorama({
  nodes,
  path,
  claimCount,
  relationCount,
  onReset,
  onNavigatePath,
  onSelectTaxonomy,
  onSearchTaxonomy,
}: Props) {
  const scene = useTaxonomyScene(nodes);
  const reducedMotion = useReducedMotion();
  const stageRef = useRef<HTMLDivElement | null>(null);
  const graphHostRef = useRef<HTMLDivElement | null>(null);
  const graphRef = useRef<CosmosGraph | null>(null);
  const initializedCameraGraphRef = useRef<CosmosGraph | null>(null);
  const graphFrameRef = useRef(0);
  const labelRefs = useRef(new Map<string, HTMLButtonElement>());
  const labelLineRefs = useRef(new Map<string, SVGLineElement>());
  const indexButtonRefs = useRef(new Map<string, HTMLButtonElement>());
  const indexLineRef = useRef<SVGLineElement | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const tooltipTitleRef = useRef<HTMLElement | null>(null);
  const tooltipDetailRef = useRef<HTMLElement | null>(null);
  const hoveredRef = useRef<HoveredPoint | null>(null);
  const pathRef = useRef(path);
  const nodesRef = useRef(nodes);
  const onSelectRef = useRef(onSelectTaxonomy);
  const positionOverlayRef = useRef<() => void>(() => undefined);
  const focusCameraRef = useRef<(duration?: number, preserveZoom?: boolean) => void>(() => undefined);
  const [graphReady, setGraphReady] = useState(false);
  const [rendererFailed, setRendererFailed] = useState(false);
  const [activeFrontierId, setActiveFrontierId] = useState<string | null>(null);

  const nodeIndexById = useMemo(() => new Map(nodes.map((node, index) => [node.id, index])), [nodes]);
  const children = useMemo(() => childrenIndex(nodes), [nodes]);
  const focus = path.at(-1) ?? null;
  const frontier = useMemo(() => children.get(focus?.id ?? null) ?? [], [children, focus?.id]);
  const nextLevel = LEVEL_ORDER[path.length] ?? null;
  const denseFrontier = frontier.length > 18;
  const mobileDenseFrontier = frontier.length > 8;
  const focusLayout = useMemo(
    () => scene ? buildTaxonomyFocusLayout(scene, nodes, focus?.id ?? null) : null,
    [focus?.id, nodes, scene],
  );
  const displayPositions = focusLayout?.positions ?? scene?.positions ?? null;

  const positionOverlay = useCallback(() => {
    window.cancelAnimationFrame(graphFrameRef.current);
    graphFrameRef.current = window.requestAnimationFrame(() => {
      const graph = graphRef.current;
      const stage = stageRef.current;
      if (!graph || !stage || !scene || !displayPositions || !graph.isReady) return;
      const width = stage.clientWidth;
      const height = stage.clientHeight;
      const compact = width <= 600;
      const focusNodeIndex = focus ? nodeIndexById.get(focus.id) : undefined;
      const focusPointIndex = focusNodeIndex === undefined ? 0 : scene.nodePointIndices[focusNodeIndex];
      const focusScreen = graph.spaceToScreenPosition([
        displayPositions[focusPointIndex * 2],
        displayPositions[focusPointIndex * 2 + 1],
      ]);
      const placements: Array<{
        label: HTMLButtonElement;
        line: SVGLineElement | undefined;
        labelHeight: number;
        anchorX: number;
        anchorY: number;
        pointX: number;
        pointY: number;
        side: "after" | "before";
        y: number;
      }> = [];
      for (const node of frontier) {
        const nodeIndex = nodeIndexById.get(node.id);
        const label = labelRefs.current.get(node.id);
        if (nodeIndex === undefined || !label || label.offsetParent === null) continue;
        const line = labelLineRefs.current.get(node.id);
        const pointIndex = scene.nodePointIndices[nodeIndex];
        const screen = graph.spaceToScreenPosition([
          displayPositions[pointIndex * 2],
          displayPositions[pointIndex * 2 + 1],
        ]);
        const visible = screen[0] > -120 && screen[0] < width + 120 && screen[1] > -70 && screen[1] < height + 70;
        if (!visible) continue;
        const labelWidth = Math.max(1, label.offsetWidth);
        const labelHeight = Math.max(1, label.offsetHeight);
        const afterFits = screen[0] + 12 + labelWidth <= width - 8;
        const beforeFits = screen[0] - 12 - labelWidth >= 8;
        const preferredSide = screen[0] >= focusScreen[0] ? "after" : "before";
        const side = preferredSide === "after"
          ? afterFits || !beforeFits ? "after" : "before"
          : beforeFits || !afterFits ? "before" : "after";
        const pointX = side === "after"
          ? Math.min(width - 20 - labelWidth, Math.max(-4, screen[0]))
          : Math.max(20 + labelWidth, Math.min(width + 4, screen[0]));
        placements.push({ label, line, labelHeight, anchorX: screen[0], anchorY: screen[1], pointX, pointY: screen[1], side, y: screen[1] });
      }
      for (const side of ["before", "after"] as const) {
        const group = placements.filter((placement) => placement.side === side).sort((left, right) => left.pointY - right.pointY);
        const minY = compact ? 84 : 64;
        const maxY = compact ? height - 8 : height - 64;
        const gap = compact ? 4 : 8;
        group.forEach((placement, index) => {
          const halfHeight = Math.max(17, placement.labelHeight / 2);
          placement.y = Math.min(maxY - halfHeight, Math.max(minY + halfHeight, placement.pointY));
          if (!index) return;
          const previous = group[index - 1];
          const previousHalfHeight = Math.max(17, previous.labelHeight / 2);
          placement.y = Math.max(placement.y, previous.y + previousHalfHeight + halfHeight + gap);
        });
        const last = group.at(-1);
        if (last) {
          const lastHalfHeight = Math.max(17, last.labelHeight / 2);
          last.y = Math.min(last.y, maxY - lastHalfHeight);
        }
        for (let index = group.length - 2; index >= 0; index -= 1) {
          const placement = group[index];
          const next = group[index + 1];
          const halfHeight = Math.max(17, placement.labelHeight / 2);
          const nextHalfHeight = Math.max(17, next.labelHeight / 2);
          placement.y = Math.min(placement.y, next.y - nextHalfHeight - halfHeight - gap);
        }
      }
      const visibleLabels = new Set(placements.map((placement) => placement.label));
      labelRefs.current.forEach((label) => {
        if (!visibleLabels.has(label)) label.dataset.visible = "false";
      });
      const visibleLines = new Set(placements.map((placement) => placement.line).filter(Boolean));
      labelLineRefs.current.forEach((line) => {
        if (!visibleLines.has(line)) line.dataset.visible = "false";
      });
      for (const placement of placements) {
        placement.label.dataset.side = placement.side;
        placement.label.dataset.visible = "true";
        placement.label.style.transform = `translate3d(${placement.pointX}px, ${placement.y}px, 0)`;
        if (placement.line) {
          placement.line.dataset.visible = "true";
          placement.line.setAttribute("x1", String(placement.anchorX));
          placement.line.setAttribute("y1", String(placement.anchorY));
          placement.line.setAttribute("x2", String(placement.side === "after" ? placement.pointX + 12 : placement.pointX - 12));
          placement.line.setAttribute("y2", String(placement.y));
        }
      }
      const indexLine = indexLineRef.current;
      const activeIndexNode = activeFrontierId ? nodesRef.current[nodeIndexById.get(activeFrontierId) ?? -1] : undefined;
      const indexButton = activeFrontierId ? indexButtonRefs.current.get(activeFrontierId) : undefined;
      if (indexLine && activeIndexNode && indexButton) {
        const nodeIndex = nodeIndexById.get(activeIndexNode.id);
        const pointIndex = nodeIndex === undefined ? undefined : scene.nodePointIndices[nodeIndex];
        const stageBounds = stage.getBoundingClientRect();
        const buttonBounds = indexButton.getBoundingClientRect();
        const visible = pointIndex !== undefined && buttonBounds.bottom > stageBounds.top && buttonBounds.top < stageBounds.bottom;
        if (visible && pointIndex !== undefined) {
          const screen = graph.spaceToScreenPosition([
            displayPositions[pointIndex * 2],
            displayPositions[pointIndex * 2 + 1],
          ]);
          indexLine.dataset.visible = "true";
          indexLine.setAttribute("x1", String(screen[0]));
          indexLine.setAttribute("y1", String(screen[1]));
          indexLine.setAttribute("x2", String(buttonBounds.left - stageBounds.left));
          indexLine.setAttribute("y2", String(buttonBounds.top - stageBounds.top + buttonBounds.height / 2));
        } else {
          indexLine.dataset.visible = "false";
        }
      } else if (indexLine) {
        indexLine.dataset.visible = "false";
      }
      const tooltip = tooltipRef.current;
      const activeHover = hoveredRef.current;
      if (tooltip && activeHover) {
        const pointX = displayPositions[activeHover.index * 2];
        const pointY = displayPositions[activeHover.index * 2 + 1];
        const screen = graph.spaceToScreenPosition([pointX, pointY]);
        tooltip.dataset.side = screen[0] > width * 0.65 ? "left" : "right";
        tooltip.style.transform = `translate3d(${Math.min(width - 18, Math.max(18, screen[0]))}px, ${Math.min(height - 18, Math.max(18, screen[1]))}px, 0)`;
      }
    });
  }, [activeFrontierId, displayPositions, focus, frontier, nodeIndexById, scene]);

  const focusCamera = useCallback((duration = reducedMotion ? 0 : 260, preserveZoom = false) => {
    const graph = graphRef.current;
    if (!graph || !scene || !graph.isReady) return;
    const scale = preserveZoom ? graph.getZoomLevel() : undefined;
    if (!focus) {
      const compact = (stageRef.current?.clientWidth ?? Number.POSITIVE_INFINITY) <= 600;
      // Give peripheral nodes room with a wider default overview.
      const overviewScale = (compact
        ? scene.visualProfile.mobileOverviewScale / scene.visualProfile.overviewScale
        : 1) / 0.9;
      const bounds = Float32Array.from(scene.overviewCameraBounds, (value) => value * overviewScale);
      graph.setZoomTransformByPointPositions(bounds, duration, scale, 0.03, false);
      return;
    }
    if (!focusLayout?.cameraBounds) return;
    graph.setZoomTransformByPointPositions(
      focusLayout.cameraBounds,
      duration,
      scale,
      frontier.length ? 0.18 : scene.visualProfile.focusPadding,
      false,
    );
  }, [focus, focusLayout, frontier.length, reducedMotion, scene]);

  useEffect(() => {
    pathRef.current = path;
    nodesRef.current = nodes;
    onSelectRef.current = onSelectTaxonomy;
  }, [nodes, onSelectTaxonomy, path]);

  useEffect(() => {
    positionOverlayRef.current = positionOverlay;
    focusCameraRef.current = focusCamera;
  }, [focusCamera, positionOverlay]);

  useEffect(() => {
    const host = graphHostRef.current;
    if (!host || !scene) return;
    let disposed = false;
    let graph: CosmosGraph | null = null;
    let canvas: HTMLCanvasElement | null = null;
    const motionDuration = reducedMotion ? 0 : 220;
    const handleContextLost = (event: Event) => {
      event.preventDefault();
      setRendererFailed(true);
    };
    setGraphReady(false);
    setRendererFailed(false);

    void loadGraphModule().then(({ Graph }) => {
      if (disposed) return;
      graph = new Graph(host, {
        attribution: "",
        backgroundColor: [0.965, 0.976, 0.992, 0],
        enableSimulation: false,
        enableSimulationDuringZoom: false,
        enableDrag: false,
        enableZoom: true,
        fitViewOnInit: false,
        pointDefaultColor: "#667085",
        pointDefaultSize: 2,
        pointOcclusionCulling: true,
        pointOpacity: 1,
        pixelRatio: Math.min(2, Math.max(1, window.devicePixelRatio || 1)),
        scalePointsOnZoom: false,
        linkDefaultColor: "#8c98ab",
        linkDefaultWidth: 1,
        linkBlending: true,
        renderLinks: true,
        rescalePositions: false,
        spaceSize: 2048,
        transitionDuration: motionDuration,
        hoveredPointCursor: "crosshair",
        onPointClick: (pointIndex) => {
          if (!scene || scene.pointKinds[pointIndex] < TAXONOMY_POINT_KIND.domain || scene.pointKinds[pointIndex] > TAXONOMY_POINT_KIND.topic) return;
          const ownerIndex = scene.pointOwners[pointIndex];
          if (ownerIndex === NO_TAXONOMY_OWNER) return;
          const node = nodesRef.current[ownerIndex];
          if (!node) return;
          const currentPath = pathRef.current;
          const selectedIndex = currentPath.findIndex((candidate) => candidate.id === node.id);
          const currentFocus = currentPath.at(-1) ?? null;
          const isFrontier = node.parentId === (currentFocus?.id ?? null);
          if (selectedIndex < 0 && !isFrontier) return;
          setActiveFrontierId(null);
          onSelectRef.current(node, selectedIndex >= 0 ? selectedIndex : LEVEL_ORDER.indexOf(node.level));
        },
        onPointMouseOver: (pointIndex) => {
          const ownerIndex = scene.pointOwners[pointIndex];
          if (ownerIndex === NO_TAXONOMY_OWNER) return;
          const node = nodesRef.current[ownerIndex];
          if (!node) return;
          const isClaim = scene.pointKinds[pointIndex] === TAXONOMY_POINT_KIND.claim;
          hoveredRef.current = {
            index: pointIndex,
            title: node.displayName,
            detail: isClaim ? `Claim 分布点 · ${distributionClaimCount(node)} Claims` : `${LEVEL_LABELS[node.level]} · ${node.paperCount} 篇论文 · ${node.claimCount} Claims`,
          };
          if (tooltipTitleRef.current) tooltipTitleRef.current.textContent = node.displayName;
          if (tooltipDetailRef.current) tooltipDetailRef.current.textContent = hoveredRef.current.detail;
          if (tooltipRef.current) tooltipRef.current.dataset.visible = "true";
          positionOverlayRef.current();
        },
        onPointMouseOut: () => {
          hoveredRef.current = null;
          if (tooltipRef.current) tooltipRef.current.dataset.visible = "false";
        },
        onZoom: () => positionOverlayRef.current(),
        onTransition: () => positionOverlayRef.current(),
        onTransitionEnd: () => positionOverlayRef.current(),
      });
      graphRef.current = graph;
      graph.setPointPositions(scene.positions, true);
      graph.setPointColors(scene.pointColors);
      graph.setPointSizes(scene.pointSizes);
      graph.setLinks(scene.links);
      graph.setLinkColors(scene.linkColors);
      graph.setLinkWidths(scene.linkWidths);
      graph.render(0, 0);
      void graph.ready.then(() => {
        if (disposed || !graph) return;
        canvas = host.querySelector("canvas");
        canvas?.setAttribute("aria-hidden", "true");
        canvas?.setAttribute("tabindex", "-1");
        canvas?.addEventListener("webglcontextlost", handleContextLost);
        setGraphReady(true);

      }).catch(() => {
        if (!disposed) setRendererFailed(true);
      });
    }).catch(() => {
      if (!disposed) setRendererFailed(true);
    });

    return () => {
      disposed = true;
      window.cancelAnimationFrame(graphFrameRef.current);
      canvas?.removeEventListener("webglcontextlost", handleContextLost);
      if (initializedCameraGraphRef.current === graph) initializedCameraGraphRef.current = null;
      if (graphRef.current === graph) graphRef.current = null;
      graph?.destroy();
    };
  }, [reducedMotion, scene]);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage || !scene || !graphReady) return;
    let frame = 0;
    let width = stage.clientWidth;
    let height = stage.clientHeight;
    const observer = new ResizeObserver(() => {
      const nextWidth = stage.clientWidth;
      const nextHeight = stage.clientHeight;
      if (nextWidth === width && nextHeight === height) return;
      width = nextWidth;
      height = nextHeight;
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        focusCameraRef.current(0);
        positionOverlayRef.current();
      });
    });
    observer.observe(stage);
    return () => {
      observer.disconnect();
      window.cancelAnimationFrame(frame);
    };
  }, [graphReady, scene]);

  useEffect(() => {
    if (!scene || !graphReady) return;
    const graph = graphRef.current;
    if (!graph) return;
    const selectedIds = new Set(path.map((node) => node.id));
    const frontierIds = new Set(frontier.map((node) => node.id));
    const descendants = descendantsOf(focus, children, nodeIndexById);
    const pointColors = new Float32Array(scene.pointColors);
    const pointSizes = new Float32Array(scene.pointSizes);
    const linkColors = new Float32Array(scene.linkColors);
    const linkWidths = new Float32Array(scene.linkWidths);

    nodes.forEach((node, nodeIndex) => {
      const pointIndex = scene.nodePointIndices[nodeIndex];
      if (selectedIds.has(node.id)) {
        writeColor(pointColors, pointIndex, GRAPHITE);
        pointSizes[pointIndex] += 3;
      } else if (activeFrontierId === node.id) {
        writeColor(pointColors, pointIndex, ACCENT);
        pointSizes[pointIndex] += 4.5;
      } else if (frontierIds.has(node.id)) {
        writeColor(pointColors, pointIndex, ACCENT);
        pointSizes[pointIndex] += 2.2;
      } else {
        writeColor(pointColors, pointIndex, CONTEXT);
      }
      if (nodeIndex < scene.linkKinds.length && scene.linkKinds[nodeIndex] === 1) {
        if (selectedIds.has(node.id) || frontierIds.has(node.id)) {
          writeColor(linkColors, nodeIndex, LINK_ACTIVE);
          linkWidths[nodeIndex] = selectedIds.has(node.id) ? 1.65 : 1.2;
        } else {
          writeColor(linkColors, nodeIndex, LINK_CONTEXT);
        }
      }
    });

    for (let pointIndex = 1 + nodes.length; pointIndex < scene.pointKinds.length - RING_POINT_COUNT; pointIndex += 1) {
      if (scene.pointKinds[pointIndex] !== TAXONOMY_POINT_KIND.claim) continue;
      const ownerIndex = scene.pointOwners[pointIndex];
      writeColor(pointColors, pointIndex, descendants === null ? CLAIM : descendants.has(ownerIndex) ? CLAIM_FOCUSED : CLAIM_DIMMED);
    }

    graph.setPointPositions(displayPositions ?? scene.positions, true);
    graph.setPointColors(pointColors);
    graph.setPointSizes(pointSizes);
    graph.setLinkColors(linkColors);
    graph.setLinkWidths(linkWidths);
    graph.render(undefined, reducedMotion ? 0 : 180);
    // The first styling pass must fit the overview, not preserve the renderer’s
    // default zoom. Schedule camera initialization here only, so a competing
    // transition from graph.ready cannot overwrite it.
    const firstCameraFrame = initializedCameraGraphRef.current !== graph;
    initializedCameraGraphRef.current = graph;
    focusCamera(firstCameraFrame ? 0 : undefined, !firstCameraFrame);
    positionOverlay();
  }, [activeFrontierId, children, displayPositions, focus, focusCamera, frontier, graphReady, nodeIndexById, nodes, path, positionOverlay, reducedMotion, scene]);

  const zoomBy = (factor: number) => {
    const graph = graphRef.current;
    if (!graph?.isReady) return;
    graph.setZoomLevel(graph.getZoomLevel() * factor, reducedMotion ? 0 : 160, false);
  };

  const setLabelRef = (nodeId: string) => (element: HTMLButtonElement | null) => {
    if (element) labelRefs.current.set(nodeId, element);
    else labelRefs.current.delete(nodeId);
  };

  const setLabelLineRef = (nodeId: string) => (element: SVGLineElement | null) => {
    if (element) labelLineRefs.current.set(nodeId, element);
    else labelLineRefs.current.delete(nodeId);
  };

  const setIndexButtonRef = (nodeId: string) => (element: HTMLButtonElement | null) => {
    if (element) indexButtonRefs.current.set(nodeId, element);
    else indexButtonRefs.current.delete(nodeId);
  };

  const handleLabelKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>, index: number) => {
    let targetIndex: number | null = null;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") targetIndex = (index + 1) % frontier.length;
    if (event.key === "ArrowLeft" || event.key === "ArrowUp") targetIndex = (index - 1 + frontier.length) % frontier.length;
    if (event.key === "Home") targetIndex = 0;
    if (event.key === "End") targetIndex = frontier.length - 1;
    if (targetIndex === null) return;
    event.preventDefault();
    labelRefs.current.get(frontier[targetIndex].id)?.focus();
  };

  return (
    <section className="taxonomy-panorama" aria-label="OpenAlex 分类图谱">
      <div
        className="taxonomy-cosmos-stage"
        ref={stageRef}
        data-frontier-density={denseFrontier ? "dense" : "standard"}
        data-mobile-frontier-density={mobileDenseFrontier ? "dense" : "standard"}
        role="region"
        aria-label={`OpenAlex 四级分类图谱，${nodes.length} 个分类节点，${scene?.claimPointCount ?? 0} 个 Claim 分布点`}
      >
        <div className="taxonomy-cosmos-grid" aria-hidden="true" />
        <div className="taxonomy-cosmos-renderer" ref={graphHostRef} aria-hidden="true" />
        <div className="taxonomy-cosmos-topbar">
          {path.length ? (
            <nav className="taxonomy-cosmos-path" aria-label="当前分类路径">
              <button
                type="button"
                aria-label="返回上一级分类"
                onClick={() => {
                  setActiveFrontierId(null);
                  if (path.length === 1) onReset();
                  else onNavigatePath(path.length - 2);
                }}
              >
                <UiIcon name="arrow-left" size={16} />
              </button>
              {path.map((node, index) => (
                <button key={node.id} type="button" aria-current={index === path.length - 1 ? "page" : undefined} onClick={() => { setActiveFrontierId(null); onNavigatePath(index); }}>
                  {node.displayName}
                </button>
              ))}
            </nav>
          ) : null}
          <div
            className="taxonomy-cosmos-status"
            aria-label={`${claimCount} 条 Claim，${relationCount} 条 Claim 关系，${nextLevel ? `选择 ${LEVEL_LABELS[nextLevel]}` : "已选 Topic"}`}
            aria-live="polite"
          >
            <span><strong>{claimCount}</strong> Claims</span>
            <span><strong>{relationCount}</strong> Claim 关系</span>
            <strong>{nextLevel ? `选择 ${LEVEL_LABELS[nextLevel]}` : "已选 Topic"}</strong>
          </div>
        </div>
        {!scene || (!graphReady && !rendererFailed) ? (
          <div className="taxonomy-cosmos-loading" role="status"><span /><span /><span /></div>
        ) : null}

        <svg className="taxonomy-cosmos-label-links" aria-hidden="true">
          {frontier.map((node) => <line key={node.id} ref={setLabelLineRef(node.id)} data-visible="false" />)}
          <line className="taxonomy-cosmos-index-line" ref={indexLineRef} data-visible="false" />
        </svg>
        <div className="taxonomy-cosmos-labels" data-density={frontier.length > 18 ? "dense" : "standard"} hidden={!graphReady} aria-label={nextLevel ? `可选 ${LEVEL_LABELS[nextLevel]} 节点` : "当前 Topic"}>
          {frontier.map((node, index) => (
            <button
              className="taxonomy-cosmos-label"
              key={node.id}
              ref={setLabelRef(node.id)}
              type="button"
              data-visible="false"
              title={`${node.displayName} · ${node.paperCount} 篇论文 · ${node.claimCount} Claims`}
              aria-label={`${node.displayName}，${node.paperCount} 篇论文，${node.claimCount} 条 Claim`}
              onClick={() => { setActiveFrontierId(null); onSelectTaxonomy(node, LEVEL_ORDER.indexOf(node.level)); }}
              onKeyDown={(event) => handleLabelKeyDown(event, index)}
            >
              <strong>{node.displayName}</strong>
              <span>{node.paperCount} 篇论文</span>
            </button>
          ))}
        </div>

        {frontier.length ? (
          <div className="taxonomy-cosmos-index" aria-label={`可选 ${nextLevel ? LEVEL_LABELS[nextLevel] : "Topic"} 节点`} onScroll={positionOverlay}>
            {frontier.map((node) => (
              <button
                key={node.id}
                ref={setIndexButtonRef(node.id)}
                type="button"
                onClick={() => { setActiveFrontierId(null); onSelectTaxonomy(node, LEVEL_ORDER.indexOf(node.level)); }}
                onFocus={() => setActiveFrontierId(node.id)}
                onBlur={() => setActiveFrontierId(null)}
                onMouseEnter={() => setActiveFrontierId(node.id)}
                onMouseLeave={() => setActiveFrontierId(null)}
              >
                <strong>{node.displayName}</strong>
                <span>{node.paperCount} 篇论文</span>
              </button>
            ))}
          </div>
        ) : null}

        <div className="taxonomy-cosmos-tooltip" ref={tooltipRef} data-visible="false" aria-hidden="true">
          <strong ref={tooltipTitleRef} /><span ref={tooltipDetailRef} />
        </div>

        <div className="taxonomy-cosmos-controls" aria-label="图谱视图控制">
          <button type="button" disabled={!graphReady} aria-label="放大图谱" onClick={() => zoomBy(1.3)}><GraphControlIcon kind="in" /></button>
          <button type="button" disabled={!graphReady} aria-label="缩小图谱" onClick={() => zoomBy(0.77)}><GraphControlIcon kind="out" /></button>
          <button type="button" disabled={!graphReady} aria-label="适应当前层级" onClick={() => focusCamera()}><GraphControlIcon kind="fit" /></button>
        </div>

        <div className="taxonomy-cosmos-legend" aria-hidden="true">
          <span><i data-kind="taxonomy" />分类节点</span>
          <span><i data-kind="claim" />Claim{scene && scene.claimUnit > 1 ? `（每点约 ${scene.claimUnit} 条）` : ""}</span>
          <span>拖动平移 · 滚轮缩放</span>
        </div>

      </div>

      {focus ? (
        <div className="taxonomy-spider-focus-actions">
          <div><strong>{focus.displayName}</strong><span>{LEVEL_LABELS[focus.level]} · {focus.paperCount} 篇论文 · {focus.claimCount} 条 Claim</span></div>
          <button className="button secondary" type="button" onClick={() => onSearchTaxonomy(focus)}>在此分类检索</button>
        </div>
      ) : null}
    </section>
  );
}
