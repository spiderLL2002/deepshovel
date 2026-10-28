> Public copy: personal machine path prefixes have been normalized; scientific input/export files are unchanged.

# One-record recipient reconstruction

This is an independent AI recipient's reconstruction of one purposively selected record, using only `protocol.md`, `value-citation-projection.csv`, and `input-record.jsonld` in this directory. No paper, other QA, old export, source audit, source PDF, production code, or network was consulted. Inputs were not modified. The existing `prov-check-env` Python with RDFLib 7.6.0 was used; no library was installed.

The CSV-only answers were written before the first read of JSON-LD. The initial script saved `answer-minimal.json`, then opened the JSON-LD and printed its content (the tool output was truncated). Subsequent inspection decoded only that same input's embedded JSON and the script was completed and rerun. The CSV answer content was unchanged on rerun. This sequence supports independence of the initial CSV answer; later reruns are reproducibility checks, not new independent trials.

| Question | Three-column CSV | JSON-LD reconstruction |
|---|---|---|
| 1. Prepared value/unit | 55–57 MPa | 55–57 MPa, operator-prepared entry |
| 2. Original observation | unavailable | numeric 55; raw 55–57 MPa; unit MPa |
| 3. Quantity/conditions | unavailable | Prepared quantity: maximum sustainable pore pressure, author-reported modeled summary. Conditions: F2; Asmari reservoir; Gachsaran oilfield, SW Iran; zero cohesion and friction coefficient 0.6; 3D Mohr diagrams, slip tendency and modified slip tendency. These are retained operator fields. |
| 4. Identities/directed route | unavailable | Selected observation `obs.f2.assessed`; claim `claim:sha256:8820b173b31b5d2e9f3d59c1cebbf15bf72a8b5af94ac82a4578a99f3c57af43`. Directed route through `reason.f2.evidence`, then `reason.f2.safety`, then claim. Relations: `premise_for`, `premise_for`, `infers`. |
| 5. Version and locators | unavailable (DOI retained) | SHA-256 `f87d7cdcf0d08a7557993c1966b56183882c0b87c68809d0519ea8060382de99`. Recorded selected anchor: page 11, Section 6 Slip tendency parameter; normalized region x=0.07899159663865546, y=0.7987341772151899, width=0.41008403361344536, height=0.11139240506329114. Operator locator says right lower column, p.11, Table 4; conclusions p.13, while retaining the imported left-column rectangle. Corrected rectangle coordinates unavailable. |
| 6. Operator conclusion/qualification | unavailable | `qualified_reuse`, authorship `local operator entry`, purpose `interpretation`. Retained uncertainty says author-reported modeled range; Table 4 separately lists formulation-specific Pc and PCr. Notes say numeric 55 is only the range lower bound and give Pc 55.03/55.31 and PCr 57.58/57.81 MPa. These are reconstructed notes, not independently verified source findings. |

The actual selected directed route ends in `infers`. The separate imported retrieval relation `supports` is not substituted for that terminal edge. The full route was recovered by following `source_id` to `target_id` in the decoded `ds:selectedInspectionPath` edges from the selected observation to the reference claim; exactly one such route was found. Neither RDF triples nor graph connectivity establishes scientific correctness.

`answer-record.json` gives the exact subject IRI, full predicate IRI, physical input JSONPath, and, for embedded strings, the JSONPath after JSON decoding for each answer. RDFLib parsed 57 triples. Domain details such as original numeric/raw values and route edges are embedded JSON strings; they were decoded in Python rather than claimed to be standalone RDF predicates. `answer-minimal.json` identifies CSV row/column evidence. Absences stay unavailable; no external lookup filled them.

This deliberately fixed CSV projection contains exactly prepared_value, unit and DOI. Its omissions are absent by construction. It is not a representative implementation of all CSV schemas, SciDaSynth, LitCurate, or any neighboring product. This one-record format-retention illustration is not a performance comparison, human comprehension/benefit experiment, random-corpus evaluation, or scientific-truth assessment.

## Actual execution

Working directory: this `recipient-check` directory. Initial input command:

```sh
cat protocol.md && cat value-citation-projection.csv
```

It returned the six-question protocol and `prepared_value,unit,doi` with data `55–57,MPa,10.1007/s12182-019-0331-9`.

After writing the initial script, the first run used the same Python command below. Its first output lines were `SAVED answer-minimal.json BEFORE opening input-record.jsonld` and `RDFLib 7.6.0 triples 57`, followed by the JSON-LD dump (truncated by the tool). A follow-up inline Python inspection used `json.load(open('input-record.jsonld'))`, enumerated `@graph`, decoded `ds:json` and `ds:canonicalPayload`, and printed their key names and bounded field excerpts. No other input was read. Final reproducible command:

```sh
<author-workspace>/Desktop/实验室/2026/evidence论文/WWW2027_Demo_v2/revision/prov-check-env/bin/python reconstruct.py
```

Actual final output (exit 0):

```text
SAVED answer-minimal.json BEFORE opening input-record.jsonld
RDFLib 7.6.0 triples 57
SAVED answer-record.json; six questions with exact RDF and JSON locators
prepared: 55–57 MPa original: {'comparator': None, 'numeric': 55, 'raw': '55–57 MPa', 'uncertainty': None, 'unit': 'MPa'}
directed route: obs.f2.assessed -> reason.f2.evidence -> reason.f2.safety -> claim:sha256:8820b173b31b5d2e9f3d59c1cebbf15bf72a8b5af94ac82a4578a99f3c57af43
terminal relation: infers
source sha256: f87d7cdcf0d08a7557993c1966b56183882c0b87c68809d0519ea8060382de99
operator conclusion: qualified_reuse
```

## Simulated downstream handoff action (same recipient context)

A follow-up supplied a concrete simulated target field, `universal_threshold_MPa`, defined as a universal threshold without reservoir, model, or conditions restrictions. This continuation is not a second independent reconstruction trial. Only the same permitted JSON-LD input was read for this action; the original record and the initial CSV/RDF answer files were left unchanged.

`apply-target.py` executes an explicit policy for this simulated target: if the retained operator conclusion is `qualified_reuse`, or retained quantity/conditions impose model or conditions restrictions, do not populate the unrestricted target field. The supplied record triggers that policy: its operator conclusion is `qualified_reuse`; quantity says “Maximum sustainable pore pressure, author-reported modeled summary”; conditions identify F2, Asmari reservoir, Gachsaran oilfield and model assumptions/methods. The notes further qualify the stored numeric lower bound and range. These retained entries justify withholding the value under the stated target definition; they are not independently verified scientific findings.

The actual action is `do_not_reuse` for this target only. `target-row.csv` has one data row with a blank `universal_threshold_MPa` field. `decision-for-target.json` retains the original prepared 55–57 MPa, its quantity, conditions, uncertainty and notes, and supplies exact source subject/predicate IRIs and JSONPaths. No universal threshold was invented. The source record itself remains available with its original restrictions; the decision does not claim the record is unusable for every other purpose.

Actual execution in this directory:

```sh
<author-workspace>/Desktop/实验室/2026/evidence论文/WWW2027_Demo_v2/revision/prov-check-env/bin/python apply-target.py
```

Output (exit 0):

```text
action: do_not_reuse
target-row.csv: universal_threshold_MPa is blank
retained original: 55–57 MPa
saved: decision-for-target.json
```

This is a functional data-handoff action on a fixed single record and an explicitly simulated target schema. It is not evidence of human benefit, real external application adoption, scientific safety certification, or a cross-corpus scientific rule. It adds no independent trial or repeated hash-pass count.
