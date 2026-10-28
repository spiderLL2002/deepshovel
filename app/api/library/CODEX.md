# Library API

Follow the application [module contract](../../CODEX.md) and root
[engineering contract](../../../CODEX.md).

`GET /api/library` is the read-only, corpus-scale listing boundary for the
research library. It returns one compact row per logical Artifact and uses an
opaque keyset cursor over immutable Artifact creation order (or stable integer
search order for name matches). Derived activity
is exposed as `updatedAt` without destabilizing pages. The route must never
reintroduce a fixed global corpus cap, full-table count, per-row network query, or
graph JSON scan. Downstream CTEs must remain constrained to the keyset page.

The request accepts bounded `query`, opaque `cursor`, `limit`, and exact
`status=pending|processing|completed|failed|all` parameters. `pending` is the
default so the primary library surface is a work queue instead of an archive.
The response returns `artifacts` plus `pageInfo.hasMore` and
`pageInfo.nextCursor`; clients must pass the cursor back unchanged and must not
infer offsets or row counts from it. A cursor is bound to the query and status
view that created it. The library UI replaces the current page and retains only
a bounded cache. Numeric deep-page OFFSET is not supported.

The row describes the Artifact's newest immutable ArtifactVersion. Compile
jobs and evidence snapshots are deliberately scoped to that newest version,
so an older version's successful compile cannot make a newly uploaded version
look processed. Machine verification and the latest human review event are
scoped to that version's newest snapshot. A `withdrawn` event remains visible
to preserve the audit trail; UI status derivation treats it as unreviewed.
Human review is optional metadata and never a gate for library inclusion.
The row's `machineVerification` communicates whether the newest snapshot was
assessed independently, while `humanReview` is only its latest optional audit
event. Neither field creates a second publication state: a successfully imported
snapshot is already searchable.

`libraryStatus` is derived from the newest version only. An active job takes
precedence over an older snapshot (`processing`); otherwise an existing
snapshot is `completed`, a failed first Compile is `failed`, and material with
neither is `pending`. A failed re-Compile therefore remains `completed` and
keeps its last valid snapshot available. Explicit Compile-all streams pending
versions in pages with bounded submission concurrency; ordinary navigation never
enumerates the whole queue. Every version remains an ordinary durable Compile
job. The Compiler serializes MinerU parsing while allowing already parsed
documents to use its bounded GPT pool concurrently.

`0018` maintains compact current-version summaries, status counters and a
contentless FTS5 index of Unicode 1/2/3-character tokens. Name search intersects
compressed integer postings, validates the exact substring in at most 1,024
candidates per request, and returns a continuation even when a window has no
exact matches. Search totals are unknown; ordinary totals use maintained counters.
Metadata for verification and review is read only for the selected page. Exact
`artifact_version_id` lookup resolves its Artifact without listing the corpus.
