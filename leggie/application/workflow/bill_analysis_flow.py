"""BillAnalysisFlow — end-to-end bill analysis workflow.

Orchestrates the full analysis pipeline using the FlowStateMachine for
state transitions and Stage lifecycle for each phase.

EN3: Optional blackboard-based aggregation replaces inline mutation.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from leggie.application.agents.improver import ImprovementEngine
from leggie.application.agents.orchestrator import Orchestrator
from leggie.application.agents.skeptic import CalibratedSkeptic
from leggie.application.ports.ingest import IngestPort
from leggie.application.ports.llm import LLMPort
from leggie.application.ports.parse import ParsePort
from leggie.application.ports.router import RouterPort
from leggie.application.services.cove_verifier import CoVeVerifier
from leggie.application.services.reports import ArticleByArticleRenderer, ExecutiveSummaryRenderer
from leggie.application.services.rerank import CompositeReranker
from leggie.application.workflow.flow_state_machine import FlowStateMachine
from leggie.domain.clustering import deduplicate
from leggie.domain.models import (
    Document,
    Event,
    EventType,
    Finding,
    WorkflowState,
)


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

    EN3: use_blackboard=True uses BlackboardAggregator for event-sourced aggregation.
    """

    def __init__(
        self,
        orchestrator: Orchestrator | None = None,
        skeptic: CalibratedSkeptic | None = None,
        cove: CoVeVerifier | None = None,
        llm: LLMPort | None = None,
        ingester: IngestPort | None = None,
        parser: ParsePort | None = None,
        dedup_threshold: float = 0.85,
        on_degradation: Callable[[Event], None] | None = None,
        router: RouterPort | None = None,
        use_blackboard: bool = True,
    ) -> None:
        self._fsm = FlowStateMachine()
        self._state = WorkflowState.IDLE
        self._llm = llm
        # Wrap _record_event to accept Event objects from lenses/callbacks
        self._on_degradation = on_degradation or (
            lambda ev: self._record_event(ev.event_type, ev.data))
        self._orchestrator = orchestrator or Orchestrator(
            llm=llm, on_degradation=self._on_degradation, router=router)
        self._reranker = CompositeReranker()
        self._skeptic = skeptic or CalibratedSkeptic()
        self._cove = cove or CoVeVerifier()
        self._improver = ImprovementEngine()
        self._ingester = ingester or _lazy_ingest_adapter()
        self._parser = parser or _lazy_parse_adapter()
        self._dedup_threshold = dedup_threshold
        self._use_blackboard = use_blackboard
        self._reports: list[Any] = []
        self._suggestions: list[Any] = []
        self._events: list[Event] = []
        self._findings: list[Finding] = []

    @property
    def state(self) -> WorkflowState:
        return self._state

    @property
    def findings(self) -> list[Finding]:
        return list(self._findings)

    async def run(self, file_path: str | Path, output_dir: str | Path = "Outputs") -> tuple[list[Finding], list[Any]]:
        """Run the full analysis workflow on a bill file.

        Args:
            file_path: Path to the bill file (PDF/DOCX/HTML/TXT).
            output_dir: Directory to save reports and findings. Defaults to "Outputs".

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

        # 1. Ingest
        self._transition(WorkflowState.INGESTING, "ingest_started")
        text = await self._do_ingest(file_path)
        self._record_event(EventType.ANALYSIS_STARTED, {"file": str(file_path), "size": len(text)})
        self._transition(WorkflowState.PARSING, "ingest_completed")

        # 2. Parse
        self._doc = self._do_parse(text, file_path)
        self._transition(WorkflowState.PLANNING, "parse_completed")

        # 3. Decompose / Plan
        self._orchestrator.decompose(self._doc)
        self._transition(WorkflowState.EXECUTING, "plan_approved")

        # 4. Execute — analyze through lenses
        if not self._doc.articles:
            self._transition(WorkflowState.EXECUTING, "execution_failed")
            return [], []

        raw_findings: list[Finding] = []
        for article in self._doc.articles:
            article_findings = await self._orchestrator.analyze_article(article)
            raw_findings.extend(article_findings)
            for f in article_findings:
                self._record_event(EventType.FINDING_CREATED, {
                    "finding_id": str(f.id),
                    "lens": f.lens,
                    "type": f.finding_type.value,
                })

        self._findings = raw_findings
        self._transition(WorkflowState.AGGREGATING, "execution_completed")

        # 5-7. Aggregate & Verify
        if self._use_blackboard:
            self._findings = await self._aggregate_via_blackboard(raw_findings)
            self._transition(WorkflowState.VERIFYING, "aggregation_completed")
        else:
            self._findings = await self._aggregate_inline(raw_findings)

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

            # Auto-save reports and findings to output directory
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
            with open(findings_path, "w", encoding="utf-8") as findings_file:
                json.dump(findings_data, findings_file, indent=2, ensure_ascii=False)

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

    async def _aggregate_via_blackboard(self, raw_findings: list[Finding]) -> list[Finding]:
        """Aggregate findings via BlackboardAggregator (EN3)."""
        from leggie.application.services.blackboard_aggregator import BlackboardAggregator
        aggregator = BlackboardAggregator(
            dedup_threshold=self._dedup_threshold,
            reranker=self._reranker,
            skeptic=self._skeptic,
            cove=self._cove,
        )
        results = await aggregator.aggregate(raw_findings)
        # Merge aggregator events into flow event log
        self._events.extend(aggregator.events)
        return results

    async def _aggregate_inline(self, raw_findings: list[Finding]) -> list[Finding]:
        """Original inline aggregation path (dedup → rerank → skeptic → CoVe)."""
        findings = raw_findings

        # 5. Dedup
        if findings:
            deduped = self._dedup_findings(findings)
            dedup_count = len(findings) - len(deduped)
            if dedup_count:
                self._record_event(EventType.DEDUP_REMOVED, {
                    "removed": dedup_count,
                    "survivors": len(deduped),
                })
            findings = deduped

        # 6. Rerank
        if findings:
            scored = await self._reranker.rerank(findings)
            findings = [s.finding for s in scored]
        self._transition(WorkflowState.VERIFYING, "aggregation_completed")

        # 7. Skeptic review
        survivors, _ = await self._skeptic.review(findings)
        refuted_count = len(findings) - len(survivors)
        findings = survivors
        if refuted_count:
            self._record_event(EventType.FINDING_REFUTED, {
                "refuted": refuted_count,
                "survivors": len(survivors),
            })

        # 8. CoVe citation verification
        if findings:
            cove_results = await self._cove.verify_batch(findings)
            verified_findings = [r.finding for r in cove_results]
            unverified = sum(1 for r in cove_results if not r.all_verified)
            findings = verified_findings
            if unverified:
                self._record_event(EventType.CITATION_FAILED, {
                    "unverified": unverified,
                })
            else:
                self._record_event(EventType.CITATION_VERIFIED, {
                    "verified": len(verified_findings),
                })

        return findings

    @property
    def reports(self) -> list[Any]:
        """Get rendered reports from last run."""
        return list(self._reports)

    @property
    def suggestions(self) -> list[Any]:
        """Get generated suggestions from last run."""
        return list(self._suggestions)

    # ── Private helpers ─────────────────────────────────────────────

    async def _do_ingest(self, file_path: Path) -> str:
        result: str = await self._ingester.ingest(file_path)
        return result

    def _do_parse(self, text: str, file_path: Path) -> Document:
        doc: Document = self._parser.parse(text, title=file_path.stem, source_format=file_path.suffix.lstrip("."))
        return doc

    def _dedup_findings(self, findings: list[Finding]) -> list[Finding]:
        """Remove near-duplicate findings, keeping the best per cluster."""
        import re
        _article_re = re.compile(r"Άρθρο\s+(\d+)", re.IGNORECASE)

        def _article_prefix(finding: Finding) -> str:
            m = _article_re.search(finding.irac.issue)
            return m.group(1) if m else ""

        if not findings:
            return []

        def _finding_similarity(a: Finding, b: Finding) -> float:
            if (a.finding_type != b.finding_type or
                a.lens != b.lens or
                _article_prefix(a) != _article_prefix(b)):
                return 0.0
            a_tokens = set(a.irac.issue.lower().split())
            b_tokens = set(b.irac.issue.lower().split())
            if not a_tokens or not b_tokens:
                return 0.0
            return len(a_tokens & b_tokens) / max(len(a_tokens), len(b_tokens))

        return deduplicate(
            findings,
            similarity_fn=_finding_similarity,
            threshold=self._dedup_threshold,
            keep="highest_confidence",
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
