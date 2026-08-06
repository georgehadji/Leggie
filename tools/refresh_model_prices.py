"""Check leggie/domain/pricing.py against the live OpenRouter catalog.

Usage:
    python tools/refresh_model_prices.py            # report drift, exit 1 if any
    python tools/refresh_model_prices.py --write    # rewrite the table in place

Network-only tool; not imported by the package and not part of any test run.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from leggie.domain.pricing import MODEL_PRICES  # noqa: E402

MODELS_URL = "https://openrouter.ai/api/v1/models"
PRICING_PY = Path(__file__).resolve().parents[1] / "leggie" / "domain" / "pricing.py"
TOLERANCE = 0.001  # USD per 1M tokens


def fetch_catalog() -> dict[str, dict[str, float | None]]:
    with urllib.request.urlopen(MODELS_URL, timeout=30) as resp:  # noqa: S310
        payload = json.loads(resp.read().decode("utf-8"))
    catalog: dict[str, dict[str, float | None]] = {}
    for entry in payload["data"]:
        pricing = entry.get("pricing") or {}
        try:
            prompt = float(pricing["prompt"]) * 1e6
            completion = float(pricing["completion"]) * 1e6
        except (KeyError, TypeError, ValueError):
            continue
        if prompt < 0 or completion < 0:  # sentinel rows such as openrouter/auto
            continue
        raw_cache = pricing.get("input_cache_read")
        cached = float(raw_cache) * 1e6 if raw_cache and float(raw_cache) > 0 else None
        catalog[entry["id"]] = {"input": prompt, "output": completion, "cached": cached}
    return catalog


def diff(catalog: dict[str, dict[str, float | None]]) -> tuple[list[str], list[str]]:
    missing: list[str] = []
    drifted: list[str] = []
    for model_id, price in MODEL_PRICES.items():
        live = catalog.get(model_id)
        if live is None:
            missing.append(model_id)
            continue
        if (
            abs(live["input"] - price.input_per_1m) > TOLERANCE
            or abs(live["output"] - price.output_per_1m) > TOLERANCE
        ):
            drifted.append(
                f"{model_id}: declared {price.input_per_1m}/{price.output_per_1m} "
                f"→ live {live['input']:.3f}/{live['output']:.2f}"
            )
    return missing, drifted


def rewrite(catalog: dict[str, dict[str, float | None]]) -> None:
    """Replace each ModelPrice(...) literal with live values, preserving layout."""
    source = PRICING_PY.read_text(encoding="utf-8")

    def replace(match: re.Match[str]) -> str:
        model_id = match.group("id")
        live = catalog.get(model_id)
        if live is None:
            return match.group(0)
        body = (
            f"\n        input_per_1m={live['input']:g}, output_per_1m={live['output']:g},"
        )
        if live["cached"] is not None:
            body += f"\n        cached_input_per_1m={live['cached']:g},"
        return f'"{model_id}": ModelPrice({body}\n    )'

    pattern = re.compile(
        r'"(?P<id>[^"]+)": ModelPrice\((?P<args>[^)]*)\)',
        re.DOTALL,
    )
    PRICING_PY.write_text(pattern.sub(replace, source), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="rewrite pricing.py in place")
    args = parser.parse_args()

    catalog = fetch_catalog()
    missing, drifted = diff(catalog)

    if missing:
        print("NOT IN LIVE CATALOG (remove or fix the ID):")
        for model_id in missing:
            print(f"  {model_id}")
    if drifted:
        print("PRICE DRIFT:")
        for line in drifted:
            print(f"  {line}")

    if not missing and not drifted:
        print(f"OK — {len(MODEL_PRICES)} models match the live catalog.")
        return 0

    if args.write:
        rewrite(catalog)
        print("\npricing.py rewritten. Review the diff; remove any missing IDs by hand.")
        return 0

    print("\nRe-run with --write to apply price updates.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
