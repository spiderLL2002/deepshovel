# Compiler maintenance contract

Follow `../CODEX.md` and the current module/configuration map in `README.md`.
Python/Pydantic models and cross-language tests are the executable authority.

- Keep one model boundary (`ModelJsonClient`), one whole-document extraction, and one independent verifier.
- Preserve exact source content, trusted geometry, scoped identities, canonical hashes and graph/path ownership.
- Task state lives in SQLite and atomic files; in-memory tasks only schedule work.
- Persist job DocumentIR before releasing a MinerU slot (configured 1–4, shared across parser consumers). GPT and retrieval use separate bounded pools.
- Restart recovery resumes jobs from saved progress and never moves a stage backwards; at most two recoveries per job. Job creation is idempotent per caller key, and no job is created after the caller's submission deadline.
- Never admit cancelled/late results or partially imported graph/report pairs.
- Interactive Retrieval2.3 resolves the task once, then ranks and labels the numbered candidate Claims in one model completion. Keep original history/selection and source identities bound to the request; do not add model re-verification or heuristic relation rewriting.
- Interactive completions race GPT and DeepSeek at 0/10/20 seconds, at most three requests each, with no application deadline. Keep batch model policy unchanged.
- HMAC integrity is a request-binding guarantee, not a scientific judgment. Final never re-plans or reorders a signed preview.
- Keep index version separate from search protocol. Older protocol numbers still used by current call sites are not backup implementations.
- Keep source extraction, Claim comparison, Citation Reception and Data Quality independent; their failures cannot silently rewrite another result.
- Tests must not depend on historical benchmark output, private papers or network model calls.
- Do not reintroduce archived experiment paths or obsolete provider compatibility layers.

- The standalone loopback HTTP/1 service explicitly closes each response connection. Workerd can otherwise reuse a socket whose idle timeout elapsed while synchronous projection work blocked its event loop; do not replace this with POST retries or a larger interactive timeout. HTTP/2 and non-HTTP ASGI scopes must keep their protocol semantics.
