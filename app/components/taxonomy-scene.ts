export type TaxonomySceneLevel = "domain" | "field" | "subfield" | "topic";

export type TaxonomySceneNode = {
  level: TaxonomySceneLevel;
  id: string;
  displayName: string;
  parentId: string | null;
  paperCount: number;
  claimCount: number;
  primaryClaimCount?: number;
};

export type TaxonomyVisualProfile = {
  claimPointSize: number;
  focusPadding: number;
  linkWidthScale: number;
  mobileOverviewScale: number;
  overviewScale: number;
  taxonomyPointScale: number;
};

export type TaxonomyScene = {
  overviewCameraBounds: Float32Array;
  positions: Float32Array;
  pointColors: Float32Array;
  pointSizes: Float32Array;
  pointKinds: Uint8Array;
  pointOwners: Uint32Array;
  nodePointIndices: Uint32Array;
  nodeBounds: Float32Array;
  links: Float32Array;
  linkColors: Float32Array;
  linkWidths: Float32Array;
  linkKinds: Uint8Array;
  claimPointCount: number;
  claimUnit: number;
  visualProfile: TaxonomyVisualProfile;
};

export type TaxonomyFocusLayout = {
  cameraBounds: Float32Array | null;
  positions: Float32Array;
  sourceSpan: number;
  targetSpan: number;
};

export const TAXONOMY_POINT_KIND = {
  root: 0,
  domain: 1,
  field: 2,
  subfield: 3,
  topic: 4,
  claim: 5,
  ring: 6,
} as const;

export const NO_TAXONOMY_OWNER = 0xffffffff;
export const TAXONOMY_SCENE_MAX_CLAIM_POINTS = 160_000;

const LEVEL_RADIUS = [28, 128 * (2 / 3), 204 * (4 / 5), 296];
const CLAIM_INNER_RADIUS = 328;
const CLAIM_OUTER_RADIUS = 570;
const RING_SEGMENTS = 112;
const GOLDEN_FRACTION = 0.6180339887498949;

const POINT_COLOR = {
  taxonomy: [0.36, 0.43, 0.54, 0.88],
  claim: [0.322, 0.384, 0.478, 1],
  transparent: [0, 0, 0, 0],
} as const;

const LINK_COLOR = {
  ring: [0.58, 0.65, 0.75, 0.25],
  hierarchy: [0.43, 0.51, 0.63, 0.42],
} as const;

type Sector = { start: number; end: number };
type Bounds = { minX: number; minY: number; maxX: number; maxY: number };

function safeCount(value: number) {
  return Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
}

function interpolate(from: number, to: number, progress: number) {
  return from + (to - from) * progress;
}

function normalizeAngle(angle: number) {
  return Math.atan2(Math.sin(angle), Math.cos(angle));
}

export function taxonomyVisualProfile(claimCount: number): TaxonomyVisualProfile {
  const renderedClaimCount = Math.min(TAXONOMY_SCENE_MAX_CLAIM_POINTS, safeCount(claimCount));
  const crowding = Math.min(1, Math.max(0,
    Math.log10(Math.max(renderedClaimCount, 500) / 500)
      / Math.log10(TAXONOMY_SCENE_MAX_CLAIM_POINTS / 500),
  ));
  return {
    claimPointSize: interpolate(4.4, 1.55, crowding ** 0.72),
    focusPadding: interpolate(0.06, 0.1, crowding),
    linkWidthScale: interpolate(1.2, 1, crowding),
    mobileOverviewScale: 1 / (1 + 0.06 * (1 - crowding)),
    overviewScale: 1 / (1 + 0.12 * (1 - crowding)),
    taxonomyPointScale: interpolate(1.5, 1, crowding),
  };
}

function hashString(value: string) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

export function distributionClaimCount(node: TaxonomySceneNode) {
  return safeCount(node.primaryClaimCount ?? node.claimCount);
}

function hierarchyWeight(node: TaxonomySceneNode) {
  return 1 + Math.log2(distributionClaimCount(node) + 2);
}

function writeColor(target: Float32Array, index: number, color: readonly number[]) {
  const offset = index * 4;
  target[offset] = color[0];
  target[offset + 1] = color[1];
  target[offset + 2] = color[2];
  target[offset + 3] = color[3];
}

function pointBounds(x: number, y: number): Bounds {
  return { minX: x, minY: y, maxX: x, maxY: y };
}

function includePoint(bounds: Bounds, x: number, y: number) {
  bounds.minX = Math.min(bounds.minX, x);
  bounds.minY = Math.min(bounds.minY, y);
  bounds.maxX = Math.max(bounds.maxX, x);
  bounds.maxY = Math.max(bounds.maxY, y);
}

function includeBounds(bounds: Bounds, child: Bounds) {
  includePoint(bounds, child.minX, child.minY);
  includePoint(bounds, child.maxX, child.maxY);
}

export function taxonomySceneKey(nodes: TaxonomySceneNode[]) {
  let hash = 2166136261;
  for (const node of nodes) {
    const signature = `${node.level}\u0000${node.id}\u0000${node.parentId ?? ""}\u0000${node.displayName}\u0000${safeCount(node.claimCount)}\u0000${distributionClaimCount(node)}`;
    hash ^= hashString(signature);
    hash = Math.imul(hash, 16777619);
  }
  return `${nodes.length}:${hash >>> 0}`;
}

export function buildTaxonomyScene(nodes: TaxonomySceneNode[]): TaxonomyScene {
  const nodeIndexById = new Map(nodes.map((node, index) => [node.id, index]));
  const children = new Map<number, number[]>();
  const roots: number[] = [];

  nodes.forEach((node, index) => {
    const parentIndex = node.parentId ? nodeIndexById.get(node.parentId) : undefined;
    if (parentIndex === undefined) {
      roots.push(index);
      return;
    }
    const siblings = children.get(parentIndex) ?? [];
    siblings.push(index);
    children.set(parentIndex, siblings);
  });

  const sortIndices = (indices: number[]) => indices.sort((left, right) => {
    const claimDifference = safeCount(nodes[right].claimCount) - safeCount(nodes[left].claimCount);
    return claimDifference || nodes[left].displayName.localeCompare(nodes[right].displayName, "en");
  });
  sortIndices(roots);
  children.forEach(sortIndices);

  const sectors = new Array<Sector>(nodes.length);
  const nodePositions = new Float32Array(nodes.length * 2);
  const depths = new Uint8Array(nodes.length);

  const placeChildren = (indices: number[], start: number, end: number, depth: number) => {
    if (!indices.length) return;
    const weights = indices.map((index) => depth === 0 ? 1 : hierarchyWeight(nodes[index]));
    const totalWeight = weights.reduce((sum, weight) => sum + weight, 0);
    const span = end - start;
    const gap = Math.min(0.022, span / Math.max(12, indices.length * 5));
    let cursor = start;
    indices.forEach((nodeIndex, orderIndex) => {
      const rawSpan = span * (weights[orderIndex] / totalWeight);
      const childStart = cursor + gap / 2;
      const childEnd = cursor + rawSpan - gap / 2;
      const usableEnd = Math.max(childStart + 0.0001, childEnd);
      const angle = (childStart + usableEnd) / 2;
      const nodeDepth = Math.min(depth, LEVEL_RADIUS.length - 1);
      const radius = LEVEL_RADIUS[nodeDepth];
      sectors[nodeIndex] = { start: childStart, end: usableEnd };
      depths[nodeIndex] = nodeDepth;
      nodePositions[nodeIndex * 2] = Math.cos(angle) * radius;
      nodePositions[nodeIndex * 2 + 1] = Math.sin(angle) * radius;
      placeChildren(children.get(nodeIndex) ?? [], childStart, usableEnd, depth + 1);
      cursor += rawSpan;
    });
  };

  placeChildren(roots, -Math.PI / 2, Math.PI * 1.5, 0);

  const leaves = nodes
    .map((node, index) => ({ node, index }))
    .filter(({ index }) => !(children.get(index)?.length));
  const totalLeafClaims = leaves.reduce((sum, { node }) => sum + distributionClaimCount(node), 0);
  const visualProfile = taxonomyVisualProfile(totalLeafClaims);
  const claimUnit = Math.max(1, Math.ceil(totalLeafClaims / TAXONOMY_SCENE_MAX_CLAIM_POINTS));
  const claimCounts = new Uint32Array(nodes.length);
  let claimPointCount = 0;
  if (totalLeafClaims <= TAXONOMY_SCENE_MAX_CLAIM_POINTS) {
    leaves.forEach(({ node, index }) => {
      const particles = distributionClaimCount(node);
      claimCounts[index] = particles;
      claimPointCount += particles;
    });
  } else {
    const scale = TAXONOMY_SCENE_MAX_CLAIM_POINTS / totalLeafClaims;
    const allocations = leaves.map(({ node, index }) => {
      const exact = distributionClaimCount(node) * scale;
      const particles = Math.floor(exact);
      claimCounts[index] = particles;
      claimPointCount += particles;
      return { index, remainder: exact - particles, id: node.id };
    }).sort((left, right) => right.remainder - left.remainder || left.id.localeCompare(right.id, "en"));
    for (let cursor = 0; claimPointCount < TAXONOMY_SCENE_MAX_CLAIM_POINTS; cursor += 1) {
      claimCounts[allocations[cursor].index] += 1;
      claimPointCount += 1;
    }
  }

  const ringPointCount = LEVEL_RADIUS.length * RING_SEGMENTS;
  const pointCount = 1 + nodes.length + claimPointCount + ringPointCount;
  const positions = new Float32Array(pointCount * 2);
  const pointColors = new Float32Array(pointCount * 4);
  const pointSizes = new Float32Array(pointCount);
  const pointKinds = new Uint8Array(pointCount);
  const pointOwners = new Uint32Array(pointCount);
  pointOwners.fill(NO_TAXONOMY_OWNER);
  const nodePointIndices = new Uint32Array(nodes.length);
  const bounds = nodes.map((_, index) => pointBounds(nodePositions[index * 2], nodePositions[index * 2 + 1]));

  positions[0] = 0;
  positions[1] = 0;
  pointKinds[0] = TAXONOMY_POINT_KIND.root;
  pointSizes[0] = 0.01;
  writeColor(pointColors, 0, POINT_COLOR.transparent);

  nodes.forEach((node, nodeIndex) => {
    const pointIndex = 1 + nodeIndex;
    nodePointIndices[nodeIndex] = pointIndex;
    positions[pointIndex * 2] = nodePositions[nodeIndex * 2];
    positions[pointIndex * 2 + 1] = nodePositions[nodeIndex * 2 + 1];
    pointKinds[pointIndex] = TAXONOMY_POINT_KIND[node.level];
    pointOwners[pointIndex] = nodeIndex;
    pointSizes[pointIndex] = [7.8, 6.8, 5.8, 4.8][depths[nodeIndex]] * visualProfile.taxonomyPointScale;
    writeColor(pointColors, pointIndex, POINT_COLOR.taxonomy);
  });

  let nextPoint = 1 + nodes.length;
  leaves.forEach(({ node, index: nodeIndex }) => {
    const count = claimCounts[nodeIndex];
    if (!count) return;
    const sector = sectors[nodeIndex] ?? { start: -Math.PI, end: Math.PI };
    const sectorWidth = Math.max(0.001, sector.end - sector.start);
    const margin = Math.min(sectorWidth * 0.16, 0.018);
    const usableStart = sector.start + margin;
    const usableWidth = Math.max(0.0004, sectorWidth - margin * 2);
    const seed = hashString(node.id) / 0xffffffff;
    const nodeRadius = LEVEL_RADIUS[depths[nodeIndex]];
    const innerRadius = Math.max(nodeRadius + 18, CLAIM_INNER_RADIUS);
    for (let order = 0; order < count; order += 1) {
      const pointIndex = nextPoint;
      nextPoint += 1;
      const radialProgress = Math.sqrt((order + 0.62) / Math.max(1, count));
      const radius = innerRadius + radialProgress * (CLAIM_OUTER_RADIUS - innerRadius);
      const angularProgress = (seed + order * GOLDEN_FRACTION) % 1;
      const angle = usableStart + angularProgress * usableWidth;
      const x = Math.cos(angle) * radius;
      const y = Math.sin(angle) * radius;
      positions[pointIndex * 2] = x;
      positions[pointIndex * 2 + 1] = y;
      pointKinds[pointIndex] = TAXONOMY_POINT_KIND.claim;
      pointOwners[pointIndex] = nodeIndex;
      pointSizes[pointIndex] = visualProfile.claimPointSize;
      writeColor(pointColors, pointIndex, POINT_COLOR.claim);
      includePoint(bounds[nodeIndex], x, y);
    }
  });

  const hierarchyLinkCount = nodes.length;
  const ringLinkCount = ringPointCount;
  const linkCount = hierarchyLinkCount + ringLinkCount;
  const links = new Float32Array(linkCount * 2);
  const linkColors = new Float32Array(linkCount * 4);
  const linkWidths = new Float32Array(linkCount);
  const linkKinds = new Uint8Array(linkCount);

  nodes.forEach((node, nodeIndex) => {
    const parentNodeIndex = node.parentId ? nodeIndexById.get(node.parentId) : undefined;
    links[nodeIndex * 2] = parentNodeIndex === undefined ? 0 : nodePointIndices[parentNodeIndex];
    links[nodeIndex * 2 + 1] = nodePointIndices[nodeIndex];
    linkKinds[nodeIndex] = 1;
    linkWidths[nodeIndex] = 0.85 * visualProfile.linkWidthScale;
    writeColor(linkColors, nodeIndex, LINK_COLOR.hierarchy);
  });

  LEVEL_RADIUS.forEach((radius, ringIndex) => {
    const ringStartPoint = nextPoint;
    for (let segment = 0; segment < RING_SEGMENTS; segment += 1) {
      const pointIndex = nextPoint;
      nextPoint += 1;
      const angle = -Math.PI / 2 + (segment / RING_SEGMENTS) * Math.PI * 2;
      positions[pointIndex * 2] = Math.cos(angle) * radius;
      positions[pointIndex * 2 + 1] = Math.sin(angle) * radius;
      pointKinds[pointIndex] = TAXONOMY_POINT_KIND.ring;
      pointSizes[pointIndex] = 0.01;
      writeColor(pointColors, pointIndex, POINT_COLOR.transparent);

      const linkIndex = hierarchyLinkCount + ringIndex * RING_SEGMENTS + segment;
      links[linkIndex * 2] = pointIndex;
      links[linkIndex * 2 + 1] = ringStartPoint + ((segment + 1) % RING_SEGMENTS);
      linkKinds[linkIndex] = 0;
      linkWidths[linkIndex] = 0.55 * visualProfile.linkWidthScale;
      writeColor(linkColors, linkIndex, LINK_COLOR.ring);
    }
  });

  const indicesByDepth = nodes.map((_, index) => index).sort((left, right) => depths[right] - depths[left]);
  indicesByDepth.forEach((nodeIndex) => {
    const parentId = nodes[nodeIndex].parentId;
    const parentIndex = parentId ? nodeIndexById.get(parentId) : undefined;
    if (parentIndex !== undefined) includeBounds(bounds[parentIndex], bounds[nodeIndex]);
  });

  const nodeBounds = new Float32Array(nodes.length * 4);
  bounds.forEach((nodeBoundsValue, index) => {
    const padding = 18;
    nodeBounds[index * 4] = nodeBoundsValue.minX - padding;
    nodeBounds[index * 4 + 1] = nodeBoundsValue.minY - padding;
    nodeBounds[index * 4 + 2] = nodeBoundsValue.maxX + padding;
    nodeBounds[index * 4 + 3] = nodeBoundsValue.maxY + padding;
  });

  const overviewRadius = (claimPointCount ? CLAIM_OUTER_RADIUS : LEVEL_RADIUS.at(-1)!) * visualProfile.overviewScale;
  const overviewCameraBounds = new Float32Array([
    -overviewRadius,
    -overviewRadius,
    overviewRadius,
    overviewRadius,
  ]);

  return {
    overviewCameraBounds,
    positions,
    pointColors,
    pointSizes,
    pointKinds,
    pointOwners,
    nodePointIndices,
    nodeBounds,
    links,
    linkColors,
    linkWidths,
    linkKinds,
    claimPointCount,
    claimUnit,
    visualProfile,
  };
}

export function buildTaxonomyFocusLayout(scene: TaxonomyScene, nodes: TaxonomySceneNode[], focusId: string | null): TaxonomyFocusLayout {
  if (!focusId) return { cameraBounds: null, positions: scene.positions, sourceSpan: 0, targetSpan: 0 };
  const nodeIndexById = new Map(nodes.map((node, index) => [node.id, index]));
  const focusIndex = nodeIndexById.get(focusId);
  if (focusIndex === undefined) return { cameraBounds: null, positions: scene.positions, sourceSpan: 0, targetSpan: 0 };

  const frontierIndices = nodes
    .map((node, index) => node.parentId === focusId ? index : -1)
    .filter((index) => index >= 0);
  if (!frontierIndices.length) {
    const offset = focusIndex * 4;
    return {
      cameraBounds: scene.nodeBounds.slice(offset, offset + 4),
      positions: scene.positions,
      sourceSpan: 0,
      targetSpan: 0,
    };
  }

  const childIndices = new Map<number, number[]>();
  nodes.forEach((node, index) => {
    const parentIndex = node.parentId ? nodeIndexById.get(node.parentId) : undefined;
    if (parentIndex === undefined) return;
    const siblings = childIndices.get(parentIndex) ?? [];
    siblings.push(index);
    childIndices.set(parentIndex, siblings);
  });
  const focusPointIndex = scene.nodePointIndices[focusIndex];
  const focusAngle = Math.atan2(scene.positions[focusPointIndex * 2 + 1], scene.positions[focusPointIndex * 2]);
  const frontier = frontierIndices.map((nodeIndex) => {
    const pointIndex = scene.nodePointIndices[nodeIndex];
    return {
      nodeIndex,
      sourceOffset: normalizeAngle(Math.atan2(scene.positions[pointIndex * 2 + 1], scene.positions[pointIndex * 2]) - focusAngle),
    };
  }).sort((left, right) => left.sourceOffset - right.sourceOffset);
  const minimumAngle = frontier[0].sourceOffset;
  const maximumAngle = frontier.at(-1)!.sourceOffset;
  const sourceSpan = Math.max(0, maximumAngle - minimumAngle);
  const focusLevel = nodes[focusIndex].level;
  const spanProfile = focusLevel === "domain"
    ? { minimum: 144, perGap: 24, maximum: 216 }
    : focusLevel === "field"
      ? { minimum: 144, perGap: 28, maximum: 240 }
      : { minimum: 180, perGap: 16, maximum: 300 };
  const desiredSpan = frontier.length === 1
    ? 0
    : Math.min(spanProfile.maximum, Math.max(spanProfile.minimum, (frontier.length - 1) * spanProfile.perGap)) * (Math.PI / 180);
  const targetSpan = Math.max(sourceSpan, desiredSpan);
  const positions = new Float32Array(scene.positions);
  const branchByOwner = new Map<number, number>();
  const branchRotations = new Map<number, number>();

  frontier.forEach(({ nodeIndex, sourceOffset }, orderIndex) => {
    const targetOffset = frontier.length === 1 ? 0 : -targetSpan / 2 + (orderIndex / (frontier.length - 1)) * targetSpan;
    branchRotations.set(nodeIndex, normalizeAngle(targetOffset - sourceOffset));
    const queue = [nodeIndex];
    while (queue.length) {
      const branchNodeIndex = queue.pop()!;
      if (branchByOwner.has(branchNodeIndex)) continue;
      branchByOwner.set(branchNodeIndex, nodeIndex);
      queue.push(...(childIndices.get(branchNodeIndex) ?? []));
    }
  });

  for (let pointIndex = 1; pointIndex < scene.pointKinds.length; pointIndex += 1) {
    const ownerIndex = scene.pointOwners[pointIndex];
    if (ownerIndex === NO_TAXONOMY_OWNER) continue;
    const branchIndex = branchByOwner.get(ownerIndex);
    if (branchIndex === undefined) continue;
    const rotation = branchRotations.get(branchIndex) ?? 0;
    if (Math.abs(rotation) < 0.0001) continue;
    const x = scene.positions[pointIndex * 2];
    const y = scene.positions[pointIndex * 2 + 1];
    const radius = Math.hypot(x, y);
    const angle = Math.atan2(y, x) + rotation;
    positions[pointIndex * 2] = Math.cos(angle) * radius;
    positions[pointIndex * 2 + 1] = Math.sin(angle) * radius;
  }

  const focusX = positions[focusPointIndex * 2];
  const focusY = positions[focusPointIndex * 2 + 1];
  const bounds = pointBounds(focusX, focusY);
  frontierIndices.forEach((nodeIndex) => {
    const pointIndex = scene.nodePointIndices[nodeIndex];
    includePoint(bounds, positions[pointIndex * 2], positions[pointIndex * 2 + 1]);
  });
  const width = bounds.maxX - bounds.minX;
  const height = bounds.maxY - bounds.minY;
  const largestExtent = Math.max(width, height, 80);
  const halfWidth = Math.max(width / 2, largestExtent * 0.28) + 24;
  const halfHeight = Math.max(height / 2, largestExtent * 0.28) + 24;
  const centerX = (bounds.minX + bounds.maxX) / 2;
  const centerY = (bounds.minY + bounds.maxY) / 2;

  return {
    cameraBounds: new Float32Array([centerX - halfWidth, centerY - halfHeight, centerX + halfWidth, centerY + halfHeight]),
    positions,
    sourceSpan,
    targetSpan,
  };
}

export function taxonomySceneTransferables(scene: TaxonomyScene) {
  return [
    scene.overviewCameraBounds.buffer,
    scene.positions.buffer,
    scene.pointColors.buffer,
    scene.pointSizes.buffer,
    scene.pointKinds.buffer,
    scene.pointOwners.buffer,
    scene.nodePointIndices.buffer,
    scene.nodeBounds.buffer,
    scene.links.buffer,
    scene.linkColors.buffer,
    scene.linkWidths.buffer,
    scene.linkKinds.buffer,
  ];
}
