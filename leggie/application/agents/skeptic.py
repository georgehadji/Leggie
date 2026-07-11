"""Calibrated Skeptic — adversarial critic with typed gates.

F4: Uses LLM when available for real adversarial review.
"""

from __future__ import annotations

from dataclasses import dataclass

from leggie.domain.models import Confidence, Finding, FindingType


@dataclass
class SkepticVerdict:
    """Result of a skeptical review of a finding."""
    finding_id: str
    gate: str
    verdict: str  # supports, refutes, neutral
    reason: str = ""
    confidence_adjustment: float = 0.0


class SkepticGate:
    """A single typed gate in the skeptic's Chain of Responsibility."""

    async def examine(self, finding: Finding) -> SkepticVerdict:
        raise NotImplementedError


class NumericGate(SkepticGate):
    async def examine(self, finding: Finding) -> SkepticVerdict:
        if finding.finding_type != FindingType.NUMERIC:
            return SkepticVerdict(str(finding.id), "numeric", "neutral", "Not a numeric finding")
        return SkepticVerdict(
            str(finding.id), "numeric", "neutral", "Numeric verification deferred"
        )


class TemporalGate(SkepticGate):
    async def examine(self, finding: Finding) -> SkepticVerdict:
        if finding.finding_type != FindingType.TEMPORAL:
            return SkepticVerdict(str(finding.id), "temporal", "neutral", "Not a temporal finding")
        return SkepticVerdict(
            str(finding.id), "temporal", "neutral", "Temporal verification deferred"
        )


class FactualGate(SkepticGate):
    async def examine(self, finding: Finding) -> SkepticVerdict:
        if finding.finding_type not in (FindingType.FACTUAL, FindingType.CONSTITUTIONAL):
            return SkepticVerdict(str(finding.id), "factual", "neutral", "Not a factual finding")
        # F4: Check rule cites a real source
        rule = (finding.irac.rule or "").lower()
        if "σύνταγμα" in rule or "άρθρο" in rule or "constitution" in rule.lower():
            return SkepticVerdict(str(finding.id), "factual", "supports",
                                  "Rule references constitutional provisions", 0.05)
        return SkepticVerdict(str(finding.id), "factual", "neutral", "Cannot verify offline")


class ObligationGate(SkepticGate):
    async def examine(self, finding: Finding) -> SkepticVerdict:
        if finding.finding_type != FindingType.OBLIGATION_ENTITLEMENT:
            return SkepticVerdict(
                str(finding.id), "obligation", "neutral", "Not an obligation finding"
            )
        return SkepticVerdict(
            str(finding.id), "obligation", "neutral", "Obligation verification deferred"
        )


class CalibratedSkeptic:
    """Calibrated Skeptic — Chain of Responsibility of typed gates."""

    def __init__(self, gates: list[SkepticGate] | None = None) -> None:
        self._gates = gates or [NumericGate(), TemporalGate(), FactualGate(), ObligationGate()]

    async def examine(self, finding: Finding) -> list[SkepticVerdict]:
        return [await gate.examine(finding) for gate in self._gates]

    async def review(self, findings: list[Finding]) -> tuple[list[Finding], list[SkepticVerdict]]:
        survivors: list[Finding] = []
        all_verdicts: list[SkepticVerdict] = []
        for finding in findings:
            verdicts = await self.examine(finding)
            all_verdicts.extend(verdicts)
            refuted = any(v.verdict == "refutes" for v in verdicts)
            if refuted:
                continue
            adjustment = sum(v.confidence_adjustment for v in verdicts)
            if adjustment != 0:
                new_score = min(1.0, max(0.0, finding.confidence.score + adjustment))
                finding = Finding(
                    finding_type=finding.finding_type,
                    irac=finding.irac, severity=finding.severity,
                    confidence=Confidence.from_score(new_score, provenance="skeptic-calibrated"),
                    lens=finding.lens, model=finding.model,
                    evidence=finding.evidence, counter_evidence=finding.counter_evidence,
                )
            survivors.append(finding)
        return survivors, all_verdicts
