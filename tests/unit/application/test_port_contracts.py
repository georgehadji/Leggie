"""Port contract tests — verify each port has a working fake.

Per BUILD_PLAN §8: "Each Port has a fake + contract test."
"""

import pytest

from leggie.application.ports.blackboard import BlackboardPort
from leggie.application.ports.citation_parser import CitationParserPort
from leggie.application.ports.event_bus import EventBusPort
from leggie.application.ports.llm import LLMPort, LLMRequest, LLMResponse
from leggie.application.ports.retrieval import RetrievalPort
from leggie.application.ports.router import RouteResult, RouterPort
from leggie.application.ports.state import StatePort
from leggie.domain.models import (
    CitationScheme,
    Event,
    EventType,
    ModelTier,
    WorkflowState,
)

# ── Fakes ────────────────────────────────────────────────────────────────────

class FakeLLM(LLMPort):
    async def generate(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            content="fake", model="fake", tier_used=ModelTier.FREE,
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )
    async def generate_structured(self, request, schema):
        return (None, await self.generate(request))
    async def count_tokens(self, text, model=None):
        return len(text) // 4


class FakeRouter(RouterPort):
    async def route(self, task_type, budget_remaining=None):
        return RouteResult(model="fake-model", tier=ModelTier.BUDGET, max_tokens=4096)
    async def cascade(self, task_type, current_tier, failure_reason=None):
        return None
    def supported_models(self):
        return ["fake-model"]


class FakeRetrieval(RetrievalPort):
    async def search(self, query, corpus="default", top_k=10, mode="hybrid"):
        return []
    async def get_document(self, document_id, corpus="default"):
        return None
    async def corpus_stats(self, corpus="default"):
        return {"size": 0}


class FakeCitationParser(CitationParserPort):
    def parse(self, text):
        return []
    async def resolve(self, citation):
        return citation
    def supported_schemes(self):
        return [CitationScheme.FEK]


class FakeEventBus(EventBusPort):
    async def publish(self, event):
        pass
    def subscribe(self, event_type, handler):
        pass
    def unsubscribe(self, event_type, handler):
        pass


class FakeState(StatePort):
    async def get_state(self, run_id):
        return WorkflowState.IDLE
    async def set_state(self, run_id, state):
        pass
    async def get_checkpoint(self, run_id, stage):
        return None
    async def save_checkpoint(self, run_id, stage, data):
        pass


class FakeBlackboard(BlackboardPort):
    async def post_finding(self, entry):
        pass
    async def get_findings(self, round_min=0, agent_id=None):
        return []
    async def get_all_findings(self):
        return []
    async def clear_round(self, round_number):
        pass


# ── Contract Tests ──────────────────────────────────────────────────────────


class TestLLMPortContract:
    def test_fake_satisfies_port(self):
        fake = FakeLLM()
        assert isinstance(fake, LLMPort)

    @pytest.mark.asyncio
    async def test_generate_returns_response(self):
        fake = FakeLLM()
        resp = await fake.generate(LLMRequest(prompt="hello"))
        assert isinstance(resp, LLMResponse)
        assert resp.content == "fake"


class TestRouterPortContract:
    @pytest.mark.asyncio
    async def test_fake_route_returns_result(self):
        fake = FakeRouter()
        result = await fake.route("lens_analysis")
        assert isinstance(result, RouteResult)
        assert result.tier == ModelTier.BUDGET


class TestCitationParserPortContract:
    def test_fake_satisfies_port(self):
        fake = FakeCitationParser()
        assert isinstance(fake, CitationParserPort)


class TestEventBusPortContract:
    @pytest.mark.asyncio
    async def test_fake_publish_no_error(self):
        fake = FakeEventBus()
        event = Event(event_type=EventType.ANALYSIS_STARTED, aggregate_id="test")
        await fake.publish(event)  # Should not raise


class TestStatePortContract:
    @pytest.mark.asyncio
    async def test_fake_get_state(self):
        fake = FakeState()
        state = await fake.get_state("run-1")
        assert state == WorkflowState.IDLE
