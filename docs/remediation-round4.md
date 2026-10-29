# Fourth remediation round — 2026-10-20

This round completes Claude's interrupted work on
`feat/review-remediation-round4-2026-10`, based on
`feat/review-remediation-round3-2026-10`. Claude's commits `34874ef` and
`f4cf2b8` are retained. PR #4 remains a draft; PRs #2 and #3 are not merged.

The deployed runtime is Web **1.1.1+065ce6f8706e** and Compiler
**0.3.1+065ce6f8706e**, installed through `make public-update`.
Documentation commits after this revision do not change the runtime.

## Targeted follow-up completed at the owner's request

The owner requested fixing the previously observed problems and then stopping.
This follow-up addressed three paths, using only the Codex built-in browser:

- Nd-paper comparison: `0540a9b` explicitly allows 32768 output tokens and
  preserves the provider's incomplete reason. That change alone still failed:
  three attempts returned `max_output_tokens` with zero reported usage, so the
  effective upstream limit and actual consumption are not established.
  `065ce6f` additionally projects interactive candidate keys to short aliases in
  the request and schema, validates complete coverage, then restores the exact
  original keys. High reasoning, scientific prompts, candidate count, stored
  identities and retry/backup rules remain unchanged.
  The same `W7165146192.pdf` Claim 1 subsequently completed **twice** on the
  deployed release. The first was pending at 80 seconds and observed complete
  by 138; the second was pending at 69 and complete by 130. Both completed 1/1
  without an error and reported insufficient evidence to confirm a direct
  relation. These are workflow successes, not confirmation of the proposition.
- Context continuation: `1100ae5` handles initial fetch rejection with the
  existing Chinese retry message and preserves cancellation reasons. It does
  not replay a potentially executed POST. Supplementary input and continuation
  state remain available. A live ambiguous question followed by the
  Minamitorishima REY clarification completed with 30 candidates and five
  groups. The original transient connection failure's cause remains unknown.
- Metadata: `e91350e` renders scientific notation through the existing safe
  component and explains unprovided/unextracted fields. Actual paper details
  were opened and screenshotted. Original OCR mistakes and missing source
  information were not guessed or overwritten.

Final `npm run verify` passed all eight stages: **1245 Compiler, 33 root Python
and 552 Node tests**, TypeScript, lint, build/runtime checks, eight deployment
bundle tests, four config tests and migrations matching 37 tables. Existing
deprecation/unused-variable warnings remain. Public Web and Compiler health
were `ok`, MinerU `available` and retrieval `ready`; public JavaScript matched
the installed bytes. Counts remain 9506 papers/versions, 9508 snapshots,
41092 active Claims, 41095 retrieval documents and 12315 relation edges, with
no open compile or background work. No QA uploads or research-data edits were
needed for this follow-up.

Evidence is in the local review folder
`code-review-2026-10-18/known-failures-fix-2026-10-20`, including both successful
comparison screenshots, the earlier failed attempt, context/metadata screenshots,
verification/deployment logs and release/data checks. The owner-requested fixes
and corresponding checks are finished; no additional audit or model requests
are being started. Natural public inference beyond 150 seconds remains an
unconfirmed test case.

## Earlier full-site acceptance snapshot

The preceding full-site acceptance used the **Codex built-in browser (IAB)** and records
**67 workflow cases: 63 passed, four partial**. Every sidebar area and its listed
workflows were exercised. This is not exhaustive coverage of all data, inputs
or upstream failures. Earlier Chrome acceptance did not establish reliable
ordinary-user behavior and does not replace this run. The comparison failure
recorded below was subsequently addressed by the targeted follow-up above.
The historical 67 cases were not all rerun; continuous parser availability and
uniform stability across all inputs are not claimed.

## Completed implementation

- Search, comparison and citation support NDJSON: immediate start, a 15-second
  heartbeat schedule, then one validated final result carrying original status,
  Retry-After and JSON. Ordinary JSON remains compatible. Interrupted/malformed
  streams fail explicitly and release resources. The deployed vinext/workerd
  Request facade is preserved; no interactive deadline was added.
- Cancellation reaches Web, Compiler and model work, including citations.
  Leaving search cancels pending work and restores its controls.
- MinerU uses `https://parse.iiot-shxt.xyz/jobs` through `parse`: no token,
  concurrency four, parsing deadline 1800 seconds. Node-prefixed IDs use bounded
  derived routing. Ambiguous uploads and consumed results are not replayed.
  A live task confirmed first-result success followed by result/status 404.
- Single/double-character Chinese preview, bounded/deduplicated Scope projections,
  coordinate tolerance and Unicode-safe truncation are retained. Retrieval lists
  cap at 64 in requests only; stored Claims/hashes stay unchanged. Unused
  `numberKeys` code is removed.
- Rejected relation handoffs receive one fresh-preview retry and private
  diagnostic reasons. No historical full-corpus backfill was rerun.
- Upload queues show new/cancelled work. Nullable audit fields survive Compiler
  serialization and Web import. Ordinary library includes pending/failed versions
  with retry; comparison selection still uses completed versions.
- Table inspection retains physical merged-cell rows and columns and refreshes
  old cached inspections from saved DocumentIR.

## Failures found through ordinary browser use

The owner's default-query failure was reproduced: the model wire allowed
contradictory target/attribute combinations that local validation rejected.
`3d1c12b` expresses the existing input/seed/missing-target rules in the output
schema. This was a protocol mismatch; balance, MinerU or an empty corpus were
not established causes. Final IAB default searches completed, including 30
candidates and 21 final groups on the deployed revision.

| Observed failure | Resulting behavior |
| --- | --- |
| PDF pages were black or depended on a browser plug-in | PDF.js renders actual pages, zoom and rotation. |
| Graph/comparison sources selected the first Claim | Sources select the clicked Claim. |
| Source visits lost graph/comparison state | Graph path/query and completed comparison results survive return. |
| Breadcrumbs, pending filenames and version menus were obscured | Layout keeps controls visible. |
| Cancelled new versions disappeared from the library | They remain findable and retryable. |
| Review returned to library; timestamps were eight hours off | Return goes to review; UTC timestamps display locally. |
| Pasted rationale bypassed HTML maxlength | Active checks enforce trimmed length 3–4000 before submission. |
| Atlas region/empty-result changes kept old charts | Statistics refresh and empty charts clear. |
| Drafts lacked withdrawal; Blob exports produced no IAB file | Draft list/withdraw/clear and HTTP attachment exports work. |
| Fake-DNS edge addresses disrupted tunnel startup | Observed fake ranges resolve through DoH to real HTTP/2 edges. |

A later comparison rejected incomplete candidate coverage. `de1680e` now sends
existing candidate enum/count constraints in the actual Responses output schema.
Local coverage validation remains strict. However, the final `babf6aa` comparison
of `W7165146192.pdf` still failed: three attempts reported `truncated_json` and
`finish_reason=length`. Generic `response.incomplete` also maps to that reason,
so the evidence does **not** prove a token-limit cause. No schema-rejection HTTP
error was observed. This historical failure led to the targeted follow-up above,
where the same case completed twice after the further repairs.

## Actual acceptance and verification

IAB coverage includes default/Chinese/English searches, support/opposition
priority, history, clarification/context continuation, current-paper/taxonomy
scopes, cancellation, source navigation, single/multiple comparison, citation
analysis, upload/dedup/version/retry/download, graph hierarchy/neighbors, PDF
pages, metadata/text/tables/figures, all five Atlas views and exports, review
decisions/drafts/filters, and task filters/cancellation/batch controls.
Asynchronous actions were observed until completion, failure or cancellation;
screenshots were inspected. Backend checks supplement rather than replace UI.

- Default runs returned 30 candidates and 22/19 groups; final release: 30/21.
- Two-Claim comparison completed 2/2; a later QA comparison showed support and
  context, opened source Claim06 and retained results on return. The separate
  failure above was partial in this earlier run; see the subsequent retest.
- New citation analysis progressed 0/1 to 1/1 with no verified conclusion.
  A separate cached positive record expanded, opened the citing PDF at page
  13/18 and navigated to the correct complete evidence. Cached display is not
  counted as a new model-call success.
- QA v1/v2 each produced six Claims; v2 had 19 observations/13 anchors.
  Duplicate upload added no version. UI showed latest v2; backend confirmed v1
  retention. The merged-table sample produced one table, nine passing checks,
  no reported risks and the expected four-by-four grid; it produced zero Claims.
- Both Atlas HTTP exports produced downloaded JSON files with checked schema,
  contents and SHA-256. Drafts were cleared without applying research changes.
- Only QA received accepted/revise/reject/withdraw reviews. On the final release,
  4001 characters caused an inline error and disabled submission; 4000 and three
  enabled it, two disabled it. The unsaved draft was cleared.

At the earlier `babf6aa` revision, full `npm run verify` passed all eight stages:
**1239 Compiler, 33 root Python and 550 Node tests**, TypeScript, ESLint,
production build/runtime artifacts, eight deployment-bundle tests, four config
tests and fresh migrations matching 37 application tables. One existing unused
variable lint warning remains.

Earlier compilation acceptance separately verified extraction-stage restart
recovery (count one, identical saved DocumentIR hash), after checking no unrelated
jobs were running. It is distinct from the latest IAB cancel/retry tests.

## DeepSeek policy

GPT attempts retain the owner's 0/10/20-second schedule. DeepSeek starts only
after all GPT attempts fail with retryable availability errors. Waiting, invalid
model output and non-transient errors do not activate it. Successful validated
results cancel remaining attempts. Batch retries, model IDs and scientific prompt
bodies stay unchanged. The final incomplete-output comparison did not qualify
for emergency fallback.

## Data and runtime preservation

Only this IAB run's two identified QA artifacts were cleaned up after compile
and background work finished: three versions, three snapshots, five compile
records. Earlier acceptance separately cleaned its own QA. Final read-only checks
returned integrity `ok`, no foreign-key violations, no remaining QA or open work,
and restored the baseline: 9506 papers/versions, 9508 snapshots, 41092 active
Claims, 41095 stored retrieval documents and 12315 relation edges. The work pump
is running; public JavaScript bytes match the installed build.

Of 33 installed Atlas files, 32 data/reference files retain baseline hashes;
only the authorized `world-atlas.web.html` interface changed. No extra backup
or full-corpus backfill was created. Originals, database, graph and credentials
remain. Business services retain manual startup; only the menu controller has
RunAtLoad enabled. Final health/integrity evidence is recorded in the local
report, including degraded external dependencies rather than just HTTP status.

## Four partial cases from the earlier run and other limits

1. Browser/latency: natural public model success beyond 150 seconds is unconfirmed.
   The earlier local 165-second stream test and shorter public heartbeat successes
   cannot be combined into that claim.
2. Context continuation: one `Failed to fetch` attempt succeeded on retry; its
   specific cause remains undetermined. Error handling and a fresh successful
   continuation were subsequently checked above.
3. Comparison: single/multiple examples completed, but the final Nd-paper case
   failed with incomplete upstream output in that run; the follow-up fixed the
   request representation and observed the same case complete twice.
4. Metadata: original OCR and missing bibliographic fields remain in source
   data; the follow-up fixed TeX rendering and missing-value explanations.

Final health at 10:07 UTC reported MinerU unavailable; direct MinerU health
and repeated Compiler health recovered by 10:10 UTC. The transient cause is
undetermined. Web/retrieval remained ready. OpenAlex `model_ready=false` denotes
an unloaded lazy pipeline, not an observed loading failure; no new classifier
call was made to prove availability. HTTP 200 alone is not an all-ready check.
Batch controls used one pending QA file, not a multi-file concurrency load test.
A separate strict source probe flags MinerU `table_caption` metadata without
lossless visual mapping; table-body coordinates passed. Unproven historical causes remain undetermined. Prompt
revisions, verifier stance, FTS storage, component decomposition and relation
quality benchmarks remain recommendations.

The checklist, report, logs, fixtures and screenshots are in the existing local
review folder `code-review-2026-10-18/iab-user-acceptance-2026-10-20` outside Git.
Older evidence remains in `full-acceptance-2026-10-20` and
`default-query-2026-10-20`. Originals, databases, private logs and Atlas datasets
are not committed.
