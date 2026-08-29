"""Implementation Lens — analyzes practical implementation feasibility.

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


class ImplementationLens(Lens):
    """Implementation lens — analyzes practical feasibility."""

    def name(self) -> str:
        return "implementation"

    def description(self) -> str:
        return "Evaluates implementation feasibility, deadlines, and transitional provisions"

    async def analyze(self, article: Article) -> list[Finding]:
        if not self._llm:
            log.info("lens_no_llm: implementation — using regex fallback")
            return self._analyze_regex(article)
        try:
            return await self._analyze_llm(article)
        except Exception as exc:
            log.error("lens_degraded: implementation article=%s error=%s", article.id, exc)
            self._emit_degradation(article, exc)
            return []

    async def _analyze_llm(self, article: Article) -> list[Finding]:
        if self._use_verbalized_sampling:
            vs_result = await self._analyze_with_vs("implementation", article)
            if vs_result:
                return vs_result

        system, template = self._prompt_for("implementation")
        prompt = template.format(article_id=article.id, article_text=article.raw_text)
        result = await self._call_llm_structured(LensFindings, prompt, system)
        if result is None or not result.findings:
            return []
        return [self._candidate_to_finding(c, article) for c in result.findings]

    def _candidate_to_finding(self, c: IRACCandidate, article: Article) -> Finding:
        prompt_hash = hashlib.sha256(f"implementation:{article.id}:{c.issue}".encode()).hexdigest()[
            :12
        ]
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
            finding_type=FindingType.IMPLEMENTATION,
            article_id=article.id,
            irac=IRAC(
                issue=c.issue, rule=c.rule, application=c.application, conclusion=c.conclusion
            ),
            severity=sev_map.get(c.severity, Severity.MEDIUM),
            confidence=Confidence.from_score(c.probability, provenance="llm-implementation"),
            lens=self.name(),
            model=self._model,
            prompt_hash=prompt_hash,
            evidence=evidence_list,
        )

    # ── Regex fallback ──────────────────────────────────────────────

    def _analyze_regex(self, article: Article) -> list[Finding]:
        findings: list[Finding] = []
        text = f"{article.id}. {article.title}\n{article.raw_text}"

        for pattern in _DEADLINE_PATTERNS:
            match = pattern.search(text)
            if match:
                findings.append(
                    Finding(
                        finding_type=FindingType.IMPLEMENTATION,
                        article_id=article.id,
                        irac=IRAC(
                            issue=f"Άρθρο {article.id}: Πιθανή μη ρεαλιστική προθεσμία",
                            rule="Οι προθεσμίες εφαρμογής πρέπει να είναι εύλογες και ρεαλιστικές",
                            application=f"Το Άρθρο {article.id} ορίζει προθεσμία/έναρξη ισχύος",
                            conclusion=f"Το Άρθρο {article.id} χρήζει ανάλυσης επάρκειας προθεσμιών",
                        ),
                        severity=Severity.MEDIUM,
                        confidence=Confidence.from_score(0.55, provenance="pattern-match"),
                        lens=self.name(),
                        model="rule-based-phase2",
                        evidence=[Evidence(text_excerpt=match.group(0), verdict="supports")],
                    )
                )

        for pattern in _TRANSITION_PATTERNS:
            match = pattern.search(text)
            if match:
                findings.append(
                    Finding(
                        finding_type=FindingType.PROCEDURAL,
                        article_id=article.id,
                        irac=IRAC(
                            issue=f"Άρθρο {article.id}: Μεταβατικές ρυθμίσεις",
                            rule="Οι μεταβατικές διατάξεις πρέπει να διασφαλίζουν ομαλή μετάβαση",
                            application=f"Το Άρθρο {article.id} περιέχει μεταβατικές ρυθμίσεις",
                            conclusion=f"Οι μεταβατικές διατάξεις του Άρθρου {article.id} χρήζουν εξέτασης",
                        ),
                        severity=Severity.LOW,
                        confidence=Confidence.from_score(0.5, provenance="pattern-match"),
                        lens=self.name(),
                        model="rule-based-phase2",
                        evidence=[Evidence(text_excerpt=match.group(0), verdict="supports")],
                    )
                )

        return findings


# ── Regex patterns (fallback) ───────────────────────────────────────

_DEADLINE_PATTERNS = [
    re.compile(
        r"(?:εντός\s+\d+\s*ημέρ(?:ας|ών)|άμεση\s+ισχύ|από\s+την\s+έναρξη|μεταβατική\s+περίοδος)",
        re.UNICODE | re.IGNORECASE,
    ),
    re.compile(
        r"(?:έναρξη\s+ισχύο[ςσ]\s+από|εφαρμόζεται\s+από|ισχύει\s+από)", re.UNICODE | re.IGNORECASE
    ),
]

_TRANSITION_PATTERNS = [
    re.compile(
        r"(?:μεταβατικ(?:έ[ςσ]|ή|ό)|υφιστάμεν(?:ο[ςισ]|η)|εκκρεμείς)", re.UNICODE | re.IGNORECASE
    ),
    re.compile(
        r"(?:εξακολουθεί\s+να\s+ισχύει|καταργούμενε[ςσ]|προγενέστερε[ςσ])",
        re.UNICODE | re.IGNORECASE,
    ),
]
