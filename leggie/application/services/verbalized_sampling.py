"""Verbalized Sampling Service — Template Method for diverse finding generation.

Per O3: one prompt asks the model to verbalize a distribution of k candidate
findings, each with a probability, then sample from the tails.
Cheaper (fewer calls) and more diverse than firing k separate calls.

Phase 2: Template Method skeleton. Phase 3+: LLM-backed implementation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from leggie.domain.models import Article, Finding


@dataclass
class VSSample:
    """A single sampled finding with its probability."""
    finding: Finding
    probability: float = 0.0


class VerbalizedSampling(ABC):
    """Verbalized Sampling — Template Method pattern.

    Lifecycle:
        1. build_prompt(lens, article, k) → str
        2. call_model(prompt) → raw_response
        3. parse_distribution(raw_response) → list[VSSample]
        4. sample_tail(samples, k) → list[Finding]
    """

    @abstractmethod
    def build_prompt(self, lens_name: str, article: Article, k: int) -> str:
        """Build a prompt asking for k candidate findings with probabilities."""

    @abstractmethod
    async def call_model(self, prompt: str) -> str:
        """Call the LLM with the prompt and return raw response."""

    @abstractmethod
    def parse_distribution(self, raw_response: str) -> list[VSSample]:
        """Parse the model response into a list of VSSample with probabilities."""

    def sample_tail(self, samples: list[VSSample], k: int) -> list[Finding]:
        """Sample from the tail of the distribution (lower probability items).

        Default implementation: take items with probability < 0.5,
        preferring diversity over top probability.
        """
        if not samples:
            return []

        # Sort by probability ascending (tail first)
        sorted_samples = sorted(samples, key=lambda s: s.probability)
        # Take at most k from the tail
        tail = sorted_samples[:min(k, len(sorted_samples))]
        return [s.finding for s in tail]

    async def generate(self, lens_name: str, article: Article, k: int = 5) -> list[Finding]:
        """Template Method: generate diverse findings via VS."""
        prompt = self.build_prompt(lens_name, article, k)
        response = await self.call_model(prompt)
        samples = self.parse_distribution(response)
        return self.sample_tail(samples, k)
