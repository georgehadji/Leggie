"""Tests for the run manifest (PROD-22): value object, builder, JSON sink."""

from __future__ import annotations

import json

from leggie.application.services.run_manifest import RunManifestBuilder
from leggie.domain.manifest import ManifestCosts, RunManifest
from leggie.domain.models import Event, EventType
from leggie.infrastructure.manifest_sink import JsonManifestSink


class TestRunManifest:
    def test_to_dict_serializes(self):
        m = RunManifest(
            run_id="r1",
            leggie_version="0.1.0",
            git_sha="abc",
            route_table_hash="h",
            seed=42,
            costs=ManifestCosts(tokens_used={"budget": 100}, cost_used={"budget": 0.01}),
            status="completed",
            finding_count=3,
        )
        d = m.to_dict()
        assert d["run_id"] == "r1"
        assert d["status"] == "completed"
        assert d["finding_count"] == 3
        # to_dict() is typed dict[str, object], so the nested costs mapping has
        # to be narrowed before indexing — which also asserts its shape.
        costs = d["costs"]
        assert isinstance(costs, dict)
        assert costs["tokens_used"] == {"budget": 100}
        # Must be JSON-serialisable
        json.dumps(d)

    def test_manifest_costs_immutability(self):
        c = ManifestCosts()
        c2 = c.add_tokens("budget", 100, 0.01)
        c3 = c2.add_tokens("budget", 50, 0.005)
        assert c.tokens_used == {}
        assert c2.tokens_used == {"budget": 100}
        assert c3.tokens_used == {"budget": 150}
        assert c3.cost_used == {"budget": 0.015}


class TestRunManifestBuilder:
    def test_builder_accumulates_from_events(self):
        b = RunManifestBuilder()
        b.set_identity("run-1", "0.1.0", "abc123")
        b.set_route_table_hash("route-hash")
        b.set_seed(42)
        b.add_tokens("budget", 100, 0.01)
        b.handle(Event(event_type=EventType.FINDING_CREATED, aggregate_id="x", data={}))
        b.handle(Event(event_type=EventType.FINDING_CONFIRMED, aggregate_id="x", data={}))
        b.handle(Event(event_type=EventType.STAGE_COMPLETED, aggregate_id="x", data={
            "tier": "budget",
            "tokens": 50,
            "cost": 0.005,
            "model": "google/gemini-2.5-flash",
            "route": "lens_analysis",
        }))
        b.handle(Event(event_type=EventType.WORKFLOW_COMPLETED, aggregate_id="x", data={}))

        m = b.freeze()
        assert m.run_id == "run-1"
        assert m.leggie_version == "0.1.0"
        assert m.git_sha == "abc123"
        assert m.route_table_hash == "route-hash"
        assert m.seed == 42
        assert m.status == "completed"
        assert m.finding_count == 2
        # 100 direct + 50 from event
        assert m.costs.tokens_used == {"budget": 150}
        assert m.resolved_models == {"lens_analysis": "google/gemini-2.5-flash"}

    def test_builder_failed_status(self):
        b = RunManifestBuilder()
        b.handle(Event(event_type=EventType.WORKFLOW_FAILED, aggregate_id="x", data={"error": "boom"}))
        m = b.freeze()
        assert m.status == "failed"
        assert m.final_error == "boom"


class TestJsonManifestSink:
    def test_write_manifest(self, tmp_path):
        sink = JsonManifestSink(tmp_path)
        b = RunManifestBuilder(sink)
        b.set_identity("run-xyz", "0.1.0", "abc")
        b.handle(Event(event_type=EventType.WORKFLOW_COMPLETED, aggregate_id="x", data={}))
        path = b.emit()

        assert path is not None
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["run_id"] == "run-xyz"
        assert data["status"] == "completed"
        assert path.name == "run-xyz_manifest.json"
