"""Router Infrastructure — static YAML rules table + Cascade (Chain of Responsibility).

Routes task types to model tiers using a declarative rules table.
Cascade escalates through FREE → BUDGET → PREMIUM on low confidence.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from leggie.application.ports.router import RouteResult, RouterPort
from leggie.domain.models import ModelTier


class StaticRouter(RouterPort):
    """Static YAML-based router — routes by task_type lookup."""

    CASCADE_ORDER = [ModelTier.FREE, ModelTier.BUDGET, ModelTier.PREMIUM]

    def __init__(self, rules_path: str = "config/routes.yaml") -> None:
        self._rules = self._load_rules(rules_path)

    def _load_rules(self, path: str) -> dict:
        path_obj = Path(path)
        if not path_obj.exists():
            return {}  # No rules = passthrough default
        with open(path_obj, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    async def route(self, task_type: str, budget_remaining: float | None = None) -> RouteResult:
        """Route a task type to the configured model/tier."""
        rule = self._rules.get("routes", {}).get(task_type, {})
        if not rule:
            # Default fallback
            return RouteResult(model="google/gemini-2.5-flash", tier=ModelTier.BUDGET, max_tokens=4096)

        tier_str = rule.get("tier", "budget")
        tier = ModelTier(tier_str)
        model = rule.get("model", self._default_for_tier(tier))
        max_tokens = rule.get("max_tokens", 4096)
        cascade = rule.get("cascade", True)

        return RouteResult(model=model, tier=tier, max_tokens=max_tokens, cascade_enabled=cascade)

    async def cascade(
        self, task_type: str, current_tier: ModelTier, failure_reason: str | None = None
    ) -> RouteResult | None:
        """Escalate to the next tier in the cascade."""
        current_idx = self.CASCADE_ORDER.index(current_tier) if current_tier in self.CASCADE_ORDER else -1
        if current_idx >= len(self.CASCADE_ORDER) - 1:
            return None  # Already at highest tier

        next_tier = self.CASCADE_ORDER[current_idx + 1]
        rule = self._rules.get("routes", {}).get(task_type, {})
        model = rule.get("cascade_models", {}).get(next_tier.value, self._default_for_tier(next_tier))
        max_tokens = rule.get("cascade_models", {}).get(f"{next_tier.value}_max_tokens", 8192)

        return RouteResult(model=model, tier=next_tier, max_tokens=max_tokens)

    def supported_models(self) -> list[str]:
        """List all models across all tiers."""
        models = set()
        for route in self._rules.get("routes", {}).values():
            model = route.get("model")
            if model:
                models.add(model)
            for cascade_model in route.get("cascade_models", {}).values():
                if isinstance(cascade_model, str):
                    models.add(cascade_model)
        return list(models)

    def _default_for_tier(self, tier: ModelTier) -> str:
        return {
            ModelTier.FREE: "google/gemini-2.5-flash-lite",
            ModelTier.BUDGET: "google/gemini-2.5-flash",
            ModelTier.PREMIUM: "google/gemini-2.5-pro",
        }.get(tier, "google/gemini-2.5-flash")
