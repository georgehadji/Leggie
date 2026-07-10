"""Orchestrator — deterministic article decomposition and lens dispatch.

Decomposes a parsed document into lens analysis tasks, dispatches them
to lens workers (Strategy pattern), and collects findings.

Per ARCHITECTURE.md §2: Orchestrator-worker pattern. Orchestrator is
thin and deterministic — no LLM decides the pipeline.

EN1: Uses RouterPort for per-lens model selection with cascade on failure.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

from leggie.application.agents.constitutional_lens import ConstitutionalLens
from leggie.application.agents.economic_lens import EconomicLens
from leggie.application.agents.eu_gdpr_lens import EUGDPRLens
from leggie.application.agents.implementation_lens import ImplementationLens
from leggie.application.agents.legal_coherence_lens import LegalCoherenceLens
from leggie.application.agents.lens import Lens
from leggie.application.ports.llm import LLMPort
from leggie.application.ports.router import RouterPort
from leggie.domain.models import Article, Document, Event, EventType, Finding, LensTask, ModelTier

log = logging.getLogger(__name__)

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
    EN1: model per lens via RouterPort, cascade on failure.
    """

    def __init__(
        self,
        llm: LLMPort | None = None,
        model: str = "google/gemini-2.5-flash",
        lens_config: dict[str, type[Lens]] | None = None,
        max_concurrent: int = _DEFAULT_MAX_CONCURRENT,
        on_degradation: Callable[..., None] | None = None,
        router: RouterPort | None = None,
    ) -> None:
        self._llm = llm
        self._model = model
        self._lens_classes = lens_config or _DEFAULT_LENSES
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._on_degradation = on_degradation
        self._router = router

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

        Uses RouterPort for per-lens model selection (EN1).
        Falls back to self._model if no router configured.

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
                return await self._run_lens_with_cascade(lens_cls, name, article)

        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_run_lens(name)) for name in lens_names]

        findings: list[Finding] = []
        for t in tasks:
            findings.extend(t.result())
        return findings

    async def _run_lens_with_cascade(
        self,
        lens_cls: type[Lens],
        name: str,
        article: Article,
    ) -> list[Finding]:
        """Run a lens with router model selection and cascade retry (EN1).

        1. Query router for (model, tier, cascade_enabled)
        2. Run lens with that model
        3. On failure/empty: cascade to next tier if available
        """
        model = self._model
        tier = ModelTier.BUDGET
        max_retries = 1

        if self._router:
            try:
                result = await self._router.route(f"lens_{name}")
                model = result.model
                tier = result.tier
                max_retries = 2 if result.cascade_enabled else 1
            except Exception:
                log.warning("route_failed: lens=%s using default model", name)

        for attempt in range(max_retries):
            try:
                lens = lens_cls(llm=self._llm, model=model,
                                on_degradation=self._on_degradation)
                findings = await lens.analyze(article)
                if findings:
                    return findings
                # Empty findings from LLM lens: cascade on low confidence
                if attempt < max_retries - 1 and self._router:
                    next_result = await self._router.cascade(
                        f"lens_{name}", tier, "empty_findings")
                    if next_result:
                        model = next_result.model
                        tier = next_result.tier
                        log.info("cascade: %s %s → %s (empty)", name, tier.value, model)
                        continue
                return findings
            except Exception as e:
                log.error("lens_crash: %s article=%s error=%s", name, article.id, str(e),
                          exc_info=True)
                if self._on_degradation:
                    with contextlib.suppress(Exception):
                        self._on_degradation(Event(
                            event_type=EventType.DEGRADED,
                            aggregate_id=f"orchestrator:lens:{name}:article:{article.id}",
                            data={"lens": name, "article_id": article.id,
                                  "error": str(e)[:500], "model": model},
                        ))
                # Cascade to next tier on failure
                if attempt < max_retries - 1 and self._router:
                    next_result = await self._router.cascade(
                        f"lens_{name}", tier, str(e)[:200])
                    if next_result:
                        model = next_result.model
                        tier = next_result.tier
                        log.info("cascade: %s %s → %s (failure)", name, tier.value, model)
                        continue
                return []
        return []

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
