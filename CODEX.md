# DeepShovel engineering contract

Read `AGENTS.md`, `README.md`, `.codex/architecture.md`, and the nearest module `CODEX.md`.
Current workspace files are authoritative. Never restore an old deployment over current work.

## Boundaries

- `lib/evidence-graph` is pure: no UI, storage, Cloudflare or model SDK imports.
- `lib/evidence-retrieval` owns DTOs and immutable projections; server-backed quick recall belongs in `lib/server`.
- `app/api` owns Web transactions; `lib/server` owns I/O and shared response boundaries.
- Browser components cannot reach API implementations, server modules or secrets.
- Compiler owns MinerU, GPT, source normalization, canonicalization, jobs and retrieval. It never writes D1/R2.
- No new wrapper or framework unless it eliminates actual duplicated behavior.
- Cross-language constants (protocol and prompt versions, Claim enums, transport and context limits, the normalized-region tolerance, session shape) live only in `contracts/shared-contract.json`. Run `npm run contract:generate` to regenerate `lib/shared-contract.generated.ts` and `compiler/compiler_service/shared_contract.py`; both suites fail on a stale module, a disagreeing hand-written Literal, or a golden case in `contracts/shared-contract-cases.json` that the two languages decide differently.

## Scientific invariants

- Separate logical Artifact from content-addressed immutable ArtifactVersion.
- Claim identity includes normalized Scope; proposition-only identity serves recall.
- The only prompt pipeline is whole-document extraction then independent verification (`1.0.0`).
- Claims are paper-level derivation sinks, with complete source-to-Claim paths and no count quota.
- Structure, identities, exact source references, DAG, hashes and verification coverage are hard gates.
- Scientific disagreement changes verifier annotations, not source text, values, units or Scope.
- Bad local Claims may be isolated only with their exclusive closure and within the cumulative 20% budget (rounded down); never remove the sole Claim into empty success. Verifier mistakes are retried, not charged to Claims, until the bounded attempts are exhausted.
- Nodes on no Claim's Evidence Path are pruned; `compiler_audit` lists each of them, each malformed node, and each removed Claim (its exclusive closure goes with it).
- A nonzero-Claim import requires a hash-bound, exact-once verifier report. Zero Claims may omit it.
- Table cells and figure points must exist in the cited block's trusted structure; every Compiler rewrite of model output is recorded in the envelope's `compiler_audit`.
- Import is atomic; cancellation and task deadlines win over late results.
- Citation Reception and corpus relations are separate rebuildable projections, not independent scientific proof.
- Original bytes remain immutable until explicit user deletion or full reset.

## Model and operation

- Current model configuration lives in `compiler_service/config.py`; transport is Responses through `gpt_responses.py`.
- Batch work retains six GPT attempts (initial plus five retries), then up to three DeepSeek attempts for availability failures. Interactive completions race GPT and DeepSeek at 0, 10 and 20 seconds, at most three requests per provider, without application time limits. The first complete response passing the shared JSON/schema/domain checks wins; cancel the remaining network requests. Never log hidden reasoning or secrets.
- Interactive answers require validated GPT or DeepSeek output. If both fail, report the failure; never substitute local recall, unassessed candidates, or template answers. Provisional previews must disappear on failure.
- MinerU defaults to the fixed allowlisted lab HTTPS route and one worker. A temporary explicit configuration may use the exact 127.0.0.1:18006 SSH tunnel with Bearer authentication and up to four slots; never follow returned URLs or silently change services.
- Only bodyless Web-to-Compiler GET network failures may retry once. Never automatically replay a POST.
- Check actual streamed byte counts, MIME, version and identity before trusting a service response.
- Keep schema migrations required by empty initialization. Do not treat them as backup source copies.
- Normal deployment preserves data; the explicit runtime reset is the only wholesale-cleanup path.
- Tests are offline and self-contained. Generated data, historical experiments, caches, logs and backups do not belong in source.

## Validation

Run `npm run verify` after integrated changes. Keep focused regressions for altered behavior and cross-language contracts.
Use the current deployment bundle and public acceptance required by `AGENTS.md`.
