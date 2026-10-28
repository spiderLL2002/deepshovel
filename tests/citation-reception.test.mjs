import assert from "node:assert/strict";
import test from "node:test";

import {
  buildCitationCandidateEntities,
  parseCitationReceptionEnvelope,
  parseCitationSourceProjection,
} from "../lib/citation-reception/index.ts";
import { hashCanonical } from "../lib/evidence-graph/index.ts";

test("citation reception preserves primary and fallback models with hash validation", async () => {
  for (const model of ["gpt-6-luna", "gpt-5.6-luna", "deepseek-v4-flash"]) {
    const envelope = await receptionEnvelope();
    envelope.extraction.model = model;
    envelope.verification.model = model;
    const parsed = await parseCitationReceptionEnvelope(envelope, pairRequest());
    assert.equal(parsed.extraction.model, model);
    assert.equal(parsed.verification.model, model);
    envelope.extraction.assertions_sha256 = "0".repeat(64);
    await assert.rejects(parseCitationReceptionEnvelope(envelope), /hash/u);
  }
  const envelope = await receptionEnvelope();
  envelope.extraction.model = "unsupported-model";
  await assert.rejects(parseCitationReceptionEnvelope(envelope), /provenance/u);
});

const assertionId = `citation-assertion:sha256:${"a".repeat(64)}`;
const mentionId = `citation-mention:sha256:${"b".repeat(64)}`;
const referenceId = `citation-reference:sha256:${"c".repeat(64)}`;

function pairRequest() {
  return {
    schema_version: "citation-reception/1.0",
    project_id: "project:test",
    citing_snapshot_id: `snapshot:sha256:${"1".repeat(64)}`,
    cited_snapshot_id: `snapshot:sha256:${"2".repeat(64)}`,
    reference: { id: referenceId, raw_text: "B et al. DOI: 10.1234/b", doi: "10.1234/b", year: 2024 },
    mentions: [{
      id: mentionId,
      marker: "[7]",
      exact_quote: "Our result agrees with B [7].",
      context_text: "Our independent experiment agrees with B [7].",
      page: 4,
      group_size: 1,
      candidate_functions: ["result_comparison"],
    }],
    citing_entities: [{
      id: "observation:a",
      kind: "observation",
      text: "A measured the same direction.",
      anchor_ids: ["anchor:a"],
      root_claim_ids: ["claim:a"],
    }],
    cited_entities: [{
      id: "observation:b",
      kind: "observation",
      text: "B measured the stated direction.",
      anchor_ids: ["anchor:b"],
      root_claim_ids: ["claim:b"],
    }],
  };
}

async function receptionEnvelope() {
  const assertions = [{
    id: assertionId,
    mention_id: mentionId,
    attributed_proposition: "B measured the stated direction.",
    function: "result_comparison",
    stance: "supports",
    confirmation_basis: "consistent_with_citing_evidence",
    target_entity_id: "observation:b",
    target_entity_kind: "observation",
    citing_evidence_entity_ids: ["observation:a"],
    confidence: 0.91,
  }];
  const assertionsSha256 = await hashCanonical(assertions);
  return {
    schema_version: "citation-reception/1.0",
    processor_version: "citation-reception-processor/1.0.0",
    citing_snapshot_id: pairRequest().citing_snapshot_id,
    cited_snapshot_id: pairRequest().cited_snapshot_id,
    reference_id: referenceId,
    extraction: {
      run_id: `citation-extraction:sha256:${"d".repeat(64)}`,
      model: "gpt-5.6-luna",
      prompt_version: "citation-extractor/1.0.0",
      attempts: 1,
      assertions_sha256: assertionsSha256,
      assertions,
    },
    verification: {
      run_id: `citation-verification:sha256:${"e".repeat(64)}`,
      model: "gpt-5.6-luna",
      prompt_version: "citation-verifier/1.0.0",
      attempts: 1,
      extraction_sha256: assertionsSha256,
      assessments: [{
        assertion_id: assertionId,
        faithfulness: "faithful",
        confirmation_basis: "consistent_with_citing_evidence",
        independence: "unknown",
        confidence: 0.88,
        evidence_ids: ["observation:a", "observation:b"],
        limitations: ["Dataset independence is not established."],
      }],
    },
  };
}

test("citation source preserves target-specific marker offsets and rejects unknown fields", () => {
  const source = {
    schema_version: "citation-source/1.0",
    extractor_version: "citation-source-extractor/1.0",
    artifact_version_id: `artifact-version:sha256:${"f".repeat(64)}`,
    references: [{
      id: referenceId,
      ordinal: 7,
      label: "7",
      raw_text: "B et al. DOI: 10.1234/B",
      doi: "10.1234/b",
      year: 2024,
      source_block_id: "block:reference",
      page: 9,
    }],
    mentions: [{
      id: mentionId,
      reference_id: referenceId,
      marker: "[7]",
      exact_quote: "Our result agrees with B [7].",
      context_text: "Our result agrees with B [7].",
      source_block_id: "block:result",
      context_block_ids: ["block:result"],
      page: 4,
      bbox: null,
      char_start: 25,
      char_end: 28,
      group_size: 1,
      candidate_functions: ["result_comparison"],
    }],
    warnings: [],
  };
  const parsed = parseCitationSourceProjection(source);
  assert.equal(parsed.mentions[0].referenceId, referenceId);
  assert.equal(parsed.references[0].doi, "10.1234/b");
  assert.throws(() => parseCitationSourceProjection({ ...source, invented: true }), /closed shape/u);
});

test("citation reception is hash-bound to the submitted A/B pair and independent verifier", async () => {
  const parsed = await parseCitationReceptionEnvelope(await receptionEnvelope(), pairRequest());
  assert.equal(parsed.extraction.assertions[0].targetEntityId, "observation:b");
  assert.equal(parsed.verification.assessments[0].independence, "unknown");

  const tampered = await receptionEnvelope();
  tampered.extraction.assertions[0].attributed_proposition = "A broader claim not present in the frozen output.";
  await assert.rejects(parseCitationReceptionEnvelope(tampered, pairRequest()), /hash/u);
});

test("the active contract keeps verifier downgrades coherent with the frozen stance", async () => {
  const request = pairRequest();
  const envelope = await receptionEnvelope();
  envelope.processor_version = "citation-reception-processor/1.0.0";
  envelope.extraction.prompt_version = "citation-extractor/1.0.0";
  envelope.verification.prompt_version = "citation-verifier/1.0.0";
  envelope.verification.assessments[0].confirmation_basis = "mere_attribution";

  await assert.rejects(
    parseCitationReceptionEnvelope(envelope, request),
    /basis does not match stance/u,
  );

  envelope.verification.assessments[0].confirmation_basis = "verbal_endorsement";
  const parsed = await parseCitationReceptionEnvelope(envelope, request);
  assert.equal(parsed.verification.assessments[0].confirmationBasis, "verbal_endorsement");
});

test("the active extractor rejects mere result reporting", async () => {
  const envelope = await receptionEnvelope();
  envelope.processor_version = "citation-reception-processor/1.0.0";
  envelope.extraction.prompt_version = "citation-extractor/1.0.0";
  envelope.verification.prompt_version = "citation-verifier/1.0.0";
  envelope.extraction.assertions[0].stance = "unclear";
  envelope.extraction.assertions[0].confirmation_basis = "mere_attribution";
  envelope.extraction.assertions_sha256 = await hashCanonical(envelope.extraction.assertions);
  envelope.verification.extraction_sha256 = envelope.extraction.assertions_sha256;
  envelope.verification.assessments[0].confirmation_basis = "mere_attribution";

  await assert.rejects(
    parseCitationReceptionEnvelope(envelope, pairRequest()),
    /non-evaluative citation attribution/u,
  );
});

test("the active contract rejects targetless evidence-backed reception", async () => {
  const envelope = await receptionEnvelope();
  envelope.processor_version = "citation-reception-processor/1.0.0";
  envelope.extraction.prompt_version = "citation-extractor/1.0.0";
  envelope.verification.prompt_version = "citation-verifier/1.0.0";
  envelope.extraction.assertions[0].target_entity_id = null;
  envelope.extraction.assertions[0].target_entity_kind = null;
  envelope.extraction.assertions_sha256 = await hashCanonical(envelope.extraction.assertions);
  envelope.verification.extraction_sha256 = envelope.extraction.assertions_sha256;

  await assert.rejects(
    parseCitationReceptionEnvelope(envelope, pairRequest()),
    /requires a B target/u,
  );
});

test("strong confirmation cannot select a non-evidentiary A entity", async () => {
  const request = pairRequest();
  request.citing_entities[0].kind = "atomic_claim";
  await assert.rejects(
    parseCitationReceptionEnvelope(await receptionEnvelope(), request),
    /invalid A evidence/u,
  );
});

test("graph candidates retain Observation targets and their owning root Claim", () => {
  const graph = {
    artifacts: [], artifact_versions: [], assumptions: [], assessments: [], snapshots: [],
    anchors: [{
      kind: "source_anchor", id: "anchor:b", artifact_version_id: "version:b", anchor_type: "text",
      selector: { page: 1, section: null, paragraph: null, table: null, row_start: null, row_end: null,
        row_range_label: null, column_names: [], figure: null, series: null, char_start: null, char_end: null,
        region: null, region_label: null, dataset_path: null },
      quote: "Measured value", quote_sha256: null,
    }],
    visual_evidence: [],
    observations: [{ kind: "observation", id: "observation:b", source_id: null, statement: "Measured value",
      observation_type: "measurement", value: null, anchor_ids: ["anchor:b"] }],
    reasoning: [],
    claims: [{ kind: "atomic_claim", id: "claim:b", source_id: null, proposition: "Target claim", claim_type: "descriptive",
      polarity: "affirmed", qualifiers: [], anchor_ids: ["anchor:b"], proposition_key: `proposition:sha256:${"3".repeat(64)}`,
      scoped_claim_id: "claim:b", scope: { location: [], time: null, formation: [], sample_type: [], method: [], condition: [], measurement: [], uncertainty: null, entities: [] }, modality: "asserted" }],
    edges: [{ kind: "edge", id: "edge:one", relation: "grounds", source_id: "observation:b", target_id: "claim:b", rationale: null, confidence: 1, scope_overlap: null }],
  };
  const candidates = buildCitationCandidateEntities(graph);
  assert.deepEqual(candidates.find((item) => item.id === "observation:b").root_claim_ids, ["claim:b"]);
});
