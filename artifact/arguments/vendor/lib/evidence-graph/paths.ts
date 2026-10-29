import {
  CLAIM_RELATIONS,
  ENTITY_COLLECTIONS,
  isClaimComparison,
  isDerivationEdge,
  type EvidenceEdge,
  type EvidenceEntity,
  type EvidenceGraph,
  type EvidencePath,
  type Snapshot,
} from "./types.ts";
import { hashCanonical } from "./canonical.ts";
import {
  allEntities,
  entityMap,
  invalid,
  validateEvidenceGraph,
  verifyEvidenceGraphIntegrity,
  type SnapshotMembers,
} from "./validation.ts";

function byId(left: { id: string }, right: { id: string }) {
  return left.id < right.id ? -1 : left.id > right.id ? 1 : 0;
}

function snapshotPayload(graph: EvidenceGraph, snapshot: SnapshotMembers) {
  const entities = entityMap(graph);
  const edges = new Map(graph.edges.map((edge) => [edge.id, edge]));
  const assessments = new Map(graph.assessments.map((assessment) => [assessment.id, assessment]));
  return {
    schema_version: graph.schema_version,
    paper_id: graph.paper_id,
    parent_snapshot_id: snapshot.parent_snapshot_id,
    entities: [...snapshot.entity_ids].sort().map((id) => entities.get(id)),
    edges: [...snapshot.edge_ids].sort().map((id) => edges.get(id)),
    assessments: [...snapshot.assessment_ids].sort().map((id) => assessments.get(id)),
  };
}

export async function computeSnapshotContentHash(graph: EvidenceGraph, snapshot: SnapshotMembers) {
  return hashCanonical(snapshotPayload(graph, snapshot));
}

export async function createSnapshot(
  graph: EvidenceGraph,
  metadata: Pick<Snapshot, "parent_snapshot_id" | "created_at" | "generator">,
): Promise<Snapshot> {
  validateEvidenceGraph(graph);
  const membership: SnapshotMembers = {
    parent_snapshot_id: metadata.parent_snapshot_id,
    entity_ids: allEntities(graph).map((item) => item.id).sort(),
    edge_ids: graph.edges.map((item) => item.id).sort(),
    assessment_ids: graph.assessments.map((item) => item.id).sort(),
  };
  const content_sha256 = await computeSnapshotContentHash(graph, membership);
  const snapshot: Snapshot = {
    kind: "snapshot",
    id: `snapshot:sha256:${content_sha256}`,
    ...membership,
    content_sha256,
    created_at: metadata.created_at,
    generator: metadata.generator,
  };
  validateEvidenceGraph({ ...graph, snapshots: [...graph.snapshots, snapshot] });
  return snapshot;
}

export async function parseEvidenceGraph(value: unknown) {
  return verifyEvidenceGraphIntegrity(value, computeSnapshotContentHash);
}

/** Hash a validated graph independently of top-level collection ordering. */
export async function hashEvidenceGraph(graph: EvidenceGraph) {
  return hashValidatedEvidenceGraph(await parseEvidenceGraph(graph));
}

/** Hash a graph that has already crossed the structural and integrity boundary. */
export function hashValidatedEvidenceGraph(graph: EvidenceGraph) {
  const ordered = { ...graph } as EvidenceGraph;
  for (const collection of ENTITY_COLLECTIONS) {
    ordered[collection] = [...graph[collection]].sort(byId) as never;
  }
  ordered.edges = [...graph.edges].sort(byId);
  ordered.assessments = [...graph.assessments].sort(byId);
  ordered.snapshots = [...graph.snapshots].sort(byId);
  return hashCanonical(ordered);
}

function structuralParents(entity: EvidenceEntity, entities: Map<string, EvidenceEntity>) {
  if (entity.kind === "artifact_version") return [entities.get(entity.artifact_id)].filter(Boolean) as EvidenceEntity[];
  if (entity.kind === "source_anchor") return [entities.get(entity.artifact_version_id)].filter(Boolean) as EvidenceEntity[];
  if (entity.kind === "structured_visual_evidence") return [entities.get(entity.anchor_id)].filter(Boolean) as EvidenceEntity[];
  if (entity.kind === "observation" || entity.kind === "assumption" || entity.kind === "reasoning" || entity.kind === "atomic_claim") {
    return entity.anchor_ids.map((id) => entities.get(id)).filter(Boolean) as EvidenceEntity[];
  }
  return [];
}

type EvidencePathOptions = { include_claim_relations?: boolean; max_depth?: number };

/**
 * Validate and index one graph once before expanding any number of Claim paths.
 */
export function createEvidencePathExpander(
  graph: EvidenceGraph,
  options: EvidencePathOptions = {},
) {
  validateEvidenceGraph(graph);
  const entities = entityMap(graph);
  const maxDepth = options.max_depth ?? 32;
  if (!Number.isSafeInteger(maxDepth) || maxDepth < 1 || maxDepth > 100) invalid("$options.max_depth", "RANGE", "must be an integer between 1 and 100");
  const edgesByEntity = new Map<string, Array<{ edge: EvidenceEdge; parent: EvidenceEntity }>>();
  const addEdge = (entityId: string, edge: EvidenceEdge, parentId: string) => {
    const entries = edgesByEntity.get(entityId);
    const entry = { edge, parent: entities.get(parentId) as EvidenceEntity };
    if (entries) entries.push(entry);
    else edgesByEntity.set(entityId, [entry]);
  };

  for (const edge of graph.edges) {
    if (isDerivationEdge(edge, entities)) {
      addEdge(edge.target_id, edge, edge.source_id);
    }
    if (options.include_claim_relations === true && isClaimComparison(edge, entities)) {
      addEdge(edge.target_id, edge, edge.source_id);
      if (CLAIM_RELATIONS.has(edge.relation)) {
        addEdge(edge.source_id, edge, edge.target_id);
      }
    }
  }

  return (rootClaimId: string): EvidencePath => {
    const root = entities.get(rootClaimId);
    if (!root || root.kind !== "atomic_claim") invalid("$rootClaimId", "REFERENCE", "must identify an atomic claim");
    const selectedEntities = new Map<string, EvidenceEntity>();
    const selectedEdges = new Map<string, EvidenceEdge>();
    const visitedDepth = new Map<string, number>();
    const queue: Array<{ id: string; depth: number }> = [{ id: rootClaimId, depth: 0 }];
    let truncated = false;

    const enqueue = (entity: EvidenceEntity | undefined, depth: number) => {
      if (!entity) return;
      selectedEntities.set(entity.id, entity);
      if ((visitedDepth.get(entity.id) ?? Number.POSITIVE_INFINITY) > depth) queue.push({ id: entity.id, depth });
    };

    for (let index = 0; index < queue.length; index += 1) {
      const current = queue[index];
      if ((visitedDepth.get(current.id) ?? Number.POSITIVE_INFINITY) <= current.depth) continue;
      visitedDepth.set(current.id, current.depth);
      const entity = entities.get(current.id) as EvidenceEntity;
      selectedEntities.set(entity.id, entity);
      const nextEntities = structuralParents(entity, entities);
      const steps = edgesByEntity.get(entity.id) ?? [];
      for (const step of steps) nextEntities.push(step.parent);
      if (current.depth >= maxDepth) {
        if (nextEntities.length) truncated = true;
        continue;
      }
      for (const step of steps) selectedEdges.set(step.edge.id, step.edge);
      for (const parent of nextEntities) enqueue(parent, current.depth + 1);
    }

    const claims = new Set([...selectedEntities.values()].filter((item) => item.kind === "atomic_claim").map((item) => item.id));
    return {
      root_claim_id: rootClaimId,
      entities: [...selectedEntities.values()].sort(byId),
      edges: [...selectedEdges.values()].sort(byId),
      assessments: graph.assessments.filter((item) => claims.has(item.target_claim_id)).sort(byId),
      truncated,
    };
  };
}

/**
 * Expand a claim backwards to reasoning, observations, anchors and artifacts.
 * Claim comparisons are opt-in so a single-paper Evidence Path remains bounded.
 */
export function expandEvidencePath(
  graph: EvidenceGraph,
  rootClaimId: string,
  options: EvidencePathOptions = {},
): EvidencePath {
  return createEvidencePathExpander(graph, options)(rootClaimId);
}
