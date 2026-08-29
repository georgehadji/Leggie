"""Mediator — central dispatcher for commands and queries.

Adapted from weebot's application/cqrs/mediator.py.
Decouples command/query senders from handlers with pipeline behaviors (middleware).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from leggie.application.cqrs.base import (
    Command,
    CommandHandler,
    CommandResult,
    IPipelineBehavior,
    Query,
    QueryHandler,
    QueryResult,
)
from leggie.observability import get_logger

logger = get_logger(__name__)

TResult = TypeVar("TResult")


class MediatorError(Exception):
    """Exception raised by the Mediator."""


class HandlerNotRegisteredError(MediatorError):
    """Raised when no handler is registered for a command/query type."""


class Mediator:
    """Central dispatcher for commands and queries.

    Decouples senders from handlers, with pipeline behaviors (middleware)
    for cross-cutting concerns like logging, telemetry, and validation.

    Example:
        mediator = Mediator()
        mediator.register_command_handler(AnalyzeBillCommand, AnalyzeBillHandler())
        mediator.add_pipeline_behavior(LoggingBehavior())
        result = await mediator.send(AnalyzeBillCommand(bill_id="..."))
    """

    def __init__(self) -> None:
        self._command_handlers: dict[type[Command], CommandHandler[Any, Any]] = {}
        self._query_handlers: dict[type[Query], QueryHandler[Any, Any]] = {}
        self._behaviors: list[IPipelineBehavior] = []

    def register_command_handler(
        self,
        command_type: type[Command],
        handler: CommandHandler[Any, Any],
    ) -> None:
        """Register a handler for a command type."""
        self._command_handlers[command_type] = handler

    def register_query_handler(
        self,
        query_type: type[Query],
        handler: QueryHandler[Any, Any],
    ) -> None:
        """Register a handler for a query type."""
        self._query_handlers[query_type] = handler

    def add_pipeline_behavior(self, behavior: IPipelineBehavior) -> None:
        """Add middleware to the execution pipeline."""
        self._behaviors.append(behavior)

    async def send(self, command: Command) -> CommandResult[Any]:
        """Execute a command through the pipeline."""
        handler = self._command_handlers.get(type(command))
        if handler is None:
            raise HandlerNotRegisteredError(f"No handler registered for {type(command).__name__}")
        result: CommandResult[Any] = await self._run_pipeline(command, handler.handle)
        return result

    async def query(self, query: Query) -> QueryResult[Any]:
        """Execute a query through the pipeline."""
        handler = self._query_handlers.get(type(query))
        if handler is None:
            raise HandlerNotRegisteredError(f"No handler registered for {type(query).__name__}")
        result: QueryResult[Any] = await self._run_pipeline(query, handler.handle)
        return result

    async def _run_pipeline(
        self,
        request: Any,
        handler_fn: Callable[..., Any],
    ) -> Any:
        """Run the request through all pipeline behaviors and then the handler."""
        if not self._behaviors:
            return await handler_fn(request)

        # Build the pipeline chain: outermost behavior wraps the next, etc.
        async def final_handler(req: Any) -> Any:
            return await handler_fn(req)

        current = final_handler
        for behavior in reversed(self._behaviors):
            prev = current

            async def make_next(
                b: IPipelineBehavior, next_fn: Callable[..., Any]
            ) -> Callable[..., Any]:
                async def wrapped(req: Any) -> Any:
                    return await b.handle(req, next_fn)

                return wrapped

            current = await make_next(behavior, prev)

        return await current(request)
