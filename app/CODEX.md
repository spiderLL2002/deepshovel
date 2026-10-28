# Application module

Follow the root [engineering contract](../CODEX.md) and [architecture](../.codex/architecture.md).

## Responsibility

- `workspace.tsx` coordinates browser reads, explicit user writes, and five
  discrete workspace views: corpus search, library, Evidence knowledge graph,
  paper detail, and optional human quality control. It must not collapse them
  back into one long page.
- `components/evidence-search.tsx` is the global/query-Claim retrieval surface.
  Search results must retain source Artifact/ArtifactVersion identity and a path
  back to the immutable graph snapshot.
- `components/library-view.tsx` renders one compact row per logical Artifact and
  consumes status-filtered, cursor-paginated library data. It defaults to the
  completed evidence collection, supports explicit multi-selection/batch Compile,
  and keeps pending work in its own filter. Exact version, hash, and network-boundary
  details stay secondary to corpus status and evidence scale.
- `components/paper-detail.tsx` owns the focused Claim/evidence, source-location,
  and run-record tabs. It groups each Observation with its direct source anchors
  and original visual excerpts before presenting reasoning or assumptions. It
  may launch a Claim-seeded global search.
- Citation Reception loads independently from paper detail and is embedded
  after each Claim's own Evidence Path. `api/citation-reception` alone resolves
  and processes local A→B pairs; the browser may request processing but never
  submits model-authored assertions. A reception failure must not fail the
  immutable paper-detail request.
- `components/scientific-content.tsx` is the only renderer for untrusted
  paper/model Markdown and LaTeX. Raw HTML remains disabled. Reliable table cells
  may render as a table; collapsed OCR rows must be labelled as a machine
  transcription and paired with the original region instead of being called
  cells.
- `scientific-markdown.ts` performs the small, deterministic display-only
  normalization shared by every scientific text surface. It accepts common
  `\\(...\\)`/`\\[...\\]` delimiters, keeps code and existing math untouched,
  and conservatively isolates known parser-style TeX expressions between prose
  boundaries. Paths, URLs, escaped backslashes, and unknown commands remain
  literal; normalization never mutates stored evidence.
- `components/pdf-page-preview.tsx` opens an exact-page, same-origin PDF modal;
  `pdf-region-preview.tsx` lazily renders only a trusted normalized visual bbox
  with PDF.js. The original PDF is authoritative; a crop never becomes new
  structured evidence.
- `components/review-workspace.tsx` is the optional human quality-control branch;
  it is never the default landing view or a prerequisite for evidence visibility.
- `components/activity-view.tsx` presents upload, `compile`, and import progress
  as a normal workspace view. Active uploads and
  Compile jobs expose single-task and cancel-all actions; a cancelled Compile
  must be made terminal at both the gateway and Compiler boundaries.
  One exact ArtifactVersion has one visible lifecycle: its completed upload is
  superseded once Compile starts, and a newer Compile supersedes old terminal
  rows. Identity compaction never compares filenames or hides active work.
- `upload-scheduler.ts` bounds independent browser uploads to two active
  requests across every file-picker and drag/drop batch. New batches may be
  appended while earlier files are active; cancellation must also release queued
  work. Multiple versions for one Artifact remain ordered even when they are
  appended from separate picker batches.
- `compile-polling.ts` serializes lightweight gateway submissions across UI
  batches, supports identity-based cancellation of one queued/submitting task,
  invalidates submissions that have not started when all work is cancelled, and
  submits each logical job with one stable idempotency key and at most three
  attempts for network/408/425/429/5xx failures. A POST that returns after local
  cancellation must have its exact returned job cancelled. It refreshes every
  active gateway job through the state-advancing `job_id` boundary with bounded
  worker concurrency and no overlapping timer. Contract errors and other 4xx
  responses are never retried. The job list must include every active job plus a
  bounded recent terminal window.
- `upload-scheduler.ts` keeps one global two-upload limit across appended UI
  selections. Each logical upload reuses one idempotency key for at most three
  attempts, retries only network/408/425/429/5xx failures, honors bounded
  `Retry-After`, and must remain immediately abortable while waiting.
- The public UI never deletes papers. The library has no delete actions, and a
  failed task row offers only "移除记录" (remove the record from the task list),
  which never touches the paper. Whole-Artifact purge exists only as local
  maintenance (`api/internal/artifacts`, loopback only).
- `api/artifacts/` is the only Artifact/ArtifactVersion upload and download boundary.
  Downloads remain attachments by default. The exact PDF-only
  `?disposition=inline` mode supports one byte range so browser/PDF.js previews
  can seek without weakening the ordinary download contract.
- `api/health/` is the cheap, public, non-sensitive liveness boundary used by
  launchd, Cloudflare Tunnel, and the macOS controller. It must not query D1,
  R2, Compiler, MinerU, or GPT.
- `api/library/` is the read-only, keyset-paginated corpus listing boundary. It
  returns one row per logical Artifact without N+1 reads or graph JSON scans.
- `api/compile/` owns actor-scoped Compiler job orchestration. It may mutate `compile_jobs`, but only the evidence import boundary may create immutable graph snapshots.
- `api/data-quality/` owns the authenticated Web-to-Compiler Data Quality
  projection. New PDF Compile starts it automatically from shared parser output;
  historical PDF checks start only from explicit user intent.
- `api/compile/compile-store.ts` owns trusted ArtifactVersion loading and Compiler/result orchestration; `compile-job-store.ts` owns the D1 state machine, compare-and-set leases, idempotency reservations, and stable audit IDs.
- `api/evidence-graphs/` validates, imports, reads, and reviews immutable graph snapshots.
- `api/evidence-search/` resolves the authoritative active D1 corpus, queues
  snapshots the authenticated v2 retrieval service cannot search yet (never
  indexing on the request), searches there, then batch-hydrates bounded Claim/evidence/
  source previews and latest independent assessments from D1. It has no legacy
  D1 ranking fallback and never selects `graph_json`.
- `api/evidence-synthesis/` normalizes a user viewpoint or accepts one selected
  stored Claim, uses Evidence Search only for bounded candidate recall, then
  loads each selected Claim's exact immutable Evidence Path for an independent
  comparison extractor/verifier. It never promotes similarity into a relation,
  writes a viewpoint into the corpus, or counts the source paper family against
  itself.
- Snapshot reads remain fail-closed under newer evidence invariants. A frozen
  legacy snapshot that no longer satisfies the current contract returns the
  non-retryable `STORED_SNAPSHOT_INCOMPATIBLE` response with a safe validation
  code/path and an explicit `recompile` action; it must never degrade into a
  generic store error or be silently normalized at read time.
- `api/evidence-graphs/import-contract.ts` owns the exact Compiler import envelope and all graph/verifier semantic checks.
- `api/evidence-graphs/import-store.ts` owns D1 provenance checks, transactions, audits, and import idempotence; route files only orchestrate HTTP.
- `workspace-types.ts` contains browser-facing DTOs only; scientific domain types come from `lib/evidence-graph`.

## Rules

- Never call GPT or the laboratory MinerU endpoint from browser code or a Cloudflare route.
  Those outbound calls belong to the standalone Python Compiler.
- Compile accepts stored PDF, DOCX, XLSX, PNG, JPEG, and TIFF bytes only. Artifact type must agree with version media; re-read from R2, revalidate signature/extension/size, and recompute SHA-256 before any upstream request.
- The artifact surface must disclose that `compile` sends original bytes to the laboratory MinerU
  service and normalized document content to GPT.
- Treat Compiler IDs, state, timestamps, errors, and result provenance as untrusted protocol input. Jobs and list/poll reads are scoped through the shared Sites actor boundary (including its explicit local-development owner fallback).
- Compiler `created_at` remains immutable, and `expires_at` remains immutable
  before a terminal state. A `completed` or `failed` response may only extend
  expiry monotonically to a finite instant no later than 25 hours after its
  `updated_at`; shortening or unbounded retention fails closed.
- A Compiler completion first acquires a gateway-clock import lease, then passes the exact result through the ordinary evidence import contract. Compile results must use the official extractor/verifier run metadata and assess every Claim exactly once. Never write verifier assessments into `graph_json`.
- A running import holds an explicit lease and cannot be cancelled into a failed
  job after its immutable snapshot has begun committing; an import waiting between
  failed attempts can be stopped.
- Passing the import contract atomically persists the newest D1 retrieval
  manifest/documents together with their `work_items` outbox rows. The work pump
  indexes the snapshot and analyses its Claim relations; no human action and no
  open browser tab may be required between successful import and retrieval.
- `api/internal/work` is the only place background work runs; `GET /api/compile`
  and `GET /api/work` are read-only observers.
- Never regenerate scientific IDs in UI components.
- Uploading the same bytes reuses an ArtifactVersion; adding changed bytes to an existing Artifact creates the next version.
- A graph import must validate the complete graph and confirm its referenced
  ArtifactVersion exists before D1 persistence. One snapshot owns exactly one
  source Artifact and one root ArtifactVersion; cross-paper composition belongs
  in retrieval, not in a multi-source immutable extraction snapshot.
- Human review is optional, append-only metadata and requires a rationale. It never gates import,
  indexing, or retrieval. A `withdrawn` event clears the derived current human status to unreviewed
  without deleting history; one idempotency key remains bound to the exact snapshot and normalized payload.
- The primary navigation and default visual hierarchy must emphasize corpus search,
  library management, the Evidence knowledge-graph library, and evidence inspection.
  Human review stays a lower-emphasis optional branch; upload/Compile activity has
  its own workspace view.
- A verifier verdict describes a candidate Claim against its own frozen paper evidence.
  Never present it as the candidate's stance toward a user's query. Only an explicit
  query-relative graph relation may be labelled support, contradiction, or direction.
- A never-processed version uses `Compile`, a failed first attempt uses
  `重试 Compile`, and a version with an existing snapshot uses `重新 Compile`.
  A successful Compile means the
  evidence graph passed machine validation and was automatically imported; it does
  not mean a researcher manually approved the snapshot.
- Empty, loading, failure, disabled, focus, mobile, and reduced-motion states are part of the interface contract.
- An original PDF crop is the default visual evidence. Machine table/figure
  transcription stays collapsed and explicitly auxiliary so it cannot compete
  with, or appear to be part of, the source screenshot.

## Verification

Run `npx tsc --noEmit`, `npm run lint`, `npm run test:node`, and `npm run build` after application changes.
