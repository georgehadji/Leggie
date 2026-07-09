"""Tests for Verbalized Sampling service — Template Method pattern."""

import pytest
from leggie.application.services.verbalized_sampling import (
    VerbalizedSampling, VSSample,
)
from leggie.domain.models import Article, Finding, IRAC, Confidence, FindingType


SAMPLE_ARTICLE = Article(id="1", raw_text="Άρθρο 1: Δοκιμαστικό άρθρο.")


class TestVSSample:
    def test_create(self):
        finding = Finding(
            finding_type=FindingType.CONSTITUTIONAL,
            irac=IRAC(issue="x", rule="y", application="z", conclusion="w"),
            confidence=Confidence.from_score(0.5),
            lens="test", model="test",
        )
        sample = VSSample(finding=finding, probability=0.3)
        assert sample.probability == 0.3


class TestVerbalizedSampling:
    @pytest.mark.asyncio
    async def test_sample_tail_returns_low_probability(self):
        vs = _create_test_vs()
        samples = [
            VSSample(_make_finding("A"), 0.8),
            VSSample(_make_finding("B"), 0.6),
            VSSample(_make_finding("C"), 0.3),
            VSSample(_make_finding("D"), 0.2),
        ]
        result = vs.sample_tail(samples, k=2)
        assert len(result) == 2  # D and C (lowest prob)
        assert result[0].irac.issue == "D"  # Lowest first

    @pytest.mark.asyncio
    async def test_sample_tail_empty(self):
        vs = _create_test_vs()
        result = vs.sample_tail([], k=5)
        assert result == []

    @pytest.mark.asyncio
    async def test_sample_tail_respects_k(self):
        vs = _create_test_vs()
        samples = [VSSample(_make_finding(str(i)), 0.1) for i in range(10)]
        result = vs.sample_tail(samples, k=3)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_generate_fake(self):
        vs = _create_test_vs()
        result = await vs.generate("test_lens", SAMPLE_ARTICLE, k=3)
        assert len(result) >= 1


def _make_finding(issue_text: str) -> Finding:
    return Finding(
        finding_type=FindingType.CONSTITUTIONAL,
        irac=IRAC(issue=issue_text, rule="r", application="a", conclusion="c"),
        confidence=Confidence.from_score(0.5),
        lens="test", model="test",
    )


def _create_test_vs() -> VerbalizedSampling:
    """Create a test VS that returns fake candidates."""
    class FakeVS(VerbalizedSampling):
        def build_prompt(self, lens_name: str, article: Article, k: int) -> str:
            return f"Analyze {article.id} from {lens_name} perspective."

        async def call_model(self, prompt: str) -> str:
            return "Finding A: 0.3, Finding B: 0.2, Finding C: 0.1"

        def parse_distribution(self, raw_response: str) -> list[VSSample]:
            return [
                VSSample(_make_finding("A"), 0.3),
                VSSample(_make_finding("B"), 0.2),
                VSSample(_make_finding("C"), 0.1),
            ]
    return FakeVS()
