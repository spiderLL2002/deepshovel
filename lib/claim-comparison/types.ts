import type { CompilerModel } from "../compiler/model.ts";
import type { ClaimModality, ClaimPolarity, ClaimType, Scope } from "../evidence-graph/index.ts";

export const CLAIM_COMPARISON_SCHEMA_VERSION = "claim-comparison/1.0" as const;
export const CLAIM_NORMALIZER_VERSION = "claim-normalizer/1.0.0" as const;
export const CLAIM_COMPARISON_PROCESSOR_VERSION = "claim-comparison-processor/1.0.0" as const;
export const CLAIM_NORMALIZER_PROMPT_VERSION = "claim-normalizer/1.0.0" as const;
export const CLAIM_COMPARISON_EXTRACTION_PROMPT_VERSION = "claim-comparison-extractor/1.0.0" as const;
export const CLAIM_COMPARISON_VERIFICATION_PROMPT_VERSION = "claim-comparison-verifier/1.0.0" as const;

export type ClaimComparisonRelation =
  | "equivalent"
  | "supports"
  | "contradicts"
  | "qualifies"
  | "refines"
  | "supplements"
  | "no_relation"
  | "insufficient_to_determine";

export type ClaimComparisonDirection =
  | "query_to_candidate"
  | "candidate_to_query"
  | "bidirectional"
  | "none";

export type ClaimComparisonStance =
  | "supportive"
  | "contradictory"
  | "qualifying"
  | "neutral"
  | "unclear";

export type ClaimComparisonScopeAlignment =
  | "equivalent"
  | "overlap"
  | "query_within_candidate"
  | "candidate_within_query"
  | "disjoint"
  | "unknown";

export type ClaimComparisonDirectness = "direct" | "indirect" | "none" | "insufficient";
export type ClaimComparisonVerdict = "maintained" | "downgraded" | "abstained";
export type ClaimComparisonEvidenceKind =
  | "source_anchor"
  | "structured_visual_evidence"
  | "observation"
  | "reasoning"
  | "assessment_summary";

export type ClaimComparisonEvidenceEntity = {
  id: string;
  kind: ClaimComparisonEvidenceKind;
  text: string;
  anchor_ids: string[];
  root_claim_ids: string[];
};

export type ClaimComparisonSide = {
  claim_id: string;
  canonical_claim: string;
  claim_type: ClaimType;
  polarity: ClaimPolarity;
  modality: ClaimModality;
  scope: Scope;
  source_snapshot_id: string | null;
  source_paper_id: string | null;
  source_paper_family_id: string | null;
  evidence_entities: ClaimComparisonEvidenceEntity[];
};

export type ClaimComparisonPair = {
  id: string;
  query: ClaimComparisonSide;
  candidate: ClaimComparisonSide;
};

export type ClaimComparisonAnalyzeRequest = {
  schema_version: typeof CLAIM_COMPARISON_SCHEMA_VERSION;
  project_id: string;
  pairs: ClaimComparisonPair[];
};

export type ClaimComparisonNormalizeRequest = {
  schema_version: typeof CLAIM_COMPARISON_SCHEMA_VERSION;
  project_id: string;
  user_text: string;
};

export type NormalizedClaimDraft = {
  id: string;
  originSpan: string;
  canonicalClaim: string;
  claimType: ClaimType;
  polarity: ClaimPolarity;
  modality: ClaimModality;
  scope: Scope;
  status: "ready" | "needs_user_definition";
  clarification: string | null;
};

export type ClaimComparisonNormalization = {
  schemaVersion: typeof CLAIM_COMPARISON_SCHEMA_VERSION;
  processorVersion: typeof CLAIM_NORMALIZER_VERSION;
  status: "ready" | "needs_user_definition";
  clarification: string | null;
  run: {
    runId: string;
    model: CompilerModel;
    promptVersion: typeof CLAIM_NORMALIZER_PROMPT_VERSION;
    attempts: number;
  };
  claims: NormalizedClaimDraft[];
};

export type ClaimComparisonDecision = {
  id: string;
  pairId: string;
  relation: ClaimComparisonRelation;
  direction: ClaimComparisonDirection;
  stance: ClaimComparisonStance;
  scopeAlignment: ClaimComparisonScopeAlignment;
  directness: ClaimComparisonDirectness;
  queryEvidenceIds: string[];
  candidateEvidenceIds: string[];
  rationale: string;
  limitations: string[];
  confidence: number;
};

export type ClaimComparisonAssessment = {
  comparisonId: string;
  pairId: string;
  verdict: ClaimComparisonVerdict;
  relation: ClaimComparisonRelation;
  direction: ClaimComparisonDirection;
  stance: ClaimComparisonStance;
  scopeAlignment: ClaimComparisonScopeAlignment;
  directness: ClaimComparisonDirectness;
  queryEvidenceIds: string[];
  candidateEvidenceIds: string[];
  rationale: string;
  limitations: string[];
  confidence: number;
};

export type ClaimComparisonEnvelope = {
  schemaVersion: typeof CLAIM_COMPARISON_SCHEMA_VERSION;
  processorVersion: typeof CLAIM_COMPARISON_PROCESSOR_VERSION;
  projectId: string;
  extraction: {
    runId: string;
    model: CompilerModel;
    promptVersion: typeof CLAIM_COMPARISON_EXTRACTION_PROMPT_VERSION;
    attempts: number;
    comparisonsSha256: string;
    comparisons: ClaimComparisonDecision[];
  };
  verification: {
    runId: string;
    model: CompilerModel;
    promptVersion: typeof CLAIM_COMPARISON_VERIFICATION_PROMPT_VERSION;
    attempts: number;
    extractionSha256: string;
    assessments: ClaimComparisonAssessment[];
  } | null;
};
