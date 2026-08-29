"""Constitutional Lens — analyzes articles for constitutional compatibility.

F1: Uses LLM for analysis with regex-based fallback when LLM is unavailable.
FX5: Distinguishes "no LLM configured" (legitimate regex mode) from
      "LLM call failed" (degradation — surfaced, not silently masked).
"""

from __future__ import annotations

import hashlib
import logging
import re
from re import Pattern
from typing import Any

from leggie.application.agents.lens import Lens
from leggie.application.ports.llm import LLMPort
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


class ConstitutionalLens(Lens):
    """Constitutional lens — uses LLM when available, regex fallback otherwise."""

    def __init__(
        self, llm: LLMPort | None = None, model: str = "google/gemini-2.5-flash", **kwargs: Any
    ) -> None:
        super().__init__(llm=llm, model=model, **kwargs)

    def name(self) -> str:
        return "constitutional"

    def description(self) -> str:
        return "Analyzes articles for compatibility with the Greek Constitution"

    async def analyze(self, article: Article) -> list[Finding]:
        if not self._llm:
            # Legitimate: no LLM configured → pure regex mode
            log.info("lens_no_llm: constitutional — using regex fallback (no LLM configured)")
            return self._analyze_regex(article)

        # LLM is configured — attempt real analysis
        try:
            return await self._analyze_llm(article)
        except Exception as exc:
            # LLM call failed — this is a degradation, not silent fallback
            log.error("lens_degraded: constitutional article=%s error=%s", article.id, exc)
            self._emit_degradation(article, exc)
            # Return empty — no findings from this lens. The pipeline sees
            # the degradation event and the missing LLM findings.
            return []

    async def _analyze_llm(self, article: Article) -> list[Finding]:
        if self._use_verbalized_sampling:
            vs_result = await self._analyze_with_vs("constitutional", article)
            if vs_result:
                return vs_result

        system, template = self._prompt_for("constitutional")
        prompt = template.format(article_id=article.id, article_text=article.raw_text)
        result = await self._call_llm_structured(LensFindings, prompt, system)
        if result is None or not result.findings:
            return []
        # F2 noise suppression: a genuine finding must cite the text it's
        # based on. A candidate with no verbatim quote has zero evidence by
        # definition — that's the exact shape of the "preliminary check,
        # nothing found" filler the model pads empty results with. Drop it
        # here rather than trust the prompt alone to suppress it.
        substantive = [c for c in result.findings if c.verbatim_quote and c.verbatim_quote.strip()]
        return [self._candidate_to_finding(c, article) for c in substantive]

    def _candidate_to_finding(self, c: IRACCandidate, article: Article) -> Finding:
        prompt_hash = hashlib.sha256(f"constitutional:{article.id}:{c.issue}".encode()).hexdigest()[
            :12
        ]
        sev_map = {s.value: s for s in Severity}
        # Validate verbatim quote (F3)
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
            finding_type=FindingType.CONSTITUTIONAL,
            irac=IRAC(
                issue=c.issue, rule=c.rule, application=c.application, conclusion=c.conclusion
            ),
            severity=sev_map.get(c.severity, Severity.MEDIUM),
            confidence=Confidence.from_score(c.probability, provenance="llm-constitutional"),
            lens=self.name(),
            model=self._model,
            prompt_hash=prompt_hash,
            evidence=evidence_list,
        )

    def _analyze_regex(self, article: Article) -> list[Finding]:
        findings: list[Finding] = []
        text = f"{article.id}. {article.title}\n{article.raw_text}"
        for pattern in _DELEGATION_PATTERNS:
            m = pattern.search(text)
            if m:
                findings.append(
                    Finding(
                        finding_type=FindingType.CONSTITUTIONAL,
                        irac=IRAC(
                            issue=f"Άρθρο {article.id}: Πιθανή υπέρβαση ορίων νομοθετικής εξουσιοδότησης",
                            rule="Το Άρθρο 43 του Συντάγματος ορίζει τα όρια της νομοθετικής εξουσιοδότησης",
                            application=f"Το Άρθρο {article.id} περιέχει τη φράση '{m.group(0)}'",
                            conclusion=f"Το Άρθρο {article.id} πιθανόν να υπερβαίνει τα όρια του Άρθρου 43",
                        ),
                        severity=Severity.HIGH,
                        confidence=Confidence.from_score(0.6, provenance="regex-fallback"),
                        lens=self.name(),
                        model="regex-fallback",
                        evidence=[Evidence(text_excerpt=m.group(0), verdict="supports")],
                    )
                )
        for pattern in _RETROACTIVE_PATTERNS:
            m = pattern.search(text)
            if m:
                findings.append(
                    Finding(
                        finding_type=FindingType.CONSTITUTIONAL,
                        irac=IRAC(
                            issue=f"Άρθρο {article.id}: Πιθανή αναδρομική ισχύς",
                            rule="Η αναδρομική ισχύς επιτρέπεται μόνο κατ' εξαίρεση (Άρθρο 77 Συντάγματος)",
                            application=f"Το Άρθρο {article.id} περιέχει αναφορά σε αναδρομική εφαρμογή",
                            conclusion=f"Η αναδρομική ισχύς στο Άρθρο {article.id} χρήζει εξέτασης",
                        ),
                        severity=Severity.MEDIUM,
                        confidence=Confidence.from_score(0.5, provenance="regex-fallback"),
                        lens=self.name(),
                        model="regex-fallback",
                        evidence=[Evidence(text_excerpt=m.group(0), verdict="supports")],
                    )
                )
        for pattern in _RIGHTS_PATTERNS:
            m = pattern.search(text)
            if m:
                findings.append(
                    Finding(
                        finding_type=FindingType.CONSTITUTIONAL,
                        irac=IRAC(
                            issue=f"Άρθρο {article.id}: Πιθανή επιρροή σε θεμελιώδη δικαιώματα",
                            rule="Τα θεμελιώδη δικαιώματα προστατεύονται από τα Άρθρα 5-25 του Συντάγματος",
                            application=f"Το Άρθρο {article.id} περιέχει ρύθμιση που επηρεάζει δικαιώματα",
                            conclusion=f"Το Άρθρο {article.id} χρήζει συνταγματικού ελέγχου",
                        ),
                        severity=Severity.HIGH,
                        confidence=Confidence.from_score(0.55, provenance="regex-fallback"),
                        lens=self.name(),
                        model="regex-fallback",
                        evidence=[Evidence(text_excerpt=m.group(0), verdict="supports")],
                    )
                )
        return findings


_DELEGATION_PATTERNS: list[Pattern[str]] = [
    re.compile(
        r"(?:εξουσιοδότηση|εξουσιοδοτεί|νομοθετική\s+εξουσιοδότηση)", re.UNICODE | re.IGNORECASE
    ),
    re.compile(
        r"(?:έκδοση\s+π\.δ\.|προεδρικό\s+διάταγμα|υπουργική\s+απόφαση)", re.UNICODE | re.IGNORECASE
    ),
]
_RETROACTIVE_PATTERNS: list[Pattern[str]] = [
    re.compile(
        r"(?:αναδρομική\s+ισχύ|αναδρομικά|από\s+την\s+έναρξη\s+ισχύος)", re.UNICODE | re.IGNORECASE
    ),
]
_RIGHTS_PATTERNS: list[Pattern[str]] = [
    re.compile(
        r"(?:περιορισμός\s+(?:θεμελιώδους|δικαιώματος)|προσβολή|παραβίαση\s+δικαιώματος)",
        re.UNICODE | re.IGNORECASE,
    ),
    re.compile(
        r"(?:προσωπικά\s+δεδομένα|απόρρητο|ιδιωτικότητα|ελευθερία\s+της\s+έκφρασης)",
        re.UNICODE | re.IGNORECASE,
    ),
]
