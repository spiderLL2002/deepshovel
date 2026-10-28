import type { CitationSourceProjection } from "./types.ts";

/** Canonical Compiler/D1 wire shape for immutable citation-source hashing. */
export function toCitationSourceProjectionDto(projection: CitationSourceProjection) {
  return {
    schema_version: projection.schemaVersion,
    extractor_version: projection.extractorVersion,
    artifact_version_id: projection.artifactVersionId,
    references: projection.references.map((item) => ({
      id: item.id,
      ordinal: item.ordinal,
      label: item.label,
      raw_text: item.rawText,
      doi: item.doi,
      year: item.year,
      source_block_id: item.sourceBlockId,
      page: item.page,
    })),
    mentions: projection.mentions.map((item) => ({
      id: item.id,
      reference_id: item.referenceId,
      marker: item.marker,
      exact_quote: item.exactQuote,
      context_text: item.contextText,
      source_block_id: item.sourceBlockId,
      context_block_ids: item.contextBlockIds,
      page: item.page,
      bbox: item.bbox,
      char_start: item.charStart,
      char_end: item.charEnd,
      group_size: item.groupSize,
      candidate_functions: item.candidateFunctions,
    })),
    warnings: projection.warnings,
  };
}
