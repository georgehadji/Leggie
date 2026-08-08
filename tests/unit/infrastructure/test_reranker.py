"""Tests for OpenRouterReranker (was ~0% coverage — PROD-18b coverage lift)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from leggie.application.ports.reranker import RerankResult
from leggie.infrastructure.llm.base import LLMConfigurationError, LLMError, LLMRateLimitError
from leggie.infrastructure.reranker import OpenRouterReranker


class TestRerankerConstruction:
    def test_requires_api_key(self):
        with pytest.raises(LLMConfigurationError):
            OpenRouterReranker(api_key="")

    def test_accepts_api_key(self):
        r = OpenRouterReranker(api_key="sk-test")
        assert r._api_key == "sk-test"
        assert r._default_model == "cohere/rerank-4-pro"


class TestRerankerRerank:
    @pytest.mark.asyncio
    async def test_success_parses_results(self):
        r = OpenRouterReranker(api_key="sk-test")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [
                {"index": 1, "relevance_score": 0.9},
                {"index": 0, "relevance_score": 0.7},
            ]
        }
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(r, "_post", AsyncMock(return_value=mock_resp))
            results = await r.rerank("query", ["doc A", "doc B"])

        assert isinstance(results, list)
        assert all(isinstance(x, RerankResult) for x in results)
        assert results[0].index == 1
        assert results[0].document == "doc B"
        assert results[1].index == 0
        assert results[1].document == "doc A"

    @pytest.mark.asyncio
    async def test_rate_limited(self):
        r = OpenRouterReranker(api_key="sk-test")
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.text = "rate limited"
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(r, "_post", AsyncMock(return_value=mock_resp))
            with pytest.raises(LLMRateLimitError):
                await r.rerank("query", ["doc"])

    @pytest.mark.asyncio
    async def test_api_error(self):
        r = OpenRouterReranker(api_key="sk-test")
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "server error"
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(r, "_post", AsyncMock(return_value=mock_resp))
            with pytest.raises(LLMError):
                await r.rerank("query", ["doc"])

    @pytest.mark.asyncio
    async def test_custom_model_and_documents(self):
        r = OpenRouterReranker(api_key="sk-test")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"results": [{"index": 0, "relevance_score": 0.5}]}
        captured: dict[str, Any] = {}
        async def _post(body):
            captured.update(body)
            return mock_resp
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(r, "_post", _post)
            results = await r.rerank("query", ["doc"], model="custom/model", top_k=5)

        assert captured["model"] == "custom/model"
        assert captured["top_k"] == 5
        assert len(results) == 1
