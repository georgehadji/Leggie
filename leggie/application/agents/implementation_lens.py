"""Implementation Lens — analyzes practical implementation feasibility.

Checks for: unrealistic deadlines, missing transitional provisions,
delegation to uncreated bodies, lack of implementing measures.
"""

from __future__ import annotations

import re

from leggie.application.agents.lens import Lens
from leggie.domain.models import (
    Article, Confidence, Evidence, Finding, FindingType, IRAC, Severity,
)

_DEADLINE_PATTERNS = [
    re.compile(r"(?:εντός\s+\d+\s*ημέρ(?:ας|ών)|άμεση\s+ισχύ|από\s+την\s+έναρξη|μεταβατική\s+περίοδος)", re.UNICODE | re.IGNORECASE),
    re.compile(r"(?:έναρξη\s+ισχύο[ςσ]\s+από|εφαρμόζεται\s+από|ισχύει\s+από)", re.UNICODE | re.IGNORECASE),
]

_TRANSITION_PATTERNS = [
    re.compile(r"(?:μεταβατικ(?:έ[ςσ]|ή|ό)|υφιστάμεν(?:ο[ςισ]|η)|εκκρεμείς)", re.UNICODE | re.IGNORECASE),
    re.compile(r"(?:εξακολουθεί\s+να\s+ισχύει|καταργούμενε[ςσ]|προγενέστερε[ςσ])", re.UNICODE | re.IGNORECASE),
]

_DELEGATION_NO_BODY_PATTERNS = [
    re.compile(r"(?:συνιστάται\s+(?:με|από)|συγκροτείται|ιδρύεται)", re.UNICODE | re.IGNORECASE),
    re.compile(r"(?:με\s+απόφαση\s+του\s+Υπουργού|με\s+πράξη\s+του|απόφαση\s+του\s+Διοικητή)", re.UNICODE | re.IGNORECASE),
]

_MISSING_MEASURES_PATTERNS = [
    re.compile(r"(?:εξουσιοδοτείται\s+να\s+εκδώσει|εκδίδεται\s+κανονιστική|προεδρικό\s+διάταγμα)", re.UNICODE | re.IGNORECASE),
    re.compile(r"(?:καθορίζεται\s+με|ρυθμίζεται\s+με|ορίζεται\s+με)\s+(?:απόφαση|πράξη|διάταγμα)", re.UNICODE | re.IGNORECASE),
]


class ImplementationLens(Lens):
    """Implementation lens — analyzes practical feasibility."""

    def name(self) -> str:
        return "implementation"

    def description(self) -> str:
        return "Evaluates implementation feasibility, deadlines, and transitional provisions"

    async def analyze(self, article: Article) -> list[Finding]:
        findings: list[Finding] = []
        text = f"{article.id}. {article.title}\n{article.raw_text}"

        for pattern in _DEADLINE_PATTERNS:
            match = pattern.search(text)
            if match:
                findings.append(Finding(
                    finding_type=FindingType.IMPLEMENTATION,
                    irac=IRAC(
                        issue=f"Άρθρο {article.id}: Πιθανή μη ρεαλιστική προθεσμία",
                        rule="Οι προθεσμίες εφαρμογής πρέπει να είναι εύλογες και ρεαλιστικές",
                        application=f"Το Άρθρο {article.id} ορίζει προθεσμία/έναρξη ισχύος που μπορεί να μην είναι επαρκής",
                        conclusion=f"Το Άρθρο {article.id} χρήζει ανάλυσης επάρκειας προθεσμιών",
                    ),
                    severity=Severity.MEDIUM,
                    confidence=Confidence.from_score(0.55, provenance="pattern-match"),
                    lens=self.name(), model="rule-based-phase2",
                    evidence=[Evidence(text_excerpt=match.group(0), verdict="supports")],
                ))

        for pattern in _TRANSITION_PATTERNS:
            match = pattern.search(text)
            if match:
                findings.append(Finding(
                    finding_type=FindingType.PROCEDURAL,
                    irac=IRAC(
                        issue=f"Άρθρο {article.id}: Μεταβατικές ρυθμίσεις",
                        rule="Οι μεταβατικές διατάξεις πρέπει να διασφαλίζουν ομαλή μετάβαση",
                        application=f"Το Άρθρο {article.id} περιέχει μεταβατικές ρυθμίσεις",
                        conclusion=f"Οι μεταβατικές διατάξεις του Άρθρου {article.id} χρήζουν εξέτασης",
                    ),
                    severity=Severity.LOW,
                    confidence=Confidence.from_score(0.5, provenance="pattern-match"),
                    lens=self.name(), model="rule-based-phase2",
                    evidence=[Evidence(text_excerpt=match.group(0), verdict="supports")],
                ))

        return findings
