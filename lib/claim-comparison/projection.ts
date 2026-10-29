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
  ClaimComparisonEvidence,
  ClaimComparisonEvidenceKind,
  ClaimComparisonSide,
} from "./types.ts";

const MAX_ENTITY_TEXT = 32_000;
const MAX_EVIDENCE_ENTITIES = 256;

function boundedText(value: string) {
  // Cut by code point: half of a surrogate pair is not valid text for the Compiler.
  return Array.from(value.normalize("NFC").replace(/\s+/gu, " ").trim()).slice(0, MAX_ENTITY_TEXT).join("");
}

function textWasTruncated(value: string) {
  return Array.from(value.normalize("NFC").replace(/\s+/gu, " ").trim()).length > MAX_ENTITY_TEXT;
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
  return [...axes, ...features, ...cells, ...series].join("; ");
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
    text = entity.quote ? `Source anchor type=${entity.anchor_type}. Exact text: ${entity.quote}` : "";
    anchorIds = [entity.id];
  } else if (entity.kind === "structured_visual_evidence") {
    kind = "structured_visual_evidence";
    text = `Structured ${entity.visual_kind} evidence. ${visualEvidenceText(entity)}`;
    anchorIds = [entity.anchor_id];
  } else if (entity.kind === "observation") {
    kind = "observation";
    text = `Observation type=${entity.observation_type}. ${entity.statement}`;
    anchorIds = entity.anchor_ids;
  } else if (entity.kind === "reasoning") {
    kind = "reasoning";
    text = [
      `Reasoning type=${entity.reasoning_type}.`,
      entity.statement,
      ...entity.limitations.map((limitation) => `Limitation: ${limitation}`),
    ].join(" ");
    anchorIds = entity.anchor_ids;
  } else if (entity.kind === "assumption") {
    kind = "assumption";
    text = `Assumption type=${entity.assumption_type}; explicit=${entity.explicit}. ${entity.statement}`;
    anchorIds = entity.anchor_ids;
  } else {
    return null;
  }

  if (!text) return null;
  return {
    id: entity.id,
    kind,
    text: boundedText(text),
    anchor_ids: [...new Set(anchorIds)].slice(0, 64),
    root_claim_ids: [rootClaimId],
    ...(entity.kind === "assumption" ? {
      assumption_type: entity.assumption_type,
      assumption_explicit: entity.explicit,
    } : {}),
    ...("scope" in entity ? { scope: comparisonScope(entity.scope) } : {}),
    content_truncated: textWasTruncated(text) || new Set(anchorIds).size > 64
      || (entity.kind === "structured_visual_evidence" && entity.series.some((series) => series.points.length > 64)),
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
  const rawText = details.join(" ");
  const text = boundedText(rawText);
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
    content_truncated: textWasTruncated(rawText),
  };
}

/**
 * Project only the selected Claim's derivation path into the comparison model.
 * Preserve assumptions as conditions, never as raw observations. The root Claim
 * is already represented by the side; artifact/version records are provenance.
 * The status describes recorded input retained by this projection, not whether
 * extraction discovered every scientifically necessary premise.
 */
export function buildClaimComparisonEvidence(
  graph: EvidenceGraph,
  rootClaimId: string,
  assessment: EvidenceRetrievalAssessment | null = null,
  options: { max_depth?: number } = {},
): ClaimComparisonEvidence {
  const path = expandEvidencePath(graph, rootClaimId, options);
  let availableEntities = path.entities.filter((entity) => (
    entity.kind !== "artifact" && entity.kind !== "artifact_version" && entity.kind !== "atomic_claim"
  )).length;
  const entities = path.entities
    .map((entity) => entityProjection(entity, rootClaimId))
    .filter((entity): entity is ClaimComparisonEvidenceEntity => entity !== null);
  const assessmentEntity = assessment === null ? null : assessmentProjection(assessment, rootClaimId);
  if (assessmentEntity && !entities.some((entity) => entity.id === assessmentEntity.id)) {
    entities.push(assessmentEntity);
    availableEntities += 1;
  }
  const retained = entities.slice(0, MAX_EVIDENCE_ENTITIES);
  const retainedIds = new Set([rootClaimId, ...retained.map((entity) => entity.id)]);
  const edges = path.edges.filter((edge) => retainedIds.has(edge.source_id) && retainedIds.has(edge.target_id));
  const omittedEntities = availableEntities - retained.length;
  const omittedEdges = path.edges.length - edges.length;
  const contentTruncated = retained.some((entity) => entity.content_truncated);
  return {
    evidence_entities: retained,
    evidence_edges: edges.map(({ id, source_id, target_id, relation }) => ({ id, source_id, target_id, relation })),
    evidence_completeness: {
      status: path.truncated || omittedEntities > 0 || omittedEdges > 0 || contentTruncated ? "truncated" : "complete",
      path_truncated: path.truncated,
      entity_limit: MAX_EVIDENCE_ENTITIES,
      depth_limit: options.max_depth ?? 32,
      available_entity_count: availableEntities,
      omitted_entity_count: omittedEntities,
      omitted_edge_count: omittedEdges,
      content_truncated: contentTruncated,
    },
  };
}

export function buildClaimComparisonSide(
  document: EvidenceRetrievalDocument,
  evidence: ClaimComparisonEvidence,
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
    ...evidence,
  };
}
