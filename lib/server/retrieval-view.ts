import type {
  EvidenceRetrievalDocument,
  QueryClaimRef,
  RetrievalMemberRef,
  RetrievalStageDiagnostics,
} from "@/lib/evidence-retrieval";

export type EvidenceSourceRow = {
  snapshotId: string;
  artifactId: string;
  artifactName: string;
  artifactVersionId: string;
  versionNumber: number;
  mediaType: string;
  sha256: string;
};

export async function loadEvidenceSources(
  DB: D1Database,
  projectId: string,
  snapshotIds: string[],
) {
  if (!snapshotIds.length) return new Map<string, EvidenceSourceRow>();
  const rows = await DB.prepare(`WITH requested AS (
    SELECT value AS snapshot_id FROM json_each(?)
  )
  SELECT g.id AS snapshotId, a.id AS artifactId, a.preferred_name AS artifactName,
    v.id AS artifactVersionId, v.version_number AS versionNumber,
    v.media_type AS mediaType, v.sha256 AS sha256
  FROM requested r JOIN evidence_graph_snapshots g ON g.id = r.snapshot_id
  JOIN artifact_versions v ON v.id = g.root_artifact_version_id
  JOIN artifacts a ON a.id = v.artifact_id
  WHERE g.project_id = ? AND a.project_id = ?`)
    .bind(JSON.stringify(snapshotIds), projectId, projectId)
    .all<EvidenceSourceRow>();
  return new Map(rows.results.map((row) => [row.snapshotId, row]));
}

export function evidenceRefKey(ref: Pick<QueryClaimRef, "snapshotId" | "claimId">) {
  return `${ref.snapshotId}\u0000${ref.claimId}`;
}

export function serviceEvidenceRefKey(
  ref: Pick<RetrievalMemberRef, "snapshot_id" | "claim_id">,
) {
  return `${ref.snapshot_id}\u0000${ref.claim_id}`;
}

export function emptyRetrievalChannels() {
  const skipped: RetrievalStageDiagnostics = {
    attempted: false,
    status: "skipped",
    provider: "none",
    returned: 0,
  };
  return { lexical: skipped, dense: skipped, rerank: skipped, relation: skipped };
}

export function truncateText(value: string, maximum: number) {
  const characters = Array.from(value);
  return characters.length <= maximum
    ? value
    : `${characters.slice(0, maximum).join("")}\u2026`;
}

export function compactEvidencePath(
  document: EvidenceRetrievalDocument,
  {
    appendLabelEllipsis = false,
    maxLabelCharacters = 800,
    maxLinks = 48,
    maxNodes = 24,
    preservePathEnds = false,
    trackLabelTruncation = false,
  }: {
    appendLabelEllipsis?: boolean;
    maxLabelCharacters?: number;
    maxLinks?: number;
    maxNodes?: number;
    preservePathEnds?: boolean;
    trackLabelTruncation?: boolean;
  } = {},
) {
  const path = document.shortestEvidencePath;
  const labelsTruncated = path.nodes.some(
    (node) => Array.from(node.label).length > maxLabelCharacters,
  );
  const nodes = path.nodes.length <= maxNodes
    ? path.nodes
    : preservePathEnds ? [
        ...path.nodes.slice(0, Math.floor(maxNodes / 2)),
        ...path.nodes.slice(-Math.ceil(maxNodes / 2)),
      ] : path.nodes.slice(0, maxNodes);
  const nodeIds = new Set(nodes.map((node) => node.id));
  return {
    rootClaimId: path.rootClaimId,
    nodes: nodes.map((node) => ({
      ...node,
      label: appendLabelEllipsis
        ? truncateText(node.label, maxLabelCharacters)
        : Array.from(node.label).slice(0, maxLabelCharacters).join(""),
    })),
    links: path.links
      .filter((link) => nodeIds.has(link.fromId) && nodeIds.has(link.toId))
      .slice(0, maxLinks),
    depth: path.depth,
    meanEdgeConfidence: path.meanEdgeConfidence,
    truncated: path.nodes.length > maxNodes
      || path.links.length > maxLinks
      || (trackLabelTruncation && labelsTruncated),
  };
}
