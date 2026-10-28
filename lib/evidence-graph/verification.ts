import { SCOPED_CLAIM_ID_RE } from "./canonical.ts";
import { createEvidencePathExpander, hashEvidenceGraph, parseEvidenceGraph } from "./paths.ts";
import type { Assessment, EvidenceGraph } from "./types.ts";
import {
  allEntities,
  invalid,
} from "./validation.ts";

const IDENTIFIER_RE = /^[A-Za-z][A-Za-z0-9._:-]{0,127}$/u;
const SHA256_RE = /^[a-f0-9]{64}$/u;

export interface CompilerAssessment {
  id: string;
  claim_id: string;
  scoped_claim_id: string;
  verdict: "supported" | "contradicted" | "qualified" | "uncertain" | "insufficient_evidence";
  confidence: number;
  evidence_ids: string[];
  rationale: string;
  limitations: string[];
}

/** Exact verifier-report JSON accepted from the standalone Compiler. */
export interface CompilerVerificationReport {
  schema_version: "1.0";
  paper_id: string;
  graph_sha256: string;
  verifier_model: string;
  assessments: CompilerAssessment[];
  warnings: string[];
}

function record(
  value: unknown,
  path: string,
  allowed: readonly string[],
  required: readonly string[] = allowed,
) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    invalid(path, "VERIFICATION_TYPE", "must be an object");
  }
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) {
    invalid(path, "VERIFICATION_TYPE", "must be a plain object");
  }
  const item = value as Record<string, unknown>;
  const allowedSet = new Set(allowed);
  for (const key of Object.keys(item)) {
    if (!allowedSet.has(key)) {
      invalid(`${path}.${key}`, "VERIFICATION_UNKNOWN_FIELD", "is not allowed by the Compiler contract");
    }
  }
  for (const key of required) {
    if (!Object.hasOwn(item, key)) {
      invalid(`${path}.${key}`, "VERIFICATION_MISSING_FIELD", "is required by the Compiler contract");
    }
  }
  return item;
}

function list(value: unknown, path: string) {
  if (!Array.isArray(value)) invalid(path, "VERIFICATION_TYPE", "must be an array");
  return value;
}

function string(value: unknown, path: string) {
  if (typeof value !== "string" || !value.trim()) {
    invalid(path, "VERIFICATION_TYPE", "must be a non-empty string");
  }
  return value.trim();
}

function identifier(value: unknown, path: string) {
  const parsed = string(value, path);
  if (!IDENTIFIER_RE.test(parsed)) {
    invalid(path, "VERIFICATION_ID", "does not match the Compiler Identifier contract");
  }
  return parsed;
}

function strings(value: unknown, path: string) {
  const values = list(value, path).map((item, index) => string(item, `${path}[${index}]`));
  if (new Set(values).size !== values.length) {
    invalid(path, "VERIFICATION_DUPLICATE", "must not contain duplicates");
  }
  return values;
}

function identifiers(value: unknown, path: string) {
  const values = list(value, path).map((item, index) => identifier(item, `${path}[${index}]`));
  if (new Set(values).size !== values.length) {
    invalid(path, "VERIFICATION_DUPLICATE", "must not contain duplicates");
  }
  return values;
}

function probability(value: unknown, path: string) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 1) {
    invalid(path, "VERIFICATION_PROBABILITY", "must be between 0 and 1");
  }
}

function verdict(value: unknown, path: string): CompilerAssessment["verdict"] {
  const parsed = string(value, path);
  const allowed = ["supported", "contradicted", "qualified", "uncertain", "insufficient_evidence"] as const;
  if (!allowed.some((item) => item === parsed)) {
    invalid(path, "VERIFICATION_ENUM", `must be one of ${allowed.join(", ")}`);
  }
  return parsed as CompilerAssessment["verdict"];
}

/** Validate a report without joining it to an extraction graph. */
export function validateCompilerVerificationReport(value: unknown): CompilerVerificationReport {
  const report = record(
    value,
    "$verification",
    ["schema_version", "paper_id", "graph_sha256", "verifier_model", "assessments", "warnings"],
  );
  if (report.schema_version !== "1.0") {
    invalid("$verification.schema_version", "VERIFICATION_SCHEMA", "must equal 1.0");
  }
  identifier(report.paper_id, "$verification.paper_id");
  if (!SHA256_RE.test(string(report.graph_sha256, "$verification.graph_sha256"))) {
    invalid("$verification.graph_sha256", "VERIFICATION_HASH", "must be a SHA-256 digest");
  }
  string(report.verifier_model, "$verification.verifier_model");
  strings(report.warnings, "$verification.warnings");
  list(report.assessments, "$verification.assessments").forEach((raw, index) => {
    const path = `$verification.assessments[${index}]`;
    const assessment = record(
      raw,
      path,
      ["id", "claim_id", "scoped_claim_id", "verdict", "confidence", "evidence_ids", "rationale", "limitations"],
    );
    identifier(assessment.id, `${path}.id`);
    identifier(assessment.claim_id, `${path}.claim_id`);
    if (!SCOPED_CLAIM_ID_RE.test(string(assessment.scoped_claim_id, `${path}.scoped_claim_id`))) {
      invalid(`${path}.scoped_claim_id`, "VERIFICATION_IDENTITY", "must be a scoped claim SHA-256 identity");
    }
    const parsedVerdict = verdict(assessment.verdict, `${path}.verdict`);
    probability(assessment.confidence, `${path}.confidence`);
    const evidenceIds = identifiers(assessment.evidence_ids, `${path}.evidence_ids`);
    if (parsedVerdict !== "insufficient_evidence" && evidenceIds.length === 0) {
      invalid(
        `${path}.evidence_ids`,
        "VERIFICATION_EVIDENCE_REQUIRED",
        "a verdict other than insufficient_evidence requires evidence_ids",
      );
    }
    string(assessment.rationale, `${path}.rationale`);
    strings(assessment.limitations, `${path}.limitations`);
  });
  return value as CompilerVerificationReport;
}

/** Validate one already-parsed report against one validated graph without changing either. */
export function validateCompilerVerificationReferences(
  graph: EvidenceGraph,
  report: CompilerVerificationReport,
) {
  const expandPath = createEvidencePathExpander(graph);
  const claims = new Map(graph.claims.flatMap((claim) => [
    [claim.id, claim] as const,
    ...(claim.source_id == null ? [] : [[claim.source_id, claim] as const]),
  ]));
  const entities = new Map(allEntities(graph).map((item) => [item.id, item]));
  const assessmentIds = new Set<string>();
  const assessedClaimIds = new Set<string>();
  const references = report.assessments.map((raw) => {
    if (assessmentIds.has(raw.id)) {
      invalid("$verification.assessments", "VERIFICATION_DUPLICATE", `duplicates assessment ID ${raw.id}`);
    }
    assessmentIds.add(raw.id);
    const claim = claims.get(raw.claim_id);
    if (!claim) {
      invalid("$verification.assessments", "VERIFICATION_REFERENCE", `${raw.id} references missing claim ${raw.claim_id}`);
    }
    if (assessedClaimIds.has(claim.id)) {
      invalid("$verification.assessments", "VERIFICATION_DUPLICATE", `claim ${raw.claim_id} has multiple assessments`);
    }
    assessedClaimIds.add(claim.id);
    if (raw.scoped_claim_id !== claim.scoped_claim_id) {
      invalid("$verification.assessments", "VERIFICATION_IDENTITY", `${raw.id} has the wrong scoped_claim_id`);
    }
    const remappedEvidence = raw.evidence_ids.map((id) => claims.get(id)?.id ?? id);
    for (const id of remappedEvidence) {
      if (!entities.has(id)) {
        invalid("$verification.assessments", "VERIFICATION_REFERENCE", `${raw.id} references missing evidence ${id}`);
      }
    }
    const evidenceEntities = remappedEvidence.map((id) => entities.get(id)!);
    if (evidenceEntities.some((entity) => entity.kind === "atomic_claim")) {
      invalid(
        "$verification.assessments",
        "VERIFICATION_EVIDENCE_NODE_TYPE",
        `${raw.id} cites a Claim as evidence`,
      );
    }
    const path = expandPath(claim.id);
    const pathIds = new Set(path.entities.map((entity) => entity.id));
    if (remappedEvidence.some((id) => !pathIds.has(id))) {
      invalid("$verification.assessments", "EVIDENCE_PATH", `${raw.id} cites evidence outside the assessed claim path`);
    }
    const hasDirectScientificEvidence = evidenceEntities.some((entity) =>
      entity.kind === "source_anchor"
      || entity.kind === "structured_visual_evidence"
      || entity.kind === "observation"
    );
    if (raw.verdict !== "insufficient_evidence" && !hasDirectScientificEvidence) {
      invalid(
        "$verification.assessments",
        "VERIFICATION_EVIDENCE_REQUIRED",
        `${raw.id} requires a cited SourceAnchor, StructuredVisualEvidence, or Observation`,
      );
    }
    return { assessment: raw, claim, evidenceIds: remappedEvidence, path };
  });
  const missingClaims = graph.claims.filter((claim) => !assessedClaimIds.has(claim.id));
  if (missingClaims.length > 0) {
    invalid(
      "$verification.assessments",
      "VERIFICATION_COVERAGE",
      `must contain exactly one assessment for every claim; missing ${missingClaims.map((claim) => claim.source_id ?? claim.id).join(", ")}`,
    );
  }
  return references;
}

/** Join verifier assessments to a copy of the graph without mutating extraction nodes. */
export async function attachCompilerVerificationReport(
  graph: EvidenceGraph,
  value: unknown,
  metadata: { created_at: string },
) {
  const report = validateCompilerVerificationReport(value);
  if (report.paper_id !== graph.paper_id) {
    invalid("$verification.paper_id", "VERIFICATION_REFERENCE", "does not match canonical graph paper_id");
  }
  if (report.graph_sha256 !== await hashEvidenceGraph(graph)) {
    invalid("$verification.graph_sha256", "VERIFICATION_HASH", "does not match the extraction graph");
  }
  const assessments = validateCompilerVerificationReferences(graph, report).map(({
    assessment,
    claim,
    evidenceIds,
    path,
  }): Assessment => ({
    kind: "assessment",
    id: assessment.id,
    target_claim_id: claim.id,
    verdict: assessment.verdict,
    basis_edge_ids: path.edges.map((edge) => edge.id),
    evidence_ids: evidenceIds,
    rationale: assessment.rationale,
    limitations: assessment.limitations,
    confidence: assessment.confidence,
    assessor: { source: "model", name: "independent-verifier", model: report.verifier_model },
    created_at: metadata.created_at,
  }));
  return parseEvidenceGraph({ ...graph, assessments });
}
