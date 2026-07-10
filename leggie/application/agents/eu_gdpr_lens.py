"""EU & GDPR Lens — analyzes compliance with EU law and GDPR requirements.

W5: Uses LLM for real analysis with regex-based fallback when LLM is unavailable.
"""

from __future__ import annotations

import hashlib
import logging
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

log = logging.getLogger(__name__)


class EUGDPRLens(Lens):
    """EU & GDPR lens — analyzes compliance with EU law."""

    def name(self) -> str:
        return "eu_gdpr"

    def description(self) -> str:
        return "Evaluates compliance with EU law and GDPR requirements"

    async def analyze(self, article: Article) -> list[Finding]:
        if not self._llm:
            log.info("lens_no_llm: eu_gdpr — using regex fallback")
            return self._analyze_regex(article)
        try:
            return await self._analyze_llm(article)
        except Exception as exc:
            log.error("lens_degraded: eu_gdpr article=%s error=%s", article.id, exc)
            self._emit_degradation(article, exc)
            return []

    async def _analyze_llm(self, article: Article) -> list[Finding]:
        if self._use_verbalized_sampling:
            vs_result = await self._analyze_with_vs("eu_gdpr", article)
            if vs_result:
                return vs_result

        system, template = self._prompt_for("eu_gdpr")
        prompt = template.format(article_id=article.id, article_text=article.raw_text)
        result = await self._call_llm_structured(LensFindings, prompt, system)
        if result is None or not result.findings:
            return []
        return [self._candidate_to_finding(c, article) for c in result.findings]

    def _candidate_to_finding(self, c: IRACCandidate, article: Article) -> Finding:
        prompt_hash = hashlib.sha256(f"eu_gdpr:{article.id}:{c.issue}".encode()).hexdigest()[:12]
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
            finding_type=FindingType.EU_COMPLIANCE,
            irac=IRAC(issue=c.issue, rule=c.rule, application=c.application, conclusion=c.conclusion),
            severity=sev_map.get(c.severity, Severity.MEDIUM),
            confidence=Confidence.from_score(c.probability, provenance="llm-eu-gdpr"),
            lens=self.name(), model=self._model, prompt_hash=prompt_hash,
            evidence=evidence_list,
        )

    # ── Regex fallback ──────────────────────────────────────────────

    def _analyze_regex(self, article: Article) -> list[Finding]:
        findings: list[Finding] = []
        text = f"{article.id}. {article.title}\n{article.raw_text}"

        for pattern in _GDPR_PATTERNS:
            match = pattern.search(text)
            if match:
                findings.append(Finding(
                    finding_type=FindingType.EU_COMPLIANCE,
                    irac=IRAC(
                        issue=f"Άρθρο {article.id}: Επεξεργασία προσωπικών δεδομένων",
                        rule="Ο ΓΚΠΔ (Κανονισμός 2016/679) απαιτεί νομική βάση για κάθε επεξεργασία",
                        application=f"Το Άρθρο {article.id} αφορά επεξεργασία δεδομένων που εμπίπτει στον ΓΚΠΔ",
                        conclusion=f"Το Άρθρο {article.id} πρέπει να ελέγχεται για συμβατότητα με ΓΚΠΔ",
                    ),
                    severity=Severity.MEDIUM,
                    confidence=Confidence.from_score(0.55, provenance="pattern-match"),
                    lens=self.name(), model="rule-based-phase2",
                    evidence=[Evidence(text_excerpt=match.group(0), verdict="supports")],
                ))

        for pattern in _CROSS_BORDER_PATTERNS:
            match = pattern.search(text)
            if match:
                findings.append(Finding(
                    finding_type=FindingType.EU_COMPLIANCE,
                    irac=IRAC(
                        issue=f"Άρθρο {article.id}: Διαβίβαση δεδομένων εκτός ΕΕ",
                        rule="Η διαβίβαση προσωπικών δεδομένων εκτός ΕΕ απαιτεί επαρκείς διασφαλίσεις",
                        application=f"Το Άρθρο {article.id} μπορεί να επιτρέπει διαβίβαση εκτός ΕΕ",
                        conclusion=f"Το Άρθρο {article.id} χρήζει ελέγχου διαβίβασης δεδομένων εκτός ΕΕ",
                    ),
                    severity=Severity.HIGH,
                    confidence=Confidence.from_score(0.5, provenance="pattern-match"),
                    lens=self.name(), model="rule-based-phase2",
                    evidence=[Evidence(text_excerpt=match.group(0), verdict="supports")],
                ))

        return findings


# ── Regex patterns (fallback) ───────────────────────────────────────

_GDPR_PATTERNS = [
    re.compile(r"(?:προσωπικά\s+δεδομένα|δεδομέν[αων]\s+προσωπικού\s+χαρακτήρα|ΓΚΠΔ|GDPR)", re.UNICODE | re.IGNORECASE),
    re.compile(r"(?:επεξεργασί[αας]|υποκείμεν[οου]\s+των\s+δεδομένων|συγκατάθεση)", re.UNICODE | re.IGNORECASE),
]

_CROSS_BORDER_PATTERNS = [
    re.compile(r"(?:διαβίβαση|διασυνοριακ[ήή]|τρίτη\s+χώρα|εκτός\s+του\s+ΕΟΧ)", re.UNICODE | re.IGNORECASE),
]
