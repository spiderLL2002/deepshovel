import type { StructuredVisualEvidence } from "../evidence-graph/index.ts";

export const DOCUMENT_EXTRACTION_SCHEMA_VERSION = "document-extraction/1.0" as const;

export const DOCUMENT_METADATA_FIELDS = [
  "title",
  "abstract",
  "year",
  "author",
  "journal",
  "issn",
  "volume",
  "issue",
  "page",
  "article_number",
  "doi",
  "link",
  "publisher",
  "language",
] as const;
export const DOCUMENT_METADATA_FIELD_SOURCES = ["rule", "artifact", "doi_registry", "empty"] as const;

export type DocumentMetadataField = (typeof DOCUMENT_METADATA_FIELDS)[number];
export type DocumentMetadata = Record<DocumentMetadataField, string>;
export type DocumentMetadataFieldSource = (typeof DOCUMENT_METADATA_FIELD_SOURCES)[number];
export type DocumentMetadataSource = Record<DocumentMetadataField, DocumentMetadataFieldSource>;

export type DocumentExtractionVisual = {
  block_id: string;
  kind: "figure" | "table";
  source_kind: "figure" | "chart" | "image" | "table";
  page: number;
  region: { x: number; y: number; width: number; height: number } | null;
  label: string;
  caption: string;
  footnote: string;
  visual: StructuredVisualEvidence | null;
};

export type DocumentExtraction = {
  schema_version: typeof DOCUMENT_EXTRACTION_SCHEMA_VERSION;
  artifact_version_id: string;
  artifact_sha256: string;
  parser: {
    name: "mineru";
    version: string;
    backend: "pipeline";
    method: "auto";
  };
  metadata: DocumentMetadata;
  field_sources: DocumentMetadataSource;
  stats: {
    page_count: number;
    text_block_count: number;
    figure_count: number;
    table_count: number;
    image_count: number;
    visual_count: number;
  };
  markdown: string;
  visuals: DocumentExtractionVisual[];
  warnings: string[];
};
