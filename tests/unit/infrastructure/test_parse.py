"""Tests for the Greek legal document parser."""

import pytest

from leggie.domain.models import Document
from leggie.infrastructure.parse import DocumentParser


@pytest.fixture
def parser():
    return DocumentParser()


SAMPLE_BILL = """
ΣΧΕΔΙΟ ΝΟΜΟΥ
«Ρυθμίσεις για την ψηφιακή διακυβέρνηση»

Άρθρο 1 – Σκοπός
1. Με τις διατάξεις του παρόντος σκοπός είναι η ψηφιακή μετάβαση.
2. Οι διατάξεις εφαρμόζονται σε όλο τον δημόσιο τομέα.
  α) Στους φορείς της Γενικής Κυβέρνησης
  β) Στους Οργανισμούς Τοπικής Αυτοδιοίκησης

Άρθρο 2 – Ορισμοί
1. Για την εφαρμογή του παρόντος νοούνται ως:
  α) «Ψηφιακή υπηρεσία»: κάθε υπηρεσία παρεχόμενη μέσω ΤΠΕ
  β) «Διαλειτουργικότητα»: η δυνατότητα ανταλλαγής δεδομένων

Άρθρο 5 – Μεταβατικές διατάξεις
1. Η ισχύς του παρόντος αρχίζει από 1.1.2026.
2. Οι διατάξεις του άρθρου 3 εφαρμόζονται από 1.6.2026.
"""


class TestDocumentParser:
    def test_parse_document_returns_document(self, parser):
        doc = parser.parse(SAMPLE_BILL, title="Test Bill", source_format="txt")
        assert isinstance(doc, Document)
        assert doc.title == "Test Bill"
        assert doc.source_format == "txt"

    def test_parse_extracts_articles(self, parser):
        doc = parser.parse(SAMPLE_BILL)
        assert len(doc.articles) == 3

    def test_parse_article_ids(self, parser):
        doc = parser.parse(SAMPLE_BILL)
        assert doc.articles[0].id == "1"
        assert doc.articles[1].id == "2"
        assert doc.articles[2].id == "5"

    def test_parse_article_titles(self, parser):
        doc = parser.parse(SAMPLE_BILL)
        assert "Σκοπός" in doc.articles[0].title
        assert "Ορισμοί" in doc.articles[1].title
        assert "Μεταβατικές" in doc.articles[2].title

    def test_parse_paragraphs(self, parser):
        doc = parser.parse(SAMPLE_BILL)
        article = doc.articles[0]
        assert len(article.paragraphs) == 2
        assert article.paragraphs[0].number == "1"

    def test_parse_subparagraphs(self, parser):
        doc = parser.parse(SAMPLE_BILL)
        article = doc.articles[0]
        para = article.paragraphs[1]
        assert len(para.subparagraphs) >= 2
        assert para.subparagraphs[0].letter == "α"

    def test_parse_preamble(self, parser):
        doc = parser.parse(SAMPLE_BILL)
        assert "ΣΧΕΔΙΟ ΝΟΜΟΥ" in doc.preamble
        assert "ψηφιακή" in doc.preamble

    def test_parse_empty_text(self, parser):
        doc = parser.parse("")
        assert len(doc.articles) == 0
        assert doc.title == "Untitled Document"

    def test_parse_no_articles(self, parser):
        doc = parser.parse("Just some preamble text without articles.")
        assert len(doc.articles) == 0


class TestCrossRefRejection:
    """F0: Parser rejects in-body cross-references as article headings."""

    def test_rejects_code_ref(self, parser):
        """'του άρθρου 552 ΚΠολΔ' should not become article 552."""
        text = "Άρθρο 1\nΠεριεχόμενο\nΤο άρθρο 552 του ΚΠολΔ εφαρμόζεται.\n"
        doc = parser.parse(text)
        nums = [a.id for a in doc.articles]
        assert "1" in nums
        assert "552" not in nums

    def test_rejects_law_ref(self, parser):
        """'του ν. 4635/2019' reference should not become article 4635."""
        text = "Άρθρο 1\nΠεριεχόμενο\nΣύμφωνα με το άρθρο 43 του ν. 4635/2019.\n"
        doc = parser.parse(text)
        nums = [a.id for a in doc.articles]
        assert "1" in nums
        assert "4635" not in nums
        assert "43" not in nums

    def test_rejects_directive_ref(self, parser):
        text = "Άρθρο 1\nΠεριεχόμενο\nΚατά το άρθρο 14 της Οδηγίας 2024/1069.\n"
        doc = parser.parse(text)
        nums = [a.id for a in doc.articles]
        assert "14" not in nums
        assert "1" in nums

    def test_accepts_legitimate_article(self, parser):
        """A real article heading should still be detected."""
        text = "Άρθρο 1 – Σκοπός\nΚείμενο του άρθρου\nΆρθρο 2 – Ορισμοί\nΚείμενο του άρθρου\n"
        doc = parser.parse(text)
        nums = [a.id for a in doc.articles]
        assert nums == ["1", "2"]

    def test_rejects_monotonic_jump(self, parser):
        """Large jump (1→552→59) means 552 is a cross-reference."""
        text = "Άρθρο 1\nΠεριεχόμενο\nΤο άρθρο 552 ΚΠολΔ\nΆρθρο 2\nΠεριεχόμενο\n"
        doc = parser.parse(text)
        nums = [a.id for a in doc.articles]
        assert "552" not in nums
        assert nums == ["1", "2"]


TOC_BILL = """
ΣΧΕΔΙΟ ΝΟΜΟΥ
«Ρυθμίσεις για την ψηφιακή διακυβέρνηση»
ΠΙΝΑΚΑΣ ΠΕΡΙΕΧΟΜΕΝΩΝ
ΜΕΡΟΣ Α’
ΓΕΝΙΚΕΣ ΔΙΑΤΑΞΕΙΣ
Άρθρο 1 Σκοπός
Άρθρο 2 Αντικείμενο (άρθρο 1 της Οδηγίας (ΕΕ) 2024/1069)
Άρθρο 3 Τροποποίηση άρθρου 72 του ν. 4999/2022
ΜΕΡΟΣ Α’
ΓΕΝΙΚΕΣ ΔΙΑΤΑΞΕΙΣ
Άρθρο 1 Σκοπός
1. Σκοπός του παρόντος είναι η ψηφιακή μετάβαση του δημοσίου τομέα.
2. Οι διατάξεις εφαρμόζονται σε όλο τον δημόσιο τομέα της χώρας.

Άρθρο 2 Αντικείμενο (άρθρο 1 της Οδηγίας (ΕΕ) 2024/1069)
1. Αντικείμενο του παρόντος είναι η ενσωμάτωση της Οδηγίας στο εθνικό δίκαιο.

Άρθρο 3 Τροποποίηση άρθρου 72 του ν. 4999/2022
1. Το άρθρο 72 του ν. 4999/2022 αντικαθίσταται ως εξής.
"""


class TestTableOfContents:
    """A bill's ΠΙΝΑΚΑΣ ΠΕΡΙΕΧΟΜΕΝΩΝ must not become phantom articles.

    Regression: the TOC listed articles 1..91, which drove the monotonic
    guard's ``last_num`` to 91. When the body restarted at Άρθρο 1 every
    heading with ``abs(delta) > 50`` was rejected, so real articles 1..40
    were dropped and the first surviving body article was exactly 91-50=41.
    """

    def test_toc_entries_do_not_become_articles(self, parser):
        doc = parser.parse(TOC_BILL)
        assert [a.id for a in doc.articles] == ["1", "2", "3"]

    def test_no_duplicate_articles_from_toc(self, parser):
        doc = parser.parse(TOC_BILL)
        ids = [a.id for a in doc.articles]
        assert len(ids) == len(set(ids))

    def test_body_articles_keep_their_content(self, parser):
        doc = parser.parse(TOC_BILL)
        assert all(a.paragraphs for a in doc.articles)
        assert "ψηφιακή μετάβαση" in doc.articles[0].paragraphs[0].text

    def test_body_restart_does_not_drop_low_numbered_articles(self, parser):
        """A TOC running past 50 must not cascade-drop the body's article 1."""
        toc = "\n".join(f"Άρθρο {n} Τίτλος {n}" for n in range(1, 92))
        body = "\n\n".join(
            f"Άρθρο {n} Τίτλος {n}\n1. Κείμενο της διάταξης {n} για τον έλεγχο."
            for n in range(1, 92)
        )
        doc = parser.parse(f"ΣΧΕΔΙΟ ΝΟΜΟΥ\nΠΙΝΑΚΑΣ ΠΕΡΙΕΧΟΜΕΝΩΝ\n{toc}\n{body}\n")
        ids = [a.id for a in doc.articles]
        assert ids == [str(n) for n in range(1, 92)]

    def test_bill_without_toc_is_unaffected(self, parser):
        doc = parser.parse(SAMPLE_BILL)
        assert [a.id for a in doc.articles] == ["1", "2", "5"]


class TestAmendingTitles:
    """Greek amending titles cite other instruments — they are REAL headings.

    Regression: ``_STOP_PATTERN`` was applied to the whole heading line, so
    the standard 'Τροποποίηση άρθρου X του ν. YYYY' title format was
    rejected outright. On the reference bill this silently dropped 22 real
    article headings.
    """

    def test_keeps_title_citing_a_directive(self, parser):
        text = (
            "Άρθρο 1 Σκοπός\n1. Κείμενο του άρθρου για τον έλεγχο.\n\n"
            "Άρθρο 2 Αντικείμενο (άρθρο 1 της Οδηγίας (ΕΕ) 2024/1069)\n"
            "1. Κείμενο του άρθρου για τον έλεγχο.\n"
        )
        doc = parser.parse(text)
        assert [a.id for a in doc.articles] == ["1", "2"]

    def test_keeps_title_amending_another_law(self, parser):
        text = (
            "Άρθρο 60 Σκοπός\n1. Κείμενο του άρθρου για τον έλεγχο.\n\n"
            "Άρθρο 61 Προσθήκη άρθρου 58Α και τροποποίηση άρθρου 72 του ν. 4999/2022\n"
            "1. Κείμενο του άρθρου για τον έλεγχο.\n"
        )
        doc = parser.parse(text)
        assert "61" in [a.id for a in doc.articles]

    def test_still_rejects_bare_cross_reference_heading(self, parser):
        """A heading that is *only* a reference has no substantive title."""
        text = (
            "Άρθρο 1 Σκοπός\n1. Κείμενο του άρθρου για τον έλεγχο.\n"
            "Άρθρο 552 του ΚΠολΔ\n"
        )
        doc = parser.parse(text)
        assert "552" not in [a.id for a in doc.articles]


class TestCitationExtraction:
    def test_extract_fek_citation(self, parser):
        text = "Όπως ορίζεται στο ΦΕΚ Α 137/2023"
        citations = parser.extract_citations(text)
        fek_cites = [c for c in citations if c["type"] == "fek"]
        assert len(fek_cites) == 1
        assert "137" in fek_cites[0]["identifier"]

    def test_extract_celex_citation(self, parser):
        text = "Σύμφωνα με CELEX:32018L1972"
        citations = parser.extract_citations(text)
        celex = [c for c in citations if c["type"] == "celex"]
        assert len(celex) == 1

    def test_extract_ecli_citation(self, parser):
        text = "Απόφαση ECLI:GR:ΣτΕ:2023:1234"
        citations = parser.extract_citations(text)
        ecli = [c for c in citations if c["type"] == "ecli"]
        assert len(ecli) == 1

    def test_extract_multiple_citations(self, parser):
        text = "ΦΕΚ Α 137/2023 και CELEX:32018L1972 και ECLI:GR:ΣτΕ:2023:1234"
        citations = parser.extract_citations(text)
        assert len(citations) == 3

    def test_extract_no_citations(self, parser):
        text = "Απλό κείμενο χωρίς παραπομπές"
        citations = parser.extract_citations(text)
        assert len(citations) == 0
