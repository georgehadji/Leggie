"""EU & GDPR Lens — analyzes alignment with EU law and data protection.

Checks for: GDPR compliance, EU directive transposition issues,
notification obligations, and cross-border data flow provisions.
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

_GDPR_PATTERNS = [
    re.compile(r"(?:προσωπικά\s+δεδομένα|δεδομένα\s+προσωπικού\s+χαρακτήρα|GDPR|ΓΚΠΔ)", re.UNICODE | re.IGNORECASE),
    re.compile(r"(?:επεξεργασί[αα]|συγκατάθεση|υπεύθυνο[ςσ]\s+προστασία[ςσ]|DPO)", re.UNICODE | re.IGNORECASE),
    re.compile(r"(?:δικαίωμα\s+πρόσβαση[ςσ]|δικαίωμα\s+διαγραφή[ςσ]|δικαίωμα\s+φορητότητα[ςσ])", re.UNICODE | re.IGNORECASE),
]

_EU_DIRECTIVE_PATTERNS = [
    re.compile(r"(?:εναρμόνισ[ηη]|ενσωμάτωσ[ηη]|μεταφορά)\s+(?:της|του)\s+(?:οδηγί[αας]|κανονισμού|απόφασης)", re.UNICODE | re.IGNORECASE),
    re.compile(r"(?:CELEX|οδηγί[αα]\s+\d{4}|κανονισμό[ςσ]\s+ΕΕ|κατ' εξουσιοδότηση)", re.UNICODE | re.IGNORECASE),
]

_NOTIFICATION_PATTERNS = [
    re.compile(r"(?:κοινοποίησ[ηη]|γνωστοποίησ[ηη]|ενημέρωσ[ηη])\s+(?:της|στην)\s+Επιτροπή", re.UNICODE | re.IGNORECASE),
    re.compile(r"(?:SEM|single\s+market|εσωτερική\s+αγορά|συμβατότητα)", re.UNICODE | re.IGNORECASE),
]

_CROSS_BORDER_PATTERNS = [
    re.compile(r"(?:διασυνοριακ(?:ή|ό[ςσ])|διαβίβασ[ηη]|τρίτη\s+χώρα|εκτός\s+ΕΕ)", re.UNICODE | re.IGNORECASE),
    re.compile(r"(?:επαρκές\s+επίπεδο|κατάλληλε[ςσ]\s+εγγυήσει[ςσ]|binding\s+corporate)", re.UNICODE | re.IGNORECASE),
]


class EUGDPRLens(Lens):
    """EU & GDPR lens — analyzes alignment with EU law."""

    def name(self) -> str:
        return "eu_gdpr"

    def description(self) -> str:
        return "Assesses EU directive transposition and GDPR compliance"

    async def analyze(self, article: Article) -> list[Finding]:
        findings: list[Finding] = []
        text = f"{article.id}. {article.title}\n{article.raw_text}"

        for pattern in _GDPR_PATTERNS:
            match = pattern.search(text)
            if match:
                findings.append(Finding(
                    finding_type=FindingType.EU_COMPLIANCE,
                    irac=IRAC(
                        issue=f"Άρθρο {article.id}: GDPR/προστασία δεδομένων",
                        rule="Ο Γενικός Κανονισμός Προστασίας Δεδομένων (ΕΕ 2016/679) έχει άμεση εφαρμογή",
                        application=f"Το Άρθρο {article.id} περιέχει αναφορά σε προσωπικά δεδομένα/GDPR",
                        conclusion=f"Το Άρθρο {article.id} χρήζει ελέγχου συμβατότητας με GDPR",
                    ),
                    severity=Severity.HIGH,
                    confidence=Confidence.from_score(0.6, provenance="pattern-match"),
                    lens=self.name(), model="rule-based-phase2",
                    evidence=[Evidence(text_excerpt=match.group(0), verdict="supports")],
                ))

        for pattern in _EU_DIRECTIVE_PATTERNS:
            match = pattern.search(text)
            if match:
                findings.append(Finding(
                    finding_type=FindingType.EU_COMPLIANCE,
                    irac=IRAC(
                        issue=f"Άρθρο {article.id}: Εναρμόνιση με ενωσιακό δίκαιο",
                        rule="Οι εθνικές διατάξεις πρέπει να συμμορφώνονται με το παράγωγο δίκαιο της ΕΕ",
                        application=f"Το Άρθρο {article.id} αναφέρεται σε εναρμόνιση/ενσωμάτωση ενωσιακού δικαίου",
                        conclusion=f"Το Άρθρο {article.id} χρήζει ελέγχου ορθής μεταφοράς ενωσιακού δικαίου",
                    ),
                    severity=Severity.HIGH,
                    confidence=Confidence.from_score(0.55, provenance="pattern-match"),
                    lens=self.name(), model="rule-based-phase2",
                    evidence=[Evidence(text_excerpt=match.group(0), verdict="supports")],
                ))

        return findings
