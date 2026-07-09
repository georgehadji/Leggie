"""Lens base class — Strategy pattern for legal analysis perspectives.

Each lens represents one analytical perspective on a bill:
    Constitutional | Legal-coherence | Economic | Implementation | EU-&-GDPR

Lenses are interchangeable Strategies behind a common interface.
F1: lenses now accept LLMPort for real LLM-based analysis.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from leggie.application.ports.llm import LLMPort
from leggie.domain.models import Article, Finding


class Lens(ABC):
    """A legal analysis lens — Strategy pattern.

    F1: Constructor takes LLMPort for real LLM analysis.
    analyze(article) makes a structured LLM call.
    """

    def __init__(self, llm: LLMPort | None = None, model: str = "") -> None:
        self._llm = llm
        self._model = model

    @abstractmethod
    def name(self) -> str:
        """Human-readable lens name, e.g. 'constitutional'."""
        ...

    @abstractmethod
    def description(self) -> str:
        """What this lens analyzes."""
        ...

    @abstractmethod
    async def analyze(self, article: Article) -> list[Finding]:
        """Analyze an article from this lens's perspective.

        Returns a list of findings (may be empty if nothing found).
        """
        ...

    def _prompt_for(self, name: str) -> tuple[str, str]:
        """Load system + user prompt templates for this lens."""
        import importlib
        mod = importlib.import_module(f"leggie.application.agents.prompts.{name}")
        return mod.SYSTEM_PROMPT, mod.USER_PROMPT_TEMPLATE

    async def _call_llm_structured(self, schema: type, prompt: str, system: str = "") -> Any:
        """Call the LLM with structured output parsing."""
        if not self._llm:
            return None
        from leggie.application.ports.llm import LLMRequest
        request = LLMRequest(
            prompt=prompt,
            system_prompt=system,
            model=self._model or None,
            response_format={"type": "json_object"},
        )
        obj, _ = await self._llm.generate_structured(request, schema)
        return obj
