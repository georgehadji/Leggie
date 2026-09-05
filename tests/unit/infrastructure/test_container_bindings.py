"""Container binding contract tests — Phase 3.2.

Verifies that every port registered by configure_defaults() resolves to a
correctly typed adapter that satisfies the port's contract, preventing
invalid bindings like StatePort → InMemoryEventBus.
"""

from __future__ import annotations

import pytest

from leggie.application.ports.blackboard import BlackboardPort
from leggie.application.ports.citation_parser import CitationParserPort
from leggie.application.ports.event_bus import EventBusPort
from leggie.application.ports.ingest import IngestPort
from leggie.application.ports.llm import LLMPort
from leggie.application.ports.parse import ParsePort
from leggie.application.ports.reranker import RerankerPort
from leggie.application.ports.router import RouterPort
from leggie.application.ports.state import StatePort
from leggie.domain.models import WorkflowState
from leggie.infrastructure.container import Container


@pytest.fixture
def container() -> Container:
    c = Container()
    c.configure_defaults()
    return c


class TestContainerBindings:
    """Verify all default port bindings resolve correctly."""

    def test_event_bus_port_resolves(self, container: Container):
        bus = container.get(EventBusPort)
        assert isinstance(bus, EventBusPort)

    def test_state_port_resolves_and_supports_contract(self, container: Container):
        """StatePort must resolve to an object that satisfies all four methods."""
        store = container.get(StatePort)
        # Check the async methods exist
        assert hasattr(store, "get_state")
        assert hasattr(store, "set_state")
        assert hasattr(store, "get_checkpoint")
        assert hasattr(store, "save_checkpoint")

        # Actually call them (in-memory, no side effects)
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(store.set_state("test-run", WorkflowState.IDLE))
            state = loop.run_until_complete(store.get_state("test-run"))
            assert state == WorkflowState.IDLE

            loop.run_until_complete(store.save_checkpoint("test-run", "ingest", {"key": "val"}))
            data = loop.run_until_complete(store.get_checkpoint("test-run", "ingest"))
            assert data == {"key": "val"}
        finally:
            loop.close()

    def test_ingest_port_resolves(self, container: Container):
        ingest = container.get(IngestPort)
        from leggie.infrastructure.ingest_adapter import IngestAdapter

        assert isinstance(ingest, IngestAdapter)

    def test_parse_port_resolves(self, container: Container):
        parse = container.get(ParsePort)
        from leggie.infrastructure.parse_adapter import ParseAdapter

        assert isinstance(parse, ParseAdapter)

    def test_citation_parser_port_resolves(self, container: Container):
        parser = container.get(CitationParserPort)
        from leggie.infrastructure.citation import GreekCitationParser

        assert isinstance(parser, GreekCitationParser)

    @pytest.mark.asyncio
    async def test_citation_parser_port_carries_resolution_index(self, container: Container):
        """D22: the container must wire a populated resolution index, not a
        bare parser — a bare GreekCitationParser() fails closed and reports
        every citation as unverified regardless of whether it actually
        resolves. Regression for AnalyzeBillHandler._handle_deliberative
        constructing GreekCitationParser() by hand instead of resolving
        CitationParserPort from this same container."""
        import json

        from leggie.domain.models import Citation, CitationScheme
        from leggie.infrastructure.resources import ResourceLocator

        index_path = ResourceLocator().package_resource("leggie.data", "citation_index.json")
        known_identifier = json.loads(index_path.read_text(encoding="utf-8"))["identifiers"][0]

        parser = container.get(CitationParserPort)
        citation = Citation(
            scheme=CitationScheme.UNKNOWN,
            identifier=known_identifier,
            original_text=known_identifier,
        )
        resolved = await parser.resolve(citation)

        assert resolved.resolved is True

    def test_blackboard_port_resolves(self, container: Container):
        board = container.get(BlackboardPort)
        from leggie.infrastructure.blackboard_adapter import BlackboardAdapter

        assert isinstance(board, BlackboardAdapter)

    def test_router_port_resolves(self, container: Container):
        router = container.get(RouterPort)
        from leggie.infrastructure.router import StaticRouter

        assert isinstance(router, StaticRouter)

    def test_reranker_port_is_bound(self, container: Container):
        """RerankerPort is registered in the container.

        Resolution requires an OpenRouter API key; the binding exists
        but is only resolved when settings.analysis.reranker == 'model'.
        """
        assert container.has_binding(RerankerPort)

    def test_llm_port_is_bound(self, container: Container):
        """LLMPort is registered in the container.

        Resolution requires an OpenRouter API key. Skip if not configured.
        """
        import os

        if not os.environ.get("LEGGIE_LLM__OPENROUTER_API_KEY"):
            pytest.skip("OpenRouter API key not configured")
        assert container.has_binding(LLMPort)
        llm = container.get(LLMPort)
        # May be wrapped in BudgetGuardDecorator, so check it's adapter-like
        assert hasattr(llm, "generate")


class TestBlackboardAdapterBehavior:
    """Behavioral tests for BlackboardAdapter — filtering, agent_id, clear_round."""

    @pytest.mark.asyncio
    async def test_post_and_retrieve_preserves_agent_id(self, container: Container):
        """findings posted via the adapter should retain their agent_id on retrieval."""
        from leggie.application.ports.blackboard import BlackboardEntry
        from leggie.domain.models import IRAC, Confidence, Finding, FindingType

        board = container.get(BlackboardPort)
        finding = Finding(
            finding_type=FindingType.CONSTITUTIONAL,
            irac=IRAC(issue="test issue", rule="r", application="a", conclusion="c"),
            confidence=Confidence.from_score(0.5),
            lens="test",
            model="test",
        )
        entry = BlackboardEntry(finding=finding, agent_id="lens-1", round=1)
        await board.post_finding(entry)
        results = await board.get_all_findings()
        assert len(results) >= 1
        assert any(r.agent_id == "lens-1" for r in results)

    @pytest.mark.asyncio
    async def test_get_findings_filters_by_round_min(self, container: Container):
        """get_findings(round_min=N) excludes entries from earlier rounds."""
        from leggie.application.blackboard import Blackboard as BlackboardService
        from leggie.domain.models import IRAC, Confidence, Finding, FindingType

        # Post directly to service to control rounds precisely
        svc = BlackboardService()
        f1 = Finding(
            finding_type=FindingType.CONSTITUTIONAL,
            irac=IRAC(issue="round 1", rule="r", application="a", conclusion="c"),
            confidence=Confidence.from_score(0.5),
            lens="test",
            model="test",
        )
        f2 = Finding(
            finding_type=FindingType.CONSTITUTIONAL,
            irac=IRAC(issue="round 2", rule="r", application="a", conclusion="c"),
            confidence=Confidence.from_score(0.5),
            lens="test",
            model="test",
        )
        svc.post(f1, agent_id="a")
        svc.next_round()
        svc.post(f2, agent_id="b")

        # Create adapter wrapping this pre-populated service
        from leggie.infrastructure.blackboard_adapter import BlackboardAdapter

        adapter = BlackboardAdapter()
        adapter._service = svc

        all_ = await adapter.get_findings(round_min=1)
        assert len(all_) >= 1  # rounds 1+2
        round2_only = await adapter.get_findings(round_min=2)
        assert len(round2_only) == 1

    @pytest.mark.asyncio
    async def test_clear_round_has_observable_behavior(self, container: Container):
        """clear_round should remove entries from the specified round."""
        from leggie.application.ports.blackboard import BlackboardEntry
        from leggie.domain.models import IRAC, Confidence, Finding, FindingType

        board = container.get(BlackboardPort)
        finding = Finding(
            finding_type=FindingType.CONSTITUTIONAL,
            irac=IRAC(issue="to be cleared", rule="r", application="a", conclusion="c"),
            confidence=Confidence.from_score(0.5),
            lens="test",
            model="test",
        )
        entry = BlackboardEntry(finding=finding, agent_id="test", round=1)
        await board.post_finding(entry)
        assert len(await board.get_all_findings()) >= 1
        await board.clear_round(1)
        # After clearing round 1, no findings should remain
        assert len(await board.get_all_findings()) == 0


class TestCitationIndexLoadRobustness:
    """DH-29: configure_defaults()'s citation_index.json load must degrade
    (fall back to an empty resolution index), never crash the whole
    composition root, and must log a warning whenever it degrades —
    the same untrusted-package-data trust-boundary class R7 already
    hardened for the Reasoner's HTTP response bodies (DH-26/DH-27),
    applied here to container.py's own file read.

    Uses a stdlib ``logging.getLogger`` module logger (container.py:35),
    not structlog — monkeypatching the bound ``.warning`` method directly
    (rather than ``caplog``) per DH-2's own documented full-suite-robustness
    finding for this repo's test suite.
    """

    @staticmethod
    def _patch_index_path(monkeypatch: pytest.MonkeyPatch, fake_path) -> None:
        """Redirect only the citation_index.json lookup to *fake_path*.

        configure_defaults() also resolves config/routes.yaml through this
        same ResourceLocator.package_resource() method, so the fake must
        delegate anything else to the real implementation.
        """
        from leggie.infrastructure.resources import ResourceLocator

        real = ResourceLocator.package_resource

        def fake(self: ResourceLocator, package: str, resource: str):
            if package == "leggie.data" and resource == "citation_index.json":
                return fake_path
            return real(self, package, resource)

        monkeypatch.setattr(ResourceLocator, "package_resource", fake)

    def test_malformed_json_shape_does_not_crash_configure_defaults(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ):
        """Proof-of-defect: a JSON body that parses fine but is not an
        object (a bare list) used to crash configure_defaults() with an
        uncaught AttributeError from ``list.get`` — reproduced directly
        against the real Container before this fix landed."""
        import json

        from leggie.application.ports.citation_parser import CitationParserPort
        from leggie.infrastructure import container as container_module
        from leggie.infrastructure.citation import GreekCitationParser

        bad_index = tmp_path / "citation_index.json"
        bad_index.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        self._patch_index_path(monkeypatch, bad_index)

        warnings: list[str] = []
        monkeypatch.setattr(
            container_module.logger,
            "warning",
            lambda msg, *a: warnings.append(msg % a if a else msg),
        )

        c = container_module.Container()
        c.configure_defaults()  # must not raise

        parser = c.get(CitationParserPort)
        assert isinstance(parser, GreekCitationParser)
        assert parser._resolution_index == set()
        assert any("malformed_shape" in w for w in warnings), warnings

    def test_unreadable_index_file_is_logged_not_silent(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ):
        """Boundary: a real OSError during the read (here: the resolved
        'file' path is actually a directory, so .read_text() raises
        IsADirectoryError, an OSError subclass) must be logged — the
        pre-existing except clause silently swallowed this with zero
        logging, violating leggie-change-control non-negotiable #6."""
        from leggie.application.ports.citation_parser import CitationParserPort
        from leggie.infrastructure import container as container_module

        a_directory = tmp_path / "citation_index.json"
        a_directory.mkdir()
        self._patch_index_path(monkeypatch, a_directory)

        warnings: list[str] = []
        monkeypatch.setattr(
            container_module.logger,
            "warning",
            lambda msg, *a: warnings.append(msg % a if a else msg),
        )

        c = container_module.Container()
        c.configure_defaults()  # must not raise

        parser = c.get(CitationParserPort)
        assert parser._resolution_index == set()
        assert any("load_failed" in w for w in warnings), warnings

    def test_missing_index_file_stays_quiet(self, tmp_path, monkeypatch: pytest.MonkeyPatch):
        """No-regression: a simply-absent index file (e.g. package data not
        installed) is the pre-existing, intentionally-quiet degrade path —
        it must stay quiet (no warning spam for a routine dev/test
        environment), unlike a file that exists but is broken."""
        from leggie.application.ports.citation_parser import CitationParserPort
        from leggie.infrastructure import container as container_module

        missing = tmp_path / "does_not_exist" / "citation_index.json"
        self._patch_index_path(monkeypatch, missing)

        warnings: list[str] = []
        monkeypatch.setattr(
            container_module.logger,
            "warning",
            lambda msg, *a: warnings.append(msg % a if a else msg),
        )

        c = container_module.Container()
        c.configure_defaults()

        parser = c.get(CitationParserPort)
        assert parser._resolution_index == set()
        assert warnings == []

    @pytest.mark.asyncio
    async def test_well_formed_index_still_loads_correctly(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ):
        """No-regression: a well-formed dict-shaped index still loads its
        identifiers and resolves a known citation, with no warning logged —
        the isinstance guard must not affect the happy path."""
        import json

        from leggie.application.ports.citation_parser import CitationParserPort
        from leggie.domain.models import Citation, CitationScheme
        from leggie.infrastructure import container as container_module

        good_index = tmp_path / "citation_index.json"
        good_index.write_text(json.dumps({"identifiers": ["N.4622/2019"]}), encoding="utf-8")
        self._patch_index_path(monkeypatch, good_index)

        warnings: list[str] = []
        monkeypatch.setattr(
            container_module.logger,
            "warning",
            lambda msg, *a: warnings.append(msg % a if a else msg),
        )

        c = container_module.Container()
        c.configure_defaults()

        parser = c.get(CitationParserPort)
        assert parser._resolution_index == {"N.4622/2019"}
        citation = Citation(
            scheme=CitationScheme.UNKNOWN,
            identifier="N.4622/2019",
            original_text="N.4622/2019",
        )
        resolved = await parser.resolve(citation)
        assert resolved.resolved is True
        assert warnings == []
