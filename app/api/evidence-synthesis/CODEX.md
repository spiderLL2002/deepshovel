# Evidence Synthesis API

`POST /api/evidence-synthesis` is the Web orchestration boundary for comparing
one selected paper Claim or one normalized user viewpoint against the immutable
Claim corpus. It is not a second search implementation and it never writes a
temporary viewpoint or provisional relation into the corpus graph.

## Actions

- `normalize` sends bounded viewpoint text to the Compiler. The response may
  contain zero atomic Claims and `needs_user_definition`; a research question
  must never be converted into a fabricated answer.
- `compare` runs the existing high-recall preview retrieval, removes the source
  paper family, hydrates both sides' complete selected Evidence Paths from D1,
  and sends at most twelve frozen pairs to the independent comparison extractor
  and verifier.

Every required model stage must complete successfully. If relation ranking or the
independent comparison/verifier fails after GPT and DeepSeek attempts, return an
explicit error; never replace the answer with unassessed preview candidates.
Claim normalization/comparison use the explicit interactive model profile;
whole-document extraction keeps its batch policy.

## Scientific publication rules

- Retrieval similarity and the old query-relative classifier are candidates,
  not published relationships.
- The final response is derived only from the verifier assessment. The verifier
  may maintain, weaken, or abstain; it may never strengthen the frozen decision
  or select evidence outside it.
- Stance, Scope alignment, and directness remain separate. Disjoint or unknown
  Scope never enters support or contradiction. A narrower or partially
  overlapping supportive/contradictory result is displayed as an applicability
  qualification, not as a whole-Claim conflict.
- Support requires `candidate_to_query` direction. Equivalence is context, not
  an independent supporting vote. `no_relation`, `insufficient_to_determine`,
  and verifier abstention remain visible as unconfirmed.
- A paper's own verifier assessment concerns that Claim against its own source
  path. It is never relabelled as its stance toward the user's Claim.
- Counts are by active paper family and Claim candidate, not truth votes. Every
  response states that Top-12 retrieval is bounded and “not found” means only
  that the current corpus and candidate set did not confirm a relationship.

The route may select stored graph JSON only after retrieval has produced bounded
Claim references; it validates every graph before projecting the selected path.
It must never scan graph JSON for recall or accept service-authored prose as
source evidence.
