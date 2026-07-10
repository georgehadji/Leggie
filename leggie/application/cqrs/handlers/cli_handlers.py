"""CLI Command Handlers — implement CLI commands through the CQRS mediator.

These handlers orchestrate infrastructure adapters behind the command interface,
keeping the interface layer (CLI) thin and testable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from leggie.application.cqrs.base import CommandHandler, CommandResult
from leggie.application.cqrs.commands.cli_commands import (
    AnalyzeBillCommand,
    EvalGoldSetCommand,
    ParseDocumentCommand,
)
from leggie.application.ports.citation_parser import CitationParserPort
from leggie.application.ports.llm import LLMPort
from leggie.application.ports.router import RouterPort
from leggie.application.services.cove_verifier import CoVeVerifier

if TYPE_CHECKING:
    from leggie.infrastructure.budget_guard import BudgetGuard
    from leggie.infrastructure.container import Container


class ParseDocumentHandler(CommandHandler[ParseDocumentCommand, dict[str, Any]]):
    """Handle bill document parsing."""

    def __init__(self, container: Container | None = None) -> None:
        self._container = container

    async def handle(self, command: ParseDocumentCommand) -> CommandResult[dict[str, Any]]:
        try:
            from leggie.infrastructure.ingest import IngestorFactory
            from leggie.infrastructure.parse import DocumentParser

            text = await IngestorFactory.ingest(Path(command.file_path))
            parser = DocumentParser()
            doc = parser.parse(text, title=Path(command.file_path).stem)
            citations = parser.extract_citations(text)

            output = {
                "title": doc.title,
                "articles": [
                    {
                        "id": a.id,
                        "title": a.title,
                        "paragraphs": [
                            {"number": p.number, "text": p.text[:200]}
                            for p in a.paragraphs
                        ],
                    }
                    for a in doc.articles
                ],
                "citations": citations,
            }

            if command.output_path:
                p = Path(command.output_path)
                p.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

            return CommandResult(success=True, data=output)
        except Exception as e:
            return CommandResult(success=False, error=str(e))


class AnalyzeBillHandler(CommandHandler[AnalyzeBillCommand, str]):
    """Handle bill analysis using the BillAnalysisFlow."""

    def __init__(self, container: Container | None = None) -> None:
        self._container = container

    async def handle(self, command: AnalyzeBillCommand) -> CommandResult[str]:
        try:
            from leggie.application.workflow.bill_analysis_flow import BillAnalysisFlow

            llm = self._resolve_llm()
            router = self._resolve_router()
            cove = self._resolve_cove()
            findings, reports = await BillAnalysisFlow(llm=llm, router=router, cove=cove).run(
                command.file_path, lenses=command.lenses, checkpoint_path=command.checkpoint_path
            )

            summary = f"Analysis complete: {len(findings)} finding(s), {len(reports)} report(s)"
            for f in findings:
                summary += f"\n  - [{f.finding_type.value}:{f.severity.value}] {f.irac.issue[:80]}"

            return CommandResult(success=True, data=summary)
        except Exception as e:
            return CommandResult(success=False, error=str(e))

    def _resolve_llm(self) -> LLMPort | None:
        """Resolve LLMPort from container, or fall back to ad-hoc factory."""
        if self._container is not None:
            if self._container.has_binding(LLMPort):
                llm: LLMPort = self._container.get(LLMPort)
                return llm
            return None
        return _try_get_llm()

    def _resolve_router(self) -> RouterPort | None:
        """Resolve RouterPort from container, or fall back to ad-hoc factory."""
        if self._container is not None:
            if self._container.has_binding(RouterPort):
                router: RouterPort = self._container.get(RouterPort)
                return router
            return None
        return _try_get_router()

    def _resolve_cove(self) -> CoVeVerifier:
        """Resolve a CoVeVerifier wired with LLM + router for factored CoVe.

        LLM + router enable the full 4-step Chain-of-Verification; the citation
        parser (when bound) adds deterministic citation resolution on top.
        """
        llm = self._resolve_llm()
        router = self._resolve_router()
        parser = None
        if self._container is not None and self._container.has_binding(CitationParserPort):
            parser = self._container.get(CitationParserPort) or None
        return CoVeVerifier(citation_parser=parser, llm=llm, router=router)


def _try_get_llm() -> LLMPort | None:
    """Try to build an LLM port from settings. Returns None if no API key."""
    from leggie.config.settings import get_settings
    s = get_settings()
    if not s.llm.openrouter_api_key:
        return None
    from leggie.infrastructure.llm import LLMAdapter
    from leggie.infrastructure.llm.decorators import BudgetGuardDecorator
    # validate_on_init=True (default) checks against offline allowlist at init
    adapter: LLMPort = LLMAdapter(
        openrouter_key=s.llm.openrouter_api_key,
        default_model=s.llm.openrouter_default_model,
    )
    # Wrap with budget guard if configured (EN2)
    budget_guard = _try_get_budget_guard()
    if budget_guard:
        adapter = BudgetGuardDecorator(adapter, budget_guard)
    return adapter


def _try_get_router() -> RouterPort | None:
    """Try to build a StaticRouter from settings. Returns None if routes YAML missing."""
    from pathlib import Path
    routes_path = Path("config/routes.yaml")
    if not routes_path.exists():
        return None
    from leggie.infrastructure.router import StaticRouter
    return StaticRouter(rules_path=str(routes_path))


def _try_get_budget_guard() -> BudgetGuard | None:
    """Try to build a BudgetGuard from settings. Returns None if budget disabled."""
    from leggie.config.settings import get_settings
    s = get_settings()
    if s.budget.max_cost_per_run <= 0:
        return None
    from leggie.infrastructure.budget_guard import BudgetGuard
    return BudgetGuard(
        max_tokens=s.budget.max_tokens_per_run,
        max_cost=s.budget.max_cost_per_run,
    )


class EvalGoldSetHandler(CommandHandler[EvalGoldSetCommand, list[Any]]):
    """Handle gold-set evaluation."""

    def __init__(self, container: Container | None = None) -> None:
        self._container = container

    async def handle(self, command: EvalGoldSetCommand) -> CommandResult[list[Any]]:
        try:
            import json
            from pathlib import Path

            from leggie.infrastructure.persistence.eval_harness import EvalScorer, GoldSet

            gold_set = GoldSet(command.gold_set_path)
            llm = self._resolve_llm() if hasattr(self, '_resolve_llm') else _try_get_llm()
            router = self._resolve_router() if hasattr(self, '_resolve_router') else _try_get_router()

            results = []
            for bill_id in gold_set.bill_ids:
                gold_set.get_labels(bill_id)
                # Try to find a bill file matching this bill_id
                bill_path = _find_bill_file(bill_id, Path(command.gold_set_path).parent)
                if bill_path and llm:
                    from leggie.application.workflow.bill_analysis_flow import BillAnalysisFlow
                    cove = self._resolve_cove() if hasattr(self, '_resolve_cove') else None
                    flow = BillAnalysisFlow(llm=llm, router=router, cove=cove)
                    findings, _ = await flow.run(bill_path)
                else:
                    findings = []

                scorer = EvalScorer(gold_set)
                result = scorer.score(bill_id, findings)
                results.append(result.to_dict())
                print(f"  {bill_id}: {result.total_gold} gold, {len(findings)} findings, "
                      f"P={result.precision:.2f} R={result.recall:.2f}")

            if command.results_path:
                p = Path(command.results_path)
                p.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

            return CommandResult(success=True, data=results)
        except Exception as e:
            return CommandResult(success=False, error=str(e))


def _find_bill_file(bill_id: str, search_dir: Path) -> Path | None:
    """Find a bill file matching the gold set bill_id."""
    if not search_dir.exists():
        return None
    for ext in [".pdf", ".txt", ".docx", ".html"]:
        for f in search_dir.glob(f"*{bill_id}*{ext}"):
            return f
    return None
