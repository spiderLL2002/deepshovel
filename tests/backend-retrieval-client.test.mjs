import assert from "node:assert/strict";
import test from "node:test";
import { encodeActiveSnapshotIds } from "../lib/evidence-retrieval/contract.ts";

import {
  RetrievalClient,
  parseRetrievalSearchResponse,
} from "../lib/server/retrieval-client.ts";

const secret = "retrieval-test-shared-secret-0123456789";
const snapshotId = `snapshot:sha256:${"a".repeat(64)}`;
const claimId = `claim:sha256:${"b".repeat(64)}`;
const queryPlanHandoff = `v1.derived-auth-v1.${Buffer.from('{"plan":"preview"}').toString("base64url")}.${"s".repeat(43)}`;
const clarificationToken = `c2.derived-auth-v1.${Buffer.from('{"choices":2}').toString("base64url")}.${"s".repeat(43)}`;

function clarificationResponse() {
  return {
    schema_version: "evidence-retrieval/2.3", stage: "preview",
    result_status: "clarification_required", query_plan: null,
    clarification: {
      question: "Which process do you mean?",
      options: [
        { option_id: "one", label: "First process", resolved_query: "Does A increase B?" },
        { option_id: "two", label: "Second process", resolved_query: "Does C increase D?" },
      ],
      clarification_token: clarificationToken,
    },
    channels: Object.fromEntries(["lexical", "dense", "rerank", "relation"].map((name) => [
      name, { attempted: false, status: "skipped", provider: name, returned: 0 },
    ])),
    preview: [], clusters: [], warnings: [], retrieval_confidence: null,
  };
}

test("2.3 clarification is a no-plan no-handoff pre-retrieval terminal response", () => {
  const response = clarificationResponse();
  assert.deepEqual(parseRetrievalSearchResponse(response, [snapshotId]), response);
  for (const mutate of [
    (value) => { value.schema_version = "evidence-retrieval/2.1"; },
    (value) => { value.schema_version = "evidence-retrieval/2.2"; },
    (value) => { value.clarification.clarification_token = `c1.${clarificationToken.slice(3)}`; },
    (value) => { value.stage = "ai_rerank"; },
    (value) => { value.query_plan_handoff = queryPlanHandoff; },
    (value) => { value.query_plan = searchResponse().query_plan; },
    (value) => { value.preview = previewSearchResponseV21().preview; },
    (value) => { value.channels.lexical.attempted = true; },
    (value) => { value.channels.dense.returned = 1; },
    (value) => { value.retrieval_confidence = retrievalConfidence(); },
    (value) => { value.clarification.extra = "invalid"; },
    (value) => { value.clarification.options.pop(); },
    (value) => { value.clarification.options[1].option_id = "one"; },
    (value) => { value.clarification.options[1].resolved_query = value.clarification.options[0].resolved_query; },
    (value) => { value.clarification.options[0].resolved_query = "e\u0301"; },
    (value) => { value.clarification.question = "x".repeat(1_001); },
    (value) => { value.clarification.options[0].label = "x".repeat(201); },
    (value) => { value.clarification.options[0].resolved_query = "x".repeat(8_001); },
    (value) => { value.clarification.clarification_token = queryPlanHandoff; },
  ]) {
    const invalid = structuredClone(response);
    mutate(invalid);
    assert.throws(() => parseRetrievalSearchResponse(invalid, [snapshotId]));
  }
});

function jsonResponse(value, init = {}) {
  return new Response(JSON.stringify(value), {
    ...init,
    headers: { "content-type": "application/json", ...init.headers },
  });
}

function manifest() {
  return {
    index_version: "evidence-retrieval/2.0",
    snapshot_id: snapshotId,
    paper_id: "paper:fixture",
    paper_family_id: "artifact:fixture",
    root_artifact_version_id: `artifact-version:sha256:${"c".repeat(64)}`,
    root_artifact_sha256: "c".repeat(64),
    graph_sha256: "a".repeat(64),
    graph_schema_version: "scientific-evidence-graph/1.0",
    extractor_prompt_version: "compiler-extractor/1.0.0",
    document_count: 0,
    created_at: "2026-08-18T04:00:00Z",
  };
}

function state(status = "indexed") {
  return {
    snapshot_id: snapshotId,
    status,
    indexed_document_count: 0,
    document_count: 0,
    manifest_sha256: status === "missing" ? null : "d".repeat(64),
    dense_complete: status !== "missing" && status !== "degraded",
  };
}

function member() {
  return {
    claim_id: claimId,
    snapshot_id: snapshotId,
    paper_id: "paper:fixture",
    paper_family_id: "artifact:fixture",
    relation: "supports",
    relation_direction: "result_to_query",
    relation_confidence: null,
    scope_relation: "overlap",
    scores: { lexical: 3.2, dense: 0.81, rrf: 0.032, rerank: 0.91 },
  };
}

function retrievalConfidence(overrides = {}) {
  return {
    mode: "observe",
    decision: "return",
    would_abstain: null,
    applicability: "applicable",
    policy_inapplicable_reasons: [],
    query_profile: "topic",
    long_query_truncated: false,
    direct_lexical_query_tokens: 2,
    direct_lexical_overlap: 1,
    direct_lexical_coverage: 0.5,
    exact_anchor_hits: 0,
    concept_anchor_hits: 1,
    scientific_anchor_hits: 0,
    strong_signal_types: ["concept_anchor"],
    dense_observed_hits: 2,
    dense_top1: 0.81,
    dense_top2: 0.75,
    dense_margin: 0.06,
    dense_top3_mean: 0.78,
    ...overrides,
  };
}

function searchResponse() {
  return {
    schema_version: "evidence-retrieval/2.0",
    stage: "ai_rerank",
    result_status: "matches",
    query_plan: {
      query_type: "supporting_evidence",
      original_query: "A increases B",
      canonical_claim: "A increases B.",
      must_terms: ["A"],
      optional_terms: ["increase"],
      exclude_terms: ["background"],
      entities: ["A", "B"],
      polarity: "affirmed",
      claim_types: ["causal"],
      scope_constraints: null,
      planner: "deterministic",
    },
    channels: {
      lexical: { attempted: true, status: "ok", provider: "bm25f", returned: 12 },
      dense: { attempted: true, status: "ok", provider: "bge-m3", returned: 10 },
      rerank: { attempted: true, status: "ok", provider: "cross-encoder", returned: 5 },
      relation: { attempted: true, status: "ok", provider: "relation-classifier", returned: 1 },
    },
    preview: [],
    clusters: [{
      cluster_id: "cluster:fixture",
      relation: "supports",
      representative: member(),
      member_refs: [member()],
      distinct_paper_families: 1,
    }],
    warnings: [],
    retrieval_confidence: null,
  };
}

function previewSearchResponseV21() {
  const response = searchResponse();
  response.schema_version = "evidence-retrieval/2.1";
  response.stage = "preview";
  response.clusters = [];
  response.preview = [{
    claim_id: claimId,
    snapshot_id: snapshotId,
    paper_id: "paper:fixture",
    paper_family_id: "artifact:fixture",
    scores: { lexical: 3.2, dense: 0.81, rrf: 0.032, rerank: 0.91 },
  }];
  response.retrieval_confidence = retrievalConfidence();
  response.channels.relation = { attempted: false, status: "skipped", provider: "gpt", returned: 0 };
  response.query_plan_handoff = queryPlanHandoff;
  return response;
}

/** Interactive finals are sealed 2.1 requests that carry the preview handoff. */
function interactiveFinalRequest() {
  return { ...searchRequest(), schema_version: "evidence-retrieval/2.1", query_plan_handoff: queryPlanHandoff };
}

function searchRequest() {
  return {
    schema_version: "evidence-retrieval/2.0",
    project_id: "DEEPSHOVEL-PROJECT",
    stage: "ai_rerank",
    query: "A increases B",
    query_claim: { snapshot_id: snapshotId, claim_id: claimId },
    candidate_refs: [{ snapshot_id: snapshotId, claim_id: claimId }],
    intent: "supporting_evidence",
    must_terms: ["A"],
    exclude_terms: ["background"],
    scope: null,
    claim_types: ["causal"],
    polarity: "affirmed",
    filters: {
      corpus: "cross_paper",
      paper_ids: [],
      paper_family_ids: [],
      active_snapshot_ids: null,
      limit: 10,
    },
  };
}

test("preview transport accepts thirty unique candidates but rejects thirty-one", () => {
  const response = previewSearchResponseV21();
  const template = response.preview[0];
  response.preview = Array.from({ length: 30 }, (_, i) => ({
    ...template, claim_id: `claim:sha256:${i.toString(16).padStart(64, "0")}`,
  }));
  assert.equal(parseRetrievalSearchResponse(response, [snapshotId]).preview.length, 30);
  response.preview.push({ ...template, claim_id: `claim:sha256:${"f".repeat(64)}` });
  assert.throws(() => parseRetrievalSearchResponse(response, [snapshotId]), /bounded array/u);
});

test("retrieval client uses fixed authenticated v2 POST boundaries", async () => {
  const calls = [];
  const client = new RetrievalClient({
    baseUrl: "http://127.0.0.1:8011",
    sharedSecret: secret,
    fetch: async (url, init) => {
      const body = JSON.parse(init.body);
      calls.push({ url: String(url), init, body });
      if (String(url).endsWith("/v1/retrieval/manifests:check")) {
        return jsonResponse({ schema_version: "evidence-retrieval/2.0", manifests: [state()] });
      }
      if (String(url).endsWith("/v1/retrieval/index")) {
        return jsonResponse({
          schema_version: "evidence-retrieval/2.0",
          manifests: [state("unchanged")],
          indexed_documents: 0,
          warnings: [],
        });
      }
      if (String(url).endsWith("/v1/retrieval/search")) return jsonResponse(searchResponse());
      return jsonResponse({
        schema_version: "evidence-retrieval/2.0",
        deleted_snapshots: 1,
        deleted_documents: 0,
        warnings: [],
      });
    },
  });

  const checked = await client.checkManifests("DEEPSHOVEL-PROJECT", [manifest()]);
  assert.equal(checked.manifests[0].dense_complete, true);
  await client.indexSnapshot("DEEPSHOVEL-PROJECT", manifest(), []);
  const searched = await client.search(searchRequest(), [snapshotId]);
  assert.equal(searched.clusters[0].representative.relation_direction, "result_to_query");
  assert.equal(searched.clusters[0].representative.relation_confidence, null);
  await client.deleteSnapshots("DEEPSHOVEL-PROJECT", [snapshotId]);

  assert.deepEqual(calls.map((call) => new URL(call.url).pathname), [
    "/v1/retrieval/manifests:check",
    "/v1/retrieval/index",
    "/v1/retrieval/search",
    "/v1/retrieval/snapshots:delete",
  ]);
  for (const call of calls) {
    assert.equal(call.init.method, "POST");
    assert.equal(call.init.redirect, "manual");
    assert.equal(call.init.referrerPolicy, "no-referrer");
    assert.equal(new Headers(call.init.headers).get("authorization"), `Bearer ${secret}`);
    assert.equal(new Headers(call.init.headers).get("content-type"), "application/json");
    assert.equal(call.body.schema_version, "evidence-retrieval/2.0");
    assert.equal(call.body.project_id, "DEEPSHOVEL-PROJECT");
  }
  assert.deepEqual(calls[2].body.query_claim, { snapshot_id: snapshotId, claim_id: claimId });
  assert.deepEqual(calls[2].body.claim_types, ["causal"]);
  assert.equal(calls[3].body.index_version, "evidence-retrieval/2.0");
});

test("retrieval protocol rejects unknown fields, inactive refs and snapshot mismatches", () => {
  assert.throws(
    () => parseRetrievalSearchResponse({ ...searchResponse(), extra: true }, [snapshotId]),
    (error) => error.code === "INVALID_RETRIEVAL_RESPONSE",
  );
  const missingConfidence = searchResponse();
  delete missingConfidence.retrieval_confidence;
  assert.throws(
    () => parseRetrievalSearchResponse(missingConfidence, [snapshotId]),
    (error) => error.code === "INVALID_RETRIEVAL_RESPONSE",
  );
  const missingResultStatus = searchResponse();
  delete missingResultStatus.result_status;
  assert.throws(
    () => parseRetrievalSearchResponse(missingResultStatus, [snapshotId]),
    (error) => error.code === "INVALID_RETRIEVAL_RESPONSE",
  );
  const falseNoAnswer = searchResponse();
  falseNoAnswer.result_status = "no_direct_answer";
  assert.throws(
    () => parseRetrievalSearchResponse(falseNoAnswer, [snapshotId]),
    (error) => error.code === "INVALID_RETRIEVAL_RESPONSE" && /no_direct_answer/u.test(error.message),
  );
  const hiddenDegradation = searchResponse();
  hiddenDegradation.channels.dense.status = "degraded";
  assert.throws(
    () => parseRetrievalSearchResponse(hiddenDegradation, [snapshotId]),
    (error) => error.code === "INVALID_RETRIEVAL_RESPONSE" && /degraded/u.test(error.message),
  );
  const inactive = searchResponse();
  inactive.clusters[0].member_refs[0].snapshot_id = `snapshot:sha256:${"e".repeat(64)}`;
  assert.throws(
    () => parseRetrievalSearchResponse(inactive, [snapshotId]),
    (error) => error.code === "INVALID_RETRIEVAL_RESPONSE" && /active corpus/u.test(error.message),
  );
  const wrongCount = searchResponse();
  wrongCount.clusters[0].distinct_paper_families = 0;
  assert.throws(
    () => parseRetrievalSearchResponse(wrongCount, [snapshotId]),
    (error) => error.code === "INVALID_RETRIEVAL_RESPONSE" && /paper_families/u.test(error.message),
  );
  const detachedRepresentative = searchResponse();
  detachedRepresentative.clusters[0].representative.claim_id = `claim:sha256:${"f".repeat(64)}`;
  assert.throws(
    () => parseRetrievalSearchResponse(detachedRepresentative, [snapshotId]),
    (error) => error.code === "INVALID_RETRIEVAL_RESPONSE" && /representative/u.test(error.message),
  );
  const representativeSecond = searchResponse();
  const other = member();
  other.claim_id = `claim:sha256:${"c".repeat(64)}`;
  other.paper_id = "paper:other";
  other.paper_family_id = "artifact:other";
  representativeSecond.clusters[0].member_refs = [other, member()];
  representativeSecond.clusters[0].distinct_paper_families = 2;
  assert.throws(
    () => parseRetrievalSearchResponse(representativeSecond, [snapshotId]),
    (error) => error.code === "INVALID_RETRIEVAL_RESPONSE" && /member_refs\[0\]/u.test(error.message),
  );
  const divergentRepresentative = searchResponse();
  divergentRepresentative.clusters[0].member_refs[0].scores.rerank = 0.1;
  assert.throws(
    () => parseRetrievalSearchResponse(divergentRepresentative, [snapshotId]),
    (error) => error.code === "INVALID_RETRIEVAL_RESPONSE" && /member_refs\[0\]/u.test(error.message),
  );
  const relationMismatch = searchResponse();
  relationMismatch.clusters[0].relation = "contradicts";
  assert.throws(
    () => parseRetrievalSearchResponse(relationMismatch, [snapshotId]),
    (error) => error.code === "INVALID_RETRIEVAL_RESPONSE" && /representative/u.test(error.message),
  );
  const openScope = searchResponse();
  openScope.query_plan.scope_constraints = { location: ["Basin X"], unexpected: true };
  assert.throws(
    () => parseRetrievalSearchResponse(openScope, [snapshotId]),
    (error) => error.code === "INVALID_RETRIEVAL_RESPONSE" && /scope_constraints/u.test(error.message),
  );
  const nullableArrayScope = searchResponse();
  nullableArrayScope.query_plan.scope_constraints = { location: null, time: null, uncertainty: null };
  assert.throws(
    () => parseRetrievalSearchResponse(nullableArrayScope, [snapshotId]),
    (error) => error.code === "INVALID_RETRIEVAL_RESPONSE" && /location/u.test(error.message),
  );
  for (const emptyScope of [{}, { location: [], entities: [], time: null, uncertainty: null }]) {
    const emptyPlanScope = searchResponse();
    emptyPlanScope.query_plan.scope_constraints = emptyScope;
    assert.throws(
      () => parseRetrievalSearchResponse(emptyPlanScope, [snapshotId]),
      (error) => error.code === "INVALID_RETRIEVAL_RESPONSE" && /at least one constraint/u.test(error.message),
    );
  }
});

test("retrieval protocol accepts full query-plan term bounds and truncated cluster family totals", () => {
  const response = searchResponse();
  response.query_plan.original_query = "🧪".repeat(5_000);
  response.query_plan.canonical_claim = "🧬".repeat(5_000);
  response.query_plan.must_terms = Array.from({ length: 64 }, (_, index) => `must-${index}`);
  response.query_plan.optional_terms = Array.from({ length: 64 }, (_, index) => `optional-${index}`);
  response.query_plan.exclude_terms = Array.from({ length: 64 }, (_, index) => `exclude-${index}`);
  response.query_plan.entities = Array.from({ length: 64 }, (_, index) => `entity-${index}`);
  response.query_plan.scope_constraints = {
    location: ["Basin X"],
    time: null,
    uncertainty: null,
  };
  response.clusters[0].distinct_paper_families = 9_000;
  const parsed = parseRetrievalSearchResponse(response, [snapshotId]);
  assert.equal(Array.from(parsed.query_plan.original_query).length, 5_000);
  assert.equal(parsed.query_plan.entities.length, 64);
  assert.deepEqual(parsed.query_plan.scope_constraints, {
    location: ["Basin X"],
    time: null,
    uncertainty: null,
  });
  assert.equal(parsed.clusters[0].member_refs.length, 1);
  assert.equal(parsed.clusters[0].distinct_paper_families, 9_000);
});

test("retrieval protocol keeps preview refs separate from AI relation clusters", () => {
  const response = searchResponse();
  response.stage = "preview";
  response.clusters = [];
  response.preview = [{
    claim_id: claimId,
    snapshot_id: snapshotId,
    paper_id: "paper:fixture",
    paper_family_id: "artifact:fixture",
    scores: { lexical: 3.2, dense: 0.81, rrf: 0.032, rerank: 0.91 },
  }];
  response.retrieval_confidence = retrievalConfidence();
  response.channels.relation = { attempted: false, status: "skipped", provider: "gpt", returned: 0 };
  const parsed = parseRetrievalSearchResponse(response, [snapshotId]);
  assert.equal(parsed.stage, "preview");
  assert.equal(parsed.preview[0].claim_id, claimId);
  assert.equal(parsed.clusters.length, 0);
  assert.equal(parsed.retrieval_confidence.mode, "observe");
  assert.equal(parsed.retrieval_confidence.decision, "return");
  assert.equal(parsed.retrieval_confidence.would_abstain, null);
  assert.deepEqual(parsed.retrieval_confidence.strong_signal_types, ["concept_anchor"]);

  const mixed = structuredClone(response);
  mixed.clusters = searchResponse().clusters;
  assert.throws(
    () => parseRetrievalSearchResponse(mixed, [snapshotId]),
    (error) => error.code === "INVALID_RETRIEVAL_RESPONSE" && /preview responses/u.test(error.message),
  );
});

test("retrieval search 2.1 requires a shaped preview handoff and never accepts an echoed final token", () => {
  const preview = parseRetrievalSearchResponse(previewSearchResponseV21(), [snapshotId]);
  assert.equal(preview.schema_version, "evidence-retrieval/2.1");
  assert.equal(preview.query_plan_handoff, queryPlanHandoff);

  const missing = previewSearchResponseV21();
  delete missing.query_plan_handoff;
  assert.throws(
    () => parseRetrievalSearchResponse(missing, [snapshotId]),
    (error) => error.code === "INVALID_RETRIEVAL_RESPONSE",
  );

  const malformed = previewSearchResponseV21();
  malformed.query_plan_handoff = `${queryPlanHandoff.slice(0, -1)}=`;
  assert.throws(
    () => parseRetrievalSearchResponse(malformed, [snapshotId]),
    (error) => error.code === "INVALID_RETRIEVAL_RESPONSE" && /handoff/u.test(error.message),
  );

  const echoedFinal = searchResponse();
  echoedFinal.schema_version = "evidence-retrieval/2.1";
  echoedFinal.query_plan_handoff = queryPlanHandoff;
  assert.throws(
    () => parseRetrievalSearchResponse(echoedFinal, [snapshotId]),
    (error) => error.code === "INVALID_RETRIEVAL_RESPONSE",
  );

  const legacyWithHandoff = searchResponse();
  legacyWithHandoff.query_plan_handoff = queryPlanHandoff;
  assert.throws(
    () => parseRetrievalSearchResponse(legacyWithHandoff, [snapshotId]),
    (error) => error.code === "INVALID_RETRIEVAL_RESPONSE",
  );

  const finalV21 = searchResponse();
  finalV21.schema_version = "evidence-retrieval/2.1";
  const parsedFinal = parseRetrievalSearchResponse(finalV21, [snapshotId]);
  assert.equal(parsedFinal.schema_version, "evidence-retrieval/2.1");
  assert.equal(Object.hasOwn(parsedFinal, "query_plan_handoff"), false);
});

test("retrieval client binds the response protocol and stage to the submitted 2.1 search", async () => {
  let calls = 0;
  const previewRequest = {
    ...searchRequest(),
    schema_version: "evidence-retrieval/2.1",
    stage: "preview",
    candidate_refs: [],
  };
  const client = new RetrievalClient({
    baseUrl: "http://127.0.0.1:8011",
    sharedSecret: secret,
    fetch: async () => {
      calls += 1;
      return jsonResponse(previewSearchResponseV21());
    },
  });
  const preview = await client.search(previewRequest, [snapshotId]);
  assert.equal(preview.query_plan_handoff, queryPlanHandoff);
  assert.equal(calls, 1);

  await assert.rejects(
    () => client.search({ ...previewRequest, query_plan_handoff: queryPlanHandoff }, [snapshotId]),
    (error) => error.code === "INVALID_RETRIEVAL_REQUEST" && /handoff/u.test(error.message),
  );
  await assert.rejects(
    () => client.search({
      ...searchRequest(),
      schema_version: "evidence-retrieval/2.1",
      query_plan_handoff: `${queryPlanHandoff.slice(0, -1)}=`,
    }, [snapshotId]),
    (error) => error.code === "INVALID_RETRIEVAL_REQUEST" && /handoff/u.test(error.message),
  );
  assert.equal(calls, 1);

  const wrongStage = new RetrievalClient({
    baseUrl: "http://127.0.0.1:8011",
    sharedSecret: secret,
    fetch: async () => jsonResponse({ ...searchResponse(), schema_version: "evidence-retrieval/2.1" }),
  });
  await assert.rejects(
    () => wrongStage.search(previewRequest, [snapshotId]),
    (error) => error.code === "INVALID_RETRIEVAL_RESPONSE" && /requested protocol version and stage/u.test(error.message),
  );
});

test("retrieval client retains the legacy handoff-free 2.0 preview shape", async () => {
  const response = previewSearchResponseV21();
  response.schema_version = "evidence-retrieval/2.0";
  delete response.query_plan_handoff;
  const client = new RetrievalClient({
    baseUrl: "http://127.0.0.1:8011",
    sharedSecret: secret,
    fetch: async () => jsonResponse(response),
  });
  const parsed = await client.search({
    ...searchRequest(),
    stage: "preview",
    candidate_refs: [],
  }, [snapshotId]);
  assert.equal(parsed.schema_version, "evidence-retrieval/2.0");
  assert.equal(parsed.stage, "preview");
  assert.equal(Object.hasOwn(parsed, "query_plan_handoff"), false);
});

test("retrieval protocol strictly validates observe-only confidence telemetry", () => {
  const preview = searchResponse();
  preview.stage = "preview";
  preview.result_status = "no_direct_answer";
  preview.clusters = [];
  preview.retrieval_confidence = retrievalConfidence();

  for (const invalidConfidence of [
    retrievalConfidence({ decision: "abstain" }),
    retrievalConfidence({ would_abstain: false }),
    retrievalConfidence({ direct_lexical_coverage: 0.4 }),
    retrievalConfidence({ direct_lexical_query_tokens: 257 }),
    retrievalConfidence({ strong_signal_types: [] }),
    retrievalConfidence({ dense_observed_hits: 1, dense_top2: 0.75, dense_margin: 0.06 }),
    { ...retrievalConfidence(), unexpected: true },
  ]) {
    const invalid = structuredClone(preview);
    invalid.retrieval_confidence = invalidConfidence;
    assert.throws(
      () => parseRetrievalSearchResponse(invalid, [snapshotId]),
      (error) => error.code === "INVALID_RETRIEVAL_RESPONSE",
    );
  }

  const finalResponse = searchResponse();
  finalResponse.retrieval_confidence = retrievalConfidence();
  assert.throws(
    () => parseRetrievalSearchResponse(finalResponse, [snapshotId]),
    (error) => error.code === "INVALID_RETRIEVAL_RESPONSE" && /preview-only/u.test(error.message),
  );
});

test("retrieval transport requires credentials and rejects redirects, reflection and oversized JSON", async () => {
  assert.throws(
    () => new RetrievalClient({ baseUrl: "http://127.0.0.1:8011", sharedSecret: "short" }),
    (error) => error.code === "RETRIEVAL_NOT_CONFIGURED",
  );
  assert.throws(
    () => new RetrievalClient({ baseUrl: "http://retrieval.example.test", sharedSecret: secret }),
    (error) => error.code === "RETRIEVAL_NOT_CONFIGURED",
  );
  assert.throws(
    () => new RetrievalClient({ baseUrl: "https://user:password@example.test", sharedSecret: secret }),
    (error) => error.code === "RETRIEVAL_NOT_CONFIGURED",
  );

  const redirect = new RetrievalClient({
    baseUrl: "https://retrieval.example.test",
    sharedSecret: secret,
    fetch: async () => new Response(null, { status: 307, headers: { location: "https://attacker.example" } }),
  });
  await assert.rejects(
    () => redirect.search(searchRequest(), [snapshotId]),
    (error) => error.code === "RETRIEVAL_REDIRECT_REJECTED" && error.details.retryable === false,
  );

  const reflected = new RetrievalClient({
    baseUrl: "https://retrieval.example.test",
    sharedSecret: secret,
    fetch: async () => jsonResponse({ error: { code: "BAD_REQUEST", message: secret } }, { status: 400 }),
  });
  await assert.rejects(
    () => reflected.search(searchRequest(), [snapshotId]),
    (error) => error.code === "RETRIEVAL_RESPONSE_REJECTED" && !error.message.includes(secret),
  );

  const oversized = new RetrievalClient({
    baseUrl: "https://retrieval.example.test",
    sharedSecret: secret,
    fetch: async () => jsonResponse({}, { headers: { "content-length": String(8 * 1024 * 1024 + 1) } }),
  });
  await assert.rejects(
    () => oversized.search(searchRequest(), [snapshotId]),
    (error) => error.code === "RETRIEVAL_RESPONSE_TOO_LARGE",
  );
});

test("retrieval request encoding failures retain their local code without sending a POST", async () => {
  let calls = 0;
  const client = new RetrievalClient({ baseUrl: "https://retrieval.example.test", sharedSecret: secret,
    fetch: async () => { calls++; throw new Error("must not send"); } });
  await assert.rejects(client.indexSnapshot("DEEPSHOVEL-PROJECT", manifest(), [{ text: "x".repeat(8 * 1024 * 1024) }]),
    error => error.status === 413 && error.code === "RETRIEVAL_REQUEST_TOO_LARGE");
  const circular = {}; circular.self = circular;
  await assert.rejects(client.indexSnapshot("DEEPSHOVEL-PROJECT", manifest(), [circular]),
    error => error.status === 500 && error.code === "INVALID_RETRIEVAL_REQUEST");
  assert.equal(calls, 0);
});

test("retrieval fetch failures log only bounded classifications and never replay POST", async (t) => {
  const logs = [];
  t.mock.method(console, "warn", (...args) => logs.push(args));
  const privateText = `https://private.example/query?secret=${secret}&q=private-query`;
  const cases = [
    [new TypeError(privateText, { cause: Object.assign(new Error(privateText), { code: "ECONNRESET" }) }), "connection_reset", "ECONNRESET"],
    [Object.assign(new Error(privateText), { code: "ECONNREFUSED" }), "connection_refused", "ECONNREFUSED"],
    [new Error(`Too many subrequests ${privateText}`), "runtime_limit", null],
    [new Error(`Network connection lost ${privateText}`), "connection_lost", null],
    [new Error(`Socket disconnected ${privateText}`), "network", null],
    [Object.assign(new Error(privateText), { code: secret }), "unknown", null],
  ];
  for (const [failure, classification, nativeCode] of cases) {
    let calls = 0;
    const client = new RetrievalClient({ baseUrl: "https://retrieval.example.test", sharedSecret: secret,
      interactive: true, fetch: async () => { calls++; throw failure; } });
    await assert.rejects(client.search(interactiveFinalRequest(), [snapshotId]),
      error => error.status === 503 && error.code === "RETRIEVAL_UNAVAILABLE");
    assert.equal(calls, 1);
    const [label, entry] = logs.at(-1);
    assert.equal(label, "DeepShovel retrieval transport failure");
    assert.deepEqual(Object.keys(entry).sort(), ["classification", "elapsedMs", "method", "nativeCode", "operation"]);
    assert.equal(entry.operation, "v1/retrieval/search");
    assert.equal(entry.method, "POST");
    assert.equal(entry.classification, classification);
    assert.equal(entry.nativeCode, nativeCode);
    assert.ok(Number.isFinite(entry.elapsedMs) && entry.elapsedMs >= 0);
  }
  assert.equal(logs.length, cases.length);
  for (const forbidden of [secret, "private.example", "private-query"]) assert.ok(!JSON.stringify(logs).includes(forbidden));
});

test("retrieval transport preserves the allowlisted re-preview conflict without reflecting upstream detail", async () => {
  for (const [code, message] of [
    ["QUERY_PLAN_HANDOFF_REPREVIEW_REQUIRED", "The retrieval preview is no longer valid; run preview again"],
    ["CLARIFICATION_REPREVIEW_REQUIRED", "The clarification is no longer valid; run preview again"],
  ]) {
    const client = new RetrievalClient({
      baseUrl: "https://retrieval.example.test",
      sharedSecret: secret,
      fetch: async () => jsonResponse({
        error: {
          code,
          message: "untrusted upstream detail",
          retryable: false,
        },
      }, { status: 409 }),
    });
    await assert.rejects(
      () => client.search({
        ...searchRequest(),
        schema_version: "evidence-retrieval/2.1",
        query_plan_handoff: queryPlanHandoff,
      }, [snapshotId]),
      (error) => error.status === 409
        && error.code === code
        && error.message === message
        && error.details.retryable === false,
    );
  }
});

test("planning failure classifications retain fixed status without upstream prose or automatic retry", async () => {
  const classifications = [
    ["RETRIEVAL_PLANNING_OUTPUT_INVALID", 502],
    ["RETRIEVAL_PLANNING_TIMEOUT", 504],
    ["RETRIEVAL_PLANNING_UPSTREAM_FAILED", 502],
    ["RETRIEVAL_PLANNING_UNAVAILABLE", 503],
    ["RETRIEVAL_PLANNING_PROVIDER_INVALID", 502],
    ["RETRIEVAL_PLANNING_INPUT_TOO_LARGE", 413],
    ["RETRIEVAL_PLANNING_BUDGET_EXCEEDED", 503],
    ["RETRIEVAL_PLANNING_CAPABILITY_UNAVAILABLE", 422],
    ["RETRIEVAL_PLANNING_INTERNAL_ERROR", 500],
  ];
  const privateMaterial = "raw-provider-message private-query c1.private.payload.signature";
  for (const [code, expectedStatus] of classifications) {
    for (const envelope of ["error", "detail"]) {
      let calls = 0;
      const client = new RetrievalClient({
        baseUrl: "https://retrieval.example.test",
        sharedSecret: secret,
        fetch: async () => {
          calls += 1;
          return jsonResponse({ [envelope]: {
            code,
            message: privateMaterial,
            retryable: true,
            diagnostics: { query: privateMaterial, token: privateMaterial },
          } }, { status: expectedStatus });
        },
      });
      await assert.rejects(() => client.search(searchRequest(), [snapshotId]), (error) => {
        assert.equal(error.code, code);
        assert.equal(error.status, expectedStatus);
        assert.deepEqual(error.details, { retryable: false });
        assert.match(error.message, /^(Retrieval planning|The supplied source)/u);
        assert.doesNotMatch(error.message, /raw-provider|private-query|c1\./u);
        assert.doesNotMatch(JSON.stringify(error), /raw-provider|private-query|c1\./u);
        return true;
      });
      assert.equal(calls, 1);
    }
  }
});

test("retrieval client threads caller cancellation into service fetch", async () => {
  const controller = new AbortController();
  controller.abort();
  const client = new RetrievalClient({
    baseUrl: "https://retrieval.example.test",
    sharedSecret: secret,
    fetch: async (_url, init) => {
      assert.equal(init.signal.aborted, true);
      throw init.signal.reason;
    },
  });
  await assert.rejects(
    () => client.search(searchRequest(), [snapshotId], controller.signal),
    (error) => error.code === "RETRIEVAL_REQUEST_CANCELLED" && error.status === 408,
  );
});

test("cancelled batches expose counters without publishing results from a deleted corpus", async () => {
  const payload = {
    schema_version: "evidence-retrieval/2.0",
    batch_id: `relation-batch:sha256:${"c".repeat(64)}`,
    status: "cancelled", reprocess: false, total: 1, completed: 1, succeeded: 0, failed: 1,
    items: [{ query_claim: { snapshot_id: snapshotId, claim_id: claimId },
      status: "failed", response: null, error_code: "CLAIM_RELATION_PROCESSING_CANCELLED" }],
  };
  const client = new RetrievalClient({ baseUrl: "https://compiler.example", sharedSecret: secret,
    fetch: async () => jsonResponse(payload) });
  const remaining = `snapshot:sha256:${"d".repeat(64)}`;
  const result = await client.getClaimRelationBatch(payload.batch_id, [remaining]);
  assert.equal(result.status, "cancelled");
  assert.equal(result.completed, 1);
  assert.deepEqual(result.items, []);
  payload.status = "completed";
  await assert.rejects(client.getClaimRelationBatch(payload.batch_id, [remaining]), /outside the active corpus/u);
});

test("interactive synchronization and search have no timeout while batch policy remains unchanged",async()=>{
  const original=AbortSignal.timeout, budgets=[];
  AbortSignal.timeout=ms=>{budgets.push(ms);return new AbortController().signal;};
  try{
    for(const interactive of [true,false]){
      const controller=new AbortController();
      const client=new RetrievalClient({baseUrl:"https://compiler.example",sharedSecret:secret,interactive,
        fetch:async(url,init)=>{
          const search=String(url).endsWith("/search");
          assert.equal(init.headers["x-deepshovel-interactive"],interactive&&search?"1":undefined);
          if(interactive)assert.equal(init.signal,controller.signal);
          await new Promise(resolve=>setImmediate(resolve));
          assert.equal(init.signal.aborted,false);
          if(search)return jsonResponse({...searchResponse(),schema_version:"evidence-retrieval/2.1"});
          return jsonResponse(String(url).endsWith("/index")
            ? {schema_version:"evidence-retrieval/2.0",manifests:[state("unchanged")],indexed_documents:0,warnings:[]}
            : {schema_version:"evidence-retrieval/2.0",manifests:[state()]});
        }});
      await client.checkManifests("DEEPSHOVEL-PROJECT",[manifest()],controller.signal);
      await client.indexSnapshot("DEEPSHOVEL-PROJECT",manifest(),[],controller.signal);
      await client.search(interactiveFinalRequest(),[snapshotId],controller.signal);
      assert.deepEqual(budgets,interactive?[]:[15000,180000]);
    }
  }finally{AbortSignal.timeout=original;}
});

 test("interactive search remains pending until success or explicit caller cancellation",async()=>{
  const controller=new AbortController();let ready;
  const entered=new Promise(resolve=>{ready=resolve;});
  const client=new RetrievalClient({baseUrl:"https://compiler.example",sharedSecret:secret,interactive:true,
    fetch:async(_url,init)=>new Promise((_resolve,reject)=>{
      init.signal.addEventListener("abort",()=>reject(new DOMException("cancelled","AbortError")),{once:true});ready();
    })});
  const running=client.search(interactiveFinalRequest(),[snapshotId],controller.signal);
  await entered;
  controller.abort();
  await assert.rejects(running,error=>error.status===408&&error.code==="RETRIEVAL_REQUEST_CANCELLED");
});

test("an interactive client refuses a handoff-free legacy final before any request",async()=>{
  let calls=0;
  const client=new RetrievalClient({baseUrl:"https://compiler.example",sharedSecret:secret,interactive:true,
    fetch:async()=>{calls+=1;return jsonResponse(searchResponse());}});
  await assert.rejects(client.search(searchRequest(),[snapshotId]),
    error=>error.status===500&&error.code==="INVALID_RETRIEVAL_REQUEST");
  assert.equal(calls,0);
  const batch=new RetrievalClient({baseUrl:"https://compiler.example",sharedSecret:secret,
    fetch:async()=>{calls+=1;return jsonResponse(searchResponse());}});
  await batch.search(searchRequest(),[snapshotId]);
  assert.equal(calls,1);
});

test("degraded final never accepts preview candidates as a model answer",async()=>{
  const response=previewSearchResponseV21();response.stage="ai_rerank";response.result_status="degraded";
  response.channels.relation={attempted:true,status:"degraded",provider:"gpt",returned:0};
  response.retrieval_confidence=null;delete response.query_plan_handoff;
  const request={...searchRequest(),schema_version:"evidence-retrieval/2.1",query_plan_handoff:queryPlanHandoff,
    candidate_refs:response.preview.map(({snapshot_id,claim_id})=>({snapshot_id,claim_id}))};
  const make=(interactive,payload=response)=>new RetrievalClient({baseUrl:"https://compiler.example",sharedSecret:secret,interactive,fetch:async()=>jsonResponse(payload)});
  for (const interactive of [false,true]) {
    await assert.rejects(make(interactive).search(request,[snapshotId]),/cannot contain preview refs/);
  }
  const invalid=structuredClone(response);invalid.channels.relation.status="ok";
  assert.throws(()=>parseRetrievalSearchResponse(invalid,[snapshotId]));
});

test("upstream auth and bad request cannot masquerade as planning availability",async()=>{
  for(const status of [400,401,403,409]){
    const client=new RetrievalClient({baseUrl:"https://compiler.example",sharedSecret:secret,fetch:async()=>jsonResponse({detail:{code:"RETRIEVAL_PLANNING_UNAVAILABLE",retryable:true}},{status})});
    await assert.rejects(client.search(searchRequest(),[snapshotId]),error=>error.status===status);
  }
});


test("batch full-project comparison supports more than 4096 snapshots without widening wire lists", async () => {
  const ids = Array.from({ length: 5000 }, (_, index) => `snapshot-${index}`);
  const payload = { schema_version: "evidence-retrieval/2.0", batch_id: "batch-large",
    status: "running", reprocess: false, total: 1, completed: 0, succeeded: 0, failed: 0, items: [] };
  const calls = [];
  const client = new RetrievalClient({ baseUrl: "https://compiler.example", sharedSecret: secret,
    fetch: async (url, init) => { calls.push({ url: String(url), body: init.body ? JSON.parse(init.body) : null }); return jsonResponse(payload); } });
  await client.startClaimRelationBatch("project-1", "request-1", ids, [{ snapshotId: ids[4999], claimId }], false, undefined, true);
  assert.equal(calls[0].body.active_snapshot_ids, null);
  assert.ok(JSON.stringify(calls[0].body).length < 1000);
  await client.getClaimRelationBatch("batch-large", ids);
  await client.cancelClaimRelationBatch("batch-large", ids);
  await assert.rejects(client.startClaimRelationBatch("project-1", "request-2", ids, [{ snapshotId: ids[0], claimId }], false), /4096/u);
  await assert.rejects(client.startClaimRelationBatch("project-1", "request-3", ids, [{ snapshotId: "outside", claimId }], false, undefined, true), /inactive Claim/u);
  await client.startClaimRelationBatch("project-1", "request-small", ids.slice(0, 2), [{ snapshotId: ids[0], claimId }], false, undefined, true);
  assert.deepEqual(calls.at(-1).body.active_snapshot_ids, ids.slice(0, 2));
  payload.status = "completed"; payload.completed = 1; payload.failed = 1;
  payload.items = [{ query_claim: { snapshot_id: ids[4999], claim_id: claimId }, status: "failed", response: null, error_code: "TEST_FAILURE" }];
  assert.equal((await client.getClaimRelationBatch("batch-large", ids)).items.length, 1);
  payload.items[0].query_claim.snapshot_id = "outside";
  await assert.rejects(client.getClaimRelationBatch("batch-large", ids), /outside the active corpus/u);
});

test("batch cursor pages replay safely, validate offsets, and distinguish retirement from cancellation", async () => {
  const item = { query_claim: { snapshot_id: snapshotId, claim_id: claimId }, status: "failed", response: null, error_code: "TEST_FAILURE" };
  const payload = { schema_version: "evidence-retrieval/2.0", batch_id: "batch-page",
    status: "completed", reprocess: false, total: 20, completed: 20, succeeded: 0, failed: 20,
    items: [item], item_offset: 19, next_cursor: 20, retired: false };
  const client = new RetrievalClient({ baseUrl: "https://compiler.example", sharedSecret: secret,
    fetch: async (url) => { assert.match(String(url), /after=19$/u); return jsonResponse(payload); } });
  const first = await client.getClaimRelationBatch("batch-page", [snapshotId], undefined, 19);
  assert.deepEqual(await client.getClaimRelationBatch("batch-page", [snapshotId], undefined, 19), first);
  assert.equal(first.next_cursor, 20);
  payload.status = "cancelled";
  assert.equal((await client.cancelClaimRelationBatch("batch-page", [snapshotId], undefined, 19)).items.length, 1);
  payload.retired = true; payload.items = [];
  assert.deepEqual((await client.getClaimRelationBatch("batch-page", [snapshotId], undefined, 19)).items, []);
  payload.item_offset = 18;
  await assert.rejects(client.getClaimRelationBatch("batch-page", [snapshotId], undefined, 19), /inconsistent/u);
});


test("ordinary search compact transport still validates results against every gateway-authorized snapshot", async () => {
  const allowed = [snapshotId, ...Array.from({ length: 4999 }, (_,i)=>`snapshot:sha256:${i.toString(16).padStart(64,'0')}`)];
  const request = searchRequest();
  request.filters.active_snapshot_ids = encodeActiveSnapshotIds(allowed);
  const response = searchResponse();
  const client = new RetrievalClient({ baseUrl: "https://compiler.example", sharedSecret: secret,
    fetch: async (_url, init) => {
      assert.equal(JSON.parse(init.body).filters.active_snapshot_ids, request.filters.active_snapshot_ids);
      return jsonResponse(response);
    } });
  assert.equal((await client.search(request, allowed)).clusters.length, 1);
  const outside = `snapshot:sha256:${'f'.repeat(64)}`;
  response.clusters[0].representative.snapshot_id = outside;
  response.clusters[0].member_refs[0].snapshot_id = outside;
  await assert.rejects(client.search(request, allowed), /outside the active corpus/u);
});
