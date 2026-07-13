"""Lens-aware Verbalized Sampling — uses existing lens prompts and LLM port.

W4: One call per (lens, article) produces k candidates with probabilities.
Tail-samples the lowest-probability (most surprising) findings.
"""

from __future__ import annotations

import logging

from leggie.application.agents.lens import Lens
from leggie.application.ports.llm import LLMPort, LLMRequest
from leggie.application.services.verbalized_sampling import VerbalizedSampling, VSSample
from leggie.domain.models import Article, Finding
from leggie.domain.models.structured_output import LensFindings

log = logging.getLogger(__name__)


class LensVerbalizedSampling:
    """Wraps a Lens to produce k diverse findings via Verbalized Sampling.

    One LLM call per (lens, article), requesting k candidates with probabilities.
    Parses the structured response and tail-samples the lowest-probability items.
    """

    def __init__(
        self,
        llm: LLMPort,
        lens_name: str,
        model: str,
        system_prompt: str,
        user_template: str,
        k: int = 5,
        seed: int = 0,
    ) -> None:
        self._llm = llm
        self._lens_name = lens_name
        self._model = model
        self._system_prompt = system_prompt
        self._user_template = user_template
        self._k = k
        self._seed = seed

    async def generate(self, lens: Lens, article: Article) -> list[Finding]:
        """Generate k candidates, parse, tail-sample, return findings."""
        prompt = self._build_prompt(article)
        raw = await self._call_llm(prompt)
        if not raw:
            return []
        samples = self._parse_distribution(raw, lens, article)
        return self._sample_tail(samples)

    def _build_prompt(self, article: Article) -> str:
        """Build a prompt requesting k candidates with probabilities."""
        base = self._user_template.format(
            article_id=article.id, article_text=article.raw_text
        )
        vs_instruction = (
            f"\n\nIMPORTANT: Generate exactly {self._k} candidate findings. "
            f"For each candidate, assign a self-reported probability (0.0-1.0) "
            f"reflecting how confident you are that this is a genuine issue. "
            f"Include at least one non-obvious or surprising finding with low probability (<0.4)."
        )
        return base + vs_instruction

    async def _call_llm(self, prompt: str) -> LensFindings | None:
        """Call LLM with structured output, expecting LensFindings with k findings."""
        request = LLMRequest(
            prompt=prompt,
            system_prompt=self._system_prompt,
            model=self._model,
            response_format={"type": "json_object"},
            seed=self._seed,
        )
        try:
            obj, _ = await self._llm.generate_structured(request, LensFindings)
            result: LensFindings | None = obj
            return result
        except Exception as e:
            log.warning("vs_llm_failed: lens=%s error=%s", self._lens_name, e)
            return None

    def _parse_distribution(self, result: LensFindings, lens: Lens, article: Article) -> list[VSSample]:
        """Parse LensFindings into VSSamples with probabilities."""
        if not result.findings:
            return []

        samples: list[VSSample] = []
        for candidate in result.findings:
            finding = lens._candidate_to_finding(candidate, article)
            samples.append(VSSample(finding=finding, probability=candidate.probability))
        return samples

    def _sample_tail(self, samples: list[VSSample]) -> list[Finding]:
        """Sort by probability ascending, take lowest k (the tail)."""
        if not samples:
            return []
        sorted_samples = sorted(samples, key=lambda s: s.probability)
        # Take at most k from the tail (lowest probability = most surprising)
        tail = sorted_samples[:min(self._k, len(sorted_samples))]
        return [s.finding for s in tail]
