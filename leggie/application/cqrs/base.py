"""CQRS base — Command, Query, Handler, and PipelineBehavior base classes.

Adapted from weebot's application/cqrs/base.py.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel

TCommand = TypeVar("TCommand", bound="Command")
TQuery = TypeVar("TQuery", bound="Query")
TResult = TypeVar("TResult")


@dataclass(frozen=True)
class CommandResult[TResult]:
    """Result of executing a command."""

    success: bool
    data: TResult | None = None
    error: str | None = None


@dataclass(frozen=True)
class QueryResult[TResult]:
    """Result of executing a query."""

    success: bool
    data: TResult | None = None
    error: str | None = None


class Command(BaseModel, ABC):
    """Base class for all commands (CQRS command side)."""


class Query(BaseModel, ABC):
    """Base class for all queries (CQRS query side)."""


class CommandHandler[TCommand: "Command", TResult](ABC):
    """Handles a single command type."""

    @abstractmethod
    async def handle(self, command: TCommand) -> CommandResult[TResult]: ...


class QueryHandler[TQuery: "Query", TResult](ABC):
    """Handles a single query type."""

    @abstractmethod
    async def handle(self, query: TQuery) -> QueryResult[TResult]: ...


class IPipelineBehavior(ABC):
    """Middleware that wraps command/query execution."""

    @abstractmethod
    async def handle(
        self,
        request: Any,
        next_handler: Any,
        **kwargs: Any,
    ) -> Any: ...
