"""StructuredOutputDecorator — 4-attempt structured-output ladder as an LLMPort decorator.

Extracted from LLMAdapter.generate_structured so that each attempt
traverses the decorator stack (budget guard, cache, etc.) independently.
The old pattern of calling `self.generate()` bypassed every wrapper.

Ladder:
1. json_schema strict mode
2. On 400 / unsupported-schema → json_object fallback
3. On finish_reason=length → retry with doubled max_tokens
4. Repair round (last resort)
"""

from __future__ import annotations

from typing import Any

from leggie.application.ports.llm import LLMPort, LLMRequest, LLMResponse
from leggie.infrastructure.llm.base import LLMError
from leggie.observability import get_logger

logger = get_logger(__name__)

_MAX_TRUNCATION_RETRY_TOKENS = 16_384

_REPAIR_PROMPT_TEMPLATE = (
    "The following content was not valid JSON matching this schema. "
    "Return ONLY valid JSON that conforms to the schema.\n\n"
    "Schema: {schema_name}\n\n"
    "Malformed content:\n{content}"
)


class StructuredOutputDecorator(LLMPort):
    """Decorator: adds structured-output ladder to any LLMPort.

    Delegates each attempt to `self._inner.generate()`, so every
    attempt passes through the decorator stack below this one.
    """

    def __init__(self, inner: LLMPort) -> None:
        self._inner = inner

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Pass through — no ladder needed for plain generation."""
        return await self._inner.generate(request)

    async def generate_structured(
        self, request: LLMRequest, schema: type
    ) -> tuple[Any, LLMResponse]:
        """Generate a structured response using json_schema strict mode.

        Retry ladder — each attempt traverses the inner decorator stack:
        1. Try json_schema strict mode.
        2. On 400 / Bad Request, fall back to json_object mode.
        3. On parse failure with finish_reason=length, retry with doubled max_tokens.
        4. Repair round as last resort.
        """
        from dataclasses import replace

        from leggie.infrastructure.llm.schema_format import pydantic_to_json_schema
        from leggie.infrastructure.llm.structured_parser import (
            StructuredResponseParser,
        )

        parser = StructuredResponseParser()
        response: LLMResponse | None = None
        schema_format: dict[str, Any] | None = None

        # ── Attempt 1: json_schema strict mode ────────────────────
        try:
            schema_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": pydantic_to_json_schema(schema),
                },
            }
            req = replace(request, response_format=schema_format)
            response = await self._inner.generate(req)
            return parser.parse(response.content, schema), response
        except (LLMError, ValueError) as exc:
            if isinstance(exc, LLMError) and ("400" in str(exc) or "Bad Request" in str(exc)):
                logger.warning(
                    "json_schema rejected, falling back to json_object: %s", exc
                )
                schema_format = None

        # ── Attempt 2: json_object mode (fallback) ────────────────
        try:
            req = replace(request, response_format={"type": "json_object"})
            response = await self._inner.generate(req)
            return parser.parse(response.content, schema), response
        except (LLMError, ValueError):
            pass

        # ── Attempt 3: truncation retry if finish_reason=length ───
        if response and response.finish_reason == "length":
            logger.info(
                "Response truncated (finish_reason=length, %d tokens). "
                "Retrying with doubled max_tokens.",
                request.max_tokens,
            )
            doubled = min(request.max_tokens * 2, _MAX_TRUNCATION_RETRY_TOKENS)
            retry_req = replace(
                request,
                max_tokens=doubled,
                response_format=schema_format or {"type": "json_object"},
            )
            try:
                response = await self._inner.generate(retry_req)
                return parser.parse(response.content, schema), response
            except (LLMError, ValueError):
                pass

        # ── Attempt 4: repair round as last resort ────────────────
        content_to_repair = response.content if response else ""
        if content_to_repair and not any(c in content_to_repair for c in "{["):
            raise LLMError(
                f"Structured response for schema {schema.__name__} "
                f"contains no JSON skeleton; skipping repair round."
            )

        try:
            if content_to_repair:
                repair_prompt = _REPAIR_PROMPT_TEMPLATE.format(
                    schema_name=schema.__name__,
                    content=content_to_repair[:4000],
                )
                repair_req = LLMRequest(
                    prompt=repair_prompt,
                    system_prompt=(
                        "You are a JSON repair assistant. "
                        "Return ONLY valid JSON."
                    ),
                    max_tokens=min(
                        request.max_tokens * 2,
                        _MAX_TRUNCATION_RETRY_TOKENS,
                    ),
                    response_format={"type": "json_object"},
                )
                response = await self._inner.generate(repair_req)
                # The port hands `schema` down as a bare `type`, so the
                # parser's schema-bound return variable resolves to Any here.
                obj: Any = parser.parse(response.content, schema)
                return obj, response
        except (LLMError, ValueError):
            pass

        raise LLMError(
            f"Failed to parse structured response after all retries "
            f"for schema {schema.__name__}"
        )

    async def count_tokens(self, text: str, model: str | None = None) -> int:
        return await self._inner.count_tokens(text, model)
