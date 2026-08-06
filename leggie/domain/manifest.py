"""RunManifest — a frozen value object capturing everything needed to
attribute a Leggie run's output back to its inputs.

This is a **new** domain module (per PROD-22), deliberately separate from
``domain/models/`` so it doesn't touch the frozen model set. It captures:

* Leggie version + git SHA
* the route-table hash (so a change to ``routes.yaml`` is observable)
* prompt-template hashes
* citation-index version
* the global seed
* per-tier token/cost totals
* per-stage wall-clock (sourced from ``observability.StageTimer``)

The manifest is immutable once frozen; a ``RunManifestBuilder`` in
``application/services/run_manifest.py`` accumulates fields then freezes.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as _field


@dataclass(frozen=True)
class ManifestCosts:
    """Per-tier token and cost usage accumulated over a run."""

    tokens_used: dict[str, int] = _field(default_factory=dict)  # tier → tokens
    cost_used: dict[str, float] = _field(default_factory=dict)  # tier → USD

    def add_tokens(self, tier: str, n: int, cost: float) -> ManifestCosts:
        """Return a new ManifestCosts with the given usage added (immutable)."""
        tokens = dict(self.tokens_used)
        costs = dict(self.cost_used)
        tokens[tier] = tokens.get(tier, 0) + n
        costs[tier] = round(costs.get(tier, 0.0) + cost, 6)
        return ManifestCosts(tokens_used=tokens, cost_used=costs)


@dataclass(frozen=True)
class RunManifest:
    """Frozen value object describing a single analysis run.

    Use ``RunManifestBuilder`` to accumulate then ``freeze()``.
    """

    # Identity
    run_id: str = ""
    leggie_version: str = ""
    git_sha: str = ""

    # Inputs / provenance
    route_table_hash: str = ""
    prompt_template_hashes: dict[str, str] = _field(default_factory=dict)
    citation_index_version: str = ""
    seed: int = 0

    # Usage
    costs: ManifestCosts = _field(default_factory=ManifestCosts)

    # Per-stage wall-clock (seconds), keyed by stage name
    stage_wallclock: dict[str, float] = _field(default_factory=dict)

    # Resolved model per call site (route name → model string)
    resolved_models: dict[str, str] = _field(default_factory=dict)

    # Outcome
    status: str = "unknown"  # completed | failed | interrupted
    finding_count: int = 0
    final_error: str = ""

    def to_dict(self) -> dict[str, object]:
        """Serialize to a JSON-serialisable dict."""
        return {
            "run_id": self.run_id,
            "leggie_version": self.leggie_version,
            "git_sha": self.git_sha,
            "route_table_hash": self.route_table_hash,
            "prompt_template_hashes": self.prompt_template_hashes,
            "citation_index_version": self.citation_index_version,
            "seed": self.seed,
            "costs": {
                "tokens_used": self.costs.tokens_used,
                "cost_used": self.costs.cost_used,
            },
            "stage_wallclock": self.stage_wallclock,
            "resolved_models": self.resolved_models,
            "status": self.status,
            "finding_count": self.finding_count,
            "final_error": self.final_error,
        }
