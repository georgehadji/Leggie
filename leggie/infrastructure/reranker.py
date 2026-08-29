"""OpenRouter rerank adapter — calls the /api/v1/rerank endpoint.

OpenRouter exposes dedicated rerank models (Cohere, NVIDIA) via a separate
endpoint from chat completions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from leggie.application.ports.llm import LLMConfigurationError, LLMError, LLMRateLimitError
from leggie.application.ports.reranker import RerankerPort, RerankResult

if TYPE_CHECKING:
    # Imported for annotations only — the runtime import stays lazy inside
    # _post() so a missing httpx degrades to a clear LLMError rather than an
    # ImportError at module load.
    import httpx


class OpenRouterReranker(RerankerPort):
    """Reranker adapter for OpenRouter's /api/v1/rerank endpoint."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        default_model: str = "cohere/rerank-4-pro",
    ) -> None:
        if not api_key:
            raise LLMConfigurationError("OpenRouter API key not configured")
        self._api_key = api_key
        self._base_url = base_url
        self._default_model = default_model

    async def rerank(
        self,
        query: str,
        documents: list[str],
        model: str = "",
        top_k: int | None = None,
    ) -> list[RerankResult]:
        """Rerank documents via OpenRouter's rerank endpoint."""
        model_id = model or self._default_model
        body: dict[str, Any] = {
            "model": model_id,
            "query": query,
            "documents": documents,
        }
        if top_k is not None:
            body["top_k"] = top_k

        resp = await self._post(body)
        return self._parse(resp, documents)

    async def _post(self, body: dict[str, Any]) -> httpx.Response:
        """Issue the HTTP POST (separated for testability)."""
        try:
            import httpx
        except ImportError as exc:
            raise LLMError("httpx not installed") from exc

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "HTTP-Referer": "https://github.com/georgehadji/Leggie",
            "X-Title": "Leggie",
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            return await client.post(
                f"{self._base_url}/rerank",
                headers=headers,
                json=body,
            )

    def _parse(self, resp: httpx.Response, documents: list[str]) -> list[RerankResult]:
        """Parse a response into RerankResults (separated for testability)."""
        if resp.status_code == 429:
            raise LLMRateLimitError(f"OpenRouter rerank rate limited: {resp.text}")
        if resp.status_code != 200:
            raise LLMError(f"OpenRouter rerank error {resp.status_code}: {resp.text}")

        data = resp.json()
        results = data.get("results", [])
        return [
            RerankResult(
                index=r.get("index", i),
                relevance_score=r.get("relevance_score", 0.0),
                document=documents[r.get("index", i)]
                if r.get("index", i) < len(documents)
                else None,
            )
            for i, r in enumerate(results)
        ]
