import assert from "node:assert/strict";
import test from "node:test";

import { parseDocumentExtraction } from "../lib/document-extraction/index.ts";

const sha256 = "a".repeat(64);

function metadata(value = "") {
  return {
    title: value,
    abstract: "",
    year: "",
    author: "",
    journal: "",
    issn: "",
    volume: "",
    issue: "",
    page: "",
    article_number: "",
    doi: "",
    link: "",
    publisher: "",
    language: "",
  };
}

function metadataSources(value = "empty") {
  return Object.fromEntries(Object.keys(metadata()).map((key) => [key, value]));
}

function extractionFixture() {
  return {
    schema_version: "document-extraction/1.0",
    artifact_version_id: `artifact-version:sha256:${sha256}`,
    artifact_sha256: sha256,
    parser: { name: "mineru", version: "2.1", backend: "pipeline", method: "auto" },
    metadata: metadata("Paper title"),
    field_sources: { ...metadataSources(), title: "rule" },
    stats: {
      page_count: 2,
      text_block_count: 4,
      figure_count: 0,
      table_count: 1,
      image_count: 0,
      visual_count: 1,
    },
    markdown: "# Paper title\n\nBody",
    visuals: [{
      block_id: "block:p1:000001",
      kind: "table",
      source_kind: "table",
      page: 1,
      region: { x: 0.1, y: 0.2, width: 0.5, height: 0.3 },
      label: "Table 1",
      caption: "Measurements",
      footnote: "",
      visual: {
        kind: "structured_visual_evidence",
        id: "document-visual:table:1",
        anchor_id: "document-anchor:table:1",
        visual_kind: "table",
        axes: [],
        series: [],
        cells: [{ row: 1, column: "Value", raw_value: "42", numeric_value: 42, unit: null }],
        features: [],
      },
    }],
    warnings: [],
  };
}

test("document extraction accepts bound metadata, Markdown, geometry, and structured tables", () => {
  const fixture = extractionFixture();
  assert.equal(parseDocumentExtraction(fixture), fixture);
});

test("document extraction accepts DOI registry metadata and article numbers", () => {
  const fixture = extractionFixture();
  fixture.metadata.article_number = "112012";
  fixture.field_sources.article_number = "doi_registry";
  assert.equal(parseDocumentExtraction(fixture), fixture);
});

test("document extraction rejects visual counts and artifact provenance that do not match", () => {
  const wrongCount = structuredClone(extractionFixture());
  wrongCount.stats.table_count = 0;
  assert.throws(() => parseDocumentExtraction(wrongCount), /stats/u);

  const wrongVersion = structuredClone(extractionFixture());
  wrongVersion.artifact_version_id = `artifact-version:sha256:${"b".repeat(64)}`;
  assert.throws(() => parseDocumentExtraction(wrongVersion), /artifact_version_id/u);
});

test("document extraction rejects raw or unknown fields instead of exposing them", () => {
  const fixture = extractionFixture();
  fixture.visuals[0].raw_html = "<script>alert(1)</script>";
  assert.throws(() => parseDocumentExtraction(fixture), /invalid shape/u);

  const unknownSource = extractionFixture();
  unknownSource.field_sources.issue = "search_engine";
  assert.throws(() => parseDocumentExtraction(unknownSource), /field_sources\.issue/u);
});
