"""Tests for BillAnalysisFlow — end-to-end workflow."""

import pytest

from leggie.application.workflow.bill_analysis_flow import BillAnalysisFlow
from leggie.domain.models import WorkflowState

SAMPLE_BILL = """
ΣΧΕΔΙΟ ΝΟΜΟΥ
«Δοκιμαστικό νομοσχέδιο»

Άρθρο 1 – Εξουσιοδοτική διάταξη
1. Με προεδρικό διάταγμα που εκδίδεται με πρόταση του Υπουργού,
   είναι δυνατή η εξουσιοδότηση για την έκδοση κανονιστικών πράξεων.
2. Οι διατάξεις του παρόντος εφαρμόζονται αναδρομικά από 1.1.2025.

Άρθρο 2 – Απλή διάταξη
1. Η ισχύς του παρόντος αρχίζει από τη δημοσίευσή του.
"""


@pytest.fixture
def sample_bill_file(tmp_path):
    path = tmp_path / "bill.txt"
    path.write_text(SAMPLE_BILL, encoding="utf-8")
    return path


class TestBillAnalysisFlow:
    @pytest.mark.asyncio
    async def test_run_returns_findings(self, sample_bill_file):
        flow = BillAnalysisFlow()
        findings, reports = await flow.run(sample_bill_file)
        assert len(findings) > 0

    @pytest.mark.asyncio
    async def test_run_state_transitions(self, sample_bill_file):
        flow = BillAnalysisFlow()
        assert flow.state == WorkflowState.IDLE
        await flow.run(sample_bill_file)
        assert flow.state == WorkflowState.DONE

    @pytest.mark.asyncio
    async def test_run_records_events(self, sample_bill_file):
        flow = BillAnalysisFlow()
        await flow.run(sample_bill_file)
        events = flow.get_event_log()
        assert len(events) >= 5  # At least: analysis_started, stage_completed × 4+, workflow_completed

    @pytest.mark.asyncio
    async def test_run_returns_findings_with_irac(self, sample_bill_file):
        flow = BillAnalysisFlow()
        findings, reports = await flow.run(sample_bill_file)
        for f in findings:
            assert f.irac.issue
            assert f.irac.rule
            assert f.irac.application
            assert f.irac.conclusion

    @pytest.mark.asyncio
    async def test_run_empty_document(self, tmp_path):
        path = tmp_path / "empty.txt"
        path.write_text("No articles here.", encoding="utf-8")
        flow = BillAnalysisFlow()
        findings, reports = await flow.run(path)
        assert len(findings) == 0

    @pytest.mark.asyncio
    async def test_run_findings_property(self, sample_bill_file):
        flow = BillAnalysisFlow()
        await flow.run(sample_bill_file)
        assert len(flow.findings) > 0

    @pytest.mark.asyncio
    async def test_run_returns_reports(self, sample_bill_file):
        flow = BillAnalysisFlow()
        findings, reports = await flow.run(sample_bill_file)
        assert len(reports) == 2
        assert reports[0].report_type == "executive_summary"
        assert reports[1].report_type == "article_by_article"

    @pytest.mark.asyncio
    async def test_reports_have_content(self, sample_bill_file):
        flow = BillAnalysisFlow()
        findings, reports = await flow.run(sample_bill_file)
        for report in reports:
            md = report.to_markdown()
            assert "Legal Analysis" in md
            assert len(md) > 100

    @pytest.mark.asyncio
    async def test_suggestions_property(self, sample_bill_file):
        flow = BillAnalysisFlow()
        await flow.run(sample_bill_file)
        assert len(flow.suggestions) > 0
