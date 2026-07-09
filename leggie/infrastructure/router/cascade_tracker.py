"""Model Cascade Tracker — telemetry for model routing decisions.

Tracks cascade decisions (which tier was used, latency, cost, outcome)
for observability and future RouteLLM training data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class CascadeDecision:
    """A single model routing decision record."""

    task_type: str
    tier_attempted: str  # free, budget, premium
    model_used: str
    success: bool
    latency_ms: float
    tokens_used: int
    estimated_cost: float
    failure_reason: str | None = None
    escalated_to: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class CascadeTracker:
    """Thread-safe tracker of model cascade decisions.

    Records every routing decision for telemetry and future analysis.
    Can be swapped for a RouteLLM matrix-factorization router (Phase 5+).
    """

    def __init__(self, max_records: int = 10_000) -> None:
        self._decisions: list[CascadeDecision] = []
        self._max_records = max_records

    def record(
        self,
        task_type: str,
        tier_attempted: str,
        model_used: str,
        success: bool,
        latency_ms: float,
        tokens_used: int,
        estimated_cost: float,
        failure_reason: str | None = None,
        escalated_to: str | None = None,
    ) -> None:
        """Record a cascade decision."""
        decision = CascadeDecision(
            task_type=task_type,
            tier_attempted=tier_attempted,
            model_used=model_used,
            success=success,
            latency_ms=latency_ms,
            tokens_used=tokens_used,
            estimated_cost=estimated_cost,
            failure_reason=failure_reason,
            escalated_to=escalated_to,
        )
        self._decisions.append(decision)
        # Trim if over max
        if len(self._decisions) > self._max_records:
            self._decisions = self._decisions[-self._max_records:]

    def get_stats(self) -> dict[str, Any]:
        """Get summary statistics for all recorded decisions."""
        if not self._decisions:
            return {"total": 0}

        total = len(self._decisions)
        successes = sum(1 for d in self._decisions if d.success)
        failures = total - successes
        total_cost = sum(d.estimated_cost for d in self._decisions)
        total_tokens = sum(d.tokens_used for d in self._decisions)
        avg_latency = sum(d.latency_ms for d in self._decisions) / total if total > 0 else 0

        # Per-tier breakdown
        tier_stats: dict[str, dict] = {}
        for d in self._decisions:
            if d.tier_attempted not in tier_stats:
                tier_stats[d.tier_attempted] = {"attempts": 0, "successes": 0, "cost": 0.0, "tokens": 0}
            tier_stats[d.tier_attempted]["attempts"] += 1
            if d.success:
                tier_stats[d.tier_attempted]["successes"] += 1
            tier_stats[d.tier_attempted]["cost"] += d.estimated_cost
            tier_stats[d.tier_attempted]["tokens"] += d.tokens_used

        return {
            "total": total,
            "successes": successes,
            "failures": failures,
            "success_rate": successes / total if total > 0 else 0.0,
            "total_cost": round(total_cost, 4),
            "total_tokens": total_tokens,
            "avg_latency_ms": round(avg_latency, 2),
            "by_tier": tier_stats,
        }

    def get_decisions(
        self,
        task_type: str | None = None,
        limit: int = 100,
    ) -> list[CascadeDecision]:
        """Get recent decisions, optionally filtered by task type."""
        filtered = self._decisions
        if task_type:
            filtered = [d for d in filtered if d.task_type == task_type]
        return filtered[-limit:]

    @property
    def total_cost(self) -> float:
        return sum(d.estimated_cost for d in self._decisions)

    @property
    def total_tokens(self) -> int:
        return sum(d.tokens_used for d in self._decisions)

    def clear(self) -> None:
        """Clear all recorded decisions."""
        self._decisions.clear()
