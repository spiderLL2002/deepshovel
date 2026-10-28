import { isCompilerModel } from "../compiler/model.ts";
import {
  CITATION_EXTRACTION_PROMPT_VERSION,
  CITATION_RECEPTION_PROCESSOR_VERSION,
  CITATION_RECEPTION_SCHEMA_VERSION,
  CITATION_SOURCE_EXTRACTOR_VERSION,
  CITATION_SOURCE_SCHEMA_VERSION,
  CITATION_VERIFICATION_PROMPT_VERSION,
  type CitationAssessment,
  type CitationConfirmationBasis,
  type CitationExtractionPromptVersion,
  type CitationEntityKind,
  type CitationFaithfulness,
  type CitationFunction,
  type CitationIndependence,
  type CitationMentionSource,
  type CitationPairRequest,
  type CitationReceptionEnvelope,
  type CitationReceptionProcessorVersion,
  type CitationReferenceSource,
  type CitationStance,
  type CitationVerificationPromptVersion,
  type CitationSourceProjection,
} from "./types.ts";
import { canonicalStringify, sha256Hex } from "../evidence-graph/canonical.ts";

const ID_RE = /^[A-Za-z][A-Za-z0-9._:-]{0,127}$/u;
const DOI_RE = /^10\.[0-9]{4,9}\/[\-._;()/:a-z0-9]+$/iu;
const FUNCTIONS = new Set<CitationFunction>(["result_comparison", "replication", "critique"]);
const ENTITY_KINDS = new Set<CitationEntityKind>([
  "source_anchor", "structured_visual_evidence", "observation", "reasoning", "atomic_claim",
]);
const STANCES = new Set<CitationStance>(["supports", "contradicts", "qualifies", "unclear"]);
const CONFIRMATION_BASES = new Set<CitationConfirmationBasis>([
  "mere_attribution", "verbal_endorsement", "verbal_critique",
  "consistent_with_citing_evidence", "contradicted_by_citing_evidence",
  "qualified_by_citing_evidence",
  "direct_replication", "reanalysis", "meta_analytic_synthesis",
]);
const FAITHFULNESS = new Set<CitationFaithfulness>([
  "faithful", "partial", "overgeneralized", "contradicted_by_target",
  "not_found", "ambiguous", "indirect",
]);
const INDEPENDENCE = new Set<CitationIndependence>([
  "independent", "shared_authors", "shared_data", "derived_data", "unknown",
]);
const SHA256_RE = /^[a-f0-9]{64}$/u;
const STRONG_CONFIRMATION = new Set<CitationConfirmationBasis>([
  "consistent_with_citing_evidence", "direct_replication", "reanalysis", "meta_analytic_synthesis",
]);
const EVIDENCE_BACKED_RECEPTION = new Set<CitationConfirmationBasis>([
  ...STRONG_CONFIRMATION,
  "contradicted_by_citing_evidence",
  "qualified_by_citing_evidence",
]);
function record(value: unknown, path: string) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${path} must be an object`);
  return value as Record<string, unknown>;
}

function exact(value: Record<string, unknown>, keys: readonly string[], path: string) {
  const actual = Object.keys(value);
  const unknown = actual.filter((key) => !keys.includes(key));
  const missing = keys.filter((key) => !Object.hasOwn(value, key));
  if (unknown.length || missing.length) throw new Error(`${path} has an invalid closed shape`);
}

function text(value: unknown, path: string, maximum: number) {
  if (typeof value !== "string" || !value.trim() || value.length > maximum) throw new Error(`${path} must be bounded text`);
  return value.trim();
}

function id(value: unknown, path: string) {
  const parsed = text(value, path, 128);
  if (!ID_RE.test(parsed)) throw new Error(`${path} must be a canonical identifier`);
  return parsed;
}

function integer(value: unknown, path: string, minimum: number, maximum: number) {
  if (!Number.isSafeInteger(value) || (value as number) < minimum || (value as number) > maximum) throw new Error(`${path} must be a bounded integer`);
  return value as number;
}

function nullableInteger(value: unknown, path: string, minimum: number, maximum: number) {
  return value === null ? null : integer(value, path, minimum, maximum);
}

function number(value: unknown, path: string, minimum = 0, maximum = 1) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < minimum || value > maximum) {
    throw new Error(`${path} must be a bounded number`);
  }
  return value;
}

function enumValue<T extends string>(value: unknown, values: Set<T>, path: string): T {
  if (typeof value !== "string" || !values.has(value as T)) throw new Error(`${path} is invalid`);
  return value as T;
}

function sha256(value: unknown, path: string) {
  if (typeof value !== "string" || !SHA256_RE.test(value)) throw new Error(`${path} must be a lowercase SHA-256 digest`);
  return value;
}

function bbox(value: unknown, path: string) {
  if (value === null) return null;
  const item = record(value, path);
  exact(item, ["x", "y", "width", "height"], path);
  for (const key of ["x", "y", "width", "height"] as const) {
    if (typeof item[key] !== "number" || !Number.isFinite(item[key])) throw new Error(`${path}.${key} must be finite`);
  }
  const parsed = item as { x: number; y: number; width: number; height: number };
  if (parsed.x < 0 || parsed.y < 0 || parsed.width <= 0 || parsed.height <= 0
    || parsed.x + parsed.width > 1.000001 || parsed.y + parsed.height > 1.000001) throw new Error(`${path} must fit the normalized page`);
  return parsed;
}

function stringArray(value: unknown, path: string, minimum: number, maximum: number, itemMaximum = 128) {
  if (!Array.isArray(value) || value.length < minimum || value.length > maximum) throw new Error(`${path} must be a bounded array`);
  const parsed = value.map((item, index) => text(item, `${path}[${index}]`, itemMaximum));
  if (new Set(parsed).size !== parsed.length) throw new Error(`${path} must be unique`);
  return parsed;
}

function reference(value: unknown, path: string): CitationReferenceSource {
  const item = record(value, path);
  exact(item, ["id", "ordinal", "label", "raw_text", "doi", "year", "source_block_id", "page"], path);
  const doi = item.doi === null ? null : text(item.doi, `${path}.doi`, 500).toLowerCase();
  if (doi !== null && !DOI_RE.test(doi)) throw new Error(`${path}.doi must be a normalized DOI`);
  return {
    id: id(item.id, `${path}.id`),
    ordinal: integer(item.ordinal, `${path}.ordinal`, 1, 100_000),
    label: text(item.label, `${path}.label`, 100),
    rawText: text(item.raw_text, `${path}.raw_text`, 32_000),
    doi,
    year: nullableInteger(item.year, `${path}.year`, 1800, 2199),
    sourceBlockId: id(item.source_block_id, `${path}.source_block_id`),
    page: integer(item.page, `${path}.page`, 1, Number.MAX_SAFE_INTEGER),
  };
}

function mention(value: unknown, path: string): CitationMentionSource {
  const item = record(value, path);
  exact(item, ["id", "reference_id", "marker", "exact_quote", "context_text", "source_block_id", "context_block_ids", "page", "bbox", "char_start", "char_end", "group_size", "candidate_functions"], path);
  if (!Array.isArray(item.candidate_functions) || item.candidate_functions.length > 3
    || item.candidate_functions.some((entry) => !FUNCTIONS.has(entry as CitationFunction))) throw new Error(`${path}.candidate_functions is invalid`);
  const candidateFunctions = item.candidate_functions as CitationFunction[];
  if (new Set(candidateFunctions).size !== candidateFunctions.length) throw new Error(`${path}.candidate_functions must be unique`);
  const exactQuote = text(item.exact_quote, `${path}.exact_quote`, 32_000);
  const marker = text(item.marker, `${path}.marker`, 200);
  const charStart = integer(item.char_start, `${path}.char_start`, 0, exactQuote.length);
  const charEnd = integer(item.char_end, `${path}.char_end`, 1, exactQuote.length);
  if (charStart >= charEnd || exactQuote.slice(charStart, charEnd) !== marker) throw new Error(`${path} marker offsets do not select marker verbatim`);
  return {
    id: id(item.id, `${path}.id`),
    referenceId: id(item.reference_id, `${path}.reference_id`),
    marker,
    exactQuote,
    contextText: text(item.context_text, `${path}.context_text`, 64_000),
    sourceBlockId: id(item.source_block_id, `${path}.source_block_id`),
    contextBlockIds: stringArray(item.context_block_ids, `${path}.context_block_ids`, 1, 3).map((item, index) => id(item, `${path}.context_block_ids[${index}]`)),
    page: integer(item.page, `${path}.page`, 1, Number.MAX_SAFE_INTEGER),
    bbox: bbox(item.bbox, `${path}.bbox`),
    charStart,
    charEnd,
    groupSize: integer(item.group_size, `${path}.group_size`, 1, 100),
    candidateFunctions,
  };
}

export function parseCitationSourceProjection(value: unknown): CitationSourceProjection {
  const source = record(value, "$");
  exact(source, ["schema_version", "extractor_version", "artifact_version_id", "references", "mentions", "warnings"], "$");
  if (source.schema_version !== CITATION_SOURCE_SCHEMA_VERSION || source.extractor_version !== CITATION_SOURCE_EXTRACTOR_VERSION) throw new Error("citation source version is unsupported");
  if (!Array.isArray(source.references) || !Array.isArray(source.mentions)) throw new Error("citation source collections must be arrays");
  const references = source.references.map((item, index) => reference(item, `$.references[${index}]`));
  const mentions = source.mentions.map((item, index) => mention(item, `$.mentions[${index}]`));
  const referenceIds = new Set(references.map((item) => item.id));
  if (referenceIds.size !== references.length || new Set(mentions.map((item) => item.id)).size !== mentions.length) throw new Error("citation source identities must be unique");
  if (mentions.some((item) => !referenceIds.has(item.referenceId))) throw new Error("citation mention references an unknown bibliography entry");
  if (references.some((item, index) => index > 0 && references[index - 1].ordinal > item.ordinal)) throw new Error("citation references must be ordered");
  return {
    schemaVersion: CITATION_SOURCE_SCHEMA_VERSION,
    extractorVersion: CITATION_SOURCE_EXTRACTOR_VERSION,
    artifactVersionId: id(source.artifact_version_id, "$.artifact_version_id"),
    references,
    mentions,
    warnings: stringArray(source.warnings, "$.warnings", 0, 64, 1_000),
  };
}

function assertion(value: unknown, path: string) {
  const item = record(value, path);
  exact(item, [
    "id", "mention_id", "attributed_proposition", "function", "stance",
    "confirmation_basis", "target_entity_id", "target_entity_kind",
    "citing_evidence_entity_ids", "confidence",
  ], path);
  const targetEntityId = item.target_entity_id === null ? null : id(item.target_entity_id, `${path}.target_entity_id`);
  const targetEntityKind = item.target_entity_kind === null
    ? null
    : enumValue(item.target_entity_kind, ENTITY_KINDS, `${path}.target_entity_kind`);
  if ((targetEntityId === null) !== (targetEntityKind === null)) throw new Error(`${path} target identity is incoherent`);
  return {
    id: id(item.id, `${path}.id`),
    mentionId: id(item.mention_id, `${path}.mention_id`),
    attributedProposition: text(item.attributed_proposition, `${path}.attributed_proposition`, 32_000),
    function: enumValue(item.function, FUNCTIONS, `${path}.function`),
    stance: enumValue(item.stance, STANCES, `${path}.stance`),
    confirmationBasis: enumValue(item.confirmation_basis, CONFIRMATION_BASES, `${path}.confirmation_basis`),
    targetEntityId,
    targetEntityKind,
    citingEvidenceEntityIds: stringArray(item.citing_evidence_entity_ids, `${path}.citing_evidence_entity_ids`, 0, 128)
      .map((entry, index) => id(entry, `${path}.citing_evidence_entity_ids[${index}]`)),
    confidence: number(item.confidence, `${path}.confidence`),
  };
}

function assessment(value: unknown, path: string): CitationAssessment {
  const item = record(value, path);
  exact(item, [
    "assertion_id", "faithfulness", "confirmation_basis", "independence",
    "confidence", "evidence_ids", "limitations",
  ], path);
  return {
    assertionId: id(item.assertion_id, `${path}.assertion_id`),
    faithfulness: enumValue(item.faithfulness, FAITHFULNESS, `${path}.faithfulness`),
    confirmationBasis: enumValue(item.confirmation_basis, CONFIRMATION_BASES, `${path}.confirmation_basis`),
    independence: enumValue(item.independence, INDEPENDENCE, `${path}.independence`),
    confidence: number(item.confidence, `${path}.confidence`),
    evidenceIds: stringArray(item.evidence_ids, `${path}.evidence_ids`, 0, 256)
      .map((entry, index) => id(entry, `${path}.evidence_ids[${index}]`)),
    limitations: stringArray(item.limitations, `${path}.limitations`, 0, 32, 2_000),
  };
}

function rawAssertion(item: CitationReceptionEnvelope["extraction"]["assertions"][number]) {
  return {
    id: item.id,
    mention_id: item.mentionId,
    attributed_proposition: item.attributedProposition,
    function: item.function,
    stance: item.stance,
    confirmation_basis: item.confirmationBasis,
    target_entity_id: item.targetEntityId,
    target_entity_kind: item.targetEntityKind,
    citing_evidence_entity_ids: item.citingEvidenceEntityIds,
    confidence: item.confidence,
  };
}

/** Strictly validate the untrusted Compiler response and bind it to the submitted pair. */
export async function parseCitationReceptionEnvelope(
  value: unknown,
  request?: CitationPairRequest,
): Promise<CitationReceptionEnvelope> {
  const source = record(value, "$");
  exact(source, [
    "schema_version", "processor_version", "citing_snapshot_id", "cited_snapshot_id",
    "reference_id", "extraction", "verification",
  ], "$");
  if (source.schema_version !== CITATION_RECEPTION_SCHEMA_VERSION
    || source.processor_version !== CITATION_RECEPTION_PROCESSOR_VERSION) {
    throw new Error("citation reception version is unsupported");
  }
  const extraction = record(source.extraction, "$.extraction");
  exact(extraction, ["run_id", "model", "prompt_version", "attempts", "assertions_sha256", "assertions"], "$.extraction");
  if (!isCompilerModel(extraction.model) || extraction.prompt_version !== CITATION_EXTRACTION_PROMPT_VERSION) {
    throw new Error("citation extraction provenance is unsupported");
  }
  if (!Array.isArray(extraction.assertions) || extraction.assertions.length > 256) {
    throw new Error("$.extraction.assertions must be a bounded array");
  }
  const assertions = extraction.assertions.map((item, index) => assertion(item, `$.extraction.assertions[${index}]`));
  const assertionsById = new Map(assertions.map((item) => [item.id, item]));
  if (assertionsById.size !== assertions.length) throw new Error("citation assertion IDs must be unique");
  const assertionsSha256 = sha256(extraction.assertions_sha256, "$.extraction.assertions_sha256");
  if (await sha256Hex(canonicalStringify(assertions.map(rawAssertion))) !== assertionsSha256) {
    throw new Error("citation extraction hash does not match the frozen assertions");
  }
  const parsed: CitationReceptionEnvelope = {
    schemaVersion: CITATION_RECEPTION_SCHEMA_VERSION,
    processorVersion: source.processor_version as CitationReceptionProcessorVersion,
    citingSnapshotId: id(source.citing_snapshot_id, "$.citing_snapshot_id"),
    citedSnapshotId: id(source.cited_snapshot_id, "$.cited_snapshot_id"),
    referenceId: id(source.reference_id, "$.reference_id"),
    extraction: {
      runId: id(extraction.run_id, "$.extraction.run_id"),
      model: extraction.model,
      promptVersion: extraction.prompt_version as CitationExtractionPromptVersion,
      attempts: integer(extraction.attempts, "$.extraction.attempts", 1, 9),
      assertionsSha256,
      assertions,
    },
    verification: null,
  };
  if (source.verification !== null) {
    const verification = record(source.verification, "$.verification");
    exact(verification, [
      "run_id", "model", "prompt_version", "attempts", "extraction_sha256", "assessments",
    ], "$.verification");
    if (!isCompilerModel(verification.model) || verification.prompt_version !== CITATION_VERIFICATION_PROMPT_VERSION) {
      throw new Error("citation verification provenance is unsupported");
    }
    if (!Array.isArray(verification.assessments) || verification.assessments.length > 256) {
      throw new Error("$.verification.assessments must be a bounded array");
    }
    const assessments = verification.assessments.map((item, index) => assessment(item, `$.verification.assessments[${index}]`));
    const assessed = assessments.map((item) => item.assertionId);
    if (assessed.length !== assertions.length || new Set(assessed).size !== assessed.length
      || assessed.some((assertionId) => !assertionsById.has(assertionId))) {
      throw new Error("citation verifier must assess every assertion exactly once");
    }
    const extractionSha256 = sha256(verification.extraction_sha256, "$.verification.extraction_sha256");
    if (extractionSha256 !== assertionsSha256) throw new Error("citation verifier extraction hash mismatch");
    parsed.verification = {
      runId: id(verification.run_id, "$.verification.run_id"),
      model: verification.model,
      promptVersion: verification.prompt_version as CitationVerificationPromptVersion,
      attempts: integer(verification.attempts, "$.verification.attempts", 1, 9),
      extractionSha256,
      assessments,
    };
  }
  if ((assertions.length === 0) !== (parsed.verification === null)) {
    throw new Error("citation verification must be present exactly when assertions exist");
  }
  if (request) validateReceptionAgainstRequest(parsed, request);
  return parsed;
}

function validateReceptionAgainstRequest(envelope: CitationReceptionEnvelope, request: CitationPairRequest) {
  if (envelope.citingSnapshotId !== request.citing_snapshot_id
    || envelope.citedSnapshotId !== request.cited_snapshot_id
    || envelope.referenceId !== request.reference.id) {
    throw new Error("citation response does not match the requested pair");
  }
  const mentionIds = new Set(request.mentions.map((item) => item.id));
  const citing = new Map(request.citing_entities.map((item) => [item.id, item]));
  const cited = new Map(request.cited_entities.map((item) => [item.id, item]));
  const assertions = new Map(envelope.extraction.assertions.map((item) => [item.id, item]));
  const directionalBasisContract = envelope.processorVersion === CITATION_RECEPTION_PROCESSOR_VERSION;
  const basisRank: Record<CitationConfirmationBasis, number> = {
    mere_attribution: 0,
    verbal_endorsement: 1,
    verbal_critique: 1,
    consistent_with_citing_evidence: 2,
    contradicted_by_citing_evidence: 2,
    qualified_by_citing_evidence: 2,
    reanalysis: 3,
    meta_analytic_synthesis: 3,
    direct_replication: 4,
  };
  for (const item of envelope.extraction.assertions) {
    if (!mentionIds.has(item.mentionId)) throw new Error("citation assertion selected an unknown mention");
    const target = item.targetEntityId === null ? null : cited.get(item.targetEntityId);
    if (item.targetEntityId !== null && (!target || target.kind !== item.targetEntityKind)) {
      throw new Error("citation assertion selected an invalid B target");
    }
    if (item.citingEvidenceEntityIds.some((entityId) => {
      const entity = citing.get(entityId);
      return !entity || !["source_anchor", "structured_visual_evidence", "observation", "reasoning"].includes(entity.kind);
    })) throw new Error("citation assertion selected invalid A evidence");
    if (directionalBasisContract && !basisMatchesStance(item.confirmationBasis, item.stance)) {
      throw new Error("citation confirmation basis does not match stance");
    }
    if (directionalBasisContract && item.confirmationBasis === "mere_attribution") {
      throw new Error("non-evaluative citation attribution is outside reception scope");
    }
    if (EVIDENCE_BACKED_RECEPTION.has(item.confirmationBasis) && item.citingEvidenceEntityIds.length === 0) {
      throw new Error("evidence-backed citation reception requires A evidence");
    }
    if (directionalBasisContract && EVIDENCE_BACKED_RECEPTION.has(item.confirmationBasis)
      && item.targetEntityId === null) {
      throw new Error("evidence-backed citation reception requires a B target");
    }
  }
  for (const item of envelope.verification?.assessments ?? []) {
    const assertionItem = assertions.get(item.assertionId)!;
    if (item.evidenceIds.some((entityId) => !citing.has(entityId) && !cited.has(entityId))) {
      throw new Error("citation assessment selected unknown evidence");
    }
    if (basisRank[item.confirmationBasis] > basisRank[assertionItem.confirmationBasis]) {
      throw new Error("citation verifier strengthened the confirmation basis");
    }
    if (directionalBasisContract && !basisMatchesStance(item.confirmationBasis, assertionItem.stance)) {
      throw new Error("verified confirmation basis does not match stance");
    }
    const directional = new Set<CitationConfirmationBasis>([
      "consistent_with_citing_evidence",
      "contradicted_by_citing_evidence",
      "qualified_by_citing_evidence",
    ]);
    if (directional.has(item.confirmationBasis)
      && directional.has(assertionItem.confirmationBasis)
      && item.confirmationBasis !== assertionItem.confirmationBasis) {
      throw new Error("citation verifier changed evidence direction");
    }
    if (EVIDENCE_BACKED_RECEPTION.has(item.confirmationBasis)
      && !item.evidenceIds.some((entityId) => assertionItem.citingEvidenceEntityIds.includes(entityId))) {
      throw new Error("verified evidence-backed reception requires A evidence");
    }
    if (assertionItem.targetEntityId === null && !["not_found", "ambiguous", "indirect"].includes(item.faithfulness)) {
      throw new Error("an unresolved B target cannot be verified faithful");
    }
    if (item.faithfulness === "faithful"
      && (assertionItem.targetEntityId === null || !item.evidenceIds.includes(assertionItem.targetEntityId))) {
      throw new Error("faithful assessment must cite the selected B target");
    }
  }
}

function basisMatchesStance(basis: CitationConfirmationBasis, stance: CitationStance) {
  if (basis === "consistent_with_citing_evidence") return stance === "supports";
  if (basis === "contradicted_by_citing_evidence") return stance === "contradicts";
  if (basis === "qualified_by_citing_evidence") return stance === "qualifies";
  if (basis === "verbal_endorsement") return stance === "supports";
  if (basis === "verbal_critique") return stance === "contradicts" || stance === "qualifies";
  if (basis === "mere_attribution") return stance === "unclear";
  return true;
}
