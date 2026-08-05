"""Tests for DeliberativeFlow — end-to-end against a fake ReasonerPort."""

import json

import pytest

from leggie.application.ports.citation_parser import CitationParserPort
from leggie.application.ports.reasoner import ReasonerPort, ReasonerRequest, ReasonerResult
from leggie.application.workflow.deliberative_flow import (
    DeliberativeBudgetExceededError,
    DeliberativeFlow,
)
from leggie.domain.models import Citation, CitationScheme, EventType

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


class FakeCitationParser(CitationParserPort):
    def __init__(self, citations: list[Citation] | None = None) -> None:
        self._citations = citations or []
        self.parse_calls: list[str] = []

    def parse(self, text: str) -> list[Citation]:
        self.parse_calls.append(text)
        return self._citations

    async def resolve(self, citation: Citation) -> Citation:
        return citation

    def supported_schemes(self) -> list[CitationScheme]:
        return [CitationScheme.FEK]


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


class TestDeliberativeFlowBudget:
    @pytest.mark.asyncio
    async def test_no_budget_configured_never_raises(self, sample_bill_file, tmp_path):
        reasoner = RecordingFakeReasoner()
        flow = DeliberativeFlow(
            reasoner=reasoner,
            stage1_preset="preset-1",
            stage2_preset="preset-2",
            max_tokens_per_run=None,
        )
        report_path = await flow.run(sample_bill_file, output_dir=tmp_path / "out")
        assert report_path.exists()

    @pytest.mark.asyncio
    async def test_estimate_within_budget_proceeds(self, sample_bill_file, tmp_path):
        reasoner = RecordingFakeReasoner()
        flow = DeliberativeFlow(
            reasoner=reasoner,
            stage1_preset="preset-1",
            stage2_preset="preset-2",
            max_tokens_per_run=1_000_000,
        )
        report_path = await flow.run(sample_bill_file, output_dir=tmp_path / "out")
        assert report_path.exists()
        assert len(reasoner.requests) == 2

    @pytest.mark.asyncio
    async def test_estimate_over_budget_raises_before_any_reasoner_call(
        self, sample_bill_file, tmp_path
    ):
        reasoner = RecordingFakeReasoner()
        flow = DeliberativeFlow(
            reasoner=reasoner,
            stage1_preset="preset-1",
            stage2_preset="preset-2",
            max_tokens_per_run=1,
        )
        with pytest.raises(DeliberativeBudgetExceededError, match="exceeds the configured budget"):
            await flow.run(sample_bill_file, output_dir=tmp_path / "out")
        assert len(reasoner.requests) == 0

    @pytest.mark.asyncio
    async def test_over_budget_records_budget_tripped_event(self, sample_bill_file, tmp_path):
        reasoner = RecordingFakeReasoner()
        flow = DeliberativeFlow(
            reasoner=reasoner,
            stage1_preset="preset-1",
            stage2_preset="preset-2",
            max_tokens_per_run=1,
        )
        with pytest.raises(DeliberativeBudgetExceededError):
            await flow.run(sample_bill_file, output_dir=tmp_path / "out")
        events = flow.get_event_log()
        assert any(e.event_type == EventType.BUDGET_TRIPPED for e in events)


class FakeCheckpointStore:
    """In-memory checkpoint store with a JSON round-trip to catch non-serializable data."""

    def __init__(self) -> None:
        self._data: dict | None = None
        self.saves = 0

    def save(self, data: dict) -> None:
        self.saves += 1
        self._data = json.loads(json.dumps(data))  # emulate on-disk JSON persistence

    def load(self) -> dict | None:
        return self._data

    def delete(self) -> None:
        self._data = None


class Stage2FailingReasoner(ReasonerPort):
    """Succeeds on Stage 1, raises on Stage 2 — to leave a Stage-1 checkpoint behind."""

    def __init__(self) -> None:
        self.calls = 0

    async def reason(self, request: ReasonerRequest) -> ReasonerResult:
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("stage2 boom")
        return ReasonerResult(
            synthesis="STAGE1 SYNTHESIS TEXT",
            critical_insights=["ci-1"],
            open_questions=[],
            citations=[
                Citation(
                    scheme=CitationScheme.FEK,
                    identifier="FEK/2024/1",
                    original_text="ΦΕΚ Α 1/2024",
                )
            ],
            models_used=["model-1"],
            total_tokens={"prompt_tokens": 10, "completion_tokens": 5},
            duration_seconds=1.0,
            errors=[],
        )


class TestDeliberativeFlowIdempotency:
    @pytest.mark.asyncio
    async def test_each_stage_carries_a_stable_client_run_id(self, sample_bill_file, tmp_path):
        reasoner = RecordingFakeReasoner()
        flow = DeliberativeFlow(
            reasoner=reasoner, stage1_preset="preset-1", stage2_preset="preset-2"
        )
        await flow.run(sample_bill_file, output_dir=tmp_path / "out")

        rid1 = reasoner.requests[0].client_run_id
        rid2 = reasoner.requests[1].client_run_id
        assert rid1 and rid1.endswith("-stage1")
        assert rid2 and rid2.endswith("-stage2")
        # Both stages share the same run prefix (so Reasoner sees one job).
        assert rid1[: -len("-stage1")] == rid2[: -len("-stage2")]


class TestDeliberativeFlowNikiPerspective:
    @pytest.mark.asyncio
    async def test_niki_perspective_label_renders_into_stage1(self, sample_bill_file, tmp_path):
        reasoner = RecordingFakeReasoner()
        flow = DeliberativeFlow(
            reasoner=reasoner, stage1_preset="preset-1", stage2_preset="preset-2"
        )
        await flow.run(sample_bill_file, output_dir=tmp_path / "out", perspective="niki")
        assert "ΝΙΚΗ" in reasoner.requests[0].problem


class TestDeliberativeFlowCheckpoint:
    @pytest.mark.asyncio
    async def test_checkpoint_deleted_on_successful_completion(self, sample_bill_file, tmp_path):
        reasoner = RecordingFakeReasoner()
        store = FakeCheckpointStore()
        flow = DeliberativeFlow(
            reasoner=reasoner,
            stage1_preset="preset-1",
            stage2_preset="preset-2",
            checkpoint_store=store,
        )
        await flow.run(sample_bill_file, output_dir=tmp_path / "out")
        assert store.saves >= 1  # Stage 1 was checkpointed
        assert store.load() is None  # cleaned up on completion

    @pytest.mark.asyncio
    async def test_resume_skips_stage1_and_reuses_persisted_result(
        self, sample_bill_file, tmp_path
    ):
        store = FakeCheckpointStore()

        # First run fails in Stage 2, leaving a Stage-1 checkpoint behind.
        failing = Stage2FailingReasoner()
        flow1 = DeliberativeFlow(
            reasoner=failing,
            stage1_preset="preset-1",
            stage2_preset="preset-2",
            checkpoint_store=store,
        )
        with pytest.raises(RuntimeError, match="stage2 boom"):
            await flow1.run(sample_bill_file, output_dir=tmp_path / "out")
        assert store.load() is not None  # checkpoint survived the failure

        # Second run resumes: Stage 1 is NOT re-billed, only Stage 2 runs.
        reasoner = RecordingFakeReasoner()
        flow2 = DeliberativeFlow(
            reasoner=reasoner,
            stage1_preset="preset-1",
            stage2_preset="preset-2",
            checkpoint_store=store,
        )
        report_path = await flow2.run(sample_bill_file, output_dir=tmp_path / "out")

        assert len(reasoner.requests) == 1  # only Stage 2 called
        assert reasoner.requests[0].preset == "preset-2"
        assert "STAGE1 SYNTHESIS TEXT" in reasoner.requests[0].problem  # resumed Stage-1 output
        assert report_path.exists()
        assert store.load() is None  # cleaned up after successful completion

    @pytest.mark.asyncio
    async def test_checkpoint_ignored_for_different_file(self, sample_bill_file, tmp_path):
        store = FakeCheckpointStore()
        # Seed a checkpoint that belongs to a different file.
        store.save(
            {
                "marker": "stage1_completed",
                "file_path": str(tmp_path / "other_bill.txt"),
                "perspective": "neutral",
                "stage1_result": {"synthesis": "unrelated"},
            }
        )
        reasoner = RecordingFakeReasoner()
        flow = DeliberativeFlow(
            reasoner=reasoner,
            stage1_preset="preset-1",
            stage2_preset="preset-2",
            checkpoint_store=store,
        )
        await flow.run(sample_bill_file, output_dir=tmp_path / "out")
        # Mismatched checkpoint ignored → both stages run.
        assert len(reasoner.requests) == 2


class TestDeliberativeFlowCitationAppendix:
    @pytest.mark.asyncio
    async def test_no_citation_parser_omits_appendix(self, sample_bill_file, tmp_path):
        reasoner = RecordingFakeReasoner()
        flow = DeliberativeFlow(
            reasoner=reasoner, stage1_preset="preset-1", stage2_preset="preset-2"
        )
        report_path = await flow.run(sample_bill_file, output_dir=tmp_path / "out")
        content = report_path.read_text(encoding="utf-8")
        assert "Παράρτημα" not in content

    @pytest.mark.asyncio
    async def test_citation_parser_with_no_matches_omits_appendix(
        self, sample_bill_file, tmp_path
    ):
        reasoner = RecordingFakeReasoner()
        citation_parser = FakeCitationParser(citations=[])
        flow = DeliberativeFlow(
            reasoner=reasoner,
            stage1_preset="preset-1",
            stage2_preset="preset-2",
            citation_parser=citation_parser,
        )
        report_path = await flow.run(sample_bill_file, output_dir=tmp_path / "out")
        content = report_path.read_text(encoding="utf-8")
        assert "Παράρτημα" not in content
        assert len(citation_parser.parse_calls) == 1

    @pytest.mark.asyncio
    async def test_citation_parser_with_matches_appends_section(
        self, sample_bill_file, tmp_path
    ):
        reasoner = RecordingFakeReasoner()
        citation_parser = FakeCitationParser(
            citations=[
                Citation(
                    scheme=CitationScheme.FEK,
                    identifier="FEK/2024/1",
                    original_text="ΦΕΚ Α 1/2024",
                )
            ]
        )
        flow = DeliberativeFlow(
            reasoner=reasoner,
            stage1_preset="preset-1",
            stage2_preset="preset-2",
            citation_parser=citation_parser,
        )
        report_path = await flow.run(sample_bill_file, output_dir=tmp_path / "out")
        content = report_path.read_text(encoding="utf-8")
        assert "# Παράρτημα: Μη-επαληθευμένες παραπομπές" in content
        assert "ΦΕΚ Α 1/2024" in content
