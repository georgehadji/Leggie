"""Tests for Orchestrator — article decomposition and lens dispatch."""

import pytest

from leggie.application.agents.orchestrator import Orchestrator
from leggie.domain.models import Article, Document

SAMPLE_DOC = Document(
    title="Test Bill",
    source_format="txt",
    raw_text="Test bill with three articles.",
    articles=[
        Article(id="1", raw_text="Άρθρο 1: Εξουσιοδότηση για έκδοση π.δ. και "
                "περιορισμός προσωπικών δεδομένων για την προστασία "
                "του απορρήτου των επικοινωνιών."),
        Article(id="2", raw_text="Άρθρο 2: Απλή διάταξη χωρίς συνταγματικά ζητήματα "
                "που ρυθμίζει την έναρξη ισχύος του παρόντος νόμου."),
        Article(id="3", raw_text="Άρθρο 3: Περιορισμός προσωπικών δεδομένων "
                "και παραβίαση δικαιώματος στην ιδιωτικότητα κατά την "
                "εφαρμογή των διατάξεων του παρόντος."),
    ],
)


class TestOrchestrator:
    def test_supported_lenses(self):
        orch = Orchestrator()
        assert "constitutional" in orch.supported_lenses

    def test_decompose_creates_tasks(self):
        orch = Orchestrator()
        tasks = orch.decompose(SAMPLE_DOC)
        # 3 articles × 5 lenses = 15 tasks
        assert len(tasks) == 15
        for task in tasks:
            assert task.lens in ["constitutional", "legal_coherence", "economic", "implementation", "eu_gdpr"]
            assert task.sample_count == 1

    def test_decompose_task_article_ids(self):
        orch = Orchestrator()
        tasks = orch.decompose(SAMPLE_DOC)
        article_ids = [t.article_id for t in tasks]
        assert "1" in article_ids
        assert "2" in article_ids
        assert "3" in article_ids

    @pytest.mark.asyncio
    async def test_analyze_article_returns_findings(self):
        orch = Orchestrator()
        article = Article(id="1", raw_text="Εξουσιοδότηση για έκδοση προεδρικού διατάγματος")
        findings = await orch.analyze_article(article)
        assert len(findings) > 0

    @pytest.mark.asyncio
    async def test_analyze_document_returns_findings(self):
        orch = Orchestrator()
        findings = await orch.analyze_document(SAMPLE_DOC)
        assert len(findings) >= 5  # Multiple findings across 5 lenses and 3 articles

    @pytest.mark.asyncio
    async def test_analyze_with_unknown_lens_skips(self):
        orch = Orchestrator()
        article = Article(id="1", raw_text="test")
        findings = await orch.analyze_article(article, lens_names=["nonexistent"])
        assert len(findings) == 0

    def test_decompose_empty_document(self):
        orch = Orchestrator()
        doc = Document(title="Empty", source_format="txt", raw_text="")
        tasks = orch.decompose(doc)
        assert len(tasks) == 0
