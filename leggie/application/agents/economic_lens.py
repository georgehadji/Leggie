"""Economic Lens — analyzes fiscal and economic impact of bill provisions.

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


class EconomicLens(Lens):
    """Economic lens — analyzes fiscal and economic impact."""

    def name(self) -> str:
        return "economic"

    def description(self) -> str:
        return "Evaluates fiscal impact, cost burden, and economic proportionality"

    async def analyze(self, article: Article) -> list[Finding]:
        if not self._llm:
            log.info("lens_no_llm: economic — using regex fallback (no LLM configured)")
            return self._analyze_regex(article)
        try:
            return await self._analyze_llm(article)
        except Exception as exc:
            log.error("lens_degraded: economic article=%s error=%s", article.id, exc)
            self._emit_degradation(article, exc)
            return []

    async def _analyze_llm(self, article: Article) -> list[Finding]:
        if self._use_verbalized_sampling:
            vs_result = await self._analyze_with_vs("economic", article)
            if vs_result:
                return vs_result

        system, template = self._prompt_for("economic")
        prompt = template.format(article_id=article.id, article_text=article.raw_text)
        result = await self._call_llm_structured(LensFindings, prompt, system)
        if result is None or not result.findings:
            return []
        return [self._candidate_to_finding(c, article) for c in result.findings]

    def _candidate_to_finding(self, c: IRACCandidate, article: Article) -> Finding:
        prompt_hash = hashlib.sha256(f"economic:{article.id}:{c.issue}".encode()).hexdigest()[:12]
        sev_map = {s.value: s for s in Severity}
        valid_quote = False
        evidence_list = []
        if c.verbatim_quote:
            from leggie.application.services.cove_verifier import _normalize

            valid_quote = _normalize(c.verbatim_quote) in _normalize(article.raw_text)
            evidence_list = [
                Evidence(text_excerpt=c.verbatim_quote, verdict="supports", citation=None)
            ]
        if not valid_quote and c.verbatim_quote:
            evidence_list = [
                Evidence(
                    text_excerpt=c.verbatim_quote,
                    verdict="neutral",
                    source_document="quote-not-verified-as-substring",
                )
            ]
        return Finding(
            finding_type=FindingType.ECONOMIC,
            irac=IRAC(
                issue=c.issue, rule=c.rule, application=c.application, conclusion=c.conclusion
            ),
            severity=sev_map.get(c.severity, Severity.MEDIUM),
            confidence=Confidence.from_score(c.probability, provenance="llm-economic"),
            lens=self.name(),
            model=self._model,
            prompt_hash=prompt_hash,
            evidence=evidence_list,
        )

    # ── Regex fallback ──────────────────────────────────────────────

    def _analyze_regex(self, article: Article) -> list[Finding]:
        findings: list[Finding] = []
        text = f"{article.id}. {article.title}\n{article.raw_text}"

        for pattern in _COST_PATTERNS:
            match = pattern.search(text)
            if match:
                findings.append(
                    Finding(
                        finding_type=FindingType.ECONOMIC,
                        irac=IRAC(
                            issue=f"Άρθρο {article.id}: Οικονομική επιβάρυνση",
                            rule="Κάθε νομοσχέδιο πρέπει να συνοδεύεται από εκτίμηση δημοσιονομικών επιπτώσεων",
                            application=f"Το Άρθρο {article.id} αναφέρεται σε δαπάνη/κόστος χωρίς ποσοτική ανάλυση",
                            conclusion=f"Απαιτείται ποσοτική εκτίμηση δημοσιονομικών επιπτώσεων για το Άρθρο {article.id}",
                        ),
                        severity=Severity.MEDIUM,
                        confidence=Confidence.from_score(0.55, provenance="pattern-match"),
                        lens=self.name(),
                        model="rule-based-phase2",
                        evidence=[Evidence(text_excerpt=match.group(0), verdict="supports")],
                    )
                )

        return findings


# ── Regex patterns (fallback) ───────────────────────────────────────

_COST_PATTERNS = [
    re.compile(
        r"(?:δαπάνη|κόστος|επιβάρυνση|χρηματοδότηση|προϋπολογισμός)", re.UNICODE | re.IGNORECASE
    ),
    re.compile(r"(?:τέλο[ςσ]|εισφορά|πρόστιμο|κύρωση|ποινή)", re.UNICODE | re.IGNORECASE),
]

# Additional regex patterns for fallback coverage
_UNFUNDED_PATTERNS = [
    re.compile(
        r"(?:χωρίς\s+πρόβλεψη|δεν\s+προβλέπεται\s+δαπάνη|από\s+τον\s+κρατικό\s+προϋπολογισμό)",
        re.UNICODE | re.IGNORECASE,
    ),
    re.compile(
        r"(?:καλύπτεται\s+από|βαρύνει\s+τον\s+κρατικό|επιβαρύνει\s+τον\s+προϋπολογισμό)",
        re.UNICODE | re.IGNORECASE,
    ),
]

_DISPROPORTIONATE_PATTERNS = [
    re.compile(
        r"(?:πρόστιμο\s+έω[ςσ]\s+\d|ποινή\s+φυλάκισης|δυσανάλογη)", re.UNICODE | re.IGNORECASE
    ),
    re.compile(r"(?:κατάσχεσ[ηη]|αναστολή\s+λειτουργίας)", re.UNICODE | re.IGNORECASE),
]
