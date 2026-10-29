import assert from "node:assert/strict";
import test from "node:test";

import {
  comparisonScope,
  parseClaimComparisonEnvelope,
  parseClaimComparisonNormalization,
} from "../lib/claim-comparison/index.ts";
import { hashCanonical } from "../lib/evidence-graph/index.ts";

const emptyScope = {
  location: [], time: null, formation: [], sample_type: [], method: [],
  condition: [], measurement: [], uncertainty: null, entities: [],
};

function pairRequest() {
  return {
    schema_version: "claim-comparison/1.0",
    project_id: "project:test",
    pairs: [{
      id: "pair:one",
      query: {
        claim_id: "claim:query",
        canonical_claim: "Treatment X improves outcome Y.",
        claim_type: "causal", polarity: "affirmed", modality: "asserted",
        scope: emptyScope,
        source_snapshot_id: null, source_paper_id: null, source_paper_family_id: null,
        evidence_entities: [],
      },
      candidate: {
        claim_id: "claim:candidate",
        canonical_claim: "Treatment X improved outcome Y in the trial.",
        claim_type: "causal", polarity: "affirmed", modality: "asserted",
        scope: emptyScope,
        source_snapshot_id: `snapshot:sha256:${"1".repeat(64)}`,
        source_paper_id: "paper:candidate",
        source_paper_family_id: "artifact:candidate",
        evidence_entities: [{
          id: "observation:candidate",
          kind: "observation",
          text: "The prespecified outcome increased relative to control.",
          anchor_ids: ["anchor:candidate"],
          root_claim_ids: ["claim:candidate"],
        }],
      },
    }],
  };
}

async function validEnvelope() {
  const comparison = {
    id: "comparison:one",
    pair_id: "pair:one",
    relation: "supports",
    direction: "candidate_to_query",
    stance: "supportive",
    scope_alignment: "equivalent",
    directness: "direct",
    query_evidence_ids: [],
    candidate_evidence_ids: ["observation:candidate"],
    rationale: "The candidate reports the same intervention, comparator, outcome, and direction.",
    limitations: [],
    confidence: 0.91,
  };
  const digest = await hashCanonical([comparison]);
  return {
    schema_version: "claim-comparison/1.0",
    processor_version: "claim-comparison-processor/1.1.0",
    project_id: "project:test",
    extraction: {
      run_id: "comparison-extraction:one",
      model: "gpt-5.6-luna",
      prompt_version: "claim-comparison-extractor/1.1.0",
      attempts: 1,
      comparisons_sha256: digest,
      comparisons: [comparison],
    },
    verification: {
      run_id: "comparison-verification:one",
      model: "gpt-5.6-luna",
      prompt_version: "claim-comparison-verifier/1.1.0",
      attempts: 1,
      extraction_sha256: digest,
      assessments: [{
        comparison_id: "comparison:one",
        pair_id: "pair:one",
        verdict: "maintained",
        relation: "supports",
        direction: "candidate_to_query",
        stance: "supportive",
        scope_alignment: "equivalent",
        directness: "direct",
        query_evidence_ids: [],
        candidate_evidence_ids: ["observation:candidate"],
        rationale: "An independent pass retained the evidence-backed relation.",
        limitations: [],
        confidence: 0.88,
      }],
    },
  };
}

test("comparison preserves primary and fallback model provenance without weakening hashes", async () => {
  for (const model of ["gpt-6-luna", "gpt-5.6-luna", "deepseek-v4-flash"]) {
    const envelope = await validEnvelope();
    envelope.extraction.model = model;
    envelope.verification.model = model;
    const parsed = await parseClaimComparisonEnvelope(envelope, pairRequest());
    assert.equal(parsed.extraction.model, model);
    assert.equal(parsed.verification.model, model);
    envelope.extraction.comparisons_sha256 = "0".repeat(64);
    await assert.rejects(parseClaimComparisonEnvelope(envelope), /hash/u);
  }
  const envelope = await validEnvelope();
  envelope.extraction.model = "unsupported-model";
  await assert.rejects(parseClaimComparisonEnvelope(envelope), /provenance/u);
});

test("viewpoint normalization is closed-shape and preserves a verbatim source span", () => {
  const text = "In adults, treatment X may improve outcome Y.";
  const parsed = parseClaimComparisonNormalization({
    schema_version: "claim-comparison/1.0",
    processor_version: "claim-normalizer/1.0.0",
    status: "ready",
    clarification: null,
    run: {
      run_id: "claim-normalization:one",
      model: "gpt-5.6-luna",
      prompt_version: "claim-normalizer/1.0.0",
      attempts: 1,
    },
    claims: [{
      id: "normalized-claim:one",
      origin_span: text,
      canonical_claim: "Treatment X may improve outcome Y in adults.",
      claim_type: "causal",
      polarity: "uncertain",
      modality: "hypothesis",
      scope: { ...emptyScope, sample_type: ["adults"] },
      status: "ready",
      clarification: null,
    }],
  }, text);
  assert.equal(parsed.claims[0].originSpan, text);
  assert.throws(() => parseClaimComparisonNormalization({
    schema_version: "claim-comparison/1.0",
    processor_version: "claim-normalizer/1.0.0",
    status: "ready", clarification: null,
    run: { run_id: "claim-normalization:one", model: "gpt-5.6-luna", prompt_version: "claim-normalizer/1.0.0", attempts: 1 },
    claims: [{
      id: "normalized-claim:one", origin_span: "A sentence that was never supplied.",
      canonical_claim: "Invented.", claim_type: "causal", polarity: "affirmed", modality: "asserted",
      scope: emptyScope, status: "ready", clarification: null,
    }],
  }, text), /verbatim/u);
});

test("comparison decisions are hash-bound, exact-once, and evidence-bound", async () => {
  const envelope = await validEnvelope();
  const parsed = await parseClaimComparisonEnvelope(envelope, pairRequest());
  assert.equal(parsed.verification.assessments[0].relation, "supports");

  envelope.extraction.comparisons[0].rationale = "Tampered after freezing.";
  await assert.rejects(parseClaimComparisonEnvelope(envelope, pairRequest()), /hash/u);
});

test("a strong model result cannot pass the Web boundary with truncated input", async () => {
  const request = pairRequest();
  request.pairs[0].candidate.evidence_completeness = {
    status: "truncated", path_truncated: true, entity_limit: 256, depth_limit: 2,
    available_entity_count: 1, omitted_entity_count: 0, omitted_edge_count: 0,
    content_truncated: false,
  };
  await assert.rejects(parseClaimComparisonEnvelope(await validEnvelope(), request), /truncated recorded argument/u);
});

test("disjoint Scope cannot be published as contradiction", async () => {
  const envelope = await validEnvelope();
  envelope.extraction.comparisons[0].relation = "contradicts";
  envelope.extraction.comparisons[0].stance = "contradictory";
  envelope.extraction.comparisons[0].direction = "bidirectional";
  envelope.extraction.comparisons[0].scope_alignment = "disjoint";
  envelope.extraction.comparisons_sha256 = await hashCanonical(envelope.extraction.comparisons);
  envelope.verification.extraction_sha256 = envelope.extraction.comparisons_sha256;
  envelope.verification.assessments[0] = {
    ...envelope.verification.assessments[0],
    relation: "contradicts", stance: "contradictory", direction: "bidirectional", scope_alignment: "disjoint",
  };
  await assert.rejects(parseClaimComparisonEnvelope(envelope, pairRequest()), /comparable Scope|disjoint scopes/u);
});

test("an abstaining verifier must publish insufficient information", async () => {
  const envelope = await validEnvelope();
  envelope.verification.assessments[0] = {
    ...envelope.verification.assessments[0],
    verdict: "abstained",
    relation: "no_relation",
    direction: "none",
    stance: "neutral",
    directness: "none",
    candidate_evidence_ids: [],
    confidence: 0.4,
  };
  await assert.rejects(parseClaimComparisonEnvelope(envelope, pairRequest()), /insufficient_to_determine/u);
});

test("a stored Scope is sent within the Compiler's comparison rules", () => {
  // Live Claims list species that are one value under NFKC and case folding.
  const scope = comparisonScope({
    ...emptyScope,
    entities: ["NdCl²⁺", "NdCl₂⁺", "Nd³⁺", "S_H", "S_h", ...Array.from({ length: 140 }, (_, index) => `e${index}`)],
    location: ["South  China", "south china", "Groß", "Gross"],
  });
  assert.deepEqual(scope.entities.slice(0, 4), ["NdCl²⁺", "Nd³⁺", "S_H", "e0"]);
  assert.equal(scope.entities.length, 128);
  assert.deepEqual(scope.location, ["South  China", "Groß"]);
  assert.deepEqual(comparisonScope(emptyScope), emptyScope);
});
