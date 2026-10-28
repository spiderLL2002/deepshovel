# Evidence Search API

`POST /api/evidence-search` is the read-only Web gateway for the external
`evidence-retrieval/2.0` index. D1 is authoritative for immutable projection
documents, source metadata, evidence paths and independent verifier
assessments; the Compiler index is rebuildable candidate/ranking state.

## Modes

- `?response=quick_preview`: local D1-only literal recall over the resolved
  active corpus (`lib/server/quick-search.ts`). It never syncs the Compiler
  index, plans, embeds or classifies relations, and its candidates never feed a
  final. It is provisional loading feedback only: the browser hides it as soon
  as the model-backed request fails. Requests that need task interpretation
  (query Claim, history, context, filters) are rejected with
  `QUICK_PREVIEW_UNSUPPORTED`.
- Ordinary search: two stages, described below.

There is no other response mode. The former default-off strict-citation
answer/capture branches were removed on 2026-10-18.

## Protocol

The browser uses one protocol, `search_protocol: "evidence-retrieval/2.3"`:
conversation history, at most one `context_submission` or
`clarification_selection`, and the two-stage flow. The former
clarification-only protocol `evidence-retrieval/2.2` was removed; requests
naming it are rejected. Requests without `search_protocol` keep the internal
2.0/2.1 semantics that the synthesis route uses server-side; the browser does
not send them.

Continuation state is server-side (2026-10-19). The Compiler-signed preview
handoff (v1), needs-context token (n1) and clarification token (c2) never leave
the server: each is stored in the D1 table `retrieval_sessions` (migration
0026, expiry 30 minutes after last use, bounded pruning on write) and the browser receives an
opaque `rs1.` session ID in the same response field (`queryPlanHandoff`,
`needsContext.contextToken`, `clarification.clarificationToken`). The route
parses the whole request with `continuations: "session_ids"` before reading any
binding, then swaps each session ID for its token, so the Compiler still
authenticates the token and every bound request field. A missing, expired or
wrong-kind session, or a raw Compiler token sent by an old page, returns the
matching safe 409 (`QUERY_PLAN_HANDOFF_REPREVIEW_REQUIRED`,
`CONTEXT_REPREVIEW_REQUIRED` or `CLARIFICATION_REPREVIEW_REQUIRED`); any other
value is a 400. A request carrying a context or clarification continuation
maps corpus drift and contract errors to `CONTEXT_REPREVIEW_REQUIRED`; a plain
final maps drift to the handoff conflict. Use slides a session's expiry, because
the final resends the preview's continuation. The session layer adds no
fallback and no extra model call.

1. `stage: "preview"` resolves the corpus, synchronizes the index, then lets the
   Compiler plan the task (or return `clarification_required` /
   `needs_context`) and recall candidates. The response carries the
   `queryPlanHandoff` session ID.
2. `stage: "ai_rerank"` returns that session ID plus the exact ordered
   `candidate_refs`. The Compiler verifies the binding and runs one ranking
   completion. A stale or invalid continuation returns a safe 409
   (`CONTEXT_REPREVIEW_REQUIRED` or `QUERY_PLAN_HANDOFF_REPREVIEW_REQUIRED`);
   the client must preview again. An interactive final without a handoff is
   refused by both the Web client and the Compiler, and an interactive planner
   failure is an error rather than a locally sealed plan.

Interactive answers come only from validated model output. A degraded relation
stage is a 503 before hydration; local recall never substitutes for a model
answer. There is no application-level time limit; only caller cancellation is
forwarded. Responses are `private, no-store`.

## Request

The parser (`parseEvidenceSearchRequest`) rejects unknown fields and conflicting
constraints. Explicit must/exclude terms, Scope, Claim types, polarity, corpus
and intent are hard constraints. The body is capped at 4 MiB.

## Corpus and lazy synchronization

The D1 resolver chooses the newest manifest per paper family; a newest
zero-Claim manifest suppresses older Claim-bearing versions, and a `query_claim`
replaces its own family's active snapshot with its explicit historical one.

Before each search, every resolved manifest is checked against the Compiler in
pages of at most 4096. Missing, stale or partial snapshots are uploaded first,
**one document per index request** (`RETRIEVAL_INDEX_INFERENCE_BATCH = 1`, to stay
inside the Worker request window) and at most 7.5 MB per request. Any sync
failure fails the search. A complete lexical-only `degraded` snapshot is not
re-embedded by ordinary queries.

Search requests always carry the explicit active snapshot set: a list when it has
at most 4096 entries, otherwise a packed `sha256-v1:` digest list. The corpus is
capped at 131,072 snapshots. Every returned reference must belong to the
D1-resolved corpus.

## Hydration and response

The service returns compact `(snapshot_id, claim_id)` references. The route
batch-loads Claim documents, latest assessments and source metadata from D1,
checks paper and family identity, and never treats service prose as evidence.
Every cluster keeps its representative; other members are hydrated round-robin up
to 200 references, with bounded previews of the original Claim, anchors and
shortest path. The JSON response is capped at 7 MiB; if representatives alone do
not fit, the route fails rather than silently dropping a cluster.

`queryRelation` is the candidate's relation to this query. `assessment` is the
latest verifier judgment of the Claim against its own paper. They are separate
concepts and must never be relabelled as each other.
