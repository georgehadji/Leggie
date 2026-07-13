"""BillOverviewGenerator — Stage 0 preview, run before ingest/analyze.

Produces a short intro + summary of the whole bill and, per article
(Άρθρο), its purpose / key provisions / practical consequences. The
caller uses this to decide which article IDs to actually send into the
deep multi-lens analysis (Orchestrator.analyze_article).

No genuine constitutional/legal findings here — this is descriptive,
not evaluative. Falls back to a minimal deterministic overview when no
LLM is configured, so preview() still works offline.
"""

from __future__ import annotations

import asyncio

from leggie.application.agents.prompts.overview import (
    ARTICLE_SYSTEM_PROMPT,
    ARTICLE_USER_PROMPT_TEMPLATE,
    BILL_SYSTEM_PROMPT,
    BILL_USER_PROMPT_TEMPLATE,
)
from leggie.application.ports.llm import LLMPort, LLMRequest
from leggie.domain.models import Article, ArticleOverview, BillOverview, Document
from leggie.domain.models.structured_output import ArticleOverviewCandidate, BillIntroSummary

_DEFAULT_MAX_CONCURRENT = 10


class BillOverviewGenerator:
    """Generates the whole-bill + per-article preview ahead of lens analysis."""

    def __init__(
        self,
        llm: LLMPort | None = None,
        model: str = "openai/gpt-4o-mini",
        max_concurrent: int = _DEFAULT_MAX_CONCURRENT,
    ) -> None:
        self._llm = llm
        self._model = model
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def generate(self, document: Document) -> BillOverview:
        intro, summary = await self._generate_bill_summary(document)
        articles = await self._generate_article_overviews(document)
        return BillOverview(intro=intro, summary=summary, articles=articles)

    async def _generate_bill_summary(self, document: Document) -> tuple[str, str]:
        if not self._llm:
            return self._fallback_bill_summary(document)
        table_of_contents = "\n".join(f"Άρθρο {a.id}: {a.title}" for a in document.articles)
        prompt = BILL_USER_PROMPT_TEMPLATE.format(
            title=document.title,
            preamble=document.preamble[:2000],
            table_of_contents=table_of_contents,
        )
        try:
            request = LLMRequest(
                prompt=prompt,
                system_prompt=BILL_SYSTEM_PROMPT,
                model=self._model or None,
                response_format={"type": "json_object"},
            )
            result, _ = await self._llm.generate_structured(request, BillIntroSummary)
            if result is None:
                return self._fallback_bill_summary(document)
            return result.intro, result.summary
        except Exception:
            return self._fallback_bill_summary(document)

    def _fallback_bill_summary(self, document: Document) -> tuple[str, str]:
        intro = f"Το νομοσχέδιο «{document.title}» περιλαμβάνει {len(document.articles)} άρθρα."
        summary = "Δεν ήταν διαθέσιμο μοντέλο γλώσσας για την παραγωγή περιληπτικής ανάλυσης."
        return intro, summary

    async def _generate_article_overviews(self, document: Document) -> list[ArticleOverview]:
        async def _one(article: Article) -> ArticleOverview:
            async with self._semaphore:
                return await self._generate_article_overview(article)

        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_one(a)) for a in document.articles]
        return [t.result() for t in tasks]

    async def _generate_article_overview(self, article: Article) -> ArticleOverview:
        if not self._llm:
            return self._fallback_article_overview(article)
        prompt = ARTICLE_USER_PROMPT_TEMPLATE.format(
            article_id=article.id,
            article_title=article.title,
            article_text=article.raw_text,
        )
        try:
            request = LLMRequest(
                prompt=prompt,
                system_prompt=ARTICLE_SYSTEM_PROMPT,
                model=self._model or None,
                response_format={"type": "json_object"},
            )
            result, _ = await self._llm.generate_structured(request, ArticleOverviewCandidate)
            if result is None:
                return self._fallback_article_overview(article)
            return ArticleOverview(
                article_id=article.id,
                title=article.title,
                purpose=result.purpose,
                key_provisions=result.key_provisions,
                practical_consequences=result.practical_consequences,
            )
        except Exception:
            return self._fallback_article_overview(article)

    def _fallback_article_overview(self, article: Article) -> ArticleOverview:
        return ArticleOverview(article_id=article.id, title=article.title)
