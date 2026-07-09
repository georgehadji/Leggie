"""Stage base class — Template Method for stage lifecycle.

Each workflow stage follows a fixed skeleton:
    plan → execute → aggregate → verify

Subclasses override the varying steps. This implements the Template Method
pattern per BUILD_PLAN §2 and §5.4.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from leggie.domain.models import Finding


@dataclass
class StageContext:
    """Mutable context passed through a stage's lifecycle.

    Carries state across plan→execute→aggregate→verify.
    """
    article_text: str
    article_id: str
    findings: list[Finding] = field(default_factory=list)
    intermediate_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class StageResult:
    """Result of a completed stage."""
    success: bool
    findings: list[Finding] = field(default_factory=list)
    error: str | None = None


class Stage(ABC):
    """Base class for a workflow stage with Template Method lifecycle.

    Lifecycle:
        1. plan(context)    — Determine work to do
        2. execute(context) — Perform the work
        3. aggregate(context) — Collect and combine results
        4. verify(context)  — Validate output
    """

    @abstractmethod
    def stage_name(self) -> str: ...

    # ── Template Method ─────────────────────────────────────────────

    async def run(self, context: StageContext) -> StageResult:
        """Template Method: executes the full stage lifecycle."""
        try:
            await self._plan(context)
            await self._execute(context)
            await self._aggregate(context)
            await self._verify(context)
            return StageResult(success=True, findings=context.findings)
        except Exception as e:
            return StageResult(success=False, error=str(e))

    # ── Lifecycle hooks (override as needed) ────────────────────────

    async def _plan(self, context: StageContext) -> None:
        """Plan the work for this stage. Default: no-op."""

    @abstractmethod
    async def _execute(self, context: StageContext) -> None:
        """Execute the stage's core work."""

    async def _aggregate(self, context: StageContext) -> None:
        """Aggregate results within the stage. Default: no-op."""

    async def _verify(self, context: StageContext) -> None:
        """Verify stage output. Default: no-op."""
