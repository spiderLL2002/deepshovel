/** Actual recorded F2 plus explicitly synthetic budget/depth controls. */
import assert from "node:assert/strict";
import fs from "node:fs";
import { parseEvidenceGraph } from "../../../lib/evidence-graph/paths.ts";
import { buildClaimComparisonEvidence, buildClaimComparisonSide } from "../../../lib/claim-comparison/projection.ts";

const saved = JSON.parse(fs.readFileSync(new URL("../fixtures/asmari-graph.json", import.meta.url), "utf8"));
const graph = await parseEvidenceGraph(saved.graph);
const claim = graph.claims.find(item => item.source_id === "claim.f2.pressure");
const document = {
  claimId: claim.id, canonicalClaim: claim.proposition, claimType: claim.claim_type,
  polarity: claim.polarity, modality: claim.modality, scope: claim.scope,
  snapshotId: graph.snapshots[0].id, paperId: graph.paper_id,
  paperFamilyId: graph.artifacts[0].id,
};

const budget = structuredClone(graph);
budget.snapshots = []; // Controlled new draft, not an authenticated frozen snapshot.
const observation = graph.observations.find(item => item.id === "obs.f2.assessed");
const premise = graph.edges.find(item => item.source_id === observation.id && item.relation === "premise_for");
const grounds = graph.edges.find(item => item.target_id === observation.id && item.relation === "grounds");
for (let index = 0; index < 260; index += 1) {
  const id = `budget.observation.${index}`;
  budget.observations.push({ ...structuredClone(observation), id });
  budget.edges.push({ ...structuredClone(premise), id: `budget.edge.${index}`, source_id: id });
  budget.edges.push({ ...structuredClone(grounds), id: `budget.grounds.${index}`, target_id: id });
}
const cases = [
  { name: "recorded-f2", provenance: "Unchanged actual recorded graph", graph, options: {} },
  { name: "depth-2", provenance: "Controlled traversal depth on the same recorded graph", graph, options: { max_depth: 2 } },
  { name: "entity-budget", provenance: "Synthetic draft: 260 duplicated observation inputs added solely to exercise the 256 entity limit", graph: budget, options: {} },
].map(item => {
  const evidence = buildClaimComparisonEvidence(item.graph, claim.id, null, item.options);
  const candidate = buildClaimComparisonSide(document, evidence);
  if (item.name === "entity-budget") candidate.source_snapshot_id = "control.snapshot.entity-budget";
  // A normalized textual question has no source graph to declare complete.
  const query = { ...candidate, claim_id: "query.f2", source_snapshot_id: null,
    source_paper_id: null, source_paper_family_id: null, evidence_entities: [], evidence_edges: [],
    evidence_completeness: { ...candidate.evidence_completeness, status: "not_applicable",
      path_truncated: false, available_entity_count: 0, omitted_entity_count: 0,
      omitted_edge_count: 0, content_truncated: false } };
  return { name: item.name, provenance: item.provenance,
    request: { schema_version: "claim-comparison/1.0", project_id: "project.f2.transport-check",
      pairs: [{ id: `pair.${item.name}`, query, candidate }] } };
});
const full = cases[0].request.pairs[0].candidate;
assert.equal(full.evidence_entities.length, 38);
assert.equal(full.evidence_entities.filter(item => item.kind === "assumption").length, 3);
assert.equal(full.evidence_edges.length, 37);
assert.equal(full.evidence_edges.filter(item => item.relation === "premise_for").length, 18);
assert.equal(full.evidence_edges.filter(item => item.relation === "assumption_for").length, 3);
assert.equal(full.evidence_completeness.status, "complete");
assert.equal(cases[1].request.pairs[0].candidate.evidence_completeness.path_truncated, true);
assert.equal(cases[2].request.pairs[0].candidate.evidence_entities.length, 256);
assert.ok(cases[2].request.pairs[0].candidate.evidence_completeness.omitted_entity_count > 0);
assert.ok(cases[2].request.pairs[0].candidate.evidence_completeness.omitted_edge_count > 0);
process.stdout.write(JSON.stringify(cases));
