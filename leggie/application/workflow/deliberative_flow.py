"""DeliberativeFlow — two-stage, Reasoner-backed deliberative analysis workflow.

Stage 1 (Prompt01): structured report + party-perspective evaluation.
Stage 2 (Prompt02): adversarial audit of Stage 1's output against the bill.

Output is persisted as a prose Markdown report (Decision B) — no Finding
mapping, no Skeptic/CoVe pass. This is a sibling to BillAnalysisFlow, not a
replacement: the deterministic `analyze` pipeline is untouched.

Robustness (added with the default-pipeline promotion):
  - Idempotent retries: each Reasoner call carries a stable ``client_run_id`` so
    a timeout-retry is recognized as the same billable job, not a new one.
  - Stage-1↔Stage-2 checkpoint/resume: Stage 1 (the expensive fan-out) is
    persisted so a Stage-2 failure does not re-bill Stage 1. The store is typed
    against a local Protocol so this application module never imports
    infrastructure (same tactic as ``ServerLifecycle``).
  - Per-preset token pre-flight and a large-bill signal for observability.
"""

from __future__ import annotations

import contextlib
import uuid
from pathlib import Path
from typing import Any, Protocol

from leggie.application.ports.citation_parser import CitationParserPort
from leggie.application.ports.ingest import IngestPort
from leggie.application.ports.parse import ParsePort
from leggie.application.ports.reasoner import ReasonerPort, ReasonerRequest, ReasonerResult
from leggie.application.services.deliberative_prompts import DeliberativePromptRenderer
from leggie.application.workflow.ingest_parse import lazy_ingest_adapter, lazy_parse_adapter
from leggie.domain.models import Citation, Event, EventType

_CHARS_PER_TOKEN = 4  # rough heuristic, consistent with LLMAdapter.count_tokens

# Per-preset token-usage multipliers relative to the bill's own token count.
# Premium presets fan out across multiple models, so they resend/expand the
# input several times over; unknown presets fall back to a conservative default
# that reproduces the historical flat "x3" estimate (1.5 + 1.5) for two stages.
_PRESET_TOKEN_MULTIPLIERS: dict[str, float] = {
    "multi-perspective-premium": 3.0,
    "subagent-premium": 2.0,
}
_DEFAULT_STAGE_MULTIPLIER = 1.5

# Soft ceiling (in estimated tokens) above which we emit an observability signal
# that the bill is large enough to risk a model context-window overflow inside
# Reasoner. Purely advisory — does not abort the run.
_LARGE_BILL_TOKEN_THRESHOLD = 150_000

_CHECKPOINT_STAGE1_MARKER = "stage1_completed"


class ServerLifecycle(Protocol):
    """Minimal capability DeliberativeFlow needs from a server manager.

    Kept as a Protocol (not the concrete ReasonerServerManager) so this
    application-layer module never imports infrastructure.
    """

    async def ensure_running(self) -> None: ...


class CheckpointStore(Protocol):
    """Minimal capability DeliberativeFlow needs from a checkpoint store.

    Structural (duck-typed) so the concrete
    ``leggie.infrastructure.persistence.checkpoint_store.CheckpointStore``
    satisfies it without this application module importing infrastructure.
    """

    def save(self, data: dict[str, Any]) -> None: ...
    def load(self) -> dict[str, Any] | None: ...
    def delete(self) -> None: ...


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
        checkpoint_store: CheckpointStore | None = None,
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
        self._checkpoint_store = checkpoint_store
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
        from leggie.infrastructure.observability import bind_trace_id, get_logger, set_trace_id

        file_path = Path(file_path)
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        bill_name = file_path.stem

        run_id = str(uuid.uuid4())
        set_trace_id(run_id)
        logger = bind_trace_id(get_logger(__name__))

        if self._server_manager is not None:
            await self._server_manager.ensure_running()

        text = await self._ingester.ingest(file_path)
        doc = self._parser.parse(
            text, title=file_path.stem, source_format=file_path.suffix.lstrip(".")
        )
        bill_text = doc.raw_text or text

        self._record_event(
            EventType.ANALYSIS_STARTED,
            {
                "file": str(file_path),
                "pipeline": "deliberative",
                "perspective": perspective,
                "run_id": run_id,
            },
        )

        self._check_budget(bill_text)
        self._warn_if_large(bill_text, logger)

        # Resume Stage 1 from a compatible checkpoint if one exists; otherwise run
        # Stage 1 and persist it so a later Stage-2 failure never re-bills Stage 1.
        stage1_result = self._load_stage1_checkpoint(file_path, perspective)
        if stage1_result is not None:
            logger.info("deliberative.resumed_from_checkpoint", stage=1, run_id=run_id)
        else:
            stage1_result = await self._run_stage1(bill_text, perspective, run_id)
            self._save_stage1_checkpoint(file_path, perspective, run_id, stage1_result)

        stage2_result = await self._run_stage2(bill_text, stage1_result.synthesis, run_id)

        report = self._assemble_report(stage1_result, stage2_result)
        report += self._citation_appendix(report)
        report_path = output_path / f"{bill_name}_deliberative.md"
        report_path.write_text(report, encoding="utf-8")

        self._delete_checkpoint()

        self._record_event(
            EventType.WORKFLOW_COMPLETED,
            {"pipeline": "deliberative", "report_path": str(report_path)},
        )
        self._log_telemetry(logger, run_id, report_path)
        return report_path

    async def _run_stage1(self, bill_text: str, perspective: str, run_id: str) -> ReasonerResult:
        prompt = self._prompts.render_stage1(bill_text, perspective=perspective)
        result = await self._reasoner.reason(
            ReasonerRequest(
                problem=prompt,
                preset=self._stage1_preset,
                client_run_id=f"{run_id}-stage1",
            )
        )
        self._record_stage_event(1, self._stage1_preset, result)
        return result

    async def _run_stage2(
        self, bill_text: str, prior_synthesis: str, run_id: str
    ) -> ReasonerResult:
        prompt = self._prompts.render_stage2(bill_text, prior_synthesis)
        result = await self._reasoner.reason(
            ReasonerRequest(
                problem=prompt,
                preset=self._stage2_preset,
                client_run_id=f"{run_id}-stage2",
            )
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

    def _stage_multiplier(self, preset: str) -> float:
        """Estimated token blow-up for a stage, relative to the bill's tokens."""
        return _PRESET_TOKEN_MULTIPLIERS.get(preset, _DEFAULT_STAGE_MULTIPLIER)

    def _estimate_tokens(self, bill_text: str) -> int:
        """Pre-flight token estimate across both stages.

        Each stage resends the bill (Stage 2 additionally carries Stage 1's
        output, itself roughly bill-sized). Premium presets fan out across
        several models, so the per-stage multiplier is preset-dependent rather
        than a single flat factor.
        """
        bill_tokens = len(bill_text) // _CHARS_PER_TOKEN
        multiplier = self._stage_multiplier(self._stage1_preset) + self._stage_multiplier(
            self._stage2_preset
        )
        return int(bill_tokens * multiplier)

    def _check_budget(self, bill_text: str) -> None:
        """Pre-flight token estimate vs the configured per-run budget."""
        if self._max_tokens_per_run is None:
            return
        estimated_tokens = self._estimate_tokens(bill_text)
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

    def _warn_if_large(self, bill_text: str, logger: Any) -> None:
        """Emit an observability signal when the bill is large enough to risk a
        model context-window overflow inside Reasoner. Advisory only."""
        estimated_tokens = self._estimate_tokens(bill_text)
        if estimated_tokens >= _LARGE_BILL_TOKEN_THRESHOLD:
            logger.warning(
                "deliberative.large_bill",
                estimated_tokens=estimated_tokens,
                threshold=_LARGE_BILL_TOKEN_THRESHOLD,
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

    # ── Checkpoint (Stage-1 resume) ────────────────────────────────────────

    def _load_stage1_checkpoint(
        self, file_path: Path, perspective: str
    ) -> ReasonerResult | None:
        """Restore a Stage-1 result from a compatible checkpoint, else None.

        A checkpoint is only reused when it belongs to the same input file and
        perspective — anything else starts fresh.
        """
        if self._checkpoint_store is None:
            return None
        data = self._checkpoint_store.load()
        if not data or data.get("marker") != _CHECKPOINT_STAGE1_MARKER:
            return None
        if data.get("file_path") != str(file_path) or data.get("perspective") != perspective:
            return None
        raw = data.get("stage1_result")
        if not raw:
            return None
        try:
            return self._deserialize_result(raw)
        except (TypeError, ValueError, KeyError):
            # Corrupt/incompatible checkpoint — start fresh rather than crash.
            return None

    def _save_stage1_checkpoint(
        self, file_path: Path, perspective: str, run_id: str, stage1_result: ReasonerResult
    ) -> None:
        if self._checkpoint_store is None:
            return
        data = {
            "marker": _CHECKPOINT_STAGE1_MARKER,
            "run_id": run_id,
            "file_path": str(file_path),
            "perspective": perspective,
            "stage1_result": self._serialize_result(stage1_result),
        }
        with contextlib.suppress(OSError):
            # Checkpointing is best-effort; never fail the run over it.
            self._checkpoint_store.save(data)

    def _delete_checkpoint(self) -> None:
        if self._checkpoint_store is None:
            return
        with contextlib.suppress(OSError):
            self._checkpoint_store.delete()

    @staticmethod
    def _serialize_result(result: ReasonerResult) -> dict[str, Any]:
        return {
            "synthesis": result.synthesis,
            "critical_insights": list(result.critical_insights),
            "open_questions": list(result.open_questions),
            "citations": [c.model_dump(mode="json") for c in result.citations],
            "models_used": list(result.models_used),
            "total_tokens": dict(result.total_tokens),
            "duration_seconds": result.duration_seconds,
            "errors": list(result.errors),
        }

    @staticmethod
    def _deserialize_result(data: dict[str, Any]) -> ReasonerResult:
        return ReasonerResult(
            synthesis=data.get("synthesis", ""),
            critical_insights=list(data.get("critical_insights", [])),
            open_questions=list(data.get("open_questions", [])),
            citations=[Citation(**c) for c in data.get("citations", [])],
            models_used=list(data.get("models_used", [])),
            total_tokens=dict(data.get("total_tokens", {})),
            duration_seconds=float(data.get("duration_seconds", 0.0)),
            errors=list(data.get("errors", [])),
        )

    def _log_telemetry(self, logger: Any, run_id: str, report_path: Path) -> None:
        """Surface per-stage cost/usage captured in the event log — the deliberative
        path is billable, so its spend should not be silently discarded."""
        stage_events = [
            e for e in self._events if e.event_type == EventType.STAGE_COMPLETED
        ]
        total_tokens = 0
        models: list[str] = []
        for e in stage_events:
            total_tokens += sum(int(v) for v in e.data.get("total_tokens", {}).values())
            models.extend(e.data.get("models_used", []))
        logger.info(
            "deliberative.completed",
            run_id=run_id,
            report_path=str(report_path),
            stages=len(stage_events),
            total_tokens=total_tokens,
            models_used=sorted(set(models)),
        )

    def _record_event(self, event_type: EventType, data: dict[str, Any]) -> None:
        self._events.append(
            Event(event_type=event_type, aggregate_id=f"deliberative-{id(self)}", data=data)
        )

    def get_event_log(self) -> list[Event]:
        """Get the full event log for this run — replayable audit trail."""
        return list(self._events)
