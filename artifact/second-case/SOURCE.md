# Source and provenance

Majid Taghipour, Mohammad Ghafoori, Gholam Reza Lashkaripour, Nasser Hafezi Moghaddas and Abdullah Molaghab (2019). *Estimation of the current stress field and fault reactivation analysis in the Asmari reservoir, SW Iran*. Petroleum Science16,513–526. DOI: https://doi.org/10.1007/s12182-019-0331-9 .

License: Creative Commons Attribution4.0, https://creativecommons.org/licenses/by/4.0/ . Confirmed on the publisher's Rights and permissions section https://link.springer.com/article/10.1007/s12182-019-0331-9 and independently on PDF page13. source.pdf is unmodified. Its SHA-256 is f87d7cdcf0d08a7557993c1966b56183882c0b87c68809d0519ea8060382de99, exactly equal to the artifact-version hash returned by the live retrieval. PDF page11 visually confirms the number and Table4; page13 confirms the three-method conclusion and license.

This is a purposefully chosen non-Nd demonstration, selected before a fresh retrieval run for explicit numeric scope and verifiable open license. One question was run once through the existing same-host preview→handoff→final protocol, with no retries or production modifications. It returned two support clusters from this paper, including the numeric claim and method agreement. Six total clusters from one paper are not six independent confirmations. No public-network success is claimed for this second case.

result.json contains a sanitized selected result and actual graph-derived observation path. The original shortest path is separately preserved: it traverses a methodological assumption rather than an observation. The additional observation path is drawn from the stored full graph and is explicitly labeled; it is not a fabricated production claim split. Observation obs.f2.assessed retains the stored numeric55 field and raw55–57MPa representation; do not treat numeric55 alone as the full range. The needed source anchors are PDF11 and13. task-fields.json is a human-readable/manual-entry worksheet, not UI prefill or a recorded user action.

The reuse qualification is substantive: source text summarizes55–57MPa, whereas Table4 gives F2 Pc55.03/55.31 and PCr57.58/57.81MPa for alternative equations. This summary is neither an uncertainty interval nor a pressure increment, and the comparison relies on specified modeling assumptions. It is not a universally validated operational safety threshold.


## Check the additional observation branch

full-graph.json preserves the actual GET graph object for this single source snapshot, with scientific node/edge values unchanged. Snapshot identity and root source-artifact identity are retained; account/review metadata is omitted. It also preserves the original retrieval shortest path for comparison. The stored graph is a recorded system artifact, not independently established scientific truth.

Run `python3 verify-path.py` (standard library only). It checks exact field-by-field equality of all five selected nodes and four edges against full-graph.json; directed connectivity anchor.text.f2.stability → obs.f2.assessed → reason.f2.evidence → reason.f2.safety → the returned claim; the observation’s anchor reference; snapshot/paper identity; and the SHA-256 of source.pdf against the stored root artifact and live result identity. It also checks that the original shortest path is unchanged, different from the additional branch and contains no observation.

The script was actually run and passed; path-verification.json records the result. No graph edges or snapshots were invented. The additional branch is assembled for this artifact from the recorded full graph and must not be labeled the automatically returned shortest path. This check establishes structural provenance, not the accuracy of the scientific interpretation.
