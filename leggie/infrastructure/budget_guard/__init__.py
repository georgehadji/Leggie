"""Budget Guard — token/$ ceiling with graceful degradation.

Circuit Breaker + Token Bucket pattern. Monitors per-run token and cost usage,
applies degrade strategy (fewer paths, fewer lenses, cheaper tier) before hard stop.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from leggie.domain.pricing import estimate_cost


class BudgetAction(StrEnum):
    ALLOW = "allow"
    DEGRADE = "degrade"  # Use cheaper tier / fewer paths
    BLOCK = "block"  # Hard stop


DegradeStrategy = Literal["fewer_paths", "fewer_lenses", "cheaper_tier"]


@dataclass
class BudgetState:
    """Mutable budget tracker for a single analysis run."""

    max_tokens: int
    max_cost: float
    tokens_used: int = 0
    cost_used: float = 0.0
    degraded: bool = False
    degrade_level: int = 0  # 0 = full, 1 = partial, 2 = minimal
    degrade_strategy: DegradeStrategy = "fewer_paths"


class BudgetGuard:
    """Budget guard — monitors and enforces token/$ ceilings per run.

    Prices are defined exclusively in ``domain.pricing.MODEL_PRICES``.
    This class uses ``estimate_cost()`` from that module — see PROD-30.
    """

    def __init__(self, max_tokens: int = 500_000, max_cost: float = 5.0) -> None:
        self._state = BudgetState(max_tokens=max_tokens, max_cost=max_cost)

    def check(
        self, prompt_tokens: int = 0, completion_tokens: int = 0, model: str = ""
    ) -> BudgetAction:
        """Check if a proposed call is within budget."""
        total_tokens = self._state.tokens_used + prompt_tokens + completion_tokens
        estimated_cost = estimate_cost(model, prompt_tokens, completion_tokens)
        total_cost = self._state.cost_used + estimated_cost

        if total_tokens > self._state.max_tokens or total_cost > self._state.max_cost:
            return BudgetAction.BLOCK

        # Warn at 80% threshold
        token_ratio = total_tokens / self._state.max_tokens
        cost_ratio = total_cost / self._state.max_cost
        if (token_ratio > 0.8 or cost_ratio > 0.8) and not self._state.degraded:
            return BudgetAction.DEGRADE

        return BudgetAction.ALLOW

    def record_usage(
        self, prompt_tokens: int, completion_tokens: int, model: str = "", cached_tokens: int = 0
    ) -> None:
        """Record actual usage after a call completes.

        Args:
            prompt_tokens: Number of prompt tokens used.
            completion_tokens: Number of completion tokens used.
            model: Model ID string.
            cached_tokens: Number of cached input tokens.
        """
        self._state.tokens_used += prompt_tokens + completion_tokens
        self._state.cost_used += estimate_cost(
            model, prompt_tokens, completion_tokens, cached_tokens
        )

    def apply_degrade(self) -> None:
        """Apply the degrade strategy."""
        self._state.degraded = True
        self._state.degrade_level = min(self._state.degrade_level + 1, 2)

    def reset(self) -> None:
        """Reset budget for a new run."""
        self._state.tokens_used = 0
        self._state.cost_used = 0.0
        self._state.degraded = False
        self._state.degrade_level = 0

    @property
    def remaining_tokens(self) -> int:
        return self._state.max_tokens - self._state.tokens_used

    @property
    def remaining_cost(self) -> float:
        return self._state.max_cost - self._state.cost_used

    @property
    def usage_ratio(self) -> float:
        token_ratio = (
            self._state.tokens_used / self._state.max_tokens if self._state.max_tokens > 0 else 0
        )
        cost_ratio = self._state.cost_used / self._state.max_cost if self._state.max_cost > 0 else 0
        return max(token_ratio, cost_ratio)

    def save_state(self) -> dict[str, Any]:
        """Serialize budget state for checkpointing."""
        return {
            "max_tokens": self._state.max_tokens,
            "max_cost": self._state.max_cost,
            "tokens_used": self._state.tokens_used,
            "cost_used": self._state.cost_used,
            "degraded": self._state.degraded,
            "degrade_level": self._state.degrade_level,
        }

    def load_state(self, state: dict[str, Any]) -> None:
        """Restore budget state from a checkpoint."""
        self._state.tokens_used = state.get("tokens_used", 0)
        self._state.cost_used = state.get("cost_used", 0.0)
        self._state.degraded = state.get("degraded", False)
        self._state.degrade_level = state.get("degrade_level", 0)

    def to_file(self, path: str) -> None:
        """Persist budget state to a JSON file."""
        import json

        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.save_state(), f, indent=2)

    @classmethod
    def from_file(cls, path: str) -> BudgetGuard | None:
        """Load budget state from a JSON file. Returns None if file missing."""
        import json
        from pathlib import Path

        p = Path(path)
        if not p.exists():
            return None
        with open(p, encoding="utf-8") as f:
            state = json.load(f)
        guard = cls(max_tokens=state["max_tokens"], max_cost=state["max_cost"])
        guard.load_state(state)
        return guard
