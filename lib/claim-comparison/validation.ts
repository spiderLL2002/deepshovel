import { isCompilerModel } from "../compiler/model.ts";
import type { ClaimModality, ClaimPolarity, ClaimType, Scope } from "../evidence-graph/index.ts";
import { canonicalStringify, sha256Hex } from "../evidence-graph/index.ts";
import {
  CLAIM_MODALITIES,
  CLAIM_POLARITIES,
  CLAIM_TYPES as SHARED_CLAIM_TYPES,
} from "../shared-contract.generated.ts";
import {
  CLAIM_COMPARISON_EXTRACTION_PROMPT_VERSION,
  CLAIM_COMPARISON_PROCESSOR_VERSION,
  CLAIM_COMPARISON_SCHEMA_VERSION,
  CLAIM_COMPARISON_VERIFICATION_PROMPT_VERSION,
  CLAIM_NORMALIZER_PROMPT_VERSION,
  CLAIM_NORMALIZER_VERSION,
  type ClaimComparisonAnalyzeRequest,
  type ClaimComparisonAssessment,
  type ClaimComparisonDecision,
  type ClaimComparisonDirection,
  type ClaimComparisonDirectness,
  type ClaimComparisonEnvelope,
  type ClaimComparisonNormalization,
  type ClaimComparisonRelation,
  type ClaimComparisonScopeAlignment,
  type ClaimComparisonStance,
  type ClaimComparisonVerdict,
  type NormalizedClaimDraft,
} from "./types.ts";

const ID_RE = /^[A-Za-z][A-Za-z0-9._:-]{0,127}$/u;
const SHA256_RE = /^[a-f0-9]{64}$/u;
const CLAIM_TYPES = new Set<ClaimType>(SHARED_CLAIM_TYPES);
const POLARITIES = new Set<ClaimPolarity>(CLAIM_POLARITIES);
const MODALITIES = new Set<ClaimModality>(CLAIM_MODALITIES);
const RELATIONS = new Set<ClaimComparisonRelation>([
  "equivalent", "supports", "contradicts", "qualifies", "refines", "supplements",
  "no_relation", "insufficient_to_determine",
]);
const DIRECTIONS = new Set<ClaimComparisonDirection>([
  "query_to_candidate", "candidate_to_query", "bidirectional", "none",
]);
const STANCES = new Set<ClaimComparisonStance>([
  "supportive", "contradictory", "qualifying", "neutral", "unclear",
]);
const ALIGNMENTS = new Set<ClaimComparisonScopeAlignment>([
  "equivalent", "overlap", "query_within_candidate", "candidate_within_query", "disjoint", "unknown",
]);
const DIRECTNESS = new Set<ClaimComparisonDirectness>(["direct", "indirect", "none", "insufficient"]);
const VERDICTS = new Set<ClaimComparisonVerdict>(["maintained", "downgraded", "abstained"]);
const RAW_EVIDENCE_KINDS = new Set(["source_anchor", "structured_visual_evidence", "observation"]);
const DOWNGRADES: Record<ClaimComparisonRelation, Set<ClaimComparisonRelation>> = {
  equivalent: new Set(["supports", "qualifies", "refines", "supplements", "no_relation", "insufficient_to_determine"]),
  supports: new Set(["qualifies", "no_relation", "insufficient_to_determine"]),
  contradicts: new Set(["qualifies", "no_relation", "insufficient_to_determine"]),
  qualifies: new Set(["no_relation", "insufficient_to_determine"]),
  refines: new Set(["qualifies", "no_relation", "insufficient_to_determine"]),
  supplements: new Set(["no_relation", "insufficient_to_determine"]),
  no_relation: new Set(["insufficient_to_determine"]),
  insufficient_to_determine: new Set(),
};
const DIRECTED_RELATIONS = new Set<ClaimComparisonRelation>(["supports", "qualifies", "refines"]);
const SCOPE_STRENGTH: Record<ClaimComparisonScopeAlignment, number> = {
  unknown: 0,
  disjoint: 0,
  overlap: 1,
  query_within_candidate: 1,
  candidate_within_query: 1,
  equivalent: 2,
};
const DIRECTNESS_STRENGTH: Record<ClaimComparisonDirectness, number> = {
  insufficient: 0,
  none: 0,
  indirect: 1,
  direct: 2,
};

function record(value: unknown, path: string) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${path} must be an object`);
  return value as Record<string, unknown>;
}

function exact(value: Record<string, unknown>, keys: readonly string[], path: string) {
  const actual = Object.keys(value);
  if (actual.some((key) => !keys.includes(key)) || keys.some((key) => !Object.hasOwn(value, key))) {
    throw new Error(`${path} has an invalid closed shape`);
  }
}

function text(value: unknown, path: string, maximum: number) {
  if (typeof value !== "string" || !value.trim() || value.length > maximum) {
    throw new Error(`${path} must be bounded text`);
  }
  return value.trim();
}

function nullableText(value: unknown, path: string, maximum: number) {
  return value === null ? null : text(value, path, maximum);
}

function id(value: unknown, path: string) {
  const parsed = text(value, path, 128);
  if (!ID_RE.test(parsed)) throw new Error(`${path} must be a canonical identifier`);
  return parsed;
}

function integer(value: unknown, path: string, minimum: number, maximum: number) {
  if (!Number.isSafeInteger(value) || (value as number) < minimum || (value as number) > maximum) {
    throw new Error(`${path} must be a bounded integer`);
  }
  return value as number;
}

function finiteNumber(value: unknown, path: string, minimum?: number, maximum?: number) {
  if (typeof value !== "number" || !Number.isFinite(value)
    || (minimum !== undefined && value < minimum)
    || (maximum !== undefined && value > maximum)) {
    throw new Error(`${path} must be a bounded number`);
  }
  return value;
}

function enumValue<T extends string>(value: unknown, values: Set<T>, path: string): T {
  if (typeof value !== "string" || !values.has(value as T)) throw new Error(`${path} is invalid`);
  return value as T;
}

function stringArray(value: unknown, path: string, maximum: number, itemMaximum = 1_000) {
  if (!Array.isArray(value) || value.length > maximum) throw new Error(`${path} must be a bounded array`);
  const parsed = value.map((item, index) => text(item, `${path}[${index}]`, itemMaximum));
  const identities = parsed.map((item) => item.normalize("NFKC").toLocaleLowerCase("und").replace(/\s+/gu, " "));
  if (new Set(identities).size !== parsed.length) throw new Error(`${path} must contain unique values`);
  return parsed;
}

function idArray(value: unknown, path: string, maximum = 256) {
  return stringArray(value, path, maximum, 128).map((item, index) => id(item, `${path}[${index}]`));
}

function nullableNumber(value: unknown, path: string) {
  return value === null ? null : finiteNumber(value, path);
}

function scope(value: unknown, path: string): Scope {
  const item = record(value, path);
  exact(item, [
    "location", "time", "formation", "sample_type", "method", "condition", "measurement", "uncertainty", "entities",
  ], path);
  let time: Scope["time"] = null;
  if (item.time !== null) {
    const rawTime = record(item.time, `${path}.time`);
    exact(rawTime, ["label", "start", "end", "unit"], `${path}.time`);
    time = {
      label: nullableText(rawTime.label, `${path}.time.label`, 1_000),
      start: nullableNumber(rawTime.start, `${path}.time.start`),
      end: nullableNumber(rawTime.end, `${path}.time.end`),
      unit: nullableText(rawTime.unit, `${path}.time.unit`, 128),
    };
    if (time.label === null && time.start === null && time.end === null) {
      throw new Error(`${path}.time requires a label or numeric bound`);
    }
    if ((time.start !== null || time.end !== null) && time.unit === null) {
      throw new Error(`${path}.time numeric bounds require a unit`);
    }
    if (time.start !== null && time.end !== null && time.start > time.end) {
      throw new Error(`${path}.time start must not exceed end`);
    }
  }
  return {
    location: stringArray(item.location, `${path}.location`, 64),
    time,
    formation: stringArray(item.formation, `${path}.formation`, 64),
    sample_type: stringArray(item.sample_type, `${path}.sample_type`, 64),
    method: stringArray(item.method, `${path}.method`, 64),
    condition: stringArray(item.condition, `${path}.condition`, 64),
    measurement: stringArray(item.measurement, `${path}.measurement`, 64),
    uncertainty: nullableText(item.uncertainty, `${path}.uncertainty`, 2_000),
    entities: stringArray(item.entities, `${path}.entities`, 128),
  };
}

function normalizedClaim(value: unknown, path: string, userText: string): NormalizedClaimDraft {
  const item = record(value, path);
  exact(item, [
    "id", "origin_span", "canonical_claim", "claim_type", "polarity", "modality",
    "scope", "status", "clarification",
  ], path);
  if (typeof item.origin_span !== "string" || !item.origin_span.trim() || item.origin_span.length > 32_000) {
    throw new Error(`${path}.origin_span must be bounded source text`);
  }
  const originSpan = item.origin_span;
  if (originSpan !== originSpan.normalize("NFC") || !userText.normalize("NFC").includes(originSpan)) {
    throw new Error(`${path}.origin_span must occur verbatim in the submitted text after NFC normalization`);
  }
  const status = enumValue(item.status, new Set(["ready", "needs_user_definition"] as const), `${path}.status`);
  const clarification = nullableText(item.clarification, `${path}.clarification`, 4_000);
  if ((status === "needs_user_definition") !== (clarification !== null)) {
    throw new Error(`${path} clarification must be present exactly for an unresolved claim`);
  }
  const parsed = {
    id: id(item.id, `${path}.id`),
    originSpan,
    canonicalClaim: text(item.canonical_claim, `${path}.canonical_claim`, 8_000),
    claimType: enumValue(item.claim_type, CLAIM_TYPES, `${path}.claim_type`),
    polarity: enumValue(item.polarity, POLARITIES, `${path}.polarity`),
    modality: enumValue(item.modality, MODALITIES, `${path}.modality`),
    scope: scope(item.scope, `${path}.scope`),
    status,
    clarification,
  };
  if (parsed.status === "ready" && !meaningfulScope(parsed.scope)) {
    throw new Error(`${path}.scope must be meaningful for a ready Claim`);
  }
  return parsed;
}

function meaningfulScope(value: Scope) {
  return value.time !== null || value.uncertainty !== null || [
    value.location, value.formation, value.sample_type, value.method,
    value.condition, value.measurement, value.entities,
  ].some((items) => items.length > 0);
}

export function parseClaimComparisonNormalization(
  value: unknown,
  userText: string,
): ClaimComparisonNormalization {
  const source = record(value, "$"), run = record(source.run, "$.run");
  exact(source, ["schema_version", "processor_version", "status", "clarification", "run", "claims"], "$");
  exact(run, ["run_id", "model", "prompt_version", "attempts"], "$.run");
  if (source.schema_version !== CLAIM_COMPARISON_SCHEMA_VERSION
    || source.processor_version !== CLAIM_NORMALIZER_VERSION
    || !isCompilerModel(run.model)
    || run.prompt_version !== CLAIM_NORMALIZER_PROMPT_VERSION) {
    throw new Error("claim normalization provenance is unsupported");
  }
  if (!Array.isArray(source.claims) || source.claims.length > 64) throw new Error("$.claims must be a bounded array");
  const claims = source.claims.map((item, index) => normalizedClaim(item, `$.claims[${index}]`, userText));
  if (new Set(claims.map((claim) => claim.id)).size !== claims.length) throw new Error("normalized Claim IDs must be unique");
  const status = enumValue(source.status, new Set(["ready", "needs_user_definition"] as const), "$.status");
  const clarification = nullableText(source.clarification, "$.clarification", 4_000);
  const expectedStatus = claims.length > 0 && claims.every((claim) => claim.status === "ready")
    ? "ready" : "needs_user_definition";
  if (status !== expectedStatus || (status === "ready") !== (clarification === null)) {
    throw new Error("normalization status and clarification must summarize all Claim drafts");
  }
  return {
    schemaVersion: CLAIM_COMPARISON_SCHEMA_VERSION,
    processorVersion: CLAIM_NORMALIZER_VERSION,
    status,
    clarification,
    run: {
      runId: id(run.run_id, "$.run.run_id"),
      model: run.model,
      promptVersion: CLAIM_NORMALIZER_PROMPT_VERSION,
      attempts: integer(run.attempts, "$.run.attempts", 1, 9),
    },
    claims,
  };
}

function decision(value: unknown, path: string, assessment = false): ClaimComparisonDecision | ClaimComparisonAssessment {
  const item = record(value, path);
  const keys = [
    assessment ? "comparison_id" : "id", "pair_id",
    ...(assessment ? ["verdict"] : []),
    "relation", "direction", "stance", "scope_alignment", "directness",
    "query_evidence_ids", "candidate_evidence_ids", "rationale", "limitations", "confidence",
  ];
  exact(item, keys, path);
  const common = {
    pairId: id(item.pair_id, `${path}.pair_id`),
    relation: enumValue(item.relation, RELATIONS, `${path}.relation`),
    direction: enumValue(item.direction, DIRECTIONS, `${path}.direction`),
    stance: enumValue(item.stance, STANCES, `${path}.stance`),
    scopeAlignment: enumValue(item.scope_alignment, ALIGNMENTS, `${path}.scope_alignment`),
    directness: enumValue(item.directness, DIRECTNESS, `${path}.directness`),
    queryEvidenceIds: idArray(item.query_evidence_ids, `${path}.query_evidence_ids`),
    candidateEvidenceIds: idArray(item.candidate_evidence_ids, `${path}.candidate_evidence_ids`),
    rationale: text(item.rationale, `${path}.rationale`, 4_000),
    limitations: stringArray(item.limitations, `${path}.limitations`, 32, 2_000),
    confidence: finiteNumber(item.confidence, `${path}.confidence`, 0, 1),
  };
  if (assessment) {
    const parsed: ClaimComparisonAssessment = {
      comparisonId: id(item.comparison_id, `${path}.comparison_id`),
      verdict: enumValue(item.verdict, VERDICTS, `${path}.verdict`),
      ...common,
    };
    validateCoreSemantics(parsed, path);
    return parsed;
  }
  const parsed: ClaimComparisonDecision = { id: id(item.id, `${path}.id`), ...common };
  validateCoreSemantics(parsed, path);
  return parsed;
}

function validateCoreSemantics(
  item: ClaimComparisonDecision | ClaimComparisonAssessment,
  path: string,
) {
  const symmetric = new Set<ClaimComparisonRelation>(["equivalent", "contradicts", "supplements"]);
  if (DIRECTED_RELATIONS.has(item.relation) && !["query_to_candidate", "candidate_to_query"].includes(item.direction)) {
    throw new Error(`${path} directed relation requires a directed orientation`);
  }
  if (symmetric.has(item.relation) && item.direction !== "bidirectional") {
    throw new Error(`${path} symmetric relation requires bidirectional orientation`);
  }
  if (!DIRECTED_RELATIONS.has(item.relation) && !symmetric.has(item.relation) && item.direction !== "none") {
    throw new Error(`${path} abstention relation requires direction none`);
  }
  const expectedStance: ClaimComparisonStance = ["equivalent", "supports"].includes(item.relation)
    ? "supportive"
    : item.relation === "contradicts" ? "contradictory"
      : ["qualifies", "refines"].includes(item.relation) ? "qualifying"
        : ["supplements", "no_relation"].includes(item.relation) ? "neutral" : "unclear";
  if (item.stance !== expectedStance) throw new Error(`${path} stance is inconsistent with relation`);
  if (item.relation === "no_relation" && item.directness !== "none") {
    throw new Error(`${path} no_relation requires directness none`);
  }
  if (item.relation === "insufficient_to_determine" && item.directness !== "insufficient") {
    throw new Error(`${path} insufficient relation requires insufficient directness`);
  }
  if (DIRECTED_RELATIONS.has(item.relation) || symmetric.has(item.relation)) {
    if (!["direct", "indirect"].includes(item.directness)) throw new Error(`${path} relation requires evidentiary directness`);
  }
  if (item.relation === "equivalent" && item.scopeAlignment !== "equivalent") {
    throw new Error(`${path} equivalent Claims require equivalent Scope`);
  }
  if (["equivalent", "supports", "contradicts", "qualifies", "refines"].includes(item.relation)
    && ["unknown", "disjoint"].includes(item.scopeAlignment)) {
    throw new Error(`${path} relation requires materially comparable Scope`);
  }
}

function rawDecision(item: ClaimComparisonDecision) {
  return {
    id: item.id,
    pair_id: item.pairId,
    relation: item.relation,
    direction: item.direction,
    stance: item.stance,
    scope_alignment: item.scopeAlignment,
    directness: item.directness,
    query_evidence_ids: item.queryEvidenceIds,
    candidate_evidence_ids: item.candidateEvidenceIds,
    rationale: item.rationale,
    limitations: item.limitations,
    confidence: item.confidence,
  };
}

function sha256(value: unknown, path: string) {
  if (typeof value !== "string" || !SHA256_RE.test(value)) throw new Error(`${path} must be a lowercase SHA-256 digest`);
  return value;
}

export async function parseClaimComparisonEnvelope(
  value: unknown,
  request?: ClaimComparisonAnalyzeRequest,
): Promise<ClaimComparisonEnvelope> {
  const source = record(value, "$"), extraction = record(source.extraction, "$.extraction");
  exact(source, ["schema_version", "processor_version", "project_id", "extraction", "verification"], "$");
  exact(extraction, ["run_id", "model", "prompt_version", "attempts", "comparisons_sha256", "comparisons"], "$.extraction");
  if (source.schema_version !== CLAIM_COMPARISON_SCHEMA_VERSION
    || source.processor_version !== CLAIM_COMPARISON_PROCESSOR_VERSION
    || !isCompilerModel(extraction.model)
    || extraction.prompt_version !== CLAIM_COMPARISON_EXTRACTION_PROMPT_VERSION) {
    throw new Error("claim comparison provenance is unsupported");
  }
  if (!Array.isArray(extraction.comparisons) || extraction.comparisons.length > 256) {
    throw new Error("$.extraction.comparisons must be a bounded array");
  }
  const comparisons = extraction.comparisons.map((item, index) => (
    decision(item, `$.extraction.comparisons[${index}]`) as ClaimComparisonDecision
  ));
  const byId = new Map(comparisons.map((item) => [item.id, item]));
  if (byId.size !== comparisons.length || new Set(comparisons.map((item) => item.pairId)).size !== comparisons.length) {
    throw new Error("comparison and pair identities must be unique");
  }
  const comparisonsSha256 = sha256(extraction.comparisons_sha256, "$.extraction.comparisons_sha256");
  if (await sha256Hex(canonicalStringify(comparisons.map(rawDecision))) !== comparisonsSha256) {
    throw new Error("claim comparison hash does not match the frozen decisions");
  }
  const parsed: ClaimComparisonEnvelope = {
    schemaVersion: CLAIM_COMPARISON_SCHEMA_VERSION,
    processorVersion: CLAIM_COMPARISON_PROCESSOR_VERSION,
    projectId: id(source.project_id, "$.project_id"),
    extraction: {
      runId: id(extraction.run_id, "$.extraction.run_id"),
      model: extraction.model,
      promptVersion: CLAIM_COMPARISON_EXTRACTION_PROMPT_VERSION,
      attempts: integer(extraction.attempts, "$.extraction.attempts", comparisons.length === 0 ? 0 : 1, 9),
      comparisonsSha256,
      comparisons,
    },
    verification: null,
  };
  if (source.verification !== null) {
    const verification = record(source.verification, "$.verification");
    exact(verification, ["run_id", "model", "prompt_version", "attempts", "extraction_sha256", "assessments"], "$.verification");
    if (!isCompilerModel(verification.model)
      || verification.prompt_version !== CLAIM_COMPARISON_VERIFICATION_PROMPT_VERSION
      || !Array.isArray(verification.assessments)
      || verification.assessments.length > 256) {
      throw new Error("claim comparison verification provenance is unsupported");
    }
    const assessments = verification.assessments.map((item, index) => (
      decision(item, `$.verification.assessments[${index}]`, true) as ClaimComparisonAssessment
    ));
    const extractionSha256 = sha256(verification.extraction_sha256, "$.verification.extraction_sha256");
    if (extractionSha256 !== comparisonsSha256) throw new Error("claim comparison verification hash mismatch");
    const assessed = assessments.map((item) => item.comparisonId);
    if (assessed.length !== comparisons.length || new Set(assessed).size !== assessed.length
      || assessed.some((comparisonId, index) => comparisonId !== comparisons[index]?.id)) {
      throw new Error("claim comparison verifier must assess every decision exactly once");
    }
    parsed.verification = {
      runId: id(verification.run_id, "$.verification.run_id"),
      model: verification.model,
      promptVersion: CLAIM_COMPARISON_VERIFICATION_PROMPT_VERSION,
      attempts: integer(verification.attempts, "$.verification.attempts", 1, 9),
      extractionSha256,
      assessments,
    };
  }
  if ((comparisons.length === 0) !== (parsed.verification === null)) {
    throw new Error("verification must be present exactly when comparison decisions exist");
  }
  if ((comparisons.length === 0) !== (parsed.extraction.attempts === 0)) {
    throw new Error("zero comparison decisions must be the only zero-attempt extraction");
  }
  if (request) validateEnvelopeAgainstRequest(parsed, request);
  return parsed;
}

function validateEnvelopeAgainstRequest(envelope: ClaimComparisonEnvelope, request: ClaimComparisonAnalyzeRequest) {
  if (envelope.projectId !== request.project_id) throw new Error("claim comparison response project mismatch");
  const requestPairs = new Map(request.pairs.map((pair) => [pair.id, pair]));
  if (requestPairs.size !== request.pairs.length
    || envelope.extraction.comparisons.length !== request.pairs.length
    || envelope.extraction.comparisons.some((item, index) => item.pairId !== request.pairs[index]?.id)) {
    throw new Error("claim comparison response must cover every requested pair exactly once");
  }
  const decisions = new Map(envelope.extraction.comparisons.map((item) => [item.id, item]));
  for (const item of envelope.extraction.comparisons) {
    const pair = requestPairs.get(item.pairId)!;
    validateDecisionEvidence(item, pair, "claim comparison decision");
  }
  for (const item of envelope.verification?.assessments ?? []) {
    const frozen = decisions.get(item.comparisonId)!;
    const pair = requestPairs.get(item.pairId);
    if (!pair || frozen.pairId !== item.pairId) throw new Error("claim comparison assessment pair mismatch");
    validateDecisionEvidence(item, pair, "claim comparison assessment");
    if (item.queryEvidenceIds.some((entry) => !frozen.queryEvidenceIds.includes(entry))
      || item.candidateEvidenceIds.some((entry) => !frozen.candidateEvidenceIds.includes(entry))) {
      throw new Error("claim comparison verifier selected evidence outside the frozen decision");
    }
    if (item.confidence > frozen.confidence) throw new Error("claim comparison verifier strengthened confidence");
    if (item.verdict === "maintained" && (
      item.relation !== frozen.relation || item.direction !== frozen.direction || item.stance !== frozen.stance
      || item.scopeAlignment !== frozen.scopeAlignment || item.directness !== frozen.directness
    )) {
      throw new Error("maintained assessment changed the frozen decision");
    }
    if (item.verdict === "downgraded" && !DOWNGRADES[frozen.relation].has(item.relation)) {
      throw new Error("downgraded assessment did not publish an allowed weaker relation");
    }
    if (item.verdict === "abstained" && item.relation !== "insufficient_to_determine") {
      throw new Error("abstained assessment must publish insufficient_to_determine");
    }
    if (DIRECTED_RELATIONS.has(frozen.relation) && DIRECTED_RELATIONS.has(item.relation)
      && item.direction !== frozen.direction) {
      throw new Error("claim comparison verifier reversed a directed relation");
    }
    if (DIRECTNESS_STRENGTH[item.directness] > DIRECTNESS_STRENGTH[frozen.directness]) {
      throw new Error("claim comparison verifier strengthened directness");
    }
    if (SCOPE_STRENGTH[item.scopeAlignment] > SCOPE_STRENGTH[frozen.scopeAlignment]) {
      throw new Error("claim comparison verifier strengthened Scope alignment");
    }
  }
}

function validateDecisionEvidence(
  item: ClaimComparisonDecision | ClaimComparisonAssessment,
  pair: ClaimComparisonAnalyzeRequest["pairs"][number],
  label: string,
) {
  if ([pair.query, pair.candidate].some((side) => side.evidence_completeness?.status === "truncated")
    && item.relation !== "insufficient_to_determine") {
    throw new Error(`${label} cannot classify a truncated recorded argument as sufficient`);
  }
  const query = new Map(pair.query.evidence_entities.map((entity) => [entity.id, entity]));
  const candidate = new Map(pair.candidate.evidence_entities.map((entity) => [entity.id, entity]));
  if (item.queryEvidenceIds.some((entry) => !query.has(entry))
    || item.candidateEvidenceIds.some((entry) => !candidate.has(entry))) {
    throw new Error(`${label} selected evidence outside the submitted pair`);
  }
  if (["supports", "contradicts", "equivalent"].includes(item.relation)
    && !item.candidateEvidenceIds.some((entry) => RAW_EVIDENCE_KINDS.has(candidate.get(entry)?.kind ?? ""))) {
    throw new Error(`${label} requires raw candidate evidence for a strong relation`);
  }
  if (item.scopeAlignment === "disjoint" && item.relation === "contradicts") {
    throw new Error(`${label} cannot call disjoint scopes contradictory`);
  }
  if (item.relation === "equivalent" && pair.query.polarity !== pair.candidate.polarity) {
    throw new Error(`${label} cannot call opposite Claim polarity equivalent`);
  }
}
