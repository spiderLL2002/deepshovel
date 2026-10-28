import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

import {
  EVIDENCE_GRAPH_SCHEMA_VERSION,
  createAtomicClaim,
  sha256Hex,
} from "../lib/evidence-graph/index.ts";

const bindings = { DB: null, BUCKET: {} };
globalThis.__evidenceDetailTestBindings = bindings;

const loader = `
let rootUrl;
export function initialize(data) { rootUrl = data.rootUrl; }
export async function resolve(specifier, context, nextResolve) {
  if (specifier === "cloudflare:workers") {
    return {
      url: "data:text/javascript,export const env=globalThis.__evidenceDetailTestBindings",
      shortCircuit: true,
    };
  }
  if (specifier.startsWith("@/")) {
    let path = specifier.slice(2);
    if (path === "lib/evidence-graph" || path === "lib/evidence-retrieval") path += "/index.ts";
    else if (!/\\.[cm]?[jt]sx?$/.test(path)) path += ".ts";
    return { url: new URL(path, rootUrl).href, shortCircuit: true };
  }
  return nextResolve(specifier, context);
}`;
register(`data:text/javascript,${encodeURIComponent(loader)}`, {
  parentURL: import.meta.url,
  data: { rootUrl: new URL("../", import.meta.url).href },
});

const { GET } = await import("../app/api/evidence-graphs/[snapshotId]/route.ts");

function scope() {
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
  };
}

function selector() {
  return {
    page: 1,
    section: "Results",
    paragraph: null,
    table: "Table 1",
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
  };
}

async function legacyGraph() {
  const claim = await createAtomicClaim({
    proposition: "The measured values agree with the model.",
    scope: scope(),
  });
  const quote = "Table 1 reports the comparison.";
  return {
    schema_version: EVIDENCE_GRAPH_SCHEMA_VERSION,
    paper_id: "paper:legacy-detail",
    artifacts: [{
      kind: "artifact",
      id: "artifact:legacy-detail",
      canonical_name: "Legacy fixture",
      artifact_type: "paper",
    }],
    artifact_versions: [{
      kind: "artifact_version",
      id: "artifact-version:legacy-detail:v1",
      artifact_id: "artifact:legacy-detail",
      version: "v1",
      locator: "fixture.pdf",
      sha256: "b".repeat(64),
      media_type: "application/pdf",
    }],
    anchors: [{
      kind: "source_anchor",
      id: "anchor:legacy-table",
      artifact_version_id: "artifact-version:legacy-detail:v1",
      anchor_type: "table",
      selector: selector(),
      quote,
      quote_sha256: await sha256Hex(quote),
    }],
    visual_evidence: [],
    observations: [{
      kind: "observation",
      id: "observation:legacy-numbers",
      statement: "The measured values were 8.59 and 8.52.",
      observation_type: "measurement",
      anchor_ids: ["anchor:legacy-table"],
      scope: scope(),
      value: null,
    }],
    assumptions: [],
    reasoning: [{
      kind: "reasoning",
      id: "reasoning:legacy",
      statement: "The reported comparison supports agreement.",
      reasoning_type: "inductive",
      anchor_ids: ["anchor:legacy-table"],
      scope: scope(),
      limitations: [],
    }],
    claims: [claim],
    edges: [
      {
        kind: "edge",
        id: "edge:legacy-ground",
        relation: "grounds",
        source_id: "anchor:legacy-table",
        target_id: "observation:legacy-numbers",
        rationale: null,
        confidence: null,
        scope_overlap: null,
      },
      {
        kind: "edge",
        id: "edge:legacy-premise",
        relation: "premise_for",
        source_id: "observation:legacy-numbers",
        target_id: "reasoning:legacy",
        rationale: null,
        confidence: null,
        scope_overlap: null,
      },
      {
        kind: "edge",
        id: "edge:legacy-infer",
        relation: "infers",
        source_id: "reasoning:legacy",
        target_id: claim.id,
        rationale: null,
        confidence: null,
        scope_overlap: null,
      },
    ],
    assessments: [],
    snapshots: [],
  };
}

function alphabeticLabel(index) {
  let value = index + 1;
  let label = "";
  while (value > 0) {
    value -= 1;
    label = String.fromCharCode(97 + (value % 26)) + label;
    value = Math.floor(value / 26);
  }
  return label;
}

async function largeGraph(claimCount) {
  const quote = "The source reports a set of distinct, supported findings.";
  const claims = [];
  const observations = [];
  const reasoning = [];
  const edges = [];
  for (let index = 0; index < claimCount; index += 1) {
    const label = alphabeticLabel(index);
    const claim = await createAtomicClaim({
      proposition: `Finding ${label} is supported by the reported evidence.`,
      scope: scope(),
    });
    const observationId = `observation:large:${label}`;
    const reasoningId = `reasoning:large:${label}`;
    claims.push(claim);
    observations.push({
      kind: "observation",
      id: observationId,
      statement: "The source reports a supported finding.",
      observation_type: "source_assertion",
      anchor_ids: ["anchor:large"],
      scope: scope(),
      value: null,
    });
    reasoning.push({
      kind: "reasoning",
      id: reasoningId,
      statement: `The reported evidence supports finding ${label}.`,
      reasoning_type: "inductive",
      anchor_ids: ["anchor:large"],
      scope: scope(),
      limitations: [],
    });
    edges.push(
      {
        kind: "edge",
        id: `edge:large:ground:${label}`,
        relation: "grounds",
        source_id: "anchor:large",
        target_id: observationId,
        rationale: null,
        confidence: null,
        scope_overlap: null,
      },
      {
        kind: "edge",
        id: `edge:large:premise:${label}`,
        relation: "premise_for",
        source_id: observationId,
        target_id: reasoningId,
        rationale: null,
        confidence: null,
        scope_overlap: null,
      },
      {
        kind: "edge",
        id: `edge:large:infer:${label}`,
        relation: "infers",
        source_id: reasoningId,
        target_id: claim.id,
        rationale: null,
        confidence: null,
        scope_overlap: null,
      },
    );
  }
  return {
    schema_version: EVIDENCE_GRAPH_SCHEMA_VERSION,
    paper_id: "paper:large-detail",
    artifacts: [{
      kind: "artifact",
      id: "artifact:large-detail",
      canonical_name: "Large fixture",
      artifact_type: "paper",
    }],
    artifact_versions: [{
      kind: "artifact_version",
      id: "artifact-version:large-detail:v1",
      artifact_id: "artifact:large-detail",
      version: "v1",
      locator: "large-fixture.pdf",
      sha256: "c".repeat(64),
      media_type: "application/pdf",
    }],
    anchors: [{
      kind: "source_anchor",
      id: "anchor:large",
      artifact_version_id: "artifact-version:large-detail:v1",
      anchor_type: "text",
      selector: selector(),
      quote,
      quote_sha256: await sha256Hex(quote),
    }],
    visual_evidence: [],
    observations,
    assumptions: [],
    reasoning,
    claims,
    edges,
    assessments: [],
    snapshots: [],
  };
}

class Statement {
  constructor(row) {
    this.row = row;
  }

  bind() { return this; }
  async first() { return this.row; }
  async all() { return { results: [] }; }
}

test("stored legacy snapshots remain readable under version-neutral structural parsing", async () => {
  const snapshotId = `snapshot:sha256:${"a".repeat(64)}`;
  const graph = await legacyGraph();
  const row = {
    id: snapshotId,
    rootArtifactVersionId: graph.artifact_versions[0].id,
    schemaVersion: graph.schema_version,
    graphSha256: "a".repeat(64),
    graphJson: JSON.stringify(graph),
    countsJson: JSON.stringify({ observations: 1, claims: 1 }),
    extractorRunId: "extractor:legacy",
    extractorModel: "model:legacy",
    extractorPromptVersion: "extractor/legacy",
    createdAt: "2026-08-15T00:00:00Z",
  };
  bindings.DB = { prepare: () => new Statement(row) };

  const response = await GET(
    new Request(`http://localhost/api/evidence-graphs/${encodeURIComponent(snapshotId)}`),
    { params: Promise.resolve({ snapshotId }) },
  );
  const body = await response.json();

  assert.equal(response.status, 200);
  assert.equal(body.ok, true);
  assert.equal(body.graph.observations[0].statement, "The measured values were 8.59 and 8.52.");
  assert.equal(body.paths.length, 1);
});

test("snapshot detail returns an evidence path for every claim without a hidden fifty-claim cap", async () => {
  const snapshotId = `snapshot:sha256:${"d".repeat(64)}`;
  const graph = await largeGraph(75);
  const row = {
    id: snapshotId,
    rootArtifactVersionId: graph.artifact_versions[0].id,
    schemaVersion: graph.schema_version,
    graphSha256: "d".repeat(64),
    graphJson: JSON.stringify(graph),
    countsJson: JSON.stringify({ observations: 75, claims: 75 }),
    extractorRunId: "extractor:large",
    extractorModel: "model:large",
    extractorPromptVersion: "compiler-extractor/1.0.0",
    createdAt: "2026-08-17T00:00:00Z",
  };
  bindings.DB = { prepare: () => new Statement(row) };

  const response = await GET(
    new Request(`http://localhost/api/evidence-graphs/${encodeURIComponent(snapshotId)}`),
    { params: Promise.resolve({ snapshotId }) },
  );
  const body = await response.json();

  assert.equal(response.status, 200);
  assert.equal(body.graph.claims.length, 75);
  assert.equal(body.paths.length, 75);
  assert.deepEqual(
    body.paths.map((path) => path.root_claim_id),
    body.graph.claims.map((claim) => claim.id),
  );
});
