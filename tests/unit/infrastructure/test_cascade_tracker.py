"""Tests for Cascade Tracker — model routing telemetry."""

from leggie.infrastructure.router.cascade_tracker import CascadeTracker


class TestCascadeTracker:
    def test_initial_state(self):
        tracker = CascadeTracker()
        stats = tracker.get_stats()
        assert stats["total"] == 0
        assert tracker.total_cost == 0.0
        assert tracker.total_tokens == 0

    def test_record_decision(self):
        tracker = CascadeTracker()
        tracker.record(
            task_type="lens_analysis",
            tier_attempted="budget",
            model_used="claude-sonnet-4",
            success=True,
            latency_ms=1200.0,
            tokens_used=5000,
            estimated_cost=0.015,
        )
        stats = tracker.get_stats()
        assert stats["total"] == 1
        assert stats["successes"] == 1
        assert stats["total_cost"] == 0.015
        assert stats["by_tier"]["budget"]["attempts"] == 1

    def test_record_failure(self):
        tracker = CascadeTracker()
        tracker.record(
            task_type="lens_analysis",
            tier_attempted="free",
            model_used="claude-haiku",
            success=False,
            latency_ms=3000.0,
            tokens_used=1000,
            estimated_cost=0.0,
            failure_reason="low_confidence",
            escalated_to="budget",
        )
        stats = tracker.get_stats()
        assert stats["total"] == 1
        assert stats["failures"] == 1
        assert stats["success_rate"] == 0.0

    def test_multiple_tiers(self):
        tracker = CascadeTracker()
        tracker.record("coding", "free", "haiku", True, 500, 1000, 0.0)
        tracker.record("coding", "budget", "sonnet", True, 1200, 5000, 0.015)
        tracker.record("coding", "premium", "opus", True, 3000, 10000, 0.15)

        stats = tracker.get_stats()
        assert stats["total"] == 3
        assert stats["total_tokens"] == 16000
        assert len(stats["by_tier"]) == 3

    def test_get_decisions_filtered(self):
        tracker = CascadeTracker()
        tracker.record("lens_analysis", "budget", "sonnet", True, 1000, 5000, 0.015)
        tracker.record("classification", "free", "haiku", True, 500, 1000, 0.0)

        decisions = tracker.get_decisions(task_type="lens_analysis")
        assert len(decisions) == 1
        assert decisions[0].task_type == "lens_analysis"

    def test_get_decisions_limit(self):
        tracker = CascadeTracker()
        for i in range(10):
            tracker.record("test", "budget", "sonnet", True, 1000, 1000, 0.01)

        decisions = tracker.get_decisions(limit=3)
        assert len(decisions) == 3

    def test_clear(self):
        tracker = CascadeTracker()
        tracker.record("test", "budget", "sonnet", True, 1000, 1000, 0.01)
        tracker.clear()
        assert tracker.get_stats()["total"] == 0

    def test_cost_and_token_tracking(self):
        tracker = CascadeTracker()
        tracker.record("a", "budget", "sonnet", True, 1000, 5000, 0.015)
        tracker.record("b", "free", "haiku", True, 500, 1000, 0.0)
        assert tracker.total_cost == 0.015
        assert tracker.total_tokens == 6000
