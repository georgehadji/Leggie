"""BlackboardAggregator — event-sourced aggregation via Blackboard + Observer pipeline.

Bridges the step-based aggregation pipeline (dedup → skeptic → CoVe → rerank)
onto the Blackboard substrate. Each step is an Observer that reacts to findings posted
to the board. A Mediator drives rounds until convergence.

EN3: Replaces in-place self._findings mutation with append-only blackboard mutations.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from leggie.application.blackboard import Blackboard, BlackboardEntry
from leggie.application.services.cove_verifier import CoVeVerifier
from leggie.application.services.rerank import CompositeReranker, Reranker
from leggie.application.agents.skeptic import CalibratedSkeptic
from leggie.domain.clustering import deduplicate
from leggie.domain.models import Event, EventType, Finding

log = logging.getLogger(__name__)


def _finding_similarity_article_aware(a: Finding, b: Finding) -> float:
    """Score similarity between two findings by (article, type, lens) + issue overlap."""
    import re
    _article_re = re.compile(r"Άρθρο\s+(\d+)", re.IGNORECASE)

    def _article_prefix(f: Finding) -> str:
        m = _article_re.search(f.irac.issue)
        return m.group(1) if m else ""

    if (a.finding_type != b.finding_type or
        a.lens != b.lens or
        _article_prefix(a) != _article_prefix(b)):
        return 0.0
    a_tokens = set(a.irac.issue.lower().split())
    b_tokens = set(b.irac.issue.lower().split())
    if not a_tokens or not b_tokens:
        return 0.0
    return len(a_tokens & b_tokens) / max(len(a_tokens), len(b_tokens))


class BlackboardAggregator:
    """Aggregates findings using Blackboard + Observer pipeline.

    The pipeline: post → dedup → skeptic → CoVe → rerank.
    Each observer transforms findings and posts results to the next round.

    Rerank runs LAST, after the verification gauntlet, because both the
    skeptic and CoVe rewrite ``Finding.confidence`` — the very field the
    composite score is built from. Ranking before them published an order
    derived from scores the surviving findings no longer carried.
    """

    def __init__(
        self,
        dedup_threshold: float = 0.85,
        reranker: Reranker | None = None,
        skeptic: CalibratedSkeptic | None = None,
        cove: CoVeVerifier | None = None,
        blackboard: Blackboard | None = None,
    ) -> None:
        self._board = blackboard or Blackboard()
        self._dedup_threshold = dedup_threshold
        self._reranker = reranker or CompositeReranker()
        self._skeptic = skeptic or CalibratedSkeptic()
        self._cove = cove or CoVeVerifier()
        self._events: list[Event] = []

    @property
    def board(self) -> Blackboard:
        return self._board

    @property
    def events(self) -> list[Event]:
        return list(self._events)

    async def aggregate(
        self, findings: list[Finding], article_index: dict[str, str] | None = None
    ) -> list[Finding]:
        """Run the full aggregation pipeline via Blackboard rounds.

        Round 1: Post all findings → dedup observer collapses duplicates
        Round 2: Skeptic observer filters refuted and recalibrates confidence
        Round 3: CoVe Chain-of-Verification revises or drops findings
        Round 4: Rerank observer orders the verified survivors

        Rerank is last so the published order reflects post-verification
        confidence and novelty measured against the surviving population.
        """
        if not findings:
            return []

        # Round 1 — Post raw findings, dedup reacts
        board = self._board
        dedup_observer = _DedupObserver(self._dedup_threshold)
        board.subscribe(dedup_observer.handle)

        for f in findings:
            board.post(f, agent_id="orchestrator")
            self._record_event(EventType.FINDING_CREATED, {
                "finding_id": str(f.id),
                "lens": f.lens,
                "type": f.finding_type.value,
            })

        dedup_survivors = dedup_observer.get_survivors()
        dedup_count = len(findings) - len(dedup_survivors)
        if dedup_count:
            self._record_event(EventType.DEDUP_REMOVED, {
                "removed": dedup_count,
                "survivors": len(dedup_survivors),
            })
        board.unsubscribe(dedup_observer.handle)

        # If dedup removed everything, stop early
        if not dedup_survivors:
            return self._complete([])

        # Round 2 — Skeptic review
        board.next_round()
        for f in dedup_survivors:
            board.post(f, agent_id="dedup")

        survivors, _ = await self._skeptic.review(dedup_survivors)
        refuted_count = len(dedup_survivors) - len(survivors)
        if refuted_count:
            self._record_event(EventType.FINDING_REFUTED, {
                "refuted": refuted_count,
                "survivors": len(survivors),
            })
        if not survivors:
            return self._complete([])

        # Round 3 — CoVe citation verification
        board.next_round()
        for f in survivors:
            board.post(f, agent_id="skeptic")

        cove_results = await self._cove.verify_batch(survivors, article_index)
        verified = [r.finding for r in cove_results if not r.dropped]
        dropped = sum(1 for r in cove_results if r.dropped)
        unverified = sum(1 for r in cove_results if not r.all_verified)
        if dropped:
            self._record_event(EventType.FINDING_REFUTED, {
                "refuted": dropped,
                "survivors": len(verified),
                "stage": "cove",
            })
        if unverified:
            self._record_event(EventType.CITATION_FAILED, {
                "unverified": unverified,
            })
        else:
            self._record_event(EventType.CITATION_VERIFIED, {
                "verified": len(verified),
            })
        if not verified:
            return self._complete([])

        # Round 4 — Rerank the verified survivors into the published order
        board.next_round()
        for f in verified:
            board.post(f, agent_id="cove")

        scored = await self._reranker.rerank(verified)
        reranked = [s.finding for s in scored]

        board.next_round()
        for f in reranked:
            board.post(f, agent_id="reranker")

        return self._complete(reranked)

    def _complete(self, findings: list[Finding]) -> list[Finding]:
        """Record the terminal aggregation event and return the final findings.

        Every terminal path records AGGREGATION_COMPLETED — including the ones
        that end with zero findings, which previously returned silently and
        left the event log without a completion record (event-spine invariant).
        """
        self._record_event(EventType.AGGREGATION_COMPLETED, {
            "rounds": self._board.round_count,
            "final_findings": len(findings),
        })
        return findings

    def _record_event(self, event_type: EventType, data: dict[str, Any]) -> None:
        self._events.append(
            Event(
                event_type=event_type,
                aggregate_id="blackboard-aggregator",
                data=data,
            )
        )


class _DedupObserver:
    """Blackboard observer: collapses near-duplicate findings."""

    def __init__(self, threshold: float = 0.85) -> None:
        self._threshold = threshold
        self._posted: list[Finding] = []

    def handle(self, entry: BlackboardEntry, board: Blackboard) -> None:
        """Collect posted findings for dedup."""
        self._posted.append(entry.finding)

    def get_survivors(self) -> list[Finding]:
        """Run dedup on all collected findings."""
        if not self._posted:
            return []
        return deduplicate(
            self._posted,
            similarity_fn=_finding_similarity_article_aware,
            threshold=self._threshold,
            keep="highest_confidence",
        )
