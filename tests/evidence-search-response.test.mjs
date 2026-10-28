import assert from "node:assert/strict";
import test from "node:test";
import { parseEvidenceSearchResponse } from "../app/evidence-search-response.ts";

const member = {
  snapshotId: "immutable-snapshot", source: { artifactName: "paper.pdf" },
  claim: { id: "claim", canonicalClaim: "原文观点", originalClaim: "原文", scope: {
    entities: [], location: [], formation: [], sample_type: [], method: [], condition: [], measurement: [],
  } },
  evidenceAnchors: [{ id: "anchor", page: 3, quote: "可追溯原文" }],
  shortestEvidencePath: { nodes: [] }, assessment: null,
};
const response = (overrides = {}) => ({
  ok: true, stage: "preview", resultStatus: "matches", query: { intent: "claim_search" },
  queryPlan: { queryType: "claim_search" }, queryPlanHandoff: null,
  previewResults: [structuredClone(member)], clusters: [],
  channels: Object.fromEntries(["lexical", "dense", "rerank", "relation"].map(key => [key, { status: "ok" }])),
  ...overrides,
});

test("malformed JSON fallthrough and malformed nested render fields fail before React state", () => {
  for (const payload of [{}, null, response({ clusters: undefined }), response({ query: undefined }),
    response({ previewResults: [{ ...member, claim: { ...member.claim, scope: {} } }] }),
    response({ previewResults: [{ ...member, shortestEvidencePath: { nodes: [null] } }] }),
    response({ clusters: [{ id: "bad", representative: member }] })]) {
    assert.throws(() => parseEvidenceSearchResponse(payload, "preview"), /格式异常/);
  }
  assert.throws(() => parseEvidenceSearchResponse(response(), "ai_rerank"), /格式异常/);
});

test("relation model failure rejects candidates and planless model responses", () => {
  for (const stage of ["preview", "ai_rerank"]) {
    assert.throws(() => parseEvidenceSearchResponse(response({ stage, resultStatus: "degraded", queryPlan: null }), stage), /格式异常/);
    const degradedRelation = response({ stage });
    degradedRelation.channels.relation.status = "degraded";
    assert.throws(() => parseEvidenceSearchResponse(degradedRelation, stage), /模型分析未完成/);
  }
});

test("quick preview keeps its limited progress contract without allowing it as a final answer", () => {
  const payload = response({ queryPlan: null, channels: undefined });
  assert.equal(parseEvidenceSearchResponse(payload, "preview", true), payload);
  assert.throws(() => parseEvidenceSearchResponse(payload, "preview"), /格式异常/);
  assert.throws(() => parseEvidenceSearchResponse({ ...payload, stage: "ai_rerank" }, "ai_rerank"), /格式异常/);
  const final = response({ stage: "ai_rerank", previewResults: [] });
  assert.equal(parseEvidenceSearchResponse(final, "ai_rerank"), final);
});

test("context and clarification still require their continuation payload and cannot be final results", () => {
  const context = response({ resultStatus: "needs_context", queryPlan: null, needsContext: { question: "补充？", contextToken: "signed" } });
  assert.equal(parseEvidenceSearchResponse(context, "preview"), context);
  assert.throws(() => parseEvidenceSearchResponse({ ...context, needsContext: {} }, "preview"), /格式异常/);
  assert.throws(() => parseEvidenceSearchResponse({ ...context, stage: "ai_rerank" }, "ai_rerank"), /格式异常/);
  assert.throws(() => parseEvidenceSearchResponse(response({ resultStatus: "clarification_required", clarification: null }), "preview"), /格式异常/);
});

test("recall channel degradation preserves a valid successful model result", () => {
  for (const stage of ["preview", "ai_rerank"]) {
    const payload = response({ stage, resultStatus: "degraded", previewResults: stage === "preview" ? [member] : [] });
    payload.channels.dense.status = "degraded";
    assert.equal(parseEvidenceSearchResponse(payload, stage), payload);
  }
  assert.throws(() => parseEvidenceSearchResponse(response({ stage: "ai_rerank" }), "ai_rerank"), /格式异常/);
});
