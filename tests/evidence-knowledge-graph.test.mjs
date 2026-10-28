import assert from "node:assert/strict";
import test from "node:test";

import {
  claimRefKey,
  projectClaimRelation,
} from "../lib/evidence-knowledge-graph/index.ts";

test("Claim relation projection keeps direct and comparison relations but drops weak matches", () => {
  const query = { snapshotId: "snapshot:one", claimId: "claim:one" };
  const candidate = { snapshotId: "snapshot:two", claimId: "claim:two" };
  assert.equal(projectClaimRelation(query, candidate, "supports", "query_to_result", 0.9)?.relation, "directly_implies");
  assert.equal(projectClaimRelation(query, candidate, "equivalent", "result_to_query", 0.9)?.relation, "equivalent_to");
  assert.equal(projectClaimRelation(query, candidate, "contradicts", "result_to_query", 0.9)?.relation, "contradicts");
  assert.equal(projectClaimRelation(query, candidate, "supplements", "result_to_query", 0.9)?.relation, "supplements");
  assert.equal(projectClaimRelation(query, candidate, "related", "result_to_query", 0.9), null);
  assert.equal(projectClaimRelation(query, candidate, "unrelated", "result_to_query", 0.9), null);
});

test("claim identity remains the snapshot and scoped Claim composite", () => {
  assert.notEqual(
    claimRefKey({ snapshotId: "snapshot:one", claimId: "claim:same" }),
    claimRefKey({ snapshotId: "snapshot:two", claimId: "claim:same" }),
  );
});
