"""Tests for Blackboard — schema-grounded aggregation."""

from leggie.application.blackboard import Blackboard, BlackboardEntry
from leggie.domain.models import IRAC, Confidence, Finding, FindingType


def make_finding(issue: str = "test") -> Finding:
    return Finding(
        finding_type=FindingType.CONSTITUTIONAL,
        irac=IRAC(issue=issue, rule="r", application="a", conclusion="c"),
        confidence=Confidence.from_score(0.5),
        lens="test",
        model="test",
    )


class TestBlackboard:
    def test_post_adds_entry(self):
        bb = Blackboard()
        finding = make_finding()
        entry = bb.post(finding, agent_id="constitutional")
        assert isinstance(entry, BlackboardEntry)
        assert entry.agent_id == "constitutional"

    def test_get_all_findings(self):
        bb = Blackboard()
        bb.post(make_finding("A"), agent_id="lens1")
        bb.post(make_finding("B"), agent_id="lens2")
        assert len(bb.get_all_findings()) == 2

    def test_rounds(self):
        bb = Blackboard()
        assert bb.current_round == 1
        bb.post(make_finding(), agent_id="lens1")
        bb.next_round()
        assert bb.current_round == 2
        assert bb.round_count == 2

    def test_round_findings(self):
        bb = Blackboard()
        bb.post(make_finding("Round1"), agent_id="lens1")
        bb.next_round()
        bb.post(make_finding("Round2"), agent_id="lens2")
        assert len(bb.get_round_findings(1)) == 1
        assert len(bb.get_round_findings(2)) == 1

    def test_entries_by_agent(self):
        bb = Blackboard()
        bb.post(make_finding("A"), agent_id="lens1")
        bb.post(make_finding("B"), agent_id="lens2")
        bb.post(make_finding("C"), agent_id="lens1")
        entries = bb.get_entries_by_agent("lens1")
        assert len(entries) == 2

    def test_observer_notified(self):
        bb = Blackboard()
        observed = []

        def observer(entry, board):
            observed.append(entry.finding.irac.issue)

        bb.subscribe(observer)
        bb.post(make_finding("observed"), agent_id="test")
        assert "observed" in observed

    def test_unsubscribe(self):
        bb = Blackboard()
        observed = []

        def observer(entry, board):
            observed.append("x")

        bb.subscribe(observer)
        bb.unsubscribe(observer)
        bb.post(make_finding(), agent_id="test")
        assert len(observed) == 0

    def test_total_entries(self):
        bb = Blackboard()
        bb.post(make_finding(), agent_id="a")
        bb.post(make_finding(), agent_id="b")
        assert bb.total_entries == 2

    def test_clear(self):
        bb = Blackboard()
        bb.post(make_finding(), agent_id="a")
        bb.clear()
        assert bb.total_entries == 0
        assert bb.current_round == 1

    def test_get_entries(self):
        bb = Blackboard()
        bb.post(make_finding("A"), agent_id="lens1")
        bb.post(make_finding("B"), agent_id="lens2")
        entries = bb.get_entries()
        assert len(entries) == 2
        assert entries[0].agent_id == "lens1"
        assert entries[1].agent_id == "lens2"

    def test_clear_round(self):
        bb = Blackboard()
        bb.post(make_finding("R1"), agent_id="lens1")
        bb.next_round()
        bb.post(make_finding("R2"), agent_id="lens2")
        assert bb.total_entries == 2
        bb.clear_round(1)
        assert bb.total_entries == 1
        assert bb.get_round_findings(1) == []
