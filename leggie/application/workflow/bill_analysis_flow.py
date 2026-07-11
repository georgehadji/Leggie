"""BillAnalysisFlow — end-to-end bill analysis workflow.

Orchestrates the full analysis pipeline using the FlowStateMachine for
state transitions and Stage lifecycle for each phase.

Phase 1: ingest → parse → analyze (Constitutional lens) → report.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from leggie.application.agents.improver import ImprovementEngine, Suggestion
from leggie.application.agents.orchestrator import Orchestrator
from leggie.application.agents.skeptic import CalibratedSkeptic
from leggie.application.services.bill_overview import BillOverviewGenerator
from leggie.application.services.cove_verifier import CoVeVerifier
from leggie.application.services.reports import (
    ArticleByArticleRenderer,
    ExecutiveSummaryRenderer,
    Report,
)
from leggie.application.services.rerank import CompositeReranker
from leggie.application.workflow.flow_state_machine import FlowStateMachine
from leggie.domain.models import (
    BillOverview,
    Document,
    Event,
    EventType,
    Finding,
    WorkflowState,
)

if TYPE_CHECKING:
    from leggie.application.ports.ingest import IngestPort
    from leggie.application.ports.llm import LLMPort
    from leggie.application.ports.parse import ParsePort


def _lazy_ingest_adapter() -> IngestPort:
    """Lazy factory for IngestAdapter to avoid top-level infra import."""
    from leggie.infrastructure.ingest_adapter import IngestAdapter
    return IngestAdapter()


def _lazy_parse_adapter() -> ParsePort:
    """Lazy factory for ParseAdapter to avoid top-level infra import."""
    from leggie.infrastructure.parse_adapter import ParseAdapter
    return ParseAdapter()


class BillAnalysisFlow:
    """End-to-end bill analysis workflow.

    Manages run lifecycle: tracks state via FlowStateMachine, dispatches
    through stages, and records events for auditability.

    Phase 2: parallel lens execution + reranking.
    """

    def __init__(
        self,
        orchestrator: Orchestrator | None = None,
        skeptic: CalibratedSkeptic | None = None,
        cove: CoVeVerifier | None = None,
        llm: LLMPort | None = None,
        ingester: IngestPort | None = None,
        parser: ParsePort | None = None,
    ) -> None:
        self._fsm = FlowStateMachine()
        self._state = WorkflowState.IDLE
        self._llm = llm
        self._orchestrator = orchestrator or Orchestrator(llm=llm)
        self._reranker = CompositeReranker()
        self._skeptic = skeptic or CalibratedSkeptic()
        self._cove = cove or CoVeVerifier()
        self._improver = ImprovementEngine()
        self._overview_generator = BillOverviewGenerator(llm=llm)
        self._ingester = ingester or _lazy_ingest_adapter()
        self._parser = parser or _lazy_parse_adapter()
        self._reports: list[Report] = []
        self._suggestions: list[Suggestion] = []
        self._events: list[Event] = []
        self._findings: list[Finding] = []
        self._doc: Document | None = None
        self._overview: BillOverview | None = None

    @property
    def state(self) -> WorkflowState:
        return self._state

    @property
    def findings(self) -> list[Finding]:
        return list(self._findings)

    @property
    def overview(self) -> BillOverview | None:
        """The bill overview produced by `preview()`, if it was called."""
        return self._overview

    async def preview(self, file_path: str | Path) -> BillOverview:
        """Stage 0 — ingest + parse, then generate a bill overview.

        Runs before the deep multi-lens analysis: produces a short intro,
        an overall summary, and per-article (Άρθρο) purpose / key
        provisions / practical consequences. Use the resulting article
        IDs to decide what to pass as `selected_article_ids` to `run()`.
        """
        file_path = Path(file_path)
        self._transition(WorkflowState.PREVIEWING, "preview_started")
        self._transition(WorkflowState.INGESTING, "ingest_started")
        text = await self._do_ingest(file_path)
        self._transition(WorkflowState.PARSING, "ingest_completed")
        self._doc = self._do_parse(text, file_path)

        overview = await self._overview_generator.generate(self._doc)
        self._overview = overview
        self._record_event(EventType.OVERVIEW_GENERATED, {
            "articles": len(self._doc.articles),
        })
        return overview

    async def run(
        self,
        file_path: str | Path,
        output_dir: str | Path = "Outputs",
        selected_article_ids: list[str] | None = None,
    ) -> tuple[list[Finding], list[Report]]:
        """Run the full analysis workflow on a bill file.

        Args:
            file_path: Path to the bill file (PDF/DOCX/HTML/TXT).
            output_dir: Directory to save reports and findings. Defaults to "Outputs".
            selected_article_ids: If given, restrict analysis to these Άρθρο
                IDs (e.g. the ones picked after reviewing `preview()`'s
                BillOverview). If `preview()` was already called for this
                file, its ingest/parse result is reused instead of redone.

        Returns:
            (findings, reports) tuple.
        """
        import json
        import uuid

        from leggie.infrastructure.observability import bind_trace_id, get_logger, set_trace_id
        file_path = Path(file_path)
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        bill_name = file_path.stem
        trace_id = str(uuid.uuid4())
        set_trace_id(trace_id)
        logger = bind_trace_id(get_logger(__name__))
        logger.info("flow.started", bill_path=str(file_path), trace_id=trace_id)

        if self._doc is None:
            # 1. Ingest
            self._transition(WorkflowState.INGESTING, "ingest_started")
            text = await self._do_ingest(file_path)
            self._record_event(
                EventType.ANALYSIS_STARTED, {"file": str(file_path), "size": len(text)}
            )
            self._transition(WorkflowState.PARSING, "ingest_completed")

            # 2. Parse
            self._doc = self._do_parse(text, file_path)
        else:
            # preview() already ingested + parsed this file — reuse it.
            self._record_event(EventType.ANALYSIS_STARTED, {
                "file": str(file_path), "size": len(self._doc.raw_text)
            })

        if selected_article_ids is not None:
            selected = [a for a in self._doc.articles if a.id in selected_article_ids]
            self._doc = self._doc.model_copy(update={"articles": selected})
            self._record_event(EventType.ARTICLES_SELECTED, {
                "selected": selected_article_ids,
                "matched": len(selected),
            })

        self._transition(WorkflowState.PLANNING, "parse_completed")

        # 3. Decompose / Plan
        self._orchestrator.decompose(self._doc)
        self._transition(WorkflowState.EXECUTING, "plan_approved")

        # 4. Execute — analyze through lenses
        if not self._doc.articles:
            self._transition(WorkflowState.EXECUTING, "execution_failed")
            return [], []

        all_findings: list[Finding] = []
        for article in self._doc.articles:
            article_findings = await self._orchestrator.analyze_article(article)
            all_findings.extend(article_findings)
            for f in article_findings:
                self._record_event(EventType.FINDING_CREATED, {
                    "finding_id": str(f.id),
                    "lens": f.lens,
                    "type": f.finding_type.value,
                })

        self._findings = all_findings
        self._transition(WorkflowState.AGGREGATING, "execution_completed")

        # 5. Aggregate — rerank findings (Phase 2)
        if all_findings:
            scored = await self._reranker.rerank(all_findings)
            self._findings = [s.finding for s in scored]
        self._transition(WorkflowState.VERIFYING, "aggregation_completed")

        # 6. Verify — Skeptic review (Phase 3)
        survivors, verdicts = await self._skeptic.review(self._findings)
        refuted_count = len(self._findings) - len(survivors)
        self._findings = survivors
        if refuted_count:
            self._record_event(EventType.FINDING_REFUTED, {
                "refuted": refuted_count,
                "survivors": len(survivors),
            })

        # 7. Verify — CoVe citation verification (Phase 3)
        if self._findings:
            cove_results = await self._cove.verify_batch(self._findings)
            verified_findings = [
                r.finding for r in cove_results
            ]
            unverified = sum(1 for r in cove_results if not r.all_verified)
            self._findings = verified_findings
            if unverified:
                self._record_event(EventType.CITATION_FAILED, {
                    "unverified": unverified,
                })
            else:
                self._record_event(EventType.CITATION_VERIFIED, {
                    "verified": len(verified_findings),
                })
        self._transition(WorkflowState.IMPROVING, "verify_passed")

        # 8. Improve — generate suggestions (Phase 4)
        if self._findings:
            self._suggestions = await self._improver.generate_suggestions(self._findings)
        self._transition(WorkflowState.REPORTING, "improvement_completed")

        # 9. Report — render both report types (Phase 4)
        if self._doc and self._findings:
            exec_summary = await ExecutiveSummaryRenderer().render(
                self._doc, self._findings, self._suggestions
            )
            article_by_article = await ArticleByArticleRenderer().render(
                self._doc, self._findings, self._suggestions
            )
            self._reports = [exec_summary, article_by_article]

            # Auto-save reports and findings to output directory
            import json
            exec_path = output_path / f"{bill_name}_executive_summary.md"
            art_path = output_path / f"{bill_name}_article_by_article.md"
            findings_path = output_path / f"{bill_name}_findings.json"

            exec_path.write_text(exec_summary.to_markdown(), encoding="utf-8")
            art_path.write_text(article_by_article.to_markdown(), encoding="utf-8")

            findings_data = []
            for f in self._findings:
                findings_data.append({
                    "id": str(f.id),
                    "type": f.finding_type.value,
                    "severity": f.severity.value,
                    "confidence": f.confidence.score,
                    "lens": f.lens,
                    "issue": f.irac.issue,
                    "rule": f.irac.rule,
                    "conclusion": f.irac.conclusion,
                    "evidence": [e.text_excerpt for e in f.evidence if e.text_excerpt],
                })
            with open(findings_path, "w", encoding="utf-8") as findings_fh:
                json.dump(findings_data, findings_fh, indent=2, ensure_ascii=False)

            logger.info(
                "flow.outputs_saved",
                executive_summary=str(exec_path),
                article_by_article=str(art_path),
                findings=str(findings_path),
            )

        # 10. Done
        self._record_event(EventType.WORKFLOW_COMPLETED, {
            "total_findings": len(self._findings),
            "suggestions": len(self._suggestions),
            "reports": len(self._reports),
            "final_state": self._state.value,
        })
        self._transition(WorkflowState.DONE, "report_completed")

        return self._findings, self._reports

    @property
    def reports(self) -> list[Report]:
        """Get rendered reports from last run."""
        return list(self._reports)

    @property
    def suggestions(self) -> list[Suggestion]:
        """Get generated suggestions from last run."""
        return list(self._suggestions)

    # ── Private helpers ─────────────────────────────────────────────

    async def _do_ingest(self, file_path: Path) -> str:
        return await self._ingester.ingest(file_path)

    def _do_parse(self, text: str, file_path: Path) -> Document:
        return self._parser.parse(
            text, title=file_path.stem, source_format=file_path.suffix.lstrip(".")
        )

    def _transition(self, target: WorkflowState, event: str) -> None:
        """Transition the FSM, tracking current state."""
        next_state = self._fsm.transition(self._state, event)
        if next_state is not None:
            self._state = next_state
            self._record_event(EventType.STAGE_COMPLETED, {
                "from": self._state.value if next_state != target else "previous",
                "to": target.value,
                "event": event,
            })

    def _record_event(self, event_type: EventType, data: dict[str, Any]) -> None:
        """Record an audit event."""
        self._events.append(
            Event(
                event_type=event_type,
                aggregate_id=f"run-{id(self)}",
                data=data,
            )
        )

    def get_event_log(self) -> list[Event]:
        """Get the full event log for this run."""
        return list(self._events)
