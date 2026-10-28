# Artifact boundary

`route.ts` owns listing and upload. The public site cannot delete papers:
there is no public DELETE and the UI offers no delete action.

Whole-Artifact purge is local maintenance only, at
`DELETE /api/internal/artifacts` (`app/api/internal/artifacts/route.ts`). The
Cloudflare Tunnel answers every `/api/internal` path with 404, and the route
also requires `assertLocalMaintainer`: no Cloudflare edge headers, a loopback
`cf-connecting-ip`, and a loopback host. Run it on the Mac with curl against
`http://127.0.0.1:8100`. `delete-store.ts` coordinates the destructive
operation across Compiler job files, the authenticated Compiler retrieval
projection, R2 source bytes, and D1-derived evidence records.

## Invariants

- The maintenance DELETE accepts a confirmed single Artifact or 1–1000 selected
  Artifact IDs. No unconfirmed shape or whole-project deletion is accepted.
- One atomic D1 batch makes every active Compile job terminal and acquires the
  short-lived deletion authorization. The authorization is the write barrier:
  new ArtifactVersion and Compile reservation inserts are rejected before any
  external cleanup begins.
- Compiler terminal jobs, retrieval FTS/vector rows, and R2 objects are removed
  before the final atomic D1 batch. All external operations are idempotent so a
  failed request can resume; retrieval failure occurs before source-byte deletion.
- The D1 batch removes audits, reviews, retrieval projections, verifier runs,
  Compile rows, snapshots, versions, and finally the Artifact. Never weaken the
  immutable UPDATE triggers or expose row-level deletion for scientific records.
- Acquire the deletion authorization before enumerating R2 keys. Database
  triggers reject new Artifact versions and Compile reservations behind that
  barrier, so a pre-barrier upload is included in the purge set.
- Every upload path rechecks a live, non-deleting version after its R2 access.
  A lost upload/delete race compensates the unreferenced object; it checks again
  after deletion and restores the same digest if another live reference won.
- `importing` is the non-cancellable final D1 commit stage. Earlier cancellation
  and whole-Artifact deletion both remove an attached Compiler job to terminal
  and then delete its private upstream files.
- Do not record deleted filenames, graph JSON, or source text in a tombstone.
