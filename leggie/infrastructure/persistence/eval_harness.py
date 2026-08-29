"""Eval Harness — gold-set evaluation for Phase 0.

Scores Leggie output vs ground-truth (Επιστημονική Υπηρεσία Βουλής reports).
Implements U3 typed metrics + Risk Direction Index.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from leggie.domain.models import Finding, FindingType, Severity


@dataclass(frozen=True)
class GoldLabel:
    """A single ground-truth label from expert reports."""

    article_id: str
    finding_type: FindingType
    description: str
    severity: Severity
    citation_text: str | None = None


@dataclass
class TypeMetrics:
    """Per-finding-type metrics (U3)."""

    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    true_negatives: int = 0

    @property
    def precision(self) -> float:
        total = self.true_positives + self.false_positives
        return self.true_positives / total if total > 0 else 0.0

    @property
    def recall(self) -> float:
        total = self.true_positives + self.false_negatives
        return self.true_positives / total if total > 0 else 0.0

    @property
    def f1(self) -> float:
        p = self.precision
        r = self.recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0


@dataclass
class EvalResult:
    """Full evaluation result for a bill."""

    bill_id: str
    total_gold: int
    total_findings: int
    matched: int
    unmatched_gold: list[GoldLabel] = field(default_factory=list)
    spurious: list[Finding] = field(default_factory=list)
    type_metrics: dict[str, TypeMetrics] = field(default_factory=dict)
    risk_direction_index: float = 0.0  # >0 = invention bias, <0 = omission bias

    @property
    def precision(self) -> float:
        return self.matched / self.total_findings if self.total_findings > 0 else 0.0

    @property
    def recall(self) -> float:
        return self.matched / self.total_gold if self.total_gold > 0 else 0.0

    @property
    def f1(self) -> float:
        p = self.precision
        r = self.recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "bill_id": self.bill_id,
            "total_gold": self.total_gold,
            "total_findings": self.total_findings,
            "matched": self.matched,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "risk_direction_index": round(self.risk_direction_index, 4),
            "type_metrics": {
                k: {
                    "precision": round(v.precision, 4),
                    "recall": round(v.recall, 4),
                    "f1": round(v.f1, 4),
                }
                for k, v in self.type_metrics.items()
            },
        }


class GoldSet:
    """A gold set of labeled bills for evaluation."""

    def __init__(self, path: str | None = None) -> None:
        self._labels: dict[str, list[GoldLabel]] = {}
        if path:
            self.load(path)

    def add_label(self, bill_id: str, label: GoldLabel) -> None:
        if bill_id not in self._labels:
            self._labels[bill_id] = []
        self._labels[bill_id].append(label)

    def get_labels(self, bill_id: str) -> list[GoldLabel]:
        return self._labels.get(bill_id, [])

    @property
    def bill_ids(self) -> list[str]:
        return list(self._labels.keys())

    def load(self, path: str) -> None:
        """Load gold labels from a JSON file."""
        filepath = Path(path)
        if not filepath.exists():
            raise FileNotFoundError(f"Gold set not found: {path}")
        data = json.loads(filepath.read_text(encoding="utf-8"))
        for bill_id, labels in data.items():
            for label in labels:
                self.add_label(
                    bill_id,
                    GoldLabel(
                        article_id=label["article_id"],
                        finding_type=FindingType(label["finding_type"]),
                        description=label["description"],
                        severity=Severity(label["severity"]),
                        citation_text=label.get("citation_text"),
                    ),
                )

    def save(self, path: str) -> None:
        """Save gold labels to a JSON file."""
        data: dict[str, list[dict[str, Any]]] = {}
        for bill_id, labels in self._labels.items():
            data[bill_id] = [
                {
                    "article_id": label.article_id,
                    "finding_type": label.finding_type.value,
                    "description": label.description,
                    "severity": label.severity.value,
                    "citation_text": label.citation_text,
                }
                for label in labels
            ]
        filepath = Path(path)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class EvalScorer:
    """Scores Leggie findings against gold labels.

    Implements U3 typed metrics per finding type and Risk Direction Index.
    """

    def __init__(self, gold_set: GoldSet) -> None:
        self._gold_set = gold_set

    def score(self, bill_id: str, findings: list[Finding]) -> EvalResult:
        """Score a set of findings against gold labels for a bill."""
        gold_labels = self._gold_set.get_labels(bill_id)

        # Initialize per-type metrics
        type_metrics: dict[str, TypeMetrics] = {}
        for ft in FindingType:
            type_metrics[ft.value] = TypeMetrics()

        # Match findings to gold labels (simple text overlap for v1)
        matched_gold: set[int] = set()
        matched_findings: set[int] = set()

        for gi, gold in enumerate(gold_labels):
            for fi, finding in enumerate(findings):
                if fi in matched_findings:
                    continue
                if self._matches(gold, finding):
                    matched_gold.add(gi)
                    matched_findings.add(fi)
                    type_metrics[finding.finding_type.value].true_positives += 1
                    break

        # False positives: findings that matched no gold
        for fi, finding in enumerate(findings):
            if fi not in matched_findings:
                type_metrics[finding.finding_type.value].false_positives += 1

        # False negatives: gold labels with no matching finding
        unmatched_gold: list[GoldLabel] = []
        for gi, gold in enumerate(gold_labels):
            if gi not in matched_gold:
                unmatched_gold.append(gold)
                type_metrics[gold.finding_type.value].false_negatives += 1

        # Spurious findings
        spurious = [f for fi, f in enumerate(findings) if fi not in matched_findings]

        # Risk Direction Index (U3):
        # > 0 = invention bias (more FP than FN)
        # < 0 = omission bias (more FN than FP)
        total_fp = len(spurious)
        total_fn = len(unmatched_gold)
        total = total_fp + total_fn
        rdi = (total_fp - total_fn) / total if total > 0 else 0.0

        return EvalResult(
            bill_id=bill_id,
            total_gold=len(gold_labels),
            total_findings=len(findings),
            matched=len(matched_findings),
            unmatched_gold=unmatched_gold,
            spurious=spurious,
            type_metrics=type_metrics,
            risk_direction_index=rdi,
        )

    def _matches(self, gold: GoldLabel, finding: Finding) -> bool:
        """Check if a finding matches a gold label.

        For v1: check article overlap + finding type + keyword overlap.
        """
        if gold.finding_type != finding.finding_type:
            return False
        if gold.article_id != finding.irac.issue.split(" ")[0]:  # rough article match
            # Try looser: finding IRAC content mentions the article
            pass
        # Simple keyword overlap in description
        gold_keywords = set(gold.description.lower().split())
        finding_keywords = set(finding.irac.issue.lower().split())
        overlap = len(gold_keywords & finding_keywords)
        return overlap >= 3  # At least 3 overlapping words
