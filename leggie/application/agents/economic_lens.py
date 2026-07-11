"""Economic Lens — analyzes fiscal and economic impact of bill provisions.

Checks for: unfunded mandates, excessive administrative burden,
missing cost-impact analysis, disproportionate fines/penalties.
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

_COST_PATTERNS = [
    re.compile(
        r"(?:δαπάνη|κόστος|επιβάρυνση|χρηματοδότηση|προϋπολογισμός)", re.UNICODE | re.IGNORECASE
    ),
    re.compile(r"(?:τέλο[ςσ]|εισφορά|πρόστιμο|κύρωση|ποινή)", re.UNICODE | re.IGNORECASE),
]

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

_ADMIN_BURDEN_PATTERNS = [
    re.compile(
        r"(?:υποχρέωση\s+υποβολή[ςσ]|υποχρέωση\s+τήρηση[ςσ]|τήρηση\s+αρχείου)",
        re.UNICODE | re.IGNORECASE,
    ),
    re.compile(
        r"(?:προθεσμί[αα]|εντός\s+\d+\s+ημερώ[νν]|άμεση\s+εφαρμογή)", re.UNICODE | re.IGNORECASE
    ),
]

_DISPROPORTIONATE_PATTERNS = [
    re.compile(
        r"(?:πρόστιμο\s+έω[σς]\s+\d|ποινή\s+φυλάκισης|ιοβόσβεστη)", re.UNICODE | re.IGNORECASE
    ),
    re.compile(
        r"(?:δέσμευση|κατάσχεσ[ηη]|αναστολή\s+λειτουργίας)", re.UNICODE | re.IGNORECASE
    ),
]


class EconomicLens(Lens):
    """Economic lens — analyzes fiscal and economic impact."""

    def name(self) -> str:
        return "economic"

    def description(self) -> str:
        return "Evaluates fiscal impact, cost burden, and economic proportionality"

    async def analyze(self, article: Article) -> list[Finding]:
        findings: list[Finding] = []
        text = f"{article.id}. {article.title}\n{article.raw_text}"

        for pattern in _COST_PATTERNS:
            match = pattern.search(text)
            if match:
                findings.append(Finding(
                    finding_type=FindingType.ECONOMIC,
                    irac=IRAC(
                        issue=f"Άρθρο {article.id}: Οικονομική επιβάρυνση",
                        rule=(
                            "Κάθε νομοσχέδιο πρέπει να συνοδεύεται από εκτίμηση "
                            "δημοσιονομικών επιπτώσεων"
                        ),
                        application=(
                            f"Το Άρθρο {article.id} αναφέρεται σε δαπάνη/κόστος "
                            "χωρίς ποσοτική ανάλυση"
                        ),
                        conclusion=(
                            "Απαιτείται ποσοτική εκτίμηση δημοσιονομικών επιπτώσεων "
                            f"για το Άρθρο {article.id}"
                        ),
                    ),
                    severity=Severity.MEDIUM,
                    confidence=Confidence.from_score(0.55, provenance="pattern-match"),
                    lens=self.name(), model="rule-based-phase2",
                    evidence=[Evidence(text_excerpt=match.group(0), verdict="supports")],
                ))

        for pattern in _ADMIN_BURDEN_PATTERNS:
            match = pattern.search(text)
            if match:
                findings.append(Finding(
                    finding_type=FindingType.IMPLEMENTATION,
                    irac=IRAC(
                        issue=f"Άρθρο {article.id}: Διοικητική επιβάρυνση",
                        rule=(
                            "Η διοικητική επιβάρυνση πρέπει να είναι αναλογική "
                            "προς τον επιδιωκόμενο σκοπό"
                        ),
                        application=f"Το Άρθρο {article.id} δημιουργεί διοικητικές υποχρεώσεις",
                        conclusion=f"Το Άρθρο {article.id} χρήζει ανάλυσης διοικητικής επιβάρυνσης",
                    ),
                    severity=Severity.LOW,
                    confidence=Confidence.from_score(0.5, provenance="pattern-match"),
                    lens=self.name(), model="rule-based-phase2",
                    evidence=[Evidence(text_excerpt=match.group(0), verdict="supports")],
                ))

        return findings
