"""End-to-end integration test — full bill analysis pipeline.

Tests the complete flow: real bill text → ingest → parse → 5 lenses →
Skeptic → CoVe → reports. No mocks, real file I/O.
"""

import pytest

from leggie.application.workflow.bill_analysis_flow import BillAnalysisFlow
from leggie.infrastructure.ingest_adapter import IngestAdapter
from leggie.infrastructure.parse_adapter import ParseAdapter


def _flow(**kw) -> BillAnalysisFlow:
    """ARCH-05: the flow no longer default-constructs its ingest/parse adapters,
    so tests inject the same two the container binds. Both are offline."""
    kw.setdefault("ingester", IngestAdapter())
    kw.setdefault("parser", ParseAdapter())
    return BillAnalysisFlow(**kw)


REAL_BILL = """
ΣΧΕΔΙΟ ΝΟΜΟΥ
«Ρυθμίσεις για την ψηφιακή διακυβέρνηση και την προστασία δεδομένων»

Άρθρο 1 – Σκοπός και πεδίο εφαρμογής
1. Σκοπός του παρόντος νόμου είναι η ενσωμάτωση της Οδηγίας 2022/2555
   (CELEX:32022L2555) για την κυβερνοασφάλεια.
2. Με προεδρικό διάταγμα που εκδίδεται με πρόταση του Υπουργού Ψηφιακής
   Διακυβέρνησης, καθορίζονται οι τεχνικές προδιαγραφές εφαρμογής.

Άρθρο 2 – Επεξεργασία προσωπικών δεδομένων
1. Η επεξεργασία προσωπικών δεδομένων διενεργείται σύμφωνα με τον
   Γενικό Κανονισμό Προστασίας Δεδομένων (GDPR).
2. Το πρόστιμο για παραβάσεις ανέρχεται έως 500.000 ευρώ.
3. Τα υποκείμενα έχουν δικαίωμα πρόσβασης και φορητότητας των δεδομένων τους.

Άρθρο 3 – Προθεσμίες και μεταβατικές διατάξεις
1. Εντός 30 ημερών από τη δημοσίευση, οι υπόχρεοι φορείς υποβάλλουν
   δήλωση συμμόρφωσης.
2. Η ισχύς των διατάξεων αρχίζει αναδρομικά από 1.1.2025.
3. Μεταβατική περίοδος 60 ημερών προβλέπεται για την προσαρμογή.

Άρθρο 4 – Τελικές διατάξεις
1. Η ισχύς του παρόντος αρχίζει από τη δημοσίευσή του στην Εφημερίδα
   της Κυβερνήσεως (ΦΕΚ Α 137/2023).
"""


@pytest.fixture
def bill_file(tmp_path):
    path = tmp_path / "e2e_bill.txt"
    path.write_text(REAL_BILL, encoding="utf-8")
    return path


class TestEndToEnd:
    """Full pipeline integration tests."""

    @pytest.mark.asyncio
    async def test_full_pipeline_findings_and_reports(self, bill_file, tmp_path):
        """End-to-end: bill → findings + reports."""
        flow = _flow()
        findings, reports = await flow.run(bill_file, output_dir=tmp_path)

        # Should find issues across multiple lenses
        assert len(findings) > 0, "Should produce at least one finding"

    @pytest.mark.asyncio
    async def test_full_pipeline_reports_rendered(self, bill_file, tmp_path):
        """Both report types rendered end-to-end."""
        flow = _flow()
        findings, reports = await flow.run(bill_file, output_dir=tmp_path)

        assert len(reports) == 2, "Should produce 2 reports"
        assert reports[0].report_type == "executive_summary"
        assert reports[1].report_type == "article_by_article"

        # Reports should render to markdown
        for report in reports:
            md = report.to_markdown()
            assert len(md) > 200, f"{report.report_type} should have content"

    @pytest.mark.asyncio
    async def test_full_pipeline_events_audit_trail(self, bill_file, tmp_path):
        """Audit trail recorded for the full run."""
        flow = _flow()
        await flow.run(bill_file, output_dir=tmp_path)

        events = flow.get_event_log()
        assert len(events) >= 6, "Should record at least 6 events"
        event_types = [str(e.event_type) for e in events]
        assert "analysis_started" in event_types
        assert "workflow_completed" in event_types

    @pytest.mark.asyncio
    async def test_full_pipeline_state_complete(self, bill_file, tmp_path):
        """Flow reaches DONE state."""
        # WorkflowState is re-exported incidentally by bill_analysis_flow;
        # import it from the module that defines it.
        from leggie.domain.models import WorkflowState

        flow = _flow()
        await flow.run(bill_file, output_dir=tmp_path)
        assert flow.state == WorkflowState.DONE

    @pytest.mark.asyncio
    async def test_full_pipeline_suggestions_generated(self, bill_file, tmp_path):
        """Improvement suggestions produced."""
        flow = _flow()
        await flow.run(bill_file, output_dir=tmp_path)
        assert len(flow.suggestions) > 0, "Should generate improvement suggestions"

    @pytest.mark.asyncio
    async def test_full_pipeline_reports_properties(self, bill_file, tmp_path):
        """Reports and suggestions accessible via properties."""
        flow = _flow()
        await flow.run(bill_file, output_dir=tmp_path)

        assert len(flow.reports) == 2
        assert len(flow.suggestions) > 0
        assert len(flow.findings) > 0
        assert len(flow.get_event_log()) > 0
