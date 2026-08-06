"""Tests for DeliberativePromptRenderer — Stage 1/Stage 2 rendering, perspective fallback."""

from leggie.application.services.deliberative_prompts import (
    DEFAULT_PERSPECTIVE,
    PERSPECTIVES,
    DeliberativePromptRenderer,
)


class TestStage1Rendering:
    def test_renders_bill_text(self):
        renderer = DeliberativePromptRenderer()
        prompt = renderer.render_stage1("Άρθρο 1: Δοκιμαστικό κείμενο", perspective="neutral")
        assert "Άρθρο 1: Δοκιμαστικό κείμενο" in prompt

    def test_renders_default_perspective_label(self):
        renderer = DeliberativePromptRenderer()
        prompt = renderer.render_stage1("bill text")
        assert PERSPECTIVES[DEFAULT_PERSPECTIVE]["label"] in prompt

    def test_system_prompt_is_nonempty(self):
        renderer = DeliberativePromptRenderer()
        assert len(renderer.stage1_system_prompt()) > 0

    def test_all_placeholders_substituted(self):
        renderer = DeliberativePromptRenderer()
        prompt = renderer.render_stage1("bill text", perspective="neutral")
        assert "{" not in prompt and "}" not in prompt


class TestStage2Rendering:
    def test_renders_bill_text_and_prior_report(self):
        renderer = DeliberativePromptRenderer()
        prompt = renderer.render_stage2("Το κείμενο του νομοσχεδίου", "Η προηγούμενη ανάλυση")
        assert "Το κείμενο του νομοσχεδίου" in prompt
        assert "Η προηγούμενη ανάλυση" in prompt

    def test_system_prompt_is_nonempty(self):
        renderer = DeliberativePromptRenderer()
        assert len(renderer.stage2_system_prompt()) > 0

    def test_all_placeholders_substituted(self):
        renderer = DeliberativePromptRenderer()
        prompt = renderer.render_stage2("bill", "prior")
        assert "{" not in prompt and "}" not in prompt


class TestPerspectiveFallback:
    def test_unknown_perspective_falls_back_to_neutral(self):
        renderer = DeliberativePromptRenderer()
        prompt = renderer.render_stage1("bill text", perspective="does-not-exist")
        assert PERSPECTIVES[DEFAULT_PERSPECTIVE]["label"] in prompt

    def test_unknown_perspective_logs_warning(self, capsys):
        renderer = DeliberativePromptRenderer()
        renderer.render_stage1("bill text", perspective="does-not-exist")
        # structlog (converted in PROD-09/10) renders to stdout via ConsoleRenderer.
        out = capsys.readouterr().out
        assert "unknown_perspective" in out or "deliberative.unknown_perspective" in out

    def test_known_perspective_does_not_warn(self, capsys):
        renderer = DeliberativePromptRenderer()
        renderer.render_stage1("bill text", perspective="neutral")
        out = capsys.readouterr().out
        assert "unknown_perspective" not in out


class TestPerspectiveIsData:
    def test_neutral_perspective_registered(self):
        assert "neutral" in PERSPECTIVES

    def test_default_perspective_is_neutral(self):
        assert DEFAULT_PERSPECTIVE == "neutral"

    def test_perspective_entries_have_label_and_instruction(self):
        for key, entry in PERSPECTIVES.items():
            assert "label" in entry, f"{key} missing label"
            assert "instruction" in entry, f"{key} missing instruction"
