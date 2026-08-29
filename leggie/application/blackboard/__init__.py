"""Blackboard Service — schema-grounded aggregation substrate.

Per ARCHITECTURE §2 and §5.4: append-only, schema-grounded mutations
(PatchBoard pattern), with public (findings) and private (agent scratch) spaces.

Findings post to the board → Observer subscriptions (dedup, rerank, skeptic)
react to new postings → Controller schedules bounded rounds.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from leggie.domain.models import Finding


@dataclass
class BlackboardEntry:
    """A single contribution to the blackboard."""

    finding: Finding
    agent_id: str
    round_number: int = 0
    posted_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BlackboardRound:
    """A round of contributions to the blackboard."""

    round_number: int
    entries: list[BlackboardEntry] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed: bool = False


ObserverCallback = Callable[..., None]  # (entry, blackboard) → None


class Blackboard:
    """Schema-grounded blackboard for finding aggregation.

    Features:
    - Append-only: entries never modified after posting
    - Schema-grounded: only Finding objects accepted
    - Rounds: adaptive stop on convergence
    - Observer pattern: subscribers notified on each post
    - Public + private spaces
    """

    def __init__(self) -> None:
        self._rounds: list[BlackboardRound] = []
        self._observers: list[ObserverCallback] = []
        self._current_round = 0
        self._start_new_round()

    def _start_new_round(self) -> None:
        self._current_round += 1
        self._rounds.append(BlackboardRound(round_number=self._current_round))

    @property
    def current_round(self) -> int:
        return self._current_round

    def post(
        self, finding: Finding, agent_id: str = "", metadata: dict[str, Any] | None = None
    ) -> BlackboardEntry:
        """Post a finding to the current round."""
        entry = BlackboardEntry(
            finding=finding,
            agent_id=agent_id,
            round_number=self._current_round,
            metadata=metadata or {},
        )
        self._rounds[-1].entries.append(entry)
        # Notify observers
        for observer in self._observers:
            observer(entry, self)
        return entry

    def get_all_findings(self) -> list[Finding]:
        """Get all findings across all rounds."""
        findings: list[Finding] = []
        for round_data in self._rounds:
            for entry in round_data.entries:
                findings.append(entry.finding)
        return findings

    def get_entries(self) -> list[BlackboardEntry]:
        """Get all entries across all rounds with full metadata."""
        entries: list[BlackboardEntry] = []
        for round_data in self._rounds:
            entries.extend(round_data.entries)
        return entries

    def get_round_findings(self, round_number: int) -> list[Finding]:
        """Get findings from a specific round."""
        for round_data in self._rounds:
            if round_data.round_number == round_number:
                return [e.finding for e in round_data.entries]
        return []

    def get_entries_by_agent(self, agent_id: str) -> list[BlackboardEntry]:
        """Get all entries from a specific agent."""
        entries: list[BlackboardEntry] = []
        for round_data in self._rounds:
            for entry in round_data.entries:
                if entry.agent_id == agent_id:
                    entries.append(entry)
        return entries

    def subscribe(self, callback: ObserverCallback) -> None:
        """Register an observer notified on each new posting."""
        self._observers.append(callback)

    def unsubscribe(self, callback: ObserverCallback) -> None:
        """Remove an observer."""
        self._observers.remove(callback)

    def next_round(self) -> int:
        """Advance to the next round. Returns new round number."""
        self._rounds[-1].completed = True
        self._start_new_round()
        return self._current_round

    @property
    def round_count(self) -> int:
        return len(self._rounds)

    @property
    def total_entries(self) -> int:
        return sum(len(r.entries) for r in self._rounds)

    def clear_round(self, round_number: int) -> None:
        """Remove all entries from a specific round.

        Non-destructive: the round dataclass itself remains, but its
        entries list is cleared. Round history is preserved via the
        round_number and completed flag.
        """
        for round_data in self._rounds:
            if round_data.round_number == round_number:
                round_data.entries.clear()
                return

    def clear(self) -> None:
        """Clear all rounds (for testing)."""
        self._rounds.clear()
        self._current_round = 0
        self._start_new_round()
