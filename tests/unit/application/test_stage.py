"""Tests for Stage lifecycle — Template Method pattern."""

import pytest

from leggie.application.workflow.stage import Stage, StageContext


class SimpleStage(Stage):
    def stage_name(self) -> str:
        return "test"

    async def _execute(self, context: StageContext) -> None:
        context.intermediate_data["executed"] = True


class TestStage:
    @pytest.mark.asyncio
    async def test_run_success(self):
        stage = SimpleStage()
        context = StageContext(article_text="test", article_id="1")
        result = await stage.run(context)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_run_executes_hooks(self):
        stage = SimpleStage()
        context = StageContext(article_text="test", article_id="1")
        await stage.run(context)
        assert context.intermediate_data.get("executed") is True

    @pytest.mark.asyncio
    async def test_run_returns_findings(self):
        stage = SimpleStage()
        context = StageContext(article_text="test", article_id="1")
        result = await stage.run(context)
        assert result.findings == []

    @pytest.mark.asyncio
    async def test_run_error_handling(self):
        class FailingStage(Stage):
            def stage_name(self) -> str:
                return "fail"

            async def _execute(self, context: StageContext) -> None:
                raise ValueError("test error")

        stage = FailingStage()
        context = StageContext(article_text="test", article_id="1")
        result = await stage.run(context)
        assert result.success is False
        assert result.error is not None
        assert "test error" in result.error
