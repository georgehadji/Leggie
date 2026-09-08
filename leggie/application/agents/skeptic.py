"""Calibrated Skeptic — adversarial critic with typed gates.

F4: Uses LLM when available for real adversarial review. Gates are a Chain of
Responsibility: cheap typed heuristics run first, then (when an LLM is
configured) an LLM adversarial gate does the sharp-reasoning pass the router's
``adversarial_critic`` route is tuned for (per routes.yaml, a stronger model
than the lens tier — its job is to CATCH legal errors the lens missed).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from leggie.application.ports.llm import LLMPort, LLMRequest
from leggie.application.ports.router import RouterPort
from leggie.config.settings import get_settings
from leggie.domain.models import Confidence, Finding, FindingType
from leggie.domain.models.structured_output import SkepticVerdictResponse
from leggie.observability import get_logger

log = get_logger(__name__)

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
        model, critic_max_tokens = await self._select_model()
        # DH-42: 'refutes' DELETES the finding (see CalibratedSkeptic.review),
        # so the grounds for it must be grounds for deletion. This prompt used
        # to list four: wrong law cited, nonexistent article, logical leap in
        # the conclusion, exaggerated severity — then said "if you find a clear
        # error, refutes". The first two falsify a finding. The last two are
        # disagreements about how strongly it was put, and 12 of the 23
        # refutations measured on 2026-09-08 were of that kind: "κάνει λογικό
        # άλμα", "υπερβάλλει στη σοβαρότητα". A real observation, overstated,
        # was being deleted rather than toned down.
        #
        # The two now route differently. Falsification still refutes and still
        # deletes; overstatement comes back as 'neutral' carrying a negative
        # confidence_adjustment, which review() folds into the score so the
        # finding survives at lower rank with the objection recorded. No schema
        # change: verdict + confidence_adjustment + reason already express this.
        system = (
            "Είσαι επικριτικός ελεγκτής νομικών ευρημάτων για ελληνικό νομοσχέδιο. "
            "Μην αποδέχεσαι ένα εύρημα απλώς επειδή ακούγεται εύλογο.\n\n"
            "Διάκρινε ΑΥΣΤΗΡΑ δύο περιπτώσεις:\n\n"
            "1) Το εύρημα είναι ΨΕΥΔΕΣ — επικαλείται διάταξη που δεν υπάρχει, "
            "αποδίδει σε άρθρο περιεχόμενο που δεν έχει, ή η νομική βάση του "
            "διαψεύδεται από το ίδιο το κείμενο. ΤΟΤΕ verdict='refutes'. Το "
            "εύρημα ΔΙΑΓΡΑΦΕΤΑΙ, οπότε χρησιμοποίησε το 'refutes' μόνο όταν "
            "μπορείς να δείξεις τι λέει πράγματι η διάταξη.\n\n"
            "2) Η παρατήρηση ΣΤΕΚΕΙ αλλά έχει διατυπωθεί υπερβολικά — λογικό "
            "άλμα στο συμπέρασμα, υπερβολή στη σοβαρότητα, ισχυρισμός πιο "
            "κατηγορηματικός απ' όσο στηρίζει το κείμενο. ΤΟΤΕ verdict='neutral' "
            "με ΑΡΝΗΤΙΚΟ confidence_adjustment (-0.1 έως -0.4 ανάλογα με την "
            "υπερβολή) και εξήγησε στο reason τι ακριβώς υπερβάλλει. ΜΗΝ "
            "χρησιμοποιείς 'refutes' εδώ: η ένσταση αφορά τον τόνο, όχι την "
            "ορθότητα.\n\n"
            "Αν το εύρημα είναι βάσιμο και σωστά διατυπωμένο, verdict='supports'."
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
                max_tokens=critic_max_tokens,
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

    async def _select_model(self) -> tuple[str | None, int]:
        """Return (model, max_tokens) from the router.

        TOK-4: RouteResult carries max_tokens (8192 for adversarial_critic).
        """
        if self._router is not None:
            try:
                route = await self._router.route(_CRITIC_TASK)
                return route.model, route.max_tokens
            except Exception:  # noqa: BLE001
                log.warning("skeptic_route_failed: using default route")
        return self._model or None, 8192


def _bounded_adjustment(verdict: SkepticVerdict) -> float:
    """Keep a verdict's confidence shift pointing the way the verdict argues.

    ``confidence_adjustment`` arrives straight from the critic model, which
    returns a magnitude with no guaranteed sign: the 2026-09-08 smoke logged
    ``verdict=refutes adjustment=0.50``, a *positive* half-point on a
    refutation. A "supports" that lowers confidence, or a "refutes" that raises
    it, is the model contradicting itself, and taking the number at face value
    lets that contradiction move the finding's score.

    Scope is deliberately narrow. Only the sign is constrained, and only where
    the verdict asserts a direction:

    * ``neutral`` is passed through untouched, sign and magnitude. A gate can
      legitimately decline to take a position while still docking confidence —
      the deterministic gates do exactly that.
    * Magnitudes are NOT capped. Clamping them would shrink penalties, i.e.
      make MORE findings survive, which is the "raise yield by loosening a
      gate" move DH-42 fences.
    """
    if verdict.verdict == "supports":
        return abs(verdict.confidence_adjustment)
    if verdict.verdict == "refutes":
        return -abs(verdict.confidence_adjustment)
    return verdict.confidence_adjustment


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
        """Run every gate, in order, isolating each gate's own failure.

        Each gate is documented to catch its own exceptions and degrade to a
        neutral verdict (see LLMAdversarialGate). Before this fix that only
        held for the gates that happened to guard themselves: a gate raising
        here would abort the whole chain via the list comprehension, silently
        discarding every earlier gate's verdict and skipping every later gate
        instead of degrading just the one gate that failed.
        """
        verdicts: list[SkepticVerdict] = []
        for gate in self._gates:
            try:
                verdicts.append(await gate.examine(finding))
            except Exception:
                log.exception(
                    "skeptic_gate_error: gate=%s finding=%s", type(gate).__name__, finding.id
                )
                verdicts.append(SkepticVerdict(str(finding.id), "unknown", "neutral", "Gate error"))
        return verdicts

    async def review(
        self, findings: list[Finding], max_concurrency: int | None = None
    ) -> tuple[list[Finding], list[SkepticVerdict]]:
        """Review a batch of findings with bounded fan-out (PROD-36).

        Each finding is examined independently under a semaphore. Results
        are folded in **input order** so model_copy confidence adjustments
        and the survivor list are order-stable.

        ``max_concurrency`` defaults to ``LLMSettings.max_skeptic_concurrency``
        (SSOT-3); it used to be a literal 10 here, so the documented env var
        was read by nothing.
        """
        if not findings:
            return [], []

        if max_concurrency is None:
            max_concurrency = get_settings().llm.max_skeptic_concurrency
        semaphore = asyncio.Semaphore(max_concurrency)

        async def _review_one(finding: Finding) -> tuple[list[SkepticVerdict], Finding | None]:
            async with semaphore:
                try:
                    verdicts = await self.examine(finding)
                    refuted = any(v.verdict == "refutes" for v in verdicts)
                    if refuted:
                        return verdicts, None
                    adjustment = sum(_bounded_adjustment(v) for v in verdicts)
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
                    return verdicts, finding
                except Exception:
                    log.exception("skeptic_review_failed: finding=%s", finding.id)
                    return [
                        SkepticVerdict(str(finding.id), "adversarial", "neutral", "Review error")
                    ], finding

        results = await asyncio.gather(
            *(_review_one(f) for f in findings),
            return_exceptions=True,
        )

        all_verdicts: list[SkepticVerdict] = []
        survivors: list[Finding] = []
        for f, r in zip(findings, results, strict=True):
            if isinstance(r, BaseException):
                log.error("skeptic_critical: finding=%s error=%s", f.id, r)
                survivors.append(f)
                continue
            verdicts, survivor = r
            all_verdicts.extend(verdicts)
            if survivor is not None:
                survivors.append(survivor)

        return survivors, all_verdicts
