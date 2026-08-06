"""Legal-coherence Lens — analyzes articles for internal consistency and legal coherence.

W5: Uses LLM for real analysis with regex-based fallback when LLM is unavailable.
"""

from __future__ import annotations

import hashlib
import re

from leggie.application.agents.lens import Lens
from leggie.domain.models import (
    IRAC,
    Article,
    Confidence,
    Evidence,
    Finding,
    FindingType,
    Severity,
)
from leggie.domain.models.structured_output import IRACCandidate, LensFindings
from leggie.observability import get_logger

log = get_logger(__name__)


class LegalCoherenceLens(Lens):
    """Legal-coherence lens — checks for internal consistency and clarity."""

    def name(self) -> str:
        return "legal_coherence"

    def description(self) -> str:
        return "Evaluates internal consistency, clarity, and defined terminology"

    async def analyze(self, article: Article) -> list[Finding]:
        if not self._llm:
            log.info("lens_no_llm: legal_coherence — using regex fallback")
            return self._analyze_regex(article)
        try:
            return await self._analyze_llm(article)
        except Exception as exc:
            log.error("lens_degraded: legal_coherence article=%s error=%s", article.id, exc)
            self._emit_degradation(article, exc)
            return []

    async def _analyze_llm(self, article: Article) -> list[Finding]:
        if self._use_verbalized_sampling:
            vs_result = await self._analyze_with_vs("legal_coherence", article)
            if vs_result:
                return vs_result

        system, template = self._prompt_for("legal_coherence")
        prompt = template.format(article_id=article.id, article_text=article.raw_text)
        result = await self._call_llm_structured(LensFindings, prompt, system)
        if result is None or not result.findings:
            return []
        return [self._candidate_to_finding(c, article) for c in result.findings]

    def _candidate_to_finding(self, c: IRACCandidate, article: Article) -> Finding:
        prompt_hash = hashlib.sha256(f"legal_coherence:{article.id}:{c.issue}".encode()).hexdigest()[:12]
        sev_map = {s.value: s for s in Severity}
        valid_quote = False
        evidence_list = []
        if c.verbatim_quote:
            from leggie.application.services.cove_verifier import _normalize
            valid_quote = _normalize(c.verbatim_quote) in _normalize(article.raw_text)
            evidence_list = [Evidence(text_excerpt=c.verbatim_quote, verdict="supports", citation=None)]
        if not valid_quote and c.verbatim_quote:
            evidence_list = [Evidence(text_excerpt=c.verbatim_quote, verdict="neutral",
                                      source_document="quote-not-verified-as-substring")]
        return Finding(
            finding_type=FindingType.FACTUAL,
            irac=IRAC(issue=c.issue, rule=c.rule, application=c.application, conclusion=c.conclusion),
            severity=sev_map.get(c.severity, Severity.MEDIUM),
            confidence=Confidence.from_score(c.probability, provenance="llm-legal-coherence"),
            lens=self.name(), model=self._model, prompt_hash=prompt_hash,
            evidence=evidence_list,
        )

    # ── Regex fallback ──────────────────────────────────────────────

    def _analyze_regex(self, article: Article) -> list[Finding]:
        findings: list[Finding] = []
        text = f"{article.id}. {article.title}\n{article.raw_text}"

        for pattern in _VAGUE_PATTERNS:
            match = pattern.search(text)
            if match:
                findings.append(Finding(
                    finding_type=FindingType.FACTUAL,
                    irac=IRAC(
                        issue=f"Άρθρο {article.id}: Ασαφής ή αόριστη διατύπωση",
                        rule="Η νομοθεσία πρέπει να είναι σαφής και ορισμένη (αρχή της ασφάλειας δικαίου)",
                        application=f"Το Άρθρο {article.id} χρησιμοποιεί τη φράση '{match.group(0)}' που είναι ασαφής",
                        conclusion=f"Το Άρθρο {article.id} χρήζει σαφέστερης διατύπωσης",
                    ),
                    severity=Severity.MEDIUM,
                    confidence=Confidence.from_score(0.55, provenance="pattern-match"),
                    lens=self.name(), model="rule-based-phase2",
                    evidence=[Evidence(text_excerpt=match.group(0), verdict="supports")],
                ))

        for pattern in _CONTRADICTION_PATTERNS:
            match = pattern.search(text)
            if match:
                findings.append(Finding(
                    finding_type=FindingType.FACTUAL,
                    irac=IRAC(
                        issue=f"Άρθρο {article.id}: Πιθανή εσωτερική αντίφαση",
                        rule="Οι διατάξεις του ίδιου νόμου πρέπει να είναι συνεπείς μεταξύ τους",
                        application=f"Το Άρθρο {article.id} περιέχει τη φράση '{match.group(0)}'",
                        conclusion=f"Το Άρθρο {article.id} χρήζει ελέγχου συνέπειας",
                    ),
                    severity=Severity.LOW,
                    confidence=Confidence.from_score(0.4, provenance="pattern-match"),
                    lens=self.name(), model="rule-based-phase2",
                    evidence=[Evidence(text_excerpt=match.group(0), verdict="supports")],
                ))

        return findings


# ── Regex patterns (fallback) ───────────────────────────────────────

_VAGUE_PATTERNS = [
    re.compile(r"(?:κατάλληλ[οςηο]|ενδεδειγμέν[οςηο]|σχετικ[όςήό])", re.UNICODE | re.IGNORECASE),
    re.compile(r"(?:με\s+απόφαση\s+του|όπως\s+ορίζεται|εφόσον\s+προβλέπεται)", re.UNICODE | re.IGNORECASE),
    re.compile(r"(?:εξαιρετικές\s+περιπτώσεις|ειδικές\s+συνθήκες|κατά\s+περίπτωση)", re.UNICODE | re.IGNORECASE),
]

_CONTRADICTION_PATTERNS = [
    re.compile(r"(?:κατά\s+παρέκκλιση|αντίθετα|ωστόσο|πλην\s+όμως)", re.UNICODE | re.IGNORECASE),
    re.compile(r"(?:δεν\s+εφαρμόζεται|εξαιρείται|παρεκκλίνει)", re.UNICODE | re.IGNORECASE),
]
