# Label-withheld AI development check

A newly spawned GPT-6 Astra assessor received only blind_packets.json, without original labels, the paper, rankings or target scores. Six fixed query IDs were chosen before this check; within each query, eight candidates with the smallest SHA256 of the documented seed/query/claim key were selected. The48 candidates were not selected by prior label or score. The original labels were revealed only after fresh_judgments.json was completed.

Exact grade agreement is43/48; linear-weighted kappa is0.84. All5 grade disagreements are between grades0 and1. Scope-error Boolean agreement is38/48. The two AI readings differ about whether related, different-setting material constitutes a scope substitution. This check measures consistency on this deterministic development subset.

Original qrels and114-query table values are retained. comparison.json contains all disagreements, so the result is independently inspectable. Both grade and scope disagreements are reported. The fixed query subset has no grade2 items and only2 grade3 items in the original labels, so agreement must be interpreted with that class distribution.

Reference answers contain reference claim identifiers. Original grades/ranks were withheld, but reference identities were visible. The recorded consistency numbers are not a fully reference-blinded reliability estimate.
