"""Tests for budget guard — token/$ ceiling and degrade strategy."""

import pytest

from leggie.infrastructure.budget_guard import BudgetAction, BudgetGuard

MODEL = "claude-sonnet-4-20250514"


class TestBudgetGuard:
    def test_initial_state(self):
        guard = BudgetGuard(max_tokens=100_000, max_cost=5.0)
        assert guard.remaining_tokens == 100_000
        assert guard.remaining_cost == 5.0
        assert guard.usage_ratio == 0.0

    def test_allow_within_budget(self):
        guard = BudgetGuard(max_tokens=100_000, max_cost=5.0)
        action = guard.check(prompt_tokens=1_000, completion_tokens=500, model=MODEL)
        assert action == BudgetAction.ALLOW

    def test_block_when_exceeded(self):
        guard = BudgetGuard(max_tokens=1_000, max_cost=1.0)
        # First exceed: guard degrades before blocking
        action = guard.check(prompt_tokens=2_000, completion_tokens=500, model=MODEL)
        assert action == BudgetAction.DEGRADE
        guard.apply_degrade()
        # Still exceeded after degrade: now block
        action = guard.check(prompt_tokens=2_000, completion_tokens=500, model=MODEL)
        assert action == BudgetAction.BLOCK

    def test_degrade_at_80_percent(self):
        guard = BudgetGuard(max_tokens=1_000, max_cost=1.0)
        # Use enough to trigger 80% threshold
        guard.record_usage(prompt_tokens=450, completion_tokens=400, model=MODEL)
        action = guard.check(prompt_tokens=50, completion_tokens=50, model=MODEL)
        assert action == BudgetAction.DEGRADE

    def test_record_usage_tracks_correctly(self):
        guard = BudgetGuard(max_tokens=100_000, max_cost=5.0)
        guard.record_usage(prompt_tokens=5_000, completion_tokens=3_000, model=MODEL)
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
        action = guard.check(prompt_tokens=90_000, completion_tokens=10_000, model=MODEL)
        assert action in (BudgetAction.DEGRADE, BudgetAction.ALLOW)

    def test_reset(self):
        guard = BudgetGuard(max_tokens=100_000, max_cost=5.0)
        guard.record_usage(prompt_tokens=50_000, completion_tokens=20_000)
        guard.apply_degrade()
        guard.reset()
        assert guard.remaining_tokens == 100_000
        assert guard.remaining_cost == 5.0
        assert guard._state.degraded is False
        assert guard._state.degrade_level == 0

    def test_estimate_cost(self):
        guard = BudgetGuard(max_tokens=100_000, max_cost=5.0)
        # Hand-calculate: Sonnet = $3/M tokens, 1000 tokens = $0.003
        cost = guard._estimate_cost("claude-sonnet-4-20250514", 500, 500)
        assert cost == pytest.approx(0.003, rel=0.1)

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
