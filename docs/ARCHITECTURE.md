# Scientific evidence graph architecture

This is the authoritative system map. Domain field details live in the executable TypeScript and Pydantic models; this document owns boundaries and data flow.

## What the system is

DeepShovel restores the argument structure of a paper and later aligns that structure across papers. It is not a knowledge-graph wrapper around document chunks.

### Paper Evidence Graph

One immutable extraction snapshot contains:

- logical Artifacts and byte-addressed ArtifactVersions;
- SourceAnchors that locate a page, quote, figure region, curve segment, table row/column/cell, dataset slice, or code range;
- structured Figure/Table evidence;
- Observations limited to what was measured or visibly reported;
- explicit Assumptions and Reasoning;
- AtomicClaims with structured Scope;
- typed, directed edges that preserve the complete evidence path.

### Corpus Evidence Graph

Cross-paper work aligns claims only after structural and Scope filtering. Persisted relations are `equivalent_to`, `supports`, `contradicts`, `refines`, `qualifies`, and `supplements`. `unrelated` and `new` are candidate-routing results, not graph edges.

The user-facing Evidence knowledge-graph library is a rebuildable projection
over the active retrieval documents. A Claim with no completion row is pending,
so newly imported Claims appear automatically. Its edges record directed
`supports`, `refines`, and `qualifies` implication plus symmetric
`equivalent_to`, `contradicts`, and `supplements` comparison. `related` and
`unrelated` are deliberately omitted. Accepted candidate relations are stored
without treating topology as scientific truth. This never mutates the immutable paper
Evidence Graph.

An assessment verdict is separate from a claim relation: `supported`, `contradicted`, `qualified`, `uncertain`, or `insufficient_evidence`.

## End-to-end flow

```text
PDF / Figure / Table / Dataset
  → R2 immutable bytes + D1 ArtifactVersion metadata
  → actor-scoped compile job + byte/signature/provenance checks
  → standalone Compiler uploads the source to the allowlisted laboratory MinerU HTTPS service
  → MinerU Markdown/content_list/middle JSON normalized to stable DocumentIR coordinates
  → one whole-document GPT extraction (strict JSON)
  → hard structure/identity/quote/region/path invariant validation
  → deterministic semantic/scoped IDs
  → immutable Paper Evidence Graph snapshot
  → independent GPT verification for every nonzero-Claim graph
  → OpenAlex Work Topics, or OpenAlex's official local title/abstract classifier
  → immutable per-Claim Assessment report (zero Claims may use null)
  → server-side TypeScript structure/hash/coverage validation on import
  → D1 snapshot metadata + human review events
  → deterministic bibliography/inline-marker `citation-source/1.0` projection
  → DOI/OpenAlex-authenticated local A→B pairing on demand
  → independent CitationAssertion extraction + hash-bound faithfulness verifier
  → OpenAlex Domain / Field / Subfield / Topic navigation
  → taxonomy-scoped claim retrieval + evidence-path expansion
```

The repository implements this path end to end for the supported compile media. The Web tier never
imports MinerU or a model SDK: it stores bytes, submits a provenance-bound job to the loopback
Compiler, observes a small status object, then validates and atomically imports the completed envelope.

### Background work

All long-running Web work is durable and server-side; the browser only observes it.
Compile jobs (`compile_jobs`) and the transactional outbox `work_items` (retrieval
indexing and Claim-relation analysis, written in the same D1 batch as the import that
creates them) are advanced by one bounded pass, `POST /api/internal/work`, which the
installed `compile-sync` LaunchAgent (the work pump) calls in a loop over loopback with
the shared service credential. The pump holds no state and never reads either database.
Every step persists its progress: leases with expiry, attempts with backoff, the last
failure cause and per-item progress (index chunk position, Claim-relation batch cursor).
GET endpoints never advance work.

- Submission sends the gateway job ID as the Compiler idempotency key plus a creation
  deadline. A lost response is reconciled by key after that deadline (adopt the job, or
  fail it as retryable once the Compiler proves none exists); a POST is never replayed.
- Completed results are copied to R2 before the first import attempt. Import failures
  are classified; transient ones retry with backoff up to a bound, permanent ones fail
  with their real cause and never trigger a paid re-run. A maintainer can re-import a
  retained result (`POST /api/internal/compile`).
- Searches never index. They hand snapshots the retrieval service cannot search yet to
  the queue and report them; one unindexable snapshot no longer fails searches.
- Claim-relation batches live only in Compiler memory; their durable progress is the D1
  completion rows, so a lost batch is replaced by one for the Claims still pending.

The Compiler is separately deployable and durable across restarts: queued jobs restart
from their saved input, interrupted parses re-run, and interrupted extraction or
verification resumes from the saved DocumentIR (bounded to two recoveries per job). It is
the repository's only document parsing, extraction, canonicalization, and verification
implementation.

Compiler 1.0.0 performs one whole-document graph extraction followed by a
separate verification phase for every nonzero-Claim import; a zero-Claim import
may omit the empty report. This is the only supported prompt pipeline.

Claim quantity follows the source's natural number of distinct paper-level
terminal conclusions only. There is no target, minimum, preferred range, or
top-level content count ceiling. Zero Claims is valid when no central conclusion
has a complete evidence path, and a long paper or book may return many Claims
only when it genuinely contains many separate main arguments. Local measurements,
formulae, parameter sweeps, and intermediate findings remain evidence or
Reasoning rather than becoming Claims. Upload bytes, MinerU response bytes,
compact DocumentIR bytes, deadlines, and nested visual collection limits remain
bounded. GPT requests have no application output-token cap; DeepSeek fallback requests
allow 65,536 output tokens, its native maximum. Crossing another transport boundary fails the job rather than silently
trimming Claims to a quota.

The active graph is an argument DAG, not a set of disjoint one-sentence chains.
The extractor identifies terminal conclusions first and works backward through
theory, assumptions, direct experiments, figures/tables, Observations, local
interpretations, and synthesis. Reasoning may feed later Reasoning; several
evidence branches may converge before the terminal Claim. Every Reasoning node
must reach a Claim, while a Claim is never an upstream derivation premise.

Compiler 1.0.0 retains hard structure and provenance admission: closed schema,
Artifact/Version and scoped Claim identity, exact quote and region lookup,
references, Observation path ownership, derivation DAG, and canonical/snapshot
hashes must all validate. Scientific value, comparator, unit, and formula
semantics are deliberately not whole-graph admission gates. The independent
verifier compares them per Claim, treats benign MinerU/OCR/LaTeX presentation
differences as equivalent, lowers the affected verdict, and records real
disagreements in `limitations`. It never calculates, converts, repairs, or
rewrites a scientific result.

Table cells and plotted points in `StructuredVisualEvidence` are provenance,
not free model text: each visual must bind to exactly one source block (source
span, or unique page/region), every cell value must occur in that block's
trusted structured template (`structured_source`, which honours
`rowspan`/`colspan` and benign inline tags), a contradicting `numeric_value` is
rejected, and table coordinates are Compiler-owned (a value found at other
coordinates is moved to its true cell and audited).

The independent verifier remains hash-bound and exact-once for every retained
nonempty Claim set, and its report is required for a nonzero-Claim Compiler 1.0.0
import. Verifier mistakes are verifier errors: every individually valid
assessment is kept across fresh verifier attempts, an unknown or malformed
assessment is reported and requested again, and a Claim leaves the graph for
verification reasons only after the bounded attempts are exhausted. The
Compiler may discard an independently identifiable bad Claim plus only its
exclusive evidence closure, with a cumulative budget of 20% of the original
Claim set (rounded down, so extractions with fewer than five Claims get no
tolerance; the sole Claim is never converted to an empty success). One rule
decides what is local: a node on no Claim's Evidence Path is pruned; a malformed
record with a usable ID removes the Claims that depend on it; a located
structural, provenance, or canonical violation removes the Claims that depend on
its location (for an edge, its target). Global or unmappable failures and excess
loss trigger a fresh whole-stage retry.
Reasoning -> Claim conclusions keep their stance (`infers` for support,
`contradicts`, `qualifies`). Every Compiler rewrite of model output -- span
hydration, re-typed anchors, cleared selections, linked visual anchors,
coordinate hydration, removed Claims, merged
verifier attempts -- and every unavailable optional projection (OpenAlex,
citation source) is listed in the envelope's value-free `compiler_audit` and
persisted with the import audit event. Verifier verdicts and scientific/formula findings are annotations:
`contradicted`,
`uncertain`, or `insufficient_evidence` does not by itself reject the extraction
or rewrite the frozen graph. Report shape, Claim coverage, scoped identity, and
on-path evidence references remain strict. Zero Claims requires no assessment
and may import with `verification: null`.
Late results after cancellation/deadline are discarded, and the Web imports a
completed graph/report/search envelope atomically, never a partial result.

Only Compiler 1.0.0 envelopes are accepted for import.

Citation Reception remains outside the Paper Evidence Graph. During ordinary
Compile, deterministic code records bibliography entries and exact high-value
inline citation contexts in a second job result, without changing graph JSON or
its hash. After Web import, a reference may be paired to local B only through an
authoritative OpenAlex Work identity and exact DOI; ambiguous local matches
abstain. A pair-level model extracts what A attributes specifically to B, then
an independent verifier checks A's context against B's frozen evidence entities
and distinguishes prose endorsement from A's own confirmation evidence. Source
facts are immutable; pair runs are rebuildable. Their failure never makes B's
own evidence page unavailable, and their events are not Claim graph edges.

The laboratory parser is an external trust boundary. Compile sends the original source bytes to
`https://mineru.acemap.cn/file_parse`; the UI and deployment docs must say so. The Compiler requests
only Markdown, content list, and middle JSON, bounds the response, validates the embedded JSON, and
ignores all task/status/result URLs returned by that service. Parser output is fallible metadata, not
scientific evidence; critical tables, figures, OCR, and coordinates still require comparison with the
immutable original artifact.

## Identity

- `Artifact.id`: stable logical object chosen by ingestion.
- `ArtifactVersion.id`: content-addressed from the exact byte SHA-256.
- `proposition_key`: normalized proposition text without Scope; useful for recall.
- `scoped_claim_id`: proposition plus normalized Scope; this is the scientific claim identity.
- `Snapshot.id`: content-addressed from canonical graph JSON.

IDs never substitute for provenance. A Claim still needs a path through Reasoning and Observation to a SourceAnchor.

## Storage now and later

The current Web application uses R2 for bytes and D1 for relational metadata, compile-job state,
immutable graph JSON, verification JSON, search projections, and append-only review/audit events.
The Compiler uses its own SQLite/job-file store and does not share the Web database. This is a
deployment choice, not a domain-model compromise.

The current public Mac runs local D1/R2 through workerd: D1 is a persistent SQLite
database, and R2 bytes are local object files. Cloudflare Tunnel only publishes
the Web endpoint; it does not move this storage to Cloudflare's hosted D1 service.

Interactive directories read compact projections maintained during writes.
Library browse and review history use indexed keyset cursors; name search uses
contentless FTS5 Unicode character postings plus exact substring checks within
a bounded candidate window. Exact browse totals come from maintained counters;
search results expose a continuation instead of computing an exact corpus total.
Active evidence is selected once per affected family/paper when its immutable
manifest completes. Graph totals and OpenAlex topic counts are incremental;
topic pages first select a bounded number of member snapshots, then bounded
Claim references. A single-paper detail still reads and validates that snapshot.
These read projections are disposable, and preserve zero-Claim activation and
the original source/verification/review histories.

The browser cancels obsolete requests, retains bounded page caches, and loads
Atlas only when selected. Compile-all is an explicit paged producer with bounded
in-flight work. It does not run as a side effect of entering the task page.

At corpus scale, keep object storage for bytes, move normalized graph records to PostgreSQL, add pgvector for candidate retrieval, and treat a graph engine as an optional projection. The graph model remains the source of truth; Neo4j is not required.

## Local Mac public deployment

The public runtime is generated from `deploy/local-public.json`. It installs an immutable
application subtree under `~/Library/Application Support/DeepShovel/app` while keeping D1/R2 state,
Compiler jobs, secrets, logs, and Cloudflare credentials in sibling persistent directories. Updates
replace only the application subtree and never overwrite persistent data or installed secrets.

One launchd service group supervises the loopback-only Compiler and built Web Worker; a separate
named Cloudflare Tunnel publishes only the Web origin. The Compiler is never a public ingress.
The hostname currently follows Huishu's public-access model and does not enable Cloudflare Access.
The manifest keeps that decision explicit; a later private deployment must add Access or application
authentication rather than treating Tunnel transport as authorization.
A native menu-bar app reports application, local health, Tunnel, and public health as independent
signals and controls only the exact DeepShovel LaunchAgent labels.

## Retrieval boundary

Correct retrieval is:

```text
question / seeded Claim
  → bounded query planning and explicit hard filters
  → BM25F lexical recall + multilingual dense ANN recall
  → reciprocal-rank fusion → Cross-Encoder reranking
  → model ordering, unrelated-candidate filtering and query-relative relations
  → paper-family collapse + non-transitive proposition clustering
  → D1 Claim/assessment/source hydration → Evidence Path/raw artifact expansion
```

Current interactive search uses `2.3` with user history, clarification and context continuation.
The compiled task binds the original request, explicit selection and source inventory before recall.
A signed handoff preserves the exact candidate order and task for final relation classification;
final does not repeat planning or recall. Pending clarification is not an empty evidence result.
Protocol `2.2` is retired end to end (Web 2026-10-18, Compiler 2026-10-19: c1 tokens,
its planner prompts and configuration identity are gone; 2.2 requests, responses and
handoffs are rejected). Synthesis previews and finals use `2.1` with the sealed handoff,
server-side. With interactive search enabled a handoff-free `2.0` final and a planner
failure are errors, never a locally sealed plan. The browser holds only opaque `rs1.`
session IDs; the Compiler-signed v1/n1/c2 tokens stay in D1 `retrieval_sessions`
(migration 0026, 30-minute expiry).
Superseded task-interpreter implementations and historical experiment programs are removed.

`evidence-retrieval/2.0` is a rebuildable projection, never scientific truth. D1 atomically stores one
self-contained search document per AtomicClaim plus independent assessment and snapshot manifest rows;
the Compiler retrieval service owns SQLite FTS5, the vector index (embedded Qdrant by default, or the
disk-backed float16 store of `retrieval_vectors.py` selected with `RETRIEVAL_VECTOR_BACKEND=numpy`),
FastEmbed providers, query planning, fusion,
reranking, relation classification, and clustering behind authenticated versioned HTTP. Import never
performs a non-atomic external index write: it records a `retrieval_index` work item in the same D1
batch, and the background work pump indexes the snapshot. The search gateway resolves the active D1
corpus, checks which snapshots the service can search, queues the rest and reports them.

The newest snapshot per stable paper Artifact is active, including zero-Claim snapshots. A historical
seed Claim replaces only its own paper family. Service results are compact `(snapshot_id, claim_id)`
references and are accepted only when they belong to that D1-resolved corpus; canonical Claim, exact
source quote, anchors, shortest Evidence Path, source metadata, and latest assessment are then hydrated
in bounded batch queries. `graph_json` is not read on the hot path.

Interactive results use the model’s returned relations and order; structural validation never
substitutes heuristic labels. Failed model stages remain errors. Legacy batch retrieval keeps its
existing graph-edge and conservative relation rules. Paper-internal verifier assessments are display-only
for retrieval and never affect relevance or stance. Passage text may enrich Claim recall, but it is never a
final evidence unit and cannot bypass graph/path expansion.

## Shared implementation and clean initialization

Cross-language contract values have one source, `contracts/shared-contract.json`
(protocol and prompt versions, Claim type/polarity/modality, reasoning-conclusion
relations, retrieval transport and context limits, continuation key/TTL, the
normalized-region tolerance shared since GRF-6, and the `rs1.` session shape).
`scripts/generate-shared-contract.mjs` writes `lib/shared-contract.generated.ts`
(an import-free leaf the pure domain modules may use) and
`compiler/compiler_service/shared_contract.py`. `tests/shared-contract.test.mjs`
fails on a stale module; `compiler/tests/test_shared_contract.py` fails when a
hand-written Python Literal disagrees; both run the golden cases in
`contracts/shared-contract-cases.json`. Deeper DTO validators are still written
per language (see the handoff for what remains).

The Web Compiler, retrieval and classification clients share streamed JSON byte limits and
credential-reflection checks in `lib/server/service-response.ts`. Request-specific errors and
contracts remain at their respective boundaries. Fast local recall is a server adapter, not a
domain-module dependency on Compiler code.

Browser upload paths share the same request/retry boundary and retain an idempotency key across
retries. Data Quality polling stays serial, continues through unchanged running statuses and is
cancelled when its ArtifactVersion changes.

`ModelJsonClient` is the single model JSON/contract boundary. GPT uses direct Responses
with the application User-Agent `DeepShovel/1.0` and stage-specific reasoning. Batch
extraction retains six GPT attempts and up to three DeepSeek V4 Flash attempts for
availability failures. Each interactive completion starts GPT and DeepSeek together,
then starts a second and third pair after 10 and 20 seconds if no valid answer has
arrived. Existing requests continue; six attempts is a count bound, not a deadline.
The first complete validated answer wins and the other network requests are cancelled.
Interactive SDK and Web requests have no application time cutoff. HTTP caller
cancellation reaches the model worker and closes its active streams.

The ordinary interactive flow makes one task-planning completion followed by one
candidate-ranking completion. Planning includes clarification and missing-context
choices; it no longer asks a second model to reinterpret the same task. Ranking
sends the question, necessary context, numbered complete Claims and their scopes,
then reads one compact ordered list of relations. Schema and source-identity checks
remain local; no second model or heuristic layer rewrites the returned relations.
Short interactive prompts use GPT medium and DeepSeek without extended thinking.
Batch extraction, its verification, and its provider settings remain unchanged.
The fixed authenticated interactive header is set only by interactive Web clients,
never copied from user headers or enabled for corpus jobs.

Interactive answers require a successful GPT or DeepSeek result. If both providers
fail, return an explicit operation error, never a successful local D1 recall or
unassessed preview answer. Quick recall is provisional loading feedback only;
the browser removes provisional candidates on model failure and rejects malformed
response shapes. Authentication, handoff integrity and cancellation remain enforced.
The former default-off strict-citation answer/capture feature was removed.

`make runtime-clean` inventories owned data and historical backups; explicit apply requires the
project services to be stopped. Reset clears originals, processing results, D1/R2, derived indexes,
logs and old caches, then deployment initializes empty schemas. Atlas samples, sources, anomaly
results and geographic basemap are protected reference data: they are excluded from document-result
and cache cleanup and must not be reset to an empty dataset. Model resources and current language
dependencies are runtime prerequisites. Cleanup does not rewrite Git history or other projects.
