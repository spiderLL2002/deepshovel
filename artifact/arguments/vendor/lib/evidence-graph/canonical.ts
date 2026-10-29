import type { AtomicClaim, ClaimModality, ClaimPolarity, ClaimType, JsonValue, Scope } from "./types.ts";

export const SHA256_RE = /^[a-f0-9]{64}$/u;
export const PROPOSITION_KEY_RE = /^proposition:sha256:([a-f0-9]{64})$/u;
export const SCOPED_CLAIM_ID_RE = /^claim:sha256:([a-f0-9]{64})$/u;
export const SNAPSHOT_ID_RE = /^snapshot:sha256:([a-f0-9]{64})$/u;

function canonicalizeInternal(value: unknown, inArray: boolean): JsonValue | undefined {
  if (value === null || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new TypeError("Canonical JSON rejects non-finite numbers");
    return value === 0 ? 0 : value;
  }
  if (typeof value === "undefined") return inArray ? null : undefined;
  if (Array.isArray(value)) return value.map((item) => canonicalizeInternal(item, true) ?? null);
  if (typeof value === "object") {
    const prototype = Object.getPrototypeOf(value);
    if (prototype !== Object.prototype && prototype !== null) throw new TypeError("Canonical JSON accepts only plain objects and arrays");
    const output: { [key: string]: JsonValue } = {};
    for (const key of Object.keys(value as Record<string, unknown>).sort()) {
      const normalized = canonicalizeInternal((value as Record<string, unknown>)[key], false);
      if (normalized !== undefined) output[key] = normalized;
    }
    return output;
  }
  throw new TypeError(`Canonical JSON does not support ${typeof value}`);
}

export function canonicalize(value: unknown): JsonValue {
  const normalized = canonicalizeInternal(value, false);
  if (normalized === undefined) throw new TypeError("Canonical JSON root cannot be undefined");
  return normalized;
}

export function canonicalStringify(value: unknown) {
  return JSON.stringify(canonicalize(value));
}

export async function sha256Hex(value: string | Uint8Array | ArrayBuffer) {
  const bytes = typeof value === "string" ? new TextEncoder().encode(value) : value instanceof Uint8Array ? value : new Uint8Array(value);
  const copy = new Uint8Array(bytes.byteLength);
  copy.set(bytes);
  const digest = await globalThis.crypto.subtle.digest("SHA-256", copy.buffer);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function hashCanonical(value: unknown) {
  return sha256Hex(canonicalStringify(value));
}

export function normalizeIdentityText(value: string) {
  const normalized = value.normalize("NFC").replace(/\s+/gu, " ").trim().toLowerCase();
  if (!normalized) throw new TypeError("Identity text must not be empty");
  return normalized;
}

function normalizeScopeText(value: string) {
  const normalized = value.normalize("NFC").replace(/\s+/gu, " ").trim();
  if (!normalized) throw new TypeError("Scope text must not be empty");
  return normalized;
}

function normalizedSet(values: string[], normalize = normalizeScopeText) {
  return [...new Set(values.map(normalize))].sort();
}

/** Normalize set-like scope fields before deriving a scoped claim identity. */
export function canonicalScope(scope: Scope): Scope {
  const nullable = (value: string | null) => value === null ? null : normalizeScopeText(value);
  return {
    location: normalizedSet(scope.location),
    time: scope.time === null ? null : {
      label: nullable(scope.time.label),
      start: scope.time.start === 0 ? 0 : scope.time.start,
      end: scope.time.end === 0 ? 0 : scope.time.end,
      unit: nullable(scope.time.unit),
    },
    formation: normalizedSet(scope.formation),
    sample_type: normalizedSet(scope.sample_type),
    method: normalizedSet(scope.method),
    condition: normalizedSet(scope.condition),
    measurement: normalizedSet(scope.measurement),
    uncertainty: nullable(scope.uncertainty),
    entities: normalizedSet(scope.entities),
  };
}

export interface PropositionIdentityInput {
  proposition: string;
  claim_type?: ClaimType;
  polarity?: ClaimPolarity;
  qualifiers?: string[];
}

export async function propositionKey(input: string | PropositionIdentityInput) {
  const value = typeof input === "string" ? { proposition: input } : input;
  return `proposition:sha256:${await sha256Hex(normalizeIdentityText(value.proposition))}`;
}

export async function scopedClaimId(proposition_key: string, scope: Scope) {
  if (!PROPOSITION_KEY_RE.test(proposition_key)) throw new TypeError("Invalid proposition_key");
  return `claim:sha256:${await hashCanonical({ proposition_key, scope: canonicalScope(scope) })}`;
}

export async function createAtomicClaim(input: {
  proposition: string;
  scope: Scope;
  source_id?: string | null;
  claim_type?: ClaimType;
  polarity?: ClaimPolarity;
  qualifiers?: string[];
  anchor_ids?: string[];
  modality?: ClaimModality;
}): Promise<AtomicClaim> {
  const scope = canonicalScope(input.scope);
  const claim_type = input.claim_type ?? "descriptive";
  const polarity = input.polarity ?? "affirmed";
  const qualifiers = normalizedSet(input.qualifiers ?? []);
  const proposition_key = await propositionKey({ proposition: input.proposition, claim_type, polarity, qualifiers });
  const scoped_claim_id = await scopedClaimId(proposition_key, scope);
  return {
    kind: "atomic_claim",
    id: scoped_claim_id,
    source_id: input.source_id ?? null,
    proposition: input.proposition.normalize("NFC").replace(/\s+/gu, " ").trim(),
    claim_type,
    polarity,
    qualifiers,
    anchor_ids: [...new Set(input.anchor_ids ?? [])].sort(),
    proposition_key,
    scoped_claim_id,
    scope,
    modality: input.modality ?? (polarity === "uncertain" ? "hypothesis" : "asserted"),
  };
}
