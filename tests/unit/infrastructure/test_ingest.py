"""Tests for the ingest module — Factory pattern."""

import asyncio
import threading
import time
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


@pytest.fixture
def bounded_warnings(monkeypatch):
    """Capture calls to ingest.bounded's own ``log.warning`` directly.

    This suite has a documented (DH-2 in docs/DEFECT_HUNT_PLAN.md),
    still-open pytest-randomly order-dependent bug where some earlier test
    (or a dependency's own test helper) leaves GLOBAL stdlib-logging state
    mutated for the rest of the session. Two confirmed mechanisms —
    ``logging.disable(...)`` left set (silently no-ops every
    ``logger.warning()`` call process-wide, checked before any handler is
    even consulted) and ``leggie.observability.configure_logging``'s
    idempotent first-call-wins ``logging.basicConfig`` — were each tried
    and each defeated by attaching a handler directly to this logger and
    resetting ``logging.disable``; that STILL intermittently failed in a
    full-suite run, meaning a third, not-yet-identified mechanism in the
    same family also reaches this far. Rather than chase DH-2's open
    mystery further from inside this region, this fixture sidesteps the
    entire stdlib logging pipeline (level checks, handlers, `disabled`,
    `disable`, propagation — all of it) by replacing the logger's
    ``warning`` method itself, so the test verifies the real production
    call site regardless of what any other test in the suite has done to
    global logging state.
    """
    from leggie.infrastructure.ingest.bounded import log as bounded_log

    calls: list[str] = []

    def fake_warning(msg: str, *args: object) -> None:
        calls.append(msg % args if args else msg)

    monkeypatch.setattr(bounded_log, "warning", fake_warning)
    return calls


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

    @pytest.mark.asyncio
    async def test_refusal_logs_a_warning_without_any_callback(self, tmp_path, bounded_warnings):
        """Non-negotiable #6 (no silent failure): a refusal must always be
        observable even when nobody wires on_degradation.

        ``IngestorFactory.get_ingestor()`` — the only production call site —
        never passes ``on_degradation``, so it silently defaults to a no-op
        lambda: the DEGRADED Event was built and immediately discarded on
        every real refusal, with no event, no log, nothing. "events OR
        warnings" (leggie-change-control non-negotiable #6) means a warning
        log must not depend on a caller opting in.
        """
        from leggie.infrastructure.ingest import TextIngestor
        from leggie.infrastructure.ingest.bounded import BoundedIngestor

        big_file = tmp_path / "big.txt"
        big_file.write_text("x" * (2 * 1024 * 1024), encoding="utf-8")
        bounded = BoundedIngestor(TextIngestor(), max_file_size_mb=1.0)  # no on_degradation

        with pytest.raises(IngestError):
            await bounded.ingest(big_file)
        assert any("ingest refused" in c for c in bounded_warnings)

    @pytest.mark.asyncio
    async def test_factory_production_path_also_logs_on_refusal(
        self, tmp_path, monkeypatch, bounded_warnings
    ):
        """The real production path (IngestAdapter -> IngestorFactory) never
        wires on_degradation either — confirm the warning fires end to end,
        not just when BoundedIngestor is constructed directly in a test."""
        monkeypatch.setitem(IngestorFactory.bounds, "max_file_size_mb", 1.0)
        big_file = tmp_path / "big.txt"
        big_file.write_text("x" * (2 * 1024 * 1024), encoding="utf-8")

        with pytest.raises(IngestError):
            await IngestorFactory.ingest(big_file)
        assert any("ingest refused" in c for c in bounded_warnings)


class TestPDFIngestorPageCap:
    """PROD-16a: the ``max_pages`` cap was accepted by ``BoundedIngestor``
    but never read anywhere (confirmed: zero references outside its own
    ``__init__``; ``implementation_audit_report_phase5.md`` line 66 records
    this as a known, "Acceptable for MVP" gap at the time). A page-count
    bomb (many pages, little text per page) sailed past the char-count
    ``max_elements`` cap, which only ever inspects the already-extracted
    result — the expensive per-page ``extract_text()`` loop already ran.
    The fix enforces the cap inside ``PDFIngestor`` itself, before that loop,
    mirroring how the DOCX ingestor already guards its own format-specific
    risk (the zip decompression-bomb check) rather than relying on
    ``BoundedIngestor`` to know PDF internals.
    """

    @staticmethod
    def _make_minimal_pdf(n_pages: int) -> bytes:
        """Hand-built minimal multi-page PDF — deliberately dependency-free.

        Only ``pdfplumber`` (a real, declared project dependency) is needed
        to read it back. ``pypdf``/``reportlab`` are present in this dev
        venv but are NOT declared dependencies of this project (verified:
        ``pdfplumber`` itself depends only on pdfminer.six/Pillow/pypdfium2),
        so a test must not rely on them — that would be incidental to one
        machine, not portable to CI's clean installs.
        """
        objs = [b"<< /Type /Catalog /Pages 2 0 R >>"]
        kids = " ".join(f"{i + 3} 0 R" for i in range(n_pages))
        objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
        objs += [b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>"] * n_pages

        out = bytearray(b"%PDF-1.4\n")
        offsets: list[int] = []
        for i, body in enumerate(objs, start=1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"

        xref_offset = len(out)
        count = len(objs) + 1
        out += f"xref\n0 {count}\n".encode() + b"0000000000 65535 f \n"
        for off in offsets:
            out += f"{off:010d} 00000 n \n".encode()
        out += (
            f"trailer\n<< /Size {count} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF"
        ).encode()
        return bytes(out)

    @pytest.mark.asyncio
    async def test_pdf_exceeding_page_cap_is_refused(self, tmp_path, monkeypatch):
        """Proof: a page-count bomb is refused before extraction completes."""
        from leggie.infrastructure.ingest import PDFIngestor

        monkeypatch.setitem(IngestorFactory.bounds, "max_pages", 3)
        pdf_path = tmp_path / "many_pages.pdf"
        pdf_path.write_bytes(self._make_minimal_pdf(10))

        with pytest.raises(IngestError, match="page cap"):
            await PDFIngestor().ingest(pdf_path)

    @pytest.mark.asyncio
    async def test_pdf_one_page_over_cap_is_refused(self, tmp_path, monkeypatch):
        """Boundary: cap + 1 pages must be refused (the > comparison)."""
        from leggie.infrastructure.ingest import PDFIngestor

        monkeypatch.setitem(IngestorFactory.bounds, "max_pages", 3)
        pdf_path = tmp_path / "one_over.pdf"
        pdf_path.write_bytes(self._make_minimal_pdf(4))

        with pytest.raises(IngestError, match="page cap"):
            await PDFIngestor().ingest(pdf_path)

    @pytest.mark.asyncio
    async def test_pdf_at_exact_page_cap_is_accepted(self, tmp_path, monkeypatch):
        """Boundary: exactly max_pages pages must NOT be refused."""
        from leggie.infrastructure.ingest import PDFIngestor

        monkeypatch.setitem(IngestorFactory.bounds, "max_pages", 3)
        pdf_path = tmp_path / "exact_cap.pdf"
        pdf_path.write_bytes(self._make_minimal_pdf(3))

        result = await PDFIngestor().ingest(pdf_path)
        assert result == ""  # blank pages: no text, but must not raise

    @pytest.mark.asyncio
    async def test_small_pdf_under_default_cap_is_unaffected(self, tmp_path):
        """No-regression: normal small PDFs are untouched by the new guard
        (real default max_pages=10_000, not monkeypatched)."""
        from leggie.infrastructure.ingest import PDFIngestor

        pdf_path = tmp_path / "normal.pdf"
        pdf_path.write_bytes(self._make_minimal_pdf(2))

        result = await PDFIngestor().ingest(pdf_path)
        assert result == ""


class TestTimeoutDoesNotActuallyStopWork:
    """DH-10. ``BoundedIngestor``'s ``timeout_s`` stops the CALLER from
    waiting; it does not stop the underlying worker, and it never can — a
    Python thread has no cooperative or forced-cancellation mechanism, so the
    OS thread runs the synchronous ingest function to completion regardless.
    The first test below locks that in: it is a property of threads, not a
    defect, and no fix short of a different execution model changes it.

    What *was* a defect is what that abandoned worker cost. Under
    ``asyncio.to_thread`` it ran on the loop's default ``ThreadPoolExecutor``,
    whose non-daemon threads ``asyncio.Runner`` joins at
    ``loop.shutdown_default_executor()`` — so the "timeout" freed the caller
    and then the process sat there waiting out the full duration of the work
    it had just abandoned (measured: 0.2 s timeout over 3.0 s of work still
    exited at 3.03 s). PROD-16a's wall-clock cap was a claim the code did not
    honour. ``run_off_loop`` (ingest/base.py) puts the work on a daemon
    thread, which shutdown does not join.

    Residual, accepted: the abandoned thread still burns CPU until it
    finishes on its own. Terminating it needs ``ProcessPoolExecutor``, whose
    per-ingest spawn cost buys nothing for a single-run CLI — see
    docs/ESCALATED_DEFECTS_PLAN.md §4.
    """

    @pytest.mark.asyncio
    async def test_wrapped_ingestor_keeps_running_past_the_timeout(self, tmp_path):
        from leggie.infrastructure.ingest.base import Ingestor
        from leggie.infrastructure.ingest.bounded import BoundedIngestor

        finished = threading.Event()

        class SlowIngestor(Ingestor):
            async def ingest(self, source: Path | str) -> str:
                def _blocking() -> str:
                    time.sleep(0.3)
                    finished.set()
                    return "done"

                return await asyncio.to_thread(_blocking)

        bounded = BoundedIngestor(SlowIngestor(), timeout_s=0.05)
        src = tmp_path / "irrelevant.txt"
        src.write_text("x", encoding="utf-8")

        with pytest.raises(IngestError, match="timed out"):
            await bounded.ingest(src)

        # BoundedIngestor already raised — the "cancelled" work is still
        # running in the background, proving the timeout only stopped the
        # waiting, not the work.
        assert not finished.is_set(), "expected the thread to still be mid-sleep"
        await asyncio.sleep(0.5)
        assert finished.is_set(), "the 'cancelled' work ran to completion anyway"

    def test_timeout_no_longer_holds_the_process_open(self, tmp_path):
        """Proof-of-defect for the half that *was* fixable.

        Deliberately a sync test: it drives ``asyncio.run`` itself, because
        the defect lives in ``asyncio.Runner``'s shutdown, which joins the
        default executor's non-daemon workers. Before the fix this took as
        long as the abandoned work (0.6 s); now the timeout governs.
        """
        from leggie.infrastructure.ingest.base import Ingestor, run_off_loop
        from leggie.infrastructure.ingest.bounded import BoundedIngestor

        class SlowIngestor(Ingestor):
            async def ingest(self, source: Path | str) -> str:
                return await run_off_loop(lambda: (time.sleep(0.6), "done")[1])

        src = tmp_path / "irrelevant.txt"
        src.write_text("x", encoding="utf-8")

        async def _drive() -> None:
            with pytest.raises(IngestError, match="timed out"):
                await BoundedIngestor(SlowIngestor(), timeout_s=0.05).ingest(src)

        start = time.perf_counter()
        asyncio.run(_drive())
        elapsed = time.perf_counter() - start

        assert elapsed < 0.4, f"process held open for {elapsed:.2f}s by abandoned ingest work"

    @pytest.mark.asyncio
    async def test_worker_runs_on_a_daemon_thread_off_the_shared_executor(self, tmp_path):
        """Boundary: the mechanism that makes the above true. A daemon thread
        is not joined at interpreter/loop shutdown, and being off the default
        executor means a leaked ingest cannot starve every other
        ``asyncio.to_thread`` caller in the process either."""
        from leggie.infrastructure.ingest.base import run_off_loop

        seen: dict[str, object] = {}

        def _work() -> str:
            current = threading.current_thread()
            seen["daemon"] = current.daemon
            seen["name"] = current.name
            return "ok"

        assert await run_off_loop(_work) == "ok"
        assert seen["daemon"] is True
        assert seen["name"] == "leggie-ingest"

    @pytest.mark.asyncio
    async def test_exception_from_the_worker_still_propagates(self):
        """No-regression: swapping the offload mechanism must not swallow or
        reshape failures raised inside the blocking extractor — the four real
        ingestors raise IngestError/InputNotFoundError from in there."""
        from leggie.infrastructure.ingest.base import run_off_loop

        def _boom() -> str:
            raise IngestError("pdfplumber exploded")

        with pytest.raises(IngestError, match="pdfplumber exploded"):
            await run_off_loop(_boom)
