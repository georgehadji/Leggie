"""Router Infrastructure — static YAML rules table + Cascade (Chain of Responsibility).

Routes task types to model tiers using a declarative rules table.
Cascade escalates through FREE → BUDGET → PREMIUM on low confidence.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from leggie.application.ports.router import RouteResult, RouterPort
from leggie.config.settings import get_settings
from leggie.domain.models import ModelTier

log = logging.getLogger(__name__)

# Fallback token ceilings, used ONLY when a rule omits them (SSOT-7). The real
# per-route values live in config/routes.yaml; these exist so a partially
# specified rule still routes instead of crashing, and every use of them is
# logged — a silently substituted ceiling once let the `lens_analysis` route sit
# dead through an entire smoke campaign with nothing complaining.
_FALLBACK_MAX_TOKENS = 4096
_FALLBACK_CASCADE_MAX_TOKENS = 8192


class StaticRouter(RouterPort):
    """Static YAML-based router — routes by task_type lookup."""

    CASCADE_ORDER = [ModelTier.FREE, ModelTier.BUDGET, ModelTier.PREMIUM]

    def __init__(self, rules_path: str | None = None) -> None:
        self._rules = self._load_rules(rules_path or get_settings().cascade.rules_path)

    def _load_rules(self, path: str) -> dict[str, Any]:
        path_obj = Path(path)
        if not path_obj.exists():
            # No silent passthrough: every route then falls back to the budget
            # tier, which looks like it works and quietly ignores every ceiling
            # and cascade the operator configured.
            log.warning(
                "router.rules_missing: %s does not exist; every task_type will fall back "
                "to the %s tier default",
                path,
                ModelTier.BUDGET.value,
            )
            return {}
        with open(path_obj, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    async def route(self, task_type: str, budget_remaining: float | None = None) -> RouteResult:
        """Route a task type to the configured model/tier."""
        rule = self._rules.get("routes", {}).get(task_type, {})
        if not rule:
            # SSOT-7: a missing route is a configuration bug, not a normal path.
            # The orchestrator once queried `lens_<name>` while routes.yaml
            # declared `lens_analysis`, so the configured route was DEAD for a
            # whole smoke campaign and the silent fallback hid it.
            log.warning(
                "router.route_missing: no rule for task_type=%r; falling back to the %s "
                "tier default with a %d token ceiling",
                task_type,
                ModelTier.BUDGET.value,
                _FALLBACK_MAX_TOKENS,
            )
            return RouteResult(
                model=self._default_for_tier(ModelTier.BUDGET),
                tier=ModelTier.BUDGET,
                max_tokens=_FALLBACK_MAX_TOKENS,
            )

        tier_str = rule.get("tier", "budget")
        tier = ModelTier(tier_str)
        model = rule.get("model", self._default_for_tier(tier))
        max_tokens = rule.get("max_tokens", _FALLBACK_MAX_TOKENS)
        cascade = rule.get("cascade", True)

        return RouteResult(model=model, tier=tier, max_tokens=max_tokens, cascade_enabled=cascade)

    async def cascade(
        self, task_type: str, current_tier: ModelTier, failure_reason: str | None = None
    ) -> RouteResult | None:
        """Escalate to the next tier in the cascade."""
        current_idx = (
            self.CASCADE_ORDER.index(current_tier) if current_tier in self.CASCADE_ORDER else -1
        )
        if current_idx >= len(self.CASCADE_ORDER) - 1:
            return None  # Already at highest tier

        next_tier = self.CASCADE_ORDER[current_idx + 1]
        rule = self._rules.get("routes", {}).get(task_type, {})
        model = rule.get("cascade_models", {}).get(
            next_tier.value, self._default_for_tier(next_tier)
        )
        max_tokens = rule.get("cascade_models", {}).get(
            f"{next_tier.value}_max_tokens", _FALLBACK_CASCADE_MAX_TOKENS
        )

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
        """Which model backs *tier* when a rule does not name one explicitly.

        SSOT-2: read from CascadeSettings, never hardcoded here. This method
        used to carry its own copy of the tier→model map, so
        LEGGIE_CASCADE__PREMIUM_MODEL (and free/budget) were documented settings
        that nothing read — five inert fields. Dead or fake model ids have
        broken this pipeline twice (2780339, repaired by 39b42ef), which is
        reason enough not to keep two answers to "what is the premium model".
        """
        cascade = get_settings().cascade
        return {
            ModelTier.FREE: cascade.free_model,
            ModelTier.BUDGET: cascade.budget_model,
            ModelTier.PREMIUM: cascade.premium_model,
        }.get(tier, cascade.budget_model)
