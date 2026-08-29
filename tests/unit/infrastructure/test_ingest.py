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
        # Factory wraps all ingestors in BoundedIngestor (PROD-16a)
        from leggie.infrastructure.ingest.bounded import BoundedIngestor

        assert isinstance(ingestor, BoundedIngestor)

    def test_get_unsupported_format(self):
        with pytest.raises(UnsupportedFormatError):
            IngestorFactory.get_ingestor("file.xyz")

    def test_register_custom_ingestor(self):
        IngestorFactory.register_format(".custom", TextIngestor)
        ingestor = IngestorFactory.get_ingestor("file.custom")
        from leggie.infrastructure.ingest.bounded import BoundedIngestor

        # Wrapped ingestors still expose the underlying ingestor behavior
        assert isinstance(ingestor, BoundedIngestor)
        assert isinstance(ingestor._wrapped, TextIngestor)


class TestBoundedIngestor:
    """PROD-16a: safety caps refuse oversized/oversized docs with DEGRADED event."""

    @pytest.mark.asyncio
    async def test_oversize_file_refused_with_degraded_event(self, tmp_path):
        from leggie.infrastructure.ingest import TextIngestor
        from leggie.infrastructure.ingest.base import IngestError
        from leggie.infrastructure.ingest.bounded import BoundedIngestor

        # Create a large file (>1MB with a 0.001MB cap)
        big_file = tmp_path / "big.txt"
        big_file.write_text("x" * (2 * 1024 * 1024), encoding="utf-8")

        degraded: list[str] = []

        def on_degradation(ev):
            degraded.append(str(ev.event_type))

        bounded = BoundedIngestor(
            TextIngestor(),
            max_file_size_mb=1.0,  # 1MB cap; file is 2MB
            on_degradation=on_degradation,
        )

        with pytest.raises(IngestError):
            await bounded.ingest(big_file)
        assert "degraded" in degraded, "Expected a DEGRADED event on refusal"

    @pytest.mark.asyncio
    async def test_oversize_refused_not_truncated(self, tmp_path):
        from leggie.infrastructure.ingest import TextIngestor
        from leggie.infrastructure.ingest.base import IngestError
        from leggie.infrastructure.ingest.bounded import BoundedIngestor

        big_file = tmp_path / "big.txt"
        big_file.write_text("x" * (2 * 1024 * 1024), encoding="utf-8")
        bounded = BoundedIngestor(TextIngestor(), max_file_size_mb=1.0)

        with pytest.raises(IngestError) as exc:
            await bounded.ingest(big_file)
        assert "exceeds" in str(exc.value) or "cap" in str(exc.value)

    @pytest.mark.asyncio
    async def test_small_file_passes_through(self, tmp_path):
        from leggie.infrastructure.ingest import TextIngestor
        from leggie.infrastructure.ingest.bounded import BoundedIngestor

        small = tmp_path / "small.txt"
        small.write_text("hello world", encoding="utf-8")
        bounded = BoundedIngestor(TextIngestor(), max_file_size_mb=10.0)
        assert await bounded.ingest(small) == "hello world"
