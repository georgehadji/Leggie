"""Tests for all 4 new Phase 2 lenses."""

import pytest
from leggie.application.agents.legal_coherence_lens import LegalCoherenceLens
from leggie.application.agents.economic_lens import EconomicLens
from leggie.application.agents.implementation_lens import ImplementationLens
from leggie.application.agents.eu_gdpr_lens import EUGDPRLens
from leggie.domain.models import Article, FindingType


VAGUE_ARTICLE = Article(id="1", raw_text=(
    "Άρθρο 1: Ο αρμόδιος φορέας λαμβάνει κατάλληλα μέτρα "
    "ανάλογα με τις ειδικές συνθήκες."
))

COST_ARTICLE = Article(id="2", raw_text=(
    "Άρθρο 2: Η δαπάνη καλύπτεται από τον κρατικό προϋπολογισμό. "
    "Το πρόστιμο ανέρχεται έως 500.000 ευρώ."
))

DEADLINE_ARTICLE = Article(id="3", raw_text=(
    "Άρθρο 3: Εντός 15 ημερών από την έναρξη ισχύος, οι ενδιαφερόμενοι "
    "υποβάλλουν αίτηση. Ισχύει μεταβατική περίοδος 30 ημερών."
))

GDPR_ARTICLE = Article(id="4", raw_text=(
    "Άρθρο 4: Η επεξεργασία προσωπικών δεδομένων γίνεται με συγκατάθεση "
    "του υποκειμένου, σύμφωνα με τον ΓΚΠΔ."
))


class TestLegalCoherenceLens:
    @pytest.mark.asyncio
    async def test_detects_vague_language(self):
        lens = LegalCoherenceLens()
        findings = await lens.analyze(VAGUE_ARTICLE)
        assert len(findings) > 0

    @pytest.mark.asyncio
    async def test_name(self):
        lens = LegalCoherenceLens()
        assert lens.name() == "legal_coherence"


class TestEconomicLens:
    @pytest.mark.asyncio
    async def test_detects_cost(self):
        lens = EconomicLens()
        findings = await lens.analyze(COST_ARTICLE)
        economic = [f for f in findings if f.finding_type == FindingType.ECONOMIC]
        assert len(economic) >= 1

    @pytest.mark.asyncio
    async def test_name(self):
        lens = EconomicLens()
        assert lens.name() == "economic"


class TestImplementationLens:
    @pytest.mark.asyncio
    async def test_detects_deadline(self):
        lens = ImplementationLens()
        findings = await lens.analyze(DEADLINE_ARTICLE)
        impl = [f for f in findings if f.finding_type == FindingType.IMPLEMENTATION]
        assert len(impl) >= 1

    @pytest.mark.asyncio
    async def test_name(self):
        lens = ImplementationLens()
        assert lens.name() == "implementation"


class TestEUGDPRLens:
    @pytest.mark.asyncio
    async def test_detects_gdpr(self):
        lens = EUGDPRLens()
        findings = await lens.analyze(GDPR_ARTICLE)
        eu = [f for f in findings if f.finding_type == FindingType.EU_COMPLIANCE]
        assert len(eu) >= 1

    @pytest.mark.asyncio
    async def test_name(self):
        lens = EUGDPRLens()
        assert lens.name() == "eu_gdpr"


class TestAllLenses:
    """Verify all 5 lenses work together through the orchestrator."""

    @pytest.mark.asyncio
    async def test_all_lenses_via_orchestrator(self):
        from leggie.application.agents.orchestrator import Orchestrator
        orch = Orchestrator()
        assert len(orch.supported_lenses) == 5
        assert "constitutional" in orch.supported_lenses
        assert "legal_coherence" in orch.supported_lenses
        assert "economic" in orch.supported_lenses
        assert "implementation" in orch.supported_lenses
        assert "eu_gdpr" in orch.supported_lenses

    @pytest.mark.asyncio
    async def test_article_through_all_lenses(self):
        from leggie.application.agents.orchestrator import Orchestrator
        orch = Orchestrator()
        article = Article(id="1", raw_text=VAGUE_ARTICLE.raw_text + "\n" + COST_ARTICLE.raw_text)
        findings = await orch.analyze_article(article)
        assert len(findings) > 0

    @pytest.mark.asyncio
    async def test_document_parallel(self):
        from leggie.application.agents.orchestrator import Orchestrator
        from leggie.domain.models import Document
        orch = Orchestrator()
        doc = Document(
            title="Test", source_format="txt", raw_text="test",
            articles=[VAGUE_ARTICLE, COST_ARTICLE, DEADLINE_ARTICLE, GDPR_ARTICLE],
        )
        findings = await orch.analyze_document(doc)
        assert len(findings) >= 4  # At least one per article
