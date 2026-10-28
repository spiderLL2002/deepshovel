export type AssessmentVerdict =
  | "supported"
  | "contradicted"
  | "qualified"
  | "uncertain"
  | "insufficient_evidence";

const VERDICT_LABEL: Record<AssessmentVerdict, string> = {
  supported: "证据支持",
  contradicted: "存在反证",
  qualified: "有条件支持",
  uncertain: "结论不确定",
  insufficient_evidence: "证据不足",
};

const VERDICT_TONE: Record<AssessmentVerdict, "positive" | "caution" | "negative"> = {
  supported: "positive",
  contradicted: "negative",
  qualified: "caution",
  uncertain: "caution",
  insufficient_evidence: "negative",
};

export function assessmentPresentation(assessment: {
  verdict: AssessmentVerdict;
  confidence: number;
  limitations: string[];
}) {
  return {
    verdictLabel: VERDICT_LABEL[assessment.verdict],
    tone: VERDICT_TONE[assessment.verdict],
    confidencePercent: Math.round(assessment.confidence * 100),
    guide: "相关原文、观察结果与推理过程列于下方。",
    limitationCount: assessment.limitations.length,
    boundaryGuide: assessment.limitations.length
      ? "部分边界条件仍需回到原件核对，详见下方证据链。"
      : null,
  };
}
