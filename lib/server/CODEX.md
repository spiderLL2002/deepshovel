# Server boundary module

Follow the root [engineering contract](../../CODEX.md).

`evidence-store.ts` owns only bounded request parsing, content validation, SHA-256/R2 helpers, Cloudflare bindings, identity headers, idempotency-key syntax, the local-maintainer guard, and safe API errors. `assertLocalMaintainer` admits only direct loopback requests on the Mac (no `cf-ray`/`cdn-loop`/`cf-visitor`, loopback `cf-connecting-ip`, loopback host); it guards destructive maintenance such as whole-Artifact purge. Keep scientific graph semantics in `lib/evidence-graph` and SQL in route handlers close to the operation that uses it. Do not add a generic repository layer until at least two real call sites share the same transaction.

Never retain a D1/R2 object, statement, response, or pending I/O Promise across
Worker requests. Content-addressed R2 creation uses a create-only conditional
write and then reads the winning object in the current request context. Safe
errors inspect only a bounded `cause` chain: documented transient D1
busy/reset/network conditions become stable retryable 503 responses with
`Retry-After`, while logs contain only the classification and error kind.
Never replay a database write here; callers may retry only with the same
idempotency key.

`compiler-client.ts` is the outbound Compiler job/result boundary. Its base URL comes from server configuration, never request data; remote transport is HTTPS and uses the 32-512 printable-ASCII service secret (loopback compile development may omit it), redirects are rejected, and reflected credential material is rejected from decoded responses. Workerd does not implement `fetch(..., { redirect: "error" })`: use `redirect: "manual"`, reject every 3xx before reading its body, and never follow the response location. Requests have fixed paths/timeouts, successful responses are size-bounded exact JSON contracts, and non-JSON proxy failures retain HTTP retryability. Status responses must retain job identity and monotonic timestamps; result JSON is still untrusted until the evidence import contract validates it.
Decode the Compiler's official `{error:{code,message,retryable}}` envelope as
well as bounded proxy-style `detail` errors. In particular, preserve
`UPSTREAM_JOB_NOT_FOUND` so whole-Artifact cleanup can treat a previously
cancelled and already-purged upstream job as idempotent success.

Only a bodyless Compiler `GET` may be replayed, once, and only when `fetch`
itself throws before any response exists. Never replay `POST`, a request with a
body, or a received redirect, HTTP failure, malformed JSON, or protocol error.

Citation pair analysis is another fixed Compiler-client operation. It requires
the shared credential, accepts only the versioned bounded A/B candidate DTO,
never retries its POST, and strictly validates pair identity, frozen assertion
hash, verifier coverage, entity references, and non-strengthening confirmation
basis before D1 persistence.

Claim normalization and Claim-pair analysis are fixed Compiler-client
operations with the same no-replay, fixed-path, credential, and 8 MiB
response boundaries. The Web side strictly binds normalization spans to the
submitted text and comparison results to the frozen request, hash, exact pair
coverage, evidence whitelists, Scope gates, and a non-strengthening independent
verifier before any result is displayed.

`retrieval-client.ts` is the separate `evidence-retrieval/2.0` projection/search
boundary. It exposes only the fixed authenticated POST endpoints for manifest
check, snapshot indexing, search, and snapshot deletion. The shared service
secret is mandatory even on loopback. Every body and decoded response is capped
at 8 MiB, every response is exact-key validated, redirects and credential
reflection fail closed, and POSTs are never replayed. Callers must thread the
incoming request abort signal through each stage; the client combines it with an
operation timeout for noninteractive synchronization; interactive synchronization
and search forward only caller cancellation.
Request encoding errors retain their own codes before transport starts. Fetch failures log only bounded allowlisted transport classifications, operation/method and elapsed time; never log native exception prose, URLs, credentials or query contents.

The opt-in 2.3 search boundary preserves full history and the exact n1 context
submission or c2 choice across preview and final. New token shapes require the
`derived-auth-v1` key identifier; only the Compiler authenticates their contents.
`needs_context` is accepted only as an empty preview with null plan/confidence,
no handoff and four skipped channels. Resolved 2.3 responses must retain the
original query and provide `resolved_query`. History and supplements are exact
NFC Unicode with shared character/UTF-8 limits. Explicit 2.3 OpenAlex filter
metadata must match its taxonomy level and is forwarded into request binding.
`CONTEXT_REPREVIEW_REQUIRED` is a fixed, non-retryable 409 without upstream prose.
The legacy search protocols and their wire shapes remain unchanged.

`retrieval-sessions.ts` keeps those Compiler tokens server-side. The search
route stores each returned v1/n1/c2 token in D1 `retrieval_sessions` and gives
the browser only an opaque, kind-bound `rs1.` ID that expires 30 minutes after
its last use;
it never decides validity itself (the Compiler still authenticates the token).
An interactive client refuses a handoff-free `ai_rerank` request.

Planning failures retain only the eight allowlisted `RETRIEVAL_PLANNING_*`
classifications, with fixed HTTP status/message and `retryable: false`. Never
forward provider prose, query text, tokens, or attempt diagnostics for these
codes. The browser localizes these classifications without treating failed
planning as an empty retrieval result; no POST is automatically repeated.

Manifest checks are capped at 4096 per call and index requests at 2048 documents
per call. Those are transport chunks, not corpus or snapshot size limits. The
gateway must page checks, repeatedly send one immutable manifest with bounded
document chunks until its cumulative indexed count is complete, and preserve
zero-Claim manifests. A complete `degraded` manifest may be searched without
re-embedding on every request; the service's explicit warmup/repair path owns
dense recovery. Missing, stale, or partial manifests must be synchronized before
returning corpus results.

`retrieval-sync.ts` owns that shared D1-manifest-to-Compiler synchronization
for both interactive search and corpus graph processing. It synchronizes only
the D1-resolved active manifests and never treats the external index as truth.
Within one Worker instance, synchronize one corpus at a time per project. The
queue shares only admission signals; each caller uses its own bindings, client
and abort signal, then checks its full manifest set again. Never cache a completed
sync as proof that a later corpus is current. Cancelled waiters leave the queue;
running calls release admission only after their own I/O has unwound.

`openalex-classifier-client.ts` is the bounded authenticated bridge used only to
backfill paper classifications missing from older snapshots. It sends title
text to the Compiler's official OpenAlex classifier endpoint; it does not define
its own labels, hierarchy, prompt, or retrieval algorithm.

Realtime search and synthesis opt into `RetrievalClientConfig.interactive`. Only
the search POST then sends fixed `x-deepshovel-interactive: 1`. Interactive
manifest checks, indexing, and search have no application-imposed transport timeout.
Default clients and graph batches retain their original policy;
manifest/index calls never receive that header. Final preview refs are rejected, including on degraded responses with a signed
handoff; model failure must not produce a candidate-only answer.
