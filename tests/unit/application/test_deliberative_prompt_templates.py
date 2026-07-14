"""Snapshot-style tests for the raw deliberative prompt templates (WU-5)."""

from leggie.application.agents.prompts import deliberative_stage1, deliberative_stage2


class TestPrompt01Template:
    def test_has_system_prompt(self):
        assert isinstance(deliberative_stage1.SYSTEM_PROMPT, str)
        assert len(deliberative_stage1.SYSTEM_PROMPT) > 0

    def test_has_required_placeholders(self):
        template = deliberative_stage1.USER_PROMPT_TEMPLATE
        assert "{bill_text}" in template
        assert "{perspective_label}" in template
        assert "{perspective_instruction}" in template

    def test_covers_required_sections(self):
        template = deliberative_stage1.USER_PROMPT_TEMPLATE
        assert "Εισαγωγή" in template
        assert "Περίληψη" in template
        assert "Μέρος/Κεφάλαιο" in template

    def test_no_hardcoded_party_name(self):
        template = deliberative_stage1.USER_PROMPT_TEMPLATE
        # Perspective framing must come from data (PERSPECTIVES), never a literal
        # party name embedded in the template itself.
        for banned in ("Νίκη", "ΝΔ", "ΣΥΡΙΖΑ", "ΠΑΣΟΚ"):
            assert banned not in template


class TestPrompt02Template:
    def test_has_system_prompt(self):
        assert isinstance(deliberative_stage2.SYSTEM_PROMPT, str)
        assert len(deliberative_stage2.SYSTEM_PROMPT) > 0

    def test_has_required_placeholders(self):
        template = deliberative_stage2.USER_PROMPT_TEMPLATE
        assert "{bill_text}" in template
        assert "{prior_report}" in template

    def test_covers_required_sections(self):
        template = deliberative_stage2.USER_PROMPT_TEMPLATE
        assert "Top-20" in template
        assert "Top-10" in template
        assert "Executive briefing" in template
        assert "Συνταγματικότητα" in template
