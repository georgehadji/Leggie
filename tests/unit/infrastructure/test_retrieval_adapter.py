"""Tests for SimpleRetrievalAdapter — local file retriever.

SimpleRetrievalAdapter is available as an experimental retrieval
implementation. It is NOT wired into the default analysis pipeline;
current lenses and CoVe do not call RetrievalPort. These tests verify
the adapter works correctly for future retrieval-backed features.
"""

from __future__ import annotations

import pytest

from leggie.infrastructure.retrieval_adapter import SimpleRetrievalAdapter


class TestSimpleRetrievalAdapter:
    """Retrieval adapter works as a local file retriever (experimental)."""

    @pytest.mark.asyncio
    async def test_search_finds_substring(self, tmp_path):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "law_1.md").write_text(
            "This is the Greek Constitution article 1.", encoding="utf-8"
        )
        (corpus / "law_2.md").write_text("European GDPR regulation text.", encoding="utf-8")

        adapter = SimpleRetrievalAdapter(corpus_dir=str(corpus))
        results = await adapter.search("Greek")
        assert len(results) >= 1
        assert "Greek" in results[0].content

    @pytest.mark.asyncio
    async def test_search_no_match(self, tmp_path):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "doc.md").write_text("Some content here.", encoding="utf-8")

        adapter = SimpleRetrievalAdapter(corpus_dir=str(corpus))
        results = await adapter.search("nonexistent")
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_get_document(self, tmp_path):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        doc = corpus / "test_doc.md"
        doc.write_text("Document text.", encoding="utf-8")

        adapter = SimpleRetrievalAdapter(corpus_dir=str(corpus))
        content = await adapter.get_document("test_doc")
        assert content is not None
        assert "Document text." in content

    @pytest.mark.asyncio
    async def test_get_document_not_found(self, tmp_path):
        adapter = SimpleRetrievalAdapter(corpus_dir=str(tmp_path / "empty"))
        result = await adapter.get_document("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_corpus_stats(self, tmp_path):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "a.md").write_text("a", encoding="utf-8")
        (corpus / "b.txt").write_text("b", encoding="utf-8")

        adapter = SimpleRetrievalAdapter(corpus_dir=str(corpus))
        stats = await adapter.corpus_stats()
        assert stats["size"] == 2
