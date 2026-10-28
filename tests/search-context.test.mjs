import assert from "node:assert/strict";
import test from "node:test";
import { contextHistoryError, contextTextError, continueContextRequest } from "../app/search-context.ts";

test("history preserves whitespace, role, and order while enforcing Unicode boundaries", () => {
  const history = [
    { role: "assistant", content: "  Prior explanation\n" },
    { role: "user", content: "\n请按原条件检索。  " },
  ];
  const frozen = structuredClone(history);
  assert.equal(contextHistoryError(history), null);
  assert.deepEqual(history, frozen);
  assert.equal(contextTextError("😀".repeat(8000)), null);
  assert.match(contextTextError("😀".repeat(8001)), /8,000/);
  assert.match(contextTextError("e\u0301"), /NFC/);
  assert.equal(contextTextError("é"), null);
  assert.match(contextTextError("\uD800"), /Unicode/);
  assert.match(contextTextError("\uDC00"), /Unicode/);
  assert.match(contextTextError("\n\t "), /非空白/);
  assert.match(contextTextError("\u0085\u001c"), /非空白/);
  assert.equal(contextTextError("\ufeff"), null);
});

test("history accepts exact count and byte ceilings and rejects excess without trimming", () => {
  const turns = (count) => Array.from({ length: count }, () => ({ role: "user", content: "a" }));
  assert.equal(contextHistoryError(turns(16)), null);
  assert.match(contextHistoryError(turns(17)), /16/);
  const exact = [
    { role: "user", content: "😀".repeat(8000) },
    { role: "assistant", content: "😀".repeat(192) },
  ];
  assert.equal(contextHistoryError(exact), null);
  const excess = [...exact, { role: "user", content: "a" }];
  const frozen = structuredClone(excess);
  assert.match(contextHistoryError(excess), /32,768/);
  assert.deepEqual(excess, frozen);
  assert.match(contextHistoryError([{ role: "user", content: "valid" }, { role: "user", content: " " }]), /第 2 条/);
});

test("supplement → choice → supplement replaces only the continuation, keeping the complete original request", () => {
  const base = {
    search_protocol: "evidence-retrieval/2.3", query: "  它们的证据呢？ ", limit: 50,
    conversation_history: [{ role: "assistant", content: "Prior task\n" }, { role: "user", content: "real context" }],
    query_claim: { snapshot_id: "snapshot", claim_id: "claim" },
    intent: "supporting_evidence", corpus: "same_paper", project_id: "project",
    filters: { must_terms: ["term"] }, openalex_scope: { level: "topic", id: "T1" },
  };
  const frozen = structuredClone(base);
  const supplemented = continueContextRequest(base, { context_submission: { context_token: "n1.first", content: "补充一\n " } });
  const selected = continueContextRequest(supplemented, { clarification_selection: { clarification_token: "c2.next", option_id: "choice-a" } });
  assert.equal("context_submission" in selected, false);
  const continued = continueContextRequest(selected, { context_submission: { context_token: "n1.last", content: "  补充二" } });
  assert.equal("clarification_selection" in continued, false);
  assert.deepEqual(continued, { ...base, context_submission: { context_token: "n1.last", content: "  补充二" } });
  assert.deepEqual(base, frozen);
  assert.deepEqual(supplemented.context_submission, { context_token: "n1.first", content: "补充一\n " });
});
