"""Specification objects — composable boolean business rules.

Specification pattern: compose rules with and/or/not for admissibility,
confidence gates, citation validity, and abstention decisions (U9).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TypeVar

from leggie.domain.models import Citation, Finding

T = TypeVar("T")


class Spec[T](ABC):
    """Base specification — composable boolean check."""

    @abstractmethod
    def is_satisfied_by(self, candidate: T) -> bool: ...

    def __and__(self, other: Spec[T]) -> Spec[T]:
        return AndSpec(self, other)

    def __or__(self, other: Spec[T]) -> Spec[T]:
        return OrSpec(self, other)

    def __invert__(self) -> Spec[T]:
        return NotSpec(self)


class AndSpec(Spec[T]):
    def __init__(self, left: Spec[T], right: Spec[T]) -> None:
        self._left = left
        self._right = right

    def is_satisfied_by(self, candidate: T) -> bool:
        return self._left.is_satisfied_by(candidate) and self._right.is_satisfied_by(candidate)


class OrSpec(Spec[T]):
    def __init__(self, left: Spec[T], right: Spec[T]) -> None:
        self._left = left
        self._right = right

    def is_satisfied_by(self, candidate: T) -> bool:
        return self._left.is_satisfied_by(candidate) or self._right.is_satisfied_by(candidate)


class NotSpec(Spec[T]):
    def __init__(self, spec: Spec[T]) -> None:
        self._spec = spec

    def is_satisfied_by(self, candidate: T) -> bool:
        return not self._spec.is_satisfied_by(candidate)


# ── Concrete Specifications ────────────────────────────────────────────────────


class FindingAdmissible(Spec[Finding]):
    """A finding is admissible for output if confidence is above threshold (U9)."""

    def __init__(self, threshold: float = 0.5) -> None:
        self._threshold = threshold

    def is_satisfied_by(self, finding: Finding) -> bool:
        return finding.confidence.above_threshold(self._threshold)


class CitationResolves(Spec[Citation]):
    """A citation is valid iff it resolves against the parser index (U1)."""

    def __init__(self, resolution_index: set[str] | None = None) -> None:
        self._resolution_index = resolution_index or set()

    def is_satisfied_by(self, citation: Citation) -> bool:
        # If we have a resolution index, check it
        if self._resolution_index:
            return citation.identifier in self._resolution_index
        # Otherwise, trust the resolved flag
        return citation.resolved

    def with_index(self, index: set[str]) -> CitationResolves:
        return CitationResolves(index)


class MeetsSeverityThreshold(Spec[Finding]):
    """Finding meets minimum severity level."""

    _SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

    def __init__(self, min_severity: str = "low") -> None:
        self._min_order = self._SEVERITY_ORDER.get(min_severity, 3)

    def is_satisfied_by(self, finding: Finding) -> bool:
        order = self._SEVERITY_ORDER.get(finding.severity.value, 5)
        return order <= self._min_order


class HasVerifiedCitations(Spec[Finding]):
    """Finding has at least one verified citation (or no citations needed)."""

    def __init__(self, require_citations: bool = False) -> None:
        self._require_citations = require_citations

    def is_satisfied_by(self, finding: Finding) -> bool:
        if not finding.evidence:
            return not self._require_citations
        verified = any(e.citation and e.citation.resolved for e in finding.evidence if e.citation)
        if self._require_citations:
            return verified
        return True  # citations helpful but not required
