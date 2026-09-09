"""ARCH-05: the workflows must not build their own infrastructure adapters.

``application/workflow/ingest_parse.py`` held two lazy factories that imported
``IngestAdapter``/``ParseAdapter`` directly. They were the last
application → infrastructure imports in the workflow package, and the reason two
entries sat on the import-linter waiver register.

They are gone. The composition root injects both ports at every production call
site. A flow built without them must name the missing port at the stage that
needs it — not fail obscurely, and not quietly reach back into infrastructure.
"""

from __future__ import annotations

import asyncio

import pytest

from leggie.application.workflow.bill_analysis_flow import BillAnalysisFlow
from leggie.application.workflow.deliberative_flow import DeliberativeFlow

SAMPLE_BILL = """
ΣΧΕΔΙΟ ΝΟΜΟΥ
«Δοκιμαστικό νομοσχέδιο»

Άρθρο 1 – Απλή διάταξη
1. Η ισχύς του παρόντος αρχίζει από τη δημοσίευσή του.
"""


@pytest.fixture
def bill_file(tmp_path):
    path = tmp_path / "bill.txt"
    path.write_text(SAMPLE_BILL, encoding="utf-8")
    return path


class TestNoInfrastructureFallback:
    def test_bill_flow_builds_no_adapter_of_its_own(self):
        flow = BillAnalysisFlow()
        assert flow._ingester is None
        assert flow._parser is None

    def test_deliberative_flow_builds_no_adapter_of_its_own(self):
        flow = DeliberativeFlow(reasoner=None, stage1_preset="p1", stage2_preset="p2")
        assert flow._ingester is None
        assert flow._parser is None


class TestMissingPortIsNamed:
    def test_ingest_stage_names_the_port(self, bill_file):
        flow = BillAnalysisFlow()
        with pytest.raises(ValueError, match="IngestPort"):
            asyncio.run(flow._do_ingest(bill_file))

    def test_parse_stage_names_the_port(self, bill_file):
        flow = BillAnalysisFlow()
        with pytest.raises(ValueError, match="ParsePort"):
            flow._do_parse(SAMPLE_BILL, bill_file)

    def test_deliberative_run_names_the_ports(self, bill_file):
        flow = DeliberativeFlow(reasoner=None, stage1_preset="p1", stage2_preset="p2")
        with pytest.raises(ValueError, match="IngestPort/ParsePort"):
            asyncio.run(flow.run(bill_file))


class TestInjectedPortsAreUsedVerbatim:
    """No wrapping, no substitution — what the container hands over is what runs."""

    def test_bill_flow_stores_what_it_is_given(self):
        from leggie.infrastructure.ingest_adapter import IngestAdapter
        from leggie.infrastructure.parse_adapter import ParseAdapter

        ingester, parser = IngestAdapter(), ParseAdapter()
        flow = BillAnalysisFlow(ingester=ingester, parser=parser)

        assert flow._ingester is ingester
        assert flow._parser is parser

    def test_deliberative_flow_stores_what_it_is_given(self):
        from leggie.infrastructure.ingest_adapter import IngestAdapter
        from leggie.infrastructure.parse_adapter import ParseAdapter

        ingester, parser = IngestAdapter(), ParseAdapter()
        flow = DeliberativeFlow(
            reasoner=None,
            stage1_preset="p1",
            stage2_preset="p2",
            ingester=ingester,
            parser=parser,
        )

        assert flow._ingester is ingester
        assert flow._parser is parser
