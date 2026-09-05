"""Tests for the Improvement Engine — suggestion generation per finding.

DH: MinimalChangeStrategy used to derive Suggestion.article_id by naively
splitting finding.irac.issue free text, instead of using the reliable,
always-populated finding.article_id field (see domain/models/__init__.py's
own field description, and the existing article_number_of() helper in
services/cove_verifier.py that every lens's regex-fallback issue-text shape,
"Άρθρο {id}: ...", already breaks that naive split on — the digit token is
immediately followed by a colon, so `"5:".isdigit()` is False).

reports.py groups suggestions by exact article_id equality (article_id ==
article.id) and treats only a FALSY article_id as "general" — so a wrong,
non-empty article_id (e.g. "5:") matches neither bucket and the suggestion
silently vanishes from the rendered report. This is the silent-data-loss
failure mode, not a crash, so it needed a positive-outcome test, not an
exception test.
"""

from __future__ import annotations

from leggie.application.agents.improver import MinimalChangeStrategy, ReformStrategy
from leggie.domain.models import IRAC, Confidence, Finding, FindingType, Severity


def make_finding(
    finding_type: FindingType,
    article_id: str,
    issue: str,
    severity: Severity = Severity.HIGH,
) -> Finding:
    return Finding(
        finding_type=finding_type,
        article_id=article_id,
        irac=IRAC(issue=issue, rule="r", application="a", conclusion="c"),
        severity=severity,
        confidence=Confidence.from_score(0.6),
        lens="test",
        model="test",
    )


class TestMinimalChangeStrategyArticleId:
    """Proof of defect / fix: article_id must match finding.article_id
    exactly (what reports.py groups suggestions by), for every finding_type
    MinimalChangeStrategy handles."""

    def test_constitutional_article_id_matches_finding_article_id(self):
        f = make_finding(
            FindingType.CONSTITUTIONAL,
            article_id="5",
            issue="Άρθρο 5: Πιθανή υπέρβαση ορίων νομοθετικής εξουσιοδότησης",
        )
        suggestions = MinimalChangeStrategy().generate(f)
        assert len(suggestions) == 1
        assert suggestions[0].article_id == "5"

    def test_eu_compliance_article_id_matches_finding_article_id(self):
        f = make_finding(
            FindingType.EU_COMPLIANCE,
            article_id="12",
            issue="Άρθρο 12: Επεξεργασία προσωπικών δεδομένων",
        )
        suggestions = MinimalChangeStrategy().generate(f)
        assert len(suggestions) == 1
        assert suggestions[0].article_id == "12"

    def test_economic_article_id_matches_finding_article_id(self):
        f = make_finding(
            FindingType.ECONOMIC,
            article_id="83",
            issue="Άρθρο 83: Οικονομική επιβάρυνση",
        )
        suggestions = MinimalChangeStrategy().generate(f)
        assert len(suggestions) == 1
        assert suggestions[0].article_id == "83"

    def test_trusts_the_structured_field_over_misleading_issue_text(self):
        """Boundary: issue text names a different number than the real
        article_id (e.g. a cross-reference to another article) — the
        structured field must win, not whatever number appears first in
        the free text."""
        f = make_finding(
            FindingType.CONSTITUTIONAL,
            article_id="5",
            issue="Σε συνδυασμό με το άρθρο 43 του Συντάγματος, υπάρχει ζήτημα",
        )
        suggestions = MinimalChangeStrategy().generate(f)
        assert suggestions[0].article_id == "5"

    def test_falls_back_to_empty_when_neither_source_has_a_number(self):
        """Boundary: legacy finding with no article_id and no parseable
        number in the issue text — must degrade to "" (routed to the
        report's general section), not raise and not fabricate a number."""
        f = make_finding(
            FindingType.ECONOMIC,
            article_id="",
            issue="Γενικό ζήτημα χωρίς αναφορά άρθρου",
        )
        suggestions = MinimalChangeStrategy().generate(f)
        assert suggestions[0].article_id == ""

    def test_reform_strategy_unaffected_uses_general_bucket_by_design(self):
        """No-regression: ReformStrategy hardcodes article_id="" (always
        "general") — this fix must not change that documented behaviour."""
        f = make_finding(
            FindingType.CONSTITUTIONAL,
            article_id="5",
            issue="Άρθρο 5: Πιθανή υπέρβαση ορίων",
            severity=Severity.CRITICAL,
        )
        suggestions = ReformStrategy().generate(f)
        assert len(suggestions) == 1
        assert suggestions[0].article_id == ""
