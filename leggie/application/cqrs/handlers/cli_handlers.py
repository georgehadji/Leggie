"""CLI Command Handlers — implement CLI commands through the CQRS mediator.

These handlers orchestrate infrastructure adapters behind the command interface,
keeping the interface layer (CLI) thin and testable.
"""

from __future__ import annotations

import json
from pathlib import Path

from leggie.application.cqrs.base import CommandHandler, CommandResult
from leggie.application.cqrs.commands.cli_commands import (
    AnalyzeBillCommand,
    EvalGoldSetCommand,
    ParseDocumentCommand,
)


class ParseDocumentHandler(CommandHandler[ParseDocumentCommand, dict]):
    """Handle bill document parsing."""

    async def handle(self, command: ParseDocumentCommand) -> CommandResult[dict]:
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

            flow = BillAnalysisFlow()
            findings = await flow.run(command.file_path)

            summary = f"Analysis complete: {len(findings)} finding(s) found"
            for f in findings:
                summary += f"\n  - [{f.finding_type.value}] {f.irac.issue[:80]}"

            return CommandResult(success=True, data=summary)
        except Exception as e:
            return CommandResult(success=False, error=str(e))


class EvalGoldSetHandler(CommandHandler[EvalGoldSetCommand, list]):
    """Handle gold-set evaluation."""

    async def handle(self, command: EvalGoldSetCommand) -> CommandResult[list]:
        try:
            from leggie.infrastructure.persistence.eval_harness import GoldSet, EvalScorer

            gold_set = GoldSet(command.gold_set_path)
            scorer = EvalScorer(gold_set)
            results = []
            for bill_id in gold_set.bill_ids:
                result = scorer.score(bill_id, [])
                results.append(result.to_dict())

            if command.results_path:
                p = Path(command.results_path)
                p.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

            return CommandResult(success=True, data=results)
        except Exception as e:
            return CommandResult(success=False, error=str(e))
