"""CLI Command Handlers — implement CLI commands through the CQRS mediator.

These handlers orchestrate infrastructure adapters behind the command interface,
keeping the interface layer (CLI) thin and testable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from leggie.application.cqrs.base import CommandHandler, CommandResult
from leggie.application.cqrs.commands.cli_commands import (
    AnalyzeBillCommand,
    EvalGoldSetCommand,
    ParseDocumentCommand,
    PreviewBillCommand,
)
from leggie.application.ports.llm import LLMPort


class ParseDocumentHandler(CommandHandler[ParseDocumentCommand, dict[str, Any]]):
    """Handle bill document parsing."""

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

    async def handle(self, command: AnalyzeBillCommand) -> CommandResult[str]:
        try:
            from leggie.application.workflow.bill_analysis_flow import BillAnalysisFlow

            # Try to inject LLM port if key is configured
            llm = _try_get_llm()
            findings, reports = await BillAnalysisFlow(llm=llm).run(
                command.file_path,
                selected_article_ids=command.article_ids,
            )

            summary = f"Analysis complete: {len(findings)} finding(s), {len(reports)} report(s)"
            for f in findings:
                summary += f"\n  - [{f.finding_type.value}:{f.severity.value}] {f.irac.issue[:80]}"

            return CommandResult(success=True, data=summary)
        except Exception as e:
            return CommandResult(success=False, error=str(e))


class PreviewBillHandler(CommandHandler[PreviewBillCommand, dict[str, Any]]):
    """Handle bill preview — Stage 0, before ingest/analyze proper.

    Produces intro, summary, and per-article purpose/key-provisions/
    practical-consequences so the caller can pick which Άρθρο IDs to
    pass into AnalyzeBillCommand.article_ids.
    """

    async def handle(self, command: PreviewBillCommand) -> CommandResult[dict[str, Any]]:
        try:
            from leggie.application.workflow.bill_analysis_flow import BillAnalysisFlow

            llm = _try_get_llm()
            overview = await BillAnalysisFlow(llm=llm).preview(command.file_path)

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
            return CommandResult(success=False, error=str(e))


def _try_get_llm() -> LLMPort | None:
    """Try to build an LLM port from settings. Returns None if no API key."""
    from leggie.config.settings import get_settings
    s = get_settings()
    if not s.llm.openrouter_api_key:
        return None
    from leggie.infrastructure.llm import LLMAdapter
    return LLMAdapter(
        openrouter_key=s.llm.openrouter_api_key,
        default_model=s.llm.openrouter_default_model,
    )


class EvalGoldSetHandler(CommandHandler[EvalGoldSetCommand, list[dict[str, Any]]]):
    """Handle gold-set evaluation."""

    async def handle(self, command: EvalGoldSetCommand) -> CommandResult[list[dict[str, Any]]]:
        try:
            import json
            from pathlib import Path

            from leggie.infrastructure.persistence.eval_harness import EvalScorer, GoldSet

            gold_set = GoldSet(command.gold_set_path)
            llm = _try_get_llm()

            results = []
            for bill_id in gold_set.bill_ids:
                # Try to find a bill file matching this bill_id
                bill_path = _find_bill_file(bill_id, Path(command.gold_set_path).parent)
                if bill_path and llm:
                    from leggie.application.workflow.bill_analysis_flow import BillAnalysisFlow
                    flow = BillAnalysisFlow(llm=llm)
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
