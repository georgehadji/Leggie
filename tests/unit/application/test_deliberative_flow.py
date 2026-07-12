"""Tests for DeliberativeFlow — end-to-end against a fake ReasonerPort."""

import pytest

from leggie.application.ports.reasoner import ReasonerPort, ReasonerRequest, ReasonerResult
from leggie.application.workflow.deliberative_flow import DeliberativeFlow
from leggie.domain.models import EventType

SAMPLE_BILL = """
ΣΧΕΔΙΟ ΝΟΜΟΥ
«Δοκιμαστικό νομοσχέδιο»

Άρθρο 1 – Απλή διάταξη
1. Η ισχύς του παρόντος αρχίζει από τη δημοσίευσή του.
"""


@pytest.fixture
def sample_bill_file(tmp_path):
    path = tmp_path / "bill.txt"
    path.write_text(SAMPLE_BILL, encoding="utf-8")
    return path


class RecordingFakeReasoner(ReasonerPort):
    """Fake that records every request it receives, returning canned per-call results."""

    def __init__(self) -> None:
        self.requests: list[ReasonerRequest] = []

    async def reason(self, request: ReasonerRequest) -> ReasonerResult:
        self.requests.append(request)
        call_number = len(self.requests)
        return ReasonerResult(
            synthesis=f"Synthesis for call {call_number} (preset={request.preset})",
            critical_insights=[f"insight-{call_number}-a", f"insight-{call_number}-b"],
            open_questions=[f"question-{call_number}"],
            citations=[],
            models_used=[f"model-{call_number}"],
            total_tokens={
                "prompt_tokens": 100 * call_number,
                "completion_tokens": 50 * call_number,
            },
            duration_seconds=1.0 * call_number,
            errors=[],
        )


class FakeServerManager:
    def __init__(self) -> None:
        self.ensure_running_calls = 0

    async def ensure_running(self) -> None:
        self.ensure_running_calls += 1


class TestDeliberativeFlowRun:
    @pytest.mark.asyncio
    async def test_returns_report_path(self, sample_bill_file, tmp_path):
        reasoner = RecordingFakeReasoner()
        flow = DeliberativeFlow(
            reasoner=reasoner, stage1_preset="preset-1", stage2_preset="preset-2"
        )
        output_dir = tmp_path / "out"
        report_path = await flow.run(sample_bill_file, output_dir=output_dir)
        assert report_path.exists()
        assert report_path.name == "bill_deliberative.md"

    @pytest.mark.asyncio
    async def test_calls_reasoner_twice_with_correct_presets(self, sample_bill_file, tmp_path):
        reasoner = RecordingFakeReasoner()
        flow = DeliberativeFlow(
            reasoner=reasoner, stage1_preset="preset-1", stage2_preset="preset-2"
        )
        await flow.run(sample_bill_file, output_dir=tmp_path / "out")
        assert len(reasoner.requests) == 2
        assert reasoner.requests[0].preset == "preset-1"
        assert reasoner.requests[1].preset == "preset-2"

    @pytest.mark.asyncio
    async def test_stage2_receives_stage1_output(self, sample_bill_file, tmp_path):
        reasoner = RecordingFakeReasoner()
        flow = DeliberativeFlow(
            reasoner=reasoner, stage1_preset="preset-1", stage2_preset="preset-2"
        )
        await flow.run(sample_bill_file, output_dir=tmp_path / "out")
        stage1_synthesis = "Synthesis for call 1 (preset=preset-1)"
        assert stage1_synthesis in reasoner.requests[1].problem

    @pytest.mark.asyncio
    async def test_report_contains_three_sections(self, sample_bill_file, tmp_path):
        reasoner = RecordingFakeReasoner()
        flow = DeliberativeFlow(
            reasoner=reasoner, stage1_preset="preset-1", stage2_preset="preset-2"
        )
        report_path = await flow.run(sample_bill_file, output_dir=tmp_path / "out")
        content = report_path.read_text(encoding="utf-8")
        assert "# Περίληψη" in content
        assert "# Κριτική (Stage 1)" in content
        assert "# Έλεγχος/Audit (Stage 2)" in content

    @pytest.mark.asyncio
    async def test_report_contains_stage_syntheses(self, sample_bill_file, tmp_path):
        reasoner = RecordingFakeReasoner()
        flow = DeliberativeFlow(
            reasoner=reasoner, stage1_preset="preset-1", stage2_preset="preset-2"
        )
        report_path = await flow.run(sample_bill_file, output_dir=tmp_path / "out")
        content = report_path.read_text(encoding="utf-8")
        assert "Synthesis for call 1 (preset=preset-1)" in content
        assert "Synthesis for call 2 (preset=preset-2)" in content

    @pytest.mark.asyncio
    async def test_bill_text_is_passed_to_reasoner(self, sample_bill_file, tmp_path):
        reasoner = RecordingFakeReasoner()
        flow = DeliberativeFlow(
            reasoner=reasoner, stage1_preset="preset-1", stage2_preset="preset-2"
        )
        await flow.run(sample_bill_file, output_dir=tmp_path / "out")
        assert "Δοκιμαστικό νομοσχέδιο" in reasoner.requests[0].problem

    @pytest.mark.asyncio
    async def test_perspective_is_passed_through(self, sample_bill_file, tmp_path):
        reasoner = RecordingFakeReasoner()
        flow = DeliberativeFlow(
            reasoner=reasoner, stage1_preset="preset-1", stage2_preset="preset-2"
        )
        await flow.run(sample_bill_file, output_dir=tmp_path / "out", perspective="neutral")
        # Neutral perspective label should appear in Stage 1's rendered prompt.
        assert "Ουδέτερη ανάλυση" in reasoner.requests[0].problem


class TestDeliberativeFlowEvents:
    @pytest.mark.asyncio
    async def test_records_analysis_started(self, sample_bill_file, tmp_path):
        reasoner = RecordingFakeReasoner()
        flow = DeliberativeFlow(
            reasoner=reasoner, stage1_preset="preset-1", stage2_preset="preset-2"
        )
        await flow.run(sample_bill_file, output_dir=tmp_path / "out")
        events = flow.get_event_log()
        assert any(e.event_type == EventType.ANALYSIS_STARTED for e in events)

    @pytest.mark.asyncio
    async def test_records_two_stage_completed_events(self, sample_bill_file, tmp_path):
        reasoner = RecordingFakeReasoner()
        flow = DeliberativeFlow(
            reasoner=reasoner, stage1_preset="preset-1", stage2_preset="preset-2"
        )
        await flow.run(sample_bill_file, output_dir=tmp_path / "out")
        events = flow.get_event_log()
        stage_events = [e for e in events if e.event_type == EventType.STAGE_COMPLETED]
        assert len(stage_events) == 2
        assert stage_events[0].data["stage"] == 1
        assert stage_events[1].data["stage"] == 2

    @pytest.mark.asyncio
    async def test_stage_events_capture_provenance(self, sample_bill_file, tmp_path):
        reasoner = RecordingFakeReasoner()
        flow = DeliberativeFlow(
            reasoner=reasoner, stage1_preset="preset-1", stage2_preset="preset-2"
        )
        await flow.run(sample_bill_file, output_dir=tmp_path / "out")
        events = flow.get_event_log()
        stage1_event = next(
            e for e in events if e.event_type == EventType.STAGE_COMPLETED and e.data["stage"] == 1
        )
        assert stage1_event.data["preset"] == "preset-1"
        assert stage1_event.data["models_used"] == ["model-1"]
        assert stage1_event.data["total_tokens"] == {"prompt_tokens": 100, "completion_tokens": 50}
        assert "synthesis" in stage1_event.data

    @pytest.mark.asyncio
    async def test_records_workflow_completed_with_report_path(self, sample_bill_file, tmp_path):
        reasoner = RecordingFakeReasoner()
        flow = DeliberativeFlow(
            reasoner=reasoner, stage1_preset="preset-1", stage2_preset="preset-2"
        )
        report_path = await flow.run(sample_bill_file, output_dir=tmp_path / "out")
        events = flow.get_event_log()
        completed = next(e for e in events if e.event_type == EventType.WORKFLOW_COMPLETED)
        assert completed.data["report_path"] == str(report_path)

    @pytest.mark.asyncio
    async def test_event_log_is_replayable_snapshot(self, sample_bill_file, tmp_path):
        """get_event_log returns a copy — mutating it must not affect the flow's state."""
        reasoner = RecordingFakeReasoner()
        flow = DeliberativeFlow(
            reasoner=reasoner, stage1_preset="preset-1", stage2_preset="preset-2"
        )
        await flow.run(sample_bill_file, output_dir=tmp_path / "out")
        events = flow.get_event_log()
        events.clear()
        assert len(flow.get_event_log()) > 0


class TestDeliberativeFlowServerLifecycle:
    @pytest.mark.asyncio
    async def test_ensure_running_called_when_manager_provided(self, sample_bill_file, tmp_path):
        reasoner = RecordingFakeReasoner()
        manager = FakeServerManager()
        flow = DeliberativeFlow(
            reasoner=reasoner,
            stage1_preset="preset-1",
            stage2_preset="preset-2",
            server_manager=manager,
        )
        await flow.run(sample_bill_file, output_dir=tmp_path / "out")
        assert manager.ensure_running_calls == 1

    @pytest.mark.asyncio
    async def test_no_server_manager_skips_lifecycle_check(self, sample_bill_file, tmp_path):
        reasoner = RecordingFakeReasoner()
        flow = DeliberativeFlow(
            reasoner=reasoner, stage1_preset="preset-1", stage2_preset="preset-2"
        )
        # Should not raise even without a server manager.
        report_path = await flow.run(sample_bill_file, output_dir=tmp_path / "out")
        assert report_path.exists()
