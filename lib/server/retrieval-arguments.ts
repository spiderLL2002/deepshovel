import { parseEvidenceGraph } from "../evidence-graph/index.ts";
import { createRetrievalArgumentProjector } from "../evidence-retrieval/arguments.ts";
import { ApiError } from "./evidence-store.ts";

/** Only the server loads these inputs; browser-supplied argument text is not accepted. */
export async function loadRetrievalArguments(
  DB: D1Database, projectId: string,
  refs: Array<{ snapshot_id: string; claim_id: string }>,
  selectedSnapshotId?: string,
) {
  if (!refs.length) return [];
  const snapshots = [...new Set(refs.map(ref => ref.snapshot_id))];
  const rows = await DB.prepare(`WITH requested AS (SELECT value AS snapshot_id FROM json_each(?))
    SELECT s.id AS snapshotId, s.graph_json AS graphJson FROM requested r
    JOIN evidence_graph_snapshots s ON s.id = r.snapshot_id WHERE s.project_id = ?`)
    .bind(JSON.stringify(snapshots), projectId).all<{ snapshotId: string; graphJson: string }>();
  const projectors = new Map(await Promise.all(rows.results.map(async row => {
    const graph = await parseEvidenceGraph(JSON.parse(row.graphJson));
    if (row.snapshotId === selectedSnapshotId) {
      for (const claim of graph.claims) {
        if (!refs.some(ref => ref.snapshot_id === row.snapshotId && ref.claim_id === claim.id)) {
          refs.push({ snapshot_id: row.snapshotId, claim_id: claim.id });
        }
      }
    }
    return [row.snapshotId, createRetrievalArgumentProjector(graph)] as const;
  })));
  return refs.map(ref => {
    const project = projectors.get(ref.snapshot_id);
    if (!project) throw new ApiError(409, "RETRIEVAL_ARGUMENT_UNAVAILABLE", "Recorded argument is unavailable; repeat the search.");
    return project(ref.snapshot_id, ref.claim_id);
  });
}
