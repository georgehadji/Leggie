"""Tests for BillAnalysisFlow — end-to-end workflow."""

import pytest

from leggie.application.workflow.bill_analysis_flow import BillAnalysisFlow
from leggie.domain.models import (
    IRAC,
    Confidence,
    EventType,
    Finding,
    FindingType,
    Severity,
    WorkflowState,
)

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


def _make_finding(issue: str, confidence: float = 0.8, finding_type=None,
                  lens: str = "test", severity: str = "medium") -> Finding:
    return Finding(
        finding_type=finding_type or FindingType.CONSTITUTIONAL,
        irac=IRAC(issue=issue, rule="r", application="a", conclusion="c"),
        confidence=Confidence.from_score(confidence),
        severity=Severity(severity),
        lens=lens,
        model="test",
    )


class TestDedupInFlow:
    """Tests for _dedup_findings (FX2)."""

    def test_dedup_removes_near_duplicates(self):
        flow = BillAnalysisFlow(dedup_threshold=0.5)
        f1 = _make_finding("Άρθρο 1: alpha beta gamma", confidence=0.9)
        f2 = _make_finding("Άρθρο 1: alpha beta delta", confidence=0.7)
        f3 = _make_finding("Άρθρο 2: different topic", confidence=0.8)
        result = flow._dedup_findings([f1, f2, f3])
        assert len(result) == 2
        scores = [r.confidence.score for r in result]
        assert 0.9 in scores  # kept higher confidence

    def test_dedup_respects_article_boundary(self):
        flow = BillAnalysisFlow(dedup_threshold=0.5)
        f1 = _make_finding("Άρθρο 1: delegation limits exceeded", confidence=0.9)
        f2 = _make_finding("Άρθρο 5: delegation limits exceeded", confidence=0.8)
        result = flow._dedup_findings([f1, f2])
        assert len(result) == 2  # Different articles, both kept

    def test_dedup_respects_different_types(self):
        flow = BillAnalysisFlow(dedup_threshold=0.5)
        f1 = _make_finding("alpha beta", finding_type=FindingType.CONSTITUTIONAL)
        f2 = _make_finding("alpha beta", finding_type=FindingType.ECONOMIC)
        result = flow._dedup_findings([f1, f2])
        assert len(result) == 2

    def test_dedup_empty_list(self):
        flow = BillAnalysisFlow()
        assert flow._dedup_findings([]) == []

    def test_dedup_idempotent(self):
        flow = BillAnalysisFlow(dedup_threshold=0.5)
        findings = [
            _make_finding("Άρθρο 1: alpha beta", confidence=0.9),
            _make_finding("Άρθρο 1: alpha beta gamma", confidence=0.7),
            _make_finding("Άρθρο 3: zeta", confidence=0.8),
        ]
        first = flow._dedup_findings(findings)
        second_pass = flow._dedup_findings(first)
        assert len(first) == len(second_pass)


class TestDegradationEvent:
    """Tests for degradation callback wiring (FX5)."""

    def test_degradation_records_event(self):
        from leggie.domain.models import Event
        events: list[Event] = []

        def record(e: Event) -> None:
            events.append(e)

        flow = BillAnalysisFlow(on_degradation=record)
        flow._on_degradation(Event(
            event_type=EventType.DEGRADED,
            aggregate_id="test:lens:article:1",
            data={"lens": "constitutional", "error": "test error"},
        ))
        assert len(events) == 1
        assert events[0].event_type == EventType.DEGRADED
        assert "test error" in events[0].data["error"]

    def test_default_degradation_uses_record_event(self):
        flow = BillAnalysisFlow()
        from leggie.domain.models import Event
        flow._on_degradation(Event(
            event_type=EventType.DEGRADED,
            aggregate_id="test",
            data={"test": True},
        ))
        log = flow.get_event_log()
        assert len(log) == 1
        assert log[0].event_type == EventType.DEGRADED
