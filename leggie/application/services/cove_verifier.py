"""CoVe Evidence Loop — Chain of Verification for legal findings.

Implements the 4-step Chain-of-Verification (O1/U1):

  1. Baseline    — the Finding produced by a lens (may hallucinate).
  2. Plan        — generate OPEN-ENDED verification questions (never yes/no).
  3. Execute     — answer each question FACTORED: independently, without the
                   baseline finding in context, grounded only in the source
                   article text. This stops the model repeating its own errors.
  4. Cross-check — compare factored answers to the baseline claim, label it
                   CONSISTENT / INCONSISTENT / PARTIALLY_CONSISTENT, then revise
                   (fix the conclusion, adjust confidence) or drop the finding.

Two modes:
  * Deterministic (no LLM): resolve citations via the parser + verbatim-quote
    substring gate. Cheap anti-hallucination floor. Never drops on its own.
  * LLM CoVe (llm provided): the full 4-step loop above. Uses the router's
    ``evidence_verification`` route for model selection when available.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel

from leggie.application.ports.citation_parser import CitationParserPort
from leggie.application.ports.llm import LLMPort, LLMRequest
from leggie.application.ports.router import RouterPort
from leggie.domain.models import Citation, Confidence, Finding, IRAC
from leggie.domain.models.structured_output import (
    CoVeAnswerResponse,
    CoVeCrossCheckResponse,
    CoVeQuestionsResponse,
)

log = logging.getLogger(__name__)

_T = TypeVar("_T", bound=BaseModel)

_ARTICLE_RE = re.compile(r"Άρθρο\s+(\d+)", re.IGNORECASE)
_VERIFY_TASK = "evidence_verification"
_MAX_QUESTIONS = 3


def article_number(text: str) -> str:
    """Extract the article number from free text (e.g. 'Άρθρο 83 ...' → '83')."""
    m = _ARTICLE_RE.search(text or "")
    return m.group(1) if m else ""


@dataclass
class VerificationQuestion:
    """A single verification question and its factored answer."""
    question: str = ""
    citation: Citation | None = None
    verified: bool = False
    answer: str = ""
    evidence: str = ""


@dataclass
class CoVeResult:
    """Result of the Chain-of-Verification for a finding.

    ``finding`` is the (possibly revised) finding to carry forward. ``dropped``
    is True when cross-check found the baseline contradicted by the source.
    """
    finding: Finding
    questions: list[VerificationQuestion] = field(default_factory=list)
    all_verified: bool = False
    verified_count: int = 0
    failed_count: int = 0
    consistency: str = "unknown"
    dropped: bool = False
    reason: str = ""


def _normalize(text: str) -> str:
    """Normalize text for substring matching: strip, lowercase, collapse whitespace."""
    return re.sub(r"\s+", " ", text.strip().lower())


class CoVeVerifier:
    """Chain-of-Verification evidence loop (deterministic + optional LLM)."""

    def __init__(
        self,
        citation_parser: CitationParserPort | None = None,
        llm: LLMPort | None = None,
        router: RouterPort | None = None,
        model: str = "",
        max_questions: int = _MAX_QUESTIONS,
    ) -> None:
        self._citation_parser = citation_parser
        self._llm = llm
        self._router = router
        self._model = model
        self._max_questions = max_questions

    # ── F3 anti-hallucination gate ──────────────────────────────────────
    def validate_quote(self, quote: str, source_text: str) -> bool:
        """Validate that the quote is a real substring of the source.

        Returns True if the normalized quote appears in the normalized source.
        The cheapest anti-hallucination gate: a fabricated verbatim quote fails.
        """
        if not quote or not source_text:
            return False
        return _normalize(quote) in _normalize(source_text)

    # ── Public API ──────────────────────────────────────────────────────
    async def verify(self, finding: Finding, source_text: str = "") -> CoVeResult:
        """Run the Chain-of-Verification on a single finding.

        With an LLM configured, runs the full 4-step factored loop against
        ``source_text``. Without one, falls back to deterministic citation
        resolution (never drops).
        """
        if self._llm is not None:
            return await self._verify_llm(finding, source_text)
        return await self._verify_deterministic(finding)

    async def verify_batch(
        self, findings: list[Finding], article_index: dict[str, str] | None = None
    ) -> list[CoVeResult]:
        """Verify a batch of findings. Each finding is verified independently.

        ``article_index`` maps article-number → source text so factored answers
        can be grounded in the real article the finding is about.
        """
        index = article_index or {}
        results: list[CoVeResult] = []
        for f in findings:
            source = index.get(article_number(f.irac.issue), "")
            results.append(await self.verify(f, source))
        return results

    # ── Deterministic path (no LLM) ─────────────────────────────────────
    async def _verify_deterministic(self, finding: Finding) -> CoVeResult:
        questions = self._plan_citation_questions(finding)
        questions = await self._resolve_citation_questions(questions)
        verified = [q for q in questions if q.verified]
        failed = [q for q in questions if not q.verified]
        return CoVeResult(
            finding=finding,
            questions=questions,
            all_verified=len(failed) == 0,
            verified_count=len(verified),
            failed_count=len(failed),
            consistency="unchecked",
            dropped=False,
        )

    def _plan_citation_questions(self, finding: Finding) -> list[VerificationQuestion]:
        """Deterministic Phase-2: one question per resolvable citation."""
        questions: list[VerificationQuestion] = []
        for evidence in finding.evidence:
            if evidence.citation:
                questions.append(VerificationQuestion(
                    citation=evidence.citation,
                    question=f"Does the citation {evidence.citation.identifier} resolve correctly?",
                ))
            elif evidence.text_excerpt and self._citation_parser:
                for cite in self._citation_parser.parse(evidence.text_excerpt):
                    questions.append(VerificationQuestion(
                        citation=cite,
                        question=f"Does the citation {cite.identifier} resolve correctly?",
                    ))
        return questions

    async def _resolve_citation_questions(
        self, questions: list[VerificationQuestion]
    ) -> list[VerificationQuestion]:
        for q in questions:
            if q.citation is None:
                continue
            if self._citation_parser:
                resolved = await self._citation_parser.resolve(q.citation)
                q.verified = resolved.resolved
                q.evidence = resolved.resolution_evidence or ""
            else:
                q.verified = q.citation.resolved
                q.evidence = q.citation.resolution_evidence or "no parser configured"
        return questions

    # ── LLM CoVe path (4-step factored loop) ────────────────────────────
    async def _verify_llm(self, finding: Finding, source_text: str) -> CoVeResult:
        # F3 gate: a fabricated verbatim quote is an immediate hard fail.
        quote = self._verbatim_quote(finding)
        if source_text and quote and not self.validate_quote(quote, source_text):
            log.info("cove_quote_fail: finding=%s (quote not in source)", finding.id)
            return CoVeResult(
                finding=finding, all_verified=False, failed_count=1,
                consistency="inconsistent", dropped=True,
                reason="Verbatim quote not found in source article (fabricated).",
            )

        # Deterministic citation gate: any citation the parser can positively
        # disprove against a configured index is an immediate hard fail — no
        # need to spend an LLM call on a citation that provably doesn't exist.
        citation_note = ""
        if self._citation_parser is not None:
            disproven, citation_note = await self._check_citations(finding)
            if disproven:
                log.info("cove_citation_fail: finding=%s", finding.id)
                return CoVeResult(
                    finding=finding, all_verified=False, failed_count=1,
                    consistency="inconsistent", dropped=True,
                    reason=f"Citation not found in resolution index: {citation_note}",
                )

        model = await self._select_model()

        try:
            questions = await self._plan_llm_questions(finding, model)
            if not questions:
                # Nothing to check → pass through unchanged.
                return CoVeResult(
                    finding=finding, all_verified=True, consistency="consistent",
                    dropped=False, reason="No verifiable factual claims.",
                )
            answered = await self._answer_factored(questions, source_text, model)
            return await self._cross_check(finding, answered, source_text, model, citation_note)
        except Exception as e:  # noqa: BLE001 — verification must never crash the run
            log.warning("cove_llm_error: finding=%s error=%s", finding.id, str(e)[:200])
            # Fail open: keep the finding, mark unverified.
            return CoVeResult(
                finding=finding, all_verified=False, consistency="unknown",
                dropped=False, reason=f"CoVe error: {str(e)[:120]}",
            )

    async def _check_citations(self, finding: Finding) -> tuple[bool, str]:
        """Structurally parse + resolve citations found in the finding's rule/evidence.

        Returns (disproven, note). ``disproven`` is True only when a citation
        was checked against a *configured* index and explicitly not found —
        never when there's simply no index to check against (that's
        "unverified", not "wrong", and is left for the LLM cross-check).
        """
        assert self._citation_parser is not None
        text = " ".join([finding.irac.rule, *[e.text_excerpt or "" for e in finding.evidence]])
        cites = self._citation_parser.parse(text)
        if not cites:
            return False, ""

        notes: list[str] = []
        for cite in cites:
            resolved = await self._citation_parser.resolve(cite)
            evidence = resolved.resolution_evidence or ""
            if not resolved.resolved and "no resolution index" not in evidence:
                return True, f"{cite.identifier} ({evidence})"
            status = "verified" if resolved.resolved else "unverified against registry"
            notes.append(f"{cite.identifier}: {status}")
        return False, "; ".join(notes)

    async def _plan_llm_questions(
        self, finding: Finding, model: str | None
    ) -> list[VerificationQuestion]:
        """Phase 2 — plan open-ended verification questions from the baseline."""
        system = (
            "Είσαι ελεγκτής νομικών ισχυρισμών. Δίνεται ένα εύρημα (IRAC) για ελληνικό "
            "νομοσχέδιο. Διατύπωσε ΑΝΟΙΧΤΕΣ ερωτήσεις επαλήθευσης που ελέγχουν τους "
            "πραγματικούς/νομικούς ισχυρισμούς του ευρήματος. ΑΠΑΓΟΡΕΥΟΝΤΑΙ ερωτήσεις "
            "ναι/όχι — κάθε ερώτηση πρέπει να απαιτεί πραγματολογική απάντηση. "
            f"Δώσε το πολύ {self._max_questions} ερωτήσεις."
        )
        prompt = (
            f"ΕΥΡΗΜΑ (baseline):\n"
            f"- Ζήτημα: {finding.irac.issue}\n"
            f"- Κανόνας: {finding.irac.rule}\n"
            f"- Εφαρμογή: {finding.irac.application}\n"
            f"- Συμπέρασμα: {finding.irac.conclusion}\n\n"
            f"Διατύπωσε τις ανοιχτές ερωτήσεις επαλήθευσης."
        )
        obj = await self._structured(CoVeQuestionsResponse, prompt, system, model, max_tokens=1024)
        if obj is None:
            return []
        return [
            VerificationQuestion(question=q.strip())
            for q in obj.questions[: self._max_questions]
            if q and q.strip()
        ]

    async def _answer_factored(
        self, questions: list[VerificationQuestion], source_text: str, model: str | None
    ) -> list[VerificationQuestion]:
        """Phase 3 — answer each question independently, WITHOUT the baseline.

        The baseline finding is deliberately absent from context so the model
        cannot echo its own hallucination. Only the source article text is given.
        """
        system = (
            "Απαντάς σε μία ερώτηση επαλήθευσης απομονωμένα. Στηρίξου ΜΟΝΟ στο "
            "κείμενο-πηγή που δίνεται. Αν το κείμενο δεν στηρίζει απάντηση, δήλωσέ το "
            "ρητά και θέσε supported_by_source=false. Μην υποθέτεις."
        )
        src_block = (
            f"ΚΕΙΜΕΝΟ-ΠΗΓΗ (άρθρο):\n{source_text}\n\n"
            if source_text else
            "ΚΕΙΜΕΝΟ-ΠΗΓΗ: (δεν δόθηκε· απάντησε μόνο αν το γνωρίζεις με βεβαιότητα)\n\n"
        )
        for q in questions:
            prompt = f"{src_block}ΕΡΩΤΗΣΗ: {q.question}\n\nΑπάντησε πραγματολογικά."
            obj = await self._structured(
                CoVeAnswerResponse, prompt, system, model, max_tokens=512
            )
            if obj is not None:
                q.answer = obj.answer
                q.verified = obj.supported_by_source
                q.evidence = obj.answer
        return questions

    async def _cross_check(
        self,
        finding: Finding,
        questions: list[VerificationQuestion],
        source_text: str,
        model: str | None,
        citation_note: str = "",
    ) -> CoVeResult:
        """Phase 4 — compare factored answers to the baseline; revise or drop."""
        qa_block = "\n".join(
            f"- Ερώτηση: {q.question}\n  Απάντηση: {q.answer}"
            f"  (τεκμηρίωση από πηγή: {'ναι' if q.verified else 'όχι'})"
            for q in questions
        )
        system = (
            "Συγκρίνεις τους ισχυρισμούς ενός ευρήματος με ανεξάρτητες απαντήσεις "
            "επαλήθευσης. Χαρακτήρισε consistency ως 'consistent', 'inconsistent' ή "
            "'partially_consistent'. Αν το εύρημα διαψεύδεται από την πηγή, θέσε "
            "keep=false. Αν είναι μερικώς σωστό, δώσε διορθωμένο συμπέρασμα στο "
            "revised_conclusion. Ερμηνεία παραπομπών: 'unverified against registry' "
            "σημαίνει ότι δεν υπάρχει διαθέσιμο μητρώο για έλεγχο — ΟΧΙ ότι είναι "
            "λανθασμένη. Απάντησε στα Ελληνικά."
        )
        citation_block = f"\nΠΑΡΑΠΟΜΠΕΣ: {citation_note}\n" if citation_note else ""
        prompt = (
            f"ΕΥΡΗΜΑ (baseline):\n"
            f"- Ζήτημα: {finding.irac.issue}\n"
            f"- Κανόνας: {finding.irac.rule}\n"
            f"- Συμπέρασμα: {finding.irac.conclusion}\n"
            f"{citation_block}\n"
            f"ΑΠΑΝΤΗΣΕΙΣ ΕΠΑΛΗΘΕΥΣΗΣ:\n{qa_block}\n\n"
            f"Διασταύρωσε και αποφάσισε."
        )
        obj = await self._structured(
            CoVeCrossCheckResponse, prompt, system, model, max_tokens=1024
        )
        verified_count = sum(1 for q in questions if q.verified)
        failed_count = len(questions) - verified_count

        if obj is None:
            return CoVeResult(
                finding=finding, questions=questions,
                all_verified=failed_count == 0, verified_count=verified_count,
                failed_count=failed_count, consistency="unknown", dropped=False,
                reason="Cross-check produced no verdict.",
            )

        consistency = (obj.consistency or "").strip().lower()
        dropped = (not obj.keep) or consistency == "inconsistent"
        revised = self._apply_revision(finding, obj) if not dropped else finding

        return CoVeResult(
            finding=revised,
            questions=questions,
            all_verified=(consistency == "consistent" and failed_count == 0),
            verified_count=verified_count,
            failed_count=failed_count,
            consistency=consistency or "unknown",
            dropped=dropped,
            reason=obj.reason,
        )

    def _apply_revision(
        self, finding: Finding, verdict: CoVeCrossCheckResponse
    ) -> Finding:
        """Build a revised finding: corrected conclusion + adjusted confidence."""
        adjustment = verdict.confidence_adjustment
        new_conclusion = (verdict.revised_conclusion or "").strip()
        if adjustment == 0.0 and not new_conclusion:
            return finding

        irac = finding.irac
        if new_conclusion:
            irac = IRAC(
                issue=irac.issue, rule=irac.rule,
                application=irac.application, conclusion=new_conclusion,
            )
        confidence = finding.confidence
        if adjustment != 0.0:
            new_score = min(1.0, max(0.0, finding.confidence.score + adjustment))
            confidence = Confidence.from_score(new_score, provenance="cove-verified")

        return finding.model_copy(update={
            "irac": irac,
            "confidence": confidence,
            "version": finding.version + 1,
        })

    # ── Helpers ─────────────────────────────────────────────────────────
    @staticmethod
    def _verbatim_quote(finding: Finding) -> str:
        for ev in finding.evidence:
            if ev.text_excerpt:
                return ev.text_excerpt
        return ""

    async def _select_model(self) -> str | None:
        """Pick the verification model via the router's evidence route, else default."""
        if self._router is not None:
            try:
                route = await self._router.route(_VERIFY_TASK)
                return route.model
            except Exception:  # noqa: BLE001
                log.warning("cove_route_failed: using default model")
        return self._model or None

    async def _structured(
        self, schema: type[_T], prompt: str, system: str, model: str | None, max_tokens: int
    ) -> _T | None:
        """One structured LLM call with low temperature for deterministic checks."""
        if self._llm is None:
            return None
        request = LLMRequest(
            prompt=prompt,
            system_prompt=system,
            model=model,
            max_tokens=max_tokens,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        obj, _ = await self._llm.generate_structured(request, schema)
        return obj if isinstance(obj, schema) else None
