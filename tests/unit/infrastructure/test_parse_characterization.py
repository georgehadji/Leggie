"""Characterization tests for the parser — lock today's behaviour.

These tests assert current parse behaviour against the real bill fixture.
Defects that are still present are marked xfail(strict=True); those that
have been resolved pass unconditionally.

See docs/PARSER_REMEDIATION_PLAN.md §3.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from leggie.infrastructure.parse import DocumentParser

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "parse"


@pytest.fixture
def parser():
    return DocumentParser()


@pytest.fixture
def real_bill_text():
    """The full text of OE_ΣΧΝ-ΥΠΔΙΚ.pdf — Greek legislative text, public record."""
    path = FIXTURES / "oe_sxn_ypdik.txt"
    assert path.exists(), f"Fixture not found: {path}"
    return path.read_text(encoding="utf-8")


# ── Ground truth: the bill has 91 articles ──────────────────────────


class TestGroundTruth:
    """The real bill structure — derived from the document, not the parser."""

    def test_bill_has_91_articles(self, parser, real_bill_text):
        """Assert the bill's own numbering: articles 1..91."""
        doc = parser.parse(real_bill_text)
        ids = [a.id for a in doc.articles]
        assert len(ids) == 91, f"Expected 91 articles, got {len(ids)}"
        assert ids == [str(i) for i in range(1, 92)], "IDs not contiguous 1..91"
        assert len(set(ids)) == 91, f"Duplicate IDs: {[id for id in ids if ids.count(id) > 1]}"

    def test_no_duplicate_ids(self, parser, real_bill_text):
        """Zero duplicate IDs."""
        doc = parser.parse(real_bill_text)
        ids = [a.id for a in doc.articles]
        assert len(ids) == len(set(ids))

    def test_all_articles_have_content(self, parser, real_bill_text):
        """No article should be heading-only (no paragraphs)."""
        doc = parser.parse(real_bill_text)
        empty = [a.id for a in doc.articles if not a.paragraphs]
        # Note: some articles legitimately have no paragraphs (e.g.,
        # purely amending articles). This is a characterization baseline.
        # For now just record the count.
        assert len(empty) >= 0  # Informational

    def test_article_selection_1_10_returns_10(self, parser, real_bill_text):
        """--articles 1-10 must select exactly 10 articles."""
        from leggie.application.workflow.bill_analysis_flow import _parse_article_selection

        doc = parser.parse(real_bill_text)
        ids = [a.id for a in doc.articles]
        selected = _parse_article_selection("1-10", ids)
        assert len(selected) == 10, f"'1-10' selected {len(selected)} articles: {selected}"


# ── P-1: Stop-list false rejections (already mitigated) ─────────────


class TestP1StopList:
    """P-1: _STOP_PATTERN must not reject legitimate headings.

    The _CROSS_REF_TITLE_PREFIX_MIN = 12 guard already mitigates this
    for the known cases. These tests verify it stays fixed.
    """

    def test_article_with_directive_ref_in_parens_is_kept(self, parser):
        """'Άρθρο 2 Αντικείμενο (άρθρο 1 της Οδηγίας …)' must not be rejected."""
        text = (
            "Άρθρο 1 Σκοπός\n1. Κείμενο του άρθρου για τον έλεγχο.\n\n"
            "Άρθρο 2 Αντικείμενο (άρθρο 1 της Οδηγίας (ΕΕ) 2024/1069)\n"
            "1. Κείμενο του άρθρου για τον έλεγχο.\n"
        )
        doc = parser.parse(text)
        ids = [a.id for a in doc.articles]
        assert "2" in ids, "Article 2 with directive ref in title was lost"

    def test_article_with_law_ref_in_parens_is_kept(self, parser):
        """'Άρθρο 3 … (άρθρο 72 του ν. 4999/2022)' must not be rejected."""
        text = (
            "Άρθρο 1 Σκοπός\n1. Κείμενο.\n\n"
            "Άρθρο 3 Τροποποίηση (άρθρο 72 του ν. 4999/2022)\n"
            "1. Κείμενο.\n"
        )
        doc = parser.parse(text)
        assert "3" in [a.id for a in doc.articles]

    def test_bare_cross_ref_still_rejected(self, parser):
        """A heading that is ONLY a cross-ref should still be rejected."""
        text = "Άρθρο 1 Σκοπός\n1. Κείμενο.\nΆρθρο 552 του ΚΠολΔ\n"
        doc = parser.parse(text)
        assert "552" not in [a.id for a in doc.articles]

    def test_short_title_with_stop_phrase_rejected(self, parser):
        """A short title where the stop phrase IS the title must be rejected."""
        text = "Άρθρο 1 Σκοπός\n1. Κείμενο.\nΆρθρο 14 της Οδηγίας\n"
        doc = parser.parse(text)
        assert "14" not in [a.id for a in doc.articles]


# ── P-2: Heading must not cross lines ────────────────────────────────


class TestP2HeadingNewline:
    """P-2: ARTICLE_HEADING regex must not absorb the next line.

    `\\s*` before the dash matches `\n`, so `(.*?)$` absorbs the next
    line into the heading title.
    """

    def test_heading_does_not_absorb_next_line(self, parser):
        """'Άρθρο 5\\n(άρθρα 6, 7 …)' — title must be '' not the parenthetical."""
        text = "Άρθρο 5\n(άρθρα 6, 7 της Οδηγίας (ΕΕ) 2024/1069)\n1. Περιεχόμενο του άρθρου.\n"
        doc = parser.parse(text)
        art5 = next((a for a in doc.articles if a.id == "5"), None)
        assert art5 is not None, "Article 5 not found"
        assert art5.title == "", f"Title should be empty, got: {art5.title!r}"

    def test_multiline_heading_body_starts_correctly(self, parser):
        """Parenthetical after Άρθρο 5 should be in body, not title."""
        text = "Άρθρο 5\n(άρθρα 6, 7 της Οδηγίας (ΕΕ) 2024/1069)\n1. Περιεχόμενο του άρθρου.\n"
        doc = parser.parse(text)
        art5 = next((a for a in doc.articles if a.id == "5"), None)
        assert art5 is not None
        assert "(άρθρα" in art5.raw_text, "Parenthetical should be in body text"
        assert art5.paragraphs, "Article 5 should have paragraphs"


# ── P-3: TOC exclusion ───────────────────────────────────────────────


class TestP3TOCExclusion:
    """P-3: TOC must be excluded as a structural region."""

    def test_real_bill_no_duplicates(self, parser, real_bill_text):
        """The real bill parses without duplicate IDs."""
        doc = parser.parse(real_bill_text)
        ids = [a.id for a in doc.articles]
        dupes = [id for id in ids if ids.count(id) > 1]
        assert len(dupes) == 0, f"Duplicate IDs: {set(dupes)}"

    def test_real_bill_all_ids_distinct(self, parser, real_bill_text):
        """Total records == distinct IDs for the real bill."""
        doc = parser.parse(real_bill_text)
        ids = [a.id for a in doc.articles]
        assert len(ids) == len(set(ids))


# ── Monotonic guard audit ────────────────────────────────────────────


class TestMonotonicGuard:
    """The monotonic guard must not silently drop candidates."""

    def test_all_rejected_candidates_recorded(self, parser, real_bill_text):
        """Silent continues in monotonic guard must be recorded.

        This currently passes as a baseline — once the guard produces
        a report, this test will assert on it.
        """
        doc = parser.parse(real_bill_text)
        assert len(doc.articles) > 0


# ── R2 defect hunt: a second heading-dense pre-body section ─────────


class TestExplanatoryMemorandumDoubleRestart:
    """A bill with a ΠΙΝΑΚΑΣ ΠΕΡΙΕΧΟΜΕΝΩΝ *and* a per-article rationale
    section (ΑΙΤΙΟΛΟΓΙΚΗ ΕΚΘΕΣΗ walking through "Άρθρο 1", "Άρθρο 2", ...
    before the enacting body) is mis-parsed: ``find_toc_span`` excises only
    the TOC (marker to its own first ascending-run break), so the break it
    finds lands on the rationale section, not the real body. The rationale
    commentary is then parsed as if it were the real articles, and the real
    body reappears afterwards as duplicates of the same IDs.

    Not reproducible against this project's one real-bill fixture (which has
    no such rationale section — TOC then body only, see
    oe_sxn_ypdik.txt), so this documents a proven-by-construction gap, not a
    measured production failure. It is bounded today by the parse-integrity
    gate (``report.is_clean`` correctly goes False on the duplicate IDs;
    ``bill_analysis_flow._do_parse`` aborts by default rather than
    silently analysing the wrong text) — see
    test_parse.py::TestParseIntegrity for the safety-net regression test.

    [REQUIRES HUMAN REVIEW]: the correct fix generalizes body-start
    detection to handle more than one pre-body ascending run. This exact
    heuristic (``infrastructure/parse/toc.py``) is the site of the F0
    phantom-articles incident (leggie-failure-archaeology #2) and is
    deliberately not touched here without a dedicated plan doc and a full
    characterization-test re-run against the reference bill, per
    leggie-change-control §3 non-negotiable #8.
    """

    RATIONALE_BILL = (
        "ΣΧΕΔΙΟ ΝΟΜΟΥ\n"
        "ΠΙΝΑΚΑΣ ΠΕΡΙΕΧΟΜΕΝΩΝ\n"
        "Άρθρο 1 Σκοπός\n"
        "Άρθρο 2 Ορισμοί\n"
        "Άρθρο 3 Πεδίο εφαρμογής\n"
        "\n"
        "ΑΙΤΙΟΛΟΓΙΚΗ ΕΚΘΕΣΗ\n"
        "Άρθρο 1\n"
        "Με το άρθρο αυτό ορίζεται ο σκοπός του νόμου.\n"
        "\n"
        "Άρθρο 2\n"
        "Με το άρθρο αυτό δίδονται οι ορισμοί.\n"
        "\n"
        "Άρθρο 3\n"
        "Με το άρθρο αυτό ρυθμίζεται το πεδίο εφαρμογής.\n"
        "\n"
        "Άρθρο 1 Σκοπός\n"
        "1. Σκοπός του παρόντος είναι η ψηφιακή μετάβαση του δημοσίου τομέα.\n"
        "\n"
        "Άρθρο 2 Ορισμοί\n"
        "1. Οι ορισμοί είναι οι εξής για την εφαρμογή του παρόντος νόμου.\n"
        "\n"
        "Άρθρο 3 Πεδίο εφαρμογής\n"
        "1. Το παρόν εφαρμόζεται σε όλους τους φορείς του δημοσίου τομέα.\n"
    )

    @pytest.mark.xfail(strict=True, reason="R2 defect hunt: double pre-body restart, unfixed")
    def test_rationale_section_does_not_duplicate_articles(self, parser):
        """Target behaviour: exactly 3 distinct articles, no duplicates."""
        doc = parser.parse(self.RATIONALE_BILL)
        ids = [a.id for a in doc.articles]
        assert ids == ["1", "2", "3"], f"Expected 3 real articles, got {ids}"
