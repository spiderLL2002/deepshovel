import {
  CLAIM_MODALITIES,
  CLAIM_POLARITIES,
  CLAIM_TYPES,
  EVIDENCE_GRAPH_SCHEMA_VERSION,
  REASONING_CONCLUSION_RELATION_NAMES,
} from "../shared-contract.generated.ts";

// Shared with the Compiler through contracts/shared-contract.json.
export {
  CLAIM_MODALITIES,
  CLAIM_POLARITIES,
  CLAIM_TYPES,
  EVIDENCE_GRAPH_SCHEMA_VERSION,
  NORMALIZED_REGION_EXTENT_MAX,
} from "../shared-contract.generated.ts";

export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
export type ArtifactType = "paper" | "figure" | "table" | "dataset" | "supplement" | "other";
export type VisualKind = "figure" | "table";
export type ObservationType = "measurement" | "trend" | "pattern" | "comparison" | "correlation" | "source_assertion";
export type ReasoningType = "deductive" | "inductive" | "abductive" | "causal_inference" | "statistical_inference" | "analogy" | "synthesis" | "other";
export type AssumptionType = "background_knowledge" | "methodological" | "measurement" | "statistical" | "causal" | "scope" | "generalization";
export type ClaimType = (typeof CLAIM_TYPES)[number];
export type ClaimPolarity = (typeof CLAIM_POLARITIES)[number];
export type ClaimModality = (typeof CLAIM_MODALITIES)[number];
export type AssessmentVerdict = "supported" | "contradicted" | "qualified" | "uncertain" | "insufficient_evidence";
export type ScopeOverlap = "partial" | "source_within_target" | "target_within_source" | "equivalent";

export interface ScopeTime {
  label: string | null;
  start: number | null;
  end: number | null;
  unit: string | null;
}

export interface Scope {
  location: string[];
  time: ScopeTime | null;
  formation: string[];
  sample_type: string[];
  method: string[];
  condition: string[];
  measurement: string[];
  uncertainty: string | null;
  entities: string[];
}

export interface Artifact {
  kind: "artifact";
  id: string;
  canonical_name: string;
  artifact_type: ArtifactType;
}

export interface ArtifactVersion {
  kind: "artifact_version";
  id: string;
  artifact_id: string;
  version: string;
  locator: string | null;
  sha256: string;
  media_type: string;
}

export interface AnchorRegion {
  x: number;
  y: number;
  width: number;
  height: number;
  unit: "normalized" | "pixel";
}

export interface AnchorSelector {
  page: number | null;
  section: string | null;
  paragraph: number | null;
  table: string | null;
  row_start: number | null;
  row_end: number | null;
  row_range_label: string | null;
  column_names: string[];
  figure: string | null;
  series: string | null;
  char_start: number | null;
  char_end: number | null;
  region: AnchorRegion | null;
  region_label: string | null;
  dataset_path: string | null;
}

export interface SourceAnchor {
  kind: "source_anchor";
  id: string;
  artifact_version_id: string;
  anchor_type: "text" | "figure" | "table" | "dataset" | "metadata" | "citation";
  selector: AnchorSelector;
  quote: string | null;
  quote_sha256: string | null;
}

export interface VisualAxis {
  id: string;
  direction: "x" | "y";
  label: string;
  unit: string | null;
  scale: "linear" | "log" | "categorical";
}

export interface VisualPoint { x: number | string; y: number | string; uncertainty: number | null }
export interface VisualSeries { id: string; label: string; x_axis_id: string; y_axis_id: string; points: VisualPoint[] }
export interface TableCell { row: number; column: string; raw_value: string; numeric_value: number | null; unit: string | null }
export interface VisualFeature {
  kind: "trend" | "peak" | "trough" | "plateau" | "change_point" | "uncertainty";
  description: string;
  series_id: string | null;
  value: number | null;
  unit: string | null;
}

export interface StructuredVisualEvidence {
  kind: "structured_visual_evidence";
  id: string;
  anchor_id: string;
  visual_kind: VisualKind;
  axes: VisualAxis[];
  series: VisualSeries[];
  cells: TableCell[];
  features: VisualFeature[];
}

export interface ObservationValue {
  raw: string;
  numeric: number | null;
  comparator: "<" | "<=" | ">" | ">=" | null;
  unit: string | null;
  uncertainty: number | null;
}

export interface Observation {
  kind: "observation";
  id: string;
  statement: string;
  observation_type: ObservationType;
  anchor_ids: string[];
  scope: Scope;
  value: ObservationValue | null;
}

export interface Assumption {
  kind: "assumption";
  id: string;
  statement: string;
  assumption_type: AssumptionType;
  explicit: boolean;
  anchor_ids: string[];
  scope: Scope;
}

export interface Reasoning {
  kind: "reasoning";
  id: string;
  statement: string;
  reasoning_type: ReasoningType;
  anchor_ids: string[];
  scope: Scope;
  limitations: string[];
}

export interface AtomicClaim {
  kind: "atomic_claim";
  /** Canonical graph endpoint identity; always equals scoped_claim_id. */
  id: string;
  /** Extractor-local identity retained only for audit and edge remapping. */
  source_id: string | null;
  proposition: string;
  claim_type: ClaimType;
  polarity: ClaimPolarity;
  qualifiers: string[];
  anchor_ids: string[];
  /** Scope-free identity of normalized proposition content. */
  proposition_key: string;
  /** Identity of proposition_key plus canonical scientific scope. */
  scoped_claim_id: string;
  scope: Scope;
  modality: ClaimModality;
}

export type EvidenceEntity = Artifact | ArtifactVersion | SourceAnchor | StructuredVisualEvidence | Observation | Assumption | Reasoning | AtomicClaim;

export type EvidenceEdgeRelation =
  | "grounds"
  | "premise_for"
  | "assumption_for"
  | "infers"
  | "equivalent_to"
  | "supports"
  | "contradicts"
  | "refines"
  | "qualifies"
  | "supplements";

export interface EvidenceEdge {
  kind: "edge";
  id: string;
  relation: EvidenceEdgeRelation;
  source_id: string;
  target_id: string;
  rationale: string | null;
  confidence: number | null;
  /** Required only when both endpoints are claims. */
  scope_overlap: ScopeOverlap | null;
}

export interface Assessor { source: "human" | "model"; name: string; model: string | null }
export interface Assessment {
  kind: "assessment";
  id: string;
  target_claim_id: string;
  verdict: AssessmentVerdict;
  basis_edge_ids: string[];
  evidence_ids: string[];
  rationale: string;
  limitations: string[];
  confidence: number;
  assessor: Assessor;
  created_at: string;
}

export interface SnapshotGenerator { source: "human" | "extractor" | "verifier" | "migration"; name: string; version: string }
export interface Snapshot {
  kind: "snapshot";
  id: string;
  parent_snapshot_id: string | null;
  entity_ids: string[];
  edge_ids: string[];
  assessment_ids: string[];
  content_sha256: string;
  created_at: string;
  generator: SnapshotGenerator;
}

export interface EvidenceGraph {
  schema_version: typeof EVIDENCE_GRAPH_SCHEMA_VERSION;
  paper_id: string;
  artifacts: Artifact[];
  artifact_versions: ArtifactVersion[];
  anchors: SourceAnchor[];
  visual_evidence: StructuredVisualEvidence[];
  observations: Observation[];
  assumptions: Assumption[];
  reasoning: Reasoning[];
  claims: AtomicClaim[];
  edges: EvidenceEdge[];
  assessments: Assessment[];
  snapshots: Snapshot[];
}

export interface EvidencePath {
  root_claim_id: string;
  entities: EvidenceEntity[];
  edges: EvidenceEdge[];
  assessments: Assessment[];
  truncated: boolean;
}

export const ENTITY_COLLECTIONS = ["artifacts", "artifact_versions", "anchors", "visual_evidence", "observations", "assumptions", "reasoning", "claims"] as const;
export const CLAIM_RELATIONS = new Set<EvidenceEdgeRelation>(["equivalent_to", "supports", "contradicts", "refines", "qualifies", "supplements"]);
export const BASE_DERIVATION_RELATIONS = new Set<EvidenceEdgeRelation>(["grounds", "premise_for", "assumption_for", "infers"]);
/**
 * Reasoning -> Claim conclusions. `infers` is a supporting conclusion; a
 * contradicting or qualifying conclusion keeps its own relation. Graphs
 * compiled before 2026-10 store every conclusion as `infers`.
 */
export const REASONING_CONCLUSION_RELATIONS = new Set<EvidenceEdgeRelation>(REASONING_CONCLUSION_RELATION_NAMES);

export function isReasoningConclusion(edge: EvidenceEdge, entities: Map<string, EvidenceEntity>) {
  return REASONING_CONCLUSION_RELATIONS.has(edge.relation)
    && entities.get(edge.source_id)?.kind === "reasoning"
    && entities.get(edge.target_id)?.kind === "atomic_claim";
}

export function isClaimComparison(edge: EvidenceEdge, entities: Map<string, EvidenceEntity>) {
  return CLAIM_RELATIONS.has(edge.relation)
    && entities.get(edge.source_id)?.kind === "atomic_claim"
    && entities.get(edge.target_id)?.kind === "atomic_claim";
}

export function isDerivationEdge(edge: EvidenceEdge, entities: Map<string, EvidenceEntity>) {
  return BASE_DERIVATION_RELATIONS.has(edge.relation) || (CLAIM_RELATIONS.has(edge.relation) && !isClaimComparison(edge, entities));
}
