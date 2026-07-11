"""CLI Command Handlers — implement CLI commands through the CQRS mediator.

These handlers orchestrate infrastructure adapters behind the command interface,
keeping the interface layer (CLI) thin and testable.

D8: The DI container is the single composition root. Handlers receive a
configured container and resolve all adapters through it; no ad-hoc factories.
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
from leggie.application.ports.reranker import RerankerPort
from leggie.application.ports.router import RouterPort
from leggie.application.services.cove_verifier import CoVeVerifier

if TYPE_CHECKING:
    from leggie.infrastructure.container import Container


class ParseDocumentHandler(CommandHandler[ParseDocumentCommand, dict[str, Any]]):
    """Handle bill document parsing."""

    def __init__(self, container: Container) -> None:
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

    def __init__(self, container: Container) -> None:
        self._container = container

    async def handle(self, command: AnalyzeBillCommand) -> CommandResult[str]:
        try:
            from leggie.application.workflow.bill_analysis_flow import BillAnalysisFlow
            from leggie.config.settings import get_settings
            from leggie.infrastructure.persistence.checkpoint_store import CheckpointStore

            llm = self._resolve_llm()
            router = self._resolve_router()
            cove = self._resolve_cove()
            settings = get_settings()
            reranker_port = None
            if self._container.has_binding(RerankerPort):
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
                use_verbalized_sampling=command.use_verbalized_sampling,
                reranker_name=settings.analysis.reranker,
                reranker_port=reranker_port,
            )
            findings, reports = await flow.run(
                command.file_path,
                lenses=command.lenses,
            )

            summary = f"Analysis complete: {len(findings)} finding(s), {len(reports)} report(s)"
            for f in findings:
                summary += f"\n  - [{f.finding_type.value}:{f.severity.value}] {f.irac.issue[:80]}"

            return CommandResult(success=True, data=summary)
        except Exception as e:
            return CommandResult(success=False, error=str(e))

    def _resolve_llm(self) -> LLMPort | None:
        """Resolve LLMPort from the container."""
        if self._container.has_binding(LLMPort):
            llm: LLMPort = self._container.get(LLMPort)
            return llm
        return None

    def _resolve_router(self) -> RouterPort | None:
        """Resolve RouterPort from the container."""
        if self._container.has_binding(RouterPort):
            router: RouterPort = self._container.get(RouterPort)
            return router
        return None

    def _resolve_cove(self) -> CoVeVerifier:
        """Resolve a CoVeVerifier wired with LLM + router + citation parser."""
        llm = self._resolve_llm()
        router = self._resolve_router()
        parser = None
        if self._container.has_binding(CitationParserPort):
            parser = self._container.get(CitationParserPort) or None
        return CoVeVerifier(citation_parser=parser, llm=llm, router=router)


class EvalGoldSetHandler(CommandHandler[EvalGoldSetCommand, list[Any]]):
    """Handle gold-set evaluation."""

    def __init__(self, container: Container) -> None:
        self._container = container

    async def handle(self, command: EvalGoldSetCommand) -> CommandResult[list[Any]]:
        try:
            from pathlib import Path

            from leggie.application.workflow.bill_analysis_flow import BillAnalysisFlow
            from leggie.infrastructure.persistence.eval_harness import EvalScorer, GoldSet

            gold_set = GoldSet(command.gold_set_path)
            llm = self._resolve_llm()
            router = self._resolve_router()

            results = []
            for bill_id in gold_set.bill_ids:
                gold_set.get_labels(bill_id)
                # Try to find a bill file matching this bill_id
                bill_path = _find_bill_file(bill_id, Path(command.gold_set_path).parent)
                if bill_path and llm:
                    cove = self._resolve_cove()
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

    def _resolve_llm(self) -> LLMPort | None:
        if self._container.has_binding(LLMPort):
            llm: LLMPort = self._container.get(LLMPort)
            return llm
        return None

    def _resolve_router(self) -> RouterPort | None:
        if self._container.has_binding(RouterPort):
            router: RouterPort = self._container.get(RouterPort)
            return router
        return None

    def _resolve_cove(self) -> CoVeVerifier:
        llm = self._resolve_llm()
        router = self._resolve_router()
        parser = None
        if self._container.has_binding(CitationParserPort):
            parser = self._container.get(CitationParserPort) or None
        return CoVeVerifier(citation_parser=parser, llm=llm, router=router)


def _find_bill_file(bill_id: str, search_dir: Path) -> Path | None:
    """Find a bill file matching the gold set bill_id."""
    if not search_dir.exists():
        return None
    for ext in [".pdf", ".txt", ".docx", ".html"]:
        for f in search_dir.glob(f"*{bill_id}*{ext}"):
            return f
    return None
