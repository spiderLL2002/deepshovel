# Test module

Follow the root [engineering contract](../CODEX.md).

Node tests cover the storage boundary, TypeScript graph invariants, canonical identity, derivation DAG, verification import, and evidence-path expansion. Python tests under `compiler/tests` mock the model SDK and remote parser, covering empty output, invalid JSON, strict Schema failure, bounded retries, hidden-reasoning exclusion, ID/Scope rules, and extractor/verifier separation. Tests must never call a live model, call MinerU, or depend on private research files.

`architecture-boundaries.test.mjs` makes the dependency direction in the root
engineering contract executable: the domain kernel stays pure, retrieval only
depends on that kernel, API routes never import browser components, client
components never import server implementations, and project imports stay
acyclic.

Storage regressions must reproduce request-actor isolation rather than sharing
R2 objects in a process-global fake. Concurrent content-addressed uploads must
use one conditional winner, and nested D1 busy/reset/network causes must remain
sanitized, retryable API errors (including `Retry-After`) at a real route catch
boundary. Unknown failures and cross-request I/O defects remain distinct,
non-retryable classifications.

Upload-scheduler regressions must keep the live `FileList` copy-before-clear
boundary, the global two-upload limit, stable logical idempotency, bounded
transient retry/`Retry-After`, ordinary-4xx exclusion, and immediate abort while
queued, in flight, or waiting between attempts.

Evidence-detail regressions must prove that an immutable snapshot accepted
before a later invariant tightening fails closed with
`STORED_SNAPSHOT_INCOMPATIBLE`, a safe validation code/path, and a `recompile`
action. It must not expose frozen content or masquerade as a transient store
failure.

Contract-retry regressions must keep one located witness per issue code,
render grouped corrections within the bounded feedback budget, and use only
the immediately preceding attempt's diagnostics. Observation numbers are not
an extraction gate: value disagreements are verifier findings.

Path-ownership regressions must reject one Observation feeding two Reasoning
nodes while accepting multiple narrow Observations that reuse one anchor and
each own exactly one outgoing `premise_for`. Preserve unrelated assumption,
visual, and complete Evidence Path edges in those fixtures.

Review-route tests keep optional human decisions append-only, bind idempotency keys to the complete
normalized request, and verify that a latest `withdrawn` event derives an unreviewed state.

Evidence-search route tests must cover batched root ArtifactVersion source
hydration for multiple ranked results and assert that neither hydration nor
retrieval selects `graph_json`. Ranking regressions must keep rare BM25 terms
ordered (without hard-cap ties) and prove that an exact multi-character CJK
scientific phrase can outrank broad unigram/bigram overlap without bypassing
Scope, relation, Evidence Path, or assessment components.

`backend-compile-route.test.mjs` exercises the real Web route with in-memory D1/R2 and a fake HTTP Compiler. Keep submission reservation, actor ownership, byte verification, polling/import leases, immutable graph persistence, official result metadata/verification coverage, proxy failures, late-response races, and audit repair covered there.

`backend-artifact-delete.test.mjs` applies every real D1 migration, proves normal
immutable deletes still fail, and exercises the confirmed Artifact purge across
terminal Compiler jobs, R2 bytes, D1 metadata, and active-job rejection.

`local-dev-wiring.test.mjs` freezes the local process boundary: Vite serve must
load `wrangler.local.jsonc`, builds must keep the inline hosting bindings, the
Wrangler file must require the shared secret, and the Compiler launcher must
source root `.dev.vars` before its private environment file.

`public-deployment.test.mjs` owns the secret-free deployment manifest,
deterministic generated runtime, shell syntax, PDF.js runtime-asset gate, and
cheap Web health contract.
Live DNS, Cloudflare Access, LaunchAgents, and public HTTP remain installation
acceptance checks and must not be exercised by the ordinary offline test suite.

Acceptance-operator regressions must accept one or more ordered `--source-dir`
values, validate their combined inventory through `--expected-count` while
preserving the default 14-document workflow, keep upload idempotency
content-addressed, scope Compile idempotency to `run_id + SHA-256`, preserve it
across request retries and `--resume`, and change it for every fresh run.

Scientific-display regressions must render normalized text through remark-math
and KaTeX, not only compare intermediate strings. Mixed prose plus `\\[...\\]`
must produce nonempty MathML without a visible delimiter, while paths, URLs,
escaped backslashes, and Markdown code remain literal.
Browser upload scheduling tests must prove one shared concurrency ceiling across
separately appended batches and ensure an aborted queued file never consumes a
slot or starts network work.
