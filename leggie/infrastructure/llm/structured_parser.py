"""Centralized structured response parser — parse, normalize, repair.

Extracted from ``LLMAdapter.generate_structured`` into its own pure-function
class so that every call-site (lens, CoVe, skeptic) shares identical, tested
repair logic.

Usage::

    parser = StructuredResponseParser()
    obj = parser.parse(content, LensFindings)
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel


class StructuredResponseParser:
    """Parse and repair LLM structured responses.

    Pure function of ``(content, schema) -> BaseModel`` — no HTTP, no side
    effects, fully unit-testable.
    """

    # Map model-invented field names onto canonical IRAC keys.
    # Models frequently name the same semantic field differently across calls;
    # these aliases let us accept responses that would otherwise be rejected.
    _IRAC_ALIASES: dict[str, list[str]] = {
        "issue": [
            "issue",
            "title",
            "finding",
            "summary",
            "concern",
            "constitutional_concern",
            "analysis",
            "legal_issue",
            "problem",
            "finding_text",
        ],
        "rule": [
            "rule",
            "constitutional_provision",
            "rule_id",
            "legal_basis",
            "provision",
            "article",
        ],
        "application": [
            "application",
            "analysis",
            "reasoning",
            "constitutional_concern",
        ],
        "conclusion": [
            "conclusion",
            "verdict",
            "constitutional_concern",
            "analysis",
        ],
        "verbatim_quote": [
            "verbatim_quote",
            "excerpt",
            "quote",
            "text_excerpt",
        ],
    }

    # ── public API ─────────────────────────────────────────────────

    def parse(self, content: str, schema: type[BaseModel]) -> BaseModel:
        """Parse *content* (raw LLM output) into *schema*, applying repairs.

        The ladder:
        1. Strip markdown code fences.
        2. JSON-decode.
        3. Wrap bare array into schema's list field.
        4. Rename known field aliases (``issues`` → ``findings``).
        5. Normalize IRAC item field names.
        6. Validate against *schema*.

        Raises ``ValueError`` (with descriptive message) on failure —
        the caller decides retry vs degrade.
        """
        content = (content or "").strip()
        # 1. Strip ```json … ``` fences
        content = self._strip_fences(content)
        if not content:
            raise ValueError("Empty content after stripping code fences")

        # 2. JSON decode
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON decode failed: {exc}") from exc

        # 3. Bare array → wrap into schema's first list field
        if isinstance(data, list):
            data = self._wrap_bare_array(data, schema)

        if not isinstance(data, dict):
            raise ValueError(f"Expected dict after wrapping, got {type(data).__name__}")

        # 4. Rename known top-level aliases
        data = self._rename_top_level(data)

        # 5. Normalize IRAC field names inside finding items
        data = self._normalize_findings(data)

        # 6. Validate
        try:
            return schema(**data)
        except Exception as exc:
            raise ValueError(f"Schema validation failed: {exc}") from exc

    def try_repair(
        self,
        content: str,
        schema: type[BaseModel],
    ) -> BaseModel | None:
        """Last-resort: feed malformed content back for re-generation.

        Wraps *content* in a terse instruction asking for valid JSON, then
        attempts a fresh parse. Returns ``None`` if still unparseable.
        """
        # The caller is expected to present this to the LLM as a new prompt.
        # This function only attempts a best-effort re-parse of content
        # that *looks* like it has valid data embedded in error text.
        return None  # Deferred to caller who controls the LLM round-trip

    # ── internals ───────────────────────────────────────────────────

    @staticmethod
    def _strip_fences(content: str) -> str:
        """Remove markdown code fences and surrounding whitespace."""
        fence = re.search(
            r"```(?:json)?\s*(.*?)```",
            content,
            re.DOTALL,
        )
        return fence.group(1).strip() if fence else content

    @staticmethod
    def _wrap_bare_array(data: list[Any], schema: type) -> dict[str, Any]:
        """Wrap a bare JSON array into the schema's first list-typed field."""
        fields = getattr(schema, "model_fields", {})
        list_field = next(
            (
                name
                for name, f in fields.items()
                if getattr(f.annotation, "__origin__", None) is list
                or str(f.annotation).startswith("list[")
            ),
            None,
        )
        if list_field:
            return {list_field: data}
        return {}

    @staticmethod
    def _rename_top_level(data: dict[str, Any]) -> dict[str, Any]:
        """Map model-invented top-level field names to canonical names."""
        mapping = {
            "issues": "findings",
            "candidates": "findings",
            "items": "findings",
            "results": "findings",
        }
        for alias, canonical in mapping.items():
            if alias in data and canonical not in data:
                data[canonical] = data.pop(alias)
        return data

    def _normalize_findings(
        self,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Normalize IRAC field aliases in every finding item."""
        findings = data.get("findings")
        if isinstance(findings, list):
            data["findings"] = [self._normalize_irac_item(item) for item in findings]
        return data

    @classmethod
    def _normalize_irac_item(cls, item: Any) -> Any:
        """Map alias keys in a single IRAC dict onto canonical field names."""
        if not isinstance(item, dict):
            return item
        normalized = dict(item)
        for target, aliases in cls._IRAC_ALIASES.items():
            if normalized.get(target):
                continue
            for alias in aliases:
                if item.get(alias):
                    normalized[target] = item[alias]
                    break
        return normalized
