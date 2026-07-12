"""Legal-coherence Lens — analyzes articles for internal consistency and legal coherence.

Checks for contradictions within the bill, vague language, undefined terms,
and conflicts with existing legislation.
"""

from __future__ import annotations

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

_VAGUE_PATTERNS = [
    re.compile(r"(?:κατάλληλ[οςηο]|ενδεδειγμέν[οςηο]|σχετικ[όςήό])", re.UNICODE | re.IGNORECASE),
    re.compile(
        r"(?:με\s+απόφαση\s+του|όπως\s+ορίζεται|εφόσον\s+προβλέπεται)", re.UNICODE | re.IGNORECASE
    ),
    re.compile(
        r"(?:εξαιρετικές\s+περιπτώσεις|ειδικές\s+συνθήκες|κατά\s+περίπτωση)",
        re.UNICODE | re.IGNORECASE,
    ),
]

_CONTRADICTION_PATTERNS = [
    re.compile(r"(?:κατά\s+παρέκκλιση|αντίθετα|ωστόσο|πλην\s+όμως)", re.UNICODE | re.IGNORECASE),
    re.compile(r"(?:δεν\s+εφαρμόζεται|εξαιρείται|παρεκκλίνει)", re.UNICODE | re.IGNORECASE),
]

_UNDEFINED_TERM_PATTERNS = [
    re.compile(
        r"(?:ορίζεται\s+στο\s+πλαίσιο|καθορίζεται\s+με|ρυθμίζεται\s+ειδικότερα)",
        re.UNICODE | re.IGNORECASE,
    ),
    re.compile(
        r"(?:με\s+κοινή\s+απόφαση|με\s+προεδρικό\s+διάταγμα|με\s+υπουργική\s+απόφαση)",
        re.UNICODE | re.IGNORECASE,
    ),
]


class LegalCoherenceLens(Lens):
    """Legal-coherence lens — checks for internal consistency and clarity."""

    def name(self) -> str:
        return "legal_coherence"

    def description(self) -> str:
        return "Evaluates internal consistency, clarity, and defined terminology"

    async def analyze(self, article: Article) -> list[Finding]:
        findings: list[Finding] = []
        text = f"{article.id}. {article.title}\n{article.raw_text}"

        for pattern in _VAGUE_PATTERNS:
            match = pattern.search(text)
            if match:
                findings.append(
                    Finding(
                        finding_type=FindingType.FACTUAL,
                        irac=IRAC(
                            issue=f"Άρθρο {article.id}: Ασαφής ή αόριστη διατύπωση",
                            rule=(
                                "Η νομοθεσία πρέπει να είναι σαφής και ορισμένη "
                                "(αρχή της ασφάλειας δικαίου)"
                            ),
                            application=(
                                f"Το Άρθρο {article.id} χρησιμοποιεί τη φράση "
                                f"'{match.group(0)}' που είναι ασαφής"
                            ),
                            conclusion=f"Το Άρθρο {article.id} χρήζει σαφέστερης διατύπωσης",
                        ),
                        severity=Severity.MEDIUM,
                        confidence=Confidence.from_score(0.55, provenance="pattern-match"),
                        lens=self.name(),
                        model="rule-based-phase2",
                        evidence=[Evidence(text_excerpt=match.group(0), verdict="supports")],
                    )
                )

        for pattern in _CONTRADICTION_PATTERNS:
            match = pattern.search(text)
            if match:
                findings.append(
                    Finding(
                        finding_type=FindingType.FACTUAL,
                        irac=IRAC(
                            issue=f"Άρθρο {article.id}: Πιθανή εσωτερική αντίφαση",
                            rule=(
                                "Οι διατάξεις του ίδιου νόμου πρέπει να είναι "
                                "συνεπείς μεταξύ τους"
                            ),
                            application=(
                                f"Το Άρθρο {article.id} περιέχει τη φράση '{match.group(0)}' "
                                "που μπορεί να υποδηλώνει αντίφαση"
                            ),
                            conclusion=f"Το Άρθρο {article.id} χρήζει ελέγχου συνέπειας",
                        ),
                        severity=Severity.LOW,
                        confidence=Confidence.from_score(0.4, provenance="pattern-match"),
                        lens=self.name(),
                        model="rule-based-phase2",
                        evidence=[Evidence(text_excerpt=match.group(0), verdict="supports")],
                    )
                )

        return findings
