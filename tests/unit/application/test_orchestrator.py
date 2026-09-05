"""Tests for Orchestrator — article decomposition and lens dispatch."""

import pytest

from leggie.application.agents.constitutional_lens import ConstitutionalLens
from leggie.application.agents.economic_lens import EconomicLens
from leggie.application.agents.orchestrator import Orchestrator
from leggie.application.ports.llm import LLMPort, LLMRequest, LLMResponse
from leggie.application.ports.router import RouteResult, RouterPort
from leggie.domain.models import Article, Document, Event, EventType, Finding, ModelTier

SAMPLE_DOC = Document(
    title="Test Bill",
    source_format="txt",
    raw_text="Test bill with three articles.",
    articles=[
        Article(
            id="1",
            raw_text="Άρθρο 1: Εξουσιοδότηση για έκδοση π.δ. και "
            "περιορισμός προσωπικών δεδομένων για την προστασία "
            "του απορρήτου των επικοινωνιών.",
        ),
        Article(
            id="2",
            raw_text="Άρθρο 2: Απλή διάταξη χωρίς συνταγματικά ζητήματα "
            "που ρυθμίζει την έναρξη ισχύος του παρόντος νόμου.",
        ),
        Article(
            id="3",
            raw_text="Άρθρο 3: Περιορισμός προσωπικών δεδομένων "
            "και παραβίαση δικαιώματος στην ιδιωτικότητα κατά την "
            "εφαρμογή των διατάξεων του παρόντος.",
        ),
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
            assert task.lens in [
                "constitutional",
                "legal_coherence",
                "economic",
                "implementation",
                "eu_gdpr",
            ]
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
        assert len(findings) >= 2  # Articles with constitutional/EU triggers

    @pytest.mark.asyncio
    async def test_analyze_document_matches_serial_result(self):
        """Parallel fan-out must yield the same findings as the serial path."""
        orch = Orchestrator()
        serial_findings: list[Finding] = []
        for article in SAMPLE_DOC.articles:
            serial_findings.extend(await orch.analyze_article(article))
        parallel_findings = await orch.analyze_document(SAMPLE_DOC)

        serial_set = {(f.lens, f.irac.issue) for f in serial_findings}
        parallel_set = {(f.lens, f.irac.issue) for f in parallel_findings}
        assert serial_set == parallel_set

    @pytest.mark.asyncio
    async def test_analyze_document_isolates_article_failure(self):
        """One article crashing must not abort the rest of the batch."""
        events: list[Event] = []

        def record_degradation(ev: Event) -> None:
            events.append(ev)

        orch = Orchestrator(on_degradation=record_degradation)
        original = orch.analyze_article

        async def failing_analyze(article: Article, lens_names: list[str] | None = None):
            if article.id == "2":
                raise RuntimeError("simulated article failure")
            return await original(article, lens_names)

        orch.analyze_article = failing_analyze  # type: ignore[method-assign]
        findings = await orch.analyze_document(SAMPLE_DOC)

        # Articles 1 and 3 should still produce findings.
        assert len(findings) >= 2
        degraded = [e for e in events if e.event_type == EventType.DEGRADED]
        assert len(degraded) == 1
        assert degraded[0].data["article_id"] == "2"

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


# ── DH: router.cascade() failure must not nuke sibling lens results ─────
#
# _run_lens_with_cascade() already guards router.route() with try/except
# (log + fall back to defaults). Its two router.cascade() call sites did not
# have the same guard. Because analyze_article() dispatches lenses inside an
# asyncio.TaskGroup, one lens's task raising ANY exception makes the
# TaskGroup cancel every sibling task and re-raise — discarding every other
# lens's already-computed findings for that article, not just the crashing
# lens's. A router is a plain RouterPort ABC with no never-raises guarantee
# (route() is defended against exactly this), so a failing cascade() is a
# realistic, in-contract failure mode, not a hypothetical.
#
# This is a deterministic control-flow bug, not a timing-dependent race:
# asyncio.TaskGroup's cancel-siblings-on-exception behavior is guaranteed,
# so a router whose cascade() always raises reproduces it on every run.

ARTICLE_DELEGATION_ONLY = Article(
    id="1",
    raw_text="Άρθρο 1: Εξουσιοδότηση για έκδοση προεδρικού διατάγματος.",
)


class CrashingCascadeRouter(RouterPort):
    """route() succeeds normally; cascade() always raises."""

    def __init__(self, cascade_enabled: bool = True) -> None:
        self._cascade_enabled = cascade_enabled

    async def route(self, task_type: str, budget_remaining: float | None = None) -> RouteResult:
        return RouteResult(
            model="google/gemini-2.5-flash",
            tier=ModelTier.BUDGET,
            max_tokens=4096,
            cascade_enabled=self._cascade_enabled,
        )

    async def cascade(
        self, task_type: str, current_tier: ModelTier, failure_reason: str | None = None
    ) -> RouteResult | None:
        raise RuntimeError("router cascade backend exploded")

    def supported_models(self) -> list[str]:
        return ["google/gemini-2.5-flash"]


class WorkingCascadeRouter(RouterPort):
    """A normal, non-crashing router whose cascade() succeeds once."""

    async def route(self, task_type: str, budget_remaining: float | None = None) -> RouteResult:
        return RouteResult(
            model="google/gemini-2.5-flash",
            tier=ModelTier.BUDGET,
            max_tokens=4096,
            cascade_enabled=True,
        )

    async def cascade(
        self, task_type: str, current_tier: ModelTier, failure_reason: str | None = None
    ) -> RouteResult | None:
        return RouteResult(model="google/gemini-2.5-pro", tier=ModelTier.PREMIUM, max_tokens=8192)

    def supported_models(self) -> list[str]:
        return ["google/gemini-2.5-flash", "google/gemini-2.5-pro"]


class EmptyThenFoundLLM(LLMPort):
    """First generate_structured() call returns no findings; second returns one."""

    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    async def generate(self, request: LLMRequest) -> LLMResponse:  # pragma: no cover - unused
        raise NotImplementedError

    async def generate_structured(self, request: LLMRequest, schema: type):
        self.requests.append(request)
        if len(self.requests) == 1:
            payload = {"findings": []}
        else:
            payload = {
                "findings": [
                    {
                        "issue": "Ζήτημα εξουσιοδότησης",
                        "rule": "Άρθρο 43 του Συντάγματος",
                        "application": "Εφαρμόζεται στο άρθρο",
                        "conclusion": "Χρήζει ελέγχου",
                        "verbatim_quote": "προεδρικού διατάγματος",
                        "severity": "high",
                        "probability": 0.7,
                    }
                ]
            }
        obj = schema.model_validate(payload)
        return obj, LLMResponse(content="", model=request.model or "fake", tier_used=ModelTier.BUDGET, usage={})

    async def count_tokens(self, text: str, model: str | None = None) -> int:
        return len(text) // 4


class TestCascadeFailureIsolation:
    @pytest.mark.asyncio
    async def test_cascade_router_failure_does_not_lose_sibling_lens_findings(self):
        """Proof of defect / fix: economic's cascade() blowing up on its own
        empty-findings retry must not discard constitutional's real finding."""
        orch = Orchestrator(
            llm=None,
            router=CrashingCascadeRouter(),
            lens_config={"constitutional": ConstitutionalLens, "economic": EconomicLens},
        )
        findings = await orch.analyze_article(
            ARTICLE_DELEGATION_ONLY, lens_names=["constitutional", "economic"]
        )
        assert any(f.lens == "constitutional" for f in findings)

    @pytest.mark.asyncio
    async def test_analyze_document_survives_cascade_router_crash(self):
        """Same failure, exercised through the real analyze_document() entry point."""
        doc = Document(
            title="T", source_format="txt", raw_text="x", articles=[ARTICLE_DELEGATION_ONLY]
        )
        orch = Orchestrator(
            llm=None,
            router=CrashingCascadeRouter(),
            lens_config={"constitutional": ConstitutionalLens, "economic": EconomicLens},
        )
        findings = await orch.analyze_document(doc, lens_names=["constitutional", "economic"])
        assert any(f.lens == "constitutional" for f in findings)

    @pytest.mark.asyncio
    async def test_cascade_disabled_never_invokes_crashing_cascade(self):
        """Boundary: cascade_enabled=False must not even attempt cascade()."""
        orch = Orchestrator(
            llm=None,
            router=CrashingCascadeRouter(cascade_enabled=False),
            lens_config={"constitutional": ConstitutionalLens, "economic": EconomicLens},
        )
        findings = await orch.analyze_article(
            ARTICLE_DELEGATION_ONLY, lens_names=["constitutional", "economic"]
        )
        assert any(f.lens == "constitutional" for f in findings)
        assert not any(f.lens == "economic" for f in findings)

    @pytest.mark.asyncio
    async def test_legitimate_cascade_still_recovers_after_empty_first_attempt(self):
        """No-regression: a non-crashing cascade() must still be honoured."""
        llm = EmptyThenFoundLLM()
        orch = Orchestrator(
            llm=llm, router=WorkingCascadeRouter(), lens_config={"constitutional": ConstitutionalLens}
        )
        findings = await orch.analyze_article(ARTICLE_DELEGATION_ONLY, lens_names=["constitutional"])
        assert len(findings) == 1
        assert len(llm.requests) == 2
        assert llm.requests[0].model == "google/gemini-2.5-flash"
        assert llm.requests[1].model == "google/gemini-2.5-pro"
