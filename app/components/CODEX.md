# Evidence UI components

Follow [the application contract](../CODEX.md). Components render DTO/domain
objects and emit user intent; they do not read D1/R2, call Compiler/MinerU/
GPT, or create scientific identities.

## Evidence presentation

- Preserve the order `Claim → Observation → direct source anchor/original
  visual → Reasoning → Assumptions`. Do not return to a flat list sorted only by
  entity type.
- Render all paper/model prose through `ScientificContent`; Markdown and LaTeX
  are supported, while embedded raw HTML and executable content remain disabled.
- Raw verifier rationale and limitation prose are internal records, not
  user-facing copy. Present only the localized verdict and confidence, a safe
  boundary count/note, and the concrete evidence path; never expose graph IDs
  or contract verdict tokens.
- A figure/table caption and normalized bbox are navigation provenance, not proof
  that pixels or cells were machine-verified. The original PDF crop is primary;
  machine structure is a clearly labelled secondary transcription.
- Never call a collapsed OCR row a table cell. If reliable row/column coordinates
  are absent, show the row text with a warning and direct the reader to the crop.
- `打开原始页` opens the exact 1-based page in the temporary modal. Ordinary
  downloads remain a separate action.
- Keep the PDF.js document cache small and LRU-bounded. A crop render owns a
  lease; failed loads leave the cache, and eviction may destroy only an idle
  loading task/document, never one used by an active render.

## Interaction

- Human review stays in the run-record/quality-control branch and never gates
  Compile, import, indexing, or search.
- Every collection that can grow with corpus size must use server-backed
  pagination instead of rendering or downloading the full collection. Reuse
  `PaginationControls`, default to 20 rows, offer 10/20/50 rows, expose total
  count when available, and reset to page 1 when search/filter state changes.
  Corpus directories use previous/next cursor navigation with a local page
  ordinal; never emulate numeric jumps by walking every intervening page.
  Unknown totals and empty search windows with a continuation must remain navigable.
- Figure/table libraries default to 5 rows and their API returns only the
  requested page of records; never fetch a complete Evidence Graph and slice
  it in the browser.
- OpenAlex taxonomy is loaded and cached separately from Claim pages. Domain,
  Field, and Subfield navigation must stay client-local; only selecting a Topic
  may request its paginated Claims, without re-querying the complete taxonomy.
- Search and filter controls for a scrollable ledger stay fixed at the top of
  that ledger's scrolling region. The result list scrolls independently; adding
  rows must never move the search controls or require scrolling back to find them.
- Preserve semantic heading order, roving keyboard tabs, visible focus, 44 px
  touch targets, modal focus return, mobile overflow, and reduced-motion rules.
- Avoid nested decorative cards. Use spacing, a source rule, and typography to
  communicate evidence hierarchy.
- Within a Claim panel, place Citation Reception after the paper's own Evidence
  Path and before Claim actions. Keep A's exact citation context visibly
  separate from A's own confirming evidence; missing independent evidence must
  remain labelled as prose-level endorsement or review-needed.

## Retrieval presentation

- Render v2 search output as proposition clusters grouped by the response's
  query-relative relation. The response intent controls group order: a completed
  contradiction search starts with contradictions, while a support search starts
  with support/equivalence. Editing the next query must not reorder old results.
- Preserve `distinctPaperFamilies` even when the bounded member preview is
  shorter. Every cluster keeps its representative; additional members, anchors,
  original-claim text, and evidence paths are explicitly bounded previews with a
  route back to paper detail.
- Query-relative support/contradiction and the paper-internal verifier assessment
  are different concepts and must remain separately labelled.
- The primary form exposes the research question, retrieval intent, and corpus
  scope. A selected Claim or OpenAlex node may narrow the search.
- Interactive `2.3` search pauses on `clarification_required`, renders its
  question and options inline, and never launches final while waiting. A user
  choice submits the frozen original request with the signed option identity;
  it does not replace the original query with model text. Editing the question,
  intent or scope invalidates that pending choice. Keep cancellation and stale
  response guards. Preview/final share one limit50 request while the service
  preview remains independently capped at12.

## Evidence synthesis presentation

- `evidence-synthesis.tsx` is a second workflow inside Evidence Search, not a
  sixth global navigation destination. It accepts selected terminal paper
  Claims or normalized user viewpoints and keeps every result scoped to one
  source Claim.
- Re-group the service response conservatively on the client as a fail-closed
  display check. Only candidate-to-query support with a comparable Scope may
  appear as support; exact-scope contradiction alone appears as substantive
  conflict. Partial/narrower results are applicability boundaries, while
  abstention, insufficient information, unknown/disjoint Scope, or missing
  provenance remains unconfirmed.
- Each published row retains paper-family identity, verifier rationale and
  limitations, selected evidence entities, exact anchors, and the shortest path.
  Opening an original file in a new tab and returning from full paper detail
  must not destroy the in-progress synthesis ledger.
- The source-Claim overview is deterministically derived from the verified
  groups and always says “current corpus/current candidate set”; never invent a
  truth score, majority vote, or whole-paper true/false label.

## Corpus Claim relationship graph

- The graph library renders active retrieval Claims and all six persisted Claim
  relations; it never edits a paper snapshot or treats a model relation as
  source evidence. Directed support/refinement/qualification uses arrows;
  symmetric equivalence/contradiction/supplement uses arrowless, labelled lines.
- A processed Claim may legitimately have zero edges. Merely related and
  unrelated candidates are not scientific Claim-relation edges.
- Claim-relation processing belongs to the Compile workflow and starts as soon
  as each paper finishes compiling.
- The graph reads persisted relations and does not expose a separate manual
  batch, reprocess, or relation-deletion workflow.
- Navigate the exact OpenAlex `Domain → Field → Subfield → Topic` hierarchy as
  one stable radial panorama. Build its coordinates and flat typed arrays in a
  worker, render taxonomy and Claim-distribution points through the WebGL graph
  layer, and keep only the current selectable level as keyboard-accessible HTML
  labels. Claim points are derived from primary-Topic aggregate counts so one
  Claim produces one panorama particle, and never load Claim text; exact Claim
  content remains paginated at Topic. Preserve the
  root-to-leaf path as accessible breadcrumbs, focus the selected branch with the
  graph camera, keep the renderer mounted while navigating, and provide a bounded
  HTML fallback when WebGL is unavailable. Every hierarchy level can launch the
  existing retrieval flow with that node as its corpus scope.
- Selecting a Claim renders only its readable first-order incoming/outgoing
  neighborhood and source-paper actions. Do not infer, test, or label the shape
  of the whole corpus graph.
