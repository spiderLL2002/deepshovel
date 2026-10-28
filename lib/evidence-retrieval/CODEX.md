# Evidence Retrieval

## Boundary

This module creates and persists the deterministic `evidence-retrieval/2.0`
projection. It does not execute hybrid search. Search is served by the Compiler
retrieval service after the Web gateway lazily synchronizes missing immutable
snapshots.

The D1 projection has three append-only tables:

- `evidence_retrieval_documents`: one self-contained search document per
  `AtomicClaim`.
- `evidence_retrieval_assessments`: independent verifier output, retained for
  result hydration but never used as query stance or ranking input.
- `evidence_retrieval_manifests`: one completion marker per snapshot and index
  version, including valid zero-Claim snapshots.

An import persists the graph snapshot, documents, assessments, and manifest in
one D1 batch. It never performs an external Compiler write inside that
transaction. The gateway checks manifests and idempotently uploads only missing
snapshot chunks. Query and hydration code must not read
`evidence_graph_snapshots.graph_json` on the search hot path.

## Claim document

Every Claim document carries the immutable snapshot, paper, and paper-family
identity; Claim and source IDs; canonical proposition and scoped identity; full
Scope; paper title; deterministic entities, key terms, abbreviations, and
qualifiers; type, polarity, modality; compact evidence anchors; the shortest
complete Artifact-to-Claim Evidence Path and its quality; extractor prompt and
index versions; and any explicit Claim-to-Claim comparison edges touching the
Claim.

`original_claim` has a stricter provenance rule than `canonical_claim`: it may
only contain an exact quote copied from the Claim or its on-path SourceAnchor.
It is `null` when no exact quote exists and is never synthesized from the
canonical proposition. Claims are terminal by graph invariant, so retrieval
documents use `authors_conclusion` attribution and `terminal_conclusion`
rhetorical role.

Projection is deterministic. Equal-length Evidence Paths are resolved by
lexicographic traversal. Derived `evidence_context` is capped at 16,000 Unicode
code points, `key_terms` at 256 entries, and abbreviations at 32 entries.
These are transport-safe derived fields; full provenance anchors and path nodes
are not silently truncated. A single serialized document larger than 512 KiB
is rejected explicitly. Snapshot Claim count has no business-level cap; the
gateway's 2,048-document limit applies only to each service upload chunk.

## Lifecycle

The newest completed manifest for a paper family is active. Consequently, a
newest zero-Claim snapshot suppresses older Claim-bearing snapshots without a
graph JSON scan. An explicit historical query Claim replaces only its own
family's active snapshot during D1 resolution.

The Compiler retrieval service is progressive. A `preview` request performs
multi-channel lexical and dense recall, fusion, and the bounded local
technical-anchor RRF pass, then returns at most 30 cross-paper compact Claim
references without a query-relation label. For a Claim-seeded search, Claims
from the same immutable paper snapshot bypass recall and are added directly to
the `ai_rerank` candidate set; they never consume a preview slot. The request
still names the exact cross-paper preview references. GPT classifies the
combined final set before clustering, deduplication, and diversity. AI failure
never falls back to token overlap as a relation judgment. The Web gateway
hydrates both stages from D1 with bounded batch queries.

The projection/index remains `evidence-retrieval/2.0`; staged browser searches
use search protocol `evidence-retrieval/2.1`. Its preview returns a bounded
four-part signed `query_plan_handoff`. The browser returns that token together
with the exact ordered preview Claim refs, and the Compiler validates all bound
request, manifest, configuration, QueryPlan, candidate, and expiry state before
the final stage. The verified final directly reloads those sealed descriptors;
it does not repeat lexical/dense recall, fusion, anchoring, diversity, or
reserve work, and its lexical/dense diagnostics truthfully report that those
operations were skipped in that stage. Same-paper closure remains allowed only
for the sealed query snapshot and still obeys every explicit caller constraint.
A handoff-free final is serialized as the explicit legacy `2.0` path for
non-interactive maintenance callers only; with interactive search enabled the
Web client and the Compiler both refuse it (synthesis finals use the sealed
2.1 handoff since 2026-10-19). The browser never holds the handoff itself: the
search route stores it server-side and hands out an opaque `rs1.` session ID
(`isRetrievalSessionId`); the same applies to n1/c2 continuation tokens. Token integrity proves only that the same operational preview
state was handed forward, not relevance or scientific truth.

The former clarification-only protocol `evidence-retrieval/2.2` (c1 tokens) was
removed from the Web tier on 2026-10-18 and from the Compiler on 2026-10-19;
requests naming it are rejected. Requests without `search_protocol` keep the
closed internal `2.0/2.1` contracts (used server-side by synthesis) and reject
selection fields.

The browser protocol `evidence-retrieval/2.3` accepts real `conversation_history`
and one `context_submission` or `clarification_selection`. History preserves
ordered user/assistant roles and exact NFC Unicode content: at most 16 turns,
8,000 code points per turn and 32,768 total UTF-8 content bytes. Supplements
obey the same per-entry text boundary. Blank, malformed, non-NFC and oversized
context is rejected without truncation or normalization. A `needs_context`
preview has a null plan/confidence, no candidates or handoff, and all retrieval
channels skipped. Its `n1` token and the new `c2` choice token use separate
purposes; the Compiler validates the complete history, filters and continuation
binding, and the final handoff retains the actual submitted continuation.
The 2.3 DTO additionally forwards an explicit OpenAlex scope so two filters
resolving to the same snapshots still have distinct request identity. Internal
2.0/2.1 wire behavior remains unchanged. Context and interpreted tasks are
user/model input, not source evidence or a scientific-quality judgment.

Artifact deletion is the sole authorized destructive lifecycle: after the D1
deletion barrier is acquired it first performs an idempotent authenticated purge
of the Compiler FTS/vector projection, then removes source bytes and the new D1
projection rows in the existing whole-Artifact purge. A service failure leaves
bytes and immutable D1 identity intact for retry. Database triggers reject
ordinary updates and deletes.

## Files

- `types.ts`: internal projection types plus the shared strict HTTP DTOs.
- `projection.ts`: deterministic Claim documents, manifests, assessments, and
  DTO serialization.
- `persistence.ts`: append-only D1 statements, active-manifest resolution, and
  bounded hydration.
- `contract.ts`: closed public request parser, service serializers, and strict
  stored-DTO parsers.
- `index.ts`: the public module exports.

## Verification

```bash
./node_modules/.bin/tsc --noEmit --incremental false
./node_modules/.bin/eslint lib/evidence-retrieval app/api/evidence-graphs/import-store.ts app/api/artifacts/delete-store.ts tests/evidence-retrieval.test.mjs
node --test tests/evidence-retrieval.test.mjs tests/backend-evidence-import.test.mjs tests/backend-artifact-delete.test.mjs
```
