import {
  CLAIM_MODALITIES,
  CLAIM_POLARITIES,
  CLAIM_TYPES,
  EVIDENCE_GRAPH_SCHEMA_VERSION,
  ENTITY_COLLECTIONS,
  NORMALIZED_REGION_EXTENT_MAX,
  isClaimComparison,
  isDerivationEdge,
  isReasoningConclusion,
  type EvidenceEdge,
  type EvidenceEntity,
  type EvidenceGraph,
  type Scope,
  type Snapshot,
} from "./types.ts";
import {
  PROPOSITION_KEY_RE,
  SCOPED_CLAIM_ID_RE,
  SHA256_RE,
  SNAPSHOT_ID_RE,
  propositionKey,
  scopedClaimId,
  sha256Hex,
} from "./canonical.ts";
const ISO_UTC_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/u;
const IDENTIFIER_RE = /^[A-Za-z][A-Za-z0-9._:-]{0,127}$/u;
const SHORT_TEXT_MAX = 1_000;
const STATEMENT_MAX = 8_000;
const QUOTE_MAX = 16_000;
const RAW_VALUE_MAX = 8_000;

export class EvidenceGraphValidationError extends Error {
  readonly path: string;
  readonly code: string;

  constructor(path: string, code: string, message: string) {
    super(`${path}: ${message}`);
    this.name = "EvidenceGraphValidationError";
    this.path = path;
    this.code = code;
  }
}

export function invalid(path: string, code: string, message: string): never {
  throw new EvidenceGraphValidationError(path, code, message);
}

function object(value: unknown, path: string, allowed: readonly string[], required: readonly string[] = allowed) {
  if (!value || typeof value !== "object" || Array.isArray(value)) invalid(path, "TYPE", "must be an object");
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) invalid(path, "TYPE", "must be a plain object");
  const record = value as Record<string, unknown>;
  const allowedSet = new Set(allowed);
  for (const key of Object.keys(record)) if (!allowedSet.has(key)) invalid(`${path}.${key}`, "UNKNOWN_FIELD", "is not allowed");
  for (const key of required) if (!Object.hasOwn(record, key)) invalid(`${path}.${key}`, "MISSING_FIELD", "is required");
  return record;
}

function text(value: unknown, path: string, nullable = false, maxLength = SHORT_TEXT_MAX): string | null {
  if (nullable && value === null) return null;
  if (typeof value !== "string" || !value.trim()) invalid(path, "TYPE", "must be a non-empty string");
  if (value !== value.normalize("NFC")) invalid(path, "UNICODE", "must use NFC normalization");
  if (Array.from(value).length > maxLength) invalid(path, "LENGTH", `must contain at most ${maxLength} characters`);
  return value;
}

function identifier(value: unknown, path: string, nullable = false): string | null {
  if (nullable && value === null) return null;
  const parsed = text(value, path, false, 128) as string;
  if (!IDENTIFIER_RE.test(parsed)) invalid(path, "IDENTIFIER", "must match ^[A-Za-z][A-Za-z0-9._:-]{0,127}$");
  return parsed;
}

function statement(value: unknown, path: string, nullable = false) {
  return text(value, path, nullable, STATEMENT_MAX);
}

function numberValue(value: unknown, path: string, nullable = false): number | null {
  if (nullable && value === null) return null;
  if (typeof value !== "number" || !Number.isFinite(value)) invalid(path, "TYPE", "must be a finite number");
  return value;
}

function integer(value: unknown, path: string, nullable = false): number | null {
  const parsed = numberValue(value, path, nullable);
  if (parsed !== null && (!Number.isSafeInteger(parsed) || parsed < 0)) invalid(path, "RANGE", "must be a non-negative safe integer");
  return parsed;
}

function array(value: unknown, path: string) {
  if (!Array.isArray(value)) invalid(path, "TYPE", "must be an array");
  return value;
}

function oneOf<T extends string>(value: unknown, path: string, values: readonly T[]): T {
  if (typeof value !== "string" || !values.includes(value as T)) invalid(path, "ENUM", `must be one of ${values.join(", ")}`);
  return value as T;
}

function texts(value: unknown, path: string, nonempty = false, maxLength = SHORT_TEXT_MAX) {
  const result = array(value, path).map((item, index) => text(item, `${path}[${index}]`, false, maxLength) as string);
  if (nonempty && result.length === 0) invalid(path, "EMPTY", "must not be empty");
  if (new Set(result).size !== result.length) invalid(path, "DUPLICATE", "must not contain duplicates");
  return result;
}

function identifiers(value: unknown, path: string, nonempty = false) {
  const result = array(value, path).map((item, index) => identifier(item, `${path}[${index}]`) as string);
  if (nonempty && result.length === 0) invalid(path, "EMPTY", "must not be empty");
  if (new Set(result).size !== result.length) invalid(path, "DUPLICATE", "must not contain duplicates");
  return result;
}

export function validateScope(value: unknown, path = "$scope"): asserts value is Scope {
  const keys = ["location", "time", "formation", "sample_type", "method", "condition", "measurement", "uncertainty", "entities"] as const;
  const item = object(value, path, keys);
  for (const key of ["location", "formation", "sample_type", "method", "condition", "measurement", "entities"] as const) texts(item[key], `${path}.${key}`);
  text(item.uncertainty, `${path}.uncertainty`, true);
  if (item.time !== null) {
    const time = object(item.time, `${path}.time`, ["label", "start", "end", "unit"]);
    const label = text(time.label, `${path}.time.label`, true);
    const start = numberValue(time.start, `${path}.time.start`, true);
    const end = numberValue(time.end, `${path}.time.end`, true);
    const unit = text(time.unit, `${path}.time.unit`, true);
    if (label === null && start === null && end === null) invalid(`${path}.time`, "EMPTY", "must contain a label or numeric bound");
    if ((start !== null || end !== null) && unit === null) invalid(`${path}.time.unit`, "MISSING_UNIT", "is required for numeric bounds");
    if (start !== null && end !== null && start > end) invalid(`${path}.time`, "RANGE", "start must not exceed end");
  }
}

function validateArtifact(value: unknown, path: string) {
  const item = object(value, path, ["kind", "id", "canonical_name", "artifact_type"]);
  oneOf(item.kind, `${path}.kind`, ["artifact"] as const);
  identifier(item.id, `${path}.id`);
  text(item.canonical_name, `${path}.canonical_name`);
  oneOf(item.artifact_type, `${path}.artifact_type`, ["paper", "figure", "table", "dataset", "supplement", "other"] as const);
}

function validateArtifactVersion(value: unknown, path: string) {
  const item = object(value, path, ["kind", "id", "artifact_id", "version", "locator", "sha256", "media_type"]);
  oneOf(item.kind, `${path}.kind`, ["artifact_version"] as const);
  identifier(item.id, `${path}.id`);
  identifier(item.artifact_id, `${path}.artifact_id`);
  text(item.version, `${path}.version`);
  text(item.locator, `${path}.locator`, true);
  const hash = text(item.sha256, `${path}.sha256`) as string;
  if (!SHA256_RE.test(hash)) invalid(`${path}.sha256`, "HASH", "must be a lowercase SHA-256 digest");
  text(item.media_type, `${path}.media_type`);
}

/** The normalized-region rule shared with the Compiler's Region and BoundingBox. */
export function isValidNormalizedRegion(x: number, y: number, width: number, height: number) {
  return x >= 0 && y >= 0 && width > 0 && height > 0 && x <= 1 && y <= 1
    && x + width <= NORMALIZED_REGION_EXTENT_MAX && y + height <= NORMALIZED_REGION_EXTENT_MAX;
}

function validateRegion(value: unknown, path: string) {
  if (value === null) return;
  const item = object(value, path, ["x", "y", "width", "height", "unit"]);
  const x = numberValue(item.x, `${path}.x`) as number;
  const y = numberValue(item.y, `${path}.y`) as number;
  const width = numberValue(item.width, `${path}.width`) as number;
  const height = numberValue(item.height, `${path}.height`) as number;
  const unit = oneOf(item.unit, `${path}.unit`, ["normalized", "pixel"] as const);
  if (x < 0 || y < 0 || width <= 0 || height <= 0) invalid(path, "RANGE", "coordinates must be non-negative and dimensions positive");
  // The extent tolerance is shared with the Compiler (GRF-6): float rounding at
  // the page edge must not make a Compiler-accepted region fail at import.
  if (unit === "normalized" && !isValidNormalizedRegion(x, y, width, height)) {
    invalid(path, "RANGE", "normalized region must fit within [0, 1]");
  }
}

function validateSelector(value: unknown, path: string, quoteOnlyAllowed = false) {
  const keys = ["page", "section", "paragraph", "table", "row_start", "row_end", "row_range_label", "column_names", "figure", "series", "char_start", "char_end", "region", "region_label", "dataset_path"] as const;
  const item = object(value, path, keys);
  const page = integer(item.page, `${path}.page`, true);
  const section = text(item.section, `${path}.section`, true);
  const paragraph = integer(item.paragraph, `${path}.paragraph`, true);
  const table = text(item.table, `${path}.table`, true);
  const rowStart = integer(item.row_start, `${path}.row_start`, true);
  const rowEnd = integer(item.row_end, `${path}.row_end`, true);
  const rowLabel = text(item.row_range_label, `${path}.row_range_label`, true);
  const columns = texts(item.column_names, `${path}.column_names`);
  const figure = text(item.figure, `${path}.figure`, true);
  const series = text(item.series, `${path}.series`, true);
  const charStart = integer(item.char_start, `${path}.char_start`, true);
  const charEnd = integer(item.char_end, `${path}.char_end`, true);
  validateRegion(item.region, `${path}.region`);
  const regionLabel = text(item.region_label, `${path}.region_label`, true);
  const datasetPath = text(item.dataset_path, `${path}.dataset_path`, true);
  if (page !== null && page < 1) invalid(`${path}.page`, "RANGE", "must be at least 1");
  if (paragraph !== null && paragraph < 1) invalid(`${path}.paragraph`, "RANGE", "must be at least 1");
  if (rowStart !== null && rowStart < 1) invalid(`${path}.row_start`, "RANGE", "must be at least 1");
  if ((rowStart === null) !== (rowEnd === null)) invalid(path, "RANGE_PAIR", "row_start and row_end must be supplied together");
  if (rowStart !== null && rowEnd !== null && rowStart > rowEnd) invalid(path, "RANGE", "row_start must not exceed row_end");
  if ((charStart === null) !== (charEnd === null)) invalid(path, "RANGE_PAIR", "char_start and char_end must be supplied together");
  if (charStart !== null && charEnd !== null && charStart >= charEnd) invalid(path, "RANGE", "char_start must be less than char_end");
  if (!quoteOnlyAllowed && [page, section, paragraph, table, rowStart, rowLabel, figure, series, charStart, item.region, regionLabel, datasetPath].every((part) => part === null) && columns.length === 0) invalid(path, "EMPTY", "must contain at least one locator");
}

function validateAnchor(value: unknown, path: string) {
  const item = object(value, path, ["kind", "id", "artifact_version_id", "anchor_type", "selector", "quote", "quote_sha256"]);
  oneOf(item.kind, `${path}.kind`, ["source_anchor"] as const);
  identifier(item.id, `${path}.id`);
  identifier(item.artifact_version_id, `${path}.artifact_version_id`);
  oneOf(item.anchor_type, `${path}.anchor_type`, ["text", "figure", "table", "dataset", "metadata", "citation"] as const);
  const quote = text(item.quote, `${path}.quote`, true, QUOTE_MAX);
  validateSelector(item.selector, `${path}.selector`, quote !== null);
  const hash = text(item.quote_sha256, `${path}.quote_sha256`, true);
  if ((quote === null) !== (hash === null)) invalid(path, "QUOTE_PAIR", "quote and quote_sha256 must be supplied together");
  if (hash !== null && !SHA256_RE.test(hash)) invalid(`${path}.quote_sha256`, "HASH", "must be a lowercase SHA-256 digest");
}

function validateAxis(value: unknown, path: string) {
  const item = object(value, path, ["id", "direction", "label", "unit", "scale"]);
  identifier(item.id, `${path}.id`);
  oneOf(item.direction, `${path}.direction`, ["x", "y"] as const);
  text(item.label, `${path}.label`);
  text(item.unit, `${path}.unit`, true);
  oneOf(item.scale, `${path}.scale`, ["linear", "log", "categorical"] as const);
}

export function validateStructuredVisualEvidence(value: unknown, path = "$visual") {
  const item = object(value, path, ["kind", "id", "anchor_id", "visual_kind", "axes", "series", "cells", "features"]);
  oneOf(item.kind, `${path}.kind`, ["structured_visual_evidence"] as const);
  identifier(item.id, `${path}.id`);
  identifier(item.anchor_id, `${path}.anchor_id`);
  const visualKind = oneOf(item.visual_kind, `${path}.visual_kind`, ["figure", "table"] as const);
  const axes = array(item.axes, `${path}.axes`);
  const series = array(item.series, `${path}.series`);
  const cells = array(item.cells, `${path}.cells`);
  const features = array(item.features, `${path}.features`);
  axes.forEach((raw, index) => validateAxis(raw, `${path}.axes[${index}]`));
  const axisIds = new Set(axes.map((raw) => (raw as Record<string, unknown>).id as string));
  const axisDirections = new Map(axes.map((raw) => {
    const axis = raw as Record<string, unknown>;
    return [axis.id as string, axis.direction as string];
  }));
  if (axisIds.size !== axes.length) invalid(`${path}.axes`, "DUPLICATE", "axis ids must be unique");
  const seriesIds = new Set<string>();
  series.forEach((raw, index) => {
    const part = object(raw, `${path}.series[${index}]`, ["id", "label", "x_axis_id", "y_axis_id", "points"]);
    const id = identifier(part.id, `${path}.series[${index}].id`) as string;
    if (seriesIds.has(id)) invalid(`${path}.series[${index}].id`, "DUPLICATE", "series id is duplicated");
    seriesIds.add(id);
    text(part.label, `${path}.series[${index}].label`);
    const xAxis = identifier(part.x_axis_id, `${path}.series[${index}].x_axis_id`) as string;
    const yAxis = identifier(part.y_axis_id, `${path}.series[${index}].y_axis_id`) as string;
    if (!axisIds.has(xAxis) || !axisIds.has(yAxis)) invalid(`${path}.series[${index}]`, "REFERENCE", "series axes must exist in the same visual object");
    if (axisDirections.get(xAxis) !== "x" || axisDirections.get(yAxis) !== "y") invalid(`${path}.series[${index}]`, "AXIS_DIRECTION", "x_axis_id and y_axis_id must reference matching axis directions");
    array(part.points, `${path}.series[${index}].points`).forEach((point, pointIndex) => {
      const coordinate = object(point, `${path}.series[${index}].points[${pointIndex}]`, ["x", "y", "uncertainty"]);
      for (const key of ["x", "y"] as const) {
        if (typeof coordinate[key] === "number") numberValue(coordinate[key], `${path}.series[${index}].points[${pointIndex}].${key}`);
        else text(coordinate[key], `${path}.series[${index}].points[${pointIndex}].${key}`);
      }
      const uncertainty = numberValue(coordinate.uncertainty, `${path}.series[${index}].points[${pointIndex}].uncertainty`, true);
      if (uncertainty !== null && uncertainty < 0) invalid(`${path}.series[${index}].points[${pointIndex}].uncertainty`, "RANGE", "must not be negative");
    });
  });
  const cellKeys = new Set<string>();
  cells.forEach((raw, index) => {
    const cell = object(raw, `${path}.cells[${index}]`, ["row", "column", "raw_value", "numeric_value", "unit"]);
    const row = integer(cell.row, `${path}.cells[${index}].row`) as number;
    if (row < 1) invalid(`${path}.cells[${index}].row`, "RANGE", "must be at least 1");
    const column = text(cell.column, `${path}.cells[${index}].column`) as string;
    const key = `${row}:${column}`;
    if (cellKeys.has(key)) invalid(`${path}.cells[${index}]`, "DUPLICATE", "table cell coordinate is duplicated");
    cellKeys.add(key);
    text(cell.raw_value, `${path}.cells[${index}].raw_value`, false, RAW_VALUE_MAX);
    numberValue(cell.numeric_value, `${path}.cells[${index}].numeric_value`, true);
    text(cell.unit, `${path}.cells[${index}].unit`, true);
  });
  features.forEach((raw, index) => {
    const feature = object(raw, `${path}.features[${index}]`, ["kind", "description", "series_id", "value", "unit"]);
    oneOf(feature.kind, `${path}.features[${index}].kind`, ["trend", "peak", "trough", "plateau", "change_point", "uncertainty"] as const);
    statement(feature.description, `${path}.features[${index}].description`);
    const seriesId = identifier(feature.series_id, `${path}.features[${index}].series_id`, true);
    if (seriesId !== null && !seriesIds.has(seriesId)) invalid(`${path}.features[${index}].series_id`, "REFERENCE", "series does not exist");
    numberValue(feature.value, `${path}.features[${index}].value`, true);
    text(feature.unit, `${path}.features[${index}].unit`, true);
  });
  if (visualKind === "figure" && (series.length === 0 || cells.length !== 0)) invalid(path, "VISUAL_SHAPE", "figure evidence requires series and forbids cells");
  if (visualKind === "table" && (cells.length === 0 || axes.length !== 0 || series.length !== 0)) invalid(path, "VISUAL_SHAPE", "table evidence requires cells and forbids axes or series");
}

function validateObservation(value: unknown, path: string) {
  const item = object(value, path, ["kind", "id", "statement", "observation_type", "anchor_ids", "scope", "value"]);
  oneOf(item.kind, `${path}.kind`, ["observation"] as const);
  identifier(item.id, `${path}.id`);
  statement(item.statement, `${path}.statement`);
  oneOf(item.observation_type, `${path}.observation_type`, ["measurement", "trend", "pattern", "comparison", "correlation", "source_assertion"] as const);
  identifiers(item.anchor_ids, `${path}.anchor_ids`, true);
  validateScope(item.scope, `${path}.scope`);
  if (item.value !== null) {
    const measured = object(item.value, `${path}.value`, ["raw", "numeric", "comparator", "unit", "uncertainty"]);
    text(measured.raw, `${path}.value.raw`, false, RAW_VALUE_MAX);
    numberValue(measured.numeric, `${path}.value.numeric`, true);
    if (measured.comparator !== null) oneOf(measured.comparator, `${path}.value.comparator`, ["<", "<=", ">", ">="] as const);
    text(measured.unit, `${path}.value.unit`, true);
    const uncertainty = numberValue(measured.uncertainty, `${path}.value.uncertainty`, true);
    if (uncertainty !== null && uncertainty < 0) invalid(`${path}.value.uncertainty`, "RANGE", "must not be negative");
  }
}

function validateAssumption(value: unknown, path: string) {
  const item = object(value, path, ["kind", "id", "statement", "assumption_type", "explicit", "anchor_ids", "scope"]);
  oneOf(item.kind, `${path}.kind`, ["assumption"] as const);
  identifier(item.id, `${path}.id`);
  statement(item.statement, `${path}.statement`);
  oneOf(item.assumption_type, `${path}.assumption_type`, ["background_knowledge", "methodological", "measurement", "statistical", "causal", "scope", "generalization"] as const);
  if (typeof item.explicit !== "boolean") invalid(`${path}.explicit`, "TYPE", "must be a boolean");
  identifiers(item.anchor_ids, `${path}.anchor_ids`);
  validateScope(item.scope, `${path}.scope`);
}

function validateReasoning(value: unknown, path: string) {
  const item = object(value, path, ["kind", "id", "statement", "reasoning_type", "anchor_ids", "scope", "limitations"]);
  oneOf(item.kind, `${path}.kind`, ["reasoning"] as const);
  identifier(item.id, `${path}.id`);
  statement(item.statement, `${path}.statement`);
  oneOf(item.reasoning_type, `${path}.reasoning_type`, ["deductive", "inductive", "abductive", "causal_inference", "statistical_inference", "analogy", "synthesis", "other"] as const);
  identifiers(item.anchor_ids, `${path}.anchor_ids`);
  validateScope(item.scope, `${path}.scope`);
  texts(item.limitations, `${path}.limitations`);
}

function validateClaim(value: unknown, path: string) {
  const keys = ["kind", "id", "source_id", "proposition", "claim_type", "polarity", "qualifiers", "anchor_ids", "proposition_key", "scoped_claim_id", "scope", "modality"] as const;
  const item = object(value, path, keys);
  oneOf(item.kind, `${path}.kind`, ["atomic_claim"] as const);
  const id = identifier(item.id, `${path}.id`) as string;
  identifier(item.source_id, `${path}.source_id`, true);
  statement(item.proposition, `${path}.proposition`);
  oneOf(item.claim_type, `${path}.claim_type`, CLAIM_TYPES);
  oneOf(item.polarity, `${path}.polarity`, CLAIM_POLARITIES);
  texts(item.qualifiers, `${path}.qualifiers`);
  identifiers(item.anchor_ids, `${path}.anchor_ids`);
  const proposition = text(item.proposition_key, `${path}.proposition_key`) as string;
  const scoped = text(item.scoped_claim_id, `${path}.scoped_claim_id`) as string;
  if (!PROPOSITION_KEY_RE.test(proposition)) invalid(`${path}.proposition_key`, "IDENTITY", "must be a proposition SHA-256 identity");
  if (!SCOPED_CLAIM_ID_RE.test(scoped) || id !== scoped) invalid(`${path}.scoped_claim_id`, "IDENTITY", "must be a scoped claim SHA-256 identity equal to id");
  validateScope(item.scope, `${path}.scope`);
  oneOf(item.modality, `${path}.modality`, CLAIM_MODALITIES);
}

function validateEdge(value: unknown, path: string) {
  const item = object(value, path, ["kind", "id", "relation", "source_id", "target_id", "rationale", "confidence", "scope_overlap"]);
  oneOf(item.kind, `${path}.kind`, ["edge"] as const);
  identifier(item.id, `${path}.id`);
  oneOf(item.relation, `${path}.relation`, ["grounds", "premise_for", "assumption_for", "infers", "equivalent_to", "supports", "contradicts", "refines", "qualifies", "supplements"] as const);
  const source = identifier(item.source_id, `${path}.source_id`);
  const target = identifier(item.target_id, `${path}.target_id`);
  if (source === target) invalid(path, "SELF_EDGE", "source_id and target_id must differ");
  statement(item.rationale, `${path}.rationale`, true);
  const confidence = numberValue(item.confidence, `${path}.confidence`, true);
  if (confidence !== null && (confidence < 0 || confidence > 1)) invalid(`${path}.confidence`, "RANGE", "must be between 0 and 1");
  if (item.scope_overlap !== null) oneOf(item.scope_overlap, `${path}.scope_overlap`, ["partial", "source_within_target", "target_within_source", "equivalent"] as const);
}

function validateAssessment(value: unknown, path: string) {
  const keys = ["kind", "id", "target_claim_id", "verdict", "basis_edge_ids", "evidence_ids", "rationale", "limitations", "confidence", "assessor", "created_at"] as const;
  const item = object(value, path, keys);
  oneOf(item.kind, `${path}.kind`, ["assessment"] as const);
  identifier(item.id, `${path}.id`);
  identifier(item.target_claim_id, `${path}.target_claim_id`);
  oneOf(item.verdict, `${path}.verdict`, ["supported", "contradicted", "qualified", "uncertain", "insufficient_evidence"] as const);
  const basis = identifiers(item.basis_edge_ids, `${path}.basis_edge_ids`);
  const evidence = identifiers(item.evidence_ids, `${path}.evidence_ids`);
  if (basis.length === 0 && evidence.length === 0) invalid(path, "EMPTY_ASSESSMENT", "must cite at least one evidence entity or path edge");
  statement(item.rationale, `${path}.rationale`);
  texts(item.limitations, `${path}.limitations`);
  const confidence = numberValue(item.confidence, `${path}.confidence`) as number;
  if (confidence < 0 || confidence > 1) invalid(`${path}.confidence`, "RANGE", "must be between 0 and 1");
  const assessor = object(item.assessor, `${path}.assessor`, ["source", "name", "model"]);
  const source = oneOf(assessor.source, `${path}.assessor.source`, ["human", "model"] as const);
  text(assessor.name, `${path}.assessor.name`);
  const model = text(assessor.model, `${path}.assessor.model`, true);
  if (source === "model" && model === null) invalid(`${path}.assessor.model`, "MODEL_REQUIRED", "is required for model assessments");
  const createdAt = text(item.created_at, `${path}.created_at`) as string;
  if (!ISO_UTC_RE.test(createdAt) || !Number.isFinite(Date.parse(createdAt))) invalid(`${path}.created_at`, "DATETIME", "must be an ISO-8601 UTC timestamp");
}

function validateSnapshot(value: unknown, path: string) {
  const keys = ["kind", "id", "parent_snapshot_id", "entity_ids", "edge_ids", "assessment_ids", "content_sha256", "created_at", "generator"] as const;
  const item = object(value, path, keys);
  oneOf(item.kind, `${path}.kind`, ["snapshot"] as const);
  const id = identifier(item.id, `${path}.id`) as string;
  if (!SNAPSHOT_ID_RE.test(id)) invalid(`${path}.id`, "IDENTITY", "must be a snapshot SHA-256 identity");
  identifier(item.parent_snapshot_id, `${path}.parent_snapshot_id`, true);
  identifiers(item.entity_ids, `${path}.entity_ids`);
  identifiers(item.edge_ids, `${path}.edge_ids`);
  identifiers(item.assessment_ids, `${path}.assessment_ids`);
  const hash = text(item.content_sha256, `${path}.content_sha256`) as string;
  if (!SHA256_RE.test(hash) || id !== `snapshot:sha256:${hash}`) invalid(`${path}.content_sha256`, "IDENTITY", "must match snapshot id");
  const createdAt = text(item.created_at, `${path}.created_at`) as string;
  if (!ISO_UTC_RE.test(createdAt) || !Number.isFinite(Date.parse(createdAt))) invalid(`${path}.created_at`, "DATETIME", "must be an ISO-8601 UTC timestamp");
  const generator = object(item.generator, `${path}.generator`, ["source", "name", "version"]);
  oneOf(generator.source, `${path}.generator.source`, ["human", "extractor", "verifier", "migration"] as const);
  text(generator.name, `${path}.generator.name`);
  text(generator.version, `${path}.generator.version`);
}

export function allEntities(graph: EvidenceGraph) {
  const entities: EvidenceEntity[] = [];
  for (const collection of ENTITY_COLLECTIONS) entities.push(...graph[collection] as EvidenceEntity[]);
  return entities;
}

export function entityMap(graph: EvidenceGraph) {
  return new Map(allEntities(graph).map((entity) => [entity.id, entity]));
}

function uniqueIds(items: Array<{ id: string }>, path: string) {
  const seen = new Set<string>();
  items.forEach((item, index) => {
    if (seen.has(item.id)) invalid(`${path}[${index}].id`, "DUPLICATE_ID", `duplicate id ${item.id}`);
    seen.add(item.id);
  });
}

function endpointAllowed(edge: EvidenceEdge, source: EvidenceEntity, target: EvidenceEntity) {
  if (edge.relation === "grounds") return (source.kind === "source_anchor" || source.kind === "structured_visual_evidence") && target.kind === "observation";
  if (edge.relation === "premise_for") return ["observation", "reasoning", "atomic_claim"].includes(source.kind) && target.kind === "reasoning";
  if (edge.relation === "assumption_for") return source.kind === "assumption" && target.kind === "reasoning";
  if (edge.relation === "infers") return source.kind === "reasoning" && target.kind === "atomic_claim";
  // A Reasoning step may contradict or qualify a Claim; keep that stance.
  if ((edge.relation === "contradicts" || edge.relation === "qualifies") && source.kind === "reasoning") return target.kind === "atomic_claim";
  return source.kind === "atomic_claim" && target.kind === "atomic_claim";
}

function derivationDag(graph: EvidenceGraph, entities: Map<string, EvidenceEntity>) {
  const adjacency = new Map<string, string[]>();
  for (const edge of graph.edges.filter((item) => isDerivationEdge(item, entities))) adjacency.set(edge.source_id, [...(adjacency.get(edge.source_id) ?? []), edge.target_id]);
  const state = new Map<string, 0 | 1 | 2>();
  const visit = (id: string, stack: string[]) => {
    if (state.get(id) === 1) invalid("$.edges", "DERIVATION_CYCLE", `derivation cycle detected: ${[...stack, id].join(" -> ")}`);
    if (state.get(id) === 2) return;
    state.set(id, 1);
    for (const next of adjacency.get(id) ?? []) visit(next, [...stack, id]);
    state.set(id, 2);
  };
  for (const id of adjacency.keys()) visit(id, []);
}

function reverseReachable(graph: EvidenceGraph, rootClaimId: string, entities: Map<string, EvidenceEntity>) {
  const edgeIds = new Set<string>();
  const entityIds = new Set<string>();
  const queue = [rootClaimId];
  while (queue.length) {
    const id = queue.shift() as string;
    if (entityIds.has(id)) continue;
    entityIds.add(id);
    const entity = entities.get(id);
    if (entity?.kind === "artifact_version") queue.push(entity.artifact_id);
    if (entity?.kind === "source_anchor") queue.push(entity.artifact_version_id);
    if (entity?.kind === "structured_visual_evidence") queue.push(entity.anchor_id);
    if (entity?.kind === "observation" || entity?.kind === "assumption" || entity?.kind === "reasoning" || entity?.kind === "atomic_claim") {
      queue.push(...entity.anchor_ids);
    }
    for (const edge of graph.edges) {
      if (edge.target_id === id && (isDerivationEdge(edge, entities) || isClaimComparison(edge, entities))) {
        edgeIds.add(edge.id);
        queue.push(edge.source_id);
      }
      if (edge.relation === "equivalent_to" && edge.source_id === id) {
        edgeIds.add(edge.id);
        queue.push(edge.target_id);
      }
    }
  }
  return { edgeIds, entityIds };
}

function validateReferences(graph: EvidenceGraph) {
  const entities = allEntities(graph);
  uniqueIds(entities, "$.entities");
  uniqueIds(graph.edges, "$.edges");
  uniqueIds(graph.assessments, "$.assessments");
  uniqueIds(graph.snapshots, "$.snapshots");
  const byId = entityMap(graph);
  const artifactIds = new Set(graph.artifacts.map((item) => item.id));
  const versionIds = new Set(graph.artifact_versions.map((item) => item.id));
  const anchorIds = new Set(graph.anchors.map((item) => item.id));
  const edgeIds = new Set(graph.edges.map((item) => item.id));
  const assessmentIds = new Set(graph.assessments.map((item) => item.id));
  const snapshotIds = new Set(graph.snapshots.map((item) => item.id));

  graph.artifact_versions.forEach((item, index) => { if (!artifactIds.has(item.artifact_id)) invalid(`$.artifact_versions[${index}].artifact_id`, "REFERENCE", "artifact does not exist"); });
  graph.anchors.forEach((item, index) => { if (!versionIds.has(item.artifact_version_id)) invalid(`$.anchors[${index}].artifact_version_id`, "REFERENCE", "artifact version does not exist"); });
  const artifactsWithVersions = new Set(graph.artifact_versions.map((item) => item.artifact_id));
  graph.artifacts.forEach((item, index) => {
    if (!artifactsWithVersions.has(item.id)) invalid(`$.artifacts[${index}]`, "ORPHAN_ARTIFACT", "requires at least one ArtifactVersion");
  });
  const versionsWithAnchors = new Set(graph.anchors.map((item) => item.artifact_version_id));
  graph.artifact_versions.forEach((item, index) => {
    if (!versionsWithAnchors.has(item.id)) invalid(`$.artifact_versions[${index}]`, "ORPHAN_ARTIFACT_VERSION", "requires at least one SourceAnchor");
  });
  graph.visual_evidence.forEach((item, index) => { if (!anchorIds.has(item.anchor_id)) invalid(`$.visual_evidence[${index}].anchor_id`, "REFERENCE", "anchor does not exist"); });
  for (const [collection, values] of [
    ["observations", graph.observations],
    ["assumptions", graph.assumptions],
    ["reasoning", graph.reasoning],
    ["claims", graph.claims],
  ] as const) {
    values.forEach((item, index) => item.anchor_ids.forEach((id, member) => {
      if (!anchorIds.has(id)) invalid(`$.${collection}[${index}].anchor_ids[${member}]`, "REFERENCE", "anchor does not exist");
    }));
  }
  graph.edges.forEach((edge, index) => {
    const source = byId.get(edge.source_id);
    const target = byId.get(edge.target_id);
    if (!source) invalid(`$.edges[${index}].source_id`, "REFERENCE", "source entity does not exist");
    if (!target) invalid(`$.edges[${index}].target_id`, "REFERENCE", "target entity does not exist");
    if (!endpointAllowed(edge, source, target)) invalid(`$.edges[${index}]`, "ENDPOINT_TYPE", `${edge.relation} cannot connect ${source.kind} to ${target.kind}`);
    const comparison = isClaimComparison(edge, byId);
    if (comparison && edge.scope_overlap === null) invalid(`$.edges[${index}].scope_overlap`, "SCOPE", "claim comparison requires explicit scope overlap");
    if (!comparison && edge.scope_overlap !== null) invalid(`$.edges[${index}].scope_overlap`, "SCOPE", "non-comparison edge must use null scope_overlap");
    if (edge.relation === "equivalent_to" && edge.scope_overlap !== "equivalent") invalid(`$.edges[${index}].scope_overlap`, "SCOPE", "equivalent claims require equivalent scope");
  });

  const incoming = new Map<string, EvidenceEdge[]>();
  const outgoing = new Map<string, EvidenceEdge[]>();
  for (const edge of graph.edges) {
    incoming.set(edge.target_id, [...(incoming.get(edge.target_id) ?? []), edge]);
    outgoing.set(edge.source_id, [...(outgoing.get(edge.source_id) ?? []), edge]);
  }
  graph.observations.forEach((item, index) => {
    const grounds = (incoming.get(item.id) ?? []).filter((edge) => edge.relation === "grounds");
    if (grounds.length === 0) invalid(`$.observations[${index}]`, "UNGROUNDED_OBSERVATION", "requires an incoming grounds edge");
    const premiseCount = (outgoing.get(item.id) ?? []).filter((edge) => edge.relation === "premise_for").length;
    if (premiseCount !== 1) {
      invalid(`$.observations[${index}]`, "OBSERVATION_PATH_OWNERSHIP", "must feed exactly one reasoning path");
    }
  });
  graph.reasoning.forEach((item, index) => {
    if (!(incoming.get(item.id) ?? []).some((edge) => edge.relation === "premise_for")) invalid(`$.reasoning[${index}]`, "MISSING_PREMISE", "requires an incoming premise_for edge");
    const queue = [item.id];
    const visited = new Set<string>();
    let reachesClaim = false;
    while (queue.length) {
      const current = queue.pop() as string;
      if (visited.has(current)) continue;
      visited.add(current);
      for (const edge of outgoing.get(current) ?? []) {
        if (isReasoningConclusion(edge, byId)) reachesClaim = true;
        else if (edge.relation === "premise_for" && byId.get(edge.target_id)?.kind === "reasoning") queue.push(edge.target_id);
      }
    }
    if (!reachesClaim) invalid(`$.reasoning[${index}]`, "MISSING_CONCLUSION", "must reach at least one atomic Claim through the reasoning DAG");
  });
  graph.claims.forEach((item, index) => {
    if (!(incoming.get(item.id) ?? []).some((edge) => isReasoningConclusion(edge, byId))) invalid(`$.claims[${index}]`, "UNSUPPORTED_CLAIM", "requires an incoming infers, contradicts or qualifies edge from Reasoning");
  });

  graph.assessments.forEach((item, index) => {
    const target = byId.get(item.target_claim_id);
    if (!target || target.kind !== "atomic_claim") invalid(`$.assessments[${index}].target_claim_id`, "REFERENCE", "must identify an atomic claim");
    const reachable = reverseReachable(graph, item.target_claim_id, byId);
    item.basis_edge_ids.forEach((id, member) => {
      if (!edgeIds.has(id)) invalid(`$.assessments[${index}].basis_edge_ids[${member}]`, "REFERENCE", "edge does not exist");
      if (!reachable.edgeIds.has(id)) invalid(`$.assessments[${index}].basis_edge_ids[${member}]`, "EVIDENCE_PATH", "edge is not on the target claim's Evidence Path");
    });
    item.evidence_ids.forEach((id, member) => {
      if (!byId.has(id)) invalid(`$.assessments[${index}].evidence_ids[${member}]`, "REFERENCE", "entity does not exist");
      if (!reachable.entityIds.has(id)) invalid(`$.assessments[${index}].evidence_ids[${member}]`, "EVIDENCE_PATH", "entity is not on the target claim's Evidence Path");
    });
  });

  graph.snapshots.forEach((snapshot, index) => {
    if (snapshot.parent_snapshot_id !== null && !snapshotIds.has(snapshot.parent_snapshot_id)) invalid(`$.snapshots[${index}].parent_snapshot_id`, "REFERENCE", "parent snapshot does not exist");
    snapshot.entity_ids.forEach((id, member) => { if (!byId.has(id)) invalid(`$.snapshots[${index}].entity_ids[${member}]`, "REFERENCE", "entity does not exist"); });
    snapshot.edge_ids.forEach((id, member) => { if (!edgeIds.has(id)) invalid(`$.snapshots[${index}].edge_ids[${member}]`, "REFERENCE", "edge does not exist"); });
    snapshot.assessment_ids.forEach((id, member) => { if (!assessmentIds.has(id)) invalid(`$.snapshots[${index}].assessment_ids[${member}]`, "REFERENCE", "assessment does not exist"); });
  });
  const parent = new Map(graph.snapshots.map((item) => [item.id, item.parent_snapshot_id]));
  for (const snapshot of graph.snapshots) {
    const seen = new Set<string>();
    let current: string | null = snapshot.id;
    while (current !== null) {
      if (seen.has(current)) invalid("$.snapshots", "SNAPSHOT_CYCLE", `snapshot parent cycle includes ${current}`);
      seen.add(current);
      current = parent.get(current) ?? null;
    }
  }
  derivationDag(graph, byId);
}

/** Strict shape, unknown-field, reference, endpoint-type and DAG validation. */
export function validateEvidenceGraph(value: unknown): EvidenceGraph {
  const keys = ["schema_version", "paper_id", "artifacts", "artifact_versions", "anchors", "visual_evidence", "observations", "assumptions", "reasoning", "claims", "edges", "assessments", "snapshots"] as const;
  const graph = object(value, "$", keys);
  if (graph.schema_version !== EVIDENCE_GRAPH_SCHEMA_VERSION) invalid("$.schema_version", "SCHEMA_VERSION", `must equal ${EVIDENCE_GRAPH_SCHEMA_VERSION}`);
  identifier(graph.paper_id, "$.paper_id");
  const validators: Record<string, (item: unknown, path: string) => void> = {
    artifacts: validateArtifact,
    artifact_versions: validateArtifactVersion,
    anchors: validateAnchor,
    visual_evidence: validateStructuredVisualEvidence,
    observations: validateObservation,
    assumptions: validateAssumption,
    reasoning: validateReasoning,
    claims: validateClaim,
    edges: validateEdge,
    assessments: validateAssessment,
    snapshots: validateSnapshot,
  };
  for (const [collection, validator] of Object.entries(validators)) array(graph[collection], `$.${collection}`).forEach((item, index) => validator(item, `$.${collection}[${index}]`));
  const typed = value as EvidenceGraph;
  validateReferences(typed);
  return typed;
}

export type SnapshotMembers = Pick<Snapshot, "parent_snapshot_id" | "entity_ids" | "edge_ids" | "assessment_ids">;

/** Verify quote, claim and snapshot hashes after synchronous graph validation. */
export async function verifyEvidenceGraphIntegrity(
  value: unknown,
  snapshotHash: (graph: EvidenceGraph, snapshot: SnapshotMembers) => Promise<string>,
) {
  const graph = validateEvidenceGraph(value);
  for (let index = 0; index < graph.anchors.length; index += 1) {
    const anchor = graph.anchors[index];
    if (anchor.quote !== null && await sha256Hex(anchor.quote) !== anchor.quote_sha256) invalid(`$.anchors[${index}].quote_sha256`, "HASH_MISMATCH", "does not match quote bytes");
  }
  for (let index = 0; index < graph.claims.length; index += 1) {
    const claim = graph.claims[index];
    const expectedProposition = await propositionKey({ proposition: claim.proposition, claim_type: claim.claim_type, polarity: claim.polarity, qualifiers: claim.qualifiers });
    if (claim.proposition_key !== expectedProposition) invalid(`$.claims[${index}].proposition_key`, "IDENTITY_MISMATCH", "does not match canonical proposition content");
    const expectedScoped = await scopedClaimId(claim.proposition_key, claim.scope);
    if (claim.scoped_claim_id !== expectedScoped) invalid(`$.claims[${index}].scoped_claim_id`, "IDENTITY_MISMATCH", "does not match proposition_key plus scope");
  }
  for (let index = 0; index < graph.snapshots.length; index += 1) {
    const snapshot = graph.snapshots[index];
    if (snapshot.content_sha256 !== await snapshotHash(graph, snapshot)) invalid(`$.snapshots[${index}].content_sha256`, "HASH_MISMATCH", "does not match frozen members");
  }
  return graph;
}
