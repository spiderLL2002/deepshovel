import {
  entityMap,
  isClaimComparison,
  isDerivationEdge,
  validateEvidenceGraph,
  type AtomicClaim,
  type CompilerVerificationReport,
  type EvidenceEdge,
  type EvidenceEntity,
  type EvidenceGraph,
  type SourceAnchor,
} from "../evidence-graph/index.ts";
import {
  EVIDENCE_RETRIEVAL_INDEX_VERSION,
  type CompactEvidenceAnchor,
  type EvidencePathLink,
  type EvidenceRetrievalAbbreviation,
  type EvidenceRetrievalAssessment,
  type EvidenceRetrievalAssessmentDto,
  type EvidenceRetrievalAssessmentMetadata,
  type EvidenceRetrievalDocument,
  type EvidenceRetrievalDocumentDto,
  type EvidenceRetrievalManifest,
  type EvidenceRetrievalManifestDto,
  type EvidenceRetrievalProjection,
  type EvidenceRetrievalProjectionMetadata,
  type ExplicitClaimRelation,
  type ShortestEvidencePath,
} from "./types.ts";

const SHA256_RE = /^[a-f0-9]{64}$/u;
const SNAPSHOT_RE = /^snapshot:sha256:([a-f0-9]{64})$/u;
const KEY_TERM_LIMIT = 256;
const ABBREVIATION_LIMIT = 32;
const EVIDENCE_CONTEXT_LIMIT = 16_000;

function compareText(left: string, right: string) {
  return left < right ? -1 : left > right ? 1 : 0;
}

function compactText(value: string, maximum = 280) {
  const normalized = value.replace(/\s+/gu, " ").trim();
  const characters = Array.from(normalized);
  return characters.length <= maximum ? normalized : `${characters.slice(0, maximum - 1).join("")}…`;
}

function entityLabel(entity: EvidenceEntity) {
  if (entity.kind === "artifact") return compactText(entity.canonical_name);
  if (entity.kind === "artifact_version") return compactText(entity.version);
  if (entity.kind === "source_anchor") {
    const selector = entity.selector;
    const coordinate = selector.figure ?? selector.table ?? selector.section
      ?? selector.dataset_path ?? selector.region_label ?? `${entity.anchor_type} anchor`;
    const page = selector.page == null ? null : `p.${selector.page}`;
    const evidence = entity.quote == null ? coordinate : `“${compactText(entity.quote, 220)}”`;
    return compactText([evidence, coordinate, page]
      .filter((part, index, parts): part is string => Boolean(part) && parts.indexOf(part) === index)
      .join(" · "));
  }
  if (entity.kind === "structured_visual_evidence") return `${entity.visual_kind} evidence`;
  if (entity.kind === "atomic_claim") return compactText(entity.proposition);
  return compactText(entity.statement);
}

type Predecessor = { entity: EvidenceEntity; link: EvidencePathLink; priority: number };

function provenancePredecessor(
  parent: EvidenceEntity | undefined,
  child: EvidenceEntity,
  relation: string,
): Predecessor | null {
  if (!parent) return null;
  return {
    entity: parent,
    priority: 1,
    link: {
      fromId: parent.id,
      toId: child.id,
      relation,
      linkType: "provenance",
      confidence: null,
    },
  };
}

function predecessorSteps(
  current: EvidenceEntity,
  entities: Map<string, EvidenceEntity>,
  incoming: Map<string, EvidenceEdge[]>,
) {
  const steps: Predecessor[] = [];
  for (const edge of incoming.get(current.id) ?? []) {
    if (!isDerivationEdge(edge, entities)) continue;
    const parent = entities.get(edge.source_id);
    if (!parent) continue;
    steps.push({
      entity: parent,
      priority: 0,
      link: {
        fromId: edge.source_id,
        toId: edge.target_id,
        relation: edge.relation,
        linkType: "edge",
        confidence: edge.confidence,
      },
    });
  }

  const add = (step: Predecessor | null) => { if (step) steps.push(step); };
  if (current.kind === "artifact_version") {
    add(provenancePredecessor(entities.get(current.artifact_id), current, "versions"));
  }
  if (current.kind === "source_anchor") {
    add(provenancePredecessor(entities.get(current.artifact_version_id), current, "locates"));
  }
  if (current.kind === "structured_visual_evidence") {
    add(provenancePredecessor(entities.get(current.anchor_id), current, "structures"));
  }

  // Direct anchor metadata is a provenance fallback only. It must never let a
  // Claim skip explicit Observation/Reasoning edges in a validated graph.
  if (!steps.some((step) => step.priority === 0)
    && (current.kind === "observation"
      || current.kind === "assumption"
      || current.kind === "reasoning"
      || current.kind === "atomic_claim")) {
    for (const anchorId of [...current.anchor_ids].sort(compareText)) {
      add(provenancePredecessor(entities.get(anchorId), current, "anchors"));
    }
  }

  const unique = new Map<string, Predecessor>();
  for (const step of steps) {
    const key = `${step.entity.id}\0${step.link.toId}\0${step.link.relation}`;
    const previous = unique.get(key);
    if (!previous || step.priority < previous.priority) unique.set(key, step);
  }
  return [...unique.values()].sort((left, right) => left.priority - right.priority
    || compareText(left.entity.id, right.entity.id)
    || compareText(left.link.relation, right.link.relation));
}

function pathFactory(graph: EvidenceGraph) {
  const entities = entityMap(graph);
  const incoming = new Map<string, EvidenceEdge[]>();
  for (const edge of graph.edges) {
    const edges = incoming.get(edge.target_id);
    if (edges) edges.push(edge);
    else incoming.set(edge.target_id, [edge]);
  }

  const outgoing = new Map<string, Array<{ child: EvidenceEntity; link: EvidencePathLink }>>();
  for (const child of entities.values()) {
    for (const step of predecessorSteps(child, entities, incoming)) {
      const steps = outgoing.get(step.entity.id);
      const forward = { child, link: step.link };
      if (steps) steps.push(forward);
      else outgoing.set(step.entity.id, [forward]);
    }
  }
  for (const steps of outgoing.values()) {
    steps.sort((left, right) => compareText(left.child.id, right.child.id)
      || compareText(left.link.relation, right.link.relation));
  }

  const distance = new Map<string, number>();
  const previous = new Map<string, { parentId: string; link: EvidencePathLink }>();
  const queue: EvidenceEntity[] = [...entities.values()]
    .filter((entity) => entity.kind === "artifact")
    .sort((left, right) => compareText(left.id, right.id));
  for (const artifact of queue) distance.set(artifact.id, 0);
  for (let head = 0; head < queue.length; head += 1) {
    const parent = queue[head];
    for (const step of outgoing.get(parent.id) ?? []) {
      if (distance.has(step.child.id)) continue;
      distance.set(step.child.id, (distance.get(parent.id) as number) + 1);
      previous.set(step.child.id, { parentId: parent.id, link: step.link });
      queue.push(step.child);
    }
  }

  return {
    entities,
    pathForClaim(claimId: string): ShortestEvidencePath | null {
      const claim = entities.get(claimId);
      if (claim?.kind !== "atomic_claim" || !distance.has(claimId)) return null;
      const pathEntities: EvidenceEntity[] = [claim];
      const links: EvidencePathLink[] = [];
      let current = claim.id;
      while (previous.has(current)) {
        const step = previous.get(current) as { parentId: string; link: EvidencePathLink };
        links.push(step.link);
        pathEntities.push(entities.get(step.parentId) as EvidenceEntity);
        current = step.parentId;
      }
      pathEntities.reverse();
      links.reverse();
      const confidences = links.flatMap((link) => link.confidence == null ? [] : [link.confidence]);
      return {
        rootClaimId: claimId,
        nodes: pathEntities.map((entity) => ({ id: entity.id, kind: entity.kind, label: entityLabel(entity) })),
        links,
        depth: links.length,
        meanEdgeConfidence: confidences.length === 0
          ? null
          : confidences.reduce((sum, value) => sum + value, 0) / confidences.length,
      };
    },
  };
}

/** One deterministic, complete shortest route from source Artifact to AtomicClaim. */
export function shortestEvidencePath(graph: EvidenceGraph, claimId: string) {
  validateEvidenceGraph(graph);
  return pathFactory(graph).pathForClaim(claimId);
}

function compactAnchor(anchor: SourceAnchor): CompactEvidenceAnchor {
  return {
    anchorId: anchor.id,
    artifactVersionId: anchor.artifact_version_id,
    anchorType: anchor.anchor_type,
    page: anchor.selector.page,
    section: anchor.selector.section,
    paragraph: anchor.selector.paragraph,
    quote: anchor.quote,
    figure: anchor.selector.figure,
    table: anchor.selector.table,
    rowStart: anchor.selector.row_start,
    rowEnd: anchor.selector.row_end,
    rowRangeLabel: anchor.selector.row_range_label,
    columnNames: [...anchor.selector.column_names],
    series: anchor.selector.series,
    charStart: anchor.selector.char_start,
    charEnd: anchor.selector.char_end,
    region: anchor.selector.region == null ? null : { ...anchor.selector.region },
    regionLabel: anchor.selector.region_label,
    datasetPath: anchor.selector.dataset_path,
  };
}

function pathEntities(path: ShortestEvidencePath, entities: Map<string, EvidenceEntity>) {
  return path.nodes.map((node) => entities.get(node.id)).filter((entity): entity is EvidenceEntity => Boolean(entity));
}

function evidenceAnchorsForClaim(
  claim: AtomicClaim,
  path: ShortestEvidencePath,
  entities: Map<string, EvidenceEntity>,
) {
  const orderedIds = path.nodes
    .filter((node) => node.kind === "source_anchor")
    .map((node) => node.id);
  for (const id of [...claim.anchor_ids].sort(compareText)) if (!orderedIds.includes(id)) orderedIds.push(id);
  return orderedIds.flatMap((id) => {
    const anchor = entities.get(id);
    return anchor?.kind === "source_anchor" ? [compactAnchor(anchor)] : [];
  });
}

function exactOriginalClaim(
  claim: AtomicClaim,
  path: ShortestEvidencePath,
  entities: Map<string, EvidenceEntity>,
) {
  const direct = [...claim.anchor_ids].sort(compareText);
  const onPath = path.nodes.filter((node) => node.kind === "source_anchor").map((node) => node.id);
  for (const id of [...direct, ...onPath]) {
    const anchor = entities.get(id);
    if (anchor?.kind === "source_anchor" && anchor.quote != null && anchor.quote.trim()) return anchor.quote;
  }
  return null;
}

function evidenceContext(path: ShortestEvidencePath, entities: Map<string, EvidenceEntity>) {
  const segments: string[] = [];
  for (const entity of pathEntities(path, entities)) {
    let segment: string | null = null;
    if (entity.kind === "source_anchor" && entity.quote?.trim()) segment = `[source] ${entity.quote}`;
    if (entity.kind === "observation") segment = `[observation] ${entity.statement}`;
    if (entity.kind === "reasoning") segment = `[reasoning] ${entity.statement}`;
    if (segment && !segments.includes(segment)) segments.push(segment);
  }
  return Array.from(segments.join("\n")).slice(0, EVIDENCE_CONTEXT_LIMIT).join("");
}

function normalizedUnique(values: string[]) {
  const unique = new Map<string, string>();
  for (const value of values) {
    const compact = value.normalize("NFKC").replace(/\s+/gu, " ").trim();
    if (!compact) continue;
    const key = compact.toLocaleLowerCase("und");
    if (!unique.has(key)) unique.set(key, compact);
  }
  return [...unique.values()].sort(compareText);
}

function scopeValues(claim: AtomicClaim) {
  const time = claim.scope.time;
  return [
    ...claim.scope.entities,
    ...claim.scope.location,
    ...claim.scope.formation,
    ...claim.scope.sample_type,
    ...claim.scope.method,
    ...claim.scope.condition,
    ...claim.scope.measurement,
    ...(claim.scope.uncertainty == null ? [] : [claim.scope.uncertainty]),
    ...(time?.label == null ? [] : [time.label]),
    ...(time?.start == null ? [] : [`${time.start}${time.unit ?? ""}`]),
    ...(time?.end == null ? [] : [`${time.end}${time.unit ?? ""}`]),
  ];
}

function extractAbbreviations(values: string[]): EvidenceRetrievalAbbreviation[] {
  const abbreviations = new Map<string, EvidenceRetrievalAbbreviation>();
  for (const raw of values) {
    const value = raw.normalize("NFKC");
    const explicit = /([\p{L}][\p{L}\p{M}\p{N}+./ -]{1,100}?)\s*\(([A-Z][A-Z0-9+./-]{1,14})\)/gu;
    for (const match of value.matchAll(explicit)) {
      const shortForm = match[2];
      const phrase = match[1].trim().split(/[;,:.!?\n]/u).at(-1)?.trim() ?? "";
      const words = phrase.split(/\s+/u);
      const longForm = words.slice(Math.max(0, words.length - 10)).join(" ");
      if (longForm) abbreviations.set(shortForm.toLocaleLowerCase("und"), { shortForm, longForm });
    }
    for (const match of value.matchAll(/\b[A-Z][A-Z0-9+./-]{1,14}\b/gu)) {
      const shortForm = match[0];
      const key = shortForm.toLocaleLowerCase("und");
      if (!abbreviations.has(key)) abbreviations.set(key, { shortForm, longForm: null });
    }
  }
  return [...abbreviations.values()].sort((left, right) => compareText(left.shortForm, right.shortForm)
    || compareText(left.longForm ?? "", right.longForm ?? "")).slice(0, ABBREVIATION_LIMIT);
}

function extractKeyTerms(values: string[], preferred: string[]) {
  const terms: string[] = [];
  const seen = new Set<string>();
  const add = (raw: string) => {
    const term = raw.normalize("NFKC").replace(/\s+/gu, " ").trim();
    const key = term.toLocaleLowerCase("und");
    if (!term || seen.has(key) || terms.length >= KEY_TERM_LIMIT) return;
    seen.add(key);
    terms.push(term);
  };
  for (const value of normalizedUnique(preferred)) add(value);
  for (const value of values) {
    for (const match of value.normalize("NFKC").matchAll(/[\p{L}\p{M}\p{N}]+(?:[+._:/-][\p{L}\p{M}\p{N}]+)*/gu)) {
      const term = match[0];
      if (Array.from(term).length >= 2 || /\d/u.test(term)) add(term);
      if (terms.length >= KEY_TERM_LIMIT) return terms;
    }
  }
  return terms;
}

function roundScore(value: number) {
  return Math.round(value * 1_000_000) / 1_000_000;
}

function pathQuality(path: ShortestEvidencePath) {
  const kinds = new Set(path.nodes.map((node) => node.kind));
  const complete = kinds.has("artifact") && kinds.has("artifact_version")
    && kinds.has("source_anchor") && kinds.has("atomic_claim");
  const quotedAnchor = path.nodes.some((node) => node.kind === "source_anchor" && node.label.startsWith("“"));
  const observation = kinds.has("observation");
  const confidence = path.meanEdgeConfidence ?? 0.5;
  return roundScore((complete ? 0.45 : 0) + (quotedAnchor ? 0.25 : 0)
    + (observation ? 0.2 : 0) + (0.1 * confidence));
}

function relationsForClaim(
  graph: EvidenceGraph,
  claimId: string,
  entities: Map<string, EvidenceEntity>,
): ExplicitClaimRelation[] {
  return graph.edges.filter((edge) => isClaimComparison(edge, entities)
    && (edge.source_id === claimId || edge.target_id === claimId))
    .map((edge): ExplicitClaimRelation => ({
      edgeId: edge.id,
      sourceClaimId: edge.source_id,
      targetClaimId: edge.target_id,
      otherClaimId: edge.source_id === claimId ? edge.target_id : edge.source_id,
      relation: edge.relation as ExplicitClaimRelation["relation"],
      direction: edge.source_id === claimId ? "outgoing" : "incoming",
      confidence: edge.confidence,
      scopeOverlap: edge.scope_overlap as ExplicitClaimRelation["scopeOverlap"],
    }))
    .sort((left, right) => compareText(left.edgeId, right.edgeId));
}

function validateProjectionMetadata(metadata: EvidenceRetrievalProjectionMetadata) {
  const snapshot = SNAPSHOT_RE.exec(metadata.snapshotId);
  if (!snapshot) throw new TypeError("Retrieval snapshotId must be a canonical snapshot SHA-256 ID");
  if (!SHA256_RE.test(metadata.graphSha256) || snapshot[1] !== metadata.graphSha256) {
    throw new TypeError("Retrieval snapshotId and graphSha256 must identify the same graph");
  }
  if (!metadata.extractorPromptVersion.trim()) throw new TypeError("Retrieval extractorPromptVersion is required");
  if (!Number.isFinite(Date.parse(metadata.createdAt))) throw new TypeError("Retrieval createdAt must be an ISO timestamp");
}

/** Build a deterministic v2 materialized search projection from a validated graph. */
export function buildEvidenceRetrievalProjection(
  graph: EvidenceGraph,
  metadata: EvidenceRetrievalProjectionMetadata,
): EvidenceRetrievalProjection {
  validateEvidenceGraph(graph);
  validateProjectionMetadata(metadata);
  const rootVersion = graph.artifact_versions.find((version) => version.id === metadata.rootArtifactVersionId);
  if (!rootVersion) throw new TypeError("Retrieval root ArtifactVersion is not present in the graph");
  const paperArtifact = graph.artifacts.find((artifact) => artifact.id === rootVersion.artifact_id);
  if (!paperArtifact) throw new TypeError("Retrieval root Artifact is not present in the graph");

  const { entities, pathForClaim } = pathFactory(graph);
  const documents = [...graph.claims].sort((left, right) => compareText(left.id, right.id)).map((claim) => {
    const path = pathForClaim(claim.id);
    if (!path) throw new TypeError(`Claim ${claim.id} has no complete Evidence Path`);
    const originalClaim = exactOriginalClaim(claim, path, entities);
    const context = evidenceContext(path, entities);
    const entitiesList = normalizedUnique(claim.scope.entities);
    const preferredTerms = [...entitiesList, ...scopeValues(claim), ...claim.qualifiers];
    const searchableText = [claim.proposition, originalClaim ?? "", paperArtifact.canonical_name, context, ...preferredTerms];
    const abbreviations = extractAbbreviations(searchableText);
    return {
      indexVersion: EVIDENCE_RETRIEVAL_INDEX_VERSION,
      snapshotId: metadata.snapshotId,
      paperId: graph.paper_id,
      paperFamilyId: paperArtifact.id,
      rootArtifactVersionId: rootVersion.id,
      claimId: claim.id,
      sourceClaimId: claim.source_id,
      originalClaim,
      canonicalClaim: claim.proposition,
      propositionKey: claim.proposition_key,
      scopedClaimId: claim.scoped_claim_id,
      paperTitle: paperArtifact.canonical_name,
      evidenceContext: context,
      entities: entitiesList,
      keyTerms: extractKeyTerms(searchableText, [
        ...preferredTerms,
        ...abbreviations.map((item) => item.shortForm),
        ...abbreviations.flatMap((item) => item.longForm == null ? [] : [item.longForm]),
      ]),
      abbreviations,
      qualifiers: [...claim.qualifiers],
      claimType: claim.claim_type,
      polarity: claim.polarity,
      modality: claim.modality,
      attribution: "authors_conclusion",
      rhetoricalRole: "terminal_conclusion",
      scope: structuredClone(claim.scope),
      evidenceAnchors: evidenceAnchorsForClaim(claim, path, entities),
      explicitRelations: relationsForClaim(graph, claim.id, entities),
      shortestEvidencePath: path,
      pathQuality: pathQuality(path),
      extractorPromptVersion: metadata.extractorPromptVersion,
    } satisfies EvidenceRetrievalDocument;
  });

  const manifest: EvidenceRetrievalManifest = {
    indexVersion: EVIDENCE_RETRIEVAL_INDEX_VERSION,
    snapshotId: metadata.snapshotId,
    paperId: graph.paper_id,
    paperFamilyId: paperArtifact.id,
    rootArtifactVersionId: rootVersion.id,
    rootArtifactSha256: rootVersion.sha256,
    graphSha256: metadata.graphSha256,
    graphSchemaVersion: graph.schema_version,
    extractorPromptVersion: metadata.extractorPromptVersion,
    documentCount: documents.length,
    createdAt: metadata.createdAt,
  };
  return { manifest, documents };
}

/** Verification remains append-only and never mutates extraction documents. */
export function buildEvidenceRetrievalAssessments(
  graph: EvidenceGraph,
  snapshotId: string,
  report: CompilerVerificationReport,
  metadata: EvidenceRetrievalAssessmentMetadata,
) {
  const canonicalClaims = new Map(graph.claims.flatMap((claim) => [
    [claim.id, claim] as const,
    ...(claim.source_id == null ? [] : [[claim.source_id, claim] as const]),
  ]));
  return report.assessments.map((assessment): EvidenceRetrievalAssessment => {
    const claim = canonicalClaims.get(assessment.claim_id)
      ?? graph.claims.find((item) => item.scoped_claim_id === assessment.scoped_claim_id);
    if (!claim) throw new TypeError(`Assessment ${assessment.id} cannot be mapped to a canonical Claim`);
    return {
      indexVersion: EVIDENCE_RETRIEVAL_INDEX_VERSION,
      verificationId: metadata.verificationId,
      snapshotId,
      assessmentId: assessment.id,
      claimId: claim.id,
      scopedClaimId: claim.scoped_claim_id,
      verdict: assessment.verdict,
      confidence: assessment.confidence,
      evidenceIds: [...assessment.evidence_ids],
      rationale: assessment.rationale,
      limitations: [...assessment.limitations],
      verifierModel: report.verifier_model,
      verifierPromptVersion: metadata.verifierPromptVersion,
      createdAt: metadata.createdAt,
    };
  }).sort((left, right) => compareText(left.assessmentId, right.assessmentId));
}

export function toEvidenceRetrievalDocumentDto(document: EvidenceRetrievalDocument): EvidenceRetrievalDocumentDto {
  return {
    index_version: document.indexVersion,
    snapshot_id: document.snapshotId,
    paper_id: document.paperId,
    paper_family_id: document.paperFamilyId,
    root_artifact_version_id: document.rootArtifactVersionId,
    claim_id: document.claimId,
    source_claim_id: document.sourceClaimId,
    original_claim: document.originalClaim,
    canonical_claim: document.canonicalClaim,
    proposition_key: document.propositionKey,
    scoped_claim_id: document.scopedClaimId,
    paper_title: document.paperTitle,
    evidence_context: document.evidenceContext,
    entities: document.entities,
    key_terms: document.keyTerms,
    abbreviations: document.abbreviations.map((item) => ({ short_form: item.shortForm, long_form: item.longForm })),
    qualifiers: document.qualifiers,
    claim_type: document.claimType,
    polarity: document.polarity,
    modality: document.modality,
    attribution: document.attribution,
    rhetorical_role: document.rhetoricalRole,
    scope: document.scope,
    evidence_anchors: document.evidenceAnchors.map((anchor) => ({
      anchor_id: anchor.anchorId,
      artifact_version_id: anchor.artifactVersionId,
      anchor_type: anchor.anchorType,
      page: anchor.page,
      section: anchor.section,
      paragraph: anchor.paragraph,
      quote: anchor.quote,
      figure: anchor.figure,
      table: anchor.table,
      row_start: anchor.rowStart,
      row_end: anchor.rowEnd,
      row_range_label: anchor.rowRangeLabel,
      column_names: anchor.columnNames,
      series: anchor.series,
      char_start: anchor.charStart,
      char_end: anchor.charEnd,
      region: anchor.region,
      region_label: anchor.regionLabel,
      dataset_path: anchor.datasetPath,
    })),
    explicit_relations: document.explicitRelations.map((relation) => ({
      edge_id: relation.edgeId,
      source_claim_id: relation.sourceClaimId,
      target_claim_id: relation.targetClaimId,
      other_claim_id: relation.otherClaimId,
      relation: relation.relation,
      direction: relation.direction,
      confidence: relation.confidence,
      scope_overlap: relation.scopeOverlap,
    })),
    shortest_evidence_path: {
      root_claim_id: document.shortestEvidencePath.rootClaimId,
      nodes: document.shortestEvidencePath.nodes,
      links: document.shortestEvidencePath.links.map((link) => ({
        from_id: link.fromId,
        to_id: link.toId,
        relation: link.relation,
        link_type: link.linkType,
        confidence: link.confidence,
      })),
      depth: document.shortestEvidencePath.depth,
      mean_edge_confidence: document.shortestEvidencePath.meanEdgeConfidence,
    },
    path_quality: document.pathQuality,
    extractor_prompt_version: document.extractorPromptVersion,
  };
}

export function toEvidenceRetrievalManifestDto(manifest: EvidenceRetrievalManifest): EvidenceRetrievalManifestDto {
  return {
    index_version: manifest.indexVersion,
    snapshot_id: manifest.snapshotId,
    paper_id: manifest.paperId,
    paper_family_id: manifest.paperFamilyId,
    root_artifact_version_id: manifest.rootArtifactVersionId,
    root_artifact_sha256: manifest.rootArtifactSha256,
    graph_sha256: manifest.graphSha256,
    graph_schema_version: manifest.graphSchemaVersion,
    extractor_prompt_version: manifest.extractorPromptVersion,
    document_count: manifest.documentCount,
    created_at: manifest.createdAt,
  };
}

export function toEvidenceRetrievalAssessmentDto(
  assessment: EvidenceRetrievalAssessment,
): EvidenceRetrievalAssessmentDto {
  return {
    index_version: assessment.indexVersion,
    verification_id: assessment.verificationId,
    snapshot_id: assessment.snapshotId,
    assessment_id: assessment.assessmentId,
    claim_id: assessment.claimId,
    scoped_claim_id: assessment.scopedClaimId,
    verdict: assessment.verdict,
    confidence: assessment.confidence,
    evidence_ids: assessment.evidenceIds,
    rationale: assessment.rationale,
    limitations: assessment.limitations,
    verifier_model: assessment.verifierModel,
    verifier_prompt_version: assessment.verifierPromptVersion,
    created_at: assessment.createdAt,
  };
}
