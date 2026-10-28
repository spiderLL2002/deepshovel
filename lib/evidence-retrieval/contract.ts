import type { ClaimPolarity, ClaimType, Scope, ScopeTime } from "../evidence-graph/types.ts";
import {
  CLAIM_POLARITIES,
  CLAIM_TYPES as SHARED_CLAIM_TYPES,
  NORMALIZED_REGION_EXTENT_MAX,
  RETRIEVAL_CONTINUATION_KEY_ID,
  RETRIEVAL_MAX_SEARCH_LIMIT,
  RETRIEVAL_SESSION_ID_PATTERN,
} from "../shared-contract.generated.ts";
import {
  toEvidenceRetrievalDocumentDto,
  toEvidenceRetrievalManifestDto,
} from "./projection.ts";
import {
  EVIDENCE_RETRIEVAL_INDEX_VERSION,
  EVIDENCE_RETRIEVAL_SEARCH_PROTOCOL_VERSION,
  EVIDENCE_RETRIEVAL_CONTEXT_PROTOCOL_VERSION,
  RETRIEVAL_CONTEXT_MAX_TURNS,
  RETRIEVAL_CONTEXT_MAX_CONTENT_LENGTH,
  RETRIEVAL_CONTEXT_MAX_BYTES,
  type ConversationTurn,
  QUERY_PLAN_HANDOFF_MAX_LENGTH,
  type EvidenceRetrievalDeleteSnapshotsRequestDto,
  type EvidenceRetrievalAssessmentDto,
  type EvidenceRetrievalDocumentDto,
  type EvidenceRetrievalIndexRequestDto,
  type EvidenceRetrievalIndexBundle,
  type EvidenceRetrievalManifestCheckRequestDto,
  type EvidenceRetrievalManifestDto,
  type EvidenceRetrievalSearchRequestDto,
  type EvidenceRetrievalSnapshotBundle,
  type EvidenceSearchIntent,
  type EvidenceSearchRequest,
  type EvidenceSearchStage,
  type QueryClaimRef,
  type ResolvedEvidenceSearchCorpus,
  type SearchScope,
} from "./types.ts";

const IDENTIFIER_RE = /^[A-Za-z][A-Za-z0-9._:-]{0,127}$/u;
const SNAPSHOT_ID_RE = /^snapshot:sha256:[a-f0-9]{64}$/u;
const CLAIM_ID_RE = /^claim:sha256:[a-f0-9]{64}$/u;
const PROPOSITION_KEY_RE = /^proposition:sha256:[a-f0-9]{64}$/u;
const SHA256_RE = /^[a-f0-9]{64}$/u;
export const MAX_CORPUS_SNAPSHOTS = 131_072;
const COMPACT_CORPUS_THRESHOLD = 4_096;

/** Lossless bounded wire codec; preserves the gateway's exact filtered order. */
export function encodeActiveSnapshotIds(ids: readonly string[]): string[] | `sha256-v1:${string}` {
  if (ids.length > MAX_CORPUS_SNAPSHOTS) throw new TypeError(`Retrieval corpus exceeds ${MAX_CORPUS_SNAPSHOTS} snapshots`);
  if (new Set(ids).size !== ids.length) throw new TypeError("Retrieval corpus snapshots must be unique");
  if (ids.length <= COMPACT_CORPUS_THRESHOLD) return [...ids];
  if (ids.some((id) => !SNAPSHOT_ID_RE.test(id))) throw new TypeError("Large retrieval corpora require canonical snapshot SHA-256 identities");
  const bytes = new Uint8Array(ids.length * 32);
  ids.forEach((id, index) => {
    const hex = id.slice("snapshot:sha256:".length);
    for (let offset = 0; offset < 32; offset++) bytes[index * 32 + offset] = Number.parseInt(hex.slice(offset * 2, offset * 2 + 2), 16);
  });
  // Avoid argument-stack overflow and repeated whole-corpus string concatenation.
  const pieces: string[] = [];
  for (let offset = 0; offset < bytes.length; offset += 16_384) {
    pieces.push(String.fromCharCode(...bytes.subarray(offset, offset + 16_384)));
  }
  return `sha256-v1:${btoa(pieces.join("")).replace(/\+/gu, "-").replace(/\//gu, "_").replace(/=+$/u, "")}`;
}

const EXACT_SOURCE_TEXT_MAX = 128_000;
const CLAIM_TYPES: readonly ClaimType[] = SHARED_CLAIM_TYPES;
const POLARITIES: readonly ClaimPolarity[] = CLAIM_POLARITIES;
const INTENTS: EvidenceSearchIntent[] = ["claim_search", "supporting_evidence", "contradiction_search"];
const SEARCH_STAGES: EvidenceSearchStage[] = ["preview", "ai_rerank"];
const QUERY_PLAN_HANDOFF_KID_RE = /^[A-Za-z0-9_-]{1,32}$/u;
const QUERY_PLAN_HANDOFF_SEGMENT_RE = /^[A-Za-z0-9_-]+$/u;
const QUERY_PLAN_HANDOFF_PAYLOAD_MAX_LENGTH = 699_900;
const QUERY_PLAN_HANDOFF_SIGNATURE_LENGTH = 43;

export class EvidenceSearchContractError extends Error {
  readonly path: string;

  constructor(path: string, message: string) {
    super(`${path}: ${message}`);
    this.name = "EvidenceSearchContractError";
    this.path = path;
  }
}

function invalid(path: string, message: string): never {
  throw new EvidenceSearchContractError(path, message);
}

function objectValue(value: unknown, path: string) {
  if (!value || typeof value !== "object" || Array.isArray(value)) invalid(path, "must be an object");
  return value as Record<string, unknown>;
}

function exactKeys(value: Record<string, unknown>, allowed: readonly string[], path: string) {
  const unknown = Object.keys(value).filter((key) => !allowed.includes(key));
  if (unknown.length) invalid(path, `contains unknown fields: ${unknown.join(", ")}`);
}

function exactRequiredKeys(value: Record<string, unknown>, keys: readonly string[], path: string) {
  exactKeys(value, keys, path);
  const missing = keys.filter((key) => !Object.hasOwn(value, key));
  if (missing.length) invalid(path, `is missing required fields: ${missing.join(", ")}`);
}

function stringValue(value: unknown, path: string, maximum = 1_000) {
  if (typeof value !== "string" || !value.trim() || Array.from(value).length > maximum) {
    invalid(path, `must be a non-empty string of at most ${maximum} characters`);
  }
  if (value !== value.normalize("NFC")) invalid(path, "must use NFC normalization");
  return value.trim();
}

function identifier(value: unknown, path: string) {
  const parsed = stringValue(value, path, 128);
  if (!IDENTIFIER_RE.test(parsed)) invalid(path, "must be a canonical local identifier");
  return parsed;
}

function stringArray(value: unknown, path: string, maximumItems = 64) {
  if (!Array.isArray(value) || value.length > maximumItems) {
    invalid(path, `must be an array with at most ${maximumItems} values`);
  }
  const parsed = value.map((item, index) => stringValue(item, `${path}[${index}]`));
  const normalized = parsed.map((item) => item.normalize("NFKC").toLocaleLowerCase("und"));
  if (new Set(normalized).size !== parsed.length) invalid(path, "must not contain duplicates");
  return parsed;
}

function identifierArray(value: unknown, path: string, maximumItems = 64) {
  return stringArray(value, path, maximumItems).map((item, index) => {
    if (!IDENTIFIER_RE.test(item)) invalid(`${path}[${index}]`, "must be a canonical local identifier");
    return item;
  });
}

function optionalTextArray(value: unknown, path: string) {
  return value === undefined ? undefined : stringArray(value, path);
}

function parseTime(value: unknown, path: string): ScopeTime | null | undefined {
  if (value === undefined || value === null) return value;
  const time = objectValue(value, path);
  exactKeys(time, ["label", "start", "end", "unit"], path);
  const nullableText = (raw: unknown, member: string) => raw == null ? null : stringValue(raw, `${path}.${member}`);
  const nullableNumber = (raw: unknown, member: string) => {
    if (raw == null) return null;
    if (typeof raw !== "number" || !Number.isFinite(raw)) invalid(`${path}.${member}`, "must be a finite number or null");
    return raw;
  };
  const parsed: ScopeTime = {
    label: nullableText(time.label, "label"),
    start: nullableNumber(time.start, "start"),
    end: nullableNumber(time.end, "end"),
    unit: nullableText(time.unit, "unit"),
  };
  if (parsed.label === null && parsed.start === null && parsed.end === null) invalid(path, "must contain a label or numeric bound");
  if ((parsed.start !== null || parsed.end !== null) && parsed.unit === null) invalid(`${path}.unit`, "is required for numeric bounds");
  if (parsed.start !== null && parsed.end !== null && parsed.start > parsed.end) invalid(path, "start must not exceed end");
  return parsed;
}

function parseScope(value: unknown): SearchScope | null {
  if (value === undefined || value === null) return null;
  const scope = objectValue(value, "$.scope");
  const keys: Array<keyof Scope> = [
    "location", "time", "formation", "sample_type", "method", "condition", "measurement", "uncertainty", "entities",
  ];
  exactKeys(scope, keys, "$.scope");
  const parsed: SearchScope = {
    location: optionalTextArray(scope.location, "$.scope.location"),
    time: parseTime(scope.time, "$.scope.time"),
    formation: optionalTextArray(scope.formation, "$.scope.formation"),
    sample_type: optionalTextArray(scope.sample_type, "$.scope.sample_type"),
    method: optionalTextArray(scope.method, "$.scope.method"),
    condition: optionalTextArray(scope.condition, "$.scope.condition"),
    measurement: optionalTextArray(scope.measurement, "$.scope.measurement"),
    uncertainty: scope.uncertainty === undefined
      ? undefined
      : scope.uncertainty === null ? null : stringValue(scope.uncertainty, "$.scope.uncertainty"),
    entities: optionalTextArray(scope.entities, "$.scope.entities"),
  };
  const meaningful = parsed.time != null
    || parsed.uncertainty != null
    || [
      parsed.location,
      parsed.formation,
      parsed.sample_type,
      parsed.method,
      parsed.condition,
      parsed.measurement,
      parsed.entities,
    ].some((items) => items !== undefined && items.length > 0);
  if (!meaningful) invalid("$.scope", "must contain at least one non-empty constraint");
  return parsed;
}

function parseClaimRef(value: unknown): QueryClaimRef | null {
  if (value === undefined || value === null) return null;
  const claim = objectValue(value, "$.query_claim");
  exactKeys(claim, ["snapshot_id", "claim_id"], "$.query_claim");
  const snapshotId = stringValue(claim.snapshot_id, "$.query_claim.snapshot_id", 128);
  if (!SNAPSHOT_ID_RE.test(snapshotId)) {
    invalid("$.query_claim.snapshot_id", "must be a canonical snapshot SHA-256 ID");
  }
  return { snapshotId, claimId: identifier(claim.claim_id, "$.query_claim.claim_id") };
}

function parseCandidateRefs(value: unknown): QueryClaimRef[] {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.length > 30) {
    invalid("$.candidate_refs", "must be an array with at most 30 Claim references");
  }
  const refs = value.map((item, index) => {
    const path = `$.candidate_refs[${index}]`;
    const ref = objectValue(item, path);
    exactKeys(ref, ["snapshot_id", "claim_id"], path);
    const snapshotId = stringValue(ref.snapshot_id, `${path}.snapshot_id`, 128);
    const claimId = stringValue(ref.claim_id, `${path}.claim_id`, 128);
    if (!SNAPSHOT_ID_RE.test(snapshotId)) invalid(`${path}.snapshot_id`, "must be a canonical snapshot SHA-256 ID");
    if (!CLAIM_ID_RE.test(claimId)) invalid(`${path}.claim_id`, "must be a canonical Claim SHA-256 ID");
    return { snapshotId, claimId };
  });
  const keys = refs.map((ref) => `${ref.snapshotId}\u0000${ref.claimId}`);
  if (new Set(keys).size !== keys.length) invalid("$.candidate_refs", "must not contain duplicates");
  return refs;
}

function enumArray<T extends string>(
  value: unknown,
  path: string,
  allowed: readonly T[],
  maximumItems = allowed.length,
) {
  const parsed = stringArray(value, path, maximumItems);
  return parsed.map((item, index) => {
    if (!allowed.includes(item as T)) invalid(`${path}[${index}]`, `must be one of ${allowed.join(", ")}`);
    return item as T;
  });
}

const RETRIEVAL_SESSION_ID_RE = new RegExp(RETRIEVAL_SESSION_ID_PATTERN, "u");

/**
 * The opaque browser handle for one server-side continuation token. Browser
 * requests carry these in place of Compiler-signed tokens.
 */
export function isRetrievalSessionId(value: unknown): value is string {
  return typeof value === "string" && RETRIEVAL_SESSION_ID_RE.test(value);
}

/** Cheap transport-shape check only; the Compiler verifies the HMAC and bound request fields. */
export function isValidQueryPlanHandoff(value: unknown): value is string {
  if (typeof value !== "string" || value.length > QUERY_PLAN_HANDOFF_MAX_LENGTH || !value.startsWith("v1.")) {
    return false;
  }
  const kidEnd = value.indexOf(".", 3);
  const payloadEnd = kidEnd < 0 ? -1 : value.indexOf(".", kidEnd + 1);
  if (kidEnd < 0 || payloadEnd < 0 || value.indexOf(".", payloadEnd + 1) !== -1) return false;
  const kid = value.slice(3, kidEnd);
  const payload = value.slice(kidEnd + 1, payloadEnd);
  const signature = value.slice(payloadEnd + 1);
  return QUERY_PLAN_HANDOFF_KID_RE.test(kid)
    && payload.length >= 1
    && payload.length <= QUERY_PLAN_HANDOFF_PAYLOAD_MAX_LENGTH
    && QUERY_PLAN_HANDOFF_SEGMENT_RE.test(payload)
    && signature.length === QUERY_PLAN_HANDOFF_SIGNATURE_LENGTH
    && QUERY_PLAN_HANDOFF_SEGMENT_RE.test(signature);
}

/** One continuation capability: a Compiler token, or at the browser boundary a session ID. */
function continuationToken(
  value: unknown,
  sessions: boolean,
  isCompilerToken: (value: unknown) => value is string,
  path: string,
  compilerTokenMessage: string,
): string {
  if (sessions) {
    if (!isRetrievalSessionId(value)) invalid(path, "must be a retrieval session ID");
    return value;
  }
  if (!isCompilerToken(value)) invalid(path, compilerTokenMessage);
  return value;
}

function parseQueryPlanHandoff(value: unknown, sessions: boolean) {
  if (value === undefined || value === null) return null;
  return continuationToken(value, sessions, isValidQueryPlanHandoff, "$.query_plan_handoff",
    `must be a v1 signed handoff token of at most ${QUERY_PLAN_HANDOFF_MAX_LENGTH} ASCII characters`);
}

/** Shape validation only; the Compiler authenticates the selection capability. */
export function isValidContextClarificationToken(value: unknown): value is string {
  return typeof value === "string" && value.startsWith(`c2.${RETRIEVAL_CONTINUATION_KEY_ID}.`)
    && isValidQueryPlanHandoff(`v1.${value.slice(3)}`);
}

export function isValidContextToken(value: unknown): value is string {
  return typeof value === "string" && value.startsWith(`n1.${RETRIEVAL_CONTINUATION_KEY_ID}.`)
    && isValidQueryPlanHandoff(`v1.${value.slice(3)}`);
}

/** Preserve signed user context exactly; unpaired UTF-16 surrogates are not Unicode text. */
export function parseRetrievalContextText(value: unknown, path: string): string {
  if (typeof value !== "string"
    || !/[^\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]/u.test(value)
    || Array.from(value).length > RETRIEVAL_CONTEXT_MAX_CONTENT_LENGTH
    || /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value)
    || value !== value.normalize("NFC")) {
    invalid(path, "must be nonblank NFC Unicode text of at most 8000 code points");
  }
  return value;
}

export function parseConversationHistory(value: unknown): ConversationTurn[] {
  if (!Array.isArray(value) || value.length > RETRIEVAL_CONTEXT_MAX_TURNS) {
    invalid("$.conversation_history", "must contain at most 16 turns");
  }
  const history = value.map((raw, index): ConversationTurn => {
    const path = `$.conversation_history[${index}]`;
    const turn = objectValue(raw, path);
    exactRequiredKeys(turn, ["role", "content"], path);
    if (turn.role !== "user" && turn.role !== "assistant") invalid(`${path}.role`, "must be user or assistant");
    return { role: turn.role, content: parseRetrievalContextText(turn.content, `${path}.content`) };
  });
  if (history.reduce((bytes, turn) => bytes + new TextEncoder().encode(turn.content).byteLength, 0) > RETRIEVAL_CONTEXT_MAX_BYTES) {
    invalid("$.conversation_history", "UTF-8 content must not exceed 32768 bytes");
  }
  return history;
}

export type EvidenceSearchParseOptions = {
  /**
   * `compiler_tokens` (default) accepts Compiler-signed continuation tokens,
   * for server-side callers. `session_ids` is the browser boundary: each
   * continuation is an opaque server-side session ID instead.
   */
  continuations?: "compiler_tokens" | "session_ids";
};

/** Parse the closed browser-facing v2 request. */
export function parseEvidenceSearchRequest(
  value: unknown,
  options: EvidenceSearchParseOptions = {},
): EvidenceSearchRequest {
  const sessions = options.continuations === "session_ids";
  const request = objectValue(value, "$");
  exactKeys(request, [
    "stage", "query", "query_claim", "candidate_refs", "query_plan_handoff", "intent", "corpus", "paper_ids", "paper_family_ids",
    "must_terms", "exclude_terms", "scope", "claim_types", "polarity", "openalex_scope", "limit",
    "search_protocol", "clarification_selection", "conversation_history", "context_submission",
  ], "$");
  const searchProtocol = request.search_protocol;
  const contextProtocol = searchProtocol === EVIDENCE_RETRIEVAL_CONTEXT_PROTOCOL_VERSION;
  if (Object.hasOwn(request, "search_protocol") && !contextProtocol) {
    invalid("$.search_protocol", "must be evidence-retrieval/2.3");
  }
  let conversationHistory: ConversationTurn[] | undefined;
  let contextSubmission: EvidenceSearchRequest["contextSubmission"];
  if (Object.hasOwn(request, "conversation_history")) {
    if (!contextProtocol) invalid("$.conversation_history", "requires search_protocol evidence-retrieval/2.3");
    conversationHistory = parseConversationHistory(request.conversation_history);
  }
  if (Object.hasOwn(request, "context_submission")) {
    if (!contextProtocol) invalid("$.context_submission", "requires search_protocol evidence-retrieval/2.3");
    if (Object.hasOwn(request, "clarification_selection")) invalid("$.context_submission", "cannot accompany clarification_selection");
    const submission = objectValue(request.context_submission, "$.context_submission");
    exactRequiredKeys(submission, ["context_token", "content"], "$.context_submission");
    const contextToken = continuationToken(submission.context_token, sessions, isValidContextToken,
      "$.context_submission.context_token", "must be a bounded n1 signed token");
    contextSubmission = { contextToken, content: parseRetrievalContextText(submission.content, "$.context_submission.content") };
  }
  let clarificationSelection: EvidenceSearchRequest["clarificationSelection"];
  if (Object.hasOwn(request, "clarification_selection")) {
    if (!contextProtocol) invalid("$.clarification_selection", "requires search_protocol evidence-retrieval/2.3");
    const selection = objectValue(request.clarification_selection, "$.clarification_selection");
    exactKeys(selection, ["clarification_token", "option_id"], "$.clarification_selection");
    const clarificationToken = continuationToken(selection.clarification_token, sessions,
      isValidContextClarificationToken, "$.clarification_selection.clarification_token",
      "must be a bounded c2 signed token");
    if (typeof selection.option_id !== "string" || !/^[A-Za-z0-9_-]{1,32}$/u.test(selection.option_id)) {
      invalid("$.clarification_selection.option_id", "must be a bounded option identifier");
    }
    clarificationSelection = { clarificationToken, optionId: selection.option_id };
  }
  const query = request.query == null ? null : contextProtocol
    ? parseRetrievalContextText(request.query, "$.query").trim() : stringValue(request.query, "$.query", 8_000);
  const queryClaim = parseClaimRef(request.query_claim);
  const stage = request.stage === undefined ? "ai_rerank" : request.stage;
  if (!SEARCH_STAGES.includes(stage as EvidenceSearchStage)) {
    invalid("$.stage", `must be one of ${SEARCH_STAGES.join(", ")}`);
  }
  const candidateRefs = parseCandidateRefs(request.candidate_refs);
  const queryPlanHandoff = parseQueryPlanHandoff(request.query_plan_handoff, sessions);
  if (contextProtocol && stage === "ai_rerank" && queryPlanHandoff === null) {
    invalid("$.query_plan_handoff", "Interactive final requests require a preview handoff");
  }
  if (stage === "preview" && candidateRefs.length) {
    invalid("$.candidate_refs", "preview requests cannot provide AI candidates");
  }
  if (stage === "preview" && queryPlanHandoff !== null) {
    invalid("$.query_plan_handoff", "preview requests cannot provide a QueryPlan handoff");
  }
  if (stage === "ai_rerank" && request.candidate_refs !== undefined && candidateRefs.length === 0) {
    invalid("$.candidate_refs", "AI rerank requests must provide at least one preview candidate");
  }
  if (query === null && queryClaim === null) invalid("$", "query or query_claim is required");

  const intent = request.intent === undefined ? "claim_search" : request.intent;
  if (!INTENTS.includes(intent as EvidenceSearchIntent)) {
    invalid("$.intent", `must be one of ${INTENTS.join(", ")}`);
  }
  const corpus = request.corpus === undefined ? "cross_paper" : request.corpus;
  if (corpus !== "same_paper" && corpus !== "cross_paper") {
    invalid("$.corpus", "must be same_paper or cross_paper");
  }
  const paperIds = request.paper_ids === undefined ? [] : identifierArray(request.paper_ids, "$.paper_ids", 32);
  const paperFamilyIds = request.paper_family_ids === undefined
    ? [] : identifierArray(request.paper_family_ids, "$.paper_family_ids", 32);
  let openAlexScope: EvidenceSearchRequest["openAlexScope"] = null;
  if (request.openalex_scope !== undefined && request.openalex_scope !== null) {
    const value = objectValue(request.openalex_scope, "$.openalex_scope");
    exactKeys(value, ["level", "id"], "$.openalex_scope");
    const level = stringValue(value.level, "$.openalex_scope.level", 16);
    const id = stringValue(value.id, "$.openalex_scope.id", 100);
    if (!["domain", "field", "subfield", "topic"].includes(level)
      || (!/^https:\/\/openalex\.org\/(?:domains|fields|subfields)\/[0-9]+$/u.test(id)
        && !/^https:\/\/openalex\.org\/T[0-9]+$/u.test(id))) {
      invalid("$.openalex_scope", "must identify one OpenAlex taxonomy node");
    }
    if (contextProtocol && !(level === "topic" ? /^https:\/\/openalex\.org\/T[0-9]+$/u
      : new RegExp(`^https://openalex\\.org/${level}s/[0-9]+$`, "u")).test(id)) {
      invalid("$.openalex_scope", "taxonomy level must match its identifier");
    }
    openAlexScope = { level: level as "domain" | "field" | "subfield" | "topic", id };
  }
  if (corpus === "same_paper" && queryClaim === null && paperIds.length + paperFamilyIds.length !== 1) {
    invalid("$.paper_ids", "same_paper text search requires exactly one paper or paper-family ID");
  }
  const mustTerms = request.must_terms === undefined ? [] : stringArray(request.must_terms, "$.must_terms", 64);
  const excludeTerms = request.exclude_terms === undefined ? [] : stringArray(request.exclude_terms, "$.exclude_terms", 64);
  const excluded = new Set(excludeTerms.map((item) => item.normalize("NFKC").toLocaleLowerCase("und")));
  if (mustTerms.some((item) => excluded.has(item.normalize("NFKC").toLocaleLowerCase("und")))) {
    invalid("$.exclude_terms", "must not exclude a required term");
  }
  const claimTypes = request.claim_types === undefined
    ? [] : enumArray(request.claim_types, "$.claim_types", CLAIM_TYPES);
  const polarity = request.polarity == null ? null : stringValue(request.polarity, "$.polarity", 32);
  if (polarity !== null && !POLARITIES.includes(polarity as ClaimPolarity)) {
    invalid("$.polarity", `must be one of ${POLARITIES.join(", ")}`);
  }
  const limit = request.limit === undefined ? 20 : request.limit;
  if (!Number.isSafeInteger(limit) || (limit as number) < 1 || (limit as number) > RETRIEVAL_MAX_SEARCH_LIMIT) {
    invalid("$.limit", `must be an integer between 1 and ${RETRIEVAL_MAX_SEARCH_LIMIT}`);
  }
  return {
    stage: stage as EvidenceSearchStage,
    ...(contextProtocol ? { searchProtocol: EVIDENCE_RETRIEVAL_CONTEXT_PROTOCOL_VERSION } : {}),
    ...(clarificationSelection === undefined ? {} : { clarificationSelection }),
    ...(conversationHistory === undefined ? {} : { conversationHistory }),
    ...(contextSubmission === undefined ? {} : { contextSubmission }),
    query,
    queryClaim,
    candidateRefs,
    queryPlanHandoff,
    intent: intent as EvidenceSearchIntent,
    corpus,
    paperIds,
    paperFamilyIds,
    openAlexScope,
    mustTerms,
    excludeTerms,
    scope: parseScope(request.scope),
    claimTypes,
    polarity: polarity as ClaimPolarity | null,
    limit: limit as number,
  };
}

function dtoText(value: unknown, path: string, maximum: number, allowEmpty = false) {
  if (typeof value !== "string" || Array.from(value).length > maximum || (!allowEmpty && !value.trim())) {
    invalid(path, `must be ${allowEmpty ? "a" : "a non-empty"} string of at most ${maximum} characters`);
  }
  return value;
}

function dtoArray(value: unknown, path: string, maximum = Number.MAX_SAFE_INTEGER) {
  if (!Array.isArray(value) || value.length > maximum) invalid(path, `must be an array with at most ${maximum} values`);
  return value;
}

function dtoTexts(value: unknown, path: string, maximumItems = Number.MAX_SAFE_INTEGER) {
  return dtoArray(value, path, maximumItems).map((item, index) => dtoText(item, `${path}[${index}]`, 16_000));
}

function nullableDtoText(value: unknown, path: string, maximum: number) {
  return value === null ? null : dtoText(value, path, maximum);
}

function finiteNumber(value: unknown, path: string, minimum?: number, maximum?: number) {
  if (typeof value !== "number" || !Number.isFinite(value)
    || (minimum !== undefined && value < minimum)
    || (maximum !== undefined && value > maximum)) {
    invalid(path, "must be a bounded finite number");
  }
  return value;
}

function nullableFiniteNumber(value: unknown, path: string, minimum?: number, maximum?: number) {
  return value === null ? null : finiteNumber(value, path, minimum, maximum);
}

function isIsoTimestampWithOffset(value: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:[.,]\d{1,9})?)?(Z|[+-](\d{2}):(\d{2}))$/u.exec(value);
  if (!match) return false;
  const [, yearText, monthText, dayText, hourText, minuteText, secondText, , offsetHourText, offsetMinuteText] = match;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const hour = Number(hourText);
  const minute = Number(minuteText);
  const second = secondText === undefined ? 0 : Number(secondText);
  const offsetHour = offsetHourText === undefined ? 0 : Number(offsetHourText);
  const offsetMinute = offsetMinuteText === undefined ? 0 : Number(offsetMinuteText);
  if (year < 1 || month < 1 || month > 12 || hour > 23 || minute > 59 || second > 59
    || offsetHour > 23 || offsetMinute > 59) return false;
  const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate();
  return day >= 1 && day <= daysInMonth;
}

function validateFullScope(value: unknown, path: string) {
  const keys = ["location", "time", "formation", "sample_type", "method", "condition", "measurement", "uncertainty", "entities"];
  const scope = objectValue(value, path);
  exactRequiredKeys(scope, keys, path);
  for (const key of ["location", "formation", "sample_type", "method", "condition", "measurement", "entities"]) {
    dtoArray(scope[key], `${path}.${key}`).forEach((item, index) => {
      dtoText(item, `${path}.${key}[${index}]`, 1_000);
    });
  }
  if (scope.uncertainty !== null) dtoText(scope.uncertainty, `${path}.uncertainty`, 1_000);
  if (scope.time !== null) {
    const time = objectValue(scope.time, `${path}.time`);
    exactRequiredKeys(time, ["label", "start", "end", "unit"], `${path}.time`);
    if (time.label !== null) dtoText(time.label, `${path}.time.label`, 1_000);
    nullableFiniteNumber(time.start, `${path}.time.start`);
    nullableFiniteNumber(time.end, `${path}.time.end`);
    if (time.unit !== null) dtoText(time.unit, `${path}.time.unit`, 1_000);
  }
}

/** Strict stored/service DTO validator used by migration and D1 hydration. */
export function parseEvidenceRetrievalDocumentDto(value: unknown): EvidenceRetrievalDocumentDto {
  const path = "$document";
  const document = objectValue(value, path);
  exactRequiredKeys(document, [
    "index_version", "snapshot_id", "paper_id", "paper_family_id", "root_artifact_version_id",
    "claim_id", "source_claim_id", "original_claim", "canonical_claim", "proposition_key",
    "scoped_claim_id", "paper_title", "evidence_context", "entities", "key_terms",
    "abbreviations", "qualifiers", "claim_type", "polarity", "modality", "attribution",
    "rhetorical_role", "scope", "evidence_anchors", "explicit_relations",
    "shortest_evidence_path", "path_quality", "extractor_prompt_version",
  ], path);
  if (document.index_version !== EVIDENCE_RETRIEVAL_INDEX_VERSION) invalid(`${path}.index_version`, `must equal ${EVIDENCE_RETRIEVAL_INDEX_VERSION}`);
  dtoText(document.snapshot_id, `${path}.snapshot_id`, 128);
  if (!SNAPSHOT_ID_RE.test(document.snapshot_id as string)) invalid(`${path}.snapshot_id`, "must be a canonical snapshot ID");
  for (const key of ["paper_id", "paper_family_id", "root_artifact_version_id", "claim_id", "proposition_key", "scoped_claim_id"]) {
    identifier(document[key], `${path}.${key}`);
  }
  if (!CLAIM_ID_RE.test(document.claim_id as string)
    || document.claim_id !== document.scoped_claim_id) {
    invalid(`${path}.claim_id`, "must equal the canonical scoped Claim SHA-256 ID");
  }
  if (!CLAIM_ID_RE.test(document.scoped_claim_id as string)) {
    invalid(`${path}.scoped_claim_id`, "must be a scoped Claim SHA-256 ID");
  }
  if (!PROPOSITION_KEY_RE.test(document.proposition_key as string)) {
    invalid(`${path}.proposition_key`, "must be a proposition SHA-256 identity");
  }
  if (document.source_claim_id !== null) identifier(document.source_claim_id, `${path}.source_claim_id`);
  nullableDtoText(document.original_claim, `${path}.original_claim`, EXACT_SOURCE_TEXT_MAX);
  dtoText(document.canonical_claim, `${path}.canonical_claim`, 8_000);
  dtoText(document.paper_title, `${path}.paper_title`, 1_000);
  dtoText(document.evidence_context, `${path}.evidence_context`, 16_000, true);
  for (const [key, maximumItems] of [["entities", Number.MAX_SAFE_INTEGER], ["key_terms", 256], ["qualifiers", Number.MAX_SAFE_INTEGER]] as const) {
    dtoArray(document[key], `${path}.${key}`, maximumItems).forEach((item, index) => {
      dtoText(item, `${path}.${key}[${index}]`, 1_000);
    });
  }
  if (!CLAIM_TYPES.includes(document.claim_type as ClaimType)) invalid(`${path}.claim_type`, "has an unsupported value");
  if (!POLARITIES.includes(document.polarity as ClaimPolarity)) invalid(`${path}.polarity`, "has an unsupported value");
  if (!["asserted", "hypothesis", "conditional"].includes(String(document.modality))) invalid(`${path}.modality`, "has an unsupported value");
  if (document.attribution !== "authors_conclusion") invalid(`${path}.attribution`, "must equal authors_conclusion");
  if (document.rhetorical_role !== "terminal_conclusion") invalid(`${path}.rhetorical_role`, "must equal terminal_conclusion");
  dtoText(document.extractor_prompt_version, `${path}.extractor_prompt_version`, 200);
  finiteNumber(document.path_quality, `${path}.path_quality`, 0, 1);
  validateFullScope(document.scope, `${path}.scope`);

  dtoArray(document.abbreviations, `${path}.abbreviations`, 32).forEach((raw, index) => {
    const itemPath = `${path}.abbreviations[${index}]`;
    const item = objectValue(raw, itemPath);
    exactRequiredKeys(item, ["short_form", "long_form"], itemPath);
    dtoText(item.short_form, `${itemPath}.short_form`, 32);
    nullableDtoText(item.long_form, `${itemPath}.long_form`, 1_000);
  });
  dtoArray(document.evidence_anchors, `${path}.evidence_anchors`).forEach((raw, index) => {
    const itemPath = `${path}.evidence_anchors[${index}]`;
    const anchor = objectValue(raw, itemPath);
    exactRequiredKeys(anchor, [
      "anchor_id", "artifact_version_id", "anchor_type", "page", "section", "paragraph", "quote",
      "figure", "table", "row_start", "row_end", "row_range_label", "column_names", "series",
      "char_start", "char_end", "region", "region_label", "dataset_path",
    ], itemPath);
    identifier(anchor.anchor_id, `${itemPath}.anchor_id`);
    identifier(anchor.artifact_version_id, `${itemPath}.artifact_version_id`);
    if (!["text", "figure", "table", "dataset", "metadata", "citation"].includes(String(anchor.anchor_type))) {
      invalid(`${itemPath}.anchor_type`, "has an unsupported value");
    }
    for (const key of ["page", "paragraph", "row_start", "row_end", "char_start", "char_end"]) {
      const minimum = key === "char_start" || key === "char_end" ? 0 : 1;
      if (anchor[key] !== null && (!Number.isSafeInteger(anchor[key]) || (anchor[key] as number) < minimum)) {
        invalid(`${itemPath}.${key}`, `must be an integer of at least ${minimum} or null`);
      }
    }
    for (const key of ["section", "quote", "figure", "table", "row_range_label", "series", "region_label", "dataset_path"]) {
      if (anchor[key] !== null) {
        dtoText(anchor[key], `${itemPath}.${key}`, key === "quote" ? EXACT_SOURCE_TEXT_MAX : 1_000);
      }
    }
    dtoArray(anchor.column_names, `${itemPath}.column_names`).forEach((item, columnIndex) => {
      dtoText(item, `${itemPath}.column_names[${columnIndex}]`, 1_000);
    });
    if (anchor.region !== null) {
      const region = objectValue(anchor.region, `${itemPath}.region`);
      exactRequiredKeys(region, ["x", "y", "width", "height", "unit"], `${itemPath}.region`);
      const x = finiteNumber(region.x, `${itemPath}.region.x`, 0);
      const y = finiteNumber(region.y, `${itemPath}.region.y`, 0);
      const width = finiteNumber(region.width, `${itemPath}.region.width`, Number.MIN_VALUE);
      const height = finiteNumber(region.height, `${itemPath}.region.height`, Number.MIN_VALUE);
      if (region.unit !== "normalized" && region.unit !== "pixel") invalid(`${itemPath}.region.unit`, "has an unsupported value");
      // The same tolerance as the graph validators, which accept the snapshot first.
      if (region.unit === "normalized" && (x > 1 || y > 1
        || x + width > NORMALIZED_REGION_EXTENT_MAX || y + height > NORMALIZED_REGION_EXTENT_MAX)) {
        invalid(`${itemPath}.region`, "normalized coordinates must fit within [0, 1]");
      }
    }
  });
  const relationIds = new Set<string>();
  dtoArray(document.explicit_relations, `${path}.explicit_relations`).forEach((raw, index) => {
    const itemPath = `${path}.explicit_relations[${index}]`;
    const relation = objectValue(raw, itemPath);
    exactRequiredKeys(relation, [
      "edge_id", "source_claim_id", "target_claim_id", "other_claim_id", "relation",
      "direction", "confidence", "scope_overlap",
    ], itemPath);
    for (const key of ["edge_id", "source_claim_id", "target_claim_id", "other_claim_id"]) identifier(relation[key], `${itemPath}.${key}`);
    if (relationIds.has(relation.edge_id as string)) invalid(`${itemPath}.edge_id`, "must be unique within the Claim document");
    relationIds.add(relation.edge_id as string);
    if (!["equivalent_to", "supports", "contradicts", "refines", "qualifies", "supplements"].includes(String(relation.relation))) {
      invalid(`${itemPath}.relation`, "has an unsupported value");
    }
    if (relation.direction !== "outgoing" && relation.direction !== "incoming") invalid(`${itemPath}.direction`, "has an unsupported value");
    const validDirection = relation.direction === "outgoing"
      ? relation.source_claim_id === document.claim_id && relation.target_claim_id === relation.other_claim_id
      : relation.target_claim_id === document.claim_id && relation.source_claim_id === relation.other_claim_id;
    if (!validDirection) invalid(itemPath, "direction and endpoints must match the containing Claim");
    nullableFiniteNumber(relation.confidence, `${itemPath}.confidence`, 0, 1);
    if (!["partial", "source_within_target", "target_within_source", "equivalent"].includes(String(relation.scope_overlap))) {
      invalid(`${itemPath}.scope_overlap`, "has an unsupported value");
    }
  });
  const evidencePath = objectValue(document.shortest_evidence_path, `${path}.shortest_evidence_path`);
  exactRequiredKeys(evidencePath, ["root_claim_id", "nodes", "links", "depth", "mean_edge_confidence"], `${path}.shortest_evidence_path`);
  identifier(evidencePath.root_claim_id, `${path}.shortest_evidence_path.root_claim_id`);
  if (evidencePath.root_claim_id !== document.claim_id) invalid(`${path}.shortest_evidence_path.root_claim_id`, "must equal claim_id");
  dtoArray(evidencePath.nodes, `${path}.shortest_evidence_path.nodes`).forEach((raw, index) => {
    const itemPath = `${path}.shortest_evidence_path.nodes[${index}]`;
    const node = objectValue(raw, itemPath);
    exactRequiredKeys(node, ["id", "kind", "label"], itemPath);
    identifier(node.id, `${itemPath}.id`);
    dtoText(node.kind, `${itemPath}.kind`, 64);
    dtoText(node.label, `${itemPath}.label`, 280);
  });
  dtoArray(evidencePath.links, `${path}.shortest_evidence_path.links`).forEach((raw, index) => {
    const itemPath = `${path}.shortest_evidence_path.links[${index}]`;
    const link = objectValue(raw, itemPath);
    exactRequiredKeys(link, ["from_id", "to_id", "relation", "link_type", "confidence"], itemPath);
    identifier(link.from_id, `${itemPath}.from_id`);
    identifier(link.to_id, `${itemPath}.to_id`);
    dtoText(link.relation, `${itemPath}.relation`, 64);
    if (link.link_type !== "edge" && link.link_type !== "provenance") invalid(`${itemPath}.link_type`, "has an unsupported value");
    nullableFiniteNumber(link.confidence, `${itemPath}.confidence`, 0, 1);
  });
  if (!Number.isSafeInteger(evidencePath.depth) || (evidencePath.depth as number) < 0) invalid(`${path}.shortest_evidence_path.depth`, "must be a non-negative integer");
  nullableFiniteNumber(evidencePath.mean_edge_confidence, `${path}.shortest_evidence_path.mean_edge_confidence`, 0, 1);
  return value as EvidenceRetrievalDocumentDto;
}

export function parseEvidenceRetrievalAssessmentDto(value: unknown): EvidenceRetrievalAssessmentDto {
  const path = "$assessment";
  const assessment = objectValue(value, path);
  exactRequiredKeys(assessment, [
    "index_version", "verification_id", "snapshot_id", "assessment_id", "claim_id", "scoped_claim_id",
    "verdict", "confidence", "evidence_ids", "rationale", "limitations", "verifier_model",
    "verifier_prompt_version", "created_at",
  ], path);
  if (assessment.index_version !== EVIDENCE_RETRIEVAL_INDEX_VERSION) invalid(`${path}.index_version`, `must equal ${EVIDENCE_RETRIEVAL_INDEX_VERSION}`);
  for (const key of ["verification_id", "assessment_id", "claim_id", "scoped_claim_id"]) identifier(assessment[key], `${path}.${key}`);
  dtoText(assessment.snapshot_id, `${path}.snapshot_id`, 128);
  if (!SNAPSHOT_ID_RE.test(assessment.snapshot_id as string)) invalid(`${path}.snapshot_id`, "must be a canonical snapshot ID");
  if (!["supported", "contradicted", "qualified", "uncertain", "insufficient_evidence"].includes(String(assessment.verdict))) {
    invalid(`${path}.verdict`, "has an unsupported value");
  }
  finiteNumber(assessment.confidence, `${path}.confidence`, 0, 1);
  dtoTexts(assessment.evidence_ids, `${path}.evidence_ids`);
  dtoText(assessment.rationale, `${path}.rationale`, 16_000);
  dtoTexts(assessment.limitations, `${path}.limitations`);
  dtoText(assessment.verifier_model, `${path}.verifier_model`, 200);
  dtoText(assessment.verifier_prompt_version, `${path}.verifier_prompt_version`, 200);
  dtoText(assessment.created_at, `${path}.created_at`, 64);
  return value as EvidenceRetrievalAssessmentDto;
}

/** Strict immutable manifest validator shared by D1 migration and service sync. */
export function parseEvidenceRetrievalManifestDto(value: unknown): EvidenceRetrievalManifestDto {
  const path = "$manifest";
  const manifest = objectValue(value, path);
  exactRequiredKeys(manifest, [
    "index_version", "snapshot_id", "paper_id", "paper_family_id",
    "root_artifact_version_id", "root_artifact_sha256", "graph_sha256",
    "graph_schema_version", "extractor_prompt_version", "document_count", "created_at",
  ], path);
  if (manifest.index_version !== EVIDENCE_RETRIEVAL_INDEX_VERSION) {
    invalid(`${path}.index_version`, `must equal ${EVIDENCE_RETRIEVAL_INDEX_VERSION}`);
  }
  dtoText(manifest.snapshot_id, `${path}.snapshot_id`, 128);
  if (!SNAPSHOT_ID_RE.test(manifest.snapshot_id as string)) {
    invalid(`${path}.snapshot_id`, "must be a canonical snapshot ID");
  }
  for (const key of ["paper_id", "paper_family_id", "root_artifact_version_id"]) {
    identifier(manifest[key], `${path}.${key}`);
  }
  for (const key of ["root_artifact_sha256", "graph_sha256"]) {
    const hash = dtoText(manifest[key], `${path}.${key}`, 64);
    if (!SHA256_RE.test(hash)) invalid(`${path}.${key}`, "must be a lowercase SHA-256 digest");
  }
  if (manifest.graph_schema_version !== "scientific-evidence-graph/1.0") {
    invalid(`${path}.graph_schema_version`, "must equal scientific-evidence-graph/1.0");
  }
  dtoText(manifest.extractor_prompt_version, `${path}.extractor_prompt_version`, 200);
  if (!Number.isSafeInteger(manifest.document_count) || (manifest.document_count as number) < 0) {
    invalid(`${path}.document_count`, "must be a non-negative safe integer");
  }
  const createdAt = dtoText(manifest.created_at, `${path}.created_at`, 40);
  if (!isIsoTimestampWithOffset(createdAt)) {
    invalid(`${path}.created_at`, "must be an ISO timestamp with a UTC offset");
  }
  if ((manifest.snapshot_id as string).slice(-64) !== manifest.graph_sha256) {
    invalid(path, "snapshot_id and graph_sha256 must identify the same immutable graph");
  }
  return value as EvidenceRetrievalManifestDto;
}

export function toEvidenceRetrievalManifestCheckRequestDto(
  projectId: string,
  bundles: Array<Pick<EvidenceRetrievalSnapshotBundle, "manifest">>,
): EvidenceRetrievalManifestCheckRequestDto {
  return {
    schema_version: EVIDENCE_RETRIEVAL_INDEX_VERSION,
    project_id: projectId,
    manifests: bundles.map((bundle) => toEvidenceRetrievalManifestDto(bundle.manifest)),
  };
}

export function toEvidenceRetrievalIndexRequestDto(
  projectId: string,
  bundle: EvidenceRetrievalIndexBundle,
): EvidenceRetrievalIndexRequestDto {
  return {
    schema_version: EVIDENCE_RETRIEVAL_INDEX_VERSION,
    project_id: projectId,
    manifest: toEvidenceRetrievalManifestDto(bundle.manifest),
    documents: bundle.documents.map(toEvidenceRetrievalDocumentDto),
  };
}

export function toEvidenceRetrievalSearchRequestDto(
  projectId: string,
  resolved: ResolvedEvidenceSearchCorpus,
): EvidenceRetrievalSearchRequestDto {
  const queryPlanHandoff = resolved.request.queryPlanHandoff ?? null;
  if (queryPlanHandoff !== null && !isValidQueryPlanHandoff(queryPlanHandoff)) {
    throw new TypeError("Retrieval QueryPlan handoff has an invalid transport shape");
  }
  if (resolved.request.stage === "preview" && queryPlanHandoff !== null) {
    throw new TypeError("Retrieval preview requests cannot provide a QueryPlan handoff");
  }
  const schemaVersion = resolved.request.searchProtocol === EVIDENCE_RETRIEVAL_CONTEXT_PROTOCOL_VERSION
    ? EVIDENCE_RETRIEVAL_CONTEXT_PROTOCOL_VERSION
    : resolved.request.stage === "preview" || queryPlanHandoff !== null
    ? EVIDENCE_RETRIEVAL_SEARCH_PROTOCOL_VERSION
    : EVIDENCE_RETRIEVAL_INDEX_VERSION;
  const dto: EvidenceRetrievalSearchRequestDto = {
    schema_version: schemaVersion,
    project_id: projectId,
    stage: resolved.request.stage,
    query: resolved.queryText,
    query_claim: resolved.request.queryClaim == null ? null : {
      snapshot_id: resolved.request.queryClaim.snapshotId,
      claim_id: resolved.request.queryClaim.claimId,
    },
    candidate_refs: resolved.request.candidateRefs.map((ref) => ({
      snapshot_id: ref.snapshotId,
      claim_id: ref.claimId,
    })),
    intent: resolved.request.intent,
    must_terms: resolved.request.mustTerms,
    exclude_terms: resolved.request.excludeTerms,
    scope: resolved.request.scope,
    claim_types: resolved.request.claimTypes,
    polarity: resolved.request.polarity,
    filters: {
      corpus: resolved.request.corpus,
      ...(schemaVersion === EVIDENCE_RETRIEVAL_CONTEXT_PROTOCOL_VERSION && resolved.request.openAlexScope !== null
        ? { openalex_scope: resolved.request.openAlexScope } : {}),
      paper_ids: resolved.request.paperIds,
      paper_family_ids: resolved.request.paperFamilyIds,
      active_snapshot_ids: resolved.manifests.length
        ? encodeActiveSnapshotIds(resolved.manifests.map((manifest) => manifest.snapshotId))
        : null,
      limit: resolved.request.limit,
    },
  };
  if (queryPlanHandoff !== null) dto.query_plan_handoff = queryPlanHandoff;
  if (resolved.request.clarificationSelection !== undefined) {
    if (schemaVersion !== EVIDENCE_RETRIEVAL_CONTEXT_PROTOCOL_VERSION) {
      throw new TypeError("Clarification selection requires search protocol 2.3");
    }
    dto.clarification_selection = {
      clarification_token: resolved.request.clarificationSelection.clarificationToken,
      option_id: resolved.request.clarificationSelection.optionId,
    };
  }
  if (resolved.request.conversationHistory !== undefined || resolved.request.contextSubmission !== undefined) {
    if (schemaVersion !== EVIDENCE_RETRIEVAL_CONTEXT_PROTOCOL_VERSION) throw new TypeError("Conversation context requires search protocol 2.3");
    if (resolved.request.conversationHistory !== undefined) dto.conversation_history = parseConversationHistory(resolved.request.conversationHistory);
    if (resolved.request.contextSubmission !== undefined) {
      if (dto.clarification_selection !== undefined || !isValidContextToken(resolved.request.contextSubmission.contextToken)) throw new TypeError("Invalid context submission");
      dto.context_submission = { context_token: resolved.request.contextSubmission.contextToken,
        content: parseRetrievalContextText(resolved.request.contextSubmission.content, "$.context_submission.content") };
    }
  }
  return dto;
}

export function toEvidenceRetrievalDeleteSnapshotsRequestDto(
  projectId: string,
  snapshotIds: string[],
): EvidenceRetrievalDeleteSnapshotsRequestDto {
  if (snapshotIds.length < 1 || snapshotIds.length > 4_096) {
    throw new TypeError("Retrieval snapshot deletion requires 1 to 4096 snapshot IDs");
  }
  if (new Set(snapshotIds).size !== snapshotIds.length || snapshotIds.some((id) => !SNAPSHOT_ID_RE.test(id))) {
    throw new TypeError("Retrieval snapshot deletion requires unique canonical snapshot IDs");
  }
  return {
    schema_version: EVIDENCE_RETRIEVAL_INDEX_VERSION,
    index_version: EVIDENCE_RETRIEVAL_INDEX_VERSION,
    project_id: projectId,
    snapshot_ids: snapshotIds,
  };
}
