"""RunManifestBuilder — accumulates run telemetry into a frozen RunManifest.

Implements the **Builder** pattern and acts as an **Observer** on the event
bus: it accumulates fields from domain events (cost/token usage, findings,
budget, degradation) plus direct calls for identity and wall-clock. The
manifest stays immutable; ``freeze()`` produces the final value object.
"""

from __future__ import annotations

from pathlib import Path

from leggie.application.ports.manifest import ManifestSinkPort
from leggie.domain.manifest import ManifestCosts, RunManifest
from leggie.domain.models import Event, EventType

# Event payload keys the builder understands.
_KEYS_COST = ("cost", "estimated_cost")
_KEYS_TOKENS = ("tokens", "prompt_tokens", "completion_tokens")


class RunManifestBuilder:
    """Incremental assembler of a ``RunManifest``.

    Not thread-safe; intended to live for one run in a single event loop.
    """

    def __init__(self, sink: ManifestSinkPort | None = None) -> None:
        self._sink = sink
        self._own: dict[str, object] = {}
        self._prompt_hashes: dict[str, str] = {}
        self._resolved_models: dict[str, str] = {}
        self._costs = ManifestCosts()
        self._wallclock: dict[str, float] = {}
        self._finding_count = 0
        self._status = "unknown"
        self._final_error = ""
        self._run_id = ""

    # ── Builder setters ───────────────────────────────────────────

    def set_identity(self, run_id: str, version: str = "", git_sha: str = "") -> RunManifestBuilder:
        self._run_id = run_id
        self._own["leggie_version"] = version
        self._own["git_sha"] = git_sha
        return self

    def set_route_table_hash(self, h: str) -> RunManifestBuilder:
        self._own["route_table_hash"] = h
        return self

    def set_prompt_template_hash(self, route: str, h: str) -> RunManifestBuilder:
        self._prompt_hashes[route] = h
        return self

    def set_citation_index_version(self, v: str) -> RunManifestBuilder:
        self._own["citation_index_version"] = v
        return self

    def set_seed(self, seed: int) -> RunManifestBuilder:
        self._own["seed"] = seed
        return self

    def set_resolved_model(self, route: str, model: str) -> RunManifestBuilder:
        self._resolved_models[route] = model
        return self

    def set_stage_wallclock(self, stages: dict[str, float]) -> RunManifestBuilder:
        self._wallclock = dict(stages)
        return self

    def add_tokens(self, tier: str, tokens: int, cost: float) -> RunManifestBuilder:
        self._costs = self._costs.add_tokens(tier, tokens, cost)
        return self

    def set_status(self, status: str, error: str = "") -> RunManifestBuilder:
        self._status = status
        self._final_error = error
        return self

    def add_finding(self) -> RunManifestBuilder:
        self._finding_count += 1
        return self

    # ── Event observer ─────────────────────────────────────────────

    def handle(self, event: Event) -> None:
        """EventBus observer — accumulate telemetry from domain events."""
        if event.event_type == EventType.WORKFLOW_COMPLETED:
            self._status = "completed"
        elif event.event_type == EventType.WORKFLOW_FAILED:
            self._status = "failed"
            if isinstance(event.data, dict):
                self._final_error = str(event.data.get("error", ""))
        elif event.event_type == EventType.FINDING_CREATED or event.event_type == EventType.FINDING_CONFIRMED:
            self._finding_count += 1

        # Cost/token telemetry from llm.call events carried as event data
        if event.event_type == EventType.STAGE_COMPLETED or event.event_type == EventType.LENS_COMPLETED:
            if isinstance(event.data, dict):
                tier = str(event.data.get("tier", "unknown"))
                cost = event.data.get("cost", event.data.get("estimated_cost", 0.0))
                tokens = event.data.get("tokens", event.data.get("prompt_tokens", 0))
                if isinstance(cost, (int, float)) and isinstance(tokens, (int, float)):
                    self._costs = self._costs.add_tokens(tier, int(tokens), float(cost))

                model = event.data.get("model")
                route = event.data.get("route", "unknown")
                if model:
                    self._resolved_models[route] = str(model)

    # ── Final assembly ─────────────────────────────────────────────

    def freeze(self) -> RunManifest:
        """Produce the immutable manifest."""
        return RunManifest(
            run_id=self._run_id,
            leggie_version=str(self._own.get("leggie_version", "")),
            git_sha=str(self._own.get("git_sha", "")),
            route_table_hash=str(self._own.get("route_table_hash", "")),
            prompt_template_hashes=dict(self._prompt_hashes),
            citation_index_version=str(self._own.get("citation_index_version", "")),
            seed=int(self._own.get("seed", 0)),
            costs=self._costs,
            stage_wallclock=dict(self._wallclock),
            resolved_models=dict(self._resolved_models),
            status=self._status,
            finding_count=self._finding_count,
            final_error=self._final_error,
        )

    def emit(self, dest: Path | None = None) -> Path | None:
        """Freeze and write to the sink (if configured). Returns the path."""
        import contextlib
        manifest = self.freeze()
        if self._sink is not None:
            with contextlib.suppress(Exception):
                return self._sink.write(manifest, dest)
        return None
