"""Model catalog consistency — offline, no network.

Guards the invariant that broke in production: `x-ai/grok-4.5` was the premium
tier in routes.yaml but absent from MODEL_PRICES, so every premium call was
costed at the conservative fallback (5.00/15.00 vs an actual 2.00/6.00). The
budget guard blocked runs on money that was never spent.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from leggie.domain.pricing import MODEL_PRICES, get_model_price
from leggie.infrastructure.llm import _OFFLINE_MODEL_ALLOWLIST

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ROUTES_YAML = _REPO_ROOT / "config" / "routes.yaml"
_SETTINGS_PY = _REPO_ROOT / "leggie" / "config" / "settings.py"
_MODEL_ID = re.compile(r"^[a-z0-9][a-z0-9\-]*/[a-z0-9][a-z0-9.\-]*$")


def _routes_models() -> set[str]:
    routes = yaml.safe_load(_ROUTES_YAML.read_text(encoding="utf-8"))["routes"]
    models: set[str] = set()
    for route in routes.values():
        models.add(route["model"])
        models.update((route.get("cascade_models") or {}).values())
    return models


def _settings_models() -> set[str]:
    """OpenRouter chat-model IDs defaulted in LLMSettings / CascadeSettings.

    Scoped to those two classes on purpose: RetrievalSettings names a
    HuggingFace embedding model, which is a different catalog and has no
    OpenRouter price.
    """
    source = _SETTINGS_PY.read_text(encoding="utf-8")
    blocks = re.findall(
        r"^class (?:LLM|Cascade)Settings\b.*?(?=^class |\Z)",
        source,
        re.MULTILINE | re.DOTALL,
    )
    return set(re.findall(r'_model:\s*str\s*=\s*"([^"]+)"', "".join(blocks)))


@pytest.mark.parametrize("model_id", sorted(MODEL_PRICES))
def test_model_ids_are_well_formed(model_id: str) -> None:
    assert _MODEL_ID.match(model_id), f"malformed OpenRouter model id: {model_id}"


def test_every_routed_model_has_a_price() -> None:
    unpriced = _routes_models() - set(MODEL_PRICES)
    assert not unpriced, (
        f"routes.yaml names models with no price entry: {sorted(unpriced)}. "
        "They would be billed at the conservative fallback, distorting the budget guard."
    )


def test_every_settings_default_model_has_a_price() -> None:
    unpriced = _settings_models() - set(MODEL_PRICES)
    assert not unpriced, f"settings.py names unpriced models: {sorted(unpriced)}"


def test_allowlist_is_derived_from_prices() -> None:
    assert frozenset(MODEL_PRICES) == _OFFLINE_MODEL_ALLOWLIST


def test_routed_models_are_allowlisted() -> None:
    """A routed model that fails the allowlist check aborts the run at init."""
    assert _routes_models() <= _OFFLINE_MODEL_ALLOWLIST


def test_fallback_price_is_not_cheaper_than_any_known_model() -> None:
    """Unknown models must never look cheap, or the budget cap under-counts."""
    fallback = get_model_price("definitely/not-a-real-model")
    priciest_out = max(p.output_per_1m for p in MODEL_PRICES.values())
    assert fallback.output_per_1m >= priciest_out * 0.2
