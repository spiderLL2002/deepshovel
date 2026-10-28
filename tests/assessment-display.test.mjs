import assert from "node:assert/strict";
import test from "node:test";

import {
  assessmentPresentation,
} from "../app/assessment-display.ts";

const INTERNAL_OR_CONTRACT_WORD = /anchor\.text|anchor:compiler|visual\.table|obs\.\d+|reason\.\d+|\b(?:supported|contradicted|qualified|uncertain|insufficient_evidence)\b/iu;

test("assessment presentation never forwards raw rationale or contract vocabulary", () => {
  const display = assessmentPresentation({
    verdict: "qualified",
    confidence: 0.78,
    rationale: "supported by anchor.text.p7conclusion and anchor:compiler:1; qualified by obs.2",
    limitations: [
      "MinerU caption bbox at anchor.text.p7conclusion is qualified.",
      "包围盒 visual.table.5 与 obs.2 需要复核，supported 仅表示本篇证据支持。",
      "需核对 series.1、axis.x.1 与 paper:example。",
    ],
  });
  const serialized = JSON.stringify(display);

  assert.equal(Object.hasOwn(display, "rationale"), false);
  assert.equal(display.verdictLabel, "有条件支持");
  assert.equal(display.confidencePercent, 78);
  assert.equal(display.guide, "相关原文、观察结果与推理过程列于下方。");
  assert.equal(display.limitationCount, 3);
  assert.equal(display.boundaryGuide, "部分边界条件仍需回到原件核对，详见下方证据链。");
  assert.doesNotMatch(serialized, INTERNAL_OR_CONTRACT_WORD);
  assert.doesNotMatch(serialized, /MinerU|caption|bbox|包围盒|series\.1|axis\.x\.1|paper:example/iu);
});

test("assessment presentation omits all model limitation text even without recognizable ID prefixes", () => {
  const display = assessmentPresentation({
    verdict: "uncertain",
    confidence: 0.5,
    limitations: ["任意内部标识 C1、nodeWithoutPrefix 和 graphThing42 均不得显示。"],
  });
  const serialized = JSON.stringify(display);
  assert.equal(display.limitationCount, 1);
  assert.doesNotMatch(serialized, /C1|nodeWithoutPrefix|graphThing42/u);
});
