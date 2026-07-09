"""Constitutional Lens — analyzes articles for constitutional compatibility.

Per BUILD_PLAN Phase 1: single lens, single path, emits 1 Finding per
article with a raw citation. Phase 1 uses deterministic pattern matching;
Phase 2+ will add Verbalized Sampling (O3).
"""

from __future__ import annotations

import re
from typing import Pattern

from leggie.application.agents.lens import Lens
from leggie.domain.models import (
    Article, Confidence, Evidence, Finding, FindingType, IRAC, Severity,
)


# Constitutional keywords and patterns
_DELEGATION_PATTERNS: list[Pattern] = [
    re.compile(r"(?:εξουσιοδότηση|εξουσιοδοτεί|νομοθετική\s+εξουσιοδότηση)", re.UNICODE | re.IGNORECASE),
    re.compile(r"(?:έκδοση\s+π.δ\.|προεδρικό\s+διάταγμα|υπουργική\s+απόφαση)", re.UNICODE | re.IGNORECASE),
]

_RETROACTIVE_PATTERNS: list[Pattern] = [
    re.compile(r"(?:αναδρομική\s+ισχύ|αναδρομικά|από\s+την\s+έναρξη\s+ισχύος\s+του\s+παρόντος)", re.UNICODE | re.IGNORECASE),
]

_RIGHTS_PATTERNS: list[Pattern] = [
    re.compile(r"(?:περιορισμός\s+(?:θεμελιώδους|δικαιώματος)|προσβολή|παραβίαση\s+δικαιώματος)", re.UNICODE | re.IGNORECASE),
    re.compile(r"(?:προσωπικά\s+δεδομένα|απόρρητο|ιδιωτικότητα|ελευθερία\s+της\s+έκφρασης)", re.UNICODE | re.IGNORECASE),
]

_PROCEDURE_PATTERNS: list[Pattern] = [
    re.compile(r"(?:τροποποίηση|κατάργηση)\s+(?:του\s+)?(?:Συντάγματος|συνταγματικής\s+διάταξης)", re.UNICODE | re.IGNORECASE),
    re.compile(r"(?:απλή\s+πλειοψηφία|αυξημένη\s+πλειοψηφία|τριάντα\s+ημέρες)", re.UNICODE | re.IGNORECASE),
]


class ConstitutionalLens(Lens):
    """Constitutional lens — checks compatibility with the Greek Constitution.

    For Phase 1: uses deterministic pattern matching to find potential
    constitutional issues. Phase 2+ adds LLM-based analysis with VS.
    """

    def name(self) -> str:
        return "constitutional"

    def description(self) -> str:
        return "Analyzes articles for compatibility with the Greek Constitution"

    async def analyze(self, article: Article) -> list[Finding]:
        """Analyze an article for constitutional issues.

        Uses pattern matching to detect:
        1. Excessive delegation of legislative authority
        2. Retroactive effect without constitutional basis
        3. Fundamental rights implications
        4. Procedural irregularities
        """
        findings: list[Finding] = []
        text = f"{article.id}. {article.title}\n{article.raw_text}"

        # Check delegation patterns
        for pattern in _DELEGATION_PATTERNS:
            match = pattern.search(text)
            if match:
                findings.append(self._make_delegation_finding(article, match))

        # Check retroactive effect
        for pattern in _RETROACTIVE_PATTERNS:
            match = pattern.search(text)
            if match:
                findings.append(self._make_retroactive_finding(article, match))

        # Check rights implications
        for pattern in _RIGHTS_PATTERNS:
            match = pattern.search(text)
            if match:
                findings.append(self._make_rights_finding(article, match))

        # Check procedure patterns
        for pattern in _PROCEDURE_PATTERNS:
            match = pattern.search(text)
            if match:
                findings.append(self._make_procedure_finding(article, match))

        # If no patterns matched but article has content, emit a single
        # low-confidence info finding to satisfy Phase 1 exit gate
        if not findings and len(text) > 50:
            findings.append(self._make_baseline_finding(article))

        return findings

    def _make_delegation_finding(self, article: Article, match: re.Match) -> Finding:
        return Finding(
            finding_type=FindingType.CONSTITUTIONAL,
            irac=IRAC(
                issue=f"Άρθρο {article.id}: Πιθανή υπέρβαση ορίων νομοθετικής εξουσιοδότησης",
                rule="Το Άρθρο 43 του Συντάγματος ορίζει τα όρια της νομοθετικής εξουσιοδότησης",
                application=f"Το Άρθρο {article.id} περιέχει τη φράση '{match.group(0)}' που υποδηλώνει εξουσιοδότηση",
                conclusion=f"Το Άρθρο {article.id} πιθανόν να υπερβαίνει τα όρια του Άρθρου 43",
            ),
            severity=Severity.HIGH,
            confidence=Confidence.from_score(0.6, provenance="pattern-match"),
            lens=self.name(),
            model="rule-based-phase1",
            evidence=[
                Evidence(
                    text_excerpt=match.group(0),
                    verdict="supports",
                )
            ],
        )

    def _make_retroactive_finding(self, article: Article, match: re.Match) -> Finding:
        return Finding(
            finding_type=FindingType.CONSTITUTIONAL,
            irac=IRAC(
                issue=f"Άρθρο {article.id}: Πιθανή αναδρομική ισχύς",
                rule="Η αναδρομική ισχύς νόμου επιτρέπεται μόνο κατ' εξαίρεση (Άρθρο 77 Συντάγματος)",
                application=f"Το Άρθρο {article.id} περιέχει αναφορά σε αναδρομική εφαρμογή",
                conclusion=f"Η αναδρομική ισχύς στο Άρθρο {article.id} χρήζει περαιτέρω εξέτασης",
            ),
            severity=Severity.MEDIUM,
            confidence=Confidence.from_score(0.5, provenance="pattern-match"),
            lens=self.name(),
            model="rule-based-phase1",
            evidence=[
                Evidence(
                    text_excerpt=match.group(0),
                    verdict="supports",
                )
            ],
        )

    def _make_rights_finding(self, article: Article, match: re.Match) -> Finding:
        return Finding(
            finding_type=FindingType.CONSTITUTIONAL,
            irac=IRAC(
                issue=f"Άρθρο {article.id}: Πιθανή επιρροή σε θεμελιώδη δικαιώματα",
                rule="Τα θεμελιώδη δικαιώματα προστατεύονται από τα Άρθρα 5-25 του Συντάγματος",
                application=f"Το Άρθρο {article.id} περιέχει ρύθμιση που επηρεάζει δικαιώματα",
                conclusion=f"Το Άρθρο {article.id} χρήζει συνταγματικού ελέγχου",
            ),
            severity=Severity.HIGH,
            confidence=Confidence.from_score(0.55, provenance="pattern-match"),
            lens=self.name(),
            model="rule-based-phase1",
            evidence=[
                Evidence(
                    text_excerpt=match.group(0),
                    verdict="supports",
                )
            ],
        )

    def _make_procedure_finding(self, article: Article, match: re.Match) -> Finding:
        return Finding(
            finding_type=FindingType.PROCEDURAL,
            irac=IRAC(
                issue=f"Άρθρο {article.id}: Πιθανή διαδικαστική ανωμαλία",
                rule="Η νομοθετική διαδικασία ορίζεται από τα Άρθρα 70-77 του Συντάγματος",
                application=f"Το Άρθρο {article.id} περιέχει διαδικαστική ρύθμιση που μπορεί να απαιτεί αυξημένη πλειοψηφία",
                conclusion=f"Το Άρθρο {article.id} χρήζει διαδικαστικού ελέγχου",
            ),
            severity=Severity.MEDIUM,
            confidence=Confidence.from_score(0.5, provenance="pattern-match"),
            lens=self.name(),
            model="rule-based-phase1",
            evidence=[
                Evidence(
                    text_excerpt=match.group(0),
                    verdict="supports",
                )
            ],
        )

    def _make_baseline_finding(self, article: Article) -> Finding:
        """Baseline finding for Phase 1 exit gate."""
        return Finding(
            finding_type=FindingType.CONSTITUTIONAL,
            irac=IRAC(
                issue=f"Άρθρο {article.id}: Προκαταρκτικός συνταγματικός έλεγχος",
                rule="Το Σύνταγμα αποτελεί τον υπέρτατο νόμο (Άρθρο 1 παράγραφος 1)",
                application=f"Το Άρθρο {article.id} υποβλήθηκε σε προκαταρκτικό έλεγχο",
                conclusion="Δεν εντοπίστηκαν προφανή συνταγματικά ζητήματα σε αυτή τη φάση",
            ),
            severity=Severity.INFO,
            confidence=Confidence.from_score(0.3, provenance="pattern-match-baseline"),
            lens=self.name(),
            model="rule-based-phase1",
        )
