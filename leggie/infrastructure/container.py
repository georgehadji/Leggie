"""Dependency Injection container for Leggie.

Centralizes port → adapter bindings. Adapted from weebot's application/di/.
Lives in Infrastructure per Clean Architecture: it wires infrastructure adapters
to application ports (the composition root).

Usage:
    container = Container()
    container.configure_defaults()
    llm = container.get(LLMPort)
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from leggie.application.ports.blackboard import BlackboardPort
from leggie.application.ports.citation_parser import CitationParserPort
from leggie.application.ports.event_bus import EventBusPort
from leggie.application.ports.ingest import IngestPort
from leggie.application.ports.llm import LLMPort
from leggie.application.ports.parse import ParsePort
from leggie.application.ports.reasoner import ReasonerPort
from leggie.application.ports.router import RouterPort
from leggie.application.ports.state import StatePort
from leggie.infrastructure.persistence.checkpoint_store import CheckpointStore

if TYPE_CHECKING:
    from leggie.infrastructure.reasoner.server_manager import ReasonerServerManager

logger = logging.getLogger(__name__)


class BindingNotFoundError(KeyError):
    """Raised when no binding is registered for a port type."""


@dataclass
class Container:
    """Simple service-locator / DI container.

    Bindings are Callable factories (lazy) to avoid instantiating
    adapters that may never be used in a given process.

    Usage:
        container = Container()
        container.register(LLMPort, lambda: LLMAdapter(...))
        llm = container.get(LLMPort)
    """

    _bindings: dict[type | str, Callable[[], Any]] = field(default_factory=dict)
    _singletons: dict[type | str, Any] = field(default_factory=dict)

    # ── registration ────────────────────────────────────────────────

    def register(self, port_type: type, factory: Callable[[], Any]) -> None:
        """Register a lazy factory for *port_type*."""
        self._bindings[port_type] = factory

    def register_instance(self, port_type: type | str, instance: Any) -> None:
        """Register an already-created singleton. Accepts a type or a string
        key for ad-hoc utilities (e.g. "rate_limiter") that have no port."""
        self._singletons[port_type] = instance

    # ── resolution ──────────────────────────────────────────────────

    def get(self, port_type: type | str) -> Any:
        """Resolve *port_type*, creating it once (singleton per type)."""
        if port_type in self._singletons:
            return self._singletons[port_type]

        factory = self._bindings.get(port_type)
        if factory is None:
            name = port_type.__name__ if hasattr(port_type, "__name__") else str(port_type)
            raise BindingNotFoundError(f"No binding registered for {name}")

        instance = factory()
        self._singletons[port_type] = instance
        return instance

    def has_binding(self, port_type: type) -> bool:
        """Check if a binding exists for *port_type*."""
        return port_type in self._bindings or port_type in self._singletons

    def clear(self) -> None:
        """Clear all bindings and singletons."""
        self._bindings.clear()
        self._singletons.clear()

    # ── convenience binders ─────────────────────────────────────────

    def configure_defaults(self) -> None:
        """Wire all defaults for Leggie's core ports."""
        # Event bus — durable (SQLite) when persistence URL is configured,
        # otherwise in-memory for tests and development (PROD-06a/c).
        from leggie.config.settings import get_settings
        s = get_settings()
        if s.persistence.url:
            from leggie.infrastructure.persistence.sqlite_event_store import SqliteEventStore
            db_path = s.persistence.url.replace("sqlite:///", "")
            self.register(EventBusPort, lambda: SqliteEventStore(db_path))
        else:
            from leggie.infrastructure.persistence import InMemoryEventBus
            self.register(EventBusPort, lambda: InMemoryEventBus())

        # LLM adapter (OpenRouter — single API key for all providers)
        # Decorator stack: StructuredOutput → PromptHardening → BudgetGuard → Transport
        # Prompt hardening (PROD-13) wraps the prompt; it must be OUTSIDE
        # BudgetGuard so the hardened prompt's tokens are what get billed,
        # yet INSIDE StructuredOutput so the ladder's repair attempts also benefit.
        def _create_llm() -> LLMPort:
            from leggie.config.settings import get_settings
            from leggie.infrastructure.llm import LLMAdapter, StructuredOutputDecorator
            from leggie.infrastructure.llm.decorators import BudgetGuardDecorator
            s = get_settings()
            rate_limiter = self.get("rate_limiter")
            adapter: LLMPort = LLMAdapter(
                openrouter_key=s.llm.openrouter_api_key,
                openrouter_base_url=s.llm.openrouter_base_url,
                default_model=s.llm.openrouter_default_model,
                rate_limiter=rate_limiter,
            )
            # Wrap with budget guard (EN2)
            if s.budget.max_cost_per_run > 0:
                from leggie.infrastructure.budget_guard import BudgetGuard
                guard = BudgetGuard(
                    max_tokens=s.budget.max_tokens_per_run,
                    max_cost=s.budget.max_cost_per_run,
                )
                adapter = BudgetGuardDecorator(adapter, guard)
            # Wrap with prompt-injection hardening (PROD-13)
            from leggie.infrastructure.llm.prompt_safety import PromptHardeningDecorator
            adapter = PromptHardeningDecorator(adapter)
            # Wrap with structured-output ladder (TOK-1)
            # Placed OUTSIDE the budget guard so each ladder attempt
            # traverses the guard and is billed independently.
            return StructuredOutputDecorator(adapter)
        self.register(LLMPort, _create_llm)

        # Router
        from leggie.config.settings import get_settings
        from leggie.infrastructure.resources import ResourceLocator
        from leggie.infrastructure.router import StaticRouter
        routes_override = get_settings().cascade.rules_path if get_settings().cascade.rules_path != "config/routes.yaml" else None
        locator = ResourceLocator()
        self.register(RouterPort, lambda: StaticRouter(str(locator.routes_path(override=routes_override))))

        # Citation parser with known-good resolution index (D7)
        from leggie.infrastructure.citation import GreekCitationParser
        from leggie.infrastructure.resources import ResourceLocator
        locator = ResourceLocator()
        resolution_index: set[str] = set()
        try:
            index_path = locator.package_resource("leggie.data", "citation_index.json")
            if index_path.exists():
                index_data = json.loads(index_path.read_text(encoding="utf-8"))
                resolution_index = set(index_data.get("identifiers", []))
        except (OSError, ValueError):
            resolution_index = set()
        self.register(
            CitationParserPort,
            lambda: GreekCitationParser(resolution_index=resolution_index),
        )

        # State store — durable (SQLite) when persistence URL is configured,
        # otherwise in-memory (PROD-06b/c).
        if s.persistence.url:
            from leggie.infrastructure.persistence.sqlite_state_store import SqliteStateStore
            self.register(StatePort, lambda: SqliteStateStore(db_path))
        else:
            from leggie.infrastructure.persistence.state_store import InMemoryStateStore
            self.register(StatePort, lambda: InMemoryStateStore())

        # Ingest / Parse adapters
        from leggie.infrastructure.ingest_adapter import IngestAdapter
        from leggie.infrastructure.parse_adapter import ParseAdapter
        self.register(IngestPort, lambda: IngestAdapter())
        self.register(ParsePort, lambda: ParseAdapter())

        # Rate limiter for LLM calls
        from leggie.config.settings import get_settings
        from leggie.infrastructure.rate_limiter import RateLimiter
        rate = get_settings().llm.max_rate_per_second
        self.register_instance("rate_limiter", RateLimiter(max_rate=rate))

        # Checkpoint store for crash-resume (D10)
        from leggie.infrastructure.resources import ResourceLocator
        locator = ResourceLocator()
        self.register(CheckpointStore, lambda: CheckpointStore(str(locator.checkpoint_path())))

        # Blackboard / Reranker
        from leggie.application.ports.reranker import RerankerPort
        from leggie.infrastructure.blackboard_adapter import BlackboardAdapter
        from leggie.infrastructure.reranker import OpenRouterReranker
        self.register(BlackboardPort, lambda: BlackboardAdapter())

        def _create_reranker() -> RerankerPort:
            from leggie.config.settings import get_settings
            s = get_settings()
            return OpenRouterReranker(
                api_key=s.llm.openrouter_api_key,
                base_url=s.llm.openrouter_base_url,
            )
        self.register(RerankerPort, _create_reranker)

        # Budget guard — the canonical BudgetGuard is created inside _create_llm()
        # and wrapped in BudgetGuardDecorator. No separate singleton needed here;
        # callers that need to introspect the guard should duck-type it from the
        # LLMPort (see BillAnalysisFlow._budget_guard()).

        # Reasoner (multi-model deliberative pipeline — opt-in, lazy: no
        # network/process activity until first resolved by the deliberative flow)
        def _create_reasoner() -> ReasonerPort:
            from leggie.config.settings import get_settings
            from leggie.infrastructure.reasoner.adapter import ReasonerAdapter
            s = get_settings()
            return ReasonerAdapter(
                base_url=s.reasoner.base_url,
                api_key=s.reasoner.api_key,
                request_timeout=float(s.reasoner.request_timeout),
            )
        self.register(ReasonerPort, _create_reasoner)

        def _create_reasoner_server_manager() -> ReasonerServerManager:
            from leggie.config.settings import get_settings
            from leggie.infrastructure.reasoner.server_manager import ReasonerServerManager
            return ReasonerServerManager(get_settings().reasoner)

        # Register lazily — ReasonerServerManager is only needed when the
        # deliberative pipeline is activated (PROD-32).
        # Use a string key since ReasonerServerManager is only TYPE_CHECKING
        # imported at module scope.
        from leggie.infrastructure.reasoner.server_manager import (
            ReasonerServerManager as _ReasonerServerManager,
        )
        self.register(_ReasonerServerManager, _create_reasoner_server_manager)

        # ── End configure_defaults() ──────────────────────────────────


