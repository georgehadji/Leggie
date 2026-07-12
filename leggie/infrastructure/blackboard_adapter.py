"""Blackboard Adapter — wraps Blackboard behind BlackboardPort."""

from __future__ import annotations

from leggie.application.blackboard import Blackboard as BlackboardService
from leggie.application.ports.blackboard import BlackboardEntry, BlackboardPort


class BlackboardAdapter(BlackboardPort):
    """Concrete adapter — delegates to the Blackboard service."""

    def __init__(self) -> None:
        self._service = BlackboardService()

    async def post_finding(self, entry: BlackboardEntry) -> None:
        self._service.post(entry.finding, agent_id=entry.agent_id, metadata=entry.metadata)

    async def get_findings(
        self, _round_min: int = 0, _agent_id: str | None = None
    ) -> list[BlackboardEntry]:
        findings = self._service.get_all_findings()
        return [BlackboardEntry(finding=f, agent_id="") for f in findings]

    async def get_all_findings(self) -> list[BlackboardEntry]:
        findings = self._service.get_all_findings()
        return [BlackboardEntry(finding=f, agent_id="") for f in findings]

    async def clear_round(self, round_number: int) -> None:
        pass
