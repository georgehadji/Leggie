"""ReasonerAdapter — HTTP client implementing ReasonerPort over Reasoner's Agent API.

Calls POST {base_url}/api/agent/run/sync with Bearer auth. Tolerant response
parsing (missing optional keys default safely); bounded retry with exponential
backoff on transient (5xx/timeout) failures.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from leggie.application.ports.reasoner import (
    ReasonerPort,
    ReasonerRequest,
    ReasonerResult,
    ReasonerUnavailableError,
)
from leggie.domain.models import Citation, CitationScheme
from leggie.observability import bind_trace_id, get_logger

_RETRYABLE_STATUS_CODES = {500, 502, 503, 504}


class ReasonerAdapter(ReasonerPort):
    """Implements ReasonerPort via HTTP calls to the Reasoner Agent API."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        request_timeout: float = 300.0,
        max_retries: int = 3,
        base_delay: float = 1.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._request_timeout = request_timeout
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._transport = transport

    async def reason(self, request: ReasonerRequest) -> ReasonerResult:
        logger = bind_trace_id(get_logger(__name__))
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }
        body: dict[str, Any] = {
            "problem": request.problem,
            "preset": request.preset,
            "top_k": request.top_k,
            "sequential": request.sequential,
            "no_cache": request.no_cache,
            "web_search": request.web_search,
        }
        if request.client_run_id:
            body["client_run_id"] = request.client_run_id

        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            start = time.monotonic()
            try:
                async with httpx.AsyncClient(
                    timeout=self._request_timeout, transport=self._transport
                ) as client:
                    resp = await client.post(
                        f"{self._base_url}/api/agent/run/sync",
                        headers=headers,
                        json=body,
                    )
            except httpx.TimeoutException as exc:
                last_error = exc
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(self._base_delay * (2**attempt))
                    continue
                raise ReasonerUnavailableError(
                    f"Reasoner request timed out after {self._max_retries} attempts", exc
                ) from exc
            except httpx.RequestError as exc:
                raise ReasonerUnavailableError(
                    f"Reasoner unreachable at {self._base_url}", exc
                ) from exc

            elapsed = time.monotonic() - start

            if resp.status_code == 401 or resp.status_code == 403:
                raise ReasonerUnavailableError(
                    f"Reasoner authentication failed ({resp.status_code})"
                )

            if resp.status_code in _RETRYABLE_STATUS_CODES:
                last_error = ReasonerUnavailableError(
                    f"Reasoner returned {resp.status_code}: {resp.text[:200]}"
                )
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(self._base_delay * (2**attempt))
                    continue
                raise last_error

            if resp.status_code != 200:
                raise ReasonerUnavailableError(
                    f"Reasoner request failed ({resp.status_code}): {resp.text[:200]}"
                )

            try:
                data = resp.json()
            except ValueError as exc:
                last_error = ReasonerUnavailableError("Reasoner returned malformed JSON", exc)
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(self._base_delay * (2**attempt))
                    continue
                raise last_error from exc

            result = self._parse_result(data, elapsed)
            logger.info(
                "reasoner.call_completed",
                preset=request.preset,
                models_used=result.models_used,
                total_tokens=result.total_tokens,
                duration_seconds=result.duration_seconds,
                attempt=attempt + 1,
            )
            return result

        raise ReasonerUnavailableError(
            f"Reasoner call failed after {self._max_retries} attempts", last_error
        )

    @staticmethod
    def _parse_result(data: dict[str, Any], elapsed: float) -> ReasonerResult:
        """Tolerant parsing — missing optional keys default safely."""
        citations = []
        for raw in data.get("citations", []) or []:
            scheme_raw = str(raw.get("scheme", "unknown")).lower()
            try:
                scheme = CitationScheme(scheme_raw)
            except ValueError:
                scheme = CitationScheme.UNKNOWN
            identifier = raw.get("identifier") or raw.get("original_text") or ""
            if not identifier:
                continue
            citations.append(
                Citation(
                    scheme=scheme,
                    identifier=identifier,
                    original_text=raw.get("original_text", identifier),
                    # Deliberative pipeline skips CoVe/Skeptic entirely (architecture
                    # contract §3) — nothing here was checked against a configured
                    # index, whatever the Reasoner backend's own "resolved" claims.
                    resolved=False,
                    checked=False,
                    resolution_evidence=raw.get("resolution_evidence"),
                )
            )

        return ReasonerResult(
            synthesis=data.get("synthesis", ""),
            critical_insights=list(data.get("critical_insights", []) or []),
            open_questions=list(data.get("open_questions", []) or []),
            citations=citations,
            models_used=list(data.get("models_used", []) or []),
            total_tokens=dict(data.get("total_tokens", {}) or {}),
            duration_seconds=float(data.get("duration_seconds", elapsed)),
            errors=list(data.get("errors", []) or []),
        )
