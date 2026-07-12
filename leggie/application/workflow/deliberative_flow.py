"""DeliberativeFlow — two-stage, Reasoner-backed deliberative analysis workflow.

Stage 1 (Prompt01): structured report + party-perspective evaluation.
Stage 2 (Prompt02): adversarial audit of Stage 1's output against the bill.

Output is persisted as a prose Markdown report (Decision B) — no Finding
mapping, no Skeptic/CoVe pass. This is a sibling to BillAnalysisFlow, not a
replacement: the deterministic `analyze` pipeline is untouched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from leggie.application.ports.citation_parser import CitationParserPort
from leggie.application.ports.ingest import IngestPort
from leggie.application.ports.parse import ParsePort
from leggie.application.ports.reasoner import ReasonerPort, ReasonerRequest, ReasonerResult
from leggie.application.services.deliberative_prompts import DeliberativePromptRenderer
from leggie.application.workflow.ingest_parse import lazy_ingest_adapter, lazy_parse_adapter
from leggie.domain.models import Event, EventType

_CHARS_PER_TOKEN = 4  # rough heuristic, consistent with LLMAdapter.count_tokens


class ServerLifecycle(Protocol):
    """Minimal capability DeliberativeFlow needs from a server manager.

    Kept as a Protocol (not the concrete ReasonerServerManager) so this
    application-layer module never imports infrastructure.
    """

    async def ensure_running(self) -> None: ...


class DeliberativeBudgetExceededError(Exception):
    """Raised when the pre-flight token estimate exceeds the configured budget."""


class DeliberativeFlow:
    """Orchestrates the two-stage deliberative pipeline and persists a prose report."""

    def __init__(
        self,
        reasoner: ReasonerPort,
        stage1_preset: str,
        stage2_preset: str,
        server_manager: ServerLifecycle | None = None,
        ingester: IngestPort | None = None,
        parser: ParsePort | None = None,
        prompt_renderer: DeliberativePromptRenderer | None = None,
        citation_parser: CitationParserPort | None = None,
        max_tokens_per_run: int | None = None,
    ) -> None:
        self._reasoner = reasoner
        self._stage1_preset = stage1_preset
        self._stage2_preset = stage2_preset
        self._server_manager = server_manager
        self._ingester = ingester or lazy_ingest_adapter()
        self._parser = parser or lazy_parse_adapter()
        self._prompts = prompt_renderer or DeliberativePromptRenderer()
        self._citation_parser = citation_parser
        self._max_tokens_per_run = max_tokens_per_run
        self._events: list[Event] = []

    async def run(
        self,
        file_path: str | Path,
        output_dir: str | Path = "Outputs",
        perspective: str = "neutral",
    ) -> Path:
        """Run the two-stage deliberative pipeline on a bill file.

        Returns the path to the saved prose report. Raises
        ReasonerUnavailableError (propagated, uncaught) if the Reasoner
        backend cannot be reached — callers decide abort-vs-fallback.
        """
        file_path = Path(file_path)
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        bill_name = file_path.stem

        if self._server_manager is not None:
            await self._server_manager.ensure_running()

        text = await self._ingester.ingest(file_path)
        doc = self._parser.parse(
            text, title=file_path.stem, source_format=file_path.suffix.lstrip(".")
        )
        bill_text = doc.raw_text or text

        self._record_event(
            EventType.ANALYSIS_STARTED,
            {"file": str(file_path), "pipeline": "deliberative", "perspective": perspective},
        )

        self._check_budget(bill_text)

        stage1_result = await self._run_stage1(bill_text, perspective)
        stage2_result = await self._run_stage2(bill_text, stage1_result.synthesis)

        report = self._assemble_report(stage1_result, stage2_result)
        report += self._citation_appendix(report)
        report_path = output_path / f"{bill_name}_deliberative.md"
        report_path.write_text(report, encoding="utf-8")

        self._record_event(
            EventType.WORKFLOW_COMPLETED,
            {"pipeline": "deliberative", "report_path": str(report_path)},
        )
        return report_path

    async def _run_stage1(self, bill_text: str, perspective: str) -> ReasonerResult:
        prompt = self._prompts.render_stage1(bill_text, perspective=perspective)
        result = await self._reasoner.reason(
            ReasonerRequest(problem=prompt, preset=self._stage1_preset)
        )
        self._record_stage_event(1, self._stage1_preset, result)
        return result

    async def _run_stage2(self, bill_text: str, prior_synthesis: str) -> ReasonerResult:
        prompt = self._prompts.render_stage2(bill_text, prior_synthesis)
        result = await self._reasoner.reason(
            ReasonerRequest(problem=prompt, preset=self._stage2_preset)
        )
        self._record_stage_event(2, self._stage2_preset, result)
        return result

    def _record_stage_event(self, stage: int, preset: str, result: ReasonerResult) -> None:
        self._record_event(
            EventType.STAGE_COMPLETED,
            {
                "stage": stage,
                "preset": preset,
                "models_used": result.models_used,
                "total_tokens": result.total_tokens,
                "duration_seconds": result.duration_seconds,
                "synthesis": result.synthesis,
                "errors": result.errors,
            },
        )

    def _assemble_report(self, stage1: ReasonerResult, stage2: ReasonerResult) -> str:
        sections = [
            "# Περίληψη",
            "",
            self._format_list("Κρίσιμες παρατηρήσεις", stage1.critical_insights)
            or "*(Δεν παρήχθησαν συνοπτικές παρατηρήσεις.)*",
            "",
            "# Κριτική (Stage 1)",
            "",
            stage1.synthesis,
            self._format_list("Ανοιχτά ερωτήματα", stage1.open_questions),
            "",
            "# Έλεγχος/Audit (Stage 2)",
            "",
            stage2.synthesis,
            self._format_list("Κρίσιμες παρατηρήσεις ελέγχου", stage2.critical_insights),
            self._format_list("Ανοιχτά ερωτήματα ελέγχου", stage2.open_questions),
        ]
        return "\n".join(s for s in sections if s is not None)

    @staticmethod
    def _format_list(title: str, items: list[str]) -> str:
        if not items:
            return ""
        lines = "\n".join(f"- {item}" for item in items)
        return f"\n**{title}:**\n{lines}"

    def _check_budget(self, bill_text: str) -> None:
        """Pre-flight token estimate — two stages each resend bill_text (+ Stage 1
        output as Stage 2's prior_report), so estimate ~3x the bill's token count."""
        if self._max_tokens_per_run is None:
            return
        estimated_tokens = (len(bill_text) // _CHARS_PER_TOKEN) * 3
        if estimated_tokens > self._max_tokens_per_run:
            self._record_event(
                EventType.BUDGET_TRIPPED,
                {
                    "estimated_tokens": estimated_tokens,
                    "max_tokens_per_run": self._max_tokens_per_run,
                },
            )
            raise DeliberativeBudgetExceededError(
                f"Estimated {estimated_tokens} tokens exceeds the configured budget of "
                f"{self._max_tokens_per_run} (LEGGIE_BUDGET__MAX_TOKENS_PER_RUN). "
                "Increase the budget or analyze a smaller bill."
            )

    def _citation_appendix(self, report: str) -> str:
        """Optional appendix: run the deterministic citation parser over the prose
        report and list every citation as unverified (deliberative path skips CoVe)."""
        if self._citation_parser is None:
            return ""
        citations = self._citation_parser.parse(report)
        if not citations:
            return ""
        lines = "\n".join(f"- {c.original_text}" for c in citations)
        return f"\n\n# Παράρτημα: Μη-επαληθευμένες παραπομπές\n\n{lines}\n"

    def _record_event(self, event_type: EventType, data: dict) -> None:
        self._events.append(
            Event(event_type=event_type, aggregate_id=f"deliberative-{id(self)}", data=data)
        )

    def get_event_log(self) -> list[Event]:
        """Get the full event log for this run — replayable audit trail."""
        return list(self._events)
