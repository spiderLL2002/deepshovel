import type { CompilerModel } from "../compiler/model.ts";
import type { AnchorRegion } from "../evidence-graph/index.ts";

export const CITATION_SOURCE_SCHEMA_VERSION = "citation-source/1.0" as const;
export const CITATION_SOURCE_EXTRACTOR_VERSION = "citation-source-extractor/1.0" as const;
export const CITATION_RECEPTION_SCHEMA_VERSION = "citation-reception/1.0" as const;
export const CITATION_RECEPTION_PROCESSOR_VERSION = "citation-reception-processor/1.0.0" as const;
export const CITATION_EXTRACTION_PROMPT_VERSION = "citation-extractor/1.0.0" as const;
export const CITATION_VERIFICATION_PROMPT_VERSION = "citation-verifier/1.0.0" as const;
export type CitationReceptionProcessorVersion = typeof CITATION_RECEPTION_PROCESSOR_VERSION;
export type CitationExtractionPromptVersion = typeof CITATION_EXTRACTION_PROMPT_VERSION;
export type CitationVerificationPromptVersion = typeof CITATION_VERIFICATION_PROMPT_VERSION;

export type CitationFunction = "result_comparison" | "replication" | "critique";
export type CitationEntityKind = "source_anchor" | "structured_visual_evidence" | "observation" | "reasoning" | "atomic_claim";
export type CitationStance = "supports" | "contradicts" | "qualifies" | "unclear";
export type CitationConfirmationBasis =
  | "mere_attribution"
  | "verbal_endorsement"
  | "verbal_critique"
  | "consistent_with_citing_evidence"
  | "contradicted_by_citing_evidence"
  | "qualified_by_citing_evidence"
  | "direct_replication"
  | "reanalysis"
  | "meta_analytic_synthesis";
export type CitationFaithfulness = "faithful" | "partial" | "overgeneralized" | "contradicted_by_target" | "not_found" | "ambiguous" | "indirect";
export type CitationIndependence = "independent" | "shared_authors" | "shared_data" | "derived_data" | "unknown";

export type CitationReferenceSource = {
  id: string;
  ordinal: number;
  label: string;
  rawText: string;
  doi: string | null;
  year: number | null;
  sourceBlockId: string;
  page: number;
};

export type CitationMentionSource = {
  id: string;
  referenceId: string;
  marker: string;
  exactQuote: string;
  contextText: string;
  sourceBlockId: string;
  contextBlockIds: string[];
  page: number;
  bbox: Omit<AnchorRegion, "unit"> | null;
  charStart: number;
  charEnd: number;
  groupSize: number;
  candidateFunctions: CitationFunction[];
};

export type CitationSourceProjection = {
  schemaVersion: typeof CITATION_SOURCE_SCHEMA_VERSION;
  extractorVersion: typeof CITATION_SOURCE_EXTRACTOR_VERSION;
  artifactVersionId: string;
  references: CitationReferenceSource[];
  mentions: CitationMentionSource[];
  warnings: string[];
};

export type CitationCandidateEntity = {
  id: string;
  kind: CitationEntityKind;
  text: string;
  anchor_ids: string[];
  root_claim_ids: string[];
};

export type CitationPairRequest = {
  schema_version: typeof CITATION_RECEPTION_SCHEMA_VERSION;
  project_id: string;
  citing_snapshot_id: string;
  cited_snapshot_id: string;
  reference: {
    id: string;
    raw_text: string;
    doi: string | null;
    year: number | null;
  };
  mentions: Array<{
    id: string;
    marker: string;
    exact_quote: string;
    context_text: string;
    page: number;
    group_size: number;
    candidate_functions: CitationFunction[];
  }>;
  citing_entities: CitationCandidateEntity[];
  cited_entities: CitationCandidateEntity[];
};

export type CitationAssertion = {
  id: string;
  mentionId: string;
  attributedProposition: string;
  function: CitationFunction;
  stance: CitationStance;
  confirmationBasis: CitationConfirmationBasis;
  targetEntityId: string | null;
  targetEntityKind: CitationEntityKind | null;
  citingEvidenceEntityIds: string[];
  confidence: number;
};

export type CitationAssessment = {
  assertionId: string;
  faithfulness: CitationFaithfulness;
  confirmationBasis: CitationConfirmationBasis;
  independence: CitationIndependence;
  confidence: number;
  evidenceIds: string[];
  limitations: string[];
};

export type CitationReceptionEnvelope = {
  schemaVersion: typeof CITATION_RECEPTION_SCHEMA_VERSION;
  processorVersion: CitationReceptionProcessorVersion;
  citingSnapshotId: string;
  citedSnapshotId: string;
  referenceId: string;
  extraction: {
    runId: string;
    model: CompilerModel;
    promptVersion: CitationExtractionPromptVersion;
    attempts: number;
    assertionsSha256: string;
    assertions: CitationAssertion[];
  };
  verification: {
    runId: string;
    model: CompilerModel;
    promptVersion: CitationVerificationPromptVersion;
    attempts: number;
    extractionSha256: string;
    assessments: CitationAssessment[];
  } | null;
};
