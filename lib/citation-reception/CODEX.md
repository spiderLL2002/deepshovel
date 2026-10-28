# Citation Reception domain

Follow the root engineering contract.

- This module owns the pure `citation-source/1.0` and
  `citation-reception/1.0` TypeScript contracts and graph-to-candidate
  projection.
- Citation Reception is a rebuildable corpus projection. It never mutates a
  Paper Evidence Graph and never writes `corpus_claim_graph_edges`.
- A citation marker, a bibliographic reference, a target alignment, citation
  faithfulness, confirmation mode, and independence are separate facts.
- Strong confirmation modes require concrete citing-paper evidence. Verbal
  endorsement must never be presented as independent replication.
- Runtime parsing is closed and bounded. Unknown fields or references fail
  before persistence.
