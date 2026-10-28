import { validateStructuredVisualEvidence } from "../evidence-graph/index.ts";
import {
  DOCUMENT_EXTRACTION_SCHEMA_VERSION,
  DOCUMENT_METADATA_FIELDS,
  DOCUMENT_METADATA_FIELD_SOURCES,
  type DocumentExtraction,
} from "./types.ts";

const VERSION_ID_RE = /^artifact-version:sha256:([a-f0-9]{64})$/u;
const IDENTIFIER_RE = /^[A-Za-z][A-Za-z0-9._:-]{0,127}$/u;
const MAX_MARKDOWN_CHARS = 8_000_000;
const MAX_VISUALS = 4_096;

function fail(path: string, message: string): never {
  throw new TypeError(`${path} ${message}`);
}

function record(value: unknown, path: string, keys: readonly string[]) {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail(path, "must be an object");
  const item = value as Record<string, unknown>;
  const actual = Object.keys(item);
  if (actual.length !== keys.length || actual.some((key) => !keys.includes(key))) {
    fail(path, "has an invalid shape");
  }
  return item;
}

function text(value: unknown, path: string, maximum: number, allowEmpty = false) {
  if (typeof value !== "string" || value.length > maximum || (!allowEmpty && !value.trim())) {
    fail(path, `must be ${allowEmpty ? "a" : "a non-empty"} string of at most ${maximum} characters`);
  }
  return value as string;
}

function count(value: unknown, path: string) {
  if (!Number.isSafeInteger(value) || Number(value) < 0) fail(path, "must be a non-negative integer");
  return Number(value);
}

function oneOf<T extends string>(value: unknown, path: string, allowed: readonly T[]) {
  if (typeof value !== "string" || !allowed.includes(value as T)) fail(path, `must be one of ${allowed.join(", ")}`);
  return value as T;
}

function metadata(value: unknown, path: string, sources = false) {
  const item = record(value, path, DOCUMENT_METADATA_FIELDS);
  for (const key of DOCUMENT_METADATA_FIELDS) {
    if (sources) oneOf(item[key], `${path}.${key}`, DOCUMENT_METADATA_FIELD_SOURCES);
    else text(item[key], `${path}.${key}`, key === "abstract" ? 100_000 : 10_000, true);
  }
}

function region(value: unknown, path: string) {
  if (value === null) return;
  const item = record(value, path, ["x", "y", "width", "height"]);
  const values = ["x", "y", "width", "height"].map((key) => {
    const part = item[key];
    if (typeof part !== "number" || !Number.isFinite(part)) fail(`${path}.${key}`, "must be a finite number");
    return part;
  });
  const [x, y, width, height] = values;
  if (x < 0 || y < 0 || width <= 0 || height <= 0 || x + width > 1.000001 || y + height > 1.000001) {
    fail(path, "must fit inside the normalized page");
  }
}

export function parseDocumentExtraction(value: unknown): DocumentExtraction {
  const root = record(value, "$", [
    "schema_version",
    "artifact_version_id",
    "artifact_sha256",
    "parser",
    "metadata",
    "field_sources",
    "stats",
    "markdown",
    "visuals",
    "warnings",
  ]);
  if (root.schema_version !== DOCUMENT_EXTRACTION_SCHEMA_VERSION) fail("$.schema_version", "is unsupported");
  const versionId = text(root.artifact_version_id, "$.artifact_version_id", 128);
  const match = VERSION_ID_RE.exec(versionId);
  const sha256 = text(root.artifact_sha256, "$.artifact_sha256", 64);
  if (!match || match[1] !== sha256) fail("$.artifact_version_id", "does not match artifact_sha256");

  const parser = record(root.parser, "$.parser", ["name", "version", "backend", "method"]);
  if (parser.name !== "mineru" || parser.backend !== "pipeline" || parser.method !== "auto") {
    fail("$.parser", "must describe the supported MinerU pipeline");
  }
  text(parser.version, "$.parser.version", 1_000);
  metadata(root.metadata, "$.metadata");
  metadata(root.field_sources, "$.field_sources", true);

  const stats = record(root.stats, "$.stats", [
    "page_count",
    "text_block_count",
    "figure_count",
    "table_count",
    "image_count",
    "visual_count",
  ]);
  const pageCount = count(stats.page_count, "$.stats.page_count");
  if (pageCount < 1) fail("$.stats.page_count", "must be at least 1");
  count(stats.text_block_count, "$.stats.text_block_count");
  const figureCount = count(stats.figure_count, "$.stats.figure_count");
  const tableCount = count(stats.table_count, "$.stats.table_count");
  const imageCount = count(stats.image_count, "$.stats.image_count");
  const visualCount = count(stats.visual_count, "$.stats.visual_count");
  text(root.markdown, "$.markdown", MAX_MARKDOWN_CHARS, true);

  if (!Array.isArray(root.visuals) || root.visuals.length > MAX_VISUALS) fail("$.visuals", `must contain at most ${MAX_VISUALS} records`);
  const seenBlocks = new Set<string>();
  let actualFigures = 0;
  let actualTables = 0;
  let actualImages = 0;
  root.visuals.forEach((raw, index) => {
    const path = `$.visuals[${index}]`;
    const item = record(raw, path, ["block_id", "kind", "source_kind", "page", "region", "label", "caption", "footnote", "visual"]);
    const blockId = text(item.block_id, `${path}.block_id`, 128);
    if (!IDENTIFIER_RE.test(blockId) || seenBlocks.has(blockId)) fail(`${path}.block_id`, "must be a unique identifier");
    seenBlocks.add(blockId);
    const kind = oneOf(item.kind, `${path}.kind`, ["figure", "table"] as const);
    const sourceKind = oneOf(item.source_kind, `${path}.source_kind`, ["figure", "chart", "image", "table"] as const);
    if ((kind === "table") !== (sourceKind === "table")) fail(path, "has inconsistent visual kinds");
    const page = count(item.page, `${path}.page`);
    if (page < 1 || page > pageCount) fail(`${path}.page`, "is outside the document");
    region(item.region, `${path}.region`);
    text(item.label, `${path}.label`, 1_000);
    text(item.caption, `${path}.caption`, 10_000, true);
    text(item.footnote, `${path}.footnote`, 10_000, true);
    if (item.visual !== null) {
      validateStructuredVisualEvidence(item.visual, `${path}.visual`);
      if ((item.visual as { visual_kind?: unknown }).visual_kind !== kind) fail(`${path}.visual.visual_kind`, "does not match kind");
    }
    if (kind === "table") actualTables += 1;
    else actualFigures += 1;
    if (sourceKind === "image") actualImages += 1;
  });
  if (visualCount !== root.visuals.length || tableCount !== actualTables || figureCount !== actualFigures || imageCount !== actualImages) {
    fail("$.stats", "does not match the visual records");
  }

  if (!Array.isArray(root.warnings) || root.warnings.length > 256) fail("$.warnings", "must contain at most 256 entries");
  root.warnings.forEach((warning, index) => text(warning, `$.warnings[${index}]`, 1_000));
  return value as DocumentExtraction;
}
