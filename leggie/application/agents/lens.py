"""Lens base class — Strategy pattern for legal analysis perspectives.

Each lens represents one analytical perspective on a bill:
    Constitutional | Legal-coherence | Economic | Implementation | EU-&-GDPR

Lenses are interchangeable Strategies behind a common interface.
F1: lenses now accept LLMPort for real LLM-based analysis.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from leggie.application.ports.llm import LLMPort, LLMRequest
from leggie.domain.models import Article, Event, EventType, Finding
from leggie.domain.models.structured_output import IRACCandidate

log = logging.getLogger(__name__)


class Lens(ABC):
    """A legal analysis lens — Strategy pattern.

    F1: Constructor takes LLMPort for real LLM analysis.
    analyze(article) makes a structured LLM call.
    """

    def __init__(
        self,
        llm: LLMPort | None = None,
        model: str = "",
        on_degradation: Callable[..., None] | None = None,
        use_verbalized_sampling: bool = False,
    ) -> None:
        self._llm = llm
        self._model = model
        self._on_degradation = on_degradation
        self._use_verbalized_sampling = use_verbalized_sampling

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

    @abstractmethod
    def _candidate_to_finding(self, c: IRACCandidate, article: Article) -> Finding:
        """Convert a raw LLM candidate into a domain Finding.

        Each lens sets its own FindingType and provenance here.
        """
        ...

    def _emit_degradation(self, article: Article, exc: Exception) -> None:
        """Emit a degradation event if a callback is registered."""
        if self._on_degradation is None:
            return
        try:
            self._on_degradation(
                Event(
                    event_type=EventType.DEGRADED,
                    aggregate_id=f"lens:{self.name()}:article:{article.id}",
                    data={
                        "lens": self.name(),
                        "article_id": article.id,
                        "error": str(exc)[:500],
                        "model": self._model,
                    },
                )
            )
        except Exception:
            log.warning("on_degradation callback failed", exc_info=True)

    def _prompt_for(self, name: str) -> tuple[str, str]:
        """Load system + user prompt templates for this lens."""
        import importlib

        mod = importlib.import_module(f"leggie.application.agents.prompts.{name}")
        return mod.SYSTEM_PROMPT, mod.USER_PROMPT_TEMPLATE

    async def _call_llm_structured(self, schema: type, prompt: str, system: str = "") -> Any:
        """Call the LLM with structured output parsing.

        Includes post-generation language check (Greek-script ratio).
        On failure, one bounded retry with a stricter instruction.
        """
        if not self._llm:
            return None
        request = LLMRequest(
            prompt=prompt,
            system_prompt=system,
            model=self._model or None,
            response_format={"type": "json_object"},
        )
        obj, _ = await self._llm.generate_structured(request, schema)
        return await self._maybe_retry_greek(obj, schema, request, system)

    async def _analyze_with_vs(self, prompt_name: str, article: Article) -> list[Finding]:
        """Run Verbalized Sampling: one call, k candidates, tail-sampled.

        Uses the lens's prompt templates and LLM port.
        Returns k diverse findings (lowest-probability tail).
        Falls back to standard _analyze_llm if LLM unavailable or VS fails.
        """
        if not self._llm:
            return []
        from leggie.application.services.lens_vs import LensVerbalizedSampling

        system, template = self._prompt_for(prompt_name)
        vs = LensVerbalizedSampling(
            llm=self._llm,
            lens_name=self.name(),
            model=self._model,
            system_prompt=system,
            user_template=template,
            k=5,
            seed=getattr(self, "_seed", 0),
        )
        return await vs.generate(self, article)

    async def _maybe_retry_greek(
        self, obj: Any, schema: type, request: LLMRequest, system: str
    ) -> Any:
        """Check Greek-script ratio and retry once with stricter instruction if low."""
        from leggie.domain.models import is_greek

        if obj is None:
            return obj
        # Flatten all string fields of the parsed object for Greek ratio check
        import dataclasses

        text_parts: list[str] = []
        try:
            if hasattr(obj, "model_dump"):
                _collect_strings(obj.model_dump(), text_parts)
            elif dataclasses.is_dataclass(obj):
                _collect_strings(
                    {f.name: getattr(obj, f.name) for f in dataclasses.fields(obj)}, text_parts
                )
            else:
                text_parts.append(str(obj))
        except Exception:
            return obj  # If check fails, accept the output as-is

        combined = " ".join(text_parts)
        if is_greek(combined, min_ratio=0.30):
            return obj  # Sufficient Greek content

        # One bounded retry with stricter instruction
        if not self._llm:
            return obj
        strict_suffix = "\n\nCRITICAL: You MUST respond in Greek (Ελληνικά) only. Απάντησε ΑΠΟΚΛΕΙΣΤΙΚΑ στα Ελληνικά."
        strict_request = dataclasses.replace(request, system_prompt=system + strict_suffix)
        retry_obj, _ = await self._llm.generate_structured(strict_request, schema)
        return retry_obj or obj  # Fall back to original if retry also fails


def _collect_strings(data: object, result: list[str]) -> None:
    """Recursively collect all string values from nested dicts/lists."""
    if isinstance(data, str):
        result.append(data)
    elif isinstance(data, dict):
        for v in data.values():
            _collect_strings(v, result)
    elif isinstance(data, (list, tuple)):
        for v in data:
            _collect_strings(v, result)
