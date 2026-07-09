"""Tests for CQRS Mediator — command/query dispatch with behaviors."""

import pytest
from leggie.application.cqrs.base import (
    Command, CommandHandler, CommandResult,
    Query, QueryHandler, QueryResult,
    IPipelineBehavior,
)
from leggie.application.cqrs.mediator import Mediator, HandlerNotRegisteredError


class TestCommand(Command):
    message: str


class TestCommandHandler(CommandHandler):
    async def handle(self, command: TestCommand) -> CommandResult:
        return CommandResult(success=True, data=f"Handled: {command.message}")


class TestQuery(Query):
    lookup: str


class TestQueryHandler(QueryHandler):
    async def handle(self, query: TestQuery) -> QueryResult:
        return QueryResult(success=True, data=f"Found: {query.lookup}")


class TestMediator:
    @pytest.mark.asyncio
    async def test_send_command(self):
        mediator = Mediator()
        mediator.register_command_handler(TestCommand, TestCommandHandler())
        result = await mediator.send(TestCommand(message="hello"))
        assert result.success is True
        assert result.data == "Handled: hello"

    @pytest.mark.asyncio
    async def test_query(self):
        mediator = Mediator()
        mediator.register_query_handler(TestQuery, TestQueryHandler())
        result = await mediator.query(TestQuery(lookup="bill-001"))
        assert result.success is True
        assert result.data == "Found: bill-001"

    @pytest.mark.asyncio
    async def test_unregistered_command_raises(self):
        mediator = Mediator()
        with pytest.raises(HandlerNotRegisteredError):
            await mediator.send(TestCommand(message="x"))

    @pytest.mark.asyncio
    async def test_unregistered_query_raises(self):
        mediator = Mediator()
        with pytest.raises(HandlerNotRegisteredError):
            await mediator.query(TestQuery(lookup="x"))


class TestPipelineBehavior:
    @pytest.mark.asyncio
    async def test_behavior_wraps_handler(self):
        calls = []

        class LogBehavior(IPipelineBehavior):
            async def handle(self, request, next_handler, **kwargs):
                calls.append("before")
                result = await next_handler(request)
                calls.append("after")
                return result

        mediator = Mediator()
        mediator.add_pipeline_behavior(LogBehavior())
        mediator.register_command_handler(TestCommand, TestCommandHandler())

        result = await mediator.send(TestCommand(message="test"))
        assert result.success is True
        assert calls == ["before", "after"]

    @pytest.mark.asyncio
    async def test_multiple_behaviors(self):
        order = []

        class BehaviorA(IPipelineBehavior):
            async def handle(self, request, next_handler, **kwargs):
                order.append("A-start")
                result = await next_handler(request)
                order.append("A-end")
                return result

        class BehaviorB(IPipelineBehavior):
            async def handle(self, request, next_handler, **kwargs):
                order.append("B-start")
                result = await next_handler(request)
                order.append("B-end")
                return result

        mediator = Mediator()
        mediator.add_pipeline_behavior(BehaviorA())
        mediator.add_pipeline_behavior(BehaviorB())
        mediator.register_command_handler(TestCommand, TestCommandHandler())

        await mediator.send(TestCommand(message="test"))
        assert order == ["A-start", "B-start", "B-end", "A-end"]
