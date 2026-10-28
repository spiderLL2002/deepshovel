# Database module

Follow the root [engineering contract](../CODEX.md) and [architecture](../.codex/architecture.md).

## Responsibility

`db/schema.ts` owns durable D1 metadata for logical artifacts, immutable versions, immutable evidence-graph snapshots, independent verification runs, human review events, and audit events. Original bytes belong in R2; executable scientific invariants belong in `lib/evidence-graph` and `compiler`, not SQL strings.

The corpus Claim graph tables are rebuildable derived state: retrieval documents
remain their node source and completion absence means unprocessed. A stable
unordered pair key permits at most one cross-paper relation per Claim pair;
directed relations are cycle-rejected and symmetric comparisons retain
canonically ordered endpoints.

OpenAlex paper classifications are snapshot-scoped derived metadata. Each
classification stores the official primary Topic plus up to two additional
Topics and their official Domain/Field/Subfield hierarchy.

Citation source manifests, bibliographic references, and exact mentions are
immutable facts owned by citing snapshot A. Citation Reception runs, assertions,
and verifier assessments are a separately rebuildable A→B projection; deleting
B removes that derived chain but never erases A's source-authored citation.

## Invariants

- Never merge `artifacts` and `artifact_versions`.
- Version, graph, and verification hashes are lowercase SHA-256 and immutable.
- Reviews and audit records are append-only.
- Review withdrawal is another append-only event; `withdrawn` derives an unreviewed current state and
  never deletes or mutates the earlier decision.
- `0003_immutable_evidence_records.sql` enforces update/delete rejection for versions, graph snapshots, verifier runs, reviews, and audit events even under direct SQL.
- `0007_lumpy_sleeper.sql` preserves immutable updates and ordinary delete rejection,
  while allowing one exact, whole-Artifact purge transaction through an ephemeral
  `artifact_deletion_authorizations` capability. Never use it for partial history edits.
- `0008_artifact_delete_write_barrier.sql` closes the purge race by rejecting
  new Artifact versions and Compile reservations after that capability exists.
- `0009_evidence_retrieval_v2.sql` backfills strict
  `evidence-retrieval/2.0` documents, assessments, and manifests before dropping
  every deployed v1 retrieval table. It preserves valid zero-Claim snapshots,
  then applies immutable update/delete triggers with the existing authorized
  whole-Artifact purge exception.
- Retrieval manifests are the active-corpus authority. Do not infer index
  freshness by scanning snapshot `graph_json`; a newest zero-Claim manifest
  intentionally suppresses an older Claim-bearing snapshot.
- Foreign keys use `RESTRICT`; historical provenance must not cascade-delete.
- `0013_sloppy_angel.sql` protects citation source facts with immutable and
  Artifact-deletion-barrier triggers. Derived reception rows reject UPDATE but
  permit delete+insert reprocessing; run insertion checks both A and B barriers.
- Add indexes only for an actual API query or uniqueness rule.
- `0017_remove_unused_prototype_tables.sql` removes the seven retired prototype tables;
  the initialized database's application tables exactly match the current schema.
- `0018`–`0022` maintain rebuildable library/snapshot summaries, compressed name
  postings, active family/paper winners, graph/topic counts, and the unclassified
  snapshot queue. Updates are
  local to the affected paper. They never replace immutable evidence records.
- `0024` covers the library projection's latest-snapshot lookup by project and
  artifact version, including versions without snapshots. Keep this lookup off
  the large snapshot payload table. Compile polling skips unchanged state writes
  so it does not repeatedly fire the projection triggers.
- Active snapshot replacement uses explicit DELETE followed by INSERT so
  dependent counts work with SQLite's default recursive-trigger setting.
- The contentless FTS5 table and its exact shadow tables are explicitly checked
  by offline verification alongside the Drizzle relational schema.
- Generate a new migration with `npm run db:generate`, inspect it, and keep prior migrations unchanged.
