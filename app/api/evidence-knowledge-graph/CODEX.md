# Evidence knowledge-graph API

Follow the application and root engineering contracts.

- Active `evidence_retrieval_documents` are the only Claim-node source. Never
  copy Claim text into another authoritative table.
- No completion row means unprocessed. Write completion only after retrieval,
  GPT classification, and edge persistence succeed together.
- `supports`, `refines`, and `qualifies` decisions carrying an explicit
  one-way relation direction become legacy-named `directly_implies` edges. `equivalent`,
  `contradicts`, and `supplements` become symmetric `equivalent_to`,
  `contradicts`, and `supplements` edges with canonically ordered endpoints.
  `related` and `unrelated` produce no edge.
- Never persist a relation between Claims from the same paper family. One
  unordered cross-paper Claim pair has one stable identity and at most one
  stored relation. Ignore a directed insertion that would close a cycle.
- A processed Claim may produce zero edges. Do not infer scientific truth from
  the shape of the resulting mixed directed/symmetric graph.
- Relation analysis is durable background work (`claim_relations` work items,
  `app/api/internal/work/claim-relation-work.ts`). The Compiler batch lives only in
  its memory; durable progress is the completion rows, so a lost batch is replaced
  by a new one for the still-unprocessed Claims. A batch is looked up before it is
  created, at most four run at once, and a failed classification is never recorded
  as a completion.
- `batch/` only requests (`POST`), observes (`GET`) and cancels (`DELETE`) that work.
  There is no single-Claim processing endpoint and no relation-deletion API.
- Persisting a reprocessed result replaces that Claim's incident edges together
  with its completion only after the new classification succeeded.
- Paper classification comes only from the OpenAlex Work API or OpenAlex's
  official title/abstract model. Claims inherit every Topic assigned to the paper.

## Bounded reads

- Graph GET reads `evidence_active_snapshots` and the incrementally maintained
  `graph_*` metadata projections. Never reload every manifest, send the corpus
  snapshot list as a JSON bind, or aggregate all Claims/edges on a browsing request.
- Global and taxonomy counts are exact differential aggregates. Taxonomy membership
  counts deduplicate paper ancestors, preserve every Topic, and distinguish the
  primary Topic. Activating/deactivating a snapshot adjusts counts and incident edges.
- Claim text still comes from the canonical retrieval document; list and neighborhood
  hydration select only the displayed Claim/title fields, never complete evidence JSON.
- Pagination uses `nextCursor`, bound to status, taxonomy scope, and search text.
  `page=1` remains accepted; later pages require the cursor. A scoped page is driven
  by ordered taxonomy memberships, then bounded Claim slices within each snapshot.
  Official projection writes (including the v2 migration) give documents and their
  manifest the same `created_at`, preserving the shared snapshot/Claim sort order.
- Explicit substring search advances at most 250 Claim candidates per request.
  Empty result windows may have `hasMore=true`; `searchInfo` marks this state,
  and search totals are null rather than pretending to be corpus-wide match counts.
- `view=neighborhood` loads only the requested active Claim and up to 200 incident
  edges. It does not reload taxonomy, global counts, or the Claim list.
- Retrieval/model-processing POST and batch polling retain the existing Compiler
  corpus protocol; bounded graph browsing does not imply that protocol scales to
  a 500,000-paper corpus.
- OpenAlex backfill reads the indexed `graph_unclassified_snapshots` queue and
  hydrates only the selected papers. A completed corpus exits after one count-row
  read. Activation, deactivation, and classification changes maintain the queue.
- A small `json_each` input alone does not bound a join: hydration must drive from
  materialized requested refs using cross joins and exact composite primary-key
  lookups. Query-plan regressions protect against D1 choosing a project-wide scan.
