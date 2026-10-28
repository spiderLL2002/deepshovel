import assert from "node:assert/strict";
import test from "node:test";

import {
  COMPILER_JOB_SCHEMA,
  CompilerClient,
  compilerProgress,
  parseCompilerJobEnvelope,
} from "../lib/server/compiler-client.ts";

const jobId = "4d63b26c-6548-4a62-8218-14aa13580cb2";
const validSecret = "compiler-test-shared-secret-0123456789";

function envelope(status = "queued") {
  return {
    schema_version: COMPILER_JOB_SCHEMA,
    job: {
      id: jobId,
      status,
      created_at: "2026-08-15T05:00:00Z",
      updated_at: "2026-08-15T05:00:01Z",
      expires_at: "2026-08-16T05:00:00Z",
      stage_timings_ms: {
        queued: 10,
        parsing: status === "queued" ? null : 1250,
        extracting: null,
        validating: null,
        total: null,
      },
      error: null,
      links: {
        self: `/v1/compile-jobs/${jobId}`,
        result: `/v1/compile-jobs/${jobId}/result`,
      },
    },
  };
}

function jsonResponse(value, init = {}) {
  return new Response(JSON.stringify(value), {
    ...init,
    headers: { "content-type": "application/json", ...init.headers },
  });
}

test("compiler status protocol is exact and versioned", () => {
  const parsed = parseCompilerJobEnvelope(envelope("parsing"));
  assert.equal(parsed.id, jobId);
  assert.equal(parsed.status, "parsing");
  assert.equal(parsed.stageTimingsMs.parsing, 1250);

  assert.throws(
    () => parseCompilerJobEnvelope({ ...envelope(), extra: true }),
    (error) => error.code === "INVALID_COMPILER_RESPONSE",
  );
  assert.throws(
    () => parseCompilerJobEnvelope({ ...envelope(), schema_version: "compiler-job/2.0" }),
    (error) => error.code === "COMPILER_PROTOCOL_MISMATCH",
  );

  const fractionalTiming = envelope();
  fractionalTiming.job.stage_timings_ms.queued = 1.5;
  assert.throws(
    () => parseCompilerJobEnvelope(fractionalTiming),
    (error) => error.code === "INVALID_COMPILER_RESPONSE",
  );
});

test("submit sends trusted provenance as exact multipart fields", async () => {
  const calls = [];
  const client = new CompilerClient({
    baseUrl: "http://127.0.0.1:8011",
    sharedSecret: validSecret,
    fetch: async (url, init) => {
      calls.push({ url, init });
      const form = init.body;
      assert.ok(form instanceof FormData);
      assert.deepEqual([...form.keys()].sort(), [
        "artifact_id", "artifact_sha256", "file", "locator", "media_type", "paper_id", "verify", "version_id",
      ]);
      assert.equal(form.get("paper_id"), "paper:fixture");
      assert.equal(form.get("artifact_id"), "artifact:fixture");
      assert.equal(form.get("version_id"), `artifact-version:sha256:${"a".repeat(64)}`);
      assert.equal(form.get("artifact_sha256"), "a".repeat(64));
      assert.equal(form.get("media_type"), "application/pdf");
      assert.equal(form.get("locator"), "upload://fixture.pdf");
      // Claim-bearing results need a verifier report, so verification is the default.
      assert.equal(form.get("verify"), "true");
      const file = form.get("file");
      assert.ok(file instanceof Blob);
      assert.equal(file.type, "application/pdf");
      assert.equal(new TextDecoder().decode(await file.arrayBuffer()), "%PDF-1.7\nfixture");
      assert.equal(new Headers(init.headers).get("authorization"), `Bearer ${validSecret}`);
      assert.equal(init.redirect, "manual");
      assert.equal(init.referrerPolicy, "no-referrer");
      return jsonResponse(envelope());
    },
  });

  const job = await client.submit(new TextEncoder().encode("%PDF-1.7\nfixture"), {
    paperId: "paper:fixture",
    artifactId: "artifact:fixture",
    versionId: `artifact-version:sha256:${"a".repeat(64)}`,
    sha256: "a".repeat(64),
    mediaType: "application/pdf",
    locator: "upload://fixture.pdf",
    fileName: "fixture.pdf",
  });
  assert.equal(job.id, jobId);
  assert.equal(calls.length, 1);
  assert.equal(String(calls[0].url), "http://127.0.0.1:8011/v1/compile-jobs");
});

test("compiler client rejects unsafe configured transport and oversized results", async () => {
  assert.throws(
    () => new CompilerClient({ baseUrl: "http://compiler.example.test" }),
    (error) => error.code === "COMPILER_NOT_CONFIGURED",
  );
  assert.throws(
    () => new CompilerClient({ baseUrl: "https://user:secret@compiler.example.test" }),
    (error) => error.code === "COMPILER_NOT_CONFIGURED",
  );
  assert.throws(
    () => new CompilerClient({ baseUrl: "https://compiler.example.test" }),
    (error) => error.code === "COMPILER_NOT_CONFIGURED",
  );
  assert.throws(
    () => new CompilerClient({ baseUrl: "https://compiler.example.test", sharedSecret: "too-short" }),
    (error) => error.code === "COMPILER_NOT_CONFIGURED",
  );
  assert.doesNotThrow(() => new CompilerClient({ baseUrl: "http://[::1]:8011" }));

  const client = new CompilerClient({
    baseUrl: "https://compiler.example.test",
    sharedSecret: validSecret,
    fetch: async () => jsonResponse({}, { headers: { "content-length": String(9 * 1024 * 1024) } }),
  });
  await assert.rejects(
    () => client.result(jobId),
    (error) => error.code === "COMPILER_RESPONSE_TOO_LARGE",
  );

  const redirect = new CompilerClient({
    baseUrl: "http://127.0.0.1:8011",
    fetch: async () => new Response(null, {
      status: 307,
      headers: { location: "https://attacker.example.test/compiler" },
    }),
  });
  await assert.rejects(
    () => redirect.status(jobId),
    (error) => error.code === "COMPILER_REDIRECT_REJECTED"
      && error.details?.retryable === false,
  );
});

test("compiler job identity, links, timestamps, and failure state are correlated", async () => {
  const wrongLinks = envelope();
  wrongLinks.job.links.result = "https://attacker.example/result";
  assert.throws(
    () => parseCompilerJobEnvelope(wrongLinks),
    (error) => error.code === "INVALID_COMPILER_RESPONSE",
  );

  const regressedTime = envelope();
  regressedTime.job.updated_at = "2026-08-14T05:00:00Z";
  assert.throws(
    () => parseCompilerJobEnvelope(regressedTime),
    (error) => error.code === "INVALID_COMPILER_RESPONSE",
  );

  const impossibleTime = envelope();
  impossibleTime.job.created_at = "2026-02-31T05:00:00Z";
  assert.throws(
    () => parseCompilerJobEnvelope(impossibleTime),
    (error) => error.code === "INVALID_COMPILER_RESPONSE",
  );

  assert.throws(
    () => parseCompilerJobEnvelope(envelope("failed")),
    (error) => error.code === "INVALID_COMPILER_RESPONSE",
  );

  const otherId = "f7acdb0c-1700-4d85-a852-31ed4f2670de";
  const other = envelope();
  other.job.id = otherId;
  other.job.links.self = `/v1/compile-jobs/${otherId}`;
  other.job.links.result = `/v1/compile-jobs/${otherId}/result`;
  const client = new CompilerClient({
    baseUrl: "https://compiler.example.test",
    sharedSecret: validSecret,
    fetch: async () => jsonResponse(other),
  });
  await assert.rejects(
    () => client.status(jobId),
    (error) => error.code === "COMPILER_JOB_MISMATCH",
  );
});

test("compiler transport errors cannot leak secrets or unbounded upstream codes", async () => {
  const unavailableSecret = "do-not-leak-compiler-secret-0123456789";
  const unavailable = new CompilerClient({
    baseUrl: "https://compiler.example.test",
    sharedSecret: unavailableSecret,
    fetch: async () => { throw new Error(`request with ${unavailableSecret} failed`); },
  });
  await assert.rejects(
    () => unavailable.status(jobId),
    (error) => error.code === "COMPILER_UNAVAILABLE" && !error.message.includes(unavailableSecret),
  );

  const rejected = new CompilerClient({
    baseUrl: "https://compiler.example.test",
    sharedSecret: validSecret,
    fetch: async () => jsonResponse({ detail: {
      code: "../../FAKE\nCODE",
      message: "bad\u0000 message",
    } }, { status: 400 }),
  });
  await assert.rejects(
    () => rejected.status(jobId),
    (error) => error.code === "UPSTREAM_REQUEST_FAILED" && !error.message.includes("\u0000"),
  );

  const alreadyRemoved = new CompilerClient({
    baseUrl: "https://compiler.example.test",
    sharedSecret: validSecret,
    fetch: async () => jsonResponse({ error: {
      code: "JOB_NOT_FOUND",
      message: "compile job was not found",
      retryable: false,
    } }, { status: 404 }),
  });
  await assert.rejects(
    () => alreadyRemoved.remove(jobId),
    (error) => error.code === "UPSTREAM_JOB_NOT_FOUND"
      && error.message === "compile job was not found",
  );

  const reflectedSecret = "reflected-secret-value-0123456789abcdef";
  const reflected = new CompilerClient({
    baseUrl: "https://compiler.example.test",
    sharedSecret: reflectedSecret,
    fetch: async () => jsonResponse({ detail: {
      code: "BAD_REQUEST",
      message: reflectedSecret,
      retryable: false,
    } }, { status: 400 }),
  });
  await assert.rejects(
    () => reflected.status(jobId),
    (error) => error.code === "COMPILER_RESPONSE_REJECTED" && !error.message.includes(reflectedSecret),
  );

  const escapedSecretValue = "reflected\\\"secret\\\\value-with-padding-0123456789";
  const escapedReflection = new CompilerClient({
    baseUrl: "https://compiler.example.test",
    sharedSecret: escapedSecretValue,
    fetch: async () => jsonResponse({ detail: {
      code: "BAD_REQUEST",
      message: escapedSecretValue,
      retryable: false,
    } }, { status: 400 }),
  });
  await assert.rejects(
    () => escapedReflection.status(jobId),
    (error) => error.code === "COMPILER_RESPONSE_REJECTED" && !error.message.includes(escapedSecretValue),
  );

  const proxyFailure = new CompilerClient({
    baseUrl: "https://compiler.example.test",
    sharedSecret: validSecret,
    fetch: async () => new Response("<html>proxy unavailable</html>", {
      status: 503,
      headers: { "content-type": "text/html" },
    }),
  });
  await assert.rejects(
    () => proxyFailure.status(jobId),
    (error) => error.code === "UPSTREAM_REQUEST_FAILED" && error.status === 503
      && error.details.retryable === true,
  );
});

test("compiler retries only bodyless GET fetch failures once", async () => {
  const recoveredCalls = [];
  const recovered = new CompilerClient({
    baseUrl: "https://compiler.example.test",
    sharedSecret: validSecret,
    fetch: async (url, init) => {
      recoveredCalls.push({ url, init });
      if (recoveredCalls.length === 1) throw new TypeError("stale keep-alive socket");
      return jsonResponse(envelope("parsing"));
    },
  });
  assert.equal((await recovered.status(jobId)).status, "parsing");
  assert.equal(recoveredCalls.length, 2);
  for (const call of recoveredCalls) {
    assert.equal(String(call.url), `https://compiler.example.test/v1/compile-jobs/${jobId}`);
    assert.equal(call.init.method, "GET");
    assert.equal(call.init.redirect, "manual");
    assert.equal(new Headers(call.init.headers).get("authorization"), `Bearer ${validSecret}`);
  }

  let failedGetCalls = 0;
  const unavailable = new CompilerClient({
    baseUrl: "https://compiler.example.test",
    sharedSecret: validSecret,
    fetch: async () => {
      failedGetCalls += 1;
      throw new TypeError("connection reset");
    },
  });
  await assert.rejects(
    () => unavailable.status(jobId),
    (error) => error.code === "COMPILER_UNAVAILABLE"
      && error.status === 503
      && error.details.retryable === true,
  );
  assert.equal(failedGetCalls, 2);

  let failedPostCalls = 0;
  const noReplay = new CompilerClient({
    baseUrl: "https://compiler.example.test",
    sharedSecret: validSecret,
    fetch: async () => {
      failedPostCalls += 1;
      throw new TypeError("connection reset");
    },
  });
  await assert.rejects(
    () => noReplay.submit(new TextEncoder().encode("%PDF-1.7\nfixture"), {
      paperId: "paper:fixture",
      artifactId: "artifact:fixture",
      versionId: `artifact-version:sha256:${"a".repeat(64)}`,
      sha256: "a".repeat(64),
      mediaType: "application/pdf",
      locator: "upload://fixture.pdf",
      fileName: "fixture.pdf",
    }),
    (error) => error.code === "COMPILER_UNAVAILABLE" && error.status === 503,
  );
  assert.equal(failedPostCalls, 1);
});

test("compiler stages map to monotonic product progress", () => {
  const stages = ["queued", "parsing", "extracting", "validating", "completed"];
  const values = stages.map(compilerProgress);
  assert.deepEqual([...values].sort((left, right) => left - right), values);
  assert.equal(compilerProgress("failed"), 100);
});

test("compiler deletions require exact 204 and are never replayed", async () => {
  const calls = [];
  const versionId = `artifact-version:sha256:${"a".repeat(64)}`;
  const client = new CompilerClient({
    baseUrl: "https://compiler.example.test",
    sharedSecret: validSecret,
    fetch: async (url, init) => {
      calls.push({ url, init });
      return new Response(null, { status: 204 });
    },
  });
  await client.remove(jobId);
  await client.removeDocumentExtraction(versionId);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].init.method, "DELETE");
  assert.equal(calls[0].init.redirect, "manual");
  assert.equal(String(calls[0].url), `https://compiler.example.test/v1/compile-jobs/${jobId}`);
  assert.equal(calls[1].init.method, "DELETE");
  assert.equal(String(calls[1].url), `https://compiler.example.test/v1/document-extractions/${encodeURIComponent(versionId)}`);

  let failedCalls = 0;
  const unavailable = new CompilerClient({
    baseUrl: "https://compiler.example.test",
    sharedSecret: validSecret,
    fetch: async () => {
      failedCalls += 1;
      throw new TypeError("connection reset");
    },
  });
  await assert.rejects(() => unavailable.remove(jobId), (error) => error.code === "COMPILER_UNAVAILABLE");
  assert.equal(failedCalls, 1);

  const wrongSuccess = new CompilerClient({
    baseUrl: "https://compiler.example.test",
    sharedSecret: validSecret,
    fetch: async () => jsonResponse({ ok: true }),
  });
  await assert.rejects(() => wrongSuccess.remove(jobId), (error) => error.code === "INVALID_COMPILER_RESPONSE");
});

test("Claim normalization uses the fixed authenticated Compiler endpoint and binds the source span", async () => {
  const userText = "在成人中，干预 X 可能改善结局 Y。";
  const calls = [];
  const client = new CompilerClient({
    baseUrl: "https://compiler.example.test",
    sharedSecret: validSecret,
    fetch: async (url, init) => {
      calls.push({ url, init });
      return jsonResponse({
        schema_version: "claim-comparison/1.0",
        processor_version: "claim-normalizer/1.0.0",
        status: "ready",
        clarification: null,
        run: {
          run_id: "claim-normalization:test",
          model: "gpt-5.6-luna",
          prompt_version: "claim-normalizer/1.0.0",
          attempts: 1,
        },
        claims: [{
          id: "claim-draft:test",
          origin_span: userText,
          canonical_claim: "干预 X 可能改善成人结局 Y。",
          claim_type: "causal",
          polarity: "uncertain",
          modality: "hypothesis",
          scope: {
            location: [], time: null, formation: [], sample_type: ["成人"], method: [],
            condition: [], measurement: ["结局 Y"], uncertainty: null, entities: ["干预 X"],
          },
          status: "ready",
          clarification: null,
        }],
      });
    },
  });
  const result = await client.normalizeClaimComparison({
    schema_version: "claim-comparison/1.0",
    project_id: "project:test",
    user_text: userText,
  });
  assert.equal(result.claims[0].originSpan, userText);
  assert.equal(calls.length, 1);
  assert.equal(String(calls[0].url), "https://compiler.example.test/v1/claim-comparisons/normalize");
  assert.equal(calls[0].init.method, "POST");
  assert.equal(new Headers(calls[0].init.headers).get("authorization"), `Bearer ${validSecret}`);
});
