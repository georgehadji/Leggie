"""Domain models — frozen Pydantic entities and value objects.

All models are immutable (frozen=True). State changes produce new objects + events.
No I/O, no outward imports.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic.functional_validators import AfterValidator

from leggie.domain.models.parse_integrity import (
    ParseIntegrityReport as ParseIntegrityReport,
)
from leggie.domain.models.parse_integrity import (
    RejectedCandidate as RejectedCandidate,
)
from leggie.domain.models.parse_integrity import (
    RejectionReason as RejectionReason,
)

# DH-34: ``frozen=True`` blocks attribute REASSIGNMENT only — a list-valued
# field stayed the same fully mutable list, so ``finding.evidence.append(...)``
# succeeded silently on an instance the architecture calls immutable, and
# ``model_copy(update=...)`` (shallow at all four production call sites) handed
# the *same* list object to both the old and the new version, letting a
# mutation of the revision corrupt its own parent.
#
# ``Sequence`` rather than ``tuple[...]`` on purpose: lists are still accepted
# at every existing construction site (~56 of them, untouched), while mypy sees
# a read-only Sequence with no ``.append``, so the invariant is enforced
# statically as well as at runtime. Precedent: parse_integrity.py already types
# its collection fields immutably.
type Frozen[T] = Annotated[Sequence[T], AfterValidator(tuple)]

# ── Enums ──────────────────────────────────────────────────────────────────────


def is_greek(text: str, min_ratio: float = 0.50) -> bool:
    """Check if text contains at least *min_ratio* Greek-script characters.

    Pure domain function — no I/O.
    Greek Unicode range: U+0370–U+03FF (Greek & Coptic), U+1F00–U+1FFF (Extended).
    """
    if not text:
        return False
    greek_chars = sum(1 for ch in text if "\u0370" <= ch <= "\u03ff" or "\u1f00" <= ch <= "\u1fff")
    return (greek_chars / max(len(text), 1)) >= min_ratio


class FindingType(StrEnum):
    """Typology of legal findings per U3 — typed hallucination categories."""

    NUMERIC = "numeric"  # amounts, dates, thresholds
    TEMPORAL = "temporal"  # deadlines, effective dates, transition periods
    OBLIGATION_ENTITLEMENT = "obligation_entitlement"  # duties, rights, prohibitions
    FACTUAL = "factual"  # factual assertions about law, procedure, or context
    PROCEDURAL = "procedural"  # process, parliamentary procedure
    CONSTITUTIONAL = "constitutional"  # constitutional compatibility
    EU_COMPLIANCE = "eu_compliance"  # EU law alignment
    IMPLEMENTATION = "implementation"  # practical feasibility, administrative burden
    ECONOMIC = "economic"  # fiscal impact, cost analysis
    OTHER = "other"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class ConfidenceGrade(StrEnum):
    CERTAIN = "certain"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    VERY_LOW = "very_low"
    ABSTAIN = "abstain"  # below threshold — ship as "needs human expert"


class ModelTier(StrEnum):
    FREE = "free"
    BUDGET = "budget"
    PREMIUM = "premium"


class CitationScheme(StrEnum):
    FEK = "fek"  # ΦΕΚ issue/year/number
    CELEX = "celex"  # EU law identifier
    ECLI = "ecli"  # European Case Law Identifier
    URL = "url"
    UNKNOWN = "unknown"


class WorkflowState(StrEnum):
    IDLE = "idle"
    PREVIEWING = "previewing"
    INGESTING = "ingesting"
    PARSING = "parsing"
    PLANNING = "planning"
    EXECUTING = "executing"
    AGGREGATING = "aggregating"
    VERIFYING = "verifying"
    IMPROVING = "improving"
    REPORTING = "reporting"
    DONE = "done"
    FAILED = "failed"


class EventType(StrEnum):
    OVERVIEW_GENERATED = "overview_generated"
    ARTICLES_SELECTED = "articles_selected"
    ANALYSIS_STARTED = "analysis_started"
    LENS_COMPLETED = "lens_completed"
    FINDING_CREATED = "finding_created"
    FINDING_REFUTED = "finding_refuted"
    FINDING_CONFIRMED = "finding_confirmed"
    CITATION_VERIFIED = "citation_verified"
    CITATION_FAILED = "citation_failed"
    STAGE_COMPLETED = "stage_completed"
    WORKFLOW_COMPLETED = "workflow_completed"
    WORKFLOW_FAILED = "workflow_failed"
    BUDGET_TRIPPED = "budget_tripped"
    DEGRADED = "degraded"  # LLM call failed, pipeline degraded output
    DEDUP_REMOVED = "dedup_removed"  # Near-duplicate findings collapsed
    AGGREGATION_COMPLETED = "aggregation_completed"  # Blackboard aggregation finished


# ── Value Objects ──────────────────────────────────────────────────────────────


class Confidence(BaseModel):
    """Calibrated confidence score with provenance."""

    model_config = {"frozen": True}

    score: float = Field(ge=0.0, le=1.0, description="Calibrated confidence 0-1")
    grade: ConfidenceGrade = Field(description="Human-readable grade")
    calibration_provenance: str = Field(
        default="uncalibrated",
        description="Source of calibration (gold-set, independent verification, etc.)",
    )

    @classmethod
    def from_score(cls, score: float, provenance: str = "uncalibrated") -> Confidence:
        """Create confidence with auto-grade from raw score."""
        if score >= 0.95:
            grade = ConfidenceGrade.CERTAIN
        elif score >= 0.85:
            grade = ConfidenceGrade.HIGH
        elif score >= 0.70:
            grade = ConfidenceGrade.MEDIUM
        elif score >= 0.50:
            grade = ConfidenceGrade.LOW
        elif score >= 0.20:
            grade = ConfidenceGrade.VERY_LOW
        else:
            grade = ConfidenceGrade.ABSTAIN
        return cls(score=round(score, 4), grade=grade, calibration_provenance=provenance)

    def above_threshold(self, threshold: float = 0.5) -> bool:
        """Is this confidence above the emission threshold (U9)?"""
        return self.score >= threshold


class Citation(BaseModel):
    """A legal citation normalized to a known scheme."""

    model_config = {"frozen": True}

    scheme: CitationScheme
    identifier: str = Field(description="Normalized ID within the scheme")
    original_text: str = Field(description="Raw citation text as found in source")
    resolved: bool = False
    checked: bool = Field(
        default=False,
        description="True iff resolve() actually checked this citation against a "
        "configured index. False means 'not checked' — resolved=False here must "
        "never be read as 'disproven', only as 'unverified'.",
    )
    resolution_evidence: str | None = None

    @field_validator("identifier")
    @classmethod
    def identifier_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("identifier must not be empty")
        return v.strip()

    @model_validator(mode="after")
    def _resolved_implies_checked(self) -> Citation:
        if self.resolved and not self.checked:
            raise ValueError("resolved=True requires checked=True (was it actually checked?)")
        return self


class Evidence(BaseModel):
    """Supporting evidence for a finding."""

    model_config = {"frozen": True}

    citation: Citation | None = None
    text_excerpt: str | None = None
    source_document: str | None = None
    verdict: str | None = None  # "supports" | "contradicts" | "neutral"


class IRAC(BaseModel):
    """IRAC structure for legal reasoning (U2): Issue · Rule · Application · Conclusion."""

    model_config = {"frozen": True}

    issue: str = Field(description="The specific legal question or problem identified")
    rule: str = Field(description="The legal rule or principle that applies")
    application: str = Field(description="Application of the rule to the bill text")
    conclusion: str = Field(description="Reasoned conclusion on the issue")


# ── Entities ────────────────────────────────────────────────────────────────────


class Article(BaseModel):
    """A parsed article (Άρθρο) of a Greek bill."""

    model_config = {"frozen": True}

    id: str = Field(description="Article number (Άρθρο N)")
    title: str = Field(default="")
    paragraphs: Frozen[Paragraph] = Field(default_factory=tuple)
    raw_text: str = Field(description="Original text of the article")

    def paragraph_by_number(self, number: str) -> Paragraph | None:
        for p in self.paragraphs:
            if p.number == number:
                return p
        return None


class Paragraph(BaseModel):
    """A paragraph (παράγραφος) within an article."""

    model_config = {"frozen": True}

    number: str
    text: str
    subparagraphs: Frozen[SubParagraph] = Field(default_factory=tuple)


class SubParagraph(BaseModel):
    """A sub-paragraph (εδάφιο) within a paragraph."""

    model_config = {"frozen": True}

    letter: str  # α, β, γ, ...
    text: str


class Document(BaseModel):
    """A parsed legal document — composite tree of articles."""

    model_config = {"frozen": True}

    title: str
    document_id: str = Field(default_factory=lambda: str(uuid4()))
    source_format: str = Field(description="pdf, docx, html, txt")
    articles: Frozen[Article] = Field(default_factory=tuple)
    preamble: str = Field(default="")
    raw_text: str = Field(default="")


class ArticleOverview(BaseModel):
    """Purpose/provisions/consequences preview for one article (Stage 0).

    Produced before the deep multi-lens analysis so a caller can decide
    which article IDs to actually send into `Orchestrator.analyze_article`.
    """

    model_config = {"frozen": True}

    article_id: str
    title: str = Field(default="")
    purpose: str = Field(default="", description="What this article is trying to achieve")
    key_provisions: Frozen[str] = Field(
        default_factory=tuple, description="The most important rules/provisions in this article"
    )
    practical_consequences: str = Field(
        default="", description="Real-world practical effects of this article"
    )


class BillOverview(BaseModel):
    """Whole-bill preview: intro + summary + per-article overview.

    Generated by the preview stage, ahead of ingest/parse/analyze proper,
    so a user can pick which Άρθρο/Παράγραφος-level scope to analyze.
    """

    model_config = {"frozen": True}

    intro: str = Field(default="")
    summary: str = Field(default="")
    articles: Frozen[ArticleOverview] = Field(default_factory=tuple)

    def article_ids(self) -> list[str]:
        return [a.article_id for a in self.articles]


class Finding(BaseModel):
    """A finding from legal analysis — IRAC-structured (U2).

    Every finding is immutable, versioned, and traceable to its provenance.
    """

    model_config = {"frozen": True}

    id: UUID = Field(default_factory=uuid4)
    finding_type: FindingType = Field(description="Category of finding (U3 typed)")
    irac: IRAC = Field(description="IRAC legal reasoning structure")
    article_id: str = Field(
        default="",
        description="Article this finding is about. Empty for legacy/pre-fix "
        "findings — consumers fall back to parsing 'Άρθρο N' out of irac.issue "
        "via article_number_of().",
    )
    severity: Severity = Field(default=Severity.MEDIUM)
    confidence: Confidence = Field(description="Calibrated confidence score")
    evidence: Frozen[Evidence] = Field(default_factory=tuple)
    counter_evidence: Frozen[Evidence] = Field(default_factory=tuple)

    # Provenance
    lens: str = Field(description="Which lens produced this finding")
    model: str = Field(description="Model used (e.g., claude-sonnet-4)")
    model_tier: ModelTier = Field(default=ModelTier.BUDGET)
    prompt_hash: str = Field(default="", description="Hash of the prompt used")
    seed: int = Field(default=0, description="Random seed for reproducibility")

    # Versioning
    version: int = Field(default=1, description="Incremented on each mutation")
    parent_finding_id: UUID | None = Field(
        default=None, description="Finding this was derived from (if revised)"
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)

    def is_admissible(self, confidence_threshold: float = 0.5) -> bool:
        """Is this finding admissible for output (U9 abstention gate)?"""
        return self.confidence.above_threshold(confidence_threshold)


class Plan(BaseModel):
    """Analysis plan — deterministic decomposition of a document into lens tasks."""

    model_config = {"frozen": True}

    id: UUID = Field(default_factory=uuid4)
    document_id: str
    lens_tasks: Frozen[LensTask] = Field(default_factory=tuple)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class LensTask(BaseModel):
    """A single analysis task for one lens on one article."""

    model_config = {"frozen": True}

    lens: str
    article_id: str
    sample_count: int = Field(default=3, ge=1, le=10, description="VS sample count")


class Event(BaseModel):
    """An immutable event in the event-sourced spine."""

    # DH-35: no ``use_enum_values`` — it flattened event_type to its plain
    # ``.value`` right after validation, so the field's declared EventType
    # annotation was one Python itself did not honour: isinstance() was False
    # and ``.value`` raised AttributeError. Every consumer compares with ==,
    # ``in``, ``str()`` or a dict key, all of which a StrEnum satisfies
    # identically, and both model_dump(mode="json") and model_dump_json()
    # are byte-for-byte unchanged — so nothing observable depended on it.
    model_config = {"frozen": True}

    id: UUID = Field(default_factory=uuid4)
    event_type: EventType
    aggregate_id: str = Field(description="ID of the aggregate this event belongs to")
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    version: int = Field(default=1)
