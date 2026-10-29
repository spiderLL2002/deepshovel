/** Reproduce the existing claim-detail expander on a recorded scientific graph.
 * Node >=22.13; no packages, network, model calls, or database required.
 * The independent oracle below is a reference checker, not production logic.
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";
import { parseEvidenceGraph, expandEvidencePath } from "./vendor/lib/evidence-graph/paths.ts";

const root = path.dirname(fileURLToPath(import.meta.url));
const outArg = process.argv.indexOf("--out");
const out = outArg < 0 ? path.join(root, "runs/latest") : path.resolve(process.argv[outArg + 1]);
fs.mkdirSync(out, { recursive: true });
const read = name => JSON.parse(fs.readFileSync(path.join(root, name), "utf8"));
const hash = name => crypto.createHash("sha256").update(fs.readFileSync(path.join(root, name))).digest("hex");
const provenance = read("module-provenance.json");
for (const file of provenance.files) assert.equal(hash(file.vendored_path ?? file.fixture_path), file.sha256);
const recorded = read("fixtures/asmari-graph.json");
const graph = await parseEvidenceGraph(recorded.graph);
const signature = e => `${e.source_id ?? e.fromId}\0${e.relation}\0${e.target_id ?? e.toId}`;
const sorted = values => [...values].sort();
const collections = ["anchors", "observations", "reasoning", "assumptions", "claims", "visual_evidence", "artifacts", "artifact_versions"];
const nodes = new Map(collections.flatMap(c => graph[c] ?? []).map(n => [n.id, n]));

// Independent fixed-point reference: scan source edges repeatedly, adding all
// antecedents of already reached nodes. It does not call the expander or its
// adjacency builder. Claim-to-claim comparison edges are explicitly excluded.
function oracle(claimId) {
  const reached = new Set([claimId]);
  const edges = new Map();
  let changed = true;
  while (changed) {
    changed = false;
    for (const e of graph.edges) {
      const source = nodes.get(e.source_id), target = nodes.get(e.target_id);
      if (source.kind === "atomic_claim" && target.kind === "atomic_claim") continue;
      if (!["grounds", "premise_for", "assumption_for", "infers", "supports", "contradicts", "qualifies", "refines", "supplements", "equivalent_to"].includes(e.relation)) continue;
      if (!reached.has(e.target_id)) continue;
      edges.set(e.id, e);
      if (!reached.has(e.source_id)) { reached.add(e.source_id); changed = true; }
    }
  }
  return { reached, edges };
}

function coverage(reference, candidateEdges) {
  const actual = new Set(candidateEdges.map(signature));
  const result = {};
  for (const relation of ["premise_for", "assumption_for"]) {
    const needed = [...reference.edges.values()].filter(e => e.relation === relation);
    const missing = needed.filter(e => !actual.has(signature(e)));
    result[relation] = { required: needed.length, retained: needed.length - missing.length,
      missing_edge_ids: sorted(missing.map(e => e.id)) };
  }
  return result;
}

const claims = [];
const depthChecks = [];
for (const claim of graph.claims) {
  const expected = oracle(claim.id);
  const actual = expandEvidencePath(graph, claim.id);
  assert.deepEqual(sorted(actual.edges.map(e => e.id)), sorted(expected.edges.keys()));
  assert.equal(actual.truncated, false);
  const c = coverage(expected, actual.edges);
  assert.equal(c.premise_for.missing_edge_ids.length + c.assumption_for.missing_edge_ids.length, 0);
  claims.push({ source_claim_id: claim.source_id, claim_id: claim.id,
    entities: actual.entities.length, edges: actual.edges.length, truncated: actual.truncated, coverage: c });
  for (const maxDepth of [1, 2, 3]) {
    const bounded = expandEvidencePath(graph, claim.id, { max_depth: maxDepth });
    const c = coverage(expected, bounded.edges);
    const omitted = c.premise_for.missing_edge_ids.length + c.assumption_for.missing_edge_ids.length;
    assert.equal(bounded.truncated, true);
    depthChecks.push({ source_claim_id: claim.source_id, max_depth: maxDepth,
      entities: bounded.entities.length, edges: bounded.edges.length, truncated: bounded.truncated,
      missing_recorded_dependency_edges: omitted, coverage: c });
  }
}

const f2 = graph.claims.find(c => c.source_id === "claim.f2.pressure");
assert.ok(f2);
const full = expandEvidencePath(graph, f2.id);
const expected = oracle(f2.id);
const originalShortest = recorded.recorded_retrieval_shortest_path;
const historic = read("fixtures/historical-reuse-export.json").payload.original_result.observation_evidence_path;
const direct = ["reason.f2.evidence", "reason.f2.safety"].map(id => ({
  reasoning_id: id,
  premise_ids: sorted(graph.edges.filter(e => e.target_id === id && e.relation === "premise_for").map(e => e.source_id)),
  assumption_ids: sorted(graph.edges.filter(e => e.target_id === id && e.relation === "assumption_for").map(e => e.source_id)),
}));
// These checks anchor the reference in the actual, inspected F2 dependency set.
assert.deepEqual(direct[0].premise_ids, ["obs.f2.assessed", "obs.f2.methods"]);
assert.deepEqual(direct[0].assumption_ids, ["assumption.cohesion", "assumption.friction", "assumption.spatial"]);
assert.deepEqual(direct[1].premise_ids, ["reason.current.synthesis", "reason.f2.evidence", "reason.pressure.transition"]);
assert.equal(coverage(expected, full.edges).premise_for.required, 18);
assert.equal(coverage(expected, full.edges).assumption_for.required, 3);

// Controlled perturbations of one recorded input. These are explicitly
// artificial negative controls, not additional scientific cases or live runs.
const negatives = [];
for (const [name, edit] of [
  ["remove_referenced_observation", g => { g.observations = g.observations.filter(n => n.id !== "obs.f2.assessed"); }],
  ["remove_referenced_assumption", g => { g.assumptions = g.assumptions.filter(n => n.id !== "assumption.friction"); }],
  ["remove_observation_premise_edge", g => { g.edges = g.edges.filter(e => !(e.source_id === "obs.f2.assessed" && e.target_id === "reason.f2.evidence" && e.relation === "premise_for")); }],
]) {
  const g = structuredClone(graph); edit(g);
  let error;
  try { expandEvidencePath(g, f2.id); } catch (e) { error = { name: e.name, code: e.code, message: e.message }; }
  assert.ok(error, name);
  negatives.push({ name, kind: "controlled_input_mutation", rejected: true, error });
}

// An omitted reasoning-to-reasoning relation can leave a structurally valid
// graph. Detecting that source omission requires the frozen reference, not
// merely an internal graph validator. Preserve this counterexample.
const omittedEdge = "edge.premise.013";
const less = structuredClone(graph); less.edges = less.edges.filter(e => e.id !== omittedEdge);
// A new, unpublished draft graph has no old signed snapshot manifest. Retaining
// that manifest would correctly reject this edit as a broken snapshot reference.
less.snapshots = [];
const lessPath = expandEvidencePath(less, f2.id);
const lessCoverage = coverage(expected, lessPath.edges);
assert.equal(lessPath.truncated, false);
assert.ok(lessCoverage.premise_for.missing_edge_ids.includes(omittedEdge));
negatives.push({ name: "omit_internal_reasoning_edge_from_input", kind: "controlled_input_mutation",
  original_snapshot_manifest_removed: true, represents_new_unpublished_graph: true,
  structurally_accepted: true, truncated: lessPath.truncated, externally_detected_missing_edge: omittedEdge,
  interpretation: "No depth truncation occurred. The input graph itself lacks an edge; only comparison with the frozen source graph exposes that omission." });

const scopeRecord = read("fixtures/scope-presentation.json");
assert.equal(new Set(scopeRecord.records.map(r => r.claim_id)).size, 1);
assert.deepEqual(scopeRecord.records.map(r => r.query_relation.relation), ["related", "supports", "related"]);
assert.ok(scopeRecord.records.every(r => r.scope_constraints === null && r.query_relation.scopeRelation === "overlap"));

const report = {
  scope: "Existing production claim-detail expander, offline on seven claims from one recorded Asmari graph; no new extraction/model/live-service execution.",
  runtime: { node: process.version, platform: process.platform, architecture: process.arch },
  source_commit: provenance.source_repository_commit,
  input_snapshot_id: recorded.snapshot.id,
  input_sha256: hash("fixtures/asmari-graph.json"),
  module_files_verified: provenance.files.filter(x => x.vendored_path).length,
  claim_results: claims, bounded_depth_results: depthChecks,
  f2: { direct_inputs: direct,
    full_claim_detail: { entities: full.entities.length, edges: full.edges.length, truncated: full.truncated, coverage: coverage(expected, full.edges) },
    original_search_shortest_preview: { entities: originalShortest.nodes.length, edges: originalShortest.links.length, coverage: coverage(expected, originalShortest.links) },
    historical_auxiliary_export: { entities: historic.nodes.length, edges: historic.edges.length, coverage: coverage(expected, historic.edges) },
  },
  negative_controls: negatives,
  saved_scope_presentation: scopeRecord,
  summary: { claims: claims.length, distinct_papers: 1, all_full_claim_closures_match_reference: true,
    bounded_depth_runs: depthChecks.length,
    bounded_runs_missing_recorded_dependencies: depthChecks.filter(x => x.missing_recorded_dependency_edges > 0).length,
    all_bounded_depth_omissions_flagged: depthChecks.filter(x => x.missing_recorded_dependency_edges > 0).every(x => x.truncated),
    structural_mutation_controls_rejected: 3, source_omission_counterexample_retained: true },
  limitations: [
    "Recorded-input preservation is not extraction accuracy or the completeness of all scientifically necessary premises.",
    "Truncated denotes configured traversal/display limits, not an automatic detector of premises absent from the source graph.",
    "The shortest search preview, full claim-detail expander, and old auxiliary export are different implemented paths.",
    "Incoming dependencies are retained; the check does not establish that every incoming relation is scientifically conjunctive or that alternative arguments are logically sound.",
    "Depth controls are explicit test parameter changes. No token-budget or missing-premise save gate is claimed.",
    "Original source-anchor rectangles remain unchanged; the known F2 wrong-column rectangle is not repaired or validated by this test.",
  ],
};
fs.writeFileSync(path.join(out, "report.json"), JSON.stringify(report, null, 2) + "\n");
fs.writeFileSync(path.join(out, "f2-full-evidence-path.json"), JSON.stringify(full, null, 2) + "\n");
fs.writeFileSync(path.join(out, "f2-depth-2.json"), JSON.stringify(expandEvidencePath(graph, f2.id, { max_depth: 2 }), null, 2) + "\n");
console.log(JSON.stringify({ ...report.summary, f2: report.f2, output_directory: path.relative(process.cwd(), out) || "." }, null, 2));
