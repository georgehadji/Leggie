"""Tests for budget guard — token/$ ceiling and degrade strategy."""

import asyncio
from typing import Any

import pytest

from leggie.infrastructure.budget_guard import BudgetAction, BudgetGuard
from leggie.infrastructure.llm.decorators import BudgetGuardDecorator


class TestBudgetGuard:
    def test_initial_state(self):
        guard = BudgetGuard(max_tokens=100_000, max_cost=5.0)
        assert guard.remaining_tokens == 100_000
        assert guard.remaining_cost == 5.0
        assert guard.usage_ratio == 0.0

    def test_allow_within_budget(self):
        guard = BudgetGuard(max_tokens=100_000, max_cost=5.0)
        action = guard.check(prompt_tokens=1_000, completion_tokens=500, model="claude-sonnet-4-20250514")
        assert action == BudgetAction.ALLOW

    def test_block_when_exceeded(self):
        guard = BudgetGuard(max_tokens=1_000, max_cost=1.0)
        # Budget exceeded: BLOCK immediately (hard ceiling)
        action = guard.check(prompt_tokens=2_000, completion_tokens=500, model="claude-sonnet-4-20250514")
        assert action == BudgetAction.BLOCK

    def test_degrade_at_80_percent(self):
        guard = BudgetGuard(max_tokens=1_000, max_cost=1.0)
        # Use enough to trigger 80% threshold
        guard.record_usage(prompt_tokens=450, completion_tokens=400, model="claude-sonnet-4-20250514")
        action = guard.check(prompt_tokens=50, completion_tokens=50, model="claude-sonnet-4-20250514")
        assert action == BudgetAction.DEGRADE

    def test_record_usage_tracks_correctly(self):
        guard = BudgetGuard(max_tokens=100_000, max_cost=5.0)
        guard.record_usage(prompt_tokens=5_000, completion_tokens=3_000, model="claude-sonnet-4-20250514")
        assert guard.remaining_tokens == 92_000
        assert guard.usage_ratio > 0.0

    def test_apply_degrade(self):
        guard = BudgetGuard(max_tokens=100_000, max_cost=5.0)
        assert guard._state.degrade_level == 0
        guard.apply_degrade()
        assert guard._state.degraded is True
        assert guard._state.degrade_level == 1
        guard.apply_degrade()
        assert guard._state.degrade_level == 2
        guard.apply_degrade()  # Cap at 2
        assert guard._state.degrade_level == 2

    def test_degrade_strategy(self):
        guard = BudgetGuard(max_tokens=100_000, max_cost=1.0)
        guard._state.degrade_strategy = "fewer_paths"
        # At exactly 100% of token budget (not exceeding): 80% threshold triggers DEGRADE
        action = guard.check(prompt_tokens=90_000, completion_tokens=10_000, model="claude-sonnet-4-20250514")
        assert action == BudgetAction.DEGRADE

    def test_reset(self):
        guard = BudgetGuard(max_tokens=100_000, max_cost=5.0)
        guard.record_usage(prompt_tokens=50_000, completion_tokens=20_000)
        guard.apply_degrade()
        guard.reset()
        assert guard.remaining_tokens == 100_000
        assert guard.remaining_cost == 5.0
        assert guard._state.degraded is False
        assert guard._state.degrade_level == 0

    def test_price_matches_domain(self):
        """Cost estimation uses domain.pricing.estimate_cost, not a private method."""
        from leggie.domain.pricing import estimate_cost as domain_estimate
        guard = BudgetGuard(max_tokens=100_000, max_cost=5.0)
        # "anthropic/claude-sonnet-4" = $3/M input, $15/M output
        expected = 500 * 3.00 / 1_000_000 + 500 * 15.00 / 1_000_000  # $0.009
        cost = domain_estimate("anthropic/claude-sonnet-4", 500, 500)
        assert cost == pytest.approx(expected, rel=0.1)

        # Same call through BudgetGuard.record_usage should produce same cost
        guard.record_usage(500, 500, "anthropic/claude-sonnet-4")
        assert guard.remaining_cost == pytest.approx(5.0 - expected, rel=0.1)

        # Verify the fallback price for unknown models
        fallback = domain_estimate("unknown-model", 1000, 1000)
        assert fallback > 0

        # Verify that COST_PER_1M_TOKENS class attribute is gone (PROD-30)
        assert not hasattr(BudgetGuard, "COST_PER_1M_TOKENS")
        assert not hasattr(guard, "_estimate_cost")

    def test_save_load_state(self):
        guard = BudgetGuard(max_tokens=100_000, max_cost=5.0)
        guard.record_usage(prompt_tokens=50_000, completion_tokens=20_000)
        guard.apply_degrade()

        state = guard.save_state()
        assert state["tokens_used"] == 70_000
        assert state["degraded"] is True

        # Load into a fresh guard with same capacities
        guard2 = BudgetGuard(max_tokens=100_000, max_cost=5.0)
        guard2.load_state(state)
        assert guard2.remaining_tokens == guard.remaining_tokens
        assert guard2._state.degraded is True
        assert guard2._state.tokens_used == 70_000

    def test_to_from_file(self, tmp_path):
        guard = BudgetGuard(max_tokens=100_000, max_cost=5.0)
        guard.record_usage(prompt_tokens=30_000, completion_tokens=10_000)

        path = str(tmp_path / "budget_checkpoint.json")
        guard.to_file(path)

        loaded = BudgetGuard.from_file(path)
        assert loaded is not None
        assert loaded.remaining_tokens == guard.remaining_tokens
        assert loaded._state.cost_used == guard._state.cost_used

    def test_from_file_missing(self):
        loaded = BudgetGuard.from_file("/nonexistent/path.json")
        assert loaded is None


# ── PROD-08 concurrency tests ───────────────────────────────────────────

class TestBudgetReservation:
    """Verify the reserve→settle pattern prevents the PROD-08 race."""

    @pytest.mark.asyncio
    async def test_n_calls_against_1_call_ceiling_admit_exactly_one(self):
        """N concurrent calls against a budget that can afford exactly 1 call
        must admit exactly 1 and raise BudgetExceededError for the rest."""
        # Set max_cost low enough that the estimated cost for 1 call fits
        # but 2 do not. "anthropic/claude-sonnet-4" = $3/M input, $15/M output.
        # Estimate: prompt ~501 tokens + 2048 completion →
        #   prompt_cost = 500 * 3 / 1M ≈ $0.0015
        #   completion_cost = 2048 * 15 / 1M ≈ $0.031
        #   total ≈ $0.032 per call
        # Set ceiling to $0.04: exactly 1 call fits, 2 do not.
        guard = BudgetGuard(max_tokens=50_000, max_cost=0.04)
        decorated = BudgetGuardDecorator(
            _FakeLLM(call_delay_s=0.05), guard,
        )

        async def call_n(_n: int) -> str:
            try:
                await decorated.generate(
                    _fake_request(prompt="x" * 2000, model="anthropic/claude-sonnet-4"),
                )
                return "ok"
            except Exception:
                import sys

                from leggie.infrastructure.llm.base import BudgetExceededError
                if isinstance(sys.exc_info()[1], BudgetExceededError):
                    return "blocked"
                raise

        results = await asyncio.gather(*(call_n(i) for i in range(10)))
        oks = results.count("ok")
        blocked = results.count("blocked")
        assert oks == 1, f"Expected 1 admitted, got {oks} (cost used: ${guard._state.cost_used:.4f})"
        assert blocked == 9, f"Expected 9 blocked, got {blocked}"

    @pytest.mark.asyncio
    async def test_reserve_releases_on_exception(self):
        """When the wrapped call raises, the full estimate is released."""
        guard = BudgetGuard(max_tokens=10_000, max_cost=5.0)
        initial_cost = guard._state.cost_used

        class FailingLLM:
            _default_model = "test"
            async def generate(self, _req):
                raise RuntimeError("simulated failure")
            async def generate_structured(self, _req, _schema):
                raise RuntimeError("simulated failure")
            async def count_tokens(self, _text, _model=None):
                return 0

        decorated = BudgetGuardDecorator(FailingLLM(), guard)

        with pytest.raises(RuntimeError, match="simulated failure"):
            await decorated.generate(_fake_request(prompt="bogus"))

        # Cost must be back to initial after the exception release
        assert guard._state.cost_used == pytest.approx(initial_cost, abs=1e-5)

    @pytest.mark.asyncio
    async def test_settle_charges_overage(self):
        """When actual tokens exceed the estimate, the overage is charged."""
        guard = BudgetGuard(max_tokens=10_000, max_cost=5.0)
        decorated = BudgetGuardDecorator(
            _FakeLLM(
                call_delay_s=0,
                response_usage={"prompt_tokens": 600, "completion_tokens": 3000},
            ),
            guard,
        )

        initial_cost = guard._state.cost_used
        await decorated.generate(
            _fake_request(prompt="x" * 2000, model="anthropic/claude-sonnet-4"),
        )
        # Estimate: prompt ~501, completion ~2048. Actual: 600 + 3000.
        # The overage (99 prompt + 952 completion) must be charged.
        assert guard._state.cost_used > initial_cost

    @pytest.mark.asyncio
    async def test_settle_refunds_underage(self):
        """When actual tokens are under the estimate, the delta is refunded."""
        guard = BudgetGuard(max_tokens=10_000, max_cost=5.0)
        decorated = BudgetGuardDecorator(
            _FakeLLM(
                call_delay_s=0,
                response_usage={"prompt_tokens": 100, "completion_tokens": 50},
            ),
            guard,
        )

        await decorated.generate(
            _fake_request(prompt="x" * 2000, model="anthropic/claude-sonnet-4"),
        )
        # Estimate: prompt ~501 tokens + 2048 completion. Actual: 100 + 50.
        # After settle, cost must reflect actuals (100 prompt + 50 completion),
        # not the estimate (501 prompt + 2048 completion).
        from leggie.domain.pricing import estimate_cost as ec
        actual_cost = ec("anthropic/claude-sonnet-4", 100, 50)
        assert guard._state.cost_used == pytest.approx(actual_cost, abs=1e-5)


# ── Helpers ─────────────────────────────────────────────────────────────

def _fake_request(*, prompt: str = "test", model: str = "test-model") -> Any:
    class FakeReq:
        prompt = "test"
        model = "test-model"
        max_tokens = 4096
    r = FakeReq()
    r.prompt = prompt
    r.model = model
    return r


class _FakeLLM:
    """Simulates a wrapped LLM with controllable delay and usage values."""

    _default_model = "test"

    def __init__(self, call_delay_s: float = 0, response_usage: dict[str, int] | None = None):
        self._delay = call_delay_s
        self._usage = response_usage or {"prompt_tokens": 200, "completion_tokens": 100}

    async def generate(self, _request: Any) -> Any:
        if self._delay:
            await asyncio.sleep(self._delay)
        # Minimal response object mimicking LLMResponse's usage dict interface
        resp: Any = type("_FakeResp", (), {})()
        resp.usage = dict(self._usage)
        resp.content = "ok"
        return resp

    async def generate_structured(self, _request: Any, _schema: type) -> tuple[Any, Any]:
        return await self.generate(_request), None

    async def count_tokens(self, _text: str, _model: str | None = None) -> int:
        return 0
