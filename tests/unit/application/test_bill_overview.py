"""Tests for BillOverviewGenerator — Stage 0 preview (LLM + fallback paths)."""

import pytest

from leggie.application.ports.llm import LLMResponse
from leggie.application.services.bill_overview import BillOverviewGenerator
from leggie.domain.models import Article, Document, ModelTier
from leggie.domain.models.structured_output import (
    ArticleOverviewCandidate,
    BillIntroSummary,
)


def _doc() -> Document:
    return Document(
        title="Δοκιμαστικό",
        source_format="txt",
        preamble="Προοίμιο",
        articles=[
            Article(id="1", title="Άρθρο 1", raw_text="Κείμενο 1"),
            Article(id="2", title="Άρθρο 2", raw_text="Κείμενο 2"),
        ],
    )


class _FakeLLM:
    """Returns well-formed structured responses for both preview schemas."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request):  # pragma: no cover - unused
        raise NotImplementedError

    async def generate_structured(self, request, schema):
        self.calls += 1
        if schema is BillIntroSummary:
            obj = BillIntroSummary(intro="ΕΙΣΑΓΩΓΗ", summary="ΠΕΡΙΛΗΨΗ")
        else:
            obj = ArticleOverviewCandidate(
                purpose="ΣΚΟΠΟΣ",
                key_provisions=["διάταξη"],
                practical_consequences="ΣΥΝΕΠΕΙΕΣ",
            )
        return obj, LLMResponse(content="{}", model="fake", tier_used=ModelTier.BUDGET, usage={})

    async def count_tokens(self, text, model=None):  # pragma: no cover
        return len(text) // 4


class _RaisingLLM(_FakeLLM):
    async def generate_structured(self, request, schema):
        raise RuntimeError("boom")


class _NoneLLM(_FakeLLM):
    async def generate_structured(self, request, schema):
        return None, LLMResponse(content="", model="fake", tier_used=ModelTier.BUDGET, usage={})


class TestBillOverviewGenerator:
    @pytest.mark.asyncio
    async def test_offline_fallback_without_llm(self):
        gen = BillOverviewGenerator(llm=None)
        overview = await gen.generate(_doc())
        assert overview.article_ids() == ["1", "2"]
        assert "2" in overview.intro  # fallback intro mentions article count

    @pytest.mark.asyncio
    async def test_llm_path_populates_overview(self):
        llm = _FakeLLM()
        gen = BillOverviewGenerator(llm=llm)
        overview = await gen.generate(_doc())
        assert overview.intro == "ΕΙΣΑΓΩΓΗ"
        assert overview.summary == "ΠΕΡΙΛΗΨΗ"
        assert overview.article_ids() == ["1", "2"]
        assert overview.articles[0].purpose == "ΣΚΟΠΟΣ"
        assert overview.articles[0].key_provisions == ["διάταξη"]
        assert overview.articles[0].practical_consequences == "ΣΥΝΕΠΕΙΕΣ"
        # 1 bill summary call + 2 article calls
        assert llm.calls == 3

    @pytest.mark.asyncio
    async def test_llm_exception_falls_back(self):
        gen = BillOverviewGenerator(llm=_RaisingLLM())
        overview = await gen.generate(_doc())
        # Falls back to deterministic overview rather than crashing.
        assert overview.article_ids() == ["1", "2"]
        assert overview.articles[0].purpose == ""

    @pytest.mark.asyncio
    async def test_llm_none_result_falls_back(self):
        gen = BillOverviewGenerator(llm=_NoneLLM())
        overview = await gen.generate(_doc())
        assert overview.article_ids() == ["1", "2"]
        assert overview.articles[0].purpose == ""
