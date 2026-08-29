"""Pure scoring functions — severity, novelty, confidence calculation.

All functions are pure: no I/O, no clocks, no randomness (seed injected).
"""

from __future__ import annotations

from collections.abc import Callable

from leggie.domain.models import Confidence, ConfidenceGrade, Finding, FindingType, Severity


def score_severity(finding: Finding) -> Severity:
    """Assign severity based on finding type and IRAC content.

    Pure function — derived from finding properties only.
    """
    # Type-based baseline
    type_baseline = {
        FindingType.CONSTITUTIONAL: Severity.HIGH,
        FindingType.EU_COMPLIANCE: Severity.HIGH,
        FindingType.NUMERIC: Severity.MEDIUM,
        FindingType.TEMPORAL: Severity.MEDIUM,
        FindingType.OBLIGATION_ENTITLEMENT: Severity.HIGH,
        FindingType.FACTUAL: Severity.MEDIUM,
        FindingType.PROCEDURAL: Severity.LOW,
        FindingType.IMPLEMENTATION: Severity.MEDIUM,
        FindingType.ECONOMIC: Severity.MEDIUM,
        FindingType.OTHER: Severity.LOW,
    }
    return type_baseline.get(finding.finding_type, Severity.LOW)


def score_novelty(
    finding: Finding,
    existing_findings: list[Finding],
    similarity_fn: Callable[[Finding, Finding], float],
    threshold: float = 0.85,
) -> float:
    """Score novelty of a finding relative to existing ones (0 = duplicate, 1 = novel)."""
    if not existing_findings:
        return 1.0
    similarities = [similarity_fn(finding, existing) for existing in existing_findings]
    max_sim = max(similarities) if similarities else 0.0
    return 1.0 - max_sim if max_sim >= threshold else 1.0


def combine_confidence(
    evidence_conf: float, verification_conf: float, weight_evidence: float = 0.4
) -> Confidence:
    """Combine evidence-based and verification-based confidence into a single score."""
    combined = evidence_conf * weight_evidence + verification_conf * (1.0 - weight_evidence)
    return Confidence.from_score(combined, provenance="combined(evidence+verification)")


def confidence_from_verification(
    verified_citations: int,
    total_citations: int,
    refuted_count: int,
) -> Confidence:
    """Derive confidence from citation verification results (O1/O4)."""
    if total_citations == 0:
        return Confidence.from_score(0.5, provenance="no-citations")
    verified_ratio = verified_citations / total_citations
    refuted_penalty = refuted_count / total_citations * 0.3
    score = (verified_ratio * 0.8) - refuted_penalty
    score = max(0.0, min(1.0, score))
    return Confidence.from_score(score, provenance="citation-verification")
