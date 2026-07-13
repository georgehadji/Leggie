"""Container binding contract tests — Phase 3.2.

Verifies that every port registered by configure_defaults() resolves to a
correctly typed adapter that satisfies the port's contract, preventing
invalid bindings like StatePort → InMemoryEventBus.
"""

from __future__ import annotations

import pytest

from leggie.application.ports.blackboard import BlackboardPort
from leggie.application.ports.citation_parser import CitationParserPort
from leggie.application.ports.event_bus import EventBusPort
from leggie.application.ports.ingest import IngestPort
from leggie.application.ports.llm import LLMPort
from leggie.application.ports.parse import ParsePort
from leggie.application.ports.retrieval import RetrievalPort
from leggie.application.ports.reranker import RerankerPort
from leggie.application.ports.router import RouterPort
from leggie.application.ports.state import StatePort
from leggie.domain.models import WorkflowState
from leggie.infrastructure.container import Container


@pytest.fixture
def container() -> Container:
    c = Container()
    c.configure_defaults()
    return c


class TestContainerBindings:
    """Verify all default port bindings resolve correctly."""

    def test_event_bus_port_resolves(self, container: Container):
        bus = container.get(EventBusPort)
        from leggie.infrastructure.persistence import InMemoryEventBus
        assert isinstance(bus, InMemoryEventBus)

    def test_state_port_resolves_and_supports_contract(self, container: Container):
        """StatePort must resolve to an object that satisfies all four methods."""
        import inspect
        store = container.get(StatePort)
        # Check the async methods exist
        assert hasattr(store, "get_state")
        assert hasattr(store, "set_state")
        assert hasattr(store, "get_checkpoint")
        assert hasattr(store, "save_checkpoint")

        # Actually call them (in-memory, no side effects)
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(store.set_state("test-run", WorkflowState.IDLE))
            state = loop.run_until_complete(store.get_state("test-run"))
            assert state == WorkflowState.IDLE

            loop.run_until_complete(store.save_checkpoint("test-run", "ingest", {"key": "val"}))
            data = loop.run_until_complete(store.get_checkpoint("test-run", "ingest"))
            assert data == {"key": "val"}
        finally:
            loop.close()

    def test_ingest_port_resolves(self, container: Container):
        ingest = container.get(IngestPort)
        from leggie.infrastructure.ingest_adapter import IngestAdapter
        assert isinstance(ingest, IngestAdapter)

    def test_parse_port_resolves(self, container: Container):
        parse = container.get(ParsePort)
        from leggie.infrastructure.parse_adapter import ParseAdapter
        assert isinstance(parse, ParseAdapter)

    def test_citation_parser_port_resolves(self, container: Container):
        parser = container.get(CitationParserPort)
        from leggie.infrastructure.citation import GreekCitationParser
        assert isinstance(parser, GreekCitationParser)

    def test_blackboard_port_resolves(self, container: Container):
        board = container.get(BlackboardPort)
        from leggie.infrastructure.blackboard_adapter import BlackboardAdapter
        assert isinstance(board, BlackboardAdapter)

    def test_retrieval_port_resolves(self, container: Container):
        retrieval = container.get(RetrievalPort)
        from leggie.infrastructure.retrieval_adapter import SimpleRetrievalAdapter
        assert isinstance(retrieval, SimpleRetrievalAdapter)

    def test_router_port_resolves(self, container: Container):
        router = container.get(RouterPort)
        from leggie.infrastructure.router import StaticRouter
        assert isinstance(router, StaticRouter)

    def test_reranker_port_is_bound(self, container: Container):
        """RerankerPort is registered in the container.

        Resolution requires an OpenRouter API key; the binding exists
        but is only resolved when settings.analysis.reranker == 'model'.
        """
        assert container.has_binding(RerankerPort)

    def test_llm_port_is_bound(self, container: Container):
        """LLMPort is registered in the container.

        Resolution requires an OpenRouter API key. Skip if not configured.
        """
        import os
        if not os.environ.get("LEGGIE_LLM__OPENROUTER_API_KEY"):
            pytest.skip("OpenRouter API key not configured")
        assert container.has_binding(LLMPort)
        llm = container.get(LLMPort)
        # May be wrapped in BudgetGuardDecorator, so check it's adapter-like
        assert hasattr(llm, "generate")
