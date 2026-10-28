"""Cross-language contract constants. GENERATED: do not edit.

Source: contracts/shared-contract.json. Regenerate with
``node scripts/generate-shared-contract.mjs``.
"""

from __future__ import annotations

from typing import Final, Literal

SHARED_CONTRACT_VERSION: Final = "deepshovel-shared-contract/1"
EXTRACTION_PROMPT_VERSION: Final = "compiler-extractor/1.0.0"
VERIFICATION_PROMPT_VERSION: Final = "compiler-verifier/1.0.0"
EVIDENCE_GRAPH_SCHEMA_VERSION: Final = "scientific-evidence-graph/1.0"
CLAIM_TYPES: Final = (
    "descriptive",
    "comparative",
    "correlational",
    "causal",
    "mechanistic",
    "methodological",
    "predictive",
)
ClaimType = Literal[
    "descriptive",
    "comparative",
    "correlational",
    "causal",
    "mechanistic",
    "methodological",
    "predictive",
]
CLAIM_POLARITIES: Final = (
    "affirmed",
    "negated",
    "uncertain",
)
ClaimPolarity = Literal[
    "affirmed",
    "negated",
    "uncertain",
]
CLAIM_MODALITIES: Final = (
    "asserted",
    "hypothesis",
    "conditional",
)
ClaimModality = Literal[
    "asserted",
    "hypothesis",
    "conditional",
]
REASONING_CONCLUSION_RELATION_NAMES: Final = (
    "infers",
    "contradicts",
    "qualifies",
)
NORMALIZED_REGION_EXTENT_MAX: Final = 1.000001
RETRIEVAL_INDEX_VERSION: Final = "evidence-retrieval/2.0"
RETRIEVAL_SEARCH_PROTOCOL_VERSION: Final = "evidence-retrieval/2.1"
RETRIEVAL_CONTEXT_PROTOCOL_VERSION: Final = "evidence-retrieval/2.3"
RETIRED_RETRIEVAL_PROTOCOL_VERSIONS: Final = (
    "evidence-retrieval/2.2",
)
QUERY_PLAN_HANDOFF_SCHEMA_VERSION: Final = "deepshovel-query-plan-handoff/1.0"
MAX_QUERY_PLAN_HANDOFF_CHARACTERS: Final = 700000
RETRIEVAL_CONTINUATION_KEY_ID: Final = "derived-auth-v1"
RETRIEVAL_CONTINUATION_TTL_SECONDS: Final = 1800
MAX_CONVERSATION_TURNS: Final = 16
MAX_CONTEXT_SUPPLEMENTS: Final = 8
MAX_CONTEXT_TEXT_CHARACTERS: Final = 8000
MAX_CONTEXT_TOTAL_BYTES: Final = 32768
MAX_CHECK_MANIFESTS: Final = 4096
MAX_INDEX_DOCUMENTS: Final = 2048
MAX_SEARCH_LIMIT: Final = 50
RETRIEVAL_SESSION_ID_PATTERN: Final = "^rs1\\.[A-Za-z0-9_-]{43}$"
RETRIEVAL_SESSION_TTL_SECONDS: Final = 1800
RETRIEVAL_SESSION_KINDS: Final = (
    "query_plan_handoff",
    "context",
    "clarification",
)
