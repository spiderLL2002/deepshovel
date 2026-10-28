import {
  expandEvidencePath,
  type EvidenceEntity,
  type EvidenceGraph,
  type Scope,
} from "../evidence-graph/index.ts";
import type {
  EvidenceRetrievalAssessment,
  EvidenceRetrievalDocument,
} from "../evidence-retrieval/index.ts";
import type {
  ClaimComparisonEvidenceEntity,
  ClaimComparisonEvidenceKind,
  ClaimComparisonSide,
} from "./types.ts";

const MAX_ENTITY_TEXT = 32_000;
const MAX_EVIDENCE_ENTITIES = 256;

function boundedText(value: string) {
  // Cut by code point: half of a surrogate pair is not valid text for the Compiler.
  return Array.from(value.normalize("NFC").replace(/\s+/gu, " ").trim()).slice(0, MAX_ENTITY_TEXT).join("");
}

// A stored Scope rejects only exact repeats and has no count cap, while the
// Compiler's ComparisonScope rejects values equal after NFKC, casefold and
// whitespace folding, and caps its lists. Keep the first of such values.
const COMPARISON_SCOPE_LIST_LIMITS = {
  location: 64, formation: 64, sample_type: 64, method: 64, condition: 64, measurement: 64, entities: 128,
} as const;

function comparisonScopeIdentity(value: string) {
  // JavaScript has no casefold; ß and final sigma are its additions in practice.
  return value.normalize("NFKC").toLowerCase().replace(/[ßẞ]/gu, "ss").replace(/ς/gu, "σ")
    .split(/\s+/u).filter(Boolean).join(" ");
}

export function comparisonScope(scope: Scope): Scope {
  const bounded = { ...scope };
  for (const [field, limit] of Object.entries(COMPARISON_SCOPE_LIST_LIMITS) as [keyof typeof COMPARISON_SCOPE_LIST_LIMITS, number][]) {
    const seen = new Set<string>();
    bounded[field] = scope[field].filter((value) => {
      const identity = comparisonScopeIdentity(value);
      if (seen.has(identity)) return false;
      seen.add(identity);
      return true;
    }).slice(0, limit);
  }
  return bounded;
}

function visualEvidenceText(entity: Extract<EvidenceEntity, { kind: "structured_visual_evidence" }>) {
  const axes = entity.axes.map((axis) => (
    `${axis.direction}-axis ${axis.label}${axis.unit ? ` (${axis.unit})` : ""}, ${axis.scale}`
  ));
  const features = entity.features.map((feature) => feature.description);
  const cells = entity.cells.map((cell) => (
    `row ${cell.row}, ${cell.column}: ${cell.raw_value}${cell.unit ? ` ${cell.unit}` : ""}`
  ));
  const series = entity.series.map((item) => {
    const points = item.points.slice(0, 64).map((point) => (
      `${String(point.x)}→${String(point.y)}${point.uncertainty === null ? "" : ` ±${point.uncertainty}`}`
    ));
    return `${item.label}: ${points.join(", ")}`;
  });
  return boundedText([...axes, ...features, ...cells, ...series].join("; "));
}

function entityProjection(
  entity: EvidenceEntity,
  rootClaimId: string,
): ClaimComparisonEvidenceEntity | null {
  let kind: ClaimComparisonEvidenceKind;
  let text: string;
  let anchorIds: string[];

  if (entity.kind === "source_anchor") {
    kind = "source_anchor";
    text = boundedText(entity.quote ? `Source anchor type=${entity.anchor_type}. Exact text: ${entity.quote}` : "");
    anchorIds = [entity.id];
  } else if (entity.kind === "structured_visual_evidence") {
    kind = "structured_visual_evidence";
    text = boundedText(`Structured ${entity.visual_kind} evidence. ${visualEvidenceText(entity)}`);
    anchorIds = [entity.anchor_id];
  } else if (entity.kind === "observation") {
    kind = "observation";
    text = boundedText(`Observation type=${entity.observation_type}. ${entity.statement}`);
    anchorIds = entity.anchor_ids;
  } else if (entity.kind === "reasoning") {
    kind = "reasoning";
    text = boundedText([
      `Reasoning type=${entity.reasoning_type}.`,
      entity.statement,
      ...entity.limitations.map((limitation) => `Limitation: ${limitation}`),
    ].join(" "));
    anchorIds = entity.anchor_ids;
  } else {
    return null;
  }

  if (!text) return null;
  return {
    id: entity.id,
    kind,
    text,
    anchor_ids: [...new Set(anchorIds)].slice(0, 64),
    root_claim_ids: [rootClaimId],
  };
}

function assessmentProjection(
  assessment: EvidenceRetrievalAssessment,
  rootClaimId: string,
): ClaimComparisonEvidenceEntity | null {
  const details = [
    `Own-paper verifier verdict: ${assessment.verdict}.`,
    assessment.rationale,
    ...assessment.limitations.map((limitation) => `Limitation: ${limitation}`),
  ];
  const text = boundedText(details.join(" "));
  if (!text) return null;
  return {
    id: assessment.assessmentId,
    kind: "assessment_summary",
    text,
    // The retrieval assessment lists heterogeneous path entity IDs, not only
    // SourceAnchor IDs. Keep that provenance in its text instead of mislabelling
    // those identities as direct anchors for the comparison model.
    anchor_ids: [],
    root_claim_ids: [rootClaimId],
  };
}

/**
 * Project only the selected Claim's derivation path into the comparison model.
 * Assumptions and neighbouring Claims are deliberately excluded: neither may be
 * treated as raw evidence for a cross-paper support or contradiction decision.
 */
export function buildClaimComparisonEvidenceEntities(
  graph: EvidenceGraph,
  rootClaimId: string,
  assessment: EvidenceRetrievalAssessment | null = null,
) {
  const path = expandEvidencePath(graph, rootClaimId);
  const entities = path.entities
    .map((entity) => entityProjection(entity, rootClaimId))
    .filter((entity): entity is ClaimComparisonEvidenceEntity => entity !== null);
  const assessmentEntity = assessment === null ? null : assessmentProjection(assessment, rootClaimId);
  if (assessmentEntity && !entities.some((entity) => entity.id === assessmentEntity.id)) {
    entities.push(assessmentEntity);
  }
  return entities.slice(0, MAX_EVIDENCE_ENTITIES);
}

export function buildClaimComparisonSide(
  document: EvidenceRetrievalDocument,
  evidenceEntities: ClaimComparisonEvidenceEntity[],
): ClaimComparisonSide {
  return {
    claim_id: document.claimId,
    canonical_claim: document.canonicalClaim,
    claim_type: document.claimType,
    polarity: document.polarity,
    modality: document.modality,
    scope: comparisonScope(document.scope),
    source_snapshot_id: document.snapshotId,
    source_paper_id: document.paperId,
    source_paper_family_id: document.paperFamilyId,
    evidence_entities: evidenceEntities,
  };
}
