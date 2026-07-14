"""ReasonerPort — abstract interface for multi-model deliberative analysis.

Provider-agnostic adapter for the Reasoner service's Agent API.
Handles two-stage reasoning: generation (Stage 1) and critique (Stage 2).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from leggie.domain.models import Citation


@dataclass(frozen=True)
class ReasonerRequest:
    """Request to the Reasoner service."""

    problem: str
    preset: str = "multi-perspective-premium"
    top_k: int = 2
    sequential: bool = False
    no_cache: bool = False
    web_search: bool = False
    client_run_id: str | None = None


@dataclass(frozen=True)
class ReasonerResult:
    """Result from a Reasoner reasoning run."""

    synthesis: str
    critical_insights: list[str]
    open_questions: list[str]
    citations: list[Citation]
    models_used: list[str]
    total_tokens: dict[str, int]
    duration_seconds: float
    errors: list[str]


class ReasonerUnavailableError(Exception):
    """Raised when Reasoner service is unreachable or unhealthy."""

    def __init__(self, message: str, cause: Exception | None = None):
        super().__init__(message)
        self.cause = cause


class ReasonerPort(ABC):
    """Port for multi-model reasoning via Reasoner service."""

    @abstractmethod
    async def reason(self, request: ReasonerRequest) -> ReasonerResult:
        """Execute a reasoning task and return the result."""
        ...
