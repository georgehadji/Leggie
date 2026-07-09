"""BillAnalysisFlow — end-to-end bill analysis workflow.

Orchestrates the full analysis pipeline using the FlowStateMachine for
state transitions and Stage lifecycle for each phase.

Phase 1: ingest → parse → analyze (Constitutional lens) → report.
"""

from __future__ import annotations

from pathlib import Path

from leggie.application.agents.orchestrator import Orchestrator
from leggie.application.agents.improver import ImprovementEngine
from leggie.application.agents.skeptic import CalibratedSkeptic
from leggie.application.services.reports import ExecutiveSummaryRenderer, ArticleByArticleRenderer
from leggie.application.services.cove_verifier import CoVeVerifier
from leggie.application.services.rerank import CompositeReranker
from leggie.domain.clustering import deduplicate
from leggie.application.workflow.flow_state_machine import FlowStateMachine
from leggie.application.workflow.stage import Stage, StageContext, StageResult
from leggie.domain.models import (
    Confidence,
    Document,
    Event,
    EventType,
    Evidence,
    Finding,
    FindingType,
    IRAC,
    Severity,
    WorkflowState,
)


class BillAnalysisFlow:
    """End-to-end bill analysis workflow.

    Manages run lifecycle: tracks state via FlowStateMachine, dispatches
    through stages, and records events for auditability.

    Phase 2: parallel lens execution + reranking.
    """

    def __init__(self, orchestrator: Orchestrator | None = None, skeptic: CalibratedSkeptic | None = None, cove: CoVeVerifier | None = None) -> None:
        self._fsm = FlowStateMachine()
        self._state = WorkflowState.IDLE
        self._orchestrator = orchestrator or Orchestrator()
        self._reranker = CompositeReranker()
        self._skeptic = skeptic or CalibratedSkeptic()
        self._cove = cove or CoVeVerifier()
        self._improver = ImprovementEngine()
        self._reports: list = []
        self._suggestions: list = []
        self._events: list[Event] = []
        self._findings: list[Finding] = []

    @property
    def state(self) -> WorkflowState:
        return self._state

    @property
    def findings(self) -> list[Finding]:
        return list(self._findings)

    async def run(self, file_path: str | Path) -> tuple[list[Finding], list]:
        """Run the full analysis workflow on a bill file.

        Returns:
            (findings, reports) tuple.
        """
        file_path = Path(file_path)

        # 1. Ingest
        self._transition(WorkflowState.INGESTING, "ingest_started")
        text = await self._do_ingest(file_path)
        self._record_event(EventType.ANALYSIS_STARTED, {"file": str(file_path), "size": len(text)})
        self._transition(WorkflowState.PARSING, "ingest_completed")

        # 2. Parse
        self._doc = self._do_parse(text, file_path)
        self._transition(WorkflowState.PLANNING, "parse_completed")

        # 3. Decompose / Plan
        tasks = self._orchestrator.decompose(self._doc)
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
            exec_summary = await ExecutiveSummaryRenderer().render(self._doc, self._findings, self._suggestions)
            article_by_article = await ArticleByArticleRenderer().render(self._doc, self._findings, self._suggestions)
            self._reports = [exec_summary, article_by_article]

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
    def reports(self) -> list:
        """Get rendered reports from last run."""
        return list(self._reports)

    @property
    def suggestions(self) -> list:
        """Get generated suggestions from last run."""
        return list(self._suggestions)

    # ── Private helpers ─────────────────────────────────────────────

    async def _do_ingest(self, file_path: Path) -> str:
        """Ingest file content."""
        from leggie.infrastructure.ingest import IngestorFactory
        return await IngestorFactory.ingest(file_path)

    def _do_parse(self, text: str, file_path: Path) -> Document:
        """Parse document text into structured form."""
        from leggie.infrastructure.parse import DocumentParser
        parser = DocumentParser()
        return parser.parse(text, title=file_path.stem, source_format=file_path.suffix.lstrip("."))

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

    def _record_event(self, event_type: EventType, data: dict) -> None:
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
