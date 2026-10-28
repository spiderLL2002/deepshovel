import assert from "node:assert/strict";
import test from "node:test";

import {
  EVIDENCE_GRAPH_SCHEMA_VERSION,
  EvidenceGraphValidationError,
  attachCompilerVerificationReport,
  canonicalStringify,
  createAtomicClaim,
  createSnapshot,
  expandEvidencePath,
  hashEvidenceGraph,
  parseEvidenceGraph,
  sha256Hex,
  validateEvidenceGraph,
} from "../lib/evidence-graph/index.ts";

function scope(overrides = {}) {
  return {
    location: [],
    time: null,
    formation: [],
    sample_type: [],
    method: [],
    condition: [],
    measurement: [],
    uncertainty: null,
    entities: [],
    ...overrides,
  };
}

function selector(overrides = {}) {
  return {
    page: null,
    section: null,
    paragraph: null,
    table: null,
    row_start: null,
    row_end: null,
    row_range_label: null,
    column_names: [],
    figure: null,
    series: null,
    char_start: null,
    char_end: null,
    region: null,
    region_label: null,
    dataset_path: null,
    ...overrides,
  };
}

async function fixture() {
  const quote = "Hg concentration peaks near 250 Ma.";
  const claim = await createAtomicClaim({
    proposition: "Strong volcanic activity occurred near 250 Ma.",
    scope: scope({
      location: ["Basin X, eastern section"],
      time: { label: "near 250 Ma", start: 250, end: 250, unit: "Ma" },
      formation: ["Formation A"],
      sample_type: ["shale"],
      method: ["ICP-MS"],
    }),
  });
  const graph = {
    schema_version: EVIDENCE_GRAPH_SCHEMA_VERSION,
    paper_id: "paper:fixture",
    artifacts: [{ kind: "artifact", id: "artifact:paper-1", canonical_name: "Paper 1", artifact_type: "paper" }],
    artifact_versions: [{
      kind: "artifact_version",
      id: "artifact-version:paper-1:v1",
      artifact_id: "artifact:paper-1",
      version: "v1",
      locator: "paper-1.pdf",
      sha256: "a".repeat(64),
      media_type: "application/pdf",
    }],
    anchors: [{
      kind: "source_anchor",
      id: "anchor:figure-5",
      artifact_version_id: "artifact-version:paper-1:v1",
      anchor_type: "figure",
      selector: selector({ page: 8, figure: "Figure 5", region: { x: 0.1, y: 0.2, width: 0.5, height: 0.4, unit: "normalized" } }),
      quote,
      quote_sha256: await sha256Hex(quote),
    }],
    visual_evidence: [{
      kind: "structured_visual_evidence",
      id: "visual:figure-5:series-hg",
      anchor_id: "anchor:figure-5",
      visual_kind: "figure",
      axes: [
        { id: "axis:age", direction: "x", label: "Age", unit: "Ma", scale: "linear" },
        { id: "axis:hg", direction: "y", label: "Hg", unit: "ng/g", scale: "linear" },
      ],
      series: [{
        id: "series:hg",
        label: "Hg",
        x_axis_id: "axis:age",
        y_axis_id: "axis:hg",
        points: [
          { x: 252, y: 25, uncertainty: null },
          { x: 250, y: 130, uncertainty: null },
        ],
      }],
      cells: [],
      features: [{ kind: "peak", description: "Local Hg maximum near 250 Ma", series_id: "series:hg", value: 130, unit: "ng/g" }],
    }],
    observations: [{
      kind: "observation",
      id: "observation:hg-peak",
      statement: "Hg has a pronounced local maximum near 250 Ma.",
      observation_type: "measurement",
      anchor_ids: ["anchor:figure-5"],
      scope: scope({ location: ["Basin X, eastern section"], time: { label: "near 250 Ma", start: 250, end: 250, unit: "Ma" }, measurement: ["Hg concentration"] }),
      value: { raw: "130 ng/g", numeric: 130, comparator: null, unit: "ng/g", uncertainty: null },
    }],
    assumptions: [{
      kind: "assumption",
      id: "assumption:hg-volcanism",
      statement: "A synchronous Hg anomaly can indicate large-scale volcanism.",
      assumption_type: "causal",
      explicit: true,
      anchor_ids: [],
      scope: scope({ condition: ["other major Hg sources excluded"] }),
    }],
    reasoning: [{
      kind: "reasoning",
      id: "reasoning:joint-indicator",
      statement: "The Hg peak, under the stated proxy assumption, supports coeval volcanism.",
      reasoning_type: "abductive",
      anchor_ids: [],
      scope: scope({ time: { label: "near 250 Ma", start: 250, end: 250, unit: "Ma" } }),
      limitations: ["Hg anomalies are not uniquely volcanic"],
    }],
    claims: [claim],
    edges: [
      { kind: "edge", id: "edge:ground", relation: "grounds", source_id: "visual:figure-5:series-hg", target_id: "observation:hg-peak", rationale: "The observation summarizes the located series and peak.", confidence: 0.98, scope_overlap: null },
      { kind: "edge", id: "edge:premise", relation: "premise_for", source_id: "observation:hg-peak", target_id: "reasoning:joint-indicator", rationale: "The measured peak is a premise of the interpretation.", confidence: 0.9, scope_overlap: null },
      { kind: "edge", id: "edge:assumption", relation: "assumption_for", source_id: "assumption:hg-volcanism", target_id: "reasoning:joint-indicator", rationale: "The proxy interpretation depends on this assumption.", confidence: 0.8, scope_overlap: null },
      { kind: "edge", id: "edge:infer", relation: "infers", source_id: "reasoning:joint-indicator", target_id: claim.id, rationale: "The reasoning yields the scoped atomic claim.", confidence: 0.82, scope_overlap: null },
    ],
    assessments: [{
      kind: "assessment",
      id: "assessment:claim-1",
      target_claim_id: claim.id,
      verdict: "supported",
      basis_edge_ids: ["edge:ground", "edge:premise", "edge:assumption", "edge:infer"],
      evidence_ids: ["anchor:figure-5", "visual:figure-5:series-hg", "observation:hg-peak", "assumption:hg-volcanism", "reasoning:joint-indicator"],
      rationale: "The located visual datum and explicit assumption form a complete evidence path.",
      limitations: ["Hg anomalies are not uniquely volcanic"],
      confidence: 0.82,
      assessor: { source: "model", name: "independent-verifier", model: "verifier-test/1" },
      created_at: "2026-08-15T00:00:00Z",
    }],
    snapshots: [],
  };
  return { graph, claim };
}

test("canonical JSON sorts object keys and graph hashes ignore collection ordering", async () => {
  assert.equal(canonicalStringify({ z: 1, a: { y: 2, x: 3 } }), '{"a":{"x":3,"y":2},"z":1}');
  const { graph } = await fixture();
  const first = await hashEvidenceGraph(graph);
  const reordered = structuredClone(graph);
  reordered.edges.reverse();
  reordered.artifacts.reverse();
  assert.equal(await hashEvidenceGraph(reordered), first);
});

test("proposition_key is scope-free while scoped_claim_id changes with scope", async () => {
  const proposition = "Hg concentration increased significantly.";
  const east = await createAtomicClaim({ proposition, scope: scope({ location: ["Basin X east"] }) });
  const west = await createAtomicClaim({ proposition: `  ${proposition.toUpperCase()}  `, scope: scope({ location: ["Basin X west"] }) });
  assert.equal(east.proposition_key, west.proposition_key);
  assert.notEqual(east.scoped_claim_id, west.scoped_claim_id);
  assert.equal(east.id, east.scoped_claim_id);
});

test("strict parser validates all identities, hashes and rejects unknown fields", async () => {
  const { graph } = await fixture();
  assert.equal(await parseEvidenceGraph(graph), graph);

  const unknown = structuredClone(graph);
  unknown.claims[0].confidence_score = 0.9;
  assert.throws(() => validateEvidenceGraph(unknown), (error) => {
    assert.ok(error instanceof EvidenceGraphValidationError);
    assert.equal(error.code, "UNKNOWN_FIELD");
    return true;
  });

  const forgedQuote = structuredClone(graph);
  forgedQuote.anchors[0].quote_sha256 = "b".repeat(64);
  await assert.rejects(() => parseEvidenceGraph(forgedQuote), (error) => {
    assert.equal(error.code, "HASH_MISMATCH");
    return true;
  });
});

test("canonical local IDs and references use the Python Identifier contract", async () => {
  const { graph } = await fixture();
  const invalidId = structuredClone(graph);
  invalidId.artifacts[0].id = "1-artifact";
  invalidId.artifact_versions[0].artifact_id = "1-artifact";
  assert.throws(() => validateEvidenceGraph(invalidId), (error) => {
    assert.equal(error.code, "IDENTIFIER");
    assert.equal(error.path, "$.artifacts[0].id");
    return true;
  });

  const invalidReference = structuredClone(graph);
  invalidReference.edges[0].source_id = "visual/figure-5";
  assert.throws(() => validateEvidenceGraph(invalidReference), (error) => {
    assert.equal(error.code, "IDENTIFIER");
    assert.equal(error.path, "$.edges[0].source_id");
    return true;
  });
});

test("canonical text bounds preserve 16k quotes and 8k raw values", async () => {
  const { graph } = await fixture();
  const atBoundary = structuredClone(graph);
  atBoundary.anchors[0].quote = "q".repeat(16_000);
  atBoundary.anchors[0].quote_sha256 = await sha256Hex(atBoundary.anchors[0].quote);
  atBoundary.observations[0].value.raw = "r".repeat(8_000);
  await parseEvidenceGraph(atBoundary);

  const oversizedStatement = structuredClone(graph);
  oversizedStatement.reasoning[0].statement = "s".repeat(8_001);
  assert.throws(() => validateEvidenceGraph(oversizedStatement), (error) => {
    assert.equal(error.code, "LENGTH");
    assert.equal(error.path, "$.reasoning[0].statement");
    return true;
  });

  const oversizedQuote = structuredClone(graph);
  oversizedQuote.anchors[0].quote = "q".repeat(16_001);
  assert.throws(() => validateEvidenceGraph(oversizedQuote), (error) => {
    assert.equal(error.code, "LENGTH");
    assert.equal(error.path, "$.anchors[0].quote");
    return true;
  });

  const oversizedRawValue = structuredClone(graph);
  oversizedRawValue.observations[0].value.raw = "r".repeat(8_001);
  assert.throws(() => validateEvidenceGraph(oversizedRawValue), (error) => {
    assert.equal(error.code, "LENGTH");
    assert.equal(error.path, "$.observations[0].value.raw");
    return true;
  });
});

test("artifacts and versions cannot be admitted without downstream provenance", async () => {
  const { graph } = await fixture();
  const orphanArtifact = structuredClone(graph);
  orphanArtifact.artifacts.push({
    kind: "artifact",
    id: "artifact:orphan",
    canonical_name: "Orphan artifact",
    artifact_type: "supplement",
  });
  assert.throws(() => validateEvidenceGraph(orphanArtifact), (error) => {
    assert.equal(error.code, "ORPHAN_ARTIFACT");
    assert.equal(error.path, "$.artifacts[1]");
    return true;
  });

  const orphanVersion = structuredClone(graph);
  orphanVersion.artifacts.push({
    kind: "artifact",
    id: "artifact:supplement",
    canonical_name: "Supplement",
    artifact_type: "supplement",
  });
  orphanVersion.artifact_versions.push({
    kind: "artifact_version",
    id: "artifact-version:supplement:v1",
    artifact_id: "artifact:supplement",
    version: "v1",
    locator: null,
    sha256: "b".repeat(64),
    media_type: "application/pdf",
  });
  assert.throws(() => validateEvidenceGraph(orphanVersion), (error) => {
    assert.equal(error.code, "ORPHAN_ARTIFACT_VERSION");
    assert.equal(error.path, "$.artifact_versions[1]");
    return true;
  });
});

test("edge endpoint types reject structurally invalid evidence links", async () => {
  const { graph, claim } = await fixture();
  const invalid = structuredClone(graph);
  invalid.edges.find((edge) => edge.id === "edge:infer").source_id = "anchor:figure-5";
  assert.throws(() => validateEvidenceGraph(invalid), (error) => {
    assert.equal(error.code, "ENDPOINT_TYPE");
    assert.match(error.message, /infers cannot connect source_anchor to atomic_claim/);
    return true;
  });
  assert.ok(claim.id.startsWith("claim:sha256:"));
});

test("reasoning conclusions keep contradicting and qualifying stances", async () => {
  const { graph, claim } = await fixture();
  for (const relation of ["contradicts", "qualifies"]) {
    const stance = structuredClone(graph);
    stance.edges.find((edge) => edge.id === "edge:infer").relation = relation;
    stance.assessments = [];
    validateEvidenceGraph(stance);
    const path = expandEvidencePath(stance, claim.id);
    assert.ok(path.edges.some((edge) => edge.relation === relation), relation);
    assert.ok(path.entities.some((entity) => entity.id === "anchor:figure-5"), relation);
  }

  const both = structuredClone(graph);
  both.assessments = [];
  both.edges.push({ ...both.edges.find((edge) => edge.id === "edge:infer"), id: "edge:qualify", relation: "qualifies" });
  validateEvidenceGraph(both);

  const stray = structuredClone(graph);
  stray.edges.find((edge) => edge.id === "edge:infer").relation = "refines";
  stray.assessments = [];
  assert.throws(() => validateEvidenceGraph(stray), (error) => {
    assert.equal(error.code, "ENDPOINT_TYPE");
    return true;
  });

  const missing = structuredClone(graph);
  missing.edges = missing.edges.filter((edge) => edge.id !== "edge:infer");
  missing.assessments = [];
  assert.throws(() => validateEvidenceGraph(missing), (error) => {
    assert.ok(["MISSING_CONCLUSION", "UNSUPPORTED_CLAIM"].includes(error.code), error.code);
    return true;
  });
});

test("derivation links must remain a DAG", async () => {
  const { graph, claim } = await fixture();
  const cyclic = structuredClone(graph);
  cyclic.edges.push({
    kind: "edge",
    id: "edge:cycle",
    relation: "premise_for",
    source_id: claim.id,
    target_id: "reasoning:joint-indicator",
    rationale: "Invalidly feeds the conclusion back into its own reasoning.",
    confidence: 1,
    scope_overlap: null,
  });
  assert.throws(() => validateEvidenceGraph(cyclic), (error) => {
    assert.equal(error.code, "DERIVATION_CYCLE");
    return true;
  });
});

test("Evidence Path expansion walks claim back to raw artifact provenance", async () => {
  const { graph, claim } = await fixture();
  const path = expandEvidencePath(graph, claim.id);
  assert.deepEqual(new Set(path.entities.map((entity) => entity.kind)), new Set([
    "artifact",
    "artifact_version",
    "source_anchor",
    "structured_visual_evidence",
    "observation",
    "assumption",
    "reasoning",
    "atomic_claim",
  ]));
  assert.deepEqual(path.edges.map((edge) => edge.id), ["edge:assumption", "edge:ground", "edge:infer", "edge:premise"]);
  assert.equal(path.assessments.length, 1);
  assert.equal(path.truncated, false);

  const bounded = expandEvidencePath(graph, claim.id, { max_depth: 1 });
  assert.equal(bounded.truncated, true);
  assert.deepEqual(new Set(bounded.entities.map((entity) => entity.kind)), new Set(["atomic_claim", "reasoning"]));
});

test("reasoning may form a multi-level DAG before reaching a terminal Claim", async () => {
  const { graph, claim } = await fixture();
  const multilevel = structuredClone(graph);
  const firstReasoning = multilevel.reasoning[0];
  multilevel.reasoning.push({
    ...firstReasoning,
    id: "reasoning:paper-level-synthesis",
    statement: "The proxy interpretation and study context jointly support the paper-level conclusion.",
    reasoning_type: "synthesis",
  });
  const oldInfer = multilevel.edges.find((edge) => edge.id === "edge:infer");
  oldInfer.relation = "premise_for";
  oldInfer.target_id = "reasoning:paper-level-synthesis";
  multilevel.edges.push({
    kind: "edge",
    id: "edge:terminal-infer",
    relation: "infers",
    source_id: "reasoning:paper-level-synthesis",
    target_id: claim.id,
    rationale: "The synthesis yields the terminal paper-level conclusion.",
    confidence: 0.82,
    scope_overlap: null,
  });
  multilevel.assessments[0].basis_edge_ids.push("edge:terminal-infer");
  multilevel.assessments[0].evidence_ids.push("reasoning:paper-level-synthesis");

  validateEvidenceGraph(multilevel);
  const path = expandEvidencePath(multilevel, claim.id);
  assert.deepEqual(
    path.entities.filter((entity) => entity.kind === "reasoning").map((entity) => entity.id),
    ["reasoning:joint-indicator", "reasoning:paper-level-synthesis"],
  );

  const dangling = structuredClone(multilevel);
  dangling.edges = dangling.edges.filter((edge) => edge.id !== "edge:terminal-infer");
  assert.throws(() => validateEvidenceGraph(dangling), (error) => {
    assert.equal(error.code, "MISSING_CONCLUSION");
    return true;
  });
});

test("snapshot identity freezes selected objects and detects later tampering", async () => {
  const { graph } = await fixture();
  const snapshot = await createSnapshot(graph, {
    parent_snapshot_id: null,
    created_at: "2026-08-15T01:00:00Z",
    generator: { source: "extractor", name: "fixture", version: "1" },
  });
  const frozen = { ...graph, snapshots: [snapshot] };
  await parseEvidenceGraph(frozen);
  assert.equal(snapshot.id, `snapshot:sha256:${snapshot.content_sha256}`);

  const tampered = structuredClone(frozen);
  tampered.observations[0].statement = "Tampered after snapshot creation.";
  await assert.rejects(() => parseEvidenceGraph(tampered), (error) => {
    assert.equal(error.code, "HASH_MISMATCH");
    assert.match(error.path, /snapshots/);
    return true;
  });
});

test("Compiler verification reports remain separate from extraction graphs", async () => {
  const { graph, claim } = await fixture();
  const extractionGraph = { ...graph, assessments: [] };
  const graphSha256 = await hashEvidenceGraph(extractionGraph);
  const verified = await attachCompilerVerificationReport(extractionGraph, {
    schema_version: "1.0",
    paper_id: graph.paper_id,
    graph_sha256: graphSha256,
    verifier_model: "verifier-test/1",
    assessments: [{
      id: "assessment:compiler:1",
      claim_id: claim.id,
      scoped_claim_id: claim.scoped_claim_id,
      verdict: "supported",
      confidence: 0.8,
      evidence_ids: ["anchor:figure-5", "visual:figure-5:series-hg", "observation:hg-peak", "reasoning:joint-indicator"],
      rationale: "The located evidence path supports the claim.",
      limitations: [],
    }],
    warnings: [],
  }, { created_at: "2026-08-15T02:00:00Z" });

  assert.equal(extractionGraph.assessments.length, 0);
  assert.equal(verified.assessments[0].target_claim_id, claim.id);
  assert.deepEqual(verified.assessments[0].evidence_ids, [
    "anchor:figure-5",
    "visual:figure-5:series-hg",
    "observation:hg-peak",
    "reasoning:joint-indicator",
  ]);
});

test("Compiler verification requires exact claim coverage and scoped identity", async () => {
  const { graph, claim } = await fixture();
  const extractionGraph = { ...graph, assessments: [] };
  const graphSha256 = await hashEvidenceGraph(extractionGraph);
  const assessment = {
    id: "assessment:compiler:coverage",
    claim_id: claim.id,
    scoped_claim_id: claim.scoped_claim_id,
    verdict: "supported",
    confidence: 0.8,
    evidence_ids: ["observation:hg-peak"],
    rationale: "The observation supports the claim.",
    limitations: [],
  };
  const report = {
    schema_version: "1.0",
    paper_id: graph.paper_id,
    graph_sha256: graphSha256,
    verifier_model: "verifier-test/1",
    assessments: [assessment],
    warnings: [],
  };

  await assert.rejects(
    () => attachCompilerVerificationReport(extractionGraph, { ...report, assessments: [] }, { created_at: "2026-08-15T02:00:00Z" }),
    (error) => error.code === "VERIFICATION_COVERAGE",
  );
  await assert.rejects(
    () => attachCompilerVerificationReport(extractionGraph, { ...report, assessments: [assessment, { ...assessment, id: "assessment:compiler:duplicate" }] }, { created_at: "2026-08-15T02:00:00Z" }),
    (error) => error.code === "VERIFICATION_DUPLICATE",
  );
  await assert.rejects(
    () => attachCompilerVerificationReport(extractionGraph, { ...report, assessments: [{ ...assessment, scoped_claim_id: `claim:sha256:${"f".repeat(64)}` }] }, { created_at: "2026-08-15T02:00:00Z" }),
    (error) => error.code === "VERIFICATION_IDENTITY",
  );
  await assert.rejects(
    () => attachCompilerVerificationReport(extractionGraph, { ...report, assessments: [{ ...assessment, evidence_ids: [] }] }, { created_at: "2026-08-15T02:00:00Z" }),
    (error) => error.code === "VERIFICATION_EVIDENCE_REQUIRED",
  );
  await assert.rejects(
    () => attachCompilerVerificationReport(extractionGraph, { ...report, assessments: [{ ...assessment, evidence_ids: [claim.id] }] }, { created_at: "2026-08-15T02:00:00Z" }),
    (error) => error.code === "VERIFICATION_EVIDENCE_NODE_TYPE",
  );
  await assert.rejects(
    () => attachCompilerVerificationReport(extractionGraph, { ...report, assessments: [{ ...assessment, evidence_ids: ["reasoning:joint-indicator"] }] }, { created_at: "2026-08-15T02:00:00Z" }),
    (error) => error.code === "VERIFICATION_EVIDENCE_REQUIRED",
  );

  const insufficient = await attachCompilerVerificationReport(
    extractionGraph,
    {
      ...report,
      assessments: [{
        ...assessment,
        verdict: "insufficient_evidence",
        evidence_ids: [],
        rationale: "No citable evidence establishes the claim.",
      }],
    },
    { created_at: "2026-08-15T02:00:00Z" },
  );
  assert.equal(insufficient.assessments[0].evidence_ids.length, 0);

  const direct = await attachCompilerVerificationReport(
    extractionGraph,
    { ...report, assessments: [{ ...assessment, evidence_ids: ["observation:hg-peak", "reasoning:joint-indicator"] }] },
    { created_at: "2026-08-15T02:00:00Z" },
  );
  assert.deepEqual(direct.assessments[0].evidence_ids, ["observation:hg-peak", "reasoning:joint-indicator"]);

  const offPathGraph = structuredClone(extractionGraph);
  offPathGraph.anchors.push({
    kind: "source_anchor",
    id: "anchor:off-path",
    artifact_version_id: "artifact-version:paper-1:v1",
    anchor_type: "text",
    selector: selector({ page: 9, section: "Unrelated appendix" }),
    quote: null,
    quote_sha256: null,
  });
  const offPathReport = {
    ...report,
    graph_sha256: await hashEvidenceGraph(offPathGraph),
    assessments: [{ ...assessment, evidence_ids: ["anchor:off-path"] }],
  };
  await assert.rejects(
    () => attachCompilerVerificationReport(offPathGraph, offPathReport, { created_at: "2026-08-15T02:00:00Z" }),
    (error) => error.code === "EVIDENCE_PATH",
  );
});

test("Compiler verification rationale wording is advisory while evidence paths stay strict", async () => {
  const { graph, claim } = await fixture();
  const extractionGraph = { ...graph, assessments: [] };
  const assessment = (evidence_ids, rationale, overrides = {}) => ({
    id: "assessment:compiler:numeric",
    claim_id: claim.id,
    scoped_claim_id: claim.scoped_claim_id,
    verdict: "qualified",
    confidence: 0.8,
    evidence_ids,
    rationale,
    limitations: [],
    ...overrides,
  });
  const report = async (source, item, warnings = []) => ({
    schema_version: "1.0",
    paper_id: source.paper_id,
    graph_sha256: await hashEvidenceGraph(source),
    verifier_model: "verifier-test/1",
    assessments: [item],
    warnings,
  });

  await attachCompilerVerificationReport(
    extractionGraph,
    await report(
      extractionGraph,
      assessment(["observation:hg-peak", "reasoning:joint-indicator"], "The compared values are 8.59 and 8.52."),
    ),
    { created_at: "2026-08-15T02:00:00Z" },
  );
  await attachCompilerVerificationReport(
    extractionGraph,
    await report(
      extractionGraph,
      assessment(["observation:hg-peak", "reasoning:joint-indicator"], "The scientific value is 0.8."),
    ),
    { created_at: "2026-08-15T02:00:00Z" },
  );

  const referenceReport = await report(
    extractionGraph,
    assessment(
      ["observation:hg-peak", "reasoning:joint-indicator"],
      "Figure 15, Table 5, page 6, 图15、表5和第6页中的路径支持该结论。",
      { limitations: ["An outside-path conflict reports 8.59."] },
    ),
    ["Another source reports 8.52."],
  );
  await attachCompilerVerificationReport(extractionGraph, referenceReport, { created_at: "2026-08-15T02:00:00Z" });
  await attachCompilerVerificationReport(
    extractionGraph,
    await report(extractionGraph, assessment(["observation:hg-peak", "reasoning:joint-indicator"], "The cited reasoning supports a qualified conclusion.")),
    { created_at: "2026-08-15T02:00:00Z" },
  );

  const observationGraph = structuredClone(extractionGraph);
  observationGraph.observations[0].statement = "The compared values are 8.59 and 8.52.";
  observationGraph.anchors[0].quote = "The compared values are 8.59 and 8.52.";
  observationGraph.anchors[0].quote_sha256 = await sha256Hex(observationGraph.anchors[0].quote);
  await attachCompilerVerificationReport(
    observationGraph,
    await report(observationGraph, assessment(["observation:hg-peak"], "The compared values are 8.590 and 8.52.")),
    { created_at: "2026-08-15T02:00:00Z" },
  );

  const unitGraph = structuredClone(extractionGraph);
  unitGraph.observations[0].statement = "The treated sample reached 5 mg.";
  unitGraph.observations[0].value = {
    raw: "5",
    numeric: 5,
    comparator: null,
    unit: "mg",
    uncertainty: null,
  };
  unitGraph.anchors[0].quote = "The treated sample reached 5 mg.";
  unitGraph.anchors[0].quote_sha256 = await sha256Hex(unitGraph.anchors[0].quote);
  await attachCompilerVerificationReport(
    unitGraph,
    await report(unitGraph, assessment(["observation:hg-peak"], "The cited result is 5 mg.")),
    { created_at: "2026-08-15T02:00:00Z" },
  );
  for (const rationale of [
    "The cited result is 5 kg.",
    "The cited result is greater than 5 mg.",
  ]) {
    await attachCompilerVerificationReport(
      unitGraph,
      await report(
        unitGraph,
        assessment(["observation:hg-peak"], rationale),
      ),
      { created_at: "2026-08-15T02:00:00Z" },
    );
  }

  const tableGraph = structuredClone(extractionGraph);
  tableGraph.anchors[0].anchor_type = "table";
  tableGraph.anchors[0].selector.figure = null;
  tableGraph.anchors[0].selector.table = "Table 5";
  tableGraph.observations[0].statement = "The compared values are 8.59 and 8.52.";
  tableGraph.observations[0].value = {
    raw: "8.59 and 8.52",
    numeric: 8.59,
    comparator: null,
    unit: "degree",
    uncertainty: null,
  };
  tableGraph.visual_evidence[0] = {
    ...tableGraph.visual_evidence[0],
    visual_kind: "table",
    axes: [],
    series: [],
    cells: [
      { row: 1, column: "Theory", raw_value: "8.59", numeric_value: 8.59, unit: "degree" },
      { row: 1, column: "Measured", raw_value: "8.52", numeric_value: 8.52, unit: "degree" },
    ],
    features: [],
  };
  await attachCompilerVerificationReport(
    tableGraph,
    await report(tableGraph, assessment(["visual:figure-5:series-hg"], "The table reports 8.59 and 8.52.")),
    { created_at: "2026-08-15T02:00:00Z" },
  );
});

test("matcher hardening preserves the fixture snapshot hash", async () => {
  const { graph } = await fixture();
  const before = await hashEvidenceGraph(graph);
  const parsed = await parseEvidenceGraph(structuredClone(graph));
  assert.equal(await hashEvidenceGraph(parsed), before);
});

test("each observation feeds exactly one reasoning path while anchors remain reusable", async () => {
  const { graph, claim } = await fixture();
  const splitGraph = structuredClone(graph);
  splitGraph.assessments = [];
  splitGraph.reasoning.push({
    ...structuredClone(splitGraph.reasoning[0]),
    id: "reasoning:duplicate-owner",
    statement: "A second reasoning path uses the same observation.",
  });
  splitGraph.edges.push(
    {
      kind: "edge",
      id: "edge:duplicate-premise",
      relation: "premise_for",
      source_id: "observation:hg-peak",
      target_id: "reasoning:duplicate-owner",
      rationale: null,
      confidence: null,
      scope_overlap: null,
    },
    {
      kind: "edge",
      id: "edge:duplicate-infer",
      relation: "infers",
      source_id: "reasoning:duplicate-owner",
      target_id: claim.id,
      rationale: null,
      confidence: null,
      scope_overlap: null,
    },
  );
  await assert.rejects(
    () => parseEvidenceGraph(splitGraph),
    (error) => error.code === "OBSERVATION_PATH_OWNERSHIP",
  );

  const sharedAnchorGraph = structuredClone(graph);
  sharedAnchorGraph.assessments = [];
  sharedAnchorGraph.observations.push({
    ...structuredClone(sharedAnchorGraph.observations[0]),
    id: "observation:hg-local-peak",
    statement: "The exact quote reports a local maximum near 250 Ma.",
    value: null,
  });
  sharedAnchorGraph.edges.push(
    {
      kind: "edge",
      id: "edge:shared-anchor-ground",
      relation: "grounds",
      source_id: "anchor:figure-5",
      target_id: "observation:hg-local-peak",
      rationale: null,
      confidence: null,
      scope_overlap: null,
    },
    {
      kind: "edge",
      id: "edge:shared-anchor-premise",
      relation: "premise_for",
      source_id: "observation:hg-local-peak",
      target_id: "reasoning:joint-indicator",
      rationale: null,
      confidence: null,
      scope_overlap: null,
    },
  );
  assert.deepEqual(
    sharedAnchorGraph.observations.map((item) => item.anchor_ids),
    [["anchor:figure-5"], ["anchor:figure-5"]],
  );
  await parseEvidenceGraph(sharedAnchorGraph);
});
