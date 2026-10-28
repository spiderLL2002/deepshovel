# Evidence Graph Domain Kernel

Follow the root [engineering contract](../../CODEX.md) and [architecture](../../.codex/architecture.md). This file owns only the pure scientific domain boundary.

## Boundary

This directory is the storage-independent trust boundary for scientific
evidence. It has no database, HTTP, UI, parser, or model-client dependency.
Model output is untrusted JSON until it passes the appropriate parser here.

The scientific argument flow is deliberately explicit:

```text
Artifact -> ArtifactVersion -> SourceAnchor
                              -> StructuredVisualEvidence
                              -> Observation
                              -> Reasoning (+ Assumption)
                              -> AtomicClaim
```

Only claims participate in corpus-level `equivalent_to`, `supports`,
`contradicts`, `refines`, `qualifies`, and `supplements` relations. Direct
Observation-to-Claim shortcuts are rejected: evidence must pass through an
explicit Reasoning node.

A Reasoning -> Claim conclusion keeps its stance: `infers` is a supporting
conclusion, while `contradicts` and `qualifies` record a refuting or limiting
one. Every Claim needs at least one such conclusion. Snapshots compiled before
2026-10 stored every conclusion as `infers`, so treat a legacy `infers` as
"concluded from" rather than proof of support.

## Canonical contract

`EvidenceGraph` has exactly these top-level fields:

```text
schema_version
paper_id
artifacts
artifact_versions
anchors
visual_evidence
observations
assumptions
reasoning
claims
edges
assessments
snapshots
```

The canonical node records intentionally retain extraction semantics needed
after normalization:

- `SourceAnchor`: `anchor_type`, immutable `artifact_version_id`, complete
  selector, optional quote and quote hash.
- `Observation`: `observation_type`, non-empty `anchor_ids`, Scope, optional
  lossless/raw and parsed measurement value.
- `Assumption`: `assumption_type`, `explicit`, `anchor_ids`, Scope.
- `Reasoning`: `reasoning_type`, `anchor_ids`, Scope, limitations.
- `AtomicClaim`: extractor-local `source_id`, claim type, polarity, qualifiers,
  `anchor_ids`, proposition identity, scoped identity, Scope, and modality.
- `Assessment`: target claim, cited path edges and evidence entities, verdict,
  limitations, confidence, assessor identity, and UTC timestamp.

All object shapes are closed: missing and unknown fields are errors. All IDs,
references, edge endpoint types, provenance links, scientific completeness,
snapshot parents, and derivation cycles are checked before admission.
Extractor-local IDs and every local-ID reference match
`^[A-Za-z][A-Za-z0-9._:-]{0,127}$`; canonical claim and snapshot identities
retain their stricter SHA-256 formats. General short text is limited to 1,000
characters and statements to 8,000, while lossless raw values retain an 8,000
character boundary and exact source quotes retain 16,000. Every Artifact must
own at least one ArtifactVersion, and every ArtifactVersion must be located by
at least one SourceAnchor.

## Claim identity

File identity and semantic identity must never be conflated:

- `ArtifactVersion.sha256` identifies immutable bytes.
- `proposition_key` is SHA-256 of the NFC-normalized, whitespace-collapsed,
  lowercase proposition text only. Claim type, polarity, qualifiers, and Scope
  do not change this scope-free key.
- `scoped_claim_id` is SHA-256 of canonical JSON containing
  `proposition_key` and the complete canonical Scope. `AtomicClaim.id` must
  equal this value.

Consequently, the same proposition in different time, place, formation,
sample, method, condition, measurement, uncertainty, or entity scopes remains
two assessable claims while still sharing a proposition key.

## Compiler verification boundary

The standalone Compiler emits a canonical extraction graph. Import it with
`parseEvidenceGraph`.

The Compiler's independent `VerificationReport` remains a separate contract.
Use `validateCompilerVerificationReport` for its closed JSON shape and
`attachCompilerVerificationReport` to validate claim/evidence references
without mutating the extraction graph. Hidden model reasoning never crosses
this boundary or becomes evidence.

For active Compiler 1.0.0, verdicts are annotations rather than extraction admission
decisions. `supported`, `contradicted`, `qualified`, `uncertain`, and
`insufficient_evidence` remain stored review/retrieval signals; none may rewrite
the graph or reject an otherwise valid Compiler 1.0.0 import solely by its value. A
nonzero-Claim Compiler 1.0.0 import must include a hash-bound report with exact-once
assessment coverage for every Claim present in the final graph, while zero
Claims requires no assessment and may use `verification: null`.

Rationale and limitation wording is advisory verifier commentary, not a
scientific graph node or an import-admission boundary. It may therefore describe
a mismatch without reproducing the source's exact numeric serialization. Claims
are conclusions under review and can never appear in `evidence_ids`. Every
verdict except `insufficient_evidence`
requires cited evidence, including at least one on-path `SourceAnchor`,
`StructuredVisualEvidence`, or `Observation`; Reasoning and Assumptions may
explain that path but cannot establish a substantive verdict by themselves.
Only `insufficient_evidence` may carry an empty evidence list.
Limitations and report warnings remain free to report outside-path conflicts
and are not treated as positive support.

Compiler 1.0.0 graph admission remains strict for closed shape, Artifact/Version and
scoped Claim identity, exact quotes and regions, references, edge endpoints,
Observation path ownership, derivation DAG, and canonical/snapshot hashes.
Scientific value, comparator, unit, and formula-semantic disagreement is not a
whole-graph admission failure. The verifier compares those properties per Claim,
lowers the affected verdict, and records details in `limitations` without
rewriting the extraction graph.

The exact-decimal, scientific-mention, and formula helpers remain available for
historical version gates and diagnostics. Decimal keys are derived from strings
without IEEE-754 conversion; navigation labels stay masked; units remain
separate semantic fields; and formula normalization never calculates, converts,
or repairs an expression. For Compiler 1.0.0 these helpers inform verification and
tests, not extraction/canonical/import rejection.

Historical extractor 2.5 retains its original numeric admission behavior.
Historical extractor 3.0 retains its scientific-mention gate, and 3.1/3.2/3.3/3.4
retain their scientific-mention plus formula gates. Those version-gated rules and
immutable stored graphs/reports are unchanged and retain their original
admission contracts.

Path ownership is equally strict across the Compiler and canonical graph: each
Observation has exactly one outgoing `premise_for` edge. Anchors remain reusable
across narrow Observations, multiple Observations may enter one Reasoning node,
and Reasoning may feed later Reasoning so evidence branches can converge before
reaching a terminal Claim. Every Reasoning must reach at least one Claim; a
Claim is not an upstream derivation premise. No analogous count is imposed on
anchors, visuals, or assumptions. Keep the
canonical integrity code `OBSERVATION_PATH_OWNERSHIP` aligned with the Compiler's
`observation_path_ownership` issue.

## Public APIs

- `validateEvidenceGraph(value)`: synchronous closed-shape, reference,
  endpoint, completeness, and derivation-DAG validation.
- `verifyEvidenceGraphIntegrity(graph, snapshotHash)`: quote, claim identity,
  and snapshot hash verification.
- `parseEvidenceGraph(value)`: the normal asynchronous trust boundary for
  canonical storage/API JSON.
- `createAtomicClaim`, `propositionKey`, `scopedClaimId`, `canonicalScope`:
  canonical identities and constructors.
- `canonicalize`, `canonicalStringify`, `sha256Hex`, `hashCanonical`,
  `hashEvidenceGraph`: deterministic serialization and SHA-256 helpers.
- `createSnapshot`, `computeSnapshotContentHash`: immutable snapshot creation.
- `expandEvidencePath`: reverse expansion from a claim through reasoning,
  assumptions, observations, visual evidence, anchors, versions, and artifacts.
- `validateCompilerVerificationReport`, `attachCompilerVerificationReport`:
  explicit Compiler verifier-report integration.

## Files

- `types.ts`: domain types, relations, and collection metadata only.
- `canonical.ts`: canonical JSON, normalization, hashing, and claim identity.
- `validation.ts`: strict runtime validation and graph invariants.
- `paths.ts`: snapshots, canonical graph hashing, and Evidence Path expansion.
- `verification.ts`: strict Compiler verification-report validation and attachment.
- `index.ts`: exports only; never place implementation in the barrel.

No third-party runtime dependency is allowed in this kernel.

## Verification

From the repository root:

```bash
./node_modules/.bin/tsc --noEmit --incremental false
./node_modules/.bin/eslint lib/evidence-graph tests/evidence-graph.test.mjs
npm run test:node
```
