"""Tests for CoVe evidence loop — Chain of Verification."""

from typing import Any

import pytest

from leggie.application.ports.citation_parser import CitationParserPort
from leggie.application.ports.llm import LLMPort, LLMResponse
from leggie.application.services.cove_verifier import CoVeVerifier, article_number_of
from leggie.domain.models import (
    IRAC,
    Citation,
    CitationScheme,
    Confidence,
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


class FakeLLM(LLMPort):
    """Scripted LLM: returns a canned object per requested schema.

    Subclasses the port rather than duck-typing it, so a new abstract method
    on LLMPort breaks this fake loudly instead of leaving it silently behind
    the interface it stands in for.
    """

    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses
        self.calls: list[str] = []

    async def generate(self, request):  # pragma: no cover - unused
        raise NotImplementedError

    async def generate_structured(self, request, schema):
        self.calls.append(schema.__name__)
        obj = self._responses[schema.__name__]
        resp = LLMResponse(content="", model="fake", tier_used=ModelTier.PREMIUM, usage={})
        return obj, resp

    async def count_tokens(self, text, model=None):  # pragma: no cover
        return len(text) // 4


def make_finding(conclusion: str = "conc", quote: str = "") -> Finding:
    evidence = [Evidence(text_excerpt=quote, verdict="supports")] if quote else []
    return Finding(
        finding_type=FindingType.CONSTITUTIONAL,
        irac=IRAC(issue="Άρθρο 5 test", rule="rule", application="app", conclusion=conclusion),
        confidence=Confidence.from_score(0.6),
        lens="test",
        model="test",
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
        lens="test",
        model="test",
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
                checked=True,
            ),
        ]
        result = await verifier.verify(make_finding_with_citations(cites))
        assert len(result.questions) == 1
        citation = result.questions[0].citation
        assert citation is not None
        assert citation.identifier == "ΦΕΚ Α 137/2023"

    @pytest.mark.asyncio
    async def test_verify_multiple_citations(self):
        verifier = CoVeVerifier()
        cites = [
            Citation(
                scheme=CitationScheme.FEK,
                identifier="ΦΕΚ Α 1/2023",
                original_text="ΦΕΚ Α 1/2023",
                resolved=True,
                checked=True,
            ),
            Citation(
                scheme=CitationScheme.CELEX,
                identifier="32018L1972",
                original_text="CELEX:32018L1972",
                resolved=True,
                checked=True,
            ),
        ]
        result = await verifier.verify(make_finding_with_citations(cites))
        assert result.verified_count == 2
        assert result.all_verified is True

    @pytest.mark.asyncio
    async def test_verify_batch(self):
        verifier = CoVeVerifier()
        findings = [
            make_finding_with_citations(),
            make_finding_with_citations(
                [
                    Citation(
                        scheme=CitationScheme.FEK,
                        identifier="ΦΕΚ Α 1/2023",
                        original_text="ΦΕΚ Α 1/2023",
                        resolved=True,
                        checked=True,
                    ),
                ]
            ),
        ]
        results = await verifier.verify_batch(findings)
        assert len(results) == 2
        assert results[0].all_verified is True

    # ── LLM 4-step CoVe path ────────────────────────────────────────
    @pytest.mark.asyncio
    async def test_llm_consistent_keeps_finding(self):
        llm = FakeLLM(
            {
                "CoVeQuestionsResponse": CoVeQuestionsResponse(questions=["Τι προβλέπει;"]),
                "CoVeAnswerResponse": CoVeAnswerResponse(answer="ok", supported_by_source=True),
                "CoVeCrossCheckResponse": CoVeCrossCheckResponse(
                    consistency="consistent", reason="r", keep=True
                ),
            }
        )
        verifier = CoVeVerifier(llm=llm)
        result = await verifier.verify(make_finding(), source_text="πηγή")
        assert result.dropped is False
        assert result.consistency == "consistent"
        assert result.all_verified is True

    @pytest.mark.asyncio
    async def test_llm_inconsistent_drops_finding(self):
        llm = FakeLLM(
            {
                "CoVeQuestionsResponse": CoVeQuestionsResponse(questions=["Τι προβλέπει;"]),
                "CoVeAnswerResponse": CoVeAnswerResponse(answer="no", supported_by_source=False),
                "CoVeCrossCheckResponse": CoVeCrossCheckResponse(
                    consistency="inconsistent", reason="contradicted", keep=False
                ),
            }
        )
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
    async def test_f3_gate_fires_without_article_prefix_in_issue(self):
        """The F3 quote gate must fire even when the LLM-authored irac.issue
        never literally says 'Άρθρο N' — source-text resolution must use
        article_id, not just regex-parse free text (see article_number_of)."""
        real_article_text = "Η άδεια χορηγείται εντός δέκα (10) εργάσιμων ημερών."
        fabricated_quote = "Η άδεια χορηγείται αυτοδικαίως χωρίς καμία προθεσμία."

        finding = Finding(
            finding_type=FindingType.CONSTITUTIONAL,
            irac=IRAC(
                issue="Υπάρχει ασάφεια ως προς την προθεσμία χορήγησης της άδειας",
                rule="Άρθρο 25 Συντάγματος",
                application="Η διάταξη δεν ορίζει σαφή προθεσμία",
                conclusion="Πιθανή αοριστία στη διάταξη",
            ),
            confidence=Confidence.from_score(0.8),
            lens="constitutional",
            model="test-model",
            article_id="15",
            evidence=[Evidence(text_excerpt=fabricated_quote, verdict="supports")],
        )

        llm = FakeLLM({})  # must not be called — F3 gate short-circuits first
        verifier = CoVeVerifier(llm=llm)
        results = await verifier.verify_batch([finding], article_index={"15": real_article_text})

        assert results[0].dropped is True
        assert llm.calls == []

    @pytest.mark.asyncio
    async def test_llm_partially_consistent_revises(self):
        llm = FakeLLM(
            {
                "CoVeQuestionsResponse": CoVeQuestionsResponse(questions=["Τι;"]),
                "CoVeAnswerResponse": CoVeAnswerResponse(
                    answer="partial", supported_by_source=True
                ),
                "CoVeCrossCheckResponse": CoVeCrossCheckResponse(
                    consistency="partially_consistent",
                    reason="fix",
                    keep=True,
                    revised_conclusion="διορθωμένο",
                    confidence_adjustment=-0.2,
                ),
            }
        )
        verifier = CoVeVerifier(llm=llm)
        original = make_finding(conclusion="αρχικό")
        result = await verifier.verify(original, source_text="πηγή")
        assert result.dropped is False
        assert result.finding.irac.conclusion == "διορθωμένο"
        assert result.finding.confidence.score == pytest.approx(0.4, abs=0.01)
        assert result.finding.version == original.version + 1

    @pytest.mark.asyncio
    async def test_llm_no_questions_passes_through(self):
        llm = FakeLLM(
            {
                "CoVeQuestionsResponse": CoVeQuestionsResponse(questions=[]),
            }
        )
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
        finding = finding.model_copy(
            update={
                "irac": IRAC(
                    issue=finding.irac.issue,
                    rule="Βλ. ΦΕΚ Α 999/2023",
                    application=finding.irac.application,
                    conclusion=finding.irac.conclusion,
                )
            }
        )
        result = await verifier.verify(finding, source_text="πηγή")
        assert result.dropped is True
        assert llm.calls == []

    @pytest.mark.asyncio
    async def test_llm_citation_no_index_does_not_disprove(self):
        from leggie.infrastructure.citation import GreekCitationParser

        parser = GreekCitationParser()  # no index configured
        llm = FakeLLM(
            {
                "CoVeQuestionsResponse": CoVeQuestionsResponse(questions=["Τι;"]),
                "CoVeAnswerResponse": CoVeAnswerResponse(answer="ok", supported_by_source=True),
                "CoVeCrossCheckResponse": CoVeCrossCheckResponse(
                    consistency="consistent", reason="r", keep=True
                ),
            }
        )
        verifier = CoVeVerifier(llm=llm, citation_parser=parser)
        finding = make_finding()
        finding = finding.model_copy(
            update={
                "irac": IRAC(
                    issue=finding.irac.issue,
                    rule="Βλ. ΦΕΚ Α 999/2023",
                    application=finding.irac.application,
                    conclusion=finding.irac.conclusion,
                )
            }
        )
        result = await verifier.verify(finding, source_text="πηγή")
        assert result.dropped is False  # unverifiable, not disproven — LLM decides

    @pytest.mark.asyncio
    async def test_citation_parser_exception_fails_open_not_dropped(self):
        """DH-15: _check_citations sat OUTSIDE _verify_llm's own try/except,
        so a citation-parser exception (a port is a plain ABC with no
        never-raises guarantee, same precedent as DH-11's RouterPort.cascade())
        propagated uncaught out of verify() instead of failing open like
        every other error in this function. verify_batch's outer catch-all
        then turned that into a hard DROP — a legitimate finding silently
        removed from the report because of a citation-lookup hiccup, not
        because anything was actually found wrong with it."""

        class _RaisingCitationParser(CitationParserPort):
            def parse(self, text: str) -> list[Citation]:
                return [
                    Citation(
                        scheme=CitationScheme.FEK,
                        identifier="ΦΕΚ Α 999/2023",
                        original_text="ΦΕΚ Α 999/2023",
                    )
                ]

            async def resolve(self, citation: Citation) -> Citation:
                raise RuntimeError("citation index unavailable")

            def supported_schemes(self) -> list[CitationScheme]:
                return [CitationScheme.FEK]

        llm = FakeLLM(
            {
                "CoVeQuestionsResponse": CoVeQuestionsResponse(questions=["Τι;"]),
                "CoVeAnswerResponse": CoVeAnswerResponse(answer="ok", supported_by_source=True),
                "CoVeCrossCheckResponse": CoVeCrossCheckResponse(
                    consistency="consistent", reason="r", keep=True
                ),
            }
        )
        verifier = CoVeVerifier(llm=llm, citation_parser=_RaisingCitationParser())
        finding = make_finding()
        finding = finding.model_copy(
            update={
                "irac": IRAC(
                    issue=finding.irac.issue,
                    rule="Βλ. ΦΕΚ Α 999/2023",
                    application=finding.irac.application,
                    conclusion=finding.irac.conclusion,
                )
            }
        )

        result = await verifier.verify(finding, source_text="πηγή")

        assert result.dropped is False
        assert result.finding.id == finding.id
        # The verification chain continued past the citation hiccup and
        # actually ran, rather than short-circuiting to a bare fail-open.
        assert llm.calls == [
            "CoVeQuestionsResponse",
            "CoVeAnswerResponse",
            "CoVeCrossCheckResponse",
        ]

    @pytest.mark.asyncio
    async def test_plan_questions_from_text_excerpt(self):
        from leggie.infrastructure.citation import GreekCitationParser

        parser = GreekCitationParser()
        verifier = CoVeVerifier(citation_parser=parser)
        finding = Finding(
            finding_type=FindingType.CONSTITUTIONAL,
            irac=IRAC(issue="test", rule="rule", application="app", conclusion="conc"),
            confidence=Confidence.from_score(0.5),
            lens="test",
            model="test",
            evidence=[Evidence(text_excerpt="ΦΕΚ Α 137/2023", verdict="supports")],
        )
        result = await verifier.verify(finding)
        assert len(result.questions) >= 1


class TestArticleNumberOf:
    """article_number_of() must prefer the structured field over free-text
    regex parsing — the whole point of D1's fix."""

    def test_prefers_article_id_over_regex(self):
        # issue text says "Άρθρο 99" but article_id says "15" — article_id wins.
        finding = make_finding(conclusion="conc")
        finding = finding.model_copy(
            update={
                "article_id": "15",
                "irac": IRAC(
                    issue="Άρθρο 99: unrelated text",
                    rule="r",
                    application="a",
                    conclusion="c",
                ),
            }
        )
        assert article_number_of(finding) == "15"

    def test_falls_back_to_regex_for_legacy_findings(self):
        finding = make_finding()
        finding = finding.model_copy(
            update={
                "irac": IRAC(issue="Άρθρο 7: something", rule="r", application="a", conclusion="c")
            }
        )
        assert finding.article_id == ""
        assert article_number_of(finding) == "7"

    def test_empty_when_neither_is_available(self):
        finding = make_finding()
        finding = finding.model_copy(
            update={
                "irac": IRAC(
                    issue="no article marker here", rule="r", application="a", conclusion="c"
                )
            }
        )
        assert article_number_of(finding) == ""
