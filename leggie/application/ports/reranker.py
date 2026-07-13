"""Reranker Port — abstract interface for document reranking.

Rerank models score the relevance of documents given a query,
unlike chat models which generate text.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class RerankResult:
    """A single reranked document with its relevance score."""

    index: int
    relevance_score: float
    document: object | None = None


class RerankerPort(ABC):
    """Port for document reranking via dedicated rerank models."""

    @abstractmethod
    async def rerank(
        self,
        query: str,
        documents: list[str],
        model: str = "",
        top_k: int | None = None,
    ) -> list[RerankResult]:
        """Rerank documents by relevance to the query.

        Args:
            query: The search/ranking query.
            documents: List of document texts to rerank.
            model: Rerank model ID (e.g. "cohere/rerank-4-pro").
            top_k: Number of top results to return (None = all).

        Returns:
            List of RerankResult sorted by relevance_score descending.
        """
        ...
