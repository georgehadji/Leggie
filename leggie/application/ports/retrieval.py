"""Retrieval Port — abstract interface for document retrieval.

Covers dense, sparse, and hybrid retrieval strategies over multiple corpora.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RetrievalResult:
    """A single retrieved document/chunk."""

    content: str
    source: str  # corpus/document identifier
    score: float
    metadata: dict = field(default_factory=dict)


class RetrievalPort(ABC):
    """Port for retrieving documents from corpora."""

    @abstractmethod
    async def search(
        self,
        query: str,
        corpus: str = "default",
        top_k: int = 10,
        mode: str = "hybrid",  # dense, sparse, hybrid
    ) -> list[RetrievalResult]:
        """Search a corpus for relevant documents."""
        ...

    @abstractmethod
    async def get_document(self, document_id: str, corpus: str = "default") -> str | None:
        """Retrieve a document by its ID from a corpus."""
        ...

    @abstractmethod
    async def corpus_stats(self, corpus: str = "default") -> dict:
        """Get statistics about a corpus (size, last indexed, etc.)."""
        ...
