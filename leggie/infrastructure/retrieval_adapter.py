"""Retrieval Adapter — stub for Phase 3 hybrid retrieval."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from leggie.application.ports.retrieval import RetrievalPort, RetrievalResult


class SimpleRetrievalAdapter(RetrievalPort):
    """Simple file-based retrieval for Phase 3 prep.

    Reads markdown/text files from a configured corpus directory.
    Full hybrid retrieval (dense + BM25) deferred to Phase 3.
    """

    def __init__(self, corpus_dir: str = "corpus") -> None:
        self._corpus_dir = Path(corpus_dir)
        self._corpus_dir.mkdir(parents=True, exist_ok=True)

    async def search(
        self, query: str, corpus: str = "default", top_k: int = 10, mode: str = "hybrid"
    ) -> list[RetrievalResult]:
        results: list[RetrievalResult] = []
        if not self._corpus_dir.exists():
            return results
        for f in sorted(self._corpus_dir.glob("*.md")) + sorted(self._corpus_dir.glob("*.txt")):
            text = f.read_text(encoding="utf-8")
            if query.lower() in text.lower():
                results.append(RetrievalResult(
                    content=text[:500],
                    source=f.name,
                    score=0.5,
                    metadata={"path": str(f)},
                ))
            if len(results) >= top_k:
                break
        return results

    async def get_document(self, document_id: str, corpus: str = "default") -> str | None:
        path = self._corpus_dir / f"{document_id}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        path = self._corpus_dir / f"{document_id}.txt"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    async def corpus_stats(self, corpus: str = "default") -> dict[str, Any]:
        files = list(self._corpus_dir.glob("*.*"))
        return {"size": len(files), "documents": [f.name for f in files]}
