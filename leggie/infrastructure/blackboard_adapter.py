"""Blackboard Adapter — wraps Blackboard behind BlackboardPort."""

from __future__ import annotations

from leggie.application.blackboard import Blackboard as BlackboardService
from leggie.application.ports.blackboard import BlackboardEntry, BlackboardPort


class BlackboardAdapter(BlackboardPort):
    """Concrete adapter — delegates to the Blackboard service.

    Translates between the port-level BlackboardEntry (which uses 'round')
    and the service-level BlackboardEntry (which uses 'round_number').
    """

    def __init__(self) -> None:
        self._service = BlackboardService()

    async def post_finding(self, entry: BlackboardEntry) -> None:
        self._service.post(entry.finding, agent_id=entry.agent_id, metadata=entry.metadata)

    async def get_findings(
        self, round_min: int = 0, agent_id: str | None = None
    ) -> list[BlackboardEntry]:
        """Get findings from the blackboard, filtered by round and/or agent.

        Returns port-level BlackboardEntry objects constructed from the
        service-level entries.
        """
        if agent_id is not None:
            service_entries = self._service.get_entries_by_agent(agent_id)
        else:
            service_entries = self._service.get_entries()
        result: list[BlackboardEntry] = []
        for se in service_entries:
            if se.round_number < round_min:
                continue
            result.append(
                BlackboardEntry(
                    finding=se.finding,
                    agent_id=se.agent_id,
                    round=se.round_number,
                    metadata=dict(se.metadata),
                )
            )
        return result

    async def get_all_findings(self) -> list[BlackboardEntry]:
        """Get all findings across all rounds with preserved agent_id."""
        service_entries = self._service.get_entries()
        return [
            BlackboardEntry(
                finding=se.finding,
                agent_id=se.agent_id,
                round=se.round_number,
                metadata=dict(se.metadata),
            )
            for se in service_entries
        ]

    async def clear_round(self, round_number: int) -> None:
        """Clear entries from a specific round via the service."""
        self._service.clear_round(round_number)
