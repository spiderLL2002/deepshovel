import { createEvidencePathExpander, type EvidenceGraph } from "../evidence-graph/index.ts";

/** Query-time projection of the recorded derivation, independent of the search preview. */
export function createRetrievalArgumentProjector(graph: EvidenceGraph) {
  const expand = createEvidencePathExpander(graph);
  return (snapshotId: string, claimId: string) => {
    const path = expand(claimId);
    const relevant = path.entities.filter(entity => entity.kind !== "artifact" && entity.kind !== "artifact_version");
    let truncated = path.truncated || relevant.length > 256;
    const nodes = relevant.slice(0, 256).map(entity => {
      const raw = "statement" in entity ? entity.statement
        : "proposition" in entity ? entity.proposition
        : entity.kind === "source_anchor" ? entity.quote ?? "" : "";
      const statement = Array.from(raw).slice(0, 8000).join("");
      truncated ||= statement !== raw;
      return {
        id: entity.id, kind: entity.kind, statement,
        scope: "scope" in entity ? entity.scope : null,
        anchor_ids: "anchor_ids" in entity ? entity.anchor_ids : [],
      };
    });
    const ids = new Set(nodes.map(node => node.id));
    const reasoningEdges = path.edges.filter(edge => ids.has(edge.source_id) && ids.has(edge.target_id));
    truncated ||= reasoningEdges.length > 512;
    return {
      snapshot_id: snapshotId, claim_id: claimId, nodes,
      edges: reasoningEdges.slice(0, 512).map(edge => ({
        source_id: edge.source_id, target_id: edge.target_id, relation: edge.relation,
      })),
      truncated,
    };
  };
}

export type RetrievalArgumentContext = ReturnType<ReturnType<typeof createRetrievalArgumentProjector>>;
