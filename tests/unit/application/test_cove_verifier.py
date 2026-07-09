"""Tests for CoVe evidence loop — Chain of Verification."""

import pytest
from leggie.application.services.cove_verifier import CoVeVerifier, VerificationQuestion
from leggie.domain.models import (
    Finding, IRAC, Confidence, Evidence, Citation,
    CitationScheme, FindingType,
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
