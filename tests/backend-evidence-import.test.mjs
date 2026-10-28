import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

import {
  EVIDENCE_GRAPH_SCHEMA_VERSION,
  attachCompilerVerificationReport,
  createAtomicClaim,
  createSnapshot,
  hashEvidenceGraph,
  sha256Hex,
} from "../lib/evidence-graph/index.ts";

const bindings = { DB: null, BUCKET: {} };
globalThis.__evidenceImportTestBindings = bindings;

const storeStub = `
export const PROJECT_ID = "DEEPSHOVEL-PROJECT";
export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message); this.name = "ApiError"; this.status = status; this.code = code; this.details = details;
  }
}
export function assertSameOrigin() {}
export function getActor() { return "test@example.com"; }
export async function getBindings() { return globalThis.__evidenceImportTestBindings; }
export async function readJsonObject(request) { return request.json(); }
export function safeError(error) {
  if (error instanceof ApiError) {
    return Response.json({ ok: false, error: { code: error.code, message: error.message } }, { status: error.status });
  }
  return Response.json({ ok: false, error: { code: "INTERNAL_ERROR", message: String(error) } }, { status: 500 });
}`;

const loader = `
let rootUrl;
let storeUrl;
export function initialize(data) { rootUrl = data.rootUrl; storeUrl = data.storeUrl; }
export async function resolve(specifier, context, nextResolve) {
  if (specifier === "cloudflare:workers") {
    return {
      url: "data:text/javascript,export const env=globalThis.__evidenceImportTestBindings",
      shortCircuit: true,
    };
  }
  if (specifier === "@/lib/server/evidence-store") {
    return { url: storeUrl, shortCircuit: true };
  }
  if (specifier.startsWith("@/")) {
    let path = specifier.slice(2);
    if (path === "lib/evidence-graph" || path === "lib/evidence-retrieval" || path === "lib/citation-reception") path += "/index.ts";
    else if (!/\\.[cm]?[jt]sx?$/.test(path)) path += ".ts";
    return { url: new URL(path, rootUrl).href, shortCircuit: true };
  }
  return nextResolve(specifier, context);
}`;
register(`data:text/javascript,${encodeURIComponent(loader)}`, {
  parentURL: import.meta.url,
  data: {
    rootUrl: new URL("../", import.meta.url).href,
    storeUrl: `data:text/javascript,${encodeURIComponent(storeStub)}`,
  },
});

const { POST } = await import("../app/api/evidence-graphs/route.ts");
const { parseEvidenceImportEnvelope } = await import("../app/api/evidence-graphs/import-contract.ts");
const { persistEvidenceImport } = await import("../app/api/evidence-graphs/import-store.ts");

class Statement {
  args = [];

  constructor(db, sql) {
    this.db = db;
    this.sql = sql;
  }

  bind(...args) {
    this.args = args;
    return this;
  }

  first() {
    return this.db.first(this.sql, this.args);
  }

  all() {
    return this.db.all(this.sql, this.args);
  }
}

class MemoryD1 {
  snapshots = new Map();
  verifications = new Map();
  citationSourceManifests = new Map();
  bibliographicReferences = new Map();
  citationMentions = new Map();
  audits = [];
  failCitationMentionInsert = false;
  artifactVersionQueries = 0;
  snapshotIdentityQueries = 0;
  verificationIdentityQueries = 0;

  constructor(artifact) {
    this.artifact = artifact instanceof Map ? null : artifact;
    this.artifacts = artifact instanceof Map ? artifact : null;
  }

  prepare(sql) {
    return new Statement(this, sql);
  }

  async first(sql, args) {
    if (sql.includes("FROM artifact_versions")) return this.artifact;
    if (sql.includes("FROM citation_source_manifests")) {
      return this.citationSourceManifests.get(args[1]) ?? null;
    }
    if (sql.includes("FROM evidence_graph_snapshots")) {
      const rows = [...this.snapshots.values()];
      if (sql.includes("graph_sha256 = ?")) return rows.find((row) => row.graphSha256 === args[1]) ?? null;
      if (sql.includes("extractor_run_id = ?")) return rows.find((row) => row.extractorRunId === args[1]) ?? null;
    }
    if (sql.includes("FROM verification_runs")) {
      const rows = [...this.verifications.values()];
      if (sql.includes("COUNT(*) AS runCount")) {
        return { runCount: rows.filter((row) => row.snapshotId === args[1]).length };
      }
      if (sql.includes("verifier_run_id = ?")) return rows.find((row) => row.verifierRunId === args[1]) ?? null;
      if (sql.includes("report_sha256 = ?")) return rows.find((row) => row.reportSha256 === args[1]) ?? null;
    }
    return null;
  }

  async all(sql, args) {
    if (sql.includes("FROM artifact_versions")) {
      this.artifactVersionQueries += 1;
      const versionIds = JSON.parse(args[1]);
      return { results: versionIds.flatMap((id) => {
        const artifact = this.artifacts?.get(id) ?? this.artifact;
        return artifact ? [{ id, ...artifact }] : [];
      }) };
    }
    if (sql.includes("FROM evidence_graph_snapshots")) {
      this.snapshotIdentityQueries += 1;
      return { results: [...this.snapshots.values()].filter((row) => (
        row.graphSha256 === args[1] || row.extractorRunId === args[2]
      )) };
    }
    if (sql.includes("FROM verification_runs")) {
      this.verificationIdentityQueries += 1;
      return { results: [...this.verifications.values()].filter((row) => (
        row.verifierRunId === args[1] || row.reportSha256 === args[2]
      )) };
    }
    return { results: [] };
  }

  async batch(statements) {
    const snapshots = new Map(this.snapshots);
    const verifications = new Map(this.verifications);
    const citationSourceManifests = new Map(this.citationSourceManifests);
    const bibliographicReferences = new Map(this.bibliographicReferences);
    const citationMentions = new Map(this.citationMentions);
    const audits = [...this.audits];
    try {
      for (const statement of statements) this.execute(statement.sql, statement.args);
    } catch (error) {
      this.snapshots = snapshots;
      this.verifications = verifications;
      this.citationSourceManifests = citationSourceManifests;
      this.bibliographicReferences = bibliographicReferences;
      this.citationMentions = citationMentions;
      this.audits = audits;
      throw error;
    }
    return statements.map(() => ({ success: true }));
  }

  execute(sql, args) {
    if (sql.includes("INSERT INTO evidence_graph_snapshots")) {
      if ([...this.snapshots.values()].some((row) => row.graphSha256 === args[4] || row.extractorRunId === args[7])) {
        throw new Error("UNIQUE evidence graph");
      }
      this.snapshots.set(args[0], {
        id: args[0],
        rootArtifactVersionId: args[2],
        schemaVersion: args[3],
        graphSha256: args[4],
        graphJson: args[5],
        countsJson: args[6],
        extractorRunId: args[7],
        extractorModel: args[8],
        extractorPromptVersion: args[9],
        createdAt: args[11],
      });
      return;
    }
    if (sql.includes("INSERT INTO verification_runs")) {
      if ([...this.verifications.values()].some((row) => row.verifierRunId === args[3] || row.reportSha256 === args[6])) {
        throw new Error("UNIQUE verification");
      }
      this.verifications.set(args[0], {
        id: args[0],
        snapshotId: args[2],
        verifierRunId: args[3],
        verifierModel: args[4],
        verifierPromptVersion: args[5],
        reportSha256: args[6],
        reportJson: args[7],
        createdAt: args[9],
      });
      return;
    }
    if (sql.includes("INSERT INTO citation_source_manifests")) {
      if (this.citationSourceManifests.has(args[0])
        || [...this.citationSourceManifests.values()].some((row) => row.sourceSha256 === args[4])) {
        throw new Error("UNIQUE citation source manifest");
      }
      this.citationSourceManifests.set(args[0], {
        snapshotId: args[0],
        schemaVersion: args[2],
        extractorVersion: args[3],
        sourceSha256: args[4],
        referenceCount: args[5],
        mentionCount: args[6],
        warningsJson: args[7],
        createdAt: args[8],
      });
      return;
    }
    if (sql.includes("INSERT INTO bibliographic_references")) {
      for (const row of JSON.parse(args[3])) {
        if (this.bibliographicReferences.has(row.id)) throw new Error("UNIQUE bibliographic reference");
        this.bibliographicReferences.set(row.id, {
          ...row,
          projectId: args[0],
          citingSnapshotId: args[1],
          createdAt: args[2],
        });
      }
      return;
    }
    if (sql.includes("INSERT INTO citation_mentions")) {
      if (this.failCitationMentionInsert) throw new Error("injected citation mention failure");
      for (const row of JSON.parse(args[3])) {
        if (this.citationMentions.has(row.id)) throw new Error("UNIQUE citation mention");
        this.citationMentions.set(row.id, {
          ...row,
          projectId: args[0],
          citingSnapshotId: args[1],
          createdAt: args[2],
        });
      }
      return;
    }
    if (sql.includes("INSERT INTO audit_events")) {
      this.audits.push(sql.includes("verification.imported") ? "verification.imported" : "evidence_graph.imported");
    }
  }
}

function scope() {
  return {
    location: ["Basin X"],
    time: null,
    formation: [],
    sample_type: ["shale"],
    method: ["ICP-MS"],
    condition: [],
    measurement: ["Hg concentration"],
    uncertainty: null,
    entities: ["Hg"],
  };
}

async function fixture() {
  const sha256 = "a".repeat(64);
  const versionId = `artifact-version:sha256:${sha256}`;
  const claim = await createAtomicClaim({
    proposition: "The Hg anomaly supports coeval volcanic activity.",
    source_id: "claim:source:1",
    scope: scope(),
  });
  const graph = {
    schema_version: EVIDENCE_GRAPH_SCHEMA_VERSION,
    paper_id: "paper:route-fixture",
    artifacts: [{ kind: "artifact", id: "artifact:paper-route", canonical_name: "Route fixture", artifact_type: "paper" }],
    artifact_versions: [{
      kind: "artifact_version",
      id: versionId,
      artifact_id: "artifact:paper-route",
      version: "1",
      locator: "paper.txt",
      sha256,
      media_type: "text/plain",
    }],
    anchors: [{
      kind: "source_anchor",
      id: "anchor:route:1",
      artifact_version_id: versionId,
      anchor_type: "text",
      selector: {
        page: 1,
        section: "Results",
        paragraph: 1,
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
      },
      quote: null,
      quote_sha256: null,
    }],
    visual_evidence: [],
    observations: [{
      kind: "observation",
      id: "observation:route:1",
      statement: "Hg increases in the target interval.",
      observation_type: "trend",
      anchor_ids: ["anchor:route:1"],
      scope: scope(),
      value: null,
    }],
    assumptions: [],
    reasoning: [{
      kind: "reasoning",
      id: "reasoning:route:1",
      statement: "The proxy anomaly is consistent with volcanic input.",
      reasoning_type: "abductive",
      anchor_ids: [],
      scope: scope(),
      limitations: ["The proxy is not unique to volcanism."],
    }],
    claims: [claim],
    edges: [
      { kind: "edge", id: "edge:route:ground", relation: "grounds", source_id: "anchor:route:1", target_id: "observation:route:1", rationale: null, confidence: 1, scope_overlap: null },
      { kind: "edge", id: "edge:route:premise", relation: "premise_for", source_id: "observation:route:1", target_id: "reasoning:route:1", rationale: null, confidence: 0.8, scope_overlap: null },
      { kind: "edge", id: "edge:route:infer", relation: "infers", source_id: "reasoning:route:1", target_id: claim.id, rationale: null, confidence: 0.7, scope_overlap: null },
    ],
    assessments: [],
    snapshots: [],
  };
  const graphSha256 = await hashEvidenceGraph(graph);
  const report = {
    schema_version: "1.0",
    paper_id: graph.paper_id,
    graph_sha256: graphSha256,
    verifier_model: "gpt-5.6-luna",
    assessments: [{
      id: "assessment:route:1",
      claim_id: "claim:source:1",
      scoped_claim_id: claim.id,
      verdict: "qualified",
      confidence: 0.7,
      evidence_ids: ["anchor:route:1", "observation:route:1", "reasoning:route:1"],
      rationale: "The evidence path supports the claim with a proxy-specific limitation.",
      limitations: ["The proxy is not unique to volcanism."],
    }],
    warnings: [],
  };
  return { graph, graphSha256, report, sha256, versionId };
}

function request(envelope) {
  return new Request("http://localhost/api/evidence-graphs", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(envelope),
  });
}

function envelope(graph, versionId, report, runId = "verification:run:1") {
  return {
    graph,
    root_artifact_version_id: versionId,
    extraction: { run_id: "extraction:run:1", model: "gpt-5.6-luna", prompt_version: "compiler-extractor/1.0.0" },
    verification: { run_id: runId, model: "gpt-5.6-luna", prompt_version: "compiler-verifier/1.0.0", report },
  };
}

function extractionOnlyEnvelope(graph, versionId, promptVersion) {
  return {
    graph,
    root_artifact_version_id: versionId,
    extraction: {
      run_id: "extraction:run:scientific-admission",
      model: "gpt-5.6-luna",
      prompt_version: promptVersion,
    },
  };
}

async function compilerEnvelope(
  graph,
  versionId,
  baseReport,
  verdict = "contradicted",
) {
  const report = structuredClone(baseReport);
  const claim = graph.claims[0];
  report.graph_sha256 = await hashEvidenceGraph(graph);
  report.assessments[0] = {
    ...report.assessments[0],
    claim_id: claim.source_id ?? claim.id,
    scoped_claim_id: claim.scoped_claim_id,
    verdict,
    rationale: "The cited evidence path does not establish the extracted scientific semantics.",
  };
  return {
    graph,
    root_artifact_version_id: versionId,
    extraction: {
      run_id: "extraction:run:compiler",
      model: "gpt-5.6-luna",
      prompt_version: "compiler-extractor/1.0.0",
    },
    verification: {
      run_id: "verification:run:compiler",
      model: "gpt-5.6-luna",
      prompt_version: "compiler-verifier/1.0.0",
      report,
    },
  };
}

async function setFormulaEvidence(graph, downstreamFormula) {
  const sourceFormula = "D(α)=180°+2α−4β";
  graph.anchors[0].quote = sourceFormula;
  graph.anchors[0].quote_sha256 = await sha256Hex(sourceFormula);
  graph.observations[0].statement = sourceFormula;
  graph.observations[0].value = null;
  graph.reasoning[0].statement = downstreamFormula;
  const claim = await createAtomicClaim({
    proposition: downstreamFormula,
    source_id: graph.claims[0].source_id,
    scope: graph.claims[0].scope,
  });
  graph.claims = [claim];
  graph.edges.find((edge) => edge.relation === "infers").target_id = claim.id;
}

function artifactRecord(sha256) {
  return {
    artifactId: "artifact:paper-route",
    sha256,
    mediaType: "text/plain",
    sourceLocator: "paper.txt",
    artifactType: "paper",
  };
}

function citationSourceFixture(versionId, rawText = "Smith (2024). Prior result. doi:10.1234/example") {
  const quote = "Our result agrees with the prior experiment [1].";
  const marker = "[1]";
  const charStart = quote.indexOf(marker);
  return {
    schema_version: "citation-source/1.0",
    extractor_version: "citation-source-extractor/1.0",
    artifact_version_id: versionId,
    references: [{
      id: `citation-reference:sha256:${"1".repeat(64)}`,
      ordinal: 1,
      label: "1",
      raw_text: rawText,
      doi: "10.1234/example",
      year: 2024,
      source_block_id: "block:references:1",
      page: 8,
    }],
    mentions: [{
      id: `citation-mention:sha256:${"2".repeat(64)}`,
      reference_id: `citation-reference:sha256:${"1".repeat(64)}`,
      marker,
      exact_quote: quote,
      context_text: quote,
      source_block_id: "block:results:1",
      context_block_ids: ["block:results:1"],
      page: 3,
      bbox: { x: 0.1, y: 0.2, width: 0.7, height: 0.05 },
      char_start: charStart,
      char_end: charStart + marker.length,
      group_size: 1,
      candidate_functions: ["result_comparison"],
    }],
    warnings: [],
  };
}

test("Compiler import preserves current and historical model provenance", async () => {
  const { parseEvidenceImportEnvelope, CURRENT_COMPILER_MODEL } = await import("../app/api/evidence-graphs/import-contract.ts");
  assert.equal(CURRENT_COMPILER_MODEL, "gpt-6-luna");
  for (const model of ["gpt-6-luna", "gpt-5.6-luna", "deepseek-v4-flash"]) {
    const { graph, report, versionId } = await fixture();
    const payload = await compilerEnvelope(graph, versionId, report);
    payload.extraction.model = model;
    payload.verification.model = model;
    payload.verification.report.verifier_model = model;
    const parsed = await parseEvidenceImportEnvelope(payload);
    assert.equal(parsed.extraction.model, model);
    assert.equal(parsed.verification.model, model);
    payload.verification.model = "unsupported-model";
    await assert.rejects(parseEvidenceImportEnvelope(payload), /model is unsupported/u);
  }
});

test("Compiler rewrite audit is optional, value-free, and strictly shaped", async () => {
  const { graph, report, versionId } = await fixture();
  const payload = await compilerEnvelope(graph, versionId, report);
  assert.equal((await parseEvidenceImportEnvelope(payload)).compilerAudit, null);

  payload.compiler_audit = {
    schema_version: "compiler-audit/1.0",
    entries: [
      { kind: "anchor_retyped", target: "anchor.text.1", detail: "source_span_hydration:figure->text" },
      { kind: "claim_removed", target: "claim.local.5", detail: "verification:verification_claim_unassessed" },
      { kind: "verification_attempts_merged", target: null, detail: "contributing_attempts=2" },
    ],
    omitted_entries: 0,
    warnings: ["openalex_classification_unavailable"],
  };
  const parsed = await parseEvidenceImportEnvelope(payload);
  assert.equal(parsed.compilerAudit.entries.length, 3);
  assert.deepEqual(parsed.compilerAudit.warnings, ["openalex_classification_unavailable"]);

  for (const mutate of [
    (audit) => { audit.schema_version = "compiler-audit/9"; },
    (audit) => { audit.entries[0].kind = "Not A Code"; },
    (audit) => { audit.entries[0].target = "has spaces"; },
    (audit) => { audit.entries[0].extra = true; },
    (audit) => { audit.omitted_entries = -1; },
  ]) {
    const broken = structuredClone(payload);
    mutate(broken.compiler_audit);
    await assert.rejects(parseEvidenceImportEnvelope(broken), (error) => error.code === "INVALID_COMPILER_AUDIT" || error.code === "INVALID_IMPORT_ENVELOPE");
  }
});

test("Compiler stores semantic disagreements with their negative assessment", async () => {
  const changedSign = "D(α)=180°+2α+4β";
  const { graph, report, sha256, versionId } = await fixture();
  await setFormulaEvidence(graph, changedSign);
  const db = new MemoryD1(artifactRecord(sha256));
  bindings.DB = db;

  const response = await POST(request(await compilerEnvelope(graph, versionId, report)));

  assert.equal(response.status, 201);
  assert.equal(db.snapshots.size, 1);
  assert.equal(db.verifications.size, 1);
  assert.equal(
    JSON.parse([...db.verifications.values()][0].reportJson).assessments[0].verdict,
    "contradicted",
  );
});
test("Compiler accepts presentation-equivalent LaTeX", async () => {
  const { graph, report, sha256, versionId } = await fixture();
  const renderedFormula = "D(α)=180°+2α−4β";
  await setFormulaEvidence(graph, renderedFormula);
  const latexFormula = String.raw`\[D(\alpha)=180^{\circ}+2\alpha-4\beta\]\tag{3}`;
  graph.anchors[0].quote = latexFormula;
  graph.anchors[0].quote_sha256 = await sha256Hex(latexFormula);
  const db = new MemoryD1(artifactRecord(sha256));
  bindings.DB = db;

  const response = await POST(request(await compilerEnvelope(graph, versionId, report, "qualified")));

  assert.equal(response.status, 201);
  assert.equal(db.snapshots.size, 1);
  assert.equal(db.verifications.size, 1);
});

test("Compiler accepts MinerU spaced-degree OCR", async () => {
  const { graph, report, sha256, versionId } = await fixture();
  const renderedFormula = "D(α)=180°+2α−4β";
  await setFormulaEvidence(graph, renderedFormula);
  const mineruFormula = String.raw`\[D(\alpha)=1 8 0^{\circ}+2\alpha-4\beta\]\tag{3}`;
  graph.anchors[0].quote = mineruFormula;
  graph.anchors[0].quote_sha256 = await sha256Hex(mineruFormula);
  const db = new MemoryD1(artifactRecord(sha256));
  bindings.DB = db;

  const response = await POST(request(await compilerEnvelope(graph, versionId, report, "qualified")));

  assert.equal(response.status, 201);
  assert.equal(db.snapshots.size, 1);
  assert.equal(db.verifications.size, 1);
});

test("Compiler keeps structural integrity and exact verification coverage as atomic hard gates", async (t) => {
  await t.test("a nonempty graph without verification is rejected", async () => {
    const { graph, sha256, versionId } = await fixture();
    const db = new MemoryD1(artifactRecord(sha256));
    bindings.DB = db;

    const response = await POST(request(extractionOnlyEnvelope(
      graph,
      versionId,
      "compiler-extractor/1.0.0",
    )));

    assert.equal(response.status, 422);
    assert.equal((await response.json()).error.code, "COMPILER_VERIFICATION_MISSING");
    assert.equal(db.snapshots.size, 0);
    assert.equal(db.verifications.size, 0);
    assert.deepEqual(db.audits, []);
  });

  await t.test("incomplete verification is rejected", async () => {
    const { graph, report, sha256, versionId } = await fixture();
    const payload = await compilerEnvelope(graph, versionId, report);
    payload.verification.report.assessments = [];
    const db = new MemoryD1(artifactRecord(sha256));
    bindings.DB = db;

    const response = await POST(request(payload));

    assert.equal(response.status, 422);
    assert.equal((await response.json()).error.code, "INVALID_VERIFICATION_REPORT");
    assert.equal(db.snapshots.size, 0);
    assert.equal(db.verifications.size, 0);
    assert.deepEqual(db.audits, []);
  });

  await t.test("a nonempty graph with the wrong verifier version is rejected", async () => {
    const { graph, report, sha256, versionId } = await fixture();
    const payload = await compilerEnvelope(graph, versionId, report);
    payload.verification.prompt_version = "compiler-verifier/unsupported";
    const db = new MemoryD1(artifactRecord(sha256));
    bindings.DB = db;

    const response = await POST(request(payload));

    assert.equal(response.status, 422);
    assert.equal((await response.json()).error.code, "COMPILER_PROMPT_VERSION_MISMATCH");
    assert.equal(db.snapshots.size, 0);
    assert.equal(db.verifications.size, 0);
    assert.deepEqual(db.audits, []);
  });

  const corruptions = [
    {
      name: "quote hash",
      mutate(payload) { payload.graph.anchors[0].quote = "Tampered after hashing."; },
    },
    {
      name: "evidence path",
      mutate(payload) {
        payload.graph.edges = payload.graph.edges.filter((edge) => edge.relation !== "premise_for");
      },
    },
    {
      name: "schema version",
      mutate(payload) { payload.graph.schema_version = "9.9"; },
    },
  ];
  for (const corruption of corruptions) {
    await t.test(`broken ${corruption.name} is rejected before any write`, async () => {
      const { graph, report, sha256, versionId } = await fixture();
      const payload = await compilerEnvelope(graph, versionId, report);
      corruption.mutate(payload);
      const db = new MemoryD1(artifactRecord(sha256));
      bindings.DB = db;

      const response = await POST(request(payload));

      assert.equal(response.status, 422);
      assert.equal((await response.json()).error.code, "INVALID_EVIDENCE_GRAPH");
      assert.equal(db.snapshots.size, 0);
      assert.equal(db.verifications.size, 0);
      assert.deepEqual(db.audits, []);
    });
  }
});

test("verification imports stay separate, append on graph replay, and reject reused run IDs", async () => {
  const { graph, report, sha256, versionId } = await fixture();
  const db = new MemoryD1({
    artifactId: "artifact:paper-route",
    sha256,
    mediaType: "text/plain",
    sourceLocator: "paper.txt",
    artifactType: "paper",
  });
  bindings.DB = db;

  const first = await POST(request(envelope(graph, versionId, report)));
  assert.equal(first.status, 201);
  const firstBody = await first.json();
  assert.equal(firstBody.duplicate, false);
  assert.deepEqual(firstBody.snapshot.machineVerification, {
    status: "assessed",
    runCount: 1,
    limitationCount: 0,
  });
  assert.equal(db.snapshots.size, 1);
  assert.equal(db.verifications.size, 1);
  assert.equal(db.snapshotIdentityQueries, 1);
  assert.equal(db.verificationIdentityQueries, 1);
  assert.deepEqual(JSON.parse([...db.snapshots.values()][0].graphJson).assessments, []);
  assert.deepEqual(db.audits.sort(), ["evidence_graph.imported", "verification.imported"]);

  const replay = await POST(request(envelope(graph, versionId, report)));
  const replayBody = await replay.json();
  assert.equal(replay.status, 200);
  assert.equal(replayBody.duplicate, true);
  assert.equal(replayBody.verification.duplicate, true);
  assert.equal(db.verifications.size, 1);

  const revisedReport = { ...report, warnings: ["Independent rerun used a stricter proxy criterion."] };
  const appended = await POST(request(envelope(graph, versionId, revisedReport, "verification:run:2")));
  const appendedBody = await appended.json();
  assert.equal(appended.status, 201);
  assert.equal(appendedBody.duplicate, true);
  assert.equal(appendedBody.verification.duplicate, false);
  assert.deepEqual(appendedBody.snapshot.machineVerification, {
    status: "assessed",
    runCount: 2,
    limitationCount: 0,
  });
  assert.equal(db.snapshots.size, 1);
  assert.equal(db.verifications.size, 2);

  const conflict = await POST(request(envelope(graph, versionId, revisedReport, "verification:run:1")));
  assert.equal(conflict.status, 409);
  assert.equal((await conflict.json()).error.code, "VERIFIER_RUN_REUSED");
});

test("verification graph hash must match the immutable extraction graph", async () => {
  const { graph, report, sha256, versionId } = await fixture();
  const db = new MemoryD1({
    artifactId: "artifact:paper-route",
    sha256,
    mediaType: "text/plain",
    sourceLocator: "paper.txt",
    artifactType: "paper",
  });
  bindings.DB = db;
  const forged = { ...report, graph_sha256: "b".repeat(64) };

  const response = await POST(request(envelope(graph, versionId, forged)));
  assert.equal(response.status, 422);
  assert.equal((await response.json()).error.code, "VERIFICATION_GRAPH_MISMATCH");
  assert.equal(db.snapshots.size, 0);
  assert.equal(db.verifications.size, 0);
});

test("extraction graph cannot smuggle verifier assessments or snapshots", async () => {
  const { graph, report, sha256, versionId } = await fixture();
  const db = new MemoryD1({
    artifactId: "artifact:paper-route",
    sha256,
    mediaType: "text/plain",
    sourceLocator: "paper.txt",
    artifactType: "paper",
  });
  bindings.DB = db;
  const verifiedGraph = await attachCompilerVerificationReport(graph, report, {
    created_at: "2026-08-15T00:00:00Z",
  });
  const extractionOnlyEnvelope = {
    graph: verifiedGraph,
    root_artifact_version_id: versionId,
    extraction: { run_id: "extraction:run:1", model: "gpt-5.6-luna", prompt_version: "compiler-extractor/1.0.0" },
  };

  const embeddedAssessment = await POST(request(extractionOnlyEnvelope));
  assert.equal(embeddedAssessment.status, 422);
  assert.equal((await embeddedAssessment.json()).error.code, "VERIFIER_DATA_IN_EXTRACTION_GRAPH");

  const verifierSnapshot = await createSnapshot(graph, {
    parent_snapshot_id: null,
    created_at: "2026-08-15T00:00:00Z",
    generator: { source: "verifier", name: "fixture", version: "1" },
  });
  const embeddedSnapshot = await POST(request({
    ...extractionOnlyEnvelope,
    graph: { ...graph, snapshots: [verifierSnapshot] },
  }));
  assert.equal(embeddedSnapshot.status, 422);
  assert.equal((await embeddedSnapshot.json()).error.code, "VERIFIER_DATA_IN_EXTRACTION_GRAPH");
  assert.equal(db.snapshots.size, 0);
  assert.equal(db.verifications.size, 0);
});

test("graph artifact type and non-null locator must match stored provenance", async () => {
  const { graph, report, sha256, versionId } = await fixture();
  const db = new MemoryD1({
    artifactId: "artifact:paper-route",
    sha256,
    mediaType: "text/plain",
    sourceLocator: "different-source.txt",
    artifactType: "paper",
  });
  bindings.DB = db;

  const locatorMismatch = await POST(request(envelope(graph, versionId, report)));
  assert.equal(locatorMismatch.status, 422);
  assert.equal((await locatorMismatch.json()).error.code, "ARTIFACT_VERSION_MISMATCH");

  db.artifact.sourceLocator = "paper.txt";
  db.artifact.artifactType = "table";
  const typeMismatch = await POST(request(envelope(graph, versionId, report)));
  assert.equal(typeMismatch.status, 422);
  assert.equal((await typeMismatch.json()).error.code, "ARTIFACT_VERSION_MISMATCH");
  assert.equal(db.snapshots.size, 0);
});

test("standalone imports reject multi-source graphs before any provenance I/O", async () => {
  const { graph, report, sha256, versionId } = await fixture();
  const stored = new Map([[versionId, {
    artifactId: "artifact:paper-route", sha256, mediaType: "text/plain",
    sourceLocator: "paper.txt", artifactType: "paper",
  }]]);
  for (let index = 1; index <= 64; index += 1) {
    const supplementSha = index.toString(16).padStart(64, "0");
    const supplementArtifactId = `artifact:supplement:${index}`;
    const supplementVersionId = `artifact-version:sha256:${supplementSha}`;
    const locator = `supplement-${index}.pdf`;
    graph.artifacts.push({
      kind: "artifact", id: supplementArtifactId,
      canonical_name: `Supplement ${index}`, artifact_type: "paper",
    });
    graph.artifact_versions.push({
      kind: "artifact_version", id: supplementVersionId,
      artifact_id: supplementArtifactId, version: "1", locator,
      sha256: supplementSha, media_type: "application/pdf",
    });
    graph.anchors.push({
      ...structuredClone(graph.anchors[0]),
      id: `anchor:supplement:${index}`,
      artifact_version_id: supplementVersionId,
      quote: null,
      quote_sha256: null,
    });
    stored.set(supplementVersionId, {
      artifactId: supplementArtifactId, sha256: supplementSha,
      mediaType: "application/pdf", sourceLocator: locator, artifactType: "paper",
    });
  }
  const db = new MemoryD1(stored);
  bindings.DB = db;

  const response = await POST(request(envelope(graph, versionId, {
    ...report,
    graph_sha256: await hashEvidenceGraph(graph),
  })));

  assert.equal(response.status, 422);
  assert.equal((await response.json()).error.code, "MULTI_SOURCE_GRAPH_UNSUPPORTED");
  assert.equal(db.artifactVersionQueries, 0);
  assert.equal(db.snapshots.size, 0);
});

test("citation source is atomic, replay-idempotent, and immutable by hash/version", async (t) => {
  await t.test("the source manifest, bibliography, and mention share the graph batch", async () => {
    const { graph, report, sha256, versionId } = await fixture();
    const db = new MemoryD1(artifactRecord(sha256));
    bindings.DB = db;
    const payload = await compilerEnvelope(graph, versionId, report, "qualified");
    const source = citationSourceFixture(versionId);
    const imported = await parseEvidenceImportEnvelope(payload, "2026-08-24T00:00:00.000Z", source);

    const first = await persistEvidenceImport(imported, "test@example.com");
    const replay = await persistEvidenceImport(imported, "test@example.com");

    assert.equal(first.status, 201);
    assert.equal(replay.status, 200);
    assert.equal(replay.body.duplicate, true);
    assert.equal(db.snapshots.size, 1);
    assert.equal(db.citationSourceManifests.size, 1);
    assert.equal(db.bibliographicReferences.size, 1);
    assert.equal(db.citationMentions.size, 1);
    assert.equal([...db.citationSourceManifests.values()][0].sourceSha256, imported.citationSource.sourceSha256);

    const conflicting = await parseEvidenceImportEnvelope(
      payload,
      "2026-08-24T00:00:00.000Z",
      citationSourceFixture(versionId, "Smith (2024). Materially changed citation text. doi:10.1234/example"),
    );
    await assert.rejects(
      () => persistEvidenceImport(conflicting, "test@example.com"),
      (error) => error?.status === 409 && error?.code === "CITATION_SOURCE_CONFLICT",
    );

    [...db.citationSourceManifests.values()][0].extractorVersion = "citation-source-extractor/0.9";
    await assert.rejects(
      () => persistEvidenceImport(imported, "test@example.com"),
      (error) => error?.status === 409 && error?.code === "CITATION_SOURCE_VERSION_MISMATCH",
    );
  });

  await t.test("a child insert failure rolls the snapshot and all source rows back", async () => {
    const { graph, report, sha256, versionId } = await fixture();
    const db = new MemoryD1(artifactRecord(sha256));
    db.failCitationMentionInsert = true;
    bindings.DB = db;
    const imported = await parseEvidenceImportEnvelope(
      await compilerEnvelope(graph, versionId, report, "qualified"),
      "2026-08-24T00:00:00.000Z",
      citationSourceFixture(versionId),
    );

    await assert.rejects(
      () => persistEvidenceImport(imported, "test@example.com"),
      /injected citation mention failure/,
    );
    assert.equal(db.snapshots.size, 0);
    assert.equal(db.citationSourceManifests.size, 0);
    assert.equal(db.bibliographicReferences.size, 0);
    assert.equal(db.citationMentions.size, 0);
    assert.deepEqual(db.audits, []);
  });
});
