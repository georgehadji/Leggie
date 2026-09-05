"""BillAnalysisFlow — end-to-end bill analysis workflow.

Orchestrates the full analysis pipeline using the FlowStateMachine for
state transitions and Stage lifecycle for each phase.

EN3: Optional blackboard-based aggregation replaces inline mutation.
D10: Crash-resume via CheckpointStore — re-enter at the last completed stage
without double-billing expensive work.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from leggie.application.agents.improver import ImprovementEngine, Suggestion
from leggie.application.agents.orchestrator import Orchestrator
from leggie.application.agents.skeptic import CalibratedSkeptic
from leggie.application.ports.ingest import IngestPort
from leggie.application.ports.llm import LLMPort
from leggie.application.ports.parse import ParsePort
from leggie.application.ports.reranker import RerankerPort
from leggie.application.ports.router import RouterPort
from leggie.application.services.bill_overview import BillOverviewGenerator
from leggie.application.services.cove_verifier import (
    CoVeVerifier,
    article_number,
    article_number_of,
)
from leggie.application.services.reports import (
    ArticleByArticleRenderer,
    ExecutiveSummaryRenderer,
    Report,
)
from leggie.application.services.rerank import CompositeReranker, ModelBasedReranker
from leggie.application.workflow.flow_state_machine import FlowStateMachine
from leggie.application.workflow.ingest_parse import lazy_ingest_adapter, lazy_parse_adapter
from leggie.config.settings import get_settings
from leggie.domain.clustering import deduplicate
from leggie.domain.models import (
    BillOverview,
    Document,
    Event,
    EventType,
    Finding,
    WorkflowState,
)
from leggie.observability import get_logger

if TYPE_CHECKING:
    from leggie.infrastructure.persistence.checkpoint_store import CheckpointStore

logger = get_logger(__name__)


class ParseIntegrityError(Exception):
    """Raised when the parse integrity gate rejects a degraded parse."""


class BillAnalysisFlow:
    """End-to-end bill analysis workflow.

    Manages run lifecycle: tracks state via FlowStateMachine, dispatches
    through stages, and records events for auditability.

    EN3: use_blackboard=True uses BlackboardAggregator for event-sourced aggregation.
    D10: If a CheckpointStore is supplied, the flow persists full stage outputs
    after every transition and can resume from the last resumable state.
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
        use_verbalized_sampling: bool = False,
        reranker_name: str = "composite",
        reranker_port: RerankerPort | None = None,
        checkpoint_store: CheckpointStore | None = None,
        checkpoint_path: str | Path | None = None,
        allow_degraded_parse: bool = False,
    ) -> None:
        self._fsm = FlowStateMachine()
        self._state = WorkflowState.IDLE
        self._llm = llm
        # Wrap _record_event to accept Event objects from lenses/callbacks
        self._on_degradation = on_degradation or (
            lambda ev: self._record_event(ev.event_type, ev.data)
        )
        self._orchestrator = orchestrator or Orchestrator(
            llm=llm,
            on_degradation=self._on_degradation,
            router=router,
            max_article_concurrency=get_settings().llm.max_concurrency,
            use_verbalized_sampling=use_verbalized_sampling,
        )
        self._reranker = self._build_reranker(reranker_name, reranker_port)
        self._skeptic = skeptic or CalibratedSkeptic(llm=llm, router=router)
        self._cove = cove or CoVeVerifier(llm=llm, router=router)
        self._improver = ImprovementEngine()
        self._overview_generator = BillOverviewGenerator(llm=llm)
        self._ingester = ingester or lazy_ingest_adapter()
        self._parser = parser or lazy_parse_adapter()
        self._dedup_threshold = dedup_threshold
        self._use_blackboard = use_blackboard
        self._reports: list[Report] = []
        self._suggestions: list[Suggestion] = []
        self._events: list[Event] = []
        self._findings: list[Finding] = []
        self._doc: Document | None = None
        self._source_text: str = ""
        self._run_id: str = ""
        self._input_file_path: str = ""
        self._checkpoint_store: CheckpointStore | None = checkpoint_store
        self._checkpoint_path: Path | None = Path(checkpoint_path) if checkpoint_path else None
        self._overview: BillOverview | None = None
        self._allow_degraded_parse: bool = allow_degraded_parse

    def _build_reranker(
        self,
        reranker_name: str,
        reranker_port: RerankerPort | None,
    ) -> CompositeReranker | ModelBasedReranker:
        """Build the reranker requested by configuration."""
        if reranker_name == "model" and reranker_port is not None:
            return ModelBasedReranker(
                reranker_port=reranker_port, on_degradation=self._on_degradation
            )
        return CompositeReranker()

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
        """Stage 0 — ingest + parse, then generate a descriptive bill overview.

        Runs before the deep multi-lens analysis: produces a short intro,
        an overall summary, and per-article (Άρθρο) purpose / key
        provisions / practical consequences. Use the resulting article IDs
        to decide what to pass as `--articles` / `selected_article_ids` to
        `run()`. This is descriptive, not evaluative — no findings here.
        """
        file_path = Path(file_path)
        # Reuse-safe like run(): a flow object may already be DONE/FAILED from
        # a prior run()/preview() call. Reset to IDLE first so this call's own
        # transitions are valid table entries instead of silent no-ops (a
        # state with no matching (state, event) row leaves self._state stuck
        # on the stale value and drops every STAGE_COMPLETED event, even
        # though ingest/parse/generate below still run regardless).
        self._state = WorkflowState.IDLE
        self._transition(WorkflowState.PREVIEWING, "preview_started")
        self._transition(WorkflowState.INGESTING, "ingest_started")
        text = await self._do_ingest(file_path)
        self._transition(WorkflowState.PARSING, "ingest_completed")
        self._doc = self._do_parse(text, file_path)

        overview = await self._overview_generator.generate(self._doc)
        self._overview = overview
        self._record_event(
            EventType.OVERVIEW_GENERATED,
            {
                "articles": len(self._doc.articles),
            },
        )
        return overview

    async def run(
        self,
        file_path: str | Path,
        output_dir: str | Path = "Outputs",
        lenses: list[str] | None = None,
        articles: str | None = None,
        selected_article_ids: list[str] | None = None,
        checkpoint_path: str | Path | None = None,
    ) -> tuple[list[Finding], list[Any]]:
        """Run the full analysis workflow on a bill file.

        Args:
            file_path: Path to the bill file (PDF/DOCX/HTML/TXT).
            output_dir: Directory to save reports and findings. Defaults to "Outputs".
            lenses: Lens names to apply. None => all configured lenses.
            articles: Article selection expression, e.g. "1-5,7,10" or "1,2,3".
                None => all parsed articles.
            selected_article_ids: Explicit list of Άρθρο IDs to restrict analysis
                to (e.g. the ones picked after reviewing `preview()`'s
                BillOverview). Applied in addition to `articles`. None => no
                explicit-id restriction.
            checkpoint_path: When set, creates a CheckpointStore at this path for
                atomic resume support. Ignored if a checkpoint_store was already
                supplied to the constructor.

        Returns:
            (findings, reports) tuple.
        """
        import json
        import uuid

        from leggie.infrastructure.persistence.checkpoint_store import CheckpointStore
        from leggie.observability import bind_trace_id, get_logger, set_trace_id

        file_path = Path(file_path)
        self._input_file_path = str(file_path)
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        bill_name = file_path.stem
        self._run_id = str(uuid.uuid4())
        trace_id = self._run_id
        set_trace_id(trace_id)
        logger = bind_trace_id(get_logger(__name__))
        logger.info("flow.started", bill_path=str(file_path), trace_id=trace_id)

        run_checkpoint_path = (
            Path(checkpoint_path) if checkpoint_path is not None else self._checkpoint_path
        )
        if self._checkpoint_store is None and run_checkpoint_path is not None:
            self._checkpoint_store = CheckpointStore(str(run_checkpoint_path))

        # Start each run with a clean slate; a compatible checkpoint will restore
        # the state we should resume from. This keeps reuse of a flow object safe.
        self._state = WorkflowState.IDLE
        self._findings = []
        self._events = []
        self._doc = None
        self._suggestions = []
        self._reports = []
        self._source_text = ""

        self._load_checkpoint(file_path)

        # If a checkpoint restored a document, apply article selection now so the
        # requested subset is respected on resume.
        if self._doc is not None and articles is not None:
            self._doc = self._filter_document(self._doc, articles)
        if self._doc is not None and selected_article_ids is not None:
            self._doc = self._select_article_ids(self._doc, selected_article_ids)

        # 1. Ingest
        if self._state == WorkflowState.IDLE:
            self._transition(WorkflowState.INGESTING, "ingest_started")
            self._source_text = await self._do_ingest(file_path)
            self._record_event(
                EventType.ANALYSIS_STARTED,
                {
                    "file": str(file_path),
                    "size": len(self._source_text),
                },
            )
            self._transition(WorkflowState.PARSING, "ingest_completed")

        # 2. Parse
        if self._state == WorkflowState.PARSING:
            self._doc = self._do_parse(self._source_text, file_path)
            if articles is not None:
                self._doc = self._filter_document(self._doc, articles)
            if selected_article_ids is not None:
                self._doc = self._select_article_ids(self._doc, selected_article_ids)
            self._source_text = ""  # No longer needed once parsed
            self._transition(WorkflowState.PLANNING, "parse_completed")

        # 3. Decompose / Plan
        if self._state == WorkflowState.PLANNING:
            if self._doc is None:
                self._transition(WorkflowState.FAILED, "plan_failed")
                return [], []
            self._orchestrator.decompose(self._doc)
            self._transition(WorkflowState.EXECUTING, "plan_approved")

        # 4. Execute — analyze through lenses
        if self._state == WorkflowState.EXECUTING:
            if self._doc is None or not self._doc.articles:
                self._transition(WorkflowState.FAILED, "execution_failed")
                return [], []

            raw_findings = await self._orchestrator.analyze_document(self._doc, lenses)
            for f in raw_findings:
                self._record_event(
                    EventType.FINDING_CREATED,
                    {
                        "finding_id": str(f.id),
                        "lens": f.lens,
                        "type": f.finding_type.value,
                    },
                )

            self._findings = raw_findings
            self._transition(WorkflowState.AGGREGATING, "execution_completed")

        # 5-8. Aggregate (dedup) & Verify (skeptic → CoVe → rerank)
        # Source index lets CoVe answer verification questions against the real
        # article text (factored), keyed by article number.
        if self._state == WorkflowState.AGGREGATING:
            article_index = {
                article_number(a.raw_text) or a.id: a.raw_text
                for a in (self._doc.articles if self._doc else [])
            }
            if self._use_blackboard:
                self._findings = await self._aggregate_via_blackboard(self._findings, article_index)
            else:
                self._findings = await self._aggregate_inline_dedup(self._findings)
            self._transition(WorkflowState.VERIFYING, "aggregation_completed")

        if self._state == WorkflowState.VERIFYING:
            if not self._use_blackboard:
                article_index = {
                    article_number(a.raw_text) or a.id: a.raw_text
                    for a in (self._doc.articles if self._doc else [])
                }
                self._findings = await self._aggregate_inline_verify(self._findings, article_index)
            self._transition(WorkflowState.IMPROVING, "verify_passed")

        # 8. Improve — generate suggestions (Phase 4)
        if self._state == WorkflowState.IMPROVING:
            if self._findings:
                self._suggestions = await self._improver.generate_suggestions(self._findings)
            self._transition(WorkflowState.REPORTING, "improvement_completed")

        # 9. Report — render both report types (Phase 4)
        if self._state == WorkflowState.REPORTING:
            if self._doc and self._findings:
                exec_summary = await ExecutiveSummaryRenderer().render(
                    self._doc, self._findings, self._suggestions
                )
                article_by_article = await ArticleByArticleRenderer().render(
                    self._doc, self._findings, self._suggestions
                )
                self._reports = [exec_summary, article_by_article]

                # Auto-save reports and findings to output directory
                exec_path = output_path / f"{bill_name}_executive_summary.md"
                exec_docx_path = output_path / f"{bill_name}_executive_summary.docx"
                art_path = output_path / f"{bill_name}_article_by_article.md"
                art_docx_path = output_path / f"{bill_name}_article_by_article.docx"
                findings_path = output_path / f"{bill_name}_findings.json"

                exec_path.write_text(exec_summary.to_markdown(), encoding="utf-8")
                art_path.write_text(article_by_article.to_markdown(), encoding="utf-8")
                exec_summary.to_docx(exec_docx_path)
                article_by_article.to_docx(art_docx_path)

                findings_data = []
                for f in self._findings:
                    findings_data.append(
                        {
                            "id": str(f.id),
                            "type": f.finding_type.value,
                            "severity": f.severity.value,
                            "confidence": f.confidence.score,
                            "lens": f.lens,
                            "issue": f.irac.issue,
                            "rule": f.irac.rule,
                            "conclusion": f.irac.conclusion,
                            "evidence": [e.text_excerpt for e in f.evidence if e.text_excerpt],
                        }
                    )
                with open(findings_path, "w", encoding="utf-8") as findings_file:
                    json.dump(findings_data, findings_file, indent=2, ensure_ascii=False)

                logger.info(
                    "flow.outputs_saved",
                    executive_summary=str(exec_path),
                    executive_summary_docx=str(exec_docx_path),
                    article_by_article=str(art_path),
                    article_by_article_docx=str(art_docx_path),
                    findings=str(findings_path),
                )
                guard = self._budget_guard()
                if guard is not None:
                    logger.info(
                        "flow.budget_state",
                        max_tokens=guard.save_state().get("max_tokens"),
                        tokens_used=guard.save_state().get("tokens_used"),
                        cost_used=round(guard.save_state().get("cost_used", 0.0), 4),
                        remaining_cost=round(guard.remaining_cost, 4),
                    )
            self._transition(WorkflowState.DONE, "report_completed")

        # 10. Done
        self._record_event(
            EventType.WORKFLOW_COMPLETED,
            {
                "total_findings": len(self._findings),
                "suggestions": len(self._suggestions),
                "reports": len(self._reports),
                "final_state": self._state.value,
            },
        )

        return self._findings, self._reports

    async def _aggregate_via_blackboard(
        self, raw_findings: list[Finding], article_index: dict[str, str] | None = None
    ) -> list[Finding]:
        """Aggregate findings via BlackboardAggregator (EN3)."""
        from leggie.application.services.blackboard_aggregator import BlackboardAggregator

        aggregator = BlackboardAggregator(
            dedup_threshold=self._dedup_threshold,
            reranker=self._reranker,
            skeptic=self._skeptic,
            cove=self._cove,
        )
        results = await aggregator.aggregate(raw_findings, article_index)
        # Merge aggregator events into flow event log
        self._events.extend(aggregator.events)
        return results

    async def _aggregate_inline_dedup(self, raw_findings: list[Finding]) -> list[Finding]:
        """Inline aggregation part 1: dedup only.

        Reranking deliberately does NOT happen here — it runs at the end of
        the verification chain, in `_aggregate_inline_verify`.
        """
        findings = raw_findings

        # 5. Dedup
        if findings:
            deduped = self._dedup_findings(findings)
            dedup_count = len(findings) - len(deduped)
            if dedup_count:
                self._record_event(
                    EventType.DEDUP_REMOVED,
                    {
                        "removed": dedup_count,
                        "survivors": len(deduped),
                    },
                )
            findings = deduped

        return findings

    async def _aggregate_inline_verify(
        self, findings: list[Finding], article_index: dict[str, str] | None = None
    ) -> list[Finding]:
        """Inline aggregation part 2: skeptic → CoVe → rerank.

        Rerank closes the chain rather than opening it: the skeptic and CoVe
        both rewrite ``Finding.confidence``, which is an input to the composite
        score, and both drop findings, which changes every novelty score. An
        order computed before them describes a population that no longer exists.
        """
        # 6. Skeptic review
        survivors, _ = await self._skeptic.review(findings)
        refuted_count = len(findings) - len(survivors)
        findings = survivors
        if refuted_count:
            self._record_event(
                EventType.FINDING_REFUTED,
                {
                    "refuted": refuted_count,
                    "survivors": len(survivors),
                },
            )

        # 7. CoVe Chain-of-Verification (factored) — revise or drop
        if findings:
            cove_results = await self._cove.verify_batch(findings, article_index)
            kept = [r.finding for r in cove_results if not r.dropped]
            dropped = sum(1 for r in cove_results if r.dropped)
            unverified = sum(1 for r in cove_results if not r.all_verified)
            findings = kept
            if dropped:
                self._record_event(
                    EventType.FINDING_REFUTED,
                    {
                        "refuted": dropped,
                        "survivors": len(kept),
                        "stage": "cove",
                    },
                )
            if unverified:
                self._record_event(
                    EventType.CITATION_FAILED,
                    {
                        "unverified": unverified,
                    },
                )
            else:
                self._record_event(
                    EventType.CITATION_VERIFIED,
                    {
                        "verified": len(kept),
                    },
                )

        # 8. Rerank the verified survivors into the published order
        if findings:
            scored = await self._reranker.rerank(findings)
            findings = [s.finding for s in scored]

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
        doc, report = self._parser.parse_with_integrity(
            text, title=file_path.stem, source_format=file_path.suffix.lstrip(".")
        )
        # Parse-integrity gate: abort on degraded parse unless explicitly allowed
        if not report.is_clean and not self._allow_degraded_parse:
            self._record_event(
                EventType.DEGRADED,
                {
                    "stage": "parse",
                    "articles_parsed": report.articles_parsed,
                    "distinct_ids": report.distinct_ids,
                    "duplicate_ids": list(report.duplicate_ids),
                    "missing_numbers": list(report.missing_numbers),
                    "rejected": [r.model_dump() for r in report.rejected],
                },
            )
            raise ParseIntegrityError(
                f"Parse integrity check failed: "
                f"{report.articles_parsed} articles, "
                f"{report.distinct_ids} distinct IDs, "
                f"{len(report.duplicate_ids)} duplicates, "
                f"{len(report.missing_numbers)} gaps, "
                f"{len(report.rejected)} rejected candidates. "
                f"Use --allow-degraded-parse to proceed anyway."
            )
        return doc

    def _filter_document(self, doc: Document, selection: str) -> Document:
        """Return a new Document keeping only the selected articles.

        Raises ValueError if selection matches fewer articles than
        requested for an explicit range.
        """
        keep_ids = _parse_article_selection(selection, [a.id for a in doc.articles])
        if not keep_ids:
            raise ValueError(
                f"Article selection '{selection}' matched none of the parsed articles: "
                f"{[a.id for a in doc.articles]}"
            )
        # Selection strictness: for an explicit range, check if we matched less
        parts = [p.strip() for p in selection.split(",") if p.strip()]
        requested_count = 0
        for part in parts:
            if "-" in part:
                try:
                    start_s, end_s = part.split("-", 1)
                    start = int(start_s.strip())
                    end = int(end_s.strip())
                    if start > end:
                        start, end = end, start
                    requested_count += end - start + 1
                except ValueError:
                    pass
            else:
                requested_count += 1
        # Only enforce for explicit ranges (requested_count > 0 and > matched)
        if requested_count > 0 and requested_count > len(keep_ids):
            raise ValueError(
                f"Article selection '{selection}' requested {requested_count} articles, "
                f"matched {len(keep_ids)} ({keep_ids}). "
                f"Available IDs: {[a.id for a in doc.articles[:10]]}{'...' if len(doc.articles) > 10 else ''}"
            )
        filtered = [a for a in doc.articles if a.id in keep_ids]
        return doc.model_copy(update={"articles": filtered}, deep=False)

    def _select_article_ids(self, doc: Document, ids: list[str]) -> Document:
        """Return a new Document keeping only articles whose id is in *ids*."""
        selected = [a for a in doc.articles if a.id in ids]
        self._record_event(
            EventType.ARTICLES_SELECTED,
            {
                "selected": ids,
                "matched": len(selected),
            },
        )
        return doc.model_copy(update={"articles": selected}, deep=False)

    def _dedup_findings(self, findings: list[Finding]) -> list[Finding]:
        """Remove near-duplicate findings, keeping the best per cluster."""
        if not findings:
            return []

        def _finding_similarity(a: Finding, b: Finding) -> float:
            if (
                a.finding_type != b.finding_type
                or a.lens != b.lens
                or article_number_of(a) != article_number_of(b)
            ):
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
        """Transition the FSM, tracking current state.

        The recorded STAGE_COMPLETED event always reflects what the FSM table
        actually computed (previous -> next_state), never the caller-supplied
        *target* — a target that disagrees with the table (e.g. an "_failed"
        event's real destination is FAILED, not the state being checked when
        it fired) must not corrupt the audit trail.
        """
        previous_state = self._state
        next_state = self._fsm.transition(self._state, event)
        if next_state is not None:
            self._state = next_state
            self._record_event(
                EventType.STAGE_COMPLETED,
                {
                    "from": previous_state.value,
                    "to": next_state.value,
                    "event": event,
                },
            )
        self._save_checkpoint()

    def _budget_guard(self) -> Any | None:
        """Duck-type the BudgetGuard out of a (possibly decorated) LLM port."""
        return getattr(self._llm, "budget_guard", None)

    def _save_checkpoint(self) -> None:
        """Persist an atomic checkpoint of the full run state, if configured."""
        if self._checkpoint_store is None:
            return
        guard = self._budget_guard()
        budget_state = guard.save_state() if guard is not None else {}
        data: dict[str, Any] = {
            "run_id": self._run_id,
            "stage": self._state.value,
            "file_path": self._input_file_path,
            "findings": [f.model_dump(mode="json") for f in self._findings],
            "events": [e.model_dump(mode="json") for e in self._events],
            "document": self._doc.model_dump(mode="json") if self._doc is not None else None,
            "source_text": self._source_text,
            "suggestions": [dataclasses.asdict(s) for s in self._suggestions],
            "reports": [dataclasses.asdict(r) for r in self._reports],
            "budget_state": budget_state,
        }
        try:
            # Checkpointing is best-effort; never fail the run over it — but a
            # crash-resume safety net that silently isn't being written is
            # worth a warning, not total silence.
            self._checkpoint_store.save(data)
        except OSError as exc:
            logger.warning("checkpoint.save_failed", stage=self._state.value, error=str(exc))

    def _load_checkpoint(self, file_path: Path | None = None) -> None:
        """Restore run state from the checkpoint store, if a compatible one exists.

        Backward-compatible: old budget-only checkpoints (keys tokens_used,
        cost_used, etc.) are loaded as budget state with stage reset to IDLE.
        """
        if self._checkpoint_store is None:
            # Legacy path-based budget-only checkpoint loading.
            self._load_legacy_budget_checkpoint()
            return

        data = self._checkpoint_store.load()
        if data is None:
            return

        # Detect old budget-only format.
        if "stage" not in data and "tokens_used" in data:
            guard = self._budget_guard()
            if guard is not None:
                try:
                    guard.load_state(data)
                except Exception:
                    logger.warning("checkpoint.legacy_budget_state_corrupt")
            return

        stage_value = data.get("stage")
        if stage_value is None:
            return
        try:
            stage = WorkflowState(stage_value)
        except ValueError:
            return
        if stage not in FlowStateMachine.resumable_states():
            return

        # Safety: a checkpoint belongs to a specific input file. Ignore it if
        # we are now processing a different file.
        checkpoint_file_path = data.get("file_path") or ""
        if (
            file_path is not None
            and checkpoint_file_path
            and checkpoint_file_path != str(file_path)
        ):
            return

        self._run_id = data.get("run_id") or self._run_id
        self._state = stage
        self._findings = [Finding(**f) for f in data.get("findings", [])]
        self._events = [Event(**e) for e in data.get("events", [])]
        doc_data = data.get("document")
        self._doc = Document(**doc_data) if doc_data is not None else None
        self._source_text = data.get("source_text", "")
        self._suggestions = [Suggestion(**s) for s in data.get("suggestions", [])]
        self._reports = [Report(**r) for r in data.get("reports", [])]

        budget_state = data.get("budget_state")
        if budget_state:
            guard = self._budget_guard()
            if guard is not None:
                try:
                    guard.load_state(budget_state)
                except Exception:
                    logger.warning("checkpoint.budget_state_corrupt")

    def _load_legacy_budget_checkpoint(self) -> None:
        """Restore budget spend from a legacy path-based checkpoint file."""
        if self._checkpoint_path is None or not self._checkpoint_path.exists():
            return
        guard = self._budget_guard()
        if guard is None:
            return
        import json

        try:
            state = json.loads(self._checkpoint_path.read_text(encoding="utf-8"))
            guard.load_state(state)
        except Exception:
            # Corrupt/missing checkpoint — start fresh rather than crash, but
            # log it: a silently-ignored corrupt checkpoint looks identical
            # to "no checkpoint" from the outside.
            logger.warning("checkpoint.legacy_load_failed", path=str(self._checkpoint_path))

    def _record_event(self, event_type: EventType, data: dict[str, Any]) -> None:
        """Record an audit event."""
        aggregate_id = self._run_id if self._run_id else f"run-{id(self)}"
        self._events.append(
            Event(
                event_type=event_type,
                aggregate_id=aggregate_id,
                data=data,
            )
        )

    def get_event_log(self) -> list[Event]:
        """Get the full event log for this run."""
        return list(self._events)


def _parse_article_selection(selection: str, available_ids: list[str]) -> list[str]:
    """Parse an article selection expression into a list of article IDs.

    Supports:
      - exact IDs: "1,3,5A"
      - numeric ranges: "1-5" (matches every article whose leading numeric part
        falls inside the inclusive range, e.g. "5" and "5A" both match "1-5")
      - mixed: "1-5,7,10-12"

    Returns the matched IDs in the original document order.
    """
    parts = [p.strip() for p in selection.split(",") if p.strip()]
    ranges: list[tuple[int, int]] = []
    exact: set[str] = set()
    for part in parts:
        if "-" in part:
            try:
                start_s, end_s = part.split("-", 1)
                start = int(start_s.strip())
                end = int(end_s.strip())
            except ValueError as exc:
                raise ValueError(f"Invalid article range '{part}'") from exc
            if start > end:
                start, end = end, start
            ranges.append((start, end))
        else:
            exact.add(part)

    def _leading_number(aid: str) -> int | None:
        m = re.match(r"\d+", aid)
        return int(m.group()) if m else None

    keep: set[str] = set()
    for aid in available_ids:
        if aid in exact:
            keep.add(aid)
            continue
        num = _leading_number(aid)
        if num is None:
            continue
        for start, end in ranges:
            if start <= num <= end:
                keep.add(aid)
                break

    return [aid for aid in available_ids if aid in keep]
