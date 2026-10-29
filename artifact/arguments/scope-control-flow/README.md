# Interactive scope-control regression (v1.1 repair)

This package documents a **public-source repair made on 29 October 2026**. The earlier interactive retrieval branch attached model decisions directly and skipped the scope guard. It could retain a contradiction or equivalence with unknown or disjoint scope. The v1.1 public branch now checks single-proposition interactive and noninteractive tasks through the existing scope-normalization functions. Missing query scope also prevents direct equivalence. This is new repaired code, not a claim that the October 25 historical service already executed these checks. Historical exports remain unchanged; this repair does not deploy the shared service.

Interactive multi-question/non-propositional tasks retain model `supports`, `refines` and `qualifies` relations and their directions. They have no single reference proposition whose scope could establish whole-task contradiction or equivalence, so those two labels become `related` with unknown scope. Single-proposition checks retain compatible support and in-scope relation behavior, model confidence, model ranking and the existing one-planning-call preview/final handoff.

## Reproduce

From the repository root, use Python 3.12 and the existing small replay environment:

```sh
python3.12 -m venv .scope-venv
.scope-venv/bin/python -m pip install -r repro/requirements.lock pytest==8.4.2
PYTHONPATH=compiler .scope-venv/bin/python -B -m pytest \
  compiler/tests/test_retrieval_relation_reference_service.py \
  compiler/tests/test_retrieval_service.py::test_relation_blockers_and_geologic_time_units_are_conservative \
  compiler/tests/test_interactive_relation_ranking.py -o addopts= -q
```

The recorded run passes **92 tests**. The first two selections form the **86-test** CI scope check; the additional file verifies the existing interactive model wire. No Qdrant, GPU packages, live model calls or production credentials are needed for these selections.

The new parameterized regression executes `RetrievalService._apply_relation_decisions`, rather than testing only a standalone helper: interactive/noninteractive × context/legacy protocol × five scope states × three proposed relations = **60 cases**. Five additional cases retain interactive non-propositional relevance while rejecting direct contradiction/equivalence. A real local preview-to-final service test also checks that scope validation executes without adding a planning call. These are explicitly synthetic contract fixtures, not scientific accuracy observations. Before the repair, the targeted regression selection had 36 failures and 30 passes.

The existing scope/admission replay also remains executable without changing its expectations:

```sh
.scope-venv/bin/python -B artifact/replays/scope_contract/run.py \
  --repo . --out runs/scope-contract-new
```

It passed nine query cases and 18 full-pool channel-parity checks against the repaired public source. `--out` must be a new directory. Its constructed vectors/labels measure contract behavior, not retrieval quality.

[verification.json](verification.json) records the commands, outcomes and hashes of the tested source/test files. Free-text scientific mismatches remain conservatively unknown; the existing checker establishes disjointness only when supported by interpretable non-overlapping numeric intervals.
