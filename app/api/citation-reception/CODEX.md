# Citation Reception API

- This route owns the rebuildable, cross-paper A→B analysis projection. It must
  never mutate or delay either paper's immutable Evidence Graph.
- A bibliography DOI is matched only to a local target whose identity came from
  a successful OpenAlex Work lookup. Model/fallback `matched_doi` values are not
  authoritative, and multiple active local families must cause abstention.
- `GET` is read-only and independently loadable by the paper detail UI. `POST`
  accepts only a target snapshot processing command; browsers never submit
  model-authored assertions or assessments.
- Strong confirmation labels require both a faithful verifier result and a
  concrete evidence entity from citing paper A. Exact citation contexts and
  source artifact metadata must survive hydration.
- Citation assertions are not corpus Claim edges and must not be projected into
  `corpus_claim_graph_edges`.
