// Cross-language contract parity: contracts/shared-contract.json is the only
// place these values are edited; the Compiler runs the mirror of these checks
// in compiler/tests/test_shared_contract.py.
import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

import { PY_PATH, TS_PATH, renderSharedContract } from "../scripts/generate-shared-contract.mjs";
import * as generated from "../lib/shared-contract.generated.ts";
import {
  COMPILER_EXTRACTION_PROMPT_VERSION,
  COMPILER_VERIFICATION_PROMPT_VERSION,
} from "../lib/compiler/prompt-version.ts";
import {
  EVIDENCE_GRAPH_SCHEMA_VERSION,
  REASONING_CONCLUSION_RELATIONS,
  isValidNormalizedRegion,
} from "../lib/evidence-graph/index.ts";
import {
  EVIDENCE_RETRIEVAL_CONTEXT_PROTOCOL_VERSION,
  EVIDENCE_RETRIEVAL_INDEX_VERSION,
  EVIDENCE_RETRIEVAL_SEARCH_PROTOCOL_VERSION,
  QUERY_PLAN_HANDOFF_MAX_LENGTH,
  isRetrievalSessionId,
} from "../lib/evidence-retrieval/index.ts";
import { RETRIEVAL_SESSION_TTL_MS } from "../lib/server/retrieval-sessions.ts";

const contract = JSON.parse(fs.readFileSync(new URL("../contracts/shared-contract.json", import.meta.url), "utf8"));
const cases = JSON.parse(fs.readFileSync(new URL("../contracts/shared-contract-cases.json", import.meta.url), "utf8"));

test("generated TypeScript and Python contract modules are current", () => {
  const { ts, py } = renderSharedContract(contract);
  assert.equal(fs.readFileSync(TS_PATH, "utf8"), ts, "run node scripts/generate-shared-contract.mjs");
  assert.equal(fs.readFileSync(PY_PATH, "utf8"), py, "run node scripts/generate-shared-contract.mjs");
});

test("Web modules expose the shared values rather than local copies", () => {
  assert.equal(generated.SHARED_CONTRACT_VERSION, contract.contract_version);
  assert.equal(COMPILER_EXTRACTION_PROMPT_VERSION, contract.compiler.extraction_prompt_version);
  assert.equal(COMPILER_VERIFICATION_PROMPT_VERSION, contract.compiler.verification_prompt_version);
  assert.equal(EVIDENCE_GRAPH_SCHEMA_VERSION, contract.evidence_graph.schema_version);
  assert.deepEqual([...REASONING_CONCLUSION_RELATIONS].sort(), [...contract.evidence_graph.reasoning_conclusion_relations].sort());
  assert.equal(EVIDENCE_RETRIEVAL_INDEX_VERSION, contract.retrieval.index_version);
  assert.equal(EVIDENCE_RETRIEVAL_SEARCH_PROTOCOL_VERSION, contract.retrieval.search_protocol_version);
  assert.equal(EVIDENCE_RETRIEVAL_CONTEXT_PROTOCOL_VERSION, contract.retrieval.context_protocol_version);
  assert.equal(QUERY_PLAN_HANDOFF_MAX_LENGTH, contract.retrieval.query_plan_handoff_max_characters);
  assert.equal(RETRIEVAL_SESSION_TTL_MS, contract.retrieval_sessions.ttl_seconds * 1000);
  // Server-side sessions must outlive every Compiler continuation token.
  assert.ok(contract.retrieval_sessions.ttl_seconds >= contract.retrieval.continuation_ttl_seconds);
  for (const retired of contract.retrieval.retired_protocol_versions) {
    assert.ok(![EVIDENCE_RETRIEVAL_INDEX_VERSION, EVIDENCE_RETRIEVAL_SEARCH_PROTOCOL_VERSION,
      EVIDENCE_RETRIEVAL_CONTEXT_PROTOCOL_VERSION].includes(retired));
  }
});

test("the retrieval_sessions table enforces the shared session kinds and ID shape", () => {
  const sql = fs.readFileSync(new URL("../drizzle/0026_retrieval_sessions.sql", import.meta.url), "utf8");
  const kinds = /"kind" IN \(([^)]*)\)/u.exec(sql)[1].split(",").map((item) => item.trim().replace(/^'|'$/gu, ""));
  assert.deepEqual(kinds, [...contract.retrieval_sessions.kinds]);
  assert.match(sql, /GLOB 'rs1\.\*' AND length\("retrieval_sessions"\."id"\) = 47/u);
  assert.equal(`rs1.${"A".repeat(43)}`.length, 47);
});

test("golden normalized regions get the Compiler's verdict (GRF-6)", () => {
  for (const { name, region, valid } of cases.normalized_regions) {
    assert.equal(isValidNormalizedRegion(...region), valid, name);
  }
});

test("golden session IDs get the Compiler's verdict", () => {
  for (const { value, valid } of cases.retrieval_session_ids) {
    assert.equal(isRetrievalSessionId(value), valid, value);
  }
});
