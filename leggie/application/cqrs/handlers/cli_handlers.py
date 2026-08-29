"""CLI Command Handlers — implement CLI commands through the CQRS mediator.

These handlers orchestrate infrastructure adapters behind the command interface,
keeping the interface layer (CLI) thin and testable.

D8: The DI container is the single composition root. Handlers receive a
configured container and resolve all adapters through it; no ad-hoc factories.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from leggie.application.cqrs.base import CommandHandler, CommandResult
from leggie.application.cqrs.commands.cli_commands import (
    AnalyzeBillCommand,
    EvalGoldSetCommand,
    ParseDocumentCommand,
    PreviewBillCommand,
    ReplayRunCommand,
)
from leggie.application.ports.citation_parser import CitationParserPort
from leggie.application.ports.event_bus import EventBusPort
from leggie.application.ports.ingest import IngestPort
from leggie.application.ports.llm import LLMConfigurationError, LLMPort
from leggie.application.ports.parse import ParsePort
from leggie.application.ports.reasoner import ReasonerPort
from leggie.application.ports.reranker import RerankerPort
from leggie.application.ports.router import RouterPort
from leggie.application.services.cove_verifier import CoVeVerifier
from leggie.observability import get_logger

logger = get_logger(__name__)


class ContainerProtocol(Protocol):
    """Minimal capability handlers need from Container (IMPL-1 Group C).

    Kept as a Protocol, not the concrete Container class, so this
    application-layer module never imports infrastructure — mirrors
    DeliberativeFlow's ServerLifecycle Protocol.
    """

    def get(self, port_type: type | str) -> Any: ...
    def has_binding(self, port_type: type) -> bool: ...


# ── Shared resolver helpers ───────────────────────────────────────────


def _resolve_llm_from_container(container: ContainerProtocol) -> LLMPort | None:
    """Resolve LLMPort from *container*, returning None on configuration errors."""
    if container.has_binding(LLMPort):
        try:
            llm: LLMPort = container.get(LLMPort)
            return llm
        except LLMConfigurationError:
            logger.warning("llm.unconfigured_fallback", exc_info=True)
            return None
    return None


def _resolve_router_from_container(container: ContainerProtocol) -> RouterPort | None:
    """Resolve RouterPort from *container*."""
    if container.has_binding(RouterPort):
        router: RouterPort = container.get(RouterPort)
        return router
    return None


def _resolve_cove_from_container(container: ContainerProtocol) -> CoVeVerifier:
    """Resolve a CoVeVerifier wired with LLM + router + citation parser."""
    llm = _resolve_llm_from_container(container)
    router = _resolve_router_from_container(container)
    parser = None
    if container.has_binding(CitationParserPort):
        parser = container.get(CitationParserPort) or None
    return CoVeVerifier(citation_parser=parser, llm=llm, router=router)


# ── Handler classes ───────────────────────────────────────────────────


class ParseDocumentHandler(CommandHandler[ParseDocumentCommand, dict[str, Any]]):
    """Handle bill document parsing."""

    def __init__(self, container: ContainerProtocol) -> None:
        self._container = container

    async def handle(self, command: ParseDocumentCommand) -> CommandResult[dict[str, Any]]:
        try:
            ingest_port = self._container.get(IngestPort)
            parse_port = self._container.get(ParsePort)

            text = await ingest_port.ingest(Path(command.file_path))
            doc, report = parse_port.parse_with_integrity(text, title=Path(command.file_path).stem)
            # extract_citations is a DocumentParser-only method, not on ParsePort —
            # it duplicates GreekCitationParser's regexes with different coverage
            # (no URL scheme) and a different FEK identifier format. Left as a
            # direct infra import pending a decision on which parser is canonical
            # (see ARCHITECTURE_IMPLEMENTATION_PLAN_2026-08-10.md §2.1 Group A).
            from leggie.infrastructure.parse import DocumentParser

            citations = DocumentParser().extract_citations(text)

            output = {
                "title": doc.title,
                "articles": [
                    {
                        "id": a.id,
                        "title": a.title,
                        "paragraphs": [{"number": p.number, "text": p.text} for p in a.paragraphs],
                    }
                    for a in doc.articles
                ],
                "citations": citations,
                "integrity": {
                    "articles_parsed": report.articles_parsed,
                    "distinct_ids": report.distinct_ids,
                    "is_clean": report.is_clean,
                    "duplicate_ids": list(report.duplicate_ids),
                    "missing_numbers": list(report.missing_numbers),
                    "rejected_count": len(report.rejected),
                    "toc_span": report.toc_span,
                },
            }

            if command.output_path:
                p = Path(command.output_path)
                p.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

            return CommandResult(success=True, data=output)
        except Exception as e:
            return CommandResult.failure(e)


class AnalyzeBillHandler(CommandHandler[AnalyzeBillCommand, str]):
    """Handle bill analysis using BillAnalysisFlow (deterministic) or DeliberativeFlow (opt-in)."""

    def __init__(self, container: ContainerProtocol) -> None:
        self._container = container

    async def handle(self, command: AnalyzeBillCommand) -> CommandResult[str]:
        if command.pipeline == "deliberative":
            return await self._handle_deliberative(command)
        return await self._handle_deterministic(command)

    async def _handle_deterministic(self, command: AnalyzeBillCommand) -> CommandResult[str]:
        try:
            from leggie.application.workflow.bill_analysis_flow import BillAnalysisFlow
            from leggie.config.settings import get_settings
            from leggie.infrastructure.persistence.checkpoint_store import CheckpointStore

            llm = _resolve_llm_from_container(self._container)
            router = _resolve_router_from_container(self._container)
            cove = _resolve_cove_from_container(self._container)
            settings = get_settings()
            reranker_port = None
            if settings.analysis.reranker == "model" and self._container.has_binding(RerankerPort):
                reranker_port = self._container.get(RerankerPort)

            checkpoint_store = None
            if command.checkpoint_path:
                checkpoint_store = CheckpointStore(command.checkpoint_path)
            elif self._container.has_binding(CheckpointStore):
                checkpoint_store = self._container.get(CheckpointStore)

            flow = BillAnalysisFlow(
                llm=llm,
                router=router,
                cove=cove,
                checkpoint_store=checkpoint_store,
                use_verbalized_sampling=command.use_verbalized_sampling
                or settings.analysis.use_verbalized_sampling,
                reranker_name=settings.analysis.reranker,
                reranker_port=reranker_port,
                allow_degraded_parse=command.allow_degraded_parse,
            )
            findings, reports = await flow.run(
                command.file_path,
                output_dir=command.output_path or "Outputs",
                lenses=command.lenses,
                articles=command.articles,
            )

            summary = f"Analysis complete: {len(findings)} finding(s), {len(reports)} report(s)"
            for f in findings:
                summary += f"\n  - [{f.finding_type.value}:{f.severity.value}] {f.irac.issue[:80]}"

            return CommandResult(success=True, data=summary)
        except Exception as e:
            return CommandResult.failure(e)

    async def _handle_deliberative(self, command: AnalyzeBillCommand) -> CommandResult[str]:
        from leggie.config.settings import get_settings

        settings = get_settings()
        if not settings.reasoner.enabled:
            return CommandResult(
                success=False,
                error=(
                    "Deliberative pipeline is disabled. Set LEGGIE_REASONER__ENABLED=true "
                    "and configure LEGGIE_REASONER__HOME / API key — see .env.example."
                ),
                error_type="ConfigurationError",
            )

        try:
            from leggie.application.ports.reasoner import ReasonerUnavailableError
            from leggie.application.workflow.deliberative_flow import (
                DeliberativeBudgetExceededError,
                DeliberativeFlow,
            )

            # D22: this used to hand-construct GreekCitationParser() with no
            # resolution_index, while the deterministic path (_resolve_cove_from_container)
            # got the container's 181-identifier index — every deliberative-report
            # citation reported "unverified" regardless of whether it actually resolved.
            from leggie.infrastructure.reasoner.server_manager import ReasonerServerManager

            reasoner = self._container.get(ReasonerPort)
            citation_parser = self._container.get(CitationParserPort)
            server_manager = ReasonerServerManager(settings.reasoner)
            flow = DeliberativeFlow(
                reasoner=reasoner,
                stage1_preset=settings.reasoner.stage1_preset,
                stage2_preset=settings.reasoner.stage2_preset,
                server_manager=server_manager,
                citation_parser=citation_parser,
                max_tokens_per_run=settings.budget.max_tokens_per_run,
            )
            try:
                report_path = await flow.run(
                    command.file_path,
                    output_dir=command.output_path or "Outputs",
                    perspective=command.perspective or settings.reasoner.perspective,
                )
            finally:
                # Release whatever ensure_running() may have autostarted — a
                # no-op if nothing was spawned. Never let a cleanup failure
                # shadow the flow's real exception/result (e.g. the
                # ReasonerUnavailableError --fallback depends on).
                try:
                    await server_manager.shutdown()
                except Exception:
                    logger.warning("reasoner.shutdown_failed", exc_info=True)
            return CommandResult(success=True, data=f"Deliberative report saved to {report_path}")
        except ReasonerUnavailableError as e:
            if command.fallback:
                return await self._handle_deterministic(command)
            return CommandResult(
                success=False,
                error=f"Reasoner unavailable: {e}. Retry with --fallback to use the "
                "deterministic pipeline instead.",
                error_type=type(e).__name__,
            )
        except DeliberativeBudgetExceededError as e:
            return CommandResult.failure(e)
        except Exception as e:
            return CommandResult.failure(e)


class PreviewBillHandler(CommandHandler[PreviewBillCommand, dict[str, Any]]):
    """Handle bill preview — Stage 0, before ingest/analyze proper.

    Produces intro, summary, and per-article purpose/key-provisions/
    practical-consequences so the caller can pick which Άρθρο IDs to
    pass into `leggie analyze --articles`.
    """

    def __init__(self, container: ContainerProtocol) -> None:
        self._container = container

    async def handle(self, command: PreviewBillCommand) -> CommandResult[dict[str, Any]]:
        try:
            from leggie.application.workflow.bill_analysis_flow import BillAnalysisFlow

            llm = _resolve_llm_from_container(self._container)
            router = _resolve_router_from_container(self._container)
            overview = await BillAnalysisFlow(llm=llm, router=router).preview(command.file_path)

            output = {
                "intro": overview.intro,
                "summary": overview.summary,
                "articles": [
                    {
                        "article_id": a.article_id,
                        "title": a.title,
                        "purpose": a.purpose,
                        "key_provisions": a.key_provisions,
                        "practical_consequences": a.practical_consequences,
                    }
                    for a in overview.articles
                ],
            }

            if command.output_path:
                p = Path(command.output_path)
                p.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

            return CommandResult(success=True, data=output)
        except Exception as e:
            return CommandResult.failure(e)


class EvalGoldSetHandler(CommandHandler[EvalGoldSetCommand, list[Any]]):
    """Handle gold-set evaluation."""

    def __init__(self, container: ContainerProtocol) -> None:
        self._container = container

    async def handle(self, command: EvalGoldSetCommand) -> CommandResult[list[Any]]:
        try:
            from pathlib import Path

            from leggie.application.workflow.bill_analysis_flow import BillAnalysisFlow
            from leggie.infrastructure.persistence.eval_harness import EvalScorer, GoldSet

            gold_set = GoldSet(command.gold_set_path)
            llm = _resolve_llm_from_container(self._container)
            router = _resolve_router_from_container(self._container)

            results = []
            for bill_id in gold_set.bill_ids:
                gold_set.get_labels(bill_id)
                bill_path = _find_bill_file(bill_id, Path(command.gold_set_path).parent)
                if bill_path and llm:
                    cove = _resolve_cove_from_container(self._container)
                    flow = BillAnalysisFlow(llm=llm, router=router, cove=cove)
                    findings, _ = await flow.run(bill_path)
                else:
                    findings = []

                scorer = EvalScorer(gold_set)
                result = scorer.score(bill_id, findings)
                results.append(result.to_dict())
                print(
                    f"  {bill_id}: {result.total_gold} gold, {len(findings)} findings, "
                    f"P={result.precision:.2f} R={result.recall:.2f}"
                )

            if command.results_path:
                p = Path(command.results_path)
                p.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

            return CommandResult(success=True, data=results)
        except Exception as e:
            return CommandResult.failure(e)


def _find_bill_file(bill_id: str, search_dir: Path) -> Path | None:
    """Find a bill file matching the gold set bill_id."""
    if not search_dir.exists():
        return None
    for ext in [".pdf", ".txt", ".docx", ".html"]:
        for f in search_dir.glob(f"*{bill_id}*{ext}"):
            return f
    return None


class ReplayRunHandler(CommandHandler["ReplayRunCommand", dict[str, object]]):
    """Handle run replay from the SQLite event store (PROD-06d)."""

    def __init__(self, container: ContainerProtocol) -> None:
        self._container = container

    async def handle(self, command: ReplayRunCommand) -> CommandResult[dict[str, object]]:
        try:
            # Resolve event store from container. Duck-typed capability check
            # instead of isinstance(store, SqliteEventStore) — replay() isn't on
            # EventBusPort (InMemoryEventBus has no durable log to replay), and
            # container.get() already returns Any, so nothing here needs the
            # concrete type.
            store = self._container.get(EventBusPort)

            if not hasattr(store, "replay"):
                return CommandResult(
                    success=False,
                    error="Replay requires a SQLite event store (set LEGGIE_DB__URL=sqlite:///leggie.db)",
                    error_type="ConfigurationError",
                )

            events = store.replay(command.run_id)
            if not events:
                return CommandResult(
                    success=False,
                    error=f"No events found for run '{command.run_id}'",
                    error_type="RunNotFoundError",
                )

            # Build a replay summary from events
            findings_created = sum(1 for e in events if str(e.event_type) == "finding_created")
            findings_refuted = sum(1 for e in events if str(e.event_type) == "finding_refuted")
            completed = any(str(e.event_type) == "workflow_completed" for e in events)
            failed = any(str(e.event_type) == "workflow_failed" for e in events)

            summary: dict[str, object] = {
                "run_id": command.run_id,
                "event_count": len(events),
                "findings_created": findings_created,
                "findings_refuted": findings_refuted,
                "findings_net": findings_created - findings_refuted,
                "status": "failed" if failed else ("completed" if completed else "incomplete"),
            }

            if command.verify:
                # Full verify (diff against stored findings JSON) is manifest-backed future work
                summary["verify"] = "not_implemented"

            return CommandResult(success=True, data=summary)
        except Exception as e:
            return CommandResult.failure(e)
