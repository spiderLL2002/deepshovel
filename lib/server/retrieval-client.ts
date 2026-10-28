import {
  EVIDENCE_RETRIEVAL_INDEX_VERSION,
  EVIDENCE_RETRIEVAL_SEARCH_PROTOCOL_VERSION,
  EVIDENCE_RETRIEVAL_CONTEXT_PROTOCOL_VERSION,
  isValidQueryPlanHandoff,
  isValidContextClarificationToken,
  isValidContextToken,
  parseConversationHistory,
  parseRetrievalContextText,
  type EvidenceRetrievalDocumentDto,
  type EvidenceRetrievalManifestDto,
  type EvidenceRetrievalSearchRequestDto,
  type RetrievalDeleteResponse,
  type RetrievalIndexResponse,
  type RetrievalManifestStatus,
  type RetrievalManifestState,
  type RetrievalMemberRef,
  type RetrievalPreviewRef,
  type RetrievalQueryPlan,
  type RetrievalClarification,
  type RetrievalSearchResponse,
  type RetrievalStageDiagnostics,
} from "../evidence-retrieval/index.ts";
import { ApiError } from "./evidence-store.ts";
import {
  CLAIM_POLARITIES as POLARITIES,
  CLAIM_TYPES,
  RETRIEVAL_MAX_INDEX_DOCUMENTS,
  RETRIEVAL_MAX_MANIFESTS,
} from "../shared-contract.generated.ts";
import { containsCredential, readBoundedJson } from "./service-response.ts";

const RETRIEVAL_ID_RE = /^[A-Za-z][A-Za-z0-9._:-]{0,127}$/;
const SHARED_SECRET_RE = /^[\x21-\x7e]{32,512}$/;
const SHA256_RE = /^[0-9a-f]{64}$/;
const UPSTREAM_ERROR_CODE_RE = /^[A-Z][A-Z0-9_]{0,79}$/;
const MAX_RESPONSE_BYTES = 8 * 1024 * 1024;
const MAX_QUERY_LENGTH = 8_000;
const MAX_WARNINGS = 32;

// Transport chunk limits shared with the Compiler (contracts/shared-contract.json).
export { RETRIEVAL_MAX_INDEX_DOCUMENTS, RETRIEVAL_MAX_MANIFESTS } from "../shared-contract.generated.ts";

export type RetrievalClientConfig = {
  baseUrl: string;
  sharedSecret: string;
  fetch?: typeof fetch;
  interactive?: boolean;
};

export type ClaimRelationBatchProgress = {
  item_offset?: number;
  next_cursor?: number;
  retired?: boolean;
  schema_version: typeof EVIDENCE_RETRIEVAL_INDEX_VERSION;
  batch_id: string;
  status: "running" | "completed" | "cancelled";
  reprocess: boolean;
  total: number;
  completed: number;
  succeeded: number;
  failed: number;
  items: Array<{
    query_claim: { snapshot_id: string; claim_id: string };
    status: "succeeded" | "failed";
    response: RetrievalSearchResponse | null;
    error_code: string | null;
  }>;
};

function objectValue(value: unknown, path: string) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw invalidResponse(`${path} must be an object`);
  }
  return value as Record<string, unknown>;
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[], path: string) {
  const unknown = Object.keys(value).filter((key) => !keys.includes(key));
  const missing = keys.filter((key) => !(key in value));
  if (unknown.length || missing.length) throw invalidResponse(`${path} has an invalid shape`);
}

function allowedKeys(value: Record<string, unknown>, keys: readonly string[], path: string) {
  if (Object.keys(value).some((key) => !keys.includes(key))) {
    throw invalidResponse(`${path} has an invalid shape`);
  }
}

function invalidResponse(message: string) {
  return new ApiError(502, "INVALID_RETRIEVAL_RESPONSE", message);
}

function textValue(value: unknown, path: string, maximum: number, allowEmpty = false) {
  if (typeof value !== "string") {
    throw invalidResponse(`${path} must be a${allowEmpty ? "" : " non-empty"} bounded string`);
  }
  const normalized = allowEmpty ? value : value.trim();
  if (Array.from(normalized).length > maximum || (!allowEmpty && !normalized)) {
    throw invalidResponse(`${path} must be a${allowEmpty ? "" : " non-empty"} bounded string`);
  }
  return normalized;
}

function idValue(value: unknown, path: string) {
  const id = textValue(value, path, 128);
  if (!RETRIEVAL_ID_RE.test(id)) throw invalidResponse(`${path} must be a valid retrieval identifier`);
  return id;
}

function integerValue(value: unknown, path: string, maximum = Number.MAX_SAFE_INTEGER) {
  if (!Number.isSafeInteger(value) || (value as number) < 0 || (value as number) > maximum) {
    throw invalidResponse(`${path} must be a bounded non-negative integer`);
  }
  return value as number;
}

function finiteValue(value: unknown, path: string, minimum?: number, maximum?: number) {
  if (typeof value !== "number" || !Number.isFinite(value)
    || (minimum !== undefined && value < minimum)
    || (maximum !== undefined && value > maximum)) {
    throw invalidResponse(`${path} must be a bounded finite number`);
  }
  return value;
}

function nullableScore(value: unknown, path: string) {
  return value === null ? null : finiteValue(value, path);
}

function enumValue<const T extends readonly string[]>(value: unknown, allowed: T, path: string): T[number] {
  if (typeof value !== "string" || !allowed.includes(value)) {
    throw invalidResponse(`${path} has an unsupported value`);
  }
  return value as T[number];
}

function stringList(value: unknown, path: string, maximumItems: number, maximumLength: number) {
  if (!Array.isArray(value) || value.length > maximumItems) {
    throw invalidResponse(`${path} must be a bounded array`);
  }
  return value.map((item, index) => textValue(item, `${path}[${index}]`, maximumLength));
}

function warningsValue(value: unknown, path = "$.warnings") {
  return stringList(value, path, MAX_WARNINGS, 500);
}

function schemaValue(value: unknown) {
  if (value !== EVIDENCE_RETRIEVAL_INDEX_VERSION) {
    throw new ApiError(
      502,
      "RETRIEVAL_PROTOCOL_MISMATCH",
      `Retrieval service must use ${EVIDENCE_RETRIEVAL_INDEX_VERSION}`,
    );
  }
  return EVIDENCE_RETRIEVAL_INDEX_VERSION;
}

function searchSchemaValue(value: unknown) {
  if (value !== EVIDENCE_RETRIEVAL_INDEX_VERSION
    && value !== EVIDENCE_RETRIEVAL_SEARCH_PROTOCOL_VERSION
    && value !== EVIDENCE_RETRIEVAL_CONTEXT_PROTOCOL_VERSION) {
    throw new ApiError(
      502,
      "RETRIEVAL_PROTOCOL_MISMATCH",
      `Retrieval search service must use ${EVIDENCE_RETRIEVAL_INDEX_VERSION}, ${EVIDENCE_RETRIEVAL_SEARCH_PROTOCOL_VERSION}, or ${EVIDENCE_RETRIEVAL_CONTEXT_PROTOCOL_VERSION}`,
    );
  }
  return value;
}

function parseManifestState<const Status extends RetrievalManifestStatus>(
  value: unknown,
  path: string,
  statuses: readonly Status[],
): RetrievalManifestState<Status> {
  const state = objectValue(value, path);
  exactKeys(state, [
    "snapshot_id", "status", "indexed_document_count", "document_count", "manifest_sha256", "dense_complete",
  ], path);
  const status = enumValue(state.status, statuses, `${path}.status`);
  const manifestSha = state.manifest_sha256 === null
    ? null
    : textValue(state.manifest_sha256, `${path}.manifest_sha256`, 64);
  if (manifestSha !== null && !SHA256_RE.test(manifestSha)) {
    throw invalidResponse(`${path}.manifest_sha256 must be a lowercase SHA-256`);
  }
  if (status !== "missing" && manifestSha === null) {
    throw invalidResponse(`${path}.manifest_sha256 may be null only for a missing manifest`);
  }
  if (typeof state.dense_complete !== "boolean") {
    throw invalidResponse(`${path}.dense_complete must be boolean`);
  }
  const documentCount = integerValue(state.document_count, `${path}.document_count`);
  const indexedDocumentCount = integerValue(
    state.indexed_document_count,
    `${path}.indexed_document_count`,
    documentCount,
  );
  if ((status === "indexed" || status === "unchanged") && indexedDocumentCount !== documentCount) {
    throw invalidResponse(`${path} reports a complete status for an incomplete snapshot`);
  }
  if ((status === "indexed" || status === "unchanged") && !state.dense_complete) {
    throw invalidResponse(`${path} reports a dense-complete status without complete vectors`);
  }
  if (status === "degraded" && state.dense_complete) {
    throw invalidResponse(`${path} reports degraded status with complete vectors`);
  }
  if (status === "missing" && (indexedDocumentCount !== 0 || manifestSha !== null || state.dense_complete)) {
    throw invalidResponse(`${path} reports state for a missing manifest`);
  }
  return {
    snapshot_id: idValue(state.snapshot_id, `${path}.snapshot_id`),
    status,
    indexed_document_count: indexedDocumentCount,
    document_count: documentCount,
    manifest_sha256: manifestSha,
    dense_complete: state.dense_complete,
  };
}

function parseManifestEnvelope<const Status extends RetrievalManifestStatus>(
  value: unknown,
  statuses: readonly Status[],
  expectedSnapshotIds: readonly string[],
) {
  const envelope = objectValue(value, "$");
  exactKeys(envelope, ["schema_version", "manifests"], "$");
  const schemaVersion = schemaValue(envelope.schema_version);
  if (!Array.isArray(envelope.manifests) || envelope.manifests.length > RETRIEVAL_MAX_MANIFESTS) {
    throw invalidResponse("$.manifests must be a bounded array");
  }
  const manifests = envelope.manifests.map((item, index) => parseManifestState(item, `$.manifests[${index}]`, statuses));
  assertExactSnapshotSet(manifests.map((item) => item.snapshot_id), expectedSnapshotIds, "$.manifests");
  return { schema_version: schemaVersion, manifests };
}

function assertExactSnapshotSet(actual: readonly string[], expected: readonly string[], path: string) {
  const actualSet = new Set(actual);
  const expectedSet = new Set(expected);
  if (actualSet.size !== actual.length || expectedSet.size !== expected.length
    || actualSet.size !== expectedSet.size || [...actualSet].some((id) => !expectedSet.has(id))) {
    throw invalidResponse(`${path} does not match the requested snapshots`);
  }
}

function parseStage(value: unknown, path: string): RetrievalStageDiagnostics {
  const stage = objectValue(value, path);
  exactKeys(stage, ["attempted", "status", "provider", "returned"], path);
  if (typeof stage.attempted !== "boolean") throw invalidResponse(`${path}.attempted must be boolean`);
  return {
    attempted: stage.attempted,
    status: enumValue(stage.status, ["ok", "degraded", "skipped"] as const, `${path}.status`),
    provider: textValue(stage.provider, `${path}.provider`, 200),
    returned: integerValue(stage.returned, `${path}.returned`, 500),
  };
}

const RELATIONS = [
  "equivalent", "supports", "contradicts", "qualifies", "refines", "supplements", "related", "unrelated",
] as const;
const SCOPE_RELATIONS = ["equivalent", "overlap", "query_within_candidate", "candidate_within_query", "disjoint", "unknown"] as const;
const RETRIEVAL_POLICY_INAPPLICABLE_REASONS = [
  "explicit_constraints",
  "query_claim",
  "restricted_corpus",
  "no_active_snapshots",
  "lexical_channel_unavailable",
  "dense_channel_unavailable",
  "invalid_dense_scores",
] as const;
const RETRIEVAL_STRONG_SIGNALS = [
  "exact_anchor", "concept_anchor", "scientific_anchor",
] as const;

function approximatelyEqual(left: number, right: number, relative = 1e-9, absolute = 1e-12) {
  return Math.abs(left - right) <= Math.max(
    absolute,
    relative * Math.max(Math.abs(left), Math.abs(right)),
  );
}

function parseRetrievalConfidence(
  value: unknown,
  path: string,
): NonNullable<RetrievalSearchResponse["retrieval_confidence"]> {
  const observation = objectValue(value, path);
  exactKeys(observation, [
    "mode", "decision", "would_abstain", "applicability", "policy_inapplicable_reasons",
    "query_profile", "long_query_truncated", "direct_lexical_query_tokens", "direct_lexical_overlap",
    "direct_lexical_coverage", "exact_anchor_hits", "concept_anchor_hits", "scientific_anchor_hits",
    "strong_signal_types", "dense_observed_hits", "dense_top1", "dense_top2", "dense_margin",
    "dense_top3_mean",
  ], path);
  if (observation.would_abstain !== null) {
    throw invalidResponse(`${path}.would_abstain must remain null in observe mode`);
  }
  if (typeof observation.long_query_truncated !== "boolean") {
    throw invalidResponse(`${path}.long_query_truncated must be boolean`);
  }

  if (!Array.isArray(observation.policy_inapplicable_reasons)
    || observation.policy_inapplicable_reasons.length > 8) {
    throw invalidResponse(`${path}.policy_inapplicable_reasons must be a bounded array`);
  }
  const reasons = observation.policy_inapplicable_reasons.map((item, index) => enumValue(
    item,
    RETRIEVAL_POLICY_INAPPLICABLE_REASONS,
    `${path}.policy_inapplicable_reasons[${index}]`,
  ));
  if (new Set(reasons).size !== reasons.length) {
    throw invalidResponse(`${path}.policy_inapplicable_reasons must be unique`);
  }
  const applicability = enumValue(
    observation.applicability,
    ["applicable", "policy_inapplicable"] as const,
    `${path}.applicability`,
  );
  if ((applicability === "applicable" && reasons.length > 0)
    || (applicability === "policy_inapplicable" && reasons.length === 0)) {
    throw invalidResponse(`${path}.applicability is inconsistent with its reasons`);
  }

  const directLexicalQueryTokens = integerValue(
    observation.direct_lexical_query_tokens,
    `${path}.direct_lexical_query_tokens`,
    256,
  );
  const directLexicalOverlap = integerValue(
    observation.direct_lexical_overlap,
    `${path}.direct_lexical_overlap`,
    256,
  );
  if (directLexicalOverlap > directLexicalQueryTokens) {
    throw invalidResponse(`${path}.direct_lexical_overlap cannot exceed query tokens`);
  }
  const directLexicalCoverage = finiteValue(
    observation.direct_lexical_coverage,
    `${path}.direct_lexical_coverage`,
    0,
    1,
  );
  const expectedCoverage = directLexicalQueryTokens === 0
    ? 0
    : directLexicalOverlap / directLexicalQueryTokens;
  if (!approximatelyEqual(directLexicalCoverage, expectedCoverage)) {
    throw invalidResponse(`${path}.direct_lexical_coverage does not match its token counts`);
  }

  const exactAnchorHits = integerValue(observation.exact_anchor_hits, `${path}.exact_anchor_hits`, 8);
  const conceptAnchorHits = integerValue(observation.concept_anchor_hits, `${path}.concept_anchor_hits`, 8_192);
  const scientificAnchorHits = integerValue(
    observation.scientific_anchor_hits,
    `${path}.scientific_anchor_hits`,
    8_192,
  );
  if (!Array.isArray(observation.strong_signal_types) || observation.strong_signal_types.length > 3) {
    throw invalidResponse(`${path}.strong_signal_types must be a bounded array`);
  }
  const strongSignalTypes = observation.strong_signal_types.map((item, index) => enumValue(
    item,
    RETRIEVAL_STRONG_SIGNALS,
    `${path}.strong_signal_types[${index}]`,
  ));
  if (new Set(strongSignalTypes).size !== strongSignalTypes.length) {
    throw invalidResponse(`${path}.strong_signal_types must be unique`);
  }
  const expectedSignals = [
    exactAnchorHits > 0 ? "exact_anchor" : null,
    conceptAnchorHits > 0 ? "concept_anchor" : null,
    scientificAnchorHits > 0 ? "scientific_anchor" : null,
  ].filter((item): item is typeof RETRIEVAL_STRONG_SIGNALS[number] => item !== null);
  if (strongSignalTypes.length !== expectedSignals.length
    || strongSignalTypes.some((item) => !expectedSignals.includes(item))) {
    throw invalidResponse(`${path}.strong_signal_types do not match anchor hit counts`);
  }

  const denseObservedHits = integerValue(
    observation.dense_observed_hits,
    `${path}.dense_observed_hits`,
    3,
  );
  const denseTop1 = observation.dense_top1 === null
    ? null
    : finiteValue(observation.dense_top1, `${path}.dense_top1`, -2, 2);
  const denseTop2 = observation.dense_top2 === null
    ? null
    : finiteValue(observation.dense_top2, `${path}.dense_top2`, -2, 2);
  const denseMargin = observation.dense_margin === null
    ? null
    : finiteValue(observation.dense_margin, `${path}.dense_margin`, 0, 4);
  const denseTop3Mean = observation.dense_top3_mean === null
    ? null
    : finiteValue(observation.dense_top3_mean, `${path}.dense_top3_mean`, -2, 2);
  if (denseObservedHits === 0
    && [denseTop1, denseTop2, denseMargin, denseTop3Mean].some((item) => item !== null)) {
    throw invalidResponse(`${path} cannot report scores without dense observations`);
  }
  if (denseObservedHits > 0 && (denseTop1 === null || denseTop3Mean === null)) {
    throw invalidResponse(`${path} dense observations require top1 and top3 mean`);
  }
  if (denseObservedHits === 1 && (denseTop2 !== null || denseMargin !== null)) {
    throw invalidResponse(`${path} one dense observation cannot report top2 or margin`);
  }
  if (denseObservedHits >= 2 && (denseTop2 === null || denseMargin === null)) {
    throw invalidResponse(`${path} two or more dense observations require top2 and margin`);
  }

  return {
    mode: enumValue(observation.mode, ["observe"] as const, `${path}.mode`),
    decision: enumValue(observation.decision, ["return"] as const, `${path}.decision`),
    would_abstain: null,
    applicability,
    policy_inapplicable_reasons: reasons,
    query_profile: enumValue(observation.query_profile, ["topic", "claim"] as const, `${path}.query_profile`),
    long_query_truncated: observation.long_query_truncated,
    direct_lexical_query_tokens: directLexicalQueryTokens,
    direct_lexical_overlap: directLexicalOverlap,
    direct_lexical_coverage: directLexicalCoverage,
    exact_anchor_hits: exactAnchorHits,
    concept_anchor_hits: conceptAnchorHits,
    scientific_anchor_hits: scientificAnchorHits,
    strong_signal_types: strongSignalTypes,
    dense_observed_hits: denseObservedHits,
    dense_top1: denseTop1,
    dense_top2: denseTop2,
    dense_margin: denseMargin,
    dense_top3_mean: denseTop3Mean,
  };
}

function parseScopeConstraints(
  value: unknown,
  path: string,
): NonNullable<RetrievalQueryPlan["scope_constraints"]> {
  const scope = objectValue(value, path);
  const listKeys = ["location", "formation", "sample_type", "method", "condition", "measurement", "entities"] as const;
  allowedKeys(scope, [...listKeys, "time", "uncertainty"], path);
  const output: Record<string, unknown> = {};
  let meaningful = false;
  for (const key of listKeys) {
    if (!(key in scope)) continue;
    const values = stringList(scope[key], `${path}.${key}`, 64, 1_000);
    output[key] = values;
    meaningful ||= values.length > 0;
  }
  if ("uncertainty" in scope) {
    output.uncertainty = scope.uncertainty === null
      ? null
      : textValue(scope.uncertainty, `${path}.uncertainty`, 1_000);
    meaningful ||= scope.uncertainty !== null;
  }
  if ("time" in scope && scope.time === null) output.time = null;
  if ("time" in scope && scope.time !== null) {
    const time = objectValue(scope.time, `${path}.time`);
    exactKeys(time, ["label", "start", "end", "unit"], `${path}.time`);
    const label = time.label === null ? null : textValue(time.label, `${path}.time.label`, 1_000);
    const start = time.start === null ? null : finiteValue(time.start, `${path}.time.start`);
    const end = time.end === null ? null : finiteValue(time.end, `${path}.time.end`);
    const unit = time.unit === null ? null : textValue(time.unit, `${path}.time.unit`, 1_000);
    if (label === null && start === null && end === null) {
      throw invalidResponse(`${path}.time requires a label or numeric bound`);
    }
    if ((start !== null || end !== null) && unit === null) {
      throw invalidResponse(`${path}.time.unit is required for numeric bounds`);
    }
    if (start !== null && end !== null && start > end) {
      throw invalidResponse(`${path}.time.start must not exceed end`);
    }
    output.time = { label, start, end, unit };
    meaningful = true;
  }
  if (!meaningful) throw invalidResponse(`${path} must contain at least one constraint`);
  return output as NonNullable<RetrievalQueryPlan["scope_constraints"]>;
}

function parseMemberRef(value: unknown, path: string, activeSnapshots: ReadonlySet<string>): RetrievalMemberRef {
  const member = objectValue(value, path);
  exactKeys(member, [
    "claim_id", "snapshot_id", "paper_id", "paper_family_id", "relation", "relation_confidence",
    "relation_direction", "scope_relation", "scores",
  ], path);
  const snapshotId = idValue(member.snapshot_id, `${path}.snapshot_id`);
  if (!activeSnapshots.has(snapshotId)) throw invalidResponse(`${path}.snapshot_id is outside the active corpus`);
  const scores = objectValue(member.scores, `${path}.scores`);
  exactKeys(scores, ["lexical", "dense", "rrf", "rerank"], `${path}.scores`);
  return {
    claim_id: idValue(member.claim_id, `${path}.claim_id`),
    snapshot_id: snapshotId,
    paper_id: idValue(member.paper_id, `${path}.paper_id`),
    paper_family_id: idValue(member.paper_family_id, `${path}.paper_family_id`),
    relation: enumValue(member.relation, RELATIONS, `${path}.relation`),
    relation_confidence: member.relation_confidence === null
      ? null
      : finiteValue(member.relation_confidence, `${path}.relation_confidence`, 0, 1),
    relation_direction: enumValue(
      member.relation_direction,
      ["query_to_result", "result_to_query"] as const,
      `${path}.relation_direction`,
    ),
    scope_relation: enumValue(member.scope_relation, SCOPE_RELATIONS, `${path}.scope_relation`),
    scores: {
      lexical: nullableScore(scores.lexical, `${path}.scores.lexical`),
      dense: nullableScore(scores.dense, `${path}.scores.dense`),
      rrf: finiteValue(scores.rrf, `${path}.scores.rrf`, 0),
      rerank: nullableScore(scores.rerank, `${path}.scores.rerank`),
    },
  };
}

function parsePreviewRef(value: unknown, path: string, activeSnapshots: ReadonlySet<string>): RetrievalPreviewRef {
  const member = objectValue(value, path);
  exactKeys(member, ["claim_id", "snapshot_id", "paper_id", "paper_family_id", "scores"], path);
  const snapshotId = idValue(member.snapshot_id, `${path}.snapshot_id`);
  if (!activeSnapshots.has(snapshotId)) throw invalidResponse(`${path}.snapshot_id is outside the active corpus`);
  const scores = objectValue(member.scores, `${path}.scores`);
  exactKeys(scores, ["lexical", "dense", "rrf", "rerank"], `${path}.scores`);
  return {
    claim_id: idValue(member.claim_id, `${path}.claim_id`),
    snapshot_id: snapshotId,
    paper_id: idValue(member.paper_id, `${path}.paper_id`),
    paper_family_id: idValue(member.paper_family_id, `${path}.paper_family_id`),
    scores: {
      lexical: nullableScore(scores.lexical, `${path}.scores.lexical`),
      dense: nullableScore(scores.dense, `${path}.scores.dense`),
      rrf: finiteValue(scores.rrf, `${path}.scores.rrf`, 0),
      rerank: nullableScore(scores.rerank, `${path}.scores.rerank`),
    },
  };
}

function contextResponseText(value: unknown, path: string) {
  try { return parseRetrievalContextText(value, path); }
  catch { throw invalidResponse(`${path} must preserve bounded NFC Unicode text`); }
}

function clarificationText(value: unknown, path: string, maximum: number, nfc = false) {
  const text = textValue(value, path, maximum);
  // Never silently normalize the text bound into a signed clarification choice.
  if (text !== value || (nfc && text.normalize("NFC") !== text)) {
    throw invalidResponse(`${path} must preserve normalized bounded text`);
  }
  return text;
}

function parseClarification(value: unknown): RetrievalClarification {
  const path = "$.clarification";
  const item = objectValue(value, path);
  exactKeys(item, ["question", "options", "clarification_token"], path);
  if (!isValidContextClarificationToken(item.clarification_token)) {
    throw invalidResponse(`${path}.clarification_token must be a bounded c2 token`);
  }
  if (!Array.isArray(item.options) || item.options.length < 2 || item.options.length > 4) {
    throw invalidResponse(`${path}.options requires 2-4 choices`);
  }
  const options = item.options.map((raw, index) => {
    const optionPath = `${path}.options[${index}]`;
    const option = objectValue(raw, optionPath);
    exactKeys(option, ["option_id", "label", "resolved_query"], optionPath);
    if (typeof option.option_id !== "string" || !/^[A-Za-z0-9_-]{1,32}$/u.test(option.option_id)) {
      throw invalidResponse(`${optionPath}.option_id must be a bounded option identifier`);
    }
    return {
      option_id: option.option_id,
      label: clarificationText(option.label, `${optionPath}.label`, 200),
      resolved_query: clarificationText(option.resolved_query, `${optionPath}.resolved_query`, MAX_QUERY_LENGTH, true),
    };
  });
  if (new Set(options.map((option) => option.option_id)).size !== options.length
    || new Set(options.map((option) => option.resolved_query)).size !== options.length) {
    throw invalidResponse(`${path}.options requires unique identifiers and resolved queries`);
  }
  return { question: clarificationText(item.question, `${path}.question`, 1_000),
    options, clarification_token: item.clarification_token };
}

export function parseRetrievalSearchResponse(
  value: unknown,
  activeSnapshotIds: readonly string[],
): RetrievalSearchResponse {
  const envelope = objectValue(value, "$");
  const schemaVersion = searchSchemaValue(envelope.schema_version);
  const contextProtocol = schemaVersion === EVIDENCE_RETRIEVAL_CONTEXT_PROTOCOL_VERSION;
  const stage = enumValue(envelope.stage, ["preview", "ai_rerank"] as const, "$.stage");
  const baseKeys = [
    "schema_version", "stage", "result_status", "query_plan", "channels", "preview", "clusters",
    "warnings", "retrieval_confidence",
  ] as const;
  if (contextProtocol
    && (envelope.result_status === "clarification_required" || envelope.result_status === "needs_context")) {
    const needsContext = envelope.result_status === "needs_context";
    exactKeys(envelope, [...baseKeys, needsContext ? "needs_context" : "clarification"], "$");
    if (stage !== "preview" || envelope.query_plan !== null || envelope.retrieval_confidence !== null
      || !Array.isArray(envelope.preview) || envelope.preview.length !== 0
      || !Array.isArray(envelope.clusters) || envelope.clusters.length !== 0) {
      throw invalidResponse("Clarification requires an empty preview without a plan, handoff or confidence");
    }
    const channels = objectValue(envelope.channels, "$.channels");
    exactKeys(channels, ["lexical", "dense", "rerank", "relation"], "$.channels");
    const parsedChannels = {
      lexical: parseStage(channels.lexical, "$.channels.lexical"),
      dense: parseStage(channels.dense, "$.channels.dense"),
      rerank: parseStage(channels.rerank, "$.channels.rerank"),
      relation: parseStage(channels.relation, "$.channels.relation"),
    };
    if (Object.values(parsedChannels).some((channel) => channel.attempted || channel.status !== "skipped" || channel.returned !== 0)) {
      throw invalidResponse("Clarification must precede every retrieval channel");
    }
    const pending = {
      stage, query_plan: null,
      channels: parsedChannels, preview: [], clusters: [],
      warnings: warningsValue(envelope.warnings), retrieval_confidence: null };
    if (needsContext) {
      const context = objectValue(envelope.needs_context, "$.needs_context");
      exactKeys(context, ["question", "context_token"], "$.needs_context");
      if (!isValidContextToken(context.context_token)) throw invalidResponse("$.needs_context.context_token must be a bounded n1 token");
      const question = contextResponseText(context.question, "$.needs_context.question");
      if (Array.from(question).length > 1_000) throw invalidResponse("$.needs_context.question must contain at most 1000 code points");
      return { ...pending, schema_version: EVIDENCE_RETRIEVAL_CONTEXT_PROTOCOL_VERSION,
        result_status: "needs_context", needs_context: {
          question,
          context_token: context.context_token,
        } };
    }
    return { ...pending, schema_version: EVIDENCE_RETRIEVAL_CONTEXT_PROTOCOL_VERSION,
      result_status: "clarification_required", clarification: parseClarification(envelope.clarification) };
  }
  if (schemaVersion !== EVIDENCE_RETRIEVAL_INDEX_VERSION && stage === "preview") {
    exactKeys(envelope, [...baseKeys, "query_plan_handoff"], "$");
    if (!isValidQueryPlanHandoff(envelope.query_plan_handoff)) {
      throw invalidResponse("$.query_plan_handoff has an invalid transport shape");
    }
  } else {
    exactKeys(envelope, [...baseKeys], "$");
  }
  const resultStatus = enumValue(
    envelope.result_status,
    ["matches", "no_direct_answer", "degraded"] as const,
    "$.result_status",
  );
  const retrievalConfidence = envelope.retrieval_confidence === null
    ? null
    : parseRetrievalConfidence(envelope.retrieval_confidence, "$.retrieval_confidence");
  if (retrievalConfidence !== null && stage !== "preview") {
    throw invalidResponse("$.retrieval_confidence is preview-only telemetry");
  }
  const queryPlan = objectValue(envelope.query_plan, "$.query_plan");
  exactKeys(queryPlan, [
    "query_type", "original_query", "canonical_claim", "must_terms", "optional_terms", "exclude_terms",
    "entities", "polarity", "claim_types", "scope_constraints", "planner",
    ...(contextProtocol ? ["resolved_query"] : []),
  ], "$.query_plan");
  const channels = objectValue(envelope.channels, "$.channels");
  exactKeys(channels, ["lexical", "dense", "rerank", "relation"], "$.channels");
  const parsedChannels = {
    lexical: parseStage(channels.lexical, "$.channels.lexical"),
    dense: parseStage(channels.dense, "$.channels.dense"),
    rerank: parseStage(channels.rerank, "$.channels.rerank"),
    relation: parseStage(channels.relation, "$.channels.relation"),
  };
  const scopeConstraints: RetrievalQueryPlan["scope_constraints"] =
    queryPlan.scope_constraints === null
      ? null
      : parseScopeConstraints(queryPlan.scope_constraints, "$.query_plan.scope_constraints");
  if (!Array.isArray(envelope.clusters) || envelope.clusters.length > 50) {
    throw invalidResponse("$.clusters must be a bounded array");
  }
  const activeSnapshots = new Set(activeSnapshotIds);
  if (!Array.isArray(envelope.preview) || envelope.preview.length > 30) {
    throw invalidResponse("$.preview must be a bounded array");
  }
  const preview = envelope.preview.map((item, index) => (
    parsePreviewRef(item, `$.preview[${index}]`, activeSnapshots)
  ));
  const previewKeys = preview.map((item) => `${item.snapshot_id}\u0000${item.claim_id}`);
  if (new Set(previewKeys).size !== previewKeys.length) throw invalidResponse("$.preview must be unique");
  if (stage === "preview" && envelope.clusters.length) throw invalidResponse("preview responses cannot contain final clusters");
  if (stage === "ai_rerank" && preview.length) throw invalidResponse("AI rerank responses cannot contain preview refs");
  const clusterIds = new Set<string>();
  const clusters = envelope.clusters.map((item, index) => {
    const path = `$.clusters[${index}]`;
    const cluster = objectValue(item, path);
    exactKeys(cluster, ["cluster_id", "relation", "representative", "member_refs", "distinct_paper_families"], path);
    const clusterId = idValue(cluster.cluster_id, `${path}.cluster_id`);
    if (clusterIds.has(clusterId)) throw invalidResponse(`${path}.cluster_id must be unique`);
    clusterIds.add(clusterId);
    if (!Array.isArray(cluster.member_refs) || cluster.member_refs.length < 1 || cluster.member_refs.length > 20) {
      throw invalidResponse(`${path}.member_refs must be a bounded array`);
    }
    const representative = parseMemberRef(cluster.representative, `${path}.representative`, activeSnapshots);
    const memberRefs = cluster.member_refs.map((member, memberIndex) => (
      parseMemberRef(member, `${path}.member_refs[${memberIndex}]`, activeSnapshots)
    ));
    const uniqueRefs = new Set(memberRefs.map((member) => `${member.snapshot_id}\u0000${member.claim_id}`));
    if (uniqueRefs.size !== memberRefs.length) throw invalidResponse(`${path}.member_refs must be unique`);
    const representativeKey = `${representative.snapshot_id}\u0000${representative.claim_id}`;
    if (!uniqueRefs.has(representativeKey)) {
      throw invalidResponse(`${path}.representative must be present in member_refs`);
    }
    if (JSON.stringify(representative) !== JSON.stringify(memberRefs[0])) {
      throw invalidResponse(`${path}.member_refs[0] must exactly equal the representative`);
    }
    const distinctPaperFamilies = integerValue(
      cluster.distinct_paper_families,
      `${path}.distinct_paper_families`,
    );
    const observedFamilies = new Set([representative, ...memberRefs].map((member) => member.paper_family_id)).size;
    if (distinctPaperFamilies < observedFamilies) {
      throw invalidResponse(`${path}.distinct_paper_families is smaller than its hydrated member references`);
    }
    const relation = enumValue(cluster.relation, RELATIONS, `${path}.relation`);
    if (relation !== representative.relation) {
      throw invalidResponse(`${path}.relation does not match its representative`);
    }
    if (memberRefs.some((member) => member.relation !== relation)) {
      throw invalidResponse(`${path}.member_refs must use the cluster relation`);
    }
    return {
      cluster_id: clusterId,
      relation,
      representative,
      member_refs: memberRefs,
      distinct_paper_families: distinctPaperFamilies,
    };
  });
  const visibleCount = stage === "preview" ? preview.length : clusters.length;
  const degraded = Object.values(parsedChannels).some((channel) => channel.status === "degraded");
  if (degraded !== (resultStatus === "degraded")) {
    throw invalidResponse("$.result_status must expose degraded retrieval channels");
  }
  if (resultStatus === "matches" && visibleCount === 0) {
    throw invalidResponse("$.result_status matches requires a visible result");
  }
  if (resultStatus === "no_direct_answer" && visibleCount > 0) {
    throw invalidResponse("$.result_status no_direct_answer cannot contain visible results");
  }
  return {
    schema_version: schemaVersion,
    stage,
    result_status: resultStatus,
    query_plan: {
      query_type: enumValue(queryPlan.query_type, [
        "paper_lookup", "topic_search", "claim_search", "supporting_evidence", "contradiction_search",
      ] as const, "$.query_plan.query_type"),
      original_query: contextProtocol ? contextResponseText(queryPlan.original_query, "$.query_plan.original_query")
        : textValue(queryPlan.original_query, "$.query_plan.original_query", MAX_QUERY_LENGTH),
      ...(Object.hasOwn(queryPlan, "resolved_query") ? {
        resolved_query: clarificationText(queryPlan.resolved_query, "$.query_plan.resolved_query", MAX_QUERY_LENGTH, true),
      } : {}),
      canonical_claim: textValue(queryPlan.canonical_claim, "$.query_plan.canonical_claim", MAX_QUERY_LENGTH),
      must_terms: stringList(queryPlan.must_terms, "$.query_plan.must_terms", 64, 1_000),
      optional_terms: stringList(queryPlan.optional_terms, "$.query_plan.optional_terms", 64, 1_000),
      exclude_terms: stringList(queryPlan.exclude_terms, "$.query_plan.exclude_terms", 64, 1_000),
      entities: stringList(queryPlan.entities, "$.query_plan.entities", 64, 1_000),
      polarity: queryPlan.polarity === null
        ? null
        : enumValue(queryPlan.polarity, POLARITIES, "$.query_plan.polarity"),
      claim_types: (() => {
        const values = stringList(queryPlan.claim_types, "$.query_plan.claim_types", 7, 128);
        return values.map((item, index) => enumValue(item, CLAIM_TYPES, `$.query_plan.claim_types[${index}]`));
      })(),
      scope_constraints: scopeConstraints,
      planner: enumValue(queryPlan.planner, ["gpt", "deterministic"] as const, "$.query_plan.planner"),
    },
    ...(schemaVersion !== EVIDENCE_RETRIEVAL_INDEX_VERSION && stage === "preview"
      ? { query_plan_handoff: envelope.query_plan_handoff as string }
      : {}),
    channels: parsedChannels,
    preview,
    clusters,
    warnings: warningsValue(envelope.warnings),
    retrieval_confidence: retrievalConfidence,
  };
}

function parseClaimRelationBatchProgress(
  value: unknown,
  activeSnapshotIds: readonly string[],
  expectedCursor?: number,
): ClaimRelationBatchProgress {
  const envelope = objectValue(value, "$");
  exactKeys(envelope, [
    "schema_version", "batch_id", "status", "reprocess", "total", "completed", "succeeded", "failed", "items",
    ...(expectedCursor === undefined ? [] : ["item_offset", "next_cursor", "retired"]),
  ], "$");
  const total = integerValue(envelope.total, "$.total", 4_096);
  const completed = integerValue(envelope.completed, "$.completed", total);
  const succeeded = integerValue(envelope.succeeded, "$.succeeded", completed);
  const failed = integerValue(envelope.failed, "$.failed", completed);
  const offset = expectedCursor === undefined ? 0 : integerValue(envelope.item_offset, "$.item_offset", completed);
  const next = expectedCursor === undefined ? completed : integerValue(envelope.next_cursor, "$.next_cursor", completed);
  const retired = expectedCursor !== undefined && envelope.retired === true;
  if (total < 1 || completed !== succeeded + failed || !Array.isArray(envelope.items)
    || (expectedCursor !== undefined && (offset !== expectedCursor || typeof envelope.retired !== "boolean" || next < offset))
    || (retired ? envelope.items.length !== 0 || next !== completed : envelope.items.length !== next - offset)
    || (expectedCursor !== undefined && envelope.items.length > 16)) {
    throw invalidResponse("Claim relation batch progress is inconsistent");
  }
  const status = enumValue(envelope.status, ["running", "completed", "cancelled"] as const, "$.status");
  if ((status === "running") === (completed === total) || typeof envelope.reprocess !== "boolean") {
    throw invalidResponse("Claim relation batch status is inconsistent");
  }
  // Cancelled batches may refer to a deleted corpus. Expose only their terminal
  // counters, never stale result payloads that a caller could persist.
  if (retired && status !== "cancelled") throw invalidResponse("Only cancelled batches may be retired");
  const cursorFields = expectedCursor === undefined ? {} : { item_offset: offset, next_cursor: next, retired };
  if (retired || (status === "cancelled" && expectedCursor === undefined)) {
    return {
      ...cursorFields,
      schema_version: schemaValue(envelope.schema_version),
      batch_id: idValue(envelope.batch_id, "$.batch_id"),
      status, reprocess: envelope.reprocess, total, completed, succeeded, failed,
      items: [],
    };
  }
  const active = new Set(activeSnapshotIds);
  const seen = new Set<string>();
  const items = envelope.items.map((raw, index) => {
    const path = `$.items[${index}]`;
    const item = objectValue(raw, path);
    exactKeys(item, ["query_claim", "status", "response", "error_code"], path);
    const query = objectValue(item.query_claim, `${path}.query_claim`);
    exactKeys(query, ["snapshot_id", "claim_id"], `${path}.query_claim`);
    const snapshotId = idValue(query.snapshot_id, `${path}.query_claim.snapshot_id`);
    const claimId = idValue(query.claim_id, `${path}.query_claim.claim_id`);
    if (!active.has(snapshotId)) throw invalidResponse(`${path}.query_claim is outside the active corpus`);
    const key = `${snapshotId}\u0000${claimId}`;
    if (seen.has(key)) throw invalidResponse("Claim relation batch items must be unique");
    seen.add(key);
    const itemStatus = enumValue(item.status, ["succeeded", "failed"] as const, `${path}.status`);
    const response = item.response === null
      ? null
      : parseRetrievalSearchResponse(item.response, activeSnapshotIds);
    const errorCode = item.error_code === null
      ? null
      : textValue(item.error_code, `${path}.error_code`, 1_000);
    if ((itemStatus === "succeeded" && (response === null || response.stage !== "ai_rerank" || errorCode !== null))
      || (itemStatus === "failed" && (response !== null || errorCode === null))) {
      throw invalidResponse(`${path} has inconsistent result fields`);
    }
    return {
      query_claim: { snapshot_id: snapshotId, claim_id: claimId },
      status: itemStatus,
      response,
      error_code: errorCode,
    };
  });
  return {
    schema_version: schemaValue(envelope.schema_version),
    batch_id: idValue(envelope.batch_id, "$.batch_id"),
    status,
    reprocess: envelope.reprocess,
    total,
    completed,
    succeeded,
    failed,
    items,
    ...cursorFields,
  };
}

function safeBaseUrl(value: string) {
  let url: URL;
  try { url = new URL(value); } catch {
    throw new ApiError(503, "RETRIEVAL_NOT_CONFIGURED", "Retrieval service base URL is invalid");
  }
  const local = url.hostname === "127.0.0.1" || url.hostname === "localhost" || url.hostname === "[::1]";
  if (url.protocol !== "https:" && !(local && url.protocol === "http:")) {
    throw new ApiError(503, "RETRIEVAL_NOT_CONFIGURED", "Retrieval service must use HTTPS except on localhost");
  }
  if (url.username || url.password || url.search || url.hash) {
    throw new ApiError(503, "RETRIEVAL_NOT_CONFIGURED", "Retrieval base URL must not contain credentials, query, or fragment data");
  }
  url.pathname = `${url.pathname.replace(/\/$/, "")}/`;
  return url;
}

function boundedJson(response: Response) {
  return readBoundedJson(response, MAX_RESPONSE_BYTES, (kind, message) => new ApiError(
    502, kind === "too_large" ? "RETRIEVAL_RESPONSE_TOO_LARGE" : "INVALID_RETRIEVAL_RESPONSE", message,
  ));
}

const PLANNING_FAILURES: Readonly<Record<string, { status: number; message: string }>> = {
  RETRIEVAL_PLANNING_OUTPUT_INVALID: { status: 502, message: "Retrieval planning output did not pass validation" },
  RETRIEVAL_PLANNING_TIMEOUT: { status: 504, message: "Retrieval planning timed out" },
  RETRIEVAL_PLANNING_UPSTREAM_FAILED: { status: 502, message: "Retrieval planning provider request failed" },
  RETRIEVAL_PLANNING_UNAVAILABLE: { status: 503, message: "Retrieval planning is unavailable" },
  RETRIEVAL_PLANNING_PROVIDER_INVALID: { status: 502, message: "Retrieval planning provider returned an invalid response" },
  RETRIEVAL_PLANNING_INPUT_TOO_LARGE: { status: 413, message: "Retrieval planning input exceeds the transport limit" },
  RETRIEVAL_PLANNING_BUDGET_EXCEEDED: { status: 503, message: "Retrieval planning exceeded its operation budget" },
  RETRIEVAL_PLANNING_CAPABILITY_UNAVAILABLE: { status: 422, message: "The supplied source cannot support the requested operation" },
  RETRIEVAL_PLANNING_INTERNAL_ERROR: { status: 500, message: "Retrieval planning encountered an internal error" },
};

function upstreamFailure(payload: unknown, status: number) {
  const value = payload && typeof payload === "object" && !Array.isArray(payload)
    ? payload as Record<string, unknown>
    : {};
  const official = value.error && typeof value.error === "object" && !Array.isArray(value.error)
    ? value.error as Record<string, unknown>
    : null;
  const detail = official ?? (
    value.detail && typeof value.detail === "object" && !Array.isArray(value.detail)
      ? value.detail as Record<string, unknown>
      : value
  );
  const rawCode = typeof detail.code === "string" ? detail.code.trim() : "REQUEST_FAILED";
  const code = UPSTREAM_ERROR_CODE_RE.test(rawCode) ? rawCode : "REQUEST_FAILED";
  if (Object.hasOwn(PLANNING_FAILURES, code) && (status >= 500 || status === PLANNING_FAILURES[code].status)) {
    const failure = PLANNING_FAILURES[code];
    // These classifications are public, but upstream prose and diagnostics are not.
    // A planning failure never authorizes an automatic repeat of its model call.
    return new ApiError(failure.status, code, failure.message, { retryable: false });
  }
  if (status === 409 && code === "CLARIFICATION_REPREVIEW_REQUIRED") {
    return new ApiError(
      409,
      "CLARIFICATION_REPREVIEW_REQUIRED",
      "The clarification is no longer valid; run preview again",
      { retryable: false },
    );
  }
  if (status === 409 && code === "CONTEXT_REPREVIEW_REQUIRED") {
    return new ApiError(409, "CONTEXT_REPREVIEW_REQUIRED", "The context is no longer valid; run preview again", { retryable: false });
  }
  if (status === 409 && code === "QUERY_PLAN_HANDOFF_REPREVIEW_REQUIRED") {
    return new ApiError(
      409,
      "QUERY_PLAN_HANDOFF_REPREVIEW_REQUIRED",
      "The retrieval preview is no longer valid; run preview again",
      { retryable: false },
    );
  }
  const retryable = typeof detail.retryable === "boolean"
    ? detail.retryable
    : status === 408 || status === 429 || status >= 500;
  const rawMessage = typeof detail.message === "string" ? detail.message : `Retrieval service returned HTTP ${status}`;
  const message = rawMessage.replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim().slice(0, 1_000)
    || `Retrieval service returned HTTP ${status}`;
  return new ApiError(status >= 400 && status < 500 ? status : retryable ? 503 : 502, `UPSTREAM_RETRIEVAL_${code}`, message, { retryable, upstreamStatus: status });
}

function encodeRequest(value: unknown) {
  let encoded: string;
  try { encoded = JSON.stringify(value); } catch {
    throw new ApiError(500, "INVALID_RETRIEVAL_REQUEST", "Retrieval request could not be serialized");
  }
  if (new TextEncoder().encode(encoded).byteLength > MAX_RESPONSE_BYTES) {
    throw new ApiError(413, "RETRIEVAL_REQUEST_TOO_LARGE", "Retrieval request exceeded 8 MiB");
  }
  return encoded;
}

// Inspect transport errors locally, but never log their prose: native error
// messages may contain request URLs, credentials, or submitted content.
function transportFailureDiagnostics(error: unknown) {
  const nativeCodes = new Set([
    "ECONNRESET", "ECONNREFUSED", "EPIPE", "ENOTFOUND", "EAI_AGAIN",
    "ETIMEDOUT", "ENETUNREACH", "EHOSTUNREACH", "UND_ERR_SOCKET",
    "UND_ERR_CONNECT_TIMEOUT", "UND_ERR_HEADERS_TIMEOUT", "UND_ERR_BODY_TIMEOUT",
  ]);
  let current = error;
  let nativeCode: string | null = null;
  let fingerprint = "";
  for (let depth = 0; depth < 4 && current && typeof current === "object"; depth++) {
    const item = current as Record<string, unknown>;
    if (typeof item.code === "string" && nativeCodes.has(item.code)) nativeCode ??= item.code;
    if (typeof item.message === "string") fingerprint += ` ${item.message.slice(0, 2_048).toLowerCase()}`;
    current = item.cause;
  }
  const classification = nativeCode === "ECONNREFUSED" || fingerprint.includes("connection refused")
    ? "connection_refused"
    : nativeCode === "ECONNRESET" || nativeCode === "EPIPE" || fingerprint.includes("connection reset")
      ? "connection_reset"
      : /too many subrequests|subrequest limit|exceeded.*(?:cpu|memory|resource).*limit/.test(fingerprint)
        ? "runtime_limit"
        : fingerprint.includes("network connection lost")
          ? "connection_lost"
          : nativeCode !== null || /network|fetch failed|socket|disconnected/.test(fingerprint)
          ? "network"
          : "unknown";
  return { classification, nativeCode };
}

function requestId(value: string, path: string) {
  if (!RETRIEVAL_ID_RE.test(value)) {
    throw new ApiError(500, "INVALID_RETRIEVAL_REQUEST", `${path} must be a valid retrieval identifier`);
  }
  return value;
}

function uniqueIds(values: readonly string[], path: string, maximum: number) {
  if (!values.length || values.length > maximum) {
    throw new ApiError(500, "INVALID_RETRIEVAL_REQUEST", `${path} must contain 1-${maximum} values`);
  }
  const ids = values.map((value, index) => requestId(value, `${path}[${index}]`));
  if (new Set(ids).size !== ids.length) {
    throw new ApiError(500, "INVALID_RETRIEVAL_REQUEST", `${path} must not contain duplicates`);
  }
  return ids;
}

function batchProgressPath(path: string, after?: number) {
  if (after === undefined) return path;
  if (!Number.isInteger(after) || after < 0 || after > 4096) throw new ApiError(400, "INVALID_RETRIEVAL_REQUEST", "Batch cursor is invalid");
  return `${path}?after=${after}`;
}

// This is the gateway's already-resolved authorization set, not a wire list.
// Keep every ID for validating returned references without imposing transport caps.
function resolvedCorpusIds(values: readonly string[]) {
  if (!values.length) throw new ApiError(500, "INVALID_RETRIEVAL_REQUEST", "Resolved corpus must not be empty");
  const ids = values.map((value, index) => requestId(value, `resolvedSnapshotIds[${index}]`));
  if (new Set(ids).size !== ids.length) throw new ApiError(500, "INVALID_RETRIEVAL_REQUEST", "Resolved corpus snapshots must be unique");
  return ids;
}

export class RetrievalClient {
  private readonly baseUrl: URL;
  private readonly request: typeof fetch;
  private readonly sharedSecret: string;
  private readonly interactive: boolean;

  constructor(config: RetrievalClientConfig) {
    this.baseUrl = safeBaseUrl(config.baseUrl);
    if (!SHARED_SECRET_RE.test(config.sharedSecret)) {
      throw new ApiError(503, "RETRIEVAL_NOT_CONFIGURED", "Retrieval shared credential has an invalid format");
    }
    this.sharedSecret = config.sharedSecret;
    this.interactive = config.interactive === true;
    this.request = config.fetch ?? fetch;
  }

  private url(path: string) {
    return new URL(path.replace(/^\//, ""), this.baseUrl);
  }

  private async invoke(
    path: string,
    value: unknown,
    timeoutMs: number | null,
    callerSignal?: AbortSignal,
    method: "POST" | "GET" | "DELETE" = "POST",
  ) {
    let response: Response;
    // Validation/serialization failures are local errors, not transport failures.
    const body = method === "POST" ? encodeRequest(value) : undefined;
    const timeoutSignal = timeoutMs === null ? undefined : AbortSignal.timeout(timeoutMs);
    const signal = callerSignal && timeoutSignal ? AbortSignal.any([callerSignal, timeoutSignal]) : callerSignal ?? timeoutSignal;
    // Projection parsing/encoding can hold Workerd's event loop beyond the
    // loopback server's idle timeout. Process pending socket-close events before
    // sending the one authorized request; never replay a failed POST.
    if (this.baseUrl.protocol === "http:") {
      await new Promise<void>((resolve) => setTimeout(resolve, 0));
    }
    const startedAt = Date.now();
    try {
      response = await this.request(this.url(path), {
        method,
        headers: {
          authorization: `Bearer ${this.sharedSecret}`,
          "content-type": "application/json",
          ...(this.interactive && path === "v1/retrieval/search" ? { "x-deepshovel-interactive": "1" } : {}),
        },
        ...(body !== undefined ? { body } : {}),
        redirect: "manual",
        referrerPolicy: "no-referrer",
        signal,
      });
    } catch (error) {
      if (callerSignal?.aborted) {
        throw new ApiError(408, "RETRIEVAL_REQUEST_CANCELLED", "Retrieval request was cancelled", { retryable: false });
      }
      if (timeoutSignal?.aborted) {
        throw new ApiError(503, "RETRIEVAL_TIMEOUT", "Retrieval service exceeded its operation timeout", { retryable: true });
      }
      console.warn("DeepShovel retrieval transport failure", {
        operation: path.split("?", 1)[0].split("/").slice(0, 3).join("/"),
        method,
        elapsedMs: Math.max(0, Date.now() - startedAt),
        ...transportFailureDiagnostics(error),
      });
      throw new ApiError(503, "RETRIEVAL_UNAVAILABLE", "Retrieval service is unavailable", { retryable: true });
    }
    if (response.status >= 300 && response.status < 400) {
      try { await response.body?.cancel(); } catch { /* already closed */ }
      throw new ApiError(502, "RETRIEVAL_REDIRECT_REJECTED", "Retrieval service redirects are not allowed", { retryable: false });
    }
    let payload: unknown;
    try {
      payload = await boundedJson(response);
    } catch (error) {
      if (callerSignal?.aborted) {
        throw new ApiError(408, "RETRIEVAL_REQUEST_CANCELLED", "Retrieval request was cancelled", { retryable: false });
      }
      if (timeoutSignal?.aborted) {
        throw new ApiError(503, "RETRIEVAL_TIMEOUT", "Retrieval service exceeded its operation timeout", { retryable: true });
      }
      if (response.ok) throw error;
      try { await response.body?.cancel(); } catch { /* already consumed or locked */ }
      throw upstreamFailure({}, response.status);
    }
    if (containsCredential(payload, this.sharedSecret)) {
      throw new ApiError(502, "RETRIEVAL_RESPONSE_REJECTED", "Retrieval response contained forbidden credential material");
    }
    if (!response.ok) throw upstreamFailure(payload, response.status);
    return payload;
  }

  async checkManifests(
    projectId: string,
    manifests: readonly EvidenceRetrievalManifestDto[],
    signal?: AbortSignal,
  ) {
    requestId(projectId, "$.project_id");
    if (!manifests.length || manifests.length > RETRIEVAL_MAX_MANIFESTS) {
      throw new ApiError(500, "INVALID_RETRIEVAL_REQUEST", `Manifest checks require 1-${RETRIEVAL_MAX_MANIFESTS} manifests`);
    }
    const snapshotIds = uniqueIds(manifests.map((manifest) => manifest.snapshot_id), "$.manifests[].snapshot_id", RETRIEVAL_MAX_MANIFESTS);
    const payload = await this.invoke("v1/retrieval/manifests:check", {
      schema_version: EVIDENCE_RETRIEVAL_INDEX_VERSION,
      project_id: projectId,
      manifests,
    }, this.interactive ? null : 15_000, signal);
    return parseManifestEnvelope(payload, ["indexed", "missing", "stale", "degraded"], snapshotIds);
  }

  async indexSnapshot(
    projectId: string,
    manifest: EvidenceRetrievalManifestDto,
    documents: readonly EvidenceRetrievalDocumentDto[],
    signal?: AbortSignal,
  ): Promise<RetrievalIndexResponse> {
    requestId(projectId, "$.project_id");
    requestId(manifest.snapshot_id, "$.manifest.snapshot_id");
    if (documents.length > RETRIEVAL_MAX_INDEX_DOCUMENTS) {
      throw new ApiError(500, "INVALID_RETRIEVAL_REQUEST", "Retrieval snapshot batch exceeds the document boundary");
    }
    const payload = await this.invoke("v1/retrieval/index", {
      schema_version: EVIDENCE_RETRIEVAL_INDEX_VERSION,
      project_id: projectId,
      manifest,
      documents,
    }, this.interactive ? null : 180_000, signal);
    const envelope = objectValue(payload, "$");
    exactKeys(envelope, ["schema_version", "manifests", "indexed_documents", "warnings"], "$");
    const base = parseManifestEnvelope(
      { schema_version: envelope.schema_version, manifests: envelope.manifests },
      ["indexed", "unchanged", "degraded"],
      [manifest.snapshot_id],
    );
    return {
      ...base,
      indexed_documents: integerValue(envelope.indexed_documents, "$.indexed_documents", RETRIEVAL_MAX_INDEX_DOCUMENTS),
      warnings: warningsValue(envelope.warnings),
    };
  }

  async search(
    request: EvidenceRetrievalSearchRequestDto,
    allowedSnapshotIds: readonly string[],
    signal?: AbortSignal,
  ) {
    requestId(request.project_id, "$.project_id");
    if (request.schema_version !== EVIDENCE_RETRIEVAL_INDEX_VERSION
      && request.schema_version !== EVIDENCE_RETRIEVAL_SEARCH_PROTOCOL_VERSION
      && request.schema_version !== EVIDENCE_RETRIEVAL_CONTEXT_PROTOCOL_VERSION) {
      throw new ApiError(500, "INVALID_RETRIEVAL_REQUEST", "Retrieval search request uses an unsupported version");
    }
    const contextProtocol = request.schema_version === EVIDENCE_RETRIEVAL_CONTEXT_PROTOCOL_VERSION;
    try {
      if (Object.hasOwn(request, "conversation_history")) {
        if (!contextProtocol) throw new Error();
        parseConversationHistory(request.conversation_history);
      }
      if (Object.hasOwn(request, "context_submission")) {
        const context = request.context_submission;
        if (!contextProtocol || Object.hasOwn(request, "clarification_selection") || !context
          || Object.keys(context).sort().join(",") !== "content,context_token"
          || !isValidContextToken(context.context_token)) throw new Error();
        parseRetrievalContextText(context.content, "$.context_submission.content");
      }
      if (contextProtocol) parseRetrievalContextText(request.query, "$.query");
      if (Object.hasOwn(request.filters, "openalex_scope")) {
        const scope = request.filters.openalex_scope;
        if (!contextProtocol || !scope || typeof scope !== "object" || Array.isArray(scope)
          || Object.keys(scope).sort().join(",") !== "id,level"
          || !["domain", "field", "subfield", "topic"].includes(scope.level)
          || typeof scope.id !== "string" || scope.id.length > 100
          || !(scope.level === "topic" ? /^https:\/\/openalex\.org\/T[0-9]+$/u
            : new RegExp(`^https://openalex\\.org/${scope.level}s/[0-9]+$`, "u")).test(scope.id)) throw new Error();
      }
    } catch {
      throw new ApiError(500, "INVALID_RETRIEVAL_REQUEST", "Retrieval conversation context is invalid");
    }
    const hasHandoff = Object.hasOwn(request, "query_plan_handoff");
    if (this.interactive && request.schema_version === EVIDENCE_RETRIEVAL_INDEX_VERSION
      && request.stage === "ai_rerank") {
      // An interactive final must rank the sealed preview under its own model
      // plan; the handoff-free legacy final would re-plan locally (RTS-1).
      throw new ApiError(500, "INVALID_RETRIEVAL_REQUEST", "Interactive final requests require a preview handoff");
    }
    if (request.schema_version === EVIDENCE_RETRIEVAL_INDEX_VERSION && hasHandoff) {
      throw new ApiError(500, "INVALID_RETRIEVAL_REQUEST", "Legacy retrieval search requests must not carry a QueryPlan handoff");
    }
    if (request.schema_version !== EVIDENCE_RETRIEVAL_INDEX_VERSION
      && ((request.stage === "preview" && hasHandoff)
        || (request.stage === "ai_rerank" && !isValidQueryPlanHandoff(request.query_plan_handoff)))) {
      throw new ApiError(500, "INVALID_RETRIEVAL_REQUEST", "Retrieval search request has an invalid QueryPlan handoff shape");
    }
    if (Object.hasOwn(request, "clarification_selection")) {
      const selection = request.clarification_selection;
      if (!contextProtocol
        || !selection || typeof selection !== "object" || Array.isArray(selection)
        || Object.keys(selection).sort().join(",") !== "clarification_token,option_id"
        || !isValidContextClarificationToken(selection.clarification_token)
        || typeof selection.option_id !== "string" || !/^[A-Za-z0-9_-]{1,32}$/u.test(selection.option_id)) {
        throw new ApiError(500, "INVALID_RETRIEVAL_REQUEST", "Retrieval clarification selection is invalid");
      }
    }
    if (!request.query.trim() || Array.from(request.query).length > MAX_QUERY_LENGTH) {
      throw new ApiError(400, "INVALID_EVIDENCE_SEARCH", `Search query must contain 1-${MAX_QUERY_LENGTH} characters`);
    }
    if (!Number.isInteger(request.filters.limit) || request.filters.limit < 1 || request.filters.limit > 50) {
      throw new ApiError(400, "INVALID_EVIDENCE_SEARCH", "Search limit must be between 1 and 50");
    }
    if (!allowedSnapshotIds.length) {
      throw new ApiError(500, "INVALID_RETRIEVAL_REQUEST", "Search response validation requires the resolved corpus snapshots");
    }
    const snapshots = allowedSnapshotIds.map((id, index) => requestId(id, `resolvedSnapshotIds[${index}]`));
    if (new Set(snapshots).size !== snapshots.length) {
      throw new ApiError(500, "INVALID_RETRIEVAL_REQUEST", "Resolved corpus snapshots must be unique");
    }
    const payload = await this.invoke("v1/retrieval/search", request, null, signal);
    const response = parseRetrievalSearchResponse(payload, snapshots);
    if (response.schema_version !== request.schema_version || response.stage !== request.stage) {
      throw invalidResponse("Retrieval search response does not match the requested protocol version and stage");
    }
    if (contextProtocol
      && response.result_status !== "clarification_required" && response.result_status !== "needs_context") {
      if (response.query_plan.original_query !== request.query || !response.query_plan.resolved_query) {
        throw invalidResponse("Resolved query plan does not preserve the original request or selected interpretation");
      }
    }
    return response;
  }

  async startClaimRelationBatch(
    projectId: string,
    clientRequestId: string,
    activeSnapshotIds: readonly string[],
    claimRefs: readonly { snapshotId: string; claimId: string }[],
    reprocess: boolean,
    signal?: AbortSignal,
    fullProjectCorpus = false,
    after?: number,
  ) {
    requestId(projectId, "$.project_id");
    requestId(clientRequestId, "$.client_request_id");
    const snapshots = fullProjectCorpus ? resolvedCorpusIds(activeSnapshotIds)
      : uniqueIds(activeSnapshotIds, "$.active_snapshot_ids", RETRIEVAL_MAX_MANIFESTS);
    const authorized = new Set(snapshots);
    if (!claimRefs.length || claimRefs.length > RETRIEVAL_MAX_MANIFESTS) {
      throw new ApiError(500, "INVALID_RETRIEVAL_REQUEST", "Claim relation batch requires 1-4096 Claims");
    }
    const seen = new Set<string>();
    const refs = claimRefs.map((ref, index) => {
      const snapshotId = requestId(ref.snapshotId, `$.claim_refs[${index}].snapshot_id`);
      const claimId = requestId(ref.claimId, `$.claim_refs[${index}].claim_id`);
      if (!authorized.has(snapshotId)) {
        throw new ApiError(500, "INVALID_RETRIEVAL_REQUEST", "Claim relation batch contains an inactive Claim");
      }
      const key = `${snapshotId}\u0000${claimId}`;
      if (seen.has(key)) throw new ApiError(500, "INVALID_RETRIEVAL_REQUEST", "Claim relation batch contains duplicates");
      seen.add(key);
      return { snapshot_id: snapshotId, claim_id: claimId };
    });
    const payload = await this.invoke(batchProgressPath("v1/retrieval/claim-relations:batch", after), {
      schema_version: EVIDENCE_RETRIEVAL_INDEX_VERSION,
      project_id: projectId,
      client_request_id: clientRequestId,
      active_snapshot_ids: fullProjectCorpus && snapshots.length > RETRIEVAL_MAX_MANIFESTS ? null : snapshots,
      claim_refs: refs,
      reprocess,
    }, 30_000, signal);
    return parseClaimRelationBatchProgress(payload, snapshots, after);
  }

  async getClaimRelationBatch(
    batchId: string,
    activeSnapshotIds: readonly string[],
    signal?: AbortSignal,
    after?: number,
  ) {
    const id = requestId(batchId, "batchId");
    const snapshots = resolvedCorpusIds(activeSnapshotIds);
    const payload = await this.invoke(
      batchProgressPath(`v1/retrieval/claim-relations:batch/${encodeURIComponent(id)}`, after),
      undefined,
      30_000,
      signal,
      "GET",
    );
    return parseClaimRelationBatchProgress(payload, snapshots, after);
  }

  async cancelClaimRelationBatch(
    batchId: string,
    activeSnapshotIds: readonly string[],
    signal?: AbortSignal,
    after?: number,
  ) {
    const id = requestId(batchId, "batchId");
    const snapshots = resolvedCorpusIds(activeSnapshotIds);
    const payload = await this.invoke(
      batchProgressPath(`v1/retrieval/claim-relations:batch/${encodeURIComponent(id)}`, after),
      undefined,
      30_000,
      signal,
      "DELETE",
    );
    return parseClaimRelationBatchProgress(payload, snapshots, after);
  }

  async deleteSnapshots(
    projectId: string,
    snapshotIds: readonly string[],
    signal?: AbortSignal,
  ): Promise<RetrievalDeleteResponse> {
    requestId(projectId, "$.project_id");
    const snapshots = uniqueIds(snapshotIds, "$.snapshot_ids", RETRIEVAL_MAX_MANIFESTS);
    const payload = await this.invoke("v1/retrieval/snapshots:delete", {
      schema_version: EVIDENCE_RETRIEVAL_INDEX_VERSION,
      index_version: EVIDENCE_RETRIEVAL_INDEX_VERSION,
      project_id: projectId,
      snapshot_ids: snapshots,
    }, 30_000, signal);
    const envelope = objectValue(payload, "$");
    exactKeys(envelope, ["schema_version", "deleted_snapshots", "deleted_documents", "warnings"], "$");
    return {
      schema_version: schemaValue(envelope.schema_version),
      deleted_snapshots: integerValue(envelope.deleted_snapshots, "$.deleted_snapshots", snapshots.length),
      deleted_documents: integerValue(envelope.deleted_documents, "$.deleted_documents"),
      warnings: warningsValue(envelope.warnings),
    };
  }
}
