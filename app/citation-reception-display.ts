import type {
  CitationReceptionItemDto,
  CitationReceptionSummaryDto,
} from "./workspace-types";

export type CitationReceptionTone = "positive" | "caution" | "negative" | "neutral";
export type CitationReceptionBucket =
  | "independent_replication"
  | "independent_consistency"
  | "synthesis"
  | "verbal_endorsement"
  | "qualification"
  | "contradiction"
  | "needs_review";

const MIN_DISPLAY_CONFIDENCE = 0.7;

const FAITHFULNESS_LABELS: Record<CitationReceptionItemDto["assessment"]["faithfulness"], string> = {
  faithful: "转述忠实",
  partial: "仅部分对应原文",
  overgeneralized: "存在过度概括",
  contradicted_by_target: "与被引原文冲突",
  not_found: "未找到被引依据",
  ambiguous: "被引目标不明确",
  indirect: "间接引用",
};

const INDEPENDENCE_LABELS: Record<CitationReceptionItemDto["assessment"]["independence"], string> = {
  independent: "独立证据",
  shared_authors: "作者有重叠",
  shared_data: "共用数据",
  derived_data: "使用衍生数据",
  unknown: "独立性未知",
};

const CONFIRMATION_LABELS: Record<CitationReceptionItemDto["assessment"]["confirmationMode"], string> = {
  verbal_endorsement: "文字层面认可",
  verbal_critique: "文字质疑",
  consistent_with_citing_evidence: "引用论文的结果一致",
  contradicted_by_citing_evidence: "引用论文的结果相反",
  qualified_by_citing_evidence: "引用论文的结果构成限定",
  direct_replication: "直接复现",
  reanalysis: "重新分析",
  meta_analytic_synthesis: "综合证据",
};

function confirmationLabel(item: CitationReceptionItemDto) {
  if (item.assessment.confirmationMode !== "consistent_with_citing_evidence") {
    return CONFIRMATION_LABELS[item.assessment.confirmationMode];
  }
  if (item.assessment.stance === "contradicts") return "引用论文的结果相反";
  if (item.assessment.stance === "qualifies") return "引用论文的结果构成限定";
  return CONFIRMATION_LABELS.consistent_with_citing_evidence;
}

function finiteProbability(value: number) {
  return Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0;
}

export function confirmationNeedsOwnEvidence(
  mode: CitationReceptionItemDto["assessment"]["confirmationMode"],
) {
  return mode === "consistent_with_citing_evidence"
    || mode === "contradicted_by_citing_evidence"
    || mode === "qualified_by_citing_evidence"
    || mode === "direct_replication"
    || mode === "reanalysis"
    || mode === "meta_analytic_synthesis";
}

export function citationReceptionPresentation(item: CitationReceptionItemDto) {
  const { assessment } = item;
  const confidence = finiteProbability(assessment.confidence);
  const alignmentConfidence = finiteProbability(item.target.alignmentConfidence);
  const missingOwnEvidence = confirmationNeedsOwnEvidence(assessment.confirmationMode)
    && item.confirmingEvidence === null;
  const unverified = assessment.faithfulness !== "faithful"
    || confidence < MIN_DISPLAY_CONFIDENCE
    || alignmentConfidence < MIN_DISPLAY_CONFIDENCE
    || missingOwnEvidence;

  let bucket: CitationReceptionBucket = "needs_review";
  let verdictLabel = "引用关系需核对";
  let tone: CitationReceptionTone = "caution";

  if (assessment.stance === "contradicts") {
    bucket = "contradiction";
    verdictLabel = assessment.confirmationMode === "direct_replication" && assessment.independence === "independent"
      ? "独立复现：结果矛盾"
      : "后续研究结果相反";
    tone = "negative";
  } else if (assessment.stance === "qualifies") {
    bucket = "qualification";
    verdictLabel = "限定适用范围";
    tone = "caution";
  } else if (assessment.stance === "supports") {
    if (assessment.confirmationMode === "direct_replication" && assessment.independence === "independent") {
      bucket = "independent_replication";
      verdictLabel = "独立直接复现";
      tone = "positive";
    } else if (assessment.confirmationMode === "consistent_with_citing_evidence" && assessment.independence === "independent") {
      bucket = "independent_consistency";
      verdictLabel = "独立结果一致";
      tone = "positive";
    } else if (assessment.confirmationMode === "reanalysis") {
      bucket = "synthesis";
      verdictLabel = assessment.independence === "independent" ? "独立再分析结果一致" : "再分析结果一致";
      tone = assessment.independence === "independent" ? "positive" : "caution";
    } else if (assessment.confirmationMode === "meta_analytic_synthesis") {
      bucket = "synthesis";
      verdictLabel = "综合证据支持";
      tone = "positive";
    } else if (assessment.confirmationMode === "verbal_endorsement") {
      bucket = "verbal_endorsement";
      verdictLabel = "文字层面认可";
      tone = "neutral";
    } else if (assessment.confirmationMode === "direct_replication") {
      bucket = "needs_review";
      verdictLabel = "复现实验（独立性有限）";
      tone = "caution";
    } else if (assessment.confirmationMode === "consistent_with_citing_evidence") {
      bucket = "needs_review";
      verdictLabel = "结果一致（独立性有限）";
      tone = "caution";
    }
  } else if (assessment.stance === "unclear") {
    verdictLabel = "引用立场不明确";
    tone = "caution";
  }

  if (unverified) {
    bucket = "needs_review";
    if (assessment.faithfulness === "contradicted_by_target" || assessment.faithfulness === "not_found") {
      verdictLabel = "转述未获原文支持";
      tone = "negative";
    } else if (missingOwnEvidence) {
      verdictLabel = "确认依据仍需核对";
      tone = "caution";
    } else {
      verdictLabel = "引用关系需核对";
      tone = "caution";
    }
  }

  const boundaryReasons = [
    assessment.faithfulness !== "faithful" ? FAITHFULNESS_LABELS[assessment.faithfulness] : null,
    alignmentConfidence < MIN_DISPLAY_CONFIDENCE ? "与本篇具体结果的对应关系仍不稳定" : null,
    confidence < MIN_DISPLAY_CONFIDENCE ? "综合判断置信度较低" : null,
    missingOwnEvidence ? "尚未提供引用论文自身的验证证据链" : null,
    assessment.limitationCount > 0 ? `${assessment.limitationCount} 项边界条件仍需对照原文核验` : null,
  ].filter((value): value is string => Boolean(value));

  return {
    bucket,
    verdictLabel,
    tone,
    faithfulnessLabel: FAITHFULNESS_LABELS[assessment.faithfulness],
    independenceLabel: INDEPENDENCE_LABELS[assessment.independence],
    confirmationLabel: confirmationLabel(item),
    confidencePercent: Math.round(confidence * 100),
    needsReview: unverified || bucket === "needs_review",
    boundaryGuide: boundaryReasons.join("；"),
  };
}

export function summarizeCitationReceptionItems(
  items: CitationReceptionItemDto[],
): CitationReceptionSummaryDto {
  const summary: CitationReceptionSummaryDto = {
    totalAssertions: items.length,
    distinctCitingPaperFamilies: new Set(items.map((item) => item.citingPaper.paperFamilyId)).size,
    independentReplications: 0,
    independentConsistentFindings: 0,
    reanalysesOrSyntheses: 0,
    verbalEndorsements: 0,
    qualifications: 0,
    contradictions: 0,
    needsReview: 0,
  };
  for (const item of items) {
    const bucket = citationReceptionPresentation(item).bucket;
    if (bucket === "independent_replication") summary.independentReplications += 1;
    else if (bucket === "independent_consistency") summary.independentConsistentFindings += 1;
    else if (bucket === "synthesis") summary.reanalysesOrSyntheses += 1;
    else if (bucket === "verbal_endorsement") summary.verbalEndorsements += 1;
    else if (bucket === "qualification") summary.qualifications += 1;
    else if (bucket === "contradiction") summary.contradictions += 1;
    else summary.needsReview += 1;
  }
  return summary;
}

export function citationReceptionBucketOrder(bucket: CitationReceptionBucket) {
  return ({
    contradiction: 0,
    independent_replication: 1,
    independent_consistency: 2,
    synthesis: 3,
    qualification: 4,
    verbal_endorsement: 5,
    needs_review: 6,
  } satisfies Record<CitationReceptionBucket, number>)[bucket];
}
