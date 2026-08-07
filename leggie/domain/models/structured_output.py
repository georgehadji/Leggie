"""Structured output schemas — Pydantic response DTOs for LLM calls.

Each DTO is validated at the infrastructure/application boundary (FIX_PLAN rule G).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

# Bound for confidence_adjustment. Kept as a *clamp*, not a hard pydantic
# bound: a model that emits e.g. -0.8 ("strongly wrong") must not cause the
# whole structured response to be REJECTED and the verdict thrown away
# (a real parse-failure cause, docs/REMEDIATION_PLAN_V3.md D18). Both
# consumers (skeptic.review, cove._apply_revision) already clamp the *final*
# Confidence.score into [0,1], so the raw delta's range is advisory only.
_ADJ_MIN, _ADJ_MAX = -0.5, 0.5


def _clamp_adjustment(v: Any) -> float:
    """Coerce and clamp a model-emitted confidence adjustment into range.

    Non-numeric or missing -> 0.0. Out-of-range -> nearest bound. Never raises,
    so an over-eager adjustment can't nuke an otherwise-valid verdict.
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return max(_ADJ_MIN, min(_ADJ_MAX, f))


def _clamp_probability(v: Any) -> float:
    """Coerce and clamp a model-emitted probability into [0, 1].

    Same rationale as _clamp_adjustment (D18): a lens emitting probability
    1.5 must not REJECT the whole LensFindings response and discard every
    finding in it. Non-numeric/missing -> 0.5 (the field's neutral default).
    The clamped value feeds Confidence.from_score, which requires [0, 1].
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, f))


class IRACCandidate(BaseModel):
    """A single IRAC finding candidate from the LLM (F1 structured output)."""
    issue: str = Field(description="The specific legal question or problem")
    rule: str = Field(description="The legal rule or principle that applies")
    application: str = Field(description="Application of the rule to the bill text")
    conclusion: str = Field(description="Reasoned conclusion on the issue")
    verbatim_quote: str = Field(default="", description="Exact text span from the article")
    severity: str = Field(default="medium", description="critical, high, medium, low, info")
    probability: float = Field(default=0.5, description="Self-reported probability/confidence (clamped to [0, 1])")

    _clamp_prob = field_validator("probability", mode="before")(
        lambda v: _clamp_probability(v)
    )


class LensFindings(BaseModel):
    """Response schema for a single lens analysis on one article."""
    findings: list[IRACCandidate] = Field(default_factory=list, description="List of findings. Empty if no issues found.")


class VSCandidate(BaseModel):
    """A candidate from Verbalized Sampling with probability."""
    issue: str = Field(description="The specific legal question")
    rule: str = Field(description="The legal rule or principle")
    application: str = Field(description="Application to the bill text")
    conclusion: str = Field(description="Reasoned conclusion")
    verbatim_quote: str = Field(default="", description="Exact text span")
    severity: str = Field(default="medium", description="critical, high, medium, low, info")
    probability: float = Field(default=0.5, description="Estimated probability this finding is real (clamped to [0, 1])")

    _clamp_prob = field_validator("probability", mode="before")(
        lambda v: _clamp_probability(v)
    )


class VSResponse(BaseModel):
    """Response schema for Verbalized Sampling (k candidates with probabilities)."""
    candidates: list[VSCandidate] = Field(description="K candidate findings with probabilities")


class SkepticVerdictResponse(BaseModel):
    """Response schema for Skeptic review of a single finding."""
    verdict: str = Field(description="supports, refutes, neutral")
    reason: str = Field(description="Brief explanation")
    confidence_adjustment: float = Field(default=0.0, description="Adjust finding confidence by this amount (clamped to [-0.5, 0.5])")

    _clamp_adj = field_validator("confidence_adjustment", mode="before")(
        lambda v: _clamp_adjustment(v)
    )


# ── Chain-of-Verification (CoVe) LLM schemas ─────────────────────────────
# Implements the 4-step CoVe loop: baseline → plan questions → factored
# answers → cross-check + revise. See services/cove_verifier.py.


class CoVeQuestionsResponse(BaseModel):
    """Phase 2 — verification questions planned for a baseline finding.

    Questions MUST be open-ended (factual answer required), never yes/no,
    so the model cannot simply agree with its own baseline claim.
    """
    questions: list[str] = Field(
        default_factory=list,
        description="Open-ended, factual verification questions (Greek). No yes/no questions.",
    )


class CoVeAnswerResponse(BaseModel):
    """Phase 3 — factored answer to a single verification question.

    Answered against source text only, WITHOUT the baseline finding in context.
    """
    answer: str = Field(description="Factual answer grounded in the source text (Greek).")
    supported_by_source: bool = Field(
        default=False,
        description="True only if the source text directly supports the answer.",
    )


class CoVeCrossCheckResponse(BaseModel):
    """Phase 4 — cross-check baseline claim against factored answers."""
    consistency: str = Field(
        description="One of: consistent, inconsistent, partially_consistent",
    )
    reason: str = Field(description="Brief justification (Greek).")
    keep: bool = Field(
        default=True,
        description="False => the finding is contradicted and should be dropped.",
    )
    revised_conclusion: str = Field(
        default="",
        description="Corrected conclusion when partially_consistent; empty otherwise.",
    )
    confidence_adjustment: float = Field(
        default=0.0,
        description="Adjust finding confidence by this amount (clamped to [-0.5, 0.5]).",
    )

    _clamp_adj = field_validator("confidence_adjustment", mode="before")(
        lambda v: _clamp_adjustment(v)
    )


# ── Bill preview (Stage 0) schemas ───────────────────────────────────────


class BillIntroSummary(BaseModel):
    """Response schema for the whole-bill intro + summary (preview stage, before ingest/analyze)."""
    intro: str = Field(description="Short 2-4 sentence introduction to the bill")
    summary: str = Field(description="Concise summary of what the bill does overall")


class ArticleOverviewCandidate(BaseModel):
    """Response schema for one article's purpose/provisions/consequences (preview stage)."""
    purpose: str = Field(default="", description="What this article is trying to achieve")
    key_provisions: list[str] = Field(
        default_factory=list, description="The most important rules/provisions in this article"
    )
    practical_consequences: str = Field(
        default="", description="Real-world practical effects of this article"
    )
