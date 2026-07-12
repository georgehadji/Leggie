"""Rerank Service — score and reorder findings.

Orders findings by composite score: severity × confidence × novelty.
Strategy pattern: interchangeable scorers behind one interface.

Phase 2: simple composite scoring. Phase 3+: LLM-boosted rerank.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from leggie.domain.models import Finding

_SEVERITY_WEIGHTS = {
    "critical": 1.0,
    "high": 0.8,
    "medium": 0.5,
    "low": 0.3,
    "info": 0.1,
}


@dataclass
class ScoredFinding:
    """A finding with its composite score."""

    finding: Finding
    composite_score: float = 0.0
    severity_score: float = 0.0
    confidence_score: float = 0.0
    novelty_score: float = 0.0


class Reranker(ABC):
    """Base reranker — Strategy pattern for scoring and ordering findings."""

    @abstractmethod
    async def score(self, finding: Finding, all_findings: list[Finding]) -> ScoredFinding:
        """Score a single finding in context of all findings."""
        ...

    async def rerank(self, findings: list[Finding]) -> list[ScoredFinding]:
        """Score and reorder all findings (descending composite score)."""
        scored = [await self.score(f, findings) for f in findings]
        scored.sort(key=lambda s: s.composite_score, reverse=True)
        return scored


class CompositeReranker(Reranker):
    """Composite reranker: severity × confidence × novelty.

    Phase 2: simple weighted product. No LLM involved.
    """

    def __init__(
        self,
        severity_weight: float = 0.4,
        confidence_weight: float = 0.4,
        novelty_weight: float = 0.2,
    ) -> None:
        self._severity_weight = severity_weight
        self._confidence_weight = confidence_weight
        self._novelty_weight = novelty_weight

    async def score(self, finding: Finding, all_findings: list[Finding]) -> ScoredFinding:
        severity = _SEVERITY_WEIGHTS.get(finding.severity.value, 0.3)
        confidence = finding.confidence.score
        novelty = self._compute_novelty(finding, all_findings)

        composite = (
            severity * self._severity_weight
            + confidence * self._confidence_weight
            + novelty * self._novelty_weight
        )

        return ScoredFinding(
            finding=finding,
            composite_score=round(composite, 4),
            severity_score=severity,
            confidence_score=confidence,
            novelty_score=novelty,
        )

    def _compute_novelty(self, finding: Finding, all_findings: list[Finding]) -> float:
        """Compute novelty relative to other findings.

        Simple text-overlap-based novelty for Phase 2.
        Phase 3+: embedding-based similarity.
        """
        if len(all_findings) <= 1:
            return 1.0

        # Compare issue text overlap with other findings
        keywords = set(finding.irac.issue.lower().split())
        if not keywords:
            return 0.5

        max_overlap = 0.0
        for other in all_findings:
            if other.id == finding.id:
                continue
            other_keywords = set(other.irac.issue.lower().split())
            if not other_keywords:
                continue
            overlap = len(keywords & other_keywords) / max(len(keywords), len(other_keywords))
            max_overlap = max(max_overlap, overlap)

        return 1.0 - max_overlap
