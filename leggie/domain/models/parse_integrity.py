"""Parse integrity domain model — structured result of parser validation.

Produced by the parser (infrastructure) and consumed by the application
flow to gate proceeding on a degraded parse. Immutable value objects.

See docs/PARSER_REMEDIATION_PLAN.md §6.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class RejectionReason(StrEnum):
    """Why a candidate heading was rejected by the parser."""

    CROSS_REFERENCE = "cross_reference"
    MONOTONIC_JUMP = "monotonic_jump"
    TOC_REGION = "toc_region"
    DUPLICATE_ID = "duplicate_id"
    UNKNOWN = "unknown"


class RejectedCandidate(BaseModel):
    """A candidate article heading the parser discarded, with reason."""

    model_config = {"frozen": True}

    number: str = Field(description="The article number that was rejected")
    reason: RejectionReason = Field(
        default=RejectionReason.UNKNOWN,
        description="Rejection reason: cross_reference, monotonic_jump, toc_region, duplicate_id",
    )
    offset: int = Field(description="Character offset in the source text")


class ParseIntegrityReport(BaseModel):
    """Structured report of parse quality and completeness.

    Produced by the parser alongside the Document. No silent drops:
    every rejected candidate is recorded.
    """

    model_config = {"frozen": True}

    articles_parsed: int = Field(description="Number of articles extracted")
    distinct_ids: int = Field(description="Number of unique article IDs")
    duplicate_ids: tuple[str, ...] = Field(
        default_factory=tuple, description="IDs that appeared more than once"
    )
    missing_numbers: tuple[int, ...] = Field(
        default_factory=tuple, description="Expected article numbers not found (gaps)"
    )
    empty_or_heading_only: tuple[str, ...] = Field(
        default_factory=tuple, description="Article IDs with no paragraphs"
    )
    toc_span: tuple[int, int] | None = Field(
        default=None, description="[start, end) of detected TOC region, if any"
    )
    rejected: tuple[RejectedCandidate, ...] = Field(
        default_factory=tuple, description="All rejected heading candidates"
    )

    @property
    def is_contiguous(self) -> bool:
        """True when article IDs form a contiguous sequence 1..N with no gaps."""
        if self.missing_numbers:
            return False
        return bool(self.articles_parsed)

    @property
    def is_clean(self) -> bool:
        """True when parse is fully clean: no duplicates, no gaps, no drops.

        This is the gate predicate for safe analysis.
        """
        return not self.duplicate_ids and not self.missing_numbers and not self.rejected
