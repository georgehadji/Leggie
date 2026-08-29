"""Proof-of-defect tests for the Defect-Hunt Protocol V7 findings.

These tests reproduce the VERIFIED DEFECTS found during the proactive audit.
Each test is the executable evidence that promoted its candidate to CONFIRMED.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from leggie.application.agents.skeptic import CalibratedSkeptic
from leggie.domain.models import IRAC, Confidence, Finding, FindingType, Severity
from leggie.infrastructure.budget_guard import BudgetAction, BudgetGuard

# ── D2: CalibratedSkeptic.review must preserve Finding identity ──────────


def test_d2_skeptic_preserves_finding_identity() -> None:
    """D2 reproducer: skeptic.review() must not generate a new UUID.

    The verified mechanism: creating a new Finding(...) without passing the
    original ``id`` field causes pydantic to call uuid4(), breaking the
    event-sourced identity invariant. The fix uses model_copy(update=...)
    which preserves all fields by default, only overriding confidence and
    version.
    """
    original_id = uuid4()

    async def _run() -> None:
        f = Finding(
            id=original_id,
            finding_type=FindingType.CONSTITUTIONAL,
            irac=IRAC(
                issue="Article 3: Test issue",
                rule="Test rule referencing constitution Article 43",
                application="Test application",
                conclusion="Test conclusion",
            ),
            severity=Severity.HIGH,
            confidence=Confidence.from_score(0.85, provenance="lens"),
            evidence=[],
            lens="constitutional",
            model="test-model",
        )
        skeptic = CalibratedSkeptic()  # no LLM — cheap gates only
        survivors, _ = await skeptic.review([f])

        assert len(survivors) == 1, "Finding should survive review"
        assert survivors[0].id == original_id, (
            f"Identity broken: {survivors[0].id} != {original_id}"
        )

    asyncio.run(_run())


def test_d2_skeptic_increments_version_on_adjustment() -> None:
    """D2 boundary: version must increment when confidence is adjusted.

    The FactualGate adds +0.05 confidence for constitutional findings that
    reference 'constitution' or 'Σύνταγμα'. The version must go from 1 → 2.
    """

    async def _run() -> None:
        f = Finding(
            id=uuid4(),
            finding_type=FindingType.CONSTITUTIONAL,
            irac=IRAC(
                issue="Article 3: Test",
                rule="Rule referencing Σύνταγμα Article 43",
                application="Test",
                conclusion="Test",
            ),
            severity=Severity.HIGH,
            confidence=Confidence.from_score(0.85, provenance="lens"),
            evidence=[],
            lens="constitutional",
            model="test-model",
        )
        skeptic = CalibratedSkeptic()
        survivors, _ = await skeptic.review([f])

        assert survivors[0].version == 2, (
            f"Version should increment from 1 to 2, got {survivors[0].version}"
        )
        assert survivors[0].confidence.score == pytest.approx(0.9), (
            f"Confidence should be 0.85 + 0.05 = 0.9, got {survivors[0].confidence.score}"
        )

    asyncio.run(_run())


def test_d2_skeptic_no_regression_no_llm() -> None:
    """D2 regression guard: skeptic without LLM still works correctly.

    Pre-existing behavior: cheap typed gates never refute, and identity is
    preserved even when no confidence adjustment occurs.
    """

    async def _run() -> None:
        original_id = uuid4()
        f = Finding(
            id=original_id,
            finding_type=FindingType.NUMERIC,
            irac=IRAC(
                issue="Article 5: Budget amount",
                rule="The amount should be €1000",
                application="Applied to Article 5",
                conclusion="Amount is correct",
            ),
            severity=Severity.MEDIUM,
            confidence=Confidence.from_score(0.75, provenance="lens"),
            evidence=[],
            lens="economic",
            model="test-model",
        )
        skeptic = CalibratedSkeptic()
        survivors, verdicts = await skeptic.review([f])

        # Numeric findings don't trigger confidence adjustments from cheap gates
        assert len(survivors) == 1
        assert survivors[0].id == original_id
        assert survivors[0].confidence.score == 0.75
        # No gate refutes — all verdicts are "neutral" or "supports"
        refuted = any(v.verdict == "refutes" for v in verdicts)
        assert not refuted, "Cheap gates should never refute"

    asyncio.run(_run())


# ── D1: BudgetGuard must return BLOCK on budget exceeded ─────────────────


def test_d1_budget_guard_blocks_on_exceeded() -> None:
    """D1 reproducer: BudgetGuard must return BLOCK immediately when exceeded.

    The verified mechanism: check() returned DEGRADE on first exceed (allowing
    the BudgetGuardDecorator to proceed and record usage past the ceiling).
    The fix returns BLOCK unconditionally when the proposed call would exceed
    max_tokens or max_cost.
    """
    guard = BudgetGuard(max_tokens=100, max_cost=0.01)
    guard.record_usage(prompt_tokens=80, completion_tokens=10, model="test")

    # Proposed call would push tokens to 100 (80+10 + 10+10 > 100)
    action = guard.check(prompt_tokens=10, completion_tokens=10, model="test")
    assert action == BudgetAction.BLOCK, f"Budget exceeded must return BLOCK, got {action}"


def test_d1_budget_guard_degrade_at_80_percent() -> None:
    """D1 boundary: DEGRADE still fires at 80% threshold (not removed).

    The fix only changed the budget-exceeded path; the 80% early-warning
    DEGRADE path is untouched.
    """
    guard = BudgetGuard(max_tokens=1_000, max_cost=1.0)
    guard.record_usage(prompt_tokens=450, completion_tokens=400, model="test")

    # 450+400 = 850 used. 850+50+50 = 950 proposed → 95% > 80%
    action = guard.check(prompt_tokens=50, completion_tokens=50, model="test")
    assert action == BudgetAction.DEGRADE, f"80% threshold must still return DEGRADE, got {action}"


def test_d1_budget_guard_no_regression_allow() -> None:
    """D1 regression guard: under-budget calls still return ALLOW."""
    guard = BudgetGuard(max_tokens=100_000, max_cost=5.0)
    action = guard.check(prompt_tokens=1_000, completion_tokens=500, model="test")
    assert action == BudgetAction.ALLOW
