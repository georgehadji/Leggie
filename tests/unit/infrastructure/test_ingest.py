"""Tests for the ingest module — Factory pattern."""
from pathlib import Path

import pytest

from leggie.infrastructure.ingest import (
    IngestError,
    IngestorFactory,
    TextIngestor,
    UnsupportedFormatError,
)


class TestTextIngestor:
    @pytest.mark.asyncio
    async def test_ingest_txt_file(self, tmp_path):
        filepath = tmp_path / "test.txt"
        filepath.write_text("Hello, Leggie!", encoding="utf-8")
        ingestor = TextIngestor()
        text = await ingestor.ingest(filepath)
        assert text == "Hello, Leggie!"

    @pytest.mark.asyncio
    async def test_ingest_nonexistent_file(self):
        ingestor = TextIngestor()
        with pytest.raises(IngestError):
            await ingestor.ingest(Path("/nonexistent/path.txt"))


class TestIngestorFactory:
    def test_get_text_ingestor(self):
        ingestor = IngestorFactory.get_ingestor("file.txt")
        assert isinstance(ingestor, TextIngestor)

    def test_get_unsupported_format(self):
        with pytest.raises(UnsupportedFormatError):
            IngestorFactory.get_ingestor("file.xyz")

    def test_register_custom_ingestor(self):
        IngestorFactory.register_format(".custom", TextIngestor)
        ingestor = IngestorFactory.get_ingestor("file.custom")
        assert isinstance(ingestor, TextIngestor)
