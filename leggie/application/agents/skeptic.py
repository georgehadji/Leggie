"""Calibrated Skeptic — adversarial critic with typed gates.

Per U3/U4: typed gates targeted at measured failure modes.
Per O5: runs in independent/fresh context, blind to the author.

Gates are asymmetric: higher attack strength where the model is weakest.
Each gate returns a verdict: supports, refutes, or neutral.
"""

from __future__ import annotations

from dataclasses import dataclass

from leggie.domain.models import Finding, FindingType


@dataclass
class SkepticVerdict:
    """Result of a skeptical review of a finding."""
    finding_id: str
    gate: str
    verdict: str  # supports, refutes, neutral
    reason: str = ""
    confidence_adjustment: float = 0.0  # positive = more confident, negative = less


class SkepticGate:
    """A single typed gate in the skeptic's Chain of Responsibility."""

    def examine(self, finding: Finding) -> SkepticVerdict:
        """Examine a finding through this gate's lens.

        Returns a verdict with a confidence adjustment.
        """
        raise NotImplementedError


class NumericGate(SkepticGate):
    """Checks numeric assertions: amounts, dates, thresholds, percentages."""

    def examine(self, finding: Finding) -> SkepticVerdict:
        if finding.finding_type != FindingType.NUMERIC:
            return SkepticVerdict(
                finding_id=str(finding.id), gate="numeric",
                verdict="neutral", reason="Not a numeric finding",
            )
        # Phase 3: rule-based numeric checks; Phase 4+: LLM verification
        return SkepticVerdict(
            finding_id=str(finding.id), gate="numeric",
            verdict="neutral", reason="Numeric verification deferred to Phase 4",
        )


class TemporalGate(SkepticGate):
    """Checks temporal assertions: deadlines, effective dates, transitions."""

    def examine(self, finding: Finding) -> SkepticVerdict:
        if finding.finding_type != FindingType.TEMPORAL:
            return SkepticVerdict(
                finding_id=str(finding.id), gate="temporal",
                verdict="neutral", reason="Not a temporal finding",
            )
        return SkepticVerdict(
            finding_id=str(finding.id), gate="temporal",
            verdict="neutral", reason="Temporal verification deferred to Phase 4",
        )


class FactualGate(SkepticGate):
    """Checks factual assertions against known legal rules."""

    def examine(self, finding: Finding) -> SkepticVerdict:
        if finding.finding_type not in (FindingType.FACTUAL, FindingType.CONSTITUTIONAL):
            return SkepticVerdict(
                finding_id=str(finding.id), gate="factual",
                verdict="neutral", reason="Not a factual/constitutional finding",
            )
        # Phase 3: check rule cited in IRAC exists; Phase 4+: LLM verification
        rule_text = finding.irac.rule.lower()
        if "σύνταγμα" in rule_text or "άρθρο" in rule_text:
            return SkepticVerdict(
                finding_id=str(finding.id), gate="factual",
                verdict="supports",
                reason="Rule references constitutional provisions",
                confidence_adjustment=0.05,
            )
        return SkepticVerdict(
            finding_id=str(finding.id), gate="factual",
            verdict="neutral", reason="Cannot verify offline",
        )


class ObligationGate(SkepticGate):
    """Checks obligation/entitlement assertions: duties, rights, prohibitions."""

    def examine(self, finding: Finding) -> SkepticVerdict:
        if finding.finding_type != FindingType.OBLIGATION_ENTITLEMENT:
            return SkepticVerdict(
                finding_id=str(finding.id), gate="obligation",
                verdict="neutral", reason="Not an obligation finding",
            )
        return SkepticVerdict(
            finding_id=str(finding.id), gate="obligation",
            verdict="neutral", reason="Obligation verification deferred to Phase 4",
        )


class CalibratedSkeptic:
    """Calibrated Skeptic — Chain of Responsibility of typed gates.

    Runs each finding through all applicable gates.
    Survivors (not refuted) gain confidence; refuted findings are flagged.
    """

    def __init__(self, gates: list[SkepticGate] | None = None) -> None:
        self._gates = gates or [
            NumericGate(),
            TemporalGate(),
            FactualGate(),
            ObligationGate(),
        ]

    async def examine(self, finding: Finding) -> list[SkepticVerdict]:
        """Run a finding through all gates. Returns all verdicts."""
        return [gate.examine(finding) for gate in self._gates]

    async def review(
        self, findings: list[Finding],
    ) -> tuple[list[Finding], list[SkepticVerdict]]:
        """Review all findings. Returns (survivors, all_verdicts).

        Survivors: findings that were not refuted by any gate.
        Refuted findings are dropped with recorded reason.
        """
        survivors: list[Finding] = []
        all_verdicts: list[SkepticVerdict] = []

        for finding in findings:
            verdicts = await self.examine(finding)
            all_verdicts.extend(verdicts)

            # Check if any gate refuted this finding
            refuted = any(v.verdict == "refutes" for v in verdicts)
            if refuted:
                continue  # Drop refuted findings

            # Adjust confidence for survivors
            adjustment = sum(v.confidence_adjustment for v in verdicts)
            if adjustment != 0:
                from leggie.domain.models import Confidence
                new_score = min(1.0, max(0.0, finding.confidence.score + adjustment))
                finding = Finding(
                    finding_type=finding.finding_type,
                    irac=finding.irac,
                    severity=finding.severity,
                    confidence=Confidence.from_score(new_score, provenance="skeptic-calibrated"),
                    lens=finding.lens, model=finding.model,
                    evidence=finding.evidence, counter_evidence=finding.counter_evidence,
                )
            survivors.append(finding)

        return survivors, all_verdicts
