"""Tests for CoVe evidence loop — Chain of Verification."""

import pytest

from leggie.application.ports.llm import LLMResponse
from leggie.application.ports.router import RouteResult
from leggie.application.services.cove_verifier import CoVeVerifier
from leggie.domain.models import (
    IRAC,
    Citation,
    CitationScheme,
    Confidence,
    Event,
    EventType,
    Evidence,
    Finding,
    FindingType,
    ModelTier,
)
from leggie.domain.models.structured_output import (
    CoVeAnswerResponse,
    CoVeCrossCheckResponse,
    CoVeQuestionsResponse,
)


class FakeLLM:
    """Scripted LLM: returns a canned object per requested schema."""

    def __init__(self, responses: dict) -> None:
        self._responses = responses
        self.calls: list[str] = []
        self.requests: list = []

    async def generate(self, request):  # pragma: no cover - unused
        raise NotImplementedError

    async def generate_structured(self, request, schema):
        self.calls.append(schema.__name__)
        self.requests.append(request)
        obj = self._responses[schema.__name__]
        resp = LLMResponse(content="", model="fake", tier_used=ModelTier.PREMIUM, usage={})
        return obj, resp

    async def count_tokens(self, text, model=None):  # pragma: no cover
        return len(text) // 4


class FakeRouter:
    """Scripted router: returns a fixed RouteResult for any task type."""

    def __init__(self, max_tokens: int, model: str = "routed-model") -> None:
        self._max_tokens = max_tokens
        self._model = model

    async def route(self, task_type, budget_remaining=None):
        return RouteResult(model=self._model, tier=ModelTier.PREMIUM, max_tokens=self._max_tokens)

    async def cascade(self, task_type, current_tier, failure_reason=None):
        return None

    def supported_models(self):
        return [self._model]


class CrashingLLM:
    """Always raises — exercises the CoVe fail-open path."""

    async def generate(self, request):  # pragma: no cover - unused
        raise NotImplementedError

    async def generate_structured(self, request, schema):
        raise RuntimeError("boom")

    async def count_tokens(self, text, model=None):  # pragma: no cover
        return len(text) // 4


def make_finding(conclusion: str = "conc", quote: str = "") -> Finding:
    evidence = [Evidence(text_excerpt=quote, verdict="supports")] if quote else []
    return Finding(
        finding_type=FindingType.CONSTITUTIONAL,
        irac=IRAC(issue="Άρθρο 5 test", rule="rule", application="app", conclusion=conclusion),
        confidence=Confidence.from_score(0.6),
        lens="test", model="test",
        evidence=evidence,
    )


def make_finding_with_citations(citations: list[Citation] | None = None) -> Finding:
    evidence_list = []
    if citations:
        for c in citations:
            evidence_list.append(Evidence(citation=c, text_excerpt="test", verdict="supports"))
    return Finding(
        finding_type=FindingType.CONSTITUTIONAL,
        irac=IRAC(issue="test", rule="rule", application="app", conclusion="conc"),
        confidence=Confidence.from_score(0.5),
        lens="test", model="test",
        evidence=evidence_list,
    )


class TestCoVeVerifier:
    @pytest.mark.asyncio
    async def test_verify_no_citations(self):
        verifier = CoVeVerifier()
        result = await verifier.verify(make_finding_with_citations())
        assert result.all_verified is True
        assert len(result.questions) == 0

    @pytest.mark.asyncio
    async def test_verify_with_citations(self):
        verifier = CoVeVerifier()
        cites = [
            Citation(
                scheme=CitationScheme.FEK,
                identifier="ΦΕΚ Α 137/2023",
                original_text="ΦΕΚ Α 137/2023",
                resolved=True,
            ),
        ]
        result = await verifier.verify(make_finding_with_citations(cites))
        assert len(result.questions) == 1
        assert result.questions[0].citation.identifier == "ΦΕΚ Α 137/2023"

    @pytest.mark.asyncio
    async def test_verify_multiple_citations(self):
        verifier = CoVeVerifier()
        cites = [
            Citation(scheme=CitationScheme.FEK, identifier="ΦΕΚ Α 1/2023", original_text="ΦΕΚ Α 1/2023", resolved=True),
            Citation(scheme=CitationScheme.CELEX, identifier="32018L1972", original_text="CELEX:32018L1972", resolved=True),
        ]
        result = await verifier.verify(make_finding_with_citations(cites))
        assert result.verified_count == 2
        assert result.all_verified is True

    @pytest.mark.asyncio
    async def test_verify_batch(self):
        verifier = CoVeVerifier()
        findings = [
            make_finding_with_citations(),
            make_finding_with_citations([
                Citation(scheme=CitationScheme.FEK, identifier="ΦΕΚ Α 1/2023", original_text="ΦΕΚ Α 1/2023", resolved=True),
            ]),
        ]
        results = await verifier.verify_batch(findings)
        assert len(results) == 2
        assert results[0].all_verified is True

    # ── LLM 4-step CoVe path ────────────────────────────────────────
    @pytest.mark.asyncio
    async def test_llm_consistent_keeps_finding(self):
        llm = FakeLLM({
            "CoVeQuestionsResponse": CoVeQuestionsResponse(questions=["Τι προβλέπει;"]),
            "CoVeAnswerResponse": CoVeAnswerResponse(answer="ok", supported_by_source=True),
            "CoVeCrossCheckResponse": CoVeCrossCheckResponse(
                consistency="consistent", reason="r", keep=True),
        })
        verifier = CoVeVerifier(llm=llm)
        result = await verifier.verify(make_finding(), source_text="πηγή")
        assert result.dropped is False
        assert result.consistency == "consistent"
        assert result.all_verified is True

    @pytest.mark.asyncio
    async def test_llm_inconsistent_drops_finding(self):
        llm = FakeLLM({
            "CoVeQuestionsResponse": CoVeQuestionsResponse(questions=["Τι προβλέπει;"]),
            "CoVeAnswerResponse": CoVeAnswerResponse(answer="no", supported_by_source=False),
            "CoVeCrossCheckResponse": CoVeCrossCheckResponse(
                consistency="inconsistent", reason="contradicted", keep=False),
        })
        verifier = CoVeVerifier(llm=llm)
        result = await verifier.verify(make_finding(), source_text="πηγή")
        assert result.dropped is True

    @pytest.mark.asyncio
    async def test_llm_fabricated_quote_dropped_without_calls(self):
        llm = FakeLLM({})  # must not be called
        verifier = CoVeVerifier(llm=llm)
        finding = make_finding(quote="ΑΥΤΟ ΔΕΝ ΥΠΑΡΧΕΙ ΣΤΗΝ ΠΗΓΗ")
        result = await verifier.verify(finding, source_text="εντελώς άλλο κείμενο")
        assert result.dropped is True
        assert result.consistency == "inconsistent"
        assert llm.calls == []  # quote gate short-circuits

    @pytest.mark.asyncio
    async def test_llm_partially_consistent_revises(self):
        llm = FakeLLM({
            "CoVeQuestionsResponse": CoVeQuestionsResponse(questions=["Τι;"]),
            "CoVeAnswerResponse": CoVeAnswerResponse(answer="partial", supported_by_source=True),
            "CoVeCrossCheckResponse": CoVeCrossCheckResponse(
                consistency="partially_consistent", reason="fix",
                keep=True, revised_conclusion="διορθωμένο", confidence_adjustment=-0.2),
        })
        verifier = CoVeVerifier(llm=llm)
        original = make_finding(conclusion="αρχικό")
        result = await verifier.verify(original, source_text="πηγή")
        assert result.dropped is False
        assert result.finding.irac.conclusion == "διορθωμένο"
        assert result.finding.confidence.score == pytest.approx(0.4, abs=0.01)
        assert result.finding.version == original.version + 1

    @pytest.mark.asyncio
    async def test_llm_no_questions_passes_through(self):
        llm = FakeLLM({
            "CoVeQuestionsResponse": CoVeQuestionsResponse(questions=[]),
        })
        verifier = CoVeVerifier(llm=llm)
        result = await verifier.verify(make_finding(), source_text="πηγή")
        assert result.dropped is False
        assert result.all_verified is True
        assert llm.calls == ["CoVeQuestionsResponse"]

    @pytest.mark.asyncio
    async def test_llm_citation_disproven_drops_without_llm_calls(self):
        from leggie.infrastructure.citation import GreekCitationParser
        parser = GreekCitationParser(resolution_index={"ΦΕΚ Α 1/2020"})  # our citation not in it
        llm = FakeLLM({})  # must not be called — citation gate short-circuits
        verifier = CoVeVerifier(llm=llm, citation_parser=parser)
        finding = make_finding()
        finding = finding.model_copy(update={"irac": IRAC(
            issue=finding.irac.issue, rule="Βλ. ΦΕΚ Α 999/2023",
            application=finding.irac.application, conclusion=finding.irac.conclusion,
        )})
        result = await verifier.verify(finding, source_text="πηγή")
        assert result.dropped is True
        assert llm.calls == []

    @pytest.mark.asyncio
    async def test_llm_citation_no_index_does_not_disprove(self):
        from leggie.infrastructure.citation import GreekCitationParser
        parser = GreekCitationParser()  # no index configured
        llm = FakeLLM({
            "CoVeQuestionsResponse": CoVeQuestionsResponse(questions=["Τι;"]),
            "CoVeAnswerResponse": CoVeAnswerResponse(answer="ok", supported_by_source=True),
            "CoVeCrossCheckResponse": CoVeCrossCheckResponse(
                consistency="consistent", reason="r", keep=True),
        })
        verifier = CoVeVerifier(llm=llm, citation_parser=parser)
        finding = make_finding()
        finding = finding.model_copy(update={"irac": IRAC(
            issue=finding.irac.issue, rule="Βλ. ΦΕΚ Α 999/2023",
            application=finding.irac.application, conclusion=finding.irac.conclusion,
        )})
        result = await verifier.verify(finding, source_text="πηγή")
        assert result.dropped is False  # unverifiable, not disproven — LLM decides

    @pytest.mark.asyncio
    async def test_plan_questions_from_text_excerpt(self):
        from leggie.infrastructure.citation import GreekCitationParser
        parser = GreekCitationParser()
        verifier = CoVeVerifier(citation_parser=parser)
        finding = Finding(
            finding_type=FindingType.CONSTITUTIONAL,
            irac=IRAC(issue="test", rule="rule", application="app", conclusion="conc"),
            confidence=Confidence.from_score(0.5),
            lens="test", model="test",
            evidence=[Evidence(text_excerpt="ΦΕΚ Α 137/2023", verdict="supports")],
        )
        result = await verifier.verify(finding)
        assert len(result.questions) >= 1


class TestCoVeRouteAndDegradation:
    """D11/D12/D13: the route's max_tokens must reach every LLM call in the
    4-step loop, and a call that fails open must be countable, not just
    logged — see docs/REMEDIATION_PLAN_V3.md Phase A."""

    @pytest.mark.asyncio
    async def test_uses_router_max_tokens_for_all_three_steps(self):
        llm = FakeLLM({
            "CoVeQuestionsResponse": CoVeQuestionsResponse(questions=["Τι;"]),
            "CoVeAnswerResponse": CoVeAnswerResponse(answer="ok", supported_by_source=True),
            "CoVeCrossCheckResponse": CoVeCrossCheckResponse(
                consistency="consistent", reason="r", keep=True),
        })
        router = FakeRouter(max_tokens=8192)
        verifier = CoVeVerifier(llm=llm, router=router)
        await verifier.verify(make_finding(), source_text="πηγή")
        assert llm.calls == [
            "CoVeQuestionsResponse", "CoVeAnswerResponse", "CoVeCrossCheckResponse",
        ]
        assert all(r.max_tokens == 8192 for r in llm.requests)

    @pytest.mark.asyncio
    async def test_answer_step_floor_not_starved_by_low_route_ceiling(self):
        """The answer step's floor (1024) protects it from a route configured
        lower for a different call shape — see cove_verifier.py's comment."""
        llm = FakeLLM({
            "CoVeQuestionsResponse": CoVeQuestionsResponse(questions=["Τι;"]),
            "CoVeAnswerResponse": CoVeAnswerResponse(answer="ok", supported_by_source=True),
            "CoVeCrossCheckResponse": CoVeCrossCheckResponse(
                consistency="consistent", reason="r", keep=True),
        })
        router = FakeRouter(max_tokens=256)
        verifier = CoVeVerifier(llm=llm, router=router)
        await verifier.verify(make_finding(), source_text="πηγή")
        answer_request = llm.requests[1]
        assert answer_request.max_tokens == 1024

    @pytest.mark.asyncio
    async def test_no_router_falls_back_to_default_max_tokens(self):
        llm = FakeLLM({
            "CoVeQuestionsResponse": CoVeQuestionsResponse(questions=["Τι;"]),
            "CoVeAnswerResponse": CoVeAnswerResponse(answer="ok", supported_by_source=True),
            "CoVeCrossCheckResponse": CoVeCrossCheckResponse(
                consistency="consistent", reason="r", keep=True),
        })
        verifier = CoVeVerifier(llm=llm)  # no router
        await verifier.verify(make_finding(), source_text="πηγή")
        assert llm.requests[0].max_tokens == 2048  # plan
        assert llm.requests[1].max_tokens == 1024  # answer
        assert llm.requests[2].max_tokens == 2048  # cross-check

    @pytest.mark.asyncio
    async def test_route_resolution_logged_at_info_not_debug(self, caplog):
        """D19: route resolution must be observable without DEBUG level --
        a silent fallback to the wrong ceiling was reproduced twice in live
        runs (subset6, subset8) with zero warning logged. INFO has been
        reliable in every live run so far; DEBUG has not."""
        import logging

        llm = FakeLLM({
            "CoVeQuestionsResponse": CoVeQuestionsResponse(questions=["Τι;"]),
            "CoVeAnswerResponse": CoVeAnswerResponse(answer="ok", supported_by_source=True),
            "CoVeCrossCheckResponse": CoVeCrossCheckResponse(
                consistency="consistent", reason="r", keep=True),
        })
        router = FakeRouter(max_tokens=8192)
        verifier = CoVeVerifier(llm=llm, router=router)
        with caplog.at_level(logging.INFO, logger="leggie.application.services.cove_verifier"):
            await verifier.verify(make_finding(), source_text="πηγή")
        assert any(
            "cove_route_resolved" in r.message and "8192" in r.message
            for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_no_router_logs_absent_at_info(self, caplog):
        import logging

        llm = FakeLLM({
            "CoVeQuestionsResponse": CoVeQuestionsResponse(questions=["Τι;"]),
            "CoVeAnswerResponse": CoVeAnswerResponse(answer="ok", supported_by_source=True),
            "CoVeCrossCheckResponse": CoVeCrossCheckResponse(
                consistency="consistent", reason="r", keep=True),
        })
        verifier = CoVeVerifier(llm=llm)  # no router
        with caplog.at_level(logging.INFO, logger="leggie.application.services.cove_verifier"):
            await verifier.verify(make_finding(), source_text="πηγή")
        assert any("cove_route_absent" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_llm_error_emits_degradation_event(self):
        events: list[Event] = []
        verifier = CoVeVerifier(llm=CrashingLLM(), on_degradation=events.append)
        f = make_finding()
        result = await verifier.verify(f, source_text="πηγή")
        assert result.dropped is False  # fail open
        assert len(events) == 1
        assert events[0].event_type == EventType.DEGRADED
        assert events[0].data["stage"] == "cove"
        assert events[0].data["finding_id"] == str(f.id)

    @pytest.mark.asyncio
    async def test_no_degradation_event_on_success(self):
        events: list[Event] = []
        llm = FakeLLM({
            "CoVeQuestionsResponse": CoVeQuestionsResponse(questions=["Τι;"]),
            "CoVeAnswerResponse": CoVeAnswerResponse(answer="ok", supported_by_source=True),
            "CoVeCrossCheckResponse": CoVeCrossCheckResponse(
                consistency="consistent", reason="r", keep=True),
        })
        verifier = CoVeVerifier(llm=llm, on_degradation=events.append)
        await verifier.verify(make_finding(), source_text="πηγή")
        assert events == []
