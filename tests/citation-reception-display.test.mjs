import assert from "node:assert/strict";
import test from "node:test";

import {
  citationReceptionPresentation,
  summarizeCitationReceptionItems,
} from "../app/citation-reception-display.ts";

function item(overrides = {}) {
  const assessment = {
    intent: ["result_report"],
    stance: "supports",
    confirmationMode: "verbal_endorsement",
    independence: "unknown",
    faithfulness: "faithful",
    confidence: 0.9,
    limitationCount: 0,
    ...overrides.assessment,
  };
  return {
    id: overrides.id ?? "citation-assertion:test",
    target: {
      entityId: "observation:test",
      entityKind: "observation",
      rootClaimIds: ["claim:test"],
      statement: "The reported value increased.",
      alignmentConfidence: 0.9,
      ...overrides.target,
    },
    citingPaper: {
      snapshotId: "snapshot:test",
      paperId: "paper:test",
      paperFamilyId: overrides.paperFamilyId ?? "artifact:test",
      title: "Citing paper",
      authors: ["A. Author"],
      year: 2026,
    },
    attributedProposition: "The earlier paper reported an increase.",
    marker: "[12]",
    mentionCount: 1,
    citationContext: { statement: "Context", source: {} },
    confirmingEvidence: overrides.confirmingEvidence ?? null,
    assessment,
  };
}

test("a verbal endorsement is never presented as independent confirmation", () => {
  const display = citationReceptionPresentation(item());
  assert.equal(display.bucket, "verbal_endorsement");
  assert.equal(display.verdictLabel, "文字层面认可");
  assert.doesNotMatch(JSON.stringify(display), /独立直接复现|独立结果一致/u);
});

test("a strong confirmation mode without its own evidence is downgraded for review", () => {
  const display = citationReceptionPresentation(item({
    assessment: {
      confirmationMode: "direct_replication",
      independence: "independent",
    },
  }));
  assert.equal(display.bucket, "needs_review");
  assert.equal(display.verdictLabel, "确认依据仍需核对");
  assert.match(display.boundaryGuide, /尚未提供引用论文自身的验证证据链/u);
});

test("an independently evidenced replication is counted separately from prose support", () => {
  const replicated = item({
    id: "citation-assertion:replicated",
    confirmingEvidence: { statement: "Our experiment reproduced the increase.", source: {} },
    assessment: {
      confirmationMode: "direct_replication",
      independence: "independent",
    },
  });
  const display = citationReceptionPresentation(replicated);
  const summary = summarizeCitationReceptionItems([replicated, item({ id: "citation-assertion:prose" })]);

  assert.equal(display.bucket, "independent_replication");
  assert.equal(display.verdictLabel, "独立直接复现");
  assert.equal(summary.independentReplications, 1);
  assert.equal(summary.verbalEndorsements, 1);
});

test("the versioned citing-evidence basis maps to independent consistency", () => {
  const consistent = item({
    confirmingEvidence: { statement: "Our measurements were consistent.", source: {} },
    assessment: {
      confirmationMode: "consistent_with_citing_evidence",
      independence: "independent",
    },
  });
  const display = citationReceptionPresentation(consistent);
  const summary = summarizeCitationReceptionItems([consistent]);
  assert.equal(display.bucket, "independent_consistency");
  assert.equal(display.verdictLabel, "独立结果一致");
  assert.equal(summary.independentConsistentFindings, 1);
});

test("a contradictory independent replication keeps its negative result", () => {
  const display = citationReceptionPresentation(item({
    confirmingEvidence: { statement: "The replication found no increase.", source: {} },
    assessment: {
      stance: "contradicts",
      confirmationMode: "direct_replication",
      independence: "independent",
    },
  }));
  assert.equal(display.bucket, "contradiction");
  assert.equal(display.tone, "negative");
  assert.equal(display.verdictLabel, "独立复现：结果矛盾");
});

test("the legacy citing-evidence mode is worded by stance, not always as agreement", () => {
  const contradicted = citationReceptionPresentation(item({
    confirmingEvidence: { statement: "A found the opposite result.", source: {} },
    assessment: {
      stance: "contradicts",
      confirmationMode: "consistent_with_citing_evidence",
    },
  }));
  const qualified = citationReceptionPresentation(item({
    id: "citation-assertion:qualified",
    confirmingEvidence: { statement: "A found a narrower scope.", source: {} },
    assessment: {
      stance: "qualifies",
      confirmationMode: "consistent_with_citing_evidence",
    },
  }));
  assert.equal(contradicted.confirmationLabel, "引用论文的结果相反");
  assert.equal(qualified.confirmationLabel, "引用论文的结果构成限定");
});

test("unfaithful or low-confidence alignment never enters confirmation counts", () => {
  const unfaithful = item({ assessment: { faithfulness: "overgeneralized" } });
  const uncertainTarget = item({
    id: "citation-assertion:uncertain-target",
    target: { alignmentConfidence: 0.4 },
  });
  const summary = summarizeCitationReceptionItems([unfaithful, uncertainTarget]);
  const serialized = JSON.stringify(citationReceptionPresentation(unfaithful));

  assert.equal(summary.needsReview, 2);
  assert.equal(summary.independentReplications, 0);
  assert.match(serialized, /存在过度概括/u);
  assert.doesNotMatch(serialized, /anchor:|observation:test|citation-assertion/u);
});

test("distinct paper-family counts do not grow with repeated assertions from one paper", () => {
  const summary = summarizeCitationReceptionItems([
    item({ id: "citation-assertion:first", paperFamilyId: "artifact:same" }),
    item({ id: "citation-assertion:second", paperFamilyId: "artifact:same" }),
  ]);
  assert.equal(summary.totalAssertions, 2);
  assert.equal(summary.distinctCitingPaperFamilies, 1);
});
