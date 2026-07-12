"""Tests for ReasonerAdapter — mocked HTTP transport, no real network."""

import json

import httpx
import pytest

from leggie.application.ports.reasoner import ReasonerRequest, ReasonerUnavailableError
from leggie.domain.models import CitationScheme
from leggie.infrastructure.reasoner.adapter import ReasonerAdapter


def _adapter(transport: httpx.MockTransport, **kwargs) -> ReasonerAdapter:
    return ReasonerAdapter(
        base_url="http://localhost:8003",
        api_key="test-key",
        transport=transport,
        base_delay=0.001,
        **kwargs,
    )


class TestReasonerAdapterHappyPath:
    @pytest.mark.asyncio
    async def test_reason_parses_full_response(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/agent/run/sync"
            assert request.headers["authorization"] == "Bearer test-key"
            body = json.loads(request.content)
            assert body["problem"] == "Analyze this bill"
            assert body["preset"] == "multi-perspective-premium"
            return httpx.Response(
                200,
                json={
                    "synthesis": "The bill introduces X.",
                    "critical_insights": ["insight A", "insight B"],
                    "open_questions": ["question A"],
                    "citations": [
                        {
                            "scheme": "fek",
                            "identifier": "FEK/2024/1",
                            "original_text": "ΦΕΚ Α 1/2024",
                            "resolved": True,
                        }
                    ],
                    "models_used": ["anthropic/claude-sonnet-4", "openai/gpt-5.6-luna"],
                    "total_tokens": {"prompt_tokens": 1000, "completion_tokens": 500},
                    "duration_seconds": 12.5,
                    "errors": [],
                },
            )

        transport = httpx.MockTransport(handler)
        adapter = _adapter(transport)
        result = await adapter.reason(
            ReasonerRequest(problem="Analyze this bill", preset="multi-perspective-premium")
        )

        assert result.synthesis == "The bill introduces X."
        assert result.critical_insights == ["insight A", "insight B"]
        assert result.open_questions == ["question A"]
        assert len(result.citations) == 1
        assert result.citations[0].scheme == CitationScheme.FEK
        assert result.citations[0].resolved is True
        assert result.models_used == ["anthropic/claude-sonnet-4", "openai/gpt-5.6-luna"]
        assert result.total_tokens == {"prompt_tokens": 1000, "completion_tokens": 500}
        assert result.duration_seconds == 12.5
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_reason_tolerates_missing_optional_keys(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"synthesis": "minimal response"})

        transport = httpx.MockTransport(handler)
        adapter = _adapter(transport)
        result = await adapter.reason(ReasonerRequest(problem="test"))

        assert result.synthesis == "minimal response"
        assert result.critical_insights == []
        assert result.open_questions == []
        assert result.citations == []
        assert result.models_used == []
        assert result.total_tokens == {}
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_reason_handles_empty_synthesis(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"synthesis": ""})

        transport = httpx.MockTransport(handler)
        adapter = _adapter(transport)
        result = await adapter.reason(ReasonerRequest(problem="test"))
        assert result.synthesis == ""

    @pytest.mark.asyncio
    async def test_unknown_citation_scheme_falls_back(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "synthesis": "text",
                    "citations": [
                        {"scheme": "not-a-real-scheme", "identifier": "X", "original_text": "X"}
                    ],
                },
            )

        transport = httpx.MockTransport(handler)
        adapter = _adapter(transport)
        result = await adapter.reason(ReasonerRequest(problem="test"))
        assert result.citations[0].scheme == CitationScheme.UNKNOWN

    @pytest.mark.asyncio
    async def test_client_run_id_included_when_set(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"synthesis": "ok"})

        transport = httpx.MockTransport(handler)
        adapter = _adapter(transport)
        await adapter.reason(ReasonerRequest(problem="test", client_run_id="run-123"))
        assert captured["body"]["client_run_id"] == "run-123"

    @pytest.mark.asyncio
    async def test_client_run_id_omitted_when_unset(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"synthesis": "ok"})

        transport = httpx.MockTransport(handler)
        adapter = _adapter(transport)
        await adapter.reason(ReasonerRequest(problem="test"))
        assert "client_run_id" not in captured["body"]


class TestReasonerAdapterErrors:
    @pytest.mark.asyncio
    async def test_401_raises_unavailable_no_retry(self):
        call_count = {"n": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            return httpx.Response(401, json={"error": "unauthorized"})

        transport = httpx.MockTransport(handler)
        adapter = _adapter(transport)
        with pytest.raises(ReasonerUnavailableError, match="authentication failed"):
            await adapter.reason(ReasonerRequest(problem="test"))
        assert call_count["n"] == 1  # no retry on auth errors

    @pytest.mark.asyncio
    async def test_503_retries_then_fails(self):
        call_count = {"n": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            return httpx.Response(503, text="service unavailable")

        transport = httpx.MockTransport(handler)
        adapter = _adapter(transport, max_retries=3)
        with pytest.raises(ReasonerUnavailableError, match="503"):
            await adapter.reason(ReasonerRequest(problem="test"))
        assert call_count["n"] == 3

    @pytest.mark.asyncio
    async def test_503_then_success_on_retry(self):
        call_count = {"n": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            if call_count["n"] < 2:
                return httpx.Response(503, text="temporarily down")
            return httpx.Response(200, json={"synthesis": "recovered"})

        transport = httpx.MockTransport(handler)
        adapter = _adapter(transport, max_retries=3)
        result = await adapter.reason(ReasonerRequest(problem="test"))
        assert result.synthesis == "recovered"
        assert call_count["n"] == 2

    @pytest.mark.asyncio
    async def test_malformed_json_raises_after_retries(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"not json{{{")

        transport = httpx.MockTransport(handler)
        adapter = _adapter(transport, max_retries=2)
        with pytest.raises(ReasonerUnavailableError, match="malformed JSON"):
            await adapter.reason(ReasonerRequest(problem="test"))

    @pytest.mark.asyncio
    async def test_other_4xx_raises_immediately(self):
        call_count = {"n": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            return httpx.Response(400, text="bad request")

        transport = httpx.MockTransport(handler)
        adapter = _adapter(transport)
        with pytest.raises(ReasonerUnavailableError, match="400"):
            await adapter.reason(ReasonerRequest(problem="test"))
        assert call_count["n"] == 1

    @pytest.mark.asyncio
    async def test_connection_error_raises_unavailable(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        transport = httpx.MockTransport(handler)
        adapter = _adapter(transport)
        with pytest.raises(ReasonerUnavailableError, match="unreachable"):
            await adapter.reason(ReasonerRequest(problem="test"))

    @pytest.mark.asyncio
    async def test_timeout_retries_then_raises(self):
        call_count = {"n": 0}

        def handler(_request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            raise httpx.ReadTimeout("timed out")

        transport = httpx.MockTransport(handler)
        adapter = _adapter(transport, max_retries=2)
        with pytest.raises(ReasonerUnavailableError, match="timed out"):
            await adapter.reason(ReasonerRequest(problem="test"))
        assert call_count["n"] == 2

    @pytest.mark.asyncio
    async def test_no_secret_in_error_message(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "unauthorized"})

        transport = httpx.MockTransport(handler)
        adapter = _adapter(transport)
        try:
            await adapter.reason(ReasonerRequest(problem="test"))
        except ReasonerUnavailableError as e:
            assert "test-key" not in str(e)
