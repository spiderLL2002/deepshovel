import type {
  AtomicClaim,
  EvidenceEntity,
  EvidenceGraph,
  Observation,
  Reasoning,
  SourceAnchor,
  StructuredVisualEvidence,
} from "../evidence-graph/index.ts";
import type { CitationCandidateEntity, CitationEntityKind } from "./types.ts";

function entityText(entity: EvidenceEntity) {
  if (entity.kind === "atomic_claim") return entity.proposition;
  if (entity.kind === "observation" || entity.kind === "reasoning" || entity.kind === "assumption") return entity.statement;
  if (entity.kind === "source_anchor") return entity.quote ?? "";
  if (entity.kind === "structured_visual_evidence") {
    const cells = entity.cells.map((cell) => `${cell.column}: ${cell.raw_value}`);
    const features = entity.features.map((feature) => feature.description);
    return [...features, ...cells].join("; ");
  }
  return "";
}

function anchorIds(entity: EvidenceEntity) {
  if (entity.kind === "source_anchor") return [entity.id];
  if (entity.kind === "structured_visual_evidence") return [entity.anchor_id];
  if (entity.kind === "observation" || entity.kind === "reasoning" || entity.kind === "atomic_claim" || entity.kind === "assumption") return entity.anchor_ids;
  return [];
}

function rootClaims(graph: EvidenceGraph) {
  const entities = new Map<string, EvidenceEntity>([
    ...graph.anchors,
    ...graph.visual_evidence,
    ...graph.observations,
    ...graph.assumptions,
    ...graph.reasoning,
    ...graph.claims,
  ].map((item) => [item.id, item]));
  const outgoing = new Map<string, Set<string>>();
  const connect = (source: string, target: string) => {
    const targets = outgoing.get(source) ?? new Set<string>();
    targets.add(target);
    outgoing.set(source, targets);
  };
  for (const edge of graph.edges) connect(edge.source_id, edge.target_id);
  for (const entity of entities.values()) {
    if ("anchor_ids" in entity) for (const anchorId of entity.anchor_ids) connect(anchorId, entity.id);
    if (entity.kind === "structured_visual_evidence") connect(entity.anchor_id, entity.id);
  }
  const memo = new Map<string, string[]>();
  const roots = (id: string, visiting = new Set<string>()): string[] => {
    const known = memo.get(id);
    if (known) return known;
    if (visiting.has(id)) return [];
    const entity = entities.get(id);
    if (entity?.kind === "atomic_claim") return [id];
    visiting.add(id);
    const result = [...new Set([...(outgoing.get(id) ?? [])].flatMap((target) => roots(target, visiting)))].sort();
    visiting.delete(id);
    memo.set(id, result);
    return result;
  };
  return { entities, roots };
}

type AlignableEntity = SourceAnchor | StructuredVisualEvidence | Observation | Reasoning | AtomicClaim;

function isAlignable(entity: EvidenceEntity): entity is AlignableEntity {
  return ["source_anchor", "structured_visual_evidence", "observation", "reasoning", "atomic_claim"].includes(entity.kind);
}

export function buildCitationCandidateEntities(graph: EvidenceGraph): CitationCandidateEntity[] {
  const { entities, roots } = rootClaims(graph);
  const output: CitationCandidateEntity[] = [];
  for (const entity of entities.values()) {
    if (!isAlignable(entity)) continue;
    const text = entityText(entity).trim();
    if (!text) continue;
    output.push({
      id: entity.id,
      kind: entity.kind as CitationEntityKind,
      text: text.slice(0, 32_000),
      anchor_ids: [...new Set(anchorIds(entity))].slice(0, 64),
      root_claim_ids: roots(entity.id).slice(0, 256),
    });
  }
  return output;
}
