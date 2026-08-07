"""Tests for Phase 1 structured-output reliability improvements.

Covers:
  - pydantic_to_json_schema (schema_format.py)
  - StructuredResponseParser (structured_parser.py)
  - LLMAdapter.generate_structured retry ladder
  - OpenRouterProvider finish_reason threading
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, Field

from leggie.application.ports.llm import LLMRequest, LLMResponse
from leggie.domain.models import ModelTier
from leggie.domain.models.structured_output import (
    CoVeCrossCheckResponse,
    IRACCandidate,
    LensFindings,
    SkepticVerdictResponse,
)
from leggie.infrastructure.llm import (
    LLMAdapter,
    LLMError,
    OpenRouterProvider,
)
from leggie.infrastructure.llm.schema_format import pydantic_to_json_schema
from leggie.infrastructure.llm.structured_parser import StructuredResponseParser

# ═══════════════════════════════════════════════════════════════════════
# 1. pydantic_to_json_schema tests
# ═══════════════════════════════════════════════════════════════════════

class TestPydanticToJsonSchema:

    def test_flat_schema(self):
        """A flat model produces an object schema with all fields required."""
        schema = pydantic_to_json_schema(IRACCandidate)
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        # All fields must be in required (strict mode)
        for field in ("issue", "rule", "application", "conclusion",
                      "verbatim_quote", "severity", "probability"):
            assert field in schema["required"], f"{field} missing from required"
        for field in ("issue", "rule"):
            assert schema["properties"][field]["type"] == "string"

    def test_schema_with_list(self):
        """A model with a list field produces proper array schema."""
        schema = pydantic_to_json_schema(LensFindings)
        assert schema["type"] == "object"
        assert "findings" in schema["required"]
        assert schema["properties"]["findings"]["type"] == "array"
        items = schema["properties"]["findings"]["items"]
        assert items["type"] == "object"
        assert "issue" in items["required"]

    def test_strict_no_additional_properties(self):
        """Every nested object also has additionalProperties: false."""
        schema = pydantic_to_json_schema(LensFindings)
        items = schema["properties"]["findings"]["items"]
        assert items.get("additionalProperties") is False

    def test_no_title_or_description_in_output(self):
        """Pydantic metadata keys are stripped for compatibility."""
        schema = pydantic_to_json_schema(IRACCandidate)
        assert "title" not in schema
        assert "description" not in schema
        for prop in schema["properties"].values():
            assert "title" not in prop
            assert "description" not in prop

    def test_inlines_refs(self):
        """$ref references are inlined so providers see a flat schema."""
        schema = pydantic_to_json_schema(LensFindings)
        assert "$defs" not in schema
        items = schema["properties"]["findings"]["items"]
        assert "issue" in items["properties"]
        assert "$ref" not in json.dumps(schema)

    def test_verbose_probability_field(self):
        """Probability field has correct type (number)."""
        schema = pydantic_to_json_schema(IRACCandidate)
        prob = schema["properties"]["probability"]
        assert prob["type"] == "number"

    def test_skeptic_verdict_schema(self):
        """SkepticVerdictResponse schema renders correctly."""
        schema = pydantic_to_json_schema(SkepticVerdictResponse)
        assert schema["type"] == "object"
        assert "verdict" in schema["required"]
        assert "reason" in schema["required"]
        assert schema["properties"]["verdict"]["type"] == "string"

    def test_number_constraints_stripped_for_provider_compatibility(self):
        """minimum/maximum on number fields are stripped — some providers reject them."""
        class ConstrainedModel(BaseModel):
            score: float = Field(ge=0.0, le=1.0)

        schema = pydantic_to_json_schema(ConstrainedModel)
        prop = schema["properties"]["score"]
        assert prop["type"] == "number"
        assert "minimum" not in prop
        assert "maximum" not in prop


# ═══════════════════════════════════════════════════════════════════════
# 2. StructuredResponseParser tests
# ═══════════════════════════════════════════════════════════════════════

class TestStructuredResponseParser:

    def make_parser(self) -> StructuredResponseParser:
        return StructuredResponseParser()

    # ── Happy path ────────────────────────────────────────────────

    def test_parse_valid_json(self):
        """Valid JSON matching the schema parses correctly."""
        parser = self.make_parser()
        content = json.dumps({
            "findings": [{
                "issue": "Test issue",
                "rule": "Test rule",
                "application": "Test application",
                "conclusion": "Test conclusion",
                "verbatim_quote": "A quote",
                "severity": "high",
                "probability": 0.8,
            }],
        })
        result = parser.parse(content, LensFindings)
        assert isinstance(result, LensFindings)
        assert len(result.findings) == 1
        assert result.findings[0].issue == "Test issue"
        assert result.findings[0].severity == "high"

    def test_parse_with_code_fences(self):
        """Markdown code fences are stripped before parsing."""
        parser = self.make_parser()
        inner = json.dumps({
            "findings": [{
                "issue": "Fenced issue",
                "rule": "Rule",
                "application": "App",
                "conclusion": "Conc",
            }],
        })
        content = f"```json\n{inner}\n```"
        result = parser.parse(content, LensFindings)
        assert len(result.findings) == 1
        assert result.findings[0].issue == "Fenced issue"

    def test_parse_code_fences_no_lang(self):
        """Fences without json tag are also stripped."""
        parser = self.make_parser()
        inner = json.dumps({
            "findings": [{
                "issue": "No lang",
                "rule": "R",
                "application": "A",
                "conclusion": "C",
            }],
        })
        content = f"```\n{inner}\n```"
        result = parser.parse(content, LensFindings)
        assert len(result.findings) == 1

    def test_parse_bare_array(self):
        """A bare array is wrapped into the schema's list field."""
        parser = self.make_parser()
        content = json.dumps([{
            "issue": "Array item",
            "rule": "Rule",
            "application": "App",
            "conclusion": "Conc",
        }])
        result = parser.parse(content, LensFindings)
        assert len(result.findings) == 1
        assert result.findings[0].issue == "Array item"

    def test_parse_issues_alias(self):
        """Top-level 'issues' is renamed to 'findings'."""
        parser = self.make_parser()
        content = json.dumps({
            "issues": [{
                "issue": "Issues alias",
                "rule": "Rule",
                "application": "App",
                "conclusion": "Conc",
            }],
        })
        result = parser.parse(content, LensFindings)
        assert len(result.findings) == 1

    def test_parse_empty_findings(self):
        """Empty findings list is valid."""
        parser = self.make_parser()
        content = json.dumps({"findings": []})
        result = parser.parse(content, LensFindings)
        assert len(result.findings) == 0

    # ── IRAC alias normalization ───────────────────────────────────

    def test_normalize_title_to_issue(self):
        """'title' in a finding item is mapped to 'issue'."""
        parser = self.make_parser()
        content = json.dumps({
            "findings": [{
                "title": "Title as issue",
                "rule": "Rule",
                "application": "App",
                "conclusion": "Conc",
            }],
        })
        result = parser.parse(content, LensFindings)
        assert result.findings[0].issue == "Title as issue"

    def test_normalize_constitutional_concern(self):
        """'constitutional_concern' alias is mapped (observed drift)."""
        parser = self.make_parser()
        content = json.dumps({
            "findings": [{
                "constitutional_concern": "CC issue",
                "rule": "Rule",
                "application": "App",
                "conclusion": "Conc",
            }],
        })
        result = parser.parse(content, LensFindings)
        # constitutional_concern appears in issue, application, and
        # conclusion aliases; issue comes first.
        assert result.findings[0].issue == "CC issue"

    def test_normalize_legal_issue(self):
        """'legal_issue' alias is mapped to 'issue'."""
        parser = self.make_parser()
        content = json.dumps({
            "findings": [{
                "legal_issue": "Legal issue text",
                "rule": "R",
                "application": "A",
                "conclusion": "C",
            }],
        })
        result = parser.parse(content, LensFindings)
        assert result.findings[0].issue == "Legal issue text"

    def test_normalize_problem(self):
        """'problem' alias is mapped to 'issue'."""
        parser = self.make_parser()
        content = json.dumps({
            "findings": [{
                "problem": "Problem text",
                "rule": "R",
                "application": "A",
                "conclusion": "C",
            }],
        })
        result = parser.parse(content, LensFindings)
        assert result.findings[0].issue == "Problem text"

    def test_normalize_finding_text(self):
        """'finding_text' alias is mapped to 'issue'."""
        parser = self.make_parser()
        content = json.dumps({
            "findings": [{
                "finding_text": "Finding text",
                "rule": "R",
                "application": "A",
                "conclusion": "C",
            }],
        })
        result = parser.parse(content, LensFindings)
        assert result.findings[0].issue == "Finding text"

    def test_normalize_excerpt_to_quote(self):
        """'excerpt' is mapped to 'verbatim_quote'."""
        parser = self.make_parser()
        content = json.dumps({
            "findings": [{
                "issue": "Issue",
                "rule": "R",
                "application": "A",
                "conclusion": "C",
                "excerpt": "Quote text",
            }],
        })
        result = parser.parse(content, LensFindings)
        assert result.findings[0].verbatim_quote == "Quote text"

    def test_canonical_key_takes_priority(self):
        """If canonical key already has a value, alias is not used."""
        parser = self.make_parser()
        content = json.dumps({
            "findings": [{
                "issue": "Canonical issue",
                "title": "Title that should be ignored",
                "rule": "R",
                "application": "A",
                "conclusion": "C",
            }],
        })
        result = parser.parse(content, LensFindings)
        assert result.findings[0].issue == "Canonical issue"

    # ── Error handling ─────────────────────────────────────────────

    def test_parse_invalid_json_raises(self):
        """Invalid JSON raises ValueError."""
        parser = self.make_parser()
        with pytest.raises(ValueError, match="JSON decode failed"):
            parser.parse("{invalid json}", LensFindings)

    def test_parse_empty_content_raises(self):
        """Empty or whitespace-only content raises ValueError."""
        parser = self.make_parser()
        with pytest.raises(ValueError, match="Empty content"):
            parser.parse("   ", LensFindings)

    def test_parse_none_content_raises(self):
        """None content is treated as empty and raises ValueError."""
        parser = self.make_parser()
        with pytest.raises(ValueError, match="Empty content"):
            parser.parse(None, LensFindings)  # type: ignore[arg-type]

    def test_parse_schema_mismatch_raises(self):
        """JSON with missing required fields in findings raises ValueError."""
        parser = self.make_parser()
        # IRACCandidate requires issue, rule, application, conclusion.
        # This finding item is missing all of them.
        content = json.dumps({"findings": [{"wrong_field": "value"}]})
        with pytest.raises(ValueError, match="Schema validation failed"):
            parser.parse(content, LensFindings)

    def test_parse_truncated_json_raises(self):
        """Unterminated string raises ValueError."""
        parser = self.make_parser()
        with pytest.raises(ValueError, match="JSON decode failed"):
            parser.parse('{"findings": [{"issue": "Unterminated}', LensFindings)

    def test_non_dict_non_list_fails(self):
        """A plain string as JSON raises ValueError."""
        parser = self.make_parser()
        with pytest.raises(ValueError, match="Expected dict"):
            parser.parse('"just a string"', LensFindings)

    def test_single_object_dict_parses_as_empty_findings(self):
        """A top-level dict with IRAC fields but no 'findings' key parses as
        empty findings (Pydantic ignores extra keys by default)."""
        parser = self.make_parser()
        content = json.dumps({
            "issue": "Single obj",
            "rule": "R",
            "application": "A",
            "conclusion": "C",
        })
        result = parser.parse(content, LensFindings)
        assert isinstance(result, LensFindings)
        assert len(result.findings) == 0


# ═══════════════════════════════════════════════════════════════════════
# 3. LLMAdapter.generate_structured retry ladder tests
# ═══════════════════════════════════════════════════════════════════════

class TestGenerateStructuredRetry:
    """Test the retry ladder in LLMAdapter.generate_structured."""

    @pytest.fixture
    def adapter(self):
        return LLMAdapter(openrouter_key="sk-test")

    @pytest.fixture
    def valid_response(self):
        return LLMResponse(
            content=json.dumps({
                "findings": [{
                    "issue": "Test",
                    "rule": "Rule",
                    "application": "App",
                    "conclusion": "Conc",
                }],
            }),
            model="test-model",
            tier_used=ModelTier.BUDGET,
            usage={"prompt_tokens": 10, "completion_tokens": 5},
            finish_reason="stop",
        )

    @pytest.mark.asyncio
    async def test_json_schema_mode_used_first(self, adapter):
        """First attempt uses json_schema strict mode."""
        with patch.object(adapter, "generate", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = LLMResponse(
                content=json.dumps({"findings": []}),
                model="test",
                tier_used=ModelTier.BUDGET,
                usage={},
            )
            result, response = await adapter.generate_structured(
                LLMRequest(prompt="test"), LensFindings,
            )
            assert isinstance(result, LensFindings)

            # Verify json_schema was sent
            call_req = mock_gen.call_args[0][0]
            assert call_req.response_format is not None
            assert call_req.response_format["type"] == "json_schema"

    @pytest.mark.asyncio
    async def test_fallback_to_json_object_on_400(self, adapter):
        """On 400 error, falls back to json_object mode."""
        call_count = 0

        async def mock_generate(req):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise LLMError("400 Bad Request: json_schema not supported")
            return LLMResponse(
                content=json.dumps({"findings": []}),
                model="test",
                tier_used=ModelTier.BUDGET,
                usage={},
            )

        with patch.object(adapter, "generate", side_effect=mock_generate):
            result, response = await adapter.generate_structured(
                LLMRequest(prompt="test"), LensFindings,
            )
            assert isinstance(result, LensFindings)
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_truncation_retry_on_length(self, adapter):
        """When finish_reason=length, retry with doubled max_tokens."""
        call_count = 0

        async def mock_generate(req):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call fails with truncated content
                return LLMResponse(
                    content='{"findings": [{"issue": "Truncated", "rule": "R", "app',
                    model="test",
                    tier_used=ModelTier.BUDGET,
                    usage={"prompt_tokens": 10, "completion_tokens": 5},
                    finish_reason="length",
                )
            return LLMResponse(
                content=json.dumps({
                    "findings": [{
                        "issue": "Complete",
                        "rule": "Rule",
                        "application": "App",
                        "conclusion": "Conc",
                    }],
                }),
                model="test",
                tier_used=ModelTier.BUDGET,
                usage={"prompt_tokens": 10, "completion_tokens": 10},
                finish_reason="stop",
            )

        with patch.object(adapter, "generate", side_effect=mock_generate):
            result, response = await adapter.generate_structured(
                LLMRequest(prompt="test", max_tokens=1000), LensFindings,
            )
            assert isinstance(result, LensFindings)
            assert len(result.findings) == 1
            assert result.findings[0].issue == "Complete"
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_repair_round_used_as_last_resort(self, adapter):
        """When truncation retry also fails, repair round is attempted."""
        call_count = 0

        async def mock_generate(req):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return LLMResponse(
                    content='{"findings": [{"issue": "Bad", "rule": "R"',
                    model="test",
                    tier_used=ModelTier.BUDGET,
                    usage={},
                    finish_reason="length",
                )
            # Repair round succeeds
            return LLMResponse(
                content=json.dumps({
                    "findings": [{
                        "issue": "Repaired",
                        "rule": "Rule",
                        "application": "App",
                        "conclusion": "Conc",
                    }],
                }),
                model="test",
                tier_used=ModelTier.BUDGET,
                usage={},
                finish_reason="stop",
            )

        with patch.object(adapter, "generate", side_effect=mock_generate):
            result, response = await adapter.generate_structured(
                LLMRequest(prompt="test", max_tokens=1000, response_format={"type": "json_object"}),
                LensFindings,
            )
            assert isinstance(result, LensFindings)
            assert result.findings[0].issue == "Repaired"
            assert call_count == 3

    @pytest.mark.asyncio
    async def test_all_attempts_exhausted_raises(self, adapter):
        """When all retries fail, raises LLMError."""
        async def mock_generate(req):
            return LLMResponse(
                content="completely invalid {content}",
                model="test",
                tier_used=ModelTier.BUDGET,
                usage={},
                finish_reason="stop",
            )

        with (
            patch.object(adapter, "generate", side_effect=mock_generate),
            pytest.raises(LLMError, match="Failed to parse structured response"),
        ):
            await adapter.generate_structured(
                LLMRequest(prompt="test", max_tokens=1000,
                           response_format={"type": "json_object"}),
                LensFindings,
            )

    @pytest.mark.asyncio
    async def test_repair_round_skipped_on_prose_garbage(self, adapter):
        """Prose-only garbage must not trigger a paid repair round."""
        calls: list[LLMRequest] = []

        async def mock_generate(req: LLMRequest):
            calls.append(req)
            return LLMResponse(
                content="This is just prose with no JSON skeleton.",
                model="test",
                tier_used=ModelTier.BUDGET,
                usage={},
                finish_reason="stop",
            )

        with (
            patch.object(adapter, "generate", side_effect=mock_generate),
            pytest.raises(LLMError, match="no JSON skeleton"),
        ):
            await adapter.generate_structured(
                LLMRequest(prompt="test", max_tokens=1000,
                           response_format={"type": "json_object"}),
                LensFindings,
            )

        # Only the json_schema and json_object attempts should fire.
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_max_tokens_doubled_on_truncation(self, adapter):
        """Truncation retry doubles max_tokens (capped)."""
        call_count = 0

        async def mock_generate(req):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                # Calls 1 (json_schema) + 2 (json_object): both truncated
                return LLMResponse(
                    content='{"bad": "data"',
                    model="test",
                    tier_used=ModelTier.BUDGET,
                    usage={},
                    finish_reason="length",
                )
            # Call 3: truncation retry — should have doubled max_tokens
            assert req.max_tokens == 2000  # 1000 * 2
            return LLMResponse(
                content=json.dumps({"findings": []}),
                model="test",
                tier_used=ModelTier.BUDGET,
                usage={},
                finish_reason="stop",
            )

        with patch.object(adapter, "generate", side_effect=mock_generate):
            await adapter.generate_structured(
                LLMRequest(prompt="test", max_tokens=1000), LensFindings,
            )
            assert call_count == 3


class TestNumericFieldsClampNotReject:
    """D18: an LLM-emitted numeric field out of its advisory range must CLAMP,
    not REJECT the whole structured response. Before this fix, a model emitting
    confidence_adjustment=-0.8 or probability=1.5 raised a pydantic
    ValidationError -> the ladder exhausted -> the entire verdict/finding-set
    was discarded. Both consumers already clamp the final Confidence.score, so
    the raw delta/probability range is advisory only. See
    docs/REMEDIATION_PLAN_V3.md D18."""

    def make_parser(self) -> StructuredResponseParser:
        return StructuredResponseParser()

    def test_skeptic_adjustment_out_of_range_clamps(self):
        parser = self.make_parser()
        low = parser.parse(
            json.dumps({"verdict": "refutes", "reason": "x", "confidence_adjustment": -0.8}),
            SkepticVerdictResponse,
        )
        assert low.confidence_adjustment == -0.5
        high = parser.parse(
            json.dumps({"verdict": "supports", "reason": "x", "confidence_adjustment": 0.9}),
            SkepticVerdictResponse,
        )
        assert high.confidence_adjustment == 0.5

    def test_skeptic_adjustment_non_numeric_defaults_zero(self):
        parser = self.make_parser()
        r = parser.parse(
            json.dumps({"verdict": "neutral", "reason": "x", "confidence_adjustment": "n/a"}),
            SkepticVerdictResponse,
        )
        assert r.confidence_adjustment == 0.0

    def test_crosscheck_adjustment_out_of_range_clamps(self):
        parser = self.make_parser()
        r = parser.parse(
            json.dumps({
                "consistency": "partially_consistent", "reason": "x",
                "keep": True, "confidence_adjustment": -0.7,
            }),
            CoVeCrossCheckResponse,
        )
        assert r.confidence_adjustment == -0.5

    def test_lens_probability_out_of_range_clamps_not_rejects_whole_set(self):
        """The failure mode that mattered most: one bad probability must not
        discard every finding in the response."""
        parser = self.make_parser()
        content = json.dumps({"findings": [
            {"issue": "a", "rule": "r", "application": "ap", "conclusion": "c", "probability": 1.5},
            {"issue": "b", "rule": "r", "application": "ap", "conclusion": "c", "probability": 0.7},
        ]})
        result = parser.parse(content, LensFindings)
        assert len(result.findings) == 2  # neither discarded
        assert result.findings[0].probability == 1.0  # clamped
        assert result.findings[1].probability == 0.7  # untouched


# ═══════════════════════════════════════════════════════════════════════
# 4. OpenRouterProvider finish_reason threading tests
# ═══════════════════════════════════════════════════════════════════════

class TestOpenRouterFinishReason:
    """Verify finish_reason is captured from API response."""

    @pytest.mark.asyncio
    async def test_finish_reason_stop(self):
        """finish_reason='stop' is threaded through."""
        prov = OpenRouterProvider(api_key="sk-test")
        body = {
            "choices": [{
                "message": {"content": "OK", "role": "assistant"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "model": "test-model",
        }
        with patch.object(prov._http_client, "post", new_callable=AsyncMock) as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = body
            mock_post.return_value = mock_resp

            with patch.object(prov._rate_limiter, "acquire", new_callable=AsyncMock):
                response = await prov.generate(LLMRequest(prompt="test"))
            assert response.finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_finish_reason_length(self):
        """finish_reason='length' is threaded through."""
        prov = OpenRouterProvider(api_key="sk-test")
        body = {
            "choices": [{
                "message": {"content": "Partial...", "role": "assistant"},
                "finish_reason": "length",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 100},
            "model": "test-model",
        }
        with patch.object(prov._http_client, "post", new_callable=AsyncMock) as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = body
            mock_post.return_value = mock_resp

            with patch.object(prov._rate_limiter, "acquire", new_callable=AsyncMock):
                response = await prov.generate(LLMRequest(prompt="test"))
            assert response.finish_reason == "length"

    @pytest.mark.asyncio
    async def test_finish_reason_defaults_to_stop(self):
        """Missing finish_reason defaults to 'stop'."""
        prov = OpenRouterProvider(api_key="sk-test")
        body = {
            "choices": [{
                "message": {"content": "OK", "role": "assistant"},
                # no finish_reason key
            }],
            "usage": {},
            "model": "test-model",
        }
        with patch.object(prov._http_client, "post", new_callable=AsyncMock) as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = body
            mock_post.return_value = mock_resp

            with patch.object(prov._rate_limiter, "acquire", new_callable=AsyncMock):
                response = await prov.generate(LLMRequest(prompt="test"))
            assert response.finish_reason == "stop"
