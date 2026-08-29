"""DeliberativePromptRenderer — renders Prompt01/Prompt02 for the Reasoner-backed pipeline.

Party perspective is data (PERSPECTIVES), never a hardcoded branch in code. An
unknown perspective key falls back to "neutral" with a logged warning.
"""

from __future__ import annotations

import logging

from leggie.application.agents.prompts import deliberative_stage1, deliberative_stage2

logger = logging.getLogger(__name__)

DEFAULT_PERSPECTIVE = "neutral"

PERSPECTIVES: dict[str, dict[str, str]] = {
    "neutral": {
        "label": "Ουδέτερη ανάλυση",
        "instruction": (
            "Αξιολόγησε το νομοσχέδιο χωρίς πολιτική τοποθέτηση, εστιάζοντας αποκλειστικά "
            "στη νομική συνέπεια, τη σαφήνεια και την πρακτική εφαρμοσιμότητα των διατάξεων."
        ),
    },
}


class DeliberativePromptRenderer:
    """Renders Stage 1 / Stage 2 prompts for the deliberative pipeline."""

    def render_stage1(self, bill_text: str, perspective: str = DEFAULT_PERSPECTIVE) -> str:
        resolved = self._resolve_perspective(perspective)
        return deliberative_stage1.USER_PROMPT_TEMPLATE.format(
            bill_text=bill_text,
            perspective_label=resolved["label"],
            perspective_instruction=resolved["instruction"],
        )

    def render_stage2(self, bill_text: str, prior_report: str) -> str:
        return deliberative_stage2.USER_PROMPT_TEMPLATE.format(
            bill_text=bill_text,
            prior_report=prior_report,
        )

    def stage1_system_prompt(self) -> str:
        return deliberative_stage1.SYSTEM_PROMPT

    def stage2_system_prompt(self) -> str:
        return deliberative_stage2.SYSTEM_PROMPT

    def _resolve_perspective(self, perspective: str) -> dict[str, str]:
        resolved = PERSPECTIVES.get(perspective)
        if resolved is None:
            logger.warning("deliberative.unknown_perspective", extra={"perspective": perspective})
            return PERSPECTIVES[DEFAULT_PERSPECTIVE]
        return resolved
