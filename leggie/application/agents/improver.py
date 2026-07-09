"""Improvement Engine — suggestion generation per finding.

Strategy pattern: interchangeable improvement strategies behind one interface.
- MinimalChangeStrategy: targeted text-level fixes
- ReformStrategy: broader structural reform suggestions

Per O2/DELEGATE-52: one-shot suggestions + verify — no long edit chains.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from leggie.domain.models import Finding, FindingType, Severity


@dataclass
class Suggestion:
    """A single suggestion for improving a legal text."""
    finding_id: str
    article_id: str
    suggestion_type: str  # "minimal_change", "reform"
    description: str
    proposed_change: str = ""
    priority: str = "medium"  # high, medium, low


class ImprovementStrategy(ABC):
    """Strategy for generating improvement suggestions."""

    @abstractmethod
    def strategy_name(self) -> str: ...

    @abstractmethod
    def generate(self, finding: Finding) -> list[Suggestion]: ...


class MinimalChangeStrategy(ImprovementStrategy):
    """Targeted text-level fixes for specific issues found."""

    def strategy_name(self) -> str:
        return "minimal_change"

    def generate(self, finding: Finding) -> list[Suggestion]:
        suggestions: list[Suggestion] = []

        if finding.finding_type == FindingType.CONSTITUTIONAL:
            suggestions.append(Suggestion(
                finding_id=str(finding.id),
                article_id=finding.irac.issue.split(" ")[1] if len(finding.irac.issue.split(" ")) > 1 else "",
                suggestion_type="minimal_change",
                description="Review delegation clause for constitutional limits per Article 43",
                proposed_change=f"Specify explicit criteria and limits for the delegated authority in Article {self._extract_article(finding)}",
                priority="high" if finding.severity == Severity.HIGH else "medium",
            ))

        elif finding.finding_type == FindingType.EU_COMPLIANCE:
            suggestions.append(Suggestion(
                finding_id=str(finding.id),
                article_id=self._extract_article(finding),
                suggestion_type="minimal_change",
                description=f"Ensure alignment with relevant EU directive: {finding.irac.rule[:80]}",
                proposed_change="Add explicit reference to the relevant EU directive and ensure full harmonization",
                priority="high",
            ))

        elif finding.finding_type == FindingType.ECONOMIC:
            suggestions.append(Suggestion(
                finding_id=str(finding.id),
                article_id=self._extract_article(finding),
                suggestion_type="minimal_change",
                description="Add fiscal impact assessment",
                proposed_change="Include a quantified fiscal impact analysis for the proposed measure",
                priority="medium",
            ))

        return suggestions

    def _extract_article(self, finding: Finding) -> str:
        parts = finding.irac.issue.split(" ")
        for p in parts:
            if p.isdigit():
                return p
        return ""


class ReformStrategy(ImprovementStrategy):
    """Broader structural reform suggestions."""

    def strategy_name(self) -> str:
        return "reform"

    def generate(self, finding: Finding) -> list[Suggestion]:
        suggestions: list[Suggestion] = []

        if finding.severity in (Severity.CRITICAL, Severity.HIGH):
            suggestions.append(Suggestion(
                finding_id=str(finding.id),
                article_id="",
                suggestion_type="reform",
                description=f"Consider structural reform: {finding.irac.issue[:80]}",
                proposed_change=f"Review the legislative approach for Article {self._extract_article(finding)}. Consider alternative approaches that address the core concern while maintaining legislative intent.",
                priority="high",
            ))

        return suggestions

    def _extract_article(self, finding: Finding) -> str:
        parts = finding.irac.issue.split(" ")
        for p in parts:
            if p.isdigit():
                return p
        return ""


class ImprovementEngine:
    """Improvement engine — generates suggestions per finding.

    Combines multiple strategies. One-shot generation per O2.
    """

    def __init__(self, strategies: list[ImprovementStrategy] | None = None) -> None:
        self._strategies = strategies or [
            MinimalChangeStrategy(),
            ReformStrategy(),
        ]

    async def generate_suggestions(self, findings: list[Finding]) -> list[Suggestion]:
        """Generate suggestions for all findings across all strategies."""
        all_suggestions: list[Suggestion] = []
        for finding in findings:
            for strategy in self._strategies:
                all_suggestions.extend(strategy.generate(finding))
        return all_suggestions
