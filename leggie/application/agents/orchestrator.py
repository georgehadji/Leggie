"""Orchestrator — deterministic article decomposition and lens dispatch.

Decomposes a parsed document into lens analysis tasks, dispatches them
to lens workers (Strategy pattern), and collects findings.

Per ARCHITECTURE.md §2: Orchestrator-worker pattern. Orchestrator is
thin and deterministic — no LLM decides the pipeline.

Phase 2+: parallel fan-out with asyncio.TaskGroup + semaphore.
"""

from __future__ import annotations

import asyncio

from leggie.application.agents.constitutional_lens import ConstitutionalLens
from leggie.application.agents.economic_lens import EconomicLens
from leggie.application.agents.eu_gdpr_lens import EUGDPRLens
from leggie.application.agents.implementation_lens import ImplementationLens
from leggie.application.agents.legal_coherence_lens import LegalCoherenceLens
from leggie.application.agents.lens import Lens
from leggie.application.ports.llm import LLMPort
from leggie.domain.models import Article, Document, Finding, LensTask

# All 5 lenses for Phase 2 Ensemble
_DEFAULT_LENSES: dict[str, type[Lens]] = {
    "constitutional": ConstitutionalLens,
    "legal_coherence": LegalCoherenceLens,
    "economic": EconomicLens,
    "implementation": ImplementationLens,
    "eu_gdpr": EUGDPRLens,
}

_DEFAULT_MAX_CONCURRENT = 10


class Orchestrator:
    """Orchestrates article decomposition and lens analysis.

    Phase 1: single lens, sequential.
    Phase 2: 5 lenses, parallel fan-out with bounded concurrency.
    """

    def __init__(
        self,
        llm: LLMPort | None = None,
        model: str = "google/gemini-2.5-flash:free",
        lens_config: dict[str, type[Lens]] | None = None,
        max_concurrent: int = _DEFAULT_MAX_CONCURRENT,
    ) -> None:
        self._llm = llm
        self._model = model
        self._lens_classes = lens_config or _DEFAULT_LENSES
        self._semaphore = asyncio.Semaphore(max_concurrent)

    def decompose(self, document: Document) -> list[LensTask]:
        """Decompose a document into lens analysis tasks.

        Deterministic — produces one task per (lens, article) pair.
        No LLM involved.
        """
        tasks: list[LensTask] = []
        for article in document.articles:
            for lens_name in self._lens_classes:
                tasks.append(
                    LensTask(
                        lens=lens_name,
                        article_id=article.id,
                        sample_count=1,
                    )
                )
        return tasks

    async def analyze_article(
        self,
        article: Article,
        lens_names: list[str] | None = None,
    ) -> list[Finding]:
        """Analyze a single article through specified lenses (parallel dispatch).

        Phase 2: uses asyncio.TaskGroup with semaphore for bounded
        parallel lens execution.

        Args:
            article: The article to analyze.
            lens_names: Lenses to apply. Defaults to all configured lenses.

        Returns:
            Combined list of findings from all lenses.
        """
        if lens_names is None:
            lens_names = list(self._lens_classes.keys())

        async def _run_lens(name: str) -> list[Finding]:
            lens_cls = self._lens_classes.get(name)
            if lens_cls is None:
                return []
            async with self._semaphore:
                try:
                    lens = lens_cls(llm=self._llm, model=self._model)
                    return await lens.analyze(article)
                except Exception as e:
                    import logging
                    log = logging.getLogger(__name__)
                    log.warning("lens_failed: %s article=%s error=%s", name, article.id, str(e))
                    return []

        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_run_lens(name)) for name in lens_names]

        findings: list[Finding] = []
        for t in tasks:
            findings.extend(t.result())
        return findings

    async def analyze_document(
        self,
        document: Document,
        lens_names: list[str] | None = None,
    ) -> list[Finding]:
        """Analyze a full document through all specified lenses (parallel articles).

        Phase 2: parallel article fan-out with asyncio.TaskGroup + semaphore.
        Each article's lens dispatch is itself parallel within.
        """
        if lens_names is None:
            lens_names = list(self._lens_classes.keys())

        async def _analyze_article(article: Article) -> list[Finding]:
            return await self.analyze_article(article, lens_names)

        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_analyze_article(a)) for a in document.articles]

        all_findings: list[Finding] = []
        for t in tasks:
            all_findings.extend(t.result())
        return all_findings

    @property
    def supported_lenses(self) -> list[str]:
        """Return list of lens names this orchestrator can dispatch."""
        return list(self._lens_classes.keys())
