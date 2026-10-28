import {
  canonicalStringify,
  EvidenceGraphValidationError,
  hashValidatedEvidenceGraph,
  parseEvidenceGraph,
  sha256Hex,
  type EvidenceGraph,
  type CompilerVerificationReport,
  validateCompilerVerificationReferences,
  validateCompilerVerificationReport,
} from "@/lib/evidence-graph";
import {
  COMPILER_EXTRACTION_PROMPT_VERSION,
  COMPILER_VERIFICATION_PROMPT_VERSION,
} from "@/lib/compiler/prompt-version.ts";
import {
  parseCitationSourceProjection,
  toCitationSourceProjectionDto,
  type CitationSourceProjection,
} from "@/lib/citation-reception/index.ts";
import { ApiError } from "@/lib/server/evidence-store";

import { CURRENT_COMPILER_MODEL, isCompilerModel } from "@/lib/compiler/model.ts";

// Kept as a compatibility export for existing callers.
export { CURRENT_COMPILER_MODEL };

export type ModelRun = {
  runId: string;
  model: string;
  promptVersion: string;
};

export type VerificationImport = ModelRun & {
  id: string;
  report: CompilerVerificationReport;
  reportJson: string;
  reportSha256: string;
  createdAt: string;
};

export type OpenAlexHierarchyNode = { id: string; displayName: string };
export type OpenAlexTopic = {
  id: string;
  displayName: string;
  score: number;
  subfield: OpenAlexHierarchyNode;
  field: OpenAlexHierarchyNode;
  domain: OpenAlexHierarchyNode;
};
export type OpenAlexClassification = {
  source: "openalex_work" | "official_title_abstract_model";
  classifier: string;
  openAlexWorkId: string | null;
  matchedDoi: string | null;
  primaryTopic: OpenAlexTopic;
  topics: OpenAlexTopic[];
};

/**
 * Value-free record of every Compiler rewrite of model output (hydrated
 * provenance, coordinate repairs, removed Claims, unavailable projections).
 * Absent from envelopes produced before 2026-10.
 */
export type CompilerAudit = {
  schemaVersion: "compiler-audit/1.0";
  entries: { kind: string; target: string | null; detail: string | null }[];
  omittedEntries: number;
  warnings: string[];
};

export type EvidenceImport = {
  graph: EvidenceGraph;
  snapshotId: string;
  rootArtifactVersionId: string;
  graphSha256: string;
  graphJson: string;
  counts: ReturnType<typeof graphCounts>;
  countsJson: string;
  extraction: ModelRun;
  verification: VerificationImport | null;
  openAlex: OpenAlexClassification | null;
  compilerAudit: CompilerAudit | null;
  citationSource: {
    projection: CitationSourceProjection;
    sourceJson: string;
    sourceSha256: string;
  } | null;
  createdAt: string;
};

function objectValue(value: unknown, path: string) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ApiError(400, "INVALID_IMPORT_ENVELOPE", `${path} must be a JSON object`);
  }
  return value as Record<string, unknown>;
}

function stringValue(value: unknown, path: string, maximum = 200) {
  if (typeof value !== "string" || !value.trim() || value.length > maximum) {
    throw new ApiError(400, "INVALID_IMPORT_ENVELOPE", `${path} must be a non-empty string of at most ${maximum} characters`);
  }
  return value.trim();
}

function exactKeys(value: Record<string, unknown>, allowed: readonly string[], path: string) {
  const unknown = Object.keys(value).filter((key) => !allowed.includes(key));
  if (unknown.length) {
    throw new ApiError(400, "INVALID_IMPORT_ENVELOPE", `${path} contains unknown fields: ${unknown.join(", ")}`);
  }
}

function optionalString(value: unknown, path: string, maximum: number) {
  if (value === null || value === undefined) return null;
  return stringValue(value, path, maximum);
}

function hierarchyNode(value: unknown, path: string): OpenAlexHierarchyNode {
  const node = objectValue(value, path);
  exactKeys(node, ["id", "display_name"], path);
  return {
    id: stringValue(node.id, `${path}.id`, 100),
    displayName: stringValue(node.display_name, `${path}.display_name`, 500),
  };
}

function openAlexTopic(value: unknown, path: string): OpenAlexTopic {
  const topic = objectValue(value, path);
  exactKeys(topic, ["id", "display_name", "score", "subfield", "field", "domain"], path);
  const id = stringValue(topic.id, `${path}.id`, 100);
  if (!/^https:\/\/openalex\.org\/T[0-9]+$/u.test(id)
    || typeof topic.score !== "number" || !Number.isFinite(topic.score)
    || topic.score < 0 || topic.score > 1) {
    throw new ApiError(400, "INVALID_OPENALEX_CLASSIFICATION", `${path} must be an official OpenAlex Topic`);
  }
  return {
    id,
    displayName: stringValue(topic.display_name, `${path}.display_name`, 500),
    score: topic.score,
    subfield: hierarchyNode(topic.subfield, `${path}.subfield`),
    field: hierarchyNode(topic.field, `${path}.field`),
    domain: hierarchyNode(topic.domain, `${path}.domain`),
  };
}

export function parseOpenAlexClassification(value: unknown, path = "$.openalex"): OpenAlexClassification | null {
  if (value === undefined || value === null) return null;
  const classification = objectValue(value, path);
  exactKeys(classification, [
    "source", "classifier", "openalex_work_id", "matched_doi", "primary_topic", "topics",
  ], path);
  if (classification.source !== "openalex_work" && classification.source !== "official_title_abstract_model") {
    throw new ApiError(400, "INVALID_OPENALEX_CLASSIFICATION", `${path}.source is invalid`);
  }
  if (!Array.isArray(classification.topics) || classification.topics.length < 1 || classification.topics.length > 3) {
    throw new ApiError(400, "INVALID_OPENALEX_CLASSIFICATION", `${path}.topics must contain one to three Topics`);
  }
  const topics = classification.topics.map((topic, index) => openAlexTopic(topic, `${path}.topics[${index}]`));
  const primaryTopic = openAlexTopic(classification.primary_topic, `${path}.primary_topic`);
  if (new Set(topics.map((topic) => topic.id)).size !== topics.length
    || JSON.stringify(primaryTopic) !== JSON.stringify(topics[0])) {
    throw new ApiError(400, "INVALID_OPENALEX_CLASSIFICATION", `${path}.primary_topic must equal the first unique Topic`);
  }
  return {
    source: classification.source,
    classifier: stringValue(classification.classifier, `${path}.classifier`, 300),
    openAlexWorkId: optionalString(classification.openalex_work_id, `${path}.openalex_work_id`, 100),
    matchedDoi: optionalString(classification.matched_doi, `${path}.matched_doi`, 500),
    primaryTopic,
    topics,
  };
}

const AUDIT_CODE_RE = /^[a-z][a-z0-9_]{0,63}$/u;
const AUDIT_TARGET_RE = /^[A-Za-z][A-Za-z0-9._:-]{0,127}$/u;

function auditText(value: unknown, path: string, maximum: number) {
  if (typeof value !== "string" || !value || value.length > maximum) {
    throw new ApiError(400, "INVALID_COMPILER_AUDIT", `${path} must be a non-empty string of at most ${maximum} characters`);
  }
  return value;
}

export function parseCompilerAudit(value: unknown, path = "$.compiler_audit"): CompilerAudit | null {
  if (value === undefined || value === null) return null;
  const audit = objectValue(value, path);
  exactKeys(audit, ["schema_version", "entries", "omitted_entries", "warnings"], path);
  if (audit.schema_version !== "compiler-audit/1.0") {
    throw new ApiError(422, "INVALID_COMPILER_AUDIT", `${path}.schema_version is unsupported`);
  }
  if (!Array.isArray(audit.entries) || audit.entries.length > 512) {
    throw new ApiError(400, "INVALID_COMPILER_AUDIT", `${path}.entries must be an array of at most 512 entries`);
  }
  if (!Number.isSafeInteger(audit.omitted_entries) || (audit.omitted_entries as number) < 0) {
    throw new ApiError(400, "INVALID_COMPILER_AUDIT", `${path}.omitted_entries must be a non-negative integer`);
  }
  if (!Array.isArray(audit.warnings) || audit.warnings.length > 64) {
    throw new ApiError(400, "INVALID_COMPILER_AUDIT", `${path}.warnings must be an array of at most 64 codes`);
  }
  const entries = audit.entries.map((raw, index) => {
    const entryPath = `${path}.entries[${index}]`;
    const entry = objectValue(raw, entryPath);
    exactKeys(entry, ["kind", "target", "detail"], entryPath);
    const kind = auditText(entry.kind, `${entryPath}.kind`, 64);
    if (!AUDIT_CODE_RE.test(kind)) {
      throw new ApiError(400, "INVALID_COMPILER_AUDIT", `${entryPath}.kind must be a lowercase code`);
    }
    const target = entry.target === null ? null : auditText(entry.target, `${entryPath}.target`, 128);
    if (target !== null && !AUDIT_TARGET_RE.test(target)) {
      throw new ApiError(400, "INVALID_COMPILER_AUDIT", `${entryPath}.target must be a graph-local identifier`);
    }
    const detail = entry.detail === null ? null : auditText(entry.detail, `${entryPath}.detail`, 300);
    return { kind, target, detail };
  });
  return {
    schemaVersion: "compiler-audit/1.0",
    entries,
    omittedEntries: audit.omitted_entries as number,
    warnings: audit.warnings.map((item, index) => auditText(item, `${path}.warnings[${index}]`, 300)),
  };
}

export function graphCounts(graph: EvidenceGraph) {
  return {
    artifactVersions: graph.artifact_versions.length,
    anchors: graph.anchors.length,
    visualEvidence: graph.visual_evidence.length,
    observations: graph.observations.length,
    assumptions: graph.assumptions.length,
    reasoning: graph.reasoning.length,
    claims: graph.claims.length,
    edges: graph.edges.length,
    assessments: graph.assessments.length,
  };
}

function assertExtractionOnlyGraph(graph: EvidenceGraph) {
  if (graph.assessments.length > 0) {
    throw new ApiError(422, "VERIFIER_DATA_IN_EXTRACTION_GRAPH", "Extraction graph must not contain assessments; import them through $.verification");
  }
  if (graph.snapshots.some((snapshot) => snapshot.generator.source === "verifier")) {
    throw new ApiError(422, "VERIFIER_DATA_IN_EXTRACTION_GRAPH", "Extraction graph must not contain verifier-generated snapshots");
  }
}

function chooseRootArtifactVersion(graph: EvidenceGraph, requested: unknown) {
  if (requested !== undefined) {
    const id = stringValue(requested, "$.root_artifact_version_id");
    if (!graph.artifact_versions.some((version) => version.id === id)) {
      throw new ApiError(400, "ROOT_VERSION_NOT_IN_GRAPH", "Root artifact version is not present in the graph");
    }
    return id;
  }
  const paperArtifacts = new Set(
    graph.artifacts.filter((artifact) => artifact.artifact_type === "paper").map((artifact) => artifact.id),
  );
  const candidates = graph.artifact_versions.filter((version) => paperArtifacts.has(version.artifact_id));
  if (candidates.length !== 1) {
    throw new ApiError(400, "ROOT_VERSION_REQUIRED", "root_artifact_version_id is required unless the graph has exactly one paper version");
  }
  return candidates[0].id;
}

function assertSingleSourceSnapshot(graph: EvidenceGraph) {
  if (graph.artifacts.length !== 1 || graph.artifact_versions.length !== 1) {
    throw new ApiError(
      422,
      "MULTI_SOURCE_GRAPH_UNSUPPORTED",
      "One immutable snapshot must contain exactly one source Artifact and its root ArtifactVersion",
    );
  }
}

function parseModelRun(value: unknown, path: "$.extraction" | "$.verification", mismatchCode: string) {
  const run = objectValue(value, path);
  const allowed = path === "$.verification"
    ? ["run_id", "model", "prompt_version", "report"]
    : ["run_id", "model", "prompt_version"];
  exactKeys(run, allowed, path);
  const metadata = {
    runId: stringValue(run.run_id, `${path}.run_id`),
    model: stringValue(run.model, `${path}.model`),
    promptVersion: stringValue(run.prompt_version, `${path}.prompt_version`),
  };
  if (!isCompilerModel(metadata.model)) {
    throw new ApiError(422, mismatchCode, `${path === "$.extraction" ? "Extractor" : "Verifier"} model is unsupported`);
  }
  return { metadata, object: run };
}

async function parseVerification(
  value: unknown,
  graph: EvidenceGraph,
  graphSha256: string,
  createdAt: string,
): Promise<VerificationImport | null> {
  if (value === undefined || value === null) return null;
  const { metadata, object } = parseModelRun(value, "$.verification", "VERIFIER_MODEL_MISMATCH");

  let report: CompilerVerificationReport;
  try {
    report = validateCompilerVerificationReport(object.report);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown verifier report validation failure";
    throw new ApiError(422, "INVALID_VERIFICATION_REPORT", message);
  }
  if (report.verifier_model !== metadata.model) {
    throw new ApiError(422, "VERIFIER_MODEL_MISMATCH", "Verifier envelope and report models must match");
  }
  if (report.paper_id !== graph.paper_id) {
    throw new ApiError(422, "VERIFICATION_PAPER_MISMATCH", "Verifier report paper_id does not match the imported graph");
  }
  if (report.graph_sha256 !== graphSha256) {
    throw new ApiError(422, "VERIFICATION_GRAPH_MISMATCH", "Verifier report graph_sha256 must match the imported canonical graph");
  }

  try {
    validateCompilerVerificationReferences(graph, report);
  } catch (error) {
    if (error instanceof EvidenceGraphValidationError && error.code === "VERIFICATION_IDENTITY") {
      throw new ApiError(422, "VERIFICATION_CLAIM_MISMATCH", error.message);
    }
    const message = error instanceof Error ? error.message : "Unknown verifier report reference failure";
    throw new ApiError(422, "INVALID_VERIFICATION_REPORT", message);
  }

  const reportJson = canonicalStringify(report);
  const reportSha256 = await sha256Hex(reportJson);
  return {
    ...metadata,
    id: `verification:sha256:${reportSha256}`,
    report,
    reportJson,
    reportSha256,
    createdAt,
  };
}

/** Parse and semantically validate the exact standalone Compiler import envelope. */
export async function parseEvidenceImportEnvelope(
  payloadValue: unknown,
  createdAt = new Date().toISOString(),
  citationSourceValue?: unknown,
): Promise<EvidenceImport> {
  const payload = objectValue(payloadValue, "$");
  exactKeys(payload, ["graph", "root_artifact_version_id", "extraction", "verification", "openalex", "compiler_audit"], "$");
  const extraction = parseModelRun(payload.extraction, "$.extraction", "EXTRACTOR_MODEL_MISMATCH").metadata;
  if (extraction.promptVersion !== COMPILER_EXTRACTION_PROMPT_VERSION) {
    throw new ApiError(422, "COMPILER_EXTRACTION_CONTRACT_MISMATCH", "Compiler extraction prompt version is unsupported");
  }

  let graph: EvidenceGraph;
  try {
    graph = await parseEvidenceGraph(payload.graph);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown evidence graph validation failure";
    throw new ApiError(422, "INVALID_EVIDENCE_GRAPH", message);
  }
  assertExtractionOnlyGraph(graph);

  const rootArtifactVersionId = chooseRootArtifactVersion(graph, payload.root_artifact_version_id);
  assertSingleSourceSnapshot(graph);
  const graphSha256 = await hashValidatedEvidenceGraph(graph);
  const snapshotId = `snapshot:sha256:${graphSha256}`;
  const counts = graphCounts(graph);
  const verification = await parseVerification(payload.verification, graph, graphSha256, createdAt);
  const openAlex = parseOpenAlexClassification(payload.openalex);
  const compilerAudit = parseCompilerAudit(payload.compiler_audit);
  let citationSource: EvidenceImport["citationSource"] = null;
  if (citationSourceValue !== undefined) {
    try {
      const compilerProjection = parseCitationSourceProjection(citationSourceValue);
      if (compilerProjection.artifactVersionId !== rootArtifactVersionId) {
        throw new Error("citation source belongs to a different ArtifactVersion");
      }
      // Compiler source IDs are stable per ArtifactVersion. D1 facts are owned
      // by an immutable graph snapshot, so namespace the same deterministic
      // source projection when a legitimate recompile creates another snapshot.
      const referenceIds = new Map(await Promise.all(compilerProjection.references.map(async (item) => [
        item.id,
        `citation-reference:sha256:${await sha256Hex(`${snapshotId}\u0000${item.id}`)}`,
      ] as const)));
      const projection = {
        ...compilerProjection,
        references: compilerProjection.references.map((item) => ({
          ...item,
          id: referenceIds.get(item.id)!,
        })),
        mentions: await Promise.all(compilerProjection.mentions.map(async (item) => ({
          ...item,
          id: `citation-mention:sha256:${await sha256Hex(`${snapshotId}\u0000${item.id}`)}`,
          referenceId: referenceIds.get(item.referenceId)!,
        }))),
      };
      const sourceJson = canonicalStringify(toCitationSourceProjectionDto(projection));
      citationSource = {
        projection,
        sourceJson,
        sourceSha256: await sha256Hex(sourceJson),
      };
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unknown citation source validation failure";
      throw new ApiError(422, "INVALID_CITATION_SOURCE", message);
    }
  }
  if (graph.claims.length > 0 && verification === null) {
    throw new ApiError(
      422,
      "COMPILER_VERIFICATION_MISSING",
      "Compiler imports with Claims require exactly one verifier assessment per Claim",
    );
  }
  if (verification !== null && verification.promptVersion !== COMPILER_VERIFICATION_PROMPT_VERSION) {
    throw new ApiError(
      422,
      "COMPILER_PROMPT_VERSION_MISMATCH",
      `Compiler extraction requires ${COMPILER_VERIFICATION_PROMPT_VERSION}`,
    );
  }
  return {
    graph,
    snapshotId,
    rootArtifactVersionId,
    graphSha256,
    graphJson: canonicalStringify(graph),
    counts,
    countsJson: JSON.stringify(counts),
    extraction,
    verification,
    openAlex,
    compilerAudit,
    citationSource,
    createdAt,
  };
}
