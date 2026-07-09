"""Tests for the eval harness — gold set and scorer."""

import pytest

from leggie.domain.models import IRAC, Confidence, Finding, FindingType, Severity
from leggie.infrastructure.persistence.eval_harness import (
    EvalResult,
    EvalScorer,
    GoldLabel,
    GoldSet,
)


@pytest.fixture
def gold_set(tmp_path):
    gs = GoldSet()
    gs.add_label("bill-001", GoldLabel(
        article_id="1",
        finding_type=FindingType.CONSTITUTIONAL,
        description="Exceeds delegation limits per Article 43",
        severity=Severity.CRITICAL,
    ))
    gs.add_label("bill-001", GoldLabel(
        article_id="3",
        finding_type=FindingType.EU_COMPLIANCE,
        description="Definition diverges from Directive 2018/1972",
        severity=Severity.HIGH,
    ))
    gs.add_label("bill-002", GoldLabel(
        article_id="2",
        finding_type=FindingType.TEMPORAL,
        description="Transition period of 15 days is impractical",
        severity=Severity.HIGH,
    ))
    return gs


class TestGoldSet:
    def test_add_and_get_labels(self, gold_set):
        labels = gold_set.get_labels("bill-001")
        assert len(labels) == 2
        assert labels[0].article_id == "1"

    def test_get_nonexistent_bill(self, gold_set):
        labels = gold_set.get_labels("nonexistent")
        assert labels == []

    def test_bill_ids(self, gold_set):
        assert set(gold_set.bill_ids) == {"bill-001", "bill-002"}

    def test_save_and_load(self, gold_set, tmp_path):
        path = tmp_path / "test_gold.json"
        gold_set.save(str(path))
        assert path.exists()

        loaded = GoldSet(str(path))
        assert len(loaded.get_labels("bill-001")) == 2
        assert len(loaded.get_labels("bill-002")) == 1


def make_finding(finding_type, issue_text="test issue", confidence=0.7, severity="medium"):
    return Finding(
        finding_type=finding_type,
        irac=IRAC(
            issue=issue_text,
            rule="test rule",
            application="test application",
            conclusion="test conclusion",
        ),
        confidence=Confidence.from_score(confidence),
        severity=Severity(severity),
        lens="test",
        model="test-model",
    )


class TestEvalScorer:
    def test_score_perfect_match(self, gold_set):
        # Finding that matches the first gold label
        findings = [
            make_finding(
                FindingType.CONSTITUTIONAL,
                issue_text="Exceeds delegation limits per Article 43",
                severity="critical",
            ),
        ]
        scorer = EvalScorer(gold_set)
        result = scorer.score("bill-001", findings)
        assert result.matched == 1

    def test_score_no_match(self, gold_set):
        findings = [
            make_finding(FindingType.ECONOMIC, issue_text="Fiscal impact analysis"),
        ]
        scorer = EvalScorer(gold_set)
        result = scorer.score("bill-001", findings)
        assert result.matched == 0
        assert len(result.unmatched_gold) == 2
        assert len(result.spurious) == 1

    def test_score_partial_match(self, gold_set):
        findings = [
            make_finding(
                FindingType.CONSTITUTIONAL,
                issue_text="Exceeds delegation limits per Article 43",
                severity="critical",
            ),
            make_finding(FindingType.ECONOMIC, issue_text="Fiscal impact"),
        ]
        scorer = EvalScorer(gold_set)
        result = scorer.score("bill-001", findings)
        assert result.matched == 1
        assert len(result.spurious) == 1

    def test_risk_direction_index_invention(self, gold_set):
        # All spurious = invention bias (need more FPs than FNs)
        findings = [
            make_finding(FindingType.ECONOMIC),
            make_finding(FindingType.IMPLEMENTATION),
            make_finding(FindingType.PROCEDURAL),
        ]
        scorer = EvalScorer(gold_set)
        result = scorer.score("bill-001", findings)
        assert result.risk_direction_index > 0  # 3 FP > 2 FN = invention bias

    def test_risk_direction_index_omission(self, gold_set):
        # No findings at all = omission bias (RDI = -1.0)
        scorer = EvalScorer(gold_set)
        result = scorer.score("bill-001", [])
        assert result.risk_direction_index < 0  # omission bias

    def test_result_metrics(self, gold_set):
        scorer = EvalScorer(gold_set)
        result = scorer.score("bill-001", [])
        assert isinstance(result, EvalResult)
        assert result.bill_id == "bill-001"
        assert result.total_gold == 2
        assert result.total_findings == 0
        assert result.precision == 0.0
        assert result.recall == 0.0
        assert result.f1 == 0.0

    def test_type_metrics_included(self, gold_set):
        scorer = EvalScorer(gold_set)
        result = scorer.score("bill-001", [])
        assert "eu_compliance" in result.type_metrics
        assert "constitutional" in result.type_metrics
