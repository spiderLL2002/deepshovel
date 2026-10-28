export const EVIDENCE_KNOWLEDGE_GRAPH_VERSION = "evidence-knowledge-graph/4.0" as const;

export type ClaimRef = {
  snapshotId: string;
  claimId: string;
};

export type ClaimModelRelation =
  | "equivalent_to"
  | "supports"
  | "contradicts"
  | "refines"
  | "qualifies"
  | "supplements";

export type StoredClaimRelation =
  | "directly_implies"
  | "equivalent_to"
  | "contradicts"
  | "supplements";

export type ClaimRelationEdge = {
  source: ClaimRef;
  target: ClaimRef;
  relation: StoredClaimRelation;
  modelRelation: ClaimModelRelation;
  confidence: number | null;
};

export function claimRefKey(ref: ClaimRef) {
  return `${ref.snapshotId}\u0000${ref.claimId}`;
}

export function toClaimModelRelation(value: string): ClaimModelRelation | null {
  if (value === "equivalent") return "equivalent_to";
  if (value === "supports"
    || value === "contradicts"
    || value === "refines"
    || value === "qualifies"
    || value === "supplements") return value;
  return null;
}

export function isDirectionalClaimRelation(
  relation: ClaimModelRelation,
): relation is "supports" | "refines" | "qualifies" {
  return relation === "supports" || relation === "refines" || relation === "qualifies";
}

export function projectClaimRelation(
  query: ClaimRef,
  candidate: ClaimRef,
  retrievalRelation: string,
  direction: "query_to_result" | "result_to_query",
  confidence: number | null,
): ClaimRelationEdge | null {
  if (claimRefKey(query) === claimRefKey(candidate)) return null;
  const modelRelation = toClaimModelRelation(retrievalRelation);
  if (modelRelation === null) return null;
  if (isDirectionalClaimRelation(modelRelation)) {
    return direction === "query_to_result"
      ? { source: query, target: candidate, relation: "directly_implies", modelRelation, confidence }
      : { source: candidate, target: query, relation: "directly_implies", modelRelation, confidence };
  }
  const [source, target] = [query, candidate].sort((left, right) =>
    claimRefKey(left).localeCompare(claimRefKey(right), "en"));
  return { source, target, relation: modelRelation, modelRelation, confidence };
}
