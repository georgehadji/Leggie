"""D21 regression — the lens path must honour its configured max_tokens.

Before this fix, `Orchestrator._run_lens_with_cascade` resolved the
`lens_analysis` route (model, tier, cascade) but **discarded**
`RouteResult.max_tokens`. Lenses then built `LLMRequest` without a
`max_tokens`, silently taking the dataclass default of 4096 while
`config/routes.yaml` declared 6144 — a third less headroom than configured.

That mismatch is what produced the 4096-token truncation observed live in
`full5_v4` (docs/SMOKE_AUDIT_V3.md §4a), a ceiling matching no configured
route.
"""

from __future__ import annotations

import pytest

from leggie.application.agents.constitutional_lens import ConstitutionalLens
from leggie.application.agents.lens import DEFAULT_LENS_MAX_TOKENS
from leggie.application.agents.orchestrator import Orchestrator
from leggie.application.ports.llm import LLMPort, LLMRequest, LLMResponse
from leggie.application.ports.router import RouteResult, RouterPort
from leggie.domain.models import Article, ModelTier

ROUTE_CEILING = 6144  # what config/routes.yaml declares for lens_analysis
CASCADE_CEILING = 8192

ARTICLE = Article(
    id="1",
    raw_text=(
        "Άρθρο 1: Εξουσιοδότηση για έκδοση π.δ. και περιορισμός "
        "προσωπικών δεδομένων για την προστασία του απορρήτου."
    ),
)


class RecordingLLM(LLMPort):
    """Captures every LLMRequest so the ceiling actually sent is assertable."""

    def __init__(self, payload: str = '{"findings": []}') -> None:
        self.requests: list[LLMRequest] = []
        self._payload = payload

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(
            content=self._payload,
            model=request.model or "fake",
            tier_used=ModelTier.BUDGET,
            usage={"prompt_tokens": 1, "completion_tokens": 1},
            finish_reason="stop",
        )

    async def generate_structured(self, request: LLMRequest, schema: type):
        self.requests.append(request)
        return schema(findings=[]), LLMResponse(
            content=self._payload,
            model=request.model or "fake",
            tier_used=ModelTier.BUDGET,
            usage={"prompt_tokens": 1, "completion_tokens": 1},
            finish_reason="stop",
        )

    async def count_tokens(self, text: str, model: str | None = None) -> int:
        return len(text) // 4 + 1


class StubRouter(RouterPort):
    def __init__(self, max_tokens: int = ROUTE_CEILING, cascade: bool = False) -> None:
        self._max_tokens = max_tokens
        self._cascade = cascade

    async def route(self, task_type: str, budget_remaining: float | None = None) -> RouteResult:
        return RouteResult(
            model="google/gemini-2.5-flash",
            tier=ModelTier.BUDGET,
            max_tokens=self._max_tokens,
            cascade_enabled=self._cascade,
        )

    async def cascade(
        self, task_type: str, current_tier: ModelTier, failure_reason: str | None = None
    ) -> RouteResult | None:
        return RouteResult(
            model="google/gemini-2.5-pro",
            tier=ModelTier.PREMIUM,
            max_tokens=CASCADE_CEILING,
            cascade_enabled=False,
        )

    def supported_models(self) -> list[str]:
        return ["google/gemini-2.5-flash", "google/gemini-2.5-pro"]


class TestLensHonoursRouteMaxTokens:
    @pytest.mark.asyncio
    async def test_orchestrator_threads_route_ceiling_into_lens(self):
        """The resolved RouteResult.max_tokens must reach the LLM request."""
        llm = RecordingLLM()
        orch = Orchestrator(llm=llm, router=StubRouter(), use_verbalized_sampling=False)

        await orch.analyze_article(ARTICLE, lens_names=["constitutional"])

        assert llm.requests, "lens made no LLM call"
        sent = {r.max_tokens for r in llm.requests}
        assert sent == {ROUTE_CEILING}, (
            f"lens sent {sent}, expected {{{ROUTE_CEILING}}} from the route"
        )
        # The specific regression: never silently fall back to the default.
        assert DEFAULT_LENS_MAX_TOKENS not in sent

    @pytest.mark.asyncio
    async def test_lens_uses_constructor_ceiling_directly(self):
        """A lens built with an explicit ceiling sends that ceiling."""
        llm = RecordingLLM()
        lens = ConstitutionalLens(llm=llm, model="m", max_tokens=ROUTE_CEILING)

        await lens.analyze(ARTICLE)

        assert llm.requests
        assert all(r.max_tokens == ROUTE_CEILING for r in llm.requests)

    @pytest.mark.asyncio
    async def test_verbalized_sampling_shares_the_lens_ceiling(self):
        """The VS path built its own LLMRequest and also dropped the ceiling."""
        llm = RecordingLLM(payload='{"findings": []}')
        lens = ConstitutionalLens(
            llm=llm, model="m", max_tokens=ROUTE_CEILING, use_verbalized_sampling=True
        )

        await lens.analyze(ARTICLE)

        assert llm.requests, "VS path made no LLM call"
        assert all(r.max_tokens == ROUTE_CEILING for r in llm.requests)

    @pytest.mark.asyncio
    async def test_default_applies_only_when_no_router(self):
        """Without a router there is no configured ceiling to honour."""
        llm = RecordingLLM()
        orch = Orchestrator(llm=llm, router=None)

        await orch.analyze_article(ARTICLE, lens_names=["constitutional"])

        assert llm.requests
        assert all(r.max_tokens == DEFAULT_LENS_MAX_TOKENS for r in llm.requests)
