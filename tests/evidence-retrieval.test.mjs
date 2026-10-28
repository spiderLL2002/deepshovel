import assert from "node:assert/strict";
import fs from "node:fs";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";

import {
  EVIDENCE_GRAPH_SCHEMA_VERSION,
  createAtomicClaim,
  sha256Hex,
} from "../lib/evidence-graph/index.ts";
import {
  EVIDENCE_RETRIEVAL_INDEX_VERSION,
  EVIDENCE_RETRIEVAL_SEARCH_PROTOCOL_VERSION,
  buildEvidenceRetrievalAssessments,
  buildEvidenceRetrievalProjection,
  evidenceRetrievalProjectionStatements,
  loadActiveEvidenceRetrievalManifests,
  loadEvidenceRetrievalDocumentsByRefs,
  loadEvidenceRetrievalIndexBundles,
  loadEvidenceRetrievalSnapshotBundles,
  loadLatestEvidenceRetrievalAssessmentsByRefs,
  parseEvidenceRetrievalDocumentDto,
  parseEvidenceRetrievalManifestDto,
  parseEvidenceSearchRequest,
  toEvidenceRetrievalDocumentDto,
  toEvidenceRetrievalIndexRequestDto,
  toEvidenceRetrievalSearchRequestDto,
} from "../lib/evidence-retrieval/index.ts";

const PROJECT_ID = "DEEPSHOVEL-PROJECT";
const SNAPSHOT_ID = `snapshot:sha256:${"a".repeat(64)}`;
const CLAIM_ID = `claim:sha256:${"b".repeat(64)}`;
const QUERY_PLAN_HANDOFF = `v1.derived-auth-v1.${Buffer.from('{"plan":"preview"}').toString("base64url")}.${"s".repeat(43)}`;
const CLARIFICATION_TOKEN = `c2.derived-auth-v1.${Buffer.from('{"choices":2}').toString("base64url")}.${"s".repeat(43)}`;

test("final candidate boundary accepts thirty refs and rejects thirty-one", () => {
  const refs = Array.from({ length: 31 }, (_, i) => ({
    snapshot_id: SNAPSHOT_ID,
    claim_id: `claim:sha256:${i.toString(16).padStart(64, "0")}`,
  }));
  const request = { query: "evidence", stage: "ai_rerank", limit: 30 };
  assert.doesNotThrow(() => parseEvidenceSearchRequest({ ...request, candidate_refs: refs.slice(0, 30) }));
  assert.throws(() => parseEvidenceSearchRequest({ ...request, candidate_refs: refs }), /at most 30/u);
});

test("2.3 is explicit and preserves signed clarification selection across preview and final", () => {
  const selection = { clarification_token: CLARIFICATION_TOKEN, option_id: "option_1" };
  for (const stage of ["preview", "ai_rerank"]) {
    const parsed = parseEvidenceSearchRequest({
      query: "Which one?", stage, search_protocol: "evidence-retrieval/2.3",
      clarification_selection: selection, limit: 50,
      ...(stage === "ai_rerank" ? { query_plan_handoff: QUERY_PLAN_HANDOFF } : {}),
    });
    const dto = toEvidenceRetrievalSearchRequestDto(PROJECT_ID, {
      request: parsed, queryText: parsed.query, queryDocument: null, manifests: [],
    });
    assert.equal(dto.schema_version, "evidence-retrieval/2.3");
    assert.equal(dto.query, "Which one?");
    assert.equal(dto.filters.limit, 50);
    assert.deepEqual(dto.clarification_selection, selection);
    assert.equal(dto.query_plan_handoff, stage === "preview" ? undefined : QUERY_PLAN_HANDOFF);
  }
  const legacy = parseEvidenceSearchRequest({ query: "Which one?", stage: "preview" });
  assert.equal(Object.hasOwn(legacy, "searchProtocol"), false);
  assert.equal(Object.hasOwn(legacy, "clarificationSelection"), false);
});

test("public clarification transport is closed, bounded and unavailable without 2.3", () => {
  const base = { query: "Which one?", stage: "preview", search_protocol: "evidence-retrieval/2.3" };
  const selection = { clarification_token: CLARIFICATION_TOKEN, option_id: "option_1" };
  for (const invalid of [
    null,
    { ...selection, extra: true },
    { ...selection, option_id: "" },
    { ...selection, option_id: "x".repeat(33) },
    { ...selection, option_id: "bad.id" },
    { ...selection, clarification_token: QUERY_PLAN_HANDOFF },
    { ...selection, clarification_token: CLARIFICATION_TOKEN + ".extra" },
    { ...selection, clarification_token: "c2." + "x".repeat(700_000) },
    { ...selection, clarification_token: `c1.${CLARIFICATION_TOKEN.slice(3)}` },
  ]) assert.throws(() => parseEvidenceSearchRequest({ ...base, clarification_selection: invalid }));
  assert.throws(() => parseEvidenceSearchRequest({ query: "Which one?", clarification_selection: selection }));
  assert.throws(() => parseEvidenceSearchRequest({ ...base, search_protocol: "evidence-retrieval/2.1" }));
  assert.throws(() => parseEvidenceSearchRequest({ ...base, search_protocol: "evidence-retrieval/2.2" }));
  assert.throws(() => parseEvidenceSearchRequest({ ...base, stage: "ai_rerank" }));
});

function scope(overrides = {}) {
  return {
    location: ["Basin X"],
    time: { label: "Late Permian", start: 251, end: 252, unit: "Ma" },
    formation: ["Formation Y"],
    sample_type: ["shale"],
    method: ["ICP-MS"],
    condition: ["anoxic"],
    measurement: ["Hg concentration"],
    uncertainty: "analytical uncertainty reported",
    entities: ["Hg", "volcanism"],
    ...overrides,
  };
}

async function graphFixture({ claimCount = 2, quote = true, directClaimAnchor = true } = {}) {
  const exactQuote = "Using inductively coupled plasma mass spectrometry (ICP-MS), the authors conclude that Hg anomalies accompany volcanism.";
  const anchorId = "anchor:retrieval:exact";
  const claims = await Promise.all(Array.from({ length: claimCount }, (_, index) => createAtomicClaim({
    proposition: index === 0
      ? "Mercury anomalies accompany coeval volcanic activity."
      : `Mercury proxy ${index} records coeval volcanic activity.`,
    source_id: `claim:source:${index}`,
    scope: scope(),
    qualifiers: ["within analytical uncertainty"],
    anchor_ids: directClaimAnchor ? [anchorId] : [],
    claim_type: "correlational",
  })));
  const rootSha = "a".repeat(64);
  const rootVersionId = `artifact-version:sha256:${rootSha}`;
  const anchors = quote ? [{
    kind: "source_anchor",
    id: anchorId,
    artifact_version_id: rootVersionId,
    anchor_type: "text",
    selector: {
      page: 3,
      section: "Results",
      paragraph: 2,
      table: null,
      row_start: null,
      row_end: null,
      row_range_label: null,
      column_names: [],
      figure: null,
      series: null,
      char_start: 40,
      char_end: 165,
      region: null,
      region_label: null,
      dataset_path: null,
    },
    quote: exactQuote,
    quote_sha256: await sha256Hex(exactQuote),
  }] : [{
    kind: "source_anchor",
    id: anchorId,
    artifact_version_id: rootVersionId,
    anchor_type: "text",
    selector: {
      page: 3,
      section: "Results",
      paragraph: 2,
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
  }];
  const graph = {
    schema_version: EVIDENCE_GRAPH_SCHEMA_VERSION,
    paper_id: "paper:retrieval-fixture",
    artifacts: [{
      kind: "artifact",
      id: "artifact:paper-retrieval",
      canonical_name: "Mercury and Volcanism",
      artifact_type: "paper",
    }],
    artifact_versions: [{
      kind: "artifact_version",
      id: rootVersionId,
      artifact_id: "artifact:paper-retrieval",
      version: "v1",
      locator: "paper.pdf",
      sha256: rootSha,
      media_type: "application/pdf",
    }],
    anchors,
    visual_evidence: [],
    observations: claims.map((_, index) => ({
      kind: "observation",
      id: `observation:retrieval:${index}`,
      statement: `Hg rises with the volcanic proxy in interval ${String.fromCharCode(65 + index)}.`,
      observation_type: "correlation",
      anchor_ids: [anchorId],
      scope: scope(),
      value: null,
    })),
    assumptions: [],
    reasoning: claims.map((_, index) => ({
      kind: "reasoning",
      id: `reasoning:retrieval:${index}`,
      statement: `The paired proxy pattern ${String.fromCharCode(65 + index)} links Hg to volcanism.`,
      reasoning_type: "synthesis",
      anchor_ids: [],
      scope: scope(),
      limitations: [],
    })),
    claims,
    edges: claims.flatMap((claim, index) => [
      {
        kind: "edge",
        id: `edge:ground:${index}`,
        relation: "grounds",
        source_id: anchorId,
        target_id: `observation:retrieval:${index}`,
        rationale: null,
        confidence: 1,
        scope_overlap: null,
      },
      {
        kind: "edge",
        id: `edge:premise:${index}`,
        relation: "premise_for",
        source_id: `observation:retrieval:${index}`,
        target_id: `reasoning:retrieval:${index}`,
        rationale: null,
        confidence: 0.9,
        scope_overlap: null,
      },
      {
        kind: "edge",
        id: `edge:infer:${index}`,
        relation: "infers",
        source_id: `reasoning:retrieval:${index}`,
        target_id: claim.id,
        rationale: null,
        confidence: 0.8,
        scope_overlap: null,
      },
    ]),
    assessments: [],
    snapshots: [],
  };
  if (claims.length > 1) {
    graph.edges.push({
      kind: "edge",
      id: "edge:claim:supports",
      relation: "supports",
      source_id: claims[0].id,
      target_id: claims[1].id,
      rationale: "The two terminal conclusions are explicitly compared.",
      confidence: 0.75,
      scope_overlap: "equivalent",
    });
  }
  return { graph, rootVersionId, rootSha, exactQuote };
}

function projectionMetadata(rootVersionId, graphSha = "b".repeat(64), createdAt = "2026-08-17T10:00:00.000Z") {
  return {
    snapshotId: `snapshot:sha256:${graphSha}`,
    rootArtifactVersionId: rootVersionId,
    graphSha256: graphSha,
    extractorPromptVersion: "compiler-extractor/1.0.0",
    createdAt,
  };
}

test("v2 projection is deterministic, Claim-first, exact-quote anchored, and relation preserving", async () => {
  const { graph, rootVersionId, exactQuote } = await graphFixture();
  const metadata = projectionMetadata(rootVersionId);
  const first = buildEvidenceRetrievalProjection(graph, metadata);
  const second = buildEvidenceRetrievalProjection(structuredClone(graph), metadata);
  assert.deepEqual(second, first);
  assert.equal(first.manifest.indexVersion, EVIDENCE_RETRIEVAL_INDEX_VERSION);
  assert.equal(first.manifest.paperFamilyId, "artifact:paper-retrieval");
  assert.equal(first.manifest.rootArtifactVersionId, rootVersionId);
  assert.equal(first.manifest.documentCount, 2);

  const document = first.documents[0];
  assert.equal(document.originalClaim, exactQuote);
  assert.equal(document.canonicalClaim, graph.claims[0].proposition);
  assert.equal(document.scopedClaimId, graph.claims[0].id);
  assert.equal(document.paperTitle, "Mercury and Volcanism");
  assert.equal(document.attribution, "authors_conclusion");
  assert.equal(document.rhetoricalRole, "terminal_conclusion");
  assert.deepEqual(document.scope, graph.claims[0].scope);
  assert.match(document.evidenceContext, /\[source\].*ICP-MS/u);
  assert.match(document.evidenceContext, /\[observation\]/u);
  assert.match(document.evidenceContext, /\[reasoning\]/u);
  assert.ok(document.keyTerms.includes("ICP-MS"));
  assert.deepEqual(document.abbreviations.find((item) => item.shortForm === "ICP-MS"), {
    shortForm: "ICP-MS",
    longForm: "Using inductively coupled plasma mass spectrometry",
  });
  assert.deepEqual(document.shortestEvidencePath.nodes.map((node) => node.kind), [
    "artifact", "artifact_version", "source_anchor", "observation", "reasoning", "atomic_claim",
  ]);
  assert.deepEqual(document.shortestEvidencePath.links.map((link) => link.relation), [
    "versions", "locates", "grounds", "premise_for", "infers",
  ]);
  assert.ok(document.pathQuality > 0.9 && document.pathQuality <= 1);
  assert.equal(document.evidenceAnchors[0].quote, exactQuote);
  assert.deepEqual(document.explicitRelations[0], {
    edgeId: "edge:claim:supports",
    sourceClaimId: graph.claims[0].id,
    targetClaimId: graph.claims[1].id,
    otherClaimId: graph.claims[1].id,
    relation: "supports",
    direction: "outgoing",
    confidence: 0.75,
    scopeOverlap: "equivalent",
  });
  const dto = toEvidenceRetrievalDocumentDto(document);
  assert.equal(dto.original_claim, exactQuote);
  assert.equal(dto.explicit_relations[0].direction, "outgoing");
  assert.equal(dto.shortest_evidence_path.links.at(-1).to_id, graph.claims[0].id);
});

test("original_claim never falls back to the generated canonical proposition", async () => {
  const { graph, rootVersionId } = await graphFixture({ claimCount: 1, quote: false, directClaimAnchor: false });
  const projection = buildEvidenceRetrievalProjection(graph, projectionMetadata(rootVersionId));
  assert.equal(projection.documents[0].originalClaim, null);
  assert.notEqual(projection.documents[0].originalClaim, projection.documents[0].canonicalClaim);
});

test("derived context and abbreviation fields use deterministic service-safe caps", async () => {
  const { graph, rootVersionId } = await graphFixture({ claimCount: 1 });
  const abbreviations = Array.from({ length: 40 }, (_, index) => (
    `${String.fromCharCode(65 + Math.floor(index / 26))}${String.fromCharCode(65 + (index % 26))}`
  ));
  graph.anchors[0].quote = `${abbreviations.join(" ")} ${"x".repeat(15_500)}`;
  graph.anchors[0].quote_sha256 = await sha256Hex(graph.anchors[0].quote);
  graph.observations[0].statement = "y".repeat(8_000);
  graph.reasoning[0].statement = "z".repeat(8_000);
  const projection = buildEvidenceRetrievalProjection(graph, projectionMetadata(rootVersionId));
  assert.equal(Array.from(projection.documents[0].evidenceContext).length, 16_000);
  assert.equal(projection.documents[0].abbreviations.length, 32);
  assert.deepEqual(projection.documents[0].abbreviations.map((item) => item.shortForm), abbreviations.slice(0, 32));
});

test("strict DTO preserves exact source text beyond the derived-context cap", async () => {
  const { graph, rootVersionId } = await graphFixture({ claimCount: 1 });
  const document = buildEvidenceRetrievalProjection(graph, projectionMetadata(rootVersionId)).documents[0];
  const dto = toEvidenceRetrievalDocumentDto(document);
  const exact = `exact source passage ${"q".repeat(20_000)}`;
  dto.original_claim = exact;
  dto.evidence_anchors[0].quote = exact;
  assert.equal(parseEvidenceRetrievalDocumentDto(dto).original_claim, exact);
});

test("a source region the graph validator accepts is accepted by the retrieval DTO", async () => {
  const { graph, rootVersionId } = await graphFixture({ claimCount: 1 });
  const dto = toEvidenceRetrievalDocumentDto(buildEvidenceRetrievalProjection(graph, projectionMetadata(rootVersionId)).documents[0]);
  // Float noise past the edge: the graph validators allow up to 1.000001.
  dto.evidence_anchors[0].region = { x: 0.6, y: 0.2, width: 0.4000005, height: 0.3, unit: "normalized" };
  assert.equal(parseEvidenceRetrievalDocumentDto(dto).evidence_anchors[0].region.width, 0.4000005);
  dto.evidence_anchors[0].region = { x: 0.6, y: 0.2, width: 0.41, height: 0.3, unit: "normalized" };
  assert.throws(() => parseEvidenceRetrievalDocumentDto(dto), /normalized coordinates must fit/u);
});

test("valid zero-Claim snapshots still create complete freshness manifests", async () => {
  const { graph, rootVersionId } = await graphFixture({ claimCount: 1 });
  graph.observations = [];
  graph.reasoning = [];
  graph.claims = [];
  graph.edges = [];
  const projection = buildEvidenceRetrievalProjection(graph, projectionMetadata(rootVersionId));
  assert.deepEqual(projection.documents, []);
  assert.equal(projection.manifest.documentCount, 0);
});

test("independent verifier assessments remain append-only documents outside extraction projection", async () => {
  const { graph } = await graphFixture({ claimCount: 1 });
  const report = {
    schema_version: "1.0",
    paper_id: graph.paper_id,
    graph_sha256: "b".repeat(64),
    verifier_model: "gpt-5.6-luna",
    assessments: [{
      id: "assessment:retrieval:1",
      claim_id: graph.claims[0].source_id,
      scoped_claim_id: graph.claims[0].scoped_claim_id,
      verdict: "qualified",
      confidence: 0.81,
      evidence_ids: [graph.observations[0].id],
      rationale: "The evidence supports the Claim under the stated Scope.",
      limitations: ["One interval"],
    }],
    warnings: [],
  };
  const assessments = buildEvidenceRetrievalAssessments(
    graph,
    `snapshot:sha256:${"b".repeat(64)}`,
    report,
    {
      verificationId: "verification:retrieval:1",
      verifierPromptVersion: "compiler-verifier/1.0.0",
      createdAt: "2026-08-17T10:01:00.000Z",
    },
  );
  assert.equal(assessments[0].claimId, graph.claims[0].id);
  assert.equal(assessments[0].scopedClaimId, graph.claims[0].id);
  assert.deepEqual(assessments[0].evidenceIds, [graph.observations[0].id]);
  assert.equal(assessments[0].verifierPromptVersion, "compiler-verifier/1.0.0");
});

test("projection persistence chunks JSON parameters but imposes no 2048-Claim business cap", async () => {
  const { graph, rootVersionId } = await graphFixture({ claimCount: 1 });
  const projection = buildEvidenceRetrievalProjection(graph, projectionMetadata(rootVersionId));
  const base = projection.documents[0];
  projection.documents = Array.from({ length: 2_101 }, (_, index) => {
    const claimId = `claim:sha256:${index.toString(16).padStart(64, "0")}`;
    return {
      ...structuredClone(base),
      claimId,
      scopedClaimId: claimId,
      explicitRelations: [],
      shortestEvidencePath: {
        ...structuredClone(base.shortestEvidencePath),
        rootClaimId: claimId,
      },
      evidenceContext: `${base.evidenceContext}\n${"x".repeat(1_000)}`,
    };
  });
  projection.manifest.documentCount = projection.documents.length;
  const prepared = [];
  const DB = {
    prepare(sql) {
      return {
        bind(...args) {
          const statement = { sql, args };
          prepared.push(statement);
          return statement;
        },
      };
    },
  };
  const statements = evidenceRetrievalProjectionStatements(DB, PROJECT_ID, projection);
  const documentStatements = prepared.filter((statement) => statement.sql.includes("evidence_retrieval_documents"));
  assert.ok(documentStatements.length > 1);
  assert.equal(statements.length, prepared.length);
  assert.ok(documentStatements.every((statement) => (
    new TextEncoder().encode(statement.args[1]).byteLength <= 512 * 1024
  )));
  const persisted = documentStatements.flatMap((statement) => JSON.parse(statement.args[1]));
  assert.equal(persisted.length, 2_101);

  const oversized = structuredClone(projection);
  const largeAnchor = {
    ...structuredClone(base.evidenceAnchors[0]),
    quote: `exact ${"q".repeat(127_990)}`,
  };
  oversized.documents = [{
    ...structuredClone(base),
    evidenceAnchors: Array.from({ length: 5 }, (_, index) => ({
      ...structuredClone(largeAnchor),
      anchorId: `anchor:oversized:${index}`,
    })),
  }];
  oversized.manifest.documentCount = 1;
  assert.throws(
    () => evidenceRetrievalProjectionStatements(DB, PROJECT_ID, oversized),
    /exceeds the D1 JSON boundary/u,
  );
});

test("public request and service DTO retain every explicit query constraint", () => {
  assert.throws(() => parseEvidenceSearchRequest({ query: "Hg", unknown: true }), /unknown fields/u);
  assert.throws(() => parseEvidenceSearchRequest({ query: "Hg", must_terms: ["Hg"], exclude_terms: ["hg"] }), /must not exclude/u);
  const request = parseEvidenceSearchRequest({
    query: "Does Hg support volcanism?",
    intent: "supporting_evidence",
    corpus: "same_paper",
    paper_family_ids: ["artifact:paper-retrieval"],
    must_terms: ["Hg"],
    exclude_terms: ["background"],
    scope: { method: ["ICP-MS"], entities: ["Hg"] },
    claim_types: ["correlational"],
    polarity: "affirmed",
    limit: 7,
  });
  const dto = toEvidenceRetrievalSearchRequestDto(PROJECT_ID, {
    request,
    queryText: request.query,
    queryDocument: null,
    manifests: [],
  });
  assert.equal(request.queryPlanHandoff, null);
  assert.equal(dto.schema_version, EVIDENCE_RETRIEVAL_INDEX_VERSION);
  assert.equal(Object.hasOwn(dto, "query_plan_handoff"), false);
  assert.equal(dto.query, request.query);
  assert.equal(dto.intent, "supporting_evidence");
  assert.deepEqual(dto.must_terms, ["Hg"]);
  assert.deepEqual(dto.scope, request.scope);
  assert.deepEqual(dto.claim_types, ["correlational"]);
  assert.deepEqual(dto.filters, {
    corpus: "same_paper",
    paper_ids: [],
    paper_family_ids: ["artifact:paper-retrieval"],
    active_snapshot_ids: null,
    limit: 7,
  });
  for (const emptyScope of [{}, { location: [], entities: [], time: null, uncertainty: null }]) {
    assert.throws(
      () => parseEvidenceSearchRequest({ query: "mercury", scope: emptyScope }),
      /scope.*non-empty constraint/u,
    );
  }
});

test("search protocol upgrades preview and handoff-bound final requests without changing the 2.0 index", () => {
  const preview = parseEvidenceSearchRequest({
    stage: "preview",
    query: "Does Hg support volcanism?",
    limit: 12,
  });
  assert.equal(preview.queryPlanHandoff, null);
  const previewDto = toEvidenceRetrievalSearchRequestDto(PROJECT_ID, {
    request: preview,
    queryText: preview.query,
    queryDocument: null,
    manifests: [],
  });
  assert.equal(EVIDENCE_RETRIEVAL_INDEX_VERSION, "evidence-retrieval/2.0");
  assert.equal(previewDto.schema_version, EVIDENCE_RETRIEVAL_SEARCH_PROTOCOL_VERSION);
  assert.equal(Object.hasOwn(previewDto, "query_plan_handoff"), false);

  const finalRequest = parseEvidenceSearchRequest({
    stage: "ai_rerank",
    query: "Does Hg support volcanism?",
    candidate_refs: [{ snapshot_id: SNAPSHOT_ID, claim_id: CLAIM_ID }],
    query_plan_handoff: QUERY_PLAN_HANDOFF,
  });
  assert.equal(finalRequest.queryPlanHandoff, QUERY_PLAN_HANDOFF);
  const finalDto = toEvidenceRetrievalSearchRequestDto(PROJECT_ID, {
    request: finalRequest,
    queryText: finalRequest.query,
    queryDocument: null,
    manifests: [],
  });
  assert.equal(finalDto.schema_version, EVIDENCE_RETRIEVAL_SEARCH_PROTOCOL_VERSION);
  assert.equal(finalDto.query_plan_handoff, QUERY_PLAN_HANDOFF);

  assert.throws(
    () => parseEvidenceSearchRequest({
      stage: "preview",
      query: "Hg",
      query_plan_handoff: QUERY_PLAN_HANDOFF,
    }),
    /preview requests cannot provide a QueryPlan handoff/u,
  );
  for (const invalidHandoff of [
    `v1.derived-auth-v1.payload.${"s".repeat(42)}`,
    `v1.derived-auth-v1.payload.${"s".repeat(42)}=`,
    `v1.derived.auth.payload.${"s".repeat(43)}`,
    `v1.derived-auth-v1.pay+load.${"s".repeat(43)}`,
    `v1.derived-auth-v1.${"p".repeat(699_901)}.${"s".repeat(43)}`,
  ]) {
    assert.throws(
      () => parseEvidenceSearchRequest({
        stage: "ai_rerank",
        query: "Hg",
        candidate_refs: [{ snapshot_id: SNAPSHOT_ID, claim_id: CLAIM_ID }],
        query_plan_handoff: invalidHandoff,
      }),
      /query_plan_handoff/u,
    );
  }
});

test("manifest timestamps accept minute precision but reject impossible calendar dates", () => {
  const graphSha = "a".repeat(64);
  const manifest = {
    index_version: EVIDENCE_RETRIEVAL_INDEX_VERSION,
    snapshot_id: `snapshot:sha256:${graphSha}`,
    paper_id: "paper:timestamp-fixture",
    paper_family_id: "artifact:timestamp-fixture",
    root_artifact_version_id: `artifact-version:sha256:${"b".repeat(64)}`,
    root_artifact_sha256: "b".repeat(64),
    graph_sha256: graphSha,
    graph_schema_version: EVIDENCE_GRAPH_SCHEMA_VERSION,
    extractor_prompt_version: "compiler-extractor/1.0.0",
    document_count: 0,
    created_at: "2026-08-18T10:00Z",
  };
  assert.equal(parseEvidenceRetrievalManifestDto(manifest), manifest);
  assert.throws(
    () => parseEvidenceRetrievalManifestDto({ ...manifest, created_at: "2026-02-30T00:00:00Z" }),
    /ISO timestamp/u,
  );
});

class SqliteStatement {
  args = [];
  constructor(database, sql) { this.database = database; this.sql = sql; }
  bind(...args) { this.args = args; return this; }
  async first() { return this.database.prepare(this.sql).get(...this.args) ?? null; }
  async all() { return { results: this.database.prepare(this.sql).all(...this.args) }; }
}

class SqliteD1 {
  queries = [];
  constructor(database) { this.database = database; }
  prepare(sql) {
    this.queries.push(sql);
    return new SqliteStatement(this.database, sql);
  }
}

function applyMigration(database, name) {
  const sql = fs.readFileSync(new URL(`../drizzle/${name}`, import.meta.url), "utf8");
  for (const statement of sql.split("--> statement-breakpoint")) {
    if (statement.trim()) database.exec(statement);
  }
}

function migrateThroughV1(database) {
  const names = fs.readdirSync(new URL("../drizzle/", import.meta.url))
    .filter((name) => /^000[0-8]_.+\.sql$/u.test(name)).sort();
  for (const name of names) applyMigration(database, name);
}

test("0009 backfills strict v2 DTO JSON, preserves zero-Claim freshness, then drops v1 tables", async () => {
  const database = new DatabaseSync(":memory:");
  try {
    database.exec("PRAGMA foreign_keys = ON");
    migrateThroughV1(database);
    const { graph, rootVersionId, rootSha, exactQuote } = await graphFixture({ claimCount: 1 });
    const oldGraphSha = "b".repeat(64);
    const oldSnapshotId = `snapshot:sha256:${oldGraphSha}`;
    const oldProjection = buildEvidenceRetrievalProjection(graph, projectionMetadata(rootVersionId, oldGraphSha));
    const pathDto = toEvidenceRetrievalDocumentDto(oldProjection.documents[0]).shortest_evidence_path;

    database.prepare("INSERT INTO artifacts (id,project_id,preferred_name,artifact_type,created_by) VALUES (?,?,?,?,?)")
      .run("artifact:paper-retrieval", PROJECT_ID, "Fallback title", "paper", "migration@test");
    database.prepare(`INSERT INTO artifact_versions
      (id,artifact_id,version_number,sha256,source_locator,media_type,original_file_name,
       byte_size,object_key,client_request_id,request_hash,created_by)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`).run(
      rootVersionId, "artifact:paper-retrieval", 1, rootSha, "upload://paper.pdf", "application/pdf",
      "paper.pdf", 100, `artifacts/${rootSha}`, "request:v1", "1".repeat(64), "migration@test",
    );
    database.prepare(`INSERT INTO evidence_graph_snapshots
      (id,project_id,root_artifact_version_id,schema_version,graph_sha256,graph_json,counts_json,
       extractor_run_id,extractor_model,extractor_prompt_version,created_by,created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`).run(
      oldSnapshotId, PROJECT_ID, rootVersionId, EVIDENCE_GRAPH_SCHEMA_VERSION, oldGraphSha,
      JSON.stringify(graph), "{}", "extractor-run:v1", "gpt-5.6-luna",
      "compiler-extractor/1.0.0", "migration@test", "2026-08-16T00:00:00.000Z",
    );
    database.prepare(`INSERT INTO evidence_claim_index
      (project_id,snapshot_id,paper_id,claim_id,source_claim_id,proposition_key,proposition,
       claim_type,polarity,modality,scope_json,document_length,path_json,path_depth,path_quality,index_version)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
      PROJECT_ID, oldSnapshotId, graph.paper_id, graph.claims[0].id, graph.claims[0].source_id,
      graph.claims[0].proposition_key, graph.claims[0].proposition, graph.claims[0].claim_type,
      graph.claims[0].polarity, graph.claims[0].modality, JSON.stringify(graph.claims[0].scope),
      12, JSON.stringify(pathDto), pathDto.depth, 0.95, "evidence-retrieval/1.0",
    );
    database.prepare(`INSERT INTO evidence_claim_terms
      (project_id,snapshot_id,claim_id,term,field,frequency,field_weight) VALUES (?,?,?,?,?,?,?)`)
      .run(PROJECT_ID, oldSnapshotId, graph.claims[0].id, "mercury", "proposition", 1, 5);
    database.prepare(`INSERT INTO evidence_search_manifests
      (snapshot_id,project_id,index_version,claim_count,term_count,relation_count,created_at)
      VALUES (?,?,?,?,?,?,?)`).run(oldSnapshotId, PROJECT_ID, "evidence-retrieval/1.0", 1, 1, 0, "2026-08-18T10:00:00+02:00");

    const verificationId = "verification:legacy:1";
    const report = {
      schema_version: "1.0",
      paper_id: graph.paper_id,
      graph_sha256: oldGraphSha,
      verifier_model: "gpt-5.6-luna",
      assessments: [{
        id: "assessment:legacy:1",
        claim_id: graph.claims[0].source_id,
        scoped_claim_id: graph.claims[0].id,
        verdict: "supported",
        confidence: 0.9,
        evidence_ids: [graph.observations[0].id],
        rationale: "Legacy evidence supports the scoped Claim.",
        limitations: ["Legacy limitation"],
      }],
      warnings: [],
    };
    database.prepare(`INSERT INTO verification_runs
      (id,project_id,snapshot_id,verifier_run_id,verifier_model,verifier_prompt_version,
       report_sha256,report_json,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)`).run(
      verificationId, PROJECT_ID, oldSnapshotId, "verifier-run:legacy", "gpt-5.6-luna",
      "compiler-verifier/1.0.0", "e".repeat(64), JSON.stringify(report), "migration@test", "2026-08-16T00:01:00.000Z",
    );
    database.prepare(`INSERT INTO evidence_assessment_index
      (project_id,verification_id,snapshot_id,assessment_id,claim_id,verdict,confidence,evidence_count,created_at)
      VALUES (?,?,?,?,?,?,?,?,?)`).run(
      PROJECT_ID, verificationId, oldSnapshotId, "assessment:legacy:1", graph.claims[0].id,
      "supported", 0.9, 1, "2026-08-16T00:01:00.000Z",
    );

    const zeroSha = "c".repeat(64);
    const zeroVersionSha = "d".repeat(64);
    const zeroVersionId = `artifact-version:sha256:${zeroVersionSha}`;
    database.prepare(`INSERT INTO artifact_versions
      (id,artifact_id,version_number,sha256,source_locator,media_type,original_file_name,
       byte_size,object_key,client_request_id,request_hash,created_by)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`).run(
      zeroVersionId, "artifact:paper-retrieval", 2, zeroVersionSha, "upload://paper-v2.pdf", "application/pdf",
      "paper-v2.pdf", 101, `artifacts/${zeroVersionSha}`, "request:v2", "2".repeat(64), "migration@test",
    );
    const zeroGraph = { ...structuredClone(graph), claims: [], observations: [], reasoning: [], anchors: [], edges: [] };
    const zeroSnapshotId = `snapshot:sha256:${zeroSha}`;
    database.prepare(`INSERT INTO evidence_graph_snapshots
      (id,project_id,root_artifact_version_id,schema_version,graph_sha256,graph_json,counts_json,
       extractor_run_id,extractor_model,extractor_prompt_version,created_by,created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`).run(
      zeroSnapshotId, PROJECT_ID, zeroVersionId, EVIDENCE_GRAPH_SCHEMA_VERSION, zeroSha,
      JSON.stringify(zeroGraph), "{}", "extractor-run:v2", "gpt-5.6-luna",
      "compiler-extractor/1.0.0", "migration@test", "2026-08-17T00:00:00.000Z",
    );
    database.prepare(`INSERT INTO evidence_search_manifests
      (snapshot_id,project_id,index_version,claim_count,term_count,relation_count,created_at)
      VALUES (?,?,?,?,?,?,?)`).run(zeroSnapshotId, PROJECT_ID, "evidence-retrieval/1.0", 0, 0, 0, "2026-08-18T09:30:00Z");

    applyMigration(database, "0009_evidence_retrieval_v2.sql");
    const DB = new SqliteD1(database);
    const syncQueryStart = DB.queries.length;
    const syncBundles = await loadEvidenceRetrievalIndexBundles(
      DB,
      PROJECT_ID,
      [oldSnapshotId, zeroSnapshotId],
    );
    assert.equal(DB.queries.length - syncQueryStart, 2);
    assert.deepEqual(syncBundles.map((bundle) => bundle.documents.length), [1, 0]);
    assert.ok(syncBundles.every((bundle) => !Object.hasOwn(bundle, "assessments")));
    const [bundle] = await loadEvidenceRetrievalSnapshotBundles(DB, PROJECT_ID, [oldSnapshotId]);
    assert.equal(bundle.documents[0].originalClaim, exactQuote);
    assert.equal(bundle.documents[0].canonicalClaim, graph.claims[0].proposition);
    assert.deepEqual(bundle.documents[0].shortestEvidencePath.nodes.map((node) => node.kind), [
      "artifact", "artifact_version", "source_anchor", "observation", "reasoning", "atomic_claim",
    ]);
    assert.equal(bundle.assessments[0].rationale, "Legacy evidence supports the scoped Claim.");
    assert.equal(bundle.assessments[0].verifierModel, "gpt-5.6-luna");
    const indexDto = toEvidenceRetrievalIndexRequestDto(PROJECT_ID, bundle);
    assert.equal(indexDto.documents[0].index_version, EVIDENCE_RETRIEVAL_INDEX_VERSION);
    assert.ok(!Object.hasOwn(indexDto, "assessments"));

    // JSON refs must drive primary-key lookups. Reordering this join to start
    // with the project index scans the corpus once per requested Claim.
    const refs = [{ snapshotId: oldSnapshotId, claimId: graph.claims[0].id }];
    for (const hydrate of [loadEvidenceRetrievalDocumentsByRefs, loadLatestEvidenceRetrievalAssessmentsByRefs]) {
      assert.equal((await hydrate(DB, PROJECT_ID, refs)).length, 1);
      const plan = database.prepare(`EXPLAIN QUERY PLAN ${DB.queries.at(-1)}`)
        .all(JSON.stringify(refs), PROJECT_ID, EVIDENCE_RETRIEVAL_INDEX_VERSION)
        .map((row) => row.detail);
      const input = plan.findIndex((detail) => /SCAN json_each/u.test(detail));
      const lookup = plan.findIndex((detail) => /SEARCH [da] USING INDEX.*snapshot_id=\?/u.test(detail));
      assert.ok(input >= 0 && lookup > input, JSON.stringify(plan));
      assert.ok(!plan.some((detail) => /SEARCH [da].*\(project_id=\?\)/u.test(detail)), JSON.stringify(plan));
    }

    // Current reads use the transactionally maintained winner projection.
    // Check the historical migration output before installing that read model.
    const filtered = await loadActiveEvidenceRetrievalManifests(DB, PROJECT_ID, {
      paperFamilyIds: ["artifact:paper-retrieval"],
    });
    applyMigration(database, "0019_graph_active_snapshots.sql");
    const active = await loadActiveEvidenceRetrievalManifests(DB, PROJECT_ID);
    assert.deepEqual(active, filtered);
    assert.ok(!DB.queries.at(-1).includes("ROW_NUMBER"));
    assert.equal(active.length, 1);
    assert.equal(active[0].snapshotId, zeroSnapshotId);
    assert.equal(active[0].documentCount, 0);

    const mismatchedClaimId = `claim:sha256:${"f".repeat(64)}`;
    const storedDocument = database.prepare(`SELECT document_json AS documentJson
      FROM evidence_retrieval_documents WHERE snapshot_id=? LIMIT 1`).get(oldSnapshotId).documentJson;
    database.prepare(`INSERT INTO evidence_retrieval_documents
      (project_id,snapshot_id,paper_id,paper_family_id,claim_id,scoped_claim_id,
       proposition_key,document_json,index_version,created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?)`).run(
      PROJECT_ID, oldSnapshotId, graph.paper_id, "artifact:paper-retrieval",
      mismatchedClaimId, mismatchedClaimId, graph.claims[0].proposition_key,
      storedDocument, EVIDENCE_RETRIEVAL_INDEX_VERSION, "2026-08-16T00:00:00.000Z",
    );
    const storedAssessment = database.prepare(`SELECT assessment_json AS assessmentJson
      FROM evidence_retrieval_assessments WHERE snapshot_id=? LIMIT 1`).get(oldSnapshotId).assessmentJson;
    database.prepare(`INSERT INTO evidence_retrieval_assessments
      (project_id,verification_id,snapshot_id,assessment_id,claim_id,
       assessment_json,index_version,created_at) VALUES (?,?,?,?,?,?,?,?)`).run(
      PROJECT_ID, verificationId, oldSnapshotId, "assessment:corrupt-column", mismatchedClaimId,
      storedAssessment, EVIDENCE_RETRIEVAL_INDEX_VERSION, "2026-08-16T00:01:00.000Z",
    );
    const mismatchedRef = [{ snapshotId: oldSnapshotId, claimId: mismatchedClaimId }];
    await assert.rejects(
      loadEvidenceRetrievalDocumentsByRefs(DB, PROJECT_ID, mismatchedRef),
      /does not match immutable document_json/u,
    );
    await assert.rejects(
      loadEvidenceRetrievalIndexBundles(DB, PROJECT_ID, [oldSnapshotId]),
      /does not match immutable document_json/u,
    );
    await assert.rejects(
      loadLatestEvidenceRetrievalAssessmentsByRefs(DB, PROJECT_ID, mismatchedRef),
      /does not match immutable assessment_json/u,
    );
    for (const oldTable of [
      "evidence_claim_index", "evidence_claim_terms", "evidence_claim_relations",
      "evidence_assessment_index", "evidence_search_manifests",
    ]) {
      assert.equal(database.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?").get(oldTable), undefined);
    }
    assert.throws(() => database.prepare("UPDATE evidence_retrieval_documents SET document_json=document_json").run(), /immutable/u);
    assert.throws(() => database.prepare("DELETE FROM evidence_retrieval_documents").run(), /immutable/u);
  } finally {
    database.close();
  }
});
