"""Independent verification that never treats verifier mistakes as bad Claims.

Each fresh verifier attempt is checked assessment by assessment. Every
individually valid assessment is kept, keyed by Claim and verifier model, so a
retry only has to supply what earlier attempts got wrong. A Claim leaves the
graph for verification reasons only after the bounded attempts are exhausted,
within the cumulative local-loss budget, and with an explicit audit record.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass, field

from .errors import ContractIssue, ContractValidationError
from .graph_contract import (
    Assessment,
    Claim,
    EvidencePaths,
    Observation,
    PaperEvidenceGraph,
    SourceAnchor,
    StructuredVisualEvidence,
    VerificationReport,
    canonical_json,
)

_MAX_IDENTIFIER_LENGTH = 128


@dataclass(frozen=True, slots=True)
class AssessmentFinding:
    """One rejected assessment, located in the attempt that produced it."""

    index: int
    claim_id: str | None
    issue: ContractIssue
    field: str

    @property
    def loc(self) -> tuple[str | int, ...]:
        return ("assessments", self.index, self.field)


@dataclass(slots=True)
class _Coverage:
    accepted: dict[str, Assessment] = field(default_factory=dict)
    assessment_ids: set[str] = field(default_factory=set)
    contributions: list[str] = field(default_factory=list)
    renamed: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class MergedVerification:
    report: VerificationReport
    contributions: tuple[str, ...]
    renamed: tuple[tuple[str, str], ...]


def payload_digest(payload: object) -> str:
    """Stable identity of one parsed verifier payload."""

    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


class VerificationAccumulator:
    """Collect individually valid assessments across fresh verifier attempts."""

    def __init__(self, graph: PaperEvidenceGraph) -> None:
        self.graph = graph
        self.claims = {claim.id: claim for claim in graph.claims}
        self.claim_order = tuple(claim.id for claim in graph.claims)
        self.nodes = {
            item.id: item
            for collection in (
                graph.claims,
                graph.observations,
                graph.reasoning,
                graph.assumptions,
                graph.anchors,
                graph.visual_evidence,
            )
            for item in collection
        }
        self._evidence_paths = EvidencePaths(graph)
        self._paths: dict[str, set[str]] = {}
        self._coverage: dict[str, _Coverage] = {}
        self.unknown_claim_ids: set[str] = set()
        self.unattributable_records = 0
        self.attempts = 0
        self.last_findings: dict[str, AssessmentFinding] = {}

    def _path(self, claim_id: str) -> set[str]:
        path = self._paths.get(claim_id)
        if path is None:
            path = self._evidence_paths.closure((claim_id,))
            self._paths[claim_id] = path
        return path

    def _assessment_issue(
        self, assessment: Assessment, claim: Claim
    ) -> tuple[ContractIssue, str] | None:
        if assessment.scoped_claim_id != claim.scoped_claim_id:
            return (
                ContractIssue.VERIFICATION_SCOPED_CLAIM_ID_MISMATCH,
                "scoped_claim_id",
            )
        evidence_ids = assessment.evidence_ids
        if len(evidence_ids) != len(set(evidence_ids)):
            return ContractIssue.VERIFICATION_EVIDENCE_ID_DUPLICATE, "evidence_ids"
        if set(evidence_ids) - set(self.nodes):
            return ContractIssue.VERIFICATION_EVIDENCE_ID_UNKNOWN, "evidence_ids"
        evidence = [self.nodes[item] for item in evidence_ids]
        if any(isinstance(item, Claim) for item in evidence):
            return ContractIssue.VERIFICATION_EVIDENCE_OUTSIDE_PATH, "evidence_ids"
        if set(evidence_ids) - self._path(assessment.claim_id):
            return ContractIssue.VERIFICATION_EVIDENCE_OUTSIDE_PATH, "evidence_ids"
        if assessment.verdict != "insufficient_evidence" and not any(
            isinstance(item, SourceAnchor | StructuredVisualEvidence | Observation)
            for item in evidence
        ):
            return ContractIssue.VERIFICATION_EVIDENCE_OUTSIDE_PATH, "evidence_ids"
        return None

    def absorb(
        self,
        report: VerificationReport,
        *,
        digest: str,
        schema_invalid_claim_ids: Collection[str] = (),
        unattributable_records: int = 0,
    ) -> tuple[AssessmentFinding, ...]:
        """Keep every valid assessment from one attempt; return its rejections."""

        self.attempts += 1
        self.unattributable_records += unattributable_records
        coverage = self._coverage.setdefault(report.verifier_model, _Coverage())
        id_counts = Counter(item.id for item in report.assessments)
        claim_counts = Counter(item.claim_id for item in report.assessments)
        findings: list[AssessmentFinding] = []
        contributed = False
        for index, assessment in enumerate(report.assessments):
            claim = self.claims.get(assessment.claim_id)
            located: tuple[ContractIssue, str] | None
            if claim is None:
                # Reported, never silently filtered: the real Claim stays
                # pending and is requested again.
                self.unknown_claim_ids.add(assessment.claim_id)
                located = ContractIssue.VERIFICATION_CLAIM_UNKNOWN, "claim_id"
            elif id_counts[assessment.id] > 1:
                located = ContractIssue.VERIFICATION_ASSESSMENT_ID_DUPLICATE, "id"
            elif claim_counts[assessment.claim_id] > 1:
                located = (
                    ContractIssue.VERIFICATION_CLAIM_ASSESSED_MULTIPLE_TIMES,
                    "claim_id",
                )
            else:
                located = self._assessment_issue(assessment, claim)
            if located is not None:
                finding = AssessmentFinding(
                    index=index,
                    claim_id=None if claim is None else claim.id,
                    issue=located[0],
                    field=located[1],
                )
                findings.append(finding)
                if claim is not None:
                    self.last_findings[claim.id] = finding
                continue
            if assessment.claim_id in coverage.accepted:
                continue
            accepted = assessment
            if assessment.id in coverage.assessment_ids:
                renamed_id = _unique_assessment_id(
                    assessment.id, self.attempts, coverage.assessment_ids
                )
                coverage.renamed.append((assessment.id, renamed_id))
                accepted = assessment.model_copy(update={"id": renamed_id})
            coverage.accepted[assessment.claim_id] = accepted
            coverage.assessment_ids.add(accepted.id)
            self.last_findings.pop(assessment.claim_id, None)
            contributed = True
        for claim_id in schema_invalid_claim_ids:
            if claim_id in self.claims and claim_id not in coverage.accepted:
                self.last_findings.setdefault(
                    claim_id,
                    AssessmentFinding(
                        index=self.claim_order.index(claim_id),
                        claim_id=claim_id,
                        issue=ContractIssue.VERIFICATION_CLAIM_UNASSESSED,
                        field="claim_id",
                    ),
                )
        if contributed:
            coverage.contributions.append(digest)
            for warning in report.warnings:
                if warning not in coverage.warnings:
                    coverage.warnings.append(warning)
        return tuple(findings)

    def missing(self, model: str) -> tuple[str, ...]:
        coverage = self._coverage.get(model)
        accepted = {} if coverage is None else coverage.accepted
        return tuple(
            claim_id for claim_id in self.claim_order if claim_id not in accepted
        )

    def best_model(self) -> str | None:
        """The verifier model with the most accepted assessments, if any."""

        best: tuple[int, str] | None = None
        for model, coverage in self._coverage.items():
            if not coverage.accepted:
                continue
            key = (len(coverage.accepted), model)
            if best is None or key[0] > best[0]:
                best = key
        return None if best is None else best[1]

    def retry_error(
        self,
        model: str,
        findings: tuple[AssessmentFinding, ...],
    ) -> ContractValidationError:
        """One located, value-free reason the latest attempt is incomplete."""

        missing = set(self.missing(model))
        for finding in findings:
            if finding.claim_id in missing:
                return ContractValidationError(finding.issue, loc=finding.loc)
        for finding in findings:
            if finding.issue is ContractIssue.VERIFICATION_CLAIM_UNKNOWN:
                return ContractValidationError(finding.issue, loc=finding.loc)
        first_missing = next(
            index
            for index, claim_id in enumerate(self.claim_order)
            if claim_id in missing
        )
        return ContractValidationError(
            ContractIssue.VERIFICATION_CLAIM_UNASSESSED,
            loc=("assessments", first_missing, "claim_id"),
        )

    def merged(
        self,
        model: str,
        *,
        graph: PaperEvidenceGraph,
        graph_sha256: str,
        warnings: Collection[str] = (),
    ) -> MergedVerification:
        """Build one exact-once report for every Claim in ``graph``."""

        coverage = self._coverage[model]
        assessments = [coverage.accepted[claim.id] for claim in graph.claims]
        report = VerificationReport.model_validate(
            {
                "schema_version": "1.0",
                "paper_id": graph.paper_id,
                "graph_sha256": graph_sha256,
                "verifier_model": model,
                "assessments": [
                    item.model_dump(mode="json") for item in assessments
                ],
                "warnings": list(dict.fromkeys((*coverage.warnings, *warnings))),
            },
            strict=True,
        )
        report.validate_against(graph)
        return MergedVerification(
            report=report,
            contributions=tuple(coverage.contributions),
            renamed=tuple(coverage.renamed),
        )


def _unique_assessment_id(base: str, attempt: int, taken: set[str]) -> str:
    suffix = f".a{attempt}"
    candidate = base[: _MAX_IDENTIFIER_LENGTH - len(suffix)] + suffix
    counter = 1
    while candidate in taken:
        tail = f".a{attempt}.{counter}"
        candidate = base[: _MAX_IDENTIFIER_LENGTH - len(tail)] + tail
        counter += 1
    return candidate


__all__ = [
    "AssessmentFinding",
    "MergedVerification",
    "VerificationAccumulator",
    "payload_digest",
]
