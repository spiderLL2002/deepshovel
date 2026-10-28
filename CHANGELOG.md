# Changelog

## Web 1.1.1 / Compiler 0.3.1 — 2026-10-20

Fourth remediation round, finishing the interrupted changes on top of round 3.

- Search, viewpoint comparison and citation analysis accept NDJSON responses:
  immediate start, 15-second heartbeats, then the original validated JSON result
  and status. Ordinary JSON callers remain compatible. Stream failures are
  errors, and cancellation reaches upstream model work, including citation
  analysis. The stream preserves vinext's Request wrapper in the deployed
  workerd runtime. No interactive request deadline is added.
- MinerU supports the operator's public asynchronous jobs API. The `parse`
  runtime profile selects it with four workers and a 30-minute parsing deadline.
  Capacity refusals may retry; ambiguous uploads and consumed results do not.
- Claim-relation batches retry a rejected preview handoff once with a fresh
  preview; private logs identify the failed check without exposing source text.
- Quick preview retains one- and two-character Chinese terms. Comparison Scope
  projections deduplicate and bound stored lists; retrieval Scope projections
  respect their 64-item limit without changing stored Claims.
- Retrieval regions use the graph's existing coordinate tolerance. Candidate
  text truncation preserves Unicode code points. Unused TypeScript scientific
  number-matching code and its isolated fixture are removed.
- Full functional acceptance fixes newly uploaded files not appearing in the
  pending queue, hidden cancelled jobs, and nullable audit fields preventing
  completed Compiler results from importing into Web.
- Search no longer shows an empty-result banner while a model result is pending
  or present. The source PDF dialog renders above the navigation.
- Data-quality inspection preserves merged table rows and columns and refreshes
  old inspection reports from saved DocumentIR without reparsing originals.
- MinerU jobs accept node-prefixed IDs and use bounded, derived node routing,
  matching the service's new gateway protocol.
- Following the owner's updated policy, realtime GPT attempts retain their
  0/10/20-second schedule; DeepSeek starts only after all GPT attempts fail with
  retryable availability errors. Waiting and invalid model output do not trigger
  the backup. Batch retry policy and model IDs remain unchanged.
- Interactive query planning now exposes the existing target/attribute rules
  in its structured-output schema. Supplied propositions require attributes;
  missing targets and indexed seeds require null attributes. This prevents the
  contradictory plans that made even the default query fail local validation.
  The planning error message now identifies model-plan validation explicitly.
- Built-in-browser acceptance found and fixed source links opening the first
  Claim instead of the clicked Claim, lost graph navigation state, overlapping
  taxonomy breadcrumbs and compressed pending-upload rows.
- Original PDF pages render with the existing PDF.js dependency, including
  page navigation, zoom and rotation, instead of depending on a browser PDF
  plug-in. Atlas combinations refresh after region changes, and empty database
  searches no longer retain an unrelated media boxplot.

- Leaving search cancels pending work and restores its controls. Comparison
  sources select the matching Claim and completed results survive source visits.
- The ordinary library includes pending/failed versions so cancelled uploads
  can be found and retried. Single-row version menus stay visible. Review details
  return to review; UTC timestamps display locally; rationale is checked against
  the existing 3–4000 character bound before submission.
- Atlas drafts can be inspected, withdrawn and cleared. Profile/correction
  exports use validated same-origin HTTP attachments that download in IAB.
- Tunnel startup recognizes observed fake-DNS address ranges and obtains real
  edge addresses through DoH for HTTP/2, retaining manual startup settings.
- Relation output receives its existing candidate-ID and count constraints in
  the actual Responses schema. Local validation and fallback policy stay strict.
- Interactive relation requests use short candidate aliases, validate complete
  coverage, then restore original IDs. The output allowance is explicitly 32768
  tokens; incomplete responses retain their actual provider reason. Scientific
  prompts, high reasoning, candidate counts and fallback policy stay unchanged.
- Initial fetch failures receive the existing Chinese retry message without
  automatically replaying a POST. Metadata fields render scientific notation
  safely and explain missing values without inventing source information.

Deployment: update Web and Compiler together with `make public-update`, then
verify the public site and the selected MinerU profile. No data migration,
full-corpus backfill, prompt change or model replacement is required.
The deployed runtime is Web `1.1.1+065ce6f8706e` / Compiler
`0.3.1+065ce6f8706e`. Full verification passed (1245 Compiler, 33 root Python
and 552 Node tests, plus the remaining verification stages).
The [round-4 handoff](docs/remediation-round4.md) preserves the earlier 67-case
built-in-browser run (63 passed, four partial) and the subsequent targeted fixes.
The previously failing Nd comparison completed twice on this release; context
continuation and metadata display were also rechecked. The 67 cases were not
rerun for this follow-up. Original OCR/missing data, the unconfirmed natural
request beyond 150 seconds and transient external availability remain limits.

## Compiler 0.3.0 — 2026-10-19 (branch `feat/review-remediation-round3-2026-10`, stacked on round 2, deployed 2026-10-19)

Third remediation round: the deferred Compiler findings that offline tests can
cover. Web stays 1.1.0 (no Web runtime change).

- `2cddb4c` Compiler release identity: `make public-update` stamps the source
  commit into the installed Compiler; `/health` reports `release` =
  `<version>+<commit>` like the Web.
- `3f03367` GRF-12: unenforced number-provenance machinery removed, including
  the repair that rewrote `Observation.anchor_ids`.
- `131e394` GRF-8: graph-structure failures are located `GRAPH_*` contract
  issues instead of `value_error` at the root; the derivation-cycle check is
  iterative.
- `0a1ee04` GRF-9: one tolerance rule. Unreachable nodes are pruned and audited,
  a malformed record removes only the Claims depending on it, and an edge
  violation taints only its target. Fixes a malformed Claim ID breaking the
  audit after the paid stages.
- `c2e9563` GRF-16: quote matching indexed once per compile, shared Evidence
  Path indexes, one backward pass for canonical paths, one canonicalization for
  a clean compile.

Fixes found while deploying and running migration 0025's relation backfill:

- `7b7f5ed` `make public-update` restarts the compile-sync work pump, which it
  had left stopped; a `+` keeps the per-snapshot document query on the primary
  key (7 s to milliseconds on the live library).
- `fc9dff2` Up to 12 Claim-relation batches at once instead of 4 (`ad36a58`
  updates its test).
- `4f8c98a` Relation analysis no longer fails for Claims near a stored Claim
  without original wording (`TypeError`) or with Scope values the query filter
  treats as one (`ValidationError`), both present since v1.0.0.
- `4c09d50` The installer rolls back after a failure under the macOS system
  bash 3.2 (its error trap used `BASHPID`).

Deployment notes: deploy together with round 2 (`make public-update`).
Snapshots no longer keep nodes that no Claim uses, so graphs that had them hash
differently; every removal is listed in `compiler_audit`.

## Web 1.1.0 / Compiler 0.2.0 — 2026-10-19 (branch `feat/review-remediation-2026-10`, deployed with round 3)

Second remediation round of the 2026-10-18 review. One commit per area on top of
`9f1d3e0` (round 1):

- `785e926` Compiler: idempotent job creation (`Idempotency-Key`, submission
  deadline, lookup by key or IDs) and restart recovery from the saved DocumentIR.
- `f5f9ffa` Web: durable D1 work queue (`work_items`, migration 0025) pumped by the
  compile-sync agent through loopback-only `POST /api/internal/work`; Compile
  import recovery with R2 staging; background indexing and Claim-relation analysis;
  `GET /api/work` (read-only).
- `a191af2` Evidence integrity: table/figure values checked against the cited
  source block (rowspan/colspan parser), verifier mistakes no longer delete Claims,
  reasoning `contradicts`/`qualifies` kept, `compiler_audit` envelope field.
- `765594e` Retrieval: CJK tokenization, negation, ontology context rules, time
  units, citation matching, capped anchor boosts, diversity demotion, opt-in numpy
  vector store (`RETRIEVAL_VECTOR_BACKEND=numpy` after the migrate CLI).
- `fefc23e` Search: browser holds only opaque `rs1.` session IDs (D1
  `retrieval_sessions`, migration 0026); synthesis finals use the sealed 2.1
  handoff; interactive planner failures are errors; protocol 2.2 retired in the
  Compiler.
- `731bf05` Decomposition of `RetrievalService._search`, `create_app`, and the
  search/synthesis routes (no behavior change).
- `d52bdb2` Shared cross-language contract (`contracts/shared-contract.json` →
  generated TS/Python modules, golden cases); GRF-6 region tolerance unified.
- `840349e` Release identification: versions bumped; `/api/health` reports
  `release` = `<web version>+<source commit>`.
- `4b65fa4` Review fixes for search sessions: sliding session expiry, slot-specific
  re-preview mapping, early quick-preview refusal.

Deployment notes: Web and Compiler must be updated together (`make public-update`),
migrations 0025 and 0026 are forward-only, and 0025 enqueues background work for
existing snapshots, including paid Claim-relation analysis. See the round-2
handoff in the review folder for verification, acceptance and rollback steps.
