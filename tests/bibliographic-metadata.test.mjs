import assert from "node:assert/strict";
import test from "node:test";

import { enrichBibliographicMetadata } from "../lib/server/bibliographic-metadata.ts";

const sha256 = "a".repeat(64);
const doi = "10.1016/j.palaeo.2024.112012";
const title = "Palaeoenvironmental changes in the Late Triassic lacustrine facies of the Ordos Basin";

function extraction(overrides = {}) {
  const metadata = {
    title,
    abstract: "Local abstract remains authoritative.",
    year: "",
    author: "",
    journal: "",
    issn: "",
    volume: "",
    issue: "",
    page: "112012",
    article_number: "",
    doi,
    link: "",
    publisher: "",
    language: "en",
    ...overrides,
  };
  return {
    schema_version: "document-extraction/1.0",
    artifact_version_id: `artifact-version:sha256:${sha256}`,
    artifact_sha256: sha256,
    parser: { name: "mineru", version: "2.1", backend: "pipeline", method: "auto" },
    metadata,
    field_sources: Object.fromEntries(Object.keys(metadata).map((field) => [
      field,
      metadata[field] ? "rule" : "empty",
    ])),
    stats: {
      page_count: 1,
      text_block_count: 1,
      figure_count: 0,
      table_count: 0,
      image_count: 0,
      visual_count: 0,
    },
    markdown: "",
    visuals: [],
    warnings: [],
  };
}

function csl(overrides = {}) {
  return {
    DOI: doi,
    title: `${title}: Implications for understanding the Carnian Pluvial Event`,
    author: [
      { given: "Miruo", family: "Lin" },
      { given: "Kelai", family: "Xi" },
    ],
    "container-title": "Palaeogeography, Palaeoclimatology, Palaeoecology",
    publisher: "Elsevier BV",
    volume: "637",
    issue: null,
    page: "112012",
    "article-number": "112012",
    ISSN: ["0031-0182"],
    issued: { "date-parts": [[2024, 3]] },
    URL: `http://dx.doi.org/${doi}`,
    ...overrides,
  };
}

function jsonResponse(value) {
  return Response.json(value, {
    headers: { "content-type": "application/vnd.citationstyles.csl+json" },
  });
}

function crossrefResponse(items) {
  return Response.json({
    status: "ok",
    "message-type": "work-list",
    message: { items },
  });
}

test("exact DOI content negotiation enriches bibliographic fields and separates an article number", async () => {
  const calls = [];
  const result = await enrichBibliographicMetadata(extraction(), {
    fetch: async (url, init) => {
      calls.push({ url: String(url), init });
      if (calls.length === 1) {
        return new Response(null, {
          status: 302,
          headers: { location: `https://api.crossref.org/v1/works/${encodeURIComponent(doi)}/transform` },
        });
      }
      return jsonResponse(csl());
    },
  });

  assert.equal(result.status, "matched");
  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, `https://doi.org/${encodeURIComponent(doi)}`);
  assert.equal(calls[0].init.redirect, "manual");
  assert.equal(new Headers(calls[0].init.headers).get("accept"), "application/vnd.citationstyles.csl+json");
  assert.equal(result.extraction.metadata.journal, "Palaeogeography, Palaeoclimatology, Palaeoecology");
  assert.equal(result.extraction.metadata.volume, "637");
  assert.equal(result.extraction.metadata.issue, "");
  assert.equal(result.extraction.metadata.page, "");
  assert.equal(result.extraction.metadata.article_number, "112012");
  assert.equal(result.extraction.metadata.publisher, "Elsevier BV");
  assert.equal(result.extraction.metadata.issn, "0031-0182");
  assert.equal(result.extraction.metadata.author, "Miruo Lin; Kelai Xi");
  assert.equal(result.extraction.metadata.year, "2024");
  assert.equal(result.extraction.metadata.link, `https://doi.org/${doi}`);
  assert.equal(result.extraction.metadata.abstract, "Local abstract remains authoritative.");
  assert.equal(result.extraction.field_sources.journal, "doi_registry");
  assert.equal(result.extraction.field_sources.issue, "doi_registry");
  assert.equal(result.extraction.field_sources.page, "doi_registry");
});

test("only an unusable title is not applicable and an ordinary 404 is deterministic", async (t) => {
  await t.test("not applicable", async () => {
    let calls = 0;
    const source = extraction({ title: "", doi: "", page: "" });
    const result = await enrichBibliographicMetadata(source, {
      fetch: async () => {
        calls += 1;
        throw new Error("must not fetch");
      },
    });
    assert.equal(result.status, "not-applicable");
    assert.equal(result.extraction, source);
    assert.equal(calls, 0);
  });

  await t.test("not found", async () => {
    const result = await enrichBibliographicMetadata(extraction(), {
      fetch: async () => new Response(null, { status: 404 }),
    });
    assert.equal(result.status, "not-found");
  });
});

test("a DOI-less paper uses one uniquely verified Crossref title candidate", async () => {
  const calls = [];
  const candidate = csl();
  const result = await enrichBibliographicMetadata(
    extraction({ doi: "", page: "", year: "2024", author: "Miruo Lin; Kelai Xi" }),
    {
      fetch: async (url, init) => {
        calls.push({ url: new URL(String(url)), init });
        return crossrefResponse([candidate]);
      },
    },
  );

  assert.equal(result.status, "matched");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url.origin, "https://api.crossref.org");
  assert.equal(calls[0].url.pathname, "/works");
  assert.equal(calls[0].url.searchParams.get("query.title"), title);
  assert.equal(calls[0].url.searchParams.get("rows"), "3");
  assert.equal(calls[0].url.searchParams.get("select").includes("DOI,title,author"), true);
  assert.equal(calls[0].init.redirect, "manual");
  assert.equal(new Headers(calls[0].init.headers).get("accept"), "application/json");
  assert.equal(result.extraction.metadata.doi, doi);
  assert.equal(result.extraction.metadata.journal, "Palaeogeography, Palaeoclimatology, Palaeoecology");
  assert.equal(result.extraction.metadata.article_number, "112012");
  assert.equal(result.extraction.field_sources.doi, "doi_registry");
});

test("Crossref title fallback rejects ambiguity and a first-author mismatch", async (t) => {
  const source = extraction({ doi: "", page: "", year: "2024", author: "Miruo Lin" });

  await t.test("ambiguous candidates", async () => {
    const result = await enrichBibliographicMetadata(source, {
      fetch: async () => crossrefResponse([
        csl({ DOI: "10.1000/candidate-a" }),
        csl({ DOI: "10.1000/candidate-b" }),
      ]),
    });
    assert.equal(result.status, "not-found");
    assert.equal(result.extraction.metadata.doi, "");
  });

  await t.test("different first author", async () => {
    const result = await enrichBibliographicMetadata(source, {
      fetch: async () => crossrefResponse([
        csl({ DOI: "10.1000/candidate", author: [{ given: "Wei", family: "Zhang" }] }),
      ]),
    });
    assert.equal(result.status, "not-found");
    assert.equal(result.extraction.metadata.doi, "");
  });
});

test("transient Crossref title lookup failures are retried briefly and deferred", async () => {
  let calls = 0;
  const result = await enrichBibliographicMetadata(
    extraction({ doi: "", page: "", year: "2024", author: "Miruo Lin" }),
    {
      fetch: async () => {
        calls += 1;
        return new Response(null, { status: 503, headers: { "retry-after": "0" } });
      },
      sleep: async () => {},
    },
  );
  assert.equal(result.status, "deferred");
  assert.equal(calls, 2);
});

test("registry data is rejected unless both DOI and local title match", async (t) => {
  await t.test("different DOI", async () => {
    const result = await enrichBibliographicMetadata(extraction(), {
      fetch: async () => jsonResponse(csl({ DOI: "10.1000/unrelated" })),
    });
    assert.equal(result.status, "not-found");
    assert.equal(result.extraction.metadata.journal, "");
  });

  await t.test("different title", async () => {
    const result = await enrichBibliographicMetadata(extraction(), {
      fetch: async () => jsonResponse(csl({ title: "An unrelated reference-list paper about a different scientific problem" })),
    });
    assert.equal(result.status, "not-found");
    assert.equal(result.extraction.metadata.journal, "");
  });
});

test("timeouts and retryable HTTP failures are bounded and remain retryable later", async (t) => {
  await t.test("network failure", async () => {
    let calls = 0;
    const result = await enrichBibliographicMetadata(extraction(), {
      fetch: async () => {
        calls += 1;
        throw new Error("timeout");
      },
      sleep: async () => {},
    });
    assert.equal(result.status, "deferred");
    assert.equal(calls, 2);
  });

  await t.test("429 and 5xx", async () => {
    const statuses = [429, 503];
    let calls = 0;
    const result = await enrichBibliographicMetadata(extraction(), {
      fetch: async () => {
        const status = statuses[calls];
        calls += 1;
        return new Response(null, { status, headers: { "retry-after": "0" } });
      },
      sleep: async () => {},
    });
    assert.equal(result.status, "deferred");
    assert.equal(calls, 2);
  });
});

test("DOI authority redirects and response sizes stay inside fixed security boundaries", async (t) => {
  await t.test("rejects an untrusted redirect", async () => {
    let calls = 0;
    const result = await enrichBibliographicMetadata(extraction(), {
      fetch: async () => {
        calls += 1;
        return new Response(null, { status: 302, headers: { location: "https://example.com/record" } });
      },
    });
    assert.equal(result.status, "deferred");
    assert.equal(calls, 1);
  });

  await t.test("rejects an oversized declared body", async () => {
    const result = await enrichBibliographicMetadata(extraction(), {
      fetch: async () => new Response("{}", {
        headers: {
          "content-length": String(512 * 1024 + 1),
          "content-type": "application/vnd.citationstyles.csl+json",
        },
      }),
    });
    assert.equal(result.status, "deferred");
  });
});
