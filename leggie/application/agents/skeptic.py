"""Calibrated Skeptic — adversarial critic with typed gates.

F4: Uses LLM when available for real adversarial review. Gates are a Chain of
Responsibility: cheap typed heuristics run first, then (when an LLM is
configured) an LLM adversarial gate does the sharp-reasoning pass the router's
``adversarial_critic`` route is tuned for (per routes.yaml, a stronger model
than the lens tier — its job is to CATCH legal errors the lens missed).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from leggie.application.ports.llm import LLMPort, LLMRequest
from leggie.application.ports.router import RouterPort
from leggie.domain.models import Confidence, Finding, FindingType
from leggie.domain.models.structured_output import SkepticVerdictResponse

log = logging.getLogger(__name__)

_CRITIC_TASK = "adversarial_critic"


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
            return SkepticVerdict(
                str(finding.id),
                "factual",
                "supports",
                "Rule references constitutional provisions",
                0.05,
            )
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


class LLMAdversarialGate(SkepticGate):
    """Adversarial LLM critic — actively tries to refute the finding.

    Uses a sharper-reasoning model (router's ``adversarial_critic`` route) than
    the lens that produced the finding, per the router config's stated intent:
    "Skeptic must CATCH legal errors the lens missed."
    """

    def __init__(
        self,
        llm: LLMPort,
        router: RouterPort | None = None,
        model: str = "",
    ) -> None:
        self._llm = llm
        self._router = router
        self._model = model

    async def examine(self, finding: Finding) -> SkepticVerdict:
        model = await self._select_model()
        system = (
            "Είσαι επικριτικός ελεγκτής νομικών ευρημάτων για ελληνικό νομοσχέδιο. "
            "Ο στόχος σου είναι να ΑΝΑΤΡΕΨΕΙΣ το εύρημα αν είναι λανθασμένο νομικά ή "
            "πραγματολογικά — μην το αποδέχεσαι απλώς επειδή ακούγεται εύλογο. Ψάξε "
            "για: εσφαλμένη επίκληση νόμου, μη υπαρκτό άρθρο, λογικό άλμα στο "
            "συμπέρασμα, υπερβολή στη σοβαρότητα. Αν δεν βρεις σφάλμα, verdict="
            "'supports' ή 'neutral'. Αν βρεις σαφές σφάλμα, verdict='refutes'."
        )
        prompt = (
            f"ΕΥΡΗΜΑ ΠΡΟΣ ΕΛΕΓΧΟ:\n"
            f"- Ζήτημα: {finding.irac.issue}\n"
            f"- Κανόνας: {finding.irac.rule}\n"
            f"- Εφαρμογή: {finding.irac.application}\n"
            f"- Συμπέρασμα: {finding.irac.conclusion}\n"
            f"- Σοβαρότητα: {finding.severity.value}\n\n"
            f"Έλεγξε κριτικά και δώσε την κρίση σου."
        )
        try:
            request = LLMRequest(
                prompt=prompt,
                system_prompt=system,
                model=model,
                max_tokens=2048,
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            obj, _ = await self._llm.generate_structured(request, SkepticVerdictResponse)
        except Exception as e:  # noqa: BLE001 — skeptic must never crash the run
            log.warning("skeptic_llm_error: finding=%s error=%s", finding.id, str(e)[:200])
            return SkepticVerdict(
                str(finding.id), "adversarial", "neutral", f"Critic error: {str(e)[:120]}"
            )

        if not isinstance(obj, SkepticVerdictResponse):
            return SkepticVerdict(str(finding.id), "adversarial", "neutral", "No verdict")

        verdict = (obj.verdict or "neutral").strip().lower()
        if verdict not in ("supports", "refutes", "neutral"):
            verdict = "neutral"
        log.info(
            "skeptic_verdict: finding=%s gate=adversarial verdict=%s adjustment=%.2f reason=%s",
            finding.id,
            verdict,
            obj.confidence_adjustment or 0.0,
            (obj.reason or "")[:120],
        )
        return SkepticVerdict(
            str(finding.id),
            "adversarial",
            verdict,
            obj.reason,
            obj.confidence_adjustment,
        )

    async def _select_model(self) -> str | None:
        if self._router is not None:
            try:
                route = await self._router.route(_CRITIC_TASK)
                return route.model
            except Exception:  # noqa: BLE001
                log.warning("skeptic_route_failed: using default model")
        return self._model or None


class CalibratedSkeptic:
    """Calibrated Skeptic — Chain of Responsibility of typed gates.

    Without an LLM: cheap typed heuristic gates only (never refutes).
    With an LLM: adds an adversarial LLM gate that can actually refute.
    """

    def __init__(
        self,
        gates: list[SkepticGate] | None = None,
        llm: LLMPort | None = None,
        router: RouterPort | None = None,
        model: str = "",
    ) -> None:
        if gates is not None:
            self._gates = gates
        else:
            self._gates = [NumericGate(), TemporalGate(), FactualGate(), ObligationGate()]
            if llm is not None:
                self._gates.append(LLMAdversarialGate(llm=llm, router=router, model=model))

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
                finding = finding.model_copy(
                    update={
                        "confidence": Confidence.from_score(
                            new_score, provenance="skeptic-calibrated"
                        ),
                        "version": finding.version + 1,
                    }
                )
            survivors.append(finding)
        return survivors, all_verdicts
