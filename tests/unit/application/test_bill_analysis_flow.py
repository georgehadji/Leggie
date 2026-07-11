"""Tests for BillAnalysisFlow — end-to-end workflow."""

import json

import pytest

from leggie.application.services.rerank import CompositeReranker
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
    def test_default_object_graph_is_baseline(self, sample_bill_file):
        """With default settings the flow graph matches the pre-feature baseline."""
        flow = BillAnalysisFlow()
        assert flow._orchestrator._use_verbalized_sampling is False
        assert isinstance(flow._reranker, CompositeReranker)

    def test_opt_in_flags_thread_to_construction(self):
        """Verbalized sampling and model reranker flags reach the object graph."""
        from leggie.application.ports.reranker import RerankerPort, RerankResult

        class FakeReranker(RerankerPort):
            async def rerank(self, query, documents, model="", top_k=None):
                return []

        flow = BillAnalysisFlow(
            use_verbalized_sampling=True,
            reranker_name="model",
            reranker_port=FakeReranker(),
        )
        assert flow._orchestrator._use_verbalized_sampling is True
        from leggie.application.services.rerank import ModelBasedReranker
        assert isinstance(flow._reranker, ModelBasedReranker)

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


class _LLMWithGuard:
    """Duck-typed LLM stub exposing a real BudgetGuard, like BudgetGuardDecorator."""

    def __init__(self, guard) -> None:
        self.budget_guard = guard

    async def generate(self, request):  # pragma: no cover - unused
        raise NotImplementedError

    async def generate_structured(self, request, schema):
        from leggie.application.ports.llm import LLMResponse
        from leggie.domain.models import ModelTier
        return None, LLMResponse(content="", model="stub", tier_used=ModelTier.BUDGET, usage={})

    async def count_tokens(self, text, model=None):  # pragma: no cover
        return len(text) // 4


class TestBudgetCheckpoint:
    @pytest.mark.asyncio
    async def test_checkpoint_written_and_reloaded(self, sample_bill_file, tmp_path):
        from leggie.infrastructure.budget_guard import BudgetGuard

        checkpoint = tmp_path / "run.checkpoint.json"
        guard = BudgetGuard(max_tokens=1000, max_cost=1.0)
        guard.record_usage(prompt_tokens=100, completion_tokens=50, model="google/gemini-2.5-flash")

        flow = BillAnalysisFlow(llm=_LLMWithGuard(guard))
        await flow.run(sample_bill_file, checkpoint_path=checkpoint)

        assert checkpoint.exists()
        saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        assert saved["budget_state"]["tokens_used"] == 150

    @pytest.mark.asyncio
    async def test_checkpoint_restores_prior_spend(self, sample_bill_file, tmp_path):
        from leggie.infrastructure.budget_guard import BudgetGuard

        checkpoint = tmp_path / "run.checkpoint.json"
        checkpoint.write_text(json.dumps({
            "max_tokens": 1000, "max_cost": 1.0,
            "tokens_used": 900, "cost_used": 0.5,
            "degraded": False, "degrade_level": 0,
        }), encoding="utf-8")

        guard = BudgetGuard(max_tokens=1000, max_cost=1.0)
        flow = BillAnalysisFlow(llm=_LLMWithGuard(guard))
        await flow.run(sample_bill_file, checkpoint_path=checkpoint)

        # Prior spend (900) plus whatever this run recorded must exceed the
        # fresh-start baseline of 0 — proves the checkpoint was actually loaded.
        assert guard.remaining_tokens <= 100

    @pytest.mark.asyncio
    async def test_no_checkpoint_path_is_noop(self, sample_bill_file):
        from leggie.infrastructure.budget_guard import BudgetGuard
        guard = BudgetGuard()
        flow = BillAnalysisFlow(llm=_LLMWithGuard(guard))
        findings, reports = await flow.run(sample_bill_file)
        assert flow.state == WorkflowState.DONE


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


class TestResumeAfterCrash:
    """Integration-style crash-resume test (D10)."""

    @pytest.mark.asyncio
    async def test_resume_after_crash(self, sample_bill_file, tmp_path):
        checkpoint = tmp_path / "resume.checkpoint.json"

        # 1. First run: crash after execution completes (checkpoint saved at AGGREGATING).
        flow1 = BillAnalysisFlow()
        original_transition = flow1._transition
        crashed_state: WorkflowState | None = None

        def crashing_transition(target: WorkflowState, event: str) -> None:
            original_transition(target, event)
            nonlocal crashed_state
            if crashed_state is None and flow1.state == WorkflowState.AGGREGATING:
                crashed_state = flow1.state
                raise RuntimeError("simulated crash")

        flow1._transition = crashing_transition
        with pytest.raises(RuntimeError):
            await flow1.run(sample_bill_file, checkpoint_path=checkpoint)

        assert crashed_state == WorkflowState.AGGREGATING
        saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        assert saved["stage"] == WorkflowState.AGGREGATING.value
        assert saved["findings"]
        assert saved["document"]

        # 2. Non-crashing fresh run for comparison.
        fresh_flow = BillAnalysisFlow()
        fresh_findings, _ = await fresh_flow.run(sample_bill_file)

        # 3. Resume with a new flow and count stage executions.
        flow2 = BillAnalysisFlow()
        calls = {"ingest": 0, "parse": 0, "decompose": 0, "analyze": 0}

        orig_do_ingest = flow2._do_ingest
        orig_do_parse = flow2._do_parse
        orig_decompose = flow2._orchestrator.decompose
        orig_analyze_document = flow2._orchestrator.analyze_document

        async def counted_do_ingest(path):
            calls["ingest"] += 1
            return await orig_do_ingest(path)

        def counted_do_parse(text, path):
            calls["parse"] += 1
            return orig_do_parse(text, path)

        def counted_decompose(doc):
            calls["decompose"] += 1
            return orig_decompose(doc)

        async def counted_analyze_document(doc, lenses):
            calls["analyze"] += 1
            return await orig_analyze_document(doc, lenses)

        flow2._do_ingest = counted_do_ingest
        flow2._do_parse = counted_do_parse
        flow2._orchestrator.decompose = counted_decompose
        flow2._orchestrator.analyze_document = counted_analyze_document

        resumed_findings, _ = await flow2.run(sample_bill_file, checkpoint_path=checkpoint)

        # 4. Assertions: skipped expensive stages and identical findings.
        assert calls["ingest"] == 0
        assert calls["parse"] == 0
        assert calls["decompose"] == 0
        assert calls["analyze"] == 0
        assert flow2.state == WorkflowState.DONE
        assert len(resumed_findings) == len(fresh_findings)
        assert sorted(f.irac.issue for f in resumed_findings) == sorted(
            f.irac.issue for f in fresh_findings
        )


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
