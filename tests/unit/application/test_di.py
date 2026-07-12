"""Tests for DI Container — service-locator pattern."""

import pytest

from leggie.application.ports.citation_parser import CitationParserPort
from leggie.application.ports.event_bus import EventBusPort
from leggie.application.ports.llm import LLMPort
from leggie.application.ports.router import RouterPort
from leggie.infrastructure.container import BindingNotFoundError, Container


class FakeLLM(LLMPort):
    async def generate(self, _request):
        from leggie.application.ports.llm import LLMResponse

        return LLMResponse(content="fake", model="fake", tier_used=None, usage={})

    async def generate_structured(self, request, _schema):
        return (None, await self.generate(request))

    async def count_tokens(self, text, _model=None):
        return len(text)


class TestContainer:
    def test_register_and_get(self):
        container = Container()
        container.register(LLMPort, lambda: FakeLLM())
        llm = container.get(LLMPort)
        assert isinstance(llm, FakeLLM)

    def test_get_returns_singleton(self):
        container = Container()
        container.register(LLMPort, lambda: FakeLLM())
        llm1 = container.get(LLMPort)
        llm2 = container.get(LLMPort)
        assert llm1 is llm2

    def test_register_instance(self):
        container = Container()
        instance = FakeLLM()
        container.register_instance(LLMPort, instance)
        assert container.get(LLMPort) is instance

    def test_unregistered_type_raises(self):
        container = Container()
        with pytest.raises(BindingNotFoundError):
            container.get(LLMPort)

    def test_has_binding_true(self):
        container = Container()
        container.register(LLMPort, lambda: FakeLLM())
        assert container.has_binding(LLMPort) is True

    def test_has_binding_false(self):
        container = Container()
        assert container.has_binding(LLMPort) is False

    def test_has_binding_instance(self):
        container = Container()
        container.register_instance(LLMPort, FakeLLM())
        assert container.has_binding(LLMPort) is True

    def test_clear(self):
        container = Container()
        container.register(LLMPort, lambda: FakeLLM())
        container.clear()
        assert container.has_binding(LLMPort) is False

    def test_configure_defaults_sets_bindings(self):
        container = Container()
        container.configure_defaults()
        assert container.has_binding(EventBusPort) is True
        assert container.has_binding(LLMPort) is True
        assert container.has_binding(RouterPort) is True
        assert container.has_binding(CitationParserPort) is True
